import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8080",
    // The production spec can target an authenticated deployment. Browser
    // artifacts can persist filled credentials, request headers, and response
    // bodies, so every project is artifact-free.
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      // Axe is injected only by Playwright. The deployed CSP remains strict and is
      // asserted separately through response-header probes.
      testIgnore: /live-smoke\.spec\.js/,
      use: { ...devices["Desktop Chrome"], bypassCSP: true, viewport: { width: 1920, height: 1080 } },
    },
    {
      // The live smoke test authenticates every request with a real tenant
      // bearer token. Traces, videos, and screenshots all persist request
      // headers and response bodies to disk, so the global retain-on-failure
      // defaults are overridden to off here — a failing live run must not leave
      // a credential sitting in test-results/. The spec re-asserts this with
      // test.use() so the guarantee survives being run under another project.
      name: "live-smoke",
      testMatch: /live-smoke\.spec\.js/,
      use: {
        ...devices["Desktop Chrome"],
        bypassCSP: true,
        trace: "off",
        video: "off",
        screenshot: "off",
      },
    },
  ],
});
