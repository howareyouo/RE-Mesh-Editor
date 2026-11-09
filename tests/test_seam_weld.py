# System-python test: UV-seam normal welding on parsed export data (no bpy needed)
# Run with: python tests/test_seam_weld.py
import os
import sys
import unittest
from types import SimpleNamespace

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

import numpy as np
from modules.mesh.re_mesh_seam_weld import weldSeamNormals

# A flat square split into two triangles that do not share vertices along the
# diagonal: vertices 0/1 and 3/4 are the two copies of each seam vertex.
POSITIONS = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0), (1, 0, 0), (1, 1, 0)]
FACES = [(0, 1, 2), (3, 4, 5)]
SEAM_UVS = [(0, 0), (1, 0), (0, 1), (0, 0.5), (1, 0.5), (1, 1)]
TILTED = (0, 0.5, 0.866)  # 30 degrees off the flat normal


def _quadMesh(otherNormals=TILTED, otherTangent=(127, 0, 0, 0), otherUVs=SEAM_UVS[3:]):
	positions = np.asarray(POSITIONS, np.float32)
	normals = np.asarray([(0, 0, 1)] * 3 + [tuple(otherNormals)] * 3, np.float32)
	tangents = np.asarray([(127, 0, 0, 0)] * 3 + [tuple(otherTangent)] * 3, np.uint8)
	uvs = np.asarray(SEAM_UVS[:3] + list(otherUVs), np.float32)
	subMesh = SimpleNamespace(vertexPosList=positions, faceList=np.asarray(FACES, np.uint32),
	                          normalList=normals, tangentList=tangents, uvList=uvs,
	                          isReusedMesh=False)
	parsedMesh = SimpleNamespace(mainMeshLODList=[
		SimpleNamespace(visconGroupList=[SimpleNamespace(subMeshList=[subMesh])])])
	return subMesh, parsedMesh


class SeamWeldTest(unittest.TestCase):
	def test_split_normals_on_a_uv_seam_are_welded(self):
		subMesh, parsedMesh = _quadMesh()
		stats = weldSeamNormals(parsedMesh)
		self.assertEqual(stats["welded_groups"], 2)
		self.assertEqual(stats["welded_verts"], 4)
		self.assertEqual(stats["crease_kept"], 0)
		# Only the seam duplicates are welded; vertex 5 has no duplicate to share with.
		np.testing.assert_allclose(subMesh.normalList[:5], [(0, 0, 1)] * 5, atol=1e-6)
		np.testing.assert_allclose(subMesh.normalList[5], TILTED, atol=1e-6)

	def test_single_vertex_positions_are_untouched(self):
		subMesh, parsedMesh = _quadMesh()
		weldSeamNormals(parsedMesh)
		np.testing.assert_allclose(subMesh.normalList[5], TILTED, atol=1e-6)

	def test_real_crease_is_kept(self):
		subMesh, parsedMesh = _quadMesh(otherNormals=(0, 1, 0))
		stats = weldSeamNormals(parsedMesh)
		self.assertEqual(stats["welded_groups"], 0)
		self.assertEqual(stats["crease_kept"], 2)
		np.testing.assert_allclose(subMesh.normalList[3], (0, 1, 0), atol=1e-6)

	def test_duplicates_with_one_uv_are_a_hard_edge(self):
		subMesh, parsedMesh = _quadMesh(otherUVs=[(0, 0), (1, 0), (1, 1)])
		stats = weldSeamNormals(parsedMesh)
		self.assertEqual(stats["same_uv_hard_edge"], 2)
		self.assertEqual(stats["welded_groups"], 0)
		np.testing.assert_allclose(subMesh.normalList[3], TILTED, atol=1e-6)

	def test_groups_that_already_share_a_normal_are_left_alone(self):
		subMesh, parsedMesh = _quadMesh(otherNormals=(0, 0, 1))
		stats = weldSeamNormals(parsedMesh)
		self.assertEqual(stats["already_shared"], 2)
		self.assertEqual(stats["welded_groups"], 0)

	def test_tangent_is_orthogonalised_against_the_welded_normal(self):
		# (0, 0.8, 0.6) has a 0.6 component along the target normal (0, 0, 1)
		subMesh, parsedMesh = _quadMesh(otherTangent=(0, 101, 76, 0))
		weldSeamNormals(parsedMesh)
		self.assertEqual(subMesh.tangentList[3].tolist(), [0, 127, 0, 0])


if __name__ == "__main__":
	unittest.main()