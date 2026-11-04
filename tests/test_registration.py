# System-python test: registration order derived from the class annotations (no bpy needed)
import os
import sys
from collections import namedtuple

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

from modules.registration import orderClasses

PropertyDeferred = namedtuple("PropertyDeferred", "func keywords")


def annotated(name, *referenced):
    """A stand-in class carrying the annotation values Blender's props helpers return."""
    return type(name, (), {"__annotations__": {
        f"p{index}": PropertyDeferred("CollectionProperty", {"type": reference})
        for index, reference in enumerate(referenced)}})


def testDependantComesLast():
    entryGroup = annotated("EntryGroup")
    exporter = annotated("Exporter", entryGroup)
    assert orderClasses([exporter, entryGroup]) == [entryGroup, exporter], "property group must register first"
    print("TEST 1 PASSED: an annotated property type registers before the class that lists it")


def testChainResolves():
    leaf = annotated("Leaf")
    middle = annotated("Middle", leaf)
    top = annotated("Top", middle)
    assert orderClasses([top, middle, leaf]) == [leaf, middle, top], "a chain has to resolve deepest first"
    print("TEST 2 PASSED: a chain of annotated types resolves deepest first")


def testIndependentClassesKeepInputOrder():
    first = annotated("First")
    second = annotated("Second")
    assert orderClasses([first, second]) == [first, second], "unrelated classes must not be reordered"
    assert orderClasses([second, first]) == [second, first], "unrelated classes must not be reordered"
    print("TEST 3 PASSED: classes with no dependency keep their input order")


def testDuplicatesCollapseOnce():
    group = annotated("Group")
    exporter = annotated("Exporter", group)
    assert orderClasses([group, exporter, group]) == [group, exporter], "each class registers once"
    print("TEST 4 PASSED: a class listed twice in the source order registers once")


def testMutualReferenceKeepsInputOrder():
    # Two classes that name each other cannot be registered in any order; Blender reports
    # the clash, so the source order is handed over unchanged.
    left = annotated("Left")
    right = annotated("Right", left)
    left.__annotations__["p0"] = PropertyDeferred("PointerProperty", {"type": right})
    assert orderClasses([left, right]) == [left, right], "a cycle must not drop classes"
    print("TEST 5 PASSED: a cycle keeps every class in input order")


def testUnannotatedClassPassesThrough():
    plain = type("Plain", (), {})
    group = annotated("Group")
    exporter = annotated("Exporter", group)
    assert orderClasses([plain, exporter, group]) == [plain, group, exporter], "classes without properties stay put"
    print("TEST 6 PASSED: classes without properties register where they are declared")


def main():
    testDependantComesLast()
    testChainResolves()
    testIndependentClassesKeepInputOrder()
    testDuplicatesCollapseOnce()
    testMutualReferenceKeepsInputOrder()
    testUnannotatedClassPassesThrough()
    print("\nALL REGISTRATION TESTS PASSED")


if __name__ == "__main__":
    main()
