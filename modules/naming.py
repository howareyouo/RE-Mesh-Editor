# RE Engine naming conventions: parsing and formatting of the structured object names
# used by mesh, MDF and SFur handling ("Group_N_Sub_N__material", "Material NN (name)")
# and material-name canonicalization. Split out of gen_functions.py, which keeps the
# binary IO and file/path utilities.

import re

# --- RE mesh object name parsing ---
# RE mesh objects follow the naming convention:
#   [LOD_N_]Group_{groupID}_Sub_{subIndex}__{materialName}
# These helpers extract the numeric IDs, returning 0 on failure so that
# malformed names degrade gracefully instead of crashing export/import.

def parseREMeshGroupID(name):
	"""Extract the Group_N number from an RE mesh object name.
	Returns 0 if the name doesn't contain a valid Group_ token."""
	try:
		return int(name.split("Group_")[1].split("_")[0])
	except (IndexError, ValueError):
		return 0


def stripBlenderUserSuffix(name):
	"""Remove Blender's auto-appended usersuffix ("Body.001" -> "Body").

	Only a trailing ".NNN" that Blender adds to duplicated data-blocks is removed, so
	legitimately dotted names like "Char.v2" or "Shirts_Mat.v001" are preserved. This is
	the same rule parseMDFObjectNameMaterialName applies to MDF material objects."""
	return re.sub(r"\.\d{3}$", "", name)


def parseREMeshObjectMaterialName(objectName):
	"""Extract the RE material name from the "__" suffix of a mesh object name.

	"LOD_0_Group_0_Sub_0__Shirts_Mat.001" -> "Shirts_Mat" (material names never
	contain "__"). Returns "" when the name has no "__" material suffix."""
	if "__" not in objectName:
		return ""
	return stripBlenderUserSuffix(objectName.split("__", 1)[1]).strip()


def resolveREMeshMaterialName(objectName, blenderMaterialName, useBlenderMaterialName = False, fallback = "NO_MATERIAL"):
	"""Resolve the RE material name from a mesh object name and a material-slot name.

	Single, pure source for the mesh exporter's name-priority rules, split out of
	getREMeshMaterialName so the exporter can resolve a name from an object name plus an
	EXTERNAL slot name (e.g. the depsgraph-evaluated clone), instead of always reading the
	original object's mesh. By default the "__" suffix of the object name wins, or the
	material slot when useBlenderMaterialName is set; each source falls back to the other
	when empty. Returns `fallback` when neither is available."""
	objectMaterialName = parseREMeshObjectMaterialName(objectName)
	if useBlenderMaterialName:
		materialName = blenderMaterialName or objectMaterialName
	else:
		materialName = objectMaterialName or blenderMaterialName
	return materialName if materialName != "" else fallback


def getREMeshMaterialName(obj, useBlenderMaterialName = False, fallback = "NO_MATERIAL"):
	"""Get the RE material name for a Blender mesh object (see resolveREMeshMaterialName)."""
	blenderMaterialName = ""
	if obj.data is not None and len(obj.data.materials) > 0:
		blenderMaterialName = stripBlenderUserSuffix(obj.data.materials[0].name).strip()
	return resolveREMeshMaterialName(obj.name, blenderMaterialName, useBlenderMaterialName, fallback)


def formatREMeshObjectName(groupID, subIndex, materialName, lodPrefix = ""):
	"""Build an RE mesh object name: "[LOD_N_]Group_<G>_Sub_<S>__<materialName>"."""
	return f"{lodPrefix}Group_{groupID}_Sub_{subIndex}__{materialName}"


def replaceREMeshMaterialSuffix(objectName, materialName):
	"""Swap the material name in an RE mesh object name.

	Only the part after the first "__" is replaced, so the "[LOD_N_]Group_<G>_Sub_<S>"
	prefix is kept byte-for-byte, including hand-edited numbering or LOD/shadow
	variants. Returns the name unchanged when it carries no material suffix."""
	if "__" not in objectName:
		return objectName
	return objectName.split("__", 1)[0] + "__" + materialName


def formatREMaterialObjectName(base, index, materialName):
	"""Build the indexed material entry object name used by MDF/SFur collections.

	Example: formatREMaterialObjectName("Material", 7, "Body") -> "Material 07 (Body)".
	The parenthesised part is parsed back by parseMDFObjectNameMaterialName."""
	return f"{base} {str(index).zfill(2)} ({materialName})"


def stripREMatSuffix(name):
	"""Remove a trailing "_Mat" material suffix (case-insensitive)."""
	return re.sub(r"_mat$", "", name, flags=re.IGNORECASE)


def parseMDFObjectNameMaterialName(objectName):
	"""Extract the material name from an RE indexed material object name.

	MDF material objects follow the naming convention "Material NN (name)"
	(SFur entries use "Shell Fur NN (name)"), where the material name is the
	text between the FIRST "(" and the LAST ")", so names that themselves
	contain parentheses (e.g. "material 00 (Body)") round-trip correctly.
	A trailing Blender usersuffix (.001) from duplicated objects is ignored.
	Returns None when the name doesn't follow the convention or the
	parenthesised part is empty."""
	match = re.match(r"^[^(]*\((.*)\)(?:\.\d{3})?$", objectName)
	if match == None or match.group(1) == "":
		return None
	return match.group(1)


def capitalizeREMaterialName(name):
	"""Capitalize the first letter of every word in a material name.

	Words are delimited by the start of the string and any non-letter
	character (e.g. "_" or a space); letters after those get uppercased
	while the rest of the name is left untouched, so camelCase and
	already-capitalized names pass through unchanged.
	"""
	return re.sub(r"(^|[^A-Za-z])([a-z])", lambda match: match.group(1) + match.group(2).upper(), name)


def normalizeREMaterialName(name, stripSuffix = True):
	"""Canonicalize a material name for RE mesh/MDF naming.

	Optionally strips a trailing "_Mat" suffix (case-insensitive) and then
	capitalizes the first letter of every word. The single source for the name
	rule shared by the Rename Meshes operator and reindexMaterials, so both
	buttons converge on the same name; idempotent, so already-normalized names
	pass through unchanged."""
	if stripSuffix:
		name = stripREMatSuffix(name)
	return capitalizeREMaterialName(name)
