# Created: 2026-05-03
# Last reused/audited: 2026-05-15
# Authority basis: docs/reference/zeus_oracle_density_discount_reference.md (v2 redesign)
"""Tests for DDD v2 — Two-Rail trigger + continuous linear curve.

Test coverage:
  - Rail 1 fires when cov<0.35 and window>0.5
  - Rail 1 does NOT fire when cov<0.35 but window<0.5
  - Rail 2 produces 0% discount at cov=floor
  - Rail 2 produces 9% cap at shortfall >= 0.45
  - Linear interpolation: shortfall=0.10 → discount ≈ 0.02
  - Small-sample amplification: discount × 1.25 when N < N_star
  - Missing config raises (fail-closed)
  - mismatch_rate floor: max(mismatch_rate, discount) preserved
  - Lagos: floor=0.45, cov=0 triggers Rail 1
  - Tokyo: floor=1.0, cov=6/7≈0.857 triggers Rail 2 mild discount
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

from src.oracle.data_density_discount import (
    DDDResult,
    ABSOLUTE_KILL_FLOOR,
    MAX_DISCOUNT,
    LINEAR_ALPHA,
    SMALL_SAMPLE_AMPLIFIER,
    _DEFAULT_FLOORS_PATH,
    _DEFAULT_NSTAR_PATH,
    evaluate_ddd,
    evaluate_ddd_from_files,
    get_city_floor,
    get_n_star,
    load_city_floors,
    load_nstar_config,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def make_floors_config(city: str, final_floor: float) -> dict:
    """Build a minimal floors config dict for testing."""
    return {
        "_metadata": {"schema_version": "2"},
        "policy_overrides": {},
        "per_city": {
            city: {
                "p05": final_floor,
                "recommended_floor_empirical": final_floor,
                "policy_override": None,
                "final_floor": final_floor,
                "floor_source": "empirical_p05",
                "train_FP_rate": 0.0,
                "n_zero_train": 0,
                "sigma_diagnostic": 0.0,
            }
        },
    }


def make_nstar_config(city: str, track: str, n_star: int | None, status: str = "OK") -> dict:
    """Build a minimal N_star config dict for testing."""
    key = f"{city}_{track}"
    return {
        "_metadata": {},
        "per_city_metric": {
            key: {
                "city": city,
                "metric": track,
                "total_N_dates": 840,
                "N_star": n_star,
                "status": status,
            }
        },
    }


def make_configs(city: str, track: str, floor: float, n_star: int | None = 110) -> tuple[dict, dict]:
    return make_floors_config(city, floor), make_nstar_config(city, track, n_star)


# ── Rail 1: absolute hard kill ────────────────────────────────────────────────

class TestRail1AbsoluteKill:
    def test_rail1_fires_when_cov_below_035_and_window_past_half(self):
        """Rail 1 HALT when cov=0.30 < 0.35 AND window=0.60 > 0.50."""
        floors, nstar = make_configs("Tokyo", "high", 1.0)
        result = evaluate_ddd(
            city="Tokyo", track="high",
            current_cov=0.30,
            window_elapsed=0.60,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "HALT"
        assert result.rail == 1
        assert result.discount == 0.0

    def test_rail1_does_not_fire_when_window_below_half(self):
        """Rail 1 does NOT fire when cov=0.30 but window=0.40 <= 0.50."""
        floors, nstar = make_configs("Tokyo", "high", 1.0)
        result = evaluate_ddd(
            city="Tokyo", track="high",
            current_cov=0.30,
            window_elapsed=0.40,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "DISCOUNT"
        assert result.rail == 2

    def test_rail1_does_not_fire_at_exactly_035_threshold(self):
        """Rail 1 does NOT fire at cov=0.35 exactly (< 0.35 required)."""
        floors, nstar = make_configs("Tokyo", "high", 1.0)
        result = evaluate_ddd(
            city="Tokyo", track="high",
            current_cov=ABSOLUTE_KILL_FLOOR,  # exactly 0.35
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "DISCOUNT"

    def test_rail1_fires_at_cov_zero(self):
        """Rail 1 fires at cov=0 regardless of city floor."""
        floors, nstar = make_configs("Lagos", "high", 0.45)
        result = evaluate_ddd(
            city="Lagos", track="high",
            current_cov=0.0,
            window_elapsed=0.70,
            N_platt_samples=828,
            mismatch_rate=0.02,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "HALT"
        assert result.rail == 1


# ── Rail 2: discount at/above floor ──────────────────────────────────────────

class TestRail2AtFloor:
    def test_zero_discount_when_cov_equals_floor(self):
        """Rail 2: shortfall=0 → discount=0% when cov=floor."""
        floors, nstar = make_configs("Tokyo", "high", 1.0)
        result = evaluate_ddd(
            city="Tokyo", track="high",
            current_cov=1.0,
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "DISCOUNT"
        assert result.rail == 2
        assert result.discount == 0.0

    def test_zero_discount_when_cov_above_floor(self):
        """Rail 2: shortfall=0 → discount=0% when cov > floor."""
        floors, nstar = make_configs("City", "high", 0.8)
        result = evaluate_ddd(
            city="City", track="high",
            current_cov=0.9,  # above floor
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.discount == 0.0


# ── Rail 2: 9% cap ────────────────────────────────────────────────────────────

class TestRail2Cap:
    def test_nine_percent_cap_at_shortfall_gte_045(self):
        """At shortfall=0.45 → 0.20*0.45=0.09 = cap exactly."""
        floors, nstar = make_configs("TestCity", "high", 1.0)
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.55,  # shortfall = 1.0 - 0.55 = 0.45
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "DISCOUNT"
        assert result.discount == pytest.approx(MAX_DISCOUNT, abs=1e-9)

    def test_nine_percent_cap_at_large_shortfall(self):
        """Shortfall=0.80 still capped at 9%."""
        floors, nstar = make_configs("TestCity", "high", 1.0)
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.20,  # shortfall = 0.80; but < 0.35 → Rail 1 at window>0.5
            window_elapsed=0.30,  # window < 0.5, so Rail 1 does NOT fire
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "DISCOUNT"
        assert result.discount == pytest.approx(MAX_DISCOUNT, abs=1e-9)


# ── Rail 2: linear interpolation ─────────────────────────────────────────────

class TestLinearInterpolation:
    def test_shortfall_010_gives_discount_002(self):
        """shortfall=0.10 → D = 0.20 × 0.10 = 0.02."""
        floors, nstar = make_configs("TestCity", "high", 1.0)
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.90,  # shortfall = 0.10
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        expected = LINEAR_ALPHA * 0.10  # 0.02
        assert result.discount == pytest.approx(expected, abs=1e-9)

    def test_shortfall_030_gives_discount_006(self):
        """shortfall=0.30 → D = 0.20 × 0.30 = 0.06."""
        floors, nstar = make_configs("TestCity", "high", 1.0)
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.70,  # shortfall = 0.30
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        expected = LINEAR_ALPHA * 0.30  # 0.06
        assert result.discount == pytest.approx(expected, abs=1e-9)


# ── Small-sample amplification ────────────────────────────────────────────────

class TestSmallSampleAmplification:
    def test_amplification_when_N_below_nstar(self):
        """When N < N_star, discount is multiplied by 1.25."""
        floors, nstar = make_configs("TestCity", "high", 1.0, n_star=200)
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.90,  # shortfall = 0.10, base discount = 0.02
            window_elapsed=0.80,
            N_platt_samples=100,  # < N_star=200
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        expected = min(MAX_DISCOUNT, LINEAR_ALPHA * 0.10 * SMALL_SAMPLE_AMPLIFIER)
        assert result.discount == pytest.approx(expected, abs=1e-9)
        assert result.diagnostic["small_sample_amp_applied"] is True

    def test_no_amplification_when_N_above_nstar(self):
        """When N >= N_star, no amplification."""
        floors, nstar = make_configs("TestCity", "high", 1.0, n_star=110)
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.90,  # shortfall = 0.10, base discount = 0.02
            window_elapsed=0.80,
            N_platt_samples=840,  # >= N_star=110
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        expected = LINEAR_ALPHA * 0.10  # 0.02 without amp
        assert result.discount == pytest.approx(expected, abs=1e-9)
        assert result.diagnostic["small_sample_amp_applied"] is False

    def test_amplification_when_nstar_is_none(self):
        """N_star=None (N_STAR_NOT_FOUND) → treated as small sample."""
        floors, nstar = make_configs("TestCity", "high", 1.0, n_star=None)
        # Override the nstar config to status=N_STAR_NOT_FOUND
        key = "TestCity_high"
        nstar["per_city_metric"][key]["status"] = "N_STAR_NOT_FOUND"
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.90,  # shortfall = 0.10
            window_elapsed=0.80,
            N_platt_samples=100,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        expected = min(MAX_DISCOUNT, LINEAR_ALPHA * 0.10 * SMALL_SAMPLE_AMPLIFIER)
        assert result.discount == pytest.approx(expected, abs=1e-9)
        assert result.diagnostic["small_sample_amp_applied"] is True


# ── Fail-closed: missing config ───────────────────────────────────────────────

class TestFailClosed:
    def test_missing_city_raises_keyerror(self):
        """City not in floors config raises KeyError (fail-CLOSED)."""
        floors = {"per_city": {}}
        nstar = {"per_city_metric": {}}
        with pytest.raises(KeyError, match="not found in DDD floors config"):
            evaluate_ddd(
                city="UnknownCity", track="high",
                current_cov=0.80, window_elapsed=0.50,
                N_platt_samples=100, mismatch_rate=0.0,
                city_floors_config=floors, n_star_config=nstar,
            )

    def test_no_train_data_city_raises_valueerror(self):
        """City with NO_TRAIN_DATA status raises ValueError (fail-CLOSED)."""
        floors = {
            "per_city": {"HongKong": {"status": "NO_TRAIN_DATA"}}
        }
        nstar = {"per_city_metric": {}}
        with pytest.raises(ValueError, match="NO_TRAIN_DATA"):
            evaluate_ddd(
                city="HongKong", track="high",
                current_cov=0.80, window_elapsed=0.50,
                N_platt_samples=100, mismatch_rate=0.0,
                city_floors_config=floors, n_star_config=nstar,
            )

    def test_missing_nstar_key_raises_keyerror(self):
        """Track not in N_star config raises KeyError (fail-CLOSED)."""
        floors = make_floors_config("TestCity", 1.0)
        nstar = {"per_city_metric": {}}  # TestCity_high missing
        with pytest.raises(KeyError, match="not found in DDD N_star config"):
            evaluate_ddd(
                city="TestCity", track="high",
                current_cov=0.80, window_elapsed=0.50,
                N_platt_samples=100, mismatch_rate=0.0,
                city_floors_config=floors, n_star_config=nstar,
            )

    def test_missing_floors_file_raises(self, tmp_path):
        """Missing floors JSON file raises FileNotFoundError."""
        missing = tmp_path / "missing_floors.json"
        with pytest.raises(FileNotFoundError, match="fail-CLOSED"):
            load_city_floors(missing)

    def test_missing_nstar_file_raises(self, tmp_path):
        """Missing N_star JSON file raises FileNotFoundError."""
        missing = tmp_path / "missing_nstar.json"
        with pytest.raises(FileNotFoundError, match="fail-CLOSED"):
            load_nstar_config(missing)


# ── mismatch_rate floor ───────────────────────────────────────────────────────

class TestMismatchRateFloor:
    def test_mismatch_rate_dominates_when_higher(self):
        """max(mismatch_rate, discount) — mismatch wins when larger."""
        floors, nstar = make_configs("TestCity", "high", 1.0)
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.95,  # shortfall=0.05 → discount=0.01
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.05,  # > 0.01
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.discount == pytest.approx(0.05, abs=1e-9)

    def test_discount_dominates_when_higher(self):
        """max(mismatch_rate, discount) — discount wins when larger."""
        floors, nstar = make_configs("TestCity", "high", 1.0)
        result = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.80,  # shortfall=0.20 → discount=0.04
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.01,  # < 0.04
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.discount == pytest.approx(LINEAR_ALPHA * 0.20, abs=1e-9)


# ── City-specific scenarios ───────────────────────────────────────────────────

class TestCityScenarios:
    def test_lagos_cov_zero_triggers_rail1(self):
        """Lagos: floor=0.45, cov=0 with window>0.5 triggers Rail 1 HALT."""
        floors, nstar = make_configs("Lagos", "high", 0.45)
        result = evaluate_ddd(
            city="Lagos", track="high",
            current_cov=0.0,
            window_elapsed=0.70,
            N_platt_samples=828,
            mismatch_rate=0.02,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "HALT"
        assert result.rail == 1

    def test_lagos_cov_zero_no_fire_early_window(self):
        """Lagos: cov=0 but window=0.30 < 0.50 — Rail 1 does NOT fire."""
        floors, nstar = make_configs("Lagos", "high", 0.45)
        result = evaluate_ddd(
            city="Lagos", track="high",
            current_cov=0.0,
            window_elapsed=0.30,  # too early
            N_platt_samples=828,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "DISCOUNT"
        # cov=0, floor=0.45, shortfall=0.45 → discount=0.09 (cap)
        assert result.discount == pytest.approx(MAX_DISCOUNT, abs=1e-9)

    def test_tokyo_floor_10_cov_6_7_mild_discount(self):
        """Tokyo: floor=1.0, cov=6/7≈0.857 → shortfall≈0.143, discount≈2.86%."""
        cov = 6.0 / 7.0  # ≈ 0.857143
        shortfall = 1.0 - cov   # ≈ 0.142857
        expected_discount = min(MAX_DISCOUNT, LINEAR_ALPHA * shortfall)
        floors, nstar = make_configs("Tokyo", "high", 1.0)
        result = evaluate_ddd(
            city="Tokyo", track="high",
            current_cov=cov,
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
        )
        assert result.action == "DISCOUNT"
        assert result.rail == 2
        assert result.discount == pytest.approx(expected_discount, abs=1e-6)
        # Confirm it's mild (< 3%)
        assert result.discount < 0.03


# ── σ diagnostic: NOT in trigger ─────────────────────────────────────────────

class TestSigmaDiagnosticOnly:
    def test_sigma_stored_in_diagnostic_but_does_not_affect_result(self):
        """σ is logged in diagnostic but does NOT change discount value."""
        floors, nstar = make_configs("TestCity", "high", 1.0)

        result_without_sigma = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.90,
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
            sigma_diagnostic=None,
        )

        result_with_sigma = evaluate_ddd(
            city="TestCity", track="high",
            current_cov=0.90,
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            city_floors_config=floors,
            n_star_config=nstar,
            sigma_diagnostic=0.15,  # large σ that would have changed v1 result
        )

        # Discount must be identical regardless of σ
        assert result_without_sigma.discount == result_with_sigma.discount
        # σ is stored in diagnostic
        assert result_with_sigma.diagnostic["sigma_diagnostic"] == 0.15
        assert result_without_sigma.diagnostic["sigma_diagnostic"] is None


# ── evaluate_ddd_from_files integration test ─────────────────────────────────

class TestFromFilesIntegration:
    def test_default_runtime_artifacts_are_source_owned_and_loadable(self):
        """Default live DDD artifacts must not depend on ops packet paths."""
        assert "docs/operations" not in _DEFAULT_FLOORS_PATH.as_posix()
        assert "docs/operations" not in _DEFAULT_NSTAR_PATH.as_posix()
        assert _DEFAULT_FLOORS_PATH.as_posix().endswith(
            "src/oracle/ddd_artifacts/v2_city_floors.json"
        )
        assert _DEFAULT_NSTAR_PATH.as_posix().endswith(
            "src/oracle/ddd_artifacts/v2_nstar.json"
        )

        floors = load_city_floors()
        nstar = load_nstar_config()

        assert floors.get("_metadata", {}).get("design_version") == "v2-two-rail-2026-05-03"
        assert "Paris" in floors["per_city"]
        assert "Paris_high" in nstar["per_city_metric"]

    def test_from_files_loads_and_evaluates(self, tmp_path):
        """evaluate_ddd_from_files loads JSON files and returns correct result."""
        city = "TestCity"
        track = "high"
        floor = 0.80

        floors_data = make_floors_config(city, floor)
        nstar_data = make_nstar_config(city, track, 110)

        floors_path = tmp_path / "floors.json"
        nstar_path = tmp_path / "nstar.json"
        floors_path.write_text(json.dumps(floors_data))
        nstar_path.write_text(json.dumps(nstar_data))

        result = evaluate_ddd_from_files(
            city=city, track=track,
            current_cov=0.70,  # shortfall=0.10 → discount=0.02
            window_elapsed=0.80,
            N_platt_samples=840,
            mismatch_rate=0.0,
            floors_path=floors_path,
            nstar_path=nstar_path,
        )
        assert result.action == "DISCOUNT"
        assert result.discount == pytest.approx(LINEAR_ALPHA * 0.10, abs=1e-9)

    def test_from_files_raises_on_missing_floors(self, tmp_path):
        """evaluate_ddd_from_files raises FileNotFoundError when floors file missing."""
        nstar_data = make_nstar_config("X", "high", 110)
        nstar_path = tmp_path / "nstar.json"
        nstar_path.write_text(json.dumps(nstar_data))

        with pytest.raises(FileNotFoundError, match="fail-CLOSED"):
            evaluate_ddd_from_files(
                city="X", track="high",
                current_cov=0.80, window_elapsed=0.60,
                N_platt_samples=100, mismatch_rate=0.0,
                floors_path=tmp_path / "nonexistent.json",
                nstar_path=nstar_path,
            )


# ── σ diagnostic sink ────────────────────────────────────────────────────────

class TestDiagnosticSink:
    """RERUN_PLAN_v2.md §5 P2 #7: structured 'ddd_evaluated' INFO log emitted
    on every DDD evaluation so monitoring tools can detect regime shifts.
    σ ships in the payload even though it never enters the trigger.
    """

    def _capture_ddd_record(self, caplog):
        """Pull the most recent ``ddd_evaluated`` record from caplog."""
        records = [r for r in caplog.records if r.message == "ddd_evaluated"]
        assert records, "expected a 'ddd_evaluated' INFO record"
        return records[-1]

    def test_diagnostic_log_emitted_on_rail2_discount(self, caplog):
        floors, nstar = make_configs("Tokyo", "high", 1.0, n_star=100)
        with caplog.at_level("INFO", logger="src.oracle.data_density_discount"):
            evaluate_ddd(
                city="Tokyo", track="high",
                current_cov=0.85, window_elapsed=0.70,
                N_platt_samples=200, mismatch_rate=0.0,
                city_floors_config=floors,
                n_star_config=nstar,
                sigma_diagnostic=0.042,
            )
        rec = self._capture_ddd_record(caplog)
        assert hasattr(rec, "ddd_diag"), "extra={'ddd_diag': ...} missing on log record"
        diag = rec.ddd_diag
        assert diag["city"] == "Tokyo"
        assert diag["track"] == "high"
        assert diag["cov"] == pytest.approx(0.85)
        assert diag["floor"] == pytest.approx(1.0)
        assert diag["sigma"] == pytest.approx(0.042)
        assert diag["action"] == "DISCOUNT"
        assert diag["rail"] == 2
        assert diag["discount"] >= 0.0

    def test_diagnostic_log_emitted_on_rail1_halt(self, caplog):
        floors, nstar = make_configs("Lagos", "high", 0.45, n_star=100)
        with caplog.at_level("INFO", logger="src.oracle.data_density_discount"):
            evaluate_ddd(
                city="Lagos", track="high",
                current_cov=0.0, window_elapsed=0.80,
                N_platt_samples=200, mismatch_rate=0.0,
                city_floors_config=floors,
                n_star_config=nstar,
                sigma_diagnostic=0.188,
            )
        rec = self._capture_ddd_record(caplog)
        diag = rec.ddd_diag
        assert diag["action"] == "HALT"
        assert diag["rail"] == 1
        assert diag["sigma"] == pytest.approx(0.188), (
            "σ must reach the log even when Rail 1 short-circuits trigger eval"
        )

    def test_diagnostic_log_carries_small_sample_amp_flag(self, caplog):
        """When N < N_star, small_sample_amp is true and visible in the log."""
        floors, nstar = make_configs("X", "high", 1.0, n_star=500)
        with caplog.at_level("INFO", logger="src.oracle.data_density_discount"):
            evaluate_ddd(
                city="X", track="high",
                current_cov=0.85, window_elapsed=0.70,
                N_platt_samples=100,  # < n_star=500
                mismatch_rate=0.0,
                city_floors_config=floors,
                n_star_config=nstar,
                sigma_diagnostic=0.0,
            )
        rec = self._capture_ddd_record(caplog)
        assert rec.ddd_diag["small_sample_amp"] is True

    def test_diagnostic_log_emitted_even_at_zero_discount(self, caplog):
        """Even healthy days (cov ≥ floor, no shortfall) emit the log so the
        monitoring stream sees baseline-σ telemetry continuously."""
        floors, nstar = make_configs("Tokyo", "high", 1.0, n_star=100)
        with caplog.at_level("INFO", logger="src.oracle.data_density_discount"):
            evaluate_ddd(
                city="Tokyo", track="high",
                current_cov=1.0, window_elapsed=0.70,
                N_platt_samples=200, mismatch_rate=0.0,
                city_floors_config=floors,
                n_star_config=nstar,
                sigma_diagnostic=0.0,
            )
        rec = self._capture_ddd_record(caplog)
        diag = rec.ddd_diag
        assert diag["discount"] == pytest.approx(0.0)
        assert diag["shortfall"] == pytest.approx(0.0)
        assert diag["sigma"] == pytest.approx(0.0)
