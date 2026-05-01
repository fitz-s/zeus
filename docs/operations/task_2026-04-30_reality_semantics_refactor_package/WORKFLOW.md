# Workflow

Status: execution workflow for the reality-semantics refactor.

## Skills

Use these skills for this package:

1. `$plan` — direct planning mode for phase sequencing, acceptance criteria, and testable gates.
2. `zeus-ai-handoff` — Zeus-specific handoff discipline: disk-first artifacts, topology first, scope lock, co-tenant git hygiene, and reviewer/verifier gates.
3. `ai-slop-cleaner` — cleanup/refactor discipline: behavior lock first, explicit cleanup plan, smell-by-smell passes, focused verification.

Do not use OMX `team`, `ralph`, or `ultrawork` from this Codex App surface
unless an attached OMX runtime is actually available. If future implementation
needs parallel review, use Codex native subagents only for bounded independent
review/verification lanes and only when explicitly authorized by the current
surface rules.

## Execution Mode

This is Mode C in Zeus handoff terms once source implementation begins:
multi-batch execution with critic/verifier gates. It touches K0/K1/K2/K3
surfaces, execution semantics, and promotion evidence. It must not be treated
as Mode A direct cleanup.

Package-file preparation itself is Mode A direct work because it is reversible
docs/package organization.

## Latest-Plan-Pre5 Realignment For Blocked Surfaces

The refactor package is now being merged into `plan-pre5`, not `main`. Treat
`plan-pre5` as the current mainline authority for this package. Conflict
resolution must preserve its newer `FinalExecutionIntent` live-submit work:
snapshot-bound final limit, native side/token identity, depth-proofed frozen
submitted shares, allocator order-type agreement, and executor no-recompute
behavior.

Latest topology reroute on the merged branch keeps these follow-ups separate:

1. F-06 venue identity gate: do not start by editing
   `src/venue/polymarket_v2_adapter.py`. Re-enter through admitted
   client/envelope/test surfaces first; open a venue-governed packet only if
   adapter proof is still required.
2. F-08 order-policy cost authority: extend the existing
   `src/contracts/execution_intent.py` contracts first. Do not add an
   unregistered `src/contracts/executable_cost_basis.py`; `FinalExecutionIntent`
   and `ExecutableCostBasis` already own the typed cost/order-policy seam.
3. F-09 fill authority split: schema, fill tracker, harvester, and new tests
   remain planning-lock scope. Split submitted target, filled quantity, filled
   cost basis, average fill price, and economics authority in a separate packet.
4. F-10 report/replay cohort hard gates: start only after F-09 provides durable
   fill/economics-version fields. Reports and replay should hard-fail mixed
   corrected/legacy cohorts or explicitly segregate them.

Stop instead of improvising if topology still marks a target as blocked,
forbidden, unclassified, or scope-expanding. No live submit, production DB
mutation, schema apply, config/source-routing flip, venue adapter rewrite, or
strategy promotion is authorized by this merge.

## Phase Workflow

### Phase 0/A — Authority, Guardrails, Behavior Locks

Goal: make unsafe legacy semantics explicit and fail-closed before runtime
rewiring.

Work shape:

- update existing authority/negative constraints only when topology admits the files
- add tests/static checks for forbidden scalar crossings
- name legacy mode as non-promotion-grade
- keep corrected live disabled by default

Acceptance:

- raw VWMP/quote cannot enter corrected posterior
- fee-adjusted implied probability cannot satisfy corrected Kelly authority
- executor corrected path cannot derive limit from posterior/VWMP
- monitor held-token quote cannot become corrected posterior prior

### Phase B — Contracts and Import Fences

Goal: make the semantic objects explicit and hard to misuse.

Work shape:

- strengthen or split `MarketPriorDistribution`, `ExecutableCostBasis`,
  `ExecutableTradeHypothesis`, `FinalExecutionIntent`, and order-policy contracts
- add import-fence tests
- keep behavior additive until tests protect runtime changes

Acceptance:

- contracts reject invalid authority
- cost basis carries snapshot/token/fee/tick/min-order/hash lineage
- final intent is immutable and submit-ready without posterior/VWMP inputs

### Phase C — Microstructure Cost Basis

Goal: derive executable cost/proceeds from CLOB token books, not market-prior
or model belief.

Work shape:

- identify canonical `ExecutableMarketSnapshotV2` producer
- implement or reuse CLOB sweep over depth
- produce BUY all-in cost and SELL all-in proceeds with fee metadata
- reject stale/missing/insufficient-depth snapshots

Acceptance:

- quote/depth change affects cost/size/limit, not posterior
- buy-NO executable cost uses native NO token book
- tick/min-order/fee/freshness are validated before live authority

### Phase D — Epistemic Posterior Split

Goal: posterior consumes calibrated belief plus optional named market prior,
never raw executable quote by default.

Work shape:

- keep `model_only_v1` as corrected baseline
- keep `legacy_vwmp_prior_v0` explicit and non-promotion-grade
- allow named complete market prior only through `MarketPriorDistribution`

Acceptance:

- sparse monitor vectors are rejected in corrected prior modes
- changing ask/depth does not change posterior
- named prior changes posterior only with traceable prior identity

### Phase E/F — Executable Hypothesis and Live Economic FDR

Goal: FDR selects the exact executable hypothesis that can be submitted.

Work shape:

- construct full executable hypothesis family after snapshot/cost basis
- hypothesis id includes bin, direction, selected token, snapshot hash,
  cost-basis hash, and order policy
- reject or recompute if snapshot/cost changes after FDR

Acceptance:

- no late mutation of selected edge/size/limit
- live economic edge is payoff probability minus executable cost
- research FDR and live economic FDR are not conflated

### Phase G/H — Runtime and Executor

Goal: executor submits or rejects immutable final intent. It does not invent
price.

Work shape:

- move snapshot/cost before corrected FDR
- remove corrected-mode late reprice
- add corrected executor entrypoint for `FinalExecutionIntent`
- preserve command-journal pre-side-effect discipline

Acceptance:

- submitted limit equals final intent limit
- corrected executor has no posterior/VWMP limit recompute path
- compatibility envelope cannot be certified live

### Phase I/J — Monitor, Exit, Persistence, Reporting

Goal: corrected entries cannot exit, settle, or report through legacy economics.

Work shape:

- split monitor probability refresh from held-token quote refresh
- exit EV uses held-token SELL quote/proceeds
- store pricing semantics version and cost/fill lineage
- reports hard-fail or segregate mixed legacy/corrected cohorts

Acceptance:

- buy-NO exit uses executable bid/proceeds, not `p_market`
- partial fills update remaining exposure
- promotion reports require a single eligible corrected cohort

### Phase K/L — Shadow, Canary, Promotion Boundary

Goal: observe corrected behavior before any live-money use.

Work shape:

- shadow-only corrected pipeline
- dry-run final intents
- compare legacy versus corrected selections
- canary only after separate operator approval and live-readiness evidence

Acceptance:

- pricing-semantics package does not claim live ready
- live readiness remains blocked by source, calibration, risk, collateral,
  venue facts, monitor/exit, settlement/learning, and operator go evidence

## Review Discipline

Every source implementation packet must have:

- pre-close critic review
- pre-close verifier review
- no packet closure before both pass
- after close, an additional third-party critic and verifier pass before
  freezing the next packet

## Stop Conditions

Stop and re-plan if:

- topology does not admit the exact files
- a phase needs more than its declared semantic lane
- implementation requires production DB mutation or live venue side effects
- old rows would be relabeled as corrected economics
- tests require weakening authority to pass
- executor no-recompute cannot be achieved without broad unplanned rewrite
