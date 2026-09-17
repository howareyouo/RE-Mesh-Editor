import bpy
import bmesh
import time
import numpy as np
from .file_re_mesh import SIX_WEIGHT_MESH_FILE_VERSIONS


# Game-specific weight packing settings, keyed by game name.
# Tuple layout: (maxWeightsPerVertex, maxWeightsPerVertexExtended, maxWeightedBones,
#                padWithLastWeightIndex, extendedWeights)
_GAME_PROFILES = {
    "MHWILDS": (8, 16, 256, False, True),
    "PRAG":    (8, 16, 256, True, True),
    "MHS3":    (8, 16, 256, True, True),
    "RE9":     (8, 16, 256, True, False),
}


def _game_settings(gameName, meshVersion):
    """Return the weight-packing settings 5-tuple for this game + mesh version."""
    mw, mew, mb, pad, ext = _GAME_PROFILES.get(gameName, (8, 16, 256, False, False))
    if meshVersion in SIX_WEIGHT_MESH_FILE_VERSIONS:
        mw, mew, mb = 6, 12, 1024  # 6-weight packing always wins over game name
    return mw, mew, mb, pad, ext


# ---------------------------------------------------------------------------
# Bone symmetry rules (table-driven replacement for the long L_/R_ if/elif)
# ---------------------------------------------------------------------------
_SYM_RULES = (
    ("L_",  lambda n: "R" + n[1:]),
    ("R_",  lambda n: "L" + n[1:]),
    ("_L",  lambda n: n[:-1] + "R"),
    ("_R",  lambda n: n[:-1] + "L"),
)


def _resolve_symmetry_bone_index(boneName: str, boneIndexDict: dict, allBoneNames: set) -> int:
    """Return the bone index of the symmetric partner, or -1 if none exists."""
    for prefix, maker in _SYM_RULES:
        if boneName.startswith(prefix) or boneName.endswith(prefix):
            symName = maker(boneName)
            if symName in allBoneNames:
                return boneIndexDict.get(symName, -1)
            return -1
    return boneIndexDict.get(boneName, -1)


def triangulateMesh(mesh):
	if all(len(poly.vertices) == 3 for poly in mesh.polygons):
		return
	bm = bmesh.new()
	bm.from_mesh(mesh)
	bmesh.ops.triangulate(bm, faces=bm.faces[:])
	bm.to_mesh(mesh)
	bm.free()


def bounding_sphere_ritter(points):
	"""Ritter's bounding sphere algorithm, fully vectorized with NumPy.
	Input: points as (N, 3) numpy array.
	Returns: (center_tuple, radius).
	"""
	pts = np.asarray(points, dtype=np.float64)
	n = len(pts)
	if n == 0:
		return (0.0, 0.0, 0.0), 0.0

	# Initial guess: x = first point, y = farthest from x, z = farthest from y
	x = pts[0]
	# Vectorized farthest-point search
	dists_y = np.sum((pts - x) ** 2, axis=1)
	y = pts[np.argmax(dists_y)]
	dists_z = np.sum((pts - y) ** 2, axis=1)
	z = pts[np.argmax(dists_z)]

	center = (y + z) / 2.0
	radius = np.sqrt(np.sum((y - z) ** 2)) / 2.0

	# Ritter iteration: vectorized per-pass (batch of points that exceed radius)
	# Process in batches to avoid O(n) Python loop while keeping intermediate arrays small
	# Strategy: compute all distances, find expanding points, update center/radius
	remaining = np.arange(n)
	while len(remaining) > 0:
		d = np.sqrt(np.sum((pts[remaining] - center) ** 2, axis=1))
		exceed_mask = d > radius
		if not np.any(exceed_mask):
			break
		exceed_idx = remaining[exceed_mask]
		d_exceed = d[exceed_mask]
		# Process expanding points sequentially (Ritter requires iterative updates)
		# but use vectorized arithmetic per point
		for i, idx in enumerate(exceed_idx):
			p = pts[idx]
			di = d_exceed[i]
			radius = 0.5 * (radius + di)
			old_to_new = di - radius
			center = (center * radius + old_to_new * p) / di
		# Re-filter remaining points (shrunk after sequential updates)
		d2 = np.sqrt(np.sum((pts[remaining] - center) ** 2, axis=1))
		still_outside = remaining[d2 > radius]
		if len(still_outside) == len(remaining):
			# Safety: if no progress, process one by one
			for idx in still_outside:
				p = pts[idx]
				di = float(np.sqrt(np.sum((p - center) ** 2)))
				if di <= radius:
					continue
				radius = 0.5 * (radius + di)
				center = (center * radius + (di - radius) * p) / di
			break
		remaining = still_outside

	return (float(center[0]), float(center[1]), float(center[2])), float(radius)


def joinObjects(objList):
	if bpy.app.version < (3, 2, 0):
		ctx = bpy.context.copy()
		# one of the objects to join
		ctx['active_object'] = objList[0]
		ctx['selected_editable_objects'] = objList
		bpy.ops.object.join(ctx)
	else:
		with bpy.context.temp_override(active_object=objList[0], selected_editable_objects=objList):
			bpy.ops.object.join()
	return bpy.context.active_object


def createMaterialDict(materialNameList):
	materialDict = {}
	for materialName in materialNameList:
		material = bpy.data.materials.new(materialName)
		material.use_nodes = True
		materialDict[materialName] = material
	return materialDict


def getCollection(collectionName, parentCollection=None, makeNew=False):
	if makeNew or not bpy.data.collections.get(collectionName):
		collection = bpy.data.collections.new(collectionName)
		collectionName = collection.name
		if parentCollection != None:
			parentCollection.children.link(collection)
		else:
			bpy.context.scene.collection.children.link(collection)
	return bpy.data.collections[collectionName]


def findArmatureObjFromData(armatureData):
	armatureObj = None
	for obj in bpy.context.scene.objects:
		if obj.type == "ARMATURE" and obj.data == armatureData:
			armatureObj = obj
			break
	return armatureObj





_IMPORT_PHASES = {}
_IMPORT_PHASE_CUR = None
_IMPORT_PHASE_T0 = None


def _phase_switch(name):
	global _IMPORT_PHASE_CUR, _IMPORT_PHASE_T0
	now = time.perf_counter()
	if _IMPORT_PHASE_CUR is not None:
		_IMPORT_PHASES[_IMPORT_PHASE_CUR] = _IMPORT_PHASES.get(_IMPORT_PHASE_CUR, 0.0) + (
					now - _IMPORT_PHASE_T0)
	_IMPORT_PHASE_CUR = name
	_IMPORT_PHASE_T0 = now


def _phase_reset():
	global _IMPORT_PHASE_CUR, _IMPORT_PHASE_T0
	_IMPORT_PHASES.clear()
	_IMPORT_PHASE_CUR = None
	_IMPORT_PHASE_T0 = None

