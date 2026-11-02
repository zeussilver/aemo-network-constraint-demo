/**
 * Seed the console database. Idempotent: safe to run after every migration.
 *
 * - RegistryUnit: real AEMO standing data from data/site_registry.csv
 *   (built by scripts/build_site_registry.py).
 * - Customer: synthetic, flagged isSynthetic, with example.com contacts.
 * - Site + Unit: one site per registered station, assigned round-robin to the synthetic
 *   customers. The assignment is invented; the stations and DUIDs are real.
 */
import "dotenv/config";
import { hashPassword } from "better-auth/crypto";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { parseCsv } from "../lib/csv";
import { createPrismaClient } from "../lib/prisma";
import { Fuel, Region } from "../lib/generated/prisma/enums";

const REGISTRY_CSV = process.env.SITE_REGISTRY_CSV ?? path.join(process.cwd(), "..", "data", "site_registry.csv");

const SYNTHETIC_CUSTOMERS = [
  { name: "Synthetic Customer A", contactEmail: "ops-a@example.com", status: "ACTIVE" },
  { name: "Synthetic Customer B", contactEmail: "ops-b@example.com", status: "ACTIVE" },
  { name: "Synthetic Customer C", contactEmail: "ops-c@example.com", status: "ACTIVE" },
  { name: "Synthetic Customer D", contactEmail: "ops-d@example.com", status: "ONBOARDING" },
] as const;

function asRegion(value: string): Region {
  if (!(value in Region)) throw new Error(`Unknown region ${value}`);
  return value as Region;
}

function asFuel(value: string): Fuel {
  if (!(value in Fuel)) throw new Error(`Unknown fuel ${value}`);
  return value as Fuel;
}

function asNumber(value: string, field: string): number {
  const number = Number(value);
  if (value === "" || !Number.isFinite(number)) throw new Error(`Invalid ${field}: ${value}`);
  return number;
}

/**
 * Staff accounts come from environment variables, never from committed values. Sign-up is
 * disabled in the app, so this is the only way an account is created. Re-running the seed
 * resets the role and password to the env values.
 */
async function seedStaffUsers(prisma: ReturnType<typeof createPrismaClient>): Promise<number> {
  const staff = [
    { role: "admin", email: process.env.SEED_ADMIN_EMAIL, password: process.env.SEED_ADMIN_PASSWORD },
    { role: "viewer", email: process.env.SEED_VIEWER_EMAIL, password: process.env.SEED_VIEWER_PASSWORD },
  ];
  let seeded = 0;
  for (const { role, email, password } of staff) {
    if (!email || !password) {
      console.warn(`SEED_${role.toUpperCase()}_EMAIL/PASSWORD not set; skipping the ${role} account.`);
      continue;
    }
    if (password.length < 12) throw new Error(`The ${role} password must be at least 12 characters.`);
    const hash = await hashPassword(password);
    const user = await prisma.user.upsert({
      where: { email: email.toLowerCase() },
      create: { id: randomUUID(), email: email.toLowerCase(), name: `Demo ${role}`, emailVerified: true, role },
      update: { role },
    });
    const account = await prisma.account.findFirst({ where: { userId: user.id, providerId: "credential" } });
    if (account) {
      await prisma.account.update({ where: { id: account.id }, data: { password: hash } });
    } else {
      await prisma.account.create({
        data: { id: randomUUID(), accountId: user.id, providerId: "credential", userId: user.id, password: hash },
      });
    }
    seeded++;
  }
  return seeded;
}

async function main() {
  const prisma = createPrismaClient();
  try {
    // A sandbox database gets its sites from the simulator (sim_sandbox.py), so it only
    // needs the staff accounts.
    if (process.env.SEED_SCOPE === "staff") {
      console.log(`Seeded ${await seedStaffUsers(prisma)} staff users (SEED_SCOPE=staff).`);
      return;
    }
    const registry = parseCsv(readFileSync(REGISTRY_CSV, "utf8"));
    for (const row of registry) {
      const data = {
        stationName: row.station_name,
        stationId: row.station_id,
        region: asRegion(row.region_id),
        fuel: asFuel(row.fuel),
        technology: row.technology,
        classification: row.classification,
        scheduleType: row.schedule_type,
        dispatchType: row.dispatch_type,
        connectionPointId: row.connection_point_id,
        registeredCapacityMw: asNumber(row.registered_capacity_mw, "registered_capacity_mw"),
        maxCapacityMw: asNumber(row.max_capacity_mw, "max_capacity_mw"),
        registrationListFile: row.registration_list_file,
        standingDataFile: row.standing_data_file,
      };
      await prisma.registryUnit.upsert({ where: { duid: row.duid }, create: { duid: row.duid, ...data }, update: data });
    }

    const customers = [];
    for (const customer of SYNTHETIC_CUSTOMERS) {
      customers.push(
        await prisma.customer.upsert({
          where: { name: customer.name },
          create: { ...customer, isSynthetic: true, notes: "Synthetic demo customer. Not a real organisation." },
          update: {},
        }),
      );
    }

    let created = 0;
    for (const [index, row] of registry.entries()) {
      const existingUnit = await prisma.unit.findUnique({ where: { duid: row.duid } });
      if (existingUnit) continue;
      const site = await prisma.site.create({
        data: {
          name: row.station_name,
          customerId: customers[index % customers.length].id,
          region: asRegion(row.region_id),
          fuel: asFuel(row.fuel),
          registeredCapacityMw: asNumber(row.registered_capacity_mw, "registered_capacity_mw"),
          units: { create: { duid: row.duid } },
        },
      });
      created += site ? 1 : 0;
    }

    const users = await seedStaffUsers(prisma);

    console.log(
      `Seeded ${registry.length} registry units, ${customers.length} synthetic customers, ${created} new sites, ` +
        `${users} staff users.`,
    );
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
