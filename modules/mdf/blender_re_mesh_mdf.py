#Author: NSA Cloud
import os
import bpy
import re
import traceback
import glob
import struct

from mathutils import Vector
from ..gen_functions import IS_WINDOWS,isLinux,resolveLinuxPath,fname
from ..blender_utils import arrangeNodeTree,showErrorMessageBox
IMPORT_TRANSLUCENT = False # Disabled since it's not quite right yet
DEBUG_MODE = False#Enables verbose console logging for console import diagnostics
#RE2 RT frosted glass: a transmission shader with a blur/clear approximation and
#an optional unscaled FrostMask, replacing the flat principled output (ported from REME v0.73)
def buildRE2FrostedGlass(matInfo, normalNode):
	material = matInfo["blenderMaterial"]
	tree = material.node_tree
	nodes, links = tree.nodes, tree.links
	props = matInfo["mPropDict"]
	texture = matInfo["textureNodeDict"].get("NormalRoughnessMap")
	if texture is None:
		raise ValueError("RE2 frosted glass requires NormalRoughnessMap")

	def scalar(name, default):
		prop = props.get(name)
		return float(prop.propValue[0]) if prop and prop.propValue else default

	colorProp = props.get("Glass_Color")
	color = tuple(colorProp.propValue) if colorProp and len(colorProp.propValue) == 4 else (1, 1, 1, 1)
	#Blender 4.2+ raytraced refraction settings
	try:
		material.surface_render_method = "DITHERED"
		material.use_raytrace_refraction = True
		material.thickness_mode = "SLAB"
	except:
		pass

	clear = nodes.new("ShaderNodeValue")
	clear.name = "RE2 Preview Clear-through"
	clear.outputs[0].default_value = 0.65

	blur = nodes.new("ShaderNodeValue")
	blur.name = "RE2 Frost Blur Approximation"
	blur.outputs[0].default_value = 0.35

	frost = nodes.new("ShaderNodeMath")
	frost.name = "RE2 Frost Amount"
	frost.operation = "MULTIPLY"
	frost.use_clamp = True
	links.new(texture.outputs["Alpha"], frost.inputs[0])
	frost.inputs[1].default_value = scalar("frosted_Roughness", 1.0)

	#FrostMask is unscaled
	mask = nodes.new("ShaderNodeMath")
	mask.name = "RE2 Frost Mask"
	mask.operation = "MULTIPLY"
	mask.use_clamp = True
	links.new(frost.outputs[0], mask.inputs[0])
	mask.inputs[1].default_value = 1.0
	if "FrostMask" in matInfo["textureNodeDict"]:
		maskChannels = nodes.new("ShaderNodeSeparateColor")
		links.new(matInfo["textureNodeDict"]["FrostMask"].outputs["Color"], maskChannels.inputs["Color"])
		links.new(maskChannels.outputs["Red"], mask.inputs[1])

	rough = nodes.new("ShaderNodeMath")
	rough.name = "RE2 Transmission Roughness"
	rough.operation = "MULTIPLY"
	rough.use_clamp = True
	links.new(mask.outputs[0], rough.inputs[0])
	links.new(blur.outputs[0], rough.inputs[1])

	refract = nodes.new("ShaderNodeBsdfRefraction")
	refract.name = "RE2 Frosted Transmission"
	refract.inputs["Color"].default_value = color
	refract.inputs["IOR"].default_value = 1.45
	links.new(rough.outputs[0], refract.inputs["Roughness"])

	transparent = nodes.new("ShaderNodeBsdfTransparent")
	transparent.name = "RE2 Preview Background"
	transparent.inputs["Color"].default_value = color

	background = nodes.new("ShaderNodeMixShader")
	background.name = "RE2 Background Approximation"
	links.new(clear.outputs[0], background.inputs[0])
	links.new(refract.outputs[0], background.inputs[1])
	links.new(transparent.outputs[0], background.inputs[2])

	reflection = nodes.new("ShaderNodeBsdfGlossy")
	reflection.name = "RE2 Glass Reflection"
	reflection.inputs["Color"].default_value = (1, 1, 1, 1)
	reflection.inputs["Roughness"].default_value = max(0.0, min(1.0, scalar("Roughness", 1.0)))

	fresnel = nodes.new("ShaderNodeFresnel")
	fresnel.name = "RE2 Glass Fresnel"
	fresnel.inputs["IOR"].default_value = 1.45

	if normalNode is not None:
		for node in (refract, reflection, fresnel):
			links.new(normalNode.outputs["Normal"], node.inputs["Normal"])

	surface = nodes.new("ShaderNodeMixShader")
	surface.name = "RE2 Frosted Glass Surface"
	links.new(fresnel.outputs[0], surface.inputs[0])
	links.new(background.outputs[0], surface.inputs[1])
	links.new(reflection.outputs[0], surface.inputs[2])

	return surface.outputs[0]


SF6_CMD_TYPES = (
	0xF3356698, # BlendRate
	0x982FBF08, # Roughness
	0x2C5538DD, # Metalness
	0xAC2691C9, # ColorOption
	0x123A0E06, # CustomizeColorData
)

def findSF6CMDUserPath(meshPath, cmdIndex=1, costumeIndex=None):
	if meshPath is None or ".mesh" not in meshPath:
		return None

	meshRoot = meshPath.split(".mesh", 1)[0]
	meshDir = os.path.dirname(meshRoot)
	meshCostumeDir = os.path.dirname(meshDir)
	characterDir = os.path.dirname(meshCostumeDir)
	characterName = os.path.basename(characterDir)
	meshCostumeName = os.path.basename(meshCostumeDir)

	if meshCostumeName == "000" and costumeIndex is not None:
		costumeName = str(costumeIndex).zfill(3)
	else:
		costumeName = meshCostumeName

	cmdNumber = str(cmdIndex).zfill(3)
	cmdSuffix = f"_cmd_{cmdNumber}.user.2"
	cmdDir = os.path.join(characterDir, costumeName)

	# Standard SF6 character CMD layout
	cmdBaseName = f"{characterName}_{costumeName}_cmd_{cmdNumber}.user.2"
	cmdPath = os.path.join(cmdDir, cmdBaseName)

	if os.path.isfile(cmdPath):
		return cmdPath

	# Allow alternative filenames, remain inside selected costume
	try:
		matches = sorted(entry.path for entry in os.scandir(cmdDir) if entry.is_file() and entry.name.lower().endswith(cmdSuffix.lower()))
	except OSError:
		matches = []

	if matches:
		return matches[0]

	return None

def getSF6CMDMaterialMap(cmdPath):
	materialMap = {}

	if cmdPath == None or not os.path.isfile(cmdPath):
		return materialMap

	with open(cmdPath, "rb") as file:
		data = file.read()

	rszBase = data.find(b"RSZ\x00")
	if data[:4] != b"USR\x00" or rszBase == -1:
		return materialMap

	u32 = lambda offset: struct.unpack_from("<I", data, offset)[0]
	u64 = lambda offset: struct.unpack_from("<Q", data, offset)[0]
	f32 = lambda offset: struct.unpack_from("<f", data, offset)[0]

	instanceCount = u32(rszBase + 0x0C)
	instanceInfoOffset = rszBase + u64(rszBase + 0x18)
	dataOffset = rszBase + u64(rszBase + 0x20)

	instanceTypes = [
		u32(instanceInfoOffset + index * 8)
		for index in range(instanceCount)
	]

	colorRecords = {}

	# Scan for the 5 consecutive type instances that make up one customize color entry
	for offset in range(dataOffset, len(data) - 47, 4):
		blendRef = u32(offset + 24)
		roughRef = u32(offset + 28)
		metalRef = u32(offset + 32)
		optionRef = u32(offset + 44)
		colorRef = optionRef + 1

		refs = (blendRef, roughRef, metalRef, optionRef, colorRef)
		if not all(0 < ref < instanceCount for ref in refs):
			continue
		if refs != tuple(range(blendRef, blendRef + 5)):
			continue
		if tuple(instanceTypes[ref] for ref in refs) != SF6_CMD_TYPES:
			continue

		enableFlags = (u32(offset), u32(offset + 8), u32(offset + 16), u32(offset + 36))
		if not all(flag in (0, 1) for flag in enableFlags):
			continue

		colorRecords[colorRef] = {
			"offset": offset,
			"colorEnabled": enableFlags[3],
			"color": tuple(data[offset + 40:offset + 44]),
			"blendRate": (enableFlags[0], f32(offset + 4)),
			"roughness": (enableFlags[1], f32(offset + 12)),
			"metalness": (enableFlags[2], f32(offset + 20)),
		}

	# Then match material names to their color record arrays
	for lengthOffset in range(dataOffset, len(data) - 12, 4):
		charCount = u32(lengthOffset)
		if not 1 < charCount < 256:
			continue

		nameOffset = lengthOffset + 4
		stringEnd = nameOffset + charCount * 2
		arrayOffset = (stringEnd + 3) & ~3

		if arrayOffset + 4 > len(data):
			continue
		if data[stringEnd - 2:stringEnd] != b"\x00\x00":
			continue

		colorCount = u32(arrayOffset)

		if not 1 <= colorCount <= 8:
			continue
		if arrayOffset + 4 + colorCount * 4 > len(data):
			continue

		try:
			materialName = data[nameOffset:stringEnd - 2].decode("utf-16le")
		except UnicodeDecodeError:
			continue

		if not materialName or not all(32 <= ord(char) <= 126 for char in materialName):
			continue

		colorRefs = [u32(arrayOffset + 4 + index * 4) for index in range(colorCount)]
		if all(ref in colorRecords for ref in colorRefs):
			materialMap[materialName] = [colorRecords[ref] for ref in colorRefs]

	if DEBUG_MODE:
		print(
			f"[SF6 CMD] Parsed {len(materialMap)} material clusters, "
			f"{len(colorRecords)} customize colors."
		)

	return materialMap

def sf6RGBToLinear(value):
	value = value / 255.0
	if value <= 0.04045:
		return value / 12.92
	return ((value + 0.055) / 1.055) ** 2.4

def applySF6CMDMaterial(materialName, materialMap, propDict, meshPath=None):
	colorIndexMap = (0, 1, 2, 3, 4, 5, 6, 7)

	cmdMaterialName = materialName
	matchType = "exact"

	if cmdMaterialName not in materialMap:
		caseInsensitiveMaterialMap = {
			name.casefold(): name for name in materialMap
		}

		caseMatchedName = caseInsensitiveMaterialMap.get(
			materialName.casefold()
		)

		if caseMatchedName is not None:
			cmdMaterialName = caseMatchedName
			matchType = "case-insensitive"

	records = materialMap.get(cmdMaterialName, [])

	# C. Viper's scalp is Head00 slot 4, but its intended tint comes from hair slot 0.
	#Keep this hardcoded patch isolated so it can be reverted when real data says otherwise.
	meshPathParts = {
		part.casefold()
		for part in os.path.normpath(meshPath).split(os.sep)
	} if meshPath else set()
	if (
		"esf030" in meshPathParts
		and materialName.casefold() == "esf_head00"
		and len(records) > 4
	):
		hairRecords = materialMap.get("esf_hair", [])
		if hairRecords:
			records = [record.copy() for record in records]
			records[4] = records[4].copy()
			records[4]["color"] = hairRecords[0]["color"]
			if DEBUG_MODE:
				print("[SF6 CMD] scalp: esf_Head00 slot 4 <- esf_hair slot 0")

	if records and DEBUG_MODE:
		print(f"[SF6 CMD] {matchType}: {materialName} -> {cmdMaterialName}")
	elif any(name.startswith("CustomizeColor_") for name in propDict) and DEBUG_MODE:
		print(f"[SF6 CMD] no override, using MDF defaults: {materialName}")

	for recordIndex, record in enumerate(records):
		colorIndex = colorIndexMap[recordIndex]
		colorProp = f"CustomizeColor_{colorIndex}"

		if colorProp in propDict:
			r, g, b, a = record["color"]
			propDict[colorProp].propValue = [
				sf6RGBToLinear(r),
				sf6RGBToLinear(g),
				sf6RGBToLinear(b),
				a / 255.0,
			]

		optionProps = (
			("blendRate", f"CustomizeColor_{colorIndex}_BlendRate"),
			("roughness", f"CustomizeRoughness_{colorIndex}"),
			("metalness", f"CustomizeMetal_{colorIndex}"),
		)

		for recordKey, propName in optionProps:
			enabled, value = record[recordKey]
			if enabled and propName in propDict:
				propDict[propName].propValue = [value]

#Texture types referenced via matInfo["textureNodeDict"] - statically maintained
#(previously scanned from this file at import time via getUsedTextureNodes).
#Keep in sync when adding new texture node wiring.
usedTextureSet = set([
	"BaseDielectricMap_B","BaseDielectricMap_G","BaseDielectricMap_R",
	"BaseDielectricMapBase","BaseDielectricMap1","BaseDielectricMap2",
	"NormalRoughnessMap_B","NormalRoughnessMap_G","NormalRoughnessMap_R",
	"NormalRoughnessCavityMapBase","NormalRoughnessCavityMap1","NormalRoughnessCavityMap2",
	"DetailMap","DetailMaskMap","DirtWearMap","HairOverMap","LayerMaskOcclusionMap",
	"MaskMap","OcclusionCavityTranslucentDetailMap","SecondaryBaseColorMap","SkinMap",
	"Tex2D_0","Tex_Dirty","Tex_Effect","Tex_Normal","tex_lineMusk","tex_noise",
	"FrostMask",
])

#Detail maps that use regular normal maps
legacyDetailMapGames = set(["RE2","RE2RT","RE3","RE3RT","RE7RT","DMC5","PRAG"])

alphaBlendShaderTypes = set([1,2,3,4,5,8,10,12,13])#Decal,DecalWithMetallic,DecalNRMR,Transparent,Distortion,Water,GUI,GUIMeshTransparent,ExpensiveTransparent
	
#MMTRs that use the alpha channel of ATOS or ALBA as a mask and not alpha
#It might be able to be checked in the flags instead as AlphaMaskUsed, but flags are messed up in SF6 onwards and aren't reliable indicators
maskAlphaMMTRSet = set([
	"env_1layer_common_dirt.mmtr",#RE4
	"env_2layer_common_dirt.mmtr",#RE4
	"env_3layer_common_dirt.mmtr",#RE4
	#"env_default.mmtr",#RE4
	#"character_default.mmtr",#DMC5
	"record_fur.mmtr",#RE2
	])
#Material Load Levels
albedoTypeSet = set([
	"ALBD",
	"ALBDmap",
	"BackMap",
	"BaseMap",
	"BackMap_1",
	"BaseAlphaMap",
	"BaseMetalMap",
	"BaseMetalMapArray",
	"BaseShiftMap",
	"BaseAnisoShiftMap",
	#"BaseDielectricMapBase",
	"BaseAlphaMap",
	"BaseDielectricMap",
	#Vertex Color
	#"BaseDielectricMap_B",
	#"BaseDielectricMap_G",
	#"BaseDielectricMap_R",
	"BaseMap",
	#"CloudMap",
	"CloudMap_1",
	"FaceBaseMap",
	"Face_BaseDielectricMap",
	"Moon_Tex",
	"Sky_Top_Tex",
	"RTReflectionBaseMap",
	#"IrisBaseMap"
	])
normalTypeSet = set([
	"Drop_NormalMap",
	"NRMR_NRRTMap",
	"NRMR_NRRTmap",
	"NRMmap",
	"NRRTMap",
	"NormalRoughnessMapArray",
	"NormalRoughnessOcclusionMap",
	"NormalRoughnessTranslucentMap",
	"NormalMap",
	"NormalRoughnessMap",
	"SnowNormalRoughnessMap",
	"snowNRMmap",
	"NormalReflactionMap",
	"NormalRoughnessMap",
	#"NormalRoughnessMap_R",
	"NormalRoughnessCavityMap",
	#"NormalRoughnessCavityMapBase",
	"NormalRoughnessOcclusionMap",
	"NormalRoughnessTranslucencyMap",
	"NormalRoughnessAlphaMap",
	"NormalRoughnessHeightMap",
	"NRRTMap",
	"Tex_Normal",
	"IrisNormalMap",
	"NormalOcclusionFilmMap",
	])

alphaTypeSet = set([
	"AlphaMap"
	])
cmmTypeSet = set([
	"UserColorchangeMap",
	"ColormaskMap",
	"ColorLayer_MaskMap"
	])
cmaskTypeSet = set([
	"CustomizeColor_Mask",
	"CustomizeColor_Mask2"
	])
emissionTypeSet = set([
	"EmissiveMap"
	])
albedoVertexColorTypeSet = set([
	"BaseDielectricMap_B",
	"BaseDielectricMap_G",
	"BaseDielectricMap_R",
	"MSK3",
	])
normalVertexColorTypeSet = set([
	"NormalRoughnessMap_B",
	"NormalRoughnessMap_G",
	"NormalRoughnessMap_R",
	])
NRRTTypes = set([
	"NormalRoughnessCavityMap",
	#"NormalRoughnessCavityMapBase",
	"NormalRoughnessOcclusionMap",
	"NormalRoughnessTranslucencyMap",
	"NormalRoughnessTranslucentMap",
	"NormalRoughnessAlphaMap",
	"NormalRoughnessHeightMap",
	"NormalOcclusionCavityMap",
	"NormalOcclusionFilmMap",
	"NRRTMap",
	])
ATOSTypes = set([
	"AlphaTranslucentOcclusionCavityMap",
	"AlphaTranslucentOcclusionSSSMap",
	"ATOCorASOC",
	"AlphaCavityOcclusionTranslucentMap",
	])
NAMTypes = set([
	"Stitch_NAM",
	"StitchMap",
	])
OCTDTypes = set([
	"OcclusionCavityTranslucentDetailMap",
	"OcclusionCavitySSSDetailMap",
	"OcclusionCavityMaskAnisoMap",
	])
SCOTTypes = set([
	"SSSCavityOcclusionTranslucentMap",
	])
MiscMapTypes = set([
	"SkinMap",
	"HairOverMap",
	#MHWILDS Minimap
	"Tex_Dirty",
	"tex_noise",
	"Tex_Effect",
	"Tex_Normal",
	"tex_lineMusk",
	"Tex2D_0",
	"FakeSpecular",
	"SecondaryBaseColorMap",
	#"Detail_ALBD_R",
	#"Detail_ALBD_G",
	#"Detail_ALBD_B",
	#"Detail_ALBD_A",
	
	#"Detail_NRRH_R",
	#"Detail_NRRH_G",
	#"Detail_NRRH_B",
	#"Detail_NRRH_A",
	])

usedTextureSet.update(albedoVertexColorTypeSet)
usedTextureSet.update(normalVertexColorTypeSet)
usedTextureSet.update(albedoTypeSet)
usedTextureSet.update(cmmTypeSet)
usedTextureSet.update(cmaskTypeSet)
usedTextureSet.update(emissionTypeSet)
usedTextureSet.update(normalTypeSet)
usedTextureSet.update(alphaTypeSet)
usedTextureSet.update(ATOSTypes)
usedTextureSet.update(OCTDTypes)
usedTextureSet.update(SCOTTypes)
usedTextureSet.update(NAMTypes)
usedTextureSet.update(MiscMapTypes)

#print(sorted(list(usedTextureSet)))
baseUVTilingList = set([#Node types that use UV_Tiling property
	"BaseDielectricMapBase",
	"NormalRoughnessCavityMapBase",
])


from ..gen_functions import raiseWarning,getBit,wildCardFileSearch
from ..game_versions import mdfVersionDict, texVersionDict, gameTexVersionDict
from .file_re_mdf import readMDF,getMDFVersionToGameName
from ..tex.blender_re_tex import loadTex
from .blender_nodes_re_mdf import addImageNode,addTextureNode,addPropertyNode,dynamicColorMixLayerNodeGroup,getBentNormalNodeGroup,getDualUVMappingNodeGroup,getMHWildsSkinMappingNodeGroup,setDualUVTilingDefault
from ..ddsconv.directx.texconv import Texconv
DEBUG_MODE = False
def debugprint(string):
	if DEBUG_MODE:
		print(string)

ADDON_NAME = __package__.split(".")[0]
def getChunkPathList(gameName):
	
	ADDON_PREFERENCES = bpy.context.preferences.addons[ADDON_NAME].preferences
	#print(gameName)
	chunkPathList = [bpy.path.abspath(item.path) for item in ADDON_PREFERENCES.chunkPathList_items if item.gameName == gameName ]
	#print(chunkPathList)
	return chunkPathList

def addChunkPath(chunkPath,gameName):

	ADDON_PREFERENCES = bpy.context.preferences.addons[ADDON_NAME].preferences
	item = ADDON_PREFERENCES.chunkPathList_items.add()
	item.gameName = gameName
	item.path = chunkPath
	print(f"Saved chunk path for {gameName}: {chunkPath}")
	 

def findMDFPathFromMeshPath(meshPath,gameName = None):
	#TODO fix this to be less of a mess
	#Should use regex to do this
	split = meshPath.split(".mesh")
	fileRoot = glob.escape(split[0])
	# Take the version from the last dot of the filename (handles e.g. .mesh_.221108797)
	meshVersion = os.path.splitext(meshPath)[1]
	mdfVersion = mdfVersionDict.get(meshVersion,None)
	if mdfVersion == None:#Allow for importing of mdfs that haven't had support added for them yet
		raiseWarning(f"Attempting to import unknown mdf version (parsed mesh version: {meshVersion}). Falling back to wildcard file search.")	
		mdfPath = wildCardFileSearch(f"{fileRoot}.mdf2.*")
		if mdfPath == None:
			mdfPath = wildCardFileSearch(f"{fileRoot}_Mat.mdf2.*")
		if mdfPath == None:
			mdfPath = wildCardFileSearch(f"{fileRoot}_v00.mdf2.*")
		if mdfPath == None and fileRoot.endswith("_f"):
			
			mdfPath = wildCardFileSearch(f"{fileRoot[:-1] + 'm'}.mdf2.*")#DD2 female armor uses male mdf, so replace _f with _m
	else:	
		mdfPath = f"{fileRoot}.mdf2{mdfVersion}"
		if not os.path.isfile(mdfPath):
			print(f"Could not find {fname(mdfPath)}.\n Trying alternate mdf names...")
			mdfPath = f"_{fileRoot}.mdf2{mdfVersion}"
		if not os.path.isfile(mdfPath):
			print(f"Could not find {fname(mdfPath)}.\n Trying alternate mdf names...")
			mdfPath = f"{fileRoot}_Mat.mdf2{mdfVersion}"
		if not os.path.isfile(mdfPath):
			print(f"Could not find {fname(mdfPath)}.\n Trying alternate mdf names...")
			mdfPath = f"{fileRoot}.mdf2_{mdfVersion}"
		if not os.path.isfile(mdfPath):
			print(f"Could not find {fname(mdfPath)}.\n Trying alternate mdf names...")
			mdfPath = f"{fileRoot}.mdf2_o{mdfVersion}"
		
		if gameName == "RE9" and not os.path.isfile(mdfPath):
			mdfPath = f"{fileRoot}_00.mdf2{mdfVersion}"
			if not os.path.isfile(mdfPath):
				print(f"Could not find {fname(mdfPath)}.\n Trying alternate mdf names...")
				mdfPath = f"{fileRoot}_01.mdf2{mdfVersion}"
			if not os.path.isfile(mdfPath):
				print(f"Could not find {fname(mdfPath)}.\n Trying alternate mdf names...")
				mdfPath = f"{fileRoot}_02.mdf2{mdfVersion}"
		
		if not os.path.isfile(mdfPath) and fileRoot.endswith("_f"):
			mdfPath = f"{fileRoot[:-1] + 'm'}.mdf2{mdfVersion}"#DD2 female armor uses male mdf, so replace _f with _m
		
		if not os.path.isfile(mdfPath) and os.path.split(fileRoot)[1].startswith("SM_"):
			split = os.path.split(fileRoot)
			mdfPath = f"{os.path.join(split[0],split[1][1::])}.mdf2{mdfVersion}"#DR Stage meshes, SM_ to M_
			
		if not os.path.isfile(mdfPath) and "wcs" in fileRoot:#SF6 world tour models
			split = os.path.split(fileRoot)
			mdfPath = os.path.join(split[0],"00",split[1]+f"_00_v00.mdf2{mdfVersion}")
		
		if not os.path.isfile(mdfPath) and meshVersion == ".2102020001" and "sk_" in fileRoot.lower():
			split = os.path.split(meshPath)
			rootPath = split[0]
			fileName = split[1].split(".mesh")[0].lower().replace("sk_","m_")
			mdfPath = os.path.join(rootPath,"Material",f"{fileName}.mdf2{mdfVersion}")
		
		try:
			if not os.path.isfile(mdfPath) and gameName != None:
				split = os.path.split(meshPath)
				rootPath = split[0]
				fileName = split[1].split(".mesh")[0].lower()
				if gameName == "MHWILDS":
					
					if not os.path.isfile(mdfPath) and fileName.startswith("ch"):
						dirSplit = re.split("Character",rootPath,1,flags=re.IGNORECASE)
						
						if fileName.count("_") == 2 and len(dirSplit) == 2:#Some models use materials from other gender
							isMale = "_000_" in fileName
							
							if isMale:
								newDir = dirSplit[1].replace("000","001",1)
								newFileName = fileName.replace("_000_", "_001_")
							else:
								newDir = dirSplit[1].replace("001","000",1)
								newFileName = fileName.replace("_001_", "_000_")
							mdfPath = os.path.join(dirSplit[0]+"Character"+newDir,f"{newFileName}.mdf2{mdfVersion}")
					if not os.path.isfile(mdfPath) and fileName.startswith("mesh_") and "ui_mesh" in rootPath and fileName.count("_") == 3:
						#Minimap textures
						split = fileName.split("_")
						stageID = split[1]
						section = split[3]
						newDir = os.path.join(os.path.dirname(rootPath),f"{stageID}_a00").replace("ui_mesh","ui_material",1)
						newFileName = f"mat_{stageID}_{section}.mdf2{mdfVersion}"
						mdfPath = os.path.join(newDir,newFileName)
						if not os.path.isfile(mdfPath):
							newFileName = f"mat_{stageID}_00.mdf2{mdfVersion}"
							mdfPath = os.path.join(newDir,newFileName)
		except:
			pass
	if mdfPath == None or not os.path.isfile(mdfPath):
		print(f"Could not find {fname(mdfPath)}.")
		mdfPath = None
			
	return mdfPath

_dirListingCache = {}


def _listTexDir(dirPath):
	"""Cached directory listing; avoids re-enumerating the same dir per texture."""
	entry = _dirListingCache.get(dirPath)
	if entry is None:
		try:
			entry = frozenset(os.listdir(dirPath))
		except OSError:
			entry = frozenset()
		if len(_dirListingCache) >= 512:  # simple memory guard
			_dirListingCache.clear()
		_dirListingCache[dirPath] = entry
	return entry


def getTexPath(baseTexturePath,chunkPathList,mdfVersion,gameName=None):
	inputPath = None
	texVersion = gameTexVersionDict.get(gameName, texVersionDict.get(mdfVersion,""))
	exactName = baseTexturePath + f".tex{texVersion}"
	exactLower = exactName.lower() if IS_WINDOWS else exactName
	#Fallback prefix: accept any .tex<N> revision when the version-specific name
	#misses (unknown/unlisted mdf tex version or a different build on disk)
	anyLower = (baseTexturePath + ".tex").lower() if IS_WINDOWS else baseTexturePath + ".tex"
	for chunkPath in chunkPathList:
		#Fast path: exact file name via a single stat, no directory enumeration
		for relDir in ("streaming",""):
			exactPath = os.path.join(chunkPath,relDir,exactName)
			if os.path.isfile(exactPath):
				return exactPath
		#Slow path: capcom can append platform/lang extensions - scan a cached directory listing once
		anyPath = None
		for relDir in ("streaming",""):
			dirPath = os.path.join(chunkPath,relDir)
			for fname in _listTexDir(dirPath):
				lname = fname.lower() if IS_WINDOWS else fname
				if lname.startswith(exactLower):
					return os.path.join(dirPath,fname)
				if anyPath is None and lname.startswith(anyLower):
					anyPath = os.path.join(dirPath,fname)
		if anyPath != None:
			return anyPath
		
		if isLinux():
			inputPath = resolveLinuxPath(os.path.join(chunkPath,"streaming",exactName))
			if inputPath == None:
				inputPath = resolveLinuxPath(os.path.join(chunkPath,exactName))
			if inputPath != None:
				break
				
	return inputPath	


def addPropertyNodeFallback(matInfo, propNames, nodeTree):
	"""Return a PropertyNode for the first propName present in matInfo's props,
	or None. Collapses the repeated if/elif fallback chains (Roughness,
	Metallic, ...) into one lookup."""
	for propName in propNames:
		if propName in matInfo["mPropDict"]:
			return addPropertyNode(matInfo["mPropDict"][propName], matInfo["currentPropPos"], nodeTree)
	return None


def connectAlphaProperty(matInfo, nodes, links, nodeTree, propName):
	"""Attach a scalar alpha property to matInfo['alphaSocket'], multiplying it
	into any existing alpha chain. Shared by the Alpha/AlphaIntensity/
	Transparent fallback chain."""
	alphaNode = addPropertyNode(matInfo["mPropDict"][propName], matInfo["currentPropPos"], nodeTree)
	if matInfo["alphaSocket"] == None:
		matInfo["alphaSocket"] = alphaNode.outputs["Value"]
	else:
		alphaMultNode = nodes.new("ShaderNodeMath")
		alphaMultNode.location = matInfo["alphaSocket"].node.location + Vector((300, 0))
		alphaMultNode.operation = "MULTIPLY"
		links.new(matInfo["alphaSocket"], alphaMultNode.inputs[0])
		links.new(alphaNode.outputs["Value"], alphaMultNode.inputs[1])
		matInfo["alphaSocket"] = alphaMultNode.outputs["Value"]


def addLayerUVMapping(matInfo, nodeTree, UVMap1Node, UVMap2Node, tilingProp, offsetProp):
	"""Wire a dual-UV mapping node group driven by a UV_TilingN property
	(layer 1/2). Returns the group node, or None if tilingProp is absent."""
	if tilingProp not in matInfo["mPropDict"]:
		return None
	tilingNode = addPropertyNode(matInfo["mPropDict"][tilingProp], matInfo["currentPropPos"], nodeTree)
	groupNode = getDualUVMappingNodeGroup(nodeTree)
	groupNode.location = tilingNode.location + Vector((-300, 0))
	nodeTree.links.new(UVMap1Node.outputs["UV"], groupNode.inputs["UV1"])
	nodeTree.links.new(UVMap2Node.outputs["UV"], groupNode.inputs["UV2"])
	nodeTree.links.new(tilingNode.outputs["Value"], groupNode.inputs["Tiling"])
	if offsetProp in matInfo["mPropDict"]:
		offsetNode = addPropertyNode(matInfo["mPropDict"][offsetProp], matInfo["currentPropPos"], nodeTree)
		nodeTree.links.new(offsetNode.outputs[0], groupNode.inputs["OffsetX"])
		nodeTree.links.new(offsetNode.outputs[1], groupNode.inputs["OffsetY"])
	return groupNode


def wireDualUVMapping(matInfo, nodeTree, UVMap1Node, UVMap2Node, textureNode,
                       useSecondaryUVProp, tilingProp=None, offsetProp=None):
	"""Wire a dual-UV mapping group feeding textureNode's Vector input.
	Shared by the LayerMaskOcclusionMap / DirtWearMap wirings (previously two
	hand-rolled copies); optional tiling/offset props drive Tiling/Offset."""
	groupNode = getDualUVMappingNodeGroup(nodeTree)
	groupNode.location = textureNode.location + Vector((-300, 0))
	setDualUVTilingDefault(groupNode)
	nodeTree.links.new(UVMap1Node.outputs["UV"], groupNode.inputs["UV1"])
	nodeTree.links.new(UVMap2Node.outputs["UV"], groupNode.inputs["UV2"])
	if useSecondaryUVProp in matInfo["mPropDict"]:
		useUV2Node = addPropertyNode(matInfo["mPropDict"][useSecondaryUVProp], matInfo["currentPropPos"], nodeTree)
		nodeTree.links.new(useUV2Node.outputs["Value"], groupNode.inputs["UseSecondaryUV"])
	if tilingProp is not None and tilingProp in matInfo["mPropDict"]:
		tilingNode = addPropertyNode(matInfo["mPropDict"][tilingProp], matInfo["currentPropPos"], nodeTree)
		nodeTree.links.new(tilingNode.outputs["Value"], groupNode.inputs["Tiling"])
	if offsetProp is not None and offsetProp in matInfo["mPropDict"]:
		offsetNode = addPropertyNode(matInfo["mPropDict"][offsetProp], matInfo["currentPropPos"], nodeTree)
		nodeTree.links.new(offsetNode.outputs[0], groupNode.inputs["OffsetX"])
		nodeTree.links.new(offsetNode.outputs[1], groupNode.inputs["OffsetY"])
	nodeTree.links.new(groupNode.outputs["Vector"], textureNode.inputs["Vector"])
	return groupNode


def importMDF(mdfFile,meshMaterialDict,loadUnusedTextures,loadUnusedProps,useBackfaceCulling,reloadCachedTextures,chunkPath = "",gameName = None,arrangeNodes = False,meshPath=None,sf6CmdIndex=0,sf6CostumeIndex=1):
	TEXTURE_CACHE_DIR = bpy.context.preferences.addons[ADDON_NAME].preferences.textureCachePath
	USE_DDS = bpy.context.preferences.addons[ADDON_NAME].preferences.useDDS == True and bpy.app.version >= (4,2,0)
	
	inErrorState = False
	loadedImageDict = dict()
	errorFileSet = set()
	texPathCache = dict()  # baseTexturePath -> resolved tex path; avoids re-globbing shared textures
	mdfVersion = mdfFile.fileVersion
	if gameName == None:
		gameName = getMDFVersionToGameName(mdfVersion)
	mdfMaterialDict = mdfFile.getMaterialDict()
	#if not os.path.isdir(chunkPath):
		#raiseWarning("Natives path not found, can't import textures")
		#raise Exception
	
	sf6CMDMaterialMapList = []

	if gameName == "SF6":
		#The base cmd_000 file holds the default costume colors, the selected cmd overrides them
		sf6CMDBasePath = findSF6CMDUserPath(meshPath, 0, sf6CostumeIndex)

		if sf6CMDBasePath is not None:
			print(f"[SF6 CMD] Using base {sf6CMDBasePath}")
			sf6CMDMaterialMapList.append(getSF6CMDMaterialMap(sf6CMDBasePath))
		else:
			print("[SF6 CMD] No base cmd_000 user file found")

		if sf6CmdIndex != 0:
			sf6CMDSelectedPath = findSF6CMDUserPath(meshPath, sf6CmdIndex, sf6CostumeIndex)

			if sf6CMDSelectedPath is not None:
				print(f"[SF6 CMD] Using selected {sf6CMDSelectedPath}")
				sf6CMDMaterialMapList.append(getSF6CMDMaterialMap(sf6CMDSelectedPath))
			else:
				print(f"[SF6 CMD] No selected cmd_{sf6CmdIndex:03d} user file found")

	chunkPathList = [chunkPath]
	chunkPathList.extend(getChunkPathList(gameName))
	if bpy.context.preferences.addons[ADDON_NAME].preferences.saveChunkPaths == True:
		if ("re_chunk_000" in chunkPath or "re_dlc_stm" in chunkPath) and not (len(chunkPathList) > 1 and chunkPath in chunkPathList[1::]) and gameName != -1:	 
			addChunkPath(chunkPath,gameName)
	#print(chunkPathList)
	texConv = Texconv()

	for materialName in meshMaterialDict.keys():
		#print(materialName)
		blenderMaterial = meshMaterialDict[materialName]
		blenderMaterial.use_nodes = True
		blenderMaterial.blend_method = "HASHED"#Blender 4.2 removed clip and opaque blend options, so everything has to be hash or blend
		if bpy.app.version < (4,2,0):
			blenderMaterial.shadow_method = "HASHED"
		blenderMaterial.node_tree.nodes.clear()
		mdfMaterial = mdfMaterialDict.get(materialName,None)
		textureNodeInfoList = []
		if mdfMaterial != None:
			# Three-point alpha gating (ported from REME v0.73): AlphaMaskUsed in
			# the flags word is only reliable below MDF v31 - v31+ (SF6+) reports
			# alpha through flagsB.AlphaUsed instead.
			hasAlpha = mdfMaterial.flags.flagValues.BaseAlphaTestEnable or mdfMaterial.flags.flagValues.AlphaTestEnable
			if mdfVersion < 31:
				hasAlpha = hasAlpha or mdfMaterial.flags.flagValues.AlphaMaskUsed
			else:
				try:
					hasAlpha = hasAlpha or mdfMaterial.flagsB.flagValues.AlphaUsed
				except AttributeError:
					pass
			if mdfMaterial.ver32Unkn0 == 1:  # vestigial: ver32Unkn0 is never parsed, upstream REME keeps the same dead check
				hasAlpha = True
			hasVertexColor = False
			#Get paths/convert textures
			#Detect albedo texture if one isn't found by the type name
			detectedAlbedo = False
			for textureEntry in mdfMaterial.textureList:
				if textureEntry.textureType in albedoTypeSet:
					detectedAlbedo = True
					break
				
			for textureEntry in mdfMaterial.textureList:
				textureType = textureEntry.textureType
				texture = textureEntry.texturePath
				autoDetectedAlbedo = False
				if not detectedAlbedo:
					if "_ALB" in texture:
						autoDetectedAlbedo = True
						detectedAlbedo = True
				if loadUnusedTextures or textureType in usedTextureSet or autoDetectedAlbedo:
					baseTexturePath = texture.replace("@","").replace(".tex","").replace('/',os.sep)
					outputPath = os.path.join(TEXTURE_CACHE_DIR,baseTexturePath+".png")
					
					if baseTexturePath in texPathCache:
						texPath = texPathCache[baseTexturePath]
					else:
						texPath = getTexPath(baseTexturePath,chunkPathList,mdfVersion,gameName)
						texPathCache[baseTexturePath] = texPath
					if texPath != None:
						if texPath not in loadedImageDict:
							try:
								if texPath not in errorFileSet:
									imageList = loadTex(texPath,outputPath,texConv,reloadCachedTextures,USE_DDS)
									loadedImageDict[texPath] = imageList
									#print(imageList)
							except Exception as err:
								imageList = [None]
								loadedImageDict[texPath] = imageList
								errorFileSet.add(texPath)
								
								raiseWarning(f"An error occured while attempting to convert {texPath} - {str(err)}")
						else:
							imageList = loadedImageDict[texPath]
					else:
						imageList = [None]  # always define; avoids reusing a stale list from a previous iteration
						if texture not in errorFileSet:
							raiseWarning("Could not find texture: " + texture + ", skipping...")
							errorFileSet.add(texture)
					#if os.path.exists(outputPath):
						#Determine what node to create for this texture
					
					if textureType in albedoVertexColorTypeSet or textureType in normalVertexColorTypeSet:
						hasVertexColor = True
					if textureType in albedoTypeSet:
						if textureType == "BaseDielectricMap" or textureType == "BaseDielectricMapBase":
							textureNodeInfoList.append(("ALBD",textureType,imageList,outputPath))
						elif textureType == "BaseMetalMap" or textureType == "BaseMetalMapArray":
							textureNodeInfoList.append(("ALBM",textureType,imageList,outputPath))
						elif textureType == "BaseAlphaMap":
							textureNodeInfoList.append(("ALBA",textureType,imageList,outputPath))
							#hasAlpha = True
							#Capcom uses this type for things other than alpha :/
						else:
							textureNodeInfoList.append(("ALB",textureType,imageList,outputPath))
						detectedAlbedo = True
					elif textureType in normalTypeSet:
						if textureType == "NormalRoughnessMap":
							textureNodeInfoList.append(("NRMR",textureType,imageList,outputPath))
						elif textureType in NRRTTypes:
							textureNodeInfoList.append(("NRRT",textureType,imageList,outputPath))
						elif textureType == "NRMR_NRRTMap":
							
							if "_nrrt" in baseTexturePath.lower():
								textureNodeInfoList.append(("NRRT",textureType,imageList,outputPath))
							else:
								textureNodeInfoList.append(("NRMR",textureType,imageList,outputPath))
						elif textureType == "NormalMap":
							textureNodeInfoList.append(("NRM",textureType,imageList,outputPath))
						else:
							textureNodeInfoList.append(("NRMR",textureType,imageList,outputPath))
					elif textureType in alphaTypeSet:
						textureNodeInfoList.append(("ALP",textureType,imageList,outputPath))
						hasAlpha = True
					elif textureType in cmmTypeSet:
						textureNodeInfoList.append(("CMM",textureType,imageList,outputPath))
					elif textureType in cmaskTypeSet:
						textureNodeInfoList.append(("CMASK",textureType,imageList,outputPath))
					elif textureType in emissionTypeSet:
						textureNodeInfoList.append(("EMI",textureType,imageList,outputPath))
					elif textureType in NAMTypes:
						textureNodeInfoList.append(("NAM",textureType,imageList,outputPath))
					elif textureType in ATOSTypes:
						textureNodeInfoList.append(("ATOS",textureType,imageList,outputPath))
					
					elif textureType in OCTDTypes:
						textureNodeInfoList.append(("OCTD",textureType,imageList,outputPath))
					elif textureType in SCOTTypes:
						textureNodeInfoList.append(("SCOT",textureType,imageList,outputPath))
					elif autoDetectedAlbedo:
						textureNodeInfoList.append(("ALB",textureType,imageList,outputPath))
					
					elif textureType == "MaskMap" and gameName == "ONI2" and "srm_msk3" in baseTexturePath.lower():
						textureNodeInfoList.append(("SRM",textureType,imageList,outputPath))
					else:
						textureNodeInfoList.append(("UNKN",textureType,imageList,outputPath))
						# print(os.path.join(chunkPath, nativesRoot, baseTexturePath + ".tex" + textureVersion))
		
			nodeMaterialOutput = blenderMaterial.node_tree.nodes.new('ShaderNodeOutputMaterial')
			nodeMaterialOutput.location = (800,0)
			#if materialLoadLevel == "1":
			#	nodeBSDF = blenderMaterial.node_tree.nodes.new('ShaderNodeBsdfDiffuse')
			#else:
			nodeBSDF = blenderMaterial.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
			
			nodeBSDF.location = (400,0)
			blenderMaterial.node_tree.links.new(nodeBSDF.outputs[0],nodeMaterialOutput.inputs[0])
			if gameName == "RE2RT" and os.path.basename(mdfMaterial.mmtrPath).lower() == "glass_frosted.mmtr":
				#RE2 RT frosted glass preview: transmission is built later, in buildRE2FrostedGlass
				if "Transmission Weight" in nodeBSDF.inputs:
					nodeBSDF.inputs["Transmission Weight"].default_value = 1.0
				try:
					blenderMaterial.surface_render_method = "DITHERED"
					blenderMaterial.use_raytrace_refraction = True
					blenderMaterial.thickness_mode = "SLAB"
				except:
					pass
				glassColor = mdfMaterial.getPropertyDict().get("Glass_Color")
				if glassColor is not None and len(glassColor.propValue) == 4:
					nodeBSDF.inputs["Base Color"].default_value = tuple(glassColor.propValue)
			if "eyetear" in mdfMaterial.mmtrPath.lower():
				#RE9 eyetear: full blend, no shadow casting, base alpha hidden (ported from REME v0.73)
				#blend_method/shadow_method were removed in Blender 4.2+, guard both
				try:
					blenderMaterial.blend_method = "BLEND"
					blenderMaterial.shadow_method = "NONE"
				except:
					pass
				nodeBSDF.inputs["Alpha"].default_value = 0.0
			currentYPos = 800
			currentXPos = -1900
			currentPropPos = [-2200,2000]
			if arrangeNodes:
				currentPropPos = [-3000,2000]
				currentXPos = -2000
			matInfo = {
				"mmtrName":os.path.split(mdfMaterial.mmtrPath.lower())[1],
				"flags":mdfMaterial.flags.flagValues,
				"textureNodeDict":{},
				"mPropDict":mdfMaterial.getPropertyDict(),
				"currentPropPos":currentPropPos,
				"blenderMaterial":blenderMaterial,
				"albedoNodeLayerGroup":dynamicColorMixLayerNodeGroup(blenderMaterial.node_tree),
				"normalNodeLayerGroup":dynamicColorMixLayerNodeGroup(blenderMaterial.node_tree),
				"roughnessNodeLayerGroup":dynamicColorMixLayerNodeGroup(blenderMaterial.node_tree),
				"metallicNodeLayerGroup":dynamicColorMixLayerNodeGroup(blenderMaterial.node_tree),
				"emissionColorNodeLayerGroup":dynamicColorMixLayerNodeGroup(blenderMaterial.node_tree),
				"emissionStrengthNodeLayerGroup":dynamicColorMixLayerNodeGroup(blenderMaterial.node_tree),
				"cavityNodeLayerGroup":dynamicColorMixLayerNodeGroup(blenderMaterial.node_tree),
				"aoNodeLayerGroup":dynamicColorMixLayerNodeGroup(blenderMaterial.node_tree),
				"alphaSocket":None,
				"translucentSocket":None,
				"subsurfaceSocket":None,
				"sheenSocket":None,
				"detailColorSocket":None,
				"detailNormalSocket":None,
				"detailRoughnessSocket":None,
				"detailMetallicSocket":None,
				"anisoSocket":None,#Not implemented yet
				"isDielectric":False,
				"isNRRT":False,
				"disableAO":False,
				"disableShadowCast":False,
				"isMaskAlphaMMTR": os.path.split(mdfMaterial.mmtrPath)[1].lower() in maskAlphaMMTRSet,
				"isAlphaBlend":False,
				"shaderType":mdfMaterial.shaderType,
				"currentShaderOutput":nodeBSDF.outputs["BSDF"],
				"gameName":gameName,
				}
			#print(gameName)
			if gameName == "SF6":
				for sf6CMDMaterialMap in sf6CMDMaterialMapList:
					applySF6CMDMaterial(materialName, sf6CMDMaterialMap, matInfo["mPropDict"], meshPath)
			nodes = blenderMaterial.node_tree.nodes
			links = blenderMaterial.node_tree.links
			nodeTree = blenderMaterial.node_tree
			
			UVMap1Node = nodes.new("ShaderNodeUVMap")
			UVMap1Node.name = "UVMap1Node"
			UVMap1Node.location = (-800,900)
			UVMap1Node.uv_map = "UVMap0"
			
			UVMap2Node = nodes.new("ShaderNodeUVMap")
			UVMap2Node.name = "UVMap2Node"
			UVMap2Node.location = (-800,600)
			UVMap2Node.uv_map = "UVMap1"
			
			blenderMaterial.use_backface_culling = useBackfaceCulling and not (matInfo["flags"].BaseTwoSideEnable == 1 or matInfo["flags"].TwoSideEnable == 1)
			
			arrayImageNodeList = []
			
			if matInfo["shaderType"] in alphaBlendShaderTypes:
				matInfo["isAlphaBlend"] = True
				if bpy.app.version < (4,2,0):
					blenderMaterial.shadow_method = "NONE"
			elif matInfo["mmtrName"] == "env_decal.mmtr":
				matInfo["isAlphaBlend"] = True
			
			elif "blend" in matInfo["mmtrName"]:
				matInfo["isAlphaBlend"] = True
			
					
			for (_,textureType,imageList,texturePath) in textureNodeInfoList:
				try:
					newNode = addImageNode(blenderMaterial.node_tree,textureType,imageList,texturePath,(currentXPos,currentYPos))
					currentYPos += 350
					#print(newNode)
	
					matInfo["textureNodeDict"][textureType] = newNode
					
					if newNode.bl_idname == "ShaderNodeGroup":
						
						if newNode.node_tree.name == "ImagePassThroughNodeGroup":
							arrayImageNodeList.append(newNode.name)
					
				except Exception as err:
					raiseWarning(f"Failed to create {textureType} node on {materialName}: {str(err)}")
			
			try:
				for (nodeType,textureType,_,_) in textureNodeInfoList:
					# Wrinkles must be added after all regular normal layers
					if nodeType not in {"UNKN", "SF6WRINKLE"} and textureType in nodes:
						addTextureNode(blenderMaterial.node_tree, nodeType, textureType, matInfo)

				if gameName == "SF6" and "FacialWrinkleMap" in matInfo["textureNodeDict"]:
					addTextureNode(blenderMaterial.node_tree, "SF6WRINKLE", "FacialWrinkleMap", matInfo)
			except Exception as err:
				raiseWarning(f"Material Importing Failed ({str(materialName)}). Error During Texture Node Assignment.\nIf you're on the latest version of RE Mesh Editor, please report this error.")
				traceback.print_exception(type(err), err, err.__traceback__)
				inErrorState = True
			
			try:#Detect nodes for game specific setups

				#RE2RT Glass_Frosted scales only the normal/roughness texture
				if gameName == "RE2RT" and matInfo["mmtrName"] == "glass_frosted.mmtr":
					glassTexture = matInfo["textureNodeDict"].get("NormalRoughnessMap")
					glassUVScale = matInfo["mPropDict"].get("UV_Scale")
					if glassTexture is not None and glassUVScale is not None and glassUVScale.propValue:
						glassMapping = nodes.new("ShaderNodeVectorMath")
						glassMapping.operation = "SCALE"
						glassMapping.name = "RE2 Frosted Glass UV Scale"
						glassMapping.label = "Glass UV_Scale"
						glassMapping.inputs["Scale"].default_value = float(glassUVScale.propValue[0])
						glassMapping.location = glassTexture.location - Vector((220, 0))
						links.new(UVMap1Node.outputs["UV"], glassMapping.inputs[0])
						links.new(glassMapping.outputs["Vector"], glassTexture.inputs["Vector"])

				#WILDS
				if "SkinMap" in matInfo["textureNodeDict"] and matInfo["gameName"] == "MHWILDS":
					skinMapNode = matInfo["textureNodeDict"]["SkinMap"]
					
					skinMappingGroupNode = getMHWildsSkinMappingNodeGroup(nodeTree)
					skinMappingGroupNode.location = skinMapNode.location - Vector((300,0))
					#nodeTree.links.new(UVMap1Node.outputs["UV"],skinMappingGroupNode.inputs["UV"])
					
					nodeTree.links.new(skinMappingGroupNode.outputs["Vector"],skinMapNode.inputs["Vector"])
					overlaySkinNode = nodes.new("ShaderNodeMixRGB")
					overlaySkinNode.location = skinMapNode.location + Vector((300,0))
					overlaySkinNode.blend_type = "OVERLAY"
					overlaySkinNode.inputs["Fac"].default_value = 1.0
					links.new(skinMapNode.outputs["Color"],overlaySkinNode.inputs["Color1"])
					if matInfo["albedoNodeLayerGroup"].currentOutSocket != None:
						links.new(matInfo["albedoNodeLayerGroup"].currentOutSocket,overlaySkinNode.inputs["Color2"])
						matInfo["albedoNodeLayerGroup"].currentOutSocket = overlaySkinNode.outputs["Color"]
						
					#matInfo["albedoNodeLayerGroup"].addMixLayer(skinMapNode.outputs["Color"],factorOutSocket = None,mixType = "OVERLAY",mixFactor = 1.0)
					
				if "HairOverMap" in matInfo["textureNodeDict"]:
					nodeTree.links.new(UVMap2Node.outputs["UV"],matInfo["textureNodeDict"]["HairOverMap"].inputs["Vector"])
					useHairOverFactorSocket = None
					hairOverOutSocket = matInfo["textureNodeDict"]["HairOverMap"].outputs["Color"]
					
					if "UseHairOverMap" in matInfo["mPropDict"]:
						useHairOverFactorSocket = addPropertyNode(matInfo["mPropDict"]["UseHairOverMap"], matInfo["currentPropPos"], nodeTree).outputs["Value"]
					if "HairOverColorA" in matInfo["mPropDict"] and "HairOverColorB" in matInfo["mPropDict"]:
						hairOverANode = addPropertyNode(matInfo["mPropDict"]["HairOverColorA"], matInfo["currentPropPos"], nodeTree)
						
						hairOverBNode = addPropertyNode(matInfo["mPropDict"]["HairOverColorB"], matInfo["currentPropPos"], nodeTree)
						
						mixHairOverNode = nodes.new("ShaderNodeMixRGB")
						mixHairOverNode.location = matInfo["textureNodeDict"]["HairOverMap"].location + Vector((300,0))
						mixHairOverNode.blend_type = "MIX"
						#The input order may need to be swapped
						links.new(hairOverANode.outputs["Color"],mixHairOverNode.inputs["Color2"])
						links.new(hairOverBNode.outputs["Color"],mixHairOverNode.inputs["Color1"])
						links.new(matInfo["textureNodeDict"]["HairOverMap"].outputs["Alpha"],mixHairOverNode.inputs["Fac"])
						
						multHairOverNode = nodes.new("ShaderNodeMixRGB")
						multHairOverNode.location = mixHairOverNode.location + Vector((300,0))
						multHairOverNode.blend_type = "MULTIPLY"
						links.new(mixHairOverNode.outputs["Color"],multHairOverNode.inputs["Color1"])
						links.new(matInfo["textureNodeDict"]["HairOverMap"].outputs["Color"],multHairOverNode.inputs["Color2"])
						multHairOverNode.inputs["Fac"].default_value = 1.0
						hairOverOutSocket = multHairOverNode.outputs["Color"]
					#TODO Look into correct way to apply hair over, alpha is used to blend through two hairover colors, also input order may need to be changed. Alma's hair does not display correctly with inputs not swapped, but swapping causes issues on things without hairover.
					matInfo["albedoNodeLayerGroup"].addMixLayer(hairOverOutSocket,factorOutSocket = useHairOverFactorSocket,mixType = "MULTIPLY",mixFactor = 1.0,swapInputs = False)
				#Base layer overrides
				roughnessNode = addPropertyNodeFallback(matInfo, ["Roughness", "Roughness_Param", "RoughnessParam"], nodeTree)
				if roughnessNode != None:
					matInfo["roughnessNodeLayerGroup"].addMixLayer(roughnessNode.outputs["Value"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
				
				
				#Tiling
				if "UV_Tiling" in matInfo["mPropDict"]:
					uvTilingNode = addPropertyNode(matInfo["mPropDict"]["UV_Tiling"], matInfo["currentPropPos"], nodeTree)
					
					uvMappingGroupNode = getDualUVMappingNodeGroup(nodeTree)
					uvMappingGroupNode.location = uvTilingNode.location + Vector((300,0))
					nodeTree.links.new(UVMap1Node.outputs["UV"],uvMappingGroupNode.inputs["UV1"])
					nodeTree.links.new(UVMap2Node.outputs["UV"],uvMappingGroupNode.inputs["UV2"])
					
					if "Value" in uvTilingNode.outputs:
						nodeTree.links.new(uvTilingNode.outputs["Value"],uvMappingGroupNode.inputs["Tiling"])
					else:#Vec4
						tilingScaleXYZNode = nodes.new("ShaderNodeCombineXYZ")
						tilingScaleXYZNode.location = uvTilingNode.location + Vector((600,0))
						links.new(uvTilingNode.outputs[0],tilingScaleXYZNode.inputs["X"])
						links.new(uvTilingNode.outputs[1],tilingScaleXYZNode.inputs["Y"])
						links.new(tilingScaleXYZNode.outputs["Vector"],uvMappingGroupNode.inputs["Tiling"])
					if "UV_Tiling_Offset" in matInfo["mPropDict"]:
						uvTilingLocationNode = addPropertyNode(matInfo["mPropDict"]["UV_Tiling_Offset"], matInfo["currentPropPos"], nodeTree)
						nodeTree.links.new(uvTilingLocationNode.outputs[0],uvMappingGroupNode.inputs["OffsetX"])
						nodeTree.links.new(uvTilingLocationNode.outputs[1],uvMappingGroupNode.inputs["OffsetY"])
					
					
					for textureType in baseUVTilingList:
						if textureType in nodeTree.nodes:
							links.new(uvMappingGroupNode.outputs["Vector"],nodeTree.nodes[textureType].inputs["Vector"])
				
				#Vertex Color Processing
				if hasVertexColor:
					vertexColorNode = nodes.new("ShaderNodeVertexColor")
					vertexColorNode.layer_name = "Col"
					vertexColorNode.location=(-400,0)
					vertexColorChannelSepNode = nodes.new("ShaderNodeSeparateColor")
					vertexColorNode.location=(-150,0)
					
					links.new(vertexColorNode.outputs["Color"],vertexColorChannelSepNode.inputs["Color"])
					if matInfo["albedoNodeLayerGroup"] != None:
						for suffix,channel in (("R","Red"),("G","Green"),("B","Blue")):
							texName = "BaseDielectricMap_" + suffix
							if texName in matInfo["textureNodeDict"]:
								node = matInfo["textureNodeDict"][texName]
								matInfo["albedoNodeLayerGroup"].addMixLayer(node.outputs["Color"],vertexColorChannelSepNode.outputs[channel],mixType = "MIX")
								matInfo["metallicNodeLayerGroup"].addMixLayer(node.outputs["Alpha"],vertexColorChannelSepNode.outputs[channel],mixType = "MIX")
					if matInfo["normalNodeLayerGroup"] != None:
						for suffix,channel in (("R","Red"),("G","Green"),("B","Blue")):
							texName = "NormalRoughnessMap_" + suffix
							if texName in matInfo["textureNodeDict"]:
								node = matInfo["textureNodeDict"][texName]
								matInfo["normalNodeLayerGroup"].addMixLayer(node.outputs["Color"],vertexColorChannelSepNode.outputs[channel],mixType = "MIX")
								matInfo["roughnessNodeLayerGroup"].addMixLayer(node.outputs["Alpha"],vertexColorChannelSepNode.outputs[channel],mixType = "MIX")
				
				if "LayerMaskOcclusionMap" in matInfo["textureNodeDict"]:
					LYMONode = matInfo["textureNodeDict"]["LayerMaskOcclusionMap"]
					LYMOSepNode = nodes.new("ShaderNodeSeparateColor")
					LYMOSepNode.location = LYMONode.location + Vector((300,0))
					links.new(LYMONode.outputs["Color"],LYMOSepNode.inputs["Color"])
					matInfo["aoNodeLayerGroup"].addMixLayer(LYMONode.outputs["Alpha"],mixType = "MULTIPLY",mixFactor = 1.0)
					
					layerMaskUVMappingGroupNode = wireDualUVMapping(
						matInfo, nodeTree, UVMap1Node, UVMap2Node, LYMONode,
						"LayerMask_Use_SecondaryUV")
					#
					if "BaseAlphaMap" in nodeTree.nodes:
						#Use secondary uv on base alpha texture if it's enabled on the layer mask
						#TODO Check this in game, there's a mesh that uses secondary uv but doesn't have base secondary uv flag enabled: "RE4_EXTRACT\re_chunk_000\natives\STM\_Chainsaw\Environment\sm\sm2X\sm21\sm21_515\sm21_515_00.mesh.221108797"
						links.new(layerMaskUVMappingGroupNode.outputs["Vector"],nodeTree.nodes["BaseAlphaMap"].inputs["Vector"])
					layer1UVMappingGroupNode = addLayerUVMapping(matInfo, nodeTree, UVMap1Node, UVMap2Node, "UV_Tiling1", "UV_Tiling_Offset1")
					layer2UVMappingGroupNode = addLayerUVMapping(matInfo, nodeTree, UVMap1Node, UVMap2Node, "UV_Tiling2", "UV_Tiling_Offset2")
							
					
					#UV_Tiling2 wired via addLayerUVMapping above
						
					
					baseDielectricEntries = (
						("BaseDielectricMapBase",LYMOSepNode.outputs["Blue"],None),
						("BaseDielectricMap1",LYMOSepNode.outputs["Red"],layer1UVMappingGroupNode),
						("BaseDielectricMap2",LYMOSepNode.outputs["Green"],layer1UVMappingGroupNode),
					)
					for texName,channelSocket,uvNode in baseDielectricEntries:
						if texName in matInfo["textureNodeDict"]:
							if texName == "BaseDielectricMapBase":
								matInfo["isDielectric"] = True
							node = matInfo["textureNodeDict"][texName]
							matInfo["albedoNodeLayerGroup"].addMixLayer(node.outputs["Color"],channelSocket,mixType = "MIX")
							matInfo["metallicNodeLayerGroup"].addMixLayer(node.outputs["Alpha"],channelSocket,mixType = "MIX")
							if uvNode != None:
								links.new(uvNode.outputs["Vector"],node.inputs["Vector"])
					
					normalRoughnessEntries = (
						("NormalRoughnessCavityMapBase",LYMOSepNode.outputs["Blue"],None),
						("NormalRoughnessCavityMap1",LYMOSepNode.outputs["Red"],layer1UVMappingGroupNode),
						("NormalRoughnessCavityMap2",LYMOSepNode.outputs["Green"],layer2UVMappingGroupNode),
					)
					for texName,channelSocket,uvNode in normalRoughnessEntries:
						if texName in matInfo["textureNodeDict"]:
							node = matInfo["textureNodeDict"][texName]
							nodeGroupNode = getBentNormalNodeGroup(nodeTree)
							nodeGroupNode.location = node.location + Vector((300,0))
							nodeTree.links.new(node.outputs["Color"],nodeGroupNode.inputs["Color"])
							nodeTree.links.new(node.outputs["Alpha"],nodeGroupNode.inputs["Alpha"])
							matInfo["normalNodeLayerGroup"].addMixLayer(nodeGroupNode.outputs["Color"],channelSocket,mixType = "MIX")
							matInfo["roughnessNodeLayerGroup"].addMixLayer(nodeGroupNode.outputs["Roughness"],channelSocket,mixType = "MIX")
							matInfo["cavityNodeLayerGroup"].addMixLayer(nodeGroupNode.outputs["BlueChannel"],channelSocket,mixType = "MIX")
							if uvNode != None:
								links.new(uvNode.outputs["Vector"],node.inputs["Vector"])
				if "DirtWearMap" in matInfo["textureNodeDict"]:
					DirtWearNode = matInfo["textureNodeDict"]["DirtWearMap"]
					
					dirtMapUVMappingGroupNode = wireDualUVMapping(
						matInfo, nodeTree, UVMap1Node, UVMap2Node, DirtWearNode,
						"DirtMap_Use_SecondaryUV",
						"DirtWearMap_Tiling", "DirtWearMap_Tiling_Offset")
					#
					
					DirtWearBrightContrastNode = nodes.new("ShaderNodeBrightContrast")
					DirtWearBrightContrastNode.location = DirtWearNode.location + Vector((300,0))
					links.new(DirtWearNode.outputs["Color"],DirtWearBrightContrastNode.inputs["Color"])
					if "DirtMask_Brightness" in matInfo["mPropDict"]:
						dirtWearBrightnessNode = addPropertyNode(matInfo["mPropDict"]["DirtMask_Brightness"], matInfo["currentPropPos"], nodeTree)
						
						correctionNode = nodes.new("ShaderNodeMath")
						correctionNode.location =  DirtWearNode.location + Vector((600,0))
						correctionNode.operation = "ADD"
						correctionNode.inputs[0].default_value = -1
						
						links.new(dirtWearBrightnessNode.outputs["Value"],correctionNode.inputs[1])
						links.new(correctionNode.outputs["Value"],DirtWearBrightContrastNode.inputs["Bright"])
					if "DirtMask_Contrast" in matInfo["mPropDict"]:
						dirtWearContrastNode = addPropertyNode(matInfo["mPropDict"]["DirtMask_Contrast"], matInfo["currentPropPos"], nodeTree)
						correctionNode = nodes.new("ShaderNodeMath")
						correctionNode.location =  DirtWearNode.location + Vector((600,-300))
						correctionNode.operation = "ADD"
						correctionNode.inputs[0].default_value = 0#TODO Figure out how contrast in game differs from blender's
						
						links.new(dirtWearContrastNode.outputs["Value"],correctionNode.inputs[1])
						links.new(correctionNode.outputs["Value"],DirtWearBrightContrastNode.inputs["Contrast"])
					DirtWearHueNode = nodes.new("ShaderNodeHueSaturation")
					DirtWearHueNode.location = DirtWearNode.location + Vector((900,0))
					if "DirtColorControl" in matInfo["mPropDict"]:
						dirtWearColorControlNode = addPropertyNode(matInfo["mPropDict"]["DirtColorControl"], matInfo["currentPropPos"], nodeTree)
						links.new(dirtWearColorControlNode.outputs["Value"],DirtWearHueNode.inputs["Hue"])
					links.new(DirtWearBrightContrastNode.outputs["Color"],DirtWearHueNode.inputs["Color"])
					DirtWearSepNode = nodes.new("ShaderNodeSeparateColor")
					DirtWearSepNode.location = DirtWearNode.location + Vector((1200,0))
					links.new(DirtWearHueNode.outputs["Color"],DirtWearSepNode.inputs["Color"])
					
					dirtRoughnessNode = None
					if "Dirt_Roughness" in matInfo["mPropDict"]:
						dirtRoughnessNode = addPropertyNode(matInfo["mPropDict"]["Dirt_Roughness"], matInfo["currentPropPos"], nodeTree)
					
					
					#The color channels are BGR for some reason
					dirtColorEntries = (
						("DirtColor1",DirtWearSepNode.outputs["Blue"],DirtWearSepNode.outputs["Red"]),
						("DirtColor2",DirtWearSepNode.outputs["Green"],DirtWearSepNode.outputs["Green"]),
						("DirtColor3",DirtWearSepNode.outputs["Red"],DirtWearSepNode.outputs["Blue"]),
					)
					for propName,albSocket,roughSocket in dirtColorEntries:
						if propName in matInfo["mPropDict"]:
							node = addPropertyNode(matInfo["mPropDict"][propName],matInfo["currentPropPos"],nodeTree)
							#TODO Mix probably isn't the right operation, but others didn't look quite right either, fix
							matInfo["albedoNodeLayerGroup"].addMixLayer(node.outputs["Color"],albSocket,mixType = "MULTIPLY")
							if dirtRoughnessNode != None:
								matInfo["roughnessNodeLayerGroup"].addMixLayer(dirtRoughnessNode.outputs["Value"],roughSocket,mixType = "MULTIPLY")
					
				if matInfo["gameName"] == "RE9":
					if "ch_hair" in matInfo["mmtrName"]:
						matInfo["disableAO"] = True#TODO Figure out what's wrong with secondary UV hair AO
					if "SecondaryBaseColorMap" in matInfo["textureNodeDict"]:
						secondaryBaseColorNode = matInfo["textureNodeDict"]["SecondaryBaseColorMap"]
						matInfo["albedoNodeLayerGroup"].addMixLayer(secondaryBaseColorNode.outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
				#MHWilds detail map
				#TODO - will come back to this
				
				#RE4 detail map
				if "DetailMap" in matInfo["textureNodeDict"] and matInfo["gameName"] != "PRAG":
					detailMapNode = matInfo["textureNodeDict"]["DetailMap"]
					currentPos = [detailMapNode.location[0]+300,detailMapNode.location[1]]
					
					#R Normal X
					#G Normal Y
					#B Cavity
					#A Roughness
					MaskMapSeparateNode = None
					if "MaskMap" in matInfo["textureNodeDict"]:
						MaskMapNode = matInfo["textureNodeDict"]["MaskMap"]
						MaskMapSeparateNode = nodes.new("ShaderNodeSeparateColor")
						MaskMapSeparateNode.location = (MaskMapNode.location[0] + 300,MaskMapNode.location[1])
						links.new(MaskMapNode.outputs["Color"],MaskMapSeparateNode.inputs["Color"])
					elif "DetailMaskMap" in matInfo["textureNodeDict"]:
						MaskMapNode = matInfo["textureNodeDict"]["DetailMaskMap"]
						MaskMapSeparateNode = nodes.new("ShaderNodeSeparateColor")
						MaskMapSeparateNode.location = (MaskMapNode.location[0] + 300,MaskMapNode.location[1])
						links.new(MaskMapNode.outputs["Color"],MaskMapSeparateNode.inputs["Color"])
					elif "OcclusionCavityTranslucentDetailMap" in matInfo["textureNodeDict"]:
						MaskMapNode = matInfo["textureNodeDict"]["OcclusionCavityTranslucentDetailMap"]
						MaskMapSeparateNode = nodes.new("ShaderNodeSeparateColor")
						MaskMapSeparateNode.location = (MaskMapNode.location[0] + 300,MaskMapNode.location[1])
						links.new(MaskMapNode.outputs["Alpha"],MaskMapSeparateNode.inputs["Color"])
					if "DetailMap_Level" in matInfo["mPropDict"] and "ARRAY_DetailMap_SELECTOR" in nodes:
						detailMapLevelNode = addPropertyNode(matInfo["mPropDict"]["DetailMap_Level"], matInfo["currentPropPos"], nodeTree)
						subtractNode = nodes.new("ShaderNodeMath")
						subtractNode.location = currentPos
						subtractNode.operation = "SUBTRACT"
						links.new(detailMapLevelNode.outputs["Value"],subtractNode.inputs[0])
						subtractNode.inputs[1].default_value = 1.0
						links.new(subtractNode.outputs["Value"],nodes["ARRAY_DetailMap_SELECTOR"].inputs["Value"])
					detailMappingNode = nodes.new("ShaderNodeMapping")
					detailMappingNode.location = currentPos
					links.new(UVMap1Node.outputs["UV"],detailMappingNode.inputs["Vector"])
					links.new(detailMappingNode.outputs["Vector"],detailMapNode.inputs["Vector"])
					if "DetailMap_Tiling_Offset" in matInfo["mPropDict"]:
						tilingScaleNode = addPropertyNode(matInfo["mPropDict"]["DetailMap_Tiling_Offset"], matInfo["currentPropPos"], nodeTree)
						tilingScaleXYZNode = nodes.new("ShaderNodeCombineXYZ")
						tilingScaleXYZNode.location = (tilingScaleNode.location[0] + 300,tilingScaleNode.location[1])
						links.new(tilingScaleNode.outputs[0],tilingScaleXYZNode.inputs["X"])
						links.new(tilingScaleNode.outputs[1],tilingScaleXYZNode.inputs["Y"])
						links.new(tilingScaleXYZNode.outputs["Vector"],detailMappingNode.inputs["Scale"])
					elif "Detail_UVScale" in matInfo["mPropDict"]:
						tilingScaleNode = addPropertyNode(matInfo["mPropDict"]["Detail_UVScale"], matInfo["currentPropPos"], nodeTree)
						tilingScaleMultNode = nodes.new("ShaderNodeMath")
						tilingScaleMultNode.location = (tilingScaleNode.location[0] + 300,tilingScaleNode.location[1])
						tilingScaleMultNode.operation = "MULTIPLY"
						links.new(tilingScaleNode.outputs["Value"],tilingScaleMultNode.inputs[0])
						tilingScaleMultNode.inputs[1].default_value = 100.0					
						links.new(tilingScaleMultNode.outputs["Value"],detailMappingNode.inputs["Scale"])
					currentPos[0] += 300
					
					separateRGBNode = nodes.new("ShaderNodeSeparateColor")
					separateRGBNode.location = currentPos
					currentPos[0] += 300
					
					if matInfo["gameName"] not in legacyDetailMapGames:
					
						nodeGroupNode = getBentNormalNodeGroup(nodeTree)
						nodeGroupNode.location = currentPos
						nodeTree.links.new(separateRGBNode.outputs["Green"],nodeGroupNode.inputs["Color"])
						nodeTree.links.new(separateRGBNode.outputs["Red"],nodeGroupNode.inputs["Alpha"])
						
					else:
						nodeGroupNode = detailMapNode
					
					links.new(detailMapNode.outputs["Color"],separateRGBNode.inputs["Color"])
					currentPos[0]+=300
					normalInfluenceNode = nodes.new("ShaderNodeMath")
					normalInfluenceNode.location = currentPos
					normalInfluenceNode.operation = "MULTIPLY"
					normalInfluenceNode.inputs[0].default_value = 1.0
					
					currentPos[0] += 300
					
					detailNormalNode = nodes.new("ShaderNodeNormalMap")
					detailNormalNode.location = currentPos
					
					if MaskMapSeparateNode != None:
						multiplyNormalBlendNode = nodes.new("ShaderNodeMath")
						multiplyNormalBlendNode.location = currentPos
						multiplyNormalBlendNode.operation = "MULTIPLY"
						links.new(MaskMapSeparateNode.outputs["Red"],multiplyNormalBlendNode.inputs[0])
						links.new(multiplyNormalBlendNode.outputs["Value"],normalInfluenceNode.inputs[0])
						multiplyNormalBlendNode.inputs[1].default_value = 1.0
						normalInfluenceNode.inputs[1].default_value = 1.0
						if "Detail_NormalBlend" in matInfo["mPropDict"]:
							detailNormalBlendNode = addPropertyNode(matInfo["mPropDict"]["Detail_NormalBlend"], matInfo["currentPropPos"], nodeTree)
							links.new(detailNormalBlendNode.outputs["Value"],multiplyNormalBlendNode.inputs[1])
						elif "Detail_Normal_Intensity" in matInfo["mPropDict"]:
							detailNormalBlendNode = addPropertyNode(matInfo["mPropDict"]["Detail_Normal_Intensity"], matInfo["currentPropPos"], nodeTree)
							links.new(detailNormalBlendNode.outputs["Value"],multiplyNormalBlendNode.inputs[1])
						currentPos[0]+= 300
						
						if "Detail_RoughnessBlend" in matInfo["mPropDict"]:
							currentPos[1]-= 300
							detailRoughnessBlendNode = addPropertyNode(matInfo["mPropDict"]["Detail_RoughnessBlend"], matInfo["currentPropPos"], nodeTree)
							
							multiplyRoughnessBlendNode = nodes.new("ShaderNodeMath")
							multiplyRoughnessBlendNode.location = currentPos
							multiplyRoughnessBlendNode.operation = "MULTIPLY"
							links.new(detailRoughnessBlendNode.outputs["Value"],multiplyRoughnessBlendNode.inputs[0])
							links.new(MaskMapSeparateNode.outputs["Red"],multiplyRoughnessBlendNode.inputs[1])
							currentPos[0]+= 300
							
							roughnessDistanceMulitplyNode = nodes.new("ShaderNodeMath")
							roughnessDistanceMulitplyNode.location = currentPos
							roughnessDistanceMulitplyNode.operation = "MULTIPLY"
							links.new(multiplyRoughnessBlendNode.outputs["Value"],roughnessDistanceMulitplyNode.inputs[0])
							roughnessDistanceMulitplyNode.inputs[1].default_value = 1.0
							currentPos[0]+= 300
							
							roughnessCorrectionNode = nodes.new("ShaderNodeMath")#Can't seem to get roughness values to match in game, so an approximation is used here
							roughnessCorrectionNode.location = currentPos
							roughnessCorrectionNode.operation = "ADD"
							roughnessCorrectionNode.use_clamp = True
							links.new(detailMapNode.outputs["Alpha"],roughnessCorrectionNode.inputs[0])
							roughnessCorrectionNode.inputs[1].default_value = 0.4
							currentPos[0]+= 300
							matInfo["roughnessNodeLayerGroup"].addMixLayer(roughnessCorrectionNode.outputs["Value"],factorOutSocket = roughnessDistanceMulitplyNode.outputs["Value"],mixType = "MULTIPLY",mixFactor = 1.0)
						if "Detail_Cavity" in matInfo["mPropDict"]:
							currentPos[1]-= 300
							detailCavityNode = addPropertyNode(matInfo["mPropDict"]["Detail_Cavity"], matInfo["currentPropPos"], nodeTree)
							
							multiplyCavityNode = nodes.new("ShaderNodeMath")
							multiplyCavityNode.location = currentPos
							multiplyCavityNode.operation = "MULTIPLY"
							links.new(detailCavityNode.outputs["Value"],multiplyCavityNode.inputs[0])
							links.new(MaskMapSeparateNode.outputs["Red"],multiplyCavityNode.inputs[1])
							
							currentPos[0]+= 300
							
							cavityDistanceMulitplyNode = nodes.new("ShaderNodeMath")
							cavityDistanceMulitplyNode.location = currentPos
							cavityDistanceMulitplyNode.operation = "MULTIPLY"
							links.new(multiplyCavityNode.outputs["Value"],cavityDistanceMulitplyNode.inputs[0])
							cavityDistanceMulitplyNode.inputs[1].default_value = 1.0
							currentPos[0]+= 300
							
							matInfo["cavityNodeLayerGroup"].addMixLayer(MaskMapSeparateNode.outputs["Blue"],factorOutSocket = cavityDistanceMulitplyNode.outputs["Value"],mixType = "MULTIPLY",mixFactor = 1.0)
						
					links.new(normalInfluenceNode.outputs["Value"],detailNormalNode.inputs["Strength"])
					links.new(nodeGroupNode.outputs["Color"],detailNormalNode.inputs["Color"])
					matInfo["detailNormalSocket"] = detailNormalNode.outputs["Normal"]
				
				
				#Texture map overrides
				
				if "BaseColor" in matInfo["mPropDict"]:
					baseColorNode = addPropertyNode(matInfo["mPropDict"]["BaseColor"], matInfo["currentPropPos"], nodeTree)
					matInfo["albedoNodeLayerGroup"].addMixLayer(baseColorNode.outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
				
				if "Sheen" in matInfo["mPropDict"]:
					sheenNode = addPropertyNode(matInfo["mPropDict"]["Sheen"], matInfo["currentPropPos"], nodeTree)
					
					reduceSheenMultNode = nodeTree.nodes.new('ShaderNodeMath')
					reduceSheenMultNode.location = sheenNode.location + Vector((300,0))
					reduceSheenMultNode.operation = "MULTIPLY"
					nodeTree.links.new(sheenNode.outputs["Value"],reduceSheenMultNode.inputs[0])
					reduceSheenMultNode.inputs[1].default_value = 0.3
					
					matInfo["sheenSocket"] = reduceSheenMultNode.outputs["Value"]
				
				if "ColorParam" in matInfo["mPropDict"]:
					baseColorFactorSocket = None
					if "ColorParam_MetalMasked" in matInfo["mPropDict"]:#WILDS
						matInfo["disableAO"] = True#Disable AO to make eye not appear dark
						#MH Wilds uses dielectric as a mask for eye color
						if matInfo["metallicNodeLayerGroup"].currentOutSocket != None:
							invertNode = nodes.new("ShaderNodeInvert")
							invertNode.location = matInfo["metallicNodeLayerGroup"].currentOutSocket.node.location + Vector((300,0))
							
							links.new(matInfo["metallicNodeLayerGroup"].currentOutSocket,invertNode.inputs["Color"])
							baseColorFactorSocket = invertNode.outputs["Color"]
							
							metalMaskNode = addPropertyNode(matInfo["mPropDict"]["ColorParam_MetalMasked"], matInfo["currentPropPos"], nodeTree)
							matInfo["albedoNodeLayerGroup"].addMixLayer(metalMaskNode.outputs["Color"],factorOutSocket = matInfo["metallicNodeLayerGroup"].currentOutSocket,mixType = "MULTIPLY",mixFactor = 1.0)
							#Disable metallic since it's not supposed to be used
							matInfo["metallicNodeLayerGroup"].currentOutSocket = None
					baseColorNode = addPropertyNode(matInfo["mPropDict"]["ColorParam"], matInfo["currentPropPos"], nodeTree)
					matInfo["albedoNodeLayerGroup"].addMixLayer(baseColorNode.outputs["Color"],factorOutSocket = baseColorFactorSocket,mixType = "MULTIPLY",mixFactor = 1.0)
				#mmtr specific nodes
				#if "eye" in matInfo["mmtrName"]:
				if "eye" in matInfo["blenderMaterial"].name or "eye" in matInfo["mmtrName"] or matInfo["mmtrName"].endswith("_ao.mmtr"):
					#Tearline mat setup
					if "FakeSpecular" in matInfo["textureNodeDict"]:
							matInfo["alphaSocket"] = nodes["FakeSpecular"].outputs["Color"]#RE9 TODO Figure out eye shell rendering properly
							
					if "TearColor" in matInfo["mPropDict"]:  
						baseColorNode = addPropertyNode(matInfo["mPropDict"]["TearColor"], matInfo["currentPropPos"], nodeTree)
						matInfo["albedoNodeLayerGroup"].addMixLayer(baseColorNode.outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
						
					elif "Eyelash_Color" in matInfo["mPropDict"]:  
						baseColorNode = addPropertyNode(matInfo["mPropDict"]["Eyelash_Color"], matInfo["currentPropPos"], nodeTree)
						matInfo["albedoNodeLayerGroup"].addMixLayer(baseColorNode.outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
					
					if "TearRoughness" in matInfo["mPropDict"]:  
						roughnessNode = addPropertyNode(matInfo["mPropDict"]["TearRoughness"], matInfo["currentPropPos"], nodeTree)
						matInfo["roughnessNodeLayerGroup"].addMixLayer(roughnessNode.outputs["Value"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
					if "TearBlendRate" in matInfo["mPropDict"]:  
						alphaNode = addPropertyNode(matInfo["mPropDict"]["TearBlendRate"], matInfo["currentPropPos"], nodeTree)
						matInfo["alphaSocket"] = alphaNode.outputs["Value"]	
					if "DisappearanceRate" in matInfo["mPropDict"]:
						alphaNode = addPropertyNode(matInfo["mPropDict"]["DisappearanceRate"], matInfo["currentPropPos"], nodeTree)
						matInfo["alphaSocket"] = alphaNode.outputs["Value"]
					
					if "Alpha" in matInfo["mPropDict"] or "Transparent" in matInfo["mPropDict"] or "TearBlendRate" in matInfo["mPropDict"] or "basealpha_emit_roughtransparent" in matInfo["mmtrName"]:#Add property node for this later
						matInfo["isAlphaBlend"] = True
					
					if "BaseAlphaMap" in matInfo["textureNodeDict"]:
						node = nodes["BaseAlphaMap"]
						matInfo["alphaSocket"] = node.outputs["Alpha"]
				if matInfo["gameName"] == "MHWILDS":
					if "ColorParam" in nodes and matInfo["alphaSocket"] != None:
						alphaMultNode = nodes.new("ShaderNodeMath")
						alphaMultNode.location = nodes["ColorParam"].location + Vector((300,0))
						alphaMultNode.operation = "MULTIPLY"
						
						links.new(matInfo["alphaSocket"],alphaMultNode.inputs[0])
						links.new(nodes["ColorParam"].outputs["Alpha"],alphaMultNode.inputs[1])
						matInfo["alphaSocket"] = alphaMultNode.outputs["Value"]
					if "lens" in matInfo["blenderMaterial"].name.lower():
						matInfo["isAlphaBlend"] = True
						
				if "Alpha" in matInfo["mPropDict"] and "StitchMap" not in matInfo["textureNodeDict"]:  
					connectAlphaProperty(matInfo, nodes, links, nodeTree, "Alpha")
				elif "AlphaIntensity" in matInfo["mPropDict"]:  
					connectAlphaProperty(matInfo, nodes, links, nodeTree, "AlphaIntensity")
				elif "Transparent" in matInfo["mPropDict"]:  
					connectAlphaProperty(matInfo, nodes, links, nodeTree, "Transparent")
			
				
				# MH WILDS FIXES
				if matInfo["gameName"] == "MHWILDS":
					#Doshaguma fix
					if "MT_ShellFur" in blenderMaterial.name:
						matInfo["isAlphaBlend"] = True
						
					if matInfo["mmtrName"].startswith("mas_"):
						#minimap paper shader
						if matInfo["mmtrName"] == "mas_st000_02_00.mmtr":
							
							if "Tex_Dirty" in matInfo["textureNodeDict"]:
								
								currentMaskNode = matInfo["textureNodeDict"]["Tex_Dirty"]
								currentPos = [currentMaskNode.location[0]-300,currentMaskNode.location[1]]
								
								#Fix base color node, too bright by default
								colorAdjustNode = nodes.new("ShaderNodeRGB")
								colorAdjustNode.outputs["Color"].default_value = (0.5,0.45,0.5,1.0)
								colorAdjustNode.location = currentPos
								matInfo["albedoNodeLayerGroup"].addMixLayer(colorAdjustNode.outputs["Color"],factorOutSocket = None,mixType = "BURN",mixFactor = 1.0)
								
								currentMappingNode = nodes.new("ShaderNodeMapping")
								currentMappingNode.location = currentPos
								currentMappingNode.inputs["Scale"].default_value = (10.0,10.0,1.0)
								links.new(UVMap1Node.outputs["UV"],currentMappingNode.inputs["Vector"])
								links.new(currentMappingNode.outputs["Vector"],currentMaskNode.inputs["Vector"])
								currentPos[0] += 600
								
								currentMaskSepNode = nodes.new("ShaderNodeSeparateColor")
								currentMaskSepNode.location = currentPos
								links.new(currentMaskNode.outputs["Color"],currentMaskSepNode.inputs["Color"])
								matInfo["albedoNodeLayerGroup"].addMixLayer(currentMaskSepNode.outputs["Green"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 0.3)
								matInfo["roughnessNodeLayerGroup"].addMixLayer(currentMaskNode.outputs["Alpha"],factorOutSocket = None,mixType = "MIX",mixFactor = 1.0)
								
								#Add gradation lines
								"""
								currentPos[0] += 300
								
								geometryNode = nodes.new("ShaderNodeNewGeometry")
								geometryNode.location = currentPos
								
								currentPos[0] += 300
								sepXYZNode = nodes.new("ShaderNodeSeparateXYZ")
								sepXYZNode.location = currentPos
								links.new(geometryNode.outputs["True Normal"],sepXYZNode.inputs["Vector"])
								currentPos[0] += 300
								
								colorRampNode = nodes.new("ShaderNodeValToRGB")
								colorRampNode.location = currentPos
								colorRampNode.color_ramp.elements[1].position = 0.0
								colorRampNode.color_ramp.elements[0].position = 0.85
								links.new(sepXYZNode.outputs["Z"],colorRampNode.inputs["Fac"])
								
								matInfo["albedoNodeLayerGroup"].addMixLayer(currentMaskSepNode.outputs["Blue"],factorOutSocket = colorRampNode.outputs["Color"],mixType = "MULTIPLY",mixFactor = 0.3)
								"""
								if "tex_noise" in matInfo["textureNodeDict"]:
									currentMaskNode = matInfo["textureNodeDict"]["tex_noise"]
									currentPos = [currentMaskNode.location[0]-300,currentMaskNode.location[1]]
									
									currentMappingNode = nodes.new("ShaderNodeMapping")
									currentMappingNode.location = currentPos
									currentMappingNode.inputs["Scale"].default_value = (10.0,10.0,1.0)
									links.new(UVMap1Node.outputs["UV"],currentMappingNode.inputs["Vector"])
									links.new(currentMappingNode.outputs["Vector"],currentMaskNode.inputs["Vector"])
							
									matInfo["albedoNodeLayerGroup"].addMixLayer(currentMaskNode.outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 0.3)
								
								if "Tex_Normal" in matInfo["textureNodeDict"]:
									currentMaskNode = matInfo["textureNodeDict"]["Tex_Normal"]
									currentPos = [currentMaskNode.location[0]-300,currentMaskNode.location[1]]
									
									currentMappingNode = nodes.new("ShaderNodeMapping")
									currentMappingNode.location = currentPos
									currentMappingNode.inputs["Scale"].default_value = (10.0,10.0,1.0)
									links.new(UVMap1Node.outputs["UV"],currentMappingNode.inputs["Vector"])
									links.new(currentMappingNode.outputs["Vector"],currentMaskNode.inputs["Vector"])
							
									matInfo["albedoNodeLayerGroup"].addMixLayer(currentMaskNode.outputs["Alpha"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 0.3)
									
								if blenderMaterial.name.startswith("mainOther00"):#Shallow Water Material
									currentMaskNode = matInfo["textureNodeDict"]["Tex_Effect"]
									currentPos = [currentMaskNode.location[0]-300,currentMaskNode.location[1]]
									
									currentMappingNode = nodes.new("ShaderNodeMapping")
									currentMappingNode.location = currentPos
									currentMappingNode.inputs["Scale"].default_value = (100.0,100.0,1.0)
									links.new(UVMap1Node.outputs["UV"],currentMappingNode.inputs["Vector"])
									links.new(currentMappingNode.outputs["Vector"],currentMaskNode.inputs["Vector"])
									currentPos[0] += 600
									currentMaskSepNode = nodes.new("ShaderNodeSeparateColor")
									currentMaskSepNode.location = currentPos
									links.new(currentMaskNode.outputs["Color"],currentMaskSepNode.inputs["Color"])
									currentPos[0] += 300
									invertNode = nodes.new("ShaderNodeInvert")
									invertNode.location = currentPos
									links.new(currentMaskSepNode.outputs["Green"],invertNode.inputs["Color"])
									matInfo["albedoNodeLayerGroup"].addMixLayer(invertNode.outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 0.35)
						#minimap line shader
						elif matInfo["mmtrName"] == "mas_st000_02_02.mmtr":
							if "tex_lineMusk" in matInfo["textureNodeDict"]:
								matInfo["albedoNodeLayerGroup"].addMixLayer(matInfo["textureNodeDict"]["tex_lineMusk"].outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
								matInfo["roughnessNodeLayerGroup"].addMixLayer(matInfo["textureNodeDict"]["tex_lineMusk"].outputs["Alpha"],factorOutSocket = None,mixType = "MIX",mixFactor = 1.0)
						elif matInfo["mmtrName"] == "mas_st000_02_03.mmtr":
							if "Tex2D_0" in matInfo["textureNodeDict"]:
								matInfo["albedoNodeLayerGroup"].addMixLayer(matInfo["textureNodeDict"]["Tex2D_0"].outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
								matInfo["roughnessNodeLayerGroup"].addMixLayer(matInfo["textureNodeDict"]["Tex2D_0"].outputs["Alpha"],factorOutSocket = None,mixType = "MIX",mixFactor = 1.0)
						#minimap wall shader
						elif matInfo["mmtrName"] == "mas_st000_02_04.mmtr":
							if "Tex2D_0" in matInfo["textureNodeDict"]:
								currentMaskNode = matInfo["textureNodeDict"]["Tex2D_0"]
								currentPos = [currentMaskNode.location[0]-300,currentMaskNode.location[1]]
								
								currentMappingNode = nodes.new("ShaderNodeMapping")
								currentMappingNode.location = currentPos
								if "UV" in matInfo["mPropDict"]:
									uvPropNode = addPropertyNode(matInfo["mPropDict"]["UV"], matInfo["currentPropPos"], nodeTree)
									links.new(uvPropNode.outputs[0],currentMappingNode.inputs["Scale"])
								links.new(UVMap1Node.outputs["UV"],currentMappingNode.inputs["Vector"])
								links.new(currentMappingNode.outputs["Vector"],currentMaskNode.inputs["Vector"])
						
								currentMaskSepNode = nodes.new("ShaderNodeSeparateColor")
								currentMaskSepNode.location = currentPos
								links.new(currentMaskNode.outputs["Color"],currentMaskSepNode.inputs["Color"])
								
								mixColorNode = nodes.new("ShaderNodeMixRGB")
								mixColorNode.location = currentMaskNode.location + Vector((300,0))
								mixColorNode.inputs["Color1"].default_value = (0.065,0.065,0.065,1.0)
								mixColorNode.inputs["Color2"].default_value = (0.14,0.12,0.08,1.0)
								links.new(currentMaskSepNode.outputs["Green"],mixColorNode.inputs["Fac"])
								
								matInfo["albedoNodeLayerGroup"].addMixLayer(mixColorNode.outputs["Color"],factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 1.0)
								matInfo["roughnessNodeLayerGroup"].addMixLayer(currentMaskNode.outputs["Alpha"],factorOutSocket = None,mixType = "MIX",mixFactor = 1.0)
				#Finish process by linking to bsdf shader
				
				
				if matInfo["cavityNodeLayerGroup"].currentOutSocket != None:#Add cavity layers into AO
					matInfo["aoNodeLayerGroup"].addMixLayer(matInfo["cavityNodeLayerGroup"].currentOutSocket,factorOutSocket = None,mixType = "MULTIPLY",mixFactor = 0.6)
				
				normalNode = None
				normalVectorRotateNode = None
				if matInfo["normalNodeLayerGroup"].currentOutSocket != None:
					
					normalNode = nodes.new("ShaderNodeNormalMap")
					normalNode.location = (matInfo["normalNodeLayerGroup"].currentOutSocket.node.location[0] + 300,matInfo["normalNodeLayerGroup"].currentOutSocket.node.location[1])
					"""
					if matInfo["isNRRT"]:
						normalNode.inputs["Strength"].default_value = .4
					"""
					
					links.new(matInfo["normalNodeLayerGroup"].currentOutSocket,normalNode.inputs["Color"])
					if matInfo["detailNormalSocket"] == None:
						links.new(normalNode.outputs["Normal"],nodeBSDF.inputs["Normal"])
					else:
						detailNodeLoc = matInfo["detailNormalSocket"].node.location
						if "geometryNode" in nodeTree.nodes:#Changed this from tex coord normal to geometry normal, so that the normals don't break when the model rotates
							geometryNode = nodeTree.nodes["geometryNode"]
						else:
							geometryNode = nodeTree.nodes.new("ShaderNodeNewGeometry")
							geometryNode.name = "geometryNode"
							geometryNode.location = (-800,400)
						crossProductNode =  nodeTree.nodes.new("ShaderNodeVectorMath")
						crossProductNode.location = (detailNodeLoc[0] + 300,detailNodeLoc[1]+300)
						crossProductNode.operation = "CROSS_PRODUCT"
						links.new(matInfo["detailNormalSocket"],crossProductNode.inputs[0])
						links.new(geometryNode.outputs["Normal"],crossProductNode.inputs[1])
						
						dotProductNode = nodeTree.nodes.new("ShaderNodeVectorMath")
						dotProductNode.location = (detailNodeLoc[0] + 300,detailNodeLoc[1])
						dotProductNode.operation = "DOT_PRODUCT"
						links.new(matInfo["detailNormalSocket"],dotProductNode.inputs[0])
						links.new(geometryNode.outputs["Normal"],dotProductNode.inputs[1])
						
						arcCosineNode =  nodeTree.nodes.new("ShaderNodeMath")
						arcCosineNode.location = (detailNodeLoc[0] + 600,detailNodeLoc[1])
						arcCosineNode.operation = "ARCCOSINE"
						links.new(dotProductNode.outputs["Value"],arcCosineNode.inputs[0])
						
						normalVectorRotateNode = nodeTree.nodes.new("ShaderNodeVectorRotate")
						normalVectorRotateNode.location = (detailNodeLoc[0] + 900,detailNodeLoc[1])
						normalVectorRotateNode.invert = True
						
						links.new(normalNode.outputs["Normal"],normalVectorRotateNode.inputs["Vector"])
						links.new(crossProductNode.outputs["Vector"],normalVectorRotateNode.inputs["Axis"])
						links.new(arcCosineNode.outputs["Value"],normalVectorRotateNode.inputs["Angle"])
						
						links.new(normalVectorRotateNode.outputs["Vector"],nodeBSDF.inputs["Normal"])
						
				if "BaseShiftMap" in matInfo["textureNodeDict"] or "BaseAnisoShiftMap" in matInfo["textureNodeDict"] :
					#TODO Add proper hair setup here, this is a hacky temporary setup
					shiftNodeName = "BaseShiftMap" if "BaseShiftMap" in matInfo["textureNodeDict"] else "BaseAnisoShiftMap"
					fresnelNode = nodes.new("ShaderNodeFresnel")
					fresnelNode.location = matInfo["textureNodeDict"][shiftNodeName].location + Vector((300,0))
					links.new(matInfo["textureNodeDict"][shiftNodeName].outputs["Alpha"],fresnelNode.inputs["IOR"])
					if normalNode != None:
						links.new(normalNode.outputs["Normal"],fresnelNode.inputs["Normal"])
					fresnelClampNode = nodes.new("ShaderNodeClamp")
					fresnelClampNode.location = fresnelNode.location + Vector((300,0))
					fresnelClampNode.inputs["Max"].default_value = 0.75
					links.new(fresnelNode.outputs["Fac"],fresnelClampNode.inputs["Value"])
					
					matInfo["metallicNodeLayerGroup"].addMixLayer(fresnelClampNode.outputs["Result"])
				if matInfo["aoNodeLayerGroup"].currentOutSocket != None and not matInfo["disableAO"]:
					#This isn't the correct way to apply AO but EEVEE has no good ways of doing AO maps that doesn't have downsides (such as killing screenspace reflections and subsurface)
					
					aoNode = nodes.new("ShaderNodeAmbientOcclusion")
					
					links.new(matInfo["aoNodeLayerGroup"].currentOutSocket,aoNode.inputs["Color"])
					if normalVectorRotateNode != None:#Use combined detail normal
						links.new(normalVectorRotateNode.outputs["Vector"],aoNode.inputs["Normal"])
					elif normalNode != None:
						links.new(normalNode.outputs["Normal"],aoNode.inputs["Normal"])
					matInfo["albedoNodeLayerGroup"].addMixLayer(aoNode.outputs["Color"],aoNode.outputs["AO"],mixType = "MULTIPLY")
				
				
				if matInfo["albedoNodeLayerGroup"].currentOutSocket != None:
					links.new(matInfo["albedoNodeLayerGroup"].currentOutSocket,nodeBSDF.inputs["Base Color"])
				
				
				if matInfo["roughnessNodeLayerGroup"].currentOutSocket != None:
					clampNode = nodes.new("ShaderNodeClamp")
					clampNode.location = (matInfo["roughnessNodeLayerGroup"].currentOutSocket.node.location[0]+300,matInfo["roughnessNodeLayerGroup"].currentOutSocket.node.location[1])
					links.new(matInfo["roughnessNodeLayerGroup"].currentOutSocket,clampNode.inputs["Value"])
					links.new(clampNode.outputs["Result"],nodeBSDF.inputs["Roughness"])
				
				if matInfo["metallicNodeLayerGroup"].currentOutSocket != None:
					clampNode = nodes.new("ShaderNodeClamp")
					clampNode.location = (matInfo["metallicNodeLayerGroup"].currentOutSocket.node.location[0]+300,matInfo["metallicNodeLayerGroup"].currentOutSocket.node.location[1])
					
					links.new(clampNode.outputs["Result"],nodeBSDF.inputs["Metallic"])
					metallicNode = addPropertyNodeFallback(matInfo, ["Metallic", "Metallic_Param", "MetalParam"], nodeTree)
					
					
					if matInfo["isDielectric"]:
						invertNode = nodes.new("ShaderNodeInvert")
						invertNode.location = (matInfo["metallicNodeLayerGroup"].currentOutSocket.node.location[0]+300,matInfo["metallicNodeLayerGroup"].currentOutSocket.node.location[1])
						links.new(matInfo["metallicNodeLayerGroup"].currentOutSocket,invertNode.inputs["Color"])
						
						if metallicNode != None:
						
							multNode = nodes.new("ShaderNodeMath")
							multNode.location = invertNode.location + Vector((300,0))
							multNode.operation = "MULTIPLY"
							
							links.new(invertNode.outputs["Color"],multNode.inputs[0])
							links.new(metallicNode.outputs["Value"],multNode.inputs[1])
							links.new(matInfo["metallicNodeLayerGroup"].currentOutSocket,clampNode.inputs["Value"])
							links.new(multNode.outputs["Value"],clampNode.inputs["Value"])
						else:
							links.new(invertNode.outputs["Color"],clampNode.inputs["Value"])
					else:
						if metallicNode != None:
						
							multNode = nodes.new("ShaderNodeMath")
							multNode.location = matInfo["metallicNodeLayerGroup"].nodeLoc + Vector((300,0))
							multNode.operation = "MULTIPLY"
							
							links.new(matInfo["metallicNodeLayerGroup"].currentOutSocket,multNode.inputs[0])
							links.new(metallicNode.outputs["Value"],multNode.inputs[1])
							links.new(multNode.outputs["Value"],clampNode.inputs["Value"])
						else:
							links.new(matInfo["metallicNodeLayerGroup"].currentOutSocket,clampNode.inputs["Value"])
				
				if matInfo["emissionColorNodeLayerGroup"].currentOutSocket != None:
					
					if bpy.app.version < (4,0,0):
						links.new(matInfo["emissionColorNodeLayerGroup"].currentOutSocket,nodeBSDF.inputs["Emission"])
				
					else:#I swear Blender changes things just to break addon compatibility
					#I had to dig through the source code just to find the actual name of this socket since it's not listed anywhere and Blender only gives you it's index :/
						links.new(matInfo["emissionColorNodeLayerGroup"].currentOutSocket,nodeBSDF.inputs["Emission Color"])
				
						
				if matInfo["emissionStrengthNodeLayerGroup"].currentOutSocket != None:
					emissionClampNode = nodes.new("ShaderNodeClamp")#Prevent negative emission values
					emissionClampNode.location = (matInfo["emissionStrengthNodeLayerGroup"].nodeLoc[0] + 300, matInfo["emissionStrengthNodeLayerGroup"].nodeLoc[1])
					emissionClampNode.inputs["Max"].default_value = 9999
					links.new(matInfo["emissionStrengthNodeLayerGroup"].currentOutSocket,emissionClampNode.inputs["Value"])
					links.new(emissionClampNode.outputs["Result"],nodeBSDF.inputs["Emission Strength"])
					
				
				alphaClippingNode = None
				#if hasAlpha:
				if matInfo["alphaSocket"] != None:
					clippingThresholdOutSocket = None

					if "Nuki" in matInfo["mPropDict"]:#I wish capcom didn't use 1000 different names for the same thing
						#clippingThresholdNode = addPropertyNode(matInfo["mPropDict"]["Nuki"], matInfo["currentPropPos"], nodeTree)
						nukiNode = addPropertyNode(matInfo["mPropDict"]["Nuki"], matInfo["currentPropPos"], nodeTree)
						if "UseNuki_Dissolve" in matInfo["mPropDict"]:
							useNukiNode = addPropertyNode(matInfo["mPropDict"]["UseNuki_Dissolve"], matInfo["currentPropPos"], nodeTree)
							dissolveNode = addPropertyNode(matInfo["mPropDict"]["Nuki_Dissolve"], matInfo["currentPropPos"], nodeTree)
							
							mixNukiNode = nodes.new("ShaderNodeMixRGB")
							mixNukiNode.location = useNukiNode.location + Vector((300,0))
							links.new(nukiNode.outputs["Value"],mixNukiNode.inputs["Color1"])
							links.new(dissolveNode.outputs["Value"],mixNukiNode.inputs["Color2"])
							links.new(useNukiNode.outputs["Value"],mixNukiNode.inputs["Fac"])
							clippingThresholdOutSocket = mixNukiNode.outputs["Color"]
							
						else:
							clippingThresholdOutSocket = nukiNode.outputs["Value"]
					elif "Nuki_Dissolve" in matInfo["mPropDict"]:
						dissolveNode = addPropertyNode(matInfo["mPropDict"]["Nuki_Dissolve"], matInfo["currentPropPos"], nodeTree)
						clippingThresholdOutSocket = dissolveNode.outputs["Value"]
					
					#Using this as the clipping threshold might not be right but certain things don't work correctly if it's the default threshold instead
					elif "AlphaTestRef" in matInfo["mPropDict"]:
						dissolveNode = addPropertyNode(matInfo["mPropDict"]["AlphaTestRef"], matInfo["currentPropPos"], nodeTree)
						clippingThresholdOutSocket = dissolveNode.outputs["Value"]
					#	correctionNode = nodes.new("ShaderNodeMath")
					#	correctionNode.location =  (dissolveNode.location[0] + 300,dissolveNode.location[1])
					#	correctionNode.operation = "SUBTRACT"
					#	correctionNode.inputs[0].default_value = 1
					#	links.new(dissolveNode.outputs["Value"],correctionNode.inputs[1])
					#	clippingThresholdOutSocket = correctionNode.outputs["Value"]
	
					elif "DissolveThreshold" in matInfo["mPropDict"]:
						dissolveNode = addPropertyNode(matInfo["mPropDict"]["DissolveThreshold"], matInfo["currentPropPos"], nodeTree)
						clippingThresholdOutSocket = dissolveNode.outputs["Value"]
					
					if not matInfo["isAlphaBlend"] and "reflectivetransparent" not in matInfo["mmtrName"] and "alphadissolve" not in matInfo["mmtrName"] and "decal_basealpha" not in matInfo["mmtrName"]:
						
						if "hair" not in matInfo["mmtrName"] and "BaseAlphaMap" not in matInfo["textureNodeDict"]:
							alphaClippingNode = nodes.new("ShaderNodeMath")
							alphaClippingNode.location = nodeBSDF.location + Vector((-300,-400))
							alphaClippingNode.operation = "GREATER_THAN"
							alphaClippingNode.outputs["Value"].default_value = 1.0
							
							links.new(alphaClippingNode.outputs["Value"],nodeBSDF.inputs["Alpha"])
							if clippingThresholdOutSocket != None:
								links.new(clippingThresholdOutSocket,alphaClippingNode.inputs[1])
							
							if matInfo["alphaSocket"] != None:
								
								links.new(matInfo["alphaSocket"],alphaClippingNode.inputs[0])
						else:#Hack to bypass clipping because alpha clipping with hair is being a pain
							if matInfo["alphaSocket"] != None: 
								links.new(matInfo["alphaSocket"],nodeBSDF.inputs["Alpha"])
					else:
						if bpy.app.version < (4,2,0):
							matInfo["blenderMaterial"].blend_method = "BLEND"
							matInfo["blenderMaterial"].shadow_method = "NONE"
						else:
							matInfo["blenderMaterial"].surface_render_method = "BLENDED"
							matInfo["blenderMaterial"].use_transparent_shadow = True
							matInfo["disableShadowCast"] = True
						
						links.new(matInfo["alphaSocket"],nodeBSDF.inputs["Alpha"])
					#Blender removed blend and shadow method in 4.2+ so everything will just be alpha hashed now
					#blenderMaterial.blend_method = "CLIP"
					#blenderMaterial.shadow_method = "CLIP"
				
				if (matInfo["gameName"] == "SF6" and "ClothAniso_ColorRate" in matInfo["mPropDict"]):
					colorRateNode = addPropertyNode(
						matInfo["mPropDict"]["ClothAniso_ColorRate"],
						matInfo["currentPropPos"],
						nodeTree
					) 

					if "Sheen Weight" in nodeBSDF.inputs:
						links.new(colorRateNode.outputs["Value"], nodeBSDF.inputs["Sheen Weight"])
					
					if ("ClothAniso_PrimalySpecularColor" in matInfo["mPropDict"] and "Sheen Tint" in nodeBSDF.inputs):
						specularColorNode = addPropertyNode(
							matInfo["mPropDict"]["ClothAniso_PrimalySpecularColor"],
							matInfo["currentPropPos"],
							nodeTree
						)

						links.new(specularColorNode.outputs["Color"], nodeBSDF.inputs["Sheen Tint"])
					
					if ("ClothAniso_PrimalySpecSharpness" in matInfo["mPropDict"] and "Sheen Roughness" in nodeBSDF.inputs):
						sharpnessNode = addPropertyNode(
							matInfo["mPropDict"]["ClothAniso_PrimalySpecSharpness"],
							matInfo["currentPropPos"],
							nodeTree
						)

						addNode = nodes.new("ShaderNodeMath")
						addNode.operation = "ADD"
						addNode.inputs[1].default_value = 1.0
						links.new(sharpnessNode.outputs["Value"], addNode.inputs[0])

						divideNode = nodes.new("ShaderNodeMath")
						divideNode.operation = "DIVIDE"
						divideNode.inputs[0].default_value = 1.0
						links.new(addNode.outputs["Value"], divideNode.inputs[1])
						links.new(divideNode.outputs["Value"], nodeBSDF.inputs["Sheen Roughness"])

				if matInfo["sheenSocket"] != None:
					if bpy.app.version < (4,0,0):
						links.new(matInfo["sheenSocket"],nodeBSDF.inputs["Sheen"])
					else:
						links.new(matInfo["sheenSocket"],nodeBSDF.inputs["Sheen Weight"])
				
				if matInfo["subsurfaceSocket"] != None:
					if matInfo["gameName"] == "MHWILDS":
						if "SSSProfile" in matInfo["mPropDict"] and matInfo["mPropDict"]["SSSProfile"].propValue[0] == 1.0:#Skin SSS profile
							sssParamNode = None
							if "SSSParam" in matInfo["mPropDict"]:
								sssParamNode = addPropertyNode(matInfo["mPropDict"]["SSSParam"], matInfo["currentPropPos"], nodeTree)
							
							sssMultNode = nodes.new("ShaderNodeMath")
							sssMultNode.location = matInfo["subsurfaceSocket"].node.location + Vector((300,0))
							sssMultNode.operation = "MULTIPLY"
							
							links.new(matInfo["subsurfaceSocket"],sssMultNode.inputs[0])
							sssMultNode.inputs[1].default_value = 1.0
							if sssParamNode != None:
								links.new(sssParamNode.outputs["Value"],sssMultNode.inputs[1])
							if "Subsurface Weight" in nodeBSDF.inputs:	
								links.new(sssMultNode.outputs["Value"],nodeBSDF.inputs["Subsurface Weight"])	
					elif matInfo["gameName"] == "SF6":
						mmtrName = matInfo["mmtrName"].lower()
						isSF6Skin = any(name in mmtrName for name in (
							"character_face",
							"character_body",
						))

						if isSF6Skin and "Subsurface Weight" in nodeBSDF.inputs:
							sssMultNode = nodes.new("ShaderNodeMath")
							sssMultNode.label = "SF6 Skin SSS"
							sssMultNode.location = matInfo["subsurfaceSocket"].node.location + Vector((300, 0))
							sssMultNode.operation = "MULTIPLY"

							links.new(matInfo["subsurfaceSocket"], sssMultNode.inputs[0])
							sssMultNode.inputs[1].default_value = 0.18
							links.new(sssMultNode.outputs["Value"], nodeBSDF.inputs["Subsurface Weight"])
							if "Subsurface Radius" in nodeBSDF.inputs:
								nodeBSDF.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.2)
							
							if "Subsurface Scale" in nodeBSDF.inputs:
								nodeBSDF.inputs["Subsurface Scale"].default_value = 0.01
				#Mix Shaders
				
				currentPos = [nodeBSDF.location[0]+300,nodeBSDF.location[1]]
				
					
				if matInfo["disableShadowCast"]:
					#Blender 4.2 made it such a pain to disable shadow casting on a material
					#Used to be just a drop down menu :/
					transparentNode = nodes.new("ShaderNodeBsdfTransparent")
					transparentNode.location = currentPos
					currentPos[0] += 300
					
					lightPathNode = nodes.new("ShaderNodeLightPath")
					lightPathNode.location = currentPos
					currentPos[0] += 300
					
					mixShader = nodes.new("ShaderNodeMixShader")
					mixShader.location = currentPos
					links.new(matInfo["currentShaderOutput"],mixShader.inputs[1])
					links.new(transparentNode.outputs["BSDF"],mixShader.inputs[2])
					links.new(lightPathNode.outputs["Is Shadow Ray"],mixShader.inputs["Fac"])
					matInfo["currentShaderOutput"] = mixShader.outputs["Shader"]
				
				#if blenderMaterial.blend_method == "OPAQUE":#Remove alpha connection if the material doesn't have alpha enabled. ATOS causes black spots otherwise.
				#	if len(nodeBSDF.inputs['Alpha'].links) > 0:
				#		alphaLink = nodeBSDF.inputs['Alpha'].links[0]
				#		blenderMaterial.node_tree.links.remove(alphaLink)
				
				if loadUnusedProps:
					for prop in matInfo["mPropDict"].values():
						propNode = addPropertyNode(prop,currentPropPos,blenderMaterial.node_tree)
				
				
				#Fix array image dependency cycle
				for arrayImageNodeName in arrayImageNodeList:
					arrayImageNode = nodeTree.nodes[arrayImageNodeName]
					#Get the source of vector input if it's connected and connect it directly to the array images to fix the dependency loop
					if arrayImageNode.inputs["Vector"].links:	
						linkSource = arrayImageNode.inputs["Vector"].links[0].from_socket
						#print(arrayImageNode.inputs["Vector"].links[0].from_socket.node.name)
					else:
						linkSource = None
					for link in arrayImageNode.outputs["Vector"].links:
						if linkSource == None:
							nodeTree.links.remove(link)
						else:
							links.new(linkSource,link.to_socket)
				if gameName == "RE2RT" and matInfo["mmtrName"] == "glass_frosted.mmtr":
					matInfo["currentShaderOutput"] = buildRE2FrostedGlass(matInfo, normalNode)
					nodes.remove(nodeBSDF)
				links.new(matInfo["currentShaderOutput"],nodeMaterialOutput.inputs["Surface"])
				if arrangeNodes:
					#TODO Force blender to update node dimensions so that a large margin doesn't need to be used as a workaround
					arrangeNodeTree(blenderMaterial.node_tree,margin_x = 300,margin_y = 300,centerNodes = True)
			except Exception as err:
				raiseWarning(f"Material Importing Failed ({str(materialName)}). Error During Node Detection. \nIf you're on the latest version of RE Mesh Editor, please report this error.")
				traceback.print_exception(type(err), err, err.__traceback__)
				inErrorState = True
		else:
			print("Material \"" + materialName + "\" is not in the MDF, cannot import")
	
	errorFileSet.clear()
	loadedImageDict.clear()
	#Note: the texconv DLL stays loaded (its module-level cache makes Texconv()
	#cheap); unloading it here forced a reload on every subsequent import.
	if inErrorState:
		showErrorMessageBox("An error occurred while importing materials. See the Window > Toggle System Console for details.")
	else:
		print("Finished loading materials.")
