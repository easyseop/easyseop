// 구현자의 자체 규칙 시험. 참고 자료이며 독립 검증을 대신하지 않는다.  실행: node --test thread/test
const test = require('node:test');
const assert = require('node:assert');
const core = require('../core.js');

const mk = () => core.seed(Date.parse('2026-11-28T06:00:00Z'));
const get = (s, u, path, query) => core.handle(s, { method: 'GET', path, userId: u, query });
const send = (s, u, method, path, body, key) => core.handle(s, { method, path, userId: u, body, idemKey: key });

test('같은 요청 키로 다시 보내면 기록은 한 건만 남는다', () => {
  const s = mk();
  const before = s.records.length;
  const body = { kind: 'note', body: '점주 2명 수신 확인', evidence: '문자 회신' };
  const a = send(s, 'u_me', 'POST', 'tasks/t4/records', body, 'k1');
  const b = send(s, 'u_me', 'POST', 'tasks/t4/records', body, 'k1');
  assert.equal(a.status, 200); assert.equal(b.status, 200);
  assert.equal(b.body.replayed, true);
  assert.equal(b.body.saved.record_id, a.body.saved.record_id);
  assert.equal(s.records.length, before + 1);
});

test('요청 키가 없는 저장은 받지 않는다', () => {
  assert.equal(send(mk(), 'u_me', 'POST', 'tasks/t4/records', { kind: 'note', body: 'x' }).status, 400);
});

test('열람이 제한된 업무는 구성원에게도 없는 업무처럼 보인다', () => {
  const s = mk();
  assert.equal(get(s, 'u_me', 'tasks/t11').status, 404);
  assert.equal(get(s, 'u_jisu1', 'tasks/t11').status, 404);
  assert.equal(get(s, 'u_jisu2', 'tasks/t11').status, 200);
  assert.equal(send(s, 'u_me', 'POST', 'tasks/t11/records', { kind: 'note', body: 'x' }, 'k').status, 404);
  const all = JSON.stringify([get(s, 'u_me', 'home'), get(s, 'u_me', 'projects/p2'), get(s, 'u_me', 'search', { q: '급여' }), get(s, 'u_me', 'search', { q: '예외' })]);
  assert.ok(!all.includes('급여성 정산 예외 내역'));
  assert.ok(!all.includes('t11'));
});

test('이름이 같은 다른 사람에게 인계 업무가 가지 않는다', () => {
  const s = mk();
  const ids = (u) => get(s, u, 'home').body.handoffs.map((t) => t.id);
  assert.deepEqual(ids('u_jisu1'), ['t8']);
  assert.deepEqual(ids('u_jisu2'), ['t11']);
});

test('볼 수 없는 자료는 목록과 근거 연결에서 빠진다', () => {
  const s = mk();
  const t = get(s, 'u_jisu1', 'tasks/t4').body.task;
  assert.ok(!t.materials.some((m) => m.id === 'mt2'));
  assert.ok(!JSON.stringify(t).includes('점주 연락처.'));
  assert.ok(get(s, 'ai_claude', 'tasks/t4').body.task.materials.every((m) => m.id !== 'mt2'));
  assert.ok(get(s, 'u_me', 'tasks/t4').body.task.materials.some((m) => m.id === 'mt2'));
});

test('이전 기준의 통과는 현재 기준의 통과가 아니다', () => {
  const func = get(mk(), 'u_me', 'tasks/t3').body.task.stages.find((x) => x.key === 'func');
  assert.equal(func.state, 'fail');
  assert.equal(func.previous.outcome, 'pass');
  assert.equal(func.previous.version, 1);
});

test('완료 조건을 바꾸면 기준이 올라가고 이전 검증은 현재 결과에서 빠진다', () => {
  const s = mk();
  const t = get(s, 'u_me', 'tasks/t1').body.task;
  const r = send(s, 'u_me', 'PATCH', 'tasks/t1', { rev: t.rev, fields: { status: 'doing', next_action: '다시 검증한다', done_criteria: ['새 조건'] } }, 'k2');
  assert.equal(r.status, 200);
  assert.equal(r.body.task.criteria_version, 2);
  assert.ok(r.body.task.stages.every((x) => x.state === 'none' && x.previous));
});

test('구현자는 독립 검증을 남길 수 없고, 검증 통과 전에는 완료로 바꿀 수 없다', () => {
  const s = mk();
  const rec = { kind: 'func', outcome: 'pass', body: '통과', evidence: '직접 확인', criteria_version: 2 };
  assert.equal(send(s, 'ai_claude', 'POST', 'tasks/t3/records', rec, 'a').status, 422);
  const t = get(s, 'u_me', 'tasks/t3').body.task;
  assert.equal(send(s, 'u_me', 'PATCH', 'tasks/t3', { rev: t.rev, fields: { status: 'done' } }, 'b').status, 422);
  assert.equal(send(s, 'ai_codex', 'POST', 'tasks/t3/records', rec, 'c').status, 200);
  const t2 = get(s, 'u_me', 'tasks/t3').body.task;
  assert.equal(send(s, 'u_me', 'PATCH', 'tasks/t3', { rev: t2.rev, fields: { status: 'done' } }, 'd').status, 200);
});

test('AI는 실사용 검증과 운영 배포 결과를 남길 수 없다', () => {
  const s = mk();
  assert.equal(send(s, 'ai_codex', 'POST', 'tasks/t3/records', { kind: 'field', outcome: 'pass', body: 'x', evidence: 'y', criteria_version: 2 }, 'a').status, 422);
  assert.equal(send(s, 'ai_codex', 'POST', 'tasks/t3/records', { kind: 'deploy', outcome: 'pass', body: 'x', criteria_version: 2 }, 'b').status, 422);
});

test('다른 사람이 먼저 바꾼 업무는 덮어쓰지 않는다', () => {
  const s = mk();
  const t = get(s, 'u_me', 'tasks/t6').body.task;
  assert.equal(send(s, 'u_dev', 'PATCH', 'tasks/t6', { rev: t.rev, fields: { next_action: 'A' } }, 'a').status, 200);
  assert.equal(send(s, 'u_me', 'PATCH', 'tasks/t6', { rev: t.rev, fields: { next_action: 'B' } }, 'b').status, 409);
  assert.equal(s.tasks.t6.next_action, 'A');
});

test('결정은 지정된 사람만, 한 번만 내린다', () => {
  const s = mk();
  assert.equal(send(s, 'u_dev', 'POST', 'tasks/t5/decision', { option: 'a' }, 'a').status, 403);
  const r = send(s, 'u_me', 'POST', 'tasks/t5/decision', { option: 'a' }, 'b');
  assert.equal(r.status, 200);
  assert.equal(r.body.task.status, 'todo');
  assert.equal(r.body.task.next_owner.id, 'ai_claude');
  assert.equal(send(s, 'u_me', 'POST', 'tasks/t5/decision', { option: 'b' }, 'c').status, 409);
  assert.equal(get(s, 'u_me', 'home').body.decisions.length, 0);
});

test('AI 조회가 실패해도 마지막 결과는 남고 현재 상태만 확인 안 됨이 된다', () => {
  const s = mk();
  send(s, 'u_me', 'POST', 'demo/agent-link', { id: 'ai_claude', reachable: false }, 'a');
  const before = s.agents[0].last_result;
  const a = send(s, 'u_me', 'POST', 'agents/ai_claude/poll', {}, 'b').body.agents.find((x) => x.agent.id === 'ai_claude');
  assert.equal(a.state_text, '확인 안 됨');
  assert.equal(a.last_result, before);
  assert.ok(a.last_polled_at > a.last_result_at);
});

test('진척은 완료한 요건 수로만 센다', () => {
  const p = get(mk(), 'u_me', 'projects/p1').body;
  assert.deepEqual([p.progress.done, p.progress.total], [2, 7]);
  assert.equal(p.later.length, 2);
});

test('같은 요청 키를 다른 내용에 다시 쓰면 받지 않는다', () => {
  const s = mk();
  assert.equal(send(s, 'u_me', 'POST', 'tasks/t4/records', { kind: 'note', body: 'A' }, 'k').status, 200);
  assert.equal(send(s, 'u_me', 'POST', 'tasks/t10/records', { kind: 'note', body: 'B' }, 'k').status, 422);
});

test('잘못된 수정 요청은 저장되지 않고 오류로 답한다', () => {
  const s = mk();
  const rev = s.tasks.t6.rev;
  for (const fields of ['abc', 5, null, [], { status: 'toString' }, { status: 'constructor' }, { next_owner_id: '__proto__' }]) {
    assert.equal(send(s, 'u_me', 'PATCH', 'tasks/t6', { rev, fields }, 'k' + JSON.stringify(fields)).status, 422);
  }
  assert.equal(s.tasks.t6.status, 'todo');
  assert.equal(get(s, '__proto__', 'home').status, 401);
  assert.equal(get(s, 'u_me', 'tasks/constructor').status, 404);
});

test('완료된 업무에 검증 실패가 들어오면 완료가 풀리고 담당자에게 돌아간다', () => {
  const s = mk();
  const r = send(s, 'u_dev', 'POST', 'tasks/t1/records', { kind: 'func', outcome: 'fail', body: '재확인에서 실패', evidence: '2건 누락', criteria_version: 1 }, 'k');
  assert.equal(r.status, 200);
  assert.equal(r.body.task.status, 'doing');
  assert.equal(r.body.task.next_owner.id, 'ai_claude');
  assert.equal(get(s, 'u_me', 'projects/p1').body.progress.done, 1);
});

test('볼 수 없는 업무는 건수만 알리고, 요건 상태는 사실대로 보인다', () => {
  const s = mk();
  s.tasks.t11.status = 'blocked'; s.tasks.t11.blocked_reason = '비밀';
  const p = get(s, 'u_jisu1', 'projects/p2').body;
  const r = p.milestones[1].requirements.find((x) => x.id === 'r24');
  assert.equal(r.status, 'blocked');
  assert.equal(r.hidden_tasks, 1);
  assert.equal(r.tasks.length, 0);
  assert.ok(!JSON.stringify(p).includes('비밀'));
});

test('통과로 남기려면 시험이 모두 통과해야 하고, 근거 수준이 구분된다', () => {
  const s = mk();
  const bad = send(s, 'ai_codex', 'POST', 'tasks/t3/records', { kind: 'func', outcome: 'pass', body: 'x', criteria_version: 2, proof: { test_passed: 6, test_total: 7 } }, 'a');
  assert.equal(bad.status, 422);
  const okr = send(s, 'ai_codex', 'POST', 'tasks/t3/records', { kind: 'func', outcome: 'pass', body: 'x', criteria_version: 2, proof: { commit: 'ABC1234', test_passed: 7, test_total: 7 } }, 'b');
  assert.equal(okr.body.task.stages[1].current.evidence_level, 'checkable');
  assert.equal(get(s, 'u_me', 'tasks/t4').body.task.records.find((r) => r.id === 'c7').evidence_level, 'stated');
});

test('마지막 확인 뒤 바뀐 것은 확인하면 비워지고, 볼 수 없는 업무의 변경은 섞이지 않는다', () => {
  const s = mk();
  const h = get(s, 'u_me', 'home').body;
  assert.ok(h.since.total > 0);
  assert.ok(h.since.changes.every((c) => c.task_id !== 't11'));
  assert.equal(send(s, 'u_me', 'POST', 'home/seen', { until: h.since.until }, 'k').body.since.total, 0);
});

test('AI 연결 시연 기능도 볼 수 있는 사람만 쓴다', () => {
  assert.equal(send(mk(), 'u_new', 'POST', 'demo/agent-link', { id: 'ai_codex', reachable: true }, 'k').status, 404);
});

// ── 2차 독립 검증에서 나온 반례
test('바뀐 완료 조건을 보지 않고 남긴 검증 결과는 받지 않는다', () => {
  const s = mk();
  const rec = { kind: 'func', outcome: 'pass', body: '확인', evidence: '직접 봄', criteria_version: 1 };
  const t = get(s, 'u_me', 'tasks/t4').body.task;
  send(s, 'u_me', 'PATCH', 'tasks/t4', { rev: t.rev, fields: { done_criteria: ['점주 14명 전원이 받았다'] } }, 'a');
  assert.equal(send(s, 'u_dev', 'POST', 'tasks/t4/records', rec, 'b').status, 409);
  assert.equal(get(s, 'u_me', 'tasks/t4').body.task.stages[0].state, 'none');
});

test('현재 기준에서 실패한 단계가 있으면 완료로 바꿀 수 없다', () => {
  const s = mk();
  send(s, 'u_new', 'POST', 'tasks/t1/records', { kind: 'field', outcome: 'fail', body: '누락', evidence: '2건', criteria_version: 1 }, 'a');
  assert.equal(s.tasks.t1.status, 'doing');
  assert.equal(send(s, 'u_me', 'PATCH', 'tasks/t1', { rev: s.tasks.t1.rev, fields: { status: 'done' } }, 'b').status, 422);
  send(s, 'u_dev', 'POST', 'tasks/t2/records', { kind: 'deploy', outcome: 'fail', body: '배포 실패', criteria_version: 1 }, 'c');
  assert.equal(s.tasks.t2.status, 'doing');
});

test('처리 중 오류가 나면 아무것도 바뀌지 않는다', () => {
  const s = mk();
  const before = JSON.stringify(s.tasks.t5);
  const r = send(s, 'u_me', 'POST', 'tasks/t5/decision', { option: 'b', note: { toString: 1 } }, 'a');
  assert.ok(r.status === 200 ? s.records.some((x) => x.task_id === 't5' && x.kind === 'decision') : JSON.stringify(s.tasks.t5) === before);
  for (const body of [null, 5, 'x', [], { kind: 'note', body: { toString: 1 } }, { kind: 'func', outcome: 'pass', body: 'x', evidence: 'y', criteria_version: 1, proof: 'zz' }]) {
    assert.ok(send(s, 'u_me', 'POST', 'tasks/t6/records', body, 'k' + JSON.stringify(body)).status < 500);
  }
});

test('시험 건수는 숫자 두 개가 모두 있어야 하고, 배포도 실패한 시험으로는 통과가 안 된다', () => {
  const s = mk();
  const base = { kind: 'deploy', outcome: 'pass', body: 'x', criteria_version: 2 };
  for (const proof of [{ test_passed: 3 }, { test_passed: true, test_total: true }, { test_passed: '7', test_total: '7' }, { test_passed: [3], test_total: [3] }, { test_passed: 1, test_total: 9 }]) {
    assert.equal(send(s, 'u_dev', 'POST', 'tasks/t3/records', Object.assign({ proof }, base), 'k' + JSON.stringify(proof)).status, 422);
  }
});

test('바뀐 것 목록은 전부 보이고, 확인은 본 데까지만 처리된다', () => {
  const s = mk();
  for (let i = 0; i < 17; i++) send(s, 'u_dev', 'POST', 'tasks/t6/records', { kind: 'note', body: '메모 ' + i }, 'n' + i);
  const h = get(s, 'u_me', 'home').body;
  assert.equal(h.since.changes.length, h.since.total);
  send(s, 'u_dev', 'POST', 'tasks/t6/records', { kind: 'note', body: '그 뒤에 생긴 것' }, 'late');
  s.changes[s.changes.length - 1].at = new Date(Date.parse(h.since.until) + 1000).toISOString();
  assert.equal(send(s, 'u_me', 'POST', 'home/seen', { until: h.since.until }, 'seen').body.since.total, 1);
});

test('결과를 남기면서 다음 사람에게 넘길 수 있고, 볼 수 없는 사람에게는 못 넘긴다', () => {
  const s = mk();
  const r = send(s, 'u_me', 'POST', 'tasks/t4/records', { kind: 'note', body: '보냄', handoff: { next_owner_id: 'u_dev', next_action: '수신 확인을 검토한다' } }, 'a');
  assert.equal(r.body.task.next_owner.id, 'u_dev');
  assert.deepEqual(get(s, 'u_me', 'home').body.handoffs.map((t) => t.id).includes('t4'), false);
  assert.ok(get(s, 'u_dev', 'home').body.handoffs.some((t) => t.id === 't4'));
  assert.equal(send(s, 'u_jisu2', 'POST', 'tasks/t11/records', { kind: 'note', body: 'x', handoff: { next_owner_id: 'u_me', next_action: 'y' } }, 'b').status, 422);
});

test('저장이 반영됐는지 요청 키로 물을 수 있다', () => {
  const s = mk();
  assert.equal(get(s, 'u_me', 'saves/k1').body.applied, false);
  send(s, 'u_me', 'POST', 'tasks/t4/records', { kind: 'note', body: 'x' }, 'k1');
  assert.equal(get(s, 'u_me', 'saves/k1').body.applied, true);
  assert.equal(get(s, 'u_dev', 'saves/k1').body.applied, false);
});

test('전달·조율 업무는 다른 사람의 확인 한 단계만 쓴다', () => {
  const s = mk();
  const t = get(s, 'u_me', 'tasks/t4').body.task;
  assert.deepEqual(t.stages.map((x) => x.label), ['다른 사람의 확인']);
  assert.equal(send(s, 'u_me', 'POST', 'tasks/t4/records', { kind: 'deploy', outcome: 'pass', body: 'x', criteria_version: 1 }, 'a').status, 422);
  assert.equal(send(s, 'u_me', 'POST', 'tasks/t4/records', { kind: 'func', outcome: 'pass', body: 'x', evidence: 'y', criteria_version: 1 }, 'b').status, 422);
  assert.equal(send(s, 'u_dev', 'POST', 'tasks/t4/records', { kind: 'func', outcome: 'pass', body: 'x', evidence: 'y', criteria_version: 1 }, 'c').status, 200);
});
