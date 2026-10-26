#!/usr/bin/env node
/**
 * Run the Playwright suite against the compose stack.
 *
 * The stack is expected to be up already (see deploy/README.md). This wrapper
 * only pins E2E_BASE_URL so the browser targets nginx on port 8090 instead of
 * the Vite dev server, and forwards any extra CLI arguments to Playwright:
 *
 *   pnpm e2e:compose
 *   pnpm e2e:compose e2e/workflows/odontogram.spec.ts --headed
 */

import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const cli = require.resolve("@playwright/test/cli");

const env = {
  ...process.env,
  E2E_BASE_URL: process.env.E2E_BASE_URL || "http://localhost:8090",
};

const child = spawn(process.execPath, [cli, "test", ...process.argv.slice(2)], {
  stdio: "inherit",
  env,
});

child.on("error", (error) => {
  console.error(`Could not start Playwright: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    console.error(`Playwright terminated by ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
