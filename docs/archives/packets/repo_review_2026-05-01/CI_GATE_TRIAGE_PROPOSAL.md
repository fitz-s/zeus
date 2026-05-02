# CI gate expansion — triage proposal (P0-6 audit)

**Status**: AUDIT-COMPLETE, SCOPED-TO-DEDICATED-SLICE
**Filed**: 2026-05-01 by team-lead during ultrareview25_remediation P0-6 audit
**Related**: `SYNTHESIS.md` P0-6 reclassification, `live_running.md` (verifier lane)

## Why this is not auto-fixable

The architect / test-engineer / critic-opus reviews framed CI gate expansion as a config flip. Depth audit shows it's a **120-failure cleanup task** behind the flip:

- **Current blocking CI** covers ~20 test files (`law-gate-tests` job in `.github/workflows/architecture_advisory_gates.yml:234-279` + `kernel-invariants` job at `:135-158`).
- **Total test files**: 286 in `tests/`.
- **Verifier 2026-05-01 ran `pytest -m ""`** (full suite, no marker filter) → **120 failures** across at least 4 categories.

If I run `git config` to flip the gate today, every operator commit blocks for unknown reasons. That's worse than the current state.

## What the verifier found (full-suite, `pytest -m ""`)

| Category | Count | Test file | Diagnosis | Triage class |
|---|---|---|---|---|
| Governance violations in production code | 10 | `tests/test_structural_linter.py` | dual-track settlements read without `temperature_metric` predicate; `calibration_pairs` queries outside allowlist; semantic provenance loss in `src/engine/cycle_runtime.py:164` | **REAL_BUGS** — fix code |
| Stale-stub regressions | 17 | `tests/test_pnl_flow_and_audit.py` | evaluator / Kelly / harvester paths drifted under stub fixtures | **STALE_FIXTURES** — refresh or delete |
| INV-25/INV-26 enforcement gaps | 4 | `tests/test_p0_hardening.py` | hardening tests landed but enforcement code didn't follow | **REAL_BUGS** — fix code |
| pytest.ini `live_topology` marker | 16 | various | excluded by default `addopts` | **MARKER_FILTER** — separate concern |
| Other | ~73 | unclassified | requires per-file triage | **UNKNOWN** |
| **Total** | **120** | | | |

Plus `test_cross_module_relationships.py` 10 always-skip-on-empty-DB tests (test-engineer finding) — these don't fail, they silently skip; if the gate switches to `--strict-markers` + skip-without-reason fail, they'd surface too.

## Recommended triage (4 phases, dedicated slice)

### Phase 1 — Per-category fix-or-delete (~3-5 days)

For each category above, an owner + a triage decision per test:

- **REAL_BUGS** → fix the source code OR file a bug ticket and `@pytest.mark.xfail(reason="...", strict=True)` with a deadline. `strict=True` ensures the test starts passing fixes the marker, surfacing accidental regressions.
- **STALE_FIXTURES** → refresh the fixture against current schema OR delete the test if the surface is dead.
- **MARKER_FILTER** (`live_topology`) — leave as-is for plain `pytest`, but require nightly CI to run `-m ""` and report.
- **UNKNOWN** → `pytest --collect-only -q tests/test_X.py` + read each failure individually. Most will fall into one of the above three.

Output: a `tests/SKIP_LEDGER.md` at the end of Phase 1 documenting every remaining `xfail`/`skip` with its deadline and tracking ticket.

### Phase 2 — Re-baseline (1 day)

After Phase 1:
- Run `pytest tests/ -m "" -q` and record the new pass/skip/xfail counts.
- Update `BASELINE_PASSED` and `BASELINE_SKIPPED` in `.claude/hooks/pre-commit-invariant-test.sh`.
- Expand `TEST_FILES` to include the surfaces that are now stable (notably `test_dual_track_law_stubs.py`, `test_runtime_guards.py`, `test_riskguard.py`, `test_executor.py`, `test_settlement_commands.py`, `test_pnl_flow_and_audit.py`).

### Phase 3 — CI workflow promotion (1 day)

Update `.github/workflows/architecture_advisory_gates.yml`:
- Expand the `law-gate-tests` job's pytest invocation to cover the broader money-path surface (not just the 19 currently listed).
- Either remove the per-file pytest call and switch to `pytest tests/ -m "" -q`, OR add the new files explicitly (per Phase 2's expanded list).
- Add a new `nightly-full-suite` job that runs `pytest tests/ --strict-markers -q` to keep `live_topology` honest.

### Phase 4 — Lock the discipline (0.5 day)

Add a CI step that fails on:
- Any `pytest.skip(...)` or `@pytest.mark.skip` without a `reason=` argument.
- Any `@pytest.mark.xfail` without `strict=True` and a tracking ticket reference.
- The test count regressing below `BASELINE_PASSED` baseline.

## Why I am stopping here

Doing Phase 1 in this session would either:
- (a) Take 3-5 days of focused triage work — out of scope for an architecture review
- (b) Be done sloppily — deleting tests instead of fixing real bugs

Either is worse than landing the audit cleanly with a follow-up plan. Operator should schedule the triage slice when bandwidth allows.

## What I did do here

- P0-2 (hooks dual-channel) — landed, makes future commits gate against `BASELINE_PASSED`.
- P0-3 (INV-05 antibody) — landed, +1 to baseline.
- P0-5 (for_city routing antibody) — landed, +1 to baseline.
- P0-4 (DATA_DEGRADED INV-19a) — landed but not yet in baseline (test_dual_track_law_stubs.py not in TEST_FILES).

So the pre-commit hook now baselines at 219, blocks regressions in those 14 file groups, and would block any further drift in the surfaces it watches. The remaining 110+ failures are not made worse by this work.

## References

- `.github/workflows/architecture_advisory_gates.yml` — current CI workflow
- `pytest.ini` — `addopts = -m "not live_topology"` default exclusion
- `.claude/hooks/pre-commit-invariant-test.sh` — current `BASELINE_PASSED=219`
- `docs/operations/repo_review_2026-05-01/live_running.md` — verifier lane that found the 120 failures
- `docs/operations/repo_review_2026-05-01/test_topology.md` — test-engineer lane on the always-skip cluster
