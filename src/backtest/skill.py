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
    SKILL_FIELDS,
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

    leaked = _economics_fields_in_limitations(summary.limitations)
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
