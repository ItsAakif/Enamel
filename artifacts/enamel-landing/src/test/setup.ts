/**
 * Test setup for the Enamel SPA unit suite.
 *
 * `@testing-library/jest-dom/vitest` adds the DOM matchers (`toBeInTheDocument`,
 * `toHaveTextContent`, ...) to Vitest's `expect`.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// The suite runs with `globals: false`, so the automatic cleanup that Testing
// Library installs when the globals are on has to be requested explicitly.
// Without it the DOM of one test leaks into the next one.
afterEach(() => {
  cleanup();
});

// jsdom does not implement `window.matchMedia`, which framer-motion (the
// animation layer of the app shell) reads on mount. Without the stub the
// component tree throws instead of rendering.
if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
