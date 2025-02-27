bl_info = {
	"name": "FBX Export Tools",
	"author": "GoncaloRod",
	"version": (0, 2, 1),
	"blender": (2, 80, 0),
	"location": "File > Export > FBX Export Tools",
	"description": "Set of tools to automate some FBX export procedures.",
	"category": "Import-Export"
}

import bpy
import mathutils
import math
import os


# Multi-user datablocks are preserved here. Unique copies are made for applying the rotation.
# Eventually multi-user datablocks become single-user and gets processed.
# Therefore restoring the multi-user data assigns a shared but already processed datablock.
shared_data = dict()

# All objects and collections in this view layer must be visible while being processed.
# apply_rotation and matrix changes don't have effect otherwise.
# Visibility will be restored right before saving the FBX.
hidden_collections = []
hidden_objects = []
disabled_collections = []
disabled_objects = []


def unhide_collections(col):
	global hidden_collections
	global disabled_collections

	# No need to unhide excluded collections. Their objects aren't included in current view layer.
	if col.exclude:
		return

	# Find hidden child collections and unhide them
	hidden = [item for item in col.children if not item.exclude and item.hide_viewport]
	for item in hidden:
		item.hide_viewport = False

	# Add them to the list so they could be restored later
	hidden_collections.extend(hidden)

	# Same with the disabled collections
	disabled = [item for item in col.children if not item.exclude and item.collection.hide_viewport]
	for item in disabled:
		item.collection.hide_viewport = False

	disabled_collections.extend(disabled)

	# Recursively unhide child collections
	for item in col.children:
		unhide_collections(item)


def unhide_objects():
	global hidden_objects
	global disabled_objects

	view_layer_objects = [ob for ob in bpy.data.objects if ob.name in bpy.context.view_layer.objects]

	for ob in view_layer_objects:
		if ob.hide_get():
			hidden_objects.append(ob)
			ob.hide_set(False)
		if ob.hide_viewport:
			disabled_objects.append(ob)
			ob.hide_viewport = False


def make_single_user_data():
	global shared_data

	for ob in bpy.data.objects:
		if ob.data and ob.data.users > 1:
			if ob.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
				# Figure out the objects that use this datablock
				users = [user for user in bpy.data.objects if user.data == ob.data]

				# Shared data will be restored if users have no active modifiers
				modifiers = 0
				for user in users:
					modifiers += len([mod for mod in user.modifiers if mod.show_viewport])
				if modifiers == 0:
					shared_data[ob.name] = ob.data

			# Make single-user copy
			ob.data = ob.data.copy()


def apply_object_modifiers():
	# Select objects in current view layer not using an armature modifier
	bpy.ops.object.select_all(action='DESELECT')
	for ob in bpy.data.objects:
		if ob.name in bpy.context.view_layer.objects:
			bypass_modifiers = False
			for mod in ob.modifiers:
				if mod.type == 'ARMATURE':
					bypass_modifiers = True
			if not bypass_modifiers:
				ob.select_set(True)

	# Conversion to mesh may not be available depending on the remaining objects
	if bpy.ops.object.convert.poll():
		bpy.ops.object.convert(target='MESH')


def reset_parent_inverse(ob):
	if (ob.parent):
		mat_world = ob.matrix_world.copy()
		ob.matrix_parent_inverse.identity()
		ob.matrix_basis = ob.parent.matrix_world.inverted() @ mat_world


def apply_transforms(ob):
	bpy.ops.object.select_all(action='DESELECT')
	ob.select_set(True)
	bpy.ops.object.transform_apply(location = False, rotation = True, scale = True)


def apply_rotation(ob):
	bpy.ops.object.select_all(action='DESELECT')
	ob.select_set(True)
	bpy.ops.object.transform_apply(location = False, rotation = True, scale = False)


def fix_object(ob, fix_rotation, move_to_center, depth = 0):
	# Only fix objects in current view layer
	if ob.name in bpy.context.view_layer.objects:
		if fix_rotation:
			apply_transforms(ob)
			
			# Reset parent's inverse so we can work with local transform directly
			reset_parent_inverse(ob)

			# Create a copy of the local matrix and set a pure X-90 matrix
			mat_original = ob.matrix_local.copy()
			ob.matrix_local = mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'X')

			# Apply the rotation to the object
			apply_rotation(ob)

			# Reapply the previous local transform with an X+90 rotation
			ob.matrix_local = mat_original @ mathutils.Matrix.Rotation(math.radians(90.0), 4, 'X')

		if move_to_center and depth == 0:
			# Move object to position (0, 0, 0)
			ob.matrix_local = mathutils.Matrix.Translation(mathutils.Vector((0, 0, 0)))

	# Recursively fix child objects in current view layer.
	# Children may be in the current view layer even if their parent isn't.
	for child in ob.children:
		fix_object(child, depth = depth + 1)


def export(context, filepath, active_collection, selected_objects, move_to_center, export_individual_file, fix_unity_rotation):
	global shared_data
	global hidden_collections
	global hidden_objects
	global disabled_collections
	global disabled_objects

	# Root objects: Empty, Mesh or Armature without parent
	root_objects = [item for item in bpy.data.objects if (item.type == "EMPTY" or item.type == "MESH" or item.type == "ARMATURE" or item.type == "OTHER") and not item.parent]

	# Preserve current scene
	# undo_push examples, including exporters' execute:
	# https://programtalk.com/python-examples/bpy.ops.ed.undo_push  (Examples 4, 5 and 6)
	# https://sourcecodequery.com/example-method/bpy.ops.ed.undo  (Examples 1 and 2)

	bpy.ops.ed.undo_push(message="Prepare Unity FBX")

	shared_data = dict()
	hidden_collections = []
	hidden_objects = []
	disabled_collections = []
	disabled_objects = []

	selection = bpy.context.selected_objects

	# Object mode
	bpy.ops.object.mode_set(mode="OBJECT")

	# Ensure all the collections and objects in this view layer are visible
	unhide_collections(bpy.context.view_layer.layer_collection)
	unhide_objects()

	# Create a single copy in multi-user datablocks. Will be restored after fixing rotations.
	make_single_user_data()

	# Apply modifiers to objects (except those affected by an armature)
	apply_object_modifiers()

	try:
		# Fix objects
		for ob in root_objects:
			print(ob.name)
			fix_object(ob, fix_unity_rotation, move_to_center)

		# Restore multi-user meshes
		for item in shared_data:
			bpy.data.objects[item].data = shared_data[item]

		# Recompute the transforms out of the changed matrices
		bpy.context.view_layer.update()

		# Restore hidden and disabled objects
		for ob in hidden_objects:
			ob.hide_set(True)
		for ob in disabled_objects:
			ob.hide_viewport = True

		# Restore hidden and disabled collections
		for col in hidden_collections:
			col.hide_viewport = True
		for col in disabled_collections:
			col.collection.hide_viewport = True

		# Export FBX file
		if export_individual_file:

			dir, filename = os.path.split(filepath)

			for ob in selection:
				bpy.ops.object.select_all(action='DESELECT')
				ob.select_set(True)

				bpy.ops.export_scene.fbx(
					filepath = dir + "/" + ob.name + ".fbx",
					use_selection = True,
					use_active_collection = active_collection,
					apply_scale_options = "FBX_SCALE_UNITS",
					object_types = { 'EMPTY', 'ARMATURE', 'MESH', 'OTHER' },
					use_mesh_modifiers = True,
					add_leaf_bones = False,
					use_armature_deform_only = True,
					mesh_smooth_type = "EDGE"
				)
		else:

			# Restore selection
			bpy.ops.object.select_all(action='DESELECT')
			for ob in selection:
				ob.select_set(True)

			bpy.ops.export_scene.fbx(
				filepath = filepath,
				use_selection = True,
				use_active_collection = active_collection,
				apply_scale_options = "FBX_SCALE_UNITS",
				object_types = { 'EMPTY', 'ARMATURE', 'MESH', 'OTHER' },
				use_mesh_modifiers = True,
				add_leaf_bones = False,
				use_armature_deform_only = True,
				mesh_smooth_type = "EDGE"
			)

	except Exception as e:
		print(e)
		print("File not saved.")
	else:
		print("FBX file for Unity saved.")

	# Restore scene and finish
	bpy.ops.ed.undo_push(message="")
	bpy.ops.ed.undo()
	bpy.ops.ed.undo_push(message="Export Unity FBX")
	
	return {'FINISHED'}


#---------------------------------------------------------------------------------------------------
# Exporter stuff (from the Operator File Export template)

# ExportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator


class FBXExportTools(Operator, ExportHelper):
	"""Set of tools to automate some FBX export procedures."""
	bl_idname 	= "export_scene.fbx_export_tools"
	bl_label 	= "Export"
	bl_options 	= {'UNDO_GROUPED'}

	# ExportHelper mixin class uses this
	filename_ext = ".fbx"

	filter_glob: StringProperty(
		default = "*.fbx",
		options = {'HIDDEN'},
		maxlen 	= 255
	)

	# List of operator properties, the attributes will be assigned
	# to the class instance from the operator settings before calling.

	active_collection: BoolProperty(
		name 		= "Active Collection Only",
		description = "Export objects in the active collection only (and its children). May be combined with Selected Objects Only.",
		default 	= False,
	)

	selected_objects: BoolProperty(
		name 		= "Selected Objects Only",
		description = "Export selected objects only. May be combined with Active Collection Only.",
		default 	= True,
	)
	
	move_to_center: BoolProperty(
		name 		= "Move to center",
		description = "Move object to position (0, 0, 0) when exporting",
		default 	= False,
	)

	export_individual_file: BoolProperty(
		name 		= "Export to individual files",
		description = "Export each object and its children to his individual file.\nFile user input name will be ignored and object name will be used. \"Selected Objects Only\" option is on by default.",
		default 	= False,
	)

	fix_unity_rotation : BoolProperty(
		name 		= "Fix rotation for Unity",
		description = "Fix object rotation for Unity's coordinate system.",
		default 	= False
	)

	# Custom draw method
	# https://blender.stackexchange.com/questions/55437/add-gui-elements-to-exporter-window
	# https://docs.blender.org/api/current/bpy.types.UILayout.html

	def draw(self, context):
		layout = self.layout

		row = layout.row()
		row.prop(self, "active_collection")

		row = layout.row()
		row.prop(self, "selected_objects")

		row = layout.row()
		row.prop(self, "move_to_center")

		row = layout.row()
		row.prop(self, "export_individual_file")

		row = layout.row()
		row.prop(self, "fix_unity_rotation")

	def execute(self, context):
		return export(context, self.filepath, self.active_collection, self.selected_objects, self.move_to_center, self.export_individual_file, self.fix_unity_rotation)


# Only needed if you want to add into a dynamic menu
def menu_func_export(self, context):
	self.layout.operator(FBXExportTools.bl_idname, text="FBX Export Tools (.fbx)")


def register():
	bpy.utils.register_class(FBXExportTools)
	bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
	bpy.utils.unregister_class(FBXExportTools)
	bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)


if __name__ == "__main__":
	register()
