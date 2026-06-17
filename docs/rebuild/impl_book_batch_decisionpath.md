# Per-token GET /book storm fix — batch decision/capture path

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: operator task (dead_order_lane_per_token_book_storm_2026-06-16.md);
  RULE 1 (a suppression is OUR defect until settlement proves otherwise).
- Files touched (MAIN tree `/Users/leofitz/zeus`, branch `live/iteration-2026-06-13`, HEAD `69da9e1eda`):
  - `src/data/market_scanner.py` — `_prefetch_selected_orderbooks` (the only fix)
  - `tests/test_market_discovery_full_coverage.py` — re-pointed one test, added one antibody
- NOT committed. NOT restarted. Orchestrator reviews/commits/deploys.

## TL;DR — what the diagnosis attributed vs. what the code actually does

The diagnosis said the *single-family DECISION / pre-warm* capture path "does NOT
batch — it calls capture with `prefetched_orderbook=None`, hitting the per-token
`_fetch_orderbook_snapshot` fallback for every token." **That is not what current HEAD
does.** I traced every call site and reproduced the path: the decision/pre-warm path
ALREADY batches. The real storm is a *budget-window guard* inside the shared batch
helper that **silently skips the batch and dumps the whole family to per-token GET /book**
when the prefetch time-window is tight. The fix is one surgical change to that guard. It
reuses the existing helper, preserves the per-token fallback, and changes no budget,
freshness window, gate, interleave, or cycle budget.

## Call-chain pinpoint (task #1) — every capture / per-token locus

The ONLY caller of the per-token `_fetch_orderbook_snapshot` (→ `GET /book`) is the
fallback inside `capture_executable_market_snapshot` when `prefetched_orderbook is None`:

- `src/data/market_scanner.py:2822-2825` — `prefetched_orderbook is not None ?
  _normalize_prefetched_orderbook : _fetch_orderbook_snapshot(clob, selected_token)`.

Every `capture_executable_market_snapshot` call site:

| call site | tokens | prefetched? | storm? |
|---|---|---|---|
| `market_scanner.py:4161` (inside `refresh_executable_market_substrate_snapshots`) | whole family | `prefetched_orderbook=prefetched_book` from `_prefetch_selected_orderbooks` (`market_scanner.py:4119`) | **the storm source — only when the batch returns {} / partial** |
| `execution/executor.py:2037` | 1 selected token (submit path) | None | no — 1 token = 1 GET, correct |
| `execution/exit_lifecycle.py:1842` | 1 selected token (exit) | None | no — 1 token, correct |
| `engine/cycle_runtime.py:869` | 1 selected token (stale re-capture) | None | no — 1 token, correct |

Every `refresh_executable_market_substrate_snapshots` caller (all route through the
batched `_prefetch_selected_orderbooks`):

- `main.py:7590` — `_edli_decision_family_snapshot_refresher` (the reactor's
  `_family_snapshot_refresher`, used by `_prewarm_event_family_snapshot`
  reactor.py:1200 AND the end-of-cycle drain reactor.py:1362). Passes
  `max_outcomes=0`, **no `budget_seconds`** → env default
  `ZEUS_MARKET_DISCOVERY_SNAPSHOT_BUDGET_SECONDS=600` → ~588s prefetch window → **always batches.**
- `main.py:4114 / 4278 / 4364` — warm-job lanes (`refresh_pending_family_snapshots` /
  `EDLI market-substrate warm`). Pass a TIGHT `budget_seconds` derived from
  `ZEUS_REACTOR_REFRESH_BUDGET_SECONDS` (live ≈14s, reserve 12s → **≈2s prefetch window**).
- `main.py:8898` — market-channel refresh (rate-limited 5-20 actions/window; batched).

The submit-time JIT provider `_edli_pre_submit_jit_book_quote_provider` (`main.py:7478`)
and the market-channel feed `clob.get_orderbook_snapshot` (`main.py:8927`) fetch ONE
token each — not a family — so they are not the dozens-per-family storm.

## Evidence (live daemon `logs/zeus-live.log`, recent ~27h tail)

- **104,197 `GET /book` vs 2,405 `POST /books`** (43:1) — matches the diagnosis's 45:1.
- Warm-job summaries show the smoking gun: cycles with `snapshot_budget_seconds: 14.0`,
  `snapshot_capture_reserve_seconds: 12.0`, **`prefetched_orderbook_count: 0`** — the
  batch was skipped and every candidate fell to per-token GET /book (then
  `handshake operation timed out` / `database is locked`). Other cycles with a wider
  window show `prefetched_orderbook_count: 441 / 500` (batch fired).
- Nearest-preceding-marker attribution of the GET /book storm (approximate, interleaved
  single-thread log): warm_pending 32,961 · warm_job 27,811 · scout 20,737 · reactor
  17,421 · exit_monitor 5,267.

## Root-cause mechanism (reproduced, not inferred)

`_prefetch_selected_orderbooks` had a **pre-loop guard**: if `(deadline - now)` <
`ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS` (0.75s) it returned `{}`
→ capture then ran a SEQUENTIAL per-token `GET /book` (~650ms each) for EVERY token in
the family. A 22-token family becomes ≈14s of serial HTTP instead of one ~1s POST —
**strictly slower than the POST the guard was avoiding**, and it exceeds the 30s
snapshot freshness window → EXECUTABLE_SNAPSHOT_BLOCKED → requeue → no decision.

Reproduced in isolation (single 11-bin family, spy clob counting GET vs POST):

- decision-refresher path (130/600s budget): **GET=0, POST=1, prefetched=22** — already batches.
- warm-job budget=14s, fast POST: GET=0, POST=1, prefetched=22 — batches.
- **warm-job budget=2s (window < 0.75 min): GET=11, POST=0, prefetched=0** — the storm.

## The fix (task #2 + #3) — `src/data/market_scanner.py::_prefetch_selected_orderbooks`

Move the budget gate INTO the chunk loop and exempt chunk 0, so the FIRST POST /books
chunk ALWAYS fires (it replaces the per-token GETs the fallback would run anyway,
bounded only by the client's own HTTP timeout — the same bound a single GET /book
carries), while the SECOND-and-later chunks of a large multi-chunk warm cycle stay
budget-gated (deferring extra chunks to a later cycle is genuine budget protection; the
deferred tokens fall back per-token in capture, never abort).

BEFORE (pre-loop skip + unconditional in-loop deadline break):

```python
    if deadline is not None:
        min_prefetch_window = _positive_float_env(
            "ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS", 0.75)
        remaining_window = deadline - time.monotonic()
        if remaining_window < min_prefetch_window:
            logger.info("Batch orderbook prefetch skipped: window %.3fs below %.3fs minimum",
                        remaining_window, min_prefetch_window)
            return {}                      # <-- DUMPS WHOLE FAMILY TO PER-TOKEN GET /book

    books: dict[str, dict] = {}
    for start in range(0, len(token_ids), _BATCH_ORDERBOOK_CHUNK):
        if deadline is not None and time.monotonic() >= deadline:
            logger.info("Batch orderbook prefetch stopped at budget deadline after %d/%d tokens",
                        start, len(token_ids))
            break                          # <-- gates chunk 0 too
        chunk = token_ids[start : start + _BATCH_ORDERBOOK_CHUNK]
```

AFTER (no pre-loop skip; gate applies to chunk_index > 0 only):

```python
    min_prefetch_window = _positive_float_env(
        "ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS", 0.75)

    books: dict[str, dict] = {}
    for chunk_index, start in enumerate(range(0, len(token_ids), _BATCH_ORDERBOOK_CHUNK)):
        if chunk_index > 0 and deadline is not None:
            remaining_window = deadline - time.monotonic()
            if remaining_window < min_prefetch_window:
                logger.info(
                    "Batch orderbook prefetch stopped after chunk %d/%d "
                    "(window %.3fs below %.3fs minimum); remaining tokens fall back per-token",
                    chunk_index,
                    (len(token_ids) + _BATCH_ORDERBOOK_CHUNK - 1) // _BATCH_ORDERBOOK_CHUNK,
                    remaining_window, min_prefetch_window)
                break
        chunk = token_ids[start : start + _BATCH_ORDERBOOK_CHUNK]
```

(Plus a ~16-line provenance comment block above, citing this evidence doc.)

### Why this honors every constraint

- **Reuses the existing batch helper** (`_prefetch_selected_orderbooks` → `clob.get_orderbook_snapshots`
  → one `POST /books`). No new batching system; the byte-identical-shape contract and
  `_assert_clob_identity` validation are unchanged (`market_scanner.py:2859`).
- **Preserves per-token fallback** for any token MISSING from the batch response and for
  every chunk deferred past chunk 0 — capture's `prefetched_orderbook=None` branch
  (market_scanner.py:2822-2825) is untouched; a partial/empty batch NEVER aborts the family.
- **Changes no budget / freshness window / gate / interleave / cycle budget.** The
  `min_prefetch_window` value, the 30s freshness window, the per-cycle decision budget,
  the fair-lane interleave, and every q/edge/money-path gate are byte-identical. The only
  behavioral delta: the ONE batch POST is always attempted instead of being skipped in
  favor of a strictly-slower per-token storm.

## Could-not-batch loci (none) — single-token paths intentionally left per-token

`executor.py:2037`, `exit_lifecycle.py:1842`, `cycle_runtime.py:869` each capture ONE
selected token. Batching a single token into `POST /books` is one round-trip either way
— over-engineering. They correctly use the per-token fallback the operator told me to
preserve. Not changed.

## Tests

Modified `tests/test_market_discovery_full_coverage.py`:
- **Re-pointed** `test_tiny_prefetch_window_skips_batch_books_and_captures` →
  `test_tiny_prefetch_window_still_attempts_one_batch_books`. The old test ASSERTED the
  storm-causing skip (`get_orderbook_snapshots.call_count == 0`, `prefetched_orderbook_count == 0`).
  It now asserts the corrected behavior: exactly ONE batch POST fires under a tiny window
  and every token is prefetched (no per-token fallback). RED-on-revert documented inline.
- **Added** `test_prefetch_first_chunk_always_fires_chunk2plus_budget_gated` — boundary
  antibody driving `_prefetch_selected_orderbooks` directly with a 2-token chunk size:
  no-deadline → all 3 chunks fire; past-deadline → chunk 0 STILL fires, chunks 1+ gated.

### Test command output

`tests/test_market_discovery_full_coverage.py` + `tests/test_executable_market_snapshot.py`:
**120 passed, 1 failed** — the 1 failure is `test_cached_topology_limits_gamma_lookup_window`
(Gamma-lookup deadline math, function NOT touched; verified FAILS on clean HEAD = pre-existing).

`tests/money_path/ tests/strategy/live_inference/`:
**341 passed, 3 failed** — all 3 are `tests/money_path/test_finding_b_free_cash_bound.py`
(operator-listed known pre-existing bankroll-harness failure; verified FAILS on clean HEAD).

`tests/data/ tests/events/` (snapshot/substrate/reactor-refresh subset):
**82 passed, 2 failed** — both `tests/events/test_always_decidable_invariant.py`
(reactor refresh-failure logging, reactor.py NOT touched; verified FAILS on clean HEAD).

`tests/money_path/ tests/strategy/live_inference/ tests/data/` (full):
**425 passed, 5 failed** — the 3 free_cash_bound + 2
`tests/data/test_replacement_cycle_availability.py` (date-dependent forecast-availability
harness, NOT touched; verified FAILS on clean HEAD).

`tests/money_path/test_edli_market_substrate_warm_cycle.py` +
`tests/test_market_substrate_warm_lock_contention.py` +
`tests/test_edli_market_channel_refresh_authority.py`: **30 passed.**

All 4 new/modified prefetch/batch/storm tests pass. **Every failure above is
pre-existing on untouched HEAD `69da9e1eda` (verified by stash-and-rerun); none is caused
by this change.** The two operator-named known failures are present and accounted for; the
other pre-existing failures (gamma-lookup window, always-decidable logging,
replacement-cycle availability) are likewise date/harness issues outside the touched code.

## Expected live effect

A 22-44-token family capture collapses from ~14-28s of sequential GET /book to one ~1s
POST /books, so it finishes well inside the 30s freshness window → forecast families
decide instead of EXECUTABLE_SNAPSHOT_BLOCKED-requeuing, and the warm/decision lanes stop
flooding the venue with the per-token storm (104k GET → expected near the ~2.4k POST
baseline). No gate loosened, no forced order — purely N sequential GET /book → 1 POST /books.

## Open cross-check (non-blocking)

A ChatGPT-Pro consult was fired in parallel to adversarially check the
invert-the-skip decision (risk of the batch POST itself overrunning the deadline vs N
GETs overrunning it far worse; whether a tiny floor should remain). Answer pending at
write time (`/tmp/cgc_answer_REQ-20260616-154851-d5280c.txt`); the fix already bounds the
single batch POST by the client's own HTTP timeout (same bound a single GET carries) and
keeps the chunk-2+ gate, so it is conservative regardless.
