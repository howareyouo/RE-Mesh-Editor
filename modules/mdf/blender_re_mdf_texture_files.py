#Author: NSA Cloud
import bpy
import os
from contextlib import contextmanager

from ..blender_utils import getAddonPreferences, findMDFCollectionForMaterialObject, iterMDFMaterialObjects
from ..gen_functions import splitNativesPath
from .blender_re_mdf import fixTexPath

# ---------------------------------------------------------------- texture file paths
# A texture binding holds the path the game resolves inside the mod's natives/STM
# folder ("character/hero/hero_ALBM.tex"), while the imported file on disk carries a
# version suffix ("hero_ALBM.tex.7602304") and can sit in a streaming sub folder.
# Renaming a binding therefore only keeps pointing at a real file when the file is
# renamed with it - the rule the mesh/MDF material name sync follows, applied to the
# texture files a material binds to.

TEX_EXTENSION = ".tex"
STREAMING_FOLDER = "streaming"

def normalizeTexturePath(texturePath):
	"""Normalise a texture binding path so two spellings of one path compare equal.

	fixTexPath drops the version suffix and the chunk root, but it cleans up the
	separators before its own natives split, so backslashes can survive it."""
	if texturePath == "":
		return ""
	return fixTexPath(texturePath.replace("\\", "/")).replace("\\", "/").strip("/")

def getTextureFileBaseName(fileName):
	"""Get the .tex base name of a texture file, e.g. "foo.tex" from "foo.tex.123" or
	"foo.tex.123.pc". Returns None for files that are not texture files (".rtex", ".txt")."""
	lowerName = fileName.lower()
	index = lowerName.find(TEX_EXTENSION)
	if index <= 0:#Ignore junk names like ".tex"
		return None
	suffix = lowerName[index + len(TEX_EXTENSION):]
	if suffix != "" and not suffix.startswith("."):
		return None
	return fileName[:index + len(TEX_EXTENSION)]

def replaceTextureFileBaseName(fileName, newBaseName):
	"""Replace the .tex base of a file name, keeping its suffix as it is, e.g.
	"foo.tex.7602304.pc" -> "bar.tex.7602304.pc".

	The version suffix identifies the file for the game, so only the name part moves."""
	baseName = getTextureFileBaseName(fileName)
	if baseName == None:
		return fileName
	return newBaseName + fileName[len(baseName):]

def getMDFSourceFolder(mdfCollection):
	"""Return the folder the MDF file was imported from, or None when the import path was
	not stored on the collection (or the collection is gone)."""
	if mdfCollection == None:
		return None
	mdfPath = mdfCollection.get("~MDFFILEPATH", None)
	if not mdfPath:
		return None
	return os.path.dirname(mdfPath)

def getTextureSearchFolderList(mdfCollection, texturePath):
	"""Return every folder a texture path can resolve to, most specific first.

	Texture paths are relative to the mod's natives/STM root, which is stored both on
	the MDF file path and in the tool panel's mod directory; streaming textures sit one
	"streaming" folder below it. The MDF's own folder is tried too, because mods often
	keep their textures next to the mdf2 instead of in the chunk folder tree."""
	dirPart = os.path.dirname(normalizeTexturePath(texturePath))
	mdfFolder = getMDFSourceFolder(mdfCollection)
	rootList = []
	mdfPath = mdfCollection.get("~MDFFILEPATH", None) if mdfCollection != None else None
	if mdfPath:
		nativesPath = splitNativesPath(mdfPath)
		if nativesPath != None:
			rootList.append(nativesPath[0])
	panel = getattr(bpy.context.scene, "re_mdf_toolpanel", None)
	modDirectory = getattr(panel, "modDirectory", "") if panel != None else ""
	if modDirectory:
		rootList.append(modDirectory)
	folderList = []
	for root in rootList:
		folderList.append(os.path.join(root, dirPart))
		folderList.append(os.path.join(root, STREAMING_FOLDER, dirPart))
	if mdfFolder:
		folderList.append(mdfFolder)
	uniqueList = []
	seenKeySet = set()
	for folder in folderList:
		key = os.path.normcase(os.path.realpath(os.path.abspath(folder)))
		if key in seenKeySet:
			continue
		seenKeySet.add(key)
		uniqueList.append(folder)
	return uniqueList

def findTextureFilesInFolder(folder, baseName):
	"""Return the texture files of one folder whose .tex base name matches.

	Several files can match (one per platform or language suffix); the suffix is not
	part of the binding path, so it can't narrow the search down."""
	try:
		nameList = sorted(os.listdir(folder))
	except OSError:
		return []
	targetName = baseName.lower()
	fileList = []
	for fileName in nameList:
		fileBaseName = getTextureFileBaseName(fileName)
		if fileBaseName == None or fileBaseName.lower() != targetName:
			continue
		filePath = os.path.join(folder, fileName)
		if os.path.isfile(filePath):
			fileList.append(filePath)
	return fileList

def resolveTextureFiles(mdfCollection, texturePath):
	"""Return the files on disk a texture binding path currently points at."""
	normalizedPath = normalizeTexturePath(texturePath)
	baseName = getTextureFileBaseName(os.path.basename(normalizedPath))
	if baseName == None:
		return []
	fileList = []
	for folder in getTextureSearchFolderList(mdfCollection, normalizedPath):
		fileList.extend(findTextureFilesInFolder(folder, baseName))
	return fileList

def getTextureFileRenamePlan(mdfCollection, oldPath, newPath):
	"""Plan renaming the texture files of a binding path to the new name.

	Only a name change inside the same folder is a rename. A path that moves to
	another folder, an empty old path (a new binding) and a texture that is not on
	disk are all left alone: there is nothing that has to follow the edit.

	Returns (planList, errorMessage), where planList holds (oldFilePath, newFilePath)
	and an empty planList means there is nothing to rename."""
	oldNormalized = normalizeTexturePath(oldPath)
	newNormalized = normalizeTexturePath(newPath)
	if oldNormalized == "" or newNormalized == "" or oldNormalized == newNormalized:
		return [], None
	if not oldNormalized.endswith(TEX_EXTENSION) or not newNormalized.endswith(TEX_EXTENSION):
		return [], None#Render targets (.rtex) and non texture bindings have no file to rename
	oldFolder, oldFileName = os.path.split(oldNormalized)
	newFolder, newFileName = os.path.split(newNormalized)
	oldBaseName = getTextureFileBaseName(oldFileName)
	newBaseName = getTextureFileBaseName(newFileName)
	if oldBaseName == None or newBaseName == None:
		return [], None
	if oldFolder.lower() != newFolder.lower() or oldBaseName.lower() == newBaseName.lower():
		return [], None
	fileList = resolveTextureFiles(mdfCollection, oldNormalized)
	if not fileList:
		return [], None
	planList = []
	for filePath in fileList:
		folder = os.path.dirname(filePath)
		newFilePath = os.path.join(folder, replaceTextureFileBaseName(os.path.basename(filePath), newBaseName))
		if os.path.normcase(newFilePath) == os.path.normcase(filePath):
			continue
		if os.path.exists(newFilePath):
			return [], f"\"{os.path.basename(newFilePath)}\" already exists in {folder}."
		planList.append((filePath, newFilePath))
	return planList, None

def renameTextureFiles(planList):
	"""Rename the planned files. Returns (renamedCount, errorMessage)."""
	renamedCount = 0
	errorList = []
	for oldFilePath, newFilePath in planList:
		try:
			os.rename(oldFilePath, newFilePath)
			renamedCount += 1
		except OSError as err:
			print(f"Failed to rename {oldFilePath}: {err}")
			errorList.append(f"\"{os.path.basename(oldFilePath)}\" could not be renamed: {err}")
	return renamedCount, (errorList[0] if errorList else None)

# ---------------------------------------------------------------- path write guard
_texturePathSyncDepth = 0

def isTexturePathSyncActive():
	"""True while a block that must not move files is writing texture binding paths.

	A path write fires the binding's update callback, which would otherwise rename the
	files and rewrite the bindings sharing them once per write of a pass."""
	return _texturePathSyncDepth > 0

@contextmanager
def texturePathSync():
	"""Run a block whose texture binding path writes must not touch the disk."""
	global _texturePathSyncDepth
	_texturePathSyncDepth += 1
	try:
		yield
	finally:
		_texturePathSyncDepth = max(0, _texturePathSyncDepth - 1)

def isAutoRenameTextureFilesEnabled():
	"""True when editing a texture binding path may rename the files on disk."""
	try:
		return getAddonPreferences().autoRenameTextureFiles
	except Exception:
		return True

# ---------------------------------------------------------------- binding sync
def iterTextureBindings(mdfCollection):
	"""Yield every texture binding of an MDF collection (empty if the collection is None)."""
	if mdfCollection == None:
		return
	for materialObj in iterMDFMaterialObjects(mdfCollection):
		for binding in materialObj.re_mdf_material.textureBindingList_items:
			yield binding

def updateBindingsUsingTexturePath(mdfCollection, oldPath, newPath, ignoreBinding = None):
	"""Point every binding that used oldPath at newPath.

	Textures are shared between the materials of an MDF (one atlas is bound by several
	materials), so the bindings that referenced the renamed file have to follow it or
	they end up pointing at a name that no longer exists. Returns the updated count."""
	oldKey = normalizeTexturePath(oldPath).lower()
	if oldKey == "":
		return 0
	updatedCount = 0
	for binding in iterTextureBindings(mdfCollection):
		if ignoreBinding != None and binding == ignoreBinding:
			continue
		if normalizeTexturePath(binding.path).lower() != oldKey:
			continue
		binding.path = newPath
		updatedCount += 1
	return updatedCount

def syncTextureFileFromBindingPath(binding, oldPath, newPath):
	"""Rename the files of a binding path change and keep the bindings sharing them in sync.

	Returns (renamedFileCount, updatedBindingCount, errorMessage). Nothing is renamed
	when the new name is already taken (see getTextureFileRenamePlan): pointing a
	binding at a texture that exists is a valid edit, and undoing it would break it."""
	if isTexturePathSyncActive():
		return 0, 0, None
	if normalizeTexturePath(oldPath) == normalizeTexturePath(newPath):
		return 0, 0, None
	materialObj = binding.id_data
	mdfCollection = findMDFCollectionForMaterialObject(materialObj)
	if mdfCollection == None:
		return 0, 0, None
	planList, error = getTextureFileRenamePlan(mdfCollection, oldPath, newPath)
	if error != None:
		return 0, 0, error
	if not planList:
		return 0, 0, None
	renamedCount, error = renameTextureFiles(planList)
	updatedCount = 0
	if renamedCount > 0:
		#Only the bindings of a file that actually moved have to follow: the ones left
		#behind would point at a name that no longer exists.
		with texturePathSync():
			updatedCount = updateBindingsUsingTexturePath(mdfCollection, oldPath, newPath, ignoreBinding = binding)
	return renamedCount, updatedCount, error
