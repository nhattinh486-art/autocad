import os
import sys
import json
import base64
import logging
import argparse
from typing import Annotated, Any, Dict, List, Optional, Union

from pydantic import Field

sys.dont_write_bytecode = True

# Tương thích hai cách chạy:
# 1. Import dưới dạng package (sau khi pip install, tên module là src.server)
# 2. Chạy trực tiếp src/server.py (giữ tương thích với cấu hình Claude/Cursor cũ)
if __package__:
    from .cad_controller import CADController
    from .config import get_config
    from .models import (ToolResult, ListEntitiesResult,
                         ListLayersResult, EntityPropertiesResult, ListBlocksResult)
    from .nlp_processor import NLPProcessor
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cad_controller import CADController
    from config import get_config
    from models import (ToolResult, ListEntitiesResult,
                        ListLayersResult, EntityPropertiesResult, ListBlocksResult)
    from nlp_processor import NLPProcessor

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations, ImageContent, TextContent
from logging.handlers import RotatingFileHandler

# Cấu hình mã hóa UTF-8 trên môi trường Windows
# Lưu ý: các trình thông dịch nhúng như IDLE sẽ thay sys.stdin/stdout/stderr bằng lớp con io.TextIOBase
# (như idlelib.run.StdInputFile), các đối tượng này không có reconfigure(), gọi trực tiếp sẽ ném
# AttributeError khiến máy chủ không thể khởi động, vì vậy cần kiểm tra trước khi thiết lập.
if sys.platform == "win32" and os.environ.get('PYTHONIOENCODING') is None:
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding="utf-8")
            except Exception:  # Bỏ qua nếu luồng riêng lẻ không hỗ trợ đổi mã hóa, tránh làm lỗi khởi động
                pass

config = get_config()

def _build_log_handlers() -> list:
    """Xây dựng các handler ghi log: console + tệp xoay vòng

    Ưu tiên ghi tệp log vào thư mục mã nguồn (thuận tiện xem khi phát triển, đường dẫn không trôi theo thư mục khởi chạy);
    khi cài bằng pip vào vị trí không có quyền ghi như site-packages thì lùi về thư mục người dùng,
    tránh việc khởi tạo FileHandler ném PermissionError làm máy chủ khởi động thất bại.
    Sử dụng tệp xoay vòng (5MB mỗi tệp, giữ lại 2 bản sao lưu) để tránh log phình to vô hạn khi chạy lâu dài.
    StreamHandler mặc định xuất ra stderr nên không làm bẩn dữ liệu giao thức ở chế độ truyền tải stdio.
    """
    handlers = [logging.StreamHandler()]
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cad_mcp.log'),
        os.path.join(os.path.expanduser('~'), '.cad-mcp', 'cad_mcp.log'),
    ]
    for path in candidates:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handlers.append(RotatingFileHandler(path, maxBytes=5 * 1024 * 1024,
                                                backupCount=2, encoding='utf-8'))
            break
        except OSError:
            continue
    return handlers


# Cấu hình logging (phải hoàn tất sớm khi import module, và toàn bộ tiến trình chỉ gọi basicConfig một lần tại đây:
# nếu root logger đã có handler thì basicConfig sẽ bị bỏ qua âm thầm, xem thêm giải thích trong src/__init__.py)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_build_log_handlers()
)

logger = logging.getLogger('mcp_cad_server')
logger.info("Khởi động máy chủ CAD MCP")

SERVER_NAME = config["server"]["name"]
SERVER_VERSION = config["server"].get("version", "2.0.0")

PROMPT_TEMPLATE = """
Mục tiêu của trợ lý là hướng dẫn cách sử dụng dịch vụ CAD MCP. Thông qua dịch vụ này, bạn có thể điều khiển phần mềm CAD trực tiếp trong cuộc trò chuyện để tạo và chỉnh sửa bản vẽ.

<cad-mcp>

Lời nhắc (Prompt):
Máy chủ này cung cấp một lời nhắc dựng sẵn có tên "cad-assistant" giúp người dùng điều khiển CAD bằng lệnh ngôn ngữ tự nhiên. Người dùng có thể yêu cầu vẽ nhiều hình dạng khác nhau, chỉnh sửa đối tượng đồ họa hoặc lưu bản vẽ.

Tài nguyên (Resource):
Máy chủ này cung cấp tài nguyên "drawing://current", biểu thị trạng thái bản vẽ CAD hiện tại (lịch sử phiên làm việc + thống kê thời gian thực từ tài liệu CAD).

Công cụ (Tools):

Máy chủ này cung cấp các nhóm công cụ sau:

Công cụ vẽ cơ bản:
"draw_line": Vẽ đoạn thẳng trong CAD
"draw_circle": Vẽ đường tròn trong CAD
"draw_arc": Vẽ cung tròn trong CAD
"draw_ellipse": Vẽ hình elip trong CAD
"draw_polyline": Vẽ đường đa tuyến (polyline) trong CAD
"draw_rectangle": Vẽ hình chữ nhật trong CAD
"draw_text": Thêm văn bản trong CAD
"draw_hatch": Vẽ tô mặt cắt (hatch) trong CAD
"add_dimension": Thêm ghi kích thước tuyến tính trong CAD

Công cụ truy vấn (nắm bắt nội dung thực tế của bản vẽ):
"list_layers": Liệt kê tất cả các lớp (layer)
"list_entities": Liệt kê các thực thể trong không gian mô hình (trả về handle để chỉnh sửa)
"get_entity_properties": Tra cứu thuộc tính chi tiết của thực thể theo handle
"screenshot": Chụp ảnh màn hình cửa sổ CAD để kiểm tra kết quả vẽ

Công cụ chỉnh sửa (thao tác thực thể đã có thông qua handle):
"erase_entity": Xóa thực thể
"move_entity": Di chuyển thực thể
"rotate_entity": Xoay thực thể
"scale_entity": Phóng to/thu nhỏ thực thể
"copy_entity": Sao chép thực thể
"mirror_entity": Lấy đối xứng thực thể
"offset_entity": Tạo đường song song (offset) thực thể
"array_linear_entity" / "array_polar_entity": Tạo mảng (array)
"undo" / "redo": Hoàn tác / làm lại

Quản lý lớp và bản vẽ:
"create_layer" / "set_current_layer": Quản lý lớp
"new_drawing" / "open_drawing" / "close_drawing" / "save_drawing": Quản lý bản vẽ
"create_block" / "insert_block" / "list_blocks": Thao tác với khối (block)
"send_command": Gửi trực tiếp lệnh bất kỳ tới dòng lệnh CAD

Giao diện cũ (Legacy):
"process_command": Xử lý lệnh ngôn ngữ tự nhiên và chuyển thành thao tác CAD (khuyên ưu tiên sử dụng các công cụ có cấu trúc ở trên)

</cad-mcp>

Hãy bắt đầu phần giới thiệu một cách thân thiện, ví dụ: "Xin chào! Hôm nay tôi sẽ hướng dẫn bạn cách sử dụng dịch vụ CAD MCP. Với dịch vụ này, bạn có thể điều khiển phần mềm CAD trực tiếp ngay trong cuộc trò chuyện mà không cần thao tác thủ công trên giao diện. Hãy cùng bắt đầu nhé!"
"""

# Hướng dẫn sử dụng cấp máy chủ (gửi kèm phản hồi initialize tới client)
SERVER_INSTRUCTIONS = """Máy chủ CAD MCP: Điều khiển AutoCAD / GstarCAD / ZWCAD trên Windows thông qua các công cụ MCP.

Lưu ý sử dụng:
- Lần đầu gọi công cụ nếu CAD chưa chạy thì máy chủ sẽ tự động khởi động (có thể mất khoảng 20 giây) và gửi thông báo tiến độ trong lúc chờ
- Các công cụ vẽ/chỉnh sửa trả về entity_handles (handle của thực thể), dùng để chỉnh sửa và truy vấn về sau
- Tham số màu hỗ trợ mã màu ACI (1-256) hoặc tên màu, nếu không chỉ định sẽ giữ màu theo lớp (ByLayer)
- Sau khi vẽ nên dùng screenshot để chụp ảnh xác nhận kết quả và dùng list_entities để kiểm tra nội dung thực tế của bản vẽ
- send_command cho phép gửi thẳng tới dòng lệnh CAD để thực thi bất kỳ lệnh CAD nào chưa được đóng gói thành công cụ
"""

# ==================== Kiểu tham số (giữ nguyên ràng buộc tọa độ của schema gốc) ====================

# Điểm tọa độ: 2~3 số [x, y] hoặc [x, y, z]
Point = Annotated[List[float], Field(min_length=2, max_length=3, description="Tọa độ [x, y] hoặc [x, y, z]")]
# Tập hợp điểm: ít nhất 2 điểm tọa độ
Points = Annotated[List[Point], Field(min_length=2, description="Tập hợp điểm [[x1,y1], [x2,y2], ...] (mỗi điểm gồm 2~3 thành phần)")]
# Tập hợp điểm đường biên hatch: ít nhất 3 điểm tọa độ
HatchPoints = Annotated[List[Point], Field(min_length=3, description="Tập hợp điểm đường biên tô mặt cắt (ít nhất 3 điểm)")]
# Tham số số dương (bán kính/chiều cao chữ/hệ số tỉ lệ...: CAD sẽ báo lỗi trực tiếp với giá trị 0 hoặc âm, chặn sớm ở tầng schema)
PosFloat = Annotated[float, Field(gt=0, description="Số dương")]
# Số lượng mảng (bao gồm thực thể gốc): tối thiểu 2 (<2 không có ý nghĩa tạo mảng), giới hạn trên tránh thao tác nhầm tạo quá nhiều thực thể làm treo bản vẽ
ArrayCount = Annotated[int, Field(ge=2, le=10000, description="Tổng số phần tử mảng (bao gồm thực thể gốc, 2~10000)")]


class CADService:
    def __init__(self):
        """Khởi tạo dịch vụ CAD"""
        self.controller = CADController()
        self.nlp_processor = NLPProcessor()
        self.drawing_state = {
            "entities": [],
            "current_layer": "0",
            "last_command": "",
            "last_result": ""
        }
        logger.info("Dịch vụ CAD đã được khởi tạo")

    @staticmethod
    def _handle(entity) -> Optional[str]:
        """Đọc an toàn handle của thực thể"""
        return CADController._safe_attr(entity, "Handle")

    def start_cad(self):
        """Khởi động CAD"""
        return self.controller.start_cad()

    def draw_line(self, start_point, end_point, layer=None, color=None, lineweight=None):
        """Vẽ đoạn thẳng"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_line(start_point, end_point, current_layer, color, lineweight)
        if result:
            self.drawing_state["entities"].append({
                "type": "line",
                "handle": self._handle(result),
                "start": start_point,
                "end": end_point,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"Vẽ đoạn thẳng từ {start_point} đến {end_point}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def draw_circle(self, center, radius, layer=None, color=None, lineweight=None):
        """Vẽ đường tròn"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_circle(center, radius, current_layer, color, lineweight)
        if result:
            self.drawing_state["entities"].append({
                "type": "circle",
                "handle": self._handle(result),
                "center": center,
                "radius": radius,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"Vẽ đường tròn, tâm {center}, bán kính {radius}, lớp {current_layer}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def draw_arc(self, center, radius, start_angle, end_angle, layer=None, color=None, lineweight=None):
        """Vẽ cung tròn"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_arc(center, radius, start_angle, end_angle, current_layer, color, lineweight)

        if result:
            self.drawing_state["entities"].append({
                "type": "arc",
                "handle": self._handle(result),
                "center": center,
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"Vẽ cung tròn, tâm {center}, bán kính {radius}, góc bắt đầu {start_angle}, góc kết thúc {end_angle}, lớp {current_layer}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def draw_ellipse(self, center, major_axis, minor_axis, rotation=0, layer=None, color=None, lineweight=None):
        """Vẽ hình elip"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_ellipse(center, major_axis, minor_axis, rotation, current_layer, color, lineweight)

        if result:
            self.drawing_state["entities"].append({
                "type": "ellipse",
                "handle": self._handle(result),
                "center": center,
                "major_axis": major_axis,
                "minor_axis": minor_axis,
                "rotation": rotation,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"Vẽ hình elip, tâm {center}, trục lớn {major_axis}, trục nhỏ {minor_axis}, góc xoay {rotation}, lớp {current_layer}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def draw_polyline(self, points, closed=False, layer=None, color=None, lineweight=None):
        """Vẽ đường đa tuyến (polyline)"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_polyline(points, closed, current_layer, color, lineweight)
        if result:
            self.drawing_state["entities"].append({
                "type": "polyline",
                "handle": self._handle(result),
                "points": points,
                "closed": closed,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"Vẽ đường đa tuyến, tập điểm {points}, {'khép kín' if closed else 'không khép kín'}, lớp {current_layer}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def draw_rectangle(self, corner1, corner2, layer=None, color=None, lineweight=None):
        """Vẽ hình chữ nhật"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_rectangle(corner1, corner2, current_layer, color, lineweight)
        if result:
            self.drawing_state["entities"].append({
                "type": "rectangle",
                "handle": self._handle(result),
                "corner1": corner1,
                "corner2": corner2,
                "layer": current_layer,
                "color": color,
                "lineweight": lineweight
            })
            self.drawing_state["last_command"] = f"Vẽ hình chữ nhật, hai góc đối diện {corner1} và {corner2}, lớp {current_layer}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def draw_text(self, position, text, height=2.5, rotation=0, layer=None, color=None):
        """Thêm văn bản"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_text(position, text, height, rotation, current_layer, color)
        if result:
            self.drawing_state["entities"].append({
                "type": "text",
                "handle": self._handle(result),
                "position": position,
                "text": text,
                "height": height,
                "rotation": rotation,
                "layer": current_layer,
                "color": color
            })
            self.drawing_state["last_command"] = f"Thêm văn bản '{text}', vị trí {position}, chiều cao {height}, góc xoay {rotation}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def draw_hatch(self, points, pattern_name="SOLID", scale=1.0, layer=None, color=None):
        """Vẽ tô mặt cắt (hatch)"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.draw_hatch(points, pattern_name, scale, current_layer, color)
        if result:
            self.drawing_state["entities"].append({
                "type": "hatch",
                "handle": self._handle(result),
                "points": points,
                "pattern_name": pattern_name,
                "scale": scale,
                "layer": current_layer,
                "color": color
            })
            self.drawing_state["last_command"] = f"Vẽ tô mặt cắt, tập điểm {points}, mẫu {pattern_name}, tỉ lệ {scale}, lớp {current_layer}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def add_dimension(self, start_point, end_point, text_position=None, textheight=5, layer=None, color=None):
        """Thêm ghi kích thước tuyến tính"""
        if not self.controller.is_running():
            self.start_cad()

        # Sử dụng lớp hiện tại hoặc lớp được chỉ định
        current_layer = layer or self.drawing_state["current_layer"]

        result = self.controller.add_dimension(start_point, end_point, text_position, textheight, current_layer, color)
        if result:
            self.drawing_state["entities"].append({
                "type": "dimension",
                "handle": self._handle(result),
                "start": start_point,
                "end": end_point,
                "text_position": text_position,
                "textheight": textheight,
                "layer": current_layer,
                "color": color
            })
            self.drawing_state["last_command"] = f"Thêm ghi kích thước từ {start_point} đến {end_point}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def save_drawing(self, file_path):
        """Lưu bản vẽ"""
        if not self.controller.is_running():
            return False

        result = self.controller.save_drawing(file_path)
        if result:
            self.drawing_state["last_command"] = f"Lưu bản vẽ tới {file_path}"
            self.drawing_state["last_result"] = "Thành công"
        else:
            self.drawing_state["last_result"] = "Thất bại"

        return result

    def process_command(self, command: str) -> Dict[str, Any]:
        """Xử lý lệnh ngôn ngữ tự nhiên (giao diện cũ, khuyến nghị client gọi trực tiếp các công cụ vẽ có cấu trúc)"""
        if not self.controller.is_running():
            self.start_cad()
        # Dùng bộ xử lý NLP để phân tích lệnh
        parsed_command = self.nlp_processor.process_command(command)
        command_type = parsed_command.get("type")
        try:
            # Trả về thất bại rõ ràng cho lệnh không nhận diện được (trước đây trả về None âm thầm)
            if command_type in (None, "unknown", "error"):
                return {
                    "success": False,
                    "message": parsed_command.get("error") or "Không thể nhận diện lệnh",
                    "original_command": command
                }

            # Phân giải màu thống nhất: màu trích xuất từ văn bản lệnh được ưu tiên hơn tham số màu trong kết quả phân tích
            color = parsed_command.get("color")
            color_rgb = self.nlp_processor.extract_color_from_command(command)
            if color_rgb is not None:
                color = color_rgb
            # Lấy tham số độ dày nét
            lineweight = parsed_command.get("lineweight")

            if command_type == "draw_line":
                result = self.draw_line(parsed_command.get("start_point"), parsed_command.get("end_point"), None, color, lineweight)
                return {
                    "success": result is not None,
                    "message": "Đã vẽ đoạn thẳng" if result else "Vẽ đoạn thẳng thất bại",
                    "entity_id": self._handle(result)
                }

            elif command_type == "draw_circle":
                result = self.draw_circle(parsed_command.get("center"), parsed_command.get("radius"), None, color, lineweight)
                return {
                    "success": result is not None,
                    "message": "Đã vẽ đường tròn" if result else "Vẽ đường tròn thất bại",
                    "entity_id": self._handle(result)
                }

            elif command_type == "draw_arc":
                center = parsed_command.get("center")
                radius = parsed_command.get("radius")
                start_angle = parsed_command.get("start_angle")
                end_angle = parsed_command.get("end_angle")
                # Đảm bảo tất cả tham số bắt buộc đều tồn tại và hợp lệ
                if center is None or radius is None or start_angle is None or end_angle is None:
                    return {
                        "success": False,
                        "message": "Vẽ cung tròn thất bại: thiếu tham số bắt buộc",
                        "error": "Thiếu tham số bắt buộc: tâm, bán kính, góc bắt đầu hoặc góc kết thúc"
                    }
                result = self.draw_arc(center, radius, start_angle, end_angle, None, color, lineweight)
                return {
                    "success": result is not None,
                    "message": "Đã vẽ cung tròn" if result else "Vẽ cung tròn thất bại",
                    "entity_id": self._handle(result)
                }

            elif command_type == "draw_ellipse":
                center = parsed_command.get("center")
                major_axis = parsed_command.get("major_axis")
                minor_axis = parsed_command.get("minor_axis")
                rotation = parsed_command.get("rotation", 0)  # Góc xoay mặc định là 0
                # Đảm bảo tất cả tham số bắt buộc đều tồn tại và hợp lệ
                if center is None or major_axis is None or minor_axis is None:
                    return {
                        "success": False,
                        "message": "Vẽ hình elip thất bại: thiếu tham số bắt buộc",
                        "error": "Thiếu tham số bắt buộc: tâm, trục lớn hoặc trục nhỏ"
                    }
                result = self.draw_ellipse(center, major_axis, minor_axis, rotation, None, color, lineweight)
                return {
                    "success": result is not None,
                    "message": "Đã vẽ hình elip" if result else "Vẽ hình elip thất bại",
                    "entity_id": self._handle(result)
                }

            elif command_type == "draw_rectangle":
                result = self.draw_rectangle(parsed_command.get("corner1"), parsed_command.get("corner2"), None, color, lineweight)
                return {
                    "success": result is not None,
                    "message": "Đã vẽ hình chữ nhật" if result else "Vẽ hình chữ nhật thất bại",
                    "entity_id": self._handle(result)
                }

            elif command_type == "draw_text":
                height = parsed_command.get("height", 2.5)
                rotation = parsed_command.get("rotation", 0)
                result = self.draw_text(parsed_command.get("position"), parsed_command.get("text"), height, rotation, None, color)
                return {
                    "success": result is not None,
                    "message": "Đã thêm văn bản" if result else "Thêm văn bản thất bại",
                    "entity_id": self._handle(result)
                }

            elif command_type == "draw_hatch":
                points = parsed_command.get("points")
                pattern_name = parsed_command.get("pattern_name", "SOLID")
                scale = parsed_command.get("scale", 1.0)
                # Đảm bảo tất cả tham số bắt buộc đều tồn tại và hợp lệ
                if points is None or len(points) < 3:
                    return {
                        "success": False,
                        "message": "Vẽ tô mặt cắt thất bại: thiếu tham số bắt buộc hoặc không đủ điểm",
                        "error": "Đường biên tô mặt cắt cần ít nhất 3 điểm"
                    }
                result = self.draw_hatch(points, pattern_name, scale, None, color)
                return {
                    "success": result is not None,
                    "message": "Đã vẽ tô mặt cắt" if result else "Vẽ tô mặt cắt thất bại",
                    "entity_id": self._handle(result)
                }

            elif command_type == "add_dimension":
                # Đi qua tầng dịch vụ thống nhất (đồng thời cập nhật trạng thái phiên)
                result = self.add_dimension(parsed_command.get("start_point"), parsed_command.get("end_point"), parsed_command.get("text_position"))
                return {
                    "success": result is not None,
                    "message": "Đã thêm ghi kích thước" if result else "Thêm ghi kích thước thất bại",
                    "entity_id": self._handle(result)
                }

            elif command_type == "save":
                file_path = parsed_command.get("file_path")
                result = self.save_drawing(file_path)
                return {
                    "success": result,
                    "message": f"Đã lưu bản vẽ tới {file_path}" if result else f"Lưu bản vẽ tới {file_path} thất bại"
                }

            # Xử lý thao tác lớp (layer)
            elif command_type == "create_layer":
                layer_name = parsed_command.get("layer_name")
                if not layer_name:
                    return {
                        "success": False,
                        "message": "Tạo lớp thất bại: chưa chỉ định tên lớp"
                    }
                result = self.controller.create_layer(layer_name)  # , color
                if result:
                    self.drawing_state["current_layer"] = layer_name
                return {
                    "success": result,
                    "message": f"Đã tạo lớp {layer_name}" if result else f"Tạo lớp {layer_name} thất bại"
                }

            # Các loại lệnh đã biết khác nhưng tạm thời chưa hỗ trợ
            return {
                "success": False,
                "message": f"Loại lệnh chưa được hỗ trợ: {command_type}"
            }

        except Exception as e:
            return {
                "success": False,
                "message": f"Lỗi khi xử lý lệnh: {str(e)}"
            }


# ==================== Thực thể dịch vụ và hàm tiện ích chung ====================

cad_service = CADService()


async def com(fn, *args, **kwargs):
    """Thực thi lệnh gọi trên luồng COM chuyên dụng (tránh chặn vòng lặp sự kiện và đảm bảo an toàn luồng)"""
    return await cad_service.controller.executor.run(fn, *args, **kwargs)


def _resolve_color(color) -> Optional[int]:
    """Phân giải tham số màu sang chỉ số ACI: None=Theo lớp (ByLayer); hỗ trợ mã chỉ số và tên màu"""
    return cad_service.nlp_processor.color_to_index(color)


async def _entity_result(entity, ok_message: str, fail_message: str) -> ToolResult:
    """Đóng gói một thực thể COM đơn lẻ thành kết quả công cụ thống nhất

    Handle bắt buộc phải được đọc trên luồng COM — thực thể do luồng COM tạo ra,
    truy cập trực tiếp con trỏ giao diện COM chưa marshal từ luồng chính là lời gọi xuyên apartment, sẽ gây lỗi hoặc crash.
    """
    if entity is None:
        return ToolResult(success=False, message=fail_message, error="Thao tác CAD thất bại, xem chi tiết trong cad_mcp.log")
    handle = await com(CADController._safe_attr, entity, "Handle")
    return ToolResult(success=True, message=ok_message,
                      entity_handles=[str(handle)] if handle else [])


def _extract_handles_sync(entities: List[Any]) -> List[str]:
    """Trích xuất hàng loạt handle của các thực thể trên luồng COM"""
    return [str(h) for h in (CADController._safe_attr(e, "Handle") for e in entities) if h]


async def _entities_result(entities: List[Any], ok_message: str, fail_message: str) -> ToolResult:
    """Đóng gói danh sách thực thể COM thành kết quả công cụ thống nhất (handle được đọc trên luồng COM)"""
    if not entities:
        return ToolResult(success=False, message=fail_message)
    handles = await com(_extract_handles_sync, entities)
    return ToolResult(success=True, message=ok_message, entity_handles=handles)


async def _ensure_running(ctx: Context) -> bool:
    """Đảm bảo CAD đang chạy: nếu chưa chạy thì gửi thông báo tiến độ rồi tự động khởi động (lần đầu khởi động có thể chậm)

    Lưu ý phải thực sự kích hoạt khởi động — trước đây chỉ gửi thông báo mà không khởi động, khiến các công cụ
    không có logic tự khởi động như screenshot ném ngoại lệ trực tiếp khi CAD chưa chạy.
    """
    if cad_service.controller.is_running():
        return True
    await ctx.info("CAD chưa chạy, đang khởi động CAD (lần đầu khởi động có thể mất khoảng 20 giây)...")
    if await com(cad_service.start_cad):
        return True
    detail = getattr(cad_service.controller, "last_connect_error", "")
    await ctx.info("Tự động khởi động CAD thất bại, vui lòng kiểm tra xem phần mềm CAD đã được cài đặt và có thể khởi chạy bình thường chưa"
                   + (f" ({detail})" if detail else ""))
    return False


def _not_running_message() -> str:
    """Thông báo khi CAD chưa chạy, kèm theo nguyên nhân lỗi kết nối gần nhất

    Trước đây mọi lỗi đều chỉ hiển thị "CAD chưa chạy và tự động khởi động thất bại", người dùng không biết ProgID nào
    không kết nối được hay mã HRESULT là gì, chỉ có thể tự đoán. Nay đính kèm thêm nguyên nhân mà controller ghi nhận.
    """
    detail = getattr(cad_service.controller, "last_connect_error", "")
    base = "CAD chưa chạy và tự động khởi động thất bại"
    return f"{base}: {detail}" if detail else base


# ==================== Máy chủ MCP và đăng ký công cụ ====================

mcp = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)


# ---------- Công cụ vẽ cơ bản ----------

@mcp.tool()
async def draw_line(ctx: Context, start_point: Point, end_point: Point,
                    layer: Optional[str] = None, color: Union[int, str, None] = None,
                    lineweight: Optional[float] = None) -> ToolResult:
    """Vẽ đoạn thẳng trong CAD

    color hỗ trợ mã màu CAD (1-256) hoặc tên màu (ví dụ: "đỏ", "red"), nếu không chỉ định sẽ giữ màu theo lớp.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.draw_line, start_point, end_point, layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "Đã vẽ đoạn thẳng", "Vẽ đoạn thẳng thất bại")


@mcp.tool()
async def draw_circle(ctx: Context, center: Point, radius: PosFloat,
                      layer: Optional[str] = None, color: Union[int, str, None] = None,
                      lineweight: Optional[float] = None) -> ToolResult:
    """Vẽ đường tròn trong CAD

    color hỗ trợ mã màu CAD (1-256) hoặc tên màu, nếu không chỉ định sẽ giữ màu theo lớp.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.draw_circle, center, radius, layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "Đã vẽ đường tròn", "Vẽ đường tròn thất bại")


@mcp.tool()
async def draw_arc(ctx: Context, center: Point, radius: PosFloat,
                   start_angle: float, end_angle: float,
                   layer: Optional[str] = None, color: Union[int, str, None] = None,
                   lineweight: Optional[float] = None) -> ToolResult:
    """Vẽ cung tròn trong CAD (đơn vị góc: độ)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.draw_arc, center, radius, start_angle, end_angle,
                       layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "Đã vẽ cung tròn", "Vẽ cung tròn thất bại")


@mcp.tool()
async def draw_ellipse(ctx: Context, center: Point, major_axis: PosFloat, minor_axis: PosFloat,
                       rotation: float = 0, layer: Optional[str] = None,
                       color: Union[int, str, None] = None,
                       lineweight: Optional[float] = None) -> ToolResult:
    """Vẽ hình elip trong CAD (rotation là góc xoay, đơn vị: độ)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.draw_ellipse, center, major_axis, minor_axis, rotation,
                       layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "Đã vẽ hình elip", "Vẽ hình elip thất bại")


@mcp.tool()
async def draw_polyline(ctx: Context, points: Points, closed: bool = False,
                        layer: Optional[str] = None, color: Union[int, str, None] = None,
                        lineweight: Optional[float] = None) -> ToolResult:
    """Vẽ đường đa tuyến trong CAD (khép kín khi closed=True)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.draw_polyline, points, closed, layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "Đã vẽ đường đa tuyến", "Vẽ đường đa tuyến thất bại")


@mcp.tool()
async def draw_rectangle(ctx: Context, corner1: Point, corner2: Point,
                         layer: Optional[str] = None, color: Union[int, str, None] = None,
                         lineweight: Optional[float] = None) -> ToolResult:
    """Vẽ hình chữ nhật trong CAD (xác định bởi hai góc đối diện)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.draw_rectangle, corner1, corner2, layer, _resolve_color(color), lineweight)
    return await _entity_result(entity, "Đã vẽ hình chữ nhật", "Vẽ hình chữ nhật thất bại")


@mcp.tool()
async def draw_text(ctx: Context, position: Point, text: str, height: PosFloat = 2.5,
                    rotation: float = 0, layer: Optional[str] = None,
                    color: Union[int, str, None] = None) -> ToolResult:
    """Thêm văn bản một dòng trong CAD"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.draw_text, position, text, height, rotation, layer, _resolve_color(color))
    return await _entity_result(entity, "Đã thêm văn bản", "Thêm văn bản thất bại")


@mcp.tool()
async def draw_hatch(ctx: Context, points: HatchPoints, pattern_name: str = "SOLID",
                     scale: PosFloat = 1.0, layer: Optional[str] = None,
                     color: Union[int, str, None] = None) -> ToolResult:
    """Vẽ tô mặt cắt trong CAD (points là tập hợp điểm đường biên khép kín, ít nhất 3 điểm)

    pattern_name là tên mẫu tô mặt cắt, mặc định SOLID (tô đặc).
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.draw_hatch, points, pattern_name, scale, layer, _resolve_color(color))
    return await _entity_result(entity, "Đã vẽ tô mặt cắt", "Vẽ tô mặt cắt thất bại")


@mcp.tool()
async def add_dimension(ctx: Context, start_point: Point, end_point: Point,
                        text_position: Optional[Point] = None, textheight: PosFloat = 5,
                        layer: Optional[str] = None, color: Union[int, str, None] = None) -> ToolResult:
    """Thêm ghi kích thước tuyến tính trong CAD (nếu không chỉ định text_position sẽ tự động đặt phía trên trung điểm)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entity = await com(cad_service.add_dimension, start_point, end_point, text_position, textheight,
                       layer, _resolve_color(color))
    return await _entity_result(entity, "Đã thêm ghi kích thước", "Thêm ghi kích thước thất bại")


# ---------- Công cụ truy vấn (khép kín vòng phản hồi AI: AI nhìn thấy nội dung thực tế của bản vẽ) ----------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_layers(ctx: Context) -> ListLayersResult:
    """Liệt kê tất cả các lớp của bản vẽ hiện tại (tên, màu, trạng thái đóng băng/khóa/bật tắt, có phải lớp hiện tại không)"""
    if not await _ensure_running(ctx):
        return ListLayersResult(success=False, message=_not_running_message())
    layers = await com(cad_service.controller.list_layers)
    return ListLayersResult(success=True, message=f"Tổng cộng {len(layers)} lớp", layers=layers)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_entities(ctx: Context, entity_type: Optional[str] = None,
                        limit: int = 50) -> ListEntitiesResult:
    """Liệt kê các thực thể trong không gian mô hình (truy vấn thời gian thực, đồng bộ với tài liệu CAD)

    Trả về handle của thực thể, dùng cho các công cụ chỉnh sửa và get_entity_properties.
    entity_type cho phép lọc theo loại (như line/circle/arc/text); limit kiểm soát số lượng trả về.
    """
    if not await _ensure_running(ctx):
        return ListEntitiesResult(success=False, message=_not_running_message())
    result = await com(cad_service.controller.list_entities, entity_type, limit)
    return ListEntitiesResult(success=True,
                              message=f"Không gian mô hình có tổng cộng {result['total']} thực thể, trả về {len(result['entities'])} thực thể",
                              total=result["total"],
                              returned=len(result["entities"]),
                              entities=result["entities"])


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_entity_properties(ctx: Context, handle: str) -> EntityPropertiesResult:
    """Tra cứu thuộc tính chi tiết của thực thể theo handle (thông tin hình học tùy thuộc loại thực thể: tọa độ/bán kính/nội dung văn bản...)"""
    if not await _ensure_running(ctx):
        return EntityPropertiesResult(success=False, message=_not_running_message())
    try:
        props = await com(cad_service.controller.get_entity_properties, handle)
        return EntityPropertiesResult(success=True, message="Truy vấn thành công", **props)
    except Exception as e:
        return EntityPropertiesResult(success=False, message=f"Truy vấn handle {handle} thất bại", error=str(e))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def screenshot(ctx: Context):
    """Chụp màn hình cửa sổ chính CAD và trả về ảnh xem trước PNG (dùng để kiểm tra kết quả vẽ)"""
    if not await _ensure_running(ctx):
        return [TextContent(type="text", text=f"{_not_running_message()}, không thể chụp ảnh màn hình")]
    png_bytes = await com(cad_service.controller.screenshot)
    # Mã hóa sang base64 và trả về khối nội dung hình ảnh
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    return [
        ImageContent(type="image", data=b64, mimeType="image/png"),
        TextContent(type="text", text="Ảnh chụp màn hình cửa sổ CAD (PNG)"),
    ]


# ---------- Công cụ chỉnh sửa (thao tác thực thể đã có thông qua handle) ----------

@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def erase_entity(ctx: Context, handle: str) -> ToolResult:
    """Xóa thực thể theo handle được chỉ định"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.erase_entity, handle)
    return ToolResult(success=ok, message="Đã xóa thực thể" if ok else f"Xóa handle {handle} thất bại")


@mcp.tool()
async def move_entity(ctx: Context, handle: str, displacement: Point) -> ToolResult:
    """Di chuyển thực thể (displacement là vectơ dịch chuyển [dx, dy, dz])"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.move_entity, handle, displacement)
    return ToolResult(success=ok, message="Đã di chuyển thực thể" if ok else f"Di chuyển handle {handle} thất bại")


@mcp.tool()
async def rotate_entity(ctx: Context, handle: str, base_point: Point, angle_degrees: float) -> ToolResult:
    """Xoay thực thể quanh điểm gốc (đơn vị góc: độ, ngược chiều kim đồng hồ là dương)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.rotate_entity, handle, base_point, angle_degrees)
    return ToolResult(success=ok, message="Đã xoay thực thể" if ok else f"Xoay handle {handle} thất bại")


@mcp.tool()
async def scale_entity(ctx: Context, handle: str, base_point: Point, scale_factor: PosFloat) -> ToolResult:
    """Phóng to/thu nhỏ thực thể quanh điểm gốc (scale_factor>1 để phóng to, <1 để thu nhỏ)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.scale_entity, handle, base_point, scale_factor)
    return ToolResult(success=ok, message="Đã thay đổi tỉ lệ thực thể" if ok else f"Thay đổi tỉ lệ handle {handle} thất bại")


@mcp.tool()
async def copy_entity(ctx: Context, handle: str, displacement: Point) -> ToolResult:
    """Sao chép thực thể và tịnh tiến theo vectơ dịch chuyển chỉ định, trả về handle thực thể mới"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    copy = await com(cad_service.controller.copy_entity, handle, displacement)
    return await _entity_result(copy, "Đã sao chép thực thể", f"Sao chép handle {handle} thất bại")


@mcp.tool()
async def mirror_entity(ctx: Context, handle: str, point1: Point, point2: Point) -> ToolResult:
    """Lấy đối xứng thực thể qua trục đối xứng xác định bởi hai điểm (giữ lại thực thể gốc), trả về handle thực thể mới"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    mirrored = await com(cad_service.controller.mirror_entity, handle, point1, point2)
    return await _entity_result(mirrored, "Đã lấy đối xứng thực thể", f"Lấy đối xứng handle {handle} thất bại")


@mcp.tool()
async def offset_entity(ctx: Context, handle: str, distance: float) -> ToolResult:
    """Tạo đường song song (offset) cho thực thể (giá trị dương/âm quyết định hướng offset), trả về handle thực thể mới"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entities = await com(cad_service.controller.offset_entity, handle, distance)
    return await _entities_result(entities, "Đã offset thực thể", f"Offset handle {handle} thất bại")


@mcp.tool()
async def array_linear_entity(ctx: Context, handle: str, count: ArrayCount, displacement: Point) -> ToolResult:
    """Mảng tuyến tính: sao chép count-1 bản dọc theo vectơ dịch chuyển (tổng cộng count phần tử kể cả thực thể gốc)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entities = await com(cad_service.controller.array_linear_entity, handle, count, displacement)
    return await _entities_result(entities, f"Đã hoàn tất mảng tuyến tính gồm {count} phần tử", f"Tạo mảng cho handle {handle} thất bại")


@mcp.tool()
async def array_polar_entity(ctx: Context, handle: str, count: ArrayCount, center: Point,
                             total_angle_degrees: float = 360.0) -> ToolResult:
    """Mảng tròn: sao chép count-1 bản quanh điểm tâm trong phạm vi góc total_angle"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    entities = await com(cad_service.controller.array_polar_entity, handle, count, center, total_angle_degrees)
    return await _entities_result(entities, f"Đã hoàn tất mảng tròn gồm {count} phần tử", f"Tạo mảng cho handle {handle} thất bại")


@mcp.tool()
async def undo(ctx: Context) -> ToolResult:
    """Hoàn tác thao tác trước đó"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.undo)
    return ToolResult(success=ok, message="Đã hoàn tác" if ok else "Hoàn tác thất bại")


@mcp.tool()
async def redo(ctx: Context) -> ToolResult:
    """Làm lại thao tác vừa hoàn tác"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.redo)
    return ToolResult(success=ok, message="Đã làm lại" if ok else "Làm lại thất bại")


# ---------- Quản lý lớp ----------

@mcp.tool()
async def create_layer(ctx: Context, layer_name: str) -> ToolResult:
    """Tạo lớp mới và đặt làm lớp hiện tại (nếu đã tồn tại thì kích hoạt trực tiếp)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.create_layer, layer_name)
    if ok:
        cad_service.drawing_state["current_layer"] = layer_name
    return ToolResult(success=ok, message=f"Đã tạo lớp {layer_name} và đặt làm lớp hiện tại" if ok else f"Tạo lớp {layer_name} thất bại")


@mcp.tool()
async def set_current_layer(ctx: Context, layer_name: str) -> ToolResult:
    """Chuyển lớp hiện tại (lớp phải đã tồn tại)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.set_current_layer, layer_name)
    if ok:
        cad_service.drawing_state["current_layer"] = layer_name
    return ToolResult(success=ok, message=f"Đã chuyển lớp hiện tại sang {layer_name}" if ok else f"Lớp {layer_name} không tồn tại hoặc chuyển đổi thất bại")


# ---------- Quản lý bản vẽ ----------

@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def new_drawing(ctx: Context) -> ToolResult:
    """Tạo tài liệu bản vẽ mới"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.new_drawing)
    if ok:
        # Lịch sử vẽ của phiên thuộc về tài liệu cũ, xóa sạch sau khi chuyển tài liệu
        cad_service.drawing_state["entities"] = []
    return ToolResult(success=ok, message="Đã tạo tài liệu mới" if ok else "Tạo tài liệu mới thất bại")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def open_drawing(ctx: Context, file_path: str) -> ToolResult:
    """Mở tệp bản vẽ đã có (DWG/DXF, v.v.)"""
    if cad_service.controller.app is None and not await com(cad_service.controller.start_cad):
        return ToolResult(success=False, message="CAD chưa chạy và khởi động thất bại")
    try:
        ok = await com(cad_service.controller.open_drawing, file_path)
    except FileNotFoundError as e:
        return ToolResult(success=False, message=str(e))
    if ok:
        cad_service.drawing_state["entities"] = []
    return ToolResult(success=ok, message=f"Đã mở tài liệu: {file_path}" if ok else f"Mở tài liệu thất bại: {file_path}")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def close_drawing(ctx: Context, save: bool = False) -> ToolResult:
    """Đóng tài liệu hiện tại (khi save=False sẽ đóng trực tiếp không lưu)"""
    if not cad_service.controller.is_running():
        return ToolResult(success=False, message="CAD chưa chạy")
    ok = await com(cad_service.controller.close_drawing, save)
    if ok:
        cad_service.drawing_state["entities"] = []
    return ToolResult(success=ok, message="Đã đóng tài liệu" if ok else "Đóng tài liệu thất bại")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def save_drawing(ctx: Context, file_path: str) -> ToolResult:
    """Lưu bản vẽ hiện tại (tự động chọn định dạng DWG/DXF theo phần mở rộng; tệp đã tồn tại sẽ bị ghi đè)"""
    # Không được tự động khởi động CAD tại đây: start_cad() sẽ tạo một Drawing1.dwg trống,
    # sau đó SaveAs sẽ ghi đè bản vẽ có sẵn tại đường dẫn người dùng bằng bản vẽ trống (không thể khôi phục nội dung gốc).
    # Vì vậy ở đây chỉ chấp nhận "CAD đã kết nối", nếu chưa kết nối thì báo lỗi an toàn, không chạm vào tệp của người dùng.
    if not cad_service.controller.is_running():
        return ToolResult(success=False, message="CAD chưa chạy, vui lòng gọi open_drawing để mở bản vẽ cần lưu trước")
    await ctx.info(f"Đang lưu bản vẽ tới {file_path} ...")
    try:
        ok = await com(cad_service.save_drawing, file_path)
    except ValueError as e:
        # Đường dẫn đích đang bị một bản vẽ khác đang mở chiếm dụng: báo lỗi nhanh sớm kèm hướng dẫn xử lý
        return ToolResult(success=False, message=f"Lưu thất bại: {e}", error=str(e))
    return ToolResult(success=ok, message=f"Đã lưu bản vẽ tới {file_path}" if ok else f"Lưu bản vẽ tới {file_path} thất bại")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def send_command(ctx: Context, command: str) -> ToolResult:
    """Gửi trực tiếp lệnh bất kỳ tới dòng lệnh CAD (cửa thoát chức năng)

    Ví dụ: "_.ZOOM E" (thu phóng toàn bộ bản vẽ), "_.ERASE" kết hợp tập chọn, v.v.
    Khuyến nghị thêm tiền tố "_." trước lệnh để ép dùng tên lệnh tiếng Anh, tương thích với các phiên bản CAD bản địa hóa.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    ok = await com(cad_service.controller.send_command, command)
    return ToolResult(success=ok, message=f"Đã gửi lệnh: {command}" if ok else "Thực thi lệnh thất bại")


# ---------- Thao tác với khối (Block) ----------

@mcp.tool()
async def create_block(ctx: Context, block_name: str, base_point: Optional[Point] = None) -> ToolResult:
    """Tạo định nghĩa khối (nếu khối cùng tên đã tồn tại thì trả về trực tiếp)

    Lưu ý: các thực thể bên trong khối có thể chỉnh sửa thông qua send_command với lệnh BEDIT, v.v.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    block = await com(cad_service.controller.create_block, block_name, base_point)
    return await _entity_result(block, f"Khối {block_name} đã sẵn sàng", f"Tạo khối {block_name} thất bại")


@mcp.tool()
async def insert_block(ctx: Context, block_name: str, position: Point,
                       x_scale: float = 1.0, y_scale: float = 1.0, z_scale: float = 1.0,
                       rotation_degrees: float = 0.0) -> ToolResult:
    """Chèn tham chiếu khối vào không gian mô hình (trả về handle của thực thể tham chiếu khối)"""
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message())
    block_ref = await com(cad_service.controller.insert_block, block_name, position,
                          x_scale, y_scale, z_scale, rotation_degrees)
    return await _entity_result(block_ref, f"Đã chèn khối {block_name}", f"Chèn khối {block_name} thất bại (vui lòng kiểm tra xem khối đã được định nghĩa chưa)")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_blocks(ctx: Context) -> ListBlocksResult:
    """Liệt kê các định nghĩa khối của người dùng trong bản vẽ hiện tại"""
    if not await _ensure_running(ctx):
        return ListBlocksResult(success=False, message=_not_running_message())
    blocks = await com(cad_service.controller.list_blocks)
    return ListBlocksResult(success=True, message=f"Tổng cộng {len(blocks)} khối", blocks=blocks)


# ---------- Giao diện cũ (Legacy) ----------

@mcp.tool()
async def process_command(ctx: Context, command: str) -> ToolResult:
    """Xử lý lệnh ngôn ngữ tự nhiên và chuyển thành thao tác CAD (giao diện cũ)

    Khuyến nghị ưu tiên gọi trực tiếp các công cụ vẽ có cấu trúc (draw_line/draw_circle, v.v.),
    chỉ sử dụng công cụ này khi người dùng yêu cầu rõ ràng đầu vào bằng ngôn ngữ tự nhiên.
    """
    if not await _ensure_running(ctx):
        return ToolResult(success=False, message=_not_running_message(), error=_not_running_message())
    result = await com(cad_service.process_command, command)
    # Tương thích cấu trúc trả về cũ (entity_id) và ánh xạ sang kết quả thống nhất
    handles = []
    if result.get("entity_id"):
        handles = [str(result["entity_id"])]
    return ToolResult(
        success=bool(result.get("success")),
        message=result.get("message", ""),
        entity_handles=handles,
        error=result.get("error") if not result.get("success") else None
    )


# ---------- Tài nguyên và lời nhắc ----------

@mcp.resource("drawing://current", name="Bản vẽ CAD hiện tại",
              description="Trạng thái bản vẽ CAD hiện tại (lịch sử vẽ của phiên + thống kê thời gian thực từ tài liệu CAD)",
              mime_type="application/json")
async def current_drawing() -> str:
    """Tài nguyên trạng thái bản vẽ CAD hiện tại"""
    # Lịch sử phiên + thống kê thời gian thực, giúp client vừa thấy những gì phiên này đã vẽ vừa thấy trạng thái thật của tài liệu
    stats = await com(cad_service.controller.get_drawing_stats)
    payload = {
        "session": cad_service.drawing_state,
        "live": stats,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


@mcp.prompt(name="cad-assistant", description="Trợ lý điều khiển CAD bằng ngôn ngữ tự nhiên")
def cad_assistant() -> str:
    """Mẫu lời nhắc của trợ lý CAD"""
    return PROMPT_TEMPLATE.strip()


# ==================== Điểm vào chính ====================

def _stdio_channel_problem():
    """Trả về lý do kênh stdio không khả dụng; trả về None khi được client khởi chạy bình thường

    Chế độ truyền tải stdio của MCP yêu cầu stdin/stdout phải là đường ống (pipe) thật do client tạo ra.
    Khi nhấn F5 trong IDLE hoặc chạy thủ công trong terminal, stdout không phải pipe nên client không thể kết nối,
    khi đó đưa ra thông báo dễ hiểu để tránh người dùng phải đoán vì sao "không có phản hồi".
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.fileno()
        except Exception:
            return "stdout/stderr hiện tại không phải đường ống (pipe) thật (có thể đang chạy trực tiếp trong trình thông dịch nhúng như IDLE)"
    try:
        if sys.stdout.isatty():
            return "stdout hiện tại là cửa sổ terminal (MCP client cần giao tiếp với tiến trình này qua đường ống pipe)"
    except Exception:
        pass
    return None


def main():
    """Điểm vào chính: hỗ trợ hai phương thức truyền tải là stdio (mặc định) và streamable-http"""
    parser = argparse.ArgumentParser(description="Máy chủ CAD MCP")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default=None,
                        help="Phương thức truyền tải (mặc định đọc từ config.json, nếu chưa cấu hình thì dùng stdio)")
    parser.add_argument("--host", default=None, help="Địa chỉ lắng nghe Streamable HTTP")
    parser.add_argument("--port", type=int, default=None, help="Cổng lắng nghe Streamable HTTP")
    args = parser.parse_args()

    server_cfg = config.get("server", {})
    transport = args.transport or server_cfg.get("transport", "stdio")
    host = args.host or server_cfg.get("host", "127.0.0.1")
    port = args.port or int(server_cfg.get("port", 8000))

    if transport == "streamable-http":
        # Truyền tải Streamable HTTP: hỗ trợ từ xa / nhiều client dùng chung một phiên bản CAD
        mcp.settings.host = host
        mcp.settings.port = port
        logger.info(f"Máy chủ đang chạy với truyền tải Streamable HTTP: http://{host}:{port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        logger.info("Máy chủ đang chạy với truyền tải stdio")
        problem = _stdio_channel_problem()
        if problem:
            logger.warning(f"Lưu ý: {problem}, MCP client không thể kết nối tới tiến trình này; "
                           "vui lòng để client tự động khởi chạy tiến trình này theo cấu hình, hoặc chuyển sang dùng --transport streamable-http")
        mcp.run()  # Mặc định stdio


if __name__ == "__main__":
    main()
