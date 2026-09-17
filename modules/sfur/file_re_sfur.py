#Author: NSA Cloud
import os

from ..gen_functions import textColors,raiseWarning,raiseError,openFileRead,openFileWrite,read_unicode_string,write_unicode_string,parseFileVersion,StringTableBuilder
from ..binary_struct import BinaryStruct,u32,u64,f32,u8,u16,arr

class SIZEDATA():
	def __init__(self,version):
		self.SFUR_ENTRY_SIZE = 72
		if version < 5:
			self.SFUR_ENTRY_SIZE = 80
			

class SFurHeader(BinaryStruct):
	_fields_ = [
		u32("magic"),u32("version"),u32("matCount"),u32("unkn"),u64("tblOffset"),
		arr("offsetList","Q","matCount"),
	]
	def __init__(self):
		self.magic = 1381320275#sfur
		self.version = 5
		self.matCount = 0
		self.unkn = 0
		self.tblOffset = 0
		self.offsetList = []
	def post_read(self,file,version):
		if self.magic != 1381320275:
			raiseError("File is not an SFur file.")
	def __str__(self):
		return str(self.__class__) + ": " + str(self.__dict__)

class SFurEntry(BinaryStruct):
	_fields_ = [
		u32("shellCount"),u32("shellThinType"),u32("groomingTexCoordType"),
		f32("shellHeight"),f32("bendRate"),f32("bendRootRate"),f32("normalTransformRate"),
		f32("stiffness"),f32("stiffnessDistribution"),f32("springCoefficient"),
		f32("damping"),f32("gravityForceScale"),f32("directWindForceScale"),
		u8("isForceTwoSide"),u8("isForceAlphaTest"),u16("padding"),
		u64("unknOffset",cond=lambda v: v < 5),
		u64("materialNameOffset"),u64("groomingTexturePathOffset"),
	]
	def __init__(self):
		self.shellCount = 0
		self.shellThinType = 0
		self.groomingTexCoordType = 0
		self.shellHeight = 0.0
		self.bendRate = 0.0
		self.bendRootRate = 0.0
		self.normalTransformRate = 0.0
		self.stiffness = 0.0
		self.stiffnessDistribution = 0.0
		self.springCoefficient = 0.0
		self.damping = 0.0
		self.gravityForceScale = 0.0
		self.directWindForceScale = 0.0
		self.isForceTwoSide = False
		self.isForceAlphaTest = False
		self.padding = 0
		self.unknOffset = 0#Version 4 only?
		self.materialNameOffset = 0
		self.materialName = "MATERIAL_NAME"
		self.groomingTexturePathOffset = 0
		self.groomingTexturePath = ""
	def post_read(self,file,version):
		self.isForceTwoSide = bool(self.isForceTwoSide)
		self.isForceAlphaTest = bool(self.isForceAlphaTest)
		currentPos = file.tell()
		file.seek(self.materialNameOffset)
		self.materialName = read_unicode_string(file)
		file.seek(self.groomingTexturePathOffset)
		self.groomingTexturePath = read_unicode_string(file)
		file.seek(currentPos)
	def __str__(self):
		return str(self.__class__) + ": " + str(self.__dict__)


class SFurFile():
	def __init__(self):
		
		self.header = SFurHeader()
		self.furEntryList = []
		self.stringTable = StringTableBuilder()
	def read(self,file):
		self.header.read(file)
		for entryOffset in self.header.offsetList:
			file.seek(entryOffset)
			entry = SFurEntry()
			entry.read(file,self.header.version)
			self.furEntryList.append(entry)
		
	def recalculateHashesAndOffsets(self):
		
		self.header.matCount = len(self.furEntryList)
		
		self.header.tblOffset = 24
		
		furEntrySize = self.sizeData.SFUR_ENTRY_SIZE * len(self.furEntryList)
		
		currentEntryOffset = self.header.tblOffset + (self.header.matCount * 8)
		stringTableOffset = currentEntryOffset + furEntrySize
		self.header.offsetList.clear()
		self.stringTable = StringTableBuilder()
		for entry in self.furEntryList:
			entry.materialNameOffset = self.stringTable.add(entry.materialName) + stringTableOffset
			entry.groomingTexturePathOffset = self.stringTable.add(entry.groomingTexturePath) + stringTableOffset
			self.header.offsetList.append(currentEntryOffset)
			currentEntryOffset += self.sizeData.SFUR_ENTRY_SIZE
			
	def write(self,file,version):
		self.header.version = version
		self.sizeData = SIZEDATA(version)
		self.recalculateHashesAndOffsets()
		self.header.write(file)
		
		print("Writing Fur Entries")
			
		for index,offset in enumerate(self.header.offsetList):
			file.seek(offset)
			furEntry = self.furEntryList[index]
			furEntry.write(file,self.header.version)
		print("Writing Strings")
		self.stringTable.write(file)
def readSFur(filepath):
	print(textColors.OKCYAN + "__________________________________\nSFur read started." + textColors.ENDC)
	print("Opening " + filepath)
	with openFileRead(filepath) as file:
		sFurFile = SFurFile()
		sFurFile.read(file)
	print(textColors.OKGREEN + "__________________________________\nSFur read finished." + textColors.ENDC)
	return sFurFile
def writeSFur(sFurFile,filepath):
	print(textColors.OKCYAN + "__________________________________\nSFur write started." + textColors.ENDC)
	print("Opening " + filepath)
	version = parseFileVersion(filepath, None)
	if version is None:
		raiseWarning("No number extension found on SFur file, defaulting to version 5")
		version = 5
	with openFileWrite(filepath) as file:
		sFurFile.write(file,version)
	print(textColors.OKGREEN + "__________________________________\nSFur write finished." + textColors.ENDC)