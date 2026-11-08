"""FastAPI 앱 생성: 미들웨어, 라우터 등록, 정적 파일, 오류 화면 (TODO.md 4장).

실행: python -m app   (또는 ./run.sh start)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, db
from .middleware import RootPathMiddleware, SecurityHeadersMiddleware
from .routes import admin, dashboard, defects, files, inspections, learning, placeholder, public, reports, segmentation, uploads
from .services import activity_log, ai_analysis, jobs
from .services import learning as learning_service
from .web import render, templates

log = logging.getLogger("weldsight")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    db.init_schema()
    for w in config.startup_warnings():
        log.warning(w)
    if not db.is_seeded():
        log.warning("DB에 데모 계정이 없습니다. `python -m app.seed.seed` 로 샘플 데이터를 만드세요.")
    else:
        with db.connect() as conn:
            ai_analysis.seed_docs(conn)
            learning_service.ensure_content(conn)
    log.info("QCheck %s 시작 · ROOT_PATH=%r · DB=%s", config.APP_VERSION, config.ROOT_PATH, config.DB_PATH)
    activity_log.log("SYSTEM", "APP_START", f"앱 시작 · v{config.APP_VERSION}",
                     {"root_path": config.ROOT_PATH, "ai_mode": config.AI_MODE})
    resumed = jobs.resume()  # 재시작 전에 끝나지 않은 AI 판정 이어서 처리
    ai_analysis.resume()      # AI 판정은 끝났지만 설명 작성 중이던 건 이어서 처리
    if resumed:
        log.info("AI 판정 %d건 이어서 처리", resumed)
    yield


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
    # API 문서는 공개 URL 에 노출하지 않는다
    docs_url=None, redoc_url=None, openapi_url=None,
)

# 미들웨어: 나중에 추가한 것이 바깥쪽. RootPath 가 가장 먼저 경로를 정리해야 한다
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie=config.SESSION_COOKIE,   # 같은 도메인의 다른 팀과 겹치지 않게
    max_age=config.SESSION_MAX_AGE,
    path=config.ROOT_PATH or "/",
    same_site="lax",
    https_only=config.SESSION_HTTPS_ONLY,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RootPathMiddleware, root_path=config.ROOT_PATH)

app.mount("/static", StaticFiles(directory=str(config.APP_DIR / "static")), name="static")

# 라우터 등록 순서 주의: placeholder(/portal/{rest:path})는 반드시 마지막
app.include_router(public.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(inspections.router)
app.include_router(defects.router)
app.include_router(uploads.router)       # 사진 여러 장 업로드 → 저장 → AI 판정 (3단계)
app.include_router(files.router)         # 업로드 사진·썸네일 (로그인 필요)
app.include_router(segmentation.router)  # 모델 A 단건 프록시 (32-Web 통합)
app.include_router(learning.router)      # 교육 과정·퀴즈 (35-Web 자료 통합)
app.include_router(reports.router)       # 보고서 생성·보관함·PDF
app.include_router(placeholder.router)


# ── 오류 처리 ────────────────────────────────────────────────
def _route_path(request: Request) -> str:
    root = request.scope.get("root_path", "")
    p = request.scope.get("path", "")
    return p[len(root):] if root and p.startswith(root) else p


def _wants_json(request: Request) -> bool:
    rp = _route_path(request)
    if rp.startswith("/portal/api/") or rp == "/healthz":
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


ERROR_TEXT = {
    400: ("요청을 처리할 수 없습니다", "입력값이 올바르지 않습니다."),
    401: ("로그인이 필요합니다", "다시 로그인해 주세요."),
    403: ("접근할 수 없습니다", "이 화면이나 기능을 사용할 권한이 없습니다."),
    404: ("페이지를 찾을 수 없습니다", "주소가 바뀌었거나 없는 항목입니다."),
    405: ("허용되지 않은 요청입니다", "이 주소는 그 방식으로 요청할 수 없습니다."),
    429: ("요청이 너무 많습니다", "잠시 뒤에 다시 시도해 주세요."),
    500: ("일시적인 오류가 발생했습니다", "문제가 기록되었습니다. 잠시 뒤 다시 시도해 주세요."),
}


def _error_response(request: Request, code: int, detail: str | None = None):
    title, msg = ERROR_TEXT.get(code, (f"오류 {code}", "요청을 처리하지 못했습니다."))
    if detail and detail not in ("Not Found", "Method Not Allowed", "Internal Server Error"):
        msg = detail
    if _wants_json(request):
        return JSONResponse({"ok": False, "error": msg, "status": code}, status_code=code)
    return render(request, "error.html", {"code": code, "title": title, "message": msg}, status_code=code)


@app.exception_handler(auth.LoginRequired)
async def _login_required(request: Request, _exc: auth.LoginRequired):
    if _wants_json(request):
        return JSONResponse({"ok": False, "error": "로그인이 필요합니다.", "status": 401}, status_code=401)
    return public.login_redirect(request)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    return _error_response(request, exc.status_code, exc.detail if isinstance(exc.detail, str) else None)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    # 예: /portal/inspections/abc (id 가 int 가 아님) → HTML 은 404 로 보여 준다
    if _wants_json(request):
        return JSONResponse({"ok": False, "error": "입력값 오류", "detail": exc.errors(), "status": 422},
                            status_code=422)
    return _error_response(request, 404 if request.method == "GET" else 400)


@app.exception_handler(Exception)
async def _server_error(request: Request, exc: Exception):
    """처리되지 않은 예외. Starlette 는 이 핸들러를 가장 바깥(세션·경로 미들웨어 밖)에서 부르므로
    request.session 을 쓰지 않고, 로그인 정보 없는 공개 레이아웃으로 오류 화면을 보여 준다."""
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    user = getattr(request.state, "user", None)
    path = request.scope.get("path", "")
    if config.ROOT_PATH and path.startswith(config.ROOT_PATH):
        path = path[len(config.ROOT_PATH):]
    activity_log.log("SYSTEM", "ERROR", f"서버 오류 · {request.method} {path}"[:200],
                     {"error": f"{type(exc).__name__}: {exc}"[:1000]},
                     user_id=user["id"] if user else None, level="ERROR", ip=auth.client_ip(request))
    title, msg = ERROR_TEXT[500]
    if path.startswith("/portal/api/") or path == "/healthz":
        return JSONResponse({"ok": False, "error": msg, "status": 500}, status_code=500)
    try:
        return templates.TemplateResponse(request, "error.html", {
            "request": request, "user": None, "csrf_token": "", "menu": [], "active": "", "active_top": "",
            "crumb": None, "bell": 0, "code": 500, "title": title, "message": msg}, status_code=500)
    except Exception:  # noqa: BLE001 — 오류 화면 렌더링마저 실패하면 최소 응답
        return JSONResponse({"ok": False, "error": "server error", "status": 500}, status_code=500)
