# K=1 Final-Snapshot Money-Decision Authority — Staged Design Plan

Issue #39 — external review verdict D, twin-authority elimination program.
READ-ONLY design. No code edits. Status: PROPOSAL (lands dark / staged).

Audited 2026-06-11 against: src/events/reactor.py (#95 two-window),
src/engine/event_reactor_adapter.py (~14k lines), mode_consistent_ev.py,
docs/operations/consolidated_systemic_overhaul_2026-06-11.md (K1, K4.0).

---

## 0. The thesis (the K=1 reframe)

Today the money decision is made **twice** against **two different books**:

- **Proof-time** (event claim → candidate proof): full selection/admission/mode
  policy runs on the *elected DB snapshot row* (`executable_market_snapshots`,
  captured by the off-cycle substrate-warm job ~30s freshness window).
- **Submit-time** (inside the network window): a *fresh JIT /book HTTP fetch*
  is pulled and the mode + price are **re-validated** against it. Any divergence
  → abort (`SUBMIT_ABORTED_PRICE_MOVED` / `SUBMIT_ABORTED_MODE_FLIPPED` /
  `would_cross_book`) → transient requeue.

This is a twin authority: two books, two evaluations, a divergence-detector
between them, and a whole requeue machine to absorb the divergence. The
K=1 decision: **the fresh submit-time book becomes the SOLE money-decision
authority.** The proof-time evaluation degrades to a *scheduling hint /
price-independent precompute*. Post-snapshot venue movement stops being a
"stale-decision" category and becomes a bounded venue-execution failure under a
max-price / post_only certificate.

This is the same shape as K4.0c (run-selection single authority) and K1.1
(mode single authority): **one rule = one implementation, evaluated once, on
the freshest authoritative input.**

---

## 1. Current-state dataflow — every book-price read between claim and submit

Legend: `world` = zeus-world.db, `trade` = zeus_trades.db (executable_market_snapshots
lives in zeus_trades — K1 DB split). `[NET]` = network I/O.

### Window A — pre-submit (world_write_mutex HELD, world txn open)
reactor.py `_process_event_unit` L453-522 → `_process_one_pre_submit` L772-860.
No book-price read here. Gates: event-type, FSR run-identity (L793-842),
reactor-mode, day0 eligibility, source-truth, **executable_snapshot_gate**
(L852: existence-only, `executable_snapshot_gate_from_trade_conn`
adapter L752 — checks a snapshot ROW EXISTS for the target bin, does not read
price for a decision), riskguard. Mutex released at L522.

### Network submit — NO mutex, NO world txn
reactor.py L527-528 `submit_result = self._submit(event, ...)`.
This single call fans into the adapter and does ALL price reads + the JIT fetch:

| # | Site (file:line) | What it reads | Book source |
|---|---|---|---|
| R1 | adapter `_build_event_bound_no_submit_receipt_core` L1704 `_latest_snapshot_rows_for_event_family` (`require_fresh=False`) | family identity rows (all MECE bins) | trade DB snapshot rows |
| R2 | adapter L1742 `_selected_snapshot_row_for_event` | the selected bin's snapshot row | trade DB row (R1 set) |
| R3 | adapter L1751 `_snapshot_price_stale_reason` (def L12436) | `freshness_deadline` vs decision_time — **30s PRICE window** on the elected row | trade DB row |
| R4 | adapter L1786 `EventBoundDecisionEngine().evaluate` | topology/binding (price-independent) | trade rows |
| R5 | adapter L1833 `_generate_candidate_proofs` L6477 | per-candidate q/FDR/admission + **mode** via `_mode_consistent_ev_for_proof` L7021 → `select_rest_then_cross_mode`; reads `_native_side_top_of_book(row)` L7054 (**best_bid/ask/tick FROM THE DB ROW**) | trade DB row (R2) |
| R6 | adapter L6544 `_family_rest_state` L6862 | unexpired-family-rest / escalated-after-rest flags | trade/world ledgers |
| R7 | adapter submit recapture ~L2520 (`_recapture.may_submit`, `SUBMIT_ABORTED_PRICE_MOVED`) | re-scores selected proof; sets PROVEN `execution_mode_intent`+`maker_limit_price` | DB row economics |
| R8 | adapter `_build_live_execution_command_certificates` L3216 → `_require_pre_submit_authority_witness` L3293 → main `_edli_pre_submit_authority_provider_from_world_conn` L6950 → `_edli_pre_submit_book_from_jit_fetch` L6899 → `get_orderbook_snapshot(token_id)` **[NET]** L6894 | **FRESH best_bid/best_ask** for the selected token | **live CLOB /book HTTP** |
| R9 | adapter L3299-3300 `fresh_best_bid/ask = authority_witness.current_best_*` | binds the fresh book | fresh (R8) |
| R10 | adapter L3343 `_fresh_rest_then_cross_mode` L3106 | **re-runs `select_rest_then_cross_mode` on the FRESH book** | fresh (R8) |
| R11 | adapter L3351 `_validate_final_order_mode_or_abort` L3177 | proof_mode (R5/R7) **vs** fresh_mode (R10) → abort `SUBMIT_ABORTED_MODE_FLIPPED` on divergence | both books |
| R12 | adapter L3399-3440 TAKER depth/sweep: `_fresh_touch` vs reservation → `TAKER_BUY_TOUCH_EXCEEDS_RESERVATION` | fresh ask (R8) | fresh |
| R13 | adapter L3568-3569 final intent `best_bid/best_ask` = fresh (TAKER) / DB (MAKER) | mixed | fresh+DB |
| R14 | adapter L3602-3625 legacy `_ev_boundary_favors_cross` (L4806) tripwire (legacy non-RTC proofs only) → `SUBMIT_ABORTED_MODE_FLIPPED` | fresh (R8) | fresh |
| R15 | adapter L4092-4140 `_would_cross_post_only_book` (def L4196) in pre-submit revalidation cert → `would_cross_book` true | fresh (R8) | fresh |

### Window B — post-submit (world_write_mutex re-acquired)
reactor.py L553-616 `_process_one_post_submit` L862. Consumes the receipt;
`_is_transient_money_path_reason` L1433 classifies R7/R11/R14/R15 aborts as
TRANSIENT → `_EXECUTABLE_SNAPSHOT_RETRY` → `requeue_pending` (bounded by
`MAX_EXECUTABLE_SNAPSHOT_RETRIES`=8). No book read.

**Twin-authority core:** the money decision is evaluated at R5 (DB book) AND
re-evaluated at R10 (fresh book), reconciled by the detector R11 (+legacy R14).
The fresh book (R8) already exists and is already authoritative for *price*
(R12/R13) — but NOT for *selection/mode/admission*, which still come from R5.

---

## 2. The K=1 target dataflow

**One book. One evaluation. One authority: the fresh submit-time book (R8),
captured into ONE persisted `executable_market_snapshots` row, on which the FULL
selection/admission/mode policy runs ONCE, from which the receipt+command are
compiled, submitted immediately under that row's max-price/post_only certificate.**

```
Window A (mutex, world txn): claim + price-INDEPENDENT gates only
  - event-type, FSR run-identity, reactor-mode, day0, source-truth, riskguard
  - executable_snapshot_gate stays EXISTENCE-only (identity, not price)
  - PRECOMPUTE & attach to event the price-independent decision substrate:
      q_lcb / posterior_id / probability_authority, family MECE topology,
      FDR family identity + hypothesis_count, direction-law center+sigma,
      conservative-evidence inputs (same_bin_yes_posterior),
      settlement_coverage_status, calibration maturity, family_rest_state flags.
    (These do NOT depend on price; computing them in Window A is the heavy
     p99=59s proof work moved OFF the submit window.)
  commit + release mutex
       |
Network submit (NO mutex, NO world txn):
  1. JIT /book fetch [NET]  ── the ONE book (today's R8)
  2. PERSIST exactly ONE executable_market_snapshots row from that fetch
     (snapshot_repo.insert_snapshot) → this row is the money authority,
     freshness_deadline = now + price_window; captured_at = fetch instant.
  3. Run the FULL price-dependent decision on THAT row, ONCE:
       select_rest_then_cross_mode (R5's kernel, fresh inputs),
       taker_all_in cost, EV, sizing/Kelly (price leg), capital efficiency,
       edge-zone admission, conservative-evidence cost leg.
     ← consumes the Window-A precompute for everything price-independent.
  4. Compile receipt + command from THAT SAME row (Fitz #4: receipt proves the
     economics it submitted under — now trivially true, one row).
  5. Submit immediately under that row's max_price / post_only certificate. [NET]
       |
Window B (mutex): ledger writes + mark. No re-decision, no divergence class.
```

There is no second book, no `_fresh_rest_then_cross_mode`, no
`_validate_final_order_mode_or_abort`, no PRICE_MOVED/MODE_FLIPPED/would_cross
*stale-decision* category. The only remaining submit-time failure is a **bounded
venue-execution failure**: the venue rejects/does-not-fill the order whose
max_price/post_only certificate the fresh row already guarantees is
non-crossing/within-ceiling. That is a venue fact, not a stale decision — it
records as an honest execution-receipt outcome, not a requeue.

---

## 3. Price-dependent vs price-independent decision-stage partition

The K=1 split hinges on this table. Price-INDEPENDENT stages are precomputed in
Window A and attached to the event (survive as scheduling-hint substrate).
Price-DEPENDENT stages re-run fresh on the persisted submit-time row.

| Decision stage | Site | Depends on book price? | Disposition under K=1 |
|---|---|---|---|
| Event-type / FSR run-identity | reactor L790-842 | NO | Window A (unchanged) |
| Source-truth gate | reactor L849 | NO | Window A (unchanged) |
| Day0 hard-fact eligibility | reactor L846 | NO | Window A (unchanged) |
| RiskGuard level | reactor L857 | NO | Window A (unchanged) |
| Executable-snapshot **existence** | reactor L852 | NO (identity) | Window A (stays existence-only) |
| Family MECE topology / binding | adapter L1700,L1786 | NO | Window A precompute |
| **q_lcb / posterior / prob authority** | `_generate_candidate_proofs` L6477 | NO | Window A precompute |
| **FDR** family id + hypothesis_count | proofs L6477 | NO (over MECE family identity) | Window A precompute |
| **Direction law** (mu, sigma center) | `_direction_law_family_center` L7090 | NO (forecast-only) | Window A precompute |
| Conservative-evidence: **YES-posterior leg** (`same_bin_yes_posterior`) | live_admission | NO (posterior) | Window A precompute |
| Settlement-coverage license (`settlement_coverage_status`) | live_admission | NO (settlement-backward) | Window A precompute |
| Calibration maturity | compiler/verifier | NO | Window A precompute |
| Family-rest state flags | `_family_rest_state` L6862 | NO (ledger state) | Window A precompute |
| **Conservative-evidence: cost leg** (`execution_price` vs q_lcb) | live_admission | **YES** | Fresh re-run |
| **Capital efficiency** (q_lcb vs `c_fee_adjusted`) | live_admission L1392 | **YES** | Fresh re-run |
| **Edge-zone admission** (q_lcb vs cost) | reactor L1420 / edge_zone_admission | **YES** | Fresh re-run |
| **Mode policy** (REST_DEFAULT/TAKER lanes) | `select_rest_then_cross_mode` | **YES** (bid/ask/spread) | Fresh re-run (ONCE) |
| **Taker all-in cost / EV** | `_mode_consistent_ev_for_proof` L7021 | **YES** | Fresh re-run |
| **Maker limit price** | `maker_limit_price` mode_consistent_ev L218 | **YES** (bid/ask/tick) | Fresh re-run |
| **Kelly size — price leg** (ExecutionPrice, fee-deducted) | Kelly proof | **YES** (cost basis) | Fresh re-run |
| Kelly — bankroll/correlation leg | portfolio_reservation | NO | Window A precompute (reservation nets fresh size in B) |
| Max-price / post_only certificate | final intent build L3541 | **YES** | Built fresh, IS the submit guarantee |

**Key correctness note (Fitz #4 / data provenance):** moving q_lcb to Window A
is safe ONLY because q_lcb bounds *parameter uncertainty of q*, not the book.
The adverse-selection haircut (`q_fill_adj = q_lcb - lambda*half_spread`,
mode_consistent_ev.py L386) IS price-dependent (half_spread) — it must re-run
fresh. So the *posterior* is precomputed but the *fill-conditioned* q_fill_adj
is fresh. The partition line is the spread, not q.

---

## 4. Staged migration plan (each stage shippable + testable + dark-safe)

Operator law: flags flip only on operator word. Every stage lands behind a flag
defaulting to **today's behavior**, or is a pure structural refactor that is
byte-identical until a later flag flips. No stage changes live behavior on merge.

### STAGE 1 — Persist the fresh submit-time book as a first-class snapshot row
**Goal:** make the JIT /book fetch (R8) write ONE `executable_market_snapshots`
row via `snapshot_repo.insert_snapshot`, tagged provenance
`source=JIT_PRESUBMIT`, BEFORE it is consumed. Today R8 is ephemeral (witness
only); this gives the fresh book a durable identity the receipt can prove.
**Behavior change:** none — the row is written and ALSO still flows through the
existing R9-R15 path. Pure additive persistence + provenance.
**Flag:** `k1_persist_presubmit_snapshot_enabled` (default OFF → no write).
**Antibody relationship tests:**
- `test_presubmit_snapshot_row_matches_witness_book`: the persisted row's
  best_bid/best_ask == authority_witness.current_best_* (R8→row identity).
- `test_presubmit_snapshot_provenance_jit`: row.source == JIT_PRESUBMIT and
  captured_at == fetch instant (provenance envelope, Fitz #4).
- `test_persist_off_is_byte_identical`: flag OFF → zero new rows, receipt hash
  unchanged (dark-safe).
**Risks addressed below:** SQLite write inside network window (§5).

### STAGE 2 — Single mode authority: fresh row drives mode, drop the validator
**Goal:** when a fresh persisted row exists (Stage 1), run
`select_rest_then_cross_mode` ONCE on THAT row and use its verdict directly as
`order_mode`. Skip `_fresh_rest_then_cross_mode` (R10) +
`_validate_final_order_mode_or_abort` (R11): there is no proof-mode to diverge
from when the proof mode IS the fresh-row mode.
**Behavior change:** gated. When `k1_fresh_mode_sole_authority_enabled` ON, the
proof-side mode (R5) is recomputed on the fresh row and the validator is bypassed
(it can never abort because there is one input). MODE_FLIPPED becomes
unreachable for RTC proofs.
**Flag:** `k1_fresh_mode_sole_authority_enabled` (default OFF → keep validator).
**Antibody relationship tests:**
- `test_fresh_mode_equals_proof_mode_on_same_book`: feed proof-row == fresh-row;
  K1-on mode == legacy validated mode (golden equivalence, the K1.2 pattern).
- `test_mode_flipped_unreachable_when_sole_authority`: with flag ON, assert no
  code path can emit `SUBMIT_ABORTED_MODE_FLIPPED` (AST/coverage relationship
  test that `_validate_final_order_mode_or_abort` is not called).
- `test_one_sided_book_still_rests_or_forbidden`: fresh one-sided book →
  `POLICY_TAKER_MAKER_INADMISSIBLE` / forbidden, never an unguarded taker.

### STAGE 3 — Single admission authority: price-dependent gates re-run on fresh row
**Goal:** capital-efficiency, edge-zone, conservative-evidence cost leg, EV,
Kelly price leg all evaluate against the fresh persisted row, consuming the
Window-A precompute for their price-independent inputs (q_lcb, YES-posterior,
coverage status). Retire R7's separate recapture as the price authority — the
fresh row IS the recapture.
**Behavior change:** gated. PRICE_MOVED stops being a divergence (the gate
either admits on the fresh price or honestly rejects EV<=0 on the fresh price —
a terminal honest no-edge, NOT a transient requeue).
**Flag:** `k1_fresh_admission_sole_authority_enabled` (default OFF).
**Antibody relationship tests:**
- `test_admission_gates_consume_fresh_price`: capital_efficiency / edge_zone
  read c_fee_adjusted derived from the fresh row, not the proof row.
- `test_price_moved_becomes_honest_reject_not_requeue`: a moved book that turns
  EV negative → terminal `CAPITAL_EFFICIENCY` / `EDGE_ZONE` reject, NOT
  `_EXECUTABLE_SNAPSHOT_RETRY`. (Cross-module: reactor disposition ← adapter
  receipt reason.)
- `test_precompute_price_independent_survives_window_a`: q_lcb/posterior/FDR
  attached in Window A equal the values the fresh admission consumes (no
  proof→receipt input loss — the 21a4c14ee2 / same_bin_yes_posterior lesson).

### STAGE 4 — Move heavy proof precompute into Window A; thin the submit window
**Goal:** the p99=59s proof generation (`_generate_candidate_proofs`) splits:
price-INDEPENDENT substrate computed in Window A and attached to the event (or a
side table keyed by event_id); only the price-DEPENDENT finalize runs in the
submit window on the fresh row. This is the latency-budget fix (§5).
**Behavior change:** gated; structural. Output identical, computed earlier.
**Flag:** `k1_window_a_precompute_enabled` (default OFF → compute everything at
submit as today).
**Antibody relationship tests:**
- `test_window_a_precompute_equals_inline`: precomputed substrate == the values
  the inline proof would have produced (golden equivalence on fixtures).
- `test_submit_window_latency_bounded`: with precompute ON, the work inside the
  network window (between mutex release and venue POST) excludes proof
  generation (timed/counted assertion — relationship between phase boundary and
  cost).
- `test_precompute_staleness_guard`: precompute carries source_available_at;
  if a later re-ingest bumps it past decision_time, the SUBMIT path
  re-precomputes or rejects (the SOURCE_CAPTURED_AFTER_DECISION_TIME family must
  not silently use stale precompute).

### STAGE 5 — Retire the transient-requeue machinery + dead validators
**Goal:** with Stages 2-4 ON and proven in production, delete the now-dead
divergence detectors and the requeue classifier branches they fed.
**Behavior change:** removal only, after the flags are operator-flipped ON and
the dead paths are confirmed cold in production (zero hits over a settle window).
**Flag:** none — this is the cleanup commit, gated on operator confirmation that
Stages 2-4 are live and the symbols in §6 show zero production traffic.
**Antibody relationship tests:**
- `test_no_stale_decision_reason_emitted`: over a replay corpus, no receipt
  carries PRICE_MOVED/MODE_FLIPPED/would_cross as a *stale-decision* reason.
- `test_requeue_only_for_genuine_snapshot_pending`: `_EXECUTABLE_SNAPSHOT_RETRY`
  fires ONLY for true snapshot-not-captured-yet (identity), never for a
  money-path price race (the honest-category-label invariant, reactor L668-701).

---

## 5. Risks

### 5.1 Latency budget inside the submit window (the binding constraint)
Today proof generation is p99=59s, max=460s (reactor L398-405 comment) and runs
INSIDE `self._submit`, i.e. *outside* the mutex but *inside* the per-event
network window. Re-running the FULL proof on the fresh row at submit time would
duplicate that cost. **Mitigation = Stage 4:** precompute the price-independent
59s of work in Window A (or a decoupled warmer), leaving only the price-leg
(mode/EV/sizing/admission — milliseconds) for the submit window. Without Stage 4,
Stages 2-3 still help (one book) but do NOT reduce the window; Stage 4 is the
latency antibody. The cycle budget (reactor L364-414,
`ZEUS_REACTOR_CYCLE_BUDGET_SECONDS`=30s) and the pre-event one-in-flight cap
(L406) survive unchanged and remain the backstop.

### 5.2 SQLite write windows (Stage 1 persistence)
The new `insert_snapshot` (Stage 1) writes to **zeus_trades.db**, NOT
zeus-world.db, and happens in the network phase where the **world** mutex is NOT
held — so it does not violate "never hold world_write_mutex across network I/O"
(#95). BUT: the adapter already holds a per-cycle `trade_conn` and commits it
per-event in a `finally` (adapter L1051-1055) precisely to avoid pinning the
trade-DB WAL lock across the multi-event cycle. The Stage-1 insert MUST follow
that same discipline: write + commit within the event, never hold the trade WAL
lock across the JIT fetch [NET]. Order matters: **fetch [NET] first, THEN open
the trade write, insert, commit** — never fetch while holding the trade write
lock. Antibody: a test asserting the trade_conn is not in_transaction across the
JIT fetch boundary (mirror of the world-side stale-snapshot guard, reactor
L379-387).

### 5.3 Venue rate limits for book fetches
The JIT fetch already exists (R8) and fires once per *actual submit candidate*
(rare, fully gated) — Stage 1-3 do NOT add fetches, they REPLACE the second
evaluation with the existing fetch. Net venue book-fetch volume is UNCHANGED or
lower (R10's fresh re-eval reused the same witness book; no new HTTP). The
substrate-warm job (`_edli_market_substrate_warm_cycle` main L6116, 30s window,
`ZEUS_REACTOR_REFRESH_BUDGET_SECONDS`<interval invariant L8644) keeps providing
identity/scheduling rows — it is NOT on the money-decision critical path under
K=1, so its cadence can relax (lower venue pressure) once the fresh row is the
authority. Risk: if a future change made the JIT fetch per-candidate-per-family
it would multiply; pin a test that exactly one /book fetch occurs per submit.

### 5.4 Interaction with the maker rest escalation job
`_maker_rest_escalation_cycle` (main L6070, 5-min cadence, cancel-only) and the
`escalated_after_rest` lane (mode_consistent_ev L539, `POLICY_TAKER_ESCALATED_
AFTER_REST`) are the one place a TAKER cross is licensed AFTER a rest. The
escalation re-certifies through the FULL pipeline — under K=1 that
re-certification ALSO runs on the fresh row (it already calls
`select_rest_then_cross_mode` with `escalated_after_rest=True`). The
`_family_rest_state` precompute (Stage 4) must read the rest ledger at Window A
AND the fresh-row evaluation must honor the escalated lane (the external-review
finding that hardcoding `escalated_after_rest=False` caused a MODE_FLIPPED loop,
adapter L3127-3130 — under K=1 that bug-class is impossible because there is no
second evaluation to disagree). Risk: the HOLD lane (`unexpired_family_rest`,
mode_consistent_ev L529) yields chosen_ev=-inf at evaluation time — the fresh
evaluation must still see the unexpired-rest flag so it does not place a second
order while a rest is live (the antibody relationship, test_rest_then_cross_
policy.py). Stage 4 precompute of `_family_rest_state` must be re-validated at
submit (rest could have filled/expired between Window A and submit) — treat the
rest flag as fresh-read at submit, not precomputed, OR precompute + re-check.

### 5.5 Per-city fairness & cycle budget (hard constraint #3)
Untouched: fairness/budget live in `process_pending`'s fetch ordering and the
per-cycle budget (reactor L356-417), all in Window A / scheduling. K=1 changes
only what happens AFTER claim, per event. The day0-tradeable demotion
(ReactorConfig L266, anti-starvation) and freshest-target-first ordering survive.
Antibody: a test that K=1 stages do not change `fetch_pending` ordering or the
budget early-return (L406-414).

---

## 6. Dead code at the end (exact symbols)

After Stage 5, with the K=1 flags operator-flipped ON and confirmed cold:

**In src/engine/event_reactor_adapter.py:**
- `_fresh_rest_then_cross_mode` (L3106) — the second mode evaluation. DEAD.
- `_validate_final_order_mode_or_abort` (L3177) — the divergence detector. DEAD.
- `_SubmitAbortedModeFlipped` (L3088) + `_MODE_FLIPPED_ABORT_PREFIX` (L3085) —
  the abort type/prefix. DEAD (no second authority to flip against).
- `_ev_boundary_favors_cross` (L4806) + its tripwire L3602-3625 — legacy
  fresh-book cross detector. DEAD.
- `_would_cross_post_only_book` (L4196) + its use in the pre-submit revalidation
  payload L4092-4140 (`would_cross_book` field) — DEAD as a *decision*; may
  survive as an observability assertion ONLY if it can never gate.
- `_select_edli_order_mode` (L4619) — retained ONLY under the `canary_force_taker`
  operator-knob bypass (adapter L3331); if that knob is also retired, DEAD.
  Otherwise demote to canary-only and document.
- The R7 submit-recapture-as-price-authority branch (~L2520, `_recapture.may_submit`
  / `SUBMIT_ABORTED_PRICE_MOVED` receipt) — the price-abort arm becomes DEAD
  (EV-on-fresh-row replaces it); the edge/family-reversal arms may survive as
  honest rejects, audit separately.

**In src/events/reactor.py:**
- `_is_transient_money_path_reason` (L1433) — the price-race classifier. DEAD
  (no money-path transients remain; only genuine snapshot-pending).
- `_EXECUTABLE_SNAPSHOT_RETRY` (L274) requeue arm for money-path reasons +
  `_transient_requeue_reasons` dict (L344) + `MONEY_PATH_TRANSIENT_EXHAUSTED`
  label (L678) — the money-path requeue machinery. DEAD. (The
  `_EXECUTABLE_SNAPSHOT_RETRY` sentinel SURVIVES for genuine
  snapshot-not-yet-captured identity retries, reactor L852-855 — that is NOT a
  money-path transient.)
- The NO_SUBMIT transient-requeue branch (L916-931, `SUBMIT_ABORTED_MODE_FLIPPED`
  / `SUBMIT_ABORTED_PRICE_MOVED` on a NO_SUBMIT receipt) — DEAD.
- The transient branch in `_reject_or_retry_post_submit` (L1018-1025) — DEAD for
  money-path reasons.

**In src/contracts (lifecycle states):**
- `CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED` /
  `SUBMIT_ABORTED_MODE_FLIPPED` (referenced adapter L7902, L1448) — retire the
  *stale-decision* meaning; if retained, redefine as venue-execution-failure
  states (writer⊆consumer relationship test K2.2 must be updated in lockstep).

**SURVIVES (do not delete):** `select_rest_then_cross_mode` (the kernel — now the
SOLE evaluator), `select_mode_consistent_ev`, the spread guard, the hysteresis
margin, `_mode_consistent_ev_for_proof` (now runs once on the fresh row), the
JIT /book provider (`_edli_pre_submit_jit_book_quote_provider`), the existence-only
`executable_snapshot_gate`, the substrate-warm job (demoted to scheduling-hint /
identity provider), the maker-rest escalation job.

---

## 7. Success criterion check (verdict D)

- No production path calls `_fresh_rest_then_cross_mode` /
  `_validate_final_order_mode_or_abort` as a second authority → §6 DEAD list,
  proven by Stage 2 `test_mode_flipped_unreachable_when_sole_authority`.
- `SUBMIT_ABORTED_PRICE_MOVED` / `SUBMIT_ABORTED_MODE_FLIPPED` / `would_cross_book`
  and the transient-requeue machinery become dead code → §6, proven by Stage 5
  `test_no_stale_decision_reason_emitted`.
- Post-snapshot venue movement = bounded venue-execution failure under a
  max-price/post_only certificate → §2 target flow + §3 max-price cert row,
  proven by Stage 3 `test_price_moved_becomes_honest_reject_not_requeue`.

## 8. Open questions for the operator / next session
1. Rest-flag freshness (§5.4): precompute `_family_rest_state` in Window A and
   re-check at submit, or read it fresh at submit only? (Correctness vs latency.)
2. Should `_would_cross_post_only_book` survive as a non-gating observability
   assertion, or be deleted outright? (It is structurally redundant once the
   maker limit is built non-crossing from the fresh book — mode_consistent_ev
   `maker_limit_price` already makes a crossing maker limit unconstructable.)
3. `canary_force_taker` knob retention (§6) — keep `_select_edli_order_mode`
   alive for the operator bypass, or retire the knob with the twin authority?
