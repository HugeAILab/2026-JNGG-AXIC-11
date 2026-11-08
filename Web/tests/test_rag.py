"""문서 근거 검색과 분석 저장의 격리 테스트."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_suite_tmp = tempfile.TemporaryDirectory(prefix="qcheck-rag-tests-")
os.environ.update(DATA_DIR=_suite_tmp.name, DB_PATH=str(Path(_suite_tmp.name) / "suite.db"),
                  LOG_DIR=str(Path(_suite_tmp.name) / "logs"),
                  SESSION_HTTPS_ONLY="false", SESSION_SECRET="test", ROOT_PATH="", AI_MODE="mock")

from app import config, db
from app.seed import seed
from app.services import ai_analysis


class RagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path_patch = patch.object(config, "DB_PATH", Path(self.tmp.name) / "test.db")
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.mode_patch = patch.object(config, "AI_MODE", "mock")
        self.mode_patch.start()
        self.addCleanup(self.mode_patch.stop)
        seed.run(force=True)
        with db.connect() as conn:
            self.inspection_id = conn.execute("SELECT id FROM inspections LIMIT 1").fetchone()[0]
            conn.execute("UPDATE inspections SET ai_status='DONE', ai_is_defect=1, "
                         "ai_domain='WELD', ai_defect_type='POROSITY', status='AI_DONE' WHERE id=?",
                         (self.inspection_id,))

    def test_docs_and_case_are_idempotent(self):
        with db.connect() as conn:
            first = ai_analysis.seed_docs(conn)
            second = ai_analysis.seed_docs(conn)
            self.assertGreater(first, 0)
            self.assertEqual(first, second)
            self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE source='DOC'"), first)
        result = asyncio.run(ai_analysis.run_analysis(self.inspection_id))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertTrue(any(h["source"] == "DOC" for h in result["hits"]))
        with db.connect() as conn:
            row = db.one(conn, "SELECT * FROM ai_analyses WHERE inspection_id=? ORDER BY id DESC LIMIT 1",
                         (self.inspection_id,))
            self.assertEqual((row["path"], row["rag_source"], row["explain_source"]),
                             ("WELD_B_RAG", "LOCAL", "TEMPLATE"))
            self.assertIn("[1]", row["description"])
            self.assertTrue(json.loads(row["causes_json"]))
            self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE source='CASE' AND inspection_id=?",
                                       (self.inspection_id,)), 1)
            self.assertEqual(db.one(conn, "SELECT status FROM inspections WHERE id=?",
                                    (self.inspection_id,))["status"], "AI_DONE")
        asyncio.run(ai_analysis.run_analysis(self.inspection_id, "MANUAL"))
        with db.connect() as conn:
            self.assertEqual(db.scalar(conn, "SELECT COUNT(*) FROM rag_chunks WHERE source='CASE' AND inspection_id=?",
                                       (self.inspection_id,)), 1)

    def test_external_answer_requires_grounded_json(self):
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            ai_analysis._parse_external("기공입니다.", 1)
        with self.assertRaises(ValueError):
            ai_analysis._parse_external(json.dumps({"description": "근거 없는 설명", "causes": [],
                                                    "prevention": [], "checks": []}), 1)
        answer = ai_analysis._parse_external(json.dumps({"description": "기공 후보입니다[1]",
                                                        "causes": ["보호 가스 점검[1]"],
                                                        "prevention": ["조건 확인[1]"],
                                                        "checks": ["원본 확인[1]"]}), 1)
        self.assertEqual(answer["checks"], ["원본 확인[1]"])
        listed = ai_analysis._parse_external(json.dumps({"description": ["기공 후보입니다[1]"],
                                                       "causes": [], "prevention": [], "checks": []}), 1)
        self.assertEqual(listed["description"], "기공 후보입니다[1]")
