#Author: NSA Cloud
bl_info = {
	"name": "RE Mesh Editor",
	"author": "NSA Cloud",
	"version": (0, 65),
	"blender": (4, 3, 2),
	"location": "File > Import-Export",
	"description": "Import and export RE Engine Mesh files natively into Blender. No Noesis required.",
	"warning": "",
	"wiki_url": "https://github.com/NSACloud/RE-Mesh-Editor",
	"tracker_url": "https://github.com/NSACloud/RE-Mesh-Editor/issues",
	"category": "Import-Export"}

import bpy
from . import addon_updater_ops
from datetime import datetime
import os
from bpy_extras.io_utils import ExportHelper,ImportHelper
from bpy.props import StringProperty, BoolProperty,IntProperty, EnumProperty, CollectionProperty,PointerProperty
from bpy.types import Operator, OperatorFileListElement,AddonPreferences
from .modules.gen_functions import textColors,raiseWarning,getFolderSize,formatByteSize,splitNativesPath,openFolder,parseFileVersion
from .modules.blender_utils import printEditorHeader, isMDFCollection, isMeshCollection, isSFurCollection, findMDFCollectionForMesh, getMeshMaterialNames, resolveSourcePath
from .modules.game_versions import mdfVersionDict
#mesh
from .modules.mesh.file_re_mesh import meshFileVersionToGameNameDict
from .modules.mesh.blender_re_mesh import importREMeshFile,exportREMeshFile
from .modules.mesh.re_mesh_propertyGroups import classes as meshPropertyGroupClasses
from .modules.mesh.re_mesh_operators import classes as meshOperatorClasses
from .modules.mesh.ui_re_mesh_panels import classes as meshPanelClasses
# Mesh rename -> material name sync (timer + load handler, see register below)
from .modules.mesh import blender_re_mesh_name_sync


#mdf
from.modules.mdf.file_re_mdf import gameNameMDFVersionDict
from .modules.mdf.blender_re_mdf import importMDFFile,exportMDFFile
from .modules.mdf.blender_re_mdf_texture_files import registerUndoHandlers, unregisterUndoHandlers
from .modules.mdf.ui_re_mdf_panels import classes as mdfPanelClasses
from .modules.mdf.re_mdf_propertyGroups import classes as mdfPropertyGroupClasses

from .modules.mdf.re_mdf_operators import classes as mdfOperatorClasses
#tex
from .modules.tex.file_re_tex import gameNameToTexVersionDict

from .modules.tex.ui_re_tex_panels import classes as texPanelClasses

from .modules.tex.re_tex_operators import classes as texOperatorClasses
from .modules.tex.re_tex_propertyGroups import classes as texPropertyGroupClasses
from .modules.mesh.re_mesh_export_errors import classes as meshErrorClasses
#fbxskel
from .modules.fbxskel.blender_re_fbxskel import importFBXSkelFile,exportFBXSkelFile
from .modules.fbxskel.re_fbxskel_operators import classes as fbxskelOperatorClasses
from .modules.fbxskel.re_fbxskel_propertyGroups import classes as fbxskelPropertyGroupClasses

#sfur
from .modules.sfur.blender_re_sfur import importSFurFile,exportSFurFile
from .modules.sfur.re_sfur_operators import classes as sfurOperatorClasses
from .modules.sfur.ui_re_sfur_panels import classes as sfurPanelClasses
from .modules.sfur.re_sfur_propertyGroups import classes as sfurPropertyGroupClasses
#mod workspace

from .modules.workspace.re_mod_workspace_operators import classes as workspaceOperatorClasses

from .modules.workspace.re_mod_workspace_propertyGroups import classes as workspacePropertyGroupClasses
from .modules.workspace.ui_re_mod_workspace_panels import classes as workspacePanelClasses
# Import/export operators and addon preferences, split out of __init__.py
from . import preferences
from .modules import io_operators
from .modules.io_operators import (ImportREMesh, ExportREMesh, ImportREMDF, ExportREMDF,
                                   ImportREFBXSkel, ExportREFBXSkel, ImportRESFur, ExportRESFur)

# Property group types reattached as RNA properties in register() below; the owning
# module also lists them in its `classes` tuple above.
from .modules.mdf.re_mdf_operators import FindReplacePropertyGroup
from .modules.mdf.re_mdf_propertyGroups import MDFMaterialPropertyGroup, MDFToolPanelPropertyGroup
from .modules.sfur.re_sfur_propertyGroups import SFurEntryPropertyGroup
from .modules.workspace.re_mod_workspace_propertyGroups import ModWorkspaceToolPanelPropertyGroup


from .modules.registration import orderClasses

# Registration. Each module owns the classes it defines (the `classes` tuple at the foot
# of the file) in whatever order it declares them; orderClasses then puts every property
# group before the classes whose annotations reference it, which Blender requires.
classes = orderClasses([
	*preferences.classes,
	*io_operators.classes,
	*workspacePropertyGroupClasses,
	*workspaceOperatorClasses,
	*workspacePanelClasses,
	*meshOperatorClasses,
	*meshPropertyGroupClasses,
	*mdfPropertyGroupClasses,
	*meshErrorClasses,
	*mdfPanelClasses,
	*meshPanelClasses,
	*mdfOperatorClasses,
	*sfurOperatorClasses,
	*sfurPanelClasses,
	*sfurPropertyGroupClasses,
	*texPropertyGroupClasses,
	*texOperatorClasses,
	*texPanelClasses,
	*fbxskelPropertyGroupClasses,
	*fbxskelOperatorClasses,
])


#Drag and drop importing, 4.1 or higher only
if bpy.app.version >= (4, 1, 0):
	class RE_FH_base(bpy.types.FileHandler):
		@classmethod
		def poll_drop(cls, context):
			return (context.area and context.area.type == 'VIEW_3D')

	def make_file_handler(bl_idname, label, operator, extensions):
		return type(bl_idname, (RE_FH_base,), {
			'bl_idname': bl_idname,
			'bl_label': label,
			'bl_import_operator': operator,
			'bl_file_extensions': extensions,
		})

	meshExtensionsString = "".join(f".{str(v)};" for v in meshFileVersionToGameNameDict.keys())
	MESH_FH_drag_import = make_file_handler(
		"MESH_FH_drag_import", "File handler for RE Mesh importing",
		"re_mesh.importfile", meshExtensionsString)
	mdfExtensionsString = "".join(f".{str(v)};" for v in gameNameMDFVersionDict.keys() if isinstance(v, int))
	MDF_FH_drag_import = make_file_handler(
		"MDF_FH_drag_import", "File handler for RE MDF importing",
		"re_mdf.importfile", mdfExtensionsString)
	texExtensionsString = ".tif;.tiff;.tga;.png;.dds;" + "".join(
		f".{str(v)};" for v in gameNameToTexVersionDict.values()) + ".31;"  # RE Verse tex drag and drop support
	TEX_FH_drag_import = make_file_handler(
		"TEX_FH_drag_import", "File handler for RE Tex Conversion",
		"re_tex.convert_tex_dds_files", texExtensionsString)
	fbxSkelExtensionsString = ".4;.5;.6;.7;.8;"
	FBXSKEL_FH_drag_import = make_file_handler(
		"FBXSKEL_FH_drag_import", "File handler for RE FBXSkel importing",
		"re_fbxskel.importfile", fbxSkelExtensionsString)
	sfurExtensionsString = ".4;.5;"
	SFUR_FH_drag_import = make_file_handler(
		"SFUR_FH_drag_import", "File handler for RE SFur importing",
		"re_sfur.importfile", sfurExtensionsString)

class IMPORT_MT_re_mesh_editor(bpy.types.Menu):
    bl_label = "RE Mesh Editor"
    bl_idname = "IMPORT_MT_re_mesh_editor"
    
    def draw(self, context):
        layout = self.layout
        
        layout.operator(ImportREMesh.bl_idname, text="RE Mesh (.mesh.x) (Model)",icon = "MESH_DATA")
        layout.operator(ImportREMDF.bl_idname, text="RE MDF (.mdf2.x) (Materials)",icon = "MATERIAL")
        layout.operator(ImportREFBXSkel.bl_idname, text="RE FBXSkel (.fbxskel.x) (Skeleton)",icon = "ARMATURE_DATA")
        layout.operator(ImportRESFur.bl_idname, text="RE SFur (.sfur.x) (Shell Fur)",icon = "CURVES_DATA")

def re_mesh_editor_import(self, context):
	self.layout.menu("IMPORT_MT_re_mesh_editor",icon = "MOD_LINEART")
	
class EXPORT_MT_re_mesh_editor(bpy.types.Menu):
    bl_label = "RE Mesh Editor"
    bl_idname = "EXPORT_MT_re_mesh_editor"
    
    def draw(self, context):
        layout = self.layout
        
        layout.operator(ExportREMesh.bl_idname, text="RE Mesh (.mesh.x) (Model)",icon = "MESH_DATA")
        layout.operator(ExportREMDF.bl_idname, text="RE MDF (.mdf2.x) (Materials)",icon = "MATERIAL")
        layout.operator(ExportREFBXSkel.bl_idname, text="RE FBXSkel (.fbxskel.x) (Skeleton)",icon = "ARMATURE_DATA")
        layout.operator(ExportRESFur.bl_idname, text="RE SFur (.sfur.x) (Shell Fur)",icon = "CURVES_DATA")

def re_mesh_editor_export(self, context):
	self.layout.menu("EXPORT_MT_re_mesh_editor",icon = "MOD_LINEART")

def register():
	addon_updater_ops.register(bl_info)
	for classEntry in classes:
		bpy.utils.register_class(classEntry)
		
	bpy.utils.register_class(IMPORT_MT_re_mesh_editor)
	bpy.utils.register_class(EXPORT_MT_re_mesh_editor)
	
	
	#REGISTER PROPERTY GROUP PROPERTIES
	bpy.types.WindowManager.enableModFileTracking = bpy.props.BoolProperty(default=False)
	bpy.types.Scene.re_mdf_toolpanel = PointerProperty(type=MDFToolPanelPropertyGroup)
	bpy.types.Scene.re_modworkspace_toolpanel = PointerProperty(type=ModWorkspaceToolPanelPropertyGroup)
	bpy.types.Object.re_mdf_material = PointerProperty(type=MDFMaterialPropertyGroup)
	bpy.types.Object.re_sfur_data = PointerProperty(type=SFurEntryPropertyGroup)
	bpy.types.WindowManager.re_mdf_findreplace = PointerProperty(type=FindReplacePropertyGroup)
	bpy.types.TOPBAR_MT_file_import.append(re_mesh_editor_import)
	bpy.types.TOPBAR_MT_file_export.append(re_mesh_editor_export)

	#Blender 4.1 and higher drag and drop operators
	if bpy.app.version >= (4, 1, 0):
		bpy.utils.register_class(MESH_FH_drag_import)
		bpy.utils.register_class(MDF_FH_drag_import)
		bpy.utils.register_class(TEX_FH_drag_import)
		bpy.utils.register_class(FBXSKEL_FH_drag_import)
		bpy.utils.register_class(SFUR_FH_drag_import)

	#Timer + load handler that keep renamed mesh material names in sync with the MDF
	blender_re_mesh_name_sync.register()
	#Undo/redo handler that renames the texture files back when a rename is undone
	registerUndoHandlers()
		
	
def unregister():
	blender_re_mesh_name_sync.unregister()
	unregisterUndoHandlers()
	del bpy.types.WindowManager.enableModFileTracking
	del bpy.types.WindowManager.re_mdf_findreplace
	del bpy.types.Scene.re_mdf_toolpanel
	del bpy.types.Scene.re_modworkspace_toolpanel
	del bpy.types.Object.re_mdf_material
	del bpy.types.Object.re_sfur_data
	addon_updater_ops.unregister()
	for classEntry in reversed(classes):
		bpy.utils.unregister_class(classEntry)
		
	bpy.utils.unregister_class(IMPORT_MT_re_mesh_editor)
	bpy.utils.unregister_class(EXPORT_MT_re_mesh_editor)
	
	
	bpy.types.TOPBAR_MT_file_import.remove(re_mesh_editor_import)
	bpy.types.TOPBAR_MT_file_export.remove(re_mesh_editor_export)
	
	if bpy.app.version >= (4, 1, 0):
		bpy.utils.unregister_class(MESH_FH_drag_import)
		bpy.utils.unregister_class(MDF_FH_drag_import)
		bpy.utils.unregister_class(TEX_FH_drag_import)
		bpy.utils.unregister_class(FBXSKEL_FH_drag_import)
		bpy.utils.unregister_class(SFUR_FH_drag_import)
if __name__ == '__main__':
	register()
