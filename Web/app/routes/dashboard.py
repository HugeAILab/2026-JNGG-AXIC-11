"""대시보드 3화면 + 집계 JSON (TODO.md 8.2).

페이지는 뼈대만 렌더링하고 GET /portal/api/stats?days= 가 모든 집계를 JSON 1개로 돌려준다.
dashboard.js 가 Chart.js 로 그린다. 세 화면은 같은 템플릿에 view 값만 다르다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import auth, db
from ..services import stats
from ..web import render, url

router = APIRouter()
VIEWS = {
    "overview": ("/portal/dashboard", "종합 현황", "KPI · 일별 추이 · 불량 처리 단계 · 기한 임박·초과 미결 불량"),
    "defects": ("/portal/dashboard/defects", "결함 유형 분석", "작업 영역별 불량 · 결함 유형 분포 · 유형별 처리 · AI 불일치 · 검토 필요 사유"),
    "areas": ("/portal/dashboard/areas", "구역·작업자 분석", "블록별 불량률 · 용접법별 불량률 · 용접사별 불량 Top 5"),
}


@router.get("/portal")
@router.get("/portal/")
def portal_root():
    return RedirectResponse(url("/portal/dashboard"), status_code=303)


def _days(request: Request) -> str:
    d = request.query_params.get("days", "30")
    return d if d in stats.PERIODS else "30"


def _page(request: Request, view: str):
    path, title, desc = VIEWS[view]
    return render(request, "portal/dashboard.html", {
        "view": view, "title": title, "desc": desc, "days": _days(request), "views": VIEWS,
    }, active=f"dashboard.{view}")


@router.get("/portal/dashboard")
def dashboard(request: Request, user: dict = Depends(auth.require_login)):
    return _page(request, "overview")


@router.get("/portal/dashboard/defects")
def dashboard_defects(request: Request, user: dict = Depends(auth.require_login)):
    return _page(request, "defects")


@router.get("/portal/dashboard/areas")
def dashboard_areas(request: Request, user: dict = Depends(auth.require_login)):
    return _page(request, "areas")


@router.get("/portal/api/stats")
def api_stats(request: Request, user: dict = Depends(auth.require_login)):
    with db.connect() as conn:
        return stats.compute(conn, stats.PERIODS[_days(request)])
