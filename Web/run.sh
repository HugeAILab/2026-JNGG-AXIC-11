#!/usr/bin/env bash
# QCheck 서버 운영 스크립트 (Ubuntu 팀 서버용, TODO.md 12.2)
#
#   ./run.sh setup     가상환경 생성·패키지 설치·.env 준비 (처음 1회, requirements 바뀌면 다시)
#   ./run.sh start     백그라운드 실행 (터미널을 닫아도 유지, DB 가 비어 있으면 샘플 데이터 자동 생성)
#   ./run.sh stop | restart | status
#   ./run.sh logs      로그 보기 (Ctrl+C 로 빠져나옴)
#   ./run.sh deploy    git pull → setup → restart → 스모크 테스트   ← 커밋·푸시 후 서버에서 이것만 실행
#   ./run.sh reseed    서버 멈춤 → 샘플 데이터 초기화(--force) → 시작   ※ 업로드·변경 데이터가 모두 지워짐
#   ./run.sh smoke [URL] [--ai]  스모크 테스트 (기본: 로컬 http://127.0.0.1:PORT/ROOT_PATH, --ai: 실제 모델 A 1회 호출)
#   ./run.sh configure [PORT]    DEVSTREAM_EXTERNAL_URL_<PORT> 로 .env 의 PORT·ROOT_PATH·HTTPS 설정 (32-Web 도구 통합)
set -euo pipefail

cd "$(dirname "$0")"
APP_DIR="$(pwd)"
VENV="$APP_DIR/.venv"
PY="$VENV/bin/python"
PID_FILE="$APP_DIR/data/app.pid"
LOG_FILE="$APP_DIR/data/logs/app.log"
PYTHON_BIN="${PYTHON_BIN:-python3}"

say()  { printf '\033[1;34m[weldsight]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[weldsight]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[weldsight]\033[0m %s\n' "$*" >&2; exit 1; }

need_venv() { [ -x "$PY" ] || die "가상환경이 없습니다. 먼저 ./run.sh setup"; }
cfg() { "$PY" -c "from app import config; print(config.$1)"; }

pid_alive() {
  [ -f "$PID_FILE" ] || return 1
  local pid; pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

cmd_setup() {
  command -v "$PYTHON_BIN" >/dev/null || die "$PYTHON_BIN 이 없습니다 (PYTHON_BIN=python3.11 ./run.sh setup 처럼 지정 가능)"
  "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11 이상이 필요합니다: $("$PYTHON_BIN" --version 2>&1)"
  # 기존 .venv 가 다른 Python 버전으로 만들어졌으면 다시 만든다
  # (버전이 섞이면 pydantic_core 등 바이너리 패키지가 ImportError 를 낸다)
  if [ -x "$PY" ]; then
    local have want_v
    have="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
    want_v="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [ "$have" != "$want_v" ]; then
      warn "가상환경 Python($have)이 $PYTHON_BIN($want_v)과 달라 .venv 를 다시 만듭니다"
      rm -rf "$VENV"
    fi
  fi
  if [ ! -x "$PY" ]; then
    say "가상환경 생성: $VENV ($("$PYTHON_BIN" --version 2>&1))"
    "$PYTHON_BIN" -m venv "$VENV"
  fi
  local stamp="$VENV/.req.sha"
  local want; want="$(sha256sum requirements.txt | cut -d' ' -f1)"
  if [ "$(cat "$stamp" 2>/dev/null || true)" != "$want" ]; then
    say "패키지 설치 (requirements.txt)"
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q -r requirements.txt
    echo "$want" > "$stamp"
  else
    say "패키지 변경 없음"
  fi
  if [ ! -f .env ]; then
    say ".env 생성 (.env.example 복사 + SESSION_SECRET 자동 생성 + SESSION_HTTPS_ONLY=true)"
    cp .env.example .env
    "$PY" - <<'PYEOF'
import re, secrets, pathlib
p = pathlib.Path(".env"); s = p.read_text(encoding="utf-8")
s = re.sub(r"(?m)^SESSION_SECRET=.*$", "SESSION_SECRET=" + secrets.token_urlsafe(48), s)
s = re.sub(r"(?m)^SESSION_HTTPS_ONLY=.*$", "SESSION_HTTPS_ONLY=true", s)
p.write_text(s, encoding="utf-8")
PYEOF
    chmod 600 .env
    warn ".env 의 ROOT_PATH(외부 URL 경로 접두사)와 PORT(안내받은 포트)를 꼭 채우세요."
  fi
  mkdir -p data/logs
  say "준비 완료 · ROOT_PATH='$(cfg ROOT_PATH)' PORT=$(cfg PORT) HTTPS_ONLY=$(cfg SESSION_HTTPS_ONLY)"
}

cmd_start() {
  need_venv
  if pid_alive; then say "이미 실행 중입니다 (pid $(cat "$PID_FILE"))"; return 0; fi
  mkdir -p "$(dirname "$LOG_FILE")"
  say "시작: python -m app  (로그: $LOG_FILE)"
  nohup "$PY" -m app >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  local port; port="$(cfg PORT)"
  for _ in $(seq 1 30); do
    sleep 1
    if ! pid_alive; then tail -n 30 "$LOG_FILE"; die "시작 실패 — 위 로그를 확인하세요"; fi
    if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
      say "실행 중 (pid $(cat "$PID_FILE")) · 로컬 확인: http://127.0.0.1:$port/healthz"
      return 0
    fi
  done
  warn "30초 안에 /healthz 응답이 없습니다. ./run.sh logs 로 확인하세요."
}

cmd_stop() {
  if ! pid_alive; then say "실행 중이 아닙니다"; rm -f "$PID_FILE"; return 0; fi
  local pid; pid="$(cat "$PID_FILE")"
  say "중지: pid $pid"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 15); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then warn "응답이 없어 강제 종료합니다"; kill -9 "$pid" 2>/dev/null || true; fi
  rm -f "$PID_FILE"
}

cmd_status() {
  need_venv
  local port; port="$(cfg PORT)"
  if pid_alive; then say "실행 중 (pid $(cat "$PID_FILE"))"; else say "실행 중이 아님"; fi
  curl -fsS "http://127.0.0.1:$port/healthz" && echo || warn "http://127.0.0.1:$port/healthz 응답 없음"
}

cmd_deploy() {
  say "git pull"
  git pull --ff-only
  cmd_setup
  cmd_stop
  cmd_start
  cmd_smoke
}

cmd_reseed() {
  need_venv
  warn "샘플 데이터를 다시 만듭니다. 현재 DB(업로드·조치·보고서 등)는 모두 지워집니다."
  if [ "${YES:-}" != "1" ]; then
    read -r -p "계속할까요? (yes 입력) " ans; [ "$ans" = "yes" ] || die "취소했습니다"
  fi
  cmd_stop
  "$PY" -m app.seed.seed --force
  cmd_start
}

cmd_smoke() {
  need_venv
  # 기본: 로컬 주소 + 경로 접두사 (세션 쿠키 path 가 ROOT_PATH 라서 접두사 없이 부르면 로그인이 유지되지 않음)
  local url="http://127.0.0.1:$(cfg PORT)$(cfg ROOT_PATH)"
  if [ -n "${1:-}" ] && [ "${1#--}" = "$1" ]; then url="$1"; shift; fi
  "$PY" -m app.tools.smoke "$url" "$@"
}

cmd_configure() {
  need_venv
  "$PY" -m app.tools.configure_deployment --port "${1:-$(cfg PORT)}"
}

case "${1:-}" in
  setup)   cmd_setup ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  logs)    tail -n 100 -f "$LOG_FILE" ;;
  deploy)  cmd_deploy ;;
  reseed)  cmd_reseed ;;
  smoke)   shift; cmd_smoke "$@" ;;
  configure) shift; cmd_configure "$@" ;;
  *) sed -n '2,13p' "$0"; exit 1 ;;
esac
