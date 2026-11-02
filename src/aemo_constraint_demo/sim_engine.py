"""Simulated solar and wind farms for the console's sandbox, calibrated on real NEMWeb data.

The engine produces, per farm and per 5-minute dispatch interval, what the ops console would
see for a real semi-scheduled unit:

- a smooth weather signal, which is what a forecast can see;
- a participant self-forecast (the signal) and AEMO's AWEFS/ASEFS forecast (the signal plus a
  slow extra error), and which one dispatch used, whose POE50 becomes the UIGF;
- the power actually available, which misses the forecast by the calibrated forecast error
  (latent: AEMO never publishes it);
- the dispatch target, held under a semi-dispatch cap during constraint episodes;
- what the farm then produced, which the next SCADA reading reports.

Every number comes from ``artifacts/sim_calibration.json`` (``scripts/sim_calibrate.py``):
quantile tables are sampled through a Gaussian copula, so each marginal matches the real
fleet and the AR(1) on the normal scores carries the 5-minute persistence.
``scripts/sim_realism.py`` checks the result against the real fleet.

Generation is sequential and causal with one random stream per farm and process, so a run
extended to a later time reproduces the earlier intervals exactly. That is what lets the
sandbox keep publishing new intervals as time passes without rewriting the past.

Times are NEM time (UTC+10). An interval is labelled by its end; trading day D runs from
D 04:05 to D+1 04:00.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Iterator, Sequence

from .nemweb_stream import NEM_TZ

INTERVAL = timedelta(minutes=5)
INTERVAL_HOURS = 5 / 60
DAY_START = timedelta(hours=4)  # trading day D: (D 04:00, D+1 04:00]
DEFAULT_CALIBRATION = Path(__file__).resolve().parents[2] / "artifacts" / "sim_calibration.json"
AEMO_ORIGIN = "AWEFS_ASEFS"
FALLBACK_ORIGIN = "SCADA"
WIND_LEVELS = {"calm": -1.0, "normal": 0.0, "windy": 1.0}
SOLAR_REGIMES = ("overcast", "partly_cloudy", "clear")
_NORMAL = NormalDist()


# ---------------------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------------------


def nem(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Times must be timezone-aware.")
    return value.astimezone(NEM_TZ)


def trading_day_of(end: datetime) -> date:
    """Trading day the interval ending at ``end`` belongs to."""
    return (nem(end) - DAY_START - INTERVAL).date()


def trading_day_start(day: date) -> datetime:
    """Exclusive start of trading day ``day``: D 04:00 NEM time."""
    return datetime.combine(day, time(0), tzinfo=NEM_TZ) + DAY_START


def trading_day_end(day: date) -> datetime:
    """Inclusive end of trading day ``day``: D+1 04:00 NEM time."""
    return trading_day_start(day) + timedelta(days=1)


def at_trading_time(day: date, clock: str) -> datetime:
    """``HH:MM`` on trading day ``day``; times before 04:05 fall on the next calendar date."""
    hours, minutes = (int(part) for part in clock.split(":"))
    moment = datetime.combine(day, time(hours, minutes), tzinfo=NEM_TZ)
    return moment + timedelta(days=1) if moment <= trading_day_start(day) else moment


def floor_interval(moment: datetime) -> datetime:
    """Latest interval end at or before ``moment``."""
    moment = nem(moment)
    return moment - timedelta(minutes=moment.minute % 5, seconds=moment.second, microseconds=moment.microsecond)


# ---------------------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------------------


class QuantileTable:
    """Inverse CDF from equally spaced quantiles (21 points: 0%, 5%, ..., 100%)."""

    def __init__(self, values: Sequence[float]) -> None:
        if len(values) < 2:
            raise ValueError("A quantile table needs at least two points.")
        self.values = [float(v) for v in values]

    def __call__(self, u: float) -> float:
        position = min(max(u, 0.0), 1.0) * (len(self.values) - 1)
        low = min(int(position), len(self.values) - 2)
        weight = position - low
        return self.values[low] * (1 - weight) + self.values[low + 1] * weight


@dataclass(frozen=True)
class Calibration:
    raw: dict

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CALIBRATION) -> "Calibration":
        return cls(json.loads(Path(path).read_text()))

    @property
    def publication_minutes(self) -> dict[str, int]:
        return self.raw["publication_minutes_after_day_end"]

    def table(self, *keys: str) -> QuantileTable:
        node = self.raw
        for key in keys:
            node = node[key]
        return QuantileTable(node)


# ---------------------------------------------------------------------------------------
# Farms
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SiteSpec:
    key: str
    duid: str
    name: str
    fuel: str  # "SOLAR" | "WIND"
    region: str
    capacity_mw: float
    customer: str
    participant: str | None = None  # origin code of the farm's self-forecast; None: AEMO only

    def __post_init__(self) -> None:
        if self.fuel not in ("SOLAR", "WIND"):
            raise ValueError(f"{self.key}: fuel must be SOLAR or WIND, not {self.fuel!r}")
        if not 0 < self.capacity_mw <= 2000:
            raise ValueError(f"{self.key}: capacity_mw must be in (0, 2000]")
        if not self.duid.isalnum() or len(self.duid) > 10 or self.duid != self.duid.upper():
            raise ValueError(f"{self.key}: a DUID is up to 10 upper-case letters and digits")


@dataclass(frozen=True)
class CapWindow:
    """A scenario-forced semi-dispatch cap: the target is held at ``limit_share`` of capacity."""

    start: datetime  # exclusive
    end: datetime  # inclusive
    limit_share: float

    def covers(self, end: datetime) -> bool:
        return self.start < end <= self.end


@dataclass
class SiteControls:
    """What the scenarios impose on one farm; empty means the farm runs on calibration alone."""

    caps: list[CapWindow] = field(default_factory=list)
    weather: dict[date, str] = field(default_factory=dict)  # trading day -> solar regime / wind level
    suppressed: list[tuple[datetime, datetime]] = field(default_factory=list)  # AEMO suppresses self-forecasts

    def cap_at(self, end: datetime) -> CapWindow | None:
        return next((window for window in self.caps if window.covers(end)), None)

    def self_suppressed(self, end: datetime) -> bool:
        return any(start < end <= stop for start, stop in self.suppressed)


@dataclass(frozen=True)
class Interval:
    """One simulated dispatch interval for one farm (all MW)."""

    duid: str
    end: datetime
    available: float  # latent: what the weather allows
    self_forecast: float | None
    aemo_forecast: float
    origin: str  # forecast dispatch used
    uigf: float
    capped: bool
    target: float  # TOTALCLEARED
    actual: float  # output at the end of the interval = SCADA labelled end + 5 min
    initial: float  # output at the start of the interval = SCADA labelled ``end``
    self_submissions: int
    self_suppressed_participant: int
    self_suppressed_aemo: int
    aemo_submissions: int


def _ar_step(rng: random.Random, score: float, phi: float, innovation: dict | None) -> float:
    """One AR(1) step of a standard normal score.

    ``innovation`` (small_sd, big_sd, big_share) makes the steps a two-normal mixture, for a
    series that mostly creeps and sometimes jumps; its variance is 1 - phi^2 by construction
    (``scripts/sim_calibrate.fit_copula_ar``). Without it the steps are plain Gaussian.
    """
    if innovation is None:
        scale = math.sqrt(max(0.0, 1.0 - phi * phi))
    else:
        scale = innovation["big_sd"] if rng.random() < innovation["big_share"] else innovation["small_sd"]
    return phi * score + scale * rng.gauss(0.0, 1.0)


class _Copula:
    """AR(1) on a standard normal score, mapped through a quantile table."""

    def __init__(self, rng: random.Random, table: QuantileTable, phi: float, innovation: dict | None = None) -> None:
        self.rng, self.table, self.phi, self.innovation = rng, table, phi, innovation
        self.score = rng.gauss(0.0, 1.0)

    def step(self, shift: float = 0.0) -> float:
        self.score = _ar_step(self.rng, self.score, self.phi, self.innovation)
        return self.table(_NORMAL.cdf(self.score + shift))


class FarmSimulator:
    """Steps one farm through consecutive intervals from ``start``."""

    def __init__(
        self,
        site: SiteSpec,
        calibration: Calibration,
        seed: int,
        controls: SiteControls | None = None,
        cap_rate_scale: float = 1.0,
    ) -> None:
        self.site = site
        self.cal = calibration.raw
        self.controls = controls or SiteControls()
        self.cap_rate_scale = cap_rate_scale
        fuel = site.fuel
        stream = lambda name: random.Random(f"{seed}:{site.duid}:{name}")  # noqa: E731
        self._weather_rng = stream("weather")
        self._cap_rng = stream("cap")
        self._source_rng = stream("source")
        self._submission_rng = stream("submissions")
        forecast = self.cal["forecast"][fuel]
        # What the farm actually delivers misses the forecast by the participant's error.
        self._miss = _Copula(stream("self-error"), calibration.table("forecast", fuel, "error_quantiles", "self"), forecast["error_phi"]["self"])
        self._aemo_rng = stream("aemo-gap")
        self._aemo_gap = 0.0
        tracking = self.cal["tracking"][fuel]
        self._tracking = _Copula(stream("tracking"), calibration.table("tracking", fuel, "error_quantiles"), tracking["phi"])
        self._submissions = calibration.table("forecast", fuel, "self_submissions_per_interval_quantiles")
        self._cap_durations = self.cal["cap"][fuel]["duration_samples"]
        self._cap_limits = calibration.table("cap", fuel, "limit_share_quantiles")
        if fuel == "WIND":
            wind = self.cal["wind"]
            self._wind = _Copula(self._weather_rng, calibration.table("wind", "marginal_quantiles"), wind["phi"], wind["innovation"])
        self._envelope = self.cal["solar"]["envelope"]
        self._kt_tables = {name: QuantileTable(regime["kt_quantiles"]) for name, regime in self.cal["solar"]["regimes"].items()}
        self._regime: str | None = None
        self._kt_score = self._weather_rng.gauss(0.0, 1.0) if fuel == "SOLAR" else 0.0
        self._solar_day: date | None = None
        self._episode_left = 0
        self._episode_limit = 0.0
        self._using_self = site.participant is not None
        self._last_actual = 0.0

    # Weather ---------------------------------------------------------------------------
    # The weather processes are calibrated on UIGF, so they stand for what a forecast sees
    # (including its 5-minute noise), not for the power on site.

    def _solar_signal(self, end: datetime) -> float:
        local = nem(end)
        regimes = self.cal["solar"]["regimes"]
        if self._solar_day != local.date():  # new calendar day: keep or redraw the regime
            self._solar_day = local.date()
            forced = self.controls.weather.get(trading_day_of(end))
            if forced in SOLAR_REGIMES:
                self._regime = forced
            elif self._regime is None or self._weather_rng.random() > self.cal["solar"]["regime_persistence"]:
                self._regime = self._weather_rng.choices(SOLAR_REGIMES, weights=[regimes[r]["share_of_days"] for r in SOLAR_REGIMES])[0]
        self._kt_score = _ar_step(self._weather_rng, self._kt_score, self.cal["solar"]["phi"], None)
        kt = self._kt_tables[self._regime](_NORMAL.cdf(self._kt_score))
        envelope = self._envelope[(local.hour * 60 + local.minute) // 5]
        return self.site.capacity_mw * envelope * kt

    def _wind_signal(self, end: datetime) -> float:
        level = self.controls.weather.get(trading_day_of(end), "normal")
        if level not in WIND_LEVELS:
            raise ValueError(f"{self.site.key}: wind level must be one of {sorted(WIND_LEVELS)}, not {level!r}")
        diurnal = self.cal["wind"]["hourly_score_shift"][nem(end).hour]
        return self.site.capacity_mw * self._wind.step(WIND_LEVELS[level] + diurnal)

    # One interval ----------------------------------------------------------------------

    def step(self, end: datetime) -> Interval:
        cap_mw = self.site.capacity_mw
        fuel = self.site.fuel
        signal = self._solar_signal(end) if fuel == "SOLAR" else self._wind_signal(end)
        producing = signal > 0.0 or self._last_actual > 0.0
        clamp = lambda value: min(max(value, 0.0), cap_mw) if producing else 0.0  # noqa: E731
        params = self.cal["forecast"][fuel]

        # The participant forecasts the signal; AEMO's forecast sits a calibrated, short-lived
        # gap away from it; the power on site misses both by the forecast error.
        gap = params["aemo_minus_self"]
        self._aemo_gap = gap["phi"] * self._aemo_gap + gap["sd"] * math.sqrt(1 - gap["phi"] ** 2) * self._aemo_rng.gauss(0.0, 1.0)
        self_forecast = clamp(signal) if self.site.participant else None
        aemo_forecast = clamp(signal + (self._aemo_gap * cap_mw if self.site.participant else 0.0))
        available = clamp(signal - self._miss.step() * cap_mw)

        # Which forecast dispatch used: runs of self and AEMO with the calibrated mean lengths;
        # a rare fallback to SCADA; AEMO whenever the self-forecasts are suppressed.
        runs = params["mean_run_intervals"]
        if self.site.participant:
            leave = 1 / runs["self"] if self._using_self else 1 / runs["aemo"]
            if self._source_rng.random() < leave:
                self._using_self = not self._using_self
        suppressed_by_aemo = self.site.participant is not None and self.controls.self_suppressed(end)
        fallback = self._source_rng.random() < params["fallback_share"]
        if fallback:
            origin, uigf = FALLBACK_ORIGIN, self._last_actual
        elif self._using_self and self.site.participant and not suppressed_by_aemo:
            origin, uigf = self.site.participant, self_forecast
        else:
            origin, uigf = AEMO_ORIGIN, aemo_forecast
        uigf = round(uigf, 3)

        # Semi-dispatch cap: a scenario window, else calibrated episodes by hour of day.
        forced = self.controls.cap_at(end)
        if self._episode_left == 0:
            rate = self.cal["cap"][fuel]["start_rate_by_hour"][nem(end).hour] * self.cap_rate_scale
            if self._cap_rng.random() < rate:
                self._episode_left = self._cap_rng.choice(self._cap_durations)
                self._episode_limit = self._cap_limits(self._cap_rng.random()) * cap_mw
        if forced is not None:
            capped, limit = True, forced.limit_share * cap_mw
        elif self._episode_left > 0:
            capped, limit = True, self._episode_limit
            self._episode_left -= 1
        else:
            capped, limit = False, cap_mw
        target = round(min(uigf, limit), 3)

        # The farm follows its target but cannot exceed what is available. The forecast miss
        # already carries most of the tracking error, so only its middle half is added here.
        noise = self._tracking.step() * cap_mw
        trim = self.cal["tracking"][fuel]["error_quantiles"]
        noise = min(max(noise, trim[5] * cap_mw), trim[15] * cap_mw)  # 25th..75th percentile
        actual = round(clamp(min(target, available) + noise), 3)

        submissions = participant_suppressed = aemo_suppressed = 0
        if self.site.participant:
            submissions = max(1, round(self._submissions(self._submission_rng.random())))
            share = params["self_participant_suppressed_share"]
            participant_suppressed = sum(self._submission_rng.random() < share for _ in range(submissions))
            if origin == self.site.participant:
                participant_suppressed = min(participant_suppressed, submissions - 1)  # one stays usable
            aemo_suppressed = submissions if suppressed_by_aemo else 0

        interval = Interval(
            duid=self.site.duid,
            end=end,
            available=round(available, 3),
            self_forecast=None if self_forecast is None else round(self_forecast, 3),
            aemo_forecast=round(aemo_forecast, 3),
            origin=origin,
            uigf=uigf,
            capped=capped,
            target=target,
            actual=actual,
            initial=self._last_actual,
            self_submissions=submissions,
            self_suppressed_participant=participant_suppressed,
            self_suppressed_aemo=aemo_suppressed,
            aemo_submissions=round(params["aemo_submissions_per_interval"]),
        )
        self._last_actual = actual
        return interval


def simulate(
    site: SiteSpec,
    calibration: Calibration,
    seed: int,
    start: datetime,
    until: datetime,
    controls: SiteControls | None = None,
    cap_rate_scale: float = 1.0,
) -> Iterator[Interval]:
    """Intervals ending after ``start`` up to and including ``until``."""
    farm = FarmSimulator(site, calibration, seed, controls, cap_rate_scale)
    end = floor_interval(start) + INTERVAL
    stop = floor_interval(until)
    while end <= stop:
        yield farm.step(end)
        end += INTERVAL
