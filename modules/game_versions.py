# Single source of truth for game / file-format version tables shared across
# mesh, mdf and tex modules. All version dicts used by multiple modules live
# here; other modules import from here instead of redefining them.
#
# GAME_INFO below is the master reference table for adding a new game:
#   - mesh: the number in the .mesh.<N> file extension
#   - mdf:  the number in the .mdf2.<N> file extension
#   - tex:  the number in the .tex.<N> file extension
#   - remap: the remapped mesh version (see modules/mesh/mesh_versions.py)
#
# When adding a game, update GAME_INFO AND the derived dicts that follow.

GAME_INFO = {
    # game      mesh         mdf  tex         remap  notes
    "DMC5":    (1808282334,  10,   11,          75),  # mesh shared w/ RE2 family lookup
    "RE2":     (1808312334,  10,   10,          80),
    "RE3":     (1902042334,  13,   190820018,   85),
    "RE8":     (2101050001,  19,   30,          90),  # also RE VERSE (2102020001 -> 20/31)
    "RE2RT":   (2109108288,  21,   34,          95),  # RE3RT shares mdf 21
    "RE7RT":   (220128762,   21,   35,          96),
    "MHRSB":   (2109148288,  23,   28,          100),
    "SF6":     (230110883,   31,   241101895,   105),
    "RE4":     (221108797,   32,   143221013,   110),
    "DD2":     (231011879,   40,   760230703,   115),  # KG/DR/DD2NEW share mdf 40
    "KG":      (240306278,   40,   231106777,   120),
    "DD2NEW":  (240423143,   40,   760230703,   124),
    "DR":      (240424828,   40,   240606151,   125),
    "MHWILDSBETA": (240820143, 45, 241106027,   130),  # beta build
    "MHWILDS": (241111606,   45,   241106027,   130),
    "ONI2":    (240827123,   46,   240701001,   127),
    "MHS3":    (250604100,   49,   251111100,   136),  # mdf2.49 has no dedicated parser yet
    "PRAG":    (250925211,   51,   250813143,   135),  # demo build, shares file versions w/ RE9
    "RE9":     (250925211,   51,   250813143,   140),
}

# MDF version <-> game name (bidirectional). Alias games share one mdf version;
# the int key maps to the first listed game.
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


# game name -> tex file version (the number in the .tex.<N> extension)
gameNameToTexVersionDict = {
    "DMC5": 11,
    "RE2": 10,
    "RE3": 190820018,
    "MHR": 28,
    "MHRSB": 28,
    "RE8": 30,
    "RE2RT": 34,
    "RE3RT": 34,
    "RE7RT": 35,
    "RE4": 143221013,
    "SF6": 241101895,
    "DD2": 760230703,
    "KG": 231106777,
    "DR": 240606151,
    "MHWILDS": 241106027,
    "ONI2": 240701001,
    "MHS3": 251111100,
    "PRAG": 250813143,
    "RE9": 250813143,
}


def getTexVersion(gameName):
    return gameNameToTexVersionDict.get(gameName, -1)


# Mesh file extension -> MDF file extension, used to locate the .mdf2 file next
# to a .mesh file. NOTE: mdf versions 21/40/46 are intentionally absent from
# texVersionDict below (they fall back to wildcard search), but are listed here.
mdfVersionDict = {
    ".1808312334": ".10",  # RE2
    ".1902042334": ".13",  # RE3
    ".32": ".6",  # RE7
    ".2101050001": ".19",  # RE8
    ".2102020001": ".20",  # RE VERSE
    ".1808282334": ".10",  # DMC5
    ".2008058288": ".19",  # MHRise
    ".2109148288": ".23",  # MHRiseSunbreak
    ".2010231143": ".19",  # REVerse
    ".2109108288": ".21",  # RERT
    ".220128762": ".21",  # RE7RT
    ".221108797": ".32",  # RE4
    ".230110883": ".31",  # SF6
    ".231011879": ".40",  # DD2
    ".240423143": ".40",  # DD2NEW
    ".240424828": ".40",  # DR
    ".240820143": ".45",  # MHWILDS
    ".241111606": ".45",  # MHWILDS
    ".240827123": ".46",  # ONI2
    ".250604100": ".49",  # MHS3
    # ".250925211": ".51",  # PRAG
    ".250925211": ".51",  # RE9
}

# MDF version -> tex file extension, used when locating .tex files next to an
# MDF. mdf versions 21 (RE2RT family), 40 (DD2/KG/DR) and 46 (ONI2) are
# intentionally absent: those games' tex versions were never confirmed, so the
# lookup falls back to a wildcard search. Do NOT add them without verifying.
texVersionDict = {
    6: ".8",
    10: ".10",
    13: ".190820018",
    19: ".30",
    20: ".31",
    # 21: ".34",  # unscannable, leave for wildcard search
    23: ".28",
    32: ".143221013",
    # 40: ".760230703",  # unscannable, leave for wildcard search
    45: ".241106027",
    51: ".250813143",
}