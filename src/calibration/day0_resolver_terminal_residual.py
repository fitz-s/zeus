# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: resolver-graded Day0 observation model (external review
#   2026-09-24, design decision items 1-2). Fitted by
#   scripts/fit_day0_resolver_terminal_residual.py.
"""Resolver-graded terminal non-violation probability for Day0 carriers.

LABEL. For station ``j``, local date ``d`` and decision time ``t``::

    A = R(O_jdt)      running extreme possessed at t, in the contract unit,
                      rounded by the contract (SettlementSemantics)
    Y = settled value in the same unit
    v = +1 (HIGH) / -1 (LOW);  D = v(Y - A);  S = I(D >= 0)

``s(x) = P(S = 1 | x)`` is the terminal non-violation probability.  It grades
the running extreme against the resolver's product.  It does not grade AWC
reports against a later Ogimet mirror of the same report.  The failure
magnitude ``G-(k | x) = P(-D = k | D < 0, x)`` has categories
``k = 1..FAILURE_STEPS`` plus one overflow category.

CELLS. Parent strata are resolver product x channel/precision class x unit x
metric x local phase x remaining-gap category.  Station cells shrink toward
their parent.  The hierarchy is L0 metric -> L1 (resolver, channel, unit,
metric) -> L2 (+ phase, gap) -> L3 (+ station).  The failure rate
``p = 1 - s`` at each level is Beta-binomial around its parent::

    E[p | data] = (f + kappa * mu_parent) / (n + kappa)

The root carries the uniform Bayes-Laplace prior, so ``n`` clean observations
bound the failure rate below ``1 - 0.05^(1/(n+1))`` at 95% (299 -> 1%).  One
``kappa`` per metric and level is fitted by the Beta-binomial marginal
likelihood on training data.  A cell with ``n = 0``
serves its parent rate, not 0.5.  Serving draws nest down the chain, so a
sparse or failure-free cell keeps its parents' binomial uncertainty and never
serves certainty.  ``G-`` is a Dirichlet chain with a fixed concentration.

LABEL HYGIENE (the fitter enforces it through the helpers here):
- at most one checkpoint per station-day per cell, namely the last eligible
  hourly checkpoint of each phase;
- one report is one observation, whether AWC, Ogimet or both rendered it;
- a missing or unfinished settlement is censored, never counted as a success;
- only labels available by the fit cutoff enter the fit.

SERVING. ``resolve_day0_resolver_terminal_input`` returns ``None`` while the
config switch is off, which leaves the legacy carrier byte-identical.  With
the switch on it returns the input for a validated, causal, fresh artifact, or
it raises for that family (see its SCOPE/DRAIN/RESET).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import betaln

from src.contracts.settlement_semantics import SettlementSemantics

_LOG = logging.getLogger("zeus.day0_resolver_terminal_residual")

SCHEMA = "day0_resolver_terminal_residual_v1"
ARTIFACT_FILENAME = "day0_resolver_terminal_residual.json"
FAILURE_STEPS = 3
PHASES: tuple[tuple[str, int, int], ...] = (
    ("h00_12", 0, 12),
    ("h12_18", 12, 18),
    ("h18_24", 18, 24),
)
GAP_MISSING = "gap_missing"
ROOT_PRIOR = (1.0, 1.0)
G_ROOT_PRIOR = (0.5, 0.25, 0.125, 0.125)
G_KAPPA = 4.0
KAPPA_GRID = tuple(float(value) for value in np.logspace(-0.5, 5.0, 56))
MAX_ARTIFACT_AGE_DAYS = 14
HKO_STATION = "HKO"
HKO_CHANNEL = "hko_accumulator_tenth"
UNKNOWN_CHANNEL = "metar_unknown"
LEVELS = ("L0", "L1", "L2", "L3")
_EPS = 1e-12

RESOLVER_BY_SOURCE_TYPE = {
    "noaa": "noaa_wrh",
    "wu_icao": "wu_history",
    "hko": "hko_daily",
}


# --------------------------------------------------------------------------
# Label law
# --------------------------------------------------------------------------


def resolver_product_from_settlement_source(settlement_source: object) -> str | None:
    """Classify a settlement row's resolver product from its source URL."""

    text = str(settlement_source or "").strip().lower()
    if "weather.gov/wrh" in text:
        return "noaa_wrh"
    if "wunderground" in text or text == "wu_icao_history":
        return "wu_history"
    if "weather.gov.hk" in text:
        return "hko_daily"
    return None


def to_contract_unit(value_c: float, unit: str) -> float:
    """Convert Celsius to the contract unit exactly as the Day0 carrier does."""

    if unit == "C":
        return float(value_c)
    if unit == "F":
        return float(value_c) * (9.0 / 5.0) + 32.0
    raise ValueError("DAY0_RESOLVER_TERMINAL_UNIT_INVALID")


def contract_settlement_value(value_native: float, semantics: SettlementSemantics) -> float:
    """``R``: the contract's own rounding, never Python ``round``."""

    if not math.isfinite(float(value_native)):
        raise ValueError("DAY0_RESOLVER_TERMINAL_VALUE_INVALID")
    return float(semantics.round_values([float(value_native)])[0])


def terminal_margin(*, settled: float, observed_settlement: float, metric: str) -> float:
    """``D = v(Y - A)``."""

    if metric == "high":
        return float(settled) - float(observed_settlement)
    if metric == "low":
        return float(observed_settlement) - float(settled)
    raise ValueError("DAY0_RESOLVER_TERMINAL_METRIC_INVALID")


def failure_category(margin: float) -> int | None:
    """Index of ``-D`` among ``1..FAILURE_STEPS`` and overflow; None if D >= 0."""

    if margin >= 0.0:
        return None
    steps = int(round(-margin))
    if steps < 1 or not math.isclose(-margin, steps, abs_tol=1e-9):
        raise ValueError("DAY0_RESOLVER_TERMINAL_MARGIN_OFF_GRID")
    return min(steps, FAILURE_STEPS + 1) - 1


def phase_of_local_hour(hour: int) -> str:
    for name, start, end in PHASES:
        if start <= hour < end:
            return name
    raise ValueError("DAY0_RESOLVER_TERMINAL_HOUR_INVALID")


def gap_category(
    members_native: Sequence[float] | None,
    *,
    observed_settlement: float,
    metric: str,
) -> str:
    """Remaining-gap class ``v(mean(members) - A)`` in native steps."""

    if not members_native:
        return GAP_MISSING
    values = np.asarray(tuple(float(value) for value in members_native), dtype=float)
    if not np.isfinite(values).all():
        return GAP_MISSING
    gap = terminal_margin(
        settled=float(values.mean()), observed_settlement=observed_settlement, metric=metric
    )
    if gap < 1.0:
        return "gap_lt1"
    if gap <= 3.0:
        return "gap_1to3"
    return "gap_gt3"


@dataclass(frozen=True)
class ReportRendering:
    """One rendering of one station report (AWC or Ogimet), contract unit."""

    report_time_utc: datetime
    possessed_at_utc: datetime
    value_native: float


def running_extreme(
    renderings: Iterable[ReportRendering],
    *,
    at: datetime,
    day_start_utc: datetime,
    metric: str,
) -> float | None:
    """Running extreme possessed at ``at``; one value per report identity.

    Two renderings of the same report share ``report_time_utc`` and count
    once; the latest rendering possessed by ``at`` is that report's value.
    """

    latest: dict[datetime, ReportRendering] = {}
    for rendering in renderings:
        if not day_start_utc <= rendering.report_time_utc <= at:
            continue
        if rendering.possessed_at_utc > at:
            continue
        known = latest.get(rendering.report_time_utc)
        if known is None or rendering.possessed_at_utc > known.possessed_at_utc:
            latest[rendering.report_time_utc] = rendering
    if not latest:
        return None
    pick = max if metric == "high" else min
    return float(pick(item.value_native for item in latest.values()))


@dataclass(frozen=True)
class TerminalLabel:
    """One resolver-graded checkpoint label."""

    city: str
    station: str
    target_date: str
    metric: str
    resolver_product: str
    channel_class: str
    unit: str
    phase: str
    gap: str
    observed_settlement: float
    settled: float
    checkpoint_utc: datetime
    available_at_utc: datetime

    @property
    def margin(self) -> float:
        return terminal_margin(
            settled=self.settled,
            observed_settlement=self.observed_settlement,
            metric=self.metric,
        )

    @property
    def nonviolation(self) -> bool:
        return self.margin >= 0.0

    def cell(self) -> tuple[str, ...]:
        return (
            self.resolver_product,
            self.channel_class,
            self.unit,
            self.metric,
            self.phase,
            self.gap,
            self.station,
        )


def station_day_labels(
    *,
    city: str,
    station: str,
    target_date: date,
    timezone_name: str,
    metric: str,
    resolver_product: str,
    channel_class: str,
    semantics: SettlementSemantics,
    settled: float | None,
    settled_available_at: datetime | None,
    running_extreme_at: Callable[[datetime], float | None],
    members_at: Callable[[datetime], Sequence[float] | None],
) -> list[TerminalLabel]:
    """At most one label per phase: the last hourly checkpoint with evidence.

    Checkpoints are local ``hh:00`` and belong to the phase of local hour
    ``hh``, the same attribution serving uses for a decision time.
    ``running_extreme_at`` returns the possessed running extreme in the
    contract unit; ``members_at`` returns the remaining-path members in the
    contract unit.  A missing settlement is censored: no label.
    """

    if settled is None or settled_available_at is None or not math.isfinite(float(settled)):
        return []
    tz = ZoneInfo(timezone_name)
    midnight = datetime(target_date.year, target_date.month, target_date.day, tzinfo=tz)
    labels: list[TerminalLabel] = []
    for phase, start, end in PHASES:
        for hour in range(end - 1, start - 1, -1):
            checkpoint = (midnight + timedelta(hours=hour)).astimezone(timezone.utc)
            observed = running_extreme_at(checkpoint)
            if observed is None:
                continue
            observed_settlement = contract_settlement_value(observed, semantics)
            labels.append(
                TerminalLabel(
                    city=city,
                    station=station,
                    target_date=target_date.isoformat(),
                    metric=metric,
                    resolver_product=resolver_product,
                    channel_class=channel_class,
                    unit=semantics.measurement_unit,
                    phase=phase,
                    gap=gap_category(
                        members_at(checkpoint),
                        observed_settlement=observed_settlement,
                        metric=metric,
                    ),
                    observed_settlement=observed_settlement,
                    settled=float(settled),
                    checkpoint_utc=checkpoint,
                    available_at_utc=settled_available_at,
                )
            )
            break
    return labels


# --------------------------------------------------------------------------
# Hierarchical fit
# --------------------------------------------------------------------------


def _level_keys(cell: Sequence[str]) -> tuple[str, str, str, str]:
    resolver, channel, unit, metric, phase, gap, station = cell
    l1 = f"L1|{resolver}|{channel}|{unit}|{metric}"
    l2 = f"{l1.replace('L1|', 'L2|', 1)}|{phase}|{gap}"
    return f"L0|{metric}", l1, l2, f"{l2.replace('L2|', 'L3|', 1)}|{station}"


def _metric_of(key: str) -> str:
    level, _, rest = key.partition("|")
    fields = rest.split("|")
    return fields[0] if level == "L0" else fields[3]


def _parent_key(key: str) -> str | None:
    level, _, rest = key.partition("|")
    fields = rest.split("|")
    if level == "L0":
        return None
    if level == "L1":
        return f"L0|{fields[3]}"
    if level == "L2":
        return "L1|" + "|".join(fields[:4])
    return "L2|" + "|".join(fields[:6])


def _log_beta_binomial(f: np.ndarray, n: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return betaln(f + a, n - f + b) - betaln(a, b)


def estimate_kappa(children: Sequence[tuple[int, int, float]]) -> float:
    """Beta-binomial marginal-likelihood kappa for (failures, n, parent mean).

    The estimate is floored so that every child prior carries at least one
    pseudo-observation on each side, ``kappa * min(mu, 1 - mu) >= 1``.  With
    zero observed failures the unconstrained likelihood prefers ``kappa -> 0``.
    That limit says some cells never fail, which the data cannot show, and it
    would serve near-certainty from a clean history.  The floor keeps each
    prior density bounded, so a clean cell's failure tail stays at least as
    wide as its pooled binomial evidence supports.
    """

    if not children:
        return KAPPA_GRID[-1]
    f = np.asarray([row[0] for row in children], dtype=float)
    n = np.asarray([row[1] for row in children], dtype=float)
    mu = np.clip(np.asarray([row[2] for row in children], dtype=float), _EPS, 1.0 - _EPS)
    scores = [
        float(_log_beta_binomial(f, n, kappa * mu, kappa * (1.0 - mu)).sum())
        for kappa in KAPPA_GRID
    ]
    floor = 1.0 / float(np.minimum(mu, 1.0 - mu).min())
    return max(KAPPA_GRID[int(np.argmax(scores))], floor)


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fit_resolver_terminal_residual(
    labels: Iterable[TerminalLabel],
    *,
    fit_cutoff_utc: datetime,
    station_channel: Mapping[str, str],
) -> dict[str, object]:
    """Fit the artifact from labels available strictly before the cutoff."""

    if fit_cutoff_utc.tzinfo is None:
        raise ValueError("DAY0_RESOLVER_TERMINAL_CUTOFF_NAIVE")
    nodes: dict[str, dict[str, object]] = {}
    for label in labels:
        if label.available_at_utc >= fit_cutoff_utc or label.checkpoint_utc >= fit_cutoff_utc:
            continue
        category = failure_category(label.margin)
        for key in _level_keys(label.cell()):
            node = nodes.setdefault(key, {"n": 0, "f": 0, "g": [0] * (FAILURE_STEPS + 1)})
            node["n"] = int(node["n"]) + 1
            if category is not None:
                node["f"] = int(node["f"]) + 1
                node["g"][category] += 1
    kappa: dict[str, dict[str, float]] = {}
    means: dict[str, float] = {}
    for metric in ("high", "low"):
        if f"L0|{metric}" not in nodes:
            continue
        kappa[metric] = {}
        for level in LEVELS:
            level_nodes = sorted(
                (key, node)
                for key, node in nodes.items()
                if key.startswith(f"{level}|") and _metric_of(key) == metric
            )
            if level != "L0":
                kappa[metric][level] = estimate_kappa(
                    [
                        (int(node["f"]), int(node["n"]), means[_parent_key(key)])
                        for key, node in level_nodes
                    ]
                )
            for key, node in level_nodes:
                f, n = float(node["f"]), float(node["n"])
                if level == "L0":
                    means[key] = (f + ROOT_PRIOR[0]) / (n + sum(ROOT_PRIOR))
                else:
                    k = kappa[metric][level]
                    means[key] = (f + k * means[_parent_key(key)]) / (n + k)
    artifact: dict[str, object] = {
        "schema": SCHEMA,
        "fit_cutoff_utc": fit_cutoff_utc.astimezone(timezone.utc).isoformat(),
        "failure_steps": FAILURE_STEPS,
        "phases": [list(phase) for phase in PHASES],
        "root_prior": list(ROOT_PRIOR),
        "g_root_prior": list(G_ROOT_PRIOR),
        "g_kappa": G_KAPPA,
        "kappa": kappa,
        "station_channel": dict(sorted(station_channel.items())),
        "nodes": dict(sorted(nodes.items())),
    }
    artifact["content_hash"] = hashlib.sha256(_canonical(artifact)).hexdigest()
    return artifact


# --------------------------------------------------------------------------
# Typed serving input
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Day0ResolverTerminalInput:
    """The resolved hierarchy chain for one decision cell (replayable)."""

    artifact_hash: str
    fit_cutoff_utc: str
    cell: tuple[str, ...]
    levels: tuple[tuple[str, int, int, float], ...]
    g_levels: tuple[tuple[int, ...], ...]
    root_prior: tuple[float, float] = ROOT_PRIOR
    g_root_prior: tuple[float, ...] = G_ROOT_PRIOR
    g_kappa: float = G_KAPPA

    def __post_init__(self) -> None:
        if (
            len(self.levels) != len(LEVELS)
            or len(self.g_levels) != len(LEVELS)
            or len(self.g_root_prior) != FAILURE_STEPS + 1
            or any(
                n < 0 or not 0 <= f <= n or not (math.isfinite(k) and k > 0.0)
                for _key, n, f, k in self.levels
            )
            or any(
                len(g) != FAILURE_STEPS + 1 or sum(g) != f or min(g) < 0
                for g, (_key, _n, f, _k) in zip(self.g_levels, self.levels)
            )
            or not all(value > 0.0 for value in (*self.root_prior, *self.g_root_prior, self.g_kappa))
        ):
            raise ValueError("DAY0_RESOLVER_TERMINAL_INPUT_INVALID")

    def failure_means(self) -> tuple[float, ...]:
        means: list[float] = []
        for index, (_key, n, f, kappa) in enumerate(self.levels):
            if index == 0:
                a, b = self.root_prior
                means.append((f + a) / (n + a + b))
            else:
                means.append((f + kappa * means[-1]) / (n + kappa))
        return tuple(means)

    @property
    def nonviolation_probability(self) -> float:
        return 1.0 - self.failure_means()[-1]

    def failure_magnitude(self) -> np.ndarray:
        prior = np.asarray(self.g_root_prior, dtype=float)
        mean = (prior + np.asarray(self.g_levels[0], dtype=float)) / (
            prior.sum() + sum(self.g_levels[0])
        )
        for counts in self.g_levels[1:]:
            c = np.asarray(counts, dtype=float)
            mean = (c + self.g_kappa * mean) / (c.sum() + self.g_kappa)
        return mean

    def draw(self, rng: np.random.Generator, rows: int) -> tuple[np.ndarray, np.ndarray]:
        """Nested hierarchical draws of ``(s, G-)``; parents' uncertainty flows down."""

        p = np.empty(rows, dtype=float)
        for index, (_key, n, f, kappa) in enumerate(self.levels):
            if index == 0:
                a, b = self.root_prior
                p = rng.beta(f + a, n - f + b, size=rows)
            else:
                parent = np.clip(p, _EPS, 1.0 - _EPS)
                p = rng.beta(f + kappa * parent, n - f + kappa * (1.0 - parent))
        g = rng.dirichlet(np.asarray(self.g_root_prior) + np.asarray(self.g_levels[0]), size=rows)
        for counts in self.g_levels[1:]:
            alpha = np.asarray(counts, dtype=float) + self.g_kappa * np.clip(g, _EPS, None)
            gamma = rng.standard_gamma(alpha)
            g = gamma / gamma.sum(axis=1, keepdims=True)
        return 1.0 - p, g

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "artifact_hash": self.artifact_hash,
            "fit_cutoff_utc": self.fit_cutoff_utc,
            "cell": list(self.cell),
            "levels": [list(level) for level in self.levels],
            "g_levels": [list(counts) for counts in self.g_levels],
            "root_prior": list(self.root_prior),
            "g_root_prior": list(self.g_root_prior),
            "g_kappa": self.g_kappa,
            "nonviolation_probability": self.nonviolation_probability,
            "failure_magnitude": [float(value) for value in self.failure_magnitude()],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "Day0ResolverTerminalInput":
        if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
            raise ValueError("DAY0_RESOLVER_TERMINAL_INPUT_INVALID")
        try:
            built = cls(
                artifact_hash=str(payload["artifact_hash"]),
                fit_cutoff_utc=str(payload["fit_cutoff_utc"]),
                cell=tuple(str(value) for value in payload["cell"]),
                levels=tuple(
                    (str(key), int(n), int(f), float(kappa))
                    for key, n, f, kappa in payload["levels"]
                ),
                g_levels=tuple(tuple(int(v) for v in counts) for counts in payload["g_levels"]),
                root_prior=tuple(float(v) for v in payload["root_prior"]),
                g_root_prior=tuple(float(v) for v in payload["g_root_prior"]),
                g_kappa=float(payload["g_kappa"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("DAY0_RESOLVER_TERMINAL_INPUT_INVALID") from exc
        if built.to_payload() != dict(payload):
            raise ValueError("DAY0_RESOLVER_TERMINAL_INPUT_INVALID")
        return built

    @property
    def identity(self) -> str:
        return hashlib.sha256(_canonical(self.to_payload())).hexdigest()


@dataclass(frozen=True)
class ResolverTerminalArtifact:
    """A validated fitted artifact."""

    payload: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ResolverTerminalArtifact":
        body = {key: value for key, value in payload.items() if key != "content_hash"}
        if (
            payload.get("schema") != SCHEMA
            or payload.get("failure_steps") != FAILURE_STEPS
            or payload.get("content_hash") != hashlib.sha256(_canonical(body)).hexdigest()
        ):
            raise ValueError("DAY0_RESOLVER_TERMINAL_ARTIFACT_INVALID")
        cutoff = datetime.fromisoformat(str(payload["fit_cutoff_utc"]))
        kappa = payload["kappa"]
        nodes = payload["nodes"]
        if (
            cutoff.tzinfo is None
            or not isinstance(kappa, Mapping)
            or not isinstance(nodes, Mapping)
            or any(
                f"L0|{metric}" not in nodes
                or any(
                    not (math.isfinite(float(levels[level])) and float(levels[level]) > 0.0)
                    for level in LEVELS[1:]
                )
                for metric, levels in kappa.items()
            )
        ):
            raise ValueError("DAY0_RESOLVER_TERMINAL_ARTIFACT_INVALID")
        for node in nodes.values():
            n, f, g = int(node["n"]), int(node["f"]), [int(v) for v in node["g"]]
            if not 0 <= f <= n or len(g) != FAILURE_STEPS + 1 or sum(g) != f or min(g) < 0:
                raise ValueError("DAY0_RESOLVER_TERMINAL_ARTIFACT_INVALID")
        return cls(payload=payload)

    @property
    def fit_cutoff(self) -> datetime:
        return datetime.fromisoformat(str(self.payload["fit_cutoff_utc"]))

    def channel_for(self, station: str) -> str:
        if station == HKO_STATION:
            return HKO_CHANNEL
        return str(self.payload["station_channel"].get(station, UNKNOWN_CHANNEL))

    def input_for(self, cell: Sequence[str]) -> Day0ResolverTerminalInput | None:
        keys = _level_keys(cell)
        nodes = self.payload["nodes"]
        kappa = self.payload["kappa"].get(_metric_of(keys[0]))
        if keys[0] not in nodes or kappa is None:
            return None
        empty = {"n": 0, "f": 0, "g": [0] * (FAILURE_STEPS + 1)}
        chain = [nodes.get(key, empty) for key in keys]
        return Day0ResolverTerminalInput(
            artifact_hash=str(self.payload["content_hash"]),
            fit_cutoff_utc=str(self.payload["fit_cutoff_utc"]),
            cell=tuple(str(value) for value in cell),
            levels=tuple(
                (key, int(node["n"]), int(node["f"]), 1.0 if level == "L0" else float(kappa[level]))
                for key, node, level in zip(keys, chain, LEVELS)
            ),
            g_levels=tuple(tuple(int(v) for v in node["g"]) for node in chain),
            root_prior=tuple(float(v) for v in self.payload["root_prior"]),
            g_root_prior=tuple(float(v) for v in self.payload["g_root_prior"]),
            g_kappa=float(self.payload["g_kappa"]),
        )


# --------------------------------------------------------------------------
# Serving
# --------------------------------------------------------------------------


def artifact_path() -> Path:
    from src.config import state_path

    return Path(state_path(ARTIFACT_FILENAME))


_cache_lock = threading.Lock()
_cache: dict[str, tuple[int, ResolverTerminalArtifact | None]] = {}
_logged: set[str] = set()


def _log_once(key: str, message: str, *args: object) -> None:
    if key not in _logged:
        _logged.add(key)
        _LOG.warning(message, *args)


def load_resolver_terminal_artifact(path: Path | None = None) -> ResolverTerminalArtifact | None:
    """The validated artifact, cached by mtime; None when absent or invalid."""

    target = path or artifact_path()
    try:
        mtime = target.stat().st_mtime_ns
    except OSError:
        return None
    key = str(target)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            artifact = ResolverTerminalArtifact.from_payload(
                json.loads(target.read_text(encoding="utf-8"))
            )
        except Exception as exc:  # noqa: BLE001 - invalid artifact stays dormant
            _log_once("invalid", "day0_resolver_terminal: unusable artifact %s: %s", key, exc)
            artifact = None
        _cache[key] = (mtime, artifact)
        return artifact


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _logged.clear()


def _station_and_channel_source(city: object, source: str) -> str | None:
    normalized = str(source or "").strip().lower().removeprefix("observation_prints:")
    if normalized.startswith("hko_hourly_accumulator"):
        return HKO_STATION
    if normalized.startswith(("aviationweather_metar", "ogimet_metar_")):
        station = str(getattr(city, "wu_station", "") or "").strip().upper()
        return station or None
    return None


def resolve_day0_resolver_terminal_input(
    *,
    city: object,
    target_date: object,
    metric: str,
    source: str,
    decision_time: datetime,
    boundary_native: float,
    members_native: Sequence[float],
    settlement_semantics: SettlementSemantics,
    artifact_file: Path | None = None,
) -> Day0ResolverTerminalInput | None:
    """Serving input for a provisional Day0 carrier; None only when the switch is off.

    With the switch on, the process stamps the resolver-graded semantics
    revision on every Day0 q.  Falling back to the survival mixture would then
    mislabel the q.  So a missing, invalid, non-causal or stale artifact, or a
    cell it cannot resolve, raises for this family instead.
    SCOPE: the one provisional Day0 city/date/metric family being built.
    DRAIN: ``scripts/fit_day0_resolver_terminal_residual.py`` writes a fresh,
    validated artifact (or the operator turns the switch off).
    RESET: the next build finds a causal artifact younger than
    ``MAX_ARTIFACT_AGE_DAYS`` that resolves the cell.
    """

    from src.config import day0_resolver_terminal_residual_enabled

    if not day0_resolver_terminal_residual_enabled():
        return None
    artifact = load_resolver_terminal_artifact(artifact_file)
    if artifact is None:
        raise ValueError("DAY0_RESOLVER_TERMINAL_ARTIFACT_UNAVAILABLE")
    if decision_time.tzinfo is None:
        raise ValueError("DAY0_RESOLVER_TERMINAL_DECISION_TIME_NAIVE")
    cutoff = artifact.fit_cutoff
    if cutoff > decision_time or decision_time - cutoff > timedelta(days=MAX_ARTIFACT_AGE_DAYS):
        raise ValueError("DAY0_RESOLVER_TERMINAL_ARTIFACT_NOT_CAUSAL_OR_STALE")
    station = _station_and_channel_source(city, source)
    from src.config import settlement_source_type_for_city

    resolver = RESOLVER_BY_SOURCE_TYPE.get(
        settlement_source_type_for_city(city, str(target_date)[:10]).strip().lower()
    )
    if station is None or resolver is None or metric not in {"high", "low"}:
        raise ValueError("DAY0_RESOLVER_TERMINAL_CELL_UNRESOLVED")
    observed = contract_settlement_value(boundary_native, settlement_semantics)
    local_hour = decision_time.astimezone(ZoneInfo(str(getattr(city, "timezone")))).hour
    cell = (
        resolver,
        artifact.channel_for(station),
        settlement_semantics.measurement_unit,
        metric,
        phase_of_local_hour(local_hour),
        gap_category(members_native, observed_settlement=observed, metric=metric),
        station,
    )
    resolved = artifact.input_for(cell)
    if resolved is None:
        raise ValueError("DAY0_RESOLVER_TERMINAL_CELL_UNRESOLVED")
    return resolved
