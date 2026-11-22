import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import os
import os.path

# Sử dụng thống nhất singleton cấu hình (thay thế việc đọc lặp lại config.json ở từng module)
try:
    from .config import get_config
except ImportError:
    from config import get_config

config = get_config()

logger = logging.getLogger('nlp_processor')

class NLPProcessor:
    """Lớp xử lý ngôn ngữ tự nhiên, chịu trách nhiệm phân tích lệnh người dùng và chuyển đổi thành thao tác CAD"""
    
    def __init__(self):
        """
        Khởi tạo bộ xử lý NLP
        """
        
        self.logger = logging.getLogger(__name__)
        self.logger.info("Bộ xử lý ngôn ngữ tự nhiên đã được khởi tạo")

        # Các phiên bản CAD cũ không hỗ trợ thiết lập màu RGB, vì vậy để đảm bảo tương thích, màu sắc được thiết lập theo chỉ số
        
        # Ánh xạ chi tiết từ tên màu sang giá trị chỉ số màu (ACI)
        self.color_rgb_map = {
            # Màu cơ bản
            "đỏ": 1,
            "màu đỏ": 1,
            "vàng": 2,
            "màu vàng": 2,
            "xanh lá": 3,
            "xanh lục": 3,
            "màu xanh lá": 3,
            "xanh lơ": 4,
            "lục lam": 4,
            "xanh ngọc": 4,
            "xanh dương": 5,
            "xanh lam": 5,
            "màu xanh dương": 5,
            "hồng cánh sen": 6,
            "đỏ tía": 6,
            "trắng": 7,
            "màu trắng": 7,
            "xám": 8,
            "màu xám": 8,
            "xám nhạt": 9,
            "đen": 250,
            "màu đen": 250,
            "nâu": 251,
            "màu nâu": 251,
            "cam": 30,
            "màu cam": 30,
            "tím": 200,
            "màu tím": 200,
            "hồng": 221,
            "màu hồng": 221,
            

            # Màu cơ bản - Khóa tiếng Anh
            "Red": 1,
            "Yellow": 2,
            "Green": 3,
            "Cyan": 4,
            "Blue": 5,
            "Magenta": 6,
            "White": 7,
            "Gray": 8,
            "Light Gray": 9,
            "Black": 250,
            "Brown": 251,
            "Orange": 30,
            "Purple": 200,
            "Pink": 221,
                       
        }
        
        # Ánh xạ từ khóa hình dạng mở rộng
        self.shape_keywords = {
            # Hình dạng cơ bản
            "đường thẳng": "line", "đoạn thẳng": "line", "đường": "line",
            "hình tròn": "circle", "đường tròn": "circle", "tròn": "circle",
            "cung tròn": "arc", "cung": "arc",
            "hình chữ nhật": "rectangle", "chữ nhật": "rectangle", "hình vuông": "square",
            "đa tuyến": "polyline", "đường gấp khúc": "polyline", "đa giác": "polyline",
            "văn bản": "text", "chữ": "text",
            "ghi kích thước": "dimension", "kích thước": "dimension", "chú thích kích thước": "dimension",
            # Khóa tiếng Anh
            "line": "line",
            "circle": "circle",
            "arc": "arc",
            "rectangle": "rectangle", "square": "square",
            "polyline": "polyline",
            "text": "text",
            "dimension": "dimension",
            
        }
        
        # Ánh xạ từ khóa hành động
        self.action_keywords = {
            # Hành động cơ bản
            "vẽ": "draw", "tạo": "draw", "thêm": "draw", "viết": "draw",
            "sửa": "modify", "chỉnh sửa": "modify", "điều chỉnh": "modify", "thay đổi": "modify",
            "di chuyển": "move", "xoay": "rotate", "thu phóng": "scale", "tỷ lệ": "scale",
            "phóng to": "scale_up", "thu nhỏ": "scale_down",
            "xóa": "erase", "gỡ bỏ": "erase", "loại bỏ": "erase",
            "lưu": "save",
            
            # Thao tác chuyên môn
            "ghi kích thước": "dimension", "đo kích thước": "dimension",
            "tô mặt cắt": "hatch", "tô vùng": "hatch", "tô mẫu": "hatch", "hatch": "hatch",
            "tạo lớp": "create_layer",
            "chuyển lớp": "change_layer", "đổi lớp": "change_layer",
            
            # Khóa tiếng Anh
            "draw": "draw", "create": "draw", "add": "draw",
            "modify": "modify",
            "move": "move", "rotate": "rotate", "scale": "scale",
            "erase": "erase", "delete": "erase",
            "save": "save",
            "dimension": "dimension",
            "create layer": "create_layer",
            "change layer": "change_layer",
        }
    
      
    # Khớp điểm tọa độ: (x,y,z) / (x,y) / x,y,z / x,y
    # Đưa lên làm thuộc tính lớp để tái sử dụng: cần loại bỏ phần tọa độ trước khi phân tích tham số số như bán kính,
    # nếu không tọa độ tâm đường tròn sẽ bị nhận nhầm làm bán kính
    _COORD_PATTERN = re.compile(
        r'\(?\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)(?:\s*,\s*(-?\d+\.?\d*))?\s*\)?')

    def color_to_index(self, color) -> Optional[int]:
        """Chuyển đổi tham số màu (tên hoặc chỉ số) thành chỉ số màu CAD (ACI)

        Hỗ trợ tên màu tiếng Việt, tiếng Anh và chỉ số nguyên từ 1-256 (256=ByLayer theo lớp).
        Trả về None nếu không nhận diện được (giữ nguyên màu theo lớp).
        """
        if color is None:
            return None
        if isinstance(color, int):
            return color if 1 <= color <= 256 else None
        if isinstance(color, str):
            text = color.strip()
            # Xử lý chuỗi thuần số theo chỉ số
            if text.isdigit():
                num = int(text)
                return num if 1 <= num <= 256 else None
            # Khớp chính xác tên màu trước, sau đó khớp không phân biệt hoa thường
            if text in self.color_rgb_map:
                return self.color_rgb_map[text]
            lower = text.lower()
            for name, idx in self.color_rgb_map.items():
                if name.lower() == lower:
                    return idx
        return None

    def extract_color_from_command(self, command: str) -> Optional[int]:
        # Trả về None khi không có thông tin màu sắc, giữ nguyên màu theo lớp (ByLayer) của thực thể
        if command is None:
            return None

        try:
            num = int(command)
            if num >= 1 and num <= 255:
                return num
        except:
            pass

        # Chuyển lệnh sang chữ thường
        command = command.lower()
        
        # Thử khớp tên màu (ưu tiên tên dài hơn để tránh "xám" khớp trước "xám nhạt")
        for color_name in sorted(self.color_rgb_map.keys(), key=len, reverse=True):
            if color_name.lower() in command:
                return self.color_rgb_map[color_name]
        
        # Thử khớp mô tả màu sắc (vd: "màu xám nhạt")
        color_pattern = r'(?:màu\s+)?([a-zA-ZÀ-ỹ]+(?:\s+[a-zA-ZÀ-ỹ]+){0,2})'
        color_matches = re.findall(color_pattern, command)
        
        for color_match in color_matches:
            # Kiểm tra xem có phải tên màu đã biết hay không
            normalized = color_match.strip()
            if normalized in self.color_rgb_map:
                return self.color_rgb_map[normalized]
                    
        # Trả về None khi không tìm thấy thông tin màu (giữ màu theo lớp, tránh ghi đè ngữ nghĩa ByLayer)
        return None
    
    def process_command(self, command: str) -> Dict[str, Any]:
        """Xử lý lệnh ngôn ngữ tự nhiên và trả về kết quả"""
        self.logger.info(f"Đang xử lý lệnh: {command}")
        
        # Phân tích lệnh
        parsed_command = self.parse_command(command)
      
        # Trả về kết quả phân tích
        return parsed_command
    
    def parse_command(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh ngôn ngữ tự nhiên và chuyển đổi thành tham số thao tác CAD"""
        self.logger.info(f"Đang phân tích lệnh: {command}")
        
        # Chuyển lệnh sang chữ thường và loại bỏ khoảng trắng thừa
        command = command.lower().strip()
        
        # Thử nhận diện loại lệnh
        command_type = self._identify_command_type(command)
        self.logger.debug(f"Loại lệnh được nhận diện: {command_type}")
        
        # Điều phối đến hàm xử lý tương ứng theo loại lệnh
        if command_type == "draw_line":
            return self._parse_draw_line(command)
        elif command_type == "draw_circle":
            return self._parse_draw_circle(command)
        elif command_type == "draw_arc":
            return self._parse_draw_arc(command)
        elif command_type == "draw_rectangle":
            return self._parse_draw_rectangle(command)
        elif command_type == "draw_polyline":
            return self._parse_draw_polyline(command)
        elif command_type == "draw_text":
            return self._parse_draw_text(command)
        elif command_type == "draw_hatch":
            return self._parse_draw_hatch(command)
        elif command_type == "add_dimension":
            return self._parse_add_dimension(command)
        elif command_type == "create_layer":
            return self._parse_create_layer(command)
        elif command_type == "save":
            return self._parse_save(command)
        else:
            # Mặc định trả về kết quả lỗi
            return {
                "type": "unknown",
                "error": "Không thể nhận diện loại lệnh",
                "original_command": command
            }
    
    def _identify_command_type(self, command: str) -> str:
        """Nhận diện loại lệnh"""
        # Kiểm tra loại thao tác
        for action, action_type in self.action_keywords.items():
            if action in command:
                # Xử lý hình dạng cơ bản
                # Từ khóa hình dạng phải được khớp theo thứ tự độ dài giảm dần (ưu tiên từ dài hơn):
                # nếu không, khớp chuỗi con sẽ khiến "tròn" khớp trước "cung tròn", nhận nhầm "vẽ một cung tròn" thành vẽ hình tròn
                # (tương tự như ưu tiên "xám nhạt" trước "xám" trong extract_color_from_command)
                for shape, shape_type in sorted(self.shape_keywords.items(),
                                                key=lambda kv: len(kv[0]), reverse=True):
                    if shape in command:
                        # "kích thước" trong "lớp kích thước" là một phần của tên lớp (vd: "tạo một lớp tên là lớp kích thước"),
                        # không được xem là từ khóa hình dạng của thao tác ghi kích thước, nếu không sẽ nhận nhầm thành add_dimension
                        if shape in ("kích thước", "ghi kích thước") and "lớp kích thước" in command:
                            continue
                        if action_type == "draw":
                            if shape_type == "line":
                                return "draw_line"
                            elif shape_type == "circle":
                                return "draw_circle"
                            elif shape_type == "arc":
                                return "draw_arc"
                            elif shape_type in ["rectangle", "square"]:
                                return "draw_rectangle"
                            elif shape_type == "polyline":
                                return "draw_polyline"
                            elif shape_type == "text":
                                return "draw_text"
                            elif shape_type == "dimension":
                                return "add_dimension"
        
        # Kiểm tra xem có phải lệnh tạo lớp hay không (từ khóa hành động cần đồng bộ với phạm vi nhận diện của _parse_create_layer)
        if any(kw in command for kw in ["lớp", "layer"]) and any(
            action in command for action in ["tạo", "tạo mới", "thêm", "lập", "khởi tạo", "create", "new", "add"]
        ):
            return "create_layer"
        
        # Kiểm tra xem có phải lệnh ghi kích thước hay không
        if any(kw in command for kw in ["ghi kích thước", "kích thước", "dimension"]):
            return "add_dimension"
        
        # Kiểm tra xem có phải lệnh tô mặt cắt hay không (hành động tô vùng không khớp nhánh draw trong vòng lặp hành động nên cần nhận diện riêng)
        if any(kw in command for kw in ["tô mặt cắt", "tô vùng", "tô mẫu", "hatch"]):
            return "draw_hatch"
        
        if any(kw in command for kw in ["lưu", "save"]):
            return "save"
        
        # Nếu không thể nhận diện, trả về loại không xác định
        return "unknown"
    
    def _extract_coordinates(self, text: str) -> List[Tuple[float, float, float]]:
        """Trích xuất các điểm tọa độ từ văn bản"""
        coordinates = []
        for match in self._COORD_PATTERN.finditer(text):
            x = float(match.group(1))
            y = float(match.group(2))
            z = float(match.group(3)) if match.group(3) else 0.0
            coordinates.append((x, y, z))
        
        return coordinates
    
    def _extract_numbers(self, text: str) -> List[float]:
        """Trích xuất các con số từ văn bản"""
        pattern = r'(-?\d+\.?\d*)'
        matches = re.findall(pattern, text)
        return [float(match) for match in matches]
    
    def _parse_draw_line(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh vẽ đường thẳng"""
        # Thử trích xuất tọa độ
        coordinates = self._extract_coordinates(command)
        
        if len(coordinates) >= 2:
            # Nếu tìm thấy ít nhất hai điểm tọa độ, dùng hai điểm đầu làm điểm đầu và điểm cuối
            return {
                "type": "draw_line",
                "start_point": coordinates[0],
                "end_point": coordinates[1]
            }
        else:
            # Nếu không tìm thấy đủ điểm tọa độ, thử dùng giá trị mặc định
            # Có thể thiết lập điểm đầu và điểm cuối mặc định tại đây tùy nhu cầu
            return {
                "type": "draw_line",
                "start_point": (0, 0, 0),
                "end_point": (100, 100, 0),
                "note": "Sử dụng tọa độ mặc định vì lệnh không cung cấp đủ thông tin tọa độ"
            }
    
    def _parse_draw_circle(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh vẽ hình tròn"""
        # Thử trích xuất tọa độ và bán kính
        coordinates = self._extract_coordinates(command)
        # Loại bỏ phần tọa độ trước khi trích xuất số để tránh nhận nhầm tọa độ tâm làm bán kính
        numbers = self._extract_numbers(self._COORD_PATTERN.sub(' ', command))
        
        # Trích xuất bán kính
        radius = None
        radius_pattern = r'(?:bán kính|\br\b|radius)[^\d]*?(-?\d+\.?\d*)'
        radius_match = re.search(radius_pattern, command, re.IGNORECASE)
        if radius_match:
            radius = float(radius_match.group(1))
        elif len(numbers) > 0:
            # Nếu không chỉ định rõ bán kính, dùng số đầu tiên tìm thấy làm bán kính
            radius = numbers[0]
        else:
            # Bán kính mặc định
            radius = 50.0
        
        # Trích xuất điểm tâm
        center = None
        if len(coordinates) > 0:
            center = coordinates[0]
        else:
            # Điểm tâm mặc định
            center = (0, 0, 0)
        
        return {
            "type": "draw_circle",
            "center": center,
            "radius": radius
        }
    
    def _parse_draw_arc(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh vẽ cung tròn"""
        # Thử trích xuất tọa độ, bán kính và góc
        coordinates = self._extract_coordinates(command)
        # Loại bỏ phần tọa độ trước khi trích xuất số để tránh nhận nhầm tọa độ tâm làm bán kính
        numbers = self._extract_numbers(self._COORD_PATTERN.sub(' ', command))
        
        # Trích xuất điểm tâm
        center = None
        if len(coordinates) > 0:
            center = coordinates[0]
        else:
            # Điểm tâm mặc định
            center = (0, 0, 0)
        
        # Trích xuất bán kính
        radius = None
        radius_pattern = r'(?:bán kính|\br\b|radius)[^\d]*?(-?\d+\.?\d*)'
        radius_match = re.search(radius_pattern, command, re.IGNORECASE)
        if radius_match:
            radius = float(radius_match.group(1))
        elif len(numbers) > 0:
            # Nếu không chỉ định rõ bán kính, dùng số đầu tiên tìm thấy làm bán kính
            radius = numbers[0]
        else:
            # Bán kính mặc định
            radius = 50.0
        
        # Trích xuất góc bắt đầu và góc kết thúc
        start_angle = 0.0
        end_angle = 90.0
        
        start_angle_pattern = r'(?:góc bắt đầu|góc đầu|start angle)[^\d]*?(-?\d+\.?\d*)'
        start_angle_match = re.search(start_angle_pattern, command, re.IGNORECASE)
        if start_angle_match:
            start_angle = float(start_angle_match.group(1))
        
        end_angle_pattern = r'(?:góc kết thúc|góc cuối|end angle)[^\d]*?(-?\d+\.?\d*)'
        end_angle_match = re.search(end_angle_pattern, command, re.IGNORECASE)
        if end_angle_match:
            end_angle = float(end_angle_match.group(1))
        
        # Đảm bảo tất cả các tham số bắt buộc đều có giá trị
        if center is None:
            center = (0, 0, 0)
        if radius is None:
            radius = 50.0
        if start_angle is None:
            start_angle = 0.0
        if end_angle is None:
            end_angle = 90.0
            
        self.logger.debug(f"Kết quả phân tích lệnh vẽ cung tròn: tâm={center}, bán kính={radius}, góc bắt đầu={start_angle}, góc kết thúc={end_angle}")
        
        return {
            "type": "draw_arc",
            "center": center,
            "radius": radius,
            "start_angle": start_angle,
            "end_angle": end_angle
        }
    
    def _parse_draw_rectangle(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh vẽ hình chữ nhật"""
        # Thử trích xuất tọa độ
        coordinates = self._extract_coordinates(command)
        
        if len(coordinates) >= 2:
            # Nếu tìm thấy ít nhất hai điểm tọa độ, dùng hai điểm đầu làm hai góc đối diện
            return {
                "type": "draw_rectangle",
                "corner1": coordinates[0],
                "corner2": coordinates[1]
            }
        else:
            # Nếu không tìm thấy đủ điểm tọa độ, thử trích xuất chiều rộng và chiều cao
            width = 100.0
            height = 100.0
            
            width_pattern = r'(?:chiều rộng|rộng|width)[^\d]*?(-?\d+\.?\d*)'
            width_match = re.search(width_pattern, command, re.IGNORECASE)
            if width_match:
                width = float(width_match.group(1))
            
            height_pattern = r'(?:chiều cao|cao|chiều dài|dài|height)[^\d]*?(-?\d+\.?\d*)'
            height_match = re.search(height_pattern, command, re.IGNORECASE)
            if height_match:
                height = float(height_match.group(1))
            
            # Nếu tìm thấy một điểm tọa độ, dùng nó làm điểm bắt đầu
            if len(coordinates) == 1:
                corner1 = coordinates[0]
                corner2 = (corner1[0] + width, corner1[1] + height, corner1[2])
            else:
                # Điểm đầu và điểm cuối mặc định
                corner1 = (0, 0, 0)
                corner2 = (width, height, 0)
            
            return {
                "type": "draw_rectangle",
                "corner1": corner1,
                "corner2": corner2
            }
    
    def _parse_draw_polyline(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh vẽ đa tuyến"""
        # Thử trích xuất tọa độ
        coordinates = self._extract_coordinates(command)
        
        # Kiểm tra xem có cần khép kín hay không
        closed = any(kw in command for kw in ["khép kín", "đóng kín", "kín", "closed"])
        
        if len(coordinates) >= 2:
            # Nếu tìm thấy ít nhất hai điểm tọa độ, dùng chúng làm các đỉnh của đa tuyến
            return {
                "type": "draw_polyline",
                "points": coordinates,
                "closed": closed
            }
        else:
            # Nếu không tìm thấy đủ điểm tọa độ, trả về lỗi
            return {
                "type": "error",
                "message": "Vẽ đa tuyến cần ít nhất hai điểm tọa độ"
            }
    
    def _parse_draw_text(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh vẽ văn bản"""
        # Thử trích xuất tọa độ
        coordinates = self._extract_coordinates(command)
        
        # Trích xuất nội dung văn bản
        # Lưu ý phải dùng nhóm (?:...) thay vì lớp ký tự [...] — lớp ký tự sẽ tách chuỗi thành tập ký tự đơn,
        # khớp bất kỳ ký tự đơn nào kèm dấu hai chấm
        text_pattern = r'(?:nội dung văn bản|nội dung|văn bản|text)\s*[:：]\s*[\"\'](.*?)[\"\']'
        text_match = re.search(text_pattern, command)
        
        text = ""
        if text_match:
            text = text_match.group(1)
        else:
            # Thử trích xuất nội dung trong dấu ngoặc kép/đơn làm văn bản
            quote_pattern = r'[\"\'](.*?)[\"\']'
            quote_match = re.search(quote_pattern, command)
            if quote_match:
                text = quote_match.group(1)
            else:
                # Nếu không tìm thấy nội dung trong dấu ngoặc, dùng văn bản mặc định
                text = "Văn bản mẫu"
        
        # Trích xuất chiều cao chữ
        height = 2.5  # Chiều cao mặc định
        height_pattern = r'(?:chiều cao|cao|height)[^\d]*?(-?\d+\.?\d*)'
        height_match = re.search(height_pattern, command, re.IGNORECASE)
        if height_match:
            height = float(height_match.group(1))
        
        # Trích xuất góc xoay
        rotation = 0.0  # Góc mặc định
        rotation_pattern = r'(?:góc xoay|xoay|góc|rotation)[^\d]*?(-?\d+\.?\d*)'
        rotation_match = re.search(rotation_pattern, command, re.IGNORECASE)
        if rotation_match:
            rotation = float(rotation_match.group(1))
        
        # Trích xuất điểm chèn
        position = None
        if len(coordinates) > 0:
            position = coordinates[0]
        else:
            # Điểm chèn mặc định
            position = (0, 0, 0)
        
        return {
            "type": "draw_text",
            "position": position,
            "text": text,
            "height": height,
            "rotation": rotation
        }
    
    def _parse_draw_hatch(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh vẽ tô mặt cắt (hatch)"""
        # Thử trích xuất tập điểm tọa độ
        coordinates = self._extract_coordinates(command)
        
        # Trích xuất tên mẫu tô
        pattern_name = "SOLID"  # Mặc định là tô đặc
        pattern_patterns = [
            r'(?:mẫu tô|mẫu|hoa văn|pattern)[^\w]*?["\'](.*?)["\']',
            r'(?:mẫu tô|mẫu|hoa văn|pattern)[^\w]*?(\w+)'
        ]
        
        for pattern in pattern_patterns:
            pattern_match = re.search(pattern, command, re.IGNORECASE)
            if pattern_match:
                pattern_name = pattern_match.group(1).upper()
                break
        
        # Trích xuất tỷ lệ tô
        scale = 1.0  # Tỷ lệ mặc định
        scale_pattern = r'(?:tỷ lệ|tỉ lệ|thu phóng|scale)[^\d]*?(\d+\.?\d*)'
        scale_match = re.search(scale_pattern, command, re.IGNORECASE)
        if scale_match:
            scale = float(scale_match.group(1))
        
        # Kiểm tra xem có đủ điểm để xác định biên vùng tô hay không
        if len(coordinates) >= 3:
            self.logger.debug(f"Kết quả phân tích lệnh tô mặt cắt: tập điểm={coordinates}, mẫu={pattern_name}, tỷ lệ={scale}")
            return {
                "type": "draw_hatch",
                "points": coordinates,
                "pattern_name": pattern_name,
                "scale": scale
            }
        else:
            # Nếu không đủ điểm, trả về thông báo lỗi
            self.logger.warning("Phân tích lệnh tô mặt cắt thất bại: cần ít nhất 3 điểm để xác định biên vùng tô")
            return {
                "type": "error",
                "message": "Tô mặt cắt cần ít nhất 3 điểm để xác định biên"
            }


    def _parse_add_dimension(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh thêm ghi kích thước tuyến tính (lấy hai điểm tọa độ đầu làm điểm đầu và điểm cuối)"""
        coordinates = self._extract_coordinates(command)
        if len(coordinates) < 2:
            return {
                "type": "error",
                "message": "Ghi kích thước cần hai điểm tọa độ (điểm đầu và điểm cuối)"
            }
        return {
            "type": "add_dimension",
            "start_point": coordinates[0],
            "end_point": coordinates[1]
        }

    def _parse_create_layer(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh tạo lớp (layer)

        Hỗ trợ nhiều trật tự từ: "tạo lớp tên là WALL", "tạo lớp WALL", "tạo WALL layer",
        dạng ngoặc kép/đơn "tạo lớp 'Tường'"
        """
        # Ưu tiên trích xuất tên lớp trong dấu ngoặc kép/đơn
        quote_match = re.search(r'["\'](.*?)["\']', command)
        if quote_match:
            return {"type": "create_layer", "layer_name": quote_match.group(1)}

        # "tên là / có tên / mang tên / đặt tên là X" — tên nằm sau từ khóa (vd: "tạo một lớp tên là WALL")
        # Nhóm bắt phải không tham lam và dừng trước "lớp/layer" hoặc cuối chuỗi
        m = re.search(r'(?:có\s+tên\s+là|đặt\s+tên\s+là|mang\s+tên|tên\s+là|tên|named)\s*[:：]?\s*([\wÀ-ỹ-]+?)(?=\s*(?:lớp|layer)|\s*$)', command)
        if m:
            return {"type": "create_layer", "layer_name": m.group(1)}

        # "lớp X" hoặc "layer X" — tên nằm ngay sau "lớp" / "layer" (vd: "tạo lớp WALL", "tạo lớp mới Tường")
        m = re.search(r'(?:lớp|layer)\s*(?:mới\s+)?(?:tên\s+là|tên|:|：)?\s*([\wÀ-ỹ-]+)', command)
        if m:
            return {"type": "create_layer", "layer_name": m.group(1)}

        # "tạo X lớp/layer" — tên nằm ngay trước "lớp" / "layer" (vd: "create WALL layer")
        m = re.search(r'(?:tạo|tạo mới|thêm|lập|khởi tạo|create|new|add)\s*(?:một\s+|a\s+)?(?:mới\s+|new\s+)?([\wÀ-ỹ-]+)\s*(?:lớp|layer)', command)
        if m:
            return {"type": "create_layer", "layer_name": m.group(1)}

        # Không nhận diện được tên lớp
        return {"type": "create_layer", "layer_name": None}

    def _parse_save(self, command: str) -> Dict[str, Any]:
        """Phân tích lệnh lưu bản vẽ"""
        # Thử trích xuất đường dẫn tệp
        path_pattern = r'(?:đường dẫn|lưu tại|lưu vào|lưu đến|path)[^\w]*?[\"\'](.*?)[\"\']'
        path_match = re.search(path_pattern, command, re.IGNORECASE)
        
        if path_match:
            file_path = path_match.group(1)
        else:
            # Đường dẫn tệp mặc định
            file_path = os.path.join(config["output"]["directory"], config["output"]["default_filename"])
        
        return {
            "type": "save",
            "file_path": file_path
        }
