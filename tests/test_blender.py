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
    from modules.mdf.file_re_mdf import readMDF
    from modules.mesh.file_re_mesh import readREMesh
    from modules.mesh.re_mesh_parse import ParsedREMesh
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

    # Second submesh sharing the same material, like a too-big mesh split into parts
    me2 = bpy.data.meshes.new("m2")
    me2.from_pydata([(2, 0, 0), (3, 0, 0), (2, 1, 0)], [], [(0, 1, 2)])
    me2.update()
    me2.uv_layers.new(name="UVMap")
    me2.materials.append(me.materials[0])
    obj2 = bpy.data.objects.new("Group_0_Sub_1__Shirts_Mat", me2)
    meshCol.objects.link(obj2)

    matObj = bpy.data.objects.new("Material 00 (Shirts_Mat)", None)
    matObj["~TYPE"] = "RE_MDF_MATERIAL"
    matObj.re_mdf_material.materialName = "Shirts_Mat"
    mdfCol.objects.link(matObj)

    # MDF material that no mesh in the collection uses
    unusedMatObj = bpy.data.objects.new("Material 01 (Pants_Mat)", None)
    unusedMatObj["~TYPE"] = "RE_MDF_MATERIAL"
    unusedMatObj.re_mdf_material.materialName = "Pants_Mat"
    mdfCol.objects.link(unusedMatObj)

    meshPath = os.path.join(tmpDir, "Char.mesh.221108797")
    mdfPath = os.path.join(tmpDir, "Char.mdf2.32")

    # 2c1. Export MDF option off -> only the mesh is written, even with an MDF collection present
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="Char.mesh")
    check("plain mesh export finishes", result == {'FINISHED'})
    check("plain mesh export writes mesh file", os.path.isfile(meshPath))
    check("plain mesh export does NOT write MDF", not os.path.isfile(mdfPath))
    os.remove(meshPath)

    # 2c2. Export MDF option on -> mesh + MDF written together, keeping every MDF material
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="Char.mesh", exportMDF=True)
    check("mesh+MDF export finishes", result == {'FINISHED'})
    check("mesh file written", os.path.isfile(meshPath))
    check("MDF file written alongside mesh", os.path.isfile(mdfPath))
    check("MDF collection stores export path", "BatchExport_path" in mdfCol and mdfCol["BatchExport_path"] == mdfPath)
    exportedNames = set(mat.materialName for mat in readMDF(mdfPath).materialList)
    check("MDF keeps materials the mesh does not use",
          exportedNames == {"Shirts_Mat", "Pants_Mat"}, str(exportedNames))

    # Read the exported mesh back: the two split submeshes share Shirts_Mat, so the
    # material name must appear once and both submeshes must reference that index.
    parsedMesh = ParsedREMesh()
    parsedMesh.ParseREMesh(readREMesh(meshPath))
    check("split submeshes dedupe to one shared material",
          parsedMesh.materialNameList == ["Shirts_Mat"], str(parsedMesh.materialNameList))
    sharedSubs = [sm for lod in parsedMesh.mainMeshLODList
                  for vg in lod.visconGroupList for sm in vg.subMeshList]
    check("both split submeshes exported", len(sharedSubs) == 2, str(len(sharedSubs)))
    check("both split submeshes point at the shared material",
          all(sm.materialIndex == 0 for sm in sharedSubs),
          str([sm.materialIndex for sm in sharedSubs]))

    # 2c2b. "Exclude Unused Materials" -> the MDF is written without the materials the mesh lacks
    check("mesh export operator has Exclude Unused Materials option",
          "exportMDFExcludeUnused" in bpy.ops.re_mesh.exportfile.get_rna_type().properties.keys())
    os.remove(mdfPath)
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="Char.mesh",
                                        exportMDF=True, exportMDFExcludeUnused=True)
    check("mesh+MDF export with unused materials excluded finishes", result == {'FINISHED'})
    exportedNames = set(mat.materialName for mat in readMDF(mdfPath).materialList)
    check("unused MDF material excluded from exported MDF", exportedNames == {"Shirts_Mat"},
          str(exportedNames))
    check("MDF material count matches mesh material count", len(exportedNames) == 1)

    # 2c3. No MDF collection in scene -> only the mesh is written
    bpy.data.objects.remove(unusedMatObj, do_unlink=True)
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


def test_export_mdf_object_name_wins(tmpDir):
    """Regression (user report): the mesh file names materials from the mesh object
    name ("Group_0_Sub_10__Siwa"), but the MDF export wrote/filtered them by the
    materialName property. Renaming an MDF material object in the outliner (the
    "Material 12 (Siwa)" convention) left that property stale, so used materials
    were lost from the combined export - and with "exclude unused" enabled the
    whole file could be dropped. buildMDF must now pull the name from the
    parentheses the same way reindexMaterials does."""
    import modules.mesh.blender_re_mesh_export as mex
    from modules.mdf.file_re_mdf import readMDF
    from modules.gen_functions import parseMDFObjectNameMaterialName
    mex.showMessageBox = lambda *a, **k: None

    weird = "material 00 (Body_00_C001_Inst_Mesh_00_EyeUpdate)"
    check("paren parser reads standard name",
          parseMDFObjectNameMaterialName("Material 12 (Siwa)") == "Siwa")
    check("paren parser keeps nested parens in material name",
          parseMDFObjectNameMaterialName("Material 00 (" + weird + ")") == weird,
          str(parseMDFObjectNameMaterialName("Material 00 (" + weird + ")")))
    check("paren parser ignores .001 usersuffix",
          parseMDFObjectNameMaterialName("Material 00 (Siwa).001") == "Siwa")
    check("paren parser rejects non-conforming names",
          parseMDFObjectNameMaterialName("Siwa") is None
          and parseMDFObjectNameMaterialName("Material 00 ()") is None)

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)

    parent = bpy.data.collections.new("Renamed")
    bpy.context.scene.collection.children.link(parent)
    meshCol = bpy.data.collections.new("Renamed.mesh")
    parent.children.link(meshCol)
    meshCol["~TYPE"] = "RE_MESH_COLLECTION"
    mdfCol = bpy.data.collections.new("Renamed.mdf2")
    parent.children.link(mdfCol)
    mdfCol["~TYPE"] = "RE_MDF_COLLECTION"

    siwaMat = bpy.data.materials.new("Siwa")
    for subIndex in (10, 11):  # mesh too big, split into two submeshes sharing one material
        me = bpy.data.meshes.new("Renamed_m_" + str(subIndex))
        me.from_pydata([(subIndex, 0, 0), (subIndex + 1, 0, 0), (subIndex, 1, 0)], [], [(0, 1, 2)])
        me.update()
        me.uv_layers.new(name="UVMap")
        me.materials.append(siwaMat)
        meshCol.objects.link(bpy.data.objects.new("Group_0_Sub_" + str(subIndex) + "__Siwa", me))
    me = bpy.data.meshes.new("Renamed_m_body")
    me.from_pydata([(9, 0, 0), (10, 0, 0), (9, 1, 0)], [], [(0, 1, 2)])
    me.update()
    me.uv_layers.new(name="UVMap")
    me.materials.append(bpy.data.materials.new("Body"))
    meshCol.objects.link(bpy.data.objects.new("Group_0_Sub_0__Body", me))

    def addMDFMaterial(objectName, propertyValue):
        matObj = bpy.data.objects.new("TempMDFObject", None)
        matObj["~TYPE"] = "RE_MDF_MATERIAL"
        # Assign the property first, then rename: update_materialName pushes
        # property -> name, so only the rename direction stays out of sync -
        # exactly the state an outliner rename leaves behind.
        matObj.re_mdf_material.materialName = propertyValue
        matObj.name = objectName
        mdfCol.objects.link(matObj)
        return matObj

    siwaObj = addMDFMaterial("Material 12 (Siwa)", "Ch4_Siwa")
    addMDFMaterial("Material 00 (Body)", "Ch4_Body")
    addMDFMaterial("Material 05 (Unused)", "Unused")

    meshPath = os.path.join(tmpDir, "Renamed.mesh.221108797")
    mdfPath = os.path.join(tmpDir, "Renamed.mdf2.32")

    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="Renamed.mesh",
                                        exportMDF=True, exportMDFExcludeUnused=True)
    check("mesh+MDF export with renamed objects finishes", result == {'FINISHED'})
    check("MDF written despite outliner renames", os.path.isfile(mdfPath))
    exportedNames = set(mat.materialName for mat in readMDF(mdfPath).materialList) \
        if os.path.isfile(mdfPath) else set()
    check("outliner-renamed materials survive the unused-exclusion filter",
          exportedNames == {"Siwa", "Body"}, str(exportedNames))
    check("genuinely unused material still excluded", "Unused" not in exportedNames)
    check("rename synced onto the scene object",
          siwaObj.re_mdf_material.materialName == "Siwa", siwaObj.name)

    # Without the exclude option the file must not carry the stale property names either
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="Renamed.mesh",
                                        exportMDF=True)
    exportedNames = set(mat.materialName for mat in readMDF(mdfPath).materialList)
    check("renamed material written under mesh-matching name without exclusion",
          "Siwa" in exportedNames and "Ch4_Siwa" not in exportedNames, str(exportedNames))


def test_naming_helpers_and_mdf_order(tmpDir):
    """Naming convergence regression: the shared helpers in gen_functions must be the
    single source for mesh-material-name parsing/priority, and the exported MDF must
    list materials in case-insensitive name order - the same order the mesh exporter
    uses for its submeshes/material list - so the game's index-based mesh->MDF
    material binding lines up (previously buildMDF sorted case-sensitively)."""
    import modules.mesh.blender_re_mesh_export as mex
    from modules.gen_functions import (stripBlenderUserSuffix, parseREMeshObjectMaterialName,
                                       getREMeshMaterialName, resolveREMeshMaterialName,
                                       formatREMeshObjectName, formatREMaterialObjectName,
                                       parseMDFObjectNameMaterialName)
    from modules.mdf.file_re_mdf import readMDF
    from modules.mesh.file_re_mesh import readREMesh
    from modules.mesh.re_mesh_parse import ParsedREMesh
    mex.showMessageBox = lambda *a, **k: None

    check("user suffix stripped", stripBlenderUserSuffix("Body.001") == "Body")
    # #7: only Blender's numeric usersuffix is removed - legitimately dotted names survive
    check("dotted name not truncated", stripBlenderUserSuffix("Char.v2") == "Char.v2")
    check("multi-char numeric suffix preserved beyond 3 digits", stripBlenderUserSuffix("Body.1000") == "Body.1000")
    check("no suffix unchanged", stripBlenderUserSuffix("Body") == "Body")
    check("mesh name preserves dotted material",
          parseREMeshObjectMaterialName("Group_0_Sub_0__Char.v2") == "Char.v2")
    check("mesh name empty __ suffix parses to empty", parseREMeshObjectMaterialName("Group_0_Sub_0__") == "")
    # #6: resolveREMeshMaterialName is the single resolver, so the sort key and the
    # written name can never diverge; the resolver is what the exporter feeds both.
    check("resolver default uses the object-name suffix",
          resolveREMeshMaterialName("Group_0_Sub_0__FromName", "FromSlot") == "FromName")
    check("resolver with useBlenderMaterialName uses the slot",
          resolveREMeshMaterialName("Group_0_Sub_0__FromName", "FromSlot", True) == "FromSlot")
    check("resolver falls back to the slot when the suffix is empty",
          resolveREMeshMaterialName("Plain", "FromSlot") == "FromSlot")
    check("resolver returns the fallback with no source at all",
          resolveREMeshMaterialName("Plain", "", fallback="NO_ASSIGNED_MATERIAL") == "NO_ASSIGNED_MATERIAL")
    check("mesh name parses material",
          parseREMeshObjectMaterialName("LOD_0_Group_0_Sub_1__Shirts_Mat.001") == "Shirts_Mat")
    check("mesh name without marker parses to empty", parseREMeshObjectMaterialName("Plain") == "")
    check("mesh object name format round-trips",
          parseREMeshObjectMaterialName(formatREMeshObjectName(2, 3, "Mat")) == "Mat")
    check("material object name format round-trips",
          parseMDFObjectNameMaterialName(formatREMaterialObjectName("Material", 7, "Body")) == "Body")

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)

    # getREMeshMaterialName priority: "__" suffix by default, slot when the option is set
    parent = bpy.data.collections.new("Order")
    bpy.context.scene.collection.children.link(parent)
    meshCol = bpy.data.collections.new("Order.mesh")
    parent.children.link(meshCol)
    meshCol["~TYPE"] = "RE_MESH_COLLECTION"
    mdfCol = bpy.data.collections.new("Order.mdf2")
    parent.children.link(mdfCol)
    mdfCol["~TYPE"] = "RE_MDF_COLLECTION"

    def addMeshObj(name, matName, verts):
        me = bpy.data.meshes.new(name + "_me")
        me.from_pydata(verts, [], [(0, 1, 2)])
        me.update()
        me.uv_layers.new(name="UVMap")
        me.materials.append(bpy.data.materials.new(matName))
        obj = bpy.data.objects.new(name, me)
        meshCol.objects.link(obj)
        return obj

    priorityObj = addMeshObj("Group_0_Sub_0__FromName", "FromSlot", [(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    check("material name defaults to object name suffix",
          getREMeshMaterialName(priorityObj) == "FromName", getREMeshMaterialName(priorityObj))
    check("material name uses slot with useBlenderMaterialName",
          getREMeshMaterialName(priorityObj, useBlenderMaterialName = True) == "FromSlot")
    noMarkerObj = addMeshObj("PlainImported", "FromSlot", [(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    check("no marker falls back to the material slot", getREMeshMaterialName(noMarkerObj) == "FromSlot")
    # Remove the slot to force the fallback return value
    noMarkerObj.data.materials.clear()
    check("missing both sources returns the fallback", getREMeshMaterialName(noMarkerObj, fallback = "") == "")
    # The two probe objects only exercise parsing; keep them out of the export below
    bpy.data.objects.remove(priorityObj, do_unlink=True)
    bpy.data.objects.remove(noMarkerObj, do_unlink=True)

    # Mixed-case material names: "Zebra" sorts after "apple" case-insensitively,
    # but before it case-sensitively. Use swapped Sub indices so the exporter must
    # re-sort by material name, not trust the stale Sub_N suffix.
    addMeshObj("Group_0_Sub_0__Zebra", "Zebra", [(3, 0, 0), (4, 0, 0), (3, 1, 0)])
    addMeshObj("Group_0_Sub_1__apple", "apple", [(5, 0, 0), (6, 0, 0), (5, 1, 0)])

    def addMDFMaterial(objectName, propertyValue):
        matObj = bpy.data.objects.new("TempMDFObject", None)
        matObj["~TYPE"] = "RE_MDF_MATERIAL"
        matObj.re_mdf_material.materialName = propertyValue
        matObj.name = objectName
        mdfCol.objects.link(matObj)
        return matObj

    addMDFMaterial("Material 00 (Zebra)", "Zebra")
    addMDFMaterial("Material 01 (apple)", "apple")

    meshPath = os.path.join(tmpDir, "Order.mesh.221108797")
    mdfPath = os.path.join(tmpDir, "Order.mdf2.32")
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="Order.mesh",
                                        exportMDF=True, exportMDFExcludeUnused=True)
    check("mixed-case export finishes", result == {'FINISHED'})
    check("MDF written", os.path.isfile(mdfPath))
    if not os.path.isfile(mdfPath):
        return
    mdfOrder = [mat.materialName for mat in readMDF(mdfPath).materialList]
    parsedMesh = ParsedREMesh()
    parsedMesh.ParseREMesh(readREMesh(meshPath))
    meshOrder = list(parsedMesh.materialNameList)
    check("MDF material order is case-insensitive", mdfOrder == ["apple", "Zebra"], str(mdfOrder))
    check("single-group mesh material order matches the MDF global sort", meshOrder == mdfOrder,
          str(meshOrder) + " vs " + str(mdfOrder))


# ---------------------------------------------------------------- 2f3. Multi-group materials bind by name
def test_multi_group_materials_bind_by_name(tmpDir):
    """Contract regression (#4): with several viscon groups the mesh file's material
    list is group-major (all of group 0, then all of group 1) while the MDF file's
    list is globally sorted, so their ORDERS legitimately differ. The engine binds
    mesh submeshes to MDF materials by NAME - not by cross-file position - so this
    divergence is safe. What must hold is (a) every mesh material name resolves into
    the MDF list, and (b) each submesh's material index points into the mesh file's
    OWN material list. A future "fix" that force-aligns the two orders would be
    needless churn; breaking (a) or (b) would be a real bug."""
    from modules.mdf.file_re_mdf import readMDF
    from modules.mesh.file_re_mesh import readREMesh
    from modules.mesh.re_mesh_parse import ParsedREMesh

    clearSceneObjectsAndCollections()
    meshCol, mdfCol = makeMeshCollectionScene("MultiGroup")

    # Group 0 uses Body/Charlie, group 1 uses Alice/Delta: the materials interleave,
    # so a global name sort differs from the group-major visit order.
    addOneFaceMesh(meshCol, "Group_0_Sub_0__Body", "Body", 0)
    addOneFaceMesh(meshCol, "Group_0_Sub_1__Charlie", "Charlie", 4)
    addOneFaceMesh(meshCol, "Group_1_Sub_0__Alice", "Alice", 8)
    addOneFaceMesh(meshCol, "Group_1_Sub_1__Delta", "Delta", 12)
    for index, name in enumerate(("Body", "Charlie", "Alice", "Delta")):
        addMDFMaterialObject(mdfCol, f"Material {index:02d} ({name})", name)

    meshPath = os.path.join(tmpDir, "MultiGroup.mesh.221108797")
    mdfPath = os.path.join(tmpDir, "MultiGroup.mdf2.32")
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="MultiGroup.mesh",
                                        exportMDF=True, exportMDFExcludeUnused=True)
    check("multi-group export finishes", result == {'FINISHED'})
    check("MDF written", os.path.isfile(mdfPath))
    if not os.path.isfile(mdfPath):
        return

    mdfOrder = [mat.materialName for mat in readMDF(mdfPath).materialList]
    parsedMesh = ParsedREMesh()
    parsedMesh.ParseREMesh(readREMesh(meshPath))
    meshOrder = list(parsedMesh.materialNameList)

    # The two orders legitimately diverge across groups (group-major vs global sort)
    check("mesh material list is group-major",
          meshOrder == ["Body", "Charlie", "Alice", "Delta"], str(meshOrder))
    check("MDF material list is globally sorted",
          mdfOrder == ["Alice", "Body", "Charlie", "Delta"], str(mdfOrder))
    check("different orders are expected - binding is by name",
          set(meshOrder) == set(mdfOrder),
          str(meshOrder) + " vs " + str(mdfOrder))
    # (b) every submesh's material index points into the mesh file's OWN material list,
    # and together they cover exactly the materials actually used.
    referencedNames = {parsedMesh.materialNameList[s.materialIndex]
                       for lod in parsedMesh.mainMeshLODList
                       for viscon in lod.visconGroupList
                       for s in viscon.subMeshList}
    check("submesh indices reference the mesh's own material list",
          referencedNames == set(meshOrder), str(referencedNames))


def clearSceneObjectsAndCollections():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


def makeMeshCollectionScene(collectionName):
    """Create a <name> / <name>.mesh / <name>.mdf2 collection trio and return
    (meshCollection, mdfCollection)."""
    parent = bpy.data.collections.new(collectionName)
    bpy.context.scene.collection.children.link(parent)
    meshCol = bpy.data.collections.new(collectionName + ".mesh")
    parent.children.link(meshCol)
    meshCol["~TYPE"] = "RE_MESH_COLLECTION"
    mdfCol = bpy.data.collections.new(collectionName + ".mdf2")
    parent.children.link(mdfCol)
    mdfCol["~TYPE"] = "RE_MDF_COLLECTION"
    return meshCol, mdfCol


def addOneFaceMesh(collection, objectName, blenderMaterialName, x=0):
    me = bpy.data.meshes.new(objectName + "_me")
    me.from_pydata([(x, 0, 0), (x + 1, 0, 0), (x, 1, 0)], [], [(0, 1, 2)])
    me.update()
    me.uv_layers.new(name="UVMap")
    if blenderMaterialName != None:
        me.materials.append(bpy.data.materials.new(blenderMaterialName))
    obj = bpy.data.objects.new(objectName, me)
    collection.objects.link(obj)
    return obj


def addMDFMaterialObject(collection, objectName, propertyValue):
    """Assign the property first, then rename, so only the rename direction stays
    out of sync - the state an outliner rename leaves behind."""
    matObj = bpy.data.objects.new("TempMDFObject", None)
    matObj["~TYPE"] = "RE_MDF_MATERIAL"
    matObj.re_mdf_material.materialName = propertyValue
    matObj.name = objectName
    collection.objects.link(matObj)
    return matObj


# ---------------------------------------------------------------- 2e. MDF checks follow the export material source
def test_mdf_check_matches_export_material_source(tmpDir):
    """Regression: when the mesh export takes material names from the Blender material
    slot (useBlenderMaterialName), the MDF checks must resolve names the same way.
    They used to always read the "__" suffix, so an object named
    "Group_0_Sub_0__FromName" with a "FromSlot" slot produced false "does not exist in
    the MDF" and "does not exist in the mesh" warnings even though the MDF held
    FromSlot and the exported mesh used FromSlot."""
    import modules.mdf.blender_re_mdf as bmr
    from modules.blender_utils import getMeshMaterialNames
    from modules.mdf.file_re_mdf import readMDF

    originalShowMessageBox = bmr.showMessageBox
    originalEnableWarnings = bmr.ENABLE_EXPORT_WARNINGS
    bmr.showMessageBox = lambda *a, **k: None
    bmr.ENABLE_EXPORT_WARNINGS = True

    try:
        clearSceneObjectsAndCollections()
        meshCol, mdfCol = makeMeshCollectionScene("SlotName")
        # Object name suffix and material slot deliberately disagree
        addOneFaceMesh(meshCol, "Group_0_Sub_0__FromName", "FromSlot")
        addMDFMaterialObject(mdfCol, "Material 00 (FromSlot)", "FromSlot")

        mdfPath = os.path.join(tmpDir, "SlotName.mdf2.32")

        # The filter set the mesh export operator hands to the MDF side
        onlyMaterialNames = getMeshMaterialNames(meshCol, useBlenderMaterialName=True,
                                                 exportAllLODs=False)
        check("helper resolves the slot name for the export",
              onlyMaterialNames == {"FromSlot"}, str(onlyMaterialNames))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            success = bmr.exportMDFFile(mdfPath, mdfCol.name, onlyMaterialNames,
                                        useBlenderMaterialName=True, exportAllLODs=False)
        out = buf.getvalue()

        # The same export with the flag off resolves the "__" suffix instead, so this
        # pair of assertions proves the check is alive and the flag is what silences it.
        bufMismatch = io.StringIO()
        with contextlib.redirect_stdout(bufMismatch):
            bmr.exportMDFFile(mdfPath, mdfCol.name, onlyMaterialNames,
                              useBlenderMaterialName=False, exportAllLODs=False)
        outMismatch = bufMismatch.getvalue()
    finally:
        bmr.showMessageBox = originalShowMessageBox
        bmr.ENABLE_EXPORT_WARNINGS = originalEnableWarnings

    check("MDF export over the slot-named mesh succeeds", success is True)
    check("MDF check resolves the slot name, not the '__' suffix",
          "(FromName)" not in out, out[-500:])
    check("no false missing-material warning with useBlenderMaterialName",
          "does not exist in the MDF" not in out)
    check("no false unused-material warning with useBlenderMaterialName",
          "but do not exist in the mesh" not in out)
    check("the check still reports the mismatch when the flag is off",
          "(FromName) on mesh" in outMismatch, outMismatch[-500:])
    exportedNames = set(mat.materialName for mat in readMDF(mdfPath).materialList) \
        if os.path.isfile(mdfPath) else set()
    check("MDF keeps the slot-named material", exportedNames == {"FromSlot"}, str(exportedNames))


# ---------------------------------------------------------------- 2f. Exclude-unused respects the exported LODs
def test_exclude_unused_respects_exported_lods(tmpDir):
    """Regression: "Exclude Unused Materials" must only count the LODs that are
    actually exported. It used to walk the whole mesh collection recursively
    (all_objects), so a material that only exists on LOD 1 survived the filter when
    "Export All LODs" was off, leaving a material in the MDF that the mesh file has no
    entry for."""
    import modules.mesh.blender_re_mesh_export as mex
    from modules.mdf.file_re_mdf import readMDF
    mex.showMessageBox = lambda *a, **k: None

    clearSceneObjectsAndCollections()
    meshCol, mdfCol = makeMeshCollectionScene("LodChar")

    lod0 = bpy.data.collections.new("Main Mesh LOD0 - LodChar")
    meshCol.children.link(lod0)
    lod0["LOD Distance"] = 10.0
    addOneFaceMesh(lod0, "Group_0_Sub_0__Body", "Body", 0)

    lod1 = bpy.data.collections.new("Main Mesh LOD1 - LodChar")
    meshCol.children.link(lod1)
    lod1["LOD Distance"] = 20.0
    addOneFaceMesh(lod1, "Group_0_Sub_0__LOD1Only", "LOD1Only", 4)

    addMDFMaterialObject(mdfCol, "Material 00 (Body)", "Body")
    addMDFMaterialObject(mdfCol, "Material 01 (LOD1Only)", "LOD1Only")

    meshPath = os.path.join(tmpDir, "LodChar.mesh.221108797")
    mdfPath = os.path.join(tmpDir, "LodChar.mdf2.32")

    # Export All LODs off: only LOD 0 is written, so LOD1Only must be dropped
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="LodChar.mesh",
                                        exportMDF=True, exportMDFExcludeUnused=True,
                                        exportAllLODs=False)
    check("LOD-scoped export finishes", result == {'FINISHED'})
    exportedNames = set(mat.materialName for mat in readMDF(mdfPath).materialList) \
        if os.path.isfile(mdfPath) else set()
    check("material used only by an unexported LOD is excluded", exportedNames == {"Body"},
          str(exportedNames))

    # Export All LODs on: both LODs are written, so both materials must stay
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="LodChar.mesh",
                                        exportMDF=True, exportMDFExcludeUnused=True,
                                        exportAllLODs=True)
    check("all-LOD export finishes", result == {'FINISHED'})
    exportedNames = set(mat.materialName for mat in readMDF(mdfPath).materialList) \
        if os.path.isfile(mdfPath) else set()
    check("material used by an exported LOD is kept", exportedNames == {"Body", "LOD1Only"},
          str(exportedNames))


# ---------------------------------------------------------------- 2f2. Empty material filter = no filter
def test_empty_material_filter_exports_all(tmpDir):
    """Regression (#5): an empty material filter (the mesh uses no exportable materials - e.g.
    nothing selected with only-selected) must mean "no filter", not "filter to nothing". It used
    to be read two conflicting ways: MDFErrorCheck treated it as "keep nothing" (spurious
    per-material 'does not exist in the MDF' warnings) while filterMDFMaterials exported every
    material. Both now share the buildMDF normalisation (normalizeMaterialNameFilter)."""
    import modules.mdf.blender_re_mdf as bmr
    from modules.mdf.file_re_mdf import readMDF

    originalShowMessageBox = bmr.showMessageBox
    originalEnableWarnings = bmr.ENABLE_EXPORT_WARNINGS
    bmr.showMessageBox = lambda *a, **k: None
    bmr.ENABLE_EXPORT_WARNINGS = True

    try:
        clearSceneObjectsAndCollections()
        meshCol, mdfCol = makeMeshCollectionScene("EmptyFilter")
        addOneFaceMesh(meshCol, "Group_0_Sub_0__Body", "Body", 0)
        addMDFMaterialObject(mdfCol, "Material 00 (Body)", "Body")
        addMDFMaterialObject(mdfCol, "Material 01 (Extra)", "Extra")

        mdfPath = os.path.join(tmpDir, "EmptyFilter.mdf2.32")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            success = bmr.exportMDFFile(mdfPath, mdfCol.name, set(),
                                        useBlenderMaterialName=True, exportAllLODs=False)
        out = buf.getvalue()
        exportedNames = set(mat.materialName for mat in readMDF(mdfPath).materialList) \
            if os.path.isfile(mdfPath) else set()
    finally:
        bmr.showMessageBox = originalShowMessageBox
        bmr.ENABLE_EXPORT_WARNINGS = originalEnableWarnings

    check("empty-filter MDF export succeeds", success is True)
    check("empty filter exports every MDF material", exportedNames == {"Body", "Extra"},
          str(exportedNames))
    # The mesh uses Body: with the filter normalised to "no filter" no material is
    # filtered away, so nothing can be falsely reported as missing from the MDF.
    check("empty filter emits no false missing-material warning",
          "does not exist in the MDF" not in out, out[-500:])
    # Counterpart: the unused-material check is NOT silenced - Extra genuinely has no
    # mesh using it, and it is written to the file (the fallback export), so it must
    # still be reported.
    check("unused-material check still reports the genuinely unused one", "Extra" in out)


# ---------------------------------------------------------------- 2g. Mesh export reads the live object name
def test_mesh_export_warning_uses_object_name(tmpDir):
    """Regression: the mesh export's own MDF material comparison read the materialName
    property, which stays stale after an outliner rename (object "Material 12 (Siwa)"
    with the property still on "Ch4_Siwa"). It must resolve the name from the object the
    way the MDF writer does, otherwise a present material is reported as missing.
    The counterpart check is asserted too, so the warning cannot be "fixed" by silencing
    it."""
    import modules.mesh.blender_re_mesh as m
    import modules.mesh.blender_re_mesh_export as mex
    mex.showMessageBox = lambda *a, **k: None
    # The mesh exporter only prints its warning dict when this is on, so the
    # "not reported" assertions below only mean something with it enabled.
    originalEnableWarnings = mex.ENABLE_EXPORT_WARNINGS
    mex.ENABLE_EXPORT_WARNINGS = True

    try:
        clearSceneObjectsAndCollections()
        meshCol, mdfCol = makeMeshCollectionScene("WarnChar")
        addOneFaceMesh(meshCol, "Group_0_Sub_0__Siwa", "Siwa", 0)
        addOneFaceMesh(meshCol, "Group_0_Sub_1__Other", "Other", 4)
        # Renamed in the outliner: the parenthesised name is the truth, the property is stale
        addMDFMaterialObject(mdfCol, "Material 12 (Siwa)", "Ch4_Siwa")

        meshPath = os.path.join(tmpDir, "WarnChar.mesh.221108797")
        options = dict(EXPORT_OPTIONS)
        options["targetCollection"] = meshCol.name

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = m.exportREMeshFile(meshPath, dict(options))
        out = buf.getvalue()
    finally:
        mex.ENABLE_EXPORT_WARNINGS = originalEnableWarnings

    check("mesh export succeeds despite stale MDF property", result is True)
    check("stale property is not used for the MDF comparison", "[Ch4_Siwa]" not in out,
          out[-500:])
    check("renamed-but-present material not reported missing", "[Siwa]" not in out)
    check("renamed-but-present material not reported unused",
          "MDF Material Not Used By Mesh" not in out)
    # The counterpart: a material genuinely absent from the MDF is still reported,
    # so the warning cannot be "fixed" by silencing it.
    check("genuinely missing material still reported", "Mesh Material Missing From MDF" in out
          and "[Other]" in out, out[-500:])


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
    the matching MDF materials. The rename ends with a reindex of the affected MDF
    collection (stripSuffix = False), aligning the result with the "Reindex
    Materials" operator: names normalized and NN indices following the alphabetical
    order of the normalized names."""
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
    # The closing reindex numbers entries alphabetically by normalized name:
    # "othermat" sorts before "shirts".
    check("MDF material object name updated", matObj.name == "Material 01 (Shirts)", matObj.name)
    check("unrelated MDF material untouched",
          otherObj.re_mdf_material.materialName == "OtherMat")
    check("unrelated MDF material reindexed", otherObj.name == "Material 00 (OtherMat)", otherObj.name)

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
        test_export_mdf_object_name_wins(tmpDir)
    except Exception:
        check("mdf object-name-wins test crashed", False, traceback.format_exc())
    try:
        test_naming_helpers_and_mdf_order(tmpDir)
    except Exception:
        check("naming helpers/order test crashed", False, traceback.format_exc())
    try:
        test_multi_group_materials_bind_by_name(tmpDir)
    except Exception:
        check("multi-group bind-by-name test crashed", False, traceback.format_exc())
    try:
        test_mdf_check_matches_export_material_source(tmpDir)
    except Exception:
        check("MDF check material-source test crashed", False, traceback.format_exc())
    try:
        test_exclude_unused_respects_exported_lods(tmpDir)
    except Exception:
        check("exclude-unused LOD scope test crashed", False, traceback.format_exc())
    try:
        test_empty_material_filter_exports_all(tmpDir)
    except Exception:
        check("empty-filter test crashed", False, traceback.format_exc())
    try:
        test_mesh_export_warning_uses_object_name(tmpDir)
    except Exception:
        check("mesh export MDF warning test crashed", False, traceback.format_exc())
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
