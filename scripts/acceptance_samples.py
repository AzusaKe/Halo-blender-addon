"""Read-only acceptance checks against HaloPackTool's real output fixtures."""

from __future__ import annotations

import sys
from pathlib import Path


project_root = Path(sys.argv[1] if len(sys.argv) > 1 else r"F:\HaloBlenderAddon")
sys.path.insert(0, str(project_root / "halo_pack_editor"))
from core.pack_io import import_pack


fixture_root = Path(r"F:\HaloPackTool\output")


def raw_def(asset):
    return asset.document.data


def groups(raw):
    result = []
    def visit(items):
        for group in items or []:
            if not isinstance(group, dict):
                continue
            result.append(group)
            visit(group.get("children", []))
    visit(raw.get("layers", []))
    return result


def primitives(raw):
    result = []
    for group in groups(raw):
        if isinstance(group.get("primitive"), dict):
            result.append(group["primitive"])
        result.extend(value for value in group.get("primitives", []) if isinstance(value, dict))
    return result


hina = raw_def(import_pack(fixture_root / "Individual" / "Hina.zip").definitions[0])
assert len(groups(hina)) == 57
assert len(primitives(hina)) == 29

rio = raw_def(import_pack(fixture_root / "Individual" / "Rio.zip").definitions[0])
assert any(len(group.get("primitives", [])) > 1 for group in groups(rio))

shiroko = raw_def(import_pack(fixture_root / "Individual" / "Shiroko.zip").definitions[0])
startup = shiroko.get("startup", {})
assert startup.get("id_overrides")
assert "degrees" in repr(startup)

toki = raw_def(import_pack(fixture_root / "Individual" / "Toki.zip").definitions[0])
assert any(group.get("glowing") is False for group in groups(toki))

ring_project = import_pack(fixture_root / "Individual" / "Ring_default.zip")
ring_asset = ring_project.definitions[0]
assert Path(ring_asset.source_path).stem.lower() != ring_asset.identifier.split(":", 1)[-1].lower()
assert any(d.code == "missing_texture" for d in ring_project.diagnostics)

all_project = import_pack(fixture_root / "All the halo pack.zip")
assert len(all_project.definitions) >= 60
print("ACCEPTANCE_OK", len(all_project.definitions), len(all_project.files), len(all_project.diagnostics))
