"""Enterprise File Manager: màn hình khởi động (Create Server / Join Server / Đăng nhập).

Luồng theo thiết kế: chưa có Server -> Super Admin chọn Create Server, nhập IP/Port ->
kiểm tra -> sinh Server ID -> tạo Workspace + Database -> tạo Super Admin -> Server chạy.
Đã có Server -> chọn Server -> Username/Password -> mở giao diện theo quyền.
"""

from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from client.login_manager import LoginManager
from client.server_connection import ServerConnection
from client.server_registry import DEFAULT_PATH, ServerRegistry
from client.session_manager import SessionManager
from enterprise.errors import EnterpriseError
from enterprise.server_runtime import ServerRuntime

DATA_ROOT = DEFAULT_PATH.parent / "servers"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "server"


class Dialog(tk.Toplevel):
    def __init__(self, master, title, fields, on_submit, *, secret=()):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.vars = {}
        for row, (key, label, default) in enumerate(fields):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            var = tk.StringVar(value=str(default))
            ttk.Entry(self, textvariable=var, width=32,
                      show="*" if key in secret else "").grid(row=row, column=1, padx=8)
            self.vars[key] = var
        ttk.Button(self, text="OK", command=lambda: self._ok(on_submit)).grid(
            row=len(fields), column=1, sticky="e", padx=8, pady=8)

    def _ok(self, on_submit):
        try:
            on_submit({k: v.get().strip() for k, v in self.vars.items()})
        except EnterpriseError as exc:
            messagebox.showerror("Lỗi", exc.message, parent=self)
            return
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Enterprise File Manager")
        self.geometry("620x360")
        self.registry = ServerRegistry()
        self.sessions = SessionManager()
        self.runtimes: dict[str, ServerRuntime] = {}
        self.tree = ttk.Treeview(self, columns=("name", "addr", "id"), show="headings", height=9)
        for col, text, w in (("name", "Server", 180), ("addr", "Địa chỉ API", 180),
                             ("id", "Server ID", 180)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w)
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        for text, cmd in (("Create Server", self.create_server), ("Join Server", self.join_server),
                          ("Đăng nhập", self.login), ("Xóa khỏi danh sách", self.remove)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=4)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        if not self.registry.list():
            self.after(200, self.create_server)  # chưa có Server -> gợi ý tạo

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for e in self.registry.list():
            self.tree.insert("", "end", iid=e["server_id"],
                             values=(e["name"], f"{e['host']}:{e['api_port']}", e["server_id"]))

    def selected(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Chọn Server", "Hãy chọn một Server trong danh sách.")
            return None
        return sel[0]

    def create_server(self):
        fields = [("name", "Tên công ty / Server", ""), ("host", "IP Server", "127.0.0.1"),
                  ("api_port", "API Port", "6971"), ("tftp_port", "TFTP Port", "6969"),
                  ("user", "Super Admin", "superadmin"), ("password", "Mật khẩu (>=8 ký tự)", ""),
                  ("password2", "Nhập lại mật khẩu", "")]

        def submit(v):
            if v["password"] != v["password2"]:
                raise EnterpriseError("Mật khẩu nhập lại không khớp")
            rt = ServerRuntime(DATA_ROOT / _slug(v["name"]))
            cfg = rt.create(v["name"], v["host"], v["api_port"], v["tftp_port"], v["user"],
                            v["password"])  # kiểm tra IP/Port -> ID -> Workspace/DB -> Super Admin
            self.runtimes[cfg.server_id] = rt
            self.registry.add(cfg.server_id, cfg.name, cfg.host, cfg.api_port, cfg.tftp_port)
            self.refresh()
            messagebox.showinfo("Đã tạo Server", f"Server ID: {cfg.server_id}\nĐang chạy tại "
                                f"{cfg.host}:{cfg.api_port} (TFTP {cfg.tftp_port})")

        Dialog(self, "Create Server", fields, submit, secret=("password", "password2"))

    def join_server(self):
        fields = [("host", "IP Server", ""), ("api_port", "API Port", "6971"),
                  ("code", "Mã tham gia", ""), ("user", "Username mới", ""),
                  ("password", "Mật khẩu (>=8 ký tự)", "")]

        def submit(v):
            conn = ServerConnection({"host": v["host"], "api_port": int(v["api_port"] or 0),
                                     "server_id": ""})
            info = conn.request("GET", "/api/health")
            sid = info.get("server_id")
            if not sid:
                raise EnterpriseError("Địa chỉ này không phải Enterprise File Manager Server")
            conn.request("POST", "/api/ent/join", {"code": v["code"], "username": v["user"],
                                                   "password": v["password"]})
            self.registry.add(sid, v["host"], v["host"], v["api_port"], 6969)
            self.refresh()

        Dialog(self, "Join Server", fields, submit, secret=("password",))

    def login(self):
        sid = self.selected()
        if not sid:
            return

        def submit(v):
            session = LoginManager(self.registry, self.sessions).login(
                sid, v["user"], v["password"])
            self.open_workspace(session)

        Dialog(self, "Đăng nhập", [("user", "Username", ""), ("password", "Password", "")],
               submit, secret=("password",))

    def open_workspace(self, session):
        win = tk.Toplevel(self)
        win.title(f"{session.username} ({session.role})")
        ttk.Label(win, text=f"Server {session.server_id}\nVai trò: {session.role}",
                  padding=10).pack()
        # Giao diện theo quyền: chỉ hiện chức năng mà Permission Manager đã cấp
        features = [("files.list", "File Manager"), ("employees.read", "Nhân sự"),
                    ("tasks.update", "Nhiệm vụ"), ("users.manage", "Tài khoản"),
                    ("audit.read", "Lịch sử hoạt động"), ("server.manage", "Cấu hình Server")]
        for perm, label in features:
            if session.can(perm):
                ttk.Button(win, text=label, width=26,
                           command=lambda l=label: messagebox.showinfo(
                               l, "Màn hình này chưa được xây dựng.")).pack(pady=2)

    def remove(self):
        sid = self.selected()
        if sid and messagebox.askyesno("Xóa", "Chỉ xóa khỏi danh sách trên máy này. Tiếp tục?"):
            self.registry.remove(sid)
            self.refresh()

    def close(self):
        for rt in self.runtimes.values():
            rt.stop()
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
