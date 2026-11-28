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
  const rec = { kind: 'func', outcome: 'pass', body: '통과', evidence: '직접 확인' };
  assert.equal(send(s, 'ai_claude', 'POST', 'tasks/t3/records', rec, 'a').status, 422);
  const t = get(s, 'u_me', 'tasks/t3').body.task;
  assert.equal(send(s, 'u_me', 'PATCH', 'tasks/t3', { rev: t.rev, fields: { status: 'done' } }, 'b').status, 422);
  assert.equal(send(s, 'ai_codex', 'POST', 'tasks/t3/records', rec, 'c').status, 200);
  const t2 = get(s, 'u_me', 'tasks/t3').body.task;
  assert.equal(send(s, 'u_me', 'PATCH', 'tasks/t3', { rev: t2.rev, fields: { status: 'done' } }, 'd').status, 200);
});

test('AI는 실사용 검증과 운영 배포 결과를 남길 수 없다', () => {
  const s = mk();
  assert.equal(send(s, 'ai_codex', 'POST', 'tasks/t3/records', { kind: 'field', outcome: 'pass', body: 'x', evidence: 'y' }, 'a').status, 422);
  assert.equal(send(s, 'ai_codex', 'POST', 'tasks/t3/records', { kind: 'deploy', outcome: 'pass', body: 'x' }, 'b').status, 422);
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
