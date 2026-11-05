# System-python test: per-version mesh write/read byte identity and parsed vertex data (no bpy needed)
import contextlib
import io
import os
import sys
from io import BytesIO

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

import numpy as np
from modules.mesh.file_re_mesh import ParsedREMeshToREMesh, readMesh, writeMesh
from modules.mesh.mesh_versions import newVersionToMeshFileVersion
from modules.mesh.re_mesh_parse import LODLevel, ParsedREMesh, SubMesh, VisconGroup

# The remapped versions, one per game profile, as in test_mesh_headers.py
FILE_VERSIONS = sorted(newVersionToMeshFileVersion)

# Two submeshes with different vertex counts and material indices, so a shifted vertex or
# face start index, a swapped material or a lost buffer shows up as a value mismatch
VERTEX_COUNTS = (6, 10)
STREAMS = ("vertexPosList", "normalList", "tangentList", "uvList", "weightList",
           "weightIndicesList", "colorList", "faceList")


def buildParsedMesh():
	parsedMesh = ParsedREMesh()
	parsedMesh.materialNameList = ["m_a", "m_b"]
	parsedMesh.bufferHasNorTan = True
	parsedMesh.bufferHasUV = True
	parsedMesh.bufferHasWeight = True
	parsedMesh.bufferHasColor = True
	lod = LODLevel()
	lod.lodDistance = 5.0
	viscon = VisconGroup()
	for index, vertexCount in enumerate(VERTEX_COUNTS):
		subMesh = SubMesh()
		subMesh.materialIndex = index
		subMesh.subMeshIndex = index
		subMesh.vertexPosList = (np.arange(vertexCount * 3, dtype=np.float32).reshape(vertexCount, 3)
		                         + float(index * 100))
		subMesh.normalList = np.tile(np.array([0.0, 1.0, 0.0], dtype=np.float32), (vertexCount, 1))
		subMesh.tangentList = np.ones((vertexCount, 4), dtype=np.int8)
		subMesh.uvList = np.tile(np.array([0.5, 0.25], dtype=np.float32), (vertexCount, 1))
		subMesh.weightIndicesList = np.zeros((vertexCount, 8), dtype=np.uint8)
		subMesh.weightList = np.full((vertexCount, 8), 0.125, dtype=np.float32)
		subMesh.colorList = np.tile(np.array([1.0, 0.5, 0.25, 1.0], dtype=np.float32),
		                            (vertexCount, 1))
		subMesh.faceList = [[0, 1, 2], [2, 1, 3]]
		viscon.subMeshList.append(subMesh)
	lod.visconGroupList.append(viscon)
	parsedMesh.mainMeshLODList.append(lod)
	return parsedMesh


def parsedValues(reMesh):
	"""The per-submesh vertex and face data a reader gets back out of a written mesh."""
	parsedMesh = ParsedREMesh()
	parsedMesh.ParseREMesh(reMesh)
	return [subMesh for lod in parsedMesh.mainMeshLODList
	        for viscon in lod.visconGroupList for subMesh in viscon.subMeshList]


def serialize(reMesh, versionIdentifier):
	stream = BytesIO()
	writeMesh(reMesh, stream, versionIdentifier)
	# REMesh.write seeks past the last section, leaving a hole in the stream
	return stream.getvalue().ljust(reMesh.fileHeader.fileSize, b"\x00")


def describeBytes(source, rewritten, version):
	if len(source) != len(rewritten):
		return f"wrote {len(source)} bytes, rewrote {len(rewritten)}"
	for index, (sourceByte, rewrittenByte) in enumerate(zip(source, rewritten)):
		if sourceByte != rewrittenByte:
			return f"byte {index} read back as {rewrittenByte}, wrote {sourceByte}"
	return ""


def compareParsed(reMesh, restored, version):
	"""Compare the parsed submesh streams of the written mesh and of the mesh read back."""
	sourceValues = parsedValues(reMesh)
	restoredValues = parsedValues(restored)
	assert len(sourceValues) == len(restoredValues), (
		f"v{version}: parsed {len(restoredValues)} submeshes, wrote {len(sourceValues)}")
	assert len({len(subMesh.vertexPosList) for subMesh in sourceValues}) == len(VERTEX_COUNTS), (
		f"v{version}: submeshes did not keep their differing vertex counts")
	for index, (source, other) in enumerate(zip(sourceValues, restoredValues)):
		assert source.materialIndex == other.materialIndex, (
			f"v{version}: submesh {index} materialIndex read {other.materialIndex}, "
			f"wrote {source.materialIndex}")
		assert len(source.vertexPosList) == len(other.vertexPosList), (
			f"v{version}: submesh {index} has {len(other.vertexPosList)} vertices, "
			f"wrote {len(source.vertexPosList)}")
		for stream in STREAMS:
			sourceStream = getattr(source, stream)
			assert len(sourceStream) != 0, f"v{version}: submesh {index} wrote no {stream}"
			assert np.array_equal(np.asarray(sourceStream), np.asarray(getattr(other, stream))), (
				f"v{version}: submesh {index} {stream} changed on the way through the file")


def checkRoundTrip(remappedVersion):
	fileVersion = newVersionToMeshFileVersion[remappedVersion]
	with contextlib.redirect_stdout(io.StringIO()):
		reMesh = ParsedREMeshToREMesh(buildParsedMesh(), fileVersion)
		# ParsedREMeshToREMesh leaves meshVersion at 0, but a read-back mesh carries the
		# file version and some version tags are derived from it
		reMesh.meshVersion = fileVersion
		data = serialize(reMesh, fileVersion)
		restored = readMesh(BytesIO(data), versionIdentifier=fileVersion)
		rewritten = serialize(restored, fileVersion)
		compareParsed(reMesh, restored, remappedVersion)
	assert data == rewritten, describeBytes(data, rewritten, remappedVersion)
	return len(data)


def main():
	failures = 0
	for remappedVersion in FILE_VERSIONS:
		try:
			size = checkRoundTrip(remappedVersion)
		except AssertionError as error:
			failures += 1
			print(f"TEST R1 FAILED v{remappedVersion}: {error}")
		else:
			print(f"TEST R1 PASSED v{remappedVersion}: round-tripped {size} bytes")
	if failures:
		print(f"{failures} OF {len(FILE_VERSIONS)} MESH ROUND TRIP TESTS FAILED")
		sys.exit(1)
	print(f"ALL {len(FILE_VERSIONS)} MESH ROUND TRIP TESTS PASSED")


if __name__ == "__main__":
	main()