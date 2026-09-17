# Author: NSA Cloud
# TODO
# Add Blendshapes
# Fix exporting SF6 Akuma with LODs, LODs use bones not used by LOD0

# Import
# -Redo vertex color based material importing

# Export
# -Submeshes aren't sorted

# This module is the public entry point for the mesh import/export pipeline.
# The implementation lives in three sibling modules:
#   blender_re_mesh_utils  - shared helpers (mesh utils, phase timing, config)
#   blender_re_mesh_import - importREMeshFile + all import-side helpers
#   blender_re_mesh_export - exportREMeshFile + all export-side helpers
from .blender_re_mesh_import import importREMeshFile
from .blender_re_mesh_export import exportREMeshFile, solveRepeatedUVs
from .blender_re_mesh_utils import joinObjects, getCollection

__all__ = ["importREMeshFile", "exportREMeshFile", "solveRepeatedUVs", "joinObjects", "getCollection"]