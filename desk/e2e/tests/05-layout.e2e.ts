// Layout regressions at phone widths, plus the session pill.
import { expect, test } from '@playwright/test';
import { ROUTES, horizontalOverflow, settled, signedIn } from './helpers';

for (const width of [320, 360, 390]) {
  test(`every bottom-tab is fully on screen at ${width}px, and no page scrolls sideways`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await signedIn(page);
    const tabs = page.locator('nav.tabbar a');
    await expect(tabs).toHaveCount(ROUTES.length);
    for (let i = 0; i < ROUTES.length; i++) {
      const box = (await tabs.nth(i).boundingBox())!;
      expect(box.x, `tab ${i} left`).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, `tab ${i} right`).toBeLessThanOrEqual(width + 0.5);
    }
    const bar = await page.locator('nav.tabbar').evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(bar).toBeLessThanOrEqual(0);
    // the last tab is reachable and works
    await tabs.last().click();
    await expect(page.locator('h1')).toHaveText('Settings');
    for (const r of ROUTES) {
      await page.goto(`/#${r.path}`);
      await settled(page);
      expect(await horizontalOverflow(page), r.path).toBeLessThanOrEqual(0);
    }
  });
}

test('the option chain table can be scrolled sideways on a phone to reach the put columns', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signedIn(page);
  await page.goto('/#/options');
  await settled(page);
  const wrap = page.locator('.tbl-wrap').first();
  await wrap.scrollIntoViewIfNeeded();
  const { sw, cw } = await wrap.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
  if (sw > cw) {
    await wrap.evaluate((el) => el.scrollTo({ left: el.scrollWidth }));
  }
  await expect(wrap.getByRole('columnheader', { name: 'PE OI' })).toBeInViewport();
});

test('the session pill says when the next change happens (desktop)', async ({ page }) => {
  await signedIn(page);
  const s = (await (await page.request.get('/api/session')).json()).data;
  const pill = page.locator('.topbar');
  if (s.nextChangeLabel && s.nextChangeAt) {
    await expect(pill).toContainText(new RegExp(`${s.nextChangeLabel} \\d{2} \\w{3},? \\d{2}:\\d{2} IST`));
  } else {
    test.info().annotations.push({ type: 'note', description: `no next change today (${s.phase})` });
  }
});
