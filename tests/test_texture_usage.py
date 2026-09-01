"""Export filtering follows final JSON references, never old cache contents."""

import unittest

from halo_pack_editor.core.texture_usage import unused_texture_files, referenced_texture_files


class TextureUsageTests(unittest.TestCase):
    def test_renamed_definition_does_not_keep_old_namespace_resources(self):
        files = {f"assets/{ns}/textures/halo/{name}.png" for ns, name in
                 (("minecraft", "halo"), ("intermediate", "halo"), ("final", "used"))}
        document = {"id": "final:halo", "layers": [{"primitives": [{"texture": "final:textures/halo/used.png"}]}]}
        self.assertEqual(unused_texture_files(files, [document]), files - {"assets/final/textures/halo/used.png"})

    def test_nested_legacy_ring_cross_namespace_and_multiple_documents(self):
        files = {f"assets/shared/textures/halo/{name}.png" for name in ("outer", "inner", "shape", "other", "stale")}
        documents = [
            {"id": "new:halo", "layers": [{"children": [{"primitive": {
                "type": "ring", "outer_texture": "shared:textures/halo/outer.png",
                "inner_texture": "shared:textures/halo/inner.png"}}]}],
             "shape": {"texture": "shared:textures/halo/shape.png"}},
            {"id": "hidden:halo", "future": [{"texture": "shared:textures/halo/other.png"}]},
        ]
        self.assertEqual(unused_texture_files(files, documents), {"assets/shared/textures/halo/stale.png"})

    def test_sidecars_and_their_metadata_follow_base_usage(self):
        files = {f"assets/ns/textures/halo/{name}{suffix}.png{metadata}"
                 for name in ("used", "old") for suffix in ("", "_n", "_s", "_e") for metadata in ("", ".mcmeta")}
        unused = unused_texture_files(files, [{"texture": "ns:textures/halo/used.png"}])
        self.assertEqual(unused, {p for p in files if "/old" in p})
        self.assertEqual(referenced_texture_files(files, [{"texture": "ns:textures/halo/used.png"}]), files - unused)

    def test_default_namespace_extensionless_and_missing_reference(self):
        files = {"assets/minecraft/textures/halo/shared.png", "assets/def/textures/halo/shared.png",
                 "assets/minecraft/textures/halo/shared_n.png", "assets/ns/textures/halo/missing.png",
                 "assets/ns/textures/missing_n.png"}
        documents = [{"id": "def:halo", "primitive": {"texture": "textures/halo/shared"},
                      "future": {"texture": "ns:textures/missing.png"}}]
        self.assertEqual(unused_texture_files(files, documents),
                         {"assets/def/textures/halo/shared.png", "assets/ns/textures/halo/missing.png"})

    def test_unknown_files_and_icons_are_not_texture_candidates(self):
        files = {"pack.png", "pack.mcmeta", "notes.txt", "assets/ns/custom/logo.png",
                 "assets/ns/textures/readme.txt", "assets/ns/textures/custom-data.json",
                 "assets/ns/textures/unused.png", "assets/ns/textures/unused.png.mcmeta"}
        self.assertEqual(unused_texture_files(files, []),
                         {"assets/ns/textures/unused.png", "assets/ns/textures/unused.png.mcmeta"})

    def test_unsafe_and_nonstring_references_cannot_keep_unrelated_files(self):
        files = {"assets/ns/textures/used.png", "assets/elsewhere/textures/stale.png"}
        documents = [{"texture": None, "x": [{"texture": 42}, {"texture": "ns:../elsewhere/textures/stale.png"}],
                      "outer_texture": "ns:textures/used.png", "inner_texture": ""}]
        self.assertEqual(unused_texture_files(files, documents), {"assets/elsewhere/textures/stale.png"})

    def test_cleanup_does_not_drop_exact_references_due_to_case(self):
        files = {"assets/ns/textures/Image.PNG", "assets/ns/textures/unused.png"}
        self.assertEqual(unused_texture_files(files, [{"texture": "ns:textures/Image.PNG"}]),
                         {"assets/ns/textures/unused.png"})


if __name__ == "__main__":
    unittest.main()
