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
from .modules.mesh.re_mesh_propertyGroups import (
	ExporterNodePropertyGroup,
	MESH_UL_REExporterList
	)
from .modules.mesh.re_mesh_operators import (
	WM_OT_DeleteLoose,
	WM_OT_RenameMeshToREFormat,
	WM_OT_RemoveZeroWeightVertexGroups,
	WM_OT_LimitTotalNormalizeAll,
	WM_OT_CreateMeshCollection,
	WM_OT_REBatchExporter,
	WM_OT_SolveRepeatedUVs,
	WM_OT_QuickBatchExport,

)
from .modules.mesh.ui_re_mesh_panels import (
	OBJECT_PT_MeshObjectModePanel,
	OBJECT_PT_MeshArmatureToolsPanel,
	OBJECT_PT_REAssetExtensionPanel,
	)


#mdf
from.modules.mdf.file_re_mdf import gameNameMDFVersionDict
from .modules.mdf.blender_re_mdf import importMDFFile,exportMDFFile
from .modules.mdf.ui_re_mdf_panels import (
	OBJECT_PT_MDFObjectModePanel,
	OBJECT_PT_MDFMaterialPresetPanel,
	OBJECT_PT_MDFMaterialPreviewPanel,
	OBJECT_PT_MDFMaterialPanel,
	OBJECT_PT_MDFFlagsPanel,
	OBJECT_PT_MDFMaterialPropertyListPanel,
	OBJECT_PT_MDFMaterialTextureBindingListPanel,
	OBJECT_PT_MDFMaterialMMTRSIndexListPanel,
	OBJECT_PT_MDFMaterialGPBFDataListPanel,
	OBJECT_PT_MDFMaterialLoadSettingsPanel,
	)
from .modules.mdf.re_mdf_propertyGroups import (
	MDFToolPanelPropertyGroup,
	MDFFlagsPropertyGroup,
	MDFPropPropertyGroup,
	MDFTextureBindingPropertyGroup,
	MDFMaterialPropertyGroup,
	MDFMMTRSIndexPropertyGroup,
	MDFGPBFDataPropertyGroup,
	
	MESH_UL_MDFPropertyList,
	MESH_UL_MDFTextureBindingList,
	MESH_UL_MDFMMTRSDataList,
	MESH_UL_MDFGPBFDataList,
	)

from .modules.mdf.re_mdf_operators import (
	WM_OT_NewMDFHeader,
	WM_OT_ReindexMaterials,
	WM_OT_AddPresetMaterial,
	WM_OT_SavePreset,
	WM_OT_OpenPresetFolder,
	WM_OT_ApplyMDFToMeshCollection,
	WM_OT_FindReplaceTextureBindings,
	WM_OT_FindReplaceAddItem,
	WM_OT_FindReplaceRemoveItem,
	WM_OT_NullifyTextureBindings,
	WM_OT_ClearUnusedTextures,
	FindReplaceItem,
	FindReplacePropertyGroup,
)
#tex
from .modules.tex.file_re_tex import gameNameToTexVersionDict

from .modules.tex.ui_re_tex_panels import (
	OBJECT_PT_TexConversionPanel,
	)

from .modules.tex.re_tex_operators import (
	WM_OT_ConvertFolderToTex,
	WM_OT_CopyConvertedTextures,
	WM_OT_ConvertDDSTexFile,
	WM_OT_PNGToTexConversionWindow,
	

)
from .modules.tex.re_tex_propertyGroups import (
	PNGConversionEntryPropertyGroup,
	TEX_UL_PNGConversionList,
	)
from .modules.mesh.re_mesh_export_errors import  (
	REMeshErrorEntry,
	MESH_UL_REMeshErrorList,
	WM_OT_ShowREMeshErrorWindow,
	)
#fbxskel
from .modules.fbxskel.blender_re_fbxskel import importFBXSkelFile,exportFBXSkelFile
from .modules.fbxskel.re_fbxskel_operators import (
	WM_OT_LinkArmatureBones,
	WM_OT_ClearBoneLinkages
	)
from .modules.fbxskel.re_fbxskel_propertyGroups import (
	ToggleStringPropertyGroup,
	FBXSKEL_UL_ObjectCheckList
	)

#sfur
from .modules.sfur.blender_re_sfur import importSFurFile,exportSFurFile
from .modules.sfur.re_sfur_operators import (
	WM_OT_NewSFurHeader,
	WM_OT_ReindexEntries,
	WM_OT_AddFurEntry,
	)
from .modules.sfur.ui_re_sfur_panels import (
	OBJECT_PT_SFurObjectModePanel,
	OBJECT_PT_SFurEntryPanel,
	)
from .modules.sfur.re_sfur_propertyGroups import (
	SFurEntryPropertyGroup,
	)
#mod workspace

from .modules.workspace.re_mod_workspace_operators import (
	WM_OT_CreateModWorkspace,
	WM_OT_EditModInfo,
	WM_OT_ConvertToREEngine,
	WM_OT_TexturePacker,
	WM_OT_ToggleModFileTracking,
	WM_OT_CopyModFilesToGameDir,
	WM_OT_UninstallModFilesFromGameDir,
	WM_OT_LaunchGame,
	WM_OT_OpenModWorkspace,
	)

from .modules.workspace.re_mod_workspace_propertyGroups import (
	ModWorkspaceToolPanelPropertyGroup,
	ConvertedMaterialEntryPropertyGroup,
	REMOD_UL_MaterialConversionList,
	)
from .modules.workspace.ui_re_mod_workspace_panels import (
	OBJECT_PT_REModWorkspacePanel,
	)
# Import/export operators and addon preferences, split out of __init__.py
from . import preferences
from .modules import io_operators
from .modules.io_operators import (ImportREMesh, ExportREMesh, ImportREMDF, ExportREMDF,
                                   ImportREFBXSkel, ExportREFBXSkel, ImportRESFur, ExportRESFur)


# Registration. First the classes from the split-out modules (preferences.py and
# modules/io_operators.py), then the feature-module classes registered from here.
classes = [
	#preferences (preferences.py)
	*preferences.classes,
	#RE mesh / MDF / FBXSkel / SFur import & export (modules/io_operators.py)
	*io_operators.classes,
	#mod workspace
	#property groups
	ModWorkspaceToolPanelPropertyGroup,
	ConvertedMaterialEntryPropertyGroup,
	REMOD_UL_MaterialConversionList,
	#operators
	WM_OT_CreateModWorkspace,
	WM_OT_EditModInfo,
	WM_OT_ConvertToREEngine,
	#WM_OT_TexturePacker,#Not implemented yet
	WM_OT_ToggleModFileTracking,
	WM_OT_CopyModFilesToGameDir,
	WM_OT_UninstallModFilesFromGameDir,
	WM_OT_LaunchGame,
	WM_OT_OpenModWorkspace,
	#ui panels
	OBJECT_PT_REModWorkspacePanel,
	
	
	#mesh

	WM_OT_DeleteLoose,
	WM_OT_RenameMeshToREFormat,
	WM_OT_RemoveZeroWeightVertexGroups,
	WM_OT_LimitTotalNormalizeAll,
	WM_OT_CreateMeshCollection,
	ExporterNodePropertyGroup,
	MESH_UL_REExporterList,
	WM_OT_REBatchExporter,
	WM_OT_SolveRepeatedUVs,
	WM_OT_QuickBatchExport,
	
	#mdf
	#property groups
	MDFMMTRSIndexPropertyGroup,
	MDFGPBFDataPropertyGroup,
	MDFFlagsPropertyGroup,
	MDFToolPanelPropertyGroup,
	MDFPropPropertyGroup,
	MDFTextureBindingPropertyGroup,
	MDFMaterialPropertyGroup,
	REMeshErrorEntry,
	MESH_UL_REMeshErrorList,
	
	MESH_UL_MDFPropertyList,
	MESH_UL_MDFTextureBindingList,
	MESH_UL_MDFMMTRSDataList,
	MESH_UL_MDFGPBFDataList,
	
	#ui panels
	OBJECT_PT_MDFObjectModePanel,
	OBJECT_PT_MDFMaterialPresetPanel,
	OBJECT_PT_MDFMaterialPreviewPanel,
	OBJECT_PT_MDFMaterialLoadSettingsPanel,
	OBJECT_PT_MeshObjectModePanel,
	OBJECT_PT_MeshArmatureToolsPanel,
	OBJECT_PT_MDFMaterialPanel,
	OBJECT_PT_MDFFlagsPanel,
	OBJECT_PT_MDFMaterialTextureBindingListPanel,
	OBJECT_PT_MDFMaterialPropertyListPanel,
	OBJECT_PT_MDFMaterialMMTRSIndexListPanel,
	OBJECT_PT_MDFMaterialGPBFDataListPanel,
	
	
	#operators
	WM_OT_NewMDFHeader,
	WM_OT_ReindexMaterials,
	WM_OT_AddPresetMaterial,
	WM_OT_SavePreset,
	WM_OT_OpenPresetFolder,
	WM_OT_ApplyMDFToMeshCollection,
	WM_OT_ShowREMeshErrorWindow,
	WM_OT_FindReplaceTextureBindings,
	WM_OT_FindReplaceAddItem,
	WM_OT_FindReplaceRemoveItem,
	WM_OT_NullifyTextureBindings,
	WM_OT_ClearUnusedTextures,
	FindReplaceItem,
	FindReplacePropertyGroup,
	
	#sfur
	#operators
	WM_OT_NewSFurHeader,
	WM_OT_ReindexEntries,
	WM_OT_AddFurEntry,
	#WM_OT_ApplySFurToMeshCollection,
	#ui panels
	OBJECT_PT_SFurObjectModePanel,
	OBJECT_PT_SFurEntryPanel,
	#property groups
	SFurEntryPropertyGroup,
	
	#tex
	#property groups
	PNGConversionEntryPropertyGroup,
	TEX_UL_PNGConversionList,
	
	#operators
	WM_OT_ConvertFolderToTex,
	WM_OT_CopyConvertedTextures,
	WM_OT_ConvertDDSTexFile,
	WM_OT_PNGToTexConversionWindow,
	#ui panels
	OBJECT_PT_TexConversionPanel,
	
	
	#fbxskel
	#property groups
	ToggleStringPropertyGroup,
	FBXSKEL_UL_ObjectCheckList,
	#operators
	WM_OT_LinkArmatureBones,
	WM_OT_ClearBoneLinkages,
	
	
	#RE asset extra tools
	OBJECT_PT_REAssetExtensionPanel,
	]


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
		
	
def unregister():
	del bpy.types.WindowManager.enableModFileTracking
	del bpy.types.WindowManager.re_mdf_findreplace
	del bpy.types.Scene.re_mdf_toolpanel
	del bpy.types.Scene.re_modworkspace_toolpanel
	del bpy.types.Object.re_mdf_material
	del bpy.types.Object.re_sfur_data
	addon_updater_ops.unregister()
	for classEntry in classes:
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
