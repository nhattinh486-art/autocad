# Floor Plan Specification — Mặt Bằng Lầu 1 (TL: 1/100)

- **Units:** Millimeters (mm)
- **Origin `(0, 0)`:** Intersection of **Axis 1** and **Axis A**

## 1. Grid System

### X-Axes (Vertical Grid Lines)
| Axis | X (mm) | Span from Previous (mm) | Notes |
| --- | ---: | ---: | --- |
| Balcony Left | `-1400` | — | Centerline of left balcony parapet |
| Axis 1 | `0` | `1400` | Exterior wall & double-door entrance |
| Axis 2 | `4800` | `4800` | Partition between Phòng ngủ 2 & Phòng ngủ 1 |
| Axis 3 | `9600` | `4800` | Partition between WC 2 & Sảnh thang |
| Axis 4 | `12200` | `2600` | Right exterior wall (Total 1–4 = `12200`) |

### Y-Axes (Horizontal Grid Lines)
| Axis | Y (mm) | Span from Previous (mm) | Notes |
| --- | ---: | ---: | --- |
| Axis A | `0` | — | Bottom exterior wall (Total A–B = `4200`) |
| Axis B | `4200` | `4200` | Top exterior wall |

## 2. Structural Columns (`200 × 200` mm, Solid Hatch)
All 8 columns are centered at grid intersections `(X, Y)` with bounds `[X-100, X+100] × [Y-100, Y+100]`:
- `C_A1`: `(0, 0)` → `x: -100..100, y: -100..100`
- `C_B1`: `(0, 4200)` → `x: -100..100, y: 4100..4300`
- `C_A2`: `(4800, 0)` → `x: 4700..4900, y: -100..100`
- `C_B2`: `(4800, 4200)` → `x: 4700..4900, y: 4100..4300`
- `C_A3`: `(9600, 0)` → `x: 9500..9700, y: -100..100`
- `C_B3`: `(9600, 4200)` → `x: 9500..9700, y: 4100..4300`
- `C_A4`: `(12200, 0)` → `x: 12100..12300, y: -100..100`
- `C_B4`: `(12200, 4200)` → `x: 12100..12300, y: 4100..4300`

## 3. Exterior Walls (`200` mm thick, `ANSI31` Diagonal Hatch)
Segmented between columns to guarantee zero geometric overlap:
- **Axis A (`y: -100..100`):**
  - `W_A_1_2`: `x: 100..4700, y: -100..100`
  - `W_A_2_3`: `x: 4900..9500, y: -100..100`
  - `W_A_3_4`: `x: 9700..12100, y: -100..100`
- **Axis B (`y: 4100..4300`):**
  - `W_B_1_2`: `x: 100..4700, y: 4100..4300`
  - `W_B_2_3`: `x: 4900..9500, y: 4100..4300`
  - `W_B_3_4`: `x: 9700..12100, y: 4100..4300`
- **Axis 4 (`x: 12100..12300`):**
  - `W_4_AB`: `x: 12100..12300, y: 100..4100`
- **Axis 1 (`x: -100..100`, double-door opening `y: 1100..2500`):**
  - `W_1_lower`: `x: -100..100, y: 100..1100`
  - `W_1_upper`: `x: -100..100, y: 2500..4100`

## 4. Balcony Parapet (`100` mm thick, `ANSI31` Diagonal Hatch)
Centered on `x = -1400`, `y = 0`, `y = 4200`, stopping cleanly at corners and Axis 1 columns:
- `P_left`: `x: -1450..-1350, y: -50..4250`
- `P_bottom`: `x: -1350..-100, y: -50..50`
- `P_top`: `x: -1350..-100, y: 4150..4250`

## 5. Interior Walls (`100` mm thick, `ANSI31` Diagonal Hatch)
Segmented around door openings and intersections for zero overlap:
- **Axis 2 wall (`x: 4750..4850`):**
  - `W_2_main`: `x: 4750..4850, y: 1000..4100` (door opening `y: 100..1000`)
- **Corridor wall (`y: 1050..1150`):**
  - `W_corr_1`: `x: 4850..7200, y: 1050..1150` (Phòng ngủ 1 door opening `x: 7200..8000`)
  - `W_corr_2`: `x: 8000..8350, y: 1050..1150` (WC 2 door opening `x: 8350..9050`)
  - `W_corr_3`: `x: 9050..9550, y: 1050..1150`
- **WC 2 partition (`x: 8150..8250`):**
  - `W_wc2`: `x: 8150..8250, y: 1150..4100`
- **Axis 3 wall (`x: 9550..9650`):**
  - `W_3_main`: `x: 9550..9650, y: 1100..4100` (corridor opening `y: 100..1100`)

## 6. Room Clear Bounds (for Reference)
- **Ban công:** `x: -1350..-100, y: 50..4150`
- **Phòng ngủ 2:** `x: 100..4750, y: 100..4100`
- **Phòng ngủ 1:** `x: 4850..8150, y: 1150..4100`
- **WC 2:** `x: 8250..9550, y: 1150..4100`
- **Hành lang (Corridor):** `x: 4850..9650, y: 100..1050`
- **Sảnh thang & Cầu thang:** `x: 9650..12100, y: 100..4100`
