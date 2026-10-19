import type { PrismaClient } from "../generated/prisma/client";
import type { Role } from "../rbac";

/** The signed-in staff member performing an action. `none` = unknown role, no permissions. */
export type Actor = {
  id: string;
  email: string;
  name: string;
  role: Role | "none";
};

export type Db = PrismaClient;

export class NotFoundError extends Error {
  constructor(entity: string, id: string) {
    super(`${entity} ${id} not found`);
    this.name = "NotFoundError";
  }
}

/** Optimistic-concurrency or uniqueness conflict the user can resolve by reloading or renaming. */
export class ConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConflictError";
  }
}

/** A business rule rejected the change (e.g. archiving a customer that still has sites). */
export class RuleError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RuleError";
  }
}
