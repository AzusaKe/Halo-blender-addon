"""Copy-on-write namespace relocation, independent of Blender's scene APIs."""

from pathlib import Path
import re

from .texture_usage import texture_candidates
from ..materials import copy_texture_with_sidecars


def remap_texture_references(value, mapping):
    """Only resource fields change; IDs, arbitrary strings and unknown keys survive."""
    if isinstance(value, dict):
        return {key: mapping.get(child, child)
                if key in {"texture", "outer_texture", "inner_texture"} and isinstance(child, str)
                else remap_texture_references(child, mapping)
                for key, child in value.items()}
    if isinstance(value, list):
        return [remap_texture_references(child, mapping) for child in value]
    return value


def migrate_texture_families(pack_root, identifiers, namespace, *, other_pack_roots=()):
    """Return reference mapping and notices; never remove or overwrite old assets.

    All textures used by this definition move into its new namespace, keeping
    subdirectories. Shared originals remain available to other definitions.
    Missing bases stay missing (with any surviving sidecars), rather than
    accidentally binding to a different existing image in the new namespace.
    """
    if not re.fullmatch(r"[a-z0-9_.-]+", namespace) or namespace in {".", ".."}:
        raise ValueError(f"无效的命名空间：{namespace}")
    root = Path(pack_root).resolve()
    mapping, resolved, notices = {}, {}, []
    for identifier in sorted(set(identifiers)):
        candidates = texture_candidates(identifier)
        if not candidates:
            raise ValueError(f"无法安全迁移贴图引用：{identifier}")
        paths = [(root / path).resolve() for path in candidates]
        if any(not path.is_relative_to(root) for path in paths):
            raise ValueError(f"贴图路径越界：{identifier}")
        source = next((path for path in paths if path.is_file()), paths[-1])
        relative = Path(candidates[-1]).relative_to("assets")
        if relative.parts[0] == namespace:
            continue
        # Explicit .png and extensionless spellings must share one copy.
        if source not in resolved:
            relative = source.relative_to(root / "assets" / relative.parts[0])
            requested = f"{namespace}:{relative.as_posix()}"
            copied = copy_texture_with_sidecars(str(source), str(root), requested, allow_missing_base=True,
                                               other_pack_roots=other_pack_roots)
            selected = Path(copied[0]).relative_to(root / "assets" / namespace).as_posix()
            resolved[source] = f"{namespace}:{selected}"
            if resolved[source] != requested:
                notices.append(f"贴图重名：{requested} → {resolved[source]}")
            if not source.is_file():
                notices.append(f"贴图缺失：{identifier}；已迁移引用及可用附图，请重新链接 PNG")
        mapping[identifier] = resolved[source]
    return mapping, notices
