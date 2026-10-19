import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("an anonymous visitor is sent to sign-in", async ({ page }) => {
  await page.goto("/sites");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Site Ops Console" })).toBeVisible();
});

test("a wrong password shows one generic message", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@example.com");
  await page.getByLabel("Password").fill("definitely-not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  // Not getByRole("alert"): Next's route announcer is also an alert region.
  await expect(page.getByText("Email or password is incorrect.")).toBeVisible();
  await expect(page.getByLabel("Email")).toHaveValue("admin@example.com");
});

test("a viewer can read but sees no write controls and cannot open admin pages", async ({ page }) => {
  await signIn(page, "viewer");
  await expect(page.getByRole("link", { name: "Audit log" })).toHaveCount(0);

  await page.getByRole("link", { name: "Customers" }).click();
  await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();
  await expect(page.getByRole("link", { name: "New customer" })).toHaveCount(0);

  await page.getByRole("link", { name: "Synthetic Customer A" }).click();
  await expect(page.getByRole("link", { name: "Edit" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Archive/ })).toHaveCount(0);

  for (const path of ["/customers/new", "/sites/new", "/audit"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: /You do not have access/ })).toBeVisible();
  }
});

test("signing out ends the session", async ({ page }) => {
  await signIn(page, "viewer");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});
