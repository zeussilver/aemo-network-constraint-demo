# Site Ops Console

Next.js 16 staff console for this repository's renewable sites: customers, sites and
DUIDs with role-based access and an audit log, plus operational views built from AEMO
NEMWeb data that the Python ingest job (`src/aemo_constraint_demo/ops_ingest.py`) loads.
Customers are synthetic; sites, DUIDs and all operational numbers are real public data.

The plan, decisions and measured results live in [`../docs/console-PLAN.md`](../docs/console-PLAN.md).

## Run locally

```bash
docker compose up -d --wait              # Postgres 17 on 127.0.0.1:55460 (repo root)
cp .env.example .env                     # then fill BETTER_AUTH_SECRET and the SEED_* passwords
npm ci                                   # also generates the Prisma client
npm run db:deploy && npm run db:seed     # schema, AEMO registry, synthetic customers, staff users
npm run dev                              # http://localhost:3000
```

Load NEMWeb data (from the repo root, with the Python environment from the main README):

```bash
DATABASE_URL=postgresql://ops:ops_local_only@127.0.0.1:55460/ops_console \
  python -m src.aemo_constraint_demo.ops_ingest --trigger backfill
```

## Checks

| Command | What it runs |
| --- | --- |
| `npm run lint` / `npm run typecheck` | ESLint, `tsc --noEmit` |
| `npm test` | Vitest: unit tests plus service integration tests on `TEST_DATABASE_URL` |
| `npm run build` | Standalone production build (`.next/standalone/server.js`) |
| `npm run test:e2e` | Resets `E2E_DATABASE_URL` (name must end in `_e2e`), then Playwright against the standalone build |

## Layout

```
app/(console)/     authenticated pages: overview, sites, customers, status, audit
app/api/           auth handler, /api/health (liveness), /api/ready (DB + data freshness)
lib/services/      data access with permission checks; used by pages and server actions
lib/rbac.ts        the role → permission table
lib/ops-math.ts    trading days, curtailment estimate, forecast-origin categories, freshness
prisma/            schema (owns the ops tables too), migrations, seed
e2e/               Playwright specs and the e2e database preparation
```
