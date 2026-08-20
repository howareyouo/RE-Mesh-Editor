#Author: NSA Cloud
import bpy
from math import radians
from mathutils import Matrix

from .gen_functions import textColors, splitNativesPath, raiseWarning
import os
from collections import OrderedDict
from itertools import repeat

# Shared rotation matrices (X-axis ±90°) for RE Engine import/export
rotate90Matrix = Matrix.Rotation(radians(90.0), 4, 'X')
rotateNeg90Matrix = Matrix.Rotation(radians(-90.0), 4, 'X')

def showMessageBox(message = "", title = "Message Box", icon = 'INFO'):

	def draw(self, context):
		self.layout.label(text = message)

	bpy.context.window_manager.popup_menu(draw, title = title, icon = icon)
	
def showErrorMessageBox(message):
	print(textColors.FAIL + "ERROR: " + message + textColors.ENDC)
	showMessageBox(message,title = "Error", icon = "ERROR")

def createRECollection(collectionName, parentCollection=None, color_tag=None, customProps=None):
	"""创建并链接一个 RE 标记集合 (MDF/SFur/Mesh 共用).
	返回新建的 collection; 自定义属性(如 ~TYPE)通过 customProps 传入."""
	collection = bpy.data.collections.new(collectionName)
	if color_tag:
		collection.color_tag = color_tag
	if customProps:
		for key, value in customProps.items():
			collection[key] = value
	if parentCollection is not None:
		parentCollection.children.link(collection)
	else:
		bpy.context.scene.collection.children.link(collection)
	return collection

def getBlenderSafeBoneName(boneName):
	"""Return a Blender-safe bone name, hashing if it exceeds the 63-char limit.
	
	Returns:
		tuple: (safeName, isHashed)
	"""
	if len(boneName) > 63:
		from ..hashing.mmh3.pymmh3 import hashUTF8
		safeName = f"#HASHED_{str(hashUTF8(boneName))}"
		raiseWarning(
			f"Bone name length exceeds Blender's limit of 63 characters, hashing bone name: {boneName}")
		return safeName, True
	return boneName, False

def setAssetPathFromFilePath(filePath, collection):
	"""Parse the natives asset path from filePath and store it on the collection.
	
	Sets collection["~ASSETPATH"] if the file is inside a natives folder.
	"""
	try:
		split = splitNativesPath(filePath)
		if split != None:
			assetPath = os.path.splitext(split[1])[0].replace(os.sep, "/")
			collection["~ASSETPATH"] = assetPath
	except:
		print("Failed to set asset path from file path, file is likely not in a natives folder.")

def createEmpty(name, propertyList, parent=None, collection=None):
	"""Create a Blender Empty object with display type PLAIN_AXES, custom
	properties, and link to a collection."""
	obj = bpy.data.objects.new(name, None)
	obj.empty_display_size = .10
	obj.empty_display_type = 'PLAIN_AXES'
	obj.parent = parent
	for property in propertyList:
		obj[property[0]] = property[1]
	if collection == None:
		collection = bpy.context.scene.collection
	collection.objects.link(obj)
	return obj

def printEditorHeader(bl_info):
	"""Print the RE Mesh Editor version header to console."""
	editorVersion = str(bl_info["version"][0])+"."+str(bl_info["version"][1])
	print(f"\n{textColors.BOLD}RE Mesh Editor V{editorVersion}{textColors.ENDC}")
	print(f"Blender Version {bpy.app.version[0]}.{bpy.app.version[1]}.{bpy.app.version[2]}")
	print("https://github.com/NSACloud/RE-Mesh-Editor")

def checkNameUsage(baseName, checkSubString=True, objList=None):
	if objList is None:
		objList = bpy.data.objects
	if checkSubString:
		return any(baseName in name for name in [obj.name for obj in objList])
	else:
		return baseName in [obj.name for obj in objList]

def tag_redraw(context, space_type="PROPERTIES", region_type="WINDOW"):
	for window in context.window_manager.windows:
		for area in window.screen.areas:
			if area.spaces[0].type == space_type:
				for region in area.regions:
					if region.type == region_type:
						region.tag_redraw()

def operator_exists(idname):
	from bpy.ops import op_as_string
	try:
		op_as_string(idname)
		return True
	except:
		return False
	
#--------------------------------
#Node arrange by JuhaW
#https://github.com/blender/blender-addons/blob/main/node_arrange.py
class values():
	average_y = 0
	x_last = 0
	margin_x = 100
	mat_name = ""
	margin_y = 20

def outputnode_search(ntree):
	outputnodes = [n for n in ntree.nodes if not n.outputs and any(i.is_linked for i in n.inputs)]
	if not outputnodes:
		print("No output node found")
		return None
	return outputnodes

def nodes_odd(ntree, nodelist):

	nodes = ntree.nodes
	for i in nodes:
		i.select = False

	a = [x for x in nodes if x not in nodelist]
	for i in a:
		i.select = True

def nodes_arrange(nodelist, level, ntree):

	parents = []
	for node in nodelist:
		parents.append(node.parent)
		node.parent = None
		ntree.nodes.update()


	widthmax = max([x.dimensions.x for x in nodelist])
	xpos = values.x_last - (widthmax + values.margin_x) if level != 0 else 0
	values.x_last = xpos

	# node y positions
	x = 0

	for node in ntree.nodes:

		if not node.parent:
			node.location.x -= center_x
			node.location.y += -center_y

def arrangeNodeTree(ntree,margin_x = 100,margin_y = 20,centerNodes = True):

#TODO Fix, blender doesn't initialize node dimensions unless the shader editor is open	

	values.margin_x = margin_x
	values.margin_y = margin_y

	ntree.nodes.update()
	#first arrange nodegroups
	n_groups = [n for n in ntree.nodes if n.type == 'GROUP']

	while n_groups:
		j = n_groups.pop(0)
		nodes_iterate(j.node_tree)
		for i in j.node_tree.nodes:
			if i.type == 'GROUP':
				n_groups.append(i)

	nodes_iterate(ntree)

	# arrange nodes + this center nodes together
	if centerNodes:
		nodes_center(ntree)
#--------------------------------