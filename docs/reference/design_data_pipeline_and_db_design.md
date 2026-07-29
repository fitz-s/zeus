# Zeus Data Pipeline & DB Design — The Active-Load-and-Hold Data Layer

Created: 2026-06-08
Last reused or audited: 2026-06-08
Authority basis: docs/architecture/system_decomposition_plan.md (P1..P4 boundaries, I1..I7 seams);
  AGENTS.md money path (`contract semantics -> source truth -> forecast signal -> calibration ->
  edge -> execution`) + INV-37 cross-DB rule; architecture/db_table_ownership.yaml schema_version 2;
  src/data/release_calendar.py + config/source_release_calendar.yaml (the existing per-source
  release-availability authority this design generalizes).

READ-ONLY design. This doc changes no running system. It cites concrete `file:line` / table /
config-key for every claim and proposes the target shape + a per-item migration.

---

## 0. The operator principle, stated as one invariant

**INV-LOAD (the whole design): every input the order decision consumes is, at decision time,
a READ of a warm row a producer has already pre-fetched and is keeping fresh on ITS OWN source's
release clock. The decide -> size -> submit -> manage path performs ZERO network/JIT fetch.**

The 00Z/12Z forecast switch is one instance. The general law: the system has N data sources, each
with its own publish clock (ECMWF 00/06/12/18Z, AIFS 00/12Z, GFS 4×/day, ICON-D2 every 3h, AROME
hourly, the CLOB book continuously, the on-chain wallet continuously, the calibration daily refit,
settlement per-resolution). For each source there is exactly ONE producer that (a) detects that
source's next release the instant it is available, (b) fetches it, and (c) writes a warm
**held-ready** row carrying an explicit freshness + identity contract. The runtime never triggers
a fetch; it only `SELECT`s a held-ready row and checks its stamped freshness.

Today this invariant holds for SOME inputs (calibration pin, settlement, risk_state — all DB reads
of rows another process writes) and is VIOLATED for the rest in two distinct ways:

1. **Late producer** (DB read is fresh-in-shape, but the producer is fixed-lag, not release-driven):
   the U0R forecast download (§3.1), readiness, baseline.
2. **JIT-in-decision-path** (the runtime itself does a network call during decide/submit):
   pre-submit `/book` (§3.3), collateral (§3.4), in-cycle bankroll (§3.5), submit recapture (§3.6),
   plus the producer-side per-candidate `market_info`/`/book` HTTP (§3.7).

A THIRD, subtler form lives UNDER the JIT and is why the pre-submit `/book` JIT cannot simply be deleted:

3. **Wrong-clock freshness gate** (the held row exists but the runtime bounds its freshness on the
   SOURCE's change-time, not the PRODUCER's observation-time, so an idle source makes the held row
   read as stale): the pre-submit book's `quote_seen_at` is the venue book-change instant
   (market_channel_ingestor.py:236,265), and the runtime gate bounds on it (src/main.py:5165-5174,
   `max_quote_age_ms=1000`). For a book that has not changed there is no new WS message, so the held
   row ages unbounded and fails closed — the JIT was built to paper over exactly this. Removing the JIT
   WITHOUT fixing the clock (§3.3.1) converts a working JIT into a fail-closed stale-row. This is the
   one input where "delete the JIT, read the held row" is insufficient; it needs the producer-freshness
   `captured_at` contract that `executable_market_snapshots` already has.

The fix is structural, not 8 patches: **one producer per source, one held-ready table per input,
one release detector (the existing `release_calendar` generalized), one rule (`evaluate_safe_fetch`)
that fires the producer on `next_safe_fetch_at`.** §1 is the mechanism; §2 the release tracker;
§3 the per-input zero-JIT conversion; §4 the per-model `forecast_hours`; §5 the DB.

---

## 1. The active-load-and-hold mechanism

### 1.1 Three layers, never crossed

```
  RELEASE-DETECTOR (per source)         PRODUCER (per source)           HELD-READY TABLE        RUNTIME
  release_calendar.evaluate_safe_fetch  forecast-live / P2 / P3 /       (freshness + identity   reads warm row,
  -> next_safe_fetch_at                 data-ingest / bankroll-warm      contract, §1.3)         checks stamp,
  -> POLLED at source cadence           writes the warm row on release                           NEVER fetches
```

- **Release-detector layer** (§2): `src/data/release_calendar.py::evaluate_safe_fetch` (line 243) is
  ALREADY this layer for ECMWF OpenData. It reads `config/source_release_calendar.yaml` and returns
  `next_safe_fetch_at` (the instant the source's run is fetchable) + `FetchDecision.SKIPPED_NOT_RELEASED`
  until then (release_calendar.py:307-315). The design GENERALIZES this one function to every source
  (forecast models, CLOB book, wallet, calibration, settlement). No new detection primitive is invented;
  the antibody already exists and is under-used.
- **Producer layer**: one program per source-class per the decomposition plan (forecast-live, P2
  substrate-observer, P3 price-channel, data-ingest, riskguard, the P1-resident bankroll/mainstream
  warmers). A producer's ONLY trigger is its own source's `next_safe_fetch_at` (or a continuous-stream
  tick). A producer is NEVER gated on a consumer's in-process state (system_decomposition_plan §7
  I1–I7 no-back-coupling).
- **Held-ready table layer** (§1.3): every producer writes a row stamped with `(captured_at,
  freshness_deadline, source_cycle_time, source_id, product_identity, content_hash)`. The runtime reads
  the row and accepts it iff `now <= freshness_deadline` — a stale row is observably refused, never
  silently used, never lazily refreshed-on-miss.

### 1.2 The held-ready cycle (the producer side)

Each producer runs a `*_warm` / `*_observer` cycle:

```
on each tick (interval) OR on release(next_safe_fetch_at):
    decision = evaluate_safe_fetch(source_id, track, candidate_cycle, now)   # §2
    if decision is SKIPPED_NOT_RELEASED:  return            # not yet published; poll again soon
    payload = fetch(source)                                  # the ONLY place a network call lives
    write_held_ready_row(table, payload,
        captured_at=now, freshness_deadline=now + ttl(source),
        source_cycle_time=cycle, source_id=..., product_identity=..., content_hash=...)
```

Two precedents already implement exactly this and are the templates:
- `_edli_bankroll_warm_cycle` (src/main.py:4410) — warms `bankroll_provider` so the per-event Kelly
  read uses `bankroll_provider.cached()` (300s window) and "MUST NOT live-fetch per decision"
  (src/main.py:4415). This IS active-load-and-hold for bankroll. The defect (§3.5) is the reactor ALSO
  does its own `current(0.0)` at cycle start (src/main.py:4043) — a redundant in-cycle fetch the warm
  cache was built to eliminate.
- `_edli_market_substrate_warm_cycle` (lifted to P2 substrate-observer per the decomposition,
  src/main.py:72-79 comment) — warms `executable_market_snapshots`. Template for I1.

### 1.3 Held-ready table contract (the antibody that makes "stale at decision" unconstructable)

Every held-ready interface table carries these columns (some already present; this design makes the
set uniform and the freshness gate mandatory in the reader):

| Column | Meaning | Why it makes the violation impossible |
|---|---|---|
| `captured_at` | wall-clock instant the PRODUCER OBSERVED the source and wrote the row (NOT the source's own change/publish timestamp) | the runtime computes `age = now - captured_at`. **This MUST be the producer-observation clock; bounding freshness on the source's change-time instead is the §3.3 bug — a source that goes idle (an unchanged book, a quiet feed) stops emitting change events, so a source-change-time deadline ages unbounded and the held row fails closed even though it is valid.** `executable_market_snapshots` gets this right (producer `captured_at`, executable_market_snapshot.py:178); `execution_feasibility_evidence` did NOT (it bounded on venue change-time `quote_seen_at`) and is corrected in §3.3.1/DB-M5. |
| `freshness_deadline` | `captured_at + ttl(source)`; row is DEAD after this | reader refuses `now > freshness_deadline` — a stale row cannot leak (correctly enforced on the PRODUCER clock for snapshots: `is_fresh`, src/contracts/executable_market_snapshot.py:364; for posteriors: `expires_at`, replacement_forecast_bundle_reader.py:468). The pre-submit gate (`max_quote_age_ms`, src/main.py:5171) is the one site enforcing on the WRONG (venue change-time) clock today — §3.3.1-b moves it onto producer `captured_at`. |
| `source_cycle_time` | the source's own publish cycle this row is FROM | proves the row tracks the source's clock, not the producer's; staleness-vs-source gate (bundle_reader.py:501-506) |
| `source_id` / `product_identity` | provenance (which model/endpoint/cell, which token) | Fitz Constraint #4: a row's value is reconstructable to its exact physical product (already done for `raw_model_forecasts`, db_table_ownership.yaml:199-235) |
| `content_hash` | hash of the payload (e.g. `book_hash`) | identity for "did the source actually change?" release detection (§2.3) |

**The reader's contract (uniform, one place):** a held-ready read returns `(row, is_fresh)`. The
decision path treats `not is_fresh` EXACTLY as "row missing" (fail-closed, observable as a stale row
with a timestamp) — it NEVER triggers a fetch to refresh it. Refresh is the producer's job on the
source's clock. This is the inverse of the GATE #84 pathology where the runtime fetched because the
row was stale (§3.3).

### 1.4 Why this is structural, not 8 patches

The 8 lazy/late inputs are symptoms of TWO design failures (Fitz §1: K structural decisions, K«N):
- **D1 — no generalized release tracker.** Only ECMWF OpenData consults `evaluate_safe_fetch`; every
  other source has an ad-hoc clock (the U0R 14h cron, the snapshot 300s TTL, the per-candidate HTTP on
  demand). Fix: one release calendar covering all sources; one producer-poll loop per source.
- **D2 — the runtime is allowed to fetch.** Five sites in the decide/submit path call the network
  (`/book`, collateral, bankroll, recapture, and the producer's per-candidate HTTP). Fix: the decision
  path is given ONLY held-ready readers; no CLOB/RPC client is wired into the reactor's decide/submit
  closures at all. With no client in scope, `clob.get_orderbook_snapshot(...)` in the decision path
  becomes un-writable (the wiring makes it impossible, not a lint rule).

---

## 2. Per-source release tracking (load instantly on each source's own publish)

### 2.1 The existing authority, generalized

`config/source_release_calendar.yaml` (loaded by `release_calendar.py:196`) already encodes per
`(source_id, track)`:
- `cycle_hours_utc` — the source's publish hours (e.g. ECMWF `[0,6,12,18]`, yaml:8).
- `cycle_profiles[].safe_fetch.{default_lag_minutes, min_partial_lag_minutes}` — minutes after the
  cycle hour when the run becomes fetchable (yaml:29,38). This is the **availability window** —
  `next_safe_fetch_at = cycle_utc + default_lag_minutes` (release_calendar.py:307).
- `cycle_profiles[].{max_step_hours, live_max_step_hours, horizon_profile}` — the source's real
  forecast horizon per cycle (yaml:25-42). **This is the per-model forecast_hours the U0R download is
  missing (§4).** The 06/18 ECMWF cycles already carry `horizon_profile: short` with a shorter
  `live_max_step_hours` (yaml:34-42) — the exact mechanism icon_d2/arome need.
- `max_source_lag_seconds` — the staleness ceiling (`STALE_BLOCKED`, release_calendar.py:296).

`evaluate_safe_fetch` returns `SKIPPED_NOT_RELEASED` with `next_safe_fetch_at` until the run is
released, then a fetchable verdict. **This is the entire release-detection primitive.** The
forecast-live OpenData track polls it every 5 min (`_opendata_safe_cycle_poll`,
forecast_live_daemon.py:768; job registered :868) and fires at 08:10/20:10 UTC — minutes after the
08:05/20:05 availability window (forecast_live_daemon.py:795,814). That is per-source, release-driven
loading done RIGHT. The U0R replacement download is the same kind of weather source but does NOT use
it (§3.1) — that is the gap.

### 2.2 What must be added to the calendar (so EVERY source is release-tracked)

Add `(source_id, track)` entries for the sources that currently have no calendar row (so each gets a
`next_safe_fetch_at` poll instead of a fixed lag or on-demand fetch):

| source_id | cycle_hours_utc | safe_fetch default_lag_minutes (pin from provider schedule) | live_max_step_hours (horizon) |
|---|---|---|---|
| `openmeteo_ecmwf_ifs` (U0R anchor) | `[0,12]` | ~485 (matches OpenData, replaces 14h=840min cron) | full ~144 |
| `openmeteo_aifs` | `[0,12]` | per AIFS dissemination | full ~144 |
| `gfs_global` | `[0,6,12,18]` | ~210 | ~120 |
| `icon_global` | `[0,6,12,18]` | ~240 | ~120 |
| `gem_global` | `[0,12]` | ~300 | ~120 |
| `jma_seamless` | `[0,6,12,18]` | ~360 | ~120 |
| `icon_eu` | `[0,6,12,18]` | ~180 | ~120 |
| `icon_d2` | `[0,3,6,9,12,15,18,21]` (every 3h) | ~120 | **~48** (the cap §4) |
| `meteofrance_arome_france_hd` | hourly/3-hourly | ~90 | **~48** (the cap §4) |

(`safe_fetch` minutes above are placeholders the implementer pins from each provider's published
schedule; the POINT is each source gets a calendar-driven `next_safe_fetch_at`, not one global 14h.)

The CLOB book and the on-chain wallet are CONTINUOUS sources (no discrete cycle). They are
release-tracked by **content-change detection** (§2.3), not by a cycle calendar.

### 2.3 Two release-detection modes

- **Discrete-cycle sources** (all weather models, calibration daily refit, TIGGE): detected by the
  calendar `next_safe_fetch_at` poll (§2.1). Producer polls every ~2–5 min; fires the instant
  `now >= next_safe_fetch_at` for an unfetched cycle; idempotent via `source_run_id` dedup
  (forecast_live_daemon.py:797). A new run is loaded within one poll interval of its publish, NOT a
  fixed 14h later.
- **Continuous sources** (CLOB book, on-chain wallet, settlement events): detected by **content
  identity**. The producer holds a persistent stream/poll and rewrites the held-ready row whenever the
  content_hash changes:
  - CLOB book → P3 market-channel WS already carries the book-change hash (`book_hash_before`, used at
    src/main.py:5159); P3 rewrites `execution_feasibility_evidence` per WS tick (the I2 seam). The
    release event IS the WS book-change message. **CAVEAT (the §3.3 trap): an idle book emits NO WS
    message, so "rewrite per WS tick" alone leaves a pinned-but-idle candidate's held row aging
    unbounded.** A continuous source whose "release" is a content-CHANGE event needs a separate
    producer-OBSERVATION heartbeat for pinned candidates (re-stamp `captured_at=now` on the held book
    every sub-1s even with no change, §3.3.1-a) so the held row's freshness tracks the producer's clock,
    not the venue's change clock. The freshness gate must bound on that producer `captured_at`, never on
    the venue change-time `quote_seen_at` (§3.3.1-b).
  - on-chain wallet → bankroll/collateral warmer polls the wallet RPC on its 60s clock; the held row's
    `content_hash` is the equity/allowance value (no-op write when unchanged).
  - settlement → data-ingest `_harvester_truth_writer_tick` (ingest_main.py:796) writes
    `settlement_outcomes` per resolution event; P4 resolver polls for new settled rows (I4).

Each source therefore has exactly ONE detector (calendar-poll OR content-hash) and is loaded within
minutes (discrete) or sub-second (continuous WS) of its OWN release. No single fixed lag exists
anywhere in the target design.

---

## 3. Zero-JIT-in-decision-path conversion (each lazy fetch -> a held-ready read)

The decision path is `_edli_event_reactor_cycle` (src/main.py:3975). It opens connections FRESH each
cycle (`get_world_connection` :4009, `get_forecasts_connection_read_only` :4025,
`get_trade_connection_with_world_required`) and ATTACHes forecasts RO for the calibration read
(:4018-4023). Below: each current lazy/late input, the exact site, and what it becomes. Net result:
the reactor's decide/size/submit/manage closures are wired with held-ready READERS only; no CLOB/RPC
client is passed into them.

### 3.1 U0R forecast download — FIXED_LAG_CRON -> release-calendar poll

- **Now:** `_replacement_forecast_publish_cron_hours()` (forecast_live_daemon.py:959-966) fires at
  `(0 + 14h, 12 + 14h) = 14:00/02:00 UTC` (config_key `replacement_forecast_shadow.download_release_lag_hours
  = 14.0`, config/settings.json). The OpenData track PROVES 00z is fetchable from ~08:05 UTC
  (forecast_live_daemon.py:795-800) — so the cron is ~6h late every cycle. The posterior the runtime
  reads (`forecast_posteriors`, replacement_forecast_bundle_reader.py:473-488) is therefore already
  ~6–8h behind source at the US open.
- **Becomes:** the download job is registered on the SAME release-calendar poll as OpenData. Replace
  `_replacement_forecast_publish_cron_hours()` with a 5-min `_opendata_safe_cycle_poll`-style loop
  (forecast_live_daemon.py:768) that calls `evaluate_safe_fetch("openmeteo_ecmwf_ifs", track, cycle,
  now)` and fires the instant the cycle is released (within one poll of ~08:10/20:10 UTC).
  `download_release_lag_hours` is DELETED as a trigger; it survives only as `source_available_at`
  provenance stamping (u0r_multimodel_download.py:529, which is correct — that is the model's stated
  availability, not the fetch trigger). Materialize stays a 5-min poll on downloaded manifests
  (config_key `materialization_interval_min = 5`) — already fine.
- **Migration M1:** add the U0R anchor + AIFS + extra-model entries to `source_release_calendar.yaml`
  (§2.2); change `_register_replacement_forecast_production_jobs` (forecast_live_daemon.py:973) to
  register a calendar-poll job (copy `_opendata_safe_cycle_poll`, :768) instead of the
  `_replacement_forecast_publish_cron_hours` cron (:983-989). Rollback: re-register the cron.

### 3.2 Forecast readiness / baseline / posterior staleness — late-because-of-3.1, then correct

- **Now:** `readiness_state` / baseline B0 (src/main.py baseline bundle provider) / `forecast_posteriors`
  are DB reads (good shape) but only as fresh as the §3.1 download; the 30h source-cycle cap
  (bundle_reader.py:501-506) and `expires_at` (bundle_reader.py:468) are CONSERVATIVE fail-closed
  bounds, not release tracking.
- **Becomes:** once §3.1 loads on release, these rows are naturally fresh; the staleness gates remain as
  the fail-closed backstop (KEEP — they are correct antibodies). No reader change needed beyond §3.1.
  The `freshness_deadline`/`source_cycle_time` contract (§1.3) is already present here.

### 3.3 Pre-submit `/book` quote — LAZY_JIT (PRIMARY) -> held-ready read (PRIMARY)

> **This is the one input where naïve M2 (just delete the JIT, fall back to the held row) is WRONG**
> and would CONVERT a working JIT into a fail-closed stale-row. The fix below adds the missing freshness
> semantics so the held row is genuinely active-load-and-hold. Without §3.3.1 the held fallback is NOT
> "held sub-second fresh" for idle books — it ages unbounded and every idle-candidate submit fails closed.

- **Now:** `_edli_pre_submit_jit_book_quote_provider` (src/main.py:5022) does
  `clob.get_orderbook_snapshot(token_id)` (:5037) as the **PRIMARY** authority (GATE #84, wired at
  :4296-4298); the DB row `execution_feasibility_evidence` (`_edli_latest_pre_submit_book_row`, :5216)
  is only the fail-closed fallback (:5151). The runtime WAITS on a single-token `/book` HTTP inside the
  submit path. The comment (src/main.py:5044-5051) is explicit about WHY the JIT exists, and it is the
  exact failure the naïve removal re-creates: the shared feed stamps `quote_seen_at` "with the venue
  book-CHANGE timestamp (1s resolution, often minutes stale for slow weather books), and only refreshes
  a given token when its WS tick arrives (median per-candidate gap ~11s)."

- **The freshness-semantics defect that breaks naïve M2 (the adversarial finding, confirmed in code):**
  the producer column the runtime gate bounds on is the WRONG clock.
  - `execution_feasibility_evidence.quote_seen_at` is written as
    `quote_seen_at=_timestamp_ms_to_iso(message.get("timestamp")) or received_at`
    (market_channel_ingestor.py:236 for `BOOK_SNAPSHOT`, :265 for `BEST_BID_ASK_CHANGED`).
    `_timestamp_ms_to_iso` merely reformats the venue's own `message["timestamp"]` — i.e. it is the
    **VENUE book-change instant**, not the producer's observation instant. (`received_at`, the producer
    clock, is only the fallback used when the venue omits a timestamp.)
  - The runtime gate is `row_age_ms = checked_at - quote_seen_at; if row_age_ms > max_quote_age_ms:
    raise PRE_SUBMIT_BOOK_AUTHORITY_STALE` (src/main.py:5165-5174), with `max_quote_age_ms = 1000`
    (config `pre_submit_max_quote_age_ms`, default 1000, src/main.py:5114; settings.json:121).
  - **Therefore for a slow/thin weather book that has NOT changed, there is NO new WS message, so no new
    row, so `quote_seen_at` ages unbounded.** Within ~1s of the last book-change the held row trips
    `PRE_SUBMIT_BOOK_AUTHORITY_STALE` and the submit fails closed — even though the book is perfectly
    valid and unchanged. That is exactly the case the JIT was built to serve (src/main.py:5102-5109).
    Bounding "is the held quote fresh?" on the VENUE change-time means an idle market is indistinguishable
    from a dead feed; an active-load-and-hold input must bound on PRODUCER observation freshness, the way
    `executable_market_snapshots` bounds on `captured_at`/`freshness_deadline` (producer-set), NOT on the
    source's change-time (executable_market_snapshot.py:178-179, 364-367).

#### 3.3.1 The fix that makes the held row genuinely active-load-and-hold (BOTH halves required)

Naïve M2 (wire `book_quote_provider=None`, accept the existing fallback) is **rejected as written**: it
removes the JIT without making the held row producer-fresh, so the fallback's `PRE_SUBMIT_BOOK_AUTHORITY_STALE`
branch (src/main.py:5174) fires on every idle candidate — a fail-closed regression, not a held-ready read.
The corrected M2 keeps D2 (no client in the decision path) by adding the freshness contract the held row
is missing. Two complementary mechanisms; **both are required** — (a) keeps the row's content current,
(b) makes "current" mean producer-observed-now instead of venue-changed-long-ago:

- **(a) P3 actively RE-OBSERVES pinned candidate books at sub-1s cadence (closes the no-new-message gap).**
  Pinning a candidate into P3's universe today (the "Blocker #52" priority-token pin, `priority_token_ids`,
  market_channel_ingestor.py:375-412; populated at src/main.py:6027-6034) only guarantees a token is
  SUBSCRIBED and gets a row when its book CHANGES — it does NOT re-observe an idle book. So P3 adds a
  **pinned-candidate re-observation tick**: for every token in `priority_token_ids`, on a sub-1s loop,
  re-read the latest book it already holds (from the WS cache / `quote_cache`, market_channel_ingestor.py:277)
  and REWRITE `execution_feasibility_evidence` stamping a producer-observed timestamp = `now` even when
  `book_hash` is unchanged (a no-op-content, fresh-timestamp rewrite, content-dedup by `book_hash`
  per §2.3). This is the continuous-source content-hold pattern: the held row's freshness tracks the
  PRODUCER's observation clock, which never goes idle, instead of the venue's change clock, which does.

- **(b) Add a producer `captured_at` column and bound the runtime gate on IT (producer freshness), not on
  `quote_seen_at` (venue change-time).** `execution_feasibility_evidence` already carries `created_at`
  (producer write time, schema line 34) but the gate ignores it and bounds on `quote_seen_at`. Make the
  producer-observation clock first-class and authoritative:
  - add `captured_at TEXT NOT NULL` to `execution_feasibility_evidence`
    (src/state/schema/execution_feasibility_evidence_schema.py), stamped = wall-clock `now` by P3 on
    EVERY write (book-change write AND the (a) re-observation rewrite). This mirrors
    `executable_market_snapshots.captured_at`/`freshness_deadline` (executable_market_snapshot.py:178-179),
    which is the existing producer-freshness antibody.
  - change the runtime gate (src/main.py:5158-5174, the fallback branch; the age compute+raise at 5165-5174) to compute `row_age_ms = checked_at - captured_at`
    (producer observation freshness) instead of `checked_at - quote_seen_at` (venue change-time). KEEP
    `quote_seen_at` for what it correctly is — the venue book-change identity used for slippage/audit
    (`book_captured_at`, src/main.py:5200; the FOK crosses against exactly this book). Two distinct clocks
    with two distinct meanings: `captured_at` = "the producer saw this book this recently" (the freshness
    gate); `quote_seen_at` = "the venue last changed the book at" (the identity / change-detection stamp).
  - With (a)+(b), `captured_at` on a pinned idle candidate is always within the sub-1s re-observation
    interval of `now`, so the held row is ALWAYS within `max_quote_age_ms` for an active candidate — the
    `PRE_SUBMIT_BOOK_AUTHORITY_STALE` branch becomes unreachable for a pinned candidate (it stays as the
    fail-closed backstop for a genuinely-starved P3, which is now an OBSERVABLE producer-staleness condition
    — a stale `captured_at` — not an idle-book false-positive).

- **Becomes:** the held `execution_feasibility_evidence` row, with (a) re-observation + (b) `captured_at`
  gating, is the PRIMARY and is genuinely held-ready (producer-fresh sub-1s for any pinned candidate). The
  JIT provider is REMOVED — `book_quote_provider` is wired to `None` (the fallback path at src/main.py:5151-5175
  becomes the only path, now correctly producer-freshness-gated, so no stale leak AND no idle-book
  false-stale).
- **Migration M2 (REVISED — three coupled changes, all required, none sufficient alone):**
  1. **M2-a:** in P3's market-channel ingestor, add the pinned-candidate sub-1s re-observation tick that
     rewrites `execution_feasibility_evidence` for every `priority_token_ids` token with a fresh producer
     timestamp even when `book_hash` is unchanged (reusing the held book in `quote_cache`,
     market_channel_ingestor.py:277). This is what makes the row never go idle-stale.
  2. **M2-b (schema, see DB-M5 §5.5):** add `captured_at TEXT NOT NULL` to
     `execution_feasibility_evidence_schema.py`; P3 stamps it = `now` on every write; change the gate at
     src/main.py:5165-5174 to bound `row_age_ms` on `captured_at`, keeping `quote_seen_at` as the
     change-time identity (it still flows to `book_captured_at`, :5200, for slippage audit).
  3. **M2-c:** only AFTER M2-a + M2-b are live, stop wiring `_edli_pre_submit_jit_book_quote_provider()`
     at src/main.py:4296 (pass `None`). Do M2-c LAST — removing the JIT before the held row is
     producer-fresh re-introduces the GATE #84 fail-closed-on-idle pathology.
  - Keep the `max_quote_age_ms` gate (now on `captured_at`) as the antibody. Rollback: re-wire the JIT
    provider (M2-c revert) — M2-a/M2-b are strictly additive (a new column + a producer tick) and safe to
    leave in place. **Make-impossible:** after M2-c the reactor's submit closure no longer receives a CLOB
    client, so a future JIT `/book` in the decision path is un-writable; and because the gate bounds on
    producer `captured_at`, "an idle but valid book reads as stale" is no longer constructable.

**Sequencing dependency (load-bearing):** M2-a + M2-b MUST land and be verified (a pinned idle candidate's
`captured_at` stays sub-1s fresh under a no-book-change soak) BEFORE M2-c removes the JIT. This ordering is
the entire correctness of M2; it is reflected in §6.

### 3.4 Pre-submit collateral/allowance — LAZY_JIT -> held-ready read

- **Now:** `_cached_collateral_payload` (src/main.py:5125) does
  `clob._ensure_v2_adapter().get_collateral_payload()` (:5131) — a live HTTP the first time the balance
  check fires in a cycle (per-cycle cached only, consumed by `_edli_balance_allowance_status` :5180-5185).
- **Becomes:** a `collateral_warm` cycle (mirror `_edli_bankroll_warm_cycle`, :4410) polls
  `get_collateral_payload()` on the wallet's continuous clock (60s, content-hash dedup §2.3) and writes
  a held-ready `collateral_current` row (world.db, §5.3). `_edli_balance_allowance_status` (:5180) reads
  the held row, not a live client.
- **Migration M3:** add a `collateral_current` held-ready table (§5.3) + a `collateral_warm` cycle;
  repoint `_cached_collateral_payload` (:5125) to read the held row. Rollback: revert to the live fetch
  closure.

### 3.5 In-cycle bankroll `current(0.0)` — LAZY_JIT -> read the existing warm cache only

- **Now:** the reactor does `bankroll_provider.current(max_age_seconds=0.0)` at cycle start
  (src/main.py:4043) — a synchronous on-chain RPC INSIDE the cycle — even though
  `_edli_bankroll_warm_cycle` (:4410) already holds it warm and the per-event read uses `cached()`
  (300s window, :4415).
- **Becomes:** delete the in-cycle `current(0.0)` (:4043); the reactor reads `cached()` only. The warm
  cycle (60s) is the SOLE wallet fetcher. The 2026-05-31 comment (:4037-4042) reveals WHY the in-cycle
  fetch was added — `cached()` was returning None because the warm cycle's `_last_fetched_at` could age
  past 300s; the correct fix is to make the WARM cycle's interval < 300s and dedup by content (§2.3),
  not to fetch in the decision path. (This is exactly the "patch at the boundary" Fitz §1 warns against:
  the in-cycle fetch is a symptom-patch for an under-frequent warmer.)
- **Migration M4:** delete src/main.py:4043-4050; keep the bankroll warm interval below the 300s
  `cached()` window (e.g. 60s, already the case at :4410) so `cached()` never goes None. Rollback:
  restore the in-cycle `current(0.0)`.

### 3.6 Submit-time executable-snapshot recapture — LAZY_JIT -> never-triggers (held fresh)

- **Now:** `_recapture_fresh_entry_snapshot_if_needed` (src/execution/executor.py:1899) fires a live
  `capture_executable_market_snapshot(... clob ...)` (:1933, via `/book`) when the stored snapshot fails
  `is_fresh` at submit (:1914), fail-closed to legacy intent (:1945).
- **Becomes:** P2 substrate-observer holds `executable_market_snapshots` fresh enough
  (`freshness_deadline`, contracts/executable_market_snapshot.py:364; `FRESHNESS_WINDOW_DEFAULT=30s`,
  :27) that the recapture branch (:1914) is never entered for an active candidate. P2's warmer is scoped
  to pending families (system_decomposition_plan §4.1) — exactly the candidates that reach submit. The
  recapture path STAYS as a fail-closed last-resort (do not delete the antibody), but it becomes dead
  code in steady state. **Make-impossible** path: pass NO `clob` into the executor's submit path; if the
  snapshot is stale, FAIL the submit (and let P2's freshness sensor alert) rather than recapture inline
  — converting a silent in-path fetch into an observable stale-row condition.
- **Migration M5:** tighten P2's substrate-warm interval below the snapshot `freshness_deadline` for
  pending-family tokens so recapture never triggers; optionally gate the recapture `clob` arg behind a
  flag defaulting off in live (fail-closed instead of recapture). Rollback: re-enable inline recapture.

### 3.7 Producer-side per-candidate `market_info`/fee/`/book` HTTP — TTL-bound -> book-event-driven

- **Now:** inside the substrate capture loop, `_fetch_clob_market_info(clob, condition_id)`
  (market_scanner.py:2788, def :4214) pulls `/markets/{cid}` (tick_size/neg_risk/fee) and
  `_fetch_orderbook_snapshot` (:2801) / per-token `/book` (:3800) per candidate. Already chunked
  (`get_orderbook_snapshots` batch, :3805) and cache-deduped within a sweep (`clob_market_info_cache`,
  :2785), but the PRODUCER's refresh is TTL-bounded (300s, src/main.py:4174), not book-change-driven.
  This is producer-side, not in the decision path — but it makes the held row only as fresh as the 300s
  TTL.
- **Becomes:** SLOW-changing identity (tick_size/neg_risk/fee — change on market re-listing) is
  refreshed by P2 on the `market_events` topology clock (data-ingest writes `market_events`, I7), NOT
  per sweep; FAST-changing book/depth is driven by P3 off WS book-change events (§2.3), so
  `executable_market_snapshots` book columns are rewritten on release, not on a 300s timer. Two clocks,
  two producers, both held-ready.
- **Migration M6:** in P2, read tick_size/neg_risk/fee from the last `market_info` cached per
  condition_id keyed on the `market_events` topology version (refresh only when topology changes), and
  let P3's WS book-change rewrite the book columns. Rollback: revert to per-sweep `_fetch_clob_market_info`.

### 3.8 Substrate universe-sweep pending-gate — producer gated on consumer -> deleted by P2 lift

- **Now:** `_market_discovery_cycle` early-returns on the reactor's in-process state:
  `if _edli_reactor_active(): return` and `if pending_count>0 and recent_discovery: return`
  (system_decomposition_plan §0/§9 cites src/main.py:3632/3656; env
  `ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING` default `'1'`). A reactor backlog makes the producer
  go topology-only — the substrate goes stale exactly when trading is busiest (the zero-trade coupling).
- **Becomes:** P2 substrate-observer lift (system_decomposition_plan §8 Step 1) DELETES these gates — a
  separate process has no `pending_count` to read (this worktree already shows the lift in progress,
  src/main.py:72-79 comment: the warm interval + discovery lock "were lifted with the substrate
  producers to src.data.substrate_observer / src.ingest.substrate_observer_daemon"). The producer then
  fires on substrate staleness alone.
- **Migration M7:** = system_decomposition_plan §8 Step 1 (already underway here). No new work; this doc
  records it as the load-bearing structural fix the held-ready substrate depends on.

### 3.9 Summary: the decision path's reads after conversion

| Decision input | Held-ready table read (target) | Producer | Release clock |
|---|---|---|---|
| executable substrate (tick/negRisk/fee/depth) | `executable_market_snapshots` (trade.db) | P2 + P3 | topology (slow) + WS book-change (fast) |
| forecast posterior q/q_lcb/q_ucb | `forecast_posteriors` (forecasts.db) | forecast-live | calendar poll (00/12Z release) |
| readiness / baseline B0 | `readiness_state` / baseline rows (forecasts.db) | forecast-live | calendar poll |
| calibration (Platt A/B/C + pairs) | `platt_models`+`calibration_pairs` (co-located, §5.2-B) | data-ingest | daily refit |
| risk level | `risk_state` row (zeus-risk DB, §5.2-A) | riskguard | continuous tick |
| bankroll | `bankroll_provider` warm cache (in-proc, P1) | bankroll-warm | wallet poll 60s |
| collateral/allowance | `collateral_current` (world.db, NEW §5.3) | collateral-warm | wallet poll 60s |
| pre-submit book (selected candidate) | `execution_feasibility_evidence` (world.db), gated on producer `captured_at` (§3.3.1-b) | P3 (WS book-change write + sub-1s pinned-candidate re-observation rewrite, §3.3.1-a) | WS book-change (identity) + producer-observation heartbeat (freshness) |
| portfolio state | `position_current` (trade.db) | P1 own fills + P4 | continuous |
| settlement truth | `settlement_outcomes` (forecasts.db) | data-ingest writer | per resolution |
| mainstream point (display-only) | `_WARM_CACHE` (in-proc, P1; NOT a decision input) | mainstream-warm | OM hourly |

Every cell is a READ of a warm row. No cell is a network call in the decide/submit path.

---

## 4. Per-model `forecast_hours` (so no model 400-drops)

### 4.1 The fault, confirmed

`download_u0r_extra_raw_inputs(..., forecast_hours: int = 120, ...)`
(src/data/u0r_multimodel_download.py:517) passes `forecast_hours=120` UNCHANGED to EVERY model in the
loop (`for model in U0R_EXTRA_MODELS: ... single_fetch(..., forecast_hours=forecast_hours)`,
:540-547). `_default_live_fetch` puts it straight into the Open-Meteo query (`"forecast_hours":
forecast_hours`, u0r_multimodel_capture.py:179). For `icon_d2` (~48h horizon) and
`meteofrance_arome_france_hd` (~48h) — both enumerated at u0r_multimodel_capture.py:60-61 and
download.py:102-104 — Open-Meteo returns **HTTP 400** (horizon exceeds the model). The fail-soft
`except` (capture.py:194; download.py:550,572) catches it and DROPS the model — the ensemble silently
loses its two highest-resolution in-domain regionals and "succeeds". The fusion "handles missing
sources by construction" (download.py:25-28) so it never errors — it just trains on a quietly-incomplete
ensemble.

### 4.2 The fix — per-model horizon from the release calendar (reuse the existing field)

The release calendar ALREADY has the per-cycle horizon field: `cycle_profiles[].live_max_step_hours` /
`max_step_hours` (yaml:25-42; consumed by `evaluate_safe_fetch`'s `required_max_step_hours` check,
release_calendar.py:283-292). The ECMWF 06/18 short cycles already use it (`horizon_profile: short`,
yaml:34). So the fix is NOT a new constant — it is: add an `icon_d2` / `arome` calendar entry with
`live_max_step_hours: 48` (§2.2) and derive each model's `forecast_hours` from its calendar horizon:

```
forecast_hours_for(model, cycle) = min(
    requested_horizon_to_target,                       # how far out the target date is
    calendar_live_max_step_hours(model, cycle))        # the model's REAL horizon
```

Concretely:
- Replace the single `forecast_hours: int = 120` param (download.py:517) with a per-model lookup:
  `model_forecast_hours = _model_horizon_hours(model, cycle)` inside the `for model` loop (:540), passed
  to both `single_fetch` (:547) and the capture path. `_model_horizon_hours` reads
  `release_calendar.cycle_profile_for_hour(get_entry(model_source_id, track), cycle.hour).live_max_step_hours`.
- A static fallback map (when a model lacks a calendar entry) caps the known short-range models:
  `{"icon_d2": 48, "meteofrance_arome_france_hd": 48, "icon_seamless": 48}` (the same models already
  enumerated at capture.py:60-63, download.py:102-104), everything else `120`.

This makes the 400-drop **impossible**, not merely retried: a too-long horizon can no longer be
constructed because the request's `forecast_hours` is clamped to the model's real horizon before the
HTTP call. (Fitz §4: make the category impossible — the wrong request is un-sendable.)

### 4.3 Antibody (relationship test, write BEFORE the fix — Fitz "relationship tests → implementation")

Cross-module invariant: "for every model in `U0R_EXTRA_MODELS`, the `forecast_hours` passed to
`single_fetch` is `<= calendar_live_max_step_hours(model)`." Expressed as a pytest over the download
loop: assert no model in a captured cycle produces a `:single_runs` entry in the `dropped` list FOR a
horizon-400 reason; and assert the ensemble row count for icon_d2/arome is non-zero on a day those
models publish. This converts "the ensemble silently dropped a model" (a security-guard alert) into a
failing test (a stage-1 antibody), per Fitz Constraint #3.

- **Migration M8:** add the short-range horizon map + calendar entries; thread `_model_horizon_hours`
  through `download_u0r_extra_raw_inputs` (:509-547) and `_default_live_fetch` (capture.py:145-155).
  Rollback: restore the flat `forecast_hours=120`. Low risk — strictly widens the surviving ensemble.

---

## 5. The complete DB design

### 5.1 Current shape (ground truth)

- `state/zeus-world.db` (WORLD_CLASS, 117 tables): markets, positions lifecycle, `opportunity_events`,
  `platt_models`, `execution_feasibility_evidence`, EDLI ledgers (db_table_ownership.yaml:631+).
- `state/zeus-forecasts.db` (FORECAST_CLASS, 22 tables): `observations`, `settlement_outcomes`,
  `calibration_pairs`, `source_run`, `readiness_state`, `forecast_posteriors`, `raw_model_forecasts`,
  `market_events` (db_table_ownership.yaml:74-390).
- `state/zeus_trades.db` (TRADE_CLASS, 89 tables): `trade_decisions`, `execution_fact`,
  `position_current`, `venue_*`, `settlement_commands`, `executable_market_snapshots`
  (db_table_ownership.yaml metadata:44-50; cutover commit eba80d2b9d).
- `state/risk_state.db` — **de-facto 4th physical DB** (`RISK_DB_PATH`, src/state/db.py:58; lock order
  `risk_state.db < zeus-world.db < zeus_trades.db`, db.py:661). Holds the `risk_state` authority row
  `get_current_level()` reads (riskguard.py:1837; create at :1030-1043) — a HARD decision input outside
  the "three canonical" framing.

Two ownership defects that hurt the active-load goal:
1. **Calibration authority split across two DBs** — `platt_models` on world.db, `calibration_pairs` on
   forecasts.db — forces the reactor to ad-hoc `ATTACH forecasts` onto the world conn EVERY cycle
   (src/main.py:4018-4023) or every live decision fails `CALIBRATION_AUTHORITY_MISSING`. A held-ready
   read should not require an ATTACH dance each cycle.
2. **risk_state.db unnamed** — a hard input lives in a partition not in the canonical framing; the row
   has a 300s staleness floor (riskguard.py:1152,1159) that flaps to DATA_DEGRADED under producer
   starvation (the WAL contention P4 removes, system_decomposition_plan §contention_now_removed (1)).

### 5.2 KEEP-OR-CHANGE the 3-DB split: **KEEP the split, NAME the 4th, FIX the two ownership seams**

**Verdict: KEEP zeus-world / zeus-forecasts / zeus_trades. Do NOT churn the split.** The decomposition
removes the in-process WAL contention the split partly worked around (chain-sync→P4, substrate
pending-gate→P2, WS-ingestor→P3; system_decomposition_plan §contention_now_removed), but the split also
serves a STILL-VALID purpose post-decomposition: each program owns one physical DB's writes, so the
producer/consumer seams (I1–I7) are clean physical boundaries and cross-program write contention is
structurally bounded. Collapsing to one DB would re-introduce a single WAL all programs contend on — the
opposite of what the decomposition buys. The operator note ("originally over-designed, partly a
contention workaround") is true of the IN-PROCESS era; in the MULTI-PROCESS era the split is the right
shape because it matches the program boundaries. So: keep the 3, but make TWO targeted changes the
active-load design needs.

**Change A — fold `risk_state.db` into a NAMED canonical partition (`zeus-risk.db`).** Rather than a 4th
unnamed file, make it first-class. The registry already supports `risk_class` (db_table_ownership.yaml
schema comment, db.py:9). **Recommended (A1): NAME the existing file `zeus-risk.db`, declare it as the
4th canonical `risk_class` partition** in db_table_ownership.yaml and the "canonical DBs" framing — NO
data move. Keeping risk_state physically separate is GOOD for active-load: riskguard (a separate process)
owns its writes, P1 only reads, and the tiny single-row DB never contends with world/trade.
**Rejected (A2): moving risk_state into trade.db** — that re-introduces riskguard↔trade write contention,
the exact starvation P4 removes. So Change A is purely documentation/registry; it ends the "4th unnamed
DB" provenance trap (Fitz Constraint #4: every authority needs a named provenance).

**Change B — end the per-cycle calibration ATTACH by co-locating calibration authority.** The reactor
ATTACHes forecasts onto the world conn every cycle ONLY because `get_calibrator` reads `platt_models`
(world) + `calibration_pairs` (forecasts) together (src/main.py:4013-4017). Co-locate them: move
`calibration_pairs` to world.db alongside `platt_models` (both are CALIBRATION authority; they are read
TOGETHER, never apart). data-ingest writes both on its daily clock (I5); after the move it writes both to
ONE DB (world) — FEWER cross-DB writes, not more. Then the reactor reads calibration from the world conn
with NO ATTACH, removing a per-cycle setup cost AND a failure mode ("ATTACH failed →
CALIBRATION_AUTHORITY_MISSING", src/main.py:4022).

**Net target shape: 4 named canonical DBs** — `zeus-world` (world + BOTH calibration tables),
`zeus-forecasts` (forecast/observation/settlement), `zeus_trades` (trade/execution/substrate),
`zeus-risk` (the single risk_state authority row). The 3-DB split is KEPT; the unnamed 4th is NAMED;
calibration authority is UN-split.

### 5.3 Per-program ownership (writer = exactly one program per table)

| DB | Owner-writer program(s) | Held-ready interface tables (the seams §3.9) | Readers |
|---|---|---|---|
| `zeus-world` | P1 (markets/positions/EDLI ledgers); **data-ingest** (calibration, §5.2-B) | `platt_models`+`calibration_pairs` (co-located); `execution_feasibility_evidence` (written by **P3**); `collateral_current` (NEW, collateral-warm); `opportunity_events`/`opportunity_event_processing` | P1 reactor (calibration, feasibility, collateral, pending scope) |
| `zeus-forecasts` | **forecast-live** (`forecast_posteriors`, `readiness_state`, baseline, `raw_model_forecasts`); **data-ingest** (`observations`, `settlement_outcomes`, `market_events`) | `forecast_posteriors`, `readiness_state`, baseline B0, `settlement_outcomes`, `market_events` | P1 reactor (q/readiness/baseline), P4 (settlement), P2 (market_events topology RO) |
| `zeus_trades` | **P2** (`executable_market_snapshots`, `book_hash_transitions`); P1 (`trade_decisions`/`execution_fact`/`position_current`/`venue_*`); **P4** (`settlement_commands`, `chain_state`) | `executable_market_snapshots` (P2 identity cols + P3 book cols), `position_current`, `settlement_commands` | P1 reactor (substrate, portfolio), riskguard (positions/equity), P4 |
| `zeus-risk` (named §5.2-A) | **riskguard** | `risk_state` (single authority row) | P1 reactor (`get_current_level`, riskguard.py:1837) |

INV-37 still governs every cross-DB write: the sanctioned `get_forecasts_connection_with_world()`
(ATTACH+SAVEPOINT) / `trade_connection_with_world_flocked()` paths (AGENTS.md:52-54). The program split
moves WHICH process owns each cross-DB transaction; it does not relax the rule. Change B (co-locating
calibration on world) REDUCES cross-DB writes (calibration becomes intra-world).

### 5.4 Held-ready freshness/identity contracts per interface table

| Table | DB | freshness contract (existing or NEW) | identity contract |
|---|---|---|---|
| `executable_market_snapshots` | trade | `freshness_deadline` (`is_fresh`, executable_market_snapshot.py:364; default 30s :27) | `selected_token`, `raw_clob_market_info_hash` (market_scanner.py:2958), `book_hash` |
| `execution_feasibility_evidence` | world | **`captured_at` ≤ `max_quote_age_ms` at read (producer-observation freshness, REVISED §3.3.1-b — was `quote_seen_at`, the venue change-time, which ages unbounded on idle books)**; P3 re-stamps `captured_at=now` sub-1s for pinned candidates (§3.3.1-a) | `token_id`, `condition_id`, `book_hash_before` (the venue change-time identity, kept as `quote_seen_at`→`book_captured_at` for slippage audit, src/main.py:5200) (db_table_ownership.yaml:969-984) |
| `forecast_posteriors` | forecasts | `expires_at` (bundle_reader.py:468) + `source_cycle_time` ≤ 30h (bundle_reader.py:501-506) | `source_id`,`product_id`,`data_version`,`posterior_id`; readiness-dependency match (bundle_reader.py:508-513) |
| `readiness_state` | forecasts | `expires_at`, `source_cycle_time` | `readiness_id`, dependency roles |
| `raw_model_forecasts` | forecasts | `source_available_at`, `captured_at` | UNIQUE(model,product_id,request_url_hash,city,target_date,metric,source_cycle_time,endpoint) — the widened identity key (db_table_ownership.yaml:220-235) |
| `platt_models`+`calibration_pairs` | world (co-located §5.2-B) | daily refit clock; pin asserted at boot (src/main.py:249-322) | model pin shape + staleness |
| `risk_state` | zeus-risk | `checked_at` ≤ 300s (riskguard.py:1152) else DATA_DEGRADED | single authority row, `riskguard_degraded_reason` floor |
| `collateral_current` (NEW) | world | `captured_at + 60s` deadline | wallet address, `content_hash`=equity/allowance |
| `position_current` | trade | continuous (own fills) | position key |
| `settlement_outcomes` | forecasts | per-resolution (event-driven, no TTL) | (city,target_date,metric) settlement key |

### 5.5 DB migrations (each rollback-able, none churns the split)

- **DB-M1 (Change A):** declare `risk_state.db` → `zeus-risk.db` as a named canonical `risk_class`
  partition in `architecture/db_table_ownership.yaml` (schema_version bump) and the "canonical DBs"
  framing; no data move (`RISK_DB_PATH`, db.py:58, points at the named file). Rollback: revert the
  registry entry. Risk: LOW (doc/registry only).
- **DB-M2 (Change B):** move `calibration_pairs` forecasts.db → world.db (co-locate with `platt_models`);
  update data-ingest's writer target and `get_calibrator` to read both from world; delete the per-cycle
  `ATTACH forecasts` for calibration at src/main.py:4018-4023. Leave a `legacy_archived` ghost on
  forecasts.db (90d, matching the existing ghost pattern, db_table_ownership.yaml:393-491). Rollback:
  re-point the writer + restore the ATTACH. Risk: MEDIUM (touches calibration authority — gate behind
  the boot pin-shape assert, src/main.py:249-322, which already FATALs on a wrong-shape store).
- **DB-M3 (collateral held-ready):** add `collateral_current` table (world.db) + `collateral_warm` cycle
  (§3.4). Rollback: drop the table, revert `_cached_collateral_payload` to the live fetch. Risk: LOW
  (new table; reader fail-closes if absent).
- **DB-M4 (drop the long-dead ghosts on schedule):** the `legacy_archived` forecast/trade ghost shells
  (drop dates 2026-08-09 / 2026-08-15, db_table_ownership.yaml:31,497) are a same-name-on-two-DBs
  provenance trap; drop them on their scheduled dates so a held-ready reader can never resolve a stale
  ghost. Rollback: restore from backup (shells are empty, verified 0 rows db_table_ownership.yaml:495).
  Risk: LOW.
- **DB-M5 (producer-freshness column for the pre-submit book — enables the corrected M2, §3.3.1-b):**
  add `captured_at TEXT NOT NULL` to `execution_feasibility_evidence`
  (src/state/schema/execution_feasibility_evidence_schema.py CREATE_TABLE_SQL); P3 stamps it = wall-clock
  `now` on EVERY write (book-change write + the sub-1s re-observation rewrite M2-a). This is the missing
  producer-observation clock that makes the held row genuinely active-load-and-hold — without it the
  runtime gate has only `quote_seen_at` (venue change-time) to bound on, which ages unbounded on idle
  books. Mirrors `executable_market_snapshots.captured_at` (executable_market_snapshot.py:178). Backfill:
  existing rows set `captured_at = created_at` (the existing producer write-time, schema line 34) so old
  rows are not NULL. Rollback: revert the runtime gate to bound on `quote_seen_at` and drop the column
  (additive column; reader still works). Risk: LOW-MEDIUM (schema add + the one gate-clock change at
  src/main.py:5165-5174; the boot-time table ensure is idempotent).

The split is NOT churned: zeus-world / zeus-forecasts / zeus_trades keep their boundaries; the only moves
are NAMING the 4th (DB-M1, no data), UN-splitting calibration (DB-M2, one table to its co-owner), and two
NEW held-ready surfaces (DB-M3 collateral table; DB-M5 the `captured_at` producer-freshness column on the
existing feasibility table).

---

## 6. Migration order (load-bearing first, each independently rollback-able)

| # | Migration | Removes which violation | Risk | Rollback |
|---|---|---|---|---|
| M7 | P2 substrate-observer lift (= decomp §8 Step 1) | producer gated on consumer (§3.8) — the zero-trade root | MED | re-enable src.main registrations |
| M1 | U0R download → release-calendar poll | fixed 14h lag (§3.1) | MED | re-register the 14h cron |
| M8 | per-model `forecast_hours` cap | icon_d2/arome 400-drop (§4) | LOW | restore flat 120 |
| M2-a | P3 sub-1s pinned-candidate re-observation rewrite of `execution_feasibility_evidence` (§3.3.1-a) | held row goes idle-stale on unchanged books | MED | drop the re-observation tick |
| M2-b / DB-M5 | add `captured_at` col + bound the pre-submit gate on producer freshness, not venue change-time (§3.3.1-b) | wrong-clock freshness gate (§3.3) | LOW-MED | revert gate to `quote_seen_at`, drop col |
| M2-c | remove pre-submit `/book` JIT (wire `book_quote_provider=None`) — **ONLY after M2-a+M2-b verified** | network-in-submit-path (§3.3) | MED | re-wire JIT provider |
| M4 | in-cycle bankroll fetch removal | network-in-cycle (§3.5) | LOW | restore current(0.0) |
| M3 | collateral held-ready warm | network-in-submit-path (§3.4) | LOW | restore live fetch |
| M5 | submit recapture → fail-closed on stale | network-in-submit-path (§3.6) | LOW | re-enable inline recapture |
| M6 | producer per-candidate HTTP → book-event-driven | TTL-not-release producer (§3.7) | MED | revert to per-sweep fetch |
| DB-M1 | name risk_state.db (4th canonical) | unnamed hard-input DB (§5.2-A) | LOW | revert registry |
| DB-M2 | co-locate calibration on world | per-cycle ATTACH (§5.2-B) | MED | re-point + restore ATTACH |
| DB-M3 | collateral_current table | (enables M3) | LOW | drop table |
| DB-M4 | drop scheduled legacy ghosts | provenance trap (§5.1) | LOW | restore empty shells |
| DB-M5 | `captured_at` col on `execution_feasibility_evidence` | (enables M2-b/M2-c, §3.3.1-b) | LOW-MED | revert gate + drop col |

M7 first (it is the load-bearing structural fix the held-ready substrate depends on and is already
underway in this worktree). M1+M8 next (the forecast freshness + completeness). M2 (the pre-submit book)
is the one with a STRICT internal order: **M2-a (re-observation) + M2-b/DB-M5 (`captured_at` + gate-clock
change) MUST both land and be soak-verified BEFORE M2-c removes the JIT** — removing the JIT before the
held row is producer-fresh re-creates the GATE #84 fail-closed-on-idle pathology the JIT exists to mask
(§3.3). M3–M6 convert the remaining JIT sites. DB-M1..M4 are independent and can land any time; DB-M5 is
part of the M2 sequence.

---

## 7. Why this is the antibody, not 8 patches (closing)

The 8 lazy/late inputs collapse to **2 structural decisions** (Fitz §1, K«N): **D1** one generalized
release tracker (the existing `release_calendar` covering ALL sources, §2) replaces every ad-hoc clock
(14h cron, 300s TTL, on-demand fetch); **D2** the decision path is given held-ready READERS only, with no
CLOB/RPC client in scope, so a network call in decide/submit is un-writable (not lint-forbidden —
structurally impossible).

D2 has a **necessary precondition the adversarial review surfaced (the pre-submit book, §3.3):** removing
a JIT is only safe if the held row it falls back to is bounded on PRODUCER-observation freshness, not on
the SOURCE's change-time. The pre-submit book's `quote_seen_at` is the venue book-change instant
(market_channel_ingestor.py:236,265); bounding the 1s gate on it (src/main.py:5165-5174) means an idle
book reads as stale and fails closed — so naïve "delete the JIT, read the held row" is a fail-closed
regression, not a held-ready read. The corrected design adds the same `captured_at`/producer-freshness
contract `executable_market_snapshots` already enforces (executable_market_snapshot.py:178, 364) to
`execution_feasibility_evidence` (§3.3.1, DB-M5), plus a sub-1s producer re-observation heartbeat for
pinned candidates (§3.3.1-a). Only then does D2's "no client in scope" hold WITHOUT converting a working
JIT into a fail-closed stale-row. This generalizes the held-ready contract (§1.3): a freshness deadline
is only meaningful when it is bounded on the PRODUCER's clock — a source-change-time deadline silently
fails for any source that can go idle (slow/thin books, low-activity feeds).

The held-ready table contract (§1.3, `freshness_deadline`/`captured_at` enforced in the reader on the
PRODUCER clock) makes "stale at decision" an OBSERVABLE refused-row condition with a timestamp, never a
silent use, never a lazy refresh, and never an idle-source false-stale. The 3-DB split is KEPT (it now
matches program boundaries), the 4th DB is NAMED, and calibration is UN-split to kill the per-cycle
ATTACH. Every cross-DB write still obeys INV-37.

The category — "the runtime waits on data / trades on stale data / fails closed on a valid-but-idle
source" — is made unconstructable, per source, on each source's own release clock, with freshness always
bounded on the producer's observation time.
