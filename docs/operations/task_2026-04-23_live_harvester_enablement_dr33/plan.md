# DR-33 Live-Harvester Enablement — Packet Plan

**Follow-up to**: data readiness workstream (closed 2026-04-23, 8/8 packets APPROVED)
**Goal**: restore the live harvester settlement-write path that P-D proved structurally unreachable, using the P-E-established canonical pattern (INV-14 + provenance_json + SettlementSemantics gate). Staged as code-only (flag OFF by default).
**Scope**: `src/execution/harvester.py` + `architecture/source_rationale.yaml` note + `tests/test_harvester_dr33_live_enablement.py`
**Date**: 2026-04-23
**Pre-review**: critic-opus
**Stage**: DR-33-A (code change + tests; `ZEUS_HARVESTER_LIVE_ENABLED` default OFF)

---

## Section 1 — Why this packet exists

Per P-D §6.1: current `_find_winning_bin` at `src/execution/harvester.py:486-503` reads `market.get("winningOutcome")`, which is absent in 412/412 Gamma closed markets (empirically verified). The entire harvester write path never fires; `run_harvester` polls Gamma every live cycle, returns 0 settled events, exits silently.

Per P-E + the workstream's canonical-authority pattern: any new settlement row MUST carry INV-14 identity spine + provenance_json with decision_time_snapshot_id + earn `authority='VERIFIED'` via `SettlementSemantics.assert_settlement_value()` containment gate.

Per first_principles.md §8 rule 12 (NH-G-future): Gamma API pagination tail is unreliable; slug-based direct fetch preferred.

DR-33 unblocks midstream T6.1/T6.2 P&L regression work (explicitly blocked on ≥30-settlement live corpus per `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md:107-108`).

---

## Section 2 — Scope (DR-33-A)

**IN scope** (code change only; flag OFF by default → no runtime behavior change on deploy):

1. **Replace `_find_winning_bin`** with P-D §6.1 UMA-vote-based logic. Strict pattern: `umaResolutionStatus='resolved'` + `outcomePrices[0]=='1'` + outcomes-order invariant + fail-closed on anything unexpected.
2. **Replace `_format_range`** with canonical text form matching P-E (`X°C or below` / `X°C or higher` for shoulders; `-999`/`999` sentinel format retired per NH-E2 + critic-opus C1 round-trip verified).
3. **Refactor `_write_settlement_truth`**:
   - Accept `obs_high_temp` + `obs_fetched_at` + `obs_id` + `obs_source` (injected from caller; not internal lookup — keeps function pure)
   - Call `SettlementSemantics.for_city(city).assert_settlement_value(obs_high_temp)` → rounded value
   - Containment check: rounded value ∈ `[pm_bin_lo, pm_bin_hi]` ?
     - Yes → `authority='VERIFIED'`, `settlement_value=rounded`, `winning_bin=canonical_bin_label(...)`
     - No → `authority='QUARANTINED'` with reason `harvester_live_obs_outside_bin`
   - Populate INV-14 fields: `temperature_metric='high'`, `physical_quantity='daily_maximum_air_temperature'`, `observation_field='high_temp'`, `data_version=per-source` (wu_icao_history_v1 / ogimet_metar_v1 / hko_daily_api_v1 / cwa_no_collector_v0)
   - Populate `provenance_json` with: `writer='harvester_live_dr33'`, `source_family`, `obs_source`, `obs_id`, `decision_time_snapshot_id`, `rounding_rule`, `reconstruction_method='harvester_live_uma_vote'`, `event_slug`, `reconstructed_at`, `audit_ref='docs/operations/task_2026-04-23_live_harvester_enablement_dr33/plan.md'`
   - Remove inline `conn.commit()` at L569 — caller owns transaction boundary (P-H / MEMORY L30 concern)
   - Use `INSERT OR REPLACE` pattern (UNIQUE(city, target_date) dedupes; safer than UPDATE-then-INSERT upsert)
4. **Feature flag at `run_harvester()` entry**:
   - Check `os.environ.get("ZEUS_HARVESTER_LIVE_ENABLED", "0")` at function start
   - If not `"1"` → log "harvester disabled by ZEUS_HARVESTER_LIVE_ENABLED flag; skip cycle" and return early with empty counts dict
   - Default OFF → deploy-safe; existing daemon cycle is a no-op (matches current behavior where _find_winning_bin never fires)
5. **Source rationale note**: add a `dr33` key under `settlement_write` in `architecture/source_rationale.yaml` pointing at this plan for future auditability. Harvester is already the registered owner.
6. **Tests** (`tests/test_harvester_dr33_live_enablement.py`):
   - T1 `_find_winning_bin` returns None on `umaResolutionStatus='pending'`
   - T2 `_find_winning_bin` returns (label, range_str) on YES-won resolved market
   - T3 `_find_winning_bin` returns None on unexpected outcomes order (`["No","Yes"]` — fail-closed)
   - T4 `_format_range` produces text form for all 4 shapes (point / range / low shoulder / high shoulder)
   - T5 `_write_settlement_truth` writes VERIFIED with full INV-14 + provenance_json when obs contains bin
   - T6 `_write_settlement_truth` writes QUARANTINED with `harvester_live_obs_outside_bin` reason when obs outside bin
   - T7 `_write_settlement_truth` does NOT call conn.commit() (transaction boundary moved to caller)
   - T8 `run_harvester` early-returns when `ZEUS_HARVESTER_LIVE_ENABLED != "1"`
   - T9 canonical bin labels from `_format_range` round-trip cleanly through `_parse_temp_range`

**NOT in scope** (DR-33-B / DR-33-C / future hygiene):
- Atomicity refactor of `append_many_and_project` callers (P-H / R3-02, R3-04, R3-05, R3-08)
- Flag flip to ON with operator approval (DR-33-C requires explicit live-trading readiness review)
- `_parse_temp_range` `re.fullmatch` hardening (NH-E1)
- Formal write_route registration for `p_e_reconstruction` (future governance packet)
- Midstream T6.1/T6.2 P&L regression (blocked on DR-33-C + ≥30 live settlements)

---

## Section 3 — Safety design

### 3.1 Feature-flag semantics

- Flag check at `run_harvester()` entry — blanket gate. If OFF, the entire function short-circuits. No partial execution.
- Default OFF means deploy doesn't change runtime behavior. Code change ships; writes dormant.
- Operator flipping ON is an explicit act of live-write enablement. At that point, DR-33-C-level review (monitoring, fallback, degrade-to-paper) is expected.

### 3.2 Fail-closed semantics in `_find_winning_bin`

- Unexpected `outcomes` order (not `["Yes","No"]`) → `continue` (don't infer)
- Non-string / missing `outcomePrices` → `continue`
- `umaResolutionStatus` != `"resolved"` → `continue`
- Every branch exit is `continue` or return None — never silent YES-win inference

### 3.3 SettlementSemantics gate is mandatory

- VERIFIED authority is ONLY stamped after `SettlementSemantics.for_city(city).assert_settlement_value(obs.high_temp)` succeeds AND containment check passes
- If assert raises `SettlementPrecisionError` (NaN/inf) → QUARANTINED with reason `harvester_live_settlement_precision_error`
- If containment fails → QUARANTINED with reason `harvester_live_obs_outside_bin`
- Matches P-E's canonical-authority pattern exactly; sustains the workstream's achievement

### 3.4 Transaction boundary

- `_write_settlement_truth` no longer calls `conn.commit()` — the caller (`run_harvester`) owns commit
- This allows future P-H atomicity refactor to wrap a full batch of settlements in one BEGIN/COMMIT
- For DR-33-A, the caller calls `conn.commit()` per-event (matches current behavior); future DR-33-B can batch

---

## Section 4 — Q1-Q10

**Q1 (invariant)**: INV-03 (append-first via canonical INSERT), INV-06 (decision_time_snapshot_id), INV-FP-1/3/4/5/6/7/9/10 (all stamped into provenance_json), INV-14 (4 identity fields populated).

**Q2 (fatal_misread)**: `wu_website_daily_summary_not_wu_api_hourly_max` preserved via data_version='wu_icao_history_v1'; `hong_kong_hko_explicit_caution_path` preserved via oracle_truncate in SettlementSemantics.for_city; `daily_day0_hourly_forecast_sources_are_not_interchangeable` preserved — harvester consumes settlement_daily_source obs only; `code_review_graph_answers_where_not_what_settles` preserved — harvester uses UMA vote not graph output.

**Q3 (single-source-of-truth)**:
- P-D §6.1 code diff is the canonical reference for `_find_winning_bin` replacement
- P-E plan + executor for INV-14 field values + provenance_json schema
- `src/contracts/settlement_semantics.py:147-182` for SettlementSemantics.for_city
- `src/config.py:68-101` for City dataclass (wu_station, settlement_source_type, settlement_unit)

**Q4 (first-failure)**:
- Gamma API 429/500 → retry with backoff (existing pattern); if persistent, cycle completes with 0 settlements (no data corruption)
- Unexpected Gamma shape → `continue` in loop; individual event skipped; other events process
- SettlementSemantics raises → QUARANTINED with reason; no exception propagation
- DB INSERT fails (CHECK / trigger) → exception logged, cycle continues to next event; UNIQUE(city, target_date) means INSERT OR REPLACE avoids spurious failures

**Q5 (commit boundary)**: DR-33-A keeps current per-event commit pattern but moves commit OUT of `_write_settlement_truth` into the caller for clean ownership. Future DR-33-B may batch.

**Q6 (verification)**:
- 9 unit tests in `tests/test_harvester_dr33_live_enablement.py`
- Schema pytests stay green (no schema change)
- Integration test: mock Gamma response, verify INSERT side-effects on scratch DB
- critic-opus reproduces via `pytest` + spot-reads on written rows

**Q7 (new hazards)**:
- H1 (feature flag leak): if `ZEUS_HARVESTER_LIVE_ENABLED` gets set by accident in non-live env, harvester starts writing. Mitigation: default OFF, explicit env var name, operator-level action required to flip.
- H2 (obs timing race): harvester may run before obs is collected for today's date → harvester finds Gamma event but obs lookup returns None. Mitigation: if `obs_high_temp is None` → skip the event entirely (don't write QUARANTINED row; retry next cycle when obs lands).
- H3 (duplicate harvester runs): two cycles racing. Mitigation: INSERT OR REPLACE is idempotent; no row duplication.
- H4 (non-standard market schema): some Gamma events may have different market structure. Mitigation: strict fail-closed on any unexpected shape; unhandled events skipped and logged.

**Q8 (closure)**:
- `plan.md` (this doc)
- `tests/test_harvester_dr33_live_enablement.py` — 9 tests all passing
- `src/execution/harvester.py` — updated function bodies
- `architecture/source_rationale.yaml` — DR-33 note under settlement_write
- `evidence/dr33_execution_log.md` — summary of changes + test output
- critic-opus APPROVE on plan + post-execution

**Q9 (decision reversal)**: None. DR-33-A aligns with the workstream-closure recommendation. Does not revive R3-09 pre-resolution price fallback (uses post-UMA-resolution vote per P-D §5.3 non-reversal attestation).

**Q10 (rollback)**:
- Code-only change; `git revert` reverses if issues surface
- No DB snapshot required (flag OFF = no DB writes)
- Future DR-33-C flag flip needs its own rollback plan

---

## Section 5 — Pre-verify items for critic-opus

1. **`_find_winning_bin` correctness**: UMA gate + outcomes-order check + fail-closed branches. Matches P-D §6.1 verbatim?
2. **`_format_range` round-trip**: all 4 shapes parse cleanly through `_parse_temp_range` (P-E C1 verified; re-verify)
3. **`_write_settlement_truth` INV-14 + provenance fields**: every INSERT populates all 18 columns including the new 5 from P-B
4. **SettlementSemantics gate**: authority='VERIFIED' path calls the canonical gate; QUARANTINED path preserves obs value as evidence
5. **Feature flag OFF = no runtime change**: demonstrable via test T8 + manual check
6. **Transaction boundary**: `_write_settlement_truth` doesn't call `conn.commit()`; caller does
7. **9 tests all pass**: `pytest -q tests/test_harvester_dr33_live_enablement.py`
8. **No regression on existing pytests**: `test_schema_v2_gate_a.py`, `test_canonical_position_current_schema_alignment.py`, `test_pe_reconstruction_relationships.py` (both modes) all still green

---

**Plan ready. Implementation + tests to follow; single pre-/post-review cycle with critic-opus.**
