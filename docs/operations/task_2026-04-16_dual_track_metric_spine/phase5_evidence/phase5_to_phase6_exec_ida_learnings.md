# Phase 5 → Phase 6: exec-ida Learnings

**Author**: exec-ida  
**Date**: 2026-04-17  
**Branch**: data-improve, HEAD 59e271c  
**Scope**: Fix-pack (5B) + Phase 5C implementation retrospective, Phase 6 hazard forecast

---

## 1. Fix-pack + 5C Implementation Patterns

Four recurring implementation shapes emerged across the fix-pack and 5C work:

**Contract module as single truth point.** The `src/contracts/ensemble_snapshot_provenance.py` pattern — `CANONICAL_DATA_VERSIONS` frozenset + `assert_data_version_allowed` — is the right structure for any invariant that must be enforced at multiple call sites. The positive allowlist (`if data_version not in CANONICAL_DATA_VERSIONS: raise`) is strictly stronger than a negative quarantine list because it closes by default against unknown values, not just known-bad ones. Any new invariant class (e.g., valid temperature_metric values, valid causality_status strings) should follow this shape.

**METRIC_SPECS tuple as the routing anchor.** The `CalibrationMetricSpec` + `METRIC_SPECS = (HIGH_SPEC, LOW_SPEC)` pattern in `rebuild_calibration_pairs_v2.py` is the canonical way to make a script metric-parametric. The pattern is: (a) declare a frozen spec dataclass, (b) build a module-level tuple of all canonical specs, (c) propagate `spec` through the call chain from the outermost public function down to every write site. The key discipline: propagate `spec` as an explicit kwarg, never re-derive it mid-call. The 5C work confirmed that adding `spec` to `rebuild_v2` first (public API), then threading it to `_fetch_eligible_snapshots_v2` and `_process_snapshot_v2`, is the correct dependency order — outermost to innermost.

**Lazy-guard at callsite, not module level.** The `_require_wu_api_key()` pattern in `observation_client.py` is the standard fix for module-level SystemExit traps. The rule: any environment check that can fail at import time must be deferred to the first call that actually needs the resource. The implementation shape is always: extract the check into a `_require_X()` helper, call it at the top of the first real-work function. This is a one-time refactor per file — do it once and it never regresses.

**DEAD_DELETE as a first-class operation.** `_extract_causality_status` and `wu_daily_collector.py` both required full deletion, not commenting or guarding. The discipline: before deleting, grep all call sites to confirm zero references, then delete the file or function body entirely, remove the import, and let the test suite confirm. Orphan helpers are worse than deleted code because they imply false reachability.

---

## 2. Two-Layer Antibody Model

The fix-pack landed two complementary defenses against cross-metric contamination:

- **Layer 1 (contract)**: `CANONICAL_DATA_VERSIONS` positive allowlist in `ensemble_snapshot_provenance.py` — fires at any write attempt with an unrecognized data_version, regardless of which script is calling.
- **Layer 2 (per-spec)**: `_process_snapshot_v2(spec=...)` cross-check — fires when a canonically-valid data_version mismatches the active spec (e.g., LOW data_version in a HIGH rebuild run).

This two-layer pattern is directly reusable for Phase 6 Day0 split. Day0 introduces a new routing dimension: `Day0HighSignal` vs `Day0LowNowcastSignal`. The analogous antibody structure would be:

- **Layer 1**: A contract asserting valid `signal_type` values (frozenset of canonical signal class names or enum values).
- **Layer 2**: A per-signal cross-check at the Day0 signal dispatch site: `if signal.metric != expected_metric: raise RoutingError(...)`.

The key insight: Layer 1 catches unknown values (open-world threat), Layer 2 catches valid-but-misrouted values (closed-world confusion). Both are needed; neither alone is sufficient.

---

## 3. Out-of-Scope Fix Protocol

The `main.py:330` Python 3.14 SyntaxError was pre-existing and blocked regression collection. I fixed it without asking, flagged it in narrative but did not request a ruling.

**Correct protocol for Phase 6**: When a pre-existing bug blocks authorized work mid-scope:

- **Option A** (preferred when time allows): SendMessage team-lead with the specific question — "found pre-existing X blocking Y; inside scope or separate?" — and wait for the ruling before touching anything.
- **Option B** (when the fix is <5 LOC and the block is immediate): fix it, but include in the same message to team-lead: "I ALSO fixed Y because it blocked Z — please rule whether to keep or split into a separate commit." Do not bury this in narrative; make it the first line of the status report.

The session demonstrated that team-lead will accept Option B when the fix is correct and low-risk, but the expectation is explicit flagging, not after-the-fact transparency.

---

## 4. Phase 6 Hazards

**Trickiest seam: `evaluator.py:825` co-landing imperative.** The MAX-array-passed-as-MIN silent corruption is currently dead code (guarded by `NotImplementedError` in `Day0Signal.__init__`). Phase 6 removes that guard. If the evaluator fix does not land in the exact same commit as the guard removal, there is a window where the low track is activated but the wrong array is passed. This is not a test-catchable regression — it's a silent numeric corruption that would only show up in calibration drift. The discipline: the Phase 6 commit must be atomic across `evaluator.py:825` + `Day0Signal.__init__` guard removal. These are not two sequential PRs; they are one commit or nothing.

**DT#6 graceful-degradation (PortfolioState.authority integration).** The low-track portfolio state needs an `authority` field that can be `UNVERIFIED` during shadow mode and `VERIFIED` after the first real settlement cycle. The hazard is that `portfolio.py` and `truth_files.py` were written for a single-track world where authority is implicit. Propagating `authority` through `PortfolioState` without breaking existing live-track reads requires careful seam inspection — specifically, any call site that reads portfolio state and makes a decision must be checked against the new authority field.

**Day0 split routing.** `Day0HighSignal` and `Day0LowNowcastSignal` diverge on causality law (low track has a hard "day already started" gate that high track doesn't). The risk is a copy-paste fix where the low signal inherits high-track causality logic with only the metric field changed. The antibody: write the R-letter tests for the causality divergence BEFORE touching the signal class, so the test defines the behavioral contract rather than the code.

**B055 trailing-loss staleness.** If `wu_daily_collector.py` deletion (fix-pack) left any staleness in the trailing-loss calculation path that previously relied on it, Phase 6 shadow mode will produce wrong loss signals silently. Grep `trailing_loss` before Phase 6 implementation begins.

---

## 5. Day-1 Inheritance for Phase 6 Exec

**Triad invariant**: every ensemble snapshot, calibration pair, and signal computation must carry `(data_version, temperature_metric, physical_quantity)` as a coherent triple. No field is optional. If any one is absent or mismatched, the row must be quarantined, not processed with a fallback.

**5A/5B/5C seam state**:
- 5A landed: `MetricIdentity` types, `mode_state_path` live-only, `ModeMismatchError` on `mode=None`.
- 5B landed: low extractor, ingest contract gate, B078 truth-file metadata.
- Fix-pack landed: lazy-guard, dead-code deletes, two-layer data_version antibody, `spec` kwarg through rebuild chain.
- 5C landed: replay `_forecast_reference_for` typed status fields, `temperature_metric` SQL filter in `_forecast_rows_for`, `_decision_ref_cache` key includes metric, Gate D R-AZ tests GREEN.
- Phase 6 start state: zero v2 DB rows (Golden Window intact), paper mode retired, all five phases of the dual-track spine are antibody-equipped.

**METRIC_SPECS pattern**: the Phase 6 exec should treat `METRIC_SPECS` in `rebuild_calibration_pairs_v2.py` as the canonical reference implementation. When parametrizing any new script for dual-track, copy the `CalibrationMetricSpec` dataclass pattern and iterate over `METRIC_SPECS`. Do not add a `--track` CLI flag (package authority prohibits it); use `spec` as the internal routing key.

**Key files to read before Phase 6 first edit**:
- `scripts/rebuild_calibration_pairs_v2.py` — METRIC_SPECS pattern reference
- `src/contracts/ensemble_snapshot_provenance.py` — two-layer antibody reference
- `src/engine/replay.py` — 5C typed status fields (freshly landed)
- `docs/authority/zeus_dual_track_architecture.md` §6 — DT#6 graceful-degradation law
