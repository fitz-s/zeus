# Zeus Investigation — Agent Prompt Library v2 (GATED, post-consult + live evidence)

```
Created: 2026-06-13
Authority basis: operator directive 2026-06-13 (write the agent prompts in full) +
  ChatGPT consult verdict (docs/evidence/investigation_2026-06-13/chatgpt_consult_verdict_digest.md) +
  live tracker evidence (docs/evidence/investigation_2026-06-13/live_state_tracker.md).
Supersedes the flat 16-angle/140-agent design in system_investigation_workflow_2026-06-13.md §3/§8.
```

## What the live evidence already settled (so these prompts target reality, not theory)
The reactor is ALIVE and cycling every ~1–2 min; EMS upstream fresh (114k rows/3h); all 3 daemons healthy; 40 historical Zeus fills confirmed. The blockers are now specific:
- **B1 (mechanical submit gate):** the M5 ws-gap submit latch is frozen `allow_submit=False` since **2026-06-12T22:58 UTC** by ONE unresolved `position_drift` reconcile finding `5bbc2be2-350c-4bdf-ac0e-f080e41f9012` (token `2599807256…`, context `ws_gap`). Blocks ALL new submission regardless of edge. → the predicted **first cut**.
- **B2 (edge / decision gate):** every current candidate rejected, dominant reason `capital_efficiency_lcb_ev` (+ `coverage_unlicensed_tail`, `direction_law`, `TRADE_SCORE_NON_POSITIVE`). Some show large `ev_per_dollar` yet die. Honest-no-edge vs over-conservative gate = the edge question.
- **B3 (open-position risk):** a live Beijing-2026-06-14 NO position has a blind exit organ (`BELIEF_AUTHORITY_FAULT`, stale belief 87 cycles).
- Secondary: Gamma empty event-lists (FDR-stuck families), `command_recovery` projection-repair error, substrate-warm backlog, boot/current SHA divergence (in grace).

So the gated workflow's job is no longer "find the dead stage" (found) — it is: (Gate 0) **confirm B1 is safe to clear + clear it**, (Gate 1) audit the gate-path that produced B1/B3 so it can't silently re-freeze, (Gate 2) decide if B2 rejections are honest, (Gate 3) refute + synthesize the ordered cut. Reserve fan-out only if escalation fires.

---

## GLOBAL PREAMBLE — prepend to EVERY agent prompt below
> You operate against a PINNED snapshot. The orchestrator gives you `{code_sha, world_db_hash, trades_db_hash, forecasts_db_hash, config_hash, asof_utc}`; echo all of them in your output — a claim without provenance is invalid. Read DBs from the read-only `.backup` copies the orchestrator names; never open the live DBs writable, never edit code, never signal a daemon (your only write is your one report file). OUTPUT-MANGLER HAZARD: a session hook reformats colon-heavy / multi-line tool output into `N matches in NF` and drops data — route raw query output to a `/tmp/*.txt` file, prefer scalar queries, and in your FINAL reply summarize in plain prose with minimal colons (no raw `key=value` dumps). Every factual claim carries `file:line` or the exact query + row count. You are a [haiku: locator — return file:line + verbatim excerpts ONLY, never existence verdicts] [sonnet/opus: may return verdicts].

---

# GATE 0 — Confirm + clear the submit blocker (run FIRST; 3–4 agents)

### G0.1 — Provenance freeze (P-witness, sonnet)
Pin the snapshot the whole investigation reads against. Run: `git rev-parse HEAD`, `git status --porcelain`, `git log -n 20 --decorate --oneline`. Capture the 3 daemon command-lines + start times (`ps -o pid,lstart,etime -p 449 67248 75222`). `sqlite3 state/zeus-world.db ".backup docs/evidence/investigation_2026-06-13/snap/world.db"` and same for `zeus_trades.db`, `zeus-forecasts.db` (use `.backup`, not `cp`, while daemons write). `sha256sum` each snapshot DB + `config/settings.json`. Write `docs/evidence/investigation_2026-06-13/snap/provenance.json` `{code_sha, status_dirty[], daemon_cmds[], db_hashes{}, config_hash, asof_utc}`. Reply with the provenance block only.

### G0.2 — Latch-resolution safety verdict (P-E execution-witness, sonnet)  ← gates the first cut
The ws-gap submit latch is frozen by finding `5bbc2be2-350c-4bdf-ac0e-f080e41f9012` (`position_drift`, token `2599807256…`). Determine whether resolving it is SAFE or needs a position-lot repair first. Read (ro): full `evidence_json` of that finding; whether the token is a live nonzero position in any position/lot table across both `zeus-world.db` and `zeus_trades.db`; what the drift is (Zeus-holds-venue-doesn't vs venue-shows-Zeus-didn't-record vs size-mismatch), with numbers. Read the M5 reconcile path to learn how the latch is meant to clear: `src/execution/exchange_reconcile.py`, `src/control/ws_gap_guard.py`, and the `_assert_ws_gap_allows_submit` site in `src/execution/executor.py`. Output: `{drift_kind, token_live_position: bool, position_size, safe_to_resolve: yes|no|unclear, resolution_mechanism: "auto_on_chain_sync"|"manual_resolve_call"|"position_lot_repair"|"operator_ack", exact_command_or_code_path, risk_if_cleared_with_drift_unresolved}`. Do NOT resolve it — produce the verdict + the exact safe procedure.

### G0.3 — Stage-funnel + boundary diff (P-B-diff, sonnet)
Build the per-cycle stage-funnel 2026-06-04→now to confirm the funnel is healthy upstream of B1 and pin when the latch first closed vs when decisions stopped. Stage→source: active families & fresh books → `executable_market_snapshots.captured_at`; forecasts → `zeus-forecasts.db`; decision evals/declines → `edli_no_submit_receipts.created_at`; `no_trade_events`; submissions → `venue_commands.created_at`; fills → `venue_order_facts`; latch state → grep `M5 WS-gap` in `logs/zeus-live.log`. Also `git show --stat b1825c4a07` and `git show b1825c4a07 -- src/events/opportunity_selector.py src/events/opportunity_book.py src/engine/event_reactor_adapter.py` to settle whether S7 removed only the selector on/off GATE (one path kept) or candidate materialization. Output the stage-funnel JSON (schema in the consult digest) + the b1825c4a07 verdict (`gate_only` | `materializer_removed`) with the diff evidence.

**GATE-0 EXIT:** if G0.2 = `safe_to_resolve: yes` → the first cut is to clear `5bbc2be2` and watch the latch reopen + a real submission attempt flow. If `no/unclear` → first cut is the position-lot repair G0.2 names. Either way proceed to Gate 1 (so the gate that froze can't silently re-freeze) — do not stop at clearing one finding.

---

# GATE 1 — Narrow forensic on the gate-path (8 agents). P-B-blind ⊕ P-B-diff.
> P-B-blind preamble: you see ONLY the symptom (`new entry submissions blocked / all candidates rejected`) + your assigned files + the pinned DB snapshot. You do NOT see any boundary-commit hypothesis, the latch finding id, or prior root-cause theories — find the dead/over-tight stage independently.
> P-B-diff preamble: you additionally see the boundary commits (S1–S7, b1825c4a07) + the B1/B2/B3 findings; produce last-good/first-bad and confirm or refute each.

| ID | Angle | Files to Read (all of them) | Blind? |
|---|---|---|---|
| R-WSGAP | ws-gap latch / reconcile / submit-gate lifecycle (owns B1) | `src/control/ws_gap_guard.py`, `src/execution/exchange_reconcile.py`, `src/execution/executor.py` (`_assert_ws_gap_allows_submit`), `src/state/chain_reconciliation.py` | blind+diff |
| R-CAPEFF | the `capital_efficiency_lcb_ev` admission gate (owns B2) | `src/engine/event_reactor_adapter.py` (capital_efficiency site), `src/strategy/{utility_ranker,kelly,risk_limits}.py`, `src/contracts/edge_context.py` | blind+diff |
| R-EXIT | exit organ / belief authority (owns B3) | `src/engine/{monitor_refresh,position_belief}.py`, `src/execution/{exit_lifecycle,exit_safety,day0_hard_fact_exit}.py`, `src/strategy/exit_constrained_posterior.py` | blind |
| R-IDENT | contract universe / token identity / market lifecycle | `src/data/{market_scanner,polymarket_client}.py`, `src/contracts/{settlement_semantics,settlement_resolution,settlement_outcome}.py`, `src/events/{opportunity_event,candidate_binding}.py` | blind |
| R-FDR | Gamma empty-event / FDR-stuck families | `src/strategy/{fdr_filter,market_analysis_family_scan}.py`, `src/data/market_scanner.py` | blind |
| R-CAPITAL | capital / collateral / risk-allocator / account readiness | `src/execution/collateral.py`, `src/riskguard/{riskguard,policy,risk_level}.py`, `src/control/{cutover_guard,heartbeat_supervisor}.py`, `src/state/{collateral_ledger,portfolio}.py` | blind |
| R-LIFECYCLE | submit/ack/fill/recover lifecycle + the projection-repair error | `src/execution/{executor,live_executor,venue_adapter,fill_tracker,command_bus,command_recovery,order_truth_reducer}.py`, `src/venue/polymarket_v2_adapter.py`, `src/state/venue_command_repo.py` | diff |
| R-STATE | state integrity / config / cross-DB atomicity / concurrent-edit | `src/state/{canonical_write,connection_pair,db,chain_reconciliation,db_writer_lock}.py`, `config/settings.json`, live-daemon SHA vs tree | blind+diff |

Each agent output: `{angle, actual_mechanism[], defects:[{title,file_line,evidence,repro,severity,would_block_submission_or_kill_pos_edge:bool}], absent[], provenance{...}}`. RULE for every defect: state whether it would, on its own, block a submission or kill a genuinely +EV candidate — if neither, tag it `DEFECT_NO_CURRENT_IMPACT`.

---

# GATE 2 — Edge-existence smoke (4 agents: P-C ×3 + P-E ×1). Go/no-go, not the full alpha program.
Embed the consult's event-level protocol. Unit of analysis = **city-date market family**, NOT the K individual contracts (counting K NO-contracts as K wins IS the base-rate illusion). Populations U_all_observed / U_full_data / U_policy_eval / U_submit / U_fill / U_missing with counts. Scores: multiclass log-score (primary), Brier, RPS (ordered bins). Benchmarks (frozen, point-in-time): market-implied distribution at decision time, walk-forward best single model, simple ensemble, climatology. Walk-forward: group-split by `event_id`, embargo so unsettled labels can't leak. Decision eligibility uses `q_lcb`; calibration scoring uses `q`. Adverse-selection: post-fill markout + settlement-conditional-on-fill. Survivorship: every statistic states its denominator. Power: `n_eff` = event-cluster count; ≈200–300 events to detect 5¢/share, ≈550–800 for 3¢; `<100` ⇒ exploratory; wide CI ⇒ `UNDERPOWERED` not "no edge".

- **G2.1 (P-C) skill-vs-market:** does fused `q` beat the **market-implied price** OOS on settled families (paired event-clustered bootstrap, log/RPS)? If it beats climatology but not price → forecast skill, NOT tradeable alpha.
- **G2.2 (P-C) capital_efficiency honesty:** take the exact rejected live candidates from `logs/zeus-live.log` (KL q_lcb=0.039/price=0.008/ev=3.9; Tel Aviv ev=26.5; etc.). Reconstruct what `capital_efficiency_lcb_ev` computed for each and whether the rejection is honest (true after-cost q_lcb ≤ price-floor) or an over-conservative/miscalibrated cut killing real +EV. This directly tests B2.
- **G2.3 (P-C) survivorship audit:** re-derive the prior "+5…+16¢/$1" claim's denominator (settled-only? gate-passing? winning-side?) and the honest unbiased version. Verdict per claim.
- **G2.4 (P-E) traded-vs-counterfactual PnL:** realized after-cost PnL on `U_submit/U_fill` (the 40 fills) vs the counterfactual untraded `U_policy_eval` at executable prices. Portfolio-level per market-family, not summed binaries.

P-C/P-E schema: the consult digest's `{snapshot_provenance, population_counts{...,n_eff_event}, benchmark_scores[], trading_ev[], queries[], data_absent[], verdict: REAL_EDGE|NO_ACTIONABLE_EDGE|UNDERPOWERED|DATA_INVALID}`.

---

# GATE 3 — Refute + synthesize (5 agents)
### G3.1/G3.2 mechanical refuters (sonnet, single-valid-refutation kill)
Given a candidate root-cause + its evidence, try to KILL it. It may be `ROOT_CAUSE` only if it passes ALL 7: (1) stage-locality — at/before the first dead stage; (2) temporal-fit — present across the whole silent window; (3) breadth — affects ~all markets not one city/bin; (4) reproduction — a pinned query/replay reproduces the missing transition; (5) **intervention** — a minimal revert/patch advances the SAME replayed market/cycle to the next money-path stage; (6) money-path-movement — that next stage is candidate-eval / receipt / venue_command / fact, not cosmetic; (7) no later-stage contradiction. ONE failed kill-test → demote to `CONTRIBUTING_DEFECT` / `OBSERVABILITY_GAP` / `UNPROVEN`. Schema: `{candidate_id, status, kill_tests:[{test,result,evidence}], causal_stage, intervention_proven:bool, would_move_real_order_path:bool, strongest_counter, required_next_probe}`.
### G3.3 empirical refuter (sonnet)
An edge claim survives only with: point-in-time reconstruction, market-implied benchmark, proper score, after-cost executable EV, separated traded/untraded denominators, event-clustered n_eff+CI, no base-rate-win-rate argument, no settled/gate/winning-only denominator. Else auto-`REFUTED`.
### G3.4 synthesis → executable cut (opus)
Emit the consult's `ordered_first_cut[]` contract: each item `{rank, hypothesis_id, classification, affected_stage, why_first, evidence[], local_verification_steps[], minimal_patch_spec{files,behavior_change,non_goals}, expected_stage_delta{before,after}, risk_cap{paper_first,max_live_notional,rollback}, kill_criteria[], fanout_gate}` + `do_not_touch_first[]` + `keep_invariants[]` + a `targeted_fix_vs_rebuild` decision (targeted if the dead stage reproduces + a minimal patch advances the path; rebuild only if replay proves the architecture cannot express the minimal honest path OR edge is provably absent with adequate n_eff).
### G3.5 anti-rebuild advocate (opus)
Argue the single-wire fix over any rebuild; must be explicitly overruled with evidence before a rebuild is recommended.

---

# ESCALATION — full fan-out (RESERVE; run ONLY if a condition fires)
Conditions: provenance missing so the dead stage can't be pinned; replay ⊥ synthetic-candidate; the minimal patch does NOT move the money path; multiple independent dead stages proven; Gate-2 shows strongly-negative after-cost EV with adequate n_eff (forces strategy/rebuild call); or contract-identity/settlement correctness materially uncertain.

Reserve populations (materialize per the templates in `system_investigation_workflow_2026-06-13.md` §5):
- **P-A clean-room ×8** (neutral brief, zero Zeus contamination) on the design-sensitive angles only: contract identity, edge evidence, microstructure, capital/risk, decision liveness, execution lifecycle, observability, minimal-kernel.
- **Full 16-angle P-B forensic + P-C empirical** per the corrected taxonomy (consult digest): R1 identity, R2 acquisition/freshness, R3 point-in-time lineage/leakage, R4 fusion/calibration, R5 edge+grading+provenance (merged), R6 efficiency/counterparty, R7 opportunity/candidate-gen, R8 microstructure, R9 friction/threshold, R10 latency/lifecycle-timing, R11 capital/collateral/account, R12 decision-gate liveness, R13 execution lifecycle, R14 observability, R15 state/config/deploy, R16 minimal-kernel/rebuild.

---

# WORKFLOW SCRIPT SHAPE (how these prompts get run)
`phase('Gate0')` run G0.1→G0.2→G0.3 (pipeline; G0.2 gates the first cut). Read G0 → decide first cut. `phase('Gate1')` parallel R-* (blind ⊕ diff as marked). `phase('Gate2')` parallel G2.* . `phase('Gate3')` pipeline: refuters → synthesis. Escalation `workflow()` to the reserve fan-out only if the gate condition trips. All agents `isolation` NOT needed for reads but EVERY agent gets the pinned `{code_sha, db_hashes}` from G0.1 in its prompt. Total gated: **≈20 agents**; reserve adds ~40 only on escalation.
```
Status: READY. Gate-0 is executable now (G0.2 is the safety verdict for the predicted first cut — clear finding 5bbc2be2 → reopen latch). Awaiting operator go to run the gated workflow (or to clear the latch directly once G0.2 returns safe).
```
