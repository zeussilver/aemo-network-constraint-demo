import type { Metadata } from "next";
import { FreshnessStrip, formatAge, SOURCE_LABELS } from "@/components/freshness";
import { Badge, Card, Forbidden, PageHeader, Table, Td, Th } from "@/components/ui";
import { requireActor } from "@/lib/dal";
import { db } from "@/lib/db";
import { formatNemTime, formatStaffTime } from "@/lib/format";
import { can } from "@/lib/rbac";
import { getFreshness, listIngestRuns, tableRowCounts } from "@/lib/services/ops";

export const metadata: Metadata = { title: "Data status" };

function formatBytes(bytes: bigint): string {
  const value = Number(bytes);
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)} MB`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)} KB`;
  return `${value} B`;
}

export default async function StatusPage() {
  const actor = await requireActor();
  if (!can(actor.role, "ops:read")) return <Forbidden what="data status" />;
  const [freshness, runs, counts] = await Promise.all([getFreshness(db), listIngestRuns(db, actor), tableRowCounts(db, actor)]);

  return (
    <>
      <PageHeader
        title="Data status"
        subtitle="Freshness of each NEMWeb source and the ingest job's recent runs. /api/ready reports the same checks to the uptime monitor."
      />
      <FreshnessStrip sources={freshness} />
      <div className="flex flex-col gap-4">
        <Card title="Rows loaded">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm lg:grid-cols-10">
            <dt className="text-slate-600">SCADA readings</dt>
            <dd className="text-right tabular-nums">{counts.scada.toLocaleString("en-AU")}</dd>
            <dt className="text-slate-600">Dispatch solutions</dt>
            <dd className="text-right tabular-nums">{counts.dispatch.toLocaleString("en-AU")}</dd>
            <dt className="text-slate-600">Forecast-used intervals</dt>
            <dd className="text-right tabular-nums">{counts.used.toLocaleString("en-AU")}</dd>
            <dt className="text-slate-600">Forecast origin rows</dt>
            <dd className="text-right tabular-nums">{counts.origins.toLocaleString("en-AU")}</dd>
            <dt className="text-slate-600">Risk snapshots</dt>
            <dd className="text-right tabular-nums">{counts.snapshots.toLocaleString("en-AU")}</dd>
          </dl>
        </Card>
        <div>
          <Table>
            <thead className="bg-slate-50">
              <tr>
                <Th>Started (Melbourne)</Th>
                <Th>Source</Th>
                <Th>Trigger</Th>
                <Th>Status</Th>
                <Th className="text-right">Files</Th>
                <Th className="text-right">Rows</Th>
                <Th className="text-right">Downloaded</Th>
                <Th className="text-right">Duration</Th>
                <Th>Newest interval</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {runs.map((run) => (
                <tr key={run.id}>
                  <Td className="whitespace-nowrap">{formatStaffTime(run.startedAt)}</Td>
                  <Td>{SOURCE_LABELS[run.source] ?? run.source}</Td>
                  <Td>{run.trigger}</Td>
                  <Td>
                    <Badge tone={run.status === "success" ? "green" : run.status === "failed" ? "red" : "amber"}>
                      {run.status === "failed" ? "▲ failed" : run.status}
                    </Badge>
                    {run.error && <p className="mt-1 max-w-xs truncate text-xs text-red-700" title={run.error}>{run.error}</p>}
                  </Td>
                  <Td className="text-right tabular-nums">{run.filesProcessed}</Td>
                  <Td className="text-right tabular-nums">{run.rowsWritten.toLocaleString("en-AU")}</Td>
                  <Td className="text-right tabular-nums">{formatBytes(run.bytesDownloaded)}</Td>
                  <Td className="text-right tabular-nums">
                    {run.finishedAt ? formatAge((run.finishedAt.getTime() - run.startedAt.getTime()) / 3_600_000) : "running"}
                  </Td>
                  <Td className="whitespace-nowrap">{formatNemTime(run.latestInterval)}</Td>
                </tr>
              ))}
              {runs.length === 0 && (
                <tr>
                  <Td className="py-6 text-center text-slate-500">No ingest runs recorded yet.</Td>
                </tr>
              )}
            </tbody>
          </Table>
        </div>
      </div>
    </>
  );
}
