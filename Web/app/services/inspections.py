"""검사 목록·상세·불량 대기열 조회 (TODO.md 8.3, 8.4, 5.2 불량 관리). 읽기 전용."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from .. import db, reference

PAGE_SIZE = 20
STATUSES = ("PENDING", "AI_DONE", "DEFECT", "REWORK", "REINSPECT", "PASS", "REJECT")
OPEN_STATUSES = ("DEFECT", "REWORK", "REINSPECT")
FLAG_LABELS = {
    "NO_DETECTION": "검출 없음 — 양품 단정 아님",
    "AB_DIFFER": "A·B 판정 다름",
    "B_UNAVAILABLE": "용접 세부 분석 없음",
    "LOW_SCORE": "신뢰도 낮음",
    "IMAGE_QUALITY": "사진 품질 경고",
}
REVIEW_SQL = "(i.ai_flags IS NOT NULL OR (i.ai_confidence IS NOT NULL AND i.ai_confidence < 0.6))"
SORTS = {
    "date": "i.inspected_at DESC, i.id DESC",
    "old": "i.inspected_at ASC, i.id ASC",
    "status": "CASE i.status WHEN 'DEFECT' THEN 1 WHEN 'REINSPECT' THEN 2 WHEN 'REWORK' THEN 3 WHEN 'AI_DONE' THEN 4 "
              "WHEN 'PENDING' THEN 5 WHEN 'REJECT' THEN 6 ELSE 7 END, i.inspected_at DESC",
    "conf": "i.ai_confidence IS NULL, i.ai_confidence ASC, i.inspected_at DESC",
}

_types: dict[str, dict] | None = None


def defect_types(conn: sqlite3.Connection | None = None) -> dict[str, dict]:
    """결함 유형 사전 캐시 (기준 데이터는 시작 때만 바뀐다)."""
    global _types
    if _types is None:
        def load(c):
            return {r["code"]: dict(r) for r in c.execute(
                "SELECT * FROM defect_types ORDER BY in_b_dict DESC, domain, code")}
        if conn is not None:
            _types = load(conn)
        else:
            with db.connect() as c:
                _types = load(c)
    return _types


def type_name(code: str | None) -> str:
    if not code:
        return "-"
    t = defect_types().get(code)
    return t["name_ko"] if t else code


def types_by_domain() -> list[tuple[str, list[dict]]]:
    out: dict[str, list[dict]] = {d: [] for d in reference.DOMAINS}
    for t in defect_types().values():
        if t["code"] != "NORMAL":
            out.setdefault(t["domain"], []).append(t)
    return [(d, out[d]) for d in reference.DOMAINS if out.get(d)]


def parse_json(v: Any, default: Any = None) -> Any:
    if not v:
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


# ── 목록 ─────────────────────────────────────────────────────

def list_filters(conn: sqlite3.Connection) -> dict[str, list]:
    return {
        "tanks": [r[0] for r in conn.execute("SELECT DISTINCT tank_no FROM inspections ORDER BY 1")],
        "blocks": [r[0] for r in conn.execute("SELECT DISTINCT block FROM inspections ORDER BY 1")],
        "processes": [r[0] for r in conn.execute(
            "SELECT DISTINCT weld_process FROM inspections WHERE weld_process <> '-' ORDER BY 1")],
    }


def search(conn: sqlite3.Connection, f: dict[str, str], *, extra_where: str = "", extra_params: tuple = (),
           sort_sql: str | None = None, page: int = 1, page_size: int = PAGE_SIZE) -> tuple[list[dict], int]:
    where, params = ["1=1"], []
    if f.get("days") and f["days"].isdigit():
        since = (db.now_kst() - timedelta(days=int(f["days"]))).strftime(db.TS_FMT)
        where.append("i.inspected_at >= ?")
        params.append(since)
    if f.get("status") in STATUSES:
        where.append("i.status = ?")
        params.append(f["status"])
    if f.get("domain") in reference.DOMAINS:
        where.append("COALESCE(i.ai_domain, 'WELD') = ?")
        params.append(f["domain"])
    if f.get("dtype"):
        where.append("(i.defect_type = ? OR (i.defect_type IS NULL AND i.ai_defect_type = ?))")
        params += [f["dtype"], f["dtype"]]
    for key, col in (("tank", "i.tank_no"), ("block", "i.block"), ("process", "i.weld_process")):
        if f.get(key):
            where.append(f"{col} = ?")
            params.append(f[key])
    if f.get("q"):
        like = f"%{f['q'].strip()}%"
        where.append("(i.code LIKE ? OR i.joint_no LIKE ? OR i.finding LIKE ? OR i.ai_label LIKE ?)")
        params += [like, like, like, like]
    if f.get("review") == "1":
        where.append(REVIEW_SQL)
    if extra_where:
        where.append(extra_where)
        params += list(extra_params)
    wsql = " AND ".join(where)
    total = db.scalar(conn, f"SELECT COUNT(*) FROM inspections i WHERE {wsql}", params) or 0
    order = sort_sql or SORTS.get(f.get("sort") or "date", SORTS["date"])
    rows = db.all_(conn, f"""
        SELECT i.*, u.name AS inspector_name FROM inspections i LEFT JOIN users u ON u.id = i.inspector_id
         WHERE {wsql} ORDER BY {order} LIMIT ? OFFSET ?""", params + [page_size, (page - 1) * page_size])
    return [decorate(dict(r)) for r in rows], total


def decorate(r: dict) -> dict:
    """화면에서 바로 쓰는 계산 칸 추가."""
    r["flags"] = parse_json(r.get("ai_flags"), [])
    r["findings"] = parse_json(r.get("ai_findings"), [])
    r["review"] = bool(r["flags"]) or (r.get("ai_confidence") is not None and r["ai_confidence"] < 0.6)
    r["domain"] = r.get("ai_domain") or "WELD"
    r["shown_type"] = r.get("defect_type") or (r.get("ai_defect_type") if r.get("ai_is_defect") else None)
    r["is_mock"] = (r.get("model_key") or "").startswith("mock")
    due = r.get("due_date")
    r["overdue"] = bool(due and r.get("status") in OPEN_STATUSES and due < db.now_kst().strftime("%Y-%m-%d"))
    return r


# ── 상세 ─────────────────────────────────────────────────────

def detail(conn: sqlite3.Connection, inspection_id: int) -> dict | None:
    row = db.one(conn, """SELECT i.*, u.name AS inspector_name FROM inspections i
                          LEFT JOIN users u ON u.id = i.inspector_id WHERE i.id = ?""", (inspection_id,))
    if row is None:
        return None
    it = decorate(dict(row))
    events = [dict(e) for e in db.all_(conn, """
        SELECT e.*, u.name AS actor_name FROM inspection_events e LEFT JOIN users u ON u.id = e.actor_id
         WHERE e.inspection_id = ? ORDER BY e.created_at, e.id""", (inspection_id,))]
    upload = db.one(conn, "SELECT id, summary, created_at, user_id FROM activity_logs WHERE id = ?",
                    (it["upload_log_id"],)) if it["upload_log_id"] else None
    guide_code = it["shown_type"] if it["shown_type"] and it["shown_type"] != "NORMAL" else None
    return {
        "it": it, "events": events, "upload": dict(upload) if upload else None,
        "ai": ai_results(conn, inspection_id),
        "analysis": latest_analysis(conn, inspection_id),
        "guide": defect_types(conn).get(guide_code) if guide_code else None,
        "neighbors": db.one(conn, """SELECT
              (SELECT id FROM inspections WHERE id < ? ORDER BY id DESC LIMIT 1) AS prev_id,
              (SELECT id FROM inspections WHERE id > ? ORDER BY id LIMIT 1) AS next_id""",
                            (inspection_id, inspection_id)),
    }


def ai_results(conn: sqlite3.Connection, inspection_id: int) -> dict:
    """단계별 최신 결과 (A, B) + 실행 이력 수."""
    out: dict[str, Any] = {"A": None, "B": None, "runs": 0, "reinspect": []}
    for r in db.all_(conn, """SELECT r.*, m.name AS model_name, m.provider FROM ai_model_results r
                              LEFT JOIN ai_models m ON m.key = r.model_key
                              WHERE r.inspection_id = ? ORDER BY r.id""", (inspection_id,)):
        d = dict(r)
        d["candidates"] = parse_json(d.get("candidates_json"), [])
        d["class_summary"] = parse_json(d.get("class_summary"), {})
        d["scores"] = parse_json(d.get("scores_json"), {})
        d["keywords"] = parse_json(d.get("keywords_json"), [])
        d["is_mock"] = d.get("provider") == "mock"
        if d.get("trigger") == "REINSPECT" and d.get("image_path") and d["stage"] == "A":
            out["reinspect"].append(d)
        if d["stage"] == "A":
            out["B"] = None  # 최신 실행 기준: A 를 다시 돌리면 이전 B 는 그 실행의 결과가 아니다
        out[d["stage"]] = d
        out["runs"] += 1
    if out["B"] and out["B"]["scores"]:
        out["B"]["scores_sorted"] = sorted(out["B"]["scores"].items(), key=lambda x: -x[1])
    if out["A"]:
        out["A"]["summary_sorted"] = sorted(out["A"]["class_summary"].items(), key=lambda x: -x[1].get("max", 0))
    return out


def latest_analysis(conn: sqlite3.Connection, inspection_id: int) -> dict | None:
    r = db.one(conn, "SELECT * FROM ai_analyses WHERE inspection_id = ? ORDER BY id DESC LIMIT 1", (inspection_id,))
    if r is None:
        return None
    d = dict(r)
    for k in ("causes_json", "prevention_json", "checks_json", "rag_hits_json", "keywords_json"):
        d[k[:-5]] = parse_json(d.get(k), [])
    return d


# ── 불량 관리 ────────────────────────────────────────────────

QUEUE_VIEWS = {
    "DEFECT": ("i.status = 'DEFECT'", "COALESCE(i.due_date, '9999') ASC, i.updated_at ASC"),
    "REWORK": ("i.status = 'REWORK'", "COALESCE(i.due_date, '9999') ASC, i.updated_at ASC"),
    "REINSPECT": ("i.status = 'REINSPECT'", "i.updated_at ASC"),
    "CLOSED": ("i.final_is_defect = 1 AND i.status IN ('PASS','REJECT')", "i.closed_at DESC"),
}


def queue_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {k: db.scalar(conn, f"SELECT COUNT(*) FROM inspections i WHERE {w}") or 0
            for k, (w, _) in QUEUE_VIEWS.items()}


EVENT_LABELS = {
    "AI_INSPECT": "AI 판정", "CONFIRM_OK": "정상 확정", "CONFIRM_DEFECT": "불량 확정", "REWORK_PLAN": "조치 계획 등록",
    "REWORK_DONE": "수정 완료", "REINSPECT_OK": "재검사 합격", "REINSPECT_NG": "재검사 불합격", "REJECT": "폐기·재제작",
}


def history(conn: sqlite3.Connection, action: str | None, page: int, page_size: int = 50) -> tuple[list[dict], int]:
    where, params = "1=1", []
    if action in EVENT_LABELS:
        where, params = "e.action = ?", [action]
    total = db.scalar(conn, f"SELECT COUNT(*) FROM inspection_events e WHERE {where}", params) or 0
    rows = db.all_(conn, f"""
        SELECT e.*, i.code, i.block, i.joint_no, i.ai_domain, i.defect_type, i.ai_defect_type, u.name AS actor_name
          FROM inspection_events e JOIN inspections i ON i.id = e.inspection_id
          LEFT JOIN users u ON u.id = e.actor_id
         WHERE {where} ORDER BY e.created_at DESC, e.id DESC LIMIT ? OFFSET ?""",
                   params + [page_size, (page - 1) * page_size])
    return [dict(r) for r in rows], total
