from pathlib import Path
import tempfile
import unittest

from halo_pack_editor.core.texture_migration import migrate_texture_families, remap_texture_references


class TextureMigrationTests(unittest.TestCase):
    def setUp(self):
        parent = Path(r"F:\codex-cache\halo-blender-addon\texture-tests")
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, content=b"png"):
        target = self.root / "assets" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def test_family_and_aliases_move_together_without_deleting_shared_original(self):
        for suffix in ("", "_n", "_s", "_e"):
            self.write(f"a/textures/halo/c{suffix}.png", suffix.encode())
            self.write(f"a/textures/halo/c{suffix}.png.mcmeta", b"metadata")
        before = {p.relative_to(self.root / "assets/a"): p.read_bytes()
                  for p in (self.root / "assets/a").rglob("*") if p.is_file()}
        mapping, notices = migrate_texture_families(self.root,
            ["a:textures/halo/c.png", "a:textures/halo/c"], "d")
        self.assertEqual(set(mapping.values()), {"d:textures/halo/c.png"})
        self.assertEqual(notices, [])
        for relative, content in before.items():
            self.assertEqual((self.root / "assets/a" / relative).read_bytes(), content)
            self.assertEqual((self.root / "assets/d" / relative).read_bytes(), content)
        back, _ = migrate_texture_families(self.root, mapping.values(), "a")
        self.assertEqual(set(back.values()), {"a:textures/halo/c.png"})

    def test_conflicting_content_suffix_and_cross_namespace_sources(self):
        self.write("a/textures/c.png", b"first")
        self.write("b/textures/c.png", b"second")
        self.write("d/textures/c.png", b"keep existing")
        refs = ["a:textures/c.png", "b:textures/c.png", "d:textures/c.png"]
        mapping, notices = migrate_texture_families(self.root, refs, "d")
        self.assertEqual(mapping, {refs[0]: "d:textures/c_1.png", refs[1]: "d:textures/c_2.png"})
        self.assertEqual(len(notices), 2)
        self.assertEqual(migrate_texture_families(self.root, refs, "d")[0], mapping)
        self.assertEqual((self.root / "assets/d/textures/c.png").read_bytes(), b"keep existing")

    def test_missing_base_keeps_sidecars_and_does_not_bind_unrelated_target(self):
        self.write("a/textures/missing_n.png", b"normal")
        self.write("d/textures/missing.png", b"not the missing image")
        mapping, notices = migrate_texture_families(self.root, ["a:textures/missing.png"], "d")
        self.assertEqual(mapping["a:textures/missing.png"], "d:textures/missing_1.png")
        self.assertFalse((self.root / "assets/d/textures/missing_1.png").exists())
        self.assertEqual((self.root / "assets/d/textures/missing_1_n.png").read_bytes(), b"normal")
        self.assertEqual(len(notices), 2)

    def test_other_imported_pack_conflicts_are_not_overwritten_on_merged_export(self):
        self.write("a/textures/c.png", b"source")
        other = self.root / "other_pack"
        target = other / "assets/d/textures/c.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"other pack content")
        mapping, notices = migrate_texture_families(self.root, ["a:textures/c.png"], "d", other_pack_roots=[other])
        self.assertEqual(mapping["a:textures/c.png"], "d:textures/c_1.png")
        self.assertEqual(target.read_bytes(), b"other pack content")
        self.assertEqual(len(notices), 1)
        target.write_bytes(b"source")
        mapping, _ = migrate_texture_families(self.root, ["a:textures/c.png"], "d", other_pack_roots=[other])
        self.assertEqual((self.root / "assets/d/textures/c_1.png").read_bytes(), b"source")


    def test_implicit_minecraft_and_unknown_fields_are_preserved(self):
        self.write("minecraft/textures/c.png")
        mapping, _ = migrate_texture_families(self.root, ["textures/c"], "d")
        raw = {"id": "a:b", "shape": {"texture": "textures/c"}, "unknown": [
            {"outer_texture": "textures/c", "inner_texture": "textures/c", "note": "textures/c"}],
            "texture": 123, "allow_mixin": True}
        result = remap_texture_references(raw, mapping)
        self.assertEqual(result["shape"]["texture"], "d:textures/c.png")
        self.assertEqual(result["unknown"][0]["inner_texture"], "d:textures/c.png")
        self.assertEqual(result["unknown"][0]["note"], "textures/c")
        self.assertEqual(raw["shape"]["texture"], "textures/c")
        self.assertEqual((result["id"], result["texture"], result["allow_mixin"]), ("a:b", 123, True))

    def test_unsafe_names_and_traversal_are_rejected(self):
        for namespace in ("..", "a/b", "a:b", "", "../escape"):
            with self.assertRaises(ValueError):
                migrate_texture_families(self.root, ["a:textures/c.png"], namespace)
        with self.assertRaises(ValueError):
            migrate_texture_families(self.root, ["a:../../escape.png"], "d")


if __name__ == "__main__":
    unittest.main()
