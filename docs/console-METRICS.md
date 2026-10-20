# Site ops console — measured numbers

Every number below was measured in this repository, and each row names its source. Where a
target from [console-PLAN.md](console-PLAN.md) section 5 is not met or not measured yet,
the row says so.

## Tests (target T5)

| Suite | Count | Source |
|---|---|---|
| pytest (Python pipeline, incl. 4 Postgres integration tests) | 125 passed | CI run 39287811049 |
| Vitest (unit + 15 Postgres integration tests) | 46 passed | CI run 39287811049 |
| Playwright (against the standalone production build) | 8 passed | CI run 39287811049 |
| Runtime dependency audit (`npm audit --omit=dev`) | 0 vulnerabilities | CI run 39287811049 |

The original forecasting pipeline had 97 pytest tests. The console work added 22, and the
Azure Functions ingest job added 6.

## Data (targets T2, T3)

| What | Value | Source |
|---|---|---|
| Sites / DUIDs | 12 real AEMO-registered solar and wind units (NSW1 3, QLD1 6, SA1 2, VIC1 1) | `data/site_registry.csv` |
| First load on Azure | SCADA 105,384 rows (459 files, 36.3 MB); dispatch 103,680 rows (30 trading days, 255.0 MB); intermittent 65,943 rows (7 trading days, 285.3 MB) | deploy run 39186569586, `ingest_run` rows |
| First load locally | 709 s for all four sources, 527.7 MB downloaded | `artifacts/ops_data_checks.json` |
| SCADA vs dispatch `INITIALMW` | median abs. difference 0.0 MW, 100% within 1 MW, over 100,224 interval pairs | `artifacts/ops_data_checks.json` |

## Delivery (target T7)

| What | Value | Source |
|---|---|---|
| Workflow start to deployed and healthy | 6 min 26 s (build 48 s; migrate, app role, seed, deploy, smoke test 5 min 36 s) | deploy run 39186569586 |
| Deploy including the ingest function | 8 min 44 s from push to the function registered (build 1 min 5 s; migrate, app roles, seed, web deploy, smoke test 4 min 32 s; function package and deploy 2 min 56 s) | deploy run 39287810990 |
| Function package | 119.8 MB zip (code, model, Linux cp313 wheels) | deploy run 39287810990 |
| Infrastructure | 17 Azure resources from Bicep (incl. 2 database admins and 1 role assignment), one `infra/deploy.sh` run | deployment `siteops-20261019121250` |
| Infrastructure for the ingest function | 10 more resources (storage account, blob service, container, Flex plan, function app, 3 storage role assignments, 2 basic-auth policies) | deployment `siteops-20261020011558` |
| Stored Azure or database secrets | none (OIDC federation for CI; managed identity + Entra token for the app and the ingest function; the function's storage rejects shared keys) | `infra/resources.bicep`, `infra/ingest-function.bicep` |

## Latency (target T6)

Measured from Melbourne. Every request opens a fresh TLS connection, so the Azure numbers
include about 4 network round trips (mean TCP connect 51 ms).

| Endpoint | Where | p50 | p95 | Source |
|---|---|---|---|---|
| `/api/health` | Azure F1, New Zealand North | 213 ms | 282 ms | console-PLAN.md, 2026-10-19 C4 entry |
| `/login` | Azure | 221 ms | 270 ms | same |
| `/api/ready` (DB queries) | Azure | 319 ms | 491 ms | same |
| `/api/health`, first request after ~25 min idle | Azure | 524 ms (single request) | — | console-PLAN.md, cold-start entry; the next request took 210 ms |
| Site page (signed in) | Azure | — | — | **not measured yet**: needs a console account; run `console/scripts/measure-latency.sh` |
| Site page (signed in) | local standalone build | 23 ms | 25 ms | same script, local run |

## Monitoring (target T8)

| What | Value | Source |
|---|---|---|
| Alert drill | synthetic failed `ingest_run` at 03:28:58 UTC → alert fired 03:44:30 (first hourly evaluation) → auto-resolved 06:45:30 (two clean evaluations) → Resolved email received | Alerts Management API history; user confirmation |
| Server-side request time (App Insights) | `/api/health` p50 5 / p95 13 ms; `/login` 11 / 19 ms; `/api/ready` 84 / 220 ms | `AppRequests`, 2026-10-19 |
| Real stale-data alerts while GitHub's schedule was not firing (1 `schedule` run in about 19 slots) | fired 09:44:32 UTC → resolved 11:45:33; fired 12:44:30 → resolved 15:45:36 | Alerts Management API; the 09:44 Fired email confirmed by the user |
| First Azure Functions timer run, 17:12 UTC 2026-10-19 | succeeded in 8.4 s, 0 exceptions; SCADA 7 files / 84 rows in 3.21 s; dispatch, intermittent and forecast had nothing new | `AppRequests`, `customEvents` (`ingest_run`), `AppExceptions` |

## Hourly ingest on Azure Functions

| What | Value | Source |
|---|---|---|
| Timer runs, 2026-10-19 17:12 to 2026-10-20 01:12 UTC | 9 of 9 succeeded, 0 exceptions | `AppRequests`, `AppExceptions` |
| SCADA-only runs (8) | median 10.3 s (8.4–12.7 s), 7–12 new SCADA files each | `AppRequests`, `customEvents` (`ingest_run`) |
| Run that also loads the daily files (19:12 UTC) | 39.7 s: SCADA 13 files; dispatch 1 file, 3,456 rows; intermittent 1 file (40.3 MB), 9,477 rows; 1 forecast snapshot (19.1 s) | `customEvents` (`ingest_run`) |
| Estimated cost (not a bill) | ≈ 16,655 GB-s a month → ≈ US$0.65 execution time, plus under US$0.50 storage and operations: about US$1/month, assuming no free grant | console-PLAN.md, 2026-10-20 entry |

## Operations data the console surfaced (trading day 2026-10-17)

- Cathedral Rocks: estimated 181.4 MWh curtailed in 120 semi-dispatch-capped intervals.
- Self-forecast share of dispatch intervals:
  - 100%: Bulgana, Goyder South, Mount Emerald;
  - 0% (AEMO AWEFS/ASEFS used all day): Cathedral Rocks, Kidston, Wambo.
- Woolooga: 2,621 of its 3,432 forecast submissions were suppressed by the participant.

Source: screenshots in `docs/images/console-*.png`, taken from the local run on the same
NEMWeb data.
