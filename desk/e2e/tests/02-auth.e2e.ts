// Authentication and session security, through a real browser and the real local API.
import { expect, request as pwRequest, test } from '@playwright/test';
import { PASSWORD, ROUTES, signIn, signedIn, watch } from './helpers';

const COOKIE = 'desk_session'; // http://localhost: the __Host- prefix is used only on https

test('unauthenticated visitors see only the sign-in form, on every route, and the API refuses them', async ({ page }) => {
  for (const r of ROUTES) {
    await page.goto(`/#${r.path}`);
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCount(0);
    await expect(page.getByText('Risk guard')).toHaveCount(0);
  }
  for (const p of ['/api/overview', '/api/settings', '/api/journal', '/api/export', '/api/status']) {
    const res = await page.request.get(p);
    expect(res.status(), p).toBe(401);
    expect((await res.json()).error.code).toBe('UNAUTHENTICATED');
  }
  expect((await page.request.get('/api/health')).status()).toBe(200);
});

test('a wrong password shows a safe message and sets no session', async ({ page, context }) => {
  const problems = watch(page);
  await signIn(page, 'definitely-not-the-password');
  await expect(page.getByText('Incorrect password.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Today' })).toHaveCount(0);
  expect((await context.cookies()).find((c) => c.name === COOKIE)).toBeUndefined();
  expect(page.url()).not.toContain('definitely-not-the-password');
  // the 401 for the bad password is expected; nothing else may fail
  expect(problems.filter((p) => !p.includes('401'))).toEqual([]);
});

test('valid sign-in, refresh keeps the session, cookie is HttpOnly + SameSite=Strict and invisible to scripts', async ({ page, context }) => {
  const problems = watch(page);
  await signedIn(page);
  const c = (await context.cookies()).find((x) => x.name === COOKIE)!;
  expect(c).toBeTruthy();
  expect(c.httpOnly).toBe(true);
  expect(c.sameSite).toBe('Strict');
  expect(c.path).toBe('/');
  expect(await page.evaluate(() => document.cookie)).not.toContain(COOKIE);

  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Today' })).toBeVisible();

  // nothing secret leaks into URLs or web storage
  const leaked = await page.evaluate(([pw, tok]) => {
    const all = [location.href, JSON.stringify(localStorage), JSON.stringify(sessionStorage), document.body.innerText];
    return all.some((s) => s.includes(pw!) || s.includes(tok!));
  }, [PASSWORD, c.value]);
  expect(leaked).toBe(false);
  expect(problems).toEqual([]);
});

test('logout returns to sign-in and the old cookie is revoked server-side (replay is refused)', async ({ page, context, baseURL }) => {
  await signedIn(page);
  const token = (await context.cookies()).find((x) => x.name === COOKIE)!.value;
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  expect((await context.cookies()).find((x) => x.name === COOKIE)).toBeUndefined();

  // An attacker who copied the cookie before logout replays it.
  const attacker = await pwRequest.newContext({ baseURL, extraHTTPHeaders: { cookie: `${COOKIE}=${token}` } });
  const r = await attacker.get('/api/settings');
  expect(r.status()).toBe(401);
  expect((await (await attacker.get('/api/auth/me')).json()).data.authenticated).toBe(false);
  await attacker.dispose();
});

test('a tampered session cookie is rejected', async ({ page, context, baseURL }) => {
  await signedIn(page);
  const token = (await context.cookies()).find((x) => x.name === COOKIE)!.value;
  const forged = token.replace(/.$/, (ch) => (ch === 'A' ? 'B' : 'A'));
  const attacker = await pwRequest.newContext({ baseURL, extraHTTPHeaders: { cookie: `${COOKIE}=${forged}` } });
  expect((await attacker.get('/api/settings')).status()).toBe(401);
  await attacker.dispose();
});

test('a write from a foreign origin is refused even with a valid session (CSRF)', async ({ page, context, baseURL }) => {
  await signedIn(page);
  const token = (await context.cookies()).find((x) => x.name === COOKIE)!.value;
  const other = await pwRequest.newContext({ baseURL, extraHTTPHeaders: { cookie: `${COOKIE}=${token}`, origin: 'https://evil.example' } });
  const r = await other.post('/api/trades', { data: {} });
  expect(r.status()).toBe(403);
  await other.dispose();
});
