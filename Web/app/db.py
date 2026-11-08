"""SQLite 연결·스키마·공통 쿼리 헬퍼 (TODO.md 7.2).

규칙
- 연결은 스레드 간에 공유하지 않는다. 요청 하나·작업 한 단계마다 `with connect() as conn:`
- 연결마다 foreign_keys / WAL / busy_timeout 을 켠다
- 시각은 DB 기본값이 아니라 앱이 만든 한국 시간 문자열(now_kst)을 넣는다
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from . import config

# 한국은 서머타임이 없으므로 고정 +09:00 으로 충분하다.
# (zoneinfo 는 Windows 에서 tzdata 패키지가 따로 필요해 쓰지 않는다)
KST = timezone(timedelta(hours=9), "KST")
TS_FMT = "%Y-%m-%d %H:%M:%S"


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def now_str() -> str:
    return now_kst().strftime(TS_FMT)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def raw_connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """with connect() as conn: ... — 정상 종료 시 commit, 예외 시 rollback, 항상 close."""
    conn = raw_connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA_VERSION = 4


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    """스키마 만들기 + 아직 적용하지 않은 변경(마이그레이션) 적용. 앱 시작·시드 때마다 불러도 안전하다.

    버전은 PRAGMA user_version 으로 관리한다 (TODO.md 7.2).
      1 = 1단계 스키마 (schema.sql, 15개 테이블)
      2 = AI 파이프라인 (schema_v2.sql + 칸 추가 + 기준 데이터 + 샘플 AI 결과 채우기, 18개 테이블)
      3 = 사진 업로드 (ai_model_results.image_path: 결과가 어느 사진의 것인지 — 재검사 사진 구분)
    각 단계는 다시 실행해도 결과가 같도록 만들어, 중간에 실패해도 재시작하면 이어서 끝난다.
    """
    if conn is None:
        with connect() as c:
            _init(c)
    else:
        _init(conn)


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript((config.APP_DIR / "schema.sql").read_text(encoding="utf-8"))
    version = scalar(conn, "PRAGMA user_version") or 0
    if version < 1:
        conn.execute("PRAGMA user_version = 1")
        version = 1
    if version < 2:
        _migrate_v2(conn)
        conn.execute("PRAGMA user_version = 2")
        version = 2
    if version < 3:
        _add_column(conn, "ai_model_results", "image_path", "TEXT")
        conn.execute("PRAGMA user_version = 3")
        version = 3
    if version < 4:
        _add_column(conn, "edu_quizzes", "content_key", "TEXT")
        _add_column(conn, "edu_quizzes", "kind", "TEXT NOT NULL DEFAULT 'choice'")
        _add_column(conn, "edu_quizzes", "answer_text", "TEXT")
        _add_column(conn, "quiz_attempts", "response_text", "TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_edu_quiz_content_key ON edu_quizzes(content_key) WHERE content_key IS NOT NULL")
        conn.execute("PRAGMA user_version = 4")
    conn.commit()


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, name: str, decl: str) -> None:
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _migrate_v2(conn: sqlite3.Connection) -> None:
    # ① 칸 추가
    _add_column(conn, "defect_types", "domain",
                "TEXT NOT NULL DEFAULT 'WELD' CHECK (domain IN ('WELD','SURFACE','INSULATION','CABLE','BINDING','CUTTING'))")
    _add_column(conn, "defect_types", "in_b_dict", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "defect_types", "aliases", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "inspections", "work_type", "TEXT")
    _add_column(conn, "inspections", "ai_domain", "TEXT")
    _add_column(conn, "inspections", "ai_source", "TEXT CHECK (ai_source IN ('A','B'))")
    _add_column(conn, "inspections", "ai_findings", "TEXT")
    _add_column(conn, "inspections", "ai_flags", "TEXT")
    _add_column(conn, "rag_chunks", "source", "TEXT NOT NULL DEFAULT 'DOC' CHECK (source IN ('DOC','CASE'))")
    _add_column(conn, "rag_chunks", "inspection_id", "INTEGER REFERENCES inspections(id)")
    _add_column(conn, "rag_chunks", "updated_at", "TEXT")
    # ② 버전 1 ai_analyses(키워드 추출 방식)는 비어 있으면 지우고, 데이터가 있으면 이름을 바꿔 보관
    if "path" not in columns(conn, "ai_analyses"):
        n = scalar(conn, "SELECT COUNT(*) FROM ai_analyses") or 0
        conn.execute("DROP TABLE ai_analyses" if n == 0 else "ALTER TABLE ai_analyses RENAME TO ai_analyses_v1")
        conn.commit()
    # ③ 새 테이블·색인
    conn.executescript((config.APP_DIR / "schema_v2.sql").read_text(encoding="utf-8"))
    # ④ 기준 데이터(결함 사전 확장·A 클래스 대응표·모델 등록부) + 기존 샘플 검사 건의 모의 A·B 결과
    from . import reference
    from .services import sample_ai
    reference.ensure_reference_data(conn, now_str())
    sample_ai.backfill(conn)


def schema_version(conn: sqlite3.Connection) -> int:
    return scalar(conn, "PRAGMA user_version") or 0


def db_exists() -> bool:
    return config.DB_PATH.exists() and config.DB_PATH.stat().st_size > 0


def is_seeded() -> bool:
    """데모 계정이 있으면 True. DB 파일이 깨진 경우 등은 예외를 그대로 올린다
    (False 로 삼키면 자동 시드가 기존 DB 를 지울 수 있으므로)."""
    if not db_exists():
        return False
    with connect() as conn:
        try:
            return one(conn, "SELECT COUNT(*) AS n FROM users")["n"] > 0
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return False
            raise


# ── 쿼리 헬퍼 (모두 ? 바인딩) ─────────────────────────────────

def one(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def all_(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def scalar(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def insert(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> int:
    cols = ", ".join(data.keys())
    qs = ", ".join("?" for _ in data)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(data.values()))
    return int(cur.lastrowid)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [r["name"] for r in all_(
        conn, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {n: scalar(conn, f"SELECT COUNT(*) FROM {n}") for n in names}
