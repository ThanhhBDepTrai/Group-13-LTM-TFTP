"""TFTP Pro GUI (Tkinter)."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tftp_client import TFTPClient

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001, S110
        pass

APP_DIR = Path(os.getenv("APPDATA", Path.home())) / "TFTPPro"
CONFIG_PATH = APP_DIR / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"host": "127.0.0.1", "port": 6969}


def save_config(data: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


class TFTPApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TFTP Pro - RFC 1350 File Transfer")
        self.geometry("820x650")
        self.minsize(740, 570)

        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._busy = False
        self._listing = False
        self._download_mode = False
        self._files: list[str] = []

        self._progress_started = 0.0
        self._last_progress_time = 0.0
        self._last_progress_bytes = 0

        cfg = load_config()
        self.host_var = tk.StringVar(value=cfg.get("host", "127.0.0.1"))
        self.port_var = tk.StringVar(value=str(cfg.get("port", 6969)))
        self.remote_var = tk.StringVar()

        self._build_ui()
        self.after(100, self._poll_queue)

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="IP:").grid(row=0, column=0, sticky="w")
        self.host_entry = ttk.Entry(top, textvariable=self.host_var, width=20)
        self.host_entry.grid(row=0, column=1, padx=5)

        ttk.Label(top, text="Port:").grid(row=0, column=2, sticky="w")
        self.port_entry = ttk.Entry(top, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=3, padx=5)

        self.save_btn = ttk.Button(
            top, text="Lưu cấu hình", command=self._save_config
        )
        self.save_btn.grid(row=0, column=4, padx=5)

        transfer = ttk.LabelFrame(self, text="Truyền file", padding=10)
        transfer.pack(fill=tk.X, padx=10, pady=5)

        self.remote_label = ttk.Label(transfer, text="Tên file trên Server:")
        self.remote_label.grid(row=0, column=0, sticky="w")

        self.remote_combo = ttk.Combobox(
            transfer,
            textvariable=self.remote_var,
            width=34,
            state="normal",
        )
        self.remote_combo.grid(row=0, column=1, padx=5)
        self.remote_combo.bind("<FocusIn>", self._on_remote_focus)

        self.refresh_btn = ttk.Button(
            transfer, text="Làm mới", command=self.on_refresh_files
        )
        self.refresh_btn.grid(row=0, column=2, padx=5)

        self.upload_btn = ttk.Button(
            transfer, text="Upload", command=self.on_upload
        )
        self.upload_btn.grid(row=0, column=3, padx=5)

        self.download_btn = ttk.Button(
            transfer, text="Download", command=self.on_download
        )
        self.download_btn.grid(row=0, column=4, padx=5)

        self.cancel_btn = ttk.Button(
            transfer,
            text="Cancel",
            command=self._cancel.set,
            state="disabled",
        )
        self.cancel_btn.grid(row=0, column=5, padx=5)

        self.history_hint = ttk.Label(
            transfer, text="Danh sách được lấy trực tiếp từ server_storage"
        )
        self.history_hint.grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0)
        )

        self._hide_remote_field()

        prog = ttk.LabelFrame(self, text="Tiến trình truyền", padding=10)
        prog.pack(fill=tk.X, padx=10, pady=5)

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

        log_frame = ttk.LabelFrame(self, text="Nhật ký", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = tk.Text(log_frame, font=("Consolas", 10))
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scroll.set)

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

        self.after(100, self._poll_queue)

    def _append_log(self, msg: str):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

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
        self.detail_var.set("Tốc độ: đang tính...   |   Còn lại: đang tính...")

        self._progress_started = time.monotonic()
        self._last_progress_time = 0.0
        self._last_progress_bytes = 0
        self._set_busy(True)

        def worker():
            try:
                fn()
                self._queue.put(("done", success_message))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _save_config(self):
        try:
            data = {
                "host": self.host_var.get().strip(),
                "port": int(self.port_var.get()),
            }
            save_config(data)
            messagebox.showinfo("Cấu hình", "Đã lưu cấu hình.")
        except ValueError:
            messagebox.showerror("Cấu hình", "Port phải là số.")

    def _connection(self):
        host = self.host_var.get().strip()
        if not host:
            raise ValueError("Vui lòng nhập IP/Host của server.")

        port = int(self.port_var.get())
        if not 1 <= port <= 65534:
            raise ValueError("Port TFTP phải nằm trong khoảng 1–65534.")

        return host, port

    def on_refresh_files(self):
        if self._listing:
            return

        try:
            host, port = self._connection()
        except ValueError as exc:
            messagebox.showerror("Thông tin kết nối", str(exc))
            return

        self._listing = True
        self.refresh_btn.configure(state="disabled")
        client = TFTPClient(host, port)

        def worker():
            try:
                files = client.list_files()
                self._queue.put(("files", files))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("list_error", str(exc)))
            finally:
                self._queue.put(("list_done", None))

        threading.Thread(target=worker, daemon=True).start()

    def on_upload(self):
        self._hide_remote_field()
        local = filedialog.askopenfilename(title="Chọn file cần Upload")
        if not local:
            return

        remote = Path(local).name

        try:
            host, port = self._connection()
        except ValueError as exc:
            messagebox.showerror("Thông tin kết nối", str(exc))
            return

        client = TFTPClient(
            host, port, on_log=lambda msg: self._queue.put(("log", msg))
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

        self._run_task(task, f"Đã upload {local} -> {remote}")

    def on_download(self):
        # Lần bấm đầu: hiện danh sách và lấy file trực tiếp từ Server.
        if not self._download_mode:
            self._show_remote_field()
            self.remote_var.set("")
            self.on_refresh_files()
            self._append_log("Chọn file trong danh sách rồi nhấn Download lần nữa.")
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
                "Chọn file",
                "Hãy chọn tên file trong danh sách Server trước.",
            )
            return

        local = filedialog.asksaveasfilename(
            initialfile=Path(remote).name,
            title="Lưu file tải xuống",
        )
        if not local:
            return

        try:
            host, port = self._connection()
        except ValueError as exc:
            messagebox.showerror("Thông tin kết nối", str(exc))
            return

        client = TFTPClient(
            host, port, on_log=lambda msg: self._queue.put(("log", msg))
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
