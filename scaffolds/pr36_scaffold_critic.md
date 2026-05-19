# SCAFFOLD Critic — PR 3+6 (B/36)

**Verdict**: NEEDS_REVISION
**Reviewer**: sonnet SCAFFOLD critic (`a015f518e63ed1b0b`)
**Reviewed**: 2026-05-19 against `0e0bf0f48f`

The structural design is sound on the happy path. Three issues require SCAFFOLD revision before production:

---

## Required revision #1 — Field count math correction (BLOCKING)

The field map said `12 existing + 8 PR 6 + 1 PR 3 = 21 fields total`. F1 (your own escalation) revealed rows 1-3 are GENUINELY NEW, not existing. Critic confirmed via grep (`provider_reported_time` + `observation_available_at` have zero codebase occurrences anywhere).

**Corrected math**: `12 existing + 4 PR 3 new + 8 PR 6 new = 24 fields total`.

Update SCAFFOLD with explicit note:
> "Field map coordination count of 21 was based on rows 1-3 being labeled (existing) in DecisionSourceContext. F1 finding corrects this: rows 1-3 are genuinely new dataclass fields. Post-merge DecisionSourceContext field count = 24."

This prevents the Wave-B opus critic from gating on the wrong number.

---

## Required revision #2 — F4 prior-hash cache site (BLOCKING)

Your F4 escalation hypothesized `cycle_runtime` snapshot cache. Critic verified this is WRONG: `raw_orderbook_hash` in `cycle_runtime.py:950` is a one-shot audit value, not a persisted cache. There is no inter-cycle prior-hash store today.

**Specification (apply to SCAFFOLD before production)**:
Add a module-level cache in the consumer path:
```python
# in src/engine/monitor_refresh.py (or wherever the per-cycle refresh runs)
_prev_orderbook_hash_by_market: dict[str, tuple[str, float]] = {}
# value: (hash_str, captured_at_unix_ts)
# updated on every snapshot ingest; delta_ms = (now - prev_ts) * 1000 when hash changes
```

Specify in your SCAFFOLD: the exact module hosting this dict, where it's read (on snapshot construction), where it's written (after hash compare), and its lifetime (process-local; no persistence needed for Phase 0 instrument).

Without this, `raw_orderbook_hash_transition_delta_ms` is unimplementable.

---

## Required revision #3 — Validator vacuousness / writer-gap decision (BLOCKING)

Critic found that PR 3's R-3.1/R-3.2/R-3.3 validators on `observation_time`/`provider_reported_time`/`observation_available_at` will pass **vacuously** at runtime: `observation_client.py:354-358` does NOT populate these fields into `DecisionSourceContext` factory paths. The validators have empty-string guards, so they silently no-op. The instrument is dead-on-delivery until a writer is wired.

**Orchestrator decision: Path B — expand PR 3 scope to wire the writer in the same PR.**

Rationale:
- Path A (ship vacuous validators): pollutes schema with dead instruments. Violates "every field has an observer" principle.
- Path C (drop rows 1-3 from PR 3): loses 3 of the 6 ordering assertions PR 3 was built to add. Halves PR 3's value.
- Path B (wire writer alongside validators): ~50-80 LOC addition to `observation_client.py` to populate the 3 fields from existing `ObservationInstant` data. Ships live-on-delivery instrument. Same PR keeps atomicity.

**Specification**:
- In `observation_client.py`, locate the construction site of `DecisionSourceContext` (or `ObservationInstant` if that's the intermediate type) and populate:
  - `observation_time` ← already-tracked timestamp of the measurement itself
  - `provider_reported_time` ← provider's stated reported-at timestamp (from the API response payload, if available; else duplicate `observation_time` with a `degradation_level` adjustment so the validator still does meaningful work)
  - `observation_available_at` ← timestamp at which the harvester/client made the value available to downstream consumers (i.e., the write-back time)
- Add 2 unit tests verifying the writer populates these fields per the ordering invariants (the writer side counterpart of R-3.1/R-3.2/R-3.3).
- LOC estimate increases ~80; revise to **~610 production + ~250 tests = ~860**.

If you discover (a) the API payload doesn't carry `provider_reported_time`, OR (b) the construction site is a hot path you can't safely modify in this PR, halt and report — that becomes an operator escalation (NOT for you to resolve unilaterally).

---

## Recommended (non-blocking, address during production if cheap)

1. **Error-string naming convention**: existing validator uses long form (`"forecast_available_after_decision"`). Your new validators use short form (`"available_after_decision"`). Pick one convention; add a note in `integrity_errors()` explaining if you keep both.
2. **`CausalityStatus` enum ↔ error-string mapping**: explicit mapping somewhere (e.g., a dict in `snapshot_ingest_contract.py`) preventing the two namespaces from drifting.
3. **Backfill condition for `polymarket_end_anchor_source`**: critic flagged the `tx_hash IS NULL → 'gamma_explicit'` heuristic may be inverted. Verify against `market_phase.py:236` writer logic before running the migration.
4. **Header blocks on 3 new test files**: `Lifecycle/Purpose/Reuse` triplet per `architecture/naming_conventions.yaml`.
5. **test_topology.yaml registration**: add the 3 new test file entries inline in the production commit. Wave-A opus critic caught this miss; don't repeat.
6. **`db_table_ownership.yaml` column inventory** updates for the 8 new columns across `ensemble_snapshots_v2`, `settlement_commands`, `wrap_unwrap_commands`.
7. **`docs/operations/INDEX.md`** entry for new module `src/runtime/clock_skew_probe.py`.
8. **LOC estimate revision**: 530 likely undercounts migrations (60-80 LOC each is realistic, not 40). With Path B writer expansion, expect ~860.

---

## APPROVED items (no action needed)

- Field naming + storage table selection
- B/27 non-collision (`raw_orderbook_hash` is attribute/dict/SQL access only)
- Clock skew probe module sketch (stdlib-only HEAD request + 60s TTL cache + 100ms threshold)
- F2 verdict: `observation_instants_v2` schema migration deferred to Phase 1 is correct decision
- F3 write site recommendation: derive `run_complete_time` from `source_run_completeness == "COMPLETE"` branch in `_fetch_ecmwf_run_data()` result-building block (line ~818), NOT the `< 51` negative guard at line 775
- Storage migration safety: nullable ALTER ADD COLUMN pattern correct
- Relationship tests structure (Fitz methodology)

---

## Next step

1. Revise SCAFFOLD doc (`scaffolds/pr36_scaffold.md`) with: (a) corrected field count (24, not 21); (b) F4 prior-hash cache module location + dict signature; (c) Path B writer expansion plan in `observation_client.py` with the 3-field population logic; (d) revised LOC estimate.
2. Commit the SCAFFOLD revision: `git commit -m "scaffold(pr36): revision per SCAFFOLD critic — field count + prior hash cache + writer wire-up"`
3. Push to same branch (`feat/phase0-pr36-decision-source-context-coordinated-20260519`).
4. Report back: revised SCAFFOLD SHA + LOC estimate + any new ESCALATIONs.
5. **DO NOT** start production code yet. Wave-B opus critic will read the revised SCAFFOLD after B/27's production phase lands and B/36's revision lands.
