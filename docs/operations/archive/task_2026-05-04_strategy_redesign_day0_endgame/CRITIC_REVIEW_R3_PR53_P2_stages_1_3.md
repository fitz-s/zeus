# Critic Review R3 — PR #53 P2 stages 1-3 (MarketPhase axis A)

HEAD: fac4a9a61e59d90239c6646cdb0034f3e8922d80
Reviewer: critic-opus
Date: 2026-05-04
Branch: strategy-day0-redesign-p2-2026-05-04
Base: e62710e6 (post PR #51 merge)

## Subject

P2 stages 1-3 of PLAN_v3 §6.P2: `MarketPhase` enum + adapter helpers (`src/strategy/market_phase.py`), candidate/decision plumbing in `cycle_runtime.execute_discovery_phase`, and `probability_trace_fact.market_phase` column + writer + index. 4 commits (6bdea5f7, a2411173, ce4c49b7, fac4a9a6); 1043 LOC across 6 files; 25/25 new tests pass on system python.

## Verdict

**APPROVED-WITH-CAVEATS**

## BOTTOM LINE

The PR is structurally sound for a tag-only / observability-only delivery: candidate gets a phase, decision gets a phase, DB column persists it. Tests cover T1, T3, T4, naive-tz guard, str-Enum serialization, DST anchor, legacy-DB ALTER path, and ON CONFLICT idempotence. **The blocking finding is ATTACK 8: the production `market` dict from `_parse_event` does NOT contain `market_end_at`/`endDate`/`end_date`, so `market_phase_from_market_dict` always falls through to the F1 12:00-UTC fallback. The "fail-loud on naive Gamma payload" test is unreachable in production today.** Two MED findings: ATTACK 6 (`_require_utc` does not actually enforce `timezone.utc` — it accepts any zero-offset zone including `Europe/London` in winter and `America/Iceland` year-round), and ATTACK 5 (per-market multi-position SAVEPOINT atomicity, which PLAN_v3 §0.1 amendment 5 explicitly placed inside P2, was deferred without a recorded plan-amendment). Neither blocks the tag-only delivery; both must land before P3 mode→phase migration can flip dispatch on the new tag.

---

## ATTACK 1 [VERDICT: PASS-WITH-CAVEAT] Spec-vs-implementation drift (severity LOW)

PLAN_v3 §6.P2 stage-by-stage:
- Define MarketPhase enum (5 values) — DELIVERED at `src/strategy/market_phase.py:44-56` with all 5 values verbatim.
- `market_phase_for_decision(market, decision_time, city)` helper — DELIVERED at `:114-163`.
- Plumb decision_time through every phase-derivation site — DELIVERED at `cycle_runtime.py:2139-2144` (single site).
- Tag every Decision/EdgeDecision/candidate snapshot — DELIVERED on MarketCandidate (`evaluator.py:183`), EdgeDecision (`evaluator.py:222`), and at decision-stamp site (`cycle_runtime.py:2194-2197`).
- `decision_chain.market_phase` NEW column — NOT delivered. PR adds the column to `probability_trace_fact` instead. PLAN_v3 §6.P2 explicitly named `decision_chain.market_phase` as the new column ("`decision_chain.market_phase` — NEW column added by P2"). The PR ships it on `probability_trace_fact`. This is a spec-vs-implementation table-name drift.
- Per-market multi-position SAVEPOINT atomicity — NOT delivered (see ATTACK 5).

Severity LOW because `probability_trace_fact` is the right table for *cohort attribution* (PLAN_v3 §6.P9 framing), and the PLAN body itself is internally inconsistent: §6.P2 names `decision_chain.market_phase`, §6.P9 names `probability_trace_fact` cohorts. The PR picks the latter, which is consistent with the §6.P9 reporting use case, but **the PLAN should be reconciled** so the next agent doesn't re-add `decision_chain.market_phase`. **Remediation**: edit `PLAN_v2.md:368` ("decision_chain.market_phase — NEW column added by P2") to read "probability_trace_fact.market_phase — NEW column added by P2 (chosen for §6.P9 cohort attribution; decision_chain stays untouched)".

## ATTACK 2 [VERDICT: PASS] Boundary semantics drift (severity LOW)

The reformulation in commit ce4c49b7 anchors `settlement_day_entry_utc` at **city-local 00:00 of `target_local_date`**.

Operator framing: "当地市场 0 点前的 24 个小时" literal parse: "the 24 hours before the local market's 0 o'clock (midnight)". The phrase "0 点" disambiguates which midnight: the natural reading in Chinese trading parlance is end-of-trading-day = end-of-target-day = 24:00 of target_date = 00:00 of target_date+1. Therefore "0 点前的 24 个小时" = the 24 hours preceding the END of target_date = the local target_date itself, anchored at start-of-target_date local 00:00.

Both old (`end - 24h`) and new (`start of target_local_date`) formulations give the same UTC instant on **non-DST days**. They diverge by ±1h on DST-transition target dates. On 2026-03-29 London spring-forward (verified with python repro):
- OLD formula: 2026-03-28 23:00 UTC (anchored at end-of-target which is already in BST, then subtracts 24h)
- NEW formula: 2026-03-29 00:00 UTC (anchored at start-of-target which is still in GMT)

The NEW formula treats "the local target_date day" as a 23h-or-25h interval in UTC on DST days, which is the *correct* local-calendar geometry. The OLD formula silently shifted the boundary by 1h on every DST day. The fix went the right way; PLAN_v2 line 124-125 wording ("city-local end-of-target_date − 24h") was the looser approximation, the new code is the precise version. PASS.

**Caveat**: PLAN_v2 §2 line 124 still says `lead_hours_to_settlement_close() ≤ 24` (= "24h before end-of-target") as the boundary anchor. This is now spec-vs-code drift the OPPOSITE direction from the v3 fix. Remediation: edit `PLAN_v2.md:124` to read "at city-local 00:00 of `target_local_date` (start-of-target_date local)".

## ATTACK 3 [VERDICT: PASS-WITH-CAVEAT] T2 invariant claim (severity LOW)

PLAN_v3 §8 enumerates T1-T6 as the merge-floor for P2. The PR delivers T1, T3, T4 as standalone helper tests + a stage-3 persistence T-suite (8 additional tests). T2 ("phase ↔ LifecyclePhase consistency") is explicitly deferred to "later commit when daemon writers tag positions" per `tests/test_market_phase.py:14-17`.

Defensibility: T2 is a CROSS-AXIS invariant — it asserts that every active position whose market is `MarketPhase.SETTLEMENT_DAY` has `LifecyclePhase ∈ {DAY0_WINDOW, PENDING_EXIT}`. Today the PR plumbs MarketPhase as a *tag on candidates and decisions only* — there is NO write to `position_current.phase` from MarketPhase, and no read of `position_current.phase` against MarketPhase. T2 cannot land until D-B (P3 mode→phase migration) actually couples `LifecyclePhase` to `MarketPhase` semantics. The deferral is honest.

**Caveat**: PLAN_v2.md §0.1 amendment 5 (line 30) says "**§8 adds T7 invariant test for no partial-write**", but §8 body (lines 553-568) only enumerates T1-T6. The plan is internally inconsistent: amendment claims T7 added, body never adds it. **Remediation**: either insert T7 in §8 (no-partial-write SAVEPOINT atomicity test) or remove the T7 promise from §0.1 amendment 5. Recommend insert; the test is structurally needed for ATTACK 5.

## ATTACK 4 [VERDICT: PASS] ON CONFLICT migration safety (severity LOW)

Verified `trace_id = f"probtrace:{decision_id}"` at `src/state/db.py:3482`. trace_id is deterministic from decision_id, so re-writes of the same decision_id produce the same trace_id and the `ON CONFLICT(trace_id) DO UPDATE` path correctly updates `market_phase=excluded.market_phase`. Legacy DB rows have NULL `market_phase` after the ALTER; subsequent post-stage-3 writes will overwrite NULL with the actual phase. Reproduced legacy + upsert path in REPL — works as designed.

`tests/test_market_phase_persistence.py::test_writer_idempotent_upsert_preserves_phase` covers the same-decision_id re-write case (PRE_SETTLEMENT_DAY → SETTLEMENT_DAY through one upsert). PASS.

## ATTACK 5 [VERDICT: FAIL] R2 A14 SAVEPOINT atomicity (severity MED)

PLAN_v3 §0.1 amendment 5 (line 30): "**A14 fix** — §6 P2 specifies atomic per-market multi-position phase transition wrapped in single SAVEPOINT; §8 adds T7 invariant test for no partial-write." PLAN_v2 §6.P2 lines 371-376 reiterate: "Per-market multi-position write atomicity (per critic R2 A14) ... wrapped in single SAVEPOINT".

The PR delivers ZERO SAVEPOINT additions (`git diff e62710e6..HEAD -- src/ | grep -i savepoint` returns empty). The PR description's `What is NOT in this PR` section says "P2 stage 4 SAVEPOINT atomicity — depends on P3 transition mechanism, deferred". This is reasonable engineering — until D-B / P3 actually flips dispatch on `market_phase`, there are no per-market multi-position TRANSITIONS to atomicize, only candidate-time TAGS. But the PLAN explicitly placed the SAVEPOINT inside P2.

Severity MED, not HIGH, because:
1. Today, candidate tagging is read-only with respect to `position_current` (the table A14 worried about). No transitional write to position rows happens from this PR.
2. The actual A14 risk surface (multi-position phase transition) only materializes when P3 lands and uses `market_phase` for dispatch.

Severity MED, not LOW, because:
1. PLAN_v3 §0.1 lists this as a *delivered amendment*, but it is not delivered. This is documentation drift that future agents will trip over.
2. The deferral was not recorded as a plan-amendment in the PR's commit message or in any new doc.

**Remediation**: add a §6.P2-defer note to PLAN_v2.md (or open a P2-stage-4 stub doc) recording that SAVEPOINT atomicity moves to P3 because the multi-position transition only happens when dispatch flips. Add T7 stub to §8. Add an explicit "P2 stage 4: SAVEPOINT-wrapped per-market transition (lands with P3)" in §6.

## ATTACK 6 [VERDICT: FAIL] UTC strict validation (severity MED)

`_require_utc` at `src/strategy/market_phase.py:59-81` rejects on `value.utcoffset() != timedelta(0)`. The function name and docstring claim it enforces UTC. **Verified in REPL that the following all pass `_require_utc`**:
- `datetime.now(timezone.utc)` — pass (intended)
- `datetime.now(ZoneInfo("UTC"))` — pass (intended)
- `datetime.now(timezone(timedelta(0)))` — pass (intended-ish)
- `datetime.now(timezone(timedelta(0), "GMT"))` — pass (NOT UTC label)
- `datetime(2026, 1, 15, tzinfo=ZoneInfo("Europe/London"))` — **pass** (London winter is GMT, offset 0)
- `datetime(2026, 6, 15, tzinfo=ZoneInfo("Atlantic/Reykjavik"))` — pass (Iceland is UTC year-round)

The check is correctly named "zero-offset" but documented and tested as "UTC". The Copilot comment 3179339283 fix was about rejecting `America/Chicago`; it succeeded at that. But it does NOT make `tzinfo == timezone.utc` an invariant.

Practical impact today: ZERO. Production calls `_require_utc` with `decision_time = datetime.now(timezone.utc)` from `cycle_runner.py:401` (`_utcnow() = datetime.now(timezone.utc)`). The Gamma adapter normalizes via `astimezone(timezone.utc)` at `market_phase.py:186`, also producing `tzinfo == timezone.utc`. No production caller would slip a London-winter datetime in.

**But** the function's named guarantee is stronger than what it enforces. A future agent (per Fitz Constraint #2 — design intent survives at ~20%) could add a caller that passes `datetime(...).astimezone(ZoneInfo("Europe/London"))` and find it accepted, reasoning "the function is named `_require_utc`, so passing must mean UTC".

**Remediation**: edit `src/strategy/market_phase.py:74` from
```python
if value.utcoffset() != timedelta(0):
```
to
```python
if value.tzinfo is not timezone.utc and value.tzinfo != timezone.utc:
    # Allow timezone.utc and equivalent fixed-offset(0) constructors.
    # Reject zero-offset DST zones (Europe/London winter, Atlantic/Reykjavik)
    # because the SAME zone could be non-zero offset in a different month.
    raise ValueError(...)
```
or, more cheaply, rename `_require_utc` → `_require_zero_utc_offset` and update the docstring to make the looser semantics explicit.

## ATTACK 7 [VERDICT: PASS] Stamp asymmetry (severity LOW)

cycle_runtime stamps the str-form `.value` on every decision (`cycle_runtime.py:2195`); candidate keeps the enum (`evaluator.py:183`). With str-Enum (post-fix), `MarketPhase.SETTLEMENT_DAY == "settlement_day"` is True. The writer at `db.py:3408-3415` reads `decision.market_phase` first, fallback `candidate.market_phase`. Verified: `decision_phase.value if hasattr(decision_phase, "value")` collapses str-Enum to "settlement_day"; `candidate_phase.value` collapses MarketPhase to "settlement_day". Same string lands in DB regardless of which side carries the tag. PASS.

Direct EdgeDecision construction sites (~30 in `evaluator.py:1433-2039+`) all use the dataclass default `market_phase: Optional[str] = None`. Without the cycle_runtime stamp loop they remain None. cycle_runtime's stamp loop is the only path that fills it, gated by `candidate.market_phase is not None`. So an evaluator-direct caller (test fixture, off-cycle path) gets None on every decision, matching the documented contract. PASS.

## ATTACK 8 [VERDICT: FAIL] F1 fallback hazard (severity HIGH)

This is the blocking finding. `market_phase_from_market_dict` at `market_phase.py:198-233` reads:
```python
end_str = market.get("market_end_at") or market.get("endDate") or market.get("end_date")
polymarket_end_utc = (
    _parse_utc(end_str) if end_str else _f1_fallback_end_utc(target_local_date)
)
```

The production `market` dict comes from `src/data/market_scanner._parse_event` (`market_scanner.py:991-1098`). The dict it returns contains exactly these keys: `event_id, slug, title, city, target_date, temperature_metric, hours_to_resolution, hours_since_open, outcomes, condition_ids, support_topology, resolution_source, resolution_sources, source_contract`. **None of `market_end_at`, `endDate`, `end_date`** appear.

The `market_end_at` field IS produced — but on a DEEPER path: `_market_outcome_facts` puts it on each *outcome* dict, not on the parent market dict. cycle_runtime calls the adapter with the parent market dict (`market_phase_from_market_dict(market=market, ...)`), so the adapter sees no end-time.

Effect: in production, **every single candidate gets a phase computed against `_f1_fallback_end_utc = 12:00 UTC of target_date`**. The "loud failure" test `test_adapter_naive_gamma_payload_is_loud_failure` is unreachable from the production code path — there is no Gamma payload datetime to be naive about because the adapter never sees one.

Severity HIGH because:
1. The PLAN's ATTACK 1 of CRITIC_REVIEW_R2 (A1) verified F1 across 13 cities and stamped it conformant — but the PLAN's design intent was that F1 is the *fallback*, not the primary path. Production now has F1 as the *only* path.
2. If F1 ever fails for one of the 51 cities (e.g., a city whose market settles at a non-12:00-UTC time due to a Polymarket schedule deviation), the silent-misphase risk is full population.
3. The fail-loud branch (per `test_adapter_naive_gamma_payload_is_loud_failure`) gives PLAN authors and reviewers false comfort. They believe Gamma payload errors will surface. They will not.

Severity is not blocker-for-revert because:
1. Today, F1 IS uniformly correct across the 13 verified cities. The phase tag will be right for those cities.
2. MarketPhase is currently a tag, not a hard gate. Wrong tag does not change live trading behavior.
3. The fix is mechanical: thread `market_end_at` from outcome → parent market in `_parse_event`, OR have cycle_runtime read the outcome and pass `market_end_at` explicitly to the adapter.

**Remediation (preferred)**: edit `src/data/market_scanner.py:1068-1098` (the dict returned by `_parse_event`) to add:
```python
"market_end_at": event.get("endDate") or event.get("end_date"),
"market_start_at": event.get("startDate") or event.get("start_date"),
```
This makes the production dict carry the explicit Gamma values. The F1 fallback then becomes an actual fallback (only triggers when Gamma omits both).

Add a relationship test: `tests/test_market_phase.py::test_adapter_uses_explicit_end_in_production_dict` that constructs a dict via the same shape `_parse_event` returns and asserts the explicit endDate is honored, not the F1 fallback.

## ATTACK 9 [VERDICT: PASS-WITH-CAVEAT] Index cost (severity LOW)

`idx_probability_trace_market_phase` is added at `db.py:1444-1447`. The PR has zero current consumers querying `WHERE market_phase = ?` (`grep -rn "market_phase" src/` returns only producer code in `evaluator.py`, `cycle_runtime.py`, `db.py`, and the helper module). PLAN_v3 §6.P9 commits to (strategy_key, market_phase) cohort SQL but P9 is later in the sequence.

Write-cost: probability_trace_fact gets one row per candidate per cycle. At full ramp 51 cities × ~5 candidates × ~96 cycles/day = ~25k writes/day; SQLite single-column TEXT index write cost is ~negligible at this volume. Read-without-index cost would be linear scan of the table. The pre-emptive index is defensible.

**Caveat**: PLAN_v3 §6.P9 commits to phase-cohort reporting. Until that lands, this index has no read consumer. **Remediation**: add a TODO comment at `db.py:1444` referencing `PLAN_v3 §6.P9` so a future cleanup pass doesn't drop the index assuming it's dead.

## ATTACK 10 [VERDICT: PASS-WITH-CAVEAT] Plan-vs-PR boundary drift (severity LOW)

Spot-checking 3 R2 amendments:
- **A14 (per-market multi-position SAVEPOINT atomicity)** — PLAN-v3 §0.1 amendment 5 promises delivery in P2; PR does not deliver. See ATTACK 5. FAIL on this amendment.
- **C10 (per-table phase handling)** — PLAN-v3 §0.1 amendment 1 says "P2 now enumerates `position_lots` (NO phase column needed; separate state enum axis) and `position_events` (already has `phase_after`; P2 does not change it)". PLAN_v2.md:362-369 documents this in §6 P2. The PR DOES respect this: it only writes `market_phase` to `probability_trace_fact`, never to `position_lots` or `position_events`. PASS.
- **A6 (Kelly multiplier post-calibration)** — relevant to P5, not P2. Not testable in this PR.
- **A9 (P5 multiplier storage)** — relevant to P5, not P2. Not testable in this PR.
- **C6 (5+1 sites)** — relevant to P3, not P2. Not testable in this PR.

PR amendments to ATTACK-drift not flagged in PLAN: the PR added a 4th commit (ce4c49b7) addressing PR Copilot comments. The DST anchor reformulation (str-Enum, strict UTC, snake_case rename, local-start-of-target_date) is a real improvement OVER the spec — it caught a DST bug the PLAN's "24h before end-of-target" wording would have shipped. This is healthy plan-amendment-from-implementation-discovery; it should be back-propagated to PLAN_v2.md:124 (see ATTACK 2 caveat).

PASS-with-caveat. The drift on A14 (ATTACK 5) is the only real plan-vs-PR mismatch.

---

## Required fixes

Ordered by severity:

### HIGH — blocking for honest "F1 only as fallback" semantics
1. **ATTACK 8 fix**: `src/data/market_scanner.py:1068-1098` (the dict returned by `_parse_event`) — add `"market_end_at": event.get("endDate") or event.get("end_date"),` and `"market_start_at": event.get("startDate") or event.get("start_date"),`. Add a test in `tests/test_market_phase.py` asserting an explicit endDate dict is honored, not silently ignored. Without this fix, F1 is the *only* production path, not a fallback.

### MED — must land before P3 dispatch flip
2. **ATTACK 5 fix**: amend PLAN_v2.md (or the PR follow-up) to record SAVEPOINT atomicity defer-from-P2-to-P3 + add T7 to §8. Without this, future agents reading §0.1 amendment 5 will believe SAVEPOINT atomicity is delivered.
3. **ATTACK 6 fix**: rename `_require_utc` → `_require_zero_utc_offset` and update docstring, OR tighten the check to compare `tzinfo is timezone.utc`. Current check accepts London winter, Iceland year-round. No production exposure today, but the named guarantee is stronger than enforced.

### LOW — doc/consistency hygiene
4. **ATTACK 1 fix**: `PLAN_v2.md:368` — change "decision_chain.market_phase" to "probability_trace_fact.market_phase" so the next agent doesn't add the column to the wrong table.
5. **ATTACK 2 fix**: `PLAN_v2.md:124` — change "lead_hours_to_settlement_close() ≤ 24" to "city-local 00:00 of target_local_date (start-of-target_date local)".
6. **ATTACK 3 fix**: `PLAN_v2.md:30` (§0.1 amendment 5) and §8 — either insert T7 in §8 body or remove T7 promise from §0.1.
7. **ATTACK 9 fix**: `src/state/db.py:1444` — add comment referencing PLAN_v3 §6.P9 so the index isn't garbage-collected as orphan.

---

## Honest disclosure

What I did NOT attack thoroughly:

- I did NOT regression-run the full test suite. The system python3 lacks sklearn; canonical zeus venv lives outside the worktree at `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv`. I ran the two new test files (25/25 pass) but did not verify zero-regression elsewhere. Per memory L22+L28, executor-claimed "zero new failures" must be reproduced by the critic's regression run; I am marking this as a *known regression-baseline gap* the team-lead must close before merge. Recommend team-lead runs `python -m pytest tests/ -q` from the canonical venv against e62710e6 baseline and current HEAD before merging PR #53.

- I did NOT read the cycle_runner-level integration to verify decision_time at `cycle_runner.py:401` always carries `tzinfo == timezone.utc` (only verified by code-grep that it's `datetime.now(timezone.utc)`). Per ATTACK 6, this is the production path that bypasses the looseness of `_require_utc` — but I did not exercise this end-to-end.

- ATTACK 4 (legacy DB upsert) — I reproduced the trace_id-collision case in REPL but did not check whether any *external* consumer (e.g., a replay harness) writes to `probability_trace_fact` with a non-`probtrace:{decision_id}` trace_id format that could collide on `decision_id` UNIQUE.

- ATTACK 9 (index cost) — I did not run `EXPLAIN QUERY PLAN` against a synthetic post-P9 cohort SQL to confirm the index is actually selected.

- I did not validate that the PR title / commit messages reference PLAN_v3 §0.1 and the deferred-A14 transition. (They reference PLAN §6.P2 by stage number, which is sufficient for a critic-reproducible chain.)

- I did not chase the down-stream consequence of `market_phase` being a string on EdgeDecision but an enum on MarketCandidate through any further consumer beyond `log_probability_trace_fact`. The asymmetry is documented in source comments (`evaluator.py:218-222`) and the writer handles both shapes via `hasattr(_, "value")`.
