"""비밀번호 해시, 로그인·역할 확인 의존성, CSRF 토큰, 로그인 실패 제한 (TODO.md 7.3, 8.1)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Callable

from fastapi import Depends, HTTPException, Request

from . import db

# ── 비밀번호 ──────────────────────────────────────────────────
PBKDF2_ITER = 200_000


def hash_password(password: str, *, iterations: int = PBKDF2_ITER) -> str:
    salt = secrets.token_hex(16)  # 16바이트
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, it, salt, hexhash = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), hexhash)
    except (ValueError, TypeError):
        return False


# 존재하지 않는 아이디로도 같은 시간이 걸리게 하는 더미 해시 (아이디 존재 여부 노출 방지)
_DUMMY_HASH = hash_password(secrets.token_hex(8))


# ── 로그인 실패 제한 (메모리, --workers 1 전제) ─────────────────
MAX_FAILS = 5
LOCK_SECONDS = 60
_fail_lock = threading.Lock()
_fails: dict[tuple[str, str], tuple[int, float]] = {}  # (username, ip) -> (연속 실패 수, 잠금 해제 시각)


def lock_remaining(username: str, ip: str) -> int:
    with _fail_lock:
        n, until = _fails.get((username.lower(), ip), (0, 0.0))
    return max(0, int(until - time.time() + 0.999)) if until else 0


def record_fail(username: str, ip: str) -> int:
    """실패 1회 기록 → 잠겼으면 남은 초, 아니면 0"""
    key = (username.lower(), ip)
    with _fail_lock:
        n, until = _fails.get(key, (0, 0.0))
        if until and until <= time.time():
            n, until = 0, 0.0
        n += 1
        if n >= MAX_FAILS:
            until = time.time() + LOCK_SECONDS
            n = 0
        _fails[key] = (n, until)
    return lock_remaining(username, ip)


def clear_fails(username: str, ip: str) -> None:
    with _fail_lock:
        _fails.pop((username.lower(), ip), None)


def authenticate(username: str, password: str) -> dict | None:
    with db.connect() as conn:
        row = db.one(conn, "SELECT * FROM users WHERE username = ?", (username.strip(),))
    if row is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return dict(row) if verify_password(password, row["password_hash"]) else None


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers 가 X-Forwarded-For 첫 값을 request.client 에 넣어 준다
    return request.client.host if request.client else "-"


# ── 세션 사용자 ───────────────────────────────────────────────
class LoginRequired(Exception):
    """로그인이 필요함. main.py 의 핸들러가 HTML 은 /login?next= 로, API 는 401 JSON 으로 바꾼다."""


def get_user(request: Request) -> dict | None:
    """세션의 사용자 (없으면 None). 요청당 1회만 조회하도록 request.state 에 캐시."""
    if hasattr(request.state, "user"):
        return request.state.user
    user = None
    uid = request.session.get("uid")
    if uid:
        with db.connect() as conn:
            row = db.one(conn, "SELECT id, username, name, role FROM users WHERE id = ?", (uid,))
        if row:
            user = dict(row)
        else:
            request.session.clear()
    request.state.user = user
    return user


def require_login(request: Request) -> dict:
    user = get_user(request)
    if user is None:
        raise LoginRequired()
    return user


ROLE_LABELS = {"admin": "관리자", "manager": "품질 관리자", "inspector": "품질 검사자", "worker": "생산·용접 담당"}


def require_role(*roles: str) -> Callable[..., dict]:
    """Depends(require_role("admin", "manager")) — 역할별 권한표(TODO.md 7.3)를 서버에서 검사."""
    def _dep(user: dict = Depends(require_login)) -> dict:
        if user["role"] not in roles:
            allowed = ", ".join(ROLE_LABELS.get(r, r) for r in roles)
            raise HTTPException(status_code=403, detail=f"이 기능은 {allowed}만 사용할 수 있습니다.")
        return user
    return _dep


def has_role(user: dict | None, *roles: str) -> bool:
    return bool(user) and user["role"] in roles


# ── CSRF ─────────────────────────────────────────────────────
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


async def verify_csrf(request: Request) -> None:
    """모든 상태 변경 POST 에 Depends(verify_csrf). 폼 hidden 필드 또는 X-CSRF-Token 헤더."""
    expected = request.session.get("csrf")
    sent = request.headers.get(CSRF_HEADER)
    form = None
    if not sent:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()  # Starlette 가 캐시하므로 라우트의 Form() 과 함께 써도 된다
            sent = form.get(CSRF_FIELD)
    if not expected or not sent or not hmac.compare_digest(str(sent), expected):
        if form is not None:
            await form.close()  # 거부한 요청의 업로드 임시 파일 정리
        raise HTTPException(status_code=403, detail="보안 토큰이 없거나 만료되었습니다. 페이지를 새로 고친 뒤 다시 시도하세요.")
