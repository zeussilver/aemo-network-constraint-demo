import type { Metadata } from "next";
import Link from "next/link";
import { ArchiveButton } from "@/components/archive-button";
import { Badge, Card, DefinitionList, Forbidden, LinkButton, Notice, PageHeader, SyntheticBadge, Table, Td, Th } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { formatStaffTime, titleCase } from "@/lib/format";
import { notFoundOnMissing } from "@/lib/page-guards";
import { can } from "@/lib/rbac";
import { getCustomer } from "@/lib/services/customers";
import { setCustomerArchivedAction } from "../actions";

export const metadata: Metadata = { title: "Customer" };

export default async function CustomerPage({ params }: PageProps<"/customers/[id]">) {
  const actor = await requireActor();
  if (!can(actor.role, "customer:read")) return <Forbidden what="customers" />;
  const { id } = await params;
  const customer = await getCustomer(db, actor, id).catch(notFoundOnMissing);
  const canWrite = can(actor.role, "customer:write");

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {customer.name} {customer.isSynthetic && <SyntheticBadge />} {customer.archivedAt && <Badge>Archived</Badge>}
          </span>
        }
        actions={
          canWrite && (
            <>
              {!customer.archivedAt && <LinkButton href={`/customers/${customer.id}/edit`}>Edit</LinkButton>}
              <ArchiveButton action={setCustomerArchivedAction} id={customer.id} version={customer.version} archived={!!customer.archivedAt} noun="customer" />
            </>
          )
        }
      />
      {customer.archivedAt && (
        <div className="mb-4">
          <Notice tone="amber">Archived {formatStaffTime(customer.archivedAt)}. Archived customers are read-only.</Notice>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Details" className="lg:col-span-1">
          <DefinitionList
            items={[
              ["Status", titleCase(customer.status)],
              ["Contact", customer.contactEmail],
              ["Notes", customer.notes ?? "—"],
              ["Created", formatStaffTime(customer.createdAt)],
              ["Last updated", formatStaffTime(customer.updatedAt)],
              ["Record version", customer.version],
            ]}
          />
        </Card>
        <div className="lg:col-span-2">
          <Table>
            <thead className="bg-slate-50">
              <tr>
                <Th>Site</Th>
                <Th>Region</Th>
                <Th>Fuel</Th>
                <Th>DUIDs</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {customer.sites.map((site) => (
                <tr key={site.id} className={site.archivedAt ? "text-slate-500" : undefined}>
                  <Td>
                    <Link href={`/sites/${site.id}`} className="font-medium hover:underline">
                      {site.name}
                    </Link>{" "}
                    {site.archivedAt && <Badge>Archived</Badge>}
                  </Td>
                  <Td>{site.region}</Td>
                  <Td>{titleCase(site.fuel)}</Td>
                  <Td className="font-mono text-xs">{site.units.map((unit) => unit.duid).join(", ") || "—"}</Td>
                </tr>
              ))}
              {customer.sites.length === 0 && (
                <tr>
                  <Td className="py-6 text-center text-slate-500">No sites yet.</Td>
                </tr>
              )}
            </tbody>
          </Table>
          {canWrite && !customer.archivedAt && (
            <p className="mt-2 text-sm">
              <Link href={`/sites/new?customerId=${customer.id}`} className="text-slate-700 underline">
                Add a site for this customer
              </Link>
            </p>
          )}
        </div>
      </div>
    </>
  );
}
