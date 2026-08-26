"""JSON helpers used by the Halo pack editor.

The Minecraft resource-pack format is deliberately extensible.  Keeping only a
typed Python representation of a definition is therefore dangerous: fields
which are not understood by the editor (and, in particular, old ``shape``
forms) would disappear on the next export.  This module keeps a normal Python
JSON tree as the source of truth and offers small helpers for making typed
views or updating that tree.

There are no Blender imports in this module.  It can be used by command line
tests, by the Blender add-on, and by external pack tooling alike.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, is_dataclass, asdict
import copy
import json
from pathlib import Path
from typing import Any, Callable, IO, TypeVar


JSONValue = Any
T = TypeVar("T")


class JsonCodecError(ValueError):
    """Raised when a JSON resource cannot be decoded or encoded safely."""


@dataclass
class JsonDocument:
    """A JSON AST together with its source information.

    ``data`` is intentionally not converted into a schema-specific object.
    Python's ``dict`` preserves insertion order, so all unknown keys and their
    relative order survive a load/edit/export cycle.  ``original_text`` is
    retained for diagnostics and for callers which need to tell a clean
    document from an edited one; exports always use the deterministic two-space
    encoder below.
    """

    data: JSONValue
    source_path: str | None = None
    original_text: str | None = None
    encoding: str = "utf-8"
    dirty: bool = False

    @property
    def ast(self) -> JSONValue:
        """Alias used by the editor and by older callers."""

        return self.data

    @property
    def root(self) -> JSONValue:
        return self.data

    def clone(self) -> "JsonDocument":
        return JsonDocument(
            clone_ast(self.data),
            source_path=self.source_path,
            original_text=self.original_text,
            encoding=self.encoding,
            dirty=self.dirty,
        )

    def replace(self, value: JSONValue, *, preserve_unknown: bool = False) -> JSONValue:
        """Replace the AST, optionally merging keys from the current AST.

        The returned value is the new AST.  ``preserve_unknown=True`` is useful
        when a model object only serializes fields known by the current add-on;
        keys absent from that model remain in the original document.
        """

        self.data = merge_ast(self.data, value) if preserve_unknown else clone_ast(value)
        self.dirty = True
        return self.data

    def set(self, path: str | Sequence[str | int], value: JSONValue) -> JSONValue:
        """Set a value at a dotted/list path and mark the document dirty."""

        self.data = set_path(self.data, path, value)
        self.dirty = True
        return self.data

    def delete(self, path: str | Sequence[str | int]) -> JSONValue:
        """Delete a value at a dotted/list path and mark the document dirty."""

        self.data = delete_path(self.data, path)
        self.dirty = True
        return self.data

    def dumps(self, *, newline: bool = False) -> str:
        return dumps(self.data, newline=newline)

    def to_bytes(self, *, newline: bool = True) -> bytes:
        return self.dumps(newline=newline).encode("utf-8")


def clone_ast(value: T) -> T:
    """Deep-copy a JSON tree without sharing mutable dictionaries/lists."""

    return copy.deepcopy(value)


def _decode_text(text: str | bytes, *, source_path: str | None = None) -> tuple[str, str]:
    if isinstance(text, bytes):
        # JSON files are UTF-8 in Minecraft packs.  utf-8-sig accepts a BOM and
        # otherwise behaves exactly like utf-8.
        try:
            return text.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError as exc:
            raise JsonCodecError(f"Invalid UTF-8 JSON{_where(source_path)}: {exc}") from exc
    if not isinstance(text, str):
        raise TypeError("JSON source must be str or bytes")
    return text.lstrip("\ufeff"), "utf-8"


def _where(source_path: str | Path | None) -> str:
    return f" in {source_path}" if source_path else ""


def parse_json(
    source: str | bytes | Path | IO[str] | IO[bytes],
    *,
    source_path: str | Path | None = None,
) -> JsonDocument:
    """Parse JSON into a :class:`JsonDocument`.

    ``source`` may be text, bytes, a filesystem path, or an open text/binary
    stream.  Decode failures and malformed JSON are reported as
    :class:`JsonCodecError` with the source path included where available.
    """

    path_hint = str(source_path) if source_path is not None else None
    if isinstance(source, Path):
        path_hint = str(source)
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise JsonCodecError(f"Unable to read JSON{_where(path_hint)}: {exc}") from exc
    elif isinstance(source, (str, bytes)):
        raw = source
    elif hasattr(source, "read"):
        try:
            raw = source.read()
        except OSError as exc:
            raise JsonCodecError(f"Unable to read JSON{_where(path_hint)}: {exc}") from exc
    else:
        raise TypeError("JSON source must be str, bytes, Path, or a readable stream")

    text, encoding = _decode_text(raw, source_path=path_hint)
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise JsonCodecError(f"Malformed JSON{_where(path_hint)}: {exc}") from exc
    return JsonDocument(value, source_path=path_hint, original_text=text, encoding=encoding)


def load_document(path: str | Path, *, encoding: str = "utf-8") -> JsonDocument:
    """Read a JSON file into a document.

    ``encoding`` is accepted for callers dealing with a legacy file.  UTF-8
    remains the export encoding and a BOM is accepted transparently.
    """

    p = Path(path)
    try:
        raw = p.read_bytes() if encoding.lower().replace("-", "") in {"utf8", "utf8sig"} else p.read_text(encoding=encoding)
    except OSError as exc:
        raise JsonCodecError(f"Unable to read JSON in {p}: {exc}") from exc
    return parse_json(raw, source_path=p)


def loads_document(text: str | bytes, *, source_path: str | Path | None = None) -> JsonDocument:
    return parse_json(text, source_path=source_path)


def loads(text: str | bytes, *, source_path: str | Path | None = None) -> JSONValue:
    """Compatibility wrapper matching :func:`json.loads` semantics.

    Use :func:`parse_json`/ :func:`loads_document` when source metadata is
    needed.  Returning the plain AST makes this helper convenient for tests and
    for existing callers of Python's ``json`` module.
    """

    return parse_json(text, source_path=source_path).data


def _json_default(value: Any) -> Any:
    """Convert common model/dataclass values to JSON-compatible values."""

    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return list(value)
    # Optional values and small model classes often expose to_json/to_dict.
    for name in ("to_json", "to_dict", "as_dict"):
        method = getattr(value, name, None)
        if callable(method):
            return method()
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {str(k): v for k, v in vars(value).items() if not k.startswith("_")}
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dumps(
    value: JSONValue,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    newline: bool = False,
) -> str:
    """Serialize a JSON AST using the pack editor's deterministic formatting.

    The encoder deliberately does not sort keys: preserving source key order
    makes diffs pleasant and is part of the editor's AST-preservation promise.
    ``allow_nan=False`` prevents writing values which Minecraft's JSON parser
    cannot consume.
    """

    try:
        text = json.dumps(
            value,
            indent=indent,
            ensure_ascii=ensure_ascii,
            allow_nan=False,
            default=_json_default,
        )
    except (TypeError, ValueError) as exc:
        raise JsonCodecError(f"Unable to encode JSON: {exc}") from exc
    return text + ("\n" if newline else "")


def dump_document(
    document: JsonDocument,
    target: str | Path | IO[str] | IO[bytes],
    *,
    newline: bool = True,
) -> None:
    """Write a document as UTF-8 JSON to a path or an open stream."""

    raw = dumps(document.data, newline=newline).encode("utf-8")
    if isinstance(target, (str, Path)):
        p = Path(target)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_bytes(raw)
        except OSError as exc:
            raise JsonCodecError(f"Unable to write JSON to {p}: {exc}") from exc
        document.source_path = str(p)
        document.dirty = False
        document.original_text = raw.decode("utf-8")
        return
    try:
        # Binary streams are preferable for guaranteed UTF-8; text streams
        # still work for Blender's Text datablock and normal StringIO objects.
        try:
            target.write(raw)  # type: ignore[arg-type]
        except TypeError:
            target.write(raw.decode("utf-8"))  # type: ignore[arg-type]
    except (OSError, TypeError, ValueError) as exc:
        raise JsonCodecError(f"Unable to write JSON stream: {exc}") from exc


def dump_json(value: JSONValue, target: str | Path | IO[str] | IO[bytes], *, newline: bool = True) -> None:
    dump_document(JsonDocument(value), target, newline=newline)


def merge_ast(original: JSONValue, edited: JSONValue) -> JSONValue:
    """Merge an edited typed view into an original AST without losing keys.

    Dictionaries are merged recursively; arrays and scalar values are replaced
    as a unit.  To intentionally delete a key, use :func:`delete_path` on the
    document (a generic merge cannot infer whether an omitted key was deleted
    or merely unsupported by the typed view).
    """

    if isinstance(original, Mapping) and isinstance(edited, Mapping):
        result = clone_ast(dict(original))
        for key, value in edited.items():
            if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
                result[key] = merge_ast(result[key], value)
            else:
                result[key] = clone_ast(value)
        return result
    return clone_ast(edited)


def semantic_equal(left: JSONValue, right: JSONValue) -> bool:
    """Return whether two values have the same JSON semantics."""

    return left == right


def _path_parts(path: str | Sequence[str | int]) -> list[str | int]:
    if isinstance(path, str):
        # Dotted paths are convenient for animation channels.  A literal list
        # index may be written as ``layers.0.position``.
        if not path:
            return []
        parts: list[str | int] = []
        for piece in path.split("."):
            try:
                parts.append(int(piece))
            except ValueError:
                parts.append(piece)
        return parts
    return list(path)


def set_path(root: JSONValue, path: str | Sequence[str | int], value: JSONValue) -> JSONValue:
    """Return a copy of ``root`` with ``path`` set to ``value``.

    Missing dictionaries are created.  List indices must already exist; this
    catches accidental edits to a wrong animation channel instead of silently
    creating an invalid array shape.
    """

    parts = _path_parts(path)
    if not parts:
        return clone_ast(value)
    result = clone_ast(root)
    cursor: Any = result
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(cursor, MutableMapping):
            if part not in cursor or cursor[part] is None:
                cursor[part] = [] if isinstance(next_part, int) else {}
            cursor = cursor[part]
        elif isinstance(cursor, list) and isinstance(part, int):
            if part < 0 or part >= len(cursor):
                raise IndexError(f"JSON list index out of range: {part}")
            cursor = cursor[part]
        else:
            raise TypeError(f"Cannot descend into JSON value at {part!r}")
    last = parts[-1]
    if isinstance(cursor, MutableMapping) and isinstance(last, str):
        cursor[last] = clone_ast(value)
    elif isinstance(cursor, list) and isinstance(last, int):
        if last < 0 or last >= len(cursor):
            raise IndexError(f"JSON list index out of range: {last}")
        cursor[last] = clone_ast(value)
    else:
        raise TypeError(f"Cannot set JSON value at {last!r}")
    return result


def delete_path(root: JSONValue, path: str | Sequence[str | int]) -> JSONValue:
    """Return a copy of ``root`` with a key/index removed.

    Deleting a missing key is idempotent.  Deleting a list index requires a
    valid index, matching normal Python list behavior.
    """

    parts = _path_parts(path)
    if not parts:
        return None
    result = clone_ast(root)
    cursor: Any = result
    for part in parts[:-1]:
        if isinstance(cursor, Mapping) and isinstance(part, str):
            if part not in cursor:
                return result
            cursor = cursor[part]
        elif isinstance(cursor, list) and isinstance(part, int):
            if part < 0 or part >= len(cursor):
                return result
            cursor = cursor[part]
        else:
            return result
    last = parts[-1]
    if isinstance(cursor, MutableMapping) and isinstance(last, str):
        cursor.pop(last, None)
    elif isinstance(cursor, list) and isinstance(last, int):
        if -len(cursor) <= last < len(cursor):
            cursor.pop(last)
    return result


def decode_typed(
    value: JSONValue,
    model_factory: Callable[[JSONValue], T] | None = None,
) -> T | JSONValue:
    """Decode a preserved AST through an optional model factory.

    The optional factory keeps this core independent of ``models.py`` while
    allowing the Blender layer to opt into its typed classes.  If no factory is
    supplied, a deep-copied plain AST is returned.
    """

    return model_factory(clone_ast(value)) if model_factory else clone_ast(value)


def encode_typed(value: Any, *, original: JSONValue | None = None, preserve_unknown: bool = True) -> JSONValue:
    """Encode a typed model and optionally merge it into an original AST."""

    if isinstance(value, JsonDocument):
        encoded = clone_ast(value.data)
    elif isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        encoded = clone_ast(value)
    else:
        encoded = _json_default(value)
    return merge_ast(original, encoded) if original is not None and preserve_unknown else encoded


# Friendly aliases used by importer code and by third-party scripts.
parse = parse_json
load = load_document
dump = dump_json
serialize = dumps
deserialize = loads


__all__ = [
    "JSONValue",
    "JsonCodecError",
    "JsonDocument",
    "clone_ast",
    "parse_json",
    "parse",
    "load_document",
    "load",
    "loads_document",
    "loads",
    "dumps",
    "serialize",
    "deserialize",
    "dump_document",
    "dump_json",
    "dump",
    "merge_ast",
    "semantic_equal",
    "set_path",
    "delete_path",
    "decode_typed",
    "encode_typed",
]
