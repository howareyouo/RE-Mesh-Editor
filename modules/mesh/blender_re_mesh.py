# Author: NSA Cloud
# TODO
# Add Blendshapes
# Fix exporting SF6 Akuma with LODs, LODs use bones not used by LOD0

# Import
# -Redo vertex color based material importing

# Export
# -Submeshes aren't sorted

import bpy
import bmesh
import os
from math import radians, floor, sqrt
from mathutils import Vector, Matrix
from itertools import chain, repeat, islice
from .file_re_mesh import readREMesh, writeREMesh, ParsedREMeshToREMesh, Sphere, AABB, meshFileVersionToGameNameDict
from .re_mesh_parse import ParsedREMesh, VisconGroup, LODLevel, SubMesh, ParsedBone, Skeleton
from .re_mesh_export_errors import addErrorToDict, printErrorDict, showREMeshErrorWindow
from ..mdf.file_re_mdf import readMDF
from ..mdf.blender_re_mesh_mdf import findMDFPathFromMeshPath, importMDF
from ..mdf.blender_re_mdf import importMDFFile
from ..sfur.blender_re_sfur import importSFurFile, findSFurPathFromMeshPath
from ..gen_functions import splitNativesPath, raiseWarning, y
from ..blender_utils import showErrorMessageBox, showMessageBox
from ..hashing.mmh3.pymmh3 import hashUTF8
import time
import numpy as np
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

timeFormat = "%d"
rotateNeg90Matrix = Matrix.Rotation(radians(-90.0), 4, 'X')
rotate90Matrix = Matrix.Rotation(radians(90.0), 4, 'X')


def triangulateMesh(mesh):
	if all(len(poly.vertices) == 3 for poly in mesh.polygons):
		return
	bm = bmesh.new()
	bm.from_mesh(mesh)
	bmesh.ops.triangulate(bm, faces=bm.faces[:])
	bm.to_mesh(mesh)
	bm.free()


# if custom_normals:
# mesh.normals_split_custom_set_from_vertices(custom_normals)

def pad_infinite(iterable, padding=None):
	return chain(iterable, repeat(padding))


def pad(iterable, size, padding=None):
	return islice(pad_infinite(iterable, padding), size)


def normalize(lst):
	s = sum(lst)
	if s != 0.0:
		return list(map(lambda x: float(x) / s, lst))
	else:
		return lst


def normalizeVec(vec):
	return Vector(vec).normalized()


def dist(a, b) -> float:
	return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


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


def vertexPosToGlobal(local_coords, world_matrix):
	# Reshape coords to Nx3 matrix
	local_coords.shape = (-1, 3)

	# Add an extra 1.0s column (for matrix dot product)
	local_coords = np.c_[local_coords, np.ones(local_coords.shape[0])]

	# Then:
	# Dot product matrix with the coords transpose
	# Keep the first 3 rows (x,y,z)
	# Transpose result to Nx3
	# Flatten
	global_coords = np.dot(world_matrix, local_coords.T)[0:3].T.reshape((-1))
	return np.reshape(global_coords, (-1, 3))


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


def createEmpty(name, propertyList, parent=None, collection=None):
	obj = bpy.data.objects.new(name, None)
	obj.empty_display_size = .10
	obj.empty_display_type = 'PLAIN_AXES'
	obj.parent = parent
	for property in propertyList:
		obj[property[0]] = property[1]
	if collection == None:
		collection = bpy.context.scene.collection

	collection.objects.link(obj)
	return obj


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
			hashedName = False
			boneName = bone.boneName
			if len(boneName) > 63:  
				# Thank DMC5 for abominations like this: 
				# bake12_sim_sm1103_vegetablebox_04__PMesh_sm1103_vegetablebox_sm1103_vegetablebox_s6_polySurface6180__p001
				boneName = f"#HASHED_{str(hashUTF8(boneName))}"
				raiseWarning(
					f"Bone name length exceeds Blender's limit of 63 characters, hashing bone name: {bone.boneName}")
				hashedName = True
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
		if len(name) > 63:
			boneNameMap[idx] = f"#HASHED_{str(hashUTF8(name))}"
		else:
			boneNameMap[idx] = name
		
		# Secondary (shapekey) bone name
		secName = "SHAPEKEY_" + name
		if len(secName) > 63:
			secBoneNameMap[idx] = f"#HASHED_{str(hashUTF8(secName))}"
		else:
			secBoneNameMap[idx] = secName
	
	return boneNameMap, secBoneNameMap


def importMesh(meshName="newMesh", vertexList=[], faceList=[], vertexNormalList=[], vertexColor0List=[],
               vertexColor1List=[], UV0List=[], UV1List=[], UV2List=[], boneNameList=[],
               vertexGroupWeightList=[], vertexGroupBoneIndicesList=[], extraVertexGroupWeightList=[],
               extraVertexGroupBoneIndicesList=[], vertexGroupWeightListSecondary=[],
               vertexGroupBoneIndicesListSecondary=[], boneNameRemapList=[], material="Material",
               armature=None, collection=None, rotate90=True, blendShapeList=[]):
	meshData = bpy.data.meshes.new(meshName)
	# Import vertices and faces
	if len(vertexList) == 0:
		raise Exception("Invalid mesh, submesh has no vertices")
	if len(faceList) == 0:
		raise Exception("Invalid mesh, submesh has no faces")
	meshData.from_pydata(vertexList, [], faceList)
	# Import vertex normals
	if len(vertexNormalList) > 0:
		meshData.update(calc_edges=True)
		meshData.polygons.foreach_set("use_smooth", [True] * len(meshData.polygons))
		meshData.validate()  # Must call validate before setting custom normals or it can cause rare crashes when importing
		# Vectorized normal normalization - much faster than Python loop
		normals = np.asarray(vertexNormalList, dtype=np.float32)
		norms = np.linalg.norm(normals, axis=1, keepdims=True)
		norms[norms == 0] = 1.0  # Avoid division by zero
		normalized_normals = (normals / norms).tolist()
		meshData.normals_split_custom_set_from_vertices(normalized_normals)

		# print(f"DEBUG:\tSet custom normals")
		if bpy.app.version < (4, 0, 0):
			meshData.use_auto_smooth = True
			meshData.calc_normals_split()
		# print(f"DEBUG:\t Loaded vertex normals")
		"""
		meshData.use_auto_smooth = True
		meshData.polygons.foreach_set("use_smooth", [True] * len(meshData.polygons))
		meshData.normals_split_custom_set_from_vertices(vertexNormalList)
		"""
	# Import UV Layers and Vertex Colors - precompute loop indices once
	loop_vertex_indices = np.zeros(len(meshData.loops), dtype=np.int32)
	meshData.loops.foreach_get("vertex_index", loop_vertex_indices)

	UVLayerList = (UV0List, UV1List, UV2List)
	for layerIndex, layer in enumerate(UVLayerList):
		if len(layer) > 0:
			newUVLayer = meshData.uv_layers.new(name="UVMap" + str(layerIndex))
			uv_data = np.asarray(layer, dtype=np.float32)[loop_vertex_indices]
			newUVLayer.data.foreach_set("uv", uv_data.ravel())
	# print(f"DEBUG:\t Loaded UV {layerIndex}")

	# Import vertex color layer 0
	if len(vertexColor0List) > 0:
		vcol_layer = meshData.vertex_colors.new()
		color_data = np.asarray(vertexColor0List, dtype=np.float32)[loop_vertex_indices]
		vcol_layer.data.foreach_set("color", color_data.ravel())
	# print(f"DEBUG:\t Loaded Vertex Color")

	meshObj = bpy.data.objects.new(meshName, meshData)

	# Import Weights
	if len(vertexGroupWeightList) > 0 and len(boneNameList) > 0:
		# Precompute bone name mapping to avoid repeated hashing (single pass for both primary + secondary)
		boneNameMap, secBoneNameMap = buildBoneNameMaps(boneNameList)
		# Only create vertex groups for bones that get used
		if len(boneNameList) > 1:
			# print(boneNameList)
			# Vectorized: use np.unique + concatenate instead of nested set comprehension
			if len(extraVertexGroupBoneIndicesList) > 0 and len(extraVertexGroupBoneIndicesList[0]) > 0:
				all_bone_indices = np.unique(np.concatenate([
					np.asarray(vertexGroupBoneIndicesList).ravel(),
					np.asarray(extraVertexGroupBoneIndicesList).ravel()
				]))
			else:
				all_bone_indices = np.unique(np.asarray(vertexGroupBoneIndicesList).ravel())
			usedBoneIndices = sorted(all_bone_indices.tolist())
			# print(usedBoneIndices)
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
		# print("Importing secondary weights")
		# Only create vertex groups for bones that get used
		# Vectorized: np.unique instead of nested set comprehension
		usedBoneIndices = sorted(np.unique(np.asarray(vertexGroupBoneIndicesListSecondary).ravel()).tolist())
		# print(boneNameList)
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

	return meshObj


def importLODGroup(parsedMesh, meshType, meshCollection, materialDict, armatureObj, hiddenCollectionSet,
                   meshOffsetDict, importAllLODs=False, createCollections=True, importShadowMeshes=False,
                   rotate90=True, mergeGroups=False, importBoundingBoxes=False):
	if meshType == "Main Mesh":
		shortName = "Main"
		targetLODList = parsedMesh.mainMeshLODList
	elif meshType == "Shadow Mesh":
		shortName = "Shadow"
		targetLODList = parsedMesh.shadowMeshLODList
	elif meshType == "Occlusion Mesh":
		shortName = "Occlusion"
	firstLOD = True

	if parsedMesh.skeleton != None:
		if parsedMesh.skeleton.weightedBones != []:
			# print(parsedMesh.skeleton.weightedBones)
			boneNameList = parsedMesh.skeleton.weightedBones
		elif len(parsedMesh.skeleton.boneList) != 0:  # No bone remap table
			boneNameList = [parsedMesh.skeleton.boneList[0].boneName]
	else:
		boneNameList = []

	if not importAllLODs and targetLODList != []:
		targetLODList = [targetLODList[0]]

	if parsedMesh.isMPLY:
		MPLYRoot = createEmpty(
			f"Meshlet Root" + f" - {meshCollection.name}" if meshCollection != None else "",
			[("~TYPE", "RE_MESH_MPLY_ROOT")], collection=meshCollection)
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
			# print(f"DEBUG: Group {visconGroup.visconGroupNum}")
			objMergeList = []
			for subMesh in visconGroup.subMeshList:
				if subMesh.isReusedMesh:
					lodCollection.objects.link(meshOffsetDict[subMesh.meshVertexOffset])
				else:
					materialName = parsedMesh.materialNameList[subMesh.materialIndex]
					# print(subMesh.vertexPosList)
					# print(f"DEBUG:\t Sub {subMesh.subMeshIndex}")
					if importAllLODs:
						LODNum = f"LOD_{str(lodIndex)}_"
					else:
						LODNum = ""
					meshObj = importMesh(
						# meshName=f"LOD_{str(lodIndex)}_{shortName}_Group_{str(visconGroup.visconGroupNum)}_Sub_{str(subMesh.subMeshIndex)}__{materialName}",
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
						# MH Wilds extra weights
						extraVertexGroupWeightList=subMesh.extraWeightList,
						extraVertexGroupBoneIndicesList=subMesh.extraWeightIndicesList,
						# DD2 shape key weights
						vertexGroupWeightListSecondary=subMesh.secondaryWeightList,
						vertexGroupBoneIndicesListSecondary=subMesh.secondaryWeightIndicesList,
						material=materialDict[materialName],
						armature=armatureObj,
						collection=lodCollection,
						rotate90=rotate90,
						blendShapeList=subMesh.blendShapeList
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
					# print(f"DEBUG:\t Finished Importing Sub {subMesh.subMeshIndex}")
					if mergeGroups:
						objMergeList.append(meshObj)
					meshOffsetDict[subMesh.meshVertexOffset] = meshObj

			if mergeGroups and len(objMergeList) > 1:
				joinObjects(objMergeList)
		# print(f"DEBUG: End Group {visconGroup.visconGroupNum}")
		firstLOD = False


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
		if len(boneParent) > 63:
			boneName = f"#HASHED_{str(hashUTF8(boneParent))}"
		else:
			boneName = boneParent
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
	return gameName


# ---RE MESH IO FUNCTIONS---#

def importREMeshFile(filePath, options):
	meshImportStartTime = time.time()
	fileName = os.path.split(filePath)[1].split(".mesh")[0]
	try:
		meshVersion = int(os.path.splitext(filePath)[1].replace(".", ""))
	except:
		print("Unable to parse mesh version number in file path.")
		meshVersion = None
	if meshVersion in meshFileVersionToGameNameDict:
		gameName = meshFileVersionToGameNameDict[meshVersion]
		if gameName in meshGameNameConflictDict:
			gameName = resolveMeshGameNameConflict(gameName, filePath)
	else:
		gameName = None
	# print(f"Game Name:{gameName}")
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
	meshParseEndTime = time.time()
	meshParseTime = meshParseEndTime - meshParseStartTime
	print(f"Mesh parsing took {y(timeFormat % (meshParseTime * 1000))} ms.")
	armatureObj = None
	parentCollection = None  # Collection for grouping mesh and mdf
	if options["createCollections"]:
		# print("DEBUG: Making collections")
		if options["loadMDFData"]:
			parentCollection = getCollection(meshFileName.split(".mesh")[0], makeNew=True)
		meshCollection = getCollection(meshFileName, parentCollection, makeNew=True)
		meshCollection.color_tag = "COLOR_01"
		meshCollection["~TYPE"] = "RE_MESH_COLLECTION"
		meshCollection["LODGroupNameHash"] = str(reMesh.fileHeader.lodGroupNameHash)
		try:
			split = splitNativesPath(filePath)
			if split != None:
				assetPath = os.path.splitext(split[1])[0].replace(os.sep, "/")
				meshCollection["~ASSETPATH"] = assetPath  # Used to determine where to export automatically
		except:
			print("Failed to set asset path from file path, file is likely not in a natives folder.")
		bpy.context.scene.re_mdf_toolpanel.meshCollection = meshCollection
	else:
		meshCollection = bpy.context.scene.collection
	hiddenCollectionSet = set()
	# print("DEBUG: Finished colllections")
	if parsedMesh.skeleton != None:
		armatureObj = importSkeleton(parsedMesh.skeleton, meshFileName.split(".mesh")[0] + " Armature",
		                             meshCollection, options["rotate90"], options["mergeArmature"])
	# Create dictionary of material names mapping to material data to avoid assigning the wrong material in case of name duplication
	materialDict = createMaterialDict(parsedMesh.materialNameList)
	meshOffsetDict = dict()

	if not options["importArmatureOnly"]:
		# print("DEBUG: Importing main mesh")
		importLODGroup(parsedMesh, "Main Mesh", meshCollection, materialDict, armatureObj,
		               hiddenCollectionSet, meshOffsetDict, options["importAllLODs"],
		               options["createCollections"], options["importShadowMeshes"], options["rotate90"],
		               options["mergeGroups"], options["importBoundingBoxes"])
	# print("DEBUG: Finished importing main mesh")
	"""
	if options["importShadowMeshes"] and parsedMesh.shadowMeshLODList != []:
		importLODGroup(parsedMesh,"Shadow Mesh",meshCollection,materialDict,armatureObj,hiddenCollectionSet,meshOffsetDict)
	"""
	# Hide other lods in viewport
	# print(hiddenCollectionSet)

	collections = bpy.context.view_layer.layer_collection.children
	for collection in collections:
		if collection.name == meshCollection.name:
			for childCollection in collection.children:
				if childCollection.name in hiddenCollectionSet:
					childCollection.hide_viewport = True
			break

	meshOffsetDict.clear()
	if options["loadMaterials"] or options["loadMDFData"]:
		# print(filePath.split(".mesh")[1])
		if options["mdfPath"] != "":
			mdfPath = options["mdfPath"]
		else:
			mdfPath = findMDFPathFromMeshPath(filePath, gameName)
		# print(mdfPath)
		try:
			if mdfPath != None:
				split = splitNativesPath(mdfPath)
				if split != None:
					chunkPath = split[0]
				else:
					chunkPath = ""
				mdfImportStartTime = time.time()
				if options[
					"loadMDFData"]:  # MDF gets read twice when importing mdf data, could fix it but reading is fast enough that it's not really noticable.
					print("Loading MDF Data...")
					try:
						importMDFFile(mdfPath, parentCollection=parentCollection)
					except Exception as err:
						raiseWarning("Could not import MDF data from " + mdfPath + ":" + str(err))
						warningList.append("Could not import MDF data from " + mdfPath + ":" + str(err))
				if options["loadMaterials"] and not options["importArmatureOnly"]:
					if options["loadMDFData"]:
						print("Loading Mesh Materials From MDF...")
					mdfFile = readMDF(mdfPath)
					importMDF(mdfFile, materialDict, options["loadUnusedTextures"],
					          options["loadUnusedProps"], options["useBackfaceCulling"],
					          options["reloadCachedTextures"], chunkPath=chunkPath, gameName=gameName,
					          arrangeNodes=True)

					mdfImportEndTime = time.time()
					mdfImportTime = mdfImportEndTime - mdfImportStartTime
					print(f"Material importing took {timeFormat % (mdfImportTime * 1000)} ms.")
			else:
				warningList.append("MDF file not found.")
		except Exception as err:
			# print(str(err))
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
	armatureObj.hide_viewport = True
	armatureObj.hide_render = True

	meshImportEndTime = time.time()
	meshImportTime = meshImportEndTime - meshImportStartTime
	print(f"Mesh imported in {y(timeFormat % (meshImportTime * 1000))} ms.")
	print("\033[92m__________________________________\nRE Mesh import finished.\033[0m")
	return (warningList, errorList)


def checkObjForUVDoubling(obj):
	if len(obj.data.uv_layers) == 0 or len(obj.data.uv_layers[0].data) == 0:
		return False
	loop_vert_idx = np.zeros(len(obj.data.loops), dtype=np.int32)
	obj.data.loops.foreach_get("vertex_index", loop_vert_idx)
	uv_data = np.zeros((len(obj.data.uv_layers[0].data), 2), dtype=np.float32)
	obj.data.uv_layers[0].data.foreach_get("uv", uv_data.ravel())
	vert_min_uv = np.full((len(obj.data.vertices), 2), np.inf, dtype=np.float32)
	vert_max_uv = np.full((len(obj.data.vertices), 2), -np.inf, dtype=np.float32)
	np.minimum.at(vert_min_uv, loop_vert_idx, uv_data)
	np.maximum.at(vert_max_uv, loop_vert_idx, uv_data)
	return bool(np.any(np.any(vert_min_uv != vert_max_uv, axis=1)))


# RE Toolbox Solve Repeated UVs

def selectRepeated(bm):
	bm.verts.index_update()
	bm.verts.ensure_lookup_table()
	targetVert = set()
	for uv_layer in bm.loops.layers.uv.values():
		uvMap = {}
		for face in bm.faces:
			for loop in face.loops:
				uvPoint = tuple(loop[uv_layer].uv)
				if loop.vert.index in uvMap and uvMap[loop.vert.index] != uvPoint:
					targetVert.add(bm.verts[loop.vert.index])
				else:
					uvMap[loop.vert.index] = uvPoint
	return targetVert


def solveRepeatedVertex(op, mesh):
	bpy.ops.mesh.select_all(action='DESELECT')
	bm = bmesh.from_edit_mesh(mesh.data)
	oldmode = bm.select_mode
	bm.select_mode = {'VERT'}
	targets = selectRepeated(bm)
	for target in targets:
		bmesh.utils.vert_separate(target, target.link_edges)
		bm.verts.ensure_lookup_table()
	bpy.ops.mesh.select_all(action='DESELECT')
	bm.select_mode = oldmode
	bm.verts.ensure_lookup_table()
	bm.verts.index_update()
	bmesh.update_edit_mesh(mesh.data)
	mesh.data.update()
	return


def solveRepeatedUVs(selection):
	context = bpy.context
	for selectedObj in selection:
		if selectedObj.type == "MESH":
			context.view_layer.objects.active = selectedObj
			if bpy.app.version < (4, 0, 0):
				if selectedObj.data.use_auto_smooth == False:
					selectedObj.data.use_auto_smooth = True
					selectedObj.data.auto_smooth_angle = .785
			selectedObj.data.polygons.foreach_set("use_smooth", [True] * len(selectedObj.data.polygons))
			# Save loop normals before modification
			n_loops = len(selectedObj.data.loops)
			saved_normals = np.zeros((n_loops, 3), dtype=np.float32)
			selectedObj.data.loops.foreach_get("normal", saved_normals.ravel())
			bpy.ops.object.mode_set(mode='EDIT')
			obj = context.edit_object
			me = obj.data
			bm = bmesh.from_edit_mesh(me)
			old_seams = [e for e in bm.edges if e.seam]
			for e in old_seams:
				e.seam = False
			bpy.ops.mesh.select_all(action='SELECT')
			bpy.ops.uv.select_all(action='SELECT')
			bpy.ops.uv.seams_from_islands()
			seams = [e for e in bm.edges if e.seam]
			bmesh.ops.split_edges(bm, edges=seams)
			for e in old_seams:
				e.seam = True
			bmesh.update_edit_mesh(me)
			solveRepeatedVertex(None, obj)
			bpy.ops.object.mode_set(mode='OBJECT')
			selectedObj.data.normals_split_custom_set(saved_normals.tolist())
			if bpy.app.version < (4, 0, 0):
				selectedObj.data.calc_normals_split()
			print(f"Solved Repeated UVs on {selectedObj.name}")
# End solve repeated UVs


# RE Toolbox Split Sharp Edges
def splitSharpEdges():
	context = bpy.context
	if context.selected_objects != []:
		selection = context.selected_objects
	else:
		selection = bpy.context.scene.objects
	for selectedObj in selection:
		if selectedObj.type == "MESH":
			isHidden = selectedObj.hide_viewport
			if isHidden:
				selectedObj.hide_viewport = False
			context.view_layer.objects.active = selectedObj

			bpy.ops.object.mode_set(mode='EDIT')
			obj = bpy.context.edit_object
			me = obj.data
			bm = bmesh.from_edit_mesh(me)
			# old seams
			sharp = [e for e in bm.edges if not e.smooth]
			if sharp != []:
				print(f"Split Sharp Edges on {selectedObj.name}")
			bmesh.ops.split_edges(bm, edges=sharp)
			bmesh.update_edit_mesh(me)
			bpy.ops.object.mode_set(mode='OBJECT')
			selectedObj.hide_viewport = isHidden


# End split sharp edges


def exportREMeshFile(filePath, options):
	# TODO Warning Conditions
	# Invalid mesh naming scheme - notify when using blender material name and setting viscon id to 0
	# Vertex groups weighted to bones that aren't on the armature
	# If an mdf for the mesh imported, check if the mesh materials are mismatched with mdf

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
	# TODO Fix having all bones as weighted bones breaks export
	meshExportStartTime = time.time()
	vertexCount = 0
	faceCount = 0
	fileName = os.path.split(filePath)[1].split(".mesh")[0]
	try:
		meshVersion = int(os.path.splitext(filePath)[1].replace(".", ""))
	except:
		print("Unable to parse mesh version number in file path.")
		meshVersion = 0
	if meshVersion in meshFileVersionToGameNameDict:
		gameName = meshFileVersionToGameNameDict[meshVersion]
	else:
		gameName = None

	print("\033[96m__________________________________\nRE Mesh export started.\033[0m")

	if bpy.context and bpy.context.active_object != None:
		bpy.ops.object.mode_set(mode='OBJECT')

	maxWeightsPerVertex = 8
	maxWeightsPerVertexExtended = 16
	maxWeightedBones = 256
	SIX_WEIGHT_GAMES = set(["SF6", "MHWILDS", "PRAG"])
	EXTENDED_WEIGHT_GAMES = set(
		["MHWILDS", "PRAG", "MHS3", ])  # Games with support for extended weight buffers
	if gameName in SIX_WEIGHT_GAMES:
		maxWeightsPerVertex = 6
		maxWeightsPerVertexExtended = 12
		maxWeightedBones = 1024
	padWithLastWeightIndex = True if gameName == "PRAG" or gameName == "MHS3" or gameName == "RE9" else False
	MAX_VERTICES = 65536
	MAX_VERTICES_EXTENDED = 4294967295
	MAX_FACES = 4294967295

	showWarningMessage = False

	subMeshCount = 0

	targetCollection = bpy.data.collections.get(options["targetCollection"])
	bpy.context.scene["REMeshLastExportedMeshVersion"] = meshVersion
	if targetCollection == None:
		print("No target collection set. Using scene collection.")
		targetCollection = bpy.context.scene.collection
	else:
		print(f"Target collection: {targetCollection.name}")
		bpy.context.scene["REMeshLastExportedCollection"] = targetCollection.name

	meshLODCollectionList = []
	addedMaterialsSet = set()
	materialIndexDict = {}  # 材质名称到索引的映射，用于O(1)查找
	dg = bpy.context.evaluated_depsgraph_get()
	parsedMesh = ParsedREMesh()
	parsedMesh.boundingBox = None
	parsedMesh.boundingSphere = None
	vertexGroupsSet = set()
	weightedBonesSet = set()
	cloneMeshNameDict = {}
	deleteCopiedMeshList = []
	boundingBoxCollection = None
	importedBoneBoundingBoxes = {}
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

	hashedBoneNameDict = dict()
	if armatureObj:
		print(f"Armature: {armatureObj.name}")
		parsedMesh.skeleton = Skeleton()
		if options["rotate90"]:
			transform = rotateNeg90Matrix @ armatureObj.matrix_world
		else:
			transform = armatureObj.matrix_world
		boneIndexDict = {bone.name: index for index, bone in enumerate(armatureObj.data.bones)}
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
			parsedBone.symmetryBoneIndex = boneIndexDict[bone.name]

			# symmetryIndex is -1 if bone is symmetry bone, but missing it's symmetric bone

			if bone.name.startswith("L_"):
				if "R" + bone.name[1::] in armatureObj.data.bones:
					parsedBone.symmetryBoneIndex = boneIndexDict["R" + bone.name[1::]]
				else:
					parsedBone.symmetryBoneIndex = -1
			elif bone.name.startswith("R_"):
				if "L" + bone.name[1::] in armatureObj.data.bones:
					parsedBone.symmetryBoneIndex = boneIndexDict["L" + bone.name[1::]]
				else:
					parsedBone.symmetryBoneIndex = -1

			elif bone.name.endswith("_L"):
				if bone.name[:-1] + "R" in armatureObj.data.bones:
					parsedBone.symmetryBoneIndex = boneIndexDict[bone.name[:-1] + "R"]
				else:
					parsedBone.symmetryBoneIndex = -1
			elif bone.name.endswith("_R"):
				if bone.name[:-1] + "L" in armatureObj.data.bones:
					parsedBone.symmetryBoneIndex = boneIndexDict[bone.name[:-1] + "L"]
				else:
					parsedBone.symmetryBoneIndex = -1

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
			if options["preserveBoneMatrices"] and bone.get("reMeshWorldMatrix"):
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

				"""
				#Get world matrix
				if bone.parent != None:
					if rotate90:
						parsedBone.worldMatrix.matrix = [list(row) for row in (rotate90Matrix @ (bone.parent.matrix_local.inverted() @ (bone.matrix_local)))]
					else:
						parsedBone.worldMatrix.matrix = [list(row) for row in (bone.parent.matrix_local.inverted() @ (bone.matrix_local))]
				else:
					
				#Get local matrix
				if bone.parent != None:
					parsedBone.localMatrix.matrix = [list(row) for row in armatureScaleMatrix @ (bone.parent.matrix_local.inverted() @ bone.matrix_local)]
				else:
					if rotate90:
						parsedBone.localMatrix.matrix = [list(row) for row in rotate90Matrix @ (armatureWorldMatrix @ bone.matrix_local)]
					else:
						parsedBone.localMatrix.matrix = [list(row) for row in (armatureWorldMatrix @ bone.matrix_local)]
				
				#Get inverse matrix
				if rotate90:
					parsedBone.inverseMatrix.matrix = [list(row) for row in (rotate90Matrix @ (armatureWorldMatrix @ (bone.matrix_local)))]
				else:
					parsedBone.inverseMatrix.matrix = [list(row) for row in (armatureWorldMatrix @ (bone.matrix_local))]
				"""
			parsedMesh.skeleton.boneList.append(parsedBone)
			if len(armatureObj.data.bones) == 0:
				raiseWarning("Armature contains no bones, skipping armature.")
	else:
		print(f"Armature: None")
		armatureObj = None

	# Get previously imported bounding boxes if option enabled
	if boundingBoxCollection and options["exportBoundingBoxes"]:
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
				importedMeshBoundingBox = AABB()
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
				importedMeshBoundingSphere = Sphere()

				parsedMesh.boundingSphere.x = obj.location[0]
				parsedMesh.boundingSphere.y = obj.location[1]
				parsedMesh.boundingSphere.z = obj.location[2]
				parsedMesh.boundingSphere.r = obj.dimensions.x / 2
	if meshLODCollectionList == []:
		meshLODCollectionList = [targetCollection]
	meshLODCollectionList.sort(key=lambda col: col.name)
	if not options["exportAllLODs"]:
		meshLODCollectionList = [meshLODCollectionList[0]]
	# --- Parallel submesh extraction worker ---
	# Runs the heavy, READ-ONLY portion of extracting a single submesh (foreach_get
	# bulk reads + numpy vectorization + per-vertex weight loop) on a worker thread.
	# It must NOT mutate any shared Blender/Mesh state - everything it produces is
	# returned and merged back on the main thread. This is safe to parallelize because
	# all mesh-mutating prep (triangulate / transform / calc_tangents) for a viscon is
	# completed serially BEFORE any worker starts, and foreach_get / numpy / bytes IO
	# release the GIL, so worker threads overlap in real time across meshes.
	MIN_WEIGHT = 0.002

	def _gatherSubMesh(task):
		(parsedSubMesh, evaluatedSubMeshData, rawName, meshHasUV, meshHasUV2, meshHasColor,
		 vertexGroupCount, vertexGroupIndexToRemapDict, shapeKeyGroupIndices,
		 hasWeight, hasSecondaryWeight) = task
		errs = []  # (code, name) collected locally, merged on main thread
		bbox = []  # (bone idx, x, y, z) bounding box contributions
		hasExtraWeightOverflow = False

		loop_vert_idx = np.zeros(len(evaluatedSubMeshData.loops), dtype=np.int32)
		evaluatedSubMeshData.loops.foreach_get("vertex_index", loop_vert_idx)
		n_polys = len(evaluatedSubMeshData.polygons)
		if n_polys > 0:
			loop_total = np.zeros(n_polys, dtype=np.int32)
			evaluatedSubMeshData.polygons.foreach_get("loop_total", loop_total)
			if np.any(loop_total != 3):
				errs.append(("NonTriangulatedFace", rawName))
			parsedSubMesh.faceList = loop_vert_idx.reshape(-1, 3).astype(np.uint32)
			if n_polys > MAX_FACES:
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
			color_array = np.zeros((len(evaluatedSubMeshData.loops), 4), dtype=np.float32)
			evaluatedSubMeshData.vertex_colors[0].data.foreach_get("color", color_array.ravel())

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
			vg_remap = vertexGroupIndexToRemapDict
			shapekey_set = shapeKeyGroupIndices
			min_w = MIN_WEIGHT
			secondary = hasSecondaryWeight
			# Only the unique verts are actually processed, so materialize their positions
			# (instead of converting the whole positions array to a list of python floats).
			pos_unique = vert_positions[unique_verts].tolist()
			for pos_idx, vi in enumerate(unique_verts.tolist()):
				vert = evaluatedSubMeshData.vertices[vi]
				prim = []  # (remapped bone idx, weight)
				sec = []   # (remapped bone idx, weight) for shapekeys
				for g in vert.groups:
					gidx = g.group
					if gidx >= vertexGroupCount:
						continue
					ridx = vg_remap.get(gidx, 0)
					if secondary and gidx in shapekey_set:
						sec.append((ridx, g.weight))
					elif g.weight >= min_w:
						prim.append((ridx, g.weight))
				if prim:
					prim.sort(key=lambda t: t[1], reverse=True)
					prim_w = [w for _, w in prim]
					prim_idx = [i for i, _ in prim]
					n_prim = len(prim)
					padding_idx = prim_idx[-1] if padWithLastWeightIndex else 0
					if n_prim > maxWeightsPerVertex:
						hasExtraWeightOverflow = True
						if gameName not in EXTENDED_WEIGHT_GAMES:
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
					for bi in prim_idx[:main_n]:
						bbox.append((bi, px, py, pz))
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
					for si in sec_idx[:sec_n]:
						bbox.append((si, px, py, pz))
		if len(unique_verts) < len(evaluatedSubMeshData.vertices):
			errs.append(("LooseVerticesOnSubMesh", rawName))
		return parsedSubMesh, errs, bbox, hasExtraWeightOverflow

	# Loop through all lod collections, or the scene collection if there is no collections
	meshDataStartTime = time.time()
	isFirstLOD = True
	remapDict = dict()
	shapeKeyBoneSet = set()  # DD2 secondary weights
	for lodIndex, lod in enumerate(meshLODCollectionList):
		print(f"LOD {lodIndex} collection:{lod.name}")
		parsedLODLevel = LODLevel()
		if lod.get("LOD Distance") == None:
			lod["LOD Distance"] = 0.167932 * (
						lodIndex + 1)  # Player model LOD distance, maybe calculate from a bounding box instead
		parsedLODLevel.lodDistance = lod["LOD Distance"]

		# Store all groups as a key in dictionary with submesh list as value
		visconDict = dict()
		boneRemapStartTime = time.time()
		# Get all meshes inside the collection
		doubledUVList = []
		sharpEdgeSplitList = []
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
						# print(f"Found doubled uvs on {obj.name}")
						doubledUVList.append(cloneObj)

				if options["preserveSharpEdges"]:
					use_smooth = np.zeros(len(cloneObj.data.polygons), dtype=bool)
					cloneObj.data.polygons.foreach_get("use_smooth", use_smooth)
					if not np.all(use_smooth):
						sharpEdgeSplitList.append(cloneObj)

				if "Group_" in obj.name:
					try:
						groupID = int(obj.name.split("Group_")[1].split("_")[0])
					except:
						pass
				else:
					print(f"Could not parse group ID in {obj.name}, setting to 0")
					groupID = 0

				# Build bone remap table from first LOD by first finding all bones that have vertex groups weighted to them

				if armatureObj != None:
					armatureBoneDict = armatureObj.data.bones
				else:
					armatureBoneDict = dict()

				if isFirstLOD:
					hasWeights = False
					# Collect which vertex-group INDICES are actually painted on the clone mesh,
					# then resolve them to bone names via the CLONE's vertex groups. Group indices
					# read from clone vertices are in the clone's own index space, which can differ
					# from the original object's after new_from_object() -- so remap by NAME here.
					mesh_data = cloneObj.data
					# Vertex-group definitions live on the OBJECT, not on the Mesh (Mesh has no vertex_groups).
					vgNameByIndex = [vg.name for vg in cloneObj.vertex_groups]
					used = set()
					for v in mesh_data.vertices:
						for g in v.groups:
							used.add(g.group)
					for gidx in used:
						if gidx >= len(vgNameByIndex):
							continue
						rawN = vgNameByIndex[gidx]
						if rawN.startswith("SHAPEKEY_"):
							vgName = rawN[9:]
							shapeKeyBoneSet.add(vgName)
						else:
							vgName = rawN
						if vgName in armatureBoneDict:
							weightedBonesSet.add(vgName)
							hasWeights = True
						else:
							remapDict[vgName] = 0
					if armatureObj != None and not hasWeights:
						raiseWarning(f"No valid vertex weights found on {obj.name}!")
						showWarningMessage = True
					# addErrorToDict(errorDict, "NoWeightsOnMesh", obj.name)
					if armatureObj == None and len(remapDict) != 0:
						addErrorToDict(errorDict, "NoArmatureInCollection", obj.name)
				if not visconDict.get(groupID):
					visconDict[groupID] = [obj]
				else:
					visconDict[groupID].append(obj)

		if doubledUVList != []:
			previousSelection = bpy.context.selected_objects
			bpy.ops.object.select_all(action='DESELECT')
			for obj in doubledUVList:
				obj.select_set(True)

			try:
				solveRepeatedUVs(selection=bpy.context.selected_objects)
			except Exception as err:
				raiseWarning(f"Failed to solve repeated UVs. {str(err)}")

			"""
			if hasattr(bpy.types, "OBJECT_PT_re_tools_quick_export_panel"):#RE Toolbox installed
				bpy.ops.re_toolbox.solve_repeated_uvs()
			else:
				raiseWarning("RE Toolbox is not installed. Cannot solve repeated UVs automatically.")
			bpy.ops.object.select_all(action='DESELECT')
			"""
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

			"""
			if hasattr(bpy.types, "OBJECT_PT_re_tools_quick_export_panel"):#RE Toolbox installed
				try:	
					bpy.ops.re_toolbox.split_sharp_edges()
				except Exception as err:
					raiseWarning(f"Failed to split sharp edges. RE Toolbox may be outdated. Update to the latest version in Edit > Preferences > Addons > RE Toolbox\n{str(err)}")
			else:
				raiseWarning("RE Toolbox is not installed. Cannot split sharp edges.")
			"""
			bpy.ops.object.select_all(action='DESELECT')
			for obj in previousSelection:
				obj.select_set(True)

		# Build remap dict once all objects of the first lod are looped through

		if isFirstLOD and armatureObj != None:
			remapIndex = 0
			for bone in armatureObj.data.bones:
				if bone.name in weightedBonesSet:
					parsedMesh.skeleton.weightedBones.append(bone.name)
					remapDict[bone.name] = remapIndex
					remapIndex += 1
			if len(parsedMesh.skeleton.weightedBones) == 0:
				raiseWarning(
					f"No bones have any weights assigned to them. Defaulting all weights to {armatureObj.data.bones[0].name}")
				parsedMesh.skeleton.weightedBones = [armatureObj.data.bones[0].name]
			boneRemapEndTime = time.time()
			boneRemapTime = boneRemapEndTime - boneRemapStartTime
			# Track per-bone min/max inline instead of collecting all positions
			boneMin = {}
			boneMax = {}
			for name in parsedMesh.skeleton.weightedBones:
				boneMin[name] = [math.inf, math.inf, math.inf]
				boneMax[name] = [-math.inf, -math.inf, -math.inf]

		# Once all viscons have been added, sort them, then parse the submeshes
		for visconGroupID in sorted(visconDict.keys()):
			print(f"  Group:{visconGroupID}")
			visconGroup = VisconGroup()
			visconGroup.visconGroupNum = visconGroupID
			subMeshTasks = []  # heavy (read-only) extraction tasks, run in a thread pool after prep
			# Sort by submesh number
			for submeshIndex, rawsubmesh in enumerate(
					sorted(visconDict[visconGroupID], key=lambda obj: obj.name)):
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
				# index space (where the weight group indices are actually read from), linking
				# to the original via vertex-group NAME instead of index.
				vgNameList = [vg.name for vg in cloneObj.vertex_groups]
				vertexGroupIndexToRemapDict = {
					idx: remapDict.get((name[9:] if name.startswith("SHAPEKEY_") else name), 0)
					for idx, name in enumerate(vgNameList)
				}

				# DD2 shape key vertex group indices
				shapeKeyGroupIndices = set(
					idx for idx, name in enumerate(vgNameList) if name.startswith("SHAPEKEY_"))
				if len(shapeKeyGroupIndices) != 0:
					parsedMesh.bufferHasSecondaryWeight = True

				# print(vertexGroupIndexToRemapDict)
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
				# Get loop vertex indices - extracted once, reused for faces and unique vertex mapping
				loop_vert_idx = np.zeros(len(evaluatedSubMeshData.loops), dtype=np.int32)
				evaluatedSubMeshData.loops.foreach_get("vertex_index", loop_vert_idx)
				n_polys = len(evaluatedSubMeshData.polygons)
				if n_polys > 0:
					loop_total = np.zeros(n_polys, dtype=np.int32)
					evaluatedSubMeshData.polygons.foreach_get("loop_total", loop_total)
					if np.any(loop_total != 3):
						addErrorToDict(errorDict, "NonTriangulatedFace", rawsubmesh.name)
					parsedSubMesh.faceList = loop_vert_idx.reshape(-1, 3).astype(np.uint32)
					if n_polys > MAX_FACES:
						addErrorToDict(errorDict, "MaxFacesExceeded", rawsubmesh.name)
				else:
					parsedSubMesh.faceList = []
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
				if len(evaluatedSubMeshData.vertex_colors) > 0:
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
					vertexGroupCount, vertexGroupIndexToRemapDict, shapeKeyGroupIndices,
					armatureObj != None, parsedMesh.bufferHasSecondaryWeight,
				))

			# Run the submesh extraction in parallel, then merge results on the main thread.
			# Mesh-mutating prep is already done, and foreach_get / numpy / bytes IO release
			# the GIL, so worker threads overlap in real time (near-linear on multicore).

			def _mergeSubMeshResult(res):
				# Serial merge: bbox update must not run concurrently.
				ps, errs, bb, fl = res
				if fl:
					parsedMesh.bufferHasExtraWeight = True
				for code, name in errs:
					addErrorToDict(errorDict, code, name)
				for bi, cx, cy, cz in bb:
					_name = parsedMesh.skeleton.weightedBones[bi]
					_bmin = boneMin[_name]
					_bmax = boneMax[_name]
					if cx < _bmin[0]: _bmin[0] = cx
					if cy < _bmin[1]: _bmin[1] = cy
					if cz < _bmin[2]: _bmin[2] = cz
					if cx > _bmax[0]: _bmax[0] = cx
					if cy > _bmax[1]: _bmax[1] = cy
					if cz > _bmax[2]: _bmax[2] = cz
				visconGroup.subMeshList.append(ps)

			workers = min(len(subMeshTasks), os.cpu_count() if os.cpu_count() else 4)
			if len(subMeshTasks) == 0:
				pass
			elif workers <= 1:
				for task in subMeshTasks:
					_mergeSubMeshResult(_gatherSubMesh(task))
			else:
				results = [None] * len(subMeshTasks)
				with ThreadPoolExecutor(max_workers=workers) as ex:
					futures = {ex.submit(_gatherSubMesh, t): i for i, t in enumerate(subMeshTasks)}
					for fut in as_completed(futures):
						results[futures[fut]] = fut.result()
				# Serial merge in original submesh order
				for res in results:
					_mergeSubMeshResult(res)

			# End submesh
			parsedLODLevel.visconGroupList.append(visconGroup)
		# End viscon
		if "+ Shadow LOD" in lod.name:
			parsedMesh.shadowMeshLinkedLODList.append(parsedLODLevel)
			print(
				f"Shadow LOD {str(len(parsedMesh.shadowMeshLinkedLODList))} linked to Main Mesh LOD {str(lodIndex)}")
		parsedMesh.mainMeshLODList.append(parsedLODLevel)
		isFirstLOD = False
	# End LOD

	meshDataEndTime = time.time()
	meshDataExportTime = meshDataEndTime - meshDataStartTime

	print(f"Gathering mesh data took {timeFormat % (meshDataExportTime * 1000)} ms.")

	# TODO Calculate bounding boxes

	# print(parsedMesh.materialNameList)
	# Get weights for meshes and calculate bone bounding boxes
	weightStartTime = time.time()
	if armatureObj:
		print(f"Generating bone remap dictionary took {timeFormat % (boneRemapTime * 1000)} ms.")
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
		weightEndTime = time.time()
		weightExportTime = weightEndTime - weightStartTime
		print(f"Building bone bounding boxes took {timeFormat % (weightExportTime * 1000)} ms.")

	# Generate mesh bounding box and bounding sphere from lowest quality LOD level
	meshBBoxStartTime = time.time()
	vertArrayList = []
	for group in parsedMesh.mainMeshLODList[-1].visconGroupList:
		vertArrayList.extend([submesh.vertexPosList for submesh in group.subMeshList])
	# print(vertArrayList)
	if vertArrayList != []:
		fullVertArray = np.vstack(vertArrayList)
		if parsedMesh.boundingSphere == None:
			center, radius = bounding_sphere_ritter(fullVertArray)
			parsedMesh.boundingSphere = Sphere()
			parsedMesh.boundingSphere.x = center[0]
			parsedMesh.boundingSphere.y = center[1]
			parsedMesh.boundingSphere.z = center[2]
			parsedMesh.boundingSphere.r = radius
		# print(center)
		# print(radius)
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
	meshBBoxEndTime = time.time()
	meshBBoxTime = meshBBoxEndTime - meshBBoxStartTime
	print(f"Calculating mesh bounding sphere and bounding box took {timeFormat % (meshBBoxTime * 1000)} ms.")

	if parsedMesh.skeleton and parsedMesh.skeleton.weightedBones and len(
			parsedMesh.skeleton.weightedBones) > maxWeightedBones:
		print(
			f"\nMaximum Weighted Bones Exceeded! {str(len(parsedMesh.skeleton.weightedBones))} / {maxWeightedBones}")
		addErrorToDict(errorDict, "MaxWeightedBonesExceeded", None)
	"""
	if armatureObj != None and len(parsedMesh.skeleton.weightedBones) == 0 and len(parsedMesh.skeleton.boneList) > 0:
		raiseWarning(f"Mesh has armature, but the mesh is not weighted to the bones on the armature.\nWeighting meshes to {parsedMesh.skeleton.boneList[0].boneName} bone.")
		parsedMesh.skeleton.weightedBones.append(parsedMesh.skeleton.boneList[0].boneName)
		parsedMesh.skeleton.boneList[0].boundingBox = parsedMesh.boundingBox
	"""
	# Clear references
	evaluatedSubMeshData = None
	for mesh in deleteCopiedMeshList:
		bpy.data.objects.remove(mesh, do_unlink=True)
	# bpy.data.meshes.remove(mesh)
	if "clonedMeshes" in bpy.data.collections:
		bpy.data.collections.remove(bpy.data.collections["clonedMeshes"])
	deleteCopiedMeshList.clear()
	cloneMeshNameDict.clear()
	# print(remapDict)

	if subMeshCount == 0:
		addErrorToDict(errorDict, "NoMeshesInCollection", None)

	if errorDict != {}:
		# showErrorMessageBox("Mesh contains errors and can not be exported. Check the console (Window > Toggle System Console) for info on how to fix it.")
		printErrorDict(errorDict)

		showREMeshErrorWindow(targetCollection.name, armatureObj, errorDict)
		return False

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
	meshWriteEndTime = time.time()
	meshWriteExportTime = meshWriteEndTime - meshWriteStartTime
	print(f"Converting to RE Mesh took {timeFormat % (meshWriteExportTime * 1000)} ms.")
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
	print(f"Mesh export finished in {y(timeFormat % (meshExportTime * 1000))} ms.")

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
	if showWarningMessage:
		showMessageBox("Warnings occured during export. Check Window > Toggle System Console for details.",
		               title="Mesh Export Warning", icon="ERROR")
	print("\033[92m__________________________________\nRE Mesh export finished.\033[0m")
	return True
