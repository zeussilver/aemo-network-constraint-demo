from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.aemo_constraint_demo import ops_ingest
from src.aemo_constraint_demo.nemweb_stream import NEM_TZ
from tests.test_nemweb_stream import SCADA_COLUMNS, aemo_csv, write_zip


def nem(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=NEM_TZ)


UNIT_SOLUTION_COLUMNS = [
    "SETTLEMENTDATE", "RUNNO", "DUID", "INTERVENTION", "INITIALMW", "TOTALCLEARED",
    "AVAILABILITY", "SEMIDISPATCHCAP", "UIGF",
]
RUN_COLUMNS = [
    "RUN_DATETIME", "DUID", "ORIGIN", "FORECAST_PRIORITY", "OFFERDATETIME",
    "PARTICIPANT_TIMESTAMP", "SUPPRESSED_PARTICIPANT", "SUPPRESSED_AEMO", "TRANSACTION_ID", "LASTCHANGED",
]
PRED_COLUMNS = [
    "RUN_DATETIME", "DUID", "ORIGIN", "FORECAST_PRIORITY", "OFFERDATETIME", "INTERVAL_DATETIME",
    "FORECAST_MEAN", "FORECAST_POE10", "FORECAST_POE50", "FORECAST_POE90",
]
TRK_COLUMNS = ["SETTLEMENTDATE", "DUID", "ORIGIN", "FORECAST_PRIORITY", "OFFERDATETIME"]


def dispatch_zip(tmp_path: Path) -> Path:
    rows = [
        ["2026/10/17 04:05:00", "1", "CATHROCK", "0", "40", "38", "45", "1", "45"],
        ["2026/10/17 04:05:00", "1", "CATHROCK", "1", "40", "37", "45", "1", "45"],  # intervention run wins
        ["2026/10/17 04:10:00", "1", "CATHROCK", "0", "38", "", "44", "0", "44"],
        ["2026/10/17 04:05:00", "1", "OTHER1", "0", "1", "1", "1", "0", "1"],
    ]
    return write_zip(tmp_path / "ndd.zip", {"ndd.CSV": aemo_csv(("DISPATCH,UNIT_SOLUTION", UNIT_SOLUTION_COLUMNS, rows))})


def intermittent_zip(tmp_path: Path) -> Path:
    t = "2026/10/17 04:05:00"
    runs = [
        [t, "WOOLGSF1", "WOOLOOGA", "100", "2026/10/17 03:59:00", "", "1", "0", "a", ""],
        [t, "WOOLGSF1", "WOOLOOGA", "100", "2026/10/17 04:00:00", "", "0", "0", "b", ""],
        [t, "WOOLGSF1", "AWEFS_ASEFS", "304", "2026/10/17 03:58:00", "", "0", "0", "c", ""],
    ]
    preds = [
        [t, "WOOLGSF1", "WOOLOOGA", "100", "2026/10/17 03:59:00", t, "10", "", "10", ""],
        [t, "WOOLGSF1", "WOOLOOGA", "100", "2026/10/17 04:00:00", t, "12", "", "12", ""],
        [t, "WOOLGSF1", "AWEFS_ASEFS", "304", "2026/10/17 03:58:00", t, "11", "", "11", ""],
    ]
    trk = [
        [t, "WOOLGSF1", "WOOLOOGA", "100", "2026/10/17 04:00:00"],
        ["2026/10/17 04:10:00", "WOOLGSF1", "SCADA", "-1", ""],
    ]
    text = aemo_csv(
        ("DEMAND,INTERMITTENT_DS_RUN", RUN_COLUMNS, runs),
        ("DEMAND,INTERMITTENT_DS_PRED", PRED_COLUMNS, preds),
        ("DISPATCH,INTERMITTENT_FORECAST_TRK", TRK_COLUMNS, trk),
    )
    return write_zip(tmp_path / "ids.zip", {"ids.CSV": text})


def test_file_stamp_reads_day_and_five_minute_names() -> None:
    assert ops_ingest.file_stamp("PUBLIC_DISPATCHSCADA_202610190115_0000000543621096.zip") == nem("2026-10-19 01:15")
    assert ops_ingest.file_stamp("PUBLIC_DISPATCHSCADA_20261017.zip") == nem("2026-10-17 00:00")
    assert ops_ingest.file_stamp("PUBLIC_NEXT_DAY_DISPATCH_20261017_0000000543466400.zip") == nem("2026-10-17 00:00")
    assert ops_ingest.file_stamp("readme.txt") is None


def remote(name: str) -> ops_ingest.RemoteFile:
    return ops_ingest.RemoteFile(name=name, url=f"https://example.invalid/{name}", stamp=ops_ingest.file_stamp(name))


def test_select_scada_files_skips_five_minute_files_covered_by_an_archive_day() -> None:
    archive = [remote("PUBLIC_DISPATCHSCADA_20261016.zip"), remote("PUBLIC_DISPATCHSCADA_20261017.zip")]
    current = [
        remote("PUBLIC_DISPATCHSCADA_202610172355_1.zip"),
        remote("PUBLIC_DISPATCHSCADA_202610180000_1.zip"),  # last interval of the 17th archive
        remote("PUBLIC_DISPATCHSCADA_202610180005_1.zip"),
    ]

    chosen = ops_ingest.select_scada_files(archive, current, seen={"PUBLIC_DISPATCHSCADA_20261016.zip"}, since=nem("2026-10-16 00:00"))

    assert [item.name for item in chosen] == ["PUBLIC_DISPATCHSCADA_20261017.zip", "PUBLIC_DISPATCHSCADA_202610180005_1.zip"]


def test_select_files_applies_window_and_seen_set() -> None:
    files = [remote(f"PUBLIC_NEXT_DAY_DISPATCH_202610{day}_1.zip") for day in ("14", "15", "16")]

    chosen = ops_ingest.select_files(files, seen={"PUBLIC_NEXT_DAY_DISPATCH_20261016_1.zip"}, since=nem("2026-10-15 00:00"))

    assert [item.name for item in chosen] == ["PUBLIC_NEXT_DAY_DISPATCH_20261015_1.zip"]


def test_dispatch_rows_keep_the_highest_intervention_run(tmp_path: Path) -> None:
    rows = sorted(ops_ingest.dispatch_rows(dispatch_zip(tmp_path), ["CATHROCK"]))

    assert rows == [
        ("CATHROCK", nem("2026-10-17 04:05"), 1, 40.0, 37.0, 45.0, 45.0, 1),
        ("CATHROCK", nem("2026-10-17 04:10"), 0, 38.0, None, 44.0, 44.0, 0),
    ]


def test_scada_rows_parse_mw(tmp_path: Path) -> None:
    text = aemo_csv(("DISPATCH,UNIT_SCADA", SCADA_COLUMNS, [["2026/10/17 00:05:00", "CATHROCK", "41.2", "x"], ["2026/10/17 00:05:00", "CATHROCK", "", "x"]]))
    path = write_zip(tmp_path / "s.zip", {"s.CSV": text})

    assert ops_ingest.scada_rows(path, ["CATHROCK"]) == [("CATHROCK", nem("2026-10-17 00:05"), 41.2)]


def test_intermittent_rows_aggregate_origins_and_join_used_poe50(tmp_path: Path) -> None:
    used, origins = ops_ingest.intermittent_rows(intermittent_zip(tmp_path), ["WOOLGSF1"])

    assert sorted(used, key=lambda row: row[1]) == [
        ("WOOLGSF1", nem("2026-10-17 04:05"), "WOOLOOGA", 100, nem("2026-10-17 04:00"), 12.0),
        ("WOOLGSF1", nem("2026-10-17 04:10"), "SCADA", -1, None, None),
    ]
    by_origin = {row[2]: row for row in origins}
    assert by_origin["WOOLOOGA"] == ("WOOLGSF1", nem("2026-10-17 04:05"), "WOOLOOGA", 2, 0, 1, 100, 12.0)
    assert by_origin["AWEFS_ASEFS"][3:] == (1, 0, 0, 304, 11.0)


def test_event_envelope_targets_the_regional_endpoint_and_splits_fields() -> None:
    connection = "InstrumentationKey=abc-123;IngestionEndpoint=https://newzealandnorth-0.in.applicationinsights.azure.com/;LiveEndpoint=x"
    event = {"event": "ingest_run", "source": "scada", "status": "failed", "rows": 12, "files": 1, "seconds": 3.5, "error": None}

    url, payload = ops_ingest.build_event_envelope(connection, "ingest_run", event, now=nem("2026-10-19 10:00"))

    assert url == "https://newzealandnorth-0.in.applicationinsights.azure.com/v2/track"
    base = payload[0]["data"]["baseData"]
    assert payload[0]["iKey"] == "abc-123" and base["name"] == "ingest_run"
    assert base["properties"] == {"event": "ingest_run", "source": "scada", "status": "failed"}
    assert base["measurements"] == {"files": 1.0, "rows": 12.0, "seconds": 3.5}


def test_emit_event_never_raises_when_telemetry_fails(monkeypatch) -> None:
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "IngestionEndpoint=https://x/")  # no key
    ops_ingest.emit_event({"event": "ingest_run", "source": "scada", "status": "success"})


def test_build_snapshot_makes_payload_json_safe() -> None:
    payload = {
        "duid": "CATHROCK",
        "as_of": "2026-10-18T04:00:00",
        "generated_at": "2026-10-19T03:00:00",
        "horizon_hours": 6,
        "data_lag_hours": 23.5,
        "constraint_risk": {"probability": 0.12, "risk_level": "low"},
        "model_metrics": {"roc_auc": float("nan")},
    }

    snapshot = ops_ingest.build_snapshot(payload, "bundle.joblib")

    assert snapshot["as_of"] == nem("2026-10-18 04:00")
    assert snapshot["payload"]["model_metrics"]["roc_auc"] is None
    assert snapshot["probability"] == pytest.approx(0.12)


# ---------------------------------------------------------------------------------------
# Database integration: needs TEST_DATABASE_URL pointing at a database migrated by
# `npx prisma migrate deploy` (console/). Skipped otherwise.
# ---------------------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def store():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE ingested_file, ingest_run, scada_reading, dispatch_solution, forecast_used, "
            "forecast_origin_interval, forecast_snapshot RESTART IDENTITY CASCADE"
        )
    store = ops_ingest.Store(TEST_DATABASE_URL)
    yield store
    store.close()


def fake_lister(files: dict[str, list[ops_ingest.RemoteFile]]):
    def lister(url: str, prefix: str) -> list[ops_ingest.RemoteFile]:
        return files.get(url, [])

    return lister


@needs_db
def test_ingest_dispatch_loads_once_and_skips_seen_files(store, tmp_path: Path) -> None:
    source = dispatch_zip(tmp_path)
    files = {ops_ingest.DISPATCH_CURRENT: [remote("PUBLIC_NEXT_DAY_DISPATCH_20261017_1.zip")]}
    config = ops_ingest.IngestConfig(cache_dir=tmp_path, trigger="manual")
    download = lambda url, cache: source  # noqa: E731

    first = ops_ingest.ingest_source(store, "dispatch", ["CATHROCK"], config, download, fake_lister(files), now=nem("2026-10-18 05:00"))
    second = ops_ingest.ingest_source(store, "dispatch", ["CATHROCK"], config, download, fake_lister(files), now=nem("2026-10-18 06:00"))

    assert (first.status, first.files, first.rows) == ("success", 1, 2)
    assert first.latest_interval == nem("2026-10-17 04:10")
    assert (second.status, second.files, second.rows) == ("success", 0, 0)
    with store.conn.cursor() as cur:
        cur.execute("SELECT count(*), max(intervention) FROM dispatch_solution")
        assert cur.fetchone() == (2, 1)
        cur.execute("SELECT source, status, files_processed, rows_written FROM ingest_run ORDER BY id")
        assert cur.fetchall() == [("dispatch", "success", 1, 2), ("dispatch", "success", 0, 0)]


@needs_db
def test_ingest_records_a_bad_file_as_failed_and_keeps_the_good_ones(store, tmp_path: Path) -> None:
    good = dispatch_zip(tmp_path)
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    files = {
        ops_ingest.DISPATCH_CURRENT: [
            remote("PUBLIC_NEXT_DAY_DISPATCH_20261016_1.zip"),
            remote("PUBLIC_NEXT_DAY_DISPATCH_20261017_1.zip"),
        ]
    }
    download = lambda url, cache: bad if "20261016" in url else good  # noqa: E731
    config = ops_ingest.IngestConfig(cache_dir=tmp_path)

    result = ops_ingest.ingest_source(store, "dispatch", ["CATHROCK"], config, download, fake_lister(files), now=nem("2026-10-18 05:00"))

    assert result.status == "failed" and result.files == 1
    assert "20261016" in result.errors[0]
    with store.conn.cursor() as cur:
        cur.execute("SELECT status, error IS NOT NULL FROM ingest_run")
        assert cur.fetchone() == ("failed", True)
        cur.execute("SELECT file_name FROM ingested_file")
        assert cur.fetchall() == [("PUBLIC_NEXT_DAY_DISPATCH_20261017_1.zip",)]


@needs_db
def test_ingest_intermittent_writes_both_tables(store, tmp_path: Path) -> None:
    source = intermittent_zip(tmp_path)
    files = {ops_ingest.INTERMITTENT_CURRENT: [remote("PUBLIC_NEXT_DAY_INTERMITTENT_DS_20261017_1.zip")]}
    config = ops_ingest.IngestConfig(cache_dir=tmp_path)

    result = ops_ingest.ingest_source(
        store, "intermittent", ["WOOLGSF1"], config, lambda url, cache: source, fake_lister(files), now=nem("2026-10-18 05:00")
    )

    assert result.status == "success" and result.rows == 4
    with store.conn.cursor() as cur:
        cur.execute("SELECT origin, poe50 FROM forecast_used ORDER BY settlement_date")
        assert cur.fetchall() == [("WOOLOOGA", 12.0), ("SCADA", None)]
        cur.execute("SELECT origin, submissions, suppressed_participant FROM forecast_origin_interval ORDER BY origin")
        assert cur.fetchall() == [("AWEFS_ASEFS", 1, 0), ("WOOLOOGA", 2, 1)]


@needs_db
def test_ingest_forecasts_saves_a_snapshot_and_skips_without_new_dispatch(store, tmp_path: Path) -> None:
    import joblib

    bundle = tmp_path / "bundle.joblib"
    joblib.dump({"duid": "CATHROCK"}, bundle)
    calls: list[str] = []

    def forecaster(model_path, duid, source, cache_dir):
        calls.append(duid)
        return {
            "duid": duid,
            "as_of": "2026-10-18T04:00:00",
            "generated_at": "2026-10-19T03:00:00+11:00",
            "horizon_hours": 6,
            "data_lag_hours": 23.0,
            "constraint_risk": {"probability": 0.9, "risk_level": "high"},
        }

    config = ops_ingest.IngestConfig(cache_dir=tmp_path, model_paths=[str(bundle)])
    first = ops_ingest.ingest_forecasts(store, ["CATHROCK"], config, dispatch_loaded=False, forecaster=forecaster)
    second = ops_ingest.ingest_forecasts(store, ["CATHROCK"], config, dispatch_loaded=False, forecaster=forecaster)

    assert first.rows == 1 and second.rows == 0 and calls == ["CATHROCK"]
    with store.conn.cursor() as cur:
        cur.execute("SELECT duid, as_of, risk_level, payload->'constraint_risk'->>'risk_level' FROM forecast_snapshot")
        assert cur.fetchone() == ("CATHROCK", nem("2026-10-18 04:00"), "high", "high")


@needs_db
def test_store_accepts_the_password_separately_from_the_url() -> None:
    # On Azure the password is an Entra token passed apart from the URL; mimic that locally.
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(TEST_DATABASE_URL)
    password = parts.password
    bare = urlunsplit(parts._replace(netloc=f"{parts.username}@{parts.hostname}:{parts.port}"))
    store = ops_ingest.Store(bare, password=password)
    try:
        with store.conn.cursor() as cur:
            cur.execute("SELECT current_user")
            assert cur.fetchone()[0] == parts.username
    finally:
        store.close()
