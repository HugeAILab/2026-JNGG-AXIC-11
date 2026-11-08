"""경로 접두사(ROOT_PATH) 처리와 공통 보안 헤더.

외부 주소: https://devopen.jntp-ax.or.kr/i/<개별 경로>/...
프록시가 접두사를 지우고 보내든(/portal/...) 붙인 채 보내든(/i/xxx/portal/...)
앱이 똑같이 동작하도록, ASGI 규약대로 scope["path"] 는 접두사를 포함하고
scope["root_path"] 에 접두사를 넣는다. Starlette 라우터·StaticFiles 는 이 둘의 차이로 라우팅한다.
→ uvicorn 의 --root-path 옵션은 쓰지 않는다 (TODO.md 12.2, 14장 #7).
"""
from __future__ import annotations

import secrets

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

RAW_PATH_KEY = "weldsight.raw_path"  # 프록시가 실제로 보낸 경로 (/healthz 에서 확인용)
NONCE_KEY = "weldsight.csp_nonce"    # 요청 1건의 CSP nonce (web.render 가 템플릿으로 넘긴다)

# 스타일은 'unsafe-inline' 을 남긴다: style="" 속성(보고서 결함 박스 위치)은 nonce 로 덮을 수 없다.
# 외부 스크립트는 없다(chart.js 는 static/vendor 자체 제공). 폰트 CSS 만 cdn.jsdelivr.net 을 허용한다.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "          # 파비콘이 data:image/svg+xml 인라인
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'self'"
)


class RootPathMiddleware:
    def __init__(self, app: ASGIApp, root_path: str) -> None:
        self.app = app
        self.root = root_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        raw = scope.get("path", "") or "/"
        scope = dict(scope)
        scope[RAW_PATH_KEY] = raw
        root = self.root
        if root:
            if raw == root or raw.startswith(root + "/"):
                path = raw                      # 프록시가 접두사를 붙인 채 보냄
            else:
                path = root + (raw if raw.startswith("/") else "/" + raw)  # 접두사가 지워져 옴
            if path == root:
                path = root + "/"
            scope["path"] = path
            scope["raw_path"] = path.encode("utf-8")
            scope["root_path"] = root
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """기본 보안 헤더 + CSP(nonce) + 로그인 화면 캐시 금지 (로그아웃 뒤 '뒤로 가기'로 데이터가 보이지 않게)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        root = scope.get("root_path", "")
        path = scope.get("path", "")
        route_path = path[len(root):] if root and path.startswith(root) else path
        # /static/ 도 CSP 를 붙인다: 없는 파일 요청은 HTML 오류 화면으로 돌아오므로
        # 여기서 빼면 그 화면만 CSP 없이 나간다. no-store 만 정적 파일에서 제외한다.
        is_static = route_path.startswith("/static/")
        nonce = secrets.token_urlsafe(16)
        scope = dict(scope)
        scope[NONCE_KEY] = nonce

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("Referrer-Policy", "same-origin")
                h.setdefault("X-Frame-Options", "SAMEORIGIN")
                h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                h.setdefault("Content-Security-Policy", _CSP.format(nonce=nonce))
                if not is_static:
                    h.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, _send)
