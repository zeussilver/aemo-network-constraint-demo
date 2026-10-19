# Site ops console — plan

Extends this repo with a lean **Next.js admin/ops console** for staff who manage
customers and renewable sites and watch their operational data. It sits next to the
existing Python pipeline (`src/aemo_constraint_demo`, FastAPI, MCP server, 97 pytest
tests) and reuses it: the same NEMWeb parsers feed the console's database, and the
same `forecast_from_bundle` produces the constraint-risk card.

Data rules:
- **Sites and units are real**: AEMO-registered solar/wind DUIDs, with capacity and
  region from AEMO standing data and fuel/station name from the AEMO NEM Registration
  and Exemption List.
- **Customers and customer→site assignments are synthetic**, labelled "Synthetic" in
  the UI and flagged in the database. No ownership is implied.
- **Operational numbers are real NEMWeb public data**, shown with their source and age.
- The console carries no third-party company name, logo or branding.

Status: approved 2026-10-19 (Option A). Build starts 2026-10-22 on branch `ops-console`.

---

## 1. Verified facts (checked 2026-10-19)

### NEMWeb sources
| Need | Source | Table / columns (verified) | Size / cadence (from directory listings) |
|---|---|---|---|
| Actual MW | `Reports/CURRENT/Dispatch_SCADA/` (5-min files), `Reports/ARCHIVE/Dispatch_SCADA/` (daily) | `DISPATCH_UNIT_SCADA`: `SETTLEMENTDATE, DUID, SCADAVALUE, LASTCHANGED` (MMS Data Model Report). `SCADAVALUE` = MW at the **start** of the interval | CURRENT: 576 files (~2 days), ~3.7 KB each. ARCHIVE: 375 daily zips, ~1.1–1.2 MB, published with ~2 days lag |
| Availability, UIGF, cap, cleared | `Reports/CURRENT/Next_Day_Dispatch/` (already used by this repo) | `DISPATCH,UNIT_SOLUTION` (= DISPATCHLOAD), read from a cached real file: `INITIALMW, TOTALCLEARED, AVAILABILITY, SEMIDISPATCHCAP, UIGF, CONFORMANCE_MODE, DISPATCHMODE` | 395 daily zips (2025-09-18 → 2026-10-17), ~8 MB zip / ~122 MB CSV each, published ~04:15 for the previous trading day |
| Forecast origin, priority, suppression | `Reports/CURRENT/Next_Day_Intermittent_DS/` | `INTERMITTENT_DS_RUN`: `RUN_DATETIME, DUID, OFFERDATETIME, ORIGIN, FORECAST_PRIORITY, SUPPRESSED_AEMO, SUPPRESSED_PARTICIPANT, TRANSACTION_ID` (`AUTHORISEDBY, COMMENTS, MODEL` are not public). `INTERMITTENT_DS_PRED`: `… INTERVAL_DATETIME, FORECAST_MEAN, FORECAST_POE10/50/90` (POE50 used in dispatch). `INTERMITTENT_FORECAST_TRK`: which forecast dispatch used per DUID/interval (`ORIGIN, FORECAST_PRIORITY`; −1 = SCADA/FCST, 0 = last target) | 60 daily zips, **~40 MB each**. Monthly archive ~1.2 GB, not used |
| Site registry | AEMO "NEM Registration and Exemption List.xlsx" | fuel source, technology, station name | 785 KB, last modified 2026-10-05 (HTTP headers only, not downloaded yet) |
| Capacity, connection point | MMSDM `DUDETAIL` (already cached) | `REGISTEREDCAPACITY, MAXCAPACITY, DISPATCHTYPE, CONNECTIONPOINTID` | April 2026 archive in `data/cache/` |

The UNIT_SOLUTION and DUDETAIL columns were read from real cached files. The SCADA
and Intermittent_DS columns come from AEMO's MMS Data Model Report. The first build
task downloads one real file of each and checks the `I,` header rows before any parser
is written. One open check: whether `INTERMITTENT_FORECAST_TRK` ships in the
Next_Day_Intermittent_DS zip. If it does not, "forecast used" is derived with the rule
AEMO documents on `INTERMITTENT_DS_RUN`: the unsuppressed run with the highest
`FORECAST_PRIORITY`. The README will say which path was used.

### Candidate sites
These are the DUIDs where this repo's constraint scan found real binding history
(`artifacts/duid_constraint_scan_expanded_top.csv`). Capacities come from the cached
April 2026 `DUDETAIL`. Station names, fuel and region get confirmed from the
Registration List / `DUDETAILSUMMARY` at build time.

| DUID | Connection point | Registered / max MW | 6h positive intervals (scan) |
|---|---|---|---|
| CATHROCK | SCRK | 66 / 66 | 228 (trained model exists) |
| BULGANA1 | VBGT1B | 204 / 204 | 151 |
| WOOLGSF1 | QWLS1W | 222 / 176 | 147 |
| MUCRKSF1 | QWLM1M | 154 / 120 | 144 |
| WAMBOWF2 | QDMR1W | 254 / 247 | 75 |
| GSWF1A | SROB1G | 209 / 201 | 76 |
| GNNDHSF1 | NGNE1G | 144 / 110 | 76 |
| RRSF1 | QROG3R | 128 / 116 | 75 |
| MEWF1 | QWKM1M | 180 / 178 | 74 |
| WELNSF1 | NWLN1W | 436 / 330 | 73 |
| KSP1 | QROG1K | 50 / 47 | 73 |
| GOONSF1 | NPG12G | 85 / 69 | 72 |

Ten of these are seeded, keeping a mix of regions and solar/wind once fuel is confirmed.
Only CATHROCK has a trained model bundle. The other sites show "no model" rather than
a made-up risk.

### Stack versions (npm registry, 2026-10-19)
`next 16.4.2`, `react 19.3.1`, `@prisma/client`/`prisma 7.10.1` (the `prisma` latest tag
points at `8.0.0-rc.23`, so the stable 7.x is pinned on purpose), `better-auth 1.7.10`,
`zod 4.6.7`, `vitest 5.0.5`, `@playwright/test 1.64.1`, `recharts 3.10.1`,
`tailwindcss 4.3.3`, `@azure/monitor-opentelemetry 1.20.1`. Local: Node 22.23, Docker 29.
`az` CLI is **not installed** (needs `brew install azure-cli`).

### Azure facts
- App Service **Free F1** (Microsoft limits page): 60 CPU minutes/day, 1 GB memory,
  1 GB storage, no Always On (cold starts after idle), `*.azurewebsites.net` only,
  Linux code apps supported. If an app exceeds the quota it is stopped until the reset (HTTP 403).
- PostgreSQL Flexible Server **B1MS**: 750 h/month + 32 GB storage free for 12 months
  under the free services offer (listed for Azure for Students). Check it in the portal
  ("Free services").
- Log Analytics / App Insights: first 5 GB/month ingestion free.
- Static Web Apps hybrid Next.js is still **preview** and runs its backend on a managed
  App Service anyway. Not chosen.
- **Azure for Students region policy**: these subscriptions carry an "Allowed resource
  deployment regions" policy, often around 5 regions that differ per subscription.
  Whether Australia East/Southeast is allowed must be checked first (Section 7).
- Classic URL ping tests were retired on 2026-09-30. Only paid "standard" availability
  tests remain.

---

## 2. Decisions (with alternatives)

| Topic | Choice | Alternative and why not (yet) |
|---|---|---|
| Location | `console/` folder in this repo, sharing one Postgres with the Python pipeline | Separate repo: splits the flagship story |
| Framework | Next.js 16 App Router, TypeScript strict, server components + server actions | — (the brief asks for React/Next.js) |
| Auth | **Better Auth** (email + password, `role` field), Prisma adapter | Auth.js v5: still `5.0.0-beta.32`, maintenance-only since the Better Auth team took it over in Sep 2025, and the docs point new projects to Better Auth. Entra ID sign-in is a stretch goal, only if the tenant allows an app registration |
| ORM | Prisma 7.10.1 pinned, migrations own the schema | Drizzle: lighter, but Prisma is more widely used. Prisma 8 is still RC |
| UI | Tailwind 4 + a few shadcn/ui components, Recharts | MUI: heavier |
| Forecast in the console | **Batch snapshot**: the ingest job calls `forecast_from_bundle` (same code as FastAPI) and stores the JSON per DUID | Live FastAPI on Azure: a second Python app on F1 would burn the CPU quota, and the forecast origin only moves once a day (Next Day Dispatch is daily), so live calls give nothing fresher. A local live-FastAPI mode was considered and not built |
| Ingestion runtime | **Python job in GitHub Actions** (cron, free for public repos), reusing `aemo_client` | Inside Next.js on F1: a 40 MB Intermittent_DS file would blow the 60 CPU-min/day quota. Azure Functions: more moving parts |
| DB access from Actions | Job adds its runner IP to the Postgres firewall, runs, then removes the rule (`if: always()`) | Open firewall to all IPs: simpler, weaker |
| Deploy auth | GitHub OIDC → **user-assigned managed identity** with a federated credential, Contributor on the resource group only | Publish profile: needs SCM basic auth and a long-lived secret |
| Infra | Bicep (`infra/main.bicep`) + `infra/deploy.sh`, which **the user runs** and which prompts for secrets | Portal clicks: not reproducible |
| Hosting | App Service F1 Linux, Node 22 LTS (same as local), Next.js `output: "standalone"` | B1 (~paid) removes cold starts: optional upgrade for interview week, paid from student credit |
| Delete semantics | Customers and sites are archived (`archivedAt`), units hard-deleted, every mutation audited | Hard delete everywhere: loses history |
| Concurrency | `version` column, update fails on a stale version ("someone else edited this") | Last write wins |
| Rendering (added in C0) | Cache Components **off** (create-next-app 16.4 turns it on) | On: every session read must sit in a `<Suspense>` boundary or the build fails, and an all-authenticated console has no static shell to gain |

---

## 3. Design

### 3.1 Data model (Prisma)
- `Customer`: id, name, contactEmail (`@example.com`), status, notes, `isSynthetic`
  (default true), archivedAt, version, timestamps.
- `Site`: id, customerId, name, region (`NSW1|QLD1|VIC1|SA1|TAS1`), fuel
  (`SOLAR|WIND`), registeredCapacityMw, status, archivedAt, version.
- `Unit`: duid (unique, must exist in `RegistryUnit`), siteId, maxCapacityMw,
  connectionPointId.
- `RegistryUnit`: read-only AEMO registry rows (duid, station, fuel, region, capacities,
  source file and date). Used to validate and pre-fill forms.
- `User` / session tables from Better Auth, plus `role` (`ADMIN | VIEWER`).
- `AuditLog`: actorId, action, entityType, entityId, before/after JSON, at.
- Ops tables, written only by the Python job, keyed `(duid, interval_end)` and upserted:
  `ScadaReading(mw)`, `DispatchSolution(initial_mw, total_cleared, availability, uigf,
  semidispatch_cap)`, `ForecastUsed(origin, priority)`, `ForecastRun(origin, priority,
  suppressed_aemo, suppressed_participant, poe50)`.
- `IngestRun`: source, trading day / window, started/finished, status, rows, error.
- `ForecastSnapshot`: duid, as_of, generated_at, horizon, probability, risk_level, payload JSON.

### 3.2 Pages
- `/login`.
- `/`: fleet overview. Per site: region, fuel, capacity, latest actual MW, last 24 h
  curtailment estimate (MWh), risk badge, plus a freshness strip per source.
- `/customers`, `/customers/[id]`: list with search and pagination; create/edit/archive (admin).
- `/sites`, `/sites/[id]`, `/sites/[id]/edit`: list with region/fuel/customer filters.
  The site page shows:
  - **actual (SCADA) vs UIGF vs availability vs cleared** for a chosen trading day;
  - **curtailment estimate** = Σ max(0, UIGF − TOTALCLEARED) × 5/60 over intervals with
    `SEMIDISPATCHCAP = 1`, labelled as an estimate;
  - **forecast origin used**: share of intervals per origin and suppression counts;
  - **constraint-risk card** from the snapshot, with `as_of`, `data_lag_hours` and the
    model's own limitations text.
- Units are managed on the site page (add/edit/remove DUID; zod checks format, uniqueness
  and presence in the registry).
- `/audit` (admin only), `/status` (ingest run history + freshness).
- `/api/health`: liveness, no DB. `/api/ready`: DB ping + per-source freshness, 503 with
  a JSON reason when stale.

Freshness limits (fixed now): SCADA ≤ 2 h, dispatch solution ≤ 30 h, intermittent ≤ 30 h,
forecast snapshot ≤ 30 h.

### 3.3 RBAC
| Action | ADMIN | VIEWER |
|---|---|---|
| Read customers, sites, ops data, status | ✓ | ✓ |
| Create / edit / archive customers, sites, units | ✓ | ✗ (server rejects; buttons hidden) |
| Read audit log | ✓ | ✗ |

Every check runs on the server (in each server action and page), not only in the UI.
Seeded test users live in the local `.env` / seed script. On Azure, the user sets the
admin password. A read-only viewer account for reviewers is optional (the user decides).

### 3.4 Ingestion (`src/aemo_constraint_demo/ops_ingest.py`)
- One hourly workflow, idempotent. Each source fetches only files newer than its last
  successful `IngestRun`, so daily sources no-op most hours.
- A new **streaming** multi-table reader (zip stream → csv rows → keep wanted tables and
  DUIDs) for the 40 MB Intermittent_DS files. The current `parse_multitable_zip` loads a
  whole payload into memory.
- Backfill: SCADA + dispatch 30 days, Intermittent_DS 7 days (≈ 280 MB download),
  serial and cached as the repo already does.
- After dispatch data lands: score each DUID that has a bundle and store a `ForecastSnapshot`.
- Each run emits an `ingest_run` telemetry record (source, status, rows, duration) to
  App Insights.
- `scripts/build_site_registry.py`: Registration List xlsx + DUDETAIL →
  `data/site_registry.csv` (committed, with source and date).
- New Python deps: `psycopg[binary]`, `openpyxl`.

### 3.5 Monitoring
- App Insights via OpenTelemetry (`instrumentation.ts` in the Node runtime) for
  requests, dependencies and exceptions.
- **Ingestion alert** (required): log search alert on `ingest_run`. Fires if any run
  failed, or if there was no successful SCADA run in 2 h. Notifies an email action group.
- Fallback that costs nothing: GitHub emails the repo owner when the workflow fails.
- Optional: one standard availability test on `/api/ready` from one Australian location.

### 3.6 CI/CD
- `ci.yml` (PR + push):
  - Python job: pytest. The existing 97 tests stay green; new parser/ingest tests are
    added, and DB tests run against a Postgres service container.
  - Console job: lint, typecheck, Vitest, `prisma migrate deploy`, build, Playwright.
  - Brand guard: fail if a third-party company name appears under `console/`.
- `deploy.yml` (main): OIDC login → firewall rule → `prisma migrate deploy` → zip deploy
  of the standalone build → smoke test `/api/health`.
- `ingest.yml`: hourly cron + manual dispatch.

### Layout
```
console/            Next.js app (app/, lib/, prisma/, tests/, e2e/)
infra/              main.bicep, deploy.sh
src/aemo_constraint_demo/ops_ingest.py, nemweb_stream.py
scripts/build_site_registry.py
data/site_registry.csv
docs/console-PLAN.md, docs/console-METRICS.md, docs/images/console-*.png
.github/workflows/  ci.yml, deploy.yml, ingest.yml
docker-compose.yml  local Postgres
```

---

## 4. Scope options

### Option A — full scope (recommended), ≈ 2.75 days
| Phase | Content | Days |
|---|---|---|
| C0 | Header check on real files, registry script, scaffold, docker Postgres, Prisma schema + seed | 0.25 |
| C1 | Better Auth + roles, customers/sites/units CRUD (zod + server actions), archive, versioning, audit log, Vitest | 0.75 |
| C2 | Streaming parser, `ops_ingest` + pytest, backfill, forecast snapshots, site ops page, overview, freshness, `/status` | 0.75 |
| C3 | Playwright flows (admin CRUD + audit, viewer blocked, site page), `ci.yml` | 0.25 |
| C4 | Bicep, OIDC identity, deploy + ingest workflows, App Insights, alert, latency measurement | 0.5 |
| C5 | Screenshots, README section, `console-METRICS.md`, old README line rewritten | 0.25 |

Cut order if time runs short:
1. Intermittent_DS origin/suppression view (heaviest data).
2. Entra ID.
3. Optional availability test.
4. Bicep replaced by an az CLI script.
5. Playwright down to 2 flows.

### Option B — 1-day minimum
- Read-only console: fleet overview + site page (actual vs availability/UIGF vs cleared,
  curtailment estimate, CATHROCK risk card, data-age badge).
- Data comes from a JSON snapshot that a Python export script builds from NEMWeb
  (7 days, 10 DUIDs).
- No DB, no auth, no CRUD, no alert.
- Vitest for the curtailment and freshness functions; CI build; deployed to App Service
  F1 by an az CLI script with App Insights and `/api/health`.

Honest trade-off: B shows Next.js + Azure, but leaves the brief's core open (staff managing
customers and sites with roles). Recommended only if time runs short.

### Calendar
Deadline 2026-10-26; another commitment comes first until 10-21.
- Option A: build 10-22 → 10-24, screenshots and README 10-25.
- Option B: 10-22, leaving 10-23 → 10-25 for the write-up.
- **Before 10-22, about 15 minutes of user time**: sign up for Azure for Students and
  check the allowed regions (Section 7). That risk should surface early, not on deploy day.

---

## 5. Acceptance targets (fixed before any results)
The README and `docs/console-METRICS.md` report actual values even when a target is missed.

| ID | What | Target |
|---|---|---|
| T1 | Headers | Every table/column the console uses is checked against a real file's `I,` row before its parser exists |
| T2 | Data | ≥ 10 DUIDs, ≥ 30 trading days of SCADA + dispatch, ≥ 7 of Intermittent_DS. Row counts, days, download MB recorded |
| T3 | Cross-check | SCADA `SCADAVALUE` vs dispatch `INITIALMW` (both MW at interval start): median abs diff ≤ 0.5 MW (a data-quality check, reported either way) |
| T4 | RBAC | Viewer mutation attempts are rejected by the server (unit + e2e), and every successful mutation writes exactly one audit row (tested) |
| T5 | Tests | Existing 97 pytest still pass. New pytest, Vitest and Playwright suites all green in CI. Counts recorded |
| T6 | Latency | Site page on F1, warm: p95 server time ≤ 800 ms over 50 requests. Cold start measured and reported separately |
| T7 | Deploy | Push to main → deployed and `/api/health` 200 in ≤ 10 min |
| T8 | Alert | A forced ingest failure fires the alert email once (screenshot in docs) |
| T9 | Hygiene | Brand guard green; synthetic customers labelled in UI and DB; no secrets in the repo |

---

## 6. Cost (to state before creating anything)
| Item | Expected cost |
|---|---|
| App Service F1 Linux | Free |
| PostgreSQL Flexible B1MS + 32 GB | Free for 12 months under the free services offer; billed after that |
| Log Analytics / App Insights | Free under 5 GB/month (this app will be far below) |
| Log search alert rule (1) | Small monthly charge. Microsoft's USD page did not show the figure; third-party sources quote roughly US$0.50–1.50/month at 15-min frequency. Confirmed in the Azure pricing calculator and shown to you before creation; paid from the student credit |
| Standard availability test (optional) | Per execution. Third-party rate US$0.0005 → 1 location every 15 min ≈ US$1.44/month. Confirmed before creation |
| GitHub Actions | Free (public repo) |

After the demo period, you choose: keep or delete. Delete = one command,
`az group delete -n <rg>`, which removes everything.

---

## 7. What the user does (credentials stay with you)
1. Sign up for Azure for Students (university email, no credit card).
2. Check allowed regions: Portal → Policy → Assignments → "Allowed resource deployment
   regions" → Parameters. If neither Australia East nor Australia Southeast is listed,
   stop and decide together. Options: Azure free account (credit card, US$200 for
   30 days) or a different subscription.
3. `brew install azure-cli` (ask me to run it), then `az login` yourself.
4. Run `infra/deploy.sh`. It prompts for the DB admin password, the alert email and the
   console admin password, and sets the GitHub secrets via `gh secret set`. You type
   them; I never see or enter them.

---

## 8. Risks
- **Region policy** (above): the main blocker; checked first.
- **Stack churn**: Next.js security releases arrive often (July and August 2026 had
  Server Action DoS fixes). Pin exact versions, take the newest 16.x patch on scaffold
  day, and record versions in the README.
- **OpenTelemetry + Next.js bundling** can need `serverExternalPackages`. Fallback:
  App Service's built-in Node auto-instrumentation.
- **F1 cold starts and quota**: first request after idle can take seconds, and a stopped
  app returns 403 until the UTC midnight reset. Both stated in the README. B1 for
  interview week is optional and paid.
- **Intermittent_DS size**: 40 MB zipped/day, unzipped size unknown until the first
  download. Mitigation: streaming reader, 7-day backfill only; cut first if slow.
- **Curtailment is an estimate**: UIGF − cleared under a semi-dispatch cap mixes economic
  and network curtailment. Labelled as such. Attributing it to binding network
  constraints (via this repo's DUID → GENCONID mapping) is a stretch, not a promise.
- **Model coverage**: only CATHROCK has a bundle, and its probabilities are near-saturated
  (see README Phase 5). The card shows the model's own caveats.

## 9. Guard-rails
- No git commit, push, PR or GitHub setting change without your explicit OK. Work goes
  on a new branch; see open question 1.
- No Azure resource is created without stating the cost and getting a yes. Regions:
  Australia East or Australia Southeast only.
- No third-party company name or branding inside the console. Customers are synthetic
  and labelled.
- No invented numbers. Every number in the README or metrics doc comes from a
  file or a measurement in this repo.

## 10. Open questions for you
1. PR #1 (`mcp-tools-and-forecast-parity`) is still open and `main` lacks Phases 5–6.
   Options: (a) merge PR #1 first, then branch `ops-console` from `main` (cleaner; my
   recommendation), or (b) branch `ops-console` from the PR branch.
2. Option A or B?
3. A read-only viewer login for reviewers on the deployed console: yes or no?
4. Besides README line 3, `README.md:83` and the Chinese planning notes under `doc/`
   mention the company by name in the public repo. Rewrite README:83 too? Leave `doc/`
   as is or move it out of the public repo?
5. "Decisions" section in the README: you write it. I can leave an outline of headings only.

---

## 11. Progress log

### 2026-10-19 — approved
- The user chose Option A (full scope). Questions 1 and 2 are answered. Questions 3–5
  stay open until C5.
- Before merging, all 97 pytest tests passed on the PR #1 head (`a2d407e`, 4.7 s).
- PR #1 was merged into `main` with a merge commit (`44ee888`). Its branch was kept.
- Local `ops-console` branch created from `main` at `44ee888`. Not pushed, not checked
  out yet.
- This plan file is still untracked. It goes into the first `ops-console` commit, which
  needs the user's OK.
- Next: C0 on 2026-10-22, after another deadline on 10-21. Before then the
  user signs up for Azure for Students and checks the allowed regions (Section 7).
- Later the same day the user asked to start right away, so C0 began on 2026-10-19.

### 2026-10-19 — C0 done
**T1 header check: met.** Every column the console reads was checked against the `I,`
row of a real file:

| File (downloaded to `data/cache/`) | Size | Tables found |
|---|---|---|
| `PUBLIC_DISPATCHSCADA_202610190115_…zip` (CURRENT) | 3.7 KB | `DISPATCH,UNIT_SCADA`: `SETTLEMENTDATE, DUID, SCADAVALUE, LASTCHANGED`; 517 rows |
| `PUBLIC_DISPATCHSCADA_20261017.zip` (ARCHIVE) | 1.19 MB | Zip of 288 five-minute zips; 148,894 rows, 3,456 for the 12 sites (12 × 288). Covers calendar day 00:05 → 24:00, not the 04:05 trading day |
| `PUBLIC_NEXT_DAY_INTERMITTENT_DS_20261017_…zip` | 40.6 MB zipped, **336.6 MB** unzipped, 80 s to download | `DEMAND,INTERMITTENT_DS_RUN` (1,058,845 rows), `DEMAND,INTERMITTENT_DS_PRED` (1,058,845), **`DISPATCH,INTERMITTENT_FORECAST_TRK` (59,040; 3,456 for the 12 sites)**. Streaming scan: 29 s |
| `NEM_Registration_and_Exemption_List_20261005.xlsx` | 785 KB | Sheet "PU and Scheduled Loads", 582 rows |
| MMSDM 2026-09 `DUDETAILSUMMARY` / `DUDETAIL` / `STATION` | 379 / 80 / 24 KB | Region, station ID, schedule type; capacities |

Findings:
- `INTERMITTENT_FORECAST_TRK` **is** in the Next_Day_Intermittent_DS file, so "forecast
  used" comes from AEMO directly and the priority-rule fallback is not needed.
- Observed columns differ slightly from the docs. `INTERMITTENT_DS_RUN` has no
  `AUTHORISEDBY/COMMENTS/MODEL` in the public file, and `PRED` has no `LASTCHANGED`.
  Neither column is used.
- On 2026-10-17, dispatch used a participant/vendor origin for most intervals at 7 sites;
  for 1 more, Gunnedah, it was 153 of 288. CATHROCK, KSP1 and WAMBOWF2 used
  `AWEFS_ASEFS` all day. Participant-side suppression is common: for example 2,621 of
  3,432 WOOLGSF1 submissions had `SUPPRESSED_PARTICIPANT = 1`. The file holds 116
  distinct origin codes, including `SCADA`, `FCST` and `Last Target`.
- All 12 scan DUIDs are in the registration list and `DUDETAILSUMMARY`, so all 12 are
  seeded (target T2 said ≥ 10): 5 wind, 7 solar; QLD1 6, NSW1 3, SA1 2, VIC1 1; all
  semi-scheduled. The registration list classifies CATHROCK as "Non-Scheduled*" while
  `DUDETAILSUMMARY` says SEMI-SCHEDULED; both are stored.
- NEM time is fixed UTC+10. File stamps match it (the 01:15 file was the newest at 01:20 AEST).
- One CURRENT listing request failed and succeeded on retry, so the ingest job needs retries.
- **Follow-up, outside this task:** `aemo_client.monthly_archive_url` double-encodes `#`
  as `%2523`. NEMWeb now returns 404 for that form, and the directory listing uses `%23`.
  Older cached months still work offline.

Built:
- `src/aemo_constraint_demo/site_registry.py` + `scripts/build_site_registry.py` →
  `data/site_registry.csv` (12 rows, source files named in each row);
  `tests/test_site_registry.py` (5 tests).
- `console/`: Next.js 16.4.2 (create-next-app; folder renamed because npm reserves the
  name `console`), `output: "standalone"`, Cache Components off.
- Prisma 7.10.1 with the pg adapter. Config lives in `prisma7.config.ts`, the name Prisma
  7.10 generates. Initial migration covers 13 tables.
- `prisma/seed.ts`: 12 registry units, 4 synthetic customers, 12 sites; a re-run creates
  nothing new.
- `docker-compose.yml`: Postgres 17.11 on `127.0.0.1:55460`, chosen to avoid other local
  containers.
- Vitest: `lib/csv.ts` (4 tests).
- New Python deps: `openpyxl`, `psycopg[binary]`.

Checks:
- pytest 102 passed (97 existing + 5 new).
- Console typecheck, lint, Vitest 4/4 and `next build` all pass.
- `npm audit`: 5 high findings, all in the dev-only ESLint chain (`braces` via
  `eslint-config-next`); `npm audit --omit=dev` finds 0. The suggested `--force` fix
  would downgrade `eslint-config-next` to 14.x, so it was not applied.

### 2026-10-19 — C1 done
**Auth: Better Auth 1.7.10**
- Email + password with public sign-up disabled; minimum password length 12; 8 h sessions.
- `role` is a server-owned user field (`input: false`, values `admin | viewer`).
- `nextCookies()` sets the session cookie from server actions.
- Auth tables were generated with `npx auth@1.7.10 generate` (the CLI moved from
  `@better-auth/cli` to the `auth` package) and migrated as `add_auth`.
- Staff users are seeded from `SEED_*` env values. Passwords are hashed with Better Auth's
  own `hashPassword`; for the local run they were generated into the gitignored
  `console/.env`.

**Authorization, three layers:**
1. `proxy.ts`: optimistic redirect when the session cookie is missing.
2. `lib/dal.ts`: session → `Actor`, cached per request.
3. `lib/rbac.ts`: one permission table. **Every service function calls `assertCan`**, so a
   server action reached by a direct POST is checked too.

Pages render a 403 notice; `forbidden()` is experimental in Next 16, so it is not used.

**Services** (`lib/services/*`, plain functions that take `db` and `actor`, testable
without Next):
- Customer/site create, update, archive and restore; unit assign/remove.
- `version`-checked `updateMany` gives optimistic concurrency.
- Business rules:
  - a customer cannot be archived while it has active sites;
  - archived records are read-only;
  - a DUID must exist in the AEMO registry (DB foreign key + check) and match the
    site's region and fuel;
  - a site holding a unit cannot change region.
- The audit row is written in the same transaction as the change.

**UI** (server components + `useActionState` forms; failed submits keep their values):
- `/customers`, `/customers/new|[id]|[id]/edit`
- `/sites`, `/sites/new|[id]|[id]/edit` (unit assignment on the site page)
- `/audit` (admin only; shows changed fields)
- `/` and `/status` placeholders until C2.

The UI kit is hand-rolled Tailwind (`components/ui.tsx`), with no component library.

**Tests**
- Vitest 32 passed: 4 CSV-parser tests, 13 unit tests (RBAC table, zod schemas, error
  mapping) and 15 integration tests on a separate Postgres database
  (`ops_console_test`, migrated by the global setup and truncated per test). The
  integration tests cover:
  - a viewer write is rejected and leaves no rows;
  - exactly one audit row per change;
  - a stale version is rejected and the first edit survives;
  - duplicate name;
  - archive rules;
  - DUID region, fuel, unknown and duplicate checks;
  - region-change block;
  - unit removal.
- Production-server smoke test with curl (`next start`):
  - viewer: `/audit`, `/customers/new` and `/sites/new` render the 403 notice;
  - admin: every page renders;
  - wrong password → 401; public sign-up → 400.
- Typecheck, lint and `next build` clean.

### 2026-10-19 — C2 done
**Python**
- `nemweb_stream.py`: a row-streaming reader for AEMO multi-table zips (nested zips,
  table + DUID filter).
- `ops_ingest.py`: four idempotent sources (`scada`, `dispatch`, `intermittent`,
  `forecast`).
  - Each NEMWeb file is upserted together with its `ingested_file` row in one
    transaction.
  - Every source run writes an `ingest_run` row and one structured `ingest_run` log line,
    the hook for App Insights in C4.
  - SCADA backfills from daily ARCHIVE zips, then takes only the 5-minute CURRENT files
    after the newest archived day.
  - Dispatch keeps the highest-INTERVENTION run per interval.
  - Intermittent stores the forecast dispatch used (TRK, joined to its POE50) and
    per-origin submission/suppression counts.
  - Forecast re-scores a bundle only when new dispatch data arrived; it uses
    `cathrock_lead_time_models.joblib`, which gives the whole-horizon probability plus a
    6-bucket curve.
- `scripts/ops_data_checks.py` → `artifacts/ops_data_checks.json`.
- pytest +15: streaming parser 4, ingest 11. 4 of the ingest tests are Postgres
  integration tests:
  - each file loads once and seen files are skipped;
  - one corrupt file marks the run failed while the good files stay loaded;
  - both intermittent tables are written;
  - a snapshot is saved, then skipped when no new dispatch data arrived.

**Backfill on 2026-10-19 (local, one command, 709 s, all four sources `success`)**

| Source | Files | Rows | Downloaded | Seconds |
|---|---|---|---|---|
| SCADA (30 days) | 341 | 103,968 | 35.9 MB | 168 |
| Dispatch (29 trading days) | 29 | 100,224 (= 12 × 288 × 29) | 246.9 MB | 258 |
| Intermittent (6 trading days) | 6 | 56,488 (20,736 used + 35,752 origin rows) | 245.0 MB | 222 |
| Forecast (CATHROCK) | 1 bundle | 1 snapshot | — | 57 |

**T2 data: met.**
- 12 DUIDs.
- SCADA covers 2026-09-19 00:05 → 2026-10-19 02:00 NEM.
- Dispatch covers trading days 2026-09-19 → 2026-10-17.
- Intermittent covers 2026-10-12 → 2026-10-17; this is 6 trading days, not the 7
  targeted, because the window starts 7 calendar days back and AEMO had not yet
  published the 10-18 file.

**T3 cross-check: met.** On 100,224 matched (DUID, interval) pairs, SCADA `SCADAVALUE`
and dispatch `INITIALMW` have a median absolute difference of 0.0 MW, p95 of 0.0 MW, and
100% are within 1 MW. The two sources line up interval for interval, which also confirms
the time alignment.

**Console**
- `lib/ops-math.ts`: trading-day bounds, curtailment estimate, origin categories,
  freshness; 9 Vitest tests.
- `lib/services/ops.ts`: fleet overview, site ops, freshness, ingest runs.
- Site page Operations section:
  - 7-day trading-day picker;
  - stat tiles (latest actual, energy dispatched, curtailment estimate, self-forecast
    share);
  - Recharts chart of SCADA vs UIGF vs dispatch target, with the semi-dispatch cap shaded;
  - constraint-risk card (snapshot, 6-hour curve, the model's own limitations);
  - forecast-used and suppression tables.
- Chart colours are categorical slots 1–3 of the dataviz reference palette; the
  validator passes on all pairs in light mode. Aqua is under 3:1 contrast, so the chart
  also has a legend and an interval table.
- Overview fleet table with a freshness strip.
- `/status`: freshness, rows loaded, ingest runs.
- `/api/health` (no DB) and `/api/ready` (DB + per-source freshness; 503 with reasons).

What the real data shows on trading day 2026-10-17:
- Cathedral Rocks: the semi-dispatch cap held the dispatch target near 0 from about
  08:00 to 17:00 while UIGF was about 10–20 MW. Estimate 181.4 MWh curtailed in 120 capped
  intervals.
- Self-forecast share of intervals: Bulgana, Goyder South and Mount Emerald 100%;
  Cathedral Rocks, Kidston and Wambo 0% (AWEFS/ASEFS).
- Woolooga: 2,621 of 3,432 of its own submissions were participant-suppressed.

**Local latency baseline** (not T6, which is measured on Azure F1). Standalone build on
an M4 Pro, Postgres in Docker, real data; 50 warm requests after 5 warm-ups:

| Page | p50 | p95 |
|---|---|---|
| Site page (CATHROCK) | 143 ms | 233 ms |
| Overview | 247 ms | 348 ms |
| `/api/ready` | 18 ms | 34 ms |

### 2026-10-19 — C3 done
- Playwright 1.64.1: 8 specs, run against the **standalone** build on a separate
  `ops_console_e2e` database. Covered flows:
  - anonymous redirect;
  - generic wrong-password message;
  - viewer sees no write controls and gets 403 on admin URLs;
  - sign-out;
  - admin creates a customer (bad email first), edits it, adds a site, moves GSWF1A
    between sites, and checks the audit order;
  - archive refusal;
  - overview and site ops render fixture data;
  - `/api/health` and `/api/ready`.

  All 8 pass.
- **Prisma guard.** Prisma 7.10 refuses `prisma migrate reset` when it detects an AI
  agent and asks for the user's explicit consent. The guard was not bypassed. Instead,
  `e2e/prepare.ts` runs `migrate deploy`, truncates the app tables and re-seeds. It refuses
  any database whose name does not end in `_e2e`.
- **Bug found by E2E:** after the last DUID was assigned, the assign form was swapped for
  the "none left" text, so the success message vanished. The form now stays mounted.
- **Visual review of real-data screenshots** (`console/scripts/capture-screenshots.ts` →
  `docs/images/console-*.png`). Fixed: clipped Y-axis labels, and the ingest-run table
  overflowing on `/status`.
- **`.github/workflows/ci.yml`** (actions checkout/setup-node/setup-python/upload-artifact
  v7). Three jobs, each with a Postgres 17 service container:
  - `python`: applies the Prisma migration SQL with psql, then pytest.
  - `console`: lint → typecheck → Vitest → build → Playwright → `npm audit --omit=dev`.
    Secrets for the run come from `openssl` and are masked.
  - `brand-guard`: whole-word grep under `console/`.

  Not run on GitHub yet: nothing is pushed.
- **Local totals:** pytest 117 passed (97 original + 20 new), Vitest 41 passed,
  Playwright 8 passed, brand guard clean. Raw migration SQL applied with psql to a fresh
  database builds all 16 tables.

### 2026-10-19 — Azure preflight (read-only checks, nothing created)
- Azure CLI 2.91.0 and Bicep 0.48.1 installed. The user ran `az login` themselves.
- Subscription: Azure for Students (`quotaId AzureForStudents_2018-01-01`), spending limit **On**.
  Tenant: the user's university directory. The user is **Owner** on the subscription, which
  is enough for a user-assigned managed identity and a resource-group role assignment.
- **Region blocker.** The subscription's "Allowed resource deployment regions" policy
  allows only `newzealandnorth`, `koreacentral`, `indonesiacentral`, `eastasia` and
  `japaneast`. Australia East and Australia Southeast are not on the list.
- In all five allowed regions, the provider metadata lists App Service (serverFarms and
  sites), PostgreSQL Flexible Server, Log Analytics, App Insights web tests and
  user-assigned identities.
- `az appservice list-locations --sku F1 --linux-workers-enabled` returns all five.
- In New Zealand North, PostgreSQL Flexible lists `Standard_B1ms` and versions 13–18, and
  scheduled query rules (log alerts) are supported.
- Resource providers (Web, DBforPostgreSQL, Insights, OperationalInsights,
  ManagedIdentity, AlertsManagement) are not registered yet. Registering them is part of C4.
- Decision needed from the user: deploy in New Zealand North under this subscription, or
  use a different subscription that allows Australia East.

### 2026-10-19 — C4 prepared (nothing created yet)
The user agreed to relax the region rule: deploy to **New Zealand North** under the Azure
for Students subscription, and say why in the README. Design changes against Section 2,
each to remove a stored secret:

| Topic | Choice | Why |
|---|---|---|
| Database auth | PostgreSQL with **Microsoft Entra auth only**, password login disabled | No database password exists to store, rotate or leak |
| App → database | The web app's **system-assigned identity**; `lib/prisma.ts` passes pg a token function (`lib/entra-token.ts`, cached, refreshed 5 min early) | Least privilege: `infra/sql/app-role-*.sql` grants DML only, no DDL |
| CI → Azure / database | One user-assigned identity, federated with `repo:…:environment:production`. Contributor on the resource group only. Entra admin on the database for migrations and ingest | No Azure secret in GitHub |
| App Insights in CI | Connection string read at run time with `az resource show` | No GitHub secret for it |
| GitHub secrets left | `SEED_ADMIN_PASSWORD` (+ optional `SEED_VIEWER_PASSWORD`), typed by the user in `infra/deploy.sh` | Console sign-in passwords are the user's to choose |
| Ingest telemetry | `ops_ingest` posts each source run as an App Insights custom event `ingest_run` (track API, no new dependency) | The log alert queries `customEvents` |
| Alert schedule | Log alert every 1 h over 2 h: fires on any failed run or no successful SCADA run. **Created disabled** until the hourly schedule runs from `main` (GitHub only schedules default-branch workflows) | Avoids alert emails during the deploy-only period |

Files:
- `infra/main.bicep` (subscription scope), `infra/resources.bicep`, `infra/postgres-admin.bicep`;
- `infra/deploy.sh` (the user runs it; `--what-if` previews);
- `.github/workflows/deploy.yml` (build → migrate/grant/seed → zip deploy → smoke test →
  ingest) and `.github/workflows/ingest.yml` (hourly, plus `workflow_call` from deploy);
- `requirements-ingest.txt` (6 pinned packages);
- `console/instrumentation.ts` (Azure Monitor OpenTelemetry, only when the connection
  string is set).

Checks:
- `az bicep build`: 0 warnings.
- `az deployment sub what-if` in newzealandnorth (read-only, placeholder alert email)
  lists 16 creates. The deploy identity's database-admin entry shows as "Unsupported"
  because its object ID only exists at creation time.
- No policy denial.
- Vitest 46 (+5 token tests), Playwright 8, pytest 119 (+2 telemetry tests).
- Standalone bundle: 77 MB.

**Prices (Azure Retail Prices API, newzealandnorth, USD, 2026-10-19):**

| Item | Price | Expected cost here |
|---|---|---|
| App Service F1 Linux | $0 | $0 |
| PostgreSQL Flexible B1MS compute | $0.0273/h | $0 under the 750 h/month free offer if it applies; otherwise ≈ $19.93/month |
| PostgreSQL storage, 32 GB | $0.1449/GB-month | $0 under the free offer; otherwise ≈ $4.64/month |
| Log Analytics ingestion | first 5 GB/month free, then $3.507/GB | $0 (daily cap 0.1 GB) |
| Log search alert, 1 rule | $0.50/month (15-minute-or-slower meter) | ≤ $0.50/month once enabled |
| Identity, federation, role assignment, firewall rules, action group email | — | $0 |

Spending limit is On, so charges stop at the student credit.

Portal check by the user (2026-10-19, "Free services", 12-month amounts): the offer lists
Azure Database for PostgreSQL at 750 hours of Flexible Server Burstable B1MS, 32 GB
storage and 32 GB backup storage. The page does not name regions. `resources.bicep` stays
inside that allowance: B1MS, 32 GB storage with auto-grow off, 7-day local backups.
Expected cost is therefore ≈ $0/month, plus ≤ $0.50 once the alert is enabled. This is
confirmed against Cost Management one to two days after creation.
