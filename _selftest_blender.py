# Blender background test: addon features end-to-end (run with blender --background --factory-startup --python)
import bpy
import io
import os
import sys
import contextlib
import tempfile
import traceback

ADDON_ROOT = os.path.dirname(os.path.abspath(__file__))
if ADDON_ROOT not in sys.path:
    sys.path.insert(0, ADDON_ROOT)

FAILURES = []


def check(label, condition, detail=""):
    status = "PASSED" if condition else "FAILED"
    print(f"[{status}] {label}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


# ---------------------------------------------------------------- 1. Addon registration
def test_addon_props():
    try:
        bpy.ops.preferences.addon_enable(module="RE-Mesh-Editor")
        props = bpy.ops.re_mesh.importfile.get_rna_type().properties.keys()
        check("Import operator registers hideArmature option", "hideArmature" in props)
        check("Import operator registers importOcclusionMeshes option", "importOcclusionMeshes" in props)
    except Exception as err:
        check("addon_enable", False, str(err))


# ---------------------------------------------------------------- 2. Export warnings
def buildScene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    arm = bpy.data.armatures.new("Arm")
    armObj = bpy.data.objects.new("Armature", arm)
    bpy.context.scene.collection.objects.link(armObj)
    bpy.context.view_layer.objects.active = armObj
    bpy.ops.object.mode_set(mode='EDIT')
    e1 = arm.edit_bones.new("J_Bip_C_Head")
    e1.head, e1.tail = (0, 0, 0), (0, 0, 0.1)
    e2 = arm.edit_bones.new("J_Sec_Hair_A")
    e2.head, e2.tail = (0, 0, 0.1), (0, 0, 0.2)
    bpy.ops.object.mode_set(mode='OBJECT')

    def makeMesh(name, materialName, weights):
        me = bpy.data.meshes.new("m_" + name)
        me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        me.update()
        me.uv_layers.new(name="UVMap")
        mat = bpy.data.materials.get(materialName) or bpy.data.materials.new(materialName)
        me.materials.append(mat)
        obj = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(obj)
        for groupName, vertIndices in weights.items():
            vg = obj.vertex_groups.new(name=groupName)
            vg.add(vertIndices, 1.0, 'REPLACE')
        return obj

    # Properly named mesh, but weighted to a group that is not a bone
    makeMesh("Group_0_Sub_0__TestMat", "TestMat",
             {"J_Bip_C_Head": [0, 1, 2], "BogusGroup": [0]})
    # Badly named mesh (no Group_ token, no __material suffix)
    makeMesh("BadName", "TestMat", {"J_Sec_Hair_A": [0, 1, 2]})


EXPORT_OPTIONS = {"targetCollection": "", "selectedOnly": False, "exportAllLODs": False,
                  "exportBlendShapes": True, "rotate90": True, "useBlenderMaterialName": False,
                  "preserveBoneMatrices": False, "exportBoundingBoxes": False,
                  "autoSolveRepeatedUVs": False, "preserveSharpEdges": False}


def test_export_warnings(tmpDir):
    import modules.mesh.blender_re_mesh as m

    # Don't pop message boxes in background mode
    m.showMessageBox = lambda *a, **k: None

    meshPath = os.path.join(tmpDir, "test.mesh.221108797")

    # 2a. Naming scheme + invalid bone group warnings (no MDF next to the file)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = m.exportREMeshFile(meshPath, dict(EXPORT_OPTIONS))
    out = buf.getvalue()
    check("export with warnings succeeds", result is True)
    check("InvalidMeshNamingScheme warning raised", "Invalid Mesh Naming Scheme" in out)
    check("naming warning lists the bad object", "[BadName]" in out)
    check("properly named object not flagged", "[Group_0_Sub_0__TestMat]" not in out.split("Vertex Group Weighted To Missing Bone")[0].split("Items with this warning")[-1])
    check("VertexGroupsNotOnArmature warning raised", "Vertex Group Weighted To Missing Bone" in out)
    check("invalid group name listed", "BogusGroup" in out)
    check("no blocking errors raised", "Unable to export mesh" not in out)
    check("mesh file written", os.path.isfile(meshPath))

    # 2b. MDF material mismatch warnings (stub the MDF reader)
    class StubMaterial:
        def __init__(self, name):
            self.materialName = name

    class StubMDF:
        materialList = [StubMaterial("OtherMat")]

    originalFindMDF = m.findMDFPathFromMeshPath
    originalReadMDF = m.readMDF
    m.findMDFPathFromMeshPath = lambda meshPath, gameName=None: meshPath
    m.readMDF = lambda path: StubMDF()
    try:
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            result2 = m.exportREMeshFile(meshPath, dict(EXPORT_OPTIONS))
        out2 = buf2.getvalue()
    finally:
        m.findMDFPathFromMeshPath = originalFindMDF
        m.readMDF = originalReadMDF
    check("export still succeeds with MDF mismatch", result2 is True)
    check("MeshMaterialsMissingFromMDF warning raised", "Mesh Material Missing From MDF" in out2)
    check("missing material name listed", "[TestMat]" in out2)
    check("MDFMaterialsMissingFromMesh warning raised", "MDF Material Not Used By Mesh" in out2)
    check("unused MDF material listed", "[OtherMat]" in out2)


# ---------------------------------------------------------------- 3. Streaming tex conversion
def makeFakeDDS(width, height):
    from modules.dds.file_dds import DDS, DX10_Header
    dds = DDS()
    dds.header.dwSize = 124
    dds.header.dwFlags = 0x00000001 | 0x00000002 | 0x00000004 | 0x00001000 | 0x00020000 | 0x00080000
    dds.header.dwHeight = height
    dds.header.dwWidth = width
    dds.header.dwPitchOrLinearSize = (width * height * 8) // 8
    dds.header.dwDepth = 1
    dataSize = 0
    mip = 0
    while True:
        mipW = max(width >> mip, 1)
        mipH = max(height >> mip, 1)
        dataSize += ((mipW + 3) // 4) * ((mipH + 3) // 4) * 16
        if mipW == 1 and mipH == 1:
            break
        mip += 1
    dds.header.dwMipMapCount = mip + 1
    dds.header.ddpfPixelFormat.dwSize = 32
    dds.header.ddpfPixelFormat.dwFlags = 0x4
    dds.header.ddpfPixelFormat.dwFourCC = 808540228  # DX10
    dds.header.ddsCaps1 = 0x00001000 | 0x00400000
    dds.header.ddsCaps2 = 0
    dds.header.dx10Header = DX10_Header()
    dds.header.dx10Header.dxgiFormat = 98  # BC7_UNORM
    dds.header.dx10Header.resourceDimension = 3
    dds.header.dx10Header.arraySize = 1
    dds.header.dx10Header.miscFlags2 = 0
    dds.data = bytes(bytearray((i * 7 + 3) & 0xFF for i in range(dataSize)))
    return dds


def test_streaming_tex(tmpDir):
    from modules.dds.file_dds import DDSFile
    from modules.tex.blender_re_tex import convertTexDDSList
    from modules.tex.file_re_tex import RE_TexFile

    inDir = os.path.join(tmpDir, "in")
    outDir = os.path.join(tmpDir, "converted")
    os.makedirs(inDir)
    for name, size in (("test", 256), ("small", 64)):
        ddsFile = DDSFile()
        ddsFile.dds = makeFakeDDS(size, size)
        ddsFile.write(os.path.join(inDir, name + ".dds"))

    convertTexDDSList(["test.dds", "small.dds"], inDir, outDir, "MHWILDS",
                      createStreamingTex=True)

    texVersion = "241106027"
    mainPath = os.path.join(outDir, "test.tex." + texVersion)
    streamPath = os.path.join(outDir, "test #STREAMING.tex." + texVersion)
    smallPath = os.path.join(outDir, "small.tex." + texVersion)
    smallStreamPath = os.path.join(outDir, "small #STREAMING.tex." + texVersion)
    check("main tex written", os.path.isfile(mainPath))
    check("streaming tex written", os.path.isfile(streamPath))
    check("small tex written without streaming companion",
          os.path.isfile(smallPath) and not os.path.isfile(smallStreamPath))

    mainTex = RE_TexFile()
    mainTex.read(mainPath)
    check("main tex holds low mips", mainTex.tex.header.width == 64
          and len(mainTex.tex.imageMipDataList[0]) == 7,
          f"width={mainTex.tex.header.width} mips={len(mainTex.tex.imageMipDataList[0])}")
    streamTex = RE_TexFile()
    streamTex.read(streamPath)
    check("streaming tex holds high mips", streamTex.tex.header.width == 256
          and len(streamTex.tex.imageMipDataList[0]) == 2,
          f"width={streamTex.tex.header.width} mips={len(streamTex.tex.imageMipDataList[0])}")


# ---------------------------------------------------------------- 4. Occlusion import branch
def test_occlusion_import():
    import modules.mesh.blender_re_mesh as m
    from modules.mesh.re_mesh_parse import ParsedREMesh, LODLevel, VisconGroup, SubMesh

    m.MERGE_SAME_MATERIAL_SUBMESHES = False  # plain path for the synthetic submesh

    parsedMesh = ParsedREMesh()
    parsedMesh.materialNameList = ["TestMat"]
    lod = LODLevel()
    lod.lodDistance = 100.0
    viscon = VisconGroup()
    viscon.visconGroupNum = 0
    sub = SubMesh()
    sub.materialIndex = 0
    sub.subMeshIndex = 0
    sub.meshVertexOffset = 0
    sub.isReusedMesh = False
    sub.vertexPosList = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    sub.faceList = [(0, 1, 2)]
    sub.normalList = [(0, 0, 1)] * 3
    sub.uvList = [(0, 0), (1, 0), (0, 1)]
    sub.blendShapeList = []
    viscon.subMeshList = [sub]
    lod.visconGroupList = [viscon]
    parsedMesh.occlusionMeshLODList = [lod]

    objectNamesBefore = set(obj.name for obj in bpy.data.objects)
    materialDict = m.createMaterialDict(["TestMat"])
    m.importLODGroup(parsedMesh, "Occlusion Mesh", bpy.context.scene.collection,
                     materialDict, None, set(), {}, importAllLODs=False,
                     createCollections=True, importShadowMeshes=False, rotate90=True,
                     mergeGroups=False, importBoundingBoxes=False)
    newObjects = [obj for obj in bpy.data.objects
                  if obj.name not in objectNamesBefore
                  and obj.name.startswith("Group_0_Sub_0__TestMat")]
    check("Occlusion Mesh import creates mesh objects", len(newObjects) == 1,
          f"new={len(newObjects)}")
    if newObjects:
        check("occlusion object has geometry", len(newObjects[0].data.polygons) == 1)


def main():
    tmpDir = tempfile.mkdtemp(prefix="remeshtest_")
    test_addon_props()
    try:
        buildScene()
        test_export_warnings(tmpDir)
    except Exception:
        check("export warnings test crashed", False, traceback.format_exc())
    try:
        test_streaming_tex(tmpDir)
    except Exception:
        check("streaming tex test crashed", False, traceback.format_exc())
    try:
        test_occlusion_import()
    except Exception:
        check("occlusion import test crashed", False, traceback.format_exc())

    print("\n================================")
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        raise SystemExit(1)
    print("ALL BLENDER TESTS PASSED")


main()
