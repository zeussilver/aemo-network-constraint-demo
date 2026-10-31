#!/usr/bin/env python
"""How close is a simulated fleet to the real one it was calibrated on?

Simulates a fleet the size of the console's (7 solar and 5 wind farms) over the same 29
trading days the calibration read (2026-09-19 to 2026-10-17), five times with different
seeds pooled (one 29-day run is too few farm-days to judge a distribution), with no
scripted events and the calibrated cap rate. Then computes the same statistics
``scripts/sim_calibrate.py`` stored for the real DUIDs and compares them check by check:

- the distribution of UIGF as a share of capacity (5th to 95th percentile);
- the 5-minute change in UIGF (median and 90th percentile);
- the mean daily shape of output (by NEM hour);
- the share of intervals under a semi-dispatch cap and the share of UIGF energy curtailed;
- how closely output follows the dispatch target;
- the share of intervals dispatched on the farm's own self-forecast.

Each check has a tolerance written next to it. Writes ``artifacts/sim_realism.json`` and
``docs/images/sim-realism.png``.

    python scripts/sim_realism.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.sim_calibrate import QUANTILE_POINTS, nem_slot, quantiles, reference_statistics
from src.aemo_constraint_demo.nemweb_stream import NEM_TZ
from src.aemo_constraint_demo.sim_engine import Calibration, SiteSpec, simulate

START = datetime(2026, 9, 19, 4, 0, tzinfo=NEM_TZ)
UNTIL = datetime(2026, 10, 18, 4, 0, tzinfo=NEM_TZ)
CENTRAL = (QUANTILE_POINTS >= 0.05) & (QUANTILE_POINTS <= 0.95)

# Tolerances, in the unit of each check (shares of capacity, or plain shares).
TOLERANCES = {
    "uigf_quantile_max_abs_diff": 0.08,
    "ramp_p50_abs_diff": 0.01,
    "ramp_p90_ratio": (0.6, 1.6),
    "hourly_mean_rmse": 0.05,
    "cap_share_abs_diff": 0.08,
    "curtailed_share_abs_diff": 0.08,
    "tracking_p90_abs_diff": 0.03,
    "self_used_share_abs_diff": 0.1,
}


def fleet(calibration: Calibration, fuels: tuple[str, ...] = ("SOLAR", "WIND")) -> list[SiteSpec]:
    """Same mix as the console's real fleet, with the calibrated share of self-forecasters."""
    sites = []
    for fuel, count in (("SOLAR", 7), ("WIND", 5)):
        if fuel not in fuels:
            continue
        self_share = calibration.raw["forecast"][fuel]["self_forecasting_share_of_units"]
        forecasters = round(count * self_share)
        for index in range(count):
            duid = f"R{fuel[0]}{index + 1}"
            sites.append(
                SiteSpec(
                    key=duid.lower(), duid=duid, name=f"Realism {fuel.title()} {index + 1}", fuel=fuel, region="NSW1",
                    capacity_mw=100.0, customer="Realism", participant=f"P{duid}" if index < forecasters else None,
                )
            )
    return sites


def simulate_fleet(
    calibration: Calibration,
    seeds: int | tuple[int, ...],
    start: datetime = START,
    until: datetime = UNTIL,
    fuels: tuple[str, ...] = ("SOLAR", "WIND"),
) -> tuple[pd.DataFrame, dict]:
    """A frame shaped like ``sim_calibrate.prepare``'s, plus each fuel's self-forecast share.
    Several seeds are pooled (each as its own set of farms) to keep sampling noise down."""
    records, self_used = [], {"SOLAR": [], "WIND": []}
    for seed in (seeds,) if isinstance(seeds, int) else seeds:
        for site in fleet(calibration, fuels):
            duid = f"{site.duid}S{seed}"
            for item in simulate(site, calibration, seed, start, until):
                records.append((duid, site.fuel, site.capacity_mw, item.end, item.uigf, item.target, item.capped, item.actual))
                if site.participant:
                    self_used[site.fuel].append(item.origin == site.participant)
    df = pd.DataFrame(records, columns=["duid", "fuel", "cap", "t", "uigf", "total_cleared", "semidispatch_cap", "actual"])
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df["semidispatch_cap"] = df["semidispatch_cap"].astype(int)
    for column in ("total_cleared", "uigf", "actual"):
        df[column + "_n"] = df[column] / df["cap"]
    df["slot"] = nem_slot(df["t"])
    return df, {fuel: float(np.mean(values)) for fuel, values in self_used.items() if values}


def compare(real: dict, simulated: dict, real_self: dict, sim_self: dict) -> dict:
    report = {}
    for fuel in ("SOLAR", "WIND"):
        r, s = real[fuel], simulated[fuel]
        checks = {
            "uigf_quantile_max_abs_diff": float(np.max(np.abs(np.array(r["uigf_quantiles"])[CENTRAL] - np.array(s["uigf_quantiles"])[CENTRAL]))),
            "ramp_p50_abs_diff": abs(r["ramp_abs_quantiles"][10] - s["ramp_abs_quantiles"][10]),
            "ramp_p90_ratio": s["ramp_abs_quantiles"][18] / r["ramp_abs_quantiles"][18],
            "cap_share_abs_diff": abs(r["cap_share"] - s["cap_share"]),
            "curtailed_share_abs_diff": abs(r["curtailed_share_of_uigf"] - s["curtailed_share_of_uigf"]),
            "tracking_p90_abs_diff": abs(r["tracking_abs_error_quantiles"][18] - s["tracking_abs_error_quantiles"][18]),
            "self_used_share_abs_diff": abs(real_self[fuel] - sim_self[fuel]),
            "hourly_mean_rmse": float(np.sqrt(np.mean((np.array(r["mean_uigf_by_hour"]) - np.array(s["mean_uigf_by_hour"])) ** 2))),
        }
        results = {}
        for name, value in checks.items():
            tolerance = TOLERANCES[name]
            passed = tolerance[0] <= value <= tolerance[1] if isinstance(tolerance, tuple) else value <= tolerance
            results[name] = {"value": round(value, 4), "tolerance": tolerance, "pass": bool(passed)}
        report[fuel] = results
    return report


def figure(real: dict, simulated: dict, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colours = {"real": "#1f2937", "sim": "#1f6feb"}
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5))
    hours = np.arange(24)
    ax = axes[0][0]
    for fuel, style in (("SOLAR", "-"), ("WIND", "--")):
        ax.plot(hours, real[fuel]["mean_uigf_by_hour"], style, color=colours["real"], label=f"{fuel.title()}, real")
        ax.plot(hours, simulated[fuel]["mean_uigf_by_hour"], style, color=colours["sim"], label=f"{fuel.title()}, simulated")
    ax.set(title="Mean UIGF by hour (share of capacity)", xlabel="NEM hour", xticks=range(0, 24, 3))
    ax.legend(fontsize=8)

    ax = axes[0][1]
    for fuel, marker in (("SOLAR", "o"), ("WIND", "s")):
        ax.plot(real[fuel]["uigf_quantiles"], simulated[fuel]["uigf_quantiles"], marker, ms=4, color=colours["sim"] if fuel == "WIND" else "#c4622d", label=fuel.title())
    ax.plot([0, 1], [0, 1], color="#9ca3af", lw=1)
    ax.set(title="UIGF quantiles: simulated against real", xlabel="real", ylabel="simulated")
    ax.legend(fontsize=8)

    ax = axes[1][0]
    for fuel, marker in (("SOLAR", "o"), ("WIND", "s")):
        r = np.array(real[fuel]["ramp_abs_quantiles"])[10:20]
        s = np.array(simulated[fuel]["ramp_abs_quantiles"])[10:20]
        ax.plot(r, s, marker, ms=4, color=colours["sim"] if fuel == "WIND" else "#c4622d", label=fuel.title())
    top = max(max(real[f]["ramp_abs_quantiles"][19] for f in real), max(simulated[f]["ramp_abs_quantiles"][19] for f in simulated))
    ax.plot([0, top], [0, top], color="#9ca3af", lw=1)
    ax.set(title="5-minute |ΔUIGF|, 50th-95th percentile", xlabel="real", ylabel="simulated")
    ax.legend(fontsize=8)

    ax = axes[1][1]
    labels, width = [], 0.35
    positions = np.arange(4)
    values_real = [real["SOLAR"]["cap_share"], real["SOLAR"]["curtailed_share_of_uigf"], real["WIND"]["cap_share"], real["WIND"]["curtailed_share_of_uigf"]]
    values_sim = [simulated["SOLAR"]["cap_share"], simulated["SOLAR"]["curtailed_share_of_uigf"], simulated["WIND"]["cap_share"], simulated["WIND"]["curtailed_share_of_uigf"]]
    labels = ["Solar\ncapped", "Solar\ncurtailed", "Wind\ncapped", "Wind\ncurtailed"]
    ax.bar(positions - width / 2, values_real, width, color=colours["real"], label="real")
    ax.bar(positions + width / 2, values_sim, width, color=colours["sim"], label="simulated")
    ax.set(title="Share of intervals capped / of UIGF energy curtailed", xticks=positions, xticklabels=labels)
    ax.legend(fontsize=8)

    fig.suptitle("Simulated fleet against the 12 real DUIDs it was calibrated on (2026-09-19 to 2026-10-17)", fontsize=11)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calibration", default="artifacts/sim_calibration.json")
    parser.add_argument("--seeds", type=int, nargs="+", default=[101, 102, 103, 104, 105])
    parser.add_argument("--out", default="artifacts/sim_realism.json")
    parser.add_argument("--figure", default="docs/images/sim-realism.png")
    args = parser.parse_args()

    calibration = Calibration.load(args.calibration)
    df, sim_self = simulate_fleet(calibration, tuple(args.seeds))
    simulated = reference_statistics(df)
    real = calibration.raw["reference"]
    real_self = {fuel: calibration.raw["forecast"][fuel]["self_used_share"] for fuel in ("SOLAR", "WIND")}
    checks = compare(real, simulated, real_self, sim_self)
    report = {
        "window": [START.isoformat(), UNTIL.isoformat()],
        "seeds": args.seeds,
        "fleet": {"SOLAR": 7, "WIND": 5, "seeds_pooled": len(args.seeds), "intervals": int(len(df))},
        "checks": checks,
        "passed": all(item["pass"] for fuel in checks.values() for item in fuel.values()),
        "simulated": simulated,
        "self_used_share": {"real": real_self, "simulated": {k: round(v, 4) for k, v in sim_self.items()}},
    }
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
    figure(real, simulated, Path(args.figure))
    print(json.dumps({"passed": report["passed"], "checks": checks}, indent=1))


if __name__ == "__main__":
    main()
