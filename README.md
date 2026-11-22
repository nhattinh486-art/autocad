# AutoCAD AI Swarm & CAD-MCP — Bộ cài đặt trọn gói tự động hóa AutoCAD bằng AI

Kho mã nguồn **tất cả trong một (All-in-One)** tích hợp sẵn **CAD-MCP Server**, **Custom Skill (`autocad-swarm`)**, **script cài đặt 1 chạm** và **trình biên dịch JSON → AutoLISP** giúp bạn điều khiển **AutoCAD** tự động dựng bản vẽ 2D từ ảnh mẫu bằng **Google Antigravity (Swarm Multi-Agent)**.

> **Ghi nhận mã nguồn mở**: Thư mục [`cad-mcp/`](./cad-mcp/) tích hợp mã nguồn [CAD-MCP](https://github.com/daobataotie/CAD-MCP) (v2.0.0, giữ nguyên giấy phép gốc trong `cad-mcp/LICENSE`).

---

## 1. Cấu trúc kho mã nguồn

```text
autocad/
├── README.md                        # Hướng dẫn cài đặt & sử dụng trọn gói
├── cad-mcp/                         # Mã nguồn CAD-MCP Server (đã tích hợp sẵn)
│   ├── src/server.py                # MCP server (35 công cụ điều khiển AutoCAD)
│   ├── requirements.txt             # mcp, pywin32, pydantic, Pillow
│   └── LICENSE
├── skills/
│   └── autocad-swarm/
│       └── SKILL.md                 # Custom Skill điều phối đội Swarm vẽ AutoCAD
├── scripts/
│   ├── install.ps1                  # Script cài đặt tự động 1 chạm (Windows)
│   └── json_to_lsp.py               # Biên dịch JSON → AutoLISP (đủ dấu tiếng Việt \U+XXXX)
├── config/
│   └── mcp_config.example.json      # Mẫu cấu hình MCP cho Antigravity
└── prompts/
    └── sample_prompt.md             # Mẫu câu lệnh (prompt) để chạy ngay
```

---

## 2. Yêu cầu hệ thống

- **Windows 10 / 11** (bắt buộc vì CAD-MCP kết nối AutoCAD qua Windows COM)
- **AutoCAD** (hoặc GstarCAD / ZWCAD)
- **Python >= 3.10** (tick *Add Python to PATH* khi cài)
- **Git** và **Google Antigravity**

---

## 3. Hướng dẫn cài đặt chi tiết

### Cách 1: Cài đặt tự động chỉ với 1 lệnh (Khuyên dùng)

Mở **PowerShell** và chạy:
```powershell
git clone https://github.com/nhattinh486-art/autocad.git
cd autocad
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Script `install.ps1` sẽ tự động làm toàn bộ các bước sau:
1. Tạo môi trường ảo `cad-mcp\.venv` và cài đầy đủ thư viện từ `cad-mcp\requirements.txt`.
2. Sao lưu (`.bak`) và tự động ghi cấu hình MCP server `autocad` vào `%USERPROFILE%\.gemini\config\mcp_config.json`.
3. Cài đặt Custom Skill `autocad-swarm` vào `%USERPROFILE%\.gemini\antigravity\skills\autocad-swarm\SKILL.md`.

---

### Cách 2: Cài đặt thủ công từng bước

#### Bước 1: Tải repo và cài thư viện cho `cad-mcp`
```powershell
git clone https://github.com/nhattinh486-art/autocad.git
cd autocad\cad-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

#### Bước 2: Khai báo MCP vào Antigravity
Mở (hoặc tạo mới) file:
```text
C:\Users\<TenUser>\.gemini\config\mcp_config.json
```
Dán nội dung sau (thay `C:\\duong\\dan\\toi\\autocad` bằng đường dẫn thật trên máy bạn):
```json
{
  "mcpServers": {
    "autocad": {
      "command": "C:\\duong\\dan\\toi\\autocad\\cad-mcp\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\duong\\dan\\toi\\autocad\\cad-mcp\\src\\server.py"
      ],
      "env": {
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

#### Bước 3: Cài Custom Skill `autocad-swarm`
Chép thư mục `skills\autocad-swarm` vào:
- **Dùng chung mọi dự án**: `C:\Users\<TenUser>\.gemini\antigravity\skills\autocad-swarm\SKILL.md`
- **Hoặc riêng 1 dự án**: `<ThuMucDuAn>\.agent\skills\autocad-swarm\SKILL.md`

---

## 4. Kiểm tra kết nối & cách sử dụng

1. Mở sẵn **AutoCAD** với một bản vẽ trống (`Drawing1.dwg`).
2. Khởi động lại **Antigravity** để nạp MCP `autocad` và skill `autocad-swarm`.
3. Trong khung chat Antigravity, nhắn thử:
   > *"Kiểm tra kết nối AutoCAD giúp tôi bằng `list_layers`"*
   - Nếu trả về danh sách có layer `0` → **Đã kết nối thành công!**
4. Tải ảnh bản vẽ mẫu lên chat và dùng các mẫu prompt có sẵn trong [`prompts/sample_prompt.md`](./prompts/sample_prompt.md) (hoặc dùng lệnh `/teamwork` để kích hoạt Swarm).

---

## 5. Tổng quan công cụ & kiến trúc quy trình

- **35 công cụ CAD-MCP**:
  - *Vẽ*: `draw_line`, `draw_circle`, `draw_arc`, `draw_ellipse`, `draw_polyline`, `draw_rectangle`, `draw_hatch`, `draw_text`, `add_dimension`
  - *Chỉnh sửa*: `move_entity`, `copy_entity`, `rotate_entity`, `scale_entity`, `mirror_entity`, `offset_entity`, `array_linear_entity`, `array_polar_entity`, `erase_entity`, `undo`, `redo`
  - *Layer / Block / File*: `create_layer`, `set_current_layer`, `list_layers`, `create_block`, `insert_block`, `list_blocks`, `list_entities`, `get_entity_properties`, `new_drawing`, `open_drawing`, `save_drawing`, `close_drawing`, `screenshot`, `send_command`
- **Quy trình Swarm chuẩn**:
  ```text
  Ảnh mẫu → Swarm Authors xuất JSON tọa độ → Verifier kiểm tra va chạm
          → Coordinator biên dịch JSON → AutoLISP (\U+XXXX) & nạp vào AutoCAD
          → Chụp screenshot để Verifier soi lỗi → Lưu file .dwg hoàn chỉnh
  ```

---

## 6. Xử lý lỗi thường gặp

| Lỗi | Cách khắc phục |
| :--- | :--- |
| Không kết nối được AutoCAD | Mở AutoCAD trước; đảm bảo Antigravity và AutoCAD chạy cùng quyền (đều thường hoặc đều Administrator). |
| Lỗi dấu tiếng Việt trên AutoCAD | Dùng `scripts/json_to_lsp.py` để tự chuyển sang mã Unicode AutoCAD (`\U+XXXX`). |
| Worker Swarm báo thiếu tool AutoCAD | Cơ chế chuẩn: chỉ `coordinator` gọi MCP; worker xuất JSON để `coordinator` thực thi. |
| Nét trục/nét đứt hiện thành nét liền | Tăng `LTSCALE` (mặc định script đặt `LTSCALE = 40` cho bản vẽ mm tỷ lệ 1/100). |
