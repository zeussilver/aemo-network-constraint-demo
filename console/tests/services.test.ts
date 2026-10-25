import { afterAll, beforeEach, describe, expect, it } from "vitest";
import { ForbiddenError } from "@/lib/rbac";
import { listAudit } from "@/lib/services/audit";
import { createCustomer, listCustomers, setCustomerArchived, updateCustomer } from "@/lib/services/customers";
import { assignUnit, createSite, removeUnit, setSiteArchived, updateSite } from "@/lib/services/sites";
import { ConflictError, RuleError } from "@/lib/services/types";
import { admin, customerFixture, hasTestDb, nobody, resetDb, siteFixture, testDb, viewer } from "./db-helpers";

describe.skipIf(!hasTestDb)("console services (Postgres)", () => {
  const db = testDb();
  const auditCount = () => db.auditLog.count();

  beforeEach(() => resetDb(db));
  afterAll(() => db.$disconnect());

  describe("RBAC", () => {
    it("rejects a viewer's write on the server and leaves no trace", async () => {
      await expect(createCustomer(db, viewer, customerFixture)).rejects.toBeInstanceOf(ForbiddenError);
      expect(await db.customer.count()).toBe(0);
      expect(await auditCount()).toBe(0);
    });

    it("lets a viewer read but not open the audit log", async () => {
      await createCustomer(db, admin, customerFixture);
      expect((await listCustomers(db, viewer)).total).toBe(1);
      await expect(listAudit(db, viewer, {})).rejects.toBeInstanceOf(ForbiddenError);
    });

    it("gives an unknown role no access at all", async () => {
      await expect(listCustomers(db, nobody)).rejects.toBeInstanceOf(ForbiddenError);
    });
  });

  describe("customers", () => {
    it("writes exactly one audit row per successful change", async () => {
      const customer = await createCustomer(db, admin, customerFixture);
      expect(await auditCount()).toBe(1);
      await updateCustomer(db, admin, customer.id, 1, { ...customerFixture, status: "INACTIVE" });
      expect(await auditCount()).toBe(2);

      const [latest] = (await listAudit(db, admin, { entityId: customer.id })).rows;
      expect(latest).toMatchObject({ action: "update", actorEmail: admin.email, entityType: "customer" });
      expect(latest.before).toMatchObject({ status: "ACTIVE", version: 1 });
      expect(latest.after).toMatchObject({ status: "INACTIVE", version: 2 });
    });

    it("rejects an update from a stale form (optimistic concurrency)", async () => {
      const customer = await createCustomer(db, admin, customerFixture);
      await updateCustomer(db, admin, customer.id, 1, { ...customerFixture, notes: "first editor" });
      await expect(
        updateCustomer(db, admin, customer.id, 1, { ...customerFixture, notes: "second editor" }),
      ).rejects.toBeInstanceOf(ConflictError);
      const stored = await db.customer.findUniqueOrThrow({ where: { id: customer.id } });
      expect(stored.notes).toBe("first editor");
      expect(await auditCount()).toBe(2);
    });

    it("reports a duplicate name as a conflict", async () => {
      await createCustomer(db, admin, customerFixture);
      await expect(createCustomer(db, admin, customerFixture)).rejects.toBeInstanceOf(ConflictError);
    });

    it("refuses to archive a customer with active sites, then allows it once they are archived", async () => {
      const customer = await createCustomer(db, admin, customerFixture);
      const site = await createSite(db, admin, siteFixture(customer.id));
      await expect(setCustomerArchived(db, admin, customer.id, 1, true)).rejects.toBeInstanceOf(RuleError);

      await setSiteArchived(db, admin, site.id, 1, true);
      const archived = await setCustomerArchived(db, admin, customer.id, 1, true);
      expect(archived.archivedAt).not.toBeNull();
      await expect(updateCustomer(db, admin, customer.id, 2, customerFixture)).rejects.toBeInstanceOf(RuleError);
      expect((await listCustomers(db, admin)).total).toBe(0);
      expect((await listCustomers(db, admin, { includeArchived: true })).total).toBe(1);
    });
  });

  describe("sites and units", () => {
    async function windSite() {
      const customer = await createCustomer(db, admin, customerFixture);
      return createSite(db, admin, siteFixture(customer.id));
    }

    it("assigns a registered DUID and audits it", async () => {
      const site = await windSite();
      await assignUnit(db, admin, site.id, "CATHROCK");
      expect(await db.unit.findUnique({ where: { duid: "CATHROCK" } })).toMatchObject({ siteId: site.id });
      const [row] = (await listAudit(db, admin, { entityType: "unit" })).rows;
      expect(row).toMatchObject({ action: "assign_unit", entityId: "CATHROCK" });
    });

    it.each([
      ["WOOLGSF1", /QLD1/],
      ["GOONSF1", /NSW1/],
      ["NOPE1", /not in the AEMO registry/],
    ])("rejects %s for an SA1 wind site", async (duid, message) => {
      const site = await windSite();
      await expect(assignUnit(db, admin, site.id, duid)).rejects.toThrow(message);
      expect(await db.unit.count()).toBe(0);
    });

    it("rejects a DUID already held by another site", async () => {
      const site = await windSite();
      const other = await createSite(db, admin, { ...siteFixture(site.customerId), name: "Second site" });
      await assignUnit(db, admin, site.id, "GSWF1A");
      await expect(assignUnit(db, admin, other.id, "GSWF1A")).rejects.toThrow(/already assigned to Test Wind Site/);
    });

    it("blocks moving a site to a region its units are not registered in", async () => {
      const site = await windSite();
      await assignUnit(db, admin, site.id, "CATHROCK");
      await expect(
        updateSite(db, admin, site.id, 1, { ...siteFixture(site.customerId), region: "VIC1" }),
      ).rejects.toThrow(/registered in SA1/);
    });

    it("blocks changing a site's fuel away from its units' fuel, and leaves no trace", async () => {
      const site = await windSite();
      await assignUnit(db, admin, site.id, "CATHROCK");
      const audits = await auditCount();
      await expect(
        updateSite(db, admin, site.id, 1, { ...siteFixture(site.customerId), fuel: "SOLAR" }),
      ).rejects.toThrow(/CATHROCK is a wind unit/);
      expect(await db.site.findUniqueOrThrow({ where: { id: site.id } })).toMatchObject({ fuel: "WIND", version: 1 });
      expect(await auditCount()).toBe(audits);
    });

    it("removes a unit and frees the DUID", async () => {
      const site = await windSite();
      await assignUnit(db, admin, site.id, "CATHROCK");
      await removeUnit(db, admin, "CATHROCK");
      expect(await db.unit.count()).toBe(0);
      const [row] = (await listAudit(db, admin, { entityType: "unit" })).rows;
      expect(row).toMatchObject({ action: "remove_unit", before: { duid: "CATHROCK", siteId: site.id } });
    });

    it("refuses a viewer's unit assignment", async () => {
      const site = await windSite();
      await expect(assignUnit(db, viewer, site.id, "CATHROCK")).rejects.toBeInstanceOf(ForbiddenError);
    });
  });
});
