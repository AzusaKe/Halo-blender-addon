"""Rendered EEVEE/Cycles regression tests for transparency and Ring culling."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy
from mathutils import Vector


project_root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
cache_root = Path(r"F:\codex-cache\halo-blender-addon\render-tests")
cache_root.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(project_root))

import halo_pack_editor
from halo_pack_editor.geometry import billboard_mesh, ring_mesh
from halo_pack_editor.materials import assign_material, create_halo_material


halo_pack_editor.register()
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_percentage = 100
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"
scene.view_settings.look = "AgX - Medium High Contrast"
world = scene.world or bpy.data.worlds.new("Halo Render Test World")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0


def clear_objects():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def image(name, left, right=None):
    right = left if right is None else right
    result = bpy.data.images.new(name, width=2, height=2, alpha=True)
    result.pixels = list(left) + list(right) + list(left) + list(right)
    result.pack()
    return result


def material(name, texture_image, *, cull=False, backface=None):
    backface_id = f"test:{backface[0]}.png" if backface else None
    result = create_halo_material(
        f"test:{name}.png",
        glowing=True,
        backface_culling=cull,
        backface_texture_id=backface_id,
        name=name,
    )
    texture = next(
        node for node in result.node_tree.nodes
        if node.bl_idname == "ShaderNodeTexImage" and node.name != "Halo Backface Texture"
    )
    texture.image = texture_image
    texture.interpolation = "Closest"
    if backface:
        paired = result.node_tree.nodes["Halo Backface Texture"]
        paired.image = backface[1]
        paired.interpolation = "Closest"
        assert result.get("halo_shader_backface_mode") == "PAIRED_TEXTURE"
    assert result.surface_render_method == "DITHERED"
    assert result.use_backface_culling is cull
    return result


def camera_at(location, direction, *, ortho_scale):
    camera_data = bpy.data.cameras.new("Halo Render Test Camera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = ortho_scale
    camera_data.lens = 50
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = Vector(direction).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    return camera


def render(name, width, height):
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.filepath = str(cache_root / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    result = bpy.data.images.load(scene.render.filepath, check_existing=False)
    pixels = list(result.pixels)
    size = (int(result.size[0]), int(result.size[1]))
    bpy.data.images.remove(result)
    return pixels, size[0], size[1]


def pixel(rendered, width, x, y):
    offset = (y * width + x) * 4
    return tuple(rendered[offset:offset + 4])


# Two Halo objects intentionally share the same object origin, reproducing
# BLENDED's ambiguous object sorting.  The front PNG is opaque red on one side
# and fully transparent on the other; DITHERED must reveal the green object
# behind the transparent pixels instead of the black world background.
clear_objects()
front_mesh = billboard_mesh("Transparency Front", (2.0, 1.0))
front = bpy.data.objects.new("Transparency Front", front_mesh)
scene.collection.objects.link(front)
assign_material(front, material("Transparency Front", image(
    "Transparency Front Image",
    (1.0, 0.0, 0.0, 1.0),
    (1.0, 0.0, 0.0, 0.0),
)))

back_mesh = billboard_mesh("Transparency Back", (2.0, 1.0))
for vertex in back_mesh.vertices:
    vertex.co.z -= 0.1
back = bpy.data.objects.new("Transparency Back", back_mesh)
scene.collection.objects.link(back)
assign_material(back, material("Transparency Back", image(
    "Transparency Back Image", (0.0, 1.0, 0.0, 1.0),
)))
camera_at((0.0, 0.0, 3.0), (0.0, 0.0, -1.0), ortho_scale=1.4)
rendered, width, height = render("eevee_transparency", 128, 64)
samples = (pixel(rendered, width, 32, height // 2), pixel(rendered, width, 96, height // 2))
assert any(color[0] > color[1] * 1.5 and color[0] > 0.2 for color in samples), samples
assert any(color[1] > color[0] * 1.5 and color[1] > 0.2 for color in samples), samples


# Explicit Ring inner/outer textures use one double-sided preview surface.
# From outside the material must select red; from inside it must select blue.
clear_objects()
ring_data = ring_mesh("Cull Ring", (1.0, 1.0), segments=64, with_inner=False)
ring = bpy.data.objects.new("Cull Ring", ring_data)
scene.collection.objects.link(ring)
outer_image = image("Cull Ring Outer Image", (1.0, 0.0, 0.0, 1.0))
inner_image = image("Cull Ring Inner Image", (0.0, 0.0, 1.0, 1.0))
assign_material(ring, material("Cull Ring Outer", outer_image, backface=("Cull Ring Inner", inner_image)), 0)
assert len(ring.data.polygons) == 64
assert len(ring.data.materials) == 1
camera_at((4.0, 0.0, 0.0), (-1.0, 0.0, 0.0), ortho_scale=3.0)
rendered, width, height = render("eevee_ring_culling", 64, 64)
outside = pixel(rendered, width, width // 2, height // 2)
assert outside[0] > outside[2] * 2.0 and outside[0] > 0.2, outside

# Cycles uses the same Geometry.Backfacing side selection.  From outside only
# the red outer texture may remain; from the center only the blue inner texture
# may remain.
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 16
scene.cycles.use_denoising = False
rendered, width, height = render("cycles_ring_outside", 64, 64)
cycles_outside = pixel(rendered, width, width // 2, height // 2)
assert cycles_outside[0] > cycles_outside[2] * 2.0 and cycles_outside[0] > 0.2, cycles_outside

camera_at((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), ortho_scale=1.4)
rendered, width, height = render("cycles_ring_inside", 64, 64)
cycles_inside = pixel(rendered, width, width // 2, height // 2)
assert cycles_inside[2] > cycles_inside[0] * 2.0 and cycles_inside[2] > 0.2, cycles_inside

# A Halo full-bright material must not behave as a Cycles area light.  Render a
# white receiver with a very strong Halo emitter just outside the camera view,
# then bypass the Light Path gate as a positive control for the same setup.
clear_objects()
receiver_mesh = billboard_mesh("Indirect Receiver", (2.0, 2.0))
receiver = bpy.data.objects.new("Indirect Receiver", receiver_mesh)
scene.collection.objects.link(receiver)
receiver_material = bpy.data.materials.new("Indirect Receiver Material")
receiver_material.use_nodes = True
receiver_shader = next(node for node in receiver_material.node_tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
receiver_shader.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
receiver_shader.inputs["Roughness"].default_value = 1.0
assign_material(receiver, receiver_material)

emitter_mesh = billboard_mesh("Halo Non-Light Emitter", (1.0, 1.0))
emitter = bpy.data.objects.new("Halo Non-Light Emitter", emitter_mesh)
scene.collection.objects.link(emitter)
emitter.location = (1.45, 0.0, 0.5)
emitter_material = material("Halo Non-Light Emitter", image("Halo Non-Light Image", (1.0, 0.1, 0.0, 1.0)))
for node in emitter_material.node_tree.nodes:
    if node.bl_idname == "ShaderNodeBsdfPrincipled":
        node.inputs["Emission Strength"].default_value = 1000.0
assign_material(emitter, emitter_material)
camera_at((0.0, 0.0, 3.0), (0.0, 0.0, -1.0), ortho_scale=2.2)
scene.cycles.samples = 64
rendered, width, height = render("cycles_no_indirect_halo_light", 64, 64)
no_light = pixel(rendered, width, width // 2, height // 2)
assert max(no_light[:3]) < 0.03, no_light

nodes = emitter_material.node_tree.nodes
links = emitter_material.node_tree.links
output = next(node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial")
for link in list(output.inputs["Surface"].links):
    links.remove(link)
emitter_shader = next(node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
links.new(emitter_shader.outputs["BSDF"], output.inputs["Surface"])
rendered, width, height = render("cycles_indirect_light_control", 64, 64)
light_control = pixel(rendered, width, width // 2, height // 2)
assert max(light_control[:3]) > max(no_light[:3]) + 0.05, (no_light, light_control)

print("RENDER_OK", {
    "transparent_samples": samples,
    "eevee_ring_outside": outside,
    "cycles_ring_outside": cycles_outside,
    "cycles_ring_inside": cycles_inside,
    "cycles_no_indirect_light": no_light,
    "cycles_indirect_control": light_control,
})
