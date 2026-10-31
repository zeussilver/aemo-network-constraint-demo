import Link from "next/link";
import { formatAge } from "@/components/freshness";
import { RiskCard } from "@/components/risk-card";
import { SiteOpsChart } from "@/components/site-ops-chart";
import { Badge, Card, Table, Td, Th } from "@/components/ui";
import { formatMw, formatNemTime } from "@/lib/format";
import { ORIGIN_LABELS, type OriginCategory } from "@/lib/ops-math";
import type { getSiteOps } from "@/lib/services/ops";

type SiteOps = NonNullable<Awaited<ReturnType<typeof getSiteOps>>>;

const CATEGORY_TONE: Record<OriginCategory, "green" | "blue" | "amber"> = { self: "green", aemo: "blue", fallback: "amber" };

function Stat({ label, value, note, warning }: { label: string; value: string; note?: string; warning?: string }) {
  return (
    <div className={warning ? "rounded-lg border border-amber-300 bg-amber-50 px-4 py-3" : "rounded-lg border border-slate-200 bg-white px-4 py-3"}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">{value}</p>
      {note && <p className="mt-0.5 text-xs text-slate-500">{note}</p>}
      {warning && <p className="mt-0.5 text-xs font-semibold text-amber-900">▲ {warning}</p>}
    </div>
  );
}

export function OpsSection({ siteId, ops, capacityMw, simulated = false }: { siteId: string; ops: SiteOps; capacityMw: number; simulated?: boolean }) {
  const latestMw = ops.latestScada.reduce((sum, row) => sum + row.mw, 0);
  const latestAt = ops.latestScada.length ? new Date(Math.min(...ops.latestScada.map((row) => new Date(row.settlement_date).getTime()))) : null;
  const stale =
    ops.latestFreshness.status === "stale" ? `Stale: no SCADA for ${formatAge(ops.latestFreshness.ageHours)} (limit 2 h)` : undefined;
  const totalIntervals = ops.origins.reduce((sum, row) => sum + row.intervals, 0);
  const selfIntervals = ops.origins.filter((row) => row.category === "self").reduce((sum, row) => sum + row.intervals, 0);
  const curtailment = ops.curtailment;

  return (
    <section className="mt-6" aria-labelledby="ops-heading">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="ops-heading" className="text-lg font-semibold">
            Operations
          </h2>
          <p className="text-sm text-slate-600">
            {simulated ? "Simulated data, in the shape of AEMO's NEMWeb tables," : "Public AEMO NEMWeb data"} for {ops.duids.join(", ")}. Trading day{" "}
            {ops.day ?? "—"} (04:05 → 04:00 NEM time).
          </p>
        </div>
        {ops.days.length > 0 && (
          <nav aria-label="Trading day" className="flex flex-wrap gap-1 text-xs">
            {ops.days.slice(0, 7).map((day) => (
              <Link
                key={day}
                href={`/sites/${siteId}?day=${day}`}
                aria-current={day === ops.day ? "page" : undefined}
                className={
                  day === ops.day
                    ? "rounded border border-slate-900 bg-slate-900 px-2 py-1 text-white"
                    : "rounded border border-slate-300 bg-white px-2 py-1 text-slate-700 hover:bg-slate-100"
                }
              >
                {day.slice(5)}
              </Link>
            ))}
          </nav>
        )}
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Latest actual" value={latestAt ? formatMw(latestMw) : "—"} note={latestAt ? formatNemTime(latestAt) : "No SCADA yet"} warning={stale} />
        <Stat label="Energy dispatched" value={curtailment ? `${curtailment.clearedMwh.toFixed(0)} MWh` : "—"} note="Σ dispatch target × 5 min" />
        <Stat
          label="Curtailment (estimate)"
          value={curtailment ? `${curtailment.curtailedMwh.toFixed(1)} MWh` : "—"}
          note={curtailment ? `${curtailment.cappedIntervals} of ${curtailment.intervals} intervals capped` : undefined}
        />
        <Stat
          label="Self-forecast used"
          value={totalIntervals ? `${((selfIntervals / totalIntervals) * 100).toFixed(0)}%` : "—"}
          note={totalIntervals ? `${selfIntervals} of ${totalIntervals} intervals` : "No intermittent data for this day"}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Actual vs available vs dispatched (MW)" className="xl:col-span-2">
          {ops.points.length > 0 ? (
            <SiteOpsChart points={ops.points} capacityMw={capacityMw} />
          ) : (
            <p className="text-sm text-slate-600">No dispatch data for this trading day yet.</p>
          )}
        </Card>
        <Card title="Constraint risk">
          <RiskCard snapshot={ops.snapshot} horizon={ops.snapshotWindow} />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Which forecast dispatch used">
          {ops.origins.length > 0 ? (
            <Table>
              <thead className="bg-slate-50">
                <tr>
                  <Th>Source</Th>
                  <Th>Origin code</Th>
                  <Th className="text-right">Intervals</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {ops.origins.map((row) => (
                  <tr key={`${row.duid}-${row.origin}`}>
                    <Td>
                      <Badge tone={CATEGORY_TONE[row.category]}>{ORIGIN_LABELS[row.category]}</Badge>
                    </Td>
                    <Td className="font-mono text-xs">{row.origin}</Td>
                    <Td className="text-right tabular-nums">{row.intervals}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <p className="text-sm text-slate-600">No INTERMITTENT_FORECAST_TRK rows for this day (the console loads the last 7 days).</p>
          )}
          <p className="mt-2 text-xs text-slate-500">From AEMO INTERMITTENT_FORECAST_TRK. Origin codes are AEMO participant IDs or AEMO&apos;s own AWEFS/ASEFS.</p>
        </Card>
        <Card title="Forecast submissions and suppression">
          {ops.suppression.length > 0 ? (
            <Table>
              <thead className="bg-slate-50">
                <tr>
                  <Th>Origin</Th>
                  <Th className="text-right">Submissions</Th>
                  <Th className="text-right">Suppressed by participant</Th>
                  <Th className="text-right">Suppressed by AEMO</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {ops.suppression.map((row) => (
                  <tr key={`${row.duid}-${row.origin}`}>
                    <Td className="font-mono text-xs">{row.origin}</Td>
                    <Td className="text-right tabular-nums">{row.submissions.toLocaleString("en-AU")}</Td>
                    <Td className="text-right tabular-nums">{row.suppressedParticipant.toLocaleString("en-AU")}</Td>
                    <Td className="text-right tabular-nums">{row.suppressedAemo.toLocaleString("en-AU")}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <p className="text-sm text-slate-600">No INTERMITTENT_DS_RUN rows for this day.</p>
          )}
          <p className="mt-2 text-xs text-slate-500">
            From AEMO INTERMITTENT_DS_RUN. Per AEMO, suppressed submissions may not be used; dispatch takes the highest-priority
            unsuppressed forecast.
          </p>
        </Card>
      </div>
    </section>
  );
}
