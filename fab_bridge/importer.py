"""Turn a downloaded folder into scene data (SPEC.md §10).

Called on the main thread through `download.manager.IMPORT_HOOK`. Everything
here touches `bpy`; nothing else in the download layer does.

Two paths:

* meshes (glTF / FBX / OBJ / USD / blend) — run the importer, adopt the new
  objects into `FAB_<title>`, and rebuild a Principled material from the sibling
  maps when the importer did not bring a usable one.
* texture sets — build `M_<title>` from the maps and drop it on the active empty
  mesh, or on a plane cut to the scan's real-world size.
"""

from __future__ import annotations

import os

import bpy

from .core import errors, log
from .download import archive

log = log.get("importer")

COLLECTION_PREFIX = "FAB_"
MATERIAL_PREFIX = "M_"
MESH_FORMATS = {"converted-files", "fbx", "gltf", "obj", "usd", "usdz", "blend"}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def import_download(job) -> str:
    """Import one finished job. Raises `errors.ImportFailed`; returns a summary."""
    directory = job.directory
    if not directory or not os.path.isdir(directory):
        raise errors.ImportFailed("the extracted folder is gone")

    title = archive.safe_name(job.title or "Fab asset")
    collection = _collection(COLLECTION_PREFIX + title)

    if job.format_code == "texture-set":
        return _import_surface(job, collection, title)

    mesh_file = archive.find_import_file(directory, job.format_code)
    if not mesh_file:
        # A mesh pack with no mesh (or a texture-only download): the maps are
        # still useful, so fall back to the surface path.
        log.info("no mesh found in %s; building the material instead", directory)
        return _import_surface(job, collection, title)

    objects = _import_mesh(mesh_file)
    if not objects:
        raise errors.ImportFailed(f"Blender imported nothing from {os.path.basename(mesh_file)}")

    _adopt(collection, objects)
    _root_empty(collection, objects, title)
    _select(objects)

    textures = archive.find_textures(directory)
    rebuilt = False
    if textures and not _has_materials(objects):
        material = build_material(
            title, textures, job.metadata, source_format=job.format_code
        )
        for obj in objects:
            if obj.type == "MESH":
                obj.data.materials.append(material)
        rebuilt = True

    summary = f"Imported {len(objects)} object{'s' if len(objects) != 1 else ''}"
    if rebuilt:
        summary += f" with {len(textures)} maps"
    return summary


# ---------------------------------------------------------------------------
# scene plumbing
# ---------------------------------------------------------------------------
def _scene():
    scene = bpy.context.scene
    if scene is None:  # pragma: no cover - background with no file
        raise errors.ImportFailed("no scene is open")
    return scene


def _collection(name: str):
    existing = bpy.data.collections.get(name)
    if existing is not None:
        return existing
    collection = bpy.data.collections.new(name)
    _scene().collection.children.link(collection)
    return collection


def _adopt(collection, objects) -> None:
    for obj in objects:
        for old in list(obj.users_collection):
            if old is not collection:
                old.objects.unlink(obj)
        if collection not in obj.users_collection:
            collection.objects.link(obj)


def _select(objects) -> None:
    try:
        view_layer = bpy.context.view_layer
        for obj in view_layer.objects:
            obj.select_set(False)
        for obj in objects:
            obj.select_set(True)
        view_layer.objects.active = objects[0]
    except Exception:  # noqa: BLE001 - selection is cosmetic
        log.debug("could not select imported objects")


def _root_empty(collection, objects, title: str):
    roots = [obj for obj in objects if obj.parent is None]
    if len(roots) < 2:
        return None
    empty = bpy.data.objects.new(title, None)
    empty.empty_display_size = 0.5
    collection.objects.link(empty)
    for obj in roots:
        obj.parent = empty
    return empty


def _has_materials(objects) -> bool:
    return any(obj.type == "MESH" and obj.data.materials for obj in objects)


# ---------------------------------------------------------------------------
# mesh importers
# ---------------------------------------------------------------------------
def _import_mesh(path: str):
    extension = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)

    if extension in {".gltf", ".glb"}:
        bpy.ops.import_scene.gltf(filepath=path)
    elif extension == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif extension == ".obj":
        try:
            bpy.ops.wm.obj_import(filepath=path)
        except AttributeError:  # pragma: no cover - Blender < 3.2
            bpy.ops.import_scene.obj(filepath=path)
    elif extension in {".usd", ".usda", ".usdc", ".usdz"}:
        bpy.ops.wm.usd_import(filepath=path)
    elif extension == ".blend":
        _append_blend(path)
    else:
        raise errors.ImportFailed(f"no importer for {extension}")

    return [obj for obj in bpy.data.objects if obj not in before]


def _append_blend(path: str) -> None:
    with bpy.data.libraries.load(path, link=False) as (source, target):
        target.objects = list(source.objects)
    for obj in target.objects:
        if obj is not None and not obj.users_collection:
            _scene().collection.objects.link(obj)


# ---------------------------------------------------------------------------
# texture set
# ---------------------------------------------------------------------------
def _import_surface(job, collection, title: str) -> str:
    directory = job.directory
    textures = archive.find_textures(directory)
    if not textures:
        raise errors.ImportFailed("no recognised texture maps in the archive")

    active = getattr(bpy.context.view_layer.objects, "active", None)
    apply_to_active = (
        active is not None
        and active.type == "MESH"
        and not active.data.materials
    )

    if apply_to_active:
        material = build_material(
            title, textures, job.metadata, source_format=job.format_code
        )
        active.data.materials.append(material)
        _select([active])
        return f"Applied {len(textures)} maps to {active.name}"

    width, height = archive.physical_size(job.metadata or {})
    material = build_material(
        title, textures, job.metadata, plane=True, source_format=job.format_code
    )
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, 0.0, 0.0))
    plane = bpy.context.object
    plane.name = title
    plane.scale = (width, height, 1.0)
    try:
        # Make `plane.dimensions` (and anything reading it immediately) honest.
        bpy.context.view_layer.update()
    except Exception:  # noqa: BLE001 - cosmetic
        pass
    plane.data.materials.append(material)
    _adopt(collection, [plane])
    _select([plane])

    if _pref("add_subdivision", False):
        modifier = plane.modifiers.new("Subdivision", "SUBSURF")
        modifier.levels = 2
        modifier.render_levels = 2

    return f"Built {len(textures)}-map material on a {width:g}×{height:g} m plane"


# ---------------------------------------------------------------------------
# material builder (SPEC.md §10.2)
# ---------------------------------------------------------------------------
def build_material(
    title: str,
    textures: dict[str, str],
    metadata: dict,
    *,
    plane: bool = False,
    source_format: str = "",
):
    """`textures` is channel → path from `archive.find_textures`."""
    if not textures:
        raise errors.ImportFailed("no texture maps to build a material from")

    material = bpy.data.materials.get(MATERIAL_PREFIX + title) or bpy.data.materials.new(
        MATERIAL_PREFIX + title
    )
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.name = "Material Output"
    output.location = (600, 0)
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.name = "Principled BSDF"
    principled.location = (300, 0)
    links.new(principled.outputs[0], output.inputs["Surface"])
    _set(principled, "Metallic", 0.0)
    _set(principled, "IOR", 1.45)
    _set(principled, "Specular IOR Level", 0.0, "Specular", 0.0)
    _set(principled, "Coat Weight", 0.0, "Clearcoat", 0.0)

    coordinates = nodes.new("ShaderNodeTexCoord")
    coordinates.name = "TexCoord"
    coordinates.location = (-900, 0)
    mapping = nodes.new("ShaderNodeMapping")
    mapping.name = "Mapping"
    mapping.location = (-700, 0)
    links.new(coordinates.outputs["UV"], mapping.inputs["Vector"])
    if plane:
        # The plane is already cut to the scan's physical size, so one tile
        # across the 0–1 UVs reads correctly.
        mapping.inputs["Scale"].default_value = (1.0, 1.0, 1.0)
    elif _pref("tiling_mode", "physical") == "none":
        mapping.inputs["Scale"].default_value = (1.0, 1.0, 1.0)
    else:
        width, height = archive.physical_size(metadata or {})
        mapping.inputs["Scale"].default_value = (1.0 / width, 1.0 / height, 1.0)

    def image(channel: str, non_color: bool):
        node = nodes.new("ShaderNodeTexImage")
        node.name = f"Image {channel}"
        node.label = os.path.basename(textures[channel])
        node.location = (-450, 0)
        node.image = _load_image(textures[channel], non_color)
        links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        return node

    albedo = image("albedo", False) if "albedo" in textures else None
    if albedo is not None:
        color = albedo.outputs["Color"]
        if "ao" in textures:
            ao = image("ao", True)
            color = _mix_multiply(nodes, links, color, ao.outputs["Color"], 0.5)
        links.new(color, principled.inputs["Base Color"])

    if "roughness" in textures:
        roughness = image("roughness", True)
        links.new(_red(nodes, links, roughness.outputs["Color"]), principled.inputs["Roughness"])
    elif "gloss" in textures:
        gloss = image("gloss", True)
        invert = nodes.new("ShaderNodeInvert")
        invert.name = "Invert gloss"
        links.new(gloss.outputs["Color"], invert.inputs["Color"])
        links.new(invert.outputs["Color"], principled.inputs["Roughness"])

    if "metal" in textures:
        metal = image("metal", True)
        links.new(_red(nodes, links, metal.outputs["Color"]), principled.inputs["Metallic"])

    if "specular" in textures and "metal" not in textures:
        specular = image("specular", True)
        _link(links, principled, "Specular IOR Level", specular.outputs["Color"], "Specular")

    if "normal" in textures:
        normal = image("normal", True)
        color = normal.outputs["Color"]
        if _flip_normals(source_format):
            color = _flip_green(nodes, links, color)
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.name = "Normal Map"
        links.new(color, normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])

    displacement = None
    if "displacement" in textures and _pref("displacement", True):
        height = image("displacement", True)
        displacement = nodes.new("ShaderNodeDisplacement")
        displacement.name = "Displacement"
        displacement.location = (300, -400)
        links.new(height.outputs["Color"], displacement.inputs["Height"])
        displacement.inputs["Scale"].default_value = _number(metadata.get("displacementScale"), 1.0)
        displacement.inputs["Midlevel"].default_value = _number(metadata.get("displacementBias"), 0.5)
        links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])
        try:
            material.displacement_method = "BOTH"
        except (AttributeError, TypeError):  # pragma: no cover - EEVEE-only builds
            pass
    elif "bump" in textures:
        bump_map = image("bump", True)
        bump = nodes.new("ShaderNodeBump")
        bump.name = "Bump"
        links.new(bump_map.outputs["Color"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], principled.inputs["Normal"])

    if "opacity" in textures:
        opacity = image("opacity", True)
        links.new(opacity.outputs["Color"], principled.inputs["Alpha"])
        try:
            material.surface_render_method = "DITHERED"
        except (AttributeError, TypeError):  # pragma: no cover - Blender < 4.2
            try:
                material.blend_method = "HASHED"
            except (AttributeError, TypeError):
                pass

    if "emission" in textures:
        emission = image("emission", False)
        _link(links, principled, "Emission Color", emission.outputs["Color"], "Emission")
        _set(principled, "Emission Strength", 5.0)

    log.info("built material %s from %s", material.name, ", ".join(sorted(textures)))
    return material


def _load_image(path: str, non_color: bool):
    image = bpy.data.images.load(path, check_existing=True)
    try:
        image.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
    except (TypeError, AttributeError):
        pass
    if _pref("pack_images", False):
        try:
            image.pack()
        except RuntimeError:
            log.debug("could not pack %s", os.path.basename(path))
    return image


def _set(node, name: str, value, fallback_name: str | None = None, fallback_value=None) -> None:
    socket = node.inputs.get(name)
    if socket is None and fallback_name:
        socket = node.inputs.get(fallback_name)
        if socket is not None:
            value = fallback_value
    if socket is not None:
        try:
            socket.default_value = value
        except (TypeError, ValueError):
            pass


def _link(links, node, name: str, socket, fallback_name: str | None = None) -> None:
    target = node.inputs.get(name)
    if target is None and fallback_name:
        target = node.inputs.get(fallback_name)
    if target is not None:
        links.new(socket, target)


def _red(nodes, links, socket):
    try:
        split = nodes.new("ShaderNodeSeparateColor")
        split.mode = "RGB"
    except RuntimeError:  # pragma: no cover - Blender < 3.3
        split = nodes.new("ShaderNodeSeparateRGB")
    links.new(socket, split.inputs[0])
    return split.outputs[0]


def _mix_multiply(nodes, links, a, b, factor: float):
    try:
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs[0].default_value = factor
        colors = [socket for socket in mix.inputs if socket.type == "RGBA"]
        outputs = [socket for socket in mix.outputs if socket.type == "RGBA"]
        if len(colors) >= 2 and outputs:
            links.new(a, colors[0])
            links.new(b, colors[1])
            return outputs[0]
    except (RuntimeError, IndexError):  # pragma: no cover - very old Blender
        pass
    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs["Fac"].default_value = factor
    links.new(a, mix.inputs["Color1"])
    links.new(b, mix.inputs["Color2"])
    return mix.outputs["Color"]


def _flip_green(nodes, links, socket):
    split = nodes.new("ShaderNodeSeparateColor")
    split.mode = "RGB"
    combine = nodes.new("ShaderNodeCombineColor")
    combine.mode = "RGB"
    links.new(socket, split.inputs[0])
    links.new(split.outputs[0], combine.inputs[0])
    one_minus = nodes.new("ShaderNodeMath")
    one_minus.operation = "SUBTRACT"
    one_minus.inputs[0].default_value = 1.0
    links.new(split.outputs[1], one_minus.inputs[1])
    links.new(one_minus.outputs[0], combine.inputs[1])
    links.new(split.outputs[2], combine.inputs[2])
    return combine.outputs[0]


def _flip_normals(source_format: str = "") -> bool:
    choice = _pref("flip_normals", "auto")
    if choice == "always":
        return True
    if choice == "never":
        return False
    # Auto: UE-sourced FBX maps are green-flipped relative to Blender; Fab's
    # converted glTF/GLB files already match.
    return source_format == "fbx"


def _number(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pref(name: str, default):
    try:
        from .ui import prefs

        settings = prefs.get()
    except Exception:  # noqa: BLE001 - headless runs have no preferences
        return default
    return getattr(settings, name, default) if settings else default
