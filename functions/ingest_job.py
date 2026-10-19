"""Scheduled NEMWeb ingest, run inside Azure by the Functions timer in function_app.py.

The function app signs in to PostgreSQL as its own managed identity: the Entra access
token is the password, so no secret exists. Data files are cached in the instance's temp
directory; that cache is disposable because the database records every file it has
loaded (``ingested_file``), so a fresh instance only downloads what is new.

Kept free of ``azure.functions`` so tests can import it without the Functions runtime.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import quote

from src.aemo_constraint_demo.ops_ingest import SOURCES, IngestConfig, SourceResult, run

POSTGRES_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
PACKAGE_ROOT = Path(__file__).resolve().parent
MODEL_FILE = "artifacts/cathrock_lead_time_models.joblib"

Runner = Callable[..., Sequence[SourceResult]]


def database_url(env: Mapping[str, str]) -> str:
    """Connection URL for the function's own database role (no password in it)."""
    missing = [name for name in ("AZURE_POSTGRES_HOST", "AZURE_POSTGRES_USER") if not env.get(name)]
    if missing:
        raise RuntimeError(f"Missing app settings: {', '.join(missing)}")
    user = quote(env["AZURE_POSTGRES_USER"], safe="")
    database = env.get("AZURE_POSTGRES_DATABASE", "ops_console")
    return f"postgresql://{user}@{env['AZURE_POSTGRES_HOST']}:5432/{database}?sslmode=require"


def managed_identity_token() -> str:
    from azure.identity import ManagedIdentityCredential

    return ManagedIdentityCredential().get_token(POSTGRES_TOKEN_SCOPE).token


def run_scheduled_ingest(
    env: Mapping[str, str] = os.environ,
    token_provider: Callable[[], str] = managed_identity_token,
    runner: Runner = run,
    package_root: Path = PACKAGE_ROOT,
) -> Sequence[SourceResult]:
    """Run every source once. Raises when a source failed, so the Functions host records
    the invocation as failed too (each source run is also an ``ingest_run`` event)."""
    config = IngestConfig(
        cache_dir=Path(tempfile.gettempdir()) / "nemweb-cache",
        trigger="schedule",
        model_paths=[str(package_root / MODEL_FILE)],
    )
    results = runner(database_url(env), SOURCES, config, password=token_provider())
    failed = [result.source for result in results if result.status == "failed"]
    if failed:
        raise RuntimeError(f"NEMWeb ingest failed for: {', '.join(failed)}")
    return results
