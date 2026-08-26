"""Pure Python data model for Halo resource-pack definitions.

The Blender side of the extension intentionally talks to this module through
small, dependency-free objects.  Nothing in this file imports :mod:`bpy` (or
``mathutils``), which also makes the parser and animation evaluator useful to
command line tools and tests.

The model keeps a copy of the source JSON object on every editable node.  The
serializer in :mod:`schema` starts from that copy and replaces known fields;
therefore fields introduced by a newer Halo version, comments represented by
sidecar tools, and the legacy ``primitive``/``shape`` spelling survive a
round-trip even though the editor does not know their meaning.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import ceil, cos, pi, sin, isfinite
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, MutableMapping, Optional, Sequence
import json
import uuid


Number = int | float
Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Vec4 = tuple[float, float, float, float]


def _vec(values: Sequence[Number] | None, length: int, defaults: Sequence[float]) -> tuple[float, ...]:
    """Convert a JSON sequence into a fixed-size immutable numeric tuple."""

    if values is None:
        return tuple(float(v) for v in defaults[:length])
    result = [float(v) for v in values[:length]]
    while len(result) < length:
        result.append(float(defaults[len(result)] if len(result) > 0 else 0.0))
    return tuple(result)


def as_vec2(values: Sequence[Number] | None, default: Vec2 = (0.0, 0.0)) -> Vec2:
    return _vec(values, 2, default)  # type: ignore[return-value]


def as_vec3(values: Sequence[Number] | None, default: Vec3 = (0.0, 0.0, 0.0)) -> Vec3:
    return _vec(values, 3, default)  # type: ignore[return-value]


def clone_json(value: Any) -> Any:
    """Deep-copy JSON-compatible data without importing a JSON library."""

    return deepcopy(value)


def _uid(value: str | None = None) -> str:
    # UUIDs are only editor identities, never written into a pack unless a
    # caller explicitly puts one in the raw AST.
    return value if value else uuid.uuid4().hex


@dataclass(frozen=True, order=True)
class SchemaVersion:
    """Semantic schema version used by the current Halo parser."""

    major: int
    minor: int
    patch: int = 0

    CURRENT = None  # assigned after the class definition

    @classmethod
    def parse(cls, value: str | Sequence[int] | "SchemaVersion") -> "SchemaVersion":
        if isinstance(value, SchemaVersion):
            return value
        if isinstance(value, (tuple, list)):
            if len(value) not in (2, 3):
                raise ValueError(f"invalid schema version: {value!r}")
            parts = [int(v) for v in value]
        else:
            parts = [int(v.strip()) for v in str(value).strip().split(".")]
            if len(parts) not in (2, 3):
                raise ValueError(f"invalid schema version: {value!r}")
        if any(v < 0 for v in parts):
            raise ValueError(f"invalid schema version: {value!r}")
        return cls(parts[0], parts[1], parts[2] if len(parts) == 3 else 0)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def is_before(self, other: "SchemaVersion") -> bool:
        return self < other


SchemaVersion.CURRENT = SchemaVersion(1, 0, 10)


@dataclass(frozen=True)
class ResourceLocation:
    """Minecraft-style ``namespace:path`` resource identifier."""

    namespace: str
    path: str

    @classmethod
    def parse(cls, value: str, default_namespace: str = "minecraft") -> "ResourceLocation":
        raw = str(value).strip()
        if ":" in raw:
            namespace, path = raw.split(":", 1)
        else:
            namespace, path = default_namespace, raw
        if not namespace or not path:
            raise ValueError(f"invalid resource location: {value!r}")
        return cls(namespace, path)

    def __str__(self) -> str:
        return f"{self.namespace}:{self.path}"


@dataclass
class AnimationTerm:
    """One resident animation function.

    Halo 1.0.10 supports ``sin``, ``cos`` and ``linear``.  Unknown functions
    are retained as raw terms so an editor can open a future pack without
    destroying data; their evaluator returns ``0`` and validation reports a
    warning/error for the caller.
    """

    function: str
    A: float = 0.0
    omega: float = 0.0
    phi: float = 0.0
    start: float = 0.0
    speed: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "AnimationTerm":
        function = str(value.get("function", "")).lower()
        return cls(
            function=function,
            A=float(value.get("A", 0.0)),
            omega=float(value.get("omega", 0.0)),
            phi=float(value.get("phi", 0.0)),
            start=float(value.get("start", 0.0)),
            speed=float(value.get("speed", 0.0)),
            raw=clone_json(dict(value)),
        )

    def evaluate(self, t: float) -> float:
        f = self.function.lower()
        if f == "sin":
            return self.A * sin(self.omega * pi * t + self.phi)
        if f == "cos":
            return self.A * cos(self.omega * pi * t + self.phi)
        if f == "linear":
            return self.start + self.speed * t
        return 0.0

    def to_json(self) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        result["function"] = self.function
        if self.function.lower() in ("sin", "cos"):
            result["A"] = self.A
            result["omega"] = self.omega
            # The parser treats phi as optional.  Keep an authored phi and
            # omit a default one for clean canonical output.
            if self.phi != 0.0 or "phi" in result:
                result["phi"] = self.phi
        elif self.function.lower() == "linear":
            result["speed"] = self.speed
            if self.start != 0.0 or "start" in result:
                result["start"] = self.start
        return result


# Convenient names for callers that prefer concrete term classes.
SinTerm = AnimationTerm
CosTerm = AnimationTerm
LinearTerm = AnimationTerm


@dataclass
class LayerAnimation:
    """Resident animation channels attached to a definition or group."""

    offset_x: list[AnimationTerm] = field(default_factory=list)
    offset_y: list[AnimationTerm] = field(default_factory=list)
    offset_z: list[AnimationTerm] = field(default_factory=list)
    rotation_yaw: list[AnimationTerm] = field(default_factory=list)
    rotation_pitch: list[AnimationTerm] = field(default_factory=list)
    rotation_roll: list[AnimationTerm] = field(default_factory=list)
    scale_x: list[AnimationTerm] = field(default_factory=list)
    scale_y: list[AnimationTerm] = field(default_factory=list)
    scale_z: list[AnimationTerm] = field(default_factory=list)
    alpha: list[AnimationTerm] = field(default_factory=list)
    glow: list[AnimationTerm] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    CHANNELS = (
        "offset_x", "offset_y", "offset_z", "rotation_yaw", "rotation_pitch",
        "rotation_roll", "scale_x", "scale_y", "scale_z", "alpha", "glow",
    )

    @classmethod
    def empty(cls) -> "LayerAnimation":
        return cls()

    @property
    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in self.CHANNELS)

    def terms(self, channel: str) -> list[AnimationTerm]:
        aliases = {
            "x": "offset_x", "y": "offset_y", "z": "offset_z",
            "yaw": "rotation_yaw", "pitch": "rotation_pitch", "roll": "rotation_roll",
        }
        return getattr(self, aliases.get(channel, channel))

    def _sum(self, terms: Iterable[AnimationTerm], t: float) -> float:
        return sum(term.evaluate(t) for term in terms)

    def evaluate_offset(self, t: float) -> Vec3:
        return (
            self._sum(self.offset_x, t), self._sum(self.offset_y, t), self._sum(self.offset_z, t)
        )

    def evaluate_rotation_degrees(self, t: float) -> Vec3:
        return (
            self._sum(self.rotation_yaw, t), self._sum(self.rotation_pitch, t),
            self._sum(self.rotation_roll, t),
        )

    # Short alias used by the Blender preview adapter.
    evaluate_rotation = evaluate_rotation_degrees

    def evaluate_scale(self, t: float) -> Vec3:
        return (
            1.0 + self._sum(self.scale_x, t), 1.0 + self._sum(self.scale_y, t),
            1.0 + self._sum(self.scale_z, t),
        )

    def evaluate_alpha(self, t: float) -> float:
        return _clamp01(self._sum(self.alpha, t)) if self.alpha else 1.0

    def evaluate_glow(self, t: float) -> float:
        return _clamp01(self._sum(self.glow, t)) if self.glow else 1.0

    def evaluate(self, t: float) -> dict[str, Any]:
        return {
            "offset": self.evaluate_offset(t),
            "rotation": self.evaluate_rotation_degrees(t),
            "scale": self.evaluate_scale(t),
            "alpha": self.evaluate_alpha(t),
            "glow": self.evaluate_glow(t),
        }

    def to_json(self) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        channels: dict[str, dict[str, list[dict[str, Any]]]] = {
            "offset": {
                "x": [t.to_json() for t in self.offset_x], "y": [t.to_json() for t in self.offset_y],
                "z": [t.to_json() for t in self.offset_z],
            },
            "rotation": {
                "yaw": [t.to_json() for t in self.rotation_yaw],
                "pitch": [t.to_json() for t in self.rotation_pitch],
                "roll": [t.to_json() for t in self.rotation_roll],
            },
            "scale": {
                "x": [t.to_json() for t in self.scale_x], "y": [t.to_json() for t in self.scale_y],
                "z": [t.to_json() for t in self.scale_z],
            },
        }
        # Preserve empty nested objects when they existed; omit newly-created
        # empty channels to match the compact format used by the mod.
        for key, value in channels.items():
            old = result.get(key)
            if any(value.values()) or isinstance(old, Mapping):
                result[key] = value
                if isinstance(old, Mapping):
                    for axis, terms in old.items():
                        if axis not in value:
                            value[axis] = clone_json(terms)
        for key, terms in (("alpha", self.alpha), ("glow", self.glow)):
            if terms or key in result:
                result[key] = [t.to_json() for t in terms]
        return result


@dataclass
class Positioning:
    offset: Vec3 = (0.0, 0.0, 0.0)
    scale: float = 1.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_json(self) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        result["offset"] = list(self.offset)
        result["scale"] = self.scale
        return result


@dataclass
class DampingConfig:
    linear_factor: float = 0.15
    angular_factor: float = 0.1
    max_linear_distance: float = 3.0
    max_angular_degrees: float = 180.0
    allow_angular_momentum: bool = False
    angular_momentum_factor: float = 0.3
    max_angular_momentum_degrees: float = 45.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_json(self) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        result.update({
            "linearFactor": self.linear_factor,
            "angularFactor": self.angular_factor,
            "maxLinearDistance": self.max_linear_distance,
            "maxAngularDegrees": self.max_angular_degrees,
            "angularMomentumFactor": self.angular_momentum_factor,
            "maxAngularMomentumDegrees": self.max_angular_momentum_degrees,
        })
        return result


@dataclass
class BillboardPrimitive:
    texture: str
    size: Vec2 = (0.5, 0.5)
    face_camera: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    uid: str = field(default_factory=_uid, repr=False, compare=False)
    type: str = field(default="billboard", init=False)

    def to_json(self) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        result["type"] = "billboard"
        result["texture"] = self.texture
        result["size"] = list(self.size)
        if self.face_camera or "face_camera" in result:
            result["face_camera"] = bool(self.face_camera)
        return result


@dataclass
class RingPrimitive:
    outer_texture: str
    inner_texture: str | None = None
    size: Vec2 = (0.35, 0.08)
    segments: int = 32
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    uid: str = field(default_factory=_uid, repr=False, compare=False)
    type: str = field(default="ring", init=False)

    @property
    def texture(self) -> str:
        return self.outer_texture

    def to_json(self) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        result["type"] = "ring"
        # Keep the authored spelling where possible.
        if "outer_texture" in result and "texture" not in result:
            result["outer_texture"] = self.outer_texture
        else:
            result["texture"] = self.outer_texture
        if self.inner_texture is None:
            result.pop("inner_texture", None)
        else:
            result["inner_texture"] = self.inner_texture
        result["size"] = list(self.size)
        result["segments"] = int(self.segments)
        return result


@dataclass
class RawPrimitive:
    """Unknown/future primitive retained for lossless pack editing."""

    raw: dict[str, Any]
    uid: str = field(default_factory=_uid, repr=False, compare=False)

    @property
    def type(self) -> str:
        return str(self.raw.get("type", "unknown"))

    def to_json(self) -> dict[str, Any]:
        return clone_json(self.raw)


Primitive = BillboardPrimitive | RingPrimitive | RawPrimitive


@dataclass
class TransitionProperty:
    from_value: tuple[float, ...] | None = None
    to_value: tuple[float, ...] | None = None
    property_duration: float | None = None
    property_easing: str | None = None
    degrees: tuple[float, ...] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # Java-compatible aliases.  ``from`` cannot be a Python identifier.
    @property
    def from_(self) -> tuple[float, ...] | None:
        return self.from_value

    @property
    def to(self) -> tuple[float, ...] | None:
        return self.to_value

    def with_degrees(self, degrees: Sequence[Number] | None) -> "TransitionProperty":
        return TransitionProperty(
            self.from_value, self.to_value, self.property_duration, self.property_easing,
            tuple(float(v) for v in degrees) if degrees is not None else None, clone_json(self.raw)
        )

    def to_json(self, scalar: bool = False) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        if self.from_value is not None:
            result["from"] = self.from_value[0] if scalar and len(self.from_value) == 1 else list(self.from_value)
        elif "from" in result:
            result["from"] = None
        if self.to_value is not None:
            result["to"] = self.to_value[0] if scalar and len(self.to_value) == 1 else list(self.to_value)
        elif "to" in result:
            result["to"] = None
        if self.property_duration is not None:
            result["duration"] = self.property_duration
        if self.property_easing is not None:
            result["easing"] = self.property_easing
        if self.degrees is not None:
            result["degrees"] = list(self.degrees)
        return result


@dataclass
class TransitionSegment:
    duration: float
    easing: str = "linear"
    offset: TransitionProperty | None = None
    scale: TransitionProperty | None = None
    alpha: TransitionProperty | None = None
    rotation: TransitionProperty | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def properties(self) -> Iterator[tuple[str, TransitionProperty]]:
        for name in ("offset", "scale", "alpha", "rotation"):
            value = getattr(self, name)
            if value is not None:
                yield name, value

    def to_json(self) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        result["duration"] = self.duration
        result["easing"] = self.easing
        if self.offset is not None:
            result["offset"] = self.offset.to_json()
        if self.scale is not None:
            result["scale"] = self.scale.to_json()
        if self.alpha is not None:
            alpha_json = self.alpha.to_json(scalar=True)
            # Keep the deprecated spelling when it was the only authored
            # spelling.  This is useful for a strict source-AST round trip;
            # callers can switch to canonical ``alpha`` simply by editing the
            # property or by deleting ``opacity`` from raw.
            if "opacity" in result and "alpha" not in result:
                result["opacity"] = alpha_json
            else:
                result["alpha"] = alpha_json
                # ``alpha`` wins when both are present, matching Java.
                if "alpha" in result:
                    result.pop("opacity", None)
        elif "alpha" not in result and "opacity" in result:
            pass
        if self.rotation is not None:
            result["rotation"] = self.rotation.to_json()
        return result


@dataclass
class TransitionQueueElement:
    start_time: float
    end_time: float
    duration: float
    start_value: tuple[float, ...]
    end_value: tuple[float, ...]
    easing: str = "linear"
    from_explicit: bool = False
    to_explicit: bool = False
    degrees: tuple[float, ...] | None = None

    @property
    def start(self) -> float:
        return self.start_time

    @property
    def end(self) -> float:
        return self.end_time

    def is_hold(self, epsilon: float = 1e-3) -> bool:
        return len(self.start_value) == len(self.end_value) and all(
            abs(a - b) <= epsilon for a, b in zip(self.start_value, self.end_value)
        )

    def evaluate(self, time: float) -> tuple[float, ...]:
        if self.duration <= 0.0:
            return self.end_value
        t = max(0.0, min(1.0, (time - self.start_time) / self.duration))
        u = easing_value(self.easing, t)
        return tuple(a + (b - a) * u for a, b in zip(self.start_value, self.end_value))

    def with_values(self, start: Sequence[Number], end: Sequence[Number]) -> "TransitionQueueElement":
        return TransitionQueueElement(
            self.start_time, self.end_time, self.duration, tuple(float(v) for v in start),
            tuple(float(v) for v in end), self.easing, self.from_explicit, self.to_explicit, self.degrees
        )

    def with_start_value(self, value: Sequence[Number]) -> "TransitionQueueElement":
        return self.with_values(value, self.end_value)

    def with_end_value(self, value: Sequence[Number]) -> "TransitionQueueElement":
        return self.with_values(self.start_value, value)

    def reversed(self, total_duration: float) -> "TransitionQueueElement":
        return TransitionQueueElement(
            total_duration - self.end_time, total_duration - self.start_time, self.duration,
            self.end_value, self.start_value, self.easing,
            self.to_explicit, self.from_explicit,
            tuple(-v for v in self.degrees) if self.degrees is not None else None,
        )


@dataclass
class TransitionQueue:
    elements: list[TransitionQueueElement]
    steady_state_value: tuple[float, ...]

    @property
    def total_duration(self) -> float:
        return self.elements[-1].end_time if self.elements else 0.0

    @property
    def is_empty(self) -> bool:
        return not self.elements

    def evaluate(self, time: float) -> tuple[float, ...]:
        if not self.elements:
            return self.steady_state_value
        if time <= self.elements[0].start_time:
            return self.elements[0].start_value
        if time >= self.elements[-1].end_time:
            return self.elements[-1].end_value
        for element in self.elements:
            if time <= element.end_time:
                return element.evaluate(time)
        return self.elements[-1].end_value

    def reversed(self) -> "TransitionQueue":
        result = [e.reversed(self.total_duration) for e in reversed(self.elements)]
        _normalize_degrees(result)
        return TransitionQueue(result, self.steady_state_value)

    def with_head_start(self, value: Sequence[Number]) -> "TransitionQueue":
        if not self.elements:
            return self
        patched = list(self.elements)
        target = tuple(float(v) for v in value)
        i = 0
        changed = False
        while i < len(patched) and not patched[i].from_explicit:
            element = patched[i]
            if element.is_hold():
                patched[i] = element.with_values(target, target)
                i += 1
            else:
                patched[i] = element.with_start_value(target)
                changed = True
                break
            changed = True
        _normalize_degrees(patched)
        return TransitionQueue(patched, self.steady_state_value) if changed else self

    def with_tail_end(self, value: Sequence[Number]) -> "TransitionQueue":
        if not self.elements:
            return self
        patched = list(self.elements)
        target = tuple(float(v) for v in value)
        i = len(patched) - 1
        changed = False
        while i >= 0 and not patched[i].to_explicit:
            element = patched[i]
            if element.is_hold():
                patched[i] = element.with_values(target, target)
                i -= 1
            else:
                patched[i] = element.with_end_value(target)
                changed = True
                break
            changed = True
        _normalize_degrees(patched)
        return TransitionQueue(patched, self.steady_state_value) if changed else self


@dataclass
class TransitionResult:
    offset: Vec3 = (0.0, 0.0, 0.0)
    scale: Vec3 = (1.0, 1.0, 1.0)
    alpha: float = 1.0
    rotation: Vec3 = (0.0, 0.0, 0.0)
    glow: float = 1.0

    @property
    def rotation_degrees(self) -> Vec3:
        return self.rotation


@dataclass
class TransitionAnimationResult:
    offset: TransitionQueue
    scale: TransitionQueue
    alpha: TransitionQueue
    rotation: TransitionQueue

    @property
    def total_duration(self) -> float:
        return max(q.total_duration for q in (self.offset, self.scale, self.alpha, self.rotation))

    def evaluate(self, elapsed: float) -> TransitionResult:
        off = _as3(self.offset.evaluate(elapsed), (0.0, 0.0, 0.0))
        scl = _as3(self.scale.evaluate(elapsed), (1.0, 1.0, 1.0))
        rot = _as3(self.rotation.evaluate(elapsed), (0.0, 0.0, 0.0))
        alpha = self.alpha.evaluate(elapsed)[0] if self.alpha.evaluate(elapsed) else 1.0
        return TransitionResult(off, scl, float(alpha), rot)

    def reversed(self) -> "TransitionAnimationResult":
        return TransitionAnimationResult(
            self.offset.reversed(), self.scale.reversed(), self.alpha.reversed(), self.rotation.reversed()
        )

    def with_head(self, values: Mapping[str, Sequence[Number]]) -> "TransitionAnimationResult":
        return TransitionAnimationResult(
            self.offset.with_head_start(values.get("offset", self.offset.steady_state_value)),
            self.scale.with_head_start(values.get("scale", self.scale.steady_state_value)),
            self.alpha.with_head_start(values.get("alpha", self.alpha.steady_state_value)),
            self.rotation.with_head_start(values.get("rotation", self.rotation.steady_state_value)),
        )

    def with_tail(self, values: Mapping[str, Sequence[Number]]) -> "TransitionAnimationResult":
        return TransitionAnimationResult(
            self.offset.with_tail_end(values.get("offset", self.offset.steady_state_value)),
            self.scale.with_tail_end(values.get("scale", self.scale.steady_state_value)),
            self.alpha.with_tail_end(values.get("alpha", self.alpha.steady_state_value)),
            self.rotation.with_tail_end(values.get("rotation", self.rotation.steady_state_value)),
        )


@dataclass
class TransitionConfig:
    segments: list[TransitionSegment] = field(default_factory=list)
    id_overrides: dict[str, list[TransitionSegment]] = field(default_factory=dict)
    direction: str = "startup"
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _cache: dict[str, TransitionAnimationResult | None] = field(default_factory=dict, init=False, repr=False)

    @property
    def max_duration(self) -> float:
        values = [sum(max(0.0, s.duration) for s in self.segments)]
        values.extend(sum(max(0.0, s.duration) for s in segs) for segs in self.id_overrides.values())
        return max(values, default=0.0)

    def segments_for_group(self, group_id: str | None) -> list[TransitionSegment]:
        if group_id and group_id in self.id_overrides and self.id_overrides[group_id]:
            return self.id_overrides[group_id]
        return self.segments

    def animation_for_group(self, group_id: str | None) -> TransitionAnimationResult | None:
        key = group_id or ""
        if key not in self._cache:
            segs = self.segments_for_group(group_id)
            self._cache[key] = build_transition_queues(segs, self.max_duration, self.direction) if segs else None
        return self._cache[key]

    def to_json(self) -> dict[str, Any]:
        result = clone_json(self.raw) if isinstance(self.raw, Mapping) else {}
        if self.segments or "segments" in result:
            result["segments"] = [s.to_json() for s in self.segments]
        if self.id_overrides or "id_overrides" in result:
            old_overrides = result.get("id_overrides")
            result["id_overrides"] = {
                gid: {"segments": [s.to_json() for s in segs]}
                if isinstance(old_overrides, Mapping) and isinstance(old_overrides.get(gid), Mapping)
                else [s.to_json() for s in segs]
                for gid, segs in self.id_overrides.items()
            }
        return result


@dataclass
class HaloGroup:
    id: str | None = None
    position: Vec3 = (0.0, 0.0, 0.0)
    rotation: Vec3 = (0.0, 0.0, 0.0)
    scale: float = 1.0
    primitives: list[Primitive] = field(default_factory=list)
    glowing: bool = True
    inherit_alpha: bool = True
    inherit_glow: bool = True
    animation: LayerAnimation | None = None
    children: list["HaloGroup"] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    uid: str = field(default_factory=_uid, repr=False, compare=False)
    source_primitive_key: str | None = field(default=None, repr=False, compare=False)

    def walk(self) -> Iterator["HaloGroup"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, group_id: str) -> "HaloGroup | None":
        return next((g for g in self.walk() if g.id == group_id), None)

    @property
    def total_primitive_count(self) -> int:
        return len(self.primitives) + sum(child.total_primitive_count for child in self.children)

    def add_child(self, child: "HaloGroup", index: int | None = None) -> None:
        if child is self or any(g is child for g in child.walk()):
            raise ValueError("cannot parent a group below itself or its descendant")
        if index is None:
            self.children.append(child)
        else:
            self.children.insert(max(0, min(index, len(self.children))), child)

    def remove_child(self, child: "HaloGroup") -> None:
        self.children.remove(child)


@dataclass
class HaloDefinition:
    id: str
    groups: list[HaloGroup] = field(default_factory=list)
    orientation_mode: str = "locked"
    sync_offset: Vec3 = (0.0, 0.0, 0.0)
    animation: LayerAnimation | None = None
    positioning: Positioning = field(default_factory=Positioning)
    damping: DampingConfig = field(default_factory=DampingConfig)
    allow_angular_momentum: bool = False
    hide_on_sleep: bool = False
    display_in_invisible: bool = False
    schema_version: SchemaVersion = field(default_factory=lambda: SchemaVersion.CURRENT)
    startup: TransitionConfig | None = None
    shutdown: TransitionConfig | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    source_path: str | None = field(default=None, repr=False, compare=False)
    namespace: str | None = field(default=None, repr=False, compare=False)
    legacy_shape: bool = field(default=False, repr=False, compare=False)
    uid: str = field(default_factory=_uid, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.id.split(":", 1)[-1]

    def walk_groups(self) -> Iterator[HaloGroup]:
        for group in self.groups:
            yield from group.walk()

    def find_group(self, group_id: str) -> HaloGroup | None:
        return next((g for g in self.walk_groups() if g.id == group_id), None)

    @property
    def total_primitive_count(self) -> int:
        return sum(group.total_primitive_count for group in self.groups)

    def resident_state(self, time: float, group: HaloGroup | None = None) -> dict[str, Any]:
        """Evaluate the effective resident channels at ``time``.

        This helper evaluates one node's own animation.  Tree inheritance is
        exposed by :func:`effective_group_state` below, which the Blender
        adapter uses while traversing the scene graph.
        """

        animation = group.animation if group is not None and group.animation else self.animation
        return (animation or LayerAnimation.empty()).evaluate(time)


@dataclass
class PackProject:
    """An editable resource pack independent of Blender."""

    definitions: list[HaloDefinition] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    pack_meta: dict[str, Any] = field(default_factory=dict)
    root: str | None = None
    source_format: str = "memory"
    dirty: bool = False

    def add_definition(self, definition: HaloDefinition) -> HaloDefinition:
        self.definitions.append(definition)
        self.dirty = True
        return definition

    def remove_definition(self, definition: HaloDefinition) -> None:
        self.definitions.remove(definition)
        self.dirty = True

    def get_definition(self, definition_id: str) -> HaloDefinition | None:
        return next((d for d in self.definitions if d.id == definition_id), None)

    def namespaces(self) -> set[str]:
        result = set()
        for definition in self.definitions:
            if ":" in definition.id:
                result.add(definition.id.split(":", 1)[0])
        return result


def easing_value(name: str | None, t: float) -> float:
    """Evaluate the three easing functions supported by Halo 1.0.10."""

    t = max(0.0, min(1.0, float(t)))
    name = (name or "linear").lower().replace("-", "_")
    if name in ("ease_out_cubic", "easeoutcubic"):
        inv = 1.0 - t
        return 1.0 - inv * inv * inv
    if name in ("ease_in_out_cubic", "easeinoutcubic"):
        if t < 0.5:
            return 4.0 * t * t * t
        f = -2.0 * t + 2.0
        return 1.0 - f * f * f / 2.0
    return t


def rotation_effective_end(start: float, end: float, degrees: float) -> float:
    """Apply Halo's minimum signed rotation travel rule for one axis."""

    if degrees == 0.0:
        return end
    remainder = (end - start) % 360.0
    if degrees > 0.0:
        travel = remainder + 360.0 * ceil((degrees - remainder) / 360.0)
    else:
        travel = remainder - 360.0 * ceil((remainder - degrees) / 360.0)
    return start + travel


def rotation_effective_ends(
    start: Sequence[Number], end: Sequence[Number], degrees: Sequence[Number]
) -> tuple[float, ...]:
    length = max(len(start), len(end), len(degrees))
    return tuple(rotation_effective_end(
        float(start[i]) if i < len(start) else 0.0,
        float(end[i]) if i < len(end) else 0.0,
        float(degrees[i]) if i < len(degrees) else 0.0,
    ) for i in range(length))


def _normalize_degrees(elements: list[TransitionQueueElement]) -> None:
    for i, element in enumerate(elements):
        if element.degrees is None:
            continue
        elements[i] = element.with_end_value(
            rotation_effective_ends(element.start_value, element.end_value, element.degrees)
        )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _as3(values: Sequence[Number], default: Vec3) -> Vec3:
    return as_vec3(values, default)


def effective_group_state(
    definition: HaloDefinition,
    group: HaloGroup,
    time: float,
    parent_alpha: float = 1.0,
    parent_glow: float = 1.0,
) -> dict[str, Any]:
    """Return one group's resident transform and inherited alpha/glow."""

    local = (group.animation or LayerAnimation.empty()).evaluate(time)
    # The inherit flags belong to the edge *below* this node.  A group's own
    # alpha/glow always receives the effective value from its parent; setting
    # inherit_alpha/inherit_glow=false means only that descendants start from
    # one, exactly as the Java renderer's tree walk does.
    alpha = local["alpha"] * parent_alpha
    glow = local["glow"] * parent_glow
    return {
        "offset": local["offset"], "rotation": local["rotation"], "scale": local["scale"],
        "alpha": alpha, "glow": glow,
        "child_alpha": alpha if group.inherit_alpha else 1.0,
        "child_glow": glow if group.inherit_glow else 1.0,
    }


def build_transition_queues(
    segments: Sequence[TransitionSegment],
    global_total_duration: float,
    direction: str = "startup",
) -> TransitionAnimationResult:
    """Build the four per-property queues used by transition previews."""

    normalized_direction = str(direction).lower()
    return TransitionAnimationResult(
        _build_property_queue(segments, "offset", (0.0, 0.0, 0.0), global_total_duration, normalized_direction),
        _build_property_queue(segments, "scale", (1.0, 1.0, 1.0), global_total_duration, normalized_direction),
        _build_property_queue(segments, "alpha", (1.0,), global_total_duration, normalized_direction),
        _build_property_queue(segments, "rotation", (0.0, 0.0, 0.0), global_total_duration, normalized_direction),
    )


def _build_property_queue(
    segments: Sequence[TransitionSegment], name: str, steady: tuple[float, ...],
    global_total_duration: float, direction: str,
) -> TransitionQueue:
    raw: list[TransitionQueueElement] = []
    active_indices = [i for i, segment in enumerate(segments) if getattr(segment, name) is not None]
    first_active = active_indices[0] if active_indices else -1
    last_active = active_indices[-1] if active_indices else -1
    segment_time = 0.0
    previous_end = 0.0
    for segment_index, segment in enumerate(segments):
        prop = getattr(segment, name)
        if prop is not None:
            start = prop.from_value
            end = prop.to_value
            # The Java parser's boundary validation uses the property's
            # steady-state value when startup's first active segment omits
            # ``from`` or shutdown's last active segment omits ``to``.  Keep
            # explicit flags false so per-instance head/tail alignment can
            # still replace the derived endpoint later.
            if direction == "startup" and segment_index == first_active and start is None:
                start = steady
            if direction == "shutdown" and segment_index == last_active and end is None:
                end = steady
            duration = prop.property_duration if prop.property_duration is not None else segment.duration
            easing = prop.property_easing or segment.easing
            duration = max(0.0, float(duration))
            prop_start = max(segment_time, previous_end)
            raw.append(TransitionQueueElement(
                prop_start, prop_start + duration, duration,
                tuple(start) if start is not None else None,  # type: ignore[arg-type]
                tuple(end) if end is not None else None,  # type: ignore[arg-type]
                easing, prop.from_value is not None, prop.to_value is not None,
                prop.degrees if name == "rotation" else None,
            ))
            previous_end = prop_start + duration
        segment_time += max(0.0, segment.duration)
    if not raw:
        return TransitionQueue([], steady)

    # Fill timeline gaps.  The null endpoint values are resolved below with
    # the same end-anchored (startup) or head-anchored (shutdown) pass as the
    # Java implementation.
    filled: list[TransitionQueueElement] = []
    cursor = 0.0
    total = max(0.0, float(global_total_duration))
    for element in raw:
        if element.start_time > cursor + 1e-6:
            filled.append(TransitionQueueElement(
                cursor, element.start_time, element.start_time - cursor,
                None, None, "linear", False, False, None
            ))
        filled.append(element)
        cursor = element.end_time
    if cursor < total - 1e-6:
        filled.append(TransitionQueueElement(cursor, total, total - cursor, None, None, "linear", False, False, None))

    if direction == "shutdown":
        for i, element in enumerate(filled):
            start = element.start_value
            end = element.end_value
            if start is None:  # type: ignore[comparison-overlap]
                start = filled[i - 1].end_value if i else steady
            if end is None:  # type: ignore[comparison-overlap]
                end = start
            filled[i] = TransitionQueueElement(
                element.start_time, element.end_time, element.duration,
                tuple(start), tuple(end), element.easing,
                element.from_explicit, element.to_explicit, element.degrees,
            )
    else:
        for i in range(len(filled) - 1, -1, -1):
            element = filled[i]
            start = element.start_value
            end = element.end_value
            if end is None:  # type: ignore[comparison-overlap]
                end = filled[i + 1].start_value if i + 1 < len(filled) else steady
            if start is None:  # type: ignore[comparison-overlap]
                start = end
            filled[i] = TransitionQueueElement(
                element.start_time, element.end_time, element.duration,
                tuple(start), tuple(end), element.easing,
                element.from_explicit, element.to_explicit, element.degrees,
            )

    # mypy cannot express the temporary None endpoints; runtime values are
    # concrete by this point.
    _normalize_degrees(filled)
    return TransitionQueue(filled, steady)


def transition_result_for(
    definition: HaloDefinition,
    group_id: str | None,
    elapsed: float,
    startup: bool = True,
    head_values: Mapping[str, Sequence[Number]] | None = None,
    tail_values: Mapping[str, Sequence[Number]] | None = None,
) -> TransitionResult:
    """Resolve and evaluate startup/shutdown including inversion fallback."""

    config = definition.startup if startup else definition.shutdown
    reversed_fallback = False
    if not startup and config is None:
        config = definition.startup
        reversed_fallback = config is not None
    if config is None:
        return TransitionResult()
    animation = config.animation_for_group(group_id)
    if animation is None:
        return TransitionResult()
    if reversed_fallback:
        animation = animation.reversed()
    if head_values is not None:
        animation = animation.with_head(head_values)
    if tail_values is not None:
        animation = animation.with_tail(tail_values)
    return animation.evaluate(elapsed)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
