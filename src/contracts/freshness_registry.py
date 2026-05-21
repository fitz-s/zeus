# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §6.2 (T3 FreshnessRegistry — code-only, no DB)

"""
FreshnessRegistry — centralized per-source data freshness gate.

Problem (§6.1): ~10 sites across src/ perform ad-hoc freshness comparisons
(direct age_seconds/age_hours threshold checks) each with hardcoded or locally-scoped
constants.  This scatters freshness policy and makes system-wide freshness tuning
impossible without grep-and-patch.

Solution: one registry, one `evaluate(source_id, age_seconds) -> FreshnessLevel`
call per gate.  Production pass replaces all 10 ad-hoc callsites.

NOTE (SCAFFOLD): `evaluate()` body is NotImplementedError.  The threshold table
and enum are concrete — they carry the existing callsite literals so the production
pass can migrate them one-for-one.

Per-source threshold table keys (source_id strings):
  collateral_snapshot          — collateral_ledger.py gate
  day0_executable_observation  — evaluator.py gate (threshold in hours, converted to seconds)
  oracle_artifact              — oracle_estimator.py gate
  riskguard_last_check         — riskguard.py gate (hardcoded 300 s)
  heartbeat_restart_seed       — heartbeat_supervisor.py read_latest_restart_seed gate
  heartbeat_status             — heartbeat_supervisor.py ExternalHeartbeatSupervisor gate
  strategy_health              — db.py query_strategy_health_snapshot gate
  venue_clearance              — venue_command_repo.py no-exposure clearance gate
  executable_snapshot          — polymarket_v2_adapter.py snapshot freshness-window gate

Sources whose threshold is dynamic (heartbeat_status, executable_snapshot) carry
  sentinel value DYNAMIC_THRESHOLD in the table; callers must pass the per-call
  threshold via the `override_threshold_seconds` parameter to evaluate().
"""

from __future__ import annotations

from enum import auto
from typing import Optional

from enum import StrEnum


# ---------------------------------------------------------------------------
# FreshnessLevel enum
# ---------------------------------------------------------------------------

class FreshnessLevel(StrEnum):
    """Ordered freshness verdict returned by FreshnessRegistry.evaluate()."""
    FRESH = auto()      # age < DEGRADED threshold
    DEGRADED = auto()   # age in [DEGRADED, STALE) — proceed with warning
    STALE = auto()      # age in [STALE, EXPIRED) — degraded-mode allowed
    EXPIRED = auto()    # age >= EXPIRED threshold — reject / fail-closed


# ---------------------------------------------------------------------------
# Sentinel for dynamic thresholds
# ---------------------------------------------------------------------------

#: Sentinel stored in the threshold table for sources whose STALE threshold is
#: determined at call time (e.g. heartbeat_status uses env-configurable max_age;
#: executable_snapshot uses per-snapshot freshness_window_seconds).
#: When a source maps to DYNAMIC_THRESHOLD, callers MUST supply
#: ``override_threshold_seconds``.
DYNAMIC_THRESHOLD: float = -1.0


# ---------------------------------------------------------------------------
# Per-source threshold table
# ---------------------------------------------------------------------------

# Tier ratios: DEGRADED = 0.75×, STALE = 1.0× (the original gate boundary),
# EXPIRED = 2.0× the original MAX_AGE constant.  This preserves exact backward-
# compat at the STALE tier so that migrated callsites produce identical gate
# behaviour on green paths.

def _thresholds(stale_seconds: float) -> dict[str, float]:
    """Return the three-tier threshold dict for a given original gate boundary."""
    return {
        "degraded_seconds": stale_seconds * 0.75,
        "stale_seconds":    stale_seconds,
        "expired_seconds":  stale_seconds * 2.0,
    }


# ---------------------------------------------------------------------------
# Import constants that were scattered across callsites so thresholds are
# a single source of truth (production pass will dedup these imports).
# Each entry preserves the original literal value so migration is zero-change
# at the gate boundary.
# ---------------------------------------------------------------------------

_COLLATERAL_MAX_AGE_SECONDS: float = 30.0 + 150.0  # REFRESH_CADENCE + JITTER_BUDGET
_DAY0_OBS_MAX_AGE_SECONDS: float = 1.0 * 3600.0    # DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS * 3600
_ORACLE_ARTIFACT_MAX_AGE_SECONDS: float = 7 * 24.0 * 3600.0  # STALE_AGE_HOURS * 3600
_RISKGUARD_MAX_AGE_SECONDS: float = 300.0           # hardcoded literal in riskguard.py
_HEARTBEAT_RESTART_SEED_MAX_AGE_SECONDS: float = 30.0  # DEFAULT_HEARTBEAT_RESTART_SEED_MAX_AGE_SECONDS
_STRATEGY_HEALTH_MAX_AGE_SECONDS: float = 300.0     # default max_age_seconds in db.py
_VENUE_CLEARANCE_MAX_AGE_SECONDS: float = 60.0      # hardcoded literal in venue_command_repo.py


#: Per-source threshold registry.  Key = source_id string; value = threshold dict
#: with keys ``degraded_seconds``, ``stale_seconds``, ``expired_seconds``.
#: Sources with DYNAMIC_THRESHOLD require ``override_threshold_seconds`` at evaluate() time.
SOURCE_THRESHOLDS: dict[str, dict[str, float]] = {
    "collateral_snapshot":         _thresholds(_COLLATERAL_MAX_AGE_SECONDS),
    "day0_executable_observation": _thresholds(_DAY0_OBS_MAX_AGE_SECONDS),
    "oracle_artifact":             _thresholds(_ORACLE_ARTIFACT_MAX_AGE_SECONDS),
    "riskguard_last_check":        _thresholds(_RISKGUARD_MAX_AGE_SECONDS),
    "heartbeat_restart_seed":      _thresholds(_HEARTBEAT_RESTART_SEED_MAX_AGE_SECONDS),
    # Dynamic: threshold determined per-call from env or snapshot attribute
    "heartbeat_status":            {"degraded_seconds": DYNAMIC_THRESHOLD, "stale_seconds": DYNAMIC_THRESHOLD, "expired_seconds": DYNAMIC_THRESHOLD},
    "strategy_health":             _thresholds(_STRATEGY_HEALTH_MAX_AGE_SECONDS),
    "venue_clearance":             _thresholds(_VENUE_CLEARANCE_MAX_AGE_SECONDS),
    # Dynamic: threshold determined per-call from snapshot.freshness_window_seconds
    "executable_snapshot":         {"degraded_seconds": DYNAMIC_THRESHOLD, "stale_seconds": DYNAMIC_THRESHOLD, "expired_seconds": DYNAMIC_THRESHOLD},
}


# ---------------------------------------------------------------------------
# FreshnessRegistry
# ---------------------------------------------------------------------------

class FreshnessRegistry:
    """Centralised freshness evaluation for all data sources in Zeus.

    Usage (production pass)::

        from src.contracts.freshness_registry import FreshnessRegistry, FreshnessLevel

        registry = FreshnessRegistry()
        level = registry.evaluate("collateral_snapshot", age_seconds)
        if level >= FreshnessLevel.STALE:
            raise StaleDataError(...)

    The registry is stateless and cheap to construct; callers may create it inline
    or import a module-level singleton (production pass decision).
    """

    def __init__(
        self,
        thresholds: Optional[dict[str, dict[str, float]]] = None,
    ) -> None:
        """
        Args:
            thresholds: Optional override of the default SOURCE_THRESHOLDS table.
                Used in tests and for runtime tuning.  If None, SOURCE_THRESHOLDS
                is used.
        """
        self._thresholds: dict[str, dict[str, float]] = (
            thresholds if thresholds is not None else SOURCE_THRESHOLDS
        )

    def evaluate(
        self,
        source_id: str,
        age_seconds: float,
        *,
        override_threshold_seconds: Optional[float] = None,
    ) -> FreshnessLevel:
        """Return the FreshnessLevel for a data source given its age.

        Args:
            source_id: Key into SOURCE_THRESHOLDS (e.g. "collateral_snapshot").
            age_seconds: Age of the data in seconds (non-negative; negative values
                are clock-skew artifacts and should be handled by the caller before
                calling evaluate).
            override_threshold_seconds: For DYNAMIC_THRESHOLD sources, the caller
                must supply the effective STALE boundary.  The DEGRADED and EXPIRED
                tiers are derived as 0.75× and 2.0× of this value respectively.
                Ignored for sources with a static threshold in the table.

        Returns:
            FreshnessLevel indicating FRESH / DEGRADED / STALE / EXPIRED.

        Raises:
            NotImplementedError: SCAFFOLD placeholder — production pass fills body.
            KeyError: If source_id is not registered in the threshold table.
            ValueError: If source has DYNAMIC_THRESHOLD and override_threshold_seconds
                is not supplied.
        """
        raise NotImplementedError(
            "FreshnessRegistry.evaluate() is a SCAFFOLD skeleton. "
            "Production pass (T3 migration) fills this body. "
            f"Called with source_id={source_id!r}, age_seconds={age_seconds}"
        )

    # ------------------------------------------------------------------
    # Observability (production pass wires these to _cnt_inc counters)
    # ------------------------------------------------------------------

    def _emit_counter(self, source_id: str, level: FreshnessLevel) -> None:  # noqa: ARG002
        """Emit observability counter ``freshness_<source>_<level>_total``.

        Production pass implementation calls _cnt_inc from src/observability/.
        SCAFFOLD: no-op.
        """
        # TODO(T3-production): wire to _cnt_inc(f"freshness_{source_id}_{level}_total")
        pass
