// Single-owner authentication: PBKDF2-hashed password, HMAC-signed session
// cookie (HttpOnly, SameSite=Strict, Secure), per-IP login throttling.
// This closes TIRE forensic defect H-1 (no API authentication) for this app.

import { createHash, createHmac, pbkdf2, randomBytes, timingSafeEqual } from 'node:crypto';
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

interface SessionPayload {
  sub: 'owner';
  iat: number;
  exp: number;
}

function sign(data: string, secret: string): string {
  return createHmac('sha256', secret).update(data).digest('base64url');
}

export function issueToken(secret: string, now: Date, days = SESSION_DAYS): { token: string; maxAge: number } {
  const iat = Math.floor(now.getTime() / 1000);
  const maxAge = days * 86400;
  const payload: SessionPayload = { sub: 'owner', iat, exp: iat + maxAge };
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

export function sessionFromRequest(req: Request, cfg: Config, now: Date): SessionPayload | null {
  const secure = new URL(req.url).protocol === 'https:';
  const tok = readCookie(req.headers.get('cookie'), cookieName(secure));
  return verifyToken(tok, cfg.sessionSecret, now);
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

export function clientKey(req: Request): string {
  const ip = req.headers.get(CLIENT_IP_HEADER) ?? 'unknown';
  return createHash('sha256').update(ip).digest('hex').slice(0, 24);
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
