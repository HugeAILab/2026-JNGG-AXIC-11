"""단계별 활성 모델 고르기 (TODO.md 9.4).

지금은 설정값으로 정한다 (관리자가 화면에서 활성 모델을 바꾸는 기능은 5단계):
- A: AI_MODE=mock → mock_a,  live → model_a (AIStream 분할)
- B: AI_MODE=mock → mock_b,  live → MODEL_B_URL 이 있으면 model_b, 없으면 MODEL_B_MOCK=true 일 때 mock_b, 아니면 건너뜀
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ... import config
from . import aistream_seg, mock, weld_api


@dataclass
class Model:
    key: str            # ai_models.key
    stage: str          # A / B
    predict: Callable[[bytes, dict], Awaitable[Any]]
    is_mock: bool


async def _model_a(content: bytes, ctx: dict) -> aistream_seg.SegResult:
    return await aistream_seg.predict(content, wait=True)  # 백그라운드 작업은 자리가 날 때까지 기다린다


def stage_a() -> Model:
    if config.AI_MODE == "mock":
        return Model("mock_a", "A", mock.predict_a, True)
    return Model("model_a", "A", _model_a, False)


def stage_b() -> Model | None:
    if config.AI_MODE == "mock":
        return Model("mock_b", "B", mock.predict_b, True)
    if config.MODEL_B_URL:
        return Model("model_b", "B", weld_api.predict, False)
    if config.MODEL_B_MOCK:
        return Model("mock_b", "B", mock.predict_b, True)
    return None
