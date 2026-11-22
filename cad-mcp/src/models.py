"""Mô hình đầu ra có cấu trúc của công cụ (MCP Structured Output, chuẩn 2025-06-18)

Mọi công cụ đều trả về thống nhất theo mô hình Pydantic, FastMCP sẽ tự động tạo outputSchema,
giúp ứng dụng khách (Antigravity / Claude / Cursor...) nhận trực tiếp structuredContent có cấu trúc,
thay vì chuỗi đối tượng COM khó phân tích như trước đây.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Kết quả thực thi công cụ chung"""
    success: bool = Field(description="Thực thi có thành công hay không")
    message: str = Field(description="Thông báo kết quả dễ đọc cho người dùng")
    entity_handles: List[str] = Field(
        default_factory=list,
        description="Danh sách handle thực thể liên quan/được tạo ra, dùng để chỉnh sửa hoặc truy vấn tiếp"
    )
    error: Optional[str] = Field(default=None, description="Thông tin lỗi khi thất bại")


class EntityInfo(BaseModel):
    """Thông tin tóm tắt của thực thể"""
    handle: str = Field(description="Handle thực thể (định danh duy nhất trong CAD)")
    entity_type: str = Field(description="Loại thực thể (line/circle/arc...)")
    layer: Optional[str] = Field(default=None, description="Tên lớp (layer) chứa thực thể")
    color: Optional[int] = Field(default=None, description="Chỉ số màu (ACI)")


class ListEntitiesResult(BaseModel):
    """Kết quả truy vấn danh sách thực thể"""
    success: bool = Field(description="Truy vấn có thành công hay không")
    message: str = Field(description="Mô tả kết quả")
    total: int = Field(default=0, description="Tổng số thực thể trong không gian mô hình (Model Space)")
    returned: int = Field(default=0, description="Số lượng thực thể thực tế trả về (giới hạn bởi limit)")
    entities: List[EntityInfo] = Field(default_factory=list, description="Danh sách thực thể")


class LayerInfo(BaseModel):
    """Thông tin lớp (layer)"""
    name: str = Field(description="Tên lớp")
    color: Optional[int] = Field(default=None, description="Chỉ số màu của lớp (ACI)")
    is_current: bool = Field(default=False, description="Có phải là lớp hiện hành hay không")
    is_frozen: bool = Field(default=False, description="Có đang bị đóng băng (frozen) hay không")
    is_locked: bool = Field(default=False, description="Có đang bị khóa (locked) hay không")
    is_on: bool = Field(default=True, description="Có đang bật hiển thị hay không")


class ListLayersResult(BaseModel):
    """Kết quả truy vấn danh sách lớp"""
    success: bool = Field(description="Truy vấn có thành công hay không")
    message: str = Field(description="Mô tả kết quả")
    layers: List[LayerInfo] = Field(default_factory=list, description="Danh sách lớp")


class EntityPropertiesResult(BaseModel):
    """Kết quả truy vấn thuộc tính chi tiết của thực thể"""
    success: bool = Field(description="Truy vấn có thành công hay không")
    message: str = Field(description="Mô tả kết quả")
    handle: str = Field(default="", description="Handle thực thể")
    entity_type: str = Field(default="", description="Loại thực thể")
    layer: Optional[str] = Field(default=None, description="Lớp chứa thực thể")
    color: Optional[int] = Field(default=None, description="Chỉ số màu (ACI)")
    linetype: Optional[str] = Field(default=None, description="Tên kiểu nét")
    lineweight: Optional[int] = Field(default=None, description="Độ dày nét")
    properties: Dict[str, Any] = Field(
        default_factory=dict,
        description="Thuộc tính hình học riêng theo từng loại thực thể (tọa độ/bán kính/nội dung chữ...)"
    )
    error: Optional[str] = Field(default=None, description="Thông tin lỗi khi thất bại")


class BlockInfo(BaseModel):
    """Thông tin khối (block)"""
    name: str = Field(description="Tên khối")
    entity_count: int = Field(default=0, description="Số lượng thực thể bên trong khối")


class ListBlocksResult(BaseModel):
    """Kết quả truy vấn danh sách khối"""
    success: bool = Field(description="Truy vấn có thành công hay không")
    message: str = Field(description="Mô tả kết quả")
    blocks: List[BlockInfo] = Field(default_factory=list, description="Danh sách khối")
