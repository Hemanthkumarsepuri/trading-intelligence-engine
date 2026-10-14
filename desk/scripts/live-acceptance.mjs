#!/usr/bin/env node
// Live acceptance checks for a deployed TIRE Desk (single-origin mode).
//
//   node scripts/live-acceptance.mjs                      unauthenticated checks only
//   DESK_PASSWORD=... node scripts/live-acceptance.mjs    + authenticated checks (read from the environment only)
//   DESK_BASE=https://host  (default: the staging API origin from release/staging.json)
//
// Uses synthetic records only and removes them. Never prints the password, cookies or
// response bodies that could contain secrets. Exit code 1 if any check fails.
// Does NOT exercise the login lockout (that would lock the caller's IP for 15 minutes);
// use --lockout to run it explicitly with deliberately wrong passwords.

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const cfg = JSON.parse(readFileSync(path.join(here, '..', 'release', 'staging.json'), 'utf8'));
const BASE = (process.env.DESK_BASE ?? cfg.railway.origin).replace(/\/$/, '');
const PW = process.env.DESK_PASSWORD ?? '';
const out = [];
const rec = (id, ok, detail = '') => out.push({ ok, line: `${ok ? 'PASS' : 'FAIL'} ${id.padEnd(52)} ${detail}` });

async function call(method, p, { cookie, body, origin = BASE, headers = {} } = {}) {
  const h = { accept: 'application/json', ...headers };
  if (cookie) h.cookie = cookie;
  if (body !== undefined) h['content-type'] = 'application/json';
  if (origin && method !== 'GET') h.origin = origin;
  const r = await fetch(BASE + p, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), redirect: 'manual' });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* not JSON */ }
  return { status: r.status, json, text, headers: r.headers, setCookie: r.headers.getSetCookie?.() ?? [] };
}
const sessionCookie = (sc) => sc.map((c) => c.split(';')[0]).find((c) => /desk_session=./.test(c)) ?? null;

// ---- unauthenticated ---------------------------------------------------------------------------------------------
const health = await call('GET', '/api/health');
rec('health 200', health.status === 200 && health.json?.data?.ok === true, String(health.status));
const me = await call('GET', '/api/auth/me');
rec('auth/me reports configured, not demo', me.status === 200 && me.json?.data?.configured === true && me.json?.data?.demo === false, `${me.status}`);

for (const p of ['/api/overview', '/api/settings', '/api/journal', '/api/export', '/api/status', '/api/portfolio', '/api/alerts', '/api/watchlist', '/api/options/NIFTY/expiries', '/api/backup/offsite/verify']) {
  const r = await call('GET', p, { headers: { 'x-desk-proxy-key': 'x'.repeat(48), 'x-desk-client-ip': '1.2.3.4' } });
  rec(`unauthenticated GET ${p}`, r.status === 401, String(r.status));
}
for (const p of ['/api/trades', '/api/import', '/api/backup/run', '/api/backup/offsite/run']) {
  const r = await call('POST', p, { body: {} });
  rec(`unauthenticated POST ${p}`, r.status === 401, String(r.status));
}

const page = await call('GET', '/');
const needHeaders = { 'content-security-policy': /default-src 'self'/, 'x-frame-options': /DENY/i, 'x-content-type-options': /nosniff/i, 'strict-transport-security': /max-age=/, 'referrer-policy': /no-referrer/ };
rec('app served with security headers', page.status === 200 && Object.entries(needHeaders).every(([k, re]) => re.test(page.headers.get(k) ?? '')), String(page.status));

const assets = [...page.text.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
const forbidden = /(DESK_PROXY_SECRET|SESSION_SECRET|APP_PASSWORD_HASH|BACKUP_ENCRYPTION_KEY|BACKUP_S3_SECRET|TELEGRAM_BOT_TOKEN|UPSTOX_ANALYTICS_TOKEN|\.up\.railway\.app)/;
let leaked = 0;
for (const a of assets) leaked += forbidden.test((await call('GET', a)).text) ? 1 : 0;
rec(`served bundle (${assets.length} assets) holds no secret name or Railway host`, assets.length > 0 && leaked === 0, `leaks=${leaked}`);

for (const p of ['/..%2f..%2fpackage.json', '/.env.example', '/dist-server/main.cjs', '/server/main.ts', '/package.json']) {
  const r = await fetch(BASE + p, { redirect: 'manual' });
  rec(`no source or config exposed: ${p}`, r.status === 404, String(r.status));
}
const foreign = await call('POST', '/api/auth/login', { body: { password: 'x' }, origin: 'https://evil.example' });
rec('login from a foreign origin refused', foreign.status === 403, String(foreign.status));

// ---- lockout (explicit opt-in) -----------------------------------------------------------------------------------
if (process.argv.includes('--lockout')) {
  const codes = [];
  for (let i = 1; i <= 7; i++) {
    codes.push((await call('POST', '/api/auth/login', { body: { password: 'wrong-probe-password' }, headers: { 'x-forwarded-for': `203.0.113.${i}`, 'x-real-ip': `198.51.100.${i}`, 'x-desk-client-ip': `192.0.2.${i}` } })).status);
  }
  rec('lockout holds despite rotating forged IP headers', codes.slice(0, 5).every((c) => c === 401) && codes.slice(5).every((c) => c === 429), codes.join(','));
}

// ---- authenticated -------------------------------------------------------------------------------------------------
if (PW) {
  const bad = await call('POST', '/api/auth/login', { body: { password: `${PW}-wrong` } });
  rec('wrong password refused, no cookie', bad.status === 401 && !sessionCookie(bad.setCookie), String(bad.status));
  const lg = await call('POST', '/api/auth/login', { body: { password: PW } });
  const cookie = sessionCookie(lg.setCookie);
  const flags = lg.setCookie.join(';');
  rec('login 200 with __Host- HttpOnly Secure SameSite=Strict cookie', lg.status === 200 && /__Host-desk_session=/.test(flags) && /HttpOnly/i.test(flags) && /Secure/i.test(flags) && /SameSite=Strict/i.test(flags) && !/Domain=/i.test(flags), String(lg.status));
  if (!cookie) {
    rec('cannot continue without a session', false);
  } else {
    const s = { cookie };
    rec('session recognised', (await call('GET', '/api/auth/me', s)).json?.data?.authenticated === true);
    const trade = { symbol: 'SYNTHTEST', segment: 'EQ_DELIVERY', side: 'LONG', qty: 1, entryPrice: 100, entryAt: '2026-10-01T04:00:00.000Z' };
    rec('write from foreign origin refused (CSRF)', (await call('POST', '/api/trades', { ...s, body: trade, origin: 'https://evil.example' })).status === 403);
    rec('write without Origin refused', (await call('POST', '/api/trades', { ...s, body: trade, origin: null })).status === 403);
    const created = await call('POST', '/api/trades', { ...s, body: trade });
    rec('synthetic trade created', created.status === 201, String(created.status));
    const id = created.json?.data?.id;
    const ex = await call('GET', '/api/export', s);
    rec('export holds the trade and no secret', ex.status === 200 && ex.text.includes('SYNTHTEST') && !/(SESSION_SECRET|APP_PASSWORD|pbkdf2|BACKUP_ENCRYPTION)/i.test(ex.text));
    const run = await call('POST', '/api/backup/run', { ...s, body: {} });
    rec('local backup runs', run.status === 200 && /^backup\/daily-/.test(run.json?.data?.key ?? ''), String(run.status));
    const ver = await call('GET', `/api/backup/verify?key=${encodeURIComponent(run.json?.data?.key ?? '')}`, s);
    rec('local backup verifies (counts match)', ver.json?.data?.ok === true, JSON.stringify(ver.json?.data?.counts ?? {}));
    if (id) rec('synthetic trade removed', (await call('DELETE', `/api/trades/${id}`, s)).status === 200);

    const st = await call('GET', '/api/status', s);
    const sch = st.json?.data?.scheduler;
    rec('scheduler enabled, healthy, one instance', sch?.enabled === true && sch?.healthy === true && typeof sch?.instance === 'string', `instance bound=${Boolean(sch?.instance)}`);
    const off = st.json?.data?.offsite;
    console.log(`INFO offsite configured=${off?.configured} lastOkAt=${off?.lastOkAt ?? '-'} lastError=${off?.lastError ?? '-'} failures=${off?.consecutiveFailures ?? '-'}`);
    if (off?.configured) {
      const o = await call('POST', '/api/backup/offsite/run', { ...s, body: {} });
      rec('off-site backup uploaded and read back', o.status === 200 && o.json?.data?.skipped === false, `${o.status} ${o.json?.error?.message ?? ''}`);
      const v = await call('GET', '/api/backup/offsite/verify', s);
      rec('off-site newest object decrypts and validates', v.json?.data?.ok === true, JSON.stringify(v.json?.data?.counts ?? {}));
    }

    for (const u of ['NIFTY', 'BANKNIFTY']) {
      const e = await call('GET', `/api/options/${u}/expiries`, s);
      const first = e.json?.data?.expiries?.[0];
      const c = first ? await call('GET', `/api/options/${u}/chain?expiry=${first}`, s) : null;
      const rows = c?.json?.data?.rows ?? [];
      const nums = rows.flatMap((r) => [r.ce.oi, r.pe.oi, r.ce.ltp, r.pe.ltp]);
      rec(`${u} option chain live from NSE`, e.status === 200 && rows.length > 0 && c.json?.data?.source === 'nse' && nums.every((v) => v === null || (typeof v === 'number' && Number.isFinite(v))), `rows=${rows.length}`);
    }
    const unlisted = await call('GET', '/api/options/NIFTY/chain?expiry=2031-01-02', s);
    rec('unlisted expiry -> honest error, no rows, no HTML', unlisted.status >= 400 && !unlisted.json?.data?.rows && !unlisted.text.includes('<html'), String(unlisted.status));

    const tampered = cookie.slice(0, -2) + (cookie.endsWith('AA') ? 'BB' : 'AA');
    rec('tampered session cookie refused', (await call('GET', '/api/settings', { cookie: tampered })).status === 401);
    await call('POST', '/api/auth/logout', { ...s, body: {} });
    rec('logout revokes the session (replay refused)', (await call('GET', '/api/settings', s)).status === 401);
  }
} else {
  console.log('INFO no DESK_PASSWORD: authenticated checks NOT RUN');
}

for (const r of out) console.log(r.line);
const failed = out.filter((r) => !r.ok).length;
console.log(`\n${out.length - failed}/${out.length} passed${PW ? '' : ' (unauthenticated only)'}`);
process.exit(failed ? 1 : 0);
