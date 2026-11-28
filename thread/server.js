#!/usr/bin/env node
/* Thread 서버. 의존성 없음: node thread/server.js  →  http://127.0.0.1:8770 */
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const core = require('./core.js');

const PORT = Number(process.env.THREAD_PORT || 8770);
const DATA = process.env.THREAD_DATA || path.join(__dirname, 'data', 'state.json');
const PUBLIC = path.join(__dirname, 'public');
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' };

let state;
try { state = JSON.parse(fs.readFileSync(DATA, 'utf8')); } catch { state = core.seed(); }
function persist() {
  fs.mkdirSync(path.dirname(DATA), { recursive: true });
  fs.writeFileSync(DATA + '.tmp', JSON.stringify(state));
  fs.renameSync(DATA + '.tmp', DATA);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let buf = '';
    req.on('data', (c) => { buf += c; if (buf.length > 1e6) reject(new Error('too large')); });
    req.on('end', () => { if (!buf) return resolve(null); try { resolve(JSON.parse(buf)); } catch (e) { reject(e); } });
    req.on('error', reject);
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname.startsWith('/api/')) {
    let body = null;
    try { body = await readBody(req); } catch {
      res.writeHead(400, { 'content-type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify({ error: '요청 형식이 올바르지 않습니다.' }));
    }
    // Node는 요청을 하나씩 처리하므로 handle() 한 번이 곧 하나의 저장 단위다.
    const out = core.handle(state, {
      method: req.method, path: url.pathname.slice(5), query: Object.fromEntries(url.searchParams),
      userId: req.headers['x-thread-user'], idemKey: req.headers['idempotency-key'], body,
    });
    if (req.method !== 'GET') persist();
    // 시연용: 저장은 끝냈지만 응답이 끊긴 상황을 만든다.
    if (req.method !== 'GET' && req.headers['x-demo-drop-response'] === '1') return req.socket.destroy();
    res.writeHead(out.status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
    return res.end(JSON.stringify(out.body));
  }
  let file = url.pathname === '/' ? '/index.html' : url.pathname;
  const full = file === '/core.js' ? path.join(__dirname, 'core.js') : path.join(PUBLIC, path.normalize(file));
  if (!full.startsWith(__dirname) || !fs.existsSync(full) || fs.statSync(full).isDirectory()) { res.writeHead(404); return res.end('Not found'); }
  res.writeHead(200, { 'content-type': TYPES[path.extname(full)] || 'application/octet-stream', 'cache-control': path.extname(full) === '.woff2' ? 'max-age=86400' : 'no-store' });
  fs.createReadStream(full).pipe(res);
});
server.listen(PORT, '127.0.0.1', () => console.log(`Thread: http://127.0.0.1:${PORT}`));
