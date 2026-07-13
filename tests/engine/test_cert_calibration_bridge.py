# Created: 2026-06-10
# Last reused or audited: 2026-07-13
# Authority basis: Operator-gated funnel #1 unlock (2026-06-10) — first-class calibration
#   authority for replacement-chain candidates (FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE). Tears
#   down the Platt-cohort wall: replacement candidates' q never passes through Platt, so the
#   legacy IDENTITY_FALLBACK_NO_PLATT_BUCKET reject is a category mismatch. These are
#   RELATIONSHIP tests (Fitz methodology): they assert the cross-function invariant
#   credential-state -> certificate-authority -> live-gate-verdict holds across the boundary
#   between the calibration-authority builder, the live admission gate, and the verifier.
#   Typed coverage verdicts license settlement coverage only when they carry enough
#   settled evidence. INSUFFICIENT_DATA is thin-history provenance and admits through
#   the conservative fused-bootstrap q_lcb authority, not through the settlement-coverage
#   authority. UNLICENSED still admits ONLY when the shrink was actually applied to the
#   leg; no verdict / unknown verdict remains blocked.
"""Quadrant relationship matrix for the replacement calibration credential.

The credential bridges three modules whose boundary the bug lived at:
  (1) the live replacement builder stamps `payload[_REPLACEMENT_CALIBRATION_CREDENTIAL_KEY]`,
  (2) `_calibration_authority_payload_and_clock` renders it into the certificate authority,
  (3) `_assert_event_bound_calibration_live_admitted` (the live gate) admits/rejects it,
  (4) the verifier's APPROVED_CALIBRATION_AUTHORITIES round-trips the admitted authority.

The quadrants:
  Q1 replacement + bounds + LICENSED / UNLICENSED-shrunk -> admitted,
     FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE
  Q1b replacement + bounds + INSUFFICIENT_DATA -> admitted,
      FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB
  Q2 replacement + NO bounds                   -> IDENTITY_FALLBACK reject (unchanged)
  Q3 replacement + bounds, NO verdict (None) / unknown status OR UNLICENSED-unshrunk
     -> UNEVALUATED reject
  Q4 legacy candidate, no Platt bucket         -> IDENTITY_FALLBACK reject (unchanged)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.config import runtime_cities_by_name, settings
from src.decision_kernel.verifier import APPROVED_CALIBRATION_AUTHORITIES
from src.engine import event_reactor_adapter as adapter
from src.engine.event_reactor_adapter import (
    FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY,
    FUSED_BOOTSTRAP_CONSERVATIVE_QLCB_AUTHORITY,
    FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED_AUTHORITY,
    _REPLACEMENT_CALIBRATION_CREDENTIAL_KEY,
    _assert_event_bound_calibration_live_admitted,
    _build_replacement_calibration_credential,
    _calibration_authority_payload_and_clock,
    _replacement_family_coverage_verdict,
)
from src.types.market import Bin


DECISION_TIME = datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc)
_CITY = "Chicago"
_METRIC = "high"
_TARGET_DATE = "2026-05-25"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _family():
    return SimpleNamespace(city=_CITY, metric=_METRIC, target_date=_TARGET_DATE)


def _replacement_bundle(*, q_lcb, q_lcb_basis, q_mode):
    """Minimal stand-in for ReplacementForecastPosteriorBundle (only the fields the
    credential builder reads: provenance_json, q_lcb, posterior_id)."""
    provenance = {
        "replacement_q_mode": q_mode,
        "q_lcb_basis": q_lcb_basis,
        "q_lcb_bootstrap_draws": 200,
    }
    return SimpleNamespace(
        provenance_json=provenance,
        q_lcb=q_lcb,
        posterior_id=4242,
    )


def _coverage_verdict(status, *, q_lcb_in=0.80, q_lcb_out=0.80, n=60, ratio=1.0, realized=0.80):
    from src.calibration.settlement_backward_coverage import CoverageVerdict

    return CoverageVerdict(
        status=status,
        q_lcb_in=q_lcb_in,
        q_lcb_out=q_lcb_out,
        n_settlement_observations=n,
        coverage_ratio=ratio,
        realized_win_rate=realized,
    )


def _render_payload(credential):
    """credential dict -> certificate calibration payload via the production builder.

    Uses a tiny payload carrying the credential + a horizon_profile so the legacy Platt
    lookup is never reached (the credential short-circuits it)."""
    payload = {
        _REPLACEMENT_CALIBRATION_CREDENTIAL_KEY: credential,
        "horizon_profile": "full",
    }
    forecast_payload = {"horizon_profile": "full"}
    cal_payload, clock = _calibration_authority_payload_and_clock(
        sqlite3.connect(":memory:"),
        event=SimpleNamespace(),
        family=_family(),
        payload=payload,
        forecast_payload=forecast_payload,
        decision_time=DECISION_TIME,
    )
    return cal_payload, clock


# ---------------------------------------------------------------------------
# Q1 — replacement + bounds + an ADMITTING coverage verdict -> new authority
#   LICENSED: admitted at the cert layer.
#   UNLICENSED-shrunk (q_lcb_out<q_lcb_in): admitted (the shrunk q_lcb is honest).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["LICENSED", "UNLICENSED"])
def test_q1_replacement_with_bounds_and_coverage_is_admitted(status):
    bundle = _replacement_bundle(
        q_lcb={"bin-a": 0.80},
        q_lcb_basis="fused_center_bootstrap_p05",
        q_mode="FUSED_NORMAL_FULL",
    )
    # UNLICENSED admits ONLY when the shrink was actually applied: a real downward shrink
    # (q_lcb_out < q_lcb_in) with the coverage gate ON. LICENSED needs no shrink.
    if status == "UNLICENSED":
        verdict = _coverage_verdict("UNLICENSED", q_lcb_in=0.80, q_lcb_out=0.66)
    else:
        verdict = _coverage_verdict("LICENSED")
    expected_samples = 60
    credential = _build_replacement_calibration_credential(
        replacement_bundle=bundle,
        q_mode="FUSED_NORMAL_FULL",
        coverage_verdict=verdict,
        family=_family(),
    )
    assert credential is not None

    cal_payload, _clock = _render_payload(credential)
    assert cal_payload["authority"] == FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY
    # provenance fields round-trip into the payload (mission part 1)
    assert cal_payload["q_lcb_basis"] == "fused_center_bootstrap_p05"
    assert cal_payload["bootstrap_draws"] == 200
    assert cal_payload["replacement_q_mode"] == "FUSED_NORMAL_FULL"
    assert cal_payload["coverage_status"] == status
    assert cal_payload["posterior_id"] == 4242
    assert cal_payload["n_samples"] == expected_samples
    assert cal_payload["season"] is not None

    # the live gate ADMITS it (no raise)
    cert = SimpleNamespace(payload=cal_payload)
    _assert_event_bound_calibration_live_admitted(cert)

    # the verifier's approved set round-trips it
    assert FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY in APPROVED_CALIBRATION_AUTHORITIES


def test_insufficient_data_coverage_uses_conservative_bootstrap_authority():
    bundle = _replacement_bundle(
        q_lcb={"bin-a": 0.80},
        q_lcb_basis="fused_center_bootstrap_p05",
        q_mode="FUSED_NORMAL_FULL",
    )
    credential = _build_replacement_calibration_credential(
        replacement_bundle=bundle,
        q_mode="FUSED_NORMAL_FULL",
        coverage_verdict=_coverage_verdict("INSUFFICIENT_DATA", n=0, ratio=None, realized=None),
        family=_family(),
    )
    assert credential is not None

    cal_payload, _clock = _render_payload(credential)
    assert cal_payload["coverage_status"] == "INSUFFICIENT_DATA"
    assert cal_payload["n_samples"] == 0
    assert cal_payload["authority"] == FUSED_BOOTSTRAP_CONSERVATIVE_QLCB_AUTHORITY
    assert cal_payload["q_lcb_basis"] == "fused_center_bootstrap_p05"
    assert cal_payload["bootstrap_draws"] == 200

    _assert_event_bound_calibration_live_admitted(SimpleNamespace(payload=cal_payload))
    assert FUSED_BOOTSTRAP_CONSERVATIVE_QLCB_AUTHORITY in APPROVED_CALIBRATION_AUTHORITIES


def test_day0_live_observation_hard_fact_cannot_authorize_entry_probability():
    """Observation truth alone is not an exact-bin probability model."""

    payload = {
        "city": _CITY,
        "target_date": _TARGET_DATE,
        "metric": _METRIC,
        "source_match_status": "MATCH",
        "station_match_status": "MATCH",
        "local_date_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "observation_time": "2026-05-24T18:00:00+00:00",
        "observation_available_at": "2026-05-24T18:01:00+00:00",
        "raw_value": 72.0,
        "rounded_value": 72,
        "station_id": "KORD",
        "settlement_source": "wu_icao_history",
        "horizon_profile": "full",
    }
    with pytest.raises(
        ValueError,
        match="DAY0_CALIBRATION_AUTHORITY_BLOCKED:day0_probability_q_source required:missing",
    ):
        _calibration_authority_payload_and_clock(
            sqlite3.connect(":memory:"),
            event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
            family=SimpleNamespace(city=_CITY, metric=_METRIC, target_date=_TARGET_DATE),
            payload=payload,
            forecast_payload={"horizon_profile": "full"},
            decision_time=DECISION_TIME,
        )


def test_q1_unlicensed_unshrunk_is_blocked(monkeypatch):
    """An UNLICENSED verdict with no real shrink is an overconfident bound serving live."""
    bundle = _replacement_bundle(
        q_lcb={"bin-a": 0.80},
        q_lcb_basis="fused_center_bootstrap_p05",
        q_mode="FUSED_NORMAL_FULL",
    )
    credential2 = _build_replacement_calibration_credential(
        replacement_bundle=bundle,
        q_mode="FUSED_NORMAL_FULL",
        coverage_verdict=_coverage_verdict("UNLICENSED", q_lcb_in=0.80, q_lcb_out=0.80),
        family=_family(),
    )
    cal_payload2, _clock2 = _render_payload(credential2)
    assert cal_payload2["authority"] == FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED_AUTHORITY


# ---------------------------------------------------------------------------
# Q2 — replacement WITHOUT bounds -> no credential -> IDENTITY_FALLBACK reject
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "q_lcb,q_lcb_basis,q_mode",
    [
        (None, "fused_center_bootstrap_p05", "FUSED_NORMAL_FULL"),          # q_lcb_json null
        ({}, "fused_center_bootstrap_p05", "FUSED_NORMAL_FULL"),            # empty bounds map
        ({"bin-a": 0.8}, "wilson_member_vote", "FUSED_NORMAL_FULL"),        # wrong basis
        ({"bin-a": 0.8}, "fused_center_bootstrap_p05", "FUSED_CENTER_ONLY_NORMAL"),
    ],
)
def test_q2_replacement_without_bounds_yields_no_credential(q_lcb, q_lcb_basis, q_mode):
    bundle = _replacement_bundle(q_lcb=q_lcb, q_lcb_basis=q_lcb_basis, q_mode=q_mode)
    credential = _build_replacement_calibration_credential(
        replacement_bundle=bundle,
        q_mode=q_mode,
        coverage_verdict=_coverage_verdict("LICENSED"),
        family=_family(),
    )
    # No bounds leg -> NO credential stamped. The calibration builder then falls through to
    # the legacy Platt path -> IDENTITY_FALLBACK_NO_PLATT_BUCKET (proven separately in
    # test_event_reactor_no_bypass.test_missing_platt_bucket_uses_identity_fallback_authority).
    assert credential is None


# ---------------------------------------------------------------------------
# Q3 — replacement + bounds, no evaluated coverage verdict -> UNEVALUATED reject
#   (distinct). INSUFFICIENT_DATA is evaluated thin history and therefore takes the
#   conservative-bootstrap authority path tested above.
# ---------------------------------------------------------------------------
def test_q3_replacement_bounds_no_coverage_verdict_is_unevaluated_blocked():
    bundle = _replacement_bundle(
        q_lcb={"bin-a": 0.80},
        q_lcb_basis="fused_center_bootstrap_p05",
        q_mode="FUSED_NORMAL_PARTIAL",
    )
    credential = _build_replacement_calibration_credential(
        replacement_bundle=bundle,
        q_mode="FUSED_NORMAL_PARTIAL",
        coverage_verdict=None,  # no verdict at all
        family=_family(),
    )
    assert credential is not None  # bounds present -> credential built

    cal_payload, _clock = _render_payload(credential)
    assert cal_payload["authority"] == FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED_AUTHORITY

    # the live gate REJECTS it with the DISTINCT reason (not IDENTITY_FALLBACK)
    cert = SimpleNamespace(payload=cal_payload)
    with pytest.raises(ValueError, match="FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED"):
        _assert_event_bound_calibration_live_admitted(cert)

    # fail-closed: the UNEVALUATED authority is NOT in the approved set (evidence-only)
    assert FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED_AUTHORITY not in APPROVED_CALIBRATION_AUTHORITIES


# ---------------------------------------------------------------------------
# Q4 — legacy candidate, no Platt bucket -> IDENTITY_FALLBACK reject (unchanged)
# ---------------------------------------------------------------------------
def _empty_platt_models_conn():
    """A calibration conn with the platt_models table present but EMPTY — the exact
    condition under which the legacy path emits IDENTITY_FALLBACK (table exists, no row
    for the bucket). A missing table raises CALIBRATION_AUTHORITY_EVIDENCE_MISSING:store
    instead, which is a different (earlier) failure mode."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE platt_models ("
        " model_key TEXT PRIMARY KEY, temperature_metric TEXT, cluster TEXT, season TEXT,"
        " data_version TEXT, input_space TEXT, param_A REAL, param_B REAL, param_C REAL,"
        " bootstrap_params_json TEXT, n_samples INTEGER, brier_insample REAL, fitted_at TEXT,"
        " is_active INTEGER, authority TEXT, cycle TEXT, source_id TEXT, horizon_profile TEXT,"
        " recorded_at TEXT)"
    )
    return conn


def test_q4_legacy_no_platt_bucket_still_blocks_exactly_as_today():
    # No replacement credential on the payload, platt_models present but EMPTY -> the legacy
    # path emits IDENTITY_FALLBACK_NO_PLATT_BUCKET exactly as today (strictness (a)).
    payload: dict = {}
    forecast_payload = {
        "forecast_source_id": "tigge_mars",
        "source_issue_time": "2026-05-24T00:00:00+00:00",
        "horizon_profile": "full",
    }
    cal_payload, _clock = _calibration_authority_payload_and_clock(
        _empty_platt_models_conn(),
        event=SimpleNamespace(),
        family=SimpleNamespace(
            city=_CITY, metric=_METRIC, target_date=_TARGET_DATE,
        ),
        payload=payload,
        forecast_payload=forecast_payload,
        decision_time=DECISION_TIME,
    )
    assert cal_payload["authority"] == "IDENTITY_FALLBACK_NO_PLATT_BUCKET"

    cert = SimpleNamespace(payload=cal_payload)
    with pytest.raises(ValueError, match="IDENTITY_FALLBACK_NO_PLATT_BUCKET"):
        _assert_event_bound_calibration_live_admitted(cert)


# ---------------------------------------------------------------------------
# Strictness (a): the legacy gate behavior for IDENTITY_FALLBACK is untouched, and the
# new admitted authority does NOT bypass the empty-sample guard.
# ---------------------------------------------------------------------------
def test_strictness_fused_bootstrap_empty_sample_still_blocks():
    # An admitted FUSED_BOOTSTRAP authority with n_samples<=0 is still blocked by the
    # belt-and-braces empty-sample guard (the credential never mints n<=0 when LICENSED,
    # but the gate must not regress its own invariant).
    cert = SimpleNamespace(
        payload={"authority": FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY, "n_samples": 0}
    )
    with pytest.raises(ValueError, match="EDLI_LIVE_CALIBRATION_SAMPLE_FLOOR_BLOCKED"):
        _assert_event_bound_calibration_live_admitted(cert)


def test_fused_bootstrap_missing_coverage_status_blocks_even_with_samples():
    cert = SimpleNamespace(
        payload={
            "authority": FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY,
            "n_samples": 60,
        }
    )

    with pytest.raises(ValueError, match="EDLI_LIVE_CALIBRATION_COVERAGE_BLOCKED:missing"):
        _assert_event_bound_calibration_live_admitted(cert)


def test_fused_bootstrap_unknown_coverage_status_blocks_even_with_samples():
    cert = SimpleNamespace(
        payload={
            "authority": FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY,
            "coverage_status": "UNKNOWN",
            "n_samples": 60,
        }
    )

    with pytest.raises(ValueError, match="EDLI_LIVE_CALIBRATION_COVERAGE_BLOCKED:UNKNOWN"):
        _assert_event_bound_calibration_live_admitted(cert)


# ---------------------------------------------------------------------------
# The family coverage-verdict helper: flag-independent verdict read (mission part 1/2c).
# ---------------------------------------------------------------------------
def _settlement_conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE settlement_outcomes ("
        " city TEXT, temperature_metric TEXT,"
        " settlement_value REAL, settlement_unit TEXT)"
    )
    conn.executemany(
        "INSERT INTO settlement_outcomes (city, temperature_metric, settlement_value, settlement_unit)"
        " VALUES (?,?,?,?)",
        rows,
    )
    return conn


def test_family_coverage_verdict_insufficient_data_below_min_n():
    # 3 settled rows < min_n=30 -> INSUFFICIENT_DATA (a real verdict, not None).
    rows = [(_CITY, _METRIC, 70.0, "F")] * 3
    conn = _settlement_conn(rows)
    bin_obj = Bin(70, 71, "F", "70-71°F")
    candidate = SimpleNamespace(condition_id="cond-1", bin=bin_obj)
    family = SimpleNamespace(
        city=_CITY, metric=_METRIC, target_date=_TARGET_DATE, candidates=(candidate,)
    )
    from src.calibration.qlcb_provenance import QlcbByDirection, _set_qlcb_provenance

    lcb = QlcbByDirection()
    _set_qlcb_provenance(lcb, ("cond-1", "buy_yes"), 0.80, source="FORECAST_BOOTSTRAP")

    verdict = _replacement_family_coverage_verdict(
        family=family, forecast_conn=conn, lcb_by_direction=lcb
    )
    assert verdict is not None
    assert verdict.status == "INSUFFICIENT_DATA"
    # INSUFFICIENT_DATA is preserved for provenance and admits through the conservative
    # bootstrap q_lcb authority, not through settlement-coverage licensing.
    credential = _build_replacement_calibration_credential(
        replacement_bundle=_replacement_bundle(
            q_lcb={"bin-a": 0.80},
            q_lcb_basis="fused_center_bootstrap_p05",
            q_mode="FUSED_NORMAL_FULL",
        ),
        q_mode="FUSED_NORMAL_FULL",
        coverage_verdict=verdict,
        family=family,
    )
    cal_payload, _clock = _render_payload(credential)
    assert cal_payload["authority"] == FUSED_BOOTSTRAP_CONSERVATIVE_QLCB_AUTHORITY
    assert cal_payload["coverage_status"] == "INSUFFICIENT_DATA"
    _assert_event_bound_calibration_live_admitted(SimpleNamespace(payload=cal_payload))


def test_selected_leg_coverage_verdict_controls_replacement_credential(monkeypatch):
    """Coverage credentials are scoped to the selected bin+direction, not the family.

    The first candidate's buy_yes can be LICENSED while the selected buy_no leg is still
    thin. The selected buy_no must use FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB, not inherit
    the sibling YES settlement-coverage license.
    """

    from src.calibration.qlcb_provenance import QlcbByDirection, _set_qlcb_provenance
    from src.calibration.settlement_backward_coverage import CoverageObservation

    def fake_observations(*, direction, claimed_q_lcb, **_kwargs):
        if direction == "buy_yes":
            return [CoverageObservation(q_lcb=float(claimed_q_lcb), won=True) for _ in range(40)]
        if direction == "buy_no":
            return []
        raise AssertionError(direction)

    monkeypatch.setattr(adapter, "_settlement_coverage_observations", fake_observations)
    candidate = SimpleNamespace(condition_id="cond-1", bin=Bin(70, 71, "F", "70-71°F"))
    family = SimpleNamespace(
        city=_CITY, metric=_METRIC, target_date=_TARGET_DATE, candidates=(candidate,)
    )
    lcb = QlcbByDirection()
    _set_qlcb_provenance(lcb, ("cond-1", "buy_yes"), 0.80, source="FORECAST_BOOTSTRAP")
    _set_qlcb_provenance(lcb, ("cond-1", "buy_no"), 0.70, source="FORECAST_BOOTSTRAP")

    verdicts = adapter._maybe_apply_settlement_coverage_to_lcb(
        family=family,
        forecast_conn=sqlite3.connect(":memory:"),
        lcb_by_direction=lcb,
    )

    assert verdicts[("cond-1", "buy_yes")].status == "LICENSED"
    assert verdicts[("cond-1", "buy_no")].status == "INSUFFICIENT_DATA"
    bundle = _replacement_bundle(
        q_lcb={"bin-a": 0.80},
        q_lcb_basis="fused_center_bootstrap_p05",
        q_mode="FUSED_NORMAL_FULL",
    )
    yes_payload, _ = _render_payload(
        _build_replacement_calibration_credential(
            replacement_bundle=bundle,
            q_mode="FUSED_NORMAL_FULL",
            coverage_verdict=verdicts[("cond-1", "buy_yes")],
            family=family,
        )
    )
    no_payload, _ = _render_payload(
        _build_replacement_calibration_credential(
            replacement_bundle=bundle,
            q_mode="FUSED_NORMAL_FULL",
            coverage_verdict=verdicts[("cond-1", "buy_no")],
            family=family,
        )
    )

    assert yes_payload["authority"] == FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY
    assert no_payload["authority"] == FUSED_BOOTSTRAP_CONSERVATIVE_QLCB_AUTHORITY
    _assert_event_bound_calibration_live_admitted(SimpleNamespace(payload=no_payload))


def test_family_coverage_verdict_none_when_no_candidate_lcb():
    # No buy_yes lcb entry -> the helper cannot key the scope -> None (UNEVALUATED).
    conn = _settlement_conn([(_CITY, _METRIC, 70.0, "F")] * 40)
    candidate = SimpleNamespace(condition_id="cond-1", bin=Bin(70, 71, "F", "70-71°F"))
    family = SimpleNamespace(
        city=_CITY, metric=_METRIC, target_date=_TARGET_DATE, candidates=(candidate,)
    )
    from src.calibration.qlcb_provenance import QlcbByDirection

    verdict = _replacement_family_coverage_verdict(
        family=family, forecast_conn=conn, lcb_by_direction=QlcbByDirection()
    )
    assert verdict is None


# ---------------------------------------------------------------------------
# pr408 #1 CRITICAL: a STRUCTURAL coverage fault must NOT collapse into INSUFFICIENT_DATA.
# ---------------------------------------------------------------------------
class _SettlementQueryBoomConn:
    """A forecast_conn whose settlement_outcomes query EXECUTION raises (a read/schema fault).
    With claim history present, the verdict path reaches this query — a structural fault, not
    thin data."""

    def execute(self, *args, **kwargs):
        raise sqlite3.OperationalError("no such table: settlement_outcomes")


def _family_one_yes_lcb():
    bin_obj = Bin(70, 71, "F", "70-71°F")
    candidate = SimpleNamespace(condition_id="cond-1", bin=bin_obj)
    family = SimpleNamespace(
        city=_CITY, metric=_METRIC, target_date=_TARGET_DATE, candidates=(candidate,)
    )
    from src.calibration.qlcb_provenance import QlcbByDirection, _set_qlcb_provenance

    lcb = QlcbByDirection()
    _set_qlcb_provenance(lcb, ("cond-1", "buy_yes"), 0.80, source="FORECAST_BOOTSTRAP")
    return family, lcb


def test_structural_coverage_fault_raises_not_insufficient_data(monkeypatch):
    """A structural settlement read/schema fault raises
    QLCB_COVERAGE_AUTHORITY_FAULT (fail closed) — it must NOT degrade to INSUFFICIENT_DATA
    (which would mask a broken read as thin data and admit-by-default an unverified bound).
    RED-on-revert: swallowing the fault to None/INSUFFICIENT_DATA removes the raise."""
    # claim history present so the path REACHES the settlement query (not short-circuited thin)
    monkeypatch.setattr(adapter, "_per_day_claimed_qlcb_by_date", lambda **kw: {_TARGET_DATE: 0.80})
    family, lcb = _family_one_yes_lcb()
    with pytest.raises(ValueError, match=r"QLCB_COVERAGE_AUTHORITY_FAULT:settlement_query"):
        _replacement_family_coverage_verdict(
            family=family, forecast_conn=_SettlementQueryBoomConn(), lcb_by_direction=lcb
        )



# ---------------------------------------------------------------------------
# Q5 — v11 defect antibody: compiler carve-out was missing FUSED_BOOTSTRAP
# ---------------------------------------------------------------------------
def test_q5_compiler_admits_fused_bootstrap_maturity4():
    """Antibody for the v11 defect: compiler.py L582 carved out only IDENTITY_FALLBACK,
    not FUSED_BOOTSTRAP, so a LICENSED credential with maturity_level=4 raised
    'calibration.maturity_level too low' at compile time, before the verifier ran.

    Root cause: the FUSED credential sets maturity_level=4 (placeholder, not a real Platt
    maturity) and authority=FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE. The compiler's
    _validate_calibration_payload check required authority == IDENTITY_FALLBACK only.
    Fix: extend the carve-out to include FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY.

    This test calls the compiler's _validate_calibration_payload directly to pin the
    invariant: FUSED_BOOTSTRAP authority with maturity=4 is NOT rejected by the compiler."""
    from src.decision_kernel.compiler import _validate_calibration_payload  # type: ignore[attr-defined]

    calibration = {
        "authority": FUSED_BOOTSTRAP_CALIBRATION_AUTHORITY,
        "maturity_level": 4,
        "input_space": "fused_center_bootstrap_lcb",
        "horizon_profile": "full",
        "training_cutoff": "2026-05-24T18:10:00+00:00",
        "model_available_at": "2026-05-24T18:10:00+00:00",
        "model_hash": "abc123",
        "n_samples": 60,
    }
    model_config = {
        "calibrator_model_key": calibration.get("calibrator_model_key"),
        "calibrator_model_hash": calibration.get("model_hash"),
        "calibration_input_space": "fused_center_bootstrap_lcb",
    }
    forecast = {
        "horizon_profile": "full",
    }
    # must NOT raise — this was the v11 defect (raises "calibration.maturity_level too low")
    _validate_calibration_payload(
        calibration, model_config, forecast, decision_time=DECISION_TIME
    )


def test_q5_unevaluated_rejected_before_maturity_check():
    """The UNEVALUATED sibling is NOT in APPROVED_CALIBRATION_AUTHORITIES, so the compiler
    rejects it at the authority check ('calibration.authority is not approved') — NOT with
    the maturity check. This ensures the error ordering is deterministic: unknown authority
    fails at the authority gate, not the maturity gate."""
    from src.decision_kernel.compiler import _validate_calibration_payload  # type: ignore[attr-defined]

    calibration = {
        "authority": FUSED_BOOTSTRAP_COVERAGE_UNEVALUATED_AUTHORITY,
        "maturity_level": 4,
        "input_space": "fused_center_bootstrap_lcb",
        "horizon_profile": "full",
        "training_cutoff": "2026-05-24T18:10:00+00:00",
        "model_available_at": "2026-05-24T18:10:00+00:00",
        "model_hash": "abc123",
        "n_samples": 0,
    }
    model_config = {"calibration_input_space": "fused_center_bootstrap_lcb"}
    forecast = {"horizon_profile": "full"}
    with pytest.raises(ValueError, match="calibration.authority is not approved"):
        _validate_calibration_payload(
            calibration, model_config, forecast, decision_time=DECISION_TIME
        )
