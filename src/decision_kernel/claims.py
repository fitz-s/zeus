"""Claim and certificate type constants for EDLI decision certificates."""

from __future__ import annotations

CLOCK_MODE = "ClockModeCertificate"
AUTHORITY_REGISTRY = "AuthorityRegistryCertificate"
CAUSAL_EVENT = "CausalEventCertificate"
CONFIG_POLICY = "ConfigPolicyCertificate"
SOURCE_TRUTH = "SourceTruthCertificate"
MARKET_TOPOLOGY = "MarketTopologyCertificate"
FAMILY_CLOSURE = "FamilyClosureCertificate"
FORECAST_AUTHORITY = "ForecastAuthorityCertificate"
DAY0_AUTHORITY = "Day0AuthorityCertificate"
MARKET_DATA = "MarketDataCertificate"
EXECUTABLE_SNAPSHOT = "ExecutableSnapshotCertificate"
FRESHNESS = "FreshnessCertificate"
CALIBRATION = "CalibrationCertificate"
MODEL_CONFIG = "ModelConfigCertificate"
BELIEF = "BeliefCertificate"
BOUNDARY = "BoundaryCertificate"
ABSORBING_BOUNDARY = "AbsorbingBoundaryCertificate"
QUOTE_FEASIBILITY = "QuoteFeasibilityCertificate"
COST_MODEL = "CostModelCertificate"
PRE_TRADE_EVIDENCE = "PreTradeEvidenceCertificate"
FILL_FEASIBILITY = "FillFeasibilityEvidenceCertificate"
CANDIDATE_EVIDENCE = "CandidateEvidenceCertificate"
TESTING_PROTOCOL = "TestingProtocolCertificate"
FDR = "FdrCertificate"
SIZING = "SizingCertificate"
PORTFOLIO_STATE = "PortfolioStateCertificate"
RISK_LEVEL = "RiskLevelCertificate"
LIVE_CAP = "LiveCapCertificate"
LIVE_CAP_TRANSITION = "LiveCapTransitionCertificate"
PRE_SUBMIT_DECISION = "PreSubmitDecisionCertificate"
EXECUTION_POLICY = "ExecutionPolicyCertificate"
BALANCE_ALLOWANCE = "BalanceAllowanceCertificate"
VENUE_CONNECTIVITY = "VenueConnectivityCertificate"
PRE_SUBMIT_REVALIDATION = "PreSubmitRevalidationCertificate"
ACTIONABLE_TRADE = "ActionableTradeCertificate"
FINAL_INTENT = "FinalIntentCertificate"
EXECUTOR_EXPRESSIBILITY = "ExecutorExpressibilityCertificate"
ORDER_EXPRESSION = "OrderExpressionCertificate"
EXECUTION_COMMAND = "ExecutionCommandCertificate"
EXECUTION_RECEIPT = "ExecutionReceiptCertificate"
VENUE_SUBMISSION = "VenueSubmissionCertificate"
USER_CHANNEL_ORDER = "UserChannelOrderCertificate"
USER_CHANNEL_TRADE = "UserChannelTradeCertificate"
RECONCILE = "ReconcileCertificate"
FILL = "FillCertificate"
SETTLEMENT = "SettlementCertificate"

PUBLIC_MARKET_CHANNEL_SOURCE = "PUBLIC_MARKET_CHANNEL"

PRE_SUBMIT_REQUIRED_TYPES: frozenset[str] = frozenset({
    CLOCK_MODE,
    CAUSAL_EVENT,
    CANDIDATE_EVIDENCE,
    TESTING_PROTOCOL,
    FDR,
    SIZING,
    RISK_LEVEL,
})

PRE_SUBMIT_FORECAST_REQUIRED_TYPES: frozenset[str] = frozenset({
    CLOCK_MODE,
    CAUSAL_EVENT,
    SOURCE_TRUTH,
    MARKET_TOPOLOGY,
    FAMILY_CLOSURE,
    FORECAST_AUTHORITY,
    CALIBRATION,
    MODEL_CONFIG,
    BELIEF,
    EXECUTABLE_SNAPSHOT,
    QUOTE_FEASIBILITY,
    COST_MODEL,
    PRE_TRADE_EVIDENCE,
    CANDIDATE_EVIDENCE,
    TESTING_PROTOCOL,
    FDR,
    SIZING,
    RISK_LEVEL,
})

PRE_SUBMIT_FORBIDDEN_TYPES: frozenset[str] = frozenset({
    ACTIONABLE_TRADE,
    EXECUTION_COMMAND,
    VENUE_SUBMISSION,
})

ACTIONABLE_REQUIRED_TYPES: frozenset[str] = frozenset({
    CLOCK_MODE,
    CAUSAL_EVENT,
    SOURCE_TRUTH,
    MARKET_TOPOLOGY,
    FAMILY_CLOSURE,
    MODEL_CONFIG,
    BELIEF,
    EXECUTABLE_SNAPSHOT,
    QUOTE_FEASIBILITY,
    COST_MODEL,
    PRE_TRADE_EVIDENCE,
    CANDIDATE_EVIDENCE,
    TESTING_PROTOCOL,
    FDR,
    SIZING,
    RISK_LEVEL,
    LIVE_CAP,
})

EXECUTION_COMMAND_REQUIRED_TYPES: frozenset[str] = frozenset({
    ACTIONABLE_TRADE,
    FINAL_INTENT,
    EXECUTOR_EXPRESSIBILITY,
    LIVE_CAP,
    PRE_SUBMIT_REVALIDATION,
})

FINAL_INTENT_REQUIRED_TYPES: frozenset[str] = frozenset({
    ACTIONABLE_TRADE,
})

EXECUTOR_EXPRESSIBILITY_REQUIRED_TYPES: frozenset[str] = frozenset({
    FINAL_INTENT,
    EXECUTABLE_SNAPSHOT,
    LIVE_CAP,
})

EXECUTION_RECEIPT_REQUIRED_TYPES: frozenset[str] = frozenset({
    EXECUTION_COMMAND,
})
