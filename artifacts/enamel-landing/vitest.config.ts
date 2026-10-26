import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

/**
 * Unit test configuration for the Enamel SPA (T105 acceptance item 8).
 *
 * The suite is deliberately small and behavioural: it renders the real
 * components and asserts what the user sees, so it catches a broken state
 * machine rather than a broken mock.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
    dedupe: ["react", "react-dom"],
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: [path.resolve(import.meta.dirname, "src/test/setup.ts")],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    css: false,
    restoreMocks: true,
  },
});
