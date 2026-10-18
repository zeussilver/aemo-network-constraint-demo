from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.aemo_constraint_demo import frames as frames_module
from src.aemo_constraint_demo.frames import build_model_frame
from src.aemo_constraint_demo.scan import add_lead_time_labels

DUID = "D1"
FEATURE_SET = "time_weather_dispatch_lag"


def synthetic_dispatch(periods: int = 288, start: str = "2026-05-01 04:05:00") -> dict[str, pd.DataFrame]:
    """A day of 5-minute dispatch for one DUID, with a short binding episode."""
    timestamps = pd.date_range(start, periods=periods, freq="5min")
    cleared = np.round(10 + 5 * np.sin(np.linspace(0, 6, periods)), 2)
    dispatchload = pd.DataFrame(
        {
            "SETTLEMENTDATE": timestamps.astype(str),
            "DUID": [DUID] * periods,
            "TOTALCLEARED": cleared.astype(str),
            "INITIALMW": np.round(cleared + 0.3, 2).astype(str),
            "AVAILABILITY": np.round(cleared + 2.0, 2).astype(str),
        }
    )
    binding = timestamps[100:110]
    dispatchconstraint = pd.DataFrame(
        {
            "SETTLEMENTDATE": binding.astype(str),
            "CONSTRAINTID": ["C1"] * len(binding),
            "MARGINALVALUE": ["5.0"] * len(binding),
            "VIOLATIONDEGREE": ["0"] * len(binding),
            "INTERVENTION": ["0"] * len(binding),
        }
    )
    return {"DISPATCHLOAD": dispatchload, "DISPATCHCONSTRAINT": dispatchconstraint}


def _weather_for(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    hours = pd.date_range("2026-05-01 00:00:00", periods=48, freq="h")
    return pd.DataFrame(
        {
            "time": hours,
            "temperature_2m": np.linspace(8.0, 26.0, len(hours)),
            "wind_speed_10m": np.linspace(2.0, 14.0, len(hours)),
        }
    )


@pytest.fixture
def offline_frames(monkeypatch):
    tables = synthetic_dispatch()
    monkeypatch.setattr(frames_module, "load_next_day_dispatch_tables", lambda **kwargs: tables)
    monkeypatch.setattr(frames_module, "fetch_open_meteo_history", lambda *args, **kwargs: _weather_for(tables))
    return tables


def _build(**overrides):
    params = {
        "duid": DUID,
        "days": 1,
        "horizon_hours": 6,
        "feature_set": FEATURE_SET,
        "latitude": -27.4,
        "longitude": 151.7,
        "relevant_genconids": ["C1"],
        "connection_point": "CP1",
    }
    params.update(overrides)
    return build_model_frame(**params)


def test_training_and_serving_frames_agree_on_the_same_origin(offline_frames) -> None:
    """The regression guard for train/serve skew.

    A serving call keeps the incomplete-horizon tail so it can score the newest interval.
    Every feature it hands the model for a shared origin must equal what training would
    have computed for that same interval.
    """
    training = _build(drop_incomplete_horizon=True)
    serving = _build(drop_incomplete_horizon=False)

    origin = training.frame["SETTLEMENTDATE"].iloc[-1]
    assert serving.feature_columns == training.feature_columns

    train_row = training.frame[training.frame["SETTLEMENTDATE"] == origin][training.feature_columns]
    serve_row = serving.frame[serving.frame["SETTLEMENTDATE"] == origin][serving.feature_columns]

    assert len(train_row) == 1 and len(serve_row) == 1
    pd.testing.assert_frame_equal(
        train_row.reset_index(drop=True),
        serve_row.reset_index(drop=True),
        check_dtype=False,
    )
    # The tail training cannot label is exactly what serving keeps.
    assert len(serving.frame) - len(training.frame) == 6 * 12


def test_serving_frame_exposes_a_fully_observed_origin_row(offline_frames) -> None:
    serving = _build(drop_incomplete_horizon=False)
    origin_row = serving.frame.iloc[-1][serving.feature_columns]

    assert not origin_row.isna().any(), sorted(origin_row[origin_row.isna()].index)
    assert "totalcleared_lag_6h" in serving.feature_columns
    assert "temperature_2m" in serving.feature_columns


def test_horizon_label_is_future_only(offline_frames) -> None:
    training = _build()

    assert training.target_column == "target_constrained_next_6h"
    labelled = training.frame.set_index("SETTLEMENTDATE")
    first_binding = pd.Timestamp("2026-05-01 04:05:00") + pd.Timedelta(minutes=5 * 100)
    # The interval right before the episode sees it inside its 6h horizon...
    assert labelled.loc[first_binding - pd.Timedelta(minutes=5), "target_constrained_next_6h"] == 1
    # ...and the current interval is excluded from its own horizon label.
    assert labelled.loc[first_binding - pd.Timedelta(minutes=5), "target_constrained"] == 0


def test_build_model_frame_requires_a_constraint_mapping(offline_frames) -> None:
    with pytest.raises(ValueError, match="relevant_genconids"):
        build_model_frame(duid=DUID, days=1, horizon_hours=6, relevant_genconids=None)


def test_weather_failure_is_fatal_for_training_but_degrades_for_serving(monkeypatch) -> None:
    tables = synthetic_dispatch()
    monkeypatch.setattr(frames_module, "load_next_day_dispatch_tables", lambda **kwargs: tables)

    def boom(*args, **kwargs):
        raise RuntimeError("open-meteo down")

    monkeypatch.setattr(frames_module, "fetch_open_meteo_history", boom)

    with pytest.raises(RuntimeError, match="open-meteo down"):
        _build(weather_required=True)

    serving = _build(weather_required=False, drop_incomplete_horizon=False)
    assert serving.metadata["weather_used"] is False
    assert "open-meteo down" in serving.metadata["weather_error"]
    assert serving.frame["temperature_2m"].isna().all()


def test_preloaded_dispatch_tables_skip_the_download(monkeypatch) -> None:
    tables = synthetic_dispatch()

    def fail(**kwargs):  # pragma: no cover - the builder must not reach this
        raise AssertionError("dispatch files should not be downloaded")

    monkeypatch.setattr(frames_module, "load_next_day_dispatch_tables", fail)

    result = _build(
        no_weather=True,
        dispatchload=tables["DISPATCHLOAD"],
        dispatchconstraint=tables["DISPATCHCONSTRAINT"],
    )

    assert result.connection_point == "CP1"
    assert result.relevant_genconids == ["C1"]


def test_lead_time_buckets_tile_the_horizon_without_overlap(offline_frames) -> None:
    result = _build(lead_hours=[1, 2, 3, 4, 5, 6])
    frame = result.frame

    assert result.lead_target_columns == {lead: f"target_constrained_lead_{lead}h" for lead in range(1, 7)}
    assert result.metadata["lead_hours"] == [1, 2, 3, 4, 5, 6]

    bucket_columns = list(result.lead_target_columns.values())
    # "binds in some bucket" must be exactly "binds somewhere in the horizon".
    union = frame[bucket_columns].max(axis=1).astype(int)
    assert (union == frame[result.target_column].astype(int)).all()


def test_lead_time_buckets_are_dropped_with_the_horizon_tail(offline_frames) -> None:
    training = _build(lead_hours=[1, 6])
    serving = _build(lead_hours=[1, 6], drop_incomplete_horizon=False)

    # Training keeps only fully-observed buckets; the horizon tail is the widest.
    assert not training.frame[list(training.lead_target_columns.values())].isna().any().any()
    assert serving.frame[serving.lead_target_columns[6]].isna().sum() == 6 * 12


def test_lead_hours_must_fit_inside_the_horizon(offline_frames) -> None:
    with pytest.raises(ValueError, match="must fall inside horizon_hours"):
        _build(horizon_hours=6, lead_hours=[1, 8])


def test_a_single_binding_interval_falls_in_exactly_one_bucket() -> None:
    """The sharp disjointness property, isolated from any binding episode."""
    periods = 120
    bind_at = 30  # 30 intervals ahead of origin 0 is 2.5h, so bucket 3: (2h, 3h]
    labels = pd.DataFrame(
        {
            "SETTLEMENTDATE": pd.date_range("2026-05-01", periods=periods, freq="5min"),
            "target_constrained": [1 if index == bind_at else 0 for index in range(periods)],
        }
    )

    out = add_lead_time_labels(labels, [1, 2, 3, 4], intervals_per_hour=12)
    columns = [f"target_constrained_lead_{lead}h" for lead in (1, 2, 3, 4)]

    assert out[columns].sum(axis=1).max() == 1
    assert out.loc[0, "target_constrained_lead_3h"] == 1
    # An origin one interval before the bind sees it in the first bucket...
    assert out.loc[bind_at - 1, "target_constrained_lead_1h"] == 1
    # ...and the binding interval itself is excluded from its own buckets.
    assert out.loc[bind_at, columns].sum() == 0
