# Zeus System Investigation Workflow — Master Plan

```
Created: 2026-06-13
Last reused or audited: 2026-06-13
Authority basis: operator directive 2026-06-13 — "comprehensive adversarial multi-angle
  investigation, divergent, fresh-context + deep-code, strong enough to blueprint a
  ground-up rebuild; both consult and workflow prompts must be extremely detailed and
  precise re: context provision and file references."
```

## 0. Objective & success criteria

**Objective.** Determine — with evidence that survives adversarial refutation — what is
*actually* wrong with the Zeus trading system, across every dimension, and produce a
spec strong enough to either (a) name the single severed wire that unblocks orders, or
(b) blueprint a K≪N ground-up rebuild. The investigation is a *diagnosis instrument*, not
a patch; it must not "fix one order to look productive."

**The symptom under investigation.** A live, funded ($1,162) daemon has placed **ZERO
orders for 7+ days** across 500+ markets. `edli_no_submit_receipts` (written at the
reactor-decline stage) has been silent since **2026-06-06**, i.e. candidates die
*upstream* of the decision fork. A prior session burned 30+ hours producing 0 orders by
debugging belief-quality instead of the dead order path, and enshrined a base-rate
illusion ("buy_no wins ~90%") as "proven alpha."

**Success = the investigation output passes all of:**
1. Every defect in the final ledger carries a **reproduction recipe** (exact query/test/
   file:line) the operator can re-run, and **survived ≥1 adversarial refutation round**.
2. The output distinguishes, with stated evidence, **"our defect hiding real edge"** from
   **"we are fitting noise (no edge exists at this price after cost)"** — pre-registered,
   not concluded post-hoc.
3. The output names the **ordered first cut** (what to do first, second) and what it
   unblocks, plus a **what-to-KEEP** list so a rebuild does not drop solved invariants.
4. Nothing is written to durable memory as fact unless a real result proved it.

## 1. The system under investigation (context for every non-fresh agent)

- **Money path (causal, linear):** `contract semantics → source truth → forecast signal →
  calibration → edge → execution → monitoring → settlement → learning`.
- **Probability chain (replacement, strategy of record, authority
  `docs/authority/replacement_final_form_2026_06_09.md`):** per-model walk-forward de-bias
  → T2 Bayesian precision fusion over 5 providers + regional experts (inverse-variance
  weights, Ledoit-Wolf Σ) → μ*, σ_pred (floor 1.0°C) → settlement σ-shape floor → q =
  settlement-preimage bin integration of N(μ*, σ_pred·k) mixed with uniform (artifact
  `state/sigma_scale_fit.json`, k=1.5833 w=0.2811 MLE on settled outcomes) → q_lcb
  conservative floor + permanent one-sided market-anchor cap → Edge → fractional Kelly →
  size.
- **Submission pipeline (real):** reactor decision (`src/engine/event_reactor_adapter.py`,
  `src/events/reactor.py`) → `execute_final_intent` (`src/execution/executor.py`) → four
  hard pre-submit gates (cutover_guard, risk_allocator, heartbeat_supervisor,
  ws_gap_guard) → `venue_command_repo.insert_command` (INSERT `venue_commands`) →
  `src/execution/venue_adapter.py` → `src/venue/polymarket_v2_adapter.py` →
  `venue_order_facts` (fill truth).
- **DB split (INV-37: cross-DB writes via ATTACH+SAVEPOINT, never independent
  connections):** `state/zeus-world.db` (`edli_no_submit_receipts`, `no_trade_events`,
  calibration), `state/zeus_trades.db` (`venue_commands`, `venue_order_facts`,
  `executable_market_snapshots`), `state/zeus-forecasts.db` (`settlement_outcomes`,
  forecasts).
- **Market structure:** Polymarket K-outcome daily temperature markets per city. Temp
  lands in exactly 1 bin (YES); the other ~K−1 bins resolve NO. So **buy_no wins ~90% by
  base rate and the price already encodes it (NO≈0.90)**. Real edge = `q_lcb > price`
  after cost, *on traded markets* — beating the price, not beating 50%.
- **Known current state:** the live decision path was just collapsed S1–S7 (bin-selection
  unification, commits `b1825c4a07`…`ac27fca1d5`); 6 shadow strategy modules + q-shadow/
  exit flags removed (`e583af06cd`); RiskGuard `dependency_db_locked` storm fixed;
  parallel Claude sessions edit the same live checkout concurrently.

## 2. Architecture — four agent populations, deliberately separated

The signal is the **divergence between populations that never see each other's framing**.

| Pop | Name | Sees | Never sees | Produces |
|---|---|---|---|---|
| P-A | Clean-room architect | Neutral domain brief only (§5.1) | Zeus code, factpack, Zeus vocabulary, this doc | The *ideal* design for its angle |
| P-B | Forensic auditor | factpack + its angle's exact file list | P-A's output | The *actual* state + defect candidates, file:line |
| P-C | Empirical quant | DB paths + its angle's exact tables/queries | code-reading framing, P-A | Findings computed on **real settled data** |
| P-D | Referee / refuter / synth | All of the above, per phase | — | Adjudication, refutation verdicts, final spec |

P-A is the antidote to "we are trapped inside our own architecture." P-C is the antidote
to "we read code about settlement instead of measuring settlement." P-D's refuter is the
antidote to the 15-false-root-cause failure.

## 3. Phase structure

**Phase 0 — fact-pack + mechanical liveness probe** *(orchestrator + 2× haiku, read-only,
inline before the fan-out).* Build `docs/evidence/investigation_2026-06-13/factpack.md`:
- Per-stage last-activity timestamp (the bisection): newest row in `venue_order_facts`,
  `venue_commands`, `edli_no_submit_receipts`, `no_trade_events` — pin which stage died at
  the 06-06 boundary.
- `no_trade_events` / rejection-reason histogram for the last 72h (what code is each
  candidate dying on, and how many).
- Flag-state snapshot (`edli.*`, `replacement_*` from config + DB).
- `git log --since=2026-06-05 --until=2026-06-08` on the order-path dirs (already partly
  captured: S1–S7 + `b1825c4a07` opportunity-selector deletion landed at the boundary).
- codegraph pipeline map of the submit path.
P-B/P-C start from this; **P-A never sees it.**

**Phase 1 — divergent tri-population sweep** *(parallel).* For each of the 16 angles (§4):
one P-A (fresh ideal), one P-B (forensic actual), and — where the angle is empirical
(A3,A5,A12,A13 mandatory; A2,A6,A7,A8 optional) — one P-C (data). ~16 P-A + 16 P-B + 8 P-C
= **40 agents**, all independent, one barrier.

**Phase 2 — per-angle reconciliation** *(P-D referee, 16 agents).* Join (ideal, actual,
empirical) per angle. Output the **divergences = candidate defects**, each tagged:
`MISSING_CAPABILITY | OVER_ENGINEERING | SEMANTIC_CONFLICT | CALIBRATION_EDGE_DEFECT |
MECHANICAL_DEAD_PATH`, money-path-impact-ranked, with a reproduction recipe.

**Phase 3 — adversarial refutation** *(P-D refuter, 3 votes per candidate).* Each skeptic
tries to **kill** the finding: is the ideal naive about a real venue/settlement constraint?
does existing code already handle it? is the empirical claim survivorship-biased? Majority-
refute → dropped. Survivors = hardened. (Cap: top ~24 candidates by impact get 3 votes;
long tail gets 1.)

**Phase 4 — synthesis** *(P-D, small panel).* Reads all hardened defects + factpack +
mechanical probe. Produces, as four sub-roles:
- **Contradiction-miner:** the structural defects that live *between* angles (e.g. "fusion
  says edge on far bins" ⊥ "microstructure says no liquidity there").
- **Keep-lane:** load-bearing correct invariants a rebuild must preserve (DST, direction
  law, settlement preimage, INV-37, the ~50 real fixes) — provenance-audited.
- **Anti-rebuild advocate:** argues the single-wire fix over the grand rebuild; must be
  explicitly overruled with evidence before any rebuild is recommended.
- **Completeness critic:** which angle/claim is unread or unverified → next probe.
Output: the two artifacts (defect ledger + ground-up blueprint with ordered first cut).

**Phase 5 — disk report + decision-grade chat summary.** Written under
`docs/evidence/investigation_2026-06-13/`. Operator decides path.

## 4. The 16 investigation angles

Columns: **CR-Q** = the clean-room design question (P-A); **Forensic files** = exact paths
P-B must Read (P-B may codegraph to expand but must cite file:line for every claim);
**Empirical** = what P-C computes on real data; **Files#** = minimum file count for P-B.

### Data & signal
- **A1 Data acquisition & freshness.** CR-Q: *ideal multi-source numerical-weather
  ingestion for a same-day temperature bet — which sources, what cadence, how is staleness
  defined and enforced?* Forensic: `src/data/{forecast_source_registry, forecast_fetch_plan,
  ecmwf_open_data_ingest, openmeteo_client, ensemble_client, observation_client,
  source_health_probe, dual_run_lock, collection_frontier, tier_resolver,
  source_watermarks}.py`, `src/ingest/forecast_live_daemon.py`. Files#: 12.
- **A2 Mathematical fusion & calibration.** CR-Q: *given N biased correlated forecasts +
  settled history, the ideal posterior over outcome bins — what estimator, how is σ set,
  how is it kept honest out-of-sample?* Forensic: `src/strategy/{market_fusion,
  probability_uncertainty, correlation_shrinkage, correlation, oracle_estimator,
  selection_shrinkage}.py`, `src/data/{replacement_forecast_emos_identity,
  replacement_forecast_production, replacement_forecast_materializer}.py`,
  `src/contracts/probability_arithmetic.py`, `state/sigma_scale_fit.json`. Files#: 10.
  Empirical(opt): calibration reliability of stored q vs settled outcome.
- **A3 OOS skill vs benchmark + overfitting audit.** CR-Q: *how do you prove a forecast
  has real skill — against which benchmarks (market price, single best model,
  climatology) and with what out-of-sample protocol?* Forensic: `src/strategy/
  benchmark_suite.py`, `src/analysis/{settlement_skill_attribution, shadow_comparator}.py`.
  **Empirical(mandatory):** for settled markets, does fused q beat market price / ECMWF /
  climatology OOS (proper scoring rule, walk-forward); count free params (k, w, weights)
  vs n settled samples; data-snooping check. Files#: 3.

### Market & alpha
- **A4 Live market microstructure.** CR-Q: *what must you know about a Polymarket CLOB book
  before sizing a quote — depth, both sides, min tick/size, taker vs maker fill?* Forensic:
  `src/data/{orderbook_depth_walk, polymarket_client, market_scanner}.py`,
  `src/events/{orderbook_projector, opportunity_book}.py`,
  `src/contracts/executable_market_snapshot.py`. Files#: 6.
- **A5 Market efficiency / counterparty / adverse-selection** *(existence question).*
  CR-Q: *who is on the other side of a weather-bin quote; if price already encodes the base
  rate, where can a modeler still have edge, and when your maker quote fills are you
  adversely selected?* Forensic: `src/strategy/{mainstream_agreement, market_phase,
  market_phase_evidence}.py`. **Empirical(mandatory):** distribution of (our intended side
  vs settled outcome) conditioned on price bucket; is realized edge concentrated where the
  book is thin/early (real) or uniform (illusion)? Files#: 3.
- **A6 Market opportunity & selection.** CR-Q: *across cities/horizons/bins, where is the
  exploitable opportunity and which subset is worth trading at all?* Forensic:
  `src/strategy/{market_analysis, market_analysis_family_scan, portfolio_rotation,
  selection_family}.py`, `src/analysis/event_opportunity_report.py`,
  `src/events/{opportunity_selector, opportunity_event}.py`. Files#: 7.
- **A7 Total friction accounting** *(kill-test).* CR-Q: *enumerate every cent between
  decision and settled PnL; what edge threshold must clear it?* Forensic: `src/strategy/
  fees.py`, `src/contracts/{fee_authority, slippage_bps, executable_cost_curve, tick_size,
  venue_submission_envelope, vig_treatment}.py`, `src/execution/collateral.py`. Files#: 8.
  Empirical(opt): realized cost vs modeled cost on any historical fills.
- **A8 Latency / alpha-decay.** CR-Q: *how fast does a same-day weather edge decay, and what
  end-to-end latency budget keeps it tradeable?* Forensic: `src/control/{freshness_gate,
  ws_gap_guard}.py`, `src/data/replacement_current_value_serving.py`,
  `src/events/continuous_redecision.py`. Files#: 4.

### Decision & execution
- **A9 Trade-decision path / the reactor gauntlet** *(why 0 orders).* CR-Q: *the minimal
  honest decision from belief+quote+cost to a sized order — how many gates, in what order?*
  Forensic: `src/engine/event_reactor_adapter.py`, `src/events/{reactor, decision_engine,
  candidate_evaluation, candidate_binding}.py`, `src/execution/executor.py`,
  `src/contracts/{no_trade_reason, rejection_reasons, alpha_decision}.py`. Files#: 9.
- **A10 Execution & order lifecycle.** CR-Q: *the ideal submit→ack→fill→position→settle→exit
  state machine with idempotency and recovery.* Forensic: `src/execution/{executor,
  live_executor, venue_adapter, fill_tracker, exit_lifecycle, command_bus,
  order_truth_reducer, command_recovery}.py`, `src/venue/polymarket_v2_adapter.py`,
  `src/state/venue_command_repo.py`, `src/events/{edli_trade_fact_bridge,
  live_order_reconcile}.py`. Files#: 12.
- **A11 Runtime observability / debuggability.** CR-Q: *what telemetry lets an operator
  answer "why did market X not trade at T" in one query?* Forensic: `src/state/{no_trade_events,
  decision_chain, chronicler}.py`, `src/events/{no_submit_receipts, no_submit_projection}.py`,
  `src/analysis/regret_decomposer.py`. **Empirical:** can the factpack queries actually
  answer the why-no-trade question, or is the data missing? Files#: 5.

### Evaluation & truth
- **A12 Settlement-grading & evaluation process.** CR-Q: *the honest way to grade whether a
  decision had edge using settlement truth — what is measured, on which population?*
  Forensic: `src/contracts/{settlement_semantics, settlement_outcome, settlement_resolution,
  graded_receipt}.py`, `src/analysis/{settlement_guard_report, evidence_report}.py`,
  `src/state/settlement_writers.py`. **Empirical(mandatory):** recompute realized after-cost
  win-rate/EV on the correct TRADED-vs-untraded population; reproduce or refute the
  "+5…+16¢/$1" claim. Files#: 7.
- **A13 Survivorship/selection-bias audit of the system's own evidence.** CR-Q: *how do you
  audit a system's self-reported edge for survivorship and selection bias?* Forensic:
  `src/analysis/{deterministic_edge_report, promotion_readiness, live_readiness_tribunal,
  promotion_proof_router}.py`, `docs/evidence/shadow_comparisons/`. **Empirical(mandatory):**
  re-derive every edge claim's denominator; is it settled-only / gate-passing-only /
  winning-side-only? Files#: 4.

### Code & structure
- **A14 Architecture / framework.** CR-Q: *the right module decomposition for this pipeline;
  what does a 14.8k-line reactor file signify?* Forensic: `src/engine/{event_reactor_adapter,
  cycle_runner, dispatch, ddd_wiring, evaluator}.py`, `architecture/test_topology.yaml`,
  root `AGENTS.md`. Files#: 7.
- **A15 Semantic conflicts + concurrency/state-integrity.** CR-Q: *which invariants in a
  trading system are most prone to silent contradiction (time, direction, settlement
  preimage), and how is cross-store atomicity guaranteed under concurrent writers?*
  Forensic: `src/contracts/{time_semantics, dst_semantics, settlement_semantics,
  execution_price, fx_classification, probability_arithmetic}.py`, `src/state/{canonical_write,
  connection_pair, db, chain_reconciliation, db_writer_lock}.py`. Files#: 11.
- **A16 Simplicity vs functionality + accretion pathology.** CR-Q: *how does a decision path
  accrete to 174 gates, and what process prevents regrowth after a collapse?* Forensic:
  `git log` of `src/engine/event_reactor_adapter.py`; `src/contracts/no_trade_reason.py` enum
  size; `docs/operations/kcut_decision_path_collapse_2026-06-13.md`; the task ledger.
  Files#: 3 + git.

## 5. Prompt templates (full fidelity — materialized per angle by the Workflow script)

### 5.1 Clean-room architect (P-A) — neutral brief, ZERO Zeus contamination
```
You are a senior quant designing a trading system from first principles. You have NO
knowledge of, and must NOT ask about, any existing implementation. Domain facts (all you
get): A venue runs daily per-city markets on "will city C's max temperature tomorrow fall
in bin B?", where the day's temperature lands in exactly one of K contiguous temperature
bins; each bin is a separate YES/NO contract with a CLOB order book (limit orders, taker
and maker, a minimum tick and size, a fee). Settlement is objective from the official
station reading. You may hold either side of any bin. You have access to multiple public
numerical weather forecast providers (global ensembles + regional models) and historical
settled outcomes.

YOUR ANGLE: {angle_title} — {clean_room_question}

Design the IDEAL approach for THIS angle only, from first principles. State: (1) the
objective this angle must achieve for the system to make money; (2) the ideal mechanism;
(3) the invariants a correct implementation must hold; (4) the failure modes that silently
destroy edge if violated; (5) the 3 hardest design decisions and your call on each. Be
concrete and quantitative. Do NOT hedge. ~600-1000 words. Return as the schema provided.
```

### 5.2 Forensic auditor (P-B) — deep code, evidence-bound
```
You are a forensic code auditor. Read the factpack first: {factpack_path}. Then Read EVERY
file in this list (do not skip; this is the minimum, expand via codegraph if a call leads
out): {file_list}. For each claim you make you MUST cite file:line. Do not theorize beyond
what the code shows; an unread path is "unread", not "fine".

YOUR ANGLE: {angle_title}. Report: (1) what this angle ACTUALLY does, mechanism + file:line;
(2) every defect — broken logic, dead code, an invariant violated, an over-built gate, a
semantic contradiction — each with file:line evidence and a one-line reproduction recipe
(a test, a grep, or a query); (3) what is notably ABSENT vs what a sound system needs; (4)
your provenance verdict on the angle's core files: CURRENT_REUSABLE | STALE_REWRITE |
DEAD_DELETE | QUARANTINED, with the law regime each was written under. Anchor on runtime-
risk surfaces first. Return as the schema provided.
```

### 5.3 Empirical quant (P-C) — compute on real settled data
```
You are a quant analyst. You answer ONLY with computation on real data. DBs (open mode=ro,
ISO-T literal bounds, timeout every query): world={world_db}, trades={trades_db},
forecasts={forecasts_db}. Relevant tables: {table_hints}.

YOUR ANGLE: {angle_title} — {empirical_question}. Write and RUN the queries (sqlite3,
read-only). For every finding, include the exact query and the row counts so it is
reproducible. Explicitly state your population and denominator (settled-only? gate-passing-
only? which side?) and whether a survivorship/selection bias inflates the result. If the
data needed to answer does not exist, say so — that absence is itself a finding. Do NOT
read source code for behavior; measure outcomes. Return as the schema provided.
```

### 5.4 Referee (P-D, Phase 2)
```
You reconcile three independent reports on ONE angle: IDEAL (clean-room, knew nothing of
the code), ACTUAL (forensic, file:line), EMPIRICAL (data, may be absent). Your job: emit
the DIVERGENCES = candidate defects. For each: a title; the tag (MISSING_CAPABILITY |
OVER_ENGINEERING | SEMANTIC_CONFLICT | CALIBRATION_EDGE_DEFECT | MECHANICAL_DEAD_PATH); the
evidence (file:line and/or query from ACTUAL/EMPIRICAL — never from IDEAL alone, the ideal
is a yardstick not evidence); a money-path impact score 0-100; a reproduction recipe.
Discard divergences that are only the ideal being naive about a real constraint the ACTUAL
correctly handles. Return as the schema provided.
INPUTS: ideal={...} actual={...} empirical={...}
```

### 5.5 Adversarial refuter (P-D, Phase 3)
```
You are a hostile skeptic. Default verdict: REFUTED. Here is one candidate defect with its
evidence: {candidate}. Try to KILL it: (a) is the "ideal" it deviates from naive about a
venue/settlement/latency constraint the real system must obey? (b) does existing code
already handle it (check the cited files)? (c) is the empirical claim survivorship- or
selection-biased, or under-powered (n too small)? (d) would "fixing" it actually move a
real order to a fill, or is it cosmetic? Only return real=true if it survives ALL four.
Return {real: bool, reason, strongest_counter, would_unblock_a_fill: bool}.
```

### 5.6 Synthesis panel (P-D, Phase 4) — four sub-roles, see §3.

## 6. Output schemas (Workflow `schema` option — agents return validated JSON)

- **P-A** `{angle, objective, ideal_mechanism, invariants[], failure_modes[], hard_decisions[]}`
- **P-B** `{angle, actual_mechanism[], defects:[{title,file_line,evidence,repro,severity}],
  absent[], provenance:[{file,verdict,law_regime}]}`
- **P-C** `{angle, findings:[{claim,query,row_counts,population,bias_risk}], data_absent[]}`
- **P-D ref** `{angle, candidates:[{title,tag,evidence,impact,repro}]}`
- **P-D refuter** `{real, reason, strongest_counter, would_unblock_a_fill}`
- **P-D synth** `{defect_ledger:[...], contradictions[], keep_invariants[], rebuild_blueprint,
  ordered_first_cut[], unverified[]}`

## 7. Context-provision & file-reference rules (the operator's explicit ask)

- **P-A gets a fixed neutral brief and NOTHING else** — no repo path, no factpack, no Zeus
  term. Contamination check: a P-A output that references a Zeus filename/flag is discarded
  and re-run.
- **P-B gets the factpack + its angle's explicit file list (count in §4) and must Read all
  of them**; every claim file:line; codegraph allowed to follow calls outward. A P-B that
  cites <Files# files is incomplete and re-run.
- **P-C gets the three DB paths + table hints + the empirical question**; must include the
  query and row counts for each finding; must not infer behavior from source.
- **P-D gets only the structured upstream outputs**, never raw repo, to keep adjudication
  about evidence not vibes.
- Shared **factpack** is the single ground-truth artifact; written once in Phase 0, read by
  all P-B/P-C, never by P-A.

## 8. Scale, cost, model tiering

~Phase0: orchestrator + 2 haiku. Phase1: 16 P-A (sonnet) + 16 P-B (sonnet, opus for
A2/A9/A14/A15) + 8 P-C (sonnet). Phase2: 16 P-D referee (sonnet). Phase3: ~24×3 + tail
refuters (sonnet). Phase4: 4 synth sub-roles (opus). **≈ 40 + 16 + ~80 + 4 ≈ 140 agent-
calls** (the "60" was the Phase-1 core; refutation dominates the tail). Concurrency cap
auto-applies; pipeline() not barrier between angle stages so a fast angle verifies while a
slow one still reads. Worktree isolation NOT needed (read-only investigation).

## 9. ChatGPT first-principles consult brief (what we ask GPT to refine BEFORE running)

Ask GPT (Pro, deep) to refine THIS plan from first principles, specifically: (1) is the
16-angle taxonomy complete and non-overlapping, or is a money-destroying dimension missing?
(2) is the clean-room/forensic/empirical separation actually sufficient to prevent the
"trapped in our own architecture" and "read-code-instead-of-measuring" failures, or is
there a contamination leak? (3) are the **empirical query designs the right statistical
tests for edge existence** (proper scoring vs market, survivorship controls, power/n)? (4)
does the refutation gate actually stop false root-causes, or can a plausible-wrong finding
still survive? (5) does the synthesis produce an *actionable ordered first cut*, or just a
report? (6) is ~140 agent-calls right-sized — what is the 20% that yields 80%, and what is
pure ceremony to cut? (7) the single highest-value angle or query we have UNDER-specified.
Demand concrete, opinionated edits, not validation.

## 10. Deliverables
- `docs/evidence/investigation_2026-06-13/factpack.md` (Phase 0).
- `docs/evidence/investigation_2026-06-13/defect_ledger.md` (hardened, reproducible).
- `docs/evidence/investigation_2026-06-13/rebuild_blueprint.md` (K≪N target + ordered first cut + keep-list).
- Decision-grade chat summary; nothing to durable memory unless a real result proved it.
```
Status: DRAFT — pending ChatGPT first-principles refinement (§9), then operator approval, then build the Workflow script that materializes §5 templates over §4 angles.
```
