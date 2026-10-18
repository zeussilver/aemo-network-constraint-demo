// Liveness: the Node process is up and serving. No database call, so a database outage
// does not make App Service restart a healthy web process.
export function GET() {
  return Response.json({ status: "ok", service: "site-ops-console" }, { headers: { "Cache-Control": "no-store" } });
}
