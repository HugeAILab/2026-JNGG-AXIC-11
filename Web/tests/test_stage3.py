"""3단계 격리 테스트: 여러 장 업로드 → 저장 → AI 판정(모의 A·B, 백그라운드) → 확정 → 조치 → 재검사 한 바퀴.

    python -m unittest discover -s tests -v

임시 폴더의 DB·파일만 쓰며 실제 DB·외부 API 를 건드리지 않는다 (AI_MODE=mock, 가짜 처리 시간 0).
"""
import gzip
import io
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ.update(DATA_DIR=_tmp.name, DB_PATH=str(Path(_tmp.name) / "app.db"), LOG_DIR=str(Path(_tmp.name) / "logs"),
                  SESSION_HTTPS_ONLY="false", SESSION_SECRET="test", ROOT_PATH="", AI_MODE="mock")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import config, db  # noqa: E402
from app.integrations.models import coco_rle, weld_api  # noqa: E402
from app.integrations.models.base import BResult, ModelError  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services import ai_pipeline, jobs  # noqa: E402

# 같은 프로세스의 다른 테스트 모듈이 먼저 설정을 읽었을 수 있으므로 파일 폴더는 직접 임시 폴더로 바꾼다
_files = Path(tempfile.mkdtemp(prefix="qcheck-files-"))
config.UPLOAD_DIR, config.THUMB_DIR, config.RESULT_DIR = _files / "uploads", _files / "thumbs", _files / "results"
config.MOCK_DELAY = 0
config.AI_MODE = "mock"


def jpeg(color="gray", size=(64, 48)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue()


CLASS_MAP = {  # a_class_map 기본값과 같은 모양
    "용접블로우홀": {"domain": "WELD", "is_defect": 1, "maps_to": "POROSITY", "threshold": 0.5, "triggers_b": 1},
    "용접불량": {"domain": "WELD", "is_defect": 1, "maps_to": "WELD_DEFECT", "threshold": 0.5, "triggers_b": 1},
    "용접양품": {"domain": "WELD", "is_defect": 0, "maps_to": "NORMAL", "threshold": 0.5, "triggers_b": 0},
    "도막떨어짐": {"domain": "SURFACE", "is_defect": 1, "maps_to": "COATING_PEEL", "threshold": 0.5, "triggers_b": 0},
    "스크래치": {"domain": "SURFACE", "is_defect": 1, "maps_to": "SCRATCH", "threshold": 0.7, "triggers_b": 0},
}
SEV = {"POROSITY": "MEDIUM", "CRACK": "CRITICAL", "UNDERCUT": "MEDIUM"}


def seg(*items, w=100, h=80):
    inst = []
    for name, score, box in items:
        x0, y0, x1, y1 = box
        inst.append({"class": {"name": name}, "score": score, "bbox_xyxy": list(box),
                     "mask_rle": coco_rle.box_mask(h, w, x0, y0, x1, y1)})
    return {"segmentation": {"instances": inst}}


class RleAndPipelineRules(unittest.TestCase):
    def test_stage_does_not_claim_failure_before_db_commits(self):
        inspection_id = -28028
        jobs.STAGES[inspection_id] = "ERROR"
        try:
            self.assertEqual(jobs.stage_of(inspection_id, "RUNNING"), "A_RUN")
            self.assertEqual(jobs.stage_of(inspection_id, "ERROR"), "ERROR")
            jobs.STAGES[inspection_id] = "B_RUN"
            self.assertEqual(jobs.stage_of(inspection_id, "DONE"), "DONE")
        finally:
            jobs.STAGES.pop(inspection_id, None)

    def test_rle_roundtrip_and_area(self):
        m = coco_rle.box_mask(80, 100, 10, 20, 30, 50)
        self.assertEqual(coco_rle.mask_area(m["counts"], 80, 100), 20 * 30)
        runs = [5, 3, 200, 7, 1, 7784]
        self.assertEqual(coco_rle.decode_runs(coco_rle.encode_runs(runs), sum(runs)), runs)

    def test_weld_goes_to_b_and_b_decides(self):
        a = ai_pipeline.summarize_a(seg(("도막떨어짐", 0.9, (0, 0, 20, 20)), ("용접블로우홀", 0.7, (50, 40, 70, 60))),
                                    100, 80, CLASS_MAP)
        self.assertTrue(a["triggers_b"])
        self.assertEqual(a["top"]["class"], "용접블로우홀")  # 용접이 섞이면 대표는 용접
        self.assertEqual(a["findings"][0]["class"], "도막떨어짐")
        self.assertAlmostEqual(a["top"]["area_ratio"], 400 / 8000)
        b = BResult(scores={"POROSITY": 0.86, "SPATTER": 0.55, "NORMAL": 0.05})
        d = ai_pipeline.decide(a, b, b_ran=True, b_threshold=0.5, severity=SEV)
        self.assertEqual((d["ai_defect_type"], d["ai_source"], d["ai_domain"]), ("POROSITY", "B", "WELD"))
        self.assertIn({"from": "B", "code": "SPATTER", "score": 0.55}, d["findings"])

    def test_non_weld_skips_b(self):
        a = ai_pipeline.summarize_a(seg(("도막떨어짐", 0.8, (0, 0, 20, 20))), 100, 80, CLASS_MAP)
        self.assertFalse(a["triggers_b"])
        d = ai_pipeline.decide(a, None, b_ran=False, b_threshold=0.5, severity=SEV)
        self.assertEqual((d["ai_defect_type"], d["ai_source"], d["ai_domain"], d["flags"]), ("COATING_PEEL", "A", "SURFACE", []))

    def test_no_detection_is_not_good(self):
        a = ai_pipeline.summarize_a(seg(("스크래치", 0.65, (0, 0, 9, 9)), ("이상한분류", 0.99, (0, 0, 5, 5))),
                                    100, 80, CLASS_MAP, "SURFACE")  # 스크래치는 기준 0.7, 대응표에 없는 분류는 무시
        d = ai_pipeline.decide(a, None, b_ran=False, b_threshold=0.5, severity=SEV)
        self.assertEqual((d["ai_is_defect"], d["ai_defect_type"]), (0, None))
        self.assertEqual(d["flags"], ["NO_DETECTION"])
        self.assertEqual(a["class_summary"]["이상한분류"]["count"], 1)  # 요약에는 남김

    def test_b_normal_and_b_missing(self):
        a = ai_pipeline.summarize_a(seg(("용접불량", 0.55, (0, 0, 20, 20))), 100, 80, CLASS_MAP)
        d = ai_pipeline.decide(a, BResult(scores={"NORMAL": 0.8, "POROSITY": 0.2}), b_ran=True, b_threshold=0.5,
                               severity=SEV)
        self.assertEqual((d["ai_defect_type"], d["ai_source"]), ("WELD_DEFECT", "A"))
        self.assertEqual(d["flags"], ["AB_DIFFER", "LOW_SCORE"])
        d = ai_pipeline.decide(a, None, b_ran=True, b_threshold=0.5, severity=SEV)
        self.assertIn("B_UNAVAILABLE", d["flags"])

    def test_duplicate_boxes_and_good_class(self):
        a = ai_pipeline.summarize_a(seg(("용접양품", 0.9, (0, 0, 50, 50)), ("용접불량", 0.6, (1, 1, 50, 50))),
                                    100, 80, CLASS_MAP)  # IoU ≥ 0.7 → 점수 높은 양품만 남음
        self.assertEqual(len(a["candidates"]), 1)
        d = ai_pipeline.decide(a, None, b_ran=False, b_threshold=0.5, severity=SEV)
        self.assertEqual((d["ai_is_defect"], d["ai_defect_type"]), (0, "NORMAL"))

    def test_b_contract_normalization(self):
        r = weld_api.to_result({"scores": {"기공": 1.4, "undercut": 0.3, "모르는코드": 0.9}, "keywords": ["a"],
                                "image_quality": "BLUR"}, 0.1)
        self.assertEqual((r.scores["POROSITY"], r.scores["UNDERCUT"], r.scores["CRACK"]), (1.0, 0.3, 0.0))
        self.assertEqual(len(r.scores), 10)
        self.assertEqual(r.image_quality, "BLUR")
        with self.assertRaises(ModelError):
            weld_api.to_result({"scores": {}}, 0)


class UploadFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.run(force=True)
        from app.main import app
        cls.app = app

    def setUp(self):
        self.client = TestClient(self.app).__enter__()  # with 블록과 같음: 백그라운드 작업이 돌 이벤트 루프 유지

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def login(self, username):
        c = self.client
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").text)[1]
        r = c.post("/login", data={"username": username, "password": seed.DEMO_PASSWORD, "csrf_token": tok},
                   follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        return re.search(r'name="csrf-token" content="([^"]+)"', c.get("/portal/inspections/upload").text)[1]

    def upload(self, token, files, **common):
        data = {"work_type": "WELD", "tank_no": "TANK-2", "block": "B-07", "weld_process": "GTAW", **common}
        return self.client.post("/portal/inspections/upload", headers={"X-CSRF-Token": token, "Accept": "application/json"},
                                data={**data, "joints": [f"J-{i}" for i in range(len(files))]},
                                files=[("files", (n, b, "image/jpeg")) for n, b in files])

    def wait(self, log_id, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            s = self.client.get(f"/portal/inspections/uploads/{log_id}/status").json()
            if s["finished"]:
                return s
            time.sleep(0.05)
        self.fail("AI 판정이 끝나지 않음")

    def test_upload_store_and_judge(self):
        tok = self.login("inspector")
        photos = [(f"p{i}.jpg", jpeg(c)) for i, c in enumerate(["gray", "white", "black", "red", "blue"])]
        r = self.upload(tok, photos)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        log_id, ids = body["log_id"], body["inspection_ids"]
        self.assertEqual(len(ids), 5)
        s = self.wait(log_id)
        self.assertEqual((s["total"], s["done"]), (5, 5))
        with db.connect() as c:
            log = db.one(c, "SELECT * FROM activity_logs WHERE id=?", (log_id,))
            self.assertEqual((log["log_type"], json.loads(log["detail_json"])["photo_count"]), ("UPLOAD", 5))
            rows = [dict(x) for x in c.execute("SELECT * FROM inspections WHERE upload_log_id=? ORDER BY id", (log_id,))]
            self.assertEqual([x["joint_no"] for x in rows], [f"J-{i}" for i in range(5)])
            for it in rows:
                self.assertEqual(it["ai_status"], "DONE")
                self.assertEqual(it["status"], "AI_DONE")
                self.assertEqual(it["model_key"].split("+")[0], "mock_a")
                self.assertTrue((config.UPLOAD_DIR / it["image_path"]).is_file())
                self.assertTrue((config.THUMB_DIR / it["image_path"]).with_suffix(".jpg").is_file())
                a = db.one(c, "SELECT * FROM ai_model_results WHERE inspection_id=? AND stage='A'", (it["id"],))
                self.assertEqual((a["status"], a["trigger"], a["image_path"]), ("OK", "AUTO", it["image_path"]))
                raw = json.loads(gzip.decompress((config.RESULT_DIR / a["raw_path"]).read_bytes()))
                self.assertIn("instances", raw["segmentation"])
                has_b = db.scalar(c, "SELECT COUNT(*) FROM ai_model_results WHERE inspection_id=? AND stage='B'", (it["id"],))
                self.assertEqual(bool(has_b), bool(db.scalar(c, "SELECT triggers_b FROM a_class_map WHERE a_class=?",
                                                               (a["top_class"],))) and bool(a["is_defect"]))
                self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM inspection_events WHERE inspection_id=? AND action='AI_INSPECT'",
                                           (it["id"],)), 1)
        # 사진·썸네일·마스크 결과: 로그인 필요
        item = s["items"][0]
        self.assertEqual(self.client.get(item["thumb"]).status_code, 200)
        self.assertEqual(self.client.get(item["image"]).headers["content-type"], "image/jpeg")
        j = self.client.get(f"/portal/api/inspections/{item['id']}/a-result").json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["image"], {"width": 64, "height": 48})
        for url in ("/portal/inspections/uploads", f"/portal/inspections/uploads/{log_id}", f"/portal/inspections/{item['id']}"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        detail_html = self.client.get(f"/portal/inspections/{item['id']}").text
        self.assertIn('id="open-predict"', detail_html)
        self.assertIn('id="predict-dialog"', detail_html)
        self.assertIn('class="model-a-classes"', detail_html)
        self.assertNotIn(f'/portal/inspections/{item["id"]}/predict"', detail_html)
        with db.connect() as c:
            before = db.scalar(c, "SELECT COUNT(*) FROM ai_model_results WHERE inspection_id=?", (item["id"],))
        self.assertEqual(self.client.post(f"/portal/inspections/{item['id']}/predict",
                                          headers={"X-CSRF-Token": tok, "Accept": "application/json"}).status_code, 409)
        with db.connect() as c:
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM ai_model_results WHERE inspection_id=?", (item["id"],)), before)
        self.assertEqual(self.client.get("/portal/files/../app.db").status_code, 404)
        anon = TestClient(self.app)
        self.assertEqual(anon.get(item["image"], headers={"Accept": "application/json"}).status_code, 401)
        # 일괄 확정: 불량 → DEFECT, 정상 → PASS, 검출 없음 → 건너뜀
        r = self.client.post(f"/portal/inspections/uploads/{log_id}/confirm-all", headers={"X-CSRF-Token": tok})
        out = r.json()
        self.assertEqual(out["defect"] + out["ok"] + out["skipped"], 5)
        with db.connect() as c:
            st = {x["status"] for x in c.execute("SELECT status FROM inspections WHERE upload_log_id=? AND ai_defect_type IS NOT NULL", (log_id,))}
            self.assertTrue(st <= {"DEFECT", "PASS"})

    def test_bad_uploads_and_roles(self):
        tok = self.login("inspector")
        r = self.upload(tok, [("ok.jpg", jpeg()), ("bad.jpg", b"not an image")])
        self.assertEqual(r.status_code, 422)
        self.assertIn("bad.jpg", r.json()["error"])
        r = self.upload(tok, [("ok.jpg", jpeg())], block="")
        self.assertEqual(r.status_code, 422)
        with db.connect() as c:  # 실패한 요청은 로그·검사 건을 남기지 않는다
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM inspections WHERE code LIKE 'INS-%' AND image_path IS NOT NULL "
                                          "AND upload_log_id NOT IN (SELECT id FROM activity_logs)"), 0)
        self.assertEqual(self.client.post("/portal/inspections/upload", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))],
                                          headers={"Accept": "application/json"}).status_code, 403)  # CSRF 없음
        self.assertEqual(self.client.get("/portal/admin/logs").status_code, 403)
        tok = self.login("worker")
        self.assertEqual(self.upload(tok, [("a.jpg", jpeg())]).status_code, 403)

    def test_full_defect_cycle(self):
        tok = self.login("inspector")
        r = self.upload(tok, [("weld.jpg", jpeg("navy"))])
        log_id, (iid,) = r.json()["log_id"], r.json()["inspection_ids"]
        self.wait(log_id)
        h = {"X-CSRF-Token": tok, "Accept": "application/json"}
        post = lambda name, data=None, **kw: self.client.post(f"/portal/inspections/{iid}/{name}", headers=h,  # noqa: E731
                                                              data=data or {}, **kw)
        self.assertEqual(post("reinspect", {"decision": "pass"}).status_code, 409)  # AI_DONE 에서는 재검사 불가
        self.assertEqual(post("confirm", {"decision": "defect", "defect_type": "POROSITY"}).status_code, 422)  # 소견 필수
        r = post("confirm", {"decision": "defect", "defect_type": "POROSITY", "finding": "기공 군집"})
        self.assertEqual(r.json()["status"], "DEFECT", r.text)
        self.assertEqual(post("confirm", {"decision": "ok"}).status_code, 409)  # 두 번 확정 불가
        self.assertEqual(post("rework-plan", {"cause": "습기", "action_plan": "재용접", "assignee": "박용접",
                                              "due_date": "2026-11-10"}).status_code, 403)  # 검사자는 조치 등록 불가
        tok_w = self.login("worker")
        hw = {"X-CSRF-Token": tok_w, "Accept": "application/json"}
        r = self.client.post(f"/portal/inspections/{iid}/rework-plan", headers=hw,
                             data={"cause": "습기", "action_plan": "재용접", "assignee": "박용접", "due_date": "2026-11-10"})
        self.assertEqual(r.json()["status"], "REWORK", r.text)
        r = self.client.post(f"/portal/inspections/{iid}/rework-done", headers=hw, data={"action_result": "그라인딩 후 재용접"},
                             files=[("photo", ("after.jpg", jpeg("green"), "image/jpeg"))])
        self.assertEqual(r.json()["status"], "REINSPECT", r.text)
        tok = self.login("inspector")
        h["X-CSRF-Token"] = tok
        r = self.client.post(f"/portal/inspections/{iid}/reinspect/photo", headers=h,
                             files=[("photo", ("re.jpg", jpeg("yellow"), "image/jpeg"))])
        self.assertTrue(r.json()["ok"], r.text)
        end = time.time() + 20
        while time.time() < end:
            if self.client.get(f"/portal/api/inspections/{iid}/ai").json()["final"]["ai_status"] == "DONE":
                break
            time.sleep(0.05)
        r = post("reinspect", {"decision": "pass", "finding": "이상 없음"})
        self.assertEqual(r.json()["status"], "PASS", r.text)
        with db.connect() as c:
            it = db.one(c, "SELECT * FROM inspections WHERE id=?", (iid,))
            self.assertEqual((it["rework_count"], it["final_is_defect"], it["defect_type"]), (1, 1, "POROSITY"))
            self.assertIsNotNone(it["closed_at"])
            acts = [r[0] for r in c.execute("SELECT action FROM inspection_events WHERE inspection_id=? ORDER BY id", (iid,))]
            self.assertEqual(acts, ["AI_INSPECT", "CONFIRM_DEFECT", "REWORK_PLAN", "REWORK_DONE", "AI_INSPECT", "REINSPECT_OK"])
            ok_ev = db.one(c, "SELECT * FROM inspection_events WHERE inspection_id=? AND action='REINSPECT_OK'", (iid,))
            self.assertIn('"trigger": "REINSPECT"', ok_ev["ai_snapshot"])
            self.assertIsNotNone(ok_ev["image_path"])
            self.assertEqual(db.scalar(c, "SELECT COUNT(*) FROM ai_model_results WHERE inspection_id=? AND trigger='REINSPECT'"
                                          " AND stage='A'", (iid,)), 1)
            # 전이 사슬이 끊기지 않음
            self.assertEqual(db.scalar(c, """SELECT COUNT(*) FROM inspection_events a JOIN inspection_events b
                ON a.inspection_id=b.inspection_id AND b.id=(SELECT MIN(id) FROM inspection_events WHERE inspection_id=a.inspection_id AND id>a.id)
                WHERE a.inspection_id=? AND b.from_status<>a.to_status""", (iid,)), 0)
        self.assertEqual(self.client.get(f"/portal/inspections/{iid}").status_code, 200)

    def test_admin_pages(self):
        self.login("admin")
        for url in ("/portal/admin/logs", "/portal/admin/logs?type=UPLOAD&level=INFO&days=7&q=%EC%82%AC%EC%A7%84",
                    "/portal/admin/health"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotIn("준비 중", r.text)
        self.assertIn("RAG · 불량 설명 근거 검색", self.client.get("/portal/admin/health").text)
        self.assertEqual(self.client.get("/portal/admin/models").status_code, 404)


if __name__ == "__main__":
    unittest.main()
