#Author: NSA Cloud
import bpy
from math import radians
from mathutils import Matrix

from .gen_functions import textColors, splitNativesPath, raiseWarning
from .naming import (getREMeshMaterialName, parseMDFObjectNameMaterialName,
                     parseREMeshObjectMaterialName, replaceREMeshMaterialSuffix)
import os
from collections import OrderedDict
from contextlib import contextmanager
from itertools import repeat

# Shared rotation matrices (X-axis ±90°) for RE Engine import/export
rotate90Matrix = Matrix.Rotation(radians(90.0), 4, 'X')
rotateNeg90Matrix = Matrix.Rotation(radians(-90.0), 4, 'X')

def getAddonPreferences():
	"""Return this addon's preferences. Uses the actual loaded module name so the
	addon keeps working when its folder is renamed (previously callers scanned
	every installed addon with a substring match on "RE-Mesh-Editor")."""
	return bpy.context.preferences.addons[__package__.split(".")[0]].preferences

def showMessageBox(message = "", title = "Message Box", icon = 'INFO'):

	def draw(self, context):
		self.layout.label(text = message)

	bpy.context.window_manager.popup_menu(draw, title = title, icon = icon)
	
def showErrorMessageBox(message):
	print(textColors.FAIL + "ERROR: " + message + textColors.ENDC)
	showMessageBox(message,title = "Error", icon = "ERROR")

def createRECollection(collectionName, parentCollection=None, color_tag=None, customProps=None):
	"""创建并链接一个 RE 标记集合 (MDF/SFur/Mesh 共用).
	返回新建的 collection; 自定义属性(如 ~TYPE)通过 customProps 传入."""
	collection = bpy.data.collections.new(collectionName)
	if color_tag:
		collection.color_tag = color_tag
	if customProps:
		for key, value in customProps.items():
			collection[key] = value
	if parentCollection is not None:
		parentCollection.children.link(collection)
	else:
		bpy.context.scene.collection.children.link(collection)
	return collection

def getBlenderSafeBoneName(boneName):
	"""Return a Blender-safe bone name, hashing if it exceeds the 63-char limit.
	
	Returns:
		tuple: (safeName, isHashed)
	"""
	if len(boneName) > 63:
		from ..hashing.mmh3.pymmh3 import hashUTF8
		safeName = f"#HASHED_{str(hashUTF8(boneName))}"
		raiseWarning(
			f"Bone name length exceeds Blender's limit of 63 characters, hashing bone name: {boneName}")
		return safeName, True
	return boneName, False

def setAssetPathFromFilePath(filePath, collection):
	"""Parse the natives asset path from filePath and store it on the collection.
	
	Sets collection["~ASSETPATH"] if the file is inside a natives folder.
	"""
	try:
		split = splitNativesPath(filePath)
		if split != None:
			assetPath = os.path.splitext(split[1])[0].replace(os.sep, "/")
			collection["~ASSETPATH"] = assetPath
	except:
		print("Failed to set asset path from file path, file is likely not in a natives folder.")

def resolveSourcePath(storedPath):
	"""Normalise a source file path stored on a collection.

	Returns "" when nothing was stored, or when the folder it points at no longer
	exists, so callers can fall back to another default."""
	if not storedPath:
		return ""
	sourcePath = os.path.abspath(bpy.path.abspath(str(storedPath)))
	if not os.path.isdir(os.path.dirname(sourcePath)):
		return ""
	return sourcePath

# ---------------------------------------------------------------- RE collection helpers
# Mesh/MDF/SFur collections are identified by a "~TYPE" custom property (set on
# import) and/or by their name suffix (.mesh / .mdf2 / .sfur). These helpers are the
# single source of truth for that convention; importers, exporters and operators all
# route through them so the rules can't drift apart.
MDF_COLLECTION_TYPE = "RE_MDF_COLLECTION"
MESH_COLLECTION_TYPE = "RE_MESH_COLLECTION"
SFUR_COLLECTION_TYPE = "RE_SFUR_COLLECTION"
MDF_MATERIAL_TYPE = "RE_MDF_MATERIAL"

def isMDFCollection(collection):
	"""True if the collection is an RE MDF collection (by ~TYPE or .mdf2 name)."""
	return collection != None and (collection.get("~TYPE") == MDF_COLLECTION_TYPE or ".mdf2" in collection.name)

def isMeshCollection(collection):
	"""True if the collection is an RE mesh collection (by ~TYPE or .mesh name)."""
	return collection != None and (collection.get("~TYPE") == MESH_COLLECTION_TYPE or ".mesh" in collection.name)

def isSFurCollection(collection):
	"""True if the collection is an RE SFur collection (by ~TYPE or .sfur name)."""
	return collection != None and (collection.get("~TYPE") == SFUR_COLLECTION_TYPE or ".sfur" in collection.name)

def getMeshCollectionNameFromMDFName(mdfCollectionName):
	"""Derive the mesh collection name from a MDF collection name.

	"<name>.mdf2" -> "<name>.mesh", dropping the optional _v00 / _Mat modifiers
	used by some games' MDF files."""
	return mdfCollectionName.replace(".mdf2", ".mesh", 1).replace("_v00", "", 1).replace("_Mat", "", 1)

def iterMDFMaterialObjects(collection):
	"""Yield the RE_MDF_MATERIAL objects inside a collection (empty if collection is None)."""
	if collection == None:
		return
	for obj in collection.all_objects:
		if obj.get("~TYPE", None) == MDF_MATERIAL_TYPE:
			yield obj

def getMDFMaterialName(materialObj):
	"""Resolve the material name of an RE_MDF_MATERIAL object.

	The "Material NN (name)" object name wins over the materialName property, the
	way the MDF writer resolves it (see syncMDFMaterialNameFromObject), so a rename
	made in the outliner is honoured even before the property is synced."""
	if "Material" in materialObj.name:
		objectMaterialName = parseMDFObjectNameMaterialName(materialObj.name)
		if objectMaterialName != None:
			return objectMaterialName
	return materialObj.re_mdf_material.materialName

def getMDFMaterialNames(mdfCollection):
	"""Return the set of material names held by an MDF collection, read live from the
	scene (so it reflects any renames made after import). Uses the same
	object-name-wins resolution as the MDF writer, so it agrees with the exported file."""
	return set(getMDFMaterialName(obj) for obj in iterMDFMaterialObjects(mdfCollection))

def setMDFMaterialName(materialObj, newName):
	"""Set an MDF material's materialName, skipping no-op writes.

	The property's update callback rewrites the "Material NN (name)" object name,
	so this is the single write path for programmatic renames (Rename Meshes,
	Reindex Materials). Returns True when the name actually changed."""
	if newName == materialObj.re_mdf_material.materialName:
		return False
	materialObj.re_mdf_material.materialName = newName
	return True

def iterMeshLODCollections(meshCollection, exportAllLODs = True):
	"""Return the collections the mesh exporter processes, in export order.

	Mirrors exportREMeshFile: the "Main Mesh LOD" child collections sorted by name,
	falling back to the mesh collection itself, truncated to LOD 0 when exportAllLODs
	is off. Kept here so the exporter and the exclude-unused material filter always
	agree on which meshes are exported. Empty for a None collection."""
	if meshCollection == None:
		return []
	lodCollectionList = []
	for childCollection in meshCollection.children:
		if "Main Mesh LOD" in childCollection.name:
			lodCollectionList.append(childCollection)
	if lodCollectionList == []:
		lodCollectionList = [meshCollection]
	lodCollectionList.sort(key = lambda collection: collection.name)
	if not exportAllLODs:
		lodCollectionList = lodCollectionList[:1]
	return lodCollectionList

def iterCollectionMeshObjects(collection, selectedOnly = False, includeExcluded = False):
	"""Yield the mesh objects the exporter writes from one collection.

	Skips non-mesh objects, objects flagged with MeshExportExclude and, when
	selectedOnly is set, unselected ones. Only the collection's own objects are
	considered (no recursion), matching the exporter's per-LOD loop. includeExcluded
	keeps the export-excluded meshes, which name-based operations still have to see."""
	selectedObjectList = bpy.context.selected_objects if selectedOnly else None
	for obj in collection.objects:
		if obj.type != "MESH" or (not includeExcluded and obj.get("MeshExportExclude")):
			continue
		if selectedOnly and obj not in selectedObjectList:
			continue
		yield obj

def iterExportableMeshObjects(meshCollection, selectedOnly = False, exportAllLODs = True, includeExcluded = False):
	"""Yield the mesh objects the mesh exporter writes, in export order.

	Single definition of "the export contains this object": the LOD collections the
	exporter iterates (see iterMeshLODCollections), filtered by iterCollectionMeshObjects."""
	for lodCollection in iterMeshLODCollections(meshCollection, exportAllLODs):
		for obj in iterCollectionMeshObjects(lodCollection, selectedOnly, includeExcluded):
			yield obj

def getMeshMaterialNames(meshCollection, useBlenderMaterialName = False, selectedOnly = False, exportAllLODs = True):
	"""Return the set of material names used by the meshes that will be exported.

	Walks the same objects as exportREMeshFile (see iterExportableMeshObjects) and
	shares getREMeshMaterialName with it, so the result matches the materials in the
	exported file: the name after "__" in the object name, or the object's first
	Blender material when useBlenderMaterialName is set (each falling back to the
	other)."""
	meshMaterialNameSet = set()
	for obj in iterExportableMeshObjects(meshCollection, selectedOnly, exportAllLODs):
		materialName = getREMeshMaterialName(obj, useBlenderMaterialName = useBlenderMaterialName, fallback = "")
		if materialName != "":
			meshMaterialNameSet.add(materialName)
	return meshMaterialNameSet

def normalizeMaterialNameFilter(onlyMaterialNames):
	"""Normalise a material-name filter so an empty set means the same as no filter.

	The mesh-exclude-unused filter may legitimately be empty (the mesh uses no exportable
	materials). An empty set must behave identically to None - "export every material" -
	wherever it is consumed, otherwise the MDF checks and the per-material filter disagree.
	Returns a fresh set, or None when empty."""
	if onlyMaterialNames:
		return set(onlyMaterialNames)
	return None

def findMDFCollectionForMesh(meshCollection):
	"""Find the MDF collection associated with a mesh collection.

	Resolution order:
	1. Naming convention: <base>.mdf2 / <base>_Mat.mdf2 / <base>_v00.mdf2
	2. A sibling MDF collection under the same parent collection
	3. The MDF collection currently selected in the MDF tool panel
	Returns None if no MDF collection is found."""
	if meshCollection == None:
		return None
	# 1. Naming convention
	baseName = meshCollection.name
	if baseName.endswith(".mesh"):
		baseName = baseName[:-len(".mesh")]
	for candidate in (f"{baseName}.mdf2", f"{baseName}_Mat.mdf2", f"{baseName}_v00.mdf2"):
		collection = bpy.data.collections.get(candidate)
		if collection != None:
			return collection
	# 2. Sibling MDF collection under the same parent collection
	for parent in (col for col in bpy.data.collections if meshCollection.name in col.children):
		for child in parent.children:
			if isMDFCollection(child):
				return child
	# 3. MDF collection selected in the MDF tool panel
	panel = getattr(bpy.context.scene, "re_mdf_toolpanel", None)
	if panel != None and panel.mdfCollection != None and panel.meshCollection == meshCollection:
		return panel.mdfCollection
	return None

def iterParentCollections(collection):
	"""Yield the collections that contain the given collection.

	Collections have no parent pointer in Blender, so this scans every collection."""
	if collection == None:
		return
	for candidate in bpy.data.collections:
		if collection.name in candidate.children:
			yield candidate

def findMeshCollectionForObject(obj):
	"""Return the RE mesh collection an object's meshes are exported from.

	Imported objects are usually linked to a per-LOD child collection ("Main Mesh
	LOD0 - Body.mesh"), and those children match isMeshCollection too, so this walks
	up to the outermost mesh collection of the chain. Returns None when the object
	isn't inside a mesh collection."""
	for collection in obj.users_collection:
		current = collection
		while isMeshCollection(current):
			parentCollection = next(
				(parent for parent in iterParentCollections(current) if isMeshCollection(parent)), None)
			if parentCollection == None:
				break
			current = parentCollection
		if isMeshCollection(current):
			return current
	return None

def findMeshCollectionForMDF(mdfCollection):
	"""Find the mesh collection that belongs to an MDF collection.

	The reverse of findMDFCollectionForMesh, resolved from the same sources in the
	same order: the naming convention, a sibling mesh collection under the same
	parent (accepted only when it maps back to mdfCollection) and the mesh collection
	picked in the MDF tool panel. Returns None when there is no association."""
	if mdfCollection == None:
		return None
	collection = bpy.data.collections.get(getMeshCollectionNameFromMDFName(mdfCollection.name))
	if isMeshCollection(collection):
		return collection
	for parent in iterParentCollections(mdfCollection):
		for child in parent.children:
			if isMeshCollection(child) and findMDFCollectionForMesh(child) == mdfCollection:
				return child
	panel = getattr(bpy.context.scene, "re_mdf_toolpanel", None)
	if panel != None and panel.mdfCollection == mdfCollection:
		return panel.meshCollection
	return None

# ---------------------------------------------------------------- material name sync
# The RE engine caps the size of a single mesh, so a model is split into several
# objects that share one material. A material name therefore lives in two places that
# have to match exactly - the "__" suffix of every mesh object name and the MDF
# material entries - and a mismatch shows up as a checkerboard texture in game.
# Everything that renames a material goes through renameREMeshMaterial so all copies
# move together and only the name changes: no Group/Sub/LOD prefix, no NN index.

_materialNameSyncDepth = 0

def isMaterialNameSyncActive():
	"""True while a material rename is being applied.

	Writing an MDF material's materialName fires the property's update callback, which
	would restart the sync for the rename it is already part of, so nested calls bail
	out instead of repeating the work."""
	return _materialNameSyncDepth > 0

@contextmanager
def materialNameSync():
	"""Run a block whose material name writes must not start a nested sync."""
	global _materialNameSyncDepth
	_materialNameSyncDepth += 1
	try:
		yield
	finally:
		_materialNameSyncDepth = max(0, _materialNameSyncDepth - 1)

def iterMeshesUsingMaterialName(meshCollection, materialName):
	"""Yield the mesh objects of a mesh collection that use the given material name.

	Returns every mesh piece sharing the material, across all LOD collections and
	including export-excluded meshes, so a rename can't leave stale names behind.
	The comparison is case-insensitive."""
	if meshCollection == None or materialName == "":
		return
	targetName = materialName.lower()
	for obj in iterExportableMeshObjects(meshCollection, includeExcluded = True):
		if parseREMeshObjectMaterialName(obj.name).lower() == targetName:
			yield obj

def getMDFMaterialNameConflict(mdfCollection, oldMaterialName, newMaterialName, ignoreMaterialObject = None):
	"""Return a reason when a material can't be renamed to newMaterialName, else None.

	An MDF holds every material name once and the meshes bind to it by name, so a
	duplicate would silently bind meshes to the wrong material. Only the MDF entries
	are compared: mesh objects already carrying the new name are the very pieces this
	rename brings together (and the one the user just typed it into), so they can't
	count as a conflict. ignoreMaterialObject skips the entry being renamed, which the
	MDF panel has already updated by the time its callback runs."""
	newName = newMaterialName.strip()
	if newName == "":
		return "Material name can't be empty."
	if "__" in newName:
		return "\"__\" can't be part of a material name, it separates the mesh name from the material name."
	targetKey = newName.lower()
	oldKey = oldMaterialName.strip().lower()
	for materialObj in iterMDFMaterialObjects(mdfCollection):
		if materialObj == ignoreMaterialObject:
			continue
		materialKey = getMDFMaterialName(materialObj).strip().lower()
		if materialKey == targetKey and materialKey != oldKey:
			return f"\"{newName}\" is already used by another material in the MDF collection."
	return None

def renameMeshObjectsToMaterialName(meshObjList, newMaterialName):
	"""Set the material name part of the given mesh object names.

	Only the text after "__" is replaced (see replaceREMeshMaterialSuffix), so each
	object keeps its LOD and Group/Sub numbering. Returns the objects that changed."""
	renamedObjList = []
	for obj in meshObjList:
		newObjectName = replaceREMeshMaterialSuffix(obj.name, newMaterialName)
		if newObjectName != obj.name:
			obj.name = newObjectName
			renamedObjList.append(obj)
	return renamedObjList

def renameMeshesUsingMaterialName(meshCollection, oldMaterialName, newMaterialName):
	"""Rename every mesh in the collection that uses oldMaterialName.

	Returns the renamed objects. A caller that renames several materials in one pass
	has to resolve the objects first and use renameMeshObjectsToMaterialName instead:
	a replacement can contain the text it replaces, so a later lookup by name could
	catch objects the pass has already renamed."""
	if meshCollection == None or oldMaterialName == "":
		return []
	return renameMeshObjectsToMaterialName(list(iterMeshesUsingMaterialName(meshCollection, oldMaterialName)), newMaterialName)

def renameREMeshMaterial(meshCollection, mdfCollection, oldMaterialName, newMaterialName, ignoreMaterialObject = None):
	"""Rename one RE material everywhere a mesh/MDF collection pair references it.

	Renames the MDF material entry of that name plus every mesh object using it, and
	nothing else: no reindexing, no prefix changes. Nothing is renamed when the new
	name is invalid or already taken (see getMDFMaterialNameConflict).

	Returns (renamedMeshObjects, renamedMDFMaterialCount, errorMessage)."""
	if isMaterialNameSyncActive():
		return [], 0, None
	newName = newMaterialName.strip()
	oldKey = oldMaterialName.strip().lower()
	meshObjList = list(iterMeshesUsingMaterialName(meshCollection, oldMaterialName))
	materialObjList = [materialObj for materialObj in iterMDFMaterialObjects(mdfCollection)
	                   if materialObj != ignoreMaterialObject and getMDFMaterialName(materialObj).strip().lower() == oldKey]
	# Nothing on either side still carries the old name: the pair is already in sync and
	# the new name being taken is not a conflict for this rename.
	if not meshObjList and not materialObjList:
		return [], 0, None
	error = getMDFMaterialNameConflict(mdfCollection, oldMaterialName, newName, ignoreMaterialObject)
	if error != None:
		return [], 0, error
	with materialNameSync():
		renamedObjList = renameMeshesUsingMaterialName(meshCollection, oldMaterialName, newName)
		renamedCount = 0
		for materialObj in materialObjList:
			if setMDFMaterialName(materialObj, newName):
				renamedCount += 1
	return renamedObjList, renamedCount, None

def syncMaterialNamesFromMDFMaterial(materialObj, oldMaterialName, newMaterialName):
	"""Push a material rename made in the MDF panel onto the meshes that use it.

	The mesh side of a material name is spread over several objects, so the MDF
	material entry is the entry point a user can't miss; renaming it renames the mesh
	pieces immediately. Returns (renamedMeshObjectCount, errorMessage) - the caller has
	to undo its own property write when the rename was refused."""
	if oldMaterialName == newMaterialName or isMaterialNameSyncActive():
		return 0, None
	mdfCollection = next((collection for collection in materialObj.users_collection if isMDFCollection(collection)), None)
	meshCollection = findMeshCollectionForMDF(mdfCollection)
	if mdfCollection == None and meshCollection == None:
		return 0, None
	renamedObjList, renamedCount, error = renameREMeshMaterial(
		meshCollection, mdfCollection, oldMaterialName, newMaterialName, ignoreMaterialObject = materialObj)
	return len(renamedObjList), error

def getMaterialNameReplacePlan(mdfCollection, findValue, replaceValue):
	"""Plan replacing text in the name of every material of an MDF collection.

	Nothing is changed here, so a bulk rename can be refused as a whole instead of
	leaving half of the materials renamed. Returns (planList, errorMessage), where
	planList holds (materialObject, oldName, newName) for the materials the
	replacement actually changes."""
	if findValue == "":
		return [], "Find text can't be empty."
	planList = []
	#Keyed by the lower-cased new name, which is how material names are compared, so the
	#error messages can still spell the names the way the user typed them.
	newNameKeyDict = {}
	untouchedKeySet = set()
	for materialObj in iterMDFMaterialObjects(mdfCollection):
		oldName = getMDFMaterialName(materialObj)
		if findValue not in oldName:
			untouchedKeySet.add(oldName.strip().lower())
			continue
		newName = oldName.replace(findValue, replaceValue).strip()
		if newName == "":
			return [], f"\"{oldName}\" would become an empty material name."
		if "__" in newName:
			return [], f"\"{newName}\" can't contain \"__\", it separates the mesh name from the material name."
		newNameKey = newName.lower()
		if newNameKey in newNameKeyDict:
			return [], f"\"{newName}\" would be the name of two materials: \"{newNameKeyDict[newNameKey][0]}\" and \"{oldName}\"."
		newNameKeyDict[newNameKey] = (oldName, newName)
		planList.append((materialObj, oldName, newName))
	clashKeyList = sorted(set(newNameKeyDict) & untouchedKeySet)
	if clashKeyList:
		return [], f"\"{newNameKeyDict[clashKeyList[0]][1]}\" is already used by another material in the MDF collection."
	return planList, None

def replaceMaterialNameText(meshCollection, mdfCollection, findValue, replaceValue):
	"""Replace text in the name of every material of an MDF collection.

	The meshes bound to each material are renamed with it, so the mesh side keeps
	matching the MDF. Names are taken as they are otherwise: no reindexing, no prefix
	changes. Returns (renamedMaterialCount, renamedMeshCount, errorMessage); nothing is
	renamed when the pass is refused (see getMaterialNameReplacePlan)."""
	planList, error = getMaterialNameReplacePlan(mdfCollection, findValue, replaceValue)
	if error != None:
		return 0, 0, error
	if not planList:
		return 0, 0, None
	#Resolve every mesh before the first rename: a replacement can contain the text it
	#replaces, so looking names up while renaming would catch objects of a later entry.
	materialPlanList = [(materialObj, newName, list(iterMeshesUsingMaterialName(meshCollection, oldName)))
	                    for materialObj, oldName, newName in planList]
	renamedMeshCount = 0
	with materialNameSync():
		for materialObj, newName, meshObjList in materialPlanList:
			renamedMeshCount += len(renameMeshObjectsToMaterialName(meshObjList, newName))
			setMDFMaterialName(materialObj, newName)
	return len(planList), renamedMeshCount, None

def createEmpty(name, propertyList, parent=None, collection=None):
	"""Create a Blender Empty object with display type PLAIN_AXES, custom
	properties, and link to a collection."""
	obj = bpy.data.objects.new(name, None)
	obj.empty_display_size = .10
	obj.empty_display_type = 'PLAIN_AXES'
	obj.parent = parent
	for property in propertyList:
		obj[property[0]] = property[1]
	if collection == None:
		collection = bpy.context.scene.collection
	collection.objects.link(obj)
	return obj

def printEditorHeader(bl_info):
	"""Print the RE Mesh Editor version header to console."""
	editorVersion = str(bl_info["version"][0])+"."+str(bl_info["version"][1])
	print(f"\n{textColors.BOLD}RE Mesh Editor V{editorVersion}{textColors.ENDC}")
	print(f"Blender Version {bpy.app.version[0]}.{bpy.app.version[1]}.{bpy.app.version[2]}")
	print("https://github.com/NSACloud/RE-Mesh-Editor")

def checkNameUsage(baseName, checkSubString=True, objList=None):
	if objList is None:
		objList = bpy.data.objects
	if checkSubString:
		return any(baseName in obj.name for obj in objList)  #Generator: no full name list materialized
	else:
		return any(obj.name == baseName for obj in objList)

def tag_redraw(context, space_type="PROPERTIES", region_type="WINDOW"):
	for window in context.window_manager.windows:
		for area in window.screen.areas:
			if area.spaces[0].type == space_type:
				for region in area.regions:
					if region.type == region_type:
						region.tag_redraw()
	
#--------------------------------
#Node arrange by JuhaW
#https://github.com/blender/blender-addons/blob/main/node_arrange.py
class values():
	average_y = 0
	x_last = 0
	margin_x = 100
	mat_name = ""
	margin_y = 20

def outputnode_search(ntree):
	outputnodes = [n for n in ntree.nodes if not n.outputs and any(i.is_linked for i in n.inputs)]
	if not outputnodes:
		print("No output node found")
		return None
	return outputnodes

def nodes_odd(ntree, nodelist):

	nodes = ntree.nodes
	for i in nodes:
		i.select = False

	a = [x for x in nodes if x not in nodelist]
	for i in a:
		i.select = True

def nodes_arrange(nodelist, level, ntree):

	parents = []
	for node in nodelist:
		parents.append(node.parent)
		node.parent = None
	ntree.nodes.update()

	widthmax = max([x.dimensions.x for x in nodelist])
	xpos = values.x_last - (widthmax + values.margin_x) if level != 0 else 0
	values.x_last = xpos

	# node y positions
	x = 0
	y = 0

	for node in nodelist:
		if node.hide:
			hidey = (node.dimensions.y / 2) - 8
			y = y - hidey
		else:
			hidey = 0

		node.location.y = y
		y = y - values.margin_y - node.dimensions.y + hidey
		node.location.x = xpos

	y = y + values.margin_y
	center = (0 + y) / 2
	values.average_y = center - values.average_y

	for i, node in enumerate(nodelist):
		node.parent = parents[i]

def nodes_iterate(ntree, arrange=True):
	nodeoutput = outputnode_search(ntree)
	if nodeoutput is None:
		return None
	a = []
	a.append([])
	for i in nodeoutput:
		a[0].append(i)

	level = 0
	while a[level]:
		a.append([])
		for node in a[level]:
			inputlist = [i for i in node.inputs if i.is_linked]
			if inputlist:
				for input in inputlist:
					for nlinks in input.links:
						node1 = nlinks.from_node
						a[level + 1].append(node1)
			else:
				pass

		level += 1

	del a[level]
	level -= 1

	#remove duplicate nodes at the same level, first wins
	for x, nodes in enumerate(a):
		a[x] = list(OrderedDict(zip(a[x], repeat(None))))

	#remove duplicate nodes in all levels, last wins
	top = level
	seen = set()
	for row in range(top, 0, -1):
		kept = []
		for node in a[row]:
			if node in seen:
				continue
			seen.add(node)
			kept.append(node)
		a[row] = kept

	if not arrange:
		nodelist = [j for i in a for j in i]
		nodes_odd(ntree, nodelist=nodelist)
		return None

	levelmax = level + 1
	level = 0
	values.x_last = 0

	while level < levelmax:
		values.average_y = 0
		nodes = [x for x in a[level]]
		nodes_arrange(nodes, level, ntree)
		level = level + 1

	return None

def nodes_center(ntree):
	bboxminx = []
	bboxmaxx = []
	bboxmaxy = []
	bboxminy = []
	for node in ntree.nodes:
		if not node.parent:
			bboxminx.append(node.location.x)
			bboxmaxx.append(node.location.x + node.dimensions.x)
			bboxmaxy.append(node.location.y)
			bboxminy.append(node.location.y - node.dimensions.y)

	bboxminx = min(bboxminx)
	bboxmaxx = max(bboxmaxx)
	bboxminy = min(bboxminy)
	bboxmaxy = max(bboxmaxy)
	center_x = (bboxminx + bboxmaxx) / 2
	center_y = (bboxminy + bboxmaxy) / 2

	for node in ntree.nodes:

		if not node.parent:
			node.location.x -= center_x
			node.location.y += -center_y

def arrangeNodeTree(ntree,margin_x = 100,margin_y = 20,centerNodes = True):

#TODO Fix, blender doesn't initialize node dimensions unless the shader editor is open

	values.margin_x = margin_x
	values.margin_y = margin_y

	ntree.nodes.update()
	#first arrange nodegroups
	n_groups = [n for n in ntree.nodes if n.type == 'GROUP']

	while n_groups:
		j = n_groups.pop(0)
		nodes_iterate(j.node_tree)
		for i in j.node_tree.nodes:
			if i.type == 'GROUP':
				n_groups.append(i)

	nodes_iterate(ntree)

	# arrange nodes + this center nodes together
	if centerNodes:
		nodes_center(ntree)
#--------------------------------