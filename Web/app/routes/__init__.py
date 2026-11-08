"""포털 메뉴 정의 1곳 (TODO.md 5.2). base.html 이 이 상수를 읽어 상단·좌측 메뉴를 그린다.

구조: 대메뉴(top) > 중메뉴(group) > 링크(item)
- key: 'dashboard.overview' 처럼 '대메뉴.링크'. 라우트가 active 로 넘기면 해당 위치가 강조된다
- roles: 지정하면 그 역할에게만 보인다 (없으면 모두)
- marker: True 면 링크가 아니라 위치 표시용 (예: 검사 상세 — id 가 있어야 열림)
- stage: 개발 단계. 아직 개발 전인 링크는 '준비 중' 화면으로 연결된다 (routes/placeholder.py)
"""
from __future__ import annotations

from typing import Any

MENU: list[dict[str, Any]] = [
    {"key": "dashboard", "label": "대시보드", "groups": [
        {"label": "품질 현황", "items": [
            {"key": "overview", "label": "종합 현황", "url": "/portal/dashboard", "stage": 4},
            {"key": "defects", "label": "결함 유형 분석", "url": "/portal/dashboard/defects", "stage": 4},
            {"key": "areas", "label": "구역·작업자 분석", "url": "/portal/dashboard/areas", "stage": 4},
        ]},
    ]},
    {"key": "inspections", "label": "품질 검사", "groups": [
        {"label": "검사 수행", "items": [
            {"key": "upload", "label": "사진 업로드 검사", "url": "/portal/inspections/upload", "stage": 3},
            {"key": "uploads", "label": "업로드 이력", "url": "/portal/inspections/uploads", "stage": 3},
        ]},
        {"label": "검사 조회", "items": [
            {"key": "list", "label": "전체 검사 목록", "url": "/portal/inspections", "stage": 2},
            {"key": "detail", "label": "검사 상세", "url": "/portal/inspections", "marker": True, "stage": 2},
        ]},
    ]},
    {"key": "defects", "label": "불량 관리", "groups": [
        {"label": "처리 대기", "items": [
            {"key": "DEFECT", "label": "조치 대기 (불량)", "url": "/portal/defects?status=DEFECT", "stage": 2},
            {"key": "REWORK", "label": "수정 중", "url": "/portal/defects?status=REWORK", "stage": 2},
            {"key": "REINSPECT", "label": "재검사 대기", "url": "/portal/defects?status=REINSPECT", "stage": 2},
        ]},
        {"label": "처리 이력", "items": [
            {"key": "CLOSED", "label": "완료된 불량", "url": "/portal/defects?status=CLOSED", "stage": 2},
            {"key": "history", "label": "전체 이력 타임라인", "url": "/portal/defects/history", "stage": 2},
        ]},
    ]},
    {"key": "reports", "label": "보고서", "groups": [
        {"label": "보고서 보관함", "items": [
            {"key": "all", "label": "전체 보고서", "url": "/portal/reports"},
            {"key": "DEFECT", "label": "결함 보고서", "url": "/portal/reports?type=DEFECT"},
            {"key": "COMPLETION", "label": "완료 보고서", "url": "/portal/reports?type=COMPLETION"},
        ]},
    ]},
    {"key": "learning", "label": "교육·퀴즈", "groups": [
        # 7단계에서 edu_classes 로 동적 생성되는 중메뉴로 바뀐다
        {"label": "교육 과정", "items": [
            {"key": "classes", "label": "교육 과정 전체", "url": "/portal/learning", "stage": 7},
        ]},
        {"label": "퀴즈", "items": [
            {"key": "quiz", "label": "퀴즈 모아 풀기", "url": "/portal/learning/quiz", "stage": 7},
            {"key": "quiz_history", "label": "나의 퀴즈 기록", "url": "/portal/learning/quiz/history", "stage": 7},
        ]},
    ]},
    {"key": "admin", "label": "시스템", "groups": [
        {"label": "관리", "items": [
            {"key": "users", "label": "사용자 목록", "url": "/portal/admin/users", "roles": ["admin"], "stage": 1},
            {"key": "logs", "label": "활동 로그", "url": "/portal/admin/logs", "roles": ["admin", "manager"], "stage": 3},
            {"key": "health", "label": "AI 연결 상태", "url": "/portal/admin/health", "roles": ["admin"], "stage": 3},
            {"key": "db", "label": "DB 테이블 정보", "url": "/portal/admin/db", "roles": ["admin"], "stage": 4},
        ]},
    ]},
]


def _visible(entry: dict, role: str | None) -> bool:
    roles = entry.get("roles")
    return not roles or (role in roles)


def menu_for(role: str | None) -> list[dict[str, Any]]:
    """역할에 맞게 거른 메뉴. 각 대메뉴에 첫 링크 url(href)을 붙인다."""
    out = []
    for top in MENU:
        groups = []
        for g in top["groups"]:
            items = [it for it in g["items"] if _visible(it, role)]
            if items:
                groups.append({**g, "items": items})
        if not groups:
            continue
        first = next(it for g in groups for it in g["items"] if not it.get("marker"))
        out.append({**top, "groups": groups, "href": first["url"]})
    return out


def find_by_url(url: str) -> tuple[dict, dict, dict] | None:
    """'/portal/defects?status=DEFECT' 같은 url 로 (top, group, item) 찾기 — 준비 중 화면에서 사용."""
    for top in MENU:
        for g in top["groups"]:
            for it in g["items"]:
                if not it.get("marker") and it["url"] == url:
                    return top, g, it
    return None


def find_by_key(active: str | None) -> tuple[dict, dict, dict] | None:
    if not active or "." not in active:
        return None
    tkey, ikey = active.split(".", 1)
    for top in MENU:
        if top["key"] != tkey:
            continue
        for g in top["groups"]:
            for it in g["items"]:
                if it["key"] == ikey:
                    return top, g, it
    return None
