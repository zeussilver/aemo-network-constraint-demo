import { defineConfig, devices } from "@playwright/test";
import "dotenv/config";

const PORT = 3100;
const SANDBOX_PORT = 3101;
const baseURL = `http://localhost:${PORT}`;
const sandboxURL = `http://localhost:${SANDBOX_PORT}`;
const browser = { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } };

/**
 * Runs against a production build (`npm run build` first), as two projects:
 *
 * - "chromium": the console on a separate database that `npm run e2e:prepare` resets.
 * - "sandbox": the same build in sandbox mode (CONSOLE_SANDBOX=1) on its own database,
 *   which the simulator fills with the training world (e2e/prepare-sandbox.ts).
 *
 * Tests share their database, so they run one at a time.
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: { trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "chromium", testIgnore: "**/sandbox/**", use: { ...browser, baseURL } },
    { name: "sandbox", testDir: "e2e/sandbox", use: { ...browser, baseURL: sandboxURL } },
  ],
  webServer: [
    {
      command: "node .next/standalone/server.js",
      url: `${baseURL}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PORT: String(PORT),
        HOSTNAME: "localhost",
        DATABASE_URL: process.env.E2E_DATABASE_URL ?? "",
        BETTER_AUTH_URL: baseURL,
      },
    },
    {
      command: "node .next/standalone/server.js",
      url: `${sandboxURL}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PORT: String(SANDBOX_PORT),
        HOSTNAME: "localhost",
        DATABASE_URL: process.env.SANDBOX_E2E_DATABASE_URL ?? "",
        BETTER_AUTH_URL: sandboxURL,
        CONSOLE_SANDBOX: "1",
      },
    },
  ],
});
