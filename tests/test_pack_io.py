"""Focused tests for the Blender add-on's pack I/O core.

These tests deliberately use ordinary bytes in place of valid PNG payloads;
pack I/O should preserve a resource without requiring Pillow or Blender at
test time.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from halo_pack_editor.core.json_codec import (  # noqa: E402
    JsonDocument,
    JsonCodecError,
    delete_path,
    dumps,
    merge_ast,
    parse_json,
)
from halo_pack_editor.core.pack_io import (  # noqa: E402
    DuplicateArchiveEntryError,
    PackIOError,
    PackProject,
    UnsafeArchiveError,
    export_folder,
    export_zip,
    import_external_png,
    import_pack,
    new_project,
    resolve_texture_reference,
    safe_extract_zip,
)


def definition(identifier: str, texture: str = "demo:textures/halo/a.png") -> dict:
    return {
        "id": identifier,
        "future_field": {"keep": [1, "二"]},
        "layers": [
            {
                "id": "root",
                "position": [0, 0, 0],
                "primitive": {"type": "billboard", "texture": texture, "size": [1, 1]},
            }
        ],
    }


class JsonCodecTests(unittest.TestCase):
    def test_two_space_utf8_and_unknown_key_merge(self) -> None:
        source = {"id": "demo:a", "unknown": {"message": "光环"}, "layers": [{"scale": 1}]}
        edited = {"id": "demo:a", "layers": [{"scale": 2}]}
        merged = merge_ast(source, edited)
        self.assertEqual(merged["unknown"]["message"], "光环")
        self.assertEqual(merged["layers"][0]["scale"], 2)
        text = dumps(merged, newline=True)
        self.assertIn('  "unknown"', text)
        self.assertIn("光环", text)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(json.loads(text), merged)
        self.assertEqual(parse_json(text.encode("utf-8")).data, merged)

    def test_bom_and_malformed_json(self) -> None:
        self.assertEqual(parse_json(b"\xef\xbb\xbf{\"x\": 1}").data, {"x": 1})
        with self.assertRaises(JsonCodecError):
            parse_json("{not-json")

    def test_document_path_edit_delete(self) -> None:
        document = JsonDocument({"a": {"b": [1, 2]}})
        document.set("a.b.1", 5)
        self.assertEqual(document.data["a"]["b"], [1, 5])
        document.delete("a.b.0")
        self.assertEqual(document.data["a"]["b"], [5])


class PackIOTests(unittest.TestCase):
    def make_folder(self, root: Path) -> None:
        (root / "assets/demo/halo_definitions").mkdir(parents=True)
        (root / "assets/demo/textures/halo").mkdir(parents=True)
        (root / "assets/demo/halo_definitions/a.json").write_text(
            json.dumps(definition("demo:a"), ensure_ascii=False), encoding="utf-8"
        )
        (root / "assets/demo/textures/halo/a.png").write_bytes(b"PNG")
        (root / "assets/demo/textures/halo/a_n.png").write_bytes(b"NORMAL")
        (root / "pack.mcmeta").write_text(
            json.dumps({"pack": {"pack_format": 15, "description": "测试"}, "unknown": 7}, ensure_ascii=False),
            encoding="utf-8",
        )
        (root / "pack.png").write_bytes(b"COVER")
        (root / "unknown.bin").write_bytes(b"UNKNOWN")

    def test_folder_import_discovers_definition_and_retains_unknown_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pack"
            self.make_folder(root)
            project = import_pack(root)
            self.assertEqual(len(project.definitions), 1)
            self.assertEqual(project.definitions[0].identifier, "demo:a")
            self.assertEqual(project.files["unknown.bin"], b"UNKNOWN")
            resolved = resolve_texture_reference(project, "demo:textures/halo/a.png")
            self.assertTrue(resolved.found)
            self.assertEqual(resolved.pack_path, "assets/demo/textures/halo/a.png")
            self.assertEqual(resolved.variants["_n"], "assets/demo/textures/halo/a_n.png")

    def test_zip_import_multi_namespace_and_internal_id_namespace_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "input.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("pack.mcmeta", b'{"pack":{"pack_format":15}}')
                archive.writestr("assets/pathns/halo_definitions/file.json", json.dumps(definition("realns:file")).encode())
                archive.writestr("assets/other/halo_definitions/other.json", json.dumps(definition("other:other")).encode())
                archive.writestr("custom/unknown.bin", b"x")
            project = import_pack(archive_path)
            self.assertEqual({d.identifier for d in project.definitions}, {"realns:file", "other:other"})
            mismatch = next(d for d in project.definitions if d.identifier == "realns:file")
            self.assertEqual(mismatch.export_path, "assets/realns/halo_definitions/file.json")
            self.assertTrue(any(d.code == "namespace_mismatch" for d in project.diagnostics))

    def test_zip_slip_and_duplicate_entries_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            slip = Path(tmp) / "slip.zip"
            with zipfile.ZipFile(slip, "w") as archive:
                archive.writestr("../escape.txt", b"no")
            with self.assertRaises(UnsafeArchiveError):
                import_pack(slip)

            duplicate = Path(tmp) / "duplicate.zip"
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("a.txt", b"1")
                archive.writestr("a.txt", b"2")
            with self.assertRaises(DuplicateArchiveEntryError):
                import_pack(duplicate)

    def test_atomic_folder_and_zip_export_preserve_files_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pack"
            self.make_folder(root)
            project = import_pack(root)
            project.definitions[0].document.set("layers.0.scale", 3)
            project.pack_mcmeta["custom"] = "保留"
            folder_out = Path(tmp) / "out-folder"
            zip_out = Path(tmp) / "out.zip"
            self.assertEqual(export_folder(project, folder_out), folder_out)
            self.assertEqual(export_zip(project, zip_out), zip_out)
            self.assertEqual((folder_out / "unknown.bin").read_bytes(), b"UNKNOWN")
            exported = json.loads((folder_out / "assets/demo/halo_definitions/a.json").read_text(encoding="utf-8"))
            self.assertEqual(exported["layers"][0]["scale"], 3)
            self.assertEqual(exported["future_field"], {"keep": [1, "二"]})
            with zipfile.ZipFile(zip_out) as archive:
                self.assertIn("pack.png", archive.namelist())
                self.assertIn("unknown.bin", archive.namelist())
                self.assertEqual(json.loads(archive.read("pack.mcmeta"))["custom"], "保留")

    def test_export_does_not_overwrite_source_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pack"
            self.make_folder(root)
            project = import_pack(root)
            with self.assertRaises(PackIOError):
                export_folder(project, root)

    def test_missing_texture_diagnostic_and_external_png_family_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = new_project()
            project.files["assets/demo/halo_definitions/a.json"] = json.dumps(
                definition("demo:a", "demo:textures/halo/missing.png")
            ).encode()
            # A manually-created project needs its definition list populated by
            # importing once; this also exercises the same parser used by packs.
            source = Path(tmp) / "source"
            source.mkdir()
            (source / "assets/demo/halo_definitions").mkdir(parents=True)
            (source / "assets/demo/halo_definitions/a.json").write_bytes(project.files["assets/demo/halo_definitions/a.json"])
            loaded = import_pack(source)
            self.assertTrue(any(d.code == "missing_texture" for d in loaded.missing_textures()))

            png_dir = Path(tmp) / "png"
            png_dir.mkdir()
            (png_dir / "halo.png").write_bytes(b"BASE")
            (png_dir / "halo_n.png").write_bytes(b"N")
            first = import_external_png(loaded, png_dir / "halo.png", namespace="demo")
            second = import_external_png(loaded, png_dir / "halo.png", namespace="demo")
            self.assertEqual(first.identifier, "demo:textures/halo/halo.png")
            self.assertEqual(second.identifier, "demo:textures/halo/halo_1.png")
            self.assertIn("assets/demo/textures/halo/halo_1_n.png", loaded.files)

    def test_safe_extract_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive_data = io.BytesIO()
            with zipfile.ZipFile(archive_data, "w") as archive:
                archive.writestr("assets/a.txt", b"ok")
            out = Path(tmp) / "out"
            self.assertEqual(safe_extract_zip(archive_data.getvalue(), out), ["assets/a.txt"])
            self.assertEqual((out / "assets/a.txt").read_bytes(), b"ok")


if __name__ == "__main__":
    unittest.main()
