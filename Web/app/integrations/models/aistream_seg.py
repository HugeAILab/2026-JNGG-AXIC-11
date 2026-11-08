"""모델 A — AIStream "LNG Tank Quality Inspection" MaskDINO 영역 분할 (TODO.md 8.10 ①, 9.1, 8.12).

32-Web(커밋 561715a)의 routes/segmentation.py 에서 호출·검증 로직을 그대로 옮겨 왔다.
라우트는 이 모듈을 부르기만 하므로, 3단계 이후의 파이프라인(jobs·ai_pipeline)도 같은 함수를 쓴다.

- 브라우저가 아니라 서버에서만 호출한다 (API 에 CORS 가 없고, 주소를 노출하지 않기 위해)
- 원본 이미지 바이트를 그대로 보낸다 (다시 인코딩하지 않음 → 마스크 좌표가 원본과 일치)
- 서빙은 작업자 1개 + 대기열 10개를 모든 팀이 함께 쓰므로, 웹 쪽 동시 호출은 2건으로 제한한다
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import time
import warnings
from dataclasses import dataclass

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from ... import config

log = logging.getLogger("weldsight.model_a")

MAX_BYTES = 10 * 1024 * 1024        # 업로드·API 공통 한도 10 MiB
MAX_PIXELS = 20_000_000             # 2천만 픽셀
MAX_RESPONSE = 32 * 1024 * 1024     # 응답(마스크 포함) 최대 32MB
MAX_INSTANCES = 1000
SLOTS = asyncio.Semaphore(2)        # 웹 → 모델 A 동시 호출 수

# API 오류 → 화면 문구 (32-Web 과 같음)
UPSTREAM_MESSAGES = {
    413: "검사 API의 이미지 크기 제한을 초과했습니다.",
    422: "검사 API가 이미지를 처리하지 못했습니다.",
    429: "검사 API 대기열이 가득 찼습니다. 잠시 후 다시 시도하세요.",
    503: "검사 모델이 준비되지 않았습니다.",
}


class ModelAError(Exception):
    """모델 A 호출 실패. status 는 웹이 돌려줄 HTTP 상태 코드."""

    def __init__(self, status: int, message: str, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


@dataclass
class SegResult:
    body: dict            # API 원본 응답 (필드 그대로)
    width: int            # EXIF 방향을 반영한 이미지 너비
    height: int
    elapsed_seconds: float

    @property
    def instances(self) -> list[dict]:
        return self.body["segmentation"]["instances"]


def base_url() -> str:
    return config.SEGMENTATION_BASE_URL


def prepare_image(content: bytes) -> tuple[bytes, int, int]:
    """실제 JPEG/PNG 인지·픽셀 수를 확인하고 EXIF 방향을 반영한 크기를 돌려준다. 바이트는 바꾸지 않는다."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in ("JPEG", "PNG"):
                    raise ModelAError(422, "JPEG 또는 PNG 이미지만 지원합니다.")
                if image.width * image.height > MAX_PIXELS:
                    raise ModelAError(413, "이미지는 최대 20,000,000픽셀까지 지원합니다.")
                image = ImageOps.exif_transpose(image)
                image.load()
                width, height = image.size
        return content, width, height
    except (UnidentifiedImageError, OSError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ModelAError(422, "이미지를 읽을 수 없습니다. 정상 JPEG/PNG 파일을 선택하세요.") from exc


def validate_result(body: dict, width: int, height: int) -> None:
    """응답 형식 검증: 후보 수·점수 범위·박스·마스크 크기가 이미지와 맞는지."""
    try:
        instances = body["segmentation"]["instances"]
        if not isinstance(instances, list) or len(instances) > MAX_INSTANCES:
            raise ValueError("invalid instances")
        for item in instances:
            score = item["score"]
            if not isinstance(item["class"]["name"], str):
                raise ValueError("invalid class")
            if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("invalid score")
            box = item["bbox_xyxy"]
            if len(box) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
                raise ValueError("invalid box")
            if box[2] < box[0] or box[3] < box[1]:
                raise ValueError("inverted box")
            mask = item["mask_rle"]
            if mask["size"] != [height, width] or not isinstance(mask["counts"], str):
                raise ValueError("mask size does not match image")
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelAError(502, "검사 API 응답 형식 또는 마스크 크기가 올바르지 않습니다.") from exc


async def health() -> bool:
    """GET /health → {"status":"ready"} 이면 True."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(base_url() + "/health")
        return response.status_code == 200 and response.json().get("status") == "ready"
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


async def predict(content: bytes, *, wait: bool = False) -> SegResult:
    """사진 1장 → POST /predict/segmentation.

    wait=False 이면 동시 호출이 가득 찼을 때 기다리지 않고 바로 429 (화면 단건 검사, 32-Web 동작).
    wait=True 이면 자리가 날 때까지 기다린다 (3단계 백그라운드 작업용).
    """
    if len(content) > MAX_BYTES:
        raise ModelAError(413, "이미지는 최대 10 MiB까지 지원합니다.")
    normalized, width, height = await asyncio.to_thread(prepare_image, content)
    is_png = normalized.startswith(b"\x89PNG\r\n\x1a\n")
    filename, content_type = ("inspection.png", "image/png") if is_png else ("inspection.jpg", "image/jpeg")
    if SLOTS.locked() and not wait:
        raise ModelAError(429, "검사 중입니다. 잠시 후 다시 요청하세요.", retry_after="1")
    start = time.monotonic()
    async with SLOTS:
        try:
            async with httpx.AsyncClient(timeout=config.SEGMENTATION_TIMEOUT) as client:
                async with client.stream("POST", base_url() + "/predict/segmentation",
                                         files={"file": (filename, normalized, content_type)}) as response:
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE:
                            raise ModelAError(502, "검사 API 응답이 허용 크기를 초과했습니다.")
                    if response.status_code != 200:
                        status = response.status_code if response.status_code in UPSTREAM_MESSAGES else 502
                        retry = response.headers.get("Retry-After", "1") if status == 429 else None
                        raise ModelAError(status, UPSTREAM_MESSAGES.get(status, "검사 API에서 오류가 발생했습니다."),
                                          retry_after=retry)
                    body = json.loads(chunks)
            validate_result(body, width, height)
        except httpx.TimeoutException as exc:
            raise ModelAError(504, "검사 응답 대기 시간이 초과됐습니다. 이미 접수된 검사는 계속될 수 있습니다.") from exc
        except httpx.HTTPError as exc:
            log.warning("model A connection error: %s", type(exc).__name__)
            raise ModelAError(502, "검사 API에 연결할 수 없습니다.") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise ModelAError(502, "검사 API에서 JSON 응답을 받지 못했습니다.") from exc
    return SegResult(body=body, width=width, height=height, elapsed_seconds=round(time.monotonic() - start, 2))
