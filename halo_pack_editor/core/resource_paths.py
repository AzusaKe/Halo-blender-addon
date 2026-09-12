"""Minecraft resource-id normalization used by import and export paths.

Minecraft resource identifiers are case-sensitive and only accept lower-case
ASCII characters.  Windows lets an editor create and preview files whose names
contain upper-case characters, so the final pack needs an explicit migration
step instead of relying on the host filesystem.
"""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import uuid
from typing import Any


RESOURCE_ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9/._-]+$")
RESOURCE_KEYS = frozenset({"texture", "inner_texture", "model"})
_TEXTURE_FAMILY_SUFFIXES = ("", ".mcmeta", "_n", "_n.mcmeta", "_s", "_s.mcmeta", "_e", "_e.mcmeta")


@dataclass(frozen=True)
class ResourceRename:
    before: str
    after: str


def lowercase_resource_identifier(value: str) -> str:
    """Lower-case both halves of a Minecraft resource identifier.

    The function deliberately does not replace unsupported punctuation.  Such
    input remains visible to validation instead of silently changing identity.
    """

    raw = str(value or "").strip().replace("\\", "/")
    if ":" in raw:
        namespace, path = raw.split(":", 1)
        return f"{namespace.lower()}:{path.lower()}"
    return raw.lower()


def _iter_resource_slots(value: Any):
    if isinstance(value, MutableMapping):
        for key, child in value.items():
            if key in RESOURCE_KEYS and isinstance(child, str) and child.strip():
                yield value, key, "model" if key == "model" else "texture"
            yield from _iter_resource_slots(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_resource_slots(child)


def _identifier_relative(identifier: str) -> PurePosixPath | None:
    value = identifier.replace("\\", "/")
    namespace, path = value.split(":", 1) if ":" in value else ("minecraft", value)
    relative = PurePosixPath(path.lstrip("/"))
    if not namespace or not relative.parts or ".." in relative.parts:
        return None
    return PurePosixPath("assets", namespace, *relative.parts)


def _family_relative(base: PurePosixPath, suffix: str) -> PurePosixPath:
    if suffix == ".mcmeta":
        return PurePosixPath(str(base) + suffix)
    if suffix.endswith(".mcmeta"):
        image_suffix = suffix.removesuffix(".mcmeta")
        image = base.with_name(base.stem + image_suffix + base.suffix)
        return PurePosixPath(str(image) + ".mcmeta")
    return base.with_name(base.stem + suffix + base.suffix)


def _file_index(root: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            result.setdefault(relative.casefold(), []).append(path)
    return result


def _find_file(root: Path, relative: PurePosixPath, index: dict[str, list[Path]]) -> Path | None:
    exact = root / Path(*relative.parts)
    if exact.is_file():
        # On a case-insensitive filesystem ``exact`` may not expose the casing
        # stored by the directory.  Prefer the enumerated path for case renames.
        matches = index.get(relative.as_posix().casefold(), ())
        return matches[0] if matches else exact
    matches = index.get(relative.as_posix().casefold(), ())
    return matches[0] if matches else None


def _digest(path: Path) -> bytes:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _move_with_case(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _same_file(source, destination):
        if source.relative_to(source.anchor).as_posix() == destination.relative_to(destination.anchor).as_posix():
            return
        temporary = source.with_name(f".{source.name}.halo_case_{uuid.uuid4().hex}")
        os.replace(source, temporary)
        os.replace(temporary, destination)
        return
    os.replace(source, destination)


def _family(root: Path, base: PurePosixPath, kind: str, index: dict[str, list[Path]]) -> dict[str, Path]:
    suffixes = _TEXTURE_FAMILY_SUFFIXES if kind == "texture" and base.suffix.lower() == ".png" else ("",)
    result = {}
    for suffix in suffixes:
        path = _find_file(root, _family_relative(base, suffix), index)
        if path is not None:
            result[suffix] = path
    return result


def _family_signature(family: dict[str, Path]) -> dict[str, bytes]:
    return {suffix: _digest(path) for suffix, path in family.items()}


def _suffixed(relative: PurePosixPath, index: int) -> PurePosixPath:
    return relative.with_name(f"{relative.stem}_{index}{relative.suffix}")


def _migrate_one(root: Path, before: str, desired: str, kind: str) -> str:
    source_relative = _identifier_relative(before)
    desired_relative = _identifier_relative(desired)
    if source_relative is None or desired_relative is None:
        return desired
    index = _file_index(root)
    source_family = _family(root, source_relative, kind, index)
    if not source_family:
        return desired
    source_paths = {path.resolve() for path in source_family.values()}
    source_signature = _family_signature(source_family)

    candidate = desired_relative
    suffix_index = 0
    while True:
        index = _file_index(root)
        existing = _family(root, candidate, kind, index)
        external = {
            suffix: path for suffix, path in existing.items()
            if path.resolve() not in source_paths
        }
        if not external:
            break
        if _family_signature(existing) == source_signature:
            for path in source_family.values():
                if not any(_same_file(path, kept) for kept in existing.values()):
                    path.unlink(missing_ok=True)
            namespace = candidate.parts[1]
            path = PurePosixPath(*candidate.parts[2:]).as_posix()
            return f"{namespace}:{path}"
        suffix_index += 1
        candidate = _suffixed(desired_relative, suffix_index)

    for suffix, source in source_family.items():
        target_relative = _family_relative(candidate, suffix)
        target = root / Path(*target_relative.parts)
        _move_with_case(source, target)
    namespace = candidate.parts[1]
    path = PurePosixPath(*candidate.parts[2:]).as_posix()
    return f"{namespace}:{path}"


def normalize_staged_resources(root: str | Path, documents: Iterable[Any]) -> list[ResourceRename]:
    """Lower-case referenced assets in an export tree and update JSON in place.

    If lower-casing collides with different bytes, the later resource receives
    a numeric suffix.  Identical resources are reused without duplication.
    """

    root_path = Path(root).resolve()
    mappings: dict[tuple[str, str], str] = {}
    changes: list[ResourceRename] = []
    for document in documents:
        for owner, key, kind in _iter_resource_slots(document):
            before = owner[key]
            desired = lowercase_resource_identifier(before)
            map_key = (kind, before)
            after = mappings.get(map_key)
            if after is None:
                # Run the migration even when the JSON is already lower-case:
                # a Windows project can still contain an upper-case on-disk
                # filename which would become a distinct, missing ZIP member.
                after = _migrate_one(root_path, before, desired, kind)
                mappings[map_key] = after
            owner[key] = after
            if after != before:
                changes.append(ResourceRename(before, after))
    return changes


def _mapping_find(entries: MutableMapping[str, bytes], relative: PurePosixPath) -> str | None:
    wanted = relative.as_posix()
    if wanted in entries:
        return wanted
    folded = wanted.casefold()
    return next((path for path in entries if path.casefold() == folded), None)


def _mapping_family(entries: MutableMapping[str, bytes], base: PurePosixPath, kind: str) -> dict[str, str]:
    suffixes = _TEXTURE_FAMILY_SUFFIXES if kind == "texture" and base.suffix.lower() == ".png" else ("",)
    result = {}
    for suffix in suffixes:
        path = _mapping_find(entries, _family_relative(base, suffix))
        if path is not None:
            result[suffix] = path
    return result


def _mapping_signature(entries: MutableMapping[str, bytes], family: dict[str, str]) -> dict[str, bytes]:
    return {suffix: hashlib.sha256(bytes(entries[path])).digest() for suffix, path in family.items()}


def _migrate_mapping_one(entries: MutableMapping[str, bytes], before: str, desired: str, kind: str) -> str:
    source_relative = _identifier_relative(before)
    desired_relative = _identifier_relative(desired)
    if source_relative is None or desired_relative is None:
        return desired
    source_family = _mapping_family(entries, source_relative, kind)
    if not source_family:
        return desired
    source_paths = set(source_family.values())
    source_signature = _mapping_signature(entries, source_family)
    candidate = desired_relative
    suffix_index = 0
    while True:
        existing = _mapping_family(entries, candidate, kind)
        external = {suffix: path for suffix, path in existing.items() if path not in source_paths}
        if not external:
            break
        if _mapping_signature(entries, existing) == source_signature:
            for path in source_family.values():
                if path not in existing.values():
                    entries.pop(path, None)
            namespace = candidate.parts[1]
            path = PurePosixPath(*candidate.parts[2:]).as_posix()
            return f"{namespace}:{path}"
        suffix_index += 1
        candidate = _suffixed(desired_relative, suffix_index)
    moved = {suffix: bytes(entries[path]) for suffix, path in source_family.items()}
    for path in source_family.values():
        entries.pop(path, None)
    for suffix, payload in moved.items():
        entries[_family_relative(candidate, suffix).as_posix()] = payload
    namespace = candidate.parts[1]
    path = PurePosixPath(*candidate.parts[2:]).as_posix()
    return f"{namespace}:{path}"


def normalize_resource_entries(entries: MutableMapping[str, bytes], documents: Iterable[Any]) -> list[ResourceRename]:
    """Mapping-backed equivalent of :func:`normalize_staged_resources`."""

    mappings: dict[tuple[str, str], str] = {}
    changes: list[ResourceRename] = []
    for document in documents:
        for owner, key, kind in _iter_resource_slots(document):
            before = owner[key]
            desired = lowercase_resource_identifier(before)
            map_key = (kind, before)
            after = mappings.get(map_key)
            if after is None:
                after = _migrate_mapping_one(entries, before, desired, kind)
                mappings[map_key] = after
            owner[key] = after
            if after != before:
                changes.append(ResourceRename(before, after))
    return changes


__all__ = [
    "RESOURCE_ID_RE",
    "ResourceRename",
    "lowercase_resource_identifier",
    "normalize_resource_entries",
    "normalize_staged_resources",
]
