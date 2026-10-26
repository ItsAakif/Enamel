import { test, expect } from "@playwright/test";
import { DEMO, loginAsOwner } from "./fixtures";

/**
 * Staff authentication.
 *
 * This is the only spec that signs in through the UI; every other spec reuses
 * the storage state produced by `global-setup.ts`. The API throttles login to
 * 5 attempts/minute, so the flow is exercised in a small number of tests.
 */
test.describe("staff authentication", () => {
  test("the seeded owner signs in, sees the workspace, and signs out", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    await loginAsOwner(page);

    // The real clinic name comes from /auth/me; the header shows the real user.
    await expect(page.getByText(DEMO.clinicName).first()).toBeVisible();
    await expect(page.getByText("Clinical Overview")).toBeVisible();
    await expect(page.getByText("Olivia Owner").first()).toBeVisible();

    await page.getByTestId("logout-btn").click();
    await expect(page).toHaveURL(/\/login/);

    expect(consoleErrors, `page errors: ${consoleErrors.join(" | ")}`).toEqual([]);
  });

  test("a wrong password is rejected with an API error", async ({ page }) => {
    await page.goto("/login");
    await page.getByTestId("login-email").fill(DEMO.ownerEmail);
    await page.getByTestId("login-password").fill("definitely-not-the-password");
    await page.getByTestId("login-submit").click();

    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("an unauthenticated visitor is redirected away from /app", async ({ page }) => {
    await page.goto("/app");
    await expect(page).toHaveURL(/\/login/);
  });
});
