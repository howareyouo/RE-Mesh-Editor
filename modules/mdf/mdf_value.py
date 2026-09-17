#Shared MDF property value helpers.
#Single home for the FLOAT/BOOL/VEC4/COLOR classification heuristics and the
#value conversions between the MDF property value list and Blender UI items.
#Used by import/export (blender_re_mdf), live edit (re_mdf_propertyGroups),
#presets (re_mdf_presets) and node building (blender_nodes_re_mdf).


#Manually defined property type overrides (historical config)
boolPropertySet = set(["BackFaceNormalFilp", "uv1or2_AlphaMap"])
colorPropertySet = set([])


def is_color_prop(propName, paramCount):
	"""Heuristic: does this property look like an RGBA color?

	Shared by the importer (blender_re_mdf) and node building
	(blender_nodes_re_mdf) - both exclude names containing 'rate'.
	"""
	if propName in colorPropertySet:
		return True
	lowerPropName = propName.lower()
	return paramCount == 4 and ("color" in lowerPropName or "_col_" in lowerPropName) and "rate" not in lowerPropName


def is_bool_prop(propName, paramCount):
	if propName in boolPropertySet:
		return True
	return paramCount == 1 and ("Use" in propName or "_or_" in propName or propName.startswith("is"))


def classify_prop(propName, paramCount):
	"""Return the UI data_type ("COLOR"/"BOOL"/"VEC4"/"FLOAT") for a property.

	Order matches the legacy importer: color wins over bool, paramCount>1 -> VEC4.
	"""
	if is_color_prop(propName, paramCount):
		return "COLOR"
	if is_bool_prop(propName, paramCount):
		return "BOOL"
	if paramCount > 1:
		return "VEC4"
	return "FLOAT"


def getPropValue(propertyEntry):
	"""Convert a UI geometry item to a value list (export getPropValue semantics).

	Always returns a list so callers can len() it / assign to prop.propValue.
	"""
	dataType = propertyEntry.data_type
	if dataType == "VEC4":
		return list(propertyEntry.float_vector_value)
	elif dataType == "COLOR":
		return list(propertyEntry.color_value)
	elif dataType == "BOOL":
		return [1.0] if propertyEntry.bool_value else [0.0]
	else:  # FLOAT
		return [propertyEntry.float_value]


def getPropValueJSON(propertyEntry):
	"""JSON-friendly value for presets (VEC4/COLOR as lists, BOOL/FLOAT scalar)."""
	dataType = propertyEntry.data_type
	if dataType in ("VEC4", "COLOR"):
		return getPropValue(propertyEntry)
	return getPropValue(propertyEntry)[0]


def setPropValue(propertyEntry, dataType, value):
	"""Assign a value (list or scalar) to a UI geometry item.

	Imported/preset values are lists; the same helpers feed both paths.
	"""
	if dataType == "VEC4":
		propertyEntry.float_vector_value = value
	elif dataType == "COLOR":
		propertyEntry.color_value = value
	elif dataType == "BOOL":
		v = value[0] if isinstance(value, (list, tuple)) else value
		propertyEntry.bool_value = bool(v)
	else:  # FLOAT
		v = value[0] if isinstance(value, (list, tuple)) else value
		propertyEntry.float_value = float(v)