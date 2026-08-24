# Auction-collapse repair design — TYPE A/C runtime silence (2026-08-24)

Status: DESIGN ONLY, not yet implemented. Investigation + design deliverable
for reversal_plan_tier0_2026-08-24.md item 5b remainder (TYPE A/C auction-
collapse mechanisms, 107h of August silence). Every claim below is traced to
file:line evidence read on this branch (`reversal-plan-tier0`); consult Item
5's prescriptions are evaluated against that evidence, not rubber-stamped.

## 0. Summary of findings

- TYPE C ("preemption churn") is driven by one closure, `epoch_superseded()`
  (`src/engine/event_reactor_adapter.py:8404-8467`), that every `superseded(stage)`
  call in the batch runtime invokes identically regardless of the `stage`
  string passed to it — **including the per-family loop call at
  `global_batch_runtime.py:7446`**. There is no per-family branch in the
  actual supersession decision; "prepare_family:{family_key}" is a log label,
  not a scope. A fact about one family cancels every other family's
  concurrently-computing work in the same batch, with one partial exception
  (probability wakes, see §1.2).
- TYPE A ("preflight staleness storm") is five genuinely distinct gates with
  different scopes and different snapshot sources — one is not a freshness
  gate at all (ownership CAS), two are true FC-03 re-fetches, one is a cheap
  in-memory identity-consistency check, and one is a deliberately redundant
  final-mile probability re-check layered *on top of* TYPE C's early exit
  (§2). The five gates are not interchangeable and a repair must not treat
  them as one mechanism.
- Tier-0's taker-only, one-per-cluster admission policy (commit `0653ed8bb`,
  `src/strategy/tier0_policy.py`) structurally disables two of the five TYPE A
  failure categories outright (§3) — this is not a coincidental side effect,
  it is a direct consequence of `TIER0_ALLOWED_EXECUTION_MODE = "TAKER_LIMIT"`.
- The codebase already has a working precedent for the exact "coalesce,
  don't re-derive" pattern this design proposes for TYPE C:
  `_reusable_global_preflight_jit_candidate` (`event_reactor_adapter.py:15197-15271`)
  reuses an already-JIT-validated curve across back-to-back re-auction
  attempts within the book's `quote_ttl` window instead of re-fetching. The
  TYPE C repair below is the same idea one layer up (batch, not candidate).

## 1. TYPE C — preemption churn

### 1.1 Where "newer fact" arrives and what actually triggers supersession

`superseded(stage)` (`global_batch_runtime.py:6704-6724`) does nothing but
call the injected `epoch_superseded()` callback and log `stage` as a label:

```python
def superseded(stage: str) -> bool:
    if epoch_superseded is None:
        return False
    changed = bool(epoch_superseded())   # <- stage never passed in
    ...
    return changed
```

It is called at three sites, one of which is *inside a per-family loop*:

- `global_batch_runtime.py:7226-7227` — `superseded("scope_scan")`, whole-batch.
- `global_batch_runtime.py:7446-7447` — `superseded(f"prepare_family:{family_key}")`,
  called once per family inside the family-preparation loop.
- `global_batch_runtime.py:7543-7544` — `superseded("prepare_families")`, whole-batch.

Because `superseded()` ignores its `stage` argument entirely, the per-family
call at 7446 is **functionally identical** to the whole-batch calls — it asks
"has anything, anywhere, superseded the WHOLE epoch", not "has anything
superseded THIS family". A batch computing families {Denver, Miami, Austin}
where only Denver's forecast changes will still discard Miami's and Austin's
completed work at the next `superseded()` checkpoint, because the check has
no family-scoped variant to call.

`epoch_superseded` is supplied as `_epoch_superseded`
(`event_reactor_adapter.py:8404-8467`, wired at `:10975`). It compares a
cheap file-stat revision tuple (`reactor_urgent_wake_revision()`,
`src/runtime/reactor_wake.py:2724-2733`, `(inode, mtime_ns, size)` of the
urgent-wake marker file) against the revision snapshotted when the batch
started. Only four wake reasons are ever written to that urgent-wake file
(`URGENT_WAKE_REASONS`, `reactor_wake.py:80-87`):

```python
URGENT_WAKE_REASONS = frozenset({
    "day0_extreme_event_committed",
    "forecast_posterior_advanced",
    "market_price_advanced",
    "position_fill_projected",
})
```

When the revision changed, `_epoch_superseded` collects `pending_wakes` and
hands them to `_global_batch_wakes_supersede`
(`event_reactor_adapter.py:2214-2263`) — this is the ONLY place any
family-scoping exists today:

```python
def _global_batch_wakes_supersede(wakes, *, day0_urgent_batch, delta_scope_family_keys):
    for wake in wakes:
        reason = str(getattr(wake, "reason", "") or "")
        if reason in {"market_price_advanced", "money_path_substrate_refreshed"}:
            continue                                    # never supersedes here (FC-03 owns it)
        if day0_urgent_batch and reason == "forecast_posterior_advanced":
            continue                                    # day0 dominates a forecast refresh
        if reason != "forecast_posterior_advanced":
            return True                                 # <- ALWAYS global, no narrowing
        if delta_scope_family_keys is None:
            return True                                 # batch wasn't itself probability-scoped
        wake_family_keys = {...derived from wake.forecast_families...}
        if wake_family_keys & delta_scope_family_keys:
            return True                                 # family-scoped narrowing (only case)
    return False
```

So, precisely:

| wake reason | narrowing today | evidence |
|---|---|---|
| `market_price_advanced` | never supersedes (JIT preflight owns it, §2) | `:2224-2232` |
| `forecast_posterior_advanced` | per-family, **but only if the current batch's own triggering events were exclusively a probability-delta batch** (`delta_scope_family_keys` comes from `_global_probability_refresh_family_keys`, `event_reactor_adapter.py:8392-8397`) | `:2236-2262` |
| `day0_extreme_event_committed` | none — always global | falls through `:2236` (not `forecast_posterior_advanced`) → `return True` |
| `position_fill_projected` | none — always global | same fallthrough |

This is the mechanism behind the ×288/2.5h and ×312 counts: any mixed-event
batch (which is the common case — a batch triggered by e.g. a
`NEW_MARKET_DISCOVERED` + `FORECAST_SNAPSHOT_READY` mix, or any batch not
itself pure-probability-scoped) gets `delta_scope_family_keys = None`, so a
SINGLE `forecast_posterior_advanced` wake for ANY family kills the entire
batch. `day0_extreme_event_committed` and `position_fill_projected` never get
narrowing at all, by design (both are treated as globally authoritative:
Day0 extremes and fills change the correct answer for every family's
capital/probability context). During an active forecast-ingest cycle or a
burst of fills (Tier-0 taker-only entries fire fills continuously), the
urgent-wake file is rewritten fast enough that a multi-second global batch
essentially never survives to `prepare_families` — which is exactly the
observed shape (full selection throughput, one completed auction in 2.5h).

### 1.2 Does fact coalescing violate FC-03 or a time-ordering law?

No, if scoped correctly. FC-03 (`architecture/failure_chains.yaml:165-194`,
`docs root AGENTS.md §0`: "re-fetch executable truth at submit (FC-03)") is a
**submit-time** re-fetch law — it says nothing about how long selection is
allowed to take before submit, only that the price/book used AT SUBMIT must
be freshly re-verified. TYPE A's gates (§2) already do that re-verification
independently of TYPE C. Coalescing facts that arrive DURING the compute
window (applying them at the next cut instead of aborting mid-compute) does
not let a stale price reach the venue — it only lets the compute finish on
the generation it opened with, and TYPE A's real re-fetches (gates 2 and 5,
§2) still run before any order is submitted. The staleness this design
admits is bounded to "one batch compute time" (the interval between opening
a generation and its `prepare_families` checkpoint), which the evidence
(§1.1 quote) shows is itself the same order of magnitude as a single JIT
re-validation (~26s observed selected-family revalidation per the existing
`WorkContext` comment at `event_reactor_adapter.py:8509-8521`).

INV-47 (SCOPE/DRAIN/RESET, `architecture/invariants.yaml:1120-1156`) is the
invariant actually at risk from a naive fix, not FC-03. Any bounded-counter
gate must declare:
- **SCOPE**: the narrowest identity it blocks. Today's `epoch_superseded()`
  is global; a repair narrowing it to "the family_key(s) the wake actually
  names" would need every `URGENT_WAKE_REASONS` producer to reliably attach
  family identity (forecast wakes already do — `wake.forecast_families`,
  `:2241`; day0/fill wakes do not carry a family list today and would need
  one, or must stay globally-scoped by design since they are genuinely
  cross-family in effect).
- **DRAIN**: what clears the bounded-completion state, on what cadence. A
  counter that never resets is a ratchet (INV-47's own definition of a
  non-gate) — it must reset once a batch actually completes cleanly.
- **RESET**: the concrete path back to "normal preemptive behavior" once the
  bounded window closes, so a batch cannot exploit the counter to run
  perpetually stale.

### 1.3 Repair option — bounded-revalidation counter

Design (matches the SCOPE/DRAIN/RESET comment idiom already used at
`global_batch_runtime.py:4077-4080` and `event_reactor_adapter.py:8522-8524`):

```
SCOPE:  the current global batch's opened generation (the epoch snapshotted
        at batch start) — never wider than "this one batch", never a
        standing exemption across batches.
DRAIN:  a per-generation supersession counter, reset to 0 the moment a NEW
        generation opens (i.e., the moment epoch_superseded() first returns
        True and the batch actually restarts). While the counter is below N
        within a rolling M-minute window, superseded() suppresses the abort
        for non-day0/non-fill reasons ONLY and lets the batch finish on its
        currently-open generation; TYPE A's real re-fetch gates (§2, gates 2
        and 5) still run at their normal cadence and are the actual freshness
        backstop, so nothing stale reaches the venue.
RESET:  once the batch completes (prepare_families succeeds) OR the counter
        reaches N within M minutes, the NEXT epoch_superseded() call reverts
        to today's unconditional global-abort behavior — the bounded window
        is a one-shot grace period per generation, not a standing loosening.
```

Concretely: `_epoch_superseded()` gains a small counter keyed by the current
batch's generation id (already threaded as `claimed_at`/CAS `generation` in
the CAS-fence code, `event_reactor_adapter.py:4071`), incremented each time
this function would otherwise have returned `True` for a non-`day0_extreme_
event_committed`/non-`position_fill_projected` reason. Day0 and fill wakes
are excluded from the grace window entirely (they stay immediate-abort,
matching their existing "always global, no narrowing" treatment — §1.1) since
both are genuinely cross-family-authoritative and cheap to re-derive (Day0 is
rare; fills are the actual economic event this system exists to protect).
Once the counter for the current generation reaches N (proposed N=3, i.e. the
batch survives its own opening fact plus 2 more before yielding) within M
minutes (proposed M=5, matching the existing 26s-order JIT revalidation
cadence with wide margin), the grace period ends for that generation and
normal immediate-abort resumes — this bounds the worst case to "N missed
early-exits, not unbounded silence."

### 1.4 Quantified tradeoff

- **Staleness admitted**: at most one batch compute time per grace-window
  use (empirically ~seconds to low tens-of-seconds, bounded further by
  TYPE A's independent re-fetch at actuation, §2 gates 2/4/5 — never reaches
  the venue stale regardless).
- **Cost today**: the cited gap #30 (288 supersessions / 2.5h, 1 completed
  auction) and gap #24 (312 supersessions) are hours of zero completed
  auctions — i.e., zero admitted trades, zero learning, in a system whose own
  standing verdict (reversal_plan_tier0_2026-08-24.md) is "absence = learning
  loss, not proven EV loss." The bounded counter converts an unbounded
  live-lock into a bounded, auditable grace period with an explicit RESET.

## 2. TYPE A — preflight staleness storm

Five distinct gates, not one mechanism. Each entry below states what it
checks, which snapshot it reads, and whether it duplicates another gate.

| # | gate | file:line | what it checks | snapshot source | redundant with TYPE C / other gates? |
|---|---|---|---|---|---|
| 1 | Claim CAS fence | `event_reactor_adapter.py:4081-4123` (`GLOBAL_WINNER_CLAIM_FENCE_LOST`) | DB compare-and-swap on `opportunity_event_processing` + absence of a durable `ExecutionCommandCreated`/`VenueSubmitAttempted` row — an ownership/race guard between concurrent carriers of the same winner pointer | durable DB rows, not a market snapshot at all | No — not a freshness gate. Consult's "single freshness re-check" framing does not apply; this must not be touched by any TYPE A/C repair. |
| 2 | Book-moved MAKER witness | `event_reactor_adapter.py:12241-12295` (`GLOBAL_BUY_JIT_MAKER_WITNESS_SUPERSEDED` / `current_limit_or_cashflow_changed`) | re-fetches a **fresh `raw_book`** (network JIT call) and compares the recomputed limit price / witness identity against the selection-time `CurrentMakerFillWitness` | fresh, genuinely newer than selection generation | No — this IS the FC-03 re-fetch for the BUY execution curve. **Only engages when `selected_execution_mode == "MAKER_REST"`** (`:12177`); the `TAKER_LIMIT` branch (`:12296`) skips this block entirely. |
| 3 | Curve-identity binding check | `event_reactor_adapter.py:16440-16451` (`GLOBAL_ACTUATION_BOOK_SUPERSEDED`) | cheap in-memory check that the candidate object being actuated still carries the SAME curve object (token/side/snapshot_id/curve-identity) it was selected with — comment: "T2 selected this exact full-depth curve from the current global book epoch... Bind the sealed epoch candidate here" (`:16435-16439`) | same generation as selection; not a re-fetch | No — catches internal candidate-mutation bugs between selection and actuation, not staleness. Cheap, should stay as-is. |
| 4 | Probability superseded | `event_reactor_adapter.py:16452-16459` (`GLOBAL_ACTUATION_PROBABILITY_SUPERSEDED`) | calls `current_global_probability_authority(...)` — a fresh probability read at actuation time, independent of and after the selection-time posterior | fresh, genuinely newer than selection generation | **Deliberately redundant with TYPE C's `forecast_posterior_advanced` narrowing** (§1.1). This is not dead weight — TYPE C's early exit is an optimization to avoid wasted preflight compute; THIS gate is the authoritative last-mile guarantee that actually prevents acting on a stale posterior. A TYPE C repair that loosens the early exit does not weaken safety because this gate is untouched and still runs. |
| 5 | Price-band / depth | `LIVE_UNIT_PRICE_OUT_OF_BOUNDS` (multiple sites, e.g. `:13044`, `:16016`, `:22704`); SIZE-TO-DEPTH sweep-VWAP mitigation comment `:21934-21941` | derived from the same JIT-refetched curve as gate 2/3; price-band fires when no crossable proposal exists inside the executable band. **Depth is NOT a rejection for TAKER FOK orders** — size is capped to available crossable depth from the elected snapshot's live book BEFORE the cert is built (comment: "size is capped at available depth (FOK semantics preserved on the sized amount → no DEPTH_INSUFFICIENT at executor validation)", `:21937-21938`) | fresh, same JIT fetch as gate 2 | Partially overlaps gate 2's fetch (same network round-trip in the BUY path); price-band is a genuine FC-03 check, "depth insufficient" as a *rejection* mostly does not occur for taker orders by construction (mitigated, not gated). |

Answering design question 2 directly: gates 2, 4, and 5 re-validate against
a **newer** snapshot generation than selection used — this is intentional
and is exactly what FC-03 requires. Gates 1 and 3 are not freshness checks at
all (ownership CAS, and in-memory identity consistency respectively) and
must not be conflated with the freshness-gate repair. The consult's "one
immutable generation through ranking→preflight→submission, FC-03 submit
re-fetch as the ONLY freshness re-check" framing is *approximately* right for
gates 2/5 (the actual re-fetch), but understates gate 4's role: gate 4 is a
second, deliberate freshness re-check specifically because TYPE C's early
exit is scoped to avoid wasted compute, not to guarantee submit-time truth —
that guarantee is gate 4's job (for probability) and gates 2/5's job (for
price/book). Nothing here breaks if TYPE C admits bounded staleness (§1.3),
because gates 2/4/5 do not consume TYPE C's early-exit decision at all — they
re-derive their own truth independently at actuation time.

## 3. Tier-0 interaction (qualitative)

Tier-0's admission policy (`src/strategy/tier0_policy.py`, commit
`0653ed8bb`) sets `TIER0_ALLOWED_EXECUTION_MODE = "TAKER_LIMIT"` and rejects
any `MAKER_REST` candidate outright (`TIER0_REJECT_MAKER_REST`, `:38`). Cross-
referenced against §2:

- **Gate 2 (book-moved MAKER witness) is structurally inert under Tier-0.**
  It only executes when `selected_execution_mode == "MAKER_REST"`
  (`event_reactor_adapter.py:12177`); Tier-0 candidates always take the
  `TAKER_LIMIT` branch (`:12296`), which builds the curve/proposal directly
  without the witness-reconciliation block that produces
  `GLOBAL_BUY_JIT_MAKER_WITNESS_SUPERSEDED`. One of the five gates' failure
  mode simply cannot fire for Tier-0 entries.
- **Gate 5's "depth insufficient" failure mode is also avoided by
  construction** for the same reason (taker FOK sizes to available depth
  rather than rejecting, `:21934-21941`), and Tier-0's flat, smallest-venue-
  legal-order stake (`reversal_plan_tier0_2026-08-24.md` item 6) is far below
  typical book depth at the sub-0.25 price band, making a depth shortfall
  even less likely in practice.
- Tier-0's "one entry per (city, target_date) cluster" rule
  (`tier0_policy.py` cluster-occupancy check) reduces the number of
  concurrently-ranked candidates per batch, which does not shrink an
  individual candidate's own selection→submit window, but does reduce the
  number of families whose in-flight preflight work is exposed to a TYPE C
  collapse in the same batch — fewer families computing concurrently means
  fewer families lose work per collapse event.
- **Gates 1, 3, and 4 are untouched by Tier-0** — the CAS fence, curve-
  identity check, and probability-superseded re-check apply identically to
  taker and maker candidates, so Tier-0 does not mitigate the CAS-fence
  losses (`GLOBAL_WINNER_CLAIM_FENCE_LOST ×205`) or probability-supersession
  losses cited in the evidence base.

Qualitative answer to design question 3: **Tier-0 alone mitigates roughly two
of the five TYPE A failure categories (gate 2 entirely, gate 5's depth
sub-case), but does not touch gates 1, 3, or 4** — so Tier-0 is a partial,
not a full, mitigation of TYPE A. The CAS-fence losses (the largest single
count in the evidence base, ×205) are completely orthogonal to execution mode
and require their own fix regardless of Tier-0's rollout.

## 4. Ranked repair options

| option | blast radius | staleness admitted | silence-hours recovered | notes |
|---|---|---|---|---|
| **A. TYPE C bounded-revalidation counter (§1.3)** | Single closure (`_epoch_superseded`), additive counter state, no change to gates 2/4/5 | ≤ one batch compute time per grace use, never reaches the venue (gates 2/4/5 unchanged) | Directly addresses the ×288/×312 supersession-storm gaps (#30, #24) — the dominant cited silence source | Smallest, most isolated change. Recommended first slice (§5). |
| **B. TYPE A gate-1 CAS-fence review** (`GLOBAL_WINNER_CLAIM_FENCE_LOST ×205`) | Touches carrier-ownership semantics — higher risk, needs its own investigation into why the fence is lost so often (recovery-owned carrier races? retry-count exhaustion?) | N/A — this is a correctness gate, not a freshness one; "fixing" it means understanding why legitimate carriers lose the fence, not loosening it | Second-largest cited count, but requires separate root-cause work not covered by this design pass | Out of scope for a minimal first slice; needs its own investigation before a repair can be designed responsibly. |
| **C. Extend Day0/fill wake family-scoping** | Requires every day0/fill wake producer to attach reliable family identity, a larger surface than option A | Same bound as A, but wider blast radius to implement correctly | Marginal — day0/fill wakes are a small fraction of the cited counts vs. forecast/mixed-batch wakes | Not recommended now; the safety case for narrowing these two reasons is weaker (both are genuinely cross-family-authoritative) and the win is smaller than option A. |
| **D. Full "one immutable generation, FC-03-only" rearchitecture** | Large — touches TYPE C's early-exit AND all five TYPE A gates' relationship to it | Same bound as A if done correctly, but far more surface to get wrong | Same recovered hours as A, at much higher implementation/review cost | Rejected for the first slice: gate 4's redundancy with TYPE C is a deliberate defense-in-depth (§2), and this option's framing (consult's) would require re-litigating that design decision without evidence it is wrong. |

## 5. Recommended first slice

**Option A only**: the TYPE C bounded-revalidation counter (§1.3), scoped
exactly as SCOPE/DRAIN/RESET describe it. Nothing in TYPE A (§2) changes.

Acceptance tests (design-level; to be written at implementation time):

1. A batch that receives a `forecast_posterior_advanced` wake for a family
   NOT in its own scope, on a mixed-event (non-probability-delta) batch,
   completes its current generation instead of aborting — while the counter
   for that generation is below N within M minutes.
2. The Nth supersession within the M-minute window for the SAME generation
   causes an immediate abort (today's behavior) — the grace period does not
   admit unbounded staleness.
3. A `day0_extreme_event_committed` or `position_fill_projected` wake always
   aborts immediately regardless of counter state (no grace period for these
   two reasons) — matches today's behavior exactly, zero regression risk on
   the two reasons already treated as globally authoritative.
4. Once a generation completes `prepare_families` successfully, its counter
   resets to 0 — the next generation starts with a full grace budget, not a
   carried-over exhausted one (DRAIN law).
5. A stale price/probability from a batch that used its full grace budget is
   still caught at actuation — gate 4 (`GLOBAL_ACTUATION_PROBABILITY_
   SUPERSEDED`) and gates 2/5 (fresh JIT re-fetch) still fire exactly as
   today; no order reaches the venue on stale truth even when the batch
   itself was allowed to finish computing on an older generation.
6. `tests/test_gate_scope_drain_reset.py`-style site-anchor test extended (or
   a sibling test added) so the new counter's SCOPE/DRAIN/RESET comment is
   enforced the same way the four existing INV-47 gates are (per NC-24).
