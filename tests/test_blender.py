# Blender background test: addon features end-to-end (run with blender --background --factory-startup --python)
import bpy
import io
import os
import sys
import contextlib
import tempfile
import traceback

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

# ---------------------------------------------------------------- 2c. Combined mesh+MDF export
def test_export_mesh_with_mdf(tmpDir):
    """The regular mesh export writes the MDF alongside the mesh when the
    "Export MDF" option is checked and an MDF collection is present in the
    scene collection."""
    import modules.mesh.blender_re_mesh_export as mex
    mex.showMessageBox = lambda *a, **k: None

    exporter = bpy.ops.re_mesh.exportfile.get_rna_type()
    check("mesh export operator is registered", "exportfile" in dir(bpy.ops.re_mesh))
    props = exporter.properties.keys()
    check("mesh export operator has mesh collection option", "targetCollection" in props)
    check("mesh export operator has mesh version option", "filename_ext" in props)
    check("mesh export operator has Export MDF option", "exportMDF" in props)
    check("combined export operator removed",
          "exportfile_with_mdf" not in dir(bpy.ops.re_mesh))

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)

    parent = bpy.data.collections.new("Char")
    bpy.context.scene.collection.children.link(parent)
    meshCol = bpy.data.collections.new("Char.mesh")
    parent.children.link(meshCol)
    meshCol["~TYPE"] = "RE_MESH_COLLECTION"
    mdfCol = bpy.data.collections.new("Char.mdf2")
    parent.children.link(mdfCol)
    mdfCol["~TYPE"] = "RE_MDF_COLLECTION"

    me = bpy.data.meshes.new("m")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    me.update()
    me.uv_layers.new(name="UVMap")
    me.materials.append(bpy.data.materials.new("Shirts_Mat"))
    obj = bpy.data.objects.new("Group_0_Sub_0__Shirts_Mat", me)
    meshCol.objects.link(obj)

    matObj = bpy.data.objects.new("Material 00 (Shirts_Mat)", None)
    matObj["~TYPE"] = "RE_MDF_MATERIAL"
    matObj.re_mdf_material.materialName = "Shirts_Mat"
    mdfCol.objects.link(matObj)

    meshPath = os.path.join(tmpDir, "Char.mesh.221108797")
    mdfPath = os.path.join(tmpDir, "Char.mdf2.32")

    # 2c1. Export MDF option off -> only the mesh is written, even with an MDF collection present
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="Char.mesh")
    check("plain mesh export finishes", result == {'FINISHED'})
    check("plain mesh export writes mesh file", os.path.isfile(meshPath))
    check("plain mesh export does NOT write MDF", not os.path.isfile(mdfPath))
    os.remove(meshPath)

    # 2c2. Export MDF option on -> mesh + MDF written together
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="Char.mesh", exportMDF=True)
    check("mesh+MDF export finishes", result == {'FINISHED'})
    check("mesh file written", os.path.isfile(meshPath))
    check("MDF file written alongside mesh", os.path.isfile(mdfPath))
    check("MDF collection stores export path", "BatchExport_path" in mdfCol and mdfCol["BatchExport_path"] == mdfPath)

    # 2c3. No MDF collection in scene -> only the mesh is written
    bpy.data.objects.remove(matObj, do_unlink=True)
    bpy.data.collections.remove(mdfCol)
    meshPath2 = os.path.join(tmpDir, "Char2.mesh.221108797")
    result2 = bpy.ops.re_mesh.exportfile(filepath=meshPath2, targetCollection="Char.mesh", exportMDF=True)
    check("mesh export finishes without MDF collection", result2 == {'FINISHED'})
    check("mesh still exported without MDF collection", os.path.isfile(meshPath2))
    check("no MDF written when MDF collection absent",
          not os.path.isfile(os.path.join(tmpDir, "Char2.mdf2.32")))

# ---------------------------------------------------------------- 2d. Renamed MDF must be read live
def test_mdf_renamed_in_blender(tmpDir):
    """Regression: after renaming mesh/materials in Blender, the exporter must
    compare against the MDF data in the scene, not a stale .mdf2 on disk.
    A stale disk MDF is simulated by stubbing findMDFPathFromMeshPath/readMDF to
    return the OLD material names; the live MDF collection holds the NEW names."""
    import modules.mesh.blender_re_mesh as m
    import modules.mesh.blender_re_mesh_export as mex
    mex.showMessageBox = lambda *a, **k: None

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)

    arm = bpy.data.armatures.new("Arm")
    armObj = bpy.data.objects.new("Armature", arm)
    bpy.context.scene.collection.objects.link(armObj)
    bpy.context.view_layer.objects.active = armObj
    bpy.ops.object.mode_set(mode='EDIT')
    e1 = arm.edit_bones.new("J_Bip_C_Head")
    e1.head, e1.tail = (0, 0, 0), (0, 0, 0.1)
    bpy.ops.object.mode_set(mode='OBJECT')

    parent = bpy.data.collections.new("HookGun")
    bpy.context.scene.collection.children.link(parent)
    meshCol = bpy.data.collections.new("HookGun.mesh")
    parent.children.link(meshCol)
    meshCol["~TYPE"] = "RE_MESH_COLLECTION"
    mdfCol = bpy.data.collections.new("HookGun.mdf2")
    parent.children.link(mdfCol)
    mdfCol["~TYPE"] = "RE_MDF_COLLECTION"

    me = bpy.data.meshes.new("m")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    me.update()
    me.uv_layers.new(name="UVMap")
    me.materials.append(bpy.data.materials.new("NewMat"))
    obj = bpy.data.objects.new("Group_0_Sub_0__NewMat", me)
    meshCol.objects.link(obj)
    vg = obj.vertex_groups.new(name="J_Bip_C_Head")
    vg.add([0, 1, 2], 1.0, 'REPLACE')

    # Live MDF material object holding the NEW (renamed) material name
    matObj = bpy.data.objects.new("Material 00 (NewMat)", None)
    matObj["~TYPE"] = "RE_MDF_MATERIAL"
    matObj.re_mdf_material.materialName = "NewMat"
    mdfCol.objects.link(matObj)

    meshPath = os.path.join(tmpDir, "HookGun.mesh.221108797")
    options = dict(EXPORT_OPTIONS)
    options["targetCollection"] = meshCol.name

    class StubMaterial:
        def __init__(self, name):
            self.materialName = name

    class StaleMDF:
        materialList = [StubMaterial("OldMat")]

    originalFindMDF = mex.findMDFPathFromMeshPath
    originalReadMDF = mex.readMDF
    mex.findMDFPathFromMeshPath = lambda meshPath, gameName=None: meshPath
    mex.readMDF = lambda path: StaleMDF()
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = m.exportREMeshFile(meshPath, dict(options))
        out = buf.getvalue()
    finally:
        mex.findMDFPathFromMeshPath = originalFindMDF
        mex.readMDF = originalReadMDF

    check("renamed-mdf export succeeds", result is True)
    check("stale-disk MDF material NOT reported as unused",
          "[OldMat]" not in out)
    check("renamed material NOT reported as missing from MDF",
          "[NewMat]" not in out)
    check("no MDF material mismatch warning at all",
          "MDF Material Not Used By Mesh" not in out
          and "Mesh Material Missing From MDF" not in out)


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
    from modules.mesh.blender_re_mesh_import import importLODGroup
    from modules.mesh.blender_re_mesh_utils import createMaterialDict
    from modules.mesh.re_mesh_parse import ParsedREMesh, LODLevel, VisconGroup, SubMesh

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
    materialDict = createMaterialDict(["TestMat"])
    importLODGroup(parsedMesh, "Occlusion Mesh", bpy.context.scene.collection,
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


# ---------------------------------------------------------------- 4b. importMesh must accept numpy buffers directly
def test_import_meshbuffer_numpy():
    """Parsed meshes carry numpy buffers; the import hot path must accept them
    without truth-value errors (`arr or []` raises on array truthiness)."""
    import numpy as np
    import modules.mesh.blender_re_mesh as m
    from modules.mesh.blender_re_mesh_import import importLODGroup
    from modules.mesh.blender_re_mesh_utils import createMaterialDict
    from modules.mesh.re_mesh_parse import ParsedREMesh, LODLevel, VisconGroup, SubMesh

    parsedMesh = ParsedREMesh()
    parsedMesh.materialNameList = ["TestMat"]
    lod = LODLevel()
    lod.lodDistance = 100.0
    viscon = VisconGroup()
    viscon.visconGroupNum = 0
    sub = SubMesh()
    sub.materialIndex = 0
    sub.subMeshIndex = 1
    sub.meshVertexOffset = 0
    sub.isReusedMesh = False
    sub.vertexPosList = np.asarray([(0, 0, 0), (1, 0, 0), (0, 1, 0)], dtype=np.float32)
    sub.faceList = np.asarray([(0, 1, 2)], dtype=np.uint32)
    sub.normalList = np.asarray([(0, 0, 1)] * 3, dtype=np.float32)
    sub.uvList = np.asarray([(0, 0), (1, 0), (0, 1)], dtype=np.float32)
    sub.blendShapeList = []
    viscon.subMeshList = [sub]
    lod.visconGroupList = [viscon]
    parsedMesh.occlusionMeshLODList = [lod]

    objectNamesBefore = set(obj.name for obj in bpy.data.objects)
    materialDict = createMaterialDict(["TestMat"])
    importLODGroup(parsedMesh, "Occlusion Mesh", bpy.context.scene.collection,
                     materialDict, None, set(), {}, importAllLODs=False,
                     createCollections=True, importShadowMeshes=False, rotate90=True,
                     mergeGroups=False, importBoundingBoxes=False)
    newObjects = [obj for obj in bpy.data.objects
                  if obj.name not in objectNamesBefore
                  and obj.name.startswith("Group_0_Sub_1__TestMat")]
    check("numpy import creates mesh objects", len(newObjects) == 1,
          f"new={len(newObjects)}")
    if newObjects:
        check("numpy import has geometry", len(newObjects[0].data.polygons) == 1)


# ---------------------------------------------------------------- 4. Rename meshes keeps MDF material names in sync
def test_rename_meshes_syncs_mdf_materials(tmpDir):
    """The RE engine requires the mesh name suffix (after the Group_/Sub_ prefix)
    to match the MDF material name one-to-one, so renaming meshes must also rename
    the matching MDF materials."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)

    parent = bpy.data.collections.new("HookGun")
    bpy.context.scene.collection.children.link(parent)
    meshCol = bpy.data.collections.new("HookGun.mesh")
    parent.children.link(meshCol)
    meshCol["~TYPE"] = "RE_MESH_COLLECTION"
    mdfCol = bpy.data.collections.new("HookGun.mdf2")
    parent.children.link(mdfCol)
    mdfCol["~TYPE"] = "RE_MDF_COLLECTION"

    me = bpy.data.meshes.new("m")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    me.update()
    me.materials.append(bpy.data.materials.new("Shirts_Mat"))
    obj = bpy.data.objects.new("Group_0_Sub_0__Shirts_Mat", me)
    meshCol.objects.link(obj)

    matObj = bpy.data.objects.new("Material 00 (Shirts_Mat)", None)
    matObj["~TYPE"] = "RE_MDF_MATERIAL"
    matObj.re_mdf_material.materialName = "Shirts_Mat"
    mdfCol.objects.link(matObj)
    # Unrelated MDF material must stay untouched
    otherObj = bpy.data.objects.new("Material 00 (OtherMat)", None)
    otherObj["~TYPE"] = "RE_MDF_MATERIAL"
    otherObj.re_mdf_material.materialName = "OtherMat"
    mdfCol.objects.link(otherObj)

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    result = bpy.ops.re_mesh.rename_meshes()

    check("rename meshes operator finishes", result == {'FINISHED'})
    check("mesh renamed with new suffix", obj.name == "Group_0_Sub_0__Shirts", obj.name)
    check("MDF material renamed to match new suffix",
          matObj.re_mdf_material.materialName == "Shirts", matObj.re_mdf_material.materialName)
    check("MDF material object name updated", matObj.name == "Material 00 (Shirts)", matObj.name)
    check("unrelated MDF material untouched",
          otherObj.re_mdf_material.materialName == "OtherMat")

    # Fallback: mesh not linked to a mesh collection still renames scene MDF materials
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)

    me2 = bpy.data.meshes.new("m2")
    me2.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    me2.update()
    me2.materials.append(bpy.data.materials.new("Cloth_Mat"))
    obj2 = bpy.data.objects.new("Group_1_Sub_2__Cloth_Mat", me2)
    bpy.context.scene.collection.objects.link(obj2)

    mdfCol2 = bpy.data.collections.new("Loose.mdf2")
    bpy.context.scene.collection.children.link(mdfCol2)
    mdfCol2["~TYPE"] = "RE_MDF_COLLECTION"
    matObj2 = bpy.data.objects.new("Material 00 (Cloth_Mat)", None)
    matObj2["~TYPE"] = "RE_MDF_MATERIAL"
    matObj2.re_mdf_material.materialName = "Cloth_Mat"
    mdfCol2.objects.link(matObj2)

    bpy.context.view_layer.objects.active = obj2
    bpy.ops.object.select_all(action='DESELECT')
    obj2.select_set(True)
    bpy.ops.re_mesh.rename_meshes()

    check("mesh renamed without mesh collection",
          obj2.name == "Group_1_Sub_0__Cloth", obj2.name)
    check("fallback MDF material renamed",
          matObj2.re_mdf_material.materialName == "Cloth", matObj2.re_mdf_material.materialName)


def main():
    tmpDir = tempfile.mkdtemp(prefix="remeshtest_")
    test_addon_props()
    try:
        test_export_mesh_with_mdf(tmpDir)
    except Exception:
        check("mesh+mdf export test crashed", False, traceback.format_exc())
    try:
        test_mdf_renamed_in_blender(tmpDir)
    except Exception:
        check("renamed-mdf test crashed", False, traceback.format_exc())
    try:
        test_rename_meshes_syncs_mdf_materials(tmpDir)
    except Exception:
        check("rename-meshes MDF sync test crashed", False, traceback.format_exc())
    try:
        test_streaming_tex(tmpDir)
    except Exception:
        check("streaming tex test crashed", False, traceback.format_exc())
    try:
        test_occlusion_import()
    except Exception:
        check("occlusion import test crashed", False, traceback.format_exc())
    try:
        test_import_meshbuffer_numpy()
    except Exception:
        check("numpy import test crashed", False, traceback.format_exc())

    print("\n================================")
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        raise SystemExit(1)
    print("ALL BLENDER TESTS PASSED")


main()
