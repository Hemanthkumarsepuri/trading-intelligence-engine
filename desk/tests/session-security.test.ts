import { createHmac } from 'node:crypto';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { beforeAll, describe, expect, it } from 'vitest';
import { clearFailures, hashPassword, issueToken, lockStatus, pruneRevocations, reserveAttempt, revokeSession, verifyToken } from '../server/auth';
import { loadConfig, type Ctx } from '../server/config';
import { FileKV, MemoryKV, type KV } from '../server/kv';
import { handle } from '../server/router';

const START = new Date('2026-10-12T05:00:00.000Z');
const PASSWORD = 'correct horse battery staple';
const ORIGIN = 'https://desk.example.netlify.app';
const SECRET = 's'.repeat(40);

let hash = '';
beforeAll(async () => {
  hash = await hashPassword(PASSWORD, 100_000);
});

/** KV whose failures can be switched on for keys under a prefix. */
class FlakyKV implements KV {
  failGet = false;
  failPut = false;
  constructor(private inner: KV, private prefix = 'auth/revoked/') {}
  get<T>(key: string) {
    if (this.failGet && key.startsWith(this.prefix)) return Promise.reject(new Error('disk error'));
    return this.inner.get<T>(key);
  }
  put<T>(key: string, value: T, expect: Parameters<KV['put']>[2]) {
    if (this.failPut && key.startsWith(this.prefix)) return Promise.reject(new Error('disk full'));
    return this.inner.put(key, value, expect);
  }
  del(key: string) {
    return this.inner.del(key);
  }
  list(prefix: string) {
    return this.inner.list(prefix);
  }
}

interface Harness {
  ctx: Ctx;
  clock: { now: Date };
  call: (method: string, p: string, o?: { cookie?: string; body?: unknown }) => Promise<Response>;
  login: () => Promise<string>;
}

function harness(kv: KV, clock = { now: new Date(START) }): Harness {
  const ctx: Ctx = {
    cfg: loadConfig({ SESSION_SECRET: SECRET, APP_PASSWORD_HASH: hash, DESK_OFFLINE: '1', DESK_STORAGE: 'memory' }),
    kv,
    now: () => clock.now,
  };
  const call: Harness['call'] = (method, p, o = {}) => {
    const headers: Record<string, string> = {};
    if (o.cookie) headers.cookie = o.cookie;
    if (method !== 'GET') {
      headers.origin = ORIGIN;
      headers['content-type'] = 'application/json';
    }
    return handle(new Request(`${ORIGIN}${p}`, { method, headers, body: method === 'GET' ? undefined : JSON.stringify(o.body ?? {}) }), ctx);
  };
  const login = async () => {
    const res = await call('POST', '/api/auth/login', { body: { password: PASSWORD } });
    expect(res.status).toBe(200);
    return (res.headers.get('set-cookie') ?? '').split(';')[0]!;
  };
  return { ctx, clock, call, login };
}

describe('server-side session revocation', () => {
  it('a valid session works, logout revokes it, and the same cookie is then refused everywhere', async () => {
    const h = harness(new MemoryKV());
    const cookie = await h.login();
    expect((await h.call('GET', '/api/settings', { cookie })).status).toBe(200);
    expect(((await (await h.call('GET', '/api/auth/me', { cookie })).json()) as any).data.authenticated).toBe(true);

    const out = await h.call('POST', '/api/auth/logout', { cookie });
    expect(out.status).toBe(200);

    const after = await h.call('GET', '/api/settings', { cookie }); // replaying the stolen/old cookie
    expect(after.status).toBe(401);
    expect(((await after.json()) as any).error.code).toBe('UNAUTHENTICATED');
    expect(((await (await h.call('GET', '/api/auth/me', { cookie })).json()) as any).data.authenticated).toBe(false);
    expect((await h.call('POST', '/api/trades', { cookie, body: {} })).status).toBe(401);
  });

  it('keeps the cookie hardening on logout (cleared, HttpOnly, Secure, SameSite=Strict, __Host- prefix)', async () => {
    const h = harness(new MemoryKV());
    const cookie = await h.login();
    const c = (await h.call('POST', '/api/auth/logout', { cookie })).headers.get('set-cookie') ?? '';
    expect(c).toMatch(/^__Host-desk_session=;/);
    for (const part of ['Max-Age=0', 'HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/']) expect(c).toContain(part);
  });

  it('does not touch unrelated sessions', async () => {
    const h = harness(new MemoryKV());
    const a = await h.login();
    const b = await h.login(); // same second: distinguishable only by the per-session id
    expect(a).not.toBe(b);
    await h.call('POST', '/api/auth/logout', { cookie: a });
    expect((await h.call('GET', '/api/settings', { cookie: a })).status).toBe(401);
    expect((await h.call('GET', '/api/settings', { cookie: b })).status).toBe(200);
  });

  it('still rejects expired sessions and bad signatures', async () => {
    const h = harness(new MemoryKV());
    const cookie = await h.login();
    const tampered = cookie.replace(/\.[^.]+$/, '.AAAA');
    expect((await h.call('GET', '/api/settings', { cookie: tampered })).status).toBe(401);
    h.clock.now = new Date(START.getTime() + 15 * 86_400_000); // past the 14-day lifetime
    expect((await h.call('GET', '/api/settings', { cookie })).status).toBe(401);
  });

  it('rejects a correctly signed token that has no session id (it could not be revoked)', () => {
    const body = Buffer.from(JSON.stringify({ sub: 'owner', iat: 1, exp: 9_999_999_999 })).toString('base64url');
    const sig = createHmac('sha256', SECRET).update(body).digest('base64url');
    expect(verifyToken(`${body}.${sig}`, SECRET, START)).toBeNull();
    expect(verifyToken(issueToken(SECRET, START).token, SECRET, START)).toMatchObject({ sub: 'owner' });
  });

  it('survives a process restart (revocation is persisted, not in memory)', async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-revoke-'));
    const first = harness(new FileKV(dir));
    const cookie = await first.login();
    expect((await first.call('POST', '/api/auth/logout', { cookie })).status).toBe(200);

    const restarted = harness(new FileKV(dir)); // brand new instance over the same volume
    expect((await restarted.call('GET', '/api/settings', { cookie })).status).toBe(401);
    const files = await fs.readdir(dir);
    expect(files.some((f) => f.startsWith('auth%2Frevoked%2F'))).toBe(true);
    expect(files.join(' ')).not.toContain(cookie.split('=')[1]!.slice(0, 20)); // the token itself is never stored
  });

  it('fails closed when the revocation list cannot be read', async () => {
    const kv = new FlakyKV(new MemoryKV());
    const h = harness(kv);
    const cookie = await h.login();
    kv.failGet = true;
    const res = await h.call('GET', '/api/settings', { cookie });
    expect(res.status).toBe(503);
    expect(((await res.json()) as any).error.code).toBe('AUTH_UNAVAILABLE');
    expect((await h.call('GET', '/api/auth/me', { cookie })).status).toBe(503);
    kv.failGet = false;
    expect((await h.call('GET', '/api/settings', { cookie })).status).toBe(200); // recovers when storage does
  });

  it('does not report a logout as successful when the revocation write fails', async () => {
    const kv = new FlakyKV(new MemoryKV());
    const h = harness(kv);
    const cookie = await h.login();
    kv.failPut = true;
    const out = await h.call('POST', '/api/auth/logout', { cookie });
    expect(out.status).toBe(503);
    expect(((await out.json()) as any).error.code).toBe('LOGOUT_NOT_REVOKED');
    expect(out.headers.get('set-cookie')).toContain('Max-Age=0'); // the browser is still signed out
    kv.failPut = false;
    expect((await h.call('POST', '/api/auth/logout', { cookie })).status).toBe(200); // retry works
    expect((await h.call('GET', '/api/settings', { cookie })).status).toBe(401);
  });

  it('handles concurrent logouts of one session and of different sessions', async () => {
    const h = harness(new MemoryKV());
    const a = await h.login();
    const b = await h.login();
    const c = await h.login();
    const res = await Promise.all([...Array(8)].map(() => h.call('POST', '/api/auth/logout', { cookie: a })).concat(h.call('POST', '/api/auth/logout', { cookie: b })));
    expect(res.every((r) => r.status === 200)).toBe(true);
    expect((await h.call('GET', '/api/settings', { cookie: a })).status).toBe(401);
    expect((await h.call('GET', '/api/settings', { cookie: b })).status).toBe(401);
    expect((await h.call('GET', '/api/settings', { cookie: c })).status).toBe(200);
  });

  it('logout without a valid session is harmless and writes nothing', async () => {
    const kv = new MemoryKV();
    const h = harness(kv);
    expect((await h.call('POST', '/api/auth/logout')).status).toBe(200);
    expect((await h.call('POST', '/api/auth/logout', { cookie: '__Host-desk_session=garbage.sig' })).status).toBe(200);
    expect(await kv.list('auth/revoked/')).toEqual([]);
  });
});

describe('revocation pruning', () => {
  const session = (exp: number, jti: string) => ({ sub: 'owner' as const, iat: exp - 1000, exp, jti });
  it('removes only records whose session has expired (with a 60 s grace) and never an active one', async () => {
    const kv = new MemoryKV();
    const now = new Date('2026-10-12T05:00:00.000Z');
    const t = Math.floor(now.getTime() / 1000);
    await revokeSession(kv, session(t - 3600, 'expired-session-aaaa'));
    await revokeSession(kv, session(t - 30, 'inside-grace-session-b')); // expired 30 s ago: kept for clock skew
    await revokeSession(kv, session(t + 3600, 'active-session-cccc'));
    await revokeSession(kv, session(t + 13 * 86400, 'long-lived-session-d'));
    expect(await pruneRevocations(kv, now)).toBe(1);
    expect((await kv.list('auth/revoked/')).length).toBe(3);
    // later, once the grace has passed and the first active one has expired, it goes too
    expect(await pruneRevocations(kv, new Date(now.getTime() + 3600_000 + 61_000))).toBe(2);
    expect((await kv.list('auth/revoked/')).length).toBe(1);
  });
  it('skips unreadable records instead of failing or deleting them', async () => {
    const inner = new MemoryKV();
    const kv = new FlakyKV(inner);
    await inner.put('auth/revoked/x', { exp: 1 }, '*');
    kv.failGet = true;
    expect(await pruneRevocations(kv, START)).toBe(0);
    expect(await inner.list('auth/revoked/')).toEqual(['auth/revoked/x']);
  });
});

describe('login lockout under concurrency', () => {
  it('admits exactly five of twenty-five simultaneous attempts', async () => {
    const kv = new MemoryKV();
    const r = await Promise.all([...Array(25)].map(() => reserveAttempt(kv, 'ip', START)));
    expect(r.filter((x) => x.allowed)).toHaveLength(5);
    expect(r.filter((x) => !x.allowed)).toHaveLength(20);
    expect(r.filter((x) => !x.allowed).every((x) => x.retryAfterSec > 0 && x.retryAfterSec <= 900)).toBe(true);
    expect((await lockStatus(kv, 'ip', START)).locked).toBe(true);
  });
  it('holds across a restart-style fresh KV view of the same storage', async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-lock-'));
    await Promise.all([...Array(7)].map(() => reserveAttempt(new FileKV(dir), 'ip', START)));
    expect((await reserveAttempt(new FileKV(dir), 'ip', START)).allowed).toBe(false);
  });
  it('does not let one client lock out another', async () => {
    const kv = new MemoryKV();
    await Promise.all([...Array(10)].map(() => reserveAttempt(kv, 'attacker', START)));
    expect((await reserveAttempt(kv, 'victim', START)).allowed).toBe(true);
  });
  it('unlocks after 15 minutes with a fresh window, and a correct login clears the count', async () => {
    const kv = new MemoryKV();
    for (let i = 0; i < 5; i++) await reserveAttempt(kv, 'ip', START);
    expect((await reserveAttempt(kv, 'ip', new Date(START.getTime() + 14 * 60_000))).allowed).toBe(false);
    const later = new Date(START.getTime() + 15 * 60_000 + 1000);
    for (let i = 0; i < 5; i++) expect((await reserveAttempt(kv, 'ip', later)).allowed).toBe(true); // a whole new budget of five
    expect((await reserveAttempt(kv, 'ip', later)).allowed).toBe(false);
    await clearFailures(kv, 'ip');
    expect((await reserveAttempt(kv, 'ip', later)).allowed).toBe(true);
  });
  it('resets a partial count after fifteen quiet minutes', async () => {
    const kv = new MemoryKV();
    for (let i = 0; i < 4; i++) await reserveAttempt(kv, 'ip', START);
    const later = new Date(START.getTime() + 16 * 60_000);
    for (let i = 0; i < 5; i++) expect((await reserveAttempt(kv, 'ip', later)).allowed).toBe(true);
    expect((await reserveAttempt(kv, 'ip', later)).allowed).toBe(false);
  });
  it('over HTTP: twelve parallel wrong passwords yield exactly five 401s and seven 429s, then the right password is refused', async () => {
    const h = harness(new MemoryKV());
    const res = await Promise.all([...Array(12)].map(() => h.call('POST', '/api/auth/login', { body: { password: 'wrong-password' } })));
    expect(res.filter((r) => r.status === 401)).toHaveLength(5);
    expect(res.filter((r) => r.status === 429)).toHaveLength(7);
    expect((await h.call('POST', '/api/auth/login', { body: { password: PASSWORD } })).status).toBe(429);
  }, 20_000);
  it('over HTTP: a correct login among parallel guesses still succeeds if it is admitted, and success resets the counter', async () => {
    const h = harness(new MemoryKV());
    for (let i = 0; i < 3; i++) await h.call('POST', '/api/auth/login', { body: { password: 'wrong-password' } });
    expect((await h.call('POST', '/api/auth/login', { body: { password: PASSWORD } })).status).toBe(200);
    const res = await Promise.all([...Array(6)].map(() => h.call('POST', '/api/auth/login', { body: { password: 'wrong-password' } })));
    expect(res.filter((r) => r.status === 401)).toHaveLength(5); // a fresh budget, not the leftover 2
  }, 20_000);
});
