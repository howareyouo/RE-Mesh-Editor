#Author: NSA Cloud
#Shared shader-node construction helpers for the MDF material builders
#(blender_nodes_re_mdf.py and blender_re_mesh_mdf.py). They collapse the
#create-then-configure boilerplate (node type, operation, location, name) that
#repeats across the per-shader recipes; wiring that is specific to one recipe
#stays in that recipe.


def addSeparateColor(nodes, links, sourceSocket = None, location = None, name = None):
	"""Add a Separate Color node, optionally positioned, named and fed by sourceSocket.

	Returns the node so the caller can read its "Red"/"Green"/"Blue" outputs."""
	node = nodes.new("ShaderNodeSeparateColor")
	if location is not None:
		node.location = location
	if name is not None:
		node.name = name
	if sourceSocket is not None:
		links.new(sourceSocket, node.inputs["Color"])
	return node


def addMathNode(nodes, operation, location = None, name = None, label = None, clamp = False):
	"""Add a Math node with the given operation; returns the node.

	clamp enables use_clamp, which most RE recipes want on ADD/SUBTRACT chains."""
	node = nodes.new("ShaderNodeMath")
	node.operation = operation
	if clamp:
		node.use_clamp = True
	if location is not None:
		node.location = location
	if name is not None:
		node.name = name
	if label is not None:
		node.label = label
	return node
