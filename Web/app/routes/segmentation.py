"""모델 A 단건 즉시 검사 프록시 (32-Web 기능 통합, TODO.md 8.12). 결과를 저장하지 않는다.

주소·응답 형식은 32-Web 과 같다. 화면의 사진 업로드 검사는 3단계부터 여러 장 업로드·저장 흐름
(routes/uploads.py → services/jobs.py)을 쓰고, 이 API 는 연결 확인(스모크 테스트 --ai)과 상태 표시에 쓴다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import auth
from ..integrations.models import aistream_seg as model_a
from ..services import activity_log

router = APIRouter()
INSPECT_ROLES = ("admin", "manager", "inspector")  # 역할별 권한표(TODO.md 7.3): 업로드·판정은 worker 제외
can_inspect = auth.require_role(*INSPECT_ROLES)


@router.get("/portal/api/segmentation/health")
async def api_health(_user: dict = Depends(auth.require_login)):
    ready = await model_a.health()
    return {"ok": ready, "status": "ready" if ready else "unavailable"}


@router.post("/portal/api/segmentation", dependencies=[Depends(auth.verify_csrf)])
async def predict(request: Request, file: UploadFile = File(...), user: dict = Depends(can_inspect)):
    content = await file.read(model_a.MAX_BYTES + 1)
    await file.close()
    try:
        result = await model_a.predict(content)
    except model_a.ModelAError as e:
        headers = {"Retry-After": e.retry_after} if e.retry_after else None
        activity_log.log("AI", "AI_ERROR", f"모델 A 검사 실패 · {e.message}"[:200],
                         {"model_key": "model_a", "status": e.status}, user_id=user["id"],
                         level="WARN", ip=auth.client_ip(request))
        return JSONResponse({"ok": False, "error": e.message, "status": e.status}, status_code=e.status, headers=headers)
    activity_log.log("AI", "SEGMENTATION", f"모델 A 영역 분할 검사 · 후보 {len(result.instances)}개",
                     {"model_key": "model_a", "width": result.width, "height": result.height,
                      "elapsed_seconds": result.elapsed_seconds},
                     user_id=user["id"], ip=auth.client_ip(request))
    # 원본 응답 필드는 그대로 돌려준다. 이미지는 저장하지 않고 브라우저에서만 미리 본다(32-Web 과 같음)
    return {"ok": True, "result": result.body, "image": {"width": result.width, "height": result.height},
            "elapsed_seconds": result.elapsed_seconds}
