import logging
import math
import time
import os
import io
import json
import threading
import queue
import concurrent.futures
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union

# Thống nhất sử dụng singleton cấu hình (thay thế việc các module tự đọc lại config.json trước đây)
try:
    from .config import get_config
except ImportError:
    from config import get_config

config = get_config()

try:
    import win32com.client
    # pythoncom là một phần của pywin32, không cần cài đặt riêng
    import pythoncom
except ImportError:
    logging.error("Không thể import win32com.client hoặc pythoncom, vui lòng đảm bảo đã cài đặt thư viện pywin32")
    raise

try:
    import anyio
except ImportError:
    # anyio là dependency của mcp SDK, bình thường sẽ được cài đặt cùng mcp
    anyio = None

logger = logging.getLogger('cad_controller')

# Lỗi COM tạm thời: lời gọi bị CAD đang bận từ chối (RPC_E_CALL_REJECTED) —
# Khi CAD đang thực thi lệnh/hiển thị hộp thoại/tái tạo (regen) sẽ tạm thời từ chối lời gọi COM, thử lại sau là có thể phục hồi
_RPC_E_CALL_REJECTED = -2147418111

# Lỗi lưu tạm thời của AutoCAD ("Lỗi khi lưu tài liệu") —
# Gọi SaveAs khi đang lưu/mở/đóng sẽ báo lỗi này, thử lại sau là có thể phục hồi
_ACAD_SAVE_TRANSIENT = -2145320861

# Lỗi tham chiếu COM chết: tiến trình CAD đã bị người dùng đóng/treo, con trỏ interface đang giữ không còn hợp lệ
# RPC_E_DISCONNECTED(-2147417848): đối tượng đã ngắt kết nối với client
# RPC_S_SERVER_UNAVAILABLE(-2147023174): máy chủ RPC không khả dụng
# Khác với lỗi tạm thời, thử lại loại lỗi này là vô nghĩa, cần đặt lại trạng thái kết nối để thao tác sau kết nối lại
_DEAD_COM_HRESULTS = (-2147417848, -2147023174)


def _is_retryable_com_error(e) -> bool:
    """Kiểm tra xem có phải lỗi COM tạm thời có thể thử lại không (bị từ chối do bận hoặc xung đột khi lưu)"""
    if getattr(e, "hresult", None) == _RPC_E_CALL_REJECTED:
        return True
    # Lỗi lưu tạm thời nằm trong tuple excepinfo của com_error
    info = tuple(getattr(e, "excepinfo", None) or ())
    return _ACAD_SAVE_TRANSIENT in info


def _is_retryable_attr_error(e, retry_attr) -> bool:
    """Kiểm tra xem có phải AttributeError do dynamic dispatch của pywin32 chuyển đổi từ "tìm kiếm thành viên bị từ chối" hay không

    Trong dynamic dispatch, khi việc tìm kiếm thành viên (GetIDsOfNames) bị CAD đang bận từ chối,
    pywin32 sẽ nuốt com_error và ném ra AttributeError, làm mất hresult (từng gây lỗi "Open.Close"
    khi đóng ngay sau khi mở tài liệu và khiến cơ chế thử lại cấp lời gọi hoàn toàn vô hiệu).
    Hai định dạng thông báo đã biết: dynamic.py ném "Open.Close"; client/__init__.py ném
    "'<COMObject Open>' object has no attribute 'Close'".
    Không bao giờ thử lại nếu không chỉ định tên thành viên.
    """
    if not retry_attr or not isinstance(e, AttributeError):
        return False
    msg = str(e)
    return msg.endswith("." + retry_attr) or msg.endswith(f"attribute '{retry_attr}'")


def _call_with_com_retry(fn, args, kwargs, retries: int = 20, delay: float = 0.5, retry_attr: str = None):
    """Thực thi lời gọi COM, tự động thử lại khi bận (RPC_E_CALL_REJECTED) hoặc xung đột lưu tạm thời

    Tổng thời gian chờ tối đa khoảng retries*delay giây, vượt quá sẽ ném ngoại lệ gốc.
    Xem mô tả của _is_retryable_attr_error về retry_attr.
    """
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            # Chỉ thử lại với lỗi tạm thời (COM bận/xung đột lưu/tìm kiếm thành viên bị từ chối), các ngoại lệ khác ném thẳng
            if (_is_retryable_com_error(e) or _is_retryable_attr_error(e, retry_attr)) \
                    and attempt < retries - 1:
                logger.debug(f"CAD đang bận hoặc xung đột khi lưu, lời gọi COM bị từ chối, thử lại lần thứ {attempt + 1}...")
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("unreachable")  # Về mặt logic không thể chạm tới, chỉ để đảm bảo kiểu trả về đầy đủ


def _print_window(hwnd):
    """Dùng PrintWindow để chụp cửa sổ (không cần đưa lên foreground, không bị che khuất), trả về None nếu thất bại hoặc đen hoàn toàn"""
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    user32 = ctypes.windll.user32
    # Khai báo tường minh kiểu tham số của PrintWindow: ctypes mặc định truyền Python int như C int 32-bit,
    # giá trị handle vượt quá 2^31-1 sẽ ném OverflowError, mà ngoại lệ đó lại bị except tầng trên nuốt mất
    # và âm thầm quay về chụp màn hình cũ (chính là hành vi hàm này muốn tránh).
    try:
        from ctypes import wintypes
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
    except Exception:
        pass
    # Nhận thức DPI cấp luồng: nếu không, trên màn hình có scale, GetWindowRect sẽ trả về tọa độ logic khiến kích thước bitmap bị sai
    old_ctx = None
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        old_ctx = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        pass
    hwnd_dc = mfc_dc = save_dc = bmp = None
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        # 2 = PW_RENDERFULLCONTENT, bao gồm vùng đồ họa vẽ bằng DirectX
        if not user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2):
            return None
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                               bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        # Đen hoàn toàn nghĩa là cửa sổ chưa thực sự vẽ nội dung, chuyển sang chụp màn hình
        if img.convert("L").getextrema()[1] == 0:
            return None
        return img
    finally:
        if bmp is not None:
            win32gui.DeleteObject(bmp.GetHandle())
        if save_dc is not None:
            save_dc.DeleteDC()
        if mfc_dc is not None:
            mfc_dc.DeleteDC()
        if hwnd_dc is not None:
            win32gui.ReleaseDC(hwnd, hwnd_dc)
        if old_ctx:
            try:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old_ctx))
            except Exception:
                pass


class COMExecutor:
    """Bộ thực thi luồng COM chuyên dụng

    Mô hình Apartment của Windows COM yêu cầu: cùng một con trỏ interface phải được dùng trên
    luồng đã tạo ra nó; truy cập trực tiếp xuyên luồng sẽ gây lỗi lời gọi hoặc thậm chí crash tiến trình.
    Bộ thực thi này tuần tự hóa tất cả lời gọi COM vào một luồng STA duy nhất, mang lại hai lợi ích:
    1. An toàn luồng — nhiều công cụ gọi đồng thời cũng không xảy ra truy cập xuyên apartment;
    2. Không chặn vòng lặp sự kiện asyncio — các lời gọi COM có tính chặn (như chờ CAD khởi động 20 giây)
       đều diễn ra trên luồng chuyên dụng, máy chủ vẫn có thể phản hồi các yêu cầu khác.
    """

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="cad-com", daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        """Vòng lặp chính của luồng COM: sau khi khởi tạo COM sẽ liên tục lấy tác vụ từ hàng đợi để thực thi"""
        # Chỉ khởi tạo một lần trong suốt vòng đời tiến trình, không gọi CoUninitialize (giữ apartment tồn tại)
        pythoncom.CoInitialize()
        while True:
            fn, args, kwargs, future = self._queue.get()
            if fn is None:
                # Tín hiệu dừng
                future.set_result(True)
                break
            try:
                future.set_result(_call_with_com_retry(fn, args, kwargs))
            except BaseException as e:
                future.set_exception(e)
            finally:
                self._queue.task_done()

    def submit(self, fn, *args, **kwargs):
        """Thực thi đồng bộ fn trên luồng COM và trả về kết quả (chặn luồng đang gọi)"""
        if threading.current_thread() is self._thread:
            # Đã ở trên luồng COM (lời gọi lồng nhau nội bộ), thực thi trực tiếp để tránh deadlock
            return fn(*args, **kwargs)
        future = concurrent.futures.Future()
        self._queue.put((fn, args, kwargs, future))
        return future.result()

    async def run(self, fn, *args, **kwargs):
        """Thực thi bất đồng bộ lời gọi COM: đưa submit có tính chặn vào thread pool để tránh chặn event loop"""
        if anyio is None:
            # Quay về thực thi đồng bộ khi không có anyio (không nên xảy ra)
            return self.submit(fn, *args, **kwargs)
        return await anyio.to_thread.run_sync(lambda: self.submit(fn, *args, **kwargs))


class CADController:
    """Lớp điều khiển CAD, chịu trách nhiệm tương tác với ứng dụng CAD"""

    def __init__(self):
        """Khởi tạo bộ điều khiển CAD"""
        self.app = None
        self.doc = None
        self.entities = {}  # Lưu tham chiếu thực thể của các hình đã tạo để chỉnh sửa về sau
        # Bộ thực thi luồng COM chuyên dụng: mọi lời gọi COM đều được thực thi tuần tự qua đây
        self.executor = COMExecutor()
        # Tải tham số từ tệp cấu hình
        self.startup_wait_time = config["cad"]["startup_wait_time"]
        self.command_delay = config["cad"]["command_delay"]
        # Lấy loại CAD
        self.cad_type = config["cad"]["type"]
        # Lý do thất bại kết nối gần nhất (ProgID, HRESULT, v.v.), để tầng trên gợi ý người dùng kiểm tra
        self.last_connect_error = ""
        # Danh sách các giá trị độ dày nét hợp lệ
        self.valid_lineweights = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211]
        logger.info("Bộ điều khiển CAD đã được khởi tạo")

    def _get_app_info(self) -> Tuple[List[str], str]:
        """Trả về (danh sách COM ProgID ứng viên, tên hiển thị) theo loại CAD đã cấu hình

        Lưu ý: Việc tra cứu ProgID của COM không phân biệt hoa thường, GCAD.Application tương đương Gcad.Application.
        Các phiên bản khác nhau của cùng một phần mềm CAD có thể đăng ký ProgID khác nhau (như GstarCAD có cả GCAD và GstarCAD),
        trả về danh sách ứng viên để start_cad thử lần lượt, tránh trường hợp máy đích chỉ đăng ký một trong hai nên không kết nối được.

        ProgID của AutoCAD được gắn cứng theo dòng phiên bản: mỗi ProgID tương ứng với CLSID riêng,
        GetActiveObject đi theo luồng "ProgID → CLSID → Running Object Table", nên AutoCAD.Application không kèm phiên bản
        chỉ kết nối được với dòng mà CurVer trỏ tới. Khi máy cài nhiều phiên bản hoặc người dùng chạy phiên bản không phải CurVer,
        kết nối sẽ thất bại trực tiếp (hiển thị như "CAD chưa chạy").
        Thứ tự ứng viên: progid chỉ định tường minh trong config.json → ProgID có phiên bản tìm thấy trong Registry (giảm dần theo phiên bản)
        → tên chung dự phòng.
        """
        # Ánh xạ COM ProgID của từng phần mềm CAD (loại không khớp sẽ quay về AutoCAD)
        app_map = {
            "autocad": (["AutoCAD.Application"], "AutoCAD"),
            "gcad": (["GCAD.Application", "GstarCAD.Application"], "GstarCAD"),
            "gstarcad": (["GCAD.Application", "GstarCAD.Application"], "GstarCAD"),
            "zwcad": (["ZWCAD.Application"], "ZWCAD"),
        }
        base_ids, app_name = app_map.get(self.cad_type.lower(),
                                        (["AutoCAD.Application"], "AutoCAD"))

        candidates: List[str] = []
        # 1) Ưu tiên ProgID do người dùng chỉ định tường minh
        custom = str((config.get("cad") or {}).get("progid") or "").strip()
        if custom:
            candidates.append(custom)
        # 2) ProgID có phiên bản tìm thấy trong Registry (chỉ AutoCAD có khái niệm dòng phiên bản)
        if self.cad_type.lower() == "autocad":
            candidates.extend(self._discover_autocad_progids())
        # 3) Tên chung dự phòng
        candidates.extend(base_ids)

        seen = set()
        unique: List[str] = []
        for prog_id in candidates:
            if prog_id and prog_id not in seen:
                seen.add(prog_id)
                unique.append(prog_id)
        return unique, app_name

    @staticmethod
    def _discover_autocad_progids() -> List[str]:
        """Quét Registry để tìm các AutoCAD ProgID có phiên bản (AutoCAD.Application.25.1, v.v.), sắp xếp giảm dần theo số phiên bản

        Khi cài nhiều phiên bản AutoCAD, ProgID có phiên bản là cách duy nhất xác định chắc chắn kết nối đến phiên bản nào.
        """
        prefix = "AutoCAD.Application."
        found: List[str] = []
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "") as root:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(root, index)
                        index += 1
                    except OSError:
                        break
                    if name.startswith(prefix):
                        found.append(name)
        except Exception as e:
            logger.warning(f"Quét ProgID có phiên bản của AutoCAD thất bại: {str(e)}")
            return []

        def version_key(name: str) -> tuple:
            try:
                return tuple(int(x) for x in name[len(prefix):].split("."))
            except ValueError:
                return ()

        return sorted(found, key=version_key, reverse=True)

    @staticmethod
    def _com_error_text(e) -> str:
        """Trích xuất thông tin dễ đọc của ngoại lệ COM (HRESULT + mô tả) để người dùng dễ kiểm tra nguyên nhân kết nối thất bại"""
        parts: List[str] = []
        hresult = getattr(e, "hresult", None)
        if hresult is not None:
            parts.append(f"HRESULT 0x{hresult & 0xFFFFFFFF:08X}")
        info = tuple(getattr(e, "excepinfo", None) or ())
        # excepinfo: (wCode, wReserved, source, description, helpFile, helpContext, scode)
        if len(info) > 3 and info[3]:
            parts.append(str(info[3]))
        if not parts:
            return str(e)
        return " ".join(parts)

    def start_cad(self) -> bool:
        """Khởi động CAD và tạo hoặc mở một tài liệu"""
        # Lưu tham chiếu phiên bản cũ (nếu có) để dọn dẹp sau
        old_app = None
        if self.app is not None:
            old_app = self.app
            self.app = None
            self.doc = None

        failures: List[str] = []
        try:
            # Khởi tạo COM (lũy đẳng nếu đã ở trên luồng COM)
            pythoncom.CoInitialize()

            app_ids, app_name = self._get_app_info()
            logger.info(f"Danh sách ProgID ứng viên của {app_name}: {app_ids}")

            # Bước 1: Thử kết nối tới phiên bản đang chạy (duyệt qua các ProgID ứng viên)
            for app_id in app_ids:
                try:
                    logger.info(f"Đang thử kết nối tới phiên bản {app_name} hiện có ({app_id})...")
                    self.app = win32com.client.GetActiveObject(app_id)
                    logger.info(f"Đã kết nối thành công tới phiên bản {app_name} đang chạy")
                    break
                except Exception as e:
                    logger.info(f"Không tìm thấy phiên bản {app_name} đang chạy ({app_id}), tiếp tục thử: {str(e)}")
                    failures.append(f"{app_id}: {self._com_error_text(e)}")

            # Bước 2: Nếu kết nối thất bại thì khởi chạy phiên bản mới (cũng duyệt qua các ProgID ứng viên)
            if self.app is None:
                for app_id in app_ids:
                    try:
                        logger.info(f"Đang khởi động phiên bản {app_name} ({app_id})...")
                        self.app = win32com.client.Dispatch(app_id)
                        self._call(setattr, self.app, "Visible", True)
                        break
                    except Exception as e:
                        logger.info(f"Khởi động phiên bản {app_name} thất bại ({app_id}), tiếp tục thử: {str(e)}")
                        failures.append(f"{app_id}: {self._com_error_text(e)}")
                if self.app is None:
                    raise Exception(f"Không thể kết nối hoặc khởi động {app_name} (ProgID ứng viên: {app_ids})")
                # Chờ CAD khởi động
                time.sleep(self.startup_wait_time)  # Dùng thời gian chờ đã cấu hình

            # Bước 3: Lấy hoặc tạo tài liệu (truy cập tập hợp và thuộc tính đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi)
            docs = self._call(getattr, self.app, "Documents")
            if self._call(getattr, docs, "Count") == 0:
                logger.info("Đang tạo tài liệu mới...")
                self.doc = self._call_attr(docs, "Add")
            else:
                try:
                    logger.info("Đang lấy tài liệu hiện hành...")
                    self.doc = self._call(getattr, self.app, "ActiveDocument")
                except Exception:
                    # Khi CAD cấu hình "không tạo tài liệu mới khi khởi động" sẽ không có tài liệu hiện hành, chuyển sang tạo thủ công.
                    # Lưu ý: Tuyệt đối không đóng bản vẽ người dùng đang mở để "ép lấy tài liệu" —
                    # Close(False) sẽ làm mất ngay các thay đổi chưa lưu (logic dự phòng cũ từng làm vậy,
                    # là lỗi mức mất dữ liệu); khi không lấy được tài liệu hiện hành thì tạo mới mới là cách an toàn
                    logger.info("Không có tài liệu hiện hành, chuyển sang tạo tài liệu mới...")
                    self.doc = self._call_attr(docs, "Add")

            # Kiểm tra an toàn bổ sung và chờ thêm
            time.sleep(2)  # Cho CAD thêm thời gian xử lý việc tạo tài liệu

            if self.doc is None:
                raise Exception("Không thể lấy đối tượng Document hợp lệ")

            # Thử đọc thuộc tính tài liệu để xác minh tính hợp lệ
            try:
                name = self._call(getattr, self.doc, "Name")
                logger.info(f"Tên tài liệu: {name}")
            except Exception as name_ex:
                logger.error(f"Không thể đọc tên tài liệu: {str(name_ex)}")
                raise Exception("Đối tượng tài liệu không hợp lệ")

            logger.info("CAD đã khởi động và sẵn sàng")
            return True

        except Exception as e:
            logger.error(f"Khởi động CAD thất bại: {str(e)}")
            # Ghi lại lý do thất bại để tầng trên gợi ý người dùng kiểm tra (trước đây mọi lỗi đều chỉ hiện "CAD chưa chạy")
            detail = str(e)
            if failures:
                # Cùng một ProgID có thể thất bại ở cả bước kết nối và khởi chạy, lọc trùng để giữ lại một dòng
                unique_failures = list(dict.fromkeys(failures))
                detail = f"{detail}; Kết quả thử từng ProgID: {'; '.join(unique_failures)}"
            self.last_connect_error = detail
            return False
        finally:
            # Dọn dẹp phiên bản cũ
            if old_app is not None:
                try:
                    del old_app
                except:
                    pass

    def is_running(self) -> bool:
        """Kiểm tra xem CAD có đang chạy hay không"""
        return self.app is not None and self.doc is not None

    def _pt(self, point) -> "win32com.client.VARIANT":
        """Đóng gói điểm tọa độ thành mảng double VARIANT mà COM chấp nhận

        Tự động bổ sung tọa độ 2D thành 3D (Z=0) để dùng cho các phương thức như AddLine/AddCircle.
        """
        if point is None or len(point) < 2:
            raise ValueError(f"Điểm tọa độ cần ít nhất hai thành phần: {point}")
        x, y = float(point[0]), float(point[1])
        z = float(point[2]) if len(point) > 2 else 0.0
        return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y, z])

    @staticmethod
    def _safe_attr(obj, name: str, default=None):
        """Đọc an toàn thuộc tính đối tượng COM, trả về giá trị mặc định nếu loại thực thể không khớp"""
        try:
            return getattr(obj, name)
        except Exception:
            return default

    @classmethod
    def _read_color(cls, obj) -> Optional[int]:
        """Đọc màu đối tượng dưới dạng số nguyên chỉ số ACI (gọi trên luồng COM)

        Tương thích với nhiều khác biệt của wrapper COM:
        1. Wrapper tĩnh gen_py (bộ đệm EnsureDispatch/makepy) phân biệt hoa thường tên thuộc tính,
           trong thư viện kiểu AutoCAD thuộc tính màu thực thể là chữ thường color;
        2. Trong dynamic dispatch, tên IDispatch không phân biệt hoa thường, có thể truy cập Color;
        3. Giá trị có thể là số nguyên (thuộc tính Color/color bản cũ) hoặc đối tượng AcadAcCmColor (TrueColor).
        """
        for name in ("color", "Color", "ColorIndex"):
            raw = cls._safe_attr(obj, name)
            if raw is None:
                continue
            if isinstance(raw, int):
                return raw
            # Phiên bản CAD mới trả về đối tượng AcadAcCmColor, lấy ColorIndex của nó
            idx = cls._safe_attr(raw, "ColorIndex")
            if idx is not None:
                return idx
        return None

    @staticmethod
    def _write_color(entity, color: int) -> None:
        """Đặt màu thực thể theo chỉ số ACI (tương thích cả hai wrapper: color chữ thường của gen_py và Color của dynamic dispatch)"""
        try:
            entity.color = color  # Tên thuộc tính thực dưới wrapper tĩnh gen_py
        except AttributeError:
            entity.Color = color  # Trong dynamic dispatch, IDispatch không phân biệt hoa thường

    def _call(self, fn, *args, **kwargs):
        """Thực thi một lời gọi COM có cơ chế thử lại (chỉ gọi trên luồng COM)

        Khi CAD bận (RPC_E_CALL_REJECTED), lời gọi bị từ chối nghĩa là chưa được thực thi,
        nên thử lại ở cấp một lời gọi COM là an toàn; còn thử lại cả phương thức nghiệp vụ
        có thể vẽ trùng lặp (trường hợp nửa đầu đã thành công, nửa sau bị từ chối), do đó
        độ mịn thử lại bắt buộc phải ở cấp lời gọi.
        """
        try:
            return _call_with_com_retry(fn, args, kwargs)
        except Exception as e:
            # Khi tiến trình CAD đã bị người dùng đóng, mọi tham chiếu COM đang giữ đều mất hiệu lực (thử lại vô nghĩa):
            # đặt lại trạng thái kết nối để thao tác tiếp theo đi vào nhánh is_running()==False và tự kết nối lại,
            # nếu không tất cả công cụ sẽ liên tục báo "thao tác thất bại" và không bao giờ kích hoạt khởi động lại
            if getattr(e, "hresult", None) in _DEAD_COM_HRESULTS:
                logger.warning("Phát hiện tiến trình CAD đã thoát, đặt lại trạng thái kết nối (thao tác tiếp theo sẽ khởi động lại CAD)")
                self.app = None
                self.doc = None
            raise

    def _call_attr(self, obj, name: str, *args, **kwargs):
        """Thực thi obj.name(*args) có thử lại: cả việc tra cứu thành viên và lời gọi đều được bảo vệ thử lại

        Trong dynamic dispatch (máy không có cache gen_py, tức trạng thái mặc định của người dùng cuối),
        bản thân việc tra cứu obj.name cũng là một lời gọi COM (GetIDsOfNames) và có thể bị CAD đang bận
        từ chối, nên không thể viết self._call(obj.name, ...) vì tra cứu diễn ra trước khi vào _call.
        Phương thức này gộp tra cứu và lời gọi thành một đơn vị thử lại, đồng thời đưa cả AttributeError
        do pywin32 chuyển đổi từ lỗi "tra cứu bị từ chối" vào diện thử lại tạm thời.
        """
        return _call_with_com_retry(lambda: getattr(obj, name)(*args, **kwargs),
                                    (), {}, retry_attr=name)

    def _ms(self):
        """Lấy không gian mô hình ModelSpace (đọc thuộc tính cũng có thể bị CAD đang bận từ chối, cần có thử lại)"""
        return self._call(getattr, self.doc, "ModelSpace")

    @contextmanager
    def _undo_group(self):
        """Gói thao tác công cụ hiện tại thành một nhóm hoàn tác độc lập (đảm bảo UNDO 1 hoàn tác đúng một bước)

        AutoCAD sẽ gộp tất cả thay đổi COM trong lúc dòng lệnh rảnh thành cùng một nhóm hoàn tác,
        nếu không đánh dấu thì một lần UNDO có thể hoàn tác toàn bộ loạt vẽ.
        An toàn khi dùng lồng nhau (ví dụ draw_rectangle → draw_polyline), nhóm trong sẽ gộp vào nhóm ngoài.
        """
        doc = self.doc
        # Lời gọi đánh dấu cũng có thể bị CAD đang bận từ chối, cần có thử lại
        self._call_attr(doc, "StartUndoMark")
        try:
            yield
        finally:
            try:
                self._call_attr(doc, "EndUndoMark")
            except Exception:
                # Trong tình huống cực đoan như tài liệu đã chuyển đổi khiến không thể đóng dấu, bỏ qua (dấu sẽ tự đặt lại theo tài liệu)
                pass

    def _check_target_not_occupied(self, file_path: str) -> None:
        """Kiểm tra đường dẫn lưu đích có đang bị bản vẽ khác đang mở chiếm dụng hay không

        AutoCAD sẽ khóa tệp DWG tương ứng với các tài liệu đang mở; gọi SaveAs vào các đường dẫn đó sẽ liên tục báo
        "Lỗi khi lưu tài liệu" (-2145320861) và thử lại cũng vô ích (thực đo chờ 40 giây vẫn thất bại),
        do đó kiểm tra trước để báo lỗi nhanh. Nếu tài liệu hiện tại đang chiếm cùng đường dẫn thì là lưu đè tại chỗ, cho phép.
        """
        try:
            norm = os.path.normcase(file_path)
            current_name = self._safe_attr(self.doc, "Name")
            docs = self._call(getattr, self.app, "Documents")
            for i in range(self._call(getattr, docs, "Count")):
                doc = self._call_attr(docs, "Item", i)
                full = self._safe_attr(doc, "FullName")
                if full and os.path.normcase(str(full)) == norm:
                    # Tên tài liệu đang mở là duy nhất trong cùng phiên CAD, trùng tên tức là tài liệu hiện tại
                    if self._safe_attr(doc, "Name") == current_name:
                        continue
                    raise ValueError(
                        f"Tệp đích đang bị bản vẽ đang mở khác chiếm dụng, không thể lưu ghi đè: {file_path} (vui lòng đóng bản vẽ đang chiếm tệp này trước)"
                    )
        except ValueError:
            raise
        except Exception:
            # Nếu bản thân bước kiểm tra trước thất bại (như CAD bận) thì không chặn việc lưu, để SaveAs và cơ chế thử lại cấp lời gọi xử lý
            pass

    def save_drawing(self, file_path: str) -> bool:
        """Lưu bản vẽ hiện tại tới đường dẫn chỉ định

        Raises:
            ValueError: Khi tệp đích đang bị bản vẽ đang mở khác chiếm dụng
        """
        if not self.is_running():
            logger.error("CAD chưa chạy, không thể lưu bản vẽ")
            return False

        try:
            # Đường dẫn tương đối phải chuyển sang tuyệt đối trước: SaveAs phân giải đường dẫn trong tiến trình CAD,
            # mà thư mục làm việc của CAD thường khác thư mục làm việc của máy chủ MCP (thực tế tệp tương đối sẽ bị lưu chỗ khác)
            file_path = os.path.abspath(file_path)

            # Đảm bảo thư mục tồn tại (sau abspath chắc chắn có dirname; thư mục có thể chưa được tạo)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            # Khi đích bị chiếm dụng, SaveAs sẽ liên tục báo lỗi và thử lại vô ích, nên kiểm tra trước để thất bại nhanh
            self._check_target_not_occupied(file_path)

            # Lưu tệp (sau khi SaveAs trả về, CAD vẫn có thể tạm thời từ chối các lời gọi chỉnh sửa tiếp theo,
            # lời gọi COM của công cụ sau sẽ tự động chờ phục hồi thông qua cơ chế thử lại cấp lời gọi)
            self._call_attr(self.doc, "SaveAs", file_path)
            logger.info(f"Bản vẽ đã được lưu tại: {file_path}")

            return True
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Lưu bản vẽ thất bại: {str(e)}")
            return False

    def refresh_view(self) -> None:
        """Bắt buộc tái tạo (Regen) khung nhìn (chỉ dùng cho ngữ cảnh làm mới tường minh, không gọi sau khi vẽ/chỉnh sửa)

        Lưu ý: AutoCAD ghi lại lệnh Regen sau thay đổi COM vào ngăn xếp hoàn tác, khiến
        lệnh UNDO tiếp theo trở thành thao tác rỗng không thấy thay đổi (thực đo đúng dù có đặt dấu undo hay không).
        Vì vậy các phương thức vẽ/chỉnh sửa không tự động làm mới nữa — thực thể mới thêm sẽ tự hiển thị,
        khi cần làm mới toàn bản vẽ hãy dùng zoom_extents hoặc send_command thực thi ZOOM.
        """
        if self.is_running():
            try:
                self._call_attr(self.doc, "Regen", 1)  # acAllViewports = 1
            except Exception as e:
                logger.error(f"Làm mới khung nhìn thất bại: {str(e)}")

    def validate_lineweight(self, lineweight) -> int:
        """Xác thực và trả về giá trị độ dày nét hợp lệ

        Nếu giá trị độ dày nét cung cấp không nằm trong danh sách hợp lệ, trả về giá trị mặc định 0

        Args:
            lineweight: Giá trị độ dày nét cần xác thực

        Returns:
            Giá trị độ dày nét hợp lệ
        """
        if lineweight is None:
            return None

        # Kiểm tra độ dày nét có nằm trong danh sách hợp lệ không
        if lineweight in self.valid_lineweights:
            return lineweight
        else:
            logger.warning(f"Giá trị độ dày nét {lineweight} không hợp lệ, sẽ dùng giá trị mặc định 0")
            return 0

    def _apply_style(self, entity, layer=None, color=None, lineweight=None) -> None:
        """Áp dụng thống nhất lớp/màu sắc/độ dày nét cho thực thể (phần dùng chung của các phương thức vẽ)

        Thiết lập thuộc tính là thao tác lũy đẳng, thực hiện lần lượt có thử lại: khi một thuộc tính bị CAD từ chối
        sẽ thử lại mà không ảnh hưởng đến các phần đã thành công.
        """
        # Nếu có chỉ định lớp, thiết lập lớp
        if layer:
            # Đảm bảo lớp tồn tại (chỉ tạo không kích hoạt: tránh âm thầm đổi mất lớp hiện hành người dùng đang dùng)
            target_layer = self._ensure_layer(layer)
            # Đặt lớp cho thực thể (dùng tên lớp thực CAD trả về để tránh lệch hoa thường với tên truyền vào)
            self._call(setattr, entity, "Layer", self._call(getattr, target_layer, "Name"))

        # Nếu có chỉ định màu, thiết lập màu
        if color is not None:
            self._call(self._write_color, entity, color)

        if lineweight is not None:
            lw = self.validate_lineweight(lineweight)
            # Dưới wrapper gen_py tên thuộc tính là Lineweight (khác biệt với LineWeight tương tự Color/color)
            try:
                self._call(setattr, entity, "Lineweight", lw)
            except AttributeError:
                self._call(setattr, entity, "LineWeight", lw)

    def draw_line(self, start_point: Tuple[float, float, float],
                 end_point: Tuple[float, float, float], layer: str = None, color: int = None, lineweight=None) -> Any:
        """Vẽ đoạn thẳng"""
        if not self.is_running():
            return None

        try:
            # Dùng VARIANT đóng gói dữ liệu điểm tọa độ (_pt tự động bổ sung trục Z)
            start_array = self._pt(start_point)
            end_array = self._pt(end_point)

            # Thêm đoạn thẳng (nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận)
            with self._undo_group():
                line = self._call_attr(self._ms(), "AddLine", start_array, end_array)

                # Thiết lập lớp, màu sắc, độ dày nét
                self._apply_style(line, layer, color, lineweight)

            logger.debug(f"Đã vẽ đoạn thẳng: điểm đầu {start_point}, điểm cuối {end_point}, lớp {layer if layer else 'mặc định'}, màu {color if color is not None else 'mặc định'}")
            return line

        except Exception as e:
            logger.error(f"Lỗi khi vẽ đoạn thẳng: {str(e)}")
            return None

    def draw_circle(self, center: Tuple[float, float, float],
                   radius: float, layer: str = None, color: int = None, lineweight=None) -> Any:
        """Vẽ đường tròn"""
        if not self.is_running():
            return None

        # Bán kính phải là số dương (CAD từ chối bán kính 0 và âm), báo lỗi sớm với log rõ ràng
        if radius <= 0:
            logger.error(f"Vẽ đường tròn thất bại: bán kính ({radius}) phải là số dương")
            return None

        try:
            # Dùng VARIANT đóng gói dữ liệu điểm tọa độ (_pt tự động bổ sung trục Z)
            center_array = self._pt(center)

            # Thêm đường tròn (nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận)
            with self._undo_group():
                circle = self._call_attr(self._ms(), "AddCircle", center_array, radius)

                # Thiết lập lớp, màu sắc, độ dày nét
                self._apply_style(circle, layer, color, lineweight)

            logger.debug(f"Đã vẽ đường tròn: tâm {center}, bán kính {radius}, lớp {layer if layer else 'mặc định'}, màu {color if color is not None else 'mặc định'}")
            return circle

        except Exception as e:
            logger.error(f"Lỗi khi vẽ đường tròn: {str(e)}")
            return None

    def draw_arc(self, center: Tuple[float, float, float],
                radius: float, start_angle: float, end_angle: float, layer: str = None, color: int = None, lineweight=None) -> Any:
        """Vẽ cung tròn"""
        if not self.is_running():
            return None

        # Bán kính phải là số dương (CAD từ chối bán kính 0 và âm), báo lỗi sớm với log rõ ràng
        if radius <= 0:
            logger.error(f"Vẽ cung tròn thất bại: bán kính ({radius}) phải là số dương")
            return None

        try:
            # Chuyển đổi góc sang radian
            start_rad = math.radians(start_angle)
            end_rad = math.radians(end_angle)

            # Dùng VARIANT đóng gói dữ liệu điểm tọa độ
            center_array = self._pt(center)

            # Thêm cung tròn (nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận)
            with self._undo_group():
                arc = self._call_attr(self._ms(), "AddArc", center_array, radius, start_rad, end_rad)

                # Thiết lập lớp, màu sắc, độ dày nét
                self._apply_style(arc, layer, color, lineweight)

            logger.debug(f"Đã vẽ cung tròn: tâm {center}, bán kính {radius}, góc bắt đầu {start_angle}, góc kết thúc {end_angle}, lớp {layer if layer else 'mặc định'}, màu {color if color is not None else 'mặc định'}")
            return arc
        except Exception as e:
            logger.error(f"Vẽ cung tròn thất bại: {str(e)}")
            return None

    def draw_ellipse(self, center: Tuple[float, float, float],
                    major_axis: float, minor_axis: float, rotation: float = 0,
                    layer: str = None, color: int = None, lineweight=None) -> Any:
        """Vẽ hình elip"""
        if not self.is_running():
            return None

        # Trục lớn/trục nhỏ phải dương và trục lớn không nhỏ hơn trục nhỏ — tham số RadiusRatio của AddEllipse
        # là trục nhỏ / trục lớn, phải nằm trong (0, 1]; trục lớn bằng 0 cũng sẽ gây lỗi chia cho 0 cục bộ
        if major_axis <= 0 or minor_axis <= 0 or minor_axis > major_axis:
            logger.error(f"Vẽ hình elip thất bại: trục lớn ({major_axis}) phải dương và không nhỏ hơn trục nhỏ ({minor_axis})")
            return None

        try:
            if rotation is None:
                rotation = 0

            # Chuyển đổi góc xoay sang radian
            rotation_rad = math.radians(rotation)

            # Dùng VARIANT đóng gói dữ liệu điểm tọa độ
            center_array = self._pt(center)

            # Tính vector trục chính của hình elip
            major_x = major_axis * math.cos(rotation_rad)
            major_y = major_axis * math.sin(rotation_rad)
            major_vector = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                               [major_x, major_y, 0])

            # Thêm hình elip (nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận)
            with self._undo_group():
                ellipse = self._call_attr(self._ms(), "AddEllipse", center_array, major_vector, minor_axis / major_axis)

                # Thiết lập lớp, màu sắc, độ dày nét
                self._apply_style(ellipse, layer, color, lineweight)

            logger.debug(f"Đã vẽ hình elip: tâm {center}, trục lớn {major_axis}, trục nhỏ {minor_axis}, góc xoay {rotation}, lớp {layer if layer else 'mặc định'}, màu {color if color is not None else 'mặc định'}")
            return ellipse
        except Exception as e:
            logger.error(f"Vẽ hình elip thất bại: {str(e)}")
            return None

    def draw_polyline(self, points: List[Tuple[float, float, float]], closed: bool = False, layer: str = None, color: int = None, lineweight=None) -> Any:
        """Vẽ đường đa tuyến (polyline)"""
        if not self.is_running():
            return None

        try:
            # Đảm bảo tất cả các điểm đều là 3D
            processed_points = []
            for point in points:
                if len(point) == 2:
                    processed_points.append((point[0], point[1], 0))
                else:
                    processed_points.append(point)

            # Tạo mảng điểm
            point_array = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                                [coord for point in processed_points for coord in point])

            # Thêm đường đa tuyến (nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận)
            with self._undo_group():
                polyline = self._call_attr(self._ms(), "AddPolyline", point_array)

                # Nếu cần khép kín
                if closed and len(processed_points) > 2:
                    self._call(setattr, polyline, "Closed", True)

                # Thiết lập lớp, màu sắc, độ dày nét
                self._apply_style(polyline, layer, color, lineweight)

            logger.debug(f"Đã vẽ đường đa tuyến: {len(points)} điểm, {'khép kín' if closed else 'không khép kín'}, lớp {layer if layer else 'mặc định'}, màu {color if color is not None else 'mặc định'}")
            return polyline
        except Exception as e:
            logger.error(f"Lỗi khi vẽ đường đa tuyến: {str(e)}")
            return None

    def draw_rectangle(self, corner1: Tuple[float, float, float],
                      corner2: Tuple[float, float, float], layer: str = None, color: int = None, lineweight=None) -> Any:
        """Vẽ hình chữ nhật"""
        if not self.is_running():
            return None

        try:
            # Đảm bảo các điểm là 3D
            if len(corner1) == 2:
                corner1 = (corner1[0], corner1[1], 0)
            if len(corner2) == 2:
                corner2 = (corner2[0], corner2[1], 0)

            # Tính bốn điểm góc của hình chữ nhật
            x1, y1, z1 = corner1
            x2, y2, z2 = corner2

            # Tạo bốn điểm của hình chữ nhật
            points = [
                (x1, y1, z1),
                (x2, y1, z1),
                (x2, y2, z1),
                (x1, y2, z1),
                (x1, y1, z1)  # Khép kín hình chữ nhật
            ]

            # Dùng đường đa tuyến để vẽ hình chữ nhật
            return self.draw_polyline(points, True, layer, color, lineweight)
        except Exception as e:
            logger.error(f"Lỗi khi vẽ hình chữ nhật: {str(e)}")
            return None

    def draw_text(self, position: Tuple[float, float, float],
                 text: str, height: float = 2.5, rotation: float = 0, layer: str = None, color: int = None) -> Any:
        """Thêm văn bản"""
        if not self.is_running():
            return None

        try:
            # Dùng VARIANT đóng gói dữ liệu điểm tọa độ (_pt tự động bổ sung trục Z)
            position_array = self._pt(position)

            # Thêm văn bản (nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận)
            with self._undo_group():
                text_obj = self._call_attr(self._ms(), "AddText", text, position_array, height)

                # Thiết lập góc xoay
                if rotation != 0:
                    self._call(setattr, text_obj, "Rotation", math.radians(rotation))

                # Thiết lập lớp, màu sắc
                self._apply_style(text_obj, layer, color)

            logger.debug(f"Đã thêm văn bản: '{text}', vị trí {position}, chiều cao {height}, góc xoay {rotation} độ, lớp {layer if layer else 'mặc định'}, màu {color if color is not None else 'mặc định'}")
            return text_obj
        except Exception as e:
            logger.error(f"Lỗi khi thêm văn bản: {str(e)}")
            return None

    def draw_hatch(self, points: List[Tuple[float, float, float]],
                  pattern_name: str = "SOLID", scale: float = 1.0, layer: str = None, color: int = None) -> Any:
        """Vẽ mẫu tô vật liệu (hatch)

        Args:
            points: Tập hợp điểm tạo đường bao tô, mỗi điểm là tuple tọa độ 2D hoặc 3D
            pattern_name: Tên mẫu tô, mặc định là "SOLID" (tô đặc)
            scale: Tỷ lệ mẫu tô, mặc định là 1.0
            layer: Tên lớp, nếu là None thì dùng lớp hiện hành
            color: Chỉ số màu, nếu là None thì dùng màu mặc định

        Returns:
            Trả về đối tượng hatch nếu thành công, trả về None nếu thất bại
        """
        if not self.is_running():
            return None

        closed_polyline = None
        try:
            # Đảm bảo tất cả các điểm đều hợp lệ
            if not points or len(points) < 3:
                logger.error("Tạo mẫu tô thất bại: cần ít nhất 3 điểm để xác định đường bao tô")
                return None

            # Tạo đường đa tuyến bao (bên trong đã có nhóm hoàn tác độc lập, nhóm ngoài ở đây giúp hatch + đường bao thành một bước hoàn tác)
            with self._undo_group():
                closed_polyline = self.draw_polyline(points, closed=True, layer=layer)
                if not closed_polyline:
                    logger.error("Tạo mẫu tô thất bại: không thể tạo đường đa tuyến bao")
                    return None

                # Tạo đối tượng hatch (0 là kiểu tô thường, True là liên kết với đường bao)
                hatch = self._call_attr(self._ms(), "AddHatch", 0, pattern_name, True)

                # Thêm vòng bao ngoài
                # Dùng VARIANT đóng gói mảng đối tượng
                object_ids = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [closed_polyline])
                self._call_attr(hatch, "AppendOuterLoop", object_ids)

                # Thiết lập tỷ lệ mẫu tô
                self._call(setattr, hatch, "PatternScale", scale)

                # Thiết lập lớp, màu sắc
                self._apply_style(hatch, layer, color)

                # Cập nhật mẫu tô (tính toán vùng tô)
                self._call_attr(hatch, "Evaluate")

            logger.debug(f"Đã tạo mẫu tô: mẫu {pattern_name}, tỷ lệ {scale}, lớp {layer if layer else 'mặc định'}, màu {color if color is not None else 'mặc định'}")
            return hatch
        except Exception as e:
            # Khi thất bại thì dọn dẹp đường đa tuyến bao đã tạo để tránh để lại thực thể mồ côi trên bản vẽ
            # (bên gọi thấy là "thất bại", trên bản vẽ không nên thừa ra một đường đa tuyến khép kín)
            if closed_polyline is not None:
                try:
                    self._call_attr(closed_polyline, "Delete")
                except Exception:
                    pass
            logger.error(f"Lỗi khi tạo mẫu tô: {str(e)}")
            return None

    def zoom_extents(self) -> bool:
        """Thu phóng khung nhìn để hiển thị toàn bộ đối tượng"""
        if not self.is_running():
            return False

        try:
            # ZoomExtents là phương thức của đối tượng Application (không phải ActiveViewport)
            self._call_attr(self.app, "ZoomExtents")
            logger.info("Đã thu phóng khung nhìn để hiển thị toàn bộ đối tượng")
            return True
        except Exception as e:
            logger.error(f"Lỗi khi thu phóng khung nhìn: {str(e)}")
            return False

    def close(self) -> None:
        """Đóng bộ điều khiển CAD

        Lưu ý: COM apartment do luồng COMExecutor nắm giữ và hệ thống sẽ thu hồi khi tiến trình thoát,
        ở đây chỉ giải phóng tham chiếu, không gọi CoUninitialize xuyên luồng.
        """
        try:
            # Giải phóng tài nguyên COM
            if self.app is not None:
                del self.app
        except:
            pass

    def _ensure_layer(self, layer_name: str):
        """Đảm bảo lớp tồn tại và trả về đối tượng lớp (nếu đã tồn tại thì trả về ngay, không đổi lớp hiện hành)

        Khi chỉ định lớp lúc vẽ sẽ đi qua đây: chỉ đảm bảo thực thể rơi vào đúng lớp đích,
        không âm thầm đổi mất lớp hiện hành người dùng đang sử dụng.
        Cả tập hợp Layers lẫn việc đọc Name đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi.
        """
        layers = self._call(getattr, self.doc, "Layers")
        for i in range(self._call(getattr, layers, "Count")):
            layer = self._call_attr(layers, "Item", i)
            # Tên lớp CAD không phân biệt hoa thường, bỏ qua hoa thường khi so sánh
            # (nếu không "wall" sẽ gọi Add trùng với "WALL" đã có và bị CAD từ chối)
            if self._call(getattr, layer, "Name").casefold() == layer_name.casefold():
                return layer
        # Nếu chưa tồn tại thì tạo mới (nhóm hoàn tác độc lập)
        with self._undo_group():
            return self._call_attr(layers, "Add", layer_name)

    def create_layer(self, layer_name: str) -> bool:    # , color: Union[int, Tuple[int, int, int]] = 7
        """Tạo lớp mới và đặt làm lớp hiện hành (nếu đã tồn tại thì kích hoạt ngay)

        Args:
            layer_name: Tên lớp
            color: Giá trị màu, có thể là chỉ số màu CAD (int) hoặc giá trị màu RGB (tuple)

        Returns:
            Thao tác có thành công hay không
        """
        if not self.is_running():
            return False

        try:
            # Đảm bảo lớp tồn tại (có sẵn thì tái sử dụng, chưa có thì tạo mới)
            new_layer = self._ensure_layer(layer_name)

            # Không đặt màu cho lớp, đặt màu cho từng thực thể bên trong
            # # Thiết lập màu
            # if isinstance(color, int):
            #     # Dùng chỉ số màu
            #     new_layer.Color = color
            # elif isinstance(color, tuple) and len(color) == 3:
            #     # Dùng giá trị RGB
            #     r, g, b = color
            #     # Thiết lập TrueColor
            #     new_layer.TrueColor = self._create_true_color(r, g, b)

            # Đặt làm lớp hiện hành
            self._call(setattr, self.doc, "ActiveLayer", new_layer)
            logger.info(f"Đã tạo và đặt làm lớp hiện hành: {layer_name}")  #, màu: {color}
            return True
        except Exception as e:
            logger.error(f"Lỗi khi tạo lớp: {str(e)}")
            return False

    def add_dimension(self, start_point: Tuple[float, float, float],
                     end_point: Tuple[float, float, float],
                     text_position: Tuple[float, float, float] = None, textheight: float = 5, layer: str = None, color: int=None) -> Any:
            """Thêm ghi kích thước tuyến tính"""
            if not self.is_running():
                return None

            try:
                # Nếu không cung cấp vị trí chữ, tự động tính toán
                if text_position is None:
                    # Phía trên trung điểm giữa điểm đầu và điểm cuối
                    mid_x = (start_point[0] + end_point[0]) / 2
                    mid_y = (start_point[1] + end_point[1]) / 2
                    text_position = (mid_x, mid_y + 5, 0)

                # Dùng VARIANT đóng gói dữ liệu điểm tọa độ (_pt tự động bổ sung trục Z)
                start_array = self._pt(start_point)
                end_array = self._pt(end_point)
                text_pos_array = self._pt(text_position)

                # Thêm ghi kích thước gióng hàng (nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận)
                with self._undo_group():
                    dimension = self._call_attr(self._ms(), "AddDimAligned", start_array, end_array, text_pos_array)

                    # Thiết lập chiều cao chữ
                    if textheight is not None:
                        self._call(setattr, dimension, "TextHeight", textheight)

                    # Thiết lập lớp, màu sắc
                    self._apply_style(dimension, layer, color)

                logger.info(f"Đã thêm ghi kích thước: từ {start_point} đến {end_point}, lớp {layer if layer else 'mặc định'}")
                return dimension
            except Exception as e:
                logger.error(f"Lỗi khi thêm ghi kích thước: {str(e)}")
                return None

    # ==================== Thao tác truy vấn (đọc trạng thái tài liệu CAD theo thời gian thực) ====================

    @staticmethod
    def _friendly_type(object_name: str) -> str:
        """Chuyển tên loại đối tượng COM (như AcDbLine) sang tên loại thân thiện (như line)"""
        mapping = {
            "AcDbLine": "line",
            "AcDbCircle": "circle",
            "AcDbArc": "arc",
            "AcDbEllipse": "ellipse",
            "AcDbPolyline": "polyline",       # Đường đa tuyến nhẹ (LWPolyline)
            "AcDb2dPolyline": "polyline",
            "AcDb3dPolyline": "polyline",
            "AcDbText": "text",
            "AcDbMText": "mtext",
            "AcDbHatch": "hatch",
            "AcDbBlockReference": "block_reference",
            "AcDbDimension": "dimension",
            "AcDbAlignedDimension": "dimension",
            "AcDbRotatedDimension": "dimension",
        }
        return mapping.get(object_name, object_name)

    def get_entity_info(self, entity) -> Dict[str, Any]:
        """Lấy thông tin tóm tắt của thực thể (handle/loại/lớp/màu sắc)"""
        return {
            "handle": str(self._safe_attr(entity, "Handle", "")),
            "entity_type": self._friendly_type(str(self._safe_attr(entity, "ObjectName", ""))),
            "layer": self._safe_attr(entity, "Layer"),
            "color": self._read_color(entity),
        }

    def list_layers(self) -> List[Dict[str, Any]]:
        """Liệt kê thông tin tất cả các lớp trong bản vẽ hiện tại"""
        if not self.is_running():
            return []

        layers = []
        # Cả tập hợp lẫn việc đọc thuộc tính đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi
        active_name = self._call(getattr, self._call(getattr, self.doc, "ActiveLayer"), "Name")
        layer_set = self._call(getattr, self.doc, "Layers")
        for i in range(self._call(getattr, layer_set, "Count")):
            layer = self._call_attr(layer_set, "Item", i)
            name = self._call(getattr, layer, "Name")
            layers.append({
                "name": name,
                "color": self._read_color(layer),
                "is_current": name == active_name,
                "is_frozen": bool(self._safe_attr(layer, "Freeze", False)),
                "is_locked": bool(self._safe_attr(layer, "Lock", False)),
                "is_on": bool(self._safe_attr(layer, "LayerOn", True)),
            })
        return layers

    def list_entities(self, entity_type: str = None, limit: int = 50) -> Dict[str, Any]:
        """Liệt kê các thực thể trong không gian mô hình (truy vấn thời gian thực, đồng bộ với tài liệu CAD)

        Args:
            entity_type: Lọc theo loại (line/circle/arc, v.v., không phân biệt hoa thường), None là không lọc
            limit: Số lượng thực thể tối đa trả về (tránh trả về quá nhiều dữ liệu một lúc với bản vẽ lớn)

        Returns:
            {"total": tổng số thực thể trong không gian mô hình, "entities": [thông tin thực thể]}
        """
        if not self.is_running():
            return {"total": 0, "entities": []}

        ms = self._ms()
        # Cả truy cập Count lẫn Item đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi
        total = self._call(getattr, ms, "Count")
        entities = []
        for i in range(total):
            info = self.get_entity_info(self._call_attr(ms, "Item", i))
            # Khớp chính xác tên loại (khớp chuỗi con sẽ khiến "line" kéo theo cả "polyline")
            if entity_type and info["entity_type"].lower() != entity_type.lower():
                continue
            entities.append(info)
            if len(entities) >= limit:
                break
        return {"total": total, "entities": entities}

    def get_entity_properties(self, handle: str) -> Dict[str, Any]:
        """Lấy thuộc tính chi tiết của thực thể theo handle (thông tin hình học tùy thuộc loại thực thể)"""
        if not self.is_running():
            raise RuntimeError("CAD chưa chạy")

        entity = self._get_entity_by_handle(handle)
        object_name = str(self._safe_attr(entity, "ObjectName", ""))
        props: Dict[str, Any] = {
            "handle": str(handle),
            "entity_type": self._friendly_type(object_name),
            "layer": self._safe_attr(entity, "Layer"),
            "color": self._read_color(entity),
            "linetype": self._safe_attr(entity, "Linetype"),
            "lineweight": self._safe_attr(entity, "Lineweight"),
        }

        # Bổ sung thuộc tính hình học theo từng loại thực thể
        detail: Dict[str, Any] = {}
        if object_name == "AcDbLine":
            detail["start_point"] = list(self._safe_attr(entity, "StartPoint") or [])
            detail["end_point"] = list(self._safe_attr(entity, "EndPoint") or [])
            detail["length"] = self._safe_attr(entity, "Length")
        elif object_name == "AcDbCircle":
            detail["center"] = list(self._safe_attr(entity, "Center") or [])
            detail["radius"] = self._safe_attr(entity, "Radius")
            detail["area"] = self._safe_attr(entity, "Area")
        elif object_name == "AcDbArc":
            detail["center"] = list(self._safe_attr(entity, "Center") or [])
            detail["radius"] = self._safe_attr(entity, "Radius")
            # StartAngle/EndAngle tính bằng radian, đổi sang độ cho dễ hiểu
            start_rad = self._safe_attr(entity, "StartAngle")
            end_rad = self._safe_attr(entity, "EndAngle")
            detail["start_angle"] = math.degrees(start_rad) if start_rad is not None else None
            detail["end_angle"] = math.degrees(end_rad) if end_rad is not None else None
        elif object_name == "AcDbEllipse":
            detail["center"] = list(self._safe_attr(entity, "Center") or [])
            detail["radius_ratio"] = self._safe_attr(entity, "RadiusRatio")
            detail["start_param"] = self._safe_attr(entity, "StartParam")
            detail["end_param"] = self._safe_attr(entity, "EndParam")
        elif object_name == "AcDbText":
            detail["text"] = self._safe_attr(entity, "TextString")
            detail["insertion_point"] = list(self._safe_attr(entity, "InsertionPoint") or [])
            detail["height"] = self._safe_attr(entity, "Height")
            rotation_rad = self._safe_attr(entity, "Rotation")
            detail["rotation"] = math.degrees(rotation_rad) if rotation_rad is not None else None
        elif object_name == "AcDbMText":
            detail["text"] = self._safe_attr(entity, "TextString")
            detail["insertion_point"] = list(self._safe_attr(entity, "InsertionPoint") or [])
        elif object_name in ("AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"):
            # Coordinates là mảng tọa độ phẳng, tách theo số thành phần mỗi điểm tùy loại:
            # đường đa tuyến nhẹ (AcDbPolyline) có 2 thành phần [x,y,...],
            # đường đa tuyến 2D/3D kiểu cũ (AcDb2dPolyline/AcDb3dPolyline) có 3 thành phần [x,y,z,...],
            # nếu tách đều theo 2 sẽ biến z thành x của điểm kế tiếp khiến toàn bộ tọa độ bị lệch
            coords = self._safe_attr(entity, "Coordinates") or []
            step = 2 if object_name == "AcDbPolyline" else 3
            detail["coordinates"] = [list(coords[i:i + step]) for i in range(0, len(coords), step)]
            detail["closed"] = bool(self._safe_attr(entity, "Closed", False))
            detail["length"] = self._safe_attr(entity, "Length")
        elif object_name == "AcDbHatch":
            detail["pattern_name"] = self._safe_attr(entity, "PatternName")
            detail["area"] = self._safe_attr(entity, "Area")
        elif object_name == "AcDbBlockReference":
            detail["name"] = self._safe_attr(entity, "Name")
            detail["insertion_point"] = list(self._safe_attr(entity, "InsertionPoint") or [])
            detail["x_scale"] = self._safe_attr(entity, "XScaleFactor")
            detail["y_scale"] = self._safe_attr(entity, "YScaleFactor")
            rotation_rad = self._safe_attr(entity, "Rotation")
            detail["rotation"] = math.degrees(rotation_rad) if rotation_rad is not None else None

        props["properties"] = detail
        return props

    def get_drawing_stats(self) -> Dict[str, Any]:
        """Lấy thông tin thống kê thời gian thực của tài liệu hiện tại (số thực thể/số lớp/tên tài liệu)"""
        if not self.is_running():
            return {"running": False}
        return {
            "running": True,
            # Mỗi thao tác đọc thuộc tính đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi
            "entity_count": self._call(getattr, self._ms(), "Count"),
            "layer_count": self._call(getattr, self._call(getattr, self.doc, "Layers"), "Count"),
            "document_name": self._call(getattr, self.doc, "Name"),
        }

    def screenshot(self) -> bytes:
        """Chụp ảnh màn hình cửa sổ chính CAD, trả về luồng byte định dạng PNG"""
        if not self.is_running():
            raise RuntimeError("CAD chưa chạy")

        import win32gui
        import win32con
        from PIL import ImageGrab

        # Bắt buộc tái tạo toàn bộ khung nhìn trước khi chụp: GstarCAD sau khi vẽ hàng loạt qua COM không tự làm mới canvas,
        # nếu không Regen sẽ chụp phải khung hình cũ trước khi vẽ (thực thể đã lưu vào cơ sở dữ liệu, chỉ chậm hiển thị)
        try:
            self._call_attr(self.doc, "Regen", 1)  # 1 = acAllViewports
        except Exception as e:
            logger.warning(f"Tái tạo (Regen) trước khi chụp thất bại (vẫn sẽ thử chụp): {str(e)}")

        # Handle cửa sổ chính của CAD (đọc thuộc tính có thể bị CAD đang bận từ chối, cần có thử lại)
        hwnd = self._call(getattr, self.app, "HWND")
        # Khi cửa sổ bị thu nhỏ, GetWindowRect trả về tọa độ không hợp lệ (quanh -32000),
        # chụp thẳng sẽ ra ảnh rác, nên khôi phục cửa sổ trước
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)
        # Ưu tiên dùng PrintWindow để CAD tự vẽ cửa sổ của nó vào bitmap: không phụ thuộc foreground,
        # bị cửa sổ khác che khuất vẫn chụp được. Windows sẽ từ chối SetForegroundWindow của tiến trình nền
        # mà không báo lỗi, khiến chụp màn hình bị chụp nhầm cửa sổ đang che phía trước
        img = None
        try:
            img = _print_window(hwnd)
        except Exception as e:
            logger.warning(f"Chụp bằng PrintWindow thất bại, chuyển sang chụp màn hình: {str(e)}")

        if img is None:
            # Phương án dự phòng: cố gắng đưa cửa sổ CAD lên foreground rồi chụp vùng màn hình
            try:
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"Không thể đưa cửa sổ CAD lên trên cùng (vẫn sẽ thử chụp): {str(e)}")
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            # all_screens=True để hỗ trợ đa màn hình / tọa độ âm
            img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # ==================== Thao tác chỉnh sửa ====================

    def _get_entity_by_handle(self, handle: str):
        """Tìm đối tượng thực thể theo handle (tra cứu là thao tác chỉ đọc, thử lại cấp lời gọi an toàn)"""
        entity = self._call_attr(self.doc, "HandleToObject", str(handle))
        if entity is None:
            raise ValueError(f"Không tìm thấy thực thể có handle là {handle}")
        return entity

    def erase_entity(self, handle: str) -> bool:
        """Xóa thực thể chỉ định"""
        if not self.is_running():
            return False
        try:
            # Nhóm hoàn tác độc lập: UNDO 1 hoàn tác đúng lần xóa này
            with self._undo_group():
                self._call_attr(self._get_entity_by_handle(handle), "Delete")
            return True
        except Exception as e:
            logger.error(f"Xóa thực thể thất bại: {str(e)}")
            return False

    def move_entity(self, handle: str, displacement: List[float]) -> bool:
        """Di chuyển thực thể (theo vector dịch chuyển [dx, dy, dz])"""
        if not self.is_running():
            return False
        try:
            entity = self._get_entity_by_handle(handle)
            # Move(điểm gốc, điểm thứ hai) thực hiện tịnh tiến: lấy gốc tọa độ làm điểm gốc, điểm thứ hai chính là vector dịch chuyển
            with self._undo_group():
                self._call_attr(entity, "Move", self._pt((0, 0, 0)), self._pt(displacement))
            return True
        except Exception as e:
            logger.error(f"Di chuyển thực thể thất bại: {str(e)}")
            return False

    def rotate_entity(self, handle: str, base_point: List[float], angle_degrees: float) -> bool:
        """Xoay thực thể quanh điểm gốc (đơn vị góc: độ, ngược chiều kim đồng hồ là dương)"""
        if not self.is_running():
            return False
        try:
            entity = self._get_entity_by_handle(handle)
            with self._undo_group():
                self._call_attr(entity, "Rotate", self._pt(base_point), math.radians(angle_degrees))
            return True
        except Exception as e:
            logger.error(f"Xoay thực thể thất bại: {str(e)}")
            return False

    def scale_entity(self, handle: str, base_point: List[float], scale_factor: float) -> bool:
        """Thu phóng thực thể quanh điểm gốc"""
        if not self.is_running():
            return False
        try:
            entity = self._get_entity_by_handle(handle)
            with self._undo_group():
                self._call_attr(entity, "ScaleEntity", self._pt(base_point), scale_factor)
            return True
        except Exception as e:
            logger.error(f"Thu phóng thực thể thất bại: {str(e)}")
            return False

    def copy_entity(self, handle: str, displacement: List[float]):
        """Sao chép thực thể và tịnh tiến theo độ dịch chuyển chỉ định, trả về thực thể mới"""
        if not self.is_running():
            return None
        try:
            entity = self._get_entity_by_handle(handle)
            # Gói sao chép + tịnh tiến thành một nhóm hoàn tác (Copy và Move thử lại riêng, đều lũy đẳng an toàn)
            with self._undo_group():
                copy = self._call_attr(entity, "Copy")
                if copy is not None and displacement:
                    self._call_attr(copy, "Move", self._pt((0, 0, 0)), self._pt(displacement))
            return copy
        except Exception as e:
            logger.error(f"Sao chép thực thể thất bại: {str(e)}")
            return None

    def mirror_entity(self, handle: str, point1: List[float], point2: List[float]):
        """Lấy đối xứng thực thể qua trục xác định bởi hai điểm, trả về thực thể mới tạo ra (giữ nguyên thực thể gốc)"""
        if not self.is_running():
            return None
        try:
            entity = self._get_entity_by_handle(handle)
            with self._undo_group():
                mirrored = self._call_attr(entity, "Mirror", self._pt(point1), self._pt(point2))
            return mirrored
        except Exception as e:
            logger.error(f"Lấy đối xứng thực thể thất bại: {str(e)}")
            return None

    def offset_entity(self, handle: str, distance: float) -> List[Any]:
        """Tạo đường song song (offset) cho thực thể (giá trị âm/dương quyết định hướng offset), trả về danh sách thực thể mới"""
        if not self.is_running():
            return []
        try:
            entity = self._get_entity_by_handle(handle)
            with self._undo_group():
                # Offset trả về mảng thực thể mới (có thể rỗng)
                result = self._call_attr(entity, "Offset", distance)
            return list(result) if result else []
        except Exception as e:
            logger.error(f"Offset thực thể thất bại: {str(e)}")
            return []

    def array_linear_entity(self, handle: str, count: int, displacement: List[float]) -> List[Any]:
        """Mảng tuyến tính: sao chép count-1 bản theo hướng vector dịch chuyển (tổng cộng count bản gồm cả thực thể gốc)"""
        if not self.is_running():
            return []
        try:
            src = self._get_entity_by_handle(handle)
            dx, dy = float(displacement[0]), float(displacement[1])
            dz = float(displacement[2]) if len(displacement) > 2 else 0.0
            copies = []
            # Gói toàn bộ mảng thành một nhóm hoàn tác; thử lại từng lời gọi trong vòng lặp để tránh lặp lại phần đã xong
            with self._undo_group():
                for i in range(1, count):
                    copy = self._call_attr(src, "Copy")
                    self._call_attr(copy, "Move", self._pt((0, 0, 0)), self._pt((dx * i, dy * i, dz * i)))
                    copies.append(copy)
            return copies
        except Exception as e:
            logger.error(f"Tạo mảng tuyến tính thất bại: {str(e)}")
            return []

    def array_polar_entity(self, handle: str, count: int, center: List[float], total_angle_degrees: float = 360.0) -> List[Any]:
        """Mảng tròn: sao chép count-1 bản xoay quanh tâm trong phạm vi góc total_angle"""
        if not self.is_running():
            return []
        try:
            src = self._get_entity_by_handle(handle)
            step = math.radians(total_angle_degrees / count)
            center_pt = self._pt(center)
            copies = []
            # Gói toàn bộ mảng thành một nhóm hoàn tác; thử lại từng lời gọi trong vòng lặp để tránh lặp lại phần đã xong
            with self._undo_group():
                for i in range(1, count):
                    copy = self._call_attr(src, "Copy")
                    self._call_attr(copy, "Rotate", center_pt, step * i)
                    copies.append(copy)
            return copies
        except Exception as e:
            logger.error(f"Tạo mảng tròn thất bại: {str(e)}")
            return []

    def undo(self) -> bool:
        """Hoàn tác thao tác trước đó"""
        if not self.is_running():
            return False
        try:
            # Lưu ý: thực đo trên AutoCAD tổ hợp "_.UNDO 1" có tiền tố + tham số số không có tác dụng,
            # tên lệnh tiếng Anh thuần "UNDO 1" đáng tin cậy (các bản CAD bản địa hóa vẫn hỗ trợ tên lệnh tiếng Anh)
            self._call_attr(self.doc, "SendCommand", "UNDO 1\n")
            # SendCommand là bất đồng bộ: chờ lệnh xử lý xong mới trả về để các lời gọi sau thấy được kết quả hoàn tác
            time.sleep(self.command_delay)
            return True
        except Exception as e:
            logger.error(f"Hoàn tác thất bại: {str(e)}")
            return False

    def redo(self) -> bool:
        """Làm lại thao tác vừa hoàn tác"""
        if not self.is_running():
            return False
        try:
            self._call_attr(self.doc, "SendCommand", "_.REDO\n")
            # SendCommand là bất đồng bộ: chờ lệnh xử lý xong mới trả về
            time.sleep(self.command_delay)
            return True
        except Exception as e:
            logger.error(f"Làm lại thất bại: {str(e)}")
            return False

    # ==================== Thao tác lớp (Layer) ====================

    def set_current_layer(self, layer_name: str) -> bool:
        """Chuyển lớp hiện hành (lớp phải đã tồn tại)"""
        if not self.is_running():
            return False
        try:
            # Cả tập hợp Layers lẫn việc đọc Name đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi
            layers = self._call(getattr, self.doc, "Layers")
            for i in range(self._call(getattr, layers, "Count")):
                layer = self._call_attr(layers, "Item", i)
                # Tên lớp CAD không phân biệt hoa thường, bỏ qua hoa thường khi so sánh
                if self._call(getattr, layer, "Name").casefold() == layer_name.casefold():
                    self._call(setattr, self.doc, "ActiveLayer", layer)
                    logger.info(f"Đã chuyển lớp hiện hành sang: {layer_name}")
                    return True
            logger.warning(f"Lớp {layer_name} không tồn tại")
            return False
        except Exception as e:
            logger.error(f"Chuyển lớp thất bại: {str(e)}")
            return False

    # ==================== Quản lý bản vẽ ====================

    def new_drawing(self) -> bool:
        """Tạo tài liệu bản vẽ mới"""
        if self.app is None:
            return False
        try:
            # Cả việc lấy tập hợp Documents lẫn lời gọi Add đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi
            docs = self._call(getattr, self.app, "Documents")
            self.doc = self._call_attr(docs, "Add")
            # Đọc Name có thể bị CAD mới tạo tài liệu tạm thời từ chối, cần có thử lại (tránh log hiện nhầm None)
            logger.info(f"Đã tạo tài liệu mới: {self._call(getattr, self.doc, 'Name')}")
            return self.doc is not None
        except Exception as e:
            logger.error(f"Tạo tài liệu mới thất bại: {str(e)}")
            return False

    def open_drawing(self, file_path: str) -> bool:
        """Mở tệp bản vẽ đã có"""
        if self.app is None:
            return False
        # Đường dẫn tương đối phải chuyển sang tuyệt đối trước: Documents.Open phân giải đường dẫn trong tiến trình CAD,
        # mà thư mục làm việc của CAD thường khác thư mục làm việc của máy chủ MCP (tương tự save_drawing)
        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Tệp không tồn tại: {file_path}")
        try:
            # Sau khi mở CAD tạm thời bận, thử lại cấp lời gọi đảm bảo bản thân Open thành công
            docs = self._call(getattr, self.app, "Documents")
            opened = self._call_attr(docs, "Open", file_path)
            # Một số phần mềm CAD không trả về đối tượng tài liệu từ Documents.Open, quay về lấy ActiveDocument
            self.doc = opened if opened is not None else self._call(getattr, self.app, "ActiveDocument")
            logger.info(f"Đã mở tài liệu: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Mở tài liệu thất bại: {str(e)}")
            return False

    def close_drawing(self, save: bool = False) -> bool:
        """Đóng tài liệu hiện tại (khi save=False sẽ đóng thẳng không lưu)"""
        if not self.is_running():
            return False
        try:
            # Trong lúc đóng CAD bận sẽ từ chối lời gọi, thử lại cấp lời gọi đảm bảo bản thân Close thành công
            self._call_attr(self.doc, "Close", save)
            # Thử chuyển sang tài liệu đang mở khác
            try:
                self.doc = self._call(getattr, self.app, "ActiveDocument")
            except Exception:
                self.doc = None
            logger.info(f"Đã đóng tài liệu hiện tại (lưu: {save})")
            return True
        except Exception as e:
            logger.error(f"Đóng tài liệu thất bại: {str(e)}")
            return False

    # ==================== Thao tác khối (Block) ====================

    def create_block(self, name: str, base_point: List[float] = None):
        """Tạo định nghĩa khối (nếu khối cùng tên đã tồn tại thì trả về ngay khối đó)

        Lưu ý: Các thực thể bên trong khối có thể chỉnh sửa thông qua send_command thực thi lệnh như BEDIT.
        """
        if not self.is_running():
            return None
        try:
            # Kiểm tra xem khối cùng tên đã tồn tại chưa (tập hợp Blocks và đọc Name đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi)
            blocks = self._call(getattr, self.doc, "Blocks")
            for i in range(self._call(getattr, blocks, "Count")):
                block = self._call_attr(blocks, "Item", i)
                if self._call(getattr, block, "Name") == name:
                    return block
            # Nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận
            with self._undo_group():
                return self._call_attr(blocks, "Add", self._pt(base_point or (0, 0, 0)), name)
        except Exception as e:
            logger.error(f"Tạo khối thất bại: {str(e)}")
            return None

    def insert_block(self, name: str, position: List[float],
                     x_scale: float = 1.0, y_scale: float = 1.0, z_scale: float = 1.0,
                     rotation_degrees: float = 0.0):
        """Chèn tham chiếu khối vào không gian mô hình"""
        if not self.is_running():
            return None
        try:
            # Nhóm hoàn tác độc lập, tự động thử lại cấp lời gọi khi bận
            with self._undo_group():
                block_ref = self._call_attr(
                    self._ms(), "InsertBlock",
                    self._pt(position), name, x_scale, y_scale, z_scale,
                    math.radians(rotation_degrees)
                )
            return block_ref
        except Exception as e:
            logger.error(f"Chèn khối thất bại: {str(e)}")
            return None

    def list_blocks(self) -> List[Dict[str, Any]]:
        """Liệt kê các định nghĩa khối của người dùng trong bản vẽ hiện tại"""
        if not self.is_running():
            return []
        blocks = []
        try:
            # Cả tập hợp Blocks lẫn đọc Name đều có thể bị CAD đang bận từ chối, thử lại từng lời gọi
            block_set = self._call(getattr, self.doc, "Blocks")
            for i in range(self._call(getattr, block_set, "Count")):
                block = self._call_attr(block_set, "Item", i)
                name = str(self._call(getattr, block, "Name"))
                # Bỏ qua các khối giữ chỗ bố cục (khối ẩn danh như *Model_Space, *Paper_Space)
                if not name.startswith("*"):
                    blocks.append({
                        "name": name,
                        "entity_count": self._safe_attr(block, "Count", 0),
                    })
        except Exception as e:
            logger.error(f"Liệt kê khối thất bại: {str(e)}")
        return blocks

    # ==================== Truyền lệnh trực tiếp ====================

    def send_command(self, command: str) -> bool:
        """Gửi trực tiếp lệnh bất kỳ tới dòng lệnh CAD (ví dụ "_.ZOOM E")

        Đây là lối thoát chức năng: mọi lệnh CAD chưa được đóng gói thành công cụ đều có thể thực thi qua đây.
        """
        if not self.is_running():
            return False
        try:
            # Nếu lệnh chưa kết thúc bằng dấu xuống dòng/khoảng trắng thì bổ sung một ký tự xuống dòng để đảm bảo lệnh được gửi đi
            if not command.endswith(("\n", "\r", " ")):
                command += "\n"
            # Bản thân SendCommand có thể bị CAD đang bận từ chối, thử lại cấp lời gọi đảm bảo lệnh vào hàng đợi
            self._call_attr(self.doc, "SendCommand", command)
            # SendCommand là bất đồng bộ: chờ lệnh thực thi (ví dụ khi lệnh ZOOM/lưu đang chạy,
            # thao tác SaveAs tiếp theo sẽ báo lỗi), thời lượng do cấu hình command_delay điều khiển
            time.sleep(self.command_delay)
            return True
        except Exception as e:
            logger.error(f"Thực thi lệnh thất bại: {str(e)}")
            return False
