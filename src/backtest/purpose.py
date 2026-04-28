"""Typed backtest purpose contracts.

Three structurally distinct purposes for replaying historical data:
- SKILL: forecast probability quality (no PnL)
- ECONOMICS: historical PnL with full parity (PROMOTION-grade — gated on
  upstream data; tombstoned until market_events_v2 is populated)
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


@dataclass(frozen=True)
class PurposeContract:
    purpose: BacktestPurpose
    permitted_outputs: frozenset[str]
    parity: ParityContract
    promotion_authority: bool


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


class PurposeContractViolation(TypeError):
    """Raised when a backtest run violates its declared purpose contract."""
