# Midstream Remediation — Work Log

## W0 — 2026-04-23 — packet open

- Packet opened: `docs/operations/task_2026-04-23_midstream_remediation/`.
- Authority source: `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md`
  (36-slice v2 plan, signed by pro-vega + con-nyx).
- Executor: team-lead (this session).
- Long-lasting critic: `con-nyx`.
- Upstream co-tenant: parallel agent working data-readiness
  (`docs/operations/task_2026-04-23_data_readiness_remediation/`).
- Initial state verified by independent probe:
  - Production `position_current`: 31 columns, canonical (no drift).
  - `config/provenance_registry.yaml`: already exists (516 lines, real
    content) — T1.b scope shifts from "create" to "audit + skipif removal".
  - `src/strategy/selection_family.py:92` — `make_family_id()` is
    deprecated; zero non-deprecated call sites in `src/` or `scripts/`.
  - `src/engine/cycle_runtime.py:703` — `execute_discovery_phase(..., *, env: str, deps)` canonical.
  - `src/engine/cycle_runtime.py:209` — `materialize_position(..., *, state: str, env: str, ...)` canonical.
  - Zeus venv at `.venv/` — `yaml 6.0.3`, `pytest 9.0.2`.

## Slice rows (appended per slice)

| Slice | Status | Commit | Critic | Date |
|---|---|---|---|---|
| W0 packet open | closed | `ec78c2f` | skipped (doc-only, planning-lock GREEN) | 2026-04-23 |
| T4.0 persistence design rev2 | closed | `9365b20` | surrogate critic CLEAR (Option E); con-nyx informed | 2026-04-23 |
| T7.b AST-walk guard | closed | `beea8a9` | 1/1 pass on first run; zero pre-state violators (grep-verified) | 2026-04-23 |
| T1.a 15-file header wave | closed | `67b5908` | narrow-scope regression 19/344/34/1 matches pre-T1a baseline exactly (zero delta from comment-only change); verified via git stash | 2026-04-23 |
| T1.b provenance_registry skipif cleanup | closed | `4943d0d` | 4 stale `skipif(not REGISTRY_YAML.exists(), ...)` markers removed; 19/19 test_provenance_enforcement tests still pass | 2026-04-23 |
| T3.1 execute_discovery_phase 5-caller env kwarg fix | closed | `716bfdd` | 6 TypeError failures → pass (2 day0_runtime + 2 discovery_phase_entry_path + 2 discovery_phase_records); zero new failures; delta-direction on 3 modified files: 28F→22F, 166P→172P | 2026-04-23 |
| T3.3 position_current ALTER TABLE canonical-column backfill | closed | `36f0189` | surrogate critic (code-reviewer@opus) CLEAR; fixes test_kernel_schema_adds_token_identity_columns; planning-lock GREEN; delta: -1 failure, 0 new failures; INV-14 runtime enforcement preserved per grep of direct-SQL writers | 2026-04-23 |
| T3.2 canonical_projection fixture patch | closed | pending | one-line category fix (Fitz C1); `test_architecture_contracts.py` 9F→1F; surrogate critic CLEAR; con-nyx dispatched; remaining 1F is unrelated `_Logger.warning` AttributeError | 2026-04-23 |
| T3.2b canonical schema alignment antibody | closed | `566a48f` | plan AST-walk premise vacuous (no dict builders in projection.py); pivoted to 3 structural-alignment tests; 3/3 pass; surrogate critic CLEAR with regex hardening polish adopted | 2026-04-23 |
| T3.2 canonical_projection fixture patch | closed | `566a48f` | one-line category fix bundled in same commit as T3.2b per slice-pairing rationale | 2026-04-23 |
| T7.a test_fdr_family_key_is_canonical activation | closed | N/A (no code change) | **slice already done pre-session**: test at L189 is unskipped and passing 1/1; plan's "skip at L67" citation was wrong (L70 is a different test, `test_no_high_low_mix_in_platt_or_bins`, NC-12 territory for T1.d); test body already covers INV-22 scope separation, determinism, metric discrimination | 2026-04-23 |

## T7.a — verification notes (2026-04-23)

Fourth plan-premise correction in this packet. Plan said:
> "Activate `test_fdr_family_key_is_canonical` — remove `pytest.skip` at
> `tests/test_dual_track_law_stubs.py:67` + verify body matches
> `make_hypothesis_family_id` + `make_edge_family_id` signatures"

Reality (grep-verified 2026-04-23):
- Line 67 is a comment (`# NC-12 / INV-16`).
- Line 70 is `pytest.skip("pending: enforced in Phase 7 rebuild")` inside
  `test_no_high_low_mix_in_platt_or_bins` (a DIFFERENT test, NC-12 /
  INV-16 enforcement for Phase 7). That stub is T1.d territory.
- `test_fdr_family_key_is_canonical` is at L189 with no skip decorators
  and a full body covering:
  - hypothesis vs edge family ID scope separation
  - determinism within each scope
  - metric discrimination (HIGH ≠ LOW family IDs per S4 R9 P10B)
- Verified passing: `pytest -q tests/test_dual_track_law_stubs.py::test_fdr_family_key_is_canonical`
  → `1 passed in 0.03s`.

Slice scope is satisfied without code changes. Receipt documents the
verified-already-done state. Per memory rule L21, "activate" was the
wrong verb in the plan — the test was already active. This is being
recorded as CLOSED with NO-OP commit (this work_log + receipt update
only).

The residual `test_no_high_low_mix_in_platt_or_bins` skip stub is
flagged for T1.d closure.

## T3.2 — execution notes (2026-04-23)

Scope: add `"temperature_metric": "high"` to `_canonical_projection()`
helper at `tests/test_architecture_contracts.py:125-157`. One line,
resolves ≥5 failing tests via single category patch (Fitz C1).

Pre-T3.2 state (test_architecture_contracts.py, post-T3.3): ~9 failures
with `ValueError: projection missing fields: ['temperature_metric']`.

Post-T3.2: **1 failed / 70 passed / 22 skipped**. Remaining failure is
`test_cycle_runtime_entry_dual_write_helper_skips_when_canonical_schema_absent`
→ `AttributeError: '_Logger' object has no attribute 'warning'` at
`src/engine/cycle_runtime.py:291`. Unrelated to fixture drift.

Surrogate critic (code-reviewer@opus): CLEAR. Confirmed `_canonical_event()`
correctly NOT patched — `CANONICAL_POSITION_EVENT_COLUMNS` does not
include `temperature_metric`. Fitz C1 compliance on "one fixture × five
callers = one patch" verified.

## T3.2b — execution notes (2026-04-23)

**Plan-premise correction (third in this packet):** fix-plan v2 T3.2b
scope was "AST-walk `src/state/projection.py` builders". Grep-verified
`src/state/projection.py` has ZERO dict-returning functions — all
functions return `None`, `tuple`, `set`, or raise. The AST-walk target
is vacuous.

Pivoted to structural-alignment antibodies that catch the drift
category responsible for T3.2 failures:
- Test 1: `temperature_metric in CANONICAL_POSITION_CURRENT_COLUMNS`
  (Python-tuple drift)
- Test 2: kernel SQL CREATE TABLE declares `temperature_metric` (SQL
  migration drift)
- Test 3: every `CANONICAL_POSITION_CURRENT_COLUMNS` entry appears in
  kernel SQL CREATE TABLE (constant ↔ schema alignment) — the real
  category-immunity antibody per Fitz C1

New file `tests/test_canonical_position_current_schema_alignment.py`
passes 3/3. Carries dated provenance header per CLAUDE.md rule.

Surrogate critic adopted: regex hardening from `\n\)\s*;` to `\)\s*;`
(drops `\n` anchor for robustness against future migration reformatting).

Critic flagged 3 low-severity future-slice antibody targets (F1-F3):
- `CANONICAL_POSITION_EVENT_COLUMNS` three-way drift (test ↔ ledger ↔ kernel SQL)
- `LifecyclePhase` Python enum ↔ `phase` CHECK constraint
- `event_type` scatter without central Python enum

All noted for potential future slices — not blocking T3.2b.

## T3.3 — execution notes (2026-04-23)

Fix-plan premise correction (third such correction in this packet, after
T4.0 and implicit T3.2 overlap):

**Plan said**: "Canonical `position_current` schema bootstrap fix. Diff
`apply_architecture_kernel_schema()` against
`CANONICAL_POSITION_CURRENT_COLUMNS`; add missing columns to bootstrap
SQL."

**Reality (factually verified)**: the kernel migration at
`architecture/2026_04_02_architecture_kernel.sql:82-129` CREATE TABLE
already declares all 31 canonical columns. A fresh DB probe via
`sqlite3 :memory: + apply_architecture_kernel_schema(conn)` yields
exactly 31 columns, zero missing, zero extra. **Bootstrap is already
correct.**

The actual defect is in the LEGACY-DB migration path (not fresh
bootstrap): when `apply_architecture_kernel_schema` runs on an
existing-but-pre-kernel `position_current` table, the
`CREATE TABLE IF NOT EXISTS` no-ops, and the explicit ALTER TABLE
loop at L147-149 only adds 3 token columns. Missing canonical columns
like `temperature_metric` remain absent, causing
`assert_canonical_transaction_schema` at L150 to raise.

### Fix

Expanded the ALTER TABLE loop to iterate over all
`CANONICAL_POSITION_CURRENT_COLUMNS` (not just the 3-tuple), adding
each missing column with plain TEXT affinity. Cached the column set
once before the loop (idempotency correctness confirmed by critic).
Multi-line comment explains the design trade-off (TEXT affinity +
runtime `require_payload_fields` guard).

### Critic review (surrogate code-reviewer@opus)

**Verdict: CLEAR — APPROVE for commit.**

Findings summary:
- F1 (low, non-blocking): TEXT affinity for REAL-typed legacy columns —
  documented trade-off; SQLite dynamic typing preserves values.
- F2 (low, non-blocking): INV-14 CHECK constraint lost for
  ALTER-migrated `temperature_metric`, but runtime guard at
  `src/state/projection.py:50-53` catches invalid writes BEFORE the
  DB; critic greped `nuke_rebuild_projections.py:124` as the only
  direct-SQL mutator and confirmed it touches only `phase`+`updated_at`.
- F3, F4: positive observations on idempotency and inline documentation.

All seven rubric axes: CORRECTNESS pass, TYPE_SEMANTICS acceptable,
IDEMPOTENCY correct, ORDERING safe, INV_14_CONSEQUENCE preserved,
PLANNING_LOCK verified green (path correction noted), TEST_EXPECTATION
fixed target green, no new regression.

### Regression evidence

- `pytest -q tests/test_architecture_contracts.py::test_kernel_schema_adds_token_identity_columns_to_existing_position_current`
  → passes (was failing pre-T3.3).
- `pytest -q tests/test_architecture_contracts.py::test_kernel_schema_migrates_existing_token_suppression_reason_check`
  → passes (not regressed).
- `pytest -q tests/test_architecture_contracts.py::test_apply_architecture_kernel_schema_bootstraps_fresh_db`
  → still fails, but the failure is on `_canonical_projection()` missing
  `temperature_metric` field in the fixture — T3.2 scope, not T3.3.
  My T3.3 change is orthogonal.

## T3.1 — execution notes (2026-04-23)

Scope per plan: "patch ALL 7 execute_discovery_phase callers in
tests/ + audit materialize_position callers". L20 grep-gate resolved
the true call-site inventory to **5 patches**, not 7:

- `tests/test_day0_runtime_observation_context.py:41` (MISSING env)
- `tests/test_day0_runtime_observation_context.py:84` (MISSING env)
- `tests/test_runtime_guards.py:717` already has `env="paper"` — NO PATCH
- `tests/test_runtime_guards.py:4877` (MISSING env) — DAY0_CAPTURE mode
- `tests/test_runtime_guards.py:5027` (MISSING env) — OPENING_HUNT mode
- `tests/test_architecture_contracts.py:3555` (MISSING env) — UPDATE_REACTION
- `tests/test_phase10e_closeout.py:352` resolved to a FUNCTION NAME,
  not a call — the grep hit was on `def test_r_df_5_*`, not on an
  actual `execute_discovery_phase(...)` invocation. No patch needed.
- `tests/test_runtime_guards.py:2010, 2057` (materialize_position)
  already carry `state="entered", env="paper"` — NO PATCH.

Applied uniform `env="paper"` to all 5 missing sites. Paper is the
safe test-context value per the pattern already established at
`test_runtime_guards.py:717`. Real production uses `env="live"` for
live runs.

Regression evidence (delta-direction per memory L28):
- Targeted tests:
  - `pytest -q tests/test_day0_runtime_observation_context.py`
    → `4 passed` (was 2 failed, 2 passed)
  - 4 T3.1-target tests (entry_path × 2, discovery_phase_records × 2)
    → `4 passed`
- Broader 3-file regression:
  - Pre-T3.1 baseline: 28 failed / 166 passed / 22 skipped
    (normalized to same 3 files as post-run by subtracting
    test_phase10e_closeout.py's 13 passing tests from the 4-file
    pre-run)
  - Post-T3.1: **22 failed / 172 passed / 22 skipped**
  - Delta: **-6 failures, +6 passes, 0 new failures**
- Remaining 22 failures on these files are out-of-T3.1-scope:
  T3.3 schema bootstrap (apply_architecture_kernel_schema,
  kernel_schema_adds_token_identity_columns, cycle_runtime_entry_dual_write_helper),
  T2.g fail-closed (test_fdr already handled), INV-08 atomicity,
  unrelated INV-14 projection drift → owned by T3.2, T3.2b, T3.3, T2.g.

## T1.b — execution notes (2026-04-23)

Scope per plan: "content audit of `config/provenance_registry.yaml`
(already exists at 516 lines) + remove redundant `skipif` markers in
`test_provenance_enforcement.py`."

Findings:
- `config/provenance_registry.yaml` exists and is populated (≥ 516
  lines, `kelly_mult` at L26, `market_fusion.TAIL_ALPHA_SCALE` present,
  required fields `declared_target, data_basis, validated_at,
  replacement_criteria` all present per registry shape).
- All 4 tests in `TestAllStrategyConstantsRegistered` carry TWO
  skipif markers:
  1. `skipif(not REGISTRY_YAML.exists(), reason="Registry YAML not yet created")` — **inaccurate today**: file exists. Removed by T1.b.
  2. `skipif(not HAS_YAML, reason="PyYAML not installed")` — **legitimate environmental check**: PyYAML may be absent in minimal Python envs; kept.
- L34 inside `_load_registry_yaml()` helper also references
  `REGISTRY_YAML.exists()` — kept (legitimate fallback, returns empty
  dict when file absent).

Per memory rule L21, this is **REMOVING STALE SKIP MARKERS**, not
"activating" tests — all 4 guarded tests already ran in the pre-T1.b
baseline because both skip conditions evaluated False. The slice
hardens the test against future regressions (if the registry file is
deleted, tests will now fail loudly instead of silent-skipping).

Regression evidence: `pytest -q tests/test_provenance_enforcement.py`
→ `19 passed in 0.15s` (matches pre-T1.b baseline exactly).

## T1.a — execution notes (2026-04-23)

Prepended a 3-line dated provenance header to each file in the
15-file midstream guardian panel. Created dates retrieved via
`git log --follow --reverse --format=%cs -- <file> | head -1`.
Authority basis uniform across the panel: "midstream verdict v2
2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a
midstream guardian panel)". Per-test specific INV references remain
inside each file's own module docstring (unchanged).

Regression verdict (L28 delta-direction, not absolute counts):
- **narrow scope (15 panel files)**: `.venv/bin/python -m pytest -q
  <15 files>` → `19 failed, 344 passed, 34 skipped, 1 xfailed` —
  matches the pre-T1.a baseline documented in the midstream trust
  verdict (K2 findings). **Zero delta.** Confirms comment-only change
  had no test-behavior impact.
- **full-suite probe**: `.venv/bin/python -m pytest -q tests/` →
  `144 failed, 2079 passed, 90 skipped, 1 xfailed`. Out-of-scope for
  T1.a closure — these failures exist independently of the 15-file
  panel and are attributable to pre-existing repo state and/or the
  concurrent upstream data-readiness agent. Noted for context; not a
  T1.a regression.

Files changed: 15 test files (3-line header prepend each), plus this
work_log.md + receipt.json. `tests/AGENTS.md` is also modified in the
tree but is **not mine** (co-tenant's work); NOT staged for this
commit.

## T4.0 — PROPOSAL notes (2026-04-23, rev2)

### Premise correction
Fix-plan v2 T4.0 row states "`decision_log` row keyed on
`decision_snapshot_id` (option b; no schema migration)". Grep-verified
at `src/state/db.py:528-536` shows `decision_log` has 6 columns
(`id, mode, started_at, completed_at, artifact_json, timestamp`) with
**no `decision_snapshot_id`**. Premise is false.

### Revision history
- **rev1 (earlier today)**: Picked Option B (new column
  `decision_evidence_json TEXT` on `trade_decisions`). Reviewed by
  surrogate critic (code-reviewer@opus).
- **rev2 (this entry)**: Integrates surrogate critic findings F1-F3
  + missed Option E. Recommendation flipped to Option E.

### Surrogate critic findings integrated
- **F1 HIGH**: rev1 cited `evaluator.py:724, 1307` as Decision handoff
  sites. Actual: FDR-filter logic. `EdgeDecision` (not `Decision`)
  is constructed at L753, 778, 803, 815, 832, 842, 866, 882, 901, 912.
- **F2 MED**: rev1 cited `db.py:2320, 2468` as INSERT sites. Actual:
  VALUES-tuple entries; INSERTs at L2325, L2473.
- **F3 HIGH**: rev1 asserted atomicity between `trade_decisions`
  INSERT and `position_events` append. Grep-verified WRONG via
  `cycle_runtime.py:1115-1140`'s explicit SAVEPOINT-then-dual-write
  comment. `trade_decisions` INSERT lives inside
  `SAVEPOINT sp_candidate_*`; `position_events` append
  (`append_many_and_project`) runs after SAVEPOINT release with its
  own `with conn:` sub-transaction. **Separate transaction boundaries;
  torn-state window exists today for `epistemic_context_json` and
  `edge_context_json`.** Option B inherits this window.
- **Missed Option E**: Piggyback on existing
  `ENTRY_ORDER_POSTED.payload_json` — `payload_json` NOT NULL TEXT
  column at `position_events` col 17 already exists (PRAGMA-verified).
  Atomic with canonical append. No schema change. No INV-07 expansion.

### Recommendation flipped: Option E

Option E is structurally superior on three axes:
1. Atomicity: evidence lands in the same canonical
   `append_many_and_project` transaction.
2. Zero schema migration: `payload_json` already exists.
3. Category immunity per Fitz C1: future decision-contract evidence
   extends the same payload-sidecar pattern.

### Planning-lock classification
REQUIRED for T4.1 per delivery.md §5. "Additive/non-breaking" is
not an exemption. Option E keeps T4.1 out of `src/state/db.py` schema
but still touches `src/engine/evaluator.py` + `src/state/lifecycle_events.py`
(or equivalent emission helper) — both under planning-lock trigger
zones.

### contract_version tag
ADOPT. `DecisionEvidence.to_json` emits
`{"contract_version": 1, "fields": {...}}`.
`from_json` raises `UnknownContractVersionError` on unknown versions.

### Open questions for con-nyx (durable critic)
1. Idempotency when `ENTRY_ORDER_POSTED` is retried: key on
   `decision_id`?
2. Helper location: `decision_chain.py` vs new
   `decision_evidence_persistence.py`?
3. Does `query_position_events` accept `runtime_trade_id` or
   `position_id`?
