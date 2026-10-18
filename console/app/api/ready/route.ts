import { db } from "@/lib/db";
import { getFreshness } from "@/lib/services/ops";

/**
 * Readiness for the uptime check and the data-freshness alert: 200 when the database
 * answers and every NEMWeb source is inside its freshness limit, 503 otherwise, with the
 * reason in the body. Public, so it returns only source names, ages and statuses.
 */
export async function GET() {
  const headers = { "Cache-Control": "no-store" };
  try {
    const sources = await getFreshness(db);
    const failing = sources.filter((source) => source.status !== "ok" || source.lastRunStatus === "failed");
    const body = {
      status: failing.length ? "degraded" : "ok",
      database: "ok",
      sources: sources.map((source) => ({
        source: source.source,
        status: source.status,
        newest: source.latest?.toISOString() ?? null,
        ageHours: source.ageHours == null ? null : Number(source.ageHours.toFixed(2)),
        limitHours: source.limitHours,
        lastRun: source.lastRunStatus,
      })),
    };
    return Response.json(body, { status: failing.length ? 503 : 200, headers });
  } catch (error) {
    console.error("Readiness check failed", error);
    return Response.json({ status: "down", database: "unreachable" }, { status: 503, headers });
  }
}
