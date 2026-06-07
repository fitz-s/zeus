# Created: 2026-06-07
# Last reused/audited: 2026-06-07
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

# Error-model family for the replacement_0_1 soft-anchor EB bias. INTENTIONALLY the SAME
# family as the legacy edli path ('edli_per_city_v1') — ONE-BUILDER reuse of the single
# promoted-bias store (the 67 HIGH + 7 LOW VERIFIED rows live here). No-double-correction
# is guaranteed STRUCTURALLY by mutually-exclusive construction surfaces (replacement_0_1
# returns before the legacy edli path in _live_yes_probabilities), NOT by family-keying.
REPLACEMENT_EB_BIAS_FAMILY = "edli_per_city_v1"

_LOG = logging.getLogger("zeus.replacement_eb_bias")


def resolve_replacement_eb_bias_shift_c(
    conn,
    *,
    city: str,
    season: str,
    month: int,
    metric: str,
    live_data_version: str,
    settlement_unit: str = "C",
    error_model_family: str = REPLACEMENT_EB_BIAS_FAMILY,
) -> float | None:
    """Return the per-city EB bias shift in degC for the replacement_0_1 path, or None.

    The shift is ``effective_bias_c`` (degC, sign = forecast - actual) from the VERIFIED
    promoted ``model_bias_ens`` row (per city x season x month x metric x live_data_version,
    authority='VERIFIED', weight_live>0). The caller applies ``corrected = raw - shift`` to
    the degC member votes AND the degC anchor center BEFORE the zero-prior veto.

    UNIT: the returned value is degC and is applied to degC cells (NO ×1.8). The
    ``settlement_unit`` argument is validated to be a known unit so a malformed cell-domain
    flows through fail-closed rather than silently mis-scaling.

    FAIL-CLOSED: missing/non-VERIFIED row, NULL effective_bias_c, weight_live<=0, or any
    exception -> None (no correction). Never raises.
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

        # effective_bias_c is degC; the replacement_0_1 soft-anchor cells are degC.
        # Apply degC-to-degC directly. NO Fahrenheit ×1.8 (that is the legacy edli path,
        # which operates on members carrying the city's settlement unit).
        shift_c = float(eff)
        _LOG.info(
            "replacement_0_1 EB bias resolved city=%s season=%s month=%d metric=%s "
            "settlement_unit=%s effective_bias_c=%.3f (degC cell-domain; no *1.8)",
            city, season, _month, metric, unit, shift_c,
        )
        return shift_c
    except Exception as exc:  # fail-closed: never break the shadow construction
        try:
            _LOG.warning("replacement_0_1 EB bias skipped (fail-closed): %s", exc)
        except Exception:
            pass
        return None
