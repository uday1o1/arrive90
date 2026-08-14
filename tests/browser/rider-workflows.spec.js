import { expect, test } from "@playwright/test";

async function openExplorer(page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Replay a prediction before revealing what happened." })).toBeVisible();
  await expect(page.getByText("ready · verified local artifacts")).toBeVisible();
  await expect(page.getByLabel("Held-out replay", { exact: true })).not.toHaveValue("");
}

async function scoreReplay(page) {
  await page.getByRole("button", { name: "Score held-out replay" }).click();
  await expect(page.getByRole("heading", { name: "Three honest points of comparison" })).toBeVisible();
}

test("full replay selection, prediction, and outcome reveal is honest", async ({ page, context }) => {
  const requested = [];
  page.on("request", (request) => requested.push(request.url()));
  await openExplorer(page);
  await page.getByLabel("Direction").selectOption("1");
  await scoreReplay(page);

  expect(requested.some((url) => url.endsWith("/outcome"))).toBe(false);
  await expect(page.getByText("Official schedule diagnostic")).toBeVisible();
  await expect(page.getByText("Training-only empirical midpoint")).toBeVisible();
  await expect(page.getByText("Promoted survival model")).toBeVisible();
  await expect(page.getByRole("table", { name: "Text alternative for the arrival CDF" })).toBeVisible();
  await expect(page.getByRole("row", { name: /p50/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /p80/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /p90/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cutoff-visible history" })).toBeVisible();
  await expect(page.getByText("Hidden until explicit reveal")).toBeVisible();
  await expect(page.locator("#outcome-result")).toBeHidden();

  await page.getByRole("button", { name: "Reveal actual outcome" }).click();
  await expect(page.locator("#outcome-result")).toBeVisible();
  await expect(page.locator("#outcome-result")).toContainText(/INTERVAL RESOLVED|LEFT CENSORED|RIGHT CENSORED|MISSING STOP OBSERVATION|OVER WIDTH INTERVAL|SESSION DISCONTINUITY/);
  expect(requested.some((url) => url.endsWith("/outcome"))).toBe(true);
  expect(await context.cookies()).toEqual([]);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);

  if (process.env.ARRIVE90_CAPTURE_DEMO === "1") {
    const video = page.video();
    await page.screenshot({ path: "artifacts/demos/replay-explorer.png", fullPage: true });
    await page.close();
    await video.saveAs("artifacts/demos/replay-explorer-walkthrough.webm");
  }
});

test("fixed horizons, calibration diagnostics, and evidence remain visible", async ({ page }) => {
  await openExplorer(page);
  await page.getByLabel("Prediction horizon").selectOption("1200");
  await scoreReplay(page);

  await expect(page.locator("#model-detail")).toContainText("20m 00s");
  await expect(page.getByRole("table", { name: "Text alternative for held-out calibration bins" })).toBeVisible();
  await expect(page.locator("#calibration-summary")).toContainText("ECE");
  await expect(page.getByRole("heading", { name: "Artifact lineage" })).toBeVisible();
  await expect(page.locator("#lineage")).toContainText("FULL-normal-scale-0p5");
  await expect(page.getByText("199,364")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Methodology and limitations stay beside the demo." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Inspect the machine-readable evaluation evidence" })).toHaveAttribute("href", "/v1/explorer/evidence");
});

test("empty filtering and unsupported requests explain the problem", async ({ page, request }) => {
  await openExplorer(page);
  await page.getByLabel("Origin station").selectOption({ index: 1 });
  await page.getByLabel("Destination station").selectOption({ index: 1 });
  if (await page.getByText("No held-out replays match those controls.").isVisible()) {
    await expect(page.getByRole("button", { name: "Score held-out replay" })).toBeDisabled();
  }

  const invalidLine = await request.get("/v1/explorer/inventory?line_id=Red");
  expect(invalidLine.status()).toBe(422);
  expect((await invalidLine.text())).toContain("only retained line");
  const invalidHorizon = await request.get("/v1/explorer/reliability?horizon_seconds=42");
  expect(invalidHorizon.status()).toBe(422);
  expect((await invalidHorizon.text())).toContain("unsupported reliability horizon");
});

test("primary workflow is keyboard reachable and never depends on color", async ({ page }) => {
  await openExplorer(page);
  await page.keyboard.press("Home");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to replay controls" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#controls$/);
  await expect(page.getByLabel("Line", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Direction")).toBeVisible();
  await expect(page.getByLabel("Origin station")).toBeVisible();
  await expect(page.getByLabel("Destination station")).toBeVisible();
  await expect(page.getByText(/meaning never depends on color alone/i)).toBeHidden();
  await scoreReplay(page);
  await expect(page.getByText(/meaning never depends on color alone/i)).toBeVisible();
  await expect(page.locator("#calibration-rows")).toContainText("Supported bin");
});
