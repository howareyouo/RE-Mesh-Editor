import base64
import bpy
import bmesh
import json
import time
import numpy as np
from .file_re_mesh import SIX_WEIGHT_MESH_FILE_VERSIONS, validatePragmataSourceIndices
from ..gen_functions import raiseError, raiseWarning

# Vertex group / shape key name prefix used to tag imported blend shapes.
SHAPEKEY_PREFIX = "SHAPEKEY_"


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
	#Bulk-read loop counts instead of a per-polygon Python generator (same pattern as _gatherSubMesh)
	if len(mesh.polygons) == 0:
		return
	loop_total = np.zeros(len(mesh.polygons), dtype=np.int32)
	mesh.polygons.foreach_get("loop_total", loop_total)
	if np.all(loop_total == 3):
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


# ---------------------------------------------------------------------------
# Pragmata retail streams that Blender cannot represent as vertex groups /
# shape keys. Stored on the .blend so export does not need the unpacked game
# dump. See docs/PRAGMATA.md.
# Per-vert INT attrs: src_index, wt_0..3 (type 4), ew_0..3 (type 7),
# ax_0..3 (aux), map. Collection: pragmata_aux_extra (b64), pragmata_veSize,
# pragmata_unkn1, pragmata_nverts, pragmata_blend_header (JSON snapshot of
# typing-2 BlendTarget grouping).
PRAG_ATTR_SRC = "pragmata_src_index"
PRAG_ATTR_MAP = "pragmata_map"
PRAG_COL_EXTRA = "pragmata_aux_extra"
PRAG_COL_VESIZE = "pragmata_veSize"
PRAG_COL_UNKN1 = "pragmata_unkn1"
PRAG_COL_NVERTS = "pragmata_nverts"
PRAG_COL_BLEND_HDR = "pragmata_blend_header"


def _prag_set_int_attr(mesh, name, values):
	attr = mesh.attributes.get(name)
	if attr is None:
		attr = mesh.attributes.new(name, "INT", "POINT")
	attr.data.foreach_set("value", np.ascontiguousarray(values, dtype=np.int32))


def _prag_get_int_attr(mesh, name, nverts):
	attr = mesh.attributes.get(name)
	if attr is None or len(mesh.vertices) != nverts:
		return None
	buf = np.empty(nverts, dtype=np.int32)
	attr.data.foreach_get("value", buf)
	return buf


def _prag_set_i4_record(mesh, prefix, raw16, nverts):
	if raw16 is None or len(raw16) != nverts * 16:
		raiseWarning(
			f"Pragmata stream {prefix!r} length mismatch: expected {nverts * 16} bytes, "
			f"got {len(raw16) if raw16 is not None else 0}"
		)
		return
	arr = np.frombuffer(raw16, dtype="<i4").reshape(nverts, 4)
	for c in range(4):
		_prag_set_int_attr(mesh, f"{prefix}{c}", arr[:, c])


def _prag_get_i4_record(mesh, prefix, nverts):
	cols = []
	for c in range(4):
		col = _prag_get_int_attr(mesh, f"{prefix}{c}", nverts)
		if col is None:
			return None
		cols.append(col)
	return np.stack(cols, axis=1).astype("<i4").tobytes()


def writePragmataVertAttrs(meshObj, parsedMesh, srcStart, nverts):
	"""Copy parsed pragmataBlendAux slices onto a newly imported submesh."""
	info = getattr(parsedMesh, "pragmataBlendAux", None)
	if not info or nverts <= 0:
		return
	mesh = meshObj.data
	src = np.arange(srcStart, srcStart + nverts, dtype=np.int32)
	_prag_set_int_attr(mesh, PRAG_ATTR_SRC, src)
	sl = slice(srcStart, srcStart + nverts)
	wt = info.get("weight")
	ew = info.get("extraW")
	ax = info.get("aux")
	mp = info.get("map")
	if wt and len(wt) >= sl.stop * 16:
		_prag_set_i4_record(mesh, "pragmata_wt_", wt[sl.start * 16 : sl.stop * 16], nverts)
	else:
		raiseWarning(f"Pragmata stream too short for submesh: expected {sl.stop * 16} bytes, got {len(wt) if wt else 0}")
	if ew and len(ew) >= sl.stop * 16:
		_prag_set_i4_record(mesh, "pragmata_ew_", ew[sl.start * 16 : sl.stop * 16], nverts)
	else:
		raiseWarning(f"Pragmata stream too short for submesh: expected {sl.stop * 16} bytes, got {len(ew) if ew else 0}")
	if ax and len(ax) >= sl.stop * 16:
		_prag_set_i4_record(mesh, "pragmata_ax_", ax[sl.start * 16 : sl.stop * 16], nverts)
	else:
		raiseWarning(f"Pragmata stream too short for submesh: expected {sl.stop * 16} bytes, got {len(ax) if ax else 0}")
	if mp and len(mp) >= sl.stop * 4:
		_prag_set_int_attr(
			mesh,
			PRAG_ATTR_MAP,
			np.frombuffer(mp[sl.start * 4 : sl.stop * 4], dtype="<i4"),
		)
	else:
		raiseWarning(f"Pragmata stream too short for submesh: expected {sl.stop * 4} bytes, got {len(mp) if mp else 0}")


def writePragmataCollectionProps(collection, parsedMesh):
	info = getattr(parsedMesh, "pragmataBlendAux", None)
	if not info or collection is None:
		return
	extra = info.get("auxExtra")
	if extra:
		collection[PRAG_COL_EXTRA] = base64.b64encode(extra).decode("ascii")
	if info.get("veSize") is not None:
		collection[PRAG_COL_VESIZE] = int(info["veSize"])
	if info.get("unkn1") is not None:
		collection[PRAG_COL_UNKN1] = int(info["unkn1"])
	if info.get("nverts") is not None:
		collection[PRAG_COL_NVERTS] = int(info["nverts"])
	hdr = info.get("blendHeader")
	if hdr:
		collection[PRAG_COL_BLEND_HDR] = json.dumps(hdr, separators=(",", ":"))


def _find_pragmata_collection(col):
	if col is None:
		return None
	if PRAG_COL_NVERTS in col or PRAG_COL_EXTRA in col or PRAG_COL_BLEND_HDR in col:
		return col
	for child in getattr(col, "children", []):
		found = _find_pragmata_collection(child)
		if found is not None:
			return found
	return None



def _permute_parsed_submesh(parsedSubMesh, perm):
	n = len(perm)

	def take(arr):
		if arr is None:
			return arr
		a = np.asarray(arr)
		if a.size == 0 or a.shape[0] != n:
			return arr
		return a[perm]

	parsedSubMesh.vertexPosList = take(parsedSubMesh.vertexPosList)
	parsedSubMesh.normalList = take(parsedSubMesh.normalList)
	parsedSubMesh.tangentList = take(parsedSubMesh.tangentList)
	parsedSubMesh.uvList = take(parsedSubMesh.uvList)
	parsedSubMesh.uv2List = take(parsedSubMesh.uv2List)
	parsedSubMesh.colorList = take(parsedSubMesh.colorList)
	parsedSubMesh.weightList = take(parsedSubMesh.weightList)
	parsedSubMesh.weightIndicesList = take(parsedSubMesh.weightIndicesList)
	parsedSubMesh.extraWeightList = take(parsedSubMesh.extraWeightList)
	parsedSubMesh.extraWeightIndicesList = take(parsedSubMesh.extraWeightIndicesList)
	parsedSubMesh.secondaryWeightList = take(parsedSubMesh.secondaryWeightList)
	parsedSubMesh.secondaryWeightIndicesList = take(
		parsedSubMesh.secondaryWeightIndicesList
	)
	rank = np.empty(n, dtype=np.int32)
	rank[perm] = np.arange(n, dtype=np.int32)
	try:
		faces = np.asarray(parsedSubMesh.faceList, dtype=np.int64)
		if faces.ndim == 2 and faces.shape[1] == 3:
			parsedSubMesh.faceList = [tuple(r) for r in rank[faces].tolist()]
		else:
			raise ValueError
	except (ValueError, TypeError):
		parsedSubMesh.faceList = [
			tuple(int(rank[v]) for v in face) for face in parsedSubMesh.faceList
		]
	for bs in parsedSubMesh.blendShapeList:
		d = np.asarray(bs.deltas)
		if d.shape[0] == n:
			bs.deltas = d[perm]


def applyPragmataExportOrderAndStreams(rawsubmesh, parsedSubMesh, nverts):
	"""Restore retail vertex order from pragmata_src_index and collect raw streams.

	Returns (weight16, extra16, aux16, map4, src) in exported vertex order.
	"""
	mesh = rawsubmesh.data
	if len(mesh.vertices) != nverts:
		return None, None, None, None, None

	src = _prag_get_int_attr(mesh, PRAG_ATTR_SRC, nverts)
	perm = None
	if src is not None:
		mn = int(src.min())
		parsedSubMesh.pragmataSrcStart = mn
		expected = np.arange(mn, mn + nverts, dtype=np.int32)
		perm = np.argsort(src, kind="stable")
		if np.array_equal(src[perm], expected):
			_permute_parsed_submesh(parsedSubMesh, perm)
		else:
			perm = None
			print(
				f"Pragmata: src_index on {rawsubmesh.name} is not a complete "
				f"{nverts}-vert range; keeping Blender vertex order"
			)
	order = perm if perm is not None else np.arange(nverts)
	wt = _prag_get_i4_record(mesh, "pragmata_wt_", nverts)
	ew = _prag_get_i4_record(mesh, "pragmata_ew_", nverts)
	ax = _prag_get_i4_record(mesh, "pragmata_ax_", nverts)
	mp = _prag_get_int_attr(mesh, PRAG_ATTR_MAP, nverts)
	if wt:
		warr = np.frombuffer(wt, dtype=np.uint8).reshape(nverts, 16)
		wt = bytes(warr[order].reshape(-1))
	if ew:
		earr = np.frombuffer(ew, dtype=np.uint8).reshape(nverts, 16)
		ew = bytes(earr[order].reshape(-1))
	if ax:
		aarr = np.frombuffer(ax, dtype=np.uint8).reshape(nverts, 16)
		ax = bytes(aarr[order].reshape(-1))
	if mp is not None:
		mp = np.ascontiguousarray(mp[order], dtype="<i4").tobytes()
	return wt, ew, ax, mp, src


def assemblePragmataBlendAux(targetCollection, parsedMesh, streamChunks):
	"""Build pragmataBlendAux from .blend attributes. No unpacked-game fallback."""
	if not streamChunks:
		return None
	col = _find_pragmata_collection(targetCollection)
	weights, extras, auxes, maps, sources = zip(*streamChunks)
	storedVertexCount = col.get(PRAG_COL_NVERTS) if col is not None else None
	topologyValid, topologyError = validatePragmataSourceIndices(
		sources,
		storedVertexCount,
	)
	if not any(weights) and not any(extras) and not any(auxes):
		return None
	if not all(weights) or not all(extras):
		print(
			"Pragmata: missing weight/extra-weight attributes on some submeshes; "
			"export will not write retail skinning streams"
		)
		return None
	info = {
		"weight": b"".join(weights),
		"extraW": b"".join(extras),
		"nverts": sum(len(w) // 16 for w in weights),
		"topologyValid": topologyValid,
		"topologyError": topologyError,
	}
	if all(auxes) and all(m is not None for m in maps):
		extra = b""
		if col is not None and PRAG_COL_EXTRA in col:
			try:
				extra = base64.b64decode(col[PRAG_COL_EXTRA], validate=True)
			except Exception as err:
				raiseError(
					f"Pragmata collection property {PRAG_COL_EXTRA} is not valid base64 ({err}). "
					"Re-import the mesh; refusing to invent a morph tail."
				)
		if extra:
			info["aux"] = b"".join(auxes) + extra + b"".join(maps)
			info["auxExtra"] = extra
			info["map"] = b"".join(maps)
		else:
			print(
				"Pragmata: missing aux extra blob on the mesh collection; "
				"morph tail will not be written"
			)
	if col is not None:
		if PRAG_COL_VESIZE in col:
			info["veSize"] = int(col[PRAG_COL_VESIZE])
		if PRAG_COL_UNKN1 in col:
			info["unkn1"] = int(col[PRAG_COL_UNKN1])
		if PRAG_COL_BLEND_HDR in col:
			try:
				info["blendHeader"] = json.loads(col[PRAG_COL_BLEND_HDR])
			except Exception as err:
				raiseWarning(
					f"Pragmata collection property {PRAG_COL_BLEND_HDR} is not valid JSON ({err})"
				)
	parsedMesh.pragmataBlendAux = info
	print(
		f"Pragmata streams from Blender: weight={len(info['weight'])} "
		f"extraW={len(info['extraW'])} aux={len(info.get('aux') or b'')} "
		f"nverts={info['nverts']}"
	)
	return info

