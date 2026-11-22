---
name: autocad-swarm
description: >-
  Coordinate a multi-agent Swarm with AutoCAD MCP to draft architectural floor
  plans from reference images into AutoCAD, verify geometry and visual
  screenshots, and save DWG outputs with full Vietnamese Unicode support.
---

# AutoCAD Swarm Drafting Skill

Use this skill when the user wants to draft or recreate architectural floor plans in AutoCAD from reference images using a multi-agent Swarm + `autocad` MCP server.

## 1. Architecture & Permissions Rule
Swarm workers do **not** have direct access to MCP tools (`autocad`). Always follow this split:
1. **Coordinator** (you):
   - Verifies AutoCAD connection via `list_layers` before starting.
   - Defines a single shared coordinate system (in `mm`, origin `(0, 0)` at bottom-left grid intersection) in every worker's brief.
   - Executes the verified JSON specs in AutoCAD via `autocad` MCP (`send_command`, `draw_*`, `screenshot`, `save_drawing`).
2. **Author Workers** (run in parallel, writing JSON files in workspace):
   - `struct-author`: Writes grid lines, grid bubbles, dimensions, columns (`SOLID` hatch), exterior/interior walls (`ANSI31` hatch) segmented cleanly around door/window openings.
   - `arch-author`: Writes doors + dashed swing arcs, windows, staircases, furniture, sanitary fixtures, landscaping.
   - `annot-author`: Writes room labels (with area `DT`), elevation markers, stair step numbers, drawing titles, and sheet title blocks.
   *(For multi-sheet projects such as KT-01, KT-02, KT-03, assign 1 author per sheet with distinct `offset: [ox, 0]`.)*
3. **Verifier** (`verifier`):
   - Validates all JSON specs for coordinate alignment, wall/door opening match, and zero overlaps/collisions before execution.
   - Inspects AutoCAD `screenshot` PNGs via `read_file` after execution and reports any visual defects (overlapping text, cut-off labels, missing hatches).
4. **Monitor** (`monitor`):
   - Runs `swarm_monitor` if available; coordinator posts `[done] <summary>` (without `@`) once finished.

## 2. Standard JSON Operation Schema
All authors must emit JSON matching this format so `scripts/json_to_lsp.py` can compile it cleanly:
```json
{
  "sheet": "KT-01",
  "offset": [0, 0],
  "operations": [
    {"op": "create_layer", "name": "WALL_EXT", "color": 7, "linetype": "Continuous"},
    {"op": "draw_line", "layer": "GRID", "start": [0, -2000], "end": [0, 12000]},
    {"op": "draw_circle", "layer": "GRID_BUBBLE", "center": [0, 12350], "radius": 350},
    {"op": "draw_arc", "layer": "A-DOOR-SWING", "center": [1800, 2900], "radius": 800, "start_angle": 90, "end_angle": 180},
    {"op": "draw_rectangle", "id": "col_1A", "layer": "COLUMN", "corner1": [-110, -110], "corner2": [110, 110]},
    {"op": "draw_polyline", "layer": "A-STAIR", "closed": false, "points": [[0, 0], [0, 2000], [1000, 2000]]},
    {"op": "draw_hatch", "layer": "COLUMN_HATCH", "pattern": "SOLID", "scale": 1, "boundary_id": "col_1A"},
    {"op": "draw_text", "layer": "A-ANNO-TEXT", "position": [2200, 1500], "text": "PHÒNG NGỦ 1", "height": 180, "align": "middle_center"},
    {"op": "add_dimension", "layer": "DIM", "start": [0, 4200], "end": [4800, 4200], "line_point": [2400, 5150]}
  ]
}
```

## 3. Execution & Visual Verification Checklist
1. **Compile to AutoLISP for speed & Vietnamese diacritics**:
   Run `scripts/json_to_lsp.py <files...> -o output.lsp` (encodes Vietnamese characters as AutoCAD `\U+XXXX` escapes and uses `entmake` with `72`/`73` alignment), then load via `send_command`:
   ```lisp
   (load "C:/path/to/output.lsp")
   ```
2. **Zoom & Screenshot**:
   Run `send_command` with `_.ZOOM _E`, then call `screenshot`.
3. **Inspect & Polish**:
   Read the screenshot PNG (and ask `@verifier` to inspect it). Fix any overlapping text or alignment issues using `erase_entity` + `draw_text` (or regenerate `.lsp`), re-verify, and save with `save_drawing`.
