import type { Metadata } from "next";
import Link from "next/link";
import { Badge, Forbidden, PageHeader, Pagination, Select, Table, Td, Th } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { formatStaffTime } from "@/lib/format";
import { can } from "@/lib/rbac";
import { enumParam, pageParam, param, withParams } from "@/lib/search-params";
import { listAudit } from "@/lib/services/audit";

export const metadata: Metadata = { title: "Audit log" };

const ENTITIES = ["customer", "site", "unit"] as const;

/** Fields whose value changed between the before and after snapshots. */
function changedFields(before: unknown, after: unknown): string[] {
  const a = (before ?? {}) as Record<string, unknown>;
  const b = (after ?? {}) as Record<string, unknown>;
  const ignore = new Set(["updatedAt", "version", "createdAt", "id"]);
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].filter(
    (key) => !ignore.has(key) && JSON.stringify(a[key]) !== JSON.stringify(b[key]),
  );
}

function entityHref(type: string, id: string): string | null {
  if (type === "customer") return `/customers/${id}`;
  if (type === "site") return `/sites/${id}`;
  return null;
}

export default async function AuditPage({ searchParams }: PageProps<"/audit">) {
  const actor = await requireActor();
  if (!can(actor.role, "audit:read")) return <Forbidden what="the audit log" />;
  const sp = await searchParams;
  const entityType = enumParam(sp, "entity", ENTITIES);
  const entityId = param(sp, "id");
  const result = await listAudit(db, actor, { entityType, entityId, page: pageParam(sp) });

  return (
    <>
      <PageHeader title="Audit log" subtitle="Every change made in the console, written in the same database transaction as the change." />
      <form className="mb-4 flex items-end gap-2">
        <Select name="entity" defaultValue={entityType ?? ""} aria-label="Entity type">
          <option value="">All entities</option>
          {ENTITIES.map((entity) => (
            <option key={entity}>{entity}</option>
          ))}
        </Select>
        <button type="submit" className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100">
          Filter
        </button>
      </form>
      <Table>
        <thead className="bg-slate-50">
          <tr>
            <Th>When (Melbourne)</Th>
            <Th>Who</Th>
            <Th>Action</Th>
            <Th>Entity</Th>
            <Th>Changed</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {result.rows.map((row) => {
            const href = entityHref(row.entityType, row.entityId);
            const fields = changedFields(row.before, row.after);
            return (
              <tr key={row.id}>
                <Td className="whitespace-nowrap">{formatStaffTime(row.at)}</Td>
                <Td>{row.actorEmail}</Td>
                <Td>
                  <Badge tone={row.action === "archive" || row.action === "remove_unit" ? "red" : row.action === "create" ? "green" : "blue"}>
                    {row.action.replace("_", " ")}
                  </Badge>
                </Td>
                <Td>
                  {row.entityType}{" "}
                  {href ? (
                    <Link href={href} className="font-mono text-xs underline">
                      {row.entityId}
                    </Link>
                  ) : (
                    <span className="font-mono text-xs">{row.entityId}</span>
                  )}
                </Td>
                <Td className="text-xs text-slate-600">{fields.join(", ") || "—"}</Td>
              </tr>
            );
          })}
          {result.rows.length === 0 && (
            <tr>
              <Td className="py-6 text-center text-slate-500">No audit entries yet.</Td>
            </tr>
          )}
        </tbody>
      </Table>
      <Pagination
        page={result.page}
        pageSize={result.pageSize}
        total={result.total}
        href={(page) => withParams("/audit", { entity: entityType, id: entityId, page })}
      />
    </>
  );
}
