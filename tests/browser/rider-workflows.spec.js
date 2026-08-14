import { expect, test } from "@playwright/test";

async function openPlanner(page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Choose for the arrival that matters." })).toBeVisible();
  await expect(page.getByText("ready · loopback local")).toBeVisible();
  await page.getByLabel("Origin station").selectOption("alpha");
  await page.getByLabel("Destination station").selectOption("bravo");
}

async function compare(page) {
  await page.getByRole("button", { name: "Compare routes" }).click();
  await expect(page.getByRole("heading", { name: "Your route comparison" })).toBeVisible();
}

function localUtcInput(date) {
  return date.toISOString().slice(0, 16);
}

test("direct target-not-met trip remains actionable and text-complete", async ({ page, context }) => {
  await openPlanner(page);
  await page.getByLabel("Reliability target").selectOption("0.80");
  await page.locator("#cap").fill("0");
  await compare(page);

  await expect(page.getByText(/Target not met/)).toBeVisible();
  await expect(page.locator("#safer-summary")).toHaveText("Direct itinerary");
  await expect(page.locator("#deadline-probability")).toHaveText("75% estimate");
  await expect(page.locator("#fastest-model-status")).toContainText("Probability unavailable");
  await expect(page.getByRole("link", { name: "How this estimate is evaluated" })).toBeVisible();

  await page.getByRole("button", { name: "Start this trip" }).click();
  await expect(page.locator("#trip-state")).toContainText("Trip active");
  await page.getByRole("button", { name: "Confirm boarded" }).click();
  await expect(page.locator("#trip-state")).toContainText("on final leg");
  await expect(page.getByRole("list", { name: "Live trip events" })).toContainText("deterministic state");
  expect(await context.cookies()).toEqual([]);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(page.url()).not.toContain("Bearer");
  await page.getByRole("button", { name: "Stop trip" }).click();
  await expect(page.locator("#trip-state")).toContainText("ended");
});

test("transfer trip exposes selected uncertainty and schedule-only recovery", async ({ page }) => {
  await openPlanner(page);
  await compare(page);

  await expect(page.getByText(/Synthetic fixture. Estimated target met/)).toBeVisible();
  await expect(page.locator("#safer-summary")).toHaveText("One-transfer itinerary");
  await expect(page.locator("#deadline-probability")).toHaveText("94% estimate");
  await expect(page.getByRole("row", { name: /p50/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /p90/ })).toBeVisible();
  await expect(page.locator("#backup-summary")).toContainText("Backup: one transfer");
  if (process.env.ARRIVE90_CAPTURE_UI === "1") {
    await page.screenshot({ path: "artifacts/demos/milestone-7-synthetic-ui.png", fullPage: true });
  }

  await page.getByRole("button", { name: "Start this trip" }).click();
  await page.getByRole("button", { name: "Confirm boarded" }).click();
  await expect(page.locator("#trip-state")).toContainText("on first leg");
  await page.getByRole("button", { name: "Confirm at transfer" }).click();

  const recovery = page.getByRole("heading", { name: "Recovery option after confirmed transfer state" });
  await expect(recovery).toBeVisible();
  await expect(page.locator("#recovery-card")).toContainText("Conditional schedule action");
  await expect(page.locator("#recovery-card")).toContainText("New deadline probabilityNot computed");
  await expect(page.locator("#recovery-card")).toContainText("New arrival quantilesNot computed");
  await expect(page.locator("#recovery-card")).toContainText("Reliability target statusNot applicable");
  await page.getByRole("button", { name: "Use this schedule option" }).click();
  await expect(page.locator("#trip-state")).toContainText("on final leg");
  await expect(page.getByRole("list", { name: "Live trip events" })).toContainText("recovery schedule only");
});

test("stale, abstained, sparse, unsupported-target, and future branches stay explicit", async ({ page }) => {
  await openPlanner(page);

  await page.getByLabel("Origin station").selectOption("alpha-stale");
  await compare(page);
  await expect(page.getByText(/Stale feed: schedule only/)).toBeVisible();
  await expect(page.locator("#selected-model-status")).toContainText("unavailable");
  await expect(page.getByRole("button", { name: "Start this trip" })).toBeDisabled();

  await page.getByLabel("Origin station").selectOption("alpha-absent");
  await compare(page);
  await expect(page.getByText(/Model abstained/)).toBeVisible();

  await page.getByLabel("Origin station").selectOption("alpha-sparse");
  await compare(page);
  await expect(page.getByText(/Insufficient evidence/)).toBeVisible();
  await expect(page.locator("#probability-panel")).toBeHidden();

  await page.getByLabel("Origin station").selectOption("alpha");
  await page.getByLabel("Reliability target").selectOption("0.95");
  await compare(page);
  await expect(page.getByText(/Insufficient evidence/)).toBeVisible();
  await expect(page.locator("#deadline-probability")).toHaveText("94% estimate");

  const ready = new Date(Date.now() + 20 * 60_000);
  const deadline = new Date(ready.getTime() + 30 * 60_000);
  await page.getByLabel("Ready at").fill(localUtcInput(ready));
  await page.getByLabel("Arrive by").fill(localUtcInput(deadline));
  await compare(page);
  await expect(page.getByText(/Future request: schedule only/)).toBeVisible();
  await expect(page.getByText("Search again within 15 minutes of readiness before starting a trip.")).toBeVisible();
  await expect(page.locator("#probability-panel")).toBeHidden();
});

test("normalization, keyboard landmarks, and no-map use are visible", async ({ page }) => {
  await openPlanner(page);
  await page.locator("[data-optional-map]").evaluate((node) => node.remove());
  const current = new Date();
  current.setSeconds(0, 0);
  await page.getByLabel("Ready at").fill(localUtcInput(new Date(current.getTime() - 60_000)));
  await page.getByLabel("Arrive by").fill(localUtcInput(new Date(current.getTime() + 31 * 60_000)));
  await compare(page);
  await expect(page.getByText("Times were conservatively normalized.")).toBeVisible();
  await expect(page.locator("#normalization")).toContainText("Requested ready");
  await expect(page.locator("#normalization")).toContainText("Effective deadline");

  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What this interface does not claim" })).toBeVisible();
  await expect(page.getByText("Schedule comparator")).toBeVisible();
  await expect(page.getByText("Bounded decision")).toBeVisible();
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to journey planner" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#planner$/);
});
