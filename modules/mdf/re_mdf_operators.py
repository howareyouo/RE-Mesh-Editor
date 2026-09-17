	#Author: NSA Cloud
import bpy
import os
import json

from bpy.types import Operator, PropertyGroup
from bpy.props import CollectionProperty, IntProperty, StringProperty, BoolProperty
from ..gen_functions import raiseWarning,openFolder,PRESET_DIR
from ..blender_utils import showErrorMessageBox,createEmpty
from .blender_re_mdf import reindexMaterials,createMDFCollection,checkNameUsage,buildMDF,fixTexPath,iterMDFMaterialObjects
from .blender_re_mesh_mdf import importMDF
from .ui_re_mdf_panels import tag_redraw
from .re_mdf_presets import saveAsPreset,readPresetJSON
from .file_re_mdf import getMDFVersionToGameName

class WM_OT_NewMDFHeader(Operator):
	bl_label = "Create MDF Collection"
	bl_idname = "re_mdf.create_mdf_collection"
	bl_options = {'UNDO'}
	bl_description = "Create an MDF collection for putting materials into.\nNOTE: The name of the collection is not important, you can rename it if you want to"
	collectionName : bpy.props.StringProperty(
		name = "MDF Name",
		description = "The name of the newly created mdf collection.\nUse the same name as the mesh file",
		default = "newMDF"
	)
	def execute(self, context):
		if self.collectionName.strip() != "":
			createMDFCollection(self.collectionName.strip()+".mdf2")
			self.report({"INFO"},"Created new RE MDF collection.")
			return {'FINISHED'}
		else:
			self.report({"ERROR"},"Invalid MDF collection name.")
			return {'CANCELLED'}
	
	def invoke(self,context,event):
		return context.window_manager.invoke_props_dialog(self)

class WM_OT_ReindexMaterials(Operator):
	bl_label = "Reindex Materials"
	bl_description = "Reorders the materials and sets their names to the name set in the custom properties. This is done automatically upon exporting"
	bl_idname = "re_mdf.reindex_materials"

	@classmethod
	def poll(self,context):
		return context.scene.re_mdf_toolpanel.mdfCollection is not None

	def execute(self, context):
		reindexMaterials(bpy.context.scene.re_mdf_toolpanel.mdfCollection)
		self.report({"INFO"},"Reindexed materials.")
		return {'FINISHED'}

class WM_OT_AddPresetMaterial(Operator):
	bl_label = "Add Preset Material"
	bl_description = "Add a new material to the file and configure it to the selected preset"
	bl_idname = "re_mdf.add_preset_material"
	bl_options = {'UNDO'}
	
	@classmethod
	def poll(self,context):
		return context.scene.re_mdf_toolpanel.mdfCollection is not None
	
	def execute(self, context): 
		enumValue = bpy.context.scene.re_mdf_toolpanel.materialPresets

		if enumValue != "":
			finished = readPresetJSON(os.path.join(PRESET_DIR,enumValue))
		else:
			finished = False
		tag_redraw(bpy.context)
		if finished:
			self.report({"INFO"},"Added preset material.")
			return {'FINISHED'}
		else:
			return {'CANCELLED'}
		
class WM_OT_ApplyMDFToMeshCollection(Operator):
	bl_label = "Apply Active MDF"
	bl_description = "Applies the Active MDF Collection to the specified Mesh Collection.\nThis will remove all materials on the mesh and rebuild them using the active MDF.\nTextures will be fetched from the chunk path set in the addon preferences"
	bl_idname = "re_mdf.apply_mdf"

	@classmethod
	def poll(self,context):
		return context.scene.re_mdf_toolpanel.mdfCollection is not None and context.scene.re_mdf_toolpanel.meshCollection is not None

	def execute(self, context):
		#reindexMaterials()
		mdfCollection = bpy.context.scene.re_mdf_toolpanel.mdfCollection
		meshCollection = bpy.context.scene.re_mdf_toolpanel.meshCollection
		modDir = os.path.realpath(bpy.context.scene.re_mdf_toolpanel.modDirectory)
		#removedMaterialSet = set()
		if mdfCollection != None and meshCollection != None and os.path.isdir(modDir):
			mdfFile = buildMDF(mdfCollection.name)
			meshMaterialDict = dict()
			for obj in meshCollection.all_objects:
				if obj.type == "MESH" and not obj.get("MeshExportExclude"):
					materialName = None
					#Fix UV map naming so materials work properly on non RE meshes
					if len(obj.data.uv_layers) > 0:
						obj.data.uv_layers[0].name = "UVMap0"
						if len(obj.data.uv_layers) > 1:
							obj.data.uv_layers[1].name = "UVMap1"
					if "__" in obj.name:
						materialName = obj.name.split("__",1)[1].split(".")[0]
						for material in obj.data.materials:
							if material.name.split(".")[0] == materialName:
								meshMaterialDict[materialName] = material
							#removedMaterialSet.add(material)
					obj.data.materials.clear()
					if materialName not in meshMaterialDict:
						if materialName:
							newMat = bpy.data.materials.new(name=materialName)
							newMat.use_nodes = True
							obj.data.materials.append(newMat)
							meshMaterialDict[materialName] = newMat
						else:
							raiseWarning(f"No material in mesh name, cannot apply materials: {obj.name}")
					else:
						obj.data.materials.append(meshMaterialDict[materialName])
			"""						
			#If the removed materials have no more users, remove them
			for material in removedMaterialSet:
				if material.users == 0:
					print(f"Removed {material.name}")
					bpy.data.materials.remove(material)
			"""
			importMDF(mdfFile, meshMaterialDict,bpy.context.scene.re_mdf_toolpanel.loadUnusedTextures,bpy.context.scene.re_mdf_toolpanel.loadUnusedProps,bpy.context.scene.re_mdf_toolpanel.useBackfaceCulling,bpy.context.scene.re_mdf_toolpanel.reloadCachedTextures,chunkPath = modDir,gameName = bpy.context.scene.re_mdf_toolpanel.activeGame,arrangeNodes = True)
			self.report({"INFO"},"Applied MDF to mesh collection.")
		else:
			self.report({"ERROR"},"Invalid mesh or MDF collection.")
		return {'FINISHED'}
	
class WM_OT_OpenPresetFolder(Operator):
	bl_label = "Open Preset Folder"
	bl_description = "Opens the preset folder in File Explorer"
	bl_idname = "re_mdf.open_preset_folder"

	def execute(self, context):
		openFolder(PRESET_DIR)
		return {'FINISHED'}

class WM_OT_SavePreset(Operator):
	bl_label = "Save Selected As Preset"
	bl_idname = "re_mdf.save_selected_as_preset"
	bl_context = "objectmode"
	bl_description = "Save the selected material object as a preset for easy reuse and sharing. Presets can be accessed using the Open Preset Folder button"
	presetName : bpy.props.StringProperty(name = "Enter Preset Name",default = "newPreset")
	
	@classmethod
	def poll(self,context):
		return context.active_object is not None
	
	def execute(self, context):
		gameName = bpy.context.scene.re_mdf_toolpanel.activeGame
		finished = saveAsPreset(context.active_object, self.presetName,gameName)
		if finished:
			self.report({"INFO"},"Saved preset.")
			return {'FINISHED'}
		else:
			return {'CANCELLED'}
	def invoke(self,context,event):
		return context.window_manager.invoke_props_dialog(self)
	
def update_findReplaceCount(self, context):
	frp = context.window_manager.re_mdf_findreplace
	replaceCount = 0
	findValues = [item.findValue for item in frp.findReplaceList if item.findValue]
	if not findValues:
		frp.instanceCount = 0
		return
	if frp.replaceAllMaterials:
		for obj in bpy.data.objects:
			if obj.get("~TYPE") == "RE_MDF_MATERIAL":
				mat = obj.re_mdf_material
				for entry in mat.textureBindingList_items:
					for fv in findValues:
						replaceCount += entry.path.count(fv)
	else:
		if context.active_object and context.active_object.get("~TYPE") == "RE_MDF_MATERIAL":
			material = context.active_object.re_mdf_material
			for entry in material.textureBindingList_items:
				for fv in findValues:
					replaceCount += entry.path.count(fv)
	frp.instanceCount = replaceCount

class FindReplaceItem(PropertyGroup):
	findValue : StringProperty(name = "Find",update = update_findReplaceCount)
	replaceValue : StringProperty(name = "Replace With")

class FindReplacePropertyGroup(PropertyGroup):
	findReplaceList : CollectionProperty(type = FindReplaceItem)
	instanceCount : IntProperty(name = "Count",default = 0)
	replaceAllMaterials : BoolProperty(name = "Replace in all MDF materials",default = False,update = update_findReplaceCount)

class WM_OT_FindReplaceTextureBindings(Operator):
	bl_label = "Find and Replace"
	bl_idname = "re_mdf.replace_texture_bindings"
	bl_context = "objectmode"
	bl_description = "Find and replace specified strings inside texture paths"
	bl_options = {'UNDO'}
	@classmethod
	def poll(self,context):
		return context.active_object is not None
	
	def execute(self, context):
		replaceCount = 0
		frp = bpy.context.window_manager.re_mdf_findreplace
		frl = frp.findReplaceList
		findReplacePairs = [(item.findValue, item.replaceValue) for item in frl if item.findValue]
		if not findReplacePairs:
			return {'CANCELLED'}
		if frp.replaceAllMaterials:
			for obj in bpy.data.objects:
				if obj.get("~TYPE") == "RE_MDF_MATERIAL":
					material = obj.re_mdf_material
					for entry in material.textureBindingList_items:
						for findVal, replaceVal in findReplacePairs:
							old_count = entry.path.count(findVal)
							entry.path = entry.path.replace(findVal, replaceVal)
							replaceCount += old_count
		elif context.active_object and context.active_object.get("~TYPE") == "RE_MDF_MATERIAL":
			material = context.active_object.re_mdf_material
			for entry in material.textureBindingList_items:
				for findVal, replaceVal in findReplacePairs:
					replaceCount += entry.path.count(findVal)
					entry.path = entry.path.replace(findVal,replaceVal)
		else:
			return {'CANCELLED'}
		self.report({"INFO"},f"Replaced {replaceCount} instances.")
		return {'FINISHED'}
	
	def draw(self,context):
		layout = self.layout
		frp = bpy.context.window_manager.re_mdf_findreplace
		row = layout.row(align=True)
		split = row.split(factor=0.5)
		split.prop(frp,"replaceAllMaterials")
		row2 = split.row(align=True)
		row2.alignment = 'RIGHT'
		row2.label(text=f"{frp.instanceCount} instances found.")
		layout.separator()
		frl = frp.findReplaceList
		for i, item in enumerate(frl):
			row = layout.row(align=False)
			split = row.split(factor=0.95)
			split2 = split.split(factor=0.5)
			split2.prop(item,"findValue",text = "")
			split2.prop(item,"replaceValue",text = "")
			if i == 0:
				split.operator(WM_OT_FindReplaceAddItem.bl_idname,text="",icon='ADD')
			else:
				split.operator(WM_OT_FindReplaceRemoveItem.bl_idname,text="",icon='TRASH')
			layout.separator()
	
	def invoke(self,context,event):
		frl = bpy.context.window_manager.re_mdf_findreplace.findReplaceList
		if len(frl) == 0:
			frl.add()
		return context.window_manager.invoke_props_dialog(self,width = 400)

class WM_OT_FindReplaceAddItem(Operator):
	bl_label = "Add"
	bl_idname = "re_mdf.find_replace_add_item"
	bl_description = "Add entry"
	bl_options = {'UNDO'}
	def execute(self, context):
		frl = context.window_manager.re_mdf_findreplace.findReplaceList
		frl.add()
		return {'FINISHED'}

class WM_OT_FindReplaceRemoveItem(Operator):
	bl_label = "Remove"
	bl_idname = "re_mdf.find_replace_remove_item"
	bl_description = "Remove entry"
	bl_options = {'UNDO'}
	def execute(self, context):
		frl = context.window_manager.re_mdf_findreplace.findReplaceList
		if len(frl) > 0:
			frl.remove(len(frl)-1)
		return {'FINISHED'}

class WM_OT_NullifyTextureBindings(Operator):
	bl_label = "Nullify Texture Bindings"
	bl_idname = "re_mdf.nullify_texture_bindings"
	bl_context = "objectmode"
	bl_description = "Replaces the texture bindings of all selected materials with null paths.\nThis is useful for when you want to assign your own textures to a material, but don't know what every texture map does.\nExperimental. May not work correctly for all materials"
	bl_options = {'UNDO'}

	@classmethod
	def poll(self,context):
		return context.active_object is not None
	
	def execute(self, context):
		jsonPath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"workspace","texturepacker","tex_bindings_null.json")
		try:
			with open(jsonPath,"r", encoding ="utf-8") as file:
				texTypeDict = json.load(file)
				print(f"Loaded {jsonPath}")
		except Exception as err:
			print(f"Failed to load {jsonPath} - {err}")
		replaceCount = 0
		for obj in bpy.context.selected_objects:
			if obj.get("~TYPE") == "RE_MDF_MATERIAL":
				material = obj.re_mdf_material
				if material.gameName != "":
					gameName = material.gameName
				else:
					print(f"Game name not found on {material.materialName} falling back to active game")
					gameName = bpy.context.scene.re_mdf_toolpanel.activeGame
				for entry in material.textureBindingList_items:
					if entry.textureType in texTypeDict:
						if gameName in texTypeDict[entry.textureType]:
							entry.path = texTypeDict[entry.textureType][gameName]
							replaceCount += 1
						elif "generic" in texTypeDict[entry.textureType]:
							entry.path = texTypeDict[entry.textureType]["generic"]
							replaceCount += 1
						else:
							print(f"Unknown texture type {entry.textureType} on {material.materialName}, skipping.")
		if replaceCount > 0:
			self.report({"INFO"},f"Replaced {replaceCount} texture bindings.")
			return {'FINISHED'}
		else:
			return {'CANCELLED'}

class WM_OT_ClearUnusedTextures(Operator):
	bl_label = "Clear Unused Textures"
	bl_idname = "re_mdf.clear_unused_textures"
	bl_context = "objectmode"
	bl_description = "Moves .tex files in the same folder as the imported MDF that are not referenced by the MDF into a _unused subfolder"
	@classmethod
	def poll(self,context):
		return context.scene.re_mdf_toolpanel.mdfCollection is not None

	def getTexBaseName(self,fname):
		#Get the .tex base name of a texture file, e.g. "foo.tex" from "foo.tex.123" or "foo.tex.123.pc".
		#Returns None for files that are not texture files (".rtex", ".txt", etc)
		lowerName = fname.lower()
		index = lowerName.find(".tex")
		if index <= 0:#Ignore junk names like ".tex"
			return None
		suffix = lowerName[index+4:]
		if suffix != "" and not suffix.startswith("."):
			return None
		return fname[:index+4]

	def getUnusedTextures(self):
		#Returns (mdfDir, unusedFileList), or (None,None) if the MDF file path was not stored on the collection or the folder no longer exists
		mdfCollection = bpy.context.scene.re_mdf_toolpanel.mdfCollection
		mdfPath = mdfCollection.get("~MDFFILEPATH",None)
		if mdfPath == None:
			return (None,None)
		mdfDir = os.path.dirname(mdfPath)
		try:
			fnameList = os.listdir(mdfDir)
		except OSError as err:
			print(f"Could not list MDF folder {mdfDir}: {err}")
			return (None,None)
		referencedSet = set()
		for materialObj in iterMDFMaterialObjects(mdfCollection):
			for binding in materialObj.re_mdf_material.textureBindingList_items:
				refBase = os.path.basename(fixTexPath(binding.path)).replace("@","").lower()
				if refBase.endswith(".tex"):
					referencedSet.add(refBase)
		unusedList = []
		for fname in fnameList:
			filePath = os.path.join(mdfDir,fname)
			if not os.path.isfile(filePath):
				continue
			texBase = self.getTexBaseName(fname)
			if texBase != None and texBase.lower() not in referencedSet:
				unusedList.append(filePath)
		return (mdfDir,unusedList)

	def execute(self, context):
		mdfDir, unusedList = self.getUnusedTextures()
		if unusedList == None:
			self.report({"ERROR"},"The MDF file path was not found on the collection or the folder no longer exists. Re-import the MDF to enable this feature.")
			return {'CANCELLED'}
		if len(unusedList) == 0:
			self.report({"INFO"},"No unused textures found.")
			return {'CANCELLED'}
		unusedDir = os.path.join(mdfDir,"_unused")
		try:
			os.makedirs(unusedDir,exist_ok = True)
		except Exception as err:
			self.report({"ERROR"},"Could not create the _unused folder: "+str(err))
			return {'CANCELLED'}
		movedCount = 0
		for filePath in unusedList:
			fileName = os.path.basename(filePath)
			destPath = os.path.join(unusedDir,fileName)
			suffixIndex = 1
			while os.path.exists(destPath):
				name, ext = os.path.splitext(fileName)
				destPath = os.path.join(unusedDir,f"{name} ({suffixIndex}){ext}")
				suffixIndex += 1
			try:
				os.rename(filePath,destPath)
				movedCount += 1
			except Exception as err:
				print(f"Failed to move {filePath} to _unused: {err}")
		self.report({"INFO"},f"Moved {movedCount} unused textures to {unusedDir}")
		return {'FINISHED'}

	def draw(self,context):
		layout = self.layout
		layout.label(text = f"Move {self.unusedCount} unused .tex file(s) to the _unused subfolder?")
		if getattr(self,"otherMDFCount",0) > 0:
			layout.label(text = f"Warning: {self.otherMDFCount} other MDF file(s) found in the same folder. Textures used by those MDFs will also be moved.",icon = "ERROR")

	def invoke(self, context, event):
		mdfDir, unusedList = self.getUnusedTextures()
		if unusedList == None:
			self.report({"ERROR"},"The MDF file path was not found on the collection or the folder no longer exists. Re-import the MDF to enable this feature.")
			return {'CANCELLED'}
		if len(unusedList) == 0:
			self.report({"INFO"},"No unused textures found.")
			return {'CANCELLED'}
		self.unusedCount = len(unusedList)
		otherMDFCount = 0
		for fname in os.listdir(mdfDir):
			if os.path.isfile(os.path.join(mdfDir,fname)) and ".mdf2" in fname.lower():
				otherMDFCount += 1
		self.otherMDFCount = otherMDFCount - 1#Exclude the active MDF itself
		return context.window_manager.invoke_props_dialog(self)
