"""Opaque PNG-family copying/deduplication does not require Blender."""

from pathlib import Path
import tempfile
import unittest

from halo_pack_editor.materials import copy_texture_with_sidecars


class TextureImportTests(unittest.TestCase):
    def setUp(self):
        cache = Path(r"F:\codex-cache\halo-blender-addon\texture-tests")
        cache.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pack = self.root / "pack"
        self.source = self.root / "source.png"
        self.source.write_bytes(b"PNG-content-one")
        self.texture_id = "demo:textures/halo/source.png"

    def copy(self, **kwargs):
        return copy_texture_with_sidecars(str(self.source), str(self.pack), self.texture_id, **kwargs)

    def test_same_png_is_reused_without_suffix(self):
        first = self.copy()
        self.assertEqual(self.copy(), first)
        self.assertEqual(len(list(self.pack.rglob("*.png"))), 1)
        self.assertEqual(Path(first[0]).name, "source.png")

    def test_uppercase_source_and_requested_id_are_written_lowercase(self):
        uppercase = self.root / "CH0069_Halo.PNG"
        self.source.rename(uppercase)
        self.source = uppercase
        self.texture_id = "Demo:Textures/Halo/CH0069_Halo.PNG"
        copied = self.copy()
        self.assertEqual(
            Path(copied[0]),
            self.pack / "assets/demo/textures/halo/ch0069_halo.png",
        )

    def test_conflicting_name_is_preserved_and_existing_suffix_reused(self):
        original = self.copy()[0]
        self.source.write_bytes(b"PNG-content-two")
        replacement = self.copy()[0]
        self.assertEqual(Path(replacement).name, "source_1.png")
        self.assertEqual(self.copy()[0], replacement)
        self.assertEqual(Path(original).read_bytes(), b"PNG-content-one")
        # Reuse existing numbered copies even when an earlier suffix is free.
        moved = Path(replacement).with_name("source_3.png")
        Path(replacement).rename(moved)
        self.assertEqual(self.copy()[0], str(moved))

    def test_bound_identical_image_under_another_name_is_reused(self):
        first = self.copy()[0]
        self.texture_id = "demo:textures/elsewhere/renamed.png"
        self.assertEqual(self.copy(reuse_candidates=[first])[0], first)
        self.assertFalse((self.pack / "assets/demo/textures/elsewhere/renamed.png").exists())

    def test_sidecars_and_metadata_are_preserved_and_compared(self):
        normal = self.source.with_name("source_n.png")
        metadata = Path(str(self.source) + ".mcmeta")
        normal.write_bytes(b"normal-one")
        metadata.write_bytes(b'{"animation":{"frametime":2}}')
        first = self.copy()
        self.assertEqual(self.copy(), first)
        self.assertEqual(len(first), 3)
        normal.write_bytes(b"normal-two")
        second = self.copy()
        self.assertEqual(Path(second[0]).name, "source_1.png")
        self.assertEqual(Path(first[-1]).read_bytes(), b"normal-one")
        metadata.write_bytes(b'{"animation":{"frametime":3}}')
        third = self.copy()
        self.assertEqual(Path(third[0]).name, "source_2.png")
        self.assertEqual(self.copy(), third)

    def test_other_namespace_or_source_is_not_reused(self):
        other = self.root / "elsewhere.png"
        other.write_bytes(self.source.read_bytes())
        namespace = self.pack / "assets/other/textures/source.png"
        namespace.parent.mkdir(parents=True)
        namespace.write_bytes(self.source.read_bytes())
        copied = self.copy(reuse_candidates=[str(other), str(namespace)])
        self.assertEqual(Path(copied[0]), self.pack / "assets/demo/textures/halo/source.png")

    def test_orphan_sidecar_is_not_overwritten_and_missing_input_is_rejected(self):
        orphan = self.pack / "assets/demo/textures/halo/source_n.png"
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"keep-this")
        copied = self.copy()
        self.assertEqual(Path(copied[0]).name, "source_1.png")
        self.assertEqual(orphan.read_bytes(), b"keep-this")
        self.source = self.root / "not-present.png"
        with self.assertRaises(FileNotFoundError):
            self.copy()


if __name__ == "__main__":
    unittest.main()
