import { chromium, type FullConfig } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { DEMO } from "./fixtures";

/**
 * Log in once as the seeded owner and persist the session.
 *
 * The API throttles login to 5 attempts/minute, so specs that are not testing
 * the login flow itself reuse this storage state instead of signing in again.
 * The state (cookies + localStorage clinic scope) is written to
 * `e2e/.auth/owner.json`.
 */

export const OWNER_STATE = path.resolve(__dirname, ".auth", "owner.json");

export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0]?.use?.baseURL ?? "http://127.0.0.1:5173";
  const browser = await chromium.launch();
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();

  await page.goto("/login");
  await page.getByTestId("login-email").fill(DEMO.ownerEmail);
  await page.getByTestId("login-password").fill(DEMO.password);
  await page.getByTestId("login-submit").click();
  await page.waitForURL(/\/app(\/)?$/);
  await page.getByTestId("logout-btn").waitFor();
  // The app pins the active clinic in localStorage once the memberships are
  // known. The specs read the clinic scope from there, so the state is saved
  // only after it is set - saving it earlier would race the first render and
  // hand the specs a state without a clinic.
  await page.waitForFunction(
    () => window.localStorage.getItem("enamel.activeClinicId") !== null,
  );

  fs.mkdirSync(path.dirname(OWNER_STATE), { recursive: true });
  await context.storageState({ path: OWNER_STATE });
  await browser.close();
}
