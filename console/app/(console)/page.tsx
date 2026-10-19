import type { Metadata } from "next";
import Link from "next/link";
import { FreshnessStrip } from "@/components/freshness";
import { riskTone } from "@/components/risk-card";
import { Badge, Forbidden, PageHeader, SyntheticBadge, Table, Td, Th } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { formatMw, formatNemTime, titleCase } from "@/lib/format";
import { can } from "@/lib/rbac";
import { getFleetOverview } from "@/lib/services/ops";

export const metadata: Metadata = { title: "Overview" };

export default async function OverviewPage() {
  const actor = await requireActor();
  if (!can(actor.role, "ops:read")) return <Forbidden what="operational data" />;
  const fleet = await getFleetOverview(db, actor);
  const capacity = fleet.sites.reduce((sum, site) => sum + site.capacityMw, 0);
  const output = fleet.sites.reduce((sum, site) => sum + (site.latestMw ?? 0), 0);

  return (
    <>
      <PageHeader
        title="Fleet overview"
        subtitle={`${fleet.sites.length} active sites · ${capacity.toFixed(0)} MW registered · ${output.toFixed(0)} MW at the latest SCADA reading`}
      />
      <FreshnessStrip sources={fleet.freshness} />
      <Table>
        <thead className="bg-slate-50">
          <tr>
            <Th>Site</Th>
            <Th>Customer</Th>
            <Th>Region · fuel</Th>
            <Th className="text-right">Latest actual</Th>
            <Th className="text-right">Curtailment est. {fleet.day && <span className="normal-case">({fleet.day})</span>}</Th>
            <Th className="text-right">Self-forecast used</Th>
            <Th>Constraint risk</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {fleet.sites.map((site) => (
            <tr key={site.id}>
              <Td>
                <Link href={`/sites/${site.id}`} className="font-medium hover:underline">
                  {site.name}
                </Link>
                <span className="block font-mono text-xs text-slate-500">{site.duids.join(", ") || "no units"}</span>
              </Td>
              <Td>
                {site.customer.name} {site.customer.isSynthetic && <SyntheticBadge />}
              </Td>
              <Td>
                {site.region} · {titleCase(site.fuel)}
              </Td>
              <Td className="text-right tabular-nums">
                {formatMw(site.latestMw)}
                <span className="block text-xs text-slate-500">
                  {site.latestMw != null ? `${((site.latestMw / site.capacityMw) * 100).toFixed(0)}% of ${site.capacityMw.toFixed(0)} MW` : ""}
                </span>
              </Td>
              <Td className="text-right tabular-nums">
                {site.curtailment.intervals ? `${site.curtailment.curtailedMwh.toFixed(1)} MWh` : "—"}
                <span className="block text-xs text-slate-500">
                  {site.curtailment.intervals ? `${site.curtailment.cappedIntervals} capped intervals` : ""}
                </span>
              </Td>
              <Td className="text-right tabular-nums">{site.selfForecastShare == null ? "—" : `${(site.selfForecastShare * 100).toFixed(0)}%`}</Td>
              <Td>
                {site.snapshot ? (
                  <span title={`As of ${formatNemTime(site.snapshot.as_of)}`}>
                    <Badge tone={riskTone(site.snapshot.risk_level)}>
                      {(site.snapshot.probability * 100).toFixed(0)}% · {site.snapshot.risk_level.replace("_", " ")}
                    </Badge>
                  </span>
                ) : (
                  <span className="text-xs text-slate-500">no model</span>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <p className="mt-3 text-xs text-slate-500">
        Latest actual: DISPATCH_UNIT_SCADA, loaded every 30 minutes. Curtailment estimate and self-forecast share: the most recent trading day in Next Day
        Dispatch / Next_Day_Intermittent_DS, which AEMO publishes once a day. Customers are synthetic.
      </p>
    </>
  );
}
