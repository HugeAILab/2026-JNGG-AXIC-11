"""환경변수 로드. 모든 설정은 여기서만 읽는다 (TODO.md 12.1).

우선순위: 실제 환경변수 > 31-Web/.env > 아래 기본값
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # 31-Web/
APP_DIR = BASE_DIR / "app"

load_dotenv(BASE_DIR / ".env", override=False)

# 사내망 등에서 Python 의 HTTPS 인증서 검증이 실패할 때(CERTIFICATE_VERIFY_FAILED) OS 인증서 저장소를 쓴다.
# 선택 패키지 truststore 가 설치되어 있을 때만 동작하며 서버 환경(requirements.txt)에는 영향이 없다.
#   로컬:  pip install truststore      끄기: .env 에 SSL_SYSTEM_TRUST=false
if os.getenv("SSL_SYSTEM_TRUST", "true").strip().lower() not in ("0", "false", "no", "off"):
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

log = logging.getLogger("weldsight")


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name) or default)
    return p if p.is_absolute() else (BASE_DIR / p)


def _root_path(raw: str) -> str:
    """'/i/abc/' → '/i/abc', '' 또는 '/' → '' (접두사 없음)"""
    raw = (raw or "").strip()
    if raw in ("", "/"):
        return ""
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw.rstrip("/")


ROOT_PATH: str = _root_path(os.getenv("ROOT_PATH", ""))
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8501"))

SESSION_SECRET: str = os.getenv("SESSION_SECRET", "")
SESSION_SECRET_GENERATED = False
if not SESSION_SECRET:
    # 비어 있으면 임시 값. 재시작하면 모두 로그아웃되므로 서버에서는 반드시 .env에 넣는다
    SESSION_SECRET = secrets.token_urlsafe(48)
    SESSION_SECRET_GENERATED = True
SESSION_COOKIE: str = os.getenv("SESSION_COOKIE", "weldsight_session")
SESSION_HTTPS_ONLY: bool = _bool("SESSION_HTTPS_ONLY", True)
SESSION_MAX_AGE: int = int(os.getenv("SESSION_MAX_AGE", str(8 * 3600)))

DATA_DIR: Path = _path("DATA_DIR", "data")
DB_PATH: Path = _path("DB_PATH", "data/app.db")
UPLOAD_DIR: Path = _path("UPLOAD_DIR", str(DATA_DIR / "uploads"))
THUMB_DIR: Path = _path("THUMB_DIR", str(DATA_DIR / "thumbs"))
LOG_DIR: Path = _path("LOG_DIR", str(DATA_DIR / "logs"))
SNAPSHOT_DIR: Path = _path("SNAPSHOT_DIR", str(DATA_DIR / "snapshots"))
RESULT_DIR: Path = _path("RESULT_DIR", str(DATA_DIR / "results"))   # 모델 A 원본 응답(마스크 포함) .json.gz

# 사진 업로드 (TODO.md 6.4): 한 번에 최대 장수. 장당 한도는 모델 A 와 같다(10 MiB, 2천만 픽셀)
UPLOAD_MAX_FILES: int = int(os.getenv("UPLOAD_MAX_FILES", "20"))

SEED_BASE_DATE: str = os.getenv("SEED_BASE_DATE", "").strip()

# ── AI 파이프라인 (TODO.md 8.10, 12.1): 모델 A 선별 → 용접이면 모델 B → RAG → 설명·원인·방지 ──
AI_MODE: str = os.getenv("AI_MODE", "mock").strip().lower()  # live | mock

# 모델 A: AIStream "LNG Tank Quality Inspection" MaskDINO 영역 분할 (32-Web 과 같은 값)
SEGMENTATION_BASE_URL: str = os.getenv(
    "SEGMENTATION_BASE_URL", "https://aiopen-serving.jntp-ax.or.kr/jntp-gpu/SV-d4074093"
).strip().rstrip("/")
SEGMENTATION_TIMEOUT: float = float(os.getenv("SEGMENTATION_TIMEOUT", "180"))

# 모델 B: 용접 상세 분석 API (개발 중 — 비어 있으면 B 단계를 건너뜀)
MODEL_B_URL: str = os.getenv("MODEL_B_URL", "").strip().rstrip("/")
MODEL_B_API_KEY: str = os.getenv("MODEL_B_API_KEY", "").strip()
MODEL_B_TIMEOUT: float = float(os.getenv("MODEL_B_TIMEOUT", "60"))
MODEL_B_THRESHOLD: float = float(os.getenv("MODEL_B_THRESHOLD", "0.5"))
# 실제 B 가 없을 때(AI_MODE=live, MODEL_B_URL 비어 있음) 모의 B 로 화면·흐름을 확인할지. 기본은 건너뜀
MODEL_B_MOCK: bool = _bool("MODEL_B_MOCK", False)
# 모의 모델의 가짜 처리 시간(초). 진행률 화면 확인용, 테스트는 0
MOCK_DELAY: float = float(os.getenv("MOCK_DELAY", "1.0"))

# (선택) 모델 A 와 같은 응답 형식의 개선 모델
IMPROVED_URL: str = os.getenv("IMPROVED_URL", "").strip().rstrip("/")

# AgentStream: RAG · 설명·원인·방지 · 보고서
AGENTSTREAM_BASE_URL: str = os.getenv("AGENTSTREAM_BASE_URL", "").strip().rstrip("/")
AGENTSTREAM_API_KEY: str = os.getenv("AGENTSTREAM_API_KEY", "").strip()
AGENTSTREAM_CHATBOT_ID: str = os.getenv("AGENTSTREAM_CHATBOT_ID", "").strip()
AGENTSTREAM_RAG_CHATBOT_ID: str = os.getenv("AGENTSTREAM_RAG_CHATBOT_ID", "").strip() or AGENTSTREAM_CHATBOT_ID
RAG_TIMEOUT: float = float(os.getenv("RAG_TIMEOUT", "30"))

# 원 페이지 푸터 (TODO.md 14장 #9)
TEAM_NAME: str = os.getenv("TEAM_NAME", "").strip() or "여순서 11"
TEAM_DESC: str = os.getenv("TEAM_DESC", "").strip() or "여수·순천·서울, 11팀"
GITHUB_URL: str = os.getenv("GITHUB_URL", "").strip()

APP_NAME = "QCheck"  # Quality Check
APP_TAGLINE = "LNG 탱크 품질 검사 AI 포털"
APP_VERSION = "0.7.0-reports"


def _git_rev() -> str:
    """배포된 커밋 확인용 짧은 해시 (git 이 없거나 저장소가 아니면 빈 문자열)."""
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BASE_DIR, capture_output=True,
                             text=True, timeout=3)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


GIT_REV: str = os.getenv("GIT_REV", "").strip() or _git_rev()
APP_VERSION_LABEL = f"v{APP_VERSION}" + (f" · {GIT_REV}" if GIT_REV else "")


def ensure_dirs() -> None:
    for d in (DATA_DIR, DB_PATH.parent, UPLOAD_DIR, THUMB_DIR, LOG_DIR, SNAPSHOT_DIR, RESULT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def startup_warnings() -> list[str]:
    w = []
    if SESSION_SECRET_GENERATED:
        w.append("SESSION_SECRET이 비어 있어 임시 값을 사용합니다. 재시작하면 모두 로그아웃됩니다 (.env에 설정하세요).")
    if SESSION_HTTPS_ONLY:
        w.append("SESSION_HTTPS_ONLY=true: http:// 로 접속하면 로그인이 유지되지 않습니다 (로컬 개발은 false).")
    elif ROOT_PATH:
        w.append("ROOT_PATH 가 설정된(외부 URL) 환경인데 SESSION_HTTPS_ONLY=false 입니다. 서버에서는 true 로 두세요.")
    return w
