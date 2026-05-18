# 🔧 운영 가이드 (책임자용)

서버 띄우기, 외부 노출, 백업, 인증, 보안. 사용자(매물 관리)는 [웹 가이드](/manual/web) 참고.

## 1. 서버 띄우기

### 매일 사용
```bash
cd ~/easyseop/meetcute
./dev.sh
```

`dev.sh` 가 자동으로:
- `.env` 환경변수 로드
- `.secret` 파일 생성/로드 (세션 쿠키 키)
- `pip install -e .` (의존성 동기화)
- uvicorn `--reload` 시작
- 15초마다 git 폴링 → 새 커밋 자동 pull → 자동 리로드
- pyproject 변경 시 자동 재설치 + 재시작
- uvicorn 죽으면 자동 재기동

### .env 옵션
```bash
PORT=8765                                     # 포트 (기본 8765)
HOST=127.0.0.1                                # 0.0.0.0 으로 하면 같은 와이파이 접근
MEETCUTE_AUTH=on                              # 인증 켜기 (기본 off)
MEETCUTE_PUBLIC=on                            # 외부 노출 모드 (HTTPS 쿠키 + AUTH 강제)
MEETCUTE_PUBLIC_URL=https://meetcute.xxx.com  # 텔레그램 메시지의 링크 prefix
MEETCUTE_TELEGRAM_BOT_TOKEN=123:abc...        # 봇 토큰 (BotFather 에서)
MEETCUTE_DB_URL=sqlite:///./data/meetcute.db  # DB URL (기본 SQLite)
MEETCUTE_TUNNEL_NAME=meetcute                 # cloudflared 영구 터널 이름
MEETCUTE_TUNNEL_HOSTNAME=meetcute.xxx.com     # 영구 도메인
```

## 2. 외부 노출 (cloudflared)

별도 터미널에서:
```bash
./tunnel.sh
```

- `.env` 의 `MEETCUTE_TUNNEL_NAME` + `MEETCUTE_TUNNEL_HOSTNAME` 둘 다 있으면 → **named 모드** (영구 URL)
- 없으면 → **quick 모드** (`*.trycloudflare.com` 랜덤 URL, 재시작마다 바뀜)

### 영구 URL 셋업 (1회)
```bash
brew install cloudflared
cloudflared tunnel login          # 브라우저로 cloudflare 계정 인증 (도메인 필요)
# .env 에 MEETCUTE_TUNNEL_NAME / MEETCUTE_TUNNEL_HOSTNAME 추가
./tunnel.sh                       # 자동으로 tunnel 생성 + DNS 라우팅
```

### URL 변경 자동 알림
`tunnel.sh` 가 현재 URL 을 `.public_url` 파일에 기록. 앱이 매분 확인 → 변경 감지 시 등록된 모든 마담뚜 에게 텔레그램으로 새 URL 푸시.

## 3. 인증 토글

| 모드 | env | 동작 |
|---|---|---|
| 로컬 (기본) | `MEETCUTE_AUTH` 없음 | 로그인 X · 모든 페이지 바로 접근 |
| 인증 | `MEETCUTE_AUTH=on` | 로그인 필요 · 다중 admin 가능 |
| 공개 | `MEETCUTE_PUBLIC=on` | 인증 자동 강제 + HTTPS 쿠키 + 분홍 배너 |

### 첫 가입자 = 책임자
- DB 에 유저 없을 때 첫 가입자가 자동으로 `is_admin=True, is_owner=True`
- 이후 가입자는 책임자 승인 대기 (`/auth/pending`)
- 책임자가 `/users` 에서 **마담뚜로** 버튼 클릭하면 풀 권한

### 책임자 (👑) 특권
- 모든 사람 텔레그램 chat_id 열람 (다른 마담뚜한테는 안 보임)
- 모든 매물 수정/삭제 (다른 마담뚜 의 매물도)
- `/users` 에서 권한 부여/회수

### 마지막 책임자 보호
- 책임자가 1명이면 강등/삭제 불가
- 책임자 옮기려면 먼저 다른 사람 승급 후 본인 강등

## 4. 데이터베이스

- **기본: SQLite** (`meetcute/data/meetcute.db`)
- 첫 부팅 시 자동 생성, 스키마 마이그레이션도 자동 (`ALTER TABLE ADD COLUMN`)
- `MEETCUTE_DB_URL` 로 MySQL/Postgres 전환 가능 (`mysql+pymysql://...`, `postgresql+psycopg2://...`)

### 시드 데이터 (가짜 매물 8명 + 만남 + 이력)
```bash
python -m app.seed              # 빈 DB 일 때만
python -m app.seed --force      # 다 지우고 다시
```

### DB 상태 확인
`/settings` 페이지의 "💾 데이터 / 시스템 상태" 카드:
- DB 타입 / 파일 경로 / 파일 크기
- 사진 폴더 크기 / 사진 개수
- 테이블별 row 개수

## 5. 백업 (Dropbox / iCloud)

데이터는 두 폴더 + **두 키 파일**에만 있음:
- `meetcute/data/meetcute.db` — DB
- `meetcute/uploads/` — 사진
- `meetcute/.secret` — 세션 쿠키 서명 키 (사라지면 모든 사용자 다시 로그인)
- `meetcute/.encryption_key` — **DB 필드 암호화 키** (사라지면 거주지/나이 + 마담뚜 이메일/비밀번호 복구 불가 = 로그인 자체 불가. 옛날에 암호화돼 아직 평문으로 안 덮어쓴 데이터도 못 읽음)

⚠️ **.encryption_key 가 가장 중요.** 이 파일 잃으면 암호화된 텍스트 영구 복구 불가. 백업 폴더에 꼭 같이 복사하세요.

### 방법 A — 심볼릭 링크 자동 (1회 셋업, ⭐ 추천)
```bash
Ctrl+C   # dev.sh 종료
cd ~/easyseop/meetcute

mkdir -p ~/Dropbox/meetcute-backup
mv data ~/Dropbox/meetcute-backup/data
mv uploads ~/Dropbox/meetcute-backup/uploads
ln -s ~/Dropbox/meetcute-backup/data data
ln -s ~/Dropbox/meetcute-backup/uploads uploads

./dev.sh
```

⚠️ **노트북 한 대에서만** 사용 (SQLite 단일 writer). 같은 Dropbox DB 를 두 노트북에서 동시 접근하면 깨질 수 있음.

### 방법 B — 주기적 rsync
```bash
crontab -e
# 매일 새벽 2시
0 2 * * * rsync -a ~/easyseop/meetcute/data ~/easyseop/meetcute/uploads ~/Dropbox/meetcute-backup/
```

### 방법 C — 백업 명령 (⭐ 추천, 키 파일까지 같이 묶임)
한 줄로 `data/` + `uploads/` + `.encryption_key` + `.secret` 모두를 timestamped `.tar.gz` 로:
```bash
cd ~/easyseop/meetcute
python -m app.backup                              # → BASE_DIR/backups/meetcute-YYYYMMDD-HHMMSS.tar.gz
python -m app.backup --out ~/Dropbox/meetcute-bk  # 다른 폴더로
python -m app.backup --keep 30                    # 최신 30개만 유지 (기본 14)
```
환경변수 `MEETCUTE_BACKUP_DIR=~/Dropbox/meetcute-bk` 로 기본 출력 폴더 지정 가능. cron 예:
```bash
0 2 * * * cd ~/easyseop/meetcute && /usr/bin/python -m app.backup --quiet
```

### 복구
```bash
Ctrl+C
# 방법 A/B (폴더 백업)
cp -R ~/Dropbox/meetcute-backup/data/* data/
cp -R ~/Dropbox/meetcute-backup/uploads/* uploads/
# 방법 C (tar.gz 백업)
tar xzf ~/Dropbox/meetcute-bk/meetcute-2026XXXX-XXXXXX.tar.gz -C ~/easyseop/meetcute/
./dev.sh
```

### 사진 썸네일 백필 (한 번만, 속도 향상)
신규 업로드는 자동으로 800px 썸네일을 만들지만, **썸네일 기능 이전에 올라온 사진은 _thumb 파일이 없어서** 카드 그리드가 매번 원본(1600px)을 불러와 느림. 한 번 돌려두면 옛 사진도 새 사진처럼 빨라짐:
```bash
cd ~/easyseop/meetcute
python -m app.backfill_thumbs
# 생성: N개  /  건너뜀(이미 있음): M개  /  실패: 0개
```
idempotent — 여러 번 돌려도 안전.

## 6. 보안

### 자동 적용
- 세션 쿠키 서명 (`.secret` 자동 생성, chmod 600)
- **DB 필드 암호화** (`.encryption_key` 자동 생성, Fernet/AES-128-CBC + HMAC)
  - 매물 PII: **거주지, 나이** 두 개만 (직장은 평문)
  - 마담뚜 계정: **이메일, 비밀번호 해시** — 비밀번호는 이미 bcrypt 단방향 해시지만 그 위에 한 번 더 암호화 (DB 만 털리고 `.encryption_key` 안 털린 경우 오프라인 크래킹 차단)
  - 비암호화: 그 외 모든 것 (public_id, 성별, 키, 직장, 이름(alias), 이상형, 주선자 메모, 만남 메모, 변경 이력, 요청/응답 메모, 닉네임, 텔레그램)
  - 백워드 호환: 기존 평문은 그대로 읽힘. 옛날에 enc1: 로 암호화된 데이터(직장/alias/이상형/메모 등)도 자동 복호화해서 읽다가, 한 번 수정/저장되면 평문으로 정착
  - 이메일은 비결정적 암호문이라 DB 인덱스 lookup 안 됨 → 로그인/중복체크는 전체 유저 스캔 + 복호화 비교 (마담뚜 수가 적어 비용 무시)
- 매물 공개 범위 (PUBLIC / RESTRICTED): RESTRICTED 매물은 owner + 책임자 + 허락된 admin 만 접근
- bcrypt 비밀번호 해싱
- SameSite=Lax 쿠키 (CSRF 차단)
- 사진 URL 인증 게이트 (로그인 안 한 사람은 못 봄)
- 로그인 5회 실패 시 IP 10분 잠금
- `/uploads/` path traversal 차단
- 책임자만 다른 사람 chat_id 열람
- 이름(메모) 은 owner 본인/책임자에게만 표시

### 외부 노출 시 추가 권장
- `.env` 에 `MEETCUTE_PUBLIC=on` → HTTPS 쿠키 강제
- 강한 `MEETCUTE_SECRET` (`.secret` 파일 자동 생성, 64자 hex)
- cloudflared 가 HTTPS 자동
- 가입은 누구나 가능하지만 책임자 승인 없이는 아무 기능 못 씀

### 비밀번호 잊었을 때 (재설정 UI 없음)
```bash
/tmp/meetcute-venv/bin/python -c "
from app.database import engine
from sqlmodel import Session, select
from app.models import User
from app.auth import hash_password
with Session(engine) as s:
    u = s.exec(select(User).where(User.email == '본인이메일')).first()
    u.password_hash = hash_password('새비밀번호')
    s.add(u); s.commit()
print('변경 완료')
"
```

또는 그 사람의 user row 를 삭제 후 재가입.

## 7. 진단 / 트러블슈팅

| 증상 | 확인 |
|---|---|
| 페이지 로딩 안 됨 | dev.sh 콘솔에 에러 있는지 |
| 텔레그램 봇 응답 X | `.env` 에 `MEETCUTE_TELEGRAM_BOT_TOKEN` 있는지 + dev.sh 재시작 했는지 |
| `봇 인증 실패: SSL 인증서` | macOS python.org 설치 시 → 자동 처리됨 (certifi 사용). 그래도 나면 `pip install -e .` 다시 |
| `MEETCUTE_SECRET 환경변수가 설정되지 않았습니다` | `.secret` 파일 자동 생성됨 — dev.sh 재시작 |
| `getUpdates` 가 비어있어 자동 감지 안 됨 | 봇 username 잘못 (다른 봇한테 메시지 보냄). `/settings` 페이지의 🟢 박스에서 정확한 username 확인 |

콘솔 로그: dev.sh 띄운 터미널.
