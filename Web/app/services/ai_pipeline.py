"""AI 파이프라인 규칙 — 모델 A 선별 → (용접이면 B) → 최종 AI 판정 (TODO.md 8.10 ①②③).

외부 호출·DB 없이 입력만으로 결과를 내는 순수 함수만 둔다 (분기 단위 테스트 대상).
실행·저장은 services/jobs.py 가 한다.
"""
from __future__ import annotations

from typing import Any

from ..integrations.models import coco_rle
from ..integrations.models.base import BResult

IOU_DUP = 0.7          # 같은 자리 중복 후보 정리 기준
MAX_FINDINGS = 3       # 함께 발견된 불량 최대 개수
MAX_CANDIDATES = 20    # DB 에 요약으로 남길 후보 수 (원본 전체는 파일)
LOW_SCORE = 0.6        # 검토 필요: 신뢰도 낮음
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def summarize_a(body: dict, width: int, height: int, class_map: dict[str, dict],
                work_type: str | None = None) -> dict[str, Any]:
    """모델 A 응답 → 선별 결과 (8.10 ②).

    class_map: a_class_map 행 {a_class: {domain, is_defect, maps_to, threshold, triggers_b}}
    반환: top(대표 후보 또는 None), is_defect, domain, triggers_b, candidates(기준 통과·중복 정리, 대표가 맨 앞),
          class_summary(필터 전 클래스별 후보 수·최고 점수), findings(함께 발견된 불량)
    """
    instances = body["segmentation"]["instances"]
    summary: dict[str, dict] = {}
    passing = []
    for no, it in enumerate(instances, start=1):
        name, score = it["class"]["name"], float(it["score"])
        s = summary.setdefault(name, {"count": 0, "max": 0.0})
        s["count"] += 1
        s["max"] = round(max(s["max"], score), 4)
        m = class_map.get(name)
        if m is None or score < float(m["threshold"]):  # 대응표에 없는 분류는 판정에 쓰지 않는다
            continue
        passing.append({"no": no, "class": name, "score": round(score, 4),
                        "bbox": [round(float(v), 1) for v in it["bbox_xyxy"]], "code": m["maps_to"],
                        "domain": m["domain"], "is_defect": int(m["is_defect"]), "triggers_b": int(m["triggers_b"]),
                        "_mask": it.get("mask_rle")})
    passing.sort(key=lambda c: -c["score"])
    kept: list[dict] = []
    for c in passing:  # 박스 IoU 0.7 이상이면 점수 높은 후보만 남김
        if all(iou(c["bbox"], k["bbox"]) < IOU_DUP for k in kept):
            kept.append(c)
    defects = [c for c in kept if c["is_defect"]]
    goods = [c for c in kept if not c["is_defect"]]
    weld = [c for c in defects if c["triggers_b"]]
    top = weld[0] if weld else (defects[0] if defects else (goods[0] if goods else None))
    if top is not None:
        mask = top.get("_mask")
        if mask:
            try:
                top["area_ratio"] = round(coco_rle.mask_area(mask["counts"], height, width) / (width * height), 4)
            except (ValueError, KeyError, TypeError, ZeroDivisionError):
                pass
        kept.remove(top)
        kept.insert(0, top)
    findings = [{"from": "A", "class": c["class"], "score": c["score"]}
                for c in defects if c is not top][:MAX_FINDINGS]
    for c in kept:
        c.pop("_mask", None)
    return {
        "top": top, "is_defect": int(bool(top and top["is_defect"])), "triggers_b": bool(weld),
        "domain": top["domain"] if top else (work_type if work_type and work_type != "MIXED" else None),
        "candidates": kept[:MAX_CANDIDATES], "class_summary": summary, "findings": findings,
        "weld_candidates": weld,
    }


def b_top(scores: dict[str, float], threshold: float, severity: dict[str, str]) -> tuple[str, float]:
    """B 대표 유형: 결함 9개 중 최고 점수가 기준 이상이면 그 코드, 아니면 NORMAL. 같으면 심각도 높은 쪽."""
    best = max(((c, s) for c, s in scores.items() if c != "NORMAL"),
               key=lambda x: (x[1], SEVERITY_RANK.get(severity.get(x[0], "NONE"), 0)), default=(None, 0.0))
    if best[0] is not None and best[1] >= threshold:
        return best[0], best[1]
    return "NORMAL", scores.get("NORMAL", 0.0)


def decide(a: dict, b: BResult | None, *, b_ran: bool, b_threshold: float,
           severity: dict[str, str]) -> dict[str, Any]:
    """최종 AI 판정 (8.10 ②③). A·B 점수끼리 비교하지 않고 파이프라인 순서로만 정한다.

    b_ran: 용접 분기라서 B 를 시도했는가 (b 가 None 이면 실패·미연결)
    """
    top = a["top"]
    flags: list[str] = []
    findings = list(a["findings"])
    out: dict[str, Any] = {"ai_domain": a["domain"], "ai_source": "A", "b_top": None}
    if top is None:
        out.update(ai_label=None, ai_is_defect=0, ai_defect_type=None, ai_confidence=None)
        flags.append("NO_DETECTION")
    elif not top["is_defect"]:
        out.update(ai_label=top["class"], ai_is_defect=0, ai_defect_type="NORMAL", ai_confidence=top["score"])
    else:
        out.update(ai_label=top["class"], ai_is_defect=1, ai_defect_type=top["code"], ai_confidence=top["score"])
        if a["triggers_b"]:
            if b is None:
                flags.append("B_UNAVAILABLE")
            else:
                code, score = b_top(b.scores, b_threshold, severity)
                out["b_top"] = (code, score)
                if code != "NORMAL":
                    out.update(ai_defect_type=code, ai_confidence=round(score, 4), ai_source="B")
                    for c, s in sorted(b.scores.items(), key=lambda x: -x[1]):
                        if c not in ("NORMAL", code) and s >= b_threshold:
                            findings.append({"from": "B", "code": c, "score": round(s, 4)})
                else:
                    flags.append("AB_DIFFER")
                if b.image_quality and b.image_quality != "OK":
                    flags.append("IMAGE_QUALITY")
        elif b_ran:  # 일어나지 않지만 방어
            flags.append("B_UNAVAILABLE")
    if out["ai_confidence"] is not None and out["ai_confidence"] < LOW_SCORE:
        flags.append("LOW_SCORE")
    out["flags"] = flags
    out["findings"] = findings[:MAX_FINDINGS + 2]
    return out


def verdict_text(d: dict, type_name) -> str:
    """처리 이력 'AI 판정' 한 줄."""
    if d["ai_is_defect"]:
        return f"AI 판정: {type_name(d['ai_defect_type'])} (모델 {d['ai_source']}, 점수 {d['ai_confidence']:.2f})"
    if d["ai_defect_type"]:
        return f"AI 판정: 정상 ({d['ai_label']}, 점수 {d['ai_confidence']:.2f})"
    return "AI 판정: 검출 없음 (양품 단정 아님)"


def finding_draft(it: dict, type_name) -> str:
    """확정 폼·일괄 확정의 소견 초안: AI 판정 + 함께 발견된 불량."""
    parts = []
    if it.get("ai_is_defect"):
        parts.append(f"AI 판정 {type_name(it['ai_defect_type'])}({(it.get('ai_confidence') or 0):.2f}, 모델 {it.get('ai_source')})")
    together = []
    for f in it.get("findings") or []:
        name = f.get("class") if f.get("from") == "A" else type_name(f.get("code"))
        together.append(f"{name}({f['score']:.2f})")
    if together:
        parts.append("함께 발견: " + ", ".join(together))
    return " · ".join(parts)
