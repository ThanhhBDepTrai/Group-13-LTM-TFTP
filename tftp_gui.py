"""TFTP Pro GUI (Tkinter)."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
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

CONFIG_PATH = Path(os.getenv("APPDATA", Path.home())) / "TFTPPro" / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"host": "127.0.0.1", "port": 6969}


def save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


class TFTPApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TFTP Pro - RFC 1350 File Transfer")
        self.geometry("780x640")
        self.minsize(720, 560)

        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._busy = False

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
        self.save_btn = ttk.Button(top, text="Lưu cấu hình", command=self._save_config)
        self.save_btn.grid(row=0, column=4, padx=5)

        transfer = ttk.LabelFrame(self, text="Truyền file", padding=10)
        transfer.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(transfer, text="Tên file remote:").grid(row=0, column=0, sticky="w")
        ttk.Entry(transfer, textvariable=self.remote_var, width=40).grid(row=0, column=1, padx=5)
        self.upload_btn = ttk.Button(transfer, text="Upload", command=self.on_upload)
        self.upload_btn.grid(row=0, column=2, padx=5)
        self.download_btn = ttk.Button(transfer, text="Download", command=self.on_download)
        self.download_btn.grid(row=0, column=3, padx=5)
        self.cancel_btn = ttk.Button(transfer, text="Cancel", command=self._cancel.set, state="disabled")
        self.cancel_btn.grid(row=0, column=4, padx=5)

        prog = ttk.Frame(self, padding=10)
        prog.pack(fill=tk.X)
        self.progress = ttk.Progressbar(prog, orient="horizontal", mode="determinate")
        self.progress.pack(fill=tk.X)
        self.status_var = tk.StringVar(value="Sẵn sàng")
        ttk.Label(prog, textvariable=self.status_var).pack(anchor="w", pady=2)

        log_frame = ttk.LabelFrame(self, text="Nhật ký", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.log_text = tk.Text(log_frame, font=("Consolas", 10))
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scroll.set)

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
            elif kind == "done":
                self._set_busy(False)
                messagebox.showinfo("Thành công", payload)
            elif kind == "error":
                self._set_busy(False)
                messagebox.showerror("Lỗi", payload)
        self.after(100, self._poll_queue)

    def _append_log(self, msg: str):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def _set_progress(self, done: int, total: int | None):
        if total:
            pct = int(done * 100 / total) if total else 0
            self.progress["value"] = pct
            self.status_var.set(f"{pct}% - {done:,} / {total:,} bytes")
        else:
            self.progress["value"] = 0
            self.status_var.set(f"{done:,} bytes received")

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.upload_btn, self.download_btn):
            btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def _run_task(self, fn, success_message: str):
        if self._busy:
            return
        self._cancel.clear()
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
            data = {"host": self.host_var.get(), "port": int(self.port_var.get())}
            save_config(data)
            messagebox.showinfo("Cấu hình", "Đã lưu cấu hình.")
        except ValueError:
            messagebox.showerror("Cấu hình", "Port phải là số.")

    def on_upload(self):
        local = filedialog.askopenfilename()
        if not local:
            return
        remote = self.remote_var.get().strip() or Path(local).name
        host = self.host_var.get().strip()
        port = int(self.port_var.get())
        client = TFTPClient(host, port, on_log=lambda m: self._queue.put(("log", m)))

        def task():
            client.upload(local, remote, on_progress=lambda d, t: self._queue.put(("progress", (d, t))), cancel=self._cancel)

        self._run_task(task, f"Đã upload {local} -> {remote}")

    def on_download(self):
        remote = self.remote_var.get().strip()
        if not remote:
            messagebox.showwarning("Thiếu thông tin", "Nhập tên file trên server")
            return
        local = filedialog.asksaveasfilename(initialfile=remote)
        if not local:
            return
        host = self.host_var.get().strip()
        port = int(self.port_var.get())
        client = TFTPClient(host, port, on_log=lambda m: self._queue.put(("log", m)))

        def task():
            client.download(remote, local, on_progress=lambda d, t: self._queue.put(("progress", (d, t))), cancel=self._cancel)

        self._run_task(task, f"Đã tải {remote} về {local}")


def main(argv=None) -> int:
    app = TFTPApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
