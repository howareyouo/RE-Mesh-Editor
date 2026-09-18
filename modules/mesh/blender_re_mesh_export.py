import bpy
import bmesh
import os
import time
import math
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from mathutils import Vector
from .file_re_mesh import writeREMesh, ParsedREMeshToREMesh, Sphere, AABB, meshFileVersionToGameNameDict
from .re_mesh_parse import ParsedREMesh, VisconGroup, LODLevel, SubMesh, ParsedBone, Skeleton
from .re_mesh_export_errors import addErrorToDict, printErrorDict, showREMeshErrorWindow, printWarningDict
from ..gen_functions import raiseWarning, y, printElapsed, formatMs, parseFileVersion, parseREMeshGroupID, getREMeshMaterialName
from ..blender_utils import showMessageBox, findMDFCollectionForMesh, getMDFMaterialNames, rotateNeg90Matrix
from ..mdf.file_re_mdf import readMDF
from ..mdf.blender_re_mesh_mdf import findMDFPathFromMeshPath
from .blender_re_mesh_utils import triangulateMesh, bounding_sphere_ritter, getCollection, _game_settings, _resolve_symmetry_bone_index


MIN_WEIGHT = 0.002

MAX_VERTICES = 65536
MAX_VERTICES_EXTENDED = 4294967295
MAX_FACES = 4294967295

# Set to True to re-enable the warnings printed after a mesh export.
ENABLE_EXPORT_WARNINGS = False


def checkObjForUVDoubling(obj):
	if len(obj.data.uv_layers) == 0 or len(obj.data.uv_layers[0].data) == 0:
		return False
	# Reuse the vectorized scan (checks all UV layers, matching solveRepeatedUVs).
	return len(findRepeatedUVVerts(obj.data)) > 0


# RE Toolbox Solve Repeated UVs

def findRepeatedUVVerts(meshData):
	# Fully vectorized: indices of vertices referenced by loops carrying more
	# than one distinct UV coordinate. Judged independently per UV layer (UV1,
	# UV2, ...) and OR'd together - a vertex is only a duplicate if a SINGLE
	# layer disagrees with itself, matching the old per-layer bmesh scan.
	# Sorting once + reduceat is much faster than np.minimum.at scatter loops.
	loop_vert_idx = np.zeros(len(meshData.loops), dtype=np.int32)
	meshData.loops.foreach_get("vertex_index", loop_vert_idx)
	n_verts = len(meshData.vertices)
	order = np.argsort(loop_vert_idx, kind='stable')
	sv = loop_vert_idx[order]
	starts = np.flatnonzero(np.concatenate([[True], sv[1:] != sv[:-1]]))
	conflict = np.zeros(n_verts, dtype=bool)
	for uv_layer in meshData.uv_layers:
		uv_data = np.zeros((len(uv_layer.data), 2), dtype=np.float32)
		uv_layer.data.foreach_get("uv", uv_data.ravel())
		su = uv_data[order]
		uv_min = np.minimum.reduceat(su, starts, axis=0)
		uv_max = np.maximum.reduceat(su, starts, axis=0)
		bad = np.any(uv_min != uv_max, axis=1)
		conflict[sv[starts][bad]] = True
	return np.flatnonzero(conflict)


def solveRepeatedUVs(selection):
	# Data-layer rebuild: vertices are split in numpy by (vertex, uv1, uv2)
	# grouping, so no EDIT-mode / bmesh pass is needed at all. Returns a dict
	# {object name: array(new_vertex -> original_vertex)} so weight extraction
	# in _gatherSubMesh can read vertex groups through the mapping.
	context = bpy.context
	uv_remap_dict = {}
	for selectedObj in selection:
		if selectedObj.type != "MESH":
			continue
		me = selectedObj.data
		conflict = findRepeatedUVVerts(me)
		if len(conflict) == 0:
			print(f"Solved Repeated UVs on {selectedObj.name}")
			continue
		n_loops = len(me.loops)
		n_old = len(me.vertices)
		loop_vert = np.zeros(n_loops, dtype=np.int32)
		me.loops.foreach_get("vertex_index", loop_vert)
		# Gather all UV layers into one (n_loops, 2*L) array
		uv_all = np.zeros((n_loops, 2 * len(me.uv_layers)), dtype=np.float32)
		for i, uvl in enumerate(me.uv_layers):
			d = np.zeros((len(uvl.data), 2), dtype=np.float32)
			uvl.data.foreach_get("uv", d.ravel())
			uv_all[:, i * 2:i * 2 + 2] = d
		# Only loops whose vertex carries duplicated UVs need new indices.
		cmask = np.isin(loop_vert, conflict)
		if not np.any(cmask):
			print(f"Solved Repeated UVs on {selectedObj.name}")
			continue
		cvert = loop_vert[cmask]
		key_parts = [cvert.astype(np.float32), uv_all[cmask]]
		key = np.column_stack(key_parts).astype(np.float32)
		keyv = key.view(np.dtype([('k', np.float32, key.shape[1])])).ravel()
		uniq_key, first_loop, inv = np.unique(keyv, return_index=True, return_inverse=True)
		G = len(uniq_key)
		group_vert = cvert[first_loop]
		# Keep each vertex's FIRST uv group on the original vertex (so no vertex
		# becomes loose); only the remaining groups get brand-new vertices.
		uniq_v, first_pos = np.unique(cvert, return_index=True)
		is_first_group = np.zeros(G, dtype=bool)
		is_first_group[inv[first_pos]] = True
		non_first = np.flatnonzero(~is_first_group)
		new_group_idx = np.empty(G, dtype=np.int32)
		new_group_idx[is_first_group] = group_vert[is_first_group]
		new_group_idx[non_first] = n_old + np.arange(len(non_first))
		new_loop_vert = loop_vert.copy()
		new_loop_vert[cmask] = new_group_idx[inv]
		old_of_new = np.concatenate([np.arange(n_old, dtype=np.int32),
		                             group_vert[non_first]])
		# Extend vertex data: new verts copy position from their original vert.
		co = np.zeros((n_old, 3), dtype=np.float32)
		me.vertices.foreach_get("co", co.ravel())
		me.vertices.add(len(non_first))
		me.vertices.foreach_set("co", co[old_of_new].ravel())
		me.loops.foreach_set("vertex_index", new_loop_vert)
		# Loop count and all per-loop data (uv) are unchanged; loop normals are
		# derived from the new topology (same as the old bmesh path, whose
		# normals_split_custom_set restore was also ineffective on split meshes).
		uv_remap_dict[selectedObj.name] = old_of_new
		print(f"Solved Repeated UVs on {selectedObj.name}")
	return uv_remap_dict
# End solve repeated UVs


# RE Toolbox Split Sharp Edges
def splitSharpEdges():
	context = bpy.context
	if context.selected_objects != []:
		selection = context.selected_objects
	else:
		selection = bpy.context.scene.objects
	for selectedObj in selection:
		if selectedObj.type != "MESH":
			continue
		isHidden = selectedObj.hide_viewport
		if isHidden:
			selectedObj.hide_viewport = False
		me = selectedObj.data
		# Non-EDIT bmesh round trip: same split_edges result as the old
		# EDIT-mode path but skips the two mode_set() operator calls (which
		# dominated time on large meshes). bmesh keeps weights automatically.
		bm = bmesh.new()
		bm.from_mesh(me)
		bm.edges.ensure_lookup_table()
		sharp = [e for e in bm.edges if not e.smooth]
		if sharp != []:
			print(f"Split Sharp Edges on {selectedObj.name}")
		bmesh.ops.split_edges(bm, edges=sharp)
		bm.to_mesh(me)
		bm.free()
		selectedObj.hide_viewport = isHidden


# End split sharp edges


# --- Parallel submesh extraction worker ---
# Runs the heavy, READ-ONLY portion of extracting a single submesh (foreach_get
# bulk reads + numpy vectorization + per-vertex weight loop) on a worker thread.
# It must NOT mutate any shared Blender/Mesh state - everything it produces is
# returned and merged back on the main thread. This is safe to parallelize because
# all mesh-mutating prep (triangulate / transform / calc_tangents) for a viscon is
# completed serially BEFORE any worker starts, and foreach_get / numpy / bytes IO
# release the GIL, so worker threads overlap in real time across meshes.



def _gatherSubMesh(task, profile, maxFaces, armatureBoneNameSet):
	(parsedSubMesh, evaluatedSubMeshData, rawName, meshHasUV, meshHasUV2, meshHasColor,
	 vertexGroupCount, vgNameList, shapeKeyGroupIndices,
	 hasWeight, hasSecondaryWeight, uvRemap, armatureBoneNameSet) = task
	min_w = MIN_WEIGHT
	maxWeightsPerVertex, maxWeightsPerVertexExtended, _, padWithLastWeightIndex, extendedWeights = profile
	errs = []  # (code, name) collected locally, merged on main thread
	bbox_gi = []   # raw group index for every weighted-bone assignment (for bone bbox)
	bbox_xyz = []  # flat x,y,z positions, parallel to bbox_gi
	hasExtraWeightOverflow = False
	used_names = set()  # unprefixed vertex-group names actually referenced (drives weightedBones/remap build)

	loop_vert_idx = np.zeros(len(evaluatedSubMeshData.loops), dtype=np.int32)
	evaluatedSubMeshData.loops.foreach_get("vertex_index", loop_vert_idx)
	n_polys = len(evaluatedSubMeshData.polygons)
	if n_polys > 0:
		loop_total = np.zeros(n_polys, dtype=np.int32)
		evaluatedSubMeshData.polygons.foreach_get("loop_total", loop_total)
		if np.any(loop_total != 3):
			errs.append(("NonTriangulatedFace", rawName))
		parsedSubMesh.faceList = loop_vert_idx.reshape(-1, 3).astype(np.uint32)
		if n_polys > maxFaces:
			errs.append(("MaxFacesExceeded", rawName))
	else:
		parsedSubMesh.faceList = []

	vert_positions = np.zeros((len(evaluatedSubMeshData.vertices), 3), dtype=np.float32)
	evaluatedSubMeshData.vertices.foreach_get("co", vert_positions.ravel())
	loop_normals = np.zeros((len(evaluatedSubMeshData.loops), 3), dtype=np.float32)
	evaluatedSubMeshData.loops.foreach_get("normal", loop_normals.ravel())
	loop_tangents = np.zeros((len(evaluatedSubMeshData.loops), 3), dtype=np.float32)
	evaluatedSubMeshData.loops.foreach_get("tangent", loop_tangents.ravel())
	loop_bitangent_signs = np.zeros(len(evaluatedSubMeshData.loops), dtype=np.float32)
	evaluatedSubMeshData.loops.foreach_get("bitangent_sign", loop_bitangent_signs)
	if meshHasUV:
		uv_layer_data = evaluatedSubMeshData.uv_layers[0].data
		uv_array = np.zeros((len(uv_layer_data), 2), dtype=np.float32)
		uv_layer_data.foreach_get("uv", uv_array.ravel())
	if meshHasUV2:
		uv2_layer_data = evaluatedSubMeshData.uv_layers[1].data
		uv2_array = np.zeros((len(uv2_layer_data), 2), dtype=np.float32)
		uv2_layer_data.foreach_get("uv", uv2_array.ravel())
	if meshHasColor:
		color_data = evaluatedSubMeshData.vertex_colors[0].data
		color_array = np.zeros((len(color_data), 4), dtype=np.float32)
		color_data.foreach_get("color", color_array.ravel())

	# Build loop->vertex mapping and find unique vertices (vectorized)
	unique_verts, first_loop_of_vert, inverse = np.unique(loop_vert_idx, return_index=True,
	                                                      return_inverse=True)
	parsedSubMesh.vertexPosList[unique_verts] = vert_positions[unique_verts]
	parsedSubMesh.normalList[unique_verts] = loop_normals[first_loop_of_vert]

	# Vectorized tangent extraction (no Python loop)
	lt = loop_tangents[first_loop_of_vert] * 1.001 * 127
	lt_int = np.floor(lt).astype(np.int32)
	sign_int = np.floor(loop_bitangent_signs[first_loop_of_vert] * 127.0).astype(np.int32)
	parsedSubMesh.tangentList[unique_verts] = np.column_stack([
		lt_int[:, 0] & 0xFF,
		lt_int[:, 1] & 0xFF,
		lt_int[:, 2] & 0xFF,
		sign_int & 0xFF
	]).astype(np.uint8)

	if meshHasUV:
		parsedSubMesh.uvList[unique_verts] = uv_array[first_loop_of_vert]
		uv_ref = uv_array[first_loop_of_vert[inverse]]
		if np.any(uv_array != uv_ref):
			errs.append(("MultipleUVsAssignedToVertex", rawName))
	if meshHasUV2:
		parsedSubMesh.uv2List[unique_verts] = uv2_array[first_loop_of_vert]
		uv2_ref = uv2_array[first_loop_of_vert[inverse]]
		if np.any(uv2_array != uv2_ref):
			errs.append(("MultipleUVsAssignedToVertex", rawName))
	if meshHasColor:
		parsedSubMesh.colorList[unique_verts] = color_array[first_loop_of_vert]

	# Bone Weights - pure Python per-vertex processing. Blender offers no
	# foreach_get for vertex-group weights, and numpy has high per-call overhead
	# on the tiny (<=8 element) per-vertex slices, so plain lists are fastest.
	if hasWeight:
		vg_name_list = vgNameList
		shapekey_set = shapeKeyGroupIndices
		min_w = MIN_WEIGHT
		secondary = hasSecondaryWeight
		# Only the unique verts are actually processed, so materialize their positions
		# (instead of converting the whole positions array to a list of python floats).
		pos_unique = vert_positions[unique_verts].tolist()
		# Resolve UV-rebuild remap ONCE outside the loop: when UV solve split
		# vertices, weights live on the ORIGINAL verts (new verts have empty
		# deform data); otherwise vertex index is the original index.
		if uvRemap is not None:
			vert_ids = uvRemap[unique_verts].tolist()
		else:
			vert_ids = unique_verts.tolist()
		verts = evaluatedSubMeshData.vertices
		invalidBoneGroupSet = set()  # Vertex groups weighted to bones that aren't on the armature
		for pos_idx, vi_old in enumerate(vert_ids):
			vert = verts[vi_old]
			prim = []  # (raw group idx, weight)
			sec = []   # (raw group idx, weight) for shapekeys
			for g in vert.groups:
				gidx = g.group
				if gidx >= vertexGroupCount:
					continue
				rawN = vg_name_list[gidx]
				used_names.add(rawN[9:] if rawN.startswith("SHAPEKEY_") else rawN)
				if not rawN.startswith("SHAPEKEY_") and rawN not in armatureBoneNameSet:
					invalidBoneGroupSet.add(rawN)
				if secondary and gidx in shapekey_set:
					sec.append((gidx, g.weight))
				elif g.weight >= min_w:
					prim.append((gidx, g.weight))
			if prim:
				prim.sort(key=lambda t: t[1], reverse=True)
				prim_w = [w for _, w in prim]
				prim_idx = [i for i, _ in prim]
				n_prim = len(prim)
				padding_idx = prim_idx[-1] if padWithLastWeightIndex else 0
				if n_prim > maxWeightsPerVertex:
					hasExtraWeightOverflow = True
					if not extendedWeights:
						errs.append(("MaxWeightsPerVertexExceeded", rawName))
					extra_n = min(n_prim - maxWeightsPerVertex, 8)
					parsedSubMesh.extraWeightList[pos_idx, :extra_n] = prim_w[maxWeightsPerVertex:maxWeightsPerVertex + extra_n]
					parsedSubMesh.extraWeightIndicesList[pos_idx, :extra_n] = prim_idx[maxWeightsPerVertex:maxWeightsPerVertex + extra_n]
					if n_prim > maxWeightsPerVertexExtended:
						errs.append(("ExtendedMaxWeightsPerVertexExceeded", rawName))
				main_n = min(n_prim, maxWeightsPerVertex)
				parsedSubMesh.weightList[pos_idx, :main_n] = prim_w[:main_n]
				parsedSubMesh.weightIndicesList[pos_idx, :main_n] = prim_idx[:main_n]
				if main_n < 8:
					parsedSubMesh.weightIndicesList[pos_idx, main_n:] = padding_idx
				px, py, pz = pos_unique[pos_idx]
				for gi in prim_idx[:main_n]:
					bbox_gi.append(gi)
					bbox_xyz.extend((px, py, pz))
			if secondary and sec:
				sec.sort(key=lambda t: t[1], reverse=True)
				sec_w = [w for _, w in sec]
				sec_idx = [i for i, _ in sec]
				n_sec = len(sec)
				if n_sec > maxWeightsPerVertex:
					errs.append(("MaxWeightsPerVertexExceeded", rawName))
				sec_n = min(n_sec, 8)
				parsedSubMesh.secondaryWeightList[pos_idx, :sec_n] = sec_w[:sec_n]
				parsedSubMesh.secondaryWeightIndicesList[pos_idx, :sec_n] = sec_idx[:sec_n]
				px, py, pz = pos_unique[pos_idx]
				for gi in sec_idx[:sec_n]:
					bbox_gi.append(gi)
					bbox_xyz.extend((px, py, pz))
		if invalidBoneGroupSet:
			for groupName in sorted(invalidBoneGroupSet):
				errs.append(("WARN_VertexGroupsNotOnArmature", f"{rawName} [{groupName}]"))
	if len(unique_verts) < len(evaluatedSubMeshData.vertices):
		errs.append(("LooseVerticesOnSubMesh", rawName))
	if bbox_gi:
		bbox = (np.asarray(bbox_gi, dtype=np.int32),
		        np.asarray(bbox_xyz, dtype=np.float32).reshape(-1, 3))
	else:
		bbox = (np.zeros(0, dtype=np.int32), np.zeros((0, 3), dtype=np.float32))
	return parsedSubMesh, errs, bbox, hasExtraWeightOverflow, used_names, vgNameList



def _export_build_skeleton(armatureObj, rotate90, preserveBoneMatrices):
	transform = None
	skeleton = None
	hashedBoneNameDict = dict()
	if armatureObj:
		print(f"Armature: {armatureObj.name}")
		skeleton = Skeleton()
		if rotate90:
			transform = rotateNeg90Matrix @ armatureObj.matrix_world
		else:
			transform = armatureObj.matrix_world
		boneIndexDict = {bone.name: index for index, bone in enumerate(armatureObj.data.bones)}
		allBoneNames = set(boneIndexDict.keys())
		for bone in armatureObj.data.bones:
			parsedBone = ParsedBone()
			# Get hierarchy
			parsedBone.boneName = bone.name
			unHashedName = bone.get("unhashedBoneName", None)
			if unHashedName != None:
				# parsedBone.boneName = unHashedName
				hashedBoneNameDict[bone.name] = unHashedName
			parsedBone.boneIndex = boneIndexDict[bone.name]
			parsedBone.nextSiblingIndex = -1
			parsedBone.nextChildIndex = -1
			parsedBone.symmetryBoneIndex = _resolve_symmetry_bone_index(
				bone.name, boneIndexDict, allBoneNames)

			if bone.parent != None:
				parsedBone.parentIndex = boneIndexDict[bone.parent.name]
				for childBone in bone.parent.children:
					if childBone.name != bone.name and boneIndexDict[bone.name] < boneIndexDict[
						childBone.name]:
						parsedBone.nextSiblingIndex = boneIndexDict[childBone.name]
						break
			else:
				parsedBone.parentIndex = -1

			if len(bone.children) != 0:
				parsedBone.nextChildIndex = boneIndexDict[bone.children[0].name]
			# Get matrices
			if preserveBoneMatrices and bone.get("reMeshWorldMatrix"):
				if bone.get("reMeshWorldMatrix"):
					parsedBone.worldMatrix.matrix = [list(row) for row in bone["reMeshWorldMatrix"]]
				if bone.get("reMeshLocalMatrix"):
					parsedBone.localMatrix.matrix = [list(row) for row in bone["reMeshLocalMatrix"]]
				if bone.get("reMeshInverseMatrix"):
					parsedBone.inverseMatrix.matrix = [list(row) for row in bone["reMeshInverseMatrix"]]
			else:
				boneLocal4 = bone.matrix_local.to_4x4()
				worldMatrix = (transform @ boneLocal4).transposed()

				if bone.parent != None:
					localMatrix = boneLocal4.transposed() @ (
						bone.parent.matrix_local.to_4x4().transposed().inverted())
				else:
					localMatrix = boneLocal4.transposed()
				inverseMatrix = worldMatrix.inverted()

				parsedBone.worldMatrix.matrix = [list(row) for row in worldMatrix]
				parsedBone.localMatrix.matrix = [list(row) for row in localMatrix]
				parsedBone.inverseMatrix.matrix = [list(row) for row in inverseMatrix]

			skeleton.boneList.append(parsedBone)
			if len(armatureObj.data.bones) == 0:
				raiseWarning("Armature contains no bones, skipping armature.")
	else:
		print(f"Armature: None")
		armatureObj = None
	armatureBoneNameSet = set(bone.name for bone in armatureObj.data.bones) if armatureObj else None
	return skeleton, transform, hashedBoneNameDict, armatureBoneNameSet

def _export_read_bounding_boxes(boundingBoxCollection, armatureObj, parsedMesh, exportBoundingBoxes):
	importedBoneBoundingBoxes = {}
	# Get previously imported bounding boxes if option enabled
	if boundingBoxCollection and exportBoundingBoxes:
		for obj in boundingBoxCollection.objects:
			objType = obj.get("~TYPE")
			if objType == "RE_MESH_BONE_BOUNDING_BOX":
				if obj.constraints.get("BoneName") != None:
					if obj.data.vertices[0].co[0] < obj.data.vertices[1].co[0] \
							or obj.data.vertices[0].co[1] < obj.data.vertices[1].co[1] \
							or obj.data.vertices[0].co[2] < obj.data.vertices[1].co[2]:
						minVert = obj.data.vertices[0].co
						maxVert = obj.data.vertices[1].co
					else:
						minVert = obj.data.vertices[1].co
						maxVert = obj.data.vertices[0].co

					if armatureObj:
						minVert = minVert @ armatureObj.matrix_world.inverted()  # Cancel out the armature rotation
						maxVert = maxVert @ armatureObj.matrix_world.inverted()
					boneBBox = AABB()
					boneBBox.min.x = minVert[0]
					boneBBox.min.y = minVert[1]
					boneBBox.min.z = minVert[2]
					boneBBox.max.x = maxVert[0]
					boneBBox.max.y = maxVert[1]
					boneBBox.max.z = maxVert[2]
					importedBoneBoundingBoxes[obj.constraints["BoneName"].subtarget] = boneBBox
			elif objType == "RE_MESH_BOUNDING_BOX":
				if obj.data.vertices[0].co[0] < obj.data.vertices[1].co[0] \
						or obj.data.vertices[0].co[1] < obj.data.vertices[1].co[1] \
						or obj.data.vertices[0].co[2] < obj.data.vertices[1].co[2]:
					minVert = obj.data.vertices[0]
					maxVert = obj.data.vertices[1]
				else:
					minVert = obj.data.vertices[1]
					maxVert = obj.data.vertices[0]
				parsedMesh.boundingBox.min.x = minVert.co[0]
				parsedMesh.boundingBox.min.y = minVert.co[1]
				parsedMesh.boundingBox.min.z = minVert.co[2]
				parsedMesh.boundingBox.max.x = maxVert.co[0]
				parsedMesh.boundingBox.max.y = maxVert.co[1]
				parsedMesh.boundingBox.max.z = maxVert.co[2]
			elif objType == "RE_MESH_BOUNDING_SPHERE":

				parsedMesh.boundingSphere.x = obj.location[0]
				parsedMesh.boundingSphere.y = obj.location[1]
				parsedMesh.boundingSphere.z = obj.location[2]
				parsedMesh.boundingSphere.r = obj.dimensions.x / 2
	return importedBoneBoundingBoxes


def _export_process_lods(meshLODCollectionList, options, profile, parsedMesh, errorDict, warningDict, dg,
                          armatureObj, armatureBoneNameSet, deleteCopiedMeshList, cloneMeshNameDict):
	subMeshCount = 0
	vertexCount = 0
	faceCount = 0
	addedMaterialsSet = set()
	materialIndexDict = {}  # 材质名称到索引的映射，用于O(1)查找
	# Loop through all lod collections, or the scene collection if there is no collections
	meshDataStartTime = time.time()
	isFirstLOD = True
	remapDict = dict()
	# Aggregated across the parallel submesh extraction for the current LOD:
	all_used_names = set()      # unprefixed group names actually referenced -> builds weightedBones/remap
	all_submesh_vg = []         # (parsedSubMesh, vgNameList, bbox) -> raw weight indices remapped later
	for lodIndex, lod in enumerate(meshLODCollectionList):
		print(f"LOD {lodIndex} collection:{lod.name}")
		parsedLODLevel = LODLevel()
		if lod.get("LOD Distance") == None:
			lod["LOD Distance"] = 0.167932 * (
						lodIndex + 1)  # Player model LOD distance, maybe calculate from a bounding box instead
		parsedLODLevel.lodDistance = lod["LOD Distance"]

		# Store all groups as a key in dictionary with submesh list as value
		visconDict = dict()
		# Get all meshes inside the collection
		doubledUVList = []
		sharpEdgeSplitList = []
		uvRemapDict = dict()  # CLN name -> new-vertex->original-vertex mapping (data-layer UV solve)
		clonedMeshCollection = getCollection("clonedMeshes")
		for obj in lod.objects:
			if options["selectedOnly"]:
				selected = obj in bpy.context.selected_objects
			else:
				selected = True

			if obj.type == "MESH" and not obj.get("MeshExportExclude") and selected:
				subMeshCount += 1
				cloneObj = obj.copy()
				# Get copy of sub mesh with modifiers applied
				# Creates copy of object so that solve repeated uvs and sharp edge splitting can be done and not affect the original mesh
				cloneObj.name = "CLN_" + obj.name
				cloneObj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
				clonedMeshCollection.objects.link(cloneObj)

				print(f"Created temporary clone of {obj.name}: {cloneObj.name}")
				cloneMeshNameDict[obj.name] = cloneObj.name
				deleteCopiedMeshList.append(cloneObj)
				if options["autoSolveRepeatedUVs"]:
					hasUVDoubling = checkObjForUVDoubling(cloneObj)
					if hasUVDoubling:
						doubledUVList.append(cloneObj)

				if options["preserveSharpEdges"]:
					use_smooth = np.zeros(len(cloneObj.data.polygons), dtype=bool)
					cloneObj.data.polygons.foreach_get("use_smooth", use_smooth)
					if not np.all(use_smooth):
						sharpEdgeSplitList.append(cloneObj)

				groupID = parseREMeshGroupID(obj.name)
				# Warn when the name doesn't follow the RE naming scheme since the viscon
				# group and material name both fall back to defaults then
				if "Group_" not in obj.name or ("__" not in obj.name and not options["useBlenderMaterialName"]):
					addErrorToDict(warningDict, "InvalidMeshNamingScheme", obj.name)

				if not visconDict.get(groupID):
					visconDict[groupID] = [obj]
				else:
					visconDict[groupID].append(obj)

		if doubledUVList != []:
			uvSolveStart = time.time()
			previousSelection = bpy.context.selected_objects
			bpy.ops.object.select_all(action='DESELECT')
			for obj in doubledUVList:
				obj.select_set(True)

			try:
				remaps = solveRepeatedUVs(selection=bpy.context.selected_objects)
				if remaps:
					uvRemapDict.update(remaps)
			except Exception as err:
				raiseWarning(f"Failed to solve repeated UVs. {str(err)}")
			printElapsed("Solve repeated UVs", uvSolveStart, suffix=f" ({len(doubledUVList)} meshes).")

			for obj in previousSelection:
				obj.select_set(True)

		if sharpEdgeSplitList != []:
			previousSelection = bpy.context.selected_objects
			bpy.ops.object.select_all(action='DESELECT')
			for obj in sharpEdgeSplitList:
				obj.select_set(True)
			try:
				splitSharpEdges()
			except Exception as err:
				raiseWarning(f"Failed to split sharp edges. {str(err)}")

			bpy.ops.object.select_all(action='DESELECT')
			for obj in previousSelection:
				obj.select_set(True)

		# The weighted bones / remap table is now built AFTER the parallel submesh extraction
		# (from the group names each worker reports back), so no serial vertex scan is needed.

		# Once all viscons have been added, sort them, then parse the submeshes
		for visconGroupID in sorted(visconDict.keys()):
			print(f"  Group:{visconGroupID}")
			visconGroup = VisconGroup()
			visconGroup.visconGroupNum = visconGroupID
			subMeshTasks = []  # heavy (read-only) extraction tasks, run in a thread pool after prep
			# Sort by material name (case-insensitive, matching the Rename
			# Meshes operator) so the submesh order is always derived from
			# the material, not the stale Sub_N suffix in the object name.
			for submeshIndex, rawsubmesh in enumerate(
					sorted(visconDict[visconGroupID], key=lambda obj: getREMeshMaterialName(obj).lower())):
				print(f"    Sub Mesh {str(submeshIndex)}:{rawsubmesh.name}")
				evaluatedSubMeshData = bpy.data.objects[cloneMeshNameDict[rawsubmesh.name]].data
				# Weight data is read from the CLONE mesh (evaluatedSubMeshData), whose vertex-group
				# index space may differ from the original object after new_from_object(). Use the
				# clone's own vertex groups for the count, bounds check, remap and shapekey indices.
				# NOTE: vertex-group NAMES/indices live on the OBJECT, not on the Mesh (Mesh has no vertex_groups),
				# so read those from the clone object.
				cloneObj = bpy.data.objects[cloneMeshNameDict[rawsubmesh.name]]
				vertexGroupCount = len(cloneObj.vertex_groups)
				triangulateMesh(evaluatedSubMeshData)
				if len((evaluatedSubMeshData.vertices)) == 0:
					addErrorToDict(errorDict, "NoVerticesOnSubMesh", rawsubmesh.name)

				if len((evaluatedSubMeshData.polygons)) == 0:
					addErrorToDict(errorDict, "NoFacesOnSubMesh", rawsubmesh.name)
				parsedSubMesh = SubMesh()
				parsedSubMesh.subMeshIndex = submeshIndex
				materialName = "NO_ASSIGNED_MATERIAL"
				if options["useBlenderMaterialName"]:  # Material name from object material
					if len(evaluatedSubMeshData.materials) > 0:
						materialName = evaluatedSubMeshData.materials[0].name.split(".")[0]
					else:
						try:  # Get material from mesh name if it isn't found
							materialName = rawsubmesh.name.split("__", 1)[1].split(".")[0]
						except:
							addErrorToDict(errorDict, "NoMaterialOnSubMesh", rawsubmesh.name)

				else:  # Material name from object name
					try:  # Get material from mesh name if it isn't found
						materialName = rawsubmesh.name.split("__", 1)[1].split(".")[0]
					except:  # Fall back to blender material name if object material name is missing
						print(
							f"Couldn't split material name on {rawsubmesh.name}, using blender material name instead")
						if len(evaluatedSubMeshData.materials) > 0:
							materialName = evaluatedSubMeshData.materials[0].name.split(".")[0]
						else:
							addErrorToDict(errorDict, "NoMaterialOnSubMesh", rawsubmesh.name)
				if materialName not in addedMaterialsSet:
					addedMaterialsSet.add(materialName)
					materialIndexDict[materialName] = len(parsedMesh.materialNameList)
					parsedMesh.materialNameList.append(materialName)
					parsedMesh.nameList.append(materialName)
					parsedSubMesh.materialIndex = materialIndexDict[materialName]
				else:
					parsedSubMesh.materialIndex = materialIndexDict[materialName]
				# Convert to global
				if options["rotate90"]:
					subMeshWorldMatrix = rotateNeg90Matrix @ rawsubmesh.matrix_world
				else:
					subMeshWorldMatrix = rawsubmesh.matrix_world

				evaluatedSubMeshData.transform(subMeshWorldMatrix)
				# evaluatedSubMeshData.normals_split_custom_set_from_vertices([vert.normal for vert in evaluatedSubMeshData.vertices])
				if bpy.app.version < (4, 0, 0):
					evaluatedSubMeshData.use_auto_smooth = True
					evaluatedSubMeshData.calc_normals_split()
				if len(evaluatedSubMeshData.uv_layers) > 0:
					try:
						evaluatedSubMeshData.calc_tangents()
					except:
						pass
				if len(evaluatedSubMeshData.vertices) > MAX_VERTICES_EXTENDED:
					addErrorToDict(errorDict, "MaxVerticesExceeded", rawsubmesh.name)
				if len(evaluatedSubMeshData.vertices) > MAX_VERTICES:
					parsedMesh.bufferHasIntFaces = True
					raiseWarning(
						f"{rawsubmesh.name} exceeded the standard limit of {str(MAX_VERTICES)} vertices. Enabling extended vertex limit of {str(MAX_VERTICES_EXTENDED)}.")
				vertexCount += len(evaluatedSubMeshData.vertices)

				faceCount += len(evaluatedSubMeshData.polygons)

				# Resolve weight-remap and shapekey indices in the CLONE mesh's vertex-group
				# index space (where the weight group indices are actually read from). The raw
				# group indices are stored during extraction and remapped on the main thread
				# once the weighted-bone table is known.
				vgNameList = [vg.name for vg in cloneObj.vertex_groups]

				# DD2 shape key vertex group indices
				shapeKeyGroupIndices = set(
					idx for idx, name in enumerate(vgNameList) if name.startswith("SHAPEKEY_"))
				if len(shapeKeyGroupIndices) != 0:
					parsedMesh.bufferHasSecondaryWeight = True

				parsedMesh.bufferHasPosition = True
				parsedSubMesh.vertexPosList = np.zeros((len(evaluatedSubMeshData.vertices), 3))
				parsedMesh.bufferHasNorTan = True
				parsedSubMesh.normalList = np.zeros((len(evaluatedSubMeshData.vertices), 3))
				parsedSubMesh.tangentList = np.zeros((len(evaluatedSubMeshData.vertices), 4), dtype="<B")
				if armatureObj:
					parsedMesh.bufferHasWeight = True
					parsedSubMesh.weightList = np.zeros((len(evaluatedSubMeshData.vertices), 8))
					parsedSubMesh.weightIndicesList = np.zeros((len(evaluatedSubMeshData.vertices), 8),
					                                           dtype="<H")  # ushort because of SF6
					# In case weights exceed standard maximum
					parsedSubMesh.extraWeightList = np.zeros((len(evaluatedSubMeshData.vertices), 8))
					parsedSubMesh.extraWeightIndicesList = np.zeros((len(evaluatedSubMeshData.vertices), 8),
					                                                dtype="<H")  # ushort because of SF6
					if parsedMesh.bufferHasSecondaryWeight:
						parsedSubMesh.secondaryWeightList = np.zeros((len(evaluatedSubMeshData.vertices), 8))
						parsedSubMesh.secondaryWeightIndicesList = np.zeros(
							(len(evaluatedSubMeshData.vertices), 8), dtype="<H")  # ushort because of SF6
				if len(evaluatedSubMeshData.uv_layers) > 0 and len(evaluatedSubMeshData.uv_layers[0].data) > 0:
					parsedSubMesh.uvList = np.zeros((len(evaluatedSubMeshData.vertices), 2))
					meshHasUV = True
					parsedMesh.bufferHasUV = True
				else:
					meshHasUV = False
					addErrorToDict(errorDict, "NoUVMapOnSubMesh", rawsubmesh.name)
				if len(evaluatedSubMeshData.uv_layers) > 1 and len(evaluatedSubMeshData.uv_layers[1].data) > 0:
					meshHasUV2 = True
					parsedSubMesh.uv2List = np.zeros((len(evaluatedSubMeshData.vertices), 2))
					parsedMesh.bufferHasUV2 = True
				else:
					parsedSubMesh.uv2List = None
					meshHasUV2 = False
				if len(evaluatedSubMeshData.vertex_colors) > 0 and len(
						evaluatedSubMeshData.vertex_colors[0].data) > 0:
					parsedSubMesh.colorList = np.zeros((len(evaluatedSubMeshData.vertices), 4))
					meshHasColor = True
					parsedMesh.bufferHasColor = True
				else:
					meshHasColor = False
					parsedSubMesh.colorList = None

				# Heavy (read-only) extraction is deferred to `_gatherSubMesh`, which runs
				# in a thread pool after ALL mesh-mutating prep above has finished. We only
				# record the task here; results are merged in submesh order below.
				subMeshTasks.append((
					parsedSubMesh, evaluatedSubMeshData, rawsubmesh.name,
					meshHasUV, meshHasUV2, meshHasColor,
					vertexGroupCount, vgNameList, shapeKeyGroupIndices,
					armatureObj != None, parsedMesh.bufferHasSecondaryWeight,
					uvRemapDict.get(cloneMeshNameDict[rawsubmesh.name]),
					armatureBoneNameSet,
				))

			# Run the submesh extraction in parallel, then merge results on the main thread.
			# Mesh-mutating prep is already done, and foreach_get / numpy / bytes IO release
			# the GIL, so worker threads overlap in real time (near-linear on multicore).

			def _mergeSubMeshResult(res):
				# Serial merge (runs on main thread). Remap + bbox are deferred until the
				# weighted-bone table is built from all groups' used_names.
				ps, errs, bb, fl, used, vgNames = res
				if fl:
					parsedMesh.bufferHasExtraWeight = True
				for code, name in errs:
					if code.startswith("WARN_"):  # Non blocking, reported after the export finishes
						addErrorToDict(warningDict, code[len("WARN_"):], name)
					else:
						addErrorToDict(errorDict, code, name)
				visconGroup.subMeshList.append(ps)
				if used and armatureObj != None:
					all_used_names.update(used)
					all_submesh_vg.append((ps, vgNames, bb))

			gatherStart = time.time()
			workers = min(len(subMeshTasks), os.cpu_count() if os.cpu_count() else 4)
			if len(subMeshTasks) == 0:
				pass
			elif workers <= 1:
				for task in subMeshTasks:
					_mergeSubMeshResult(_gatherSubMesh(task, profile, MAX_FACES, armatureBoneNameSet))
			else:
				results = [None] * len(subMeshTasks)
				with ThreadPoolExecutor(max_workers=workers) as ex:
					futures = {ex.submit(_gatherSubMesh, t, profile, MAX_FACES, armatureBoneNameSet): i for i, t in enumerate(subMeshTasks)}
					for fut in as_completed(futures):
						results[futures[fut]] = fut.result()
				# Serial merge in original submesh order
				for res in results:
					_mergeSubMeshResult(res)
			print(
				f"  Parallel submesh extraction took {formatMs(time.time() - gatherStart)} ms ({workers} workers, {len(subMeshTasks)} meshes).")

			# End submesh
			parsedLODLevel.visconGroupList.append(visconGroup)
		# End viscon

		# Build the weighted-bone table from the (parallel) extracted used_names, then
		# remap every submesh's raw group indices and fold bone bounding-box contributions
		# in. This replaces the old serial all-vertex scan (which took ~900ms for a full LOD).
		if armatureObj != None:
			boneRemapStartTime = time.time()  # measure only the (now cheap) remap build+apply
			if isFirstLOD:
				armatureBoneDict = armatureObj.data.bones
				remapIndex = 0
				for bone in armatureBoneDict:
					if bone.name in all_used_names:
						parsedMesh.skeleton.weightedBones.append(bone.name)
						remapDict[bone.name] = remapIndex
						remapIndex += 1
				if len(parsedMesh.skeleton.weightedBones) == 0:
					raiseWarning(
						f"No bones have any weights assigned to them. Defaulting all weights to {armatureBoneDict[0].name}")
					parsedMesh.skeleton.weightedBones = [armatureBoneDict[0].name]
				# Vectorized per-bone min/max accumulation (indexed by weighted-bone order)
				_numWB = len(parsedMesh.skeleton.weightedBones)
				boneMinArr = np.full((_numWB, 3), np.inf, dtype=np.float32)
				boneMaxArr = np.full((_numWB, 3), -np.inf, dtype=np.float32)

			if isFirstLOD or remapDict:  # always on first LOD (remap table now known), and on later LODs once built
				# Remap raw group indices -> weighted-bone index (vectorized per submesh)
				for ps, vgNames, bb in all_submesh_vg:
					n = len(vgNames)
					lookup = np.asarray([
						remapDict.get(vgNames[i][9:] if vgNames[i].startswith("SHAPEKEY_") else vgNames[i], 0)
						for i in range(n)], dtype=np.int32)
					weightIdx = ps.weightIndicesList
					if weightIdx is not None and len(weightIdx):
						weightIdx[:] = lookup[weightIdx]
						extra = ps.extraWeightIndicesList
						if extra is not None and len(extra):
							extra[:] = lookup[extra]
						sec = ps.secondaryWeightIndicesList
						if sec is not None and len(sec):
							sec[:] = lookup[sec]
					# Bone bounding-box: vectorized min/max scatter into per-bone boxes
					bbox_gi, bbox_pos = bb
					if len(bbox_gi):
						rb = lookup[bbox_gi]
						np.minimum.at(boneMinArr, rb, bbox_pos)
						np.maximum.at(boneMaxArr, rb, bbox_pos)
			# Materialize name-keyed bone boxes for the downstream bounding-box pass
			if isFirstLOD:
				boneMin = {name: boneMinArr[i].tolist()
				           for i, name in enumerate(parsedMesh.skeleton.weightedBones)}
				boneMax = {name: boneMaxArr[i].tolist()
				           for i, name in enumerate(parsedMesh.skeleton.weightedBones)}
			all_used_names.clear()
			all_submesh_vg.clear()

		if "+ Shadow LOD" in lod.name:
			parsedMesh.shadowMeshLinkedLODList.append(parsedLODLevel)
			print(
				f"Shadow LOD {str(len(parsedMesh.shadowMeshLinkedLODList))} linked to Main Mesh LOD {str(lodIndex)}")
		parsedMesh.mainMeshLODList.append(parsedLODLevel)
		isFirstLOD = False
	# End LOD
	if armatureObj == None:
		boneMin = None
		boneMax = None
	printElapsed("Gathering mesh data", meshDataStartTime)
	if armatureObj != None:
		printElapsed("Generating bone remap dictionary", boneRemapStartTime)
	return subMeshCount, vertexCount, faceCount, boneMin, boneMax

def _export_build_bone_bboxes(parsedMesh, armatureObj, options, transform, importedBoneBoundingBoxes,
                               boneMin, boneMax):
	weightStartTime = time.time()
	if armatureObj:
		boneBBoxDict = dict()
		for boneName in parsedMesh.skeleton.weightedBones:
			bonePos = transform @ armatureObj.data.bones[boneName].head_local

			bmin = boneMin[boneName]
			bmax = boneMax[boneName]
			if bmin[0] != math.inf:
				minVec = Vector(bmin) - bonePos
				maxVec = Vector(bmax) - bonePos
			else:
				raiseWarning(f"{boneName} has zero weight vertex groups assigned.")
				minVec = Vector((0.0, 0.0, 0.0))
				maxVec = Vector((0.01, 0.01, 0.01))
			boneBBoxDict[boneName] = {"min": minVec, "max": maxVec}

		if parsedMesh.bufferHasSecondaryWeight and len(parsedMesh.skeleton.boneList) > 1:
			# DD2, mark all bones as secondary weight if at least one bone is
			for bone in parsedMesh.skeleton.boneList[1::]:
				bone.useSecondaryWeight = 1
		# Assign bounding boxes to bones
		for bone in parsedMesh.skeleton.boneList:

			# Check if using DD2 secondary weight
			# if bone.boneName in shapeKeyBoneSet:
			# bone.useSecondaryWeight = 1
			if bone.boneName in boneBBoxDict:
				if options["exportBoundingBoxes"] and bone.boneName in importedBoneBoundingBoxes:
					bone.boundingBox = importedBoneBoundingBoxes[bone.boneName]
				else:
					bone.boundingBox = AABB()
					bone.boundingBox.min.x = boneBBoxDict[bone.boneName]["min"][0]
					bone.boundingBox.min.y = boneBBoxDict[bone.boneName]["min"][1]
					bone.boundingBox.min.z = boneBBoxDict[bone.boneName]["min"][2]
					bone.boundingBox.max.x = boneBBoxDict[bone.boneName]["max"][0]
					bone.boundingBox.max.y = boneBBoxDict[bone.boneName]["max"][1]
					bone.boundingBox.max.z = boneBBoxDict[bone.boneName]["max"][2]
		printElapsed("Building bone bounding boxes", weightStartTime)

def _export_calc_mesh_bounds(parsedMesh):
	# Generate mesh bounding box and bounding sphere from lowest quality LOD level
	meshBBoxStartTime = time.time()
	vertArrayList = []
	for group in parsedMesh.mainMeshLODList[-1].visconGroupList:
		vertArrayList.extend([submesh.vertexPosList for submesh in group.subMeshList])
	if vertArrayList != []:
		fullVertArray = np.vstack(vertArrayList)
		if parsedMesh.boundingSphere == None:
			center, radius = bounding_sphere_ritter(fullVertArray)
			parsedMesh.boundingSphere = Sphere()
			parsedMesh.boundingSphere.x = center[0]
			parsedMesh.boundingSphere.y = center[1]
			parsedMesh.boundingSphere.z = center[2]
			parsedMesh.boundingSphere.r = radius
		if parsedMesh.boundingBox == None:
			minVec = Vector(np.min(fullVertArray, axis=0))
			maxVec = Vector(np.max(fullVertArray, axis=0))
			parsedMesh.boundingBox = AABB()
			parsedMesh.boundingBox.min.x = minVec[0]
			parsedMesh.boundingBox.min.y = minVec[1]
			parsedMesh.boundingBox.min.z = minVec[2]
			parsedMesh.boundingBox.max.x = maxVec[0]
			parsedMesh.boundingBox.max.y = maxVec[1]
			parsedMesh.boundingBox.max.z = maxVec[2]
	printElapsed("Calculating mesh bounding sphere and bounding box", meshBBoxStartTime)

def _export_finalize(parsedMesh, meshVersion, filePath, gameName, targetCollection, hashedBoneNameDict,
                      subMeshCount, vertexCount, faceCount, maxWeightedBones, meshExportStartTime,
                      warningDict):
	showWarningMessage = False
	# Warning: Compare the exported mesh materials against the materials in the mesh's MDF file
	if parsedMesh.materialNameList:
		# Prefer the MDF data in the scene (live, reflects renames made after import)
		mdfMaterialNameSet = getMDFMaterialNames(findMDFCollectionForMesh(targetCollection)) or None
		if mdfMaterialNameSet == None:
			# No MDF data in the scene for this mesh, fall back to the .mdf2 on disk
			mdfPath = findMDFPathFromMeshPath(filePath, gameName)
			if mdfPath != None and os.path.isfile(mdfPath):
				try:
					mdfMaterialNameSet = set(
						material.materialName for material in readMDF(mdfPath).materialList)
				except Exception as err:
					print(f"Could not read MDF to compare mesh materials: {str(err)}")
					mdfMaterialNameSet = None
		if mdfMaterialNameSet is not None:
			try:
				mdfLowerNameDict = {name.lower(): name for name in mdfMaterialNameSet}
				for materialName in parsedMesh.materialNameList:
					if materialName.lower() in mdfLowerNameDict:
						del mdfLowerNameDict[materialName.lower()]
					else:
						addErrorToDict(warningDict, "MeshMaterialsMissingFromMDF", materialName)
				for materialName in sorted(mdfLowerNameDict.values()):
					addErrorToDict(warningDict, "MDFMaterialsMissingFromMesh", materialName)
			except Exception as err:
				print(f"Could not compare mesh materials with MDF: {str(err)}")

	if hashedBoneNameDict:  # Translate hashed bone names to their original names
		print("Translating hashed bone names...")
		for bone in parsedMesh.skeleton.boneList:
			if bone.boneName in hashedBoneNameDict:
				print(f"Translated {bone.boneName} to {hashedBoneNameDict[bone.boneName]}")
				bone.boneName = hashedBoneNameDict[bone.boneName]

		for index, boneName in enumerate(parsedMesh.skeleton.weightedBones):
			if boneName in hashedBoneNameDict:
				parsedMesh.skeleton.weightedBones[index] = hashedBoneNameDict[boneName]

	meshWriteStartTime = time.time()
	reMesh = ParsedREMeshToREMesh(parsedMesh, meshVersion)
	if targetCollection != None:
		reMesh.fileHeader.lodGroupNameHash = int(targetCollection.get("LODGroupNameHash", "0"))
	writeREMesh(reMesh, filePath)
	printElapsed("Converting to RE Mesh", meshWriteStartTime)
	vertexBufferString = ""
	if parsedMesh.bufferHasPosition:
		vertexBufferString += "[Position] "
	if parsedMesh.bufferHasNorTan:
		vertexBufferString += "[Normals] "

	if parsedMesh.bufferHasUV:
		vertexBufferString += "[UV1] "

	if parsedMesh.bufferHasUV2:
		vertexBufferString += "[UV2] "

	if parsedMesh.bufferHasWeight:
		vertexBufferString += "[Weight] "
	if parsedMesh.bufferHasColor:
		vertexBufferString += "[Color] "
	if parsedMesh.bufferHasExtraWeight:
		vertexBufferString += "[Extra Weight] "

	meshExportEndTime = time.time()
	meshExportTime = meshExportEndTime - meshExportStartTime
	print(f"Mesh export finished in {y(formatMs(meshExportTime))} ms.")

	print("\nMesh Info:")
	print(f"Mesh Count: {str(subMeshCount)}")
	print(f"Vertex Count: {str(vertexCount)}")
	print(f"Face Count: {str(faceCount)}")
	print(f"Vertex Buffer Format: {vertexBufferString}")
	if parsedMesh.skeleton:
		print(f"Armature Bone Count: {str(len(parsedMesh.skeleton.boneList))}")
		print(f"Weighted Bone Count: {str(len(parsedMesh.skeleton.weightedBones))} / {maxWeightedBones}")
	print(f"Materials ({str(len(parsedMesh.materialNameList))}):")
	for materialName in parsedMesh.materialNameList:
		print(materialName)
	if warningDict and ENABLE_EXPORT_WARNINGS:
		printWarningDict(warningDict)
		showWarningMessage = True
	if showWarningMessage:
		showMessageBox("Warnings occured during export. Check Window > Toggle System Console for details.",
		               title="Mesh Export Warning", icon="ERROR")
	print("\033[92m__________________________________\nRE Mesh export finished.\033[0m")
	return True

def exportREMeshFile(filePath, options):
	# Warning Conditions (non blocking, printed after export finishes)
	# Invalid mesh naming scheme - notified when the object name falls back to viscon group 0 and/or the blender material name
	# Vertex groups weighted to bones that aren't on the armature
	# If an mdf for the mesh is found, check if the mesh materials are mismatched with mdf

	# Error Conditions
	# No meshes in collection or selection x
	# More than one armature in collection x
	# No material on submesh x
	# Loose vertices on submesh x
	# No uv on submesh x
	# Max weighted bones exceeded x
	# Max weights per vertex exceeded x
	# Multiple uvs assigned to single vertex x
	# No vertices on submesh x
	# No faces on submesh x
	# Non triangulated face x
	# Max vertices exceeded x
	# Max faces exceeded x
	# No bones on armature x

	# TODO Error Conditions
	# More than one material on submesh

	errorDict = dict()
	warningDict = dict()  # Non blocking issues that are reported after the export finishes
	# TODO Fix having all bones as weighted bones breaks export
	meshExportStartTime = time.time()
	meshVersion = parseFileVersion(filePath, 0)
	if meshVersion == 0:
		print("Unable to parse mesh version number in file path.")
	if meshVersion in meshFileVersionToGameNameDict:
		gameName = meshFileVersionToGameNameDict[meshVersion]
	else:
		gameName = None

	print("\033[96m__________________________________\nRE Mesh export started.\033[0m")

	if bpy.context and bpy.context.active_object != None:
		bpy.ops.object.mode_set(mode='OBJECT')

	weightSettings = _game_settings(gameName, meshVersion)
	maxWeightedBones = weightSettings[2]

	targetCollection = bpy.data.collections.get(options["targetCollection"])
	bpy.context.scene["REMeshLastExportedMeshVersion"] = meshVersion
	if targetCollection == None:
		print("No target collection set. Using scene collection.")
		targetCollection = bpy.context.scene.collection
	else:
		print(f"Target collection: {targetCollection.name}")
		bpy.context.scene["REMeshLastExportedCollection"] = targetCollection.name

	meshLODCollectionList = []
	boundingBoxCollection = None
	for childCollection in targetCollection.children:
		if "Main Mesh LOD" in childCollection.name:
			meshLODCollectionList.append(childCollection)
		elif childCollection.get("~TYPE") == "RE_MESH_BOUNDING_BOX_COLLECTION":
			boundingBoxCollection = childCollection

	# Find armature and parse it
	armatureObj = None
	for obj in targetCollection.objects:
		if obj.type == "ARMATURE":
			if armatureObj == None:
				armatureObj = obj
			else:
				addErrorToDict(errorDict, "MoreThanOneArmature", None)

	skeleton, transform, hashedBoneNameDict, armatureBoneNameSet = _export_build_skeleton(
		armatureObj, options["rotate90"], options["preserveBoneMatrices"])

	parsedMesh = ParsedREMesh()
	parsedMesh.boundingBox = None
	parsedMesh.boundingSphere = None
	parsedMesh.skeleton = skeleton

	importedBoneBoundingBoxes = _export_read_bounding_boxes(
		boundingBoxCollection, armatureObj, parsedMesh, options["exportBoundingBoxes"])

	if meshLODCollectionList == []:
		meshLODCollectionList = [targetCollection]
	meshLODCollectionList.sort(key=lambda col: col.name)
	if not options["exportAllLODs"]:
		meshLODCollectionList = [meshLODCollectionList[0]]

	dg = bpy.context.evaluated_depsgraph_get()
	deleteCopiedMeshList = []
	cloneMeshNameDict = {}
	subMeshCount, vertexCount, faceCount, boneMin, boneMax = _export_process_lods(
		meshLODCollectionList, options, weightSettings, parsedMesh, errorDict, warningDict, dg,
		armatureObj, armatureBoneNameSet, deleteCopiedMeshList, cloneMeshNameDict)
	_export_build_bone_bboxes(parsedMesh, armatureObj, options, transform, importedBoneBoundingBoxes,
	                          boneMin, boneMax)
	_export_calc_mesh_bounds(parsedMesh)

	if parsedMesh.skeleton and parsedMesh.skeleton.weightedBones and len(
			parsedMesh.skeleton.weightedBones) > maxWeightedBones:
		print(
			f"\nMaximum Weighted Bones Exceeded! {str(len(parsedMesh.skeleton.weightedBones))} / {maxWeightedBones}")
		addErrorToDict(errorDict, "MaxWeightedBonesExceeded", None)
	# Clear references
	for mesh in deleteCopiedMeshList:
		bpy.data.objects.remove(mesh, do_unlink=True)
	# bpy.data.meshes.remove(mesh)
	if "clonedMeshes" in bpy.data.collections:
		bpy.data.collections.remove(bpy.data.collections["clonedMeshes"])
	deleteCopiedMeshList.clear()
	cloneMeshNameDict.clear()

	if subMeshCount == 0:
		addErrorToDict(errorDict, "NoMeshesInCollection", None)

	if errorDict != {}:
		printErrorDict(errorDict)
		showREMeshErrorWindow(targetCollection.name, armatureObj, errorDict)
		return False

	return _export_finalize(parsedMesh, meshVersion, filePath, gameName, targetCollection,
	                        hashedBoneNameDict, subMeshCount, vertexCount, faceCount,
	                        maxWeightedBones, meshExportStartTime, warningDict)
