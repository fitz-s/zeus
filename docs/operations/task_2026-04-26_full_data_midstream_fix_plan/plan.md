# Zeus Full Data + Midstream Fix Plan

Created: 2026-04-26
Last audited: 2026-04-26
Authority basis: PR #19 review workbook (`docs/to-do-list/zeus_full_data_midstream_review_2026-04-26.md`), `zeus/AGENTS.md`, `docs/operations/current_state.md` (mainline = `midstream_remediation`, P4 BLOCKED)
Status: planning evidence; not authority. No production DB / schema mutation in any slice of this plan.
Branch: `claude/zeus-full-data-midstream-fix-plan-2026-04-26` (off `origin/midstream_remediation@6499f9c`)
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-fix-plan-20260426`
Workbook origin: PR #19 (https://github.com/fitz-s/zeus/pull/19), author `app/copilot-swe-agent`, opened 2026-04-26
Companion PR target: `midstream_remediation` (same as PR #19)

---

## 0. Scope statement

This packet is a PLAN, not implementation. It:

- Decomposes the 10 findings + 16 todos in PR #19's workbook into K structural decisions where K << 10 (Fitz Constraint #1: structural decisions > patches).
- Splits remediation into narrow, Zeus-governance-compliant slices.
- Identifies blockers that are operator/data/governance, not code.
- Does NOT mutate code or DB. Each implementation slice will be filed as a separate packet under `docs/operations/task_2026-04-26_<slice>/` (or later dates).

**Out of scope for this packet:**

- Implementing any of the slices below.
- Any DB row mutation, schema change, view creation, quarantine, or `INSERT OR REPLACE` overhaul.
- Re-running P4 readiness, TIGGE manifest validation, HK/HKO governance, or DST historical rebuild — these are operator/data tracks.
- Modifying authority, lifecycle, or governance documents.

---

## 1. Why this packet exists

PR #19's workbook is operational evidence (todo list), not authority. It enumerates 10 important findings and 16 follow-up todos but does NOT specify:

- Which findings collapse into shared structural decisions.
- The order of remediation.
- The narrow-packet slicing required by Zeus governance (planning-lock + per-packet receipts).
- The relationship-test contracts (Fitz: relationship tests before implementation).
- The acceptance criteria per slice.
- The blast radius and dependency graph between slices.

This plan provides those.

---

## 2. Citation grep-gate (workbook → current source)

Per memory `feedback_grep_gate_before_contract_lock.md` (L20) and Zeus AGENTS.md, every plan citation must be fresh-grep verified within 10 minutes of write. Verification log: see `evidence/grep_gate_log.md`.

Summary:

| Workbook finding | Citation in workbook | Verified at | Status |
|---|---|---|---|
| F5: legacy refit reads metric-blind pairs/counts | `src/calibration/manager.py` calls `get_decision_group_count(conn, cluster, season)` and `get_pairs_for_bucket(..., bin_source_filter="canonical_v1")`; `src/calibration/store.py` lacks `temperature_metric` filter on those functions | `manager.py:193,275`; `store.py:207,292` | CURRENT |
| F6: maturity gate vs refit input subset mismatch | `get_decision_group_count()` counts all VERIFIED groups in a cluster/season; `_fit_from_pairs()` reads only `canonical_v1` rows | `store.py:292` (count is metric-blind), `manager.py:275` (refit uses `bin_source_filter="canonical_v1"` only) | CURRENT |
| F7: snapshot stamp `or "high"` fallback | `_store_ens_snapshot()` uses fallback `"high"` when `ens.temperature_metric` is missing | `evaluator.py:1818` (double-getattr fallback `or "high"`) + L497, L537, L674 (`candidate.temperature_metric or "high"`) + L91 (CandidateContext default `temperature_metric: str = "high"`) | CURRENT, ENRICHED — fallback exists in 5 sites total, not 1 |
| F8: rescue authority returns "high"+UNVERIFIED | `resolve_rescue_authority()` returns `("high", "UNVERIFIED", ...)` when `temperature_metric` missing | `chain_reconciliation.py:49` exact match | CURRENT |
| F9: terminal-state set duplicated | `cycle_runner.py` mirrors terminal states from `portfolio.py` in a local frozenset | `portfolio.py:962` `{settled, voided, admin_closed, quarantined}` (canonical) + `cycle_runner.py:54` `{settled, voided, admin_closed, quarantined}` (frozenset mirror, agrees with portfolio) + `cycle_runner.py:341` `{settled, voided, admin_closed, economically_closed}` (THIRD inline set literal — **DISAGREES with canonical**: includes `economically_closed` (NOT terminal per `LEGAL_LIFECYCLE_FOLDS`) and excludes `quarantined` (IS terminal per folds)) | CURRENT, ELEVATED — finding 9 is a **semantic bug**, not duplication: the third set literal at L341 contains the wrong members |

**Premise rot: 0% (within 10-minute window). Re-verified after rebase onto `origin/main` 2026-04-26.** All workbook citations stand. Finding 9 is more severe than the workbook described (3 sources of truth + the third disagrees on membership = real semantic bug).

The remaining findings (F1–F4, F10) are governance/operator/data items whose verification is via `current_state.md` and `known_gaps.md`, not source grep. Confirmed via `current_state.md`: P4 mutation BLOCKED, `ensemble_snapshots_v2`/`market_events_v2`/`settlements_v2`/`calibration_pairs_v2` empty, `WU_API_KEY` missing in current shell, auto-pause tombstone is operator decision.

**Post-rebase precedent discovery.** `scripts/semantic_linter.py:628 _has_settlements_metric_predicate` already enforces metric predicates on `settlements` reads (P3 4.5.A landed work). The slices A1+A2 in this plan extend the SAME antibody pattern to `calibration_pairs` reads. Slice A1 has a natural follow-on opportunity to add `_has_calibration_pairs_metric_predicate` to the same linter (filed as candidate slice A1b).

---

## 3. Structural decomposition (Fitz Constraint #1)

Reframe: the 10 findings are NOT 10 independent fixes. They are symptoms of K structural decisions, K << 10.

| Decision | Findings collapsed | Type | Code slices | Where intent currently lives |
|---|---|---|---|---|
| **A. Calibration metric identity is required, typed, non-defaultable at every interface** | F5, F6, F7, F8 | Math + Architecture | A1, A2, A3, A4 | docstrings + comments — NOT structure |
| **B. Lifecycle terminal predicate has single ownership** | F9 | Architecture (refactor) | B1 | three places: `portfolio.py:962`, `cycle_runner.py:54`, `cycle_runner.py:341` |
| **C. Operator/data trust gates are sequenced, evidence-gated, not code-blocked** | F1, F2, F3, F4, F10 | Governance + Data | out-of-code | `current_state.md`, `known_gaps.md`, `current_source_validity.md` |

10 findings → 3 decisions + 5 narrow code slices + a parallel C-track. The 5 governance items (Decision C) are NOT code packets; they are evidence-gathering tracks tracked separately.

This is Fitz Constraint #1 in action: enumerate-and-patch produces 10 PRs and re-occurrence at every patch boundary; structural reframing produces 5 small code packets and permanent immunity.

---

### 3.A Decision A: Calibration metric identity is required, typed, non-defaultable

**Failure pattern.** `temperature_metric` is a string with a silent default of `"high"`, applied at 4 different code seams. The infrastructure to thread it (`MetricIdentity` typed atom at `evaluator.py:361 _normalize_temperature_metric`, manager `temperature_metric` param at `manager.py:128,156,165,267`) ALREADY EXISTS but does not reach the leaf calls. Result: data computed under high-track law can be silently mixed into low-track training, and vice versa. The intent is encoded in **docstrings and code comments** (`store.py` K4 doc; `chain_reconciliation.py:33` "downstream analytics can filter on authority='VERIFIED'") rather than in **structure** — Fitz Constraint #2 violation (translation loss to docs vs code).

**Structural fix.** Make metric a non-defaultable, typed positional argument on the leaf retrieval/store/snapshot/rescue interfaces. Make calling without it a `TypeError`, not a silent default. The wrong code becomes unwritable, not just discouraged.

**Findings resolved:**

- **F5** (legacy refit reads metric-blind pairs/counts): signature change on `get_pairs_for_bucket` + `get_decision_group_count` requires metric.
- **F6** (maturity gate vs refit input subset mismatch): both consume the same metric-aware count/retrieval.
- **F7** (snapshot stamp `or "high"` fallback): replaced with fail-closed assertion at writer + at `CandidateContext` construction.
- **F8** (rescue authority "high" default): retain backward-compatible default but enforce consumer-side strict filter via relationship test that learning paths reject `authority != "VERIFIED"`.

#### Slice A1 — Add optional metric kwarg to calibration store (in-place extension)

**DESIGN REVISION (post-rebase 2026-04-26)**: original plan called for sibling `_v2` functions. After re-reading store.py, both target functions ALREADY accept additive kwargs (`authority_filter`, `bin_source_filter`); adding `metric` as a third additive kwarg in-place is cleaner than duplicating function bodies. v2 sibling deferred to a future restrictive-typed packet if needed.

Scope (additive, lowest blast):
- `src/calibration/store.py` only.
- Tests in `tests/test_calibration_store_metric_required.py` (NEW).

Change shape:
1. Extend `get_pairs_for_bucket(conn, cluster, season, authority_filter='VERIFIED', bin_source_filter=None, *, metric: Literal["high","low"] | None = None)` — add keyword-only `metric` param defaulting to `None` (preserves existing behavior). When `metric` is provided, append `AND temperature_metric = ?` to all three SQL branches (any-filter, no-auth-column, VERIFIED-with-auth) and pass metric in params.
2. Same extension for `get_decision_group_count(conn, cluster, season, authority_filter="VERIFIED", *, metric: Literal["high","low"] | None = None)` — applies to both branches (any-or-no-auth-column and VERIFIED-with-auth).

Relationship tests (BEFORE implementation, per Fitz):
- "Querying calibration_pairs for `metric='high'` must never return rows where `temperature_metric='low'`" — assert against fixture DB seeded with mixed rows.
- "Querying decision_group_count for `metric='low'` must never include groups whose only outcomes are HIGH" — same fixture.
- "Backward compatibility: `metric=None` (default) returns identical results to current callers" — fixture comparison.

Function tests (after impl):
- Function filters by `(metric, bin_source, authority)` correctly across all 3 SQL branches × both metrics.
- `metric` kwarg is keyword-only (positional invocation raises TypeError).

Acceptance:
- Function extends with passing relationship + function tests.
- All existing call sites still work without modification (additive only).
- No DB mutation, no schema change.

Blast radius: low (additive only). All 4 known internal call sites (manager.py L193, L275; harvester etc.) keep working; only manager passes `metric` explicitly in slice A2.

#### Slice A2 — Switch `manager.py` to v2 metric-aware calls

Scope: `src/calibration/manager.py` only.

Change:
- `manager.py:193`: `n = get_decision_group_count_v2(conn, cluster, season, metric=metric_identity)`.
- `manager.py:275`: `pairs = get_pairs_for_bucket_v2(conn, cluster, season, metric=metric_identity, bin_source_filter="canonical_v1")`.
- Construct `metric_identity = MetricIdentity(temperature_metric=temperature_metric)` near function entry from the existing `temperature_metric: Literal["high","low"]` param.

Relationship test (NEW, before impl):
- "When `_fit_from_pairs(temperature_metric=X)` runs, `get_decision_group_count_v2(metric=X)` and `get_pairs_for_bucket_v2(metric=X)` MUST be the only metric-bound retrieval calls" — assert via spy/mock that no metric-blind call escapes.
- "Maturity-gate count cardinality for metric=X must equal cardinality of refit input pairs for metric=X (modulo authority/data_version filter), not the union over both metrics" — fixture DB with mixed rows; numeric assertion.

Function tests:
- Existing calibration manager unit tests pass after wiring change.
- Calibration regression suite (whatever exists today) passes.

Acceptance:
- Per-metric maturity counts and pair retrievals come from the same metric-bound subset.
- Manager unit + integration tests pass.
- Calibration regression baseline diff = 0 new failures.

Blast radius: medium (touches live calibration manager). Must run full calibration regression.

#### Slice A3 — Snapshot stamping fail-closed

Scope: `src/engine/evaluator.py` (`_store_ens_snapshot` at L1819 + L497, L537, L674 fallback patterns + L91 CandidateContext default + any additional construction sites discovered by grep).

Change:
- Replace `or "high"` fallbacks with explicit `MetricIdentity`-bound input. If a caller cannot supply a metric, the writer raises `ValueError("ENS snapshot writer requires temperature_metric; got None")` BEFORE INSERT.
- `CandidateContext`: drop `temperature_metric: str = "high"` default; require explicit field at construction.
- Audit grep for `CandidateContext(` to enumerate all construction sites; verify each passes metric explicitly. If any test fixture relies on the default, list those fixtures in `evidence/candidate_context_construction_sites.md` (in slice A3 packet) and either update the fixtures or annotate why omission is acceptable for that test.

Relationship tests (NEW, before impl):
- "An ENS snapshot row written without `temperature_metric` must not exist; the writer must raise BEFORE the SQL INSERT." — instrument the writer with a transactional rollback assertion.
- "A `CandidateContext` constructed without `temperature_metric` must fail at construction time, not at first use." — type/runtime assertion.

Function tests:
- Snapshot writer raises on missing metric; existing successful path still inserts correctly.
- All existing call sites updated.

Acceptance:
- Evaluator unit + signal regression tests pass after fixture updates.
- All `CandidateContext(` construction sites updated (audit grep returns zero implicit-default constructions).
- No silent default path remains in the evaluator's snapshot write.

Blast radius: medium-high (evaluator hot path). Must run full evaluator + signal + day0 regression. Likely surfaces 5–20 test fixture updates.

#### Slice A4 — Authority-strict learning consumers (relationship test)

Scope: `tests/test_authority_strict_learning.py` (NEW) + minimal consumer fix if a learning path is found that does not enforce `authority='VERIFIED'`.

Change:
- Audit each consumer of `rescue_events_v2`, `calibration_pairs`, `settlements` that participates in TRAINING/LEARNING (Stage-2 harvester learning at `src/execution/harvester.py`, Platt refit at `src/calibration/manager.py:_fit_from_pairs`, replay rebuild at `scripts/rebuild_calibration_pairs_canonical.py`, `scripts/rebuild_calibration_pairs_v2.py`). Verify each ignores `authority != 'VERIFIED'`.
- If any current consumer fails, fix that consumer (narrow scope, in same packet).
- Add relationship test that the contract becomes structural rather than docstring-level.

Relationship tests (NEW, before impl):
- "Insert `rescue_events_v2` row with `authority='UNVERIFIED'` → run Stage-2 learning → assert that row's metric was NOT used in calibration pair creation." — fixture-driven.
- "Insert `rescue_events_v2` row with `authority='VERIFIED'` → run Stage-2 learning → assert that row WAS used (positive control)." — fixture-driven.

Acceptance:
- New relationship test exists and passes.
- If a consumer was found broken, narrow fix lands in same packet.
- The contract `chain_reconciliation.py:33` docstring promise is now backed by an executable test, not a comment.

Blast radius: low (mostly test). Code change only if a consumer is found broken.

---

### 3.B Decision B: Lifecycle terminal predicate has single ownership (SEMANTIC BUG, not just duplication)

**Failure pattern, ELEVATED post-rebase 2026-04-26.** Terminal-state membership is defined THREE times AND THE THREE DEFINITIONS DISAGREE:

1. `portfolio.py:962` `_TERMINAL_POSITION_STATES = {"settled", "voided", "admin_closed", "quarantined"}` (canonical, matches `LEGAL_LIFECYCLE_FOLDS` ground truth).
2. `cycle_runner.py:54` `_TERMINAL_POSITION_STATES_FOR_SWEEP = {"settled", "voided", "admin_closed", "quarantined"}` (frozenset mirror, AGREES with portfolio).
3. `cycle_runner.py:341` `terminal_states = {"settled", "voided", "admin_closed", "economically_closed"}` (inline set literal, **DISAGREES with canonical** — includes `economically_closed` (NOT terminal per `LEGAL_LIFECYCLE_FOLDS["ECONOMICALLY_CLOSED"]` which transitions to SETTLED/VOIDED) and **excludes `quarantined`** (IS terminal per `LEGAL_LIFECYCLE_FOLDS["QUARANTINED"] = {QUARANTINED}`)).

Authority proof from `src/state/lifecycle_manager.py` `LEGAL_LIFECYCLE_FOLDS`: a phase is terminal iff `LEGAL_LIFECYCLE_FOLDS[phase] = {phase}` (only fold to itself). Per the table, 4 phases satisfy this: `SETTLED`, `VOIDED`, `QUARANTINED`, `ADMIN_CLOSED`. `ECONOMICALLY_CLOSED` does NOT satisfy this (folds to `{ECONOMICALLY_CLOSED, SETTLED, VOIDED}`).

**Behavioral consequence of `cycle_runner.py:341` bug** (smoke-test portfolio cap exposure-block branch):
- `economically_closed` positions are EXCLUDED from `open_cost_basis_usd` sum today, despite still carrying cost basis until on-chain settlement → exposure summary under-reports open exposure.
- `quarantined` positions are INCLUDED in `open_cost_basis_usd` sum today, despite being terminal off-path positions → exposure summary over-reports open exposure (though the `has_quarantine` block at L327 already independently blocks new entries, mitigating the entry-blocking impact).

**Structural fix.** Single owner of `is_terminal_state(state) -> bool` and `TERMINAL_STATES: frozenset[str]` in `src/state/lifecycle_manager.py`, **derived programmatically from `LEGAL_LIFECYCLE_FOLDS`** (so future enum/fold changes auto-update; the canonical set cannot be re-hardcoded incorrectly). All readers import the predicate. Site 3 (`cycle_runner.py:341`) gets a SEMANTIC FIX, not just a refactor — its behavior changes to match canonical.

#### Slice B1 — Centralize terminal predicate (with semantic fix at site 3)

Scope: `src/state/lifecycle_manager.py`, `src/engine/cycle_runner.py`, `src/state/portfolio.py`, plus new test file.

Change:
- Add `TERMINAL_STATES: frozenset[str]` and `def is_terminal_state(state: str) -> bool` to `src/state/lifecycle_manager.py`. **Derive `TERMINAL_STATES` programmatically from `LEGAL_LIFECYCLE_FOLDS`** by selecting phases whose fold equals `{phase}` — guarantees structural correctness, immune to future hardcoded-set drift.
- `portfolio.py:962`: replace `_TERMINAL_POSITION_STATES = frozenset({...})` with import from `lifecycle_manager.TERMINAL_STATES`. Verify the resulting set matches the literal we replace (it does: `{settled, voided, admin_closed, quarantined}`).
- `cycle_runner.py:54`: replace `_TERMINAL_POSITION_STATES_FOR_SWEEP` with same import. Verify set match (it does).
- `cycle_runner.py:341`: replace inline `terminal_states = {...}` with `is_terminal_state(...)` predicate call. **This is a behavior change**: `economically_closed` positions now counted in `open_cost_basis_usd`; `quarantined` positions now excluded.

Relationship test (NEW, before impl):
- "All three call sites MUST agree with `lifecycle_manager.is_terminal_state` for every `LifecyclePhase` member and for the 4 canonical terminal strings." — parametrized pytest.
- "`TERMINAL_STATES` derived from `LEGAL_LIFECYCLE_FOLDS` MUST equal the manually-asserted canonical set `{settled, voided, admin_closed, quarantined}`" — invariant guard.

Function tests:
- `is_terminal_state("settled")` / `("voided")` / `("admin_closed")` / `("quarantined")` → True.
- `is_terminal_state("economically_closed")` → False (per `LEGAL_LIFECYCLE_FOLDS` it transitions to settled/voided).
- `is_terminal_state("active")` / `("pending_entry")` / etc. → False.
- Hypothetical new terminal phase added to `LifecyclePhase` + `LEGAL_LIFECYCLE_FOLDS[NEW] = {NEW}` is automatically picked up by `TERMINAL_STATES` (test by monkey-patching the folds dict in a fixture).

Acceptance:
- All 3 sites import from `lifecycle_manager`.
- Relationship test passes.
- Repository-wide grep for any hardcoded terminal set literal containing `{"settled", "voided"}` AND `"admin_closed"` AND any of `{"economically_closed", "quarantined"}` returns 1 hit (the lifecycle_manager.py derivation source) plus the test file.
- Smoke-test portfolio cap exposure-block test (if exists) updated for new `economically_closed`-counted / `quarantined`-excluded semantics; if no such test exists, add one to prevent regression.

Blast radius: low to medium. Sites 1+2 are pure refactor (set unchanged). Site 3 is a behavior change at the smoke-test portfolio cap. The change makes exposure reporting more conservative for `economically_closed` and less inflated for `quarantined`. Both directions improve correctness against on-chain reality.

---

### 3.C Decision C: Operator/data unblock sequence (out-of-code)

These items from the workbook are NOT code packets. They gate downstream code work. This packet flags their criticality and ordering; it does NOT execute them.

| Item | Workbook ref | Track | Owner | Prerequisite for | Current status (per `current_state.md` / `known_gaps.md`) |
|------|--------------|-------|-------|------------------|------------------------------------------------------------|
| Re-run P4 readiness with full deps | F1, P0 todo | Operator + Data eng | operator | All v2 mutation slices | `numpy` not installed in sandbox; checker exists; latest = `NOT_READY` |
| TIGGE parity/hash/source-time manifests | F2, P0 todo | Data engineering | data eng | Calibration v2 rebuild trust | Reportedly downloaded; manifests TBD |
| Refresh `current_source_validity.md` | P0 todo | Governance + ops | operator | Source routing trust | Marked as historical planning context per `current_source_validity.md` header |
| HK/HKO settlement source/rounding policy | F3, P0 todo | Governance packet | settlement governance | HK rows in training | `known_gaps.md` flags HKO floor-containment + 2026-03-13/14 mismatch |
| Keep `ZEUS_HARVESTER_LIVE_ENABLED=off` | F10, P0 todo | Operator | operator | Stage-2 canonical substrate completion | `current_state.md` confirms still off |
| WU/HKO/Ogimet completeness manifests | P2 todo | Operations | ops | Daily backfill trust | `WU_API_KEY` missing → backfill blocked |
| DST historical rebuild | F4, P2 todo | Data engineering | data eng | Day0 diurnal trust for DST cities | `known_gaps.md` OPEN — NOT LIVE-CERTIFIED |
| Audit existing `calibration_pairs` for mixed metric/bin_source/data_version | P2 todo | Forensic data | data eng | Live legacy fallback safety; A1 deprecation removal | Not started |
| Audit existing `ensemble_snapshots` for NULL `temperature_metric` | P2 todo | Forensic data | data eng | Slice A3 cutover safety | Not started |
| Audit legacy `platt_models` for metric contamination | P2 todo | Forensic data | data eng | Live legacy fallback safety | Not started |

These tracks are documented here for visibility. They become individual operator/governance packets if not already filed.

---

## 4. Slice ordering and dependency graph

```
A1 (store v2)        ─→ A2 (manager v2) ─┐
                                          │
B1 (terminal pred.)  ─ independent ──────┤
                                          ├──→ Calibration trust upgrade complete
A3 (snapshot stamp)  ─→ depends on        │     (gates legacy fallback removal in A5)
                       A1's MetricIdentity│
                       wiring             │
                                          │
A4 (authority-strict)─→ depends on A3 ───┘
                       (clean metric flow)

C tracks (operator/data) — gate any v2 DB mutation, parallel to A/B
A5 (deprecation removal) — after A1+A2+A3+A4 land + C-track audits complete
```

Suggested execution sequence:

1. **A1** — additive, lowest blast.
2. **B1** — refactor, parallel to A1.
3. **A2** — depends on A1.
4. **A3** — depends on A1's `MetricIdentity` plumbing.
5. **A4** — depends on A3 (clean metric flow), surfaces consumer fix if any.
6. **C tracks** — operator-driven, parallel.
7. **A5** — legacy fallback removal (`get_pairs_for_bucket`, `get_decision_group_count`, evaluator's `or "high"` paths) — REQUIRES C-track audits complete (must know existing rows are clean before removing fallback).

---

## 5. Test topology (relationship tests first)

Per Fitz: "Test relationships, not just functions. Write relationship tests BEFORE implementation. The order is: relationship tests → implementation → function tests. Not reversible."

| Slice | Relationship test (must exist before code) | Where |
|-------|--------------------------------------------|-------|
| A1 | metric-bound queries never cross metric tracks | `tests/test_calibration_store_metric_required.py` |
| A2 | maturity count and refit input come from same metric subset | `tests/test_calibration_manager_metric_consistency.py` |
| A3 | no ENS snapshot row exists without `temperature_metric`; CandidateContext fails on construction | `tests/test_evaluator_snapshot_metric_required.py` |
| A4 | learning consumers reject `authority != VERIFIED` | `tests/test_authority_strict_learning.py` |
| B1 | all three terminal-state readers produce identical answers | `tests/test_lifecycle_terminal_predicate_unique.py` |

If a relationship test cannot be expressed as pytest assertion, the relationship is not understood. Stop and re-plan that slice.

Each slice must register the new test file in `architecture/test_topology.yaml` as part of mesh maintenance.

---

## 6. Acceptance gates per slice

Each slice packet (separate from this plan packet) must:

1. Pass the slice's relationship + function tests.
2. Pass the full `tests/` regression with pre-slice baseline diff = 0 new failures (per memory `feedback_critic_reproduces_regression_baseline.md`: critic re-runs regression independently; team-lead memory-cited counts are routinely off by 2–3 due to topology flake).
3. Pass `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <slice-plan>`.
4. Pass `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit` if files added/renamed.
5. Update `current_state.md` only via the slice's plan/work_log/receipt path.
6. Critic review (con-nyx primary; surrogate `code-reviewer@opus` parallel per memory `feedback_surrogate_critic_when_con_nyx_silent.md`).
7. Verifier review with relationship-test re-run on a clean checkout.

---

## 7. Risk + blocker matrix

| Slice | Risk | Mitigation | Blocker |
|-------|------|------------|---------|
| A1 | None (additive only) | New functions; old kept | None |
| A2 | Live calibration regression on metric-aware switch | Relationship test before; full regression after; revert if any new failure | None |
| A3 | Evaluator hot-path regression; many fixture updates | Audit `CandidateContext(` construction sites; expect 5–20 fixture updates; full evaluator + signal + day0 regression | All callers must thread metric explicitly |
| A4 | Discovery of broken consumer (Stage-2 learning ignores authority filter) | Narrow scope to test + minimum fix in same packet | None |
| A5 | Legacy v1 functions removed before existing rows audited | Gate on C-track audit completion | C-track audits |
| B1 | Lifecycle vocab evolution | Single source predicate makes future evolution safe | None |
| C-* | Operator/data dependence; long lead times | Out-of-code; flagged as prerequisite; tracked as separate packets | Operator availability, data engineering capacity, governance scheduling |

---

## 8. Out-of-scope (explicitly)

- DB mutation of any kind in any slice.
- Schema/view changes.
- Quarantine of legacy rows.
- Live harvester enable.
- P4 v2 population.
- TIGGE training authoritative cutover.
- **Day0 binary→continuous weighting** (P3 todo; deferred — tracked in `known_gaps.md`).
- **Entry/exit epistemic symmetry** (P3 todo; deferred).
- **Typed execution-price / tick-size / slippage contracts** through CLOB-send/realized-fill boundary (P3 todo; deferred).
- **Operator-visible alerting on calibration v2→legacy fallback** (P3 todo; deferred until A5 closes legacy fallback).

These are valid follow-up work but require A+B+C closure first, plus their own structural decision passes.

---

## 9. Open questions for operator

**Q1.** Do you want slice A1 to keep old metric-blind `get_pairs_for_bucket` / `get_decision_group_count` indefinitely, or schedule their removal in A5 after C-track audits? Recommendation: schedule A5 after audits prove existing rows clean.

**Q2.** For slice A4, if a learning consumer is found that does NOT filter `authority='VERIFIED'`, do you want the fix in that same packet, or split? Recommendation: keep in same packet (relationship test + minimum fix), and flag any unrelated authority-filter gaps for follow-up.

**Q3.** Slice A3 (`CandidateContext` drop-default) may surface many test fixtures with implicit `temperature_metric=high`. Do you want a fixture-update sub-packet, or treat surfaced failures as a pre-merge audit list? Recommendation: pre-merge audit list — explicit test fixtures are a feature, not a chore.

**Q4.** PR #19 itself — should this fix-plan packet be merged into `midstream_remediation` BEFORE or AFTER PR #19 lands? Recommendation: AFTER, so the workbook is on disk and `evidence/pr19_workbook_snapshot.md` (vendored copy) can be replaced with a registry pointer.

**Q5.** Should slice B1 (terminal predicate) be bundled into the same packet as A1 (both narrow, both refactor-style) to reduce overhead? Recommendation: keep separate — different blast radii, different test surfaces, different review reasoning.

---

## 10. Provenance and authority

This plan is operational evidence, not authority. It does not redefine invariants, change manifests, or grant permissions.

Authority basis (cold-read order):

- `zeus/AGENTS.md` — money path, INVs, planning-lock, mesh maintenance.
- `docs/operations/current_state.md` — mainline = `midstream_remediation`, P4 BLOCKED.
- `docs/operations/known_gaps.md` — DST historical rebuild, instrument model, exit/entry symmetry.
- PR #19 workbook (vendored at `evidence/pr19_workbook_snapshot.md`) — finding catalog.
- `architecture/source_rationale.yaml`, `architecture/test_topology.yaml`, `architecture/script_manifest.yaml` — registries to update during slice packets (NOT in this plan packet).

Memory citations applied:

- `feedback_grep_gate_before_contract_lock.md` (L20) — citations grep-verified within 10 min; this plan: 0% rot, sample size 5/5 source citations.
- `feedback_critic_prompt_adversarial_template.md` — slice critic must run 10-point adversarial template, no rubber-stamp.
- `feedback_post_compact_critic_is_con_nyx.md` — post-compact critic is `con-nyx`.
- `feedback_surrogate_critic_when_con_nyx_silent.md` — fall back to surrogate `code-reviewer@opus` parallel.
- `feedback_zeus_plan_citations_rot_fast.md` — plan citation rot ~20–30% in 10 min historically; this plan refreshes via grep-gate.
- `feedback_no_git_add_all_with_cotenant.md` — never `git add -A` with co-tenant active; commit only files under this packet folder.
- `feedback_executor_commit_boundary_gate.md` (L22) — slice executor must NOT autocommit before critic review.

End of plan.
