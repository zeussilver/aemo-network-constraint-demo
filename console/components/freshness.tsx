import { formatNemTime } from "@/lib/format";
import type { SourceFreshness } from "@/lib/services/ops";
import { cx } from "./ui";

export const SOURCE_LABELS: Record<string, string> = {
  scada: "Actual MW (SCADA)",
  dispatch: "Dispatch solution",
  intermittent: "Forecast origin",
  forecast: "Risk model",
};

const STATUS = {
  ok: { icon: "●", text: "Fresh", tone: "border-emerald-200 bg-emerald-50 text-emerald-900" },
  stale: { icon: "▲", text: "Stale", tone: "border-amber-300 bg-amber-50 text-amber-900" },
  missing: { icon: "○", text: "No data", tone: "border-slate-300 bg-slate-50 text-slate-700" },
} as const;

export function formatAge(hours: number | null): string {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.max(0, Math.round(hours * 60))} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}

/** One chip per data source: icon + word + age, so state is never carried by colour alone. */
export function FreshnessStrip({ sources }: { sources: SourceFreshness[] }) {
  return (
    <ul className="mb-5 grid grid-cols-2 gap-2 lg:grid-cols-4" aria-label="Data freshness">
      {sources.map((source) => {
        const status = STATUS[source.status];
        const failed = source.lastRunStatus === "failed";
        return (
          <li key={source.source} className={cx("rounded-md border px-3 py-2 text-xs", status.tone)}>
            <p className="font-semibold">
              <span aria-hidden>{status.icon}</span> {SOURCE_LABELS[source.source]} · {status.text}
            </p>
            <p className="mt-0.5">
              Newest {formatNemTime(source.latest)} · age {formatAge(source.ageHours)} (limit {source.limitHours} h)
            </p>
            {failed && <p className="mt-0.5 font-semibold text-red-800">▲ Last ingest run failed</p>}
          </li>
        );
      })}
    </ul>
  );
}
