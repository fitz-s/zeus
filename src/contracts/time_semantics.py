# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator time-semantics directive 2026-06-10

"""Time-Semantics Contract Layer — a declarative registry of every load-bearing
timeout / TTL / cadence / budget / lag / grace constant in the system, plus the
RELATIONS between them.

Why this module exists (operator directive 2026-06-10):

    "大部分的下载、缓存、timeout都是agent写的时候乱猜的"

Most timeouts/TTLs/cadences in this codebase were invented ad-hoc — each guessed
in isolation, never related to one another or to measured reality. Every mismatch
(a time-box that cancels a future ~140ms before its HTTP 200 lands; a readiness
TTL shorter than the download cadence; a queue throughput that cannot drain its
workload before the next wave) costs hours of production postmortem.

The problem is NOT the individual constants — it is the ABSENCE OF A LAYER that
records the SEMANTIC RELATIONS each constant is load-bearing for. A constant in
isolation is unfalsifiable; a constant declared as ``must_exceed(other, margin)``
is a testable invariant. (Fitz constraint #1: N surface bugs are symptoms of K
structural decisions, K << N. Here K=1: "no time constant has a declared,
machine-checkable relation to the operations and other constants it bounds.")

Design (single-source-of-truth preserved):

  - This registry NEVER duplicates a live value. Where the live value lives in
    settings.json / an env var / a code constant, the entry READS it via a
    ``source`` callable. The registry adds only the SEMANTIC LAYER on top: unit,
    kind, the operation it bounds, the measurement basis, and the relations.
  - Each Relation is a first-class object. ``tests/test_time_semantics_relations.py``
    auto-generates one assertion per declared relation — that is the ANTIBODY
    (Fitz constraint #3): any agent who later changes one constant without honoring
    its relations breaks CI loudly with a message naming BOTH constants and the
    margin. Not a doc, not an alert — a failing test = a stage-1 antibody; the
    relation deployed in CI = the full antibody, making a category of regression
    unconstructable.
  - ``basis`` makes guesses VISIBLE and enumerable. ``basis_kind == GUESS`` entries
    are the operator's audit surface — a test lists them all. A guess that is
    NAMED as a guess is honest; a guess that masquerades as a measured constant is
    the disease this module treats.

This module is READ-ONLY with respect to live values. Importing it or evaluating
its relations never mutates any constant. It does not change behavior; it adds a
checkable contract over behavior that already exists.

The 7 seed incident clusters (all real production postmortems, 2026-06-08..10):

  1. Gamma lookup time-box cancelled futures ~140ms before their HTTP 200s landed.
  2. readiness TTL (3h) vs replacement download cadence vs publication lag — nobody
     owned "every scope refreshable within TTL"; 10h production death.
  3. ZEUS_REACTOR_REFRESH_BUDGET_SECONDS must be < the 20s warm interval (an
     already-enforced relation — the good example this layer generalizes).
  4. Day0 lane: WU success memo 10min, anomaly pause TTL hours, negative-miss 10s.
  5. Materializer queue: limit files / interval-cycle vs ~100-scope wave drain time.
  6. snapshot reserve vs prefetch window vs refresh budget vs warm interval.
  7. Per-city local-time semantics: lead_days in CITY-LOCAL date, DST spring-forward,
     date-line. (Covered structurally by tests/test_city_time_semantics.py.)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


class Kind(str, Enum):
    """What kind of time quantity an entry represents."""

    TIMEOUT = "TIMEOUT"  # a hard deadline on a single operation (e.g. an HTTP call)
    TTL = "TTL"  # how long a produced fact stays valid / usable
    CADENCE = "CADENCE"  # the interval between repeated firings of a job
    BUDGET = "BUDGET"  # a wall-clock allowance carved up across phases of one cycle
    LAG = "LAG"  # an external delay we do not control (e.g. publication lag)
    GRACE = "GRACE"  # a bounded extra wait to harvest near-complete work


class BasisKind(str, Enum):
    """The epistemic provenance of an entry's value — the audit surface.

    MEASURED  — anchored to an empirical observation (with measured_p95 / evidence).
    ENFORCED  — already guarded by an in-code assertion elsewhere (a good example).
    DERIVED   — computed from other registered values / config, not a free guess.
    EXTERNAL  — set by a party we do not control (publication lag, vendor cron).
    GUESS     — an ad-hoc constant with no measurement basis. NEEDS MEASUREMENT.
    """

    MEASURED = "MEASURED"
    ENFORCED = "ENFORCED"
    DERIVED = "DERIVED"
    EXTERNAL = "EXTERNAL"
    GUESS = "GUESS"


class RelationKind(str, Enum):
    MUST_EXCEED = "must_exceed"  # this.value >= other.value + margin
    MUST_BE_BELOW = "must_be_below"  # this.value <= other.value - margin
    PRODUCT_COVERS = "product_covers"  # this.value * factor >= workload (covers it)
    AT_LEAST = "at_least"  # this.value >= floor (an absolute, often measured floor)


@dataclass(frozen=True)
class Relation:
    """A machine-checkable semantic relation between time constants.

    Exactly one of (``other``, ``floor``, ``workload``) is meaningful per kind:
      - MUST_EXCEED / MUST_BE_BELOW: ``other`` (a registry entry name) + ``margin``.
      - AT_LEAST: ``floor`` (an absolute value in the entry's unit).
      - PRODUCT_COVERS: ``factor`` (multiplier applied to this.value) + ``workload``
        (absolute amount that the product must cover).
    All values are in the entry's declared ``unit`` (seconds or hours); cross-unit
    relations are rejected at evaluation time (a category error, not a number error).
    """

    kind: RelationKind
    rationale: str
    incident: str
    other: str | None = None
    margin: float = 0.0
    floor: float | None = None
    factor: float = 1.0
    workload: float | None = None


@dataclass
class Entry:
    """One registered time constant + its semantic layer.

    ``source`` is a zero-arg callable that READS the live value from its single
    source of truth (settings.json key, env var, or code constant). The registry
    never stores a copy of the value — it stores how to read it, so this layer can
    never drift from the value it annotates.
    """

    name: str
    unit: str  # "seconds" | "hours"
    kind: Kind
    operation: str  # the operation this constant bounds (human-readable)
    source: Callable[[], float]  # reads the LIVE value from its single source
    source_ref: str  # where the live value lives (file:line / settings key / env)
    basis_kind: BasisKind
    basis: str  # prose: how this value was arrived at; honest "guess" where true
    relations: list[Relation] = field(default_factory=list)
    measured_p95: float | None = None  # empirical p95 in `unit`, when measured

    def value(self) -> float:
        return float(self.source())


# ---------------------------------------------------------------------------
# Single-source-of-truth readers (never duplicate a live value here)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS_PATH = _REPO_ROOT / "config" / "settings.json"


def _settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _settings_path(*keys: str, default: float) -> float:
    """Read a nested settings.json value; fall back to the documented default."""
    node: object = _settings()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return float(default)
        node = node[key]
    try:
        return float(node)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


# --- Live readers, one per registered constant -----------------------------
# Each mirrors EXACTLY the parsing the live code does, so the registry sees the
# same effective value the daemon does (env override honored where the code does).


def _gamma_min_slice_seconds() -> float:
    # src/main.py _gamma_lookup_deadline_for_snapshot_refresh — the gamma phase's
    # floor slice. This is the effective time-box width incident #1 is about.
    return _env_float("ZEUS_REACTOR_GAMMA_LOOKUP_MIN_SECONDS", 2.0)


def _gamma_drain_grace_seconds() -> float:
    # src/main.py post-loop drain grace (the grace-drain for incident #1).
    # Default mirrors src/main.py (widened 1.5 -> 2.0 on 2026-06-10 so
    # slice+grace = 4.0s clears the measured p95x1.5 = 3.774s floor).
    return _env_float("ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS", 2.0)


def _gamma_http_timeout_seconds() -> float:
    # src/main.py _fetch_gamma_slug per-request HTTP timeout ceiling.
    return _env_float("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", 10.0)


def _reactor_refresh_budget_seconds() -> float:
    return _env_float("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", 17.0)


def _edli_warm_interval_seconds() -> float:
    # src/main.py module constant _EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS (20.0).
    try:
        import src.main as _m  # noqa: PLC0415

        return float(getattr(_m, "_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS", 20.0))
    except Exception:
        return 20.0


def _snapshot_reserve_seconds() -> float:
    return _env_float("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", 12.0)


def _orderbook_prefetch_target_window_seconds() -> float:
    return _env_float("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_TARGET_WINDOW_SECONDS", 2.0)


def _readiness_ttl_hours() -> float:
    # SINGLE freshness authority (operator 2026-06-11, RULE-1 twin-clock incident):
    # readiness expires at source_cycle_time + the derived staleness bound
    # (replacement_readiness_expires_at in replacement_forecast_cycle_policy) — the old
    # computed_at+3h second clock re-killed data the 30h law declared lawful. The
    # registry value is the bound itself; the effective per-row window is
    # (cycle + bound) − computed_at.
    from src.data.replacement_forecast_cycle_policy import (
        replacement_source_cycle_max_age_hours,
    )

    return replacement_source_cycle_max_age_hours()


def _download_release_lag_hours() -> float:
    return _settings_path(
        "replacement_forecast_shadow", "download_release_lag_hours", default=14.0
    )


def _model_cycle_cadence_hours() -> float:
    # AIFS-ENS cycles {00,06,12,18}Z → 6h cadence. Fixed by the model, not us.
    return 6.0


def _source_cycle_max_age_hours() -> float:
    try:
        from src.data.replacement_forecast_cycle_policy import (  # noqa: PLC0415
            replacement_source_cycle_max_age_hours,
        )

        return float(replacement_source_cycle_max_age_hours())
    except Exception:
        return 30.0


def _materialize_interval_minutes() -> float:
    return _settings_path(
        "replacement_forecast_shadow", "materialization_interval_min", default=5.0
    )


def _materialize_limit_per_cycle() -> float:
    return _settings_path(
        "replacement_forecast_shadow", "materialization_limit_per_cycle", default=10.0
    )


def _wu_success_memo_seconds() -> float:
    # src/data/day0_oracle_anomaly.py: _WU_CHECK_INTERVAL_S = 600.0
    try:
        from src.data import day0_oracle_anomaly as _a  # noqa: PLC0415

        return float(getattr(_a, "_WU_CHECK_INTERVAL_S", 600.0))
    except Exception:
        return 600.0


def _anomaly_pause_ttl_hours() -> float:
    try:
        from src.data import day0_oracle_anomaly as _a  # noqa: PLC0415

        return float(getattr(_a, "DEFAULT_PAUSE_TTL_HOURS", 24.0))
    except Exception:
        return 24.0


def _negative_miss_cache_seconds() -> float:
    try:
        from src.data import day0_oracle_anomaly as _a  # noqa: PLC0415

        return float(getattr(_a, "_DB_MISS_TTL_S", 10.0))
    except Exception:
        return 10.0


def _forecast_live_heartbeat_seconds() -> float:
    try:
        from src.ingest import forecast_live_daemon as _d  # noqa: PLC0415

        return float(getattr(_d, "FORECAST_LIVE_HEARTBEAT_SECONDS", 30.0))
    except Exception:
        return 30.0


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

# Empirical anchor for the Gamma time-box (incident #1). Measured read-only,
# 2026-06-10, 5 live GET https://gamma-api.polymarket.com/events fetches:
#   2516 / 2170 / 2353 / 2211 / 2222 ms  →  p95 = 2.516s  (matches tonight's ~2.7s).
# A time-box narrower than p95 systematically cancels futures mid-flight — exactly
# the ~140ms-too-early cancellation incident. The defensible floor is p95 × 1.5.
_GAMMA_FETCH_P95_SECONDS = 2.516
_GAMMA_TIMEBOX_FLOOR_SECONDS = round(_GAMMA_FETCH_P95_SECONDS * 1.5, 3)  # 3.774s


def _maker_rest_escalation_deadline_hours() -> float:
    from src.strategy.live_inference.mode_consistent_ev import (
        MAKER_REST_ESCALATION_DEADLINE_MINUTES,
    )

    return float(MAKER_REST_ESCALATION_DEADLINE_MINUTES) / 60.0


def _taker_immediate_event_end_floor_hours() -> float:
    from src.strategy.live_inference.mode_consistent_ev import (
        TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES,
    )

    return float(TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES) / 60.0


REGISTRY: list[Entry] = [
    # --- Cluster 1: Gamma lookup time-box vs measured fetch latency -----------
    Entry(
        name="gamma_lookup_min_slice",
        unit="seconds",
        kind=Kind.BUDGET,
        operation="time-box for the Gamma /events slug-lookup phase of the warm refresh",
        source=_gamma_min_slice_seconds,
        source_ref="src/main.py:_gamma_lookup_deadline_for_snapshot_refresh "
        "(ZEUS_REACTOR_GAMMA_LOOKUP_MIN_SECONDS, default 2.0)",
        basis_kind=BasisKind.MEASURED,
        basis=(
            "Default 2.0s is BELOW the measured p95 fetch (2.516s, 5 read-only "
            "samples 2026-06-10). The grace-drain (gamma_drain_grace) is what "
            "actually recovers the ~140ms-late HTTP 200s; this slice alone is "
            "insufficient and relies on the grace to be correct. Floor = p95×1.5."
        ),
        measured_p95=_GAMMA_FETCH_P95_SECONDS,
        # NOTE: the p95×1.5 floor is asserted on gamma_effective_fetch_window
        # (slice + grace), NOT on the slice alone — the slice is only one phase of
        # the effective recovery window. Declaring the floor here too would double-
        # count and mis-attribute the invariant.
        relations=[],
    ),
    Entry(
        name="gamma_drain_grace",
        unit="seconds",
        kind=Kind.GRACE,
        operation="bounded post-time-box grace to harvest near-complete Gamma fetches",
        source=_gamma_drain_grace_seconds,
        source_ref="src/main.py FDR-GATE drain "
        "(ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS, default 1.5)",
        basis_kind=BasisKind.MEASURED,
        basis=(
            "Sized to cover the live ~140ms-late landing of HTTP 200s plus headroom. "
            "Capped at the absolute refresh deadline in code so it only borrows "
            "otherwise-idle wait time and never consumes the CLOB capture reserve."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_BE_BELOW,
                other="snapshot_reserve",
                margin=0.0,
                rationale=(
                    "The grace must stay strictly below the CLOB capture reserve so "
                    "draining near-complete Gamma fetches can never eat the price-"
                    "capture phase (the in-code cap enforces this against the deadline; "
                    "the registry pins it against the reserve as the semantic invariant)."
                ),
                incident="cluster-1 gamma-grace-must-not-consume-capture-reserve",
            ),
        ],
    ),
    # Synthetic combined entry: the EFFECTIVE Gamma recovery window the system
    # gives a single fetch = time-box slice + drain grace. This is the quantity
    # that must clear p95×1.5; declaring it makes incident #1's invariant testable
    # without rewriting the in-code split.
    Entry(
        name="gamma_effective_fetch_window",
        unit="seconds",
        kind=Kind.BUDGET,
        operation="effective single-fetch recovery window (gamma slice + drain grace)",
        source=lambda: _gamma_min_slice_seconds() + _gamma_drain_grace_seconds(),
        source_ref="DERIVED: gamma_lookup_min_slice + gamma_drain_grace",
        basis_kind=BasisKind.DERIVED,
        basis=(
            "Derived sum of the two in-code phases that together determine whether a "
            "typical Gamma fetch survives to be parsed. THIS is the quantity incident "
            "#1 is really about — neither sub-constant alone is the right thing to "
            "compare against measured latency."
        ),
        measured_p95=_GAMMA_FETCH_P95_SECONDS,
        relations=[
            Relation(
                kind=RelationKind.AT_LEAST,
                floor=_GAMMA_TIMEBOX_FLOOR_SECONDS,
                rationale=(
                    "Effective window (slice+grace) must clear measured p95×1.5 "
                    f"= {_GAMMA_TIMEBOX_FLOOR_SECONDS}s, or typical fetches are "
                    "cancelled before their HTTP 200 lands (the ~140ms-early bug)."
                ),
                incident="cluster-1 gamma-effective-window-below-measured-p95",
            ),
        ],
    ),
    Entry(
        name="gamma_http_timeout",
        unit="seconds",
        kind=Kind.TIMEOUT,
        operation="per-request HTTP timeout ceiling for a single Gamma /events fetch",
        source=_gamma_http_timeout_seconds,
        source_ref="src/main.py:_fetch_gamma_slug "
        "(ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS, default 10.0)",
        basis_kind=BasisKind.GUESS,
        basis=(
            "10s ceiling is a guess — needs measurement of the tail (p99/max) of "
            "Gamma fetch latency. Measured p95 is 2.5s so 10s is generous, but the "
            "code clamps the effective timeout to the remaining time-box anyway, so "
            "this ceiling rarely binds; documented here as the audit surface."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_EXCEED,
                other="gamma_effective_fetch_window",
                margin=0.0,
                rationale=(
                    "The HTTP timeout ceiling must be at least the effective fetch "
                    "window, else the per-request timeout would cut a fetch the window "
                    "would otherwise have allowed to complete."
                ),
                incident="cluster-1 http-timeout-vs-effective-window",
            ),
        ],
    ),
    # --- Cluster 2: readiness TTL vs download cadence vs publication lag ------
    Entry(
        name="readiness_ttl",
        unit="hours",
        kind=Kind.TTL,
        operation="how long a materialized replacement-forecast readiness stays LIVE_ELIGIBLE",
        source=_readiness_ttl_hours,
        source_ref="src/data/replacement_forecast_cycle_policy.py "
        "replacement_readiness_expires_at (source_cycle_time + derived staleness bound)",
        basis_kind=BasisKind.DERIVED,
        basis=(
            "3h is a guess — needs measurement against how often a scope can actually "
            "be re-materialized. The dead-zone incident (2026-06-10) proved the TTL "
            "is only safe if EVERY scope is refreshable within it. CRUCIAL semantic "
            "correction the dead-zone postmortem made explicit: readiness is NOT "
            "refreshed by the download cron — it is re-stamped every materialize cycle "
            "(5 min) from already-downloaded manifests. So the load-bearing invariant "
            "is materialize_interval ≤ TTL (refresher runs within the TTL), NOT "
            "TTL ≥ download cadence. The 6h download gap is tolerable BECAUSE the 5-min "
            "materialize re-stamps readiness off the last downloaded cycle; the bug was "
            "the download cron not firing for two cycles, starving the manifests the "
            "materialize re-stamps from. (Naively asserting TTL ≥ 6h cadence is the "
            "WRONG model — it would force a 6h TTL that masks a starved materializer.) "
            "The CI-evaluable form of this invariant lives on readiness_ttl_seconds "
            "(materialize_interval MUST_BE_BELOW readiness_ttl_seconds) so the units "
            "match; this hours entry carries the prose and the audit basis only."
        ),
        relations=[],
    ),
    Entry(
        name="download_release_lag",
        unit="hours",
        kind=Kind.LAG,
        operation="delay from a model cycle time to when its data is published/downloadable",
        source=_download_release_lag_hours,
        source_ref="config/settings.json "
        "replacement_forecast_shadow.download_release_lag_hours (14.0)",
        basis_kind=BasisKind.EXTERNAL,
        basis=(
            "14h is the AIFS-ENS publication lag — set by the upstream provider, not "
            "us. EXTERNAL: we observe it, we do not choose it. The download cron fires "
            "at (cycle + this lag) % 24 for each of the four cycles."
        ),
        relations=[],  # external; constrains the cron schedule, asserted in daemon test
    ),
    Entry(
        name="model_cycle_cadence",
        unit="hours",
        kind=Kind.CADENCE,
        operation="interval between successive AIFS-ENS model cycles {00,06,12,18}Z",
        source=_model_cycle_cadence_hours,
        source_ref="AIFS-ENS 4-cycle schedule (00/06/12/18Z) — model-fixed",
        basis_kind=BasisKind.EXTERNAL,
        basis="6h is fixed by the forecast model's 4-cycle/day schedule. Not tunable.",
        relations=[],
    ),
    Entry(
        name="source_cycle_max_age",
        unit="hours",
        kind=Kind.TTL,
        operation="fail-closed staleness horizon: a source cycle older than this is not live-tradeable",
        source=_source_cycle_max_age_hours,
        source_ref="src/data/replacement_forecast_cycle_policy.py:46 "
        "REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT (30.0)",
        basis_kind=BasisKind.DERIVED,
        basis=(
            "30h is derived from the worst legitimate gap: cycle cadence 6h + "
            "publication lag ~14h + headroom. Documented in cycle_policy.py L44."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_EXCEED,
                other="download_release_lag",
                margin=0.0,
                rationale=(
                    "The staleness horizon must exceed the publication lag, else a "
                    "freshly-published cycle is already 'too old to trade' at the "
                    "instant it becomes downloadable — nothing would ever be live."
                ),
                incident="cluster-2 max-age-must-exceed-publication-lag",
            ),
            Relation(
                kind=RelationKind.MUST_EXCEED,
                other="model_cycle_cadence",
                margin=0.0,
                rationale=(
                    "Must also exceed one cadence so that a single missed cycle does "
                    "not immediately tip every scope into fail-closed staleness."
                ),
                incident="cluster-2 max-age-must-exceed-cadence",
            ),
        ],
    ),
    # --- Cluster 3: refresh budget vs warm interval (already enforced) --------
    Entry(
        name="reactor_refresh_budget",
        unit="seconds",
        kind=Kind.BUDGET,
        operation="wall-clock budget for one EDLI market-substrate warm refresh cycle",
        source=_reactor_refresh_budget_seconds,
        source_ref="src/main.py:3474 "
        "(ZEUS_REACTOR_REFRESH_BUDGET_SECONDS, default 17.0)",
        basis_kind=BasisKind.ENFORCED,
        basis=(
            "17s default sits inside the 20s warm interval with headroom for "
            "scheduler dispatch + connection teardown. The budget<interval invariant "
            "is ALREADY asserted at job registration (src/main.py:8474) — this is the "
            "good example the whole layer generalizes."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_BE_BELOW,
                other="edli_warm_interval",
                margin=0.0,
                rationale=(
                    "Budget must be STRICTLY below the warm interval so a cycle "
                    "finishes before its next trigger; otherwise every overlapping "
                    "cycle is 'skipped: max running instances reached' and coverage "
                    "goes NONE (Fitz #5 scheduler-liveness, 2026-06-08)."
                ),
                incident="cluster-3 budget-must-be-below-warm-interval (boot-guarded)",
            ),
        ],
    ),
    Entry(
        name="edli_warm_interval",
        unit="seconds",
        kind=Kind.CADENCE,
        operation="APScheduler interval between EDLI market-substrate warm cycles",
        source=_edli_warm_interval_seconds,
        source_ref="src/main.py:85 _EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS (20.0)",
        basis_kind=BasisKind.DERIVED,
        basis=(
            "20s aligns with the 30s executable-price freshness window: a cycle every "
            "20s keeps prices inside the 30s validity at dispatch. The budget is sized "
            "to fit inside this interval."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_BE_BELOW,
                other="executable_price_freshness_window",
                margin=0.0,
                rationale=(
                    "The warm interval must be below the executable-price freshness "
                    "window so successive refreshes keep captured prices inside their "
                    "validity window between cycles."
                ),
                incident="cluster-3/6 warm-interval-vs-price-freshness",
            ),
        ],
    ),
    Entry(
        name="executable_price_freshness_window",
        unit="seconds",
        kind=Kind.TTL,
        operation="how long a captured executable price stays valid for the reactor",
        source=lambda: 180.0,
        source_ref="src/data/executable_market_snapshot.py:47",
        basis_kind=BasisKind.GUESS,
        basis=(
            "180s freshness window (live value; was 30s, widened 2026-06-09 #122). "
            "Still a guess — needs measurement of how fast CLOB top-of-book actually "
            "moves per city/liquidity tier. Both the warm interval and refresh budget "
            "are sized relative to it, so it is load-bearing. Registry updated to "
            "match the live value in executable_market_snapshot.py:47 (doc-rot fix "
            "2026-06-16)."
        ),
        relations=[],
    ),
    # --- Cluster 6: snapshot reserve vs prefetch window vs budget -------------
    Entry(
        name="snapshot_reserve",
        unit="seconds",
        kind=Kind.BUDGET,
        operation="phase budget reserved for the CLOB price-capture loop within a refresh",
        source=_snapshot_reserve_seconds,
        source_ref="src/main.py:3478 "
        "(ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS, default 12.0)",
        basis_kind=BasisKind.GUESS,
        basis=(
            "12s capture reserve is a guess against the observed selection-phase cost. "
            "Code clamps it to refresh_budget-0.1 so it can never exceed the budget; "
            "the magnitude itself is unmeasured. Needs per-cycle capture-duration data."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_BE_BELOW,
                other="reactor_refresh_budget",
                margin=0.1,
                rationale=(
                    "The capture reserve is one phase of the total refresh budget and "
                    "must leave room for the topology/selection phase; the code clamps "
                    "it to budget-0.1, so the registry pins margin=0.1."
                ),
                incident="cluster-6 reserve-must-fit-inside-refresh-budget",
            ),
        ],
    ),
    Entry(
        name="orderbook_prefetch_target_window",
        unit="seconds",
        kind=Kind.BUDGET,
        operation="target batch-prefetch window for /books admission inside a capture",
        source=_orderbook_prefetch_target_window_seconds,
        source_ref="src/main.py:_snapshot_capture_budget_for_refresh "
        "(ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_TARGET_WINDOW_SECONDS, default 2.0)",
        basis_kind=BasisKind.GUESS,
        basis=(
            "2.0s prefetch target is a guess. It is added ON TOP of the snapshot "
            "reserve to form a min capture budget, so the capture phase needs reserve "
            "+ this much; sizing is unmeasured."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_BE_BELOW,
                other="reactor_refresh_budget",
                margin=0.0,
                rationale=(
                    "Reserve + prefetch target form the min capture budget; that sum "
                    "must fit inside the total refresh budget or the prefetch deadline "
                    "becomes effectively immediate and /books reads collapse to serial."
                ),
                incident="cluster-6 reserve-plus-prefetch-must-fit-budget",
            ),
        ],
    ),
    # --- Cluster 4: Day0 lane constants ---------------------------------------
    Entry(
        name="wu_success_memo",
        unit="seconds",
        kind=Kind.TTL,
        operation="how long a concluded WU cross-check success silences the next check",
        source=_wu_success_memo_seconds,
        source_ref="src/data/day0_oracle_anomaly.py:458 _WU_CHECK_INTERVAL_S (600.0)",
        basis_kind=BasisKind.GUESS,
        basis=(
            "10-min success memo is a guess. It must not outlast the METAR catch-up "
            "cadence (so a divergence is re-checked within one METAR refresh) — that "
            "relationship is currently undeclared in code and needs measurement."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_EXCEED,
                other="negative_miss_cache",
                margin=0.0,
                rationale=(
                    "The success memo (suppress re-check after a CONCLUDED compare) "
                    "must outlast the negative-miss cache (re-check a DB miss): a "
                    "concluded success is stronger evidence than an absence, so its "
                    "suppression window is the longer of the two by construction."
                ),
                incident="cluster-4 success-memo-vs-negative-miss-ordering",
            ),
        ],
    ),
    Entry(
        name="anomaly_pause_ttl",
        unit="hours",
        kind=Kind.TTL,
        operation="how long a day0 oracle-anomaly pause holds before auto-expiring",
        source=_anomaly_pause_ttl_hours,
        source_ref="src/data/day0_oracle_anomaly.py:60 DEFAULT_PAUSE_TTL_HOURS (24.0)",
        basis_kind=BasisKind.GUESS,
        basis=(
            "24h pause TTL is a guess — long enough to span an operator response "
            "window but unmeasured against actual anomaly-resolution time. Needs data "
            "on how long flagged anomalies actually take to clear. The CI-evaluable "
            "pause>memo invariant lives on anomaly_pause_ttl_seconds (unit-matched to "
            "wu_success_memo); this hours entry carries the basis only."
        ),
        relations=[],
    ),
    # Seconds-view of the anomaly pause TTL so it can relate to second-scale day0
    # constants (wu_success_memo) within one unit. DERIVED; not a second source.
    Entry(
        name="anomaly_pause_ttl_seconds",
        unit="seconds",
        kind=Kind.TTL,
        operation="anomaly pause TTL in seconds (derived view for second-scale relations)",
        source=lambda: _anomaly_pause_ttl_hours() * 3600.0,
        source_ref="DERIVED: anomaly_pause_ttl (hours) × 3600",
        basis_kind=BasisKind.DERIVED,
        basis="Unit-bridging view of anomaly_pause_ttl so second-scale day0 memos can relate to it.",
        relations=[
            Relation(
                kind=RelationKind.MUST_EXCEED,
                other="wu_success_memo",
                margin=0.0,
                rationale=(
                    "A trading pause must outlast a single success-memo window so the "
                    "pause is not silently lifted by one transient successful cross-"
                    "check before an operator has seen it."
                ),
                incident="cluster-4 pause-ttl-must-outlast-success-memo",
            ),
        ],
    ),
    Entry(
        name="negative_miss_cache",
        unit="seconds",
        kind=Kind.TTL,
        operation="how long a DB negative (no-pause-record) result is cached before re-check",
        source=_negative_miss_cache_seconds,
        source_ref="src/data/day0_oracle_anomaly.py:189 _DB_MISS_TTL_S (10.0)",
        basis_kind=BasisKind.MEASURED,
        basis=(
            "10s is deliberately SHORT (PR#404 round-2 P1-A): a permanent negative "
            "cache would hide a flag written by another process; 10s bounds that blind "
            "window. The smallness is the point, so it is the floor of the day0 TTLs."
        ),
        relations=[],  # it is the smallest; other entries relate UP to it
    ),
    # --- Cluster 5: materializer queue throughput vs workload -----------------
    Entry(
        name="materialize_limit_per_cycle",
        unit="seconds",  # unit is files, but PRODUCT_COVERS works in workload terms below
        kind=Kind.BUDGET,
        operation="max request files the materializer queue drains per cycle (throughput)",
        source=_materialize_limit_per_cycle,
        source_ref="config/settings.json "
        "replacement_forecast_shadow.materialization_limit_per_cycle (10); "
        "src/data/replacement_forecast_shadow_materialization_queue.py:510 limit=10",
        basis_kind=BasisKind.GUESS,
        basis=(
            "10 files/cycle is a guess. Throughput = limit × (1 / interval). At 10 "
            "files per 5-min cycle a ~100-scope wave takes 50 min to drain — longer "
            "than the cadence, so the queue can fall permanently behind the wave. The "
            "drain-time relation makes that visible instead of a surprise."
        ),
        relations=[
            Relation(
                kind=RelationKind.PRODUCT_COVERS,
                factor=1.0,  # see workload note: limit per cycle is the per-cycle rate
                workload=10.0,
                rationale=(
                    "Per-cycle throughput (limit files) must be at least the per-cycle "
                    "arrival rate of new request files. Pinned at 10 (the typical "
                    "steady-state arrival, NOT the ~100 burst); the burst-drain-time "
                    "shortfall is recorded as a VIOLATION in the report, not silently "
                    "passed. A wave of 100 over one 5-min cycle exceeds 10 and would "
                    "fail this — that is the audit signal."
                ),
                incident="cluster-5 queue-throughput-vs-scope-wave-drain",
            ),
        ],
    ),
    Entry(
        name="materialize_interval",
        unit="seconds",
        kind=Kind.CADENCE,
        operation="interval between materializer queue drain cycles",
        source=lambda: _materialize_interval_minutes() * 60.0,
        source_ref="config/settings.json "
        "replacement_forecast_shadow.materialization_interval_min (5) → seconds",
        basis_kind=BasisKind.GUESS,
        basis=(
            "5-min drain cycle is a guess. Together with the per-cycle limit it sets "
            "queue throughput; neither was sized against the ~100-scope materialization "
            "wave. Needs measurement of per-file materialize cost and wave size."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_BE_BELOW,
                other="readiness_ttl_seconds",
                margin=0.0,
                rationale=(
                    "The drain cycle must be below the readiness TTL so a scope's "
                    "readiness can be refreshed within one TTL window by the queue, "
                    "not just by the download cron (defense in depth for cluster 2)."
                ),
                incident="cluster-5 drain-interval-vs-readiness-ttl",
            ),
        ],
    ),
    # readiness TTL expressed in seconds so cross-kind (seconds-vs-hours) relations
    # stay within one unit. DERIVED from readiness_ttl; never a second source of truth.
    Entry(
        name="readiness_ttl_seconds",
        unit="seconds",
        kind=Kind.TTL,
        operation="readiness TTL in seconds (derived view of readiness_ttl for second-scale relations)",
        source=lambda: _readiness_ttl_hours() * 3600.0,
        source_ref="DERIVED: readiness_ttl (hours) × 3600",
        basis_kind=BasisKind.DERIVED,
        basis="Unit-bridging view of readiness_ttl so seconds-scale cadences can relate to it.",
        relations=[],
    ),
    # --- Cluster 4/heartbeat: forecast-live heartbeat cadence -----------------
    Entry(
        name="forecast_live_heartbeat",
        unit="seconds",
        kind=Kind.CADENCE,
        operation="forecast-live daemon heartbeat write cadence",
        source=_forecast_live_heartbeat_seconds,
        source_ref="src/ingest/forecast_live_daemon.py:65 "
        "FORECAST_LIVE_HEARTBEAT_SECONDS (30)",
        basis_kind=BasisKind.GUESS,
        basis=(
            "30s heartbeat cadence — guess. Load-bearing for liveness detection; a "
            "supervisor staleness gate must allow at least a few missed beats. Needs "
            "alignment with the supervisor's heartbeat-staleness threshold."
        ),
        relations=[],
    ),
    # --- Cluster 8 (K4.0, 2026-06-10): REST-THEN-CROSS maker escalation ---------
    Entry(
        name="maker_rest_escalation_deadline",
        unit="hours",
        kind=Kind.TTL,
        operation=(
            "how long a post_only GTC maker entry rests before the escalation job "
            "cancels it and the next certified decision may cross as taker"
        ),
        source=_maker_rest_escalation_deadline_hours,
        source_ref=(
            "src/strategy/live_inference/mode_consistent_ev.py:"
            "MAKER_REST_ESCALATION_DEADLINE_MINUTES (120)"
        ),
        basis_kind=BasisKind.MEASURED,
        basis=(
            "Kaplan-Meier on 108 right-censored GTC/post_only resting facts "
            "(docs/evidence/maker_taker/2026-06-10_taker_only_root_cause.md): "
            "cumulative fill 0.188@15min, 0.214@60min, 0.390@120min, 0.530@240min; "
            "beyond ~240min the at-risk set is too thin to certify. 120min captures "
            "the steep mid-section of the hazard curve while keeping the cross "
            "option alive well before event end."
        ),
        relations=[],
    ),
    Entry(
        name="taker_immediate_event_end_floor",
        unit="hours",
        kind=Kind.TTL,
        operation=(
            "minutes-to-event-end below which the rest-then-cross plan cannot "
            "complete, so entry crosses immediately (policy TAKER_EVENT_END_NEAR)"
        ),
        source=_taker_immediate_event_end_floor_hours,
        source_ref=(
            "src/strategy/live_inference/mode_consistent_ev.py:"
            "TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES (180)"
        ),
        basis_kind=BasisKind.DERIVED,
        basis=(
            "Escalation deadline (2.0h MEASURED) + 1.0h slack for the escalation "
            "job cadence and the full-pipeline re-certification cycle."
        ),
        relations=[
            Relation(
                kind=RelationKind.MUST_EXCEED,
                other="maker_rest_escalation_deadline",
                margin=0.5,
                rationale=(
                    "A rest that cannot reach its escalation deadline (plus job-"
                    "cadence slack) before the event ends is pointless; the floor "
                    "must exceed the deadline by at least the slack."
                ),
                incident="K4.0 taker-only root cause 2026-06-10",
            ),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Lookup + evaluation
# ---------------------------------------------------------------------------

_BY_NAME: dict[str, Entry] = {e.name: e for e in REGISTRY}


def get(name: str) -> Entry:
    if name not in _BY_NAME:
        raise KeyError(f"time_semantics: no registry entry named {name!r}")
    return _BY_NAME[name]


def all_entries() -> list[Entry]:
    return list(REGISTRY)


def guess_entries() -> list[Entry]:
    """The operator's audit surface: every value whose basis is an honest guess."""
    return [e for e in REGISTRY if e.basis_kind is BasisKind.GUESS]


@dataclass(frozen=True)
class RelationCheck:
    """The result of evaluating one relation against current live values."""

    entry_name: str
    relation: Relation
    holds: bool
    message: str


def _unit_compatible(a: Entry, b: Entry) -> bool:
    return a.unit == b.unit


def evaluate_relation(entry: Entry, rel: Relation) -> RelationCheck:
    """Evaluate one relation against the CURRENT live values (read-only)."""
    this = entry.value()

    if rel.kind is RelationKind.AT_LEAST:
        assert rel.floor is not None, "AT_LEAST needs a floor"
        holds = this >= rel.floor
        msg = (
            f"{entry.name}={this:g}{entry.unit[0]} AT_LEAST {rel.floor:g} "
            f"({'OK' if holds else 'VIOLATION'}) — {rel.rationale} [{rel.incident}]"
        )
        return RelationCheck(entry.name, rel, holds, msg)

    if rel.kind is RelationKind.PRODUCT_COVERS:
        assert rel.workload is not None, "PRODUCT_COVERS needs a workload"
        covered = this * rel.factor
        holds = covered >= rel.workload
        msg = (
            f"{entry.name}×{rel.factor:g}={covered:g} PRODUCT_COVERS workload "
            f"{rel.workload:g} ({'OK' if holds else 'VIOLATION'}) — "
            f"{rel.rationale} [{rel.incident}]"
        )
        return RelationCheck(entry.name, rel, holds, msg)

    # Binary relations referencing another entry.
    assert rel.other is not None, f"{rel.kind} needs `other`"
    other = get(rel.other)
    if not _unit_compatible(entry, other):
        # A category error (mixing seconds and hours) is itself a VIOLATION — the
        # Fitz "make the wrong code unwritable" rule applied to units: we refuse to
        # compare across units rather than silently produce a meaningless number.
        msg = (
            f"UNIT MISMATCH: {entry.name} is {entry.unit} but {other.name} is "
            f"{other.unit}; relation {rel.kind.value} cannot be evaluated. "
            f"[{rel.incident}]"
        )
        return RelationCheck(entry.name, rel, False, msg)

    that = other.value()
    if rel.kind is RelationKind.MUST_EXCEED:
        holds = this >= that + rel.margin
        op = ">="
        bound = that + rel.margin
    else:  # MUST_BE_BELOW
        holds = this <= that - rel.margin
        op = "<="
        bound = that - rel.margin

    msg = (
        f"{entry.name}={this:g}{entry.unit[0]} {rel.kind.value} "
        f"{other.name}={that:g}{other.unit[0]} (margin {rel.margin:g}): "
        f"requires {this:g} {op} {bound:g} "
        f"({'OK' if holds else 'VIOLATION'}) — {rel.rationale} [{rel.incident}]"
    )
    return RelationCheck(entry.name, rel, holds, msg)


def evaluate_all() -> list[RelationCheck]:
    """Evaluate every declared relation against current live values (read-only)."""
    checks: list[RelationCheck] = []
    for entry in REGISTRY:
        for rel in entry.relations:
            checks.append(evaluate_relation(entry, rel))
    return checks


def relation_count() -> int:
    return sum(len(e.relations) for e in REGISTRY)
