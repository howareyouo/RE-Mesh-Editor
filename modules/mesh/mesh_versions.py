# Mesh version numbers, file versions and remap tables.
# Shared by file_re_mesh.py, re_mesh_parse.py and file_re_mesh_mply.py.
#
# Every table below derives from the per-game rows in game_profile.py; edit that
# module to add a game or change a game's versions.

from .game_profile import (ALL_PROFILES,
                           VERSION_DR, VERSION_KG, VERSION_MHWILDS, VERSION_MHS3, VERSION_MHRSB,
                           VERSION_ONI2, VERSION_PRAG, VERSION_RE2, VERSION_RE3, VERSION_RE4,
                           VERSION_RE7RT, VERSION_RE8, VERSION_RE9, VERSION_RERT, VERSION_SF6,
                           profilesByFileVersion, profilesByInternalVersion, profilesByName)

# Container magics and special internal versions (single source of truth; shared by
# file_re_mesh.py, file_re_mesh_mply.py and re_mesh_parse.py).
MESH_FILE_MAGIC = 1213416781  # ".mesh" container magic
MPLY_FILE_MAGIC = 1498173517  # MPLY meshlet container magic (stage meshes)
PRAGMATA_INTERNAL_MESH_VERSION = profilesByName["PRAG"].internalVersion  # fileHeader.version of Pragmata meshes (6-weight layout)

SIX_WEIGHT_GAMES = frozenset(
	gameProfile.remapVersion for gameProfile in ALL_PROFILES
	if gameProfile.sixWeight and gameProfile.remapVersion is not None
)

meshFileVersionToNewVersionDict = {
	gameProfile.meshFileVersion: gameProfile.remapVersion
	for gameProfile in profilesByFileVersion.values()
	if gameProfile.remapVersion is not None
}
newVersionToMeshFileVersion = {
	gameProfile.remapVersion: gameProfile.meshFileVersion
	for gameProfile in ALL_PROFILES
	if gameProfile.meshFileVersion is not None
}
meshFileVersionToInternalVersionDict = {
	gameProfile.meshFileVersion: gameProfile.internalVersion
	for gameProfile in profilesByFileVersion.values()
	if gameProfile.internalVersion is not None
}
# Several games can share one internal version; game_profile decides which game a
# header resolves to (240704828 is MHWILDS_BETA, MHWILDS and ONI2, resolves to MHWILDS).
internalVersionToMeshFileVersionDict = {
	internalVersion: gameProfile.meshFileVersion
	for internalVersion, gameProfile in profilesByInternalVersion.items()
}
meshFileVersionToGameNameDict = {
	gameProfile.meshFileVersion: gameProfile.reportedName
	for gameProfile in profilesByFileVersion.values()
}


# Used for unmapped mesh versions, potentially allows for importing
def getNearestRemapVersion(meshVersion):  # Returns the remapped version number of the closest mesh version
	return meshFileVersionToNewVersionDict[
		min(meshFileVersionToNewVersionDict.keys(), key=lambda x: abs(x - meshVersion))]


# --- Mesh FILE versions (the number in the file extension, e.g. .mesh.230110883) ---
# Named differently from the remapped VERSION_* values above on purpose: the two
# namespaces were previously confused (re_mesh_parse had VERSION_SF6 = file version).
MESH_FILE_VERSION_SF6 = profilesByName["SF6"].meshFileVersion

# Pragmata retail mesh extension (file .mesh.251121828). Not MH Wilds: typing 2, float16 xyz
# deltas, aux+map prefix, sunbreakSecondUnknown = (mapOffset, deltaOffset). See docs/PRAGMATA.md.
PRAGMATA_BLEND_SHAPE_FILE_VERSIONS = frozenset(
	gameProfile.meshFileVersion for gameProfile in profilesByFileVersion.values()
	if gameProfile.pragmataBlendShape
)


def isPragmataMeshFileVersion(fileVersion):
	return fileVersion in PRAGMATA_BLEND_SHAPE_FILE_VERSIONS


# Meshes from these FILE versions use 6 bone weights per vertex.
SIX_WEIGHT_MESH_FILE_VERSIONS = frozenset(
	gameProfile.meshFileVersion for gameProfile in profilesByFileVersion.values()
	if gameProfile.sixWeight
)
