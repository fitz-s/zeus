# Multi-Winner Auction — Implementation Plan (2026-07-19)

Status: PROPOSED (design only; no code changed by this document).
Author task: relax the single-winner-per-cycle constraint in the global order auction.
Evidence base: `docs/evidence/capital_efficiency_2026_07_19/capital_utilization.md`
(82.1% mean idle bankroll; ~707 Kelly-passed candidates → ~10 fills on 07-07;
deployed capital returned 18.1%/19d vs bankroll 2.96%/19d).

Money-path change: touches the live entry path. STOP-AND-PLAN class per AGENTS §3.
Recommended change is deliberately confined to ONE reactor decision point so the
money-path core (selection, sizing, collateral, actuation, command recovery,
preflight) is left byte-for-byte unchanged.

---

## TL;DR

- The throttle is a single deliberate line: after one auction epoch the reactor
  `return result`s instead of running a second epoch in the same wake
  (`src/events/reactor.py:1208-1210`, comment: *"One global auction epoch may
  start at most one venue submit. Do not page into a second auction inside the
  same reactor cycle."*). The daemon then sleeps ~1 min (APScheduler interval
  job `src/main.py:6657-6663`; `reactor_scan_interval_seconds` default 60,
  `config/settings.example.json:67`) before the next single submit.
- **Recommended: Option (b), sequential re-auction as a bounded caller-level
  loop in the reactor.** Replace the unconditional `return result` with a loop
  that re-invokes the *existing, unmodified* single-winner epoch while the prior
  epoch submitted an order and the wake's wall-clock budget / preemption hooks
  allow.
- Why it is safe with essentially no core change: **submits stay strictly
  serialized** — one fully durable command at a time, each committing its
  collateral reservation + venue command before the next epoch begins. The loop
  removes the *idle sleep between submits*, nothing else. Every invariant that
  already holds across two consecutive 1-min cycles holds across two consecutive
  in-wake iterations, because they are the identical operation.
- The wealth witness already does the hard part: each fresh epoch re-captures
  `spendable_cash = pusd_balance − Σ(pending BUY reservations)`
  (`src/engine/global_auction_universe.py:2790-2794`), so winner #2 is sized
  against cash winner #1 already committed — correct sizing for free, zero change
  to the sizing layer.

---

## 1. The invariant(s) that motivated single-winner (file:line)

Single-winner is enforced at **four per-epoch surfaces** plus **one
serialization gate**. Critically, all four are *per-epoch* — they say "one epoch
emits at most one submit," not "one submit per wall-clock wake." Only surface (E)
turns that into a per-wake restriction.

**(A) Result dataclass invariant.** `GlobalBatchSubmitResult.__post_init__`
(`src/events/reactor.py:931-963`) hard-raises unless `venue_submit_count ∈ {0,1}`,
`winner_event_id` is scalar, exactly one submitted receipt exists, and the
submitted set equals `{winner_event_id}`. `next_claim_event` is mutually
exclusive with a winner this epoch (`:945-950`) — a winner that needs an
un-paged claim carrier is explicitly deferred to a *future* cycle via
`_queue_global_winner_for_claim` (`:1327-1348`).

**(B) One-shot actuation capability.** `GlobalOneShotActuator`
(`src/engine/global_batch_runtime.py:178-189`) raises
`GLOBAL_ACTUATION_CAPABILITY_CONSUMED` on a second `consume()`; consumed once at
`:2613`. The venue submit count is asserted `∈ {0,1}` at `:2610-2625`
(`RuntimeError("GLOBAL_ACTUATION_VENUE_COUNT_INVALID")`). Preflight mints a
single-use `_GlobalWinnerBindingToken` binding event_id + actuation identity +
book epoch + wealth witness + deadline
(`src/engine/event_reactor_adapter.py:7378-7395`); `_actuate_preflighted`
(`:7426-7487`) re-validates every token field before `_submit_inner`.

**(C) Certificate binding guard.** `_build_live_execution_command_certificates`
(`src/engine/event_reactor_adapter.py:16033-16059`) raises
`GLOBAL_ACTUATION_CERTIFICATE_BINDING_INVALID` unless
`receipt.global_actuation.winner_event_id == event.event_id` (scalar identity).

**(D) Frozen single wealth/q/book cut → single capital scalar.** The whole epoch
runs on one immutable information vector: *"The complete q/book/wealth cut is
immutable from this point forward. Later global wakes belong to the next epoch"*
(`src/engine/global_batch_runtime.py:2138-2142`). `selection_wealth` is captured
once (`:2118`) and `capital_limit_usd = selection_wealth.spendable_cash_usd` is
passed once (`:2261` → `global_single_order_auction.py:371` →
`select_global_single_order`, `src/solve/solver.py:3345`). The solver rejects any
candidate whose wealth identity has moved: `CAPITAL_IDENTITY_SUPERSEDED`
(`solver.py:3443-3463`). This is *why* one epoch can only place one order safely:
its cash scalar is frozen; a second order sized against the same scalar would be
double-counting cash unless a new cut is taken.

**(E) Reactor page-return (the actual per-wake throttle).**
`process_pending` runs `_process_global_event_batch` once, then unconditionally
`return result` (`src/events/reactor.py:1197-1210`); `_process_global_event_batch`
docstring: *"let one opaque adapter auction act once"* (`:1255`). **This is the
line that converts a per-epoch limit into one-submit-per-minute.**

**(F) In-flight-buy serialization gate.** `process_current_global_batch` opens
with `probe_inflight_buy_ambiguity(trade_conn)` →
`CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS`
(`src/engine/global_batch_runtime.py:1861-1862`). Contrary to a first reading,
this does **not** block whenever a prior BUY is unresolved. It blocks **only when
a pending BUY cash effect lacks a persisted command bound**
(`src/engine/global_auction_universe.py:2326-2424`, docstring: *"Reject only
in-flight BUY cash effects lacking a persisted command bound"*): a pending
`collateral_reservations` PUSD_BUY / unsettled `OUTGOING_DEDUCTION` row whose
`LEFT JOIN venue_commands` yields a valid `(side=BUY, intent_kind=ENTRY, size)`
row returns a finite bound and is **not** ambiguous. A cleanly-recorded winner #1
therefore does not trip this gate for winner #2's iteration. (It is a genuine
fail-closed only when we reserved/deducted cash but cannot say for which order —
a real incident.)

### Not an invariant here: the FDR budget

Benjamini-Hochberg FDR is applied **upstream**, at edge-certificate generation
(`src/engine/qkernel_spine_bridge.py:166-306, 2773-2836`;
`src/strategy/selection_family.py::DEFAULT_FDR_ALPHA`;
`bootstrap_ci_bh_fdr`). Every candidate that reaches the auction already carries
a passed, FDR-corrected certificate. The auction holds **no per-cycle hypothesis
budget**. Acting on more of the already-corrected passing set does not re-inflate
the false-discovery rate: FDR controls the expected *proportion* of false
discoveries among *all* rejections (passing candidates), so any subset of the
passing set may be acted upon without weakening the guarantee. **Multi-winner
does not touch FDR discipline.**

### Not an invariant here: collateral atomicity

Reservations are per-`command_id` rows (`collateral_reservations`, command_id
PRIMARY KEY, `src/state/collateral_ledger.py:73-86`), enforced by a
compare-and-swap `INSERT…SELECT…WHERE` that **re-reads live committed reservation
state at submit time** — not the frozen auction witness —
(`_cas_insert_pusd_reservation`, `collateral_ledger.py:520-550`, raising
`CollateralInsufficient` on rowcount 0), with a DB trigger backstop
`trg_reservations_no_overreserve` (`:103-115`). Reservation + venue command
insert commit in **one transaction on one `zeus_trades.db` connection before the
network POST** (`src/execution/executor.py:7338-7357`), so INV-37 is clean and
INV-42 (reservation terminalization centralized at
`venue_command_repo.append_event`, per-command) is untouched. SQLite single-writer
serialization makes double-commit overspend *unrepresentable* regardless of how
many winners selection emits: a second winner's CAS always observes the first
winner's committed reservation.

---

## 2. Downstream path a winner takes, and which steps assume singleness

`select_global_single_order` (solver) → `PreparedGlobalAuctionResult` +
`GlobalSingleOrderActuation` (`global_single_order_auction.py:600-657`) →
`process_current_global_batch` winner-preflight loop
(`global_batch_runtime.py:2400-2603`) → `GlobalOneShotActuator.consume`
(`:2613`) → `_actuate_preflighted` token re-validation
(`event_reactor_adapter.py:7426-7487`) → collateral reservation + venue command
(one txn, `executor.py:7338-7357`) → venue POST → `_build_live_execution_command_
certificates` (`event_reactor_adapter.py:16033-16059`) →
`GlobalBatchSubmitResult` (`reactor.py:931`) → reactor finalization
(`reactor.py:1365-1406`) → command recovery / reconciliation on the next
lifecycle pass.

Steps that assume singleness, and their true scope:

| Step | Assumes one… | True scope | Multi-winner impact |
|---|---|---|---|
| Actuator / venue_delta (B) | submit per *epoch* | per-epoch | none — each loop iteration is a fresh epoch with a fresh actuator |
| Result dataclass (A) | winner per *epoch* | per-epoch | none — each iteration returns its own single-winner result |
| Certificate binding (C) | winner_event_id scalar | per-epoch | none — one scalar winner per iteration |
| Frozen capital scalar (D) | one order per *cut* | per-cut | resolved by re-capturing the cut each iteration (sizing decrements automatically) |
| Reactor page-return (E) | one epoch per *wake* | per-wake | **the only surface that changes** |
| In-flight gate (F) | bounded in-flight buys | per-command | none — winner #1 is bounded before winner #2 probes |
| Collateral CAS | live-state re-read | per-command | none — already multi-safe |
| Command recovery | per-command_id terminalization (INV-42) | per-command | none — submits stay serialized; ≤1 command mid-submit at any instant |

**Command-recovery replay specifically:** because option (b) keeps submits
strictly serialized (each iteration fully commits its reservation + command
before the next begins), the system never has two commands mid-submit
simultaneously — identical to today. Recovery reconciles per `command_id`
(INV-42; terminalization only through `venue_command_repo.append_event`), so K
sequentially-created commands each recover independently. There is no singleton
"pending command" assumption to break; the only multiplicity-sensitive guard is
(F), already shown to accept multiple *bounded* in-flight buys.

---

## 3. Options evaluated, and recommendation

Scoring axes: command-recovery replay correctness; collateral reservation races;
FDR discipline; blast radius; operator no-artificial-caps / no-shadow law.

### (a) Top-K in one frozen epoch (per-family exclusion, existing risk caps)

Selection returns K winners from one immutable cut. But the cut carries a single
frozen `capital_limit_usd` scalar (D). Sizing K winners against it either
(i) re-witnesses wealth per pick — which *is* sequential re-auction, i.e. option
(b) done inside the epoch, but now breaking the "one immutable information vector"
contract mid-epoch — or (ii) sizes all K against full cash, so winners #2..K are
over-sized; the ledger CAS then fail-closed-rejects them (`CollateralInsufficient`,
no money risk) but wastefully, and you must thread an explicit in-cycle
running-reserved decrement through the frozen-epoch selection core (a new stateful
parameter into `select_global_single_order`). It also forces relaxing surfaces
(A), (B), (C) together (multiple winners, multiple actuations, multiple
certificates per epoch). Highest blast radius; reintroduces the exact
cash-double-count race the frozen single-witness design removed.
**Reject.**

### (b) Sequential re-auction within one wake (RECOMMENDED)

Loop the existing, unmodified single-winner epoch: run one epoch → if it submitted
and budget/preemption allow, run another → stop when an epoch yields no submit,
the wake's wall-clock budget elapses, or preemption fires. Each iteration is a
complete independent epoch: fresh scope scan, fresh q/book/wealth cut, fresh
one-shot actuator, fresh preflight, fresh reservation.

- **Command recovery:** submits stay serialized (one command fully committed
  before the next); ≤1 command ever mid-submit. Identical to today's
  cycle-to-cycle pattern. Safe.
- **Collateral races:** each iteration re-captures `spendable_cash = balance −
  Σ pending BUY reservations` (`global_auction_universe.py:2663-2666, 2790-2794`),
  so winner #2 is sized against reduced cash automatically; the submit-time CAS is
  the same fail-closed backstop as today. Safe.
- **FDR:** untouched (upstream, per-candidate).
- **Blast radius:** ONE reactor decision point (`reactor.py:1208-1210`). Surfaces
  (A)(B)(C)(D)(F) and the entire money-path core are unchanged.
- **No-caps / no-shadow law:** natural terminator is edge/cash/cap exhaustion —
  the loop stops when the full-universe auction, sized against drawn-down cash and
  per-family risk caps, produces no positive-robust-EV winner. No hard K
  opportunity cap. No shadow tier; lands direct.

**Recommend.**

### (c) Event-driven continuous auction (no cycle batching)

Removes the reactor's epoch batching entirely; every fact wake could actuate.
Largest architectural change (re-architects the reactor scheduling, the claim/
finalize lease model, and the fair-lane interleave). Violates "entities earn
existence only when the problem demands it" — the problem (idle sleep between
submits) is solved by (b) without rebuilding the scheduler.
**Reject (over-engineered for the stated problem).**

### Recommendation, in three lines

1. **Option (b): sequential re-auction as a bounded loop in the reactor.** It
   removes only the idle inter-submit sleep; submits stay serialized, so every
   per-submit invariant holds trivially and the money-path core is unchanged.
2. The wealth witness already decrements `spendable_cash` by committed
   reservations each fresh epoch, so K-winner sizing is correct for free — no
   change to selection, sizing, collateral, actuation, or recovery.
3. It respects the no-artificial-caps law: the loop terminates on edge/cash/cap
   exhaustion (an epoch with no positive winner), bounded only by the existing
   per-wake wall-clock budget and preemption hooks.

---

## 4. Minimal change design (Option b)

### 4.1 The one change

`src/events/reactor.py`, global-batch branch (`:1192-1210`). Replace the
unconditional `return result` after the first `_process_global_event_batch` with
a bounded loop:

```
while True:
    epoch = _process_global_event_batch(events, decision_time=..., result=result,
                                         budget=budget, cycle_start=cycle_start,
                                         remaining=remaining, cancelled=cycle_cancelled)
    remaining -= epoch.attempted   (when remaining is not None)
    if not epoch.submitted:                       # no winner this epoch → edge/cash exhausted
        break
    if cycle_cancelled():                          # preemption (existing hook)
        break
    if budget is not None and (time.monotonic() - cycle_start) >= budget:  # liveness (existing guard)
        break
    if remaining is not None and remaining <= 0:   # event-count budget (existing)
        break
    events = self._store.fetch_pending(**fetch_kwargs)   # re-fetch: winner now in held_families
    if not events:
        break
return result
```

Rationale for the loop conditions:
- **Stop on `not submitted`.** A full-universe auction that finds no
  positive-robust-EV order against current cash/caps will find none on an
  identical retry; only a *submit* (which moved cash/holdings) can make the
  next-best candidate a winner. This is the natural, no-caps terminator.
- **Re-fetch pending events each iteration.** After winner #1 finalizes, its
  family is in `position_current` → `_current_held_weather_families`
  (`global_batch_runtime.py:1440-1471`) → scope scan, and its reservation is in
  the wealth witness. The next epoch sees the updated world. This is exactly the
  re-decision lane the engine already runs cross-cycle (AGENTS §0: "New-entry
  candidates are re-emitted every reactor cycle").
- **Budget / cancel / remaining** reuse the exact guards that already bound a
  single wake (`reactor.py:1224-1232`); worst-case overrun is unchanged in kind
  (one in-flight epoch past the budget), only repeated.

### 4.2 Supporting signature change

`_process_global_event_batch` currently returns `attempted: int`
(`reactor.py:1244-1406`). It must also surface whether the epoch submitted, so
the loop can stop on the first no-submit epoch. Return a small frozen struct
`GlobalEpochOutcome(attempted: int, submitted: bool)` (submitted =
`batch_result.venue_submit_count == 1`, read at `:1370`). No other caller depends
on the `int` return beyond `remaining -= attempted`.

### 4.3 What explicitly does NOT change

`select_global_single_order`, `_score_global_single_order`, the frozen-epoch cut
and its `capital_limit_usd` scalar, `GlobalOneShotActuator`, `venue_delta ∈ {0,1}`,
`GlobalBatchSubmitResult` invariants, the binding token, the certificate guard,
`probe_inflight_buy_ambiguity`, the collateral CAS/trigger, command recovery,
INV-37 / INV-42 seams. Each remains correct *per epoch*; the loop composes epochs.

### 4.4 No-caps compliance and the one defensive bound

No hard K opportunity cap. The only bounds are liveness bounds that already exist
(wall-clock budget, preemption). *Optionally* — defense-in-depth against a
runaway-loop *bug*, not a throttle — a `max_epochs_per_wake` set far above the
realistic passing-candidate count with a WARNING log if hit (never a silent
clamp). Defer to operator preference; the natural terminator (§4.1) should end
the loop long before any such bound.

---

## 5. Test surfaces to change / add

### Existing tests that stay valid (per-epoch invariants are unchanged)

- `tests/events/test_reactor.py` — the `venue_submit_count ∈ {0,1}` assertions
  (`:1804`, `:1842` *"at most one venue submit"*) remain correct **per epoch**;
  do not weaken them. Add loop coverage separately.
- `tests/integration/test_w3_solve_seam_g3.py` — auction seam + preflight
  re-auction fall-through (`:10324-10352`) unchanged.
- `tests/money_path/test_edli_live_readiness.py` — single-epoch readiness
  unchanged.

### Tests to CHANGE

- `tests/events/test_reactor.py`: the `_process_global_event_batch` /
  `process_pending` global-branch tests must assert the epoch struct
  (`attempted`, `submitted`) and the loop's stop conditions, not a single call.

### Invariant tests to ADD (antibody set)

1. **Double-submit impossibility within a wake.** Two consecutive epochs in one
   wake never have two commands mid-submit; each epoch mints a fresh
   `GlobalOneShotActuator` and a second `consume()` on a *prior* epoch's actuator
   still raises `GLOBAL_ACTUATION_CAPABILITY_CONSUMED`. Assert total venue
   submits over the wake == number of submitting epochs, each = 1.
2. **Reservation atomicity under K winners.** Drive K sequential BUY winners in
   one wake against a bounded wallet; assert Σ committed reservations ≤ initial
   `spendable_cash`, and the first winner whose size would exceed remaining cash
   is rejected by `_cas_insert_pusd_reservation` (`CollateralInsufficient`,
   fail-closed) — never an overspend.
3. **Wealth re-witness monotonicity + termination.** Across BUY iterations,
   each fresh `current_portfolio_wealth_witness.spendable_cash_usd` strictly
   decreases by the prior committed reservation; the loop terminates when
   spendable cash / per-family caps leave no positive-robust-EV winner.
4. **In-flight gate accepts bounded prior buys.** After a cleanly-recorded winner
   #1 (reservation + `venue_commands` row), `probe_inflight_buy_ambiguity`
   returns False and the next epoch proceeds; an *unbounded* pending buy
   (reservation without a command row) still raises
   `CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS` (fail-closed preserved).
5. **Loop preemption / budget.** An epoch that returns `submitted=False` stops
   the loop; a tripped `cycle_cancelled()` or an elapsed wall-clock budget stops
   the loop mid-wake leaving remaining candidates PENDING for the next wake.
6. **Command-recovery over K commands.** Simulate a crash after K sequential
   submits in one wake; assert each `command_id` recovers/terminalizes
   independently through `venue_command_repo.append_event` (INV-42) with no
   collapse or double-release.

Register additions in `architecture/test_topology.yaml`; add a new invariant to
`architecture/invariants.yaml` capturing "in-wake epochs are strictly serialized;
each is a complete single-winner epoch; the loop terminates on
edge/cash/cap/budget" and cite it from the new antibody tests.

---

## 6. Staged rollout, measurement, rollback

### Land

Direct land (no shadow tier — operator law). The change is behaviorally "run the
existing epoch back-to-back within a wake instead of sleeping ~1 min between
submits." Start with a conservative per-wake wall-clock budget so worst-case
overrun is bounded to one in-flight epoch past the budget (the existing guard),
and let the natural terminator do the rest.

### Measure (first 24-72h, then weekly)

Primary (the thesis):
- **Entries/day** — expect a rise from ~2-10 toward the cash/cap-bounded fraction
  of daily Kelly-passed candidates. Compare to
  `capital_utilization.md` §2 baselines.
- **Idle bankroll fraction** — expect a fall from the 82.1% mean; watch the
  concurrent open cost basis and per-day idle %.
- **Concurrent deployed capital** — should rise toward, but not exceed, the
  `max_correlated_exposure` $1000 cap (`config/risk_caps.yaml`); if it pins there,
  the correlated-exposure cap is now the binding constraint (a real, intended
  limit — not a bug).

Safety / regression (must stay flat):
- `GLOBAL_ACTUATION_VENUE_COUNT_INVALID`, `GLOBAL_ACTUATION_CAPABILITY_CONSUMED`,
  `GLOBAL_ACTUATION_CERTIFICATE_BINDING_INVALID` — must remain **zero**.
- `CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS` rate — a spike means a submit is not
  durably recording its command bound before the next epoch (investigate
  `executor.py:7338-7357` commit ordering); the loop degrades safely (stops early)
  but the rate is the health signal.
- `CollateralInsufficient` / CAS rejection rate — should be ~0 if wealth
  re-witnessing is correct; a nonzero rate means an epoch is sizing against stale
  cash (a witness/decrement bug), caught fail-closed with no money risk.
- Per-wake latency p50/p99/max — the loop multiplies per-epoch cost (scope scan +
  prepare + book fetch); confirm it stays within the wake budget and does not
  starve the fair-lane interleave (`reactor.py:1179-1211`).

Economic:
- Realized PnL per deployed dollar — should hold near the ~18%/19d edge density
  (`capital_utilization.md` §6) if more candidates share the same edge; a
  material drop signals the loop is reaching into thinner-edge candidates and the
  robust-EV threshold is the right place to look, not the loop.

### Rollback point

The change is one reactor decision point plus one small return-struct. Rollback =
restore the unconditional `return result` after the first epoch at
`src/events/reactor.py:1210` (revert the loop and the `GlobalEpochOutcome`
struct). **No schema, no migration, no money-path core change, no data written
that a revert must undo.** Clean one-commit revert; the very next wake resumes
single-submit-per-minute behavior.

---

## Appendix — key file:line index

| Concern | Location |
|---|---|
| Reactor page-return (change site) | `src/events/reactor.py:1197-1210` |
| Reactor global branch / budget guards | `src/events/reactor.py:1161-1232` |
| `_process_global_event_batch` | `src/events/reactor.py:1244-1406` |
| `GlobalBatchSubmitResult` invariant | `src/events/reactor.py:931-963` |
| next_claim deferral to future cycle | `src/events/reactor.py:1327-1348` |
| In-wake preflight re-auction loop (proof iteration is safe) | `src/engine/global_batch_runtime.py:2400-2603` |
| One-shot actuator | `src/engine/global_batch_runtime.py:178-189` |
| venue_delta ∈ {0,1} | `src/engine/global_batch_runtime.py:2610-2625` |
| Frozen cut / capital scalar | `src/engine/global_batch_runtime.py:2118, 2138-2142, 2261` |
| held-family scope feed | `src/engine/global_batch_runtime.py:1440-1471` |
| `probe_inflight_buy_ambiguity` | `src/engine/global_batch_runtime.py:1861-1862` |
| Ambiguity = unbounded-only + wealth subtraction | `src/engine/global_auction_universe.py:2326-2424, 2663-2666, 2790-2796` |
| Selection objective / SELL-lexically-prior / "re-auction on next cycle" | `src/solve/solver.py:3331, 3443-3463, 3854-3912` |
| Binding token + preflighted actuate | `src/engine/event_reactor_adapter.py:7378-7395, 7426-7487` |
| Certificate binding guard | `src/engine/event_reactor_adapter.py:16033-16059` |
| Collateral CAS + trigger + per-command rows | `src/state/collateral_ledger.py:73-86, 103-115, 520-550` |
| Reserve + command one-txn before POST | `src/execution/executor.py:7338-7357` |
| FDR upstream (not in auction) | `src/engine/qkernel_spine_bridge.py:166-306`; `src/strategy/selection_family.py` |
| Daemon cadence (~1 min) | `src/main.py:6657-6663`; `config/settings.example.json:67` (`reactor_scan_interval_seconds`=60) |
| Risk caps (correlated-exposure ceiling) | `src/risk_allocator/governor.py:66-69`; `config/risk_caps.yaml` |
| INV-37 (cross-DB) / INV-42 (reservation terminalization) | `architecture/invariants.yaml:879-943` |
| Evidence | `docs/evidence/capital_efficiency_2026_07_19/capital_utilization.md` |
