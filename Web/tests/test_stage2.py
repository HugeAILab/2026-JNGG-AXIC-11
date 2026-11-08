"""2단계 격리 테스트: 스키마 버전 2 마이그레이션 · 샘플 AI 파이프라인 결과 · 화면 조회.

    python -m unittest discover -s tests -v

임시 폴더의 DB 만 쓰며 실제 DB·외부 API 를 건드리지 않는다.
"""
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ.update(DATA_DIR=_tmp.name, DB_PATH=str(Path(_tmp.name) / "app.db"), LOG_DIR=str(Path(_tmp.name) / "logs"),
                  SESSION_HTTPS_ONLY="false", SESSION_SECRET="test", ROOT_PATH="", AI_MODE="mock")

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db  # noqa: E402
from app.seed import seed  # noqa: E402

V1_SCHEMA = (config.APP_DIR / "schema.sql").read_text(encoding="utf-8")


def fresh_v1_db(path: Path) -> sqlite3.Connection:
    """1단계와 같은 버전 1 DB (schema.sql 만 적용, user_version 0) + 1단계 시드 형식의 검사 건 2개."""
    if path.exists():
        path.unlink()
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript(V1_SCHEMA)
    c.execute("INSERT INTO users (username,password_hash,name,role,created_at) VALUES ('u','x','검사','inspector','2026-11-01')")
    c.execute("""INSERT INTO defect_types (code,name_ko,name_en,model_label,description,causes,remedies,keywords,severity)
                 VALUES ('POROSITY','기공','Porosity',NULL,'둥근 구멍','습기','재용접','[\"둥근 구멍\"]','MEDIUM'),
                        ('NORMAL','정상','Normal',NULL,'-','-','-','[]','NONE')""")
    for i, (ai_def, ai_type, final, ftype, status) in enumerate([(1, "POROSITY", 1, "POROSITY", "DEFECT"),
                                                                  (0, "NORMAL", 0, None, "PASS")], start=1):
        c.execute("""INSERT INTO inspections (id,code,project,tank_no,block,joint_no,weld_process,is_sample,ai_label,
                     ai_is_defect,ai_defect_type,ai_confidence,ai_status,final_is_defect,defect_type,ai_agree,status,
                     inspected_at) VALUES (?,?,'P','TANK-1','B-01',?, 'GTAW',1,?,?,?,0.9,'DONE',?,?,1,?,'2026-11-02 10:00:00')""",
                  (i, f"INS-T-{i}", f"J-{i}", ai_type, ai_def, ai_type, final, ftype, status))
        c.execute("""INSERT INTO inspection_events (inspection_id,action,from_status,to_status,comment,created_at)
                     VALUES (?,'AI_INSPECT','PENDING','AI_DONE','old','2026-11-02 10:00:01')""", (i,))
    c.commit()
    return c


class MigrationTests(unittest.TestCase):
    def test_v1_to_v2_keeps_data_and_fills_ai(self):
        path = config.DB_PATH
        fresh_v1_db(path).close()
        db.init_schema()
        db.init_schema()  # 두 번 실행해도 같아야 한다
        with db.connect() as c:
            self.assertEqual(db.schema_version(c), 4)  # 4 = 교육 퀴즈의 안정적인 콘텐츠 키와 서술형 답안
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.assertEqual(len(tables), 18)
            self.assertTrue({"ai_models", "a_class_map", "ai_model_results"} <= tables)
            self.assertIn("path", db.columns(c, "ai_analyses"))
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM users"), 1)
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM defect_types"), 19)
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM a_class_map"), 15)
            # 불량 샘플: A + B, 정상 샘플: A 만
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM ai_model_results WHERE inspection_id=1"), 2)
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM ai_model_results WHERE inspection_id=2"), 1)
            r = db.one(c, "SELECT * FROM inspections WHERE id=1")
            self.assertEqual(r["ai_domain"], "WELD")
            self.assertIn(r["ai_source"], ("A", "B"))
            self.assertEqual(db.scalar(c, "SELECT comment FROM inspection_events WHERE inspection_id=1")[:6], "AI 판정:")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_a_class_trigger_rule(self):
        db.init_schema()
        with db.connect() as c:
            trig = {r["a_class"] for r in c.execute("SELECT a_class FROM a_class_map WHERE triggers_b=1")}
        self.assertEqual(trig, {"용접불량", "용접블로우홀", "균열"})


class SeedAndPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.run(force=True)
        from app.main import app
        cls.client = TestClient(app)
        tok = re.search(r'name="csrf_token" value="([^"]+)"', cls.client.get("/login").text)[1]
        r = cls.client.post("/login", data={"username": "inspector", "password": seed.DEMO_PASSWORD, "csrf_token": tok},
                            follow_redirects=False)
        assert r.status_code == 303

    def test_seed_shape(self):
        with db.connect() as c:
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM inspections"), 136)
            domains = {r[0] for r in c.execute("SELECT DISTINCT ai_domain FROM inspections")}
            self.assertEqual(domains, {"WELD", "SURFACE", "INSULATION", "CABLE", "BINDING", "CUTTING"})
            # 모델 B 는 용접 불량에만
            self.assertEqual(db.scalar(c, """SELECT COUNT(*) FROM ai_model_results r JOIN inspections i ON i.id=r.inspection_id
                                             WHERE r.stage='B' AND i.ai_domain<>'WELD'"""), 0)
            # 모든 검사 건에 모델 A 결과
            self.assertEqual(db.scalar(c, """SELECT COUNT(*) FROM inspections i WHERE NOT EXISTS
                                             (SELECT 1 FROM ai_model_results r WHERE r.inspection_id=i.id AND r.stage='A')"""), 0)
            agree = db.scalar(c, "SELECT AVG(ai_agree) FROM inspections WHERE ai_agree IS NOT NULL")
            self.assertGreater(agree, 0.8)
            # 상태 전이 사슬이 끊기지 않음
            self.assertEqual(db.scalar(c, """SELECT COUNT(*) FROM inspection_events a JOIN inspection_events b
                ON a.inspection_id=b.inspection_id AND b.id=a.id+1 WHERE b.from_status<>a.to_status"""), 0)

    def test_pages(self):
        c = self.client
        for url in ("/portal/inspections", "/portal/inspections?domain=SURFACE&review=1&sort=conf",
                    "/portal/defects?status=DEFECT", "/portal/defects?status=CLOSED", "/portal/defects/history",
                    "/portal/inspections/1", "/portal/inspections/130"):
            self.assertEqual(c.get(url).status_code, 200, url)
        r = c.get("/portal/inspections/99999")  # 없는 번호: 메뉴·목록 + 안내 캐릭터, 상태 코드는 404
        self.assertEqual(r.status_code, 404)
        self.assertIn("찾을 수 없어요", r.text)
        self.assertIn("lost-inspector.png", r.text)
        self.assertIn('class="tbl list"', r.text)
        self.assertEqual(c.get("/static/images/ui/lost-inspector.png").status_code, 200)
        self.assertEqual(c.get("/static/images/ui/lost-inspector.svg").status_code, 200)  # 대체 이미지
        self.assertEqual(c.get("/portal/inspections/abc").status_code, 404)
        self.assertIn("upload-form", c.get("/portal/inspections/upload").text)  # 여러 장 업로드 화면 (3단계)
        self.assertIn("업로드 이력", c.get("/portal/inspections/uploads").text)  # 3단계부터 실제 화면
        j = c.get("/portal/api/inspections/1/ai").json()
        self.assertTrue(j["ok"])
        self.assertIsNotNone(j["A"])


if __name__ == "__main__":
    unittest.main()
