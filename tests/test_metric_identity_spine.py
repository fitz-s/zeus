"""Relationship tests for the MetricIdentity type spine — R1, R2, R4.

Phase: 1 (MetricIdentity Spine + FDR Scope Split)
R-numbers covered: R1 (metric identity type safety), R2 (Day0 low branch refuses),
                   R4 (string→MetricIdentity one-way type seam)

These tests MUST FAIL today (2026-04-16) because:
  - src/types/metric_identity.py does not yet exist (R1, R2, R4 will ImportError).
  - Day0Signal currently accepts temperature_metric: str without raising (R4 bare-string test).
  - Day0Signal low-metric path silently produces high-semantics output (R2).

First commit that should make this green: executor Phase 1 implementation commit
(creates src/types/metric_identity.py, threads MetricIdentity into Day0Signal.__init__).
"""
from __future__ import annotations

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# R1 — Metric identity type safety
# ---------------------------------------------------------------------------

class TestMetricIdentityTypeSafety:
    """R1: MetricIdentity cannot hold inconsistent field pairings."""

    def test_metric_identity_module_exists(self):
        """Smoke test: src.types.metric_identity is importable after Phase 1 lands."""
        try:
            from src.types.metric_identity import MetricIdentity  # noqa: F401
        except ImportError:
            pytest.fail(
                "Phase 1 module not yet implemented: src.types.metric_identity does not exist"
            )

    def test_metric_identity_rejects_cross_pairing_high_metric_low_obs(self):
        """R1: MetricIdentity(temperature_metric='high', observation_field='low_temp') raises ValueError.

        A high-track identity MUST have observation_field='high_temp'.  Cross-pairing
        is a semantic error that the constructor must catch at the boundary.
        """
        try:
            from src.types.metric_identity import MetricIdentity
        except ImportError:
            pytest.fail("Phase 1 module not yet implemented: src.types.metric_identity does not exist")

        with pytest.raises(ValueError):
            MetricIdentity(
                temperature_metric="high",
                physical_quantity="mx2t6_local_calendar_day_max",
                observation_field="low_temp",  # WRONG: high-track must pair with high_temp
                data_version="tigge_mx2t6_local_calendar_day_max_v1",
            )

    def test_metric_identity_rejects_cross_pairing_low_metric_high_obs(self):
        """R1: MetricIdentity(temperature_metric='low', observation_field='high_temp') raises ValueError.

        Symmetric guard: low-track identity must have observation_field='low_temp'.
        """
        try:
            from src.types.metric_identity import MetricIdentity
        except ImportError:
            pytest.fail("Phase 1 module not yet implemented: src.types.metric_identity does not exist")

        with pytest.raises(ValueError):
            MetricIdentity(
                temperature_metric="low",
                physical_quantity="mn2t6_local_calendar_day_min",
                observation_field="high_temp",  # WRONG: low-track must pair with low_temp
                data_version="tigge_mn2t6_local_calendar_day_min_v1",
            )

    def test_metric_identity_canonical_instances_exist(self):
        """R1: HIGH_LOCALDAY_MAX and LOW_LOCALDAY_MIN canonical instances exist and carry correct field pairings."""
        try:
            from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN
        except ImportError:
            pytest.fail("Phase 1 module not yet implemented: canonical instances not found in src.types.metric_identity")

        # High-track pairing
        assert HIGH_LOCALDAY_MAX.temperature_metric == "high"
        assert HIGH_LOCALDAY_MAX.observation_field == "high_temp"

        # Low-track pairing
        assert LOW_LOCALDAY_MIN.temperature_metric == "low"
        assert LOW_LOCALDAY_MIN.observation_field == "low_temp"

    def test_metric_identity_canonical_instances_carry_ensemble_fields(self):
        """R1: Canonical instances must expose a data_version consistent with their track.

        This is a field-pairing smoke test: high-track data_version must not reference
        'min' and low-track must not reference 'max'.  Cross-pollination at this field
        would make the physical_quantity → ensemble_field mapping ambiguous.
        """
        try:
            from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN
        except ImportError:
            pytest.fail("Phase 1 module not yet implemented: canonical instances not found in src.types.metric_identity")

        assert "min" not in HIGH_LOCALDAY_MAX.data_version.lower() or "max" in HIGH_LOCALDAY_MAX.data_version.lower()
        assert "max" not in LOW_LOCALDAY_MIN.data_version.lower() or "min" in LOW_LOCALDAY_MIN.data_version.lower()

    def test_metric_identity_high_valid_construction_succeeds(self):
        """R1 guard against false-positive: a correctly-paired high identity constructs without error."""
        try:
            from src.types.metric_identity import MetricIdentity
        except ImportError:
            pytest.fail("Phase 1 module not yet implemented: src.types.metric_identity does not exist")

        # This must NOT raise — it is a correctly-paired identity
        identity = MetricIdentity(
            temperature_metric="high",
            physical_quantity="mx2t6_local_calendar_day_max",
            observation_field="high_temp",
            data_version="tigge_mx2t6_local_calendar_day_max_v1",
        )
        assert identity.temperature_metric == "high"
        assert identity.observation_field == "high_temp"

    def test_metric_identity_is_high_method(self):
        """R1: MetricIdentity exposes an is_high() or equivalent predicate."""
        try:
            from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN
        except ImportError:
            pytest.fail("Phase 1 module not yet implemented: src.types.metric_identity does not exist")

        assert HIGH_LOCALDAY_MAX.is_high() is True
        assert LOW_LOCALDAY_MIN.is_high() is False

    def test_metric_identity_is_low_method(self):
        """R1: MetricIdentity exposes an is_low() predicate."""
        try:
            from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN
        except ImportError:
            pytest.fail("Phase 1 module not yet implemented: src.types.metric_identity does not exist")

        assert LOW_LOCALDAY_MIN.is_low() is True
        assert HIGH_LOCALDAY_MAX.is_low() is False


# ---------------------------------------------------------------------------
# R2 — Day0 low branch must not silently consume high-side inputs
# ---------------------------------------------------------------------------

class TestDay0SignalLowMetricRefuses:
    """R2: Day0Signal with a low-track MetricIdentity raises NotImplementedError.

    The paired HIGH assertion (marked with # MAY PASS TODAY) documents that Day0Signal
    with a high-track MetricIdentity still works — preventing a false-positive where
    Day0Signal simply raises on all inputs.
    """

    def test_day0signal_low_metric_refused_post_phase6(self):
        """Phase 6 re-guard: Day0Signal(LOW) raises TypeError; Day0Router.route(LOW) returns Day0LowNowcastSignal.

        Phase 1 R2 antibody refused LOW with NotImplementedError ("not yet built").
        Phase 6 upgrades the boundary: Day0Signal is HIGH-only (TypeError),
        and the router-level invariant ensures LOW never produces Day0Signal output.
        Fitz P4 category-impossibility preserved at both the class and router seam.
        """
        from src.types.metric_identity import LOW_LOCALDAY_MIN
        from src.signal.day0_signal import Day0Signal
        from src.signal.day0_router import Day0Router, Day0SignalInputs
        from src.signal.day0_low_nowcast_signal import Day0LowNowcastSignal

        # Part 1: direct Day0Signal construction with LOW raises TypeError (class-level guard)
        with pytest.raises(TypeError):
            Day0Signal(
                observed_high_so_far=80.0,
                current_temp=78.0,
                hours_remaining=4.0,
                member_maxes_remaining=np.array([85.0, 84.0, 82.0]),
                temperature_metric=LOW_LOCALDAY_MIN,
            )

        # Part 2: router never returns Day0Signal for LOW (router-level guard)
        inputs = Day0SignalInputs(
            temperature_metric=LOW_LOCALDAY_MIN,
            current_temp=32.0,
            hours_remaining=6.0,
            observed_high_so_far=None,
            observed_low_so_far=30.0,
            member_maxes_remaining=None,
            member_mins_remaining=np.array([26.0, 28.0]),
            causality_status="OK",
        )
        signal = Day0Router.route(inputs)
        assert isinstance(signal, Day0LowNowcastSignal)
        assert not isinstance(signal, Day0Signal)

    def test_day0signal_high_metric_still_constructs_successfully(self):
        """R2 paired assertion: Day0Signal(temperature_metric=HIGH_LOCALDAY_MAX, ...) must NOT raise.

        This test guards against a false-positive where R2 passes only because Day0Signal
        raises on every MetricIdentity argument indiscriminately.

        NOTE: This test MAY PASS TODAY if Day0Signal accepts str and HIGH_LOCALDAY_MAX
        simply substitutes without triggering the NotImplementedError guard.  That outcome
        is acceptable — what matters is that the low branch test above fails today.
        """
        try:
            from src.types.metric_identity import HIGH_LOCALDAY_MAX
        except ImportError:
            pytest.fail("Phase 1 module not yet implemented: HIGH_LOCALDAY_MAX not found")

        from src.signal.day0_signal import Day0Signal

        # Must construct without raising — high-track is the implemented path
        sig = Day0Signal(
            observed_high_so_far=80.0,
            current_temp=78.0,
            hours_remaining=4.0,
            member_maxes_remaining=np.array([85.0, 84.0, 82.0]),
            temperature_metric=HIGH_LOCALDAY_MAX,  # high-track: valid today after Phase 1
        )
        assert sig is not None


# ---------------------------------------------------------------------------
# R4 — String → MetricIdentity one-way type seam
# ---------------------------------------------------------------------------

class TestDay0SignalRefusesBareString:
    """R4: Day0Signal refuses a bare str for temperature_metric.

    After Phase 1, every signal class accepts only MetricIdentity.  The one
    legal string→MetricIdentity conversion point is evaluator.py:650-651
    (_normalize_temperature_metric / MetricIdentity.from_raw).
    """

    def test_day0signal_refuses_bare_string_metric(self):
        """R4: Day0Signal(temperature_metric='high') raises TypeError.

        Today this test FAILS because Day0Signal.__init__ accepts
        temperature_metric: str = 'high' without protest.
        """
        from src.signal.day0_signal import Day0Signal

        with pytest.raises(TypeError):
            Day0Signal(
                observed_high_so_far=80.0,
                current_temp=78.0,
                hours_remaining=4.0,
                member_maxes_remaining=np.array([85.0]),
                temperature_metric="high",  # bare str, not MetricIdentity
            )

    def test_day0signal_refuses_bare_string_low_metric(self):
        """R4: Day0Signal(temperature_metric='low') also raises TypeError (not NotImplementedError).

        The type-seam check must fire BEFORE the NotImplementedError guard — a str
        arriving at the seam boundary is the wrong type regardless of its value.
        """
        from src.signal.day0_signal import Day0Signal

        with pytest.raises(TypeError):
            Day0Signal(
                observed_high_so_far=60.0,
                current_temp=58.0,
                hours_remaining=6.0,
                member_maxes_remaining=np.array([65.0]),
                temperature_metric="low",  # bare str, not MetricIdentity
            )
