"""실행 진입점 (Windows·Linux 공통): python -m app [--reload] [--port 8501]

- .env 를 읽고, 데모 계정이 없으면(첫 실행) 샘플 데이터를 자동으로 만든다
- 프로세스는 1개(--workers 1 고정): 메모리 상태(로그인 실패 카운터, 이후 BM25·AI 작업)를 쓰기 때문
- 프록시 뒤에서 실제 접속 IP·https 를 알 수 있도록 proxy_headers 를 켠다
"""
from __future__ import annotations

import argparse
import logging

import uvicorn

from . import config, db


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app", description="QCheck 웹 서버 실행")
    p.add_argument("--host", default=config.HOST)
    p.add_argument("--port", type=int, default=config.PORT)
    p.add_argument("--reload", action="store_true", help="개발용: 코드가 바뀌면 자동 재시작")
    p.add_argument("--no-seed", action="store_true", help="DB가 비어 있어도 샘플 데이터를 만들지 않음")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.ensure_dirs()
    db.init_schema()
    if not args.no_seed and not db.is_seeded():
        from .seed import seed
        logging.getLogger("weldsight").warning("데모 계정이 없어 샘플 데이터를 생성합니다 (첫 실행).")
        seed.run()  # 데이터가 있으면 지우지 않고 멈춘다

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )


if __name__ == "__main__":
    main()
