"""보고서 생성·권한·고정 스냅샷·PDF 통합 테스트."""
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


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.run(force=True)
        from app.main import app
        cls.app = app

    def login(self, name):
        c = TestClient(self.app)
        token = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text)[1]
        self.assertEqual(c.post("/login", data={"username": name, "password": seed.DEMO_PASSWORD,
                                                "csrf_token": token}, follow_redirects=False).status_code, 303)
        page = c.get("/portal/inspections")
        csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)[1]
        return c, {"X-CSRF-Token": csrf}

    def test_create_snapshot_and_pdf(self):
        c, headers = self.login("admin")
        with db.connect() as conn:
            row = db.one(conn, "SELECT id FROM inspections WHERE ai_is_defect=1 OR final_is_defect=1 LIMIT 1")
        self.assertIsNotNone(row)
        payload = {"type": "DEFECT", "source": "SELECTION", "inspection_ids": [row["id"]], "title": "통합 시험 보고서"}
        p = c.post("/portal/api/reports/preview", json=payload, headers=headers)
        self.assertEqual(p.status_code, 200, p.text)
        self.assertEqual(p.json()["eligible"], 1)
        r = c.post("/portal/reports", json=payload, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()["report_id"]
        view = c.get(f"/portal/reports/{rid}")
        self.assertEqual(view.status_code, 200, view.text)
        self.assertIn("③-2 불량 설명", view.text)
        pdf = c.get(f"/portal/reports/{rid}.pdf")
        self.assertEqual(pdf.status_code, 200, pdf.text[:100] if pdf.status_code != 200 else "")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("통합 시험 보고서", c.get("/portal/reports").text)
        with db.connect() as conn:
            self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM report_items WHERE report_id=?", (rid,)), 1)
            self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM activity_logs WHERE action='REPORT_CREATE' AND target_id=?", (rid,)), 1)
            self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM activity_logs WHERE action='REPORT_PDF' AND target_id=?", (rid,)), 1)

    def test_worker_cannot_create(self):
        c, headers = self.login("worker")
        self.assertNotIn("report-row", c.get("/portal/inspections").text)
        self.assertEqual(c.post("/portal/reports", json={"title": "x"}, headers=headers).status_code, 403)

    def test_completion_and_upload_scope(self):
        c, headers = self.login("admin")
        with db.connect() as conn:
            done = db.one(conn, "SELECT id FROM inspections WHERE status='PASS' AND rework_count>=1 LIMIT 1")
            upload = db.one(conn, "SELECT upload_log_id FROM inspections WHERE upload_log_id IS NOT NULL LIMIT 1")
        self.assertIsNotNone(done)
        body = {"type": "COMPLETION", "source": "SELECTION", "inspection_ids": [done["id"]], "title": "완료 보고서"}
        r = c.post("/portal/reports", json=body, headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(c.get(f'/portal/reports/{r.json()["report_id"]}.pdf').content.startswith(b"%PDF"))
        if upload:
            upload_body = {"type": "DEFECT", "source": "UPLOAD", "upload_log_id": upload["upload_log_id"],
                           "scope": "ALL", "title": "업로드 전체 보고서"}
            p = c.post("/portal/api/reports/preview", json=upload_body, headers=headers)
            self.assertEqual(p.status_code, 200, p.text)
            self.assertEqual(p.json()["eligible"], p.json()["total"])
            created = c.post("/portal/reports", json=upload_body, headers=headers)
            self.assertEqual(created.status_code, 200, created.text)
            with db.connect() as conn:
                self.assertEqual(db.scalar(conn, "SELECT item_count FROM reports WHERE id=?", (created.json()["report_id"],)), p.json()["total"])


if __name__ == "__main__":
    unittest.main()
