"""불량 관리 상태 전이 규칙 (TODO.md 6.3). 허용 전이는 ALLOWED 표 하나로 관리한다.

모든 전이는 조건부 갱신(WHERE id=? AND status=?)으로 하고, 바뀐 행이 0이면 '다른 작업이 먼저 상태를 바꿨습니다'.
전이마다 inspection_events 에 1행을 남긴다.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
from .inspections import defect_types, parse_json, type_name

ALLOWED: dict[tuple[str, str], str] = {
    ("PENDING", "CONFIRM_OK"): "PASS", ("AI_DONE", "CONFIRM_OK"): "PASS",
    ("PENDING", "CONFIRM_DEFECT"): "DEFECT", ("AI_DONE", "CONFIRM_DEFECT"): "DEFECT",
    ("DEFECT", "REWORK_PLAN"): "REWORK",
    ("REWORK", "REWORK_DONE"): "REINSPECT",
    ("REINSPECT", "REINSPECT_OK"): "PASS", ("REINSPECT", "REINSPECT_NG"): "DEFECT",
    ("DEFECT", "REJECT"): "REJECT", ("REWORK", "REJECT"): "REJECT",
}
# 역할별 권한표 (TODO.md 7.3)
ROLES: dict[str, tuple[str, ...]] = {
    "CONFIRM_OK": ("admin", "manager", "inspector"), "CONFIRM_DEFECT": ("admin", "manager", "inspector"),
    "REWORK_PLAN": ("admin", "manager", "worker"), "REWORK_DONE": ("admin", "manager", "worker"),
    "REINSPECT_OK": ("admin", "manager", "inspector"), "REINSPECT_NG": ("admin", "manager", "inspector"),
    "REJECT": ("admin",),
}
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


class WorkflowError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def can(user: dict | None, action: str) -> bool:
    return bool(user) and user["role"] in ROLES.get(action, ())


def actions_for(status: str, user: dict | None) -> set[str]:
    return {a for (s, a) in ALLOWED if s == status and can(user, a)}


def _text(data: dict, key: str, label: str, required: bool = True, limit: int = 2000) -> str | None:
    v = (data.get(key) or "").strip()[:limit]
    if required and not v:
        raise WorkflowError(422, f"{label}을(를) 입력하세요.")
    return v or None


def _defect_type(data: dict) -> str:
    code = (data.get("defect_type") or "").strip()
    if code not in defect_types() or code == "NORMAL":
        raise WorkflowError(422, "결함 유형을 하나 고르세요.")
    return code


def _severity(data: dict, code: str) -> str:
    sev = (data.get("severity") or "").strip()
    if sev in SEVERITIES:
        return sev
    default = defect_types()[code]["severity"]
    return default if default in SEVERITIES else "MEDIUM"


def _agree(it: dict, final_defect: int, code: str | None) -> int | None:
    """AI-검사자 일치 (6.3): 불량 여부가 같고, 불량이면 유형도 같다. AI 판정 전에 확정하면 NULL."""
    if it["ai_status"] != "DONE" or it["ai_is_defect"] is None:
        return None
    if int(it["ai_is_defect"]) != final_defect:
        return 0
    return int(not final_defect or it["ai_defect_type"] == code)


def _latest_reinspect_ai(conn: sqlite3.Connection, inspection_id: int) -> tuple[str | None, str | None]:
    """재검사 사진의 AI 결과 (이벤트 ai_snapshot·image_path 용)."""
    ev = db.one(conn, """SELECT ai_snapshot FROM inspection_events WHERE inspection_id=? AND action='AI_INSPECT'
                         AND ai_snapshot LIKE '%"trigger": "REINSPECT"%' ORDER BY id DESC LIMIT 1""", (inspection_id,))
    img = db.scalar(conn, """SELECT image_path FROM ai_model_results WHERE inspection_id=? AND trigger='REINSPECT'
                             ORDER BY id DESC LIMIT 1""", (inspection_id,))
    return (ev["ai_snapshot"] if ev else None), img


def transition(conn: sqlite3.Connection, inspection_id: int, action: str, actor: dict,
               data: dict[str, Any] | None = None, *, image_path: str | None = None) -> str:
    """전이 실행 → 새 상태. 실패는 WorkflowError."""
    data = data or {}
    it = db.one(conn, "SELECT * FROM inspections WHERE id=?", (inspection_id,))
    if it is None:
        raise WorkflowError(404, "검사 건을 찾을 수 없습니다.")
    it = dict(it)
    if not can(actor, action):
        raise WorkflowError(403, "이 처리를 할 권한이 없습니다.")
    frm = it["status"]
    to = ALLOWED.get((frm, action))
    if to is None:
        raise WorkflowError(409, f"지금 상태({frm})에서는 할 수 없는 처리입니다. 화면을 새로 고치세요.")
    now = db.now_str()
    sets: dict[str, Any] = {}
    comment = None
    snapshot = None
    ev_image = image_path

    if action == "CONFIRM_OK":
        sets.update(final_is_defect=0, defect_type=None, severity=None,
                    finding=_text(data, "finding", "소견", required=False), ai_agree=_agree(it, 0, None), closed_at=now)
        comment = sets["finding"] or "정상 확정"
    elif action == "CONFIRM_DEFECT":
        code = _defect_type(data)
        sets.update(final_is_defect=1, defect_type=code, severity=_severity(data, code),
                    finding=_text(data, "finding", "소견"), ai_agree=_agree(it, 1, code))
        comment = f"{type_name(code)} · {sets['finding']}"
    elif action == "REWORK_PLAN":
        due = (data.get("due_date") or "").strip()[:10]
        if len(due) != 10:
            raise WorkflowError(422, "예정일을 입력하세요.")
        sets.update(cause=_text(data, "cause", "추정 원인"), action_plan=_text(data, "action_plan", "조치 방법"),
                    assignee=_text(data, "assignee", "담당자", limit=60), due_date=due)
        comment = f"{sets['action_plan']} (담당 {sets['assignee']}, 예정 {due})"
    elif action == "REWORK_DONE":
        sets.update(action_result=_text(data, "action_result", "조치 결과"), rework_count=it["rework_count"] + 1)
        comment = sets["action_result"]
    elif action in ("REINSPECT_OK", "REINSPECT_NG"):
        snapshot, re_img = _latest_reinspect_ai(conn, inspection_id)
        ev_image = ev_image or re_img
        if action == "REINSPECT_OK":
            sets.update(closed_at=now)
            comment = _text(data, "finding", "의견", required=False) or "재검사 합격"
        else:
            code = _defect_type(data)
            finding = _text(data, "finding", "소견")
            prev = f"이전: {type_name(it['defect_type'])} · {it['finding'] or '-'}"
            sets.update(defect_type=code, severity=_severity(data, code), finding=finding)
            comment = f"{type_name(code)} · {finding} ({prev})"
    elif action == "REJECT":
        comment = _text(data, "reason", "폐기 사유")
        sets.update(closed_at=now)

    sets["status"] = to
    sets["updated_at"] = now
    cols = ", ".join(f"{k}=?" for k in sets)
    cur = conn.execute(f"UPDATE inspections SET {cols} WHERE id=? AND status=?",
                       [*sets.values(), inspection_id, frm])
    if cur.rowcount == 0:
        raise WorkflowError(409, "다른 작업이 먼저 상태를 바꿨습니다. 화면을 새로 고치세요.")
    db.insert(conn, "inspection_events", {
        "inspection_id": inspection_id, "action": action, "from_status": frm, "to_status": to,
        "comment": (comment or "")[:1000] or None, "image_path": ev_image, "ai_snapshot": snapshot,
        "actor_id": actor["id"], "created_at": now})
    if action in ("CONFIRM_DEFECT", "REWORK_PLAN", "REWORK_DONE", "REINSPECT_OK", "REINSPECT_NG", "REJECT"):
        from . import ai_analysis
        ai_analysis.upsert_case(conn, inspection_id)
    return to


def confirm_all(conn: sqlite3.Connection, upload_log_id: int, actor: dict) -> dict[str, int]:
    """AI 결과대로 일괄 확정 (6.4 ⑥). AI_DONE 건만: 불량 → 불량 확정(유형 = AI 최종 유형), 정상 → 정상 확정.
    검출 없음(유형 없음)은 양품 단정이 아니므로 건너뛰고 검사자가 직접 본다."""
    from .ai_pipeline import finding_draft
    out = {"defect": 0, "ok": 0, "skipped": 0}
    rows = db.all_(conn, "SELECT * FROM inspections WHERE upload_log_id=? AND status='AI_DONE' ORDER BY id",
                   (upload_log_id,))
    for r in rows:
        it = dict(r)
        it["findings"] = parse_json(it.get("ai_findings"), [])
        try:
            if it["ai_is_defect"] and it["ai_defect_type"] and it["ai_defect_type"] != "NORMAL":
                transition(conn, it["id"], "CONFIRM_DEFECT", actor, {
                    "defect_type": it["ai_defect_type"], "finding": finding_draft(it, type_name) or "AI 결과대로 확정"})
                out["defect"] += 1
            elif it["ai_defect_type"] == "NORMAL":
                transition(conn, it["id"], "CONFIRM_OK", actor, {"finding": "AI 결과대로 정상 확정"})
                out["ok"] += 1
            else:
                out["skipped"] += 1
        except WorkflowError:
            out["skipped"] += 1
    return out
