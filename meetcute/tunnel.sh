#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# meetcute tunnel.sh — cloudflared 터널 (quick 또는 named)
#
# .env 에 다음을 설정하면 영구 URL 모드:
#   MEETCUTE_TUNNEL_NAME=meetcute
#   MEETCUTE_TUNNEL_HOSTNAME=meetcute.yourdomain.com
#
# 설정 안 하면 quick 모드 (재시작마다 랜덤 URL).
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

# Quick mode — 랜덤 URL
if [ -z "$TUNNEL_NAME" ] || [ -z "$HOSTNAME" ]; then
  echo "⚠️ .env 에 MEETCUTE_TUNNEL_NAME / MEETCUTE_TUNNEL_HOSTNAME 없음 → quick 모드 (랜덤 URL)"
  echo ""
  exec cloudflared tunnel --url "http://localhost:${PORT}"
fi

# Named mode
echo "🌐 Named tunnel 모드: $TUNNEL_NAME → $HOSTNAME"

# tunnel 이 없으면 생성
if ! cloudflared tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$TUNNEL_NAME"; then
  echo "🆕 새 tunnel '$TUNNEL_NAME' 생성 중..."
  cloudflared tunnel create "$TUNNEL_NAME" || {
    echo "❌ tunnel 생성 실패. 먼저 'cloudflared tunnel login' 하셨는지 확인."
    exit 1
  }
fi

# DNS 라우팅 (이미 있으면 noop-ish)
echo "🔗 DNS 라우팅: $HOSTNAME → $TUNNEL_NAME"
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME" 2>/dev/null || true

# 실행
echo "🚀 https://$HOSTNAME 으로 접근 가능 (DNS 전파 1-2분 걸릴 수 있음)"
echo ""
exec cloudflared tunnel run --url "http://localhost:${PORT}" "$TUNNEL_NAME"
