from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "functions"))

import ingest_job  # noqa: E402 - needs the path above
from src.aemo_constraint_demo.ops_ingest import SOURCES, SourceResult  # noqa: E402

ENV = {"AZURE_POSTGRES_HOST": "db.example.net", "AZURE_POSTGRES_USER": "func-siteops-abc"}


def test_database_url_has_no_password_and_requires_tls() -> None:
    url = ingest_job.database_url(ENV)
    assert url == "postgresql://func-siteops-abc@db.example.net:5432/ops_console?sslmode=require"


def test_database_url_names_missing_settings() -> None:
    with pytest.raises(RuntimeError, match="AZURE_POSTGRES_USER"):
        ingest_job.database_url({"AZURE_POSTGRES_HOST": "db.example.net"})


def test_scheduled_ingest_passes_the_token_as_password_and_runs_every_source(tmp_path: Path) -> None:
    seen: dict = {}

    def runner(url, sources, config, password):
        seen.update(url=url, sources=sources, config=config, password=password)
        return [SourceResult(source) for source in sources]

    results = ingest_job.run_scheduled_ingest(ENV, token_provider=lambda: "entra-token", runner=runner, package_root=tmp_path)

    assert [r.source for r in results] == list(SOURCES)
    assert seen["password"] == "entra-token"
    assert seen["config"].trigger == "schedule"
    assert seen["config"].model_paths == [str(tmp_path / "artifacts" / "cathrock_lead_time_models.joblib")]
    assert seen["config"].cache_dir.name == "nemweb-cache"


def test_scheduled_ingest_raises_when_a_source_failed() -> None:
    def runner(url, sources, config, password):
        failed = SourceResult("dispatch")
        failed.errors.append("boom")
        return [SourceResult("scada"), failed]

    with pytest.raises(RuntimeError, match="dispatch"):
        ingest_job.run_scheduled_ingest(ENV, token_provider=lambda: "t", runner=runner)


def test_packaged_model_file_exists_in_the_repo() -> None:
    assert (ROOT / ingest_job.MODEL_FILE).is_file()
