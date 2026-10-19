import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("the fleet overview and a site's ops view render the loaded data", async ({ page }) => {
  await signIn(page, "viewer");
  await expect(page.getByRole("list", { name: "Data freshness" })).toBeVisible();

  const row = page.getByRole("row", { name: /Cathedral Rocks/ });
  await expect(row).toContainText("CATHROCK");
  await expect(row).toContainText("42% · medium");
  // Fixture: 24 capped intervals, 12 MW short each, 5 minutes = 24 MWh.
  await expect(row).toContainText("24.0 MWh");

  await row.getByRole("link", { name: "Cathedral Rocks" }).click();
  await expect(page.getByRole("heading", { name: "Operations" })).toBeVisible();
  await expect(page.getByText("24 of 288 intervals capped")).toBeVisible();
  await expect(page.getByRole("img", { name: /Actual output, available capacity/ }).locator("svg path.recharts-curve").first()).toBeVisible();
  await expect(page.getByRole("cell", { name: "AWEFS_ASEFS" }).first()).toBeVisible();
  await expect(page.getByText("No constraint-risk model")).toHaveCount(0);
});

test("health and readiness endpoints answer without a session", async ({ request }) => {
  const health = await request.get("/api/health");
  expect(health.status()).toBe(200);

  const ready = await request.get("/api/ready");
  const body = await ready.json();
  expect(body.database).toBe("ok");
  expect(body.sources.map((source: { source: string }) => source.source)).toEqual(["scada", "dispatch", "intermittent", "forecast"]);
  // Fixture data is a day old, so SCADA (2 h limit) is stale and readiness reports 503.
  expect(ready.status()).toBe(503);
  expect(body.sources.find((source: { source: string }) => source.source === "scada").status).toBe("stale");
});
