import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  outputDir: "./artifacts/runtime/playwright",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["line"],
    ["json", { outputFile: "artifacts/runtime/playwright-results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:8876",
    browserName: "chromium",
    timezoneId: "UTC",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: process.env.ARRIVE90_CAPTURE_DEMO === "1" ? "on" : "off",
  },
  webServer: {
    command: "UV_CACHE_DIR=.cache/uv uv run --no-sync python scripts/run_browser_fixture.py --port 8876",
    url: "http://127.0.0.1:8876/v1/system/status",
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
