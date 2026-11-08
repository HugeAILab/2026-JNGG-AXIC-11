"""COCO 압축 RLE (static/js/segmentation.js 의 decodeCocoRle 와 같은 규칙, 서버 쪽).

- 픽셀은 열 우선(column-major) 순서, run 은 0 부터 시작해 0·1 이 번갈아 나온다.
- 문자열은 run 마다 5비트 묶음(48 더한 문자)이고, 세 번째 run 부터는 두 칸 앞 run 과의 차이를 적는다.

서버는 결함 면적 비율(마스크 픽셀 ÷ 이미지 픽셀, TODO.md 8.10 ②)을 계산하고, 모의 모델 A 가 마스크를 만들 때 쓴다.
"""
from __future__ import annotations


def decode_runs(counts: str, total: int) -> list[int]:
    runs: list[int] = []
    p = s = 0
    while p < len(counts):
        value = shift = 0
        while True:
            if p >= len(counts) or shift > 50:
                raise ValueError("잘못된 RLE 문자열")
            c = ord(counts[p]) - 48
            p += 1
            if not 0 <= c <= 63:
                raise ValueError("잘못된 RLE 문자")
            value += (c & 31) << shift
            shift += 5
            if not c & 32:
                break
        if c & 16:
            value -= 1 << shift
        if len(runs) > 2:
            value += runs[-2]
        if value < 0 or s + value > total:
            raise ValueError("잘못된 RLE 길이")
        runs.append(value)
        s += value
    if s != total:
        raise ValueError("RLE 픽셀 수 불일치")
    return runs


def encode_runs(runs: list[int]) -> str:
    out = []
    for i, x in enumerate(runs):
        if i > 2:
            x -= runs[i - 2]
        more = True
        while more:
            c = x & 0x1F
            x >>= 5
            more = (x != -1) if (c & 0x10) else (x != 0)
            if more:
                c |= 0x20
            out.append(chr(c + 48))
    return "".join(out)


def mask_area(counts: str, height: int, width: int) -> int:
    """마스크 픽셀 수 = 홀수 번째 run 길이의 합."""
    return sum(decode_runs(counts, height * width)[1::2])


def box_mask(height: int, width: int, x0: int, y0: int, x1: int, y1: int) -> dict:
    """사각형 마스크(열 우선) → {"size": [h, w], "counts": ...}. 모의 모델용."""
    x0, x1 = max(0, min(x0, width)), max(0, min(x1, width))
    y0, y1 = max(0, min(y0, height)), max(0, min(y1, height))
    if x1 <= x0 or y1 <= y0:
        return {"size": [height, width], "counts": encode_runs([height * width])}
    span = y1 - y0
    runs = [x0 * height + y0]
    for col in range(x0, x1):
        runs.append(span)
        runs.append(height - span if col < x1 - 1 else 0)
    tail = (width - x1) * height + (height - y1)
    runs[-1] = tail
    return {"size": [height, width], "counts": encode_runs(runs)}
