# Created: 2026-09-13
# Last reused/audited: 2026-09-13
# Purpose: Full-pipeline antibody for re-wiring the settlement σ-floor under the current-evidence
#   (Day0/decision-time) shape. b6e587ab2 (2026-07-11) bypassed the floor lookup whenever
#   `bayes_precision_fusion_override.current_evidence_shape is not None` -- every live row --
#   which measurement (scratchpad/noaa_audit/S_sigma_floor2.md, 14-day walk-forward, cluster-
#   robust) showed under-disperses the served belief (cov80 0.787 vs 0.80 nominal; 52.6% of
#   post-cutover rows served a sigma below their own live floor). This module proves the floor
#   now reaches the shipped q builder under current-shape, and that doing so does not reintroduce
#   the "second probability regime" b6e587ab2 warned about: the floor is a max()-only widen on the
#   SAME sigma, not a parallel k/w correction (that neutralization, via _resolve_sigma_tau_calibration,
#   is untouched by this change and stays out of scope here).
"""Settlement σ-floor wiring antibodies for the current-evidence-shape serving path."""
from __future__ import annotations

import json
from dataclasses import replace as _dc_replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as materializer_mod
from src.data.replacement_forecast_materializer import (
    _BayesPrecisionFusionFusionOverride,
    _current_evidence_shape_from_values,
    materialize_replacement_forecast_live,
)
from tests.test_replacement_forecast_materializer import (
    _TemperatureBin,
    _conn,
    _current_baseline_data_version,
    _dt,
    _request,
)
from tests.test_sigma_tau_calibration_serving_equivalence import (
    _REQUEST_KWARGS,
    _current_shape_members,
    _install_current_evidence_fusion,
)

_UTC = timezone.utc


def _full_row(conn) -> dict:
    row = conn.execute(
        "SELECT q_json, q_lcb_json, q_ucb_json, provenance_json FROM forecast_posteriors "
        "ORDER BY posterior_id DESC LIMIT 1"
    ).fetchone()
    return {
        "q": json.loads(row["q_json"]),
        "q_lcb": json.loads(row["q_lcb_json"]) if row["q_lcb_json"] is not None else None,
        "q_ucb": json.loads(row["q_ucb_json"]) if row["q_ucb_json"] is not None else None,
        "provenance": json.loads(row["provenance_json"]),
    }


def _isolate_sigma_tau_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No sigma_tau_calibration.json -> _resolve_sigma_tau_calibration resolves to the neutral
    # (1.0, 0.0, 0.0) k/w/floor_steps under current-shape, so ONLY the settlement sigma floor
    # (this change) can widen sigma in these tests -- isolates the property under test.
    monkeypatch.setattr(cfg, "runtime_state_path", lambda fn: tmp_path / fn)


def _patch_floor(monkeypatch: pytest.MonkeyPatch, floor_c: float | None, reason: str | None) -> None:
    monkeypatch.setattr(
        materializer_mod,
        "_replacement_settlement_sigma_floor_lookup",
        lambda request, *, metric: (floor_c, reason),
    )


# The default _request() bins (tests/test_replacement_forecast_materializer._bins()):
#   cool: upper_c=20.0 (open-ended low side)
#   warm: lower_c=21.0, upper_c=30.0 (interior)
#   hot:  lower_c=31.0 (open-ended high side)
# anchor_value_c=25.0, predictive_sigma_c=2.0 (from _install_current_evidence_fusion's override).


def test_current_shape_floor_applies_and_widens_sigma(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(a) Under current-shape, a binding floor (5.0 >> predictive_sigma_c=2.0) is threaded into the
    served q -- provenance carries the applied floor value, and the far catch-all is measurably
    widened relative to the un-floored serving (proving sigma_used actually changed, i.e. the floor
    reached the builder rather than being silently dropped as it was under b6e587ab2)."""
    _isolate_sigma_tau_artifact(monkeypatch, tmp_path)
    _install_current_evidence_fusion(monkeypatch)

    _patch_floor(monkeypatch, None, "SETTLEMENT_SIGMA_FLOOR_ABSENT:test")
    conn_bare = _conn()
    result_bare = materialize_replacement_forecast_live(conn_bare, _request(**_REQUEST_KWARGS))
    assert result_bare.ok is True
    bare = _full_row(conn_bare)

    _patch_floor(monkeypatch, 5.0, None)
    conn_floored = _conn()
    result_floored = materialize_replacement_forecast_live(conn_floored, _request(**_REQUEST_KWARGS))
    assert result_floored.ok is True
    floored = _full_row(conn_floored)

    assert floored["provenance"]["settlement_sigma_floor_applied"] is True
    assert floored["provenance"]["settlement_sigma_floor_c"] == pytest.approx(5.0)
    # sigma_used = max(anchor_sigma, floor) only ever WIDENS: the un-floored serving must differ
    # from the floored one (the floor actually reached the pure builder under current-shape).
    assert floored["q"] != bare["q"], (
        "a floor of 5.0 against predictive_sigma_c=2.0 must change the served q; identical q means "
        "the floor never reached _build_scaled_normal_uniform_q under current-shape"
    )


def test_current_shape_open_ended_bin_keeps_unfloored_mass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(b) The catch-all coherence cap (_build_scaled_normal_uniform_q, ~:5288+) pins each open-ended
    bin at its un-floored mass under current-shape too -- the "Paris >=26" invariant applies
    regardless of which branch supplied the floor. A binding floor must mark BOTH open-ended bins
    (cool, hot) as catch-all-capped, and neither's served mass may exceed its un-floored mass."""
    _isolate_sigma_tau_artifact(monkeypatch, tmp_path)
    _install_current_evidence_fusion(monkeypatch)

    _patch_floor(monkeypatch, None, "SETTLEMENT_SIGMA_FLOOR_ABSENT:test")
    conn_bare = _conn()
    result_bare = materialize_replacement_forecast_live(conn_bare, _request(**_REQUEST_KWARGS))
    assert result_bare.ok is True
    bare = _full_row(conn_bare)

    _patch_floor(monkeypatch, 5.0, None)
    conn_floored = _conn()
    result_floored = materialize_replacement_forecast_live(conn_floored, _request(**_REQUEST_KWARGS))
    assert result_floored.ok is True
    floored = _full_row(conn_floored)

    capped = set(floored["provenance"]["settlement_sigma_floor_catchall_capped"])
    assert capped == {"cool", "hot"}, (
        f"both open-ended bins must be catch-all-capped under a floor this much wider than "
        f"predictive_sigma_c; got {capped}"
    )
    # The cap pins the RAW (pre-normalization) open-ended mass at its un-floored value WITHIN this
    # one builder call -- cross-call comparison against `bare` (a different sigma regime, hence a
    # different normalization total) is not the right invariant; that per-call pre-normalization
    # bound is already proven independently in
    # tests/calibration/test_scaled_normal_uniform_q_builder.py
    # (test_catchall_open_ended_never_exceeds_unfloored_mass,
    # test_golden_full_ladder_with_binding_settlement_floor_matches_hand_derivation). This test's
    # job is only to prove the cap is REACHED (capped == {"cool", "hot"}) under current-shape,
    # which it now is -- b6e587ab2's bypass made this set always empty.
    assert bare["provenance"]["settlement_sigma_floor_catchall_capped"] == []


def test_current_shape_floor_provenance_applied_and_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(c) settlement_sigma_floor_applied / settlement_sigma_floor_c / _unavailable_reason are
    stamped correctly under current-shape in both directions: floor present -> applied True, the
    value populated, no reason; floor absent -> applied False, no value, the reason populated."""
    _isolate_sigma_tau_artifact(monkeypatch, tmp_path)
    _install_current_evidence_fusion(monkeypatch)

    _patch_floor(monkeypatch, 5.0, None)
    conn_floored = _conn()
    result = materialize_replacement_forecast_live(conn_floored, _request(**_REQUEST_KWARGS))
    assert result.ok is True
    prov = _full_row(conn_floored)["provenance"]
    assert prov["settlement_sigma_floor_applied"] is True
    assert prov["settlement_sigma_floor_c"] == pytest.approx(5.0)
    assert prov["settlement_sigma_floor_unavailable_reason"] is None

    _patch_floor(monkeypatch, None, "SETTLEMENT_SIGMA_FLOOR_ABSENT:Shanghai|JJA|high")
    conn_absent = _conn()
    result_absent = materialize_replacement_forecast_live(conn_absent, _request(**_REQUEST_KWARGS))
    assert result_absent.ok is True
    prov_absent = _full_row(conn_absent)["provenance"]
    assert prov_absent["settlement_sigma_floor_applied"] is False
    assert prov_absent["settlement_sigma_floor_c"] is None
    assert prov_absent["settlement_sigma_floor_unavailable_reason"] == (
        "SETTLEMENT_SIGMA_FLOOR_ABSENT:Shanghai|JJA|high"
    )


def test_current_shape_non_binding_floor_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(d) A row whose anchor sigma (2.0) already exceeds the floor (1.0) is byte-identical to the
    no-floor serving on q / q_lcb / q_ucb -- max() only widens and never binds here, so re-wiring
    the lookup must not perturb any row where the floor was already slack."""
    _isolate_sigma_tau_artifact(monkeypatch, tmp_path)
    _install_current_evidence_fusion(monkeypatch)

    _patch_floor(monkeypatch, None, "SETTLEMENT_SIGMA_FLOOR_ABSENT:test")
    conn_bare = _conn()
    result_bare = materialize_replacement_forecast_live(conn_bare, _request(**_REQUEST_KWARGS))
    assert result_bare.ok is True
    bare = _full_row(conn_bare)

    _patch_floor(monkeypatch, 1.0, None)  # 1.0 < predictive_sigma_c=2.0 -> never binds
    conn_slack = _conn()
    result_slack = materialize_replacement_forecast_live(conn_slack, _request(**_REQUEST_KWARGS))
    assert result_slack.ok is True
    slack = _full_row(conn_slack)

    assert slack["q"] == bare["q"]
    assert slack["q_lcb"] == bare["q_lcb"]
    assert slack["q_ucb"] == bare["q_ucb"]
    assert slack["provenance"]["settlement_sigma_floor_catchall_capped"] == []
    # Only the floor-provenance fields legitimately differ (the lookup ran and found a slack floor).
    assert slack["provenance"]["settlement_sigma_floor_applied"] is True
    assert slack["provenance"]["settlement_sigma_floor_c"] == pytest.approx(1.0)
    diff_keys = {
        k
        for k in set(bare["provenance"]) | set(slack["provenance"])
        if bare["provenance"].get(k) != slack["provenance"].get(k)
    }
    assert diff_keys <= {
        "settlement_sigma_floor_applied",
        "settlement_sigma_floor_c",
        "settlement_sigma_floor_unavailable_reason",
    }, f"a non-binding floor must not perturb any other provenance field; extra diffs: {diff_keys}"


# ---------------------------------------------------------------------------
# R-AJ review HIGH finding: the shared Day0 carrier branch (q_shape ==
# "day0_remaining_shared_carrier_v1", ~65% of live rows) discards the builder entirely and
# never consults settlement_sigma_floor_c -- the now-unconditional lookup must not leave stale
# applied=True/floor_c=<value> provenance on a q it never shaped.
# ---------------------------------------------------------------------------


def _install_current_evidence_hko_carrier_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A current_evidence_shape (drives the settlement-floor lookup, now unconditional) combined
    with an HKO provisional day0 carrier on the SAME row -- the exact combination R-AJ's review
    measured live."""
    shape = _current_evidence_shape_from_values(
        snapshot_id=42,
        source_cycle_time="2026-06-06T00:00:00+00:00",
        source_available_at="2026-06-06T02:00:00+00:00",
        members_c=_current_shape_members(),
        provider_values_c={"ecmwf_ifs": 24.8, "icon_global": 25.2},
        provider_weights={"ecmwf_ifs": 0.5, "icon_global": 0.5},
        center_c=25.0,
        provider_cycles={
            "ecmwf_ifs": "2026-06-06T00:00:00+00:00",
            "icon_global": "2026-06-06T00:00:00+00:00",
        },
    )
    override = _BayesPrecisionFusionFusionOverride(
        anchor_value_c=25.0,
        anchor_sigma_c=0.35,
        method="test_bayes_precision_fusion",
        used_models=("ecmwf_ifs9", "gfs", "icon", "gem", "jma"),
        model_set_hash="test-model-set",
        resolution_mix_hash="test-resolution-mix",
        lead_bucket="d1",
        dropped_models=(),
        excluded_regionals=(),
        dropped_aliases=(),
        raw_model_forecast_ids=(101, 102, 103),
        anchor_bridge={"test": True},
        predictive_sigma_c=2.0,
        decorrelated_providers_complete=True,
        decorrelated_providers_served=5,
        decorrelated_providers_expected=5,
        current_value_serving={"ecmwf_ifs9": {"served_via": "single_runs"}},
        current_evidence_shape=shape.as_payload(),
        current_evidence_members_c=shape.members_c,
    )
    monkeypatch.setattr(
        materializer_mod,
        "_replacement_bayes_precision_fusion_override",
        lambda *args, **kwargs: override,
    )


_CARRIER_Q = (0.05, 0.15, 0.80)


def _hko_carrier_request():
    # Bin/observation shape mirrors tests/test_replacement_forecast_materializer.py::
    # test_materializer_hko_provisional_observation_does_not_truncate_support (the day0_remaining_
    # shared_carrier_v1 trigger: an HKO-provisional day0_observed_extreme_source + a local day
    # already under way). That test itself currently fails pre-existing on parent 34b9fe237 too
    # (read_day0_current_temperature_state needs live ingest tables _conn() does not seed --
    # confirmed identical in the 89/89 baseline-parity failure set, unrelated to this change), so
    # this test bypasses _day0_noaa_preliminary_carrier directly (below) rather than depend on
    # that DB-backed helper.
    return _dc_replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=_UTC),
            day0_observed_extreme_c=25.7,
            day0_observed_extreme_source="hko_hourly_accumulator",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
            day0_observed_extreme_sample_count=12,
        ),
        temperature_metric="low",
        baseline_data_version=_current_baseline_data_version("low"),
        bins=(
            _TemperatureBin(
                "below24", upper_c=23.0, center_c=22.0, rounding_rule="oracle_truncate"
            ),
            _TemperatureBin(
                "target24", lower_c=24.0, upper_c=24.0, center_c=24.0,
                rounding_rule="oracle_truncate",
            ),
            _TemperatureBin(
                "25plus", lower_c=25.0, center_c=26.0, rounding_rule="oracle_truncate"
            ),
        ),
    )


def _install_synthetic_day0_carrier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypasses _day0_noaa_carrier_future_members AND _day0_noaa_preliminary_carrier (both would
    otherwise call read_day0_current_temperature_state, which needs live ingest tables this
    in-memory fixture does not have -- the same pre-existing gap noted in _hko_carrier_request)
    so the materializer reaches the `_day0_shared_carrier is not None` branch (:6527) under test
    without depending on that unrelated, already-broken DB read."""
    monkeypatch.setattr(
        materializer_mod,
        "_day0_noaa_carrier_future_members",
        lambda *_args, **_kwargs: ((25.2, 25.5, 28.3), 0.5, _dt(18).isoformat(), ()),
    )
    samples = [list(_CARRIER_Q) for _ in range(500)]
    carrier = {
        "q": list(_CARRIER_Q),
        "samples": samples,
        "content_identity": "test-content-identity",
        "operator": "test-operator",
        "sample_count": len(samples),
    }
    likelihood = {"identity_hash": "test-carrier-identity-hash"}
    monkeypatch.setattr(
        materializer_mod,
        "_day0_noaa_preliminary_carrier",
        lambda *_args, **_kwargs: (carrier, likelihood),
    )


def test_current_shape_carrier_branch_floor_provenance_is_neutral(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(R-AJ HIGH) On the shared Day0 carrier branch, a binding floor must NOT make
    settlement_sigma_floor_applied lie True: reset to neutral with a named reason, and the
    served q/q_lcb/q_ucb stay byte-identical whether or not a floor exists for the cell."""
    _isolate_sigma_tau_artifact(monkeypatch, tmp_path)
    _install_current_evidence_hko_carrier_fusion(monkeypatch)
    _install_synthetic_day0_carrier(monkeypatch)

    _patch_floor(monkeypatch, None, "SETTLEMENT_SIGMA_FLOOR_ABSENT:test")
    conn_bare = _conn()
    result_bare = materialize_replacement_forecast_live(conn_bare, _hko_carrier_request())
    assert result_bare.ok is True
    bare = _full_row(conn_bare)
    assert bare["provenance"]["q_shape"] == "day0_remaining_shared_carrier_v1"

    _patch_floor(monkeypatch, 5.0, None)  # would bind against predictive_sigma_c=2.0 if it reached anything
    conn_floored = _conn()
    result_floored = materialize_replacement_forecast_live(conn_floored, _hko_carrier_request())
    assert result_floored.ok is True
    floored = _full_row(conn_floored)
    assert floored["provenance"]["q_shape"] == "day0_remaining_shared_carrier_v1"

    assert floored["provenance"]["settlement_sigma_floor_applied"] is False, (
        "the carrier branch must never claim the floor shaped a q it never consulted"
    )
    assert floored["provenance"]["settlement_sigma_floor_c"] is None
    assert floored["provenance"]["settlement_sigma_floor_unavailable_reason"] == (
        "SETTLEMENT_SIGMA_FLOOR_NOT_APPLICABLE:day0_shared_carrier"
    )
    assert floored["q"] == bare["q"]
    assert floored["q_lcb"] == bare["q_lcb"]
    assert floored["q_ucb"] == bare["q_ucb"]
