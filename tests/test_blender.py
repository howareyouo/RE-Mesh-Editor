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

# ---------------------------------------------------------------- 2d-pre. Hide-in-game round trip
def test_hide_in_game_roundtrip(tmpDir):
    """Regression: "Hide Material In Game" is encoded in the file as a zeroed
    material name hash. Exporting a hidden material and re-importing it must
    restore the checkbox (previously the read side never recovered the flag,
    so re-exporting silently un-hid the material)."""
    from modules.mdf.file_re_mdf import readMDF
    from modules.mdf.blender_re_mdf import importMDFFile

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)

    parent = bpy.data.collections.new("CharHiddenRoot")
    bpy.context.scene.collection.children.link(parent)
    meshCol = bpy.data.collections.new("CharHidden.mesh")
    parent.children.link(meshCol)
    meshCol["~TYPE"] = "RE_MESH_COLLECTION"
    mdfCol = bpy.data.collections.new("CharHidden.mdf2")
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
    matObj.re_mdf_material.flags.hideMaterialInGame = True
    mdfCol.objects.link(matObj)

    meshPath = os.path.join(tmpDir, "CharHidden.mesh.221108797")
    mdfPath = os.path.join(tmpDir, "CharHidden.mdf2.32")
    result = bpy.ops.re_mesh.exportfile(filepath=meshPath, targetCollection="CharHidden.mesh", exportMDF=True)
    check("hide-in-game mesh+MDF export finishes", result == {'FINISHED'})

    exported = readMDF(mdfPath).materialList[0]
    check("hidden material exported with zeroed hash",
          exported.matNameHash == 0 and exported.hideInGame,
          f"hash={exported.matNameHash}")

    # Remove the scene's MDF collection so the re-import can claim its name,
    # like a fresh import into an empty scene
    bpy.data.objects.remove(matObj, do_unlink=True)
    bpy.data.collections.remove(mdfCol)

    importMDFFile(mdfPath)
    importedCol = bpy.data.collections.get("CharHidden.mdf2")
    importedMatObjs = [o for o in importedCol.all_objects
                       if o.get("~TYPE") == "RE_MDF_MATERIAL"] if importedCol else []
    check("re-imported MDF has one material", len(importedMatObjs) == 1,
          f"col={importedCol.name if importedCol else None} objs={len(importedMatObjs)}")
    if importedMatObjs:
        check("hide-in-game checkbox restored after import",
              importedMatObjs[0].re_mdf_material.flags.hideMaterialInGame is True)

    for path in (meshPath, mdfPath):
        if os.path.isfile(path):
            os.remove(path)

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
    from modules.naming import parseMDFObjectNameMaterialName
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
    from modules.naming import (stripBlenderUserSuffix, parseREMeshObjectMaterialName,
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


# ---------------------------------------------------------------- 2f. material name sync
def test_material_name_sync(tmpDir):
    """A material name lives in two places that have to match: the "__" suffix of every
    mesh object using it and the MDF material entry. The engine splits one material over
    several meshes, so renaming it in one place has to move every copy - in both
    directions - and must never let two materials end up with the same name."""
    from modules.mesh import blender_re_mesh_name_sync as nameSync

    clearSceneObjectsAndCollections()
    meshCol, mdfCol = makeMeshCollectionScene("Body")
    # Imported meshes live in a per-LOD child collection, which is itself named "*.mesh"
    lodCol = bpy.data.collections.new("Main Mesh LOD0 - Body.mesh")
    meshCol.children.link(lodCol)
    pieceA = addOneFaceMesh(lodCol, "Group_0_Sub_0__Shirts_Mat", "Shirts_Mat", x=0)
    pieceB = addOneFaceMesh(lodCol, "Group_0_Sub_1__Shirts_Mat", "Shirts_Mat", x=2)
    pieceC = addOneFaceMesh(lodCol, "Group_0_Sub_2__Other_Mat", "Other_Mat", x=4)
    shirtsMat = addMDFMaterialObject(mdfCol, "Material 00 (Shirts_Mat)", "Shirts_Mat")
    otherMat = addMDFMaterialObject(mdfCol, "Material 01 (Other_Mat)", "Other_Mat")

    # mesh -> MDF: renaming one piece in the outliner renames its siblings and the material
    nameSync.refreshNameSnapshot()
    pieceA.name = "Group_0_Sub_0__Cloth_Mat"
    syncedCount = nameSync.syncRenamedMaterialNames()
    check("mesh rename syncs to the MDF material", syncedCount == 1, str(syncedCount))
    check("mesh sharing the material is renamed too",
          pieceB.name == "Group_0_Sub_1__Cloth_Mat", pieceB.name)
    check("mesh sharing the material keeps its numbering",
          pieceB.name.startswith("Group_0_Sub_1__"), pieceB.name)
    check("mesh using another material is untouched",
          pieceC.name == "Group_0_Sub_2__Other_Mat", pieceC.name)
    check("MDF material follows the mesh rename",
          shirtsMat.re_mdf_material.materialName == "Cloth_Mat", shirtsMat.re_mdf_material.materialName)
    check("MDF material object name follows the mesh rename",
          shirtsMat.name == "Material 00 (Cloth_Mat)", shirtsMat.name)
    check("unrelated MDF material untouched", otherMat.re_mdf_material.materialName == "Other_Mat",
          otherMat.re_mdf_material.materialName)

    # A rename onto a name another material already holds is refused, and the mesh name
    # the outliner already accepted is put back.
    nameSync.refreshNameSnapshot()
    pieceB.name = "Group_0_Sub_1__Other_Mat"
    check("conflicting mesh rename is not synced", nameSync.syncRenamedMaterialNames() == 0)
    check("refused mesh rename is undone", pieceB.name == "Group_0_Sub_1__Cloth_Mat", pieceB.name)
    check("refused mesh rename leaves the MDF material alone",
          shirtsMat.re_mdf_material.materialName == "Cloth_Mat", shirtsMat.re_mdf_material.materialName)
    check("refused mesh rename leaves the sibling alone",
          pieceA.name == "Group_0_Sub_0__Cloth_Mat", pieceA.name)

    # MDF -> mesh: the MDF panel is the other entry point, and renaming there has to
    # reach every mesh piece, also when they sit directly in the mesh collection.
    clearSceneObjectsAndCollections()
    meshCol, mdfCol = makeMeshCollectionScene("Body")
    clothA = addOneFaceMesh(meshCol, "Group_0_Sub_0__Shirts_Mat", "Shirts_Mat", x=0)
    clothB = addOneFaceMesh(meshCol, "Group_1_Sub_0__Shirts_Mat", "Shirts_Mat", x=2)
    matObj = addMDFMaterialObject(mdfCol, "Material 00 (Shirts_Mat)", "Shirts_Mat")
    nameSync.refreshNameSnapshot()

    matObj.re_mdf_material.materialName = "Leather_Mat"

    check("MDF panel rename renames the mesh using the material",
          clothA.name == "Group_0_Sub_0__Leather_Mat", clothA.name)
    check("MDF panel rename renames every mesh using the material",
          clothB.name == "Group_1_Sub_0__Leather_Mat", clothB.name)
    check("MDF panel rename keeps the MDF object name in step",
          matObj.name == "Material 00 (Leather_Mat)", matObj.name)

    # Refused duplicate: the panel can't reject the property write, so the sync undoes it
    otherMat = addMDFMaterialObject(mdfCol, "Material 01 (Other_Mat)", "Other_Mat")
    matObj.re_mdf_material.materialName = "Other_Mat"
    check("duplicate material name is refused on the MDF side",
          matObj.re_mdf_material.materialName == "Leather_Mat", matObj.re_mdf_material.materialName)
    check("refused MDF rename restores the MDF object name",
          matObj.name == "Material 00 (Leather_Mat)", matObj.name)
    check("refused MDF rename leaves the other material alone",
          otherMat.re_mdf_material.materialName == "Other_Mat", otherMat.re_mdf_material.materialName)
    check("refused MDF rename leaves the meshes alone",
          clothA.name == "Group_0_Sub_0__Leather_Mat" and clothB.name == "Group_1_Sub_0__Leather_Mat",
          f"{clothA.name} / {clothB.name}")

    # The third entry point: renaming the "Material NN (name)" empty in the outliner. The
    # MDF writer resolves the material name from that object name, so the property and
    # every mesh piece have to follow it instead of waiting for the next export.
    nameSync.refreshNameSnapshot()
    matObj.name = "Material 00 (Denim_Mat)"
    check("MDF object rename is synced", nameSync.syncRenamedMaterialNames() == 1)
    check("MDF object rename follows through to the property",
          matObj.re_mdf_material.materialName == "Denim_Mat", matObj.re_mdf_material.materialName)
    check("MDF object rename reaches every mesh using the material",
          clothA.name == "Group_0_Sub_0__Denim_Mat" and clothB.name == "Group_1_Sub_0__Denim_Mat",
          f"{clothA.name} / {clothB.name}")

    # A name that isn't the "Material NN (name)" form carries no material name, so the
    # property keeps it and the conventional object name is put back
    nameSync.refreshNameSnapshot()
    matObj.name = "Denim"
    check("non conventional MDF object rename is not a material rename",
          nameSync.syncRenamedMaterialNames() == 0)
    check("non conventional MDF object name is put back",
          matObj.name == "Material 00 (Denim_Mat)", matObj.name)
    check("non conventional MDF object rename keeps the property",
          matObj.re_mdf_material.materialName == "Denim_Mat", matObj.re_mdf_material.materialName)

    # Refused duplicate on the object name side: the outliner rename is undone
    nameSync.refreshNameSnapshot()
    matObj.name = "Material 00 (Other_Mat)"
    check("conflicting MDF object rename is not synced", nameSync.syncRenamedMaterialNames() == 0)
    check("refused MDF object rename restores the object name",
          matObj.name == "Material 00 (Denim_Mat)", matObj.name)
    check("refused MDF object rename leaves the meshes alone",
          clothA.name == "Group_0_Sub_0__Denim_Mat" and clothB.name == "Group_1_Sub_0__Denim_Mat",
          f"{clothA.name} / {clothB.name}")

    # The plain case: a material used by a single mesh has no sibling to bring along
    clearSceneObjectsAndCollections()
    meshCol, mdfCol = makeMeshCollectionScene("Body")
    soloObj = addOneFaceMesh(meshCol, "Group_0_Sub_0__Solo_Mat", "Solo_Mat")
    soloMat = addMDFMaterialObject(mdfCol, "Material 00 (Solo_Mat)", "Solo_Mat")
    nameSync.refreshNameSnapshot()
    soloObj.name = "Group_0_Sub_0__Single_Mat"
    check("single-mesh rename is synced", nameSync.syncRenamedMaterialNames() == 1)
    check("single-mesh rename reaches the MDF material and its object name",
          soloMat.re_mdf_material.materialName == "Single_Mat" and soloMat.name == "Material 00 (Single_Mat)",
          f"{soloMat.re_mdf_material.materialName} / {soloMat.name}")


# ---------------------------------------------------------------- 2g. material name find/replace
def setReplaceText(findValue, replaceValue):
    """Fill in the "Find and Replace Material Names" fields of the RE MDF Tools panel.

    These are scene panel properties, the sub panel reads them and the operator runs
    off whatever the panel currently holds."""
    re_mdf_toolpanel = bpy.context.scene.re_mdf_toolpanel
    re_mdf_toolpanel.materialNameFindValue = findValue
    re_mdf_toolpanel.materialNameReplaceValue = replaceValue


def getReplacePreview():
    """Return (planList, error) for what the panel shows before the replace is run."""
    from modules.blender_utils import getMaterialNameReplacePlan
    re_mdf_toolpanel = bpy.context.scene.re_mdf_toolpanel
    return getMaterialNameReplacePlan(re_mdf_toolpanel.mdfCollection,
                                      re_mdf_toolpanel.materialNameFindValue,
                                      re_mdf_toolpanel.materialNameReplaceValue)


def runReplaceOperator(findValue, replaceValue):
    """Return the result and error text of the material name find/replace operator.

    A refused operator reports {"ERROR"}, which bpy.ops turns into a RuntimeError."""
    setReplaceText(findValue, replaceValue)
    try:
        return bpy.ops.re_mdf.replace_material_names(), ""
    except RuntimeError as err:
        return {'CANCELLED'}, str(err)


def test_replace_material_names(tmpDir):
    """The RE MDF Tools "Find and Replace Material Names" operator replaces text in the
    name of every material of the active MDF collection and renames the meshes bound to
    each of them, so both sides keep matching."""
    # The fields live in a collapsible sub panel of RE MDF Tools, not in a popup dialog
    panel = getattr(bpy.types, "OBJECT_PT_mdf_material_name_replace_panel", None)
    check("material name replace panel is registered", panel is not None)
    check("material name replace panel is collapsed by default",
          panel is not None and "DEFAULT_CLOSED" in panel.bl_options,
          str(getattr(panel, "bl_options", None)))
    check("material name replace panel sits under the RE MDF Tools panel",
          panel is not None and panel.bl_parent_id == "OBJECT_PT_mdf_tools_panel",
          str(getattr(panel, "bl_parent_id", None)))
    check("replace fields are tool panel properties",
          {"materialNameFindValue", "materialNameReplaceValue"}.issubset(
              bpy.context.scene.re_mdf_toolpanel.bl_rna.properties.keys()))
    check("replace operator keeps no input of its own",
          "findValue" not in bpy.ops.re_mdf.replace_material_names.get_rna_type().properties.keys())

    clearSceneObjectsAndCollections()
    meshCol, mdfCol = makeMeshCollectionScene("Body")
    shirtsA = addOneFaceMesh(meshCol, "Group_0_Sub_0__Shirts_Mat", "Shirts_Mat", x=0)
    shirtsB = addOneFaceMesh(meshCol, "Group_1_Sub_0__Shirts_Mat", "Shirts_Mat", x=2)
    wornObj = addOneFaceMesh(meshCol, "Group_0_Sub_0__Shirts_Mat_Worn", "Shirts_Mat_Worn", x=4)
    pantsObj = addOneFaceMesh(meshCol, "Group_0_Sub_0__Pants_Mat", "Pants_Mat", x=6)
    shirtsMat = addMDFMaterialObject(mdfCol, "Material 00 (Shirts_Mat)", "Shirts_Mat")
    wornMat = addMDFMaterialObject(mdfCol, "Material 01 (Shirts_Mat_Worn)", "Shirts_Mat_Worn")
    pantsMat = addMDFMaterialObject(mdfCol, "Material 02 (Pants_Mat)", "Pants_Mat")

    bpy.context.scene.re_mdf_toolpanel.mdfCollection = mdfCol
    setReplaceText("Shirts", "Cloth")
    planList, error = getReplacePreview()
    check("panel preview lists the materials to rename",
          error is None and [newName for materialObj, oldName, newName in planList] ==
          ["Cloth_Mat", "Cloth_Mat_Worn"], f"{error} {planList}")
    result = bpy.ops.re_mdf.replace_material_names()

    check("material name find/replace operator finishes", result == {'FINISHED'}, str(result))
    check("material name replaced in the MDF",
          shirtsMat.re_mdf_material.materialName == "Cloth_Mat", shirtsMat.re_mdf_material.materialName)
    check("second matching material replaced too",
          wornMat.re_mdf_material.materialName == "Cloth_Mat_Worn", wornMat.re_mdf_material.materialName)
    check("non-matching material untouched",
          pantsMat.re_mdf_material.materialName == "Pants_Mat", pantsMat.re_mdf_material.materialName)
    check("MDF material object names follow", shirtsMat.name == "Material 00 (Cloth_Mat)" and
          wornMat.name == "Material 01 (Cloth_Mat_Worn)", f"{shirtsMat.name} / {wornMat.name}")
    check("meshes using a replaced material renamed", shirtsA.name == "Group_0_Sub_0__Cloth_Mat", shirtsA.name)
    check("every mesh piece of the material renamed", shirtsB.name == "Group_1_Sub_0__Cloth_Mat", shirtsB.name)
    check("mesh with the longer name renamed", wornObj.name == "Group_0_Sub_0__Cloth_Mat_Worn", wornObj.name)
    check("mesh of a non-matching material untouched", pantsObj.name == "Group_0_Sub_0__Pants_Mat", pantsObj.name)

    # A replacement may contain the text it replaces: the meshes of a later material
    # must not be resolved through names an earlier rename already produced. "Aa" and
    # "Aaa" collide after the replacement ("Aaa" and "Aaaaa"), which only the whole-pass
    # resolution in replaceMaterialNameText keeps apart.
    clearSceneObjectsAndCollections()
    meshCol, mdfCol = makeMeshCollectionScene("Body")
    shortObj = addOneFaceMesh(meshCol, "Group_0_Sub_0__Aa", "Aa", x=0)
    longObj = addOneFaceMesh(meshCol, "Group_0_Sub_1__Aaa", "Aaa", x=2)
    shortMat = addMDFMaterialObject(mdfCol, "Material 00 (Aa)", "Aa")
    longMat = addMDFMaterialObject(mdfCol, "Material 01 (Aaa)", "Aaa")
    bpy.context.scene.re_mdf_toolpanel.mdfCollection = mdfCol
    setReplaceText("a", "aa")
    result = bpy.ops.re_mdf.replace_material_names()
    check("overlapping replacement is applied", result == {'FINISHED'}, str(result))
    check("overlapping replacement covers both materials",
          shortMat.re_mdf_material.materialName == "Aaa" and longMat.re_mdf_material.materialName == "Aaaaa",
          f"{shortMat.re_mdf_material.materialName} / {longMat.re_mdf_material.materialName}")
    check("overlapping replacement renames each mesh once",
          shortObj.name == "Group_0_Sub_0__Aaa" and longObj.name == "Group_0_Sub_1__Aaaaa",
          f"{shortObj.name} / {longObj.name}")

    # The whole pass is refused when it would give two materials the same name
    clearSceneObjectsAndCollections()
    meshCol, mdfCol = makeMeshCollectionScene("Body")
    targetObj = addOneFaceMesh(meshCol, "Group_0_Sub_0__BodyA", "BodyA", x=0)
    matchedObj = addOneFaceMesh(meshCol, "Group_0_Sub_1__XBodyA", "XBodyA", x=2)
    matchedMat = addMDFMaterialObject(mdfCol, "Material 00 (XBodyA)", "XBodyA")
    addMDFMaterialObject(mdfCol, "Material 01 (BodyA)", "BodyA")
    bpy.context.scene.re_mdf_toolpanel.mdfCollection = mdfCol
    result, error = runReplaceOperator("X", "")
    check("duplicate material name aborts the replacement", result == {'CANCELLED'}, str(result))
    check("duplicate refusal spells the material name as it is",
          '"BodyA"' in error, error)
    check("aborted replacement leaves the MDF alone",
          matchedMat.re_mdf_material.materialName == "XBodyA", matchedMat.re_mdf_material.materialName)
    check("aborted replacement leaves the meshes alone",
          targetObj.name == "Group_0_Sub_0__BodyA" and matchedObj.name == "Group_0_Sub_1__XBodyA",
          f"{targetObj.name} / {matchedObj.name}")
    result, error = runReplaceOperator("", "X")
    check("empty find text is refused", result == {'CANCELLED'} and "empty" in error, f"{result} {error}")


# ---------------------------------------------------------------- 2h. texture binding file rename
def makeTextureFile(folder, fileName):
    os.makedirs(folder, exist_ok=True)
    filePath = os.path.join(folder, fileName)
    with open(filePath, "w", encoding="utf-8"):
        pass
    return filePath


def addTextureBinding(materialObj, textureType, path):
    binding = materialObj.re_mdf_material.textureBindingList_items.add()
    binding.textureType = textureType
    binding.path = path
    return binding


def getToolPanelPreferences():
    return bpy.context.preferences.addons["RE-Mesh-Editor"].preferences


def test_texture_binding_file_rename(tmpDir):
    """Editing a texture binding path renames the .tex files on disk, so the binding
    keeps pointing at a file that exists. The files carry a version suffix and can sit
    in a streaming folder, and an atlas is bound by several materials, so every copy and
    every binding that used the path has to follow the edit."""
    clearSceneObjectsAndCollections()
    modRoot = os.path.join(tmpDir, "texmod")
    heroDir = os.path.join(modRoot, "natives", "STM", "character", "hero")
    streamDir = os.path.join(modRoot, "natives", "STM", "streaming", "character", "hero")
    makeTextureFile(heroDir, "hero_ALBM.tex.7602304")
    makeTextureFile(streamDir, "hero_ALBM.tex.7602304")
    makeTextureFile(heroDir, "taken.tex.7602304")
    makeTextureFile(heroDir, "render_a.rtex")
    meshCol, mdfCol = makeMeshCollectionScene("hero")
    mdfCol["~MDFFILEPATH"] = os.path.join(heroDir, "hero.mdf2")
    matObj = addMDFMaterialObject(mdfCol, "Material 00 (Body)", "Body")
    mainBinding = addTextureBinding(matObj, "ALBD", "character/hero/hero_ALBM.tex")
    sharedBinding = addTextureBinding(matObj, "ATXX", "character/hero/hero_ALBM.tex")
    bpy.context.scene.re_mdf_toolpanel.mdfCollection = mdfCol

    mainBinding.path = "character/hero/hero_ALBD.tex"
    check("renamed texture file keeps its version suffix",
          os.path.isfile(os.path.join(heroDir, "hero_ALBD.tex.7602304")),
          str(sorted(os.listdir(heroDir))))
    check("streaming copy of the texture renamed too",
          os.path.isfile(os.path.join(streamDir, "hero_ALBD.tex.7602304")),
          str(sorted(os.listdir(streamDir))))
    check("old texture file name is gone",
          not os.path.exists(os.path.join(heroDir, "hero_ALBM.tex.7602304")))
    check("binding that shared the file follows the rename",
          sharedBinding.path == "character/hero/hero_ALBD.tex", sharedBinding.path)
    check("edited binding keeps the new path", mainBinding.path == "character/hero/hero_ALBD.tex", mainBinding.path)

    # A name that is already on disk is not overwritten: the edit points the binding at
    # a texture that exists, so there is nothing to file under a new name.
    sharedBinding.path = "character/hero/taken.tex"
    check("taken name leaves the files alone",
          os.path.isfile(os.path.join(heroDir, "hero_ALBD.tex.7602304")) and
          os.path.isfile(os.path.join(heroDir, "taken.tex.7602304")), str(sorted(os.listdir(heroDir))))
    check("taken name keeps the path the user typed", sharedBinding.path == "character/hero/taken.tex", sharedBinding.path)

    # Moving a binding to another folder is a repoint, not a rename
    otherBinding = addTextureBinding(matObj, "NRMR", "character/hero/taken.tex")
    otherBinding.path = "character/other/taken.tex"
    check("folder change does not move files",
          os.path.isfile(os.path.join(heroDir, "taken.tex.7602304")), str(sorted(os.listdir(heroDir))))
    check("folder change keeps the path", otherBinding.path == "character/other/taken.tex", otherBinding.path)

    # Render targets have no .tex file to rename
    renderBinding = addTextureBinding(matObj, "RT", "character/hero/render_a.rtex")
    renderBinding.path = "character/hero/render_b.rtex"
    check("render target path is not renamed",
          os.path.isfile(os.path.join(heroDir, "render_a.rtex")) and
          not os.path.exists(os.path.join(heroDir, "render_b.rtex")), str(sorted(os.listdir(heroDir))))

    # A new binding has no previous path, and a missing file has nothing to rename
    newBinding = addTextureBinding(matObj, "ALBM", "")
    newBinding.path = "character/hero/brand_new.tex"
    check("new binding does not rename anything",
          not os.path.exists(os.path.join(heroDir, "brand_new.tex.7602304")))
    missingBinding = addTextureBinding(matObj, "MISS", "character/hero/missing_a.tex")
    missingBinding.path = "character/hero/missing_b.tex"
    check("missing texture file is not renamed",
          not os.path.exists(os.path.join(heroDir, "missing_b.tex.7602304")))

    # The option only controls the disk side: the path edit itself always lands
    preferences = getToolPanelPreferences()
    preferences.autoRenameTextureFiles = False
    try:
        mainBinding.path = "character/hero/hero_ALBJ.tex"
    finally:
        preferences.autoRenameTextureFiles = True
    check("disabled option leaves the files alone",
          os.path.isfile(os.path.join(heroDir, "hero_ALBD.tex.7602304")), str(sorted(os.listdir(heroDir))))
    check("disabled option keeps the edited path", mainBinding.path == "character/hero/hero_ALBJ.tex", mainBinding.path)

    # The find/replace operator writes many paths at once and renames each file once
    clearSceneObjectsAndCollections()
    makeTextureFile(heroDir, "body_ALBM.tex.7602304")
    meshCol, mdfCol = makeMeshCollectionScene("hero")
    mdfCol["~MDFFILEPATH"] = os.path.join(heroDir, "hero.mdf2")
    replaceMat = addMDFMaterialObject(mdfCol, "Material 00 (Body)", "Body")
    firstBinding = addTextureBinding(replaceMat, "ALBD", "character/hero/body_ALBM.tex")
    secondBinding = addTextureBinding(replaceMat, "ATXX", "character/hero/body_ALBM.tex")
    bpy.context.scene.re_mdf_toolpanel.mdfCollection = mdfCol
    bpy.context.view_layer.objects.active = replaceMat
    findReplace = bpy.context.window_manager.re_mdf_findreplace
    while len(findReplace.findReplaceList) > 0:
        findReplace.findReplaceList.remove(0)
    findReplace.replaceAllMaterials = False
    item = findReplace.findReplaceList.add()
    item.findValue = "ALBM"
    item.replaceValue = "ALBD"
    result = bpy.ops.re_mdf.replace_texture_bindings()
    check("texture find/replace operator finishes", result == {'FINISHED'}, str(result))
    check("texture find/replace renames the file",
          os.path.isfile(os.path.join(heroDir, "body_ALBD.tex.7602304")) and
          not os.path.exists(os.path.join(heroDir, "body_ALBM.tex.7602304")), str(sorted(os.listdir(heroDir))))
    check("texture find/replace updates every binding",
          firstBinding.path == "character/hero/body_ALBD.tex" and
          secondBinding.path == "character/hero/body_ALBD.tex",
          f"{firstBinding.path} / {secondBinding.path}")

    # Undoing the pass restores the binding paths but nothing moves the files back, so the
    # recorded rename has to be walked back with the paths the scene holds afterwards.
    # Blender's undo can't run in background mode, so the paths undo would restore are
    # written with the file sync held off, the way undo writes them, and the handler body
    # is run directly.
    # The addon runs its own copy of the modules, so the guard and the handler have to come
    # from that copy: a module imported through the top level "modules" package has its own
    # sync depth and its own records, which no binding callback would ever touch.
    textureFiles = sys.modules["RE-Mesh-Editor.modules.mdf.blender_re_mdf_texture_files"]
    check("undo/redo handler is registered",
          textureFiles._restoreTextureFileNamesAfterUndo in bpy.app.handlers.undo_post and
          textureFiles._restoreTextureFileNamesAfterUndo in bpy.app.handlers.redo_post)
    with textureFiles.texturePathSync():
        firstBinding.path = "character/hero/body_ALBM.tex"
        secondBinding.path = "character/hero/body_ALBM.tex"
    undoMovedCount = textureFiles.restoreTextureFileNamesForUndo()
    check("undo renames the texture file back",
          os.path.isfile(os.path.join(heroDir, "body_ALBM.tex.7602304")) and
          not os.path.exists(os.path.join(heroDir, "body_ALBD.tex.7602304")), str(sorted(os.listdir(heroDir))))
    check("undo reports the moved file", undoMovedCount == 1, str(undoMovedCount))

    # Redo puts the paths back, so the file has to follow them forward again
    with textureFiles.texturePathSync():
        firstBinding.path = "character/hero/body_ALBD.tex"
        secondBinding.path = "character/hero/body_ALBD.tex"
    redoMovedCount = textureFiles.restoreTextureFileNamesForUndo()
    check("redo renames the texture file forward again",
          os.path.isfile(os.path.join(heroDir, "body_ALBD.tex.7602304")) and
          not os.path.exists(os.path.join(heroDir, "body_ALBM.tex.7602304")), str(sorted(os.listdir(heroDir))))
    check("redo reports the moved file", redoMovedCount == 1, str(redoMovedCount))

    # A path that is still used on both sides (one binding kept the old one) must not
    # move the file: there is no single name the binding paths agree on
    with textureFiles.texturePathSync():
        firstBinding.path = "character/hero/body_ALBM.tex"
    check("path in use on both sides leaves the file alone",
          textureFiles.restoreTextureFileNamesForUndo() == 0 and
          os.path.isfile(os.path.join(heroDir, "body_ALBD.tex.7602304")),
          str(sorted(os.listdir(heroDir))))
    # A path that survives nowhere has no binding left to keep in step with
    with textureFiles.texturePathSync():
        firstBinding.path = "character/hero/gone.tex"
        secondBinding.path = "character/hero/gone.tex"
    check("record of a path nothing uses is dropped",
          textureFiles.restoreTextureFileNamesForUndo() == 0)
    check("dropped record leaves the file where it is",
          os.path.isfile(os.path.join(heroDir, "body_ALBD.tex.7602304")), str(sorted(os.listdir(heroDir))))

    # A batch that fails on every file warns once instead of once per binding
    while len(findReplace.findReplaceList) > 0:
        findReplace.findReplaceList.remove(0)
    makeTextureFile(heroDir, "body_ALBM.tex.7602304")
    makeTextureFile(heroDir, "body_ALBI.tex.7602304")
    # The names the pass would produce are taken, so both renames are refused
    makeTextureFile(heroDir, "body_ALBDM.tex.7602304")
    makeTextureFile(heroDir, "body_ALBDI.tex.7602304")
    failBindingA = addTextureBinding(replaceMat, "ALBD", "character/hero/body_ALBM.tex")
    failBindingB = addTextureBinding(replaceMat, "ATXX", "character/hero/body_ALBI.tex")
    item = findReplace.findReplaceList.add()
    item.findValue = "ALB"
    item.replaceValue = "ALBD"
    capturedOutput = io.StringIO()
    with contextlib.redirect_stdout(capturedOutput):
        result = bpy.ops.re_mdf.replace_texture_bindings()
    batchOutput = capturedOutput.getvalue()
    check("batch rename failure still finishes", result == {'FINISHED'}, str(result))
    check("batch rename failure warns once",
          batchOutput.count("Texture files were not renamed") == 1, batchOutput.strip())
    check("batch rename failure counts the bindings", "for 2 bindings" in batchOutput, batchOutput.strip())
    check("batch rename failure leaves every file alone",
          os.path.isfile(os.path.join(heroDir, "body_ALBM.tex.7602304")) and
          os.path.isfile(os.path.join(heroDir, "body_ALBI.tex.7602304")), str(sorted(os.listdir(heroDir))))
    check("batch rename failure keeps the paths the pass wrote",
          failBindingA.path == "character/hero/body_ALBDM.tex" and
          failBindingB.path == "character/hero/body_ALBDI.tex",
          f"{failBindingA.path} / {failBindingB.path}")
    while len(findReplace.findReplaceList) > 0:
        findReplace.findReplaceList.remove(0)

    # The auto rename option gates the bulk operator like a single MDF panel edit:
    # with it off the bindings move but the files on disk keep their names
    clearSceneObjectsAndCollections()
    makeTextureFile(heroDir, "body_ALBM.tex.7602304")
    meshCol, mdfCol = makeMeshCollectionScene("hero")
    mdfCol["~MDFFILEPATH"] = os.path.join(heroDir, "hero.mdf2")
    noRenameMat = addMDFMaterialObject(mdfCol, "Material 00 (Body)", "Body")
    noRenameBinding = addTextureBinding(noRenameMat, "ALBD", "character/hero/body_ALBM.tex")
    bpy.context.scene.re_mdf_toolpanel.mdfCollection = mdfCol
    bpy.context.view_layer.objects.active = noRenameMat
    item = findReplace.findReplaceList.add()
    item.findValue = "ALBM"
    item.replaceValue = "ALBD"
    filesBefore = sorted(os.listdir(heroDir))
    preferences.autoRenameTextureFiles = False
    try:
        result = bpy.ops.re_mdf.replace_texture_bindings()
    finally:
        preferences.autoRenameTextureFiles = True
    check("disabled option: find/replace operator finishes", result == {'FINISHED'}, str(result))
    check("disabled option: find/replace leaves every file alone",
          sorted(os.listdir(heroDir)) == filesBefore, str(sorted(os.listdir(heroDir))))
    check("disabled option: find/replace still rewrites the binding",
          noRenameBinding.path == "character/hero/body_ALBD.tex", noRenameBinding.path)
    while len(findReplace.findReplaceList) > 0:
        findReplace.findReplaceList.remove(0)


def test_load_material_textures_iterates_all_entries():
    """loadMaterialTextures must classify every used texture binding in a material,
    not just the last one. Regression for a refactor that hoisted the
    loadUnusedTextures/isUsedTexture gate out of the per-binding loop (and left
    TEXTURE_CACHE_DIR/USE_DDS unresolved in the split-out function)."""
    from types import SimpleNamespace
    from modules.mdf.blender_re_mesh_mdf import loadMaterialTextures

    textureList = [
        SimpleNamespace(textureType="MaskMap", texturePath="tex/a_msk1.tex"),
        SimpleNamespace(textureType="MaskMap", texturePath="tex/b_msk1.tex"),
        SimpleNamespace(textureType="UnusedFillerMap", texturePath="tex/c_unused.tex"),
        SimpleNamespace(textureType="MaskMap", texturePath="tex/d_msk1.tex"),
    ]
    mdfMaterial = SimpleNamespace(textureList=textureList)
    textureNodeInfoList = []
    with tempfile.TemporaryDirectory(prefix="reme_mdftex_") as cacheDir:
        hasVertexColor = loadMaterialTextures(
            mdfMaterial, 32, "RE4", "Regress_Mat", [], None, cacheDir, False,
            False, False, {}, set(), {}, textureNodeInfoList)
    check("every used texture binding classified",
          len(textureNodeInfoList) == 3,
          f"expected 3 node entries, got {len(textureNodeInfoList)}")
    check("unused binding still gated out",
          all(info[1] != "UnusedFillerMap" for info in textureNodeInfoList))
    check("plain mask maps do not set the vertex color flag", hasVertexColor is False)


def main():
    tmpDir = tempfile.mkdtemp(prefix="remeshtest_")
    test_addon_props()
    try:
        test_hide_in_game_roundtrip(tmpDir)
    except Exception:
        check("hide-in-game roundtrip test crashed", False, traceback.format_exc())
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
        test_material_name_sync(tmpDir)
    except Exception:
        check("material name sync test crashed", False, traceback.format_exc())
    try:
        test_replace_material_names(tmpDir)
    except Exception:
        check("material name find/replace test crashed", False, traceback.format_exc())
    try:
        test_texture_binding_file_rename(tmpDir)
    except Exception:
        check("texture binding file rename test crashed", False, traceback.format_exc())
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
    try:
        test_load_material_textures_iterates_all_entries()
    except Exception:
        check("material texture iteration test crashed", False, traceback.format_exc())

    print("\n================================")
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): {FAILURES}")
        raise SystemExit(1)
    print("ALL BLENDER TESTS PASSED")


main()
