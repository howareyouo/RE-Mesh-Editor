"""Synthetic checks for legacy RE Engine blend-shape import.

Ported from REME v0.73 scratch_test_legacy_blend_shapes.py and adapted to this
addon's package layout. Runs without Blender: `python tests/test_legacy_blend_shapes.py`
"""

from __future__ import annotations

import io
import os
import struct
import sys
import types
import unittest

import numpy as np


def _ensure_package_import():
	repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	parent_dir = os.path.dirname(repo_dir)
	if parent_dir not in sys.path:
		sys.path.insert(0, parent_dir)
	if "REMeshTestAddon" not in sys.modules:
		pkg = types.ModuleType("REMeshTestAddon")
		pkg.__path__ = [repo_dir]
		sys.modules["REMeshTestAddon"] = pkg


_ensure_package_import()

from REMeshTestAddon.modules.mesh import file_re_mesh as mesh_file  # noqa: E402
from REMeshTestAddon.modules.mesh import re_mesh_parse as mesh_parse  # noqa: E402


def _make_aabb():
	box = mesh_file.AABB()
	box.min.x, box.min.y, box.min.z = -1.0, -2.0, -3.0
	box.max.x, box.max.y, box.max.z = 1.0, 2.0, 3.0
	return box


class LegacyBlendShapeHeaderTest(unittest.TestCase):
	def test_legacy_blend_header_uses_compact_layout(self):
		stream = io.BytesIO(bytearray(160))
		stream.seek(0)
		stream.write(bytes([1]))  # u8 count
		stream.write((0x123456789ABC).to_bytes(7, byteorder="little"))  # skipped
		stream.write(struct.pack("<Q", 16))  # mainOffset
		stream.seek(16)
		stream.write(struct.pack("<Q", 32))  # offset table entry -> BlendShapeData
		stream.seek(32)
		stream.write(struct.pack("<HHIIIQQ", 1, 0, 0, 0, 0, 64, 80))  # BlendShapeData (legacy: no blendS offsets)
		stream.seek(64)
		stream.write(struct.pack("<IIHHI", 3, 5, 0, 2, 0))  # BlendTarget (legacy layout)
		stream.seek(80)
		# AABB min/max.
		stream.write(struct.pack("<4f4f", -1.0, -2.0, -3.0, 0.0, 1.0, 2.0, 3.0, 0.0))
		stream.write(struct.pack("<3i", 5, 6, 7))  # blendS
		stream.write(struct.pack("<2i", 10, 11))  # blendSSList

		stream.seek(0)
		header = mesh_file.BlendShapeHeader()
		header.read(stream, mesh_file.VERSION_RE8)

		self.assertEqual(header.count, 1)
		data = header.blendShapeList[0]
		self.assertEqual(data.targetCount, 1)
		self.assertEqual(data.blendS, [5, 6, 7])
		self.assertEqual(data.blendSSList, [10, 11])
		target = data.blendTargetList[0]
		self.assertEqual(target.subMeshVertexStartIndex, 3)
		self.assertEqual(target.vertCount, 5)
		self.assertEqual(target.blendShapeNum, 2)

	def test_sf6_header_layout_unchanged(self):
		# SF6+ layout must keep the u64 count path (regression guard).
		stream = io.BytesIO(bytearray(160))
		stream.write(struct.pack("<QQQQ", 1, 0, 32, 0))  # count, zero, mainOffset, hash
		stream.write(struct.pack("<Q", 48))  # offset table entry
		stream.seek(0)
		header = mesh_file.BlendShapeHeader()
		header.read(stream, mesh_file.VERSION_SF6)
		self.assertEqual(header.count, 1)
		self.assertEqual(header.blendShapeOffsetList, [48])


class BlendDeltaRemapTest(unittest.TestCase):
	def test_legacy_delta_unpack_and_aabb_remap(self):
		packed = np.array([2047 | (511 << 11) | (0 << 21)], dtype="<u4").tobytes()
		normalized = mesh_parse.ReadBlendShapeByteBuffer(packed, set())
		self.assertTrue(np.allclose(normalized[0], [1.0, 511 / 1023, 0.0], atol=1e-6))

		remapped = mesh_parse.remapBlendShapeDeltas(normalized, _make_aabb())
		expected = np.array([[1.0, -0.001955, -3.0]], dtype=np.float32)
		self.assertTrue(np.allclose(remapped, expected, atol=1e-5), remapped)

	def test_zero_is_sentinel(self):
		deltas = np.array([[0.0, 0.0, 0.0], [1.0, 0.5, 0.25]], dtype=np.float32)
		box = _make_aabb()
		kept = mesh_parse.remapBlendShapeDeltas(deltas, box, zeroIsSentinel=True)
		self.assertEqual(kept[0].tolist(), [0.0, 0.0, 0.0])  # sentinel stays zero
		self.assertFalse(np.allclose(kept[1], 0.0))
		# Without the flag the all-zero row decodes to the aabb min.
		raw = mesh_parse.remapBlendShapeDeltas(deltas, box)
		self.assertTrue(np.allclose(raw[0], [-1.0, -2.0, -3.0], atol=1e-6))
		# Source array must not be modified in place.
		self.assertEqual(deltas[0].tolist(), [0.0, 0.0, 0.0])


if __name__ == "__main__":
	unittest.main()
