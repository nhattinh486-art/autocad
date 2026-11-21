# AutoCAD AI Swarm & CAD-MCP Guide

Hướng dẫn và bộ công cụ mẫu để điều khiển **AutoCAD** tự động vẽ bản vẽ kiến trúc từ ảnh mẫu bằng **Antigravity Swarm** (nhiều AI Agent phối hợp) kết hợp **CAD-MCP**.

## 1. Kiến trúc hoạt động

```text
Ảnh mặt bằng mẫu (.jpg/.png)
        │
        ▼
┌─────────────────────────────────────────┐
│        Antigravity Swarm (Team)         │
│  • struct-author : Lưới trục, tường/cột │
│  • arch-author   : Cửa, thang, nội thất │
│  • annot-author  : Ghi chú, khung tên   │
│  • verifier      : Soi va chạm & ảnh CAD│
└──────────────────┬──────────────────────┘
                   │ Xuất JSON tọa độ chuẩn
                   ▼
┌─────────────────────────────────────────┐
│     Coordinator + AutoCAD MCP (COM)     │
│  • Thực thi tool MCP / biên dịch .lsp   │
│  • Chụp screenshot để verifier duyệt    │
└──────────────────┬──────────────────────┘
                   ▼
         AutoCAD (.dwg hoàn chỉnh)
```

> **Lưu ý**: Worker trong Swarm không có quyền gọi trực tiếp MCP server. Vì vậy các worker sẽ xuất thông số hình học ra file JSON chuẩn, `verifier` kiểm tra va chạm trước, rồi `coordinator` thực thi lên AutoCAD (trực tiếp qua tool MCP hoặc qua `scripts/json_to_lsp.py` dùng mã `\U+XXXX` để hiển thị tiếng Việt đủ dấu).

---

## 2. Cấu trúc thư mục

- `README.md` — Tài liệu hướng dẫn cài đặt & sử dụng.
- `skills/autocad-swarm/SKILL.md` — Custom Skill cho Antigravity để tái sử dụng quy trình vẽ AutoCAD bằng Swarm.
- `scripts/json_to_lsp.py` — Script Python biên dịch file JSON tọa độ thành AutoLISP (`.lsp`) hỗ trợ đầy đủ dấu tiếng Việt (`\U+XXXX`), hatch `SOLID`/`ANSI31` và đường dim kiến trúc.
- `config/mcp_config.example.json` — File cấu hình mẫu cho `CAD-MCP` trong Antigravity.

---

## 3. Hướng dẫn cài đặt (Windows)

### Bước 1: Yêu cầu hệ thống
- **Windows** đã cài **AutoCAD** (hoặc ZWCAD / GstarCAD)
- **Python >= 3.10**, **Git**
- **Antigravity**

### Bước 2: Cài đặt `CAD-MCP`
Mở PowerShell và chạy:
```powershell
git clone https://github.com/daobataotie/CAD-MCP.git
cd CAD-MCP
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Thư viện phụ thuộc: `mcp>=1.12,<2`, `pywin32>=306`, `pydantic>=2.0.0`, `Pillow>=10.0.0`.

### Bước 3: Khai báo MCP trong Antigravity
Mở file cấu hình MCP của Antigravity tại:
`C:\Users\<TenUser>\.gemini\config\mcp_config.json`

Chép nội dung từ `config/mcp_config.example.json` và thay đường dẫn đúng với thư mục `CAD-MCP` trên máy bạn:
```json
{
  "mcpServers": {
    "autocad": {
      "command": "C:\\path\\to\\CAD-MCP\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\path\\to\\CAD-MCP\\src\\server.py"
      ],
      "env": {
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

### Bước 4: Cài Custom Skill (tuỳ chọn)
Chép thư mục `skills/autocad-swarm` vào thư mục skills của Antigravity (hoặc giữ ngay trong workspace dự án tại `.agent/skills/autocad-swarm/SKILL.md`).

### Bước 5: Kiểm tra & chạy thử
1. Mở **AutoCAD** (tạo 1 bản vẽ trống).
2. Khởi động lại **Antigravity**.
3. Nhắn thử trong chat: *"Kiểm tra kết nối AutoCAD bằng `list_layers`"* — nếu trả về layer `0` là thành công.
4. Gửi ảnh mặt bằng mẫu vào chat và yêu cầu dựng bản vẽ bằng **Swarm** (hoặc dùng lệnh `/teamwork`).

---

## 4. Dùng nhanh script `json_to_lsp.py`

Biên dịch 1 hoặc nhiều file JSON thành file AutoLISP `.lsp`:
```powershell
python scripts/json_to_lsp.py kt01_tang1.json kt02_tang2.json kt03_tmb.json -o draw_all.lsp
```
Sau đó nạp trực tiếp vào AutoCAD bằng lệnh:
```lisp
(load "C:/duong/dan/toi/draw_all.lsp")
```
