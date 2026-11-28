/*
 * Thread core — 업무 규칙 한 벌.
 * 서버(server.js)와 브라우저 시연본이 같은 파일을 쓴다. 화면에서 본 규칙과 검증한 규칙이 갈라지지 않게 하기 위해서다.
 * 모든 요청은 handle(state, req) 하나로 들어온다. 열람 권한은 여기서만 판단한다.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ThreadCore = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const STATUS = { todo: '할 일', doing: '진행 중', blocked: '막힘', hold: '보류', done: '완료' };
  const KINDS = {
    note: '기록', impl: '구현 완료 보고', func: '독립 기능 검증', field: '실사용 검증',
    deploy: '운영 배포', decision: '결정', handoff: '인계', edit: '수정',
  };
  const STAGES = [
    { key: 'impl', label: '구현', pass: '완료', fail: '미완료', none: '아직 안 됨' },
    { key: 'func', label: '독립 기능 검증', pass: '통과', fail: '실패', none: '미검증' },
    { key: 'field', label: '실사용 검증', pass: '통과', fail: '실패', none: '미검증' },
    { key: 'deploy', label: '운영 배포', pass: '배포됨', fail: '배포 실패', none: '배포 전' },
  ];
  const EDITABLE = {
    title: '제목', status: '상태', next_owner_id: '다음 담당자', next_action: '다음 할 일',
    blocked_reason: '막힌 이유', current_state: '현재 상태', done_criteria: '완료 조건',
  };

  /* ───────────── 가상 데이터 ───────────── */
  function seed(nowMs) {
    const t0 = nowMs || Date.now();
    const ago = (h) => new Date(t0 - h * 3600e3).toISOString();
    const users = {};
    [
      ['u_me', '한서윤', 'human', '제품기획팀'],
      ['u_dev', '박도현', 'human', '플랫폼개발팀'],
      ['u_jisu1', '김지수', 'human', '디자인팀'],
      ['u_jisu2', '김지수', 'human', '재무팀'],
      ['u_new', '이하람', 'human', '매장운영팀'],
      ['ai_claude', 'Claude', 'ai', 'AI · 구현'],
      ['ai_codex', 'Codex', 'ai', 'AI · 검증'],
      ['ai_verifier', '독립 검증자', 'ai', 'AI · 별도 세션'],
    ].forEach(([id, name, kind, team]) => { users[id] = { id, name, kind, team }; });

    const projects = [
      {
        id: 'p1', name: '예약 알림 개편',
        goal: '예약 고객이 알림을 받고 직접 변경·취소할 수 있게 해 노쇼율을 12%에서 6%로 낮춘다',
        start: '노쇼율 12%', finish: '노쇼율 6%',
        members: ['u_me', 'u_dev', 'u_jisu1', 'u_jisu2', 'u_new', 'ai_claude', 'ai_codex'],
        milestones: [
          { id: 'm1', title: '알림이 제때 도착한다', requirements: [
            { id: 'r1', title: '예약 24시간 전과 2시간 전에 알림을 보낸다' },
            { id: 'r2', title: '카카오 알림톡이 실패하면 문자로 대신 보낸다' },
          ] },
          { id: 'm2', title: '고객이 직접 변경·취소한다', requirements: [
            { id: 'r3', title: '알림 안의 링크로 로그인 없이 예약을 변경한다' },
            { id: 'r4', title: '취소할 때 위약금을 미리 안내한다' },
            { id: 'r5', title: '변경·취소 결과를 매장 관리자에게 알린다' },
          ] },
          { id: 'm3', title: '효과를 확인한다', requirements: [
            { id: 'r6', title: '매장별 노쇼율을 개편 전후로 비교한다' },
            { id: 'r7', title: '매장 2곳에서 2주간 시범 운영한다' },
          ] },
        ],
        later: [
          { id: 'r8', title: '알림 문구를 두 가지로 나눠 효과를 비교한다', own_status: 'hold', note: '시범 운영 결과가 나온 뒤 다시 검토' },
          { id: 'r9', title: '영어·중국어 알림을 지원한다', own_status: 'hold', note: '외국인 예약 비율 3% 미만. 다음 분기 검토' },
        ],
      },
      {
        id: 'p2', name: '정산 리포트 자동화',
        goal: '매달 3일 걸리던 매장 정산 리포트를 하루 안에 검수까지 끝낸다',
        start: '수작업 3일', finish: '하루 안에 확정',
        members: ['u_me', 'u_dev', 'u_jisu1', 'u_jisu2', 'ai_codex'],
        milestones: [
          { id: 'm21', title: '초안이 자동으로 만들어진다', requirements: [
            { id: 'r21', title: '매출·환불 데이터를 매일 모은다' },
            { id: 'r22', title: '정산 리포트 초안을 자동으로 만든다' },
          ] },
          { id: 'm22', title: '검수하고 확정한다', requirements: [
            { id: 'r23', title: '환불·부분취소가 두 번 집계되지 않는지 검수한다' },
            { id: 'r24', title: '급여성 정산 예외를 따로 검토한다' },
            { id: 'r25', title: '확정본을 매장에 보낸다' },
          ] },
        ],
        later: [
          { id: 'r26', title: '매장의 정산 문의에 자동으로 답한다', own_status: 'hold', note: '확정본 발송이 안정된 뒤 검토' },
        ],
      },
    ];

    const tasks = {};
    const T = (o) => {
      tasks[o.id] = Object.assign({
        assignee_id: null, next_owner_id: null, next_action: '', blocked_reason: '', goal: '',
        done_criteria: [], criteria_version: 1, criteria_log: [], current_state: '', rationale: [],
        restricted: null, decision: null, track: 'build', handoff_from: null, handed_at: null, rev: 1, updated_at: ago(24),
      }, o);
      if (!tasks[o.id].criteria_log.length) {
        tasks[o.id].criteria_log = [{ version: 1, items: tasks[o.id].done_criteria.slice(), at: ago(120), by: 'u_me' }];
      }
    };

    T({ id: 't1', project_id: 'p1', requirement_id: 'r1', title: '알림 발송 시각을 예약 24시간 전·2시간 전으로 맞춘다',
      status: 'done', assignee_id: 'ai_claude', goal: '고객이 예약을 잊지 않게 한다',
      done_criteria: ['예약 24시간 전과 2시간 전에 각각 한 번 발송된다', '이미 취소된 예약에는 발송되지 않는다'],
      current_state: '운영에 배포되어 사용 중.', updated_at: ago(200) });
    T({ id: 't2', project_id: 'p1', requirement_id: 'r2', title: '알림톡 실패 시 문자로 대신 보낸다',
      status: 'done', assignee_id: 'ai_claude', goal: '알림톡을 못 받는 고객도 알림을 받게 한다',
      done_criteria: ['알림톡 실패 후 1분 안에 문자가 발송된다', '같은 알림이 두 번 가지 않는다'],
      current_state: '운영에 배포되어 사용 중.', updated_at: ago(150) });
    T({ id: 't3', project_id: 'p1', requirement_id: 'r3', title: '알림 링크로 로그인 없이 예약을 변경하게 한다',
      status: 'doing', assignee_id: 'ai_claude', next_owner_id: 'ai_claude',
      next_action: '만료 시각을 서버에서 다시 검사하도록 고치고 Codex에 재검증을 요청한다',
      goal: '고객이 매장에 전화하지 않고도 예약을 바꿀 수 있게 한다',
      done_criteria: ['알림 안의 링크로 로그인 없이 예약 시간을 바꿀 수 있다', '변경하면 확인 문자가 온다', '링크는 한 번만 쓸 수 있고 24시간 뒤 만료된다'],
      criteria_version: 2,
      criteria_log: [
        { version: 1, items: ['알림 안의 링크로 로그인 없이 예약 시간을 바꿀 수 있다', '변경하면 확인 문자가 온다'], at: ago(120), by: 'u_me' },
        { version: 2, items: ['알림 안의 링크로 로그인 없이 예약 시간을 바꿀 수 있다', '변경하면 확인 문자가 온다', '링크는 한 번만 쓸 수 있고 24시간 뒤 만료된다'], at: ago(30), by: 'u_me' },
      ],
      current_state: '구현은 끝났지만 현재 기준(v2) 독립 기능 검증에서 실패했다. 만료된 링크로도 예약이 변경된다.',
      rationale: [
        { text: '보안 검토에서 링크가 유출되면 타인이 예약을 바꿀 수 있다는 지적이 나와 완료 조건에 만료를 추가했다', ref: { type: 'record', id: 'c3' } },
      ],
      updated_at: ago(5) });
    T({ id: 't4', track: 'check', project_id: 'p1', requirement_id: 'r3', title: '시범 매장 2곳에 예약 변경 링크 안내문을 전달한다',
      status: 'todo', assignee_id: 'u_me', next_owner_id: 'u_me',
      next_action: '강남점·판교점 점주에게 안내문 v3을 보내고, 수신 확인을 이 업무에 기록한다',
      goal: '시범 운영 전에 점주가 고객 문의에 답할 수 있게 한다',
      done_criteria: ['강남점·판교점 점주가 안내문을 받았다', '두 점주의 수신 확인이 이 업무에 기록되어 있다'],
      current_state: '안내문 초안 v3 완성. 디자인 검수 통과. 아직 발송 전.',
      rationale: [
        { text: '안내문은 점주 5명 사전 인터뷰에서 가장 많이 나온 질문 3가지를 기준으로 썼다', ref: { type: 'material', id: 'mt1' } },
        { text: '문구와 레이아웃은 디자인팀 검수를 통과했다', ref: { type: 'record', id: 'c7' } },
        { text: '점주에게 직접 연락하는 일이라 발송은 사람이 맡는다', ref: { type: 'record', id: 'c8' } },
      ],
      handoff_from: 'ai_claude', handed_at: ago(2), updated_at: ago(2) });
    T({ id: 't5', project_id: 'p1', requirement_id: 'r4', title: '취소 화면에 위약금 안내를 넣는다',
      status: 'blocked', assignee_id: 'ai_claude', next_owner_id: 'u_me',
      next_action: '위약금 안내 기준을 결정한다',
      blocked_reason: '매장마다 위약금 기준이 달라 안내할 금액을 정할 수 없다',
      goal: '고객이 취소 전에 비용을 알고 결정하게 한다',
      done_criteria: ['취소 버튼을 누르기 전에 위약금 안내가 보인다', '안내 금액이 정해진 기준과 일치한다'],
      current_state: '취소 화면 틀은 구현됨. 안내할 금액 기준이 없어 멈춤.',
      rationale: [
        { text: '14개 매장 중 11곳은 본사 표준 기준을 쓰고, 3곳만 자체 기준을 쓴다', ref: { type: 'material', id: 'mt4' } },
      ],
      decision: {
        question: '위약금 안내 기준을 무엇으로 할까요?',
        why: '고객 정책과 일정에 영향을 줍니다',
        owner_id: 'u_me', requested_by: 'ai_claude', requested_at: ago(9), recommended: 'a', unblocks: true,
        options: [
          { id: 'a', label: '본사 표준 기준으로 안내한다', effect: '모든 매장에 같은 기준을 보여 준다. 추가 개발 없음. 자체 기준을 쓰는 3곳은 문의가 늘 수 있다.', next_action: '본사 표준 위약금 기준으로 안내 화면을 구현한다', next_owner_id: 'ai_claude' },
          { id: 'b', label: '매장별 기준을 입력받아 안내한다', effect: '매장마다 정확한 금액을 보여 준다. 입력 화면 개발에 약 2주가 더 든다.', next_action: '매장별 위약금 입력 화면의 요건을 정리한다', next_owner_id: 'u_dev' },
          { id: 'c', label: '금액 없이 위약금이 있을 수 있다고만 안내한다', effect: '가장 빨리 끝난다. 고객이 금액을 몰라 매장 문의가 늘 수 있다.', next_action: '금액 없는 위약금 안내 문구를 넣는다', next_owner_id: 'ai_claude' },
        ],
        chosen: null, chosen_at: null,
      },
      updated_at: ago(9) });
    T({ id: 't6', project_id: 'p1', requirement_id: 'r5', title: '예약이 바뀌면 매장 관리자에게 알림을 보낸다',
      status: 'todo', assignee_id: 'u_dev', next_owner_id: 'u_dev', next_action: '알림을 받을 관리자 범위(점주만, 또는 직원 포함)를 정한다',
      goal: '매장이 바뀐 예약을 놓치지 않게 한다', done_criteria: ['변경·취소 후 1분 안에 관리자 알림이 간다'],
      current_state: '아직 시작하지 않음.', updated_at: ago(70) });
    T({ id: 't8', track: 'check', project_id: 'p1', requirement_id: 'r7', title: '시범 운영 점검표를 새 화면 시안에 맞춰 고친다',
      status: 'doing', assignee_id: 'u_jisu1', next_owner_id: 'u_jisu1', next_action: '점검표의 화면 캡처 4장을 새 시안으로 바꾼다',
      goal: '점주가 점검표만 보고 시범 운영을 준비하게 한다', done_criteria: ['점검표의 모든 화면이 최신 시안과 같다'],
      current_state: '점검표 문구는 확정. 화면 캡처만 이전 시안이다.',
      handoff_from: 'u_me', handed_at: ago(26), updated_at: ago(26) });
    T({ id: 't13', track: 'check', project_id: 'p1', requirement_id: 'r7', title: '시범 운영 매장 2곳의 시작 일정을 확정한다',
      status: 'todo', assignee_id: 'u_new', next_owner_id: 'u_new',
      next_action: '강남점·판교점 점주와 통화해 시작일을 12월 둘째 주 안에서 정하고 이 업무에 기록한다',
      goal: '시범 운영을 12월 안에 시작한다', done_criteria: ['두 매장의 시작일이 정해졌다', '시작일이 이 업무에 기록되어 있다'],
      current_state: '두 매장 모두 참여에 동의함. 시작일만 미정.',
      rationale: [{ text: '두 점주 모두 12월 둘째 주가 가능하다고 답했다', ref: { type: 'record', id: 'c12' } }],
      handoff_from: 'u_dev', handed_at: ago(8), updated_at: ago(8) });
    T({ id: 't9', project_id: 'p1', requirement_id: 'r6', title: '매장별 노쇼율 비교 화면을 만든다',
      status: 'hold', assignee_id: 'u_dev', next_owner_id: 'u_dev', next_action: '데이터팀의 집계 기준이 확정되면 다시 시작한다',
      blocked_reason: '데이터팀이 노쇼 집계 기준을 12월 중순에 확정할 예정',
      goal: '개편 효과를 숫자로 확인한다', done_criteria: ['매장별로 개편 전후 노쇼율이 나란히 보인다'],
      current_state: '화면 구성안만 있음.', updated_at: ago(90) });

    T({ id: 't20', project_id: 'p2', requirement_id: 'r21', title: '매출·환불 데이터를 매일 새벽에 모은다',
      status: 'done', assignee_id: 'ai_codex', goal: '리포트가 항상 전날까지의 데이터를 쓰게 한다',
      done_criteria: ['매일 06시 전에 전날 데이터가 들어온다'], current_state: '운영에 배포되어 사용 중.', updated_at: ago(300) });
    T({ id: 't12', project_id: 'p2', requirement_id: 'r22', title: '정산 리포트 초안을 자동으로 만든다',
      status: 'doing', assignee_id: 'ai_codex', next_owner_id: 'ai_codex', next_action: '남은 2단계(수수료 계산, 매장별 합계)를 구현한다',
      goal: '사람이 표를 만드는 시간을 없앤다', done_criteria: ['초안의 매장별 합계가 수작업 리포트와 1원 단위까지 같다'],
      current_state: '5단계 중 3단계까지 구현. 연결이 끊겨 이후 진행은 확인되지 않았다.', updated_at: ago(3) });
    T({ id: 't10', track: 'check', project_id: 'p2', requirement_id: 'r23',
      title: '10월 정산 리포트에서 환불·부분취소 건이 두 번 집계되는지 재무팀 기준으로 검수 항목을 정리한다',
      status: 'todo', assignee_id: 'u_me', next_owner_id: 'u_me',
      next_action: '재무팀 검수표 초안의 7개 항목 중 자동 검수로 바꿀 항목을 고른다',
      goal: '검수를 사람 기억이 아니라 정해진 항목으로 한다',
      done_criteria: ['검수 항목이 목록으로 정리되어 있다', '각 항목에 자동·수동 여부가 적혀 있다'],
      current_state: '재무팀이 검수표 초안(7개 항목)을 올림. 자동·수동 구분 전.',
      rationale: [{ text: '10월 리포트에서 부분취소 4건이 두 번 집계된 사례가 있었다', ref: { type: 'material', id: 'mt5' } }],
      handoff_from: 'u_jisu2', handed_at: ago(20), updated_at: ago(20) });
    T({ id: 't11', track: 'check', project_id: 'p2', requirement_id: 'r24', title: '급여성 정산 예외 내역을 검토한다',
      status: 'todo', assignee_id: 'u_jisu2', next_owner_id: 'u_jisu2', next_action: '11월 예외 6건의 지급 근거를 확인한다',
      goal: '급여성 지급이 리포트에 섞이지 않게 한다', done_criteria: ['예외 6건의 근거가 확인되었다'],
      current_state: '예외 6건 목록 확보.', restricted: ['u_jisu2', 'u_dev'],
      handoff_from: 'u_dev', handed_at: ago(12), updated_at: ago(12) });

    // Thread 자체의 요건. 가상 프로젝트와 달리 실제 개발·검증 현황을 적는다.
    projects.unshift({
      id: 'p0', name: 'Thread 만들기', real: true,
      goal: '처음 온 사람이 설명 없이 목표·진행 중인 일·근거·다음 행동을 파악하고 바로 이어서 일한다',
      start: '기존 자료 없음', finish: '첫 사용자 세 유형 통과',
      members: ['u_me', 'u_dev', 'ai_claude', 'ai_verifier'],
      milestones: [
        { id: 'm01', title: '홈에서 찾고 상세에서 확인한다', requirements: [
          { id: 'r01', title: '홈에서 판단할 일과 이어받을 일을 구분해 보여 준다' },
          { id: 'r02', title: '업무 상세를 읽기 상태로 열고 상태·담당자·다음 할 일을 먼저 보여 준다' },
          { id: 'r03', title: '저장 전에는 바뀌는 내용을, 저장 후에는 반영 여부와 다음 할 일을 보여 준다' },
          { id: 'r18', title: '마지막으로 확인한 뒤 바뀐 것을 홈에서 먼저 보여 준다' },
        ] },
        { id: 'm02', title: '요건으로 진척을 본다', requirements: [
          { id: 'r04', title: '프로젝트 목표에서 중간 목표, 요건, 업무로 내려가며 진척을 본다' },
          { id: 'r05', title: '검색 결과에서 원래 요구사항과 검증 이력을 연다' },
        ] },
        { id: 'm03', title: '인계와 검증을 믿을 수 있다', requirements: [
          { id: 'r06', title: '저장을 재시도해도 결과는 한 건만 남는다' },
          { id: 'r07', title: '열람 권한이 없는 사람과 이름이 같은 다른 사람에게 업무·자료를 보이지 않는다' },
          { id: 'r08', title: '구현·독립 기능 검증·실사용 검증·운영 배포와 이전 기준·현재 기준을 구분한다' },
          { id: 'r09', title: 'AI 현황에서 마지막으로 확인한 결과와 최근 조회 시각을 구분한다' },
          { id: 'r17', title: '근거를 확인할 수 있는 것(커밋, 시험 결과)과 서술만 있는 것으로 나눠 보여 준다' },
        ] },
        { id: 'm04', title: '실제 첫 사용자가 통과한다', requirements: [
          { id: 'r10', title: '비개발자·개발자·AI 도구 첫 사용자가 도움 없이 핵심 과제를 끝낸다' },
        ] },
      ],
      later: [
        { id: 'r11', title: '개인 공간과 팀 공간을 나누고 접근 권한을 관리한다', own_status: 'hold', note: '첫 작업 범위 밖. 지금은 프로젝트 구성원과 업무별 열람 제한만 있다' },
        { id: 'r12', title: 'AI마다 읽을 수 있는 자료 범위를 정한다', own_status: 'hold', note: '지금은 자료별 열람자 지정만 있다' },
        { id: 'r13', title: 'Claude·Codex 스킬 허브를 둔다', own_status: 'hold', note: '후순위' },
        { id: 'r14', title: '프로젝트 지식을 업무에 연결한다', own_status: 'hold', note: '후순위' },
        { id: 'r15', title: '일정과 담당자 부재를 관리한다', own_status: 'hold', note: '후순위' },
        { id: 'r19', title: '결정한 뒤에도 버린 대안과 이유를 남기고 프로젝트별 결정 목록을 둔다', own_status: 'hold', note: '조사 메모(2026-11-28)에서 나온 요건. 지금은 선택한 안만 남는다' },
        { id: 'r20', title: '완료·통과 뒤에 실패 기록이 들어오면 다시 확인할 일로 알린다', own_status: 'hold', note: '조사 메모에서 나온 요건. 지금은 완료 조건이 바뀔 때만 이전 기준으로 내려간다' },
        { id: 'r21x', title: '커밋과 시험 결과를 저장소에서 자동으로 읽어 근거로 붙인다', own_status: 'hold', note: '지금은 기록하는 쪽이 커밋 번호와 시험 결과를 직접 적는다' },
        { id: 'r22x', title: 'AI 세션이 끝나거나 압축되기 전에 인계 기록을 자동으로 남긴다', own_status: 'hold', note: '조사 메모에서 나온 요건. 스킬 허브와 함께 검토' },
        { id: 'r23x', title: '열람 제한이 없는 업무를 저장소의 읽기 전용 파일로 내보낸다', own_status: 'hold', note: '저장은 서버에 둔다. 열람 권한을 파일로는 지킬 수 없기 때문' },
        { id: 'r16', title: '테마를 바꾼다(캐릭터 테마 포함)', own_status: 'hold', note: '캐릭터 형태가 배경과 화면 구성에 녹아야 하고 업무 정보의 가독성을 해치지 않아야 한다' },
      ],
    });
    projects[0].milestones.slice(0, 3).forEach((m) => m.requirements.forEach((r) => {
      T({ id: 't' + r.id.slice(1) + '0', project_id: 'p0', requirement_id: r.id, title: r.title,
        status: 'doing', assignee_id: 'ai_claude', next_owner_id: 'ai_verifier',
        next_action: '실제 화면에서 정상 흐름과 영향이 큰 반례를 확인하고 결과를 남긴다',
        goal: projects[0].goal, done_criteria: [r.title],
        current_state: '구현은 끝났다. 구현에 참여하지 않은 검증자의 확인을 기다린다.', updated_at: ago(1) });
    }));
    T({ id: 't100', track: 'check', project_id: 'p0', requirement_id: 'r10', title: '첫 사용자 세 유형이 PC에서 직접 써 보게 한다',
      status: 'blocked', assignee_id: 'u_me', next_owner_id: 'u_me',
      next_action: '비개발자·개발자·AI 도구 첫 사용자를 한 명씩 정해, 목적만 알려 주고 PC에서 쓰게 한 뒤 첫 클릭과 헤맨 지점을 기록한다',
      blocked_reason: '실제 참가자를 확보하지 못했다. AI가 사용자 역할을 대신한 결과는 세지 않는다',
      goal: '설명 없이 쓸 수 있는지 실제 사람으로 확인한다',
      done_criteria: ['세 유형 모두 도움 없이 이어받을 일과 근거를 찾고 다음 할 일을 말했다', '첫 클릭·헤맨 지점·잘못 이해한 정보·도움 요청 여부가 기록되어 있다'],
      current_state: '미검증. 참가자가 정해지면 다시 시작한다.', updated_at: ago(1) });

    const records = [];
    // proof: 다른 사람이 다시 확인할 수 있는 근거(커밋 번호, 시험 실행 결과). 없으면 서술만 있는 기록이다.
    const R = (id, task_id, author_id, kind, outcome, body, evidence, v, h, proof) =>
      records.push({ id, task_id, author_id, kind, outcome, body, evidence: evidence || '', proof: proof || null, criteria_version: v, at: ago(h) });
    R('c20', 't1', 'ai_claude', 'impl', 'pass', '발송 시각 계산과 취소 예약 제외를 구현했다.', '시험 예약 40건 발송 기록', 1, 260);
    R('c21', 't1', 'ai_codex', 'func', 'pass', '두 시각 모두 한 번씩만 발송됐고 취소 예약에는 발송되지 않았다.', '시험 예약 40건 중 40건 정상, 취소 예약 6건 미발송 확인', 1, 250, { commit: '4be19a2', test_passed: 46, test_total: 46 });
    R('c22', 't1', 'u_new', 'field', 'pass', '강남점 실제 예약으로 3일간 확인했다.', '실제 예약 118건, 누락 0건', 1, 220);
    R('c23', 't1', 'u_dev', 'deploy', 'pass', '전 매장 운영에 배포했다.', '배포 기록 2026-11-19', 1, 200);
    R('c24', 't2', 'ai_claude', 'impl', 'pass', '알림톡 실패 응답을 받으면 문자로 다시 보내도록 구현했다.', '실패 응답 시험 15건', 1, 190);
    R('c25', 't2', 'ai_codex', 'func', 'pass', '실패 후 평균 22초 안에 문자가 발송됐고 중복 발송은 없었다.', '시험 15건 중 15건 정상', 1, 180, { commit: '9c03d7e', test_passed: 15, test_total: 15 });
    R('c26', 't2', 'u_new', 'field', 'pass', '알림톡 차단 고객 5명에게 실제로 문자가 도착했다.', '고객 5명 수신 확인', 1, 165);
    R('c27', 't2', 'u_dev', 'deploy', 'pass', '전 매장 운영에 배포했다.', '배포 기록 2026-11-22', 1, 150);
    R('c1', 't3', 'ai_claude', 'impl', 'pass', '변경 링크로 예약 시간을 바꾸는 흐름을 구현했다.', '변경 요청 시험 12건', 1, 50);
    R('c2', 't3', 'ai_codex', 'func', 'pass', '링크로 변경하고 확인 문자를 받는 흐름이 정상이다.', '시험 예약 8건 중 8건 변경 성공', 1, 46);
    R('c3', 't3', 'u_me', 'edit', null, '완료 조건을 바꿨다(기준 v1 → v2). 추가: 링크는 한 번만 쓸 수 있고 24시간 뒤 만료된다. 보안 검토 지적 반영.', '', 2, 30);
    R('c4', 't3', 'ai_claude', 'impl', 'pass', '링크 1회 사용과 24시간 만료를 추가로 구현했다.', '만료·재사용 시험 6건', 2, 20, { commit: 'e71f0b4', test_passed: null, test_total: null });
    R('c5', 't3', 'ai_codex', 'func', 'fail', '만료된 링크로 예약이 변경된다. 1회 사용 조건은 통과했다.', '만료 후 25시간 지난 링크 3건 중 3건이 동작(모두 거절되어야 정상). 사용한 링크 재사용 4건은 모두 거절됨.', 2, 5, { commit: 'e71f0b4', test_passed: 4, test_total: 7 });
    R('c6', 't4', 'ai_claude', 'note', null, '안내문 초안 v3을 작성했다. 점주가 자주 묻는 질문 3가지에 대한 답을 넣었다.', '', 1, 6);
    R('c7', 't4', 'u_jisu1', 'note', null, '문구와 레이아웃 검수 통과. 수정할 곳 없음.', '검수본: 안내문 초안 v3', 1, 3);
    R('c8', 't4', 'ai_claude', 'handoff', null, '한서윤 님에게 인계한다. 점주에게 직접 연락하는 일이라 발송은 사람이 맡는다.', '', 1, 2);
    R('c9', 't5', 'ai_claude', 'note', null, '매장별 위약금 기준을 조사했다. 기준이 통일되어 있지 않아 안내 금액을 정할 수 없다. 결정을 요청한다.', '14개 매장 조사표', 1, 9);
    R('c12', 't13', 'u_dev', 'handoff', null, '이하람 님에게 인계한다. 두 점주 모두 12월 둘째 주가 가능하다고 답했다. 날짜 확정 통화만 남았다.', '11월 26일 통화 메모', 1, 8);
    R('c13', 't10', 'u_jisu2', 'handoff', null, '한서윤 님에게 인계한다. 검수표 초안 7개 항목을 올렸다. 자동으로 바꿀 항목을 골라 주면 된다.', '검수표 초안', 1, 20);
    R('c14', 't12', 'ai_codex', 'note', null, '5단계 중 3단계(데이터 불러오기, 환불 차감, 부분취소 차감)를 구현했다.', '10월 데이터로 3단계까지 수작업 값과 일치', 1, 3);
    R('c15', 't20', 'ai_codex', 'impl', 'pass', '매일 05시 30분 수집을 구현했다.', '7일 연속 수집 기록', 1, 330);
    R('c16', 't20', 'u_dev', 'func', 'pass', '7일간 누락 없이 06시 전에 들어왔다.', '수집 기록 7일분', 1, 320);
    R('c17', 't20', 'u_jisu2', 'field', 'pass', '재무팀이 실제 마감에 사용했다.', '10월 마감 사용', 1, 310);
    R('c18', 't20', 'u_dev', 'deploy', 'pass', '운영에 배포했다.', '배포 기록 2026-11-12', 1, 300);
    R('c19', 't11', 'u_dev', 'handoff', null, '김지수(재무팀) 님에게 인계한다. 예외 6건 목록을 첨부했다.', '', 1, 12);

    Object.values(tasks).filter((t) => t.project_id === 'p0' && t.id !== 't100').forEach((t, i) =>
      R('c9' + i, t.id, 'ai_claude', 'impl', 'pass', '구현했다. 구현자의 자체 확인은 참고 자료이며 독립 검증을 대신하지 않는다.', '자체 규칙 시험(test/core.test.js)', 1, 1, { commit: '2ec355d', test_passed: 13, test_total: 13 }));
    const materials = [
      { id: 'mt1', task_id: 't4', title: '점주 사전 인터뷰 요약', kind: '문서', summary: '점주 5명 인터뷰. 가장 많은 질문: 고객이 바꾼 예약은 어디서 보나, 변경 마감은 언제인가, 잘못 바꾸면 되돌릴 수 있나.', allowed: null },
      { id: 'mt2', task_id: 't4', title: '시범 매장 점주 연락처', kind: '연락처', summary: '강남점·판교점 점주 연락처.', allowed: ['u_me', 'u_dev', 'u_new'] },
      { id: 'mt3', task_id: 't4', title: '안내문 초안 v3', kind: '문서', summary: '점주용 1쪽 안내문. 변경 링크가 무엇인지, 고객이 바꾸면 매장에서 무엇이 보이는지 설명.', allowed: null },
      { id: 'mt4', task_id: 't5', title: '매장별 위약금 기준 조사표', kind: '표', summary: '14개 매장 중 11곳은 본사 표준(당일 취소 30%), 3곳은 자체 기준(10~50%).', allowed: null },
      { id: 'mt5', task_id: 't10', title: '10월 이중 집계 사례', kind: '표', summary: '부분취소 4건이 환불과 부분취소에 각각 한 번씩 집계됨. 차액 합계 312,000원.', allowed: null },
      { id: 'mt6', task_id: 't11', title: '11월 급여성 예외 6건', kind: '표', summary: '급여성 지급 예외 6건의 대상과 금액.', allowed: ['u_jisu2', 'u_dev'] },
      { id: 'mt7', task_id: 't13', title: '시범 매장 점주 연락처', kind: '연락처', summary: '강남점·판교점 점주 연락처.', allowed: ['u_me', 'u_dev', 'u_new'] },
    ];

    const agents = [
      { id: 'ai_claude', task_id: 't3', last_result: '만료 검사 수정 중. 시험 6건 중 4건 통과.', last_result_at: ago(0.7), last_polled_at: ago(0.04), connected: true, reachable: true },
      { id: 'ai_codex', task_id: 't12', last_result: '리포트 초안 5단계 중 3단계 통과.', last_result_at: ago(3), last_polled_at: ago(0.07), connected: false, reachable: false },
    ];

    const changes = [
      { id: 'g1', project_id: 'p1', task_id: 't3', text: '독립 기능 검증 실패(현재 기준 v2)', actor_id: 'ai_codex', at: ago(5) },
      { id: 'g2', project_id: 'p1', task_id: 't4', text: '한서윤 님에게 인계', actor_id: 'ai_claude', at: ago(2) },
      { id: 'g3', project_id: 'p1', task_id: 't5', text: '막힘: 결정 요청', actor_id: 'ai_claude', at: ago(9) },
      { id: 'g4', project_id: 'p1', task_id: 't13', text: '이하람 님에게 인계', actor_id: 'u_dev', at: ago(8) },
      { id: 'g5', project_id: 'p2', task_id: 't10', text: '한서윤 님에게 인계', actor_id: 'u_jisu2', at: ago(20) },
      { id: 'g6', project_id: 'p2', task_id: 't12', text: 'Codex 연결 끊김. 현재 상태 확인 안 됨', actor_id: 'ai_codex', at: ago(0.07) },
      { id: 'g7', project_id: 'p2', task_id: 't11', text: '김지수(재무팀) 님에게 인계', actor_id: 'u_dev', at: ago(12) },
    ];

    const seen = {};
    Object.keys(users).forEach((id) => { seen[id] = ago(24); });
    return { users, projects, tasks, records, materials, agents, changes, seen, idem: {}, seq: 100 };
  }

  /* ───────────── 조회 도우미 ───────────── */
  const has = (o, k) => !!o && typeof k === 'string' && Object.prototype.hasOwnProperty.call(o, k);
  const txt = (v) => (typeof v === 'string' ? v.trim() : '');
  const err = (status, message, extra) => ({ status, body: Object.assign({ error: message }, extra || {}) });
  const ok = (body) => ({ status: 200, body });

  function pubUser(state, id) {
    const u = state.users[id];
    if (!u) return null;
    return { id: u.id, name: u.name, kind: u.kind, team: u.team };
  }
  const projectOf = (state, id) => state.projects.find((p) => p.id === id);
  const isMember = (state, pid, uid) => { const p = projectOf(state, pid); return !!p && p.members.includes(uid); };
  // 열람 권한: 프로젝트 구성원이면서, 제한 업무라면 지정된 사람만. 이름이 아니라 id로만 판단한다.
  function canSee(state, task, uid) {
    return !!task && isMember(state, task.project_id, uid) && (!task.restricted || task.restricted.includes(uid));
  }
  function findReq(state, rid) {
    for (const p of state.projects) {
      for (const m of p.milestones) for (const r of m.requirements) if (r.id === rid) return { project: p, milestone: m, req: r };
      for (const r of p.later) if (r.id === rid) return { project: p, milestone: null, req: r };
    }
    return null;
  }
  const tasksOfReq = (state, rid) => Object.values(state.tasks).filter((t) => t.requirement_id === rid);
  function reqStatus(state, req) {
    const ts = tasksOfReq(state, req.id);
    if (!ts.length) return req.own_status || 'todo';
    if (ts.every((t) => t.status === 'done')) return 'done';
    if (ts.some((t) => t.status === 'blocked')) return 'blocked';
    if (ts.every((t) => t.status === 'hold')) return 'hold';
    if (ts.some((t) => t.status === 'doing' || t.status === 'done')) return 'doing';
    return 'todo';
  }

  function proofView(pr) {
    if (!pr) return null;
    const out = {};
    if (pr.commit) out.commit = pr.commit;
    if (pr.test_total != null) { out.test_passed = pr.test_passed; out.test_total = pr.test_total; }
    return Object.keys(out).length ? out : null;
  }
  // 근거 수준: 다시 확인할 수 있는 근거가 있으면 checkable, 글로 적은 것뿐이면 stated, 아무것도 없으면 none.
  const evidenceLevel = (r) => (proofView(r.proof) ? 'checkable' : r.evidence ? 'stated' : 'none');

  // 만드는 업무는 네 단계를 모두 쓴다. 전달·조율 같은 업무는 다른 사람의 확인 한 단계만 쓴다.
  const stageDefs = (task) => (task.track === 'check' ? [Object.assign({}, STAGES[1], { label: '다른 사람의 확인', pass: '확인됨', fail: '되돌려 보냄', none: '확인 전' })] : STAGES);
  const kindLabel = (task, kind) => { const d = stageDefs(task).find((x) => x.key === kind); return d ? (kind === 'impl' ? KINDS.impl : d.label) : KINDS[kind]; };
  function stagesOf(state, task) {
    const recs = state.records.filter((r) => r.task_id === task.id);
    return stageDefs(task).map((s) => {
      const mine = recs.filter((r) => r.kind === s.key).sort((a, b) => (a.at < b.at ? 1 : -1));
      const cur = mine.find((r) => r.criteria_version === task.criteria_version) || null;
      const prev = mine.find((r) => r.criteria_version < task.criteria_version) || null;
      const st = cur ? cur.outcome : 'none';
      return {
        key: s.key, label: s.label, state: st, text: s[st],
        current: cur ? { record_id: cur.id, at: cur.at, by: pubUser(state, cur.author_id), evidence: cur.evidence, proof: proofView(cur.proof), evidence_level: evidenceLevel(cur) } : null,
        previous: prev ? { record_id: prev.id, at: prev.at, version: prev.criteria_version, text: s[prev.outcome], outcome: prev.outcome } : null,
      };
    });
  }

  function taskSummary(state, t) {
    const fr = findReq(state, t.requirement_id);
    return {
      id: t.id, title: t.title, status: t.status,
      project: { id: fr.project.id, name: fr.project.name },
      milestone: fr.milestone ? { id: fr.milestone.id, title: fr.milestone.title } : null,
      requirement: { id: fr.req.id, title: fr.req.title },
      assignee: pubUser(state, t.assignee_id), next_owner: pubUser(state, t.next_owner_id),
      next_action: t.next_action, blocked_reason: t.blocked_reason, current_state: t.current_state,
      handoff_from: pubUser(state, t.handoff_from), handed_at: t.handed_at, updated_at: t.updated_at,
      criteria_version: t.criteria_version,
      stages: stagesOf(state, t).map((s) => ({ key: s.key, label: s.label, state: s.state, text: s.text, previous: s.previous && { version: s.previous.version, text: s.previous.text } })),
      decision_open: !!(t.decision && !t.decision.chosen), track: t.track,
      failing: stagesOf(state, t).filter((x) => x.state === 'fail').map((x) => `${x.label} ${x.text}`),
    };
  }

  function taskDetail(state, t, uid) {
    const base = taskSummary(state, t);
    const d = t.decision;
    return Object.assign(base, {
      goal: t.goal, done_criteria: t.done_criteria.slice(), rev: t.rev,
      criteria_log: t.criteria_log.map((c) => ({ version: c.version, items: c.items.slice(), at: c.at, by: pubUser(state, c.by) })),
      rationale: t.rationale.map((r) => {
        // 근거가 가리키는 자료를 볼 수 없으면 연결만 뺀다.
        let ref = r.ref || null;
        if (ref && ref.type === 'material') {
          const m = state.materials.find((x) => x.id === ref.id);
          if (!m || (m.allowed && !m.allowed.includes(uid))) ref = null;
        }
        return { text: r.text, ref };
      }),
      materials: state.materials.filter((m) => m.task_id === t.id && (!m.allowed || m.allowed.includes(uid))).map((m) => ({
        id: m.id, title: m.title, kind: m.kind, summary: m.summary,
        readers: m.allowed ? m.allowed.map((id) => pubUser(state, id)) : null,
      })),
      decision: d ? {
        question: d.question, why: d.why, owner: pubUser(state, d.owner_id), requested_by: pubUser(state, d.requested_by),
        requested_at: d.requested_at, recommended: d.recommended, chosen: d.chosen, chosen_at: d.chosen_at,
        options: d.options.map((o) => ({ id: o.id, label: o.label, effect: o.effect, next_action: o.next_action, next_owner: pubUser(state, o.next_owner_id) })),
        can_decide: d.owner_id === uid && !d.chosen,
      } : null,
      stages: stagesOf(state, t),
      records: state.records.filter((r) => r.task_id === t.id).sort((a, b) => (a.at < b.at ? 1 : -1)).map((r) => ({
        id: r.id, kind: r.kind, kind_label: kindLabel(t, r.kind), is_stage: stageDefs(t).some((x) => x.key === r.kind), outcome: r.outcome, body: r.body, evidence: r.evidence, proof: proofView(r.proof), evidence_level: evidenceLevel(r),
        criteria_version: r.criteria_version, is_current_criteria: r.criteria_version === t.criteria_version,
        at: r.at, author: pubUser(state, r.author_id),
      })),
      members: projectOf(state, t.project_id).members.filter((id) => !t.restricted || t.restricted.includes(id)).map((id) => pubUser(state, id)),
      restricted: !!t.restricted,
    });
  }

  function progressOf(state, p) {
    let done = 0, total = 0;
    const milestones = p.milestones.map((m) => {
      const sts = m.requirements.map((r) => reqStatus(state, r));
      const d = sts.filter((s) => s === 'done').length;
      done += d; total += sts.length;
      return { id: m.id, title: m.title, done: d, total: sts.length };
    });
    let seen = false;
    milestones.forEach((m) => {
      if (m.done === m.total) m.state = 'done';
      else if (!seen) { m.state = 'current'; seen = true; } else m.state = 'upcoming';
    });
    return { done, total, milestones, later_count: p.later.length };
  }

  /* ───────────── 화면별 조회 ───────────── */
  function home(state, uid) {
    const visible = Object.values(state.tasks).filter((t) => canSee(state, t, uid));
    const decisions = visible.filter((t) => t.decision && !t.decision.chosen && t.decision.owner_id === uid);
    const decIds = new Set(decisions.map((t) => t.id));
    const handoffs = visible.filter((t) => t.next_owner_id === uid && t.status !== 'done' && !decIds.has(t.id))
      .sort((a, b) => ((a.handed_at || a.updated_at) < (b.handed_at || b.updated_at) ? 1 : -1));
    const projects = state.projects.filter((p) => p.members.includes(uid)).map((p) => {
      const reqs = p.milestones.flatMap((m) => m.requirements);
      return {
        id: p.id, name: p.name, goal: p.goal, start: p.start, finish: p.finish, progress: progressOf(state, p),
        blocked: reqs.filter((r) => reqStatus(state, r) === 'blocked').length,
        doing: reqs.filter((r) => reqStatus(state, r) === 'doing').map((r) => r.title),
        changes: state.changes.filter((c) => c.project_id === p.id && canSee(state, state.tasks[c.task_id], uid))
          .sort((a, b) => (a.at < b.at ? 1 : -1)).slice(0, 3)
          .map((c) => ({ id: c.id, task_id: c.task_id, task_title: state.tasks[c.task_id].title, text: c.text, actor: pubUser(state, c.actor_id), at: c.at })),
      };
    });
    const seenAt = (state.seen && state.seen[uid]) || null;
    const feed = state.changes.filter((c) => canSee(state, state.tasks[c.task_id], uid) && c.actor_id !== uid)
      .sort((a, b) => (a.at < b.at ? 1 : -1));
    const fresh = seenAt ? feed.filter((c) => c.at > seenAt) : feed.slice(0, 5);
    const since = {
      at: seenAt, total: fresh.length,
      until: fresh.length ? fresh[0].at : seenAt,
      changes: fresh.slice(0, 100).map((c) => ({ id: c.id, task_id: c.task_id, task_title: state.tasks[c.task_id].title,
        project: { id: c.project_id, name: projectOf(state, c.project_id).name }, text: c.text, actor: pubUser(state, c.actor_id), at: c.at })),
    };
    return ok({
      me: pubUser(state, uid), since,
      decisions: decisions.map((t) => Object.assign(taskSummary(state, t), {
        question: t.decision.question, why: t.decision.why, option_count: t.decision.options.length,
        requested_by: pubUser(state, t.decision.requested_by), requested_at: t.decision.requested_at,
      })),
      handoffs: handoffs.map((t) => taskSummary(state, t)),
      projects,
    });
  }

  function projectView(state, uid, pid) {
    const p = projectOf(state, pid);
    if (!p || !p.members.includes(uid)) return err(404, '프로젝트를 찾을 수 없습니다.');
    const reqView = (r) => {
      const ts = tasksOfReq(state, r.id).filter((t) => canSee(state, t, uid));
      // 요건 상태는 구성원 모두에게 사실대로 보인다. 볼 수 없는 업무는 내용 없이 건수만 알린다.
      return { id: r.id, title: r.title, status: reqStatus(state, r), note: r.note || '', tasks: ts.map((t) => taskSummary(state, t)), hidden_tasks: tasksOfReq(state, r.id).length - ts.length };
    };
    return ok({
      id: p.id, name: p.name, goal: p.goal, start: p.start, finish: p.finish, progress: progressOf(state, p),
      members: p.members.map((id) => pubUser(state, id)),
      milestones: p.milestones.map((m) => ({ id: m.id, title: m.title, requirements: m.requirements.map(reqView) })),
      later: p.later.map(reqView),
    });
  }

  function search(state, uid, q) {
    q = String(q || '').trim().toLowerCase();
    if (!q) return ok({ q: '', results: [] });
    const snip = (text) => {
      const i = text.toLowerCase().indexOf(q);
      if (i < 0) return null;
      const s = Math.max(0, i - 24);
      return (s > 0 ? '…' : '') + text.slice(s, i + q.length + 40) + (i + q.length + 40 < text.length ? '…' : '');
    };
    const results = [];
    Object.values(state.tasks).filter((t) => canSee(state, t, uid)).forEach((t) => {
      const hits = [];
      const fr = findReq(state, t.requirement_id);
      [['제목', t.title], ['원래 요구사항', fr.req.title], ['다음 할 일', t.next_action], ['현재 상태', t.current_state], ['막힌 이유', t.blocked_reason], ['완료 조건', t.done_criteria.join(' / ')]]
        .forEach(([label, text]) => { const s = text && snip(text); if (s) hits.push({ where: label, text: s }); });
      state.records.filter((r) => r.task_id === t.id).forEach((r) => {
        const s = snip(r.body + ' ' + r.evidence);
        if (s) hits.push({ where: KINDS[r.kind], text: s });
      });
      if (hits.length) {
        results.push(Object.assign(taskSummary(state, t), {
          hits: hits.slice(0, 3),
          verify_count: state.records.filter((r) => r.task_id === t.id && ['impl', 'func', 'field', 'deploy'].includes(r.kind)).length,
        }));
      }
    });
    return ok({ q, results });
  }

  function agentsView(state, uid) {
    return ok({
      agents: state.agents.filter((a) => canSee(state, state.tasks[a.task_id], uid)).map((a) => ({
        agent: pubUser(state, a.id), task: taskSummary(state, state.tasks[a.task_id]),
        connected: a.connected, state_text: a.connected ? '연결됨' : '확인 안 됨',
        last_result: a.last_result, last_result_at: a.last_result_at, last_polled_at: a.last_polled_at,
      })),
    });
  }

  /* ───────────── 변경 ───────────── */
  const nextId = (state, prefix) => prefix + (++state.seq);
  function logChange(state, task, text, actor, now) {
    state.changes.push({ id: nextId(state, 'g'), project_id: task.project_id, task_id: task.id, text, actor_id: actor, at: now });
  }
  const nameOf = (state, id) => { const u = state.users[id]; return u ? `${u.name}(${u.team})` : '없음'; };

  function addRecord(state, uid, task, body, now) {
    if (!body || typeof body !== 'object') return err(422, '기록 내용이 올바르지 않습니다.');
    const kind = body.kind;
    const text = txt(body.body);
    const evidence = txt(body.evidence);
    let outcome = body.outcome;
    const me = state.users[uid];
    const defs = stageDefs(task);
    if (kind !== 'note' && !defs.some((d) => d.key === kind)) return err(422, '기록 종류를 선택해 주세요.');
    // 검증 결과는 기록하는 사람이 본 완료 조건 기준으로만 남긴다.
    if (kind !== 'note' && body.criteria_version !== task.criteria_version) {
      return err(409, `완료 조건이 바뀌었습니다(지금 기준 v${task.criteria_version}). 새 완료 조건을 확인한 뒤 다시 저장해 주세요.`, { task: taskDetail(state, task, uid) });
    }
    if (!text) return err(422, '내용을 입력해 주세요.');
    if (text.length > 2000 || evidence.length > 2000) return err(422, '내용과 근거는 각각 2,000자까지 입력할 수 있습니다.');
    if (kind === 'note') outcome = null;
    else if (kind === 'impl') outcome = 'pass';
    else if (!['pass', 'fail'].includes(outcome)) return err(422, '결과(통과 또는 실패)를 선택해 주세요.');
    let proof = null;
    const pin = body.proof && typeof body.proof === 'object' ? body.proof : {};
    const commit = txt(pin.commit).toLowerCase();
    const given = (v) => v !== undefined && v !== null && v !== '';
    const hasTest = given(pin.test_total) || given(pin.test_passed);
    if (commit && !/^[0-9a-f]{7,40}$/.test(commit)) return err(422, '커밋 번호는 7~40자의 16진수로 적어 주세요. 예: 2ec355d');
    if (hasTest) {
      const tp = pin.test_passed, tt = pin.test_total;
      if (typeof tp !== 'number' || typeof tt !== 'number' || !Number.isInteger(tp) || !Number.isInteger(tt) || tt < 1 || tp < 0 || tp > tt || tt > 1e6) return err(422, '시험 결과는 통과한 건수와 전체 건수를 숫자로 적어 주세요. 통과한 건수는 전체 건수보다 클 수 없습니다.');
      if (outcome === 'pass' && tp < tt) return err(422, `시험 ${tt}건 중 ${tp}건만 통과했습니다. 모두 통과하지 않았으면 결과를 통과로 남길 수 없습니다.`);
      proof = { commit: commit || null, test_passed: tp, test_total: tt };
    } else if (commit) proof = { commit, test_passed: null, test_total: null };
    if (['func', 'field'].includes(kind) && !evidence && !proof) return err(422, '검증 결과에는 확인한 근거를 적어 주세요.');
    if (kind === 'func') {
      const built = task.assignee_id === uid || state.records.some((r) => r.task_id === task.id && r.kind === 'impl' && r.author_id === uid);
      if (built) return err(422, '이 업무를 구현한 담당자는 독립 기능 검증 결과를 남길 수 없습니다. 구현에 참여하지 않은 검증자가 남겨야 합니다.');
    }
    if (kind === 'field' && me.kind === 'ai') return err(422, '실사용 검증은 실제 사용자가 확인한 결과만 남길 수 있습니다.');
    if (kind === 'deploy' && me.kind === 'ai') return err(422, '운영 배포 결과는 사람이 남겨야 합니다.');
    // 결과를 남기면서 다음 사람에게 넘길 수 있다.
    let ho = null;
    if (body.handoff != null) {
      if (typeof body.handoff !== 'object') return err(422, '넘길 내용이 올바르지 않습니다.');
      const to = body.handoff.next_owner_id, act = txt(body.handoff.next_action);
      if (!has(state.users, to) || !canSee(state, task, to)) return err(422, '이 업무를 볼 수 있는 구성원에게만 넘길 수 있습니다.');
      if (!act) return err(422, '넘겨받는 사람이 할 일을 적어 주세요.');
      if (act.length > 500) return err(422, '다음 할 일은 500자까지 입력할 수 있습니다.');
      ho = { to, act };
    }
    const rec = { id: nextId(state, 'c'), task_id: task.id, author_id: uid, kind, outcome, body: text, evidence, proof, criteria_version: task.criteria_version, at: now };
    state.records.push(rec);
    task.updated_at = now; task.rev += 1;
    const stage = defs.find((s) => s.key === kind);
    // 완료된 업무에 현재 기준의 검증 실패가 들어오면 완료를 풀고 담당자에게 돌려보낸다.
    let reopened = false;
    if (task.status === 'done' && ['func', 'field', 'deploy'].includes(kind) && outcome === 'fail') {
      reopened = true;
      task.status = 'doing'; task.next_owner_id = task.assignee_id;
      task.next_action = `${stage.label} 실패 원인을 확인하고 고친 뒤 다시 검증을 요청한다`;
      task.current_state = `완료 후 ${stage.label}에서 실패해 다시 열렸다.`;
      task.handoff_from = uid; task.handed_at = now;
    }
    if (ho && !reopened) {
      task.next_owner_id = ho.to; task.next_action = ho.act; task.handoff_from = uid; task.handed_at = now;
      if (task.status === 'todo') task.status = 'doing';
    }
    logChange(state, task, (stage ? `${stage.label}: ${stage[outcome]}` : `기록: ${text.slice(0, 30)}`) + (reopened ? '. 완료가 풀림' : '') + (ho && !reopened ? `. ${state.users[ho.to].name} 님에게 인계` : ''), uid, now);
    const ag = state.agents.find((a) => a.id === uid && a.task_id === task.id);
    if (ag) { ag.last_result = text; ag.last_result_at = now; ag.last_polled_at = now; ag.connected = true; }
    return ok({
      saved: { record_id: rec.id, summary: (stage ? `${stage.label} 결과가 '${stage[outcome]}'(으)로 기록되었습니다.` : '기록 1건이 추가되었습니다.') + (reopened ? ' 업무가 완료에서 진행 중으로 돌아갔습니다.' : '') + (ho && !reopened ? ` ${nameOf(state, ho.to)} 님에게 넘겼습니다.` : '') },
      task: taskDetail(state, task, uid),
    });
  }

  function editTask(state, uid, task, body, now) {
    if (!body || body.rev !== task.rev) {
      return err(409, '다른 사람이 먼저 이 업무를 바꿨습니다. 최신 내용을 확인한 뒤 다시 수정해 주세요.', { task: taskDetail(state, task, uid) });
    }
    const fields = body.fields;
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return err(422, '수정할 항목이 올바르지 않습니다.');
    const patch = {};
    for (const k of Object.keys(EDITABLE)) if (has(fields, k)) patch[k] = fields[k];
    const str = txt;
    const next = Object.assign({}, task);
    if ('title' in patch) { next.title = str(patch.title); if (!next.title) return err(422, '제목을 입력해 주세요.'); if (next.title.length > 200) return err(422, '제목은 200자까지 입력할 수 있습니다.'); }
    if ('status' in patch) { if (!has(STATUS, patch.status)) return err(422, '상태를 선택해 주세요.'); next.status = patch.status; }
    if ('next_owner_id' in patch) {
      const id = patch.next_owner_id || null;
      if (id && !has(state.users, id)) return err(422, '다음 담당자를 찾을 수 없습니다.');
      if (id && !(isMember(state, task.project_id, id) && (!task.restricted || task.restricted.includes(id)))) return err(422, '이 업무를 볼 수 있는 구성원만 다음 담당자로 지정할 수 있습니다.');
      next.next_owner_id = id;
    }
    if ('next_action' in patch) next.next_action = str(patch.next_action);
    if ('blocked_reason' in patch) next.blocked_reason = str(patch.blocked_reason);
    if ('current_state' in patch) next.current_state = str(patch.current_state);
    if ('done_criteria' in patch) {
      if (!Array.isArray(patch.done_criteria)) return err(422, '완료 조건 형식이 올바르지 않습니다.');
      next.done_criteria = patch.done_criteria.map(str).filter(Boolean);
      if (!next.done_criteria.length) return err(422, '완료 조건을 한 줄 이상 적어 주세요.');
    }
    if (['blocked', 'hold'].includes(next.status) && !next.blocked_reason) return err(422, `${STATUS[next.status]} 상태에는 이유를 적어 주세요.`);
    if (!['blocked', 'hold'].includes(next.status)) next.blocked_reason = '';
    if (next.status !== 'done' && !next.next_action) return err(422, '다음 할 일을 적어 주세요.');
    const criteriaChanged = JSON.stringify(next.done_criteria) !== JSON.stringify(task.done_criteria);
    if (next.status === 'done' && (task.status !== 'done' || criteriaChanged)) {
      if (criteriaChanged) return err(422, '완료 조건을 바꾸면 새 기준으로 다시 검증해야 합니다. 상태를 진행 중으로 함께 바꿔 주세요.');
      const st = stagesOf(state, task), func = st.find((s) => s.key === 'func'), bad = st.find((s) => s.state === 'fail');
      if (bad) return err(422, `현재 기준에서 ‘${bad.label}’ 결과가 ‘${bad.text}’입니다. 이 결과가 바뀌기 전에는 완료로 바꿀 수 없습니다.`);
      if (func.state !== 'pass') return err(422, `‘${func.label}’이(가) 끝나야 완료로 바꿀 수 있습니다. 담당자가 아닌 사람이 결과를 남겨야 합니다.`);
    }
    const show = (k, v) => (k === 'status' ? STATUS[v] : k === 'next_owner_id' ? nameOf(state, v) : k === 'done_criteria' ? v.join(' / ') : v || '(비어 있음)');
    const changes = [];
    for (const k of Object.keys(EDITABLE)) {
      const label = k === 'blocked_reason' && (next.status === 'hold' || (task.status === 'hold' && next.status !== 'blocked')) ? '보류 이유' : EDITABLE[k];
      if (JSON.stringify(next[k]) !== JSON.stringify(task[k])) changes.push({ field: k, label, from: show(k, task[k]), to: show(k, next[k]) });
    }
    if (!changes.length) return ok({ saved: { summary: '바뀐 항목이 없어 저장하지 않았습니다.', changes: [] }, task: taskDetail(state, task, uid) });
    if (next.next_owner_id !== task.next_owner_id && next.next_owner_id) { next.handoff_from = uid; next.handed_at = now; }
    if (criteriaChanged) {
      next.criteria_version = task.criteria_version + 1;
      next.criteria_log = task.criteria_log.concat([{ version: next.criteria_version, items: next.done_criteria.slice(), at: now, by: uid }]);
    }
    Object.assign(task, next, { rev: task.rev + 1, updated_at: now });
    state.records.push({
      id: nextId(state, 'c'), task_id: task.id, author_id: uid, kind: 'edit', outcome: null,
      body: changes.map((c) => `${c.label}: ${c.from} → ${c.to}`).join('\n') + (criteriaChanged ? `\n완료 조건이 바뀌어 기준이 v${task.criteria_version}이 되었습니다. 이전 검증 결과는 이전 기준으로 남습니다.` : ''),
      evidence: '', criteria_version: task.criteria_version, at: now,
    });
    const moved = changes.some((c) => c.field === 'next_owner_id') && task.next_owner_id;
    logChange(state, task, moved ? `${state.users[task.next_owner_id].name} 님에게 인계` : changes.map((c) => c.label).join(', ') + ' 변경', uid, now);
    return ok({
      saved: { summary: `${changes.length}개 항목이 반영되었습니다.`, changes, criteria_changed: criteriaChanged },
      task: taskDetail(state, task, uid),
    });
  }

  function decide(state, uid, task, body, now) {
    const d = task.decision;
    if (!d) return err(404, '결정할 항목이 없습니다.');
    if (!body || typeof body !== 'object') return err(422, '선택지를 골라 주세요.');
    if (d.owner_id !== uid) return err(403, '이 결정은 지정된 담당자만 내릴 수 있습니다.');
    if (d.chosen) return err(409, '이미 결정된 항목입니다.', { task: taskDetail(state, task, uid) });
    const note = txt(body.note);
    const opt = d.options.find((o) => o.id === body.option);
    if (!opt) return err(422, '선택지를 골라 주세요.');
    d.chosen = opt.id; d.chosen_at = now;
    task.next_owner_id = opt.next_owner_id; task.next_action = opt.next_action;
    task.handoff_from = uid; task.handed_at = now;
    if (d.unblocks && task.status === 'blocked') { task.status = 'todo'; task.blocked_reason = ''; }
    task.rev += 1; task.updated_at = now;
    state.records.push({ id: nextId(state, 'c'), task_id: task.id, author_id: uid, kind: 'decision', outcome: null, body: `결정: ${opt.label}`, evidence: note, proof: null, criteria_version: task.criteria_version, at: now });
    logChange(state, task, `결정: ${opt.label}`, uid, now);
    return ok({ saved: { summary: '결정이 반영되었습니다.' }, task: taskDetail(state, task, uid) });
  }

  /* ───────────── 진입점 ───────────── */
  function route(state, req, uid, now) {
    const p = req.path.replace(/^\/+|\/+$/g, '').split('/');
    const m = req.method;
    if (m === 'GET' && p[0] === 'users' && p.length === 1) return ok({ users: Object.keys(state.users).map((id) => pubUser(state, id)) });
    if (m === 'GET' && p[0] === 'home') return home(state, uid);
    if (m === 'POST' && p[0] === 'home' && p[1] === 'seen') {
      // 화면에 보인 마지막 변경까지만 확인 처리한다. 그 뒤에 생긴 변경은 남는다.
      const until = txt(req.body && req.body.until);
      if (!until || isNaN(Date.parse(until)) || until > now) return err(422, '확인할 범위가 올바르지 않습니다.');
      state.seen = state.seen || {};
      if (!state.seen[uid] || until > state.seen[uid]) state.seen[uid] = until;
      return home(state, uid);
    }
    if (m === 'GET' && p[0] === 'saves' && p[1]) return ok({ applied: has(state.idem, uid + ':' + p[1]) });
    if (m === 'GET' && p[0] === 'projects' && p[1]) return projectView(state, uid, p[1]);
    if (m === 'GET' && p[0] === 'search') return search(state, uid, req.query && req.query.q);
    if (m === 'GET' && p[0] === 'agents') return agentsView(state, uid);
    if (p[0] === 'agents' && p[2] === 'poll' && m === 'POST') {
      const a = state.agents.find((x) => x.id === p[1]);
      if (!a || !canSee(state, state.tasks[a.task_id], uid)) return err(404, 'AI를 찾을 수 없습니다.');
      a.last_polled_at = now; a.connected = !!a.reachable; // 조회에 실패해도 마지막 결과는 지우지 않는다.
      return agentsView(state, uid);
    }
    if (p[0] === 'demo' && p[1] === 'agent-link' && m === 'POST') {
      const a = state.agents.find((x) => x.id === (req.body && req.body.id));
      if (!a || !canSee(state, state.tasks[a.task_id], uid)) return err(404, 'AI를 찾을 수 없습니다.');
      a.reachable = !!req.body.reachable;
      return ok({ ok: true });
    }
    if (p[0] === 'tasks' && p[1]) {
      const task = has(state.tasks, p[1]) ? state.tasks[p[1]] : null;
      // 볼 수 없는 업무는 없는 업무와 똑같이 응답한다.
      if (!canSee(state, task, uid)) return err(404, '업무를 찾을 수 없습니다.');
      if (m === 'GET' && p.length === 2) return ok({ task: taskDetail(state, task, uid) });
      if (m === 'PATCH' && p.length === 2) return editTask(state, uid, task, req.body, now);
      if (m === 'POST' && p[2] === 'records') return addRecord(state, uid, task, req.body, now);
      if (m === 'POST' && p[2] === 'decision') return decide(state, uid, task, req.body, now);
    }
    return err(404, '요청한 항목을 찾을 수 없습니다.');
  }

  function handle(state, req) {
    const now = new Date(req.now || Date.now()).toISOString();
    const path = req.path.replace(/^\/+|\/+$/g, '');
    const uid = req.userId;
    if (!has(state.users, uid)) return err(401, '사용자를 확인할 수 없습니다.');
    if (req.method === 'POST' && path === 'demo/reset') {
      const fresh = seed(req.now);
      Object.keys(state).forEach((k) => delete state[k]);
      Object.assign(state, fresh);
      return ok({ ok: true });
    }
    if (req.method === 'GET') return route(state, req, uid, now);
    // 저장 요청은 요청 키가 있어야 하고, 같은 키로 다시 오면 처음 결과를 그대로 돌려준다.
    if (!req.idemKey) return err(400, '저장 요청 키가 없습니다.');
    const key = uid + ':' + String(req.idemKey);
    const print = req.method + ' ' + path + ' ' + JSON.stringify(req.body == null ? null : req.body);
    if (has(state.idem, key)) {
      const prev = JSON.parse(state.idem[key]);
      if (prev.print !== print) return err(422, '이 요청 키는 이미 다른 저장에 쓰였습니다. 새 요청 키로 다시 보내 주세요.');
      prev.res.body.replayed = true;
      return prev.res;
    }
    // 처리 도중 예상하지 못한 오류가 나면 절반만 바뀐 채로 두지 않고 요청 전으로 되돌린다.
    const before = JSON.stringify(state);
    let res;
    try { res = route(state, req, uid, now); } catch (e) {
      const old = JSON.parse(before);
      Object.keys(state).forEach((k) => delete state[k]);
      Object.assign(state, old);
      return err(500, '요청을 처리하지 못했습니다. 저장되지 않았습니다.');
    }
    if (res.status === 200) state.idem[key] = JSON.stringify({ print, res });
    return res;
  }

  return { seed, handle, STATUS, KINDS, STAGES, EDITABLE };
});
