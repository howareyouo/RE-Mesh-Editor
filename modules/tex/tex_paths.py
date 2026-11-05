#Author: NSA Cloud
"""Resolve the texture files a texture binding path points at, without bpy.

A texture binding holds the path the game resolves inside the mod's natives/<platform>
folder ("character/hero/hero_ALBM.tex"). The file on disk carries the version suffix
("hero_ALBM.tex.7602304") and can sit in the streaming sub folder, so finding it is a
search. Which places are searched depends on the caller (chunk folders, the mod
directory, the folder the mdf came from), so the search roots are always passed in:
this module reads no scene state and imports no bpy.
"""
import os
from collections import namedtuple

from ..gen_functions import STREAMING_FOLDER, splitNativesPath

TEX_EXTENSION = ".tex"

#Match every file whose .tex base name equals the binding's, whatever version suffix
#and platform extension it carries. The rename pass needs all of them.
TEX_MATCH_BASE = "base"
#Match the file of one expected version suffix and fall back to any other version of
#the same name, the way the texture import resolves a binding.
TEX_MATCH_VERSION = "version"

#One place a binding path is resolved against.
#rootPath:  folder the relative texture path starts at (chunk folder, mod natives folder)
#relDir:    path below the root the binding's folder maps to, None for the binding's own
#streaming: also search the streaming sibling folder of the root
SearchRoot = namedtuple("SearchRoot", ("rootPath", "relDir", "streaming"), defaults = (None, True))


def normalizeTexturePath(texturePath):
	"""Return a binding path the way the game resolves it: version suffix and chunk root
	dropped, forward slashes, no leading or trailing separator."""
	if texturePath == "":
		return ""
	path = texturePath.replace("\\", "/")
	if not path.endswith(".rtex"):#Render targets keep their path as it is
		path = path.split(TEX_EXTENSION)[0] + TEX_EXTENSION
		if "natives" in path.lower():
			nativesPath = splitNativesPath(path)
			if nativesPath != None:
				path = nativesPath[1].replace("\\", "/")
	return path.strip("/")


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


def getStreamingTexturePath(texPath):
	"""Returns the streaming tex path for a tex file inside a mod directory.

	The streaming folder is placed directly under the game folder and mirrors
	the texture's relative path, ex:
	natives/STM/Art/Model/foo/pl_foo_ALB.tex.241106027 ->
	natives/STM/streaming/Art/Model/foo/pl_foo_ALB.tex.241106027"""
	normalizedPath = texPath.replace("/", os.sep).replace("\\", os.sep)
	split = splitNativesPath(normalizedPath)
	if split != None:
		rootPath, nativesPath = split
		return os.path.join(rootPath, STREAMING_FOLDER, nativesPath)
	return os.path.join(os.path.dirname(texPath), STREAMING_FOLDER, os.path.basename(texPath))


def findTextureFiles(bindingPath, searchRootList, texVersion = "", matchMode = TEX_MATCH_BASE, listDir = None, resolvePath = None):
	"""Return the files on disk behind a texture binding path, most specific first.

	The roots are searched in the order given: TEX_MATCH_VERSION stops at the first root
	that holds a match (the caller wants one file), TEX_MATCH_BASE collects the matches
	of every root (one base name can have a file per platform). Inside a folder the
	entries are matched in name order. An empty list - not an exception - means the
	texture is not on disk.

	listDir is an optional cached listing callback (folder -> entry names), resolvePath
	an optional case-insensitive path resolver, tried last with the version specific
	candidate names."""
	normalizedPath = normalizeTexturePath(bindingPath)
	if normalizedPath == "":
		return []
	if matchMode == TEX_MATCH_VERSION:
		return _findVersionedFiles(normalizedPath, searchRootList, texVersion, listDir, resolvePath)
	return _findBaseNamedFiles(os.path.split(normalizedPath), searchRootList, listDir)


def findTextureFile(bindingPath, searchRootList, texVersion = "", matchMode = TEX_MATCH_BASE, listDir = None, resolvePath = None):
	"""The first file findTextureFiles returns, or None when the texture is not on disk."""
	fileList = findTextureFiles(bindingPath, searchRootList, texVersion, matchMode, listDir, resolvePath)
	return fileList[0] if fileList else None


def _findBaseNamedFiles(splitPath, searchRootList, listDir):
	dirPart, fileName = splitPath
	baseName = getTextureFileBaseName(fileName)
	if baseName == None:
		return []#Render targets (.rtex) and non texture bindings have no file to find
	targetName = baseName.lower()
	fileList = []
	for folder in _getUniqueFolderList(searchRootList, dirPart):
		for entryName in _listFolder(folder, listDir):
			entryBaseName = getTextureFileBaseName(entryName)
			if entryBaseName == None or entryBaseName.lower() != targetName:
				continue
			filePath = os.path.join(folder, entryName)
			if os.path.isfile(filePath):
				fileList.append(os.path.normpath(filePath))
	return fileList


def _findVersionedFiles(normalizedPath, searchRootList, texVersion, listDir, resolvePath):
	#normcase keeps these comparisons case-insensitive on Windows and case-sensitive
	#elsewhere, which is the rule the import used as a platform switch
	exactName = normalizedPath + str(texVersion)
	exactKey = os.path.normcase(exactName)
	anyVersionKey = os.path.normcase(normalizedPath)
	for root in searchRootList:
		folderList = _getSearchFolderList(root, "")
		#The version specific file, without reading a directory listing
		for folder in folderList:
			exactPath = os.path.join(folder, exactName)
			if os.path.isfile(exactPath):
				return [os.path.normpath(exactPath)]
		#The game appends platform and language extensions to the version specific name,
		#so an entry starting with it is that file. The listed entries are bare names
		#while the keys are whole relative paths, so only a texture at the root of a
		#chunk can match here - the scope the import has always had.
		anyVersionPath = None
		for folder in folderList:
			for entryName in _listFolder(folder, listDir):
				entryKey = os.path.normcase(entryName)
				if entryKey.startswith(exactKey):
					return [os.path.normpath(os.path.join(folder, entryName))]
				if anyVersionPath == None and entryKey.startswith(anyVersionKey):
					anyVersionPath = os.path.join(folder, entryName)
		if anyVersionPath != None:
			return [os.path.normpath(anyVersionPath)]
		if resolvePath != None:
			for folder in folderList:
				resolvedPath = resolvePath(os.path.join(folder, exactName))
				if resolvedPath != None:
					return [resolvedPath]
	return []


def _getUniqueFolderList(searchRootList, dirPart):
	"""Return every folder of the roots, in priority order, without repeats."""
	folderList = []
	seenKeySet = set()
	for root in searchRootList:
		relDir = dirPart if root.relDir == None else root.relDir
		for folder in _getSearchFolderList(root, relDir):
			key = os.path.normcase(os.path.realpath(os.path.abspath(folder)))
			if key in seenKeySet:
				continue
			seenKeySet.add(key)
			folderList.append(folder)
	return folderList


def _getSearchFolderList(root, relDir):
	"""Return the folders of one search root, its streaming sibling first."""
	folderList = []
	if root.streaming:
		folderList.append(_joinRelPath(os.path.join(root.rootPath, STREAMING_FOLDER), relDir))
	folderList.append(_joinRelPath(root.rootPath, relDir))
	return folderList


def _joinRelPath(folderPath, relDir):
	return os.path.join(folderPath, relDir) if relDir else folderPath


def _listFolder(folder, listDir = None):
	"""Return the entry names of a folder in name order, empty when it can't be read."""
	if listDir != None:
		return listDir(folder)
	try:
		return sorted(os.listdir(folder))
	except OSError:
		return ()
