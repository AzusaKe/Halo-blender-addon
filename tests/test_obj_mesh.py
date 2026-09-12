"""Parity tests for HaloCore's OBJ subset."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from halo_pack_editor.obj_mesh import copy_obj_resource, parse_obj


class ObjMeshTests(unittest.TestCase):
    def test_textured_quad_negative_indices_and_uv_flip(self):
        mesh = parse_obj("""
v -1 0 -1
v 1 0 -1
v 1 0 1
v -1 0 1
vt 0 0
vt 1 0
vt 1 1
vt 0 1
f -4/-4 -3/-3 -2/-2 -1/-1
""", "demo:models/halo/quad.obj")
        self.assertEqual(mesh.triangle_count, 2)
        self.assertEqual(mesh.triangles, ((0, 1, 2), (0, 2, 3)))
        self.assertEqual(mesh.minimum, (-1.0, 0.0, -1.0))
        self.assertEqual(mesh.maximum, (1.0, 0.0, 1.0))
        self.assertEqual(mesh.uvs[0], (0.0, 1.0))
        self.assertEqual(mesh.uvs[2], (1.0, 0.0))

    def test_rejects_missing_uv_nonplanar_quad_and_unknown_statement(self):
        common = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\n"
        with self.assertRaisesRegex(ValueError, "requires a UV"):
            parse_obj(common + "f 1 2 3\n")
        with self.assertRaises(ValueError):
            parse_obj(common + "f 1/1/ 2/2/ 3/3/\n")
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            parse_obj(common.replace("v 1 0 0", "v 1e39 0 0") + "f 1/1 2/2 3/3\n")
        nonplanar = (
            "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 1\n"
            "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nf 1/1 2/2 3/3 4/4\n"
        )
        with self.assertRaisesRegex(ValueError, "Non-planar quad"):
            parse_obj(nonplanar)
        with self.assertRaisesRegex(ValueError, "Unsupported OBJ statement"):
            parse_obj(common + "l 1 2\nf 1/1 2/2 3/3\n")

    def test_copy_obj_reuses_identical_and_suffixes_only_conflicts(self):
        triangle = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nf 1/1 2/2 3/3\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.obj"
            second = root / "second.obj"
            first.write_text(triangle, encoding="utf-8")
            second.write_text(triangle.replace("v 1 0 0", "v 2 0 0"), encoding="utf-8")
            identifier, destination = copy_obj_resource(first, root / "pack", "demo:models/halo/part.obj")
            self.assertEqual(identifier, "demo:models/halo/part.obj")
            repeated, repeated_path = copy_obj_resource(first, root / "pack", "demo:models/halo/part.obj")
            self.assertEqual((repeated, repeated_path), (identifier, destination))
            conflict, _ = copy_obj_resource(second, root / "pack", "demo:models/halo/part.obj")
            self.assertEqual(conflict, "demo:models/halo/part_1.obj")


if __name__ == "__main__":
    unittest.main()
