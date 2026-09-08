import os
import bpy
import glob
from mathutils import Vector,Quaternion,Matrix
from math import radians
from ..blender_utils import showMessageBox,createRECollection,setAssetPathFromFilePath,checkNameUsage
from ..gen_functions import textColors,raiseWarning,splitNativesPath,parseFileVersion
from .file_re_sfur import readSFur,writeSFur,SFurFile,SFurEntry

def createSFurCollection(collectionName,parentCollection = None):
	collection = createRECollection(
		collectionName, parentCollection,
		color_tag="COLOR_08", customProps={"~TYPE": "RE_SFUR_COLLECTION"})
	bpy.context.scene.re_mdf_toolpanel.sFurCollection = collection
	return collection

def createCurveEmpty(name,propertyList,parent = None,collection = None):
	CURVE_DATA_NAME = "emptyCurve"#Share the data for all empty curves since it's not needed and it prevents unnecessary duplicates
	if CURVE_DATA_NAME in bpy.data.curves:
		curveData = bpy.data.curves[CURVE_DATA_NAME]
	else:	
		curveData = bpy.data.curves.new(CURVE_DATA_NAME, 'CURVE')
		curveData.use_path = False
		
	
	obj = bpy.data.objects.new(name, curveData)
	obj.parent = parent
	for property in propertyList:
 
		obj[property[0]] = property[1]
	if collection == None:
		collection = bpy.context.scene.collection
		
	collection.objects.link(obj)
		
		
	return obj

def reindexEntries(sFurCollection):
	
	if sFurCollection != None:
		
		currentIndex = 0
		for obj in sorted(sFurCollection.all_objects,key = lambda item: item.name):
			
			if obj.get("~TYPE",None) == "RE_SFUR_ENTRY":
				#Change the material name in the mdf material settings to the one in the object name
				#This allows for the user to set the material name by either method of renaming the object or setting it in the mdf material settings
				if "Shell Fur" in obj.name and "(" in obj.name:
					objMaterialName = obj.name.rsplit("(",1)[1].split(")")[0]
					if objMaterialName != obj.re_sfur_data.materialName:
						obj.re_sfur_data.materialName = objMaterialName
				obj.name = "Shell Fur "+str(currentIndex).zfill(2)+ " ("+obj.re_sfur_data.materialName+")"
				currentIndex += 1

def findSFurPathFromMeshPath(meshPath,gameName = None):
	split = meshPath.split(".mesh")
	fileRoot = glob.escape(split[0])
	meshVersion = split[1]
	sFurVersionDict = {
		".221108797":".4",#RE4
		".240423143":".5",#DD2NEW
		".241111606":".5",#MHWILDS
		".250925211":".5",#RE9
		}
	sFurVersion = sFurVersionDict.get(meshVersion,None)
	sFurPath = None
	if meshVersion in sFurVersionDict:
		sFurPath = f"{fileRoot}.sfur{sFurVersion}"
	if sFurPath != None and not os.path.isfile(sFurPath):
		#RE4, check directory above current one
		parentDir = os.path.dirname(os.path.split(meshPath)[0])
		try:
			sFurPath = f"{os.path.join(parentDir,os.path.split(fileRoot)[1])}.sfur{sFurVersion}"
		except:
			pass
		if not os.path.isfile(sFurPath):#Check another directory level above if not the first one
			try:
				sFurPath = f"{os.path.join(os.path.dirname(parentDir),os.path.split(fileRoot)[1])}.sfur{sFurVersion}"
				
			except:
				pass
			
	if sFurPath == None or not os.path.isfile(sFurPath):
		#print("No sfur file found.")
		sFurPath = None
	#print(sFurPath)
	return sFurPath

#Maps re_sfur_data property names to SFurEntry field names, with converters for the
#directions whose types differ (entry values -> property strings -> entry ints).
SFUR_FIELD_MAPPING = (
	("shellCount","shellCount",None,None),
	("shellThinType","shellThinType",str,int),
	("groomingTexCoordType","groomingTexCoordType",str,int),
	("shellHeight","shellHeight",None,None),
	("bendRate","bendRate",None,None),
	("bendRootRate","bendRootRate",None,None),
	("normalTransformRate","normalTransformRate",None,None),
	("stiffness","stiffness",None,None),
	("stiffnessDistribution","stiffnessDistribution",None,None),
	("springCoefficient","springCoefficient",None,None),
	("damping","damping",None,None),
	("gravityForceScale","gravityForceScale",None,None),
	("directWindForceScale","directWindForceScale",None,None),
	("isForceTwoSide","isForceTwoSide",None,None),
	("isForceAlphaTest","isForceAlphaTest",None,None),
	("unknownFlag","padding",None,None),
	("materialName","materialName",None,None),
	("groomingTexturePath","groomingTexturePath",None,None),
	)

#SFUR IMPORT

def importSFurFile(filePath,parentCollection = None):
	
	sFurFile = readSFur(filePath)
	sFurVersion = sFurFile.header.version
	bpy.context.scene["REMeshLastImportedSFurVersion"] = sFurVersion
	fileName = os.path.splitext(os.path.split(filePath)[1])[0]
	sFurCollection = createSFurCollection(fileName,parentCollection)
	
	for index, entry in enumerate(sFurFile.furEntryList):
		name = "Shell Fur "+str(index).zfill(2)+ " ("+entry.materialName+")"
		furObj = createCurveEmpty(name,[("~TYPE","RE_SFUR_ENTRY")],None,sFurCollection)

		for propName,entryField,entryToProp,_ in SFUR_FIELD_MAPPING:
			value = getattr(entry,entryField)
			setattr(furObj.re_sfur_data,propName,entryToProp(value) if entryToProp != None else value)
	
		#TODO Find each submesh using material and add a shell fur geonode modifier to this object for it
	
	setAssetPathFromFilePath(filePath, sFurCollection)
	
	
	
	return True


#SFUR EXPORT


def exportSFurFile(filepath,targetCollection):
	sFurFile = SFurFile()
	sfurVersion = parseFileVersion(filepath, None)
	if sfurVersion is None:
		print("Unable to parse sfur version number in file path, defaulting to 5.")
		sfurVersion = 5
	sFurFile.header.version = sfurVersion
	collection = bpy.data.collections.get(targetCollection,None)
	if collection != None:
		reindexEntries(collection)
		for furObj in collection.all_objects:
			if furObj.get("~TYPE") == "RE_SFUR_ENTRY":
				entry = SFurEntry()
				for propName,entryField,_,propToEntry in SFUR_FIELD_MAPPING:
					value = getattr(furObj.re_sfur_data,propName)
					setattr(entry,entryField,propToEntry(value) if propToEntry != None else value)
				
				sFurFile.furEntryList.append(entry)
				
		writeSFur(sFurFile, filepath)
	return True