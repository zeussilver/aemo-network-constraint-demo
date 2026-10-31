"""Sandbox simulator: farm physics, determinism, AEMO's publication cadence, scenarios,
training answers, realism against the real fleet, and the sandbox database writer."""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import pytest

from src.aemo_constraint_demo import sim_sandbox
from src.aemo_constraint_demo import sim_scenarios as sc
from src.aemo_constraint_demo.nemweb_stream import NEM_TZ
from src.aemo_constraint_demo.sim_engine import (
    AEMO_ORIGIN,
    Calibration,
    CapWindow,
    QuantileTable,
    SiteControls,
    SiteSpec,
    at_trading_time,
    simulate,
    trading_day_end,
    trading_day_of,
)

CAL = Calibration.load()
WIND = SiteSpec("w", "TSTWND1", "Test Wind", "WIND", "SA1", 100.0, "Test Customer", participant="TSTW")
SOLAR = SiteSpec("s", "TSTSOL1", "Test Solar", "SOLAR", "QLD1", 50.0, "Test Customer", participant="TSTS")


def nem(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=NEM_TZ)


def run(site: SiteSpec, start: str, until: str, seed: int = 1, controls: SiteControls | None = None):
    return list(simulate(site, CAL, seed, nem(start), nem(until), controls))


# ---------------------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------------------


def test_quantile_table_interpolates_and_clamps() -> None:
    table = QuantileTable([0.0, 10.0, 30.0])
    assert table(0.0) == 0.0
    assert table(0.25) == pytest.approx(5.0)
    assert table(0.75) == pytest.approx(20.0)
    assert table(1.5) == 30.0 and table(-1) == 0.0


@pytest.mark.parametrize("site", [WIND, SOLAR], ids=["wind", "solar"])
def test_every_interval_obeys_the_farm_physics(site: SiteSpec) -> None:
    series = run(site, "2026-10-01 04:00", "2026-10-05 04:00")
    assert len(series) == 4 * 288
    previous = None
    for item in series:
        for value in (item.uigf, item.target, item.actual, item.initial, item.available):
            assert 0.0 <= value <= site.capacity_mw
        assert item.target <= item.uigf + 1e-9  # dispatch never asks for more than the forecast
        if not item.capped:
            assert item.target == pytest.approx(item.uigf)
        assert 0 <= item.self_suppressed_participant <= item.self_submissions
        if previous is not None:
            assert item.initial == previous.actual  # SCADA at the start = output at the previous end
            assert item.end - previous.end == timedelta(minutes=5)
        previous = item


def test_solar_is_dark_at_night() -> None:
    night = [item for item in run(SOLAR, "2026-10-01 04:00", "2026-10-03 04:00") if 0 <= item.end.hour < 4 or item.end.hour >= 20]
    assert night and all(item.uigf == 0.0 and item.actual == 0.0 for item in night)


def test_generation_is_deterministic_and_prefix_stable() -> None:
    short = run(WIND, "2026-10-01 04:00", "2026-10-02 04:00", seed=3)
    long = run(WIND, "2026-10-01 04:00", "2026-10-04 04:00", seed=3)
    assert long[: len(short)] == short  # extending a run never rewrites the past
    assert run(WIND, "2026-10-01 04:00", "2026-10-02 04:00", seed=3) == short
    assert run(WIND, "2026-10-01 04:00", "2026-10-02 04:00", seed=4) != short


def test_a_forced_cap_holds_the_target_at_its_limit() -> None:
    window = CapWindow(nem("2026-10-01 09:00"), nem("2026-10-01 19:00"), limit_share=0.05)
    controls = SiteControls(caps=[window], weather={date(2026, 10, 1): "windy"})
    series = run(WIND, "2026-10-01 04:00", "2026-10-02 04:00", controls=controls)
    inside = [item for item in series if window.covers(item.end)]
    assert len(inside) == 120
    assert all(item.capped and item.target <= 5.0 + 1e-9 for item in inside)
    assert sum(item.uigf - item.target for item in inside) / 12 > 300  # a windy day loses hundreds of MWh


def test_suppressed_self_forecasts_send_dispatch_to_aemo() -> None:
    start, end = nem("2026-10-01 07:00"), nem("2026-10-01 16:00")
    series = run(SOLAR, "2026-10-01 04:00", "2026-10-02 04:00", controls=SiteControls(suppressed=[(start, end)]))
    inside = [item for item in series if start < item.end <= end]
    assert all(item.origin != SOLAR.participant for item in inside)
    assert all(item.self_suppressed_aemo == item.self_submissions > 0 for item in inside)
    outside = [item for item in series if not start < item.end <= end]
    assert any(item.origin == SOLAR.participant for item in outside)


def test_a_forced_weather_level_moves_the_wind() -> None:
    def mean_uigf(level: str) -> float:
        day = date(2026, 10, 1)
        series = run(WIND, "2026-10-01 04:00", "2026-10-02 04:00", seed=9, controls=SiteControls(weather={day: level}))
        return sum(item.uigf for item in series) / len(series)

    assert mean_uigf("calm") < mean_uigf("normal") < mean_uigf("windy")


def test_site_spec_rejects_bad_values() -> None:
    with pytest.raises(ValueError, match="fuel"):
        replace(WIND, fuel="HYDRO")
    with pytest.raises(ValueError, match="DUID"):
        replace(WIND, duid="sim-1")
    with pytest.raises(ValueError, match="capacity"):
        replace(WIND, capacity_mw=0)


# ---------------------------------------------------------------------------------------
# Time and publication cadence
# ---------------------------------------------------------------------------------------


def test_trading_day_helpers() -> None:
    assert trading_day_of(nem("2026-10-31 04:05")) == date(2026, 10, 31)
    assert trading_day_of(nem("2026-10-31 04:00")) == date(2026, 10, 30)
    assert at_trading_time(date(2026, 10, 30), "09:00") == nem("2026-10-30 09:00")
    assert at_trading_time(date(2026, 10, 30), "02:00") == nem("2026-10-31 02:00")  # still trading day 30


@pytest.mark.parametrize(
    ("anchor", "scada_until", "dispatch_until", "intermittent_until", "reference"),
    [
        # Next Day Dispatch for D appears 12 minutes after D ends, Intermittent DS 20 minutes after.
        ("2026-10-31 04:11", "2026-10-31 04:10", "2026-10-30 04:00", "2026-10-30 04:00", "2026-10-29"),
        ("2026-10-31 04:15", "2026-10-31 04:15", "2026-10-31 04:00", "2026-10-30 04:00", "2026-10-29"),
        ("2026-10-31 04:20", "2026-10-31 04:20", "2026-10-31 04:00", "2026-10-31 04:00", "2026-10-30"),
        ("2026-10-31 23:59", "2026-10-31 23:55", "2026-10-31 04:00", "2026-10-31 04:00", "2026-10-30"),
    ],
)
def test_publication_follows_nemweb(anchor: str, scada_until: str, dispatch_until: str, intermittent_until: str, reference: str) -> None:
    at = nem(anchor)
    assert sc.published_until(at, "scada", CAL) == nem(scada_until)
    assert sc.published_until(at, "dispatch", CAL) == nem(dispatch_until)
    assert sc.published_until(at, "intermittent", CAL) == nem(intermittent_until)
    assert sc.reference_day(at, CAL).isoformat() == reference


# ---------------------------------------------------------------------------------------
# Worlds, scenarios and training tasks
# ---------------------------------------------------------------------------------------

TRAINING = sc.load_world("training")


def materialise(anchor: datetime, world: sc.World = TRAINING):
    anchored = sc.anchor_world(world, anchor, CAL)
    rows = sc.published_rows(anchored, sc.generate(anchored, CAL), CAL, anchor)
    runs = sc.ingest_runs(anchored, rows, CAL, anchor)
    return anchored, rows, runs


def test_published_rows_respect_cadence_and_the_outage() -> None:
    anchor = nem("2026-11-03 14:37")
    anchored, rows, _ = materialise(anchor)
    assert max(row[1] for row in rows["scada"]) == nem("2026-11-03 14:35")
    assert max(row[1] for row in rows["dispatch"]) == trading_day_end(anchored.reference_day)
    assert max(row[1] for row in rows["forecast_used"]) == trading_day_end(anchored.reference_day)
    outage = max(row[1] for row in rows["scada"] if row[0] == "SIMSLB1")
    assert outage == nem("2026-11-03 11:35")  # three hours before the anchor
    assert len({row[0] for row in rows["dispatch"]}) == len(TRAINING.sites)
    # One AEMO row per interval per farm, plus one self-forecast row for farms that submit.
    per_interval = {duid: sum(1 for row in rows["forecast_origin"] if row[0] == duid) for duid in ("SIMSLB1", "SIMSLE1")}
    assert per_interval["SIMSLB1"] == 2 * per_interval["SIMSLE1"]


@pytest.mark.parametrize("anchor", ["2026-11-02 00:10", "2026-11-03 04:25", "2026-11-04 12:00", "2026-11-05 19:45"])
def test_training_answers_match_the_scripted_scenarios(anchor: str) -> None:
    anchored, rows, runs = materialise(nem(anchor))
    document = sc.manifest(anchored, rows, runs)
    answers = {task["id"]: task["answer"] for task in document["tasks"]}
    assert answers["network-constraint"]["site"] == "wind-a"
    assert answers["network-constraint"]["mwh"] == document["curtailment_reference_day"]["SIMWDA1"]["curtailed_mwh"]
    assert answers["scada-outage"]["site"] == "solar-b"
    assert answers["self-forecast-suppressed"] == {"site": "solar-c", "fallback": "AEMO AWEFS/ASEFS"}
    assert answers["ingest-failure"] == {"source": "scada"}
    assert all("{day}" not in task["prompt"] for task in document["tasks"])
    # The console reads each source's latest run; for SCADA that must be the scripted failure.
    latest_scada = max((run for run in runs if run["source"] == "scada"), key=lambda run: run["started_at"])
    assert latest_scada["status"] == "failed"


def test_curtailment_uses_the_console_formula() -> None:
    day = date(2026, 10, 30)
    t = nem("2026-10-30 12:00")
    rows = {
        "dispatch": [
            ("A", t, 0, 0, 30.0, 42.0, 42.0, 1),  # 12 MW short for 5 min = 1 MWh
            ("A", t + timedelta(minutes=5), 0, 0, 50.0, 44.0, 44.0, 1),  # cleared above UIGF: 0
            ("A", t + timedelta(minutes=10), 0, 0, 20.0, 80.0, 80.0, 0),  # not capped: not curtailment
            ("A", t + timedelta(days=1), 0, 0, 0.0, 99.0, 99.0, 1),  # another trading day
        ]
    }
    assert sc.curtailment_by_site(rows, day) == {"A": {"curtailed_mwh": 1.0, "capped_intervals": 2, "intervals": 3}}


def test_world_files_are_validated() -> None:
    base = {
        "seed": 1,
        "sites": [{"key": "a", "duid": "SIMA1", "name": "A", "fuel": "WIND", "region": "SA1", "capacity_mw": 10, "customer": "C"}],
    }

    def world(*events: dict) -> sc.World:
        return sc.parse_world({**base, "scenarios": [{"key": "s", "title": "t", "description": "d", "events": list(events)}]}, "test")

    with pytest.raises(ValueError, match="unknown site"):
        world({"kind": "cap", "site": "zz", "day": 0, "start": "09:00", "hours": 1, "limit_share": 0.1})
    with pytest.raises(ValueError, match="event kind"):
        world({"kind": "earthquake", "site": "a"})
    with pytest.raises(ValueError, match="0 or negative"):
        world({"kind": "weather", "site": "a", "day": 1, "weather": "windy"})
    with pytest.raises(ValueError, match="unknown event fields"):
        world({"kind": "scada_outage", "site": "a", "hours_before_anchor": 1, "colour": "red"})
    with pytest.raises(ValueError, match="past the end"):
        sc.anchor_world(world({"kind": "cap", "site": "a", "day": 0, "start": "20:00", "hours": 10, "limit_share": 0.1}), nem("2026-11-03 12:00"), CAL)
    with pytest.raises(ValueError, match="no self-forecast"):
        sc.anchor_world(world({"kind": "self_forecast_suppressed", "site": "a", "day": 0, "start": "08:00", "hours": 2}), nem("2026-11-03 12:00"), CAL)
    with pytest.raises(ValueError, match="weather for WIND"):
        sc.anchor_world(world({"kind": "weather", "site": "a", "day": 0, "weather": "clear"}), nem("2026-11-03 12:00"), CAL)


# ---------------------------------------------------------------------------------------
# Realism against the real fleet (scripts/sim_realism.py)
# ---------------------------------------------------------------------------------------


def test_simulated_fleet_stays_within_the_realism_tolerances() -> None:
    pytest.importorskip("pandas")
    from scripts.sim_calibrate import reference_statistics
    from scripts.sim_realism import compare, simulate_fleet

    df, sim_self = simulate_fleet(CAL, (101, 102, 103, 104, 105))
    real_self = {fuel: CAL.raw["forecast"][fuel]["self_used_share"] for fuel in ("SOLAR", "WIND")}
    checks = compare(CAL.raw["reference"], reference_statistics(df), real_self, sim_self)
    failed = {f"{fuel}.{name}": item["value"] for fuel, items in checks.items() for name, item in items.items() if not item["pass"]}
    assert not failed


# ---------------------------------------------------------------------------------------
# Sandbox database
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ops_console", "ops_console_test", "sandbox_prod", "ops_console_sandbox_backup"])
def test_the_latch_refuses_anything_but_a_sandbox(name: str) -> None:
    with pytest.raises(ValueError, match="must end in _sandbox or _e2e"):
        sim_sandbox.assert_sandbox_database(f"postgresql://u:p@localhost:5432/{name}")
    with pytest.raises(ValueError):
        sim_sandbox.load(f"postgresql://u:p@localhost:5432/{name}", TRAINING, nem("2026-11-03 12:00"), CAL)


def test_the_latch_accepts_sandbox_and_e2e_databases() -> None:
    assert sim_sandbox.assert_sandbox_database("postgresql://u:p@h/ops_console_sandbox") == "ops_console_sandbox"
    assert sim_sandbox.assert_sandbox_database("postgresql://u:p@h/ops_console_sim_e2e?sslmode=require") == "ops_console_sim_e2e"


SANDBOX_TEST_DATABASE_URL = os.environ.get("SANDBOX_TEST_DATABASE_URL")
needs_sandbox_db = pytest.mark.skipif(not SANDBOX_TEST_DATABASE_URL, reason="SANDBOX_TEST_DATABASE_URL not set")


def _count(conn, table: str, where: str = "") -> int:
    return conn.execute(f"SELECT count(*) FROM {table} {where}").fetchone()[0]


@needs_sandbox_db
def test_load_then_tick_keeps_the_past_and_adds_the_present() -> None:
    psycopg = pytest.importorskip("psycopg")
    anchor = nem("2026-11-03 14:37")
    summary = sim_sandbox.load(SANDBOX_TEST_DATABASE_URL, TRAINING, anchor, CAL)
    assert summary["reference_day"] == "2026-11-02"
    assert summary["sites"] == 5 and summary["customers"] == 2
    with psycopg.connect(SANDBOX_TEST_DATABASE_URL) as conn:
        assert _count(conn, "registry_unit") == _count(conn, "unit") == _count(conn, "site") == 5
        assert _count(conn, "audit_log", "WHERE actor_email = 'sandbox-simulator'") == summary["audit_rows"]
        assert _count(conn, "customer", "WHERE NOT is_synthetic") == 0
        manifest = conn.execute("SELECT manifest FROM sim_run").fetchone()[0]
        assert [task["id"] for task in manifest["tasks"]] == summary["tasks"]
        before = conn.execute("SELECT mw FROM scada_reading WHERE duid = 'SIMWDA1' ORDER BY settlement_date").fetchall()
        failed = conn.execute("SELECT source FROM ingest_run ORDER BY started_at DESC LIMIT 1").fetchone()[0]
        assert failed == "scada"

    later = sim_sandbox.tick(SANDBOX_TEST_DATABASE_URL, anchor + timedelta(hours=1), CAL)
    assert later["rows"]["scada_reading"] == 12 * 4  # an hour for every farm but the silent one
    assert later["ingest_runs"] == ["scada"]
    assert sim_sandbox.tick(SANDBOX_TEST_DATABASE_URL, anchor + timedelta(hours=1), CAL)["rows"]["scada_reading"] == 0
    with psycopg.connect(SANDBOX_TEST_DATABASE_URL) as conn:
        after = conn.execute(
            "SELECT mw FROM scada_reading WHERE duid = 'SIMWDA1' AND settlement_date <= %s ORDER BY settlement_date", (anchor,)
        ).fetchall()
        assert after == before  # earlier rows are untouched
        assert conn.execute("SELECT max(settlement_date) FROM scada_reading WHERE duid = 'SIMSLB1'").fetchone()[0] == nem("2026-11-03 11:35")

    # The next morning, Next Day files for the anchor's trading day arrive in one tick.
    morning = sim_sandbox.tick(SANDBOX_TEST_DATABASE_URL, nem("2026-11-04 05:00"), CAL)
    assert morning["rows"]["dispatch_solution"] == 288 * 5
    assert set(morning["ingest_runs"]) == {"scada", "dispatch", "intermittent"}


@needs_sandbox_db
def test_cli_refuses_a_production_database_name() -> None:
    parts = urlsplit(SANDBOX_TEST_DATABASE_URL)
    production = urlunsplit(parts._replace(path="/ops_console"))
    assert sim_sandbox.main(["load", "--database-url", production]) == 2
