// Single-origin mode (DESK_INGRESS=railway): the frontend and API share one Railway
// origin, so no proxy credential exists. These tests pin the replacement trust model:
// the caller IP comes only from the right-most X-Forwarded-For entry, client-supplied
// x-desk-* headers are inert, and authentication/CSRF are enforced independently of IP.

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { promises as fs } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { hashPassword, normalizeIp, singleHeaderIp } from '../server/auth';
import { resetCacheMemory } from '../server/cache';
import { fatalProductionProblems, loadConfig, type Ctx } from '../server/config';
import { FileKV } from '../server/kv';
import { createHandler } from '../server/node-http';
import { listRoutes } from '../server/router';
import { SECURITY_HEADERS } from '../server/static';

const PASSWORD = 'correct horse battery staple';
const ORIGIN = 'https://desk.example.up.railway.app';
let hash = '';
beforeAll(async () => {
  hash = await hashPassword(PASSWORD, 100_000);
});
beforeEach(() => resetCacheMemory());

const env = (dir: string, extra: Record<string, string> = {}) => ({
  NODE_ENV: 'production',
  SESSION_SECRET: 's'.repeat(40),
  APP_PASSWORD_HASH: hash,
  DESK_DATA_DIR: dir,
  DESK_PUBLIC_ORIGIN: ORIGIN,
  DESK_INGRESS: 'railway',
  DESK_SCHEDULER: 'off',
  ...extra,
});

describe('single-origin production configuration', () => {
  it('accepts railway ingress with no proxy secret', () => {
    const cfg = loadConfig(env('/data/desk'));
    expect(cfg.ingress).toBe('railway');
    expect(cfg.proxySecret).toBeNull();
    expect(fatalProductionProblems(cfg)).toEqual([]);
  });
  it('refuses to mix railway ingress with a proxy secret', () => {
    expect(fatalProductionProblems(loadConfig(env('/data/desk', { DESK_PROXY_SECRET: 'p'.repeat(40) }))).join(' ')).toMatch(/mutually exclusive/);
  });
  it('refuses production with no trusted ingress at all', () => {
    expect(fatalProductionProblems(loadConfig(env('/data/desk', { DESK_INGRESS: '' }))).join(' ')).toMatch(/DESK_INGRESS=railway/);
  });
  it('keeps the legacy proxy mode valid on its own', () => {
    const cfg = loadConfig(env('/data/desk', { DESK_INGRESS: '', DESK_PROXY_SECRET: 'p'.repeat(40) }));
    expect(cfg.ingress).toBe('edge-proxy');
    expect(fatalProductionProblems(cfg)).toEqual([]);
  });
});

describe('client address handling', () => {
  it('accepts only a single valid IP from the platform header', () => {
    expect(singleHeaderIp('203.0.113.9')).toBe('203.0.113.9');
    expect(singleHeaderIp(' 2001:db8::1 ')).toBe('2001:db8::1');
    for (const bad of ['6.6.6.6, 7.7.7.7', 'not-an-ip', '', undefined, ['1.1.1.1', '2.2.2.2']]) expect(singleHeaderIp(bad as string), String(bad)).toBeNull();
  });
  it('collapses IPv6 to the /64 and unwraps IPv4-mapped addresses', () => {
    expect(normalizeIp('2001:db8:1:2:aaaa:bbbb:cccc:dddd')).toBe('2001:0db8:0001:0002::/64');
    expect(normalizeIp('2001:db8:1:2::1')).toBe('2001:0db8:0001:0002::/64');
    expect(normalizeIp('2001:DB8:1:2:ffff::')).toBe('2001:0db8:0001:0002::/64');
    expect(normalizeIp('::ffff:203.0.113.9')).toBe('203.0.113.9');
    expect(normalizeIp('203.0.113.9')).toBe('203.0.113.9');
    expect(normalizeIp('garbage')).toBe('unknown');
  });
});

interface Running {
  server: Server;
  base: string;
  close: () => Promise<void>;
}

let webDir = '';
beforeAll(async () => {
  webDir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-web-'));
  await fs.mkdir(path.join(webDir, 'assets'));
  await fs.writeFile(path.join(webDir, 'index.html'), '<!doctype html><title>desk</title>');
  await fs.writeFile(path.join(webDir, 'assets', 'app-abc.js'), 'console.log(1)');
  await fs.writeFile(path.join(webDir, 'secret.bin'), 'x');
});
afterAll(async () => fs.rm(webDir, { recursive: true, force: true }));

async function start(dir: string): Promise<Running> {
  const cfg = loadConfig(env(dir));
  cfg.offline = true;
  const ctx: Ctx = { cfg, kv: new FileKV(dir), now: () => new Date() };
  const server = createServer(createHandler(async () => ctx, { requireProxyKey: false, platformIpHeader: 'x-real-ip', webRoot: webDir }));
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${(server.address() as AddressInfo).port}`, close: () => new Promise((r) => server.close(() => r())) };
}

describe('single-origin server', () => {
  let dir: string;
  let api: Running;
  beforeEach(async () => {
    dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-so-'));
    api = await start(dir);
  });
  afterEach(async () => {
    await api.close();
    await fs.rm(dir, { recursive: true, force: true });
  });

  const login = (ip: string, headers: Record<string, string> = {}, password = PASSWORD) =>
    fetch(`${api.base}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json', origin: ORIGIN, 'x-real-ip': ip, ...headers }, body: JSON.stringify({ password }) });

  it('serves the app with the full security header set and correct caching', async () => {
    const res = await fetch(`${api.base}/`);
    expect(res.status).toBe(200);
    expect(await res.text()).toContain('<title>desk</title>');
    for (const [k, v] of Object.entries(SECURITY_HEADERS)) expect(res.headers.get(k), k).toBe(v);
    expect(res.headers.get('cache-control')).toBe('no-cache');
    const asset = await fetch(`${api.base}/assets/app-abc.js`);
    expect(asset.headers.get('cache-control')).toContain('immutable');
    expect(asset.headers.get('content-type')).toContain('javascript');
  });

  it('keeps the header set in step with netlify.toml', async () => {
    const toml = await fs.readFile(path.join(__dirname, '..', 'netlify.toml'), 'utf8');
    const block = toml.split('[[headers]]').find((b) => /for = "\/\*"/.test(b)) ?? '';
    for (const [k, v] of Object.entries(SECURITY_HEADERS)) {
      if (k === 'Strict-Transport-Security') continue; // platform-specific max-age/preload
      expect(block, k).toContain(`${k} = "${v}"`);
    }
  });

  it('never serves files outside the web root or of unknown type', async () => {
    for (const p of ['/..%2f..%2fpackage.json', '/%2e%2e/package.json', '/assets/..%2f..%2fpackage.json', '/secret.bin', '/%00', '/nope.js', '/..%5cpackage.json']) {
      const r = await fetch(`${api.base}${p}`);
      expect(r.status, p).toBe(404);
    }
    expect((await fetch(`${api.base}/`, { method: 'POST' })).status).toBe(405);
  });

  it('requires a session on every private route and gives proxy headers no effect', async () => {
    const routes = listRoutes();
    const concrete = (p: string) => p.replace(':symbol', 'TCS').replace(':id', 'NIFTY').replace(':kind', 'pre');
    for (const r of routes) {
      if (r.public) continue;
      const res = await fetch(`${api.base}${concrete(r.path)}`, {
        method: r.method,
        headers: { origin: ORIGIN, 'content-type': 'application/json', 'x-desk-proxy-key': 'x'.repeat(40) },
        body: r.method === 'GET' ? undefined : '{}',
      });
      expect(res.status, `${r.method} ${r.path}`).toBe(401);
    }
  });

  it('rejects cross-origin and origin-less writes even with a valid session', async () => {
    const cookie = (await login('198.51.100.10')).headers.get('set-cookie')!.split(';')[0]!;
    const trade = JSON.stringify({ symbol: 'TCS', segment: 'EQ_DELIVERY', side: 'LONG', qty: 1, entryPrice: 100, entryAt: '2026-10-01T04:00:00.000Z' });
    const post = (headers: Record<string, string>) => fetch(`${api.base}/api/trades`, { method: 'POST', headers: { 'content-type': 'application/json', cookie, ...headers }, body: trade });
    expect((await post({ origin: 'https://evil.example' })).status).toBe(403);
    expect((await post({ origin: `${ORIGIN}.evil.example` })).status).toBe(403);
    expect((await post({})).status).toBe(403);
    expect((await post({ origin: ORIGIN })).status).toBe(201);
  });

  it('locks out by the platform-set address; x-forwarded-for and x-desk-client-ip cannot rotate identity', async () => {
    for (let i = 0; i < 5; i++) {
      const r = await login('198.51.100.66', { 'x-forwarded-for': `10.0.0.${i}`, 'x-desk-client-ip': `10.9.9.${i}` }, 'wrong');
      expect(r.status).toBe(401);
    }
    expect((await login('198.51.100.66', { 'x-forwarded-for': '1.2.3.4', 'x-desk-client-ip': '8.8.8.8' })).status).toBe(429);
    // the owner at another address is unaffected
    expect((await login('203.0.113.9')).status).toBe(200);
  }, 30_000);

  it('treats a whole IPv6 /64 as one client', async () => {
    for (let i = 0; i < 5; i++) expect((await login(`2001:db8:1:2:${i}::${i + 1}`, {}, 'wrong')).status).toBe(401);
    expect((await login('2001:db8:1:2:ffff:ffff:ffff:ffff')).status).toBe(429);
    expect((await login('2001:db8:1:3::1')).status).toBe(200);
  }, 30_000);
});
