/**
 * Role-based access control for the console. Two roles, one permission table, and every
 * server action and page asks `can()` before touching data. The UI uses the same table to
 * hide controls, but hiding is cosmetic: the server check is the one that counts.
 */
export const ROLES = ["admin", "viewer"] as const;
export type Role = (typeof ROLES)[number];

export const PERMISSIONS = {
  "customer:read": ["admin", "viewer"],
  "customer:write": ["admin"],
  "site:read": ["admin", "viewer"],
  "site:write": ["admin"],
  "ops:read": ["admin", "viewer"],
  "audit:read": ["admin"],
} as const satisfies Record<string, readonly Role[]>;

export type Permission = keyof typeof PERMISSIONS;

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLES as readonly string[]).includes(value);
}

/** Unknown or missing roles get no permissions. */
export function can(role: unknown, permission: Permission): boolean {
  return isRole(role) && (PERMISSIONS[permission] as readonly Role[]).includes(role);
}

export class ForbiddenError extends Error {
  constructor(permission: Permission) {
    super(`Forbidden: missing permission ${permission}`);
    this.name = "ForbiddenError";
  }
}
