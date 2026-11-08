"""불량 AI 판정 뒤 근거 검색과 설명 초안 저장.

용접은 팀 작성 DOC와 과거 CASE를 검색한다. AgentStream이 구조화된 답을
반환하면 사용하고, 그렇지 않으면 근거와 유형 가이드로 검토용 초안을 만든다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx

from .. import config, db
from . import activity_log
from .inspections import ai_results, defect_types, parse_json

log = logging.getLogger("weldsight.analysis")
SLOTS = asyncio.Semaphore(2)
DOC_DIR = config.APP_DIR / "seed" / "rag_docs"
LESSON_CODES = ("CRACK", "POROSITY", "UNDERCUT", "LACK_OF_FUSION", "INCOMPLETE_PENETRATION",
                "SLAG_INCLUSION", "OVERLAP", "SPATTER", "BEAD_IRREGULAR")
DOC_FILES = (("용접결함_상세학습.md", "용접 결함별 상세 학습자료"),
             ("용접부_검수_기초교육.md", "용접부 검수 기초교육"))
_resumed: set[asyncio.Task] = set()


async def connection_health() -> dict:
    """외부 RAG 챗봇 연결을 확인한다. 키 값은 응답에 포함하지 않는다."""
    configured = bool(config.AGENTSTREAM_BASE_URL and config.AGENTSTREAM_API_KEY and
                      config.AGENTSTREAM_RAG_CHATBOT_ID)
    if not configured:
        return {"configured": False, "ready": None, "message": "외부 RAG 미설정 · 로컬 교육자료 검색 사용"}
    url = (f"{config.AGENTSTREAM_BASE_URL.rstrip('/')}/open/v1/chatbots/"
           f"{config.AGENTSTREAM_RAG_CHATBOT_ID}/health")
    try:
        async with httpx.AsyncClient(timeout=min(config.RAG_TIMEOUT, 10)) as client:
            response = await client.get(url, headers={"X-API-Key": config.AGENTSTREAM_API_KEY})
            response.raise_for_status()
            body = response.json()
        state = str(body.get("status", "")).lower() if isinstance(body, dict) else ""
        ready = state in ("healthy", "ready", "ok") if state else True
        return {"configured": True, "ready": ready,
                "message": "챗봇 연결 정상" if ready else f"챗봇 상태: {state or '확인 필요'}"}
    except (httpx.HTTPError, ValueError) as exc:
        return {"configured": True, "ready": False,
                "message": f"연결 실패: {type(exc).__name__}"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _clean(text: str) -> str:
    text = re.sub(r"!\[[^]]*\]\([^)]*\)", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def seed_docs(conn: sqlite3.Connection) -> int:
    """배포에 포함한 팀 문서를 절 단위로 DOC 조각에 반영한다. 재실행해도 중복되지 않는다."""
    known = {r[0] for r in conn.execute("SELECT code FROM defect_types")}
    count = 0
    for filename, title in DOC_FILES:
        path = DOC_DIR / filename
        if not path.is_file():
            continue
        section = title
        parent = title
        code: str | None = None
        lines: list[str] = []

        def save() -> None:
            nonlocal count
            body = _clean("\n".join(lines))
            if len(body) < 25:
                return
            row = db.one(conn, "SELECT id, text FROM rag_chunks WHERE source='DOC' AND doc_title=? AND section=?",
                         (title, section))
            if row:
                if row["text"] != body:
                    conn.execute("UPDATE rag_chunks SET text=?, defect_type=?, updated_at=? WHERE id=?",
                                 (body, code, db.now_str(), row["id"]))
            else:
                db.insert(conn, "rag_chunks", {"doc_title": title, "section": section, "defect_type": code,
                          "text": body, "source": "DOC", "updated_at": db.now_str()})
            count += 1

        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## ") or line.startswith("### "):
                save()
                lines = []
                heading = line.lstrip("# ").strip()
                if line.startswith("## "):
                    parent = heading
                    section = heading
                else:
                    section = f"{parent} > {heading}"
                lesson = re.match(r"L(0[1-9])\b", heading) or re.match(r"L(0[1-9])\b", parent)
                if lesson:
                    code = LESSON_CODES[int(lesson.group(1)) - 1]
                elif line.startswith("## "):
                    code = None
                if code not in known:
                    code = None
            else:
                lines.append(line)
        save()
    return count


def build_request(conn: sqlite3.Connection, inspection_id: int) -> dict:
    row = db.one(conn, "SELECT * FROM inspections WHERE id=?", (inspection_id,))
    if row is None:
        raise ValueError("검사 건을 찾을 수 없습니다.")
    it = dict(row)
    if it.get("ai_status") != "DONE" or not it.get("ai_is_defect"):
        raise ValueError("불량 AI 판정이 완료된 건만 분석할 수 있습니다.")
    code = it.get("ai_defect_type")
    guide = defect_types(conn).get(code) or {}
    models = ai_results(conn, inspection_id)
    a, b = models["A"], models["B"]
    top = (a or {}).get("candidates", [None])[0] if a and a.get("candidates") else None
    b_keywords = list((b or {}).get("keywords") or [])[:8]
    terms = [guide.get("name_ko") or code or "불량"] * 2
    if b and b.get("scores"):
        for other, score in sorted(b["scores"].items(), key=lambda x: -x[1]):
            if other != code and other != "NORMAL" and score >= config.MODEL_B_THRESHOLD:
                terms.append((defect_types(conn).get(other) or {}).get("name_ko") or other)
    terms.extend(b_keywords)
    terms.extend(v for v in ((b or {}).get("location"), (top or {}).get("class"), it.get("weld_process")) if v)
    return {"inspection_id": inspection_id, "code": it["code"],
            "path": "WELD_B_RAG" if it.get("ai_domain") == "WELD" else "A_GUIDE",
            "context": {k: it.get(k) for k in ("tank_no", "block", "joint_no", "work_type", "weld_process")},
            "final": {"domain": it.get("ai_domain"), "defect_type": code, "type_name": guide.get("name_ko") or code,
                      "confidence": it.get("ai_confidence"), "source": it.get("ai_source"),
                      "flags": parse_json(it.get("ai_flags"), []), "findings": parse_json(it.get("ai_findings"), [])},
            "model_a": {"id": a["id"], "class": a.get("top_class"), "score": a.get("top_score"),
                        "area_ratio": (top or {}).get("area_ratio"), "bbox_xyxy": (top or {}).get("bbox")}
                       if a else None,
            "model_b": {"id": b["id"], "keywords": b_keywords, "observation": b.get("observation"),
                        "location": b.get("location"), "scores": b.get("scores"),
                        "image_quality": b.get("image_quality")} if b and b.get("status") == "OK" else None,
            "guide": {k: guide.get(k) for k in ("code", "name_ko", "description", "causes", "remedies", "keywords")},
            "query": " ".join(str(v) for v in terms if v and v != "-")[:1000]}


def search_hits(conn: sqlite3.Connection, req: dict, limit: int = 5) -> list[dict]:
    """같은 유형의 교육 절과 이전 사례를 찾는다. 현재 사례 자신은 제외한다."""
    if req["path"] != "WELD_B_RAG":
        return []
    code = req["final"]["defect_type"]
    if not code:
        return []
    docs = db.all_(conn, """SELECT * FROM rag_chunks WHERE source='DOC' AND defect_type=?
                            ORDER BY id""", (code,))
    # 정의·식별, 원인 확인, 보수 검토 절을 고르게 선택한다.
    priority = {" B ": 0, " D ": 1, " E ": 2, " C ": 3, " A ": 4, " F ": 5}
    def order(row):
        section = f" {row['section']} "
        return (next((rank for marker, rank in priority.items() if marker in section), 9), row["id"])
    docs = sorted(docs, key=order)[:3]
    cases = db.all_(conn, """SELECT * FROM rag_chunks WHERE source='CASE' AND defect_type=?
                             AND inspection_id<>? ORDER BY updated_at DESC, id DESC LIMIT 2""",
                    (code, req["inspection_id"]))
    rows = list(docs) + list(cases)
    return [{"no": n, "source": r["source"], "doc": r["doc_title"], "section": r["section"],
             "text": r["text"][:1200], "score": 1.0 if r["source"] == "DOC" else 0.7,
             "inspection_id": r["inspection_id"]} for n, r in enumerate(rows[:limit], 1)]


def _items(text: str | None, limit: int = 3) -> list[str]:
    return [part.strip(" ·-\n") for part in re.split(r"[,，;；]|\n", text or "") if part.strip(" ·-\n")][:limit]


def template_explain(req: dict, hits: list[dict]) -> dict:
    """근거와 유형 가이드만 쓰는 보수적 초안. 측정값이나 합격 여부를 만들어내지 않는다."""
    name = req["final"]["type_name"] or "불량"
    guide = req["guide"]
    cite = "[1]" if hits else "[유형 가이드]"
    description = (f"AI 판정은 {name} 후보입니다. 유형 가이드에는 {name}을(를) "
                   f"‘{guide.get('description') or '추가 확인이 필요한 형상'}’으로 설명합니다[유형 가이드]. "
                   f"원본 사진과 필요한 검사 결과를 대조해 적용 기준에 따라 판정하세요{cite}.")
    if "AB_DIFFER" in req["final"]["flags"]:
        description = ("선별 모델은 용접 불량 후보를 찾았으나 용접 상세 분석에서는 뚜렷한 결함이 확인되지 않았습니다. "
                       "원본과 현장 상태를 추가 확인하세요[모델 결과]. " + description)
    causes = [f"가능 원인 점검: {item}[유형 가이드]" for item in _items(guide.get("causes"))]
    prevention = [f"작업·보수 조건 검토: {item}[유형 가이드]" for item in _items(guide.get("remedies"))]
    checks = [f"원본과 현장 측정 결과를 대조하고 적용 검사 기준을 확인하세요{cite}."]
    if req.get("model_b") and req["model_b"].get("image_quality") not in (None, "OK"):
        checks.append("사진 품질 경고가 있어 재촬영 또는 현장 확인이 필요합니다[모델 결과].")
    return {"description": description[:1500], "causes": causes, "prevention": prevention,
            "checks": checks[:2]}


def _parse_external(text: str, hit_count: int) -> dict:
    """AgentStream의 구조화된 JSON만 허용. 자유 서술은 저장하지 않는다."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    body = json.loads(fenced.group(1) if fenced else text.strip())
    if isinstance(body.get("explanation"), dict):
        body = body["explanation"]
    desc = body.get("description")
    if isinstance(desc, list) and all(isinstance(v, str) for v in desc):
        desc = " ".join(desc)
    lists = [body.get(k) for k in ("causes", "prevention", "checks")]
    if not isinstance(desc, str) or not desc.strip() or any(not isinstance(v, list) for v in lists):
        raise ValueError("구조화된 설명 필드가 없습니다.")
    valid = {f"[{n}]" for n in range(1, hit_count + 1)}
    result = {"description": desc.strip()[:1500]}
    for key, raw in zip(("causes", "prevention", "checks"), lists):
        result[key] = [v.strip()[:500] for v in raw[:3 if key != "checks" else 2] if isinstance(v, str) and v.strip()]
    all_text = [result["description"], *result["causes"], *result["prevention"], *result["checks"]]
    if not valid or any(not any(c in item for c in valid) for item in all_text):
        raise ValueError("근거 번호가 없는 설명입니다.")
    if any(word in result["description"] for word in ("합격입니다", "불합격입니다", "확정합니다")):
        raise ValueError("AI가 판정을 단정했습니다.")
    return result


async def _external(req: dict, hits: list[dict]) -> dict:
    """팀 근거 절을 AgentStream에 보내 JSON 초안을 요청한다."""
    # AgentStream은 질문 중간의 JSON을 표 데이터 분석 입력으로 오인할 수 있어 입력은 일반 문장으로 전달한다.
    parts = ["용접 결함의 검토용 설명을 작성하세요. 아래 모델 결과와 번호가 붙은 팀 근거만 사용하세요.",
             "응답은 JSON 코드 블록 하나로 작성하세요. 필드는 description, causes, prevention, checks입니다. "
             "description은 문자열, 나머지 세 필드는 문자열 배열입니다. 각 문장과 목록 항목 끝에 근거 번호를 붙이세요. "
             "입력에 없는 수치·원인·합격 판정은 만들지 마세요.",
             f"AI 판정 유형: {req['final']['type_name']}"]
    if req["final"]["flags"]:
        parts.append("모델 확인 표시: " + ", ".join(req["final"]["flags"]))
    b = req.get("model_b") or {}
    if b.get("observation"):
        parts.append("모델 B 관찰: " + str(b["observation"])[:300])
    if b.get("keywords"):
        parts.append("모델 B 키워드: " + ", ".join(map(str, b["keywords"][:8])))
    for h in hits:
        content = re.sub(r"\s+", " ", h["text"][:900]).strip()
        parts.append(f"근거 [{h['no']}] {h['source']} · {h['section']}: {content}")
    prompt = "\n".join(parts)
    return _parse_external(await asyncio.to_thread(_ask_agentstream, prompt), len(hits))


def _ask_agentstream(prompt: str) -> str:
    """RAG 챗봇의 세션·메시지 API를 호출한다. 이 모듈만으로 배포 가능하다."""
    base = config.AGENTSTREAM_BASE_URL.rstrip("/")
    bot = config.AGENTSTREAM_RAG_CHATBOT_ID
    headers = {"X-API-Key": config.AGENTSTREAM_API_KEY, "Content-Type": "application/json"}
    with httpx.Client(timeout=config.RAG_TIMEOUT) as client:
        response = client.post(f"{base}/open/v1/chatbots/{bot}/sessions",
                               headers=headers, json={"query": prompt})
        response.raise_for_status()
        session_id = response.json().get("session_id")
        if not session_id:
            raise ValueError("RAG 세션 ID가 없습니다.")
        parts: list[str] = []
        with client.stream("POST", f"{base}/open/v1/chat-sessions/{session_id}/messages",
                           headers=headers, json={"message": prompt}) as stream:
            stream.raise_for_status()
            for line in stream.iter_lines():
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "assistant.delta":
                    parts.append(event.get("content") or "")
                elif event.get("event") == "turn.failed":
                    raise RuntimeError("AgentStream 대화 실행 실패")
    return "".join(parts).strip()


def upsert_case(conn: sqlite3.Connection, inspection_id: int) -> None:
    row = db.one(conn, "SELECT * FROM inspections WHERE id=?", (inspection_id,))
    if not row or not row["ai_is_defect"] or row["ai_domain"] != "WELD":
        return
    analysis = db.one(conn, "SELECT description FROM ai_analyses WHERE inspection_id=? AND status IN ('DONE','PARTIAL')"
                            " ORDER BY id DESC LIMIT 1", (inspection_id,))
    if not analysis:
        return
    it = dict(row)
    types = defect_types(conn)
    code = it.get("defect_type") or it.get("ai_defect_type")
    if code not in types:
        return
    name = types[code]["name_ko"]
    details = [f"{name} · 탱크 {it.get('tank_no') or '-'} · 블록 {it.get('block') or '-'} · "
               f"위치 {it.get('joint_no') or '-'} · 용접법 {it.get('weld_process') or '-'}",
               f"AI 초안: {analysis['description'] or '-'}"]
    for label, key in (("검사자 소견", "finding"), ("조치 계획", "action_plan"),
                       ("조치 결과", "action_result")):
        if it.get(key):
            details.append(f"{label}: {it[key][:500]}")
    if it.get("status") in ("PASS", "REJECT"):
        details.append(f"최종 상태: {it['status']}")
    text = "\n".join(details)[:2500]
    old = db.one(conn, "SELECT id FROM rag_chunks WHERE source='CASE' AND inspection_id=?", (inspection_id,))
    if old:
        conn.execute("UPDATE rag_chunks SET doc_title=?, section=?, defect_type=?, text=?, updated_at=? WHERE id=?",
                     (it["code"], f"{name} · {it.get('block') or '-'}", code, text, db.now_str(), old["id"]))
    else:
        db.insert(conn, "rag_chunks", {"doc_title": it["code"], "section": f"{name} · {it.get('block') or '-'}",
                  "defect_type": code, "text": text, "source": "CASE", "inspection_id": inspection_id,
                  "updated_at": db.now_str()})


async def run_analysis(inspection_id: int, trigger: str = "AUTO", *, reuse_id: int | None = None) -> dict | None:
    """분석 실패를 PARTIAL 초안으로 기록하며 기존 AI 판정·업무 상태를 변경하지 않는다."""
    async with SLOTS:
        started = time.monotonic()
        with db.connect() as conn:
            req = build_request(conn, inspection_id)
            seed_docs(conn)
            if reuse_id is None:
                reuse_id = db.insert(conn, "ai_analyses", {
                    "inspection_id": inspection_id, "trigger": trigger, "status": "RUNNING", "path": req["path"],
                    "model_result_ids": _json([x["id"] for x in (req["model_a"], req["model_b"]) if x]),
                    "keywords_json": _json((req["model_b"] or {}).get("keywords") or []),
                    "rag_query": req["query"], "created_at": db.now_str()})
            hits = search_hits(conn, req)
        explanation = template_explain(req, hits)
        status, explain_source = "PARTIAL", "TEMPLATE"
        error = None
        if req["path"] == "WELD_B_RAG" and hits and config.AI_MODE != "mock" and \
                config.AGENTSTREAM_BASE_URL and config.AGENTSTREAM_API_KEY and config.AGENTSTREAM_RAG_CHATBOT_ID:
            try:
                explanation = await _external(req, hits)
                status, explain_source = "DONE", "LLM"
            except Exception as exc:  # noqa: BLE001 — 외부 장애·형식 오류 시 템플릿으로 대체
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.warning("RAG 외부 초안 대체 · 검사 %s · %s", inspection_id, error)
        with db.connect() as conn:
            conn.execute("""UPDATE ai_analyses SET status=?, rag_source=?, rag_hits_json=?, explain_source=?,
                        description=?, causes_json=?, prevention_json=?, checks_json=?, elapsed_ms=?, error=?,
                        finished_at=? WHERE id=?""",
                         (status, "LOCAL" if hits else "NONE", _json(hits), explain_source,
                          explanation["description"], _json(explanation["causes"]),
                          _json(explanation["prevention"]), _json(explanation["checks"]),
                          int((time.monotonic() - started) * 1000), error, db.now_str(), reuse_id))
            if req["path"] == "WELD_B_RAG":
                upsert_case(conn, inspection_id)
            activity_log.log("AI", "ANALYSIS_DONE", f"불량 설명 초안 · {req['code']} · {status}",
                             {"inspection_id": inspection_id, "analysis_id": reuse_id, "source": explain_source,
                              "hits": len(hits), "rag_used": bool(hits),
                              "rag_source": "LOCAL" if hits else "NONE"},
                             ("inspection", inspection_id), conn=conn)
        return {"id": reuse_id, "status": status, "source": explain_source, "hits": hits}


async def _resume_one(inspection_id: int, analysis_id: int, trigger: str) -> None:
    try:
        await run_analysis(inspection_id, trigger, reuse_id=analysis_id)
    except Exception:  # noqa: BLE001
        log.exception("RAG 분석 재개 실패 · 검사 %s", inspection_id)
        with db.connect() as conn:
            conn.execute("UPDATE ai_analyses SET status='ERROR', error=?, finished_at=? WHERE id=?",
                         ("재개 실패", db.now_str(), analysis_id))


def resume() -> int:
    """재시작 전에 RUNNING으로 남은 분석을 이어서 실행한다."""
    with db.connect() as conn:
        rows = db.all_(conn, """SELECT a.id, a.inspection_id, a.trigger FROM ai_analyses a
                               WHERE a.status='RUNNING' AND a.id=(SELECT MAX(id) FROM ai_analyses
                                 WHERE inspection_id=a.inspection_id)""")
    for row in rows:
        task = asyncio.get_running_loop().create_task(_resume_one(row["inspection_id"], row["id"], row["trigger"]))
        _resumed.add(task)
        task.add_done_callback(_resumed.discard)
    return len(rows)
