"""아직 개발하지 않은 메뉴 링크 → '준비 중' 화면. 반드시 가장 마지막에 등록한다.

메뉴(MENU)에 있는 url 만 받아 주고 나머지는 404. 각 단계에서 실제 라우트를 만들면
그 라우트가 먼저 등록되어 이 화면은 자연히 쓰이지 않게 된다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import auth
from ..routes import find_by_url
from ..web import render

router = APIRouter()


@router.get("/portal/{rest:path}")
def placeholder(rest: str, request: Request, user: dict = Depends(auth.require_login)):
    path = "/portal/" + rest
    found = find_by_url(path + (f"?{request.url.query}" if request.url.query else "")) or find_by_url(path)
    if not found:
        raise HTTPException(status_code=404)
    top, group, item = found
    if item.get("roles") and user["role"] not in item["roles"]:
        raise HTTPException(status_code=403, detail="이 메뉴를 볼 권한이 없습니다.")
    return render(request, "portal/placeholder.html", {"item": item, "group": group, "top": top},
                  active=f"{top['key']}.{item['key']}")
