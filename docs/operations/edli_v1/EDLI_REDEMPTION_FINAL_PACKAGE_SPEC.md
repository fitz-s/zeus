# EDLI Redemption Final Package — 从 runtime feature 重建为 proof-carrying decision protocol

## 0. 总裁决

你的 critique 是正确的。EDLI 不能继续作为 runtime feature 被 patch；它必须先被重建为 claim-indexed verified certificate DAG / proof-carrying decision protocol。

之前持续出现 80+ P0 的根因不是某个 PR 写坏了，而是系统架构允许以下危险对象流入 money path：

raw event
raw DB row
bool gate
runtime summary
event payload field
unverified receipt
public book quote
old cycle side effect

这些对象都不是 proof。它们不能证明：

这个 claim 属于 Zeus agent filtration I_t；
这个 source authority 是正确的；
这个 family closure 是完整的；
这个 FDR look 是有效的；
这个 Kelly cost 是 typed executable cost；
这个 market quote 是 native YES/NO executable quote；
这个 fill claim 来自 user channel / reconcile / FOK/FAK / validated cohort；
这个 no-submit decision 没有被误解成 real submit。

因此最终架构必须改成：

EDLI = certificate compiler + verifier + ledger + projection reports
not:
EDLI = event reactor + adapters + receipts + many gates

最新 PR332 head 已经 rebased 到 current main，head 是 d82bdfa0ce1be0914e5db420224947d142edb479；PR body 说明现在是 forecast-driven no-submit order-intent path、market-channel ingestion code 默认不启用、Day0 online hard-fact eventing 不启用、real submit disabled，并且 latest local verification 包括 EDLI/money-path/schema/replay/topology 等 targeted tests 通过。但 PR 仍是 draft / not deploy-ready，daemon restart、Polymarket REST seed / websocket subscribe、user-channel / reconcile smoke、DB concurrency smoke、full sweep pass/waiver 都未完成。  最新 workflow runs 也显示 money-path/replay/topology/secrets 等成功，但 full-pytest-sweep 仍 skipped。

这意味着：当前 PR332 可以作为“过渡性 no-submit scaffold”，但不能作为最终 EDLI deploy design。最终 design 必须升级到 certificate kernel。

⸻

## 1. 数学基础重述：从 event ∈ F_t 改成 agent-filtration certificate

### 1.1 原表达的问题

早期写法：

event e ∈ F_t

工程上直观，概率论上不精确。F_t 是 sigma-algebra；runtime event object 不是 sigma-algebra 的元素。正确表达应该是：

An observation record O_e induces a measurable random variable / proposition.
That proposition is admissible for a live Zeus decision at time t
iff it is source-available, agent-received, and live-persisted by t.

因此不能只使用一个 filtration。必须至少区分：

A_t = source / venue / provider availability filtration
I_t = Zeus agent information filtration

Live decision 必须是：

I_t-measurable

而不是只满足：

some external source made it available by t

否则会出现 hidden branch：

source_available_at <= t
but Zeus did not receive or persist the record by t
later replay uses it and pretends live no-leakage

### 1.2 时间 invariant

所有 live certificate 必须带三组时间：

source_available_at
agent_received_at
persisted_at

并满足：

max_parent_source_available_at <= decision_time
max_parent_agent_received_at <= decision_time
max_parent_persisted_at <= decision_time
certificate.source_available_at <= decision_time
certificate.agent_received_at <= decision_time
certificate.persisted_at <= decision_time

如果是 replay / counterfactual，不允许伪装成 live。必须写：

mode = REPLAY_COUNTERFACTUAL

Final rule:

LIVE certificate requires source_available_at, agent_received_at, persisted_at <= decision_time.
NO_SUBMIT certificate also requires this if it is produced by live daemon.
REPLAY_COUNTERFACTUAL certificate may relax persisted_at but cannot be promoted to LIVE.

⸻

## 2. No-submit 数学定理：不能产生 positive robust executable TradeScore

### 2.1 严格形式

Robust executable EV 是：

Eθ[1_F * (Y - C - λ) | I_t]

其中 F 是 fill event。

在 no-submit 模式下，如果没有：

actual submission
user-channel fill evidence
explicit reconciliation fill evidence
FOK / FAK attempt evidence
pre-registered empirical fill model

且 uncertainty set Θ_t 包含 admissible zero-fill model，则：

inf_{θ ∈ Θ_t} Pθ(F | I_t, no_submit, public visible book only) = 0

所以：

robust executable TradeScore_LCB = 0

这不是说 no-submit 没有价值。它说明 no-submit 只能证明不同 claim：

QuoteEdgeBound
ConditionalEdgeGivenFill
QuoteFeasibilityEvidence
KellyDryRun
RiskDryRun
NoSubmitDecision

它不能证明：

positive actionable executable TradeScore

### 2.2 必须改名的对象

No-submit path 禁止出现这些 claim：

actionable_trade_score > 0
would_fill
submitted
accepted
final executable trade
executable TradeScore positive

No-submit path 允许：

quote_feasible
quote_edge_bound
conditional_edge_given_fill
candidate_selected_for_dry_run
kelly_dry_run_size
risk_level_proof
no_submit_verified

### 2.3 Score 分层

必须拆成：

QuoteEdgeBound:
  q/c/book/fee/depth static quote edge bound.
  No fill claim.
ConditionalEdgeGivenFill:
  E[Y - C - λ | F, I_t].
  Only says if fill occurs, edge may exist.
FillFeasibilityEvidence:
  user-channel fill, reconciliation, FOK/FAK, or validated empirical cohort.
  Public book alone cannot create this certificate.
ActionableTradeScore:
  P_fill_LCB * robust conditional edge.
  Can be positive only when FillFeasibilityEvidence + ExecutionPolicy exists.
NoSubmitEvidenceScore:
  can be positive, but must not be named executable TradeScore.

Polymarket docs support this separation: market channel is public level-2 data by asset IDs and emits book/price/trade/market events, while user-channel is authenticated order/trade updates; orderbook docs distinguish buy best ask / sell best bid and display midpoint/last-trade behavior.

⸻

## 3. Certificate DAG，不是线性链

### 3.1 正确 DAG

不要实现线性链：

event -> source -> topology -> forecast -> belief -> quote -> FDR -> Kelly -> risk

应该实现 typed DAG：

```text
CausalEventCertificate
        │
        ├── ClockModeCertificate
        ├── SourceTruthCertificate
        ├── MarketTopologyCertificate
        │        └── FamilyClosureCertificate
        ├── ForecastAuthorityCertificate
        ├── Day0AuthorityCertificate
        ├── MarketDataCertificate
        ├── ConfigPolicyCertificate
        └── AuthorityRegistryCertificate
ForecastAuthority + Day0Authority + Calibration + Topology + Config
        └── BeliefCertificate
MarketData + Topology + ExecutableSnapshot + CostModel + Freshness
        └── QuoteFeasibilityCertificate
Belief + QuoteFeasibility + SourceTruth + Tail/CorrelationPolicy
        └── PreTradeEvidenceCertificate
FamilyClosure + CandidateEvidence + TestingProtocol
        └── FdrCertificate
Belief + QuoteFeasibility + BankrollPolicy + CostModel
        └── KellyDryRunCertificate
PortfolioState + RiskPolicy + ExposureState
        └── RiskLevelCertificate
PreTradeEvidence + FDR + KellyDryRun + RiskLevel + Mode
        └── NoSubmitDecisionCertificate
```

Live/action path extends:

```text
NoSubmitDecision
+ FillFeasibilityEvidence
+ ExecutionPolicy
+ BalanceAllowance
+ VenueConnectivity
+ PreSubmitRevalidation
        └── ActionableTradeCertificate
        └── OrderExpressionCertificate
        └── ExecutionCommandCertificate
        └── VenueSubmissionCertificate
        └── UserChannelOrderCertificate / ReconcileCertificate
        └── FillCertificate
        └── SettlementCertificate
```

### 3.2 DAG prevents wrong ordering

| Wrong ordering | Certificate prevention |
| --- | --- |
| quote before topology | QuoteFeasibility requires MarketTopology + ExecutableSnapshot |
| Kelly before typed cost | KellyDryRun requires QuoteFeasibility / CostModel certificate |
| FDR before family closure | FdrCertificate requires FamilyClosure + TestingProtocol |
| risk before portfolio state | RiskLevelCertificate requires PortfolioState + RiskPolicy |
| fill from market channel | FillCertificate requires UserChannel / Reconcile / FOK/FAK evidence |
| replay masquerades live | ClockModeCertificate enforces mode and persisted_at rules |
| final action without venue connectivity | ActionableTrade requires VenueConnectivity + PreSubmitRevalidation |

⸻

## 4. Claim-indexed minimal certificate sets

No global minimal set exists. Minimality depends on claim.

Claim A — Quote observation

Claim: “This native token had visible quote data at decision time.”

Minimum parents:

ClockModeCertificate
MarketTopologyCertificate
MarketDataCertificate
ExecutableSnapshotCertificate
FreshnessCertificate
QuoteFeasibilityCertificate

Does not need:

Belief
FDR
Kelly
Risk

Claim B — Candidate evidence

Claim: “This event produced q/c candidate evidence under current model.”

Minimum parents:

ClockModeCertificate
SourceTruthCertificate
MarketTopologyCertificate
FamilyClosureCertificate
ForecastAuthorityCertificate or Day0AuthorityCertificate
CalibrationCertificate
ModelConfigCertificate
BeliefCertificate
QuoteFeasibilityCertificate

Does not need:

FDR
Kelly
Risk

Claim C — No-submit dry-run decision

Claim: “This candidate passes the non-side-effect decision stack.”

Minimum parents:

CandidateEvidenceCertificate
TestingProtocolCertificate
FdrCertificate
KellyDryRunCertificate
RiskLevelCertificate
NoSubmitModeCertificate
NoSubmitDecisionCertificate

Claim D — Actionable trade

Claim: “This candidate may become execution command.”

Minimum parents:

NoSubmitDecisionCertificate
FillFeasibilityEvidenceCertificate
ExecutionPolicyCertificate
BalanceAllowanceCertificate
VenueConnectivityCertificate
PreSubmitRevalidationCertificate
ActionableTradeCertificate

This prevents both over-certification and under-certification. Quote evidence will not be forced through FDR/Kelly, while dry-run trading decisions cannot skip FDR/Kelly/Risk.

⸻

## 5. FDR optional stopping is mandatory

Sibling family denominator is necessary but insufficient.

Event-driven discovery is a stopping-time process:

same family
multiple events
multiple looks
adaptive timing

A standard per-event BH/FDR certificate is not enough unless it proves repeated looks are handled.

Therefore add:

TestingProtocolCertificate

Required fields:

testing_protocol_id
family_id
decision_window_id
event_trigger_type
look_index
max_looks
alpha_budget
alpha_spent_so_far
alpha_spending_rule
pvalue_type
optional_stopping_valid: bool
sibling_hypothesis_count
family_closure_hash
predeclared_at
protocol_available_at
agent_received_at
persisted_at

Allowed modes:

FIXED_WINDOW_BH
ALPHA_SPENDING
ALWAYS_VALID_PVALUES
SHADOW_ONLY
NO_FDR_CLAIM

For live/no-submit dry-run decision:

FdrCertificate requires TestingProtocolCertificate.

If no optional-stopping-valid protocol exists:

FdrCertificate.status = REJECTED
NoSubmitDecision cannot claim "would pass trading stack"

This closes a class of hidden statistical P0s that code-level gate checks cannot catch.

⸻

## 6. Certificate grammar

### 6.1 Header

Every certificate must share:

```python
@dataclass(frozen=True)
class CertificateHeader:
    certificate_id: str
    certificate_type: str
    schema_version: int
    canonicalization_version: str
    semantic_key: str
    claim_type: str
    mode: Literal["LIVE", "NO_SUBMIT", "SHADOW", "REPLAY_COUNTERFACTUAL"]
    decision_time: datetime
    source_available_at: datetime | None
    agent_received_at: datetime | None
    persisted_at: datetime | None
    max_parent_source_available_at: datetime | None
    max_parent_agent_received_at: datetime | None
    max_parent_persisted_at: datetime | None
    parent_edges: tuple["ParentEdge", ...]
    authority_id: str
    authority_version: str
    algorithm_id: str
    algorithm_version: str
    config_hash: str | None
    model_version_hash: str | None
    payload_hash: str
    certificate_hash: str
    verifier_status: Literal["VERIFIED", "REJECTED", "SUPERSEDED", "REVIEW_REQUIRED"]
```

### 6.2 Parent edges are role-labeled

Do not store only hash tuple. Store:

```python
@dataclass(frozen=True)
class ParentEdge:
    role: str
    certificate_hash: str
    certificate_type: str
    required: bool = True
```

Examples:

```text
("topology", hash1)
("forecast_authority", hash2)
("calibration", hash3)
("quote_feasibility", hash4)
("testing_protocol", hash5)
```

Role swap changes certificate hash. This prevents semantic parent confusion.

### 6.3 Hash input

```text
certificate_hash = H(
  certificate_type,
  schema_version,
  canonicalization_version,
  semantic_key,
  claim_type,
  mode,
  decision_time,
  source_available_at,
  agent_received_at,
  persisted_at,
  parent_edges_with_roles,
  authority_id,
  authority_version,
  algorithm_id,
  algorithm_version,
  config_hash,
  model_version_hash,
  payload_hash
)
```

Merkle hash only proves structural consistency, not truth. Truth comes from:

authority_id
authority_version
verifier_status
parent roles
mode
time invariants
constructor verifier

⸻

## 7. Core certificate taxonomy

### 7.1 Base certificates

ClockModeCertificate
AuthorityRegistryCertificate
CausalEventCertificate
ConfigPolicyCertificate

ClockModeCertificate is mandatory parent for every live/no-submit decision.

Fields:

mode
decision_time
clock_source
agent_runtime_id
replay_run_id optional
live_persist_required bool

### 7.2 Source / topology / data

SourceTruthCertificate
MarketTopologyCertificate
FamilyClosureCertificate
ForecastAuthorityCertificate
Day0AuthorityCertificate
MarketDataCertificate
ExecutableSnapshotCertificate
FreshnessCertificate

### 7.3 Model / belief

CalibrationCertificate
ModelConfigCertificate
BeliefCertificate
BoundaryCertificate

### 7.4 Quote / evidence

QuoteFeasibilityCertificate
CostModelCertificate
PreTradeEvidenceCertificate
FillFeasibilityEvidenceCertificate

FillFeasibilityEvidenceCertificate cannot be produced from public market channel alone.

### 7.5 Decision stack

TestingProtocolCertificate
FdrCertificate
KellyDryRunCertificate
PortfolioStateCertificate
RiskLevelCertificate
NoSubmitDecisionCertificate

### 7.6 Action path

ExecutionPolicyCertificate
BalanceAllowanceCertificate
VenueConnectivityCertificate
PreSubmitRevalidationCertificate
ActionableTradeCertificate
OrderExpressionCertificate
ExecutionCommandCertificate
VenueSubmissionCertificate
UserChannelOrderCertificate
UserChannelTradeCertificate
ReconcileCertificate
FillCertificate
SettlementCertificate

⸻

## 8. New directory topology

Add:

```text
src/decision_kernel/
  AGENTS.md
  __init__.py
  certificate.py
  canonicalization.py
  clock.py
  authority.py
  ledger.py
  verifier.py
  compiler.py
  claims.py
  modes.py
  errors.py
  certificates/
    __init__.py
    base.py
    event.py
    source.py
    topology.py
    forecast.py
    day0.py
    calibration.py
    belief.py
    market_data.py
    quote.py
    evidence.py
    testing_protocol.py
    fdr.py
    kelly.py
    risk.py
    no_submit.py
    action.py
    execution.py
    fill.py
    settlement.py
  adapters/
    __init__.py
    event_store_adapter.py
    forecast_authority_adapter.py
    topology_adapter.py
    calibration_adapter.py
    executable_snapshot_adapter.py
    market_channel_adapter.py
    day0_adapter.py
    fdr_adapter.py
    kelly_adapter.py
    risk_adapter.py
  reports/
    __init__.py
    certificate_report.py
    no_submit_decision_report.py
    compile_failure_report.py
```

Existing src/events/reactor.py becomes thin:

fetch OpportunityEvent
call DecisionCompiler.compile_no_submit(...)
persist certificates
mark event processed/dead-letter

No custom FDR/Kelly/TradeScore logic in reactor.

⸻

## 9. Database schema

### 9.1 Certificate ledger tables

```sql
CREATE TABLE IF NOT EXISTS decision_certificates (
    certificate_id TEXT NOT NULL PRIMARY KEY,
    certificate_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    canonicalization_version TEXT NOT NULL,
    semantic_key TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('LIVE','NO_SUBMIT','SHADOW','REPLAY_COUNTERFACTUAL')),
    decision_time TEXT NOT NULL,
    source_available_at TEXT,
    agent_received_at TEXT,
    persisted_at TEXT,
    max_parent_source_available_at TEXT,
    max_parent_agent_received_at TEXT,
    max_parent_persisted_at TEXT,
    authority_id TEXT NOT NULL,
    authority_version TEXT NOT NULL,
    algorithm_id TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    config_hash TEXT,
    model_version_hash TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    certificate_hash TEXT NOT NULL UNIQUE,
    verifier_status TEXT NOT NULL CHECK (
      verifier_status IN ('VERIFIED','REJECTED','SUPERSEDED','REVIEW_REQUIRED')
    ),
    created_at TEXT NOT NULL,
    UNIQUE(certificate_type, semantic_key, mode, decision_time)
);
CREATE TABLE IF NOT EXISTS decision_certificate_edges (
    child_certificate_id TEXT NOT NULL,
    parent_role TEXT NOT NULL,
    parent_certificate_hash TEXT NOT NULL,
    parent_certificate_type TEXT NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0,1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (child_certificate_id, parent_role, parent_certificate_hash)
);
CREATE TABLE IF NOT EXISTS decision_certificate_supersessions (
    supersession_id TEXT NOT NULL PRIMARY KEY,
    old_certificate_hash TEXT NOT NULL,
    new_certificate_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_compile_failures (
    failure_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    mode TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT,
    parent_hashes_json TEXT,
    created_at TEXT NOT NULL
);
```

### 9.2 No-submit projection table

edli_no_submit_receipts can remain as projection, not source of truth. Rename or map to:

no_submit_decision_projections

It should reference:

no_submit_decision_certificate_hash
belief_certificate_hash
quote_certificate_hash
fdr_certificate_hash
kelly_certificate_hash
risk_certificate_hash

The certificate ledger is authority; projection is report convenience.

⸻

## 10. Compiler API

### 10.1 No-submit compiler

```python
class DecisionCompiler:
    def compile_no_submit(
        self,
        event: OpportunityEvent,
        *,
        decision_time: datetime,
        mode: Literal["NO_SUBMIT", "REPLAY_COUNTERFACTUAL"],
    ) -> NoSubmitCompileResult:
        ...
```

Return:

```python
@dataclass(frozen=True)
class NoSubmitCompileResult:
    status: Literal["VERIFIED", "REJECTED", "REVIEW_REQUIRED"]
    no_submit_certificate: NoSubmitDecisionCertificate | None
    certificates: tuple[DecisionCertificate, ...]
    failures: tuple[CompileFailure, ...]
```

### 10.2 Reactor

```python
result = compiler.compile_no_submit(event, decision_time=decision_time, mode="NO_SUBMIT")
ledger.persist_all(result.certificates)
failure_ledger.persist_all(result.failures)
if result.status == "VERIFIED":
    mark_processed
else:
    mark_processed_or_dead_letter_based_on_failure_policy
```

Reactor does not implement:

source truth
executable snapshot
FDR
Kelly
risk
TradeScore
receipt construction

It only persists compiler output.

⸻

## 11. Claim-specific compiler flows

### 11.1 ForecastSnapshotReady no-submit flow

```text
OpportunityEvent
  -> CausalEventCertificate
  -> ClockModeCertificate
  -> MarketTopologyCertificate
  -> FamilyClosureCertificate
  -> ForecastAuthorityCertificate via canonical executable forecast reader
  -> CalibrationCertificate via current live calibration authority
  -> ModelConfigCertificate
  -> BeliefCertificate
  -> ExecutableSnapshotCertificate
  -> QuoteFeasibilityCertificate
  -> PreTradeEvidenceCertificate
  -> TestingProtocolCertificate
  -> FdrCertificate
  -> KellyDryRunCertificate
  -> RiskLevelCertificate
  -> NoSubmitDecisionCertificate
```

No submit, no fill, no actionable trade.

### 11.2 Market channel flow

```text
MarketChannelMessage
  -> MarketDataCertificate
  -> ExecutableSnapshotInvalidationCertificate optional
  -> QuoteFeasibilityCertificate optional
```

No belief, no FDR, no Kelly, no NoSubmitDecision unless a separate Forecast/Day0 event triggers candidate evidence.

### 11.3 Day0 flow

Current PR disables Day0 online. Future flow:

```text
Day0ObservationContext
  -> CausalEventCertificate
  -> SourceTruthCertificate
  -> Day0AuthorityCertificate
  -> BoundaryCertificate
  -> BeliefCertificate
  -> downstream no-submit flow
```

No Day0 certificate unless:

source match
station match
local date match
DST unambiguous
metric match
rounding match
source authorized
observation_available_at <= decision_time
agent_received_at <= decision_time
persisted_at <= decision_time

⸻

## 12. Certificate verifier rules

### 12.1 General verifier

```python
def verify_certificate(cert, parents, decision_time):
    assert cert.mode in allowed_modes
    assert cert.decision_time == decision_time
    assert all(parent.hash == edge.parent_hash for edge in parent_edges)
    assert all(edge.role unique and expected)
    assert max_parent_source_available_at <= decision_time
    assert max_parent_agent_received_at <= decision_time
    if cert.mode == "LIVE" or cert.mode == "NO_SUBMIT":
        assert max_parent_persisted_at <= decision_time
    assert canonical_hash(cert) == cert.certificate_hash
```

### 12.2 No-submit verifier

Requires:
  ClockModeCertificate(mode=NO_SUBMIT)
  CandidateEvidenceCertificate
  FdrCertificate
  KellyDryRunCertificate
  RiskLevelCertificate
Forbids:
  ActionableTradeCertificate
  ExecutionCommandCertificate
  VenueSubmissionCertificate
  submitted=true
  action_score > 0

### 12.3 Actionable trade verifier

Requires:

NoSubmitDecisionCertificate
FillFeasibilityEvidenceCertificate
ExecutionPolicyCertificate
BalanceAllowanceCertificate
VenueConnectivityCertificate
PreSubmitRevalidationCertificate

Forbids:

public market-channel-only fill evidence
stale quote
midpoint cost
NO complement cost
last trade cost

⸻

## 13. Testing package

### 13.1 Pure theorem tests

```text
tests/decision_kernel/test_certificate_time_filtration.py
  test_live_rejects_parent_source_available_after_decision
  test_live_rejects_parent_agent_received_after_decision
  test_live_rejects_parent_persisted_after_decision
  test_replay_counterfactual_cannot_be_live
tests/decision_kernel/test_certificate_hashing.py
  test_parent_role_swap_changes_hash
  test_same_semantic_key_different_hash_requires_supersession
  test_hash_canonicalization_decimal_datetime_stable
tests/decision_kernel/test_no_submit_theorems.py
  test_no_submit_cannot_have_actionable_trade_score
  test_no_submit_cannot_have_execution_command
  test_quote_feasibility_is_not_fill_feasibility
tests/decision_kernel/test_no_bypass.py
  test_actionable_trade_requires_fill_feasibility
  test_execution_command_requires_verified_actionable_trade
  test_market_channel_certificate_cannot_be_fill_certificate
```

### 13.2 Authority adapter tests

```text
tests/decision_kernel/test_forecast_authority_adapter.py
  test_uses_canonical_executable_forecast_reader
  test_reader_reason_code_is_authority
  test_reader_applied_validations_preserved
  test_source_run_policy_not_forked
tests/decision_kernel/test_topology_adapter.py
  test_market_topology_from_forecasts_authority
  test_family_closure_requires_all_sibling_conditions
  test_high_low_metric_never_mix
tests/decision_kernel/test_calibration_adapter.py
  test_uses_current_live_calibration_authority
  test_predictive_error_not_live_wired_or_explicit_parent
  test_missing_calibration_blocks_belief
tests/decision_kernel/test_quote_adapter.py
  test_buy_yes_native_yes_ask
  test_buy_no_native_no_ask
  test_sell_held_token_bid
  test_midpoint_last_trade_forbidden
  test_public_visible_depth_not_fill
```

### 13.3 Statistical tests

```text
tests/decision_kernel/test_testing_protocol_certificate.py
  test_fdr_requires_testing_protocol
  test_repeated_look_without_alpha_spending_blocks
  test_fixed_window_bh_requires_predeclared_window
  test_always_valid_pvalues_required_for_open_ended_event_stream
```

### 13.4 Runtime integration tests

```text
tests/events/test_reactor_compiler_integration.py
  test_reactor_only_calls_compiler
  test_reactor_persists_all_certificates_before_processed
  test_compile_failure_included_in_denominator
  test_no_submit_projection_not_source_of_truth
```

### 13.5 Deploy smoke tests

```text
tests/smoke/test_edli_daemon_restart.py
tests/smoke/test_market_channel_seed_and_subscribe.py
tests/smoke/test_user_channel_reconcile_separation.py
tests/smoke/test_edli_db_concurrency.py
```

These can be operator-run, but deploy-ready requires pass or waiver.

⸻

## 14. Implementation order

### PR-A — Certificate grammar and theorem tests

Scope:

src/decision_kernel/certificate.py
src/decision_kernel/canonicalization.py
src/decision_kernel/verifier.py
src/decision_kernel/claims.py
src/decision_kernel/modes.py
tests/decision_kernel/test_certificate_*.py

No DB, no runtime, no EDLI reactor changes.

Acceptance:

theorem tests pass
no-submit cannot construct actionable score
market-channel cannot construct fill certificate
parent role swap changes hash
agent filtration invariants enforced

### PR-B — Certificate ledger

Scope:

src/decision_kernel/ledger.py
src/state/schema/decision_certificates_schema.py
architecture/db_table_ownership.yaml
tests/decision_kernel/test_certificate_ledger.py

Acceptance:

insert verified certificate
duplicate same hash idempotent
duplicate same semantic key different hash requires supersession or error
edges persisted with roles
compile failures persisted

### PR-C — Authority adapters

Scope:

src/decision_kernel/adapters/*
tests/decision_kernel/test_*_adapter.py

Acceptance:

forecast adapter uses canonical executable forecast reader only
topology adapter uses forecasts.market_events_v2
calibration adapter uses current live calibration authority
quote adapter uses native bid/ask/depth/tick/min/negRisk

### PR-D — No-submit compiler

Scope:

src/decision_kernel/compiler.py
src/decision_kernel/certificates/no_submit.py
tests/decision_kernel/test_no_submit_compiler.py

Acceptance:

ForecastSnapshotReady event compiles to NoSubmitDecisionCertificate
No-submit cannot contain ActionableTradeCertificate
Compile failures persisted
No FDR without TestingProtocolCertificate

### PR-E — Reactor projection integration

Scope:

src/events/reactor.py
src/events/opportunity_event.py
src/events/no_submit_projection.py
src/analysis/*
tests/events/test_reactor_compiler_integration.py

Acceptance:

reactor calls compiler only
reactor persists certificates before marking event processed
no bool gates for FDR/Kelly/Risk
projection reports read certificate ledger

### PR-F — Market-channel evidence service

Scope:

src/events/triggers/market_channel_ingestor.py
src/decision_kernel/adapters/market_channel_adapter.py
tests/events/test_market_channel_ingestor.py
tests/smoke/test_market_channel_seed_and_subscribe.py

Acceptance:

market channel creates MarketDataCertificate only
quote evidence but no fill evidence
DB concurrency smoke

### PR-G — Day0 online hook

Scope:

src/events/triggers/day0_extreme_updated.py
src/decision_kernel/adapters/day0_adapter.py
tests/events/test_day0_live_authority.py

Acceptance:

Day0ObservationContext hook emits event
source/station/local-date/DST/rounding/metric gate
BoundaryCertificate produced
Day0 flags can turn on only after smoke

### PR-H — Actionable trade path

Scope only after no-submit stable:

FillFeasibilityEvidenceCertificate
ExecutionPolicyCertificate
BalanceAllowanceCertificate
VenueConnectivityCertificate
PreSubmitRevalidationCertificate
ActionableTradeCertificate
ExecutionCommandCertificate

Acceptance:

no live trade without FillFeasibilityEvidence
no market-channel fill truth
pre-submit revalidation required
executor accepts verified ExecutionCommandCertificate only

⸻

## 15. What to do with current PR332

Current PR332 is not useless. It should become the transition scaffold, not final proof architecture.

Keep / salvage

event store / event processing / dead letters
forecast trigger machinery
market-channel parser/coalescer pieces
no-submit receipt projection ideas
native executable cost helpers
trade-score math helpers, renamed for evidence mode
forecast source/topology/calibration connection split
tests that prove no broad run_cycle wrapper
tests for no-submit submitted=false

Demote / replace

EventSubmissionReceipt -> NoSubmitDecisionProjection
edli_no_submit_receipts -> projection table, not proof authority
reactor gates -> compiler certificates
adapter SQL policy -> authority adapters / canonical readers
FDR/Kelly/Risk booleans -> certificates

Scope current PR if merging before full certificate kernel

Only acceptable label:

EDLI forecast no-submit scaffold / non-deploy

Required merge conditions:

market_channel_ingestor_enabled=false
Day0 disabled
no deploy reboot
full sweep waiver or pass
docs say certificate-kernel follow-up required

If you keep deploy-ready as the bar, do not merge current architecture; implement PR-A through PR-E first.

⸻

## 16. Final E2E acceptance contract

A deploy-ready EDLI implementation must satisfy:

A01 EDLI is a certificate compiler, not a reactor feature.
A02 Reactor fetches events and persists compiler output; no FDR/Kelly/Risk gate logic in reactor.
A03 Every certificate has mode, claim_type, semantic_key, role-labeled parents, authority_id, algorithm_version, config_hash, payload_hash, certificate_hash.
A04 Live/no-submit certificates require source_available_at, agent_received_at, persisted_at <= decision_time.
A05 Replay-counterfactual certificates cannot be promoted to live.
A06 Merkle parent edges include role labels.
A07 Duplicate semantic key with different hash requires supersession or hard error.
A08 Public market data cannot produce FillCertificate.
A09 No-submit cannot produce positive actionable executable TradeScore.
A10 No-submit can produce quote_edge_bound and conditional_edge_given_fill.
A11 Quote observation claim does not require FDR/Kelly/Risk.
A12 Dry-run decision claim requires CandidateEvidence, TestingProtocol, FDR, KellyDryRun, RiskLevel, NoSubmitMode.
A13 Actionable trade claim requires FillFeasibility, ExecutionPolicy, BalanceAllowance, VenueConnectivity, PreSubmitRevalidation.
A14 BUY YES uses native YES ask.
A15 BUY NO uses native NO ask.
A16 SELL uses held-token bid.
A17 Midpoint/displayed probability/last trade are forbidden executable costs.
A18 ForecastAuthorityCertificate uses canonical executable forecast reader.
A19 CalibrationCertificate uses current live calibration authority.
A20 BeliefCertificate is reproducible and bound to model/config/calibration versions.
A21 FdrCertificate requires TestingProtocolCertificate with optional-stopping validity or fixed window.
A22 KellyDryRunCertificate requires typed ExecutionPrice.
A23 RiskLevelCertificate has portfolio/risk-state parent.
A24 ExecutionCommandCertificate can only be built from verified ActionableTradeCertificate.
A25 Executor accepts only verified ExecutionCommandCertificate.
A26 All compile failures are persisted and included in report denominators.
A27 Market-channel daemon service has REST seed / WS subscribe / reconnect / DB concurrency smoke.
A28 User-channel/reconcile remains fill authority and is smoke-tested.
A29 Full pytest sweep passes or has explicit unrelated-baseline waiver.
A30 Day0 online is either disabled and scoped as follow-up, or wired through Day0ObservationContext with smoke.

⸻

## 17. Final conclusion

The refined direction is now stable:

Stop patching EDLI as runtime adapter.
Build claim-indexed verified certificate DAG.
Let reactor persist compiler output.
Treat current PR332 as scaffold/projection, not final authority.

The math is the anchor:

No-submit cannot prove positive robust executable TradeScore.
Public market data cannot prove fill.
FDR under event-driven optional stopping requires TestingProtocolCertificate.
Live no-leakage is agent-filtration measurability, not source availability alone.

If the repo implements this certificate compiler, the P0 class we have been discovering becomes structurally impossible. If it keeps patching adapters and receipts, the loop will continue.
