"""시스템 메뉴: 사용자 목록(admin, 1단계) · 활동 로그(admin·manager) · AI 연결 상태(admin) (TODO.md 7.3, 8.7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import auth, db
from ..web import render

router = APIRouter()


@router.get("/portal/admin/users")
def users(request: Request, user: dict = Depends(auth.require_role("admin"))):
    with db.connect() as conn:
        rows = db.all_(conn, """
            SELECT u.id, u.username, u.name, u.role, u.created_at,
                   (SELECT MAX(created_at) FROM activity_logs l
                     WHERE l.user_id = u.id AND l.action = 'LOGIN') AS last_login
              FROM users u ORDER BY u.id""")
    return render(request, "portal/admin/users.html", {"rows": rows}, active="admin.users")


# ── 활동 로그 (admin·manager, TODO.md 8.7) ─────────────────────────────────────
LOG_FILTERS = ("days", "type", "level", "user", "q")


@router.get("/portal/admin/logs")
def logs(request: Request, user: dict = Depends(auth.require_role("admin", "manager"))):
    import json
    from datetime import timedelta
    from ..services.activity_log import LEVELS, LOG_TYPES
    from .inspections import _page, page_links
    f = {k: (request.query_params.get(k) or "").strip()[:60] for k in LOG_FILTERS}
    where, params = ["1=1"], []
    if f["days"].isdigit():
        where.append("l.created_at >= ?")
        params.append((db.now_kst() - timedelta(days=int(f["days"]))).strftime(db.TS_FMT))
    if f["type"] in LOG_TYPES:
        where.append("l.log_type = ?")
        params.append(f["type"])
    elif f["type"] == "RAG":
        # 기존 기록은 AI 유형으로 저장되어 있으므로 화면에서 RAG만 분리해 보여준다.
        where.append("l.log_type = 'AI' AND l.action IN ('ANALYSIS_DONE', 'RAG_CALL')")
    if f["level"] in LEVELS:
        where.append("l.level = ?")
        params.append(f["level"])
    if f["user"].isdigit():
        where.append("l.user_id = ?")
        params.append(int(f["user"]))
    if f["q"]:
        where.append("(l.summary LIKE ? OR l.action LIKE ?)")
        params += [f"%{f['q']}%", f"%{f['q']}%"]
    w = " AND ".join(where)
    page = _page(request)
    with db.connect() as conn:
        total = db.scalar(conn, f"SELECT COUNT(*) FROM activity_logs l WHERE {w}", params) or 0
        rows = [dict(r) for r in db.all_(conn, f"""
            SELECT l.*, u.name AS user_name FROM activity_logs l LEFT JOIN users u ON u.id = l.user_id
             WHERE {w} ORDER BY l.id DESC LIMIT 50 OFFSET ?""", params + [(page - 1) * 50])]
        users_ = db.all_(conn, "SELECT id, name, username FROM users ORDER BY id")
    for r in rows:
        r["display_type"] = "RAG" if r["log_type"] == "AI" and r["action"] in ("ANALYSIS_DONE", "RAG_CALL") else r["log_type"]
        r["display_action"] = "근거 검색·설명 작성" if r["action"] == "ANALYSIS_DONE" else r["action"]
        r["display_summary"] = r["summary"]
        if r.get("detail_json"):
            try:
                detail = json.loads(r["detail_json"])
                r["detail_pretty"] = json.dumps(detail, ensure_ascii=False, indent=2)
                if r["action"] == "ANALYSIS_DONE":
                    source = "AI 생성 설명" if detail.get("source") == "LLM" else "기본 설명"
                    hits = detail.get("hits", 0)
                    used = bool(hits)
                    inspection = r["summary"].split(" · ")[1] if " · " in r["summary"] else "검사"
                    r["display_summary"] = f"{inspection} · RAG {'사용' if used else '미사용'} · {source}"
                    r["detail_pretty"] = (f"RAG 검색: {'사용' if used else '미사용'}\n"
                                          f"검색 근거: {hits}건\n설명 방식: {source}\n"
                                          f"검사 번호: {detail.get('inspection_id', '-')}")
            except ValueError:
                r["detail_pretty"] = r["detail_json"]
    return render(request, "portal/admin/logs.html", {
        "rows": rows, "f": f, "pager": page_links(f, page, total, 50), "log_types": (*LOG_TYPES, "RAG"), "levels": LEVELS,
        "users": users_,
    }, active="admin.logs")


# ── AI 연결 상태 (admin, TODO.md 9장) ──────────────────────────────────────────
@router.get("/portal/admin/health")
async def ai_health(request: Request, user: dict = Depends(auth.require_role("admin"))):
    from .. import config
    from ..integrations.models import aistream_seg, registry, weld_api
    from ..services import ai_analysis, jobs
    a, b = registry.stage_a(), registry.stage_b()
    a_ready = await aistream_seg.health() if not a.is_mock else None
    b_ready = await weld_api.health() if (b and not b.is_mock) else None
    rag_connection = await ai_analysis.connection_health()
    with db.connect() as conn:
        recent = [dict(r) for r in db.all_(conn, """
            SELECT model_key, stage, status, COUNT(*) AS n, MAX(created_at) AS last FROM ai_model_results
             WHERE trigger <> 'SEED' GROUP BY model_key, stage, status ORDER BY stage, model_key, status""")]
        queue = db.scalar(conn, "SELECT COUNT(*) FROM inspections WHERE ai_status IN ('QUEUED','RUNNING')") or 0
        rag_docs = db.scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE source='DOC'") or 0
        rag_cases = db.scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE source='CASE'") or 0
    return render(request, "portal/admin/health.html", {
        "mode": config.AI_MODE, "a": a, "b": b, "a_ready": a_ready, "b_ready": b_ready,
        "a_url": config.SEGMENTATION_BASE_URL, "b_url": config.MODEL_B_URL, "recent": recent, "queue": queue,
        "running": jobs.pending_count(), "b_threshold": config.MODEL_B_THRESHOLD,
        "rag_connection": rag_connection, "rag_docs": rag_docs, "rag_cases": rag_cases,
        "rag_timeout": config.RAG_TIMEOUT,
    }, active="admin.health")


# ── DB 테이블 정보 (admin) — 대시보드에 있던 'DB 테이블 행 수'를 옮김 ─────────────────
TABLE_GROUPS = [
    ("불량 관리", ["inspections", "inspection_events", "defect_types", "activity_logs"]),
    ("AI 모델·지식", ["ai_models", "a_class_map", "ai_model_results", "ai_analyses", "rag_chunks", "model_runs"]),
    ("보고서", ["reports", "report_items"]),
    ("교육", ["edu_classes", "edu_articles", "edu_quizzes", "quiz_attempts"]),
    ("시스템", ["users", "app_settings"]),
]


@router.get("/portal/admin/db")
def db_info(request: Request, user: dict = Depends(auth.require_role("admin"))):
    from .. import config
    with db.connect() as conn:
        counts = db.table_counts(conn)
        cols = {t: len(db.columns(conn, t)) for t in counts}
        last = {}
        for t in counts:
            c = db.columns(conn, t)
            col = next((x for x in ("created_at", "updated_at", "imported_at") if x in c), None)
            last[t] = db.scalar(conn, f"SELECT MAX({col}) FROM {t}") if col else None
        version = db.schema_version(conn)
        journal = db.scalar(conn, "PRAGMA journal_mode")
    known = {t for _, ts in TABLE_GROUPS for t in ts}
    groups = [(g, [t for t in ts if t in counts]) for g, ts in TABLE_GROUPS]
    other = sorted(t for t in counts if t not in known)
    if other:
        groups.append(("기타 (이전 버전 보관 등)", other))
    size = lambda p: p.stat().st_size if p.exists() else 0  # noqa: E731
    files = {"db": size(config.DB_PATH), "wal": size(config.DB_PATH.with_name(config.DB_PATH.name + "-wal"))}
    return render(request, "portal/admin/db.html", {
        "groups": groups, "counts": counts, "cols": cols, "last": last, "version": version, "journal": journal,
        "files": files, "total_rows": sum(counts.values()), "db_path": config.DB_PATH.name,
    }, active="admin.db")
