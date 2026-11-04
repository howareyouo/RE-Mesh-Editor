# System-python test: material name rules and bulk rename planning (no bpy needed)
import os
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

from modules.naming import (getMaterialNamePassError, getMaterialNameRenameError,
                            planMaterialNameReplacement)


def testSingleRename():
    # A material name can't be empty or carry the mesh/material separator
    assert getMaterialNameRenameError([], "Shirt", "") == "Material name can't be empty."
    assert getMaterialNameRenameError([], "Shirt", "   ") == "Material name can't be empty."
    assert getMaterialNameRenameError(
        [], "Shirt", "Bad__Name") == ("\"__\" can't be part of a material name, "
                                      "it separates the mesh name from the material name.")

    # An MDF holds every material name once, compared case-insensitively
    assert getMaterialNameRenameError(
        ["Shirt", "Pants"], "Pants",
        "shirt") == "\"shirt\" is already used by another material in the MDF collection."
    assert getMaterialNameRenameError(["Shirt", "Pants"], "Pants", "Jacket") is None

    # A rename to a different case is the same material, not a duplicate
    assert getMaterialNameRenameError(["Shirt", "Pants"], "Shirt", "SHIRT") is None
    assert getMaterialNameRenameError(["Shirt", "Pants"], "Shirt", "Shirt") is None

    # The object the rename came from already carries the new name, so callers leave it out
    # of the list - the entry beside it is what decides whether the name is free
    assert getMaterialNameRenameError(["Shirt"], "Shirt", "Jacket") is None
    assert getMaterialNameRenameError(
        ["Jacket", "Pants"], "Shirt",
        "Jacket") == "\"Jacket\" is already used by another material in the MDF collection."
    print("TEST 1 PASSED: one-name rules (empty, \"__\", duplicate, case-only, source object)")


def testPassRules():
    assert getMaterialNamePassError(["Shirt", "Pants"], []) is None

    # Two materials renamed onto one name is refused before anything is written
    assert getMaterialNamePassError(
        ["shirt", "Shirt_Mat"], [(0, "Shirt"), (1, "Shirt")]) == (
        "\"Shirt\" would be the name of two materials: \"shirt\" and \"Shirt_Mat\".")

    # A planned name that an unplanned material keeps is a clash too
    assert getMaterialNamePassError(
        ["Shirt", "Shirt_Mat"],
        [(1, "Shirt")]) == "\"Shirt\" is already used by another material in the MDF collection."

    # ...but a name another planned material vacates is not
    assert getMaterialNamePassError(["Shirt", "Pants"], [(0, "Jacket"), (1, "Shirt")]) is None

    assert getMaterialNamePassError(["Shirt"], [(0, "")]) == \
        "\"Shirt\" would become an empty material name."
    assert getMaterialNamePassError(
        ["Shirt"], [(0, "A__B")]) == ("\"A__B\" can't contain \"__\", "
                                      "it separates the mesh name from the material name.")
    print("TEST 2 PASSED: pass rules (duplicate result, untouched clash, vacated name, empty/\"__\")")


def testReplacePlan():
    assert planMaterialNameReplacement(["Shirt"], "", "Extra") == ([], "Find text can't be empty.")
    assert planMaterialNameReplacement(["Shirt"], "Pants", "Extra") == ([], None)

    # Only the names holding the text are planned, and each entry keeps its index into the
    # names as they were passed in
    planList, error = planMaterialNameReplacement(["Shirt", "Pants", "Shirt_Mat"], "Shirt", "Jacket")
    assert error is None
    assert planList == [(0, "Shirt", "Jacket"), (2, "Shirt_Mat", "Jacket_Mat")]

    # A replacement that contains the text it replaces is still derived from the old name once
    planList, error = planMaterialNameReplacement(["Shirt", "Shirt_Mat"], "Shirt", "Shirt_Extra")
    assert error is None
    assert planList == [(0, "Shirt", "Shirt_Extra"), (1, "Shirt_Mat", "Shirt_Extra_Mat")]

    # The pass is refused as a whole when it would duplicate a name or clash with a kept one
    assert planMaterialNameReplacement(
        ["Ashirt", "AAshirt"], "A", "") == (
        [], "\"shirt\" would be the name of two materials: \"Ashirt\" and \"AAshirt\".")
    assert planMaterialNameReplacement(
        ["Shirt_Mat", "Shirt"], "_Mat", "") == (
        [], "\"Shirt\" is already used by another material in the MDF collection.")
    print("TEST 3 PASSED: replace plan (matching, original indices, duplicate and clash refusals)")


def testReplaceResolvesBeforeRenaming():
    # A replacement may contain the text it replaces, so one entry's new name can be another
    # entry's old name. Every mesh is resolved before the first rename (see
    # replaceMaterialNameText): a lookup made while renaming finds the objects an earlier entry
    # already renamed, and renames them twice.
    planList, error = planMaterialNameReplacement(["Shirt", "Pants_Shirt"], "Shirt", "Pants_Shirt")
    assert error is None
    assert planList == [(0, "Shirt", "Pants_Shirt"), (1, "Pants_Shirt", "Pants_Pants_Shirt")]
    meshNameList = ["Group_0_Sub_0__Shirt", "Group_0_Sub_1__Pants_Shirt"]

    def matches(objectName, materialName):
        return objectName.endswith("__" + materialName)

    def rename(objectName, oldName, newName):
        return objectName[:-len(oldName)] + newName

    eagerList = [(oldName, newName,
                  [objectName for objectName in meshNameList if matches(objectName, oldName)])
                 for _, oldName, newName in planList]
    assert eagerList == [("Shirt", "Pants_Shirt", ["Group_0_Sub_0__Shirt"]),
                         ("Pants_Shirt", "Pants_Pants_Shirt", ["Group_0_Sub_1__Pants_Shirt"])]

    lazyNameList = list(meshNameList)
    lazyList = []
    for _, oldName, newName in planList:
        lazyList.append((oldName, [objectName for objectName in lazyNameList
                                   if matches(objectName, oldName)]))
        lazyNameList = [rename(objectName, oldName, newName) if matches(objectName, oldName) else objectName
                        for objectName in lazyNameList]
    assert lazyList[1][1] == ["Group_0_Sub_0__Pants_Shirt", "Group_0_Sub_1__Pants_Shirt"]

    renamedList = []
    for oldName, newName, meshNames in eagerList:
        renamedList.extend(rename(meshName, oldName, newName) for meshName in meshNames)
    assert renamedList == ["Group_0_Sub_0__Pants_Shirt", "Group_0_Sub_1__Pants_Pants_Shirt"]
    print("TEST 4 PASSED: replacement resolves every name before the first rename")


def main():
    testSingleRename()
    testPassRules()
    testReplacePlan()
    testReplaceResolvesBeforeRenaming()
    print("\nALL MATERIAL NAME TESTS PASSED")


if __name__ == "__main__":
    main()
