"""생성 시점의 검사 정보를 고정해 보관하는 보고서 서비스."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from collections import Counter
from html import escape

from .. import config, db
from . import activity_log, inspections

KINDS = {"DEFECT": "결함", "COMPLETION": "완료"}


def default_title(kind: str) -> str:
    today = db.now_kst()
    return f"{today.year}년 {today.month}월 {today.day}일 {KINDS[kind]} 보고서"


def collect(conn, *, source: str, ids: list[int] | None = None, upload_log_id: int | None = None):
    if source == "UPLOAD":
        if not upload_log_id or not db.one(conn, "SELECT id FROM activity_logs WHERE id=? AND log_type='UPLOAD'", (upload_log_id,)):
            raise ValueError("업로드 기록을 찾을 수 없습니다.")
        return [dict(r) for r in db.all_(conn, "SELECT * FROM inspections WHERE upload_log_id=? ORDER BY id", (upload_log_id,))]
    if source != "SELECTION":
        raise ValueError("생성 경로가 올바르지 않습니다.")
    ids = list(dict.fromkeys(int(x) for x in (ids or [])))
    if not ids or len(ids) > 200 or any(x < 1 for x in ids):
        raise ValueError("검사 항목을 1~200건 선택하세요.")
    rows = {r["id"]: dict(r) for r in db.all_(conn, f"SELECT * FROM inspections WHERE id IN ({','.join('?' * len(ids))})", ids)}
    if len(rows) != len(ids):
        raise ValueError("선택한 검사 중 찾을 수 없는 항목이 있습니다.")
    return [rows[x] for x in ids]


def eligible(row: dict, kind: str, *, source: str, scope: str) -> tuple[bool, str]:
    if kind == "COMPLETION":
        return (True, "") if row["status"] == "PASS" and row["rework_count"] >= 1 else (False, "재검사 합격 전")
    if source == "UPLOAD" and scope == "ALL":
        return True, ""
    return (True, "") if row["ai_is_defect"] == 1 or row["final_is_defect"] == 1 else (False, "불량 판정 아님")


def preview(conn, *, kind: str, source: str, ids=None, upload_log_id=None, scope="DEFECT") -> dict:
    if kind not in KINDS or (source == "UPLOAD" and kind != "DEFECT") or scope not in ("ALL", "DEFECT"):
        raise ValueError("보고서 종류 또는 범위가 올바르지 않습니다.")
    rows = collect(conn, source=source, ids=ids, upload_log_id=upload_log_id)
    included, excluded = [], []
    for row in rows:
        ok, reason = eligible(row, kind, source=source, scope=scope)
        (included if ok else excluded).append(row if ok else {"id": row["id"], "code": row["code"], "reason": reason})
    return {"rows": included, "eligible": len(included), "excluded": excluded, "total": len(rows),
            "default_title": default_title(kind)}


def snapshot(conn, row: dict) -> dict:
    d = inspections.detail(conn, row["id"])
    it = d["it"]
    photos = [{"label": "최초 검사", "image_path": it["image_path"]}] if it["image_path"] else []
    model_a = d["ai"].get("A")
    if photos and model_a and model_a.get("candidates") and model_a.get("image_w") and model_a.get("image_h"):
        box = model_a["candidates"][0].get("bbox")
        if isinstance(box, list) and len(box) == 4:
            photos[0]["top_box"] = [round(box[0] / model_a["image_w"], 4), round(box[1] / model_a["image_h"], 4),
                                    round(box[2] / model_a["image_w"], 4), round(box[3] / model_a["image_h"], 4)]
    for e in d["events"]:
        if e["image_path"]:
            photos.append({"label": {"REWORK_DONE": "수정 후", "REINSPECT_OK": "재검사 (합격)",
                                      "REINSPECT_NG": "재검사 (불합격)"}.get(e["action"], "추가 사진"),
                           "image_path": e["image_path"]})
    a = d["analysis"]
    return {"header": {"code": it["code"], "status": it["status"],
                       "defect_type": inspections.type_name(it["defect_type"] or it["ai_defect_type"])},
            "photos": photos,
            "basic": {k: it.get(k) for k in ("project", "tank_no", "block", "joint_no", "work_type", "weld_process",
                                              "weld_position", "welder", "inspector_name", "inspected_at", "upload_log_id")},
            "ai_basic": {**{k: it.get(k) for k in ("ai_status", "ai_is_defect", "ai_defect_type", "ai_domain",
                                                      "ai_source", "ai_confidence", "ai_error", "final_is_defect")},
                         "flags": it["flags"], "findings": it["findings"], "is_mock": it["is_mock"]},
            "analysis": ({**{k: a.get(k) for k in ("status", "explain_source", "description", "causes", "prevention", "checks")},
                          "rag_hits": [{k: hit.get(k) for k in ("no", "source", "doc", "section")}
                                       for hit in (a.get("rag_hits") or []) if isinstance(hit, dict)]} if a else None),
            "events": [{k: e.get(k) for k in ("action", "from_status", "to_status", "created_at", "actor_name", "comment", "image_path")} for e in d["events"]]}


def analysis_for(conn, rows: list[dict], snaps: list[dict], kind: str) -> tuple[dict, str]:
    types = Counter(x["header"]["defect_type"] for x in snaps)
    areas = Counter(x["ai_basic"]["ai_domain"] or "미분류" for x in snaps)
    repeated = sum(1 for x in snaps if sum(e["action"] == "REWORK_DONE" for e in x["events"]) >= 2)
    causes = Counter(c for x in snaps if x["analysis"] for c in (x["analysis"].get("causes") or []) if isinstance(c, str))
    prevention = Counter(c for x in snaps if x["analysis"] for c in (x["analysis"].get("prevention") or []) if isinstance(c, str))
    attributes = {}
    for field in ("tank_no", "block", "weld_process", "welder"):
        counts = Counter(x["basic"].get(field) for x in snaps if x["basic"].get(field) not in (None, "", "-"))
        attributes[field] = [{"value": v, "count": n} for v, n in counts.items() if n >= len(snaps) / 2]
    selected = {r["id"] for r in rows}
    since = (db.now_kst() - timedelta(days=30)).strftime(db.TS_FMT)
    past = [dict(r) for r in db.all_(conn, """SELECT id, tank_no, block, joint_no, welder, ai_domain,
        COALESCE(defect_type, ai_defect_type) dtype FROM inspections WHERE inspected_at>=? AND
        (ai_is_defect=1 OR final_is_defect=1)""", (since,)) if r["id"] not in selected]
    recurrence = {"same_block_type": 0, "same_tank_area": 0, "same_welder_type": 0}
    for r in rows:
        dtype = r.get("defect_type") or r.get("ai_defect_type")
        recurrence["same_block_type"] += sum(x["block"] == r["block"] and x["dtype"] == dtype for x in past)
        recurrence["same_tank_area"] += sum(x["tank_no"] == r["tank_no"] and x["ai_domain"] == r["ai_domain"] for x in past)
        if r.get("ai_domain") == "WELD" and r.get("welder"):
            recurrence["same_welder_type"] += sum(x["welder"] == r["welder"] and x["dtype"] == dtype for x in past)
    result = {"type_counts": dict(types), "area_counts": dict(areas), "common_attributes": attributes,
              "repeat_rework": repeated, "recurrence_30d": recurrence,
              "common_causes": [x for x, _ in causes.most_common(3)], "prevention": [x for x, _ in prevention.most_common(3)]}
    if kind == "COMPLETION":
        durations = []
        for snap in snaps:
            events = snap["events"]
            start = next((e["created_at"] for e in events if e["action"] == "CONFIRM_DEFECT"), None)
            end = next((e["created_at"] for e in reversed(events) if e["action"] == "REINSPECT_OK"), None)
            if start and end:
                try: durations.append((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 86400)
                except ValueError: pass
        distribution = Counter(sum(e["action"] == "REWORK_DONE" for e in x["events"]) for x in snaps)
        result["completion"] = {"average_days": round(sum(durations)/len(durations), 1) if durations else None,
                                "rework_distribution": dict(distribution),
                                "repeated_codes": [x["header"]["code"] for x in snaps if sum(e["action"] == "REWORK_DONE" for e in x["events"]) >= 2]}
    text = f"{len(snaps)}건의 검사 항목을 포함합니다. 주요 유형은 {types.most_common(1)[0][0]}이며, 재작업 2회 이상 항목은 {repeated}건입니다."
    if result["common_causes"]:
        text += " 주요 원인: " + "; ".join(result["common_causes"]) + "."
    if result["prevention"]:
        text += " 권고: " + "; ".join(result["prevention"]) + "."
    if kind == "COMPLETION" and result["completion"]["average_days"] is not None:
        text += f' 평균 처리 기간은 {result["completion"]["average_days"]}일입니다.'
    return result, text


def summary_text(analysis: dict, fallback: str) -> tuple[str, int]:
    """집계된 수치만 외부 요약기에 보내고 응답이 불안정하면 규칙 문장 사용."""
    if config.AI_MODE != "live" or not (config.AGENTSTREAM_API_KEY and config.AGENTSTREAM_RAG_CHATBOT_ID):
        return fallback, 0
    try:
        from .ai_analysis import _ask_agentstream
        prompt = "다음 보고서 집계 JSON만 근거로 한국어 요약 3~5문장과 권고 2~3개를 존댓말로 작성하세요. 입력에 없는 수치·판정은 만들지 마세요.\n" + json.dumps(analysis, ensure_ascii=False)
        text = _ask_agentstream(prompt).strip()[:2000]
        return (text, 1) if len(text) >= 20 else (fallback, 0)
    except Exception:
        return fallback, 0


def body_html(snaps: list[dict], report_no: str, summary: str) -> str:
    def line(label, value):
        return f"<dt>{escape(label)}</dt><dd>{escape(str(value if value is not None else '-'))}</dd>"
    out = [f'<section class="report-summary"><h2>문서 요약</h2><p>{escape(summary)}</p></section>']
    for n, s in enumerate(snaps, 1):
        h, b, ai, a = s["header"], s["basic"], s["ai_basic"], s["analysis"]
        out += [f'<article class="report-item"><h2>항목 {n} / {len(snaps)} · {escape(h["code"])} · {escape(h["defect_type"])} · {escape(h["status"])}</h2>',
                '<h3>① 사진</h3>']
        for p in s["photos"]:
            from .uploads import storage_url
            url = storage_url(p["image_path"], thumb=True)
            if url:
                box = p.get("top_box")
                overlay = ''
                if box:
                    x0, y0, x1, y1 = [max(0, min(1, float(v))) for v in box]
                    overlay = f'<span class="report-box" style="left:{x0*100:.1f}%;top:{y0*100:.1f}%;width:{(x1-x0)*100:.1f}%;height:{(y1-y0)*100:.1f}%"></span>'
                out.append(f'<figure><span class="report-photo"><img src="{escape(url, quote=True)}" alt="{escape(p["label"])}">{overlay}</span><figcaption>{escape(p["label"])}</figcaption></figure>')
        if not s["photos"]: out.append("<p>사진 없음</p>")
        out.append('<h3>② 기본 정보</h3><dl class="kv">')
        for k, label in (("project", "프로젝트"), ("tank_no", "탱크"), ("block", "블록"), ("joint_no", "위치"),
                         ("work_type", "작업 종류"), ("weld_process", "용접법"), ("weld_position", "자세"),
                         ("welder", "용접사"), ("inspector_name", "검사자"), ("inspected_at", "검사 일시"),
                         ("upload_log_id", "업로드 번호")):
            out.append(line(label, b.get(k)))
        out.append('</dl><h3>③-1 AI 판정 (기본만)</h3><dl class="kv">')
        for label, value in (("판정", "불량" if ai["ai_is_defect"] else "정상 / 검출 없음"),
                             ("유형", h["defect_type"]), ("작업 영역", ai["ai_domain"]),
                             ("판정 모델", ai["ai_source"]), ("모델 점수 — 참고용", ai["ai_confidence"]),
                             ("검토 필요", ", ".join(ai["flags"]))):
            out.append(line(label, value))
        out.append('</dl>')
        if ai["final_is_defect"] is None: out.append('<p>AI 추정 · 미확정</p>')
        out.append('<h3>③-2 불량 설명 · 원인 · 방지</h3>')
        if a:
            out.append('<p>AI 초안 · ' + ('근거 검색' if a.get('explain_source') == 'LLM' else '템플릿') + '</p>')
            out.append(f'<p>{escape(a.get("description") or "AI 분석 없음")}</p>')
            for key, label in (("causes", "원인"), ("prevention", "방지"), ("checks", "추가 확인")):
                out.append(f'<h4>{label}</h4><ul>')
                out += [f'<li>{escape(str(v))}</li>' for v in (a.get(key) or [])]
                out.append('</ul>')
            if a.get("rag_hits"):
                out.append('<h4>근거 출처</h4><ul>')
                for hit in a["rag_hits"]:
                    out.append(f'<li>[{escape(str(hit.get("no") or "유형 가이드"))}] {escape(str(hit.get("doc") or "-"))} · {escape(str(hit.get("section") or "-"))}</li>')
                out.append('</ul>')
        else: out.append('<p>AI 분석 없음</p>')
        out.append('<h3>⑥ 처리 이력</h3><table class="tbl"><thead><tr><th>일시</th><th>행동</th><th>상태</th><th>처리자</th><th>내용</th></tr></thead><tbody>')
        for e in s["events"]:
            out.append('<tr>' + ''.join(f'<td>{escape(str(v or "-"))}</td>' for v in
                       (e["created_at"], inspections.EVENT_LABELS.get(e["action"], e["action"]),
                        f'{e["from_status"] or "-"} → {e["to_status"] or "-"}', e["actor_name"] or "AI", e["comment"])) + '</tr>')
        out.append('</tbody></table></article>')
    return '\n'.join(out)


def create(conn, *, kind: str, title: str, source: str, ids=None, upload_log_id=None, scope="DEFECT", user: dict):
    title = title.strip()
    if not 1 <= len(title) <= 120:
        raise ValueError("제목은 1~120자로 입력하세요.")
    p = preview(conn, kind=kind, source=source, ids=ids, upload_log_id=upload_log_id, scope=scope)
    if not p["rows"]:
        raise ValueError("보고서에 포함할 수 있는 검사 항목이 없습니다.")
    snaps = [snapshot(conn, x) for x in p["rows"]]
    analysis, fallback = analysis_for(conn, p["rows"], snaps, kind)
    summary, llm_used = summary_text(analysis, fallback)
    prefix = "DR" if kind == "DEFECT" else "CR"
    stem = f'{prefix}-{db.now_kst():%Y%m%d}-'
    last = db.scalar(conn, "SELECT MAX(report_no) FROM reports WHERE report_no LIKE ?", (stem + '%',))
    report_no = stem + f'{int(last[-3:]) + 1 if last else 1:03d}'
    counts = Counter(x["ai_basic"]["ai_defect_type"] for x in snaps if x["ai_basic"]["ai_defect_type"])
    report_id = db.insert(conn, "reports", {"report_no": report_no, "type": kind, "title": title, "source": source,
        "upload_log_id": upload_log_id if source == "UPLOAD" else None, "item_count": len(snaps),
        "top_defect_type": counts.most_common(1)[0][0] if counts else None,
        "analysis_json": json.dumps(analysis, ensure_ascii=False), "analysis_text": summary, "llm_used": llm_used,
        "body_html": body_html(snaps, report_no, summary), "created_by": user["id"], "created_at": db.now_str()})
    for n, (row, snap) in enumerate(zip(p["rows"], snaps), 1):
        db.insert(conn, "report_items", {"report_id": report_id, "inspection_id": row["id"],
                                           "sort_order": n, "snapshot_json": json.dumps(snap, ensure_ascii=False)})
    activity_log.log("REPORT", "REPORT_CREATE", f"보고서 {report_no} 생성", {"type": kind, "item_count": len(snaps)},
                     ("report", report_id), user_id=user["id"], conn=conn)
    return report_id, p["excluded"]
