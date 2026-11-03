"""TFTP Pro GUI (Tkinter).

Gồm 4 khu vực chính theo kế hoạch:
* Truyền file TFTP (giữ tương thích GUI cũ).
* File Manager qua Admin API (không truy cập trực tiếp ổ đĩa server).
* Dashboard metrics và Packet Analyzer.
* Quản lý người dùng và PXE profile (Admin API).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import unicodedata
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from admin_client import AdminClient
from enterprise.server_runtime import ServerRuntime
from packet_analyzer import PacketAnalyzer, PacketFilter
from tftp_client import TFTPClient
from tftp_config import (
    DEFAULT_ADMIN_HOST,
    DEFAULT_ADMIN_PORT,
    DEFAULT_CLIENT_HOST,
    DEFAULT_MAX_ADMIN_BODY,
    DEFAULT_MAX_WINDOWSIZE,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_WINDOWSIZE,
)

if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001, S110
        pass

APP_DIR = Path(os.getenv("APPDATA", Path.home())) / "TFTPPro"
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_ADMIN_URL = f"http://{DEFAULT_ADMIN_HOST}:{DEFAULT_ADMIN_PORT}"
logger = logging.getLogger(__name__)


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("config must be an object")
        return data
    except (OSError, TypeError, ValueError):
        return {
            "host": DEFAULT_CLIENT_HOST,
            "port": DEFAULT_PORT,
            "timeout": DEFAULT_TIMEOUT,
            "windowsize": DEFAULT_WINDOWSIZE,
            "admin_url": DEFAULT_ADMIN_URL,
            "admin_user": "admin",
        }


def save_config(data: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def join_remote(base: str, name: str) -> str:
    base = (base or "").strip().strip("/")
    name = (name or "").strip().strip("/")
    return f"{base}/{name}".strip("/") if base else name


class TFTPApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TFTP Pro - Transfer, Admin API & Packet Analyzer")
        self.geometry("1280x860")
        self.minsize(1040, 720)
        self._setup_theme()

        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._busy = False
        self._listing = False
        self._download_mode = False
        self._files: list[str] = []

        self._progress_started = 0.0
        self._last_progress_time = 0.0
        self._last_progress_bytes = 0

        self.admin: AdminClient | None = None
        self.admin_user: dict | None = None
        self._admin_client_pending: AdminClient | None = None
        self._fm_by_iid: dict[str, dict] = {}
        self._users_by_name: dict[str, dict] = {}
        self._pxe_by_name: dict[str, dict] = {}

        cfg = load_config()
        self.host_var = tk.StringVar(value=cfg.get("host", DEFAULT_CLIENT_HOST))
        self.port_var = tk.StringVar(value=str(cfg.get("port", DEFAULT_PORT)))
        self.windowsize_var = tk.StringVar(
            value=str(cfg.get("windowsize", DEFAULT_WINDOWSIZE))
        )
        self.remote_var = tk.StringVar()

        self.admin_url_var = tk.StringVar(
            value=cfg.get("admin_url", DEFAULT_ADMIN_URL)
        )
        self.admin_user_var = tk.StringVar(value=cfg.get("admin_user", "admin"))
        self.admin_pass_var = tk.StringVar()
        self.admin_status_var = tk.StringVar(value="Chưa đăng nhập Admin API")

        self.fm_path_var = tk.StringVar(value="")
        self.fm_search_var = tk.StringVar()
        self.fm_hash_var = tk.StringVar(value="SHA-256: --")

        self.packet_opcode_var = tk.StringVar(value="Tất cả")
        self.packet_direction_var = tk.StringVar(value="Tất cả")
        self.packet_limit_var = tk.StringVar(value="200")
        self.packet_summary_var = tk.StringVar(value="Chưa có dữ liệu packet.")

        self.user_username_var = tk.StringVar()
        self.user_password_var = tk.StringVar()
        self.user_new_role_var = tk.StringVar(value="EMPLOYEE")
        self.user_edit_role_var = tk.StringVar(value="EMPLOYEE")

        self.pxe_name_var = tk.StringVar()
        self.pxe_description_var = tk.StringVar()
        self.pxe_bios_var = tk.StringVar()
        self.pxe_uefi_var = tk.StringVar()
        self.pxe_default_var = tk.BooleanVar(value=False)
        self.pxe_require_files_var = tk.BooleanVar(value=False)
        self.pxe_arch_var = tk.StringVar(value="bios")
        self.pxe_issues_var = tk.StringVar(value="")

        self._dash_vars = {
            key: tk.StringVar(value="--")
            for key in (
                "active_clients",
                "total_transfers",
                "successful",
                "failed",
                "uploads",
                "downloads",
                "bytes_upload",
                "bytes_download",
                "avg_speed_bytes_per_second",
                "timeouts",
                "retries",
            )
        }

        self.workspace_data: dict = {}
        self._server_runtime: ServerRuntime | None = None

        # Bắt đầu từ màn hình Welcome
        self._build_welcome_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_queue)


    # ------------------------------------------------------------------ #
    # Giao diện / theme
    # ------------------------------------------------------------------ #
    def _setup_theme(self):
        """Thiết lập theme thống nhất, không thay đổi logic nghiệp vụ."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.palette = {
            "navy": "#172B4D",
            "navy_light": "#243F66",
            "blue": "#2563EB",
            "blue_hover": "#1D4ED8",
            "background": "#F3F6FB",
            "surface": "#FFFFFF",
            "border": "#DCE3ED",
            "text": "#243247",
            "muted": "#718096",
            "green": "#16865B",
            "red": "#C2413A",
        }
        self.configure(bg=self.palette["background"])
        style.configure(".", font=("Segoe UI", 10), foreground=self.palette["text"])
        style.configure("TFrame", background=self.palette["background"])
        style.configure("TLabel", background=self.palette["background"], foreground=self.palette["text"])
        style.configure("TLabelframe", background=self.palette["surface"], bordercolor=self.palette["border"], relief="solid")
        style.configure("TLabelframe.Label", background=self.palette["surface"], foreground=self.palette["navy"], font=("Segoe UI", 10, "bold"))
        style.configure("TEntry", padding=(9, 7), fieldbackground=self.palette["surface"], bordercolor=self.palette["border"])
        style.configure("TCombobox", padding=(8, 6), fieldbackground=self.palette["surface"], bordercolor=self.palette["border"])
        style.configure("TSpinbox", padding=(8, 6), fieldbackground=self.palette["surface"])
        style.configure("TButton", padding=(12, 8), background="#E8EEF7", foreground=self.palette["navy"], borderwidth=0, focusthickness=0, font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", "#D8E3F2"), ("pressed", "#CBD8E9"), ("disabled", "#EEF1F5")], foreground=[("disabled", "#9AA5B4")])
        style.configure("Accent.TButton", background=self.palette["blue"], foreground="#FFFFFF")
        style.map("Accent.TButton", background=[("active", self.palette["blue_hover"]), ("pressed", self.palette["navy_light"])], foreground=[("disabled", "#D8E1EF")])
        style.configure("TNotebook", background=self.palette["background"], borderwidth=0, tabmargins=(3, 5, 3, 0))
        style.configure("TNotebook.Tab", padding=(18, 11), background="#E8EDF5", foreground=self.palette["muted"], font=("Segoe UI", 9, "bold"), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", self.palette["surface"]), ("active", "#DDE7F4")], foreground=[("selected", self.palette["blue"]), ("active", self.palette["navy"])])
        style.configure("Treeview", background=self.palette["surface"], fieldbackground=self.palette["surface"], foreground=self.palette["text"], rowheight=31, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#EAF0F7", foreground=self.palette["navy"], relief="flat", padding=(9, 9), font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#DCEAFE")], foreground=[("selected", self.palette["navy"])])
        style.configure("Horizontal.TProgressbar", troughcolor="#E5EAF2", background=self.palette["blue"], bordercolor="#E5EAF2", lightcolor=self.palette["blue"], darkcolor=self.palette["blue"], thickness=12)
        style.configure("TCheckbutton", background=self.palette["surface"])
        style.configure("TRadiobutton", background=self.palette["surface"])

    # ------------------------------------------------------------------ #
    # Helper
    # ------------------------------------------------------------------ #
    def _clear_screen(self):
        for widget in self.winfo_children():
            widget.destroy()

    # ------------------------------------------------------------------ #
    # 1. Welcome Screen
    # ------------------------------------------------------------------ #
    def _build_welcome_ui(self):
        self._clear_screen()
        self.title("Enterprise File Manager")
        self.geometry("1000x680")
        self.minsize(850, 600)

        bg_color = "#F3F6FA"
        primary = "#1E3A5F"
        text_color = "#243447"
        muted = "#718096"
        self.configure(bg=bg_color)

        main = tk.Frame(self, bg=bg_color)
        main.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(main, bg=primary, height=210)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(
            header, text="ENTERPRISE FILE MANAGER",
            font=("Segoe UI", 25, "bold"), fg="white", bg=primary,
        ).pack(pady=(48, 8))
        tk.Label(
            header, text="WORKSPACE  •  FILE MANAGEMENT  •  TFTP TRANSFER",
            font=("Segoe UI", 10), fg="#C8D6E5", bg=primary,
        ).pack()

        content = tk.Frame(main, bg=bg_color)
        content.pack(fill=tk.BOTH, expand=True, padx=70, pady=35)

        tk.Label(
            content, text="Chào mừng bạn!",
            font=("Segoe UI", 23, "bold"), fg=text_color, bg=bg_color,
        ).pack(pady=(5, 8))
        tk.Label(
            content, text="Bạn muốn bắt đầu bằng cách nào?",
            font=("Segoe UI", 11), fg=muted, bg=bg_color,
        ).pack(pady=(0, 25))

        cards = tk.Frame(content, bg=bg_color)
        cards.pack(fill=tk.X, expand=True)
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        create_card = tk.Frame(
            cards, bg="white", highlightbackground="#DCE4ED",
            highlightthickness=1, padx=22, pady=25,
        )
        create_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12), ipady=15)
        tk.Label(create_card, text="＋", font=("Segoe UI", 35, "bold"),
                 fg="#269B69", bg="white").pack(pady=(5, 10))
        tk.Label(create_card, text="Create Server", font=("Segoe UI", 17, "bold"),
                 fg=text_color, bg="white").pack()
        tk.Label(create_card, text="Create New Workspace", font=("Segoe UI", 10),
                 fg="#269B69", bg="white").pack(pady=(4, 12))
        tk.Label(
            create_card,
            text="Tạo Workspace mới cho công ty.\nThiết lập thông tin tổ chức và\ntài khoản SUPER_ADMIN đầu tiên.",
            font=("Segoe UI", 10), fg=muted, bg="white", justify=tk.CENTER,
        ).pack(pady=(0, 22))
        ttk.Button(create_card, text="CREATE SERVER",
                   command=self._open_create_server).pack(fill=tk.X, ipady=7)

        join_card = tk.Frame(
            cards, bg="white", highlightbackground="#DCE4ED",
            highlightthickness=1, padx=22, pady=25,
        )
        join_card.grid(row=0, column=1, sticky="nsew", padx=(12, 0), ipady=15)
        tk.Label(join_card, text="⇄", font=("Segoe UI", 35, "bold"),
                 fg="#3985D6", bg="white").pack(pady=(5, 10))
        tk.Label(join_card, text="Join Server", font=("Segoe UI", 17, "bold"),
                 fg=text_color, bg="white").pack()
        tk.Label(join_card, text="Connect Existing Workspace", font=("Segoe UI", 10),
                 fg="#3985D6", bg="white").pack(pady=(4, 12))
        tk.Label(
            join_card,
            text="Kết nối tới Server của công ty.\nSử dụng địa chỉ Server và tài khoản\nđược Admin hoặc HR cấp.",
            font=("Segoe UI", 10), fg=muted, bg="white", justify=tk.CENTER,
        ).pack(pady=(0, 22))
        ttk.Button(join_card, text="JOIN SERVER",
                   command=self._open_join_server).pack(fill=tk.X, ipady=7)

        tk.Label(
            main, text="Enterprise File Manager  |  Version 1.0",
            font=("Segoe UI", 9), fg=muted, bg=bg_color,
        ).pack(pady=(0, 15))

    def _open_create_server(self):
        self._build_create_workspace_ui()

    def _open_join_server(self):
        self._build_login_ui()

    # ------------------------------------------------------------------ #
    # 2. Create Workspace
    # ------------------------------------------------------------------ #
    def _build_create_workspace_ui(self):
        self._clear_screen()
        self.title("Enterprise File Manager - Create Workspace")
        self.geometry("1000x720")
        self.minsize(850, 650)

        bg = "#F3F6FA"
        primary = "#1E3A5F"
        text_color = "#243447"
        muted = "#718096"
        self.configure(bg=bg)

        self.workspace_name_var = tk.StringVar()
        self.company_name_var = tk.StringVar()
        self.workspace_id_var = tk.StringVar()
        self.workspace_storage_var = tk.StringVar(
            value=str(Path.home() / "EnterpriseFileManager" / "storage")
        )
        self.workspace_name_var.trace_add("write", self._generate_workspace_id)

        main = tk.Frame(self, bg=bg)
        main.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(main, bg=primary, height=145)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="CREATE WORKSPACE",
            font=("Segoe UI", 23, "bold"), fg="white", bg=primary,
        ).pack(anchor="w", padx=65, pady=(32, 5))
        tk.Label(
            header, text="Thiết lập không gian làm việc cho tổ chức của bạn",
            font=("Segoe UI", 10), fg="#C8D6E5", bg=primary,
        ).pack(anchor="w", padx=67)

        content = tk.Frame(main, bg=bg)
        content.pack(fill=tk.BOTH, expand=True, padx=65, pady=25)
        tk.Label(
            content, text="Workspace Information",
            font=("Segoe UI", 17, "bold"), fg=text_color, bg=bg,
        ).pack(anchor="w", pady=(0, 5))
        tk.Label(
            content, text="Nhập thông tin cơ bản của Workspace.",
            font=("Segoe UI", 10), fg=muted, bg=bg,
        ).pack(anchor="w", pady=(0, 15))

        form = tk.Frame(
            content, bg="white", highlightbackground="#DCE4ED",
            highlightthickness=1, padx=25, pady=20,
        )
        form.pack(fill=tk.BOTH, expand=True)
        form.columnconfigure(1, weight=1)

        def add_field(row, label, variable, hint=None, readonly=False):
            tk.Label(
                form, text=label, font=("Segoe UI", 10, "bold"),
                fg=text_color, bg="white",
            ).grid(row=row, column=0, sticky="w", padx=(0, 20), pady=10)
            entry = ttk.Entry(form, textvariable=variable, font=("Segoe UI", 10))
            entry.grid(row=row, column=1, sticky="ew", pady=10, ipady=5)
            if readonly:
                entry.configure(state="readonly")
            if hint:
                tk.Label(
                    form, text=hint, font=("Segoe UI", 8), fg=muted, bg="white",
                ).grid(row=row + 1, column=1, sticky="w", pady=(0, 5))
            return entry

        add_field(0, "Workspace Name *", self.workspace_name_var,
                  "Tên không gian làm việc, ví dụ: IT Department")
        add_field(2, "Company Name *", self.company_name_var,
                  "Tên công ty hoặc tổ chức sở hữu Workspace.")
        add_field(4, "Workspace ID", self.workspace_id_var,
                  "Mã định danh được tự động tạo từ Workspace Name.", readonly=True)

        tk.Label(
            form, text="Description", font=("Segoe UI", 10, "bold"),
            fg=text_color, bg="white",
        ).grid(row=6, column=0, sticky="nw", padx=(0, 20), pady=10)
        self.workspace_description_text = tk.Text(
            form, height=3, font=("Segoe UI", 10),
            relief=tk.SOLID, borderwidth=1, wrap=tk.WORD,
        )
        self.workspace_description_text.grid(row=6, column=1, sticky="ew", pady=10)

        tk.Label(
            form, text="Storage Location *", font=("Segoe UI", 10, "bold"),
            fg=text_color, bg="white",
        ).grid(row=7, column=0, sticky="w", padx=(0, 20), pady=10)
        storage_frame = tk.Frame(form, bg="white")
        storage_frame.grid(row=7, column=1, sticky="ew", pady=10)
        storage_frame.columnconfigure(0, weight=1)
        ttk.Entry(storage_frame, textvariable=self.workspace_storage_var).grid(
            row=0, column=0, sticky="ew", ipady=5
        )
        ttk.Button(
            storage_frame, text="Browse", command=self._browse_workspace_storage
        ).grid(row=0, column=1, padx=(8, 0))

        tk.Label(
            content,
            text="* Các trường bắt buộc phải nhập.",
            font=("Segoe UI", 9), fg=muted, bg=bg, justify=tk.LEFT,
        ).pack(anchor="w", pady=(12, 0))

        footer = tk.Frame(main, bg=bg)
        footer.pack(fill=tk.X, padx=65, pady=(0, 20))
        ttk.Button(footer, text="BACK", command=self._build_welcome_ui).pack(
            side=tk.LEFT, ipadx=20, ipady=6
        )
        ttk.Button(footer, text="NEXT", command=self._workspace_next).pack(
            side=tk.RIGHT, ipadx=30, ipady=6
        )

    def _generate_workspace_id(self, *_args):
        name = self.workspace_name_var.get().strip()
        normalized = unicodedata.normalize("NFKD", name)
        normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
        workspace_id = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        self.workspace_id_var.set(workspace_id)

    def _browse_workspace_storage(self):
        folder = filedialog.askdirectory(title="Chọn thư mục lưu trữ Workspace")
        if folder:
            self.workspace_storage_var.set(folder)

    def _workspace_next(self):
        workspace_name = self.workspace_name_var.get().strip()
        company_name = self.company_name_var.get().strip()
        workspace_id = self.workspace_id_var.get().strip()
        storage = self.workspace_storage_var.get().strip()
        description = self.workspace_description_text.get("1.0", tk.END).strip()

        if not workspace_name:
            messagebox.showwarning("Create Workspace", "Vui lòng nhập Workspace Name.")
            return
        if not company_name:
            messagebox.showwarning("Create Workspace", "Vui lòng nhập Company Name.")
            return
        if not workspace_id:
            messagebox.showwarning("Create Workspace", "Workspace ID không hợp lệ.")
            return
        if not storage:
            messagebox.showwarning("Create Workspace", "Vui lòng chọn Storage Location.")
            return

        self.workspace_data = {
            "workspace_name": workspace_name,
            "company_name": company_name,
            "workspace_id": workspace_id,
            "description": description,
            "storage_location": storage,
        }
        self._build_admin_setup_ui()

    # ------------------------------------------------------------------ #
    # 3. Admin Setup
    # ------------------------------------------------------------------ #
    def _build_admin_setup_ui(self):
        self._clear_screen()
        self.title("Enterprise File Manager - Admin Setup")
        self.geometry("1000x680")
        self.minsize(850, 600)

        bg = "#F3F6FA"
        primary = "#1E3A5F"
        text_color = "#243447"
        muted = "#718096"
        self.configure(bg=bg)

        self.admin_username_var = tk.StringVar(value="admin")
        self.admin_email_var = tk.StringVar()
        self.admin_password_var = tk.StringVar()
        self.admin_confirm_var = tk.StringVar()

        main = tk.Frame(self, bg=bg)
        main.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(main, bg=primary, height=130)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="ADMIN SETUP",
            font=("Segoe UI", 22, "bold"), fg="white", bg=primary,
        ).pack(anchor="w", padx=65, pady=(28, 4))
        tk.Label(
            header, text="Tạo tài khoản SUPER_ADMIN cho Workspace",
            font=("Segoe UI", 10), fg="#C8D6E5", bg=primary,
        ).pack(anchor="w", padx=67)

        content = tk.Frame(main, bg=bg)
        content.pack(fill=tk.BOTH, expand=True, padx=65, pady=25)

        form = tk.Frame(
            content, bg="white", highlightbackground="#DCE4ED",
            highlightthickness=1, padx=30, pady=25,
        )
        form.pack(fill=tk.BOTH, expand=True)
        form.columnconfigure(1, weight=1)

        def add_row(row, label, variable, show=None):
            tk.Label(
                form, text=label, font=("Segoe UI", 10, "bold"),
                fg=text_color, bg="white",
            ).grid(row=row, column=0, sticky="w", padx=(0, 20), pady=12)
            entry = ttk.Entry(form, textvariable=variable, font=("Segoe UI", 10), show=show)
            entry.grid(row=row, column=1, sticky="ew", pady=12, ipady=5)
            return entry

        add_row(0, "Username *", self.admin_username_var)
        add_row(1, "Email", self.admin_email_var)
        add_row(2, "Password *", self.admin_password_var, show="*")
        add_row(3, "Confirm Password *", self.admin_confirm_var, show="*")

        tk.Label(
            content, text="* Bắt buộc. Tài khoản này sẽ có quyền SUPER_ADMIN.",
            font=("Segoe UI", 9), fg=muted, bg=bg,
        ).pack(anchor="w", pady=(10, 0))

        footer = tk.Frame(main, bg=bg)
        footer.pack(fill=tk.X, padx=65, pady=(0, 20))
        ttk.Button(
            footer, text="BACK", command=self._build_create_workspace_ui
        ).pack(side=tk.LEFT, ipadx=20, ipady=6)
        ttk.Button(
            footer, text="CREATE WORKSPACE", command=self._finish_create_workspace
        ).pack(side=tk.RIGHT, ipadx=20, ipady=6)

    def _finish_create_workspace(self):
        username = self.admin_username_var.get().strip()
        password = self.admin_password_var.get()
        confirm = self.admin_confirm_var.get()
        email = self.admin_email_var.get().strip()

        if not username:
            messagebox.showwarning("Admin Setup", "Vui lòng nhập Username.")
            return
        if not password or len(password) < 8:
            messagebox.showwarning("Admin Setup", "Password phải có ít nhất 8 ký tự.")
            return
        if password != confirm:
            messagebox.showwarning("Admin Setup", "Password và Confirm Password không khớp.")
            return

        workspace = self.workspace_data
        storage_root = Path(workspace["storage_location"]).expanduser()
        data_dir = APP_DIR / "servers" / workspace["workspace_id"]
        runtime = ServerRuntime(data_dir, storage_root=storage_root)
        try:
            if runtime.exists:
                # A previous attempt may have created the database before API startup failed.
                # Reuse that workspace instead of rejecting every subsequent Create click.
                runtime.start()
                cfg = runtime.config
            else:
                cfg = runtime.create(
                    workspace.get("company_name") or workspace["workspace_name"],
                    "127.0.0.1", DEFAULT_ADMIN_PORT, DEFAULT_PORT,
                    username, password, check_bind=True, auto_start=True,
                    workspace_name=workspace["workspace_name"],
                )
            client = AdminClient(f"http://127.0.0.1:{cfg.api_port}")
            result = client.login(username, password)
        except Exception as exc:
            try:
                runtime.stop()
            except Exception:
                pass
            messagebox.showerror(
                "Không thể tạo Workspace",
                f"Không thể tạo hoặc khởi động Workspace:\n{exc}\n\n"
                "Nếu đây là lần thử trước đã tạo database, ứng dụng sẽ thử mở lại Workspace đó. "
                "Nếu vẫn lỗi, hãy kiểm tra port 6971/6969 và quyền ghi thư mục lưu trữ.",
            )
            return

        self._server_runtime = runtime
        self.workspace_data["admin"] = {
            "username": username, "email": email,
        }
        self.admin = client
        self.admin_user = {
            "username": result.get("username", username),
            "role": result.get("role", "SUPER_ADMIN"),
            "permissions": result.get("permissions", []),
            "email": email,
        }
        self.admin_url_var.set(client.base_url)
        self.admin_user_var.set(username)
        self.admin_pass_var.set(password)
        self.admin_status_var.set(f"Đăng nhập: {username} (SUPER_ADMIN)")
        self._build_ui()  # _build_ui clears the setup screen before showing TFTP Pro.
        self.admin_status_var.set(f"Đăng nhập: {username} (SUPER_ADMIN)")
        if hasattr(self, "admin_logout_btn"):
            self.admin_logout_btn.configure(state="normal")
        self._refresh_after_login()
        messagebox.showinfo(
            "Tạo Workspace thành công",
            f"Workspace: {workspace['workspace_name']}\n"
            f"Admin API: {client.base_url}\nTFTP: 127.0.0.1:{cfg.tftp_port}\n\n"
            "Server đang chạy. Màn hình thiết lập đã được đóng.",
        )

    # ------------------------------------------------------------------ #
    # Join Server / Login
    # ------------------------------------------------------------------ #
    def _build_login_ui(self):
        self._clear_screen()
        self.title("Enterprise File Manager - Join Server")
        self.geometry("1000x680")
        self.minsize(850, 600)

        bg = "#F3F6FA"
        primary = "#1E3A5F"
        text_color = "#243447"
        muted = "#718096"
        self.configure(bg=bg)

        self.login_server_var = tk.StringVar(value=self.admin_url_var.get())
        self.login_user_var = tk.StringVar()
        self.login_pass_var = tk.StringVar()

        main = tk.Frame(self, bg=bg)
        main.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(main, bg=primary, height=130)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header, text="JOIN SERVER",
            font=("Segoe UI", 22, "bold"), fg="white", bg=primary,
        ).pack(anchor="w", padx=65, pady=(28, 4))
        tk.Label(
            header, text="Đăng nhập vào Workspace đã tồn tại",
            font=("Segoe UI", 10), fg="#C8D6E5", bg=primary,
        ).pack(anchor="w", padx=67)

        content = tk.Frame(main, bg=bg)
        content.pack(fill=tk.BOTH, expand=True, padx=65, pady=30)

        form = tk.Frame(
            content, bg="white", highlightbackground="#DCE4ED",
            highlightthickness=1, padx=30, pady=25,
        )
        form.pack(fill=tk.BOTH, expand=True)
        form.columnconfigure(1, weight=1)

        def add_row(row, label, variable, show=None):
            tk.Label(
                form, text=label, font=("Segoe UI", 10, "bold"),
                fg=text_color, bg="white",
            ).grid(row=row, column=0, sticky="w", padx=(0, 20), pady=12)
            entry = ttk.Entry(form, textvariable=variable, font=("Segoe UI", 10), show=show)
            entry.grid(row=row, column=1, sticky="ew", pady=12, ipady=5)
            return entry

        add_row(0, "Server Address *", self.login_server_var)
        add_row(1, "Username *", self.login_user_var)
        add_row(2, "Password *", self.login_pass_var, show="*")

        tk.Label(
            content,
            text="* Nhập địa chỉ Server (ví dụ: http://127.0.0.1:6971) và tài khoản được cấp.",
            font=("Segoe UI", 9), fg=muted, bg=bg,
        ).pack(anchor="w", pady=(10, 0))

        footer = tk.Frame(main, bg=bg)
        footer.pack(fill=tk.X, padx=65, pady=(0, 20))
        ttk.Button(footer, text="BACK", command=self._build_welcome_ui).pack(
            side=tk.LEFT, ipadx=20, ipady=6
        )
        self.join_login_btn = ttk.Button(footer, text="LOGIN", command=self._do_login)
        self.join_login_btn.pack(side=tk.RIGHT, ipadx=30, ipady=6)
        self.login_status_var = tk.StringVar(value="Sẵn sàng đăng nhập")
        ttk.Label(footer, textvariable=self.login_status_var).pack(side=tk.RIGHT, padx=12)

    def _do_login(self):
        server = self.login_server_var.get().strip().rstrip("/")
        username = self.login_user_var.get().strip()
        password = self.login_pass_var.get()

        if not server:
            messagebox.showwarning("Login", "Vui lòng nhập Server Address.")
            return
        if not username:
            messagebox.showwarning("Login", "Vui lòng nhập Username.")
            return
        if not password:
            messagebox.showwarning("Login", "Vui lòng nhập Password.")
            return

        self.admin_url_var.set(server)
        self.admin_user_var.set(username)
        self.admin_pass_var.set(password)

        if getattr(self, "join_login_btn", None) is not None:
            self.join_login_btn.configure(state="disabled")
        if hasattr(self, "login_status_var"):
            self.login_status_var.set("Đang kết nối và xác thực...")
        client = AdminClient(server)

        def worker():
            try:
                result = client.login(username, password)
                me = {
                    "username": result.get("username", username),
                    "role": result.get("role", "user"),
                    "permissions": result.get("permissions", []),
                }
                self._queue.put(("join_login_ok", (client, me)))
            except Exception as exc:
                self._queue.put(("admin_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Dựng giao diện chính
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self._clear_screen()
        self.title("TFTP Pro | Enterprise File Manager")
        self.configure(bg=self.palette["background"])

        # Header thương hiệu: chỉ bổ sung phần trình bày, không can thiệp các tab.
        header = tk.Frame(self, bg=self.palette["navy"], height=76)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=self.palette["navy"])
        brand.pack(side=tk.LEFT, fill=tk.Y, padx=22, pady=12)
        tk.Label(brand, text="TFTP PRO", bg=self.palette["navy"], fg="#FFFFFF",
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(brand, text="ENTERPRISE FILE MANAGEMENT", bg=self.palette["navy"],
                 fg="#B9C9DF", font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(1, 0))
        tk.Label(header, text="TRANSFER  •  MONITOR  •  MANAGE", bg=self.palette["navy"],
                 fg="#D3DEEC", font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=24)

        top = ttk.Frame(self, padding=(16, 12, 16, 8))
        top.pack(fill=tk.X)

        ttk.Label(top, text="TFTP IP:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.host_var, width=18).grid(
            row=0, column=1, padx=5
        )
        ttk.Label(top, text="Port:").grid(row=0, column=2, sticky="w")
        ttk.Entry(top, textvariable=self.port_var, width=8).grid(
            row=0, column=3, padx=5
        )
        ttk.Label(top, text="Window:").grid(row=0, column=4, sticky="w")
        self.windowsize_spin = ttk.Spinbox(
            top,
            from_=1,
            to=DEFAULT_MAX_WINDOWSIZE,
            textvariable=self.windowsize_var,
            width=5,
        )
        self.windowsize_spin.grid(row=0, column=5, padx=5)
        self.save_btn = ttk.Button(
            top, text="Lưu cấu hình", command=self._save_config
        )
        self.save_btn.grid(row=0, column=6, padx=5)

        admin_bar = ttk.LabelFrame(
            self, text="TÀI KHOẢN & KẾT NỐI QUẢN TRỊ", padding=8
        )
        admin_bar.pack(fill=tk.X, padx=16, pady=(0, 8))
        if self.admin is not None and self.admin_user:
            # Workspace owner / joined user is already authenticated. Do not
            # show a second username/password form inside the main dashboard.
            ttk.Label(
                admin_bar,
                text=f"Đã đăng nhập: {self.admin_user.get('username', '')} "
                     f"({self.admin_user.get('role', 'UNKNOWN')})",
            ).pack(side=tk.LEFT, padx=6)
            ttk.Label(
                admin_bar, text=f"API: {self.admin_url_var.get()}",
                foreground=self.palette["muted"],
            ).pack(side=tk.LEFT, padx=12)
            self.admin_login_btn = None
            self.admin_logout_btn = ttk.Button(
                admin_bar, text="Đăng xuất", command=self._admin_logout
            )
            self.admin_logout_btn.pack(side=tk.RIGHT, padx=3)
            self.admin_status_var.set(
                f"Đã kết nối · {self.admin_user.get('role', 'UNKNOWN')}"
            )
        else:
            ttk.Label(admin_bar, text="API URL:").pack(side=tk.LEFT)
            ttk.Entry(admin_bar, textvariable=self.admin_url_var, width=26).pack(
                side=tk.LEFT, padx=4
            )
            ttk.Label(admin_bar, text="User:").pack(side=tk.LEFT)
            ttk.Entry(admin_bar, textvariable=self.admin_user_var, width=12).pack(
                side=tk.LEFT, padx=4
            )
            ttk.Label(admin_bar, text="Password:").pack(side=tk.LEFT)
            ttk.Entry(
                admin_bar, textvariable=self.admin_pass_var, width=14, show="*"
            ).pack(side=tk.LEFT, padx=4)
            self.admin_login_btn = ttk.Button(
                admin_bar, text="Đăng nhập", command=self._admin_login
            )
            self.admin_login_btn.pack(side=tk.LEFT, padx=3)
            self.admin_logout_btn = ttk.Button(
                admin_bar, text="Đăng xuất", command=self._admin_logout,
                state="disabled",
            )
            self.admin_logout_btn.pack(side=tk.LEFT, padx=3)
            ttk.Label(admin_bar, textvariable=self.admin_status_var).pack(
                side=tk.LEFT, padx=8
            )

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 14))
        self._build_transfer_tab()
        self._build_file_manager_tab()
        self._build_dashboard_tab()
        self._build_packet_tab()
        self._build_users_tab()
        self._build_employees_tab()
        self._build_pxe_tab()

        self._hide_remote_field()

    def _build_transfer_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Truyền file")

        transfer = ttk.LabelFrame(tab, text="Truyền file TFTP", padding=8)
        transfer.pack(fill=tk.X, pady=(0, 5))

        self.remote_label = ttk.Label(
            transfer, text="Tên file trên Server:"
        )
        self.remote_label.grid(row=0, column=0, sticky="w")
        self.remote_combo = ttk.Combobox(
            transfer, textvariable=self.remote_var, width=38, state="normal"
        )
        self.remote_combo.grid(row=0, column=1, padx=5)
        self.remote_combo.bind("<FocusIn>", self._on_remote_focus)

        self.refresh_btn = ttk.Button(
            transfer, text="Làm mới", command=self.on_refresh_files
        )
        self.refresh_btn.grid(row=0, column=2, padx=4)
        self.upload_btn = ttk.Button(
            transfer, text="Upload", command=self.on_upload
        )
        self.upload_btn.grid(row=0, column=3, padx=4)
        self.download_btn = ttk.Button(
            transfer, text="Download", command=self.on_download
        )
        self.download_btn.grid(row=0, column=4, padx=4)
        self.cancel_btn = ttk.Button(
            transfer,
            text="Cancel",
            command=self._cancel.set,
            state="disabled",
        )
        self.cancel_btn.grid(row=0, column=5, padx=4)

        self.history_hint = ttk.Label(
            transfer,
            text="Danh sách lấy từ dịch vụ listing cũ (TCP port+1). "
            "Quản trị file đầy đủ nằm ở tab File Manager.",
        )
        self.history_hint.grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0)
        )

        prog = ttk.LabelFrame(tab, text="Tiến trình truyền", padding=8)
        prog.pack(fill=tk.X, pady=5)
        self.progress = ttk.Progressbar(
            prog, orient="horizontal", mode="determinate", maximum=100
        )
        self.progress.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="Sẵn sàng")
        ttk.Label(prog, textvariable=self.status_var).pack(
            anchor="w", pady=(5, 0)
        )
        self.detail_var = tk.StringVar(value="Tốc độ: --   |   Còn lại: --")
        ttk.Label(prog, textvariable=self.detail_var).pack(anchor="w")

        log_frame = ttk.LabelFrame(tab, text="Nhật ký", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = tk.Text(log_frame, font=("Consolas", 10), height=12)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scroll.set)

    def _tree(self, parent, columns, headings, widths, *, height=14):
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=height,
        )
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=width, stretch=column in ("path", "name"))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return frame, tree

    def _build_file_manager_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="File Manager")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        path_bar = ttk.Frame(tab)
        path_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(path_bar, text="Thư mục:").pack(side=tk.LEFT)
        ttk.Entry(path_bar, textvariable=self.fm_path_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5
        )
        ttk.Button(
            path_bar, text="Mở", command=lambda: self._refresh_fm(self.fm_path_var.get())
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(path_bar, text="Lên", command=self._fm_up).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(path_bar, text="Làm mới", command=self._refresh_fm).pack(
            side=tk.LEFT, padx=2
        )

        search_bar = ttk.Frame(tab)
        search_bar.grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Label(search_bar, text="Tìm:").pack(side=tk.LEFT)
        ttk.Entry(search_bar, textvariable=self.fm_search_var, width=28).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(search_bar, text="Tìm kiếm", command=self._fm_search).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Label(search_bar, textvariable=self.fm_hash_var).pack(
            side=tk.LEFT, padx=12
        )

        tree_frame, self.fm_tree = self._tree(
            tab,
            ("name", "type", "size", "modified", "path"),
            ("Tên", "Loại", "Dung lượng", "Cập nhật (UTC)", "Đường dẫn"),
            (230, 80, 100, 180, 320),
            height=15,
        )
        tree_frame.grid(row=2, column=0, sticky="nsew")
        self.fm_tree.tag_configure("dir", foreground="#0a6b3a")
        self.fm_tree.bind("<Double-1>", lambda _event: self._fm_enter_selected())

        actions = ttk.Frame(tab)
        actions.grid(row=3, column=0, sticky="ew", pady=6)
        for text, command in (
            ("Upload", self._fm_upload),
            ("Download", self._fm_download),
            ("Tạo thư mục", self._fm_mkdir),
            ("Đổi tên", self._fm_rename),
            ("Xóa", self._fm_delete),
            ("Hash", self._fm_hash),
            ("Verify", self._fm_verify),
        ):
            ttk.Button(actions, text=text, command=command).pack(
                side=tk.LEFT, padx=3
            )

        ttk.Label(
            tab,
            text="Mọi thao tác đi qua Admin API và StorageManager; "
            "path traversal bị từ chối ở server.",
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))

    def _build_dashboard_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Dashboard")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        stats = ttk.LabelFrame(tab, text="Thống kê server", padding=8)
        stats.grid(row=0, column=0, sticky="ew")
        items = [
            ("active_clients", "Client đang hoạt động"),
            ("total_transfers", "Tổng phiên"),
            ("successful", "Thành công"),
            ("failed", "Thất bại"),
            ("uploads", "Upload"),
            ("downloads", "Download"),
            ("bytes_upload", "Dung lượng upload"),
            ("bytes_download", "Dung lượng download"),
            ("avg_speed_bytes_per_second", "Tốc độ trung bình"),
            ("timeouts", "Timeout"),
            ("retries", "Retry"),
        ]
        for index, (key, label) in enumerate(items):
            row, column = divmod(index, 4)
            cell = ttk.Frame(stats)
            cell.grid(row=row, column=column, sticky="w", padx=10, pady=5)
            ttk.Label(cell, text=label + ":").pack(anchor="w")
            ttk.Label(
                cell,
                textvariable=self._dash_vars[key],
                font=("Segoe UI", 12, "bold"),
            ).pack(anchor="w")

        controls = ttk.Frame(tab)
        controls.grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Button(
            controls, text="Làm mới Dashboard", command=self._refresh_dashboard
        ).pack(side=tk.LEFT)
        ttk.Label(
            controls,
            text="Dữ liệu lấy từ Admin API (metrics SQLite), không đọc DB trực tiếp.",
        ).pack(side=tk.LEFT, padx=10)

        tree_frame, self.dash_tree = self._tree(
            tab,
            (
                "timestamp",
                "filename",
                "direction",
                "bytes",
                "duration",
                "retries",
                "timeouts",
                "status",
                "error",
            ),
            (
                "Thời gian",
                "File",
                "Chiều",
                "Bytes",
                "Giây",
                "Retry",
                "Timeout",
                "Trạng thái",
                "Lỗi",
            ),
            (145, 230, 90, 85, 65, 55, 65, 80, 180),
            height=15,
        )
        tree_frame.grid(row=2, column=0, sticky="nsew")

    def _build_packet_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Packet Analyzer")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        filters = ttk.Frame(tab)
        filters.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(filters, text="Opcode:").pack(side=tk.LEFT)
        ttk.Combobox(
            filters,
            textvariable=self.packet_opcode_var,
            values=("Tất cả", "RRQ", "WRQ", "OACK", "DATA", "ACK", "ERROR"),
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(filters, text="Chiều:").pack(side=tk.LEFT)
        ttk.Combobox(
            filters,
            textvariable=self.packet_direction_var,
            values=("Tất cả", "Client -> Server", "Server -> Client"),
            width=18,
            state="readonly",
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(filters, text="Giới hạn:").pack(side=tk.LEFT)
        ttk.Spinbox(
            filters,
            from_=10,
            to=2000,
            increment=50,
            textvariable=self.packet_limit_var,
            width=7,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            filters, text="Làm mới / Lọc", command=self._refresh_packets
        ).pack(side=tk.LEFT, padx=5)
        ttk.Label(filters, textvariable=self.packet_summary_var).pack(
            side=tk.LEFT, padx=12
        )

        tree_frame, self.packet_tree = self._tree(
            tab,
            (
                "timestamp",
                "source",
                "destination",
                "opcode",
                "block",
                "bytes",
                "direction",
            ),
            (
                "Timestamp",
                "Source",
                "Destination",
                "Opcode",
                "Block",
                "Payload",
                "Direction",
            ),
            (150, 180, 180, 75, 65, 75, 150),
            height=17,
        )
        tree_frame.grid(row=2, column=0, sticky="nsew")
        ttk.Label(
            tab,
            text="Đây là trace của chính ứng dụng (RRQ/OACK/DATA/ACK), "
            "không yêu cầu quyền packet capture của Windows.",
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))

    def _build_users_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Người dùng")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        create_bar = ttk.LabelFrame(tab, text="Tạo tài khoản", padding=6)
        create_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(create_bar, text="Username:").pack(side=tk.LEFT)
        ttk.Entry(
            create_bar, textvariable=self.user_username_var, width=16
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(create_bar, text="Password:").pack(side=tk.LEFT)
        ttk.Entry(
            create_bar,
            textvariable=self.user_password_var,
            width=16,
            show="*",
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(create_bar, text="Role:").pack(side=tk.LEFT)
        ttk.Combobox(
            create_bar,
            textvariable=self.user_new_role_var,
            values=("ADMIN", "HR", "MANAGER", "TEAM_LEADER", "EMPLOYEE", "INTERN"),
            width=9,
            state="readonly",
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            create_bar, text="Tạo", command=self._user_create
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            create_bar, text="Làm mới", command=self._refresh_users
        ).pack(side=tk.LEFT, padx=4)

        tree_frame, self.users_tree = self._tree(
            tab,
            ("username", "role", "active", "created_at"),
            ("Username", "Role", "Active", "Tạo lúc"),
            (180, 100, 80, 220),
            height=13,
        )
        tree_frame.grid(row=1, column=0, sticky="nsew")

        actions = ttk.Frame(tab)
        actions.grid(row=2, column=0, sticky="ew", pady=6)
        ttk.Label(actions, text="Role mới:").pack(side=tk.LEFT)
        ttk.Combobox(
            actions,
            textvariable=self.user_edit_role_var,
            values=("ADMIN", "HR", "MANAGER", "TEAM_LEADER", "EMPLOYEE", "INTERN"),
            width=9,
            state="readonly",
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            actions, text="Đổi role", command=self._user_set_role
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            actions, text="Bật / Tắt", command=self._user_toggle_active
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            actions, text="Đặt lại mật khẩu", command=self._user_reset_password
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            actions, text="Xóa", command=self._user_delete
        ).pack(side=tk.LEFT, padx=2)

    def _build_pxe_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="PXE")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        form = ttk.LabelFrame(tab, text="Boot profile", padding=6)
        form.grid(row=0, column=0, sticky="ew")
        for column, (label, variable) in enumerate(
            (
                ("Tên:", self.pxe_name_var),
                ("Mô tả:", self.pxe_description_var),
                ("BIOS file:", self.pxe_bios_var),
                ("UEFI file:", self.pxe_uefi_var),
            )
        ):
            ttk.Label(form, text=label).grid(
                row=0, column=column * 2, sticky="w", padx=(0, 3)
            )
            ttk.Entry(form, textvariable=variable, width=24).grid(
                row=0, column=column * 2 + 1, sticky="we", padx=(0, 8)
            )
        ttk.Checkbutton(
            form, text="Mặc định", variable=self.pxe_default_var
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(
            form,
            text="Bắt buộc file tồn tại",
            variable=self.pxe_require_files_var,
        ).grid(row=1, column=2, columnspan=2, sticky="w")
        ttk.Button(form, text="Lưu profile", command=self._pxe_save).grid(
            row=1, column=4, padx=3
        )
        ttk.Button(form, text="Xóa", command=self._pxe_delete).grid(
            row=1, column=5, padx=3
        )
        ttk.Button(form, text="Làm mới", command=self._refresh_pxe).grid(
            row=1, column=6, padx=3
        )

        select_bar = ttk.Frame(tab)
        select_bar.grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Label(select_bar, text="Kiểm tra boot theo kiến trúc:").pack(
            side=tk.LEFT
        )
        ttk.Combobox(
            select_bar,
            textvariable=self.pxe_arch_var,
            values=("bios", "uefi"),
            width=8,
            state="readonly",
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            select_bar, text="Chọn boot file", command=self._pxe_select
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(select_bar, textvariable=self.pxe_issues_var).pack(
            side=tk.LEFT, padx=12
        )

        tree_frame, self.pxe_tree = self._tree(
            tab,
            ("name", "default", "bios", "uefi", "missing"),
            ("Tên", "Mặc định", "BIOS file", "UEFI file", "Thiếu"),
            (170, 75, 230, 230, 200),
            height=12,
        )
        tree_frame.grid(row=2, column=0, sticky="nsew")
        self.pxe_tree.bind("<<TreeviewSelect>>", lambda _event: self._pxe_load_selected())
        ttk.Label(
            tab,
            text="TFTP Pro không chạy DHCP. DHCP lab độc lập trả next-server/bootfile; "
            "TFTP Server chỉ cung cấp boot file. Xem docs/pxe_lab.md.",
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))

    def _on_close(self):
        if self._server_runtime is not None:
            self._server_runtime.stop()
            self._server_runtime = None
        self.destroy()

    # ------------------------------------------------------------------ #
    # Queue / UI helpers
    # ------------------------------------------------------------------ #
    def _poll_queue(self):
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_log(payload)
            elif kind == "progress":
                self._set_progress(*payload)
            elif kind == "files":
                self._files = payload
                self.remote_combo.configure(values=self._files)
                current = self.remote_var.get().strip()
                if current and current not in self._files:
                    self.remote_var.set("")
                self._append_log(
                    f"Đã cập nhật danh sách từ Server: {len(self._files)} file"
                )
            elif kind == "list_error":
                self._append_log(f"Lỗi lấy danh sách: {payload}")
                messagebox.showerror("Lỗi lấy danh sách", payload)
            elif kind == "list_done":
                self._listing = False
                self.refresh_btn.configure(state="normal")
            elif kind == "refresh_request":
                self.on_refresh_files()
            elif kind == "done":
                self._set_busy(False)
                self.progress["value"] = 100
                self.status_var.set("Hoàn tất - 100%")
                self.detail_var.set("Tốc độ: --   |   Còn lại: 0 giây")
                messagebox.showinfo("Thành công", payload)
            elif kind == "error":
                self._set_busy(False)
                if "cancel" in payload.lower():
                    self.status_var.set("Đã hủy truyền file")
                    self._append_log("Đã hủy truyền file.")
                else:
                    self.status_var.set("Truyền file thất bại")
                    messagebox.showerror("Lỗi", payload)
            elif kind == "admin_login_ok":
                client, user = payload
                self.admin = client
                self.admin_user = user
                self._admin_client_pending = None
                self.admin_login_btn.configure(state="normal")
                self.admin_logout_btn.configure(state="normal")
                self.admin_status_var.set(
                    f"Đăng nhập: {user.get('username')} ({user.get('role')})"
                )
                self._append_log(
                    f"Admin API: đăng nhập {user.get('username')} "
                    f"({user.get('role')})"
                )
                self._refresh_after_login()
            elif kind == "join_login_ok":
                client, user = payload
                if getattr(self, "join_login_btn", None) is not None:
                    self.join_login_btn.configure(state="normal")
                self.admin = client
                self.admin_user = user
                self.admin_status_var.set(
                    f"Đăng nhập: {user.get('username')} ({user.get('role')})"
                )
                messagebox.showinfo(
                    "Login",
                    f"Đăng nhập thành công!\n\n"
                    f"User: {user.get('username')}\n"
                    f"Role: {user.get('role')}",
                )
                self._build_ui()
                self.admin_status_var.set(
                    f"Đăng nhập: {user.get('username')} ({user.get('role')})"
                )
                if hasattr(self, "admin_logout_btn"):
                    self.admin_logout_btn.configure(state="normal")
                self._refresh_after_login()
            elif kind == "admin_error":
                if getattr(self, "admin_login_btn", None) is not None:
                    self.admin_login_btn.configure(state="normal")
                if getattr(self, "join_login_btn", None) is not None:
                    self.join_login_btn.configure(state="normal")
                if hasattr(self, "login_status_var"):
                    self.login_status_var.set(f"Đăng nhập thất bại: {payload}")
                self.admin_status_var.set("Lỗi Admin API")
                self._append_log(f"Admin API lỗi: {payload}")
                messagebox.showerror("Admin API", payload)
            elif kind == "fm_entries":
                self._apply_fm_entries(payload)
            elif kind == "fm_refresh":
                self._refresh_fm()
            elif kind == "users_refresh":
                self._refresh_users()
            elif kind == "pxe_refresh":
                self._refresh_pxe()
            elif kind == "fm_info":
                self._append_log(payload)
                messagebox.showinfo("File Manager", payload)
            elif kind == "fm_hash":
                path, digest = payload
                self.fm_hash_var.set(f"SHA-256 {path}: {digest}")
                self._append_log(f"SHA-256 {path}: {digest}")
            elif kind == "fm_verify":
                path, status, expected, actual = payload
                self.fm_hash_var.set(f"{path}: {status}")
                text = f"{path}\nKết quả: {status}"
                if expected:
                    text += f"\nClient: {expected}\nServer: {actual}"
                self._append_log(text.replace("\n", " | "))
                messagebox.showinfo("SHA-256", text)
            elif kind == "fm_verify_server":
                path, result = payload
                self.fm_hash_var.set(f"{path}: {result.get('status')}")
                text = (
                    f"{path}\nKết quả: {result.get('status')}\n"
                    f"Expected: {result.get('expected')}\n"
                    f"Actual:   {result.get('actual')}"
                )
                self._append_log(text.replace("\n", " | "))
                messagebox.showinfo("SHA-256", text)
            elif kind == "dashboard":
                self._apply_dashboard(payload)
            elif kind == "packets":
                self._apply_packets(payload)
            elif kind == "users":
                self._apply_users(payload)
            elif kind == "employees":
                self._apply_employees(payload)
            elif kind == "pxe":
                self._apply_pxe(payload)
            elif kind == "pxe_selected":
                profile = payload
                exists = profile.get("boot_file_exists")
                messagebox.showinfo(
                    "PXE boot file",
                    f"Profile: {profile.get('name')}\n"
                    f"Kiến trúc: {profile.get('arch')}\n"
                    f"Boot file: {profile.get('boot_file')}\n"
                    f"Tồn tại: {'Có' if exists else 'Không'}\n\n"
                    "Dùng thông tin này trong DHCP lab (next-server + bootfile).",
                )

        self.after(100, self._poll_queue)

    def _append_log(self, msg: str):
        # Login/setup screens do not create the TFTP log widget. Logging must
        # never interrupt queue processing or hide the actual API error.
        widget = getattr(self, "log_text", None)
        if widget is None:
            logger.info("GUI: %s", msg)
            return
        try:
            widget.insert(tk.END, msg + "\n")
            widget.see(tk.END)
        except tk.TclError:
            logger.info("GUI: %s", msg)

    def _set_progress(self, done: int, total: int | None):
        now = time.monotonic()
        elapsed = max(now - self._progress_started, 0.001)

        if total and total > 0:
            pct = min(100, int(done * 100 / total))
            self.progress["value"] = pct
            rate = done / elapsed
            remaining = max(total - done, 0) / rate if rate > 0 else 0
            self.status_var.set(
                f"{pct}% - {self._format_bytes(done)} / {self._format_bytes(total)}"
            )
            self.detail_var.set(
                f"Tốc độ: {self._format_bytes(rate)}/s   |   "
                f"Còn lại: {self._format_time(remaining)}"
            )
        else:
            self.progress["value"] = 0
            delta = max(now - self._last_progress_time, 0.001)
            rate = (
                (done - self._last_progress_bytes) / delta
                if self._last_progress_time
                else 0
            )
            self.status_var.set(
                f"Đã nhận {self._format_bytes(done)} "
                "(chưa biết tổng dung lượng)"
            )
            self.detail_var.set(
                f"Tốc độ: {self._format_bytes(rate)}/s   |   "
                "Còn lại: chưa xác định"
            )

        self._last_progress_time = now
        self._last_progress_bytes = done

    @staticmethod
    def _format_bytes(value: float) -> str:
        amount = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if amount < 1024 or unit == "GB":
                return f"{amount:.1f} {unit}"
            amount /= 1024
        return f"{amount:.1f} GB"

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds} giây"
        if seconds < 3600:
            return f"{seconds // 60} phút {seconds % 60} giây"
        return f"{seconds // 3600} giờ {(seconds % 3600) // 60} phút"

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.upload_btn, self.download_btn):
            button.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def _run_task(self, fn, success_message: str):
        if self._busy:
            return
        self._cancel.clear()
        self.progress["value"] = 0
        self.status_var.set("Đang chuẩn bị truyền file...")
        self.detail_var.set(
            "Tốc độ: đang tính...   |   Còn lại: đang tính..."
        )
        self._progress_started = time.monotonic()
        self._last_progress_time = 0.0
        self._last_progress_bytes = 0
        self._set_busy(True)

        def worker():
            try:
                message = fn()
                self._queue.put(("done", message or success_message))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _save_config(self):
        try:
            data = {
                "host": self.host_var.get().strip(),
                "port": int(self.port_var.get()),
                "windowsize": int(self.windowsize_var.get()),
                "admin_url": self.admin_url_var.get().strip(),
                "admin_user": self.admin_user_var.get().strip(),
            }
            if not 1 <= data["port"] <= 65534:
                raise ValueError("Port TFTP phải nằm trong 1–65534.")
            if not 1 <= data["windowsize"] <= DEFAULT_MAX_WINDOWSIZE:
                raise ValueError("Window Size không hợp lệ.")
            save_config(data)
            messagebox.showinfo("Cấu hình", "Đã lưu cấu hình.")
        except ValueError as exc:
            messagebox.showerror("Cấu hình", str(exc) or "Giá trị không hợp lệ.")

    def _connection(self):
        host = self.host_var.get().strip()
        if not host:
            raise ValueError("Vui lòng nhập IP/Host của server.")
        port = int(self.port_var.get())
        if not 1 <= port <= 65534:
            raise ValueError("Port TFTP phải nằm trong khoảng 1–65534.")
        windowsize = int(self.windowsize_var.get())
        if not 1 <= windowsize <= DEFAULT_MAX_WINDOWSIZE:
            raise ValueError(
                f"Window Size phải nằm trong khoảng 1–{DEFAULT_MAX_WINDOWSIZE}."
            )
        return host, port, windowsize

    # ------------------------------------------------------------------ #
    # Admin API chung
    # ------------------------------------------------------------------ #
    def _admin_login(self):
        url = self.admin_url_var.get().strip().rstrip("/")
        username = self.admin_user_var.get().strip()
        password = self.admin_pass_var.get()
        if not url or not username or not password:
            messagebox.showwarning(
                "Admin API", "Nhập API URL, username và password."
            )
            return
        self.admin_login_btn.configure(state="disabled")
        self.admin_status_var.set("Đang đăng nhập, vui lòng chờ...")
        client = AdminClient(url)

        def worker():
            try:
                result = client.login(username, password)
                me = {
                    "username": result.get("username", username),
                    "role": result.get("role", ""),
                    "permissions": result.get("permissions", []),
                }
                self._queue.put(("admin_login_ok", (client, me)))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("admin_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _admin_logout(self):
        client = self.admin
        self.admin = None
        self.admin_user = None
        self.admin_pass_var.set("")
        self.admin_status_var.set("Chưa đăng nhập Admin API")
        if client is not None:
            threading.Thread(target=self._quiet_logout, args=(client,), daemon=True).start()
        # Rebuild the account strip so the login controls reappear after logout.
        self._build_ui()

    @staticmethod
    def _quiet_logout(client: AdminClient):
        try:
            client.logout()
        except Exception:
            logger.debug("Admin logout failed", exc_info=True)

    def _require_admin(self) -> AdminClient | None:
        if self.admin is None:
            messagebox.showwarning(
                "Admin API", "Hãy đăng nhập Admin API trước."
            )
            return None
        return self.admin

    def _start_admin_task(self, fn, kind: str):
        def worker():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("admin_error", str(exc)))
            else:
                self._queue.put((kind, result))

        threading.Thread(target=worker, daemon=True).start()

    def _start_admin_action(
        self,
        fn,
        *,
        message: str | None = None,
        refresh: str | None = None,
    ):
        """Chạy thao tác ghi rồi thông báo/làm mới trên UI thread."""

        def worker():
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("admin_error", str(exc)))
            else:
                if message:
                    self._queue.put(("fm_info", message))
                if refresh:
                    self._queue.put((refresh, None))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_after_login(self):
        user = self.admin_user or {}
        role = str(user.get("role", "")).upper()
        permissions = set(user.get("permissions") or [])
        # Keep older login payloads compatible, but use Enterprise permissions
        # whenever the server supplies them. Avoid automatic 403 requests for
        # tabs the current role is not allowed to access.
        if not permissions and role in ("SUPER_ADMIN", "ADMIN"):
            permissions = {
                "files.list", "stats.read", "users.manage", "pxe.read",
                "files.read", "files.upload", "files.download",
                "files.delete", "files.mkdir", "files.rename", "pxe.manage",
            }
        if "files.list" in permissions or role in ("SUPER_ADMIN", "ADMIN"):
            self._refresh_fm(self.fm_path_var.get())
        if "stats.read" in permissions:
            self._refresh_dashboard()
            self._refresh_packets()
        if "users.manage" in permissions:
            self._refresh_users()
        if "employees.read" in permissions:
            self._refresh_employees()
        if "pxe.read" in permissions:
            self._refresh_pxe()

    # ------------------------------------------------------------------ #
    # File Manager
    # ------------------------------------------------------------------ #
    def _refresh_fm(self, path: str | None = None):
        client = self.admin
        if client is None:
            return
        target = self.fm_path_var.get() if path is None else path

        def task():
            return client.list_dir(target)

        self._start_admin_task(task, "fm_entries")

    def _apply_fm_entries(self, payload: dict):
        path = str(payload.get("path", ""))
        entries = payload.get("entries", [])
        self.fm_path_var.set(path)
        self.fm_tree.delete(*self.fm_tree.get_children())
        self._fm_by_iid = {}
        for entry in entries:
            relative = str(entry.get("relative_path", ""))
            is_dir = bool(entry.get("is_dir"))
            self._fm_by_iid[relative] = entry
            self.fm_tree.insert(
                "",
                tk.END,
                iid=relative,
                values=(
                    entry.get("name", ""),
                    "Thư mục" if is_dir else "File",
                    "" if is_dir else self._format_bytes(entry.get("size", 0)),
                    entry.get("modified_at", ""),
                    relative,
                ),
                tags=("dir",) if is_dir else (),
            )
        self.fm_hash_var.set(f"{path or '/'}: {len(entries)} mục")

    def _selected_fm(self) -> dict | None:
        selection = self.fm_tree.selection()
        if not selection:
            messagebox.showwarning("File Manager", "Chọn một file/thư mục trước.")
            return None
        return self._fm_by_iid.get(selection[0])

    def _fm_up(self):
        path = self.fm_path_var.get().strip().strip("/")
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        self._refresh_fm(parent)

    def _fm_enter_selected(self):
        entry = self._selected_fm()
        if entry is None:
            return
        if entry.get("is_dir"):
            self._refresh_fm(entry.get("relative_path", ""))
        else:
            self._fm_hash_selected(entry)

    def _fm_search(self):
        client = self._require_admin()
        if client is None:
            return
        needle = self.fm_search_var.get().strip()
        if not needle:
            self._refresh_fm()
            return
        current_path = self.fm_path_var.get()

        def task():
            return client.search(needle)

        def worker():
            try:
                result = task()
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("admin_error", str(exc)))
            else:
                self._queue.put(
                    (
                        "fm_entries",
                        {
                            "path": current_path,
                            "entries": result.get("entries", []),
                        },
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def _fm_upload(self):
        client = self._require_admin()
        if client is None:
            return
        local = filedialog.askopenfilename(title="Chọn file cần upload")
        if not local:
            return
        size = Path(local).stat().st_size
        if size > DEFAULT_MAX_ADMIN_BODY:
            messagebox.showerror(
                "File Manager",
                f"File lớn hơn giới hạn Admin API "
                f"({self._format_bytes(DEFAULT_MAX_ADMIN_BODY)}).",
            )
            return
        remote = join_remote(self.fm_path_var.get(), Path(local).name)

        def worker():
            try:
                data = Path(local).read_bytes()
                client_hash = hashlib.sha256(data).hexdigest()
                result = client.upload(remote, data)
                server_hash = result.get("sha256", "")
                status = "VERIFIED" if server_hash == client_hash else "MISMATCH"
                self._queue.put(
                    (
                        "fm_verify",
                        (remote, status, client_hash, server_hash),
                    )
                )
                self._queue.put(("fm_refresh", None))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("admin_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _fm_download(self):
        client = self._require_admin()
        if client is None:
            return
        entry = self._selected_fm()
        if entry is None:
            return
        if entry.get("is_dir"):
            messagebox.showwarning("File Manager", "Không thể download thư mục.")
            return
        local = filedialog.asksaveasfilename(
            initialfile=Path(entry.get("name", "download.bin")).name,
            title="Lưu file từ Server",
        )
        if not local:
            return
        remote = entry.get("relative_path", "")

        def worker():
            try:
                body, headers = client.download_with_headers(remote)
                Path(local).write_bytes(body)
                lower = {str(key).lower(): value for key, value in headers.items()}
                server_hash = lower.get("x-file-sha256", "")
                client_hash = hashlib.sha256(body).hexdigest()
                status = (
                    "VERIFIED"
                    if server_hash and server_hash == client_hash
                    else "MISMATCH" if server_hash else "UNKNOWN"
                )
                self._queue.put(
                    (
                        "fm_verify",
                        (remote, status, client_hash, server_hash or "--"),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("admin_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _fm_mkdir(self):
        client = self._require_admin()
        if client is None:
            return
        name = simpledialog.askstring(
            "Tạo thư mục", "Tên thư mục (có thể dùng a/b):", parent=self
        )
        if not name:
            return
        target = join_remote(self.fm_path_var.get(), name)

        def task():
            return client.mkdir(target)

        self._start_admin_action(
            task,
            message=f"Đã tạo thư mục {target}",
            refresh="fm_refresh",
        )

    def _fm_rename(self):
        client = self._require_admin()
        if client is None:
            return
        entry = self._selected_fm()
        if entry is None:
            return
        old = entry.get("relative_path", "")
        new = simpledialog.askstring(
            "Đổi tên", "Đường dẫn mới:", initialvalue=old, parent=self
        )
        if not new or new == old:
            return

        def task():
            return client.rename(old, new)

        self._start_admin_action(
            task,
            message=f"Đã đổi tên {old} -> {new}",
            refresh="fm_refresh",
        )

    def _fm_delete(self):
        client = self._require_admin()
        if client is None:
            return
        entry = self._selected_fm()
        if entry is None:
            return
        remote = entry.get("relative_path", "")
        if not messagebox.askyesno(
            "Xóa", f"Xóa {remote}? Thao tác không thể hoàn tác."
        ):
            return
        recursive = bool(entry.get("is_dir"))

        def task():
            return client.delete(remote, recursive=recursive)

        self._start_admin_action(
            task,
            message=f"Đã xóa {remote}",
            refresh="fm_refresh",
        )

    def _fm_hash(self):
        entry = self._selected_fm()
        if entry is None:
            return
        if entry.get("is_dir"):
            messagebox.showwarning("SHA-256", "Chọn một file, không phải thư mục.")
            return
        self._fm_hash_selected(entry)

    def _fm_hash_selected(self, entry: dict):
        client = self._require_admin()
        if client is None:
            return
        path = entry.get("relative_path", "")

        def task():
            return path, client.hash(path)

        self._start_admin_task(task, "fm_hash")

    def _fm_verify(self):
        client = self._require_admin()
        if client is None:
            return
        entry = self._selected_fm()
        if entry is None:
            return
        if entry.get("is_dir"):
            messagebox.showwarning("SHA-256", "Chọn một file, không phải thư mục.")
            return
        expected = simpledialog.askstring(
            "SHA-256",
            "Nhập SHA-256 mong đợi (64 ký tự hex):",
            parent=self,
        )
        if not expected:
            return
        path = entry.get("relative_path", "")

        def task():
            return path, client.verify(path, expected.strip())

        self._start_admin_task(task, "fm_verify_server")

    # ------------------------------------------------------------------ #
    # Dashboard / Packet Analyzer / Users / PXE
    # ------------------------------------------------------------------ #
    def _refresh_dashboard(self):
        client = self.admin
        if client is None:
            return
        self._start_admin_task(lambda: client.dashboard(limit=50), "dashboard")

    def _apply_dashboard(self, payload: dict):
        stats = payload.get("stats", {})
        for key, variable in self._dash_vars.items():
            value = stats.get(key, 0)
            if key.startswith("bytes_") or key.endswith("_per_second"):
                variable.set(self._format_bytes(value))
            else:
                variable.set(str(value))
        self.dash_tree.delete(*self.dash_tree.get_children())
        for item in payload.get("recent", []):
            self.dash_tree.insert(
                "",
                tk.END,
                values=(
                    item.get("timestamp", ""),
                    item.get("filename", ""),
                    item.get("direction", ""),
                    self._format_bytes(item.get("bytes_total", 0)),
                    f"{float(item.get('duration', 0)):.3f}",
                    item.get("retries", 0),
                    item.get("timeouts", 0),
                    item.get("status", ""),
                    item.get("error") or "",
                ),
            )

    def _refresh_packets(self):
        client = self.admin
        if client is None:
            return
        opcode = self.packet_opcode_var.get()
        opcode = None if opcode == "Tất cả" else opcode
        try:
            limit = max(10, min(int(self.packet_limit_var.get()), 2000))
        except ValueError:
            limit = 200
            self.packet_limit_var.set("200")
        self._start_admin_task(
            lambda: client.packets(limit=limit, opcode=opcode), "packets"
        )

    def _apply_packets(self, payload: dict):
        analyzer = PacketAnalyzer.from_api(payload)
        direction = self.packet_direction_var.get()
        packet_filter = PacketFilter(
            direction=None if direction == "Tất cả" else direction
        )
        rows = analyzer.rows(packet_filter=packet_filter, limit=2000)
        summary = analyzer.summary(packet_filter=packet_filter)
        self.packet_summary_var.set(
            f"Tổng {summary['total']} packet | "
            f"DATA bytes {summary['data_bytes']} | "
            f"Retransmit {summary['data_retransmissions']} | "
            + ", ".join(
                f"{name}={count}" for name, count in summary["counts"].items()
            )
        )
        self.packet_tree.delete(*self.packet_tree.get_children())
        for item in rows:
            block = item.get("block_number")
            self.packet_tree.insert(
                "",
                tk.END,
                values=(
                    item.get("timestamp", ""),
                    item.get("source", ""),
                    item.get("destination", ""),
                    item.get("opcode", ""),
                    "" if block is None else block,
                    item.get("payload_length", 0),
                    item.get("direction", ""),
                ),
            )

    def _refresh_users(self):
        client = self.admin
        user = self.admin_user or {}
        role = str(user.get("role", "")).upper()
        permissions = set(user.get("permissions") or [])
        if client is None:
            messagebox.showwarning("Tài khoản", "Bạn chưa đăng nhập Server.")
            return
        if "users.manage" not in permissions and role not in ("SUPER_ADMIN", "ADMIN"):
            messagebox.showwarning("Tài khoản", "Tài khoản không có quyền xem danh sách tài khoản.")
            return
        self._start_admin_task(client.list_users, "users")

    def _build_employees_tab(self):
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="Nhân viên")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(bar, text="Danh sách hồ sơ nhân viên Enterprise").pack(side=tk.LEFT)
        ttk.Button(bar, text="Làm mới", command=self._refresh_employees).pack(side=tk.RIGHT)
        frame, self.employees_tree = self._tree(
            tab,
            ("username", "full_name", "email", "department", "position"),
            ("Tài khoản", "Họ tên", "Email", "Phòng ban", "Chức vụ"),
            (130, 190, 210, 150, 150),
            height=13,
        )
        frame.grid(row=1, column=0, sticky="nsew")

    def _refresh_employees(self):
        client = self.admin
        user = self.admin_user or {}
        role = str(user.get("role", "")).upper()
        permissions = set(user.get("permissions") or [])
        if client is None:
            messagebox.showwarning("Nhân viên", "Bạn chưa đăng nhập Server.")
            return
        if "employees.read" not in permissions and role not in ("SUPER_ADMIN", "ADMIN"):
            messagebox.showwarning("Nhân viên", "Tài khoản không có quyền xem danh sách nhân viên.")
            return
        self._start_admin_task(client.list_employees, "employees")

    def _apply_employees(self, employees):
        self.employees_tree.delete(*self.employees_tree.get_children())
        for index, employee in enumerate(employees):
            self.employees_tree.insert("", tk.END, iid=f"employee-{index}", values=(
                employee.get("username", ""), employee.get("full_name", ""),
                employee.get("email", ""), employee.get("department", ""),
                employee.get("position", ""),
            ))

    def _apply_users(self, users: list[dict]):
        self.users_tree.delete(*self.users_tree.get_children())
        self._users_by_name = {}
        for user in users:
            name = str(user.get("username", ""))
            self._users_by_name[name] = user
            self.users_tree.insert(
                "",
                tk.END,
                iid=name,
                values=(
                    name,
                    user.get("role", ""),
                    "Có" if user.get("active") else "Không",
                    user.get("created_at", ""),
                ),
            )

    def _selected_user(self) -> dict | None:
        selection = self.users_tree.selection()
        if not selection:
            messagebox.showwarning("Người dùng", "Chọn một tài khoản trước.")
            return None
        return self._users_by_name.get(selection[0])

    def _user_create(self):
        client = self._require_admin()
        if client is None:
            return
        username = self.user_username_var.get().strip()
        password = self.user_password_var.get()
        role = self.user_new_role_var.get()
        if not username or not password:
            messagebox.showwarning("Người dùng", "Nhập username và password.")
            return

        def task():
            return client.create_user(username, password, role)

        self._start_admin_action(
            task,
            message=f"Đã tạo người dùng {username} ({role})",
            refresh="users_refresh",
        )
        self.user_password_var.set("")

    def _user_set_role(self):
        client = self._require_admin()
        if client is None:
            return
        user = self._selected_user()
        if user is None:
            return
        role = self.user_edit_role_var.get()

        def task():
            return client.set_user_role(user["username"], role)

        self._start_admin_action(
            task,
            message=f"Đã đổi role {user['username']} -> {role}",
            refresh="users_refresh",
        )

    def _user_toggle_active(self):
        client = self._require_admin()
        if client is None:
            return
        user = self._selected_user()
        if user is None:
            return

        def task():
            return client.set_user_active(
                user["username"], not bool(user.get("active"))
            )

        self._start_admin_action(task, refresh="users_refresh")

    def _user_reset_password(self):
        client = self._require_admin()
        if client is None:
            return
        user = self._selected_user()
        if user is None:
            return
        password = simpledialog.askstring(
            "Đặt lại mật khẩu",
            f"Mật khẩu mới cho {user['username']}:",
            show="*",
            parent=self,
        )
        if not password:
            return

        def task():
            return client.change_password(password, username=user["username"])

        self._start_admin_action(
            task,
            message=f"Đã đặt lại mật khẩu cho {user['username']}",
            refresh="users_refresh",
        )

    def _user_delete(self):
        client = self._require_admin()
        if client is None:
            return
        user = self._selected_user()
        if user is None:
            return
        if not messagebox.askyesno(
            "Xóa người dùng", f"Xóa tài khoản {user['username']}?"
        ):
            return

        def task():
            return client.delete_user(user["username"])

        self._start_admin_action(
            task,
            message=f"Đã xóa người dùng {user['username']}",
            refresh="users_refresh",
        )

    def _refresh_pxe(self):
        client = self.admin
        if client is None:
            return
        self._start_admin_task(client.pxe_overview, "pxe")

    def _apply_pxe(self, payload: dict):
        profiles = payload.get("profiles", [])
        issues = payload.get("issues", [])
        self.pxe_tree.delete(*self.pxe_tree.get_children())
        self._pxe_by_name = {}
        for profile in profiles:
            name = str(profile.get("name", ""))
            self._pxe_by_name[name] = profile
            self.pxe_tree.insert(
                "",
                tk.END,
                iid=name,
                values=(
                    name,
                    "Có" if profile.get("default") else "",
                    profile.get("bios_file") or "",
                    profile.get("uefi_file") or "",
                    ", ".join(profile.get("missing_files", [])),
                ),
            )
        if issues:
            self.pxe_issues_var.set(
                f"{len(issues)} vấn đề: "
                + "; ".join(
                    f"{item.get('name')}: {item.get('error')}" for item in issues[:2]
                )
            )
        else:
            self.pxe_issues_var.set("Boot file cấu hình đầy đủ.")

    def _pxe_load_selected(self):
        selection = self.pxe_tree.selection()
        if not selection:
            return
        profile = self._pxe_by_name.get(selection[0])
        if not profile:
            return
        self.pxe_name_var.set(profile.get("name", ""))
        self.pxe_description_var.set(profile.get("description", ""))
        self.pxe_bios_var.set(profile.get("bios_file") or "")
        self.pxe_uefi_var.set(profile.get("uefi_file") or "")
        self.pxe_default_var.set(bool(profile.get("default")))

    def _pxe_save(self):
        client = self._require_admin()
        if client is None:
            return
        profile = {
            "name": self.pxe_name_var.get().strip(),
            "description": self.pxe_description_var.get().strip(),
            "bios_file": self.pxe_bios_var.get().strip() or None,
            "uefi_file": self.pxe_uefi_var.get().strip() or None,
            "default": self.pxe_default_var.get(),
        }
        require_files = self.pxe_require_files_var.get()

        def task():
            return client.upsert_pxe_profile(
                profile, require_files=require_files
            )

        self._start_admin_action(
            task,
            message=f"Đã lưu PXE profile {profile['name']}",
            refresh="pxe_refresh",
        )

    def _pxe_delete(self):
        client = self._require_admin()
        if client is None:
            return
        selection = self.pxe_tree.selection()
        if not selection:
            messagebox.showwarning("PXE", "Chọn profile cần xóa.")
            return
        name = selection[0]
        if not messagebox.askyesno("PXE", f"Xóa profile {name}?"):
            return

        def task():
            return client.delete_pxe_profile(name)

        self._start_admin_action(
            task,
            message=f"Đã xóa PXE profile {name}",
            refresh="pxe_refresh",
        )

    def _pxe_select(self):
        client = self._require_admin()
        if client is None:
            return
        selection = self.pxe_tree.selection()
        name = selection[0] if selection else None

        def task():
            return client.select_pxe_profile(self.pxe_arch_var.get(), name)

        self._start_admin_task(task, "pxe_selected")

    # ------------------------------------------------------------------ #
    # TFTP transfer (giữ hành vi cũ)
    # ------------------------------------------------------------------ #
    def _on_remote_focus(self, _event=None):
        if self.remote_combo.winfo_viewable() and not self._listing:
            self.on_refresh_files()

    def _hide_remote_field(self):
        self._download_mode = False
        self.remote_label.grid_remove()
        self.remote_combo.grid_remove()
        self.refresh_btn.grid_remove()
        self.history_hint.grid_remove()

    def _show_remote_field(self):
        self._download_mode = True
        self.remote_label.grid()
        self.remote_combo.grid()
        self.refresh_btn.grid()
        self.history_hint.grid()

    def on_refresh_files(self):
        if self._listing:
            return
        try:
            host, port, windowsize = self._connection()
        except ValueError as exc:
            messagebox.showerror("Thông tin kết nối", str(exc))
            return
        self._listing = True
        self.refresh_btn.configure(state="disabled")
        client = TFTPClient(host, port, windowsize=windowsize)

        def worker():
            try:
                files = client.list_files()
                self._queue.put(("files", files))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("list_error", str(exc)))
            finally:
                self._queue.put(("list_done", None))

        threading.Thread(target=worker, daemon=True).start()

    def _integrity_for_files(self, local: str | Path, remote: str) -> str:
        """So hash file local với hash server qua Admin API (nếu đã đăng nhập)."""
        try:
            local_hash = sha256_file(local)
        except OSError as exc:
            return f"Không tính được SHA-256 local: {exc}"
        if self.admin is None:
            return f"{local_hash} (chưa đăng nhập Admin API để đối chiếu)"
        try:
            server_hash = self.admin.hash(remote)
        except Exception as exc:  # noqa: BLE001 - không làm hỏng kết quả truyền
            return f"{local_hash} (không lấy được hash server: {exc})"
        status = "VERIFIED" if local_hash == server_hash else "MISMATCH"
        return f"{status}\nLocal:  {local_hash}\nServer: {server_hash}"

    def on_upload(self):
        self._hide_remote_field()
        local = filedialog.askopenfilename(title="Chọn file cần Upload")
        if not local:
            return
        remote = Path(local).name
        try:
            host, port, windowsize = self._connection()
        except ValueError as exc:
            messagebox.showerror("Thông tin kết nối", str(exc))
            return
        client = TFTPClient(
            host,
            port,
            windowsize=windowsize,
            on_log=lambda msg: self._queue.put(("log", msg)),
        )

        def task():
            client.upload(
                local,
                remote,
                on_progress=lambda done, total: self._queue.put(
                    ("progress", (done, total))
                ),
                cancel=self._cancel,
            )
            self._queue.put(("log", f"Upload thành công: {remote}"))
            self._queue.put(("refresh_request", None))
            integrity = self._integrity_for_files(local, remote)
            return f"Đã upload {local} -> {remote}\n\nSHA-256: {integrity}"

        self._run_task(task, f"Đã upload {local} -> {remote}")

    def on_download(self):
        if not self._download_mode:
            self._show_remote_field()
            self.remote_var.set("")
            self.on_refresh_files()
            self._append_log(
                "Chọn file trong danh sách rồi nhấn Download lần nữa."
            )
            return
        if self._listing:
            messagebox.showinfo(
                "Đang tải danh sách",
                "Vui lòng đợi Server trả về danh sách file.",
            )
            return
        remote = self.remote_var.get().strip()
        if not remote:
            messagebox.showwarning(
                "Chọn file", "Hãy chọn tên file trong danh sách Server trước."
            )
            return
        local = filedialog.asksaveasfilename(
            initialfile=Path(remote).name, title="Lưu file tải xuống"
        )
        if not local:
            return
        try:
            host, port, windowsize = self._connection()
        except ValueError as exc:
            messagebox.showerror("Thông tin kết nối", str(exc))
            return
        client = TFTPClient(
            host,
            port,
            windowsize=windowsize,
            on_log=lambda msg: self._queue.put(("log", msg)),
        )

        def task():
            client.download(
                remote,
                local,
                on_progress=lambda done, total: self._queue.put(
                    ("progress", (done, total))
                ),
                cancel=self._cancel,
            )
            self._queue.put(("log", f"Download thành công: {remote}"))
            self._queue.put(("refresh_request", None))
            integrity = self._integrity_for_files(local, remote)
            return f"Đã tải {remote} về {local}\n\nSHA-256: {integrity}"

        self._run_task(task, f"Đã tải {remote} về {local}")

    def destroy(self):
        self._cancel.set()
        super().destroy()


def main(argv=None) -> int:
    app = TFTPApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
