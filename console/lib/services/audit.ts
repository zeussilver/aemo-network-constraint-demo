import type { Prisma } from "../generated/prisma/client";
import { can, ForbiddenError, type Permission } from "../rbac";
import type { Actor, Db } from "./types";

type Tx = Prisma.TransactionClient;

export type AuditAction = "create" | "update" | "archive" | "restore" | "assign_unit" | "remove_unit";
export type AuditEntity = "customer" | "site" | "unit";

export function assertCan(actor: Actor, permission: Permission): void {
  if (!can(actor.role, permission)) throw new ForbiddenError(permission);
}

/** Dates become ISO strings, so before/after snapshots round-trip through JSON columns. */
function snapshot(value: unknown): Prisma.InputJsonValue | undefined {
  return value == null ? undefined : (JSON.parse(JSON.stringify(value)) as Prisma.InputJsonValue);
}

/** Write one audit row inside the same transaction as the change it describes. */
export async function writeAudit(
  tx: Tx,
  actor: Actor,
  action: AuditAction,
  entityType: AuditEntity,
  entityId: string,
  before: unknown,
  after: unknown,
): Promise<void> {
  await tx.auditLog.create({
    data: {
      actorId: actor.id,
      actorEmail: actor.email,
      action,
      entityType,
      entityId,
      before: snapshot(before),
      after: snapshot(after),
    },
  });
}

export const AUDIT_PAGE_SIZE = 50;

export async function listAudit(
  db: Db,
  actor: Actor,
  filters: { entityType?: AuditEntity; entityId?: string; page?: number },
) {
  assertCan(actor, "audit:read");
  const page = Math.max(1, filters.page ?? 1);
  const where = { entityType: filters.entityType, entityId: filters.entityId };
  const [total, rows] = await db.$transaction([
    db.auditLog.count({ where }),
    db.auditLog.findMany({
      where,
      orderBy: { at: "desc" },
      skip: (page - 1) * AUDIT_PAGE_SIZE,
      take: AUDIT_PAGE_SIZE,
    }),
  ]);
  return { total, page, pageSize: AUDIT_PAGE_SIZE, rows };
}
