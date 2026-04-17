# Zeus Dual-Track Coordination Handoff — Truly-RED Bug Queue

Session: 2026-04-18 (post-session-2 closure push `f0c1795`)
Audit: [zeus_data_improve_bug_audit_100_dual_track_reassessment.md](zeus_data_improve_bug_audit_100_dual_track_reassessment.md)
DT package: [zeus_dual_track_refactor_package_v2_2026-04-16/00_MASTER_EXECUTION_PLAN_zh.md](../../zeus_dual_track_refactor_package_v2_2026-04-16/00_MASTER_EXECUTION_PLAN_zh.md)
Assumption: **DT has progressed to Phase 5** (Phases 1–4 are DONE). Phase 5 builds the low historical lane and the truth/authority flags; Phase 6 (Day0 runtime split + DT#6 graceful degradation) is still future.

## Phase anchoring cheat sheet

| DT phase | Deliverable | State (2026-04-18) |
|---|---|---|
| Phase 1 | MetricIdentity spine + `time_context.py` rewrite (`b025883`) | DONE |
| Phase 2 | World DB **v2** schema (`settlements_v2`, `ensemble_snapshots_v2`, `calibration_pairs_v2`, audit-append tables) + DT#1 commit-ordering + `ChainState` enum (`16e7385`) | DONE |
| Phase 3 | `observation_client` `low_so_far` + source-registry collapse (`6e5de84`) | DONE |
| Phase 4 | High lane local-calendar-day max v1 + refit_platt_v2 (`5d0e191`) | DONE |
| Phase 5 | Low historical lane; truth metadata registry dual-track; authoritative truth flag | **IN FLIGHT** |
| Phase 6 | `Day0HighSignal` / `Day0LowNowcastSignal` split; DT#6 graceful-degradation law | FUTURE |
| DT#1 architect packet | Atomic `_execute_candidate` transaction | OPEN |

"Schema v2" everywhere in the audit refers to Phase 2 deliverables and is therefore **landed** under this assumption.

## Summary matrix

| Bug | File:line | Section | DT prerequisite |
|---|---|---|---|
| B063 | [src/state/chain_reconciliation.py](../../src/state/chain_reconciliation.py) + [src/state/db.py](../../src/state/db.py) | **A** | Phase 2 `rescue_events_v2` audit table |
| B070 | [src/state/db.py#L3592-L3640](../../src/state/db.py#L3592) | **A** | Phase 2 `control_overrides_history_v2` |
| B071 | [src/state/db.py#L3643-L3722](../../src/state/db.py#L3643) | **A** | Phase 2 `token_suppression_history_v2` |
| B091 | [src/engine/evaluator.py#L1494-L1515](../../src/engine/evaluator.py#L1494) | **A** | Phase 1 MetricIdentity + `time_field_status` enum (SD-G) |
| B100 | [src/state/db.py#L1078-L1094](../../src/state/db.py#L1078) | **A** | Phase 2 v2 migration path (replaces `DROP TABLE`) |
| B069 | [src/state/db.py#L3560-L3583](../../src/state/db.py#L3560) + [src/state/portfolio.py](../../src/state/portfolio.py) | **B** | Phase 5 authoritative truth flag + Phase 1 MetricIdentity view layer |
| B073 | [src/state/portfolio.py#L974-L993](../../src/state/portfolio.py#L974) | **B** | Phase 5 authoritative truth flag |
| B077 | [src/state/truth_files.py#L101-L102](../../src/state/truth_files.py#L101) | **B** | Phase 5 SD-A mode authority (land `mode` as first-class routing key) |
| B078 | [src/state/truth_files.py#L46-L50](../../src/state/truth_files.py#L46), [L123-L125](../../src/state/truth_files.py#L123), [L174-L179](../../src/state/truth_files.py#L174) | **B** | Phase 5 low historical lane truth-meta registry |
| B093 | [src/engine/replay.py#L246-L280](../../src/engine/replay.py#L246) | **B** | Phase 5 low-lane MetricIdentity enforcement |
| B055 | [src/riskguard/riskguard.py#L173-L178](../../src/riskguard/riskguard.py#L173) | **C** | Phase 6 DT#6 graceful-degradation law |
| B099 | [src/engine/cycle_runtime.py#L1078-L1103](../../src/engine/cycle_runtime.py#L1078), [L1191-L1193](../../src/engine/cycle_runtime.py#L1191) | **C** | DT#1 architect packet (atomic `_execute_candidate` transaction) |

---

## Section A — Pre-Phase-5 unlock candidates

These bugs depend on Phase 1–4 deliverables that are already in place. They can be scheduled **now** against the current `data-improve` HEAD without further DT gating.

### B063 — Rescue event has no durable audit row
- **File**: [src/state/chain_reconciliation.py](../../src/state/chain_reconciliation.py) writing through [src/state/db.py](../../src/state/db.py).
- **Failure mode today**: Rescue is emitted as a structured log line only; there is no append-only `rescue_events_v2` row with `(trade_id, decision_snapshot_id, metric_identity, chain_state, reason, occurred_at)`. On the low lane, `N/A_CAUSAL_DAY_ALREADY_STARTED` slots bypass rescue entirely, and we cannot tell afterwards whether a rescue was *skipped legitimately* or *silently lost*.
- **Expected fix pattern**: Under Phase 2 schema v2, add a `rescue_events_v2` audit table with `temperature_metric` and `causality_status` columns, and have `chain_reconciliation` call a thin `db.log_rescue_event(...)` helper inside the same transaction that updates position state. Keep the existing log line; the row is the authority.
- **DT prerequisite**: Phase 2 v2 audit-append tables (DONE) + the SD-F "append-only audit vs mutable current-row" contract.
- **Verification-after-fix**: A test that triggers a low-lane N/A_CAUSAL rescue attempt and asserts exactly one `rescue_events_v2` row with `causality_status="N/A_CAUSAL_DAY_ALREADY_STARTED"` and non-null `temperature_metric`; a second test asserts that a DB write failure rolls back the parent state mutation.

### B070 — `control_overrides` upsert silently overwrites prior authority
- **File**: [src/state/db.py#L3592-L3640](../../src/state/db.py#L3592) (override upsert path).
- **Failure mode today**: The current path writes a mutable "current state" row; replacing an active override erases who set the prior value, when, and why. Supervisors cannot reconstruct an override timeline and cannot attribute post-override behavior to a specific actor.
- **Expected fix pattern**: Split into (a) `control_overrides_history_v2` append-only audit (UTC `applied_at`, `actor`, `reason`, `prior_value_json`, `new_value_json`, `expires_at`) and (b) a `control_overrides_current` view derived from the history via `MAX(applied_at)`-per-key. All supervisor reads go through the view; all writes go through the audit table.
- **DT prerequisite**: Phase 2 v2 audit tables (DONE) + SD-F append-only contract.
- **Verification-after-fix**: A test that applies two overrides on the same key and asserts that the history table has 2 rows, the view reflects only the later one, and a rollback/expiry flow is reconstructible from history alone.

### B071 — `token_suppression` upsert merges history into a single row
- **File**: [src/state/db.py#L3643-L3722](../../src/state/db.py#L3643).
- **Failure mode today**: Re-suppressing a token with a new reason replaces the prior reason and `suppressed_at`, so the sequence `(auto-suppress → manual-override → auto-suppress again)` is indistinguishable from a single manual action in the post-mortem record.
- **Expected fix pattern**: Identical shape to B070: introduce `token_suppression_history_v2` (append-only) + a derived-view `token_suppression_current`. Writes become `INSERT` only; reads aggregate.
- **DT prerequisite**: Phase 2 v2 audit tables (DONE).
- **Verification-after-fix**: Test suppresses then un-suppresses then re-suppresses a token; asserts 3 history rows, the view reflects only the last state, and the `(token_id, suppressed_at)` timeline is recoverable.

### B091 — Sentinel strings in `MarketAnalysis` time fields
- **File**: [src/engine/evaluator.py#L1494-L1515](../../src/engine/evaluator.py#L1494) (the materialized-decision emission block).
- **Failure mode today**: Time-like fields (`decision_time`, `issue_time`, `observation_time`) are emitted as bare strings, occasionally as `""` or `"UNAVAILABLE"` on the degraded path. Downstream consumers can't tell "never observed" from "observed at epoch zero", and the replay/forecast fallback at B093 relies on this ambiguity.
- **Expected fix pattern**: Per SD-G, add a typed companion `time_field_status: Literal["OK","UNAVAILABLE_UPSTREAM","UNSPECIFIED"]` adjacent to each time field, with the time field itself constrained to `datetime | None`. Evaluator emits MetricIdentity-bound time objects (Phase 1 contract) or `None + status`, never a sentinel string.
- **DT prerequisite**: Phase 1 MetricIdentity + `time_context.py` (DONE); this is the last SD-G debt.
- **Verification-after-fix**: A test that forces the Day0 degraded path and asserts the produced decision has `decision_time is None and time_field_status == "UNAVAILABLE_UPSTREAM"`; a second test asserts no string comparison on `decision_time` survives in downstream call sites (grep-gated regression).

### B100 — DDL `DROP TABLE` in migration path
- **File**: [src/state/db.py#L1078-L1094](../../src/state/db.py#L1078).
- **Failure mode today**: The migration helper calls `DROP TABLE` then re-creates, which destroys any rows written between v1 shutdown and v2 bootstrap. With Phase 2 v2 tables landed, this is a silent data-loss hazard if the migration re-runs.
- **Expected fix pattern**: Replace the `DROP TABLE` block with the Phase 2 v2 migration pattern — create v2 alongside v1, backfill rows with a one-shot `INSERT … SELECT` that tags `data_version`, then flip reads. Drops, if any, happen only behind an explicit `--destructive` CLI flag with a confirmation log.
- **DT prerequisite**: Phase 2 v2 migration tooling (DONE).
- **Verification-after-fix**: A migration test that seeds v1 rows, runs the migrator, and asserts both v1 rows survive in v2 and that a second re-run is a no-op (idempotent).

---

## Section B — At-Phase-5 unlock

These bugs require Phase 5 deliverables that are currently in flight (authoritative truth flag, low-lane MetricIdentity propagation, mode-aware truth files). Land the fix **as** the matching Phase 5 commit merges, not before — otherwise the fix will be re-written.

### B069 — `portfolio_loader_view` synthesizes defaults; DB outage ≡ legitimate-empty
- **File**: [src/state/db.py#L3560-L3583](../../src/state/db.py#L3560) + consumers in [src/state/portfolio.py](../../src/state/portfolio.py).
- **Failure mode today**: When the canonical DB is unreachable the loader returns an empty `PortfolioState` with default bankroll and no marker. A legitimately empty low book is indistinguishable from a live outage, and downstream risk math uses synthesized zeros as if they were authority.
- **Expected fix pattern**: Gate the loader on the Phase 5 authoritative truth flag (`truth.authority ∈ {"canonical_db","UNVERIFIED"}`) and return a typed `PortfolioState.degraded(reason=…, authority="UNVERIFIED")` instead of defaults. Consumers must check `state.authority != "UNVERIFIED"` before letting entries through.
- **DT prerequisite**: Phase 5 truth-flag landing + Phase 1 MetricIdentity view-layer projection (so low-book emptiness carries `temperature_metric` context).
- **Verification-after-fix**: Unit test with a forced DB outage asserts the returned state has `authority="UNVERIFIED"` and that `entries_blocked_reason` trips on it; a second test with an intentionally empty but reachable DB asserts `authority="canonical_db"` is preserved.

### B073 — `load_portfolio` truth outage returns degraded state without authority flag
- **File**: [src/state/portfolio.py#L974-L993](../../src/state/portfolio.py#L974) (currently logs an error and returns an empty `PortfolioState` with `entries suppressed`).
- **Failure mode today**: The error log fires but the returned dataclass has no machine-readable `authority` field; upstream `choose_portfolio_truth_source` return value is consumed locally and not propagated. A caller that skips the `policy.source` check downgrades silently.
- **Expected fix pattern**: Under Phase 5, `PortfolioState` gains an `authority: Literal["canonical_db","degraded","unverified"]` field populated from `policy`. Every public entry point asserts `state.authority == "canonical_db"` before accepting the value as input to risk sizing.
- **DT prerequisite**: Phase 5 authoritative truth flag (shared with B069).
- **Verification-after-fix**: Test forces `choose_portfolio_truth_source` to return non-canonical; asserts returned state has `authority != "canonical_db"`; a second test asserts risk-sizing callers raise rather than proceed on non-canonical authority.

### B077 — `read_mode_truth_json` ignores `mode` parameter
- **File**: [src/state/truth_files.py#L101-L102](../../src/state/truth_files.py#L101).
- **Failure mode today**: The function signature accepts `mode` but delegates to `read_truth_json(mode_state_path(filename))` without threading `mode` into path resolution or authority tagging. Live-vs-paper truth files can collide at the routing layer; a paper caller can be served live truth or vice versa.
- **Expected fix pattern**: Pass `mode` into `mode_state_path(filename, mode=mode)` and into the returned truth metadata (`truth["mode"]` must match the caller's `mode` argument, else raise `ModeMismatchError`). This is the SD-A "mode as first-class routing key" commitment.
- **DT prerequisite**: Phase 5 SD-A mode-authority propagation (the mode field becomes strict-required on truth metadata).
- **Verification-after-fix**: Test invokes `read_mode_truth_json("portfolio.json", mode="paper")` against a `live`-tagged file on disk and asserts `ModeMismatchError`; a second test confirms correct-mode reads still succeed and the returned metadata's `mode` round-trips.

### B078 — Truth metadata registry is live-only; low historical lane has no entries
- **File**: [src/state/truth_files.py#L46-L50](../../src/state/truth_files.py#L46), [L123-L125](../../src/state/truth_files.py#L123), [L174-L179](../../src/state/truth_files.py#L174) (legacy-archive and `LEGACY_STATE_FILES` plumbing).
- **Failure mode today**: The registry knows `ACTIVE_MODES` for live/paper trading state but has no hooks for Phase 5 low-historical-lane truth files (`platt_models_low`, `calibration_pairs_low`). Phase 5 tools that write those artifacts have nowhere to record `generated_at` / `source_path` / `data_version`, so low-lane provenance dies at the filesystem boundary.
- **Expected fix pattern**: Extend `LEGACY_STATE_FILES` and `build_truth_metadata` to cover the new low-lane file set, with `temperature_metric` and `data_version` as required metadata keys. Reads must fail closed when these are missing for files under the low-historical prefix.
- **DT prerequisite**: Phase 5 low historical lane file layout (defined by the Phase 5 commit that introduces the low-side artifacts).
- **Verification-after-fix**: Test writes a low-historical-lane truth file, round-trips it through `annotate_truth_payload` / `read_truth_json`, and asserts `temperature_metric="low"` and `data_version` are preserved; a second test asserts a low-lane file missing `temperature_metric` fails closed.

### B093 — Replay forecast fallback fabricates `decision_time` and hardcodes `agreement="AGREE"`
- **File**: [src/engine/replay.py#L246-L280](../../src/engine/replay.py#L246) (`_forecast_reference_for`).
- **Failure mode today**: When no historical decision exists, the fallback synthesizes `decision_time = f"{basis_dates[-1]}T12:00:00+00:00"` (or epoch-of-target-date) and returns `agreement="AGREE"` unconditionally. This is a SD-E violation; on the low lane it doubles the ambiguity because `forecast_low` column usage is not tagged with `temperature_metric`.
- **Expected fix pattern**: Replace synthesized values with explicit markers: `decision_reference_source="forecasts_table_synthetic"`, `agreement="UNKNOWN"`, and a `decision_time_status="SYNTHETIC_MIDDAY"`. Query must filter by `temperature_metric` once the low-lane MetricIdentity propagation lands.
- **DT prerequisite**: Phase 5 low-lane MetricIdentity enforcement + B091's `time_field_status` (Section A).
- **Verification-after-fix**: Test on a target_date with no decision asserts the returned reference has `decision_reference_source == "forecasts_table_synthetic"`, `agreement == "UNKNOWN"`, and `decision_time_status == "SYNTHETIC_MIDDAY"`; a second test asserts the query no longer returns high rows when `temperature_metric="low"` is requested.

---

## Section C — Post-Phase-5 / architect-gated

These bugs cannot land under Phase 5 alone: one requires the DT#1 commit-ordering architect packet (B099); the other is explicitly bundled into Phase 6 DT#6 (B055). Do not attempt independent fixes.

### B055 — Riskguard trailing-loss 2h staleness tolerance
- **File**: [src/riskguard/riskguard.py#L173-L178](../../src/riskguard/riskguard.py#L173) (`TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE = timedelta(hours=2)` and surrounding `_trailing_loss_reference`).
- **Failure mode today**: A two-hour staleness window can mask a real outage: during the window the guard reports `TRAILING_LOSS_SOURCE_DEGRADED` without escalating to `DATA_DEGRADED` control state, and there is no graceful-degradation law tying this to a `RUNNING | DEGRADED | PAUSED` transition.
- **Expected fix pattern**: Under DT#6, the staleness check becomes one of the explicit inputs to the graceful-degradation state machine — an SLO-calibrated tolerance, an explicit transition to `DEGRADED`, and a rule for escalation to `PAUSED` after N consecutive degraded ticks. Do **not** pre-fix the tolerance constant in isolation; the consolidation packet covers B047 + B049 + B055 together.
- **DT prerequisite**: Phase 6 DT#6 graceful-degradation law (FUTURE).
- **Verification-after-fix**: Relationship test: force a 3-hour reference gap and assert control state transitions `RUNNING → DEGRADED` on tick 1 and `DEGRADED → PAUSED` after the configured consecutive-tick threshold; assert trailing-loss output carries the matching control state, not a bare `source` string.

### B099 — `cycle_runtime._execute_candidate` outer `except` after partial commits
- **File**: [src/engine/cycle_runtime.py#L1078-L1103](../../src/engine/cycle_runtime.py#L1078) (call sequence: `deps.add_position(...)` → `log_trade_entry(...)` → `_dual_write_canonical_entry_if_available(...)` → `log_execution_report(...)`) and [L1191-L1193](../../src/engine/cycle_runtime.py#L1191)+ outer `except Exception`.
- **Failure mode today**: The block at L1078-1103 performs four independent state mutations, and the outer `except Exception` at L1205 catches *after* any subset of them may have committed. A failure in `log_execution_report` leaves `add_position` and `log_trade_entry` visible; a failure in the canonical dual-write leaves in-memory portfolio ahead of canonical. This is the largest remaining DT#1 debt.
- **Expected fix pattern**: Wrap the full sequence in an explicit `with conn:` transactional scope (or a named SAVEPOINT for the canonical dual-write), and make every mutation either roll back together or not apply at all. The fix must be authored against the architect's DT#1 commit-ordering packet so the in-memory portfolio and canonical DB agree on ordering (canonical first, in-memory last, reconcile on restart).
- **DT prerequisite**: DT#1 architect packet (open). The audit §7c explicitly planning-locks this zone.
- **Verification-after-fix**: Failure-injection test: stub `log_execution_report` to raise; assert `add_position` and `log_trade_entry` both roll back (no orphaned position, no orphaned ledger row); a second test stubs `_dual_write_canonical_entry_if_available` to raise and asserts the canonical and in-memory states remain consistent on the next reconcile tick.

---

## Merge hygiene notes

- Any bug whose fix file overlaps with the Dual-Track patch map (see [02_FILE_BY_FILE_PATCH_MAP_zh.md](../../zeus_dual_track_refactor_package_v2_2026-04-16/02_FILE_BY_FILE_PATCH_MAP_zh.md)) MUST be merged **after** the corresponding DT phase has committed, not before. In particular, Section B bugs must sequence behind the Phase 5 commit introducing the authoritative truth flag and the low-lane file layout.
- Before starting each bug, run `git --no-pager diff --stat <file>` to verify no other-agent edits have landed on disk undetected. The `git diff --stat` pre-stage antibody caught contamination twice in session-2 (`src/state/portfolio.py` and `src/signal/day0_signal.py`); treat any line-count surprise as a stop-the-line event and `git checkout HEAD -- <file>` before re-applying the intended edit.
- Every fix must include a test that **fails without the fix and passes with it**. Mere narrowing of `except`-types, addition of `isinstance` checks, or introduction of typed status columns all qualify as long as there is a concrete failing assertion without the change.
- Section A bugs (B063, B070, B071, B091, B100) touch audit-append tables and a SD-G time-status enum; they are low-ripple and should be sequenced first to shrink the RED queue from 12 to 7 before Phase 5 lands.
- Section B bugs (B069, B073, B077, B078, B093) share the Phase 5 truth-authority seam; prefer batching their commits with the Phase 5 landing to avoid three-way merges on `src/state/truth_files.py` and `src/state/portfolio.py`.
- Section C bugs (B055, B099) require architect sign-off. Do not open either without the DT#1 / DT#6 packet on file.
