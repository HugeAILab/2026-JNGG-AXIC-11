"""템플릿 렌더링 공통 처리.

모든 링크·정적 파일 경로는 ROOT_PATH 접두사가 붙은 '경로'로 만든다 (TODO.md 12.2).
템플릿에서: {{ root }}/portal/...  ,  {{ static('css/portal.css') }}
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import auth, config, db, middleware, reference
from .routes import find_by_key, menu_for

templates = Jinja2Templates(directory=str(config.APP_DIR / "templates"))
_ASSET_VER = str(int(time.time()))  # 배포(재시작)마다 바뀌어 브라우저 캐시를 무효화


def static_url(path: str) -> str:
    return f"{config.ROOT_PATH}/static/{path.lstrip('/')}?v={_ASSET_VER}"


def url(path: str) -> str:
    """앱 내부 경로 → 접두사가 붙은 경로. RedirectResponse 등 파이썬 코드에서 사용."""
    return f"{config.ROOT_PATH}{path}"


STATUS_LABELS = {
    "PENDING": "검사 대기", "AI_DONE": "AI 판정 완료", "DEFECT": "불량 · 조치 대기", "REWORK": "수정 중",
    "REINSPECT": "재검사 대기", "PASS": "합격", "REJECT": "폐기·재제작",
}

def _type_name(code):
    from .services.inspections import type_name
    return type_name(code)


def _flag_label(flag):
    from .services.inspections import FLAG_LABELS
    return FLAG_LABELS.get(flag, flag)


templates.env.globals.update(
    type_name=_type_name,
    flag_label=_flag_label,
    domain_labels=reference.DOMAIN_LABELS,
    root=config.ROOT_PATH,
    static=static_url,
    app_name=config.APP_NAME,
    app_version=config.APP_VERSION,
    app_version_label=config.APP_VERSION_LABEL,
    team_name=config.TEAM_NAME,
    team_desc=config.TEAM_DESC,
    app_tagline=config.APP_TAGLINE,
    github_url=config.GITHUB_URL,
    role_labels=auth.ROLE_LABELS,
    status_labels=STATUS_LABELS,
)


def _bell_count() -> int:
    """우측 상단 알림: 조치 대기 + 재검사 대기 (TODO.md 5.3)"""
    try:
        with db.connect() as conn:
            return db.scalar(conn, "SELECT COUNT(*) FROM inspections WHERE status IN ('DEFECT','REINSPECT')") or 0
    except Exception:  # noqa: BLE001
        return 0


def render(request: Request, name: str, ctx: dict[str, Any] | None = None, *,
           active: str | None = None, status_code: int = 200):
    """포털·공개 화면 공통 렌더. active='inspections.list' 처럼 메뉴 위치를 넘긴다."""
    user = auth.get_user(request)
    ctx = dict(ctx or {})
    found = find_by_key(active)
    ctx.update(
        request=request,
        user=user,
        csrf_token=auth.csrf_token(request),
        menu=menu_for(user["role"]) if user else [],
        active=active or "",
        active_top=(active or "").split(".")[0],
        crumb=found,
        bell=_bell_count() if user else 0,
        csp_nonce=request.scope.get(middleware.NONCE_KEY, ""),
    )
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
