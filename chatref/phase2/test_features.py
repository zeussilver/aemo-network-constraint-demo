from __future__ import annotations

import pandas as pd
import pytest

from src.aemo_constraint_demo.features import (
    add_time_features,
    build_label_frame,
    relevant_constraints_for_duid,
    select_feature_columns,
)


def test_relevant_constraints_for_duid_uses_current_connection_point_and_energy_terms() -> None:
    dudetail = pd.DataFrame(
        [
            {"DUID": "D1", "CONNECTIONPOINTID": "OLD_CP", "EFFECTIVEDATE": "2024-01-01", "VERSIONNO": "1"},
            {"DUID": "D1", "CONNECTIONPOINTID": "CP1", "EFFECTIVEDATE": "2025-01-01", "VERSIONNO": "1"},
            {"DUID": "D1", "CONNECTIONPOINTID": "CP2", "EFFECTIVEDATE": "2025-01-01", "VERSIONNO": "2"},
        ]
    )
    spd = pd.DataFrame(
        [
            {"CONNECTIONPOINTID": "CP2", "GENCONID": "C_NETWORK", "FACTOR": "1", "BIDTYPE": "ENERGY"},
            {"CONNECTIONPOINTID": "CP2", "GENCONID": "C_ZERO", "FACTOR": "0", "BIDTYPE": "ENERGY"},
            {"CONNECTIONPOINTID": "CP2", "GENCONID": "C_FCAS", "FACTOR": "1", "BIDTYPE": "RAISE6SEC"},
            {"CONNECTIONPOINTID": "OLD_CP", "GENCONID": "C_OLD", "FACTOR": "1", "BIDTYPE": "ENERGY"},
        ]
    )

    constraints, connection_point, rows = relevant_constraints_for_duid("d1", dudetail, spd)

    assert connection_point == "CP2"
    assert constraints == ["C_NETWORK"]
    assert rows["GENCONID"].tolist() == ["C_NETWORK"]


def test_relevant_constraints_keeps_fcas_when_no_energy_terms_exist() -> None:
    dudetail = pd.DataFrame([{"DUID": "D1", "CONNECTIONPOINTID": "CP1"}])
    spd = pd.DataFrame([{"CONNECTIONPOINTID": "CP1", "GENCONID": "C_FCAS", "FACTOR": "1", "BIDTYPE": "RAISE6SEC"}])

    constraints, _, _ = relevant_constraints_for_duid("D1", dudetail, spd)

    assert constraints == ["C_FCAS"]


def test_relevant_constraints_reports_missing_mapping() -> None:
    dudetail = pd.DataFrame([{"DUID": "D1", "CONNECTIONPOINTID": "CP1"}])
    spd = pd.DataFrame([{"CONNECTIONPOINTID": "OTHER", "GENCONID": "C1", "FACTOR": "1"}])

    with pytest.raises(ValueError, match="No SPDCONNECTIONPOINTCONSTRAINT rows"):
        relevant_constraints_for_duid("D1", dudetail, spd)


def test_build_label_frame_marks_binding_relevant_constraints_only() -> None:
    dispatchload = pd.DataFrame(
        [
            {"SETTLEMENTDATE": "2026-05-01 00:05:00", "DUID": "D1", "TOTALCLEARED": "10"},
            {"SETTLEMENTDATE": "2026-05-01 00:10:00", "DUID": "D1", "TOTALCLEARED": "11"},
            {"SETTLEMENTDATE": "2026-05-01 00:15:00", "DUID": "D1", "TOTALCLEARED": "12"},
            {"SETTLEMENTDATE": "2026-05-01 00:10:00", "DUID": "D2", "TOTALCLEARED": "99"},
        ]
    )
    dispatchconstraint = pd.DataFrame(
        [
            {"SETTLEMENTDATE": "2026-05-01 00:05:00", "CONSTRAINTID": "OTHER", "MARGINALVALUE": "9", "VIOLATIONDEGREE": "0", "INTERVENTION": "0"},
            {"SETTLEMENTDATE": "2026-05-01 00:10:00", "CONSTRAINTID": "C1", "MARGINALVALUE": "0.2", "VIOLATIONDEGREE": "0", "INTERVENTION": "0"},
            {"SETTLEMENTDATE": "2026-05-01 00:15:00", "CONSTRAINTID": "C1", "MARGINALVALUE": "0", "VIOLATIONDEGREE": "1", "INTERVENTION": "1"},
        ]
    )

    frame = build_label_frame(dispatchload, dispatchconstraint, "D1", ["C1"])

    assert frame["target_constrained"].tolist() == [0, 1, 0]
    assert frame["binding_constraint_ids"].tolist() == ["", "C1", ""]
    assert frame["DUID"].tolist() == ["D1", "D1", "D1"]


def test_time_feature_selection_includes_only_available_columns() -> None:
    frame = add_time_features(pd.DataFrame({"SETTLEMENTDATE": ["2026-05-02 12:30:00"]}))
    frame["temperature_2m"] = 21.5
    frame["TOTALCLEARED"] = 42
    frame["target_constrained"] = 1
    frame["target_constrained_next_6h"] = 1
    frame["binding_constraint_ids"] = "C1"

    assert "is_weekend" in frame.columns
    assert frame.loc[0, "is_weekend"] == 1
    assert "TOTALCLEARED" not in select_feature_columns(frame)
    assert "TOTALCLEARED" in select_feature_columns(frame, include_dispatch_features=True)
    assert "target_constrained" not in select_feature_columns(frame, include_dispatch_features=True)
    assert "target_constrained_next_6h" not in select_feature_columns(frame, include_dispatch_features=True)
    assert "binding_constraint_ids" not in select_feature_columns(frame, include_dispatch_features=True)
