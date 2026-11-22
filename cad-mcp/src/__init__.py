"""
Gói dịch vụ CAD MCP
"""

import logging

# Lưu ý: tuyệt đối không gọi logging.basicConfig ở đây —
# Khi chạy theo dạng package (cổng vào cad-mcp nhập src.server), module này chạy trước server.py,
# basicConfig sẽ gắn trước StreamHandler mặc định cho root logger,
# khiến cấu hình log trong server.py (gồm FileHandler) bị bỏ qua âm thầm
# (basicConfig không có tác dụng khi root đã có handler) và cad_mcp.log sẽ luôn trống.
# Cấu hình log được thực hiện thống nhất tại điểm vào server.py.

logger = logging.getLogger('cad_mcp')

# Dùng thống nhất singleton cấu hình từ config.py (có cấu hình mặc định dự phòng khi tải lỗi)
try:
    from .config import get_config
    config = get_config()
except ImportError:
    from config import get_config
    config = get_config()

__all__ = [
    'config'
]
