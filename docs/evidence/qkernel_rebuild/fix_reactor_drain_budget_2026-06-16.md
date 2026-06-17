# Fix: reactor end-of-cycle substrate-refresh drain — per-cycle wall-clock budget (#83 continuous-fills limiter)

```
# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: GOAL #83 continuous-fills limiter; root cause in
#   docs/evidence/qkernel_rebuild/redecide_block_2026-06-16.md §3 (cycle wall-time >> 60s schedule
#   -> APScheduler coalesces into 3-13 min gaps -> ~1 family decided/cycle -> 2-3h to rotate 49 cities).
# Running sha at fix time: HEAD 4d09ae8bc7; reactor.py last-modified bef3671835 (2026-06-16,
#   forecast-first interleave) — that change + the held-first drain ordering law are kept intact.
# Scope: ONE throughput fix — bound the BACKGROUND substrate-refresh drain by a wall-clock budget.
#   No money-path change. In-process, non-on-chain, reversible.
```

## ARCH plan-evidence (governed path `src/events/reactor.py`)

- **Capability:** add a per-cycle wall-clock budget to the reactor's end-of-cycle substrate-refresh
  drain (`_drain_substrate_refreshes` / `_drain_one_bucket`, `src/events/reactor.py`), so a large
  blocked-family set can no longer overrun the 60s reactor schedule.
- **What:**
  1. New module-level reader `_drain_budget_seconds()` (`src/events/reactor.py`), mirroring the
     existing `_cycle_budget_seconds()`. Reads `ZEUS_REACTOR_DRAIN_BUDGET_SECONDS` (default
     `DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS = 10.0`); `0`/negative disables the budget (legacy
     unbounded drain); malformed → default.
  2. `_drain_substrate_refreshes` computes ONE shared monotonic deadline for the cycle and threads
     it into both `_drain_one_bucket` calls (snapshot bucket first, cycle-advance bucket second,
     same deadline).
  3. `_drain_one_bucket` gains a `deadline` parameter. Before invoking the refresher for each family
     it checks the deadline (AFTER the previous family finished — never mid-network). The budget can
     truncate ONLY the non-held rotation tail: held-position families (idx < n_held) are never
     truncated, and the first non-held family is always attempted (one unit of new-money progress per
     cycle). Truncated families are RETAINED in `_pending_*` (in-place `families[:] = unreached`) for
     a later cycle; `ReactorResult.drained_truncated` records the count and one INFO line is logged.
  4. New visibility counter `ReactorResult.drained_truncated`.
- **Why:** root cause (`redecide_block_2026-06-16.md` §3): the drain runs at END of every cycle and,
  per its own docstring, "the per-cycle drain has NO drop-cap — it covers EVERY family it was handed
  this cycle". With ~49 blocked families that is ~49 `/book` network snapshot fetches per cycle, which
  is the overrun. The reactor is scheduled every 60s (`src/main.py:9486`, `interval, minutes=1`) but
  the cycle wall-time blows past 60s, so APScheduler coalesces it into 3-13 min real gaps → ~1 family
  decided per cycle → 2-3 h to rotate 49 cities → the harvest crosses far too slowly for continuous
  fills. Bounding the background drain keeps the whole cycle inside the 60s schedule so the coalescing
  stops and the harvest rotates promptly.
- **Reversibility:** in-process, non-on-chain. Set `ZEUS_REACTOR_DRAIN_BUDGET_SECONDS=0` to restore
  the legacy unbounded drain instantly (no restart of any on-chain component, no state migration).
- **Test:** `tests/events/test_reactor_drain_budget.py` (new, 5 cases) + the existing
  `tests/events/test_always_decidable_invariant.py` (11 cases) all green; full `tests/events/`
  (521 passed, 8 skipped, 2 xfailed); `tests/money_path/` zero NEW failures.

## NOT a money-path cap (operator law — stated explicitly)

This is a **BACKGROUND-I/O time budget**, identical in kind to the existing warm-cycle
`ZEUS_REACTOR_REFRESH_BUDGET_SECONDS` (default 17.0s inside a 20s interval, `src/main.py:3504`). It is
**NOT** a money-path cap / throttle / allowlist / notional limit. Untouched by this change:

- per-event decision logic and gates,
- the 30s decision budget (`ZEUS_REACTOR_CYCLE_BUDGET_SECONDS` / `_cycle_budget_seconds`),
- the fair-lane interleave and the per-(tier,city) fetch ordering,
- every money-path / risk gate and submit path.

Only the background refresh fan-out (which families get their executable-snapshot substrate
re-fetched this cycle) is time-bounded. A family not refreshed this cycle is refreshed on a later
cycle; the drain's fair-cursor rotation already guarantees bounded-cycle coverage with no starvation
— exactly the "future per-cycle fan-out cap" the drain's own ordering comment (≈ reactor.py:1255)
already anticipated. The drain's docstring/comment already said fair rotation was chosen *because* it
gives the best worst-case time-to-full-coverage "under any such future per-cycle fan-out cap"; this
fix introduces that cap.

## Held-first preserved (a budget never starves money at risk)

Held-position families (money at risk) are computed once per cycle (`_held_families_failsoft`) and
sort FIRST in `ordered`. The budget check is gated on `idx > n_held`, so:

- a held family (idx `< n_held`) is **never** truncated — it is refreshed even if the budget is
  already spent;
- the first non-held family (idx `== n_held`) is always attempted (no total stall);
- only the non-held rotation tail (idx `> n_held`) can be deferred once the budget is spent.

Test `test_held_position_family_never_budget_starved` drives a refresher whose first call already
blows the budget and asserts both held families still drain (and in held-first order) while the
non-held tail defers.

## Fail-soft preserved

A refresher that raises still logs exactly one warning per failed family and never raises into the
cycle (the `try/except` is unchanged); the budget logic wraps around it. Test
`test_failsoft_preserved_under_budget_one_warning_no_raise` pins this.

## Budget value + schedule math (justifies 10.0s default)

Reactor schedule = 60s (`src/main.py:9486`, `interval, minutes=1`, `coalesce=True`).
Per-cycle DECISION budget = 30s (`DEFAULT_REACTOR_CYCLE_BUDGET_SECONDS`, `src/events/reactor.py:61`).

```
60s schedule = 30s decision budget + DRAIN budget + scheduler/dispatch/teardown headroom
```

Choosing the drain budget = **10.0s** gives `30 + 10 = 40s`, leaving ~20s headroom for
`fetch_pending` reads, the status-pulse write, APScheduler dispatch latency, and connection teardown
— so the whole cycle fits inside the 60s schedule with margin and the coalescing that produced the
3-13 min gaps stops. This mirrors the warm-cycle's discipline (17.0s budget inside a 20s interval =
~3s headroom, scaled here to the larger 60s/30s envelope). Env-overridable via
`ZEUS_REACTOR_DRAIN_BUDGET_SECONDS` if live p99 drain-per-family data later argues for a different
value; `0` disables it entirely (legacy behavior).

## Exact diff (file:line)

`src/events/reactor.py`:
- **+`DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS = 10.0`** and **`_drain_budget_seconds()`** (inserted just
  before `_operator_disarm_active`, ≈ line 93) — env reader, same shape as `_cycle_budget_seconds`.
- **+`ReactorResult.drained_truncated: int = 0`** (≈ line 526) — visibility counter.
- **`_drain_substrate_refreshes`** (≈ line 1119): compute one shared `drain_deadline` from
  `_drain_budget_seconds()`; pass `deadline=drain_deadline` to both `_drain_one_bucket` calls; remove
  the blanket `_pending_*.clear()` (the bucket clear/retain is now owned by `_drain_one_bucket`).
- **`_drain_one_bucket`** (≈ line 1239): add `deadline` param; per-family budget check gated on
  `idx > n_held` (never truncate held; always attempt first non-held); on truncation retain the
  unreached tail in `families[:]`, bump `result.drained_truncated`, log one INFO line; on full drain
  `families.clear()`.

Net: drain fan-out is the only changed behavior; decision/money paths byte-for-byte unchanged.

## Test output

```
$ python3 -m pytest tests/events/test_reactor_drain_budget.py tests/events/test_always_decidable_invariant.py -q
................                                                          [100%]
16 passed in 1.85s

$ python3 -m pytest tests/events/ -q
... 521 passed, 8 skipped, 2 xfailed in 55.41s

$ python3 -m pytest tests/money_path/ -q
... 23 failed, 172 passed
```

`tests/money_path/` 23 failures are **PRE-EXISTING** — identical count with my changes stashed
(baseline on clean HEAD 4d09ae8bc7: `23 failed, 21 passed` for the two affected files), so ZERO new
failures. They live in `test_edli_online_invariants.py` (19 — daemon boot / EDLI-mode / registry
`assert_db_matches_registry` INV-05 wiring) and `test_finding_b_free_cash_bound.py` (4 — free-cash
stake binding). Neither file touches the end-of-cycle drain; both are orthogonal to this change.

## Provenance verdicts (files touched)

- `src/events/reactor.py` — **CURRENT_REUSABLE.** Last touched `bef3671835` (2026-06-16 00:23,
  forecast-first interleave); matches the running lineage. The held-first drain ordering (operator
  correction 2026-06-12) and the forecast-first interleave are both preserved; the new budget is the
  "future per-cycle fan-out cap" the existing drain-ordering comment was explicitly written to
  anticipate.
- `tests/events/test_reactor_drain_budget.py` — **new** (2026-06-16), authority basis = GOAL #83.
