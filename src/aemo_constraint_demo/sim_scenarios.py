"""Sandbox worlds: simulated farms, scripted scenarios, AEMO's publication cadence, and the
training tasks whose answers come from the generated data.

A world file (TOML, ``data/sim/*.toml``) lists the farms and the scenarios. Scenario events
are placed relative to an anchor time (normally "now" when the sandbox is loaded):

- ``day`` counts trading days back from the *reference day*, the latest one whose Next Day
  files AEMO would already have published at the anchor (``day = 0``);
- ``hours_before_anchor`` places an event on the live edge (SCADA, ingest runs).

Event kinds:

- ``cap``: a network constraint holds a farm's target at ``limit_share`` of capacity;
- ``weather``: forces a solar regime (clear / partly_cloudy / overcast) or a wind level
  (calm / normal / windy) for a trading day;
- ``self_forecast_suppressed``: AEMO suppresses every self-forecast submission, so dispatch
  falls back to AWEFS/ASEFS;
- ``scada_outage``: the farm's SCADA readings stop arriving;
- ``ingest_failure``: the latest ingest run of one source fails (the data is still there).

What the console can see at a time ``t`` follows NEMWeb: SCADA every 5 minutes; Next Day
Dispatch and Next Day Intermittent DS for trading day D only once AEMO publishes them,
shortly after D ends (``publication_minutes_after_day_end`` in the calibration).
"""
from __future__ import annotations

import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from .sim_engine import (
    AEMO_ORIGIN,
    INTERVAL,
    INTERVAL_HOURS,
    SOLAR_REGIMES,
    WIND_LEVELS,
    Calibration,
    CapWindow,
    Interval,
    SiteControls,
    SiteSpec,
    at_trading_time,
    floor_interval,
    nem,
    simulate,
    trading_day_end,
    trading_day_of,
    trading_day_start,
)

WORLDS_DIR = Path(__file__).resolve().parents[2] / "data" / "sim"
EVENT_KINDS = ("cap", "weather", "self_forecast_suppressed", "scada_outage", "ingest_failure")
TASK_KINDS = ("largest_curtailment", "scada_outage", "self_forecast_suppressed", "ingest_failure")
SOURCES = ("scada", "dispatch", "intermittent", "forecast")


@dataclass(frozen=True)
class Event:
    kind: str
    site: str | None = None
    day: int | None = None
    start: str | None = None  # HH:MM on the trading day
    hours: float | None = None
    hours_before_anchor: float | None = None
    limit_share: float | None = None
    weather: str | None = None
    source: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Task:
    kind: str
    prompt: str
    explanation: str


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    events: tuple[Event, ...]
    task: Task | None = None


@dataclass(frozen=True)
class World:
    name: str
    title: str
    seed: int
    history_days: int
    cap_rate_scale: float
    sites: tuple[SiteSpec, ...]
    scenarios: tuple[Scenario, ...]

    def site(self, key: str) -> SiteSpec:
        for site in self.sites:
            if site.key == key:
                return site
        raise KeyError(f"No site {key!r} in world {self.name!r}")


def _event(raw: dict, scenario: str, site_keys: set[str]) -> Event:
    unknown = set(raw) - set(Event.__dataclass_fields__)
    if unknown:
        raise ValueError(f"{scenario}: unknown event fields {sorted(unknown)}")
    event = Event(**raw)
    where = f"{scenario}/{event.kind}"
    if event.kind not in EVENT_KINDS:
        raise ValueError(f"{scenario}: event kind must be one of {EVENT_KINDS}, not {event.kind!r}")
    if event.kind != "ingest_failure" and event.site not in site_keys:
        raise ValueError(f"{where}: unknown site {event.site!r}")
    if event.kind in ("cap", "self_forecast_suppressed"):
        if event.day is None or event.start is None or not event.hours or event.hours <= 0:
            raise ValueError(f"{where}: needs day, start (HH:MM) and hours > 0")
    if event.kind == "cap" and not (event.limit_share is not None and 0 <= event.limit_share < 1):
        raise ValueError(f"{where}: limit_share must be in [0, 1)")
    if event.kind == "weather":
        if event.day is None or event.weather is None:
            raise ValueError(f"{where}: needs day and weather")
    if event.kind in ("scada_outage", "ingest_failure") and not (event.hours_before_anchor and event.hours_before_anchor > 0):
        raise ValueError(f"{where}: needs hours_before_anchor > 0")
    if event.kind == "ingest_failure" and (event.source not in SOURCES or not event.error):
        raise ValueError(f"{where}: needs source (one of {SOURCES}) and error")
    if event.day is not None and event.day > 0:
        raise ValueError(f"{where}: day counts back from the reference day, so it must be 0 or negative")
    return event


def parse_world(raw: dict, name: str) -> World:
    sites = tuple(SiteSpec(**site) for site in raw.get("sites", []))
    keys = {site.key for site in sites}
    if len(keys) != len(sites) or len({site.duid for site in sites}) != len(sites):
        raise ValueError(f"{name}: site keys and DUIDs must be unique")
    scenarios = []
    for item in raw.get("scenarios", []):
        task = item.get("task")
        if task is not None:
            task = Task(**task)
            if task.kind not in TASK_KINDS:
                raise ValueError(f"{item['key']}: task kind must be one of {TASK_KINDS}")
        events = tuple(_event(event, item["key"], keys) for event in item.get("events", []))
        scenarios.append(Scenario(item["key"], item["title"], item["description"].strip(), events, task))
    world = World(
        name=raw.get("name", name),
        title=raw.get("title", name),
        seed=int(raw["seed"]),
        history_days=int(raw.get("history_days", 3)),
        cap_rate_scale=float(raw.get("cap_rate_scale", 1.0)),
        sites=sites,
        scenarios=tuple(scenarios),
    )
    if not 1 <= world.history_days <= 14:
        raise ValueError(f"{name}: history_days must be between 1 and 14")
    return world


def load_world(name_or_path: str | Path) -> World:
    path = Path(name_or_path)
    if not path.suffix:
        path = WORLDS_DIR / f"{name_or_path}.toml"
    return parse_world(tomllib.loads(path.read_text()), path.stem)


# ---------------------------------------------------------------------------------------
# Anchoring: scenario events -> concrete times and per-farm controls
# ---------------------------------------------------------------------------------------


def published_until(anchor: datetime, source: str, calibration: Calibration) -> datetime:
    """Newest interval end of ``source`` that AEMO has published by ``anchor``."""
    anchor = nem(anchor)
    if source == "scada":
        return floor_interval(anchor)
    lag = timedelta(minutes=calibration.publication_minutes[source])
    # The interval after (anchor - lag) belongs to the trading day still running then, so
    # the day before it is the latest one complete and published.
    day = trading_day_of(floor_interval(anchor - lag) + INTERVAL) - timedelta(days=1)
    return trading_day_end(day)


def reference_day(anchor: datetime, calibration: Calibration) -> date:
    """Latest trading day whose Next Day Dispatch and Intermittent files are both out."""
    return trading_day_of(min(published_until(anchor, source, calibration) for source in ("dispatch", "intermittent")))


@dataclass
class Anchored:
    """A world placed at an anchor: the controls the engine needs and the live-edge events."""

    world: World
    anchor: datetime
    reference_day: date
    start: datetime
    controls: dict[str, SiteControls]
    scada_outages: dict[str, datetime]  # DUID -> last interval end SCADA reports
    ingest_failures: list[tuple[str, datetime, str]]  # (source, failed at, error)
    windows: dict[str, list[tuple[str, datetime, datetime]]] = field(default_factory=dict)  # scenario -> (site, start, end)


def anchor_world(world: World, anchor: datetime, calibration: Calibration) -> Anchored:
    anchor = nem(anchor)
    ref = reference_day(anchor, calibration)
    controls: dict[str, SiteControls] = defaultdict(SiteControls)
    outages: dict[str, datetime] = {}
    failures: list[tuple[str, datetime, str]] = []
    windows: dict[str, list] = defaultdict(list)
    for scenario in world.scenarios:
        for event in scenario.events:
            site = world.site(event.site) if event.site else None
            day = ref + timedelta(days=event.day) if event.day is not None else None
            if event.kind in ("cap", "self_forecast_suppressed"):
                start = at_trading_time(day, event.start)
                end = start + timedelta(hours=event.hours)
                if end > trading_day_end(day):
                    raise ValueError(f"{scenario.key}: the {event.kind} window runs past the end of its trading day")
                if event.kind == "cap":
                    controls[site.duid].caps.append(CapWindow(start, end, event.limit_share))
                else:
                    if not site.participant:
                        raise ValueError(f"{scenario.key}: {site.key} has no self-forecast to suppress")
                    controls[site.duid].suppressed.append((start, end))
                windows[scenario.key].append((site.key, start, end))
            elif event.kind == "weather":
                allowed = SOLAR_REGIMES if site.fuel == "SOLAR" else tuple(WIND_LEVELS)
                if event.weather not in allowed:
                    raise ValueError(f"{scenario.key}: weather for {site.fuel} must be one of {allowed}")
                controls[site.duid].weather[day] = event.weather
            elif event.kind == "scada_outage":
                outages[site.duid] = floor_interval(anchor - timedelta(hours=event.hours_before_anchor))
            elif event.kind == "ingest_failure":
                failures.append((event.source, anchor - timedelta(hours=event.hours_before_anchor), event.error))
    start = trading_day_start(ref - timedelta(days=world.history_days))
    return Anchored(world, anchor, ref, start, dict(controls), outages, failures, dict(windows))


def generate(anchored: Anchored, calibration: Calibration, until: datetime | None = None) -> dict[str, list[Interval]]:
    """Every farm's intervals from the history start up to ``until`` (default: the anchor)."""
    until = nem(until or anchored.anchor)
    world = anchored.world
    return {
        site.duid: list(
            simulate(site, calibration, world.seed, anchored.start, until, anchored.controls.get(site.duid), world.cap_rate_scale)
        )
        for site in world.sites
    }


# ---------------------------------------------------------------------------------------
# Publication: intervals -> the rows the ingest job would have written by a given time
# ---------------------------------------------------------------------------------------


def published_rows(
    anchored: Anchored, intervals: dict[str, list[Interval]], calibration: Calibration, at: datetime
) -> dict[str, list[tuple]]:
    """Rows per ops table, in the column order of ``ops_ingest.UPSERTS``, visible at ``at``."""
    limits = {source: published_until(at, source, calibration) for source in ("scada", "dispatch", "intermittent")}
    rows: dict[str, list[tuple]] = {"scada": [], "dispatch": [], "forecast_used": [], "forecast_origin": []}
    sites = {site.duid: site for site in anchored.world.sites}
    for duid, series in intervals.items():
        site = sites[duid]
        priority = calibration.raw["forecast"][site.fuel]["priority"]
        priority_of = {AEMO_ORIGIN: priority["aemo"], site.participant: priority["self"]}
        outage = anchored.scada_outages.get(duid)
        for item in series:
            # SCADA labelled ``end`` reports the output at the start of the interval.
            if item.end <= limits["scada"] and (outage is None or item.end <= outage):
                rows["scada"].append((duid, item.end, item.initial))
            if item.end <= limits["dispatch"]:
                rows["dispatch"].append(
                    (duid, item.end, 0, item.initial, item.target, item.uigf, item.uigf, 1 if item.capped else 0)
                )
            if item.end <= limits["intermittent"]:
                offer = item.end - timedelta(minutes=10)
                used_priority = priority_of.get(item.origin, priority.get("fallback", -1))
                rows["forecast_used"].append((duid, item.end, item.origin, used_priority, offer, item.uigf))
                rows["forecast_origin"].append(
                    (duid, item.end, AEMO_ORIGIN, item.aemo_submissions, 0, 0, priority["aemo"], item.aemo_forecast)
                )
                if item.self_submissions:
                    rows["forecast_origin"].append(
                        (
                            duid,
                            item.end,
                            site.participant,
                            item.self_submissions,
                            item.self_suppressed_aemo,
                            item.self_suppressed_participant,
                            priority["self"],
                            item.self_forecast,
                        )
                    )
    return rows


def ingest_runs(anchored: Anchored, rows: dict[str, list[tuple]], calibration: Calibration, at: datetime) -> list[dict]:
    """``ingest_run`` rows a scheduled job would have left: hourly SCADA loads over the last
    day, one Next Day load per published trading day, then any scripted failures."""
    at = nem(at)
    runs: list[dict] = []
    scada_times = sorted({row[1] for row in rows["scada"]})
    hour = at.replace(minute=0, second=0, microsecond=0)
    for back in range(23, -1, -1):
        finished = hour - timedelta(hours=back) + timedelta(minutes=2)
        if finished > at or finished < anchored.start:
            continue
        loaded = [t for t in scada_times if finished - timedelta(hours=1, minutes=2) < t <= finished - timedelta(minutes=2)]
        runs.append(_run("scada", finished, len(loaded) * len(anchored.world.sites), max(loaded, default=None)))
    for source, table in (("dispatch", "dispatch"), ("intermittent", "forecast_used")):
        lag = timedelta(minutes=calibration.publication_minutes[source])
        days = sorted({trading_day_of(row[1]) for row in rows[table]})
        for day in days:
            count = sum(1 for row in rows[table] if trading_day_of(row[1]) == day)
            runs.append(_run(source, trading_day_end(day) + lag + timedelta(minutes=3), count, trading_day_end(day)))
    for source, failed_at, error in anchored.ingest_failures:
        if failed_at <= at:
            # The scripted failure is that source's latest run: drop scheduled runs after it.
            runs = [run for run in runs if run["source"] != source or run["finished_at"] < failed_at]
            runs.append({**_run(source, failed_at, 0, None), "status": "failed", "error": error})
    return sorted(runs, key=lambda run: run["started_at"])


def _run(source: str, finished: datetime, rows: int, latest: datetime | None) -> dict:
    return {
        "source": source,
        "trigger": "simulator",
        "status": "success",
        "started_at": finished - timedelta(seconds=40),
        "finished_at": finished,
        "files_processed": 1 if rows else 0,
        "rows_written": rows,
        "latest_interval": latest,
        "error": None,
    }


# ---------------------------------------------------------------------------------------
# Ground truth and training tasks
# ---------------------------------------------------------------------------------------


def curtailment_by_site(rows: dict[str, list[tuple]], day: date) -> dict[str, dict]:
    """The console's curtailment estimate per DUID for one trading day (ops-math.ts)."""
    totals: dict[str, dict] = defaultdict(lambda: {"curtailed_mwh": 0.0, "capped_intervals": 0, "intervals": 0})
    for duid, end, _, _, cleared, _, uigf, cap in rows["dispatch"]:
        if trading_day_of(end) != day:
            continue
        entry = totals[duid]
        entry["intervals"] += 1
        if cap == 1:
            entry["capped_intervals"] += 1
            entry["curtailed_mwh"] += max(0.0, uigf - cleared) * INTERVAL_HOURS
    return {duid: {**value, "curtailed_mwh": round(value["curtailed_mwh"], 1)} for duid, value in totals.items()}


def _clock(moment: datetime) -> str:
    return nem(moment).strftime("%H:%M")


def build_tasks(anchored: Anchored, rows: dict[str, list[tuple]], runs: Iterable[dict]) -> list[dict]:
    """One task per scenario that has one, with the answer read from the published rows, so
    it always matches what the console shows."""
    world = anchored.world
    site_of = {site.duid: site for site in world.sites}
    day = anchored.reference_day
    tasks = []
    for scenario in world.scenarios:
        if scenario.task is None:
            continue
        task = scenario.task
        fields: list[dict]
        if task.kind == "largest_curtailment":
            curtailment = curtailment_by_site(rows, day)
            duid = max(curtailment, key=lambda key: curtailment[key]["curtailed_mwh"])
            mwh = curtailment[duid]["curtailed_mwh"]
            fields = [
                {"name": "site", "label": "Site", "type": "site"},
                {"name": "mwh", "label": "Curtailment estimate (MWh)", "type": "number", "tolerance": max(1.0, round(0.1 * mwh, 1))},
            ]
            answer = {"site": site_of[duid].key, "mwh": mwh}
        elif task.kind == "scada_outage":
            latest = {duid: max(row[1] for row in rows["scada"] if row[0] == duid) for duid in site_of if any(row[0] == duid for row in rows["scada"])}
            duid = min(latest, key=latest.get)
            fields = [
                {"name": "site", "label": "Site", "type": "site"},
                {"name": "time", "label": "Time of its last reading (HH:MM, NEM time)", "type": "time", "tolerance_minutes": 5},
            ]
            answer = {"site": site_of[duid].key, "time": _clock(latest[duid])}
        elif task.kind == "self_forecast_suppressed":
            suppressed = defaultdict(int)
            for duid, end, _, _, aemo, *_ in rows["forecast_origin"]:
                if trading_day_of(end) == day:
                    suppressed[duid] += aemo
            duid = max(suppressed, key=suppressed.get)
            fields = [
                {"name": "site", "label": "Site", "type": "site"},
                {
                    "name": "fallback",
                    "label": "Forecast dispatch used instead",
                    "type": "choice",
                    "options": ["Self-forecast (participant)", "AEMO AWEFS/ASEFS", "Fallback (SCADA / last target)"],
                },
            ]
            answer = {"site": site_of[duid].key, "fallback": "AEMO AWEFS/ASEFS"}
        else:  # ingest_failure
            failed = [run for run in runs if run["status"] == "failed"]
            if not failed:
                continue
            latest_failure = max(failed, key=lambda run: run["started_at"])
            fields = [{"name": "source", "label": "Source", "type": "choice", "options": list(SOURCES)}]
            answer = {"source": latest_failure["source"]}
        tasks.append(
            {
                "id": scenario.key,
                "title": scenario.title,
                "kind": task.kind,
                "prompt": task.prompt.replace("{day}", day.isoformat()),
                "fields": fields,
                "answer": answer,
                "explanation": task.explanation.strip().replace("{day}", day.isoformat()),
            }
        )
    return tasks


def manifest(anchored: Anchored, rows: dict[str, list[tuple]], runs: list[dict]) -> dict:
    """What ``sim_run.manifest`` stores: enough for the training page and for audits."""
    world = anchored.world
    return {
        "world": world.name,
        "title": world.title,
        "seed": world.seed,
        "anchor": anchored.anchor.isoformat(),
        "reference_day": anchored.reference_day.isoformat(),
        "history_start": anchored.start.isoformat(),
        "sites": [
            {"key": s.key, "duid": s.duid, "name": s.name, "fuel": s.fuel, "region": s.region, "capacity_mw": s.capacity_mw}
            for s in world.sites
        ],
        "scenarios": [
            {
                "key": scenario.key,
                "title": scenario.title,
                "description": scenario.description,
                "windows": [
                    {"site": site, "start": start.isoformat(), "end": end.isoformat()}
                    for site, start, end in anchored.windows.get(scenario.key, [])
                ],
            }
            for scenario in world.scenarios
        ],
        "tasks": build_tasks(anchored, rows, runs),
        "curtailment_reference_day": curtailment_by_site(rows, anchored.reference_day),
    }
