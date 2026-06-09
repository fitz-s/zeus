# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# 2026-06-07 ITEM 1+2 (settlement-validated): added the STRUCTURAL over-correction GUARD
#   (bound_eb_bias_shift: reliability-shrink x magnitude-cap + stability gate) applied to the
#   served effective_bias_c (a RAW per-city mean at full magnitude — write_promoted_edli_bias
#   .py:73,95), so an implausible bias (Tokyo -4.847C/n=7) is tempered toward 0 while a stable
#   moderate bias is retained, and NO city can ever receive an over-correction (only-more-
#   conservative). Added the anti-lookahead SELF-GATE: the resolver serves a row only when its
#   training_cutoff is STRICTLY BEFORE target_date (no external gate). Both gated under the
#   SAME flag (replacement_0_1_eb_bias_correction_enabled, default OFF).
# Authority basis: docs/the_path/P2_BLEND.md §3 (per-city walk-forward Empirical-Bayes
#   bias-correction of the replacement_0_1 center, applied BEFORE the soft_anchor
#   zero-prior veto), §4 (reuse zeus-world.model_bias_ens; flag-gated default-OFF;
#   fail-closed on no VERIFIED row), §5 (layering: evidence gate -> bias-correction
#   (CENTER) -> q_lcb floor (SIGMA)). REUSE: src/calibration/ens_bias_repo.read_bias_model.
"""Per-city EB bias-shift resolver for the replacement_0_1 (AIFS soft-anchor) path.

The replacement_0_1 forecast (AIFS sampled-2t member votes + Open-Meteo ECMWF-IFS 0.1deg
deterministic soft-anchor) runs ~-1.0C cold (P2_BLEND.md §1c). This module resolves the
per-city VERIFIED promoted bias from the ALREADY-BUILT ``zeus-world.model_bias_ens`` table
(ONE-BUILDER: it does NOT refit a parallel store) and returns the degC center shift the
construction applies BEFORE the zero-prior veto. ``corrected = raw - bias`` (bias =
forecast - actual) so a cold (negative) bias warms the cloud.

UNIT SAFETY (P2_BLEND.md + Fitz Constraint #4):
  model_bias_ens.effective_bias_c is degC. The replacement_0_1 soft-anchor cells (AIFS
  member high_c/low_c, the OM9 anchor high_c/low_c) are ALWAYS degC — every value is
  unit-normalized to degC on ingest, regardless of the city's settlement unit. So the
  shift applied to those degC cells is degC-vs-degC and unit-correct WITHOUT any
  Fahrenheit ×1.8. The ×1.8 lives ONLY in the legacy edli p_raw path
  (event_reactor_adapter._maybe_apply_edli_bias_correction), where the member array
  carries the city's SETTLEMENT unit (degF for SF/Seattle/Atlanta/Austin). Applying
  ×1.8 here would be the exact F/C unit-mix contamination this path must NOT introduce.
  ``resolve_replacement_eb_bias_shift_c`` takes the city ``settlement_unit`` only to
  ASSERT the cell domain is degC and to keep the call site honest; it never scales by 1.8.

NO-DOUBLE-CORRECTION (P2_BLEND.md §4):
  This path REUSES the SAME error_model_family='edli_per_city_v1' as the legacy edli path
  (ONE-BUILDER: one promoted-bias store, not a duplicate). The no-double-correction
  guarantee is therefore STRUCTURAL, not family-keying: the replacement_0_1 construction
  (replacement_forecast_materializer) and the legacy edli p_raw construction
  (event_reactor_adapter._snapshot_p_raw) are SEPARATE, MUTUALLY-EXCLUSIVE construction
  surfaces — _live_yes_probabilities returns the replacement_0_1 posterior BEFORE the
  legacy path runs, so the same promoted bias is subtracted exactly once per forecast.
  (Do NOT assume distinct families isolate them — they share the family; only the
  surface-exclusivity does.)

FAIL-CLOSED: any missing flag/row/field, non-VERIFIED authority, weight_live<=0, or
exception returns ``None`` (NO correction, construction proceeds with raw inputs).
"""

from __future__ import annotations

import logging
import math

# Error-model family for the replacement_0_1 soft-anchor EB bias. INTENTIONALLY the SAME
# family as the legacy edli path ('edli_per_city_v1') — ONE-BUILDER reuse of the single
# promoted-bias store (the 67 HIGH + 7 LOW VERIFIED rows live here). No-double-correction
# is guaranteed STRUCTURALLY by mutually-exclusive construction surfaces (replacement_0_1
# returns before the legacy edli path in _live_yes_probabilities), NOT by family-keying.
REPLACEMENT_EB_BIAS_FAMILY = "edli_per_city_v1"

_LOG = logging.getLogger("zeus.replacement_eb_bias")


# ===========================================================================
# STRUCTURAL OVER-CORRECTION GUARD (ITEM 1, 2026-06-07)
# ===========================================================================
# WHY a guard, and WHY this form:
#   The value the resolver serves (model_bias_ens.effective_bias_c) is, for the
#   edli_per_city_v1 rows this path reads, a RAW per-city mean of (forecast - actual)
#   applied at FULL magnitude — NOT an EB posterior shrunk toward a prior.
#   (scripts/write_promoted_edli_bias.py:73,95: effective_bias_c = eff = errs.mean();
#    correction_strength = 1.0; weight_live = 1.0. The resolver ignores correction_strength
#    and serves effective_bias_c directly. Verified against zeus-world.db: Tokyo JJA6
#    effective_bias_c == bias_c == -4.847.)
#   On small n, that raw mean inflates: Tokyo JJA6 = -4.847C on n=7 (settlement-validated
#   OVER-correction — anchor 20.0C==truth got promoted to a 26C miss). Crucially the defect
#   is MAGNITUDE, not significance: |b|/SE = 4.847/(1.757/sqrt(7)) = 7.3, so a pure
#   "distinguishable-from-0" stability gate would PASS Tokyo. The load-bearing component is
#   therefore a magnitude CAP, layered with a reliability SHRINK (small-n tempering) and a
#   stability GATE (zero a sub-SE bias).
#
# This is NOT a parallel mechanism: it reuses the SAME inputs already in the row
#   (n_live, residual_sd_c) and the SAME notion the existing bias_decay_kelly_haircut /
#   bias_treatment_v2 layers embody — an untrustworthy bias must be TEMPERED, not trusted at
#   face value (ONE-BUILDER). It is a STRUCTURAL bounded transform: NO city, present or
#   future, can receive a shift larger in magnitude than its raw bias, and none can exceed
#   the absolute ceiling. The guard can ONLY make the correction more conservative.
#
# Form:  applied = sign(b) * min(|b| * n/(n+KAPPA),  min(C_ABS, K_SD * residual_sd))
#        with a stability gate: |b| <= Z_STABLE * SE  =>  0.0   (SE = residual_sd/sqrt(n)).
#
# Constants (frozen; chosen against the settlement-validated cohort 2026-06-07):
#   KAPPA   reliability-shrink pseudo-count. b*n/(n+KAPPA): at n=7, KAPPA=6 keeps 7/13≈54%;
#           at n=200 keeps ~97%. Tempers small-n inflation, near-passthrough for large n.
#   C_ABS   hard absolute ceiling (degC). A day-ahead 2m-temp bias beyond this is
#           implausible for a stable city; caps the Tokyo artifact at the principled limit.
#   K_SD    scale-relative ceiling: a trustworthy bias cannot exceed K_SD residual SDs.
#           A row whose residual scale collapses (sd→0) gets cap→0 (untrustworthy scale).
#   Z_STABLE  stability-gate z: |b| must exceed Z_STABLE standard errors to be served.
GUARD_KAPPA: float = 6.0
GUARD_C_ABS_C: float = 2.5
GUARD_K_SD: float = 1.5
GUARD_Z_STABLE: float = 1.0


def bound_eb_bias_shift(bias_c: float, n: int, residual_sd_c: float) -> float:
    """Structurally bound a served EB bias shift so it can only be MORE conservative.

    Reliability-shrink × magnitude-cap with a stability gate. Returns a degC shift whose
    magnitude is NEVER larger than ``|bias_c|`` and never larger than the absolute /
    scale-relative ceiling, and whose sign is never flipped. A statistically
    indistinguishable bias (|bias| <= Z·SE) or a degenerate input returns 0.0.

    Parameters
    ----------
    bias_c : the RAW served effective_bias_c (degC, sign = forecast - actual).
    n      : the n_live behind the bias (reliability pseudo-count denominator term).
    residual_sd_c : the per-city residual scale (degC) from the same row; drives BOTH the
        stability gate's standard error AND the scale-relative cap.

    Fail-safe: n<=0, non-finite bias/sd, or sd<=0 -> 0.0 (cannot establish reliability or a
    trustworthy scale -> NO correction). This keeps the guard "more conservative, never
    larger" on every degenerate path.
    """
    # Degenerate / non-finite inputs cannot support a trustworthy correction -> abstain.
    if not (math.isfinite(bias_c) and math.isfinite(residual_sd_c)):
        return 0.0
    if n <= 0 or residual_sd_c <= 0.0:
        return 0.0
    if bias_c == 0.0:
        return 0.0

    se = residual_sd_c / math.sqrt(n)
    # Stability gate: a bias smaller than its own standard error is noise -> zero it.
    if abs(bias_c) <= GUARD_Z_STABLE * se:
        return 0.0

    # Reliability shrink: small n -> stronger pull toward 0; large n -> near passthrough.
    shrunk = abs(bias_c) * (n / (n + GUARD_KAPPA))
    # Magnitude cap: the tighter of the absolute ceiling and the scale-relative ceiling.
    cap = min(GUARD_C_ABS_C, GUARD_K_SD * residual_sd_c)
    bounded = min(shrunk, cap)
    return math.copysign(bounded, bias_c)


def _training_cutoff_is_causal(training_cutoff, target_date: str | None) -> bool:
    """True iff the row's ``training_cutoff`` is STRICTLY BEFORE ``target_date``.

    Anti-lookahead self-gate (ITEM 2). The resolver does NOT rely on an external gate: a
    row whose training window includes data on/after the target day would leak future
    information into a live decision. Compares on the DATE portion (first 10 chars) so it
    is robust to both a bare ``YYYY-MM-DD`` cutoff and an ISO datetime cutoff
    (``YYYY-MM-DDThh:mm:ss+00:00``); both real producers are present in the store.

    FAIL-CLOSED: a NULL/empty/short cutoff cannot PROVE causality -> not causal (no
    correction). When ``target_date`` is None the caller has opted out of the gate (legacy
    signature) and this returns True (the caller is then responsible for causality).
    """
    if target_date is None:
        return True  # caller opted out of the self-gate (backward-compatible signature)
    tc = str(training_cutoff or "").strip()
    td = str(target_date or "").strip()
    if len(tc) < 10 or len(td) < 10:
        return False  # cannot establish a date ordering -> fail closed
    return tc[:10] < td[:10]


def resolve_replacement_eb_bias_shift_c(
    conn,
    *,
    city: str,
    season: str,
    month: int,
    metric: str,
    live_data_version: str,
    settlement_unit: str = "C",
    target_date: str | None = None,
    error_model_family: str = REPLACEMENT_EB_BIAS_FAMILY,
) -> float | None:
    """Return the per-city EB bias shift in degC for the replacement_0_1 path, or None.

    The raw shift is ``effective_bias_c`` (degC, sign = forecast - actual) from the VERIFIED
    promoted ``model_bias_ens`` row (per city x season x month x metric x live_data_version,
    authority='VERIFIED', weight_live>0). It is then passed through the STRUCTURAL
    over-correction GUARD (``bound_eb_bias_shift``, ITEM 1) using the SAME row's ``n_live``
    and ``residual_sd_c`` — so an implausible/unstable bias (e.g. Tokyo -4.847C on small n)
    is tempered toward 0 (shrunk + capped at <=2.5C) while a stable moderate bias
    (Wellington -2.07, Taipei -1.79) is mostly retained. The served shift is therefore NEVER
    larger in magnitude than the raw bias. The caller applies ``corrected = raw - shift`` to
    the degC member votes AND the degC anchor center BEFORE the zero-prior veto.

    LOOKAHEAD SELF-GATE (ITEM 2): when ``target_date`` (``YYYY-MM-DD``) is supplied, the row
    is served ONLY if its ``training_cutoff`` is STRICTLY BEFORE ``target_date`` (date-portion
    comparison, robust to bare-date and ISO-datetime cutoffs). A NULL/short cutoff, or a
    cutoff on/after the target day, returns None (no correction) — the resolver does NOT rely
    on an external gate. When ``target_date`` is None the gate is skipped (legacy signature;
    caller owns causality).

    UNIT: the returned value is degC and is applied to degC cells (NO ×1.8). The
    ``settlement_unit`` argument is validated to be a known unit so a malformed cell-domain
    flows through fail-closed rather than silently mis-scaling.

    FAIL-CLOSED: missing/non-VERIFIED row, NULL effective_bias_c, weight_live<=0, lookahead
    cutoff, a bias the guard zeroes, or any exception -> None (no correction). Never raises.
    """
    try:
        from src.calibration.ens_bias_repo import read_bias_model  # noqa: PLC0415

        if not str(live_data_version or "").strip():
            return None
        unit = str(settlement_unit or "C").strip().upper()
        if unit not in {"C", "F"}:
            # Unknown cell domain -> refuse rather than risk a mis-scaled shift.
            return None

        _month = int(month)
        row = read_bias_model(
            conn,
            city=city,
            season=season,
            metric=metric,
            live_data_version=str(live_data_version),
            month=_month,
            target_month=_month,
            authority="VERIFIED",
            error_model_family=error_model_family,
        )
        if row is None:
            return None
        keys = set(row.keys())
        eff = row["effective_bias_c"] if "effective_bias_c" in keys else None
        weight_live = row["weight_live"] if "weight_live" in keys else 0.0
        if eff is None or float(weight_live or 0.0) <= 0.0:
            return None

        # ITEM 2 — anti-lookahead self-gate. The training_cutoff must be STRICTLY BEFORE the
        # target_date or no correction is served. Read it from the row (SELECT * already
        # includes training_cutoff). Do NOT depend on an external gate to enforce causality.
        training_cutoff = row["training_cutoff"] if "training_cutoff" in keys else None
        if not _training_cutoff_is_causal(training_cutoff, target_date):
            _LOG.info(
                "replacement_0_1 EB bias skipped (lookahead self-gate) city=%s season=%s "
                "month=%d metric=%s training_cutoff=%r target_date=%r",
                city, season, _month, metric, training_cutoff, target_date,
            )
            return None

        # ITEM 1 — STRUCTURAL over-correction guard. effective_bias_c is a RAW per-city mean
        # served at full magnitude (see module header); temper it with the row's own n_live +
        # residual_sd_c so no city can ever receive an over-correction. The guard only ever
        # makes the shift MORE conservative (smaller / abstaining), never larger.
        raw_bias = float(eff)
        n_live = int(row["n_live"]) if "n_live" in keys and row["n_live"] is not None else 0
        residual_sd_c = (
            float(row["residual_sd_c"])
            if "residual_sd_c" in keys and row["residual_sd_c"] is not None
            else 0.0
        )
        guarded = bound_eb_bias_shift(raw_bias, n_live, residual_sd_c)
        if guarded == 0.0:
            _LOG.info(
                "replacement_0_1 EB bias guarded to 0 (unstable/indistinguishable) city=%s "
                "season=%s month=%d metric=%s raw_bias=%.3f n_live=%d residual_sd_c=%.3f",
                city, season, _month, metric, raw_bias, n_live, residual_sd_c,
            )
            return None

        # The returned shift is degC; the replacement_0_1 soft-anchor cells are degC.
        # Apply degC-to-degC directly. NO Fahrenheit ×1.8 (that is the legacy edli path,
        # which operates on members carrying the city's settlement unit).
        _LOG.info(
            "replacement_0_1 EB bias resolved city=%s season=%s month=%d metric=%s "
            "settlement_unit=%s raw_bias=%.3f n_live=%d residual_sd_c=%.3f "
            "guarded_shift=%.3f (degC cell-domain; no *1.8; only-more-conservative)",
            city, season, _month, metric, unit, raw_bias, n_live, residual_sd_c, guarded,
        )
        return guarded
    except Exception as exc:  # fail-closed: never break the shadow construction
        try:
            _LOG.warning("replacement_0_1 EB bias skipped (fail-closed): %s", exc)
        except Exception:
            pass
        return None
