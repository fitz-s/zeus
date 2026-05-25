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
QUOTE_FEASIBILITY = "QuoteFeasibilityCertificate"
COST_MODEL = "CostModelCertificate"
PRE_TRADE_EVIDENCE = "PreTradeEvidenceCertificate"
FILL_FEASIBILITY = "FillFeasibilityEvidenceCertificate"
CANDIDATE_EVIDENCE = "CandidateEvidenceCertificate"
TESTING_PROTOCOL = "TestingProtocolCertificate"
FDR = "FdrCertificate"
KELLY_DRY_RUN = "KellyDryRunCertificate"
PORTFOLIO_STATE = "PortfolioStateCertificate"
RISK_LEVEL = "RiskLevelCertificate"
NO_SUBMIT_MODE = "NoSubmitModeCertificate"
NO_SUBMIT_DECISION = "NoSubmitDecisionCertificate"
EXECUTION_POLICY = "ExecutionPolicyCertificate"
BALANCE_ALLOWANCE = "BalanceAllowanceCertificate"
VENUE_CONNECTIVITY = "VenueConnectivityCertificate"
PRE_SUBMIT_REVALIDATION = "PreSubmitRevalidationCertificate"
ACTIONABLE_TRADE = "ActionableTradeCertificate"
ORDER_EXPRESSION = "OrderExpressionCertificate"
EXECUTION_COMMAND = "ExecutionCommandCertificate"
VENUE_SUBMISSION = "VenueSubmissionCertificate"
USER_CHANNEL_ORDER = "UserChannelOrderCertificate"
USER_CHANNEL_TRADE = "UserChannelTradeCertificate"
RECONCILE = "ReconcileCertificate"
FILL = "FillCertificate"
SETTLEMENT = "SettlementCertificate"

PUBLIC_MARKET_CHANNEL_SOURCE = "PUBLIC_MARKET_CHANNEL"

NO_SUBMIT_REQUIRED_TYPES: frozenset[str] = frozenset({
    CLOCK_MODE,
    CAUSAL_EVENT,
    CANDIDATE_EVIDENCE,
    TESTING_PROTOCOL,
    FDR,
    KELLY_DRY_RUN,
    RISK_LEVEL,
    NO_SUBMIT_MODE,
})

NO_SUBMIT_FORECAST_REQUIRED_TYPES: frozenset[str] = frozenset({
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
    KELLY_DRY_RUN,
    RISK_LEVEL,
    NO_SUBMIT_MODE,
})

NO_SUBMIT_FORBIDDEN_TYPES: frozenset[str] = frozenset({
    ACTIONABLE_TRADE,
    EXECUTION_COMMAND,
    VENUE_SUBMISSION,
})

ACTIONABLE_REQUIRED_TYPES: frozenset[str] = frozenset({
    NO_SUBMIT_DECISION,
    FILL_FEASIBILITY,
    EXECUTION_POLICY,
    BALANCE_ALLOWANCE,
    VENUE_CONNECTIVITY,
    PRE_SUBMIT_REVALIDATION,
})

EXECUTION_COMMAND_REQUIRED_TYPES: frozenset[str] = frozenset({
    ACTIONABLE_TRADE,
})
