# Mẫu câu lệnh (Prompt) chạy AutoCAD AI Swarm

## 1. Kiểm tra kết nối AutoCAD sau khi cài
```text
Kiểm tra kết nối AutoCAD giúp tôi bằng công cụ list_layers.
```

## 2. Dựng 1 bản vẽ mặt bằng từ ảnh mẫu bằng đội Swarm
*(Kèm tải lên 1 ảnh mặt bằng `.jpg` / `.png`)*
```text
Hãy khởi tạo đội Swarm (struct-author, arch-author, annot-author, verifier, monitor)
và dựng lại chính xác mặt bằng trong ảnh này lên AutoCAD theo từng lớp:
lưới trục, bong bóng trục, dim kích thước, cột, tường + hatch, cửa + cung mở nét đứt,
cầu thang, nội thất, ghi chú tiếng Việt đủ dấu và tiêu đề bản vẽ.
Sau khi vẽ xong hãy chụp screenshot để verifier kiểm tra và sửa lỗi chồng chữ nếu có.
```

## 3. Dựng nhiều bản vẽ (Tầng 1, Tầng 2, Tổng mặt bằng) trong cùng 1 file DWG
*(Kèm tải lên các ảnh mặt bằng)*
```text
Hãy vẽ tất cả các mặt bằng trong các ảnh đính kèm lên cùng một bản vẽ AutoCAD,
đặt các bản vẽ nằm ngang cạnh nhau kèm đầy đủ khung tên, kích thước, nội thất và tiếng Việt đủ dấu.
Sau khi vẽ xong chụp ảnh từng bản vẽ kiểm tra và lưu thành file .dwg.
```
