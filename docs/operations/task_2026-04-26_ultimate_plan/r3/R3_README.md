# Zeus Ultimate Plan R3 — Implementation-ready package

Created: 2026-04-26 (post multi-review + V2.1 structural diff merger)
HEAD anchor: `874e00cc0244135f49708682cab434b4d151d25d` (`main`)
Parent doc: `../ULTIMATE_PLAN.md` (R2, debate-view) + `evidence/multi_review/V2_1_STRUCTURAL_DIFF.md`

This README is the COLD-START ENTRY POINT for any agent (compacted, zero
context, fresh session) implementing the Zeus Ultimate Plan. Read this
README first; it tells you exactly which file to open next.

## Mission statement

After R3 implementation completes, Zeus must be able to:
1. **Trade live, real money on Polymarket V2** with no silent S0 losses.
2. **Dominate the live market** — alpha + execution + risk allocation
   wired so that edge can compound rather than being erased by
   execution-state pollution.
3. **Ingest TIGGE / additional forecast sources + retrain calibration**
   on operator approval (wiring is built; pulling the trigger is operator
   decision).

## Decomposition philosophy

R3 organizes work by **data-lifecycle phases**, not code-locality regions.
The R2 debate decomposed by region (Up boundary / Mid execution / Down
transport); that was correct for adversarial review but caused
implementation strain (every implementation task crossed regions).
R3 inherits R2's concrete file:line work and antibody system but
re-sequences execution along data-lifecycle phases per V2.1.

## Phase DAG

```
Z0 plan-lock  → Z1 cutover-guard  → Z2 V2-adapter  → Z3 heartbeat  → Z4 collateral-ledger
                                                                                  ↓
                                                                              U1 snapshot-v2
                                                                                  ↓
                                                                              U2 raw-provenance-schema (5 projections)
                                                                                  ↓
                                                                              M1 lifecycle-grammar  → M2 unknown-side-effect
                                                                                                          ↓
                                                                              M3 user-channel-ws   ←  ↓
                                                                                  ↓                    ↓
                                                                              M4 cancel-replace    ←  ↓
                                                                                  ↓
                                                                              M5 reconcile-sweep
                                                                                  ↓
                                                                              R1 settlement-ledger  →  T1 fake-venue
                                                                                                          ↓
                                                            F1 forecast-pipeline-plumbing  ──→  F2 calibration-loop-wiring  ──→  F3 tigge-ingest-stub
                                                                                                          ↓
                                                                              A1 benchmark-harness  →  A2 risk-allocator
                                                                                                          ↓
                                                                              G1 live-readiness-gates
```

The critical path runs Z0 → Z1 → Z2 → Z3 → Z4 → U1 → U2 → M1 → M2 → M5 → T1 → A1 → A2 → G1. F1/F2/F3 (forecast plumbing) is parallelizable after U2 lands. R1 is parallelizable after M1. M3/M4 are parallelizable with M5.

## Where to start

**For brand-new agent sessions (zero context)**: use
`templates/fresh_start_prompt.md` — it forces ORIENTATION before any
phase assignment. The fresh-start agent writes an orientation report at
`r3/_orientation/<tag>_<date>.md` and waits for operator authorization
before starting code.

**For agents already oriented**: use `templates/phase_prompt_template.md`
filled in with `<PHASE_ID>` to operate on one phase.

The protocol is LIVING. Read these BEFORE writing code:
- `IMPLEMENTATION_PROTOCOL.md` — the 14 failure modes + their mechanisms
- `CONFUSION_CHECKPOINTS.md` — 12 stop-and-verify moments (CC-1..CC-12)
- `SELF_LEARNING_PROTOCOL.md` — how to capture + propagate learnings (3 buckets)

Manual workflow if you want to skip the templates:

1. **Read this README in full.**
2. Read `../ULTIMATE_PLAN.md` (R2) for context on what came before.
3. Read `ULTIMATE_PLAN_R3.md` for the cohesive R3 synthesis.
4. Read `IMPLEMENTATION_PROTOCOL.md` + `CONFUSION_CHECKPOINTS.md` + `SELF_LEARNING_PROTOCOL.md`.
5. Read `operator_decisions/INDEX.md` to identify which gates are open.
6. Read `_phase_status.yaml` to see current phase state.
7. Pick a phase from the `ready_to_start:` list (no unmet `depends_on`).
8. Open `slice_cards/<phase_id>.yaml` for your chosen phase.
9. Run `scripts/r3_drift_check.py --phase <PHASE_ID>` — must be GREEN/YELLOW.
10. Read `learnings/<PHASE_ID>_*.md` if any (prior agent learnings) +
    `_confusion/<PHASE_ID>_*.md` (open confusions on this phase) +
    `_protocol_evolution/*.md` (pending amendments).
11. Verify HEAD state matches the card's `preconditions:` block.
12. Write `boot/<PHASE_ID>_<author>_<date>.md` (boot evidence).
13. Implement; antibodies in `acceptance_tests:` are the contract.
14. Run living-protocol loops continuously (Loop A confusion handling,
    Loop B learning capture, Loop C protocol evolution, Loop D
    verification). See `templates/phase_prompt_template.md` for details.
15. After implementation: dispatch `code-reviewer` + (HIGH-risk only)
    `critic-opus` before merge.
16. Update `_phase_status.yaml` to COMPLETE + write end-of-phase retro at
    `learnings/<PHASE_ID>_<author>_<date>_retro.md`.
17. Run `scripts/aggregate_r3_cards.py` to refresh dep graph.

## Operator decision gates (do NOT pass without operator)

Six points where engineering MUST stop and wait for operator action:

| Gate | Phase blocked | Artifact required | Default if absent |
|---|---|---|---|
| Q1-zeus-egress | Z2 (production cutover) | `evidence/q1_zeus_egress_<date>.txt` host probe from Zeus daemon machine | engineering proceeds, cutover BLOCKED |
| Q-HB-cadence | Z3 (heartbeat tuning) | `evidence/q_hb_cadence_<date>.md` operator inquiry result | engineering implements at default 5s, MAY be tuned later |
| Q-FX-1 | Z4 (pUSD redemption) | `evidence/q_fx_1_classification_decision_<date>.md` + ZEUS_PUSD_FX_CLASSIFIED env flag | code raises FXClassificationPending |
| INV-29 amendment | M1 (state grammar) | governance commit + planning-lock receipt | M1 PR cannot merge |
| TIGGE-ingest go-live | F3 (TIGGE active ingest) | `evidence/tigge_ingest_decision_<date>.md` + ZEUS_TIGGE_INGEST_ENABLED env flag + local JSON payload path (`payload_path:` or ZEUS_TIGGE_PAYLOAD_PATH) | code path wired, ingest disabled |
| Calibration retrain go-live | F2 (calibration update) | `evidence/calibration_retrain_decision_<date>.md` + operator dispatch | engine reads existing Platt params; no retrain |
| CLOB v2 cutover go/no-go | Z1 → CutoverGuard.LIVE_ENABLED | operator dispatch + `evidence/cutover_runbook_<date>.md` | CutoverGuard stays in PRE_CUTOVER_FREEZE |
| impact_report rewrite | Z0 (Phase 0.F critic gate) | `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/v2_system_impact_report.md` rewritten with marketing-label disclaimer | engineering proceeds, but live placement BLOCKED |

See `operator_decisions/INDEX.md` for full decision register with deadlines.

## What to do if HEAD has changed

If HEAD anchor `874e00cc` is no longer current main:
1. `git log --oneline 874e00cc..HEAD` to see what landed since.
2. Re-run citation grep on slice cards you intend to start
   (`scripts/citation_check.py` if exists; otherwise grep manually).
3. Memory `feedback_zeus_plan_citations_rot_fast` — expect 20-30%
   premise mismatch. Update card `file_line:` entries with current
   line numbers before implementing.
4. If a phase's preconditions are violated by upstream changes, FREEZE
   and write `_blocked.md` with the mismatch detail.

## Antibody discipline (R2 inheritance)

R3 cards inherit R2's NC-NEW-A..F antibody system:
- **NC-NEW-A** `zeus-venue-commands-repo-only` semgrep — no INSERT INTO
  venue_commands outside `src/state/venue_command_repo.py`.
- **NC-NEW-B** `executable_market_snapshots` APPEND-ONLY — SQLite trigger
  + semgrep + Python encapsulation.
- **NC-NEW-C** `zeus-create-order-via-order-semantics-only` — order
  construction goes through `OrderSemantics.for_market()`.
- **NC-NEW-D** function-scope: `cycle_runner._execute_force_exit_sweep`
  is the SOLE caller of `insert_command(IntentKind.CANCEL, ...)` for
  RED-emission within `cycle_runner.py`.
- **NC-NEW-E** `RESTING-not-enum` — `venue_status='RESTING'` is
  payload-discrim, NOT a CommandState enum member. (R3 may reframe under
  the venue_order_facts projection — see U2.)
- **NC-NEW-F** single-tombstone — `state/auto_pause_failclosed.tombstone`
  has ONE physical writer pattern; HeartbeatSupervisor reuses, does NOT
  introduce a second tombstone source.

R3 adds:
- **NC-NEW-G** `provenance-not-seam` — pin `VenueSubmissionEnvelope`
  contract, NOT specific SDK call shape (per V2.1 §31).
- **NC-NEW-H** `matched-not-confirmed` — calibration training paths
  filter `venue_trade_facts WHERE state = 'CONFIRMED'`; SELECTing MATCHED
  rows for training raises ValueError.
- **NC-NEW-I** `optimistic-vs-confirmed-exposure` — risk allocator
  separates capital deployed against OPTIMISTIC_EXPOSURE from
  CONFIRMED_EXPOSURE; sizing function distinguishes them.
- **NC-NEW-J** `tigge-ingest-flag-gate` — `TIGGEClient.fetch()` raises
  unless `ZEUS_TIGGE_INGEST_ENABLED=1` AND operator decision file
  present; open-gate ingest still fails closed unless an operator-approved
  local JSON payload path is configured.

## Memory cross-references

R3 implementation should consult these memory entries:
- `feedback_lifecycle_decomposition_for_execution` — why R3 is structured this way.
- `feedback_multi_angle_review_at_packet_close` — run 5-angle review when packet ships.
- `feedback_grep_gate_before_contract_lock` — re-grep file:line within 10 min of contract lock.
- `feedback_zeus_plan_citations_rot_fast` — expect 20-30% premise mismatch in Zeus plans.
- `feedback_on_chain_eth_call_for_token_identity` — when ERC-20 identity is disputed, dispatch sub-agent for direct eth_call.
- `feedback_critic_prompt_adversarial_template` — 10 explicit attacks; never "narrow scope self-validating".
- `feedback_default_dispatch_reviewers_per_phase` — auto-dispatch critic + code-reviewer after each phase.

## Layout

**Top-level docs** (read in this order):
- `R3_README.md` — this file
- `IMPLEMENTATION_PROTOCOL.md` — 14 failure modes + anti-drift mechanisms
- `CONFUSION_CHECKPOINTS.md` — 12 stop-and-verify moments (CC-1..CC-12)
- `SELF_LEARNING_PROTOCOL.md` — capture + propagate learnings (3 buckets)
- `ULTIMATE_PLAN_R3.md` — cohesive plan synthesis (phase summaries + DAG)
- `INVARIANTS_LEDGER.md` — single source of truth for 30 invariants
- `SKILLS_MATRIX.md` — phase × step × skill mapping
- `_phase_status.yaml` — phase progress tracker

**Templates** (fill in + dispatch):
- `templates/fresh_start_prompt.md` — for brand-new agent sessions
- `templates/phase_prompt_template.md` — for in-flight phase work

**Living-protocol directories** (populated DURING implementation):
- `slice_cards/<phase_id>.yaml` — 20 implementation contracts (already written)
- `operator_decisions/INDEX.md` — 8-gate register (operator artifacts land here)
- `learnings/<PHASE_ID>_<topic>.md` — phase-specific learnings (Bucket A)
- `learnings/<PHASE_ID>_<author>_<date>_retro.md` — end-of-phase retros
- `_confusion/<PHASE_ID>_<topic>.md` — open confusions (CC-1..12 triggered)
- `_protocol_evolution/<topic>.md` — proposed amendments to the protocol itself
- `_orientation/<tag>_<date>.md` — fresh-start orientation reports
- `boot/<PHASE_ID>_<author>_<date>.md` — per-phase boot evidence
- `frozen_interfaces/<phase_id>.md` — public API contracts produced by phases
- `reference_excerpts/<topic>_<date>.md` — frozen excerpts of external docs (V2 SDK source, TIGGE access, etc.)
- `drift_reports/<date>.md` — auto-generated daily drift reports

**Scripts** (run on demand or via CI):
- `scripts/r3_drift_check.py` — citation drift detector (run daily)
- `scripts/aggregate_r3_cards.py` — phase aggregator (run on every phase merge)

**Auto-generated**:
- `dependency_graph_r3.mmd` — Mermaid graph
- `slice_summary_r3.md` — per-card summary + critical path

## Inheritance from R2

R2 artifacts at `../slice_cards/{up,mid,down}-NN.yaml` and
`../evidence/{up,mid,down}/converged_R<N>L<N>.md` are the **detailed
implementation source** for R3 phases. Each R3 card has a `links.r2_cards`
field pointing to the relevant R2 work. R2 file:line citations + antibody
test names + grep evidence carry forward.

The R2→R3 mapping:
- R2 up-01..03 → R3 U1
- R2 up-04 → R3 U2 (extended into 5 projections)
- R2 up-06 → R3 G1 (UNVERIFIED rejection matrix becomes a live-readiness gate)
- R2 up-07 → R3 U1 (snapshot freshness gate is part of snapshot-v2)
- R2 up-08 → R3 G1 (frozen-replay is a readiness gate)
- R2 mid-01 → R3 M1 (RED→durable-cmd, NC-NEW-D function-scope antibody preserved)
- R2 mid-02 → R3 Z2 + U2 (signer interception → VenueSubmissionEnvelope)
- R2 mid-03 → R3 M1 + M2 (state grammar amendment → 5-projection split)
- R2 mid-04 → R3 M1 (PARTIAL → trade-facts MATCHED/MINED/CONFIRMED)
- R2 mid-05 → R3 M5 (exchange reconciliation sweep)
- R2 mid-06 → R3 G1 (relationship tests become readiness gates)
- R2 mid-07 → R3 M3 (WS_OR_POLL → user-channel ingest with REST fallback)
- R2 mid-08 → R3 T1 (failure injection → fake venue parity)
- R2 down-01 → R3 Z2 (V2 SDK swap → strict adapter module)
- R2 down-02 → R3 Z0 (D0 questions become operator-decision register)
- R2 down-03 → R3 Z2 (V2 SDK contract antibody)
- R2 down-04 → R3 operator_decisions/q1_zeus_egress.md
- R2 down-05 → R3 Z0 (status amendment)
- R2 down-06 → R3 Z3 (HeartbeatSupervisor mandatory)
- R2 down-07 → R3 Z4 (CollateralLedger expanded scope)

## Total R3 budget

20 phase cards. Estimated 220-280h (vs R2's 162.5h). Critical path
~80-100h. Bigger because:
- A1 + A2 dominance work moved IN from R2 §4 deferred Roadmap
- F1/F2/F3 forecast plumbing added (TIGGE wired + calibration loop)
- 5-projection state-machine split (vs R2's compression into one
  CommandState grammar)
- Z1 CutoverGuard becomes code, not runbook
- Z4 CollateralLedger replaces R2's down-07 single-card balanceOf rewire

## "Implement and dominate" definition

When G1 live-readiness gates all pass:
- Every order Zeus places is reconstructable from raw payload provenance.
- Every order Zeus places is heartbeat-protected.
- Every cancel has a typed outcome (`CANCELED` / `CANCEL_FAILED` / `CANCEL_UNKNOWN`).
- Every fill is split into MATCHED → MINED → CONFIRMED with calibration
  consuming CONFIRMED only.
- Every redemption is durable command-journal entry.
- Every collateral-asset call distinguishes pUSD-buy from CTF-token-sell.
- Paper and live use the SAME state machine via T1 fake venue.
- A1 benchmark suite gates strategy promotion.
- A2 risk allocator caps capital deployment per market / event /
  resolution-time / drawdown.
- F1/F2/F3 forecast pipeline is wired so operator can flip local TIGGE ingest
  + calibration retrain switches without code changes; external TIGGE archive
  HTTP/GRIB remains a later data-source packet.

That is the minimum infrastructure for "Zeus dominates live market"
per the original prompt.
