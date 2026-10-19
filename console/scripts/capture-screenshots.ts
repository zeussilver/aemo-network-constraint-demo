/**
 * Capture README screenshots from a running console (default http://localhost:3000).
 *
 *   npm run build && npm start            # in one terminal, against the dev database
 *   npx tsx scripts/capture-screenshots.ts  # signs in as the seeded admin from .env
 *
 * Writes docs/images/console-*.png at the repository root.
 */
import "dotenv/config";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const BASE = process.env.SCREENSHOT_BASE_URL ?? "http://localhost:3000";
const OUT = path.join(process.cwd(), "..", "docs", "images");

async function main() {
  const email = process.env.SEED_ADMIN_EMAIL;
  const password = process.env.SEED_ADMIN_PASSWORD;
  if (!email || !password) throw new Error("SEED_ADMIN_EMAIL/PASSWORD must be set.");
  mkdirSync(OUT, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Fleet overview" }).waitFor();

  const shots: Array<[string, string]> = [
    ["console-overview.png", "/"],
    ["console-status.png", "/status"],
    ["console-customers.png", "/customers"],
    ["console-audit.png", "/audit"],
  ];
  for (const [file, url] of shots) {
    await page.goto(`${BASE}${url}`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: path.join(OUT, file), fullPage: true });
    console.log(`wrote ${file}`);
  }

  for (const [file, name] of [
    ["console-site-woolooga.png", "Woolooga Solar Farm"],
    ["console-site-cathedral-rocks.png", "Cathedral Rocks"],
  ] as const) {
    await page.goto(`${BASE}/sites`);
    await page.getByRole("link", { name }).first().click();
    await page.getByRole("heading", { name: "Operations" }).waitFor();
    await page.waitForTimeout(500); // let the chart finish its first layout
    await page.screenshot({ path: path.join(OUT, file), fullPage: true });
    console.log(`wrote ${file}`);
  }
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
