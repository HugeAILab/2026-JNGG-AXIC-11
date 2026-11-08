"""모델 A(영역 분할) 격리 테스트 — 32-Web tests/test_segmentation.py 8개를 31-Web 구조로 옮김.

실제 운영 DB·추론 API 를 건드리지 않는다 (httpx 클라이언트를 가짜로 바꿔 끼움).
    python -m unittest discover -s tests -v
"""
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
# 다른 테스트 모듈과 같은 프로세스에서 돌므로 설정은 test_stage2.py 와 같은 형태로 맞춘다 (경로 접두사 없음)
os.environ.update(DATA_DIR=_temp.name, DB_PATH=str(Path(_temp.name) / "app.db"), LOG_DIR=str(Path(_temp.name) / "logs"),
                  SESSION_HTTPS_ONLY="false", SESSION_SECRET="test", ROOT_PATH="", AI_MODE="mock")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import auth, db  # noqa: E402
from app.integrations.models import aistream_seg as seg  # noqa: E402
from app.main import app  # noqa: E402


def image_bytes(size=(2, 2), fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, "gray").save(output, format=fmt)
    return output.getvalue()


RESULT = {"segmentation": {"instances": [{"class": {"name": "용접불량", "index": 0, "category_id": 1},
          "score": 0.9, "bbox_xyxy": [-0.1, 0, 2.1, 2], "mask_rle": {"size": [2, 2], "counts": "04"}}]}}


class FakeResponse:
    status_code = 200
    headers = {}
    body = RESULT

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass

    async def aiter_bytes(self):
        yield json.dumps(self.body).encode()


class FakeClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def stream(self, *args, **kwargs): return FakeResponse()


class SegmentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_schema()
        with db.connect() as conn:
            for name, role in [("seg-inspector", "inspector"), ("seg-worker", "worker")]:
                if not db.one(conn, "SELECT 1 FROM users WHERE username=?", (name,)):
                    db.insert(conn, "users", {"username": name, "name": name, "role": role,
                                             "password_hash": auth.hash_password("test-password")})

    def setUp(self):
        self.client = TestClient(app, base_url="http://testserver")

    def tearDown(self):
        self.client.close()

    def login(self, username="seg-inspector"):
        response = self.client.get("/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]
        response = self.client.post("/login", data={"username": username, "password": "test-password",
                                                     "csrf_token": token}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        response = self.client.get("/portal/inspections/upload")
        self.assertEqual(response.status_code, 200)
        self.assertIn("upload-form", response.text)  # 3단계: 여러 장 업로드 화면 (단건 프록시 API 는 그대로)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]

    def post(self, token=None, content=None):
        return self.client.post("/portal/api/segmentation",
                                files={"file": ("test.png", image_bytes() if content is None else content, "image/png")},
                                headers={"X-CSRF-Token": token} if token else {})

    def test_auth_and_csrf(self):
        self.assertIn(self.post().status_code, (401, 403))
        self.login()
        self.assertEqual(self.post().status_code, 403)

    def test_worker_cannot_inspect(self):
        self.assertEqual(self.post(self.login("seg-worker")).status_code, 403)

    def test_valid_result_and_untouched_coordinates(self):
        token = self.login()
        with patch.object(seg.httpx, "AsyncClient", return_value=FakeClient()):
            response = self.post(token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"], RESULT)

    def test_invalid_uploads(self):
        token = self.login()
        self.assertEqual(self.post(token, b"not an image").status_code, 422)
        self.assertEqual(self.post(token, image_bytes(fmt="GIF")).status_code, 422)
        with patch.object(seg, "MAX_BYTES", 3):
            self.assertEqual(self.post(token, b"1234").status_code, 413)
        with patch.object(seg, "MAX_PIXELS", 3):
            self.assertEqual(self.post(token).status_code, 413)

    def test_exif_dimensions_and_original_bytes(self):
        image = Image.new("RGB", (2, 3))
        exif = image.getexif()
        exif[274] = 6
        output = io.BytesIO()
        image.save(output, format="JPEG", exif=exif)
        original = output.getvalue()
        self.assertEqual(seg.prepare_image(original), (original, 3, 2))

    def test_queue_full(self):
        token = self.login()
        with patch.object(seg.httpx, "AsyncClient", return_value=FakeClient()), \
                patch.object(FakeResponse, "status_code", 429):
            response = self.post(token)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "1")

    def test_bad_upstream_response(self):
        token = self.login()
        with patch.object(seg.httpx, "AsyncClient", return_value=FakeClient()), \
                patch.object(FakeResponse, "body", {"bad": True}):
            self.assertEqual(self.post(token).status_code, 502)

    def test_mask_size_mismatch(self):
        with self.assertRaises(seg.ModelAError) as error:
            seg.validate_result(RESULT, 3, 2)
        self.assertEqual(error.exception.status, 502)


if __name__ == "__main__":
    unittest.main()
