#Author: NSA Cloud
#Keeps the material name inside a mesh object name in sync with the MDF material of
#the same name, in both directions, when an object is renamed in the outliner.
#
#Blender has no callback for data-block renames (and bpy.msgbus doesn't deliver them
#either), so the only way to notice a rename is to compare the current names against a
#snapshot. The comparison runs on a timer instead of a depsgraph handler: an outliner
#rename doesn't reliably tag a depsgraph update, and a timer also catches renames made
#by undo/redo or by other addons.
#
#The snapshot and the renames themselves belong to material_name.py, which owns them for
#every door a rename can come from; this module only decides which renames the user made
#and hands them over as requests.

import bpy

from ..blender_utils import (findMDFCollectionForMaterialObject, findMDFCollectionForMesh,
                             findMeshCollectionForMDF, findMeshCollectionForObject, getMDFMaterialName,
                             getToolPanelFlag, iterAllMDFMaterialObjects, setMDFMaterialObjectName)
from ..naming import parseMDFObjectNameMaterialName, parseREMeshObjectMaterialName
from .material_name import (MaterialRenameRequest, applyMaterialRename, clearNameSnapshot,
                            getMDFMaterialNameConflict, getSnapshotObjectName, hasNameSnapshot,
                            refreshNameSnapshot, restoreRefusedMaterialRename)

POLL_INTERVAL = 0.5


def findRenamedMeshMaterials():
	"""Return (object, oldMaterialName, newMaterialName) for mesh renames since the baseline.

	Reads the snapshot without touching it, so a caller can decide what to do first."""
	renameList = []
	for obj in bpy.data.objects:
		if obj.type != "MESH":
			continue
		oldObjectName = getSnapshotObjectName(obj)
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


def syncRenamedMeshMaterial(obj, oldMaterialName, newMaterialName):
	"""Push a material rename made by renaming a mesh object onto the rest of its material.

	The outliner rename has already moved obj onto the new material name, so it is passed
	along: the name it now carries is what has to be checked for a conflict, and the rest
	of the material follows it. A refused rename is undone by the rename itself, and the
	rename count decides whether anything happened - the object itself is always already
	renamed."""
	meshCollection = findMeshCollectionForObject(obj)
	if meshCollection == None:
		return False
	result = applyMaterialRename(MaterialRenameRequest(meshCollection, findMDFCollectionForMesh(meshCollection),
	                                                   oldMaterialName, newMaterialName, renamedMeshObject = obj))
	if result.error != None:
		print(f"RE Mesh Editor: material name not synced from \"{obj.name}\": {result.error}")
		return False
	return bool(result.renamedMeshObjects or result.renamedMDFMaterialCount)


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
	restoreRefusedMaterialRename(MaterialRenameRequest(oldMaterialName = oldMaterialName, renamedMeshObject = obj))
	return True


def findRenamedMDFMaterialObjects():
	"""Return (object, oldObjectName, oldMaterialName, newMaterialName) for MDF material
	object renames since the baseline.

	newMaterialName is None when the new name no longer follows the "Material NN (name)"
	convention, so the caller can decide how to resolve the material name instead.

	Reads the snapshot without touching it, so a caller can decide what to do first."""
	renameList = []
	for obj in iterAllMDFMaterialObjects():
		oldObjectName = getSnapshotObjectName(obj)
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
	#The renamed object is the source of the rename: it already carries the new name, so it
	#is passed along instead of being looked up under the old one, and its property is written
	#with the rest so the mesh side and the MDF stay in step.
	result = applyMaterialRename(MaterialRenameRequest(findMeshCollectionForMDF(mdfCollection), mdfCollection,
	                                                   oldMaterialName, newMaterialName,
	                                                   renamedMaterialObject = obj, previousObjectName = oldObjectName))
	if result.error != None:
		print(f"RE Mesh Editor: material name not synced from \"{obj.name}\": {result.error}")
		return 0
	#Nothing left to change means the rename was already applied - e.g. by the mesh pass
	#of the same tick, which renames the MDF material of a renamed mesh
	return 1 if (result.renamedMeshObjects or result.renamedMDFMaterialCount) else 0


def syncRenamedMaterialNames(syncMeshRenames = True):
	"""Apply material name changes made by renames in the outliner to the whole pair.

	Both directions are read from the same snapshot before anything is renamed, because
	a rename on one side renames the other side too - reading again between the passes
	would see our own writes as new user renames. Returns the number of synced renames.

	syncMeshRenames is the "Auto Sync Material Names" option in the RE MDF Tools panel:
	renaming a material always renames the meshes, the option only controls the mesh to
	material direction. Turning it off stops the sync, not the duplicate name check: a
	rename onto a material name another mesh already uses is undone either way."""
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
		refreshNameSnapshot()
	return syncedCount


def _pollMaterialNameRenames():
	try:
		if not hasNameSnapshot():
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
	clearNameSnapshot()
