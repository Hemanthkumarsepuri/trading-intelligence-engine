// Runs LAST: it locks sign-in for the local IP for 15 minutes on this test server.
import { expect, test } from '@playwright/test';
import { signIn } from './helpers';

test('five wrong passwords lock sign-in; even the right password is then refused with a clear message', async ({ page }) => {
  test.setTimeout(60_000);
  for (let i = 0; i < 5; i++) {
    await signIn(page, `wrong-${i}`);
    await expect(page.getByText('Incorrect password.')).toBeVisible();
  }
  await signIn(page);
  await expect(page.getByText(/Too many attempts\. Try again in \d+ minute/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Today' })).toHaveCount(0);
});
