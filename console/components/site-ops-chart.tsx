"use client";

import { CartesianGrid, ComposedChart, Legend, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { IntervalPoint } from "@/lib/services/ops";

// Categorical slots 1-3 of the dataviz reference palette, validated (all pairs, light).
const SERIES = [
  { key: "scada", label: "Actual (SCADA)", color: "#2a78d6" },
  { key: "uigf", label: "Available (UIGF)", color: "#eb6834" },
  { key: "cleared", label: "Dispatch target", color: "#1baf7a" },
] as const;

const time = new Intl.DateTimeFormat("en-AU", { timeZone: "Australia/Brisbane", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const label = (iso: string) => time.format(new Date(iso));

/** Contiguous runs of capped intervals, drawn as shaded bands behind the lines. */
function cappedSpans(points: IntervalPoint[]): Array<[string, string]> {
  const spans: Array<[string, string]> = [];
  let start: string | null = null;
  points.forEach((point, index) => {
    if (point.capped && start === null) start = point.t;
    const next = points[index + 1];
    if (point.capped && (!next || !next.capped) && start !== null) {
      spans.push([start, point.t]);
      start = null;
    }
  });
  return spans;
}

type TooltipEntry = { dataKey?: string | number; value?: number | null; color?: string };

function ChartTooltip({ active, payload, label: at }: { active?: boolean; payload?: TooltipEntry[]; label?: string }) {
  if (!active || !payload?.length || !at) return null;
  const point = (payload[0] as { payload?: IntervalPoint }).payload;
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-sm">
      <p className="mb-1 font-medium text-slate-700">Interval ending {label(at)} NEM</p>
      {SERIES.map((series) => {
        const value = point?.[series.key];
        return (
          <p key={series.key} className="flex items-center gap-2">
            <span className="inline-block h-0.5 w-3" style={{ background: series.color }} />
            <span className="font-semibold tabular-nums text-slate-900">{value == null ? "—" : `${value.toFixed(1)} MW`}</span>
            <span className="text-slate-500">{series.label}</span>
          </p>
        );
      })}
      {point?.capped && <p className="mt-1 text-slate-600">Semi-dispatch cap applied</p>}
    </div>
  );
}

export function SiteOpsChart({ points, capacityMw }: { points: IntervalPoint[]; capacityMw: number }) {
  const spans = cappedSpans(points);
  return (
    <figure>
      <div className="h-72 w-full" role="img" aria-label="Actual output, available capacity and dispatch target in MW for each 5-minute interval of the trading day">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 16, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid stroke="#e2e8f0" vertical={false} />
            {spans.map(([from, to]) => (
              <ReferenceArea key={from} x1={from} x2={to} fill="#cbd5e1" fillOpacity={0.45} ifOverflow="hidden" />
            ))}
            <XAxis dataKey="t" tickFormatter={label} minTickGap={48} tick={{ fontSize: 11, fill: "#64748b" }} stroke="#cbd5e1" />
            <YAxis
              domain={[0, Math.ceil(capacityMw / 10) * 10]}
              tick={{ fontSize: 11, fill: "#64748b" }}
              stroke="#cbd5e1"
              width={40}
              label={{ value: "MW", position: "insideTopLeft", offset: 0, dy: -14, fontSize: 11, fill: "#64748b" }}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: "#94a3b8", strokeWidth: 1 }} isAnimationActive={false} />
            <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: "#334155" }} />
            {SERIES.map((series) => (
              <Line
                key={series.key}
                type="linear"
                dataKey={series.key}
                name={series.label}
                stroke={series.color}
                strokeWidth={2}
                strokeDasharray={series.key === "uigf" ? "5 3" : undefined}
                dot={false}
                activeDot={{ r: 4, stroke: "#ffffff", strokeWidth: 2 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-1 text-xs text-slate-500">
        Shaded bands: intervals where AEMO applied the semi-dispatch cap. SCADA is the MW reading at the start of each
        interval; UIGF and the dispatch target refer to its end.
      </figcaption>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-slate-600">Show the interval data as a table</summary>
        <div className="mt-2 max-h-72 overflow-y-auto">
          <table className="min-w-full text-right tabular-nums">
            <thead className="sticky top-0 bg-white text-slate-500">
              <tr>
                <th className="px-2 py-1 text-left">Interval end</th>
                {SERIES.map((series) => (
                  <th key={series.key} className="px-2 py-1">
                    {series.label} (MW)
                  </th>
                ))}
                <th className="px-2 py-1">Capped</th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.t} className="border-t border-slate-100">
                  <td className="px-2 py-0.5 text-left">{label(point.t)}</td>
                  {SERIES.map((series) => (
                    <td key={series.key} className="px-2 py-0.5">
                      {point[series.key]?.toFixed(1) ?? "—"}
                    </td>
                  ))}
                  <td className="px-2 py-0.5">{point.capped ? "yes" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
