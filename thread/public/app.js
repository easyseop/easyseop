/* Thread 화면. 서버가 있으면 /api 를, 시연본(window.THREAD_DEMO)이면 같은 core.js 를 브라우저에서 직접 쓴다. */
(function () {
  'use strict';
  const DEMO = !!window.THREAD_DEMO;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* 저장소가 없어도 동작한다 */ } },
    getS(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
    setS(k, v) { try { sessionStorage.setItem(k, v); } catch { /* 없어도 동작한다 */ } },
  };
  const S = { viewer: store.get('thread.viewer') || 'u_me', users: [], home: null, dropNext: false, q: '', flash: null, pending: null, ui: {} };
  const demoState = DEMO ? ThreadCore.seed() : null;
  const STATUS = { todo: '시작 전', doing: '진행 중', blocked: '막힘', hold: '보류', done: '완료' };

  /* ───────── 통신 ───────── */
  class NetError extends Error {}
  class ApiError extends Error { constructor(status, body) { super(body.error || '요청을 처리하지 못했습니다.'); this.status = status; this.body = body; } }
  async function api(method, path, body, key) {
    const drop = method !== 'GET' && S.dropNext;
    if (drop) { S.dropNext = false; }
    if (DEMO) {
      await new Promise((r) => setTimeout(r, 120));
      const [p, qs] = path.split('?');
      const out = ThreadCore.handle(demoState, { method, path: p, query: Object.fromEntries(new URLSearchParams(qs || '')), userId: S.viewer, idemKey: key, body: body ? JSON.parse(JSON.stringify(body)) : null });
      if (drop) throw new NetError('dropped');
      if (out.status !== 200) throw new ApiError(out.status, out.body);
      return JSON.parse(JSON.stringify(out.body));
    }
    let res;
    try {
      const headers = { 'x-thread-user': S.viewer };
      if (body) headers['content-type'] = 'application/json';
      if (key) headers['idempotency-key'] = key;
      if (drop) headers['x-demo-drop-response'] = '1';
      res = await fetch('/api/' + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch { throw new NetError('network'); }
    let data = {};
    try { data = await res.json(); } catch { /* 본문 없음 */ }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }
  const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : 'k' + Date.now() + Math.random().toString(16).slice(2));

  /* ───────── 표시 도우미 ───────── */
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const pad = (n) => String(n).padStart(2, '0');
  function abs(iso) { const d = new Date(iso); return `${d.getMonth() + 1}월 ${d.getDate()}일 ${pad(d.getHours())}:${pad(d.getMinutes())}`; }
  function rel(iso) {
    const m = Math.round((Date.now() - new Date(iso)) / 60000);
    if (m < 1) return '방금';
    if (m < 60) return `${m}분 전`;
    if (m < 60 * 24) return `${Math.round(m / 60)}시간 전`;
    return `${Math.round(m / 1440)}일 전`;
  }
  const when = (iso) => (iso ? `<time title="${esc(abs(iso))}">${rel(iso)}</time>` : '');
  const ICON = {
    todo: '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.8"/>',
    doing: '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M8 2.5a5.5 5.5 0 0 1 0 11z" fill="currentColor"/>',
    blocked: '<rect x="2" y="2" width="12" height="12" rx="3" fill="currentColor"/><path d="M5.5 5.5l5 5m0-5l-5 5" stroke="var(--panel)" stroke-width="1.8" stroke-linecap="round"/>',
    hold: '<circle cx="8" cy="8" r="6.4" fill="currentColor"/><path d="M6.2 5.3v5.4m3.6-5.4v5.4" stroke="var(--panel)" stroke-width="1.8" stroke-linecap="round"/>',
    done: '<circle cx="8" cy="8" r="6.4" fill="currentColor"/><path d="M5 8.2l2.1 2.1L11 6.2" fill="none" stroke="var(--panel)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    pass: '<circle cx="8" cy="8" r="6.4" fill="currentColor"/><path d="M5 8.2l2.1 2.1L11 6.2" fill="none" stroke="var(--panel)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    fail: '<rect x="2" y="2" width="12" height="12" rx="3" fill="currentColor"/><path d="M5.5 5.5l5 5m0-5l-5 5" stroke="var(--panel)" stroke-width="1.8" stroke-linecap="round"/>',
    none: '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-dasharray="2.4 2.2"/>',
  };
  const icon = (k) => `<svg class="ic" viewBox="0 0 16 16" aria-hidden="true">${ICON[k]}</svg>`;
  const chip = (st) => `<span class="chip st-${st}">${icon(st)}${STATUS[st]}</span>`;
  const stageChip = (s) => `<span class="chip sg-${s.state}">${icon(s.state)}${esc(s.text)}</span>`;
  function who(u, opt) {
    if (!u) return '<span class="who none">정해지지 않음</span>';
    const me = u.id === S.viewer ? '<em class="me">나</em>' : '';
    return `<span class="who"><i class="av ${u.kind}" aria-hidden="true">${u.kind === 'ai' ? 'AI' : esc(u.name.slice(0, 1))}</i><b>${esc(u.name)}</b><small>${esc(u.team)}</small>${opt === 'plain' ? '' : me}</span>`;
  }
  const nm = (u) => (u ? `${esc(u.name)}(${esc(u.team)})` : '');
  const empty = (title, text) => `<div class="empty"><b>${esc(title)}</b><p>${esc(text)}</p></div>`;

  /* 실 모양 진척: 시작 매듭에서 목표까지, 중간 목표마다 구슬 하나. 완료한 요구사항 비율만큼만 실이 채워진다. */
  function thread(p, start, finish, compact) {
    const data = esc(JSON.stringify(p.milestones.map((m) => [m.done, m.total])));
    const labels = compact ? '' : `<ol class="th-labels" style="--n:${p.milestones.length}">${p.milestones.map((m) => `
      <li class="ms-${m.state}"><b>${esc(m.title)}</b><span>${m.total}개 중 ${m.done}개 완료</span></li>`).join('')}</ol>`;
    return `<div class="thread ${compact ? 'compact' : ''}">
      ${compact ? '' : `<div class="th-ends"><span><small>시작</small>${esc(start)}</span><span><small>최종 목표</small>${esc(finish)}</span></div>`}
      <div class="th-line" data-ms="${data}" role="img" aria-label="요구사항 ${p.total}개 중 ${p.done}개 완료"></div>${labels}</div>`;
  }
  function drawThreads() {
    const lines = document.querySelectorAll('.th-line');
    lines.forEach((el) => { el.innerHTML = ''; });
    lines.forEach((el) => {
      const ms = JSON.parse(el.dataset.ms), M = ms.length, W = el.clientWidth, H = 56, mid = 34, A = 10, x0 = 10, x1 = W - 10;
      if (W < 40) return;
      const up = ms.reduce((a, [d, t]) => a + (t ? d / t : 0), 0) / M;
      const X = (u) => x0 + u * (x1 - x0), Y = (u) => mid + A * Math.sin(Math.PI * M * u);
      const path = (a, b) => { let d = ''; const n = Math.max(2, Math.ceil((b - a) * 120)); for (let i = 0; i <= n; i++) { const u = a + ((b - a) * i) / n; d += (i ? 'L' : 'M') + X(u).toFixed(1) + ' ' + Y(u).toFixed(1); } return d; };
      let beads = `<circle cx="${X(0)}" cy="${Y(0)}" r="4" class="b-done"/>`;
      ms.forEach(([d, t], i) => {
        const u = (i + 1) / M, full = d === t, last = i === M - 1;
        beads += `<circle cx="${X(u)}" cy="${Y(u)}" r="${last ? 7 : 5.5}" class="${full ? 'b-done' : 'b-open'}"/>` + (last ? `<circle cx="${X(u)}" cy="${Y(u)}" r="2.5" class="${full ? 'b-hole' : 'b-done'}"/>` : '');
      });
      const tx = Math.min(Math.max(X(up), 28), W - 28);
      el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
        <path d="${path(up, 1)}" class="t-rest"/>${up > 0 ? `<path d="${path(0, up)}" class="t-done"/>` : ''}${beads}
        ${up < 1 ? `<circle cx="${X(up)}" cy="${Y(up)}" r="6.5" class="b-now"/><text x="${tx}" y="${Math.max(10, Y(up) - 13).toFixed(1)}" text-anchor="middle" class="t-now">현재 위치</text>` : ''}</svg>`;
    });
  }
  window.addEventListener('resize', drawThreads);

  /* ───────── 저장 흐름: 한 번 만든 요청 키를 재시도에도 그대로 쓴다 ───────── */
  const PENDING = { type: 'err', title: '저장 응답을 받지 못했습니다', lines: ['반영되었는지 확인되지 않았습니다. 재시도해도 두 번 저장되지 않습니다.'], retry: true };
  async function save(op) {
    if (S.saving) { S.flash = { type: 'busy', title: '앞의 저장이 아직 끝나지 않았습니다', lines: ['이 저장은 보내지 않았습니다. 앞의 저장이 끝난 뒤 다시 눌러 주세요.'] }; paintFlash(); return; }
    const sig = JSON.stringify([op.method, op.path, op.body]), from = location.hash;
    if (S.pending && S.pending.sig !== sig) { // 앞선 저장이 풀리기 전에는 다른 저장을 보내지 않는다.
      S.flash = Object.assign({}, PENDING, { lines: ['이전 저장의 반영 여부가 확인되지 않았습니다. 먼저 재시도하거나 반영 여부 확인해 주세요.'] });
      paintFlash(); return;
    }
    // 응답을 못 받은 같은 저장은 어느 버튼으로 다시 보내든 같은 요청 키를 쓴다.
    if (S.pending && !S.pending.op) { await resolvePending(); paintFlash(); return; }
    const pend = S.pending || { op, key: newKey(), sig };
    S.pending = null; S.saving = true; lockForms(true);
    S.flash = { type: 'busy', title: '저장하는 중입니다' };
    paintFlash();
    try {
      const out = await api(pend.op.method, pend.op.path, pend.op.body, pend.key);
      S.saving = false;
      const ch = out.saved && out.saved.changes;
      S.flash = {
        type: 'ok', title: '반영되었습니다', changes: ch,
        lines: [ch && ch.length ? null : out.saved && out.saved.summary, out.replayed ? '끊겼던 저장이 이미 반영되어 있었습니다. 두 번 저장되지 않았습니다.' : null].filter(Boolean),
      };
      if (location.hash !== from) { // 저장하는 사이 다른 화면으로 옮겼다. 그 화면에서 쓰던 내용은 건드리지 않는다.
        S.flash = { type: 'ok', title: '이전 저장이 반영되었습니다', lines: ['이 화면에서 누른 저장이 있었다면 보내지 않았습니다. 다시 눌러 주세요.'] };
        lockForms(false); paintFlash(); return;
      }
      S.ui = {};
      await render();
      window.scrollTo(0, 0);
    } catch (e) {
      S.saving = false;
      if (e instanceof NetError) { S.pending = pend; store.setS('thread.pending', pend.key); S.flash = PENDING; paintFlash(); return; } // 입력 칸은 잠근 채로 둔다.
      const latest = e.status === 409 && e.body.task;
      if (latest && S.ui.mode && S.ui.base) {
        // 그 사이 다른 사람이 바꿨다. 내가 손대지 않은 항목은 최신 값을 따르고, 내가 고친 항목만 다시 비교한다.
        const was = shape(S.ui.base), now = shape(latest);
        Object.keys(was).forEach((k) => { if (JSON.stringify(S.ui.draft[k]) === JSON.stringify(was[k])) S.ui.draft[k] = now[k]; });
        S.ui.base = latest; S.ui.mode = 'confirm';
        S.flash = { type: 'err', title: '다른 사람이 먼저 이 업무를 바꿨습니다', lines: ['저장되지 않았습니다. 아래 표는 최신 내용과 내 수정을 다시 비교한 것입니다. 확인한 뒤 저장해 주세요.'] };
      } else if (latest && S.ui.rec) {
        const cvChanged = S.ui.cv !== latest.criteria_version;
        S.ui.cv = latest.criteria_version; S.ui.rev = latest.rev;
        S.flash = { type: 'err', title: cvChanged ? '완료 조건이 바뀌었습니다' : '다른 사람이 먼저 이 업무를 변경했습니다', lines: ['저장되지 않았습니다. 최신 내용을 확인한 뒤 다시 저장해 주세요. 입력한 내용은 그대로 있습니다.'] };
      } else {
        if (e.status === 409) S.ui = {};
        S.flash = { type: 'err', title: '저장하지 못했습니다', lines: [e.message] };
        if (e.status !== 409) { lockForms(false); paintFlash(); return; }
      }
      await render();
    }
  }
  // 응답을 못 받은 저장이 실제로 반영됐는지 서버에 묻는다.
  async function resolvePending() {
    try {
      const r = await api('GET', 'saves/' + S.pending.key);
      S.pending = null; store.setS('thread.pending', '');
      S.flash = r.applied ? { type: 'ok', title: '이전 저장은 반영되어 있습니다', lines: ['다시 저장할 필요가 없습니다.'] }
        : { type: 'err', title: '이전 저장은 반영되지 않았습니다', lines: ['같은 내용을 다시 저장해 주세요.'] };
      return r.applied;
    } catch { S.flash = Object.assign({}, PENDING, { lines: ['아직 서버에 연결되지 않습니다. 잠시 뒤 다시 확인해 주세요.'] }); return null; }
  }
  function lockForms(on) {
    document.querySelectorAll('#rec-form, #edit-form, .confirm, .decision').forEach((f) =>
      f.querySelectorAll('button, input, select, textarea').forEach((el) => { el.disabled = on; }));
  }
  function paintFlash() {
    const el = document.getElementById('flash');
    if (!el) return;
    const f = S.flash;
    el.className = f && f.type !== 'ok' ? 'stick' : '';
    if (!f) { el.innerHTML = ''; return; }
    const li = (c) => `<li><span>${esc(c.label)}</span> ${esc(c.from)} → <strong>${esc(c.to)}</strong></li>`;
    el.innerHTML = `<div class="flash ${f.type}" role="${f.type === 'err' ? 'alert' : 'status'}">
      <div class="fl-main"><b>${esc(f.title)}</b>${(f.lines || []).map((l) => `<p>${esc(l)}</p>`).join('')}
      ${f.changes && f.changes.length ? `<ul>${f.changes.slice(0, 3).map(li).join('')}</ul>${f.changes.length > 3 ? `<details class="more"><summary>외 ${f.changes.length - 3}건</summary><ul>${f.changes.slice(3).map(li).join('')}</ul></details>` : ''}` : ''}
</div>
      <div class="fl-act">${f.retry ? '<button class="btn primary" data-act="retry">재시도</button><button class="btn ghost" data-act="check">반영 여부 확인</button>' : f.type !== 'busy' ? '<button class="btn ghost" data-act="flash-close">닫기</button>' : ''}</div></div>`;
    if (S.pending || S.saving) lockForms(true);
  }

  /* ───────── 화면: 홈 ───────── */
  // 한 건은 두 줄: 무엇인지, 다음에 무엇을 하는지. 나머지는 상세에서 본다.
  function homeItem(t, decide) {
    const reason = t.blocked_reason ? `<span class="item-line reason"><small>${t.status === 'hold' ? '보류 사유' : '막힘 사유'}</small>${esc(t.blocked_reason)}</span>` : '';
    const by = decide ? `${nm(t.requested_by)} 요청 · ${when(t.requested_at)}` : t.handoff_from ? `${nm(t.handoff_from)} 인계 · ${when(t.handed_at)}` : when(t.updated_at);
    return `<li><a class="item ${decide ? 'decide' : ''}" href="#task-${t.id}${decide ? '-decide' : ''}">
      <span class="item-top">${decide ? '' : chip(t.status)}<span class="crumb">${esc(t.project.name)}</span><span class="item-by">${by}</span></span>
      <b class="item-title">${esc(decide ? t.question : t.title)}</b>${decide ? '' : reason}
      ${t.failing.length ? `<span class="item-line reason"><small>검증</small>${esc(t.failing.join(', '))}</span>` : ''}
      ${decide ? `<span class="item-go">선택지 ${t.option_count}개 중에서 결정하기 →</span>` : `<span class="item-line one"><small>다음 조치</small><span class="clip" title="${esc(t.next_action)}"><b>${esc(t.next_action)}</b></span></span>`}</a></li>`;
  }
  function viewHome(h) {
    const n = h.decisions.length + h.handoffs.length, sc = h.since;
    const mine = `${h.me.name}(${h.me.team}) 님에게 인계`;
    const change = (c) => `<li><a href="#task-${c.task_id}"><b>${esc(c.text === mine ? '나에게 인계' : c.text)}</b><span title="${esc(c.task_title)}">${esc(c.task_title)}</span></a><small>${nm(c.actor)} · ${when(c.at)}</small></li>`;
    const since = `<section aria-labelledby="h-since"><header class="row"><h2 id="h-since">${sc.at ? '미확인 변경' : '최근 변경'} <span class="num">${sc.total}</span></h2>
        ${sc.total ? '<button class="btn ghost sm" data-act="seen">읽음 처리</button>' : ''}</header><div class="since">
      ${sc.total ? `<ul class="feed">${sc.changes.slice(0, 4).map(change).join('')}</ul>
        ${sc.changes.length > 4 ? `<details class="more"><summary>${sc.changes.length - 4}건 더 보기</summary><ul class="feed">${sc.changes.slice(4).map(change).join('')}</ul></details>` : ''}`
        : `<p class="muted small">${sc.at ? esc(abs(sc.at)) + ' 이후 변경이 없습니다.' : '변경이 없습니다.'}</p>`}</div></section>`;
    const proj = h.projects.map((p) => `<li><a class="proj" href="#project-${p.id}">
        <span class="proj-top"><b>${esc(p.name)}</b><span class="count"><b>${p.progress.done}</b>/${p.progress.total} 요구사항 완료</span></span>
        ${thread(p.progress, p.start, p.finish, true)}
        ${p.blocked ? `<span class="chip st-blocked">${icon('blocked')}막힘 요구사항 ${p.blocked}개</span>` : ''}</a></li>`).join('');
    const decSec = `<section aria-labelledby="h-dec"><h2 id="h-dec">판단 대기 <span class="num">${h.decisions.length}</span></h2>
            ${h.decisions.length ? `<ul class="items">${h.decisions.map((t) => homeItem(t, true)).join('')}</ul>` : '<p class="none-line">판단 대기 업무가 없습니다.</p>'}</section>`;
    const handSec = `<section aria-labelledby="h-hand"><h2 id="h-hand">인계받은 업무 <span class="num">${h.handoffs.length}</span></h2>
            ${h.handoffs.length ? `<ul class="items">${h.handoffs.map((t) => homeItem(t, false)).join('')}</ul>` : '<p class="none-line">인계받은 업무가 없습니다.</p>'}</section>`;
    return `
      <header class="page-head"><p class="eyebrow">${esc(h.me.name)} 님 · ${esc(h.me.team)}</p>
        <h1>${n ? `처리할 업무 <mark>${n}건</mark>` : '처리할 업무가 없습니다'}</h1></header>
      <div class="home-grid">
        <div class="col">${n ? '' : '<p class="none-line">판단 대기 또는 인계받은 업무가 생기면 여기에 표시됩니다.</p>'}
          ${h.decisions.length ? decSec : ''}${n ? handSec : ''}${n && !h.decisions.length ? decSec : ''}
        </div>
        <aside class="col">${since}
          <section aria-labelledby="h-proj"><h2 id="h-proj">내 프로젝트</h2>
          ${proj ? `<ul class="projs">${proj}</ul>` : empty('참여 중인 프로젝트가 없습니다', '구성원으로 추가되면 여기에 보입니다.')}</section></aside>
      </div>`;
  }

  /* ───────── 화면: 프로젝트 ───────── */
  function reqRow(r, focus, later) {
    const hlMine = (t) => (t.next_owner && t.next_owner.id === S.viewer ? 'hl' : '');
    const single = r.tasks.length === 1 && r.tasks[0].title.replace(/\s/g, '') === r.title.replace(/\s/g, '') ? r.tasks[0] : null;
    const facts = (t) => (t.status === 'done' ? '' : `
        <dl class="facts tight">${t.blocked_reason ? `<div class="fact reason ${t.status === 'hold' ? 'hold' : ''}"><dt>${t.status === 'hold' ? '보류 사유' : '막힘 사유'}</dt><dd>${esc(t.blocked_reason)}</dd></div>` : ''}
          <div class="fact next one"><dt>다음 조치</dt><dd><span class="clip" title="${esc(t.next_action)}"><span class="${hlMine(t)}">${esc(t.next_action)}</span></span>${who(t.next_owner)}</dd></div></dl>`);
    if (single) {
      return `<li class="req ${r.status === 'done' ? 'is-done' : ''} ${focus === r.id ? 'focus' : ''}" id="req-${r.id}" data-status="${r.status}">
        <div class="req-head">${chip(r.status)}<h4><a href="#task-${single.id}">${esc(r.title)}</a></h4>${single.failing.map((f) => `<span class="chip sg-fail">${icon('fail')}${esc(f)}</span>`).join('')}</div>${facts(single)}</li>`;
    }
    const same = r.tasks.length > 0 && r.tasks.every((t) => t.status === r.status);
    const tasks = r.tasks.map((t) => `
      <li class="trow ${t.status === 'done' ? 'is-done' : ''}">
        <div class="trow-main">${same ? '' : chip(t.status)}<a href="#task-${t.id}">${esc(t.title)}</a>${t.failing.map((f) => `<span class="chip sg-fail">${icon('fail')}${esc(f)}</span>`).join('')}</div>
        ${facts(t)}
      </li>`).join('');
    return `<li class="req ${r.status === 'done' ? 'is-done' : ''} ${focus === r.id ? 'focus' : ''}" id="req-${r.id}" data-status="${r.status}">
      <div class="req-head">${later ? '<span class="tag">후순위</span>' : chip(r.status)}<h4>${esc(r.title)}</h4></div>
      ${r.note ? `<p class="req-note">${esc(r.note)}</p>` : ''}
      ${!tasks ? '' : r.status === 'done' && focus !== r.id ? `<details class="fold-s"><summary>업무 ${r.tasks.length}건</summary><ul class="tasks">${tasks}</ul></details>` : `<ul class="tasks">${tasks}</ul>`}
      ${r.hidden_tasks ? `<p class="req-note lock">열람 제한 업무 ${r.hidden_tasks}건</p>` : (!tasks && !r.note ? '<p class="req-note">연결된 업무 없음</p>' : '')}</li>`;
  }
  function viewProject(p, focus) {
    const all = p.milestones.flatMap((m) => m.requirements);
    const f = S.ui.filter || 'all';
    const counts = Object.keys(STATUS).map((k) => [k, all.filter((r) => r.status === k).length]);
    const show = (r) => f === 'all' || r.status === f;
    let shown = 0;
    return `
      <header class="page-head"><p class="eyebrow">프로젝트${p.id === 'p0' ? ' · 실제 개발 현황' : ''}</p><h1>${esc(p.name)}</h1>
        <p class="lede goal-line"><span>목표</span>${esc(p.goal)}</p></header>
      <section class="panel progress" aria-label="진척"><div class="prog-top"><h2>진척</h2>
        <p class="count big"><b>${p.progress.done}</b>/${p.progress.total} 요구사항 완료</p></div>
        ${thread(p.progress, p.start, p.finish)}
        <p class="muted small">완료한 요구사항 수 기준. 후순위 ${p.progress.later_count}개 제외.</p></section>
      <div class="filters" role="group" aria-label="상태로 걸러 보기">
        <button class="fbtn ${f === 'all' ? 'on' : ''}" aria-pressed="${f === 'all'}" data-act="filter" data-v="all">전체 ${all.length}</button>
        ${counts.map(([k, c]) => `<button class="fbtn ${f === k ? 'on' : ''} ${c ? '' : 'zero'}" aria-pressed="${f === k}" data-act="filter" data-v="${k}">${icon(k)}${STATUS[k]} ${c}</button>`).join('')}</div>
      ${p.milestones.map((m, i) => {
        const rs = m.requirements.filter(show);
        const d = m.requirements.filter((r) => r.status === 'done').length;
        if (f !== 'all' && !rs.length) return '';
        shown += rs.length;
        return `<section class="mile"><header><span class="step">중간 목표 ${i + 1}</span><h3>${esc(m.title)}</h3><span class="count">${d}/${m.requirements.length} 완료</span></header>
          ${rs.length ? `<ul class="reqs">${rs.map((r) => reqRow(r, focus)).join('')}</ul>` : `<p class="muted pad">이 상태의 요구사항이 없습니다.</p>`}</section>`;
      }).join('')}
      ${f !== 'all' && !all.some(show) ? `<div class="empty"><b>${STATUS[f]} 상태인 요구사항이 없습니다</b><p><button class="btn" data-act="filter" data-v="all">전체 보기</button></p></div>` : ''}
      <details class="later" ${focus && p.later.some((r) => r.id === focus) ? 'open' : ''}><summary>후순위 요구사항 ${p.later.length}개 <span>현재 범위 외</span></summary>
        <ul class="reqs">${p.later.map((r) => reqRow(r, focus, true)).join('')}</ul></details>`;
  }

  /* ───────── 화면: 업무 상세 ───────── */
  const KIND_OPTS = [['note', '기록'], ['impl', '구현 완료 보고'], ['func', '제3자 기능 검증'], ['field', '실사용자 검증'], ['deploy', '운영 배포']];
  const shape = (t) => ({ title: t.title, status: t.status, next_owner_id: t.next_owner ? t.next_owner.id : '', blocked_reason: t.blocked_reason, next_action: t.next_action, current_state: t.current_state, done_criteria: t.done_criteria.slice() });
  const outWord = (t, k, o) => (k === 'deploy' ? (o === 'pass' ? '배포됨' : '배포 실패') : t.track === 'check' ? (o === 'pass' ? '검수 통과' : '되돌려 보냄') : (o === 'pass' ? '통과' : '실패'));
  function editForm(t) {
    const d = S.ui.draft;
    return `<section class="panel edit" aria-labelledby="ed-h"><h2 id="ed-h">업무 수정</h2>
      <form id="edit-form" class="form">
        <label class="wide">제목<input id="f-title" name="title" value="${esc(d.title)}" maxlength="200" required></label>
        <label>상태<select id="f-status" name="status">${Object.entries(STATUS).map(([k, v]) => `<option value="${k}" ${d.status === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
        <label>다음 담당자<select id="f-owner" name="next_owner_id"><option value="">정해지지 않음</option>${t.members.map((u) => `<option value="${u.id}" ${d.next_owner_id === u.id ? 'selected' : ''}>${esc(u.name)} · ${esc(u.team)}</option>`).join('')}</select></label>
        <label class="wide" id="f-reason-wrap" ${['blocked', 'hold'].includes(d.status) ? '' : 'hidden'}><span id="f-reason-l">${d.status === 'hold' ? '보류 사유' : '막힘 사유'}</span><input id="f-reason" name="blocked_reason" value="${esc(d.blocked_reason)}"></label>
        <label class="wide">다음 조치<textarea id="f-next" name="next_action" rows="2">${esc(d.next_action)}</textarea></label>
        <label class="wide">진행 상황<textarea id="f-state" name="current_state" rows="2">${esc(d.current_state)}</textarea></label>
        <label class="wide">완료 조건 <small>한 줄에 하나씩. 바꾸면 검증 기준이 새로 시작됩니다.</small><textarea id="f-criteria" name="done_criteria" rows="4">${esc(d.done_criteria.join('\n'))}</textarea></label>
        <div class="form-act wide"><button class="btn primary" type="submit">변경 내용 확인</button><button class="btn ghost" type="button" data-act="edit-cancel">수정 취소</button></div>
      </form></section>`;
  }
  function draftDiff(t, d) {
    const name = (id) => { const u = t.members.find((m) => m.id === id); return u ? `${u.name}(${u.team})` : '정해지지 않음'; };
    const cur = shape(t);
    const L = { title: '제목', status: '상태', next_owner_id: '다음 담당자', blocked_reason: d.status === 'hold' ? '보류 사유' : '막힘 사유', next_action: '다음 조치', current_state: '진행 상황', done_criteria: '완료 조건' };
    const fmt = (k, v) => (k === 'status' ? STATUS[v] : k === 'next_owner_id' ? name(v) : k === 'done_criteria' ? v.join(' / ') : v || '(비어 있음)');
    const rows = [], fields = {};
    Object.keys(L).forEach((k) => {
      if (JSON.stringify(cur[k]) !== JSON.stringify(d[k])) { rows.push({ label: L[k], from: fmt(k, cur[k]), to: fmt(k, d[k]), k }); fields[k] = d[k]; }
    });
    return { rows, fields, name };
  }
  function confirmPanel(t) {
    const d = S.ui.draft, { rows, name } = draftDiff(S.ui.base, d);
    const notes = [];
    if (rows.some((r) => r.k === 'done_criteria')) notes.push('완료 조건이 바뀌어 지금까지의 검증 결과는 종전 기준으로 남습니다. 새 조건으로 다시 검증해야 합니다.');
    if (rows.some((r) => r.k === 'next_owner_id') && d.next_owner_id) notes.push(`${name(d.next_owner_id)} 님이 담당자가 되고, 그 사람의 홈 ‘인계받은 업무’에 이 업무가 표시됩니다.`);
    if (rows.some((r) => r.k === 'status') && d.status === 'done') notes.push(`현행 기준의 ${(t.stages.find((x) => x.key === 'func') || {}).label}에서 통과한 경우에만 완료로 저장됩니다.`);
    return `<section class="panel confirm" aria-labelledby="cf-h"><h2 id="cf-h">저장 시 변경 내용</h2>
      ${rows.length ? `<div class="scroll-x"><table class="diff"><thead><tr><th>항목</th><th>지금</th><th>저장 후</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><th>${esc(r.label)}</th><td class="from">${esc(r.from)}</td><td class="to">${esc(r.to)}</td></tr>`).join('')}</tbody></table></div>
        ${notes.map((n) => `<p class="note">${esc(n)}</p>`).join('')}` : '<p class="muted">바뀐 항목이 없습니다.</p>'}
      <div class="form-act"><button class="btn primary" data-act="edit-save" ${rows.length ? '' : 'disabled'}>저장</button><button class="btn ghost" data-act="edit-back">수정 계속</button></div></section>`;
  }
  function decisionPanel(t) {
    const d = t.decision;
    if (d.chosen) {
      const o = d.options.find((x) => x.id === d.chosen);
      return `<section class="panel decision done" id="sec-decide"><h2>결정 사항</h2><p class="q">${esc(d.question)}</p>
        <p class="chosen">${icon('done')}<b>${esc(o.label)}</b></p><p class="muted">${who(d.owner)} 결정 · ${when(d.chosen_at)}</p></section>`;
    }
    const sel = S.ui.option, o = d.options.find((x) => x.id === sel);
    return `<section class="panel decision" id="sec-decide" aria-labelledby="dc-h">
      <h2 id="dc-h">결정 요청</h2>
      <p class="q">${esc(d.question)}</p>
      ${t.blocked_reason ? `<p class="item-line reason"><small>막힘 사유</small>${esc(t.blocked_reason)}</p>` : ''}
      <p class="muted small">${esc(d.why)} · ${nm(d.requested_by)} 요청 · ${when(d.requested_at)}</p>
      ${d.can_decide ? '' : `<p class="note">이 결정은 ${esc(d.owner.name)}(${esc(d.owner.team)}) 님만 내릴 수 있습니다.</p>`}
      <div class="options" role="radiogroup" aria-label="선택지">${d.options.map((x) => `
        <label class="opt ${sel === x.id ? 'on' : ''}"><input type="radio" name="opt" id="opt-${x.id}" value="${x.id}" ${sel === x.id ? 'checked' : ''} ${d.can_decide ? '' : 'disabled'} data-act="pick">
          <span class="opt-body"><b>${esc(x.label)}</b>${d.recommended === x.id ? `<em class="rec">${esc(d.requested_by.name)} 추천</em>` : ''}<span>${esc(x.effect)}</span></span></label>`).join('')}</div>
      ${!d.can_decide ? '' : `<div class="will" id="will">${o ? `<h3>결정 시 변경 내용</h3><dl class="facts tight">
          <div class="fact"><dt>다음 담당자</dt><dd>${who(t.next_owner)} → ${who(o.next_owner)}</dd></div>
          <div class="fact next"><dt>다음 조치</dt><dd><b>${esc(o.next_action)}</b></dd></div>
          ${t.status === 'blocked' ? '<div class="fact"><dt>상태</dt><dd>막힘 → 시작 전</dd></div>' : ''}</dl>` : ''}
          <div class="row"><button class="btn primary" data-act="decide" ${o ? '' : 'disabled'}>선택한 안으로 확정</button>${o ? '' : '<span class="muted small">선택지를 고르면 확정할 수 있습니다.</span>'}</div></div>`}
    </section>`;
  }
  function recordForm(t) {
    const me = S.users.find((u) => u.id === S.viewer) || {};
    const built = t.did_work.includes(S.viewer);
    const may = (key) => !(key === 'func' && built) && !(['field', 'deploy'].includes(key) && me.kind === 'ai');
    const kinds = [['note', '메모']].concat(t.stages.filter((x) => may(x.key)).map((x) => [x.key, x.key === 'impl' ? '구현 완료 보고' : x.label]));
    const k = kinds.some((x) => x[0] === S.ui.kind) ? S.ui.kind : 'note', oc = S.ui.outcome || 'pass', canPass = t.status !== 'done', after = canPass ? S.ui.after || 'keep' : 'keep';
    const stage = t.stages.find((x) => x.key === k), needs = stage && k !== 'impl', build = stage && t.track === 'build';
    const others = t.members.filter((u) => u.id !== S.viewer);
    const reviewStage = t.stages.find((x) => x.key === 'func'), reviewName = reviewStage ? reviewStage.label : '제3자 검수';
    const will = [];
    if (stage) will.push(`${stage.label} 결과가 ‘${k === 'impl' ? '완료' : outWord(t, k, oc)}’로 기록됩니다.`);
    if (stage && t.status === 'done' && oc === 'fail' && k !== 'impl') will.push('업무가 완료에서 진행 중으로 돌아갑니다.');
    if (after === 'work') will.push(`담당자와 다음 담당자가 바뀌고, 그 사람의 홈에 이 업무가 표시됩니다.${t.status === 'todo' ? ' 상태는 진행 중으로 바뀝니다.' : ''}`);
    if (after === 'review') will.push(`담당자는 그대로이고, 검수 담당의 홈에 이 업무가 표시됩니다.${t.status === 'todo' ? ' 상태는 진행 중으로 바뀝니다.' : ''}`);
    return `<form id="rec-form" class="form rec-form">
      <label ${kinds.length > 1 ? '' : 'hidden'}>종류<select id="r-kind" name="kind" data-act="rec-kind">${kinds.map(([v, l]) => `<option value="${v}" ${k === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
      <fieldset ${needs ? '' : 'hidden'}><legend>결과</legend>
        <label class="radio"><input type="radio" name="outcome" id="r-pass" value="pass" ${oc === 'pass' ? 'checked' : ''} data-act="rec-outcome">${outWord(t, k, 'pass')}</label>
        <label class="radio"><input type="radio" name="outcome" id="r-fail" value="fail" ${oc === 'fail' ? 'checked' : ''} data-act="rec-outcome">${outWord(t, k, 'fail')}</label></fieldset>
      <label class="wide">내용<textarea id="r-body" name="body" rows="3" maxlength="2000" required placeholder="무엇을 했고 결과가 어땠는지 적습니다">${esc(S.ui.body || '')}</textarea></label>
      <label class="wide">근거 서술 <small>${needs ? '(필수)' : '(생략 가능)'}</small><input id="r-evidence" name="evidence" maxlength="2000" value="${esc(S.ui.evidence || '')}" placeholder="확인한 건수, 화면, 문서 이름 등"></label>
      ${build ? `<label>코드 변경 번호(커밋) <small>(생략 가능)</small><input id="r-commit" name="commit" maxlength="40" value="${esc(S.ui.commit || '')}" placeholder="예: 2ec355d"></label>
      <div class="pair"><label>시험 통과 건수 <small>(생략 가능)</small><input id="r-tp" name="tp" type="number" min="0" value="${esc(S.ui.tp || '')}"></label>
        <label>시험 전체 건수 <small>(생략 가능)</small><input id="r-tt" name="tt" type="number" min="1" value="${esc(S.ui.tt || '')}"></label></div>` : ''}
      ${!canPass ? '' : `<fieldset class="wide"><legend>저장 후 처리</legend>
        <label class="radio"><input type="radio" name="after" id="r-keep" value="keep" ${after === 'keep' ? 'checked' : ''} data-act="rec-after">담당자 유지</label>
        <label class="radio"><input type="radio" name="after" id="r-review" value="review" ${after === 'review' ? 'checked' : ''} data-act="rec-after">${esc(reviewName)} 요청</label>
        <label class="radio"><input type="radio" name="after" id="r-work" value="work" ${after === 'work' ? 'checked' : ''} data-act="rec-after">다른 담당자에게 인계</label></fieldset>`}
      ${after === 'keep' ? '' : `<label>${after === 'review' ? '검수 담당' : '인계 대상'}<select id="r-to" name="to" required><option value="">선택하세요</option>${others.filter((u) => after !== 'review' || !t.did_work.includes(u.id)).map((u) => `<option value="${u.id}" ${S.ui.to === u.id ? 'selected' : ''}>${esc(u.name)} · ${esc(u.team)}</option>`).join('')}</select></label>
        <label>${after === 'review' ? '요청 내용' : '인계 후 조치'}<input id="r-act" name="act" maxlength="500" required value="${esc(S.ui.act != null ? S.ui.act : after === 'review' ? '결과를 검수하고 검수 결과를 기록한다' : '')}"></label>`}
      ${will.length ? `<p class="note wide"><b>저장하면</b> ${esc(will.join(' '))}</p>` : ''}
      <div class="form-act wide"><button class="btn primary" type="submit">기록 저장</button><button class="btn ghost" type="button" data-act="rec-close">닫기</button></div></form>`;
  }
  function viewTask(t, part) {
    const mine = t.next_owner && t.next_owner.id === S.viewer && t.status !== 'done';
    const editing = S.ui.mode === 'edit', confirming = S.ui.mode === 'confirm';
    const head = `
      <nav class="crumbs" aria-label="위치"><a href="#project-${t.project.id}">${esc(t.project.name)}</a>${t.milestone ? `<span>${esc(t.milestone.title)}</span>` : '<span>후순위</span>'}
        <a href="#project-${t.project.id}-${t.requirement.id}" class="req-link"><small>상위 요구사항</small>${esc(t.requirement.title)}</a></nav>
      <header class="task-head"><div><h1 class="${t.title.length > 44 ? 'long' : ''}">${esc(t.title)}</h1>
        <div class="meta">${chip(t.status)}${t.failing.map((f) => `<button class="chip sg-fail as-btn" data-act="goto" data-v="sec-verify">${icon('fail')}${esc(f)}</button>`).join('')}<span class="mi"><small>담당자</small>${who(t.assignee)}</span><span class="mi"><small>마지막 수정</small>${when(t.updated_at)}</span>${t.restricted ? '<span class="tag lock">지정된 사람만 열람</span>' : ''}</div></div>
        ${editing || confirming ? '' : '<button class="btn" data-act="edit">수정</button>'}</header>`;
    const hero = t.status === 'done' ? '<p class="done-line">완료된 업무입니다. 남은 조치가 없습니다.</p>' : `
      <section class="hero ${t.blocked_reason ? 'two' : ''}">
        <div class="next-box ${mine ? '' : 'other'}"><small>다음 조치</small><p>${esc(t.next_action)}</p>
          <div class="next-owner"><small>다음 담당자</small>${who(t.next_owner)}</div>
          ${t.handoff_from && t.handoff_from.id !== S.viewer ? `<p class="by">${nm(t.handoff_from)} · ${when(t.handed_at)} 인계</p>` : ''}
          ${mine ? '<button class="btn dark" data-act="rec-open" data-v="pass">결과 기록 및 인계</button>' : ''}</div>
        ${t.blocked_reason ? `<div class="reason-box"><small>${t.status === 'hold' ? '보류 사유' : '막힘 사유'}</small><p>${esc(t.blocked_reason)}</p></div>` : ''}
      </section>`;
    const prevCriteria = t.criteria_log.filter((c) => c.version < t.criteria_version);
    // 근거: 다시 확인할 수 있는 것(커밋, 시험 결과)과 글로 적은 것을 나눠 보여 준다.
    const proof = (x) => {
      if (x.evidence_level === 'none') return '<span class="ev-tag stated">근거 없음</span>';
      const parts = [];
      if (x.proof && x.proof.test_total != null) parts.push(`시험 ${x.proof.test_total}건 중 ${x.proof.test_passed}건 통과`);
      if (x.proof && x.proof.commit) parts.push(`코드 변경 <code>${esc(x.proof.commit.slice(0, 10))}</code>`);
      return x.evidence_level === 'checkable' ? `<span class="ev-tag checkable">검증 가능 근거</span>${parts.join(' · ')}` : '<span class="ev-tag stated">서술 근거</span>';
    };
    const recItem = (r) => `<li id="rec-${r.id}" class="${r.is_current_criteria || !r.is_stage ? '' : 'old'}">
          <div class="tl-head"><b>${esc(r.kind_label)}</b>${r.outcome && r.kind !== 'impl' ? `<span class="chip sg-${r.outcome}">${icon(r.outcome)}${outWord(t, r.kind, r.outcome)}</span>` : ''}
            ${r.is_current_criteria || !r.is_stage ? '' : `<span class="ver old">종전 기준 ${r.criteria_version}차</span>`}<span class="tl-by">${nm(r.author)} · <time>${esc(abs(r.at))}</time></span></div>
          <p class="tl-body">${esc(r.body).replace(/\n/g, '<br>')}</p>
          ${['impl', 'func', 'field', 'deploy'].includes(r.kind) || r.evidence ? `<p class="tl-ev">${['impl', 'func', 'field', 'deploy'].includes(r.kind) ? proof(r) : ''}${r.evidence ? ` <span>${esc(r.evidence)}</span>` : ''}</p>` : ''}</li>`;
    const body = `
      <div class="task-grid ${t.failing.length ? 'has-fail' : ''}">
        <section class="panel" aria-labelledby="ho-h"><h2 id="ho-h">업무 내용</h2>
          <dl class="spec"><div><dt>목표</dt><dd>${esc(t.goal)}</dd></div>
            <div><dt>진행 상황</dt><dd>${esc(t.current_state)}</dd></div>
            <div><dt>완료 조건</dt><dd><ul class="criteria">${t.done_criteria.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
              ${prevCriteria.length ? `<details class="prev"><summary>종전 완료 조건 보기</summary>${prevCriteria.map((c) => `<p><b>${c.version}차</b> · ${esc(abs(c.at))}<br>${c.items.map(esc).join(' / ')}</p>`).join('')}</details>` : ''}</dd></div>
            ${t.rationale.length ? `<div><dt>판단 근거</dt><dd><ul class="why-list">${t.rationale.map((r) => `<li>${esc(r.text)}${r.ref ? ` <button class="link" data-act="goto" data-v="${r.ref.type === 'record' ? 'rec' : 'mat'}-${r.ref.id}">${r.ref.type === 'record' ? '기록 보기' : '자료 보기'}</button>` : ''}</li>`).join('')}</ul></dd></div>` : ''}</dl>
          ${t.materials.length ? `<details class="fold"><summary>자료 ${t.materials.length}건</summary><ul class="mats">${t.materials.map((m) => `<li id="mat-${m.id}"><span class="tag">${esc(m.kind)}</span><div><b>${esc(m.title)}</b><p>${esc(m.summary)}</p>
            <small>열람: ${m.readers ? m.readers.map((u) => `${esc(u.name)}(${esc(u.team)})`).join(', ') : '이 업무를 볼 수 있는 구성원 모두'}</small></div></li>`).join('')}</ul></details>` : ''}
        </section>
        <section class="panel" id="sec-verify" aria-labelledby="vf-h"><h2 id="vf-h">${t.track === 'check' ? '검수' : '검증 단계'}${t.criteria_version > 1 ? ` <span class="ver">현행 기준 ${t.criteria_version}차</span>` : ''}</h2>
          <ol class="stages ${t.stages.length === 1 ? 'single' : ''}">${t.stages.map((s) => `<li class="sg sg-${s.state}"><div class="sg-top"><b>${esc(s.label)}</b>${stageChip(s)}</div>
            ${s.current && s.state === 'fail' ? `<p class="sg-why">${esc(s.current.summary)}</p>` : ''}
            ${s.current ? `<p class="sg-ev">${proof(s.current)} <button class="link" data-act="goto" data-v="rec-${s.current.record_id}">기록 보기</button></p>` : ''}
            ${s.previous ? `<p class="sg-prev">종전 기준(${s.previous.version}차)에서는 ${esc(s.previous.text)} <button class="link" data-act="goto" data-v="rec-${s.previous.record_id}">기록 보기</button></p>` : ''}</li>`).join('')}</ol>
        </section>
      </div>
      <section class="panel" id="sec-records" aria-labelledby="rc-h"><header class="row"><h2 id="rc-h">기록 <span class="num">${t.records.length}</span></h2>
        ${S.ui.rec ? '' : '<button class="btn" data-act="rec-open">기록 작성</button>'}</header>
        ${S.ui.rec ? recordForm(t) : ''}
        ${t.records.length ? `<ol class="timeline">${t.records.slice(0, 2).map(recItem).join('')}</ol>` : '<p class="muted">아직 기록이 없습니다.</p>'}
        ${t.records.length > 2 ? `<details class="fold"><summary>이전 기록 ${t.records.length - 2}건</summary><ol class="timeline">${t.records.slice(2).map(recItem).join('')}</ol></details>` : ''}</section>`;
    return head + (editing ? editForm(t) : confirming ? confirmPanel(t) : (t.decision_open ? '' : hero) + (t.decision ? decisionPanel(t) : '')) + body;
  }

  /* ───────── 화면: 검색, AI 현황 ───────── */
  function viewSearch(r) {
    if (!r.q) return `<header class="page-head"><h1>검색</h1><p class="lede">위 검색창에 업무 제목, 요구사항, 기록의 낱말을 입력하세요.</p></header>`;
    return `<header class="page-head"><p class="eyebrow">검색</p><h1>‘${esc(r.q)}’ 결과 ${r.results.length}건</h1>
      </header>
      ${r.results.length ? `<ul class="results">${r.results.map((t) => `<li class="card">
        <header><span class="row">${chip(t.status)}${t.failing.map((f) => `<span class="chip sg-fail">${icon('fail')}${esc(f)}</span>`).join('')}</span><span class="crumb">${esc(t.project.name)}</span></header>
        <h3><a href="#task-${t.id}">${esc(t.title)}</a></h3>
        <ul class="hits">${t.hits.filter((h) => h.where !== '제목').map((h) => `<li><span>${esc(h.where)}</span>${esc(h.text).split(new RegExp('(' + esc(r.q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'i')).map((x, n) => (n % 2 ? `<b class="found">${x}</b>` : x)).join('')}</li>`).join('')}</ul>
        <footer class="links"><a class="btn ghost" href="#project-${t.project.id}-${t.requirement.id}">상위 요구사항 보기</a>
          ${t.verify_count ? `<a class="btn ghost" href="#task-${t.id}-verify">검증 이력 보기 <span class="sub">${t.verify_count}건</span></a>` : ''}</footer></li>`).join('')}</ul>`
        : empty('찾은 업무가 없습니다', '다른 낱말로 다시 찾아 보세요. 열람 권한이 없는 업무는 결과에 나오지 않습니다.')}`;
  }
  function viewAgents(a) {
    return `<header class="page-head"><h1>AI 현황</h1>
      </header>
      ${a.agents.length ? `<div class="agents">${a.agents.map((x) => `<article class="panel agent ${x.connected ? '' : 'off'}">
        <header class="row"><div>${who(x.agent, 'plain')}</div><span class="chip ${x.connected ? 'sg-pass' : 'st-hold'}">${icon(x.connected ? 'pass' : 'hold')}연결 상태: ${esc(x.state_text)}</span></header>
        <p class="ag-task"><small>맡은 업무</small><a href="#task-${x.task.id}">${esc(x.task.title)}</a></p>
        ${x.connected ? '' : `<p class="note warn">연결이 끊겨 지금 상태를 확인할 수 없습니다. 아래는 끊기기 전에 확인한 결과입니다.</p>`}
        <div class="ag-grid"><div class="box"><small>마지막으로 확인한 결과</small><p>${esc(x.last_result)}</p><span>확인 시각 ${esc(abs(x.last_result_at))} · ${when(x.last_result_at)}</span></div>
          <div class="box"><small>최근 조회 시각</small><p>${esc(abs(x.last_polled_at))}</p><span>${when(x.last_polled_at)} · ${x.connected ? '조회됨' : '응답 없음'}</span></div></div>
        <footer class="row"><button class="btn" data-act="poll" data-v="${x.agent.id}">지금 다시 조회</button>
          <button class="link demo" data-act="link" data-v="${x.agent.id}" data-on="${x.connected ? 0 : 1}">시연용: ${x.connected ? '연결 끊기' : '연결 복구'}</button></footer></article>`).join('')}</div>`
        : empty('볼 수 있는 AI 작업이 없습니다', '내가 볼 수 있는 업무를 AI가 맡으면 여기에 보입니다.')}`;
  }

  /* ───────── 틀과 라우팅 ───────── */
  function parseHash() {
    const h = location.hash.replace(/^#/, '');
    const p = h.split('-');
    if (p[0] === 'task' && p[1]) return { view: 'task', id: p[1], part: p[2] };
    if (p[0] === 'project' && p[1]) return { view: 'project', id: p[1], focus: p[2] };
    if (p[0] === 'search') return { view: 'search' };
    if (p[0] === 'agents') return { view: 'agents' };
    return { view: 'home' };
  }
  function shell(route, inner) {
    const me = S.users.find((u) => u.id === S.viewer);
    const projs = (S.home && S.home.projects) || [];
    const n = S.home ? S.home.decisions.length + S.home.handoffs.length : 0;
    return `<a class="skip" href="#main">본문으로 건너뛰기</a>
    <aside class="side"><a class="logo" href="#home" aria-label="Thread 홈"><svg viewBox="0 0 34 24" aria-hidden="true"><path d="M2 14c5-12 9 10 14 0s9-10 16 0" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/><circle cx="2.6" cy="14" r="2.6" fill="currentColor"/></svg>Thread</a>
      <nav aria-label="주 메뉴"><a href="#home" class="${route.view === 'home' ? 'on' : ''}">처리할 업무${n ? `<span class="badge">${n}</span>` : ''}</a>
        <p class="nav-h">내 프로젝트</p>${projs.map((p) => `<a href="#project-${p.id}" class="${route.view === 'project' && route.id === p.id ? 'on' : ''}">${esc(p.name)}<span class="frac">${p.progress.done}/${p.progress.total}</span></a>`).join('') || '<span class="muted small nav-empty">없음</span>'}
        <hr><a href="#agents" class="${route.view === 'agents' ? 'on' : ''}">AI 현황</a></nav></aside>
    <div class="main-wrap"><header class="top">
      <form id="search-form" role="search"><label class="sr" for="q">검색</label><input id="q" name="q" type="search" placeholder="업무, 요구사항, 기록 검색" value="${esc(S.q)}" autocomplete="off"><button class="btn" type="submit">검색</button></form>
      <details class="viewer" id="viewer"><summary><small>시연용 · 보는 사람</small>${me ? `<b>${esc(me.name)}</b> ${esc(me.team)}` : ''}</summary>
        <div class="menu"><p>가상 인물로 바꿔 봅니다.</p>
          ${S.users.map((u) => `<button data-act="viewer" data-v="${u.id}" class="${u.id === S.viewer ? 'on' : ''}">${who(u, 'plain')}</button>`).join('')}
          <hr><label class="check"><input type="checkbox" id="drop-next" data-act="drop" ${S.dropNext ? 'checked' : ''}>다음 저장 한 번은 응답 끊기</label>
          <button data-act="reset" class="plain">가상 데이터를 처음 상태로</button></div></details></header>
      <main id="main" tabindex="-1"><div id="flash"></div>${inner}</main></div>`;
  }
  let seq = 0;
  async function render() {
    const my = ++seq, route = parseHash(), app = document.getElementById('app');
    try {
      if (!S.users.length) S.users = (await api('GET', 'users')).users;
      const homeP = api('GET', 'home');
      homeP.then((hm) => { if (my === seq) S.home = hm; }, () => {});
      const [home, data] = await Promise.all([
        homeP,
        route.view === 'task' ? api('GET', 'tasks/' + route.id) : route.view === 'project' ? api('GET', 'projects/' + route.id)
          : route.view === 'search' ? api('GET', 'search?q=' + encodeURIComponent(S.q)) : route.view === 'agents' ? api('GET', 'agents') : null,
      ]);
      if (my !== seq) return;
      S.home = home; S.data = data;
      if (route.view === 'task') { S.task = data.task; if (route.part === 'edit' && !S.ui.mode) startEdit(); }
      const inner = route.view === 'home' ? viewHome(home) : route.view === 'task' ? viewTask(data.task, route.part)
        : route.view === 'project' ? viewProject(data, route.focus) : route.view === 'search' ? viewSearch(data) : viewAgents(data);
      const open = document.getElementById('viewer') && document.getElementById('viewer').open;
      app.innerHTML = shell(route, inner);
      if (open) document.getElementById('viewer').open = true;
      document.title = (route.view === 'task' ? data.task.title : route.view === 'project' ? data.name : route.view === 'search' ? '검색' : route.view === 'agents' ? 'AI 현황' : '처리할 업무') + ' · Thread';
    } catch (e) {
      if (my !== seq) return;
      const gone = e instanceof ApiError && e.status === 404;
      app.innerHTML = shell(route, `<div class="empty big"><h1>${gone ? '찾을 수 없는 페이지입니다' : '내용을 불러오지 못했습니다'}</h1>
        <p>${gone ? '없는 항목이거나 열람 권한이 없습니다.' : '연결 상태를 확인한 뒤 재시도해 주세요.'}</p>
        <p><a class="btn" href="#home">홈으로</a> ${gone ? '' : '<button class="btn primary" data-act="reload">재시도</button>'}</p></div>`);
    }
    drawThreads();
    paintFlash();
    return route;
  }
  function focusPart(route) {
    const id = route.view === 'task' ? ({ verify: 'sec-verify' }[route.part]) : route.view === 'project' && route.focus ? 'req-' + route.focus : null;
    const el = id && document.getElementById(id);
    if (el) el.scrollIntoView({ block: 'start' }); else window.scrollTo(0, 0);
  }
  async function navigate() { S.ui = {}; if (S.pending) await resolvePending(); else if (!S.saving) S.flash = null; const r = await render(); if (r) focusPart(r); }
  window.addEventListener('hashchange', navigate);

  function startEdit() {
    const t = S.task;
    S.ui = { mode: 'edit', base: t, draft: shape(t) };
  }
  function readDraft() {
    const v = (id) => document.getElementById(id).value.trim();
    const st = v('f-status');
    S.ui.draft = { title: v('f-title'), status: st, next_owner_id: v('f-owner'), blocked_reason: ['blocked', 'hold'].includes(st) ? v('f-reason') : '', next_action: v('f-next'), current_state: v('f-state'), done_criteria: v('f-criteria').split('\n').map((s) => s.trim()).filter(Boolean) };
  }
  function keepRec() { const b = document.getElementById('r-body'); if (b) { const v = (id) => { const el = document.getElementById(id); return el ? el.value.trim() : ''; }; S.ui.body = b.value; S.ui.evidence = v('r-evidence'); S.ui.commit = v('r-commit'); S.ui.tp = v('r-tp'); S.ui.tt = v('r-tt'); if (document.getElementById('r-to')) { S.ui.to = v('r-to'); S.ui.act = v('r-act'); } } }
  function goto(id) { const el = document.getElementById(id); if (!el) return; const d = el.closest('details'); if (d) d.open = true; el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); el.classList.remove('flashit'); void el.offsetWidth; el.classList.add('flashit'); }

  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b || b.tagName === 'SELECT' || b.type === 'radio' || b.type === 'checkbox') return;
    const act = b.dataset.act, v = b.dataset.v;
    if ((S.pending || S.saving) && !['retry', 'check', 'goto'].includes(act)) { if (S.saving) S.flash = { type: 'busy', title: '저장하는 중입니다', lines: ['저장이 끝난 뒤 다시 눌러 주세요.'] }; paintFlash(); return; }
    if (act === 'viewer') { S.viewer = v; store.set('thread.viewer', v); S.ui = {}; S.flash = null; document.getElementById('viewer').open = false; if (location.hash && location.hash !== '#home') location.hash = 'home'; else navigate(); }
    else if (act === 'reset') { await api('POST', 'demo/reset', {}, newKey()); document.getElementById('viewer').open = false; navigate(); }
    else if (act === 'reload') navigate();
    else if (act === 'seen') { try { await api('POST', 'home/seen', { until: S.home.since.until }, newKey()); } catch { /* 다음에 다시 누를 수 있다 */ } render(); }
    else if (act === 'filter') { S.ui.filter = v; render(); }
    else if (act === 'edit') { startEdit(); S.flash = null; await render(); document.getElementById('f-title').focus(); }
    else if (act === 'edit-cancel') { S.ui = {}; render(); }
    else if (act === 'edit-back') { S.ui.mode = 'edit'; render(); }
    else if (act === 'edit-save') { const { fields } = draftDiff(S.ui.base, S.ui.draft); save({ method: 'PATCH', path: 'tasks/' + S.ui.base.id, body: { rev: S.ui.base.rev, fields } }); }
    else if (act === 'decide') save({ method: 'POST', path: `tasks/${S.task.id}/decision`, body: { option: S.ui.option, rev: S.task.rev } });
    else if (act === 'rec-open') { S.ui.rec = true; S.ui.cv = S.task.criteria_version; S.ui.rev = S.task.rev; if (v === 'pass') S.ui.after = S.task.assignee && S.task.assignee.id === S.viewer ? 'review' : 'work'; await render(); const el = document.getElementById('rec-form'); el.scrollIntoView({ block: 'center' }); document.getElementById('r-body').focus(); }
    else if (act === 'rec-close') { S.ui.rec = false; render(); }
    else if (act === 'goto') goto(v);
    else if (act === 'retry' && S.pending) { if (S.pending.op) save(S.pending.op); else { await resolvePending(); render(); } }
    else if (act === 'check') { const applied = await resolvePending(); if (applied) S.ui = {}; if (applied !== null) await render(); else paintFlash(); }
    else if (act === 'flash-close') { S.flash = null; S.pending = null; paintFlash(); }
    else if (act === 'poll') { try { await api('POST', `agents/${v}/poll`, {}, newKey()); S.flash = null; } catch { S.flash = { type: 'err', title: '조회 요청을 보내지 못했습니다', lines: ['연결 상태를 확인한 뒤 다시 조회해 주세요.'] }; } render(); }
    else if (act === 'link') { await api('POST', 'demo/agent-link', { id: v, reachable: b.dataset.on === '1' }, newKey()); await api('POST', `agents/${v}/poll`, {}, newKey()); render(); }
  });
  document.addEventListener('change', (e) => {
    const act = e.target.dataset.act;
    if ((S.pending || S.saving) && act) { paintFlash(); return; }
    if (act === 'pick') { S.ui.option = e.target.value; render().then(() => { const el = document.getElementById('opt-' + S.ui.option); if (el) el.focus({ preventScroll: true }); const w = document.getElementById('will'); if (w) w.scrollIntoView({ block: 'nearest' }); }); }
    else if (act === 'rec-kind') { keepRec(); S.ui.kind = e.target.value; render().then(() => document.getElementById('r-kind').focus()); }
    else if (act === 'rec-outcome') { keepRec(); S.ui.outcome = e.target.value; render(); }
    else if (act === 'drop') S.dropNext = e.target.checked;
    else if (e.target.id === 'f-status') { document.getElementById('f-reason-wrap').hidden = !['blocked', 'hold'].includes(e.target.value); document.getElementById('f-reason-l').textContent = e.target.value === 'hold' ? '보류 사유' : '막힘 사유'; }
    else if (act === 'rec-after') { keepRec(); S.ui.after = e.target.value; S.ui.act = null; S.ui.to = ''; render(); }
  });
  document.addEventListener('submit', (e) => {
    e.preventDefault();
    if (e.target.id === 'search-form') { S.q = document.getElementById('q').value.trim(); if (location.hash === '#search') navigate(); else location.hash = 'search'; }
    else if (e.target.id === 'edit-form') {
      readDraft();
      const d = S.ui.draft;
      if (['blocked', 'hold'].includes(d.status) && !d.blocked_reason) { S.flash = { type: 'err', title: `${d.status === 'hold' ? '보류 사유' : '막힘 사유'}를 적어 주세요` }; paintFlash(); document.getElementById('f-reason').focus(); return; }
      S.ui.mode = 'confirm'; S.flash = null; render().then(() => window.scrollTo(0, 0));
    }
    else if (e.target.id === 'rec-form') {
      keepRec();
      const t = S.task, k = t.stages.some((x) => x.key === S.ui.kind) ? S.ui.kind : 'note', stage = k !== 'note';
      const num = (x) => (x === '' || x == null ? null : Number(x));
      const body = { kind: k, outcome: stage && k !== 'impl' ? (S.ui.outcome || 'pass') : null, body: S.ui.body, evidence: S.ui.evidence, criteria_version: S.ui.cv };
      if (stage && t.track === 'build' && (S.ui.commit || S.ui.tp || S.ui.tt)) body.proof = { commit: S.ui.commit, test_passed: num(S.ui.tp), test_total: num(S.ui.tt) };
      if (['work', 'review'].includes(S.ui.after) && t.status !== 'done') { body.handoff = { next_owner_id: S.ui.to, next_action: S.ui.act, purpose: S.ui.after }; body.rev = S.ui.rev; }
      save({ method: 'POST', path: `tasks/${t.id}/records`, body });
    }
  });
  document.addEventListener('click', (e) => { const v = document.getElementById('viewer'); if (v && v.open && !v.contains(e.target)) v.open = false; });

  // 새로 고쳐도 응답을 못 받은 저장이 있었는지 잊지 않는다.
  if (!DEMO && store.getS('thread.pending')) S.pending = { key: store.getS('thread.pending'), sig: null, op: null };
  navigate();
})();
