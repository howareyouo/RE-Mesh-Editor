# System-python test: occlusion LOD parsing via parseLODStructure (no bpy needed)
import os
import sys
import struct

ADDON_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ADDON_ROOT)

import numpy as np
from modules.mesh.re_mesh_parse import parseLODStructure


class Stub:
    pass


def buildReMesh(blendShapeHeader):
    reMesh = Stub()
    reMesh.lodHeader = Stub()
    reMesh.lodHeader.has32BitIndexBuffer = 0
    reMesh.blendShapeHeader = blendShapeHeader
    return reMesh


def buildOcclusionGroup():
    lodGroup = Stub()
    lodGroup.distance = 75.0
    viscon = Stub()
    viscon.visconGroupID = 7
    viscon.vertexCount = 2
    sub = Stub()
    sub.materialIndex = 0
    sub.isQuad = 0
    sub.vertexBufferIndex = 0
    sub.faceCount = 3  # RE format: faceCount counts indices, 1 triangle = 3
    sub.faceStartIndex = 0
    sub.vertexStartIndex = 4  # points into the shared vertex buffer past the main mesh verts
    viscon.vertexInfoList = [sub]
    lodGroup.meshGroupList = [viscon]
    return lodGroup


def buildVertexDicts():
    # Shared vertex buffer: 6 verts, occlusion group uses verts 4-5
    position = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
                         [5, 5, 5], [6, 5, 5]], dtype=np.float32)
    return [{"Position": position, "NorTan": None, "UV": None, "UV2": None,
             "Weight": None, "ExtraWeight": None, "Color": None, "SecondaryWeight": None}]


def main():
    faceBuffer = struct.pack("<6H", 0, 1, 2, 0, 2, 3)  # 2 triangles of uint16 indices

    # 1. Occlusion group parses with blend shapes disabled even when a blend shape header exists
    reMesh = buildReMesh(blendShapeHeader=object())  # sentinel: would crash without parseBlendShapes=False
    lodList = parseLODStructure(reMesh, [buildOcclusionGroup()], buildVertexDicts(),
                                [faceBuffer], [dict()], blendShapeBuffer=None,
                                parseBlendShapes=False)
    assert len(lodList) == 1, "expected one parsed LOD"
    lod = lodList[0]
    assert lod.lodDistance == 75.0
    assert len(lod.visconGroupList) == 1
    viscon = lod.visconGroupList[0]
    assert viscon.visconGroupNum == 7
    assert len(viscon.subMeshList) == 1
    sub = viscon.subMeshList[0]
    assert sub.meshVertexOffset == 4
    assert np.allclose(np.asarray(sub.vertexPosList), [[5, 5, 5], [6, 5, 5]]), sub.vertexPosList
    assert sub.faceList.shape == (1, 3) and list(sub.faceList[0]) == [0, 1, 2]
    assert sub.blendShapeList == [], "occlusion submesh must not receive blend shapes"
    print("TEST A1 PASSED: occlusion LOD parsed, blend shapes skipped")

    # 2. Default call (parseBlendShapes=True) still works without a blend shape header
    reMesh2 = buildReMesh(blendShapeHeader=None)
    lodList2 = parseLODStructure(reMesh2, [buildOcclusionGroup()], buildVertexDicts(),
                                 [faceBuffer], [dict()])
    assert len(lodList2) == 1 and len(lodList2[0].visconGroupList[0].subMeshList) == 1
    print("TEST A2 PASSED: default parse call unchanged")

    # 3. Vertex reuse: same vertexStartIndex marks the submesh as reused
    reMesh3 = buildReMesh(blendShapeHeader=None)
    reusedGroup = buildOcclusionGroup()
    reusedGroup.meshGroupList[0].vertexInfoList[0].vertexStartIndex = 0  # same as "main mesh" offset
    usedDicts = [dict()]  # simulate main mesh having registered offset 0
    usedDicts[0][0] = "MAIN_MESH_SUBMESH"  # value unused, only presence matters
    lodList3 = parseLODStructure(reMesh3, [reusedGroup], buildVertexDicts(),
                                 [faceBuffer], usedDicts)
    sub3 = lodList3[0].visconGroupList[0].subMeshList[0]
    assert sub3.isReusedMesh is True and sub3.linkedSubMesh == "MAIN_MESH_SUBMESH"
    print("TEST A3 PASSED: occlusion submesh reusing main mesh vertices is linked, not duplicated")

    print("\nALL PARSE TESTS PASSED")


if __name__ == "__main__":
    main()
