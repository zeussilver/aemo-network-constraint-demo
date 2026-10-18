import type { Metadata } from "next";
import Link from "next/link";
import { Badge, Forbidden, Input, LinkButton, PageHeader, Pagination, Select, SyntheticBadge, Table, Td, Th } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { titleCase } from "@/lib/format";
import { can } from "@/lib/rbac";
import { enumParam, flagParam, pageParam, param, withParams } from "@/lib/search-params";
import { listCustomers } from "@/lib/services/customers";

export const metadata: Metadata = { title: "Customers" };

const STATUSES = ["ACTIVE", "ONBOARDING", "INACTIVE"] as const;
const statusTone = { ACTIVE: "green", ONBOARDING: "blue", INACTIVE: "slate" } as const;

export default async function CustomersPage({ searchParams }: PageProps<"/customers">) {
  const actor = await requireActor();
  if (!can(actor.role, "customer:read")) return <Forbidden what="customers" />;
  const sp = await searchParams;
  const filters = {
    q: param(sp, "q"),
    status: enumParam(sp, "status", STATUSES),
    includeArchived: flagParam(sp, "archived"),
  };
  const result = await listCustomers(db, actor, { ...filters, page: pageParam(sp) });

  return (
    <>
      <PageHeader
        title="Customers"
        subtitle="All customers in this demo are synthetic. The sites assigned to them are real AEMO-registered stations."
        actions={can(actor.role, "customer:write") && <LinkButton href="/customers/new" tone="primary">New customer</LinkButton>}
      />
      <form className="mb-4 flex flex-wrap items-end gap-2" role="search">
        <Input name="q" placeholder="Search by name" defaultValue={filters.q} aria-label="Search customers" />
        <Select name="status" defaultValue={filters.status ?? ""} aria-label="Status">
          <option value="">Any status</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {titleCase(status)}
            </option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5 text-sm text-slate-700">
          <input type="checkbox" name="archived" value="1" defaultChecked={filters.includeArchived} /> Include archived
        </label>
        <button type="submit" className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100">
          Filter
        </button>
      </form>
      <Table>
        <thead className="bg-slate-50">
          <tr>
            <Th>Name</Th>
            <Th>Status</Th>
            <Th>Contact</Th>
            <Th className="text-right">Active sites</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {result.rows.map((customer) => (
            <tr key={customer.id} className={customer.archivedAt ? "bg-slate-50 text-slate-500" : undefined}>
              <Td>
                <Link href={`/customers/${customer.id}`} className="font-medium text-slate-900 hover:underline">
                  {customer.name}
                </Link>{" "}
                {customer.isSynthetic && <SyntheticBadge />} {customer.archivedAt && <Badge>Archived</Badge>}
              </Td>
              <Td>
                <Badge tone={statusTone[customer.status]}>{titleCase(customer.status)}</Badge>
              </Td>
              <Td>{customer.contactEmail}</Td>
              <Td className="text-right tabular-nums">{customer._count.sites}</Td>
            </tr>
          ))}
          {result.rows.length === 0 && (
            <tr>
              <Td className="py-6 text-center text-slate-500">No customers match these filters.</Td>
            </tr>
          )}
        </tbody>
      </Table>
      <Pagination
        page={result.page}
        pageSize={result.pageSize}
        total={result.total}
        href={(page) => withParams("/customers", { q: filters.q, status: filters.status, archived: filters.includeArchived, page })}
      />
    </>
  );
}
