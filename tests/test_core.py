"""Regression tests for the dependency-free Halo core."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from halo_pack_editor.core import (
    AnimationTerm,
    BillboardPrimitive,
    HaloGroup,
    LayerAnimation,
    SchemaVersion,
    TransitionConfig,
    TransitionProperty,
    TransitionSegment,
    blender_to_mc,
    compose_blender_transform,
    definition_to_dict,
    effective_group_state,
    evaluate_definition_tree,
    load_pack,
    mc_to_blender,
    mc_to_blender_matrix,
    minecraft_transform_matrix,
    parse_definition,
    rotation_effective_end,
    validate_definition,
)
from halo_pack_editor.core.animation import evaluate_definition_transition


class CoreModelTests(unittest.TestCase):
    def test_json_ast_round_trip_preserves_unknown_fields_and_legacy_spelling(self):
        source = {
            "id": "demo:legacy",
            "custom_future_field": {"keep": [1, 2, 3]},
            "shape": {
                "type": "billboard", "texture": "demo:textures/halo/a.png", "size": [1, 2],
                "future_primitive_field": True,
            },
            "positioning": {"offset": [0, 0, 0]},
        }
        definition = parse_definition(source)
        output = definition_to_dict(definition)
        self.assertEqual(output["custom_future_field"], source["custom_future_field"])
        self.assertEqual(output["shape"]["future_primitive_field"], True)
        self.assertEqual(output["shape"]["type"], "billboard")

    def test_animation_terms_and_channels_match_java_units(self):
        anim = LayerAnimation(
            offset_y=[AnimationTerm("sin", A=0.5, omega=1.0)],
            rotation_yaw=[AnimationTerm("linear", speed=30)],
            scale_x=[AnimationTerm("sin", A=0.1, omega=1.0)],
            alpha=[AnimationTerm("linear", start=0.25, speed=0)],
            glow=[],
        )
        self.assertAlmostEqual(anim.evaluate_offset(0.5)[1], 0.5)
        self.assertAlmostEqual(anim.evaluate_rotation_degrees(2.0)[0], 60.0)
        self.assertAlmostEqual(anim.evaluate_scale(0.5)[0], 1.1)
        self.assertAlmostEqual(anim.evaluate_alpha(10), 0.25)
        self.assertAlmostEqual(anim.evaluate_glow(0), 1.0)

    def test_alpha_and_glow_inheritance_cut_at_parent_edge(self):
        parent_anim = LayerAnimation(alpha=[AnimationTerm("linear", start=0.5, speed=0)])
        child_anim = LayerAnimation(alpha=[AnimationTerm("linear", start=0.5, speed=0)])
        child = HaloGroup(id="child", animation=child_anim)
        parent = HaloGroup(id="parent", animation=parent_anim, children=[child])
        definition = parse_definition({"id": "demo:tree", "layers": []})
        definition.groups = [parent]
        states = evaluate_definition_tree(definition, 0)
        self.assertAlmostEqual(states[parent.uid]["alpha"], 0.5)
        self.assertAlmostEqual(states[child.uid]["alpha"], 0.25)
        parent.inherit_alpha = False
        states = evaluate_definition_tree(definition, 0)
        self.assertAlmostEqual(states[child.uid]["alpha"], 0.5)

    def test_transition_defaults_property_overrides_and_degrees(self):
        segment = TransitionSegment(
            0.5, "ease_out_cubic",
            scale=TransitionProperty((0, 0, 0), None, property_duration=0.8),
            rotation=TransitionProperty((0, 0, 0), (30, 0, 0), degrees=(90, 0, 0)),
        )
        config = TransitionConfig([segment])
        animation = config.animation_for_group(None)
        self.assertIsNotNone(animation)
        result = animation.evaluate(0.4)
        # Property duration is longer than segment duration and is respected.
        self.assertGreater(result.scale[0], 0.0)
        self.assertAlmostEqual(animation.rotation.elements[0].end_value[0], 390.0)
        self.assertAlmostEqual(rotation_effective_end(0, 30, 90), 390.0)

    def test_empty_shutdown_reverses_startup_per_mod_behavior(self):
        definition = parse_definition({
            "id": "trinity:serina",
            "layers": [{"id": "serina"}],
            "startup": {"id_overrides": {"serina": {"segments": [{
                "duration": 0.5,
                "scale": {"from": [0, 0, 0]},
            }]}}},
            "shutdown": {},
        })
        start = evaluate_definition_transition(definition, "serina", 0.0, startup=False)
        end = evaluate_definition_transition(definition, "serina", 0.5, startup=False)
        self.assertEqual(start.scale, (1.0, 1.0, 1.0))
        self.assertEqual(end.scale, (0.0, 0.0, 0.0))

    def test_coordinate_change_and_transform_conjugation(self):
        self.assertEqual(mc_to_blender((1, 2, 3)), (1, -3, 2))
        self.assertEqual(blender_to_mc((1, -3, 2)), (1, 2, 3))
        matrix = mc_to_blender_matrix(minecraft_transform_matrix((1, 2, 3)))
        self.assertEqual(tuple(row[3] for row in matrix[:3]), (1.0, -3.0, 2.0))
        self.assertEqual(compose_blender_transform((1, 2, 3)), matrix)

    def test_validation_reports_unknown_primitive_and_bad_duration(self):
        definition = parse_definition({
            "id": "demo:test", "layers": [{"primitives": [{"type": "future"}]}],
            "startup": {"segments": [{"duration": 0, "scale": {"from": [0, 0, 0]}}]},
        })
        report = validate_definition(definition)
        self.assertTrue(any(issue.code == "unknown_primitive" for issue in report.warnings))
        self.assertTrue(any(issue.code == "transition_duration" for issue in report.errors))

    def test_zip_member_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../escape.json", "{}")
            with self.assertRaises(ValueError):
                load_pack(path)


if __name__ == "__main__":
    unittest.main()
