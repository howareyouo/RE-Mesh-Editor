# Author: NSA Cloud
# Credit to AsteriskAmpersand for original re mesh addon that I used for reference
# Also credit to AlphaZomega for noesis re mesh plugin and re mesh 010 template

# How mesh import/export works:
# 1. The mesh file is imported in it's original structure and layout in this file (file_re_mesh.py)
# 2. The mesh structure is converted into an intermediary parsed format in re_mesh_parse.py
# 3. The parsed mesh format is passed to blender to be imported by blender_re_mesh.py
# 4. For export in blender, the mesh is checked for errors or things that otherwise wont work in the mesh format
# 5. The parsed mesh format is rebuilt inside blender_re_mesh.py once it has been error checked
# 6. The parsed format is passed back to file_re_mesh.py and rebuilt into a mesh structure (ParsedREMeshToREMesh())

IMPORT_BLEND_SHAPES = False  # Disabled by default because it's broken at the moment.
# Set to True if you want to try to fix blend shape importing. The relevant code is in re_mesh_parse.py.
# There's something wrong with getting the amount of deltas and also the way the deltas are parsed is not correct.

# Meshes to test blend shapes with:
# MHR player face "F:\MHR_EXTRACT\extract\re_chunk_000\natives\STM\player\mod\face\pl_face000.mesh.2109148288"
# RE4R leon face "I:\RE4_EXTRACT\re_chunk_000\natives\STM\_Chainsaw\Character\ch\cha0\cha000\10\cha000_10.mesh.221108797"
# SF6 chun li body "J:\SF6_EXTRACT\re_chunk_000\natives\stm\product\model\esf\esf004\001\01\esf004_001_01.mesh.230110883"

IMPORT_MPLY = True
# Not implemented fully yet, need to figure out unkn struct and how meshlets get positioned

# Large file I/O buffer (1MB) for 20-100MB mesh files
MESH_IO_BUFFER_SIZE = 1024 * 1024

import numpy as np
import os
import struct
import time
from io import BytesIO
from ..gen_functions import (ClampNameList, formatMs, getBit, getPaddedPos, getPaddingAmount,
                             openFileRead, openFileWrite, parseFileVersion, raiseError, raiseWarning,
                             read_float, read_null_terminated_names, read_ubyte, read_uint, read_uint64,
                             read_uint64_array, read_ushort, read_ushort_array, setBit, splitNativesPath,
                             write_float, write_ubyte, write_uint, write_uint64, write_ushort)
from .file_re_mesh_mply import REMeshMPLY, StreamingInfo, StreamingInfoEntry

# Version numbers and remap tables live in mesh_versions.py (single source of truth,
# shared with re_mesh_parse.py and file_re_mesh_mply.py).
# Explicit import (was `import *`): the star import both hid `os` leaking in via
# gen_functions' old star export (fixed 2026-09) and prevented static analysis.
# meshFileVersionToGameNameDict + SIX_WEIGHT_MESH_FILE_VERSIONS are re-exported
# for blender_re_mesh_import/export/utils which import them FROM this module.
from .mesh_versions import (PRAGMATA_BLEND_SHAPE_FILE_VERSIONS, SIX_WEIGHT_GAMES,
                            SIX_WEIGHT_MESH_FILE_VERSIONS,
                            VERSION_DD2, VERSION_DD2NEW, VERSION_DR, VERSION_MHWILDS, VERSION_ONI2,
                            VERSION_PRAG, VERSION_RE8, VERSION_RE9, VERSION_SF6,
                            getNearestRemapVersion, meshFileVersionToGameNameDict,
                            meshFileVersionToInternalVersionDict, meshFileVersionToNewVersionDict,
                            newVersionToMeshFileVersion)

# Vertex element layout (u16 typing + u16 stride + u32 offset) - shared by every
# batched read/write of vertex element tables (previously a hardcoded literal x6)
VERTEX_ELEMENT_DTYPE = np.dtype([('typing', '<u2'), ('stride', '<u2'), ('posStartOffset', '<u4')])



class Vec3():
	def __init__(self):
		self.x = 0.0
		self.y = 0.0
		self.z = 0.0

	def read(self, file):
		self.x = read_float(file)
		self.y = read_float(file)
		self.z = read_float(file)

	def write(self, file):
		file.write(np.array([self.x, self.y, self.z], dtype="<f").tobytes())


class Vec4():
	def __init__(self):
		self.x = 0.0
		self.y = 0.0
		self.z = 0.0
		self.w = 0.0

	def read(self, file):
		self.x = read_float(file)
		self.y = read_float(file)
		self.z = read_float(file)
		self.w = read_float(file)

	def write(self, file):
		file.write(np.array([self.x, self.y, self.z, self.w], dtype="<f").tobytes())


class Sphere():
	def __init__(self):
		self.x = 0.0
		self.y = 0.0
		self.z = 0.0
		self.r = 0.0

	def read(self, file):
		self.x = read_float(file)
		self.y = read_float(file)
		self.z = read_float(file)
		self.r = read_float(file)

	def write(self, file):
		file.write(np.array([self.x, self.y, self.z, self.r], dtype="<f").tobytes())


class Matrix4x4():
	def __init__(self):
		self.matrix = [[0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]]

	def read(self, file):
		self.matrix = np.frombuffer(file.read(64), dtype='<4f').tolist()

	def write(self, file):
		file.write(np.asarray(self.matrix, dtype=np.float32).tobytes())


class AABB():
	def __init__(self):
		self.min = Vec4()
		self.max = Vec4()

	def read(self, file):
		self.min.read(file)
		self.max.read(file)

	def write(self, file):
		self.min.write(file)
		self.max.write(file)


class MaterialSubdivision():
	def __init__(self):
		self.materialIndex = 0
		self.isQuad = 0
		self.vertexBufferIndex = 0
		self.padding = 0
		self.dr_unkn0 = 0
		self.faceCount = 0
		self.faceStartIndex = 0
		self.vertexStartIndex = 0
		self.streamingOffsetBytes = 0
		self.streamingPlatormSpecificOffsetBytes = 0
		self.dr_unkn1 = 0

	def read(self, file, version):
		# Batch read: 4 bytes base fields
		raw = file.read(4)
		self.materialIndex, self.isQuad, self.vertexBufferIndex, self.padding = struct.unpack_from('<BBBB', raw)
		# Variable-length fields based on version
		fields = []
		if version >= VERSION_DR:
			fields.append(read_uint(file))
		fields.extend([read_uint(file), read_uint(file), read_uint(file)])
		if version >= VERSION_RE8:
			fields.extend([read_uint(file), read_uint(file)])
		if version >= VERSION_DD2NEW:
			fields.append(read_uint(file))
		idx = 0
		self.faceCount = fields[idx]; idx += 1
		self.faceStartIndex = fields[idx]; idx += 1
		self.vertexStartIndex = fields[idx]; idx += 1
		if version >= VERSION_RE8:
			self.streamingOffsetBytes = fields[idx]; idx += 1
			self.streamingPlatormSpecificOffsetBytes = fields[idx]; idx += 1
		if version >= VERSION_DD2NEW:
			self.dr_unkn1 = fields[idx]

	def write(self, file, version):
		file.write(struct.pack('<BBBB', self.materialIndex, self.isQuad, self.vertexBufferIndex, self.padding))
		if version >= VERSION_DR:
			write_uint(file, self.dr_unkn0)
		write_uint(file, self.faceCount)
		write_uint(file, self.faceStartIndex)
		write_uint(file, self.vertexStartIndex)
		if version >= VERSION_RE8:
			write_uint(file, self.streamingOffsetBytes)
			write_uint(file, self.streamingPlatormSpecificOffsetBytes)
		if version >= VERSION_DD2NEW:
			write_uint(file, self.dr_unkn1)


class MeshGroup():
	def __init__(self):
		self.visconGroupID = 0
		self.meshCount = 0
		self.null0 = 0
		self.null1 = 0
		self.null2 = 0
		self.vertexCount = 0
		self.faceCount = 0
		self.vertexInfoList = []

	def read(self, file, version):
		# Batch read: ubyte×2 + ushort×3 + uint×2 = 16 bytes
		raw = file.read(16)
		(self.visconGroupID, self.meshCount,
		 self.null0, self.null1, self.null2,
		 self.vertexCount, self.faceCount) = struct.unpack_from('<BBHHHII', raw)
		for i in range(0, self.meshCount):
			entry = MaterialSubdivision()
			entry.read(file, version)
			self.vertexInfoList.append(entry)

	def write(self, file, version):
		file.write(struct.pack('<BBHHHII', self.visconGroupID, self.meshCount,
			self.null0, self.null1, self.null2, self.vertexCount, self.faceCount))
		for entry in self.vertexInfoList:
			entry.write(file, version)


class LODGroupHeader():
	def __init__(self):
		self.count = 0
		self.vertexFormat = 0
		self.reserved = 0
		self.distance = 0
		self.offsetOffset = 0
		self.meshGroupOffsetList = []
		# padding align 16
		self.meshGroupList = []

	def read(self, file, version):
		self.count = read_ubyte(file)
		self.vertexFormat = read_ubyte(file)
		self.reserved = read_ushort(file)
		self.distance = read_float(file)
		self.offsetOffset = read_uint64(file)
		if self.count > 0:
			self.meshGroupOffsetList = read_uint64_array(file, self.count)
		file.seek(getPaddedPos(file.tell(), 16))
		for i in range(0, self.count):
			entry = MeshGroup()
			entry.read(file, version)
			self.meshGroupList.append(entry)

	def write(self, file, version):
		write_ubyte(file, self.count)
		write_ubyte(file, self.vertexFormat)
		write_ushort(file, self.reserved)
		write_float(file, self.distance)
		write_uint64(file, self.offsetOffset)
		if self.meshGroupOffsetList:
			file.write(struct.pack(f'<{len(self.meshGroupOffsetList)}Q', *self.meshGroupOffsetList))
		file.seek(getPaddedPos(file.tell(), 16))
		for entry in self.meshGroupList:
			entry.write(file, version)


class MainMeshHeader():
	def __init__(self):
		self.lodGroupCount = 0
		self.materialCount = 0
		self.uvCount = 0
		self.skinWeightCount = 18
		self.totalMeshCount = 0
		self.has32BitIndexBuffer = 0
		self.sharedLodBits = 0
		self.nullPadding = 0  # PRE RE8
		self.sphere = Sphere()
		self.bbox = AABB()
		self.offsetOffset = 0
		self.lodGroupOffsetList = []
		self.lodGroupList = []

	# padding align 16
	def read(self, file, version, lodTarget=None):
		# Batch read: byte×4 + ushort + byte×2 + (optional uint64) = 8+2+2+(8) bytes
		raw = file.read(8)
		(self.lodGroupCount, self.materialCount, self.uvCount, self.skinWeightCount,
		 self.totalMeshCount, self.has32BitIndexBuffer, self.sharedLodBits) = struct.unpack_from('<BBBBHBB', raw)
		if version < VERSION_RE8:
			self.nullPadding = read_uint64(file)
		# Batch read: Sphere(16) + AABB(32) + uint64 = 56 bytes
		raw2 = file.read(56)
		off = 0
		self.sphere.x, self.sphere.y, self.sphere.z, self.sphere.r = struct.unpack_from('<4f', raw2, off); off += 16
		self.bbox.min.x, self.bbox.min.y, self.bbox.min.z, self.bbox.min.w = struct.unpack_from('<4f', raw2, off); off += 16
		self.bbox.max.x, self.bbox.max.y, self.bbox.max.z, self.bbox.max.w = struct.unpack_from('<4f', raw2, off); off += 16
		self.offsetOffset, = struct.unpack_from('<Q', raw2, off)
		if self.lodGroupCount > 0:
			self.lodGroupOffsetList = read_uint64_array(file, self.lodGroupCount)
		self.lodGroupList = []
		startPos = file.tell()

		if lodTarget != None:
			lodTarget = abs(lodTarget)
			if lodTarget >= self.lodGroupCount:  # If the chosen LOD target isn't on the mesh, use the lowest quality LOD possible
				lodTarget = self.lodGroupCount - 1
		for lodIndex, offset in enumerate(self.lodGroupOffsetList):
			if lodTarget == None or lodTarget == lodIndex:  # Read only the target lod if specified
				file.seek(offset)
				entry = LODGroupHeader()
				entry.read(file, version)
				self.lodGroupList.append(entry)
		file.seek(startPos)
		file.seek(getPaddedPos(file.tell(), 16))

	def write(self, file, version):
		file.write(struct.pack('<BBBBHBB', self.lodGroupCount, self.materialCount,
			self.uvCount, self.skinWeightCount, self.totalMeshCount,
			self.has32BitIndexBuffer, self.sharedLodBits))
		if version < VERSION_RE8:
			write_uint64(file, self.nullPadding)
		# Batch write: Sphere + AABB + offsetOffset
		file.write(struct.pack('<4f', self.sphere.x, self.sphere.y, self.sphere.z, self.sphere.r))
		file.write(struct.pack('<4f', self.bbox.min.x, self.bbox.min.y, self.bbox.min.z, self.bbox.min.w))
		file.write(struct.pack('<4f', self.bbox.max.x, self.bbox.max.y, self.bbox.max.z, self.bbox.max.w))
		write_uint64(file, self.offsetOffset)
		if self.lodGroupOffsetList:
			file.write(struct.pack(f'<{len(self.lodGroupOffsetList)}Q', *self.lodGroupOffsetList))
		file.seek(getPaddedPos(file.tell(), 16))
		for entry in self.lodGroupList:
			entry.write(file, version)


class ShadowHeader():
	def __init__(self):
		self.lodGroupCount = 0
		self.materialCount = 0
		self.uvCount = 0
		self.skinWeightCount = 18
		self.totalMeshCount = 0
		self.nullPadding = 0
		self.offsetOffset = 0
		self.null0 = 0
		self.null1 = 0
		self.null2 = 0
		self.null3 = 0
		self.null4 = 0
		self.null5 = 0
		self.lodGroupOffsetList = []
		self.lodGroupList = []

	def read(self, file, version):
		# Batch read: byte×4 + uint = 8 bytes
		raw = file.read(8)
		(self.lodGroupCount, self.materialCount, self.uvCount, self.skinWeightCount,
		 self.totalMeshCount) = struct.unpack_from('<BBBBI', raw)
		if version < VERSION_RE8:
			self.nullPadding = read_uint64(file)
		# Batch read: 7×uint64 = 56 bytes
		raw2 = file.read(56)
		(self.offsetOffset, self.null0, self.null1, self.null2,
		 self.null3, self.null4, self.null5) = struct.unpack_from('<7Q', raw2)

		self.lodGroupOffsetList = []
		if self.lodGroupCount > 0:
			self.lodGroupOffsetList = read_uint64_array(file, self.lodGroupCount)
		self.lodGroupList = []

		# Commented out because there's no reason to read it, shadow meshes can only use main mesh lods
		file.seek(getPaddedPos(file.tell(), 16))

	def write(self, file, version):
		# print(file.tell())
		file.write(struct.pack('<BBBBI', self.lodGroupCount, self.materialCount,
			self.uvCount, self.skinWeightCount, self.totalMeshCount))
		if version < VERSION_RE8:
			write_uint64(file, self.nullPadding)
		# Batch write: 7×uint64
		file.write(struct.pack('<7Q', self.offsetOffset, self.null0, self.null1,
			self.null2, self.null3, self.null4, self.null5))
		if self.lodGroupOffsetList:
			file.write(struct.pack(f'<{len(self.lodGroupOffsetList)}Q', *self.lodGroupOffsetList))
		file.seek(getPaddedPos(file.tell(), 16))

		# Shadow meshes can't have unique lods, the game will crash


# WILDS
class StreamingBufferHeaderEntry():
	def __init__(self):
		self.unkn0 = 0
		self.totalBufferSize = 0
		self.vertexBufferLength = 0
		self.mainVertexElementCount = 0
		self.vertexElementCount = 0
		self.unpaddedBufferSize = 0
		self.unpaddedBufferSize2 = 0
		self.prag_unknOffset0 = 0
		self.prag_unknOffset1 = 0
		self.unkn7 = 0
		self.unkn8 = 0
		self.unkn9 = 0
		self.unkn10 = 0
		self.unkn11 = 0
		self.unkn12 = 0
		self.unkn13 = 0
		self.nextBufferOffset = 0
		self.unkn15 = 0
		self.vertexBuffer = None
		self.faceBuffer = None
		self.vertexElementList = []

	def read(self, file, version):
		# Batch read: uint64 + uint×2 + ushort×2 = 8+8+4 = 20 bytes
		raw = file.read(20)
		self.unkn0, self.totalBufferSize, self.vertexBufferLength = struct.unpack_from('<QII', raw)
		self.mainVertexElementCount, self.vertexElementCount = struct.unpack_from('<HH', raw, 12)
		if version >= VERSION_PRAG:
			raw2 = file.read(16)
			self.prag_unknOffset0, self.prag_unknOffset1 = struct.unpack_from('<QQ', raw2)
		# Batch read: uint×9 + uint + uint = 40 bytes
		raw3 = file.read(40)
		(self.unpaddedBufferSize, self.unpaddedBufferSize2, self.unkn7, self.unkn8,
		 self.unkn9, self.unkn10, self.unkn11, self.unkn12, self.unkn13,
		 self.nextBufferOffset, self.unkn15) = struct.unpack_from('<11I', raw3)

	def write(self, file, version):
		file.write(struct.pack('<QIIHH', self.unkn0, self.totalBufferSize,
			self.vertexBufferLength, self.mainVertexElementCount, self.vertexElementCount))
		if version >= VERSION_PRAG:
			file.write(struct.pack('<QQ', self.prag_unknOffset0, self.prag_unknOffset1))
		file.write(struct.pack('<11I', self.unpaddedBufferSize, self.unpaddedBufferSize2,
			self.unkn7, self.unkn8, self.unkn9, self.unkn10, self.unkn11,
			self.unkn12, self.unkn13, self.nextBufferOffset, self.unkn15))


#

class VertexElementStruct():
	def __init__(self):
		self.typing = 0
		self.stride = 0
		self.posStartOffset = 0

	def read(self, file):
		raw = file.read(8)
		self.typing, self.stride, self.posStartOffset = struct.unpack_from('<HHI', raw)

	def write(self, file):
		file.write(struct.pack('<HHI', self.typing, self.stride, self.posStartOffset))


class MeshBufferHeader():
	def __init__(self):
		self.vertexElementOffset = 0
		self.vertexBufferOffset = 0
		self.faceBufferOffset = 0
		self.sunbreakOffset = 0
		self.vertexBufferSize = 0
		self.faceBufferSize = 0
		self.mainVertexElementCount = 0
		self.vertexElementCount = 0
		self.prag_unknOffset0 = 0
		self.prag_unknOffset1 = 0
		self.block2FaceBufferOffset = 0
		self.NULL = 0
		self.vertexElementSize = 0  # TODO this field name is not correct
		self.unkn1 = -1
		self.sunbreakSecondUnknown = 0
		self.vertexElementList = []
		self.streamingBufferHeaderList = []  # WILDS
		self.vertexBuffer = bytearray()
		self.faceBuffer = bytearray()  # NOTE: Face buffer is padded to 4 byte alignment per sub mesh
		self.secondaryWeightBuffer = None  # DD2 shape keys
		# SF6
		self.totalBufferSize = 0
		self.sf6unkn0 = 0
		self.streamingVertexElementOffset = 0  # vectorStructSize
		self.sf6unkn2 = 0  # vectorStructOffset #TODO FIX - sf6unkn2 is vertexElementStreamInfoOffset

	def read(self, file, version, streamingHeader=None, streamingBuffer=None):
		# Batch read header fields: uint64×2 = 16 bytes
		raw = file.read(16)
		self.vertexElementOffset, self.vertexBufferOffset = struct.unpack_from('<QQ', raw)
		if version < VERSION_SF6:
			# Batch read: uint64 + (optional uint64) + uint×4 + ushort×2 + uint×2 + short×2 + (optional uint64)
			# Max: 8+8+16+4+8+4+8 = 56 bytes (with sunbreak)
			raw2 = file.read(48)
			off = 0
			self.faceBufferOffset, = struct.unpack_from('<Q', raw2, off); off += 8
			if version > VERSION_RE8:
				self.sunbreakOffset, = struct.unpack_from('<Q', raw2, off); off += 8
			else:
				off -= 0  # no sunbreak
			self.vertexBufferSize, self.faceBufferSize = struct.unpack_from('<II', raw2, off); off += 8
			self.mainVertexElementCount, self.vertexElementCount = struct.unpack_from('<HH', raw2, off); off += 4
			self.block2FaceBufferOffset, self.NULL = struct.unpack_from('<II', raw2, off); off += 8
			self.vertexElementSize, self.unkn1 = struct.unpack_from('<hh', raw2, off); off += 4
			if version > VERSION_RE8:
				self.sunbreakSecondUnknown, = struct.unpack_from('<Q', raw2, off)
		elif version >= VERSION_SF6:
			# Batch read: uint64 + uint×2 + ushort×2 + (optional uint64×2) + uint×2 + short×2 + uint64×4
			raw2 = file.read(80)
			off = 0
			self.sunbreakOffset, = struct.unpack_from('<Q', raw2, off); off += 8
			self.totalBufferSize, self.vertexBufferSize = struct.unpack_from('<II', raw2, off); off += 8
			self.faceBufferOffset = self.vertexBufferOffset + self.vertexBufferSize
			self.mainVertexElementCount, self.vertexElementCount = struct.unpack_from('<HH', raw2, off); off += 4
			if version >= VERSION_PRAG:
				self.prag_unknOffset0, self.prag_unknOffset1 = struct.unpack_from('<QQ', raw2, off); off += 16
			self.block2FaceBufferOffset, self.NULL = struct.unpack_from('<II', raw2, off); off += 8
			self.faceBufferSize = self.block2FaceBufferOffset - self.vertexBufferSize
			self.vertexElementSize, self.unkn1 = struct.unpack_from('<hh', raw2, off); off += 4
			(self.sunbreakSecondUnknown, self.sf6unkn0,
			 self.streamingVertexElementOffset, self.sf6unkn2) = struct.unpack_from('<QQQQ', raw2, off)

		if streamingHeader != None and streamingHeader.entryCount != 0 and streamingBuffer != None:
			# Made a bit of a miscalculation, this doesn't account for the fact that the vertex buffers can't just be stacked since the elements won't be grouped together correctly
			# Moved into re_mesh_parse

			# print("Merging streamed face buffers...")
			# print(f"Streamed buffer size {len(streamingBuffer)}")
			# elementArrayList = []

			# Zero-copy view over the streamed file data (slicing a memoryview is O(1))
			streamingView = memoryview(streamingBuffer)

			for i in range(0, streamingHeader.entryCount):

				entry = StreamingBufferHeaderEntry()
				entry.read(file, version)
				# print(entry.__dict__)
				streamInfo = streamingHeader.streamingInfoEntryList[i]
				# vertexBytes = streamingBuffer[streamInfo.bufferStart:streamInfo.bufferStart+entry.vertexBufferLength]
				# faceBytes = streamingBuffer[streamInfo.bufferStart+entry.vertexBufferLength:streamInfo.bufferStart+entry.unpaddedBufferSize]
				entry.vertexBuffer = streamingView[
					streamInfo.bufferStart:streamInfo.bufferStart + entry.vertexBufferLength]
				entry.faceBuffer = streamingView[
					streamInfo.bufferStart + entry.vertexBufferLength:streamInfo.bufferStart + entry.unpaddedBufferSize]
				# print(f"stream header {i} vertex buffer size: {len(entry.vertexBuffer)}")
				# print(f"stream header {i} face buffer size: {len(entry.faceBuffer)}")
				# entry.faceBuffer = streamingBuffer[streamInfo.bufferStart+entry.vertexBufferLength:entry.nextBufferOffset]

				currentPos = file.tell()
				file.seek(self.streamingVertexElementOffset + (
							i * getPaddedPos(8 * self.mainVertexElementCount,
							                 16)))  # 8 is vertex element size

				# Batch read streaming vertex elements
				if self.mainVertexElementCount > 0:
					raw_elem_data = file.read(self.mainVertexElementCount * 8)
					entry.vertexElementList = np.frombuffer(raw_elem_data, dtype=VERTEX_ELEMENT_DTYPE).copy()
				file.seek(currentPos)
				# self.vertexBuffer.extend(vertexBytes)
				# self.faceBuffer.extend(faceBytes)

				self.streamingBufferHeaderList.append(entry)

		# print(f"vertex range {i} {streamInfo.bufferStart}:{streamInfo.bufferStart+entry.vertexBufferLength}")
		# print(f"face range {i} {streamInfo.bufferStart+entry.vertexBufferLength}:{streamInfo.bufferStart+entry.unpaddedBufferSize}")

		# print(f"current vertex buffer size {i} {len(self.vertexBuffer)}")
		# print(f"current face buffer size {i} {len(self.faceBuffer)}")

		self.vertexElementList = np.empty(0, dtype=VERTEX_ELEMENT_DTYPE)
		file.seek(self.vertexElementOffset)
		# Batch read all vertex elements at once (8 bytes each: u16 typing + u16 stride + u32 offset)
		if self.vertexElementCount > 0:
			raw_data = file.read(self.vertexElementCount * 8)
			self.vertexElementList = np.frombuffer(raw_data, dtype=VERTEX_ELEMENT_DTYPE).copy()

		file.seek(self.vertexBufferOffset)
		# print(f"Vertex buffer start {str(file.tell())}")
		# Preallocate and read in place to avoid an extra full-size copy of 100MB+ buffers
		self.vertexBuffer = bytearray(self.vertexBufferSize)
		read_vertex_len = file.readinto(self.vertexBuffer)
		if read_vertex_len < self.vertexBufferSize:
			del self.vertexBuffer[read_vertex_len:]
		# print(f"Vertex buffer end {str(file.tell())}")
		file.seek(self.faceBufferOffset)
		# print(f"Face buffer start {str(file.tell())}")
		self.faceBuffer = bytearray(self.faceBufferSize)
		read_face_len = file.readinto(self.faceBuffer)
		if read_face_len < self.faceBufferSize:
			del self.faceBuffer[read_face_len:]

		if self.sunbreakOffset != 0:
			if (version == VERSION_DD2 or version == VERSION_DD2NEW):
				# Limit this DD2 for now in case it happens to be used in other games for other things
				file.seek(self.sunbreakOffset)
				vertexCount = int(self.vertexElementList[1]['posStartOffset']) // 12  # Get amount of vertices from length of position buffer,pos data is 12 bytes
				self.secondaryWeightBuffer = file.read(vertexCount * 16)  # Weight data is 16 bytes
				print("Read DD2 secondary weight data")

	# print(f"full face buffer size {len(self.faceBuffer)}")
	# print(f"Face buffer end {str(file.tell())}")
	def write(self, file, version):
		file.write(struct.pack('<QQ', self.vertexElementOffset, self.vertexBufferOffset))
		if version < VERSION_SF6:
			file.write(struct.pack('<Q', self.faceBufferOffset))
			if version > VERSION_RE8:
				file.write(struct.pack('<Q', self.sunbreakOffset))
			file.write(struct.pack('<IIHHIIhh', self.vertexBufferSize, self.faceBufferSize,
				self.mainVertexElementCount, self.vertexElementCount,
				self.block2FaceBufferOffset, self.NULL,
				self.vertexElementSize, self.unkn1))
			if version > VERSION_RE8:
				file.write(struct.pack('<Q', self.sunbreakSecondUnknown))
		elif version >= VERSION_SF6:
			file.write(struct.pack('<QIIHH', self.sunbreakOffset, self.totalBufferSize,
				self.vertexBufferSize, self.mainVertexElementCount, self.vertexElementCount))
			if version >= VERSION_PRAG:
				file.write(struct.pack('<QQ', self.prag_unknOffset0, self.prag_unknOffset1))
			file.write(struct.pack('<IIhh', self.block2FaceBufferOffset, self.NULL,
				self.vertexElementSize, self.unkn1))
			file.write(struct.pack('<QQQQ', self.sunbreakSecondUnknown, self.sf6unkn0,
				self.streamingVertexElementOffset, self.sf6unkn2))

		# Batch write all vertex elements at once (8 bytes each: u16 typing + u16 stride + u32 offset)
		if len(self.vertexElementList) > 0:
			file.write(self.vertexElementList.tobytes())
		file.seek(getPaddedPos(file.tell(), 16))
		file.write(self.vertexBuffer)
		file.seek(getPaddedPos(file.tell(), 16))
		file.write(self.faceBuffer)
		if self.secondaryWeightBuffer != None:
			file.seek(getPaddedPos(file.tell(), 16))
			file.write(self.secondaryWeightBuffer)


class ContentFlag():  # Short bitflag in header that determines what content the mesh has Ex: Blend shapes, skeleton, etc.
	def __init__(self):
		self.bitFlag = 0
		self.hasUnknFlag16 = False
		self.hasUnknFlag10 = False
		self.hasUnknFlag8 = False  # Always true on MHR
		self.hasGroupPivot = False
		self.hasBlendShape = False
		self.hasSkeleton = False
		self.hasAABB = False

	def parseBitFlag(self):
		self.hasAABB = bool(getBit(self.bitFlag, 0))
		self.hasSkeleton = bool(getBit(self.bitFlag, 1))
		self.hasBlendShape = bool(getBit(self.bitFlag, 2))
		self.hasGroupPivot = bool(getBit(self.bitFlag, 3))
		self.hasUnknFlag8 = bool(getBit(self.bitFlag, 7))
		self.hasUnknFlag10 = bool(getBit(self.bitFlag, 9))
		self.hasUnknFlag16 = bool(getBit(self.bitFlag, 15))

	# print(f"aabb:{self.hasAABB}")
	# print(f"skeleton:{self.hasSkeleton}")
	# print(f"blendshape:{self.hasBlendShape}")
	# print(f"grouppivot:{self.hasGroupPivot}")
	def setBitFlag(self, hasUnknFlag16, hasUnknFlag10, hasUnknFlag8, hasGroupPivot, hasBlendShape,
	               hasSkeleton, hasAABB):
		self.bitFlag = 0
		if hasAABB:
			self.bitFlag = setBit(self.bitFlag, 0)
		if hasSkeleton:
			self.bitFlag = setBit(self.bitFlag, 1)
		if hasBlendShape:
			self.bitFlag = setBit(self.bitFlag, 2)
		if hasGroupPivot:
			self.bitFlag = setBit(self.bitFlag, 3)
		if hasUnknFlag8:
			self.bitFlag = setBit(self.bitFlag, 7)
		if hasUnknFlag10:
			self.bitFlag = setBit(self.bitFlag, 9)
		if hasUnknFlag16:
			self.bitFlag = setBit(self.bitFlag, 15)
		self.parseBitFlag()

	def read(self, file):
		self.bitFlag = struct.unpack_from('<H', file.read(2))[0]
		self.parseBitFlag()

	def write(self, file):
		file.write(struct.pack('<H', self.bitFlag))


class FileHeader():
	def __init__(self):
		self.magic = 1213416781
		self.version = 0
		self.fileSize = 0
		self.lodGroupNameHash = 0  # This determines what LOD distance scaling to use based on category of object
		self.contentFlag = ContentFlag()  # Bitflag 1000 XXXX-[GroupPivot/Floats][Blendshape][Skeleton][AABB]
		self.nameCount = 0
		self.unkn = 0
		self.meshGroupOffset = 0
		self.shadowMeshGroupOffset = 0
		self.occlusionMeshGroupOffset = 0
		self.skeletonOffset = 0
		self.normalRecalcOffset = 0
		self.blendShapesOffset = 0
		self.aabbOffset = 0
		self.meshOffset = 0
		self.floatsOffset = 0
		self.materialNameRemapOffset = 0
		self.boneNameRemapOffset = 0
		self.blendShapeNameOffset = 0
		self.nameOffsetsOffset = 0

		# SF6
		self.sf6UnknCount = 0
		self.sf6unkn0 = 0
		self.sf6unkn1 = 0
		self.streamingInfoOffset = 0
		self.sf6unkn3 = 0
		self.sf6unkn4 = 0

		# DD2
		self.dd2HashOffset = 0
		self.verticesOffset = 0

		# MHWilds
		# TODO Update offset calculation for wilds meshes
		# TODO Fix write for wilds changes
		self.wilds_unkn1 = 0  # TODO Clean these variables up and figure out if they're not actually new, just shifted
		self.wilds_unkn2 = 0
		self.wilds_unkn3 = 0
		self.wilds_unkn4 = 0
		self.wilds_unkn5 = 0
		self.streamingInfoOffset = 0

	def read(self, file, version):
		self.magic = read_uint(file)
		if self.magic != 1213416781:
			if self.magic == 1498173517:  # MPLY
				raise Exception("MPLY formatted mesh files (stage meshes mostly) are not supported yet.")
			else:
				raise Exception("File is not an RE mesh file.")
		self.version = read_uint(file)
		self.fileSize = read_uint(file)
		self.lodGroupNameHash = read_uint(file)

		if version < VERSION_SF6:
			self.contentFlag.read(file)
			# Batch read: short + uint + 13×uint64 = 2+4+104 = 110 bytes
			raw = file.read(110)
			off = 0
			self.nameCount, = struct.unpack_from('<h', raw, off); off += 2
			self.unkn, = struct.unpack_from('<I', raw, off); off += 4
			(self.meshGroupOffset, self.shadowMeshGroupOffset, self.occlusionMeshGroupOffset,
			 self.skeletonOffset, self.normalRecalcOffset, self.blendShapesOffset,
			 self.aabbOffset, self.meshOffset, self.floatsOffset,
			 self.materialNameRemapOffset, self.boneNameRemapOffset,
			 self.blendShapeNameOffset, self.nameOffsetsOffset) = struct.unpack_from('<13Q', raw, off)
		elif version >= VERSION_SF6 and version < VERSION_ONI2:
			self.contentFlag.read(file)
			# Buffer size depends on version: DD2+ has extra dd2HashOffset (8 bytes)
			# Pre-DD2: short×3(6) + uint×2(8) + uint64×13(104) + uint64×2(16) + uint64×2(16) = 150 bytes
			# DD2+:   short×3(6) + uint×2(8) + uint64×13(104) + uint64×3(24) + uint64×2(16) = 158 bytes
			bufSize = 158 if version >= VERSION_DD2 else 150
			raw = file.read(bufSize)
			off = 0
			self.sf6UnknCount, self.nameCount, self.sf6unkn3 = struct.unpack_from('<hhh', raw, off); off += 6
			self.unkn, self.sf6unkn0 = struct.unpack_from('<II', raw, off); off += 8
			(self.meshGroupOffset, self.shadowMeshGroupOffset, self.occlusionMeshGroupOffset,
			 self.normalRecalcOffset, self.blendShapesOffset, self.meshOffset,
			 self.sf6unkn1) = struct.unpack_from('<7Q', raw, off); off += 56

			self.floatsOffset, self.aabbOffset, self.skeletonOffset = struct.unpack_from('<3Q', raw, off); off += 24
			self.materialNameRemapOffset, self.boneNameRemapOffset = struct.unpack_from('<2Q', raw, off); off += 16
			self.blendShapeNameOffset, = struct.unpack_from('<Q', raw, off); off += 8

			if version < VERSION_DD2:
				self.streamingInfoOffset, self.nameOffsetsOffset = struct.unpack_from('<QQ', raw, off); off += 16
			else:
				self.nameOffsetsOffset, self.dd2HashOffset, self.streamingInfoOffset = struct.unpack_from('<QQQ', raw, off); off += 24
			self.verticesOffset, self.sf6unkn4 = struct.unpack_from('<QQ', raw, off)

		elif version >= VERSION_ONI2:
			# Batch read: uint + short + ushort + short + uint×3 + short + 17×uint64 = 24+136 = 160 bytes
			raw = file.read(160)
			off = 0
			self.wilds_unkn1, = struct.unpack_from('<I', raw, off); off += 4
			self.nameCount, = struct.unpack_from('<h', raw, off); off += 2
			# contentFlag is ushort at offset 6
			self.contentFlag.bitFlag, = struct.unpack_from('<H', raw, off); off += 2
			self.contentFlag.parseBitFlag()
			self.sf6UnknCount, = struct.unpack_from('<h', raw, off); off += 2
			self.wilds_unkn2, self.wilds_unkn3, self.wilds_unkn4 = struct.unpack_from('<III', raw, off); off += 12
			self.wilds_unkn5, = struct.unpack_from('<h', raw, off); off += 2
			# 15×uint64
			(self.verticesOffset, self.meshGroupOffset, self.shadowMeshGroupOffset,
			 self.occlusionMeshGroupOffset, self.normalRecalcOffset, self.blendShapesOffset,
			 self.meshOffset, self.sf6unkn1, self.floatsOffset, self.aabbOffset,
			 self.skeletonOffset, self.materialNameRemapOffset, self.boneNameRemapOffset,
			 self.blendShapeNameOffset, self.nameOffsetsOffset,
			 self.streamingInfoOffset, self.sf6unkn4) = struct.unpack_from('<17Q', raw, off)

	def write(self, file, version):
		write_uint(file, self.magic)
		write_uint(file, self.version)
		write_uint(file, self.fileSize)
		write_uint(file, self.lodGroupNameHash)

		if version < VERSION_SF6:
			self.contentFlag.write(file)
			# Batch write: short + uint + 13×uint64 = 110 bytes
			raw = struct.pack('<hI', self.nameCount, self.unkn)
			raw += struct.pack('<13Q', self.meshGroupOffset, self.shadowMeshGroupOffset,
				self.occlusionMeshGroupOffset, self.skeletonOffset, self.normalRecalcOffset,
				self.blendShapesOffset, self.aabbOffset, self.meshOffset, self.floatsOffset,
				self.materialNameRemapOffset, self.boneNameRemapOffset,
				self.blendShapeNameOffset, self.nameOffsetsOffset)
			file.write(raw)
		elif version >= VERSION_SF6 and version < VERSION_ONI2:
			self.contentFlag.write(file)
			# Batch write: short×3 + uint×2 = 14 bytes
			file.write(struct.pack('<hhhII', self.sf6UnknCount, self.nameCount, self.sf6unkn3,
				self.unkn, self.sf6unkn0))
			# Batch write: 13×uint64 = 104 bytes (meshGroupOffset through blendShapeNameOffset)
			file.write(struct.pack('<13Q', self.meshGroupOffset, self.shadowMeshGroupOffset,
				self.occlusionMeshGroupOffset, self.normalRecalcOffset, self.blendShapesOffset,
				self.meshOffset, self.sf6unkn1, self.floatsOffset, self.aabbOffset,
				self.skeletonOffset, self.materialNameRemapOffset, self.boneNameRemapOffset,
				self.blendShapeNameOffset))
			if version < VERSION_DD2:
				file.write(struct.pack('<QQ', self.streamingInfoOffset, self.nameOffsetsOffset))
			else:
				file.write(struct.pack('<QQQ', self.nameOffsetsOffset, self.dd2HashOffset, self.streamingInfoOffset))
			file.write(struct.pack('<QQ', self.verticesOffset, self.sf6unkn4))
		elif version >= VERSION_ONI2:
			# Batch write: uint + short + ushort + short + uint×3 + short + 17×uint64
			raw = struct.pack('<IhHhIIIh', self.wilds_unkn1, self.nameCount, self.contentFlag.bitFlag,
				self.sf6UnknCount, self.wilds_unkn2, self.wilds_unkn3, self.wilds_unkn4,
				self.wilds_unkn5)
			file.write(raw)
			file.write(struct.pack('<17Q', self.verticesOffset, self.meshGroupOffset,
				self.shadowMeshGroupOffset, self.occlusionMeshGroupOffset,
				self.normalRecalcOffset, self.blendShapesOffset, self.meshOffset,
				self.sf6unkn1, self.floatsOffset, self.aabbOffset, self.skeletonOffset,
				self.materialNameRemapOffset, self.boneNameRemapOffset,
				self.blendShapeNameOffset, self.nameOffsetsOffset,
				self.streamingInfoOffset, self.sf6unkn4))


class BlendSubMesh():
	def __init__(self):
		self.subMeshVertexStartIndex = 0
		self.vertOffset = 0
		self.vertCount = 0
		self.paramUnkn3 = 0

	def read(self, file):
		self.subMeshVertexStartIndex, self.vertOffset, self.vertCount, self.paramUnkn3 = struct.unpack_from('<4I', file.read(16))



class BlendTarget():
	def __init__(self):
		self.subMeshVertexStartIndex = 0
		self.vertCount = 0
		self.blendSSIndex = 0
		self.blendShapeNum = 0
		self.deltaOffset = 0

		# sf6 changes
		self.unkn0 = 0
		self.subMeshEntryCount = 0
		self.unkn2 = 0
		self.subMeshEntryOffset = 0
		self.subMeshEntryList = []

	def read(self, file, version):
		if version < VERSION_SF6:
			self.subMeshVertexStartIndex = read_uint(file)
			self.vertCount = read_uint(file)
			self.blendSSIndex = read_ushort(file)
			self.blendShapeNum = read_ushort(file)
			self.deltaOffset = read_uint(file)
		else:
			self.blendSSIndex = read_ushort(file)
			self.blendShapeNum = read_ushort(file)
			self.unkn0 = read_ushort(file)
			self.subMeshEntryCount = read_ubyte(file)
			self.unkn2 = read_ubyte(file)
			self.subMeshEntryOffset = read_uint64(file)
			currentPos = file.tell()
			file.seek(self.subMeshEntryOffset)
			for i in range(0, self.subMeshEntryCount):
				subMeshEntry = BlendSubMesh()
				subMeshEntry.read(file)
				self.subMeshEntryList.append(subMeshEntry)

			file.seek(currentPos)



class BlendShapeData():
	def __init__(self):
		self.targetCount = 1
		self.typing = 0
		self.unknFlag = 0
		self.padding1 = 0
		self.padding2 = 0
		self.dataOffset = 0  # [Target count]
		self.aabbOffset = 0
		self.blendSOffset = 0
		self.blendSSOffset = 0
		self.blendTargetList = []
		self.pragmataExtraTargets = []
		self.aabbList = [AABB()]
		self.blendS = [0, 0, 0, 0]
		self.blendSSList = []

	def read(self, file, version):
		if version < VERSION_SF6:
			# Legacy compact layout (RE8 and earlier, ported from REME v0.73):
			# header ends after aabbOffset - no blendSOffset/blendSSOffset (u64×2).
			raw = file.read(32)
			(self.targetCount, self.typing, self.unknFlag, self.padding1,
			 self.padding2) = struct.unpack_from('<HHIII', raw, 0)
			self.dataOffset, self.aabbOffset = struct.unpack_from('<QQ', raw, 16)
		else:
			# Batch read: ushort×2 + uint×3 + uint64×4 = 4+12+32 = 48 bytes
			raw = file.read(48)
			(self.targetCount, self.typing, self.unknFlag, self.padding1,
			 self.padding2) = struct.unpack_from('<HHIII', raw, 0)
			(self.dataOffset, self.aabbOffset, self.blendSOffset,
			 self.blendSSOffset) = struct.unpack_from('<QQQQ', raw, 16)
		file.seek(self.dataOffset)
		for i in range(0, self.targetCount):
			blendTargetEntry = BlendTarget()
			blendTargetEntry.read(file, version)
			self.blendTargetList.append(blendTargetEntry)
		# Pragmata typing-2 files reserve (targetCount + typing) BlendTarget slots. The extra
		# slots group non-morphing submeshes (teeth/eyes/leftover). Read them so export can
		# paste the table instead of reconstructing it from one character's vert counts.
		self.pragmataExtraTargets = []
		if version == VERSION_PRAG and self.typing:
			for i in range(0, self.typing):
				extraTarget = BlendTarget()
				extraTarget.read(file, version)
				self.pragmataExtraTargets.append(extraTarget)
		file.seek(self.aabbOffset)  # TODO FIX WRITE
		self.aabbList.clear()
		for i in range(0, self.targetCount):
			aabbEntry = AABB()
			aabbEntry.read(file)
			self.aabbList.append(aabbEntry)
		# Batch read blendS: 3×int
		blendSSize = sum(t.blendShapeNum for t in self.blendTargetList)
		raw = file.read(12 + blendSSize * 4)
		self.blendS = list(struct.unpack_from('<3i', raw, 0))
		if blendSSize > 0:
			self.blendSSList = list(struct.unpack_from(f'<{blendSSize}i', raw, 12))
		else:
			self.blendSSList = []



class BlendShapeHeader():
	def __init__(self):
		self.count = 0
		self.mainOffset = 0
		self.zero = 0
		self.hash = 0
		self.blendShapeOffsetList = []
		self.blendShapeList = []

	# TODO Blend shapes are different in wilds, fix

	def read(self, file, version):
		if version < VERSION_SF6:
			# Legacy compact layout (RE8 and earlier, ported from REME v0.73):
			# u8 count + skip 7 + u64×3 (mainOffset/zero/hash); the offset table
			# lives at mainOffset instead of directly after the header.
			self.count = read_ubyte(file)
			file.seek(7, 1)
			raw = file.read(24)
			self.mainOffset, self.zero, self.hash = struct.unpack_from('<QQQ', raw, 0)
		else:
			# Batch read: count + 3×uint64 = 32 bytes
			raw = file.read(32)
			self.count, = struct.unpack_from('<Q', raw, 0)
			if version < VERSION_ONI2:
				self.mainOffset, self.zero = struct.unpack_from('<QQ', raw, 8)
			else:
				self.zero, self.mainOffset = struct.unpack_from('<QQ', raw, 8)
			self.hash, = struct.unpack_from('<Q', raw, 24)
		if self.count > 0:
			if version < VERSION_SF6:
				file.seek(self.mainOffset)
			self.blendShapeOffsetList = read_uint64_array(file, int(self.count))
		else:
			self.blendShapeOffsetList = []
		self.blendShapeList = []
		currentPos = file.tell()
		for i in range(0, self.count):
			file.seek(self.blendShapeOffsetList[i])
			entry = BlendShapeData()
			entry.read(file, version)
			self.blendShapeList.append(entry)
		file.seek(currentPos)


def capturePragmataBlendHeader(bsData):
	"""Snapshot typing-2 BlendTarget grouping as JSON-friendly dicts.

	Retail files store extra padded target slots after ``targetCount``. Those slots
	hold the non-morphing submesh records (params, leftover grouping). Pasting this
	table on export works for any character; reconstructing it from vert counts does not.
	"""
	extra = getattr(bsData, "pragmataExtraTargets", None) or []
	subTable = []
	targetMeta = []
	for target in list(bsData.blendTargetList) + list(extra):
		subStart = len(subTable)
		for se in target.subMeshEntryList:
			subTable.append(
				[
					int(se.subMeshVertexStartIndex),
					int(se.vertOffset),
					int(se.vertCount),
					int(se.paramUnkn3),
				]
			)
		targetMeta.append(
			{
				"ssIndex": int(target.blendSSIndex),
				"nSh": int(target.blendShapeNum),
				"unkn0": int(target.unkn0),
				"subCount": int(target.subMeshEntryCount),
				"unkn2": int(target.unkn2),
				"subStart": subStart,
			}
		)
	return {
		"typing": int(getattr(bsData, "typing", 2) or 2),
		"padding1": int(getattr(bsData, "padding1", 1) or 0),
		"blendS": [int(x) for x in (getattr(bsData, "blendS", None) or [0, 0, 0])[:3]],
		"subTable": subTable,
		"targets": targetMeta,
	}


def _pragmataBlendShapeName(name):
	if not name:
		return name
	if name.startswith("Neutral_geo_cbs.") or "." in name:
		return name
	if name.startswith("crct_"):
		return "Neutral_geo_cbs." + name
	return name


def computeBlendShapeAABB(deltaArrayList):
	"""Return the symmetric (min = -max) AABB covering the per-axis extent of the given delta arrays.

	This is the box the 11/10/11 delta packing quantizes against.
	"""
	aabb = AABB()
	if not deltaArrayList:
		return aabb
	allDeltas = np.concatenate(
		[np.asarray(d, dtype=np.float64).reshape(-1, 3) for d in deltaArrayList], axis=0)
	if len(allDeltas) == 0:
		return aabb
	maxAbs = np.maximum(np.max(np.abs(allDeltas), axis=0), 1e-6)
	aabb.min.x, aabb.min.y, aabb.min.z = (-maxAbs[0], -maxAbs[1], -maxAbs[2])
	aabb.max.x, aabb.max.y, aabb.max.z = (maxAbs[0], maxAbs[1], maxAbs[2])
	return aabb


def remapPragmataBlendHeader(header, origStartToExportStart, morphExportStart, morphVertCount):
	"""Rewrite imported subTable start indices to the exported vertex buffer.

	Leftover records that span several submeshes keep their vertCount and only
	remap ``startIdx``. The first record is the morphing submesh.
	"""
	subTable = []
	for start, voff, count, param in header.get("subTable") or []:
		newStart = origStartToExportStart.get(int(start), int(start))
		subTable.append([int(newStart), int(voff or 0), int(count), int(param)])
	if subTable:
		subTable[0][0] = int(morphExportStart)
		subTable[0][2] = int(morphVertCount)
	return subTable


def fallbackPragmataBlendSubs(allSubs):
	"""Build a morph-first sub table when no imported header is on the .blend.

	Each non-morphing submesh gets ``param = matIdx << 8``. No leftover collapse.
	"""
	extraSubs = []
	morph = None
	for startIdx, vertCount, matIdx, hasShapes, sm in allSubs:
		if hasShapes:
			morph = (int(startIdx), 0, int(vertCount), 256)
			continue
		extraSubs.append(
			(int(startIdx), 0, int(vertCount), (int(matIdx) << 8) & 0xFFFFFFFF))
	targetMeta = [
		{"ssIndex": 0, "nSh": 0, "unkn0": 0, "subCount": 1, "unkn2": 1, "subStart": 0}
	]
	if extraSubs:
		targetMeta.append(
			{
				"ssIndex": 0,
				"nSh": 0,
				"unkn0": 0,
				"subCount": len(extraSubs),
				"unkn2": 0,
				"subStart": 1,
			}
		)
	return morph, extraSubs, targetMeta


def buildPragmataBlendShapeExport(parsedMesh, parsedSubMeshToSubMeshDataDict):
	"""Pack Pragmata face blendshapes as an 8-byte-per-vert tail (face submesh only).

	Unlike MH Wilds, retail Pragmata (`typing` 2) stores one target whose dense delta block covers
	only the morphing submesh. A topology aux table and u32 vert map sit in front of the float16
	deltas; MeshBufferHeader.sunbreakSecondUnknown points at those two streams.

	Extra-submesh grouping is pasted from the imported blend header when present.
	Without that snapshot, each non-morphing submesh is emitted as its own record.
	"""
	blendNames = []
	deltaChunks = []
	targets = []
	if not parsedMesh.mainMeshLODList:
		return None, None, None
	lod = parsedMesh.mainMeshLODList[0]
	allSubs = []  # (start, count, matIdx, hasShapes, sm)
	for viscon in lod.visconGroupList:
		for sm in viscon.subMeshList:
			shapes = getattr(sm, "blendShapeList", None)
			subData = parsedSubMeshToSubMeshDataDict.get(sm)
			startIdx = subData.vertexStartIndex if subData is not None else 0
			vertCount = len(sm.vertexPosList)
			matIdx = int(getattr(sm, "materialIndex", 0) or 0)
			allSubs.append((startIdx, vertCount, matIdx, bool(shapes), sm))

	morphingSubs = [entry for entry in allSubs if entry[3]]
	if len(morphingSubs) > 1:
		raiseError(
			"Pragmata typing-2 export supports exactly one morphing submesh, "
			f"found {len(morphingSubs)}. Remove shape keys from every other "
			"submesh before export."
		)

	extraSubs = []
	morphStart = morphCount = morphParam = None
	origStartToExportStart = {}
	for startIdx, vertCount, matIdx, hasShapes, sm in allSubs:
		orig = getattr(sm, "pragmataSrcStart", None)
		if orig is not None:
			origStartToExportStart[int(orig)] = int(startIdx)
		if hasShapes:
			morphStart, morphCount, morphParam = startIdx, vertCount, 256

	header = None
	auxInfo = getattr(parsedMesh, "pragmataBlendAux", None) or {}
	if isinstance(auxInfo.get("blendHeader"), dict):
		header = auxInfo["blendHeader"]
	targetMeta = None
	if header and header.get("subTable") and morphStart is not None:
		subTable = remapPragmataBlendHeader(
			header, origStartToExportStart, morphStart, morphCount)
		extraSubs = [tuple(s) for s in subTable[1:]]
		targetMeta = list(header.get("targets") or [])
		print(
			f"Pragmata blend header from .blend: {len(subTable)} sub records, "
			f"{len(targetMeta)} target slots"
		)
	else:
		_morphSub, extraSubs, targetMeta = fallbackPragmataBlendSubs(allSubs)
		if morphStart is None and _morphSub is not None:
			morphStart, morphCount, morphParam = (
				_morphSub[0],
				_morphSub[2],
				_morphSub[3],
			)
		raiseWarning(
			"Pragmata blend-header snapshot missing on the .blend; writing a generic "
			"morph-first sub table (no leftover collapse). Re-import the mesh to paste "
			"retail grouping."
		)

	for viscon in lod.visconGroupList:
		for sm in viscon.subMeshList:
			shapes = getattr(sm, "blendShapeList", None)
			if not shapes:
				continue
			subData = parsedSubMeshToSubMeshDataDict.get(sm)
			startIdx = subData.vertexStartIndex if subData is not None else 0
			vertCount = len(sm.vertexPosList)
			aabb = computeBlendShapeAABB([bs.deltas for bs in shapes])
			ssIndex = len(blendNames)
			for bs in shapes:
				bs.blendShapeName = _pragmataBlendShapeName(bs.blendShapeName)
				blendNames.append(bs.blendShapeName)
			# typing 2 stores IEEE float16 xyz deltas (4th half unused). Wilds 11/10/11 packing
			# in this slot is read as huge f16 values (~100-1000m), which swallows the camera.
			shapeBytes = []
			for bs in shapes:
				real = np.asarray(bs.deltas, dtype=np.float32).reshape(-1, 3)
				if len(real) != vertCount:
					padded = np.zeros((vertCount, 3), dtype=np.float32)
					n = min(len(real), vertCount)
					padded[:n] = real[:n]
					real = padded
				rec = np.zeros((vertCount, 4), dtype="<f2")
				rec[:, 0] = real[:, 0]
				rec[:, 1] = real[:, 1]
				rec[:, 2] = real[:, 2]
				shapeBytes.append(rec.tobytes())
			deltaChunks.append(b"".join(shapeBytes))
			matIdx = int(getattr(sm, "materialIndex", 0) or 0)
			targets.append(
				{
					"blendShapeNum": len(shapes),
					"subEntries3": [
						(startIdx, 0, vertCount, 256)
					],
					"extraSubs": extraSubs,
					"aabb": aabb,
					"blendSSIndex": ssIndex,
					"targetMeta": targetMeta,
				}
			)
			print(
				f"Pragmata blend export: {len(shapes)} shape(s) on submesh start={startIdx} "
				f"verts={vertCount} bytes={len(deltaChunks[-1])}"
			)
	if not targets:
		return None, None, None
	typingVal = 2
	padding1 = 1
	blendS = [0, 0, 0]
	if header:
		typingVal = int(header.get("typing") or 2)
		padding1 = int(header.get("padding1") or 1)
		blendS = list(header.get("blendS") or [0, 0, 0])[:3]
	perLodList = [
		{
			"targets": targets,
			"typing": typingVal,
			"padding1": padding1,
			"blendS": blendS,
			"targetMeta": targetMeta,
		}
	]
	return b"".join(deltaChunks), perLodList, blendNames


def serializePragmataBlendShapeRegion(perLodList, baseOffset):
	"""Serialize Pragmata typing-2 blend metadata in retail field order.

	Wilds writes BlendTargets immediately after BlendShapeData. Retail typing-2 files store the
	submesh table first (morphing submesh first, then extra grouped records), then
	targetCount+typing BlendTarget slots, then AABB / blendS (16-aligned) / blendSS. The game
	appears to walk that layout; using the Wilds order makes it read the mesh buffer header as
	vert counts and explode the head.
	"""
	lod = perLodList[0]
	targets = lod["targets"]
	t0 = targets[0]
	typingVal = int(lod.get("typing") or 2)
	nTargets = 1
	targetMeta = lod.get("targetMeta") or t0.get("targetMeta")
	paddedTargetCount = nTargets + typingVal
	if targetMeta:
		paddedTargetCount = max(paddedTargetCount, len(targetMeta))
	morphSub = tuple(t0["subEntries3"][0])
	extraSubs = [tuple(s) for s in (t0.get("extraSubs") or [])]
	subTable = [morphSub] + extraSubs
	nShapes = int(t0["blendShapeNum"])
	aabb = t0["aabb"]

	# Relative layout matching vanilla ch0100_10 (720-byte block).
	dataStructOff = 48
	subOff = 96
	targetListOff = subOff + 16 * len(subTable)
	aabbOff = targetListOff + 16 * paddedTargetCount
	blendSOff = aabbOff + 32 * nTargets
	blendSSOff = getPaddedPos(blendSOff + 12, 16)
	endOff = getPaddedPos(blendSSOff + nShapes * 4, 16)
	buf = bytearray(endOff)

	def abs_off(rel):
		return baseOffset + rel

	struct.pack_into("<Q", buf, 0, 1)  # count
	struct.pack_into("<Q", buf, 8, 0)  # zero
	struct.pack_into("<Q", buf, 16, abs_off(32))  # mainOffset -> offset list
	struct.pack_into("<Q", buf, 24, 0)  # hash
	struct.pack_into("<Q", buf, 32, abs_off(dataStructOff))

	d = dataStructOff
	struct.pack_into("<H", buf, d + 0, nTargets)
	struct.pack_into("<H", buf, d + 2, typingVal)
	struct.pack_into("<I", buf, d + 4, (nShapes << 16) | (int(t0.get("blendSSIndex", 0)) & 0xFFFF))
	struct.pack_into("<I", buf, d + 8, int(lod.get("padding1", 1)))
	struct.pack_into("<I", buf, d + 12, 0)
	struct.pack_into("<Q", buf, d + 16, abs_off(targetListOff))
	struct.pack_into("<Q", buf, d + 24, abs_off(aabbOff))
	struct.pack_into("<Q", buf, d + 32, abs_off(blendSOff))
	struct.pack_into("<Q", buf, d + 40, abs_off(blendSSOff))

	for i, se in enumerate(subTable):
		startIdx, vertOffset, vertCount = se[0], se[1], se[2]
		paramUnkn3 = se[3] if len(se) > 3 else 0
		o = subOff + i * 16
		struct.pack_into("<I", buf, o + 0, int(startIdx))
		struct.pack_into("<I", buf, o + 4, int(vertOffset or 0))
		struct.pack_into("<I", buf, o + 8, int(vertCount))
		struct.pack_into("<I", buf, o + 12, int(paramUnkn3))

	# Real morph target, then `typing` extra targets that group the non-morphing submeshes.
	# Vanilla ch0100_10: target1 has 4 entries (tooth/eyes/shell), target2 has the leftover record.
	def pack_target(slot, ssIndex, nSh, unkn0, subCount, unkn2, subRel):
		o = targetListOff + slot * 16
		struct.pack_into("<H", buf, o + 0, ssIndex)
		struct.pack_into("<H", buf, o + 2, nSh)
		struct.pack_into("<H", buf, o + 4, unkn0)
		struct.pack_into("<B", buf, o + 6, subCount)
		struct.pack_into("<B", buf, o + 7, unkn2)
		struct.pack_into("<Q", buf, o + 8, abs_off(subRel) if subCount else 0)

	pack_target(0, int(t0.get("blendSSIndex", 0)), nShapes, 0, 1, 1, subOff)
	if targetMeta and len(targetMeta) > 1:
		for slot, tm in enumerate(targetMeta[1:], start=1):
			pack_target(
				slot,
				int(tm.get("ssIndex") or 0),
				int(tm.get("nSh") or 0),
				int(tm.get("unkn0") or 0),
				int(tm.get("subCount") or 0),
				int(tm.get("unkn2") or 0),
				subOff + 16 * int(tm.get("subStart") or 0),
			)
	elif len(extraSubs) >= 5:
		pack_target(1, 0, 0, 0, 4, 0, subOff + 16)
		pack_target(2, 0, 0, 0, 1, 0, subOff + 16 * 5)
	elif extraSubs:
		pack_target(1, 0, 0, 0, len(extraSubs), 0, subOff + 16)
		pack_target(2, 0, 0, 0, 0, 0, 0)

	struct.pack_into("<4f", buf, aabbOff + 0, aabb.min.x, aabb.min.y, aabb.min.z, 0.0)
	struct.pack_into("<4f", buf, aabbOff + 16, aabb.max.x, aabb.max.y, aabb.max.z, 0.0)

	for k, v in enumerate((lod.get("blendS") or [0, 0, 0])[:3]):
		struct.pack_into("<i", buf, blendSOff + k * 4, int(v))
	for i in range(nShapes):
		struct.pack_into("<i", buf, blendSSOff + i * 4, i)

	print(
		f"Pragmata blend region: {endOff} bytes, {len(subTable)} sub records, "
		f"targetRel={targetListOff} subRel={subOff}"
	)
	return bytes(buf)


class BoneAABBGroup():
	def __init__(self):
		self.count = 0
		self.offset = 0
		self.bboxList = []

	# padding align 16

	def read(self, file):
		self.count = read_uint64(file)
		self.offset = read_uint64(file)
		self.bboxList = []
		if self.count > 0:
			# Batch read all AABBs at once: each AABB is 32 bytes (2×Vec4)
			raw = file.read(int(self.count) * 32)
			aabb_arr = np.frombuffer(raw, dtype=[('min', '<4f'), ('max', '<4f')])
			for i in range(len(aabb_arr)):
				entry = AABB()
				entry.min.x, entry.min.y, entry.min.z, entry.min.w = aabb_arr[i]['min']
				entry.max.x, entry.max.y, entry.max.z, entry.max.w = aabb_arr[i]['max']
				self.bboxList.append(entry)
		file.seek(getPaddedPos(file.tell(), 16))

	def write(self, file):
		write_uint64(file, self.count)
		write_uint64(file, self.offset)
		if self.bboxList:
			aabb_arr = np.empty((len(self.bboxList), 2, 4), dtype=np.float32)
			for i, entry in enumerate(self.bboxList):
				aabb_arr[i][0] = [entry.min.x, entry.min.y, entry.min.z, entry.min.w]
				aabb_arr[i][1] = [entry.max.x, entry.max.y, entry.max.z, entry.max.w]
			file.write(aabb_arr.tobytes())
		file.seek(getPaddedPos(file.tell(), 16))


class Bone():
	def __init__(self):
		self.boneIndex = 0
		self.boneParent = 0
		self.boneSibling = 0
		self.boneChild = 0
		self.boneSymmetric = 0
		self.useSecondaryWeight = 0
		self.padding0 = 0
		self.padding1 = 0

	def read(self, file):
		raw = file.read(16)
		(self.boneIndex, self.boneParent, self.boneSibling, self.boneChild,
		 self.boneSymmetric, self.useSecondaryWeight,
		 self.padding0, self.padding1) = struct.unpack_from('<HhHHHHhh', raw)

	def write(self, file):
		file.write(struct.pack('<HhHHHHhh', self.boneIndex, self.boneParent, self.boneSibling,
			self.boneChild, self.boneSymmetric, self.useSecondaryWeight,
			self.padding0, self.padding1))


class Skeleton():
	def __init__(self):
		self.boneCount = 0
		self.remapCount = 0
		self.NULL = 0
		self.boneHeaderOffset = 0
		self.boneLocalMatrixOffset = 0
		self.boneWorldMatrixOffset = 0
		self.boneInverseMatrixOffset = 0
		self.boneRemapList = []
		# padding align 16
		self.boneInfoList = []
		# padding align 16
		self.localMatList = []
		self.worldMatList = []
		self.inverseMatList = []

	def read(self, file):
		self.boneCount = read_uint(file)
		self.remapCount = read_uint(file)
		self.NULL = read_uint64(file)
		self.boneHeaderOffset = read_uint64(file)
		self.boneLocalMatrixOffset = read_uint64(file)
		self.boneWorldMatrixOffset = read_uint64(file)
		self.boneInverseMatrixOffset = read_uint64(file)
		# Batch read bone remap list
		if self.remapCount > 0:
			self.boneRemapList = read_ushort_array(file, self.remapCount)
		else:
			self.boneRemapList = []
		file.seek(getPaddedPos(file.tell(), 16))
		# Batch read bone info: each Bone is 16 bytes (8×ushort)
		self.boneInfoList = []
		if self.boneCount > 0:
			raw = file.read(self.boneCount * 16)
			bone_arr = np.frombuffer(raw, dtype=[('boneIndex', '<u2'), ('boneParent', '<i2'),
				('boneSibling', '<i2'), ('boneChild', '<i2'), ('boneSymmetric', '<i2'),
				('useSecondaryWeight', '<i2'), ('padding0', '<i2'), ('padding1', '<i2')])
			for i in range(self.boneCount):
				entry = Bone()
				entry.boneIndex = int(bone_arr[i]['boneIndex'])
				entry.boneParent = int(bone_arr[i]['boneParent'])
				entry.boneSibling = int(bone_arr[i]['boneSibling'])
				entry.boneChild = int(bone_arr[i]['boneChild'])
				entry.boneSymmetric = int(bone_arr[i]['boneSymmetric'])
				entry.useSecondaryWeight = int(bone_arr[i]['useSecondaryWeight'])
				entry.padding0 = int(bone_arr[i]['padding0'])
				entry.padding1 = int(bone_arr[i]['padding1'])
				self.boneInfoList.append(entry)
		file.seek(getPaddedPos(file.tell(), 16))
		# Batch read 3×boneCount matrices (each 64 bytes = 4×4 float32)
		self.localMatList = []
		self.worldMatList = []
		self.inverseMatList = []
		if self.boneCount > 0:
			mat_size = self.boneCount * 64
			raw_local = file.read(mat_size)
			raw_world = file.read(mat_size)
			raw_inverse = file.read(mat_size)
			local_arr = np.frombuffer(raw_local, dtype='<16f').reshape(-1, 4, 4)
			world_arr = np.frombuffer(raw_world, dtype='<16f').reshape(-1, 4, 4)
			inverse_arr = np.frombuffer(raw_inverse, dtype='<16f').reshape(-1, 4, 4)
			for i in range(self.boneCount):
				entry_l = Matrix4x4()
				entry_l.matrix = local_arr[i].tolist()
				self.localMatList.append(entry_l)
				entry_w = Matrix4x4()
				entry_w.matrix = world_arr[i].tolist()
				self.worldMatList.append(entry_w)
				entry_i = Matrix4x4()
				entry_i.matrix = inverse_arr[i].tolist()
				self.inverseMatList.append(entry_i)

	def write(self, file):
		write_uint(file, self.boneCount)
		write_uint(file, self.remapCount)
		write_uint64(file, self.NULL)
		write_uint64(file, self.boneHeaderOffset)
		write_uint64(file, self.boneLocalMatrixOffset)
		write_uint64(file, self.boneWorldMatrixOffset)
		write_uint64(file, self.boneInverseMatrixOffset)
		if self.boneRemapList:
			file.write(struct.pack(f'<{len(self.boneRemapList)}H', *self.boneRemapList))
		file.seek(getPaddedPos(file.tell(), 16))
		# Batch write bone info
		if self.boneInfoList:
			bone_arr = np.empty(len(self.boneInfoList), dtype=[('boneIndex', '<u2'), ('boneParent', '<i2'),
				('boneSibling', '<i2'), ('boneChild', '<i2'), ('boneSymmetric', '<i2'),
				('useSecondaryWeight', '<i2'), ('padding0', '<i2'), ('padding1', '<i2')])
			for i, entry in enumerate(self.boneInfoList):
				bone_arr[i] = (entry.boneIndex, entry.boneParent, entry.boneSibling, entry.boneChild,
					entry.boneSymmetric, entry.useSecondaryWeight, entry.padding0, entry.padding1)
			file.write(bone_arr.tobytes())
		# Batch write matrices
		for matList in (self.localMatList, self.worldMatList, self.inverseMatList):
			if matList:
				mat_arr = np.array([m.matrix for m in matList], dtype=np.float32)
				file.write(mat_arr.tobytes())


class FloatData():
	def __init__(self):
		self.bufferSize = 0
		self.offset = 0
		self.unknDataList = []

	def read(self, file):
		self.count = read_uint64(file)
		self.offset = read_uint64(file)
		self.unknDataList = []
		startPos = file.tell()
		file.seek(self.offset)
		bufCount = self.bufferSize // 12
		if bufCount > 0:
			raw = file.read(bufCount * 12)
			vdata = np.frombuffer(raw, dtype='<3f')
			for i in range(len(vdata)):
				entry = Vec3()
				entry.x, entry.y, entry.z = vdata[i]
				self.unknDataList.append(entry)
		file.seek(startPos)

	def write(self, file):
		write_uint64(file, self.count)
		write_uint64(file, self.offset)
		startPos = file.tell()
		file.seek(self.offset)
		if self.unknDataList:
			vdata = np.array([[e.x, e.y, e.z] for e in self.unknDataList], dtype=np.float32)
			file.write(vdata.tobytes())
		file.seek(startPos)


def checkWriteOffset(file, label, expected):
	"""Warn when the write cursor does not match the header offset being written.

	`expected` comes from the header; a mismatch means the offset math above is wrong.
	"""
	if expected and expected != file.tell():
		print(f"ERROR IN OFFSET CALCULATION - {label} - expected {expected}, actual {file.tell()}")

class REMesh():
	def __init__(self):
		self.meshVersion = 0
		self.isMPLY = False
		self.fileHeader = FileHeader()
		self.lodHeader = None
		self.shadowHeader = None
		self.occlusionHeader = None
		self.skeletonHeader = None
		self.normalRecalcHeader = None
		self.blendShapeHeader = None
		self.boneBoundingBoxHeader = None
		self.streamingInfoHeader = None  # WILDS
		self.streamingBuffer = None  # WILDS
		self.meshBufferHeader = None
		self.floatsHeader = None
		self.rawNameOffsetList = []
		self.rawNameList = ClampNameList()
		self.materialNameRemapList = []
		self.boneNameRemapList = []
		self.blendShapeNameRemapList = []
		self.normalRecalcRegionBytes = b""  # Pragmata 16-byte normal-recalc stub (pre-serialized)
		self.blendShapeRegionBytes = b""  # Pragmata typing-2 blend metadata region (pre-serialized)

	def read(self, file, version, lodTarget=None,
	         streamingBuffer=None):  # LOD target is an int that determines what lod level to import, the rest get ignored
		self.streamingBuffer = streamingBuffer
		if streamingBuffer != None:
			lodTarget = None  # Disable lod target optimization since all lods are needed
		self.fileHeader.read(file, version)

		if self.fileHeader.meshGroupOffset:
			file.seek(self.fileHeader.meshGroupOffset)
			self.lodHeader = MainMeshHeader()
			self.lodHeader.read(file, version, lodTarget)

		if self.fileHeader.shadowMeshGroupOffset and lodTarget == None:
			file.seek(self.fileHeader.shadowMeshGroupOffset)
			self.shadowHeader = ShadowHeader()
			self.shadowHeader.read(file, version)

		if self.fileHeader.occlusionMeshGroupOffset:  # Occlusion header is small, always read it regardless of lodTarget
			file.seek(self.fileHeader.occlusionMeshGroupOffset)
			self.occlusionHeader = LODGroupHeader()
			self.occlusionHeader.read(file, version)

		if self.fileHeader.skeletonOffset:
			file.seek(self.fileHeader.skeletonOffset)
			self.skeletonHeader = Skeleton()
			self.skeletonHeader.read(file)
		# TODO - Normal recalc is changed or offset is different in mhwilds
		# Pragmata typing-2 meshes always read their blend shape header; other games stay gated by the flag.
		if self.fileHeader.blendShapesOffset and (IMPORT_BLEND_SHAPES or self.meshVersion in PRAGMATA_BLEND_SHAPE_FILE_VERSIONS):
			file.seek(self.fileHeader.blendShapesOffset)
			self.blendShapeHeader = BlendShapeHeader()
			self.blendShapeHeader.read(file, version)

		if self.fileHeader.aabbOffset:
			file.seek(self.fileHeader.aabbOffset)
			self.boneBoundingBoxHeader = BoneAABBGroup()
			self.boneBoundingBoxHeader.read(file)

		if version >= VERSION_SF6:
			if self.fileHeader.streamingInfoOffset:
				file.seek(self.fileHeader.streamingInfoOffset)
				self.streamingInfoHeader = StreamingInfo()
				self.streamingInfoHeader.read(file)
				if self.streamingInfoHeader.entryCount != 0 and streamingBuffer == None:
					raiseError(
						"Streaming mesh file is missing. Both mesh files are required. Extract the corresponding mesh file from inside the streaming directory.\n\nExample Mesh Path: natives\\STM\\Art\\Model\\Character\\ch02\\007\\000\\1\\ch02_007_0001.mesh.241111606\nExample Streaming Mesh Path: natives\\STM\\streaming\\Art\\Model\\Character\\ch02\\007\\000\\1\\ch02_007_0001.mesh.241111606")
		if self.fileHeader.meshOffset:
			file.seek(self.fileHeader.meshOffset)
			self.meshBufferHeader = MeshBufferHeader()
			self.meshBufferHeader.read(file, version, self.streamingInfoHeader, streamingBuffer)

		if self.fileHeader.floatsOffset:
			file.seek(self.fileHeader.floatsOffset)
			self.floatsHeader = FloatData()
			self.floatsHeader.read(file)

		if self.fileHeader.nameOffsetsOffset:
			file.seek(self.fileHeader.nameOffsetsOffset)
			if self.fileHeader.nameCount > 0:
				raw_data = file.read(self.fileHeader.nameCount * 8)
				self.rawNameOffsetList = list(struct.unpack(f'<{self.fileHeader.nameCount}Q', raw_data))

				# Batch read: load the string region in one shot and split by null
				# terminators (shared with the MPLY parser). The region is bounded by
				# the next section offset so a mid-file table doesn't slurp the rest
				# of the file (previously this read straight to EOF).
				self.rawNameList = read_null_terminated_names(
					file, self.rawNameOffsetList,
					(self.fileHeader.meshOffset, self.fileHeader.floatsOffset,
					 self.fileHeader.materialNameRemapOffset, self.fileHeader.boneNameRemapOffset,
					 self.fileHeader.blendShapeNameOffset, self.fileHeader.streamingInfoOffset))

		if self.fileHeader.materialNameRemapOffset and self.lodHeader != None:
			file.seek(self.fileHeader.materialNameRemapOffset)
			# Batch-read ushort remap entries with numpy (avoids Python loop)
			if self.lodHeader.materialCount > 0:
				raw_data = file.read(self.lodHeader.materialCount * 2)
				self.materialNameRemapList = list(
					struct.unpack(f'<{self.lodHeader.materialCount}H', raw_data))

		if self.fileHeader.boneNameRemapOffset and self.skeletonHeader != None:
			file.seek(self.fileHeader.boneNameRemapOffset)
			# Batch-read ushort remap entries with numpy (avoids Python loop)
			if self.skeletonHeader.boneCount > 0:
				raw_data = file.read(self.skeletonHeader.boneCount * 2)
				self.boneNameRemapList = list(struct.unpack(f'<{self.skeletonHeader.boneCount}H', raw_data))

		if self.fileHeader.blendShapeNameOffset and self.blendShapeHeader != None:
			file.seek(self.fileHeader.blendShapeNameOffset)
			blendNameCount = self.fileHeader.nameCount - len(self.materialNameRemapList) - len(
				self.boneNameRemapList)
			# Batch-read remaining ushort remap entries with numpy (avoids Python loop)
			if blendNameCount > 0:
				raw_data = file.read(blendNameCount * 2)
				self.blendShapeNameRemapList = list(struct.unpack(f'<{blendNameCount}H', raw_data))

	def write(self, file, version):
		self.fileHeader.write(file, version)

		if self.fileHeader.meshGroupOffset:
			checkWriteOffset(file, "meshGroupOffset", self.fileHeader.meshGroupOffset)
			self.lodHeader.write(file, version)

		if self.fileHeader.shadowMeshGroupOffset:
			checkWriteOffset(file, "shadowMeshGroupOffset", self.fileHeader.shadowMeshGroupOffset)
			self.shadowHeader.write(file, version)

		if self.fileHeader.skeletonOffset:
			checkWriteOffset(file, "skeletonOffset", self.fileHeader.skeletonOffset)
			self.skeletonHeader.write(file)

		checkWriteOffset(file, "materialNameRemapOffset", self.fileHeader.materialNameRemapOffset)
		# Batch write material remap table (single struct.pack instead of per-entry write_ushort)
		if self.materialNameRemapList:
			file.write(struct.pack(f'<{len(self.materialNameRemapList)}H', *self.materialNameRemapList))

		file.seek(getPaddedPos(file.tell(), 16))
		checkWriteOffset(file, "boneNameRemapOffset", self.fileHeader.boneNameRemapOffset)
		# Batch write bone remap table
		if self.boneNameRemapList:
			file.write(struct.pack(f'<{len(self.boneNameRemapList)}H', *self.boneNameRemapList))

		file.seek(getPaddedPos(file.tell(), 16))
		checkWriteOffset(file, "blendShapeNameOffset", self.fileHeader.blendShapeNameOffset)
		# Batch write blend shape remap table
		if self.blendShapeNameRemapList:
			file.write(struct.pack(f'<{len(self.blendShapeNameRemapList)}H', *self.blendShapeNameRemapList))

		file.seek(getPaddedPos(file.tell(), 16))

		checkWriteOffset(file, "nameOffsetsOffset", self.fileHeader.nameOffsetsOffset)

		# Batch write name offset list (single struct.pack instead of per-entry write_uint64)
		if self.rawNameOffsetList:
			file.write(struct.pack(f'<{len(self.rawNameOffsetList)}Q', *self.rawNameOffsetList))
		file.seek(getPaddedPos(file.tell(), 16))

		# Batch write all strings as a single bytes block
		if self.rawNameList:
			file.write(b''.join(name.encode('utf-8') + b'\x00' for name in self.rawNameList))

		file.seek(getPaddedPos(file.tell(), 16))

		if self.fileHeader.aabbOffset:
			checkWriteOffset(file, "aabbOffset", self.fileHeader.aabbOffset)
			self.boneBoundingBoxHeader.write(file)

		# Pragmata normal-recalc stub (pre-serialized in ParsedREMeshToREMesh).
		if self.fileHeader.normalRecalcOffset and self.normalRecalcRegionBytes:
			checkWriteOffset(file, "normalRecalcOffset", self.fileHeader.normalRecalcOffset)
			file.write(self.normalRecalcRegionBytes)
			file.write(b'\x00' * getPaddingAmount(file.tell(), 16))

		# Pragmata typing-2 blend shape struct region (pre-serialized in ParsedREMeshToREMesh).
		if self.fileHeader.blendShapesOffset and self.blendShapeRegionBytes:
			checkWriteOffset(file, "blendShapesOffset", self.fileHeader.blendShapesOffset)
			file.write(self.blendShapeRegionBytes)
			file.write(b'\x00' * getPaddingAmount(file.tell(), 16))

		if self.fileHeader.meshOffset:
			checkWriteOffset(file, "meshOffset", self.fileHeader.meshOffset)
			self.meshBufferHeader.write(file, version)

		file.write(b'\x00' * getPaddingAmount(file.tell(), 16))  # Write end of file padding
		if self.fileHeader.fileSize != file.tell():
			print(
				f"ERROR IN OFFSET CALCULATION - fileSize - expected {self.fileHeader.fileSize}, actual {file.tell()}")


# List to buffer conversions

def WriteToVertexPosBuffer(bufferStream, vertexPosList):
	# NumPy tobytes: avoids struct.pack + chain.from_iterable (no intermediate Python tuple)
	vertexPosArray = np.asarray(vertexPosList, dtype=np.float32, order='C')
	bufferStream.write(vertexPosArray.tobytes())


def WriteToNorTanBuffer(bufferStream, normalArray, tangentArray):
	vertexCount = len(normalArray)
	norTanArray = np.empty((vertexCount * 2, 4), dtype=np.int8)
	normals = np.asarray(normalArray, dtype=np.float32)
	norTanArray[::2, :3] = np.floor(normals * 127).astype(np.int8)
	norTanArray[::2, 3] = 0
	norTanArray[1::2] = np.asarray(tangentArray).astype(np.int8, copy=False)
	bufferStream.write(norTanArray.tobytes())


def WriteToUVBuffer(bufferStream, uvList):
	uvArray = np.array(uvList, dtype=np.dtype("<e"))
	uvArray = uvArray.flatten()
	uvArray[1::2] = 1 - uvArray[1::2]
	bufferStream.write(uvArray.tobytes())


def _normalizeWeights(boneWeightsArray):
	weightSums = np.sum(boneWeightsArray, axis=1, dtype=np.float32, keepdims=True)
	weightSums[weightSums == 0] = 1.0
	boneWeightsArray = np.round(boneWeightsArray / weightSums * 255)
	diffSums = 255.0 - np.sum(boneWeightsArray, axis=1, dtype=np.float32)
	boneWeightsArray[np.arange(boneWeightsArray.shape[0]), np.argmax(boneWeightsArray, axis=1)] += diffSums
	boneWeightsArray = boneWeightsArray.astype("<B")
	if (255 - np.sum(boneWeightsArray, axis=1, dtype=np.int32) != 0).any():
		raiseWarning("Non normalized weights detected on sub mesh! Weights may not behave as expected in game!")
	return boneWeightsArray


def _packIndices(boneIndicesList, isSixWeight, indexPad=0):
	if isSixWeight:
		indexPad &= 3
		indices = np.asarray(boneIndicesList, dtype=np.uint64)
		uint64Array = (
				(indices[:, 0] & 0x3FF) |
				((indices[:, 1] & 0x3FF) << 10) |
				((indices[:, 2] & 0x3FF) << 20) |
				((indexPad) << 30) |
				((indices[:, 3] & 0x3FF) << 32) |
				((indices[:, 4] & 0x3FF) << 42) |
				((indices[:, 5] & 0x3FF) << 52) |
				((indexPad) << 62)
		)
		return np.ascontiguousarray(uint64Array).view(dtype="<B").reshape(-1, 8)
	return np.asarray(boneIndicesList, dtype="<B")


def WriteToWeightBuffer(bufferStream, boneWeightsList, boneIndicesList, isSixWeight, indexPad=0):
	boneIndicesArray = _packIndices(boneIndicesList, isSixWeight, indexPad)
	boneWeightsArray = np.array(boneWeightsList, dtype=np.float32)
	boneWeightsArray = _normalizeWeights(boneWeightsArray)
	weightArray = np.empty((len(boneWeightsList) * 2, 8), dtype=np.dtype("<B"))
	weightArray[::2] = boneIndicesArray
	weightArray[1::2] = boneWeightsArray
	bufferStream.write(weightArray.tobytes())


def WriteToWeightBufferExtended(bufferStream, boneWeightsList, boneIndicesList, extraBufferStream,
                                extraBoneWeightsList, extraBoneIndicesList, isSixWeight, indexPad=0):
	boneIndicesArray = _packIndices(boneIndicesList, isSixWeight, indexPad)
	extraBoneIndicesArray = _packIndices(extraBoneIndicesList, isSixWeight)

	boneWeightsArray = np.array(boneWeightsList, dtype=np.float32)
	boneWeightsArray = np.hstack((boneWeightsArray, np.array(extraBoneWeightsList, dtype=np.float32)))
	boneWeightsArray = _normalizeWeights(boneWeightsArray)

	weightArray = np.empty((len(boneWeightsList) * 2, 8), dtype=np.dtype("<B"))
	weightArray[::2] = boneIndicesArray
	weightArray[1::2] = boneWeightsArray[:, :8]
	bufferStream.write(weightArray.tobytes())

	extraWeightArray = np.empty((len(extraBoneWeightsList) * 2, 8), dtype=np.dtype("<B"))
	extraWeightArray[::2] = extraBoneIndicesArray
	extraWeightArray[1::2] = boneWeightsArray[:, 8:]
	extraBufferStream.write(extraWeightArray.tobytes())


def WriteToColorBuffer(bufferStream, colorList):
	colorArray = np.array(colorList, dtype=np.float32)
	colorArray = (colorArray * 255).astype(dtype=">B")
	bufferStream.write(colorArray.tobytes())


def WriteToFaceBuffer(bufferStream, faceList):
	faceArray = np.asarray(faceList, dtype=np.uint16)
	data = faceArray.tobytes()
	if (len(data)) % 4 != 0:  # Align face buffer to 4 bytes per submesh
		data += b'\x00\x00'
	bufferStream.write(data)


def WriteToIntFaceBuffer(bufferStream, faceList):
	faceArray = np.asarray(faceList, dtype=np.uint32)
	bufferStream.write(faceArray.tobytes())


class sizeData:
	def __init__(self, version):
		self.MESH_HEADER_SIZE = 128
		if version >= VERSION_SF6:
			self.MESH_HEADER_SIZE = 168
		if version >= VERSION_DD2:
			self.MESH_HEADER_SIZE = 176
		self.LOD_HEADER_OFFSET_LIST_OFFSET = 64  # Offset from start of lod header to offset list
		self.LOD_GROUP_HEADER_OFFSET_LIST_OFFSET = 16  # Offset from start of lod group to offset list
		self.MESH_GROUP_SIZE = 16
		self.MATERIAL_SUBDIVISION_SIZE = 24
		self.SKELETON_REMAP_TABLE_OFFSET = 48
		self.BONE_INFO_ENTRY_SIZE = 16
		self.MATRIX_SIZE = 64
		self.AABB_OFFSET = 16
		self.AABB_SIZE = 32
		self.VERTEX_ELEMENT_OFFSET = 64
		self.STREAMING_HEADER_SIZE = 16  # WILDS
		if version < VERSION_RE8:
			self.LOD_HEADER_OFFSET_LIST_OFFSET = 72
			self.MATERIAL_SUBDIVISION_SIZE = 16

		if version <= VERSION_RE8:
			self.VERTEX_ELEMENT_OFFSET = 48
		if version >= VERSION_SF6:
			self.VERTEX_ELEMENT_OFFSET = 80

		if version >= VERSION_DD2NEW:
			self.MATERIAL_SUBDIVISION_SIZE = 28

		if version >= VERSION_DR:
			self.MATERIAL_SUBDIVISION_SIZE = 32

		if version >= VERSION_PRAG:
			self.VERTEX_ELEMENT_OFFSET = 96

		self.VERTEX_ELEMENT_SIZE = 8


def ParsedREMeshToREMesh(parsedMesh, meshVersion):
	print(f"Mesh Version:{meshVersion}")
	version = meshFileVersionToNewVersionDict.get(meshVersion, getNearestRemapVersion(meshVersion))
	print(f"Remapped Version:{version}")
	sd = sizeData(version)
	currentOffset = 0
	currentVertexIndex = 0
	currentFaceIndex = 0

	totalTangentGenerationTime = 0.0 # For benchmarking the time it takes tangents to calculate

	# Buffers
	vertexPosBuffer = BytesIO()
	norTanBuffer = BytesIO()
	UVBuffer = BytesIO()
	UV2Buffer = BytesIO()
	weightBuffer = BytesIO()
	colorBuffer = BytesIO()
	faceBuffer = BytesIO()
	extraWeightBuffer = BytesIO()  # MH Wilds extended weight buffer
	secondaryWeightBuffer = BytesIO()  # DD2 shapekey

	parsedSubMeshToSubMeshDataDict = dict()

	reMesh = REMesh()

	reMesh.fileHeader.version = meshFileVersionToInternalVersionDict.get(
		meshVersion,
		getNearestRemapVersion(meshVersion)
	)
	# TODO Fix shadow mesh export, causes game to crash. It seems shadow meshes can't have unique lods, even if the sub mesh offsets are still shared. They might only be able to use the existing full lods from the main mesh
	# parsedMesh.shadowMeshLODList.clear()

	# Main Meshes
	if parsedMesh.mainMeshLODList != []:
		reMesh.fileHeader.meshGroupOffset = sd.MESH_HEADER_SIZE
		reMesh.lodHeader = MainMeshHeader()
		reMesh.lodHeader.lodGroupCount = len(parsedMesh.mainMeshLODList)
		reMesh.lodHeader.materialCount = len(parsedMesh.materialNameList)
		reMesh.lodHeader.bbox = parsedMesh.boundingBox
		reMesh.lodHeader.sphere = parsedMesh.boundingSphere
		for viscon in parsedMesh.mainMeshLODList[0].visconGroupList:
			reMesh.lodHeader.totalMeshCount += len(viscon.subMeshList)
		reMesh.lodHeader.skinWeightCount = 18
		if version == VERSION_SF6:
			reMesh.lodHeader.skinWeightCount = 9
		elif version == VERSION_MHWILDS:
			reMesh.lodHeader.skinWeightCount = 25  # Not sure why but this fixes monsters causing crashes and dead hitbox issues
		elif version == VERSION_PRAG:
			reMesh.lodHeader.skinWeightCount = 27  #
		elif version == VERSION_RE9:
			reMesh.lodHeader.skinWeightCount = 18  #
		if parsedMesh.bufferHasUV2:  # This is wrong, uv count is determined by something else. However uv count is unused by the game so it doesn't really matter
			reMesh.lodHeader.uvCount = 2
		else:
			reMesh.lodHeader.uvCount = 1
		if parsedMesh.bufferHasIntFaces:
			reMesh.lodHeader.has32BitIndexBuffer = 1
		reMesh.lodHeader.offsetOffset = sd.MESH_HEADER_SIZE + sd.LOD_HEADER_OFFSET_LIST_OFFSET

		# currentOffset = LOD Group 0 offset
		currentOffset = reMesh.lodHeader.offsetOffset + 8 * reMesh.lodHeader.lodGroupCount + getPaddingAmount(
			reMesh.lodHeader.offsetOffset + (8 * reMesh.lodHeader.lodGroupCount), 16)

		# SF6 uses six weights with higher possible bone index values
		isSixWeight = version in SIX_WEIGHT_GAMES
		# Retail Pragmata sets the 2-bit pads in the 6-weight index pack to 0b11.
		weightIndexPad = 3 if version == VERSION_PRAG else 0

		# Main Meshes
		# TODO Move lod parsing into a function and call it for both main and shadow mesh
		for lod in parsedMesh.mainMeshLODList:
			reMesh.lodHeader.lodGroupOffsetList.append(currentOffset)
			lodGroupHeader = LODGroupHeader()
			lodGroupHeader.count = len(lod.visconGroupList)
			lodGroupHeader.distance = lod.lodDistance
			currentOffset += sd.LOD_GROUP_HEADER_OFFSET_LIST_OFFSET
			lodGroupHeader.offsetOffset = currentOffset
			# Viscon 0 Offset
			currentOffset = lodGroupHeader.offsetOffset + 8 * lodGroupHeader.count + getPaddingAmount(
				lodGroupHeader.offsetOffset + (8 * lodGroupHeader.count), 16)
			for viscon in lod.visconGroupList:
				lodGroupHeader.meshGroupOffsetList.append(currentOffset)
				# print(f"viscon {viscon.visconGroupNum} offset: {str(currentOffset)}")
				meshGroup = MeshGroup()
				meshGroup.visconGroupID = viscon.visconGroupNum
				meshGroup.meshCount = len(viscon.subMeshList)
				for parsedSubMesh in viscon.subMeshList:
					subMesh = MaterialSubdivision()
					subMesh.materialIndex = parsedSubMesh.materialIndex
					subMesh.faceCount = len(parsedSubMesh.faceList) * 3
					if parsedMesh.bufferHasIntFaces:
						paddedFaceCount = subMesh.faceCount
					else:
						paddedFaceCount = getPaddedPos(subMesh.faceCount, 2)
					meshGroup.faceCount += paddedFaceCount

					vertCount = len(parsedSubMesh.vertexPosList)
					meshGroup.vertexCount += vertCount
					parsedSubMeshToSubMeshDataDict[parsedSubMesh] = subMesh
					if not parsedSubMesh.isReusedMesh:
						subMesh.faceStartIndex = currentFaceIndex
						subMesh.vertexStartIndex = currentVertexIndex
						currentVertexIndex += vertCount
						currentFaceIndex += paddedFaceCount
						# TODO Add vertices and faces to buffers
						WriteToVertexPosBuffer(vertexPosBuffer, parsedSubMesh.vertexPosList)

						tangentGenerationStartTime = time.time()
						WriteToNorTanBuffer(norTanBuffer, parsedSubMesh.normalList, parsedSubMesh.tangentList)
						totalTangentGenerationTime +=  (time.time() - tangentGenerationStartTime)

						# Copy uv1 to uv2 if buffer has uv2, but the mesh only has 1 uv
						if parsedMesh.bufferHasUV2 and parsedSubMesh.uv2List is None:
							parsedSubMesh.uv2List = parsedSubMesh.uvList

						WriteToUVBuffer(UVBuffer, parsedSubMesh.uvList)
						if parsedSubMesh.uv2List is not None:
							WriteToUVBuffer(UV2Buffer, parsedSubMesh.uv2List)

						if len(parsedSubMesh.weightIndicesList) != 0 and len(
								parsedSubMesh.weightIndicesList) == len(parsedSubMesh.weightList):
							if parsedMesh.bufferHasExtraWeight and len(
									parsedSubMesh.extraWeightIndicesList) != 0 and len(
									parsedSubMesh.extraWeightIndicesList) == len(
									parsedSubMesh.extraWeightList):
								WriteToWeightBufferExtended(weightBuffer, parsedSubMesh.weightList,
								                            parsedSubMesh.weightIndicesList,
								                            extraWeightBuffer, parsedSubMesh.extraWeightList,
								                            parsedSubMesh.extraWeightIndicesList, isSixWeight,
								                            indexPad=weightIndexPad)
							else:
								WriteToWeightBuffer(weightBuffer, parsedSubMesh.weightList,
								                    parsedSubMesh.weightIndicesList, isSixWeight,
								                    indexPad=weightIndexPad)

						# DD2 shapekeys
						if len(parsedSubMesh.secondaryWeightIndicesList) != 0 and len(
								parsedSubMesh.secondaryWeightIndicesList) == len(
								parsedSubMesh.secondaryWeightList):
							WriteToWeightBuffer(secondaryWeightBuffer, parsedSubMesh.secondaryWeightList,
							                    parsedSubMesh.secondaryWeightIndicesList, isSixWeight)

						# Add vertex color if it's missing and other meshes have it (vectorized)
						if parsedMesh.bufferHasColor and parsedSubMesh.colorList is None:
							n_v = len(parsedSubMesh.vertexPosList)
							parsedSubMesh.colorList = np.full((n_v, 4), 255, dtype=np.float32)

						if parsedSubMesh.colorList is not None:
							WriteToColorBuffer(colorBuffer, parsedSubMesh.colorList)
						if parsedMesh.bufferHasIntFaces:
							WriteToIntFaceBuffer(faceBuffer, parsedSubMesh.faceList)
						else:
							WriteToFaceBuffer(faceBuffer, parsedSubMesh.faceList)
					else:
						linkedMeshData = parsedSubMeshToSubMeshDataDict[parsedSubMesh.linkedSubMesh]
						subMesh.faceStartIndex = linkedMeshData.faceStartIndex
						subMesh.vertexStartIndex = linkedMeshData.vertexStartIndex
					# TODO Get linked mesh offset for reused meshes
					# Make dict of offset key to tuple of vertexstartindex and facestartindex
					# meshOffsetDict[parsedSubMesh.linkedMesh][0]
					meshGroup.vertexInfoList.append(subMesh)
				currentOffset += sd.MESH_GROUP_SIZE + meshGroup.meshCount * sd.MATERIAL_SUBDIVISION_SIZE

				lodGroupHeader.meshGroupList.append(meshGroup)
			reMesh.lodHeader.lodGroupList.append(lodGroupHeader)
	print(f"Tangent calculation took {formatMs(totalTangentGenerationTime)} ms.")
	# Shadow Meshes

	if parsedMesh.shadowMeshLinkedLODList != []:
		reMesh.fileHeader.shadowMeshGroupOffset = currentOffset
		reMesh.shadowHeader = ShadowHeader()
		reMesh.shadowHeader.skinWeightCount = 18
		reMesh.shadowHeader.lodGroupCount = len(parsedMesh.shadowMeshLinkedLODList)
		reMesh.shadowHeader.materialCount = reMesh.lodHeader.materialCount
		reMesh.shadowHeader.totalMeshCount = reMesh.lodHeader.totalMeshCount

		if parsedMesh.bufferHasUV2:
			reMesh.shadowHeader.uvCount = 2
		else:
			reMesh.shadowHeader.uvCount = 1
		reMesh.shadowHeader.offsetOffset = reMesh.fileHeader.shadowMeshGroupOffset + sd.LOD_HEADER_OFFSET_LIST_OFFSET

		for linkedLOD in parsedMesh.shadowMeshLinkedLODList:
			mainMeshLODIndex = parsedMesh.mainMeshLODList.index(linkedLOD)
			reMesh.shadowHeader.lodGroupOffsetList.append(
				reMesh.lodHeader.lodGroupOffsetList[mainMeshLODIndex])

		# currentOffset = LOD Group 0 offset
		currentOffset = getPaddedPos(reMesh.shadowHeader.offsetOffset + 8 * reMesh.shadowHeader.lodGroupCount, 16)
	# It turns out shadow meshes can only use existing lods from the main mesh so this was pointless
	# Skeleton / AABB
	if parsedMesh.skeleton != None:
		reMesh.fileHeader.skeletonOffset = currentOffset
		reMesh.skeletonHeader = Skeleton()
		reMesh.skeletonHeader.boneCount = len(parsedMesh.skeleton.boneList)
		reMesh.skeletonHeader.remapCount = len(parsedMesh.skeleton.weightedBones)

		# Do AABB struct while looping through bones
		if reMesh.skeletonHeader.remapCount > 0:
			reMesh.boneBoundingBoxHeader = BoneAABBGroup()
			reMesh.boneBoundingBoxHeader.count = reMesh.skeletonHeader.remapCount
		weightedBoneNameSet = set(parsedMesh.skeleton.weightedBones)
		for boneIndex, parsedBone in enumerate(parsedMesh.skeleton.boneList):
			if parsedBone.boneName in weightedBoneNameSet:
				reMesh.skeletonHeader.boneRemapList.append(boneIndex)
				if parsedBone.boundingBox != None and reMesh.boneBoundingBoxHeader != None:
					reMesh.boneBoundingBoxHeader.bboxList.append(parsedBone.boundingBox)
			reMesh.skeletonHeader.localMatList.append(parsedBone.localMatrix)
			reMesh.skeletonHeader.worldMatList.append(parsedBone.worldMatrix)
			reMesh.skeletonHeader.inverseMatList.append(parsedBone.inverseMatrix)

			bone = Bone()
			bone.boneIndex = boneIndex
			bone.boneParent = parsedBone.parentIndex
			bone.boneSibling = parsedBone.nextSiblingIndex
			bone.boneChild = parsedBone.nextChildIndex
			bone.boneSymmetric = parsedBone.symmetryBoneIndex
			bone.useSecondaryWeight = parsedBone.useSecondaryWeight
			reMesh.skeletonHeader.boneInfoList.append(bone)

		reMesh.skeletonHeader.boneHeaderOffset = getPaddedPos(
			reMesh.fileHeader.skeletonOffset + sd.SKELETON_REMAP_TABLE_OFFSET + 2 * reMesh.skeletonHeader.remapCount,
			16)
		reMesh.skeletonHeader.boneLocalMatrixOffset = reMesh.skeletonHeader.boneHeaderOffset + reMesh.skeletonHeader.boneCount * sd.BONE_INFO_ENTRY_SIZE
		reMesh.skeletonHeader.boneWorldMatrixOffset = reMesh.skeletonHeader.boneLocalMatrixOffset + reMesh.skeletonHeader.boneCount * sd.MATRIX_SIZE
		reMesh.skeletonHeader.boneInverseMatrixOffset = reMesh.skeletonHeader.boneWorldMatrixOffset + reMesh.skeletonHeader.boneCount * sd.MATRIX_SIZE

		currentOffset = reMesh.skeletonHeader.boneInverseMatrixOffset + reMesh.skeletonHeader.boneCount * sd.MATRIX_SIZE
	# Name lists and remaps
	currentNameIndex = 0
	for index, materialName in enumerate(parsedMesh.materialNameList):
		reMesh.rawNameList.append(materialName)
		reMesh.materialNameRemapList.append(index)
	currentNameIndex += len(reMesh.rawNameList)
	if parsedMesh.skeleton != None:
		for bone in parsedMesh.skeleton.boneList:
			reMesh.rawNameList.append(bone.boneName)
			reMesh.boneNameRemapList.append(currentNameIndex)
			currentNameIndex += 1
	# TODO Blend Shape Names Remap
	# Pragmata blend shape (shape key) export: gather packed deltas and register morph names.
	# Blend shape names are appended after material/bone names; the remap maps each to its
	# rawNameList index (the import reads them back as the trailing name entries).
	# Returns (None, None, None) when there are no shape keys.
	if version == VERSION_PRAG:
		blendDeltaBytes, blendPerLodList, blendNames = buildPragmataBlendShapeExport(
			parsedMesh, parsedSubMeshToSubMeshDataDict)
	else:
		blendDeltaBytes, blendPerLodList, blendNames = None, None, None
	if blendNames is not None:
		for name in blendNames:
			reMesh.blendShapeNameRemapList.append(len(reMesh.rawNameList))
			reMesh.rawNameList.append(name)
			currentNameIndex += 1

	reMesh.fileHeader.materialNameRemapOffset = currentOffset
	currentOffset = getPaddedPos(currentOffset + (len(reMesh.materialNameRemapList) * 2), 16)
	if parsedMesh.skeleton != None:
		reMesh.fileHeader.boneNameRemapOffset = currentOffset
		currentOffset = getPaddedPos(currentOffset + (len(reMesh.boneNameRemapList) * 2), 16)
	if blendNames is not None:
		reMesh.fileHeader.blendShapeNameOffset = currentOffset
		currentOffset = getPaddedPos(currentOffset + (len(reMesh.blendShapeNameRemapList) * 2), 16)

	reMesh.fileHeader.nameOffsetsOffset = currentOffset
	currentOffset = getPaddedPos(currentOffset + (len(reMesh.rawNameList) * 8),
	                             16)  # Get the position after all string offsets
	for name in reMesh.rawNameList:
		reMesh.rawNameOffsetList.append(currentOffset)
		currentOffset += len(name.encode('utf-8')) + 1
	reMesh.fileHeader.nameCount = len(reMesh.rawNameList)
	currentOffset = getPaddedPos(currentOffset, 16)
	# AABB
	if reMesh.boneBoundingBoxHeader != None:
		reMesh.fileHeader.aabbOffset = currentOffset
		reMesh.boneBoundingBoxHeader.offset = currentOffset + sd.AABB_OFFSET
		currentOffset += sd.AABB_OFFSET + reMesh.boneBoundingBoxHeader.count * sd.AABB_SIZE

	# Retail Pragmata stores a 16-byte normal-recalc stub immediately before the blend header.
	if version == VERSION_PRAG and blendPerLodList is not None:
		reMesh.fileHeader.normalRecalcOffset = currentOffset
		reMesh.normalRecalcRegionBytes = struct.pack("<Q", 1) + bytes(8)
		currentOffset = getPaddedPos(currentOffset + 16, 16)

	# Pragmata typing-2 blend shape struct region (header + per-LOD data), placed before the mesh buffer.
	if blendPerLodList is not None:
		reMesh.fileHeader.blendShapesOffset = currentOffset
		reMesh.blendShapeRegionBytes = serializePragmataBlendShapeRegion(blendPerLodList, currentOffset)
		currentOffset = getPaddedPos(currentOffset + len(reMesh.blendShapeRegionBytes), 16)

	# Mesh Buffer
	reMesh.fileHeader.meshOffset = currentOffset

	reMesh.meshBufferHeader = MeshBufferHeader()
	# Retail Pragmata extra-weight (type 7) is not Wilds' 7th-12th influences: it is a second
	# index pack the face shader fetches. Blender cannot represent it as vertex groups, so
	# reuse the streams persisted on the .blend (imported as vertex attributes).
	if version == VERSION_PRAG:
		auxInfo = getattr(parsedMesh, "pragmataBlendAux", None) or {}
		nverts = vertexPosBuffer.tell() // 12
		want = nverts * 16
		vanWeight = auxInfo.get("weight") if auxInfo else None
		vanExtra = auxInfo.get("extraW") if auxInfo else None
		if vanWeight and len(vanWeight) == want:
			weightBuffer.close()
			weightBuffer = BytesIO(vanWeight)
			weightBuffer.seek(0, 2)
			print(f"Pragmata: using imported type-4 weight stream ({want} bytes); vertex-group edits are not written")
		if vanExtra and len(vanExtra) == want:
			extraWeightBuffer.close()
			extraWeightBuffer = BytesIO(vanExtra)
			extraWeightBuffer.seek(0, 2)
			print(f"Pragmata: using imported type-7 extra-weight stream ({want} bytes); vertex-group edits are not written")

	# Collect vertex buffer chunks as bytes, join once at the end (avoids repeated bytearray.extend reallocations)
	vertexBufferChunks = []
	vertexElementTuples = []
	currentBufferOffset = 0
	if vertexPosBuffer.tell() != 0:
		vertexElementTuples.append((0, 12, currentBufferOffset))
		currentBufferOffset += vertexPosBuffer.tell()
		vertexBufferChunks.append(vertexPosBuffer.getvalue())

	if norTanBuffer.tell() != 0:
		vertexElementTuples.append((1, 8, currentBufferOffset))
		currentBufferOffset += norTanBuffer.tell()
		vertexBufferChunks.append(norTanBuffer.getvalue())

	if UVBuffer.tell() != 0:
		vertexElementTuples.append((2, 4, currentBufferOffset))
		currentBufferOffset += UVBuffer.tell()
		vertexBufferChunks.append(UVBuffer.getvalue())

	if UV2Buffer.tell() != 0:
		vertexElementTuples.append((3, 4, currentBufferOffset))
		currentBufferOffset += UV2Buffer.tell()
		vertexBufferChunks.append(UV2Buffer.getvalue())

	if weightBuffer.tell() != 0:
		vertexElementTuples.append((4, 16, currentBufferOffset))
		currentBufferOffset += weightBuffer.tell()
		vertexBufferChunks.append(weightBuffer.getvalue())

	if colorBuffer.tell() != 0:
		vertexElementTuples.append((5, 4, currentBufferOffset))
		currentBufferOffset += colorBuffer.tell()
		vertexBufferChunks.append(colorBuffer.getvalue())

	if extraWeightBuffer.tell() != 0:
		vertexElementTuples.append((7, 16, currentBufferOffset))
		currentBufferOffset += extraWeightBuffer.tell()
		vertexBufferChunks.append(extraWeightBuffer.getvalue())

	# Pragmata typing-2 tail: after declared vertex elements the buffer is
	#   [aux 16*nverts][extra 896][u32 vert map][float16 xyz deltas]
	# MeshBufferHeader.sunbreakSecondUnknown holds (mapOffset, deltaOffset) as two u32s.
	# Putting deltas at the geometry end (and leaving those offsets 0) makes the face shader
	# fail to bind -- eyes/teeth still draw because they are not morph targets.
	if version == VERSION_PRAG and blendDeltaBytes:
		nverts = vertexPosBuffer.tell() // 12
		declared = currentBufferOffset
		auxInfo = getattr(parsedMesh, "pragmataBlendAux", None)
		aux = (auxInfo or {}).get("aux") or b""
		extra = (auxInfo or {}).get("auxExtra") or b""
		if not extra or len(aux) != nverts * 20 + len(extra):
			raiseError(
				"Pragmata face export requires imported morph aux/map streams on the .blend "
				"(same vertex count as import). Re-import the mesh; remesh is not supported. "
				"Refusing to write a zeroed morph tail, which crashes the GPU."
			)
		vertexBufferChunks.append(aux)
		currentBufferOffset += len(aux)
		mapOff = declared + len(aux) - nverts * 4
		deltaOff = currentBufferOffset
		vertexBufferChunks.append(blendDeltaBytes)
		currentBufferOffset += len(blendDeltaBytes)
		reMesh.meshBufferHeader.sunbreakSecondUnknown = mapOff | (deltaOff << 32)
		if auxInfo.get("veSize") is not None:
			reMesh.meshBufferHeader.vertexElementSize = int(auxInfo["veSize"])
		if auxInfo.get("unkn1") is not None:
			reMesh.meshBufferHeader.unkn1 = int(auxInfo["unkn1"])
		print(
			f"Pragmata blend tail: aux={len(aux)} mapOff={mapOff} deltaOff={deltaOff} "
			f"deltas={len(blendDeltaBytes)} vtxBuf={currentBufferOffset}"
		)

	# Single allocation: join all chunks into the final vertex buffer
	reMesh.meshBufferHeader.vertexBuffer = bytearray().join(vertexBufferChunks)

	reMesh.meshBufferHeader.faceBuffer = faceBuffer.getvalue()
	# print(len(reMesh.meshBufferHeader.faceBuffer))
	# Build numpy structured array directly from collected tuples
	if vertexElementTuples:
		reMesh.meshBufferHeader.vertexElementList = np.array(vertexElementTuples,
			dtype=VERTEX_ELEMENT_DTYPE)
	else:
		reMesh.meshBufferHeader.vertexElementList = np.empty(0, dtype=VERTEX_ELEMENT_DTYPE)
	reMesh.meshBufferHeader.vertexElementCount = len(reMesh.meshBufferHeader.vertexElementList)
	reMesh.meshBufferHeader.mainVertexElementCount = reMesh.meshBufferHeader.vertexElementCount
	reMesh.meshBufferHeader.vertexElementOffset = reMesh.fileHeader.meshOffset + sd.VERTEX_ELEMENT_OFFSET
	reMesh.meshBufferHeader.vertexBufferOffset = getPaddedPos(
		reMesh.meshBufferHeader.vertexElementOffset + reMesh.meshBufferHeader.vertexElementCount * sd.VERTEX_ELEMENT_SIZE,
		16)

	# TODO check on this, padding vertex buffer size might cause issues in some games
	reMesh.meshBufferHeader.vertexBufferSize = getPaddedPos(currentBufferOffset, 16)
	reMesh.meshBufferHeader.faceBufferOffset = getPaddedPos(
		reMesh.meshBufferHeader.vertexBufferOffset + reMesh.meshBufferHeader.vertexBufferSize, 16)
	reMesh.meshBufferHeader.faceBufferSize = faceBuffer.tell()

	# Content Flags
	unknFlag16 = False  # Bit index 15
	unknFlag10 = False  # Bit index 9

	if version < VERSION_SF6:
		reMesh.meshBufferHeader.vertexElementSize = 31872
		reMesh.meshBufferHeader.block2FaceBufferOffset = reMesh.meshBufferHeader.faceBufferSize

	if version >= VERSION_SF6:
		reMesh.fileHeader.sf6UnknCount = 84
		reMesh.meshBufferHeader.vertexElementSize = 27104
		reMesh.fileHeader.verticesOffset = reMesh.meshBufferHeader.vertexBufferOffset
		reMesh.fileHeader.streamingInfoOffset = reMesh.fileHeader.meshOffset + sd.VERTEX_ELEMENT_OFFSET - 16
		reMesh.meshBufferHeader.block2FaceBufferOffset = reMesh.meshBufferHeader.vertexBufferSize + reMesh.meshBufferHeader.faceBufferSize
		reMesh.meshBufferHeader.NULL = reMesh.meshBufferHeader.block2FaceBufferOffset
		reMesh.meshBufferHeader.totalBufferSize = getPaddedPos(reMesh.meshBufferHeader.block2FaceBufferOffset,
		                                                       16)
		unknFlag16 = True
		unknFlag10 = True

	if version == VERSION_SF6:
		reMesh.fileHeader.sf6UnknCount = 6
		reMesh.fileHeader.lodGroupNameHash = 3407096719

	# Final Pragmata backfill: the tail path above may already have set these, and the
	# SF6 defaults (27104 / -1) must never leak into the retail header.
	pragmataAux = getattr(parsedMesh, "pragmataBlendAux", None)
	if version == VERSION_PRAG and pragmataAux:
		if pragmataAux.get("veSize") is not None:
			reMesh.meshBufferHeader.vertexElementSize = int(pragmataAux["veSize"])
		if pragmataAux.get("unkn1") is not None:
			reMesh.meshBufferHeader.unkn1 = int(pragmataAux["unkn1"])

	currentOffset = getPaddedPos(
		reMesh.meshBufferHeader.faceBufferOffset + reMesh.meshBufferHeader.faceBufferSize, 16)
	# DD2 secondary weights reuse sunbreakSecondUnknown as a byte length. Pragmata/RE9
	# store (mapOffset, deltaOffset) there; do not overwrite it.
	if version >= VERSION_DD2 and version not in (VERSION_PRAG, VERSION_RE9):
		if parsedMesh.bufferHasSecondaryWeight:
			reMesh.meshBufferHeader.sunbreakOffset = reMesh.meshBufferHeader.vertexBufferOffset + reMesh.meshBufferHeader.totalBufferSize

			reMesh.meshBufferHeader.secondaryWeightBuffer = secondaryWeightBuffer.getvalue()
			reMesh.meshBufferHeader.sunbreakSecondUnknown = len(reMesh.meshBufferHeader.secondaryWeightBuffer)
			currentOffset = reMesh.meshBufferHeader.sunbreakOffset + len(
				reMesh.meshBufferHeader.secondaryWeightBuffer)
	reMesh.fileHeader.fileSize = currentOffset

	reMesh.fileHeader.contentFlag.setBitFlag(unknFlag16, unknFlag10, hasUnknFlag8=True,
	                                         hasGroupPivot=reMesh.floatsHeader != None,
	                                         hasBlendShape=blendPerLodList is not None,
	                                         hasSkeleton=reMesh.skeletonHeader != None,
	                                         hasAABB=reMesh.boneBoundingBoxHeader != None)
	vertexPosBuffer.close()
	norTanBuffer.close()
	UVBuffer.close()
	UV2Buffer.close()
	weightBuffer.close()
	colorBuffer.close()
	extraWeightBuffer.close()
	faceBuffer.close()
	secondaryWeightBuffer.close()

	return reMesh


# ---RE MESH IO FUNCTIONS---#

def readREMesh(filepath, lodTarget=None):
	print("Opening " + filepath)
	file = openFileRead(filepath, MESH_IO_BUFFER_SIZE)
	meshVersion = parseFileVersion(filepath, None)
	if meshVersion is None:
		print("Unable to read mesh version from file path, assuming MHRSB")
		meshVersion = 2109148288  # MHRSB
	version = meshFileVersionToNewVersionDict.get(meshVersion, getNearestRemapVersion(meshVersion))
	if meshVersion not in meshFileVersionToNewVersionDict:
		raiseWarning(f"Mesh Version ({str(meshVersion)}) not supported! Attempting import...")
		print(
			f"Nearest Remap Version: {str(version)} ({meshFileVersionToGameNameDict[newVersionToMeshFileVersion[version]]})")

	streamingBuffer = None  # WILDS
	# if version >= VERSION_MHWILDS:

	# Precheck to see if user imported a headerless streaming mesh
	magic = read_uint(file)
	if magic != 1213416781 and "streaming" in filepath:
		raiseError(
			"Attempted to import a streaming mesh file. Streaming mesh files cannot be imported directly.\nImport the mesh file that has same path and name that's not in the streaming folder.")
	file.seek(0)

	if version >= VERSION_SF6:
		paths = splitNativesPath(filepath)
		if paths != None:  # Returns none if path does not contain a natives folder
			rootPath = paths[0]  # The path to the natives\STM folder from the root
			nativesPath = paths[1]  # The path to the file inside the natives\STM folder
			streamingMeshPath = os.path.join(rootPath, "streaming", nativesPath)
			if os.path.isfile(streamingMeshPath):

				with openFileRead(streamingMeshPath, MESH_IO_BUFFER_SIZE) as streamFile:
					streamingBuffer = streamFile.read()
				print(f"Loaded {len(streamingBuffer)} bytes from streaming mesh at {streamingMeshPath}")
	if magic == 1498173517 and IMPORT_MPLY:  # MPLY Mesh
		reMeshFile = REMeshMPLY()
		print("Loading MPLY mesh.")
	else:
		reMeshFile = REMesh()
	reMeshFile.meshVersion = meshVersion
	try:
		reMeshFile.read(file, version, lodTarget, streamingBuffer)
	finally:
		file.close()
	return reMeshFile


def writeREMesh(reMeshFile, filepath):
	print("Writing to " + filepath)
	meshVersion = parseFileVersion(filepath, None)
	if meshVersion is None:
		print("Unable to read mesh version from file path, assuming MHRSB")
		meshVersion = 2109148288  # MHRSB
	version = newVersionToMeshFileVersion.get(meshVersion, getNearestRemapVersion(meshVersion))
	reMeshFile.meshVersion = meshVersion
	with openFileWrite(filepath, MESH_IO_BUFFER_SIZE) as file:
		reMeshFile.write(file, version)
