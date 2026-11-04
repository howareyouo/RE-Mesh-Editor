# System-python test: texture binding path resolution via modules.tex.tex_paths (no bpy needed)
import os
import shutil
import sys
import tempfile

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

from modules.tex.tex_paths import (SearchRoot, TEX_MATCH_BASE, TEX_MATCH_VERSION, findTextureFile,
                                   findTextureFiles, getStreamingTexturePath, getTextureFileBaseName,
                                   normalizeTexturePath, replaceTextureFileBaseName)


def touch(rootPath, relPath):
    filePath = os.path.join(rootPath, relPath.replace("/", os.sep))
    os.makedirs(os.path.dirname(filePath), exist_ok=True)
    with open(filePath, "wb"):
        pass
    return filePath


def expectedPath(rootPath, relPath):
    return os.path.normpath(os.path.join(rootPath, relPath.replace("/", os.sep)))


def buildFakeMod(tempDir):
    # chunk = a game chunk folder the mdf2 references, mod = the mod's natives folder
    chunkDir = os.path.join(tempDir, "chunk")
    modDir = os.path.join(tempDir, "mod")
    for relPath in ("chars/hero/hero_ALBM.tex.7602304",        # the current revision
                    "chars/hero/hero_ALBM.tex.7602304.pc",     # same revision per platform
                    "chars/hero/hero_ALBM.tex.7602304.ps4",
                    "chars/hero/hero_ALBM.tex.111",            # an older revision
                    "chars/hero/hero_NRM.tex.7602304",         # another texture of the folder
                    "roottex.tex.555.pc",                      # platform suffix, at the chunk root
                    "rootany.tex.999",                         # only another revision on disk
                    "flat/plain.tex",                          # no version suffix at all
                    "flat/only_plain.tex.555",
                    "flat/only_plain.tex.111",
                    "flat/rootstream.tex.555",                 # also present as a streaming copy
                    "streaming/flat/rootstream.tex.555"):
        touch(chunkDir, relPath)
    for relPath in ("deep/modlocal_ALBM.tex.42",
                    "streaming/deep/streamed_ALBM.tex.42"):    # streaming only
        touch(modDir, relPath)
    return chunkDir, modDir


def testBaseNameFiles(chunkDir, modDir):
    # Every platform and revision of a base name is a file the rename has to follow
    fileList = findTextureFiles("chars/hero/hero_ALBM.tex", [SearchRoot(chunkDir)], TEX_MATCH_BASE)
    assert fileList == [expectedPath(chunkDir, "chars/hero/hero_ALBM.tex.111"),
                        expectedPath(chunkDir, "chars/hero/hero_ALBM.tex.7602304"),
                        expectedPath(chunkDir, "chars/hero/hero_ALBM.tex.7602304.pc"),
                        expectedPath(chunkDir, "chars/hero/hero_ALBM.tex.7602304.ps4")], fileList
    print("TEST 1 PASSED: base name matching collects every revision and platform of one texture")

    # A binding path carrying a version suffix and backslashes resolves to the same files
    # (the suffix is not part of the name the files are matched on)
    versionedList = findTextureFiles("chars\\hero\\hero_ALBM.tex.7602304.pc", [SearchRoot(chunkDir)], TEX_MATCH_BASE)
    assert versionedList == fileList, versionedList
    print("TEST 2 PASSED: a binding path with a version suffix and backslashes still resolves")

    # The streaming sibling folder is searched, and only when the root asks for it
    streamedOnly = [expectedPath(modDir, "streaming/deep/streamed_ALBM.tex.42")]
    assert findTextureFiles("deep/streamed_ALBM.tex", [SearchRoot(modDir)], TEX_MATCH_BASE) == streamedOnly
    assert findTextureFiles("deep/streamed_ALBM.tex", [SearchRoot(modDir, streaming = False)], TEX_MATCH_BASE) == []
    print("TEST 3 PASSED: streaming-only texture found, streaming search skipped when the root disables it")

    # The caller decides which folder of a root the binding's folder maps to
    relDirList = [expectedPath(modDir, "deep/modlocal_ALBM.tex.42")]
    assert findTextureFiles("deep/modlocal_ALBM.tex", [SearchRoot(modDir, "deep", False)], TEX_MATCH_BASE) == relDirList
    assert findTextureFiles("deep/modlocal_ALBM.tex", [SearchRoot(chunkDir, streaming = False)], TEX_MATCH_BASE) == []
    print("TEST 4 PASSED: roots searched in order, files collected across roots")

    # Several roots add up, and a repeated root does not list a file twice
    bothRootList = findTextureFiles("chars/hero/hero_ALBM.tex", [SearchRoot(chunkDir, streaming = False),
                                                                SearchRoot(chunkDir, streaming = False)], TEX_MATCH_BASE)
    assert len(bothRootList) == 4, bothRootList
    print("TEST 5 PASSED: a repeated root is searched once")

    # A texture that is not on disk is an empty result, not an error
    assert findTextureFiles("chars/hero/missing_ALBM.tex", [SearchRoot(chunkDir), SearchRoot(modDir)], TEX_MATCH_BASE) == []
    assert findTextureFile("chars/hero/missing_ALBM.tex", [SearchRoot(chunkDir)], TEX_MATCH_BASE) is None
    assert findTextureFiles("", [SearchRoot(chunkDir)], TEX_MATCH_BASE) == []
    print("TEST 6 PASSED: a missing texture returns an empty result without raising")

    # Render targets and non texture bindings have no texture file to find
    assert findTextureFiles("chars/hero/hero_ALBM.rtex", [SearchRoot(chunkDir)], TEX_MATCH_BASE) == []
    assert findTextureFiles("chars/hero/hero_ALBM.dds", [SearchRoot(chunkDir)], TEX_MATCH_BASE) == []
    print("TEST 7 PASSED: .rtex and non texture paths resolve to nothing")


def testVersionFiles(chunkDir):
    # The revision the mdf2 names wins over the others in the same folder
    assert findTextureFile("flat/only_plain.tex", [SearchRoot(chunkDir)], ".555", TEX_MATCH_VERSION) == \
        expectedPath(chunkDir, "flat/only_plain.tex.555")
    assert findTextureFile("flat/only_plain.tex", [SearchRoot(chunkDir)], ".111", TEX_MATCH_VERSION) == \
        expectedPath(chunkDir, "flat/only_plain.tex.111")
    print("TEST 8 PASSED: the requested version is preferred over the other versions on disk")

    # Streaming copies of a texture are the ones the game streams in
    assert findTextureFile("flat/rootstream.tex", [SearchRoot(chunkDir)], ".555", TEX_MATCH_VERSION) == \
        expectedPath(chunkDir, "streaming/flat/rootstream.tex.555")
    print("TEST 9 PASSED: the streaming copy wins when both folders hold the texture")

    # A texture without a version suffix is found by its plain name
    assert findTextureFile("flat/plain.tex", [SearchRoot(chunkDir)], "", TEX_MATCH_VERSION) == \
        expectedPath(chunkDir, "flat/plain.tex")
    print("TEST 10 PASSED: a texture without a version suffix resolves")

    # The game appends platform and language suffixes to the version name
    assert findTextureFile("roottex.tex", [SearchRoot(chunkDir)], ".555", TEX_MATCH_VERSION) == \
        expectedPath(chunkDir, "roottex.tex.555.pc")
    print("TEST 11 PASSED: platform suffix after the version name is accepted")

    # Any other revision is accepted when the requested one is not on disk
    assert findTextureFile("rootany.tex", [SearchRoot(chunkDir)], ".555", TEX_MATCH_VERSION) == \
        expectedPath(chunkDir, "rootany.tex.999")
    print("TEST 12 PASSED: another revision on disk is used when the requested one is missing")

    # The any revision fallback compares against the bare names of the chunk folder, so
    # it only reaches textures at the root of a chunk - the scope the import always had
    assert findTextureFile("chars/hero/hero_ALBM.tex", [SearchRoot(chunkDir)], ".999", TEX_MATCH_VERSION) is None
    assert findTextureFile("chars/hero/missing_ALBM.tex", [SearchRoot(chunkDir)], ".555", TEX_MATCH_VERSION) is None
    print("TEST 13 PASSED: the any revision fallback stays out of sub folders")

    # The cached listing the import passes in is what the search reads
    seenFolderList = []

    def listDir(folderPath):
        seenFolderList.append(folderPath)
        return tuple(sorted(os.listdir(folderPath)))

    assert findTextureFile("roottex.tex", [SearchRoot(chunkDir)], ".555", TEX_MATCH_VERSION,
                           listDir = listDir) == expectedPath(chunkDir, "roottex.tex.555.pc")
    assert seenFolderList == [os.path.join(chunkDir, "streaming"), chunkDir], seenFolderList
    print("TEST 14 PASSED: the search reads the listing through the caller's cached callback")

    # The case-insensitive resolver is the last resort, per root
    candidateList = []

    def resolvePath(candidatePath):
        candidateList.append(candidatePath)
        if candidatePath == os.path.join(chunkDir, "resolved.tex.7"):
            return expectedPath(chunkDir, "rootany.tex.999")
        return None

    assert findTextureFile("resolved.tex", [SearchRoot(chunkDir)], ".7", TEX_MATCH_VERSION,
                           resolvePath = resolvePath) == expectedPath(chunkDir, "rootany.tex.999")
    assert candidateList == [os.path.join(chunkDir, "streaming", "resolved.tex.7"),
                             os.path.join(chunkDir, "resolved.tex.7")], candidateList
    assert findTextureFile("resolved.tex", [SearchRoot(chunkDir)], ".7", TEX_MATCH_VERSION) is None
    print("TEST 15 PASSED: the resolver fallback is tried last and only when the caller passes one")


def testPathHelpers():
    assert normalizeTexturePath("") == ""
    assert normalizeTexturePath("chars\\hero\\hero_ALBM.tex.7602304") == "chars/hero/hero_ALBM.tex"
    assert normalizeTexturePath("/chars/hero/hero_ALBM.tex/") == "chars/hero/hero_ALBM.tex"
    #A chunk root inside the path is dropped, the way the game resolves the path
    assert normalizeTexturePath("Chars/natives/STM/hero/hero_ALBM.tex.7602304") == "hero/hero_ALBM.tex"
    assert normalizeTexturePath("ui/target.rtex") == "ui/target.rtex"
    print("TEST 16 PASSED: binding paths normalise to the path the game resolves")

    assert getTextureFileBaseName("hero_ALBM.tex") == "hero_ALBM.tex"
    assert getTextureFileBaseName("hero_ALBM.tex.7602304") == "hero_ALBM.tex"
    assert getTextureFileBaseName("hero_ALBM.tex.7602304.pc") == "hero_ALBM.tex"
    assert getTextureFileBaseName("hero_ALBM.rtex") is None
    assert getTextureFileBaseName("hero_ALBM.txt") is None
    assert getTextureFileBaseName(".tex") is None
    print("TEST 17 PASSED: the .tex base name of a file is found or refused")

    assert replaceTextureFileBaseName("hero_ALBM.tex.7602304.pc", "pl_ALBM.tex") == "pl_ALBM.tex.7602304.pc"
    assert replaceTextureFileBaseName("hero_ALBM.rtex", "pl.rtex") == "hero_ALBM.rtex"
    print("TEST 18 PASSED: renaming keeps the version and platform suffix of the file")

    nativesPath = os.path.join("mods", "natives", "STM", "hero", "foo.tex.42")
    assert getStreamingTexturePath(nativesPath) == \
        os.path.join("mods", "natives", "STM", "streaming", "hero", "foo.tex.42")
    print("TEST 19 PASSED: the streaming path mirrors the texture path under the natives folder")


def main():
    tempDir = tempfile.mkdtemp(prefix = "re_tex_paths_")
    try:
        chunkDir, modDir = buildFakeMod(tempDir)
        testBaseNameFiles(chunkDir, modDir)
        testVersionFiles(chunkDir)
        testPathHelpers()
    finally:
        shutil.rmtree(tempDir, ignore_errors = True)
    print("\nALL TEX PATH TESTS PASSED")


if __name__ == "__main__":
    main()
