// Single-owner authentication: PBKDF2-hashed password, HMAC-signed session
// cookie (HttpOnly, SameSite=Strict, Secure), per-IP login throttling, and
// server-side session revocation (logout) backed by the persistent KV.
// This closes TIRE forensic defect H-1 (no API authentication) for this app.

import { createHash, createHmac, pbkdf2, randomBytes, timingSafeEqual } from 'node:crypto';
import { isIP } from 'node:net';
import { promisify } from 'node:util';
import type { Config } from './config';
import { update, type KV } from './kv';

const pbkdf2Async = promisify(pbkdf2);
const ITERATIONS = 600_000; // OWASP guidance for PBKDF2-HMAC-SHA256
const KEYLEN = 32;
export const SESSION_DAYS = 14;

const b64u = (b: Buffer | string): string => Buffer.from(b).toString('base64url');

export async function hashPassword(password: string, iterations = ITERATIONS): Promise<string> {
  const salt = randomBytes(16);
  const dk = await pbkdf2Async(password, salt, iterations, KEYLEN, 'sha256');
  return `pbkdf2$sha256$${iterations}$${salt.toString('base64url')}$${dk.toString('base64url')}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const parts = stored.split('$');
  if (parts.length !== 5 || parts[0] !== 'pbkdf2' || parts[1] !== 'sha256') return false;
  const iterations = Number(parts[2]);
  if (!Number.isInteger(iterations) || iterations < 100_000 || iterations > 5_000_000) return false;
  const salt = Buffer.from(parts[3]!, 'base64url');
  const expected = Buffer.from(parts[4]!, 'base64url');
  if (expected.length === 0) return false;
  const dk = await pbkdf2Async(password, salt, iterations, expected.length, 'sha256');
  return dk.length === expected.length && timingSafeEqual(dk, expected);
}

export async function checkPassword(cfg: Config, password: string): Promise<boolean> {
  if (cfg.passwordHash) return verifyPassword(password, cfg.passwordHash);
  if (cfg.plainPassword) {
    const a = createHash('sha256').update(password).digest();
    const b = createHash('sha256').update(cfg.plainPassword).digest();
    return timingSafeEqual(a, b);
  }
  return false;
}

// ---- Session token --------------------------------------------------------------

export interface SessionPayload {
  sub: 'owner';
  iat: number;
  exp: number;
  /** random per-session id; the only thing a revocation record is keyed on (never the token itself) */
  jti: string;
}

function sign(data: string, secret: string): string {
  return createHmac('sha256', secret).update(data).digest('base64url');
}

export function issueToken(secret: string, now: Date, days = SESSION_DAYS): { token: string; maxAge: number } {
  const iat = Math.floor(now.getTime() / 1000);
  const maxAge = days * 86400;
  const payload: SessionPayload = { sub: 'owner', iat, exp: iat + maxAge, jti: randomBytes(16).toString('base64url') };
  const body = b64u(JSON.stringify(payload));
  return { token: `${body}.${sign(body, secret)}`, maxAge };
}

export function verifyToken(token: string | null, secret: string | null, now: Date): SessionPayload | null {
  if (!token || !secret) return null;
  const [body, sig] = token.split('.');
  if (!body || !sig) return null;
  const expected = sign(body, secret);
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  try {
    const p = JSON.parse(Buffer.from(body, 'base64url').toString('utf8')) as SessionPayload;
    if (p.sub !== 'owner' || typeof p.exp !== 'number' || p.exp * 1000 < now.getTime()) return null;
    // a token without a session id cannot be revoked, so it is not accepted
    if (typeof p.jti !== 'string' || !/^[\w-]{16,64}$/.test(p.jti)) return null;
    return p;
  } catch {
    return null;
  }
}

export function cookieName(secure: boolean): string {
  return secure ? '__Host-desk_session' : 'desk_session';
}

export function sessionCookie(token: string, maxAge: number, secure: boolean): string {
  return `${cookieName(secure)}=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${secure ? '; Secure' : ''}`;
}

export function clearCookie(secure: boolean): string {
  return `${cookieName(secure)}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${secure ? '; Secure' : ''}`;
}

export function readCookie(header: string | null, name: string): string | null {
  if (!header) return null;
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
}

/** Signature + expiry only. Use `authenticate` for anything that grants access: it also checks revocation. */
export function sessionFromRequest(req: Request, cfg: Config, now: Date): SessionPayload | null {
  const secure = new URL(req.url).protocol === 'https:';
  const tok = readCookie(req.headers.get('cookie'), cookieName(secure));
  return verifyToken(tok, cfg.sessionSecret, now);
}

// ---- Server-side revocation ----------------------------------------------------------
//
// Logout writes one record per revoked session, keyed by a hash of its jti, so
// concurrent logouts never contend on a shared document. A record only has to
// outlive the token's own expiry; after that the token is rejected anyway, so
// records are pruned once `exp` has passed (plus a clock-skew grace).

const REVOKED_PREFIX = 'auth/revoked/';
const PRUNE_GRACE_MS = 60_000;
const PRUNE_BATCH = 200;

/** The revocation store could not be read or written. Callers must fail closed. */
export class SessionStoreError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SessionStoreError';
  }
}

const revokedKey = (jti: string): string => REVOKED_PREFIX + createHash('sha256').update(jti).digest('hex').slice(0, 32);

export async function revokeSession(kv: KV, session: SessionPayload): Promise<void> {
  try {
    await kv.put(revokedKey(session.jti), { exp: session.exp }, '*');
  } catch {
    throw new SessionStoreError('could not record session revocation');
  }
}

export async function isRevoked(kv: KV, session: SessionPayload): Promise<boolean> {
  try {
    return (await kv.get<{ exp: number }>(revokedKey(session.jti))).value !== null;
  } catch {
    throw new SessionStoreError('could not read session revocation list'); // unreadable == unknown == not trusted
  }
}

/** A valid, unexpired, non-revoked session, or null. Throws SessionStoreError if revocation cannot be checked. */
export async function authenticate(req: Request, cfg: Config, kv: KV, now: Date): Promise<SessionPayload | null> {
  const session = sessionFromRequest(req, cfg, now);
  if (!session) return null;
  return (await isRevoked(kv, session)) ? null : session;
}

/** Delete revocation records whose session has expired. Never touches a record that could still matter. */
export async function pruneRevocations(kv: KV, now: Date): Promise<number> {
  let removed = 0;
  const keys = (await kv.list(REVOKED_PREFIX)).slice(0, PRUNE_BATCH);
  for (const k of keys) {
    try {
      const rec = (await kv.get<{ exp?: unknown }>(k)).value;
      if (rec && typeof rec.exp === 'number' && rec.exp * 1000 + PRUNE_GRACE_MS < now.getTime()) {
        await kv.del(k);
        removed++;
      }
    } catch {
      /* unreadable record: leave it alone */
    }
  }
  return removed;
}

// ---- Login throttling ---------------------------------------------------------------

interface Attempts {
  fails: number;
  windowStart: number;
  lockedUntil: number;
}

const MAX_FAILS = 5;
const WINDOW_MS = 15 * 60_000;
const LOCK_MS = 15 * 60_000;

/**
 * Header carrying the caller's IP. Only the HTTP adapter (server/node-http.ts)
 * sets it, from a source it trusts (the authenticated Netlify proxy, or the
 * socket address in development); any client-supplied copy is discarded there.
 */
export const CLIENT_IP_HEADER = 'x-desk-client-ip';

/**
 * Canonical throttling identity for an address: IPv4 as is, IPv4-mapped IPv6 as IPv4,
 * and any other IPv6 address collapsed to its /64 (a single subscriber routinely owns
 * a whole /64, so per-address keys would let one attacker rotate through 2^64 of them).
 */
export function normalizeIp(raw: string): string {
  const ip = raw.trim().toLowerCase().replace(/^\[|\]$/g, '').replace(/%.*$/, '');
  const mapped = /^(?:::ffff:|0:0:0:0:0:ffff:)(\d{1,3}(?:\.\d{1,3}){3})$/.exec(ip);
  if (mapped?.[1]) return mapped[1];
  if (isIP(ip) === 4) return ip;
  if (isIP(ip) !== 6) return 'unknown';
  const [head = '', tail] = ip.split('::');
  const h = head ? head.split(':') : [];
  const t = tail === undefined ? [] : tail ? tail.split(':') : [];
  const groups = tail === undefined ? h : [...h, ...Array<string>(Math.max(0, 8 - h.length - t.length)).fill('0'), ...t];
  return `${groups.slice(0, 4).map((g) => g.padStart(4, '0')).join(':')}::/64`;
}

/** A single, syntactically valid IP from a platform-set header (e.g. X-Real-IP), or null. Lists are rejected. */
export function singleHeaderIp(header: string | string[] | undefined): string | null {
  if (typeof header !== 'string') return null;
  const v = header.trim().replace(/^\[|\]$/g, '');
  return v && isIP(v) ? v : null;
}

export function clientKey(req: Request): string {
  const ip = normalizeIp(req.headers.get(CLIENT_IP_HEADER) ?? 'unknown');
  return createHash('sha256').update(ip).digest('hex').slice(0, 24);
}

// In-process mutex per client key. Hashing is deliberately outside it: only the
// short reserve step is serialised, so one slow attempt never blocks other clients.
const keyLocks = new Map<string, Promise<unknown>>();
function withKeyLock<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const prev = keyLocks.get(key) ?? Promise.resolve();
  const run = prev.then(fn, fn);
  const tail = run.catch(() => undefined);
  keyLocks.set(key, tail);
  void tail.then(() => {
    if (keyLocks.get(key) === tail) keyLocks.delete(key);
  });
  return run;
}

/**
 * Atomically admit or refuse one login attempt BEFORE the password is checked.
 * Every admitted attempt is counted up front, so N parallel guesses can never
 * all be verified: at most MAX_FAILS attempts are admitted per window, and the
 * MAX_FAILS-th locks the client for LOCK_MS. A correct password then calls
 * clearFailures(), which resets the counter. The window also resets when it has
 * been quiet for WINDOW_MS, and an expired lock starts a fresh window. (Single
 * Railway process: the keyed mutex gives exactness; the CAS in update() keeps
 * it safe if that ever changes.)
 */
export async function reserveAttempt(kv: KV, key: string, now: Date): Promise<{ allowed: boolean; retryAfterSec: number }> {
  return withKeyLock(key, async () => {
    let result = { allowed: true, retryAfterSec: 0 };
    await update<Attempts>(kv, `auth/rl/${key}`, { fails: 0, windowStart: now.getTime(), lockedUntil: 0 }, (a) => {
      if (a.lockedUntil > now.getTime()) {
        result = { allowed: false, retryAfterSec: Math.ceil((a.lockedUntil - now.getTime()) / 1000) };
        return a;
      }
      result = { allowed: true, retryAfterSec: 0 };
      const fresh = now.getTime() - a.windowStart > WINDOW_MS || a.lockedUntil > 0;
      const fails = (fresh ? 0 : a.fails) + 1;
      return { fails, windowStart: fresh ? now.getTime() : a.windowStart, lockedUntil: fails >= MAX_FAILS ? now.getTime() + LOCK_MS : 0 };
    });
    return result;
  });
}

export async function lockStatus(kv: KV, key: string, now: Date): Promise<{ locked: boolean; retryAfterSec: number }> {
  const a = (await kv.get<Attempts>(`auth/rl/${key}`)).value;
  if (a && a.lockedUntil > now.getTime()) return { locked: true, retryAfterSec: Math.ceil((a.lockedUntil - now.getTime()) / 1000) };
  return { locked: false, retryAfterSec: 0 };
}

export async function recordFailure(kv: KV, key: string, now: Date): Promise<void> {
  await update<Attempts>(kv, `auth/rl/${key}`, { fails: 0, windowStart: now.getTime(), lockedUntil: 0 }, (a) => {
    const fresh = now.getTime() - a.windowStart > WINDOW_MS;
    const fails = (fresh ? 0 : a.fails) + 1;
    return { fails, windowStart: fresh ? now.getTime() : a.windowStart, lockedUntil: fails >= MAX_FAILS ? now.getTime() + LOCK_MS : a.lockedUntil };
  });
}

export async function clearFailures(kv: KV, key: string): Promise<void> {
  await kv.del(`auth/rl/${key}`);
}
