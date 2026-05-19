from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.aemo_constraint_demo.scan import (
    STATUS_MISSING_CONNECTION_POINT,
    STATUS_OK,
    ScanThresholds,
    add_horizon_labels,
    horizon_label_column,
    rank_scan_results,
    recommend_candidate,
    summarise_duid_window,
)


def _dispatchload(duid: str = "D1") -> pd.DataFrame:
    intervals = pd.date_range("2026-05-01 00:05:00", periods=4, freq="5min")
    return pd.DataFrame({"SETTLEMENTDATE": intervals.astype(str), "DUID": duid, "TOTALCLEARED": [1, 2, 3, 4]})


def _dudetail(connection_point: str = "CP1") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "DUID": "D1",
                "CONNECTIONPOINTID": connection_point,
                "EFFECTIVEDATE": "2026-01-01",
                "VERSIONNO": "1",
                "REGIONID": "NSW1",
            }
        ]
    )


def _spd() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"CONNECTIONPOINTID": "CP1", "GENCONID": "C1", "FACTOR": "1", "BIDTYPE": "ENERGY"},
            {"CONNECTIONPOINTID": "CP1", "GENCONID": "C2", "FACTOR": "1", "BIDTYPE": "ENERGY"},
        ]
    )


def test_recommendation_logic_accepts_valid_sparse_positive_window() -> None:
    thresholds = ScanThresholds(min_positive_samples=20, min_positive_rate=0.005, max_positive_rate=0.10)

    recommended, reason = recommend_candidate(
        status=STATUS_OK,
        relevant_genconid_count=4,
        positive_intervals=25,
        positive_rate=0.02,
        thresholds=thresholds,
    )

    assert recommended is True
    assert "meets" in reason


def test_zero_positive_sample_duid_is_not_recommended() -> None:
    result = summarise_duid_window(
        duid="D1",
        days=1,
        thresholds=ScanThresholds(min_positive_samples=1, min_positive_rate=0.01, max_positive_rate=0.50),
        dudetail=_dudetail(),
        spd_connectionpoint=_spd(),
        dispatchload=_dispatchload(),
        dispatchconstraint=pd.DataFrame(
            columns=["SETTLEMENTDATE", "CONSTRAINTID", "MARGINALVALUE", "VIOLATIONDEGREE", "INTERVENTION"]
        ),
    )

    assert result["status"] == STATUS_OK
    assert result["positive_intervals"] == 0
    assert result["recommended"] is False


def test_zero_positive_horizon_duid_is_not_recommended() -> None:
    result = summarise_duid_window(
        duid="D1",
        days=1,
        thresholds=ScanThresholds(min_positive_samples=1, min_positive_rate=0.01, max_positive_rate=0.10),
        horizon_thresholds=ScanThresholds(min_positive_samples=1, min_positive_rate=0.01, max_positive_rate=0.50),
        horizon_hours=1,
        dudetail=_dudetail(),
        spd_connectionpoint=_spd(),
        dispatchload=_dispatchload(),
        dispatchconstraint=pd.DataFrame(
            columns=["SETTLEMENTDATE", "CONSTRAINTID", "MARGINALVALUE", "VIOLATIONDEGREE", "INTERVENTION"]
        ),
    )

    assert result["horizon_hours"] == 1
    assert result["status"] == STATUS_OK
    assert result["positive_intervals"] == 0
    assert result["recommended"] is False


def test_horizon_label_marks_intervals_before_future_event() -> None:
    intervals = pd.date_range("2026-05-01 00:00:00", periods=15, freq="5min")
    labels = pd.DataFrame({"SETTLEMENTDATE": intervals, "target_constrained": [0] * 12 + [1, 0, 0]})

    out = add_horizon_labels(labels, [1])
    col = horizon_label_column(1)

    assert out.loc[0, col] == 1
    assert out.loc[11, col] == 1


def test_horizon_label_excludes_current_and_past_events() -> None:
    intervals = pd.date_range("2026-05-01 00:00:00", periods=15, freq="5min")
    labels = pd.DataFrame({"SETTLEMENTDATE": intervals, "target_constrained": [0] * 12 + [1, 0, 0]})

    out = add_horizon_labels(labels, [1])
    col = horizon_label_column(1)

    assert out.loc[12, col] == 0
    assert out.loc[13, col] == 0


def test_missing_connection_point_is_reported_without_crashing() -> None:
    result = summarise_duid_window(
        duid="D1",
        days=1,
        thresholds=ScanThresholds(),
        dudetail=_dudetail(connection_point=""),
        spd_connectionpoint=_spd(),
        dispatchload=_dispatchload(),
        dispatchconstraint=pd.DataFrame(),
    )

    assert result["status"] == STATUS_MISSING_CONNECTION_POINT
    assert result["recommended"] is False


def test_multiple_relevant_genconids_in_same_interval_count_as_one_positive_interval() -> None:
    interval = "2026-05-01 00:10:00"
    dispatchconstraint = pd.DataFrame(
        [
            {
                "SETTLEMENTDATE": interval,
                "CONSTRAINTID": "C1",
                "MARGINALVALUE": "0.2",
                "VIOLATIONDEGREE": "0",
                "INTERVENTION": "0",
            },
            {
                "SETTLEMENTDATE": interval,
                "CONSTRAINTID": "C2",
                "MARGINALVALUE": "0.1",
                "VIOLATIONDEGREE": "0",
                "INTERVENTION": "0",
            },
        ]
    )

    result = summarise_duid_window(
        duid="D1",
        days=1,
        thresholds=ScanThresholds(min_positive_samples=1, min_positive_rate=0.01, max_positive_rate=0.50),
        dudetail=_dudetail(),
        spd_connectionpoint=_spd(),
        dispatchload=_dispatchload(),
        dispatchconstraint=dispatchconstraint,
    )

    assert result["positive_intervals"] == 1
    assert result["unique_binding_genconid_count"] == 2
    assert result["recommended"] is True


def test_horizon_recommendation_uses_horizon_max_positive_rate() -> None:
    strict_thresholds = ScanThresholds(min_positive_samples=1, min_positive_rate=0.01, max_positive_rate=0.10)
    horizon_thresholds = ScanThresholds(min_positive_samples=1, min_positive_rate=0.01, max_positive_rate=0.25)

    current_recommended, current_reason = recommend_candidate(
        status=STATUS_OK,
        relevant_genconid_count=1,
        positive_intervals=20,
        positive_rate=0.20,
        thresholds=strict_thresholds,
    )
    horizon_recommended, horizon_reason = recommend_candidate(
        status=STATUS_OK,
        relevant_genconid_count=1,
        positive_intervals=20,
        positive_rate=0.20,
        thresholds=horizon_thresholds,
        horizon_hours=3,
    )

    assert current_recommended is False
    assert "above" in current_reason
    assert horizon_recommended is True
    assert "horizon" in horizon_reason


def test_rank_scan_results_puts_recommended_candidates_first() -> None:
    rows = pd.DataFrame(
        [
            {"duid": "D2", "days": 30, "recommended": False, "positive_intervals": 100, "positive_rate": 0.08},
            {"duid": "D1", "days": 30, "recommended": True, "positive_intervals": 20, "positive_rate": 0.02},
        ]
    )

    ranked = rank_scan_results(rows)

    assert ranked.iloc[0]["duid"] == "D1"


def test_cli_can_run_with_mocked_small_data(tmp_path: Path, monkeypatch) -> None:
    from scripts import find_constrained_duids as cli

    dispatchconstraint = pd.DataFrame(
        [
            {
                "SETTLEMENTDATE": "2026-05-01 00:10:00",
                "CONSTRAINTID": "C1",
                "MARGINALVALUE": "0.2",
                "VIOLATIONDEGREE": "0",
                "INTERVENTION": "0",
            }
        ]
    )
    standing = {"DUDETAIL": _dudetail(), "SPDCONNECTIONPOINTCONSTRAINT": _spd()}
    dispatch = {"DISPATCHLOAD": _dispatchload(), "DISPATCHCONSTRAINT": dispatchconstraint}

    monkeypatch.setattr(cli, "load_standing_data", lambda **kwargs: standing)
    monkeypatch.setattr(cli, "load_dispatch_tables_for_window", lambda **kwargs: dispatch)

    out = tmp_path / "scan.csv"
    result = cli.main(
        [
            "--days",
            "1",
            "--horizons-hours",
            "1",
            "--duids",
            "D1",
            "--out",
            str(out),
            "--top-out",
            str(tmp_path / "custom_top.csv"),
            "--min-positive-samples",
            "1",
            "--min-positive-rate",
            "0.01",
            "--max-positive-rate-horizon",
            "0.50",
        ]
    )

    top = tmp_path / "custom_top.csv"
    df = pd.read_csv(out)
    assert result == 0
    assert top.exists()
    assert df.loc[0, "duid"] == "D1"
    assert df.loc[0, "horizon_hours"] == 1
    assert bool(df.loc[0, "recommended"]) is True
