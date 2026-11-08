"""사진 여러 장 업로드: 검증 → 파일 저장 → UPLOAD 로그 1행 → 사진별 검사 건 생성 (TODO.md 6.4 ①②).

업로드 묶음 테이블은 따로 두지 않는다. 업로드 1회 = activity_logs 의 UPLOAD 로그 1행이고,
그 업로드로 만든 검사 건은 inspections.upload_log_id 로 그 로그를 가리킨다.
요청 본문 한도(프록시) 때문에 화면은 사진을 몇 장씩 나눠 보낼 수 있다. 첫 요청이 로그를 만들고,
이어지는 요청은 같은 log_id 에 검사 건을 덧붙인다 (로그 행은 고치지 않는다 — 추가 전용).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .. import config, db, reference
from ..integrations.models import aistream_seg
from . import activity_log, storage
from .inspections import decorate, parse_json

WORK_TYPES = ("WELD", "SURFACE", "INSULATION", "CABLE", "BINDING", "CUTTING", "MIXED")
WORK_LABELS = {**reference.DOMAIN_LABELS, "MIXED": "혼합"}
WELD_PROCESSES = ("SMAW", "GTAW", "GMAW", "FCAW", "SAW")
WELD_POSITIONS = ("1G", "2G", "3G", "4G")
TANKS = ("TANK-1", "TANK-2", "TANK-3", "TANK-4")
APPEND_WINDOW = timedelta(minutes=30)  # 나눠 보내기: 첫 요청 뒤 이 시간 안에만 같은 로그에 덧붙임


class UploadError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def clean_common(form: dict[str, str]) -> dict[str, Any]:
    """공통 정보 검증. 작업 종류는 참고 정보 (실제 분기는 모델 A 결과로 한다)."""
    g = lambda k, n=40: (form.get(k) or "").strip()[:n]  # noqa: E731
    work = g("work_type")
    if work not in WORK_TYPES:
        raise UploadError(422, "작업 종류를 고르세요.")
    tank = g("tank_no")
    if tank not in TANKS:
        raise UploadError(422, "탱크를 고르세요.")
    block = g("block", 20)
    if not block:
        raise UploadError(422, "블록을 입력하세요 (예: B-07).")
    weldish = work in ("WELD", "MIXED")
    process = g("weld_process") if weldish else "-"
    if weldish and process not in WELD_PROCESSES:
        raise UploadError(422, "용접 작업은 용접법을 고르세요.")
    position = g("weld_position") if weldish and g("weld_position") in WELD_POSITIONS else None
    return {"project": g("project", 40) or "LNG-PJT-A", "tank_no": tank, "block": block, "work_type": work,
            "weld_process": process, "weld_position": position, "welder": (g("welder", 30) if weldish else "") or None}


def check_photo(name: str, content: bytes) -> tuple[bool, int, int]:
    """JPEG/PNG · 10 MiB · 2천만 픽셀 (모델 A 한도와 같음). → (PNG 여부, 너비, 높이)"""
    if not content:
        raise UploadError(422, f"{name}: 빈 파일입니다.")
    if len(content) > aistream_seg.MAX_BYTES:
        raise UploadError(413, f"{name}: 사진은 최대 10 MiB까지 올릴 수 있습니다.")
    try:
        _b, w, h = aistream_seg.prepare_image(content)
    except aistream_seg.ModelAError as e:
        raise UploadError(e.status, f"{name}: {e.message}") from e
    return content.startswith(b"\x89PNG\r\n\x1a\n"), w, h


def _next_codes(conn: sqlite3.Connection, n: int) -> list[str]:
    day = db.now_kst().strftime("%Y%m%d")
    last = db.scalar(conn, "SELECT MAX(CAST(substr(code, 14) AS INTEGER)) FROM inspections WHERE code LIKE ?",
                     (f"INS-{day}-%",)) or 0
    return [f"INS-{day}-{last + i:04d}" for i in range(1, n + 1)]


def open_log(conn: sqlite3.Connection, user: dict, common: dict, manifest: list[str], ip: str | None) -> int:
    """UPLOAD 로그 1행 (요약: '사진 12장 업로드 · TANK-2 / B-07')."""
    count = len(manifest)
    return activity_log.log(
        "UPLOAD", "UPLOAD_PHOTOS", f"사진 {count}장 업로드 · {common['tank_no']} / {common['block']}"
        f" · {WORK_LABELS.get(common['work_type'], common['work_type'])}",
        {"photo_count": count, "files": manifest[:config.UPLOAD_MAX_FILES], "common": common},
        user_id=user["id"], ip=ip, conn=conn)


def get_log(conn: sqlite3.Connection, log_id: int) -> dict | None:
    r = db.one(conn, """SELECT l.*, u.name AS user_name FROM activity_logs l LEFT JOIN users u ON u.id = l.user_id
                        WHERE l.id=? AND l.log_type='UPLOAD'""", (log_id,))
    if r is None:
        return None
    d = dict(r)
    d["detail"] = parse_json(d.get("detail_json"), {})
    return d


def can_append(log: dict, user: dict) -> bool:
    if log["user_id"] != user["id"]:
        return False
    created = datetime.strptime(log["created_at"], db.TS_FMT)
    return db.now_kst().replace(tzinfo=None) - created <= APPEND_WINDOW


def add_photos(conn: sqlite3.Connection, log_id: int, user: dict, common: dict,
               photos: list[tuple[str, bytes, str]]) -> list[int]:
    """사진 저장 + 검사 건 생성 (status=PENDING, ai_status=QUEUED). photos: (파일명, 바이트, 위치 번호)."""
    already = db.scalar(conn, "SELECT COUNT(*) FROM inspections WHERE upload_log_id=?", (log_id,)) or 0
    if already + len(photos) > config.UPLOAD_MAX_FILES:
        raise UploadError(422, f"한 번에 최대 {config.UPLOAD_MAX_FILES}장까지 올릴 수 있습니다.")
    checked = [(name, content, joint, check_photo(name, content)) for name, content, joint in photos]
    codes = _next_codes(conn, len(checked))
    now = db.now_str()
    ids = []
    for i, ((name, content, joint, (is_png, _w, _h)), code) in enumerate(zip(checked, codes), start=already + 1):
        rel = storage.save_photo(content, is_png)
        ids.append(db.insert(conn, "inspections", {
            "code": code, "upload_log_id": log_id, **{k: common[k] for k in (
                "project", "tank_no", "block", "work_type", "weld_process", "weld_position", "welder")},
            "joint_no": (joint or "").strip()[:20] or f"P-{log_id}-{i:02d}", "inspector_id": user["id"],
            "image_path": rel, "is_sample": 0, "ai_status": "QUEUED", "status": "PENDING",
            "inspected_at": now, "updated_at": now}))
    return ids


def status(conn: sqlite3.Connection, log_id: int, stage_of) -> dict[str, Any]:
    """업로드 진행률 + 사진별 카드 정보 (화면이 1초마다 조회)."""
    log = get_log(conn, log_id)
    rows = db.all_(conn, "SELECT * FROM inspections WHERE upload_log_id=? ORDER BY id", (log_id,))
    items = []
    for r in rows:
        it = decorate(dict(r))
        analysis = db.one(conn, """SELECT status, description, explain_source FROM ai_analyses
                                   WHERE inspection_id=? ORDER BY id DESC LIMIT 1""", (it["id"],))
        stage = "ANALYZE" if analysis and analysis["status"] == "RUNNING" else stage_of(it["id"], it["ai_status"])
        a = db.one(conn, """SELECT id, top_class, top_score, candidates_json, image_w, image_h, raw_path, status, error
                            FROM ai_model_results WHERE inspection_id=? AND stage='A' ORDER BY id DESC LIMIT 1""",
                   (it["id"],))
        b = db.one(conn, """SELECT top_code, top_score, keywords_json, status, error, model_key
                            FROM ai_model_results WHERE inspection_id=? AND stage='B' ORDER BY id DESC LIMIT 1""",
                   (it["id"],))
        top_box = None
        if a and a["candidates_json"] and a["image_w"]:
            cands = parse_json(a["candidates_json"], [])
            if cands:
                x0, y0, x1, y1 = cands[0]["bbox"]
                top_box = [round(x0 / a["image_w"], 4), round(y0 / a["image_h"], 4),
                           round(x1 / a["image_w"], 4), round(y1 / a["image_h"], 4)]
        items.append({
            "id": it["id"], "code": it["code"], "joint_no": it["joint_no"], "status": it["status"],
            "ai_status": it["ai_status"], "stage": stage, "ai_error": it["ai_error"],
            "domain": it["ai_domain"], "ai_is_defect": it["ai_is_defect"], "ai_defect_type": it["ai_defect_type"],
            "ai_label": it["ai_label"], "ai_confidence": it["ai_confidence"], "ai_source": it["ai_source"],
            "flags": it["flags"], "findings": it["findings"], "review": it["review"], "is_mock": it["is_mock"],
            "final_is_defect": it["final_is_defect"], "defect_type": it["defect_type"],
            "image": storage_url(it["image_path"]), "thumb": storage_url(it["image_path"], thumb=True),
            "has_mask": bool(a and a["raw_path"]), "top_box": top_box,
            "a": {"class": a["top_class"], "score": a["top_score"], "status": a["status"]} if a else None,
            "b": {"code": b["top_code"], "score": b["top_score"], "status": b["status"], "error": b["error"],
                  "keywords": parse_json(b["keywords_json"], []), "is_mock": (b["model_key"] or "").startswith("mock")}
            if b else None,
            "analysis": {"status": analysis["status"], "description": (analysis["description"] or "")[:180],
                         "source": analysis["explain_source"]} if analysis else None,
        })
    done = sum(1 for x in items if x["ai_status"] in ("DONE", "ERROR"))
    planned = (log or {}).get("detail", {}).get("photo_count") or len(items)
    return {"ok": True, "log_id": log_id, "total": len(items), "planned": planned, "done": done,
            "finished": bool(items) and done == len(items) and len(items) >= planned,
            "defects": sum(1 for x in items if x["ai_is_defect"]),
            "confirmable": sum(1 for x in items if x["status"] == "AI_DONE"), "items": items}


def storage_url(rel: str | None, *, thumb: bool = False) -> str | None:
    if not rel:
        return None
    return f"{config.ROOT_PATH}/portal/files/{'thumbs/' + storage.thumb_rel(rel) if thumb else rel}"


def history(conn: sqlite3.Connection, user: dict, page: int, page_size: int = 20) -> tuple[list[dict], int]:
    """업로드 이력. admin·manager 는 전체, 그 밖은 자기 업로드만 (8.7)."""
    where, params = "l.log_type='UPLOAD'", []
    if user["role"] not in ("admin", "manager"):
        where += " AND l.user_id=?"
        params.append(user["id"])
    total = db.scalar(conn, f"SELECT COUNT(*) FROM activity_logs l WHERE {where}", params) or 0
    rows = db.all_(conn, f"""
        SELECT l.*, u.name AS user_name,
               (SELECT COUNT(*) FROM inspections i WHERE i.upload_log_id=l.id) AS n,
               (SELECT COUNT(*) FROM inspections i WHERE i.upload_log_id=l.id AND i.ai_status IN ('DONE','ERROR')) AS n_done,
               (SELECT COUNT(*) FROM inspections i WHERE i.upload_log_id=l.id AND i.ai_is_defect=1) AS n_defect,
               (SELECT COUNT(*) FROM inspections i WHERE i.upload_log_id=l.id AND i.status IN ('PENDING','AI_DONE')) AS n_open,
               (SELECT image_path FROM inspections i WHERE i.upload_log_id=l.id AND i.image_path IS NOT NULL
                 ORDER BY i.id LIMIT 1) AS cover
          FROM activity_logs l LEFT JOIN users u ON u.id=l.user_id
         WHERE {where} ORDER BY l.id DESC LIMIT ? OFFSET ?""", params + [page_size, (page - 1) * page_size])
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = parse_json(d.get("detail_json"), {})
        d["cover_thumb"] = storage_url(d["cover"], thumb=True)
        out.append(d)
    return out, total
