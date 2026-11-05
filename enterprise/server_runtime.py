"""Vòng đời một Server chạy ngay trong ứng dụng (luồng "Create Server" của Super Admin).

Create: kiểm tra IP/Port -> sinh Server ID -> tạo Workspace + Database ->
tạo tài khoản Super Admin -> chạy TFTP + Admin API.
"""

from __future__ import annotations

import threading
from pathlib import Path

from admin_api import AdminAPIServer
from auth import AuthManager
from enterprise.errors import EnterpriseError
from enterprise.server.server_config import ServerConfig as EfmServerConfig
from enterprise.service import EnterpriseService
from metrics import MetricsCollector
from packet_trace import PacketTracer
from tftp_server import ServerConfig as TftpConfig
from tftp_server import TFTPServer

DB_NAME = "efm.db"


class ServerRuntime:
    def __init__(self, data_dir: str | Path, storage_root: str | Path | None = None):
        self.data_dir = Path(data_dir)
        self.files_root = Path(storage_root) if storage_root else self.data_dir / "files"
        self.service: EnterpriseService | None = None
        self.config: EfmServerConfig | None = None
        self._tftp: TFTPServer | None = None
        self._api: AdminAPIServer | None = None
        self._tftp_thread: threading.Thread | None = None

    @property
    def exists(self) -> bool:
        return (self.data_dir / DB_NAME).exists()

    @property
    def running(self) -> bool:
        return self._api is not None

    def create(self, name, host, api_port, tftp_port, admin_user, admin_password,
               *, check_bind=True, auto_start=True, workspace_name=None) -> EfmServerConfig:
        if self.exists:
            raise EnterpriseError("Thư mục dữ liệu này đã có Server", 409)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        svc = EnterpriseService(self.data_dir / DB_NAME, self.files_root)
        try:
            self.config = svc.setup_server(name, host, api_port, tftp_port, admin_user,
                                           admin_password, check_bind=check_bind, workspace_name=workspace_name)
        except EnterpriseError:
            (self.data_dir / DB_NAME).unlink(missing_ok=True)  # không để lại DB nửa vời
            raise
        self.service = svc
        if auto_start:
            self.start()
        return self.config

    def start(self) -> None:
        if self.running:
            return
        if self.service is None:
            if not self.exists:
                raise EnterpriseError("Server chưa được tạo", 404)
            self.service = EnterpriseService(self.data_dir / DB_NAME, self.files_root)
        self.config = self.service.servers.get()
        cfg = self.config
        if cfg is None:
            raise EnterpriseError(
                "Database đã tồn tại nhưng chưa có cấu hình Server hoàn chỉnh. "
                "Hãy sao lưu thư mục dữ liệu trước khi khởi tạo lại.", 409
            )
        metrics = MetricsCollector(self.data_dir / "metrics.db")
        tracer = PacketTracer()
        tftp = TFTPServer(
            TftpConfig(host=cfg.host, port=cfg.tftp_port, root=self.files_root),
            metrics=metrics,
            tracer=tracer,
        )
        tftp.storage.ensure_layout()
        api = AdminAPIServer(tftp.storage, AuthManager(self.data_dir / "legacy_auth.db"),
                             metrics=metrics, tracer=tracer, enterprise=self.service,
                             host=cfg.host, port=cfg.api_port)
        api.start()
        thread = threading.Thread(target=tftp.serve_forever, name="efm-tftp", daemon=True)
        thread.start()
        self._tftp, self._api, self._tftp_thread = tftp, api, thread

    def stop(self) -> None:
        if self._tftp is not None:
            self._tftp.stop()
        if self._api is not None:
            self._api.stop()
        self._tftp = self._api = self._tftp_thread = None
