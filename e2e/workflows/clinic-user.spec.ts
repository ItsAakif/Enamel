import { test, expect } from "@playwright/test";
import { DEMO_CLINIC_NAME, ORIGIN, uniqueEmail } from "./helpers";

/**
 * Workflow 1: a brand-new clinic plus its first owner.
 *
 * The journey runs in a clean browser context (no shared owner session) and
 * registers through the real form, then proves the account and the clinic exist
 * through the shell and `/auth/me`.
 */

test.describe("clinic and user registration", () => {
  test("registers a clinic and its owner end to end", async ({ browser }) => {
    const context = await browser.newContext({ baseURL: ORIGIN });
    const page = await context.newPage();

    const stamp = Date.now();
    const clinicName = `E2E Dental ${stamp}`;
    const clinicSlug = `e2e-dental-${stamp}`;
    const ownerName = `Dr E2E Owner ${stamp}`;
    const email = uniqueEmail("owner.register");
    const password = "EnamelE2E!2026";

    await page.goto("/register");
    await expect(page.getByRole("heading", { name: "Create your clinic" })).toBeVisible();

    await page.getByTestId("register-clinic-name").fill(clinicName);
    await page.getByTestId("register-clinic-slug").fill(clinicSlug);
    await page.getByTestId("register-full-name").fill(ownerName);
    await page.getByTestId("register-email").fill(email);
    await page.getByTestId("register-password").fill(password);
    await page.getByTestId("register-submit").click();

    await expect(page).toHaveURL(/\/app(\/)?$/);
    await expect(page.getByTestId("logout-btn")).toBeVisible();

    // The shell shows the active clinic identity. The switcher itself only
    // appears when the signed-in user belongs to more than one clinic, so a
    // fresh owner sees the clinic name instead of a one-option dropdown.
    const currentClinic = page.getByTestId("current-clinic");
    await expect(currentClinic).toBeVisible();
    await expect(currentClinic).toHaveText(clinicName);
    await expect(page.getByTestId("clinic-switcher")).toHaveCount(0);

    // ...and the session really is the new owner, not the shared demo owner.
    const response = await context.request.get(`${ORIGIN}/api/v1/auth/me/`);
    expect(response.ok(), `GET /auth/me failed: ${response.status()}`).toBeTruthy();
    const me = (await response.json()) as {
      user: { email: string; full_name: string };
      memberships: Array<{ role: string; clinic: { name: string; slug: string } }>;
    };

    expect(me.user.email).toBe(email);
    expect(me.user.full_name).toBe(ownerName);
    const membership = me.memberships.find((item) => item.clinic.slug === clinicSlug);
    expect(membership, "the new owner must belong to the clinic they registered").toBeTruthy();
    expect(membership?.clinic.name).toBe(clinicName);
    expect(membership?.role).toBe("owner");

    // The demo clinic is not part of a fresh registration.
    expect(me.memberships.some((item) => item.clinic.name === DEMO_CLINIC_NAME)).toBe(false);

    await context.close();
  });
});
