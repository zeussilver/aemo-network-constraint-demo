import "dotenv/config";
import { createPrismaClient } from "@/lib/prisma";
import type { Actor } from "@/lib/services/types";

export const TEST_DATABASE_URL = process.env.TEST_DATABASE_URL;
export const hasTestDb = Boolean(TEST_DATABASE_URL);

export function testDb() {
  return createPrismaClient(TEST_DATABASE_URL);
}

export const admin: Actor = { id: "u-admin", email: "admin@example.com", name: "Admin", role: "admin" };
export const viewer: Actor = { id: "u-viewer", email: "viewer@example.com", name: "Viewer", role: "viewer" };
export const nobody: Actor = { id: "u-none", email: "none@example.com", name: "None", role: "none" };

type Db = ReturnType<typeof testDb>;

/** Empty every console table, then load a tiny registry fixture (shapes copied from the real CSV). */
export async function resetDb(db: Db) {
  await db.$executeRawUnsafe(
    'TRUNCATE TABLE "audit_log", "unit", "site", "customer", "registry_unit" RESTART IDENTITY CASCADE',
  );
  const common = {
    technology: "test",
    classification: "Semi-Scheduled",
    scheduleType: "SEMI-SCHEDULED",
    dispatchType: "GENERATOR",
    registrationListFile: "fixture",
    standingDataFile: "fixture",
  };
  await db.registryUnit.createMany({
    data: [
      { duid: "CATHROCK", stationName: "Cathedral Rocks", stationId: "CATHROCK", region: "SA1", fuel: "WIND", connectionPointId: "SCRK", registeredCapacityMw: 66, maxCapacityMw: 66, ...common },
      { duid: "GSWF1A", stationName: "Goyder South Wind Farm 1A", stationId: "GSWF1A", region: "SA1", fuel: "WIND", connectionPointId: "SROB1G", registeredCapacityMw: 209, maxCapacityMw: 201, ...common },
      { duid: "WOOLGSF1", stationName: "Woolooga Solar Farm", stationId: "WOOLGSF", region: "QLD1", fuel: "SOLAR", connectionPointId: "QWLS1W", registeredCapacityMw: 222.5, maxCapacityMw: 176, ...common },
      { duid: "GOONSF1", stationName: "Goonumbla Solar Farm", stationId: "GOONSF", region: "NSW1", fuel: "SOLAR", connectionPointId: "NPG12G", registeredCapacityMw: 85, maxCapacityMw: 69, ...common },
    ],
  });
}

export const customerFixture = {
  name: "Synthetic Customer T",
  contactEmail: "ops-t@example.com",
  status: "ACTIVE" as const,
  notes: null,
};

export const siteFixture = (customerId: string) => ({
  name: "Test Wind Site",
  customerId,
  region: "SA1" as const,
  fuel: "WIND" as const,
  registeredCapacityMw: 66,
  status: "OPERATING" as const,
});
