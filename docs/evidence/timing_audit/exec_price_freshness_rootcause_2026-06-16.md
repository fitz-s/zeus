# Exec-Price Quote Freshness Root-Cause — 2026-06-16

```
# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: read-only static analysis of src/ + existing timing-audit evidence docs
# Key sources: freshness_fallback_map_2026-06-16.md, live_latency_bottlenecks_2026-06-16.md,
#   fallback_outcome_quality_2026-06-16.md, capture_reactor_stall_rootcause_2026-06-16.md
```

---

## SUMMARY

The 60–97% stale-quote "rejection" figure conflates two distinct gates at two stages of the
pipeline. The SELECTION-stage gate (`_snapshot_price_stale_reason`, 600s window) was recently
widened from a structurally-starving 30s window, so it is largely no longer the bottleneck.
The SUBMISSION-stage rejection (60–97% of venue submit attempts rejected on most days) is
measured from `edli_live_order_events` in zeus-world.db and reflects pre-venue validation
failures — raw_response_hash=None on every rejection — attributable to price-moved
(TAKER_BUY_TOUCH_EXCEEDS_RESERVATION / depth-authority mismatch) and to market tradeability
flags, not solely to a stale-quote freshness timeout. The original C5 starvation bug (warm
cadence >> freshness window) existed and was real, but it was a SELECTION-stage stall
(decisions never reaching the submit path), not the submit rejection rate. The current commit
history shows the selection-window was widened to 600s and the JIT recapture path was added;
the remaining 60–97% venue-side rejection is a different (and insufficiently attributed) failure
mode — price-at-decision vs price-at-submit divergence plus market tradeability events.

---

## THE GATE (file:line + logic)

There are TWO freshness checkpoints on the execution path. They are separate and must be
distinguished.

### Gate 1 — SELECTION-stage price freshness (decision path, ~advisory → recapture)

**File:** `src/engine/event_reactor_adapter.py:13867–13900`
**Function:** `_snapshot_price_stale_reason(row, *, decision_time)`

Logic:
1. If `captured_at` is available: compute `selection_deadline = captured_at + 600s`. If
   `selection_deadline < decision_time` → return `EXECUTABLE_SNAPSHOT_STALE:selection_deadline=...`.
2. Fallback: if `captured_at` missing, use stored `freshness_deadline` (the snapshot's own 180s
   window baked in at capture time). If `freshness_deadline < decision_time` → return stale reason.
3. Return `None` (fresh).

**Current window:** `_DECISION_SELECTION_PRICE_WINDOW_SECONDS = 600.0` seconds (10 minutes).
Defined at `src/engine/event_reactor_adapter.py:13864`.

**Recovery path (decision-triggered targeted refresh):** If `selected_stale_reason is not None`
AND a `family_snapshot_refresher` callable is wired (it is, via main.py), the adapter drops its
trade-DB read snapshot, invokes the refresher to re-capture fresh CLOB books for the whole
family, re-elects the latest row, and re-runs the staleness check. Only if the refreshed row is
STILL stale does the path hard-fail with `EXECUTABLE_SNAPSHOT_STALE`. Defined at
`src/engine/event_reactor_adapter.py:2276–2382`.

### Gate 2 — EXECUTION-stage JIT book witness (pre-submit, strict 30s)

**File:** `src/engine/event_reactor_adapter.py:434`  
**Constant:** `_K1_DEFAULT_PRESUBMIT_FRESHNESS_SECONDS = 30.0`

**File:** `src/engine/event_reactor_adapter.py:437–504` — `build_presubmit_snapshot_row()`

At submit time a live JIT `/book` fetch is performed. The witness's `captured_at` is anchored
to the fetch instant (`decision_time.astimezone(UTC)`) and a new `freshness_deadline =
captured_at + 30s` is computed. The gate then checks:
- `TAKER_BUY_TOUCH_EXCEEDS_RESERVATION`: fresh best_ask > q_lcb reservation → abort
  (`src/engine/event_reactor_adapter.py:4355–4359`).
- Depth-authority mismatch (`_assert_taker_depth_authority_fresh`): witness top-of-book
  diverges from elected snapshot → abort with typed TRANSIENT reason.

**File:** `src/contracts/executable_market_snapshot.py:395–398`
```python
def is_fresh(snapshot: ExecutableMarketSnapshot, now: datetime) -> bool:
    return _as_utc(now, field_name="now") <= snapshot.freshness_deadline
```

**File:** `src/engine/cycle_runtime.py:830–885` — `_ensure_fresh_executable_snapshot()`
Pre-submit path: stale + CLOB client → recapture; stale + no client → raise
`executable_snapshot_stale`. Fail-closed with recapture-then-recheck discipline.

### Gate 3 — SNAPSHOT SELECTION identity freshness (family completeness, no price decay)

**File:** `src/engine/event_reactor_adapter.py:13903–13975`
**Function:** `_latest_snapshot_rows_for_event_family(..., require_fresh=False)`

Called with `require_fresh=False` for family-completeness proofs (market identity does not
decay with price age). The `freshness_deadline >= ?` predicate is applied only when
`require_fresh=True`. This gate is correctly NOT applying the tight price-freshness to market
identity checks.

---

## THE BUDGET + BASIS (numbers)

### Selection-stage budget
- **Window:** 600 seconds (10 minutes)
- **Basis computation:** `selection_deadline = captured_at + 600s` vs `decision_time`
- `captured_at` = timestamp the CLOB book was fetched by the warm-substrate job
- `decision_time` = wall-clock time the reactor is evaluating the decision
- **Who sets captured_at:** `src/data/market_scanner.py:2979` — `captured_at=captured` (the
  `datetime` passed into `capture_executable_market_snapshot`), which is `time.time()` at fetch
  in the warm job. This is the genuine CLOB-fetch instant — not the decision time, not a DB
  write time. Basis is real.
- **Historical note:** the window was 30s prior to 2026-06-09 (commit comment #122). At 30s
  with a ~5.4min per-family warm cadence, the elapsed time from capture to decision was
  ~325s, so 30s/325s = ~9% of wall-clock time a family was decidable. Comment at
  `event_reactor_adapter.py:2277–2278` explicitly states: "so the elected row is price-stale
  ~91% of wall-clock time." The 600s window was introduced to cover the ~5.4min cadence plus
  jitter.

### Execution-stage (JIT pre-submit) budget
- **Window:** 30 seconds
- **Constant:** `_K1_DEFAULT_PRESUBMIT_FRESHNESS_SECONDS = 30.0`
  (`src/engine/event_reactor_adapter.py:434`)
- **Basis computation:** `freshness_deadline = decision_time + 30s` (JIT witness captures
  at the decision instant, so the deadline is 30s from right now). This is a submit-time
  authority: by the time submit fires, the JIT book is milliseconds old, so 30s is a generous
  safety margin for the network round-trip + signing path.

### Snapshot SELECTION freshness window (baked into each snapshot object)
- **Value:** 180 seconds (since 2026-06-09 widening from 30s, #122)
- **File:** `src/contracts/executable_market_snapshot.py:47`
  `FRESHNESS_WINDOW_DEFAULT = timedelta(seconds=180)`
- **Baked at capture:** `src/data/market_scanner.py:2980`
  `freshness_deadline=captured + FRESHNESS_WINDOW_DEFAULT`
- **Used by:** `is_fresh(snapshot, now)` (execution-path freshness, cycle_runtime.py) and as
  fallback in `_snapshot_price_stale_reason` when `captured_at` is absent.

---

## CADENCE vs BUDGET

| Layer | Cadence | Budget / Window | Coverage ratio |
|-------|---------|----------------|----------------|
| EDLI warm substrate (fires all families) | 20s interval (`_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS = 20.0`, `src/main.py:85`) — but this fires a ROTATING SLICE, not all families | N/A | N/A |
| Per-family effective sweep | ~5.4 min (documented `event_reactor_adapter.py:294,2277`) — ~150 families / ~17s budget per cycle = ~1 family/0.11s, 150 cycles × 20s = ~50min total sweep; comments say 5.4min but the math for the full live set is longer | 600s selection window | 600s / 324s ≈ 1.85× — NOW structurally covered after 600s widening |
| Snapshot baked freshness | At capture: freshness_deadline = captured_at + 180s | 180s | Warm interval (20s) << 180s — comfortably inside |
| Pre-submit JIT fetch | Per-submit (live /book fetch) | 30s | Not a cadence vs budget issue; the JIT fetch is synchronous at submit |

**Key structural relationship (asserted at boot):** `src/main.py:9351–9362` asserts that
`_warm_refresh_budget_s < _EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS`, preventing budget overrun.
Comment at `src/main.py:83–84` states the interval "stays within the 180s executable-price
freshness window" — 20s << 180s. This relation is healthy.

**Previous C5 failure (now mitigated):** Before the 600s selection-window widening, the 30s
window meant the SELECTION gate rejected ~91% of decision attempts because the 5.4min
per-family cadence left every elected row stale before the decision path ran. Code comment at
`event_reactor_adapter.py:294–297` documents this explicitly: "On that cadence any family is
decidable only ~9% of wall-clock time." The C5 widening to 600s plus the decision-triggered
targeted-refresh path (`FamilySnapshotRefresher`) together address this starvation.

---

## THE 60–97% EVIDENCE (grounded)

**Source:** `docs/evidence/timing_audit/live_latency_bottlenecks_2026-06-16.md`, Section 5

**Method:** Direct SQL query against `edli_live_order_events` in zeus-world.db (Jun 1–16).
Read-only query counting `Attempted`, `Acked`, `Rejected`, `Unknown`, `Filled` rows per day.

**Raw numbers:**

| Day | Attempted | Acked | Rejected | Ack Rate |
|-----|-----------|-------|----------|---------|
| Jun 1 | 45 | 0 | 44 | 0% |
| Jun 6 | 39 | 13 | 22 | 33% |
| Jun 7 | 40 | 10 | 28 | 25% |
| Jun 10 | 23 | 7 | 16 | 30% |
| Jun 11 | 31 | 1 | 30 | 3% |
| Jun 12 | 29 | 5 | 15 | 17% |
| Jun 15 | 9 | 2 | 6 | 22% |
| Jun 16 | 17 | 8 | 5 | 47% |

**Overall: ~60–70% of venue submit attempts rejected on most days. Jun 11 = 97% rejection.
Jun 1 = 98% rejection.**

**Critical observation from the evidence doc:**
> "Rejection payloads (`SubmitRejected`) contain `raw_response_hash=None` consistently — the
> rejection happens before a venue response is received, implying pre-submission validation
> failure (stale quote, price moved, size constraint)."

The `raw_response_hash=None` finding means the rejection happens INSIDE Zeus before the order
even reaches Polymarket's matching engine. This is a Zeus-side pre-submit gate firing, not a
Polymarket-side rejection.

**What is not stated in the evidence doc:** The exact breakdown of rejection sub-reasons
(TAKER_BUY_TOUCH_EXCEEDS_RESERVATION vs EXECUTABLE_SNAPSHOT_STALE vs
SUBMIT_ABORTED_PRICE_MOVED vs tradeability-flag failures) is NOT in the DB. The
`edli_live_order_events` table records that a rejection occurred and the reason field, but the
evidence doc does not report the reason field's content. The "stale quote" label in the task
brief is an inference from the symptom, not a confirmed reason-code breakdown.

**The 60-97% figure is a VENUE-SUBMIT rejection rate, not a SELECTION-stage stale-snapshot
rate.** These are different layers. The SELECTION-stage starvation (the ~91% figure documented
in source comments) predates the June fixes and was SELECTION-layer starvation, not a
submit-layer rate.

---

## COMPETING HYPOTHESES

### H1 — STARVATION BUG (over-tight or mis-based budget causes correct data to be wrongly rejected)

**Claim:** The freshness budget is so tight relative to the warm cadence that legitimately
fresh-enough quotes get marked stale before the reactor decides, starving the submission lane.

**Evidence FOR:**
- Code comment at `event_reactor_adapter.py:294–298` documents that the old 30s window caused
  ~91% stale-rejection at the SELECTION stage. This was a confirmed starvation bug.
- The 600s selection-window widening was explicitly motivated by this starvation diagnosis
  (commit comment #122, referenced in `event_reactor_adapter.py:13857–13863`).
- The C5 implementation report (`impl_C5_AB4_2026-06-16.md`) confirms the fix was to widen
  the selection window.
- The per-family warm cadence (~5.4min) exceeded the old 30s window by 10×, which is a textbook
  C5 cadence-coverage failure.

**Evidence AGAINST (for the current state):**
- The 600s window now covers the ~5.4min per-family cadence with ~1.85× headroom. The
  structural starvation is mitigated.
- The decision-triggered targeted-refresh path (`family_snapshot_refresher`) provides a
  just-in-time recapture before the SELECTION rejection fires. Even if the warm-lane snapshot
  is stale, the adapter fires a targeted CLOB refresh and re-checks.
- Jun 16 ack rate improved to 47% — consistent with a partially-fixed (but not fully resolved)
  problem.

**Current confidence:** HIGH that H1 was the dominant cause in Jun 1–11. MEDIUM that it
remains the dominant cause in Jun 12–16 given the selection-window widening.

### H2 — CORRECT REFUSAL (the rejected quotes are genuinely stale / price-moved; system is working correctly)

**Claim:** The 60–97% rejection rate reflects real price movement between decision time and
submit time; the JIT witness correctly refuses because the price drifted above the q_lcb
reservation, and refusing is the right behavior (never submit above q_lcb).

**Evidence FOR:**
- `fallback_outcome_quality_2026-06-16.md` confirms: "Every single stale or unavailable
  decision was hard-refused by the execution gate." Zero stale or degraded decisions ever
  reached settlement. The gate is functioning.
- The reject payload has `raw_response_hash=None` (Zeus-side, pre-venue), consistent with the
  JIT witness `TAKER_BUY_TOUCH_EXCEEDS_RESERVATION` abort — a correct safety refuse.
- Thin, slow weather books (as noted in the 180s comment at `executable_market_snapshot.py:43`)
  can still move if a large operator order crosses between decision and submit. A 30s JIT
  window is generous for a thin book; the refuse is correct when ask > reservation.
- Jun 16 improving to 47% ack rate without any known code change to the JIT gate — suggests
  market conditions (book thinness, operator co-trading) play a role.

**Evidence AGAINST:**
- If rejects were purely due to correct price-moved signals, the rate would track market
  volatility. Jun 1 (0% ack) and Jun 11 (3% ack) are severe outliers inconsistent with purely
  random price-move events. Jun 1 = 45 consecutive rejections in one day with zero acks
  suggests a systematic, not stochastic, failure.
- `raw_response_hash=None` is consistent with multiple pre-venue gates (not exclusively
  price-moved); tradeability issues and depth-authority mismatches also produce pre-venue
  rejects.
- Jun 11's 31 attempts with 30 rejects (97%) occurring the same day as documented
  `MONEY_PATH_TRANSIENT_EXHAUSTED` dead-letters (referenced in
  `event_reactor_adapter.py:298`) is direct evidence of the C5 starvation-then-exhaustion
  pattern: selections repeatedly land stale → requeue → all 6 built intents
  dead-letter → 0 real submits.

**Current confidence:** MEDIUM that H2 is a partial (and perhaps minor) contributor. LOW
confidence it explains the outlier days.

### H3 — MIS-BASED TIMESTAMP (quote-age computed against wrong reference point)

**Claim:** The freshness gate uses the wrong timestamp as the age baseline — e.g., a DB write
time instead of the actual CLOB-fetch instant — causing the measured age to be larger than the
true quote age.

**Evidence FOR:**
- `live_latency_bottlenecks_2026-06-16.md` notes that `source_run` fetch timestamps show
  `fetch_start = fetch_end (0s)`, suggesting timestamp recording artifacts exist elsewhere.

**Evidence AGAINST:**
- `captured_at` is explicitly set to the `captured_at` argument passed into
  `capture_executable_market_snapshot()` (`src/data/market_scanner.py:2979`), which is the
  `datetime` at which the live CLOB fetch was initiated, not a DB write time. The docstring
  at `event_reactor_adapter.py:453–457` confirms: "captured_at is anchored to the fetch
  instant, NOT the elected row's stale captured_at."
- `is_fresh(snapshot, now)` compares `now <= snapshot.freshness_deadline` where
  `freshness_deadline = captured_at + FRESHNESS_WINDOW_DEFAULT`. The comparison is
  `wall_clock_now <= (clob_fetch_instant + 180s)`. The basis is correct.
- No code evidence of timestamp mis-basing at either the selection or submission stage.

**Current confidence:** LOW. Timestamp basis appears correct statically. Would need log
evidence to confirm.

---

## REBUTTAL ROUND

**Best challenge to H1 (starvation):** The starvation was demonstrably fixed by widening
the selection window to 600s. If H1 were still the dominant cause, Jun 16 should show a
similarly high rejection rate, but it improved to 47% ack. The warm cadence (5.4min) now
fits inside the 600s window. The targeted-refresh path provides a JIT escape valve. H1 as
currently framed no longer explains the residual ~50–60% rejection rate post-fix.

**Why H1 still partially stands:** "Fixed" is overstated. Two contributing factors remain:
(a) The decision-triggered refresher can itself fail (Exception path falls through to stale
rejection — `event_reactor_adapter.py:2319–2327`). If the family refresher is slow or
errors, the selection-stage still fails. (b) The targeted-refresh uses the warm-job capture
path which includes a Gamma topology reconstruct + CLOB /book. If the CLOB client is
unavailable or slow at decision time, the refresh fails. (c) The comment at
`event_reactor_adapter.py:13857–13858` notes the live 2026-06-15 state: "families_needing_
refresh oscillated 1→116 of 188; processed=0 decisions/cycle" — meaning at some point the
refresher was not functioning (zero decisions processed). The 600s window widening may have
been committed on the same day as zero-decisions were observed, suggesting the widening is
recent and the refresher's effectiveness is still uncertain.

**Best challenge to H2 (correct refusal):** If rejects were all legitimate price-moved aborts,
the ack rate should be non-trivially higher on low-volatility days; thin books with no
competing orders should not move within 1–2s of submit. The evidence doc's "stale quote,
price moved, size constraint" list of causes is an inference, not a confirmed reason-code
breakdown.

---

## CONVERGENCE / SEPARATION NOTES

H1 (starvation bug) and H2 (correct refusal) are **genuinely distinct** root causes:
- H1 produces SELECTION-stage rejects (`EXECUTABLE_SNAPSHOT_STALE` as the `receipt.reason`)
  that never reach the JIT pre-submit path. They inflate no-submit rates at the
  SELECTION layer, not the VENUE-SUBMIT layer.
- H2 produces SUBMISSION-stage aborts (`TAKER_BUY_TOUCH_EXCEEDS_RESERVATION`,
  `SUBMIT_ABORTED_PRICE_MOVED`, depth-authority mismatch) which show up as `SubmitRejected`
  events in `edli_live_order_events`.

The 60–97% figure from `edli_live_order_events` is a VENUE-SUBMIT metric; it counts attempts
that reached the venue-submit path and were then rejected pre-venue. SELECTION-stage stale
rejects would NOT appear in this table (they produce `EventSubmissionReceipt(False, ...)`
before the submit path is invoked). Therefore:
- The old SELECTION starvation (H1/30s window) was invisible in the 60–97% figure — it
  caused decisions to NEVER ATTEMPT a venue submit.
- The 60–97% reject rate is a POST-SELECTION phenomenon: decisions that cleared the
  selection gate and were attempted, then rejected at the JIT/pre-submit stage.
- H1 and the 60–97% figure describe different failure layers that were conflated in the task
  brief.

---

## FALLBACK SAFETY

### Selection stage: FAIL-CLOSED
If `selected_stale_reason is not None` after refresh:
- `return EventSubmissionReceipt(False, ...)` — no trade, no submission.
  (`src/engine/event_reactor_adapter.py:2377–2383`)
- No fallback that submits on a stale selection snapshot.

### Submission stage: FAIL-CLOSED with typed abort
- `TAKER_BUY_TOUCH_EXCEEDS_RESERVATION` → abort, candidate requeues.
  (`src/engine/event_reactor_adapter.py:4355–4358`)
- `_assert_taker_depth_authority_fresh` mismatch → abort with typed TRANSIENT, requeues.
  (`src/engine/event_reactor_adapter.py:4389–4394`)
- `_ensure_fresh_executable_snapshot` stale + no client → `raise ValueError
  ("executable_snapshot_stale")` → execution aborted.
  (`src/engine/cycle_runtime.py:864–865, 878–884`)

**No fallback anywhere on the execution path that would submit on a stale or price-moved
quote.** `fallback_outcome_quality_2026-06-16.md` confirms empirically: zero stale or
degraded decisions reached settlement across all observed history (n=42 settled trades).

**Verdict: FAIL-CLOSED throughout.** The system refuses correctly; it does not fall forward
on stale quotes. The operator "must-refuse" law is satisfied at both the selection and
submission stages.

---

## RECOMMENDED NEXT PROBE

### Critical unknown

The exact reason-code breakdown of the 60–97% `SubmitRejected` events. The evidence doc
establishes that `raw_response_hash=None` (pre-venue) but does not report the `reason` field
from `edli_live_order_events`. Without it, we cannot distinguish:
- `TAKER_BUY_TOUCH_EXCEEDS_RESERVATION` (price moved post-selection → H2, correct refusal)
- `SUBMIT_ABORTED_PRICE_MOVED` (price-race abort at the redecision layer → H2, correct)
- `executable_snapshot_stale` at cycle_runtime (`_ensure_fresh_executable_snapshot` failing
  → residual H1 starvation, still hitting the submit path)
- `pre_submit_collateral_reservation_failed` (unrelated to quote freshness)
- Tradeability flags (market closed between selection and submit)

### Discriminating probe

```sql
-- Run against zeus-world.db (read-only, CLI access required due to broken trigger)
SELECT
    substr(occurred_at, 1, 10) as day,
    reason,
    COUNT(*) as n
FROM edli_live_order_events
WHERE event_type = 'SubmitRejected'
  AND occurred_at >= '2026-06-01'
GROUP BY day, reason
ORDER BY day, n DESC;
```

This single query collapses the ambiguity: if `TAKER_BUY_TOUCH_EXCEEDS_RESERVATION` dominates,
the rejects are correct price-safety refuses (H2). If `executable_snapshot_stale` or a
SELECTION-stall reason dominates, the starvation bug is leaking into the submit path (residual
H1). If tradeability reasons dominate, neither hypothesis applies and the root is a market-
lifecycle issue. The query is read-only and sub-second; it requires the sqlite3 CLI (not Python
sqlite3, due to the broken trigger `trg_opportunity_events_no_update` in zeus-world.db).

---

## UNCERTAINTY NOTES

1. The per-family warm cadence (~5.4min) cited in source comments was measured under live
   conditions at some prior point; the current live cadence (post 600s widening + cursor
   rotation) is not confirmed from DB evidence. If the live family count changed or the cursor
   slice size changed, the cadence could differ.

2. The `family_snapshot_refresher` callable's current live effectiveness is unclear. The code
   shows it can fail silently (Exception falls through to stale reject). Whether it is
   successfully refreshing families on the 2026-06-16 run is not confirmed from static analysis.

3. The 600s selection window is described in the code as "interim constant pending #64 staleness
   fitting" (`event_reactor_adapter.py:13863`) — explicitly acknowledged as a guess pending
   measurement. Its adequacy depends on the actual per-family cadence in the current live run.

4. The `edli_live_order_events` reason field content has not been confirmed for the Jun 1–16
   period. All starvation vs correct-refusal reasoning above is inference from code structure
   and event counts, not confirmed reason-code analysis.
