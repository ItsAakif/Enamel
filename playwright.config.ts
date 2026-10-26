import { defineConfig, devices } from "@playwright/test";

/**
 * Enamel end-to-end harness.
 *
 * The specs run against a *running* stack by default:
 *   - Django API on http://127.0.0.1:8000
 *   - Vite dev server on http://127.0.0.1:5173 (proxying /api to Django)
 *
 * Point the harness elsewhere with `E2E_BASE_URL`.
 *
 * Starting the servers automatically is opt-in: set `E2E_START_SERVER=1` and
 * Playwright will boot the Vite dev server itself (the Django API must already
 * be running). This is documented rather than forced, because the backend is a
 * separate process with its own migrations/seed step.
 */

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
// An explicit E2E_BASE_URL always points at an already-running stack (for
// example the compose stack on http://localhost:8090); never boot a second web
// server against it.
const startServer =
  process.env.E2E_BASE_URL === undefined && process.env.E2E_START_SERVER === "1";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 1,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    ignoreHTTPSErrors: true,
    // The seeded clinic schedules in Asia/Kolkata. Pinning the browser zone
    // makes the calendar grid, datetime-local inputs and reminder dates
    // deterministic regardless of the developer machine.
    timezoneId: "Asia/Kolkata",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The patient portal must remain usable on a phone viewport.
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
      testMatch: /portal\.spec\.ts/,
    },
  ],
  webServer: startServer
    ? [
        {
          command: "pnpm --filter @workspace/enamel-landing run dev",
          url: baseURL,
          reuseExistingServer: true,
          timeout: 120_000,
        },
      ]
    : undefined,
});
