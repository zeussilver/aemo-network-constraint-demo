import { assessFreshness, forecastWindow, FRESHNESS_LIMITS_HOURS, originCategory, summariseCurtailment, tradingDayBounds, tradingDayOf, type Freshness, type OriginCategory, type SourceName } from "../ops-math";
import { assertCan } from "./audit";
import { NotFoundError, type Actor, type Db } from "./types";

/** Newest interval per source, with its age against the limit. Also used by /api/ready. */
export async function getFreshness(db: Db, now = new Date()) {
  const [scada, dispatch, intermittent, forecast, runs] = await Promise.all([
    db.scadaReading.aggregate({ _max: { settlementDate: true } }),
    db.dispatchSolution.aggregate({ _max: { settlementDate: true } }),
    db.forecastUsed.aggregate({ _max: { settlementDate: true } }),
    db.forecastSnapshot.aggregate({ _max: { asOf: true } }),
    db.$queryRaw<Array<{ source: string; status: string; finished_at: Date | null; error: string | null }>>`
      SELECT DISTINCT ON (source) source, status, finished_at, error
      FROM ingest_run ORDER BY source, started_at DESC`,
  ]);
  const latest: Record<SourceName, Date | null> = {
    scada: scada._max.settlementDate,
    dispatch: dispatch._max.settlementDate,
    intermittent: intermittent._max.settlementDate,
    forecast: forecast._max.asOf,
  };
  const lastRun = new Map(runs.map((run) => [run.source, run]));
  return (Object.keys(FRESHNESS_LIMITS_HOURS) as SourceName[]).map((source) => {
    const run = lastRun.get(source);
    const freshness: Freshness = assessFreshness(latest[source], FRESHNESS_LIMITS_HOURS[source], now);
    return {
      source,
      latest: latest[source],
      limitHours: FRESHNESS_LIMITS_HOURS[source],
      ...freshness,
      lastRunStatus: run?.status ?? null,
      lastRunFinished: run?.finished_at ?? null,
      lastRunError: run?.error ?? null,
    };
  });
}

export type SourceFreshness = Awaited<ReturnType<typeof getFreshness>>[number];

export async function latestTradingDay(db: Db, duids?: string[]): Promise<string | null> {
  const row = await db.dispatchSolution.aggregate({
    _max: { settlementDate: true },
    where: duids ? { duid: { in: duids } } : undefined,
  });
  return row._max.settlementDate ? tradingDayOf(row._max.settlementDate) : null;
}

/** Fleet table: one row per active site with its latest actuals and yesterday's numbers. */
export async function getFleetOverview(db: Db, actor: Actor, now = new Date()) {
  assertCan(actor, "ops:read");
  const sites = await db.site.findMany({
    where: { archivedAt: null },
    orderBy: { name: "asc" },
    include: { customer: { select: { name: true, isSynthetic: true } }, units: { select: { duid: true } } },
  });
  const duids = sites.flatMap((site) => site.units.map((unit) => unit.duid));
  const day = await latestTradingDay(db);
  if (duids.length === 0) return { day, sites: [], freshness: await getFreshness(db, now) };

  const bounds = day ? tradingDayBounds(day) : null;
  const [scada, dispatch, used, snapshots, freshness] = await Promise.all([
    db.$queryRaw<Array<{ duid: string; settlement_date: Date; mw: number }>>`
      SELECT DISTINCT ON (duid) duid, settlement_date, mw FROM scada_reading
      WHERE duid = ANY(${duids}) ORDER BY duid, settlement_date DESC`,
    bounds
      ? db.dispatchSolution.findMany({
          where: { duid: { in: duids }, settlementDate: { gt: bounds.after, lte: bounds.until } },
          select: { duid: true, totalCleared: true, uigf: true, semidispatchCap: true },
        })
      : Promise.resolve([]),
    bounds
      ? db.forecastUsed.groupBy({
          by: ["duid", "origin"],
          where: { duid: { in: duids }, settlementDate: { gt: bounds.after, lte: bounds.until } },
          _count: { _all: true },
        })
      : Promise.resolve([]),
    db.$queryRaw<Array<{ duid: string; as_of: Date; horizon_hours: number; probability: number; risk_level: string }>>`
      SELECT DISTINCT ON (duid) duid, as_of, horizon_hours, probability, risk_level FROM forecast_snapshot
      WHERE duid = ANY(${duids}) ORDER BY duid, as_of DESC`,
    getFreshness(db, now),
  ]);

  const scadaBy = new Map(scada.map((row) => [row.duid, row]));
  const snapshotBy = new Map(snapshots.map((row) => [row.duid, row]));
  return {
    day,
    freshness,
    sites: sites.map((site) => {
      const siteDuids = site.units.map((unit) => unit.duid);
      const latest = siteDuids.map((duid) => scadaBy.get(duid)).filter((row) => row !== undefined);
      const usedRows = used.filter((row) => siteDuids.includes(row.duid));
      const usedTotal = usedRows.reduce((sum, row) => sum + row._count._all, 0);
      const usedSelf = usedRows
        .filter((row) => originCategory(row.origin) === "self")
        .reduce((sum, row) => sum + row._count._all, 0);
      const snapshot = siteDuids.map((duid) => snapshotBy.get(duid)).find(Boolean) ?? null;
      return {
        id: site.id,
        name: site.name,
        region: site.region,
        fuel: site.fuel,
        capacityMw: site.registeredCapacityMw,
        customer: site.customer,
        duids: siteDuids,
        latestMw: latest.length ? latest.reduce((sum, row) => sum + row.mw, 0) : null,
        latestAt: latest.length ? new Date(Math.min(...latest.map((row) => row.settlement_date.getTime()))) : null,
        curtailment: summariseCurtailment(dispatch.filter((row) => siteDuids.includes(row.duid))),
        selfForecastShare: usedTotal ? usedSelf / usedTotal : null,
        snapshot: snapshot ? { ...snapshot, window: forecastWindow(snapshot.as_of, snapshot.horizon_hours, now) } : null,
      };
    }),
  };
}

export type IntervalPoint = {
  t: string; // ISO interval end
  scada: number | null;
  uigf: number | null;
  availability: number | null;
  cleared: number | null;
  capped: boolean;
};

/** One trading day of operations for a site: series, curtailment, forecast origins, risk. */
export async function getSiteOps(db: Db, actor: Actor, siteId: string, requestedDay?: string, now = new Date()) {
  assertCan(actor, "ops:read");
  const site = await db.site.findUnique({ where: { id: siteId }, include: { units: { select: { duid: true } } } });
  if (!site) throw new NotFoundError("Site", siteId);
  const duids = site.units.map((unit) => unit.duid);
  if (duids.length === 0) return null;

  const dayRows = await db.$queryRaw<Array<{ day: string }>>`
    SELECT DISTINCT to_char((settlement_date AT TIME ZONE 'Australia/Brisbane') - interval '4 hours 5 minutes', 'YYYY-MM-DD') AS day
    FROM dispatch_solution WHERE duid = ANY(${duids}) ORDER BY day DESC LIMIT 60`;
  const days = dayRows.map((row) => row.day);
  const day = requestedDay && days.includes(requestedDay) ? requestedDay : days[0];

  const [latestScada, snapshot] = await Promise.all([
    db.$queryRaw<Array<{ duid: string; settlement_date: Date; mw: number }>>`
      SELECT DISTINCT ON (duid) duid, settlement_date, mw FROM scada_reading
      WHERE duid = ANY(${duids}) ORDER BY duid, settlement_date DESC`,
    db.forecastSnapshot.findFirst({ where: { duid: { in: duids } }, orderBy: { asOf: "desc" } }),
  ]);
  const snapshotWindow = snapshot ? forecastWindow(snapshot.asOf, snapshot.horizonHours, now) : null;
  if (!day) return { day: null, days, duids, latestScada, snapshot, snapshotWindow, points: [], curtailment: null, origins: [], suppression: [] };

  const { after, until } = tradingDayBounds(day);
  const window = { duid: { in: duids }, settlementDate: { gt: after, lte: until } };
  const [scada, dispatch, used, suppression] = await Promise.all([
    db.scadaReading.findMany({ where: window }),
    db.dispatchSolution.findMany({ where: window }),
    db.forecastUsed.groupBy({ by: ["duid", "origin"], where: window, _count: { _all: true }, _avg: { forecastPriority: true } }),
    db.forecastOriginInterval.groupBy({
      by: ["duid", "origin"],
      where: window,
      _sum: { submissions: true, suppressedAemo: true, suppressedParticipant: true },
    }),
  ]);

  const points = new Map<string, IntervalPoint>();
  const point = (time: Date) => {
    const key = time.toISOString();
    let entry = points.get(key);
    if (!entry) {
      entry = { t: key, scada: null, uigf: null, availability: null, cleared: null, capped: false };
      points.set(key, entry);
    }
    return entry;
  };
  const add = (current: number | null, value: number | null) => (value == null ? current : (current ?? 0) + value);
  for (const row of scada) point(row.settlementDate).scada = add(point(row.settlementDate).scada, row.mw);
  for (const row of dispatch) {
    const entry = point(row.settlementDate);
    entry.uigf = add(entry.uigf, row.uigf);
    entry.availability = add(entry.availability, row.availability);
    entry.cleared = add(entry.cleared, row.totalCleared);
    entry.capped = entry.capped || row.semidispatchCap === 1;
  }

  const origins = used
    .map((row) => ({
      duid: row.duid,
      origin: row.origin,
      category: originCategory(row.origin) as OriginCategory,
      intervals: row._count._all,
    }))
    .sort((a, b) => b.intervals - a.intervals);

  return {
    day,
    days,
    duids,
    latestScada,
    snapshot,
    snapshotWindow,
    points: [...points.values()].sort((a, b) => a.t.localeCompare(b.t)),
    curtailment: summariseCurtailment(dispatch),
    origins,
    suppression: suppression
      .map((row) => ({
        duid: row.duid,
        origin: row.origin,
        category: originCategory(row.origin) as OriginCategory,
        submissions: row._sum.submissions ?? 0,
        suppressedAemo: row._sum.suppressedAemo ?? 0,
        suppressedParticipant: row._sum.suppressedParticipant ?? 0,
      }))
      .sort((a, b) => b.submissions - a.submissions),
  };
}

export async function listIngestRuns(db: Db, actor: Actor, take = 30) {
  assertCan(actor, "ops:read");
  return db.ingestRun.findMany({ orderBy: { startedAt: "desc" }, take });
}

export async function tableRowCounts(db: Db, actor: Actor) {
  assertCan(actor, "ops:read");
  const [scada, dispatch, used, origins, snapshots] = await Promise.all([
    db.scadaReading.count(),
    db.dispatchSolution.count(),
    db.forecastUsed.count(),
    db.forecastOriginInterval.count(),
    db.forecastSnapshot.count(),
  ]);
  return { scada, dispatch, used, origins, snapshots };
}
