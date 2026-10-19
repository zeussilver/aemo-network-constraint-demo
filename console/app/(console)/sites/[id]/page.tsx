import type { Metadata } from "next";
import Link from "next/link";
import { ArchiveButton } from "@/components/archive-button";
import { Badge, Card, DefinitionList, Forbidden, LinkButton, Notice, PageHeader, SyntheticBadge, Table, Td, Th } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { formatStaffTime, titleCase } from "@/lib/format";
import { notFoundOnMissing } from "@/lib/page-guards";
import { can } from "@/lib/rbac";
import { param } from "@/lib/search-params";
import { getSiteOps } from "@/lib/services/ops";
import { getSite, listUnassignedRegistryUnits } from "@/lib/services/sites";
import { setSiteArchivedAction } from "../actions";
import { AssignUnitForm, RemoveUnitButton } from "../unit-controls";
import { OpsSection } from "./ops-section";

export const metadata: Metadata = { title: "Site" };

export default async function SitePage({ params, searchParams }: PageProps<"/sites/[id]">) {
  const actor = await requireActor();
  if (!can(actor.role, "site:read")) return <Forbidden what="sites" />;
  const { id } = await params;
  const site = await getSite(db, actor, id).catch(notFoundOnMissing);
  const ops = can(actor.role, "ops:read") ? await getSiteOps(db, actor, id, param(await searchParams, "day")) : null;
  const canWrite = can(actor.role, "site:write") && !site.archivedAt;
  const unassigned = canWrite
    ? (await listUnassignedRegistryUnits(db, actor)).filter((unit) => unit.region === site.region && unit.fuel === site.fuel)
    : [];

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {site.name} {site.archivedAt && <Badge>Archived</Badge>}
          </span>
        }
        subtitle={
          <>
            {site.region} · {titleCase(site.fuel)} · {site.registeredCapacityMw.toFixed(1)} MW registered · customer{" "}
            <Link href={`/customers/${site.customer.id}`} className="underline">
              {site.customer.name}
            </Link>{" "}
            {site.customer.isSynthetic && <SyntheticBadge />}
          </>
        }
        actions={
          can(actor.role, "site:write") && (
            <>
              {!site.archivedAt && <LinkButton href={`/sites/${site.id}/edit`}>Edit</LinkButton>}
              <ArchiveButton action={setSiteArchivedAction} id={site.id} version={site.version} archived={!!site.archivedAt} noun="site" />
            </>
          )
        }
      />
      {site.archivedAt && (
        <div className="mb-4">
          <Notice tone="amber">Archived {formatStaffTime(site.archivedAt)}. Archived sites are read-only.</Notice>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Site record">
          <DefinitionList
            items={[
              ["Status", titleCase(site.status)],
              ["Region", site.region],
              ["Fuel", titleCase(site.fuel)],
              ["Registered capacity", `${site.registeredCapacityMw.toFixed(1)} MW`],
              ["Last updated", formatStaffTime(site.updatedAt)],
              ["Record version", site.version],
            ]}
          />
        </Card>
        <Card title="Units (DUIDs)" className="lg:col-span-2">
          <Table>
            <thead className="bg-slate-50">
              <tr>
                <Th>DUID</Th>
                <Th>AEMO station</Th>
                <Th>Technology</Th>
                <Th>Classification</Th>
                <Th className="text-right">Reg / max MW</Th>
                <Th>Connection point</Th>
                {canWrite && <Th />}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {site.units.map(({ duid, registry }) => (
                <tr key={duid}>
                  <Td className="font-mono">{duid}</Td>
                  <Td>{registry.stationName}</Td>
                  <Td>{registry.technology}</Td>
                  <Td>
                    {registry.classification}
                    <span className="block text-xs text-slate-500">{registry.scheduleType.toLowerCase()} in MMS</span>
                  </Td>
                  <Td className="text-right tabular-nums">
                    {registry.registeredCapacityMw} / {registry.maxCapacityMw}
                  </Td>
                  <Td className="font-mono text-xs">{registry.connectionPointId}</Td>
                  {canWrite && (
                    <Td className="text-right">
                      <RemoveUnitButton duid={duid} />
                    </Td>
                  )}
                </tr>
              ))}
              {site.units.length === 0 && (
                <tr>
                  <Td className="py-6 text-center text-slate-500">No units assigned.</Td>
                </tr>
              )}
            </tbody>
          </Table>
          {canWrite && (
            <div className="mt-4 max-w-md">
              <AssignUnitForm
                siteId={site.id}
                options={unassigned}
                emptyText={`No unassigned ${site.region} ${site.fuel.toLowerCase()} DUIDs are left in the loaded AEMO registry.`}
              />
            </div>
          )}
          <p className="mt-3 text-xs text-slate-500">
            Registry source: {site.units[0]?.registry.registrationListFile ?? "AEMO NEM Registration and Exemption List"} and MMSDM DUDETAILSUMMARY.
          </p>
        </Card>
      </div>
      {ops && <OpsSection siteId={site.id} ops={ops} capacityMw={site.registeredCapacityMw} />}
    </>
  );
}
