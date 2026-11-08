"""공개 화면: 원 페이지(/), 로그인(/login), 로그아웃, /healthz (TODO.md 5.4, 5.5, 8.1)."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import auth, config, db
from ..middleware import RAW_PATH_KEY
from ..seed.accounts import DEMO_ACCOUNTS, DEMO_PASSWORD
from ..services import activity_log
from ..web import render, url

router = APIRouter()


def _safe_next(nxt: str | None) -> str:
    """오픈 리다이렉트 방지: 우리 포털 경로만 허용"""
    default = url("/portal/dashboard")
    if not nxt:
        return default
    prefix = url("/portal")
    if not (nxt == prefix or nxt.startswith(prefix + "/") or nxt.startswith(prefix + "?")):
        return default
    if "//" in nxt or "\\" in nxt or "\n" in nxt or "\r" in nxt:
        return default
    return nxt


@router.get("/")
def landing(request: Request):
    return render(request, "landing.html")


@router.get("/login")
def login_form(request: Request, next: str | None = None, bye: int = 0):
    if auth.get_user(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return render(request, "login.html", {
        "next": _safe_next(next), "bye": bool(bye), "demo_accounts": DEMO_ACCOUNTS,
        "demo_password": DEMO_PASSWORD, "error": None, "username": "",
    })


@router.post("/login", dependencies=[Depends(auth.verify_csrf)])
def login_submit(request: Request, username: str = Form(""), password: str = Form(""),
                 next: str = Form("")):
    ip = auth.client_ip(request)
    username = username.strip()[:50]
    ctx = {"next": _safe_next(next), "bye": False, "demo_accounts": DEMO_ACCOUNTS,
           "demo_password": DEMO_PASSWORD, "username": username}

    wait = auth.lock_remaining(username, ip)
    if wait:
        ctx["error"] = f"로그인 실패가 반복되어 잠시 막혔습니다. {wait}초 뒤에 다시 시도하세요."
        return render(request, "login.html", ctx, status_code=429)

    user = auth.authenticate(username, password) if username and password else None
    if user is None:
        wait = auth.record_fail(username, ip)
        activity_log.log("AUTH", "LOGIN_FAIL", f"로그인 실패 · {username or '(빈 아이디)'}",
                         {"username": username}, level="WARN", ip=ip)
        ctx["error"] = (f"로그인 실패가 반복되어 잠시 막혔습니다. {wait}초 뒤에 다시 시도하세요." if wait
                        else "아이디 또는 비밀번호가 올바르지 않습니다.")
        return render(request, "login.html", ctx, status_code=429 if wait else 401)

    auth.clear_fails(username, ip)
    request.session.clear()  # 세션 고정 방지: 로그인하면 새 세션
    request.session["uid"] = user["id"]
    auth.csrf_token(request)
    activity_log.log("AUTH", "LOGIN", f"로그인 · {user['name']} ({user['username']})",
                     user_id=user["id"], ip=ip)
    return RedirectResponse(ctx["next"], status_code=303)


@router.post("/logout", dependencies=[Depends(auth.verify_csrf)])
def logout(request: Request):
    user = auth.get_user(request)
    if user:
        activity_log.log("AUTH", "LOGOUT", f"로그아웃 · {user['name']} ({user['username']})",
                         user_id=user["id"], ip=auth.client_ip(request))
    request.session.clear()
    return RedirectResponse(url("/login?bye=1"), status_code=303)


@router.get("/healthz")
def healthz(request: Request):
    """앱 상태 + 프록시 경로 처리 확인용 (TODO.md 14장 #7). 데이터는 담지 않는다."""
    ok_db, seeded = False, False
    try:
        with db.connect() as conn:
            db.scalar(conn, "SELECT 1")
            ok_db = True
            seeded = (db.scalar(conn, "SELECT COUNT(*) FROM users") or 0) > 0
    except Exception:  # noqa: BLE001
        pass
    raw = request.scope.get(RAW_PATH_KEY, "")
    root = config.ROOT_PATH
    return JSONResponse({
        "ok": ok_db,
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "db": ok_db,
        "seeded": seeded,
        "time_kst": db.now_str(),
        "root_path": root,
        "path_received": raw,
        "proxy_prefix": ("kept" if root and (raw == root or raw.startswith(root + "/"))
                         else "stripped" if root else "none (ROOT_PATH 비어 있음)"),
        "scheme": request.url.scheme,
        "client": auth.client_ip(request),
    }, status_code=200 if ok_db else 503)


def login_redirect(request: Request) -> RedirectResponse:
    """로그인이 필요할 때 /login?next=<현재 경로> 로 (main.py 핸들러에서 사용)"""
    here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(url("/login?next=" + quote(here, safe="")), status_code=303)
