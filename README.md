English | [中文](README_ZH.md)

# Halo Pack Editor

Halo Pack Editor is a Blender 5.2 LTS extension for importing, previewing, editing, merging, and exporting resource packs for the Halo Minecraft mod. It preserves unknown pack files and unrecognized JSON fields so existing and future definitions can be edited without losing data.

## Download and compatibility

- Latest stable release: [`v0.4.0`](https://github.com/AzusaKe/Halo-blender-addon/releases/tag/v0.4.0), available from [GitHub Releases](https://github.com/AzusaKe/Halo-blender-addon/releases/latest).
- Host: Blender 5.2 LTS or newer.
- Halo formats: full Billboard/Ring support for Halo 1.x and native OBJ Mesh support for Halo 2.x schema 1.1.0.
- Generated resource-pack metadata targets Minecraft Java 1.20 through 26.3 and future formats by default, using the practical open upper bound `2147483647`. A compatible Halo mod build is still required in game; the bound should be revisited if Mojang changes the metadata contract.

## Features

- Import and export ZIP archives or unpacked resource-pack folders.
- Keep multiple imported packs in one editing project and export them as a merged pack.
- Load multiple namespaces and Halo definitions at once. Later duplicate definition IDs receive numeric suffixes instead of overwriting earlier definitions.
- Edit the same group/primitive hierarchy used by the mod through Blender objects and the Outliner.
- Preview Billboard, Ring, and Halo 2.x native OBJ Mesh primitives with Minecraft-to-Blender coordinate conversion.
- Edit position, YXZ rotation, uniform scale, glow, alpha/glow inheritance, positioning, orientation, and damping fields.
- Edit and preview resident `sin`, `cos`, and `linear` animation terms, plus graphical startup/shutdown transition segments.
- Preview in Halo-local space or relative to a standard Minecraft player head.
- Create blank packs, definitions, groups, and primitives; import or relink PNG and OBJ resources.
- Convert any ordinary Mesh already in the Blender scene—including geometry imported from FBX or glTF/GLB—directly into a Halo-native triangulated OBJ, with direct-image or Cycles-baked material import.
- Duplicate, delete, and move sibling groups or primitives in batches.
- Convert ordinary Blender Mesh objects into nested Halo groups and Billboard primitives, using Cycles baking or optional direct UV sampling.
- Preview transparent materials consistently in EEVEE and avoid coincident inner/outer Ring surfaces in Cycles.

## Halo 2.x native Mesh support

The Mesh editor follows Halo schema 1.1.0 and supports:

- importing or relinking OBJ models under the current definition namespace;
- importing a regular Blender scene Mesh directly into the selected Halo Mesh primitive;
- per-axis bounding-box fitting through `size`;
- authored-coordinate scaling through `preserve_proportions` and uniform `scale`;
- `material.double_sided`;
- one `alpha_mask` effect with `linear`/`step`, threshold, and animated U/V offsets;
- independent base-texture and mask dimensions and aspect ratios;
- preserving unknown Mesh and shader fields through the raw JSON AST.

At `preserve_proportions: true` and `scale: 1`, one OBJ unit equals one Minecraft block. The original OBJ origin is retained. Base textures and alpha masks keep their native resolutions and share normalized UV coordinates; the editor does not resample them or require matching dimensions.

Minecraft resource IDs and newly imported PNG/OBJ filenames are normalized to lowercase. Identical files are reused, while genuine lowercase path collisions receive `_1`, `_2`, and later suffixes.

### Importing a project Mesh as native OBJ

Select a Halo native Mesh primitive and choose **Project Mesh…** beside the external OBJ button. The source list contains regular Mesh objects in the current scene, including meshes previously imported from FBX, glTF/GLB, or other formats. Halo preview objects are excluded.

- **Keep scene appearance** evaluates visible modifiers and bakes the source object's transform relative to the target Halo parent group into the OBJ. **Source local coordinates only** ignores object G/R/S. Neither path modifies the source object or Mesh datablock.
- Output contains only Halo-supported `v`, `vt`, and triangular `f` statements. Quads and N-gons are triangulated during serialization; MTL declarations, rigs, animations, and unsupported OBJ extensions are omitted.
- **No material import** retains the primitive's current base texture. A source without UVs receives placeholder UVs and a warning.
- **Direct image import** preserves the active UV map. It accepts a directly connected Principled/Emission Image Texture and can also extract the sole image from common FBX/glTF/VRM Mix graphs, with a warning that Mix/color/lighting/alpha operations are omitted. External PNG/labPBR files, packed images, and generated images are supported without starting Cycles. Multiple images or Mapping/Generated coordinates are ambiguous against Halo's single `texture` and require baking.
- **Cycles bake** creates a separate Smart UV atlas and bakes multiple materials to one 16–8192 px PNG with configurable margin and diffuse/emission/combined modes. Original/render UVs remain active for source material sampling; `Halo Bake UV` is used only as the bake destination and final OBJ UV, preventing the source texture from being resampled through the new atlas. Blender 5.2 exposes material baking through Cycles rather than EEVEE. Complex transparency that cannot be reduced to Principled Alpha is baked opaque and reported as a warning.

Generated textures are packed into the `.blend` and materialized only when the resource pack is exported. The generated OBJ is validated with the same restricted parser used for imported Halo models before it enters the editable pack.

## Installation

1. Download `halo_pack_editor-0.4.0.zip` from the latest release.
2. Open **Edit → Preferences → Extensions** in Blender 5.2 LTS or newer.
3. Choose **Install from Disk** and select the ZIP.
4. Enable Halo Pack Editor.
5. In the 3D View, press `N` and open the **Halo** sidebar tab.

## Basic workflow

1. In the **Project** panel, import a resource-pack ZIP/folder or create a blank pack. Imports are appended to the current project rather than replacing it.
2. Select a definition root, group, or primitive in the Outliner. The Halo sidebar changes to the corresponding graphical editor.
3. Use the group transform controls for Minecraft position, YXZ rotation, and uniform scale. Managed Halo objects intentionally lock Blender's native G/R/S fields so unsavable transforms are not introduced accidentally.
4. Edit primitive textures, dimensions, Ring segments, `face_camera`, or Mesh-specific OBJ/material settings. A native Mesh can load an external OBJ or convert a regular Mesh from the current project. Geometry and materials refresh immediately.
5. Use **Tree Editing** to move groups, or migrate primitives into an automatically created child group. Batch operations accept only siblings with the same direct parent.
6. Edit resident and transition animations graphically, or open the multiline JSON editor for advanced fields. Invalid pending JSON never overwrites the last valid definition.
7. Select resident, startup, shutdown, or full-sequence preview and play the Blender timeline.
8. Run validation, then export the complete project as a ZIP or folder.

The original imported ZIP/folder is never edited in place. Export refuses to overwrite an existing target unless overwrite is explicitly enabled.

## Project and resource persistence

Imported/relinked PNG files and editable source snapshots are embedded when the `.blend` file is saved. This includes OBJ files, labPBR companions, `pack.png`, `.png.mcmeta`, and unknown pack files. Reopening the project reconstructs an independent user-data cache, so editing and export do not depend on a temporary extraction directory.

For older projects, use **3D View → N → Halo → Project / Resource Packs → Repair and Embed Resources**, then save a new `.blend` copy. Irrecoverable missing temporary images are removed from Blender image data and replaced by visible missing-texture placeholders without deleting their JSON resource IDs.

Export retains only texture families reachable from the final Halo JSON documents. Unreferenced PNG files left by earlier IDs or namespace renames are removed from the temporary export tree, while source caches and embedded project copies remain untouched.

## Converting Blender Mesh objects

Select the target Halo root/group and open **Mesh to Child Groups**:

- The source Mesh's local coordinates become the wrapper group's local coordinates. Object-level G/R/S is intentionally ignored; apply those transforms in Blender first if required.
- Each valid face or merged coplanar face cluster is projected to its minimum covering rectangle and emitted as a flat child group with a Billboard.
- Coplanar adjacent faces with the same material and orientation can be merged to remove internal seams.
- Transparent pixels outside the source polygon preserve non-rectangular silhouettes.
- Edge expansion copies sampled RGBA pixels outward without averaging colors.
- Direct UV sampling handles simple color/image materials quickly; unsupported node graphs fall back to the selected Cycles bake mode.
- Interactive conversion runs incrementally with a visible progress bar and cancellation between baked clusters.

Generated textures are packed into the `.blend` project and written under `assets/<namespace>/textures/halo/mesh_bakes/` only during resource-pack export.

## Coordinates and JSON compatibility

- Minecraft `(x, y, z)` maps to Blender `(x, -z, y)`.
- JSON rotation is `[yaw, pitch, roll]` in degrees and YXZ order.
- Billboard geometry lies in the definition-local XZ plane with a `-Y` normal.
- Ring `size` is `[radius, axial_width]`, not radial thickness.

The typed editor targets Halo 2.x schema `1.1.0` and remains compatible with Halo 1.0.x Billboard/Ring definitions. Unknown keys, legacy `primitive`/`shape` spellings, and unsupported future fields are preserved in the raw JSON AST. Export uses UTF-8 and two-space indentation; original whitespace is not preserved byte-for-byte.

## Development and verification

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
blender --command extension validate halo_pack_editor
blender --command extension build --source-dir halo_pack_editor --output-filepath dist\halo_pack_editor-0.5.1.zip
```

Background Blender integration tests are under `scripts/`. Development and render caches belong under `F:\codex-cache\halo-blender-addon`; the installed extension does not depend on that path.

The current release passes 51 pure-Python tests, Blender 5.2 background integration and save/reopen tests, EEVEE/Cycles render checks, and Java parser validation against five native Mesh definitions.

See [compatibility details](docs/COMPATIBILITY.md), the [test report](docs/TEST_REPORT.md), and [known limitations](docs/KNOWN_ISSUES.md).
