"""모델 B — 용접 상세 분석 API 어댑터 (개발 중, TODO.md 8.10 ③ · 9.3 의 계약안 기준).

    POST {MODEL_B_URL}/analyze   multipart: file=원본 사진, context=JSON(선택)
    → {"scores": {코드: 0~1, ...10종}, "keywords": [...], "observation": "...", "location": "...",
       "image_quality": "OK", "model_version": "..."}

실제 API 가 확정되면 to_result() 한 함수만 고친다 (14장 #13).
"""
from __future__ import annotations

import asyncio
import json
import math
import time

import httpx

from ... import config, reference
from .base import BResult, ModelError

SLOTS = asyncio.Semaphore(2)
QUALITY = ("OK", "BLUR", "DARK", "NOT_WELD")
_alias: dict[str, str] | None = None


def _aliases() -> dict[str, str]:
    """'기공'·'porosity'·'POROSITY' → 'POROSITY'"""
    global _alias
    if _alias is None:
        _alias = {}
        for t in reference.all_defect_types():
            if t["code"] in reference.B_DICT_CODES:
                for a in t["aliases"] + [t["code"]]:
                    _alias[a.lower()] = t["code"]
    return _alias


def to_result(body: dict, elapsed: float) -> BResult:
    """응답 → 표준 B 결과. 코드 별칭 변환, 사전 밖 키 버림, 0~1 자르기, 빠진 코드는 0 (8.10 ③ 검증·정규화)."""
    raw_scores = body.get("scores")
    if not isinstance(raw_scores, dict) or not raw_scores:
        raise ModelError(502, "용접 분석 API 응답 형식이 올바르지 않습니다 (scores 없음).")
    scores = {c: 0.0 for c in reference.B_DICT_CODES}
    known = 0
    for key, value in raw_scores.items():
        code = _aliases().get(str(key).strip().lower())
        if code is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        scores[code] = round(min(1.0, max(0.0, float(value))), 4)
        known += 1
    if not known:
        raise ModelError(502, "용접 분석 API 응답 형식이 올바르지 않습니다 (알 수 있는 코드 없음).")
    kws = body.get("keywords") if isinstance(body.get("keywords"), list) else []
    quality = body.get("image_quality") if body.get("image_quality") in QUALITY else None
    text = lambda k: str(body[k])[:300] if isinstance(body.get(k), str) and body[k].strip() else None  # noqa: E731
    return BResult(scores=scores, keywords=[str(k)[:40] for k in kws[:8]], observation=text("observation"),
                   location=text("location"), image_quality=quality, model_version=text("model_version"),
                   elapsed_seconds=elapsed, raw=body)


async def predict(content: bytes, ctx: dict) -> BResult:
    if not config.MODEL_B_URL:
        raise ModelError(503, "용접 분석 API 주소(MODEL_B_URL)가 설정되지 않았습니다.")
    context = {"weld_process": ctx.get("weld_process"), "block": ctx.get("block"),
               "a_candidates": [{"class": c["class"], "score": c["score"], "bbox_xyxy": c["bbox"]}
                                for c in ctx.get("a_weld", [])[:5]]}
    headers = {"Authorization": f"Bearer {config.MODEL_B_API_KEY}"} if config.MODEL_B_API_KEY else {}
    is_png = content.startswith(b"\x89PNG\r\n\x1a\n")
    files = {"file": ("inspection.png" if is_png else "inspection.jpg", content, "image/png" if is_png else "image/jpeg")}
    last: ModelError | None = None
    for attempt in range(2):  # 형식이 깨지면 1회 재요청
        start = time.monotonic()
        async with SLOTS:
            try:
                async with httpx.AsyncClient(timeout=config.MODEL_B_TIMEOUT) as client:
                    r = await client.post(config.MODEL_B_URL + "/analyze", files=files, headers=headers,
                                          data={"context": json.dumps(context, ensure_ascii=False)})
            except httpx.TimeoutException as exc:
                raise ModelError(504, "용접 분석 API 응답 대기 시간이 초과됐습니다.") from exc
            except httpx.HTTPError as exc:
                raise ModelError(502, "용접 분석 API에 연결할 수 없습니다.") from exc
        if r.status_code == 429:
            raise ModelError(429, "용접 분석 API가 바쁩니다.", retry_after=r.headers.get("Retry-After", "2"))
        if r.status_code != 200:
            raise ModelError(502, f"용접 분석 API 오류 (HTTP {r.status_code}).")
        try:
            return to_result(r.json(), round(time.monotonic() - start, 2))
        except (ValueError, ModelError) as exc:
            last = exc if isinstance(exc, ModelError) else ModelError(502, "용접 분석 API 응답 형식이 올바르지 않습니다.")
    raise last  # type: ignore[misc]


async def health() -> bool:
    if not config.MODEL_B_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(config.MODEL_B_URL + "/health")
        return r.status_code == 200
    except httpx.HTTPError:
        return False
