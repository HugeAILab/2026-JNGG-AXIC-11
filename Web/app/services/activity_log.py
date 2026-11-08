"""활동 로그 기록·조회 (TODO.md 8.7). 기록 지점은 log() 하나로 통일한다. 추가 전용."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from .. import db

LOG_TYPES = ("AUTH", "UPLOAD", "AI", "REPORT", "QUIZ", "SYSTEM")
LEVELS = ("INFO", "WARN", "ERROR")

_pylog = logging.getLogger("weldsight.activity")


def log(
    log_type: str,
    action: str,
    summary: str,
    detail: dict[str, Any] | None = None,
    target: tuple[str, int] | None = None,
    *,
    user_id: int | None = None,
    level: str = "INFO",
    ip: str | None = None,
    conn: sqlite3.Connection | None = None,
    created_at: str | None = None,
) -> int | None:
    """활동 로그 1행 추가. conn 을 주면 그 트랜잭션 안에서 기록한다.

    로그 기록 실패가 본 작업을 망치지 않도록, conn 없이 부를 때는 예외를 삼키고 None 을 돌려준다.
    """
    if log_type not in LOG_TYPES:
        raise ValueError(f"unknown log_type {log_type}")
    if level not in LEVELS:
        raise ValueError(f"unknown level {level}")
    row = {
        "log_type": log_type,
        "action": action,
        "level": level,
        "user_id": user_id,
        "target_type": target[0] if target else None,
        "target_id": target[1] if target else None,
        "summary": summary[:300],
        "detail_json": json.dumps(detail, ensure_ascii=False) if detail is not None else None,
        "ip": ip,
        "created_at": created_at or db.now_str(),
    }
    if conn is not None:
        return db.insert(conn, "activity_logs", row)
    try:
        with db.connect() as c:
            return db.insert(c, "activity_logs", row)
    except Exception:  # noqa: BLE001
        _pylog.exception("activity log write failed: %s %s", log_type, action)
        return None


def recent(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return db.all_(conn, """
        SELECT l.*, u.name AS user_name
          FROM activity_logs l LEFT JOIN users u ON u.id = l.user_id
         ORDER BY l.id DESC LIMIT ?""", (limit,))
