import { test, expect } from "@playwright/test";
import {
  DEMO,
  ORIGIN,
  OWNER_STATE,
  activeClinicId,
  createAppointmentViaApi,
  createPatientViaApi,
  portalOtpCode,
  uniqueEmail,
} from "./fixtures";

/**
 * Patient portal end-to-end.
 *
 * Runs in two projects (desktop + Pixel 7) so the phone viewport is exercised.
 * The staff storage state is intentionally active while the patient signs in,
 * proving the two principals are never mixed in the UI.
 *
 * The OTP flow needs the one-time code. A development stack (DEBUG=true plus
 * PORTAL_OTP_ECHO=true) echoes it in the response; the compose rehearsal stack
 * does not (production refuses DEBUG), so the harness reads the code from the
 * dev adapter's file inside the API container. See `e2e/fixtures.ts`.
 */
test.use({ storageState: OWNER_STATE });

interface PortalAppointmentRow {
  id: string;
  start_at: string;
  status: string;
}

async function listPortalAppointments(page: import("@playwright/test").Page): Promise<PortalAppointmentRow[]> {
  const response = await page.request.get(`${ORIGIN}/api/v1/portal/appointments/?page_size=100`);
  expect(response.ok(), `portal appointments failed: ${response.status()}`).toBeTruthy();
  const body = (await response.json()) as { results?: PortalAppointmentRow[] };
  return body.results ?? [];
}

function localInputValue(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

test.describe("patient portal", () => {
  test("a patient signs in with a dev OTP, sees only their own data, and leaves no PHI in the cache", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    // --- arrange: a patient whose contact we control ------------------------
    await page.goto("/app");
    const clinicId = await activeClinicId(page);
    const email = uniqueEmail("portal.patient");
    const patient = await createPatientViaApi(page.context(), clinicId, email, "Portal", "Patient");

    // One appointment inside the 24h cancel/reschedule cutoff and one well
    // outside it, so both the 409 policy path and the success path are real.
    const nearStart = new Date(Date.now() + 2 * 60 * 60 * 1000);
    const farStart = new Date(Date.now() + 11 * 24 * 60 * 60 * 1000);
    farStart.setHours(10, 0, 0, 0);
    const near = await createAppointmentViaApi(page.context(), clinicId, patient.id, nearStart);
    const far = await createAppointmentViaApi(page.context(), clinicId, patient.id, farStart);

    // --- portal sign-in ------------------------------------------------------
    await page.goto("/portal/login");
    await expect(page.getByRole("heading", { name: "Patient Portal" })).toBeVisible();

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

    // --- the portal home -----------------------------------------------------
    await expect(page).toHaveURL(/\/portal$/);
    await expect(page.getByRole("heading", { name: /Welcome back, Portal/i })).toBeVisible();

    // A staff session is active in the same browser; the portal says so.
    await expect(page.getByTestId("portal-staff-session-note")).toBeVisible();

    // --- records show this patient's own data only ---------------------------
    await page.goto("/portal/records");
    await expect(page.getByRole("heading", { name: "My Records" })).toBeVisible();
    await expect(page.getByText(email)).toBeVisible();

    // --- appointments: policy 409, reschedule and cancel ---------------------
    await page.goto("/portal/appointments");
    await expect(page.getByRole("heading", { name: "Appointments", exact: true })).toBeVisible();
    await expect(page.getByTestId("portal-appointments")).toBeVisible({ timeout: 20_000 });

    // Cancelling inside the cutoff is refused with the policy reason.
    await page.getByTestId(`cancel-${near.id}`).click();
    const policyAlert = page.getByRole("alert");
    await expect(policyAlert).toContainText(/can no longer be cancelled online/i);
    await expect(policyAlert).toContainText(/24h/);

    // Rescheduling outside the cutoff creates a replacement appointment.
    const rescheduledStart = new Date(
      new Date(far.startAt).getTime() + 24 * 60 * 60 * 1000 + (Date.now() % 180) * 60 * 1000,
    );
    await page.getByTestId(`reschedule-${far.id}`).click();
    await page.locator(`#reschedule-input-${far.id}`).fill(localInputValue(rescheduledStart));
    await page.getByRole("button", { name: "Confirm" }).click();

    await expect
      .poll(async () => {
        const rows = await listPortalAppointments(page);
        return rows.find((row) => row.id === far.id)?.status ?? "missing";
      })
      .toBe("cancelled");

    await expect
      .poll(async () => {
        const rows = await listPortalAppointments(page);
        return (
          rows.find(
            (row) =>
              row.status === "scheduled" &&
              Math.abs(new Date(row.start_at).getTime() - rescheduledStart.getTime()) < 60_000,
          )?.id ?? null
        );
      })
      .not.toBeNull();

    const rows = await listPortalAppointments(page);
    const replacementId = rows.find(
      (row) =>
        row.status === "scheduled" &&
        Math.abs(new Date(row.start_at).getTime() - rescheduledStart.getTime()) < 60_000,
    )?.id;
    expect(replacementId, "expected a replacement appointment").toBeTruthy();

    // Cancelling outside the cutoff succeeds and the row becomes cancelled.
    await page.reload();
    await page.getByTestId(`cancel-${replacementId as string}`).click();
    await expect
      .poll(async () => {
        const rows = await listPortalAppointments(page);
        return rows.find((row) => row.id === replacementId)?.status ?? "missing";
      })
      .toBe("cancelled");

    // --- plans / invoices / documents reach their empty or data state --------
    await page.goto("/portal/plans");
    await expect(page.getByRole("heading", { name: "Treatment Plans", exact: true })).toBeVisible();

    await page.goto("/portal/invoices");
    await expect(page.getByRole("heading", { name: "Invoices", exact: true })).toBeVisible();

    await page.goto("/portal/documents");
    await expect(page.getByRole("heading", { name: "Documents", exact: true })).toBeVisible();

    // --- PWA: manifest served, service worker registered ---------------------
    const manifestResponse = await page.request.get(`${ORIGIN}/manifest.webmanifest`);
    expect(manifestResponse.ok()).toBeTruthy();
    const manifest = (await manifestResponse.json()) as { name?: string };
    expect(manifest.name).toContain("Enamel");

    await page.waitForFunction(async () => {
      if (!("serviceWorker" in navigator)) return false;
      const registration = await navigator.serviceWorker.getRegistration();
      return Boolean(registration);
    });

    // --- no API response is ever written to Cache Storage --------------------
    const cachedUrls = await page.evaluate(async () => {
      const urls: string[] = [];
      const names = await caches.keys();
      for (const name of names) {
        const cache = await caches.open(name);
        for (const request of await cache.keys()) {
          urls.push(request.url);
        }
      }
      return urls;
    });
    expect(cachedUrls.filter((url) => url.includes("/api/"))).toEqual([]);
    expect(cachedUrls.filter((url) => url.includes("/media/"))).toEqual([]);

    // --- sign out clears the portal session ----------------------------------
    await page.getByTestId("portal-logout").click();
    await expect(page).toHaveURL(/\/portal\/login/);

    expect(consoleErrors, `page errors: ${consoleErrors.join(" | ")}`).toEqual([]);
  });
});

