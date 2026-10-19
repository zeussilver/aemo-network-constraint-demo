import { describe, expect, it } from "vitest";
import { toActionError } from "@/lib/action-result";
import { can, ForbiddenError, PERMISSIONS } from "@/lib/rbac";
import { ConflictError } from "@/lib/services/types";
import { customerInput, duidInput, siteInput } from "@/lib/validation";

describe("can()", () => {
  it("gives admin every permission", () => {
    for (const permission of Object.keys(PERMISSIONS) as Array<keyof typeof PERMISSIONS>) {
      expect(can("admin", permission)).toBe(true);
    }
  });

  it("gives viewer read access only, and no audit log", () => {
    expect(can("viewer", "site:read")).toBe(true);
    expect(can("viewer", "ops:read")).toBe(true);
    expect(can("viewer", "site:write")).toBe(false);
    expect(can("viewer", "customer:write")).toBe(false);
    expect(can("viewer", "audit:read")).toBe(false);
  });

  it.each([undefined, null, "", "Admin", "root", "none"])("denies unknown role %s", (role) => {
    expect(can(role, "site:read")).toBe(false);
  });
});

describe("validation", () => {
  it("normalises a customer form", () => {
    expect(
      customerInput.parse({ name: "  Acme  ", contactEmail: " ops@example.com ", status: "ACTIVE", notes: "" }),
    ).toEqual({ name: "Acme", contactEmail: "ops@example.com", status: "ACTIVE", notes: null });
  });

  it("rejects a bad email and a too-short name", () => {
    const result = customerInput.safeParse({ name: "A", contactEmail: "nope", status: "ACTIVE", notes: "" });
    expect(result.success).toBe(false);
    const fields = result.success ? {} : result.error.flatten().fieldErrors;
    expect(Object.keys(fields).sort()).toEqual(["contactEmail", "name"]);
  });

  it("coerces capacity and bounds it", () => {
    const base = { name: "Site", customerId: "c1", region: "SA1", fuel: "WIND", status: "OPERATING" };
    expect(siteInput.parse({ ...base, registeredCapacityMw: "66.5" }).registeredCapacityMw).toBe(66.5);
    expect(siteInput.safeParse({ ...base, registeredCapacityMw: "0" }).success).toBe(false);
    expect(siteInput.safeParse({ ...base, registeredCapacityMw: "abc" }).success).toBe(false);
    expect(siteInput.safeParse({ ...base, region: "WA1", registeredCapacityMw: "10" }).success).toBe(false);
  });

  it("upper-cases DUIDs and rejects odd characters", () => {
    expect(duidInput.parse(" cathrock ")).toBe("CATHROCK");
    expect(duidInput.safeParse("CATH ROCK").success).toBe(false);
    expect(duidInput.safeParse("X").success).toBe(false);
  });
});

describe("toActionError", () => {
  it("maps known errors to messages and hides unknown ones", () => {
    expect(toActionError(new ForbiddenError("site:write"))).toMatchObject({ ok: false, error: /role does not allow/ });
    expect(toActionError(new ConflictError("Stale"))).toMatchObject({ ok: false, error: "Stale" });
    const original = console.error;
    console.error = () => {};
    try {
      expect(toActionError(new Error("db password leaked"))).toEqual({
        ok: false,
        error: "Something went wrong. The change was not saved.",
        values: undefined,
      });
    } finally {
      console.error = original;
    }
  });
});
