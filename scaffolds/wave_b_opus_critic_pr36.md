# Wave-B Opus Critic — PR 3+6 (B/36) Findings

**Verdict**: NEEDS_REVISION (2 CRITICALs + 1 MAJOR blocking before production code begins)

---

## B1 (CRITICAL) — Prior-hash cache hosted in WRONG module; INV-alpha-provenance instrument dead-on-arrival

**Evidence**:
- `src/engine/monitor_refresh.py:652` (`monitor_quote_refresh`) and `:1320` (`refresh_position`) use `clob.get_best_bid_ask(tid)` returning `(bid, ask, bid_sz, ask_sz)` only
- `raw_orderbook_hash` is computed at `src/data/market_scanner.py:1919/1949` (inside `capture_executable_market_snapshot`)
- That's the ONLY path with `raw_orderbook_hash` in scope
- Your SCAFFOLD's `_prev_orderbook_hash_by_market` cache in `monitor_refresh.py` has NO access path to `raw_orderbook_hash`
- Therefore: cache never gets a hash to compare; `raw_orderbook_hash_transition_delta_ms` always `None`; INV-alpha-provenance antibody (`tests/test_inv_alpha_provenance.py`) unsatisfiable

**Fix paths (pick one)**:
- **(a) Relocate cache**: move `_prev_orderbook_hash_by_market` cache to `src/data/market_scanner.py::capture_executable_market_snapshot` (alongside line 1949 where hash is computed). Read prior entry on entry; write current after computation.
- **(b) Derived-field approach**: make `raw_orderbook_hash_transition_delta_ms` a derived field computed at `ExecutableMarketSnapshotV2` construction time, by looking up most-recent prior snapshot for same `condition_id` from world.db before insert.

**Orchestrator recommendation: (a)**. Process-local cache is faster + simpler; (b) adds an extra DB read per snapshot. Either is correct; (a) matches your original intent.

---

## B2 (CRITICAL) — Backfill SQL references non-existent column `market_end_at` on `settlement_commands`

**Evidence**:
- Your SCAFFOLD migration: `UPDATE settlement_commands SET polymarket_end_anchor_source = CASE WHEN market_end_at IS NOT NULL THEN 'gamma_explicit' ELSE 'f1_12z_fallback' END`
- `src/execution/settlement_commands.py:33-53` defines the table
- **`settlement_commands` has NO `market_end_at` column** — grep-confirmed by opus critic
- `market_end_at` lives on `executable_market_snapshots` (world.db) and `markets` table (`db.py:1184`), NOT on `settlement_commands`
- Migration UPDATE will crash with `OperationalError: no such column: market_end_at`

**Fix paths**:
- **(a) JOIN through executable_market_snapshots**: UPDATE-FROM subquery joining on `condition_id`, checking the snapshot's `market_end_at`. More elaborate SQL but retroactively accurate.
- **(b) Default historical rows to `'gamma_explicit'`**: simpler. Reasoning: only F1-fallback derives the anchor explicitly; if a historical row was created without explicit tagging, the safest default is the dominant case (`gamma_explicit`).

**Orchestrator recommendation: (b)**. Retroactive accuracy isn't critical for historical rows; simplicity wins. Add a comment noting that pre-PR-3 rows default to `'gamma_explicit'` because the anchor source wasn't tracked before this PR.

---

## B3 (MAJOR) — `DecisionSourceContext.required_fields` interaction with 12 new fields unspecified

**Evidence**:
- `execution_intent.py:642-655` defines `required_fields` (12 keys today)
- PR 3 adds 4 new fields: `observation_time`, `provider_reported_time`, `observation_available_at`, `polymarket_end_anchor_source`
- PR 6 adds 8 more fields
- Your SCAFFOLD discusses Path F's Optional treatment for `provider_reported_time` but is SILENT on the other 11
- If they ARE in `required_fields`: every existing decision emits `missing_observation_time`, etc., as integrity errors → backward-incompatible
- If they ARE NOT: silent population gap; field exists but no enforcement

**Fix**: SCAFFOLD must explicitly enumerate which of the 12 new fields are `required_fields` vs optional, with rationale.

**Orchestrator recommended classification**:
- **Required (must be populated)**: `observation_time`, `observation_available_at`, `polymarket_end_anchor_source` (PR 3); `first_member_observed_time`, `run_complete_time`, `zeus_submit_intent_time`, `venue_ack_time` (PR 6 — directly observable from writers in this PR)
- **Optional (Path F semantics)**: `provider_reported_time` (PR 3 — Path F Optional, your earlier resolution); `first_inclusion_block_time`, `finality_confirmed_time` (PR 6 — chain confirmations may arrive post-decision-write; treat as Optional with conditional validators); `clock_skew_estimate_ms` (PR 6 — Optional because probe might fail), `raw_orderbook_hash_transition_delta_ms` (PR 6 — Optional because first-observation has None)
- **Data migration plan for backward compat**: integrity check `_required_field_missing` applies only to decisions created with `decision_time >= PR_3_LANDED_AT`. Add a sentinel field `data_version >= N` or check the timestamp threshold; existing decisions remain in their "missing" state without emitting new errors.

If you disagree with any classification, push back with evidence before implementing.

---

## B4 (MINOR) — Clock-skew threshold 100ms is aggressive

**Evidence**: HTTPS RTT to public REST endpoint typically 30-100ms even on healthy network. HEAD-request NTP-style skew has additional confounding from response-processing time. 100ms barely exceeds noise floor.

**Fix paths**:
- **(a) Widen to 200ms** for `"excessive_clock_drift"` error; emit `"clock_drift_warning"` (non-blocking observability) at 100ms boundary
- **(b) Per-host RTT subtraction**: probe twice, use RTT/2 as network correction; tighten threshold to 50ms with the noise removed

**Orchestrator recommendation: (a)**. Simpler; observability ≠ failure. Document the rationale.

---

## Cross-PR (acknowledged, no action needed by B/36)

- Your `raw_orderbook_hash` line anchor `:94` is stale (B/27 shifted it to `:97`). Functionally irrelevant since you access by attribute. Refresh SCAFFOLD note for clarity.
- INV-37 not exercised in this wave (your DB writes are within-DB only). No cross-DB transaction site.
- SCHEMA_VERSION: B/27 bumped 10 → 11 + rewrote `tests/state/_schema_pinned_hash.txt`. **You must bump 11 → 12** + regenerate the pin in your migration commit. Add to SCAFFOLD commit-plan.

---

## Action

1. Read this verdict
2. Apply B1 fix (path a — relocate cache to `market_scanner.py::capture_executable_market_snapshot`)
3. Apply B2 fix (path b — default historical rows to `'gamma_explicit'`)
4. Apply B3 enumeration (use orchestrator's recommended classification or push back with evidence)
5. Apply B4 fix (path a — widen to 200ms; warning at 100ms)
6. Refresh stale `raw_orderbook_hash` line anchor (94 → 97)
7. Add SCHEMA_VERSION 11→12 + pinned_hash regen to commit-plan
8. Update `scaffolds/pr36_scaffold.md` with all fixes
9. Commit + push:
   ```
   git add scaffolds/
   git commit -m "scaffold(pr36): Wave-B opus revisions — B1 cache relocate + B2 backfill fix + B3 required_fields enum + B4 clock skew"
   git push origin feat/phase0-pr36-decision-source-context-coordinated-20260519
   ```
10. Report new SHA + final LOC + confirmation each blocker resolved
11. **THEN proceed to production phase** — implement the SCAFFOLD as code. Run regression. Commit + push. Report HEAD SHA + regression pass count.
12. **DO NOT open PR** — orchestrator batches with B/27.
