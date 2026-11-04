# MDF and tex version tables shared across the mesh, mdf and tex modules.
#
# The per-game numbers live in modules/mesh/game_profile.py and the tables below
# derive from those rows. Add a game by adding a row to game_profile._GAME_ROWS.

from .mesh.game_profile import ALL_PROFILES, profile, profilesByFileVersion

# game: (mesh file version, mdf file version, tex file version, remapped mesh version)
GAME_INFO = {
	gameProfile.name: (gameProfile.meshFileVersion, gameProfile.mdfVersion,
	                   gameProfile.texVersion, gameProfile.remapVersion)
	for gameProfile in ALL_PROFILES
	if gameProfile.meshFileVersion is not None
}

# MDF version <-> game name (bidirectional). Alias games share one mdf version;
# the int key maps to the first listed game. Hand-curated: this is the UI alias
# table, so RE3RT / RE Verse are listed here and DD2NEW / MHWILDSBETA are not.
gameNameMDFVersionDict = {
    10: "RE2",  # DMC5
    13: "RE3",
    19: "RE8",
    20: "RE8",  # RE Verse, imported as RE8
    21: "RE2RT",  # RE3RT
    23: "MHRSB",
    31: "SF6",
    32: "RE4",
    40: "DD2",
    # 40:"DR",
    45: "MHWILDS",
    46: "ONI2",
    # 51:"PRAG",
    51: "RE9",
    "DMC5": 10,
    "RE2": 10,
    "RE3": 13,
    "RE8": 19,
    "RE2RT": 21,
    "RE3RT": 21,
    "MHRSB": 23,
    "SF6": 31,
    "RE4": 32,
    "DD2": 40,  # KG
    "KG": 40,
    "DR": 40,
    "MHWILDS": 45,
    "ONI2": 46,
    "PRAG": 51,
    "RE9": 51,
}


def getMDFVersionToGameName(gameName):
    return gameNameMDFVersionDict.get(gameName, -1)


# game name -> tex file version (the number in the .tex.<N> extension).
# Build variants report the retail name, so DD2NEW / MHWILDSBETA are skipped here.
gameNameToTexVersionDict = {
    gameProfile.name: gameProfile.texVersion
    for gameProfile in ALL_PROFILES
    if gameProfile.texVersion is not None and gameProfile.reportedName == gameProfile.name
}


def getTexVersion(gameName):
    return gameNameToTexVersionDict.get(gameName, -1)


# Mesh file extension -> MDF file extension, used to locate the .mdf2 file next
# to a .mesh file. NOTE: mdf versions 21/40/46 are intentionally absent from
# texVersionDict below (they fall back to wildcard search), but are listed here.
mdfVersionDict = {
    f".{gameProfile.meshFileVersion}": f".{gameProfile.mdfVersion}"
    for gameProfile in profilesByFileVersion.values()
    if gameProfile.mdfVersion is not None
}
# Mesh file versions that have no game row of their own.
mdfVersionDict.update({
    ".32": ".6",  # RE7
    ".2008058288": ".19",  # MHRise
    ".2010231143": ".19",  # REVerse
})

# MDF version -> tex file extension, used when locating .tex files next to an
# MDF. These games' tex versions were never confirmed, so the lookup falls back
# to a wildcard search. Do NOT add them without verifying.
_TEX_VERSION_UNCONFIRMED_MDF = frozenset((21, 40, 46, 49))  # RE2RT, DD2/KG/DR, ONI2, MHS3

texVersionDict = {6: ".8"}  # RE7: mdf2.6 has no mesh game row
for _gameProfile in profilesByFileVersion.values():
    if _gameProfile.mdfVersion is None or _gameProfile.mdfVersion in _TEX_VERSION_UNCONFIRMED_MDF:
        continue
    texVersionDict[_gameProfile.mdfVersion] = f".{_gameProfile.texVersion}"
texVersionDict = dict(sorted(texVersionDict.items()))

# Explicit per-game tex version overrides, applied before texVersionDict when
# the game name is known. PRAG retail shares mdf 51 with RE9 but ships MHS3's
# tex version, so the mdf-derived lookup would pick RE9's .250813143.
gameTexVersionDict = {
    "PRAG": f".{profile('PRAG').texVersion}",
}


# Mesh file extension -> sfur file extension, used to locate the .sfur file
# next to a .mesh. Kept here so every version table has one home.
sfurVersionDict = {
    ".221108797": ".4",  # RE4
    ".240423143": ".5",  # DD2NEW
    ".241111606": ".5",  # MHWILDS
    ".250925211": ".5",  # RE9
}
