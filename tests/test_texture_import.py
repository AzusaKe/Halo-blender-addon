"""Opaque PNG-family copying/deduplication does not require Blender."""

from pathlib import Path
import tempfile
import unittest

from halo_pack_editor.materials import (
    carry_labpbr_sidecars,
    copy_texture_with_sidecars,
    import_labpbr_texture,
    labpbr_texture_id,
)


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

    def test_labpbr_maps_use_the_albedo_name_and_replace_in_place(self):
        albedo = Path(self.copy()[0])
        normal = self.root / "chosen-normal.png"
        normal.write_bytes(b"normal-one")
        Path(str(normal) + ".mcmeta").write_bytes(b'{"animation":{"frametime":2}}')
        normal_id = import_labpbr_texture(normal, self.pack, self.texture_id, "NORMAL")
        self.assertEqual(normal_id, "demo:textures/halo/source_n.png")
        self.assertEqual(albedo.with_name("source_n.png").read_bytes(), b"normal-one")
        self.assertTrue(Path(str(albedo.with_name("source_n.png")) + ".mcmeta").is_file())

        normal.write_bytes(b"normal-two")
        Path(str(normal) + ".mcmeta").unlink()
        self.assertEqual(import_labpbr_texture(normal, self.pack, self.texture_id, "NORMAL"), normal_id)
        self.assertEqual(albedo.with_name("source_n.png").read_bytes(), b"normal-two")
        self.assertFalse(Path(str(albedo.with_name("source_n.png")) + ".mcmeta").exists())

        specular = self.root / "unrelated-name.png"
        specular.write_bytes(b"specular")
        specular_id = import_labpbr_texture(specular, self.pack, self.texture_id, "SPECULAR")
        self.assertEqual(specular_id, "demo:textures/halo/source_s.png")
        self.assertEqual(albedo.with_name("source_s.png").read_bytes(), b"specular")
        reused = self.copy(preserve_existing_sidecars=True)
        self.assertEqual(Path(reused[0]), albedo)
        self.assertFalse(albedo.with_name("source_1.png").exists())

    def test_labpbr_requires_existing_albedo_and_follows_replacement(self):
        normal = self.root / "normal.png"
        normal.write_bytes(b"normal")
        with self.assertRaisesRegex(ValueError, "固有色"):
            import_labpbr_texture(normal, self.pack, self.texture_id, "NORMAL")

        self.copy()
        import_labpbr_texture(normal, self.pack, self.texture_id, "NORMAL")
        specular = self.root / "specular.png"
        specular.write_bytes(b"specular")
        import_labpbr_texture(specular, self.pack, self.texture_id, "SPECULAR")
        old_normal = self.pack / "assets/demo/textures/halo/source_n.png"
        Path(str(old_normal) + ".mcmeta").write_bytes(b"metadata")
        new_base = self.pack / "assets/demo/textures/halo/replacement.png"
        new_base.write_bytes(b"replacement")
        copied = carry_labpbr_sidecars(
            self.pack,
            self.texture_id,
            "demo:textures/halo/replacement.png",
        )
        self.assertEqual(labpbr_texture_id("demo:textures/halo/replacement", "NORMAL"),
                         "demo:textures/halo/replacement_n.png")
        self.assertTrue(new_base.with_name("replacement_n.png").is_file())
        self.assertTrue(new_base.with_name("replacement_s.png").is_file())
        self.assertTrue(Path(str(new_base.with_name("replacement_n.png")) + ".mcmeta").is_file())
        self.assertTrue(old_normal.is_file(), "old family stays available to other primitives")
        self.assertGreaterEqual(len(copied), 3)


if __name__ == "__main__":
    unittest.main()
