#Author: NSA Cloud
#Keeps the material name inside a mesh object name in sync with the MDF material of
#the same name, in both directions, when an object is renamed in the outliner.
#
#Blender has no callback for data-block renames (and bpy.msgbus doesn't deliver them
#either), so the only way to notice a rename is to compare the current names against a
#snapshot. The comparison runs on a timer instead of a depsgraph handler: an outliner
#rename doesn't reliably tag a depsgraph update, and a timer also catches renames made
#by undo/redo or by other addons.

import bpy

from ..blender_utils import (MDF_MATERIAL_TYPE, findMDFCollectionForMaterialObject,
                             findMDFCollectionForMesh, findMeshCollectionForMDF,
                             findMeshCollectionForObject, getMDFMaterialName,
                             getMDFMaterialNameConflict, getToolPanelFlag,
                             iterAllMDFMaterialObjects, materialNameSync,
                             renameREMeshMaterial, setMDFMaterialName, setMDFMaterialObjectName)
from ..naming import parseMDFObjectNameMaterialName, parseREMeshObjectMaterialName, replaceREMeshMaterialSuffix

POLL_INTERVAL = 0.5

#object pointer -> object name as of the last check
_nameSnapshot = {}


def captureNameSnapshot():
	"""Record the current name of every object a material name lives on.

	Both sides are needed: the "__" suffix of the mesh objects and the
	"Material NN (name)" name of the MDF material objects, which is where a user can
	rename a material without touching any property (see findRenamedMDFMaterialObjects)."""
	#bpy.data is a _RestrictData stub while an addon loads, and an empty snapshot only
	#means "no baseline yet" - the first timer tick captures the real one.
	objectList = getattr(bpy.data, "objects", None)
	if objectList == None:
		return {}
	snapshot = {}
	for obj in objectList:
		if obj.type == "MESH" or obj.get("~TYPE", None) == MDF_MATERIAL_TYPE:
			snapshot[obj.as_pointer()] = obj.name
	return snapshot


def refreshNameSnapshot():
	"""Make the current names the baseline, ignoring renames made until now."""
	global _nameSnapshot
	_nameSnapshot = captureNameSnapshot()


def clearSnapshotEntry(obj):
	"""Drop one object from the baseline, so what happens to its name next - including
	being renamed by someone else - is new state and not a user rename."""
	global _nameSnapshot
	_nameSnapshot.pop(obj.as_pointer(), None)


def restoreObjectName(obj, restoreName):
	"""Put a name a rename was refused on back onto the object.

	Blender doesn't just give the old name back: a .NNN suffix is appended when another
	object has taken it in the meantime (e.g. a duplicated mesh in the same tick), and
	the data-block name is refused outright while its mesh datablock still holds it. A
	failed restore raises into the timer and stops the whole sync pass, so both cases are
	handled here - the exception is the datablock case, where a different name is the
	only way to get the object back off the refused one. Returns the name it ended up
	with (see stripBlenderUserSuffix, which keeps ".NNN" suffixes out of the material
	name), so the caller can log it."""
	try:
		obj.name = restoreName
	except Exception:
		#Blender refuses the name only while the object's own mesh datablock still holds it
		#(the rest of the collision cases are resolved with a suffix instead), which is the
		#datablock case: release the name there and try again.
		meshData = getattr(obj, "data", None)
		if meshData == None:
			raise
		meshData.name = getattr(meshData, "name", "") + "_mesh"
		obj.name = restoreName
	#The object is clean again, so the rollback inside the next tick must not be taken for
	#a fresh rename - of the material name or of any sibling that was let go to restore it
	for otherObj in bpy.data.objects:
		previousName = _nameSnapshot.get(otherObj.as_pointer())
		if previousName == None or otherObj.name in (previousName, restoreName):
			continue
		if parseREMeshObjectMaterialName(otherObj.name) == parseREMeshObjectMaterialName(restoreName):
			clearSnapshotEntry(otherObj)
	return obj.name


def findRenamedMeshMaterials():
	"""Return (object, oldMaterialName, newMaterialName) for mesh renames since the baseline.

	Reads the snapshot without touching it, so a caller can decide what to do first."""
	renameList = []
	for obj in bpy.data.objects:
		if obj.type != "MESH":
			continue
		oldObjectName = _nameSnapshot.get(obj.as_pointer())
		if oldObjectName == None or oldObjectName == obj.name:
			continue
		oldMaterialName = parseREMeshObjectMaterialName(oldObjectName)
		newMaterialName = parseREMeshObjectMaterialName(obj.name)
		#Renaming the Group/Sub/LOD prefix doesn't touch the material name
		if oldMaterialName == "" or newMaterialName == "" or oldMaterialName == newMaterialName:
			continue
		renameList.append((obj, oldMaterialName, newMaterialName))
	return renameList


def getMeshRenameConflict(obj, oldMaterialName, newMaterialName):
	"""Return a reason when a mesh rename can't become a material, else None.

	Only renames the mesh side can push along need a material to land on: a mesh name
	whose suffix names no material at all is taken as a request to create one, which is
	the same case as a mesh imported under a name the MDF doesn't hold yet (see
	MDFErrorCheck, which reports it without refusing the rename)."""
	mdfCollection = findMDFCollectionForMesh(findMeshCollectionForObject(obj))
	return getMDFMaterialNameConflict(mdfCollection, oldMaterialName, newMaterialName, ignoreMeshObject = obj)


def rollbackMeshMaterialName(obj, oldMaterialName):
	"""Put a mesh name a refused rename left behind back onto its old material name."""
	restoredName = replaceREMeshMaterialSuffix(obj.name, oldMaterialName)
	restoredName = restoreObjectName(obj, restoredName)
	if parseREMeshObjectMaterialName(restoredName) != oldMaterialName:
		print(f"RE Mesh Editor: \"{obj.name}\" was renamed back to \"{restoredName}\", "
		      f"\"{oldMaterialName}\" was taken")


def syncRenamedMeshMaterial(obj, oldMaterialName, newMaterialName):
	"""Push a material rename made by renaming a mesh object onto the rest of its material.

	The outliner rename has already moved obj onto the new material name, so it is passed
	along: the name it now carries is what has to be checked for a conflict, and the rest
	of the material follows it. A refused rename is undone, and the rename count decides
	whether anything happened - the object itself is always already renamed."""
	meshCollection = findMeshCollectionForObject(obj)
	if meshCollection == None:
		return False
	renamedObjList, renamedMDFCount, error = renameREMeshMaterial(
		meshCollection, findMDFCollectionForMesh(meshCollection), oldMaterialName, newMaterialName,
		renamedMeshObject = obj)
	if error != None:
		print(f"RE Mesh Editor: material name not synced from \"{obj.name}\": {error}")
		#A refused rename is undone: the outliner can't veto it, and a mesh name with no
		#matching material would fail the export check anyway.
		rollbackMeshMaterialName(obj, oldMaterialName)
		return False
	return bool(renamedObjList or renamedMDFCount)


def refuseRenamedMeshMaterial(obj, oldMaterialName, newMaterialName):
	"""Undo a mesh rename that would give a used name to a second mesh.

	The mesh to material direction is off by default, but a name that is already used is
	not a sync the option should be able to switch off: the other mesh keeps it, so this
	one would end up bound to a material it doesn't own and both meshes would be exported
	under one name. Only a rename onto a name the MDF holds is checked - see
	getMeshRenameConflict. Returns True when the rename was undone."""
	error = getMeshRenameConflict(obj, oldMaterialName, newMaterialName)
	if error == None:
		return False
	print(f"RE Mesh Editor: material name not synced from \"{obj.name}\": {error}")
	rollbackMeshMaterialName(obj, oldMaterialName)
	return True


def findRenamedMDFMaterialObjects():
	"""Return (object, oldObjectName, oldMaterialName, newMaterialName) for MDF material
	object renames since the baseline.

	newMaterialName is None when the new name no longer follows the "Material NN (name)"
	convention, so the caller can decide how to resolve the material name instead.

	Reads the snapshot without touching it, so a caller can decide what to do first."""
	renameList = []
	for obj in iterAllMDFMaterialObjects():
		oldObjectName = _nameSnapshot.get(obj.as_pointer())
		if oldObjectName == None or oldObjectName == obj.name:
			continue
		oldMaterialName = parseMDFObjectNameMaterialName(oldObjectName)
		#Nothing to carry over when the baseline name wasn't the conventional form: the
		#materialName property is the name of the material in that case
		if oldMaterialName == None:
			continue
		newMaterialName = parseMDFObjectNameMaterialName(obj.name)
		#Only the NN index changed, the material name is still the same
		if newMaterialName == oldMaterialName:
			continue
		renameList.append((obj, oldObjectName, oldMaterialName, newMaterialName))
	return renameList


def syncRenamedMDFMaterialObject(obj, oldObjectName, oldMaterialName, newMaterialName):
	"""Push a material rename made by renaming an MDF material object onto the meshes.

	This is the third way to rename a material, next to the MDF panel field and the mesh
	name: renaming the "Material NN (name)" empty in the outliner. The MDF writer resolves
	the name from that object name (see getMDFMaterialName), so the property and the
	meshes have to follow it. Returns 1 when a rename was applied, else 0."""
	if newMaterialName == None:
		#Not the "Material NN (name)" form, so this is not a material name: the property
		#still is, and putting the convention back keeps the object name readable - the
		#same resolution the MDF writer and reindexMaterials use.
		typedName = obj.name
		keptName = getMDFMaterialName(obj)
		keptObjectName = setMDFMaterialObjectName(obj, keptName)
		print(f"RE Mesh Editor: \"{oldObjectName}\" renamed to \"{typedName}\" doesn't follow "
		      f"\"Material NN (name)\", kept \"{keptName}\" ({keptObjectName})")
		return 0
	mdfCollection = findMDFCollectionForMaterialObject(obj)
	meshCollection = findMeshCollectionForMDF(mdfCollection)
	#The renamed object is skipped as an ignore target: it already carries the new name, so
	#looking it up under the old name finds nothing. It still has to be reported as the source
	#of the rename, or the rollback short circuit would take a refused rename for a done one.
	renamedObjList, renamedMDFCount, error = renameREMeshMaterial(
		meshCollection, mdfCollection, oldMaterialName, newMaterialName,
		ignoreMaterialObject = obj, renamedMaterialObject = obj)
	if error != None:
		print(f"RE Mesh Editor: material name not synced from \"{obj.name}\": {error}")
		#A refused rename is undone, like the mesh side: a duplicate name in the MDF would
		#bind the meshes to the wrong material. The object name is the source of this rename,
		#so it is put back on a "Material NN (name)" carrying the old material name first -
		#the rollback has to keep that convention even when the old object name was taken.
		setMDFMaterialObjectName(obj, oldMaterialName)
		restoredName = restoreObjectName(obj, oldObjectName)
		if restoredName != oldObjectName:
			print(f"RE Mesh Editor: \"{oldObjectName}\" was renamed back to \"{restoredName}\" "
			      f"because another object took the old name")
		return 0
	#The object name is the source of this rename, so the property is written after the
	#meshes: the property's update callback would otherwise start a second sync.
	with materialNameSync():
		propertyChanged = setMDFMaterialName(obj, newMaterialName)
	#Nothing left to change means the rename was already applied - e.g. by the mesh pass
	#of the same tick, which renames the MDF material of a renamed mesh
	return 1 if (renamedObjList or renamedMDFCount or propertyChanged) else 0


def syncRenamedMaterialNames(syncMeshRenames = True):
	"""Apply material name changes made by renames in the outliner to the whole pair.

	Both directions are read from the same snapshot before anything is renamed, because
	a rename on one side renames the other side too - reading again between the passes
	would see our own writes as new user renames. Returns the number of synced renames.

	syncMeshRenames is the "Auto Sync Material Names" option in the RE MDF Tools panel:
	renaming a material always renames the meshes, the option only controls the mesh to
	material direction. Turning it off stops the sync, not the duplicate name check: a
	rename onto a material name another mesh already uses is undone either way."""
	global _nameSnapshot
	syncedCount = 0
	meshRenameList = findRenamedMeshMaterials()
	materialRenameList = findRenamedMDFMaterialObjects()
	try:
		for obj, oldMaterialName, newMaterialName in meshRenameList:
			if syncMeshRenames:
				if syncRenamedMeshMaterial(obj, oldMaterialName, newMaterialName):
					syncedCount += 1
				continue
			refuseRenamedMeshMaterial(obj, oldMaterialName, newMaterialName)
		for obj, oldObjectName, oldMaterialName, newMaterialName in materialRenameList:
			syncedCount += syncRenamedMDFMaterialObject(obj, oldObjectName, oldMaterialName, newMaterialName)
	finally:
		#Re-capture unconditionally: our own renames must not look like user renames on
		#the next tick, and anything renamed while we were working shows up next pass.
		_nameSnapshot = captureNameSnapshot()
	return syncedCount


def _pollMaterialNameRenames():
	try:
		if not _nameSnapshot:
			refreshNameSnapshot()
		else:
			#The mesh to material direction is the only one the option controls, and it is off by default
			syncRenamedMaterialNames(getToolPanelFlag("autoSyncMaterialNames", default = False))
	except Exception as error:
		#A failing timer would be unregistered by Blender, so keep it alive and report
		print(f"RE Mesh Editor: material name sync failed - {error}")
	return POLL_INTERVAL


@bpy.app.handlers.persistent
def resetNameSnapshotOnLoad(dummy):
	"""A newly loaded file has different objects, so stale pointers must not be
	compared against the new ones (an address can be reused by a different object)."""
	refreshNameSnapshot()


def register():
	refreshNameSnapshot()
	bpy.app.handlers.load_post.append(resetNameSnapshotOnLoad)
	if not bpy.app.timers.is_registered(_pollMaterialNameRenames):
		bpy.app.timers.register(_pollMaterialNameRenames, first_interval=POLL_INTERVAL, persistent=True)


def unregister():
	if bpy.app.timers.is_registered(_pollMaterialNameRenames):
		bpy.app.timers.unregister(_pollMaterialNameRenames)
	if resetNameSnapshotOnLoad in bpy.app.handlers.load_post:
		bpy.app.handlers.load_post.remove(resetNameSnapshotOnLoad)
	_nameSnapshot.clear()
