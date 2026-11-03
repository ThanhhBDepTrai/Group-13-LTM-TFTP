"""REST API quản trị bằng FastAPI.

Lớp này chỉ là phần keo mỏng: toàn bộ logic nằm ở ``StorageManager``,
``AuthService``, ``Database``, ``MetricsService`` và ``PXEManager`` (đã có test
riêng). Cố ý KHÔNG dùng ``from __future__ import annotations`` để FastAPI đọc
được kiểu của các tham số.

Phân quyền (xem ``auth.ROLE_PERMISSIONS``):
    admin: tất cả | user: xem + tạo thư mục + đổi tên + xem lịch sử | guest: chỉ xem file
"""

from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from auth import AuthError, AuthService, PermissionDenied, User
from database import Database
from metrics import MetricsService
from pxe_config import PXEError, PXEManager
from storage_manager import StorageError, StorageManager

__all__ = ["create_app"]

_STORAGE_STATUS = {"not_found": 404, "exists": 409, "forbidden": 403, "invalid": 400}
_BEARER_HEADER = {"WWW-Authenticate": "Bearer"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    role: Optional[str] = None
    disabled: Optional[bool] = None
    password: Optional[str] = None


class MkdirRequest(BaseModel):
    path: str
    parents: bool = False


class RenameRequest(BaseModel):
    source: str
    destination: str
    overwrite: bool = False


class PXEUpdate(BaseModel):
    enabled: Optional[bool] = None
    boot_dir: Optional[str] = None
    boot_files: Optional[Dict[str, str]] = None
    next_server: Optional[str] = None


def _user_out(user: User) -> Dict[str, Any]:
    return {"id": user.id, "username": user.username, "role": user.role, "disabled": user.disabled}


def create_app(
    *,
    storage: StorageManager,
    db: Database,
    auth: AuthService,
    metrics: MetricsService,
    pxe: PXEManager,
    title: str = "TFTP Pro Admin API",
) -> FastAPI:
    app = FastAPI(title=title, version="1.0.0")
    bearer = HTTPBearer(auto_error=False)

    # ---------------------------- phụ thuộc ---------------------------- #

    def current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> User:
        if credentials is None:
            raise HTTPException(401, "Not authenticated", headers=_BEARER_HEADER)
        try:
            return auth.user_from_token(credentials.credentials)
        except AuthError as exc:
            raise HTTPException(401, str(exc), headers=_BEARER_HEADER)

    def needs(permission: str) -> Callable[..., User]:
        def dependency(user: User = Depends(current_user)) -> User:
            try:
                auth.require(user, permission)
            except PermissionDenied as exc:
                raise HTTPException(403, str(exc))
            return user

        return dependency

    def storage_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except StorageError as exc:
            raise HTTPException(_STORAGE_STATUS.get(exc.kind, 400), exc.message)

    def auth_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except AuthError as exc:
            code = 429 if str(exc).startswith("Too many") else 400
            raise HTTPException(code, str(exc))

    # ------------------------------ chung ------------------------------ #

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/login")
    def login(body: LoginRequest) -> Dict[str, Any]:
        try:
            token, user = auth.login(body.username, body.password)
        except AuthError as exc:
            code = 429 if str(exc).startswith("Too many") else 401
            raise HTTPException(code, str(exc), headers=_BEARER_HEADER if code == 401 else None)
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": auth.token_ttl,
            "user": _user_out(user),
        }

    @app.get("/auth/me")
    def me(user: User = Depends(current_user)) -> Dict[str, Any]:
        return _user_out(user)

    # ---------------------------- người dùng --------------------------- #

    @app.get("/users")
    def list_users(_: User = Depends(needs("users:manage"))) -> List[Dict[str, Any]]:
        return db.list_users()

    @app.post("/users", status_code=201)
    def create_user(body: UserCreate, _: User = Depends(needs("users:manage"))) -> Dict[str, Any]:
        return _user_out(auth_call(auth.create_user, body.username, body.password, body.role))

    @app.patch("/users/{user_id}")
    def update_user(
        user_id: int, body: UserUpdate, _: User = Depends(needs("users:manage"))
    ) -> Dict[str, Any]:
        return _user_out(
            auth_call(
                auth.update_user, user_id,
                role=body.role, disabled=body.disabled, password=body.password,
            )
        )

    @app.delete("/users/{user_id}", status_code=204)
    def delete_user(user_id: int, _: User = Depends(needs("users:manage"))) -> None:
        auth_call(auth.delete_user, user_id)

    # ------------------------------- file ------------------------------ #

    @app.get("/files")
    def list_files(
        path: str = "", _: User = Depends(needs("files:read"))
    ) -> Dict[str, Any]:
        return {"path": path, "entries": storage_call(storage.list_dir, path)}

    @app.get("/files/info")
    def file_info(path: str, _: User = Depends(needs("files:read"))) -> Dict[str, Any]:
        return storage_call(storage.stat, path)

    @app.get("/files/sha256")
    def file_sha256(path: str, _: User = Depends(needs("files:read"))) -> Dict[str, str]:
        return {"path": path, "sha256": storage_call(storage.sha256, path)}

    @app.post("/files/mkdir", status_code=201)
    def mkdir(body: MkdirRequest, _: User = Depends(needs("files:write"))) -> Dict[str, Any]:
        return storage_call(storage.mkdir, body.path, parents=body.parents)

    @app.post("/files/rename")
    def rename(body: RenameRequest, _: User = Depends(needs("files:write"))) -> Dict[str, Any]:
        return storage_call(
            storage.rename, body.source, body.destination, overwrite=body.overwrite
        )

    @app.delete("/files", status_code=204)
    def delete_file(
        path: str, recursive: bool = False, _: User = Depends(needs("files:delete"))
    ) -> None:
        storage_call(storage.delete, path, recursive=recursive)

    @app.get("/storage/usage")
    def usage(_: User = Depends(needs("files:read"))) -> Dict[str, int]:
        return storage.usage()

    # ------------------------- lịch sử & metrics ------------------------ #

    @app.get("/transfers")
    def transfers(
        limit: int = Query(50, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        status: Optional[str] = None,
        op: Optional[str] = None,
        filename: Optional[str] = None,
        _: User = Depends(needs("transfers:read")),
    ) -> Dict[str, Any]:
        rows = db.list_transfers(limit=limit, offset=offset, status=status, op=op, filename=filename)
        return {"items": rows, "limit": limit, "offset": offset}

    @app.get("/transfers/summary")
    def transfer_summary(_: User = Depends(needs("transfers:read"))) -> Dict[str, Any]:
        return db.transfer_summary()

    @app.get("/metrics")
    def get_metrics(_: User = Depends(needs("metrics:read"))) -> Dict[str, Any]:
        return metrics.snapshot()

    @app.get("/metrics/prometheus", response_class=PlainTextResponse)
    def get_prometheus(_: User = Depends(needs("metrics:read"))) -> str:
        return metrics.to_prometheus()

    # -------------------------------- PXE ------------------------------- #

    @app.get("/pxe")
    def get_pxe(_: User = Depends(needs("pxe:read"))) -> Dict[str, Any]:
        return {"config": pxe.config.to_dict(), "problems": pxe.validate()}

    @app.put("/pxe")
    def put_pxe(body: PXEUpdate, _: User = Depends(needs("pxe:write"))) -> Dict[str, Any]:
        data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        changes = {k: v for k, v in data.items() if v is not None}
        try:
            config = pxe.update(**changes)
        except PXEError as exc:
            raise HTTPException(400, str(exc))
        return {"config": config.to_dict(), "problems": pxe.validate()}

    @app.get("/pxe/files")
    def pxe_files(_: User = Depends(needs("pxe:read"))) -> List[Dict[str, Any]]:
        return pxe.list_boot_files()

    @app.get("/pxe/dhcp", response_class=PlainTextResponse)
    def pxe_dhcp(fmt: str = "dnsmasq", _: User = Depends(needs("pxe:read"))) -> str:
        try:
            return pxe.dhcp_snippet(fmt)
        except PXEError as exc:
            raise HTTPException(400, str(exc))

    return app
