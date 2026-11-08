"""불량 관리: 상태별 대기열 · 완료된 불량 · 전체 처리 이력 (TODO.md 5.2, 6장). 2단계는 조회만 한다."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import auth, db, reference
from ..services import inspections as svc
from ..web import render
from .inspections import _filters, _page, page_links

router = APIRouter()

TITLES = {"DEFECT": "조치 대기 (불량)", "REWORK": "수정 중", "REINSPECT": "재검사 대기", "CLOSED": "완료된 불량"}


@router.get("/portal/defects")
def defect_queue(request: Request, user: dict = Depends(auth.require_login)):
    view = request.query_params.get("status", "DEFECT")
    if view not in svc.QUEUE_VIEWS:
        view = "DEFECT"
    where, order = svc.QUEUE_VIEWS[view]
    f = _filters(request)
    f["status"] = ""  # 대기열은 상태가 정해져 있으므로 목록 필터의 상태는 쓰지 않는다
    page = _page(request)
    with db.connect() as conn:
        svc.defect_types(conn)
        rows, total = svc.search(conn, f, extra_where=where, sort_sql=order, page=page)
        counts = svc.queue_counts(conn)
    return render(request, "portal/defects/queue.html", {
        "rows": rows, "view": view, "title": TITLES[view], "counts": counts, "titles": TITLES, "f": f,
        "pager": page_links({**f, "status": view}, page, total, svc.PAGE_SIZE), "domains": reference.DOMAINS,
    }, active=f"defects.{view}")


@router.get("/portal/defects/history")
def defect_history(request: Request, user: dict = Depends(auth.require_login)):
    action = request.query_params.get("action") or ""
    page = _page(request)
    with db.connect() as conn:
        svc.defect_types(conn)
        rows, total = svc.history(conn, action, page)
    return render(request, "portal/defects/history.html", {
        "rows": rows, "action": action, "event_labels": svc.EVENT_LABELS,
        "pager": page_links({"action": action}, page, total, 50),
    }, active="defects.history")
