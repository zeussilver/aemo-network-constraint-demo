import type { Fuel, Prisma, Region } from "../generated/prisma/client";
import type { SiteInput } from "../validation";
import { assertCan, writeAudit } from "./audit";
import { FOREIGN_KEY_VIOLATION, prismaCode, UNIQUE_VIOLATION } from "./prisma-errors";
import { ConflictError, NotFoundError, RuleError, type Actor, type Db } from "./types";

export const SITE_PAGE_SIZE = 20;

export type SiteFilters = {
  q?: string;
  region?: Region;
  fuel?: Fuel;
  customerId?: string;
  includeArchived?: boolean;
  page?: number;
};

export async function listSites(db: Db, actor: Actor, filters: SiteFilters = {}) {
  assertCan(actor, "site:read");
  const page = Math.max(1, filters.page ?? 1);
  const where: Prisma.SiteWhereInput = {
    archivedAt: filters.includeArchived ? undefined : null,
    region: filters.region,
    fuel: filters.fuel,
    customerId: filters.customerId,
    OR: filters.q
      ? [
          { name: { contains: filters.q, mode: "insensitive" } },
          { units: { some: { duid: { contains: filters.q.toUpperCase() } } } },
        ]
      : undefined,
  };
  const [total, rows] = await db.$transaction([
    db.site.count({ where }),
    db.site.findMany({
      where,
      orderBy: { name: "asc" },
      skip: (page - 1) * SITE_PAGE_SIZE,
      take: SITE_PAGE_SIZE,
      include: {
        customer: { select: { id: true, name: true, isSynthetic: true } },
        units: { select: { duid: true }, orderBy: { duid: "asc" } },
      },
    }),
  ]);
  return { total, page, pageSize: SITE_PAGE_SIZE, rows };
}

export async function getSite(db: Db, actor: Actor, id: string) {
  assertCan(actor, "site:read");
  const site = await db.site.findUnique({
    where: { id },
    include: {
      customer: { select: { id: true, name: true, isSynthetic: true, archivedAt: true } },
      units: { orderBy: { duid: "asc" }, include: { registry: true } },
    },
  });
  if (!site) throw new NotFoundError("Site", id);
  return site;
}

/** Registry DUIDs not yet assigned to any site, for the "add unit" picker. */
export async function listUnassignedRegistryUnits(db: Db, actor: Actor) {
  assertCan(actor, "site:read");
  return db.registryUnit.findMany({
    where: { unit: null },
    orderBy: { duid: "asc" },
    select: { duid: true, stationName: true, region: true, fuel: true },
  });
}

async function requireActiveCustomer(tx: Prisma.TransactionClient, customerId: string) {
  const customer = await tx.customer.findUnique({ where: { id: customerId } });
  if (!customer) throw new RuleError("The selected customer does not exist.");
  if (customer.archivedAt) throw new RuleError("The selected customer is archived.");
}

const STALE = "This site was changed by someone else. Reload the page and try again.";

export async function createSite(db: Db, actor: Actor, input: SiteInput) {
  assertCan(actor, "site:write");
  return db.$transaction(async (tx) => {
    await requireActiveCustomer(tx, input.customerId);
    const site = await tx.site.create({ data: input });
    await writeAudit(tx, actor, "create", "site", site.id, null, site);
    return site;
  });
}

export async function updateSite(db: Db, actor: Actor, id: string, version: number, input: SiteInput) {
  assertCan(actor, "site:write");
  return db.$transaction(async (tx) => {
    const before = await tx.site.findUnique({ where: { id } });
    if (!before) throw new NotFoundError("Site", id);
    if (before.archivedAt) throw new RuleError("Archived sites are read-only. Restore the site first.");
    if (input.customerId !== before.customerId) await requireActiveCustomer(tx, input.customerId);
    const units = await tx.unit.findMany({ where: { siteId: id }, include: { registry: true } });
    const mismatch = units.find((unit) => unit.registry.region !== input.region);
    if (mismatch) {
      throw new RuleError(
        `Unit ${mismatch.duid} is registered in ${mismatch.registry.region}; a site holding it cannot move to ${input.region}.`,
      );
    }
    // Same rule as assignUnit: a site's fuel must match every unit it holds.
    const fuelMismatch = units.find((unit) => unit.registry.fuel !== input.fuel);
    if (fuelMismatch) {
      throw new RuleError(
        `Unit ${fuelMismatch.duid} is a ${fuelMismatch.registry.fuel.toLowerCase()} unit; a site holding it cannot change to ${input.fuel.toLowerCase()}.`,
      );
    }
    const { count } = await tx.site.updateMany({ where: { id, version }, data: { ...input, version: { increment: 1 } } });
    if (count === 0) throw new ConflictError(STALE);
    const after = await tx.site.findUniqueOrThrow({ where: { id } });
    await writeAudit(tx, actor, "update", "site", id, before, after);
    return after;
  });
}

export async function setSiteArchived(db: Db, actor: Actor, id: string, version: number, archived: boolean) {
  assertCan(actor, "site:write");
  return db.$transaction(async (tx) => {
    const before = await tx.site.findUnique({ where: { id } });
    if (!before) throw new NotFoundError("Site", id);
    if (!archived) await requireActiveCustomer(tx, before.customerId);
    const { count } = await tx.site.updateMany({
      where: { id, version },
      data: { archivedAt: archived ? new Date() : null, version: { increment: 1 } },
    });
    if (count === 0) throw new ConflictError(STALE);
    const after = await tx.site.findUniqueOrThrow({ where: { id } });
    await writeAudit(tx, actor, archived ? "archive" : "restore", "site", id, before, after);
    return after;
  });
}

/** Attach an AEMO-registered DUID to a site. The DUID must match the site's region and fuel. */
export async function assignUnit(db: Db, actor: Actor, siteId: string, duid: string) {
  assertCan(actor, "site:write");
  try {
    return await db.$transaction(async (tx) => {
      const site = await tx.site.findUnique({ where: { id: siteId } });
      if (!site) throw new NotFoundError("Site", siteId);
      if (site.archivedAt) throw new RuleError("Archived sites are read-only. Restore the site first.");
      const registry = await tx.registryUnit.findUnique({ where: { duid } });
      if (!registry) throw new RuleError(`${duid} is not in the AEMO registry loaded into this console.`);
      if (registry.region !== site.region) {
        throw new RuleError(`${duid} is registered in ${registry.region}, but this site is in ${site.region}.`);
      }
      if (registry.fuel !== site.fuel) {
        throw new RuleError(`${duid} is a ${registry.fuel.toLowerCase()} unit, but this site is ${site.fuel.toLowerCase()}.`);
      }
      const unit = await tx.unit.create({ data: { duid, siteId } });
      await writeAudit(tx, actor, "assign_unit", "unit", duid, null, { duid, siteId });
      return unit;
    });
  } catch (error) {
    if (prismaCode(error) === UNIQUE_VIOLATION) {
      const owner = await db.unit.findUnique({ where: { duid }, include: { site: { select: { name: true } } } });
      throw new ConflictError(`${duid} is already assigned to ${owner?.site.name ?? "another site"}.`);
    }
    if (prismaCode(error) === FOREIGN_KEY_VIOLATION) {
      throw new RuleError(`${duid} is not in the AEMO registry loaded into this console.`);
    }
    throw error;
  }
}

export async function removeUnit(db: Db, actor: Actor, duid: string) {
  assertCan(actor, "site:write");
  return db.$transaction(async (tx) => {
    const unit = await tx.unit.findUnique({ where: { duid }, include: { site: true } });
    if (!unit) throw new NotFoundError("Unit", duid);
    if (unit.site.archivedAt) throw new RuleError("Archived sites are read-only. Restore the site first.");
    await tx.unit.delete({ where: { duid } });
    await writeAudit(tx, actor, "remove_unit", "unit", duid, { duid, siteId: unit.siteId }, null);
    return unit;
  });
}
