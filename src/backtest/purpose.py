"""Typed backtest purpose contracts.

Three structurally distinct purposes for replaying historical data:
- SKILL: forecast probability quality (no PnL)
- ECONOMICS: historical PnL with full parity (PROMOTION-grade — gated on
  upstream data; tombstoned until market_events is populated)
- DIAGNOSTIC: code-vs-history decision divergence (NOT PnL)

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
    DIAGNOSTIC = "diagnostic"


class Sizing(str, Enum):
    NONE = "none"
    FLAT_DIAGNOSTIC = "flat_5"
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

DIAGNOSTIC_FIELDS = frozenset({
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

DIAGNOSTIC_PARITY = ParityContract(
    sizing=Sizing.FLAT_DIAGNOSTIC,
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


class PromotionAuthorityViolation(PurposeContractViolation):
    """Raised when a non-promotion-grade result claims promotion/learning authority
    (TRIBUNAL PR H). Subclass of PurposeContractViolation so existing handlers catch it.
    """


@dataclass(frozen=True)
class PurposeContract:
    purpose: BacktestPurpose
    permitted_outputs: frozenset[str]
    parity: ParityContract
    promotion_authority: bool

    def __post_init__(self) -> None:
        # Structural antibody (TRIBUNAL PR H): a SKILL or DIAGNOSTIC contract can
        # NEVER carry promotion_authority=True. Make the wrong state unconstructable
        # rather than trusting every call site to re-check it.
        if self.promotion_authority and self.purpose in (
            BacktestPurpose.SKILL,
            BacktestPurpose.DIAGNOSTIC,
        ):
            raise PromotionAuthorityViolation(
                f"{self.purpose.value} purpose cannot carry promotion_authority=True; "
                f"only ECONOMICS is promotion-grade."
            )


SKILL_CONTRACT = PurposeContract(
    purpose=BacktestPurpose.SKILL,
    permitted_outputs=SKILL_FIELDS,
    parity=SKILL_PARITY,
    promotion_authority=False,
)

DIAGNOSTIC_CONTRACT = PurposeContract(
    purpose=BacktestPurpose.DIAGNOSTIC,
    permitted_outputs=DIAGNOSTIC_FIELDS,
    parity=DIAGNOSTIC_PARITY,
    promotion_authority=False,
)

ECONOMICS_CONTRACT = PurposeContract(
    purpose=BacktestPurpose.ECONOMICS,
    permitted_outputs=ECONOMICS_FIELDS,
    parity=ECONOMICS_PARITY,
    promotion_authority=True,
)


# ── Promotion / learning authority gates (TRIBUNAL PR H) ──────────────────────
#
# Replay results are diagnostic-grade by default. Promotion (feeding a result into
# live calibration/strategy promotion) or learning (training on it) requires:
#   1. a promotion-grade purpose (only ECONOMICS),
#   2. a ForecastObject identity (the forecast random variable is fully specified),
#   3. a SettlementResolution whose resolution is promotion_eligible
#      (value-derived winner, not an exceptional 50/50/void/unresolved outcome).
# And no result may ever derive authority from the legacy_archived trade_decisions.

# Tables that can NEVER serve as a promotion/learning authority source. trade_decisions
# is schema_class=legacy_archived (log_trade_entry is a no-op stub); canonical entry
# truth is position_events / position_current.
LEGACY_NON_AUTHORITY_TABLES = frozenset({"trade_decisions"})


def assert_not_legacy_authority(source_table: str) -> None:
    """Refuse a legacy_archived table as a promotion/learning authority source."""
    if source_table in LEGACY_NON_AUTHORITY_TABLES:
        raise PromotionAuthorityViolation(
            f"{source_table!r} is legacy_archived and can never serve as a promotion/"
            f"learning authority. Canonical entry truth is position_events / "
            f"position_current."
        )


def assert_promotion_grade(contract, *, forecast_object, settlement_resolution) -> None:
    """Raise PromotionAuthorityViolation unless the result is promotion-grade.

    A promotion-grade replay result requires an ECONOMICS (promotion-authority)
    contract, a ForecastObject identity, and a promotion_eligible SettlementResolution.
    SKILL / DIAGNOSTIC results can never promote, regardless of any flag. Settlement
    objects with an exceptional resolution (50/50, disputed, unresolved) are refused.
    """
    if contract.purpose is not BacktestPurpose.ECONOMICS or not contract.promotion_authority:
        raise PromotionAuthorityViolation(
            f"promotion requires an ECONOMICS promotion-authority contract; got "
            f"purpose={contract.purpose.value} promotion_authority={contract.promotion_authority}."
        )
    if forecast_object is None:
        raise PromotionAuthorityViolation(
            "promotion requires a ForecastObject identity (the forecast random variable "
            "must be fully specified); got None."
        )
    if settlement_resolution is None:
        raise PromotionAuthorityViolation(
            "promotion requires a SettlementResolution; got None."
        )
    if not getattr(settlement_resolution, "promotion_eligible", False):
        status = getattr(settlement_resolution, "resolution_status", "unknown")
        raise PromotionAuthorityViolation(
            f"settlement is not promotion_eligible (resolution_status={status!r}); an "
            f"exceptional resolution (50/50 / disputed / unresolved) cannot promote."
        )
