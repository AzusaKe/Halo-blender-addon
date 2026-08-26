"""Resident and transition animation evaluation for Halo packs.

The implementation mirrors the Java renderer's deliberately simple rules:
terms on one channel are summed, scale terms are deltas from one, alpha/glow
are clamped, transition properties have independent queues, and rotation is
interpolated as Euler degrees (never quaternion slerp).  This module is a
thin, documented API over the value objects in :mod:`models`; it is kept
separate so a Blender frame-change handler can import it without importing the
JSON or packaging layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .models import (
    AnimationTerm,
    HaloDefinition,
    HaloGroup,
    LayerAnimation,
    TransitionAnimationResult,
    TransitionConfig,
    TransitionProperty,
    TransitionQueue,
    TransitionQueueElement,
    TransitionResult,
    TransitionSegment,
    build_transition_queues,
    easing_value,
    effective_group_state,
    rotation_effective_end,
    rotation_effective_ends,
    transition_result_for,
)


class AnimationError(ValueError):
    """Raised for an invalid animation request."""


def evaluate_term(term: AnimationTerm, seconds: float) -> float:
    return term.evaluate(seconds)


def evaluate_resident(animation: LayerAnimation | None, seconds: float) -> dict[str, Any]:
    """Evaluate all resident channels, returning JSON-shaped values."""

    return (animation or LayerAnimation.empty()).evaluate(seconds)


def transition_duration(config: TransitionConfig | None) -> float:
    return config.max_duration if config else 0.0


def build_transition(
    config: TransitionConfig,
    group_id: str | None = None,
) -> TransitionAnimationResult | None:
    return config.animation_for_group(group_id)


def evaluate_transition(
    config: TransitionConfig | None,
    elapsed: float,
    *,
    group_id: str | None = None,
    head_values: Mapping[str, Sequence[float]] | None = None,
    tail_values: Mapping[str, Sequence[float]] | None = None,
    reverse: bool = False,
) -> TransitionResult:
    """Evaluate one transition configuration.

    ``head_values`` patches derived leading endpoints (used for shutdown from
    an in-progress preview); ``tail_values`` patches derived trailing
    endpoints (used to align startup with the resident animation).  A caller
    can request the same fallback inversion used by the mod with ``reverse``.
    """

    if config is None:
        return TransitionResult()
    animation = config.animation_for_group(group_id)
    if animation is None:
        return TransitionResult()
    if reverse:
        animation = animation.reversed()
    if head_values is not None:
        animation = animation.with_head(head_values)
    if tail_values is not None:
        animation = animation.with_tail(tail_values)
    return animation.evaluate(elapsed)


def evaluate_definition_transition(
    definition: HaloDefinition,
    group_id: str | None,
    elapsed: float,
    *,
    startup: bool = True,
    head_values: Mapping[str, Sequence[float]] | None = None,
    tail_values: Mapping[str, Sequence[float]] | None = None,
) -> TransitionResult:
    return transition_result_for(
        definition, group_id, elapsed, startup,
        head_values=head_values, tail_values=tail_values,
    )


def resident_group_state(
    definition: HaloDefinition,
    group: HaloGroup,
    seconds: float,
    *,
    parent_alpha: float = 1.0,
    parent_glow: float = 1.0,
) -> dict[str, Any]:
    return effective_group_state(definition, group, seconds, parent_alpha, parent_glow)


def evaluate_definition_tree(
    definition: HaloDefinition,
    seconds: float,
) -> dict[str, dict[str, Any]]:
    """Evaluate every group and apply alpha/glow inheritance down the tree.

    The returned mapping is keyed by stable editor UID.  ``alpha`` and
    ``glow`` are the values used by the current group; ``child_alpha`` and
    ``child_glow`` expose the values to pass to its children after respecting
    the two inheritance switches.
    """

    result: dict[str, dict[str, Any]] = {}

    root = (definition.animation or LayerAnimation.empty()).evaluate(seconds)

    def visit(group: HaloGroup, parent_alpha: float, parent_glow: float) -> None:
        state = resident_group_state(
            definition, group, seconds,
            parent_alpha=parent_alpha, parent_glow=parent_glow,
        )
        result[group.uid] = state
        for child in group.children:
            visit(child, state["child_alpha"], state["child_glow"])

    for group in definition.groups:
        # The definition-level animation is an implicit root group.  Its
        # alpha/glow channels therefore seed inheritance for every top-level
        # group (the Blender transform adapter separately composes its
        # offset/rotation/scale matrix).
        visit(group, root["alpha"], root["glow"])
    return result


# Public aliases convenient for frame-change callbacks.
evaluate_tree = evaluate_definition_tree
evaluate_animation = evaluate_resident


__all__ = [
    "AnimationError", "AnimationTerm", "LayerAnimation", "TransitionConfig",
    "TransitionProperty", "TransitionSegment", "TransitionQueue", "TransitionQueueElement",
    "TransitionAnimationResult", "TransitionResult", "build_transition", "build_transition_queues",
    "evaluate_term", "evaluate_resident", "evaluate_animation", "evaluate_transition",
    "evaluate_definition_transition", "evaluate_definition_tree", "evaluate_tree",
    "resident_group_state", "transition_duration", "easing_value", "rotation_effective_end",
    "rotation_effective_ends",
]
