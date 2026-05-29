"""SKILL purpose orchestrator.

Wraps the existing diagnostic_non_promotion forecast-skill replay lane
(src.engine.replay.run_wu_settlement_sweep) with the typed
PurposeContract from src.backtest.purpose so callers must declare
purpose=SKILL explicitly and cannot silently mix in ECONOMICS-shaped
fields. The underlying replay engine is unchanged.

Per packet 2026-04-27 §01 §3.B / §6 S2: this is a NEW orchestrator that
adds typed enforcement on top of replay.py. Migrating the
scripts/run_replay.py CLI and the legacy run_replay() dispatcher to
require purpose= is a follow-up slice (S2.1) that touches replay.py
directly.
"""

from src.backtest.purpose import (
    BacktestPurpose,
    PurposeContract,
    PurposeContractViolation,
    SKILL_CONTRACT,
)


def run_skill(
    start_date: str,
    end_date: str,
    *,
    contract: PurposeContract = SKILL_CONTRACT,
    allow_snapshot_only_reference: bool = False,
):
    """Run a forecast-skill backtest.

    Output is `diagnostic_non_promotion` per BACKTEST_AUTHORITY_SCOPE.
    Returns a ReplaySummary whose `limitations` block must not contain
    any ECONOMICS-shaped fields (Brier/log-loss/accuracy only).
    """
    if contract.purpose is not BacktestPurpose.SKILL:
        raise PurposeContractViolation(
            f"run_skill requires PurposeContract(purpose=SKILL); "
            f"got {contract.purpose.value}"
        )
    if contract.promotion_authority:
        raise PurposeContractViolation(
            "SKILL purpose cannot carry promotion_authority=True"
        )

    from src.engine.replay import run_wu_settlement_sweep

    summary = run_wu_settlement_sweep(
        start_date,
        end_date,
        allow_snapshot_only_reference=allow_snapshot_only_reference,
    )

    leaked = _economics_fields_in_summary(summary)
    if leaked:
        raise PurposeContractViolation(
            f"SKILL summary leaked ECONOMICS-shaped fields: {sorted(leaked)}"
        )
    return summary


def _economics_fields_in_limitations(limitations: dict) -> set[str]:
    """Detect any ECONOMICS-only field name appearing in a SKILL summary.

    The current replay path emits `pnl_available: False` etc. as honest
    limitation markers; those are NOT economics outputs (they're absence
    declarations). We only flag fields that belong to ECONOMICS_FIELDS
    proper (realized_pnl, sharpe, max_drawdown, ...).
    """
    from src.backtest.purpose import ECONOMICS_FIELDS

    leaked: set[str] = set()
    for key in limitations.keys():
        if key in ECONOMICS_FIELDS:
            leaked.add(key)
    return leaked


def _economics_fields_in_summary(summary) -> set[str]:
    """Walk the full ReplaySummary (limitations + per_city + outcomes) for
    any ECONOMICS-shaped field. Catches the seam where per_city dicts can
    leak win_rate (an ECONOMICS_FIELDS member) into a SKILL output.
    """
    from src.backtest.purpose import ECONOMICS_FIELDS

    leaked: set[str] = set()
    leaked.update(_economics_fields_in_limitations(summary.limitations or {}))
    per_city = getattr(summary, "per_city", None) or {}
    for city_block in per_city.values():
        if not isinstance(city_block, dict):
            continue
        for key in city_block.keys():
            if key in ECONOMICS_FIELDS:
                leaked.add(key)
    return leaked


def score_forecast_vector(
    p_vector,
    ordered_bin_labels,
    settlement_object,
    *,
    top_k: int = 3,
):
    """Score one forecast probability vector against a SettlementObject.

    This is the TRIBUNAL §4.4 group-level SKILL result: ONE categorical result
    per (forecast vector × settlement × bin grid), NOT K independent binary rows.
    It consumes the value-derived winner from ``settlement_object`` (never the
    stored ``winning_bin`` string) and emits proper categorical metrics only —
    NO PnL / win-rate / sharpe (those are ECONOMICS, which stays tombstoned).

    Args:
        p_vector: forecast probabilities laid out on the same ordered grid as
            ``settlement_object.ordered_bin_labels``.
        ordered_bin_labels: the bin labels of ``p_vector`` in order, used to prove
            the vector and the settlement speak the same grid before scoring.
        settlement_object: a ``SettlementObject`` (winner derived from value).
        top_k: k for the top-k hit flag (default 3).

    Returns:
        dict with ``group_integrity_status`` and, when valid, the metric bundle.
        ``promotion_authority`` is always False here — promotion gating is a
        separate, not-yet-wired layer (TRIBUNAL PR H). ``group_exclusion_reason``
        is set (and metrics are None) when the group fails integrity.
    """
    # Imported lazily to keep module import-light and avoid a contracts cycle.
    from src.calibration import scoring
    from src.calibration.scoring import ProbabilityGroupError

    labels = tuple(ordered_bin_labels)
    base = {
        "group_integrity_status": "valid",
        "group_exclusion_reason": None,
        "winner_bin_index": settlement_object.winning_bin_index,
        "winner_bin_label": settlement_object.winning_bin_label,
        "truth_source": settlement_object.truth_source,
        "settlement_resolution_status": settlement_object.resolution_status,
        "promotion_authority": False,
        "learning_eligible": settlement_object.learning_eligible,
    }

    def _excluded(reason: str) -> dict:
        out = dict(base)
        out["group_integrity_status"] = "excluded"
        out["group_exclusion_reason"] = reason
        for k in (
            "p_winner",
            "categorical_log_loss",
            "multiclass_brier",
            "ranked_probability_score",
            "winner_rank",
            "reciprocal_rank",
            "top1_hit",
            f"top{top_k}_hit",
        ):
            out[k] = None
        return out

    if len(p_vector) != len(labels):
        return _excluded(
            f"length_mismatch: |p|={len(p_vector)} != |labels|={len(labels)}"
        )
    if labels != settlement_object.ordered_bin_labels:
        return _excluded("bin_grid_mismatch: vector grid != settlement grid")
    try:
        scoring.validate_probability_group(p_vector)
    except ProbabilityGroupError as exc:
        return _excluded(f"invalid_distribution: {exc}")

    winner = settlement_object.winning_bin_index
    result = dict(base)
    result.update(
        {
            "p_winner": scoring.p_winner(p_vector, winner),
            "categorical_log_loss": scoring.categorical_log_loss(p_vector, winner),
            "multiclass_brier": scoring.multiclass_brier(p_vector, winner),
            "ranked_probability_score": scoring.ranked_probability_score(
                p_vector, winner
            ),
            "winner_rank": scoring.winner_rank(p_vector, winner),
            "reciprocal_rank": scoring.reciprocal_rank(p_vector, winner),
            "top1_hit": scoring.top_k_hit(p_vector, winner, 1),
            f"top{top_k}_hit": scoring.top_k_hit(p_vector, winner, top_k),
        }
    )
    return result
