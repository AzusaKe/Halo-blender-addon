"""Import, validate, and export Halo resource packs.

This module is intentionally independent of Blender.  A pack is represented by
the :class:`PackProject` object, which contains every original file (including
files unknown to the editor) and a list of definition documents.  Blender code
can build typed model views from ``DefinitionAsset.document.data`` while this
module remains responsible for lossless I/O.

The accepted layout follows the pack produced by ``F:\\HaloPackTool``::

    pack.mcmeta
    pack.png
    assets/<namespace>/halo_definitions/<file>.json
    assets/<namespace>/textures/halo/<file>.png

All paths stored in a project use POSIX separators, even on Windows.  ZIP
archives are read manually rather than through ``ZipFile.extract`` so a
malicious ``../`` member can never escape the destination directory.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from typing import Any, BinaryIO

try:  # Package import (normal add-on use).
    from .json_codec import JsonCodecError, JsonDocument, clone_ast, dumps, parse_json
    from .resource_paths import lowercase_resource_identifier, normalize_resource_entries
except ImportError:  # Direct script/test import.
    from json_codec import JsonCodecError, JsonDocument, clone_ast, dumps, parse_json
    from resource_paths import lowercase_resource_identifier, normalize_resource_entries


PACK_FORMAT = 15
MIN_PACK_FORMAT = [15, 0]
# Minecraft has no wildcard token for pack ranges.  A scalar max_format is a
# major version whose every minor version is accepted, so Java's largest
# positive integer is the practical open-ended upper bound.  This includes
# the current 26.3 resource-pack format 97.1 and future formats until Mojang
# changes the metadata contract itself.
MAX_PACK_FORMAT = 2_147_483_647
SUPPORTED_PACK_FORMATS = {"min_inclusive": 15, "max_inclusive": MAX_PACK_FORMAT}
LEGACY_DEFAULT_MAX_PACK_FORMAT = [88, 0]
LEGACY_DEFAULT_SUPPORTED_PACK_FORMATS = {"min_inclusive": 15, "max_inclusive": 88}
DEFAULT_PACK_DESCRIPTION = "Halo Pack Editor export"
SCHEMA_VERSION = "1.1.0"
PBR_SUFFIXES = ("_n", "_s", "_e")
IDENTIFIER_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
NAMESPACE_RE = re.compile(r"^[a-z0-9_.-]+$")
RESOURCE_PATH_RE = re.compile(r"^[a-z0-9_./-]+$")


def _definition_filename(identifier: str) -> str:
    """Map the resource-path portion of an ID to one portable JSON name."""

    name = identifier.split(":", 1)[-1]
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)[:180]
    return (safe or "halo") + ".json"


def _complete_pack_mcmeta(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Add required Minecraft metadata fields while retaining unknown data."""

    metadata = clone_ast(dict(value)) if isinstance(value, Mapping) else {}
    source_pack = metadata.get("pack")
    pack = clone_ast(dict(source_pack)) if isinstance(source_pack, Mapping) else {}
    pack.setdefault("pack_format", PACK_FORMAT)
    pack.setdefault("supported_formats", clone_ast(SUPPORTED_PACK_FORMATS))
    pack.setdefault("min_format", clone_ast(MIN_PACK_FORMAT))
    pack.setdefault("max_format", clone_ast(MAX_PACK_FORMAT))
    if pack.get("supported_formats") == LEGACY_DEFAULT_SUPPORTED_PACK_FORMATS:
        pack["supported_formats"] = clone_ast(SUPPORTED_PACK_FORMATS)
    if pack.get("max_format") == LEGACY_DEFAULT_MAX_PACK_FORMAT:
        pack["max_format"] = MAX_PACK_FORMAT
    description = pack.get("description")
    if description is None or (isinstance(description, str) and not description.strip()):
        pack["description"] = DEFAULT_PACK_DESCRIPTION
    metadata["pack"] = pack
    return metadata


class PackIOError(ValueError):
    """Base exception for invalid pack input or export arguments."""


class UnsafeArchiveError(PackIOError):
    """Raised when an archive contains an absolute or traversal path."""


class DuplicateArchiveEntryError(PackIOError):
    """Raised when an archive contains two members with the same path."""


@dataclass(frozen=True)
class Diagnostic:
    """A non-fatal import/export validation message."""

    severity: str
    code: str
    message: str
    path: str | None = None
    identifier: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def level(self) -> str:
        return self.severity

    def __str__(self) -> str:
        location = f" [{self.path}]" if self.path else ""
        return f"{self.severity.upper()} {self.code}{location}: {self.message}"


@dataclass(frozen=True)
class TextureReference:
    """A texture field found in a definition AST."""

    identifier: Any
    definition_path: str | None = None
    key: str | None = None
    namespace: str | None = None


@dataclass
class TextureResolution:
    """Result of resolving one Minecraft texture identifier."""

    identifier: str
    resource_id: str | None
    pack_path: str | None
    found: bool
    candidates: tuple[str, ...] = ()
    variants: dict[str, str] = field(default_factory=dict)
    message: str | None = None

    @property
    def path(self) -> str | None:
        return self.pack_path

    @property
    def exists(self) -> bool:
        return self.found

    def __bool__(self) -> bool:
        return self.found

    def __fspath__(self) -> str:
        return self.pack_path or ""

    def __str__(self) -> str:
        return self.pack_path or ""


@dataclass(eq=False)
class TextureImportResult:
    """Details returned after importing an external PNG.

    The object compares equal to its resource-ID string for compatibility with
    simple scripts which historically expected ``import_png(...)`` to return a
    string.
    """

    identifier: str
    path: str
    copied: tuple[str, ...] = ()
    source: str | None = None

    @property
    def resource_id(self) -> str:
        return self.identifier

    @property
    def files(self) -> tuple[str, ...]:
        return self.copied

    def __str__(self) -> str:
        return self.identifier

    def __fspath__(self) -> str:
        return self.path

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.identifier == other
        if isinstance(other, TextureImportResult):
            return (
                self.identifier,
                self.path,
                self.copied,
            ) == (other.identifier, other.path, other.copied)
        return NotImplemented

    def __getitem__(self, key: str) -> Any:
        return {
            "identifier": self.identifier,
            "resource_id": self.identifier,
            "path": self.path,
            "copied": self.copied,
            "files": self.copied,
            "source": self.source,
        }[key]


@dataclass
class DefinitionAsset:
    """One imported halo definition and its preserved source AST."""

    source_path: str
    namespace: str
    name: str
    identifier: str | None
    document: JsonDocument
    raw_bytes: bytes
    valid_identifier: bool = True
    diagnostics: list[Diagnostic] = field(default_factory=list)
    model: Any = None
    dirty: bool = False

    # ``path``/``json_path`` are convenient aliases used by the Blender layer.
    @property
    def path(self) -> str:
        return self.source_path

    @property
    def json_path(self) -> str:
        return self.source_path

    @property
    def ast(self) -> Any:
        return self.document.data

    @property
    def raw_json(self) -> Any:
        return self.document.data

    @property
    def id(self) -> str | None:
        return self.identifier

    @property
    def definition_name(self) -> str:
        """The path component of the JSON ``id`` (or the file stem)."""

        return self.identifier.split(":", 1)[-1] if self.identifier else self.name

    @property
    def raw(self) -> Any:
        """Alias used by the typed core model and Blender bridge."""

        return self.document.data

    def to_dict(self) -> Any:
        return clone_ast(self.document.data)

    @property
    def export_path(self) -> str:
        """Canonical output path, using the JSON ``id`` as authority."""

        out_namespace = self.namespace
        if self.identifier and ":" in self.identifier:
            candidate = self.identifier.split(":", 1)[0]
            if NAMESPACE_RE.fullmatch(candidate):
                out_namespace = candidate
            return f"assets/{out_namespace}/halo_definitions/{_definition_filename(self.identifier)}"
        return f"assets/{out_namespace}/halo_definitions/{PurePosixPath(self.source_path).name}"

    @property
    def resource_id(self) -> str | None:
        return self.identifier

    def to_bytes(self, *, newline: bool = True) -> bytes:
        return dumps(self.document.data, newline=newline).encode("utf-8")

    def update_ast(self, value: Any, *, preserve_unknown: bool = False) -> Any:
        self.document.replace(value, preserve_unknown=preserve_unknown)
        self.dirty = True
        return self.document.data

    def mark_dirty(self) -> None:
        self.document.dirty = True
        self.dirty = True


@dataclass
class PackProject:
    """Lossless in-memory representation of a resource pack."""

    source_path: str | None = None
    source_kind: str = "memory"  # memory, folder, zip
    files: dict[str, bytes] = field(default_factory=dict)
    definitions: list[DefinitionAsset] = field(default_factory=list)
    pack_mcmeta: dict[str, Any] | None = None
    pack_mcmeta_document: JsonDocument | None = None
    pack_mcmeta_raw: bytes | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    texture_imports: list[TextureImportResult] = field(default_factory=list)

    @property
    def halos(self) -> list[DefinitionAsset]:
        return self.definitions

    @property
    def assets(self) -> list[DefinitionAsset]:
        return self.definitions

    @property
    def definition_map(self) -> dict[str, DefinitionAsset]:
        return {d.identifier: d for d in self.definitions if d.identifier}

    @property
    def metadata(self) -> dict[str, Any] | None:
        return self.pack_mcmeta

    @metadata.setter
    def metadata(self, value: dict[str, Any] | None) -> None:
        self.pack_mcmeta = value

    # Compatibility aliases matching ``core.models.PackProject``.  Keeping
    # these as properties lets the I/O object interoperate with the typed model
    # without importing (and thereby coupling to) that module.
    @property
    def pack_meta(self) -> dict[str, Any]:
        return self.pack_mcmeta if isinstance(self.pack_mcmeta, dict) else {}

    @pack_meta.setter
    def pack_meta(self, value: Mapping[str, Any] | None) -> None:
        self.pack_mcmeta = clone_ast(dict(value)) if isinstance(value, Mapping) else None

    @property
    def root(self) -> str | None:
        return self.source_path

    @property
    def source_format(self) -> str:
        return "directory" if self.source_kind == "folder" else self.source_kind

    @property
    def dirty(self) -> bool:
        return any(getattr(d, "dirty", False) or getattr(d.document, "dirty", False) for d in self.definitions)

    @dirty.setter
    def dirty(self, value: bool) -> None:
        if value:
            return
        for definition in self.definitions:
            definition.dirty = False
            definition.document.dirty = False

    def add_definition(self, definition: DefinitionAsset) -> DefinitionAsset:
        self.definitions.append(definition)
        definition.dirty = True
        return definition

    def remove_definition(self, definition: DefinitionAsset) -> None:
        self.definitions.remove(definition)

    def namespaces(self) -> set[str]:
        return {d.namespace for d in self.definitions if d.namespace}

    def get_definition(self, identifier: str) -> DefinitionAsset | None:
        for definition in self.definitions:
            if definition.identifier == identifier:
                return definition
        return None

    def resolve_texture(self, identifier: str, *, default_namespace: str | None = None) -> TextureResolution:
        return resolve_texture_reference(self, identifier, default_namespace=default_namespace)

    def missing_textures(self) -> list[Diagnostic]:
        return diagnose_missing_textures(self)

    def validate(self) -> list[Diagnostic]:
        return validate_pack(self)

    def export_folder(self, target: str | Path, *, overwrite: bool = False, atomic: bool = True) -> Path:
        return export_folder(self, target, overwrite=overwrite, atomic=atomic)

    def export_zip(self, target: str | Path, *, overwrite: bool = False, atomic: bool = True) -> Path:
        return export_zip(self, target, overwrite=overwrite, atomic=atomic)

    def import_png(
        self,
        source: str | Path | bytes,
        *,
        namespace: str,
        filename: str | None = None,
        overwrite: bool = False,
    ) -> TextureImportResult:
        return import_external_png(self, source, namespace=namespace, filename=filename, overwrite=overwrite)


def new_project(
    *,
    description: str = DEFAULT_PACK_DESCRIPTION,
    pack_format: int = PACK_FORMAT,
) -> PackProject:
    """Create a new empty pack with the current default pack metadata."""

    metadata = _complete_pack_mcmeta({
        "pack": {"pack_format": int(pack_format), "description": description},
    })
    return PackProject(pack_mcmeta=metadata, pack_mcmeta_document=JsonDocument(clone_ast(metadata)))


def _canonical_path(value: str | os.PathLike[str], *, allow_empty: bool = False) -> str:
    """Normalize a package-relative path and reject traversal/absolute paths."""

    raw = os.fspath(value).replace("\\", "/")
    if "\x00" in raw:
        raise UnsafeArchiveError("NUL byte in archive path")
    if raw.startswith("/") or raw.startswith("//") or re.match(r"^[A-Za-z]:", raw):
        raise UnsafeArchiveError(f"Absolute archive path is not allowed: {value!r}")
    # Strip harmless ./ prefixes while retaining a strict traversal check.
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        if allow_empty:
            return ""
        raise UnsafeArchiveError("Empty archive path")
    parts = raw.split("/")
    if any(part in {"", "."} for part in parts):
        parts = [part for part in parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise UnsafeArchiveError(f"Path traversal is not allowed: {value!r}")
    normalized = posixpath.normpath("/".join(parts))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise UnsafeArchiveError(f"Invalid archive path: {value!r}")
    return normalized


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _read_zip_entries(source: str | Path | bytes | BinaryIO) -> dict[str, bytes]:
    close = False
    if isinstance(source, (str, Path)):
        try:
            archive: zipfile.ZipFile | None = zipfile.ZipFile(source, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise PackIOError(f"Unable to open ZIP pack {source}: {exc}") from exc
        close = True
    else:
        stream: Any = io.BytesIO(source) if isinstance(source, bytes) else source
        try:
            archive = zipfile.ZipFile(stream, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise PackIOError(f"Unable to open ZIP pack: {exc}") from exc
        close = True
    entries: dict[str, bytes] = {}
    try:
        for info in archive.infolist():
            raw_name = info.filename
            # Directory records are harmless; directory names still go through
            # validation so ``../`` cannot be hidden in one.
            name = _canonical_path(raw_name.rstrip("/"), allow_empty=raw_name.endswith("/"))
            if not name:
                continue
            if info.is_dir() or raw_name.endswith("/"):
                continue
            if _is_zip_symlink(info):
                raise UnsafeArchiveError(f"Symlink archive member is not allowed: {raw_name!r}")
            if name in entries:
                raise DuplicateArchiveEntryError(f"Duplicate archive member: {name}")
            try:
                entries[name] = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise PackIOError(f"Unable to read ZIP member {raw_name!r}: {exc}") from exc
    finally:
        if close:
            archive.close()
    return entries


def safe_extract_zip(
    source: str | Path | bytes | BinaryIO,
    destination: str | Path,
    *,
    overwrite: bool = True,
) -> list[str]:
    """Safely extract a ZIP archive and return extracted relative paths."""

    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve()
    entries = _read_zip_entries(source)
    extracted: list[str] = []
    for name, data in entries.items():
        target = root / Path(*PurePosixPath(name).parts)
        # Resolve the parent even when it does not exist yet; this also catches
        # an attacker-provided symlink already present in ``destination``.
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            parent_resolved = target.parent.resolve()
            parent_resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise UnsafeArchiveError(f"Extraction path escapes destination: {name}") from exc
        if target.exists() and not overwrite:
            raise FileExistsError(target)
        if target.exists() and target.is_symlink():
            raise UnsafeArchiveError(f"Refusing to write through symlink: {name}")
        target.write_bytes(data)
        extracted.append(name)
    return extracted


def _read_folder_entries(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        raise PackIOError(f"Pack folder does not exist: {root}")
    entries: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = _canonical_path(path.relative_to(root).as_posix())
        try:
            entries[rel] = path.read_bytes()
        except OSError as exc:
            raise PackIOError(f"Unable to read pack file {path}: {exc}") from exc
    return entries


def _valid_identifier(raw: Any) -> bool:
    return isinstance(raw, str) and IDENTIFIER_RE.fullmatch(raw) is not None


def _path_namespace(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[1] if len(parts) > 1 else ""


def discover_definition_paths(files: Mapping[str, bytes] | Iterable[str]) -> list[str]:
    """Find all ``assets/*/halo_definitions/*.json`` files in a pack."""

    paths = files.keys() if isinstance(files, Mapping) else files
    result: list[str] = []
    for raw in paths:
        try:
            path = _canonical_path(raw)
        except UnsafeArchiveError:
            continue
        parts = PurePosixPath(path).parts
        if len(parts) >= 4 and parts[0] == "assets" and parts[2] == "halo_definitions" and path.lower().endswith(".json"):
            result.append(path)
    return sorted(set(result))


def _make_definition(source_path: str, raw: bytes, diagnostics: list[Diagnostic]) -> DefinitionAsset | None:
    try:
        document = parse_json(raw, source_path=source_path)
    except JsonCodecError as exc:
        diagnostics.append(Diagnostic("warning", "invalid_json", str(exc), path=source_path))
        return None
    if not isinstance(document.data, dict):
        diagnostics.append(Diagnostic("warning", "definition_not_object", "Definition root must be an object", path=source_path))
        return None
    raw_id = document.data.get("id")
    source_namespace = _path_namespace(source_path)
    identifier = raw_id if isinstance(raw_id, str) else None
    valid = _valid_identifier(raw_id)
    local_diags: list[Diagnostic] = []
    if not valid:
        local_diags.append(
            Diagnostic("warning", "invalid_id", "Definition has no valid namespace:path id", path=source_path, identifier=identifier)
        )
        diagnostics.extend(local_diags)
    else:
        id_namespace = raw_id.split(":", 1)[0]
        if source_namespace and id_namespace != source_namespace:
            local_diags.append(
                Diagnostic(
                    "warning",
                    "namespace_mismatch",
                    f"Path namespace '{source_namespace}' differs from id namespace '{id_namespace}'; id namespace wins on export",
                    path=source_path,
                    identifier=raw_id,
                )
            )
            diagnostics.extend(local_diags)
        source_namespace = id_namespace
    asset = DefinitionAsset(
        source_path=source_path,
        namespace=source_namespace,
        name=PurePosixPath(source_path).stem,
        identifier=identifier,
        document=document,
        raw_bytes=raw,
        valid_identifier=valid,
        diagnostics=local_diags,
    )
    # ``schema.py``/``models.py`` are optional from the I/O module's point of
    # view.  When present, expose a typed view for Blender while retaining the
    # AST above as the authoritative export representation.
    try:
        try:
            from .schema import parse_definition as parse_typed_definition
        except ImportError:
            from schema import parse_definition as parse_typed_definition
        asset.model = parse_typed_definition(document.data, source_path=source_path, namespace=source_namespace)
    except Exception as exc:  # typed parsing is best-effort; raw JSON remains usable
        asset.model = None
        if valid:
            asset.diagnostics.append(
                Diagnostic("info", "typed_model_unavailable", f"Typed model was not created: {exc}", path=source_path)
            )
    return asset


def parse_definition(
    value: Any,
    *,
    source_path: str | Path | None = None,
    namespace: str | None = None,
) -> Any:
    """Parse one definition through the typed schema when available.

    This compatibility entry point intentionally returns the model object from
    ``core.schema`` (when that module is installed) and otherwise returns a
    :class:`JsonDocument`.  Pack import itself always retains a
    :class:`DefinitionAsset` so unknown fields cannot be lost.
    """

    try:
        try:
            from .schema import parse_definition as parse_typed_definition
        except ImportError:
            from schema import parse_definition as parse_typed_definition
        return parse_typed_definition(value, source_path=source_path, namespace=namespace)
    except ImportError:
        return parse_json(value, source_path=str(source_path) if source_path is not None else None)


def definition_to_dict(definition: Any, *, preserve_unknown: bool = True) -> Any:
    """Serialize a typed definition while preserving its source AST."""

    if isinstance(definition, DefinitionAsset):
        return clone_ast(definition.document.data)
    try:
        try:
            from .schema import definition_to_dict as serializer
        except ImportError:
            from schema import definition_to_dict as serializer
        return serializer(definition, preserve_unknown=preserve_unknown)
    except (ImportError, AttributeError):
        for method in ("to_dict", "to_json", "as_dict"):
            callback = getattr(definition, method, None)
            if callable(callback):
                return clone_ast(callback())
        if isinstance(definition, Mapping):
            return clone_ast(dict(definition))
        raise PackIOError(f"Unable to serialize definition {definition!r}")


def dumps_definition(definition: Any, *, preserve_unknown: bool = True, newline: bool = True) -> str:
    return dumps(definition_to_dict(definition, preserve_unknown=preserve_unknown), newline=newline)


def import_pack(source: str | Path | bytes | BinaryIO, *, strict: bool = False) -> PackProject:
    """Import a whole folder or ZIP resource pack.

    Every original file is retained in ``project.files``.  A malformed
    definition is reported and left untouched in that mapping; valid sibling
    definitions still import normally.  ``strict=True`` raises when any such
    diagnostic is produced.
    """

    source_path: str | None = None
    if isinstance(source, (str, Path)) and Path(source).is_dir():
        root = Path(source)
        files = _read_folder_entries(root)
        source_kind = "folder"
        source_path = str(root)
    elif isinstance(source, (str, Path)):
        files = _read_zip_entries(source)
        source_kind = "zip"
        source_path = str(Path(source))
    else:
        files = _read_zip_entries(source)
        source_kind = "zip"

    project = PackProject(source_path=source_path, source_kind=source_kind, files=files)
    meta_raw = files.get("pack.mcmeta")
    if meta_raw is not None:
        project.pack_mcmeta_raw = meta_raw
        try:
            doc = parse_json(meta_raw, source_path="pack.mcmeta")
            if isinstance(doc.data, dict):
                project.pack_mcmeta_document = doc
                project.pack_mcmeta = doc.data
            else:
                project.diagnostics.append(Diagnostic("warning", "pack_mcmeta_not_object", "pack.mcmeta root must be an object", path="pack.mcmeta"))
        except JsonCodecError as exc:
            project.diagnostics.append(Diagnostic("warning", "invalid_pack_mcmeta", str(exc), path="pack.mcmeta"))
    else:
        project.diagnostics.append(Diagnostic("info", "missing_pack_mcmeta", "Pack has no pack.mcmeta file", path="pack.mcmeta"))

    for path in discover_definition_paths(files):
        definition = _make_definition(path, files[path], project.diagnostics)
        if definition is not None:
            project.definitions.append(definition)

    # Duplicate IDs are legal as files but ambiguous to the runtime.  Keep all
    # ASTs for editing while reporting the issue.
    seen: dict[str, str] = {}
    for definition in project.definitions:
        if definition.identifier and definition.identifier in seen:
            project.diagnostics.append(
                Diagnostic(
                    "warning",
                    "duplicate_definition_id",
                    f"Definition id also appears in {seen[definition.identifier]}",
                    path=definition.source_path,
                    identifier=definition.identifier,
                )
            )
        elif definition.identifier:
            seen[definition.identifier] = definition.source_path

    project.diagnostics.extend(diagnose_missing_textures(project, append=False))
    if strict and any(d.severity in {"error", "warning"} for d in project.diagnostics):
        raise PackIOError("Pack import produced diagnostics: " + "; ".join(str(d) for d in project.diagnostics))
    return project


def load_pack(source: str | Path | bytes | BinaryIO, *, strict: bool = False) -> PackProject:
    return import_pack(source, strict=strict)


def read_pack(source: str | Path | bytes | BinaryIO, *, strict: bool = False) -> PackProject:
    return import_pack(source, strict=strict)


def _iter_texture_refs(value: Any, *, definition_path: str | None = None, key: str | None = None) -> Iterable[TextureReference]:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            child_key_str = str(child_key)
            if child_key_str in {"texture", "outer_texture", "inner_texture"}:
                yield TextureReference(child, definition_path=definition_path, key=child_key_str)
            yield from _iter_texture_refs(child, definition_path=definition_path, key=child_key_str)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_texture_refs(child, definition_path=definition_path, key=key)


def iter_texture_references(value: Any, *, definition_path: str | None = None) -> Iterable[TextureReference]:
    """Yield texture fields from a definition AST or :class:`DefinitionAsset`."""

    if isinstance(value, DefinitionAsset):
        definition_path = value.source_path
        value = value.document.data
    yield from _iter_texture_refs(value, definition_path=definition_path)


def texture_references(value: Any, *, definition_path: str | None = None) -> list[TextureReference]:
    return list(iter_texture_references(value, definition_path=definition_path))


def _parse_texture_id(raw: Any, *, default_namespace: str | None = None) -> tuple[str, str, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise PackIOError("Texture reference must be a non-empty string")
    value = raw.strip()
    if value.startswith("`") or value.endswith("`") or "\\" in value:
        raise PackIOError(f"Invalid texture identifier: {raw!r}")
    if ":" in value:
        namespace, path = value.split(":", 1)
    elif default_namespace:
        namespace, path = default_namespace, value
    else:
        raise PackIOError(f"Texture identifier is missing namespace: {raw!r}")
    if not NAMESPACE_RE.fullmatch(namespace or "") or not RESOURCE_PATH_RE.fullmatch(path or ""):
        raise PackIOError(f"Invalid texture identifier: {raw!r}")
    if path.startswith("/") or any(piece == ".." for piece in path.split("/")):
        raise PackIOError(f"Texture path traversal is not allowed: {raw!r}")
    normalized_path = path
    return namespace, normalized_path, f"{namespace}:{normalized_path}"


def _candidate_texture_paths(namespace: str, path: str) -> list[str]:
    path = path.lstrip("/")
    candidates = [f"assets/{namespace}/{path}"]
    if not path.lower().endswith(".png"):
        candidates.append(f"assets/{namespace}/{path}.png")
    # The packer flattens input PNGs into textures/halo.  Retain a basename
    # fallback for packs produced from the packer's loose input directory.
    basename = PurePosixPath(path).name
    if not basename.lower().endswith(".png"):
        basename += ".png"
    fallback = f"assets/{namespace}/textures/halo/{basename}"
    if fallback not in candidates:
        candidates.append(fallback)
    return list(dict.fromkeys(candidates))


def _project_files(project: PackProject | Any) -> MutableMapping[str, bytes]:
    files = getattr(project, "files", None)
    if isinstance(files, MutableMapping):
        return files
    if isinstance(files, Mapping):
        replacement = dict(files)
        try:
            setattr(project, "files", replacement)
        except Exception:
            pass
        return replacement
    replacement = {}
    try:
        setattr(project, "files", replacement)
    except Exception as exc:
        raise PackIOError("Project object must expose a mutable files mapping") from exc
    return replacement


def resolve_texture_reference(
    project: PackProject | Any,
    identifier: str,
    *,
    default_namespace: str | None = None,
) -> TextureResolution:
    """Resolve a texture id to a preserved pack-relative path.

    Both explicit ``namespace:path`` IDs and extensionless IDs are accepted.
    Missing base textures are reported without raising so the viewport can show
    a placeholder and the validator can produce a useful warning.
    """

    try:
        namespace, path, resource_id = _parse_texture_id(identifier, default_namespace=default_namespace)
    except PackIOError as exc:
        return TextureResolution(str(identifier), None, None, False, message=str(exc))
    files = _project_files(project)
    candidates = _candidate_texture_paths(namespace, path)
    found_path = next((candidate for candidate in candidates if candidate in files), None)
    variants: dict[str, str] = {}
    if found_path:
        pure = PurePosixPath(found_path)
        stem = pure.stem
        for suffix in PBR_SUFFIXES:
            variant = pure.with_name(stem + suffix + pure.suffix).as_posix()
            if variant in files:
                variants[suffix] = variant
    return TextureResolution(
        identifier=str(identifier),
        resource_id=resource_id,
        pack_path=found_path,
        found=found_path is not None,
        candidates=tuple(candidates),
        variants=variants,
        message=None if found_path else f"Texture not found; checked {', '.join(candidates)}",
    )


def resolve_texture_path(
    project: PackProject | Any,
    identifier: str,
    *,
    default_namespace: str | None = None,
) -> str | None:
    return resolve_texture_reference(project, identifier, default_namespace=default_namespace).pack_path


def diagnose_missing_textures(project: PackProject | Any, *, append: bool = True) -> list[Diagnostic]:
    """Return diagnostics for malformed or missing texture references."""

    result: list[Diagnostic] = []
    definitions = getattr(project, "definitions", getattr(project, "halos", [])) or []
    if isinstance(definitions, Mapping):
        definitions = definitions.values()
    for definition in definitions:
        ast = getattr(getattr(definition, "document", None), "data", None)
        if ast is None:
            ast = getattr(definition, "ast", getattr(definition, "raw_json", definition))
        path = getattr(definition, "source_path", getattr(definition, "path", None))
        default_namespace = getattr(definition, "namespace", None)
        for reference in iter_texture_references(ast, definition_path=path):
            if not isinstance(reference.identifier, str):
                diagnostic = Diagnostic("warning", "invalid_texture_reference", "Texture field must be a string", path=path)
                result.append(diagnostic)
                continue
            resolution = resolve_texture_reference(project, reference.identifier, default_namespace=default_namespace)
            if not resolution.found:
                code = "invalid_texture_reference" if resolution.resource_id is None else "missing_texture"
                result.append(
                    Diagnostic(
                        "warning",
                        code,
                        resolution.message or "Texture is missing",
                        path=path,
                        identifier=reference.identifier,
                        details={"key": reference.key, "candidates": resolution.candidates},
                    )
                )
    if append:
        existing = getattr(project, "diagnostics", None)
        if isinstance(existing, list):
            existing.extend(d for d in result if d not in existing)
    return result


def validate_pack(project: PackProject | Any) -> list[Diagnostic]:
    """Validate definitions, metadata, hierarchy-independent references, and IDs."""

    diagnostics = list(getattr(project, "diagnostics", []) or [])
    # Avoid repeating import-time missing-texture diagnostics.
    for diagnostic in diagnose_missing_textures(project, append=False):
        if diagnostic not in diagnostics:
            diagnostics.append(diagnostic)
    definitions = getattr(project, "definitions", getattr(project, "halos", [])) or []
    if isinstance(definitions, Mapping):
        definitions = list(definitions.values())
    ids: dict[str, str] = {}
    for definition in definitions:
        path = getattr(definition, "source_path", getattr(definition, "path", None))
        identifier = getattr(definition, "identifier", getattr(definition, "id", None))
        if not _valid_identifier(identifier):
            diagnostic = Diagnostic("warning", "invalid_id", "Definition id must match namespace:path", path=path, identifier=identifier)
            if diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
        elif identifier in ids:
            diagnostic = Diagnostic(
                "warning",
                "duplicate_definition_id",
                f"Definition id also appears in {ids[identifier]}",
                path=path,
                identifier=identifier,
            )
            if diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
        else:
            ids[identifier] = path or ""
    return diagnostics


def _read_external_source(source: str | Path | bytes, filename: str | None) -> tuple[bytes, str, str | None]:
    if isinstance(source, bytes):
        if not filename:
            raise PackIOError("filename is required when importing PNG bytes")
        return source, filename, None
    path = Path(source)
    try:
        return path.read_bytes(), filename or path.name, str(path)
    except OSError as exc:
        raise PackIOError(f"Unable to read external PNG {path}: {exc}") from exc


def _safe_texture_filename(filename: str) -> str:
    name = str(filename).replace("\\", "/")
    if "/" in name or name in {"", ".", ".."}:
        raise PackIOError(f"Texture filename must be a simple file name: {filename!r}")
    if not name.lower().endswith(".png"):
        name += ".png"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", name, re.IGNORECASE):
        raise PackIOError(f"Invalid PNG filename: {filename!r}")
    return name.lower()


def _source_texture_variants(source_path: str | None, filename: str, base_stem: str) -> dict[str, tuple[bytes, str]]:
    """Collect sibling base/PBR files when importing a filesystem PNG."""

    result: dict[str, tuple[bytes, str]] = {}
    if source_path is None:
        return result
    source = Path(source_path)
    directory = source.parent
    for suffix in ("",) + PBR_SUFFIXES:
        candidate = directory / f"{base_stem}{suffix}.png"
        if not candidate.is_file():
            continue
        try:
            result[suffix] = (candidate.read_bytes(), candidate.name)
        except OSError:
            continue
    # A caller may have picked foo_n.png.  Include that picked file even when
    # there is no foo.png sibling.
    if not result:
        try:
            result["_selected"] = (source.read_bytes(), filename)
        except OSError:
            pass
    return result


def import_external_png(
    project: PackProject | Any,
    source: str | Path | bytes,
    *,
    namespace: str,
    filename: str | None = None,
    overwrite: bool = False,
) -> TextureImportResult:
    """Copy a PNG and adjacent labPBR variants into a project.

    Existing files are never overwritten unless ``overwrite=True``.  On a name
    collision the complete base/PBR family receives a ``_1``, ``_2`` suffix so
    the variants stay paired.
    """

    if not NAMESPACE_RE.fullmatch(namespace or ""):
        raise PackIOError(f"Invalid namespace: {namespace!r}")
    data, raw_filename, source_path = _read_external_source(source, filename)
    safe_name = _safe_texture_filename(raw_filename)
    filename_stem = PurePosixPath(safe_name).stem
    base_stem = filename_stem
    for suffix in PBR_SUFFIXES:
        if base_stem.endswith(suffix):
            base_stem = base_stem[: -len(suffix)]
            break
    family = _source_texture_variants(source_path, safe_name, base_stem)
    if not family:
        selected_suffix = next((suffix for suffix in PBR_SUFFIXES if filename_stem.endswith(suffix)), "")
        family = {selected_suffix: (data, safe_name)}
    # Ensure the explicitly supplied bytes win for the selected source file.
    selected_suffix = next((suffix for suffix in ("",) + PBR_SUFFIXES if safe_name == f"{base_stem}{suffix}.png"), None)
    if selected_suffix is None:
        family["_selected"] = (data, safe_name)
    else:
        family[selected_suffix] = (data, safe_name)

    files = _project_files(project)
    candidate_stem = base_stem
    counter = 0
    while True:
        candidate_base = f"assets/{namespace}/textures/halo/{candidate_stem}.png"
        occupied = any(
            f"assets/{namespace}/textures/halo/{candidate_stem}{suffix}.png" in files
            for suffix in ("",) + PBR_SUFFIXES
            if suffix in family or suffix == selected_suffix
        )
        if overwrite or not occupied:
            break
        counter += 1
        candidate_stem = f"{base_stem}_{counter}"

    copied: list[str] = []
    for suffix, (payload, original_name) in family.items():
        if suffix == "_selected":
            # Non-standard selected filename; preserve its suffix while still
            # keeping it in the same family directory.
            target_name = f"{candidate_stem}_{PurePosixPath(original_name).stem}.png"
        else:
            target_name = f"{candidate_stem}{suffix}.png"
        target = f"assets/{namespace}/textures/halo/{target_name}"
        files[target] = bytes(payload)
        copied.append(target)
    # Return the base identifier whenever a base was copied; otherwise return
    # the selected file's identifier.
    result_name = f"{candidate_stem}.png" if "" in family else PurePosixPath(copied[0]).name
    result = TextureImportResult(
        identifier=f"{namespace}:textures/halo/{result_name}",
        path=f"assets/{namespace}/textures/halo/{result_name}",
        copied=tuple(copied),
        source=source_path,
    )
    imports = getattr(project, "texture_imports", None)
    if isinstance(imports, list):
        imports.append(result)
    return result


def import_png(
    project: PackProject | Any,
    source: str | Path | bytes,
    *,
    namespace: str,
    filename: str | None = None,
    overwrite: bool = False,
) -> TextureImportResult:
    return import_external_png(project, source, namespace=namespace, filename=filename, overwrite=overwrite)


def _definition_list(project: Any) -> list[Any]:
    definitions = getattr(project, "definitions", getattr(project, "halos", [])) or []
    if isinstance(definitions, Mapping):
        return list(definitions.values())
    return list(definitions)


def _definition_ast(definition: Any) -> Any:
    document = getattr(definition, "document", None)
    if document is not None and hasattr(document, "data"):
        return clone_ast(document.data)
    for name in ("ast", "raw_json", "data"):
        value = getattr(definition, name, None)
        if value is not None:
            return clone_ast(value)
    for name in ("to_json", "to_dict", "as_dict"):
        method = getattr(definition, name, None)
        if callable(method):
            return clone_ast(method())
    if isinstance(definition, Mapping):
        return clone_ast(definition)
    raise PackIOError(f"Definition {definition!r} has no JSON AST")


def _definition_source_path(definition: Any) -> str | None:
    return getattr(definition, "source_path", getattr(definition, "path", getattr(definition, "json_path", None)))


def _definition_output_path(definition: Any) -> str:
    explicit = getattr(definition, "export_path", None)
    if isinstance(explicit, str):
        return _canonical_path(explicit)
    identifier = getattr(definition, "identifier", getattr(definition, "id", None))
    if isinstance(identifier, str) and _valid_identifier(identifier):
        namespace, _name = identifier.split(":", 1)
        return _canonical_path(f"assets/{namespace}/halo_definitions/{_definition_filename(identifier)}")
    source = _definition_source_path(definition)
    if source:
        namespace = getattr(definition, "namespace", _path_namespace(source))
        return _canonical_path(f"assets/{namespace}/halo_definitions/{PurePosixPath(source).name}")
    raise PackIOError("Definition needs source_path or a valid id before export")


def _export_entries(project: PackProject | Any) -> dict[str, bytes]:
    source_files = _project_files(project)
    entries: dict[str, bytes] = {}
    for raw_path, value in source_files.items():
        path = _canonical_path(raw_path)
        # Definition JSONs are regenerated below.  A malformed file which was
        # not parsed remains untouched because it has no DefinitionAsset.
        if path == "pack.mcmeta" or path in {_definition_source_path(d) for d in _definition_list(project)}:
            continue
        entries[path] = bytes(value)

    definitions = _definition_list(project)
    output_paths: dict[str, Any] = {}
    for definition in definitions:
        output_path = _definition_output_path(definition)
        previous = output_paths.get(output_path)
        if previous is not None:
            previous_id = getattr(previous, "identifier", getattr(previous, "id", "<unknown>"))
            current_id = getattr(definition, "identifier", getattr(definition, "id", "<unknown>"))
            raise PackIOError(
                f"Definition filename collision at {output_path}: {previous_id}, {current_id}; rename a definition id"
            )
        output_paths[output_path] = definition

    used_paths = set(entries)
    definition_records: list[tuple[str, Any]] = []
    for definition in definitions:
        data = _definition_ast(definition)
        try:
            output_path = _definition_output_path(definition)
        except PackIOError:
            source = _definition_source_path(definition)
            if not source:
                raise
            output_path = _canonical_path(source)
        if output_path in used_paths and output_path != _definition_source_path(definition):
            raise PackIOError(
                f"Definition output collides with an existing pack file: {output_path}; rename the definition id"
            )
        entries[output_path] = dumps(data, newline=True).encode("utf-8")
        used_paths.add(output_path)
        definition_records.append((output_path, data))

    # Repair legacy projects which retained upper-case source filenames.  The
    # cloned definition ASTs and copied export entries are migrated together;
    # the in-memory editing project is intentionally left untouched.
    normalize_resource_entries(entries, (data for _path, data in definition_records))
    for output_path, data in definition_records:
        entries[output_path] = dumps(data, newline=True).encode("utf-8")

    # Metadata is editable but unknown keys in it are retained through the AST.
    metadata = getattr(project, "pack_mcmeta", None)
    metadata_raw = getattr(project, "pack_mcmeta_raw", None)
    metadata_document = getattr(project, "pack_mcmeta_document", None)
    if metadata is not None:
        entries["pack.mcmeta"] = dumps(_complete_pack_mcmeta(metadata), newline=True).encode("utf-8")
    elif metadata_raw is not None:
        entries["pack.mcmeta"] = bytes(metadata_raw)
    elif metadata_document is not None and hasattr(metadata_document, "data"):
        entries["pack.mcmeta"] = dumps(_complete_pack_mcmeta(metadata_document.data), newline=True).encode("utf-8")
    else:
        entries["pack.mcmeta"] = dumps(_complete_pack_mcmeta(None), newline=True).encode("utf-8")
    return entries


def _assert_export_target(target: Path, source_path: str | None, *, overwrite: bool, kind: str) -> None:
    if source_path:
        try:
            if target.resolve() == Path(source_path).resolve():
                raise PackIOError("Refusing to export over the imported source pack; choose a different target")
        except OSError:
            pass
    if target.exists() and not overwrite:
        raise FileExistsError(f"Export target already exists: {target}")
    if kind == "folder" and target.exists() and not target.is_dir():
        raise PackIOError(f"Folder export target is a file: {target}")
    if kind == "zip" and target.exists() and target.is_dir():
        raise PackIOError(f"ZIP export target is a directory: {target}")


def _write_entries_to_folder(entries: Mapping[str, bytes], root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve()
    for path, payload in sorted(entries.items()):
        target = root / Path(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.resolve().relative_to(root_resolved)
        except ValueError as exc:
            raise UnsafeArchiveError(f"Export path escapes folder: {path}") from exc
        target.write_bytes(bytes(payload))


def _atomic_folder_swap(staging: Path, target: Path, *, overwrite: bool) -> None:
    if not overwrite:
        os.replace(staging, target)
        return
    backup = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
    moved_old = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
    except Exception:
        if moved_old and not target.exists():
            try:
                os.replace(backup, target)
            except OSError:
                pass
        raise
    finally:
        if moved_old and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def export_folder(
    project: PackProject | Any,
    target: str | Path,
    *,
    overwrite: bool = False,
    atomic: bool = True,
) -> Path:
    """Export a project to a folder, preserving all unknown files."""

    destination = Path(target)
    source_path = getattr(project, "source_path", None)
    _assert_export_target(destination, source_path, overwrite=overwrite, kind="folder")
    entries = _export_entries(project)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not atomic:
        if destination.exists() and overwrite:
            # Clear only the explicitly requested destination.  Source packs are
            # rejected above, and callers opting into overwrite own this path.
            for child in destination.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        _write_entries_to_folder(entries, destination)
        return destination
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=str(destination.parent)))
    try:
        _write_entries_to_folder(entries, staging)
        _atomic_folder_swap(staging, destination, overwrite=overwrite)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def export_zip(
    project: PackProject | Any,
    target: str | Path,
    *,
    overwrite: bool = False,
    atomic: bool = True,
) -> Path:
    """Export a project to a ZIP archive with an atomic replacement."""

    destination = Path(target)
    source_path = getattr(project, "source_path", None)
    _assert_export_target(destination, source_path, overwrite=overwrite, kind="zip")
    entries = _export_entries(project)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if atomic:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.tmp-", suffix=".zip", dir=str(destination.parent))
        os.close(fd)
        temporary = Path(temporary_name)
    else:
        temporary = destination
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, payload in sorted(entries.items()):
                info = zipfile.ZipInfo(path)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, bytes(payload))
        if atomic:
            os.replace(temporary, destination)
    except Exception:
        if atomic and temporary.exists():
            temporary.unlink(missing_ok=True)
        raise
    return destination


def write_folder(project: PackProject | Any, target: str | Path, *, overwrite: bool = False, atomic: bool = True) -> Path:
    return export_folder(project, target, overwrite=overwrite, atomic=atomic)


def write_zip(project: PackProject | Any, target: str | Path, *, overwrite: bool = False, atomic: bool = True) -> Path:
    return export_zip(project, target, overwrite=overwrite, atomic=atomic)


def export_pack(project: PackProject | Any, target: str | Path, *, overwrite: bool = False, atomic: bool = True) -> Path:
    destination = Path(target)
    if destination.suffix.lower() == ".zip":
        return export_zip(project, destination, overwrite=overwrite, atomic=atomic)
    return export_folder(project, destination, overwrite=overwrite, atomic=atomic)


__all__ = [
    "PACK_FORMAT",
    "MIN_PACK_FORMAT",
    "MAX_PACK_FORMAT",
    "SUPPORTED_PACK_FORMATS",
    "SCHEMA_VERSION",
    "PBR_SUFFIXES",
    "PackIOError",
    "UnsafeArchiveError",
    "DuplicateArchiveEntryError",
    "Diagnostic",
    "TextureReference",
    "TextureResolution",
    "TextureImportResult",
    "DefinitionAsset",
    "PackProject",
    "new_project",
    "safe_extract_zip",
    "discover_definition_paths",
    "parse_definition",
    "definition_to_dict",
    "dumps_definition",
    "import_pack",
    "load_pack",
    "read_pack",
    "iter_texture_references",
    "texture_references",
    "resolve_texture_reference",
    "resolve_texture_path",
    "diagnose_missing_textures",
    "validate_pack",
    "import_external_png",
    "import_png",
    "export_folder",
    "export_zip",
    "export_pack",
    "write_folder",
    "write_zip",
]
