#!/usr/bin/env python
"""Fit the sandbox simulator's parameters to the console's real NEMWeb data.

Reads the ops tables (12 real solar and wind DUIDs, about 30 days of Next Day Dispatch and
7 days of intermittent forecast data) and writes the numbers ``sim_engine`` draws from,
plus the reference statistics ``scripts/sim_realism.py`` compares a simulated fleet
against. Everything is normalised by registered capacity, so a simulated farm of any size
can use it.

- Solar: a clear-sky envelope per 5-minute slot of the day, and the clear-sky index (UIGF
  over the envelope) split into clear, partly cloudy and overcast days, each with its own
  distribution and a Gaussian-copula AR(1) for how it moves within the day.
- Wind: the capacity-factor distribution, through the same kind of copula AR(1).
- Semi-dispatch cap episodes: start rate per hour of day, duration, and the limit as a share
  of capacity.
- Tracking: actual output (the next SCADA reading) minus the dispatch target.
- Forecasts: self-forecast vs AWEFS/ASEFS errors against what the farm then produced, how
  long dispatch stays on one source, submissions per interval and how many the participant
  suppresses.

    DATABASE_URL=... python scripts/sim_calibrate.py --out artifacts/sim_calibration.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

INTERVAL = pd.Timedelta(minutes=5)
NEM_OFFSET = pd.Timedelta(hours=10)
QUANTILE_POINTS = np.linspace(0, 1, 21)
# Next Day Dispatch / Intermittent DS for trading day D appear on NEMWeb shortly after the
# day ends at D+1 04:00. Read from the CURRENT directory listings on 2026-10-31 (04:11-04:12
# and 04:20-04:21 on three consecutive days).
PUBLICATION_MINUTES_AFTER_DAY_END = {"dispatch": 12, "intermittent": 20}
SOLAR_REGIMES = (("overcast", 0.0, 0.4), ("partly_cloudy", 0.4, 0.8), ("clear", 0.8, 9.9))


def load(database_url: str) -> dict[str, pd.DataFrame]:
    queries = {
        "units": "SELECT duid, fuel::text AS fuel, region_id::text AS region, registered_capacity_mw AS cap FROM registry_unit",
        "dispatch": "SELECT duid, settlement_date AS t, total_cleared, uigf, semidispatch_cap FROM dispatch_solution",
        "scada": "SELECT duid, settlement_date AS t, mw FROM scada_reading",
        "used": "SELECT duid, settlement_date AS t, origin, forecast_priority FROM forecast_used",
        "origins": (
            "SELECT duid, settlement_date AS t, origin, submissions, suppressed_aemo, suppressed_participant, "
            "max_priority, latest_poe50 FROM forecast_origin_interval"
        ),
    }
    with psycopg.connect(database_url) as conn:
        frames = {}
        for name, sql in queries.items():
            with conn.cursor() as cur:
                cur.execute(sql)
                frames[name] = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    for name in ("dispatch", "scada", "used", "origins"):
        frames[name]["t"] = pd.to_datetime(frames[name]["t"], utc=True)
    return frames


def quantiles(values) -> list[float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return [round(float(v), 5) for v in np.quantile(values, QUANTILE_POINTS)]


def lag1(series: pd.Series) -> float:
    return float(series.autocorr(lag=1))


def normal_scores(values: pd.Series) -> pd.Series:
    ranks = values.rank(method="average")
    inv = NormalDist().inv_cdf
    return (ranks / (len(values) + 1)).map(inv)


def nem_slot(t: pd.Series) -> pd.Series:
    """5-minute slot of the NEM clock day the interval ends in (00:00 -> 0, 23:55 -> 287)."""
    local = t + NEM_OFFSET
    return (local.dt.hour * 12 + local.dt.minute // 5).astype(int)


def nem_date(t: pd.Series) -> pd.Series:
    return (t + NEM_OFFSET).dt.date


def runs(flags: pd.Series) -> pd.DataFrame:
    """Runs of consecutive equal values in a 5-minute series: value, start time, length."""
    change = flags.ne(flags.shift()).cumsum()
    grouped = flags.groupby(change)
    return pd.DataFrame({"value": grouped.first(), "start": grouped.apply(lambda s: s.index[0]), "length": grouped.size()})


def prepare(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per (DUID, interval): dispatch, the next SCADA reading and the fuel."""
    units = frames["units"].set_index("duid")
    dispatch = frames["dispatch"].merge(frames["units"][["duid", "fuel", "cap"]], on="duid")
    scada = frames["scada"].copy()
    # SCADA at settlement t is the MW at the start of the interval ending t, i.e. the output
    # at t - 5 min; the output at the end of interval t is the reading labelled t + 5 min.
    scada["t"] = scada["t"] - INTERVAL
    dispatch = dispatch.merge(scada.rename(columns={"mw": "actual"}), on=["duid", "t"], how="left")
    for column in ("total_cleared", "uigf", "actual"):
        dispatch[column + "_n"] = dispatch[column] / dispatch["cap"]
    dispatch["slot"] = nem_slot(dispatch["t"])
    dispatch["date"] = nem_date(dispatch["t"])
    assert set(dispatch["duid"]) <= set(units.index)
    return dispatch.sort_values(["duid", "t"]).reset_index(drop=True)


def solar_parameters(df: pd.DataFrame) -> tuple[dict, dict]:
    solar = df[df["fuel"] == "SOLAR"]
    envelopes = []
    for _, unit in solar.groupby("duid"):
        by_slot = unit.groupby("slot")["uigf_n"].quantile(0.95).reindex(range(288), fill_value=0.0)
        # Smooth over +-2 slots (25 minutes) with a mean, which keeps sunrise and sunset where
        # they are (a rolling max would move both outwards).
        envelopes.append(by_slot.rolling(5, center=True, min_periods=1).mean().clip(0, 1))
    envelope = pd.concat(envelopes, axis=1).median(axis=1)

    solar = solar.assign(envelope=solar["slot"].map(envelope))
    day = solar[solar["envelope"] > 0.15].copy()
    day["kt"] = (day["uigf_n"] / day["envelope"]).clip(0, 1.2)
    daily = day.groupby(["duid", "date"])["kt"].agg(["mean", "size"])
    daily = daily[daily["size"] >= 60]["mean"]  # whole daylight days only

    # Per regime, the clear-sky index through a copula: partly cloudy days flip between
    # near-clear and shaded, which a Gaussian around the day's mean cannot reproduce. How
    # fast it moves is tuned later by simulation (tune_solar_persistence).
    regimes = {}
    for name, low, high in SOLAR_REGIMES:
        members = daily[(daily >= low) & (daily < high)]
        kt = day.set_index(["duid", "date"]).loc[members.index, "kt"]
        regimes[name] = {"share_of_days": round(len(members) / len(daily), 4), "kt_quantiles": quantiles(kt)}
    same_regime_next_day = []
    for _, unit_days in daily.groupby(level="duid"):
        labels = pd.cut(unit_days.values, [low for _, low, _ in SOLAR_REGIMES] + [9.9], labels=False, right=False)
        same_regime_next_day.extend(labels[1:] == labels[:-1])
    params = {
        "envelope": [round(float(v), 4) for v in envelope],
        "regimes": regimes,
        "regime_persistence": round(float(np.mean(same_regime_next_day)), 4),
    }
    reference = {"days": int(len(daily)), "daily_kt_quantiles": quantiles(daily)}
    return params, reference


def _mixture_abs_quantile(q: float, small: float, big: float, share: float) -> float:
    """``q``-quantile of |e| when e is N(0, small^2) w.p. 1 - share, else N(0, big^2)."""
    cdf = lambda x: (1 - share) * (2 * NormalDist(0, small).cdf(x) - 1) + share * (2 * NormalDist(0, big).cdf(x) - 1)  # noqa: E731
    low, high = 0.0, 10 * big
    for _ in range(60):
        middle = (low + high) / 2
        low, high = (middle, high) if cdf(middle) < q else (low, middle)
    return (low + high) / 2


def fit_copula_ar(series: list[pd.Series]) -> dict:
    """AR(1) for normal scores whose innovations are a two-normal mixture.

    Real UIGF moves in small steps most of the time and jumps now and then (a forecast
    source switch, a gust front, a cloud edge). One Gaussian AR(1) cannot do both: fitted to
    the lag-1 autocorrelation it makes every step about three times too large. So the
    persistence comes from the 1-hour autocorrelation, the small steps from the median
    5-minute change, and the jumps take the rest of the variance, with their share set to
    match the 90th-percentile change.
    """
    hourly = [s.autocorr(12) for s in series if len(s) > 24]
    phi = min(float(np.nanmedian(hourly)), 0.9999) ** (1 / 12)
    step = pd.concat([s.diff().dropna() for s in series]).abs()
    total_var = 1 - phi**2
    small = min(float(step.quantile(0.5)) / 0.6745, 0.95 * total_var**0.5)
    target_p90 = float(step.quantile(0.9))
    best = None
    for share in np.linspace(0.01, 0.5, 50):
        big_var = (total_var - (1 - share) * small**2) / share
        if big_var <= small**2:
            continue
        miss = abs(_mixture_abs_quantile(0.9, small, big_var**0.5, share) - target_p90)
        if best is None or miss < best[0]:
            best = (miss, share, big_var**0.5)
    _, share, big = best
    return {
        "phi": round(phi, 5),
        "innovation": {"small_sd": round(small, 5), "big_sd": round(float(big), 5), "big_share": round(float(share), 4)},
    }


def tune_solar_persistence(calibration: dict, grid=(0.95, 0.955, 0.96, 0.965, 0.97, 0.975, 0.98, 0.985), seeds=(1, 2, 3)) -> float:
    """5-minute persistence of the clear-sky index's normal score, chosen by simulation.

    Measured inside each day, the score's 1-hour autocorrelation implies about 0.92 per
    5 minutes, and a fleet simulated with that ramps 1.6-1.9 times harder than the real one
    at the 90th percentile (the measurement mixes cloud edges with the sun's own curve).
    So this picks, from ``grid``, the value whose simulated 90th-percentile ramp is closest
    to the real one, over a few seeds.
    """
    from scripts.sim_realism import simulate_fleet  # imports this module, so import late

    from src.aemo_constraint_demo.sim_engine import Calibration

    real = calibration["reference"]["SOLAR"]["ramp_abs_quantiles"][18]
    best = None
    for phi in grid:
        trial = {**calibration, "solar": {**calibration["solar"], "phi": phi}}
        ratios = []
        for seed in seeds:
            sim, _ = simulate_fleet(Calibration(trial), seed, fuels=("SOLAR",))
            ratios.append(reference_statistics(sim)["SOLAR"]["ramp_abs_quantiles"][18] / real)
        miss = abs(np.log(np.mean(ratios)))
        if best is None or miss < best[0]:
            best = (miss, phi)
    return best[1]


def wind_parameters(df: pd.DataFrame) -> dict:
    """Copula AR(1) for wind, plus a mean shift of the normal score per NEM hour: the real
    farms blow harder at night than at midday."""
    wind = df[df["fuel"] == "WIND"]
    scores = [normal_scores(unit.set_index("t")["uigf_n"]) for _, unit in wind.groupby("duid")]
    pooled = pd.concat(scores)
    by_hour = pooled.groupby((pooled.index + NEM_OFFSET).hour).mean().reindex(range(24), fill_value=0.0)
    return {
        "marginal_quantiles": quantiles(wind["uigf_n"]),
        "hourly_score_shift": [round(float(v - by_hour.mean()), 4) for v in by_hour],
        **fit_copula_ar([s - s.groupby((s.index + NEM_OFFSET).hour).transform("mean") for s in scores]),
    }


def cap_parameters(df: pd.DataFrame, fuel: str) -> dict:
    units = df[df["fuel"] == fuel]
    starts, durations, limits = [], [], []
    free_by_hour = pd.Series(0, index=range(24))
    for _, unit in units.groupby("duid"):
        unit = unit.set_index("t")
        capped = unit["semidispatch_cap"].fillna(0).astype(int) == 1
        # An episode can only start in an interval that follows an uncapped one.
        free = ~capped.shift(fill_value=False)
        free_by_hour = free_by_hour.add(free.groupby((free.index + NEM_OFFSET).hour).sum(), fill_value=0)
        for _, run in runs(capped).iterrows():
            if not run["value"]:
                continue
            window = unit.loc[run["start"] : run["start"] + (run["length"] - 1) * INTERVAL]
            starts.append(int((run["start"] + NEM_OFFSET).hour))
            durations.append(int(run["length"]))
            binding = window[(window["uigf"] - window["total_cleared"]) > 0.5]
            if len(binding):
                limits.append(float(binding["total_cleared_n"].median()))
    by_hour = pd.Series(starts).value_counts().reindex(range(24), fill_value=0)
    return {
        "episodes": len(durations),
        # Probability that an episode starts in an uncapped 5-minute interval of that NEM hour.
        "start_rate_by_hour": [round(float(n) / max(1.0, float(free_by_hour[h])), 6) for h, n in by_hour.items()],
        # Every observed duration (intervals): the tail is too long for a 21-point table,
        # whose linear top bin would make multi-day episodes far too common.
        "duration_samples": sorted(durations),
        "limit_share_quantiles": quantiles(limits),
    }


def tracking_parameters(df: pd.DataFrame, fuel: str) -> dict:
    units = df[(df["fuel"] == fuel) & df["actual_n"].notna()]
    error = (units["actual_n"] - units["total_cleared_n"]).rename("e")
    phis = [lag1(unit.set_index("t")["actual_n"] - unit.set_index("t")["total_cleared_n"]) for _, unit in units.groupby("duid")]
    return {"error_quantiles": quantiles(error), "phi": round(float(np.nanmedian(phis)), 4)}


def origin_category(origin: str) -> str:
    value = origin.strip().upper()
    if value == "AWEFS_ASEFS":
        return "aemo"
    if value in ("SCADA", "FCST", "LAST TARGET", ""):
        return "fallback"
    return "self"


def forecast_parameters(frames: dict[str, pd.DataFrame], df: pd.DataFrame, fuel: str) -> dict:
    units = frames["units"]
    duids = set(units.loc[units["fuel"] == fuel, "duid"])
    used = frames["used"][frames["used"]["duid"].isin(duids)].copy()
    used["category"] = used["origin"].map(origin_category)
    self_duids = set(used.loc[used["category"] == "self", "duid"])

    run_lengths: dict[str, list[int]] = {"self": [], "aemo": []}
    self_share = []
    for duid, unit in used[used["duid"].isin(self_duids)].groupby("duid"):
        series = unit.set_index("t")["category"].sort_index()
        self_share.append(float((series == "self").mean()))
        for _, run in runs(series).iterrows():
            if run["value"] in run_lengths:
                run_lengths[run["value"]].append(int(run["length"]))

    origins = frames["origins"][frames["origins"]["duid"].isin(duids)].copy()
    origins["category"] = origins["origin"].map(origin_category)
    self_origins = origins[origins["category"] == "self"]
    aemo_origins = origins[origins["category"] == "aemo"]

    # Forecast error against what the farm then produced, only where no cap held it back.
    actual = df[(df["fuel"] == fuel) & (df["semidispatch_cap"] == 0)][["duid", "t", "actual", "cap"]]
    # Night-time zeros (solar) would swamp the distribution, so only producing intervals count.
    joined = origins.merge(actual, on=["duid", "t"]).dropna(subset=["latest_poe50", "actual"])
    joined = joined[(joined["latest_poe50"] > 0.01 * joined["cap"]) | (joined["actual"] > 0.01 * joined["cap"])]
    joined["error"] = (joined["latest_poe50"] - joined["actual"]) / joined["cap"]
    error_phi = {
        category: float(
            np.nanmedian([lag1(series.set_index("t")["error"].sort_index()) for _, series in group.groupby("duid") if len(series) > 50])
        )
        for category, group in joined.groupby("category")
        if category in ("self", "aemo")
    }

    # How far AEMO's forecast sits from the participant's for the same interval, where a
    # farm has both and is producing: the simulator adds this as AEMO's extra error.
    poe50 = origins[origins["category"].isin(["self", "aemo"])].pivot_table(
        index=["duid", "t"], columns="category", values="latest_poe50", aggfunc="first"
    )
    caps = units.set_index("duid")["cap"]
    if {"self", "aemo"} <= set(poe50.columns):
        both = poe50.dropna()
        both = both[(both["self"] > 0) | (both["aemo"] > 0)]
        gap = (both["aemo"] - both["self"]) / both.index.get_level_values("duid").map(caps).to_numpy()
        gap_phi = float(np.nanmedian([lag1(series.droplevel("duid").sort_index()) for _, series in gap.groupby(level="duid") if len(series) > 50]))
        aemo_minus_self = {"sd": round(float(gap.std()), 5), "phi": round(gap_phi, 4)}
    else:
        aemo_minus_self = None
    return {
        "self_forecasting_share_of_units": round(len(self_duids) / len(duids), 3),
        "self_used_share": round(float(np.mean(self_share)), 4) if self_share else None,
        "mean_run_intervals": {k: round(float(np.mean(v)), 2) for k, v in run_lengths.items() if v},
        "fallback_share": round(float((used["category"] == "fallback").mean()), 5),
        "self_submissions_per_interval_quantiles": quantiles(self_origins["submissions"]),
        "self_participant_suppressed_share": round(
            float(self_origins["suppressed_participant"].sum() / max(1, self_origins["submissions"].sum())), 4
        ),
        "aemo_submissions_per_interval": round(float(aemo_origins["submissions"].median()), 2),
        "error_quantiles": {
            category: quantiles(joined.loc[joined["category"] == category, "error"]) for category in ("self", "aemo")
        },
        "error_phi": {category: round(value, 4) for category, value in error_phi.items()},
        "aemo_minus_self": aemo_minus_self,
        "priority": {
            category: int(used.loc[used["category"] == category, "forecast_priority"].median())
            for category in ("self", "aemo", "fallback")
            if (used["category"] == category).any()
        },
    }


def reference_statistics(df: pd.DataFrame) -> dict:
    """What the realism report checks a simulated fleet against."""
    stats = {}
    for fuel, units in df.groupby("fuel"):
        ramps = units.groupby("duid")["uigf_n"].diff().dropna()
        capped = units["semidispatch_cap"] == 1
        stats[fuel] = {
            "uigf_quantiles": quantiles(units["uigf_n"]),
            "ramp_abs_quantiles": quantiles(ramps.abs()),
            "mean_uigf_by_hour": [
                round(float(v), 4) for v in units.groupby((units["slot"] // 12))["uigf_n"].mean().reindex(range(24), fill_value=0)
            ],
            "cap_share": round(float(capped.mean()), 4),
            "curtailed_share_of_uigf": round(
                float(((units["uigf"] - units["total_cleared"]).clip(lower=0) * capped).sum() / units["uigf"].sum()), 4
            ),
            "tracking_abs_error_quantiles": quantiles((units["actual_n"] - units["total_cleared_n"]).abs()),
        }
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--out", default="artifacts/sim_calibration.json")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is not set.")

    frames = load(args.database_url)
    df = prepare(frames)
    solar, solar_reference = solar_parameters(df)
    reference = reference_statistics(df)
    reference["SOLAR"].update(solar_reference)
    calibration = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "duids": {fuel: sorted(units["duid"].unique().tolist()) for fuel, units in df.groupby("fuel")},
            "dispatch_intervals": int(len(df)),
            "first_interval": df["t"].min().isoformat(),
            "last_interval": df["t"].max().isoformat(),
            "intermittent_intervals": int(len(frames["used"])),
        },
        "publication_minutes_after_day_end": PUBLICATION_MINUTES_AFTER_DAY_END,
        "solar": solar,
        "wind": wind_parameters(df),
        "cap": {fuel: cap_parameters(df, fuel) for fuel in ("SOLAR", "WIND")},
        "tracking": {fuel: tracking_parameters(df, fuel) for fuel in ("SOLAR", "WIND")},
        "forecast": {fuel: forecast_parameters(frames, df, fuel) for fuel in ("SOLAR", "WIND")},
        "reference": reference,
    }
    calibration["solar"]["phi"] = tune_solar_persistence(calibration)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(calibration, indent=1) + "\n")
    summary = {k: v for k, v in calibration.items() if k not in ("solar", "reference")}
    print(json.dumps(summary, indent=1)[:4000])


if __name__ == "__main__":
    main()
