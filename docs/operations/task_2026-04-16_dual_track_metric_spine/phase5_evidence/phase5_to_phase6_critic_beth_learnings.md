# phase5_to_phase6 — critic-beth learnings

**Date**: 2026-04-17
**Author**: critic-beth (opus, persistent; spanning fix-pack → 5C → `59e271c`)
**Anchor**: P5 complete; pre-P6 compact; P6 opens on Day0 split + DT#6 + B055.

## 1. Structural antibody categories closed across 5A/5B/5C/fix-pack

Four repeating shapes — each closes a category, not an instance:

- **Authority inversion at seams.** 5A: `PortfolioState.authority` stamped by DB writer (not payload self-report). 5B: `validate_snapshot_contract` moved from test-only → runtime ingest gate. 5C: `_forecast_reference_for` emits `decision_reference_source="forecasts_table_synthetic"` computed from context, not from whatever string the snapshot claimed. Rule: **module that computes authority wins over the payload that self-reports it.**
- **Per-spec cross-checks at multi-metric seams.** Fix-pack: `_process_snapshot_v2(*, spec)` raises on `data_version != spec.allowed_data_version`. 5C: `rebuild_v2(spec=…)` + parametrized SQL metric filter. `CANONICAL_DATA_VERSIONS` frozenset positive-allowlist. Triad invariant (`data_version + temperature_metric + physical_quantity`) cross-checked at every v2 write/read.
- **Metric-aware cache keys.** `_decision_ref_cache: dict[tuple[str,str,str], …]` — any cache whose key omits `temperature_metric` is a cross-metric leakage path. Pattern repeats anywhere a dict/frozenset indexes by `(city, target_date)`.
- **Lazy/callsite guards over module-level exits.** R-AT: `observation_client.py:87` `SystemExit` moved from import-time to `_require_wu_api_key()` callsite. Test topology unblocked (pre-fix full suite was "no tests ran" at collection).

### P6 unlocked next
Day0 split (`Day0HighSignal` / `Day0LowNowcastSignal`) opens two new antibody categories:

1. **Router-at-construction**: per critic-alice's 5B→5C note, a Day0 router accepting `metric_identity` and refusing ambiguous inputs at construction prevents silent cross-track routing. Authority inversion shape applied to a factory.
2. **Signal-path type-branching**: the `is_low()` branch in `day0_window.py:67` (returns `slice_data.min(axis=1)`) must be lifted OUT of the shared class into a metric-specific subclass. Mixing `min/max` dispatch inside one class is a polarity-swap footgun — same shape as testeng-grace's R-AG importability-only warning.

## 2. L0.0 peer-not-suspect — field report

**Four+ disk-vs-report disagreements this phase, zero discipline findings filed, zero false escalations.** Hypothesis ordering (6-tier, alice's "deferred ruling" refinement adopted) worked every time:

- scout-gary "Target 6 already landed" → partial landing; refinement negotiation, not discipline.
- testeng-hank `store.py` "merge conflict" → stale-read artifact (tier 3).
- team-lead "14/14 GREEN" dispatch while exec-ida mid-revert → tier 1 concurrent-write. Stop-the-line raised, reconciled in <5min.
- team-lead final "10p+2xfail" dispatch while disk showed 12p+0xfail-spec-param-landed → tier 2 memory/report-state lag or tier 4 deferred ruling I didn't see.
- exec-juan's self-flag on `DIAGNOSTIC_REPLAY_REFERENCE_SOURCES` needing update → his own memory-lag (disk showed it already updated).

### P6 refinement proposal

**Add tier 2.5 — "mid-edit concurrent-tool-output artifact."** Same turn, first Grep shows state A at L44, parallel sed shows state B at L44 — disk is thrashing because someone (or a linter) is actively editing. Not quite concurrent-write (same second), not quite tool artifact (tool is correct, target file is moving). Symptom: two read-tool outputs in the same turn disagree. Response: wait 15s, re-grep from single authoritative source, resolve. Hit this once in 5C mid-review.

Also worth noting: **L0.0 applies to team-lead dispatches too.** Team-lead's described disk state drifted from reality three times this phase. Each resolved via fresh grep on critic side. Peer-not-suspect for lead → critic is the same default: they're not gaslighting, they're reading from a stale snapshot.

## 3. Fixture-vs-production divergence — forward-log

CRITICAL-1 in 5C was **invisible to tests and almost shipped to production**: `_forecast_rows_for` SQL at L258 added `AND temperature_metric = ?` against the `forecasts` table. Production DDL (`src/state/db.py:651`) lacks that column; `sqlite3.OperationalError: no such column` → swallowed by `except Exception: return []` → HIGH replay silently skips every settlement.

Why tests didn't catch: `test_phase5c_replay_metric_identity.py::_make_replay_db:47` builds a parallel `forecasts` schema WITH `temperature_metric`. Testeng knew (comment at L256) but fixture didn't match prod. This is Fitz Constraint #4 exactly (data provenance), surfaced at the test-infrastructure layer.

**P6 forward-log for Day0 tests**: every new Day0 test fixture must either (a) import production DDL from `src/state/db.py` / `src/state/schema/`, or (b) carry a provenance comment + assertion `# Fixture schema MUST match production as of <date>`. Proposing: add a pytest conftest helper `assert_fixture_schema_matches_production(conn, table)` that runs `PRAGMA table_info` on both and diffs. One-pass antibody; catches the entire class.

## 4. P6 hazards — the structural risk in splitting Day0Signal

Three hazards ordered by blast radius:

- **`evaluator.py:825` co-landing imperative (CRITICAL)**: `member_mins_remaining=remaining_member_extrema` passes the MAX array as MIN input. Dead-but-live code today (guarded by `NotImplementedError` in `Day0Signal.__init__`). **When the guard is removed without simultaneously fixing L825, any low-track Day0 candidate silently uses max values as min**. Scout-finn's learning doc flags this as category-B death trap. Must land in one commit.
- **DT#6 graceful-degradation shape**: `load_portfolio` authority-loss cannot `RuntimeError` the whole cycle. State machine needed: `RUNNING | DEGRADED | PAUSED` transitions tied to SLO tolerance windows. B055's `TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE = timedelta(hours=2)` gets absorbed here. Risk: DT#6 is a 3-state machine but I've seen 5-state and 7-state variants — pin the grammar BEFORE the code lands, or executors will drift.
- **Day0LowNowcastSignal vs Day0HighSignal shared tooling**: `remaining_member_maxes_for_day0` at `day0_window.py:21` already handles both tracks via `is_low()` branch. Risk: P6 executors either (a) fork it into two functions (divergence later) or (b) rename it (callers break). Correct answer: rename to `remaining_member_extrema_for_day0`, keep the `is_low()` dispatch, let both Day0 subclasses call it. Scout-finn flagged this explicitly; reinforce in P6 brief.

## 5. Fresh-critic inheritance (if I'm replaced)

**Inherit (day-1 context, don't re-derive)**:
- L0.0 peer-not-suspect 6-tier (methodology file has it now).
- Four antibody categories above (this doc).
- Triad invariant + `CANONICAL_DATA_VERSIONS` frozenset pattern.
- **Mid-edit tool-output artifact hypothesis** (tier 2.5 above — needs methodology update).
- Team-lead dispatches may lag disk by minutes; always fresh-grep before acting on a described state.
- Fix-pack PASS-verdict-blind-spot: cross-check obsolete tests in OTHER files when a fix-pack commits a contract inversion. Fix-pack commit `3f42842` broke `test_phase5a_truth_authority.py`; I missed it until 5C review surfaced it. **Post-commit full-suite diff is non-negotiable, not "regressions handwave."**

**Re-derive (each phase)**:
- Current `git log -5`, `git diff --stat`, full pytest count vs prior-phase baseline.
- What's in the current phase's scope doc vs what's on disk — drift is common and usually benign.
- Executor-level intent: always read the actual diff, not the executor's summary of the diff.

---

*Context-investment returned to the record. P5 complete at `59e271c`. Standing by for P6 onboarding.*
