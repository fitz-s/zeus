# Merge drift audit — origin/live hotfixes vs one-law exit (2026-07-24)

Read-only semantic classification for merging `claude/ultimate-alpha`
(HEAD `4960080741`, base `86b1342f3`) with `origin/live` (`5db9285020`, +52
commits). Merge-base == declared base `86b1342f3` — clean lineage, no divergence.

## The consumption surface (what the one-law reads)

`Position.evaluate_exit` (portfolio.py:1015) → `predicted_bin_law.exit_decision`
reads exactly four inputs:

- `q_lcb`  ← `_held_side_robust_lower` ← `current_ci[0]`, `fresh_prob`,
  `fresh_prob_is_fresh`, `belief_available`  (portfolio.py:939-972)
- `lock`   ← `_settlement_preimage_lock` ← `day0_zero_probability_exit_authority`,
  `fresh_prob`  (portfolio.py:974-993)
- `bid_breakpoints` ← `_exit_bid_breakpoints` ← `best_bid`  (portfolio.py:995-1013)
- RED ← `self.exit_reason == "red_force_exit"`

`_build_exit_context` (cycle_runtime.py:5048) fills `current_ci` **only** from a
finite held-side bootstrap CI (edge_ctx.confidence_band_*), `fresh_prob` from the
monitor probability, `day0_zero_probability_exit_authority` from the position's
absorbing-hard-fact flag. It never sets `belief_available` (defaults True), so the
degraded-belief signal reaches the law through `current_ci is None`, not a flag.

**Consequence:** any hotfix that improves the belief probability content upstream
(monitor_refresh / event_reactor_adapter / position_belief) is SUBSUMED — the law
consumes the improved `fresh_prob`/`current_ci` each tick. Any hotfix in a file
our branch never touched is PRESERVED-BY-KEEP.

## Classification table

| sha | invariant | verdict | evidence |
|---|---|---|---|
| 11ed0dcfa Day0 sells→temporal EV | Day0 hold on point-q terminal EV not UCB tail; immature Day0 statistical authority must HOLD | CONFLICT-TEXTUAL→OURS; point-vs-UCB SUBSUMED-BY-DESIGN; immature-HOLD SUBSUMED-WITH-CAVEAT | law uses `q_lcb` (lower bound) uniformly, stricter than their point-q → UCB pathology impossible. +2 ExitContext fields auto-merge but law never reads them (orphaned) |
| 2ef364bda use current prob bounds | exit reads CURRENT fresh bootstrap belief, not entry/stale | SUBSUMED + PRESERVED-BY-KEEP | is the law's design (`_held_side_robust_lower` reads `current_ci[0]`); monitor_refresh/position_belief untouched |
| 628a37d57 restore provisional Day0 temporal authority | Day0 provisional temporal prob keeps authority through monitor | SUBSUMED + PRESERVED-BY-KEEP | feeds fresh_prob/current_ci; files disjoint |
| 2aa419659 preserve prob content across handoff | probability content survives monitor handoff | SUBSUMED + PRESERVED-BY-KEEP | cycle_runtime edits (5314/5463) disjoint from our _build_exit_context; auto-merged |
| 1e6bd04ae token-typed exit value | (a) compare executable sale before edge-magnitude threshold; (b) dedup selects held token by direction | (a) SUBSUMED-BY-DESIGN; (b) PRESERVED-BY-KEEP | (a) law has no edge/neg_edge threshold, direct L(x) vs x·q⁻+M_x; (b) dedup fns untouched (portfolio.py:3048-3116, evaluator _has_same_token_blocking_open_db) |
| d1e47e3f0 honor degraded monitor reservations | degraded monitor with no progress keeps budget reservation | PRESERVED-BY-KEEP | cycle_runtime ~5949 disjoint, auto-merged; monitor scheduling upstream of law |
| 93faef3ed monitor budget on full held book | reservation count spans full held book | PRESERVED-BY-KEEP | cycle_runtime ~129/5712-5951 disjoint, auto-merged |
| f8730f18d retain no-order exits until bid returns | decided SELL with no fillable order retained, not released | PRESERVED-BY-KEEP | exit_lifecycle.py untouched; post-decision execution, orthogonal |
| ad128aabe bind SELL authority to current prob | batch SELL authority bound to current probability | SUBSUMED + PRESERVED-BY-KEEP | global_batch_runtime untouched; aligns with law consuming current q_lcb |
| 3a017ee88 pre-observation Day0 continuity | pre-observation Day0 prob continuous | SUBSUMED + PRESERVED-BY-KEEP | event_reactor_adapter/monitor_refresh untouched; feeds fresh_prob |
| 909d71dce fractional Kelly to family targets | family sizing targets get fractional-Kelly haircut | PRESERVED-BY-KEEP | solver.py untouched, no kelly import; different sizing layer |
| 589ec6aab exclude unresolved claims from SELL inventory | unresolved claims out of SELL inventory | PRESERVED-BY-KEEP | global_auction_universe untouched; orthogonal |
| b39f3a277 price provisional Day0 remaining window | provisional Day0 prob prices remaining window to terminal | SUBSUMED + PRESERVED-BY-KEEP | upstream producer of terminal q⁻ the law mandates; exit-side re-multiply prohibition not violated |
| 71bb2a9bc freeze Day0 temporal evidence epoch | Day0 temporal evidence epoch frozen within decision | SUBSUMED + PRESERVED-BY-KEEP | event_reactor_adapter untouched; feeds fresh_prob |
| ea38fabc2 restore continuous probability authority | continuous (not step) Day0 probability authority | SUBSUMED + PRESERVED-BY-KEEP | day0_fast_obs/event_reactor_adapter/reactor/ingest_main untouched; feeds fresh_prob |

## LOST list

**None fully lost.** The only invariant with no arithmetic equivalent in the
law is **11ed0dcfa's immature-Day0-statistical-authority HOLD** (keyed off
`day0_exit_authority_status ∈ {immature, unavailable}`, a field that fed only the
deleted Day0 exit branch and maps to neither `current_ci` nor
`day0_zero_probability_exit_authority`). This is a **deliberate FINAL_SPEC kill**
(§离场律: no Day0 permission gate; current q⁻ already integrates the full
remaining window), not an accidental loss — reclassified **SUBSUMED-WITH-CAVEAT**,
not LOST. See merge decision #2.

## Textual conflicts

**Count: 1 file** (`src/state/portfolio.py`), 2 diff3 hunks, both inside the
`evaluate_exit` region:

- Hunk A `@@ -1079,+1081 @@` — our SELL_REVERSAL branch vs their old exit tree
  (best_bid/forward_edge/day0_active + 11ed0dcfa immature-HOLD + point-vs-UCB).
- Hunk B `@@ -1089,+1342 @@` — our HOLD tail vs their `ci_separation_gate` /
  1e6bd04ae reordered CI_SEPARATED_REVERSAL.
- (Non-conflict) `ExitContext` +day0_exit_authority_status/reason auto-merges.

**All other overlapping files auto-merge:** cycle_runtime.py, evaluator.py,
edli_position_bridge.py, command_recovery.py, db.py, and 3 test files.

## Merge-procedure recommendation

1. Resolve `portfolio.py` = **OURS** for the entire `evaluate_exit` body. Both
   conflict hunks are dead branches under the one-law.
2. **Conscious decision on 11ed0dcfa immature-HOLD:** verify monitor_refresh sets
   `current_ci=None` / `fresh_prob_is_fresh=False` whenever
   `day0_exit_authority_status ∈ {immature, unavailable}`. If a valid CI can
   coexist with immature status, the one-law will SELL where the hotfix held —
   accept as deliberate under FINAL_SPEC, or fold the maturity signal into
   `belief_available` in `_build_exit_context` to preserve the hold.
3. **Drop** the orphaned `day0_exit_authority_status` / `day0_exit_authority_reason`
   ExitContext fields — the law never reads them.
4. **Keep theirs verbatim** for all monitor_refresh / event_reactor_adapter /
   position_belief / solver / exit_lifecycle / global_* hotfixes and 1e6bd04ae's
   dedup token-typing (all in files/regions we never touched).
5. Kelly deletion verified clean: merged evaluator.py/strategy_profile.py retain
   no reference to deleted `observed_target_day_fraction` / `city_kelly_multiplier`.

**Coverage:** exit-law reconciliation and both-sides exit files (cycle_runtime,
evaluator — confirmed disjoint) audited. edli_position_bridge/command_recovery/db.py
+ test files confirmed textual auto-merge only, NOT semantic-clean — run the suite
green before cutover.

---

## Wave 2 (109c89cd7..7663310b0)

Wave 1 merged cleanly as commit `550dc4d03` — the merge-base of our HEAD
(`4c081482a`) and `origin/live` is **exactly `109c89cd7`**, confirming Wave 1
consumed origin/live up to that tip with no residual drift. Wave 2 audits the
**41 commits** `109c89cd7..origin/live` (tip pinned `7663310b0`, the PR #445
shadow-diagnostic-extinction merge). Our-side diff: 54 files; their-side: 844;
overlap: 30 files.

### Summary counts

- **41 commits** audited. **8 files conflict** under `git merge-tree`; 22 of the
  30 overlap files auto-merge.
- **LOST = ∅.** Every content conflict is one of **our deliberate one-law
  deletions** where origin/live *kept or refined machinery we removed*. There is
  no origin/live invariant without a one-law equivalent.
- **Merged-tree dangling-symbol check: CLEAN.** After resolving the 8 conflict
  files OURS, `git grep` over the written merge tree
  (`e04b4b164`) finds **zero** references to any symbol we deleted
  (`_compute_exit_correlation_crowding`, `exit_correlation_crowding_rate`,
  `is_phase_allowed`, `ci_width > 0.1x`, `kelly_for_phase`) in any auto-merged
  file. The merge is self-consistent under the one-law.
- **Law-identity contract survives.** Merged `db.py` retains
  `_OPTIONAL_IDENTITY_COLUMNS`, `assert_law_identity`, `decision_law_id` /
  `position_origin`, and `_migrate_decision_law_identity_columns` intact.

### The single semantic driver

One commit — **`fe5afb2d2 refactor(runtime): extirpate alternate live
semantics`** (the body of PR #445) — produces every *runtime* conflict
(config.py, kelly.py, strategy_profile.py, portfolio.py, registry). It is a
**shadow/alternate-vocabulary extinction** pass and is therefore **aligned with
COLLISION.md C1 (no-shadow)** — it deletes alternate-concept machinery, exactly
our direction. The conflicts arise only because it *refined-in-place* three
pieces our one-law *deleted outright* (correlation-crowding, the stepwise Kelly
`ci_width`/`lead_days` modifier, `is_phase_allowed`) rather than removing them.
The remaining ~30 governance/cutover/extinction-proof commits are
`topology_doctor` antibody + test commits with no runtime overlap.

### Classification table (overlap commits only)

| sha | subject | invariant | verdict |
|---|---|---|---|
| 0867a8687 | recovery: reconstruct mixed-token entry positions | token-identity (YES/NO) recovery of mixed-token positions | PRESERVED-BY-KEEP — auto-merge; 0 decision-law-identity hits (orthogonal axis) |
| b7b1f453e | recovery: narrow mixed-token repair candidates | perf-narrow of same repair | PRESERVED-BY-KEEP — auto-merge, orthogonal |
| e8598ac6d | recovery: require exact B71 certificates | recovery requires exact cert match | PRESERVED-BY-KEEP — auto-merge; cert axis, not our CERT_EXPIRY_PULL producer |
| 8c4aa329f | state: compare held tokens in F109 | projection compares held tokens | PRESERVED-BY-KEEP — projection.py auto-merge; disjoint from our identity-column payload |
| c4b3946b1 | state: keep opposite tokens distinct | projection keeps YES/NO distinct | PRESERVED-BY-KEEP — projection.py auto-merge, orthogonal |
| 003ee5628 | solve: count confirmed positions in Kelly endowment | Kelly endowment counts confirmed positions | PRESERVED-BY-KEEP — solver-layer endowment, not our `kelly.py` multiplier; touches only test_topology governance |
| fe5afb2d2 | runtime: extirpate alternate live semantics | remove alternate/shadow vocabulary | CONFLICT-TEXTUAL→OURS (5 files) — see hunks; SUBSUMED, aligned with C1 no-shadow |
| a4a47a7d0 | runtime: close single-live review gaps | adds exit-cost + fill-authority tests | CONFLICT-TEXTUAL→OURS (2 test files) — tests exercise deleted API; invariants SUBSUMED-BY-DESIGN |
| 0db526894 | runtime: preserve hotfix semantics without gates | adds near-one hold-value tests | CONFLICT-TEXTUAL→OURS (test_live_safety) — retired trigger strings; SUBSUMED |
| 483dbc9fe | runtime: reconcile current live semantics | test + fingerprint reconcile | PRESERVED-BY-KEEP — tests + `_schema_fingerprint` only, no runtime overlap |
| b2f799480, 383071ebb, 7ca3c67eb, db128ce13, f0331027a | single-live cutover / fail-closed | db.py / db_writer_lock cutover fences | PRESERVED-BY-KEEP — auto-merge; do not touch identity columns |
| (~30 others) | governance/cutover/extinction proof | topology_doctor antibodies, cutover taint tracking | no runtime overlap — one-line skip (test_topology.yaml / governance only) |

### LOST list

**None.** The origin/live invariants that could look lost are all re-expressed
by the one-law comparison `L(x)` vs `x·q⁻ + M_x`:

- **best_bid quote authority** (exit EV must use held-token `best_bid`, not the
  `current_market_price` scalar — origin/live's
  `test_buy_no_exit_ev_gate_uses_best_bid_not_current_market_price`):
  SUBSUMED-BY-DESIGN — the law's sale side is `_exit_bid_breakpoints ← best_bid`.
- **fail-closed on missing bid** (`test_buy_no_edge_exit_requires_best_bid`):
  SUBSUMED — no `best_bid` ⇒ no breakpoints ⇒ HOLD/EVIDENCE_UNAVAILABLE.
- **fill-authority shares** (`test_exit_ev_gate_uses_fill_authority_shares…`
  asserts sizing uses `effective_shares`, not `size_usd/entry_price`): SUBSUMED —
  our exit path uses `self.effective_shares` (portfolio.py:829/881/989).
- **near-one / CI-overlap hold-value dominance** (retired triggers
  `CI_OVERLAP_HOLD_VALUE_DOMINATES`, `NEAR_SETTLEMENT_HOLD_VALUE_DOMINATES`):
  SUBSUMED — "hold when terminal value beats immediate sale" *is* the one-law
  comparison; the specific trigger strings collapsed into the {HOLD,
  SELL_REVERSAL, EVIDENCE_UNAVAILABLE, RED_FORCE_EXIT} vocabulary. Their tests
  assert the retired strings → dead, not a lost invariant.

### Conflict hunks + resolutions (8 files)

1. **`architecture/_schema_fingerprint.txt`** — generated schema hash. Do **not**
   hand-resolve; **regenerate** after resolving db-schema files (its inputs are
   unchanged by our side).
2. **`architecture/strategy_profile_registry.yaml`** — 3 hunks,
   `min_entry_price 0.05` (ours) vs `0.10` (theirs) + adjacent
   `min_settled_decisions`/`promotion_evidence_ref`. → **OURS**. Base held both
   0.10 and 0.05; our one-law collapse sets the universal venue band edge 0.05
   ([[price-band-production-law]]). Their 0.10 is the pre-collapse per-profile floor.
3. **`src/config.py`** — `hold_value_exit_costs_enabled()` (ours) vs
   `exit_correlation_crowding_rate()` (theirs) at the same region. Base had
   **both**; we deleted the crowding accessor, they kept it. → **OURS** (drop
   `exit_correlation_crowding_rate`). Safe: the only caller was
   `portfolio.py` (also resolved OURS); no other merged file imports it.
4. **`src/strategy/kelly.py`** — stepwise `ci_width > 0.10/0.15` + `lead_days`
   modifiers (theirs) vs deleted (ours). → **OURS**. SUBSUMED: their own docstring
   states the live global solver "presents `ci_width=0` here," so the modifier is
   a **no-op on the live path** either way; the stepwise branch is dead under
   `GLOBAL_KELLY_FRACTION=1.0`. Common `portfolio_heat` reciprocal attenuation is
   below the marker and identical on both sides.
5. **`src/strategy/strategy_profile.py`** — `is_phase_allowed` method (theirs) vs
   deleted (ours). → **OURS**. Safe: zero live callers in origin/live (only a
   docstring example + the def itself); our branch has 0 references.
6. **`src/state/portfolio.py`** — 3 hunks, all inside `evaluate_exit` /
   `_buy_*_exit`: old exit tree + `_compute_exit_correlation_crowding` +
   `_near_settlement_hold_confirmation_reason` + `FLASH_CRASH_PANIC` branch +
   `decision_scoped_value_validations` set (theirs) vs the one-law consumption
   surface (ours). → **OURS** — identical posture to Wave 1: dead branches under
   the one-law.
7. **`tests/test_hold_value_exit_costs.py`** — `TestPortfolioExitIntegration`
   calling deleted `_buy_yes_exit`/`_buy_no_exit(forward_edge=…, day0_active=…)`
   signatures (theirs) vs surviving fee-only `HoldValue` contract tests (ours).
   → **OURS** (their tests import a rewritten API; invariants SUBSUMED per LOST list).
8. **`tests/test_live_safety_invariants.py`** — tests `monkeypatch`-ing the
   **deleted** `src.state.portfolio._compute_exit_correlation_crowding` and the old
   `_buy_no_exit` signature / retired trigger strings (theirs) vs ours. → **OURS**
   (unrunnable against our tree; fill-authority-shares invariant SUBSUMED —
   exit uses `effective_shares`).

### Merge-procedure recommendation

**MERGE, not rebase.** A second merge commit is expected and correct — Wave 1
already landed `550dc4d03`; the merge-base is clean at `109c89cd7`, so `git merge
origin/live` replays only these 41 commits. Procedure:

1. Resolve the **8 conflict files OURS**, except **regenerate**
   `architecture/_schema_fingerprint.txt` from the resolved schema (do not pick a
   side).
2. Take **theirs verbatim** for all 22 auto-merging overlap files (recovery/token
   reconstruction, single-live cutover, governance antibodies) and the new
   migration `scripts/migrations/2026_07_position_token_split_reconstructed.py`
   (new file, clean).
3. The merged-tree dangling-symbol scan already passed — **no post-merge symbol
   surgery needed**. Run the suite green (origin/live's deleted-API tests are gone
   because we take OURS; expect no red from them).
4. **C1 alignment confirmed:** PR #445 (shadow-diagnostic-extinction) removes
   alternate/shadow vocabulary — same direction as our no-shadow law, no
   reintroduction of shadow paths.

**Coverage:** all 8 conflict files inspected hunk-by-hunk against base
(`109c89cd7`), ours, and theirs; the 5 recovery/identity commits confirmed
orthogonal to decision-law identity (0 hits) with the `_OPTIONAL_IDENTITY_COLUMNS`
contract intact in the merged tree; dangling-symbol scan run over the actual
written merge tree. Not independently re-run: the full pytest suite (recommended
gate before cutover, step 3).
