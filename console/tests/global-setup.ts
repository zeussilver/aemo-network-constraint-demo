import { execSync } from "node:child_process";
import "dotenv/config";

/** Apply migrations to the integration-test database once per Vitest run. */
export default function setup() {
  const url = process.env.TEST_DATABASE_URL;
  if (!url) {
    console.warn("TEST_DATABASE_URL not set: database integration tests will be skipped.");
    return;
  }
  execSync("npx prisma migrate deploy", { env: { ...process.env, DATABASE_URL: url }, stdio: "pipe" });
}
