import os
import bpy

from ..blender_utils import (showMessageBox, showErrorMessageBox, createRECollection, setAssetPathFromFilePath, createEmpty, checkNameUsage,
	getMeshCollectionNameFromMDFName, iterMDFMaterialObjects, iterExportableMeshObjects, getMDFMaterialName, setMDFMaterialName,
	normalizeMaterialNameFilter,
	MDF_MATERIAL_TYPE, MDF_COLLECTION_TYPE,
	)
from ..gen_functions import textColors,raiseWarning,splitNativesPath,getAdjacentFileVersion,parseFileVersion,ENABLE_EXPORT_WARNINGS
from ..naming import normalizeREMaterialName,getREMeshMaterialName,formatREMaterialObjectName
from .file_re_mdf import readMDF,writeMDF,MDFFile,Material,TextureBinding,Property,gameNameMDFVersionDict,getMDFVersionToGameName,MMTRSData,GPBFEntry
from .mdf_value import classify_prop,getPropValue,setPropValue,flipSF6CustomizeValues
from .ui_re_mdf_panels import tag_redraw

MDFGameNameConflictDict = set(["RE2","RE2RT","DD2"])


def resolveMDFGameNameConflict(gameName,mdfFile,filePath):
	rootPath = os.path.split(filePath)[0].lower()
	realGameName = None
	if gameName == "RE2":
		if "re2" in rootPath:
			realGameName = "RE2"
		elif "dmc5" in rootPath or "dmcv" in rootPath:
			realGameName = "DMC5"
		if realGameName == None:
			meshVersion = getAdjacentFileVersion(rootPath,".mesh")
			if meshVersion == 1808312334: #RE2:
				realGameName = "RE2"
			elif meshVersion == 1808282334:
				realGameName = "DMC5"
		if realGameName == None:
			texVersion = getAdjacentFileVersion(rootPath,".tex")
			if texVersion == 10:
				realGameName = "RE2"
			elif texVersion == 11:
				realGameName = "DMC5"
		if realGameName == None and len(mdfFile.materialList) > 0:
			for texture in mdfFile.materialList[0].textureList:
				if "objectroot" in texture.texturePath.lower():
					realGameName = "RE2"
					break 
		if realGameName == None:
			realGameName = "DMC5"
	elif gameName == "RE2RT":
		if "re3" in rootPath:
			realGameName = "RE3RT"
		elif "re2" in rootPath:
			realGameName = "RE2RT"
		elif "re7" in rootPath:
			realGameName = "RE7RT"
		if realGameName == None and len(mdfFile.materialList) > 0:
			texVersion = getAdjacentFileVersion(rootPath,".tex")
			if texVersion == 35:
				realGameName = "RE7RT"
			else:
				for texture in mdfFile.materialList[0].textureList:
					if "escape" in texture.texturePath.lower():
						realGameName = "RE3RT"
						break
	elif gameName == "DD2":
		if "dd2" in rootPath:
			realGameName = "DD2"
			
		if realGameName == None:
			meshVersion = getAdjacentFileVersion(rootPath,".mesh")
			if meshVersion != -1:
				if meshVersion == 240306278:
					realGameName = "KG"
				elif meshVersion == 231011879:
					realGameName = "DD2"
				elif meshVersion == 240424828:
					realGameName = "DR"
		if realGameName == None:
			texVersion = getAdjacentFileVersion(rootPath,".tex")
			if texVersion != -1:
				if texVersion == 231106777:
					realGameName = "KG"
				elif texVersion == 760230703:
					realGameName = "DD2"
				elif texVersion == 240606151:
					realGameName = "DR"
	if realGameName == None:
		realGameName = gameName
	return realGameName
	



def createMDFCollection(collectionName,parentCollection = None):
	collection = createRECollection(
		collectionName, parentCollection,
		color_tag="COLOR_05", customProps={"~TYPE": MDF_COLLECTION_TYPE})
	bpy.context.scene.re_mdf_toolpanel.mdfCollection = collection
	return collection


def addPropsToPropList(obj,matPropertyList):
	obj.re_mdf_material.propertyList_items.clear()
	for prop in matPropertyList:
		newListItem = obj.re_mdf_material.propertyList_items.add()
		newListItem.prop_name = prop.propName
		newListItem.padding = prop.padding
		newListItem.frontPadding = prop.frontPadding
		newListItem.data_type = classify_prop(prop.propName, prop.paramCount)
		setPropValue(newListItem, newListItem.data_type, prop.propValue)


def addTextureBindingsToBindingList(obj,matTextureList):
	for texture in matTextureList:
		newListItem = obj.re_mdf_material.textureBindingList_items.add()
		newListItem.textureType = texture.textureType
		newListItem.path = texture.texturePath

def addMMTRSDataToList(obj,mmtrsData):
	for indexList in mmtrsData.indexDataList:
		newListItem = obj.re_mdf_material.mmtrsData_items.add()
		newString = ""
		for i, index in enumerate(indexList):
			newString+=f"{str(index)}"
			if i != len(indexList)-1:
				newString+=","
		newListItem.indexString = newString

def addGPBFDataToList(obj,bufferNameList,bufferPathList):
	for i in range(0,len(bufferNameList)):
		newListItem = obj.re_mdf_material.gpbfData_items.add()
		newListItem.gpbfDataString = f"{bufferNameList[i].name},{bufferPathList[i].name},{str(bufferPathList[i].nameUTF16Hash)},{str(bufferPathList[i].nameUTF8Hash)}"

def getMDFFlags(obj,flags,flagsB):
	obj.re_mdf_material.flags.flagIntValue = flags.asInt32
	obj.re_mdf_material.flags.flagIntValueB = flagsB.asInt32
	
#MDF IMPORT


def importMDFFile(filePath,parentCollection = None,mdfFile = None):
	if mdfFile is None:
		mdfFile = readMDF(filePath)
	mdfFileName = os.path.splitext(os.path.split(filePath)[1])[0]
	mdfVersion = parseFileVersion(filePath, None)
	if mdfVersion is None:
		print("Unable to parse mdf version number in file path.")
		gameName = -1
		mdfVersion = 45
	else:
		if mdfVersion in gameNameMDFVersionDict:
			gameName = gameNameMDFVersionDict[mdfVersion]
			if gameName in MDFGameNameConflictDict:
				gameName = resolveMDFGameNameConflict(gameName, mdfFile, filePath)
				print(f"MDF version {str(mdfVersion)} is used by more than one game, detected {gameName}")
			bpy.context.scene.re_mdf_toolpanel.activeGame = gameName
		else:
			gameName = -1
	#headerObj = createEmpty("MDF_HEADER ("+os.path.split(filePath)[1]+")",[("~TYPE","RE_MDF_HEADER"),("unknHeaderValue",mdfFile.Header.version),("MDFVersion",os.path.splitext(filePath)[1].split(".")[1])],None,"MDFData")
	mdfCollection = createMDFCollection(mdfFileName,parentCollection)
	bpy.context.scene["REMeshLastImportedMDFVersion"] = mdfVersion
	setAssetPathFromFilePath(filePath, mdfCollection)
	mdfCollection["~MDFFILEPATH"] = filePath #Store the source file path so operators can locate the MDF folder
	#MATERIALS IMPORT
	for index, material in enumerate(mdfFile.materialList):
		name = formatREMaterialObjectName("Material", index, material.materialName)
		materialObj = createEmpty(name,[("~TYPE",MDF_MATERIAL_TYPE)],None,mdfCollection)
		#gameName = getMDFVersionToGameName(mdfVersion)
		if gameName != -1:
			materialObj.re_mdf_material.gameName = gameName
		materialObj.re_mdf_material.materialName = material.materialName
		materialObj.re_mdf_material.shaderType = str(material.shaderType)
		materialObj.re_mdf_material.mmtrPath = material.mmtrPath
		materialObj.re_mdf_material.flags.ver32Unknown = material.ver32Unkn0
		materialObj.re_mdf_material.flags.ver32Unknown1 = material.ver32Unkn1
		materialObj.re_mdf_material.flags.ver32Unknown2 = material.ver32Unkn2
		
		materialObj.re_mdf_material.flags.shaderLODNum = material.shaderLODNum 
		materialObj.re_mdf_material.flags.bakeTextureArraySize = material.bakeTextureArraySize
		
		getMDFFlags(materialObj,material.flags,material.flagsB)
		materialObj.re_mdf_material.flags.hideMaterialInGame = material.hideInGame
		if gameName == "SF6":
			flipSF6CustomizeValues(material.propertyList, toGame=False)
		addPropsToPropList(materialObj,material.propertyList)
		addTextureBindingsToBindingList(materialObj, material.textureList)
		if material.mmtrsData != None:
			addMMTRSDataToList(materialObj,material.mmtrsData)
		if material.GPBFBufferNameCount > 0:
			addGPBFDataToList(materialObj,material.gpbfBufferNameList,material.gpbfBufferPathList)
		#flagsObj = createEmpty("Material "+str(index).zfill(2)+" Flags", getMaterialFlags(material.flags.flags),None,mdfCollection)
		#textureBindingsObj = createEmpty("Material "+str(index).zfill(2)+" Texture Bindings", getTextureBindings(material.textureList),None,mdfCollection)
		#propertyListObj = createEmpty("Material "+str(index).zfill(2)+" Property List", getPropertyList(material.propertyList),None,mdfCollection)
	tag_redraw(bpy.context)
	return True


#MDF EXPORT

def syncMDFMaterialNameFromObject(obj):
	"""Adopt a "Material NN (name)" outliner rename into the materialName property.

	The mesh exporter names materials from the mesh object name, while the MDF
	writer (and the exclude-unused filter) use this property, so renaming an MDF
	material object in the outliner without touching the property makes the two
	sides diverge and the material goes missing in game. The parenthesised name
	wins, matching the convention documented in reindexMaterials."""
	setMDFMaterialName(obj, getMDFMaterialName(obj))
def reindexMaterials(mdfCollection, stripSuffix = True):

	if mdfCollection != None:

		# Sort by material name (case-insensitive) and normalize each name with the
		# same shared rule the Rename Meshes operator uses, so MDF material order
		# and names stay consistent with the mesh. stripSuffix = True (the manual
		# "Reindex Materials" operator) also drops a trailing "_Mat" suffix
		# (case-insensitive). Export paths pass stripSuffix = False so "_Mat" names
		# are preserved in the output file.
		materialObjs = list(iterMDFMaterialObjects(mdfCollection))
		materialObjs.sort(key = lambda obj: normalizeREMaterialName(obj.re_mdf_material.materialName, stripSuffix).lower())
		for currentIndex, obj in enumerate(materialObjs):
			#Change the material name in the mdf material settings to the one in the object name
			#This allows for the user to set the material name by either method of renaming the object or setting it in the mdf material settings
			syncMDFMaterialNameFromObject(obj)
			setMDFMaterialName(obj, normalizeREMaterialName(obj.re_mdf_material.materialName, stripSuffix))
			obj.name = formatREMaterialObjectName("Material", currentIndex, obj.re_mdf_material.materialName)
def MDFErrorCheck(collectionName, onlyMaterialNames = None, useBlenderMaterialName = False, exportAllLODs = True):
	print("\nChecking for problems with MDF structure...")
	
	#Check that there is mdf data collection
	#Check that there is only one header
	#Check that all materials have only one flag, property list and texture binding objects
	#Check that all materials are parented to the header
	#Check that parenting structure is valid
	#Check for duplicate material names
	errorList = []
	warningList = []
	materialNameSet = set()
	meshMaterialSet = set()
	if bpy.data.collections.get(collectionName,None) != None:
		#The ~TYPE filter lives in iterMDFMaterialObjects, like everywhere else
		for materialObj in iterMDFMaterialObjects(bpy.data.collections[collectionName]):
			if materialObj.re_mdf_material.materialName not in materialNameSet:
				materialNameSet.add(materialObj.re_mdf_material.materialName)
			else:
				errorList.append("Duplicate material name on " + materialObj.name+". Set the material name in the custom properties of the material object to a different name.")
	else:
		errorList.append("MDF objects must be grouped in a collection.")
	
	# OnlyMaterialNames: the export is limited to the materials the mesh uses, the other
	# MDF materials are dropped from the file, so they must not be reported as unused.
	# Matching is case-insensitive, like the mesh exporter's material comparison.
	if onlyMaterialNames != None:
		keepNameSet = set(materialName.lower() for materialName in onlyMaterialNames)
		materialNameSet = set(materialName for materialName in materialNameSet if materialName.lower() in keepNameSet)
	# All mesh/MDF name comparisons below are case-insensitive (see comment above), so
	# a mesh named "__Siwa" matches an MDF material "siwa" instead of warning twice.
	materialNameSetLower = set(materialName.lower() for materialName in materialNameSet)
	
	meshCollectionName = getMeshCollectionNameFromMDFName(collectionName)
	meshCollection = bpy.data.collections.get(meshCollectionName,None)
	if meshCollection != None:
		# Same objects and same name resolution as the mesh exporter
		# (iterExportableMeshObjects + getREMeshMaterialName), so these warnings agree with
		# the material names actually written to the mesh file. A standalone MDF export has
		# no export options and uses the defaults.
		for obj in iterExportableMeshObjects(meshCollection, exportAllLODs = exportAllLODs):
			matName = getREMeshMaterialName(obj, useBlenderMaterialName = useBlenderMaterialName, fallback = "")
			if matName == "":
				continue
			meshMaterialSet.add(matName)
			if matName.lower() not in materialNameSetLower:
				warningList.append("The material ("+matName+") on mesh " + obj.name + " does not exist in the MDF.")
	else:
		if ENABLE_EXPORT_WARNINGS:
			raiseWarning(f"Could not find mesh collection ({meshCollectionName}) to check materials against.")
	meshMaterialSetLower = set(materialName.lower() for materialName in meshMaterialSet)
	unusedMaterialNames = [materialName for materialName in materialNameSet if materialName.lower() not in meshMaterialSetLower]
	if len(meshMaterialSet) != 0 and unusedMaterialNames != []:
		materialString = ""
		for materialName in unusedMaterialNames:
			materialString += materialName+"\n"
		warningList.append(f"The following materials exist in the MDF ({collectionName}) but do not exist in the mesh ({meshCollectionName}):\n{materialString}")
	if warningList != [] and ENABLE_EXPORT_WARNINGS:
		warningList.append("If this mesh is supposed to be used with this MDF, the number of materials and name of materials in the MDF must match the mesh.\nThe material will appear as a checkerboard texture in game.")
		for warning in warningList:
			raiseWarning(warning)
				
	if errorList == []:
		print("No errors found.")
		#print(noesisMeshMaterialSet)
		if warningList != [] and ENABLE_EXPORT_WARNINGS:
			showMessageBox("Warnings occured during export. Check Window > Toggle System Console for details.",title = "MDF Conversion Warning", icon = "ERROR")
		return True
	else:
		errorString = ""
		for error in errorList:
			errorString += textColors.FAIL +"ERROR: " + error + textColors.ENDC +"\n"
		showMessageBox("MDF structure contains errors and cannot export. Check Window > Toggle System Console for details.",title = "Export Error", icon = "ERROR")
		print(errorString)
		print(textColors.FAIL + "__________________________________\nMDF export failed."+textColors.ENDC)
		return False

def fixTexPath(path):#Fix potential path problems
	if not path.endswith(".rtex"):
		path = path.replace(os.sep,"/").split(".tex")[0]+".tex"#Fix incorrect directory separators and including version numbers on the tex path
		if "natives" in path.lower():
			splitPath = splitNativesPath(path)
			if splitPath != None:
				path = splitPath[1]#Fix including the chunk root path in the tex path
	return path
def filterMDFMaterials(materialObjList, onlyMaterialNames):
	"""Keep only the material objects whose name is used by the mesh, so materials that
	exist in the MDF collection but not in the mesh collection are left out of the file.
	Names are compared case-insensitively, like the mesh exporter's material check.
	onlyMaterialNames is a non-empty set: an empty set is normalised to "no filter" in
	buildMDF (normalizeMaterialNameFilter) before this is called."""
	keepNameSet = set(materialName.lower() for materialName in onlyMaterialNames)
	keptList = []
	for materialObj in materialObjList:
		materialName = materialObj.re_mdf_material.materialName
		if materialName.lower() in keepNameSet:
			keptList.append(materialObj)
		else:
			print(f"Excluding MDF material that is not used by the mesh: {materialName}")
	return keptList
def buildMDF(mdfCollectionName,mdfVersion = None,onlyMaterialNames = None,useBlenderMaterialName = False,exportAllLODs = True):
	mdfCollection = bpy.data.collections.get(mdfCollectionName)
	# An empty material filter (the mesh uses no exportable materials) is normalised to
	# "no filter" here, so MDFErrorCheck and filterMDFMaterials share ONE rule and can
	# never disagree about an empty set (one used to filter to nothing, the other to
	# export everything). Warn so the fallback - exporting every material - is not silent.
	if onlyMaterialNames is not None and len(onlyMaterialNames) == 0:
		raiseWarning("No materials found in the mesh collection, exporting every material in the MDF instead.")
	onlyMaterialNames = normalizeMaterialNameFilter(onlyMaterialNames)
	if mdfVersion == None:
		
		mdfVersion = getMDFVersionToGameName(bpy.context.scene.re_mdf_toolpanel.activeGame)
		reindexMaterials(mdfCollection, stripSuffix = False)
	if mdfCollection != None:
		#Export paths always pass an explicit version, so reindexMaterials above never
		#runs during a mesh+MDF export. Sync the "Material NN (name)" object names into
		#the property first: the mesh file names materials from the mesh object name, so
		#a renamed MDF object must not be written (or filtered) under its stale property.
		for materialObj in iterMDFMaterialObjects(mdfCollection):
			syncMDFMaterialNameFromObject(materialObj)
		valid = MDFErrorCheck(mdfCollectionName,onlyMaterialNames,useBlenderMaterialName,exportAllLODs)
	else:
		showErrorMessageBox("MDF collection is not set, cannot export")
		valid = False
	if valid:
		# Case-insensitive name sort keeps the MDF's own material list deterministic for
		# MDF-side tooling. It does NOT need to match the mesh file's material list order:
		# the mesh binds each submesh to its OWN material list by index (self-consistent),
		# and mesh<->MDF material binding is by NAME (MDFErrorCheck compares names, the
		# import path matches by name, and the engine resolves mesh material names into the
		# MDF's list by name). Multi-group meshes therefore write a group-major mesh list
		# and a globally-sorted MDF list without breaking in-game materials.
		materialObjList = sorted(iterMDFMaterialObjects(mdfCollection),key = lambda item: item.re_mdf_material.materialName.lower())
		if onlyMaterialNames != None:
			materialObjList = filterMDFMaterials(materialObjList,onlyMaterialNames)
			if materialObjList == []:
				# An MDF without materials would only show up as a checkerboard in game, so refuse to write one
				raiseWarning("None of the materials in the MDF collection are used by the mesh, MDF export cancelled.")
				return None
		newMDFFile = MDFFile()
		newMDFFile.fileVersion = mdfVersion
		newMDFFile.Header.version = 1
		if mdfVersion == 45:#MHWILDS
			newMDFFile.Header.materialFlags = 1
			
		for materialObj in materialObjList:
			materialEntry = Material()
			
			#Reassign previously unknown values from older mdf imports
			if mdfVersion >= 31 and materialObj.re_mdf_material.flags.ver32Unknown != -1:
				materialObj.re_mdf_material.bakeTextureArraySize = int(materialObj.re_mdf_material.shaderType)
				materialObj.re_mdf_material.shaderType = str(materialObj.re_mdf_material.flags.ver32Unknown)
				materialObj.re_mdf_material.flags.ver32Unknown = -1
				
				materialObj.re_mdf_material.flags.flagIntValueB =  materialObj.re_mdf_material.flags.ver32Unknown1
				materialObj.re_mdf_material.flags.ver32Unknown1 = -1
				
				materialObj.re_mdf_material.flags.shaderLODNum =  materialObj.re_mdf_material.flags.ver32Unknown2
				materialObj.re_mdf_material.flags.ver32Unknown2 = -1
				print(f"Corrected old MDF material import: {materialObj.re_mdf_material.materialName}")
			materialEntry.shaderType = int(materialObj.re_mdf_material.shaderType)
			materialEntry.materialName = materialObj.re_mdf_material.materialName
			materialEntry.mmtrPath = materialObj.re_mdf_material.mmtrPath
			#materialEntry.ver32Unkn0 = materialObj.re_mdf_material.flags.ver32Unknown
			#materialEntry.ver32Unkn1 = materialObj.re_mdf_material.flags.ver32Unknown1
			#materialEntry.ver32Unkn2 = materialObj.re_mdf_material.flags.ver32Unknown2
			
			materialEntry.shaderLODNum = materialObj.re_mdf_material.flags.shaderLODNum
			materialEntry.bakeTextureArraySize = materialObj.re_mdf_material.flags.bakeTextureArraySize
			
			materialEntry.hideInGame = materialObj.re_mdf_material.flags.hideMaterialInGame
			
			materialEntry.flags.asInt32 = materialObj.re_mdf_material.flags.flagIntValue
			materialEntry.flagsB.asInt32 = materialObj.re_mdf_material.flags.flagIntValueB
			
			for textureBinding in materialObj.re_mdf_material.textureBindingList_items:
				textureEntry = TextureBinding()
				textureEntry.textureType = textureBinding.textureType
				textureEntry.texturePath = fixTexPath(textureBinding.path)
				materialEntry.textureList.append(textureEntry)
			
			for prop in materialObj.re_mdf_material.propertyList_items:
				propertyEntry = Property()
				propertyEntry.propName = prop.prop_name
				propertyEntry.propValue = getPropValue(prop)
				propertyEntry.padding = prop.padding
				propertyEntry.frontPadding = prop.frontPadding
				propertyEntry.paramCount = len(propertyEntry.propValue)
				materialEntry.propertyList.append(propertyEntry)
				
			if len(materialObj.re_mdf_material.mmtrsData_items) != 0:
				materialEntry.mmtrsData = MMTRSData()
				for item in materialObj.re_mdf_material.mmtrsData_items:
					indexList = []
					rawIndicesList = item.indexString.split(",")
					for entry in rawIndicesList:
						if entry.isdigit():
							indexList.append(int(entry))
					materialEntry.mmtrsData.indexDataList.append(indexList)
					
			if len(materialObj.re_mdf_material.gpbfData_items) != 0:
				for item in materialObj.re_mdf_material.gpbfData_items:
					nameEntry = GPBFEntry()
					pathEntry = GPBFEntry()
					split = item.gpbfDataString.split(",")
					nameEntry.name = split[0]
					pathEntry.name = split[1]
					if split[2].isdigit():
						pathEntry.nameUTF16Hash = int(split[2])
					else:
						pathEntry.nameUTF16Hash = 0
					if split[3].isdigit():
						pathEntry.nameUTF8Hash = int(split[3])
					else:
						pathEntry.nameUTF8Hash = 1
					
					materialEntry.gpbfBufferNameList.append(nameEntry)
					materialEntry.gpbfBufferPathList.append(pathEntry)
			if mdfVersion == 31:#SF6
				#Set altered customize values back to original values since some files do use them
				flipSF6CustomizeValues(materialEntry.propertyList, toGame=True)
			newMDFFile.materialList.append(materialEntry)
		return newMDFFile
	else:
		return None
def exportMDFFile(filepath,mdfCollectionName = "",onlyMaterialNames = None,useBlenderMaterialName = False,exportAllLODs = True):
	mdfVersion = parseFileVersion(filepath, None)
	if mdfVersion is None:
		print("Unable to parse MDF version number in file path.")
	mdfFile = buildMDF(mdfCollectionName,mdfVersion,onlyMaterialNames,useBlenderMaterialName,exportAllLODs)
	if mdfFile != None:
		writeMDF(mdfFile,filepath)
		return True
	else:
		return False