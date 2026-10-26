import { expect, type BrowserContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

/**
 * Shared demo fixtures for the Enamel E2E harness.
 *
 * The credentials come from `manage.py seed_demo`:
 *   owner@enamel.demo / EnamelDemo!2026  (owner of "Enamel Central")
 *
 * `OWNER_STATE` is the storage state produced by `global-setup.ts` (one login
 * shared by every spec that is not itself testing the login flow).
 */

export const OWNER_STATE = path.resolve(__dirname, ".auth", "owner.json");

export const DEMO = {
  ownerEmail: "owner@enamel.demo",
  password: "EnamelDemo!2026",
  clinicSlug: "enamel-central",
  clinicName: "Enamel Central",
} as const;

export const ORIGIN = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";

/** Sign in through the real login form and wait for the workspace shell. */
export async function loginAsOwner(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByTestId("login-email").fill(DEMO.ownerEmail);
  await page.getByTestId("login-password").fill(DEMO.password);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/app(\/)?$/);
  await expect(page.getByTestId("logout-btn")).toBeVisible();
}

/** The `X-Clinic-Id` scope for the demo clinic, read from the app's own storage. */
export async function activeClinicId(page: Page): Promise<string> {
  const id = await page.evaluate(() => window.localStorage.getItem("enamel.activeClinicId"));
  expect(id, "expected an active clinic id in localStorage").toBeTruthy();
  return id as string;
}

// ---------------------------------------------------------------------------
// Patient portal one-time code
// ---------------------------------------------------------------------------

/** The compose API container the harness reads the portal code from. */
export const API_CONTAINER = process.env.E2E_API_CONTAINER ?? "enamel-api-1";

/** Where the dev OTP adapter writes the code inside the API container. */
export const PORTAL_OTP_LOG_PATH =
  process.env.E2E_PORTAL_OTP_LOG_PATH ?? "/app/logs/portal_otp.log";

interface PortalOtpLine {
  channel?: string;
  contact?: string;
  code?: string;
}

/**
 * The one-time code the API sent to `contact`, read from the dev adapter's
 * file inside the API container.
 *
 * The production profile never echoes the code (the echo needs `DEBUG`, which
 * production refuses), so the compose rehearsal stack is the only place the
 * harness can read it. The file holds contact identifiers and one-time codes;
 * it lives inside the container and is never served.
 */
export function portalOtpCodeFromContainer(contact: string): string {
  let output: string;
  try {
    output = execFileSync("docker", ["exec", API_CONTAINER, "cat", PORTAL_OTP_LOG_PATH], {
      encoding: "utf8",
      timeout: 30_000,
    });
  } catch (error) {
    const failure = error as { stderr?: string; message?: string };
    throw new Error(
      `could not read the portal one-time code from ${API_CONTAINER}:${PORTAL_OTP_LOG_PATH}\n` +
        `${failure.stderr?.trim() ?? ""}\n${failure.message ?? String(error)}`,
    );
  }

  const wanted = contact.trim().toLowerCase();
  const lines = output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .reverse();

  for (const line of lines) {
    let parsed: PortalOtpLine;
    try {
      parsed = JSON.parse(line) as PortalOtpLine;
    } catch {
      continue;
    }
    if (typeof parsed.code !== "string") continue;
    if ((parsed.contact ?? "").trim().toLowerCase() !== wanted) continue;
    return parsed.code;
  }

  throw new Error(
    `no portal one-time code for "${contact}" in ${API_CONTAINER}:${PORTAL_OTP_LOG_PATH}`,
  );
}

/**
 * The code for a portal sign-in, whether the API echoed it or not.
 *
 * A development stack (`DEBUG=true` plus `PORTAL_OTP_ECHO=true`) shows the
 * code in the UI; the compose rehearsal stack does not, and the code is read
 * from the API container instead.
 */
export async function portalOtpCode(page: Page, contact: string): Promise<string> {
  const devHint = page.getByTestId("portal-dev-code");
  const echoed = await devHint
    .waitFor({ state: "visible", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);

  if (echoed) {
    const code = (await devHint.locator("code").innerText()).trim();
    expect(code, "the echoed portal code must be six digits").toMatch(/^\d{6}$/);
    return code;
  }

  return portalOtpCodeFromContainer(contact);
}

async function csrfToken(context: BrowserContext): Promise<string | null> {
  const cookies = await context.cookies();
  return cookies.find((cookie) => cookie.name === "csrftoken")?.value ?? null;
}

/** Headers every staff API call in the harness needs. */
export async function staffApiHeaders(context: BrowserContext, clinicId: string): Promise<Record<string, string>> {
  const csrf = await csrfToken(context);
  return {
    "X-Clinic-Id": clinicId,
    ...(csrf ? { "X-CSRFToken": csrf } : {}),
  };
}

interface ListEnvelope<T> {
  results?: T[];
}

async function getStaffList<T>(context: BrowserContext, clinicId: string, path: string): Promise<T[]> {
  const response = await context.request.get(`${ORIGIN}${path}`, {
    headers: { "X-Clinic-Id": clinicId },
  });
  expect(response.ok(), `GET ${path} failed: ${response.status()} ${await response.text()}`).toBeTruthy();
  const body = (await response.json()) as T[] | ListEnvelope<T>;
  return Array.isArray(body) ? body : (body.results ?? []);
}

export interface CreatedPatient {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
}

/**
 * Create a patient through the staff API, sharing the browser context's cookies.
 *
 * Used by the portal spec so it can log in with a contact that is guaranteed to
 * exist, instead of depending on whatever demo data happens to be seeded.
 */
export async function createPatientViaApi(
  context: BrowserContext,
  clinicId: string,
  email: string,
  firstName: string,
  lastName: string,
): Promise<CreatedPatient> {
  const csrf = await csrfToken(context);
  const response = await context.request.post(`${ORIGIN}/api/v1/patients/`, {
    headers: {
      "X-Clinic-Id": clinicId,
      ...(csrf ? { "X-CSRFToken": csrf } : {}),
    },
    data: {
      first_name: firstName,
      last_name: lastName,
      preferred_name: firstName,
      email,
    },
  });
  expect(response.ok(), `patient create failed: ${response.status()} ${await response.text()}`).toBeTruthy();
  const body = (await response.json()) as { id: string };
  return { id: body.id, email, firstName, lastName };
}

/** A contact that is unique per run so repeated runs never collide. */
export function uniqueEmail(prefix: string): string {
  return `${prefix}.${Date.now()}.${Math.floor(Math.random() * 10000)}@e2e.enamel.demo`;
}

export interface CreatedAppointment {
  id: string;
  startAt: string;
}

/**
 * Create a staff appointment through the API for a patient the spec owns.
 *
 * The portal specs use this to exercise the real cancel/reschedule paths
 * without relying on whatever demo appointments happen to exist.
 *
 * The double-booking exclusion constraints are per provider lane, so a busy
 * provider must not move the appointment away from the time the test asked
 * for: every provider is tried at the requested time first. Only a clinic that
 * is completely full shifts the start, and then by a bounded amount - a silent
 * drift would move an appointment out of the cancel cutoff and the spec would
 * assert the wrong thing.
 */
export async function createAppointmentViaApi(
  context: BrowserContext,
  clinicId: string,
  patientId: string,
  startAt: Date,
): Promise<CreatedAppointment> {
  const [types, providers, locations] = await Promise.all([
    getStaffList<{ id: string }>(context, clinicId, "/api/v1/appointment-types/?page_size=100"),
    getStaffList<{ id: string }>(context, clinicId, "/api/v1/providers/?page_size=100"),
    getStaffList<{ id: string }>(context, clinicId, `/api/v1/clinics/${clinicId}/locations/`),
  ]);
  expect(types.length, "expected a seeded appointment type").toBeGreaterThan(0);
  expect(providers.length, "expected a seeded provider").toBeGreaterThan(0);
  expect(locations.length, "expected a seeded location").toBeGreaterThan(0);

  // How far the start may drift when every provider lane is busy. The portal
  // spec needs an appointment inside the 24h cancel cutoff, so the drift has
  // to stay well below it.
  const MAX_DRIFT_MINUTES = 12 * 60;
  const SHIFT_MINUTES = 37;

  const requested = startAt.getTime();
  let candidate = new Date(requested);

  for (let round = 0; round <= Math.ceil(MAX_DRIFT_MINUTES / SHIFT_MINUTES); round += 1) {
    for (const provider of providers) {
      const response = await context.request.post(`${ORIGIN}/api/v1/appointments/`, {
        headers: await staffApiHeaders(context, clinicId),
        data: {
          patient: patientId,
          provider: provider.id,
          appointment_type: types[0].id,
          location: locations[0].id,
          start_at: candidate.toISOString(),
          reason: "e2e portal",
        },
      });

      if (response.ok()) {
        const body = (await response.json()) as { id: string; start_at: string };
        return { id: body.id, startAt: body.start_at };
      }

      const body = (await response.json()) as { error?: { code?: string } };
      if (response.status() !== 409 || body.error?.code !== "slot_conflict") {
        throw new Error(
          `appointment create failed: ${response.status()} ${await response.text()}`,
        );
      }
      // This provider lane is busy at that time: try the next one.
    }

    // Every lane is busy: shift the start and try again.
    candidate = new Date(candidate.getTime() + SHIFT_MINUTES * 60 * 1000);
  }

  throw new Error(
    `no free slot for an appointment at ${new Date(requested).toISOString()} ` +
      `(or within ${MAX_DRIFT_MINUTES} minutes of it)`,
  );
}
