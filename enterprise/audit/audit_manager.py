"""Ghi và truy vấn lịch sử hoạt động (audit_logs)."""

from __future__ import annotations

from database.database import Database, now_iso
from enterprise.audit.audit_models import AuditEntry


class AuditManager:
    def __init__(self, db: Database):
        self.db = db

    def log(self, actor, action, target=None, detail=None, success=True) -> None:
        with self.db.connect() as c:
            c.execute(
                "INSERT INTO audit_logs(timestamp, actor, action, target, detail, success) "
                "VALUES (?,?,?,?,?,?)",
                (now_iso(), actor, action, target, detail, 1 if success else 0),
            )

    def list(self, *, limit=100, actor=None, action_prefix=None) -> list[AuditEntry]:
        sql, args = "SELECT * FROM audit_logs WHERE 1=1", []
        if actor:
            sql += " AND actor=?"
            args.append(actor)
        if action_prefix:
            sql += " AND action LIKE ?"
            args.append(action_prefix + "%")
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self.db.connect() as c:
            rows = c.execute(sql, args).fetchall()
        return [AuditEntry(r["id"], r["timestamp"], r["actor"], r["action"], r["target"],
                           r["detail"], bool(r["success"])) for r in rows]
