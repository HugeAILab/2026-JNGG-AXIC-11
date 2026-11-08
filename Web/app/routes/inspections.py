"""품질 검사: 전체 검사 목록 · 검사 상세 · AI 결과 JSON (TODO.md 8.3, 8.4, 10장). 2단계는 조회만 한다.

`{inspection_id:int}` 경로 변환기를 써서 숫자가 아닌 주소(/upload, /uploads, /export.csv)는
이 라우트에 걸리지 않고 뒤의 다른 라우트(또는 준비 중 화면)로 넘어간다.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth, db, reference
from ..services import inspections as svc
from ..web import render

router = APIRouter()

FILTER_KEYS = ("days", "status", "domain", "dtype", "tank", "block", "process", "q", "review", "sort")


def _filters(request: Request) -> dict[str, str]:
    return {k: (request.query_params.get(k) or "").strip()[:60] for k in FILTER_KEYS}


def _page(request: Request) -> int:
    p = request.query_params.get("page", "1")
    return max(1, int(p)) if p.isdigit() else 1


def page_links(base_params: dict[str, str], page: int, total: int, size: int) -> dict:
    pages = max(1, (total + size - 1) // size)
    keep = {k: v for k, v in base_params.items() if v}

    def qs(p: int) -> str:
        return "?" + urlencode({**keep, "page": p})
    window = [p for p in range(max(1, page - 3), min(pages, page + 3) + 1)]
    return {"page": page, "pages": pages, "total": total, "qs": qs, "window": window,
            "first": (page - 1) * size + 1 if total else 0, "last": min(total, page * size)}


def _render_list(request: Request, *, not_found_id: int | None = None, status_code: int = 200):
    f = _filters(request)
    page = _page(request)
    with db.connect() as conn:
        svc.defect_types(conn)
        rows, total = svc.search(conn, f, page=page)
        options = svc.list_filters(conn)
    return render(request, "portal/inspections/list.html", {
        "rows": rows, "f": f, "options": options, "pager": page_links(f, page, total, svc.PAGE_SIZE),
        "types_by_domain": svc.types_by_domain(), "domains": reference.DOMAINS,
        "can_report": auth.has_role(auth.get_user(request), "admin", "manager", "inspector"),
        "not_found_id": not_found_id,
    }, active="inspections.list", status_code=status_code)


@router.get("/portal/inspections")
def inspection_list(request: Request, user: dict = Depends(auth.require_login)):
    return _render_list(request)


@router.get("/portal/inspections/{inspection_id:int}")
def inspection_detail(inspection_id: int, request: Request, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        svc.defect_types(conn)
        d = svc.detail(conn, inspection_id)
        included_reports = [dict(r) for r in db.all_(conn, """SELECT r.id, r.report_no, r.title FROM report_items ri
            JOIN reports r ON r.id=ri.report_id WHERE ri.inspection_id=? ORDER BY r.id DESC""", (inspection_id,))]
    if d is None:
        # 오류 화면 대신 메뉴·목록을 그대로 보여 주고 위에 안내를 띄운다 (상태 코드는 404 유지)
        return _render_list(request, not_found_id=inspection_id, status_code=404)
    from ..services import ai_pipeline, learning, uploads, workflow
    it = d["it"]
    photos = [{"label": "최초 검사", "url": uploads.storage_url(it["image_path"])}] if it["image_path"] else []
    for e in d["events"]:
        if e.get("image_path"):
            photos.append({"label": {"REWORK_DONE": "수정 후", "REINSPECT_OK": "재검사 (합격)",
                                     "REINSPECT_NG": "재검사 (불합격)"}.get(e["action"], "추가 사진"),
                           "url": uploads.storage_url(e["image_path"])})
    re_img = [r for r in d["ai"].get("reinspect", [])]
    return render(request, "portal/inspections/detail.html", {
        **d, "types_by_domain": svc.types_by_domain(), "event_labels": svc.EVENT_LABELS,
        "b_dict": reference.B_DICT_CODES, "actions": workflow.actions_for(it["status"], auth.get_user(request)),
        "photos": photos, "reinspect_photos": re_img, "finding_draft": ai_pipeline.finding_draft(it, svc.type_name),
        "included_reports": included_reports,
        "can_predict": auth.has_role(auth.get_user(request), "admin", "manager", "inspector"),
        "related_lesson": next((f"L{i:02d}" for i, code in enumerate(learning.LESSON_CODES, 1)
                                if code == it.get("shown_type")), None),
        "msg": request.query_params.get("msg", "")[:200], "err": request.query_params.get("err", "")[:200],
        "today": db.now_kst().strftime("%Y-%m-%d"), "severities": workflow.SEVERITIES,
    }, active="inspections.detail")


@router.get("/portal/api/inspections/{inspection_id:int}/ai")
def inspection_ai_json(inspection_id: int, user: dict = Depends(auth.require_login)):
    """단계별 최신 결과(A 요약·B 점수) + 최종 판정 (TODO.md 10장)."""
    with db.connect() as conn:
        it = db.one(conn, "SELECT * FROM inspections WHERE id = ?", (inspection_id,))
        if it is None:
            raise HTTPException(status_code=404, detail="검사 건을 찾을 수 없습니다.")
        ai = svc.ai_results(conn, inspection_id)
    it = svc.decorate(dict(it))

    def slim(r):
        if not r:
            return None
        keep = ("id", "model_key", "model_name", "stage", "trigger", "status", "top_class", "top_code", "top_score",
                "is_defect", "candidates", "class_summary", "scores", "keywords", "observation", "location",
                "image_quality", "model_version", "elapsed_ms", "error", "created_at", "is_mock")
        return {k: r.get(k) for k in keep}
    return JSONResponse({
        "ok": True, "inspection_id": inspection_id, "code": it["code"],
        "final": {k: it.get(k) for k in ("ai_status", "ai_domain", "ai_label", "ai_is_defect", "ai_defect_type",
                                         "ai_confidence", "ai_source", "model_key", "ai_at")}
        | {"flags": it["flags"], "findings": it["findings"], "is_mock": it["is_mock"]},
        "A": slim(ai["A"]), "B": slim(ai["B"]),
    })


@router.get("/portal/api/analysis/{inspection_id:int}")
def inspection_analysis_json(inspection_id: int, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        if db.one(conn, "SELECT id FROM inspections WHERE id=?", (inspection_id,)) is None:
            raise HTTPException(status_code=404, detail="검사 건을 찾을 수 없습니다.")
        analysis = svc.latest_analysis(conn, inspection_id)
    return JSONResponse({"ok": True, "inspection_id": inspection_id, "analysis": analysis})


# ── 3단계: 확정 · 조치 · 재검사 · 폐기 · AI 재판정 (TODO.md 6.3, 10장) ─────────────────────────
# 상세 화면의 폼(POST → 303 으로 상세로 돌아감)과 결과 검토 카드의 fetch(JSON) 를 같은 주소로 받는다.

ACTION_ROUTES = {"confirm": None, "rework-plan": "REWORK_PLAN", "rework-done": "REWORK_DONE",
                 "reinspect": None, "reject": "REJECT"}


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "") or \
        request.headers.get("content-type", "").startswith("application/json")


async def _form_data(request: Request) -> tuple[dict[str, str], list]:
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
        return ({k: str(v) for k, v in body.items() if v is not None} if isinstance(body, dict) else {}), []
    form = await request.form()
    files = [f for f in form.getlist("photo") if hasattr(f, "read") and f.filename]
    return {k: str(v) for k, v in form.items() if isinstance(v, str)}, files


def _done(request: Request, inspection_id: int, ok: bool, message: str, status: int = 200, extra: dict | None = None):
    if _wants_json(request):
        return JSONResponse({"ok": ok, "message" if ok else "error": message, **(extra or {})},
                            status_code=200 if ok else status)
    from urllib.parse import quote
    from fastapi.responses import RedirectResponse
    from ..web import url
    key = "msg" if ok else "err"
    return RedirectResponse(url(f"/portal/inspections/{inspection_id}?{key}={quote(message)}#actions"), status_code=303)


async def _save_photo(files: list) -> str | None:
    """조치·재검사 사진 1장 저장 (선택). 검증 실패는 UploadError."""
    from ..services import storage, uploads
    if not files:
        return None
    f = files[0]
    content = await f.read(10 * 1024 * 1024 + 1)
    await f.close()
    is_png, _w, _h = uploads.check_photo(f.filename or "photo", content)
    return storage.save_photo(content, is_png)


@router.post("/portal/inspections/{inspection_id:int}/{name}", dependencies=[Depends(auth.verify_csrf)])
async def inspection_action(inspection_id: int, name: str, request: Request,
                            user: dict = Depends(auth.require_login)):
    from ..services import uploads, workflow
    if name not in ACTION_ROUTES and name not in ("predict", "analyze"):
        raise HTTPException(status_code=404)
    if name == "analyze":
        if not auth.has_role(user, "admin", "manager", "inspector"):
            return _done(request, inspection_id, False, "다시 분석할 권한이 없습니다.", 403)
        from ..services import ai_analysis
        with db.connect() as conn:
            latest = svc.latest_analysis(conn, inspection_id)
        if latest and latest["status"] == "RUNNING":
            return _done(request, inspection_id, False, "이미 설명을 작성 중입니다.", 409)
        try:
            await ai_analysis.run_analysis(inspection_id, "MANUAL")
        except ValueError as exc:
            return _done(request, inspection_id, False, str(exc), 409)
        return _done(request, inspection_id, True, "불량 설명 초안을 새로 작성했습니다.")
    data, files = await _form_data(request)
    with db.connect() as conn:
        svc.defect_types(conn)
    if name == "predict":  # 화면에서 준비 중 안내만 제공한다. 직접 POST해도 재판정을 시작하지 않는다.
        if not auth.has_role(user, "admin", "manager", "inspector"):
            return _done(request, inspection_id, False, "AI 재판정 권한이 없습니다.", 403)
        with db.connect() as conn:
            exists = db.one(conn, "SELECT id FROM inspections WHERE id=?", (inspection_id,))
        if exists is None:
            raise HTTPException(status_code=404)
        return _done(request, inspection_id, False, "AI 다시 판정은 아직 준비 중입니다.", 409)
    action = ACTION_ROUTES[name]
    if name == "confirm":
        action = "CONFIRM_DEFECT" if data.get("decision") == "defect" else "CONFIRM_OK"
    elif name == "reinspect":
        action = "REINSPECT_NG" if data.get("decision") == "fail" else "REINSPECT_OK"
    try:
        photo = await _save_photo(files) if action == "REWORK_DONE" else None
        with db.connect() as conn:
            to = workflow.transition(conn, inspection_id, action, user, data, image_path=photo)
    except uploads.UploadError as e:
        return _done(request, inspection_id, False, e.message, e.status)
    except workflow.WorkflowError as e:
        if e.status == 404:
            raise HTTPException(status_code=404, detail=e.message)
        return _done(request, inspection_id, False, e.message, e.status)
    from ..web import STATUS_LABELS
    return _done(request, inspection_id, True, f"{svc.EVENT_LABELS[action]} 완료 → {STATUS_LABELS[to]}",
                 extra={"status": to})


@router.post("/portal/inspections/{inspection_id:int}/reinspect/photo", dependencies=[Depends(auth.verify_csrf)])
async def reinspect_photo(inspection_id: int, request: Request,
                          user: dict = Depends(auth.require_role("admin", "manager", "inspector"))):
    """재검사 ① 사진 업로드 → AI 판정 (상태는 REINSPECT 유지, ai_* 갱신). ② 합격/불합격은 /reinspect."""
    from ..services import jobs, uploads
    _data, files = await _form_data(request)
    with db.connect() as conn:
        it = db.one(conn, "SELECT status, ai_status FROM inspections WHERE id=?", (inspection_id,))
    if it is None:
        raise HTTPException(status_code=404)
    if it["status"] != "REINSPECT":
        return _done(request, inspection_id, False, "재검사 대기 상태에서만 재검사 사진을 올릴 수 있습니다.", 409)
    if not files:
        return _done(request, inspection_id, False, "재검사 사진을 고르세요.", 422)
    if it["ai_status"] in ("QUEUED", "RUNNING"):
        return _done(request, inspection_id, False, "이미 AI 판정 중입니다.", 409)
    try:
        rel = await _save_photo(files)
    except uploads.UploadError as e:
        return _done(request, inspection_id, False, e.message, e.status)
    jobs.enqueue(inspection_id, trigger="REINSPECT", image_rel=rel, user_id=user["id"])
    return _done(request, inspection_id, True, "재검사 사진을 올렸습니다. AI 판정이 끝나면 합격/불합격을 결정하세요.")


@router.get("/portal/api/inspections/{inspection_id:int}/a-result")
def inspection_a_result(inspection_id: int, user: dict = Depends(auth.require_login)):
    """모델 A 원본 응답(마스크 포함, 저장 파일을 풀어서) — 분할 뷰어용. ?result_id= 로 특정 실행."""
    from ..services import storage, uploads
    with db.connect() as conn:
        r = db.one(conn, """SELECT id, raw_path, image_path, image_w, image_h, elapsed_ms, trigger, created_at
                            FROM ai_model_results WHERE inspection_id=? AND stage='A' AND status='OK'
                              AND raw_path IS NOT NULL ORDER BY id DESC LIMIT 1""", (inspection_id,))
    if r is None:
        raise HTTPException(status_code=404, detail="저장된 모델 A 결과(마스크)가 없습니다.")
    body = storage.load_result(r["raw_path"])
    if body is None:
        raise HTTPException(status_code=404, detail="모델 A 결과 파일을 찾을 수 없습니다.")
    return {"ok": True, "result": body, "image": {"width": r["image_w"], "height": r["image_h"]},
            "image_url": uploads.storage_url(r["image_path"]), "elapsed_seconds": round((r["elapsed_ms"] or 0) / 1000, 2),
            "result_id": r["id"], "trigger": r["trigger"], "created_at": r["created_at"]}
