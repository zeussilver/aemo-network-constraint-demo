from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from src.aemo_constraint_demo.forecast import forecast_from_bundle

app = FastAPI(
    title="AEMO DUID Network Constraint Forecast Demo",
    description=(
        "Minimal demo service: query a trained model for a DUID's near-term constraint risk. "
        "The model is origin-anchored, so a call returns one probability for the horizon "
        "starting at the latest observed dispatch interval."
    ),
    version="0.2.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/forecast/{duid}")
def forecast_duid(
    duid: str,
    horizon_hours: int | None = Query(default=None, ge=1, le=72),
    model_path: str = Query(default="artifacts/model.joblib"),
    history_days: int = Query(
        default=2, ge=1, le=14, description="Trading days of dispatch history to load for lag features"
    ),
    source: str = Query(
        default="auto", description="Where dispatch files come from: auto, cache, or network"
    ),
    end_date: str | None = Query(
        default=None, description="Only use trading days on or before this date, e.g. 2026-05-18"
    ),
    use_weather: bool = Query(default=True),
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
        return forecast_from_bundle(
            path,
            duid=duid,
            horizon_hours=horizon_hours,
            history_days=history_days,
            source=source,
            end_date=end_date,
            use_weather=use_weather,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
