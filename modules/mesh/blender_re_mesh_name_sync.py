#Author: NSA Cloud
#Keeps the material name inside a mesh object name in sync with the MDF material of
#the same name when the mesh is renamed in the outliner.
#
#Blender has no callback for data-block renames (and bpy.msgbus doesn't deliver them
#either), so the only way to notice a rename is to compare the current names against a
#snapshot. The comparison runs on a timer instead of a depsgraph handler: an outliner
#rename doesn't reliably tag a depsgraph update, and a timer also catches renames made
#by undo/redo or by other addons.

import bpy

from ..blender_utils import (findMDFCollectionForMesh, findMeshCollectionForObject,
                             getAddonPreferences, renameREMeshMaterial)
from ..naming import parseREMeshObjectMaterialName, replaceREMeshMaterialSuffix

POLL_INTERVAL = 0.5

#object pointer -> object name as of the last check
_nameSnapshot = {}


def captureNameSnapshot():
	"""Record the current name of every mesh object."""
	#bpy.data is a _RestrictData stub while an addon loads, and an empty snapshot only
	#means "no baseline yet" - the first timer tick captures the real one.
	objectList = getattr(bpy.data, "objects", None)
	if objectList == None:
		return {}
	return {obj.as_pointer(): obj.name for obj in objectList if obj.type == "MESH"}


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


def syncRenamedMeshMaterials():
	"""Apply material name changes made by renaming a mesh to the whole collection.

	Each rename is pushed through renameREMeshMaterial, which renames the matching MDF
	material and the other mesh pieces using it. Returns the number of synced renames."""
	global _nameSnapshot
	syncedCount = 0
	try:
		for obj, oldMaterialName, newMaterialName in findRenamedMeshMaterials():
			meshCollection = findMeshCollectionForObject(obj)
			if meshCollection == None:
				continue
			renamedObjList, renamedMDFCount, error = renameREMeshMaterial(
				meshCollection, findMDFCollectionForMesh(meshCollection), oldMaterialName, newMaterialName)
			if error != None:
				print(f"RE Mesh Editor: material name not synced from \"{obj.name}\": {error}")
				#A refused rename is undone: the outliner can't veto it, and a mesh name
				#with no matching material would fail the export check anyway.
				obj.name = replaceREMeshMaterialSuffix(obj.name, oldMaterialName)
				continue
			if renamedObjList or renamedMDFCount:
				syncedCount += 1
	finally:
		#Re-capture unconditionally: our own renames must not look like user renames on
		#the next tick, and anything renamed while we were working shows up next pass.
		_nameSnapshot = captureNameSnapshot()
	return syncedCount


def _autoSyncEnabled():
	try:
		return bool(getAddonPreferences().autoSyncMaterialNames)
	except Exception:
		#Preferences don't resolve when the module is imported outside the addon (tests)
		return True


def _pollMaterialNameRenames():
	try:
		if not _nameSnapshot:
			refreshNameSnapshot()
		elif _autoSyncEnabled():
			syncRenamedMeshMaterials()
		else:
			refreshNameSnapshot()
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
