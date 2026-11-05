"""Local registry for Workspaces created by this desktop application."""

from __future__ import annotations

import json
import os
from pathlib import Path


class LocalWorkspaceRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def list(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Không đọc được danh sách Workspace: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("workspaces"), list):
            raise TypeError("Tệp danh sách Workspace có định dạng không hợp lệ")
        return [entry for entry in data["workspaces"] if isinstance(entry, dict)]

    def save(self, workspace: dict) -> None:
        required = (
            "workspace_id",
            "workspace_name",
            "company_name",
            "data_dir",
            "storage_location",
            "server_id",
            "api_port",
            "tftp_port",
        )
        if any(not workspace.get(key) for key in required):
            raise ValueError("Thiếu thông tin bắt buộc để lưu Workspace")
        workspaces = {item["workspace_id"]: item for item in self.list()}
        workspaces[str(workspace["workspace_id"])] = {
            key: workspace[key] for key in required
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        try:
            temp_path.write_text(
                json.dumps(
                    {"workspaces": list(workspaces.values())},
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(temp_path, self.path)
        except OSError as exc:
            raise ValueError(f"Không lưu được danh sách Workspace: {exc}") from exc
