from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from src.aemo_constraint_demo.forecast import forecast_from_bundle

app = FastAPI(
    title="AEMO DUID Network Constraint Forecast Demo",
    description="Minimal demo service: query a trained model for a DUID's near-term constraint risk.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/forecast/{duid}")
def forecast_duid(
    duid: str,
    horizon_hours: int | None = Query(default=None, ge=1, le=72),
    model_path: str = Query(default="artifacts/model.joblib"),
    start: str | None = Query(default=None, description="Optional start timestamp, e.g. 2026-05-20 08:00"),
) -> dict:
    path = Path(model_path)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Model file not found: {model_path}. Train first, for example: "
                "python -m src.aemo_constraint_demo.train --duid CATHROCK --days 60 "
                "--horizon-hours 6 --model-out artifacts/cathrock_6h.joblib"
            ),
        )
    try:
        return forecast_from_bundle(path, duid=duid, horizon_hours=horizon_hours, start=start)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
