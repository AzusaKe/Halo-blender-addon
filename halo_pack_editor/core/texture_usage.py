"""Texture reachability for clean exports; never mutates an editing project."""

from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from .pack_io import iter_texture_references


def texture_candidates(identifier: str) -> tuple[str, ...]:
    """Match the editor's exact resource path / extensionless PNG resolution.

    Minecraft's implicit namespace is minecraft, not the definition's ID.
    Do not guess by basename: an unrelated old file must not satisfy a missing
    reference merely because the two names happen to match.
    """
    if not isinstance(identifier, str) or not identifier.strip():
        return ()
    value = identifier.strip().replace("\\", "/")
    namespace, relative = value.split(":", 1) if ":" in value else ("minecraft", value)
    namespace, relative = namespace or "minecraft", relative.lstrip("/")
    if (not relative or namespace in {".", ".."} or "/" in namespace
            or ".." in PurePosixPath(relative).parts):
        return ()
    # Do not turn cleanup into schema validation: preserve exact references
    # with unusual spelling/case when an existing file really matches them.
    path = f"assets/{namespace}/{relative}"
    return (path,) if PurePosixPath(relative).suffix else (path, path + ".png")


def referenced_texture_ids(documents: Iterable[Any]) -> set[str]:
    return {ref.identifier for document in documents for ref in iter_texture_references(document)
            if isinstance(ref.identifier, str) and ref.identifier.strip()}


def referenced_texture_files(paths: Iterable[str], documents: Iterable[Any]) -> set[str]:
    """Return referenced base images and their labPBR / .mcmeta companions."""
    available = set(paths)
    keep = set()
    for identifier in referenced_texture_ids(documents):
        candidates = texture_candidates(identifier)
        if not candidates:
            continue
        # A missing base can still have useful sidecars; keep those without
        # pretending that another namespace or basename resolves the image.
        selected = next((path for path in candidates if path in available), candidates[-1])
        base = PurePosixPath(selected)
        for suffix in ("", "_n", "_s", "_e"):
            image = base.with_name(base.stem + suffix + base.suffix).as_posix()
            keep.update((image, image + ".mcmeta"))
    return keep & available


def unused_texture_files(paths: Iterable[str], documents: Iterable[Any]) -> set[str]:
    """Prune only texture PNGs and PNG metadata, preserving other pack files.

    Icons such as pack.png and unknown non-texture assets are not candidates.
    This is a pure selection function: callers delete only from export staging.
    """
    available = set(paths)
    keep = referenced_texture_files(available, documents)
    candidates = set()
    for path in available:
        parts = PurePosixPath(path).parts
        if (len(parts) >= 4 and parts[0] == "assets" and parts[2] == "textures"
                and ".." not in parts and path.lower().endswith((".png", ".png.mcmeta"))):
            candidates.add(path)
    return candidates - keep
