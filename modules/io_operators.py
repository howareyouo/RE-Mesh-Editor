#Author: NSA Cloud
#Import/export operators for every asset type (RE Mesh, MDF, FBXSkel, SFur), split out
#of __init__.py. The shared import drivers, file-browser sync helpers and export-path
#resolution used by those operators live here as well.

import bpy
import os
from bpy.props import StringProperty, BoolProperty, IntProperty, EnumProperty, CollectionProperty
from bpy.types import Operator, OperatorFileListElement
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .. import bl_info  # defined at the top of __init__.py, which imports this module
from .gen_functions import parseFileVersion, raiseWarning, splitNativesPath
from .blender_utils import (printEditorHeader, isMDFCollection, isMeshCollection, isSFurCollection,
                            findMDFCollectionForMesh, getMeshMaterialNames, resolveSourcePath,
                            getAddonPreferences)
from .game_versions import mdfVersionDict
from .mesh.file_re_mesh import meshFileVersionToGameNameDict
from .mesh.blender_re_mesh import importREMeshFile, exportREMeshFile
from .mdf.file_re_mdf import gameNameMDFVersionDict
from .mdf.blender_re_mdf import importMDFFile, exportMDFFile
from .fbxskel.blender_re_fbxskel import importFBXSkelFile, exportFBXSkelFile
from .sfur.blender_re_sfur import importSFurFile, exportSFurFile


def setModDirectoryFromFilePath(filePath):
	if "re_chunk_000" not in filePath and "re_dlc_stm" not in filePath and "natives" in filePath:
		try:
			bpy.context.scene.re_mdf_toolpanel.modDirectory = splitNativesPath(filePath)[0]
			print(f"Set mod directory to {bpy.context.scene.re_mdf_toolpanel.modDirectory}")
		except:
			print("ERROR: Failed to set mod directory, exported file path probably does not follow the chunk naming scheme.")
def applySourceDirectory(collection, sourcePathKey, filepath):
	"""Point filepath at the folder this collection was imported from."""
	sourcePath = resolveSourcePath(collection.get(sourcePathKey, "")) if collection != None else ""
	if sourcePath != "":
		return os.path.join(os.path.dirname(sourcePath), os.path.basename(filepath))
	return filepath


def toggleConsole():
	"""Toggle the system console, if the build has one and the user wants it."""
	if not bpy.app.background and getAddonPreferences().showConsole:
		try:
			bpy.ops.wm.console_toggle()
		except:
			pass


def remapLegacySceneVersion(scene, propertyName):
	"""Map mesh versions that were superseded by a later build onto their
	replacement, so a stale scene property still exports a valid file."""
	versionMap = {
		231011879: 240423143,  #DD2 version update fix
		2102020001: 2101050001,  #Remap RE Verse to RE8
		240820143: 241111606,  #Remap MH Wilds beta to full release
		}
	version = scene.get(propertyName, 0)
	if version in versionMap:
		scene[propertyName] = versionMap[version]


def importFileList(self, importFunc, formatLabel, logLabel = None):
	"""Multi-file import driver shared by the list based importers.
	Reports the same way for every format; returns False when a file failed."""
	multiFileImport = len(self.files) > 1
	hasImportErrors = False
	for index, file in enumerate(self.files):
		filepath = os.path.join(self.directory,file.name)
		if logLabel != None and multiFileImport:
			print(f"Multi {logLabel} Import ({index+1}/{len(self.files)})")
		if os.path.isfile(filepath):
			if not importFunc(filepath):
				hasImportErrors = True
		else:
			hasImportErrors = True
			raiseWarning(f"Path does not exist, cannot import file. If you are importing multiple files at once, they must all be in the same directory.\nInvalid Path:{filepath}")

	if hasImportErrors:
		self.report({"INFO"},f"Failed to import RE {formatLabel}. Check the console for errors.")
		return False
	if not multiFileImport:
		self.report({"INFO"},f"Imported RE {formatLabel} file.")
	else:
		self.report({"INFO"},f"Imported {str(len(self.files))} RE {formatLabel} files.")
	return True


def _drawInvalidCollection(layout, message):
	row = layout.row()
	row.alert=True
	row.label(icon = "ERROR",text=message)


def drawCollectionWarning(layout, self, attributeName, validator, invalidMessage):
	"""Red warning shared by the export operators when the picked collection
	doesn't exist or isn't the right kind."""
	collectionName = getattr(self, attributeName)
	if collectionName in bpy.data.collections:
		if not validator(bpy.data.collections[collectionName]):
			_drawInvalidCollection(layout, invalidMessage)
	elif collectionName == "":
		_drawInvalidCollection(layout, invalidMessage)
	else:
		row = layout.row()
		row.label(icon="ERROR",text="Chosen collection doesn't exist.")
		row.alert=True


class ImportREMesh(Operator, ImportHelper):
	'''Import RE Engine Mesh File'''
	bl_idname = "re_mesh.importfile"
	bl_label = "Import RE Mesh"
	bl_options = {'PRESET', "REGISTER", "UNDO"}
	files : CollectionProperty(
			name="File Path",
			type=OperatorFileListElement,
			)
	directory : StringProperty(
			subtype='DIR_PATH',
			options={'SKIP_SAVE'}
			)
	filename_ext = ".mesh.*"
	filter_glob: StringProperty(default="*.mesh.*", options={'HIDDEN'})
	clearScene : BoolProperty(
	   name = "Clear Scene",
	   description = "Clears all objects before importing the mesh file",
	   default = False)
	loadMaterials : BoolProperty(
	   name = "Load Materials",
	   description = "Load materials from the MDF2 file. This may increase the time the mesh takes to import",
	   default = False)
	loadMDFData : BoolProperty(
	   name = "Load MDF Material Data",
	   description = "Imports the MDF materials as objects inside a collection in the outliner.\nYou can make changes to MDF materials by selecting the Material objects in the outliner.\nUnder the Object Properties tab (orange square), there's a panel called \"RE MDF Material Settings\".\nMake any changes to MDF materials there.\nIf you're not modding an RE Engine game, you can uncheck this option since it won't be needed",
	   default = True)
	loadShellFur : BoolProperty(
	   name = "Load Shell Fur",
	   description = "Load shell fur data from sfur file if present",
	   default = False)
	sf6CmdIndex : IntProperty(
	   name = "SF6 Color Index",
	   description = "CMD color file index. For example, 0 loads cmd_000 and 2 loads cmd_002. Leave to 1 to import SF6 Base Color outfit.",
	   default = 0,
	   min = 0,
	   max = 999)
	sf6CostumeIndex : IntProperty(
	   name = "SF6 Costume Index",
	   description = "Costume folder containing the CMD Files. For example, 1 uses costume folder 001. Shared meshes under folder 000 can use this to load the selected costume colors.",
	   default = 1,
	   min = 0,
	   max = 999)
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
	   default = False)
	mdfPath : StringProperty(
		name = "",
		description = "Manually set the path of the mdf2 file. The MDF is found automatically if this is left blank.\nTip:Hold shift and right click the mdf2 file and click \"Copy as path\", then paste into this field",
		default = "",
		)
	createCollections : BoolProperty(
	   name = "Create Collections",
	   description = "Create a collection for the mesh and for each LOD level. Note that collections are required for exporting LODs and applying MDF changes. Leaving this option enabled is recommended",
	   default = True)
	mergeArmature : StringProperty(
	   name = "",
	   description = "Merges the imported mesh's armature with the selected armature. Leave this blank if not merging with an armature",
	   default = "")
	mergeImportedArmatures : BoolProperty(
	   name = "Merge All New Armatures",
	   description = "If multiple mesh files are selected, merge the armatures of all newly imported mesh files into one",
	   default = False)
	importArmatureOnly : BoolProperty(
	   name = "Only Import Armature",
	   description = "Imports the armature of the mesh file, but not any of the meshes",
	   default = False)
	importAllLODs : BoolProperty(
	   name = "Import All LODs",
	   description = "Imports all LOD (level of detail) meshes. If unchecked, only the first LOD of each mesh will be imported",
	   default = False)
	rotate90 : BoolProperty(
	   name = "Y Up to Z Up",
	   description = "Rotate meshes and armatures by 90 degrees. Leaving this option enabled is recommended",
	   default = True)
	importBlendShapes : BoolProperty(
	   name = "Import Blend Shapes",
	   description = "Imports blend shapes as shape keys if present",
	   default = True)
	importShadowMeshes : BoolProperty(
	   name = "Import Shadow Cast Mesh",
	   description = "Imports shadow cast meshes if present",
	   default = True)
	mergeGroups : BoolProperty(
	   name = "Merge Mesh Groups",
	   description = "Merges all submeshes of a mesh group. IMPORTANT: MERGED MESHES CANNOT BE EXPORTED BACK TO MESH",
	   default = False)
	mergeSameMaterialSubmeshes : BoolProperty(
	   name = "Merge Same Material Submeshes",
	   description = "WARNING: MERGED MESHES LOSE THEIR PER-SUBMESH IDENTITY AND CANNOT BE EXPORTED BACK TO MESH.\nCombines all submeshes that share the same material into one mesh to speed up import. Submeshes with blend shapes, extra weights or DD2 secondary weights are never merged",
	   default = False)
	importOcclusionMeshes : BoolProperty(
	   name = "Import Occlusion Mesh",
	   description = "Imports occlusion meshes if present",
	   default = False)
	importBoundingBoxes : BoolProperty(
	   name = "Import Bounding Boxes",
	   description = "Import mesh and bone bounding boxes for debugging purposes",
	   default = False)
	hideArmature : BoolProperty(
	   name = "Hide Armature",
	   description = "Hides the imported armature in the viewport and render",
	   default = True)
	
	#Internal properties for grouping material settings
	showAdvancedOptions : BoolProperty(
	   name = "Show Advanced Options",
	   default = False)
	showMaterialOptions : BoolProperty(
	   name = "Show Material Options",
	   default = True)

	def isSF6Import(self):
		"""Whether the pending import targets SF6, based on the mesh version in
		each queued file's name. Handles both the .mesh.<version> and the
		.mesh_.<version> (underscore, streaming chunk) naming conventions.
		Files with an unknown version keep the SF6 options visible to preserve
		the previous behavior."""
		if not self.files:
			return True
		for file in self.files:
			version = parseFileVersion(file.name, None)
			gameName = meshFileVersionToGameNameDict.get(version) if version != None else None
			if gameName != None and gameName != "SF6":
				return False
		return True
	
	def draw(self, context):
		layout = self.layout
		row = layout.row()
		row.prop(self, "clearScene")
		row.enabled = self.mergeArmature == ""
		
		#layout.prop(self, "importBlendShapes")
		layout.label(text = "Merge With Armature")
		layout.prop_search(self, "mergeArmature",bpy.data,"armatures")
		
		row = layout.row()
		row.prop(self, "mergeImportedArmatures")
		row.enabled = len(self.files) > 1
		
		row = layout.row()
		icon = 'DOWNARROW_HLT' if self.showMaterialOptions else 'RIGHTARROW'
		row.prop(self, 'showMaterialOptions', icon=icon, icon_only=True)
		row.label(text='Material Settings')
		split = layout.split(factor = 0.01)
		column = split.column()
		column2 = split.column()
		if self.showMaterialOptions:
			column2.prop(self, "loadMaterials")
			column2.prop(self, "loadMDFData")
			column2.prop(self, "loadShellFur")
			if self.isSF6Import():
				column2.prop(self, "sf6CmdIndex")
				column2.prop(self, "sf6CostumeIndex")
			column2.prop(self, "reloadCachedTextures")
			column2.prop(self, "loadUnusedTextures")
			column2.prop(self, "loadUnusedProps")
			column2.prop(self, "useBackfaceCulling")
			column2.label(text = "Manual MDF Path")
			column2.prop(self, "mdfPath")	
		
		row = layout.row()
		icon = 'DOWNARROW_HLT' if self.showAdvancedOptions else 'RIGHTARROW'
		row.prop(self, 'showAdvancedOptions', icon=icon, icon_only=True)
		row.label(text='Advanced Options')
		split = layout.split(factor = 0.01)
		column = split.column()
		column2 = split.column()
		if self.showAdvancedOptions:
			column2.prop(self, "importAllLODs")
			column2.prop(self, "createCollections")
			column2.prop(self, "mergeGroups")
			column2.prop(self, "mergeSameMaterialSubmeshes")
			column2.prop(self, "importArmatureOnly")
		
			column2.prop(self, "rotate90")
			column2.prop(self, "importBoundingBoxes")
			column2.prop(self, "hideArmature")
			column2.prop(self, "importOcclusionMeshes")
		
	def execute(self, context):
		try:
			os.makedirs(bpy.path.abspath(getAddonPreferences().textureCachePath),exist_ok = True)
		except:
			raiseWarning("Could not create texture cache directory at " + getAddonPreferences().textureCachePath)
		if self.mergeArmature:
			self.clearScene = False
		options = {"clearScene":self.clearScene,"createCollections":self.createCollections,"loadMaterials":self.loadMaterials,"loadMDFData":self.loadMDFData,"loadShellFur":self.loadShellFur,"sf6CmdIndex":self.sf6CmdIndex,"sf6CostumeIndex":self.sf6CostumeIndex,"loadUnusedTextures":self.loadUnusedTextures,"loadUnusedProps":self.loadUnusedProps,"useBackfaceCulling":self.useBackfaceCulling,"reloadCachedTextures":self.reloadCachedTextures,"mdfPath":self.mdfPath.replace("\"",""),"importAllLODs":self.importAllLODs,"importBlendShapes":self.importBlendShapes,"rotate90":self.rotate90,"mergeArmature":self.mergeArmature,"importArmatureOnly":self.importArmatureOnly,"mergeGroups":self.mergeGroups,"mergeSameMaterialSubmeshes":self.mergeSameMaterialSubmeshes,"importShadowMeshes":self.importShadowMeshes,"importOcclusionMeshes":self.importOcclusionMeshes,"importBoundingBoxes":self.importBoundingBoxes,"hideArmature":self.hideArmature}
		printEditorHeader(bl_info)
		
		toggleConsole()
		
		multiFileImport = len(self.files) > 1
		hasImportErrors = False
		mergeArmatureName = ""
		mergeArmatureNameIndex = 1
		if multiFileImport and self.mergeImportedArmatures:
			mergeArmatureName = self.files[0].name.split(".mesh")[0] + " Armature"
			baseMergeArmatureName = mergeArmatureName
			while mergeArmatureName in bpy.data.armatures:#Get the name of armature that will be imported, this accounts for if there's already an armature with that name imported
				mergeArmatureName = baseMergeArmatureName + "." + str(mergeArmatureNameIndex).zfill(3)
				mergeArmatureNameIndex += 1
			
		for index, file in enumerate(self.files):
			filepath = os.path.join(self.directory,file.name)
			if multiFileImport:
				print(f"Multi Mesh Import ({index+1}/{len(self.files)})")
				if index != 0:
					options["mergeArmature"] = mergeArmatureName
			if os.path.isfile(filepath):
				warningList, errorList = importREMeshFile(filepath,options)
				options["clearScene"] = False#Disable clear scene after first mesh is imported
				if errorList: hasImportErrors = True
				for warning in warningList:
					raiseWarning(warning)
			else:
				hasImportErrors = True
				raiseWarning(f"Path does not exist, cannot import file. If you are importing multiple files at once, they must all be in the same directory.\nInvalid Path:{filepath}")
			
		if not hasImportErrors:
			toggleConsole()
				
			if not multiFileImport:
				self.report({"INFO"},"Imported RE Mesh file.")
			else:
				self.report({"INFO"},f"Imported {str(len(self.files))} RE Mesh files.")
			
			return {"FINISHED"}
		else:
			self.report({"INFO"},"Failed to import RE Mesh. Check the console for errors.")
			return {"CANCELLED"}
	
	def invoke(self, context, event):
		if self.directory:
			if getAddonPreferences().dragDropImportOptions:
				return context.window_manager.invoke_props_dialog(self)
			else:
				return self.execute(context)
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}
def _syncFileBrowserFilename(self, propertyName, fileExtension):
	"""Point the open file browser's filename field at the name derived from the
	collection picker property, so the browser follows the user's selection."""
	temp = bpy.data.screens.get("temp")
	browserSpace = None
	if temp != None:
		for area in temp.areas:
			for space in area.spaces:
				params = getattr(space, "params", None)
				if params is not None and type(params).__name__ == "FileSelectParams":
					browserSpace = space
					break
	if browserSpace != None:
		value = getattr(self, propertyName)
		if fileExtension in value:
			browserSpace.params.filename = value.split(fileExtension)[0] + fileExtension + self.filename_ext

def update_targetMeshCollection(self,context):
	_syncFileBrowserFilename(self, "targetCollection", ".mesh")
class ExportREMesh(Operator, ExportHelper):
	'''Export RE Engine Mesh File'''
	bl_idname = "re_mesh.exportfile"
	bl_label = "Export RE Mesh"
	bl_options = {'PRESET'}
	
	filter_glob: StringProperty(default="*.mesh*", options={'HIDDEN'})
	
	filename_ext: EnumProperty(
		name="",
		description="Set which game to export the mesh for",
		items= [
				(".1808282334", "Devil May Cry 5", "Devil May Cry 5"), 
				(".1808312334", "Resident Evil 2", "Resident Evil 2"),
				(".1902042334", "Resident Evil 3", "Resident Evil 3"),
				(".2101050001", "Resident Evil 8", "Resident Evil 8"),
				(".2109108288", "Resident Evil 2 / 3 Ray Tracing", "Resident Evil 2/3 Ray Tracing Version"),
				(".220128762", "Resident Evil 7 Ray Tracing", "Resident Evil 7 Ray Tracing Version"),
			    (".2109148288", "Monster Hunter Rise", "Monster Hunter Rise"),
				(".221108797", "Resident Evil 4", "Resident Evil 4"),
				(".230110883", "Street Fighter 6", "Street Fighter 6"),
				(".240423143", "Dragon's Dogma 2", "Dragon's Dogma 2"),
				(".240306278", "Kunitsu-Gami", "Kunitsu-Gami"),
				(".240424828", "Dead Rising", "Dead Rising"),
				(".240827123", "Onimusha 2", "Onimusha 2"),
				(".241111606", "Monster Hunter Wilds", "Monster Hunter Wilds"),
				#(".250925211", "Resident Evil 9 / Pragmata", "Resident Evil 9 / Pragmata"),
				(".251121828", "Pragmata", "Pragmata"),
				(".250925211", "Resident Evil 9", "Resident Evil 9"),
				(".250604100", "Monster Hunter Stories 3", "Monster Hunter Stories 3"),
			   ]
		)
	targetCollection: bpy.props.StringProperty(
		name="",
		description = "Set the collection containing the meshes to be exported.\nNote: Mesh collections are red and end with .mesh",
		update = update_targetMeshCollection
		)
	selectedOnly : BoolProperty(
	   name = "Selected Objects Only",
	   description = "Limit export to selected objects",
	   default = False)

	
	exportAllLODs : BoolProperty(
	   name = "Export All LODs",
	   description = "Export all LODs. If disabled, only LOD0 will be exported. Note that LODs meshes must be grouped inside a collection for each level and that collection must be contained in another collection. See a mesh with LODs imported for reference on how it should look. A target collection must also be set",
	   default = True)
	exportBlendShapes : BoolProperty(
	   name = "Export Blend Shapes",
	   description = "Exports blend shapes from mesh if present",
	   default = True)
	rotate90 : BoolProperty(
	   name = "Convert Z Up To Y Up",
	   description = "Rotates objects 90 degrees for export. Leaving this option enabled is recommended",
	   default = True)
	autoSolveRepeatedUVs : BoolProperty(
	   name = "Auto Solve Repeated UVs",
	   description = "Splits connected UV islands if present. The mesh format does not allow for multiple uvs assigned to a vertex.\nNOTE: This will modify the exported mesh. If auto smooth is disabled on the mesh, the normals may change",
	   default = True)
	preserveSharpEdges : BoolProperty(
	   name = "Split Sharp Edges",
	   description = "Edge splits all edges marked as sharp to preserve them on the exported mesh.\nNOTE: This will modify the exported mesh",
	   default = True)
	useBlenderMaterialName : BoolProperty(
	   name = "Use Blender Material Names",
	   description = "If left unchecked, the exporter will get the material names to be used from the end of each object name. For example, if a mesh is named LOD_0_Group_0_Sub_0__Shirts_Mat, the material name is Shirts_Mat. If this option is enabled, the material name will instead be taken from the first material assigned to the object",
	   default = False)
	preserveBoneMatrices : BoolProperty(
	   name = "Preserve Bone Matrices",
	   description = "Export using the original matrices of the imported bones. Note that this option only applies armatures imported with this addon. Any newly added bones will have new matrices calculated",
	   default = False)
	exportBoundingBoxes : BoolProperty(
	   name = "Export Bounding Boxes",
	   description = "Exports the original bounding boxes from the \"Import Bounding Boxes\" import option. New bounding boxes will be generated for any bones that do not have them",
	   default = False)
	exportMDF : BoolProperty(
	   name = "Export MDF",
	   description = "Exports MDF file after the mesh is exported, if an MDF collection exists in the scene collection",
	   default = False)
	exportMDFExcludeUnused : BoolProperty(
	   name = "Exclude Unused Materials",
	   description = "When exporting the MDF, exclude the materials that do not exist in the mesh collection. Only applies with \"Export MDF\"",
	   default = False)
	def invoke(self, context, event):
		remapLegacySceneVersion(context.scene, "REMeshLastImportedMeshVersion")
		
		remapLegacySceneVersion(context.scene, "REMeshLastExportedMeshVersion")
		
		if self.targetCollection == "":
			
			
			#Get last exported collection, if there isn't one, get last imported
			exportCollection = context.scene.get("REMeshLastExportedCollection","")
			if exportCollection in bpy.data.collections:
				self.targetCollection = exportCollection
				if ".mesh" in exportCollection:#Remove blender suffix after .mesh if it exists
					if context.scene.get("REMeshLastExportedMeshVersion") in meshFileVersionToGameNameDict:
						self.filename_ext = "."+str(context.scene.get("REMeshLastExportedMeshVersion",1808282334))
					self.filepath = exportCollection.split(".mesh")[0]+".mesh" + self.filename_ext
			else:
				prevCollection = context.scene.get("REMeshLastImportedCollection","")
				if prevCollection in bpy.data.collections:
					self.targetCollection = prevCollection
				if ".mesh" in prevCollection:#Remove blender suffix after .mesh if it exists
					if context.scene.get("REMeshLastImportedMeshVersion") in meshFileVersionToGameNameDict:
						self.filename_ext = "."+str(context.scene.get("REMeshLastImportedMeshVersion",1808282334))
					self.filepath = prevCollection.split(".mesh")[0]+".mesh" + self.filename_ext

			
		self.filepath = applySourceDirectory(bpy.data.collections.get(self.targetCollection, None), "~MESHFILEPATH", self.filepath)
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}
	def draw(self, context):
		layout = self.layout
		layout.label(text = "Mesh Version:")
		layout.prop(self, "filename_ext")
		layout.label(text = "Mesh Collection:")
		layout.prop_search(self, "targetCollection",bpy.data,"collections",icon = "COLLECTION_COLOR_01")
		
		drawCollectionWarning(layout, self, "targetCollection", isMeshCollection, "Collection is not a mesh collection.")
		layout.prop(self, "selectedOnly")
		layout.label(text = "Advanced Options")
		layout.prop(self, "exportAllLODs")
		#layout.prop(self, "exportBlendShapes")
		#hasREToolbox = hasattr(bpy.types, "OBJECT_PT_re_tools_quick_export_panel")
		row = layout.row()
		#row.enabled = hasREToolbox
		row.prop(self,"autoSolveRepeatedUVs")
		row2 = layout.row()
		#row2.enabled = hasREToolbox
		row2.prop(self,"preserveSharpEdges")

		layout.prop(self, "rotate90")
		layout.prop(self, "useBlenderMaterialName")
		layout.prop(self, "preserveBoneMatrices")
		layout.prop(self, "exportBoundingBoxes")
		layout.prop(self, "exportMDF")
		mdfRow = layout.row()
		mdfRow.enabled = self.exportMDF
		mdfRow.prop(self, "exportMDFExcludeUnused")

	def execute(self, context):
		options = {"targetCollection":self.targetCollection,"selectedOnly":self.selectedOnly,"exportAllLODs":self.exportAllLODs,"exportBlendShapes":self.exportBlendShapes,"rotate90":self.rotate90,"useBlenderMaterialName":self.useBlenderMaterialName,"preserveBoneMatrices":self.preserveBoneMatrices,"exportBoundingBoxes":self.exportBoundingBoxes,"autoSolveRepeatedUVs":self.autoSolveRepeatedUVs,"preserveSharpEdges":self.preserveSharpEdges}
		meshVersion = parseFileVersion(self.filepath, None)
		if meshVersion is None:
			self.report({"INFO"},"Mesh file path is missing number extension. Cannot export.")
			return{"CANCELLED"}
		printEditorHeader(bl_info)
		
		toggleConsole()
		# Resolve the materials used by the mesh before exporting it, since the exporter changes the selection while it runs
		onlyMaterialNames = None
		if self.exportMDF and self.exportMDFExcludeUnused:
			onlyMaterialNames = getMeshMaterialNames(bpy.data.collections.get(self.targetCollection, None), useBlenderMaterialName=self.useBlenderMaterialName, selectedOnly=self.selectedOnly, exportAllLODs=self.exportAllLODs)
		success = exportREMeshFile(self.filepath,options)
		if success:
			self.report({"INFO"},"Exported RE Mesh successfully.")
			if self.targetCollection in bpy.data.collections:
				bpy.data.collections[self.targetCollection]["BatchExport_path"] = self.filepath
				bpy.data.collections[self.targetCollection]["BatchExport_exportAllLODs"] = self.exportAllLODs
				bpy.data.collections[self.targetCollection]["BatchExport_preserveSharpEdges"] = self.preserveSharpEdges
				bpy.data.collections[self.targetCollection]["BatchExport_rotate90"] = self.rotate90
				bpy.data.collections[self.targetCollection]["BatchExport_exportBlendShapes"] = self.exportBlendShapes
				bpy.data.collections[self.targetCollection]["BatchExport_useBlenderMaterialName"] = self.useBlenderMaterialName
				bpy.data.collections[self.targetCollection]["BatchExport_preserveBoneMatrices"] = self.preserveBoneMatrices
				bpy.data.collections[self.targetCollection]["BatchExport_exportBoundingBoxes"] = self.exportBoundingBoxes
		else:
			self.report({"INFO"},"RE Mesh export failed. See Window > Toggle System Console for info on how to fix it.")
		if self.exportMDF:
			# Export the MDF for this mesh if an MDF collection exists in the scene collection
			mdfCollection = findMDFCollectionForMesh(bpy.data.collections.get(self.targetCollection, None))
			if mdfCollection == None:
				self.report({"INFO"},"No MDF collection found in the scene collection for this mesh. Only the mesh was exported.")
			else:
				# Map the mesh version extension (.221108797) to the matching MDF version (.32)
				meshVersionExtension = os.path.splitext(self.filepath)[1]
				mdfVersionExtension = mdfVersionDict.get(meshVersionExtension, None)
				if mdfVersionExtension == None:
					self.report({"WARNING"},f"Unable to determine the MDF version for mesh version {meshVersionExtension}. Only the mesh was exported.")
				else:
					# Use the MDF collection's own base name, so _Mat / _v00 MDF collections keep their file name
					mdfFileName = mdfCollection.name.rsplit(".mdf2", 1)[0] + ".mdf2" + mdfVersionExtension
					mdfPath = os.path.join(os.path.dirname(self.filepath), mdfFileName)
					print(f"Exporting MDF: {mdfPath}")
					mdfSuccess = exportMDFFile(mdfPath, mdfCollection.name, onlyMaterialNames, useBlenderMaterialName=self.useBlenderMaterialName, exportAllLODs=self.exportAllLODs)
					if mdfSuccess:
						self.report({"INFO"},"Exported RE MDF successfully.")
						bpy.data.collections[mdfCollection.name]["BatchExport_path"] = mdfPath
					else:
						self.report({"WARNING"},"RE MDF export failed. See Window > Toggle System Console for info on how to fix it.")
		
		if bpy.context.scene.re_mdf_toolpanel.modDirectory == "":
			setModDirectoryFromFilePath(self.filepath)

		toggleConsole()
		return {"FINISHED"}

class ImportREMDF(bpy.types.Operator, ImportHelper):
	'''Import RE Engine MDF File'''
	bl_idname = "re_mdf.importfile"
	bl_label = "Import RE MDF"
	bl_options = {'PRESET', "REGISTER", "UNDO"}
	
	files : CollectionProperty(
			name="File Path",
			type=OperatorFileListElement,
			)
	directory : StringProperty(
			subtype='DIR_PATH',
			options={'SKIP_SAVE'}
			)
	filename_ext = ".mdf.*"
	filter_glob: StringProperty(default="*.mdf2.*", options={'HIDDEN'})

	def execute(self, context):
		printEditorHeader(bl_info)
		if importFileList(self, importMDFFile, "MDF", "MDF"):
			return {"FINISHED"}
		return {"CANCELLED"}
	def invoke(self, context, event):
		if self.directory:
			return self.execute(context)
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}

def update_targetMDFCollection(self,context):
	_syncFileBrowserFilename(self, "targetCollection", ".mdf2")
class ExportREMDF(bpy.types.Operator, ExportHelper):
	'''Export RE Engine MDF File'''
	bl_idname = "re_mdf.exportfile"
	bl_label = "Export RE MDF"
	bl_options = {'PRESET'}
	filename_ext: EnumProperty(
		name="",
		description="Set which game to export the MDF for",
		items=[ 
				(".10", "Devil May Cry 5 / Resident Evil 2", ""),
				(".13", "Resident Evil 3", ""),
				(".19", "Resident Evil 8", ""),
				(".21", "Resident Evil 2 / 3 / 7 Ray Tracing", ""),
				(".23", "Monster Hunter Rise", ""),
				(".32", "Resident Evil 4", ""),
				(".31", "Street Fighter 6", ""),
				(".40", "Dragon's Dogma 2 / Kunitsu-Gami / Dead Rising", "Dragon's Dogma 2, Kunitsu-Gami, Dead Rising"),
				(".46", "Onimusha 2", "Onimusha 2"),
				(".45", "Monster Hunter Wilds", "Monster Hunter Wilds"),
				(".51", "Resident Evil 9 / Pragmata", "Resident Evil 9 / Pragmata"),
				(".49", "Monster Hunter Stories 3", "Monster Hunter Stories 3"),
			  ]
		)
	targetCollection : StringProperty(
	   name = "",
	   description = "Set the MDF collection to be exported\nNote: MDF collections are blue and end with .mdf2",
	   default = "",
	   update = update_targetMDFCollection)
	filter_glob: StringProperty(default="*.mdf2*", options={'HIDDEN'})
	def invoke(self, context, event):
		if bpy.data.collections.get(self.targetCollection,None) == None:
			if bpy.context.scene.re_mdf_toolpanel.mdfCollection:
				self.targetCollection = bpy.context.scene.re_mdf_toolpanel.mdfCollection.name
				if self.targetCollection.endswith(".mdf2"):
					self.filepath = self.targetCollection + self.filename_ext
			self.filename_ext = "."+str(gameNameMDFVersionDict[bpy.context.scene.re_mdf_toolpanel.activeGame])
		self.filepath = applySourceDirectory(bpy.data.collections.get(self.targetCollection, None), "~MDFFILEPATH", self.filepath)
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}
	
	def draw(self, context):
		layout = self.layout
		layout.label(text = "MDF Version:")
		layout.prop(self,"filename_ext")
		layout.label(text = "MDF Collection:")
		layout.prop_search(self, "targetCollection",bpy.data,"collections",icon = "COLLECTION_COLOR_05")
		drawCollectionWarning(layout, self, "targetCollection", isMDFCollection, "Collection is not a MDF collection.")
	def execute(self, context):
		printEditorHeader(bl_info)
		success = exportMDFFile(self.filepath,self.targetCollection)
		if success:
			self.report({"INFO"},"Exported RE MDF successfully.")
			
			if bpy.context.scene.re_mdf_toolpanel.modDirectory == "":
				setModDirectoryFromFilePath(self.filepath)
			
			bpy.data.collections[self.targetCollection]["BatchExport_path"] = self.filepath
			return {"FINISHED"}
		else:
			self.report({"INFO"},"Failed to export RE MDF. Check Window > Toggle System Console for details.")
			return {"CANCELLED"}

class ImportREFBXSkel(bpy.types.Operator, ImportHelper):
	'''Import RE Engine FBXSkel File'''
	bl_idname = "re_fbxskel.importfile"
	bl_label = "Import RE FBXSkel"
	bl_options = {'PRESET', "REGISTER", "UNDO"}
	files : CollectionProperty(
			name="File Path",
			type=OperatorFileListElement,
			)
	directory : StringProperty(
			subtype='DIR_PATH',
			options={'SKIP_SAVE'}
			)
	filename_ext = ".fbxskel.*"
	filter_glob: StringProperty(default="*.fbxskel.*", options={'HIDDEN'})
	
	def invoke(self, context, event):
		if self.directory:
			return self.execute(context)
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}
	def execute(self, context):
		success = importFBXSkelFile(self.filepath)
		if success:
			return {"FINISHED"}
		else:
			self.report({"INFO"},"Failed to import RE FBXSkel.")
			return {"CANCELLED"}

def update_targetFBXSkelArmature(self,context):
	_syncFileBrowserFilename(self, "targetArmature", ".fbxskel")
		
class ExportREFBXSkel(bpy.types.Operator, ExportHelper):
	'''Export RE Engine FBXSkel File'''
	bl_idname = "re_fbxskel.exportfile"
	bl_label = "Export RE FBXSkel"
	bl_options = {'PRESET'}
	filename_ext: EnumProperty(
		name="",
		description="Set which game to export the FBXSkel for",
		items=[ (".3", "DMC5 (.3)", ""),
				(".4", "RERT (.4)", ""),
				(".5", "RE4R (.5)", ""),
				(".6", "DD2 (.6)", ""),
				(".7", "MH Wilds / MHS3 (.7)", ""),
				(".8", "Resident Evil 9 / Pragmata (.8)", ""),

			   ],
		default = ".7"
		)
	
	filter_glob: StringProperty(default="*.fbxskel*", options={'HIDDEN'})
	targetArmature : StringProperty(
	   name = "",
	   description = "Set the armature to be exported",
	   default = "",
	   update = update_targetFBXSkelArmature)
	
	def invoke(self, context, event):
		if self.targetArmature == "":
			if context.active_object != None:
				if context.active_object.type == "ARMATURE":
					self.targetArmature = context.active_object.name
					
			else:
				for obj in bpy.context.scene.objects:
					if obj.type == "ARMATURE" and (".fbxskel" in obj.name or ".skeleton" in obj.name or ".refskel" in obj.name):
						self.targetArmature = obj.name
						break
			if self.targetArmature != "":
				if ".fbxskel" in self.targetArmature:
					self.filepath = self.targetArmature.split(".fbxskel")[0]+".fbxskel" + self.filename_ext
				elif ".skeleton" in self.targetArmature:
					self.filepath = self.targetArmature.split(".skeleton")[0]+".skeleton" + self.filename_ext
				elif ".refskel" in self.targetArmature:
					self.filepath = self.targetArmature.split(".refskel")[0]+".refskel" + self.filename_ext
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}
	
	def draw(self, context):
		layout = self.layout
		layout.label(text = "FBXSkel Version:")
		layout.prop(self,"filename_ext")
		layout.label(text = "FBXSkel Armature:")
		layout.prop_search(self, "targetArmature",bpy.data,"objects",icon = "ARMATURE_DATA")
		if self.targetArmature in bpy.data.objects:
			obj = bpy.data.objects[self.targetArmature]
			if not obj.type == "ARMATURE":
				row = layout.row()
				row.alert=True
				row.label(icon = "ERROR",text="Object is not an armature.")
		elif self.targetArmature == "":
			row = layout.row()
			row.alert=True
			row.label(icon = "ERROR",text="No armature is selected.")
			
		else:
			row = layout.row()
			row.label(icon="ERROR",text="Armature doesn't exist.")
			row.alert=True
	
	def execute(self, context):
		success = exportFBXSkelFile(self.filepath,self.targetArmature)
		if success:
			self.report({"INFO"},"Exported RE FBXSkel successfully.")
			bpy.data.objects[self.targetArmature]["BatchExport_path"] = self.filepath
			return {"FINISHED"}
		else:
			self.report({"INFO"},"Failed to export RE FBXSkel. Check Window > Toggle System Console for details.")
			return {"CANCELLED"}

class ImportRESFur(bpy.types.Operator, ImportHelper):
	'''Import RE Engine SFur File'''
	bl_idname = "re_sfur.importfile"
	bl_label = "Import RE SFur"
	bl_options = {'PRESET', "REGISTER", "UNDO"}
	
	files : CollectionProperty(
			name="File Path",
			type=OperatorFileListElement,
			)
	directory : StringProperty(
			subtype='DIR_PATH',
			options={'SKIP_SAVE'}
			)
	filename_ext = ".sfur.*"
	filter_glob: StringProperty(default="*.sfur.*", options={'HIDDEN'})

	def execute(self, context):
		printEditorHeader(bl_info)
		if importFileList(self, importSFurFile, "SFur"):
			return {"FINISHED"}
		return {"CANCELLED"}
	def invoke(self, context, event):
		if self.directory:
			return self.execute(context)
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}

def update_targetSFurCollection(self,context):
	_syncFileBrowserFilename(self, "targetCollection", ".sfur")
class ExportRESFur(bpy.types.Operator, ExportHelper):
	'''Export RE Engine SFur File'''
	bl_idname = "re_sfur.exportfile"
	bl_label = "Export RE SFur"
	bl_options = {'PRESET'}
	filename_ext: EnumProperty(
		name="",
		description="Set which game to export the MDF for",
		items=[ 
				(".4", "Resident Evil 4", ""),
				(".5", "Resident Evil 9 / Dragon's Dogma 2 / Monster Hunter Wilds", ""),
			  ]
		)
	targetCollection : StringProperty(
	   name = "",
	   description = "Set the SFur collection to be exported\nNote: SFur collections are brown and end with .sfur",
	   default = "",
	   update = update_targetSFurCollection)
	filter_glob: StringProperty(default="*.sfur*", options={'HIDDEN'})
	def invoke(self, context, event):
		if context.scene.get("REMeshLastImportedSFurVersion",0) != 0:
			self.filename_ext = "."+str(context.scene.get("REMeshLastImportedSFurVersion"))
	
		if bpy.data.collections.get(self.targetCollection,None) == None:
			if bpy.context.scene.re_mdf_toolpanel.sFurCollection:
				self.targetCollection = bpy.context.scene.re_mdf_toolpanel.sFurCollection.name
				if self.targetCollection.endswith(".sfur"):
					self.filepath = self.targetCollection + self.filename_ext
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}
	
	def draw(self, context):
		layout = self.layout
		layout.label(text = "SFur Version:")
		layout.prop(self,"filename_ext")
		layout.label(text = "SFur Collection:")
		layout.prop_search(self, "targetCollection",bpy.data,"collections",icon = "COLLECTION_COLOR_08")
		drawCollectionWarning(layout, self, "targetCollection", isSFurCollection, "Collection is not a SFUR collection.")
	def execute(self, context):
		printEditorHeader(bl_info)
		success = exportSFurFile(self.filepath,self.targetCollection)
		if success:
			self.report({"INFO"},"Exported RE SFur successfully.")
			
			if bpy.context.scene.re_mdf_toolpanel.modDirectory == "":
				setModDirectoryFromFilePath(self.filepath)
			
			bpy.data.collections[self.targetCollection]["BatchExport_path"] = self.filepath
			return {"FINISHED"}
		else:
			self.report({"INFO"},"Failed to export RE SFur. Check Window > Toggle System Console for details.")
			return {"CANCELLED"}


# Registration
classes = (
	ImportREMesh,
	ExportREMesh,
	ImportREMDF,
	ExportREMDF,
	ImportREFBXSkel,
	ExportREFBXSkel,
	ImportRESFur,
	ExportRESFur,
)
