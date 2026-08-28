"""Halo 1.0.10 JSON and resource-pack I/O.

This module deliberately uses only the Python standard library.  Parsing is
permissive at the model boundary: malformed/unknown primitive kinds are kept
as :class:`~.models.RawPrimitive` and reported by :mod:`validation`, allowing
the editor to repair a pack instead of losing the rest of the file.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import os
import shutil
import tempfile
import zipfile
from typing import Any, Iterable, Mapping, Sequence
import uuid

from .models import (
    AnimationTerm,
    BillboardPrimitive,
    DampingConfig,
    HaloDefinition,
    HaloGroup,
    LayerAnimation,
    PackProject,
    Positioning,
    RawPrimitive,
    ResourceLocation,
    RingPrimitive,
    SchemaVersion,
    TransitionConfig,
    TransitionProperty,
    TransitionSegment,
    as_vec2,
    as_vec3,
    clone_json,
)


DEFAULT_PACK_DESCRIPTION = "Halo Pack Editor export"


def _complete_pack_meta(value: Mapping[str, Any] | None, *, pack_format: int = 15) -> dict[str, Any]:
    metadata = clone_json(dict(value)) if isinstance(value, Mapping) else {}
    source_pack = metadata.get("pack")
    pack = clone_json(dict(source_pack)) if isinstance(source_pack, Mapping) else {}
    pack.setdefault("pack_format", pack_format)
    description = pack.get("description")
    if description is None or (isinstance(description, str) and not description.strip()):
        pack["description"] = DEFAULT_PACK_DESCRIPTION
    metadata["pack"] = pack
    return metadata


class SchemaError(ValueError):
    """Raised for JSON that cannot identify a Halo definition."""


def parse_json(value: str | bytes | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Read a JSON object from text, bytes, path, or an already-decoded map."""

    if isinstance(value, Mapping):
        return clone_json(dict(value))
    # A JSON string can be longer than Windows' maximum path length; inspect
    # its first non-space character before asking the filesystem whether it is
    # a path.
    looks_like_json = isinstance(value, str) and value.lstrip().startswith(("{", "["))
    is_path = isinstance(value, Path)
    if isinstance(value, str) and not looks_like_json:
        try:
            is_path = os.path.exists(value)
        except OSError:
            is_path = False
    if is_path:
        try:
            return json.loads(Path(value).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SchemaError(f"unable to read JSON {value!r}: {exc}") from exc
    try:
        return json.loads(value.decode("utf-8") if isinstance(value, bytes) else value)
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise SchemaError(f"invalid JSON: {exc}") from exc


def parse_definition(
    value: str | bytes | Path | Mapping[str, Any],
    *,
    source_path: str | Path | None = None,
    namespace: str | None = None,
) -> HaloDefinition:
    """Parse one ``halo_definitions/*.json`` object.

    Both the current ``layers`` format and the old ``shape`` format are
    accepted.  The returned object owns a deep copy of the original JSON AST.
    """

    root = parse_json(value)
    if not isinstance(root, Mapping):
        raise SchemaError("halo definition root must be a JSON object")
    raw = clone_json(dict(root))
    raw_id = raw.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise SchemaError("halo definition requires a non-empty string 'id'")
    definition_id = raw_id.strip()
    inferred_namespace = definition_id.split(":", 1)[0] if ":" in definition_id else namespace

    try:
        version = SchemaVersion.parse(raw.get("version", str(SchemaVersion.CURRENT)))
    except (TypeError, ValueError) as exc:
        raise SchemaError(str(exc)) from exc

    orientation = str(raw.get("orientation_mode", "locked")).lower()
    if orientation not in {"locked", "free", "sync"}:
        # Match the Java parser's best-effort fallback while validation points
        # out that the authored value is unsupported.
        orientation = "locked"
    sync_offset = as_vec3(raw.get("sync_offset"), (0.0, 0.0, 0.0))
    groups: list[HaloGroup]
    legacy_shape = "layers" not in raw and isinstance(raw.get("shape"), Mapping)
    if isinstance(raw.get("layers"), list):
        groups = [_parse_group(v, f"layers/{i}") for i, v in enumerate(raw["layers"]) if isinstance(v, Mapping)]
    elif legacy_shape:
        groups = _parse_legacy_shape(raw["shape"])
    else:
        groups = []

    return HaloDefinition(
        id=definition_id,
        groups=groups,
        orientation_mode=orientation,
        sync_offset=sync_offset,
        animation=_parse_animation(raw.get("animation"), "animation"),
        positioning=_parse_positioning(raw.get("positioning")),
        damping=_parse_damping(raw.get("damping"), bool(raw.get("allow_angular_momentum", False))),
        allow_angular_momentum=bool(raw.get("allow_angular_momentum", False)),
        hide_on_sleep=bool(raw.get("hide_on_sleep", False)),
        display_in_invisible=bool(raw.get("display_in_invisible", False)),
        schema_version=version,
        startup=_parse_transition_config(raw.get("startup"), "startup"),
        shutdown=_parse_transition_config(raw.get("shutdown"), "shutdown"),
        raw=raw,
        source_path=str(source_path) if source_path is not None else None,
        namespace=inferred_namespace,
        legacy_shape=legacy_shape,
        uid=_stable_uid(f"definition:{definition_id}:{source_path or ''}"),
    )


def _parse_group(value: Mapping[str, Any], path: str) -> HaloGroup:
    raw = clone_json(dict(value))
    group_id = raw.get("id")
    group_id = str(group_id) if group_id is not None else None
    primitives: list[Any] = []
    source_key: str | None = None
    if isinstance(raw.get("primitives"), list):
        source_key = "primitives"
        primitives = [_parse_primitive(v, f"{path}/primitives/{i}")
                      for i, v in enumerate(raw["primitives"]) if isinstance(v, Mapping)]
    elif isinstance(raw.get("primitive"), Mapping):
        source_key = "primitive"
        primitives = [_parse_primitive(raw["primitive"], f"{path}/primitive")]
    children = [_parse_group(v, f"{path}/children/{i}")
                for i, v in enumerate(raw.get("children", [])) if isinstance(v, Mapping)]
    return HaloGroup(
        id=group_id,
        position=as_vec3(raw.get("position")),
        rotation=as_vec3(raw.get("rotation")),
        scale=_number(raw.get("scale", 1.0), 1.0),
        primitives=primitives,
        glowing=bool(raw.get("glowing", True)),
        inherit_alpha=bool(raw.get("inherit_alpha", True)),
        inherit_glow=bool(raw.get("inherit_glow", True)),
        animation=_parse_animation(raw.get("animation"), f"{path}/animation"),
        children=children,
        raw=raw,
        uid=_stable_uid(f"group:{path}:{group_id or ''}"),
        source_primitive_key=source_key,
    )


def _parse_primitive(value: Mapping[str, Any], path: str = "primitive") -> Any:
    raw = clone_json(dict(value))
    primitive_type = str(raw.get("type", "")).lower()
    if primitive_type == "billboard":
        return BillboardPrimitive(
            texture=str(raw.get("texture", "")),
            size=as_vec2(raw.get("size"), (0.5, 0.5)),
            face_camera=bool(raw.get("face_camera", False)),
            raw=raw,
            uid=_stable_uid(f"primitive:{path}"),
        )
    if primitive_type == "ring":
        outer = raw.get("outer_texture", raw.get("texture", ""))
        inner = raw.get("inner_texture")
        return RingPrimitive(
            outer_texture=str(outer),
            inner_texture=str(inner) if inner is not None else None,
            size=as_vec2(raw.get("size"), (0.35, 0.08)),
            segments=_int(raw.get("segments", 32), 32),
            raw=raw,
            uid=_stable_uid(f"primitive:{path}"),
        )
    return RawPrimitive(raw=raw, uid=_stable_uid(f"primitive:{path}"))


def _parse_legacy_shape(shape: Mapping[str, Any]) -> list[HaloGroup]:
    kind = str(shape.get("type", "")).lower()
    if kind == "billboard":
        return [HaloGroup(primitives=[_parse_primitive(shape, "shape")], raw={}, source_primitive_key="primitive")]
    if kind == "multi_billboard":
        primitives = [_parse_primitive(v, f"shape/layers/{i}")
                      for i, v in enumerate(shape.get("layers", [])) if isinstance(v, Mapping)]
        return [HaloGroup(primitives=primitives, raw={}, source_primitive_key="primitives")]
    # Keep an unknown legacy shape visible to the editor as a future primitive.
    return [HaloGroup(primitives=[RawPrimitive(clone_json(dict(shape)))], raw={}, source_primitive_key="primitive")]


def _parse_animation(value: Any, path: str) -> LayerAnimation | None:
    if not isinstance(value, Mapping):
        return None

    def terms(parent: Any, key: str) -> list[AnimationTerm]:
        values = parent.get(key, []) if isinstance(parent, Mapping) else []
        if not isinstance(values, list):
            return []
        return [AnimationTerm.from_json(v) for v in values if isinstance(v, Mapping)]

    def nested(group: str, axis: str) -> list[AnimationTerm]:
        obj = value.get(group, {})
        return terms(obj, axis)

    return LayerAnimation(
        offset_x=nested("offset", "x"), offset_y=nested("offset", "y"), offset_z=nested("offset", "z"),
        rotation_yaw=nested("rotation", "yaw"), rotation_pitch=nested("rotation", "pitch"),
        rotation_roll=nested("rotation", "roll"), scale_x=nested("scale", "x"),
        scale_y=nested("scale", "y"), scale_z=nested("scale", "z"),
        alpha=terms(value, "alpha"), glow=terms(value, "glow"), raw=clone_json(dict(value)),
    )


def _parse_positioning(value: Any) -> Positioning:
    raw = clone_json(dict(value)) if isinstance(value, Mapping) else {}
    return Positioning(as_vec3(raw.get("offset")), _number(raw.get("scale", 1.0), 1.0), raw)


def _parse_damping(value: Any, allow_angular_momentum: bool) -> DampingConfig:
    raw = clone_json(dict(value)) if isinstance(value, Mapping) else {}
    return DampingConfig(
        linear_factor=_number(raw.get("linearFactor", 0.15), 0.15),
        angular_factor=_number(raw.get("angularFactor", 0.1), 0.1),
        max_linear_distance=_number(raw.get("maxLinearDistance", 3.0), 3.0),
        max_angular_degrees=_number(raw.get("maxAngularDegrees", 180.0), 180.0),
        allow_angular_momentum=allow_angular_momentum,
        angular_momentum_factor=_number(raw.get("angularMomentumFactor", 0.3), 0.3),
        max_angular_momentum_degrees=_number(raw.get("maxAngularMomentumDegrees", 45.0), 45.0),
        raw=raw,
    )


def _parse_transition_config(value: Any, direction: str) -> TransitionConfig | None:
    if not isinstance(value, Mapping):
        return None
    segments = [_parse_transition_segment(v) for v in value.get("segments", []) if isinstance(v, Mapping)]
    overrides: dict[str, list[TransitionSegment]] = {}
    raw_overrides = value.get("id_overrides", {})
    if isinstance(raw_overrides, Mapping):
        for group_id, item in raw_overrides.items():
            if isinstance(item, list):
                arr = item
            elif isinstance(item, Mapping) and isinstance(item.get("segments"), list):
                arr = item["segments"]
            else:
                continue
            overrides[str(group_id)] = [_parse_transition_segment(v) for v in arr if isinstance(v, Mapping)]
    return TransitionConfig(segments, overrides, direction, clone_json(dict(value)))


def _parse_transition_segment(value: Mapping[str, Any]) -> TransitionSegment:
    raw = clone_json(dict(value))
    def prop(key: str, size: int, *, scalar: bool = False) -> TransitionProperty | None:
        item = raw.get(key)
        if not isinstance(item, Mapping):
            return None
        return _parse_transition_property(item, size, scalar=scalar)

    alpha = prop("alpha", 1, scalar=True)
    if alpha is None:
        alpha = prop("opacity", 1, scalar=True)
    return TransitionSegment(
        duration=_number(raw.get("duration", 0.0), 0.0),
        easing=str(raw.get("easing", "linear")),
        offset=prop("offset", 3), scale=prop("scale", 3), alpha=alpha,
        rotation=prop("rotation", 3), raw=raw,
    )


def _parse_transition_property(value: Mapping[str, Any], size: int, *, scalar: bool = False) -> TransitionProperty | None:
    raw = clone_json(dict(value))
    def read(key: str) -> tuple[float, ...] | None:
        if key not in raw or raw[key] is None:
            return None
        item = raw[key]
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            values = [float(v) for v in item]
        else:
            values = [float(item)]
        if scalar:
            return (values[0],) if values else None
        while len(values) < size:
            values.append(0.0)
        return tuple(values[:size])
    from_value = read("from")
    to_value = read("to")
    degrees = read("degrees")
    if from_value is None and to_value is None and degrees is None:
        # Keep an empty property absent, like the Java parser.
        return None
    prop_duration = _number(raw["duration"], 0.0) if "duration" in raw and raw["duration"] is not None else None
    prop_easing = str(raw["easing"]) if "easing" in raw and raw["easing"] is not None else None
    return TransitionProperty(from_value, to_value, prop_duration, prop_easing, degrees, raw)


def definition_to_dict(definition: HaloDefinition, *, preserve_unknown: bool = True) -> dict[str, Any]:
    """Serialize a definition while retaining unknown source keys."""

    result = clone_json(definition.raw) if preserve_unknown and isinstance(definition.raw, Mapping) else {}
    result["id"] = definition.id
    result["version"] = str(definition.schema_version)
    result["orientation_mode"] = definition.orientation_mode
    result["allow_angular_momentum"] = bool(definition.allow_angular_momentum)
    if definition.orientation_mode == "sync" or "sync_offset" in result:
        result["sync_offset"] = list(definition.sync_offset)

    if definition.legacy_shape and "layers" not in result and _legacy_representable(definition.groups):
        result["shape"] = _groups_to_legacy_shape(definition.groups)
        result.pop("layers", None)
    else:
        result["layers"] = [_group_to_dict(g, preserve_unknown=preserve_unknown) for g in definition.groups]
        result.pop("shape", None)

    result["animation"] = definition.animation.to_json() if definition.animation else {}
    result["hide_on_sleep"] = bool(definition.hide_on_sleep)
    result["display_in_invisible"] = bool(definition.display_in_invisible)
    result["positioning"] = definition.positioning.to_json()
    result["damping"] = definition.damping.to_json()
    if definition.startup is not None:
        result["startup"] = definition.startup.to_json()
    elif "startup" in result:
        result.pop("startup", None)
    if definition.shutdown is not None:
        result["shutdown"] = definition.shutdown.to_json()
    elif "shutdown" in result:
        result.pop("shutdown", None)
    return result


def dumps_definition(definition: HaloDefinition, *, preserve_unknown: bool = True) -> str:
    return json.dumps(definition_to_dict(definition, preserve_unknown=preserve_unknown), ensure_ascii=False, indent=2) + "\n"


def dump_definition(definition: HaloDefinition, path: str | Path, *, preserve_unknown: bool = True) -> None:
    Path(path).write_text(dumps_definition(definition, preserve_unknown=preserve_unknown), encoding="utf-8")


def _group_to_dict(group: HaloGroup, *, preserve_unknown: bool) -> dict[str, Any]:
    result = clone_json(group.raw) if preserve_unknown and isinstance(group.raw, Mapping) else {}
    if group.id is None:
        result.pop("id", None)
    else:
        result["id"] = group.id
    result.update({
        "position": list(group.position), "rotation": list(group.rotation), "scale": group.scale,
        "glowing": bool(group.glowing), "inherit_alpha": bool(group.inherit_alpha),
        "inherit_glow": bool(group.inherit_glow),
    })
    result["animation"] = group.animation.to_json() if group.animation else {}
    primitive_values = [p.to_json() for p in group.primitives]
    if group.source_primitive_key == "primitive" and len(primitive_values) == 1:
        result["primitive"] = primitive_values[0]
        result.pop("primitives", None)
    else:
        if primitive_values or "primitives" in result or "primitive" not in result:
            result["primitives"] = primitive_values
        result.pop("primitive", None)
    result["children"] = [_group_to_dict(child, preserve_unknown=preserve_unknown) for child in group.children]
    return result


def _legacy_representable(groups: Sequence[HaloGroup]) -> bool:
    return len(groups) == 1 and not groups[0].children and all(
        isinstance(p, BillboardPrimitive) for p in groups[0].primitives
    )


def _groups_to_legacy_shape(groups: Sequence[HaloGroup]) -> dict[str, Any]:
    primitives = [p.to_json() for p in groups[0].primitives]
    if len(primitives) == 1:
        return primitives[0]
    return {"type": "multi_billboard", "layers": primitives}


def load_pack(source: str | Path, *, strict: bool = False) -> PackProject:
    """Load a directory or ZIP resource pack into a :class:`PackProject`.

    ZIP members are validated before reading, so ``../`` and absolute names
    cannot escape the logical pack root.  Invalid definitions are collected in
    ``project.parse_errors`` unless ``strict`` is requested.
    """

    source_path = Path(source)
    files: dict[str, bytes] = {}
    if source_path.is_dir():
        for path in source_path.rglob("*"):
            if path.is_file():
                rel = path.relative_to(source_path).as_posix()
                files[rel] = path.read_bytes()
        source_format = "directory"
    elif source_path.is_file() and source_path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source_path, "r") as archive:
                seen_members: set[str] = set()
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = _safe_member_name(info.filename)
                    if name in seen_members:
                        raise SchemaError(f"duplicate ZIP member path: {name!r}")
                    seen_members.add(name)
                    files[name] = archive.read(info)
        except (OSError, zipfile.BadZipFile) as exc:
            raise SchemaError(f"unable to read ZIP pack {source}: {exc}") from exc
        source_format = "zip"
    else:
        raise SchemaError(f"pack path is neither a directory nor a .zip file: {source}")

    pack_meta: dict[str, Any] = {}
    if "pack.mcmeta" in files:
        try:
            parsed_meta = json.loads(files["pack.mcmeta"].decode("utf-8"))
            if isinstance(parsed_meta, Mapping):
                pack_meta = clone_json(dict(parsed_meta))
        except (UnicodeError, json.JSONDecodeError):
            # Preserve the bytes; validation can tell the user that metadata
            # is invalid without blocking definition editing.
            pack_meta = {}

    project = PackProject(definitions=[], files=files, pack_meta=pack_meta,
                          root=str(source_path), source_format=source_format)
    parse_errors: list[tuple[str, str]] = []
    for name, payload in files.items():
        parts = name.split("/")
        if len(parts) < 4 or parts[0] != "assets" or parts[2] != "halo_definitions" or not name.lower().endswith(".json"):
            continue
        namespace = parts[1]
        try:
            definition = parse_definition(payload, source_path=name, namespace=namespace)
            project.definitions.append(definition)
        except (SchemaError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            parse_errors.append((name, str(exc)))
            if strict:
                raise SchemaError(f"invalid definition {name}: {exc}") from exc
    project.parse_errors = parse_errors
    project.dirty = False
    return project


def project_files(project: PackProject, *, preserve_unknown: bool = True) -> dict[str, bytes]:
    """Return all pack files with edited definitions and metadata applied."""

    result = dict(project.files)
    if project.pack_meta:
        result["pack.mcmeta"] = (json.dumps(_complete_pack_meta(project.pack_meta), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    for definition in project.definitions:
        target = _definition_path(definition)
        result[target] = dumps_definition(definition, preserve_unknown=preserve_unknown).encode("utf-8")
        # A renamed definition should not leave the old source file behind if
        # the editor knows that source belonged to the same definition.
        if definition.source_path and definition.source_path != target:
            result.pop(definition.source_path, None)
    return result


def write_pack(
    project: PackProject,
    target: str | Path,
    *,
    as_zip: bool | None = None,
    overwrite: bool = False,
    preserve_unknown: bool = True,
) -> Path:
    """Write a pack atomically to a directory or ZIP destination."""

    destination = Path(target)
    if as_zip is None:
        as_zip = destination.suffix.lower() == ".zip"
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing pack: {destination}")
    files = project_files(project, preserve_unknown=preserve_unknown)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if as_zip:
        fd, tmp_name = tempfile.mkstemp(prefix="halo-pack-", suffix=".zip", dir=str(destination.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in sorted(files.items()):
                    archive.writestr(name, payload)
            _replace_path(tmp, destination, overwrite)
        finally:
            if tmp.exists():
                tmp.unlink()
    else:
        tmp = Path(tempfile.mkdtemp(prefix="halo-pack-", dir=str(destination.parent)))
        try:
            for name, payload in files.items():
                path = tmp / Path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            _replace_path(tmp, destination, overwrite)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)
    project.dirty = False
    return destination


def new_project(*, pack_format: int = 15, description: str = DEFAULT_PACK_DESCRIPTION) -> PackProject:
    return PackProject(pack_meta={"pack": {"pack_format": pack_format, "description": description}})


def new_definition(
    definition_id: str = "halo:new_halo", *, namespace: str | None = None
) -> HaloDefinition:
    definition_id = definition_id if ":" in definition_id else f"{namespace or 'halo'}:{definition_id}"
    return HaloDefinition(id=definition_id, namespace=definition_id.split(":", 1)[0])


def _definition_path(definition: HaloDefinition) -> str:
    namespace, name = (definition.id.split(":", 1) + [""])[:2] if ":" in definition.id else (definition.namespace or "halo", definition.id)
    return f"assets/{namespace}/halo_definitions/{name}.json"


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or Path(normalized).drive or ".." in parts:
        raise SchemaError(f"unsafe ZIP member path: {name!r}")
    # Reject Windows drive prefixes even when running on POSIX.
    if len(normalized) >= 2 and normalized[1] == ":":
        raise SchemaError(f"unsafe ZIP member path: {name!r}")
    return normalized


def _replace_path(tmp: Path, destination: Path, overwrite: bool) -> None:
    if destination.exists():
        if not overwrite:
            raise FileExistsError(destination)
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    os.replace(tmp, destination)


def _stable_uid(text: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "halo-pack-editor:" + text).hex


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
