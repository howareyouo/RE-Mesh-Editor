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
                             findMeshCollectionForObject, getMDFMaterialName, getToolPanelFlag,
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


def applyMeshMaterialRename(obj, oldMaterialName, newMaterialName):
	"""Push one mesh rename through renameREMeshMaterial, undoing it when it is refused.

	Returns True when the rename reached the rest of the collection."""
	meshCollection = findMeshCollectionForObject(obj)
	if meshCollection == None:
		return False
	renamedObjList, renamedMDFCount, error = renameREMeshMaterial(
		meshCollection, findMDFCollectionForMesh(meshCollection), oldMaterialName, newMaterialName)
	if error != None:
		print(f"RE Mesh Editor: material name not synced from \"{obj.name}\": {error}")
		#A refused rename is undone: the outliner can't veto it, and a mesh name with no
		#matching material would fail the export check anyway.
		obj.name = replaceREMeshMaterialSuffix(obj.name, oldMaterialName)
		return False
	return bool(renamedObjList or renamedMDFCount)


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
	renamedObjList, renamedMDFCount, error = renameREMeshMaterial(
		meshCollection, mdfCollection, oldMaterialName, newMaterialName, ignoreMaterialObject = obj)
	if error != None:
		print(f"RE Mesh Editor: material name not synced from \"{obj.name}\": {error}")
		#A refused rename is undone, like the mesh side: a duplicate name in the MDF would
		#bind the meshes to the wrong material.
		obj.name = oldObjectName
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
	material direction."""
	global _nameSnapshot
	syncedCount = 0
	meshRenameList = findRenamedMeshMaterials() if syncMeshRenames else []
	materialRenameList = findRenamedMDFMaterialObjects()
	try:
		for obj, oldMaterialName, newMaterialName in meshRenameList:
			if applyMeshMaterialRename(obj, oldMaterialName, newMaterialName):
				syncedCount += 1
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
			#The mesh to material direction is the only one the option controls
			syncRenamedMaterialNames(getToolPanelFlag("autoSyncMaterialNames"))
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
