#Author: NSA Cloud
#Pushes a material rename made in the outliner onto the meshes that use the material: the
#"Material NN (name)" empty carries the material name in its object name, and the meshes carry
#it in the "__" suffix of their names, so renaming the empty has to move every mesh piece.
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

from ..blender_utils import (findMDFCollectionForMaterialObject, findMeshCollectionForMDF,
                             getMDFMaterialName, iterAllMDFMaterialObjects, setMDFMaterialObjectName)
from ..naming import parseMDFObjectNameMaterialName
from .material_name import (MaterialRenameRequest, applyMaterialRename, clearNameSnapshot,
                            getSnapshotObjectName, hasNameSnapshot, refreshNameSnapshot)

POLL_INTERVAL = 0.5


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

	This is the second way to rename a material, next to the MDF panel field: renaming the
	"Material NN (name)" empty in the outliner. The MDF writer resolves the name from that
	object name (see getMDFMaterialName), so the property and the meshes have to follow it.
	Returns 1 when a rename was applied, else 0."""
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
	#Nothing left to change means the rename was already applied
	return 1 if (result.renamedMeshObjects or result.renamedMDFMaterialCount) else 0


def syncRenamedMaterialNames():
	"""Apply material name changes made by renames of "Material NN (name)" objects to the meshes.

	The renames are read from the snapshot before anything is renamed, because a rename
	renames the meshes too - reading again between the objects would see our own writes as
	new user renames. Returns the number of synced renames."""
	syncedCount = 0
	materialRenameList = findRenamedMDFMaterialObjects()
	try:
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
			syncRenamedMaterialNames()
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
