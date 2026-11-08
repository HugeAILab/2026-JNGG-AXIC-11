"""모의 모델 A·B (AI_MODE=mock, 개발·시연 준비용). 화면에는 '모의'로 표시된다 (TODO.md 9.4).

같은 사진이면 같은 결과가 나오도록 사진 바이트의 해시로 난수를 고정한다.
- 모의 A: 실제 AIStream 응답과 같은 형식(class·score·bbox_xyxy·mask_rle). 마스크는 박스 모양 사각형
- 모의 B: 용접 결함 사전 10종 점수 + 키워드·관찰·위치 (8.10 ③ 계약안 형식)
업로드 때 입력한 작업 종류(ctx["work_type"])를 참고해 그럴듯한 분류를 고른다. 실제 판정이 아니다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random

from ... import config, reference
from ...seed import defect_catalog as cat
from . import aistream_seg, coco_rle
from .base import BResult

_A_BY_DOMAIN: dict[str, list[str]] = {}
for _a, _src, _dom, _is_def, _maps, _trig in reference.A_CLASS_MAP:
    _A_BY_DOMAIN.setdefault(_dom, []).append(_a)
_IS_DEFECT = {a: bool(d) for a, _s, _dm, d, _m, _t in reference.A_CLASS_MAP}
_MAPS = {a: m for a, _s, _dm, _d, m, _t in reference.A_CLASS_MAP}
_INDEX = {a: i for i, (a, *_rest) in enumerate(reference.A_CLASS_MAP)}
_KEYWORDS = {t["code"]: t["keywords"] for t in cat.DEFECT_TYPES}
POSITIONS = ["비드 중앙부", "비드 시작부", "비드 종단부", "크레이터 부근", "상부 토우", "하부 토우", "루트 측"]


def _rng(content: bytes, salt: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(salt.encode() + content).digest()[:8], "big"))


async def _delay(rng: random.Random, lo: float, hi: float) -> float:
    seconds = round(config.MOCK_DELAY * rng.uniform(lo, hi), 2)
    if seconds > 0:
        await asyncio.sleep(seconds)
    return seconds


def _instance(rng: random.Random, name: str, score: float, w: int, h: int) -> dict:
    bw, bh = max(2, int(w * rng.uniform(0.08, 0.35))), max(2, int(h * rng.uniform(0.06, 0.3)))
    x0, y0 = rng.randint(0, max(0, w - bw)), rng.randint(0, max(0, h - bh))
    return {"class": {"index": _INDEX.get(name, 0), "category_id": _INDEX.get(name, 0) + 1, "name": name,
                      "source_name": name},
            "score": round(score, 4), "bbox_xyxy": [float(x0), float(y0), float(x0 + bw), float(y0 + bh)],
            "mask_rle": coco_rle.box_mask(h, w, x0, y0, x0 + bw, y0 + bh)}


async def predict_a(content: bytes, ctx: dict) -> aistream_seg.SegResult:
    _bytes, w, h = await asyncio.to_thread(aistream_seg.prepare_image, content)
    rng = _rng(content, "A")
    elapsed = await _delay(rng, 0.8, 2.0)
    work = ctx.get("work_type")
    domain = work if work in reference.DOMAINS else rng.choice(["WELD", "WELD", "WELD", "SURFACE", "INSULATION",
                                                                "CABLE", "BINDING", "CUTTING"])
    classes = _A_BY_DOMAIN[domain]
    defects = [c for c in classes if _IS_DEFECT[c]]
    goods = [c for c in classes if not _IS_DEFECT[c]]
    roll = rng.random()
    inst = []
    if roll < 0.68:  # 불량 후보
        top = rng.choice(defects)
        inst.append(_instance(rng, top, rng.uniform(0.55, 0.95), w, h))
        if rng.random() < 0.15:  # 함께 발견되는 다른 불량
            other = rng.choice([c for c in _IS_DEFECT if _IS_DEFECT[c] and c != top])
            inst.append(_instance(rng, other, rng.uniform(0.5, 0.7), w, h))
    elif roll < 0.9 and goods:  # 양품 후보
        inst.append(_instance(rng, rng.choice(goods), rng.uniform(0.6, 0.93), w, h))
    # 그 밖(약 10%, 양품 클래스가 없는 영역은 더 많이): 기준을 넘는 후보 없음 → '검출 없음'
    for _ in range(rng.randint(2, 6)):  # 기준 아래 약한 후보
        inst.append(_instance(rng, rng.choice(list(_IS_DEFECT)), rng.uniform(0.03, 0.42), w, h))
    body = {"segmentation": {"instances": inst}, "model": {"name": "mock-seg-0", "mock": True}}
    aistream_seg.validate_result(body, w, h)
    return aistream_seg.SegResult(body=body, width=w, height=h, elapsed_seconds=elapsed)


CONFUSE = {
    "LACK_OF_FUSION": ["INCOMPLETE_PENETRATION", "SLAG_INCLUSION"], "INCOMPLETE_PENETRATION": ["LACK_OF_FUSION"],
    "UNDERCUT": ["OVERLAP", "BEAD_IRREGULAR"], "OVERLAP": ["UNDERCUT", "BEAD_IRREGULAR"],
    "POROSITY": ["SLAG_INCLUSION", "SPATTER"], "SLAG_INCLUSION": ["POROSITY", "LACK_OF_FUSION"],
    "SPATTER": ["POROSITY", "BEAD_IRREGULAR"], "BEAD_IRREGULAR": ["UNDERCUT", "OVERLAP"],
    "CRACK": ["LACK_OF_FUSION", "UNDERCUT"],
}


async def predict_b(content: bytes, ctx: dict) -> BResult:
    rng = _rng(content, "B")
    elapsed = await _delay(rng, 0.5, 1.2)
    a_class = (ctx.get("a_top") or {}).get("class")
    hint = _MAPS.get(a_class or "")
    codes = [c for c in reference.B_DICT_CODES if c != "NORMAL"]
    scores = {c: round(rng.uniform(0.01, 0.12), 3) for c in reference.B_DICT_CODES}
    if rng.random() < 0.12:  # A 는 용접 불량, B 는 정상 → 'A·B 판정 다름'
        scores["NORMAL"] = round(rng.uniform(0.6, 0.85), 3)
        return BResult(scores=scores, keywords=["비드 균일", "표면 양호"], observation="뚜렷한 용접 결함이 관찰되지 않음",
                       image_quality="OK", model_version="mock-weld-0", elapsed_seconds=elapsed,
                       raw={"mock": True, "scores": scores})
    top = hint if hint in codes else rng.choice(["POROSITY", "UNDERCUT", "BEAD_IRREGULAR", "SPATTER", "LACK_OF_FUSION"])
    for c in CONFUSE.get(top, []):
        scores[c] = round(rng.uniform(0.18, 0.45), 3)
    scores[top] = round(rng.uniform(0.62, 0.93), 3)
    scores["NORMAL"] = round((1 - scores[top]) * rng.uniform(0.3, 0.6), 3)
    kws = list(_KEYWORDS.get(top, []))
    rng.shuffle(kws)
    loc = rng.choice(POSITIONS)
    name = next(t["name_ko"] for t in cat.DEFECT_TYPES if t["code"] == top)
    quality = "BLUR" if rng.random() < 0.06 else "OK"
    body = {"scores": scores, "keywords": kws[:3], "observation": f"{loc}에서 {name} 특징({', '.join(kws[:2])})이 관찰됨",
            "location": loc, "image_quality": quality, "model_version": "mock-weld-0", "mock": True}
    return BResult(scores=scores, keywords=kws[:3], observation=body["observation"], location=loc,
                   image_quality=quality, model_version="mock-weld-0", elapsed_seconds=elapsed,
                   raw=json.loads(json.dumps(body, ensure_ascii=False)))
