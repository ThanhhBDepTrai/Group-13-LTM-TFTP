"""Route /api/ent/* gắn vào Admin API hiện có (không đụng route cũ)."""

from __future__ import annotations

from enterprise.errors import EnterpriseError


def _perms(svc, role):
    return sorted(svc.permissions.permissions_of(role))


def handle(handler, svc, method: str, path: str, query: dict) -> bool:
    """Trả True nếu route được xử lý."""
    if not path.startswith("/api/ent/"):
        return False
    send = handler._send_json
    sub = path[len("/api/ent"):]

    if sub == "/login" and method == "POST":
        b = handler._json_body()
        ip = handler.client_address[0] if handler.client_address else None
        s = svc.login(str(b.get("username", "")), str(b.get("password", "")), ip)
        s["permissions"] = _perms(svc, s["role"])
        send(200, s)
        return True
    if sub == "/join" and method == "POST":
        b = handler._json_body()
        send(201, svc.join(str(b.get("code", "")), str(b.get("username", "")),
                           str(b.get("password", ""))))
        return True

    header = handler.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise EnterpriseError("Thiếu hoặc sai Authorization header", 401)
    token = header[7:].strip()
    user = svc.authenticate(token)

    if sub == "/logout" and method == "POST":
        svc.accounts.logout(token)
        send(200, {"status": "logged_out"})
    elif sub == "/me" and method == "GET":
        send(200, {**user, "permissions": _perms(svc, user["role"])})
    elif sub == "/users" and method == "GET":
        svc.permissions.require(user, "users.manage")
        send(200, {"users": svc.accounts.list_users()})
    elif sub == "/users" and method == "POST":
        b = handler._json_body()
        send(201, svc.create_user(user, str(b.get("username", "")), str(b.get("password", "")),
                                  str(b.get("role", "EMPLOYEE"))))
    elif sub == "/users/role" and method == "POST":
        b = handler._json_body()
        svc.set_role(user, str(b.get("username", "")), str(b.get("role", "")))
        send(200, {"status": "ok"})
    elif sub == "/guest-access" and method == "GET":
        if user.get("role") not in ("SUPER_ADMIN", "ADMIN"):
            raise EnterpriseError("Chỉ Admin mới được xem quyền tải của Guest", 403)
        path_values = query.get("path", [""])
        path = str(path_values[0]) if path_values else ""
        guest = {"username": "", "role": "GUEST"}
        entries = handler.server.app.storage.list_dir(path)
        send(
            200,
            {
                "path": handler.server.app.storage.clean_relative(path),
                "entries": [
                    {
                        "relative_path": entry.relative_path,
                        "guest_can_download": (
                            svc.acl.can_read_tree(guest, entry.relative_path)
                            if entry.is_dir
                            else svc.acl.check(guest, entry.relative_path, "read")
                        ),
                    }
                    for entry in entries
                ],
            },
        )
    elif sub == "/guest-access" and method == "POST":
        if user.get("role") not in ("SUPER_ADMIN", "ADMIN"):
            raise EnterpriseError("Chỉ Admin mới được cấp quyền tải cho Guest", 403)
        b = handler._json_body()
        path = str(b.get("path", ""))
        if bool(b.get("allowed", True)):
            svc.acl.set(
                path, "role", "GUEST", read=True, write=False, delete=False
            )
        else:
            svc.acl.remove(path, "role", "GUEST")
        svc.audit.log(
            user["username"],
            "guest.file_access",
            path,
            "allowed" if bool(b.get("allowed", True)) else "revoked",
        )
        send(200, {"path": path, "guest_read": bool(b.get("allowed", True))})
    elif sub == "/join-codes" and method == "POST":
        b = handler._json_body()
        code = svc.create_join_code(user, role=str(b.get("role", "EMPLOYEE")),
                                    ttl=int(b.get("ttl", 86400)),
                                    max_uses=int(b.get("max_uses", 1)))
        send(201, {"code": code})
    elif sub == "/employees" and method == "GET":
        svc.permissions.require(user, "employees.read")
        send(200, {"employees": svc.employees.list()})
    elif sub == "/employees" and method == "POST":
        svc.permissions.require(user, "employees.manage")
        b = handler._json_body()
        send(201, svc.employees.create(
            str(b.get("full_name", "")),
            **{k: b.get(k) for k in ("username", "email", "department_id", "position_id",
                                      "manager_id")}))
    elif sub == "/departments" and method in ("GET", "POST"):
        if method == "GET":
            svc.permissions.require(user, "employees.read")
            send(200, {"departments": svc.departments.list()})
        else:
            svc.permissions.require(user, "employees.manage")
            b = handler._json_body()
            send(201, svc.departments.create(str(b.get("name", "")), str(b.get("description", ""))))
    elif sub == "/tasks" and method == "GET":
        send(200, {"tasks": svc.tasks.list_for(user["username"])})
    elif sub == "/tasks" and method == "POST":
        b = handler._json_body()
        send(201, svc.assign_task(user, str(b.get("title", "")), list(b.get("assignees", [])),
                                  description=str(b.get("description", "")),
                                  due_date=b.get("due_date")))
    elif sub == "/tasks/status" and method == "POST":
        b = handler._json_body()
        send(200, svc.update_task_status(user, int(b.get("id", 0)), str(b.get("status", ""))))
    elif sub == "/audit" and method == "GET":
        limit = int((query.get("limit") or ["100"])[0])
        send(200, {"audit": [e.__dict__ for e in svc.list_audit(user, limit=limit)]})
    elif sub == "/transfer/authorize" and method == "POST":
        b = handler._json_body()
        svc.authorize_transfer(user, str(b.get("op", "")).upper(), str(b.get("path", "")))
        send(200, {"allowed": True})
    else:
        raise EnterpriseError("Không tìm thấy endpoint", 404)
    return True
