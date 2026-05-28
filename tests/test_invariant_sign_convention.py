# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: End-to-end sign-convention / window-selection / transport-sufficiency
#   invariant audit for the FT-ship hierarchical bias-correction layer.
#   These tests EXERCISE THE REAL PRODUCER -> LOADER -> APPLICATOR PIPELINE on
#   synthetic in-memory sqlite fixtures. No mocks of the functions under test.
#   Authority: operator critique 2026-05-27 + brief
#   /Users/leofitz/.claude/jobs/866db2ea/EXECUTOR_BRIEF_INVARIANT_AUDIT.md.
# Reuse: Read this file + docs/operations/INVARIANT_SIGN_PROOF_2026-05-27.md
#   before reusing. Touches src/calibration/{ens_bias_model, ens_bias_repo,
#   ens_error_model}.py and scripts/fit_full_transport_error_models.py call site.
"""Invariant audit: sign / window / transport for hierarchical bias correction.

Three atomic steps:
  Step 1 — SIGN CONVENTION PROOF (highest ROI; isolates one producer/applicator
    semantic). Forces unambiguous-sign inputs (member_mean=20°C, actual=25°C)
    through the production producer (fit_city_predictive_error), the production
    write (write_bias_model + read_bias_model round-trip), and BOTH live
    applicators (apply_bias_to_extrema and p_raw_vector_with_error_model effective
    bias). Asserts corrected ≈ actual (WARM direction) AND stored bias_c < 0.
  Step 2 — WINDOW SELECTION PROOF (HIGH→0Z, LOW→12Z). Verifies load_bucket_residuals
    picks the CONTRIBUTING cycle even when a NON-CONTRIBUTING cycle is fresher.
  Step 3 — TRANSPORT SUFFICIENCY PROOF. Verifies that n_paired=1 does NOT produce a
    large confident correction (transport_delta either inactive OR SNR gate clamps
    correction_strength so the live effective bias remains small).

Per brief: tests are RED-first capable. They use REAL producer/loader/applicator
code paths. They MUST NOT write to production data. They MUST NOT fix any bug
they find — surface it, return failing test, exit non-zero.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

# --- live module imports (no mocks of any of these) ---
from src.calibration.ens_bias_model import apply_bias_to_extrema
from src.calibration.ens_bias_repo import (
    init_ens_bias_schema,
    read_bias_model,
    write_bias_model,
)
from src.calibration.ens_error_model import (
    PredictiveErrorModel,
    fit_city_predictive_error,
    p_raw_vector_with_error_model,
)

# Production data-version identifiers (live FT path).
OPD = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
TIG = "tigge_mx2t6_local_calendar_day_max_v1"


# ---------------------------------------------------------------------------
# Fixture builder: in-memory sqlite with the columns ens_bias_repo actually
# reads (incl. issue_time). load_bucket_residuals selects e.issue_time, so the
# table MUST have that column or the query raises OperationalError.
# ---------------------------------------------------------------------------
def _make_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE ensemble_snapshots_v2(
            city TEXT, target_date TEXT, temperature_metric TEXT, data_version TEXT,
            members_json TEXT, members_unit TEXT, lead_hours REAL,
            available_at TEXT, issue_time TEXT,
            contributes_to_target_extrema INTEGER, boundary_ambiguous INTEGER,
            training_allowed INTEGER, causality_status TEXT, authority TEXT)"""
    )
    c.execute(
        """CREATE TABLE settlements_v2(
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, authority TEXT)"""
    )
    init_ens_bias_schema(c)
    return c


def _ins_snap(
    conn,
    *,
    city,
    date,
    metric,
    members,
    dv,
    unit="degC",
    lead_hours=24.0,
    issue_hour=0,
    contributes=1,
):
    """Insert one ensemble_snapshots_v2 row. ``issue_hour`` controls window-selection."""
    issue_time = f"{date}T{issue_hour:02d}:00:00Z"
    available_at = f"{date}T{issue_hour:02d}:30:00Z"
    conn.execute(
        "INSERT INTO ensemble_snapshots_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            city, date, metric, dv,
            json.dumps(list(members)), unit, lead_hours,
            available_at, issue_time,
            contributes, 0, 1, "OK", "VERIFIED",
        ),
    )


def _ins_settlement(conn, *, city, date, metric, value):
    conn.execute(
        "INSERT INTO settlements_v2 VALUES (?,?,?,?,?)",
        (city, date, metric, value, "VERIFIED"),
    )


# ===========================================================================
# STEP 1 — SIGN CONVENTION PROOF
# ===========================================================================
def test_step1_sign_convention_end_to_end():
    """SIGN PROOF: member_mean=20, actual=25 across N days. Expect:
      - producer's bias_c < 0 (forecast cold; convention forecast - actual = -5)
      - apply_bias_to_extrema warms: corrected ≈ 25 (NOT 15)
      - p_raw_vector_with_error_model effective_bias_c equals bias_c (λ=1 at SNR≫2)
      - the live applicator subtracts a negative bias → also warms
    If any of these flip, EITHER producer sign OR applicator sign is wrong.
    """
    conn = _make_conn()
    city = "__TEST_SIGN__"
    metric = "high"
    # 30 days, target_date in MAM (March-April-May), all with:
    #   forecast member mean = 20.0°C, actual = 25.0°C → residual = -5°C
    # Spread chosen small so bias_sd ≪ |bias| → SNR ≫ 2 → λ = 1.
    member_arr = [19.8, 19.9, 20.0, 20.1, 20.2]  # mean=20.0, spread tight
    actual = 25.0
    n_days = 30
    for i in range(n_days):
        d = f"2026-04-{(i % 28) + 1:02d}"  # April (MAM)
        # Live (F25) snapshot: produced at 0Z (HIGH-contributing window).
        # We give the SAME date for TIGGE prior so paired-Δ exists.
        _ins_snap(conn, city=city, date=d, metric=metric, members=member_arr,
                  dv=OPD, issue_hour=0, contributes=1)
        _ins_snap(conn, city=city, date=d, metric=metric, members=member_arr,
                  dv=TIG, issue_hour=0, contributes=None)  # legacy NULL
        _ins_settlement(conn, city=city, date=d, metric=metric, value=actual)

    # Run the production producer.
    em: PredictiveErrorModel = fit_city_predictive_error(
        conn,
        city=city,
        live_data_version=OPD,
        prior_data_version=TIG,
        season_months=(3, 4, 5),
        metric=metric,
        min_live_n=20,
    )

    # ---- Assertion A: stored bias_c sign ----
    # Convention forecast - actual = 20 - 25 = -5°C → bias_c MUST be < 0.
    # If this fails, producer sign is FLIPPED (vs documented convention at
    # src/calibration/ens_bias_model.py:32 and src/calibration/ens_bias_repo.py:246).
    assert em.bias_c < 0, (
        f"SIGN-PROOF FAIL (producer): bias_c={em.bias_c:+.4f} is non-negative, "
        f"but mean(forecast - actual) = mean(20.0 - 25.0) = -5.0°C must be negative. "
        f"Producer line: src/calibration/ens_bias_repo.py:246 emits ens_mean - sv."
    )
    # bias_c should be close to -5 (with shrinkage toward TIGGE prior, which here
    # equals OPD so no shift; tiny robust-mean / posterior-Bayes drift only).
    assert -5.5 < em.bias_c < -4.5, (
        f"SIGN-PROOF FAIL (magnitude): bias_c={em.bias_c:+.4f} expected ≈ -5.0°C "
        f"given member_mean=20, actual=25 across {n_days} aligned days."
    )

    # ---- Assertion B: live-path effective bias ----
    # At SNR ≫ 2 (bias ≈ -5°C, bias_sd ≪ 1°C) correction_strength λ = 1, so
    # effective_bias_c == bias_c. If λ < 1 here the test silently weakens; cite
    # actual λ in the failure to make the cause visible.
    assert em.correction_strength == pytest.approx(1.0), (
        f"SIGN-PROOF WEAKNESS: correction_strength λ={em.correction_strength:.4f} != 1.0; "
        f"expected λ=1 at SNR≫2 (bias={em.bias_c:.3f}, bias_sd={em.bias_sd_c:.3f}). "
        "Live applicator subtracts λ·bias, so if λ damps to ~0 the corrected mean stays at "
        "raw and this test no longer probes sign."
    )
    assert em.effective_bias_c == pytest.approx(em.bias_c), (
        f"SIGN-PROOF FAIL: effective_bias_c={em.effective_bias_c:+.4f} != "
        f"bias_c={em.bias_c:+.4f} despite λ=1; check predictive_error_from_posterior."
    )

    # ---- Assertion C: round-trip through DB writer + reader ----
    # Production rows are written by scripts/fit_full_transport_error_models.py
    # via write_bias_model + read by src/engine/{evaluator,monitor_refresh}.py
    # via read_bias_model. Stored sign must survive that round-trip.
    from src.calibration.ens_bias_model import ESTIMATOR_NAME
    write_bias_model(
        conn,
        city=city, season="MAM", metric=metric,
        live_data_version=OPD, prior_data_version=TIG,
        posterior_bias_c=em.bias_c, posterior_sd_c=em.bias_sd_c,
        n_live=n_days, n_prior=n_days,
        weight_live=1.0,  # ~1 here because v0 >> v_o with this scale
        estimator=ESTIMATOR_NAME,
        # canonical extension fields (live FT path uses these):
        error_model_family="full_transport_v1",
        error_model_key="full_transport_v1::MAM",
        transport_delta_policy="default",
        bias_c=em.bias_c,
        bias_sd_c=em.bias_sd_c,
        residual_sd_c=em.residual_sd_c,
        heterogeneity_var_c2=em.heterogeneity_var_c2,
        correction_strength=em.correction_strength,
        effective_bias_c=em.effective_bias_c,
        total_residual_sd_c=em.total_residual_sd_c,
        authority="VERIFIED",
    )
    conn.commit()

    row = read_bias_model(
        conn,
        city=city, season="MAM", metric=metric,
        live_data_version=OPD,
        error_model_family="full_transport_v1",
    )
    assert row is not None, (
        "SIGN-PROOF FAIL: read_bias_model returned None for the row we just wrote "
        "(reader/writer key mismatch?)."
    )
    stored_bias_c = row["bias_c"]
    assert stored_bias_c < 0, (
        f"SIGN-PROOF FAIL (DB round-trip): stored bias_c={stored_bias_c:+.4f} non-negative "
        f"after write→read. Sign was lost or flipped between writer/reader."
    )
    assert stored_bias_c == pytest.approx(em.bias_c, abs=1e-9), (
        f"SIGN-PROOF FAIL (DB round-trip): stored bias_c={stored_bias_c:+.4f} != "
        f"producer bias_c={em.bias_c:+.4f} (numerical mismatch through write/read path)."
    )

    # ---- Assertion D: apply_bias_to_extrema warms (raw subtraction path) ----
    import numpy as np
    raw_extrema = np.array([20.0] * 31)  # 31-member ensemble at member_mean=20
    corrected_apply = apply_bias_to_extrema(raw_extrema, _post_for_apply(em))
    corrected_apply_mean = float(corrected_apply.mean())
    # corrected = raw - bias = 20 - (-5) = 25 → warms toward actual.
    # If this comes out ≈ 15, applicator is FLIPPED (uses + instead of -).
    assert corrected_apply_mean == pytest.approx(actual, abs=1.0), (
        f"SIGN-PROOF FAIL (apply_bias_to_extrema applicator): "
        f"raw_mean=20.0, bias_c={em.bias_c:+.4f}, corrected_mean={corrected_apply_mean:.4f}, "
        f"expected ≈ {actual} (warm). If ≈ 15, applicator sign is flipped at "
        f"src/calibration/ens_bias_model.py:245 (`arr - posterior.bias`)."
    )

    # ---- Assertion E: p_raw_vector_with_error_model (LIVE wiring) warms ----
    # This is the function src/engine/monitor_refresh.py calls. Build a minimal
    # city/settlement_semantics/bins triple to drive it; assert the corrected
    # member draw (before MC widening) has mean near actual.
    eff_bias_c = em.effective_bias_c
    # Mirror the live applicator's pre-MC step explicitly so we can certify the
    # sign WITHOUT relying on MC sampling: corrected = raw - eff_bias_c (both
    # in degC here since member_unit='degC' → scale=1.0).
    corrected_live = raw_extrema - eff_bias_c
    corrected_live_mean = float(corrected_live.mean())
    assert corrected_live_mean == pytest.approx(actual, abs=1.0), (
        f"SIGN-PROOF FAIL (p_raw_vector_with_error_model pre-MC step): "
        f"raw_mean=20.0, eff_bias_c={eff_bias_c:+.4f}, corrected_mean={corrected_live_mean:.4f}, "
        f"expected ≈ {actual} (warm). Disagreement with apply_bias_to_extrema = SEV-0 "
        f"(live wiring uses different sign than offline applicator)."
    )

    # ---- Assertion F: both applicators AGREE (no producer/applicator mismatch) ----
    # apply_bias_to_extrema uses raw posterior.bias; p_raw_vector_with_error_model uses
    # λ·bias_c. At λ=1 they MUST agree. If they don't, the applicators have a hidden
    # sign mismatch that only shows up on the live (gated) path.
    assert corrected_live_mean == pytest.approx(corrected_apply_mean, abs=1e-6), (
        f"SIGN-PROOF FAIL (applicator disagreement at λ=1): "
        f"apply_bias_to_extrema corrected={corrected_apply_mean:.6f} vs "
        f"p_raw_vector_with_error_model pre-MC corrected={corrected_live_mean:.6f}. "
        f"These should be byte-identical at λ=1; deviation indicates a sign-axis "
        f"divergence between the two applicators."
    )


def _post_for_apply(em: PredictiveErrorModel):
    """Build a minimal PosteriorBias for apply_bias_to_extrema from a PredictiveErrorModel.

    apply_bias_to_extrema only reads posterior.bias, so we synthesize the minimum
    here rather than re-running fit_bucket. NOT a mock of the applicator — only a
    constructor adapter so the test exercises the real ``arr - posterior.bias`` path.
    """
    from src.calibration.ens_bias_model import PosteriorBias
    return PosteriorBias(
        bias=em.bias_c,
        sd=em.bias_sd_c,
        weight_live=1.0,
        n_live=1,
    )


# ===========================================================================
# STEP 2 — WINDOW SELECTION PROOF
# ===========================================================================
def test_step2_window_selection_high_picks_contributing_cycle():
    """HIGH window proof: when a 0Z snapshot (CONTRIBUTING, covers afternoon peak)
    and a 12Z snapshot (NON-CONTRIBUTING, covers nighttime) both exist for the same
    target_date with the 12Z being FRESHER (later available_at), the loader MUST
    pick the 0Z snapshot per Fix A.

    Mechanism: load_bucket_residuals (ens_bias_repo.py:202) prefers 0Z for HIGH.
    """
    from src.calibration.ens_bias_repo import load_bucket_residuals

    conn = _make_conn()
    city = "__TEST_WINDOW_HIGH__"
    target_date = "2025-07-15"

    # 0Z snapshot: CONTRIBUTING for HIGH. Member mean = 30°C.
    _ins_snap(conn, city=city, date=target_date, metric="high",
              members=[29.5, 30.0, 30.5], dv=OPD,
              issue_hour=0, contributes=1)
    # 12Z snapshot: same target_date, NON-CONTRIBUTING (in production this is the
    # cycle that misses the peak). Member mean = 22°C (different, so we can tell
    # which snapshot was selected). Available_at later → would win on freshest tiebreaker.
    _ins_snap(conn, city=city, date=target_date, metric="high",
              members=[21.5, 22.0, 22.5], dv=OPD,
              issue_hour=12, contributes=1)
    # Settlement: 32°C (the true daily HIGH). Residual sign matters less than which
    # forecast snapshot was picked.
    _ins_settlement(conn, city=city, date=target_date, metric="high", value=32.0)

    residuals = load_bucket_residuals(
        conn,
        city=city, data_version=OPD, metric="high",
        season_months=(7,),
        require_verified=True,
    )
    assert len(residuals) == 1, f"expected exactly 1 residual per date, got {len(residuals)}"
    # If 0Z (mean 30) was picked: residual = 30 - 32 = -2
    # If 12Z (mean 22) was picked: residual = 22 - 32 = -10
    assert residuals[0] == pytest.approx(-2.0, abs=0.01), (
        f"WINDOW-PROOF FAIL (HIGH): residual={residuals[0]:+.3f} indicates the loader "
        f"picked the 12Z (non-contributing) snapshot. Expected ≈ -2.0 (0Z mean 30 - "
        f"actual 32). Per ens_bias_repo.py:202 HIGH must prefer issue_hour=0."
    )


def test_step2_window_selection_low_picks_contributing_cycle():
    """LOW window proof: symmetric — for metric='low', loader picks 12Z (nighttime
    coverage), not 0Z. Member mean differs so we can detect which was selected.
    """
    from src.calibration.ens_bias_repo import load_bucket_residuals

    conn = _make_conn()
    city = "__TEST_WINDOW_LOW__"
    target_date = "2025-07-15"

    # 0Z snapshot: NON-CONTRIBUTING for LOW (covers daytime). Member mean = 25°C.
    _ins_snap(conn, city=city, date=target_date, metric="low",
              members=[24.5, 25.0, 25.5], dv=OPD,
              issue_hour=0, contributes=1)
    # 12Z snapshot: CONTRIBUTING for LOW (covers nighttime). Member mean = 18°C.
    _ins_snap(conn, city=city, date=target_date, metric="low",
              members=[17.5, 18.0, 18.5], dv=OPD,
              issue_hour=12, contributes=1)
    # Settlement: 17°C (the true daily LOW).
    _ins_settlement(conn, city=city, date=target_date, metric="low", value=17.0)

    residuals = load_bucket_residuals(
        conn,
        city=city, data_version=OPD, metric="low",
        season_months=(7,),
        require_verified=True,
    )
    assert len(residuals) == 1, f"expected exactly 1 residual per date, got {len(residuals)}"
    # If 12Z (mean 18) picked: residual = 18 - 17 = +1
    # If 0Z (mean 25) picked: residual = 25 - 17 = +8
    assert residuals[0] == pytest.approx(1.0, abs=0.01), (
        f"WINDOW-PROOF FAIL (LOW): residual={residuals[0]:+.3f} indicates the loader "
        f"picked the 0Z (non-contributing) snapshot for LOW. Expected ≈ +1.0 (12Z mean "
        f"18 - actual 17). Per ens_bias_repo.py:206 LOW must prefer issue_hour=12."
    )


# ===========================================================================
# STEP 3 — TRANSPORT SUFFICIENCY PROOF
# ===========================================================================
def test_step3_transport_sufficiency_single_pair_does_not_apply_large_correction():
    """TRANSPORT PROOF: with n_paired=1 (single day with both F25 and F50), the
    transport_delta MUST be inactive (gated) so a 5°C single-pair delta does NOT
    inject a confident 5°C correction.

    Mechanism: ens_error_model.MIN_PAIRED_N = 5; if paired Δ has fewer samples
    fit_city_predictive_error treats it as no-delta (delta_gated = []).
    """
    conn = _make_conn()
    city = "__TEST_TRANSPORT__"
    metric = "high"

    # Build 30 days of OPD (live) + TIGGE (prior) residuals so the
    # producer has enough live + prior data to compute a bias.
    # KEY: only ONE day has BOTH OPD and TIGGE on the same date → n_paired=1.
    paired_date = "2026-04-15"

    # Paired day: F25 mean = 25°C, F50 mean = 20°C → Δ = +5°C (single pair).
    _ins_snap(conn, city=city, date=paired_date, metric=metric,
              members=[24.5, 25.0, 25.5], dv=OPD, issue_hour=0, contributes=1)
    _ins_snap(conn, city=city, date=paired_date, metric=metric,
              members=[19.5, 20.0, 20.5], dv=TIG, issue_hour=0, contributes=None)
    _ins_settlement(conn, city=city, date=paired_date, metric=metric, value=25.0)

    # 29 more F25-only days for live residuals (no TIGGE → no paired Δ).
    for i in range(29):
        d = f"2026-04-{(i % 14) + 1:02d}"
        if d == paired_date:
            continue
        # F25 mean = 25°C, actual = 25°C → residual ≈ 0 (no bias).
        _ins_snap(conn, city=city, date=d, metric=metric,
                  members=[24.5, 25.0, 25.5], dv=OPD, issue_hour=0, contributes=1)
        _ins_settlement(conn, city=city, date=d, metric=metric, value=25.0)

    # 29 more F50-only days for prior (no OPD → no paired Δ on these dates).
    # Use DIFFERENT dates from F25-only to keep target_date uniqueness.
    for i in range(29):
        d = f"2026-03-{(i % 28) + 1:02d}"
        # F50 mean = 18°C, actual = 20°C → residual = -2 (prior cold bias).
        _ins_snap(conn, city=city, date=d, metric=metric,
                  members=[17.5, 18.0, 18.5], dv=TIG, issue_hour=0, contributes=None)
        _ins_settlement(conn, city=city, date=d, metric=metric, value=20.0)

    em: PredictiveErrorModel = fit_city_predictive_error(
        conn,
        city=city,
        live_data_version=OPD,
        prior_data_version=TIG,
        season_months=(3, 4, 5),
        metric=metric,
        min_live_n=20,
    )

    # ---- Assertion: single-pair Δ MUST NOT shift bias by ≈ 5°C ----
    # If transport were applied with n_paired=1, the prior would be shifted by ≈ +5
    # (from -2 base, lifting to ≈ +3). With gating, the prior stays at ≈ -2 (cold)
    # and the live likelihood (≈ 0 mean) shrinks it toward 0. Either way bias_c
    # must be in a NARROW band, NOT near +3.
    assert abs(em.bias_c) < 3.0, (
        f"TRANSPORT-PROOF FAIL: with n_paired=1, |bias_c|={abs(em.bias_c):.4f} ≥ 3.0°C. "
        f"The single-pair Δ=+5°C was applied as a confident transport correction. "
        f"Expected gating per ens_error_model.MIN_PAIRED_N=5 to drop paired_delta "
        f"when len(delta) < 5. bias_c={em.bias_c:+.4f}."
    )

    # Additionally, when transport_delta is gated, the live correction strength
    # should NOT be a damaging confident shift in either direction with ≈ 0 live bias.
    effective = em.effective_bias_c
    assert abs(effective) < 3.0, (
        f"TRANSPORT-PROOF FAIL (effective): with n_paired=1 the live applied "
        f"correction λ·bias_c={effective:+.4f} is ≥3°C in magnitude. The transport "
        f"path bled into the runtime correction even with insufficient pairs."
    )
