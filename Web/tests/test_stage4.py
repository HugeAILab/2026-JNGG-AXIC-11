"""4단계 격리 테스트: 대시보드 집계 JSON · 화면 3개 · 관리자 DB 테이블 정보.

    python -m unittest discover -s tests -v
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ.update(DATA_DIR=_tmp.name, DB_PATH=str(Path(_tmp.name) / "app.db"), LOG_DIR=str(Path(_tmp.name) / "logs"),
                  SESSION_HTTPS_ONLY="false", SESSION_SECRET="test", ROOT_PATH="", AI_MODE="mock")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services import stats  # noqa: E402


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.run(force=True)
        from app.main import app
        cls.app = app

    def client_for(self, username):
        c = TestClient(self.app)
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text)[1]
        r = c.post("/login", data={"username": username, "password": seed.DEMO_PASSWORD, "csrf_token": tok},
                   follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        return c

    def test_stats_numbers(self):
        with db.connect() as c:
            s = stats.compute(c, None)
            total = db.scalar(c, "SELECT COUNT(*) FROM inspections")
            conf = db.scalar(c, "SELECT COUNT(*) FROM inspections WHERE final_is_defect IS NOT NULL")
            bad = db.scalar(c, "SELECT COUNT(*) FROM inspections WHERE final_is_defect=1")
        k = s["kpi"]
        self.assertEqual((k["total"], k["confirmed"], k["defects"]), (total, conf, bad))
        self.assertAlmostEqual(k["defect_rate"], round(bad / conf * 100, 1))
        for key in ("reinspect_rate", "lead_days", "agree"):
            self.assertIsNotNone(k[key], key)
        self.assertEqual(sum(x["n"] for x in s["daily"]), total)  # 전체 기간: 일별 합 = 총 건수
        self.assertEqual(sum(x["n"] for x in s["stages"]), bad)    # 불량 확정 건은 모두 처리 단계 중 하나
        self.assertEqual(sum(x["n"] for x in s["types"]), bad)
        self.assertEqual({x["domain"] for x in s["by_domain"]}, {"WELD", "SURFACE", "INSULATION", "CABLE", "BINDING", "CUTTING"})
        self.assertLessEqual(len(s["due"]), 10)
        self.assertLessEqual(len(s["welders"]), 5)
        self.assertTrue(s["heat"]["tanks"] and s["heat"]["blocks"])
        with db.connect() as c:
            s7 = stats.compute(c, 7)
        self.assertEqual(len(s7["daily"]), 7)
        self.assertLessEqual(s7["kpi"]["total"], total)

    def test_pages_and_roles(self):
        c = self.client_for("worker")  # 대시보드는 모든 역할 조회 가능
        for url in ("/portal/dashboard", "/portal/dashboard/defects", "/portal/dashboard/areas?days=7",
                    "/portal/dashboard?days=bad"):
            r = c.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn("dashboard.js", r.text)
            self.assertNotIn("준비 중", r.text)
            self.assertNotIn("DB 테이블 행 수", r.text)   # 관리자 화면으로 옮김
            self.assertNotIn("최근 활동 로그", r.text)    # 대시보드에서 삭제
        j = c.get("/portal/api/stats?days=30").json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["days"], 30)
        self.assertEqual(c.get("/portal/admin/db").status_code, 403)
        a = self.client_for("admin")
        r = a.get("/portal/admin/db")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ai_model_results", r.text)
        self.assertIn("/portal/admin/db", a.get("/portal/admin/users").text)  # 시스템 메뉴에 링크


if __name__ == "__main__":
    unittest.main()
