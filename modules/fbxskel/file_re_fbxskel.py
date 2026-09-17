#Author: NSA Cloud
import os

from ..gen_functions import textColors,raiseWarning,raiseError,openFileRead,openFileWrite,getPaddingAmount,parseFileVersion,read_unicode_string,write_unicode_string,StringTableBuilder
from ..hashing.mmh3.pymmh3 import hashUTF16
from ..binary_struct import BinaryStruct,u32,u64,i16,f32x3,f32x4,skip

DEBUG_MODE = False
class SIZEDATA():
	def __init__(self):
		self.HEADER_SIZE = 48
		self.BONE_ENTRY_SIZE = 64
		self.HASH_ENTRY_SIZE = 8
		self.PROPERTY_ENTRY_SIZE = 24
		self.PROPERTY_VALUE_SIZE = 4
		
def debugprint(string):
	if DEBUG_MODE:
		print(string)
class FBXSkelHeader(BinaryStruct):
	_fields_ = [
		u32("version"),u32("magic"),skip(8),
		u32("boneOffset"),skip(4),u32("hashOffset"),skip(4),u32("boneCount"),
	]
	def __init__(self):
		self.version = 5
		self.magic = 1852599155#skln
		self.boneOffset = 48
		self.hashOffset = 0
		self.boneCount = 0
	def post_read(self,file,version):
		if self.magic != 1852599155:
			raiseError("File is not an FBXSkel file.")
	def __str__(self):
		return str(self.__class__) + ": " + str(self.__dict__)

class BoneEntry(BinaryStruct):
	_fields_ = [
		u64("boneNameOffset"),u32("boneMMH3Hash"),i16("parentIndex"),i16("boneIndex"),
		f32x4("rotation",cond=lambda v: v >= 5),f32x3("translation",cond=lambda v: v >= 5),
		f32x3("scale",cond=lambda v: v >= 5),u32("segmentScaling",cond=lambda v: v >= 5),skip(4,cond=lambda v: v >= 5),
		f32x3("translation",cond=lambda v: v < 5),skip(4,cond=lambda v: v < 5),
		f32x4("rotation",cond=lambda v: v < 5),f32x3("scale",cond=lambda v: v < 5),skip(4,cond=lambda v: v < 5),
	]
	def __init__(self):
		self.boneNameOffset = 0
		self.boneName = "BONE_NAME"
		self.boneMMH3Hash = 0
		self.parentIndex = 0
		self.boneIndex = 0
		self.translation = (0.0,0.0,0.0)
		self.rotation = (0.0,0.0,0.0,1.0)
		self.scale = (1.0,1.0,1.0)
		self.segmentScaling = 0
	def post_read(self,file,version):
		currentPos = file.tell()
		file.seek(self.boneNameOffset)
		self.boneName = read_unicode_string(file)
		file.seek(currentPos)
	def __str__(self):
		return str(self.__class__) + ": " + str(self.__dict__)

class HashEntry(BinaryStruct):
	_fields_ = [
		u32("mmh3Hash"),u32("boneIndex"),
	]
	def __init__(self):
		self.mmh3Hash = 0
		self.boneIndex = 0
	def __str__(self):
		return str(self.__class__) + ": " + str(self.__dict__)

class FBXSkelFile():
	def __init__(self):
		self.sizeData = SIZEDATA()
		self.header = FBXSkelHeader()
		self.boneEntryList = []
		self.boneHashList = []#Sorted by hash value
		self.stringTable = StringTableBuilder(write_duplicates=True)
	def read(self,file):
		self.header.read(file)
		debugprint(self.header)
		file.seek(self.header.boneOffset)
		for i in range(0,self.header.boneCount):
			entry = BoneEntry()
			entry.read(file,self.header.version)
			debugprint(entry)
			self.boneEntryList.append(entry)
		file.seek(self.header.hashOffset)
		for i in range(0,self.header.boneCount):
			entry = HashEntry()
			entry.read(file)
			self.boneHashList.append(entry)
		
	def recalculateHashesAndOffsets(self):
		self.header.boneCount = len(self.boneEntryList)
		
		boneEntriesSize = self.sizeData.BONE_ENTRY_SIZE * len(self.boneEntryList)
		self.header.hashOffset = self.sizeData.HEADER_SIZE + boneEntriesSize
		
		stringTableOffset = self.header.hashOffset + (self.header.boneCount * self.sizeData.HASH_ENTRY_SIZE)
		self.stringTable = StringTableBuilder(write_duplicates=True)
		for bone in self.boneEntryList:
			bone.boneMMH3Hash = hashUTF16(bone.boneName)
			bone.boneNameOffset = self.stringTable.add(bone.boneName) + stringTableOffset
		self.boneHashList = []
		for index,boneEntry in enumerate(self.boneEntryList):
			hashEntry = HashEntry()
			hashEntry.mmh3Hash = boneEntry.boneMMH3Hash
			hashEntry.boneIndex = index
			self.boneHashList.append(hashEntry)
		self.boneHashList.sort(key=lambda x: x.mmh3Hash)
		
	def write(self,file,version):
		self.header.version = version
		self.recalculateHashesAndOffsets()
		self.header.write(file)
		file.seek(self.header.boneOffset)
		print("Writing Bone Entries")
		for boneEntry in self.boneEntryList:
			debugprint(boneEntry)
			boneEntry.write(file,self.header.version)
		#Loop to write texture entries
		print("Writing Bone Hashes")
		for hashEntry in self.boneHashList:
			hashEntry.write(file)
		#Loop to write property headers
		print("Writing Bone Strings")
		self.stringTable.write(file)
def readFBXSkel(filepath):
	print(textColors.OKCYAN + "__________________________________\nFBXSkel read started." + textColors.ENDC)
	print("Opening " + filepath)
	with openFileRead(filepath) as file:
		fbxSkelFile = FBXSkelFile()
		fbxSkelFile.read(file)
	print(textColors.OKGREEN + "__________________________________\nFBXSkel read finished." + textColors.ENDC)
	return fbxSkelFile
def writeFBXSkel(fbxSkelFile,filepath):
	print(textColors.OKCYAN + "__________________________________\nFBXSkel write started." + textColors.ENDC)
	print("Opening " + filepath)
	version = parseFileVersion(filepath, 5)
	if version == 5 and not os.path.splitext(filepath)[1][1:].isdigit():
		raiseWarning("No number extension found on FBXSkel file, defaulting to version 5")
	with openFileWrite(filepath) as file:
		fbxSkelFile.write(file,version)
	print(textColors.OKGREEN + "__________________________________\nFBXSkel write finished." + textColors.ENDC)
