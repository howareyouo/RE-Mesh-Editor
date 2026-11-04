from ..gen_functions import textColors
import textwrap

try:
    import bpy
except ImportError:  # Without Blender only the issue registry and the reporting rules are available.
    bpy = None

ERROR_WINDOW_SIZE = 750
SPLIT_FACTOR = .35

ERROR = "error"
WARNING = "warning"


if bpy is not None:
    class REMeshErrorEntry(bpy.types.PropertyGroup):
        errorType: bpy.props.StringProperty(
            name="",
        )

        errorName: bpy.props.StringProperty(
            name="",
        )
        errorDescription: bpy.props.StringProperty(
            name="",
        )
        objectSetString: bpy.props.StringProperty(
            name="",
        )
        errorCount: bpy.props.IntProperty(
            name="",
        )


    class MESH_UL_REMeshErrorList(bpy.types.UIList):

        def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
            layout.label(text=f"{item.errorType} ({str(item.errorCount)})")

        # Disable double-click to rename
        def invoke(self, context, event):
            return {'PASS_THROUGH'}


    class WM_OT_ShowREMeshErrorWindow(bpy.types.Operator):
        'Show Export Errors'
        bl_idname = 're_mesh.show_export_error_window'
        bl_label = 'RE Mesh Export Error'
        bl_options = {'REGISTER'}
        targetCollection: bpy.props.StringProperty()
        armatureName: bpy.props.StringProperty()
        errorList_items: bpy.props.CollectionProperty(type=REMeshErrorEntry)
        errorList_index: bpy.props.IntProperty(name="")

        def execute(self, context):
            print("Displayed error window2.")
            return {'FINISHED'}

        def invoke(self, context, event):
            region = bpy.context.region
            centerX = region.width // 2
            centerY = region.height

            # currentX = event.mouse_region_X
            # currentY = event.mouse_region_Y

            # Move cursor to center so error window is at the center of the window
            context.window.cursor_warp(centerX, centerY)
            for entry in bpy.context.scene.re_mesh_error_list:
                item = self.errorList_items.add()
                for key, value in entry.items():
                    item[key] = value
            return context.window_manager.invoke_props_dialog(self, width=ERROR_WINDOW_SIZE)

        def draw(self, context):
            layout = self.layout
            rowCount = 2
            uifontscale = 9 * context.preferences.view.ui_scale
            max_label_width = int((ERROR_WINDOW_SIZE * (1 - SPLIT_FACTOR) * (2 - SPLIT_FACTOR)) // uifontscale)
            layout.label(
                text=f"The mesh has {len(self.errorList_items)} {'issues' if len(self.errorList_items) > 1 else 'issue'} that must be fixed before it can be exported.",
                icon="ERROR")
            row = layout.row()
            row.label(text=f"Target Collection: {self.targetCollection}")
            row.label(text=f"Target Armature: {self.armatureName}")

            row = layout.row().separator()
            split = layout.split(
                factor=SPLIT_FACTOR)  # Indent list slightly to make it more clear it's a part of a sub panel
            col1 = split.column()
            col2 = split.column()

            """
            layout.prop(self,"currentError")
            row.label(text =f"Error {str(self.currentError)} / {str(len(bpy.context.scene.re_mesh_error_list))}")
            if self.currentError <= len(bpy.context.scene.re_mesh_error_list):
            """

            if len(self.errorList_items) != 0:
                item = self.errorList_items[self.errorList_index]
                col2.label(text=f"Error Info: {item.errorType}")
                box = col2.box()
                row = box.row()
                for line in item.errorDescription.splitlines():
                    line = line.strip()
                    for chunk in textwrap.wrap(line, width=max_label_width):
                        box.label(text=chunk)
                        rowCount += 1
                row = layout.row()
                if item.objectSetString != "":
                    for line in item.objectSetString.splitlines():
                        line = line.strip()
                        for chunk in textwrap.wrap(line, width=max_label_width):
                            box.label(text=chunk)
                            rowCount += 1
            col1.label(text=f"Error Count: {str(len(self.errorList_items))}")
            col1.template_list(
                listtype_name="MESH_UL_REMeshErrorList",
                list_id="",
                dataptr=self,
                propname="errorList_items",
                active_dataptr=self,
                active_propname="errorList_index",
                rows=rowCount,
                type='DEFAULT'
            )


def addErrorToDict(errorDict, errorType, objectName):
    if errorType not in ISSUES:
        raise KeyError(f"Unregistered export issue: {errorType}")
    entry = errorDict.get(errorType)
    if entry is None:
        errorDict[errorType] = entry = {"count": 0, "objectSet": set()}
    entry["count"] += 1
    if objectName is not None:
        entry["objectSet"].add(objectName)


ISSUES = {
    "NoMeshesInCollection": (ERROR, """No Meshes In Collection
No meshes were found in the target collection.
	
HOW TO FIX:
_______________
	
Specify a target collection in the export options that contains meshes, or leave it blank and move your meshes and armature into the Scene Collection.
"""),

    "MoreThanOneArmature": (ERROR, """More Than One Armature
More than one armature was found in the target collection. Only one armature can be present in a mesh collection. 
	
HOW TO FIX:
_______________
	
Move the extra armature into another collection or delete it. 
"""),
    "NoMaterialOnSubMesh": (ERROR, """No Material On Sub Mesh
A mesh has no material assigned to it. All meshes must have one material assigned to them.
	
HOW TO FIX:
_______________
	
Specify an MDF material name on the end of the object name separated by two underscores. See example, here the material name is "pl1000_Body_Mat".

Example Object Name: LOD_0_Group_0_Sub_0__pl1000_Body_Mat
"""),

    "MoreThanOneMaterialOnSubMesh": (ERROR, """More Than One Material On Sub Mesh
A mesh has more than one material assigned to it. All meshes must have only one material assigned to them.
	
HOW TO FIX:
_______________
	
Select the listed mesh in edit mode, press A to select all vertices. Then press P > Material to split the mesh by it's materials.
"""),

    "NoUVMapOnSubMesh": (ERROR, """No UV Map On Sub Mesh
A mesh has no UV map. All meshes require at least one uv map.
	
HOW TO FIX:
_______________
	
Create a UV map.
"""),

    "NoVerticesOnSubMesh": (ERROR, """No Vertices On Sub Mesh
A mesh has no vertices. All meshes must have at least 3 vertices and 1 face.
	
HOW TO FIX:
_______________
	
Delete the listed mesh.
"""),

    "NoFacesOnSubMesh": (ERROR, """No Faces On Sub Mesh
A mesh has no faces. All meshes must have at least 3 vertices and 1 face.
	
HOW TO FIX:
_______________
	
Delete the listed mesh.
"""),

    "LooseVerticesOnSubMesh": (ERROR, """Loose Vertices On Sub Mesh
A mesh has loose vertices with no faces assigned.
	
HOW TO FIX:
_______________
	
Select the listed mesh in edit mode, press A to select all vertices. In the menu bar at the top, select > Mesh > Clean Up > Delete Loose.

OR

Press the "Delete Loose Geometry" button in the RE Mesh tab to delete loose vertices on all meshes.
"""),

    "NonTriangulatedFace": (ERROR, """Non Triangulated Faces
A mesh has non triangulated faces. All faces must be triangulated.
	
HOW TO FIX:
_______________
	
Select the listed mesh in edit mode, press A to select all vertices. Press Ctrl + T to triangulate faces.


"""),
    "MultipleUVsAssignedToVertex": (ERROR, """Multiple UVs Assigned To Vertex
A mesh has multiple uvs assigned to a single vertex.
	
HOW TO FIX:
_______________
	
Select the listed mesh in edit mode, switch to edge select mode.
Press A to select all edges,then press F3 to search for and select "Seams From Islands".
Select an edge that is marked red on the mesh, then press Shift > G > Seam. 
Press V to rip the vertices and then press Esc so that the vertices stay in the same location.

OR

Check the "Auto Solve Repeated UVs" box when exporting the mesh.
"""),
    "MaxVerticesExceeded": (ERROR, """Max Vertices Exceeded On Sub Mesh
A mesh exceeded the limit of 4294967295 vertices.
	
HOW TO FIX:
_______________
	
Separate parts of the mesh into more sub meshes.

OR

Use the decimate modifier to reduce mesh quality.
"""),
    "MaxFacesExceeded": (ERROR, """Max Faces Exceeded On Sub Mesh
A mesh exceeded the limit of 4294967295 faces.
	
HOW TO FIX:
_______________
	
Reconsider the life choices that led you to decide to try to export a mesh with 4 million faces.
"""),

    "MaxWeightsPerVertexExceeded": (ERROR, """Max Weights Per Vertex Exceeded On Sub Mesh
A vertex has more the maximum of 8 (or 6 for SF6) weights assigned to it.
	
HOW TO FIX:
_______________
	
Limit total weights to 8 in weight paint mode and normalize all weights from the Weights menu.

OR

Use the "Limit Total and Normalize All Weights" button in the RE Mesh tab.

"""),

    "ExtendedMaxWeightsPerVertexExceeded": (ERROR, """Extended Max Weights Per Vertex Exceeded On Sub Mesh
A vertex has more the maximum of 16 (or 12 for SF6 and MH Wilds) weights assigned to it.
	
HOW TO FIX:
_______________
	
Limit total weights to 16 in weight paint mode and normalize all weights from the Weights menu.

OR

Use the "Limit Total and Normalize All Weights" button the RE Mesh tab.

"""),

    "NoBonesOnArmature": (ERROR, """No Bones on Armature
The armature in the target collection has no bones.
	
HOW TO FIX:
_______________
	
Import a valid armature from an existing mesh file.
"""),

    "NoArmatureInCollection": (ERROR, """No Armature In Collection
A mesh has weights but no armature is inside the mesh collection.
	
HOW TO FIX:
_______________
	
Move the armature that the mesh is parented to inside the mesh collection.

You can do this by selecting the armature in the outliner and dragging it onto the mesh collection.

"""),

    "NoWeightsOnMesh": (ERROR, """No Weights on Sub Mesh
A mesh has an armature, but no weights assigned to bones.
	
HOW TO FIX:
_______________
	
Add a new vertex group and weight it to a bone on the armature in weight paint mode.
"""),
    "MaxWeightedBonesExceeded": (ERROR, """Max Weighted Bones Exceeded
The mesh exceeded the limit of 256 bones with weights assigned to them.
	
HOW TO FIX:
_______________
	
Reduce the amount of chain bones on the armature.
"""),
    "InvalidWeights": (ERROR, """Invalid Weighting
A submesh contains invalid weights and can't be exported.
	
HOW TO FIX:
_______________
	
Check that the armature is inside the mesh collection.

Also check that all bones that are being weighted to are on the armature.

Be sure to limit total weights to 6, normalize all, and remove unweighted vertex groups.

"""),

    "InvalidMeshNamingScheme": (WARNING, """Invalid Mesh Naming Scheme
An object name doesn't follow the RE mesh naming scheme, so it was exported with viscon group ID 0 and/or the material name was taken from the Blender material slot instead of the object name.

This can cause submeshes to end up in the wrong group or materials to not match the game's materials.

HOW TO FIX:
_______________

Use the "Rename Meshes" operator in the RE Mesh tab to rename all meshes to the naming scheme.

Example Object Name: LOD_0_Group_0_Sub_0__pl1000_Body_Mat
"""),
    "VertexGroupsNotOnArmature": (WARNING, """Vertex Group Weighted To Missing Bone
A vertex group has weights assigned to a bone that doesn't exist on the armature. Those weights are exported on the first weighted bone instead.

HOW TO FIX:
_______________

Check the vertex groups on the listed meshes (Object Data Properties > Vertex Groups) and remove or rename the groups that don't match an armature bone name.
"""),
    "MeshMaterialsMissingFromMDF": (WARNING, """Mesh Material Missing From MDF
The mesh uses a material that doesn't exist in the mesh's MDF file. The material may not render correctly in game.

HOW TO FIX:
_______________

Make sure the material name matches the name in the MDF file exactly, or add the material to the MDF file.
"""),
    "MDFMaterialsMissingFromMesh": (WARNING, """MDF Material Not Used By Mesh
The MDF file contains a material that no mesh in the export uses. This is usually fine, but it can also mean that the mesh is missing a submesh or that the wrong MDF was found.

HOW TO FIX:
_______________

Check that the mesh contains all of the submeshes that the MDF file expects.
"""),
}


_ERROR_OBJECTS_HEADER = "Objects with this error"
_WARNING_OBJECTS_HEADER = "Items with this warning"

_ISSUE_FORMATS = {
    ERROR: (textColors.FAIL, "Unable to export mesh. ",
            " error(s) were found that need to be fixed.", "ERROR", _ERROR_OBJECTS_HEADER),
    WARNING: (textColors.WARNING, "", " warning type(s) were found during export:",
              "WARNING", _WARNING_OBJECTS_HEADER),
}


def _objectListString(objectSet, header):
    if not objectSet:
        return ""
    return f"\n{header} ({str(len(objectSet))}):\n" + "".join(f"[{name}]\n" for name in sorted(objectSet))


def _printIssues(issueDict, kind):
    color, header, suffix, label, objectHeader = _ISSUE_FORMATS[kind]
    issueTypes = sorted(issueDict.keys())
    for errorType in issueTypes:
        if ISSUES[errorType][0] != kind:
            raise ValueError(f"{errorType} is registered as {ISSUES[errorType][0]}, but it was reported as {kind}.")
    print(f"\n{color}{header}{len(issueDict)}{suffix}{textColors.ENDC}\n")
    for index, errorType in enumerate(issueTypes):
        message = ISSUES[errorType][1]
        count = issueDict[errorType]["count"]
        nameListString = _objectListString(issueDict[errorType]["objectSet"], objectHeader)
        print(
            f"{color}{label} ({str(index + 1)} / {len(issueDict)}): {str(count)} instance(s) of {message}{nameListString}\n__________________________________{textColors.ENDC}")


def printErrorDict(errorDict):
    _printIssues(errorDict, ERROR)


def printWarningDict(warningDict):
    _printIssues(warningDict, WARNING)


if bpy is not None:
    def showREMeshErrorWindow(targetCollectionName, armatureObj, errorDict):
        bpy.types.Scene.re_mesh_error_list = bpy.props.CollectionProperty(type=REMeshErrorEntry)
        bpy.context.scene.re_mesh_error_list.clear()
        for errorType in sorted(errorDict.keys()):
            item = bpy.context.scene.re_mesh_error_list.add()
            item.errorCount = errorDict[errorType]["count"]
            errorInfoSplit = ISSUES[errorType][1].split("\n", 1)
            item.errorType = errorInfoSplit[0]
            item.errorDescription = errorInfoSplit[1]
            item.objectSetString = _objectListString(errorDict[errorType]["objectSet"], _ERROR_OBJECTS_HEADER)
        if armatureObj is not None:
            armatureName = armatureObj.name
        else:
            armatureName = "None"

        bpy.ops.re_mesh.show_export_error_window('INVOKE_DEFAULT', targetCollection=targetCollectionName,
                                                 armatureName=armatureName)


# Registration
# empty without Blender, e.g. when the export-issue rules are imported by tests
classes = (
	REMeshErrorEntry,
	MESH_UL_REMeshErrorList,
	WM_OT_ShowREMeshErrorWindow,
) if bpy is not None else ()
