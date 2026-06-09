# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K2+N1+#122 (task #167). Reactor-wiring relationship tests for
#   the BiasTreatment v2 path in src/engine/event_reactor_adapter.py. Gated on
#   edli_v1.bias_treatment_v2_enabled. Proves: (1) flag-OFF is BYTE-IDENTICAL to legacy
#   for BOTH _maybe_apply_edli_bias_correction and _maybe_bias_decay_kelly_haircut;
#   (2) flag-ON enforces corrected-XOR-haircut (kills N1 double penalty); (3) flag-ON
#   fail-closes on NULL-authority (#122) and stale training_cutoff; (4) flag-ON folds the
#   bias-mean SE into representativeness_sigma so a low-n correction widens q_lcb (D4).
"""BiasTreatment v2 reactor wiring tests.

The type-level invariants are in tests/contracts/test_bias_treatment.py. Here we test that
the live reactor seams honour the flag and compose CORRECT-XOR-HAIRCUT.
"""
from __future__ import annotations

import numpy as np
import pytest

import src.engine.event_reactor_adapter as era
import src.calibration.ens_bias_repo as ens_bias_repo
import src.state.db as state_db
import src.calibration.manager as cal_manager


class _FakeCity:
    def __init__(self, name, unit, lat=35.0):
        self.name = name
        self.settlement_unit = unit
        self.lat = lat


class _FakeConn:
    row_factory = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


class _Family:
    def __init__(self, city, metric="high", target_date="2026-06-15"):
        self.city = city
        self.metric = metric
        self.target_date = target_date


def _row(*, eff=3.5, sd=2.1, n=7, n_prior=1, n_paired=0, authority="VERIFIED",
         training_cutoff="2026-06-10T00:00:00+00:00", weight_live=1.0,
         total_sd=None, correction_strength=0.8):
    """A model_bias_ens row dict the patched read_bias_model returns."""
    return {
        "effective_bias_c": eff,
        "residual_sd_c": sd,
        "total_residual_sd_c": total_sd if total_sd is not None else sd,
        "n_live": n,
        "n_prior": n_prior,
        "n_paired": n_paired,
        "authority": authority,
        "training_cutoff": training_cutoff,
        "weight_live": weight_live,
        "correction_strength": correction_strength,
    }


@pytest.fixture
def patched(monkeypatch):
    cities = {
        "Tokyo": _FakeCity("Tokyo", "C", 35.0),
        "San Francisco": _FakeCity("San Francisco", "F", 37.6),
    }
    monkeypatch.setattr(era, "runtime_cities_by_name", lambda: cities)
    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(cal_manager, "season_from_date", lambda d, lat=None: "JJA")

    state = {"row": _row()}

    def _read(*a, **k):
        # honour the authority filter the real query applies: a non-VERIFIED request
        # would never return a VERIFIED-only row, but our callers always pass VERIFIED.
        return state["row"]

    monkeypatch.setattr(ens_bias_repo, "read_bias_model", _read)
    # canonical thresholds + haircut ON (legacy baseline behaviour)
    monkeypatch.setitem(era.settings["edli_v1"], "bias_decay_kelly_haircut_enabled", True)
    monkeypatch.setitem(era.settings["edli_v1"], "edli_bias_correction_enabled", True)
    monkeypatch.setitem(era.settings["edli_v1"], "bias_decay_threshold_c", 2.0)
    monkeypatch.setitem(era.settings["edli_v1"], "bias_decay_threshold_f", 3.0)
    monkeypatch.setitem(era.settings["edli_v1"], "bias_decay_kelly_factor", 0.5)
    return state


# ---------------------------------------------------------------------------
# (1) FLAG-OFF == LEGACY (byte-identical) — the non-negotiable shadow-safety contract
# ---------------------------------------------------------------------------
class TestFlagOffCorrectionFailClosed:
    def test_flag_off_correction_returns_raw_members(self, patched, monkeypatch):
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", False)
        members = np.array([20.0, 21.0, 22.0, 23.0, 24.0])
        snapshot = {"dataset_id": "ecmwf_opendata_mx2t3_local_calendar_day_max"}
        family = _Family("Tokyo")
        city = era.runtime_cities_by_name()["Tokyo"]
        out, applied = era._maybe_apply_edli_bias_correction(
            members, snapshot=snapshot, family=family, city=city, payload={}
        )
        assert applied is False
        np.testing.assert_allclose(out, members)

    def test_flag_off_haircut_byte_identical(self, patched, monkeypatch):
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", False)
        family = _Family("Tokyo")
        km, applied, native, reason = era._maybe_bias_decay_kelly_haircut(1.0, family=family)
        # legacy: |3.5| > 2.0 -> halve.
        assert applied is True
        assert km == pytest.approx(0.5)
        assert reason == "bias_exceeds"


# ---------------------------------------------------------------------------
# (2) FLAG-ON: corrected XOR haircut — the N1 double-penalty kill
# ---------------------------------------------------------------------------
class TestNoDoublePenaltyWhenFlagOn:
    def test_corrected_bucket_does_not_also_haircut(self, patched, monkeypatch):
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", True)
        family = _Family("Tokyo")
        city = era.runtime_cities_by_name()["Tokyo"]
        snapshot = {"dataset_id": "ecmwf_opendata_mx2t3_local_calendar_day_max"}
        # correction DOES apply (shift p_raw)
        members = np.array([20.0, 21.0, 22.0, 23.0, 24.0])
        out, applied = era._maybe_apply_edli_bias_correction(
            members, snapshot=snapshot, family=family, city=city, payload={}
        )
        assert applied is True
        np.testing.assert_allclose(out, members - 3.5)
        # ... therefore the haircut MUST NOT also fire on the SAME row (XOR)
        km, hc_applied, native, reason = era._maybe_bias_decay_kelly_haircut(1.0, family=family)
        assert km == pytest.approx(1.0), "corrected bucket was ALSO haircut — N1 double penalty"
        assert hc_applied is False

    def test_uncorrected_bucket_still_haircuts(self, patched, monkeypatch):
        # When the correction would NOT apply (correction flag off for this bucket) the
        # haircut path is still available — XOR, not "never haircut".
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", True)
        monkeypatch.setitem(era.settings["edli_v1"], "edli_bias_correction_enabled", False)
        family = _Family("Tokyo")
        km, hc_applied, native, reason = era._maybe_bias_decay_kelly_haircut(1.0, family=family)
        assert km == pytest.approx(0.5)
        assert hc_applied is True


# ---------------------------------------------------------------------------
# (3) FLAG-ON fail-closed: NULL-authority (#122) + stale cutoff refused
# ---------------------------------------------------------------------------
class TestFailClosedProvenanceAndStale:
    def test_null_authority_row_refused(self, patched, monkeypatch):
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", True)
        patched["row"] = _row(authority=None)
        family = _Family("Tokyo")
        city = era.runtime_cities_by_name()["Tokyo"]
        snapshot = {"dataset_id": "ecmwf_opendata_mx2t3_local_calendar_day_max"}
        members = np.array([20.0, 21.0, 22.0, 23.0, 24.0])
        out, applied = era._maybe_apply_edli_bias_correction(
            members, snapshot=snapshot, family=family, city=city, payload={}
        )
        assert applied is False, "NULL-authority row entered the correction (provenance #122)"
        np.testing.assert_allclose(out, members)

    def test_stale_training_cutoff_refused(self, patched, monkeypatch):
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", True)
        # season_from_date is patched to always "JJA" in the fixture; override it to a
        # real mapping so a May cutoff vs June target is genuinely out-of-season.
        from src.contracts import season as season_mod
        monkeypatch.setattr(
            cal_manager, "season_from_date",
            lambda d, lat=None: season_mod.season_from_date(d, lat=lat if lat is not None else 90.0),
        )
        patched["row"] = _row(training_cutoff="2026-05-25T00:00:00+00:00")
        family = _Family("Tokyo", target_date="2026-06-15")
        city = era.runtime_cities_by_name()["Tokyo"]
        snapshot = {"dataset_id": "ecmwf_opendata_mx2t3_local_calendar_day_max"}
        members = np.array([20.0, 21.0, 22.0, 23.0, 24.0])
        out, applied = era._maybe_apply_edli_bias_correction(
            members, snapshot=snapshot, family=family, city=city, payload={}
        )
        assert applied is False, "stale (May) fit entered a June correction"
        np.testing.assert_allclose(out, members)

    def test_no_prior_or_paired_support_correction_row_refused(self, patched, monkeypatch):
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", True)
        patched["row"] = _row(correction_strength=0.8, n_prior=0, n_paired=0)
        family = _Family("Tokyo", target_date="2026-06-15")
        city = era.runtime_cities_by_name()["Tokyo"]
        snapshot = {"dataset_id": "ecmwf_opendata_mx2t3_local_calendar_day_max"}
        members = np.array([20.0, 21.0, 22.0, 23.0, 24.0])
        out, applied = era._maybe_apply_edli_bias_correction(
            members, snapshot=snapshot, family=family, city=city, payload={}
        )
        assert applied is False, "no-support bias row shifted p_raw"
        np.testing.assert_allclose(out, members)


# ---------------------------------------------------------------------------
# (4) FLAG-ON D4: low-n correction folds SE -> wider representativeness_sigma
# ---------------------------------------------------------------------------
class TestLowNWidensRepresentativenessSigma:
    def test_low_n_sigma_gt_high_n_sigma(self, patched, monkeypatch):
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", True)
        family = _Family("Tokyo")
        city = era.runtime_cities_by_name()["Tokyo"]
        snapshot = {"dataset_id": "ecmwf_opendata_mx2t3_local_calendar_day_max"}
        # n=7 (<20) -> SE folded; n=50 -> SE negligible. SAME residual scale.
        patched["row"] = _row(n=7, sd=2.1, total_sd=2.1)
        sig_n7 = era._edli_representativeness_sigma_native(snapshot=snapshot, family=family, city=city)
        patched["row"] = _row(n=50, sd=2.1, total_sd=2.1)
        sig_n50 = era._edli_representativeness_sigma_native(snapshot=snapshot, family=family, city=city)
        assert sig_n7 > sig_n50, "low-n correction did not widen representativeness_sigma (D4)"

    def test_flag_off_sigma_unchanged_by_n(self, patched, monkeypatch):
        # With v2 OFF the SE term is NOT folded — sigma depends only on total_residual_sd_c,
        # identical for n=7 and n=50. Byte-identical legacy behaviour.
        monkeypatch.setitem(era.settings["edli_v1"], "bias_treatment_v2_enabled", False)
        family = _Family("Tokyo")
        city = era.runtime_cities_by_name()["Tokyo"]
        snapshot = {"dataset_id": "ecmwf_opendata_mx2t3_local_calendar_day_max"}
        patched["row"] = _row(n=7, sd=2.1, total_sd=2.1)
        sig_n7 = era._edli_representativeness_sigma_native(snapshot=snapshot, family=family, city=city)
        patched["row"] = _row(n=50, sd=2.1, total_sd=2.1)
        sig_n50 = era._edli_representativeness_sigma_native(snapshot=snapshot, family=family, city=city)
        assert sig_n7 == pytest.approx(sig_n50)
