"""보고서 목록·생성·상세·PDF."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import auth, config, db
from ..services import activity_log, report_pdf, report_store
from ..web import render, url
from .inspections import page_links

router = APIRouter()
CREATE_ROLES = ("admin", "manager", "inspector")


def _params(data: dict) -> dict:
    kind = data.get("type", "DEFECT")
    source = data.get("source", "SELECTION")
    ids = data.get("inspection_ids")
    if ids is not None and (not isinstance(ids, list) or any(type(x) is not int for x in ids)):
        raise ValueError("검사 항목 번호가 올바르지 않습니다.")
    log_id = data.get("upload_log_id")
    if log_id is not None and type(log_id) is not int:
        raise ValueError("업로드 번호가 올바르지 않습니다.")
    return dict(kind=kind, source=source, ids=ids, upload_log_id=log_id, scope=data.get("scope", "DEFECT"))


@router.get("/portal/reports")
def report_list(request: Request, user: dict = Depends(auth.require_login)):
    kind = request.query_params.get("type", "")
    days = request.query_params.get("days", "")
    q = request.query_params.get("q", "").strip()[:80]
    raw_page = request.query_params.get("page", "1")
    page = max(1, int(raw_page)) if raw_page.isdigit() else 1
    where, params = ["1=1"], []
    if kind in report_store.KINDS:
        where.append("r.type=?"); params.append(kind)
    if days in ("7", "30"):
        from datetime import timedelta
        where.append("r.created_at>=?"); params.append((db.now_kst() - timedelta(days=int(days))).strftime(db.TS_FMT))
    if q:
        where.append("(r.title LIKE ? OR r.report_no LIKE ?)"); params += [f"%{q}%", f"%{q}%"]
    sql = " AND ".join(where)
    with db.connect() as conn:
        count = db.scalar(conn, f"SELECT COUNT(*) FROM reports r WHERE {sql}", params) or 0
        rows = [dict(r) for r in db.all_(conn, f"""SELECT r.*, u.name author_name FROM reports r
                    LEFT JOIN users u ON u.id=r.created_by WHERE {sql} ORDER BY r.id DESC LIMIT 20 OFFSET ?""", params + [(page-1)*20])]
    return render(request, "portal/reports/list.html", {"rows": rows, "kind": kind, "days": days, "q": q,
                  "pager": page_links({"type": kind, "days": days, "q": q}, page, count, 20)}, active="reports." + (kind if kind in report_store.KINDS else "all"))


@router.post("/portal/api/reports/preview", dependencies=[Depends(auth.verify_csrf)])
async def report_preview(request: Request, user: dict = Depends(auth.require_role(*CREATE_ROLES))):
    try:
        params = _params(await request.json())
        with db.connect() as conn:
            p = report_store.preview(conn, **params)
        return {k: v for k, v in p.items() if k != "rows"}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/portal/reports", dependencies=[Depends(auth.verify_csrf)])
async def report_create(request: Request, user: dict = Depends(auth.require_role(*CREATE_ROLES))):
    try:
        body = await request.json()
        params = _params(body)
        with db.connect() as conn:
            report_id, excluded = report_store.create(conn, title=body.get("title", ""), user=user, **params)
        return {"ok": True, "report_id": report_id, "url": url(f"/portal/reports/{report_id}"), "excluded": excluded}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def _report(conn, report_id: int):
    row = db.one(conn, "SELECT r.*, u.name author_name FROM reports r LEFT JOIN users u ON u.id=r.created_by WHERE r.id=?", (report_id,))
    if not row:
        raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
    return dict(row)


@router.get("/portal/reports/{report_id:int}.pdf")
def report_download(report_id: int, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        report = _report(conn, report_id)
        items = [dict(x) for x in db.all_(conn, "SELECT * FROM report_items WHERE report_id=? ORDER BY sort_order", (report_id,))]
    directory = config.DATA_DIR / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{report["report_no"]}.pdf'
    if not path.exists():
        content = report_pdf.generate(report, items)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)
    activity_log.log("REPORT", "REPORT_PDF", f'보고서 {report["report_no"]} PDF 다운로드',
                     target=("report", report_id), user_id=user["id"])
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/portal/reports/{report_id:int}")
def report_detail(report_id: int, request: Request, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        report = _report(conn, report_id)
        items = [dict(x) for x in db.all_(conn, """SELECT ri.sort_order, i.id, i.code, i.status FROM report_items ri
                JOIN inspections i ON i.id=ri.inspection_id WHERE ri.report_id=? ORDER BY ri.sort_order""", (report_id,))]
    return render(request, "portal/reports/view.html", {"report": report, "items": items}, active="reports.all")
