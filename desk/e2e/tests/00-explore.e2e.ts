// Exploratory pass: every route at desktop and phone width. Records problems and
// screenshots (e2e/evidence/) for manual review. Assertions are in the
// other files; this one only fails if a route crashes outright.
import { test } from '@playwright/test';
import path from 'node:path';
import { EVIDENCE, ROUTES, horizontalOverflow, signedIn, watch } from './helpers';

for (const vp of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }]) {
  test(`explore all routes (${vp.name})`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    const problems = watch(page);
    await signedIn(page);
    const log: string[] = [];
    for (const r of ROUTES) {
      await page.goto(`/#${r.path}`);
      await page.waitForTimeout(2500);
      const h1 = await page.locator('h1').first().textContent().catch(() => null);
      const errors = await page.locator('[role="alert"]').allTextContents();
      const overflow = await horizontalOverflow(page);
      const skeletons = await page.locator('.skeleton').count();
      log.push(`${r.path} h1=${h1} overflow=${overflow} skeletons=${skeletons} alerts=${JSON.stringify(errors).slice(0, 300)}`);
      await page.screenshot({ path: path.join(EVIDENCE, `explore-${vp.name}${r.path === '/' ? '-today' : r.path.replace('/', '-')}.png`), fullPage: true });
    }
    console.log(`[explore ${vp.name}]\n${log.join('\n')}\nproblems:\n${problems.join('\n') || '(none)'}`);
  });
}
