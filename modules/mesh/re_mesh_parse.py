import numpy as np
from .file_re_mesh import Matrix4x4, AABB, Sphere

# MESH VERSIONS
VERSION_SF6 = 230110883
VERSION_MHWILDS_BETA = 240820143
VERSION_MHWILDS = 241111606
VERSION_MHS3 = 250604100
VERSION_PRAGDEMO = 250925211
VERSION_RE9 = 250925211

SIX_WEIGHT_MESH_VERSIONS = frozenset([
	VERSION_SF6,
	VERSION_MHWILDS_BETA,
	VERSION_MHWILDS,
	VERSION_MHS3,
	# VERSION_PRAGDEMO,
])

typeNameMapping = ["Position", "NorTan", "UV", "UV2", "Weight", "Color", "SF6UnknownVertexDataType",
                   "ExtraWeight"]
typeStrideDict = {
	"Position": 12,
	"NorTan": 8,
	"UV": 4,
	"UV2": 4,
	"Weight": 16,
	"Color": 4,
	"ExtraWeight": 16,
}

blendShapeNameMapping = ["BlendShapeByte", "BlendShapeShort"]
blendShapeStrideDict = {
	"BlendShapeByte": 4,
	"BlendShapeShort": 8,
}


def ReadPosBuffer(vertexPosBuffer, tags):
	return np.frombuffer(vertexPosBuffer, dtype="<3f")


def ReadNorTanBuffer(norTanBuffer, tags):
	norTanArray = np.frombuffer(norTanBuffer, dtype="<4b")
	# Use reshape + slice to avoid np.delete (creates copy)
	norTanArray = norTanArray.reshape(-1, 4)[:, :3].copy()
	norTanArray = np.divide(norTanArray, 127, dtype=np.float32)
	# Slice array by even and odd to get normals and tangents
	return (norTanArray[::2], norTanArray[1::2])


def ReadUVBuffer(uvBuffer, tags):
	uvArray = np.frombuffer(bytearray(uvBuffer),
	                        dtype="<e", )  # Convert bytes to bytearray to make numpy array mutable
	# Do (1-x) to v value
	uvArray[1::2] *= -1
	uvArray[1::2] += 1
	uvArray = uvArray.reshape((-1, 2))
	return uvArray


def ReadWeightBuffer(weightBuffer, tags):
	weightArray = np.frombuffer(weightBuffer, dtype="<8B")
	if "SixWeightCompressed" in tags:
		# Vectorized bitfield unpack using pure NumPy (no Python loop, no ctypes)
		# Each 8-byte block encodes 6x10-bit bone indices
		uint64Array = np.frombuffer(weightArray[::2].tobytes(), dtype="<Q")

		w0 = (uint64Array >> 0) & 0x3FF
		w1 = (uint64Array >> 10) & 0x3FF
		w2 = (uint64Array >> 20) & 0x3FF
		w3 = (uint64Array >> 32) & 0x3FF
		w4 = (uint64Array >> 42) & 0x3FF
		w5 = (uint64Array >> 52) & 0x3FF

		boneIndicesArray = np.column_stack([
			w0, w1, w2, w3, w4, w5,
			np.zeros(len(w0), dtype=np.uint64),
			np.zeros(len(w0), dtype=np.uint64)
		])
	else:
		boneIndicesArray = weightArray[::2].astype(np.uint8)
	boneWeightsArray = weightArray[1::2].astype(np.float32) / 255.0
	return (boneIndicesArray, boneWeightsArray)


def ReadColorBuffer(colorBuffer, tags):
	colorArray = np.frombuffer(colorBuffer, dtype="<4B", )
	colorArray = np.divide(colorArray, 255, dtype=np.float32)
	return colorArray


def ReadFaceBuffer(faceBuffer):
	return np.frombuffer(faceBuffer, dtype="<3H")


def ReadIntFaceBuffer(faceBuffer):
	return np.frombuffer(faceBuffer, dtype="<3I")


def readPackedBitsVec3Array(packedIntArray, numBits):
	limit = 2 ** numBits - 1
	vec3Array = np.empty((len(packedIntArray), 3), dtype=np.float32)
	vec3Array[:, 0] = ((packedIntArray >> 0) & limit) / limit
	vec3Array[:, 1] = ((packedIntArray >> (numBits * 1)) & limit) / limit
	vec3Array[:, 2] = ((packedIntArray >> (numBits * 2)) & limit) / limit
	return vec3Array


# MPLY

def ReadNorBuffer(norBuffer, tags):
	norArray = np.frombuffer(norBuffer, dtype="<4b")
	# Use reshape + slice instead of np.delete
	norArray = norArray.reshape(-1, 4)[:, :3].copy()
	return norArray


def ReadCompressedPosBuffer(vertexPosBuffer, bitFlag, center, relOffset, posDecodeScale=None,
                            posDecodeOffset=None):
	if bitFlag.flags.use24BitPos:
		byte3Array = np.frombuffer(vertexPosBuffer, dtype="<3b")
		posArray = byte3Array * (1.0 / 255)
		posArray[:] = 0  # TODO FIX 24 bit import
	elif bitFlag.flags.use32BitPos:
		packedIntArray = np.frombuffer(vertexPosBuffer, dtype="<I")
		# 10-10-10 packing - vectorized
		posArray = np.empty((len(packedIntArray), 3), dtype=np.float32)
		posArray[:, 0] = (packedIntArray & 1023)
		posArray[:, 1] = ((packedIntArray >> 10) & 1023)
		posArray[:, 2] = ((packedIntArray >> 20) & 1023)
	else:
		posArray = np.frombuffer(vertexPosBuffer, dtype="<3H")
		posArray = posArray.astype(dtype="f")
		posArray /= 65535.0

	# Use pre-computed scale/offset when available (MPLY optimization)
	if posDecodeScale is not None and posDecodeOffset is not None:
		scale = posDecodeScale
		offset = posDecodeOffset
	else:
		num = bitFlag.asUInt32
		divByte = (num >> 24) & 0xFF
		multByte = (num >> 16) & 0xFF
		divShift = (divByte - 127)
		scale = (1 << divShift) if divShift >= 0 else (1.0 / (1 << -divShift))
		offset = 1 << (multByte - divByte)

	posArray = (posArray - 0.5 + relOffset * offset) * scale + center

	return posArray


BufferReadDict = {
	"Position": ReadPosBuffer,
	"NorTan": ReadNorTanBuffer,
	"UV": ReadUVBuffer,
	"UV2": ReadUVBuffer,
	"Weight": ReadWeightBuffer,
	"Color": ReadColorBuffer,
	"SF6UnknownVertexDataType": ReadColorBuffer,
	# Read as color data for now until what it is can be determined
	"ExtraWeight": ReadWeightBuffer,
}


def ReadBlendShapeByteBuffer(blendShapeBuffer, tags):
	blendShapeIntArray = np.frombuffer(blendShapeBuffer, dtype="<I")
	blendShapeArray = readPackedBitsVec3Array(blendShapeIntArray, 10)
	return blendShapeArray


def ReadBlendShapeShortBuffer(blendShapeBuffer, tags):
	blendShapeArray = np.frombuffer(blendShapeBuffer, dtype="<4H", )
	# Remove 4th column using reshape + slice instead of np.delete
	blendShapeArray = blendShapeArray.reshape(-1, 4)[:, :3].copy()
	blendShapeArray = blendShapeArray.astype("float32")

	blendShapeArray = np.where(blendShapeArray < 0, blendShapeArray / 32768, blendShapeArray / 32767)
	return blendShapeArray


BlendShapeBufferReadDict = {
	"BlendShapeByte": ReadBlendShapeByteBuffer,
	"BlendShapeShort": ReadBlendShapeShortBuffer,
}


def ReadVertexElementBuffers(vertexElementList, vertexBuffer, tagSet):
	vertexDict = {
		"Position": None,
		"NorTan": None,
		"UV": None,
		"UV2": None,
		"Weight": None,
		"Color": None,
		"SF6UnknownVertexDataType": None,
		"ExtraWeight": None,
		"SecondaryWeight": None,
	}
	lastIndex = len(vertexElementList) - 1
	importedElementsSet = set()
	for index, vertexElement in enumerate(vertexElementList):
		stride = int(vertexElement['stride'])
		posOffset = int(vertexElement['posStartOffset'])
		if index == lastIndex:
			# bufferEnd = len(vertexBuffer)
			bufferEnd = posOffset + (stride * len(vertexDict["Position"]))
		else:
			bufferEnd = int(vertexElementList[index + 1]['posStartOffset'])
		elementName = typeNameMapping[int(vertexElement['typing'])]
		# print(f"{elementName} start {str(posOffset)} end {str(bufferEnd)} size {str(bufferEnd-posOffset)}")
		# print(elementName)

		if elementName not in importedElementsSet:  # Prevent importing of doubled vertex element entries present on some meshes
			# I suspect the doubled vertex elements are for when the shadow meshes have their own unique LODs
			# TODO Make shadowVertexDict
			if "shadowLOD" not in tagSet:  # Skip reading the first vertex element entry of a type if reading unique shadow LOD
				vertexDict[elementName] = BufferReadDict[elementName](
					vertexBuffer[posOffset:bufferEnd], tagSet)
			importedElementsSet.add(elementName)
		elif "shadowLOD" in tagSet:
			vertexDict[elementName] = BufferReadDict[elementName](
				vertexBuffer[posOffset:bufferEnd], tagSet)
	return vertexDict


class VisconGroup:
	def __init__(self):
		self.visconGroupNum = 0
		self.subMeshList = []


class LODLevel:
	def __init__(self):
		self.visconGroupList = []
		self.lodDistance = 0.0


class SubMesh:
	def __init__(self):
		self.vertexPosList = []
		self.faceList = []
		self.normalList = []
		self.tangentList = []
		self.uvList = []
		self.uv2List = []
		self.weightList = []
		self.weightIndicesList = []
		# MH Wilds extra weights
		self.extraWeightIndicesList = []
		self.extraWeightList = []
		self.colorList = []
		self.materialIndex = 0
		self.meshVertexOffset = 0  # Used for determining mesh reuse
		self.isReusedMesh = False
		self.linkedSubMesh = None
		self.subMeshIndex = 0
		self.blendShapeList = []
		# DD2 shape key weights
		self.secondaryWeightList = []
		self.secondaryWeightIndicesList = []
		# MPLY
		self.relPos = None
		self.boundingBox = None
		self.boundingBoxCenter = None


class ParsedBone:
	def __init__(self):
		self.boneName = "BONE"
		self.boneIndex = 0
		self.parentIndex = 0
		self.nextSiblingIndex = 0
		self.nextChildIndex = 0
		self.symmetryBoneIndex = 0
		self.useSecondaryWeight = 0
		self.worldMatrix = Matrix4x4()
		self.localMatrix = Matrix4x4()
		self.inverseMatrix = Matrix4x4()
		self.boundingBox = None  # Bounding box of weighted vertices


class Skeleton:
	def __init__(self):
		self.weightedBones = []
		self.boneList = []


class BlendShape:
	def __init__(self):
		self.blendShapeName = "newBlendShape"
		self.deltas = []


def rescale_blend_deltas(deltas, aabb_min, aabb_max):
	deltas[:, 0] = aabb_max.x * deltas[:, 0] + aabb_min.x
	deltas[:, 1] = aabb_max.y * deltas[:, 1] + aabb_min.y
	deltas[:, 2] = aabb_max.z * deltas[:, 2] + aabb_min.z


def parseLODStructure(reMesh, targetLODList, vertexDictList, faceBufferList, usedVertexOffsetDictList,
                      blendShapeBuffer=None):
	lodList = []
	currentBlendShapeOffset = 0
	blendShapeDict = {}
	for lodIndex, lodGroup in enumerate(targetLODList):

		# BLEND SHAPES - LOD level
		if reMesh.blendShapeHeader != None and len(reMesh.blendShapeHeader.blendShapeList) > lodIndex:
			blendShapeLODData = reMesh.blendShapeHeader.blendShapeList[lodIndex]
		else:
			blendShapeLODData = None

		# BLEND SHAPES - submesh
		currentBlendShapeNameIndex = 0
		currentBlendDeltaOffset = 0
		if blendShapeLODData != None:
			blendShapeTags = set()  # Unused currently but there if needed in the future
			bufferType = blendShapeNameMapping[blendShapeLODData.typing]
			bufferStride = blendShapeStrideDict[bufferType]

			# Only parse current LOD's blend shape data instead of the whole buffer
			endOffset = currentBlendShapeOffset + (blendShapeLODData.vertCount * bufferStride)
			blendShapeDeltas = BlendShapeBufferReadDict[bufferType](
				blendShapeBuffer[currentBlendShapeOffset:endOffset], tags=blendShapeTags)
			currentBlendShapeOffset = endOffset

			currentDeltaOffset = 0
			# TODO - Blend shape vertex count can span across meshes, add list of vertex ranges for every sub mesh
			for blendTargetIndex, blendTarget in enumerate(blendShapeLODData.blendTargetList):

				if blendShapeLODData.typing == 0:
					step_size_x = (blendShapeLODData.aabbList[blendTargetIndex].max.x -
					               blendShapeLODData.aabbList[blendTargetIndex].min.x) / (2 ** 11 - 1)
					step_size_y = (blendShapeLODData.aabbList[blendTargetIndex].max.y -
					               blendShapeLODData.aabbList[blendTargetIndex].min.y) / (2 ** 10 - 1)
					step_size_z = (blendShapeLODData.aabbList[blendTargetIndex].max.z -
					               blendShapeLODData.aabbList[blendTargetIndex].min.z) / (2 ** 11 - 1)
				else:
					step_size_x = (blendShapeLODData.aabbList[blendTargetIndex].max.x -
					               blendShapeLODData.aabbList[blendTargetIndex].min.x) / (2 ** 16 - 1)
					step_size_y = (blendShapeLODData.aabbList[blendTargetIndex].max.y -
					               blendShapeLODData.aabbList[blendTargetIndex].min.y) / (2 ** 16 - 1)
					step_size_z = (blendShapeLODData.aabbList[blendTargetIndex].max.z -
					               blendShapeLODData.aabbList[blendTargetIndex].min.z) / (2 ** 16 - 1)

				for blendNameIndex in range(0, blendTarget.blendShapeNum):
					blendShapeName = reMesh.rawNameList[
						reMesh.blendShapeNameRemapList[currentBlendShapeNameIndex + blendNameIndex]]

					# print(blendShapeEntry.blendShapeName)
					if blendTarget.subMeshEntryCount != 0:  # If Version >= SF6
						for subMeshEntry in blendTarget.subMeshEntryList:

							blendShapeEntry = BlendShape()
							blendShapeEntry.blendShapeName = blendShapeName
							blendShapeEntry.deltas = blendShapeDeltas[
								currentBlendDeltaOffset:currentBlendDeltaOffset + subMeshEntry.vertCount]
							aabb = blendShapeLODData.aabbList[blendTargetIndex]
							rescale_blend_deltas(blendShapeEntry.deltas, aabb.min, aabb.max)

							currentBlendDeltaOffset += subMeshEntry.vertCount
							blendShapeDict.setdefault(subMeshEntry.subMeshVertexStartIndex, []).append(blendShapeEntry)

					else:
						blendShapeEntry = BlendShape()
						blendShapeEntry.blendShapeName = blendShapeName
						blendShapeEntry.deltas = blendShapeDeltas[
							currentBlendDeltaOffset:currentBlendDeltaOffset + blendTarget.vertCount]
						aabb = blendShapeLODData.aabbList[blendTargetIndex]
						rescale_blend_deltas(blendShapeEntry.deltas, aabb.min, aabb.max)

						currentBlendDeltaOffset += blendTarget.vertCount
						blendShapeDict.setdefault(blendTarget.subMeshVertexStartIndex, []).append(blendShapeEntry)

				currentBlendShapeNameIndex += blendTarget.blendShapeNum

		lod = LODLevel()
		lod.lodDistance = lodGroup.distance
		# print(f"lod {lodIndex}")
		for visconGroup in lodGroup.meshGroupList:

			group = VisconGroup()
			group.visconGroupNum = visconGroup.visconGroupID
			lastSubmeshIndex = len(visconGroup.vertexInfoList) - 1

			# Precompute common references outside the loop
			has32BitIndex = reMesh.lodHeader.has32BitIndexBuffer
			faceBuffer = faceBufferList[
				visconGroup.vertexInfoList[0].vertexBufferIndex] if visconGroup.vertexInfoList else None
			indexMultiplier = 4 if has32BitIndex else 2

			for index, meshInfo in enumerate(visconGroup.vertexInfoList):
				if index == lastSubmeshIndex:
					bufferEnd = visconGroup.vertexInfoList[0].vertexStartIndex + visconGroup.vertexCount
				else:
					bufferEnd = visconGroup.vertexInfoList[index + 1].vertexStartIndex
				submesh = SubMesh()
				submesh.materialIndex = meshInfo.materialIndex
				submesh.subMeshIndex = index

				if meshInfo.vertexStartIndex in usedVertexOffsetDictList[meshInfo.vertexBufferIndex]:
					submesh.isReusedMesh = True
					submesh.linkedSubMesh = usedVertexOffsetDictList[meshInfo.vertexBufferIndex][
						meshInfo.vertexStartIndex]
				else:
					usedVertexOffsetDictList[meshInfo.vertexBufferIndex][meshInfo.vertexStartIndex] = submesh
				submesh.meshVertexOffset = meshInfo.vertexStartIndex

				vertexDict = vertexDictList[meshInfo.vertexBufferIndex]
				if vertexDict["Position"] is not None:
					submesh.vertexPosList = vertexDict["Position"][meshInfo.vertexStartIndex:bufferEnd]

				# Precompute face buffer slice
				faceStart = meshInfo.faceStartIndex * indexMultiplier
				faceEnd = faceStart + meshInfo.faceCount * indexMultiplier
				if has32BitIndex:
					submesh.faceList = ReadIntFaceBuffer(faceBuffer[faceStart:faceEnd])
				else:
					submesh.faceList = ReadFaceBuffer(faceBuffer[faceStart:faceEnd])
				if vertexDict["NorTan"] is not None:
					submesh.normalList = vertexDict["NorTan"][0][meshInfo.vertexStartIndex:bufferEnd]
					submesh.tangentList = vertexDict["NorTan"][1][meshInfo.vertexStartIndex:bufferEnd]
				if vertexDict["UV"] is not None:
					submesh.uvList = vertexDict["UV"][meshInfo.vertexStartIndex:bufferEnd]
				if vertexDict["UV2"] is not None:
					submesh.uv2List = vertexDict["UV2"][meshInfo.vertexStartIndex:bufferEnd]
				if vertexDict["Weight"] is not None:
					submesh.weightIndicesList = vertexDict["Weight"][0][meshInfo.vertexStartIndex:bufferEnd]
					submesh.weightList = vertexDict["Weight"][1][meshInfo.vertexStartIndex:bufferEnd]
				if vertexDict["ExtraWeight"] is not None:
					submesh.extraWeightIndicesList = vertexDict["ExtraWeight"][0][
						meshInfo.vertexStartIndex:bufferEnd]
					submesh.extraWeightList = vertexDict["ExtraWeight"][1][
						meshInfo.vertexStartIndex:bufferEnd]

				if vertexDict["Color"] is not None:
					submesh.colorList = vertexDict["Color"][meshInfo.vertexStartIndex:bufferEnd]

				if vertexDict["SecondaryWeight"] is not None:
					submesh.secondaryWeightIndicesList = vertexDict["SecondaryWeight"][0][
						meshInfo.vertexStartIndex:bufferEnd]
					submesh.secondaryWeightList = vertexDict["SecondaryWeight"][1][
						meshInfo.vertexStartIndex:bufferEnd]

				# if blendShapeLODData != None:
				if meshInfo.vertexStartIndex in blendShapeDict:
					submesh.blendShapeList.extend(blendShapeDict[meshInfo.vertexStartIndex])
				group.subMeshList.append(submesh)
			lod.visconGroupList.append(group)
		lodList.append(lod)
	return lodList


def debug_Generate010StreamingTemplate(templateLODList):
	# Yes this is driving me insane to the point to where I'm generating an 010 template to check if the buffers are being read correctly
	print("//Auto generated streaming mesh template")
	print("""
typedef struct
{
    float x;
    float y;
    float z;
}Position<bgcolor=0x0000FF>;

typedef struct
{
    ubyte normal[4];
    ubyte tangent[4];
}NorTan<bgcolor=0x00FF00>;

typedef struct
{
    hfloat u;
    hfloat v;
}UV<bgcolor=0xFF0000>;

typedef struct
{
    hfloat u;
    hfloat v;
}UV2<bgcolor=0xCC0000>;
typedef struct
{
    uint64 w0:10;;
    uint64 w1:10;
    uint64 w2:10;
    uint64 pad0:2;
    uint64 w3:10;
    uint64 w4:10;
    uint64 w5:10;
    uint64 pad1:2;
	ubyte indices[8];
}Weight<bgcolor=0x00FFFF>;

typedef struct
{
    ubyte r;
    ubyte g;
    ubyte b;
    ubyte a;
}Color<bgcolor=0xFFFF00>;

	""")
	for index, elementList in enumerate(templateLODList):
		print("struct")
		print("{")
		for element in elementList:
			print("\tFSeek(" + str(element["start"]) + ");\n\t struct\n\t{")
			print("\t\t" + element["type"] + " entry[" + str(
				(element["end"] - element["start"]) // element["stride"]) + "];")
			print("\t}element;")
		print("}LOD" + str(index) + ";")

	print("//EOF")


class ParsedREMesh:
	def __init__(self):
		self.isMPLY = False
		self.skeleton = None
		self.mainMeshLODList = []
		# self.shadowMeshLODList = []#Commented out because shadow meshes can only reuse lods from main mesh
		self.shadowMeshLinkedLODList = []  #
		self.occlusionMeshLODList = []
		self.nameList = []
		self.boneNameRemapList = []
		self.materialNameList = []
		self.boundingSphere = Sphere()
		self.boundingBox = AABB()
		self.bufferHasPosition = False
		self.bufferHasNorTan = False
		self.bufferHasUV = False
		self.bufferHasUV2 = False
		self.bufferHasWeight = False
		self.bufferHasColor = False
		self.bufferHasIntFaces = False
		self.bufferHasExtraWeight = False  # Doubled weight buffer, used in MH Wilds
		self.bufferHasSecondaryWeight = False  # DD2 shapekeys

	def ParseREMesh(self, reMesh, importOptions={"importAllLOD": True, "importShadowMesh": True,
	                                             "importOcclusionMesh": True, "importBlendShapes": True}):

		self.isMPLY = reMesh.isMPLY
		usedVertexOffsetDictList = []
		lodOffsetDict = dict()  # Used for linking shadow mesh lods to main mesh lods
		self.nameList = reMesh.rawNameList
		self.boneNameRemapList = reMesh.boneNameRemapList
		self.materialRemapList = reMesh.materialNameRemapList
		# Parse Skeleton
		for remapIndex in reMesh.materialNameRemapList:
			self.materialNameList.append(reMesh.rawNameList[remapIndex])
		if reMesh.skeletonHeader != None:
			self.skeleton = Skeleton()
			self.skeleton.weightedBones = []

			for remapIndex in reMesh.skeletonHeader.boneRemapList:
				self.skeleton.weightedBones.append(reMesh.rawNameList[reMesh.boneNameRemapList[remapIndex]])

			# I hope this doesn't cause weird issues somewhere, the root bone isn't counted in the remap table but it is used by some meshes and that causes issues
			# EX F:\RE2RT_EXTRACT\re_chunk_000\natives\STM\ObjectRoot\SetModel\sm4x_Gimmick\sm42\sm42_253_Switch01A\sm42_253_Switch01A_00md.mesh.2109108288
			# Check if the root bone is weighted and add it to the weighted bone list

			# Update: turns out this was right but the root bone is supposed to be the last bone in the list, not the first
			if reMesh.boneBoundingBoxHeader != None and reMesh.skeletonHeader.remapCount != reMesh.boneBoundingBoxHeader.count:
				self.skeleton.weightedBones.append(reMesh.rawNameList[reMesh.boneNameRemapList[0]])
			weightedBoneIndex = 0
			for i in range(reMesh.skeletonHeader.boneCount):
				# print(i)
				bone = ParsedBone()
				bone.boneName = reMesh.rawNameList[reMesh.boneNameRemapList[i]]
				bone.boneIndex = i
				bone.parentIndex = reMesh.skeletonHeader.boneInfoList[i].boneParent
				bone.nextSiblingIndex = reMesh.skeletonHeader.boneInfoList[i].boneSibling
				bone.nextChildIndex = reMesh.skeletonHeader.boneInfoList[i].boneChild
				bone.symmetryBoneIndex = reMesh.skeletonHeader.boneInfoList[i].boneSymmetric
				bone.useSecondaryWeight = reMesh.skeletonHeader.boneInfoList[i].useSecondaryWeight
				bone.worldMatrix = reMesh.skeletonHeader.worldMatList[i]
				bone.localMatrix = reMesh.skeletonHeader.localMatList[i]
				bone.inverseMatrix = reMesh.skeletonHeader.inverseMatList[i]

				if bone.boneName in self.skeleton.weightedBones:
					try:
						bone.boundingBox = reMesh.boneBoundingBoxHeader.bboxList[weightedBoneIndex]
						weightedBoneIndex += 1
					except IndexError:
						print("WARNING: Missing bone bounding box, likely incorrectly exported mesh mod")
				self.skeleton.boneList.append(bone)

		# Parse Vertex Buffer
		if reMesh.meshBufferHeader != None:
			tags = set()
			if reMesh.meshVersion in SIX_WEIGHT_MESH_VERSIONS or reMesh.fileHeader.version == 250707828:  # Street Fighter 6 mesh version + MH Wilds, #Pragmata internal mesh version uses 6 weight but RE9 uses 8
				tags.add("SixWeightCompressed")  # Add tag to parse compressed weights
			# if duplicate in vertexelementlist, add shadowLOD tag

			vertexDictList = []
			faceBufferList = []

			vertexDictList.append(ReadVertexElementBuffers(reMesh.meshBufferHeader.vertexElementList,
			                                               reMesh.meshBufferHeader.vertexBuffer, tags))
			faceBufferList.append(reMesh.meshBufferHeader.faceBuffer)

			if reMesh.meshBufferHeader.secondaryWeightBuffer != None:
				vertexDictList[-1]["SecondaryWeight"] = ReadWeightBuffer(
					reMesh.meshBufferHeader.secondaryWeightBuffer, tags=set())

			if reMesh.streamingInfoHeader != None and reMesh.streamingInfoHeader.entryCount != 0 and reMesh.streamingBuffer != None:
				for entry in reMesh.meshBufferHeader.streamingBufferHeaderList:
					vertexDictList.append(
						ReadVertexElementBuffers(entry.vertexElementList, entry.vertexBuffer, tags))
					faceBufferList.append(entry.faceBuffer)
					usedVertexOffsetDictList.append(dict())

			usedVertexOffsetDictList.append(dict())
			# TODO
			# tags.add("shadowLOD")
			# shadowVertexDict = ReadVertexElementBuffers(reMesh.meshBufferHeader.vertexElementList, reMesh.meshBufferHeader.vertexBuffer,tags)
			# Parse Blend Shapes
			vertexCount = len(vertexDictList[-1]["Position"])
			lastElement = reMesh.meshBufferHeader.vertexElementList[-1]
			blendShapeStartPos = int(lastElement['posStartOffset']) + vertexCount * int(lastElement['stride'])
			blendShapeBuffer = reMesh.meshBufferHeader.vertexBuffer[blendShapeStartPos:]
			if reMesh.blendShapeHeader != None:
				print(
					f"blendShape buffer start pos {str(reMesh.meshBufferHeader.vertexBufferOffset + blendShapeStartPos)}")

		# Parse Main Meshes
		if reMesh.lodHeader != None and len(vertexDictList) != 0:
			if reMesh.lodHeader.has32BitIndexBuffer:
				self.bufferHasIntFaces = True
			self.boundingSphere = reMesh.lodHeader.sphere
			self.boundingBox = reMesh.lodHeader.bbox
			self.mainMeshLODList = parseLODStructure(reMesh, reMesh.lodHeader.lodGroupList, vertexDictList,
			                                         faceBufferList, usedVertexOffsetDictList,
			                                         blendShapeBuffer)
			for i in range(len(self.mainMeshLODList)):
				lodOffsetDict[reMesh.lodHeader.lodGroupOffsetList[i]] = self.mainMeshLODList[i]
		if reMesh.shadowHeader != None and len(vertexDictList) != 0:
			for offset in reMesh.shadowHeader.lodGroupOffsetList:
				if offset in lodOffsetDict:
					self.shadowMeshLinkedLODList.append(lodOffsetDict[offset])
				else:  # This shouldn't happen
					# Update: it does :/
					# RE3_EXTRACT\re_chunk_000\natives\stm\escape\character\enemy\em9200\mesh\em9200.mesh.2109108288
					# TODO Add unique shadow mesh LOD importing
					print("ERROR: Shadow mesh has unique lod offsets, cannot import")
		# self.shadowMeshLODList = parseLODStructure(reMesh,reMesh.shadowHeader.lodGroupList,vertexDict,usedVertexOffsetDict)

		# TODO Add occlusion mesh

		if self.isMPLY:

			minAABB = reMesh.meshletLayout.meshletHeader.minAABB
			maxAABB = reMesh.meshletLayout.meshletHeader.maxAABB
			self.boundingBox.min.x = minAABB[0]
			self.boundingBox.min.y = minAABB[1]
			self.boundingBox.min.z = minAABB[2]
			self.boundingBox.max.x = maxAABB[0]
			self.boundingBox.max.y = maxAABB[1]
			self.boundingBox.max.z = maxAABB[2]

			AABBCenter = (np.array(minAABB) + np.array(maxAABB)) / 2
			AABBOffset = np.array(reMesh.meshletBVH.offset)
			AABBScale = reMesh.meshletBVH.scale
			# Pre-compute HALF_VEC for offset calculation (avoids np.array per cluster)
			HALF_VEC = np.float32(0.5)
			print("Parsing MPLY.")
			self.mainMeshLODList = []
			self.materialNameList = reMesh.rawNameList
			for lodIndex in range(0, reMesh.meshletLayout.gpuMeshletHeader.lodNum):
				lod = LODLevel()
				lod.lodDistance = reMesh.meshletLayout.gpuMeshletHeader.lodFactor
				group = VisconGroup()
				group.visconGroupNum = 0

				for submeshIndex, clusterHeader in enumerate(
						reMesh.meshletBVH.clusterHeaderLODList[lodIndex]):
					submesh = SubMesh()
					submesh.materialIndex = clusterHeader.bitfield.fields.materialId
					submesh.subMeshIndex = submeshIndex
					meshEntry = reMesh.clusterInfoLayout.lodList[lodIndex].entryList[submeshIndex]

					tags = set()

					# Calculate bounding box (meshEntry values are already numpy arrays)
					submesh.boundingBox = AABB()

					# Use pre-stored numpy arrays from ClusterInfo - no np.array() overhead
					subAABBMin = (meshEntry.bboxAABBCenter - meshEntry.bboxExtent) * AABBScale + AABBOffset
					subAABBMax = (meshEntry.bboxAABBCenter + meshEntry.bboxExtent) * AABBScale + AABBOffset
					submesh.relPos = (0.0, 0.0, 0.0)

					submesh.boundingBox.min.x = subAABBMin[0]
					submesh.boundingBox.min.y = subAABBMin[1]
					submesh.boundingBox.min.z = subAABBMin[2]

					submesh.boundingBox.max.x = subAABBMax[0]
					submesh.boundingBox.max.y = subAABBMax[1]
					submesh.boundingBox.max.z = subAABBMax[2]

					# Use pre-computed scale/offset and numpy arrays from ClusterInfo
					relOffset = meshEntry.bboxAABBCenter - HALF_VEC
					submesh.vertexPosList = ReadCompressedPosBuffer(
						meshEntry.posBuffer, meshEntry.bitFlag,
						meshEntry.partAABBCenter, relOffset,
						posDecodeScale=meshEntry.posDecodeScale,
						posDecodeOffset=meshEntry.posDecodeOffset
					)
					if meshEntry.bitFlag.flags.isMeshletNoTangent:
						submesh.normalList = ReadNorBuffer(meshEntry.normalBuffer, tags)
					else:
						normalList, tangentList = ReadNorTanBuffer(meshEntry.normalBuffer, tags)
						submesh.normalList = normalList
						submesh.tangentList = tangentList

					submesh.uvList = ReadUVBuffer(meshEntry.uvBuffer, tags)
					if meshEntry.uv2Buffer is not None:
						submesh.uv2List = ReadUVBuffer(meshEntry.uv2Buffer, tags)
					if meshEntry.uv3Buffer is not None:
						submesh.uv3List = ReadUVBuffer(meshEntry.uv3Buffer, tags)
					if meshEntry.colorBuffer is not None:
						submesh.colorList = ReadColorBuffer(meshEntry.colorBuffer, tags)
					if reMesh.streamingBuffer is not None:
						faceStartOffset = clusterHeader.indexOffsetBytes
						faceEndOffset = clusterHeader.indexOffsetBytes + (
									clusterHeader.bitfield.fields.indexCount * 2)
						submesh.faceList = ReadFaceBuffer(
							reMesh.streamingBuffer[faceStartOffset:faceEndOffset])
					else:
						submesh.faceList = ReadFaceBuffer(meshEntry.faceBuffer)

					group.subMeshList.append(submesh)
				lod.visconGroupList.append(group)
				self.mainMeshLODList.append(lod)
