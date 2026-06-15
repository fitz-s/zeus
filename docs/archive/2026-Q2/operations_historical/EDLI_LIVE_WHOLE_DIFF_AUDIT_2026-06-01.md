# EDLI Live Whole-Diff Adversarial Audit — 2026-06-01

- **Scope:** `origin/main..HEAD` = `563cc5a6fa..67e3d74673`, 26 commits, +9,695/−328, 45 files.
- **Mode:** READ-ONLY adversarial. Started THOROUGH; **escalated to ADVERSARIAL** (1 SEV-1 + multiple SEV-2 found; lock-fix self-contradiction is systemic).
- **Runtime posture at HEAD:** SHADOW (`real_order_submit=False`), 0 real orders ever executed. Most SEV-1/SEV-2 are LATENT — they fire only when armed.
- **Pending (NOT in this diff, not flagged for absence):** Wall A (bias CI-split, ERA ~3133-3178 + `_snapshot_p_raw`:3365), Wall B (single-snapshot binding, ERA ~1121-1157 + `decision_kernel/certificates/execution.py`). Working tree holds uncommitted Wall B edits in `execution.py` + `event_reactor_adapter.py`.

---

## COHERENCE VERDICT

The 26 commits are a **coherent feature**, not contradictory patches. They build one EDLI event-driven live-execution lane by sequentially clearing real gate walls. Cross-cutting invariants are handled **consistently** across surfaces:

- `would_cross_book` is conditioned on `post_only is not False` at BOTH enforcement sites (`verifier.py:456`, `live_order_aggregate.py:602`) — no unconditional fourth site. **No contradiction.**
- The maker/taker order-type tuple is gated by one shared helper (`_order_spec_for_mode` emit-side, `_assert_order_type_tuple_coherent` verify-side) — fail-closed toward maker. Cert emits `order_mode`+tuple together; translator (`event_bound_final_intent.py:206`) re-derives `is_taker` from all three fields consistently. **No contradiction.**
- `neg_risk` propagation (commit 6) is threaded reactor receipt → ERA actionable → cert → executor, with `!=` consistency checks at execution_command / executor_expressibility / executor-vs-snapshot. **Coherent.**
- The world-DB write-mutex fix is applied to all three in-process writers (reactor per-event, ingestor, emit block) and they are SEQUENTIAL (emit released at `main.py:3455` before `process_pending` at `:3556`) — **no self-deadlock** between them, and the mutex is non-reentrant but never re-acquired within a single held span (verified `_process_one` writes only to `self._store.conn`).

**SILENT REVERTS: none found.** Verified at HEAD: would_cross_book conditioning (both sites present and consistent), GATE#85 post_only exemption (live_order_aggregate.py:300-306 present), neg_risk receipt field (reactor.py:79 present), scheduler `add_job` wiring is still literal direct calls (`main.py:5108-5137`) so the AST-scan `assert_writer_jobs_registered` boot guard is NOT broken by a spec-list refactor (the prior precedent does not recur). `world_write_mutex` import live at reactor.py:19.

---

## SEV-1 — BLOCKS FIRST ARMED ORDER (would corrupt / abort a live taker submit)

### SEV-1.1 — Cert vs executor `expected_fill_price_before_fee` exact-equality fails on the first armed TAKER order (thin / multi-level book), and is fragile via a float round-trip even on a single-level book.

- **Evidence:**
  - Cert build (ERA, working-tree Wall B): `event_reactor_adapter.py:411-421` sweeps `_desired_shares` (PRE depth-cap) at `_limit_price_d`, stores `sweep_expected_fill_price = float(_depth_sweep.average_price)`.
  - Cert builder: `decision_kernel/certificates/execution.py:52-66` then caps `size = min(size, available_crossable_shares)` (POST-cap) but stores the cert payload `expected_fill_price_before_fee = sweep_expected_fill_price` — i.e. the VWAP of the **pre-cap** sweep (execution.py:108). No re-sweep at the capped size.
  - Executor: `executor.py:1752` re-sweeps with `requested_size_value=Decimal(str(submitted_shares))` = the cert's **post-cap** `size`, then `executor.py:1778` asserts `sweep.average_price != intent.expected_fill_price_before_fee` → `ValueError` → wrapped as `PreVenueSubmitError` (executor.py:387) → terminal `PRE_SUBMIT_ERROR`.
  - `simulate_clob_sweep` VWAP depends on `requested_size_value` (consumes levels until filled — `execution_intent.py` sweep loop). On a multi-level book where the depth-cap binds (`available_crossable_shares < _desired_shares` — the exact case the cap exists for), pre-cap VWAP ≠ post-cap VWAP → exact `!=` trips → **first armed taker order terminally rejects on a thin book**, which is precisely when taker mode is meant to be used.
  - Float round-trip: cert stores `float(Decimal_vwap)` (ERA:421); executor compares against `_decimal(str(expected_fill_price_before_fee))` (event_bound_final_intent.py:136) vs the executor's own `Decimal` sweep. `Decimal(str(float(d)))` can differ from `d`, so the exact `==` can fail even on a single-level book where sizes coincide.
- **Why it matters:** The first armed taker FOK is the canary's whole purpose. This path has NEVER run; tests pass because they don't exercise the cap-binds-on-multi-level case with a real cert→executor handoff. A first armed order would silently abort at pre-venue and re-loop boot readiness (cap-release path is correct, so no stuck cap — but no order ever lands).
- **Confidence:** HIGH on mechanism; MEDIUM on "fires on first order" (depends on first candidate's book shape). On a single-level book it MAY pass; on any multi-level book where the cap binds it WILL fail.
- **Realist check:** This lands inside the Wall B region the operator already plans to finish. Worst case is "no order ever submits" (no capital corruption, fail-closed). Not downgraded below SEV-1 because it defeats the entire arm objective and the equality is unverifiable without a real run.
- **Fix direction:** After capping `size` to `available_crossable_shares`, RE-SWEEP at the capped size and store THAT VWAP as `expected_fill_price_before_fee`; and replace the exact `!=` at executor.py:1778 with a tick/epsilon tolerance (`abs(a-b) <= tick/2` or compare quantized Decimals), eliminating the float round-trip fragility. Add a test: multi-level book, cap binds, assert executor sweep-equality passes end-to-end (prove RED against current code).

---

## SEV-2 — FIX BEFORE ARM

### SEV-2.1 — World-DB write mutex is held across venue HTTP (JIT `/book` fetch + order POST) in live-submit mode — violates the fix's own contract and re-introduces lock-starvation precisely when armed.

- **Evidence:** Reactor acquires `world_write_mutex()` per event (`reactor.py:196`) and holds it across `self._submit(event)` (`reactor.py:333`). In live-submit mode `self._submit` = `submit_adapter` (`main.py:3545`), whose pre-submit authority calls `_edli_pre_submit_jit_book_quote_provider` → `clob.get_orderbook_snapshot(token_id)` (live HTTP `/book`, `main.py:3896-3897`) and `executor_submit` → real executor POST. The fix's own contract (`db.py:101-104`, `reactor.py:186-189`) states the mutex must NEVER be held across HTTP/venue fetch.
- **Why it matters:** Every armed submit holds the single world-WAL write lock across one (or more) venue round-trips, starving the market-channel ingestor / CollateralLedger for the POST duration — the exact "database is locked" pathology the 26 commits exist to kill, reappearing under live load.
- **Confidence:** HIGH. **Latent:** in SHADOW `live_submit_effective=False` → no-submit adapter → no HTTP under the mutex, so it does not bite today.
- **Realist check:** Mitigated by being SHADOW-only now and by per-event commit bounding the lock to one event. Still SEV-2 because it activates on arm. Not SEV-1 (no corruption; degradation only).
- **Fix direction:** Restructure the reactor event unit so the mutex wraps ONLY the DB write+commit, releasing it before `self._submit` does any venue I/O (mirror the ingestor's "collect under lock, execute actions after release" pattern at `market_channel_ingestor.py:283-296`). Or move submit HTTP outside the claim→mark write unit.

### SEV-2.2 — `world_write_lock` commits a caller's pre-existing transaction.

- **Evidence:** `db.py:118-126`: when `conn.in_transaction` is already True the CM does NOT `BEGIN` but still `conn.commit()` on exit, committing the caller's outer transaction prematurely. Docstring acknowledges it.
- **Why it matters:** Any caller that wraps `world_write_lock` around a sub-step of a larger transaction loses atomicity — the outer txn is committed at the inner block's exit. No current caller hits this (only `world_write_mutex()` — the bare lock — is used in the hot paths), so it is latent.
- **Confidence:** MEDIUM (no live caller today). **Realist check:** unused-API hazard → SEV-2/borderline SEV-3. Keep at SEV-2 because it is a trap for the next author.
- **Fix direction:** Track whether the CM opened the txn; only commit when `began` is True, else yield without committing (let the caller own the boundary). The `_ = began` no-op at `db.py:134` signals the author knew but punted.

### SEV-2.3 — `bankroll_provider.cached()` resilient bound widened 300s → 1800s; verify it never feeds order SIZING in live.

- **Evidence:** `bankroll_provider.py:425,473`. `cached()` now serves a last-good wallet value up to 30 min stale. Justified for proof-only reads (wallet moves only on own fills). RISK: after a real fill the wallet DROPS; a stale cached bankroll could over-size a subsequent candidate past the live cap if `cached()` (not `current()`) ever feeds Kelly/cap sizing.
- **Why it matters:** Live cap integrity. The submit path uses `current()` (300s); `cached()` is documented proof-only — but this must be VERIFIED, not assumed, before arming.
- **Confidence:** MEDIUM (depends on whether any sizing path reads `cached()`). **Realist check:** the live-cap reservation is a separate ledger gate, so over-size is likely caught downstream → SEV-2 not SEV-1. NOT downgraded further because it touches capital sizing.
- **Fix direction:** Grep all `cached()` consumers; assert none reach `kelly_size_usd` / `live_cap_reserved_notional_usd` derivation in live mode. If any do, pin them to `current()` or a tight bound.

### SEV-2.4 — `edli_bridge_position_id` has only 28 bits of entropy (`"edli"` + 7 hex chars); birthday-collision 50% at ~19.3k positions.

- **Evidence:** `edli_position_bridge.py:87-95`: `("edli" + sha256(aggregate_id))[:11]` → 4 fixed + 7 hex = 2^28 space. Computed: 50% collision at ~19,288 positions.
- **Why it matters:** A `position_id` collision across two distinct EDLI aggregates would make one fill's bridge `ON CONFLICT(position_id) DO UPDATE` silently overwrite an UNRELATED position → wrong shares/cost_basis/token → capital mis-tracking, chain-reconcile mismatch.
- **Confidence:** HIGH on math; LOW on near-term occurrence (canary volume is tiny). **Realist check:** at canary scale (single-digit positions) collision probability is negligible; this is a scaling cliff, not an arm blocker → SEV-2 (note), arguably SEV-3 for the canary. Kept SEV-2 because the failure mode is silent capital cross-contamination.
- **Fix direction:** Use the full digest width the `position_id` column allows (drop the 11-char truncation, or namespace as `edli:<full-or-longer-hash>`), or add a `UNIQUE(aggregate_id)` provenance column and assert no cross-aggregate reuse before UPDATE.

---

## SEV-3 — NOTE

- **SEV-3.1 — `executor.py` PreVenueSubmitError wrapper catches only `(ValueError, TypeError)`** (executor.py:387). `_final_intent_snapshot_metadata` / `_legacy_entry_intent_from_final` can raise `sqlite3.Error`, `KeyError`, `DecimalException` — those bypass the wrapper and are classified `POST_SUBMIT_UNKNOWN` (the bug commit `ae8186a3ff` set out to fix). Broaden the pre-venue try to `except Exception` for the genuinely-pre-venue span, or enumerate the additional pre-venue exception types. Confidence MEDIUM.
- **SEV-3.2 — Bridge `_edli_events_table` world-prefix branch is untested.** `edli_position_bridge.py:98-116` selects `world.edli_live_order_events` when world is ATTACHed; bridge tests use a single `init_schema` conn so only the unqualified branch runs. The production INV-37 ATTACH path is exercised only in live. Add a test on a trade+world-ATTACHed conn. Confidence HIGH (coverage gap, not a bug).
- **SEV-3.3 — Legacy venue `OrderArgs` (polymarket_v2_adapter.py:2703, UNCHANGED in diff) carries no FOK/time_in_force field.** A taker FOK's order-type must be conveyed to the venue somewhere on the never-run POST path; verify the FOK semantics actually reach `create_and_post_order`, else a "FOK" submits as a resting limit. Out-of-diff legacy; flagged for the first-armed-order checklist. Confidence LOW (path not traced to venue in this audit).

---

## COVERAGE INTEGRITY

- **Strong / genuine relationship tests:** `test_edli_position_bridge.py` (479 LOC) asserts token-election-by-direction (buy_yes→token_id, buy_no→no_token_id), idempotent replay (`created` False + single row + single OPEN_INTENT), partial-fill size-weighted VWAP summing, and a REAL `chain_reconciliation.reconcile` relationship test that matches the bridged row by token and sets chain_shares. `test_taker_execution_law.py` (609), `test_event_reactor_no_bypass.py`, `test_continuous_redecision_exit.py` all green. **Ran at HEAD: 113 passed, 1 xfailed.**
- **Gap (maps to SEV-1.1):** NO test exercises the cert→executor `expected_fill_price_before_fee` equality on a multi-level book where the depth-cap binds. The dominant first-armed-order risk is unproven. **This is the single most important missing test.**
- **Gap (SEV-3.2):** bridge INV-37 ATTACH path untested.
- Strategy-key CHECK: `settlement_capture` is valid (`architecture/2026_04_02_architecture_kernel.sql:126`) — bridge first-write will NOT crash on the CHECK (verified).

---

## INV / K1 COMPLIANCE

- **INV-37 (bridge):** COMPLIANT. `materialize_position_current_from_edli_fill` reads `edli_live_order_events` (world) and writes `position_events`+`position_current` (trade) on ONE `get_trade_connection_with_world_required` connection (`main.py:4476`); the canonical write nests its own SAVEPOINT (`ledger.py:516`); the bridge opens no independent connection and does not commit (caller commits at `main.py:4492`). Producer→consumer wiring complete (`main.py:4418` collects, `:4477` consumes after world commit at `:4453`).
- **INV-37 (market_scanner per-item commit):** `market_scanner.py:27` commits per row on the trade-rooted connection that owns `executable_market_snapshots` — single-connection, no cross-DB independent write. Compliant.
- **K1 ownership:** EDLI live-order tables declared `legacy_archived` on trade DB (commit `0e24670839`, `db_table_ownership.yaml +39`); world_class EDLI tables read from world. Consistent.

---

## PUSH-READINESS

**REVISE before push is NOT required for SHADOW-safety, but DO NOT ARM until SEV-1.1 and SEV-2.1 are fixed.**

- The 26 commits are internally coherent, contain **no silent reverts**, pass their suites, and do not destabilize the current SHADOW daemon (all SEV-1/SEV-2 are latent on the never-run armed path).
- **Pushing to `main` is acceptable** IF the operator accepts that the armed path is known-broken (SEV-1.1) and will land Wall B's re-sweep fix + the mutex-vs-HTTP fix (SEV-2.1) before flipping `real_order_submit=True`. Since Wall B is already pending in this exact region, fold SEV-1.1's "re-sweep at capped size + epsilon-equality" into the Wall B completion.
- **Recommended gate before arm:** (1) SEV-1.1 re-sweep + tolerance + the missing multi-level-cap test proving RED; (2) SEV-2.1 mutex released before submit HTTP; (3) SEV-2.3 verify `cached()` is not on the sizing path; (4) SEV-3.3 confirm FOK reaches the venue POST.

**SILENT REVERTS: none found** — verified at HEAD for every cross-fix-touched invariant (would_cross_book ×2 sites, GATE#85 post_only exemption, neg_risk receipt field, scheduler add_job wiring form, world_write_mutex import).
