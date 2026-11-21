---
name: autocad-swarm
description: >-
  Coordinate a multi-agent Swarm with AutoCAD MCP to draft architectural floor
  plans from reference images into AutoCAD, verify geometry and visual
  screenshots, and save DWG outputs with full Vietnamese Unicode support.
---

# AutoCAD Swarm Drafting Skill

Use this skill when the user wants to draft or recreate architectural floor plans in AutoCAD from reference images using a multi-agent Swarm + `autocad` MCP server.

## Architecture Rule
Swarm workers do **not** have direct access to MCP tools. Therefore:
1. **Coordinator** owns all `autocad` MCP tool calls (`list_layers`, `new_drawing`, `draw_*`, `send_command`, `screenshot`, `save_drawing`).
2. **Author Workers** write structured JSON geometry specs into the workspace using a shared coordinate system (`mm`):
   - `struct-author`: Grid lines, grid bubbles, dimensions, columns (`SOLID` hatch), exterior/interior walls (`ANSI31` hatch) segmented cleanly around openings.
   - `arch-author`: Doors + dashed swing arcs, windows, staircases, furniture, sanitary fixtures, landscaping.
   - `annot-author`: Room labels, areas (`DT`), elevation markers, titles, and sheet title blocks.
3. **Verifier** checks all JSON specs for coordinate alignment and zero overlaps, and inspects AutoCAD `screenshot` PNGs after execution.

## Rendering Best Practices
- **Vietnamese diacritics**: For large drawings (100+ entities), compile the JSON specs into an AutoLISP `.lsp` script using `scripts/json_to_lsp.py` (which encodes non-ASCII characters as AutoCAD `\U+XXXX` escapes and uses `entmake` with `72`/`73` middle-center alignment), then load it via `send_command`:
  ```lisp
  (load "C:/path/to/output.lsp")
  ```
- **Visual Verification**: Always run `_.ZOOM _E` and `screenshot` after drawing, inspect the image (and have `verifier` inspect it), fix any text overlaps or misalignments, and save with `save_drawing`.
