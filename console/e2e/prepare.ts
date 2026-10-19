/**
 * Reset the end-to-end database before a Playwright run: apply pending migrations, empty
 * the app tables, run the normal seed (registry, synthetic customers, sites, staff users
 * from SEED_* env), then add one
 * trading day of TEST FIXTURE ops data for CATHROCK so the ops views have something to
 * render without downloading NEMWeb. Fixture rows exist only in the e2e database.
 */
import "dotenv/config";
import { execSync } from "node:child_process";
import { createPrismaClient } from "../lib/prisma";

const url = process.env.E2E_DATABASE_URL;
if (!url) throw new Error("E2E_DATABASE_URL is not set.");
// Safety latch: this script empties tables, so it only ever runs on a database whose
// name ends in _e2e. A dev or production URL pasted here by mistake is refused.
const databaseName = new URL(url).pathname.replace(/^\//, "");
if (!databaseName.endsWith("_e2e")) {
  throw new Error(`Refusing to prepare "${databaseName}": the e2e database name must end in _e2e.`);
}
const env = { ...process.env, DATABASE_URL: url };

const APP_TABLES = [
  "audit_log", "unit", "site", "customer", "registry_unit", "session", "account", "verification", "user",
  "scada_reading", "dispatch_solution", "forecast_used", "forecast_origin_interval", "forecast_snapshot",
  "ingested_file", "ingest_run",
];

execSync("npx prisma migrate deploy", { env, stdio: "inherit" });

// The last complete trading day in NEM time (the current one is still running until 04:00
// tomorrow); a 10:00-12:00 window is capped 12 MW below UIGF.
const FIXTURE_SQL = `
WITH day AS (
  SELECT (((now() AT TIME ZONE 'Australia/Brisbane') - interval '4 hours 5 minutes')::date - 1) AS d
), intervals AS (
  SELECT (d + time '04:05' + (n * interval '5 minutes')) AT TIME ZONE 'Australia/Brisbane' AS t, n
  FROM day, generate_series(0, 287) AS n
), values AS (
  SELECT t, n, round((40 + 15 * sin(n / 20.0))::numeric, 1)::float AS uigf,
         (extract(hour FROM t AT TIME ZONE 'Australia/Brisbane') BETWEEN 10 AND 11) AS capped
  FROM intervals
), dispatch AS (
  INSERT INTO dispatch_solution (duid, settlement_date, intervention, initial_mw, total_cleared, availability, uigf, semidispatch_cap)
  SELECT 'CATHROCK', t, 0, uigf, CASE WHEN capped THEN uigf - 12 ELSE uigf END, uigf, uigf, CASE WHEN capped THEN 1 ELSE 0 END
  FROM values RETURNING 1
), scada AS (
  INSERT INTO scada_reading (duid, settlement_date, mw)
  SELECT 'CATHROCK', t, CASE WHEN capped THEN uigf - 12 ELSE uigf END FROM values RETURNING 1
), used AS (
  INSERT INTO forecast_used (duid, settlement_date, origin, forecast_priority, poe50)
  SELECT 'CATHROCK', t, CASE WHEN n % 4 = 0 THEN 'SCADA' ELSE 'AWEFS_ASEFS' END, CASE WHEN n % 4 = 0 THEN -1 ELSE 104 END, uigf
  FROM values RETURNING 1
), origins AS (
  INSERT INTO forecast_origin_interval (duid, settlement_date, origin, submissions, suppressed_aemo, suppressed_participant, max_priority, latest_poe50)
  SELECT 'CATHROCK', t, 'AWEFS_ASEFS', 4, 0, 0, 104, uigf FROM values RETURNING 1
)
INSERT INTO forecast_snapshot (duid, as_of, generated_at, horizon_hours, probability, risk_level, data_lag_hours, model_file, payload)
SELECT 'CATHROCK', max(t), now(), 6, 0.42, 'medium', 3.5, 'e2e-fixture.joblib',
       '{"risk_curve": [{"lead_hours": 1, "window_start": "x", "probability": 0.2, "risk_level": "medium"}], "limitations": "E2E fixture."}'::jsonb
FROM values;
`;

async function main() {
  const prisma = createPrismaClient(url);
  try {
    await prisma.$executeRawUnsafe(`TRUNCATE TABLE ${APP_TABLES.map((name) => `"${name}"`).join(", ")} RESTART IDENTITY CASCADE`);
    execSync("npx prisma db seed", { env, stdio: "inherit" });
    await prisma.$executeRawUnsafe(FIXTURE_SQL);
    console.log("E2E database reset, seeded, and loaded with CATHROCK fixture ops data.");
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
