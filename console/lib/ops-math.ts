/**
 * Pure operational calculations shared by the ops pages and the readiness probe.
 *
 * Time conventions: NEM time is fixed UTC+10. A dispatch interval is labelled by its end
 * time. A trading day D runs from D 04:05 to D+1 04:00 (288 intervals), which is how Next
 * Day Dispatch files are cut.
 */

const NEM_OFFSET_MS = 10 * 60 * 60 * 1000;
const INTERVAL_HOURS = 5 / 60;

/** Trading day (YYYY-MM-DD) that the interval ending at `end` belongs to. */
export function tradingDayOf(end: Date): string {
  const shifted = new Date(end.getTime() + NEM_OFFSET_MS - (4 * 60 + 5) * 60 * 1000);
  return shifted.toISOString().slice(0, 10);
}

/** Interval-end bounds of a trading day: start is exclusive, end inclusive. */
export function tradingDayBounds(day: string): { after: Date; until: Date } {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error(`Invalid trading day ${day}`);
  const midnightNem = Date.parse(`${day}T00:00:00+10:00`);
  if (Number.isNaN(midnightNem)) throw new Error(`Invalid trading day ${day}`);
  return {
    after: new Date(midnightNem + 4 * 60 * 60 * 1000), // D 04:00, exclusive
    until: new Date(midnightNem + 28 * 60 * 60 * 1000), // D+1 04:00, inclusive
  };
}

export type DispatchPoint = {
  totalCleared: number | null;
  uigf: number | null;
  semidispatchCap: number | null;
};

export type CurtailmentSummary = {
  intervals: number;
  cappedIntervals: number;
  clearedMwh: number;
  uigfMwh: number;
  curtailedMwh: number;
};

/**
 * Curtailment estimate for semi-scheduled units: in intervals where the semi-dispatch cap
 * applied, energy the unit could have produced (UIGF) but was not dispatched for. It mixes
 * economic and network curtailment, so it is labelled an estimate in the UI.
 */
export function summariseCurtailment(points: DispatchPoint[]): CurtailmentSummary {
  let capped = 0;
  let cleared = 0;
  let uigf = 0;
  let curtailed = 0;
  for (const point of points) {
    cleared += (point.totalCleared ?? 0) * INTERVAL_HOURS;
    uigf += (point.uigf ?? 0) * INTERVAL_HOURS;
    if (point.semidispatchCap === 1) {
      capped += 1;
      if (point.uigf != null && point.totalCleared != null) {
        curtailed += Math.max(0, point.uigf - point.totalCleared) * INTERVAL_HOURS;
      }
    }
  }
  return { intervals: points.length, cappedIntervals: capped, clearedMwh: cleared, uigfMwh: uigf, curtailedMwh: curtailed };
}

export type OriginCategory = "self" | "aemo" | "fallback";

/** Who produced the forecast dispatch used: the participant (self-forecast), AEMO, or a fallback. */
export function originCategory(origin: string): OriginCategory {
  const value = origin.trim().toUpperCase();
  if (value === "AWEFS_ASEFS") return "aemo";
  if (value === "SCADA" || value === "FCST" || value === "LAST TARGET" || value === "") return "fallback";
  return "self";
}

export const ORIGIN_LABELS: Record<OriginCategory, string> = {
  self: "Self-forecast (participant)",
  aemo: "AEMO AWEFS/ASEFS",
  fallback: "Fallback (SCADA / last target)",
};

export type FreshnessStatus = "ok" | "stale" | "missing";

export type Freshness = { status: FreshnessStatus; ageHours: number | null };

export function assessFreshness(latest: Date | null, maxAgeHours: number, now: Date): Freshness {
  if (!latest) return { status: "missing", ageHours: null };
  const ageHours = (now.getTime() - latest.getTime()) / 3_600_000;
  return { status: ageHours <= maxAgeHours ? "ok" : "stale", ageHours };
}

export type ForecastWindow = { endsAt: Date; ended: boolean };

/**
 * The window a constraint-risk snapshot covers: `horizonHours` after `asOf`. Next Day
 * Dispatch arrives a day late, so a snapshot's window has usually ended by the time staff
 * see it; the UI must then present it as the latest scored forecast, not a live one.
 */
export function forecastWindow(asOf: Date, horizonHours: number, now: Date): ForecastWindow {
  const endsAt = new Date(asOf.getTime() + horizonHours * 3_600_000);
  return { endsAt, ended: now.getTime() >= endsAt.getTime() };
}

/** Freshness limits fixed in docs/console-PLAN.md section 3.2. */
export const FRESHNESS_LIMITS_HOURS = {
  scada: 2,
  dispatch: 30,
  intermittent: 30,
  forecast: 30,
} as const;

export type SourceName = keyof typeof FRESHNESS_LIMITS_HOURS;
