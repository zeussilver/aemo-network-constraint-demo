import type { CustomerStatus, Prisma } from "../generated/prisma/client";
import type { CustomerInput } from "../validation";
import { assertCan, writeAudit } from "./audit";
import { prismaCode, UNIQUE_VIOLATION } from "./prisma-errors";
import { ConflictError, NotFoundError, RuleError, type Actor, type Db } from "./types";

export const CUSTOMER_PAGE_SIZE = 20;

export type CustomerFilters = {
  q?: string;
  status?: CustomerStatus;
  includeArchived?: boolean;
  page?: number;
};

export async function listCustomers(db: Db, actor: Actor, filters: CustomerFilters = {}) {
  assertCan(actor, "customer:read");
  const page = Math.max(1, filters.page ?? 1);
  const where: Prisma.CustomerWhereInput = {
    archivedAt: filters.includeArchived ? undefined : null,
    status: filters.status,
    name: filters.q ? { contains: filters.q, mode: "insensitive" } : undefined,
  };
  const [total, rows] = await db.$transaction([
    db.customer.count({ where }),
    db.customer.findMany({
      where,
      orderBy: { name: "asc" },
      skip: (page - 1) * CUSTOMER_PAGE_SIZE,
      take: CUSTOMER_PAGE_SIZE,
      include: { _count: { select: { sites: { where: { archivedAt: null } } } } },
    }),
  ]);
  return { total, page, pageSize: CUSTOMER_PAGE_SIZE, rows };
}

export async function getCustomer(db: Db, actor: Actor, id: string) {
  assertCan(actor, "customer:read");
  const customer = await db.customer.findUnique({
    where: { id },
    include: {
      sites: { orderBy: { name: "asc" }, include: { units: { select: { duid: true } } } },
    },
  });
  if (!customer) throw new NotFoundError("Customer", id);
  return customer;
}

/** Active customers for the site form's customer picker. */
export async function listCustomerOptions(db: Db, actor: Actor) {
  assertCan(actor, "customer:read");
  return db.customer.findMany({
    where: { archivedAt: null },
    orderBy: { name: "asc" },
    select: { id: true, name: true },
  });
}

function conflictOnDuplicateName(error: unknown, name: string): never {
  if (prismaCode(error) === UNIQUE_VIOLATION) {
    throw new ConflictError(`A customer named "${name}" already exists.`);
  }
  throw error;
}

export async function createCustomer(db: Db, actor: Actor, input: CustomerInput) {
  assertCan(actor, "customer:write");
  try {
    return await db.$transaction(async (tx) => {
      const customer = await tx.customer.create({ data: { ...input, isSynthetic: true } });
      await writeAudit(tx, actor, "create", "customer", customer.id, null, customer);
      return customer;
    });
  } catch (error) {
    conflictOnDuplicateName(error, input.name);
  }
}

const STALE = "This customer was changed by someone else. Reload the page and try again.";

export async function updateCustomer(db: Db, actor: Actor, id: string, version: number, input: CustomerInput) {
  assertCan(actor, "customer:write");
  try {
    return await db.$transaction(async (tx) => {
      const before = await tx.customer.findUnique({ where: { id } });
      if (!before) throw new NotFoundError("Customer", id);
      if (before.archivedAt) throw new RuleError("Archived customers are read-only. Restore the customer first.");
      const { count } = await tx.customer.updateMany({
        where: { id, version },
        data: { ...input, version: { increment: 1 } },
      });
      if (count === 0) throw new ConflictError(STALE);
      const after = await tx.customer.findUniqueOrThrow({ where: { id } });
      await writeAudit(tx, actor, "update", "customer", id, before, after);
      return after;
    });
  } catch (error) {
    conflictOnDuplicateName(error, input.name);
  }
}

export async function setCustomerArchived(db: Db, actor: Actor, id: string, version: number, archived: boolean) {
  assertCan(actor, "customer:write");
  return db.$transaction(async (tx) => {
    const before = await tx.customer.findUnique({ where: { id } });
    if (!before) throw new NotFoundError("Customer", id);
    if (archived) {
      const activeSites = await tx.site.count({ where: { customerId: id, archivedAt: null } });
      if (activeSites > 0) {
        throw new RuleError(
          `This customer still has ${activeSites} active site(s). Archive or reassign them first.`,
        );
      }
    }
    const { count } = await tx.customer.updateMany({
      where: { id, version },
      data: { archivedAt: archived ? new Date() : null, version: { increment: 1 } },
    });
    if (count === 0) throw new ConflictError(STALE);
    const after = await tx.customer.findUniqueOrThrow({ where: { id } });
    await writeAudit(tx, actor, archived ? "archive" : "restore", "customer", id, before, after);
    return after;
  });
}
