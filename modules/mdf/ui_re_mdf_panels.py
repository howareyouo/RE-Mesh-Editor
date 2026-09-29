import bpy

from bpy.types import (
	Panel,
	Menu,
	Operator,
	PropertyGroup,
)
from ..blender_utils import tag_redraw
from .file_re_mdf import getMDFVersionToGameName


def getActiveMaterialMDFVersion(context):
	#MDF version the active material was imported from, -1 when the game (and with it the version) is unknown
	obj = context.active_object
	if obj == None:
		return -1
	return getMDFVersionToGameName(obj.re_mdf_material.gameName)


class OBJECT_PT_MDFObjectModePanel(Panel):
	bl_label = "RE MDF Tools"
	bl_idname = "OBJECT_PT_mdf_tools_panel"
	bl_space_type = "VIEW_3D"   
	bl_region_type = "UI"
	bl_category = "RE Mesh"   
	bl_context = "objectmode"

	@classmethod
	def poll(self,context):
		return context is not None and "HIDE_RE_MDF_EDITOR_TAB" not in context.scene

	def draw(self, context):
		layout = self.layout
		scene = context.scene
		re_mdf_toolpanel = scene.re_mdf_toolpanel
		layout.operator("re_mdf.create_mdf_collection",icon = "COLLECTION_NEW")
		layout.label(text = "Active MDF Material Collection")
		layout.prop_search(re_mdf_toolpanel, "mdfCollection",bpy.data,"collections",icon = "COLLECTION_COLOR_05")
		layout.label(text = "Active Game")
		layout.prop(re_mdf_toolpanel, "activeGame")
		layout.operator("re_mdf.nullify_texture_bindings")
		layout.operator("re_mdf.clear_unused_textures")
		layout.operator("re_mdf.reindex_materials")
		
		
class OBJECT_PT_MDFMaterialPresetPanel(Panel):
	bl_label = "MDF Material Presets"
	bl_idname = "OBJECT_PT_mdf_material_preset_panel"
	bl_parent_id = "OBJECT_PT_mdf_tools_panel"  # Specify the ID of the parent panel
	bl_space_type = "VIEW_3D"   
	bl_region_type = "UI"
	bl_category = "RE Mesh"   
	bl_options = {'DEFAULT_CLOSED'}
	
	def draw(self, context):
		layout = self.layout
		obj = context.active_object
		re_mdf_toolpanel = context.scene.re_mdf_toolpanel
		row = layout.row()
		row.emboss = "PULLDOWN_MENU"
		row.label(text="Premade MDF materials.")
		split = layout.split(factor=0.025)#Indent list slightly to make it more clear it's a part of a sub panel
		col1 = split.column()
		col2 = split.column()
		col2.prop(re_mdf_toolpanel, "materialPresets")
		col2.operator("re_mdf.add_preset_material")
		col2.operator("re_mdf.save_selected_as_preset")
		col2.operator("re_mdf.open_preset_folder")

class OBJECT_PT_MDFMaterialPreviewPanel(Panel):
	bl_label = "MDF Preview"
	bl_idname = "OBJECT_PT_mdf_material_preview_panel"
	bl_parent_id = "OBJECT_PT_mdf_tools_panel"  # Specify the ID of the parent panel
	bl_space_type = "VIEW_3D"
	bl_region_type = "UI"
	bl_category = "RE Mesh"
	bl_options = {'DEFAULT_CLOSED'}
	
	def draw(self, context):
		layout = self.layout
		obj = context.active_object
		re_mdf_toolpanel = context.scene.re_mdf_toolpanel
		row = layout.row()
		row.emboss = "PULLDOWN_MENU"
		row.label(text="View MDF materials in Blender.")
		split = layout.split(factor=0.025)#Indent list slightly to make it more clear it's a part of a sub panel
		col1 = split.column()
		col2 = split.column()
		
		
		col2.label(text = "Mesh Collection")
		col2.prop_search(re_mdf_toolpanel, "meshCollection",bpy.data,"collections",icon = "COLLECTION_COLOR_01")
		col2.label(text = "Mod Directory")
		col2.prop(re_mdf_toolpanel, "modDirectory")
		col2.operator("re_mdf.apply_mdf")

class OBJECT_PT_MDFMaterialLoadSettingsPanel(Panel):
	bl_label = "MDF Load Settings"
	bl_idname = "OBJECT_PT_mdf_material_load_settings_panel"
	bl_parent_id = "OBJECT_PT_mdf_material_preview_panel"  # Specify the ID of the parent panel
	bl_space_type = "VIEW_3D"   
	bl_region_type = "UI"
	bl_category = "RE Mesh"   
	bl_options = {'DEFAULT_CLOSED'}
	
	def draw(self, context):
		layout = self.layout
		obj = context.active_object
		re_mdf_toolpanel = context.scene.re_mdf_toolpanel
		split = layout.split(factor=0.025)#Indent list slightly to make it more clear it's a part of a sub panel
		col1 = split.column()
		col2 = split.column()
		col2.alignment='RIGHT'
		col2.prop(re_mdf_toolpanel,"reloadCachedTextures")
		col2.prop(re_mdf_toolpanel, "loadUnusedTextures")
		col2.prop(re_mdf_toolpanel, "loadUnusedProps")
		col2.prop(re_mdf_toolpanel, "useBackfaceCulling")
		
		
class OBJECT_PT_MDFMaterialPanel(Panel):
	bl_label = "RE MDF Material Settings"
	bl_idname = "OBJECT_PT_mdf_material_panel"
	bl_space_type = "PROPERTIES"   
	bl_region_type = "WINDOW"
	bl_category = "RE MDF Material Settings"
	bl_context = "object"

	
	@classmethod
	def poll(self,context):
		
		return context and context.object.mode == "OBJECT" and context.active_object.get("~TYPE",None) == "RE_MDF_MATERIAL" and not "HIDE_RE_MDF_EDITOR_PANEL" in context.scene

	def draw(self, context):
		layout = self.layout
		obj = context.active_object
		re_mdf_material = obj.re_mdf_material
		
		split = layout.split(factor=0.025)
		col1 = split.column()
		col2 = split.column()
		col2.label(text = f"{re_mdf_material.gameName} Material")
		split = layout.split(factor=0.01)
		col3 = split.column()
		col4 = split.column()
		col4.alignment='RIGHT'
		col4.use_property_split = True
		
		col4.prop(re_mdf_material, "materialName")
		col4.prop(re_mdf_material, "mmtrPath") 
		col4.prop(re_mdf_material, "shaderType")
		col4.prop(re_mdf_material, "linkedMaterial")

class OBJECT_PT_MDFFlagsPanel(Panel):
	bl_label = "Flags"
	bl_idname = "OBJECT_PT_mdf_material_flags_panel"
	bl_parent_id = "OBJECT_PT_mdf_material_panel"  # Specify the ID of the parent panel
	bl_space_type = 'PROPERTIES'
	bl_region_type = 'WINDOW'
	bl_options = {'DEFAULT_CLOSED'}
	
	def draw(self, context):
		layout = self.layout
		obj = context.active_object
		re_mdf_material = obj.re_mdf_material
		flags = re_mdf_material.flags
		#Bit Flag B and the SF6 only fields are only written to the file on MDF version 31 (SF6) and
		#newer, so hide them for older materials where they have no effect
		isSF6OrNewer = getActiveMaterialMDFVersion(context) >= 31
		split = layout.split(factor=0.025)#Indent list slightly to make it more clear it's a part of a sub panel
		col1 = split.column()
		col2 = split.column()
		col2.alignment='RIGHT'
		col2.prop(flags, "hideMaterialInGame")
		if isSF6OrNewer:
			col2.prop(flags, "shaderLODNum")
			col2.prop(flags, "bakeTextureArraySize")
		col2.separator()

		col2.prop(flags,"flagIntValue")
		if isSF6OrNewer:
			col2.prop(flags, "flagIntValueB")
		col2.separator()
		col2.separator(type = "LINE")

		col2.prop(flags,"BaseTwoSideEnable")
		col2.prop(flags,"BaseAlphaTestEnable")
		col2.prop(flags,"ShadowCastDisable")
		col2.prop(flags,"VertexShaderUsed")
		col2.prop(flags,"EmissiveUsed")
		col2.prop(flags,"TessellationEnable")
		col2.prop(flags,"EnableIgnoreDepth")
		col2.prop(flags,"AlphaMaskUsed")
		col2.prop(flags,"ForcedTwoSideEnable")
		col2.prop(flags,"TwoSideEnable")
		if isSF6OrNewer:
			col2.prop(flags,"TransparentZPostPassEnable")
		col2.prop(flags,"TessFactor")
		col2.prop(flags,"PhongFactor")
		col2.prop(flags,"RoughTransparentEnable")
		col2.prop(flags,"ForcedAlphaTestEnable")
		col2.prop(flags,"AlphaTestEnable")
		col2.prop(flags,"SSSProfileUsed")
		col2.prop(flags,"EnableStencilPriority")
		col2.prop(flags,"RequireDualQuaternion")
		col2.prop(flags,"PixelDepthOffsetUsed")
		col2.prop(flags,"NoRayTracing")

		if isSF6OrNewer:
			col2.separator()
			col2.separator(type = "LINE")
			col2.prop(flags,"TransparentDistortionEnable")
			col2.prop(flags,"AlphaUsed")
			col2.prop(flags,"BakeTextureUseSecondaryUV")
			col2.prop(flags,"ForwardPrepassEnabled")
			col2.prop(flags,"ForcedAlphaTestEnableShadow")
			col2.prop(flags,"TessellationZPrepassDisable")
			col2.prop(flags,"DitheredLodTransitionEnable")
			col2.prop(flags,"reserved0")
			col2.prop(flags,"TransparentPriorityBias")
			col2.prop(flags,"reserved1")
			col2.prop(flags,"reserved2")
		

def _build_list_panel(cls_name, idname, label, listtype_name, propname, active_propname, count_attr, count_prefix, rows, warning_label = None, has_replace_operator = False, panel_options = None, min_mdf_version = None):
	# NOTE: the parameter names deliberately differ from the Blender panel
	# attributes they feed (label/idname/panel_options) -- a class body cannot
	# read a name it assigns itself (LOAD_NAME never reaches the enclosing
	# function scope), so `bl_label = bl_label` would raise NameError.
	class _ListPanel(Panel):
		bl_label = label
		bl_idname = idname
		bl_parent_id = "OBJECT_PT_mdf_material_panel"
		bl_space_type = 'PROPERTIES'
		bl_region_type = 'WINDOW'
		if panel_options is not None:
			bl_options = panel_options
		if warning_label is not None or min_mdf_version is not None:
			@classmethod
			def poll(cls, context):
				if context.active_object == None:
					return False
				#Data that only exists on newer MDF versions is hidden entirely, editing it
				#would either be dropped on export or corrupt the file layout
				if min_mdf_version is not None and getActiveMaterialMDFVersion(context) < min_mdf_version:
					return False
				return len(getattr(context.active_object.re_mdf_material, count_attr)) != 0
		def draw(self, context):
			layout = self.layout
			obj = context.active_object
			re_mdf_material = obj.re_mdf_material
			split = layout.split(factor=0.025)#Indent list slightly to make it more clear it's a part of a sub panel
			col1 = split.column()
			col2 = split.column()
			if warning_label is not None:
				col2.label(text = warning_label)
			col2.label(text = f"{count_prefix}: {str(len(getattr(obj.re_mdf_material, count_attr)))}")
			if has_replace_operator:
				col2.operator("re_mdf.replace_texture_bindings")
			col2.template_list(
				listtype_name = listtype_name,
				list_id = "",
				dataptr = re_mdf_material,
				propname = propname,
				active_dataptr = re_mdf_material,
				active_propname = active_propname,
				rows = rows,
				type='DEFAULT'
			)
	_ListPanel.__name__ = cls_name
	_ListPanel.__qualname__ = cls_name
	_ListPanel.__module__ = __name__
	return _ListPanel

OBJECT_PT_MDFMaterialPropertyListPanel = _build_list_panel(
	"OBJECT_PT_MDFMaterialPropertyListPanel",
	"OBJECT_PT_mdf_material_proplist_panel",
	"Property List",
	"MESH_UL_MDFPropertyList",
	"propertyList_items",
	"propertyList_index",
	"propertyList_items",
	"Property Count",
	6,
)
OBJECT_PT_MDFMaterialTextureBindingListPanel = _build_list_panel(
	"OBJECT_PT_MDFMaterialTextureBindingListPanel",
	"OBJECT_PT_mdf_material_texturebindinglist_panel",
	"Texture Bindings",
	"MESH_UL_MDFTextureBindingList",
	"textureBindingList_items",
	"textureBindingList_index",
	"textureBindingList_items",
	"Texture Binding Count",
	6,
	has_replace_operator = True,
)
OBJECT_PT_MDFMaterialMMTRSIndexListPanel = _build_list_panel(
	"OBJECT_PT_MDFMaterialMMTRSIndexListPanel",
	"OBJECT_PT_mdf_material_mmtrsindexlist_panel",
	"MMTRS Data",
	"MESH_UL_MDFMMTRSDataList",
	"mmtrsData_items",
	"mmtrsData_index",
	"mmtrsData_items",
	"Index Count",
	8,
	warning_label = "Do not change these unless you know what you're doing.",
	panel_options = {"DEFAULT_CLOSED"},
	min_mdf_version = 31,#MMTRS data is only present in the file on SF6 (version 31) and newer
)
OBJECT_PT_MDFMaterialGPBFDataListPanel = _build_list_panel(
	"OBJECT_PT_MDFMaterialGPBFDataListPanel",
	"OBJECT_PT_mdf_material_gpbfdatalist_panel",
	"GPBF Data",
	"MESH_UL_MDFGPBFDataList",
	"gpbfData_items",
	"gpbfData_index",
	"gpbfData_items",
	"Index Count",
	3,
	warning_label = "Do not change these unless you know what you're doing.",
	panel_options = {"DEFAULT_CLOSED"},
	min_mdf_version = 19,#GPBF buffer entries are only present in the file on version 19 and newer
)