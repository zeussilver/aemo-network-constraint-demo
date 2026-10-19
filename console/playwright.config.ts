import { defineConfig, devices } from "@playwright/test";
import "dotenv/config";

const PORT = 3100;
const baseURL = `http://localhost:${PORT}`;

/**
 * Runs against a production build (`npm run build` first) on a separate database that
 * `npm run e2e:prepare` resets. Tests share that database, so they run one at a time.
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: { baseURL, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
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
});
