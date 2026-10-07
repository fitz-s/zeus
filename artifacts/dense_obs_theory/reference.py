"""Exact finite-state oracle for a marked, discretely sampled settlement law.

This is a research reference, NOT a continuous-temperature production forecaster.
The forward recursion is exact (up to floating-point arithmetic) for its declared
finite hidden-state / finite candidate-event model. A finite grid is not an exact
OU process and candidate ticks are not a continuous-time SPECI point process.
``ou_grid_transition`` is explicitly an approximation and supplies no error bound.

No repository runtime is imported. Run ``python test_reference.py`` in this
directory. NumPy and SciPy are the only dependencies.

Authoritative numerical outputs are log probabilities AND log complements. Model
support and settlement-fact certificates are separate. A small linear float or a
rounded 1.0 is never evidence of logical impossibility or certainty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from math import isfinite
from typing import Callable, Literal, Sequence, TypeVar
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import log_ndtr, logsumexp
from scipy.stats import norm, t as student_t

Array = np.ndarray
Metric = Literal["high", "low"]
ResultT = TypeVar("ResultT")


class InconsistentEvidence(ValueError):
    """The specified model gives received evidence exactly zero likelihood."""


def local_day_utc_bounds(day: date, timezone_name: str) -> tuple[float, float]:
    """Half-open local calendar day; DST days can contain 23 or 25 UTC hours."""
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return start.timestamp(), end.timestamp()


def routine_instants_for_local_day(day: date, timezone_name: str,
                                    minutes: Sequence[int]) -> tuple[float, ...]:
    """Both repeated DST folds are retained; nonexistent local minutes are absent.

    This recurring-minute schedule is an explicit model input, not a station
    schedule registry. Unscheduled eligible events still need their own model.
    """
    if not minutes or any(type(m) is not int or not 0 <= m < 60 for m in minutes):
        raise ValueError("a nonempty set of minute positions in [0,60) is required")
    start, end = local_day_utc_bounds(day, timezone_name)
    zone = ZoneInfo(timezone_name)
    wanted = set(minutes)
    return tuple(float(t) for t in np.arange(start, end, 60)
                 if datetime.fromtimestamp(float(t), timezone.utc).astimezone(zone).minute in wanted)


def _log_probabilities(probabilities: Sequence[float] | Array) -> Array:
    p = np.asarray(probabilities, dtype=float)
    if np.any(~np.isfinite(p)) or np.any(p < 0):
        raise ValueError("probabilities must be finite and nonnegative")
    with np.errstate(divide="ignore"):
        return np.log(p)


def _frozen_array(value: Array) -> Array:
    """Detach input buffers so normal caller mutation cannot alter a snapshot."""
    result = np.array(value, dtype=float, copy=True)
    result.flags.writeable = False
    return result


def _validate_log_distribution(a: Array, axis: int = -1) -> None:
    if np.any(np.isnan(a)) or np.any(np.isposinf(a)):
        raise ValueError("log probabilities may be finite or -inf only")
    totals = logsumexp(a, axis=axis)
    if np.any(~np.isfinite(totals)) or not np.allclose(totals, 0, atol=2e-12, rtol=0):
        raise ValueError("each distribution must have logsumexp equal to zero")


def round_half_up(value: float | Decimal | int) -> int:
    """floor(x + 0.5), including -1.5 -> -1; NOT Decimal ROUND_HALF_UP."""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if not d.is_finite():
        raise ValueError("nonfinite temperature")
    return int((d + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


@dataclass(frozen=True)
class WrhRow:
    """One native source row; air_temp already has the requested source unit."""

    air_temp: float | Decimal | None
    unit: Literal["C", "F"]
    sea_level_pressure: float | None = None
    raw_metar: str | None = None


def wrh_row_value(row: WrhRow, station: str, view: Literal["all", "hourly"],
                  contract_unit: Literal["C", "F"]) -> int | None:
    """Pinned WRH render law, without re-decoding temperature from METAR text.

    ``hourly`` includes a pressure-bearing row OR a raw report starting with the
    station ID, so a qualifying SPECI is not excluded just by its report type.
    Unit mismatch is rejected: callers must request the correct native feed unit.
    Date, revision, station and completion authority are upstream responsibilities.
    """
    if view not in ("all", "hourly") or not station:
        raise ValueError("explicit page view and station are required")
    if row.unit != contract_unit:
        raise ValueError("native feed unit does not match the settlement contract")
    if row.air_temp is None:
        return None
    if isinstance(row.air_temp, bool):
        raise ValueError("boolean temperature")
    official = row.sea_level_pressure is not None or bool(
        row.raw_metar and row.raw_metar.upper().startswith(station.upper())
    )
    if view == "hourly" and not official:
        return None
    return round_half_up(row.air_temp)


def raw_report_predictor_value(*, body_integer_c: int, t_group_c: Decimal | None,
                               use_t_group: bool, unit: Literal["C", "F"]) -> int:
    """Explicit alternate decoder for a predictor; never override native WRH data.

    Whether T-group or body is selected is an input representing a source-product
    decoding hypothesis. It is NOT inferred from routine/SPECI identity here.
    """
    if type(body_integer_c) is not int or unit not in ("C", "F"):
        raise ValueError("body must be a Celsius integer and unit explicit")
    if use_t_group and t_group_c is None:
        raise ValueError("T-group decoding requested but missing")
    c = t_group_c if use_t_group else Decimal(body_integer_c)
    assert c is not None
    value = c if unit == "C" else c * Decimal("1.8") + Decimal(32)
    return round_half_up(value)


def log_normal_interval(lo: Array | float, hi: Array | float,
                        mean: Array | float = 0.0, sd: Array | float = 1.0) -> Array:
    """Stable log P(lo <= Normal(mean, sd) < hi), including remote tails.

    Normal distributions have no endpoint atoms; open/closed endpoints matter in
    the discrete mark kernels, where values are mapped explicitly instead.
    """
    lo, hi, mean, sd = np.broadcast_arrays(
        np.asarray(lo, float), np.asarray(hi, float), np.asarray(mean, float),
        np.asarray(sd, float),
    )
    if np.any(sd <= 0) or np.any(~np.isfinite(sd)) or np.any(~np.isfinite(mean)):
        raise ValueError("positive finite sd and finite mean required")
    if np.any(np.isnan(lo)) or np.any(np.isnan(hi)) or np.any(lo >= hi):
        raise ValueError("interval must have strictly ordered non-NaN endpoints")
    a, b = (lo - mean) / sd, (hi - mean) / sd
    # Use the survival function for a positive interval to avoid subtracting 1s.
    high = np.where(a >= 0, log_ndtr(-a), log_ndtr(b))
    low = np.where(a >= 0, log_ndtr(-b), log_ndtr(a))
    delta = low - high
    with np.errstate(divide="ignore", invalid="ignore"):
        result = high + np.log(-np.expm1(delta))
    if np.any(np.isnan(result)):
        raise FloatingPointError("interval likelihood could not be evaluated")
    return result


@dataclass(frozen=True)
class State:
    temperature_c: float
    weather_regime: str = "default"


@dataclass(frozen=True)
class Mark:
    key: str
    settlement_value: int | None
    kind: str = "routine"
    emits_report: bool = True


@dataclass(frozen=True)
class ReportKernel:
    marks: tuple[Mark, ...]
    log_probabilities: Array  # state x mark; weather dependence may be arbitrary

    def __post_init__(self) -> None:
        object.__setattr__(self, "marks", tuple(self.marks))
        object.__setattr__(self, "log_probabilities", _frozen_array(self.log_probabilities))

    @classmethod
    def from_probabilities(cls, marks: Sequence[Mark], probabilities: Array) -> "ReportKernel":
        return cls(tuple(marks), _log_probabilities(probabilities))

    def validate(self, states: int) -> None:
        if not self.marks or len({m.key for m in self.marks}) != len(self.marks):
            raise ValueError("mark keys must be nonempty and unique at each event")
        if np.shape(self.log_probabilities) != (states, len(self.marks)):
            raise ValueError("report kernel must have shape state x mark")
        for m in self.marks:
            if m.settlement_value is not None and type(m.settlement_value) is not int:
                raise ValueError("settlement mark must be an exact integer")
            if not m.emits_report and m.settlement_value is not None:
                raise ValueError("a nonexistent report cannot settle a temperature")
        _validate_log_distribution(self.log_probabilities)


@dataclass(frozen=True)
class FiniteMarkedModel:
    times: tuple[float, ...]  # strictly increasing absolute clocks (toy seconds OK)
    states: tuple[State, ...]
    initial_log_probabilities: Array
    log_transitions: tuple[Array, ...]  # len(times)-1, old state x new state
    reports: tuple[ReportKernel, ...]
    name: str = "finite_state_reference"
    available_at: float = -np.inf
    approximation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "times", tuple(self.times))
        object.__setattr__(self, "states", tuple(self.states))
        object.__setattr__(self, "reports", tuple(self.reports))
        object.__setattr__(self, "initial_log_probabilities", _frozen_array(self.initial_log_probabilities))
        object.__setattr__(self, "log_transitions", tuple(_frozen_array(p) for p in self.log_transitions))

    def validate(self) -> None:
        n, k = len(self.times), len(self.states)
        if not n or not k or any(not isfinite(t) for t in self.times):
            raise ValueError("nonempty finite event clocks and states required")
        if any(a >= b for a, b in zip(self.times, self.times[1:])):
            raise ValueError("event clocks must be strictly increasing")
        if any(not isfinite(s.temperature_c) for s in self.states):
            raise ValueError("state temperatures must be finite")
        if np.shape(self.initial_log_probabilities) != (k,):
            raise ValueError("initial state distribution shape mismatch")
        _validate_log_distribution(self.initial_log_probabilities)
        if len(self.log_transitions) != n - 1 or len(self.reports) != n:
            raise ValueError("one transition per adjacent pair and report kernel per event")
        for p in self.log_transitions:
            if np.shape(p) != (k, k):
                raise ValueError("transition shape mismatch")
            _validate_log_distribution(p)
        for r in self.reports:
            r.validate(k)


@dataclass(frozen=True)
class Evidence:
    observation_id: str
    event_index: int
    receipt_at: float
    channel: Literal["dense", "settlement", "auxiliary", "arrival"]
    log_state_likelihood: Array | None = None
    log_report_likelihood: Array | None = None  # mark or state x mark
    # Only the report_fact constructor attaches a settlement-fact certificate.
    known_mark_key: str | None = None
    arrival_status: Literal["not_received", "received"] | None = None

    def __post_init__(self) -> None:
        for name in ("log_state_likelihood", "log_report_likelihood"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _frozen_array(value))


def report_fact(model: FiniteMarkedModel, event_index: int, mark_index: int, *,
                receipt_at: float, observation_id: str) -> Evidence:
    """Observe an immutable/final-authority source mark in the declared model.

    A provisional version that may be overwritten at settlement is not this fact.
    Such a version needs a noisy observation of an augmented terminal-source-tape
    state; this simple extremum accumulator does not model row revisions.
    """
    kernel = model.reports[event_index]
    if not 0 <= mark_index < len(kernel.marks):
        raise ValueError("invalid report mark")
    likelihood = np.full(len(kernel.marks), -np.inf)
    likelihood[mark_index] = 0.0
    return Evidence(observation_id, event_index, receipt_at, "settlement",
                    log_report_likelihood=likelihood,
                    known_mark_key=kernel.marks[mark_index].key)


def interval_evidence(model: FiniteMarkedModel, event_index: int, integer: int, *,
                      receipt_at: float, observation_id: str,
                      bias_c: float = 0.0) -> Evidence:
    """Exact integer censoring of the specified finite latent sensor state.

    This is predictor evidence only. It is not a final-source mark certificate.
    AWC integer evidence must not silently harden an unverified WRH feed outcome.
    """
    t = np.array([s.temperature_c for s in model.states]) + bias_c
    support = (t >= integer - 0.5) & (t < integer + 0.5)
    return Evidence(observation_id, event_index, receipt_at, "auxiliary",
                    log_state_likelihood=np.where(support, 0.0, -np.inf))


def dense_evidence(model: FiniteMarkedModel, event_index: int, reading_c: float, *,
                   receipt_at: float, observation_id: str,
                   bias_c: float | Array = 0.0, sigma_c: float = 0.1,
                   quantum_c: float | None = None, outlier_weight: float = 0.0,
                   outlier_scale_c: float = 1.0, outlier_df: float = 4.0) -> Evidence:
    """Full-support dense emission, optionally quantized and Student-t robust.

    ``bias_c`` may depend on hidden sensor regime. For time-averaged readings the
    hidden state must encode the averaging functional; a point-value likelihood
    is only the explicitly declared toy model. A nonzero quantum models a rounded
    dense number by integrating its preimage, not by treating it as exact.
    """
    if not isfinite(reading_c) or sigma_c <= 0 or not isfinite(sigma_c):
        raise ValueError("finite observation and positive measurement sigma required")
    if not 0 <= outlier_weight < 1 or outlier_scale_c <= 0 or outlier_df <= 0:
        raise ValueError("invalid robust-emission parameters")
    means = np.array([s.temperature_c for s in model.states]) + bias_c
    if quantum_c is None:
        good = norm.logpdf(reading_c, loc=means, scale=sigma_c)
        bad = student_t.logpdf(reading_c, df=outlier_df, loc=means, scale=outlier_scale_c)
    else:
        if quantum_c <= 0 or not isfinite(quantum_c):
            raise ValueError("positive finite measurement quantum required")
        lo, hi = reading_c - quantum_c / 2, reading_c + quantum_c / 2
        good = log_normal_interval(lo, hi, means, sigma_c)
        a, b = (lo - means) / outlier_scale_c, (hi - means) / outlier_scale_c
        high = np.where(a >= 0, student_t.logsf(a, outlier_df), student_t.logcdf(b, outlier_df))
        low = np.where(a >= 0, student_t.logsf(b, outlier_df), student_t.logcdf(a, outlier_df))
        with np.errstate(divide="ignore", invalid="ignore"):
            bad = high + np.log(-np.expm1(low - high))
    loglik = good if outlier_weight == 0 else np.logaddexp(
        np.log1p(-outlier_weight) + good, np.log(outlier_weight) + bad,
    )
    if np.any(~np.isfinite(loglik)):
        raise FloatingPointError("dense likelihood lost full support numerically")
    return Evidence(observation_id, event_index, receipt_at, "dense", loglik)


def nonreceipt_evidence(model: FiniteMarkedModel, event_index: int, *,
                        checked_at: float, rates: float | Array,
                        observation_id: str) -> Evidence:
    """Likelihood for no report arrival through checked_at; exponential toy lag.

    rates broadcasts to state x mark and may depend on weather and report kind.
    No-event marks have likelihood 1. This is optional explicit modeling of
    informative receipt latency; simply filtering receipt clocks assumes the
    omitted arrival mechanism is conditionally ignorable. Do not include this
    nonreceipt snapshot together with a report already received by checked_at.
    """
    age = checked_at - model.times[event_index]
    shape = model.reports[event_index].log_probabilities.shape
    r = np.broadcast_to(np.asarray(rates, float), shape)
    if age < 0 or np.any(~np.isfinite(r)) or np.any(r < 0):
        raise ValueError("nonnegative age and finite nonnegative lag rates required")
    likelihood = -age * r
    for j, mark in enumerate(model.reports[event_index].marks):
        if not mark.emits_report:
            likelihood[:, j] = 0.0
    return Evidence(observation_id, event_index, checked_at, "arrival",
                    log_report_likelihood=likelihood, arrival_status="not_received")


def receipt_density_evidence(model: FiniteMarkedModel, event_index: int, *,
                             receipt_at: float, rates: float | Array,
                             observation_id: str) -> Evidence:
    """Exponential toy receipt-density factor r*exp(-r*lag), conditional on a row.

    Pair with report_fact for the mark itself. Rates can depend on latent weather
    and mark kind. A recorded arrival supersedes earlier nonreceipt snapshots;
    the same arrival variable must never be conditioned on twice as independent.
    """
    lag = receipt_at - model.times[event_index]
    shape = model.reports[event_index].log_probabilities.shape
    r = np.broadcast_to(np.asarray(rates, float), shape)
    if lag < 0 or np.any(~np.isfinite(r)) or np.any(r < 0):
        raise ValueError("nonnegative lag and finite nonnegative lag rates required")
    with np.errstate(divide="ignore"):
        likelihood = np.log(r) - lag * r
    for j, mark in enumerate(model.reports[event_index].marks):
        if not mark.emits_report:
            likelihood[:, j] = -np.inf
    return Evidence(observation_id, event_index, receipt_at, "arrival",
                    log_report_likelihood=likelihood, arrival_status="received")


def _same_payload(a: Evidence, b: Evidence) -> bool:
    return (a.event_index == b.event_index and a.channel == b.channel and
            a.known_mark_key == b.known_mark_key and a.arrival_status == b.arrival_status and
            all((x is None and y is None) or
                (x is not None and y is not None and np.array_equal(x, y))
                for x, y in ((a.log_state_likelihood, b.log_state_likelihood),
                             (a.log_report_likelihood, b.log_report_likelihood))))


def causal_evidence(model: FiniteMarkedModel, observations: Sequence[Evidence],
                     decision_at: float, use_dense: bool = True) -> tuple[Evidence, ...]:
    """Strict receipt < decision; duplicate lineage IDs count once.

    Revised facts need distinct versioned IDs plus an upstream revision policy.
    Conflicting payloads under one ID are rejected, not multiplied or overwritten.
    """
    if not isfinite(decision_at) or not model.available_at < decision_at:
        raise ValueError("model snapshot must be available strictly before decision")
    selected: dict[str, Evidence] = {}
    k = len(model.states)
    for e in observations:
        if not isfinite(e.receipt_at):
            raise ValueError("a finite receipt clock is required to select as-of evidence")
        if e.receipt_at >= decision_at or (e.channel == "dense" and not use_dense):
            continue
        if not e.observation_id or not 0 <= e.event_index < len(model.times):
            raise ValueError("observation needs a lineage ID and valid event index")
        if model.times[e.event_index] > e.receipt_at:
            raise ValueError("an observation cannot arrive before its observation instant")
        if e.channel not in ("dense", "settlement", "auxiliary", "arrival"):
            raise ValueError("unknown admitted evidence channel")
        j = len(model.reports[e.event_index].marks)
        for a, shapes in ((e.log_state_likelihood, ((k,),)),
                          (e.log_report_likelihood, ((j,), (k, j)))):
            if a is not None and (np.shape(a) not in shapes or np.any(np.isnan(a)) or
                                  np.any(np.isposinf(a))):
                raise ValueError("invalid observation likelihood")
            if e.channel == "dense" and a is not None and np.any(~np.isfinite(a)):
                raise ValueError("dense evidence may not create structural support zeros")
        old = selected.get(e.observation_id)
        if old is not None and not _same_payload(old, e):
            raise ValueError("conflicting duplicate observation lineage")
        if old is None or e.receipt_at < old.receipt_at:
            selected[e.observation_id] = e
    canonical = tuple(sorted(selected.values(), key=lambda e: (e.event_index, e.observation_id)))
    arrival_events: set[int] = set()
    for e in canonical:
        if e.channel == "arrival":
            if e.event_index in arrival_events:
                raise ValueError("one canonical arrival lifecycle likelihood per report event required")
            arrival_events.add(e.event_index)
            if e.arrival_status == "not_received" and any(
                f.channel == "settlement" and f.event_index == e.event_index
                for f in canonical
            ):
                raise ValueError("nonreceipt contradicts the current lifecycle of an already received source report")
    return canonical


@dataclass(frozen=True)
class SettlementBin:
    name: str
    lower: int | None = None  # inclusive; None denotes -infinity
    upper: int | None = None  # inclusive; None denotes +infinity
    receives_no_data: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("bin name must be a nonempty string")
        if any(v is not None and type(v) is not int for v in (self.lower, self.upper)):
            raise ValueError("bin endpoints must be exact integers or None, never bool/float")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("bin interval is empty")
        if type(self.receives_no_data) is not bool:
            raise ValueError("no-data assignment must be Boolean")

    def contains(self, value: int | None) -> bool:
        if value is None:
            return self.receives_no_data
        return ((self.lower is None or self.lower <= value) and
                (self.upper is None or value <= self.upper))


@dataclass(frozen=True)
class BinProbability:
    log_probability: float
    log_complement: float
    model_supported: bool
    model_complement_supported: bool
    settlement_forced_zero: bool
    settlement_forced_one: bool

    def linear_for_display(self) -> float:
        """Rounded display only; inspect log complement and certificates as well."""
        return float(np.exp(self.log_probability))


@dataclass(frozen=True)
class InferenceResult:
    metric: Metric
    values: tuple[int | None, ...]  # None = no eligible source row in the day
    log_probabilities: Array
    model_support: Array
    log_evidence: float
    received_settlement_values: tuple[int, ...] = ()
    component_posteriors: dict[str, float] = field(default_factory=dict)
    serving_authorized: bool = False  # this finite oracle is not a production law

    def log_probability_of_value(self, value: int | None) -> float:
        try:
            return float(self.log_probabilities[self.values.index(value)])
        except ValueError:
            return -np.inf

    def bin_probability(self, b: SettlementBin) -> BinProbability:
        mask = np.array([b.contains(v) for v in self.values])
        lp = float(logsumexp(self.log_probabilities[mask]))
        lc = float(logsumexp(self.log_probabilities[~mask]))
        bound = None
        if self.received_settlement_values:
            bound = (max if self.metric == "high" else min)(self.received_settlement_values)
        if self.metric == "high":
            zero = bound is not None and b.upper is not None and b.upper < bound
            one = bound is not None and b.upper is None and (b.lower is None or b.lower <= bound)
        else:
            zero = bound is not None and b.lower is not None and b.lower > bound
            one = bound is not None and b.lower is None and (b.upper is None or b.upper >= bound)
        return BinProbability(lp, lc, bool(np.any(self.model_support[mask])),
                              bool(np.any(self.model_support[~mask])), zero, one)

    def bin_vector(self, bins: Sequence[SettlementBin]) -> dict[str, BinProbability]:
        """Demand a disjoint exhaustive integer partition and one no-data winner."""
        if not bins or len({b.name for b in bins}) != len(bins):
            raise ValueError("unique nonempty bin names required")
        if sum(b.receives_no_data for b in bins) != 1:
            raise ValueError("exactly one contractual no-data winning bracket required")
        ordered = sorted(bins, key=lambda b: -np.inf if b.lower is None else b.lower)
        if ordered[0].lower is not None or ordered[-1].upper is not None:
            raise ValueError("bins must cover both integer shoulders")
        for b in ordered:
            if b.lower is not None and b.upper is not None and b.lower > b.upper:
                raise ValueError("empty finite bin")
        for a, b in zip(ordered, ordered[1:]):
            if a.upper is None or b.lower is None or a.upper + 1 != b.lower:
                raise ValueError("bins overlap or leave an integer gap")
        return {b.name: self.bin_probability(b) for b in bins}


def infer(model: FiniteMarkedModel, observations: Sequence[Evidence], *,
          decision_at: float, metric: Metric = "high", use_dense: bool = True) -> InferenceResult:
    """Joint forward recursion over latent state and full-day sampled extremum.

    All candidate slots are integrated, including past reports not yet received.
    SPECI/no-SPECI probabilities enter the mark kernel, not a deterministic extra
    temperature sample. State-dependent arrival likelihoods can enter evidence.
    Complexity O(N K^2 E + N K E J), memory O(K E + K^2 E) in this simple code.
    """
    model.validate()
    if metric not in ("high", "low"):
        raise ValueError("metric must be high or low")
    observations = causal_evidence(model, observations, decision_at, use_dense)
    by_time: dict[int, list[Evidence]] = {}
    facts: list[int] = []
    for e in observations:
        by_time.setdefault(e.event_index, []).append(e)
        if e.known_mark_key is not None:
            if e.channel != "settlement":
                raise ValueError("only settlement evidence can certify a known source mark")
            kernel = model.reports[e.event_index]
            matches = [j for j, m in enumerate(kernel.marks) if m.key == e.known_mark_key]
            if len(matches) != 1 or e.log_report_likelihood is None:
                raise ValueError("invalid known mark certificate")
            j = matches[0]
            l = np.broadcast_to(e.log_report_likelihood, kernel.log_probabilities.shape)
            other = np.arange(len(kernel.marks)) != j
            if np.any(np.isfinite(l[:, other])) or not np.all(l[:, j] == 0):
                raise ValueError("known mark certificate must select exactly its source mark")
            v = kernel.marks[j].settlement_value
            if v is not None:
                facts.append(v)
    labels = sorted({m.settlement_value for r in model.reports for m in r.marks
                     if m.settlement_value is not None})
    values = (None, *labels)
    index = {v: i for i, v in enumerate(values)}
    k, ecount = len(model.states), len(values)
    alpha = np.full((k, ecount), -np.inf)
    alpha[:, 0] = model.initial_log_probabilities
    support = np.zeros((k, ecount), dtype=bool)
    support[:, 0] = np.isfinite(model.initial_log_probabilities)
    evidence_total = 0.0
    extreme = max if metric == "high" else min
    for i, kernel in enumerate(model.reports):
        if i:
            transition = model.log_transitions[i - 1]
            alpha = logsumexp(alpha[:, None, :] + transition[:, :, None], axis=0)
            support = np.any(support[:, None, :] & np.isfinite(transition[:, :, None]), axis=0)
        report_likelihood = np.array(kernel.log_probabilities, copy=True)
        for observation in by_time.get(i, ()):
            if observation.log_state_likelihood is not None:
                alpha += observation.log_state_likelihood[:, None]
                support &= np.isfinite(observation.log_state_likelihood[:, None])
            if observation.log_report_likelihood is not None:
                report_likelihood += observation.log_report_likelihood
        updated = np.full_like(alpha, -np.inf)
        updated_support = np.zeros_like(support)
        for j, mark in enumerate(kernel.marks):
            for old, value in enumerate(values):
                v = (value if mark.settlement_value is None else
                     mark.settlement_value if value is None else extreme(value, mark.settlement_value))
                new = index[v]
                updated[:, new] = np.logaddexp(updated[:, new], alpha[:, old] + report_likelihood[:, j])
                updated_support[:, new] |= support[:, old] & np.isfinite(report_likelihood[:, j])
        normalizer = float(logsumexp(updated))
        if not isfinite(normalizer):
            raise InconsistentEvidence(f"zero model evidence at event {i}")
        alpha = updated - normalizer
        support = updated_support
        evidence_total += normalizer
    logp = logsumexp(alpha, axis=0)
    logp -= logsumexp(logp)
    supported = np.any(support, axis=0)
    if not np.array_equal(supported, np.isfinite(logp)):
        raise FloatingPointError("log-domain computation disagrees with Boolean support")
    return InferenceResult(metric, values, logp, supported, evidence_total, tuple(facts))


def infer_metar_only(model: FiniteMarkedModel, observations: Sequence[Evidence], **kwargs) -> InferenceResult:
    """Exact no-dense branch; no dense likelihood or dense-triggered parameter change."""
    return infer(model, observations, use_dense=False, **kwargs)


def infer_optional_dense(model: FiniteMarkedModel, observations: Sequence[Evidence], **kwargs) -> InferenceResult:
    """Absent dense data gives bit-identical recursion to infer_metar_only.

    Old received dense evidence is not silently forgotten. Exact age-based erasure
    is unjustified unless the model supplies a true conditional-independence cut.
    """
    return infer(model, observations, use_dense=True, **kwargs)


def dispatch_optional_dense(dense_observations: Sequence[Evidence] | None, *,
                              decision_at: float, legacy_callback: Callable[[], ResultT],
                              dense_callback: Callable[[tuple[Evidence, ...]], ResultT]) -> ResultT:
    """Compatibility boundary: no admitted dense evidence returns the legacy object.

    No model fitting, copying, renormalization or candidate-model validation happens
    on that branch. The closures own the exact legacy inputs and decision snapshot.
    A full production adapter also needs typed optional-channel failure handling;
    this unit property does not verify all historical Zeus A0 outputs.
    """
    if not dense_observations:
        return legacy_callback()
    if not isfinite(decision_at):
        raise ValueError("finite decision clock required")
    admitted = tuple(e for e in dense_observations
                     if isfinite(e.receipt_at) and e.receipt_at < decision_at and e.channel == "dense")
    return dense_callback(admitted) if admitted else legacy_callback()


def infer_mixture(models: Sequence[FiniteMarkedModel], prior_weights: Sequence[float],
                   observations_by_model: Sequence[Sequence[Evidence]], *,
                   decision_at: float, metric: Metric = "high",
                   use_dense: bool = True) -> InferenceResult:
    """Posterior evidence weights, not unchanged forecast-member prior weights."""
    if not models or len(models) != len(prior_weights) or len(models) != len(observations_by_model):
        raise ValueError("one prior and evidence list per mixture component required")
    if len({m.name for m in models}) != len(models):
        raise ValueError("mixture components need unique names")
    lp = _log_probabilities(prior_weights)
    _validate_log_distribution(lp)
    results: list[InferenceResult | None] = []
    weights = np.full(len(models), -np.inf)
    for i, (model, evidence) in enumerate(zip(models, observations_by_model)):
        if not np.isfinite(lp[i]):
            results.append(None)
            continue
        try:
            r = infer(model, evidence, decision_at=decision_at, metric=metric, use_dense=use_dense)
        except InconsistentEvidence:
            results.append(None)
            continue
        results.append(r)
        weights[i] = lp[i] + r.log_evidence
    total = float(logsumexp(weights))
    if not isfinite(total):
        raise InconsistentEvidence("all mixture components give evidence zero likelihood")
    weights -= total
    values = (None, *sorted({v for r in results if r is not None for v in r.values if v is not None}))
    logp = np.full(len(values), -np.inf)
    support = np.zeros(len(values), dtype=bool)
    fact_sets = {r.received_settlement_values for r in results if r is not None}
    if len(fact_sets) != 1:
        raise ValueError("mixture members must condition on identical settlement-source facts")
    for w, r in zip(weights, results):
        if r is not None:
            for j, v in enumerate(values):
                logp[j] = np.logaddexp(logp[j], w + r.log_probability_of_value(v))
                support[j] |= v in r.values and bool(r.model_support[r.values.index(v)])
    logp -= logsumexp(logp)
    return InferenceResult(metric, values, logp, support, total, next(iter(fact_sets)),
                           {m.name: float(np.exp(w)) for m, w in zip(models, weights)})


def temperature_report_kernel(states: Sequence[State], *, routine: bool,
                               speci_probability_by_weather: dict[str, float] | None = None,
                               unit: Literal["C", "F"] = "C") -> ReportKernel:
    """Toy native-feed mark law with weather-driven discretionary report chance.

    A zero SPECI probability is supported. Bernoulli candidate slots are exact for
    this finite model only; approximating arbitrary SPECI times by slots is an
    additional approximation. Real triggering histories require state augmentation.
    """
    if unit not in ("C", "F"):
        raise ValueError("unknown contract unit")
    values = [round_half_up(s.temperature_c if unit == "C" else
                           Decimal(str(s.temperature_c)) * Decimal("1.8") + Decimal(32))
              for s in states]
    integers = sorted(set(values))
    kind = "routine" if routine else "speci"
    marks = (Mark("no_report", None, "none", False), *(
        Mark(f"{kind}:{v}", v, kind) for v in integers
    ))
    probabilities = np.zeros((len(states), len(marks)))
    for i, state in enumerate(states):
        p = 1.0 if routine else (speci_probability_by_weather or {}).get(state.weather_regime, 0.0)
        if not 0 <= p <= 1:
            raise ValueError("report probability must be in [0,1]")
        probabilities[i, 0] = 1 - p
        probabilities[i, 1 + integers.index(values[i])] = p
    return ReportKernel.from_probabilities(marks, probabilities)


def ou_grid_transition(centers: Sequence[float], edges: Sequence[float], *, dt: float,
                        tau: float, stationary_sd: float, forecast_now: float = 0.0,
                        forecast_next: float = 0.0) -> Array:
    """APPROXIMATION: OU cell transition from representative source-cell centers.

    Integrates destination cells including infinite tail cells. Future propagation
    collapses each cell to its representative, so this is a finite-state model,
    not exact continuous OU filtering. Refinement agreement alone is not a bound.
    """
    centers, edges = np.asarray(centers, float), np.asarray(edges, float)
    if len(edges) != len(centers) + 1 or edges[0] != -np.inf or edges[-1] != np.inf:
        raise ValueError("one cell per center and both infinite tail edges required")
    if np.any(np.diff(edges) <= 0) or np.any(~np.isfinite(centers)):
        raise ValueError("ordered cells and finite representatives required")
    if not (dt > 0 and tau > 0 and stationary_sd > 0):
        raise ValueError("positive dt, tau and stationary_sd required")
    phi = np.exp(-dt / tau)
    mean = forecast_next + phi * (centers - forecast_now)
    sd = stationary_sd * np.sqrt(-np.expm1(-2 * dt / tau))
    result = log_normal_interval(edges[:-1][None, :], edges[1:][None, :], mean[:, None], sd)
    result -= logsumexp(result, axis=1, keepdims=True)
    return result


@dataclass(frozen=True)
class PairFit:
    bias_c: float
    dense_sigma_c: float
    latent_sigma_c: float
    negative_log_likelihood: float
    converged: bool
    samples: int


def interval_pair_log_likelihood(metar_integer: Array, dense: Array, prior_means: Array, *,
                                  bias_c: float, dense_sigma_c: float,
                                  latent_sigma_c: float) -> Array:
    """Exact Gaussian latent / interval-censored-integer paired likelihood.

    X~N(f,s_x^2), K=floor(X+.5), Y=X+b+N(0,s_e^2). The product integral equals
    p(Y) times P(K-.5 <= X < K+.5 | Y). Known prior means anchor the latent location.
    This iid parameter-identification experiment is not a fitted temporal model.
    """
    metar_integer, dense, prior_means = np.broadcast_arrays(
        np.asarray(metar_integer, float), np.asarray(dense, float), np.asarray(prior_means, float),
    )
    if dense_sigma_c <= 0 or latent_sigma_c <= 0:
        raise ValueError("strictly positive variances required")
    v = latent_sigma_c ** 2 + dense_sigma_c ** 2
    gain = latent_sigma_c ** 2 / v
    posterior_mean = prior_means + gain * (dense - bias_c - prior_means)
    posterior_sd = latent_sigma_c * dense_sigma_c / np.sqrt(v)
    return norm.logpdf(dense, prior_means + bias_c, np.sqrt(v)) + log_normal_interval(
        metar_integer - 0.5, metar_integer + 0.5, posterior_mean, posterior_sd,
    )


def fit_interval_pairs(metar_integer: Sequence[int], dense: Sequence[float],
                       prior_means: Sequence[float]) -> PairFit:
    """Joint MLE of bias, dense noise and latent residual scale on a frozen train set.

    Callers must pass only the training fold; no clock inference or automatic data
    fetching occurs. Sensor identity and forecast location are externally anchored.
    Log scale parameterization enforces positivity; it is not a live confidence
    cap or an error-detection mechanism.
    """
    k, y, f = np.broadcast_arrays(np.asarray(metar_integer, float), np.asarray(dense, float),
                                 np.asarray(prior_means, float))
    if k.ndim != 1 or len(k) < 10 or any(np.any(~np.isfinite(a)) for a in (k, y, f)):
        raise ValueError("at least ten finite paired training rows required")
    if np.any(k != np.floor(k)):
        raise ValueError("METAR labels must be integer-censored observations")

    def loss(theta: Array) -> float:
        # An average objective keeps the finite-difference gradient tolerance
        # independent of training-set size; the returned NLL is still a sum.
        return -float(np.mean(interval_pair_log_likelihood(
            k, y, f, bias_c=theta[0], dense_sigma_c=np.exp(theta[1]), latent_sigma_c=np.exp(theta[2]),
        )))

    initial = np.array([np.mean(y - k), np.log(0.2), np.log(max(np.std(k - f), 0.1))])
    fit = minimize(loss, initial, method="BFGS", options={"gtol": 1e-6, "maxiter": 300})
    theta = fit.x
    # BFGS can declare finite-difference precision loss at a valid near-stationary
    # solution. Do not relabel that as optimizer success: return its actual flag.
    return PairFit(float(theta[0]), float(np.exp(theta[1])), float(np.exp(theta[2])),
                   float(fit.fun * len(k)), bool(fit.success), len(k))


@dataclass(frozen=True)
class PersistenceFit:
    tau: float
    negative_log_likelihood: float
    converged: bool
    samples: int


def fit_finite_persistence(times: Sequence[float], state_temperatures_c: Sequence[float],
                           stationary_weights: Sequence[float], metar_integer: Sequence[float],
                           dense_c: Sequence[float], *, bias_c: float,
                           dense_sigma_c: float) -> PersistenceFit:
    """Fit tau in an exact finite sticky-state HMM from censored/missing evidence.

    P_dt = exp(-dt/tau) I + (1-exp(-dt/tau)) 1*pi. This is an exact semigroup
    for a finite reset process, NOT an OU process. Known pi/state temperatures and
    externally anchored dense bias/noise identify the temporal persistence here.
    NaN denotes a missing observation; METAR integers are exact interval censoring.
    Only the training arrays supplied by the caller enter this marginal likelihood.
    """
    times = np.asarray(times, float)
    temperatures = np.asarray(state_temperatures_c, float)
    k, y = np.asarray(metar_integer, float), np.asarray(dense_c, float)
    pi = _log_probabilities(stationary_weights)
    _validate_log_distribution(pi)
    if (times.ndim != 1 or len(times) < 10 or k.shape != times.shape or y.shape != times.shape
            or temperatures.shape != pi.shape or np.any(~np.isfinite(times))
            or np.any(np.diff(times) <= 0) or np.any(~np.isfinite(temperatures))
            or dense_sigma_c <= 0 or not isfinite(dense_sigma_c)):
        raise ValueError("ordered training times, aligned observations and a positive dense scale are required")
    if np.any(np.isinf(k)) or np.any(np.isinf(y)) or np.any(k[np.isfinite(k)] != np.floor(k[np.isfinite(k)])):
        raise ValueError("observations must be finite/NaN and METAR values integral")
    emissions = np.zeros((len(times), len(temperatures)))
    observed_y = np.isfinite(y)
    emissions[observed_y] = norm.logpdf(y[observed_y, None], temperatures[None, :] + bias_c, dense_sigma_c)
    observed_k = np.isfinite(k)
    censored_support = ((temperatures[None, :] >= k[observed_k, None] - 0.5) &
                        (temperatures[None, :] < k[observed_k, None] + 0.5))
    emissions[observed_k] += np.where(censored_support, 0.0, -np.inf)
    if np.any(~np.any(np.isfinite(emissions), axis=1)):
        raise InconsistentEvidence("training integer lies outside declared finite sensor support")
    differences = np.diff(times)

    def loss(log_tau: float) -> float:
        with np.errstate(over="ignore", under="ignore"):
            tau = np.exp(log_tau)
        if not isfinite(tau) or tau <= 0:
            return np.inf
        log_keep = -differences / tau
        with np.errstate(divide="ignore"):
            log_reset = np.log(-np.expm1(log_keep))
        alpha = pi + emissions[0]
        normalizer = float(logsumexp(alpha))
        alpha -= normalizer
        total = normalizer
        for i in range(1, len(times)):
            alpha = np.logaddexp(log_keep[i - 1] + alpha, log_reset[i - 1] + pi) + emissions[i]
            normalizer = float(logsumexp(alpha))
            if not isfinite(normalizer):
                return np.inf
            alpha -= normalizer
            total += normalizer
        return -total / len(times)

    fit = minimize_scalar(loss, method="brent", options={"xtol": 1e-6, "maxiter": 100})
    return PersistenceFit(float(np.exp(fit.x)), float(fit.fun * len(times)), bool(fit.success), len(times))
