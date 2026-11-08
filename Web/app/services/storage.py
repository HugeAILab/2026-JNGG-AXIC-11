"""파일 저장: 업로드 사진 원본 · 썸네일 · 모델 A 원본 응답 (TODO.md 2.3, 4장 data/).

    data/uploads/YYYYMMDD/<uuid>.jpg|png   원본 (받은 바이트 그대로 — 모델 A 에 같은 바이트를 보내 좌표가 맞는다)
    data/thumbs/YYYYMMDD/<uuid>.jpg        썸네일 (EXIF 방향 반영, 긴 변 320px)
    data/results/YYYYMMDD/<검사 id>-A-<결과 id>.json.gz   모델 A 응답 원본 (마스크 포함, 수 MB)

DB 에는 위 폴더 기준 상대 경로만 넣는다 (inspections.image_path = 'YYYYMMDD/<uuid>.jpg').
"""
from __future__ import annotations

import gzip
import io
import json
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .. import config, db

THUMB_SIZE = 320


def _inside(base: Path, rel: str) -> Path | None:
    """rel 을 base 아래 경로로 풀되 밖으로 나가면 None (경로 조작 방지)."""
    if not rel or "\\" in rel or rel.startswith("/"):
        return None
    base = base.resolve()
    p = (base / rel).resolve()
    return p if p.is_relative_to(base) and p != base else None


def photo_path(rel: str) -> Path | None:
    return _inside(config.UPLOAD_DIR, rel)


def thumb_rel(rel: str) -> str:
    return str(Path(rel).with_suffix(".jpg")).replace("\\", "/")


def thumb_path(rel: str) -> Path | None:
    return _inside(config.THUMB_DIR, rel)


def save_photo(content: bytes, is_png: bool) -> str:
    """원본 저장 + 썸네일 생성 → 상대 경로."""
    day = db.now_kst().strftime("%Y%m%d")
    rel = f"{day}/{uuid.uuid4().hex}.{'png' if is_png else 'jpg'}"
    dest = config.UPLOAD_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    try:
        make_thumb(content, config.THUMB_DIR / thumb_rel(rel))
    except Exception:  # noqa: BLE001 — 썸네일 실패는 원본 저장을 막지 않는다 (화면은 원본으로 대신 표시)
        pass
    return rel


def make_thumb(content: bytes, dest: Path) -> None:
    with Image.open(io.BytesIO(content)) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((THUMB_SIZE, THUMB_SIZE))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, format="JPEG", quality=82)


def read_photo(rel: str) -> bytes:
    p = photo_path(rel)
    if p is None or not p.is_file():
        raise FileNotFoundError(rel)
    return p.read_bytes()


def save_result(inspection_id: int, result_id: int, body: dict) -> str:
    day = db.now_kst().strftime("%Y%m%d")
    rel = f"{day}/{inspection_id}-A-{result_id}.json.gz"
    dest = config.RESULT_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(gzip.compress(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6))
    return rel


def load_result(rel: str) -> dict | None:
    p = _inside(config.RESULT_DIR, rel)
    if p is None or not p.is_file():
        return None
    return json.loads(gzip.decompress(p.read_bytes()))
