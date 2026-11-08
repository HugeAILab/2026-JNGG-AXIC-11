"""교육 자료·퀴즈가 포털 로그인과 같은 DB를 쓰는지 검증한다."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_suite_tmp = tempfile.TemporaryDirectory(prefix="qcheck-learning-tests-")
os.environ.update(DATA_DIR=_suite_tmp.name, DB_PATH=str(Path(_suite_tmp.name) / "suite.db"),
                  LOG_DIR=str(Path(_suite_tmp.name) / "logs"),
                  SESSION_HTTPS_ONLY="false", SESSION_SECRET="test", ROOT_PATH="", AI_MODE="mock")

from fastapi.testclient import TestClient  # noqa: E402
from app import config, db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


class LearningFlowTests(unittest.TestCase):
    def test_content_and_account_history(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "app.db"):
            seed.run(force=True)
            with TestClient(app) as client:
                with db.connect() as conn:
                    self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM edu_articles WHERE slug LIKE 'learning-%'"), 13)
                    self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM edu_quizzes WHERE content_key IS NOT NULL"), 46)
                token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text)[1]
                self.assertEqual(client.post("/login", data={"username": "inspector", "password": seed.DEMO_PASSWORD,
                                                              "csrf_token": token}, follow_redirects=False).status_code, 303)
                for path in ("/portal/learning", "/portal/learning/L01", "/portal/learning/quiz",
                             "/portal/learning/quiz/L01", "/static/learning/images/lesson_crack.svg"):
                    self.assertEqual(client.get(path).status_code, 200, path)
                token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/portal/learning/quiz/L01").text)[1]
                self.assertEqual(client.post("/portal/learning/quiz/L01",
                                             data={"choice": "L01", "csrf_token": token}).status_code, 200)
                self.assertIn("L01", client.get("/portal/learning/quiz/history").text)
                with db.connect() as conn:
                    row = db.one(conn, "SELECT user_id, is_correct FROM quiz_attempts ORDER BY id DESC LIMIT 1")
                    self.assertEqual(row["is_correct"], 1)
                    self.assertEqual(row["user_id"], db.one(conn, "SELECT id FROM users WHERE username='inspector'")["id"])
                    self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM activity_logs WHERE log_type='QUIZ' AND action='QUIZ_FINISH'"), 1)
                    self.assertGreater(db.scalar(conn, "SELECT COUNT(*) FROM activity_logs WHERE action='LEARNING_VIEW'"), 0)
