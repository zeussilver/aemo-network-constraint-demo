import { expect, test } from "@playwright/test";
import { runTag, signIn } from "./helpers";

test("an admin onboards a customer and site, moves a unit, and the audit log records it", async ({ page }) => {
  const customerName = `E2E Customer ${runTag}`;
  const siteName = `E2E Wind Site ${runTag}`;
  page.on("dialog", (dialog) => dialog.accept());
  await signIn(page, "admin");

  // Create a customer; the first submit fails validation and keeps the typed name.
  await page.getByRole("link", { name: "Customers" }).click();
  await page.getByRole("link", { name: "New customer" }).click();
  await page.getByLabel("Name").fill(customerName);
  await page.getByLabel("Operations contact email").fill("not-an-email");
  await page.getByRole("button", { name: "Create customer" }).click();
  await expect(page.getByText("Enter a valid email address")).toBeVisible();
  await expect(page.getByLabel("Name")).toHaveValue(customerName);
  await page.getByLabel("Operations contact email").fill("e2e@example.com");
  await page.getByRole("button", { name: "Create customer" }).click();
  await expect(page.getByRole("heading", { name: customerName })).toBeVisible();
  await expect(page.getByText("Synthetic", { exact: true })).toBeVisible();

  // Edit it.
  await page.getByRole("link", { name: "Edit" }).click();
  await page.getByLabel("Status").selectOption("ACTIVE");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Record version").locator("..")).toContainText("2");

  // Add a site for it.
  await page.getByRole("link", { name: "Add a site for this customer" }).click();
  await page.getByLabel("Site name").fill(siteName);
  await page.getByLabel("NEM region").selectOption("SA1");
  await page.getByLabel("Fuel").selectOption("WIND");
  await page.getByLabel("Registered capacity (MW)").fill("209");
  await page.getByRole("button", { name: "Create site" }).click();
  await expect(page.getByRole("heading", { name: siteName })).toBeVisible();
  await expect(page.getByText("No unassigned SA1 wind DUIDs")).toBeVisible();

  // Free GSWF1A from its seeded site, then assign it to the new site.
  await page.goto("/sites?q=GSWF1A");
  await page.getByRole("link", { name: "Goyder South Wind Farm 1A" }).click();
  await page.getByRole("row", { name: /GSWF1A/ }).getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("No units assigned.")).toBeVisible();

  await page.goto("/sites");
  await page.getByRole("link", { name: siteName }).click();
  await page.getByLabel("Add a DUID from the AEMO registry").selectOption("GSWF1A");
  await page.getByRole("button", { name: "Assign unit" }).click();
  await expect(page.getByText("GSWF1A assigned.")).toBeVisible();
  await expect(page.getByRole("cell", { name: "GSWF1A" })).toBeVisible();

  // The audit log shows the chain of changes, newest first.
  await page.getByRole("link", { name: "Audit log" }).click();
  const actions = page.getByRole("row").filter({ hasText: "admin@example.com" });
  await expect(actions.nth(0)).toContainText("assign unit");
  await expect(actions.nth(1)).toContainText("remove unit");
  await expect(actions.nth(2)).toContainText("create");
  await expect(actions.nth(3)).toContainText("update");
});

test("archiving a customer with active sites is refused with a reason", async ({ page }) => {
  await signIn(page, "admin");
  await page.getByRole("link", { name: "Customers" }).click();
  await page.getByRole("link", { name: "Synthetic Customer B" }).click();
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Archive customer" }).click();
  await expect(page.getByText(/still has \d+ active site\(s\)/)).toBeVisible();
});
