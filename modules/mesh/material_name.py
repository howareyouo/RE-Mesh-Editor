#Author: NSA Cloud
#The material rename engine. The RE engine caps the size of a single mesh, so a model is split
#into several objects that share one material, and a material name therefore lives in two places
#that have to match exactly - the "__" suffix of every mesh object name and the MDF material
#entries - where a mismatch shows up as a checkerboard texture in game.
#
#A rename can come from three doors: the MDF panel field, an outliner rename of a
#"Material NN (name)" empty, and the Rename Meshes / find-and-replace passes. All of them end up
#in applyMaterialRename, so the copies always move together and only the name changes: no
#Group/Sub/LOD prefix, no NN index, no reindexing. A name that can't be used is refused the same
#way whichever door it came from, nothing is renamed then, and the part of the rename the user's
#own edit had already applied is rolled back.
#
#Renaming a material while a rename is being applied would start a second sync from the MDF
#materialName property's update callback, so materialNameSync guards the whole apply.

import bpy

from contextlib import contextmanager

from ..naming import (getMaterialNameRenameError, parseREMeshObjectMaterialName,
                      planMaterialNameReplacement, replaceREMeshMaterialSuffix)

from ..blender_utils import (MDF_MATERIAL_TYPE, getMDFMaterialName, iterExportableMeshObjects,
                             iterMDFMaterialObjects, setMDFMaterialName, setMDFMaterialObjectName)

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

# ------------------ outliner rename snapshot ------------------
#Blender has no callback for data-block renames (and bpy.msgbus doesn't deliver them either), so
#the only way to notice an outliner rename is to compare the current names against a snapshot.

#object pointer -> object name as of the last check
_nameSnapshot = {}

def captureNameSnapshot():
	"""Record the current name of every "Material NN (name)" object.

	That object name is where a user can rename a material without touching any
	property (see findRenamedMDFMaterialObjects)."""
	#bpy.data is a _RestrictData stub while an addon loads, and an empty snapshot only means
	#"no baseline yet" - the first timer tick captures the real one.
	objectList = getattr(bpy.data, "objects", None)
	if objectList == None:
		return {}
	snapshot = {}
	for obj in objectList:
		if obj.get("~TYPE", None) == MDF_MATERIAL_TYPE:
			snapshot[obj.as_pointer()] = obj.name
	return snapshot

def refreshNameSnapshot():
	"""Make the current names the baseline, ignoring renames made until now."""
	global _nameSnapshot
	_nameSnapshot = captureNameSnapshot()

def clearNameSnapshot():
	"""Drop the baseline: every rename from now on is new state."""
	_nameSnapshot.clear()

def getSnapshotObjectName(obj):
	"""Return the name the object had at the last check, or None when it wasn't captured."""
	return _nameSnapshot.get(obj.as_pointer())

def hasNameSnapshot():
	"""True when there is a baseline to compare names against (see captureNameSnapshot)."""
	return bool(_nameSnapshot)

def restoreObjectName(obj, restoreName):
	"""Put the name a rename was refused on back onto the object.

	Blender doesn't just give the old name back: a .NNN suffix is appended when another
	object has taken it in the meantime, which parseMDFObjectNameMaterialName ignores, so
	the material name still resolves the same way. Returns the name it ended up with."""
	obj.name = restoreName
	return obj.name

# ------------------ rename ------------------

def iterMeshesUsingMaterialName(meshCollection, materialName):
	"""Yield the mesh objects of a mesh collection that use the given material name.

	Returns every mesh piece sharing the material, across all LOD collections and including
	export-excluded meshes, so a rename can't leave stale names behind. The comparison is
	case-insensitive."""
	if meshCollection == None or materialName == "":
		return
	targetName = materialName.lower()
	for obj in iterExportableMeshObjects(meshCollection, includeExcluded = True):
		if parseREMeshObjectMaterialName(obj.name).lower() == targetName:
			yield obj

def getMDFMaterialNameConflict(mdfCollection, oldMaterialName, newMaterialName, ignoreMaterialObject = None):
	"""Return a reason when a material can't be renamed to newMaterialName, else None.

	Only the MDF entries are compared: the mesh objects this rename brings along already
	carry the new name, so they can't count as a conflict. ignoreMaterialObject skips the
	entry being renamed, which the MDF panel has already updated by the time its callback
	runs."""
	materialNameList = [getMDFMaterialName(materialObj) for materialObj in iterMDFMaterialObjects(mdfCollection)
	                    if materialObj != ignoreMaterialObject]
	return getMaterialNameRenameError(materialNameList, oldMaterialName, newMaterialName)

def renameMeshObjectsToMaterialName(meshObjList, newMaterialName):
	"""Set the material name part of the given mesh object names.

	Only the text after "__" is replaced (see replaceREMeshMaterialSuffix), so each object keeps
	its LOD and Group/Sub numbering. Returns the objects that changed."""
	renamedObjList = []
	for obj in meshObjList:
		newObjectName = replaceREMeshMaterialSuffix(obj.name, newMaterialName)
		if newObjectName != obj.name:
			obj.name = newObjectName
			renamedObjList.append(obj)
	return renamedObjList


class MaterialRenameRequest:
	"""One material rename, from whichever door it came.

	meshCollection/mdfCollection are the pair the name lives in; either may be None when it
	isn't known, and that side is then left alone. oldMaterialName is the name the material had
	and newMaterialName the one it should get.

	renamedMaterialObject is the MDF material object the panel field or an outliner rename has
	already moved onto the new name, and previousObjectName the name that object had before.
	That object is the source of the rename: it no longer carries the old name, the rest of the
	material follows the name it does carry, and it is put back when the rename is refused (see
	restoreRefusedMaterialRename)."""

	__slots__ = ("meshCollection", "mdfCollection", "oldMaterialName", "newMaterialName",
	             "renamedMaterialObject", "previousObjectName")

	def __init__(self, meshCollection = None, mdfCollection = None, oldMaterialName = "", newMaterialName = "",
	             renamedMaterialObject = None, previousObjectName = None):
		self.meshCollection = meshCollection
		self.mdfCollection = mdfCollection
		self.oldMaterialName = oldMaterialName
		self.newMaterialName = newMaterialName
		self.renamedMaterialObject = renamedMaterialObject
		self.previousObjectName = previousObjectName


class MaterialRenameResult:
	"""What a rename did, or why it didn't.

	renamedMeshObjects are the mesh objects whose names changed, renamedMDFMaterialCount the
	number of MDF material entries whose materialName was rewritten (including the entry the
	rename came from), error the reason the rename was refused, and restoredName the name the
	refused object was put back onto - it differs from the requested one when Blender had to
	suffix it - or None when nothing had to be restored."""

	__slots__ = ("renamedMeshObjects", "renamedMDFMaterialCount", "error", "restoredName")

	def __init__(self, renamedMeshObjects = None, renamedMDFMaterialCount = 0, error = None, restoredName = None):
		self.renamedMeshObjects = renamedMeshObjects if renamedMeshObjects != None else []
		self.renamedMDFMaterialCount = renamedMDFMaterialCount
		self.error = error
		self.restoredName = restoredName


def restoreRefusedMaterialRename(request):
	"""Put what the user's own edit had already moved back where a refused rename found it.

	Returns the object name it was restored to, or None when nothing had to be restored. The
	MDF side has to get the "Material NN (name)" form carrying the old material name back
	before its previous object name is put back, because that name is what the MDF writer
	resolves the material name from - and the property goes back with it, since the panel
	field's write landed before its callback could refuse it."""
	obj = request.renamedMaterialObject
	if obj == None:
		return None
	with materialNameSync():
		setMDFMaterialName(obj, request.oldMaterialName)
		setMDFMaterialObjectName(obj, request.oldMaterialName)
		restoredName = restoreObjectName(obj, request.previousObjectName or obj.name)
	if request.previousObjectName != None and restoredName != request.previousObjectName:
		print(f"RE Mesh Editor: \"{request.previousObjectName}\" was renamed back to \"{restoredName}\" "
		      f"because another object took the old name")
	return restoredName

def applyMaterialRename(request):
	"""Rename one RE material everywhere a mesh/MDF collection pair references it.

	Renames the MDF material entries of the old name plus every mesh object using it, and its
	own materialName when the object it came from is one of them. Nothing is renamed when the
	new name is invalid or already used by another material (see
	getMDFMaterialNameConflict), and the edit the rename came from is rolled back then.

	The names are all resolved before the first rename is applied, so the objects this pass
	renames can't be picked up by a later lookup of the same name."""
	if isMaterialNameSyncActive() or request.oldMaterialName == request.newMaterialName:
		return MaterialRenameResult()
	newName = request.newMaterialName.strip()
	oldKey = request.oldMaterialName.strip().lower()
	meshObjList = list(iterMeshesUsingMaterialName(request.meshCollection, request.oldMaterialName))
	materialObjList = [materialObj for materialObj in iterMDFMaterialObjects(request.mdfCollection)
	                   if materialObj != request.renamedMaterialObject
	                   and getMDFMaterialName(materialObj).strip().lower() == oldKey]
	#Nothing on either side still carries the old name: the pair is already in sync and the new
	#name being taken is not a conflict for this rename. An object already carrying the new name
	#is the exception - it is the rename itself, and its new name has to be checked like any
	#other before the rest of the material is moved onto it.
	if not meshObjList and not materialObjList and request.renamedMaterialObject == None:
		return MaterialRenameResult()
	error = getMDFMaterialNameConflict(request.mdfCollection, request.oldMaterialName, newName,
	                                   request.renamedMaterialObject)
	if error != None:
		return MaterialRenameResult(error = error, restoredName = restoreRefusedMaterialRename(request))
	with materialNameSync():
		renamedObjList = renameMeshObjectsToMaterialName(meshObjList, newName)
		renamedCount = 0
		for materialObj in materialObjList:
			if setMDFMaterialName(materialObj, newName):
				renamedCount += 1
		#The object name is the source of the rename, so its property is written after the
		#meshes: the property's update callback would otherwise start a second sync.
		if request.renamedMaterialObject != None and setMDFMaterialName(request.renamedMaterialObject, newName):
			renamedCount += 1
	return MaterialRenameResult(renamedObjList, renamedCount)

# ------------------ find and replace ------------------
def getMaterialNameReplacePlan(mdfCollection, findValue, replaceValue):
	"""Plan replacing text in the name of every material of an MDF collection.

	Nothing is changed here, so a bulk rename can be refused as a whole instead of leaving half
	of the materials renamed. Returns (planList, errorMessage), where planList holds
	(materialObject, oldName, newName) for the materials the replacement actually changes."""
	materialObjList = list(iterMDFMaterialObjects(mdfCollection))
	planList, error = planMaterialNameReplacement([getMDFMaterialName(materialObj) for materialObj in materialObjList],
	                                              findValue, replaceValue)
	if error != None:
		return [], error
	return [(materialObjList[index], oldName, newName) for index, oldName, newName in planList], None

def replaceMaterialNameText(meshCollection, mdfCollection, findValue, replaceValue):
	"""Replace text in the name of every material of an MDF collection.

	The meshes bound to each material are renamed with it, so the mesh side keeps matching the
	MDF. Names are taken as they are otherwise: no reindexing, no prefix changes. Returns
	(renamedMaterialCount, renamedMeshCount, errorMessage); nothing is renamed when the pass is
	refused (see getMaterialNameReplacePlan)."""
	planList, error = getMaterialNameReplacePlan(mdfCollection, findValue, replaceValue)
	if error != None:
		return 0, 0, error
	if not planList:
		return 0, 0, None
	#Every mesh is resolved before the first rename (see getMaterialNameReplacePlan)
	materialPlanList = [(materialObj, newName, list(iterMeshesUsingMaterialName(meshCollection, oldName)))
	                    for materialObj, oldName, newName in planList]
	renamedMeshCount = 0
	with materialNameSync():
		for materialObj, newName, meshObjList in materialPlanList:
			renamedMeshCount += len(renameMeshObjectsToMaterialName(meshObjList, newName))
			setMDFMaterialName(materialObj, newName)
	return len(planList), renamedMeshCount, None
