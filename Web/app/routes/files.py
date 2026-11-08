"""업로드 사진·썸네일 제공 (로그인 필요, TODO.md 10장).

    GET /portal/files/<YYYYMMDD>/<uuid>.jpg          원본 (data/uploads)
    GET /portal/files/thumbs/<YYYYMMDD>/<uuid>.jpg   썸네일 (data/thumbs) — 없으면 원본으로 대신

경로는 resolve() 뒤 각 폴더 밖이면 404. 정적 파일(/static)과 달리 로그아웃 상태에서는 볼 수 없다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import auth
from ..services import storage

router = APIRouter()
TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


@router.get("/portal/files/{path:path}")
def file(path: str, _user: dict = Depends(auth.require_login)):
    p = None
    if path.startswith("thumbs/"):
        rel = path[len("thumbs/"):]
        p = storage.thumb_path(rel)
        if p is None or not p.is_file():  # 썸네일 생성에 실패한 사진은 원본으로
            for ext in (".jpg", ".png"):
                q = storage.photo_path(rel[:-4] + ext) if rel.endswith(".jpg") else None
                if q is not None and q.is_file():
                    p = q
                    break
    else:
        p = storage.photo_path(path)
    if p is None or not p.is_file() or p.suffix.lower() not in TYPES:
        raise HTTPException(status_code=404)
    # 로그인해야 보이는 검사 사진이다. no-store 로 브라우저 캐시에 남기지 않는다
    # (공용 PC 에서 로그아웃한 뒤 캐시로 다시 보이는 것을 막는다 — 보안 점검.md SEC-06).
    # 공통 미들웨어는 setdefault 라서 여기서 지정한 값을 덮지 않으므로 이 자리에서 정한다.
    return FileResponse(p, media_type=TYPES[p.suffix.lower()], headers={"Cache-Control": "no-store"})
