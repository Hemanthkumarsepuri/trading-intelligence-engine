import { describe, expect, it } from 'vitest';
import { checkPassword, clearFailures, hashPassword, issueToken, lockStatus, readCookie, recordFailure, verifyPassword, verifyToken } from '../server/auth';
import { MemoryKV } from '../server/kv';
import { loadConfig } from '../server/config';

const NOW = new Date('2026-10-12T05:00:00Z');
const SECRET = 'x'.repeat(40);

describe('password hashing', () => {
  it('verifies the right password and rejects the wrong one', async () => {
    const h = await hashPassword('correct horse battery', 100_000);
    expect(await verifyPassword('correct horse battery', h)).toBe(true);
    expect(await verifyPassword('wrong horse battery', h)).toBe(false);
  });
  it('rejects malformed or downgraded hashes', async () => {
    expect(await verifyPassword('x', 'garbage')).toBe(false);
    expect(await verifyPassword('x', 'pbkdf2$sha256$1$AAAA$BBBB')).toBe(false); // iteration floor
    expect(await verifyPassword('x', 'pbkdf2$sha1$600000$AAAA$BBBB')).toBe(false);
  });
  it('uses a fresh salt each time', async () => {
    expect(await hashPassword('same', 100_000)).not.toBe(await hashPassword('same', 100_000));
  });
  it('falls back to a plain password only when no hash is configured', async () => {
    const plain = loadConfig({ APP_PASSWORD: 'dev-only-pass' });
    expect(await checkPassword(plain, 'dev-only-pass')).toBe(true);
    expect(await checkPassword(plain, 'nope')).toBe(false);
    const none = loadConfig({});
    expect(await checkPassword(none, '')).toBe(false);
  });
});

describe('session tokens', () => {
  it('round-trips and expires', () => {
    const { token } = issueToken(SECRET, NOW, 1);
    expect(verifyToken(token, SECRET, NOW)).toMatchObject({ sub: 'owner' });
    expect(verifyToken(token, SECRET, new Date(NOW.getTime() + 2 * 86400000))).toBeNull();
  });
  it('rejects tampering and a wrong secret', () => {
    const { token } = issueToken(SECRET, NOW);
    const [body, sig] = token.split('.');
    const forged = Buffer.from(JSON.stringify({ sub: 'owner', iat: 1, exp: 9_999_999_999 })).toString('base64url');
    expect(verifyToken(`${forged}.${sig}`, SECRET, NOW)).toBeNull();
    expect(verifyToken(token, 'y'.repeat(40), NOW)).toBeNull();
    expect(verifyToken(`${body}.`, SECRET, NOW)).toBeNull();
    expect(verifyToken(null, SECRET, NOW)).toBeNull();
    expect(verifyToken(token, null, NOW)).toBeNull();
  });
  it('parses cookies', () => {
    expect(readCookie('a=1; __Host-desk_session=abc.def; b=2', '__Host-desk_session')).toBe('abc.def');
    expect(readCookie(null, 'a')).toBeNull();
  });
});

describe('login throttling', () => {
  it('locks after five failures and clears on success', async () => {
    const kv = new MemoryKV();
    for (let i = 0; i < 4; i++) await recordFailure(kv, 'ip1', NOW);
    expect((await lockStatus(kv, 'ip1', NOW)).locked).toBe(false);
    await recordFailure(kv, 'ip1', NOW);
    const s = await lockStatus(kv, 'ip1', NOW);
    expect(s.locked).toBe(true);
    expect(s.retryAfterSec).toBeGreaterThan(800);
    expect((await lockStatus(kv, 'ip1', new Date(NOW.getTime() + 16 * 60_000))).locked).toBe(false);
    await clearFailures(kv, 'ip1');
    expect((await lockStatus(kv, 'ip1', NOW)).locked).toBe(false);
  });
  it('tracks clients independently', async () => {
    const kv = new MemoryKV();
    for (let i = 0; i < 5; i++) await recordFailure(kv, 'a', NOW);
    expect((await lockStatus(kv, 'b', NOW)).locked).toBe(false);
  });
});
