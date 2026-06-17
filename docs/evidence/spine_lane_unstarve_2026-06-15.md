# Spine lane un-starve — the general "zero forecast orders" root, fixed in layers (2026-06-15)

Created: 2026-06-15
Last reused or audited: 2026-06-15
Authority basis: GOAL #83 (continuous settlement-graded positive-after-cost fills); task #118;
live daemon `live/iteration-2026-06-13`; qkernel spine flag ON.

## Symptom
Zero forecast/spine orders for the whole week. The rebuilt q-kernel spine
(`FamilyDecisionEngine.decide`) had produced **zero** live decisions (`q_source=qkernel_spine`
count = 0). The spine triggers ONLY on `FORECAST_SNAPSHOT_READY` / `EDLI_REDECISION_PENDING`
events. Ground truth: those events were **never reaching the decision core** — not a q-engine
problem, a **queue/claim starvation** problem with several compounding layers.

## The layers (each a real root, each fixed + deployed)

### L1 — day0 in NEITHER drain sweep → Tier-0 flood + throughput collapse  (commit c809434c5c)
`DAY0_EXTREME_UPDATED` was in neither `archive_expired_candidates` (FSR-only) nor the
token-keyed channel supersession. Stale + past-date day0 piled up: **1972 pending rows /
152 families; ~890 on SETTLED past dates**. day0 is Tier-0 in `fetch_pending`, so the pileup
was claimed ahead of tradeable FSR AND bloated the working set (slow `fetch_pending` =
the ~84-events/hr throughput collapse, `U`-state, apscheduler instance-skips).
Fix: generalize `archive_expired_candidates` to day0 + new `archive_superseded_day0_events`
(keep-latest per `city,target_date,metric`), wired into the always-on prune.
Verified: day0 pending **2006→135**; throughput restored (6228 day0 processed / ~18 min).

### L2 — day0 EMISSION firehose (live observation-instants scanner)  (commit 93853f6f07)
`Day0ExtremeUpdatedTrigger.scan_observation_instants_rows` emitted a day0 event for EVERY
eligible `(city,target_date,station) × {high,low}` on EVERY scan with **no change-gate**.
The GROUP BY recomputes `MAX(imported_at)` as `observation_available_at`, and the event
idempotency keys on `available_at`, so an UNCHANGED running extreme minted a brand-new event
every cycle — a ~350/min Tier-0 firehose that saturated the bounded per-cycle claim.
(`scan_authority_rows` already gates on monotonic advance; the live path did not.)
Fix: emit only when a family's running extreme strictly ADVANCES beyond the high-/low-water
mark of its already-emitted day0 events (cross-cycle via persisted events; fail-soft).
Verified: `day0_observation_instants_emitted` **hundreds → 0–1 per cycle**.

### L3 — day0 Tier-0 count ≥ claim limit → FSR truncated off the claim  (commit <this>)
Even with L1+L2, FSR was still **0 claimed** (15312 forward FSR `attempt_count=0`). Cause:
`fetch_pending` orders **all Tier-0 day0 before any Tier-1 FSR**, then truncates
`admissible[:limit]` (reactor `limit=100`). day0 pending = **115** (91 current 06-15 +
**24 settled past-date 06-14 stragglers**) > 100 → day0 fills 100% of every claim batch,
FSR truncated off. NOT throughput (day0 was only ~13 processed/10min by then — spare capacity).
The 24 settled 06-14 day0 were spared by the Oceania `-1`-day frontier MARGIN in the expiry
sweep. That margin is for FSR (target can be a future trading day, ambiguous across tz); a
day0 is a SAME-DAY signal with no forward ambiguity, so the margin only strands yesterday's
settled day0 in the band.
Fix: day0 expiry candidate band widened to today-inclusive (`< Oceania_date + 1`); the exact
per-city `_strictly_past_in_tz` check still keeps any day0 whose local day is still open.
FSR margin unchanged. Expected: day0 → ~91 < 100 → FSR finally gets ~9 claim slots/cycle.

## Tests (all green)
`tests/events/test_archive_day0_events_swept.py` (past-date expiry, FSR-expiry regression,
supersession keep-latest, family independence, fail-closed, idempotent, **frontier-band
sweep + FSR-margin-preserved**, processing-status index plan);
`tests/events/test_day0_extreme_updated_trigger.py` (change-gate suppress-unchanged +
emit-on-advance). Full `tests/events/` suite: 502 passed. 3 pre-existing
`test_finding_b_free_cash_bound.py` failures confirmed unrelated (clean-HEAD repro).

## Remaining blocker stack (BELOW the claim — only reachable once FSR claims)
Once forecast families reach the decision core, the next gates observed on the day0 lane
(which will also apply to the spine path) are:
1. `executable_forecast_reader` **MISSING_EXPECTED_MEMBERS** (member-floor 40/51 — incomplete
   ensemble for some families). Task #70.
2. `REPLACEMENT_FORECAST_LIVE_DIRECTION_PROOF_MISSING`.
3. `DAY0_ORACLE_ANOMALY_PAUSED:Tokyo` still firing (clean-verdict-clears didn't clear Tokyo).
4. The spine's OWN decision: `NO_POSITIVE_EDGE_CANDIDATE` vs a real fill — the actual
   alpha question (does the rebuilt vector q clear price+cost on a tradeable class).

## L4 — THE binding constraint (current): decision latency × budget × day0 monopoly
Even with L1–L3, FSR stays 0-claimed. `fetch_pending` orders all Tier-0 day0 before any
Tier-1 FSR; `process_pending` runs a **45s per-cycle wall-clock budget**
(`ZEUS_REACTOR_CYCLE_BUDGET_SECONDS`) and a single 22-candidate family decision runs
**p99=59s, max=460s** (reactor.py:633 comment) — so only **~3–4 decisions complete per
cycle**, 100% consumed by Tier-0 day0 (~92–104 families; 46 cities × {high,low}). day0
currently produces ZERO orders (capital_efficiency / MISSING_EXPECTED_MEMBERS), so the entire
budget is spent on a non-productive lane while the spine lane gets nothing. Raising the fetch
limit does nothing — it's budget-bound, not fetch-bound. day0 dropping 115→104 (L3) cannot
cross below ~92, which already ≈ a 45s budget's throughput.

Latency signal: **417 live `clob.polymarket.com/book` fetches per ~2000 log lines**, ~0.67s
each, back-to-back — ~22 sequential live book fetches per family decision is the dominant
latency. Open question (needs a path trace): is that the per-candidate JIT proof-gen fetch
(then **parallelizing the 22 fetches** cuts ~15s→~2s with no law change — high-leverage), or
the background substrate-warm snapshot refresh (separate job)? The K=1 fresh-book-is-the-
decision-authority law (task #39) may REQUIRE a fresh per-candidate fetch, so the safe speedup
is concurrency, not caching.

## Two L4 fix directions (operator's call — touches event_priority + K=1 law)
A. **Reduce day0's budget monopoly** (claim-priority change): reserve an FSR slice, interleave
   day0/FSR per-city, or fast-path the non-productive day0. All de-prioritize day0 somewhat —
   inherent, since under a ~4-decision budget any strict day0-first ordering starves FSR.
B. **Speed the decision** (latency): parallelize the per-candidate book fetches so the 45s
   budget covers many more families and reaches FSR naturally — helps BOTH lanes, no priority
   change, but touches the network/txn discipline and must preserve the K=1 fresh-book law.

## L5 — RESOLVED: fair cross-lane interleave → THE SPINE RUNS (commit reactor interleave)
`_fair_lane_interleave` (reactor.py) round-robins the forecast-decision lane against day0 1:1
within the per-cycle budget. Deployed + restarted → **fsr_claimed=2, fsr_processed=2** (0 all
week), and the rebuilt spine returned real reasons (`QKERNEL_SPINE_NO_TRADE:...`). The 4-layer
queue/claim starvation (L1–L4) is fully cleared; the rebuilt q-kernel spine is on the live
decision path for the first time.

## L6 — current frontier: spine runs but gets SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED
Sub-typed the reason (commit) → **universal `MU_SIGMA_NOT_STASHED`** (7/7 spine decisions over
12 min; zero real price decisions). Traced:
- The Stage-0 producer (`_market_analysis_from_event_snapshot`, event_reactor_adapter.py:11190)
  stashes `_edli_spine_mu/sigma_native` onto the SAME threaded payload the seam reads (verified
  caller @10687); no early return bypasses it.
- `ensemble_snapshots` (zeus-forecasts.db) HAS members for every current target (06-15:
  1372/1372, 06-16: 1156/1156) — so this is NOT a raw-supply gap.
- Therefore: `_spine_mu_native` is None (the EMOS @11307 / honest-raw @11355 belief branch did
  NOT run for these families) AND the corrected member array `members` is empty at the producer
  (the empirical fallback @11547 can't fire). The served predictive center is never computed.
- Tied to the live `REPLACEMENT_FORECAST_LIVE_DIRECTION_PROOF_MISSING` cycle reason: the
  replacement/EMOS belief isn't producing mu/sigma for the FSR families reaching the spine.

NEXT (task #119): the forecast BELIEF computation — why EMOS/replacement does not produce
mu/sigma and why the corrected `members` array is empty for live FSR families despite raw
ensemble members existing. This is the forecast-belief-computation layer (tasks #97/#98/#70),
now the live frontier because the spine finally runs and reaches it. The fix lets the spine
price → the path to a real settlement-graded fill.

## Policy lever flagged for the operator
day0 (Tier-0) genuinely has ~92 families (46 cities × {high,low}). With claim `limit=100`,
day0 nearly fills the claim every cycle, leaving FSR only the residual (~8–9/cycle). The L3
fix makes FSR non-zero but MARGINAL. If day0 stays non-productive (capital_efficiency /
MISSING_EXPECTED_MEMBERS) while occupying Tier-0, the spine lane stays throttled. Cleaner
levers (operator's call — touches the operator-designed `event_priority` Tier-0 policy):
raise the per-cycle claim limit, or interleave day0/FSR per-city across tiers so each city's
freshest decision-trigger gets a slot rather than draining all day0 before any FSR.
