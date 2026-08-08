import { test, expect } from '@playwright/test';

/**
 * Full loop against the real API in mock mode:
 * create world -> add character -> start session -> play a turn -> reload.
 */
test('a player can create a world, play a turn, and reload it', async ({ page }) => {
  const worldName = `E2E World ${Date.now()}`;

  await page.goto('/');

  await page.getByRole('button', { name: /create a world/i }).click();
  await page.getByLabel(/world name/i).fill(worldName);
  await page.getByLabel(/genre/i).fill('fantasy');
  await page.getByRole('button', { name: /^create$/i }).click();

  await expect(page.getByRole('heading', { name: worldName })).toBeVisible();

  await page.getByRole('button', { name: /add character/i }).click();
  await page.getByLabel(/^name$/i).fill('Elena');
  await page.getByLabel(/personality/i).fill('sarcastic, cautious');
  await page.getByRole('button', { name: /^create$/i }).click();
  await expect(page.getByRole('heading', { name: 'Elena' })).toBeVisible();

  await page.getByRole('button', { name: /start a new game/i }).click();
  await page.getByLabel(/save name/i).fill('E2E run');
  await page.getByLabel(/your character/i).fill('Rin');
  await page.getByRole('button', { name: /start a new game/i }).click();

  await expect(page.getByRole('heading', { name: 'E2E run' })).toBeVisible();
  await expect(page.getByText(/has not started/i)).toBeVisible();

  await page.getByPlaceholder(/type anything/i).fill('I walk toward Elena and ask her name.');
  await page.getByRole('button', { name: /send/i }).click();

  await expect(page.locator('[data-role="player"]')).toContainText('I walk toward Elena');
  await expect(page.locator('[data-role="narrator"]')).toBeVisible();
  await expect(page.locator('[data-role="character"]')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Ask Elena/ }).first()).toBeVisible();

  // The turn must survive a reload: this is the persistence guarantee.
  const url = page.url();
  await page.reload();
  expect(page.url()).toBe(url);
  await expect(page.locator('[data-role="player"]')).toContainText('I walk toward Elena');
  await expect(page.getByText(/turn 1/i)).toBeVisible();
});

test('a suggested action can be played with one tap', async ({ page }) => {
  await page.goto('/');

  await page.getByRole('button', { name: /create a world/i }).click();
  await page.getByLabel(/world name/i).fill(`Suggest ${Date.now()}`);
  await page.getByRole('button', { name: /^create$/i }).click();

  await page.getByRole('button', { name: /start a new game/i }).click();
  await page.getByLabel(/save name/i).fill('Suggest run');
  await page.getByLabel(/your character/i).fill('Rin');
  await page.getByRole('button', { name: /start a new game/i }).click();

  await page.getByPlaceholder(/type anything/i).fill('I look around.');
  await page.getByRole('button', { name: /send/i }).click();
  await expect(page.locator('[data-role="narrator"]')).toBeVisible();

  const suggestion = page.getByRole('button', { name: /^(Ask|Look|Wait)/ }).first();
  const label = ((await suggestion.textContent()) ?? '').trim();
  await suggestion.click();

  await expect(page.locator('[data-role="player"]').last()).toContainText(label);
  await expect(page.getByText(/turn 2/i)).toBeVisible();
});
