import { formatNemTime } from "@/lib/format";
import type { ForecastWindow } from "@/lib/ops-math";
import { Badge, type BadgeTone, Notice } from "./ui";

type Snapshot = {
  duid: string;
  asOf: Date;
  generatedAt: Date;
  horizonHours: number;
  probability: number;
  riskLevel: string;
  dataLagHours: number | null;
  modelFile: string;
  payload: unknown;
};

const RISK_TONES: Record<string, BadgeTone> = { high: "red", medium: "amber", low: "blue", very_low: "slate" };

export function riskTone(level: string): BadgeTone {
  return RISK_TONES[level] ?? "slate";
}

type CurvePoint = { lead_hours: number; window_start: string; probability: number; risk_level: string };

/** Constraint-risk snapshot from the repo's model, shown with its own caveats. */
export function RiskCard({ snapshot, horizon }: { snapshot: Snapshot | null; horizon: ForecastWindow | null }) {
  if (!snapshot) {
    return (
      <p className="text-sm text-slate-600">
        No constraint-risk model is trained for this site&apos;s DUIDs. The demo ships one model (CATHROCK, 6-hour horizon).
      </p>
    );
  }
  const payload = (snapshot.payload ?? {}) as { risk_curve?: CurvePoint[]; limitations?: string; feature_coverage?: { imputed?: number; required?: number } };
  const curve = payload.risk_curve ?? [];
  return (
    <div className="flex flex-col gap-3 text-sm">
      {horizon?.ended && (
        <Notice tone="amber">
          Window ended {formatNemTime(horizon.endsAt)}. This is the latest scored forecast, not a live one: Next Day Dispatch publishes the
          previous trading day once a day.
        </Notice>
      )}
      <div className="flex items-baseline gap-3">
        <span className={`text-3xl font-semibold tabular-nums${horizon?.ended ? " text-slate-400" : ""}`}>{(snapshot.probability * 100).toFixed(0)}%</span>
        <Badge tone={riskTone(snapshot.riskLevel)}>{snapshot.riskLevel.replace("_", " ")} risk</Badge>
      </div>
      <p className="text-slate-700">
        Probability that a network constraint relevant to {snapshot.duid} binds in the {snapshot.horizonHours} hours after{" "}
        {formatNemTime(snapshot.asOf)}.
      </p>
      {curve.length > 0 && (
        <table className="text-xs tabular-nums">
          <thead className="text-slate-500">
            <tr>
              <th className="pr-3 text-left font-medium">Hour ahead</th>
              <th className="pr-3 text-right font-medium">P(bind)</th>
              <th className="text-left font-medium">Band</th>
            </tr>
          </thead>
          <tbody>
            {curve.map((point) => (
              <tr key={point.lead_hours}>
                <td className="pr-3">+{point.lead_hours} h</td>
                <td className="pr-3 text-right">{(point.probability * 100).toFixed(0)}%</td>
                <td>{point.risk_level.replace("_", " ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <ul className="list-disc pl-4 text-xs text-slate-600">
        <li>
          Forecast origin is the newest published dispatch interval; inputs were {snapshot.dataLagHours?.toFixed(1) ?? "?"} h old when it was
          scored (Next Day Dispatch is published once a day).
        </li>
        {payload.feature_coverage && (
          <li>
            Features observed: {(payload.feature_coverage.required ?? 0) - (payload.feature_coverage.imputed ?? 0)} of {payload.feature_coverage.required}.
          </li>
        )}
        <li>Treat it as a ranking signal, not a calibrated probability. {payload.limitations}</li>
        <li>
          Model file {snapshot.modelFile}; scored {snapshot.generatedAt.toISOString().slice(0, 16).replace("T", " ")} UTC.
        </li>
      </ul>
    </div>
  );
}
