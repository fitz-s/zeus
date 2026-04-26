# SCAFFOLD — Zeus Full Data + Midstream Fix Plan

Created: 2026-04-26
Authority basis: `~/epistemic_scaffold/SCAFFOLD.md` template (Fitz universal onboarding gate)
Companion: `plan.md` in this same packet folder
Scope: epistemic scaffold for the multi-slice remediation plan derived from PR #19 workbook

---

## 1. Assumption Discovery

What is being assumed (without proof) about inputs, behavior, or environment?

| Assumption | Source | Risk if wrong |
|---|---|---|
| Workbook citations to `manager.py:193,275`, `store.py:207,292`, `evaluator.py:1819,497,537,674,91`, `chain_reconciliation.py:48`, `cycle_runner.py:54,341`, `portfolio.py:962` are accurate at audit time | grep-gate run 2026-04-26 | All slice scopes shift; plan has to be re-cited |
| `MetricIdentity` typed atom at `evaluator.py:361 _normalize_temperature_metric` is canonical and exposes `temperature_metric` attribute | grep on evaluator.py | Slice A1 v2 signature signature design must change |
| Manager `temperature_metric: Literal["high","low"]` param at `manager.py:128,156,165,267` is fully threaded into `_fit_from_pairs` and platt model write | partial grep | Slice A2 may need broader changes |
| Mainline branch for upcoming work is `midstream_remediation` per `current_state.md` | `current_state.md` "Branch: `main`" + the long status narrative on midstream_remediation | If actually targeting `main`, base of plan branch is wrong; need rebase |
| `is_terminal_state` predicate centralization in `lifecycle_manager.py` is acceptable per AGENTS.md "lifecycle owner" rule | AGENTS.md §1 "Position lifecycle: Key file `src/state/lifecycle_manager.py`" | If lifecycle_manager has explicit prohibition on adding utility predicates, slice B1 must place predicate elsewhere |
| Stage-2 harvester learning at `src/execution/harvester.py` is the primary calibration-pair-creation consumer | AGENTS.md §1 + workbook F8/F10 references | Slice A4 audit scope incomplete |
| All slices can land BEFORE PR #19 merges | Workbook is on `copilot/full-review-of-upstream-data-storage`, plan branches off `midstream_remediation` | If PR #19 changes workbook content, vendored snapshot drifts |
| Operator can re-run P4 readiness checker in a `numpy`-equipped environment | Workbook F1 + `current_state.md` evidence | C-track stays blocked |
| Existing legacy `calibration_pairs` rows MAY have NULL `temperature_metric`, mixed `bin_source`, mixed `data_version` | Workbook P2 todos (audits not yet run) | Slice A5 (legacy fallback removal) gating uncertain |

---

## 2. Provenance Interrogation

Where does each load-bearing input come from? Is its source authority-grade?

| Input | Source | Authority class | Last verified |
|---|---|---|---|
| PR #19 workbook content | `app/copilot-swe-agent` (bot author), opened 2026-04-26 02:36:56Z | Operational evidence (per its own header "not authority") | 2026-04-26 — full content vendored at `evidence/pr19_workbook_snapshot.md` |
| `manager.py` / `store.py` / `evaluator.py` / `chain_reconciliation.py` / `cycle_runner.py` / `portfolio.py` line citations | git working tree at HEAD `6499f9c` (origin/midstream_remediation) | Code (canonical) | 2026-04-26 grep-gate |
| `current_state.md` mainline = `midstream_remediation`, P4 BLOCKED | `docs/operations/current_state.md` at HEAD | Authority (operations control surface) | 2026-04-26 |
| `known_gaps.md` DST historical rebuild OPEN | `docs/operations/known_gaps.md` at HEAD | Authority (operations control surface) | 2026-04-26 |
| Memory citations (L20, L22, L28, L30, surrogate critic, etc.) | User auto-memory `MEMORY.md` index | Personal-memory (recall-only; verify before recommending per "Before recommending from memory" rule in user CLAUDE.md) | Re-verified for relevance 2026-04-26 |
| `MetricIdentity` typed atom existence | `src/engine/evaluator.py:361 _normalize_temperature_metric` (visible in grep output) | Code (canonical) | 2026-04-26 |
| Lifecycle terminal vocab `{settled, voided, admin_closed, economically_closed}` | `src/state/portfolio.py:962` (canonical) + `cycle_runner.py:341` (literal) + AGENTS.md §1 ("Terminal states: voided, quarantined, admin_closed") | Mixed — AGENTS.md says 3 terminals incl. `quarantined`; code has 4 incl. `economically_closed`; `quarantined` NOT in code's terminal set | **DISCREPANCY** — needs resolution before slice B1 |

**Provenance flag — DISCREPANCY:** AGENTS.md §1 lists terminal states as `{voided, quarantined, admin_closed}` (3 items), but code's canonical set is `{settled, voided, admin_closed, economically_closed}` (4 items, no `quarantined`). The code is authority over docs per Zeus rules, but the AGENTS.md doc-truth gap should be reconciled in slice B1 or flagged as a separate doc-fix packet. Recommend: slice B1 packet includes a one-line AGENTS.md update OR explicitly defers it.

---

## 3. Cross-Module Relationships

What flows between modules, and what invariant must survive each handoff?

### Calibration chain (slices A1+A2)

```
candidate (with temperature_metric)
  ↓ src/engine/evaluator.py:_evaluate_candidate
context (CandidateContext.temperature_metric)
  ↓
calibration_manager._fit_from_pairs(temperature_metric=X)
  ↓ — INVARIANT BREACH: metric is dropped here today
calibration_store.get_decision_group_count(conn, cluster, season)        ← no metric
calibration_store.get_pairs_for_bucket(conn, cluster, season, ...)      ← no metric
  ↓
calibration_pairs DB rows (mixed high+low possible)
  ↓
Platt fit
  ↓
platt_models row (saved per cluster/season — metric-blind)
```

Cross-module invariant (must survive each arrow): **temperature_metric flows unbroken from candidate construction through every calibration layer down to the Platt model that gets saved.**

Today: invariant breaks at `manager.py:193,275`. Slice A1+A2 restores it.

Relationship test for the chain (slice A2): a HIGH-metric candidate's calibration MUST NEVER touch a `calibration_pairs` row with `temperature_metric='low'`.

### Snapshot stamping chain (slice A3)

```
ENS fetch (ens object with .temperature_metric)
  ↓ src/engine/evaluator.py:_store_ens_snapshot
INSERT into ensemble_snapshots(..., temperature_metric=snap_metric, ...)
                                                       ^^^^^^^^^^^
                            today: getattr(...) or "high"  ← silent default
  ↓
ensemble_snapshots row (potentially mis-stamped)
  ↓
rebuild_calibration_pairs* reads back, filters by temperature_metric
  ↓
calibration_pairs row stamped with snapshot's metric
```

Cross-module invariant: **a snapshot row's `temperature_metric` must equal the upstream ENS object's `temperature_metric` exactly, with no fallback.**

Today: invariant breaks at the writer fallback. Slice A3 restores it.

### Rescue authority chain (slice A4)

```
Position (with temperature_metric or NULL)
  ↓ src/state/chain_reconciliation.py:resolve_rescue_authority
(metric, authority, source) — defaults to ("high", "UNVERIFIED", ...) on NULL
  ↓ INSERT into rescue_events_v2
  ↓
Stage-2 harvester learning reads rescue_events_v2
  ↓ — INVARIANT IMPLICIT: should filter authority='VERIFIED'
calibration_pairs row created from rescue evidence
```

Cross-module invariant: **a rescue row with `authority='UNVERIFIED'` must NEVER feed calibration training.**

Today: this invariant is enforced by *docstring promise only* (`chain_reconciliation.py:33`). Slice A4 makes it executable.

### Terminal-state chain (slice B1)

```
Lifecycle vocab (LifecyclePhase enum + terminal additions)
  ↓
[3 sites read terminal-state membership]
- portfolio.py:962  (load filter — drops terminal rows from active view)
- cycle_runner.py:54 (RED sweep filter — skips terminal positions)
- cycle_runner.py:341 (exposure-block filter — counts non-terminal cost basis)
```

Cross-module invariant: **all three readers must agree on terminal-state membership at all times, including under future vocab evolution.**

Today: three independent literal copies. Slice B1 collapses to one source.

---

## 4. What I Don't Know

Things this plan does NOT have evidence for, listed so a future cold-read can find them:

1. **Whether existing `ensemble_snapshots` rows have NULL `temperature_metric`.** Workbook P2 todo. Slice A3 cutover safety depends on this. If yes, A3 needs a quarantine companion before deploying fail-closed writer.
2. **Whether existing `calibration_pairs` rows have mixed `temperature_metric` per `(cluster, season, bin_source)` bucket.** Workbook P2 todo. Slice A5 (legacy fallback removal) gating depends on this.
3. **Whether legacy `platt_models` rows have metric contamination.** Workbook P2 todo. Live legacy fallback safety depends on this.
4. **Whether Stage-2 harvester learning currently respects `authority='VERIFIED'`.** Slice A4 audit will determine. If NO, A4 includes the fix; if YES, A4 is just the relationship test.
5. **The exact set of test fixtures relying on `CandidateContext(temperature_metric)` default `="high"`.** Slice A3 audit will determine. Could be 5 or 50.
6. **Whether `quarantined` should be in code's terminal set or stay separate (per `AGENTS.md` §1 vs `portfolio.py:962`).** Doc-truth/code-truth discrepancy noted in §2. Operator clarification recommended before slice B1 includes any vocab changes.
7. **PR #19's merge timeline.** If it merges before this plan packet's slices land, the workbook becomes on-disk and the vendored copy in `evidence/pr19_workbook_snapshot.md` should be replaced with a pointer.
8. **Whether the suggested slice ordering is operator-acceptable** given concurrent mainline work on `midstream_remediation`. Slice A2 (live calibration manager change) may need to land in a quiet window.
9. **Whether `topology_doctor --planning-lock` will pass for any of the slices** without operator-side registry remediation (the workbook itself notes topology_doctor was blocked by pre-existing registry/doc/lore issues).
10. **Whether the existing `_normalize_temperature_metric` accepts `None`/empty/out-of-domain inputs gracefully** or raises. Slice A3 needs to reuse this normalizer; if it has a permissive fallback, A3's fail-closed semantics need to wrap or extend it, not just call it.

---

## 5. Action gating from this scaffold

Based on §4 unknowns, this plan packet does NOT execute any code change. Each slice packet will reduce one or more unknowns above before code changes. In particular:

- Slice A3 packet must close unknowns #1 and #5 in its own audit phase before code lands.
- Slice A4 packet must close unknown #4 in its own audit phase.
- Slice A5 packet must close unknowns #2 and #3 (C-track audits).
- Slice B1 packet must close unknown #6 (`quarantined` doc/code reconciliation).

---

End of scaffold.
