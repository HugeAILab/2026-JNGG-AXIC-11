"""경로 접두사(ROOT_PATH) 처리와 공통 보안 헤더.

외부 주소: https://devopen.jntp-ax.or.kr/i/<개별 경로>/...
프록시가 접두사를 지우고 보내든(/portal/...) 붙인 채 보내든(/i/xxx/portal/...)
앱이 똑같이 동작하도록, ASGI 규약대로 scope["path"] 는 접두사를 포함하고
scope["root_path"] 에 접두사를 넣는다. Starlette 라우터·StaticFiles 는 이 둘의 차이로 라우팅한다.
→ uvicorn 의 --root-path 옵션은 쓰지 않는다 (TODO.md 12.2, 14장 #7).
"""
from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

RAW_PATH_KEY = "weldsight.raw_path"  # 프록시가 실제로 보낸 경로 (/healthz 에서 확인용)


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
    """기본 보안 헤더 + 로그인 화면 캐시 금지 (로그아웃 뒤 '뒤로 가기'로 데이터가 보이지 않게)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        root = scope.get("root_path", "")
        path = scope.get("path", "")
        route_path = path[len(root):] if root and path.startswith(root) else path
        no_store = not route_path.startswith("/static/")

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("Referrer-Policy", "same-origin")
                h.setdefault("X-Frame-Options", "SAMEORIGIN")
                if no_store:
                    h.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, _send)
