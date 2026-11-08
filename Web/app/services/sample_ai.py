"""샘플(합성) 검사 건의 모의 AI 파이프라인 결과 만들기 (TODO.md 7.5, 8.10).

시드와 스키마 버전 2 마이그레이션이 함께 쓴다. 모델별 결과(ai_model_results)가 아직 없는 샘플 검사 건
(is_sample=1)만 채우므로 여러 번 불러도 안전하다. 실제 모델을 부르지 않으며 화면에는 '샘플(모의) 판정'으로 표시된다.

입력: 1단계 시드가 만든 inspections.ai_*(모의 판정 코드·신뢰도)와 검사자 확정 결과
출력: 모델 A 결과 1행 (+ 용접 불량이면 모델 B 결과 1행) + inspections 의 파이프라인 칸(ai_domain·ai_source·ai_findings·ai_flags…)
"""
from __future__ import annotations

import json
import random
import sqlite3

from .. import reference

POSITIONS = ["비드 중앙부", "비드 시작부", "비드 종단부", "크레이터 부근", "상부 토우", "하부 토우", "루트 측"]
IMAGE_W, IMAGE_H = 1280, 960  # 샘플은 사진이 없으므로 표시용 가상 크기

# 내부 코드 → 모델 A 분류명 후보 (용접 세부 유형은 A 가 구분하지 못하므로 '용접불량')
_CODE_TO_A: dict[str, list[str]] = {"POROSITY": ["용접블로우홀", "용접블로우홀", "용접불량"], "CRACK": ["균열"]}
for _a, _src, _dom, _is_def, _maps, _trig in reference.A_CLASS_MAP:
    if _is_def and _maps not in _CODE_TO_A and _dom != "WELD":
        _CODE_TO_A[_maps] = [_a]

CONFUSE = {  # B 점수에서 중간 점수를 줄 헷갈리기 쉬운 유형
    "LACK_OF_FUSION": ["INCOMPLETE_PENETRATION", "SLAG_INCLUSION"], "INCOMPLETE_PENETRATION": ["LACK_OF_FUSION"],
    "UNDERCUT": ["OVERLAP", "BEAD_IRREGULAR"], "OVERLAP": ["UNDERCUT", "BEAD_IRREGULAR"],
    "POROSITY": ["SLAG_INCLUSION", "SPATTER"], "SLAG_INCLUSION": ["POROSITY", "LACK_OF_FUSION"],
    "SPATTER": ["POROSITY", "BEAD_IRREGULAR"], "BEAD_IRREGULAR": ["UNDERCUT", "OVERLAP"],
    "CRACK": ["LACK_OF_FUSION", "UNDERCUT"],
}


def _clamp(x: float, lo: float = 0.0, hi: float = 0.99) -> float:
    return round(max(lo, min(hi, x)), 3)


def _a_class_for(code: str, rng: random.Random) -> str:
    return rng.choice(_CODE_TO_A.get(code, ["용접불량"]))


def _b_scores(top: str | None, top_score: float, rng: random.Random) -> dict[str, float]:
    scores = {c: _clamp(rng.uniform(0.01, 0.12)) for c in reference.B_DICT_CODES}
    if top is None:  # B 가 '정상'으로 보는 경우
        scores["NORMAL"] = _clamp(rng.uniform(0.65, 0.9))
        return scores
    scores["NORMAL"] = _clamp((1 - top_score) * rng.uniform(0.3, 0.6))
    for c in CONFUSE.get(top, []):
        scores[c] = _clamp(rng.uniform(0.18, 0.45))
    scores[top] = _clamp(top_score)
    return scores


def backfill(conn: sqlite3.Connection) -> int:
    """A·B 결과가 없는 샘플 검사 건을 채운다. 채운 건수를 돌려준다."""
    types = {r["code"]: dict(r) for r in conn.execute("SELECT * FROM defect_types")}
    a_map = {r["a_class"]: dict(r) for r in conn.execute("SELECT * FROM a_class_map")}
    rows = conn.execute("""
        SELECT i.* FROM inspections i
         WHERE i.is_sample = 1
           AND NOT EXISTS (SELECT 1 FROM ai_model_results r WHERE r.inspection_id = i.id)
         ORDER BY i.id""").fetchall()
    n = 0
    for r in rows:
        _fill_one(conn, dict(r), types, a_map)
        n += 1
    return n


def _fill_one(conn: sqlite3.Connection, it: dict, types: dict, a_map: dict) -> None:
    rng = random.Random(it["id"] * 7919 + 2026)
    created = it["ai_at"] or it["inspected_at"]
    code_hint = it["ai_defect_type"] if it["ai_is_defect"] else (it["defect_type"] or it["ai_defect_type"])
    domain = (types.get(code_hint or "", {}).get("domain") if code_hint and code_hint != "NORMAL" else None) \
        or it.get("work_type") or "WELD"
    if domain not in reference.DOMAINS:
        domain = "WELD"
    base_conf = it["ai_confidence"] or 0.8
    flags: list[str] = []
    findings: list[dict] = []
    b_id = None
    b_top = None

    if it["ai_is_defect"]:
        code = it["ai_defect_type"] or "WELD_DEFECT"
        a_class = _a_class_for(code, rng)
        a_score = _clamp(base_conf + rng.uniform(-0.08, 0.05), 0.5)
        cand = [{"no": 1, "class": a_class, "score": a_score, "bbox": _bbox(rng), "code": a_map[a_class]["maps_to"],
                 "domain": domain, "area_ratio": round(rng.uniform(0.004, 0.06), 4)}]
        extra = None
        if rng.random() < 0.08:  # 같은 사진에서 다른 불량이 함께 발견됨
            extra_class = rng.choice(["스크래치", "도막떨어짐"] if domain == "WELD" else ["용접불량"])
            extra = {"no": 2, "class": extra_class, "score": _clamp(rng.uniform(0.52, 0.7)), "bbox": _bbox(rng),
                     "code": a_map[extra_class]["maps_to"], "domain": a_map[extra_class]["domain"],
                     "area_ratio": round(rng.uniform(0.002, 0.02), 4)}
            cand.append(extra)
            findings.append({"from": "A", "class": extra_class, "score": extra["score"]})
        a_id = _insert_a(conn, it["id"], cand, a_class, a_map[a_class]["maps_to"], a_score, 1, created, rng)
        final_code, final_conf, source = a_map[a_class]["maps_to"], a_score, "A"
        if domain == "WELD":
            if rng.random() < 0.12:  # A 는 용접 불량, B 는 정상 → A 판정 유지 + 검토 필요
                b_scores = _b_scores(None, 0, rng)
                flags.append("AB_DIFFER")
            else:
                b_top = code if code in reference.B_DICT_CODES and code != "NORMAL" else \
                    rng.choice(["POROSITY", "UNDERCUT", "BEAD_IRREGULAR"])
                b_scores = _b_scores(b_top, _clamp(base_conf, 0.5), rng)
                final_code, final_conf, source = b_top, b_scores[b_top], "B"
                second = sorted(((c, s) for c, s in b_scores.items() if c not in ("NORMAL", b_top)),
                                key=lambda x: -x[1])[0]
                if rng.random() < 0.1:
                    b_scores[second[0]] = _clamp(rng.uniform(0.5, 0.62))
                    findings.append({"from": "B", "code": second[0], "score": b_scores[second[0]]})
            b_id = _insert_b(conn, it["id"], b_scores, b_top, types, created, rng)
            if rng.random() < 0.08:
                flags.append("IMAGE_QUALITY")
                conn.execute("UPDATE ai_model_results SET image_quality='BLUR' WHERE id=?", (b_id,))
        ai = {"ai_label": a_class, "ai_is_defect": 1, "ai_defect_type": final_code, "ai_confidence": final_conf,
              "ai_source": source}
    else:
        good = reference.GOOD_CLASS.get(domain)
        if good and rng.random() > 0.08:
            a_score = _clamp(base_conf + rng.uniform(-0.05, 0.03), 0.5)
            cand = [{"no": 1, "class": good, "score": a_score, "bbox": _bbox(rng), "code": "NORMAL", "domain": domain,
                     "area_ratio": round(rng.uniform(0.05, 0.3), 4)}]
            a_id = _insert_a(conn, it["id"], cand, good, "NORMAL", a_score, 0, created, rng)
            ai = {"ai_label": good, "ai_is_defect": 0, "ai_defect_type": "NORMAL", "ai_confidence": a_score,
                  "ai_source": "A"}
        else:  # 기준을 넘은 후보 없음 → '검출 없음 — 양품 단정 아님'
            a_id = _insert_a(conn, it["id"], [], None, None, None, 0, created, rng)
            flags.append("NO_DETECTION")
            ai = {"ai_label": None, "ai_is_defect": 0, "ai_defect_type": None, "ai_confidence": None,
                  "ai_source": "A"}
    if ai["ai_confidence"] is not None and ai["ai_confidence"] < 0.6:
        flags.append("LOW_SCORE")

    # AI-검사자 일치율은 '최초 확정' 기준이다(6.3). 재검사를 거친 건은 ai_* 가 재검사 판정이므로 기존 값을 유지한다
    agree = it["ai_agree"]
    reinspected = conn.execute("""SELECT 1 FROM inspection_events WHERE inspection_id=?
                                  AND action IN ('REINSPECT_OK','REINSPECT_NG') LIMIT 1""", (it["id"],)).fetchone()
    if it["final_is_defect"] is not None and not reinspected:
        same_type = (not it["final_is_defect"]) or ai["ai_defect_type"] == it["defect_type"]
        agree = int(ai["ai_is_defect"] == it["final_is_defect"] and same_type)
    if not reinspected:  # 처리 이력의 첫 'AI 판정' 문구도 파이프라인 결과로 맞춘다
        if ai["ai_is_defect"]:
            text = f"AI 판정: {types[ai['ai_defect_type']]['name_ko']} (모델 {ai['ai_source']}, 점수 {ai['ai_confidence']:.2f})"
        elif ai["ai_defect_type"]:
            text = f"AI 판정: 정상 ({ai['ai_label']}, 점수 {ai['ai_confidence']:.2f})"
        else:
            text = "AI 판정: 검출 없음 (양품 단정 아님)"
        conn.execute("""UPDATE inspection_events SET comment=?
                         WHERE id=(SELECT MIN(id) FROM inspection_events WHERE inspection_id=? AND action='AI_INSPECT')""",
                     (text, it["id"]))
    conn.execute("""
        UPDATE inspections SET ai_label=?, ai_is_defect=?, ai_defect_type=?, ai_confidence=?, ai_source=?,
               ai_domain=?, ai_findings=?, ai_flags=?, ai_raw=?, ai_agree=?, ai_status='DONE',
               model_key=?, model_version=?, ai_at=COALESCE(ai_at, ?)
         WHERE id=?""",
        (ai["ai_label"], ai["ai_is_defect"], ai["ai_defect_type"], ai["ai_confidence"], ai["ai_source"], domain,
         json.dumps(findings, ensure_ascii=False) if findings else None,
         json.dumps(flags) if flags else None,
         json.dumps({"a_result_id": a_id, "b_result_id": b_id, "sample": True}),
         agree, "mock_a+mock_b" if b_id else "mock_a", "mock-seg-0" + (" / mock-weld-0" if b_id else ""),
         created, it["id"]))


def _bbox(rng: random.Random) -> list[float]:
    x, y = rng.uniform(100, 900), rng.uniform(80, 700)
    return [round(x, 1), round(y, 1), round(x + rng.uniform(40, 260), 1), round(y + rng.uniform(30, 180), 1)]


def _insert_a(conn, insp_id, cand, top_class, top_code, top_score, is_defect, created, rng) -> int:
    summary: dict[str, dict] = {}
    for c in cand:
        summary[c["class"]] = {"count": rng.randint(1, 4), "max": c["score"]}
    weak = rng.choice(["용접불량", "스크래치", "용접양품", "도막분리"])  # 기준 아래 후보 (표시용)
    if weak not in summary:
        summary[weak] = {"count": rng.randint(1, 6), "max": _clamp(rng.uniform(0.08, 0.42))}
    cur = conn.execute("""
        INSERT INTO ai_model_results (inspection_id, model_key, stage, trigger, status, top_class, top_code, top_score,
               is_defect, candidates_json, class_summary, raw_path, image_w, image_h, model_version, elapsed_ms, created_at)
        VALUES (?, 'mock_a', 'A', 'SEED', 'OK', ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'mock-seg-0', ?, ?)""",
        (insp_id, top_class, top_code, top_score, is_defect, json.dumps(cand, ensure_ascii=False),
         json.dumps(summary, ensure_ascii=False), IMAGE_W, IMAGE_H, rng.randint(2500, 9000), created))
    return int(cur.lastrowid)


def _insert_b(conn, insp_id, scores, top, types, created, rng) -> int:
    if top:
        kws = json.loads(types[top]["keywords"])[:]
        rng.shuffle(kws)
        kws = kws[:3]
        loc = rng.choice(POSITIONS)
        obs = f"{loc}에서 {types[top]['name_ko']} 특징({', '.join(kws[:2])})이 관찰됨"
        top_score, is_def = scores[top], 1
    else:
        kws, loc, obs = ["비드 균일", "표면 양호"], None, "뚜렷한 용접 결함이 관찰되지 않음"
        top, top_score, is_def = "NORMAL", scores["NORMAL"], 0
    cur = conn.execute("""
        INSERT INTO ai_model_results (inspection_id, model_key, stage, trigger, status, top_class, top_code, top_score,
               is_defect, scores_json, keywords_json, observation, location, image_quality, model_version,
               elapsed_ms, created_at)
        VALUES (?, 'mock_b', 'B', 'SEED', 'OK', ?, ?, ?, ?, ?, ?, ?, ?, 'OK', 'mock-weld-0', ?, ?)""",
        (insp_id, top, top, top_score, is_def, json.dumps(scores), json.dumps(kws, ensure_ascii=False), obs, loc,
         rng.randint(1500, 6000), created))
    return int(cur.lastrowid)
