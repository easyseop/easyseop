# 영구 도메인 셋업 (cloudflared named tunnel)

`madam-ddu.com` 을 써서 **재부팅해도 안 바뀌는 고정 URL** 로 서비스를 노출한다.
서비스 주소: **`https://madam-ddu.com`** (루트 도메인)

> 나중에 서브도메인(`app.madam-ddu.com` 등)으로 바꾸고 싶으면 `.env` 의
> `MEETCUTE_TUNNEL_HOSTNAME` 한 줄만 고치고 `./tunnel.sh` 재실행하면 됨.

> 랜덤 URL 을 매번 공유하던 quick 모드 대신, 도메인 하나로 영구 고정.
> 앱/DB/사진은 전부 맥북에 그대로. 터널만 도메인에 묶는다.

## 사전 조건 (한 번만)

1. 도메인 `madam-ddu.com` 을 Cloudflare 계정에 보유 (Registrar 에서 구매 → 자동 연동됨)
2. `brew install cloudflared` (이미 설치돼 있으면 스킵)

## 1단계 — Cloudflare 로그인 (맥북 브라우저 인증)

```bash
cloudflared tunnel login
```
- 브라우저가 열리면 Cloudflare 로그인 → **`madam-ddu.com` 선택 → Authorize**
- 성공하면 `~/.cloudflared/cert.pem` 이 생김 (이게 인증 파일)

## 2단계 — `.env` 에 두 줄 추가

`~/meetcute/.env` 파일에 아래 두 줄 추가 (없으면 새로 만들기):

```
MEETCUTE_TUNNEL_NAME=meetcute
MEETCUTE_TUNNEL_HOSTNAME=madam-ddu.com
```

> `.env` 는 git 에 안 올라감(gitignore). 맥북 로컬에만 존재.
> 기존 `.env` 의 `MEETCUTE_AUTH=on`, 텔레그램 토큰 등은 그대로 두고 두 줄만 추가.

## 3단계 — 터널 실행

```bash
cd ~/meetcute
./tunnel.sh
```

`tunnel.sh` 가 named 모드로 인식하고 자동으로:
1. 터널 `meetcute` 생성 (`cloudflared tunnel create meetcute`) — 처음 한 번만
2. DNS 라우팅 (`madam-ddu.com` → 터널)
3. `.public_url` 에 `https://madam-ddu.com` 기록 → 앱이 감지해 마담뚜에게 새 URL 텔레그램 알림
4. 터널 실행

## 재부팅 후 절차 (앞으로 매번)

```bash
# 터미널 A
cd ~/meetcute && ./dev.sh

# 터미널 B
cd ~/meetcute && ./tunnel.sh
```

URL 은 **항상 `https://madam-ddu.com`** — 안 바뀌므로 마담뚜에게 재공유 불필요.
홈 화면에 추가한 아이콘도 계속 유효.

## 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| `tunnel login` 후 도메인이 안 보임 | Registrar 에서 산 도메인이 아직 계정에 연동 안 됨. 몇 분 기다렸다 재시도 |
| `tunnel.sh` 가 quick 모드로 뜸 | `.env` 의 두 변수 오타 확인 (`TUNNEL_NAME` / `TUNNEL_HOSTNAME`) |
| `route dns` 실패 (already exists) | 이미 라우팅됨 — 무시해도 됨. tunnel.sh 가 `|| true` 로 넘어감 |
| 502 Bad Gateway | dev.sh(8765) 가 안 떠있음. 터미널 A 먼저 실행 |

## 참고 — 나중에 주소 바꾸기

지금은 루트 `madam-ddu.com` 사용. 나중에 서브도메인으로 바꾸려면
`.env` 의 `MEETCUTE_TUNNEL_HOSTNAME=app.madam-ddu.com` 처럼 고치고
`./tunnel.sh` 재실행하면 즉시 반영된다 (터널 자체는 재사용).
