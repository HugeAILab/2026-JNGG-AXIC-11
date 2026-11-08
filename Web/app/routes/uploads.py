"""사진 업로드 검사: 여러 장 업로드 → 저장 → AI 판정(백그라운드) → 결과 검토·일괄 확정 (TODO.md 6.4, 10장).

    GET  /portal/inspections/upload                     업로드 화면 (사진 선택 → 썸네일 카드, 공통 정보)
    POST /portal/inspections/upload                     사진 저장 + UPLOAD 로그 + 검사 건 생성 + AI 판정 시작 (JSON)
    GET  /portal/inspections/uploads                    업로드 이력
    GET  /portal/inspections/uploads/{log_id}           결과 검토 화면 (카드 그리드, 1초마다 진행률 조회)
    GET  /portal/inspections/uploads/{log_id}/status    진행률 JSON
    POST /portal/inspections/uploads/{log_id}/confirm-all   AI 결과대로 일괄 확정
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth, config, db, reference
from ..services import inspections as svc
from ..services import jobs, uploads, workflow
from ..web import render
from .inspections import _page, page_links

router = APIRouter()
UPLOAD_ROLES = ("admin", "manager", "inspector")  # 역할별 권한표(TODO.md 7.3): 업로드는 worker 제외
can_upload = auth.require_role(*UPLOAD_ROLES)


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, "status": status}, status_code=status)


@router.get("/portal/inspections/upload")
def upload_page(request: Request, user: dict = Depends(auth.require_login)):
    return render(request, "portal/inspections/upload.html", {
        "can_upload": user["role"] in UPLOAD_ROLES, "is_mock_mode": config.AI_MODE == "mock",
        "work_types": uploads.WORK_TYPES, "work_labels": uploads.WORK_LABELS, "tanks": uploads.TANKS,
        "processes": uploads.WELD_PROCESSES, "positions": uploads.WELD_POSITIONS,
        "max_files": config.UPLOAD_MAX_FILES, "has_b": config.AI_MODE == "mock" or bool(config.MODEL_B_URL)
        or config.MODEL_B_MOCK,
    }, active="inspections.upload")


@router.post("/portal/inspections/upload", dependencies=[Depends(auth.verify_csrf)])
async def upload(request: Request, user: dict = Depends(can_upload)):
    """multipart: files[]=사진, joints[]=사진별 위치 번호(비우면 자동), 공통 정보,
    manifest=JSON(이번 업로드 전체 파일명 — 나눠 보낼 때 첫 요청에), log_id=(이어 보낼 때)."""
    form = await request.form()
    files = [f for f in form.getlist("files") if hasattr(f, "read")]
    joints = [str(j) for j in form.getlist("joints")]
    photos = []
    for i, f in enumerate(files):  # 먼저 모두 읽고 닫는다 (어느 단계에서 거절해도 임시 파일이 남지 않게)
        content = await f.read(10 * 1024 * 1024 + 1)
        await f.close()
        photos.append(((f.filename or f"photo-{i + 1}")[:120], content, joints[i] if i < len(joints) else ""))
    if not photos:
        return _err(422, "사진을 한 장 이상 고르세요.")
    try:
        common = uploads.clean_common({k: str(form.get(k) or "") for k in (
            "project", "tank_no", "block", "work_type", "weld_process", "weld_position", "welder")})
    except uploads.UploadError as e:
        return _err(e.status, e.message)
    log_id_raw = str(form.get("log_id") or "")
    try:
        with db.connect() as conn:
            if log_id_raw.isdigit():
                log = uploads.get_log(conn, int(log_id_raw))
                if log is None or not uploads.can_append(log, user):
                    return _err(409, "이어서 보낼 업로드를 찾을 수 없습니다. 처음부터 다시 올려 주세요.")
                log_id = log["id"]
            else:
                try:
                    manifest = [str(x)[:120] for x in json.loads(str(form.get("manifest") or "[]"))]
                except ValueError:
                    manifest = []
                manifest = manifest or [p[0] for p in photos]
                if len(manifest) > config.UPLOAD_MAX_FILES:
                    return _err(422, f"한 번에 최대 {config.UPLOAD_MAX_FILES}장까지 올릴 수 있습니다.")
                log_id = uploads.open_log(conn, user, common, manifest, auth.client_ip(request))
            ids = uploads.add_photos(conn, log_id, user, common, photos)
    except uploads.UploadError as e:  # 트랜잭션이 되돌려져 로그·검사 건이 남지 않는다 (저장된 파일만 고아로 남음)
        return _err(e.status, e.message)
    for i in ids:
        jobs.enqueue(i, user_id=user["id"])
    return {"ok": True, "log_id": log_id, "inspection_ids": ids,
            "review_url": f"{config.ROOT_PATH}/portal/inspections/uploads/{log_id}"}


@router.get("/portal/inspections/uploads")
def upload_history(request: Request, user: dict = Depends(auth.require_login)):
    page = _page(request)
    with db.connect() as conn:
        rows, total = uploads.history(conn, user, page)
    return render(request, "portal/inspections/uploads.html", {
        "rows": rows, "pager": page_links({}, page, total, 20), "work_labels": uploads.WORK_LABELS,
        "all_users": user["role"] in ("admin", "manager"),
    }, active="inspections.uploads")


def _log_or_404(conn, log_id: int, user: dict) -> dict:
    log = uploads.get_log(conn, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="업로드 기록을 찾을 수 없습니다.")
    return log


@router.get("/portal/inspections/uploads/{log_id:int}")
def upload_review(log_id: int, request: Request, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        svc.defect_types(conn)
        log = _log_or_404(conn, log_id, user)
        existing_reports = [dict(r) for r in db.all_(conn, "SELECT id, report_no, title FROM reports WHERE upload_log_id=? ORDER BY id DESC", (log_id,))]
    return render(request, "portal/inspections/review.html", {
        "log": log, "work_labels": uploads.WORK_LABELS, "types_by_domain": svc.types_by_domain(),
        "can_confirm": workflow.can(user, "CONFIRM_DEFECT"), "domains": reference.DOMAINS,
        "existing_reports": existing_reports,
    }, active="inspections.uploads")


@router.get("/portal/inspections/uploads/{log_id:int}/status")
def upload_status(log_id: int, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        svc.defect_types(conn)
        _log_or_404(conn, log_id, user)
        data = uploads.status(conn, log_id, jobs.stage_of)
    for it in data["items"]:
        it["type_name"] = svc.type_name(it["ai_defect_type"]) if it["ai_defect_type"] else None
        it["final_type_name"] = svc.type_name(it["defect_type"]) if it["defect_type"] else None
        it["flag_labels"] = [svc.FLAG_LABELS.get(f, f) for f in it["flags"]]
        if it["b"]:
            it["b"]["name"] = svc.type_name(it["b"]["code"]) if it["b"]["code"] else None
        for f in it["findings"]:
            f["name"] = f.get("class") if f.get("from") == "A" else svc.type_name(f.get("code"))
    return data


@router.post("/portal/inspections/uploads/{log_id:int}/confirm-all", dependencies=[Depends(auth.verify_csrf)])
def upload_confirm_all(log_id: int, user: dict = Depends(auth.require_role(*workflow.ROLES["CONFIRM_DEFECT"]))):
    with db.connect() as conn:
        svc.defect_types(conn)
        _log_or_404(conn, log_id, user)
        out = workflow.confirm_all(conn, log_id, user)
    return {"ok": True, **out}
