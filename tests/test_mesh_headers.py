# System-python test: per-version mesh header layouts and the in-memory mesh seam (no bpy needed)
import os
import sys
from io import BytesIO

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

import numpy as np
from modules.gen_functions import getPaddedPos
from modules.mesh.file_re_mesh import (DEFAULT_MESH_FILE_VERSION, VERTEX_ELEMENT_DTYPE, FileHeader,
                                       MeshBufferHeader, MESH_FILE_MAGIC, REMesh, readMesh,
                                       resolveMeshVersion, writeMesh)
from modules.mesh.mesh_versions import (VERSION_PRAG, VERSION_SF6,
                                        getNearestRemapVersion,
                                        meshFileVersionToInternalVersionDict,
                                        meshFileVersionToNewVersionDict,
                                        newVersionToMeshFileVersion)

# The versions the header layouts are keyed by, i.e. the remapped versions
FILE_VERSIONS = sorted(newVersionToMeshFileVersion)

# Header sizes measured from the hand-written readers/writers these declarative layouts
# replaced. A change here means a version's on-disk layout changed, which breaks existing
# files, so it must be a deliberate edit. Versions added later are only round-trip tested.
FILE_HEADER_SIZES = {75: 128, 80: 128, 85: 128, 90: 128, 95: 128, 96: 128, 100: 128,
                     105: 168, 110: 168, 115: 176, 120: 176, 124: 176, 125: 176, 127: 176,
                     130: 176, 135: 176, 136: 176, 140: 176}
MESH_BUFFER_HEADER_SIZES = {75: 48, 80: 48, 85: 48, 90: 48, 95: 64, 96: 64, 100: 64,
                            105: 80, 110: 80, 115: 80, 120: 80, 124: 80, 125: 80, 127: 80,
                            130: 80, 135: 96, 136: 96, 140: 96}

# Fields that hold a negative sentinel or are signed in the header; everything else is unsigned
SIGNED_FIELDS = {"nameCount", "sf6UnknCount", "sf6unkn3", "wilds_unkn5", "vertexElementSize",
                 "unkn1"}
# Not plain header scalars: bitflag object and the bulk buffers that post_read/post_write handle
NON_SCALAR_FIELDS = {"contentFlag", "vertexElementList", "vertexBuffer", "faceBuffer",
                     "streamingBufferHeaderList", "secondaryWeightBuffer"}


def scalars(obj):
    return {name: value for name, value in vars(obj).items()
            if name not in NON_SCALAR_FIELDS and isinstance(value, int)}


def fillScalars(obj):
    """Give every field a unique non-zero value so a lost field cannot hide behind a default."""
    for index, name in enumerate(list(vars(obj))):
        if name in NON_SCALAR_FIELDS:
            continue
        value = index * 7 + 1
        setattr(obj, name, -value if name in SIGNED_FIELDS else value)


def writeHeader(header, version):
    stream = BytesIO()
    header.write(stream, version)
    return stream.getvalue()


def checkRoundTrip(header, version, label, derived=()):
    """Write `header`, read it back and compare every field the version writes."""
    data = writeHeader(header, version)
    restored = type(header)()
    stream = BytesIO(data)
    restored.read(stream, version)
    restoredScalars = scalars(restored)
    defaultScalars = scalars(type(header)())
    sourceScalars = scalars(header)
    written = {name: value for name, value in restoredScalars.items()
               if value != defaultScalars[name] and name not in derived}
    for name, value in written.items():
        assert value == sourceScalars[name], (
            f"v{version}: {label} {name} read {value}, wrote {sourceScalars[name]}")
    return data, stream, written


def checkFileHeader(version):
    source = FileHeader()
    fillScalars(source)
    source.magic = MESH_FILE_MAGIC
    source.contentFlag.setBitFlag(True, False, True, True, False, True, True)

    data, stream, written = checkRoundTrip(source, version, "FileHeader")
    assert stream.tell() == len(data), (
        f"v{version}: FileHeader read consumed {stream.tell()} of {len(data)} bytes")
    # magic holds its default value, so it is not in `written`; a 2nd version guard is the size
    assert len(written) >= 18, f"v{version}: only {len(written)} FileHeader fields round-tripped"
    assert len(data) == FILE_HEADER_SIZES.get(version, len(data)), (
        f"v{version}: FileHeader is {len(data)} bytes, was {FILE_HEADER_SIZES[version]}")

    restored = FileHeader()
    restored.read(BytesIO(data), version)
    assert restored.contentFlag.bitFlag == source.contentFlag.bitFlag, (
        f"v{version}: contentFlag {restored.contentFlag.bitFlag} != {source.contentFlag.bitFlag}")
    for flag in ("hasAABB", "hasSkeleton", "hasBlendShape", "hasGroupPivot", "hasUnknFlag8",
                 "hasUnknFlag10", "hasUnknFlag16"):
        assert getattr(restored.contentFlag, flag) == getattr(source.contentFlag, flag), (
            f"v{version}: contentFlag.{flag} did not round-trip")
    return len(data), len(written)


def checkMeshBufferHeader(version):
    source = MeshBufferHeader()
    fillScalars(source)
    # No DD2 secondary weight buffer and no streaming header in this test
    source.sunbreakOffset = 0
    # The element table starts right after the header; two entries exercise the batch read
    source.vertexElementList = np.array([(1, 12, 0), (2, 16, 32)], dtype=VERTEX_ELEMENT_DTYPE)
    source.mainVertexElementCount = 1
    source.vertexElementCount = 2

    headerOnly = MeshBufferHeader()
    for name, value in scalars(source).items():
        setattr(headerOnly, name, value)
    headerOnly.vertexElementOffset = 0
    headerOnly.vertexElementCount = 0
    headerSize = len(writeHeader(headerOnly, version))
    assert headerSize == MESH_BUFFER_HEADER_SIZES.get(version, headerSize), (
        f"v{version}: MeshBufferHeader is {headerSize} bytes, was {MESH_BUFFER_HEADER_SIZES[version]}")
    source.vertexElementOffset = headerSize

    # faceBufferOffset/faceBufferSize are stored below SF6, derived from the buffer block above it
    derived = {"faceBufferOffset", "faceBufferSize"} if version >= VERSION_SF6 else set()
    data, stream, written = checkRoundTrip(source, version, "MeshBufferHeader", derived)
    assert len(written) >= 11, f"v{version}: only {len(written)} fields round-tripped"

    # Buffer reads only need the file to be long enough; their sizes come back from the header
    restored = MeshBufferHeader()
    restored.read(BytesIO(data + b"\x00" * 4096), version)
    assert [tuple(entry) for entry in restored.vertexElementList] == [
        tuple(entry) for entry in source.vertexElementList], f"v{version}: vertex elements"
    if version >= VERSION_SF6:
        assert restored.faceBufferOffset == (
            restored.vertexBufferOffset + restored.vertexBufferSize), f"v{version}: faceBufferOffset"
        assert restored.faceBufferSize == (
            restored.block2FaceBufferOffset - restored.vertexBufferSize), f"v{version}: faceBufferSize"
    return headerSize, len(written)


def checkVersionResolution():
    """The three version namespaces must resolve into each other without ambiguity."""
    for remappedVersion, fileVersion in sorted(newVersionToMeshFileVersion.items()):
        assert resolveMeshVersion(remappedVersion).fileVersion == fileVersion, remappedVersion
        assert resolveMeshVersion(fileVersion).remapVersion == remappedVersion, fileVersion
    unversioned = resolveMeshVersion(None, "natives/STM/foo/bar.mesh")
    assert unversioned.fileVersion == DEFAULT_MESH_FILE_VERSION, unversioned.fileVersion
    assert unversioned.fallbackReason == "unversioned", unversioned.fallbackReason
    unsupported = resolveMeshVersion(1)
    assert unsupported.fileVersion == 1 and unsupported.fallbackReason == "unsupported"
    assert unsupported.remapVersion == getNearestRemapVersion(1), unsupported.remapVersion


def checkInMemoryRoundTrip(fileVersion):
    resolved = resolveMeshVersion(fileVersion)
    reMesh = REMesh()
    reMesh.fileHeader.version = resolved.internalVersion
    reMesh.fileHeader.contentFlag.setBitFlag(True, True, True, True, True, True, True)
    reMesh.fileHeader.fileSize = getPaddedPos(
        len(writeHeader(reMesh.fileHeader, resolved.remapVersion)), 16)

    stream = BytesIO()
    writeMesh(reMesh, stream, fileVersion)
    # REMesh.write pads with seek(), which leaves a hole in a BytesIO that a real file fills
    # with zeros, so pad here to get the bytes an on-disk file would hold
    data = stream.getvalue().ljust(reMesh.fileHeader.fileSize, b"\x00")
    assert len(data) == reMesh.fileHeader.fileSize, (
        f"{fileVersion}: wrote {len(data)} bytes, fileSize says {reMesh.fileHeader.fileSize}")

    restored = readMesh(BytesIO(data), versionIdentifier=fileVersion)
    assert restored.fileHeader.magic == MESH_FILE_MAGIC
    assert restored.fileHeader.fileSize == reMesh.fileHeader.fileSize
    assert restored.fileHeader.version == resolved.internalVersion
    assert restored.fileHeader.contentFlag.bitFlag == reMesh.fileHeader.contentFlag.bitFlag
    assert restored.meshVersion == fileVersion, (
        f"{fileVersion}: meshVersion read back as {restored.meshVersion}")
    # A header-only mesh has every section offset at zero
    assert restored.lodHeader is None and restored.meshBufferHeader is None
    assert restored.streamingInfoHeader is None and restored.isMPLY is False
    return len(data)


def main():
    sizes = []
    fieldCounts = []
    for version in FILE_VERSIONS:
        size, count = checkFileHeader(version)
        sizes.append(size)
        fieldCounts.append(count)
    print(f"TEST H1 PASSED: FileHeader write/read identical for {len(FILE_VERSIONS)} versions "
          f"({min(sizes)}-{max(sizes)} bytes, {min(fieldCounts)}-{max(fieldCounts)} fields)")

    sizes = []
    fieldCounts = []
    for version in FILE_VERSIONS:
        size, count = checkMeshBufferHeader(version)
        sizes.append(size)
        fieldCounts.append(count)
    print(f"TEST H2 PASSED: MeshBufferHeader write/read identical for {len(FILE_VERSIONS)} versions "
          f"({min(sizes)}-{max(sizes)} bytes, {min(fieldCounts)}-{max(fieldCounts)} fields)")

    # File versions are what call sites pass; 2102020001 shares RE3 RT's profile but has no
    # internal version of its own, so it is covered by the header tests only
    roundTripSizes = [checkInMemoryRoundTrip(fileVersion)
                      for fileVersion in sorted(meshFileVersionToInternalVersionDict)]
    print(f"TEST H3 PASSED: writeMesh/readMesh round-trip in memory for {len(roundTripSizes)} versions "
          f"({min(roundTripSizes)}-{max(roundTripSizes)} bytes)")

    checkVersionResolution()
    print(f"TEST H4 PASSED: version namespaces resolve for "
          f"{len(newVersionToMeshFileVersion)} versions plus both fallbacks")

    assert VERSION_PRAG > VERSION_SF6
    print("\nALL MESH HEADER TESTS PASSED")


if __name__ == "__main__":
    main()
