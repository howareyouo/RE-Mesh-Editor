# Single source of truth for per-game RE Engine format facts.
#
# One row per game in _GAME_ROWS below; every other version table derives from it:
#   - modules/mesh/mesh_versions.py: remapped versions, mesh file version / remap /
#     internal version maps, six weight and Pragmata blend shape sets
#   - modules/game_versions.py: GAME_INFO, mdfVersionDict, texVersionDict, tex by game
#   - modules/mesh/blender_re_mesh_utils.py: _GAME_PROFILES weight packing
# Adding a game means adding one row here.

from typing import NamedTuple

# Mesh version numbers do not always increase for newer versions of the file format
# Therefore mesh versions have been remapped to new values to allow for conditional import and export changes depending on the mesh version

# Leaving gaps in case the versions in between these need to be parsed
VERSION_DMC5 = 75
VERSION_RE2 = 80
VERSION_RE3 = 85
VERSION_RE8 = 90
VERSION_RERT = 95
VERSION_RE7RT = 96
VERSION_MHRSB = 100
VERSION_SF6 = 105
VERSION_RE4 = 110
VERSION_DD2 = 115
VERSION_KG = 120
VERSION_DD2NEW = 124
VERSION_DR = 125
VERSION_ONI2 = 127
VERSION_MHWILDS = 130
VERSION_PRAG = 135
VERSION_MHS3 = 136
VERSION_RE9 = 140

# Weight packing tuple layout: (maxWeightsPerVertex, maxWeightsPerVertexExtended,
# maxWeightedBones, padWithLastWeightIndex, extendedWeights)
DEFAULT_WEIGHT_PACKING = (8, 16, 256, False, False)
# MainMeshHeader.skinWeightCount used by games that do not override it.
DEFAULT_SKIN_WEIGHT_COUNT = 18


class GameProfile(NamedTuple):
	"""Immutable facts about one game. Get these from profile(), not by hand."""

	name: str
	# Name reported for this game's mesh file version; build variants report the
	# retail name (DD2NEW and MHWILDSBETA are reported as DD2 and MHWILDS).
	reportedName: str
	# Number in the .mesh.<N> file extension, None for games with no mesh row.
	meshFileVersion: int | None
	# Number in the .mdf2.<N> file extension.
	mdfVersion: int | None
	# Number in the .tex.<N> file extension.
	texVersion: int | None
	# Remapped mesh version (VERSION_* above).
	remapVersion: int | None
	# fileHeader.version; None when unconfirmed or shared with another game.
	internalVersion: int | None
	# 6 bone weights per vertex instead of 4.
	sixWeight: bool
	# MainMeshHeader.skinWeightCount.
	skinWeightCount: int
	# Layout: (maxWeightsPerVertex, maxWeightsPerVertexExtended, maxWeightedBones,
	# padWithLastWeightIndex, extendedWeights)
	weightPacking: tuple
	# Pragmata retail typing-2 blend shape layout (docs/PRAGMATA.md).
	pragmataBlendShape: bool
	# In the name side of the MDF alias table in modules/game_versions.py. Build
	# variants (DD2NEW, MHWILDSBETA) stay out of it.
	mdfAlias: bool
	# The row a numeric MDF version resolves to in that table.
	mdfAliasWinner: bool

	@property
	def padWithLastWeightIndex(self):
		return self.weightPacking[3]

	@property
	def extendedWeights(self):
		return self.weightPacking[4]


# name: (meshFileVersion, mdfVersion, texVersion, remapVersion, internalVersion,
#        sixWeight, skinWeightCount, padWithLastWeightIndex, extendedWeights,
#        pragmataBlendShape, reportedName, mdfAlias, mdfAliasWinner)
_GAME_ROWS = {
	"DMC5":        (1808282334, 10, 11,        VERSION_DMC5, 386270720, False, 18, False, False, False, None, True, False),
	"RE2":         (1808312334, 10, 10,        VERSION_RE2, 386270720, False, 18, False, False, False, None, True, True),
	"RE3":         (1902042334, 13, 190820018, VERSION_RE3, 21011200, False, 18, False, False, False, None, True, True),
	"RE8":         (2101050001, 19, 30,        VERSION_RE8, 2020091500, False, 18, False, False, False, None, True, True),
	"RE2RT":       (2109108288, 21, 34,        VERSION_RERT, 21041600, False, 18, False, False, False, None, True, True),
	"RE7RT":       (220128762, 21, 35,         VERSION_RE7RT, None, False, 18, False, False, False, None, True, False),
	"MHRSB":       (2109148288, 23, 28,        VERSION_MHRSB, 21091000, False, 18, False, False, False, None, True, True),
	"SF6":         (230110883, 31, 241101895,  VERSION_SF6, 220705151, True, 9, False, False, False, None, True, True),
	"RE4":         (221108797, 32, 143221013,  VERSION_RE4, 220822879, False, 18, False, False, False, None, True, True),
	"DD2":         (231011879, 40, 760230703,  VERSION_DD2, 230517984, False, 18, False, False, False, None, True, True),
	"KG":          (240306278, 40, 231106777,  VERSION_KG, 230727984, False, 18, False, False, False, None, True, False),
	"DD2NEW":      (240423143, 40, 760230703,  VERSION_DD2NEW, 230517984, False, 18, False, False, False, "DD2", False, False),
	"DR":          (240424828, 40, 240606151,  VERSION_DR, 240423829, False, 18, False, False, False, None, True, False),
	"MHWILDSBETA": (240820143, 45, 241106027,  VERSION_MHWILDS, 240704828, True, 25, False, True, False, "MHWILDS", False, False),
	"MHWILDS":     (241111606, 45, 241106027,  VERSION_MHWILDS, 240704828, True, 25, False, True, False, None, True, True),
	"ONI2":        (240827123, 46, 240701001,  VERSION_ONI2, 240704828, False, 18, False, False, False, None, True, True),
	# mdf2.49 has no parser, so MHS3 has no alias entry.
	"MHS3":        (250604100, 49, 251111100,  VERSION_MHS3, 250203152, True, 18, True, True, False, None, False, False),
	# PRAG shares mdf 51 with RE9, which wins the numeric lookup.
	"PRAG":        (251121828, 51, 251111100,  VERSION_PRAG, 250707828, True, 27, True, True, True, None, True, False),
	"RE9":         (250925211, 51, 250813143,  VERSION_RE9, 250904410, False, 18, True, False, False, None, True, True),
	# Name-only rows: appear in the MDF / tex lookup tables but ship no mesh row of
	# their own here (RE3RT shares RE2RT's remap, MHRise has no mesh file version).
	"RE3RT":       (None, 21, 34, None, None, False, 18, False, False, False, None, True, False),
	"MHR":         (None, None, 28, None, None, False, 18, False, False, False, None, False, False),
}

# Mesh file versions that belong to a game row but carry their own MDF / tex version.
# RE VERSE ships RE8's remap with mdf2.20 and tex.31; its internal version is unconfirmed.
_FILE_VERSION_ALIASES = {
	2102020001: ("RE8", 20, 31),
}

# Internal versions shared by several games. A mesh header cannot tell those games
# apart, so the reverse lookups resolve to the game listed here.
_INTERNAL_VERSION_PREFERRED_NAME = {
	386270720: "DMC5",  # shared with RE2
	230517984: "DD2NEW",  # shared with DD2
	240704828: "MHWILDS",  # shared with MHWILDSBETA and ONI2
}


def _weightPacking(padWithLastWeightIndex, extendedWeights):
	if not (padWithLastWeightIndex or extendedWeights):
		return DEFAULT_WEIGHT_PACKING
	return (DEFAULT_WEIGHT_PACKING[0], DEFAULT_WEIGHT_PACKING[1], DEFAULT_WEIGHT_PACKING[2],
	        padWithLastWeightIndex, extendedWeights)


ALL_PROFILES = tuple(
	GameProfile(
		name=name,
		reportedName=reportedName or name,
		meshFileVersion=meshFileVersion,
		mdfVersion=mdfVersion,
		texVersion=texVersion,
		remapVersion=remapVersion,
		internalVersion=internalVersion,
		sixWeight=sixWeight,
		skinWeightCount=skinWeightCount,
		weightPacking=_weightPacking(padWithLastWeightIndex, extendedWeights),
		pragmataBlendShape=pragmataBlendShape,
		mdfAlias=mdfAlias,
		mdfAliasWinner=mdfAliasWinner,
	)
	for name, (meshFileVersion, mdfVersion, texVersion, remapVersion, internalVersion,
	           sixWeight, skinWeightCount, padWithLastWeightIndex, extendedWeights,
	           pragmataBlendShape, reportedName, mdfAlias, mdfAliasWinner) in _GAME_ROWS.items()
)

profilesByName = {gameProfile.name: gameProfile for gameProfile in ALL_PROFILES}

profilesByFileVersion = {
	gameProfile.meshFileVersion: gameProfile
	for gameProfile in ALL_PROFILES
	if gameProfile.meshFileVersion is not None
}
for _fileVersion, (_gameName, _mdfVersion, _texVersion) in _FILE_VERSION_ALIASES.items():
	profilesByFileVersion[_fileVersion] = profilesByName[_gameName]._replace(
		meshFileVersion=_fileVersion,
		mdfVersion=_mdfVersion,
		texVersion=_texVersion,
		internalVersion=None,
	)

profilesByRemapVersion = {
	gameProfile.remapVersion: gameProfile
	for gameProfile in ALL_PROFILES
	if gameProfile.remapVersion is not None
}

profilesByInternalVersion = {}
for _gameProfile in ALL_PROFILES:
	if _gameProfile.internalVersion is None:
		continue
	if (_gameProfile.internalVersion in profilesByInternalVersion
			and _INTERNAL_VERSION_PREFERRED_NAME.get(_gameProfile.internalVersion) != _gameProfile.name):
		continue
	profilesByInternalVersion[_gameProfile.internalVersion] = _gameProfile

UNKNOWN_PROFILE = GameProfile(
	name=None,
	reportedName=None,
	meshFileVersion=None,
	mdfVersion=None,
	texVersion=None,
	remapVersion=None,
	internalVersion=None,
	sixWeight=False,
	skinWeightCount=DEFAULT_SKIN_WEIGHT_COUNT,
	weightPacking=DEFAULT_WEIGHT_PACKING,
	pragmataBlendShape=False,
	mdfAlias=False,
	mdfAliasWinner=False,
)


def profile(key):
	"""Return the cached GameProfile for a game name, mesh file version or remapped version.

	Names ("PRAG") and mesh file versions (251121828, the number in the .mesh.<N>
	extension) resolve exactly. A remapped version (VERSION_PRAG) resolves to the
	retail game when two games share one. Unknown keys return UNKNOWN_PROFILE,
	whose .name is None, instead of raising.
	"""
	if isinstance(key, str):
		return profilesByName.get(key, UNKNOWN_PROFILE)
	gameProfile = profilesByFileVersion.get(key)
	if gameProfile is not None:
		return gameProfile
	return profilesByRemapVersion.get(key, UNKNOWN_PROFILE)


def profileByInternalVersion(internalVersion):
	"""Return the GameProfile for a mesh fileHeader.version.

	MHWILDSBETA, MHWILDS and ONI2 share 240704828, so this resolves to MHWILDS and
	must not be used to identify ONI2 or the MHWILDS beta build.
	"""
	return profilesByInternalVersion.get(internalVersion, UNKNOWN_PROFILE)
