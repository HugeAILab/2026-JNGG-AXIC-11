"""대시보드 집계 (TODO.md 8.2). GET /portal/api/stats 가 이 결과를 JSON 1개로 돌려주고 dashboard.js 가 그린다.

기간(days)은 검사 일시(inspected_at) 기준. 비우면 전체.
- 불량률 = 불량 확정 ÷ 확정 (최초 검사자 확정 final_is_defect 기준)
- 재검사 합격률 = REINSPECT_OK ÷ (REINSPECT_OK + REINSPECT_NG) — 기간 안에 검사한 건의 이벤트
- 평균 처리 기간 = 불량 확정(CONFIRM_DEFECT) → 합격(REINSPECT_OK) 평균 일수
- AI-검사자 일치율 = AVG(ai_agree) (최초 확정 기준, AI 판정 전 확정은 NULL 이라 빠짐)
A·B 점수끼리 비교하는 지표는 두지 않는다 (8.2 끝).
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from .. import db, reference
from .inspections import FLAG_LABELS, defect_types

OPEN = ("DEFECT", "REWORK", "REINSPECT")
PERIODS = {"7": 7, "30": 30, "": None}


def _where(days: int | None) -> tuple[str, list]:
    if not days:
        return "1=1", []
    since = (db.now_kst() - timedelta(days=days)).strftime(db.TS_FMT)
    return "i.inspected_at >= ?", [since]


def _rate(a: int, b: int) -> float | None:
    return round(a / b * 100, 1) if b else None


def compute(conn: sqlite3.Connection, days: int | None) -> dict[str, Any]:
    w, p = _where(days)
    types = defect_types(conn)
    name = lambda c: types[c]["name_ko"] if c in types else (c or "-")  # noqa: E731
    q = lambda sql, params=(): db.all_(conn, sql, list(p) + list(params))  # noqa: E731
    one = lambda sql: db.one(conn, sql, p)  # noqa: E731

    # ── KPI 6개 ──
    k = one(f"""SELECT COUNT(*) AS total,
                       SUM(final_is_defect IS NOT NULL) AS confirmed, SUM(final_is_defect = 1) AS defects,
                       SUM(status IN ('DEFECT','REWORK','REINSPECT')) AS open_, AVG(ai_agree) AS agree,
                       SUM(ai_agree IS NOT NULL) AS agree_n
                  FROM inspections i WHERE {w}""")
    ev = one(f"""SELECT SUM(e.action='REINSPECT_OK') AS ok, SUM(e.action='REINSPECT_NG') AS ng
                   FROM inspection_events e JOIN inspections i ON i.id=e.inspection_id WHERE {w}""")
    lead = one(f"""SELECT AVG(julianday(ok.t) - julianday(cd.t)) AS d, COUNT(*) AS n FROM inspections i
                     JOIN (SELECT inspection_id, MIN(created_at) AS t FROM inspection_events WHERE action='CONFIRM_DEFECT'
                           GROUP BY inspection_id) cd ON cd.inspection_id=i.id
                     JOIN (SELECT inspection_id, MAX(created_at) AS t FROM inspection_events WHERE action='REINSPECT_OK'
                           GROUP BY inspection_id) ok ON ok.inspection_id=i.id
                    WHERE {w} AND i.status='PASS'""")
    span = one(f"SELECT MIN(i.inspected_at) AS a, MAX(i.inspected_at) AS b FROM inspections i WHERE {w}")
    kpi = {
        "total": k["total"] or 0, "confirmed": k["confirmed"] or 0, "defects": k["defects"] or 0,
        "defect_rate": _rate(k["defects"] or 0, k["confirmed"] or 0), "open": k["open_"] or 0,
        "reinspect_ok": ev["ok"] or 0, "reinspect_ng": ev["ng"] or 0,
        "reinspect_rate": _rate(ev["ok"] or 0, (ev["ok"] or 0) + (ev["ng"] or 0)),
        "lead_days": round(lead["d"], 1) if lead["d"] is not None else None, "lead_n": lead["n"] or 0,
        "agree": round(k["agree"] * 100, 1) if k["agree"] is not None else None, "agree_n": k["agree_n"] or 0,
        "from": (span["a"] or "")[:10], "to": (span["b"] or "")[:10],
    }

    # ── 일별 검사·불량 추이 (기간 전체, '전체'는 데이터 첫날부터) ──
    rows = {r["d"]: r for r in q(f"""SELECT substr(i.inspected_at,1,10) AS d, COUNT(*) AS n,
                                            SUM(final_is_defect IS NOT NULL) AS c, SUM(final_is_defect=1) AS f
                                       FROM inspections i WHERE {w} GROUP BY d""")}
    daily = []
    if rows:
        end = db.now_kst().date()
        start = (end - timedelta(days=days - 1)) if days else datetime.strptime(min(rows), "%Y-%m-%d").date()
        d = start
        while d <= end:
            r = rows.get(d.isoformat())
            daily.append({"date": d.isoformat(), "n": r["n"] if r else 0, "defects": (r["f"] or 0) if r else 0,
                          "rate": _rate(r["f"] or 0, r["c"] or 0) if r else None})
            d += timedelta(days=1)

    # ── 불량 처리 단계 현황 (불량으로 확정된 건) ──
    st = {r["status"]: r["n"] for r in q(f"""SELECT status, COUNT(*) AS n FROM inspections i
                                             WHERE {w} AND final_is_defect=1 GROUP BY status""")}
    stages = [{"key": s, "n": st.get(s, 0)} for s in ("DEFECT", "REWORK", "REINSPECT", "PASS", "REJECT")]
    waiting = db.scalar(conn, f"SELECT COUNT(*) FROM inspections i WHERE {w} AND status IN ('PENDING','AI_DONE')", p) or 0

    # ── 기한 임박·초과 미결 불량 10건 ──
    today = db.now_kst().strftime("%Y-%m-%d")
    soon = (db.now_kst() + timedelta(days=3)).strftime("%Y-%m-%d")
    due = []
    for r in q(f"""SELECT i.id, i.code, i.status, i.tank_no, i.block, i.joint_no, i.defect_type, i.ai_domain,
                          i.assignee, i.due_date, i.rework_count FROM inspections i
                    WHERE {w} AND i.status IN ('DEFECT','REWORK','REINSPECT')
                    ORDER BY i.due_date IS NULL, i.due_date, i.updated_at LIMIT 10"""):
        d = dict(r)
        d["type_name"] = name(d["defect_type"])
        d["overdue"] = bool(d["due_date"] and d["due_date"] < today)
        d["soon"] = bool(d["due_date"] and today <= d["due_date"] <= soon)
        due.append(d)

    # ── 결함 유형 분석 ──
    by_domain = [{"domain": r["dm"], "label": reference.DOMAIN_LABELS.get(r["dm"], r["dm"]), "n": r["n"], "defects": r["f"] or 0,
                  "rate": _rate(r["f"] or 0, r["c"] or 0)}
                 for r in q(f"""SELECT COALESCE(i.ai_domain,'WELD') AS dm, COUNT(*) AS n,
                                       SUM(final_is_defect IS NOT NULL) AS c, SUM(final_is_defect=1) AS f
                                  FROM inspections i WHERE {w} GROUP BY dm ORDER BY f DESC""")]
    type_rows = [dict(r) for r in q(f"""
        SELECT i.defect_type AS code, COUNT(*) AS n, AVG(i.rework_count) AS rework,
               AVG(CASE WHEN i.status='PASS' THEN julianday(i.closed_at) - julianday(cd.t) END) AS lead
          FROM inspections i LEFT JOIN (SELECT inspection_id, MIN(created_at) AS t FROM inspection_events
                                         WHERE action='CONFIRM_DEFECT' GROUP BY inspection_id) cd ON cd.inspection_id=i.id
         WHERE {w} AND i.final_is_defect=1 AND i.defect_type IS NOT NULL GROUP BY i.defect_type ORDER BY n DESC""")]
    for t in type_rows:
        t["name"] = name(t["code"])
        t["domain"] = types.get(t["code"], {}).get("domain", "WELD")
        t["weld"] = bool(types.get(t["code"], {}).get("in_b_dict"))
        t["rework"] = round(t["rework"] or 0, 2)
        t["lead"] = round(t["lead"], 1) if t["lead"] is not None else None
    mismatch = []
    # 검사자가 확정한 유형(정상 확정은 '정상') 기준. inspections 에 code 칸이 있으므로 별칭은 tcode
    for r in q(f"""SELECT CASE WHEN i.final_is_defect=1 THEN COALESCE(i.defect_type,'WELD_DEFECT') ELSE 'NORMAL' END AS tcode,
                          COUNT(*) AS n, SUM(ai_agree=0) AS bad
                     FROM inspections i WHERE {w} AND ai_agree IS NOT NULL GROUP BY tcode HAVING bad > 0
                    ORDER BY bad DESC, n DESC LIMIT 8"""):
        mismatch.append({"code": r["tcode"], "name": "정상" if r["tcode"] == "NORMAL" else name(r["tcode"]),
                         "n": r["n"], "bad": r["bad"], "rate": _rate(r["bad"], r["n"])})
    flags: Counter = Counter()
    for r in q(f"SELECT ai_flags, ai_confidence FROM inspections i WHERE {w} AND ai_status='DONE'"):
        fl = json.loads(r["ai_flags"]) if r["ai_flags"] else []
        flags.update(fl)
    review = [{"flag": f, "label": FLAG_LABELS[f], "n": flags.get(f, 0)} for f in FLAG_LABELS]

    # ── 구역·작업자 분석 ──
    cells = q(f"""SELECT i.tank_no AS t, i.block AS b, SUM(final_is_defect IS NOT NULL) AS c, SUM(final_is_defect=1) AS f,
                         COUNT(*) AS n FROM inspections i WHERE {w} GROUP BY t, b""")
    tanks = sorted({r["t"] for r in cells})
    blocks = sorted({r["b"] for r in cells})
    heat = {f"{r['t']}|{r['b']}": {"n": r["n"], "c": r["c"] or 0, "f": r["f"] or 0, "rate": _rate(r["f"] or 0, r["c"] or 0)}
            for r in cells}
    process = [{"process": r["wp"], "n": r["n"], "c": r["c"] or 0, "defects": r["f"] or 0, "rate": _rate(r["f"] or 0, r["c"] or 0)}
               for r in q(f"""SELECT i.weld_process AS wp, COUNT(*) AS n, SUM(final_is_defect IS NOT NULL) AS c,
                                     SUM(final_is_defect=1) AS f FROM inspections i
                               WHERE {w} AND i.weld_process IS NOT NULL AND i.weld_process <> '-' GROUP BY wp ORDER BY wp""")]
    welders = []
    for r in q(f"""SELECT i.welder AS wd, COUNT(*) AS n, SUM(final_is_defect IS NOT NULL) AS c, SUM(final_is_defect=1) AS f,
                          GROUP_CONCAT(i.defect_type) AS ts FROM inspections i
                    WHERE {w} AND i.welder IS NOT NULL AND i.welder <> '' GROUP BY wd HAVING f > 0
                    ORDER BY f DESC, (f * 1.0 / c) DESC LIMIT 5"""):
        top = Counter(t for t in (r["ts"] or "").split(",") if t and t != "NORMAL").most_common(1)
        welders.append({"welder": r["wd"], "n": r["n"], "c": r["c"] or 0, "defects": r["f"] or 0,
                        "rate": _rate(r["f"] or 0, r["c"] or 0), "top_type": name(top[0][0]) if top else "-"})

    return {"ok": True, "days": days, "kpi": kpi, "daily": daily, "stages": stages, "waiting_confirm": waiting,
            "due": due, "by_domain": by_domain, "types": type_rows, "mismatch": mismatch, "review": review,
            "heat": {"tanks": tanks, "blocks": blocks, "cells": heat}, "process": process, "welders": welders}
