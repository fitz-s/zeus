# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Production tests for FreshnessRegistry.evaluate() — tier boundaries, DYNAMIC, counters
# Reuse: Run after any changes to freshness_registry.py; all tests must pass GREEN
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §6.2 (T3 FreshnessRegistry — production tests)
"""Production tests for FreshnessRegistry.evaluate().

Covers:
  - Tier boundary semantics (STRICT >: at boundary returns DEGRADED not STALE)
  - DYNAMIC_THRESHOLD sources: ValueError when override not supplied
  - DYNAMIC_THRESHOLD sources: correct tier derivation from override
  - Static source override: override_threshold_seconds respected
  - Unknown source_id: KeyError
  - Counter emission: _emit_counter called with correct name
"""

from __future__ import annotations

import pytest

from src.contracts.freshness_registry import (
    DYNAMIC_THRESHOLD,
    FreshnessLevel,
    FreshnessRegistry,
    SOURCE_THRESHOLDS,
    registry,
)


# ---------------------------------------------------------------------------
# Tier boundary tests — static source ("riskguard_last_check", stale=300s)
# ---------------------------------------------------------------------------

class TestEvaluateBoundaries:
    """Boundary semantics: STRICT age > stale_seconds (NOT >=)."""

    def setup_method(self) -> None:
        self.reg = FreshnessRegistry()
        self.stale_s = SOURCE_THRESHOLDS["riskguard_last_check"]["stale_seconds"]   # 300.0
        self.degraded_s = SOURCE_THRESHOLDS["riskguard_last_check"]["degraded_seconds"]  # 225.0
        self.expired_s = SOURCE_THRESHOLDS["riskguard_last_check"]["expired_seconds"]  # 600.0

    def test_fresh_well_below_degraded(self) -> None:
        assert self.reg.evaluate("riskguard_last_check", 0.0) == FreshnessLevel.FRESH

    def test_fresh_just_below_degraded_boundary(self) -> None:
        assert self.reg.evaluate("riskguard_last_check", self.degraded_s) == FreshnessLevel.FRESH

    def test_degraded_just_above_degraded_boundary(self) -> None:
        assert self.reg.evaluate("riskguard_last_check", self.degraded_s + 0.001) == FreshnessLevel.DEGRADED

    def test_degraded_at_stale_boundary(self) -> None:
        # At EXACTLY stale_seconds, should return DEGRADED (strict > means boundary is still safe)
        assert self.reg.evaluate("riskguard_last_check", self.stale_s) == FreshnessLevel.DEGRADED

    def test_stale_just_above_stale_boundary(self) -> None:
        assert self.reg.evaluate("riskguard_last_check", self.stale_s + 0.001) == FreshnessLevel.STALE

    def test_stale_at_expired_boundary(self) -> None:
        # At EXACTLY expired_seconds, should return STALE (strict >)
        assert self.reg.evaluate("riskguard_last_check", self.expired_s) == FreshnessLevel.STALE

    def test_expired_just_above_expired_boundary(self) -> None:
        assert self.reg.evaluate("riskguard_last_check", self.expired_s + 0.001) == FreshnessLevel.EXPIRED

    def test_stale_comparison_semantics(self) -> None:
        """evaluate() >= STALE is False at boundary — matches old age > MAX behavior."""
        result = self.reg.evaluate("riskguard_last_check", self.stale_s)
        # At exact stale boundary: returns DEGRADED, so >= STALE is False
        assert result == FreshnessLevel.DEGRADED
        assert not (result >= FreshnessLevel.STALE)

    def test_enum_severity_ordering(self) -> None:
        """EXPIRED > STALE > DEGRADED > FRESH in numeric rank (IntEnum, NOT lexicographic)."""
        assert FreshnessLevel.EXPIRED > FreshnessLevel.STALE
        assert FreshnessLevel.STALE > FreshnessLevel.DEGRADED
        assert FreshnessLevel.DEGRADED > FreshnessLevel.FRESH
        # Critical: EXPIRED >= STALE must be True for fail-closed gates to work
        assert FreshnessLevel.EXPIRED >= FreshnessLevel.STALE


# ---------------------------------------------------------------------------
# DYNAMIC_THRESHOLD sources
# ---------------------------------------------------------------------------

class TestDynamicThreshold:
    def setup_method(self) -> None:
        self.reg = FreshnessRegistry()

    def test_dynamic_source_raises_without_override(self) -> None:
        with pytest.raises(ValueError, match="DYNAMIC_THRESHOLD"):
            self.reg.evaluate("heartbeat_status", 5.0)

    def test_dynamic_source_executable_snapshot_raises_without_override(self) -> None:
        with pytest.raises(ValueError, match="DYNAMIC_THRESHOLD"):
            self.reg.evaluate("executable_snapshot", 10.0)

    def test_dynamic_source_fresh_with_override(self) -> None:
        # override=100s, age=50s → FRESH (50 < 75 degraded)
        result = self.reg.evaluate("heartbeat_status", 50.0, override_threshold_seconds=100.0)
        assert result == FreshnessLevel.FRESH

    def test_dynamic_source_degraded_with_override(self) -> None:
        # override=100s → degraded=75s, stale=100s; age=80s → DEGRADED
        result = self.reg.evaluate("heartbeat_status", 80.0, override_threshold_seconds=100.0)
        assert result == FreshnessLevel.DEGRADED

    def test_dynamic_source_stale_with_override(self) -> None:
        # override=100s, age=101s → STALE
        result = self.reg.evaluate("heartbeat_status", 101.0, override_threshold_seconds=100.0)
        assert result == FreshnessLevel.STALE

    def test_dynamic_source_expired_with_override(self) -> None:
        # override=100s → expired=200s; age=201s → EXPIRED
        result = self.reg.evaluate("heartbeat_status", 201.0, override_threshold_seconds=100.0)
        assert result == FreshnessLevel.EXPIRED

    def test_dynamic_source_boundary_at_stale(self) -> None:
        # override=100s, age=100s exactly → DEGRADED (strict >)
        result = self.reg.evaluate("executable_snapshot", 100.0, override_threshold_seconds=100.0)
        assert result == FreshnessLevel.DEGRADED
        assert not (result >= FreshnessLevel.STALE)


# ---------------------------------------------------------------------------
# Static source with override (heartbeat_restart_seed — accepts per-call param)
# ---------------------------------------------------------------------------

class TestStaticSourceOverride:
    def setup_method(self) -> None:
        self.reg = FreshnessRegistry()

    def test_override_respected_for_static_source(self) -> None:
        # Default stale=30s; pass override=60s → degraded=45s; age=46s → DEGRADED (not STALE)
        # Without override, age=46s > stale=30s → STALE.  Override changes the boundary.
        result = self.reg.evaluate("heartbeat_restart_seed", 46.0, override_threshold_seconds=60.0)
        assert result == FreshnessLevel.DEGRADED

    def test_no_override_uses_table_value(self) -> None:
        # Default stale=30s; age=31s → STALE
        result = self.reg.evaluate("heartbeat_restart_seed", 31.0)
        assert result == FreshnessLevel.STALE


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestErrorCases:
    def setup_method(self) -> None:
        self.reg = FreshnessRegistry()

    def test_unknown_source_id_raises_keyerror(self) -> None:
        with pytest.raises(KeyError, match="unknown_source"):
            self.reg.evaluate("unknown_source", 10.0)


# ---------------------------------------------------------------------------
# Counter emission
# ---------------------------------------------------------------------------

class TestCounterEmission:
    def test_emit_counter_called_with_correct_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        emitted: list[str] = []
        monkeypatch.setattr(
            "src.contracts.freshness_registry._cnt_inc",
            lambda name: emitted.append(name),
        )
        reg = FreshnessRegistry()
        reg.evaluate("riskguard_last_check", 0.0)
        assert len(emitted) == 1
        # Counter uses .name.lower() so it stays "fresh" regardless of enum backing type.
        assert emitted[0] == "freshness_riskguard_last_check_fresh_total"

    def test_emit_counter_stale_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        emitted: list[str] = []
        monkeypatch.setattr(
            "src.contracts.freshness_registry._cnt_inc",
            lambda name: emitted.append(name),
        )
        stale_age = SOURCE_THRESHOLDS["riskguard_last_check"]["stale_seconds"] + 1.0
        reg = FreshnessRegistry()
        reg.evaluate("riskguard_last_check", stale_age)
        assert emitted[0] == "freshness_riskguard_last_check_stale_total"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

def test_module_singleton_is_freshnessregistry_instance() -> None:
    assert isinstance(registry, FreshnessRegistry)

def test_module_singleton_evaluates_correctly() -> None:
    result = registry.evaluate("collateral_snapshot", 0.0)
    assert result == FreshnessLevel.FRESH
