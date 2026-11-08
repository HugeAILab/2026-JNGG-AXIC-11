"""서버 .env 를 플랫폼이 알려 준 외부 주소에 맞춰 설정 (32-Web tools/configure_deployment.py 를 옮겨 일반화).

    python -m app.tools.configure_deployment --port 8502      # bash run.sh configure 8502
    python -m app.tools.configure_deployment --port 8501

- DevStream 은 포트마다 외부 주소를 환경변수 DEVSTREAM_EXTERNAL_URL_<포트> 로 알려 준다.
  있으면: ROOT_PATH = 그 주소의 경로, SESSION_HTTPS_ONLY=true
  없으면: ROOT_PATH 비움, SESSION_HTTPS_ONLY=false (서버 안·SSH 터널의 http 로 확인하는 시험용)
- PORT, AI_MODE=live 도 함께 쓴다. 세션 쿠키 이름은 31-Web 기본값(weldsight_session)을 유지해
  같은 도메인의 32-Web(weldsight32_session)과 로그인이 섞이지 않게 한다.
"""
from __future__ import annotations

import argparse
import os
import re
from urllib.parse import urlsplit

from app import config


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.tools.configure_deployment")
    p.add_argument("--port", type=int, default=config.PORT)
    a = p.parse_args()
    public_url = os.environ.get(f"DEVSTREAM_EXTERNAL_URL_{a.port}", "").strip()
    settings = {
        "PORT": str(a.port),
        "ROOT_PATH": urlsplit(public_url).path.rstrip("/") if public_url else "",
        "SESSION_HTTPS_ONLY": "true" if public_url else "false",
        "AI_MODE": "live",
    }
    path = config.BASE_DIR / ".env"
    if not path.exists():
        raise SystemExit(".env 가 없습니다. 먼저 bash run.sh setup")
    text = path.read_text(encoding="utf-8")
    for name, value in settings.items():
        pattern = rf"(?m)^{name}=.*$"
        text = re.sub(pattern, f"{name}={value}", text) if re.search(pattern, text) else text + f"\n{name}={value}\n"
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    where = public_url or "(외부 주소 없음 — 서버 안 또는 SSH 터널의 http 로 확인)"
    print(f"설정 완료: port={settings['PORT']} root={settings['ROOT_PATH'] or '(없음)'} "
          f"https_only={settings['SESSION_HTTPS_ONLY']} · 외부 주소 {where}")


if __name__ == "__main__":
    main()
