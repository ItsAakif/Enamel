import { test, expect, type Page } from "@playwright/test";
import {
  DEMO,
  OWNER_STATE,
  activeClinicId,
  createAppointmentViaApi,
  createPatientViaApi,
  portalOtpCode,
  uniqueEmail,
} from "./fixtures";

/**
 * Patient portal keyboard-only journey (T122 acceptance item 8).
 *
 * The assertions are about behaviour, not about markup: the focus really
 * moves, the dialog really keeps the focus, Escape really closes it and the
 * focus really comes back. A page full of `tabindex="-1"` would pass a
 * markup check and fail this one.
 */

test.use({ storageState: OWNER_STATE });

/** A short description of whatever currently has the focus. */
async function focusLabel(page: Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) return "";
    const testId = el.getAttribute("data-testid");
    if (testId) return `testid:${testId}`;
    const text = (el.textContent ?? "").trim().replace(/\s+/g, " ");
    return `text:${text.slice(0, 40)}`;
  });
}

/** True when the focused element sits inside the open dialog. */
async function focusIsInsideDialog(page: Page): Promise<boolean> {
  return page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')));
}

/** Press Tab `steps` times and collect what had the focus each time. */
async function tabThrough(page: Page, steps: number): Promise<string[]> {
  const seen: string[] = [];
  for (let index = 0; index < steps; index += 1) {
    await page.keyboard.press("Tab");
    seen.push(await focusLabel(page));
  }
  return seen;
}

/** Sign the patient in with the dev OTP and land on the portal home. */
async function signIntoPortal(page: Page, email: string): Promise<void> {
  await page.goto("/portal/login");
  await page.getByTestId("portal-clinic").fill(DEMO.clinicSlug);
  await page.getByTestId("portal-contact").fill(email);
  await page.getByTestId("portal-request-code").click();

  // The API echoes the code in DEBUG; the compose rehearsal stack does not,
  // and the code is read from the API container instead.
  const code = await portalOtpCode(page, email);
  expect(code).toMatch(/^\d{6}$/);

  const codeField = page.getByTestId("portal-code");
  if ((await codeField.inputValue()) !== code) {
    await codeField.fill(code);
  }
  await page.getByTestId("portal-verify-code").click();
  await expect(page).toHaveURL(/\/portal$/);
}

test.describe("patient portal keyboard navigation", () => {
  test("the portal can be operated with the keyboard alone", async ({ page }) => {
    // --- arrange: a patient the portal can sign in --------------------------
    await page.goto("/app");
    const clinicId = await activeClinicId(page);
    const email = uniqueEmail("portal.keyboard");
    const patient = await createPatientViaApi(
      page.context(),
      clinicId,
      email,
      "Keyboard",
      "Patient",
    );
    const start = new Date(Date.now() + 9 * 24 * 60 * 60 * 1000);
    start.setHours(11, 0, 0, 0);
    await createAppointmentViaApi(page.context(), clinicId, patient.id, start);

    await signIntoPortal(page, email);

    // --- the primary actions are reachable with Tab only --------------------
    await page.goto("/portal");
    await expect(page.getByRole("heading", { name: /Welcome back, Keyboard/i })).toBeVisible();

    const seen = await tabThrough(page, 15);
    expect(seen.length).toBeGreaterThan(0);

    // The sign-out control and every portal section are in the tab order.
    expect(
      seen.some((label) => label.includes("portal-logout")),
      `the sign-out control was not reachable: ${seen.join(" | ")}`,
    ).toBe(true);
    for (const section of ["Appointments", "Records", "Plans", "Invoices", "Documents"]) {
      expect(
        seen.some((label) => label.includes(section)),
        `the "${section}" section was not reachable: ${seen.join(" | ")}`,
      ).toBe(true);
    }

    // --- activating a focused link with the keyboard navigates --------------
    const nav = page.getByRole("navigation", { name: "Portal sections" });
    const appointmentsLink = nav.getByRole("link", { name: "Appointments", exact: true });
    await appointmentsLink.focus();
    await expect.poll(() => focusLabel(page)).toContain("Appointments");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/portal\/appointments$/);
  });

  test("the reschedule dialog traps the focus, closes on Escape and releases it", async ({
    page,
  }) => {
    // --- arrange ------------------------------------------------------------
    await page.goto("/app");
    const clinicId = await activeClinicId(page);
    const email = uniqueEmail("portal.keyboard.dialog");
    const patient = await createPatientViaApi(
      page.context(),
      clinicId,
      email,
      "Dialog",
      "Patient",
    );
    const start = new Date(Date.now() + 9 * 24 * 60 * 60 * 1000);
    start.setHours(13, 0, 0, 0);
    const appointment = await createAppointmentViaApi(
      page.context(),
      clinicId,
      patient.id,
      start,
    );

    await signIntoPortal(page, email);
    await page.goto("/portal/appointments");

    const trigger = page.getByTestId(`reschedule-${appointment.id}`);
    await expect(trigger).toBeVisible();

    // --- open the dialog with the keyboard only -----------------------------
    await trigger.focus();
    await expect
      .poll(() =>
        page.evaluate(() => document.activeElement?.getAttribute("data-testid") ?? ""),
      )
      .toBe(`reschedule-${appointment.id}`);
    await page.keyboard.press("Enter");

    const dialog = page.getByTestId("reschedule-dialog");
    await expect(dialog).toBeVisible();

    // The focus moved into the dialog.
    await expect.poll(() => focusIsInsideDialog(page)).toBe(true);

    // --- the focus is trapped: Tab and Shift+Tab stay inside ---------------
    for (let index = 0; index < 6; index += 1) {
      await page.keyboard.press("Tab");
      expect(
        await focusIsInsideDialog(page),
        `the focus left the dialog after ${index + 1} forward tabs`,
      ).toBe(true);
    }
    for (let index = 0; index < 4; index += 1) {
      await page.keyboard.press("Shift+Tab");
      expect(
        await focusIsInsideDialog(page),
        `the focus left the dialog after ${index + 1} backward tabs`,
      ).toBe(true);
    }

    // --- Escape closes it and the focus goes back to the trigger ------------
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();

    await expect
      .poll(() =>
        page.evaluate(() => document.activeElement?.getAttribute("data-testid") ?? ""),
      )
      .toBe(`reschedule-${appointment.id}`);
  });
});
