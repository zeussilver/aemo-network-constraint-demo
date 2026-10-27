from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_notebook_is_cleared_and_contains_core_workflow_sections() -> None:
    notebook = json.loads((ROOT / "notebooks" / "aemo_constraint_forecast_demo.ipynb").read_text())
    text = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])

    for heading in [
        "## 1. Target discovery summary",
        "## 2. Map DUID -> connection point -> GENCONID",
        "## 3. Horizon label definition",
        "## 4. Training command",
        "## 6. Event-window backtest summary",
        "## 7. API query examples",
        "## 8. Limitations",
    ]:
        assert heading in text

    assert 'read_monthly_table("GENCONSET"' in text
    assert "target_constrained_next_6h" in text
    assert "backtest_horizon_model.py" in text

    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell.get("execution_count") is None
            assert cell.get("outputs") == []


def test_readme_and_forecasting_doc_document_run_method_limitations_and_extensions() -> None:
    readme = (ROOT / "README.md").read_text()
    doc = (ROOT / "docs" / "constraint-forecasting.md").read_text()

    for expected in [
        "python -m pytest",
        "uvicorn app:app --reload",
        "DUID -> CONNECTIONPOINTID -> GENCONID",
        "GET /forecast/{duid}",
        "docs/constraint-forecasting.md",
    ]:
        assert expected in readme

    for expected in [
        "scripts/backtest_horizon_model.py",
        "Phase 2.5",
        "DUID -> CONNECTIONPOINTID -> GENCONID",
        "## Known limitations",
        "## Next production extensions",
        "GET /forecast/{duid}",
    ]:
        assert expected in doc
