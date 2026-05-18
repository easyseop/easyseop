# Performance & Troubleshooting Log

이 문서는 meetcute 의 성능 개선 / 디버깅 작업을 시간순으로 기록함. 각 항목은
**원인 → 해결 → 측정된 효과** 로 구성. 새 문제 만나면 여기에 추가하고, 비슷한
증상이 다시 나오면 이 문서부터 검색.

벤치마크 표준 (재현 가능하게 동일 조건으로 비교하기 위함):
- 데이터: 마담뚜 3명 + 매물 50–150개 + 만남 30–200건
- 사진: 4000×3000 q92 (모바일 원본 시뮬, 약 3–4MB/장)
- 클라이언트: SQLite + 로컬 TestClient (네트워크 latency 제외)
- 모바일 셀룰러 가정: 5 Mbps (≈ 625 KB/s)

---

## 1. /persons + 대시보드: 같은 Encounter 쿼리를 페이지당 2번 실행

### 원인
`statuses_for_persons` 와 `activity_for_persons` 가 각자 동일한
`SELECT * FROM encounter WHERE person_a_id IN (...) OR person_b_id IN (...)` 을
실행. 게다가 암호화된 `notes` 컬럼까지 매번 가져와서 Fernet 으로 복호화 —
notes 는 status/activity 계산에 안 쓰는데도.

### 해결 (`a287ad3`)
- `status.grouped_encounters_for_persons()` — 단일 fetch + `defer(Encounter.notes)`.
  결과를 `dict[person_id, list[Encounter]]` 로 반환.
- 기존 두 함수에 `grouped=` 선택 인자 추가. 호출자가 한 번만 fetch 해서 양쪽
  전달.
- `routers/persons.list_persons` + `main.index` 둘 다 패치.

### 효과
| 항목 | 변경 전 | 변경 후 |
|---|---:|---:|
| Encounter 쿼리 수 | 2 | 1 |
| `notes` 복호화 횟수 | 2 × N | 0 |
| `/persons` median | (측정 시 67ms) | 64.8ms |

가장 큰 수혜는 콜드 페이지에서 — 페이지당 한 번만 fetch.

---

## 2. 사진 압축 도입: 업로드 1.5초 vs 페이지 28초 트레이드오프

### 원인
원본 모바일 사진을 그대로 저장하면 5장 ≈ 16.7MB. 매 카드 그리드 방문마다
이 16.7MB 를 셀룰러 (5 Mbps) 로 받아옴 → **28초**. 첫 방문이 아니더라도 다른
디바이스나 캐시 만료 시 같은 일이 반복.

### 해결 (`9553c10`)
업로드 시 Pillow 로 자동 리사이즈:
- 긴 변 1600px 비율 유지 thumbnail
- JPEG quality 85 + progressive
- HEIC 는 PIL 미지원 → 원본 복사

### 효과 (5장 4000×3000 q92 합 16.7MB 기준)
| 모드 | 업로드 처리 | 디스크 | 모바일 카드 첫 로드 |
|---|---:|---:|---:|
| 압축 OFF (이전) | 3ms | 16.7MB | 28,000ms |
| 압축 ON (이 커밋) | 1,684ms | 1.1MB | 61ms |

업로드는 한 번, 페이지 보기는 수십~수백 번. **압도적 net win.**

---

## 3. 카드 사진이 여전히 무거움 — 1600px 원본을 ~400px 카드에 그대로

### 원인
업로드 압축이 1600px 까지 줄였지만, 카드 그리드는 화면에서 ~400px 너비.
브라우저가 1600px 짜리를 받아서 400px 로 다시 그림 → 픽셀 수 기준 16배 큰
이미지를 다운받는 셈.

### 해결 (`4e24d6d` + `b0a95c3` + `d07fb5a`)
- 업로드 시 **원본(1600px) + 썸네일(500px)** 둘 다 생성.
  - 카드 / 리스트 / 대시보드 / 폼 미리보기 → 썸네일
  - 상세 / 라이트박스 확대 → 원본
- Jinja 필터 `thumb_url`: `'a/b.jpg' → 'a/b_thumb.jpg'`.
- 옛 사진 호환: `serve_upload` 가 `_thumb` 파일 없으면 원본으로 폴백.
- `python -m app.backfill_thumbs` 한 줄로 기존 사진들 일괄 썸네일 생성
  (idempotent).
- 썸네일 사이즈/품질을 **500px / q75** 로 추가 축소 (800/85 → 500/75).
- 업로드 처리도 최적화: `img.draft("RGB", (1600,1600))` 로 JPEG decoder 가
  중간 해상도로 직접 decode, `img.copy()` 생략 in-place thumbnail, `optimize=True`
  제거 (측정상 동일 사이즈에 시간만 더 듦).

### 효과
| 항목 | 변경 전 | 변경 후 |
|---|---:|---:|
| 카드용 사진 1장 | ~80 KB (1600px q85) | **~10 KB (500px q75)** |
| 60장 카드 그리드 다운로드 | ~4.8 MB | **~600 KB** |
| 업로드 처리 (5장) | 1,684 ms | **1,510 ms** (-10%) |

---

## 4. 사진 carousel: swipe 가 카드 클릭으로 인식 → 매물 상세로 튕김

### 원인
카드 전체가 `<a href="/persons/{id}">` 로 감싸여 있고, `hx-boost="true"` 환경에서
모바일 swipe (carousel 좌우 슬라이드) 가 click 이벤트와 충돌. 손가락을 좌우로
끌었는데 anchor 의 navigation 이 먼저 트리거되면서 매물 상세로 튕겨남. 이 때문에
사용자에겐 "슬라이드 기능이 사라진 것" 처럼 보임.

### 해결 (`10fc544`)
JS 클릭 가드:
```js
strip.addEventListener('touchstart', e => { startX = e.touches[0].clientX; moved = false; }, {passive:true});
strip.addEventListener('touchmove',  e => { if (Math.abs(e.touches[0].clientX - startX) > 8) moved = true; }, {passive:true});
strip.addEventListener('click', e => { if (moved) { e.preventDefault(); e.stopPropagation(); } }, {capture:true});
```
8px 이상 움직였으면 그 직후 click 을 capture 단계에서 차단. 진짜 탭은 통과,
가로 swipe 는 슬라이드만.

immediate `attach` 호출에도 `mcBound` 가드 추가 — htmx 가 inline `<script>` 도
재실행해서 swap 시 중복 부착되던 거 방지.

### 효과
- 모바일에서 carousel 슬라이드 정상 동작.
- "느림" 의 일부 원인 (잘못된 페이지 이동) 도 함께 해소.

---

## 5. 모바일 스크롤 끈적함: PullToRefresh.js 가 비-passive 리스너 부착

### 원인
`pulltorefreshjs@0.1.22` 가 body 에 비-passive `touchstart` / `touchmove`
리스너를 부착. 브라우저는 "이 JS 가 preventDefault 할지도 모르니 기다림" 으로
스크롤 처리를 지연 → 매 터치마다 살짝 끈적함. `shouldPullToRefresh` 가 false
반환해도 매번 JS 가 돌아 차이가 남.

### 해결 (`7ef08f4`)
라이브러리 제거. 모바일 브라우저 탭에서는 어차피 네이티브 pull-to-refresh 가
동작하니, PWA standalone 모드 (홈화면 추가) 케이스만 햄버거 옆 🔄 버튼 한 개로
커버.

같은 커밋에 `require_owner` 의 한글 Location 헤더 버그도 fix — HTTP 헤더가
latin-1 이라 한글 그대로 박으면 `UnicodeEncodeError`. `urllib.parse.quote` 로
URL-encode.

### 효과
- 모바일 스크롤 즉시 부드러워짐.
- 첫 페이지 로드 CDN 요청 1개 감소.

---

## 6. 사진 carousel 의 나머지 사진까지 첫 로드에 다 받아옴

### 원인
매물 카드 1개에 사진 3장이면 페이지 진입 즉시 3개의 `/uploads/...` 요청 발생.
30개 카드면 90건. 사용자가 카드를 탭하지도 않았는데 모두 다운로드.

### 해결 (오늘 커밋 예정)
- 첫 사진은 `src` 즉시, 두 번째부터는 `data-src` 로 들고 있음.
- 사용자가 strip 에 touchstart/scroll/mouseenter 하면 `hydrate(strip)` 호출 →
  나머지 `data-src → src` 로 교체.

### 효과
- 30 카드 × 평균 3장 → 첫 로드 사진 수: **90 → 30** (~67% 감소).
- 셀룰러 5 Mbps 기준 첫 페이지 로드 시간 비례 단축.

---

## 7-bis. (회귀) 6번 적용 후 슬라이드 자체가 안 됨

### 원인
6번에서 두 번째 사진부터 `src` 대신 `data-src` 로만 들고 있게 했는데,
`<img>` 가 src 없으면 **layout box 가 0폭으로 무너짐**. 가로로 스와이프할
공간 자체가 사라져서 첫 장만 표시되는 슬라이드 한 칸으로 변함. 사용자
입장에서 "옆으로 넘겨도 아무 것도 안 일어남" = 슬라이드가 망가진 걸로 보임.

### 해결 (`<다음 커밋>`)
4:3 비율의 투명 SVG 데이터 URL 을 자리표시자 `src` 로 박음:
```html
<img src="data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 4 3'%3E%3C/svg%3E"
     data-src="/uploads/.../photo_thumb.jpg" />
```
이러면 img 가 정상 layout 잡고 (`w-full h-full` 적용됨), 손가락 대면
hydrate() 가 진짜 URL 로 src 교체. bg-neutral-100 으로 회색 자리표시
색깔만 보이다 진짜 사진으로 교체됨.

### 교훈
빈 src 의 img 는 layout 잡지 못함. 자리표시자 (data URL 또는 width/height
attr) 가 반드시 필요.

---

## 7. 사진 업로드 1.5초도 줄이고 싶을 때 — 클라이언트 사이드 압축

### 원인
서버에서 Pillow 로 1600px 리사이즈하는 비용 = 5장에 1.5초. Python GIL +
단일 스레드에 묶여 있어 더 줄이기 어려움. 그동안 사용자는 "저장" 버튼 누른 채
대기.

### 해결 (오늘 커밋 예정)
브라우저에서 미리 `createImageBitmap({imageOrientation:'from-image'})` + `<canvas>`
로 1600px / quality 0.85 압축 후 업로드. 서버는 그 작은 파일을 그냥 저장
(자기 코드도 동일 흐름 한 번 더 돌리지만 입력이 작아 빠름).

HEIC 는 브라우저가 decode 못 하니 그대로 보냄 (서버 원본 복사 경로 유지).
400 KB 미만은 압축 안 함.

### 효과 (예상)
- 업로드 wait time: 1.5s → **~100ms** (브라우저 압축 + 작은 파일 전송).
- 셀룰러 업로드 시 전송 바이트도 16MB → 1MB.
- 서버 CPU 부담 0 (Pillow 가 작은 입력만 처리).

---

## 부록 A. 진단해본 다른 후보 (병목 아님으로 확인)

### 필드 암호화 (`enc1:` Fernet)
- `location`, `age`, `User.email`, `User.password_hash` 가 매 row 마다 Fernet
  복호화.
- 측정: 50개 매물 페이지에서 약 2ms. 60장 사진 다운로드 대비 무시 가능.
- **결론: 보안 가치 그대로, 성능 영향 없음.**

### 로그인 lookup O(n) scan
- `User.email` 이 비결정 암호문이라 `WHERE email = X` lookup 불가. 대신
  `find_user_by_email` 가 전체 유저 스캔 + 복호화 비교.
- 마담뚜 수가 작아 (~5명) 측정상 1ms 미만.
- bcrypt 자체가 300ms 라서 어차피 그게 dominant.

### 활동 로그 INSERT
- 매 write 액션마다 ActivityLog row 1개 추가.
- 측정: <1ms (트랜잭션에 묻어가서 추가 commit 없음).

### Tailwind CDN JIT
- 클래스 많을수록 브라우저 JIT 컴파일 비용.
- 첫 로드에만 영향 (~50–100ms). 이후 cache.
- **언젠가 빌드된 CSS 로 바꾸는 게 정답이지만 셋업 비용 있음.**
  지금은 우선순위 낮음.

---

## 측정 방법 (재현용)

```bash
cd ~/easyseop/meetcute
rm -rf /tmp/mc_bench && mkdir -p /tmp/mc_bench/data /tmp/mc_bench/uploads
MEETCUTE_DB_URL="sqlite:////tmp/mc_bench/data/bench.db" \
MEETCUTE_AUTH=on \
MEETCUTE_SECRET="bench-secret-key-1234567890" \
uv run python <<'EOF'
# (벤치 스크립트는 git log 의 perf 커밋 메시지 또는 본 문서 부록에 박힘)
EOF
```

새 변경이 성능에 영향 줄 것 같으면 위 패턴으로 before/after 두 번 돌리고 이
문서 항목 추가.
