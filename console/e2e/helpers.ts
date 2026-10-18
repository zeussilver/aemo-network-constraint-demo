import { expect, type Page } from "@playwright/test";

type Role = "admin" | "viewer";

function credentials(role: Role) {
  const email = process.env[`SEED_${role.toUpperCase()}_EMAIL`];
  const password = process.env[`SEED_${role.toUpperCase()}_PASSWORD`];
  if (!email || !password) throw new Error(`SEED_${role.toUpperCase()}_EMAIL/PASSWORD must be set for e2e tests.`);
  return { email, password };
}

export async function signIn(page: Page, role: Role) {
  const { email, password } = credentials(role);
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Fleet overview" })).toBeVisible();
}

/** Unique per run so repeated local runs never collide on unique names. */
export const runTag = `${Date.now().toString(36)}`;
