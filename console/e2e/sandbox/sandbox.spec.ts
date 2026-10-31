import { expect, type Page, test } from "@playwright/test";
import { signIn } from "../helpers";

/**
 * The console in sandbox mode, on the simulator's training world (e2e/prepare-sandbox.ts):
 * every scripted scenario must be visible where the training task says to look, and the
 * training answers (computed by the simulator) must agree with what the console computes.
 */

// The innermost section with that heading: the card, not the Operations section around it.
const card = (page: Page, title: string) => page.locator("section", { has: page.getByRole("heading", { name: title }) }).last();

test("the sandbox says so, and the farm whose SCADA went quiet is flagged", async ({ page }) => {
  await signIn(page, "viewer");
  await expect(page.getByRole("note", { name: "Sandbox" })).toContainText("simulated");
  await expect(page.getByRole("row", { name: /Sim Solar Farm B/ })).toContainText("Stale");
  await expect(page.getByRole("row", { name: /Sim Wind Farm A/ })).not.toContainText("Stale");

  // Fleet-wide, SCADA is still fresh: only the per-site badge shows the silent farm. The
  // scripted failed load shows on the same chip.
  const scada = page.getByRole("list", { name: "Data freshness" }).getByRole("listitem").filter({ hasText: "Actual MW (SCADA)" });
  await expect(scada).toContainText("Fresh");
  await expect(scada).toContainText("Last ingest run failed");
});

test("the constraint is on the site page and the training answer matches the console's estimate", async ({ page }) => {
  await signIn(page, "viewer");
  const row = page.getByRole("row", { name: /Sim Wind Farm A/ });
  const consoleMwh = Number(/([\d.]+) MWh/.exec((await row.textContent()) ?? "")?.[1]);
  expect(consoleMwh).toBeGreaterThan(300);

  await row.getByRole("link", { name: "Sim Wind Farm A" }).click();
  await expect(page.getByText(/Simulated data, in the shape of AEMO's NEMWeb tables/)).toBeVisible();
  const capped = Number(/(\d+) of 288 intervals capped/.exec((await page.getByText(/of 288 intervals capped/).textContent()) ?? "")?.[1]);
  expect(capped).toBeGreaterThanOrEqual(120); // the scripted ten hours, plus any background cap

  await page.getByRole("link", { name: "Training" }).click();
  const task = page.getByRole("form", { name: "A network constraint holds a wind farm back" });
  await task.getByRole("button", { name: "Show the answer" }).click();
  const revealed = task.getByRole("status");
  await expect(revealed).toContainText("Sim Wind Farm A");
  const answerMwh = Number(/([\d.]+) \(±/.exec((await revealed.textContent()) ?? "")?.[1]);
  expect(Math.abs(answerMwh - consoleMwh)).toBeLessThanOrEqual(0.1); // Python and TypeScript agree
});

test("AEMO's suppression of a self-forecast shows on the site page", async ({ page }) => {
  await signIn(page, "viewer");
  await page.getByRole("row", { name: /Sim Solar Farm C/ }).getByRole("link", { name: "Sim Solar Farm C" }).click();
  await expect(card(page, "Which forecast dispatch used").getByRole("cell", { name: "AWEFS_ASEFS" })).toBeVisible();
  const suppressed = card(page, "Forecast submissions and suppression").getByRole("row", { name: /SIMSOLC/ }).getByRole("cell").last();
  expect(Number((await suppressed.textContent())?.replace(/,/g, ""))).toBeGreaterThan(0);
});

test("the Data status page lists the failed run's error", async ({ page }) => {
  await signIn(page, "viewer");
  await page.getByRole("link", { name: "Data status" }).click();
  await expect(page.getByText(/BadZipFile/).first()).toBeVisible();
});

test("a trainee checks answers on the Training page", async ({ page }) => {
  await signIn(page, "viewer");
  // Find the answer the way a trainee would: on the silent farm's own page.
  await page.getByRole("row", { name: /Sim Solar Farm B/ }).getByRole("link", { name: "Sim Solar Farm B" }).click();
  await expect(page.getByText(/Stale: no SCADA for/)).toBeVisible();
  // The "Latest actual" stat's note, e.g. "31 Oct, 13:20 NEM" (not the trading-day line).
  const note = page.getByText(/^\d{2} [A-Z][a-z]{2}, \d{2}:\d{2} NEM$/).first();
  const lastReading = /(\d{2}:\d{2}) NEM/.exec((await note.textContent()) ?? "")?.[1];
  expect(lastReading).toBeTruthy();

  await page.getByRole("link", { name: "Training" }).click();
  const outage = page.getByRole("form", { name: "A farm stops sending SCADA" });
  await outage.getByLabel("Site").selectOption({ label: "Sim Solar Farm B" });
  await outage.getByLabel(/Time of its last reading/).fill(lastReading!);
  await outage.getByRole("button", { name: "Check answer" }).click();
  await expect(outage.getByText("Correct.")).toBeVisible();

  const ingest = page.getByRole("form", { name: "The latest SCADA load fails" });
  await ingest.getByLabel("Source").selectOption("dispatch");
  await ingest.getByRole("button", { name: "Check answer" }).click();
  await expect(ingest.getByText(/Not yet/)).toBeVisible();
  await expect(ingest.getByLabel("Source")).toHaveValue("dispatch"); // the answer survives the check
});
