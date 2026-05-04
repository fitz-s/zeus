# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v2.md §6.P3 (D-B mode→phase migration; v3 per §0.1).
"""D-B dispatch helpers.

PLAN_v3 §6.P3 migrates strategy/observation dispatch from
``DiscoveryMode.DAY0_CAPTURE`` (cycle-axis) to
``MarketPhase.SETTLEMENT_DAY`` (market-axis). The migration is
**flag-gated, default OFF**: with the flag unset, dispatch is
byte-equal to pre-P3 (T6 invariant in PLAN_v2 §8). With the flag set,
dispatch reads ``candidate.market_phase`` instead of
``candidate.discovery_mode``.

Why a flag rather than a hard cutover:

- Critic R2 C6 + R1 C5 require that no single PR flips dispatch for
  all 51 cities at once without an evidence cohort. The flag lets P3
  ship the migration scaffolding while keeping production on the
  legacy path until an explicit ON/OFF decision and supporting evidence
  bundle (per ``docs/operations/activation/UNLOCK_CRITERIA.md`` precedent).
- Once the flag is ON, ``MarketPhase`` becomes the dispatch axis. Once
  it is locked ON for ≥1 stable week with no regressions, P3.5 can
  excise the legacy branch.

This module is the single locus for the dispatch decision so the four
call sites (3 in evaluator.py + 1 in cycle_runtime.py) all read the
same flag and the same logic. Cycle-axis sites
(cycle_runner.py:_classify_edge_source / freshness short-circuit) are
NOT migrated by P3 because they operate before per-candidate phase is
available — see ``settlement_day_dispatch_for_mode`` for the legacy
fallback used at those sites.
"""
from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

from src.engine.discovery_mode import DiscoveryMode

if TYPE_CHECKING:
    from src.engine.evaluator import MarketCandidate
    from src.strategy.market_phase import MarketPhase


_DISPATCH_FLAG_ENV = "ZEUS_MARKET_PHASE_DISPATCH"


def market_phase_dispatch_enabled() -> bool:
    """Return True iff ``ZEUS_MARKET_PHASE_DISPATCH`` is set to a truthy
    value. Default OFF; T6 byte-equal invariant requires that when this
    is OFF every dispatch site behaves byte-equal to pre-P3.
    """
    return os.environ.get(_DISPATCH_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def is_settlement_day_dispatch(candidate: "MarketCandidate") -> bool:
    """Single dispatch question: at this candidate, should the daemon
    take the SETTLEMENT_DAY-class strategy path?

    Flag OFF (default, byte-equal to pre-P3): legacy
    ``candidate.discovery_mode == DAY0_CAPTURE.value``.

    Flag ON: ``candidate.market_phase == MarketPhase.SETTLEMENT_DAY``.
    Falls back to legacy logic if ``candidate.market_phase`` is None
    (untagged / off-cycle / test fixture) — fail-soft so the migration
    never trades silent misclassification for a hard fault.
    """
    if not market_phase_dispatch_enabled():
        return _is_day0_capture_legacy(candidate)

    market_phase = getattr(candidate, "market_phase", None)
    if market_phase is None:
        # Untagged candidate — defer to legacy. This is the path taken
        # by test fixtures and any off-cycle direct construction; the
        # production cycle_runtime always tags at construction.
        return _is_day0_capture_legacy(candidate)

    # str-Enum equality: ``MarketPhase.SETTLEMENT_DAY == "settlement_day"``.
    return market_phase == "settlement_day"


def settlement_day_dispatch_for_mode(mode: DiscoveryMode) -> bool:
    """Mode-axis fallback for cycle-level callers (e.g.,
    ``cycle_runner._classify_edge_source``) that don't have a candidate
    in scope. Always uses the legacy ``DiscoveryMode`` axis regardless
    of the flag — these sites are explicitly NOT migrated by P3 because
    cycle-level decisions happen before per-candidate phase is known.

    Kept here for symmetry so future cleanup passes can find every
    "is this DAY0_CAPTURE-class?" site through one grep.
    """
    return mode == DiscoveryMode.DAY0_CAPTURE


def _is_day0_capture_legacy(candidate: "MarketCandidate") -> bool:
    return getattr(candidate, "discovery_mode", "") == DiscoveryMode.DAY0_CAPTURE.value
