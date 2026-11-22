# AutoCAD AI Swarm & CAD-MCP — Bộ cài đặt & Hướng dẫn tự động hóa AutoCAD bằng AI

Bộ công cụ, **Custom Skill**, cấu hình **CAD-MCP** và script hỗ trợ giúp bạn dùng **Google Antigravity (Swarm Multi-Agent)** kết hợp **AutoCAD** để tự động dựng bản vẽ mặt bằng 2D từ ảnh mẫu (đầy đủ lưới trục, tường/cột/hatch, cửa, cầu thang, nội thất, dim kích thước, khung tên và tiếng Việt đủ dấu).

---

## 1. Tổng hợp các thành phần cần thiết

### A. Phần mềm nền tảng (Yêu cầu hệ thống)
- **Hệ điều hành**: Windows 10 / 11 (bắt buộc vì CAD-MCP giao tiếp qua Windows COM)
- **Phần mềm CAD**: AutoCAD (hoặc GstarCAD / ZWCAD)
- **Python**: Phiên bản `>= 3.10` (nhớ tick *Add Python to PATH* khi cài)
- **Git**: Để tải mã nguồn
- **Google Antigravity**: Nền tảng chạy AI Agent & Swarm

### B. MCP Server: `CAD-MCP` (v2.0.0)
- **Kho mã nguồn**: https://github.com/daobataotie/CAD-MCP
- **Thư viện Python**: `mcp>=1.12,<2`, `pywin32>=306`, `pydantic>=2.0.0`, `Pillow>=10.0.0`
- **35 công cụ điều khiển AutoCAD**:
  - *Vẽ hình học*: `draw_line`, `draw_circle`, `draw_arc`, `draw_ellipse`, `draw_polyline`, `draw_rectangle`, `draw_hatch`, `draw_text`, `add_dimension`
  - *Chỉnh sửa*: `move_entity`, `copy_entity`, `rotate_entity`, `scale_entity`, `mirror_entity`, `offset_entity`, `array_linear_entity`, `array_polar_entity`, `erase_entity`, `undo`, `redo`
  - *Layer & Block*: `create_layer`, `set_current_layer`, `list_layers`, `create_block`, `insert_block`, `list_blocks`, `list_entities`, `get_entity_properties`
  - *Bản vẽ & Kiểm tra*: `new_drawing`, `open_drawing`, `save_drawing`, `close_drawing`, `screenshot` (chụp cửa sổ CAD cho AI tự soi lỗi), `send_command` (nạp file `.lsp` / chạy lệnh CAD trực tiếp)

### C. Bộ Skills (Kỹ năng cho AI Agent)
| Skill | Loại | Vai trò |
| :--- | :--- | :--- |
| `native_swarm` (`/teamwork`) | Có sẵn trong Antigravity | Chia việc song song cho đội agent trên bảng tin chung (`coordinator`, `struct-author`, `arch-author`, `annot-author`, `verifier`). |
| `swarm_monitor` | Có sẵn trong Antigravity | Giám sát tiến độ toàn đội, tự động nhắc khi có agent bị dừng/chờ. |
| `autocad-swarm` | **Custom Skill trong repo này** (`skills/autocad-swarm/SKILL.md`) | Chuẩn hóa quy trình phối hợp Swarm + CAD-MCP, định dạng JSON tọa độ thống nhất, chuẩn hóa tiếng Việt Unicode (`\U+XXXX`) và bước soi ảnh chụp AutoCAD. |

### D. Kiến trúc quy trình hoạt động
```text
[Trigger] Người dùng gửi ảnh mặt bằng mẫu + mở AutoCAD
   │
   ▼
[Xử lý] Thống nhất hệ tọa độ chung (mm) & phân rã bản vẽ thành các lớp độc lập
   │
   ▼
[AI - Swarm] Đội Agent làm việc song song:
   • struct-author : Lưới trục, dim, cột (SOLID), tường (ANSI31)
   • arch-author   : Cửa + cung mở nét đứt, cầu thang, nội thất, vệ sinh
   • annot-author  : Tên phòng, diện tích, cao độ, tiêu đề, khung tên
   • verifier      : Rà soát va chạm hình học trên JSON trước khi vẽ
   │
   ▼
[Hành động] Coordinator biên dịch JSON → AutoLISP (Unicode \U+XXXX) & nạp qua CAD-MCP
   │
   ▼
[Kết quả] AutoCAD dựng bản vẽ hoàn chỉnh → chụp screenshot để Verifier soi lỗi → lưu file .dwg
```

---

## 2. Cấu trúc thư mục repo

```text
autocad/
├── README.md                        # Hướng dẫn tổng hợp & cài đặt chi tiết
├── config/
│   └── mcp_config.example.json      # Mẫu cấu hình CAD-MCP cho Antigravity
├── skills/
│   └── autocad-swarm/
│       └── SKILL.md                 # Custom Skill điều phối Swarm vẽ AutoCAD
├── scripts/
│   ├── install.ps1                  # Script cài đặt tự động 1 chạm (Windows)
│   └── json_to_lsp.py               # Trình biên dịch JSON → AutoLISP (đủ dấu tiếng Việt)
└── prompts/
    └── sample_prompt.md             # Mẫu prompt để chạy ngay sau khi cài
```

---

## 3. Hướng dẫn cài đặt (Windows)

### Cách 1: Cài tự động nhanh bằng 1 lệnh (Khuyên dùng)
Mở **PowerShell** và chạy:
```powershell
git clone https://github.com/nhattinh486-art/autocad.git
cd autocad
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```
Script `install.ps1` sẽ tự động:
1. Tải `CAD-MCP` về `%USERPROFILE%\.gemini\cad-mcp` và cài môi trường ảo `.venv` + toàn bộ thư viện.
2. Sao lưu và thêm cấu hình `autocad` vào `%USERPROFILE%\.gemini\config\mcp_config.json`.
3. Cài đặt Custom Skill `autocad-swarm` vào `%USERPROFILE%\.gemini\antigravity\skills\autocad-swarm\SKILL.md`.

---

### Cách 2: Cài thủ công từng bước

#### Bước 1: Tải và cài `CAD-MCP`
```powershell
git clone https://github.com/daobataotie/CAD-MCP.git
cd CAD-MCP
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

#### Bước 2: Khai báo MCP vào Antigravity
Mở (hoặc tạo mới) file:
```text
C:\Users\<TenUser>\.gemini\config\mcp_config.json
```
Thêm cấu hình từ `config/mcp_config.example.json` (thay đúng đường dẫn `CAD-MCP` trên máy bạn):
```json
{
  "mcpServers": {
    "autocad": {
      "command": "C:\\duong\\dan\\toi\\CAD-MCP\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\duong\\dan\\toi\\CAD-MCP\\src\\server.py"
      ],
      "env": {
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

#### Bước 3: Cài Custom Skill `autocad-swarm`
Chép thư mục `skills/autocad-swarm` vào **1 trong 2 vị trí**:
- **Dùng cho mọi dự án (Global)**: `C:\Users\<TenUser>\.gemini\antigravity\skills\autocad-swarm\SKILL.md`
- **Chỉ dùng trong 1 thư mục dự án (Workspace)**: `<ThuMucDuAn>\.agent\skills\autocad-swarm\SKILL.md`

---

## 4. Kiểm tra kết nối & sử dụng

1. Mở sẵn **AutoCAD** và tạo 1 bản vẽ trống (`Drawing1.dwg`).
2. Khởi động lại **Antigravity** để nạp MCP `autocad` và skill `autocad-swarm`.
3. Mở chat trong Antigravity và test kết nối:
   > *"Kiểm tra kết nối AutoCAD giúp tôi bằng `list_layers`"*
   - Nếu trả về danh sách có layer `0` → **Kết nối thành công!**
4. Gửi ảnh mặt bằng mẫu vào khung chat và dùng các câu lệnh mẫu trong [prompts/sample_prompt.md](prompts/sample_prompt.md).

### Dùng trực tiếp script `json_to_lsp.py` (tuỳ chọn)
```powershell
python scripts/json_to_lsp.py step_01_grid.json step_02_structure.json -o draw_all.lsp
```
Sau đó gõ trong dòng lệnh AutoCAD:
```lisp
(load "C:/duong/dan/toi/draw_all.lsp")
```

---

## 5. Xử lý lỗi thường gặp (Troubleshooting)

| Lỗi gặp phải | Nguyên nhân & Cách khắc phục |
| :--- | :--- |
| **MCP không kết nối được AutoCAD** | Mở AutoCAD trước khi gọi tool; đảm bảo chạy Antigravity và AutoCAD cùng mức quyền (không để 1 bên Run as Administrator, 1 bên thường). |
| **Chữ tiếng Việt trên AutoCAD bị lỗi dấu** | Dùng `scripts/json_to_lsp.py` để tự động chuyển tiếng Việt sang mã Unicode AutoCAD (`\U+XXXX`), hoặc dùng font hỗ trợ Unicode (Arial / Times New Roman) trong Text Style của AutoCAD. |
| **Worker trong Swarm báo không có tool AutoCAD** | Đây là cơ chế phân quyền chuẩn: chỉ `coordinator` gọi MCP. Các worker xuất file JSON, `coordinator` biên dịch/thực thi lên AutoCAD. |
| **Nét đứt (`DASHED`) / nét trục (`CENTER`) hiển thị như nét liền** | Tăng biến `LTSCALE` trong AutoCAD (mặc định script đặt `LTSCALE = 40`, phù hợp bản vẽ đơn vị mm tỷ lệ 1/100). |
