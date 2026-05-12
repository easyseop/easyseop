# meetcute 💘

Private matchmaking admin tool — 본인이 주선하는 소개팅 매물·매칭·만남 이력을 한 곳에서 관리.

> ⚠️ 외부 공개용 아님. 인증은 Phase 5에서 추가됨. 일단 로컬에서만 띄워서 사용.

## 빠른 시작

```bash
cd meetcute
uv sync                         # 또는: pip install -e .
uv run uvicorn app.main:app --reload
# http://127.0.0.1:8000
```

`pip` 사용 시:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

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
| 5 | 사진 자동 회전(EXIF) · 썸네일 · 드래그 정렬 | ⬜ |
| 6 | 통계 (성공률, 평균 만남 횟수, 매칭 소요 시간) | ⬜ |
| 7 | 비밀번호 재설정 · CSRF · 외부 배포 | ⬜ |

> 📖 사용 매뉴얼은 `MANUAL.md` (또는 앱 실행 후 `/manual`).
> 첫 실행 시 가장 먼저 `/auth/register` 로 가서 첫 계정을 만들면 자동으로 관리자가 됩니다.

## 스택
FastAPI · SQLModel · SQLite · Jinja2 · HTMX · Tailwind (CDN)
