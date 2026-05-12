# meetcute 💘

Private matchmaking admin tool — 본인이 주선하는 소개팅 매물·매칭·만남 이력을 한 곳에서 관리.

> ⚠️ 외부 공개용 아님. 인증은 Phase 5에서 추가됨. 일단 로컬에서만 띄워서 사용.

## 빠른 시작

### 1) MySQL 준비
기본 DB가 **MySQL** 입니다. 로컬에 안 깔려있으면 한 번만:

```bash
# Mac
brew install mysql && brew services start mysql

# Ubuntu/Debian
sudo apt install mysql-server && sudo systemctl start mysql
```

기본 접속 정보는 `root` / 빈 비밀번호 / `127.0.0.1:3306` 를 가정합니다. 다르면 환경변수로 덮어쓰세요:
```bash
export MEETCUTE_DB_URL="mysql+pymysql://USER:PASS@HOST:3306/meetcute?charset=utf8mb4"
```

> 💡 **`meetcute` 데이터베이스는 알아서 만들어집니다** (서버 첫 부팅 시 `CREATE DATABASE IF NOT EXISTS`). 본인은 MySQL 서버만 띄워주면 끝.

> 💡 **MySQL 없거나 귀찮으면 SQLite 폴백**:
> ```bash
> export MEETCUTE_DB_URL="sqlite:///./data/meetcute.db"
> ```

### 2) 의존성 + 시드 데이터 + 서버

```bash
cd meetcute
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 가짜 매물 8명 + 만남 8건 + 변경 이력 자동 생성 (선택)
python -m app.seed

uvicorn app.main:app --reload
# http://127.0.0.1:8000
```

또는 `uv` 사용:
```bash
uv sync && uv run python -m app.seed && uv run uvicorn app.main:app --reload
```

### 🌱 시드 스크립트
```bash
python -m app.seed           # 빈 DB일 때만 채움 (안전)
python -m app.seed --force   # 기존 데이터 다 지우고 새로 채움
```
사진은 컬러 정사각형 placeholder 로 자동 생성됩니다. 실제 사진으로 교체하려면 매물 수정에서 새로 업로드.

### 🤖 자리 비울 때: `./dev.sh` (자동 pull + 자동 재시작)

```bash
./dev.sh                        # 기본 (127.0.0.1:8000, 15초마다 깃 폴링)
PORT=8001 ./dev.sh              # 다른 포트
HOST=0.0.0.0 ./dev.sh           # 같은 와이파이의 다른 기기에서 접근 허용
MEETCUTE_AUTH=on ./dev.sh       # 인증 켜기
```

이 스크립트는 우리가 외부에서 새 커밋을 푸시해도 **노트북을 안 만져도 알아서 반영**해줍니다:
- 코드만 바뀌면 → uvicorn이 자동 리로드
- `pyproject.toml` 도 바뀌면 → `pip install` 후 uvicorn 강제 재시작
- uvicorn이 어떤 이유로 죽으면 자동 재기동
- 충돌이 나면 멈추고 알려줌 (수동 해결 필요)

## 데이터 위치

- DB: `./data/meetcute.db` (SQLite, gitignored)
- 사진: `./uploads/{person_id}/...` (gitignored)
- 둘 다 첫 실행 시 자동 생성. 백업은 두 폴더만 챙기면 됨.

## 핵심 모델

| 모델 | 역할 |
|---|---|
| `Person` | 매물. `public_id` (`M-001`/`F-001`/`X-001`) 자동 발급, 실명 X |
| `Photo` | Person에 종속. 디스크 파일 + DB row 동시 관리 |
| `Encounter` | 소개팅 기록. Person 삭제 시 FK는 NULL이 되고 `*_snapshot`에 ID 보존 |

### 정책
- **하드 삭제**: Person 삭제 = DB row + photos + 디스크 파일 모두 제거
- **이력 보존**: Encounter는 절대 삭제 안 됨. 한쪽 Person이 사라져도 스냅샷 문자열로 누구였는지 남음
- **동시 진행 허용**: 한 명이 여러 Encounter를 동시에 PENDING/CONTINUING 상태로 가질 수 있음

## 단계별 로드맵

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | Person CRUD + 사진 업로드 + 대시보드 카운트 | ✅ |
| 2 | Encounter CRUD · 상태 파생 · 만남 이력 타임라인 · 호환성 체크 · 대시보드 강화 · `/manual` | ✅ |
| 3 | 인증 (이메일/비번) · 다중 관리자 · `/users` 권한 토글 | ✅ |
| 4 | 매물 변경 이력(PersonRevision) · 활동 통계 · 잠자는 매물 알림 · 활동 필터/정렬 | ✅ |
| 4.5 | 만남 결과 변경 이력(EncounterEvent) · 결과 변경 사유 메모 | ✅ |
| 5 | 사진 자동 회전(EXIF) · 썸네일 · 드래그 정렬 | ⬜ |
| 6 | 통계 (성공률, 평균 만남 횟수, 매칭 소요 시간) | ⬜ |
| 7 | 비밀번호 재설정 · CSRF · 외부 배포 | ⬜ |

> 📖 사용 매뉴얼은 `MANUAL.md` (또는 앱 실행 후 `/manual`).
> 첫 실행 시 가장 먼저 `/auth/register` 로 가서 첫 계정을 만들면 자동으로 관리자가 됩니다.

## 스택
FastAPI · SQLModel · SQLite · Jinja2 · HTMX · Tailwind (CDN)
