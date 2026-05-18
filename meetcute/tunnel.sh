#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# meetcute tunnel.sh — cloudflared 터널 (quick 또는 named) + URL 자동 노출
#
# 작동 결과로 .public_url 파일에 현재 외부 접속 URL 을 씁니다.
# 앱의 url_watcher 가 이 파일을 모니터링해서 변경 시 모든 admin 에게
# 텔레그램으로 새 URL 을 자동 알림합니다.
#
# 모드:
#   - named  : .env 에 MEETCUTE_TUNNEL_NAME + MEETCUTE_TUNNEL_HOSTNAME 둘 다 있으면
#   - quick  : 둘 중 하나라도 없으면. 랜덤 URL 을 stdout 파싱해서 .public_url 에 기록.
#
# 사전 준비 (영구 URL 쓰려면, 한 번만):
#   1) brew install cloudflared
#   2) cloudflared tunnel login          # 브라우저로 cloudflare 계정 인증
#   3) cloudflare 에 도메인 1개 (계정에 추가, 무료. 도메인 자체는 구매 필요)
#   4) .env 에 MEETCUTE_TUNNEL_NAME / MEETCUTE_TUNNEL_HOSTNAME 박기
#   5) ./tunnel.sh 실행 → 자동으로 tunnel 생성 + DNS 라우팅 + 실행
# ──────────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")"

# .env 자동 로드
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

PORT="${PORT:-8765}"
TUNNEL_NAME="${MEETCUTE_TUNNEL_NAME:-}"
HOSTNAME="${MEETCUTE_TUNNEL_HOSTNAME:-}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "❌ cloudflared 미설치. 먼저: brew install cloudflared"
  exit 1
fi

LOG_FILE="$(mktemp -t meetcute-tunnel.XXXXXX.log)"
URL_FILE=".public_url"
trap "rm -f \"$LOG_FILE\" \"$URL_FILE\"" EXIT

# ── Quick mode ─────────────────────────────────────────────────
if [ -z "$TUNNEL_NAME" ] || [ -z "$HOSTNAME" ]; then
  echo "⚠️ MEETCUTE_TUNNEL_NAME / MEETCUTE_TUNNEL_HOSTNAME 없음 → quick 모드 (랜덤 URL)"
  echo ""

  # 백그라운드로 띄우고 stdout 을 로그로
  cloudflared tunnel --url "http://localhost:${PORT}" 2>&1 | tee "$LOG_FILE" &
  CFD_PID=$!

  # URL 파싱 (최대 30초 대기)
  PUBLIC_URL=""
  for i in $(seq 1 30); do
    sleep 1
    PUBLIC_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | head -1 || true)
    if [ -n "$PUBLIC_URL" ]; then
      echo "$PUBLIC_URL" > "$URL_FILE"
      echo ""
      echo "🌐 Public URL: $PUBLIC_URL"
      echo "   (.public_url 파일에 저장됨 → 앱이 자동 감지해 admin 들에게 텔레그램 알림)"
      echo ""
      break
    fi
  done

  if [ -z "$PUBLIC_URL" ]; then
    echo "⚠️ 30초 안에 URL 못 찾았어요. cloudflared 출력을 확인하세요."
  fi

  # 메인 프로세스를 cloudflared 로 (Ctrl+C 시 정상 종료)
  wait $CFD_PID
  exit $?
fi

# ── Named mode ─────────────────────────────────────────────────
echo "🌐 Named tunnel 모드: $TUNNEL_NAME → $HOSTNAME"

if ! cloudflared tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$TUNNEL_NAME"; then
  echo "🆕 새 tunnel '$TUNNEL_NAME' 생성 중..."
  cloudflared tunnel create "$TUNNEL_NAME" || {
    echo "❌ tunnel 생성 실패. 먼저 'cloudflared tunnel login' 하셨는지 확인."
    exit 1
  }
fi

echo "🔗 DNS 라우팅: $HOSTNAME → $TUNNEL_NAME"
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME" 2>/dev/null || true

# Named 모드는 URL 이 고정이라 바로 기록
echo "https://$HOSTNAME" > "$URL_FILE"
echo "🌐 Public URL: https://$HOSTNAME"
echo ""

exec cloudflared tunnel run --url "http://localhost:${PORT}" "$TUNNEL_NAME"
