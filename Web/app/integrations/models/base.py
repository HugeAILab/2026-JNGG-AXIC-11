"""모델 어댑터 공통 형식 (TODO.md 9.4).

- 모델 A 어댑터: async predict(content, ctx) -> aistream_seg.SegResult  (AIStream 분할 응답 원본 + 이미지 크기)
- 모델 B 어댑터: async predict(content, ctx) -> BResult                (용접 결함 사전 10종 점수·키워드·관찰·위치)
- 실패는 모두 ModelError(status, message) — aistream_seg.ModelAError 와 같은 모양이라 함께 처리한다.
파이프라인(services/ai_pipeline.py)과 작업(services/jobs.py)은 어떤 모델인지 모른다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .aistream_seg import ModelAError as ModelError  # noqa: F401 — 같은 예외를 B 에서도 쓴다


@dataclass
class BResult:
    scores: dict[str, float]
    keywords: list[str] = field(default_factory=list)
    observation: str | None = None
    location: str | None = None
    image_quality: str | None = None
    model_version: str | None = None
    elapsed_seconds: float = 0.0
    raw: dict[str, Any] | None = None


def result_status(status: int, message: str = "") -> str:
    """HTTP 상태 → ai_model_results.status"""
    if status == 429:
        return "BUSY"
    if status == 504:
        return "TIMEOUT"
    if status == 502 and "형식" in message:
        return "INVALID"
    return "ERROR"
