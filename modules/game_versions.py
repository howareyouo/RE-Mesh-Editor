# MDF and tex version tables shared across the mesh, mdf and tex modules.
#
# The per-game numbers live in modules/mesh/game_profile.py and the tables below
# derive from those rows. Add a game by adding a row to game_profile._GAME_ROWS.

from .mesh.game_profile import ALL_PROFILES, profilesByFileVersion

# MDF version <-> game name (bidirectional). Aliases share one MDF version: the
# numeric key resolves to the row flagged mdfAliasWinner and every mdfAlias row
# appears in the name direction. Built from profilesByFileVersion so RE Verse's
# mdf2.20 is listed even though it has no row of its own.
gameNameMDFVersionDict = {
	gameProfile.mdfVersion: gameProfile.name
	for gameProfile in profilesByFileVersion.values()
	if gameProfile.mdfAliasWinner
}
gameNameMDFVersionDict.update({
	gameProfile.name: gameProfile.mdfVersion
	for gameProfile in ALL_PROFILES
	if gameProfile.mdfAlias
})


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
TEX_VERSION_UNCONFIRMED_MDF = frozenset((21, 40, 46, 49))  # RE2RT, DD2/KG/DR, ONI2, MHS3

texVersionDict = {6: ".8"}  # RE7: mdf2.6 has no mesh game row
for _gameProfile in profilesByFileVersion.values():
    if _gameProfile.mdfVersion is None or _gameProfile.mdfVersion in TEX_VERSION_UNCONFIRMED_MDF:
        continue
    texVersionDict[_gameProfile.mdfVersion] = f".{_gameProfile.texVersion}"
texVersionDict = dict(sorted(texVersionDict.items()))


# Mesh file extension -> sfur file extension, used to locate the .sfur file
# next to a .mesh. Kept here so every version table has one home.
sfurVersionDict = {
    ".221108797": ".4",  # RE4
    ".240423143": ".5",  # DD2NEW
    ".241111606": ".5",  # MHWILDS
    ".250925211": ".5",  # RE9
}
