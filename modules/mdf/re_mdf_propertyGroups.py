#Author: NSA Cloud
import bpy
import os
from .file_re_mdf import MDFFlags,MDFFlagsB,MDFFlags_bits,MDFFlagsB_bits
from bpy.props import (StringProperty,
					   BoolProperty,
					   IntProperty,
					   FloatProperty,
					   FloatVectorProperty,
					   EnumProperty,
					   PointerProperty,
					   CollectionProperty
					   )

from .file_re_mdf import gameNameMDFVersionDict
from .blender_re_mesh_mdf import importMDF
from .re_mdf_presets import reloadPresets
from ..blender_utils import isMDFCollection, isMeshCollection, isSFurCollection, getMeshCollectionNameFromMDFName





def getUsableProps(propFileList):
	propSet = set()
	path = os.path.split(__file__)[0]
	for file in propFileList:
		f = open(os.path.join(path,file),"r")
		for line in f.readlines():
			if "addPropertyNode(matInfo[\"mPropDict\"]" in line:
				propName = line.split("addPropertyNode(matInfo[\"mPropDict\"][\"")[1].split("\"]",1)[0]
				propSet.add(propName)
		f.close()
	return propSet
try:
	editablePropsSet = getUsableProps(
	propFileList = [
		
		"blender_re_mesh_mdf.py",
		"blender_nodes_re_mdf.py",])
except Exception as err:
	print(f"Unable to load usable properties - {str(err)}")
	editablePropsSet = set()
#print(editablePropsSet)

def filterMDFCollection(self, collection):
    return isMDFCollection(collection)

def filterMeshCollection(self, collection):
    return isMeshCollection(collection)

def filterSFurCollection(self, collection):
    return isSFurCollection(collection)


flags = MDFFlags()#Bitflag struct
flagsB = MDFFlagsB()#Bitflag struct
def linkBlenderMaterial(materialObj,materialName):
	
	#Check if material name is used more than once, link it if it's only used once
	matchCount = 0
	materialKey = None
	for mat in bpy.data.materials.keys():
		if materialName == mat.split(".",1)[0]:
			materialKey = mat
			matchCount += 1
	if matchCount == 1:
		#print(f"Linked {materialObj.name} to {bpy.data.materials[materialKey]} (Fast)")
		materialObj.re_mdf_material.linkedMaterial = bpy.data.materials[materialKey]
	elif matchCount > 1:
		#If there's more than one material with the same name, search for a mesh collection with the same name as the mdf and find the material assigned to an object
		mdfCollectionName = None#Get first mdf collection linked to material obj
		for collection in materialObj.users_collection:
			if ".mdf2" in collection.name:
				mdfCollectionName = collection.name
		
		if mdfCollectionName != None:
			meshCollection = bpy.data.collections.get(getMeshCollectionNameFromMDFName(mdfCollectionName),None)
			
			if meshCollection != None:
				meshObj = None#Mesh object with mdf material assigned to it
				for obj in meshCollection.all_objects:
					if obj.type == "MESH":
						if materialName in obj.name and "__" in obj.name:
							if materialName == obj.name.split("__",1)[1].split(".")[0]:
								meshObj = obj
								break
				if meshObj != None:
					for material in meshObj.data.materials:
						if material.name.split(".")[0] == materialName:
							materialObj.re_mdf_material.linkedMaterial = material
							#print(f"Linked {materialObj.name} to {material} (Slow)")
							break						

def update_mdfCollection(self, context):#Set mesh collection automatically if it exists when active mdf is changed
	meshCollectionName = getMeshCollectionNameFromMDFName(self.mdfCollection.name) if self.mdfCollection != None else None
	if meshCollectionName != None and meshCollectionName in bpy.data.collections:
		self.meshCollection = bpy.data.collections[meshCollectionName]
								
def update_materialNodes(self,context):
	obj = self.id_data
	if self.prop_name in editablePropsSet:
		#matName = obj.re_mdf_material.materialName
		if obj.re_mdf_material.linkedMaterial == None:
			linkBlenderMaterial(obj, obj.re_mdf_material.materialName)
		
		#TODO filter by collection
		if obj.re_mdf_material.linkedMaterial != None:
			node = obj.re_mdf_material.linkedMaterial.node_tree.nodes.get(self.prop_name,None)
			if node != None:
				#print(self.data_type)
				#print(node.name)
				if self.data_type == "FLOAT":
					value = self.float_value
					node.outputs["Value"].default_value = value
				elif self.data_type == "VEC4":
					value = list(self.float_vector_value)
					node.inputs[0].default_value = value[0]
					node.inputs[1].default_value = value[1]
					node.inputs[2].default_value = value[2]
					node.inputs[3].default_value = value[3]
				elif self.data_type == "COLOR":
					value = list(self.color_value)
					node.inputs["Color"].default_value = value
	
				elif self.data_type == "BOOL":
					if self.bool_value:
						value = 1.0
					else:
						value = 0.0
					node.outputs["Value"].default_value = value
				else:
					value = 0.0
					node.outputs["Value"].default_value = value
	
		
			
			#print(f"Set Prop on {obj.name} - {matName} - {self.prop_name} - {self.data_type} - {str(value)}")

def update_modDirectoryRelPathToAbs(self,context):
	try:
		
		if "//" in self.modDirectory:
			#print("updated path")
			self.modDirectory = os.path.realpath(bpy.path.abspath(self.modDirectory))
	except:
		pass
def update_textureDirectoryRelPathToAbs(self,context):
	try:
		if "//" in self.textureDirectory:
			#print("updated path")
			self.textureDirectory = os.path.realpath(bpy.path.abspath(self.textureDirectory))
	except:
		pass
def update_listFilter(self, context):
	context.area.tag_redraw()
	
def update_materialName(self, context):
	parentObj = self.id_data
	try:
		#print(parentObj)
		if parentObj != None:
			split = parentObj.name.split("(",1)
			if len(split) == 2:
				parentObj.name = f"{split[0]}({self.materialName})"
			else:
				parentObj.name =f"Material 00 ({self.materialName})"
	except:
		pass

def update_FlagsFromInt(self, context):
	if not self.internal_changingFlagValues: 
		try:
			flags.asInt32 = self.flagIntValue
			flagsB.asInt32 = self.flagIntValueB
			self.internal_changingFlagValues = True
			for field_name, field_type, _ in flags.flagValues._fields_:
				#print(f"setting {field_name} to {abs(getattr(flags.flagValues, field_name))}")
				setattr(self,field_name,abs(getattr(flags.flagValues, field_name)))
			for field_name, field_type, _ in flagsB.flagValues._fields_:
				#print(f"setting {field_name} to {abs(getattr(flagsB.flagValues, field_name))}")
				setattr(self,field_name,abs(getattr(flagsB.flagValues, field_name)))
			self.internal_changingFlagValues = False
		except:
			self.internal_changingFlagValues = False
def update_IntFromFlags(self, context):
	if not self.internal_changingFlagValues:
		try:
			flags.asInt32 = 0
			flagsB.asInt32 = 0
			for field in flags.flagValues._fields_:
				fieldName = field[0]
				if fieldName in self:
					#print(f"setting {fieldName} to {getattr(self,fieldName)}")
					setattr(flags.flagValues,fieldName,getattr(self,fieldName))
			for field in flagsB.flagValues._fields_:
				fieldName = field[0]
				if fieldName in self:
					#print(f"setting {fieldName} to {getattr(self,fieldName)}")
					setattr(flagsB.flagValues,fieldName,getattr(self,fieldName))
						
			self.internal_changingFlagValues = True
			self.flagIntValue = flags.asInt32
			self.flagIntValueB = flagsB.asInt32 
			self.internal_changingFlagValues = False
		except:
			self.internal_changingFlagValues = False
class MDFToolPanelPropertyGroup(bpy.types.PropertyGroup):
	
	def getMaterialPresets(self,context):
		return reloadPresets(context.scene.re_mdf_toolpanel.activeGame)

	mdfCollection: bpy.props.PointerProperty(
		name="",
		description = "Set the collection containing the MDF file to edit.\nHint: MDF collections are blue.\nYou can create a new MDF collection by pressing the \"Create MDF Collection\" button",
		type=bpy.types.Collection,
		poll = filterMDFCollection,
		update = update_mdfCollection
		)
	activeGame: EnumProperty(
		name="",
		description="Set the game to determine which presets to use and which chunk paths to read textures from",
		items=[ 
				("DMC5", "Devil May Cry 5", ""),
				("RE2", "Resident Evil 2", ""),
				("RE3", "Resident Evil 3", ""),
				("MHRSB", "Monster Hunter Rise", ""),
				("RE8", "Resident Evil 8", ""),
				("RE2RT", "Resident Evil 2 Ray Tracing", ""),
				("RE3RT", "Resident Evil 3 Ray Tracing", ""),
				("RE7RT", "Resident Evil 7 Ray Tracing", ""),
				("RE4", "Resident Evil 4", ""),
				("SF6", "Street Fighter 6", ""),
				("DD2", "Dragon's Dogma 2", ""),
				("KG", "Kunitsu-Gami", ""),
				("DR", "Dead Rising", ""),
				("ONI2", "Onimusha 2", ""),
				("MHWILDS", "Monster Hunter Wilds", ""),
				("PRAG", "Pragmata", ""),
				("MHS3", "Monster Hunter Stories 3", ""),
				("RE9", "Resident Evil 9", ""),
			  ]
		)
	materialPresets: EnumProperty(
		name="",
		description="Set preset to be used by Add Material Preset button",
		items= getMaterialPresets
		)
	meshCollection: bpy.props.PointerProperty(
		name="",
		description = "Set the mesh collection to apply the active MDF collection to.\nHint: Mesh collections are red",
		type=bpy.types.Collection,
		poll = filterMeshCollection
		)
	sFurCollection: bpy.props.PointerProperty(
		name="",
		description = "Set the sfur collection to be edited.",
		type=bpy.types.Collection,
		poll = filterSFurCollection
		)
	modDirectory: bpy.props.StringProperty(
		name="",
		subtype = "DIR_PATH",
		description = "Set the natives directory of your mod.\nThis is used by \"Apply Active MDF\" and \"Copy Converted Tex\".\nThe platform folder must be included. (STM in most cases)\nThis will be set automatically when a mesh or mdf file is exported.\nExample:\n"+r"C:\Modding\Monster Hunter Rise\FluffyManager\Games\MHRISE\Mods\ArmorTest\natives\STM",
		update = update_modDirectoryRelPathToAbs
		)
	textureDirectory: bpy.props.StringProperty(
		name="",
		subtype = "DIR_PATH",
		description = "Set the directory containing images to be converted to .tex files",
		update = update_textureDirectoryRelPathToAbs
		)
	createStreamingTextures: BoolProperty(
		name = "Create Streaming Textures",
		description="Creates a low quality and a high quality texture. This lowers VRAM usage.\nHigh quality textures will be placed in the converted texture folder in a folder called \"streaming\".\nThese textures need to be placed at the same location as the non streaming textures, but the root level folder is called \"streaming\".\nExample:\nNon Streaming Tex Path: "+r"natives\STM\weapon\Bow\custom_bow\bow_Body_ALBD.tex.28"+"\nStreaming Tex Path: "+r"natives\STM\streaming\weapon\Bow\custom_bow\bow_Body_ALBD.tex.28",
		default = False,
		)
	openConvertedFolder: BoolProperty(
		name = "Open Folder After Conversion",
		description="Opens the directory containing the converted image files after conversion",
		default = False,
		)
	#mdf load settings
	loadUnusedTextures : BoolProperty(
	   name = "Load Unused Textures",
	   description = "Loads textures that have no function assigned to them in the material shader graph.\nLeaving this disabled will make materials load faster.\nOnly enable this if you plan on editing the material shader graph",
	   default = False)
	loadUnusedProps : BoolProperty(
	   name = "Load Unused Material Properties",
	   description = "Loads material properties that have no function assigned to them in the material shader graph.\nLeaving this disabled will make materials load faster.\nOnly enable this if you plan on editing the material shader graph",
	   default = False)
	useBackfaceCulling : BoolProperty(
	   name = "Use Backface Culling",
	   description = "Enables backface culling on materials. May improve Blender's performance on high poly meshes.\nBackface culling will only be enabled on materials without the two sided flag",
	   default = False)

	reloadCachedTextures : BoolProperty(
	   name = "Reload Cached Textures",
	   description = "Convert all textures again instead of reading from already converted textures. Use this if you make changes to textures and need to reload them",
	   default = True)
	
class MDFFlagsPropertyGroup(bpy.types.PropertyGroup):
	hideMaterialInGame: BoolProperty(
		name = "Hide Material In Game",
		description="If checked, any meshes using this material will appear invisible in game.\nThis works by making the game unable to find the material hash.\nUsing this option is not recommended. Deleting the materials from the MDF and the objects using them in the mesh files is the correct way to do this.\nOnly use this if there's some reason you can't alter the mesh file.",
		default = False
	)
	ver32Unknown: IntProperty(#Unused, kept for backwards compatibility with saved blend files
		name = "Version 31 Unknown",
		description="Unknown value for version 31 and above, likely flags",#TODO Add description
		default = -1
		)
	ver32Unknown1: IntProperty(#Unused, kept for backwards compatibility with saved blend files
		name = "Version 31 Unknown 2",
		description="Unknown value for version 31 and above, likely flags",#TODO Add description
		default = -1
		)
	ver32Unknown2: IntProperty(#Unused, kept for backwards compatibility with saved blend files
		name = "Version 31 Unknown 3",
		description="Unknown value for version 31 and above, likely flags",#TODO Add description
		default = -1
		)
	flagIntValue: IntProperty(
		name = "Bit Flag A",
		description="Integer representation of all flag values.\nChanging this will change all of the flag values",
		update = update_FlagsFromInt
		)
	flagIntValueB: IntProperty(
		name = "Bit Flag B",
		description="Extended bit flag for SF6 (version 31) and newer.\nChanging this will change all of the extended flag values",
		update = update_FlagsFromInt
		)
	shaderLODNum: IntProperty(#SF6 or newer
		name = "Shader LOD Num",
		description="Do not change this unless you know what you're doing. Controls MMTRS LODs.",
		default = 0
		)
	bakeTextureArraySize: IntProperty(#SF6 or newer
		name = "Bake Texture Array Size",
		description="Do not change this unless you know what you're doing.",
		default = 0
		)
	internal_changingFlagValues: BoolProperty(
		name = "Change Flag Values",
		description="This value is inaccessible by the user, it is used to determine whether the user changed a value or an update function did so that an infinite loop doesn't happen",
		default = False,
		)
#Generate the flag properties from the ctypes bit-field definitions in file_re_mdf so the
#file format stays the single source of truth (update_FlagsFromInt/update_IntFromFlags already
#iterate the same _fields_). Each 1-bit field becomes a BoolProperty, wider fields an
#IntProperty with the matching range.
_FLAG_DESCRIPTION_OVERRIDES = {
	#display name and description overrides for flags whose UI text differs from the field name
	"TransparentZPostPassEnable": ("TransparentZPostPassEnable*",
				"*SF6 (version 31) or newer only, this bit is a part of TessFactor on earlier versions"),
	}
_SF6_ONLY_DESCRIPTION = "*SF6 (version 31) or newer only"

for _bitStruct,_sf6Only in ((MDFFlags_bits,False),(MDFFlagsB_bits,True)):
	for _fieldName,_fieldType,_bitWidth in _bitStruct._fields_:
		if _fieldName in _FLAG_DESCRIPTION_OVERRIDES:
			_displayName,_description = _FLAG_DESCRIPTION_OVERRIDES[_fieldName]
		elif _sf6Only:
			_displayName,_description = _fieldName,_SF6_ONLY_DESCRIPTION
		else:
			_displayName,_description = _fieldName,""
		if _bitWidth == 1:
			MDFFlagsPropertyGroup.__annotations__[_fieldName] = BoolProperty(
				name = _displayName,
				description = _description,
				update = update_IntFromFlags
				)
		else:
			MDFFlagsPropertyGroup.__annotations__[_fieldName] = IntProperty(
				name = _fieldName,
				description = "",
				min = 0,
				max = (1 << _bitWidth) - 1,
				update = update_IntFromFlags
				)



	
class MDFPropPropertyGroup(bpy.types.PropertyGroup):
    prop_name: bpy.props.StringProperty(
        name="",
    )
    data_type: bpy.props.EnumProperty(
        items=[
            ('FLOAT', 'Float', 'Float'),
            ('BOOL', 'Bool', 'Bool'),
            ('VEC4', 'Vec4', 'Vec4'),
            ('COLOR', 'COLOR', 'COLOR'),
        ],
        name="Data Type",
    )
    float_vector_value: bpy.props.FloatVectorProperty(
        name="",
        size=4,
		update = update_materialNodes
    )
    float_value: bpy.props.FloatProperty(
        name="",
		update = update_materialNodes
    )
    bool_value: bpy.props.BoolProperty(
        name="",
        default=False,
		update = update_materialNodes
    )
    color_value: bpy.props.FloatVectorProperty(
        name="",
        subtype="COLOR" if bpy.app.version < (5,0,0) else "COLOR_GAMMA",
        size=4,
        min=0.0,
        soft_max=1.0,
		update = update_materialNodes
    )
    padding: bpy.props.IntProperty(#Not exposed in editor, used for SF6's weird mmtrs padding
        name="",
        default=0
    )
    frontPadding: bpy.props.IntProperty(#Not exposed in editor, used for SF6's weird mmtrs padding
        name="",
        default=0
    )
class MDFTextureBindingPropertyGroup(bpy.types.PropertyGroup):
    textureType: StringProperty(
        name="",
    )
    path: StringProperty(
        name="",
    )

class MDFMMTRSIndexPropertyGroup(bpy.types.PropertyGroup):
    indexString: StringProperty(
        name="Indices",
    )
class MDFGPBFDataPropertyGroup(bpy.types.PropertyGroup):
    gpbfDataString: StringProperty(
        name="GPBF Data",
    )	

class MESH_UL_MDFPropertyList(bpy.types.UIList):
	
	filterString: StringProperty(
		name = "Filter",
		description = "Search the list for items that contain this string.\nPress enter to search",
		default='',
		update = update_listFilter)
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # Display the properties of each item in the UIList
        #col1.label(text=item.name)
		layout.ui_units_y = 1.4
		split = layout.split(factor=0.48)
		col1 = split.column()
		col2 = split.column()
		row = col2.row()
		col2.alignment='RIGHT'
		col1.label(text = item.prop_name,icon = "MODIFIER" if item.prop_name in editablePropsSet else "NONE")
		if item.data_type == 'VEC4':
			row.prop(item, "float_vector_value")
		elif item.data_type == 'FLOAT':
			row.prop(item, "float_value")
		elif item.data_type == 'BOOL':
			row.prop(item, "bool_value")
		elif item.data_type == 'COLOR':
			row.prop(item, "color_value")
	# Disable double-click to rename
	def invoke(self, context, event):
		return {'PASS_THROUGH'}
	def draw_filter(self, context, layout):
		col = layout.column(align=True)
		row = col.row(align=True)
		row.prop(self, 'filterString', text='', icon='VIEWZOOM')
	def filter_items(self, context, data, propname):
		"""Filter and order items in the list.""" 
		filtered = []
		ordered = []
		items = getattr(data, propname)
		
		# Initialize with all items visible
		if self.filterString:
			filtered = [self.bitflag_filter_item] * len(items)
			for i, item in enumerate(items):
				if self.filterString.lower() not in item.prop_name.lower():
					filtered[i] &= ~self.bitflag_filter_item
		return filtered, ordered

class MESH_UL_MDFTextureBindingList(bpy.types.UIList):
	
	filterString: StringProperty(
		name = "Filter",
		description = "Search the list for items that contain this string.\nPress enter to search",
		default='',
		update = update_listFilter)
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # Display the properties of each item in the UIList
        #col1.label(text=item.name)
		layout.ui_units_y = 1.4
		split = layout.split(factor=0.35)
		col1 = split.column()
		col2 = split.column()
		row = col2.row()
		col2.alignment='RIGHT'
		col1.label(text = item.textureType)
		row.prop(item,"path")
	# Disable double-click to rename
	def invoke(self, context, event):
		return {'PASS_THROUGH'}
	def draw_filter(self, context, layout):
		col = layout.column(align=True)
		row = col.row(align=True)
		row.prop(self, 'filterString', text='', icon='VIEWZOOM')
	def filter_items(self, context, data, propname):
		"""Filter and order items in the list.""" 
		filtered = []
		ordered = []
		items = getattr(data, propname)
		
		# Initialize with all items visible
		if self.filterString:
			filtered = [self.bitflag_filter_item] * len(items)
			for i, item in enumerate(items):
				if self.filterString.lower() not in item.textureType.lower():
					filtered[i] &= ~self.bitflag_filter_item
		return filtered, ordered

class MESH_UL_MDFMMTRSDataList(bpy.types.UIList):
	
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # Display the properties of each item in the UIList
        #col1.label(text=item.name)
		layout.ui_units_y = 1.4
		split = layout.split(factor=0.35)
		col1 = split.column()
		col2 = split.column()
		row = col2.row()
		col2.alignment='RIGHT'
		#col1.label(text = item.textureType)
		layout.prop(item,"indexString")
	# Disable double-click to rename
	def invoke(self, context, event):
		return {'PASS_THROUGH'}

class MESH_UL_MDFGPBFDataList(bpy.types.UIList):
	
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # Display the properties of each item in the UIList
        #col1.label(text=item.name)
		layout.ui_units_y = 1.4
		split = layout.split(factor=0.35)
		col1 = split.column()
		col2 = split.column()
		row = col2.row()
		col2.alignment='RIGHT'
		#col1.label(text = item.textureType)
		layout.prop(item,"gpbfDataString")
	# Disable double-click to rename
	def invoke(self, context, event):
		return {'PASS_THROUGH'}

class MDFMaterialPropertyGroup(bpy.types.PropertyGroup):
	gameName:StringProperty()
	
	materialName: StringProperty(
		name = "Material Name",
		description="The name of the MDF material. The material names must match on both the mesh and mdf file.\nIf the material names and number of materials do not match in the mesh and mdf, you will get a checkerboard texture in game",
		update=update_materialName
		)
	mmtrPath: StringProperty(
		name = "Master Material Path",
		description="Do not change this unless you know what you're doing",
		)
	shaderType: EnumProperty(
		name="Material Shader Type",
		description="Set shader type. Note that these values are from MH Wilds and may display incorrectly on different games",
		items=[ ("0", "Standard (0)", ""),
				("1", "Decal (1)", ""),
				("2", "DecalStencil (2)", ""),
				("3", "SeparateAlphaDecal (3)", ""),
				("4", "DecalWithMetallic (4)", ""),
				("5", "DecalNRMR (5)", ""),
				("6", "Transparent (6)", ""),
				("7", "TransparentStencil (7)", ""),
				("8", "Distortion (8)", ""),
				("9", "PrimitiveMesh (9)", ""),
				("10", "PrimitiveSolidMesh (10)", ""),
				("11", "Water (11)", ""),
				("12", "SpeedTree (12)", ""),
				("13", "GUI (13)", ""),
				("14", "GUIMesh (14)", ""),
				("15", "GUIMeshTransparent (15)", ""),
				("16", "ExpensiveTransparent (16)", ""),
				("17", "Forward (17)", ""),
				("18", "RenderTarget (18)", ""),
				("19", "PostProcess (19)", ""),
				("20", "PrimitiveMaterial (20)", ""),
				("21", "PrimitiveSolidMaterial (21)", ""),
				("22", "PrimitiveSolidMaterialExpensive (22)", ""),
				("23", "SpineMaterial (23)", ""),
				("24", "VolumetricFog (24)", ""),
				("25", "ShellFurMaterial (25)", ""),
				("26", "VolumeDecal (26)", ""),
				("27", "AlembicMesh (27)", ""),
				("28", "AlembicMeshForward (28)", ""),
				("29", "AlembicMeshTransparent (29)", ""),
				("30", "MarchingCubes (30)", ""),
				("31", "MarchingCubesForward (31)", ""),
				("32", "MarchingCubesTransparent (32)", ""),
				("33", "Strands (33)", ""),
				("34", "Eyeball (34)", ""),
				("35", "EyeballPostProcess (35)", ""),
				("36", "NFXTransparent (36)", ""),
				("37", "Cloudscape2 (37)", ""),
				("38", "CloudscapeReserved (38)", ""),
				("39", "VolumeSolidMaterial (39)", ""),
				("40", "VolumeDecalMetallic (40)", ""),
				
			   ]
		)
	linkedMaterial: bpy.props.PointerProperty(
        name="Linked Material",
		description="The Blender material that corresponds to this MDF material. Any changes made to supported MDF properties will reflect on the Blender material.\nIf a linked material is not set, it will be set automatically once an MDF property is changed",
        type=bpy.types.Material,
    )
	flags:PointerProperty(type = MDFFlagsPropertyGroup)
	propertyList_items: CollectionProperty(type=MDFPropPropertyGroup)
	propertyList_index: IntProperty(name="")
	
	textureBindingList_items: CollectionProperty(type=MDFTextureBindingPropertyGroup)
	textureBindingList_index: IntProperty(name="")
	
	mmtrsData_items: CollectionProperty(type = MDFMMTRSIndexPropertyGroup)
	mmtrsData_index: IntProperty(name="")
	
	gpbfData_items: CollectionProperty(type = MDFGPBFDataPropertyGroup)
	gpbfData_index: IntProperty(name="")