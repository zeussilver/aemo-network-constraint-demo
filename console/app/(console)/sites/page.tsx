import type { Metadata } from "next";
import Link from "next/link";
import { Badge, Forbidden, Input, LinkButton, PageHeader, Pagination, Select, SyntheticBadge, Table, Td, Th } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { titleCase } from "@/lib/format";
import { can } from "@/lib/rbac";
import { enumParam, flagParam, pageParam, param, withParams } from "@/lib/search-params";
import { listCustomerOptions } from "@/lib/services/customers";
import { listSites } from "@/lib/services/sites";

export const metadata: Metadata = { title: "Sites" };

const REGIONS = ["NSW1", "QLD1", "VIC1", "SA1", "TAS1"] as const;
const FUELS = ["SOLAR", "WIND"] as const;

export default async function SitesPage({ searchParams }: PageProps<"/sites">) {
  const actor = await requireActor();
  if (!can(actor.role, "site:read")) return <Forbidden what="sites" />;
  const sp = await searchParams;
  const filters = {
    q: param(sp, "q"),
    region: enumParam(sp, "region", REGIONS),
    fuel: enumParam(sp, "fuel", FUELS),
    customerId: param(sp, "customerId"),
    includeArchived: flagParam(sp, "archived"),
  };
  const [result, customers] = await Promise.all([
    listSites(db, actor, { ...filters, page: pageParam(sp) }),
    listCustomerOptions(db, actor),
  ]);

  return (
    <>
      <PageHeader
        title="Sites"
        subtitle="Real AEMO-registered solar and wind stations. Customer assignments are synthetic."
        actions={can(actor.role, "site:write") && <LinkButton href="/sites/new" tone="primary">New site</LinkButton>}
      />
      <form className="mb-4 flex flex-wrap items-end gap-2" role="search">
        <Input name="q" placeholder="Name or DUID" defaultValue={filters.q} aria-label="Search sites" />
        <Select name="region" defaultValue={filters.region ?? ""} aria-label="Region">
          <option value="">Any region</option>
          {REGIONS.map((region) => (
            <option key={region}>{region}</option>
          ))}
        </Select>
        <Select name="fuel" defaultValue={filters.fuel ?? ""} aria-label="Fuel">
          <option value="">Any fuel</option>
          {FUELS.map((fuel) => (
            <option key={fuel} value={fuel}>
              {titleCase(fuel)}
            </option>
          ))}
        </Select>
        <Select name="customerId" defaultValue={filters.customerId ?? ""} aria-label="Customer">
          <option value="">Any customer</option>
          {customers.map((customer) => (
            <option key={customer.id} value={customer.id}>
              {customer.name}
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
            <Th>Site</Th>
            <Th>Customer</Th>
            <Th>Region</Th>
            <Th>Fuel</Th>
            <Th className="text-right">Registered MW</Th>
            <Th>DUIDs</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {result.rows.map((site) => (
            <tr key={site.id} className={site.archivedAt ? "bg-slate-50 text-slate-500" : undefined}>
              <Td>
                <Link href={`/sites/${site.id}`} className="font-medium text-slate-900 hover:underline">
                  {site.name}
                </Link>{" "}
                {site.archivedAt && <Badge>Archived</Badge>}
              </Td>
              <Td>
                <Link href={`/customers/${site.customer.id}`} className="hover:underline">
                  {site.customer.name}
                </Link>{" "}
                {site.customer.isSynthetic && <SyntheticBadge />}
              </Td>
              <Td>{site.region}</Td>
              <Td>{titleCase(site.fuel)}</Td>
              <Td className="text-right tabular-nums">{site.registeredCapacityMw.toFixed(1)}</Td>
              <Td className="font-mono text-xs">{site.units.map((unit) => unit.duid).join(", ") || "—"}</Td>
            </tr>
          ))}
          {result.rows.length === 0 && (
            <tr>
              <Td className="py-6 text-center text-slate-500">No sites match these filters.</Td>
            </tr>
          )}
        </tbody>
      </Table>
      <Pagination
        page={result.page}
        pageSize={result.pageSize}
        total={result.total}
        href={(page) =>
          withParams("/sites", {
            q: filters.q,
            region: filters.region,
            fuel: filters.fuel,
            customerId: filters.customerId,
            archived: filters.includeArchived,
            page,
          })
        }
      />
    </>
  );
}
