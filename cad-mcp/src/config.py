"""Module nạp cấu hình: singleton toàn cục

Trước đây config.json bị đọc lặp lại ở ba nơi server.py / cad_controller.py / nlp_processor.py,
nay được gom về module này để toàn bộ tiến trình chỉ nạp cấu hình một lần duy nhất.
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger('cad_mcp_config')

# Tệp cấu hình nằm cùng thư mục với module này
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

# Cấu hình mặc định (dự phòng khi tệp cấu hình bị thiếu hoặc hỏng, giữ nguyên hành vi __init__.py cũ)
_DEFAULT_CONFIG = {
    "server": {
        "name": "Máy chủ CAD MCP",
        "version": "2.0.0",
        "transport": "stdio",
        "host": "127.0.0.1",
        "port": 8000
    },
    "cad": {
        "type": "AUTOCAD",
        "startup_wait_time": 20,
        "command_delay": 0.5
    },
    "output": {
        "directory": "./output",
        "default_filename": "cad_drawing.dwg"
    }
}


@lru_cache(maxsize=1)
def get_config() -> dict:
    """Nạp và lưu đệm tệp cấu hình (singleton trong tiến trình)"""
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info("Nạp tệp cấu hình thành công")
        return config
    except Exception as e:
        logger.error(f"Nạp tệp cấu hình thất bại: {str(e)}, sử dụng cấu hình mặc định")
        return _DEFAULT_CONFIG
