# Task 2026-04-19 — Execution-State Truth Upgrade

Status: refreshed planning package  
Branch audited: `copilot/task-model-implementation-plan` workspace snapshot on 2026-04-26  
Target landing path: `docs/operations/task_2026-04-19_execution_state_truth_upgrade/`

## 1. Executive ruling

The supplied review remains valuable after removing claims already fixed by the latest branch. The mainline remains **Execution-State Truth Re-Architecture**, not posterior/model sophistication.

The corrected ruling is:

1. Zeus must first preserve state semantics and authority over order/position truth.
2. Zeus must survive incomplete venue/chain truth by recording `UNKNOWN` / `REVIEW_REQUIRED`, not by fabricating certainty.
3. Only after execution truth, recovery, and authoritative de-risk are closed should market eligibility and persistent alpha governance become the main path.

## 2. Latest-branch validation summary

### Confirmed fixed or materially improved; remove from active indictment

- **DB before JSON export** is fixed in current runtime: `cycle_runner.run_cycle()` uses `commit_then_export(...)` after `store_artifact(...)`, with portfolio/tracker/status writes exported afterward.
- **FDR family split** is fixed at the helper level: `selection_family.py` contains `make_hypothesis_family_id()` and `make_edge_family_id()`; the old claim that family identity is still only strategy-key-mixed is stale.
- **Portfolio degraded loader no longer hard-shuts the whole cycle**: `cycle_runner.py` runs degraded risk tick, suppresses entries through risk level, and keeps monitor/exit/reconciliation lanes alive.
- **RED is no longer purely entry-block-only**: current runtime marks non-terminal active positions with `exit_reason="red_force_exit"` for the exit lane.

### Confirmed still valuable / unresolved

- **Degraded export still labels as verified**: `_TRUTH_AUTHORITY_MAP` in `src/state/portfolio.py` maps `"degraded"` to `"VERIFIED"`.
- **No durable venue command journal exists**: there are no `venue_commands` / `venue_command_events` authority tables or repository API.
- **Order side effects still happen before durable command authority**: `cycle_runtime.execute_discovery_phase()` calls `execute_intent(...)`; `executor._live_order()` calls `PolymarketClient.place_limit_order(...)`; only after the result returns does cycle runtime materialize/log position and execution report.
- **Execution capability labels still exceed implementation**: `create_execution_intent()` emits `slice_policy="iceberg"`, `reprice_policy="dynamic_peg"`, and `liquidity_guard=True`; current execution path still submits one live limit order.
- **UNKNOWN exists but is not command-aware**: `ChainState.CHAIN_UNKNOWN` guards some empty-chain cases, but unresolved venue commands are not part of the classifier, and mixed fresh/stale empty responses still collapse at portfolio scope.
- **Rescue still carries fabricated time**: pending rescue can write `entered_at="unknown_entered_at"` when no real entry timestamp exists.
- **RED target is not yet command-authoritative**: the current sweep is a real improvement, but it marks local exit intent; it does not yet emit durable `CANCEL` / `DERISK` / `EXIT` venue commands.
- **CLOB V2 readiness is absent in live client path**: `PolymarketClient` still wraps the current `py_clob_client` constructor against `https://clob.polymarket.com`; no explicit V2 preflight/cutover generation gate exists.
- **Persistent alpha spending remains future work**: current FDR scope is improved per snapshot/family, but no durable cross-cycle alpha ledger was found.

## 3. Authority and evidence order

This package uses the following order when sources conflict:

1. Runtime code and machine-checkable tests in the current branch.
2. Machine manifests and current authority law.
3. Current operations pointer.
4. Official venue/settlement documentation for external facts.
5. The supplied review as evidence input.
6. Historical docs, stale tests, and comments.

External venue fact correction: the supplied review cites a 2026-04-22 CLOB V2 cutover. Current public Polymarket migration documentation indicates **2026-04-28 ~11:00 UTC**, approximately one hour of downtime, open-order wipe, no backward compatibility for old SDK integrations, V2 testing at `https://clob-v2.polymarket.com`, and production V2 served from `https://clob.polymarket.com` after cutover. Use official venue docs at implementation time.

## 4. Facts / decisions / open questions / risks

### Facts

- Zeus authority law already says DB/event truth outranks JSON/status projections.
- Current runtime has commit-then-export and degraded read-only cycle behavior.
- Current runtime still lacks pre-side-effect venue command persistence.
- Current runtime still exposes order placement through `PolymarketClient.place_limit_order(...)` without a command boundary.
- Current chain reconciliation can return `CHAIN_UNKNOWN`, but it is not tied to unresolved venue command truth.

### Decisions

- Mainline: Execution-State Truth Re-Architecture.
- Immediate posture: no unrestricted live entry; at most monitor/exit/recovery until P0/P1/P2 gates close.
- P0 is narrow hardening; P1 introduces durable command truth; P2 closes UNKNOWN/RED semantics; P3 contains settlement/market and alpha-spending pressure.
- `execution_report` remains telemetry. `positions.json` remains projection.

### Open questions

- Final schema ownership for `venue_commands` and `venue_command_events`.
- Exact command-event grammar and allowed state transitions.
- Approved V2 Python client/package/version and authentication surface.
- Recovery precedence when command journal, venue REST, user websocket, chain, and operator input conflict.
- Idempotency key format and replay behavior.

### Risks

- Implementing model/eligibility work first can hide execution authority loss.
- Adding command schema without gateway-only enforcement creates duplicate truth planes.
- Treating V2 base URL continuity as compatibility can silently break order semantics.
- Stale tests/comments can guide future agents toward undoing fixed work.

## 5. Mainline objective

Build a durable execution-state authority spine:

`decision -> persisted VenueCommand -> command event -> venue side effect/recovery -> position event/projection -> JSON/status projection`

The success condition is not prettier status text. It is crash/replay evidence that Zeus can say either “I know this order state” or “I do not know; new entries are blocked and recovery/review is active.”

## 6. Non-goals

- Do not implement a Bayesian/model upgrade in this package.
- Do not build queue-position or impact simulation before user-order/fill ground truth exists.
- Do not broaden market universe before eligibility and settlement containment are explicit.
- Do not automate quarantine self-healing before `UNKNOWN` / `REVIEW_REQUIRED` contracts are mature.
- Do not re-enable unrestricted live entry before P0/P1/P2 verification.

## 7. Package document map

- `project_brief.md` — current-branch ruling and review-triage outcome.
- `prd.md` — product requirements and acceptance criteria.
- `architecture_note.md` — target authority model and data/state architecture.
- `implementation_plan.md` — phase-by-phase implementation plan.
- `task_packet.md` — downstream coding-agent handoff.
- `verification_plan.md` — tests, failure drills, and live-entry re-enable gates.
- `decisions.md` — accepted decisions and remaining blockers.
- `not_now.md` — explicit deferrals.
- `work_log.md` — planning refresh evidence.
