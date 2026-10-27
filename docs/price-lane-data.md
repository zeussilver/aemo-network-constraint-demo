# Price lane: the data layer

This lane asks a different question from the constraint model in this repository: how will a
region's reference price (RRP) behave over the next few hours, and how wide is the
uncertainty around it? Everything below is the data that question needs, loaded once into
gzipped CSV files under `data/price/` so the modelling and backtesting code never has to
touch NEMWeb again.

`src/aemo_constraint_demo/price_ingest.py` loads it; `scripts/price_data_checks.py` checks
what landed.

```bash
python -m src.aemo_constraint_demo.price_ingest --from 2025-10 --to 2026-09 \
    --raw-regions NSW1,SA1
python scripts/price_data_checks.py --out artifacts/price_data_checks.json
```

## Sources, and why each one

| Source | AEMO table | What it gives | Output |
|---|---|---|---|
| `actuals` | `DISPATCH,PRICE` | 5-minute RRP per region — the target | `actual_price_<YYYY-MM>.csv.gz` |
| `demand` | `DISPATCH,REGIONSUM` | demand, available generation, semi-scheduled solar and wind — the inputs that define a market regime | `region_demand_<YYYY-MM>.csv.gz` |
| `predispatch` | `PREDISPATCH,REGION_PRICES` | AEMO's own published forecast price — the benchmark | `predispatch_price_<YYYY-MM>.csv.gz` |
| `sensitivities` | `PREDISPATCH,PRICESENSITIVITIES` | 43 scenario prices per region per period, out to ~39 hours — AEMO's own published view of price uncertainty | `sensitivity_summary_<week>.csv.gz` (+ `sensitivity_scenarios_<week>.csv.gz`) |

The first three come from the monthly MMSDM archive, which is small (about 2–6 MB a month
per table). The sensitivities come from the weekly
`Reports/Archive/Predispatch_Sensitivities` zips instead, because **the monthly archive
keeps only period 1 of each predispatch run**: 7,440 rows a month rather than the ~400,000
the weekly files hold. A single-horizon file cannot answer a question about the forecast
curve, so the heavier source is the right one. The monthly `PREDISPATCH,REGION_PRICES` file
is still loaded, for the base-scenario check below.

## Decisions worth knowing when reading the files

**Intervention runs.** When AEMO intervenes, the interval is solved twice. Prices are taken
from the pricing run (`INTERVENTION = 0`) and `REGIONSUM` quantities from the physical run
(`INTERVENTION = 1`) when both exist — the same rule `ops_ingest.py` uses for dispatch
targets. The manifest records how many rows this dropped.

**Publication time, so a backtest cannot cheat.** A predispatch row says which interval it
is about, not when it was published. The publication timestamp in each run's file name is
kept as `published`, and `horizon_min` is measured from it rather than from the trading
day. A backtest can then select forecasts by the time they actually existed.

**Summary before raw.** Each sensitivity row carries 43 scenario prices. Every row is
summarised in place — quantiles, spread, the share of scenarios that are negative or above
$300/MWh — because that summary is what a probabilistic model consumes. The raw 43 columns
are kept only for the regions named in `--raw-regions` (NSW1 and SA1 in the backfill: the
largest region and the most renewable-heavy one), which keeps a year of data to a few
hundred MB instead of over a gigabyte.

**Resumable.** An output file is written to `.part` first and moved into place only when
complete, and a finished file is skipped on the next run. The backfill can be interrupted
and restarted. Weekly archives are deleted after parsing unless `--keep-archives` is given.

**One manifest.** `data/price/manifest.json` records, per output file, where it came from,
how many rows it holds, the span it covers and when it was written.

## Which scenario is the base case, and where the forecast sits among them

AEMO publishes `RRPEEP1`..`RRPEEP43` without saying which column is the unchanged case. The
monthly archive keeps the published forecast price for period 1 of every run, so each
scenario column can be compared against it for the same run, region and interval (the
intervals were checked to line up, so this is a like-for-like comparison).

`RRPEEP37` is the answer, with a caveat. It is the best-matching column in **every one of
the twelve full months**, reproducing the published forecast price exactly on 67–91% of
runs depending on the month. (The thirteenth, partial month in the window holds 18 compared
rows, too few to read anything into.) The misses are not a different column taking over — they are runs
where no column matches at all (3–8% a month) plus ties, where several scenarios return the
same price and one of them happens to be it. So `RRPEEP37` can be treated as the base
scenario, but the sensitivity set is solved separately from the published predispatch run
and does not always agree with it, so a model should anchor on the published forecast and
read the scenarios as a shape around it rather than assume the base column is the forecast.

The shape is not symmetric. The published forecast price falls inside the scenario range on
**every** row compared, but its median position in that range is the **33rd percentile** —
most scenarios sit above AEMO's own forecast, which is what a set dominated by
demand-increase cases should look like. A prediction interval built by centring the scenario
spread on the point forecast would therefore be biased high.

## What landed

12 months, 2025-10-01 to 2026-10-01 NEM time. Numbers from
`artifacts/price_data_checks.json`.

| | |
|---|---|
| Actual prices | 525,600 rows, 12 files — 105,120 five-minute intervals for each of the five regions, **0 missing and 0 duplicate** |
| Region demand | 525,600 rows, same span, every demand and semi-scheduled column populated |
| Published forecast price | 87,600 rows (period 1 of each run) |
| Sensitivities | 4,848,480 rows over 52 weekly files: 17,472 runs across 365 days, a median of 48 runs a day, and 43 scenarios on **every** row |
| Forecast horizon | up to 2,363 minutes (39.4 hours) ahead |
| Scenario spread | p90 − p10 median $27.08/MWh, p95 $155.06/MWh |

Prices by region, over the year:

| Region | Median RRP | Negative | Above $300 | Max |
|---|---|---|---|---|
| NSW1 | $71.10 | 9.6% | 0.27% | $20,300 |
| QLD1 | $66.58 | 14.0% | 0.10% | $19,728 |
| SA1 | $51.00 | 29.5% | 1.41% | $20,300 |
| TAS1 | $79.24 | 4.8% | 0.15% | $23,200 |
| VIC1 | $37.18 | 25.9% | 0.20% | $19,070 |

Those columns are the case for modelling regimes rather than one average market: SA1 and
VIC1 spend a quarter to a third of the year at negative prices while TAS1 spends 4.8%, and
SA1 is above $300/MWh fourteen times as often as QLD1.

**One day where publication time matters.** 50 rows of the 4.8 million — all on
2026-04-26, all from one late run — describe an interval that had already passed when the
run was published. They are the only negative `horizon_min` values in the year. A backtest
keyed on the trading day rather than on publication time would have used them without
noticing; keyed on `published`, they drop out.
