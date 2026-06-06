# PRE-ARM SAFETY AUDIT — 2026-06-02

```
Created: 2026-06-02
Last reused or audited: 2026-06-02
Authority basis: PRE-ARM safety triage; tasks #96 / #97 / #99; branch edli-correctness-recover-2026-06-02 @ d95f5e67
Mode: READ-ONLY audit. Daemon SHADOW, arm OFF, real_order_submit_enabled=False.
```

> Supersession note, 2026-06-05: the #99 cap/flood-guard discussion below is
> historical. Current EDLI no-cap authority is
> `docs/operations/LIVE_CAP_NO_CAP_REGRESSION_EVIDENCE_2026-06-05.md`.
> When `tiny_live_notional_cap_enabled=false`, there is no EDLI notional cap.
> When `tiny_live_daily_order_cap_enabled=false`, there is no hidden EDLI
> order-count cap, including the rate-window table. Do not use this older audit
> to reintroduce a non-configurable notional ceiling or date-keyed count cap.

## Executive summary (decision-grade)

1. **#97 is NOT 14 failures — it is 5.** Live-run on branch HEAD (`ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1`, an
   untracked-script collection guard unrelated to the audit): `test_edli_live_canary.py` = **32 passed** (all 9 presumed
   failures already resolved); the remaining set is **5 failed, 69 passed** across the other two files.
2. **Of those 5: 3 are fixture-rot, 1 is stale-expectation, 1 is a REAL pre-arm config-bleed** (the #99 flood-cap
   regression surfacing through an invariant test). Only the last one is a genuine armed-path safety concern.
3. **#99 is a CONFIRMED REGRESSION and the single hard PRE-ARM BLOCKER.** HEAD config = `5.0 / 1`; working tree =
   `185.0 / 1000`. The `$5→$185` notional raise was bundled with `max_orders_per_day 1→1000`, deleting the only
   global daily flood guard. **Worst-case armed exposure: up to 1000 venue orders/day at $185 = ~$185,000/day**, vs the
   original `$5/day` envelope. No independent rate-limiter exists — the two caps are a coupled pair in `settings.json`.
4. **#96 is LOWER severity than feared.** (a) The `with conn:`/SAVEPOINT atomicity trap is **already mitigated by
   design** (DR-33-B + memory rule L30) — the live armed write path (reactor) uses explicit `SAVEPOINT` discipline with
   zero `with conn:`; no fix required. (b) The bankroll 1800s-cache-vs-sizing staleness is a **REAL but low-severity**
   invariant gap (stale-high bankroll after a fill → next order over-sized; negligible at canary scale). (c) The bridge
   `position_id` 28-bit truncation is a **REAL structural cap** but collision probability is `<0.00003%` at <100 fills —
   not a canary blocker, a scale antibody.
5. **PRE-ARM BLOCKER count = 1 hard (#99), 2 soft (#96b, #96c) to fix-or-accept-with-eyes-open before scale.** The
   crossing-decision invariant (F34) is real and valid but unreachable from its tests — it does NOT block arm.

---

## (1) #97 — 14-test triage table

Actual failure count is **5**, not 14. `test_edli_live_canary.py` passes 32/32. Run command (read-only):
`ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1 python -m pytest tests/money_path/test_edli_online_invariants.py tests/engine/test_crossing_decision.py tests/money_path/test_edli_live_canary.py -q` → `5 failed, 69 passed`.

(The conftest writer-lock antibody — `tests/conftest.py:386` — fires on untracked `scripts/calibration_bakeoff.py:190,483`
[`git status` = `??`], an artifact unrelated to the audit; bypassed via the documented env var for the read-only run.)

| # | Test (file:line of assertion / crash) | Class | Crash / mismatch message | Root | Armed-path bug? |
|---|---|---|---|---|---|
| 1 | `test_crossing_decision.py::test_flag_off_does_not_call_crossing_decision` (crash @ `src/engine/cycle_runtime.py:1539`) | **FIXTURE-ROT** | `ValueError: PASSIVE_FILL_PROBABILITY_UNMODELED: post_only_passive_limit requires PassiveMakerExecutionContext` | `_snapshot()` fixture `tests/engine/test_crossing_decision.py:110-124` lacks `min_order_size`; `_run_reprice` passes `conn=None` (`:147`) → `estimate_passive_maker_execution()` returns `None` → `passive_maker_context` stays `None` → guard raises before reaching the F34 assertion. Production path has non-None conn + real DB history. | **NO** |
| 2 | `test_crossing_decision.py::test_flag_on_without_intent_gate_does_not_call_crossing_decision` (same crash site) | **FIXTURE-ROT** | same `PASSIVE_FILL_PROBABILITY_UNMODELED` | same fixture defect | **NO** |
| 3 | `test_crossing_decision.py::test_flag_on_with_intent_gate_calls_crossing_decision_with_intended_order_size` (same crash site) | **FIXTURE-ROT** | same `PASSIVE_FILL_PROBABILITY_UNMODELED` | same fixture defect (test logs `F34_CROSSING_DECISION PASSIVE` then dies before its assert) | **NO** |
| 4 | `test_edli_online_invariants.py:53` (`tiny_live_max_notional_usd == 5.0`) | **REAL CONFIG-BLEED (#99)** | assertion fails: config = `185.0` (`config/settings.json:124`) | The invariant test is the antibody for the #99 cap regression — config raised to 185.0 without a replacement rate-limit and without updating the threshold. | **YES — same root as #99** |
| 5 | `test_edli_online_invariants.py:372` (`pytest.raises EDLI_LIVE_CANARY_READINESS_FAIL`) | **STALE-EXPECTATION** | `DID NOT RAISE RuntimeError` | Post-PR #367 (`:367-368` comment) stage paths configured in `settings.json:118` → `_require_stage_file_paths` no longer raises; readiness returns `WAITING_FOR_QUALIFYING_EVENT`, treated as acceptable at `src/main.py:393-396`. Behavior correct; `pytest.raises` block never updated. | **NO** |

**Counts:** config-bleed/real = **1** (#4, = the #99 regression), fixture-rot = **3**, stale-expectation = **1**.
**Real armed-path pre-arm blockers from #97 = 1** (and it is the #99 item, not a distinct bug).

> Note on `tiny_live_max_orders_per_day == 1` (`test_edli_online_invariants.py:54`): this assertion ALSO fails against
> the working tree (`1000`). It is the same #99 root as row #4; counted under #99, not as a 6th distinct failure (the
> two assertions live in one test function, which reports as one FAILED).

---

## (2) #96 — hardening verdict per sub-item

### #96(a) — `world_write_lock` txn-commit trap (`with conn:` inside SAVEPOINT)
**Verdict: ALREADY MITIGATED BY DESIGN — no fix required.**

- The live armed write path is the EDLI reactor. `src/events/reactor.py:227,235,241,274,286,290-291,356-357` uses
  **explicit** `SAVEPOINT edli_reactor_event` / `RELEASE` / `ROLLBACK TO` and contains **zero** `with conn:` calls.
- `EventStore` (`src/events/event_store.py`) uses raw `conn.execute()` only.
- The `with conn:` occurrences in `src/state/ledger.py` are **5** (lines 81, 145, 203, 243, 290) — leg-2's "two" was an
  undercount — but **all 5 are in `_ensure_*` schema-migration helpers** (`_ensure_token_suppression_reason_schema`,
  `_ensure_day0_window_entered_event_type`, `_ensure_venue_position_observed_event_type`,
  `_ensure_review_required_event_type`, etc.), called from `init_schema` at boot, top-level, never SAVEPOINT-nested.
- The canonical-append helper that *could* be called inside a caller's SAVEPOINT was deliberately rebuilt under
  **DR-33-B (2026-04-24)** to use explicit nested `SAVEPOINT` rather than `with conn:` — see the design note at
  `src/state/ledger.py:470-495`, which cites **memory rule L30** by name ("`with conn:` commits + releases the
  innermost active SAVEPOINT… broke the caller's ROLLBACK path"). The trap #96 names is the exact one this comment
  documents as already closed.
- `src/state/db.py:9243` (`with conn:` in `record_token_suppression`) — callers (`cycle_runtime.py:2734`,
  `chain_reconciliation.py:959`, `harvester.py:2491`) are not inside an active SAVEPOINT at the call site.

→ **No nested `with conn:`/SAVEPOINT atomicity loss exists on the live armed path.** Recommend adding a CI antibody
that greps `src/events/reactor.py` for `with conn:` (assert zero) to prevent regression, but no live fix needed.

### #96(b) — bankroll 1800s cache vs sizing staleness
**Verdict: REAL BUG, LOW severity, design-acknowledged but under-documented as a sizing risk. Soft pre-arm item.**

- Sizing calls `_runtime_bankroll_usd(cached_only=True)` (`src/engine/event_reactor_adapter.py:812-815`) →
  `bankroll_provider.cached()` honors a 1800s resilient bound (`src/runtime/bankroll_provider.py:61`,
  `_DEFAULT_CACHED_RESILIENT_BOUND_SECONDS = 1800.0`, comment "survives RPC blip clusters" = the task #64 fix).
- Cycle-start warm forces one fresh fetch per ~1-min cycle (`src/main.py:3424`, `current(max_age_seconds=0.0)`). If that
  warm FAILS (RPC blip), `cached()` keeps returning the last good value up to 1800s old, with no `KELLY_PROOF_MISSING`.
- **The broken invariant:** if a fill occurs between the last successful fetch and the next sizing call, the cached
  bankroll is **stale-high** (the fill spent wallet balance), so the next order is sized against an inflated bankroll.
  `cached_only=True` suppresses the fail-closed that staleness should trigger. No staleness haircut is applied.
- **Severity LOW at canary:** single, infrequent fills; the over-size error is bounded by one fill's notional. The
  1800s bound was a deliberate availability tradeoff (don't reject all canary on a blip). **Fix-or-accept decision
  needed before scale**, not a hard canary blocker.

### #96(c) — bridge `position_id` 28-bit collision
**Verdict: REAL structural cap, NEGLIGIBLE at canary, antibody-before-scale. Soft pre-arm item.**

- `edli_bridge_position_id` (`src/events/edli_position_bridge.py:80-89`) returns `("edli" + sha256_hex)[:11]` =
  4-char literal prefix + **7 hex chars** of SHA-256 = **28 bits** entropy (`_TRADE_ID_WIDTH = 11`, `:74`). Namespace
  = 268,435,456. Birthday collision ≈ 0.1% @ 732 positions, ≈ 1% @ 2,322 positions.
- A collision silently merges two distinct `aggregate_id`s into one `position_current` row via
  `ON CONFLICT(position_id) DO UPDATE` (`:455-460`), corrupting `shares`/`cost_basis` for the earlier position with no
  error raised.
- **At canary (<100 fills) probability is `<0.00003%`** — not a canary blocker. The 11-char width is a legacy
  `trade_id`-shape compatibility cap (design comment `:71-73`), not a safety decision. **Widen or add a uniqueness
  antibody (assert no two live `aggregate_id`s map to one `position_id`) before scaling fill volume.**

---

## (3) #99 — flood-cap verdict + worst-case armed orders/day

**Verdict: CONFIRMED REGRESSION. HARD PRE-ARM BLOCKER.**

| Surface | HEAD (committed, d95f5e67) | Working tree (current) | Source |
|---|---|---|---|
| `tiny_live_max_notional_usd` | **5.0** | **185.0** | `config/settings.json:124` |
| `tiny_live_max_orders_per_day` | **1** | **1000** | `config/settings.json:125` |

- The `$5→$185` notional raise (legitimately needed for real order sizes) was bundled with the `1→1000`
  order-count change, **destroying the only global daily flood guard**.
- **`max_orders_per_day` is the ONLY global daily order-count limiter.** The cap mechanism is a shared slot pool:
  `for slot in range(1, int(max_orders_per_day)+1)` (`src/events/live_cap.py:211`) inserting into
  `edli_live_cap_day_slots` with `PRIMARY KEY (cap_scope, cap_date, slot)`
  (`src/state/schema/edli_live_cap_usage_schema.py:43`). The scope is global `'tiny_live_canary'` — **not per-market,
  not per-city** (three reserve sites: `src/engine/event_reactor_adapter.py:1434,1639,1651`). With 1000, the pool
  admits 1000 distinct submissions per calendar day.
- **No independent rate-limit exists.** The only other bounds are per-cycle (`proof_limit` default 10/max 50;
  `redecision_max_per_cycle` default 50/max 200) — not a per-day frequency limiter. The two safety controls (notional
  cap + order-count cap) are set as a coupled pair in `settings.json` with no structural separation. The fix #99 asks
  for — a rate-limit decoupled from the notional cap — **does not exist in the codebase**.

**Worst-case armed orders/day:**
- Reactor fires every 1 minute (`src/main.py:5401-5403`, `interval, minutes=1`) → 1440 cycles/day.
- At `proof_limit=50`/cycle the reactor can *attempt* 50×1440 = **72,000 reservations/day**.
- The day-slot pool **caps actual CONSUMED orders at `max_orders_per_day` = 1000/day** (hard wall: `LiveCapError
  "live cap max_orders_per_day exhausted"`, `live_cap.py:224`).
- **Worst-case if armed (`real_order_submit_enabled=True`): up to 1000 venue orders/day** before the cap stops it.
  At $185/order that is **up to ~$185,000/day exposure** — 3 orders of magnitude above the original `$5/day` design
  envelope, global (not per-market/city). A re-decision bug looping on one event family could burn the whole 1000.
- Currently `real_order_submit_enabled=False` → no venue orders fire today; this is latent until arm.

---

## (4) PRE-ARM SAFETY BLOCKER list (prioritized)

| Pri | Blocker | Status | What "fixed" looks like | Evidence |
|---|---|---|---|---|
| **P0 — HARD** | #99 flood-cap regression: `max_orders_per_day=1000` + `notional=185` with no decoupled rate-limit. Worst case ~$185k/day if armed. | **OPEN — must fix before any live canary** | Add a real per-day (and ideally per-market) order-frequency limiter independent of the notional cap; set the canary count cap back to a conservative value (1–N) until the rate-limiter exists; re-green `test_edli_online_invariants.py:53-54` to whatever the new agreed envelope is (don't just delete the antibody). | `config/settings.json:124-125`; `src/events/live_cap.py:211,224`; `edli_live_cap_usage_schema.py:43`; `src/main.py:5401-5403`; `event_reactor_adapter.py:1434,1639,1651`; test `:53-54` |
| **P1 — SOFT (decide before scale)** | #96(b) bankroll stale-high after fill → next order over-sized; `cached_only=True` suppresses fail-closed within 1800s. | OPEN | Apply a staleness haircut OR force a post-fill bankroll refetch before the next sizing; or explicitly accept the 1800s bound with a logged rationale tied to fill cadence. | `event_reactor_adapter.py:812-815`; `bankroll_provider.py:61`; `main.py:3424` |
| **P1 — SOFT (antibody before scale)** | #96(c) bridge `position_id` 28-bit truncation → silent `position_current` merge on collision. Negligible <100 fills. | OPEN | Widen `_TRADE_ID_WIDTH` (or store full hash) OR add a uniqueness antibody asserting no two live `aggregate_id`s collide to one `position_id`. | `edli_position_bridge.py:74,80-89,455-460` |
| **P2 — FIXTURE HYGIENE (not arm-blocking)** | 3 crossing-decision tests crash before their F34 assertion (fixture lacks `min_order_size` + conn). The F34 crossing invariant is real and valid but currently unreachable from these tests. | OPEN | Populate `_snapshot()` fixture with `min_order_size` and a non-None conn (or stub `estimate_passive_maker_execution`) so the test exercises F34 instead of dying at the passive-context guard. | fixture `test_crossing_decision.py:110-124,147`; guard `cycle_runtime.py:1539` |
| **P3 — TEST UPDATE (not a bug)** | `test_live_canary_requires_stage_evidence_file_paths` expects an error that PR #367 intentionally removed. | OPEN | Update the `pytest.raises` block to assert the post-#367 `WAITING_FOR_QUALIFYING_EVENT`-accepted behavior. | test `:367-372`; `main.py:393-396`; `settings.json:118` |
| **P4 — REGRESSION GUARD (recommended)** | #96(a) already mitigated by design (DR-33-B / L30) but unguarded against future reintroduction. | RECOMMEND | Add CI grep antibody: assert `src/events/reactor.py` contains zero `with conn:`. | `reactor.py:227-357`; `ledger.py:470-495` |

**Bottom line for the operator:** exactly **one HARD blocker (#99)** stands between SHADOW and a live canary. #96(a) is
already closed by design; #96(b)/(c) are real but soft (decide-and-document before scale, negligible at first-fill
canary scale). The crossing-decision and stage-readiness test failures are hygiene, not safety. Do **not** arm until
the #99 daily flood guard is restored as a rate-limiter decoupled from the notional cap.
