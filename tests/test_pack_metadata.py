from __future__ import annotations

import json
import unittest

from halo_pack_editor.blender_scene import manifest_description_text, manifest_with_description


class PackMetadataTests(unittest.TestCase):
    def test_plain_description_round_trip_preserves_unknown_metadata(self):
        source = {
            "pack": {"pack_format": 15, "description": "old", "custom": True},
            "unknown": {"keep": [1, 2, 3]},
        }
        updated = manifest_with_description(source, "新的描述")
        self.assertEqual(updated["pack"]["description"], "新的描述")
        self.assertTrue(updated["pack"]["custom"])
        self.assertEqual(updated["unknown"], {"keep": [1, 2, 3]})
        self.assertEqual(source["pack"]["description"], "old")

    def test_structured_component_is_losslessly_displayed_until_edited(self):
        source = {"pack": {"description": {"text": "Halo", "color": "gold"}}}
        text, is_component = manifest_description_text(json.dumps(source))
        self.assertTrue(is_component)
        self.assertEqual(json.loads(text), source["pack"]["description"])
        updated = manifest_with_description(source, "普通文本")
        self.assertEqual(updated["pack"]["description"], "普通文本")

    def test_invalid_manifest_cannot_be_silently_destroyed(self):
        with self.assertRaises(json.JSONDecodeError):
            manifest_with_description("{broken", "description")


if __name__ == "__main__":
    unittest.main()
