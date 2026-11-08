"""스모크 테스트 (TODO.md 12.2): 실행 중인 서버에 실제 HTTP 요청을 보내 핵심 동작을 확인한다.

    python -m app.tools.smoke http://127.0.0.1:8501
    python -m app.tools.smoke https://devopen.jntp-ax.or.kr/i/<개별 경로>

데이터를 바꾸지 않는다(로그인·로그아웃 활동 로그만 남는다). 1단계 범위만 검사하며 단계마다 항목을 추가한다.
"""
from __future__ import annotations

import argparse
import re
import sys
import time

import httpx

from ..routes import MENU
from ..seed.accounts import DEMO_PASSWORD

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


class Smoke:
    def __init__(self, base: str, verify: bool, ai: bool = False) -> None:
        self.base = base.rstrip("/")
        self.verify = verify
        self.ai = ai
        self.ok = self.fail = 0

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base, follow_redirects=False, timeout=20, verify=self.verify)

    def check(self, name: str, cond: bool, info: str = "") -> bool:
        if cond:
            self.ok += 1
            print(f"  [ OK ] {name}")
        else:
            self.fail += 1
            print(f"  [FAIL] {name}  {info}")
        return cond

    @staticmethod
    def csrf(html: str) -> str:
        m = CSRF_RE.search(html)
        return m.group(1) if m else ""

    def login(self, c: httpx.Client, username: str) -> bool:
        tok = self.csrf(c.get("/login").text)
        r = c.post("/login", data={"username": username, "password": DEMO_PASSWORD, "csrf_token": tok, "next": ""})
        if r.status_code != 303:
            return False
        r2 = c.get("/portal/dashboard")
        return r2.status_code == 200

    def run(self) -> int:
        t0 = time.time()
        print(f"QCheck 스모크 테스트 → {self.base}")

        print("\n[1] 공개 화면 · 경로 접두사")
        with self.client() as c:
            r = c.get("/healthz")
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            self.check("/healthz 200 + DB 연결", r.status_code == 200 and j.get("db") is True, f"status={r.status_code}")
            self.check("샘플 데이터 있음 (seeded)", j.get("seeded") is True, "python -m app.seed.seed 실행 필요")
            if j:
                print(f"         root_path={j.get('root_path')!r} path_received={j.get('path_received')!r} "
                      f"proxy_prefix={j.get('proxy_prefix')} scheme={j.get('scheme')}")
            r = c.get("/")
            self.check("원 페이지 / 200", r.status_code == 200 and "QCheck" in r.text)
            css = re.search(r'href="([^"]*landing\.css[^"]*)"', r.text)
            if self.check("원 페이지 CSS 경로에 접두사 포함", bool(css)):
                path = css.group(1)
                full = httpx.URL(self.base).copy_with(path=path.split("?")[0], query=None)
                rs = c.get(str(full))
                self.check("정적 파일(CSS) 로드", rs.status_code == 200 and "text/css" in rs.headers.get("content-type", ""),
                           f"{full} → {rs.status_code}")
            r = c.get("/login")
            self.check("로그인 화면 200 + CSRF 토큰", r.status_code == 200 and bool(self.csrf(r.text)))
            self.check("데모 계정 안내 표시", "demo-fill" in r.text)
            r = c.get("/portal/dashboard")
            loc = r.headers.get("location", "")
            self.check("미로그인 /portal → /login?next= 로 이동", r.status_code == 303 and "/login?next=" in loc, f"{r.status_code} {loc}")
            r = c.get("/static/%2e%2e/config.py")
            self.check("정적 경로 밖 파일(소스) 접근 차단", r.status_code in (400, 404), f"status={r.status_code}")

        print("\n[2] 로그인 · CSRF · 실패 처리")
        with self.client() as c:
            tok = self.csrf(c.get("/login").text)
            r = c.post("/login", data={"username": "admin", "password": DEMO_PASSWORD})
            self.check("CSRF 토큰 없는 POST 거부 (403)", r.status_code == 403, f"status={r.status_code}")
            r = c.post("/login", data={"username": "admin", "password": "wrong-pass", "csrf_token": tok})
            self.check("잘못된 비밀번호 → 401 + 안내 문구", r.status_code == 401 and "올바르지 않습니다" in r.text, f"status={r.status_code}")
            r = c.post("/login", data={"username": "admin", "password": DEMO_PASSWORD, "csrf_token": tok,
                                       "next": "https://evil.example/"})
            loc = r.headers.get("location", "")
            self.check("로그인 성공 → 303, 외부 next 무시", r.status_code == 303 and loc.endswith("/portal/dashboard"), loc)
            cookie_ok = c.get("/portal/dashboard").status_code == 200
            self.check("로그인 유지 (세션 쿠키)", cookie_ok,
                       "http:// 로 접속했다면 .env 의 SESSION_HTTPS_ONLY=false 필요")
            if not cookie_ok:
                return self.finish(t0)

            print("\n[3] 포털 화면 (admin)")
            r = c.get("/portal/dashboard")
            self.check("대시보드 200 + KPI·차트 뼈대", "총 검사 건수" in r.text and "dashboard.js" in r.text)
            s = c.get("/portal/api/stats?days=")
            self.check("대시보드 집계 JSON", s.status_code == 200 and s.json().get("ok") is True
                       and s.json()["kpi"]["total"] > 0, f"status={s.status_code}")
            self.check("DB 테이블 정보 (admin)", "inspections" in c.get("/portal/admin/db").text)
            self.check("상단 대메뉴 6개", all(t["label"] in r.text for t in MENU))
            urls = sorted({it["url"] for t in MENU for g in t["groups"] for it in g["items"] if not it.get("marker")})
            bad = [u for u in urls if c.get(u).status_code != 200]
            self.check(f"메뉴 링크 {len(urls)}개 모두 열림 (준비 중 화면 포함)", not bad, f"실패: {bad}")
            r = c.get("/portal/admin/users")
            self.check("사용자 목록 (admin) 200", r.status_code == 200 and "inspector" in r.text)
            r = c.get("/portal/no-such-page")
            self.check("없는 주소 → 404 오류 화면", r.status_code == 404 and "페이지를 찾을 수 없습니다" in r.text)
            r = c.get("/portal/api/no-such", headers={"accept": "application/json"})
            self.check("API 주소 오류는 JSON", r.headers.get("content-type", "").startswith("application/json"))
            self.check("포털 응답 캐시 금지 헤더", "no-store" in c.get("/portal/dashboard").headers.get("cache-control", ""))

            print("\n[3-2] 2단계: 검사 목록 · 상세 · 불량 관리")
            r = c.get("/portal/inspections")
            m = re.search(r"총 (\d+)건", r.text)
            total = int(m.group(1)) if m else 0
            self.check("검사 목록 200 + 건수 표시", r.status_code == 200 and total > 0, f"total={total}")
            first = re.search(r'/portal/inspections/(\d+)"', r.text)
            r = c.get("/portal/inspections?review=1&domain=WELD&sort=conf&page=1")
            self.check("목록 필터·정렬 동작", r.status_code == 200)
            if self.check("목록에서 상세 링크 찾음", bool(first)):
                iid = first.group(1)
                r = c.get(f"/portal/inspections/{iid}")
                self.check("검사 상세 200 + AI 판정·처리 이력",
                           r.status_code == 200 and "AI 판정" in r.text and "처리 이력" in r.text)
                r = c.get(f"/portal/api/inspections/{iid}/ai")
                j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                self.check("AI 결과 JSON (모델 A 결과 포함)", j.get("ok") is True and j.get("A") is not None)
            r = c.get("/portal/inspections/99999999")
            self.check("없는 검사 번호 → 목록 + 안내 캐릭터 (404)",
                       r.status_code == 404 and "찾을 수 없어요" in r.text and "전체 검사 목록" in r.text
                       and "lost-inspector.png" in r.text)
            self.check("숫자 아닌 검사 주소 → 404", c.get("/portal/inspections/abc").status_code == 404)
            bad = [v for v in ("DEFECT", "REWORK", "REINSPECT", "CLOSED")
                   if c.get(f"/portal/defects?status={v}").status_code != 200]
            self.check("불량 관리 대기열 4종", not bad, f"실패: {bad}")
            self.check("전체 이력 타임라인", c.get("/portal/defects/history").status_code == 200)

            print("\n[4] 로그아웃")
            r = c.post("/logout")
            self.check("CSRF 없는 로그아웃 거부 (403)", r.status_code == 403)
            tok = self.csrf(c.get("/portal/dashboard").text)
            r = c.post("/logout", data={"csrf_token": tok})
            self.check("로그아웃 → /login?bye=1", r.status_code == 303 and "/login?bye=1" in r.headers.get("location", ""))
            self.check("로그아웃 후 포털 접근 차단", c.get("/portal/dashboard").status_code == 303)

        print("\n[5] 역할 권한")
        with self.client() as c:
            if self.check("worker 로그인", self.login(c, "worker")):
                r = c.get("/portal/admin/users")
                self.check("worker → 사용자 목록 403 오류 화면", r.status_code == 403 and "접근할 수 없습니다" in r.text,
                           f"status={r.status_code}")
                r = c.get("/portal/dashboard")
                self.check("worker 메뉴에 관리자 전용 링크 숨김", "/portal/admin/users" not in r.text)
        for un in ("manager", "inspector"):
            with self.client() as c:
                self.check(f"{un} 로그인", self.login(c, un))

        print("\n[6] 사진 업로드 검사 (3단계) · 모델 A 연결")
        with self.client() as c:
            if self.check("inspector 로그인", self.login(c, "inspector")):
                r = c.get("/portal/inspections/upload")
                self.check("사진 업로드 화면 (여러 장 · 썸네일)",
                           r.status_code == 200 and "upload-form" in r.text and "upload.js" in r.text)
                r2 = c.get("/portal/inspections/uploads")
                self.check("업로드 이력 화면", r2.status_code == 200 and "업로드 이력" in r2.text)
                h = c.get("/portal/api/segmentation/health")
                ready = h.status_code == 200 and h.json().get("ok") is True
                print(f"         모델 A API 상태: {'준비 완료' if ready else '연결 안 됨 (실패로 세지 않음)'}")
                if self.ai:
                    self.check("모델 A API 준비 완료 (--ai)", ready)
                    if ready:
                        self.real_api(c, r.text)
        return self.finish(t0)

    def real_api(self, c: httpx.Client, page_html: str) -> None:
        """합성 이미지 1장으로 실제 모델 A 호출 (32-Web segmentation_smoke.py). 정확도 검증이 아니라 연동 확인."""
        import io
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (640, 480), "#888888")
        draw = ImageDraw.Draw(image)
        draw.rectangle((270, 0, 370, 480), fill="#aaaaaa")
        for y in range(0, 480, 25):
            draw.arc((270, y, 370, y + 35), 0, 180, fill="#555555", width=3)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        token = self.csrf(page_html)
        t = time.time()
        r = c.post("/portal/api/segmentation", headers={"X-CSRF-Token": token}, timeout=240,
                   files={"file": ("synthetic-test.png", buf.getvalue(), "image/png")})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok = r.status_code == 200 and body.get("image") == {"width": 640, "height": 480} \
            and isinstance(body.get("result", {}).get("segmentation", {}).get("instances"), list)
        n = len(body["result"]["segmentation"]["instances"]) if ok else "-"
        self.check(f"실제 모델 A 호출 (합성 이미지, 후보 {n}개, {time.time() - t:.1f}초)", ok,
                   f"status={r.status_code} {str(body.get('error', ''))[:80]}")

    def finish(self, t0: float) -> int:
        print(f"\n결과: 통과 {self.ok} · 실패 {self.fail} · {time.time() - t0:.1f}초")
        return 0 if self.fail == 0 else 1


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.tools.smoke")
    p.add_argument("base_url", nargs="?", default="http://127.0.0.1:8501",
                   help="예: https://devopen-gpu.jntp-ax.or.kr/i/0kMGUuxvQ6gdAE9Q")
    p.add_argument("--insecure", action="store_true", help="HTTPS 인증서 검증 끄기 (사내망 프록시 등)")
    p.add_argument("--ai", action="store_true", help="실제 모델 A API 를 합성 이미지로 1회 호출 (활동 로그 1행 추가)")
    a = p.parse_args()
    sys.exit(Smoke(a.base_url, verify=not a.insecure, ai=a.ai).run())


if __name__ == "__main__":
    main()
