import bpy
import bmesh
import os
import time
import numpy as np
from math import sqrt
from mathutils import Vector, Matrix
from .file_re_mesh import readREMesh, meshFileVersionToGameNameDict
from .re_mesh_parse import ParsedREMesh
from ..gen_functions import splitNativesPath, raiseWarning, y, printElapsed, formatMs, parseFileVersion
from ..blender_utils import getBlenderSafeBoneName, setAssetPathFromFilePath, createEmpty, rotate90Matrix, rotateNeg90Matrix, MESH_COLLECTION_TYPE
from ..mdf.file_re_mdf import readMDF
from ..mdf.blender_re_mesh_mdf import findMDFPathFromMeshPath, importMDF
from ..mdf.blender_re_mdf import importMDFFile
from ..sfur.blender_re_sfur import importSFurFile, findSFurPathFromMeshPath
from .blender_re_mesh_utils import joinObjects, createMaterialDict, getCollection, findArmatureObjFromData, _IMPORT_PHASES, _phase_switch, _phase_reset


def importSkeleton(parsedSkeleton, armatureName, collection, rotate90, targetArmatureName=None):
	mergedArmature = False
	# Merging with existing armature if specified in import menu

	if targetArmatureName != "" and targetArmatureName in bpy.data.armatures:
		armatureObj = findArmatureObjFromData(bpy.data.armatures[targetArmatureName])
		if armatureObj != None:
			armatureData = armatureObj.data
			mergedArmature = True
		else:
			armatureData = bpy.data.armatures.new(armatureName)
			armatureObj = bpy.data.objects.new(armatureName, armatureData)
			collection.objects.link(armatureObj)

	else:
		armatureData = bpy.data.armatures.new(armatureName)
		armatureObj = bpy.data.objects.new(armatureName, armatureData)
		collection.objects.link(armatureObj)
	armatureObj.hide_viewport = False
	bpy.context.view_layer.objects.active = armatureObj
	bpy.ops.object.mode_set(mode='EDIT')

	boneNameIndexDict = {index: bone.boneName for index, bone in enumerate(parsedSkeleton.boneList)}
	if mergedArmature:
		print(f"Merging imported armature with {armatureObj.name}")
		if rotate90:
			armatureObj.data.transform(
				rotateNeg90Matrix)  # TODO do a less ugly workaround for merging rotated armatures
	elif targetArmatureName != "":
		print(
			"The specified armature to merge with could not be found. Importing the armature as a new object.")
	boneParentList = []  # List of tuples containing armature bone and parent bone name string
	hashedNameDict = dict()
	for bone in parsedSkeleton.boneList:
		if bone.boneName not in armatureData.bones:
			boneName, hashedName = getBlenderSafeBoneName(bone.boneName)
			if hashedName:
				hashedNameDict[bone.boneName] = boneName
			editBone = armatureData.edit_bones.new(boneName)
			if hashedName:
				editBone["unhashedBoneName"] = bone.boneName
			editBone.tail = editBone.head + Vector((.0, .0, .1))
			if bone.parentIndex != -1:
				boneParentName = boneNameIndexDict[bone.parentIndex]
				if boneParentName in hashedNameDict:
					boneParentName = hashedNameDict[boneParentName]
				boneParentList.append(
					(editBone, boneParentName))  # Set bone parents after all bones have been imported
			# editBone.parent = armatureData.edit_bones[boneNameIndexDict[bone.parentIndex]]
			else:
				bone.head = Vector([.0, .0, .01])

			if bone.boundingBox != None:
				editBone.length = sqrt((bone.boundingBox.max.x - bone.boundingBox.min.x) ** 2 + (
							bone.boundingBox.max.y - bone.boundingBox.min.y) ** 2 + (
							                       bone.boundingBox.max.z - bone.boundingBox.min.z) ** 2) * .15
			else:
				editBone.length = .05
			if editBone.length < .01:
				editBone.length = .01
			editBone.matrix = bone.worldMatrix.matrix
			editBone["reMeshWorldMatrix"] = bone.worldMatrix.matrix
			editBone["reMeshLocalMatrix"] = bone.localMatrix.matrix
			editBone["reMeshInverseMatrix"] = bone.inverseMatrix.matrix
			if mergedArmature:
				print(f"[MERGE] Added {bone.boneName} to {armatureObj.name}")
	# Assign bone parents
	for editBone, parentBoneName in boneParentList:
		editBone.parent = armatureData.edit_bones[parentBoneName]

	if mergedArmature:
		if rotate90:
			armatureObj.data.transform(
				rotate90Matrix)  # TODO do a less ugly workaround for merging rotated armatures
	bpy.ops.object.mode_set(mode='OBJECT')

	if rotate90 and targetArmatureName not in bpy.data.objects:
		prevSelection = bpy.context.selected_objects
		for obj in prevSelection:
			obj.select_set(False)

		armatureObj.matrix_world = armatureObj.matrix_world @ rotate90Matrix
		armatureObj.select_set(True)
		# I would prefer not to use bpy.ops but the data.transform on armatures does not function correctly.
		bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
		armatureObj.select_set(False)

		for obj in prevSelection:
			obj.select_set(True)
	return armatureObj



IMPORT_EXTRA_WEIGHTS = True


def _importWeightsToGroup(boneIndicesList, weightList, boneToGroupMap, weightFilterList=None):
	"""Batch import vertex weights using per-(bone, weight) group add.
	Groups (vertex, weight) by bone then sub-groups by weight value,
	so VertexGroup.add() processes multiple vertices per call.
	
	Fully vectorized: uses NumPy advanced indexing to avoid per-vertex Python loop.
	"""
	boneArr = np.asarray(boneIndicesList)
	weightArr = np.asarray(weightList)

	if weightFilterList is not None:
		filterArr = np.asarray(weightFilterList)
		active_mask = filterArr > 0
	else:
		active_mask = weightArr > 0

	# Vectorized: find all active (vertex, slot) pairs in one shot
	active_vi, active_si = np.where(active_mask)
	if len(active_vi) == 0:
		return

	# Extract bone indices and weights for active slots (vectorized)
	active_bones = boneArr[active_vi, active_si].astype(np.int32)
	active_weights = weightArr[active_vi, active_si]
	# Round weights to 4 decimal places for grouping key
	active_weights_rounded = np.round(active_weights, 4)

	# Group by (bone, weight) using numpy sort + boundary detection
	# This replaces the O(N) Python dict loop with O(N log N) numpy sort
	# then iterates only over unique (bone, weight) pairs (M << N)
	bone_keys = active_bones.astype(np.int64)
	weight_keys = np.round(active_weights_rounded * 10000).astype(np.int64)
	composite = bone_keys << 44 | weight_keys
	order = np.argsort(composite)
	sorted_vi = active_vi[order]
	sorted_bones = active_bones[order]
	sorted_weights = active_weights_rounded[order]
	sorted_composite = composite[order]
	boundaries = np.where(sorted_composite[:-1] != sorted_composite[1:])[0] + 1
	starts = np.concatenate([[0], boundaries])
	ends = np.concatenate([boundaries, [len(sorted_composite)]])
	for s, e in zip(starts, ends):
		bi = int(sorted_bones[s])
		w = float(sorted_weights[s])
		# .tolist() is required: some Blender builds strictly check that
		# VertexGroup.add() receives Python ints and reject numpy int64 items.
		verts = sorted_vi[s:e].tolist()
		vg = boneToGroupMap[bi]
		vg.add(verts, w, 'ADD')


def buildBoneNameMaps(boneNameList):
	"""Precompute all bone name variations (primary + shapekey) in single pass.
	
	Avoids duplicate string operations and hashing when processing both
	primary and secondary (shapekey) vertex weights.
	
	Returns:
		tuple: (boneNameMap, secBoneNameMap) dictionaries mapping bone indices to names
	"""
	boneNameMap = {}
	secBoneNameMap = {}
	
	for idx, name in enumerate(boneNameList):
		# Primary bone name
		boneNameMap[idx] = getBlenderSafeBoneName(name)[0]
		
		# Secondary (shapekey) bone name
		secName = "SHAPEKEY_" + name
		secBoneNameMap[idx] = getBlenderSafeBoneName(secName)[0]
	
	return boneNameMap, secBoneNameMap


# --- lightweight import phase profiler (accumulated across submeshes, printed
# once per import) ---



def importMesh(meshName="newMesh", vertexList=[], faceList=[], vertexNormalList=[], vertexColor0List=[],
               vertexColor1List=[], UV0List=[], UV1List=[], UV2List=[], boneNameList=[],
               vertexGroupWeightList=[], vertexGroupBoneIndicesList=[], extraVertexGroupWeightList=[],
               extraVertexGroupBoneIndicesList=[], vertexGroupWeightListSecondary=[],
               vertexGroupBoneIndicesListSecondary=[], boneNameRemapList=[], material="Material",
               armature=None, collection=None, rotate90=True, blendShapeList=[]):
	_phase_switch("construct")
	meshData = bpy.data.meshes.new(meshName)
	# Import vertices and faces
	if len(vertexList) == 0:
		raise Exception("Invalid mesh, submesh has no vertices")
	if len(faceList) == 0:
		raise Exception("Invalid mesh, submesh has no faces")
	# Bulk-construct the mesh with add() + foreach_set instead of from_pydata
	# (same philosophy as the vectorized export gathering): numpy buffers are
	# handed straight to Blender with no per-vertex/per-face Python conversion.
	vertexArr = np.asarray(vertexList, dtype=np.float32)
	try:
		faceArr = np.asarray(faceList)
	except ValueError:  # Ragged Python list (non-uniform face sizes) - handled by the fallback below
		faceArr = faceList
	n_verts = len(vertexArr)
	n_polys = len(faceArr)
	if hasattr(faceArr, "ndim") and faceArr.ndim == 2 and faceArr.shape[1] == 3:  # Standard RE mesh: all triangles
		n_loops = n_polys * 3
		loop_start = np.arange(n_polys, dtype=np.int32) * 3
		loop_total = np.full(n_polys, 3, dtype=np.int32)
		loop_vert = faceArr.reshape(-1)
	else:  # Defensive fallback for non-uniform face sizes
		loop_sizes = np.asarray([len(f) for f in faceArr], dtype=np.int32)
		loop_start = np.concatenate([[0], np.cumsum(loop_sizes)[:-1]]).astype(np.int32)
		loop_total = loop_sizes
		loop_vert = np.concatenate([np.asarray(f, dtype=np.int32) for f in faceArr])
	meshData.vertices.add(n_verts)
	meshData.vertices.foreach_set("co", vertexArr.reshape(-1))
	if n_polys > 0:
		meshData.loops.add(n_loops)
		meshData.loops.foreach_set("vertex_index", loop_vert.astype(np.int32, copy=False))
		meshData.polygons.add(n_polys)
		meshData.polygons.foreach_set("loop_start", loop_start)
		meshData.polygons.foreach_set("loop_total", loop_total)
	# from_pydata used to compute edges internally. Blender <4.0's
	# calc_normals_split() needs edge data, so keep calc_edges there; on 4.0+
	# edges are computed lazily by Blender and nothing in this addon reads
	# mesh.edges (export/solve-UVs/split-sharp all work on loops/bmesh), so the
	# expensive per-submesh edge build is skipped on big imports.
	meshData.update(calc_edges=(bpy.app.version < (4, 0, 0)))
	# Import vertex normals
	if len(vertexNormalList) > 0:
		_phase_switch("normals_prep")
		meshData.polygons.foreach_set("use_smooth", np.ones(len(meshData.polygons), dtype=bool))
		if bpy.app.version < (4, 0, 0):
			# Validate before setting custom normals to avoid rare crashes on old
			# Blender. The mesh is bulk-constructed and well-formed (exact array
			# sizes, in-range indices) and RE files have no loose vertices, so on
			# 4.0+ this O(n) full-mesh sanity pass is pure overhead (~0.5s on a
			# 460k-vert import) and is skipped.
			meshData.validate()
		# Vectorized normal normalization - much faster than Python loop
		normals = np.asarray(vertexNormalList, dtype=np.float32)
		norms = np.linalg.norm(normals, axis=1, keepdims=True)
		norms[norms == 0] = 1.0  # Avoid division by zero
		normalized_normals = normals / norms
		_phase_switch("normals_c")
		# Blender's normals_split_custom_set_from_vertices() iterates every vertex
		# through the Python sequence protocol IN C: passing a numpy array forces
		# a slow per-row PySequence_Fast + numpy-scalar float conversion for each
		# vertex (~575ms on 460k verts). Feeding .tolist() gives pure Python
		# floats, which the C code reads directly - counterintuitive but measured
		# ~2x faster than handing it the numpy array.
		meshData.normals_split_custom_set_from_vertices(normalized_normals.tolist())

		if bpy.app.version < (4, 0, 0):
			meshData.use_auto_smooth = True
			meshData.calc_normals_split()
	# Import UV Layers and Vertex Colors - precompute loop indices once
	_phase_switch("uv_color")
	if bpy.app.version >= (4, 0, 0):
		# No validate() on 4.0+ (see normals phase), so the loop->vertex indices
		# are exactly what was written during construction - reuse them instead
		# of paying a multi-million element foreach_get per submesh.
		loop_vertex_indices = loop_vert.astype(np.int32, copy=False)
	else:
		loop_vertex_indices = np.zeros(len(meshData.loops), dtype=np.int32)
		meshData.loops.foreach_get("vertex_index", loop_vertex_indices)

	UVLayerList = (UV0List, UV1List, UV2List)
	for layerIndex, layer in enumerate(UVLayerList):
		if len(layer) > 0:
			newUVLayer = meshData.uv_layers.new(name="UVMap" + str(layerIndex))
			uv_data = np.asarray(layer, dtype=np.float32)[loop_vertex_indices]
			newUVLayer.data.foreach_set("uv", uv_data.ravel())

	# Import vertex color layer 0
	if len(vertexColor0List) > 0:
		vcol_layer = meshData.vertex_colors.new()
		color_data = np.asarray(vertexColor0List, dtype=np.float32)[loop_vertex_indices]
		vcol_layer.data.foreach_set("color", color_data.ravel())

	meshObj = bpy.data.objects.new(meshName, meshData)

	# Import Weights
	_phase_switch("weights")
	if len(vertexGroupWeightList) > 0 and len(boneNameList) > 0:
		# Precompute bone name mapping to avoid repeated hashing (single pass for both primary + secondary)
		boneNameMap, secBoneNameMap = buildBoneNameMaps(boneNameList)
		# Only create vertex groups for bones that get used
		if len(boneNameList) > 1:
			# Only create groups for bones that actually have non-zero weight.
			# Unused skinning slots are padded with boneIndex 0 (root bone, e.g. "Hip")
			# and weight 0, so without this filter every object would get a bogus
			# empty vertex group for the root bone.
			boneArr = np.asarray(vertexGroupBoneIndicesList)
			weightArr = np.asarray(vertexGroupWeightList)
			usedBoneIndices = sorted(np.unique(boneArr[weightArr > 0]).tolist())
			if len(extraVertexGroupBoneIndicesList) > 0 and len(extraVertexGroupBoneIndicesList[0]) > 0:
				extraBoneArr = np.asarray(extraVertexGroupBoneIndicesList)
				extraWeightArr = np.asarray(extraVertexGroupWeightList)
				usedBoneIndices = sorted(np.unique(np.concatenate([
					np.asarray(usedBoneIndices),
					extraBoneArr[extraWeightArr > 0]
				])).tolist())
			# Create vertex groups and build index-to-group lookup (avoids string-based lookups in inner loops)
			boneIndexToVGroup = {}
			for boneIndex in usedBoneIndices:
				boneIndexToVGroup[boneIndex] = meshObj.vertex_groups.new(name=boneNameMap[boneIndex])
			# Optimized weight assignment: use NumPy boolean mask to pre-filter zero weights, batch by bone
			_importWeightsToGroup(vertexGroupBoneIndicesList, vertexGroupWeightList, boneIndexToVGroup)
			if len(extraVertexGroupWeightList) > 0 and IMPORT_EXTRA_WEIGHTS:
				_importWeightsToGroup(extraVertexGroupBoneIndicesList, extraVertexGroupWeightList,
				                      boneIndexToVGroup)
		else:  # No bone remap table edge case
			vg = meshObj.vertex_groups.new(name=boneNameMap[0])
			vg.add(np.arange(len(meshObj.data.vertices)).tolist(), 1.0, 'REPLACE')

	# DD2 Shapekey Weights
	# Import Secondary Weights

	if vertexGroupWeightListSecondary != [] and boneNameList != []:
		# Use precomputed secondary bone name map from buildBoneNameMaps (no duplicate work)
		# Only create vertex groups for bones that get used
		# Filter by active slots (same mask _importWeightsToGroup uses: primary weight > 0)
		secBoneArr = np.asarray(vertexGroupBoneIndicesListSecondary)
		primWeightArr = np.asarray(vertexGroupWeightList)
		usedBoneIndices = sorted(np.unique(secBoneArr[primWeightArr > 0]).tolist())
		if len(boneNameList) > 1:
			# Create vertex groups and build index-to-group lookup
			boneIndexToSecVGroup = {}
			for boneIndex in usedBoneIndices:
				boneIndexToSecVGroup[boneIndex] = meshObj.vertex_groups.new(name=secBoneNameMap[boneIndex])
			_importWeightsToGroup(vertexGroupBoneIndicesListSecondary, vertexGroupWeightListSecondary,
			                      boneIndexToSecVGroup, vertexGroupWeightList)
		else:  # No bone remap table edge case
			vg = meshObj.vertex_groups.new(name=secBoneNameMap[0])
			vg.add(np.arange(len(meshObj.data.vertices)).tolist(), 1.0, 'REPLACE')

	_phase_switch("finalize")
	if armature != None:
		meshObj.parent = armature
		mod = meshObj.modifiers.new(name='Armature', type='ARMATURE')
		mod.object = armature
	# meshObj.matrix_parent_inverse = armature.matrix_world.inverted()
	if rotate90:
		meshObj.data.transform(rotate90Matrix)
	# meshObj.matrix_world = meshObj.matrix_world @ rotate90Matrix
	if material != None:
		meshObj.data.materials.append(material)
	if collection != None:
		collection.objects.link(meshObj)
	else:
		bpy.context.scene.collection.objects.link(meshObj)

	# Import Blend Shapes
	if blendShapeList != []:
		skB = meshObj.shape_key_add(name="Basis")
		skB.interpolation = 'KEY_LINEAR'

		# Pre-fetch basis coordinates once for all blend shapes
		n_verts = len(meshObj.data.vertices)
		basis_co = np.zeros(n_verts * 3, dtype=np.float32)
		skB.data.foreach_get("co", basis_co)

		for blendShapeEntry in blendShapeList:
			name = blendShapeEntry.blendShapeName
			deltas = np.asarray(blendShapeEntry.deltas, dtype=np.float32)
			sk = meshObj.shape_key_add(name=name)
			sk.interpolation = 'KEY_LINEAR'
			# Batch-set: add deltas to basis coordinates in one numpy operation
			new_co = basis_co + deltas.ravel()
			sk.data.foreach_set("co", new_co)

	_phase_switch(None)
	return meshObj


# Option (importLODGroup's mergeSameMaterialSubmeshes, exposed in the import
# dialog, OFF by default): build ONE Blender mesh per (viscon group, material)
# instead of one per submesh, by concatenating same-material submeshes in numpy
# before creating the mesh. The dominant import cost on many-submesh files is the
# per-submesh Blender build (mesh create + normals + uv layers + link), so merging
# N submeshes into ~N/materials builds speeds up large imports substantially.
# NOTE: merged meshes lose per-submesh identity, so the exported file structure
# differs from the original on re-export. Reused meshes, blend shapes, extra
# weights and DD2 secondary weights are never merged (they keep the safe path).
MERGE_VERTEX_LIMIT = 60000


def _subMeshMergeProfile(subMesh):
	"""Return a (buffer-presence) profile for a submesh if it is safe to merge
	into a combined mesh, or None if it must be imported individually.

	Submeshes carrying blend shapes, extra (MH Wilds) weights or DD2 secondary
	weights can't be merged without losing data, so they keep the per-submesh path.
	"""
	if subMesh.blendShapeList:
		return None
	if getattr(subMesh, "extraWeightList", None):
		return None
	if getattr(subMesh, "secondaryWeightList", None):
		return None
	has = lambda x: x is not None and len(x) > 0
	return (
		has(subMesh.normalList),
		has(subMesh.uvList),
		has(subMesh.uv2List),
		has(subMesh.colorList),
		has(subMesh.weightList),
	)


def _importMergedSubMeshes(subMeshList, materialName, LODNum, groupNum, boneNameList,
                           materialDict, armatureObj, collection, rotate90):
	"""Concatenate same-material submeshes (vertex buffers + face indices offset
	per submesh) and import them as a single Blender mesh."""
	vertParts = []
	faceParts = []
	offsets = []
	offset = 0
	for subMesh in subMeshList:
		offsets.append(offset)
		offset += len(subMesh.vertexPosList)
	for subMesh, vertOffset in zip(subMeshList, offsets):
		vertParts.append(np.asarray(subMesh.vertexPosList, dtype=np.float32))
		faces = np.asarray(subMesh.faceList)
		# Promote before offsetting so uint16 face buffers can't wrap around
		faceParts.append((faces.astype(np.int64) + vertOffset).astype(np.uint32))

	def _cat(attr, dtype=None):
		return np.concatenate([np.asarray(getattr(s, attr), dtype=dtype) for s in subMeshList])

	firstSub = subMeshList[0]
	mergedName = (f"{LODNum}Group_{str(groupNum)}_Sub_{str(firstSub.subMeshIndex)}_"
	              f"{str(len(subMeshList))}__{materialName}")
	has = lambda x: x is not None and len(x) > 0
	return importMesh(
		meshName=mergedName,
		vertexList=np.concatenate(vertParts),
		faceList=np.concatenate(faceParts),
		vertexNormalList=_cat("normalList", np.float32) if has(firstSub.normalList) else [],
		vertexColor0List=_cat("colorList", np.float32) if has(firstSub.colorList) else [],
		UV0List=_cat("uvList", np.float32) if has(firstSub.uvList) else [],
		UV1List=_cat("uv2List", np.float32) if has(firstSub.uv2List) else [],
		UV2List=[],
		boneNameList=boneNameList,
		vertexGroupWeightList=_cat("weightList", np.float32) if has(firstSub.weightList) else [],
		vertexGroupBoneIndicesList=_cat("weightIndicesList") if has(firstSub.weightList) else [],
		material=materialDict[materialName],
		armature=armatureObj,
		collection=collection,
		rotate90=rotate90,
	)


def importLODGroup(parsedMesh, meshType, meshCollection, materialDict, armatureObj, hiddenCollectionSet,
                   meshOffsetDict, importAllLODs=False, createCollections=True, importShadowMeshes=False,
                   rotate90=True, mergeGroups=False, importBoundingBoxes=False,
                   mergeSameMaterialSubmeshes=False):
	if meshType == "Main Mesh":
		shortName = "Main"
		targetLODList = parsedMesh.mainMeshLODList
	elif meshType == "Shadow Mesh":
		shortName = "Shadow"
		targetLODList = parsedMesh.shadowMeshLODList
	elif meshType == "Occlusion Mesh":
		shortName = "Occlusion"
		targetLODList = parsedMesh.occlusionMeshLODList
	firstLOD = True

	if parsedMesh.skeleton != None:
		if parsedMesh.skeleton.weightedBones != []:
			boneNameList = parsedMesh.skeleton.weightedBones
		elif len(parsedMesh.skeleton.boneList) != 0:  # No bone remap table
			boneNameList = [parsedMesh.skeleton.boneList[0].boneName]
	else:
		boneNameList = []

	if not importAllLODs and targetLODList != []:
		targetLODList = [targetLODList[0]]

	# Offsets that are referenced by isReusedMesh consumers must stay reachable
	# through meshOffsetDict, so their source submeshes are never merged.
	reuseSourceOffsets = set()
	if mergeSameMaterialSubmeshes:
		for lod in targetLODList:
			for visconGroup in lod.visconGroupList:
				for subMesh in visconGroup.subMeshList:
					if subMesh.isReusedMesh:
						reuseSourceOffsets.add(subMesh.meshVertexOffset)

	if parsedMesh.isMPLY:
		MPLYRoot = createEmpty(
			f"Meshlet Root" + f" - {meshCollection.name}" if meshCollection != None else "",
			[("~TYPE", "RE_MESH_MPLY_ROOT")], collection=meshCollection)
	totalMergedSubMeshes = 0
	totalMergedMeshes = 0
	for lodIndex, lod in enumerate(targetLODList):
		shadowLODString = ""
		if importShadowMeshes:
			if lod in parsedMesh.shadowMeshLinkedLODList:
				shadowLODString = f" + Shadow LOD{parsedMesh.shadowMeshLinkedLODList.index(lod)}"
		if createCollections and importAllLODs:
			lodCollection = getCollection(
				f"{meshType} LOD{str(lodIndex)}{shadowLODString} - {meshCollection.name}", meshCollection,
				makeNew=True)
			lodCollection["LOD Distance"] = lod.lodDistance
		else:
			lodCollection = meshCollection
		if not firstLOD and createCollections:
			# lodCollection.hide_viewport = True
			hiddenCollectionSet.add(lodCollection.name)
		for visconGroup in lod.visconGroupList:
			objMergeList = []
			LODNum = f"LOD_{str(lodIndex)}_" if importAllLODs else ""
			mergedSubMeshIDs = set()
			if mergeSameMaterialSubmeshes and not parsedMesh.isMPLY:
				# Pre-pass: bucket mergeable same-material submeshes and build each
				# bucket as ONE larger mesh (fewer Blender builds = faster import).
				# Buckets are vertex-capped: once adding a submesh would push the
				# cumulative vertex count past MERGE_VERTEX_LIMIT, the bucket is
				# closed and a new one is started, so no merged mesh can exceed
				# the RE 16-bit index limit. A bucket with a single member is not
				# merged (imported individually like before).
				mergeBuckets = {}  # (materialName, profile) -> list of (bucket, vert_count)
				for subMesh in visconGroup.subMeshList:
					if subMesh.isReusedMesh:
						continue
					profile = _subMeshMergeProfile(subMesh)
					if profile is None or subMesh.meshVertexOffset in reuseSourceOffsets:
						continue
					materialName = parsedMesh.materialNameList[subMesh.materialIndex]
					key = (materialName, profile)
					vcount = len(subMesh.vertexPosList)
					buckets = mergeBuckets.get(key)
					if buckets is None:
						mergeBuckets[key] = [([subMesh], vcount)]
					else:
						lastBucket, lastCount = buckets[-1]
						if lastCount + vcount <= MERGE_VERTEX_LIMIT:
							lastBucket.append(subMesh)
							buckets[-1] = (lastBucket, lastCount + vcount)
						else:
							buckets.append(([subMesh], vcount))
				for (materialName, _), bucketList in mergeBuckets.items():
					for mergeList, _ in bucketList:
						if len(mergeList) < 2:
							continue
						meshObj = _importMergedSubMeshes(
							mergeList, materialName, LODNum, visconGroup.visconGroupNum,
							boneNameList, materialDict, armatureObj, lodCollection, rotate90)
						if mergeGroups:
							objMergeList.append(meshObj)
						totalMergedSubMeshes += len(mergeList)
						totalMergedMeshes += 1
						for subMesh in mergeList:
							mergedSubMeshIDs.add(id(subMesh))
			for subMesh in visconGroup.subMeshList:
				if id(subMesh) in mergedSubMeshIDs:
					continue
				if subMesh.isReusedMesh:
					lodCollection.objects.link(meshOffsetDict[subMesh.meshVertexOffset])
				else:
					materialName = parsedMesh.materialNameList[subMesh.materialIndex]
					meshObj = importMesh(
						meshName=f"{LODNum}Group_{str(visconGroup.visconGroupNum)}_Sub_{str(subMesh.subMeshIndex)}__{materialName}",
						vertexList=subMesh.vertexPosList,
						faceList=subMesh.faceList,
						vertexNormalList=subMesh.normalList,
						vertexColor0List=subMesh.colorList,
						UV0List=subMesh.uvList,
						UV1List=subMesh.uv2List,
						boneNameList=boneNameList,
						vertexGroupWeightList=subMesh.weightList,
						vertexGroupBoneIndicesList=subMesh.weightIndicesList,
						extraVertexGroupWeightList=subMesh.extraWeightList,
						extraVertexGroupBoneIndicesList=subMesh.extraWeightIndicesList,
						vertexGroupWeightListSecondary=subMesh.secondaryWeightList,
						vertexGroupBoneIndicesListSecondary=subMesh.secondaryWeightIndicesList,
						material=materialDict[materialName],
						armature=armatureObj,
						collection=lodCollection,
						rotate90=rotate90,
						blendShapeList=subMesh.blendShapeList,
					)
					if parsedMesh.isMPLY:
						meshObj.parent = MPLYRoot

						if rotate90:
							meshObj.location = (subMesh.relPos[0], subMesh.relPos[2], subMesh.relPos[1])
						else:
							meshObj.location = subMesh.relPos

						if importBoundingBoxes:
							importBoundingBox(
								subMesh.boundingBox,
							    f"BBOX: {LODNum}Group_{str(visconGroup.visconGroupNum)}_Sub_{str(subMesh.subMeshIndex)}__{materialName}",
							    meshCollection, rotate90=rotate90
							)
					if mergeGroups:
						objMergeList.append(meshObj)
					meshOffsetDict[subMesh.meshVertexOffset] = meshObj

			if mergeGroups and len(objMergeList) > 1:
				joinObjects(objMergeList)
		firstLOD = False
	if totalMergedMeshes:
		print(f"Merged {totalMergedSubMeshes} submeshes into {totalMergedMeshes} meshes "
		      f"(merge same material submeshes).")



def importBoundingBox(bbox, bboxName, meshCollection, armatureObj=None, boneParent=None, rotate90=True):
	bboxVertList = [
		(bbox.min.x, bbox.min.y, bbox.min.z),
		(bbox.max.x, bbox.max.y, bbox.max.z),
	]
	bboxData = bpy.data.meshes.new(bboxName)
	bboxData.from_pydata(bboxVertList, [], [])
	bboxData.update()

	bboxObj = bpy.data.objects.new(bboxName, bboxData)
	meshCollection.objects.link(bboxObj)

	if armatureObj != None and boneParent != None:
		boneName = getBlenderSafeBoneName(boneParent)[0]
		constraint = bboxObj.constraints.new(type="CHILD_OF")
		constraint.target = armatureObj
		constraint.subtarget = boneName
		constraint.name = "BoneName"
		constraint.inverse_matrix = Matrix()
		bboxObj["~TYPE"] = "RE_MESH_BONE_BOUNDING_BOX"
	else:
		bboxObj["~TYPE"] = "RE_MESH_BOUNDING_BOX"
		if rotate90:
			bboxObj.matrix_world = bboxObj.matrix_world @ rotate90Matrix

	bboxObj["MeshExportExclude"] = 1
	bboxObj.show_bounds = True
	return bboxObj


def importBoundingSphere(sphere, sphereName, meshCollection, rotate90=True):
	# Create an empty mesh and the object.
	sphereData = bpy.data.meshes.new(sphereName)
	sphereObj = bpy.data.objects.new(sphereName, sphereData)
	sphereObj.location = (sphere.x, sphere.y, sphere.z)
	sphereObj.display_type = "BOUNDS"
	sphereObj.display_bounds_type = "SPHERE"
	sphereObj["~TYPE"] = "RE_MESH_BOUNDING_SPHERE"
	sphereObj["MeshExportExclude"] = 1
	# sphereData.update()

	# Add the object into the scene.
	meshCollection.objects.link(sphereObj)

	# Construct the bmesh sphere and assign it to the blender mesh.
	bm = bmesh.new()
	bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=8, radius=sphere.r)
	bm.to_mesh(sphereData)
	bm.free()
	bpy.context.view_layer.update()
	if rotate90:
		sphereObj.matrix_world = rotate90Matrix @ sphereObj.matrix_world
	return sphereObj


def importBoundingBoxes(meshBoundingBox, meshBoundingSphere, meshCollection, armatureObj, parsedSkeleton=None,
                        rotate90=True):
	meshBBox = importBoundingBox(meshBoundingBox, "Mesh Bounding Box", meshCollection, rotate90=rotate90)
	meshSphere = importBoundingSphere(meshBoundingSphere, "Mesh Bounding Sphere", meshCollection,
	                                  rotate90=rotate90)
	if parsedSkeleton != None:
		for bone in parsedSkeleton.boneList:
			if bone.boundingBox != None:
				importBoundingBox(bone.boundingBox, f"Bone Bounding Box ({bone.boneName})", meshCollection,
				                  armatureObj, bone.boneName, rotate90)


meshGameNameConflictDict = set(["RERT"])  # Games that use the same mesh version


def resolveMeshGameNameConflict(gameName, filePath):
	rootPath = os.path.split(filePath)[0]
	realGameName = None
	if gameName == "RERT":
		if "RE2" in rootPath:
			realGameName = "RE2RT"
		elif "RE3" in rootPath or "escape" in rootPath.lower():
			realGameName = "RE3RT"
		else:
			realGameName = "RE2RT"
	if realGameName == None:
		realGameName = gameName
	return realGameName



def importREMeshFile(filePath, options):
	meshImportStartTime = time.time()
	fileName = os.path.split(filePath)[1].split(".mesh")[0]
	meshVersion = parseFileVersion(filePath, None)
	if meshVersion is None:
		print("Unable to parse mesh version number in file path.")
	if meshVersion in meshFileVersionToGameNameDict:
		gameName = meshFileVersionToGameNameDict[meshVersion]
		if gameName in meshGameNameConflictDict:
			gameName = resolveMeshGameNameConflict(gameName, filePath)
	else:
		gameName = None
	warningList = []
	errorList = []

	if options["clearScene"]:
		for collection in bpy.data.collections:
			for obj in collection.objects:
				collection.objects.unlink(obj)
			bpy.data.collections.remove(collection)
		for bpy_data_iter in (bpy.data.objects, bpy.data.meshes, bpy.data.lights, bpy.data.cameras):
			for id_data in bpy_data_iter:
				bpy_data_iter.remove(id_data)
		for material in bpy.data.materials:
			bpy.data.materials.remove(material)
		for amt in bpy.data.armatures:
			bpy.data.armatures.remove(amt)
		for obj in bpy.data.objects:
			bpy.data.objects.remove(obj)
			obj.user_clear()
		for nodeGroup in bpy.data.node_groups:
			bpy.data.node_groups.remove(nodeGroup)
		for img in bpy.data.images:
			if not img.users:
				bpy.data.images.remove(img)

	print("\033[96m__________________________________\nRE Mesh import started.\033[0m")
	if options["importAllLODs"]:
		lodTarget = None
	else:
		lodTarget = 0
	reMesh = readREMesh(filePath, lodTarget)
	meshFileName = os.path.splitext(os.path.split(filePath)[1])[0]
	meshParseStartTime = time.time()
	parsedMesh = ParsedREMesh()
	parsedMesh.ParseREMesh(reMesh)
	printElapsed("Mesh parsing", meshParseStartTime)
	armatureObj = None
	parentCollection = None  # Collection for grouping mesh and mdf
	if options["createCollections"]:
		if options["loadMDFData"]:
			parentCollection = getCollection(meshFileName.split(".mesh")[0], makeNew=True)
		meshCollection = getCollection(meshFileName, parentCollection, makeNew=True)
		meshCollection.color_tag = "COLOR_01"
		meshCollection["~TYPE"] = MESH_COLLECTION_TYPE
		meshCollection["LODGroupNameHash"] = str(reMesh.fileHeader.lodGroupNameHash)
		setAssetPathFromFilePath(filePath, meshCollection)
		meshCollection["~MESHFILEPATH"] = os.path.abspath(filePath) #Store the source file path so operators can default to the folder this mesh came from
		bpy.context.scene.re_mdf_toolpanel.meshCollection = meshCollection
	else:
		meshCollection = bpy.context.scene.collection
	hiddenCollectionSet = set()
	if parsedMesh.skeleton != None:
		armatureObj = importSkeleton(parsedMesh.skeleton, meshFileName.split(".mesh")[0] + " Armature",
		                             meshCollection, options["rotate90"], options["mergeArmature"])
	# Create dictionary of material names mapping to material data to avoid assigning the wrong material in case of name duplication
	materialDict = createMaterialDict(parsedMesh.materialNameList)
	meshOffsetDict = dict()

	if not options["importArmatureOnly"]:
		meshBuildStartTime = time.time()
		importLODGroup(parsedMesh, "Main Mesh", meshCollection, materialDict, armatureObj,
		               hiddenCollectionSet, meshOffsetDict, options["importAllLODs"],
		               options["createCollections"], options["importShadowMeshes"], options["rotate90"],
		               options["mergeGroups"], options["importBoundingBoxes"],
		               options.get("mergeSameMaterialSubmeshes", False))
		printElapsed("Mesh build", meshBuildStartTime)
		if options["importOcclusionMeshes"] and parsedMesh.occlusionMeshLODList != []:
			occlusionBuildStartTime = time.time()
			importLODGroup(parsedMesh, "Occlusion Mesh", meshCollection, materialDict, armatureObj,
			               hiddenCollectionSet, meshOffsetDict, options["importAllLODs"],
			               options["createCollections"], options["importShadowMeshes"], options["rotate90"],
			               options["mergeGroups"], options["importBoundingBoxes"],
			               options.get("mergeSameMaterialSubmeshes", False))
			printElapsed("Occlusion mesh build", occlusionBuildStartTime)
		if _IMPORT_PHASES:
			phaseStr = ", ".join(f"{name} {sec * 1000:.0f}ms" for name, sec in sorted(_IMPORT_PHASES.items()))
			print(f"Mesh import phases: {phaseStr}")
			_phase_reset()
	# Hide other lods in viewport

	collections = bpy.context.view_layer.layer_collection.children
	for collection in collections:
		if collection.name == meshCollection.name:
			for childCollection in collection.children:
				if childCollection.name in hiddenCollectionSet:
					childCollection.hide_viewport = True
			break

	meshOffsetDict.clear()
	if options["loadMaterials"] or options["loadMDFData"]:
		if options["mdfPath"] != "":
			mdfPath = options["mdfPath"]
		else:
			mdfPath = findMDFPathFromMeshPath(filePath, gameName)
		try:
			if mdfPath != None:
				split = splitNativesPath(mdfPath)
				if split != None:
					chunkPath = split[0]
				else:
					chunkPath = ""
				mdfImportStartTime = time.time()
				mdfFile = readMDF(mdfPath)  # Read once, shared by both consumers below.
				if options["loadMDFData"]:
					print("Loading MDF Data...")
					try:
						importMDFFile(mdfPath, parentCollection=parentCollection, mdfFile=mdfFile)
					except Exception as err:
						raiseWarning("Could not import MDF data from " + mdfPath + ":" + str(err))
						warningList.append("Could not import MDF data from " + mdfPath + ":" + str(err))
				if options["loadMaterials"] and not options["importArmatureOnly"]:
					if options["loadMDFData"]:
						print("Loading Mesh Materials From MDF...")
					importMDF(mdfFile, materialDict, options["loadUnusedTextures"],
					          options["loadUnusedProps"], options["useBackfaceCulling"],
					          options["reloadCachedTextures"], chunkPath=chunkPath, gameName=gameName,
					          arrangeNodes=True)

					mdfImportEndTime = time.time()
					mdfImportTime = mdfImportEndTime - mdfImportStartTime
					printElapsed("Material importing", mdfImportStartTime)
			else:
				warningList.append("MDF file not found.")
		except Exception as err:
			warningList.append("Could not import mesh materials from " + mdfPath + ":" + str(err))

	if options["loadShellFur"]:
		sFurPath = findSFurPathFromMeshPath(filePath, gameName)
		if sFurPath != None:
			print("Loading SFur Data...")
			try:
				importSFurFile(sFurPath, parentCollection=parentCollection)
			except Exception as err:
				raiseWarning("Could not import SFur data from " + sFurPath + ":" + str(err))
				warningList.append("Could not import SFur data from " + sFurPath + ":" + str(err))

	if options["createCollections"]:
		bpy.context.scene["REMeshLastImportedCollection"] = meshCollection.name
	bpy.context.scene["REMeshLastImportedMeshVersion"] = meshVersion
	if options["importBoundingBoxes"]:
		if options["createCollections"]:
			boundingBoxCollection = getCollection(f"{meshFileName} Bounding Boxes", meshCollection,
			                                      makeNew=True)
			boundingBoxCollection["~TYPE"] = "RE_MESH_BOUNDING_BOX_COLLECTION"
		else:
			boundingBoxCollection = meshCollection
		if not parsedMesh.isMPLY:
			importBoundingBoxes(parsedMesh.boundingBox, parsedMesh.boundingSphere, boundingBoxCollection,
			                    armatureObj, parsedMesh.skeleton, options["rotate90"])
		else:
			importBoundingBox(parsedMesh.boundingBox, f"Mesh Bounding Box", boundingBoxCollection,
			                  rotate90=options["rotate90"])

	# Blender hide bones:
	if options["hideArmature"] and armatureObj != None:
		armatureObj.hide_viewport = True
		armatureObj.hide_render = True

	meshImportEndTime = time.time()
	meshImportTime = meshImportEndTime - meshImportStartTime
	print(f"Mesh imported in {y(formatMs(meshImportTime))} ms.")
	print("\033[92m__________________________________\nRE Mesh import finished.\033[0m")
	return (warningList, errorList)
