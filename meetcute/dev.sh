#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# meetcute dev.sh
#
# 한 번 실행해두면 알아서:
#   1) uvicorn --reload 로 서버 띄움
#   2) 백그라운드에서 git fetch 폴링
#   3) 새 커밋 들어오면 git pull
#       - 코드만 바뀌면 → uvicorn이 자동 리로드
#       - pyproject.toml 도 바뀌면 → pip install -e . 후 uvicorn 강제 재시작
#   4) uvicorn이 어떤 이유로 죽으면 자동 재기동
#
# 사용:
#   ./dev.sh                       # 기본 (127.0.0.1:8765, 15초 폴링)
#   PORT=8001 ./dev.sh             # 다른 포트
#   HOST=0.0.0.0 ./dev.sh          # 같은 와이파이의 다른 기기에서 접근 허용
#   POLL_INTERVAL=5 ./dev.sh       # 5초마다 깃 확인
#   MEETCUTE_AUTH=on ./dev.sh      # 인증 켜기
# ──────────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")"

POLL_INTERVAL="${POLL_INTERVAL:-15}"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"

# venv 우선
if [ -x ".venv/bin/uvicorn" ]; then
  UVICORN=.venv/bin/uvicorn
  PIP=.venv/bin/pip
elif command -v uvicorn >/dev/null 2>&1; then
  UVICORN=uvicorn
  PIP=pip
else
  echo "❌ uvicorn 을 찾을 수 없습니다. 가상환경을 먼저 만들고 의존성을 설치하세요:"
  echo "    python3 -m venv .venv && source .venv/bin/activate && pip install -e ."
  exit 1
fi

ts() { date '+%H:%M:%S'; }

start_server() {
  $UVICORN app.main:app --reload --host "$HOST" --port "$PORT" &
  UVICORN_PID=$!
  echo "[$(ts)] 🚀 uvicorn 시작 (pid=$UVICORN_PID, http://$HOST:$PORT)"
}

stop_server() {
  if [ -n "${UVICORN_PID:-}" ] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
}

cleanup() {
  echo ""
  echo "[$(ts)] 👋 종료합니다."
  stop_server
  exit 0
}
trap cleanup INT TERM

hash_pyproject() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum pyproject.toml 2>/dev/null | awk '{print $1}'
  else
    shasum -a 256 pyproject.toml 2>/dev/null | awk '{print $1}'   # macOS
  fi
}

LAST_PYPROJECT=$(hash_pyproject)

# 시작 시 한 번 의존성 동기화 — 사용자가 dev.sh 시작 전에 git pull 했어도
# 새로 추가된 라이브러리가 알아서 들어오도록.
echo "[$(ts)] 📦 의존성 동기화 (pip install -e .)..."
if $PIP install -e . --quiet; then
  echo "[$(ts)] ✅ 의존성 OK"
else
  echo "[$(ts)] ⚠️ pip install 실패 — 그래도 일단 서버는 띄워봅니다."
fi

start_server

echo "[$(ts)] 👀 ${POLL_INTERVAL}초마다 깃 변경 확인합니다. (Ctrl+C 로 종료)"

while true; do
  sleep "$POLL_INTERVAL"

  # uvicorn이 죽었으면 살림
  if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
    echo "[$(ts)] ⚠️ uvicorn이 죽었습니다. 재시작..."
    start_server
  fi

  # 깃 변경 확인 (네트워크 일시 장애 등은 무시)
  git fetch --quiet 2>/dev/null || continue

  LOCAL=$(git rev-parse HEAD)
  REMOTE=$(git rev-parse '@{u}' 2>/dev/null || echo "$LOCAL")
  [ "$LOCAL" = "$REMOTE" ] && continue

  echo "[$(ts)] 🔄 새 커밋 감지 (${LOCAL:0:7} → ${REMOTE:0:7}). pull 받는 중..."
  if ! git pull --quiet --ff-only 2>/dev/null; then
    echo "[$(ts)] ⚠️ pull 실패 (로컬 커밋이 있거나 충돌). 수동으로 처리해주세요."
    continue
  fi

  NEW_PYPROJECT=$(hash_pyproject)
  if [ "$NEW_PYPROJECT" != "$LAST_PYPROJECT" ]; then
    echo "[$(ts)] 📦 pyproject.toml 변경 감지 → 의존성 재설치"
    if $PIP install -e . --quiet; then
      LAST_PYPROJECT=$NEW_PYPROJECT
      echo "[$(ts)] 🔁 새 의존성 적용을 위해 uvicorn 재시작"
      stop_server
      start_server
    else
      echo "[$(ts)] ⚠️ pip install 실패. 서버는 기존 상태 유지."
    fi
  else
    echo "[$(ts)] ✨ 코드만 변경됨 → uvicorn이 알아서 리로드"
  fi
done
