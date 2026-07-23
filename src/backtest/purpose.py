"""Typed backtest purpose contracts.

Three structurally distinct purposes for replaying historical data:
- SKILL: forecast probability quality (no PnL)
- ECONOMICS: historical PnL with full parity (LEARNING-grade — gated on
  upstream data; tombstoned until market_events is populated)
- TELEMETRY: code-vs-history decision divergence (NOT PnL)

Replaces the implicit 3-purpose conflation in src/engine/replay.py with
typed contracts. Design + rationale at:
docs/operations/task_2026-04-27_backtest_first_principles_review/01_backtest_upgrade_design.md
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class BacktestPurpose(str, Enum):
    SKILL = "skill"
    ECONOMICS = "economics"
    AUDIT_REPLAY = "audit_replay"


class Sizing(str, Enum):
    NONE = "none"
    FLAT_AUDIT = "flat_5"
    KELLY_BOOTSTRAP = "kelly_bootstrap"


class Selection(str, Enum):
    NONE = "none"
    BH_FDR = "bh_fdr"


SKILL_FIELDS = frozenset({
    "brier",
    "log_loss",
    "accuracy",
    "calibration_buckets",
    "climatology_skill_score",
    "majority_baseline",
    "positive_prediction_precision",
    "negative_prediction_precision",
})

ECONOMICS_FIELDS = frozenset({
    "realized_pnl",
    "sharpe",
    "max_drawdown",
    "fdr_adjusted_alpha",
    "win_rate",
    "kelly_size_distribution",
})

AUDIT_REPLAY_FIELDS = frozenset({
    "decision_divergence_count",
    "divergence_by_cohort",
    "edge_sign_flips",
    "size_class_changes",
    "unintended_regression_subjects",
})


@dataclass(frozen=True)
class ParityContract:
    sizing: Sizing
    selection: Selection
    market_price_linkage: Literal["full", "partial", "none"]


SKILL_PARITY = ParityContract(
    sizing=Sizing.NONE,
    selection=Selection.NONE,
    market_price_linkage="none",
)

AUDIT_REPLAY_PARITY = ParityContract(
    sizing=Sizing.FLAT_AUDIT,
    selection=Selection.NONE,
    market_price_linkage="none",
)

ECONOMICS_PARITY = ParityContract(
    sizing=Sizing.KELLY_BOOTSTRAP,
    selection=Selection.BH_FDR,
    market_price_linkage="full",
)


class PurposeContractViolation(TypeError):
    """Raised when a backtest run violates its declared purpose contract."""


class LearningAuthorityViolation(PurposeContractViolation):
    """Raised when a non-learning-grade result claims learning authority
    (TRIBUNAL PR H). Subclass of PurposeContractViolation so existing handlers catch it.
    """


@dataclass(frozen=True)
class PurposeContract:
    purpose: BacktestPurpose
    permitted_outputs: frozenset[str]
    parity: ParityContract
    learning_authority: bool

    def __post_init__(self) -> None:
        # Structural antibody (TRIBUNAL PR H): a SKILL or AUDIT_REPLAY contract can
        # NEVER carry learning_authority=True. Make the wrong state unconstructable
        # rather than trusting every call site to re-check it.
        if self.learning_authority and self.purpose in (
            BacktestPurpose.SKILL,
            BacktestPurpose.AUDIT_REPLAY,
        ):
            raise LearningAuthorityViolation(
                f"{self.purpose.value} purpose cannot carry learning_authority=True; "
                f"only ECONOMICS is learning-grade."
            )


SKILL_CONTRACT = PurposeContract(
    purpose=BacktestPurpose.SKILL,
    permitted_outputs=SKILL_FIELDS,
    parity=SKILL_PARITY,
    learning_authority=False,
)

AUDIT_REPLAY_CONTRACT = PurposeContract(
    purpose=BacktestPurpose.AUDIT_REPLAY,
    permitted_outputs=AUDIT_REPLAY_FIELDS,
    parity=AUDIT_REPLAY_PARITY,
    learning_authority=False,
)

ECONOMICS_CONTRACT = PurposeContract(
    purpose=BacktestPurpose.ECONOMICS,
    permitted_outputs=ECONOMICS_FIELDS,
    parity=ECONOMICS_PARITY,
    learning_authority=True,
)


# ── Learning authority gates (TRIBUNAL PR H) ──────────────────────
#
# Replay results are audit-grade by default. Learning (feeding a result into
# training) or learning (training on it) requires:
#   1. a learning-grade purpose (only ECONOMICS),
#   2. a ForecastObject identity (the forecast random variable is fully specified),
#   3. a SettlementResolution whose resolution is learning_eligible
#      (value-derived winner, not an exceptional 50/50/void/unresolved outcome).
# And no result may ever derive authority from the legacy_archived trade_decisions.

# Tables that can NEVER serve as a learning authority source. trade_decisions
# is schema_class=legacy_archived (log_trade_entry is a no-op stub); canonical entry
# truth is position_events / position_current.
LEGACY_NON_AUTHORITY_TABLES = frozenset({"trade_decisions"})


def assert_not_legacy_authority(source_table: str) -> None:
    """Refuse a legacy_archived table as a learning authority source."""
    if source_table in LEGACY_NON_AUTHORITY_TABLES:
        raise LearningAuthorityViolation(
            f"{source_table!r} is legacy_archived and can never serve as a learning/"
            f"learning authority. Canonical entry truth is position_events / "
            f"position_current."
        )


def assert_learning_grade(contract, *, forecast_object, settlement_resolution) -> None:
    """Raise LearningAuthorityViolation unless the result is learning-grade.

    A learning-grade replay result requires an ECONOMICS (learning-authority)
    contract, a ForecastObject identity, and a learning_eligible SettlementResolution.
    SKILL / AUDIT_REPLAY results can never enter learning, regardless of any flag. Settlement
    objects with an exceptional resolution (50/50, disputed, unresolved) are refused.
    """
    if contract.purpose is not BacktestPurpose.ECONOMICS or not contract.learning_authority:
        raise LearningAuthorityViolation(
            f"learning requires an ECONOMICS learning-authority contract; got "
            f"purpose={contract.purpose.value} learning_authority={contract.learning_authority}."
        )
    if forecast_object is None:
        raise LearningAuthorityViolation(
            "learning requires a ForecastObject identity (the forecast random variable "
            "must be fully specified); got None."
        )
    if settlement_resolution is None:
        raise LearningAuthorityViolation(
            "learning requires a SettlementResolution; got None."
        )
    if not getattr(settlement_resolution, "learning_eligible", False):
        status = getattr(settlement_resolution, "resolution_status", "unknown")
        raise LearningAuthorityViolation(
            f"settlement is not learning_eligible (resolution_status={status!r}); an "
            f"exceptional resolution (50/50 / disputed / unresolved) cannot enter learning."
        )
