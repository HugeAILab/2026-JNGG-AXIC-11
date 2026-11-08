"""사진별 AI 판정 백그라운드 작업: 모델 A → (용접이면 모델 B) → 최종 판정 저장 (TODO.md 2.3 ③, 6.4 ③).

- 큐 서버 없이 앱의 이벤트 루프에서 asyncio 작업으로 돈다 (uvicorn 워커 1개 전제).
- 모델 A 동시 호출은 aistream_seg.SLOTS(2) 가 제한한다. A 서빙이 429 면 Retry-After 만큼 쉬고 최대 3회 다시 시도.
- 상태 규칙(6.3): ai_* 칸은 언제든 갱신, 업무 상태는 PENDING → AI_DONE 만 (그것도 아직 PENDING 일 때만).
- 앱을 다시 시작하면 QUEUED·RUNNING 으로 남은 건을 다시 넣는다(resume). ERROR 는 수동 재판정.
- 화면 진행률용 사진별 단계(A 대기·A 실행·B 실행·완료·실패)는 메모리(STAGES)에 두고, 없으면 DB 의 ai_status 로 대신한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .. import config, db
from ..integrations.models import registry
from ..integrations.models.base import BResult, ModelError, result_status
from . import activity_log, ai_pipeline, storage
from .inspections import type_name

log = logging.getLogger("weldsight.jobs")

STAGES: dict[int, str] = {}        # inspection_id → A_WAIT / A_RUN / B_RUN / ANALYZE / DONE / ERROR
_tasks: set[asyncio.Task] = set()  # 작업이 GC 로 사라지지 않게 참조 보관
A_RETRIES = 3


def stage_of(inspection_id: int, ai_status: str) -> str:
    if ai_status in ("DONE", "ERROR"):
        return ai_status  # 완료·실패는 DB 확정 상태가 화면 표시의 기준이다
    s = STAGES.get(inspection_id)
    if s and s not in ("DONE", "ERROR"):
        return s
    return {"QUEUED": "A_WAIT", "RUNNING": "A_RUN"}.get(ai_status, "A_WAIT")


def enqueue(inspection_id: int, *, trigger: str = "AUTO", image_rel: str | None = None,
            user_id: int | None = None) -> None:
    """작업 넣기. 이벤트 루프 안(async 라우트·lifespan)에서 부른다."""
    with db.connect() as conn:
        conn.execute("UPDATE inspections SET ai_status='QUEUED', ai_error=NULL, updated_at=? WHERE id=?",
                     (db.now_str(), inspection_id))
    STAGES[inspection_id] = "A_WAIT"
    task = asyncio.get_running_loop().create_task(run(inspection_id, trigger=trigger, image_rel=image_rel,
                                                      user_id=user_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def resume() -> int:
    """재시작 후 이어서 처리 (QUEUED·RUNNING). 넣은 건수."""
    with db.connect() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM inspections WHERE ai_status IN ('QUEUED','RUNNING') AND image_path IS NOT NULL ORDER BY id")]
    for i in ids:
        enqueue(i, trigger="AUTO")
    return len(ids)


def pending_count() -> int:
    return sum(1 for t in _tasks if not t.done())


async def wait_all(timeout: float = 30) -> None:
    """테스트용: 넣은 작업이 모두 끝날 때까지."""
    if _tasks:
        await asyncio.wait_for(asyncio.gather(*list(_tasks), return_exceptions=True), timeout)


def _class_map(conn) -> dict[str, dict]:
    return {r["a_class"]: dict(r) for r in conn.execute("SELECT * FROM a_class_map")}


def _severity(conn) -> dict[str, str]:
    return {r["code"]: r["severity"] for r in conn.execute("SELECT code, severity FROM defect_types")}


async def _call_a(model: registry.Model, content: bytes, ctx: dict):
    for attempt in range(A_RETRIES + 1):
        try:
            return await model.predict(content, ctx)
        except ModelError as e:
            if e.status != 429 or attempt == A_RETRIES:
                raise
            try:
                wait = min(10.0, float(e.retry_after or 2))
            except ValueError:
                wait = 2.0
            await asyncio.sleep(wait * (attempt + 1))
    raise AssertionError("unreachable")


async def run(inspection_id: int, *, trigger: str = "AUTO", image_rel: str | None = None,
              user_id: int | None = None) -> None:
    try:
        await _run(inspection_id, trigger, image_rel, user_id)
    except Exception as exc:  # noqa: BLE001 — 예기치 않은 오류도 건을 ERROR 로 남겨 화면이 멈추지 않게
        log.exception("AI job failed for inspection %s", inspection_id)
        with db.connect() as conn:
            conn.execute("UPDATE inspections SET ai_status='ERROR', ai_error=?, updated_at=? WHERE id=?",
                         (f"내부 오류: {type(exc).__name__}"[:200], db.now_str(), inspection_id))
        STAGES[inspection_id] = "ERROR"


async def _run(inspection_id: int, trigger: str, image_rel: str | None, user_id: int | None) -> None:
    with db.connect() as conn:
        it = db.one(conn, "SELECT * FROM inspections WHERE id=?", (inspection_id,))
        if it is None:
            return
        it = dict(it)
        cmap, severity = _class_map(conn), _severity(conn)
        conn.execute("UPDATE inspections SET ai_status='RUNNING', updated_at=? WHERE id=?", (db.now_str(), inspection_id))
    rel = image_rel or it["image_path"]
    content = await asyncio.to_thread(storage.read_photo, rel)
    ctx: dict[str, Any] = {"work_type": it.get("work_type"), "weld_process": it.get("weld_process"),
                           "block": it.get("block")}
    target = ("inspection", inspection_id)

    # ① 모델 A
    STAGES[inspection_id] = "A_RUN"
    model_a = registry.stage_a()
    t0 = time.monotonic()
    try:
        seg = await _call_a(model_a, content, ctx)
    except ModelError as e:
        ms = int((time.monotonic() - t0) * 1000)
        with db.connect() as conn:
            db.insert(conn, "ai_model_results", {
                "inspection_id": inspection_id, "model_key": model_a.key, "stage": "A", "trigger": trigger,
                "status": result_status(e.status, e.message), "error": e.message[:300], "elapsed_ms": ms,
                "image_path": rel, "created_at": db.now_str()})
            conn.execute("UPDATE inspections SET ai_status='ERROR', ai_error=?, updated_at=? WHERE id=?",
                         (e.message[:200], db.now_str(), inspection_id))
            activity_log.log("AI", "AI_ERROR", f"모델 A 판정 실패 · {it['code']} · {e.message}"[:200],
                             {"model_key": model_a.key, "status": e.status, "inspection_id": inspection_id},
                             target, user_id=user_id, level="WARN", conn=conn)
        STAGES[inspection_id] = "ERROR"
        return
    a = ai_pipeline.summarize_a(seg.body, seg.width, seg.height, cmap, it.get("work_type"))
    top = a["top"]
    with db.connect() as conn:
        a_id = db.insert(conn, "ai_model_results", {
            "inspection_id": inspection_id, "model_key": model_a.key, "stage": "A", "trigger": trigger, "status": "OK",
            "top_class": top["class"] if top else None, "top_code": top["code"] if top else None,
            "top_score": top["score"] if top else None, "is_defect": a["is_defect"],
            "candidates_json": json.dumps(a["candidates"], ensure_ascii=False),
            "class_summary": json.dumps(a["class_summary"], ensure_ascii=False),
            "image_w": seg.width, "image_h": seg.height, "image_path": rel,
            "model_version": "mock-seg-0" if model_a.is_mock else "LNG Tank Quality Inspection",
            "elapsed_ms": int(seg.elapsed_seconds * 1000), "created_at": db.now_str()})
    raw_rel = await asyncio.to_thread(storage.save_result, inspection_id, a_id, seg.body)
    with db.connect() as conn:
        conn.execute("UPDATE ai_model_results SET raw_path=? WHERE id=?", (raw_rel, a_id))
        activity_log.log("AI", "AI_PREDICT",
                         f"모델 A 판정 · {it['code']} · {top['class'] if top else '검출 없음'}"[:200],
                         {"model_key": model_a.key, "inspection_id": inspection_id, "result_id": a_id,
                          "candidates": len(seg.body['segmentation']['instances']),
                          "elapsed_ms": int(seg.elapsed_seconds * 1000)},
                         target, user_id=user_id, conn=conn)

    # ② 용접 불량이면 모델 B
    b: BResult | None = None
    b_id = None
    b_model = registry.stage_b() if a["triggers_b"] else None
    if b_model is not None:
        STAGES[inspection_id] = "B_RUN"
        ctx.update(a_top=top, a_weld=a["weld_candidates"])
        t0 = time.monotonic()
        try:
            b = await b_model.predict(content, ctx)
        except ModelError as e:
            with db.connect() as conn:
                db.insert(conn, "ai_model_results", {
                    "inspection_id": inspection_id, "model_key": b_model.key, "stage": "B", "trigger": trigger,
                    "status": result_status(e.status, e.message), "error": e.message[:300], "image_path": rel,
                    "elapsed_ms": int((time.monotonic() - t0) * 1000), "created_at": db.now_str()})
                activity_log.log("AI", "AI_ERROR", f"모델 B 분석 실패 · {it['code']} · {e.message}"[:200],
                                 {"model_key": b_model.key, "status": e.status, "inspection_id": inspection_id},
                                 target, user_id=user_id, level="WARN", conn=conn)
    d = ai_pipeline.decide(a, b, b_ran=b_model is not None, b_threshold=config.MODEL_B_THRESHOLD, severity=severity)
    if b is not None:
        code, score = d["b_top"]
        with db.connect() as conn:
            b_id = db.insert(conn, "ai_model_results", {
                "inspection_id": inspection_id, "model_key": b_model.key, "stage": "B", "trigger": trigger,
                "status": "OK", "top_class": code, "top_code": code, "top_score": round(score, 4),
                "is_defect": int(code != "NORMAL"), "scores_json": json.dumps(b.scores),
                "keywords_json": json.dumps(b.keywords, ensure_ascii=False), "observation": b.observation,
                "location": b.location, "image_quality": b.image_quality,
                "raw": json.dumps(b.raw, ensure_ascii=False)[:20000] if b.raw else None, "image_path": rel,
                "model_version": b.model_version, "elapsed_ms": int(b.elapsed_seconds * 1000),
                "created_at": db.now_str()})
            activity_log.log("AI", "AI_PREDICT", f"모델 B 분석 · {it['code']} · {type_name(code)} {score:.2f}"[:200],
                             {"model_key": b_model.key, "inspection_id": inspection_id, "result_id": b_id},
                             target, user_id=user_id, conn=conn)

    # ③ 최종 판정 저장 + 처리 이력
    _save_final(inspection_id, it, d, a_id, b_id, model_a, b_model if b is not None else None, trigger, top, user_id)
    if d["ai_is_defect"]:
        STAGES[inspection_id] = "ANALYZE"
        try:
            from . import ai_analysis
            await ai_analysis.run_analysis(inspection_id, trigger)
        except Exception:  # noqa: BLE001 — 설명 실패가 완료된 AI 판정을 되돌리면 안 된다
            log.exception("RAG 분석 실패 · 검사 %s", inspection_id)
    STAGES[inspection_id] = "DONE"


def _save_final(inspection_id, it, d, a_id, b_id, model_a, model_b, trigger, top, user_id) -> None:
    now = db.now_str()
    model_key = model_a.key + (f"+{model_b.key}" if model_b else "")
    snapshot = {k: d[k] for k in ("ai_label", "ai_is_defect", "ai_defect_type", "ai_confidence", "ai_source",
                                  "ai_domain", "flags")}
    snapshot.update(a_result_id=a_id, b_result_id=b_id, a_top=(top or {}).get("class"),
                    b_top=list(d["b_top"]) if d["b_top"] else None, trigger=trigger)
    with db.connect() as conn:
        conn.execute("""
            UPDATE inspections SET ai_label=?, ai_is_defect=?, ai_defect_type=?, ai_confidence=?, ai_source=?,
                   ai_domain=?, ai_findings=?, ai_flags=?, ai_raw=?, ai_at=?, model_key=?, model_version=?,
                   ai_status='DONE', ai_error=NULL, updated_at=?
             WHERE id=?""",
            (d["ai_label"], d["ai_is_defect"], d["ai_defect_type"], d["ai_confidence"], d["ai_source"],
             d["ai_domain"], json.dumps(d["findings"], ensure_ascii=False) if d["findings"] else None,
             json.dumps(d["flags"]) if d["flags"] else None, json.dumps({"a_result_id": a_id, "b_result_id": b_id}),
             now, model_key, "mock" if model_a.is_mock else None, now, inspection_id))
        cur = conn.execute("UPDATE inspections SET status='AI_DONE' WHERE id=? AND status='PENDING'", (inspection_id,))
        status = db.scalar(conn, "SELECT status FROM inspections WHERE id=?", (inspection_id,))
        db.insert(conn, "inspection_events", {
            "inspection_id": inspection_id, "action": "AI_INSPECT",
            "from_status": "PENDING" if cur.rowcount else status, "to_status": status,
            "comment": ("재검사 사진 " if trigger == "REINSPECT" else "") + ai_pipeline.verdict_text(d, type_name),
            "ai_snapshot": json.dumps(snapshot, ensure_ascii=False), "actor_id": None, "created_at": now})
