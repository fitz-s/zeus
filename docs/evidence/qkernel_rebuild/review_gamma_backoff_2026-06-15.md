# Adversarial Review — FUTURE-NOT-LISTED WARM-BACKOFF (#122)

- **Date:** 2026-06-15
- **Reviewer:** code-reviewer (read-only)
- **Subject:** Uncommitted diff to `src/main.py`, branch `live/iteration-2026-06-13` (LIVE trading daemon)
- **Surface:** Tier-0-adjacent hot warm-capture loop `_refresh_pending_family_snapshots` (~3313-4130)
- **Verdict:** **SHIP** (no CRITICAL/HIGH at HIGH confidence; two LOW observations, neither blocking)

## What the diff does
Adds an evidence-keyed cooldown. A no-topology family whose Gamma `/events` slug
lookup returned an EMPTY event list is parked in module-global
`_GAMMA_EMPTY_BACKOFF_UNTIL: dict[tuple[str,str,str], float]` (family_key ->
monotonic deadline) for `ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS` (default 300s)
and not re-probed until the deadline passes. New diagnostic counter
`no_topology_backed_off` surfaced in the return summary.

Sites:
- Decl `src/main.py:102`
- `global` add `src/main.py:3439`
- Env parse `src/main.py:3490-3495`
- Check (skip) `src/main.py:3602-3608`
- Set (park) `src/main.py:3942-3951`
- Counter init/incr/return `src/main.py:3522 / 3607 / 4118`

---

## Hunt items (verdict + evidence)

### 1. KEY-TYPE MISMATCH — PASS (keys provably identical)
Both sides funnel through the SAME nested `_refresh_family_key` (`3392-3397`) and
both feed it the SAME already-canonicalized `(city, target_date, metric)` tuple:

- **Check-site (`3602`)**: `nb_key = _refresh_family_key(city, target_date, metric)`
  where `(city, target_date, metric)` is the loop var over `families`
  (`3538`). Each `families` tuple was built canonicalized at `3401-3405`
  (`_canonical_refresh_city_name` / `_canonical_refresh_metric`).
- **Set-site (`3729`)**: `family_key = _refresh_family_key(fam_city, fam_date, fam_metric)`
  where `(fam_city, fam_date, fam_metric)` iterates `gamma_refresh_families`
  (`3728`). Items were appended to `gamma_refresh_families` as the SAME
  canonicalized `(city, target_date, metric)` tuple at `3615`.
- `result["family_key"]` carried into `gamma_empty_family_keys` (`3818`) is
  exactly that `job["family_key"]` (set at `3758`), i.e. the same
  `_refresh_family_key(...)` output — never re-derived from the Gamma payload.

`_refresh_family_key` is **idempotent**: `_canonical_refresh_city_name` does an
alias->canonical map lookup whose VALUES are canonical names that map to
themselves (the map is built from canonical `.name` at `3370-3378`, and
`_refresh_family_text_key` normalization is stable under re-application);
`_canonical_refresh_metric("low"|"high")` returns the input unchanged. So
re-canonicalizing an already-canonical tuple is a no-op. Both sides yield an
identical `tuple[str,str,str]`. **The backoff matches; the fix is NOT inert.**

### 2. SCOPE / RESOLUTION — PASS
- `_GAMMA_EMPTY_BACKOFF_UNTIL` declared module-global `102`; `global` declaration
  at `3439` correctly lists it. Only the dict CONTENTS are mutated (`3951`
  item-set) and READ (`3605` `.get`) — the name is never rebound, so the
  `global` is technically sufficient even for the read; including it is correct
  and harmless.
- `_refresh_family_key` is a nested closure defined at `3392`, which is BEFORE
  both the check (`3602`) and set (`3729`) sites within the same function body —
  in scope at both. PASS.

### 3. WRONGLY-PARKED TRADEABLE FAMILY — PASS
Parking is sourced ONLY from `gamma_empty_family_keys` (`3950`), which is
populated ONLY in the `status == "empty"` branch of `_harvest_gamma_result`
(`3816-3818`) — i.e. a family that was PROBED and Gamma returned a parseable but
empty event list.
- Families that returned events go through the `else` branch (`3819-3824`) and
  are NEVER added to `gamma_empty_family_keys`. Not parked.
- `timebox_unattempted` (never probed) families are never harvested
  (`gamma_harvested_family_keys` gates that at `3809`), and a never-harvested
  family is never `status=="empty"`, so never parked — confirmed by the
  set being disjoint from the unattempted tail. They stay immediately retryable.
- `http_non_200` families increment `gamma_slug_http_non_200` (`3810-3811`) and
  are NOT added to the empty set. Not parked. (Correct: a transient 5xx must not
  trigger a 5-min cooldown.)
A family with a REAL listed market returns events => never parked. PASS.

### 4. PERMANENT DROP — PASS (non-terminal by construction)
A backed-off family `continue`s at `3608` BEFORE the
`gamma_refresh_families.append(...)` at `3615`, so it is simply absent from this
cycle's work lists. The warm refresh is a pure snapshot-capture / cache-warm path
(docstring `3320-3334`); it performs NO event-lifecycle write — no dead-letter,
no attempt-cap increment, no `EXECUTABLE_SNAPSHOT_BLOCKED`. The downstream
TERMINAL "stay at FDR gate" verdict (`4000-4014`) is reachable ONLY for families
present in `gamma_refresh_families` (loop at `3996`); a backed-off family is
absent from that loop, so it cannot be marked terminal. The family's pending
status is governed solely by the EventStore / gate path, untouched here. After
the cooldown expires, `_GAMMA_EMPTY_BACKOFF_UNTIL.get(nb_key) > monotonic()` is
False, the skip is bypassed, the family flows to Gamma again and is captured the
moment the market lists. Worst-case latency to pick up a newly-listed market is
≤ cooldown (300s default). PASS.

### 5. THREAD-SAFETY — PASS
- The dict is touched ONLY on the MAIN thread: read at `3605`, written at `3951`.
  The write at `3948-3951` executes AFTER the `with ThreadPoolExecutor(...)`
  block has fully exited (context manager closes at `3938`; the executor `with`
  opened at `3827`). Worker fn `_fetch_gamma_slug` (`3763-3777`) never references
  `_GAMMA_EMPTY_BACKOFF_UNTIL`. No cross-thread access.
- `_refresh_pending_family_snapshots` runs under the `edli_market_substrate_warm`
  APScheduler job registered with `max_instances=1, coalesce=True`
  (`9368-9376`) — single-instance, no overlapping warm cycle. This is the same
  single-instance assumption `_SUBSTRATE_REFRESH_CURSOR` already relies on. No
  race on the dict. PASS.

### 6. UNBOUNDED GROWTH — PASS (bounded, not a leak)
Keys are `_refresh_family_key` tuples of DISTINCT pending families (city ×
target_date × metric). The live universe is bounded (~hundreds; brief cites
14 cities × small date horizon × {low,high}). Stale entries are never pruned, but
the keyspace is naturally bounded and each value is a single float. Even over many
days, distinct future target_dates are a small finite set and re-setting an
existing key overwrites in place (`3951`), it does not accumulate. The dict resets
on process restart (decl comment `101`). Practically capped at low thousands of
8-tuple+float entries — sub-megabyte. Not a real leak. **LOW (non-blocking)** — a
trivial opportunistic prune of expired keys would keep it tidy, but absence of one
is not a defect at this scale.

### 7. INTERACTION with `_family_venue_closed` skip and `_SUBSTRATE_REFRESH_CURSOR` — PASS
- Order: `_family_venue_closed` skip (`3579-3583`) runs BEFORE the topology
  lookup and the new backoff skip (`3586+`). A venue-closed family `continue`s
  first and never reaches the backoff branch — no double-handling, no conflict.
- Cursor fairness: `families_processed_this_cycle` defaults to `len(families)`
  (`3534`) and is only reduced on a topology-deadline early break (`3545`). A
  backed-off family `continue`s but is NOT an early break, so it still counts as
  "processed" this cycle. The cursor advances by the full processed count
  (`3659-3661`), so round-robin fairness is preserved — parked families do not
  stall cursor advancement nor cause a slice to be re-swept or skipped. PASS.
- The topology-deadline early-break guard (`3539-3540`) keys off
  `cached_topology_markets or gamma_refresh_families`. A cycle of ALL-backed-off
  families leaves both empty, so no early break fires — the loop simply runs to
  completion over a cheap skip path (each iteration is a dict `.get` + topology
  row check, no Gamma call), which is exactly the intended un-clogging. PASS.

### 8. OTHER correctness / env-parse / off-by-one — PASS
- Env parse `3492-3495`: `max(0.0, float(os.environ.get(..., "300.0")))`. `0`
  cleanly disables (guarded by `_gamma_empty_backoff_s > 0.0` at BOTH check
  `3604` and set `3948`). A malformed env value raises `ValueError` — consistent
  with every other env parse in this function (e.g. `3483`, `3487`), which is the
  established fail-fast convention here; not a regression.
- Monotonic comparison `3605`: `_GAMMA_EMPTY_BACKOFF_UNTIL.get(nb_key, 0.0) >
  time.monotonic()`. Default `0.0` for an absent key is always `<= monotonic()`
  (process-uptime seconds, always > 0 in a running daemon) => unparked families
  fall through correctly. Strict `>` means at the exact deadline instant the
  family becomes eligible again — correct, no off-by-one (parking is a soft
  efficiency skip; a one-tick boundary difference is immaterial).
- Deadline uses `time.monotonic()` on both set (`3949`) and check (`3605`) — same
  clock, immune to wall-clock/NTP jumps. Correct choice for a duration cooldown.
- `time` is module-imported (`32`); `time.monotonic` used consistently with the
  rest of the function. No shadowing issue (`_time` alias at `2600` is unrelated
  scope).
- Diagnostic `no_topology_backed_off` correctly init/incr/returned
  (`3522/3607/4118`) for observability of how many families the cooldown saved
  per cycle. Good.
- `py_compile` + `ast.parse` clean; `pyflakes` clean over the changed region.
  (LSP `ty` server not installed in this env — substituted compile+pyflakes.)

---

## Positive observations
- Evidence-keyed (probed-AND-empty) parking is the precise minimal condition; it
  deliberately excludes `timebox_unattempted` and `http_non_200`, avoiding the
  two classic false-park traps. Well-scoped.
- Symmetric with the existing `_family_venue_closed` focus-skip pattern; same
  "never a terminal drop" discipline, same fail-soft default.
- Monotonic clock, single-thread mutation after executor close, `max_instances=1`
  job — the concurrency story is clean and matches the pre-existing
  `_SUBSTRATE_REFRESH_CURSOR` single-instance assumption.
- New diagnostic counter gives operators direct visibility into the fix's effect.

## Non-blocking LOW items (do not gate ship)
- **LOW** `_GAMMA_EMPTY_BACKOFF_UNTIL` is never pruned. Bounded by distinct
  pending families (~hundreds), so not a leak, but an opportunistic
  `del expired` sweep (or pruning inside the set loop) would keep it minimal.
- **LOW** A malformed `ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS` raises rather
  than falling back to default. Consistent with sibling env parses in this
  function, so acceptable, but a `try/except -> default` would be more
  forgiving for a hot live loop. Matches existing convention; flagging only for
  completeness.

## Recommendation
**SHIP.** No CRITICAL/HIGH at HIGH confidence. The four highest-risk failure
modes for this class of fix — key mismatch (inert fix), wrongly-parked tradeable
family, permanent drop of a listing family, and a data race on the shared dict —
are each provably absent. The two LOW items are cosmetic/robustness, not
correctness, and need not block deploy.
