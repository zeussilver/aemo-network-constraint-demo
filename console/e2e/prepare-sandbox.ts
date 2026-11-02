/**
 * Reset the sandbox end-to-end database the way a sandbox deployment is set up: apply
 * migrations, empty it, create only the staff accounts (SEED_SCOPE=staff), then have the
 * simulator load the training world anchored at now (src/aemo_constraint_demo/sim_sandbox.py).
 *
 * The simulator runs on SIM_PYTHON (default: the repo's .venv if there is one, else
 * python3), which needs psycopg.
 */
import "dotenv/config";
import { execFileSync, execSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { createPrismaClient } from "../lib/prisma";

const url = process.env.SANDBOX_E2E_DATABASE_URL;
if (!url) throw new Error("SANDBOX_E2E_DATABASE_URL is not set.");
// Same latch as e2e/prepare.ts, and the simulator applies its own (_sandbox or _e2e).
const databaseName = new URL(url).pathname.replace(/^\//, "");
if (!databaseName.endsWith("_e2e")) {
  throw new Error(`Refusing to prepare "${databaseName}": the sandbox e2e database name must end in _e2e.`);
}
const env = { ...process.env, DATABASE_URL: url, SEED_SCOPE: "staff" };
const repoRoot = path.resolve(process.cwd(), "..");
const python = process.env.SIM_PYTHON ?? (existsSync(path.join(repoRoot, ".venv/bin/python")) ? path.join(repoRoot, ".venv/bin/python") : "python3");

execSync("npx prisma migrate deploy", { env, stdio: "inherit" });

async function main() {
  const prisma = createPrismaClient(url);
  try {
    await prisma.$executeRawUnsafe('TRUNCATE TABLE "session", "account", "verification", "user" RESTART IDENTITY CASCADE');
  } finally {
    await prisma.$disconnect();
  }
  execSync("npx prisma db seed", { env, stdio: "inherit" });
  execFileSync(python, ["-m", "src.aemo_constraint_demo.sim_sandbox", "load", "--world", "training", "--anchor", "now", "--database-url", url!], {
    cwd: repoRoot,
    stdio: "inherit",
  });
  console.log("Sandbox e2e database reset, staff seeded, and the training world loaded.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
