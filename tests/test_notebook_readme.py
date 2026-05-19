from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_notebook_is_cleared_and_contains_core_workflow_sections() -> None:
    notebook = json.loads((ROOT / "notebooks" / "aemo_constraint_forecast_demo.ipynb").read_text())
    text = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])

    for heading in [
        "## 1. Download standing-data tables",
        "## 2. Map DUID",
        "## 3. Download recent Next Day Dispatch files",
        "## 4. Build labels",
        "## 7. Train baseline model",
        "## 9. Query example",
    ]:
        assert heading in text

    assert 'read_monthly_table("GENCONSET"' in text
    assert '"planned_genconset": relevant_gset' in text

    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell.get("execution_count") is None
            assert cell.get("outputs") == []


def test_readme_documents_run_method_tests_limitations_and_extensions() -> None:
    readme = (ROOT / "README.md").read_text()

    for expected in [
        "python -m pytest",
        "uvicorn app:app --reload",
        "DUID -> CONNECTIONPOINTID -> GENCONID",
        "## Known limitations",
        "## Next production extensions",
        "GET /forecast/{duid}",
    ]:
        assert expected in readme
