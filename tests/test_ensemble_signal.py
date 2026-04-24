# Created: 2026-03-30
# Last reused/audited: 2026-04-23
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a midstream guardian panel)
"""Tests for EnsembleSignal.

Covers:
1. Happy path: standard 51-member ensemble
2. Edge cases from CLAUDE.md §4.2:
   - All 51 members in same bin → P_raw ≈ 1.0
   - Bimodal detection (25 at 40°F + 26 at 60°F)
   - Boundary sensitivity near bin edge
3. Failure modes: < 51 members rejected, empty hours
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from src.config import City
from src.contracts.settlement_semantics import SettlementSemantics
from src.signal.ensemble_signal import EnsembleSignal, SIGMA_INSTRUMENT
from src.types import Bin


# Test city fixture
NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="US-Northeast",
    settlement_unit="F", wu_station="KLGA",
)

# Standard 11-bin structure for NYC winter
NYC_BINS = [
    Bin(low=None, high=32, label="32°F or below", unit="F"),
    Bin(low=33, high=34, label="33-34°F", unit="F"),
    Bin(low=35, high=36, label="35-36°F", unit="F"),
    Bin(low=37, high=38, label="37-38°F", unit="F"),
    Bin(low=39, high=40, label="39-40°F", unit="F"),
    Bin(low=41, high=42, label="41-42°F", unit="F"),
    Bin(low=43, high=44, label="43-44°F", unit="F"),
    Bin(low=45, high=46, label="45-46°F", unit="F"),
    Bin(low=47, high=48, label="47-48°F", unit="F"),
    Bin(low=49, high=50, label="49-50°F", unit="F"),
    Bin(low=51, high=None, label="51°F or higher", unit="F"),
]

# Minimal valid settlement semantics for NYC (WU integer °F rounding)
NYC_SEMANTICS = SettlementSemantics.default_wu_fahrenheit("KLGA")
TARGET_DATE = date(2026, 1, 15)


def _make_constant_members(value: float, n_members: int = 51, n_hours: int = 24):
    """Create members_hourly where every member peaks at `value`."""
    # Members all have a constant temperature across hours
    return np.full((n_members, n_hours), value, dtype=np.float64)


def _make_bimodal_members(v1: float, n1: int, v2: float, n2: int, n_hours: int = 24):
    """Create bimodal ensemble: n1 members at v1, n2 members at v2."""
    arr = np.zeros((n1 + n2, n_hours), dtype=np.float64)
    arr[:n1, :] = v1
    arr[n1:, :] = v2
    return arr


def _make_local_day_times(target_date: date, timezone_name: str, total_hours: int = 24, days_before: int = 0):
    tz = ZoneInfo(timezone_name)
    start_local = datetime.combine(target_date - timedelta(days=days_before), time.min, tzinfo=tz)
    return [
        (start_local + timedelta(hours=i)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for i in range(total_hours)
    ]


class TestEnsembleSignalInit:
    def test_rejects_missing_forecast_times(self):
        members = _make_constant_members(40.0)
        with pytest.raises(TypeError):
            EnsembleSignal(members, None, NYC, TARGET_DATE, NYC_SEMANTICS)  # type: ignore[arg-type]

    def test_rejects_less_than_51_members(self):
        """CLAUDE.md: ENS response < 51 members → reject entirely."""
        members = np.zeros((30, 24), dtype=np.float64)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        with pytest.raises(ValueError, match="Expected ≥51"):
            EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)

    def test_accepts_exactly_51_members(self):
        members = _make_constant_members(40.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        assert len(ens.member_maxes) == 51

    def test_member_maxes_correct(self):
        """Each member's max across the timezone-selected hours should be extracted."""
        target_date = TARGET_DATE
        members = np.random.default_rng(42).uniform(30, 50, (51, 120))
        times = _make_local_day_times(target_date, NYC.timezone, total_hours=120, days_before=2)
        ens = EnsembleSignal(members, times, NYC, target_date, NYC_SEMANTICS)
        tz = ZoneInfo(NYC.timezone)
        tz_hours = EnsembleSignal._select_hours_for_date(target_date, tz, times=times)
        expected = members[:, tz_hours].max(axis=1)
        np.testing.assert_array_almost_equal(ens.member_maxes, expected)

    def test_real_times_override_decision_time_approximation(self):
        target_date = TARGET_DATE
        members = np.zeros((51, 72), dtype=np.float64)
        times = _make_local_day_times(target_date, NYC.timezone, total_hours=72, days_before=1)
        tz = ZoneInfo(NYC.timezone)
        target_idxs = EnsembleSignal._select_hours_for_date(target_date, tz, times=times)
        members[:, target_idxs] = 55.0
        ens = EnsembleSignal(
            members,
            times,
            NYC,
            target_date,
            NYC_SEMANTICS,
            decision_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert np.all(ens.member_maxes == 55.0)

    def test_raises_when_target_date_is_absent_from_real_times(self):
        members = _make_constant_members(40.0, n_hours=24)
        times = _make_local_day_times(TARGET_DATE - timedelta(days=1), NYC.timezone)
        with pytest.raises(ValueError, match="No forecast hours map to local target date"):
            EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)


class TestPRawVector:
    def test_all_members_same_bin(self):
        """All 51 members at 40°F → P_raw for 39-40 bin should be ≈1.0.

        With instrument noise σ=0.5°F, some probability leaks to adjacent bins,
        but the center bin should dominate.
        """
        np.random.seed(42)
        members = _make_constant_members(40.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=2000)

        assert p_raw.shape == (11,)
        assert pytest.approx(p_raw.sum(), abs=0.001) == 1.0

        # Bin index 4 is "39-40" → should have highest probability
        assert p_raw[4] > 0.5  # Most probability in the 39-40 bin
        # Adjacent bins get some noise spillover
        assert p_raw[4] + p_raw[3] + p_raw[5] > 0.95

    def test_sums_to_one(self):
        """P_raw vector must always sum to 1.0."""
        np.random.seed(42)
        members = np.random.default_rng(42).uniform(35, 50, (51, 24))
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=1000)
        assert pytest.approx(p_raw.sum(), abs=0.001) == 1.0

    def test_no_negative_probabilities(self):
        """All probabilities must be ≥ 0."""
        np.random.seed(42)
        members = _make_constant_members(50.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=1000)
        assert np.all(p_raw >= 0)

    def test_shoulder_low_dominates_for_cold(self):
        """All members at 25°F → "32°F or below" bin should dominate."""
        np.random.seed(42)
        members = _make_constant_members(25.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=1000)
        assert p_raw[0] > 0.95  # "32 or below" bin

    def test_shoulder_high_dominates_for_hot(self):
        """All members at 60°F → "51°F or higher" bin should dominate."""
        np.random.seed(42)
        members = _make_constant_members(60.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        p_raw = ens.p_raw_vector(NYC_BINS, n_mc=1000)
        assert p_raw[10] > 0.95  # "51 or higher" bin


class TestSpread:
    def test_zero_spread_for_constant(self):
        members = _make_constant_members(40.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        spread = ens.spread()
        from src.types.temperature import TemperatureDelta
        assert isinstance(spread, TemperatureDelta)
        assert spread.value == pytest.approx(0.0)
        assert spread.unit == "F"

    def test_positive_spread_for_varied(self):
        members = np.random.default_rng(42).uniform(30, 50, (51, 24))
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        assert ens.spread().value > 0


class TestBimodal:
    def test_unimodal_constant(self):
        """All members at same value → not bimodal."""
        members = _make_constant_members(40.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        assert ens.is_bimodal() is False

    def test_bimodal_two_clusters(self):
        """25 members at 40°F + 26 at 60°F → bimodal."""
        members = _make_bimodal_members(40.0, 25, 60.0, 26)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        assert ens.is_bimodal() is True

    def test_unimodal_tight_spread(self):
        """Members in narrow range → unimodal."""
        rng = np.random.default_rng(42)
        vals = rng.normal(40.0, 1.0, (51, 24))
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(vals, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        assert ens.is_bimodal() is False


class TestBoundarySensitivity:
    def test_all_members_at_boundary(self):
        """All members at 40.0°F, boundary at 40 → sensitivity should be 1.0."""
        members = _make_constant_members(40.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        assert ens.boundary_sensitivity(40.0) == pytest.approx(1.0)

    def test_no_members_near_boundary(self):
        """All members at 40°F, boundary at 60 → sensitivity should be 0."""
        members = _make_constant_members(40.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        assert ens.boundary_sensitivity(60.0) == pytest.approx(0.0)

    def test_partial_sensitivity(self):
        """Some members near boundary, some far."""
        # 10 members at 39.8 (within 0.5 of 40), 41 members at 50 (far)
        members = _make_bimodal_members(39.8, 10, 50.0, 41)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
        sensitivity = ens.boundary_sensitivity(40.0)
        assert sensitivity == pytest.approx(10.0 / 51.0, abs=0.01)


# =============================================================================
# B059 / B061 / B062 relationship tests — typed error taxonomy (SD-B).
#
# Invariant: ensemble_signal.py must distinguish between
#   (a) KNOWN degraded states (config missing, KDE numerical failure on
#       degenerate input) — handled silently with explicit fallback, AND
#   (b) UNKNOWN code/infrastructure failures (ImportError for stdlib,
#       AttributeError on wrong object, NameError) — which MUST propagate
#       so the operator sees a real fault instead of a ghost degraded signal.
# =============================================================================
class TestEnsembleBoundaryErrors:
    def test_b062_is_bimodal_falls_back_on_numerical_failure(self):
        """Degenerate input (all members identical) makes gaussian_kde fail
        with LinAlgError. is_bimodal must fall back to the gap heuristic,
        not raise."""
        # All 51 members at exactly the same value: covariance is singular →
        # gaussian_kde raises np.linalg.LinAlgError. The pre-check for
        # rng < sigma_instrument already returns False, so we need a case
        # with spread ≥ sigma but still degenerate. Use 2 distinct values
        # with zero spread in one of the modes is tricky; easier: 3 distinct
        # values far apart, forcing KDE but the bandwidth-selection path is
        # still well-defined. Instead we test the fallback directly by
        # monkeypatching gaussian_kde to raise np.linalg.LinAlgError.
        import numpy as np
        from unittest.mock import patch

        members = _make_bimodal_members(40.0, 25, 60.0, 26)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)

        with patch("src.signal.ensemble_signal.gaussian_kde",
                   side_effect=np.linalg.LinAlgError("singular covariance")):
            # Must NOT raise; must fall through to the gap heuristic.
            result = ens.is_bimodal()
        assert isinstance(result, bool)

    def test_b062_is_bimodal_propagates_unknown_failures(self):
        """If gaussian_kde fails with an unexpected exception (e.g. TypeError
        from a code bug), is_bimodal must NOT silently swallow it. A code
        defect masquerading as bimodal/unimodal signal is a death-trap."""
        from unittest.mock import patch

        members = _make_bimodal_members(40.0, 25, 60.0, 26)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)
        ens = EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)

        with patch("src.signal.ensemble_signal.gaussian_kde",
                   side_effect=TypeError("is_bimodal: wrong call signature")):
            with pytest.raises(TypeError):
                ens.is_bimodal()

    def test_b061_bias_correction_closes_connection_on_query_failure(self):
        """If season_from_date raises inside _apply_bias_correction's try
        block (AFTER get_world_connection but BEFORE conn.close()), the
        connection must still be closed — not leaked. The current code
        re-raises RuntimeError but leaves conn un-closed if the failure
        point is past get_world_connection."""
        from unittest.mock import MagicMock, patch
        import numpy as np

        fake_conn = MagicMock()
        # Simulate a failure inside season_from_date (the call right after
        # get_world_connection but before fetchone). The conn must still
        # be closed.
        with patch("src.signal.ensemble_signal.EnsembleSignal._apply_bias_correction") as _bypass:
            pass  # placeholder — we exercise the real path below

        with patch("src.state.db.get_world_connection", return_value=fake_conn), \
             patch("src.calibration.manager.season_from_date",
                   side_effect=ValueError("bad lat")):
            maxes = np.full(51, 40.0)
            with pytest.raises(RuntimeError, match="Bias correction database fault"):
                EnsembleSignal._apply_bias_correction(maxes, NYC, TARGET_DATE)
        # After re-raise, the connection must have been closed to avoid
        # leaking file handles on long-running daemons.
        assert fake_conn.close.called, (
            "B061: _apply_bias_correction must close the world-DB "
            "connection even when an inner call raises"
        )

    def test_b059_bias_correction_config_failure_is_not_silent(self):
        """Constructor's bias-correction block previously caught ALL
        exceptions with `except Exception: pass`. This means an
        ImportError from a missing dependency, or a RuntimeError raised by
        _apply_bias_correction itself (signaling DB fault), would be
        silently swallowed and bias_corrected would remain False — visibly
        identical to 'bias correction disabled by config'.

        Invariant: an UnknownError path (not the legitimate 'settings
        attribute missing' path) must propagate. We simulate by making
        settings.bias_correction_enabled True and having
        _apply_bias_correction raise RuntimeError; the EnsembleSignal
        constructor must NOT silently continue with bias_corrected=False."""
        from unittest.mock import patch
        import numpy as np

        members = _make_constant_members(40.0)
        times = _make_local_day_times(TARGET_DATE, NYC.timezone)

        # Force the bias-correction code path on, and have
        # _apply_bias_correction raise a RuntimeError (simulating the
        # "Bias correction database fault" path). The constructor must
        # surface that, not swallow it.
        with patch("src.config.settings") as fake_settings, \
             patch.object(
                 EnsembleSignal, "_apply_bias_correction",
                 side_effect=RuntimeError("Bias correction database fault"),
             ):
            fake_settings.bias_correction_enabled = True
            with pytest.raises(RuntimeError, match="Bias correction database fault"):
                EnsembleSignal(members, times, NYC, TARGET_DATE, NYC_SEMANTICS)
