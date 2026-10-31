# Sandbox simulator

The sandbox is the ops console running on simulated solar and wind farms instead of real
ones. It has two jobs:

- **Testing.** Real NEMWeb data shows a long network constraint, a farm whose SCADA goes
  quiet, or a suppressed self-forecast only when the market happens to produce one. The
  simulator produces them on demand, with known answers, so the console can be checked
  against each of them in CI.
- **Training.** New staff can learn the console on farms that look and behave like real
  ones, work through tasks on a Training page, and check their answers, without touching a
  customer's data.

The sandbox is the same console build with `CONSOLE_SANDBOX=1`, pointed at its own
database. Sandbox mode adds two things: a banner saying the data is simulated, and the
Training page. The simulator writes the ops tables with the same upserts as the real ingest
job (`ops_ingest.UPSERTS`), so the console reads simulated and real data through one code
path.

```mermaid
flowchart LR
  Real[(12 real DUIDs<br/>in the ops database)] -->|scripts/sim_calibrate.py| Cal[artifacts/sim_calibration.json]
  World[data/sim/training.toml<br/>farms + scenarios] --> Engine
  Cal --> Engine[sim_engine<br/>farm physics]
  Engine --> Scen[sim_scenarios<br/>anchor, NEMWeb cadence,<br/>training answers]
  Scen -->|sim_sandbox load / tick<br/>refuses any DB not named *_sandbox / *_e2e| DB[(sandbox Postgres<br/>same schema)]
  DB --> App[same console build<br/>CONSOLE_SANDBOX=1]
  Cal -->|scripts/sim_realism.py| Check[realism report]
```

## What a simulated farm does

Each farm steps through 5-minute dispatch intervals. Every quantity is drawn from
`artifacts/sim_calibration.json`, which `scripts/sim_calibrate.py` fits to the console's
12 real DUIDs (7 solar, 5 wind; trading days 2026-09-19 to 2026-10-17, 100,224 dispatch
intervals). Everything is a share of capacity, so a farm of any size can use it.

| Step | Model | Calibrated from |
|---|---|---|
| Weather signal | Wind: a Gaussian-copula AR(1) on the real capacity-factor distribution, with a per-hour shift (the real farms blow harder at night) and steps drawn from a two-normal mixture (UIGF mostly creeps and sometimes jumps). Solar: a clear-sky envelope per 5-minute slot times a clear-sky index, whose distribution depends on the day's regime (clear, partly cloudy, overcast; 45% / 32% / 23% of real farm-days). | UIGF: its distribution, 1-hour autocorrelation, median and 90th-percentile 5-minute step, mean by hour |
| Forecasts | The participant's self-forecast is the signal. AEMO's AWEFS/ASEFS forecast sits a short-lived gap away from it. | The gap between the two POE50s where a farm had both |
| Which forecast dispatch used | Runs of self-forecast and AEMO with the real mean lengths; a rare fallback to SCADA; AEMO whenever AEMO suppresses the self-forecasts. Submissions per interval and the share the participant suppresses. | `INTERMITTENT_FORECAST_TRK`, `INTERMITTENT_DS_RUN` |
| Power on site | The signal minus the forecast error. | POE50 against the next SCADA reading, uncapped intervals only |
| Semi-dispatch cap | Episodes start at the real rate for each hour of the day (per uncapped interval). Their length is drawn from the 1,793 real episodes and their limit from the real distribution. | `SEMIDISPATCHCAP` runs in Next Day Dispatch |
| Target and output | Target = min(UIGF, cap limit). Output follows the target, cannot exceed the power on site, and the next SCADA reading reports it (SCADA labelled *t* is the output at the start of the interval ending *t*, as in the real data). | |

Generation is sequential, with one random stream per farm and process, so extending a run
to a later time reproduces the earlier intervals exactly. That lets `sim_sandbox tick`
publish new intervals as time passes without rewriting the past.

## When the console sees each row

Data is published on NEMWeb's real cadence:

- SCADA is published every 5 minutes.
- Next Day Dispatch for trading day D appears 12 minutes after D ends (D+1 04:12).
- Next Day Intermittent DS appears 20 minutes after D ends (D+1 04:20).

The two lags were read from the NEMWeb `CURRENT` directory listings on 2026-10-31. A world
is loaded at an *anchor* time, normally now. Scenario events are placed relative to the
*reference day*: the latest trading day both Next Day files would already be out for at the
anchor. Live-edge events are placed in hours before the anchor instead.

## The training world

`data/sim/training.toml` has five farms (three self-forecasting) and four scenarios. Each
scenario has a training task whose answer the simulator reads from the rows it published.
So the answer is always what the console should show, and a CI test checks that the console
agrees.

| Scenario | What is scripted | Where it shows in the console | Task |
|---|---|---|---|
| Network constraint | A windy day; Sim Wind Farm A held at 5% of capacity from 09:00 for ten hours | Overview curtailment column; shaded capped intervals on the site page | Which site had the largest curtailment estimate, and about how many MWh (±10%)? |
| SCADA outage | Sim Solar Farm B's SCADA stops three hours before the anchor | A Stale badge on the farm in the overview and on its site page | Which site, and the time of its last reading (±5 min)? |
| Self-forecast suppressed | On a clear day AEMO suppresses every Sim Solar Farm C self-forecast from 07:00 to 16:00 | "Which forecast dispatch used" shows AWEFS/ASEFS; "suppressed by AEMO" counts | Which site, and what did dispatch use instead? |
| Ingest failure | The latest hourly SCADA load fails on a truncated file; earlier loads succeeded | The SCADA freshness chip stays fresh but flags the failed run; the Data status page shows the error | Which source? |

The Training page sends only prompts and fields to the browser. Answers stay in
`sim_run.manifest` and are checked by a server action. "Show the answer" reveals the answer
and an explanation of where to look.

## What the simulation found in the console

The SCADA-outage scenario showed a gap in the console. The data-freshness strip uses the
newest reading across all sites. While the other farms kept reporting, a farm silent for
three hours turned nothing amber. The overview kept showing that farm's last reading as its
current output, with no time beside it.

The console now applies the same 2-hour SCADA limit per site (`siteScadaFreshness` in
`console/lib/ops-math.ts`). It shows a Stale badge in the overview and a warning on the site
page, and a site with several units is only as fresh as its stalest unit. The check runs on
real data as well, not only in the sandbox.

## Realism

`scripts/sim_realism.py` simulates a fleet with the real mix over the same 29 trading days,
pooling five seeds, and compares it with the real fleet check by check. All 16 checks pass
(`artifacts/sim_realism.json`). A pytest test reruns the same comparison, so a change to the
engine that breaks realism fails CI.

![Simulated against real: mean UIGF by hour, UIGF quantiles, 5-minute ramps, cap and curtailment shares](images/sim-realism.png)

| Check (share of capacity unless stated) | Solar | Wind | Tolerance |
|---|---|---|---|
| UIGF distribution, largest gap between 5th-95th percentiles | 0.030 | 0.009 | 0.08 |
| Mean UIGF by hour, RMSE | 0.030 | 0.011 | 0.05 |
| 5-minute UIGF change: median, gap | 0.001 | 0.004 | 0.01 |
| 5-minute UIGF change: 90th percentile, simulated / real | 0.98 | 1.25 | 0.6-1.6 |
| Share of intervals capped, gap | 0.032 | 0.001 | 0.08 |
| Share of UIGF energy curtailed, gap | 0.035 | 0.021 | 0.08 |
| Output vs target, 90th percentile, gap | 0.015 | 0.020 | 0.03 |
| Share of intervals on the self-forecast, gap | 0.001 | 0.001 | 0.10 |

The first version failed 4 of these checks, for three reasons:

- **Ramps 2.7 times too large.** The weather process was fitted to UIGF, which already
  contains forecast noise, and then a forecast error was added on top.
- **Wind capped 18 points too often.** Episode lengths came from a 21-point quantile table,
  whose linear top bin (up to 1,164 intervals) made multi-day caps common. The start rate
  was also counted per interval instead of per uncapped interval.
- **Solar ramps 1.6-1.9 times too large.** A Gaussian AR(1) fitted to the lag-1
  autocorrelation took every 5-minute step from the occasional jumps.

The fixes are the model in the table above:

- the forecast is the smooth signal, and the power on site misses it;
- every real episode length is kept;
- the start rate is counted per uncapped interval;
- mixture steps are used for wind;
- solar's persistence is chosen by simulation so the 90th-percentile ramp matches.

## Limitations

- The model is calibrated on 29 days of spring 2026. The solar envelope is September-October's,
  and so is the high curtailment of that season (about 30% of intervals capped).
- Farms are independent. Real neighbouring farms share weather and network constraints.
- There are no prices or bids. Economic curtailment is folded into the cap episodes, as it
  is in the console's own estimate.
- One envelope serves all solar farms, whatever their region. There are no intervention runs
  and no constraint-risk model for simulated farms (their risk card says so).
- In the sandbox, SCADA rows appear every 5 minutes, while the real job loads them hourly.
- Only the latest POE50 per origin and interval is simulated, not the full forecast
  trajectory.

## Run it

From the repository root, with the local Postgres from `docker-compose.yml`:

```bash
docker compose exec postgres createdb -U ops ops_console_sandbox
(cd console && DATABASE_URL=postgresql://ops:ops_local_only@127.0.0.1:55460/ops_console_sandbox npx prisma migrate deploy)
(cd console && DATABASE_URL=postgresql://ops:ops_local_only@127.0.0.1:55460/ops_console_sandbox SEED_SCOPE=staff npx prisma db seed)
export SANDBOX_DATABASE_URL=postgresql://ops:ops_local_only@127.0.0.1:55460/ops_console_sandbox
python -m src.aemo_constraint_demo.sim_sandbox load --world training   # anchored at now
python -m src.aemo_constraint_demo.sim_sandbox run                       # hourly ticks, like the ingest job
(cd console && DATABASE_URL=$SANDBOX_DATABASE_URL CONSOLE_SANDBOX=1 npm run dev)
```

- `python -m src.aemo_constraint_demo.sim_sandbox preview --anchor 2026-11-03T12:00` prints a
  world's manifest and answers without a database.
- Refit and recheck: `DATABASE_URL=... python scripts/sim_calibrate.py`, then
  `python scripts/sim_realism.py`.
- Tests:
  - `tests/test_sim.py`: physics, determinism, cadence, scenarios, realism, and the latch.
    The load and tick tests also need `SANDBOX_TEST_DATABASE_URL`.
  - `console/tests/training.test.ts`: answer checking and what reaches the browser.
  - `console/e2e/sandbox/`: every scenario in the browser, and the training answer against
    the console's own estimate.
