"""Weld the split normals of duplicate vertices along UV seams on export.

Symptom this fixes: a modded mesh often smooths normals per UV island, so the two
copies of each seam vertex each carry their own normal. In game that renders as a
brightness step along the seam that no texture edit removes, because the normals are
not shared at the seam (not a texture problem).

Ported from RE-Engine-Kit's `mesh_seam_weld.py` / `rekit/mesh/seams.py` (MIT). The
CLI script decodes a `.mesh.N` and works on it; the addon already holds the same data
in `ParsedREMesh` before serialization (`SubMesh.vertexPosList` / `normalList` /
`tangentList` / `uvList` / `faceList`), so the export welds in memory and never reads
the file back.

Rules (identical to the reference):
- Group the submesh's vertices by quantised position.
- Within a group, vertices that share one UV (wrapped) are a real hard edge: keep.
- The target normal is the area-weighted average of the faces at that position.
- Any member further than `WELD_THRESH` (60\u00b0) from the target is a real crease: keep.
- Tangents are re-orthogonalised against the new normal, `w` (handedness) is kept.
- Only the welded vertices change; every other byte of the export is untouched.

60\u00b0 is the reference default: real creases on the measured mesh sat above 60\u00b0 while
the UV-seam splits sat below it; 120\u00b0 starts flattening genuine creases.
"""
from __future__ import annotations

import numpy as np

POS_QUANT = 1e-5      # position quantisation step: duplicate vertices merge at this
UV_EPS = 1e-4         # UVs closer than this (after wrapping) are the same UV
WELD_THRESH = 60.0    # deviation past this is a real crease, not a seam split
SPLIT_EPS = 1.0       # normals already this close share a normal: nothing to weld
TANGENT_MIN = 0.2     # tangent shorter than this after projection keeps its old value
NORMAL_SCALE = 127.0  # stored normal/tangent = floor(value * 127) as int8


def _angles(a, b):
	"""Row-wise angle between vectors, in degrees."""
	c = np.clip((np.asarray(a, np.float64) * np.asarray(b, np.float64)).sum(-1), -1.0, 1.0)
	return np.degrees(np.arccos(c))


def _uvWrapDistance(a, b):
	"""Max component distance after UV wrapping, so a 0/1 seam is not mistaken for a gap.

	Keeps the input dtype: callers pass float32 UVs and the comparison against `UV_EPS`
	must stay in float32, like the rest of the per-vertex math."""
	d = np.abs(np.asarray(a) - np.asarray(b))
	return np.max(np.abs(d - np.round(d)), axis=-1)


def _runs(sortedKeys):
	"""Start index and length of each run of equal values in an ascending array."""
	if sortedKeys.size == 0:
		return np.zeros(0, np.int64), np.zeros(0, np.int64)
	bounds = np.flatnonzero(np.diff(sortedKeys)) + 1
	starts = np.concatenate(([0], bounds))
	ends = np.concatenate((bounds, [sortedKeys.size]))
	return starts, ends - starts


def _encodeNormalBytes(normals):
	"""The bytes `WriteToNorTanBuffer` stores for these normals, as unsigned ints."""
	return np.floor(np.asarray(normals, np.float32) * NORMAL_SCALE).astype(np.int32) & 0xFF


def _weldSubMesh(subMesh, stats):
	"""Weld one submesh in place. Its arrays are per-submesh, so no vertex ranges."""
	normals = np.asarray(subMesh.normalList, np.float32)
	vertexCount = len(normals)
	if vertexCount == 0 or normals.shape != (vertexCount, 3):
		return
	positions = np.asarray(subMesh.vertexPosList, np.float32)
	uvs = np.asarray(subMesh.uvList, np.float32)
	tangents = np.asarray(subMesh.tangentList)
	faces = np.asarray(subMesh.faceList)
	if positions.shape != (vertexCount, 3) or uvs.shape != (vertexCount, 2):
		return
	if tangents.shape != (vertexCount, 4) or faces.size == 0:
		return
	triangles = faces.reshape(-1, 3).astype(np.int64)

	# Same position -> same quantised id. Keep the division in float32 like the
	# reference, so duplicate vertices land together with bit-identical rounding.
	quantised = np.round(positions / POS_QUANT).astype(np.int64)
	_, positionIds = np.unique(quantised, axis=0, return_inverse=True)
	positionIds = positionIds.reshape(-1)

	# Area-weighted face normal accumulated per position. `cross` is exactly
	# (unit face normal * triangle area), i.e. area weighting without the square.
	facePositions = positions[triangles]
	cross = np.cross(
		facePositions[:, 1] - facePositions[:, 0],
		facePositions[:, 2] - facePositions[:, 0]).astype(np.float64)
	cornerIds = positionIds[triangles.reshape(-1)]  # one position id per triangle corner
	order = np.argsort(cornerIds, kind="stable")  # stable: same accumulation order as per-face
	starts, _ = _runs(cornerIds[order])
	# One copy of the face's cross product per corner it touches that position with.
	crossPerCorner = np.repeat(cross, 3, axis=0)[order]
	accumulated = np.add.reduceat(crossPerCorner, starts, axis=0)
	accumulatedPositions = cornerIds[order][starts]
	accumulatedIndex = {key: index for index, key in enumerate(accumulatedPositions.tolist())}

	# Vertices sharing a position inside this submesh, in stable vertex order.
	groupOrder = np.argsort(positionIds, kind="stable")
	groupStarts, groupLengths = _runs(positionIds[groupOrder])
	vertexIds = np.arange(vertexCount)
	tangentInt8 = tangents.astype(np.int8)
	changed = False

	for group in np.flatnonzero(groupLengths >= 2):
		members = vertexIds[groupOrder[groupStarts[group]: groupStarts[group] + groupLengths[group]]]
		memberUVs = uvs[members]
		if _uvWrapDistance(memberUVs[:, None, :], memberUVs[None, :, :]).max() < UV_EPS:
			stats["same_uv_hard_edge"] += 1  # one UV on duplicates = real hard edge
			continue

		index = accumulatedIndex.get(int(positionIds[members[0]]))
		target = accumulated[index] if index is not None else np.zeros(3)
		length = np.linalg.norm(target)
		if length < 1e-12:  # degenerate triangles: fall back to the stored normals
			target = normals[members].sum(0).astype(np.float64)
			length = np.linalg.norm(target)
			if length < 1e-9:
				continue
		target = target / length

		memberNormals = normals[members].astype(np.float64)
		if _angles(memberNormals[:, None, :], memberNormals[None, :, :]).max() <= SPLIT_EPS:
			stats["already_shared"] += 1  # normals already shared: no split to weld
			continue

		offsets = _angles(normals[members], target)
		if offsets.max() > WELD_THRESH:
			stats["crease_kept"] += 1  # real crease, keep its hard edge
			continue

		target32 = target.astype(np.float32)
		targetBytes = _encodeNormalBytes(target32)
		if np.all(_encodeNormalBytes(normals[members]) == targetBytes[None, :]):
			stats["already_consistent"] += 1  # writing would not change a byte
			continue

		# Orthogonalise the tangent xyz against the new normal; `w` (handedness) is untouched.
		memberTangents = tangentInt8[members, :3].astype(np.float64) / NORMAL_SCALE
		projected = memberTangents - target[None, :] * (memberTangents @ target)[:, None]
		projectedLength = np.linalg.norm(projected, axis=1)
		ok = projectedLength > TANGENT_MIN
		newTangentBytes = tangents[members].copy()
		newTangentBytes[ok, :3] = (
			np.floor(projected[ok] / projectedLength[ok, None] * NORMAL_SCALE).astype(np.int32) & 0xFF
		).astype(np.uint8)

		normals[members] = target32
		tangents[members] = newTangentBytes
		changed = True
		stats["welded_groups"] += 1
		stats["welded_verts"] += len(members)
		stats["max_shift_deg"] = max(stats["max_shift_deg"], float(offsets.max()))

	if changed:
		# `asarray` may have copied a plain list; hand the written arrays back.
		subMesh.normalList = normals
		subMesh.tangentList = tangents


def weldSeamNormals(parsedMesh):
	"""Weld UV-seam normal splits on every main-mesh submesh of a parsed export.

	Groups whose normals are already identical are left alone (nothing to weld there).
	Returns the statistics dict printed by `printSeamWeldStats`. Submeshes sharing their
	buffers with an earlier submesh are skipped: the original is welded for both."""
	stats = dict(welded_groups=0, welded_verts=0, crease_kept=0, same_uv_hard_edge=0,
	             already_shared=0, already_consistent=0, max_shift_deg=0.0)
	for lod in getattr(parsedMesh, "mainMeshLODList", ()):
		for visconGroup in lod.visconGroupList:
			for subMesh in visconGroup.subMeshList:
				if not getattr(subMesh, "isReusedMesh", False):
					_weldSubMesh(subMesh, stats)
	return stats


def printSeamWeldStats(stats):
	parts = []
	for name, value in stats.items():
		parts.append(f"{name}={value:.1f}deg" if name == "max_shift_deg" else f"{name}={value}")
	print("  Seam normal weld: " + ", ".join(parts))
