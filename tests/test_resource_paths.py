from pathlib import Path
import tempfile
import unittest

from halo_pack_editor.core.resource_paths import (
    lowercase_resource_identifier,
    normalize_resource_entries,
    normalize_staged_resources,
)


def mesh_document(texture="threed:textures/halo/CH0069_Halo.png", mask="threed:textures/halo/FX_TEX_Trail_01.png"):
    return {
        "id": "threed:mika",
        "layers": [{
            "id": "mesh",
            "primitives": [{
                "type": "mesh",
                "model": "threed:models/halo/FX_Halo_Trail_01.OBJ",
                "texture": texture,
                "material": {"effects": [{"type": "alpha_mask", "texture": mask}]},
            }],
        }],
    }


class ResourcePathTests(unittest.TestCase):
    def test_identifier_lowercases_namespace_and_path(self):
        self.assertEqual(
            lowercase_resource_identifier("ThreeD:Textures/Halo/CH0069_Halo.PNG"),
            "threed:textures/halo/ch0069_halo.png",
        )

    def test_mapping_migrates_mesh_texture_mask_and_sidecars(self):
        entries = {
            "assets/threed/models/halo/FX_Halo_Trail_01.OBJ": b"obj",
            "assets/threed/textures/halo/CH0069_Halo.png": b"base",
            "assets/threed/textures/halo/CH0069_Halo_n.png": b"normal",
            "assets/threed/textures/halo/FX_TEX_Trail_01.png": b"mask",
        }
        document = mesh_document()
        changes = normalize_resource_entries(entries, [document])
        primitive = document["layers"][0]["primitives"][0]
        self.assertEqual(primitive["model"], "threed:models/halo/fx_halo_trail_01.obj")
        self.assertEqual(primitive["texture"], "threed:textures/halo/ch0069_halo.png")
        self.assertEqual(
            primitive["material"]["effects"][0]["texture"],
            "threed:textures/halo/fx_tex_trail_01.png",
        )
        self.assertIn("assets/threed/models/halo/fx_halo_trail_01.obj", entries)
        self.assertIn("assets/threed/textures/halo/ch0069_halo.png", entries)
        self.assertIn("assets/threed/textures/halo/ch0069_halo_n.png", entries)
        self.assertEqual(len(changes), 3)

    def test_different_lowercase_collision_uses_numeric_suffix(self):
        entries = {
            "assets/demo/textures/halo/Foo.png": b"upper",
            "assets/demo/textures/halo/foo.png": b"lower",
        }
        document = {"texture": "demo:textures/halo/Foo.png"}
        normalize_resource_entries(entries, [document])
        self.assertEqual(document["texture"], "demo:textures/halo/foo_1.png")
        self.assertEqual(entries["assets/demo/textures/halo/foo.png"], b"lower")
        self.assertEqual(entries["assets/demo/textures/halo/foo_1.png"], b"upper")

    def test_identical_lowercase_collision_is_reused(self):
        entries = {
            "assets/demo/textures/halo/Foo.png": b"same",
            "assets/demo/textures/halo/foo.png": b"same",
        }
        document = {"texture": "demo:textures/halo/Foo.png"}
        normalize_resource_entries(entries, [document])
        self.assertEqual(document["texture"], "demo:textures/halo/foo.png")
        self.assertNotIn("assets/demo/textures/halo/Foo.png", entries)
        self.assertEqual(entries["assets/demo/textures/halo/foo.png"], b"same")

    def test_staged_case_only_rename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "assets/threed/textures/halo/CH0069_Halo.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"base")
            document = {"texture": "threed:textures/halo/CH0069_Halo.png"}
            normalize_staged_resources(root, [document])
            self.assertEqual(document["texture"], "threed:textures/halo/ch0069_halo.png")
            self.assertTrue((root / "assets/threed/textures/halo/ch0069_halo.png").is_file())

    def test_lowercase_reference_also_repairs_uppercase_member(self):
        entries = {"assets/demo/textures/halo/Halo.PNG": b"base"}
        document = {"texture": "demo:textures/halo/halo.png"}
        normalize_resource_entries(entries, [document])
        self.assertEqual(document["texture"], "demo:textures/halo/halo.png")
        self.assertEqual(entries, {"assets/demo/textures/halo/halo.png": b"base"})


if __name__ == "__main__":
    unittest.main()
