# SF6 JCNS pose-corrective shape key drivers.
# Ported from REME v0.73 (blender_re_mesh.py). The implementation is complete
# but stays dormant: nothing calls installSF6JCNSDrivers() until the generated
# drivers are verified against real in-game pose data. The import path only
# parses the JCNS file for diagnostics and keeps it in sf6JCNSData.

import bpy
from math import radians

# create compact numeric literals for blender driver expression
def _formatSF6DriverValue(value):
	return f"{value:.9g}"

def evaluateSF6JCNSCurve(value, x0, x1, x2, y0, y1, y2):
	"""
	Evaluate one clamped three point JCNS angle curve
	"""
	if value <= x0:
		return y0
	if value >= x2:
		return y2

	# duplicate lower knots can't reach this branch because value > x0
	if value < x1:
		return y0 + (y1 - y0) * (value - x0) / (x1 - x0)

	# duplicate upper knots can't reach this branch because value < x2
	return y1 + (y2 - y1) * (value - x1) / (x2 - x1)

# Keep generated driver expressions short enough for Blender's 255 character limit
bpy.app.driver_namespace["re_sf6c"] = evaluateSF6JCNSCurve

def buildSF6JCNSDriverExpression(recordList):
	"""
	Convert the JCNS records for one shape key into a Blender driver.

	Each supported condition becomes one driver variable named v0, v1, etc.
	Conditions within one record are added together.
	When one JCNS output is represented by multiple records then those record results are multiplied.

	Returns:
		expression: Blender scripted driver expression
		conditionList: Conditions in the same order as the driver variables
	"""
	conditionList = []
	recordExpressionList = []

	for record in recordList:
		# Holds every one-dimensional bone angle curve in this record
		conditionExpressionList = []

		for condition in record.conditionList:
			# Mode 1 and 3 use ordinary local Euler rotation channels.
			# Mode 0 is only used by PointCons entries and needs separate transform space handling,
			# skipped for now
			if condition.curveModeRaw not in (1, 3):
				continue

			# driver installer creates transform variables with this name
			# and target the condition's bone and rotation axis
			variableName = f"v{len(conditionList)}"
			conditionList.append(condition)

			# JCNS stores three angle-to-weight control points.
			# Their stored order is not always numerically ascending, sort the complete pairs,
			# without separating an angle from its corresponding weight
			points = sorted(zip(condition.inputValues, condition.outputValues), key=lambda point: point[0])
			(x0, y0), (x1, y1), (x2, y2) = points

			# JCNS angles are degrees, Blender transform-driver rotation variable returns radians
			# Convert authored control points to radians
			x0 = radians(x0)
			x1 = radians(x1)
			x2 = radians(x2)

			# Store curve parameters directly in compact function call
			# Even the largest five condition SF6 driver remains below Blender's 255 character expression limit
			curveValues = (x0, x1, x2, y0, y1, y2)
			expression = (
				f"re_sf6c({variableName}," + ",".join(_formatSF6DriverValue(value) for value in curveValues) + ")"
			)

			conditionExpressionList.append(expression)

		# Conditions inside a record contribute additively.
		# Parentheses preserve that sum when multiple records are later multiplied
		if conditionExpressionList:
			recordExpressionList.append("(" + "+".join(conditionExpressionList) + ")")

	# Repeated records describe combined bone requirements for the same output.
	# The returned condition order must match v0, v1, ... during installation
	return "*".join(recordExpressionList), conditionList

def installSF6JCNSDrivers(jcnsData, meshObjectList, armatureObj):
	"""
	Install JCNS pose drivers on matching raw SF6 shape key names

	Drivers read local XYZ pose bone rotations.
	Their variables are ordered to match the conditions returned by buildSF6JCNSDriverExpression()
	"""
	if jcnsData is None or armatureObj is None:
		return 0

	# Ensure custom curve evaluator is available after a addon reload
	bpy.app.driver_namespace["re_sf6c"] = evaluateSF6JCNSCurve

	# 1 output can have multiple records representing combined bone inputs
	recordDict = {}
	for record in jcnsData.recordList:
		recordDict.setdefault(record.outputName, []).append(record)

	transformTypeList = ("ROT_X", "ROT_Y", "ROT_Z")
	installedDriverCount = 0
	driverMeshCount = 0
	unsupportedOutputCount = 0
	oversizedExpressionCount = 0
	missingBoneNameSet = set()

	for meshObject in meshObjectList:
		if meshObject.type != "MESH":
			continue

		shapeKeyData = meshObject.data.shape_keys
		if shapeKeyData is None:
			continue

		meshHasDriver = False

		for outputName, recordList in recordDict.items():
			shapeKey = shapeKeyData.key_blocks.get(outputName)
			if shapeKey is None:
				continue

			expression, conditionList = buildSF6JCNSDriverExpression(recordList)

			# empty expressions are 0 condition records or unsupported mode 0 PointCons conditions
			if not expression or not conditionList:
				unsupportedOutputCount += 1
				continue

			# blender silently truncates expressions longer than 255 characters
			if len(expression) > 255:
				oversizedExpressionCount += 1
				continue

			missingBones = {
				condition.boneName
				for condition in conditionList
				if armatureObj.pose.bones.get(condition.boneName) is None
			}
			if missingBones:
				missingBoneNameSet.update(missingBones)
				continue

			fcurve = shapeKey.driver_add("value")
			driver = fcurve.driver
			driver.type = "SCRIPTED"

			# remove variables left by existing driver on this shape key
			for variable in list(driver.variables):
				driver.variables.remove(variable)

			for conditionIndex, condition in enumerate(conditionList):
				variable = driver.variables.new()
				variable.name = f"v{conditionIndex}"
				variable.type = "TRANSFORMS"

				target = variable.targets[0]
				target.id = armatureObj
				target.bone_target = condition.boneName
				target.transform_type = transformTypeList[condition.axis]
				target.transform_space = "LOCAL_SPACE"
				target.rotation_mode = "XYZ"

			driver.expression = expression
			installedDriverCount += 1
			meshHasDriver = True

		if meshHasDriver:
			driverMeshCount += 1

			# these markers will let the exporter later temporarily disable generated drivers instead of baking posed corrections
			shapeKeyData["re_sf6_jcns_drivers"] = True
			shapeKeyData["re_sf6_jcns_path"] = jcnsData.filePath

	print(f"[SF6 JCNS] Installed {installedDriverCount} pose drivers across {driverMeshCount} mesh objects")

	if unsupportedOutputCount:
		print(f"[SF6 JCNS] Skipped {unsupportedOutputCount} unsupported or 0 condition outputs")

	if oversizedExpressionCount:
		print(f"[SF6 JCNS] Skipped {oversizedExpressionCount} expressions exceeding Blender's 255 character limit")

	if missingBoneNameSet:
		print("[SF6 JCNS] Missing driver bones: " + ",".join(sorted(missingBoneNameSet)))

	return installedDriverCount
