"""Halo Pack Editor Blender Extension.

Install the directory/ZIP through Blender's Extension manager.  The package
does not depend on a fixed development path; resource roots are stored on the
scene when a pack is imported.
"""

from __future__ import annotations

try:
    import bpy
except ImportError:  # pragma: no cover - source tooling outside Blender
    bpy = None

from . import blender_scene, geometry, handlers, materials, operators, panels, properties


def register():
    if bpy is None:  # pragma: no cover
        return
    properties.register_properties()
    operators.register_operators()
    panels.register_panels()
    handlers.register_handlers()


def unregister():
    if bpy is None:  # pragma: no cover
        return
    handlers.unregister_handlers()
    panels.unregister_panels()
    operators.unregister_operators()
    properties.unregister_properties()


if __name__ == "__main__":  # pragma: no cover
    register()


__all__ = ["register", "unregister"]
