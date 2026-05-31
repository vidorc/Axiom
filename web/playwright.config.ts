import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the Axiom builder E2E suite.
 *
 * The suite drives a real Chromium browser against a full local stack (API +
 * worker + Next dev), launched once in `global-setup` and torn down in
 * `global-teardown`. Designed to be CI-safe:
 *   * `workers: 1` — the tests share one backend + one tenant (the dev org), so
 *     they must not run concurrently against the same data.
 *   * `retries` on CI — absorbs first-compile / cold-cache flakiness; a retry
 *     captures a trace for diagnosis.
 *   * `forbidOnly` on CI — a stray `test.only` fails the build instead of
 *     silently skipping the rest.
 *
 * Prerequisite: the isolated datastores must be up (`make test-stack-up`).
 */

const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3010);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    actionTimeout: 15_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
