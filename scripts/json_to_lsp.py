#!/usr/bin/env python3
"""Compile one or more AutoCAD JSON drawing specs into a single AutoLISP (.lsp) script.

Supports:
- Layers with ACI colors and CENTER/DASHED/Continuous linetypes
- Geometry: draw_line, draw_circle, draw_arc, draw_rectangle, draw_polyline
- Hatches: draw_hatch (SOLID, ANSI31, etc. via points or boundary_id)
- Vietnamese Unicode text via AutoCAD \\U+XXXX escapes with middle_center/left alignment
- Clean architectural dimensions (extension lines, 45-degree ticks, centered text)
"""

import argparse
import json
import math
from pathlib import Path


def to_acad_unicode(s: str) -> str:
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(f"\\U+{ord(ch):04X}")
    return "".join(out)


def compile_sheets(sheets_data: list[dict]) -> str:
    lsp = [
        '(setvar "CMDECHO" 0)',
        '(setvar "OSMODE" 0)',
        '(if (not (tblsearch "LTYPE" "CENTER")) (command "_.LINETYPE" "_L" "CENTER" "acadiso.lin" ""))',
        '(if (not (tblsearch "LTYPE" "CENTER")) (command "_.LINETYPE" "_L" "CENTER" "acad.lin" ""))',
        '(if (not (tblsearch "LTYPE" "DASHED")) (command "_.LINETYPE" "_L" "DASHED" "acadiso.lin" ""))',
        '(if (not (tblsearch "LTYPE" "DASHED")) (command "_.LINETYPE" "_L" "DASHED" "acad.lin" ""))',
        '(setvar "LTSCALE" 40)',
    ]
    created_layers: set[str] = set()

    for data in sheets_data:
        ox, oy = data.get("offset", [0, 0])
        ops = data.get("operations", [])

        rects_by_id = {}
        for o in ops:
            if o.get("op") == "draw_rectangle" and o.get("id"):
                c1, c2 = o["corner1"], o["corner2"]
                rects_by_id[o["id"]] = [
                    [c1[0], c1[1]],
                    [c2[0], c1[1]],
                    [c2[0], c2[1]],
                    [c1[0], c2[1]],
                ]

        for o in ops:
            op = o.get("op")
            if op == "create_layer":
                name = o["name"]
                if name in created_layers:
                    continue
                created_layers.add(name)
                col = int(o.get("color", 7))
                lt = o.get("linetype", "Continuous")
                lsp.append(
                    f'(if (not (tblsearch "LAYER" "{name}")) '
                    f'(command "_.-LAYER" "_M" "{name}" "_C" "{col}" "{name}" "_L" "{lt}" "{name}" "") '
                    f'(command "_.-LAYER" "_C" "{col}" "{name}" "_L" "{lt}" "{name}" ""))'
                )
            elif op == "draw_line":
                layer = o.get("layer", "0")
                x1, y1 = o["start"][0] + ox, o["start"][1] + oy
                x2, y2 = o["end"][0] + ox, o["end"][1] + oy
                lsp.append(
                    f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") '
                    f"(list 10 {x1:.2f} {y1:.2f} 0.0) (list 11 {x2:.2f} {y2:.2f} 0.0)))"
                )
            elif op == "draw_circle":
                layer = o.get("layer", "0")
                cx, cy = o["center"][0] + ox, o["center"][1] + oy
                r = o["radius"]
                lsp.append(
                    f'(entmake (list \'(0 . "CIRCLE") \'(8 . "{layer}") '
                    f"(list 10 {cx:.2f} {cy:.2f} 0.0) (cons 40 {r:.2f})))"
                )
            elif op == "draw_arc":
                layer = o.get("layer", "0")
                cx, cy = o["center"][0] + ox, o["center"][1] + oy
                r = o["radius"]
                sa = math.radians(o["start_angle"])
                ea = math.radians(o["end_angle"])
                lsp.append(
                    f'(entmake (list \'(0 . "ARC") \'(8 . "{layer}") '
                    f"(list 10 {cx:.2f} {cy:.2f} 0.0) (cons 40 {r:.2f}) "
                    f"(cons 50 {sa:.6f}) (cons 51 {ea:.6f})))"
                )
            elif op == "draw_rectangle":
                layer = o.get("layer", "0")
                x1, y1 = o["corner1"][0] + ox, o["corner1"][1] + oy
                x2, y2 = o["corner2"][0] + ox, o["corner2"][1] + oy
                lsp.append(
                    f'(entmake (list \'(0 . "LWPOLYLINE") \'(100 . "AcDbEntity") \'(8 . "{layer}") '
                    f'\'(100 . "AcDbPolyline") \'(90 . 4) \'(70 . 1) '
                    f"(list 10 {x1:.2f} {y1:.2f}) (list 10 {x2:.2f} {y1:.2f}) "
                    f"(list 10 {x2:.2f} {y2:.2f}) (list 10 {x1:.2f} {y2:.2f})))"
                )
            elif op == "draw_polyline":
                layer = o.get("layer", "0")
                pts = o["points"]
                closed = 1 if o.get("closed", False) else 0
                pts_str = " ".join(f"(list 10 {p[0]+ox:.2f} {p[1]+oy:.2f})" for p in pts)
                lsp.append(
                    f'(entmake (list \'(0 . "LWPOLYLINE") \'(100 . "AcDbEntity") \'(8 . "{layer}") '
                    f'\'(100 . "AcDbPolyline") (cons 90 {len(pts)}) (cons 70 {closed}) {pts_str}))'
                )
            elif op == "draw_hatch":
                layer = o.get("layer", "WALL_HATCH")
                pat = o.get("pattern", o.get("pattern_name", "SOLID")).upper()
                scale = o.get("scale", 50)
                pts = o.get("points") or rects_by_id.get(o.get("boundary_id"))
                if not pts or len(pts) < 3:
                    continue
                pts_str = " ".join(f"(list 10 {p[0]+ox:.2f} {p[1]+oy:.2f})" for p in pts)
                lsp.append(f'(setvar "CLAYER" "{layer}")')
                lsp.append(
                    f'(entmake (list \'(0 . "LWPOLYLINE") \'(100 . "AcDbEntity") \'(8 . "{layer}") '
                    f'\'(100 . "AcDbPolyline") (cons 90 {len(pts)}) \'(70 . 1) {pts_str}))'
                )
                lsp.append("(setq _hb (entlast))")
                if pat == "SOLID":
                    lsp.append('(command "_.-HATCH" "_P" "SOLID" "_S" _hb "" "")')
                else:
                    lsp.append(f'(command "_.-HATCH" "_P" "{pat}" {scale} 0 "_S" _hb "" "")')
                lsp.append("(entdel _hb)")
            elif op == "draw_text":
                layer = o.get("layer", "A-ANNO-TEXT")
                x, y = o["position"][0] + ox, o["position"][1] + oy
                h = o.get("height", 180)
                txt = to_acad_unicode(str(o.get("text", "")))
                align = o.get("align", "left")
                rot = math.radians(o.get("rotation", 0))
                if align in ("middle_center", "center"):
                    lsp.append(
                        f'(entmake (list \'(0 . "TEXT") \'(8 . "{layer}") '
                        f"(list 10 {x:.2f} {y:.2f} 0.0) (cons 40 {h:.2f}) "
                        f'(cons 1 "{txt}") (cons 50 {rot:.6f}) \'(72 . 1) \'(73 . 2) '
                        f"(list 11 {x:.2f} {y:.2f} 0.0)))"
                    )
                else:
                    lsp.append(
                        f'(entmake (list \'(0 . "TEXT") \'(8 . "{layer}") '
                        f"(list 10 {x:.2f} {y:.2f} 0.0) (cons 40 {h:.2f}) "
                        f'(cons 1 "{txt}") (cons 50 {rot:.6f})))'
                    )
            elif op == "add_dimension":
                layer = o.get("layer", "DIM")
                sx, sy = o["start"][0] + ox, o["start"][1] + oy
                ex, ey = o["end"][0] + ox, o["end"][1] + oy
                lp = o.get("line_point") or [(sx + ex) / 2 - ox, (sy + ey) / 2 - oy]
                lx, ly = lp[0] + ox, lp[1] + oy
                dx, dy = abs(ex - sx), abs(ey - sy)
                val = int(round(math.hypot(ex - sx, ey - sy)))
                txt = to_acad_unicode(str(o.get("text", val)))
                tick = 80.0
                if dx >= dy:
                    lsp.append(f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") (list 10 {sx:.2f} {ly:.2f} 0.0) (list 11 {ex:.2f} {ly:.2f} 0.0)))')
                    lsp.append(f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") (list 10 {sx:.2f} {sy:.2f} 0.0) (list 11 {sx:.2f} {ly + (150 if ly >= sy else -150):.2f} 0.0)))')
                    lsp.append(f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") (list 10 {ex:.2f} {ey:.2f} 0.0) (list 11 {ex:.2f} {ly + (150 if ly >= ey else -150):.2f} 0.0)))')
                    for tx in (sx, ex):
                        lsp.append(f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") (list 10 {tx-tick:.2f} {ly-tick:.2f} 0.0) (list 11 {tx+tick:.2f} {ly+tick:.2f} 0.0)))')
                    mx = (sx + ex) / 2.0
                    lsp.append(
                        f'(entmake (list \'(0 . "TEXT") \'(8 . "{layer}") '
                        f"(list 10 {mx:.2f} {ly+140:.2f} 0.0) '(40 . 180.0) "
                        f'(cons 1 "{txt}") \'(72 . 1) \'(73 . 2) (list 11 {mx:.2f} {ly+140:.2f} 0.0)))'
                    )
                else:
                    lsp.append(f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") (list 10 {lx:.2f} {sy:.2f} 0.0) (list 11 {lx:.2f} {ey:.2f} 0.0)))')
                    lsp.append(f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") (list 10 {sx:.2f} {sy:.2f} 0.0) (list 11 {lx + (150 if lx >= sx else -150):.2f} {sy:.2f} 0.0)))')
                    lsp.append(f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") (list 10 {ex:.2f} {ey:.2f} 0.0) (list 11 {lx + (150 if lx >= ex else -150):.2f} {ey:.2f} 0.0)))')
                    for ty in (sy, ey):
                        lsp.append(f'(entmake (list \'(0 . "LINE") \'(8 . "{layer}") (list 10 {lx-tick:.2f} {ty-tick:.2f} 0.0) (list 11 {lx+tick:.2f} {ty+tick:.2f} 0.0)))')
                    my = (sy + ey) / 2.0
                    rot = math.pi / 2.0
                    lsp.append(
                        f'(entmake (list \'(0 . "TEXT") \'(8 . "{layer}") '
                        f"(list 10 {lx-140:.2f} {my:.2f} 0.0) '(40 . 180.0) "
                        f'(cons 1 "{txt}") (cons 50 {rot:.6f}) \'(72 . 1) \'(73 . 2) (list 11 {lx-140:.2f} {my:.2f} 0.0)))'
                    )

    lsp.append('(setvar "CLAYER" "0")')
    lsp.append('(command "_.ZOOM" "_E")')
    lsp.append('(princ "DONE")')
    return "\n".join(lsp) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Compile AutoCAD JSON specs to AutoLISP (.lsp)")
    parser.add_argument("inputs", nargs="+", help="Input JSON files")
    parser.add_argument("-o", "--output", default="output.lsp", help="Output .lsp path")
    args = parser.parse_args()

    sheets = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.inputs]
    lsp_code = compile_sheets(sheets)
    Path(args.output).write_text(lsp_code, encoding="ascii")
    print(f"Wrote {len(lsp_code.splitlines())} lines to {args.output}")


if __name__ == "__main__":
    main()
