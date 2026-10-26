#Author: NSA Cloud
#Addon preferences (REMeshPreferences), the chunk path list UI classes and the
#texture-cache utility operators, split out of __init__.py.

import bpy
import os
from datetime import datetime
from bpy.props import StringProperty, BoolProperty, IntProperty, EnumProperty, CollectionProperty
from bpy.types import Operator, AddonPreferences

from . import addon_updater_ops
from .modules.gen_functions import formatByteSize, getFolderSize, openFolder

# The addon's registered module name; equals __init__.py's __name__.
ADDON_NAME = __package__


class ChunkPathPropertyGroup(bpy.types.PropertyGroup):
    gameName: EnumProperty(
		name="",
		description="Set the game",
		items= [ 
		("DMC5", "Devil May Cry 5", ""),
		("RE2", "Resident Evil 2", ""),
		("RE3", "Resident Evil 3", ""),
		("RE8", "Resident Evil 8", ""),	
		("RE2RT", "Resident Evil 2 Ray Tracing", ""),
		("RE3RT", "Resident Evil 3 Ray Tracing", ""),
		("RE7RT", "Resident Evil 7 Ray Tracing", ""),
		("MHRSB", "Monster Hunter Rise", ""),
		("SF6", "Street Fighter 6", ""),
		("RE4", "Resident Evil 4", ""),
		("DD2", "Dragon's Dogma 2", ""),
		("KG", "Kunitsu-Gami", ""),
		("DR", "Dead Rising", ""),
		("ONI2", "Onimusha 2", ""),
		("MHWILDS", "Monster Hunter Wilds", ""),
		("PRAG", "Pragmata", ""),
		("MHS3", "Monster Hunter Stories 3", ""),
		("RE9", "Resident Evil 9", ""),
		]
    )
    path: StringProperty(
        name="Path",
		subtype="DIR_PATH",
		description = "Set the path to the natives\\STM folder inside the extracted chunk files\nThis determines where textures will be imported from\n"+r"Example: I:\RE4_EXTRACT\re_chunk_000\natives\STM"
    )


class AddItemOperator(bpy.types.Operator):
	bl_idname = "re_mesh.chunk_path_list_add_item"
	bl_description = "Add path to the extracted chunk's natives\\STM\\ folder.\n"+r"Example: I:\RE4_EXTRACT\re_chunk_000\natives\STM"
	bl_label = "Add Path"

	def execute(self, context):
		# Add an item to the list
		context.preferences.addons[ADDON_NAME].preferences.chunkPathList_items.add()
		return {'FINISHED'}

# Operator to remove an item from the list
class RemoveItemOperator(bpy.types.Operator):
	bl_idname = "re_mesh.chunk_path_list_remove_item"
	bl_description = "Remove chunk path from the list"
	bl_label = "Remove Selected Path"

	def execute(self, context):
		chunkList = bpy.context.preferences.addons[ADDON_NAME].preferences.chunkPathList_items
		index = bpy.context.preferences.addons[ADDON_NAME].preferences.chunkPathList_index
		chunkList.remove(index)
		bpy.context.preferences.addons[ADDON_NAME].preferences.chunkPathList_index = min(max(0, index - 1), len(chunkList) - 1)
		return {'FINISHED'}

# Operator to reorder items in the list
class ReorderItemOperator(bpy.types.Operator):
	bl_idname = "re_mesh.chunk_path_list_reorder_item"
	bl_description = "Change the order in which files will be searched"
	bl_label = "Reorder Item"

	direction: bpy.props.EnumProperty(
		items=[('UP', "Up", ""), ('DOWN', "Down", "")],
		default='UP'
	)

	def move_index(self):
		index = bpy.context.preferences.addons[ADDON_NAME].preferences.chunkPathList_index
		list_length = len(bpy.context.preferences.addons[ADDON_NAME].preferences.chunkPathList_items)
		new_index = index + (-1 if self.direction == 'UP' else 1)
		bpy.context.preferences.addons[ADDON_NAME].preferences.chunkPathList_index = max(0,min(new_index,list_length))
		
	def execute(self, context):
		chunkList = bpy.context.preferences.addons[ADDON_NAME].preferences.chunkPathList_items
		index = bpy.context.preferences.addons[ADDON_NAME].preferences.chunkPathList_index
		
		neighbor = index + (-1 if self.direction == 'UP' else 1)
		chunkList.move(neighbor, index)
		self.move_index()
		return {'FINISHED'}

class MESH_UL_ChunkPathList(bpy.types.UIList):
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
		layout.prop(item,"gameName")
		layout.prop(item,"path")

def checkTextureCacheSize():
	try:
		bpy.context.preferences.addons[ADDON_NAME].preferences.textureCacheSizeString = formatByteSize(getFolderSize(bpy.path.abspath(bpy.context.preferences.addons[ADDON_NAME].preferences.textureCachePath)))
		timestamp = str(datetime.now()).split(".")[0]
		bpy.context.preferences.addons[ADDON_NAME].preferences.textureCacheCheckDate = timestamp
	except:
		pass
	
class REMeshPreferences(AddonPreferences):
	bl_idname = ADDON_NAME
	
	dragDropImportOptions: BoolProperty(
		name="Show Drag and Drop Import Options (Blender 4.1 or higher)",
		description = "Show import options when dragging a mesh or mdf file into the 3D View.\nIf this is disabled, the default import options will be used.\nDrag and drop importing is only supported on Blender 4.1 or higher",
		default = False if bpy.app.version < (4,1,0) else True
	)
	showConsole: BoolProperty(
		name="Show Console During Import/Export",
		description = "When importing or exporting a file, the console will be opened so that progress can be viewed.\nNote that if the console is already opened before import or export, it will be closed instead.\n This is a limitation of Blender, there's no way to get the active state of the console window.",
		default = True
	)
	textureCachePath: StringProperty(
		name="Texture Cache Path",
		subtype='DIR_PATH',
		description = "Location to save converted textures",
		default = os.path.join(os.path.dirname(os.path.realpath(__file__)),"TextureCache")
	)
	useDDS: BoolProperty(
		name="Use DDS Textures (Blender 4.2 or higher)",
		description = "Use DDS textures when loading textures instead of converting to PNG.\nThis greatly improves mesh import speed but is only usable on Blender 4.2 or higher.\nIf the Blender version is less than 4.2, this option will do nothing",
		default = False if bpy.app.version < (4,2,0) else True
	)
	convertTexToPNG: BoolProperty(
		name="Convert Tex to PNG",
		description = "When converting a tex file, convert it to a .png file instead of .dds",
		default = False
	)
	modFileTrackingPollingRate: IntProperty(
		name = "Mod File Tracking Polling Rate",
		description = "Amount of seconds between checking for changes made to files when mod file tracking is enabled",
		min = 1,
		default = 5
	)
	saveChunkPaths: BoolProperty(
		name="Save Chunk Paths Automatically",
		description = "If a chunk path is detected when a mesh is imported, add it to the chunk path list automatically",
		default = True
	)
	autoSyncMaterialNames: BoolProperty(
		name="Auto Sync Material Names",
		description = "Keep material names in sync between the meshes and the MDF when a mesh is renamed in the outliner.\nRenaming a mesh updates the MDF material of the same name and every other mesh piece that shares it (RE engine models are split into several meshes per material).\nRenaming a material in the MDF panel always renames the meshes, this option only controls the mesh to MDF direction",
		default = True
	)
	autoRenameTextureFiles: BoolProperty(
		name="Auto Rename Texture Files",
		description = "Rename the .tex files on disk when the path of a texture binding is changed in the MDF panel, so the binding keeps pointing at a file that exists.\nOnly a name change inside the same folder renames a file, and nothing is renamed when the new name is already taken.\nThe files are renamed outside of Blender, so this is not undone by Blender's undo",
		default = True
	)
	chunkPathList_items: CollectionProperty(type=ChunkPathPropertyGroup)
	chunkPathList_index: IntProperty(name="")
	# addon updater preferences
	auto_check_update: bpy.props.BoolProperty(
	    name = "Auto-check for Update",
	    description = "If enabled, auto-check for updates using an interval",
	    default = False,
	)
	updater_interval_months: bpy.props.IntProperty(
	    name='Months',
	    description = "Number of months between checking for updates",
	    default=0,
	    min=0
	)
	updater_interval_days: bpy.props.IntProperty(
	    name='Days',
	    description = "Number of days between checking for updates",
	    default=7,
	    min=0,
	)
	updater_interval_hours: bpy.props.IntProperty(
	    name='Hours',
	    description = "Number of hours between checking for updates",
	    default=0,
	    min=0,
	    max=23
	)
	updater_interval_minutes: bpy.props.IntProperty(
	    name='Minutes',
	    description = "Number of minutes between checking for updates",
	    default=0,
	    min=0,
	    max=59
	)
	textureCacheSizeString: bpy.props.StringProperty(
	    default="",
	)
	textureCacheCheckDate: bpy.props.StringProperty(
	    default="",
	)


	def draw(self, context):
		layout = self.layout
		split = layout.split(factor = .3)
		col1 = split.column()
		col2 = split.column()
		col3 = split.column()
		op = col2.operator(
			'wm.url_open',
			text='Donate on Ko-fi',
			icon='FUND'
        )
		layout.prop(self, "dragDropImportOptions")
		layout.prop(self, "showConsole")
		layout.prop(self, "useDDS")
		layout.prop(self, "convertTexToPNG")
		layout.prop(self, "modFileTrackingPollingRate")
		layout.prop(self, "autoSyncMaterialNames")
		layout.prop(self, "autoRenameTextureFiles")
		
		box = layout.box()
		row = box.row()
		
		if len(self.textureCachePath) > 110:
			row.alert = True
			row.prop(self,"textureCachePath")
			box.label(text="Texture cache path is very long.",icon = "ERROR")
			box.label(text="File paths may exceed the max length of 255 characters and fail to convert.")
			box.label(text="Consider changing this to a shorter path such as C:\\REMeshTextureCache.")
		else:
			row.prop(self,"textureCachePath")
		row = box.row()
		if self.textureCacheCheckDate == "":
			checkTextureCacheSize()
		row.label(text=f"Cache Size: {self.textureCacheSizeString}")
		row.operator("re_mesh.check_texture_cache_size",icon = "FILE_REFRESH",text = "")
		box.label(text=f"Last Checked: {self.textureCacheCheckDate}")
		box.operator("re_mesh.open_texture_cache_folder")
		box.operator("re_mesh.clear_texture_cache_folder")
		
		op.url = 'https://ko-fi.com/nsacloud'
		
		layout.label(text = "Chunk Path List")
		layout.prop(self, "saveChunkPaths")
		layout.template_list("MESH_UL_ChunkPathList", "", self, "chunkPathList_items", self, "chunkPathList_index",rows = 3)
		row = layout.row(align=True)
		row.operator("re_mesh.chunk_path_list_add_item")
		row.operator("re_mesh.chunk_path_list_remove_item")

        # Reorder buttons
		row = layout.row(align=True)
		row.operator("re_mesh.chunk_path_list_reorder_item", text="Move Up").direction = 'UP'
		row.operator("re_mesh.chunk_path_list_reorder_item", text="Move Down").direction = 'DOWN'
		addon_updater_ops.update_settings_ui(self,context)


class WM_OT_OpenTextureCacheFolder(Operator):
	bl_label = "Open Texture Cache Folder"
	bl_description = "Opens the texture cache folder in File Explorer"
	bl_idname = "re_mesh.open_texture_cache_folder"

	def execute(self, context):
		try:
			openFolder(bpy.path.abspath(bpy.context.preferences.addons[ADDON_NAME].preferences.textureCachePath))
		except:
			pass
		checkTextureCacheSize()
		return {'FINISHED'}

class WM_OT_CheckTextureCacheSize(Operator):
	bl_label = "Check Cache Size"
	bl_description = "Shows the current size of the texture cache folder."
	bl_idname = "re_mesh.check_texture_cache_size"

	def execute(self, context):
		checkTextureCacheSize()
		return {'FINISHED'} 

class WM_OT_ClearTextureCacheFolder(Operator):
	bl_label = "Clear Texture Cache Folder"
	bl_description = "Deletes all cached converted textures. Note that any saved blend files will lose their textures if the cache is cleared."
	bl_idname = "re_mesh.clear_texture_cache_folder"
	def draw(self,context):
		layout = self.layout
		layout.label(text="Are you sure you want to delete all cached textures?")
		layout.label(text=f"Directory:")
		layout.label(text=f"{bpy.path.abspath(bpy.context.preferences.addons[ADDON_NAME].preferences.textureCachePath)}")
	def invoke(self,context,event):
		checkTextureCacheSize()
		return context.window_manager.invoke_props_dialog(self,width = 400)
	def execute(self, context):
		imageExtensions = (".dds",".png",".tga",".tif",".tiff") 
		deletionList = []
		#I could do shutil.rmtree but deleting everything from a directory could be potentially dangerous if the path is somehow wrong.
		#So in order to minimize potential damage, it loops through and only deletes image files in the directory.
		for root, dirs, files in os.walk(bpy.path.abspath(bpy.context.preferences.addons[ADDON_NAME].preferences.textureCachePath)):
			for file in files:
				if file.lower().endswith(imageExtensions):
					deletionList.append(os.path.join(root,file))
		
		print(f"Deleting {len(deletionList)} image files...")
		for path in deletionList:
			try:
				os.remove(path)
			except Exception as err:
				print(f"Failed to delete {path} - {str(err)}")
		self.report({"INFO"},"Cleared texture cache.")
		checkTextureCacheSize()
		return {'FINISHED'}


# Registration
classes = (
	ChunkPathPropertyGroup,
	AddItemOperator,
	RemoveItemOperator,
	ReorderItemOperator,
	MESH_UL_ChunkPathList,
	WM_OT_OpenTextureCacheFolder,
	WM_OT_CheckTextureCacheSize,
	WM_OT_ClearTextureCacheFolder,
	REMeshPreferences,
)
