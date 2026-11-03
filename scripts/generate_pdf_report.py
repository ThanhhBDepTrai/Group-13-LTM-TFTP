"""Tạo báo cáo PDF tổng hợp TFTP Pro (font Unicode, bảng tự động xuống dòng)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
OUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "summary_report.pdf"

NAVY = (15, 42, 74)
BLUE = (26, 115, 232)
GREEN = (46, 125, 50)
TEXT = (45, 45, 45)
MUTED = (110, 118, 130)
LINE = (208, 216, 228)
ZEBRA = (244, 248, 253)
SOFT_BLUE = (234, 242, 255)

MARGIN = 15
PAGE_W = 210
CONTENT_W = PAGE_W - 2 * MARGIN  # 180 mm


class Report(FPDF):
    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=True, margin=18)
        self.alias_nb_pages()
        self._register_fonts()
        self.set_font("Segoe", "", 9.5)
        self.set_text_color(*TEXT)

    def _register_fonts(self) -> None:
        self.add_font("Segoe", "", str(FONT_DIR / "segoeui.ttf"))
        self.add_font("Segoe", "B", str(FONT_DIR / "segoeuib.ttf"))
        self.add_font("Segoe", "I", str(FONT_DIR / "segoeuii.ttf"))
        self.add_font("Consolas", "", str(FONT_DIR / "consola.ttf"))

    def footer(self) -> None:
        if self.page_no() == 1:
            return
        self.set_y(-13)
        self.set_draw_color(*LINE)
        self.set_line_width(0.2)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.set_y(-11)
        self.set_font("Segoe", "", 8)
        self.set_text_color(*MUTED)
        self.cell(
            0,
            6,
            f"TFTP Pro 1.1.0  ·  Báo cáo tổng hợp  ·  Trang {self.page_no()}/{{nb}}",
            align="C",
        )


def section(pdf: Report, title: str) -> None:
    if pdf.get_y() > pdf.h - 55:
        pdf.add_page()
    pdf.ln(2)
    pdf.set_font("Segoe", "B", 13.5)
    pdf.set_text_color(*BLUE)
    pdf.cell(CONTENT_W, 9, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    y = pdf.get_y() + 0.6
    pdf.set_draw_color(*BLUE)
    pdf.set_line_width(0.5)
    pdf.line(MARGIN, y, PAGE_W - MARGIN, y)
    pdf.set_line_width(0.2)
    pdf.set_y(y + 3.2)
    pdf.set_text_color(*TEXT)


def sub(pdf: Report, title: str, keep: float = 16.0) -> None:
    if pdf.get_y() + 7 + keep > pdf.h - 20:
        pdf.add_page()
    pdf.ln(1.5)
    pdf.set_font("Segoe", "B", 10.8)
    pdf.set_text_color(*NAVY)
    pdf.cell(CONTENT_W, 7, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*TEXT)
    pdf.ln(0.6)


def para(pdf: Report, text: str, size: float = 9.5, gap: float = 1.2) -> None:
    pdf.set_font("Segoe", "", size)
    pdf.multi_cell(CONTENT_W, 5.2, text)
    pdf.ln(gap)


def bullet(pdf: Report, text: str, size: float = 9.4) -> None:
    indent = 4.5
    pdf.set_font("Segoe", "", size)
    old_margin = pdf.l_margin
    pdf.set_left_margin(MARGIN + indent)
    pdf.set_x(MARGIN + indent)
    pdf.multi_cell(pdf.w - pdf.r_margin - (MARGIN + indent), 5.1, "•  " + text)
    pdf.set_left_margin(old_margin)
    pdf.set_x(MARGIN)


def info_box(pdf: Report, title: str, lines: list[str]) -> None:
    pad = 3.6
    body_lines = len(lines) * 5.2 + 2
    height = body_lines + 8.2
    if pdf.get_y() + height > pdf.h - 22:
        pdf.add_page()
    x, y = MARGIN, pdf.get_y()
    pdf.set_fill_color(*SOFT_BLUE)
    pdf.rect(x, y, CONTENT_W, height, style="F")
    pdf.set_fill_color(*BLUE)
    pdf.rect(x, y, 1.8, height, style="F")
    pdf.set_xy(x + pad + 1.2, y + pad - 0.6)
    pdf.set_font("Segoe", "B", 10)
    pdf.set_text_color(*NAVY)
    pdf.cell(CONTENT_W - 2 * pad, 5.2, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Segoe", "", 9.3)
    pdf.set_text_color(*TEXT)
    pdf.set_x(x + pad + 1.2)
    for line in lines:
        pdf.multi_cell(CONTENT_W - 2 * pad - 1.2, 5.2, line)
        pdf.set_x(x + pad + 1.2)
    pdf.set_y(y + height + 3.5)


def code_block(pdf: Report, lines: list[str], size: float = 8.4) -> None:
    line_h = 4.7
    avail = CONTENT_W - 6
    pdf.set_font("Consolas", "", size)
    widest = max(pdf.get_string_width(line) for line in lines)
    if widest > avail:
        size = max(6.4, size * avail / widest)
    height = len(lines) * line_h + 4
    if pdf.get_y() + height > pdf.h - 22:
        pdf.add_page()
    x, y = MARGIN, pdf.get_y()
    pdf.set_fill_color(243, 246, 251)
    pdf.set_draw_color(*LINE)
    pdf.rect(x, y, CONTENT_W, height, style="DF")
    pdf.set_font("Consolas", "", size)
    pdf.set_text_color(32, 38, 48)
    pdf.set_xy(x + 3, y + 2)
    for line in lines:
        pdf.cell(CONTENT_W - 6, line_h, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(x + 3)
    pdf.set_text_color(*TEXT)
    pdf.set_y(y + height + 2.5)


def table(
    pdf: Report,
    headers: list[str],
    rows: list[list[str]],
    widths: list[int],
    aligns: list[str] | None = None,
    size: float = 8.5,
    total: bool = False,
) -> None:
    """Vẽ bảng có tự động xuống dòng, không tràn cột, lặp header khi sang trang."""
    pad_x, pad_y, line_h = 2.4, 1.5, 4.35
    aligns = aligns or ["L"] * len(widths)
    assert abs(sum(widths) - CONTENT_W) < 0.6, f"Tổng độ rộng cột = {sum(widths)}"

    def lines_of(row: list[str]) -> list[list[str]]:
        pdf.set_font("Segoe", "", size - 0.15)
        out: list[list[str]] = []
        for text, width in zip(row, widths):
            rendered = pdf.multi_cell(
                width - 2 * pad_x, line_h, str(text), dry_run=True, output="LINES"
            )
            out.append(list(rendered) or [""])
        return out

    def draw_header() -> None:
        height = 7.6
        pdf.set_font("Segoe", "B", size)
        pdf.set_text_color(255, 255, 255)
        pdf.set_fill_color(*BLUE)
        pdf.set_draw_color(*BLUE)
        x, y = MARGIN, pdf.get_y()
        for i, width in enumerate(widths):
            pdf.rect(x, y, width, height, style="DF")
            pdf.set_xy(x, y)
            pdf.cell(width, height, headers[i], align="C")
            x += width
        pdf.set_y(y + height)
        pdf.set_text_color(*TEXT)

    def draw_row(row: list[str], *, bold: bool = False, fill: tuple | None = None) -> None:
        cell_lines = lines_of(row)
        height = max(len(part) for part in cell_lines) * line_h + 2 * pad_y
        if pdf.get_y() + height > pdf.h - 20:
            pdf.add_page()
            draw_header()
        x, y = MARGIN, pdf.get_y()
        pdf.set_draw_color(*LINE)
        for i, width in enumerate(widths):
            if fill:
                pdf.set_fill_color(*fill)
                pdf.rect(x, y, width, height, style="DF")
            else:
                pdf.rect(x, y, width, height, style="D")
            pdf.set_font("Segoe", "B" if bold else "", size - 0.15)
            pdf.set_text_color(*TEXT)
            pdf.set_xy(x + pad_x, y + pad_y)
            pdf.multi_cell(
                width - 2 * pad_x, line_h, str(row[i]), align=aligns[i]
            )
            x += width
        pdf.set_y(y + height)

    if pdf.get_y() + 16 > pdf.h - 20:
        pdf.add_page()
    draw_header()
    for index, row in enumerate(rows):
        fill = ZEBRA if index % 2 == 1 else None
        if total and index == len(rows) - 1:
            fill = (232, 245, 233)
        draw_row(row, bold=total and index == len(rows) - 1, fill=fill)
    pdf.ln(3.5)


def build() -> Path:
    pdf = Report()
    pdf.add_page()

    # ---------------- Trang bìa / tiêu đề ----------------
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, PAGE_W, 42, style="F")
    pdf.set_fill_color(*BLUE)
    pdf.rect(0, 42, PAGE_W, 2.2, style="F")

    pdf.set_xy(MARGIN, 13)
    pdf.set_font("Segoe", "B", 21)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(CONTENT_W, 11, "TFTP PRO", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(MARGIN)
    pdf.set_font("Segoe", "", 11.5)
    pdf.cell(
        CONTENT_W,
        7,
        "Báo cáo tổng hợp: chức năng, giao diện và kết quả kiểm thử sau khi hợp nhất",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_x(MARGIN)
    pdf.set_font("Segoe", "", 8.8)
    pdf.set_text_color(200, 215, 235)
    pdf.cell(CONTENT_W, 6, "Phiên bản 1.1.0  ·  Ngày 04/11/2026  ·  Nhóm 13", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_y(50)
    pdf.set_text_color(*TEXT)
    pdf.set_font("Segoe", "", 9.2)
    pdf.multi_cell(
        CONTENT_W,
        5.2,
        "Tài liệu tóm tắt toàn bộ thay đổi của dự án TFTP Pro sau khi hợp nhất ba nhánh "
        "tftp_engine_upgrade, backend-developer và efm-enterprise vào nhánh main.",
    )
    pdf.ln(2)

    info_box(
        pdf,
        "Thông tin nhanh",
        [
            "Kho mã nguồn: github.com/ThanhhBDepTrai/Group-13-LTM-TFTP",
            "Nhánh chính: main  ·  Commit hợp nhất: 9683811",
            "Kiểm thử: 255/255 test PASSED (16 tệp, 22,26 giây)",
            "Môi trường: Python 3.15.2 trên Windows x64",
        ],
    )

    # ---------------- 1. Tổng quan ----------------
    section(pdf, "1. Tổng quan")
    para(
        pdf,
        "TFTP Pro là ứng dụng truyền tệp theo giao thức TFTP (RFC 1350), mở rộng theo các chuẩn "
        "RFC 2347/2348/2349 (option negotiation) và RFC 7440 (window size). Ứng dụng gồm máy chủ "
        "TFTP, máy khách, Admin API dạng REST, giao diện đồ họa Tkinter và mô-đun quản trị doanh "
        "nghiệp (Enterprise) phục vụ chia sẻ tệp nội bộ.",
    )
    para(
        pdf,
        "Ba nhánh phát triển đã được hợp nhất thành công vào main. Nhánh tftp_engine_upgrade nâng cấp "
        "lõi truyền tải; backend-developer bổ sung quản lý tệp, xác thực, đo lường, SHA-256 và PXE; "
        "efm-enterprise mang đến tài khoản, nhân viên, phân quyền tệp, đa máy chủ và giao diện quản "
        "trị mới. Toàn bộ 255 bài kiểm thử đều đạt.",
    )
    sub(pdf, "Thay đổi nổi bật")
    for item in [
        "Lõi TFTP hỗ trợ cửa sổ trượt (RFC 7440), thương lượng tùy chọn blksize/timeout/tsize/windowsize và ACK tích lũy.",
        "Admin API có xác thực token, phân quyền admin/user/guest, quản lý tệp, thống kê, phân tích gói tin và hồ sơ PXE.",
        "Giao diện Tkinter được viết lại: thêm màn hình chào, tạo Workspace, kết nối máy chủ, đăng nhập và tab Nhân viên.",
        "Mô-đun Enterprise bổ sung tài khoản, nhân viên, phòng ban, chức vụ, phân quyền tệp, công việc và nhật ký kiểm toán.",
        "Hỗ trợ nhiều máy chủ với mã định danh SRV-XXXXXXXX và tự động khởi chạy TFTP + Admin API cho từng máy chủ.",
    ]:
        bullet(pdf, item)
    pdf.ln(2)

    # ---------------- 2. Kết quả kiểm thử ----------------
    section(pdf, "2. Kết quả kiểm thử")
    para(
        pdf,
        "Kết quả thu được bằng lệnh pytest trên toàn bộ thư mục tests/ tại nhánh main sau khi hợp nhất.",
    )
    test_rows = [
        ["test_packet.py", "135", "PASSED"],
        ["test_storage.py", "22", "PASSED"],
        ["test_windowsize.py", "21", "PASSED"],
        ["test_auth.py", "14", "PASSED"],
        ["test_admin_api.py", "13", "PASSED"],
        ["test_enterprise.py", "13", "PASSED"],
        ["test_server.py", "8", "PASSED"],
        ["test_pxe.py", "7", "PASSED"],
        ["test_config.py", "6", "PASSED"],
        ["test_integrity.py", "4", "PASSED"],
        ["test_metrics.py", "4", "PASSED"],
        ["test_packet_analyzer.py", "3", "PASSED"],
        ["test_multi_server.py", "2", "PASSED"],
        ["test_cancel.py", "1", "PASSED"],
        ["test_client.py", "1", "PASSED"],
        ["test_integration.py", "1", "PASSED"],
        ["TỔNG CỘNG", "255", "TẤT CẢ PASSED"],
    ]
    table(
        pdf,
        ["Tệp kiểm thử", "Số bài", "Kết quả"],
        test_rows,
        [90, 30, 60],
        aligns=["L", "C", "C"],
        total=True,
    )

    # ---------------- 3. Chức năng ----------------
    section(pdf, "3. Chức năng chính")

    sub(pdf, "3.1. Lõi TFTP (RFC 1350, 2347, 2348, 2349, 7440)")
    for item in [
        "RRQ (tải xuống) và WRQ (tải lên) có thương lượng tùy chọn; ghi tệp nguyên tử bằng tệp tạm và os.replace.",
        "Đóng gói và phân tích DATA, ACK, ERROR, OACK; số khối 16-bit, xử lý đúng mốc kết thúc tệp.",
        "Window size: gửi/nhận nhiều khối trước khi chờ ACK, tự động giảm về 1 khi máy khách không hỗ trợ.",
        "Thương lượng blksize (8–65464), timeout (1–255 giây), tsize và windowsize (1–65535).",
        "Cơ chế timeout và gửi lại có giới hạn; ACK tích lũy theo cửa sổ; phát hiện gói trùng.",
        "Mỗi phiên dùng một TID riêng, tránh lẫn gói giữa các máy khách; gói sai TID bị từ chối.",
        "Kiểm tra an toàn đường dẫn: chặn path traversal, đường dẫn tuyệt đối và thoát liên kết tượng trưng.",
    ]:
        bullet(pdf, item)
    pdf.ln(1.5)

    sub(pdf, "3.2. Admin API (HTTP REST)")
    for item in [
        "Xác thực Bearer token; mật khẩu băm PBKDF2-HMAC-SHA256 kèm salt; token lưu dưới dạng SHA-256.",
        "Ba vai trò admin, user, guest với ma trận quyền rõ ràng cho từng nhóm endpoint.",
        "Quản lý tệp: liệt kê, tìm kiếm, xem metadata, tải lên, tải xuống, đổi tên, tạo thư mục, xóa.",
        "Tính và đối chiếu SHA-256, trả về VERIFIED hoặc MISMATCH.",
        "Bảng điều khiển: số phiên, dung lượng, tốc độ, số lần timeout và gửi lại.",
        "Phân tích gói tin: ghi vết nội bộ, lọc theo opcode, chiều truyền và số khối.",
        "Hồ sơ PXE: quản lý cấu hình khởi động BIOS/UEFI và kiểm tra tệp boot.",
        "Quản lý người dùng: thêm, xóa, đổi vai trò, bật/tắt, đặt lại mật khẩu và nhật ký kiểm toán.",
    ]:
        bullet(pdf, item)
    pdf.ln(1.5)

    sub(pdf, "3.3. Mô-đun Enterprise")
    for item in [
        "Tài khoản: SUPER_ADMIN, mã tham gia (join code), khóa tài khoản khi đăng nhập sai nhiều lần.",
        "Nhân viên: hồ sơ, phòng ban, chức vụ và phân cấp quản lý.",
        "Phân quyền tệp (ACL) theo người dùng hoặc vai trò trên từng tệp và thư mục.",
        "Công việc: giao việc, theo dõi trạng thái và lưu vết thay đổi.",
        "Đa máy chủ: đăng ký, kết nối và xác minh danh tính máy chủ qua mã SRV-XXXXXXXX.",
        "Server Runtime: tự động khởi chạy TFTP và Admin API cho mỗi máy chủ được tạo.",
        "Triển khai: script cài đặt Linux, systemd, tường lửa, sao lưu và kiểm tra sức khỏe hệ thống.",
    ]:
        bullet(pdf, item)
    pdf.ln(1.5)

    sub(pdf, "3.4. Công cụ hỗ trợ")
    for item in [
        "Benchmark: đo hiệu năng theo các bộ tham số A–E, kiểm tra SHA-256 và xuất kết quả JSON.",
        "Packet Analyzer: lọc, nhóm, tổng hợp sự kiện gói tin và phát hiện truyền lại.",
        "Giao diện Tkinter: cấu hình, đăng nhập và các thao tác quản trị trực quan.",
        "CI/CD: GitHub Actions chạy trên Ubuntu và Windows với Python 3.10, 3.11, 3.12.",
    ]:
        bullet(pdf, item)
    pdf.ln(2)

    # ---------------- 4. Giao diện ----------------
    section(pdf, "4. Thay đổi giao diện")
    para(
        pdf,
        "Đây là phần thay đổi rõ rệt nhất với người dùng cuối. Trước khi hợp nhất, giao diện mở thẳng "
        "vào cửa sổ chính với 6 tab và chưa có luồng tạo/kết nối máy chủ. Sau khi hợp nhất, giao diện "
        "được viết lại theo mô hình doanh nghiệp: có màn hình chào, tạo Workspace, kết nối máy chủ, "
        "đăng nhập và thêm tab quản lý nhân viên.",
    )

    sub(pdf, "4.1. Trước khi hợp nhất")
    for item in [
        "Tệp tftp_gui.py dài 1.781 dòng, mở thẳng vào giao diện chính.",
        "6 tab: Truyền file, File Manager, Dashboard, Packet Analyzer, Người dùng, PXE.",
        "Chưa có khái niệm Workspace, chưa có đăng nhập doanh nghiệp, chưa quản lý nhân viên.",
    ]:
        bullet(pdf, item)
    pdf.ln(1.5)

    sub(pdf, "4.2. Sau khi hợp nhất")
    for item in [
        "Tệp tftp_gui.py dài 2.496 dòng (tăng 715 dòng, khoảng 40%).",
        "Màn hình chào với ba lựa chọn: Tạo Workspace mới, Join Server, Đăng nhập.",
        "Luồng tạo Workspace: nhập thông tin tổ chức, kiểm tra máy chủ, sinh Server ID và tạo SUPER_ADMIN.",
        "Luồng Join Server: nhập địa chỉ máy chủ và tài khoản được cấp, xác minh mã SRV trước khi kết nối.",
        "Thanh đăng nhập/đăng xuất ở góc trên; giao diện đổi theo quyền của tài khoản.",
        "Thêm tab Nhân viên (quản lý nhân viên, phòng ban, chức vụ) bên cạnh 6 tab cũ.",
        "Bảng màu và kiểu chữ được làm mới (Segoe UI, tông xanh navy, tab gọn hơn).",
        "Ứng dụng efm_app.py (176 dòng) cho phép chạy riêng luồng Enterprise File Manager.",
    ]:
        bullet(pdf, item)
    pdf.ln(2)

    sub(pdf, "4.3. Bảy tab trên giao diện chính")
    table(
        pdf,
        ["Tab", "Chức năng"],
        [
            ["Truyền file", "Tải lên/tải xuống TFTP, thanh tiến trình, tốc độ, thời gian còn lại, nhật ký và hủy phiên."],
            ["File Manager", "Duyệt và tìm tệp qua Admin API; tải lên, tải xuống, đổi tên, tạo thư mục, xóa, kiểm tra SHA-256."],
            ["Dashboard", "Số máy khách, tổng phiên, dung lượng, tốc độ, số lần timeout và gửi lại."],
            ["Packet Analyzer", "Lọc vết gói tin theo opcode, chiều truyền và số khối; xem payload, nguồn, đích."],
            ["Người dùng", "Tạo và xóa tài khoản, đổi vai trò, bật/tắt, đặt lại mật khẩu."],
            ["PXE", "Tạo, sửa, xóa hồ sơ khởi động BIOS/UEFI và kiểm tra tệp boot."],
            ["Nhân viên", "Quản lý nhân viên, phòng ban, chức vụ và phân quyền theo vai trò."],
        ],
        [42, 138],
    )

    sub(pdf, "4.4. Cấu trúc thư mục sau hợp nhất")
    table(
        pdf,
        ["Thư mục / tệp", "Nội dung"],
        [
            ["tftp_pro.py", "Điểm khởi chạy máy chủ TFTP và Admin API."],
            ["tftp_server.py", "Lõi TFTP: RRQ/WRQ, cửa sổ trượt, TID, an toàn đường dẫn."],
            ["tftp_client.py, tftp_packet.py, tftp_options.py", "Máy khách, đóng gói gói tin và thương lượng tùy chọn."],
            ["admin_api.py, admin_client.py", "REST API quản trị và thư viện gọi API phía máy khách."],
            ["auth.py, metrics.py, storage_manager.py, pxe_config.py", "Xác thực, đo lường, lưu trữ tệp và cấu hình PXE."],
            ["tftp_gui.py", "Giao diện Tkinter 7 tab."],
            ["efm_app.py", "Ứng dụng Enterprise File Manager chạy độc lập."],
            ["client/", "Đăng nhập, kết nối máy chủ, đăng ký và quản lý phiên."],
            ["enterprise/", "Tài khoản, nhân viên, phân quyền tệp, công việc, kiểm toán."],
            ["database/", "Lưu trữ dữ liệu và dữ liệu mẫu."],
            ["deployment/", "Script cài đặt Linux, systemd, tường lửa, sao lưu."],
            ["docs/, scripts/", "Tài liệu kỹ thuật, benchmark, đóng gói và tạo báo cáo."],
            ["tests/", "16 tệp kiểm thử với 255 bài."],
        ],
        [62, 118],
    )

    # ---------------- 5. Lỗi đã sửa ----------------
    section(pdf, "5. Lỗi đã sửa trong quá trình hợp nhất")
    table(
        pdf,
        ["Lỗi", "Nguyên nhân", "Cách khắc phục"],
        [
            ["ModuleNotFoundError: tftp_transfer", "Nhánh backend đổi lõi sang mô-đun không tồn tại", "Khôi phục tệp lõi từ main"],
            ["ModuleNotFoundError: fastapi", "admin_api.py phụ thuộc FastAPI", "Khôi phục admin_api.py từ main"],
            ["ImportError: AuthManager", "auth.py bị thay đổi API", "Khôi phục auth.py từ main"],
            ["ImportError: MetricsCollector", "metrics.py bị thay đổi API", "Khôi phục metrics.py từ main"],
            ["ImportError: DEFAULT_TOKEN_TTL", "tftp_config.py thiếu hằng số", "Khôi phục tftp_config.py từ main"],
            ["TypeError: unexpected keyword 'enterprise'", "AdminAPIServer thiếu tham số enterprise", "Thêm enterprise=None và gán self.enterprise"],
            ["OSError: WinError 10022", "Socket chưa bind đã gọi getsockname", "Bind socket trước khi gán self.sock"],
            ["EnterpriseError: Server ID không khớp", "Endpoint health thiếu server_id", "Trả server_id khi có enterprise"],
            ["Xung đột hợp nhất efm-enterprise", "Lịch sử Git không liên quan, 6 tệp add/add", "Giải quyết xung đột và áp dụng lại bản vá"],
        ],
        [50, 62, 68],
    )

    # ---------------- 6. Hướng dẫn chạy ----------------
    section(pdf, "6. Hướng dẫn chạy")
    para(
        pdf,
        "Máy hiện tại không có python trong PATH, vì vậy cần dùng đường dẫn đầy đủ tới trình thông dịch "
        "hoặc đặt biến môi trường PY cho gọn. Mở Command Prompt tại thư mục dự án:",
    )
    code_block(
        pdf,
        [
            "cd E:\\Group-13-LTM-TFTP",
            "set PY=C:\\Users\\Administrator\\AppData\\Roaming\\uv\\python\\cpython-3.15.2-windows-x86_64-none\\python.exe",
        ],
    )

    sub(pdf, "6.1. Chạy máy chủ TFTP và Admin API")
    code_block(pdf, ["%PY% tftp_pro.py"])
    para(pdf, "Mặc định máy chủ lắng nghe TFTP tại 0.0.0.0:6969 và Admin API tại http://127.0.0.1:6971.")

    sub(pdf, "6.2. Chạy giao diện đồ họa")
    code_block(pdf, ["%PY% tftp_gui.py"])

    sub(pdf, "6.3. Chạy máy khách dòng lệnh")
    code_block(
        pdf,
        [
            "%PY% tftp_client.py --host 127.0.0.1 --port 6969 get README.md README.downloaded.md",
            "%PY% tftp_client.py --host 127.0.0.1 --port 6969 put README.md README.md",
        ],
    )

    sub(pdf, "6.4. Chạy benchmark và kiểm thử")
    code_block(
        pdf,
        [
            "%PY% scripts\\benchmark_tftp.py --size 2MiB --runs 3",
            "%PY% -m pytest tests\\ -v --tb=short",
        ],
    )

    sub(pdf, "6.5. Chạy ứng dụng Enterprise")
    code_block(pdf, ["%PY% efm_app.py"])

    # ---------------- 7. Cấu hình ----------------
    section(pdf, "7. Cấu hình mặc định")
    table(
        pdf,
        ["Thông số", "Giá trị"],
        [
            ["Giao thức TFTP (UDP)", "0.0.0.0:6969"],
            ["Admin API", "http://127.0.0.1:6971"],
            ["Thư mục chia sẻ", "server_storage"],
            ["Kích thước khối mặc định", "512 byte"],
            ["Timeout / số lần gửi lại", "3,0 giây / 5 lần"],
            ["Số máy khách tối đa", "20"],
            ["Window size phía máy khách", "1 (tương thích RFC 1350)"],
            ["Window size tối đa phía máy chủ", "16 (RFC 7440)"],
            ["Kích thước yêu cầu Admin tối đa", "64 MiB"],
            ["Thời hạn token", "8 giờ"],
        ],
        [80, 100],
    )

    # ---------------- 8. Bảo mật ----------------
    section(pdf, "8. Bảo mật và giới hạn")
    for item in [
        "Không đưa TFTP hoặc Admin API ra Internet; chỉ dùng trong mạng nội bộ, phòng lab hoặc qua VPN.",
        "Admin API mặc định chỉ lắng nghe trên localhost; chương trình cảnh báo nếu bind ra địa chỉ khác.",
        "Mật khẩu lưu bằng PBKDF2-HMAC-SHA256 kèm salt; token lưu dưới dạng băm SHA-256.",
        "Nhật ký không ghi mật khẩu hay token.",
        "StorageManager chặn path traversal, đường dẫn tuyệt đối, ổ đĩa Windows và liên kết tượng trưng.",
        "PXE chỉ quản lý hồ sơ cấu hình; dịch vụ DHCP nằm ngoài phạm vi dự án.",
        "SHA-256 dùng để kiểm tra toàn vẹn, không biến TFTP thành giao thức an toàn.",
    ]:
        bullet(pdf, item)
    pdf.ln(2)

    # ---------------- 9. Đề xuất ----------------
    section(pdf, "9. Đề xuất bước tiếp theo")
    para(
        pdf,
        "Các đầu việc dưới đây được tổng hợp từ kết quả rà soát mã nguồn và tài liệu, ưu tiên theo mức "
        "độ ảnh hưởng tới việc bàn giao và vận hành thực tế.",
    )
    table(
        pdf,
        ["Công việc", "Ưu tiên", "Ghi chú"],
        [
            ["Kiểm thử tải với nhiều máy khách thật", "Cao", "Đo thông lượng, timeout và retransmission ở 20–50 phiên đồng thời."],
            ["Hoàn thiện phân quyền Enterprise", "Cao", "Bổ sung ACL theo phòng ban và kiểm thử các tình huống bị từ chối."],
            ["Kiểm thử giao diện theo kịch bản", "Trung bình", "Lập checklist cho 7 tab và ba luồng Workspace/Join/Login."],
            ["Cập nhật tài liệu phân công", "Trung bình", "Đồng bộ docs/phan_chia_cong_viec.docx với tiến độ hợp nhất."],
            ["Đóng gói bản cài 1.1.0", "Trung bình", "Sửa scripts/build_exe.ps1 còn ghi tên bản 1.0.0."],
            ["Dọn dẹp kho mã nguồn", "Thấp", "Bổ sung tftp_admin.db, *.metrics.db, pxe_profiles.json vào .gitignore."],
            ["Sửa đường dẫn cứng trong script", "Thấp", "scripts/make_docx.py còn trỏ tới ổ E:\\VSCode\\TFTP-Pro."],
        ],
        [60, 22, 98],
        aligns=["L", "C", "L"],
    )

    # ---------------- 10. Kết luận ----------------
    section(pdf, "10. Kết luận")
    para(
        pdf,
        "Dự án TFTP Pro đã hoàn thiện ở mức có thể bàn giao: ba nhánh được hợp nhất, 255 bài kiểm thử "
        "đều đạt, giao diện được nâng cấp theo hướng doanh nghiệp và tài liệu kỹ thuật đầy đủ. Các "
        "bước tiếp theo nên tập trung vào kiểm thử trên mạng thật nhiều máy khách, bổ sung phân quyền "
        "chi tiết hơn cho Enterprise và cập nhật tài liệu phân công công việc theo tiến độ hiện tại.",
    )
    info_box(
        pdf,
        "Kết quả chính",
        [
            "255/255 bài kiểm thử đạt trên nhánh main.",
            "Giao diện tăng từ 6 lên 7 tab, thêm luồng Workspace, Join Server và đăng nhập.",
            "Mô-đun Enterprise, đa máy chủ và script triển khai đã sẵn sàng.",
        ],
    )

    pdf.output(str(OUT_PATH))
    return OUT_PATH


if __name__ == "__main__":
    path = build()
    print(f"PDF created: {path}")
