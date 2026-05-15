# P2 Companion-Required Mechanism — SCAFFOLD

Created: 2026-05-15
Status: SPEC ONLY — no implementation code; this document is the build contract for P2
Authority basis:
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/03_authority_drift_remediation/REMEDIATION_PLAN.md (§ Companion-Update Enforcement)
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/03_authority_drift_remediation/DRIFT_ASSESSMENT.md (Cohort 4)
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md (§9 Companion-Loop-Break)
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/MIGRATION_PATH.md (Phase 2 95% AGREE threshold)
- docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md (P1 admission unit; §1.9, §6, §7)
- architecture/reference_replacement.yaml (current profile schema baseline)

This SCAFFOLD specifies an additive extension to the P1 v_next admission engine that converts authority-doc drift from a “find later” signal into a write-time admission gate. Source-edit packets that touch a profile with declared authority-doc dependencies must include the companion doc OR present a single-call human-acknowledged skip token. P2 ships shadow-first: 14-day advisory window then soft-block; hard-block escalation is explicitly deferred to P3.

---

## §0.A Prerequisites

P2 CANNOT ship until the following conditions are satisfied:

1. **P1 merged to main.** P2 is a strictly additive extension to P1's admission engine. P2's `_check_companion_required` helper, `BindingLayer` field additions, and `companion_skip_logger.py` all assume P1's `admission_engine.py`, `dataclasses.py`, `profile_loader.py`, and `cli_integration_shim.py` are present and passing their own test suite.
2. **P1's `--v-next-shadow` has reached 95% AGREE.** The AGREE rate is defined in MIGRATION_PATH §Phase 2: ≥ 95% of shadow comparisons over a rolling 7-day window must show AGREE (old-engine and v_next agree on admission outcome). This threshold must be met BEFORE the P2.a window opens, because the AGREE signal is the primary instrument for detecting regressions. If P2 ships before 95% AGREE, any companion-required MISSING_COMPANION emissions will conflate with residual P1 divergence signals, making both metrics uninterpretable.
3. **P2.a window starts on day 1 after P1 cutover.** "P1 cutover" is defined as the commit that sets `binding.severity_overrides["v_next"] = "soft_block"` in the binding YAML (the P1 promotion commit). The P2.a 14-day shadow window starts the calendar day after that commit lands on main.
4. **The two shadow windows MUST NOT overlap.** P1's shadow window and P2's shadow window are sequential, not concurrent. Overlapping them would conflate P1 divergence signals with P2 companion signals; the AGREE rate metrics would conflate; false-positive attribution would be impossible. Enforcement: the P2 PR cannot be opened until the P1 promotion commit exists on main.

---

## §0. Input Inconsistencies Found (binding instruction precedence)

INCONSISTENCY-1: GOAL/REMEDIATION schema vs P1 BindingLayer dataclass shape.
- REMEDIATION_PLAN § Companion-Update Enforcement shows YAML at the **profile** level:
  ```yaml
  profile: modify_calibration_weighting
  allowed_files: [...]
  companion_required: [docs/reference/zeus_calibration_weighting_authority.md]
  companion_skip_acknowledge_token: COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1
  ```
- P1 SCAFFOLD §1.2 declares `CoverageMap.profiles: dict[str, tuple[str, ...]]` (profile_id → glob tuple). There is NO per-profile sub-record in the existing dataclass.
- Resolution (binding): the GOAL’s **YAML surface** is preserved (profiles file remains profile-keyed and human-readable per REMEDIATION_PLAN), but the **typed in-memory representation** lives as TWO new top-level fields on `BindingLayer`, both keyed by `profile_id`. This preserves the P1 Codex single-import contract — `CoverageMap.profiles` shape is unchanged, and the additive fields default to empty dicts when absent.
  - `companion_required: dict[str, tuple[str, ...]]` — profile_id → tuple of authority-doc paths
  - `companion_skip_tokens: dict[str, str]` — profile_id → exact token string (e.g. `"COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1"`)
- Flag: P1 SCAFFOLD §1.2 should be revised in the P2 implementation PR to add these two fields with `field(default_factory=dict)` defaults so existing P1 binding YAML continues to load without modification.

INCONSISTENCY-2: P1 §9 Companion-Loop-Break vs P2 companion_required.
- P1 §9 / Universal §9 handles **2-file source-pair cohorts** (e.g., new test file + test_topology.yaml registration). The companion is a SOURCE artifact required for the change to compile/route.
- P2 companion_required handles **source-edit → authority-doc pair**. The companion is a NARRATIVE artifact whose drift compounds silently.
- These are complementary, not duplicative. P2 introduces a NEW admission helper (`_check_companion_required`); it does not modify or replace the §9 shim.
- Resolution: §3 below names the new helper explicitly and pins the insertion point in `admission_engine.admit()` so reviewers can verify the mechanisms remain orthogonal.

INCONSISTENCY-3: agent_id source for skip-token logging.
- GOAL §5 schema requires `agent_id` field in the skip-token log.
- P1 `admit()` signature (P1 SCAFFOLD §6) does NOT carry agent_id — adding it would change the Codex-portable contract.
- Resolution: read agent_id from environment at log-write time inside the logger module (`OMC_AGENT_ID`, fallback `CLAUDE_AGENT_ID`, fallback `CODEX_AGENT_ID`, fallback literal `"unknown"`). The `admit()` signature is unchanged. Documented in §5.

---

## §1. Empirical Motivation

P8’s HYP-B reference_replacement failures (3 documented cases resolved 2026-05-15) all share one root cause: a source-edit packet was admitted, merged, and only weeks later did the topology_doctor `reference_replacement_missing_entry` check surface the resulting authority-doc drift. In each case, the source change was correct in isolation; what failed was the process — there was NO admission-time gate that asked “did you also touch the authority doc that names this surface?” The fix is not more auditing (the weekly drift-surface task is the safety net, not the structural fix). The structural fix is to convert the question from “find drift later” into “block at write time” by making the admission engine refuse a source-edit packet that names one of the registered profiles unless the changed-paths union includes the companion authority doc OR the agent presents a one-call skip-acknowledge token whose use is logged for human review. This is the structural decision Fitz Constraint #1 demands: ten symptoms (drift entries) are one design failure (no admission-time companion gate).

---

## §2. Profile Schema Extension

### 2.1 YAML surface (human-authored, profile-keyed)

The profiles binding YAML keeps the REMEDIATION_PLAN structure verbatim — profile-keyed for readability:

```yaml
# architecture/topology_v_next_binding.yaml (additive section)
profiles:
  - id: modify_calibration_weighting
    patterns:
      - src/calibration/*.py
      - tests/test_calibration_*.py
    companion_required:
      - docs/reference/zeus_calibration_weighting_authority.md
    companion_skip_acknowledge_token: COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1
    companion_reason: >
      any change to calibration weighting must update the authority reference;
      otherwise the authority drift problem compounds (P8 HYP-B precedent)
```

Backwards compatibility guarantee: profiles WITHOUT `companion_required:` and WITHOUT `companion_skip_acknowledge_token:` parse cleanly and yield empty entries in the typed dataclass. Existing P1 binding YAML continues to load with NO modification.

### 2.2 Typed in-memory representation (BindingLayer fields)

Two new top-level `BindingLayer` fields (additive; default empty):

```python
# Conceptual extension to scripts/topology_v_next/dataclasses.py — schema only, no code in this packet
@dataclass(frozen=True)
class BindingLayer:
    # ... P1 fields unchanged ...
    companion_required: dict[str, tuple[str, ...]] = field(default_factory=dict)
        # profile_id → tuple of authority-doc relative paths
    companion_skip_tokens: dict[str, str] = field(default_factory=dict)
        # profile_id → exact token string (literal match, no fuzzy)
```

`profile_loader.py` extension:
- After parsing each profile entry, if `companion_required:` key present → populate `companion_required[profile.id] = tuple(paths)`.
- If `companion_skip_acknowledge_token:` key present → populate `companion_skip_tokens[profile.id] = token`.
- `validate_binding_layer()` adds two new advisory checks: (a) every path in `companion_required[*]` must exist on disk at load time (else WARN `companion_target_missing`); (b) every token must match regex `^[A-Z_]+(=[A-Za-z0-9_]+)?$` (else WARN `companion_token_malformed`). Validation is advisory; load proceeds.

### 2.3 Two concrete examples

**Example A — minimal (no skip token):**

```yaml
- id: modify_kelly_sizing
  patterns:
    - src/strategy/kelly_*.py
    - tests/test_kelly_*.py
  companion_required:
    - docs/reference/zeus_kelly_asymmetric_loss_handoff.md
  companion_reason: >
    P8 HYP-B precedent: kelly sizing changes drifted from the asymmetric
    loss handoff authority doc within 2 weeks of merge.
```

If a packet edits `src/strategy/kelly_sizing.py` without including the doc and there is no skip token configured, MISSING_COMPANION emits and (in P2.b) SOFT_BLOCKs.

**Example B — with skip token:**

```yaml
- id: modify_vendor_response
  patterns:
    - src/data/vendor_response_*.py
    - src/ingest/vendor_response_*.py
    - tests/test_vendor_response_*.py
  companion_required:
    - docs/reference/zeus_vendor_change_response_registry.md
  companion_skip_acknowledge_token: COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1
  companion_reason: >
    Vendor change response edits frequently arrive as hotfixes with no
    time for doc-side coordination; skip token allows admission with
    audit trail for next-week digest review.
```

If the agent supplies env `COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1` (literal string match) at `admit()` invocation, the change admits AND a row is appended to `state/companion_skip_token_log.jsonl` for human review. The next weekly digest names every skip use.

---

## §3.0 Composition-Rule Extension

`composition_rules.apply_composition()` treats every path in `binding.companion_required[profile_id]` as a Rule C1 declared companion of `profile_id`. This is an additive modification to P1's `composition_rules.py` module — strictly more permissive (adds a new admission path; removes none). Acknowledged in §11.

**Why this extension is required.** Without it, the following trap exists:

- `probe2` calls `admit(files=["src/calibration/weighting.py", "docs/reference/zeus_calibration_weighting_authority.md"])` — a source file plus its companion doc.
- The `files` set spans 3 profiles (e.g., `modify_calibration_weighting`, a documentation profile, and a cross-profile overlap).
- Universal §4 step 4 triggers `UNION_SCOPE` → Rule C1 rejects the multi-profile packet → a `composition_conflict` SOFT_BLOCK fires from step 4, before the algorithm ever reaches step 6 (`_check_companion_required`).
- The agent sees `MISSING_COMPANION`, adds the doc, then receives a **different** SOFT_BLOCK (`composition_conflict`) — an unsolvable trap: adding the required companion breaks composition.

**The §3.0 extension closes this trap** by pre-registering every `companion_required` path for `profile_id` as a Rule C1 declared companion before composition runs. The composition engine therefore does not penalize the source+companion-doc combination as a multi-profile violation — it treats the authority doc as a co-declared member of the same profile cohort. The net effect: `probe2`'s `admit()` call flows through composition cleanly and reaches `_check_companion_required` in step 6, where it exits with `ADMIT` (no missing companion). The trap is permanently closed.

---

## §3. Admission Engine Extension

P1 SCAFFOLD §1.9 declares `admit()` with internal helpers `_run_kernel`, `_resolve_intent`, `_resolve_candidates`, `_apply_composition`, `_apply_companion_loop` (the §9 source-pair shim), `_apply_severity_overrides`, `_assemble_diagnosis`, `_increment_friction_budget`. P2 adds ONE new internal helper:

```
_check_companion_required(profile_id, files, binding) -> list[IssueRecord]
```

Insertion point (binding): IMMEDIATELY AFTER `_apply_composition()` (which produces the resolved profile_id) and IMMEDIATELY BEFORE `_apply_severity_overrides()` (which determines whether `companion_missing` advisory promotes to soft_block per binding-layer policy). This ordering ensures the issue is visible to the severity table and to `_assemble_diagnosis()` without re-running composition.

### 3.1 Order of operations (within `admit()`)

```
1. _run_kernel(...)                          # P1, unchanged
2. _resolve_intent(...)                      # P1, unchanged
3. _resolve_candidates(...)                  # P1, unchanged
4. _apply_composition(...) → profile_id      # P1, unchanged
5. _apply_companion_loop(...)                # P1 §9 source-pair, unchanged
6. _check_companion_required(...)            # P2 NEW — authority-doc gate
7. _apply_severity_overrides(...)            # P1, sees the new issue
8. _assemble_diagnosis(...)                  # P1, sees the new issue
9. _increment_friction_budget(...)           # P1, unchanged
10. return AdmissionDecision(...)
```

The new helper sits between profile resolution (it needs a resolved profile_id) and severity application (so binding overrides can promote `companion_missing` from default ADVISORY to SOFT_BLOCK once the 14-day shadow window passes).

### 3.2 Pseudocode for `_check_companion_required`

```
def _check_companion_required(
    profile_id: str | None,
    files: list[str],
    binding: BindingLayer,
) -> list[IssueRecord]:

    # Profiles with no companion declaration → no-op. Backwards-compatible.
    if profile_id is None:
        return []
    required_docs = binding.companion_required.get(profile_id, ())
    if not required_docs:
        return []

    # Skip-token short-circuit: literal env-var match, no fuzzy.
    skip_token = binding.companion_skip_tokens.get(profile_id)
    if skip_token:
        token_key, _, token_value = skip_token.partition("=")
        env_value = os.environ.get(token_key)
        if env_value is not None and env_value == (token_value or "1"):
            # Skip authorized — log to skip-token log, emit ADVISORY for visibility.
            companion_skip_logger.log(
                profile_id=profile_id,
                source_files=files,
                expected_companions=required_docs,
                token_value=skip_token,
            )
            return [IssueRecord(
                code="companion_skip_token_used",
                path=files[0] if files else "",
                severity=Severity.ADVISORY,
                message=(
                    f"COMPANION_SKIP token used for profile '{profile_id}'; "
                    f"skipped companions: {','.join(required_docs)}; logged for review."
                ),
                metadata={"profile": profile_id, "skipped": list(required_docs)},
            )]

    # Default path: union of changed files MUST include every required companion.
    files_norm = {PurePosixPath(f).as_posix() for f in files}
    missing = [doc for doc in required_docs if doc not in files_norm]
    if not missing:
        return []  # All companions present — clean admit.

    # Emit one MISSING_COMPANION issue per missing doc, naming both source and doc.
    source_trigger = next((f for f in files if profile_id in coverage_map.resolve_candidates([f]).get(f, set())), files[0] if files else "<no-source>")
    return [
        IssueRecord(
            code="missing_companion",
            path=doc,
            severity=Severity.ADVISORY,  # P2.a default; binding promotes to SOFT_BLOCK at P2.b
            message=(
                f"MISSING_COMPANION profile={profile_id} "
                f"missing_companion={doc} triggered_by={source_trigger}"
            ),
            metadata={
                "profile": profile_id,
                "missing_companion": doc,
                "triggered_by": source_trigger,
                "all_required": list(required_docs),
            },
        )
        for doc in missing
    ]
```

Properties:
- Pure read of `binding` and `os.environ`. No mutation of admission state.
- Profile resolution → companion check → MISSING_COMPANION emission is deterministic; same inputs always produce the same issue list.
- The single optional side-effect (skip-token log write) is delegated to `companion_skip_logger.log()` which uses the same atomic-write pattern as `divergence_logger.py` (write-tmp + os.rename).

---

## §4. MISSING_COMPANION Error Format

### 4.1 Message template (1-line, grep-able)

```
MISSING_COMPANION profile=<profile_id> missing_companion=<doc_path> triggered_by=<source_file>
```

Constraints:
- Exactly one line per missing doc; no embedded newlines.
- Field order is fixed for grep stability: `profile=` always first, `missing_companion=` always second, `triggered_by=` always third.
- Field values contain no spaces (paths are POSIX-style). No quoting required for grep.
- One issue record per missing companion (a profile requiring 2 docs both absent → 2 issues).

### 4.2 Exit code policy

| Phase | Behavior | Exit code |
|-------|----------|-----------|
| P2.a SHADOW (days 1–14) | log only; emit ADVISORY in AdmissionDecision.issues | 0 (admit succeeds) |
| P2.b SOFT_BLOCK (post-95% agreement) | binding promotes ADVISORY → SOFT_BLOCK; admission refuses | nonzero (per existing v_next severity policy) |
| P3 HARD_BLOCK (out of P2 scope) | reserved; requires separate packet | nonzero |

The promotion P2.a → P2.b is binding-config only — no code change. Reviewer of the post-shadow cutover edits `binding.severity_overrides["missing_companion"] = "soft_block"` in the YAML.

P2 hard-block (HARD_STOP) is explicitly NOT introduced. Any escalation to HARD_STOP requires a separate packet because per Universal §15 G6 “severity may be promoted by project binding (advisory → soft_block) but never demoted from hard_stop” — once at HARD_STOP, the override path is gone and skip-token semantics must be re-architected.

---

## §5. Skip-Token Usage Logging

### 5.1 Storage

Path: `state/companion_skip_token_log.jsonl`
Format: append-only JSONL, one record per skip-token use.
Atomicity: write to `state/companion_skip_token_log.jsonl.tmp` then `os.rename` (same pattern as P1 `divergence_logger.py`).
Git: `.gitignore` add `state/companion_skip_token_log.jsonl*` so raw skip use is not committed.

### 5.2 Schema

```json
{
  "ts": "2026-05-15T13:42:01.234Z",
  "profile": "modify_vendor_response",
  "source_files": [
    "src/data/vendor_response_xyz.py",
    "tests/test_vendor_response_xyz.py"
  ],
  "expected_companions": [
    "docs/reference/zeus_vendor_change_response_registry.md"
  ],
  "token_value": "COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1",
  "agent_id": "venus-zeus-executor",
  "session_id": "<from env CRG_SESSION_ID or short uuid>",
  "justification_env": null
}
```

Field rules:
- `ts` — ISO-8601 UTC.
- `profile` — exact profile_id from binding.
- `source_files` — verbatim copy of the `files` argument to `admit()`.
- `expected_companions` — verbatim copy of `binding.companion_required[profile_id]`.
- `token_value` — full token string from binding (so log records WHICH token was honored).
- `agent_id` — resolved from env at log-write time per §0 INCONSISTENCY-3 fallback chain (`OMC_AGENT_ID` → `CLAUDE_AGENT_ID` → `CODEX_AGENT_ID` → `"unknown"`).
- `session_id` — best-effort env read; null acceptable.
- `justification_env` — optional env `COMPANION_SKIP_JUSTIFICATION` value if set; null otherwise. Allows the agent to record a one-line reason without changing `admit()` signature.

### 5.2.1 Env Var Population Wiring

`agent_id` is resolved from environment at log-write time per §0 INCONSISTENCY-3 fallback chain. For this to produce meaningful audit trail entries rather than 100% `"unknown"`, the three dispatch surfaces must reliably populate the env before any `admit()` call:

- **`OMC_AGENT_ID`** — set by the openclaw gateway when dispatching a task to a workspace agent. P2.1 includes a one-line addition to the gateway dispatch path to export this variable.
- **`CLAUDE_AGENT_ID`** — set by the Claude Code session wrapper (the shell environment established when a Claude Code session starts). P2.1 includes a one-line update to the session wrapper to export this variable.
- **`CODEX_AGENT_ID`** — set by the codex CLI invocation hook. P2.1 includes a one-line update to the codex hook to export this variable.

Without this wiring, every skip-token log row will carry `agent_id: "unknown"`, rendering the audit trail useless for attribution. This wiring is a P2.1 delivery prerequisite, not a P2.a prerequisite — P2.a shadow runs are acceptable with partial attribution. The wiring MUST be in place before P2.b cutover so that the weekly digest human-review step can meaningfully attribute skip uses to specific agents.

### 5.3 Retention

- 90 days rolling window. After day 90, daily log rotation to `state/companion_skip_token_log.jsonl.<YYYYMMDD>`. Rotated files retained ≥ 180 days for audit.
- Single-file size cap: 10 MB. Force rotation if exceeded.

### 5.4 Review queue digest

Path: `state/companion_skip_token_weekly_digest.md` (regenerated by the existing `authority_drift_surface` weekly task — see REMEDIATION_PLAN cadence).
Format: human-readable markdown, one section per profile, one row per skip use (ts, source_files, agent_id, justification). Empty week → digest contains a one-line “zero skip-token uses this week” entry, which is itself a positive signal (the structural fix is working).
Weekly task gains a new function `regenerate_companion_skip_digest()`; surfaces non-empty digests to the configured human notification channel.

---

## §6. Initial Profile Candidates (3 anchors + 2 extensions)

Drawn from DRIFT_ASSESSMENT Cohort 4 (verified-drift entries; the 3 TOPOLOGY BLOCKING anchors take priority because they have demonstrated `reference_replacement_missing_entry` hits).

| Profile id | Patterns (sketch) | companion_required | Justification |
|---|---|---|---|
| `modify_calibration_weighting` | `src/calibration/*.py`, `tests/test_calibration_*.py` | `docs/reference/zeus_calibration_weighting_authority.md` | TOPOLOGY BLOCKING anchor; the example named verbatim in REMEDIATION_PLAN. |
| `modify_kelly_sizing` | `src/strategy/kelly_*.py`, `tests/test_kelly_*.py` | `docs/reference/zeus_kelly_asymmetric_loss_handoff.md` | TOPOLOGY BLOCKING anchor; asymmetric-loss handoff is exactly the kind of authority that drifts silently after kelly tweaks. |
| `modify_vendor_response` | `src/data/vendor_response_*.py`, `src/ingest/vendor_response_*.py`, `tests/test_vendor_response_*.py` | `docs/reference/zeus_vendor_change_response_registry.md` | TOPOLOGY BLOCKING anchor; vendor change response is the canonical hotfix-arrives-without-doc-update pattern, hence the only initial anchor with a skip token. |
| `modify_data_replay_surface` | `src/data/replay_*.py`, `src/data/client_*.py`, `tests/test_data_*.py` | `docs/reference/zeus_data_and_replay_reference.md` | Cohort 4 verified drift; data client surface is high-churn and the replay reference is exactly one of the authority docs that lags behind. |
| `modify_risk_strategy_surface` | `src/risk/*.py`, `src/strategy/risk_*.py`, `tests/test_risk_*.py` | `docs/reference/zeus_risk_strategy_reference.md` | Cohort 4 verified drift; risk strategy reference is the canonical doc covering the Kelly sizing formula and edge decay model — separate from `modify_kelly_sizing` which targets the asymmetric-loss handoff doc specifically. |

Glob patterns above are sketches; the implementation PR will validate against `architecture/module_manifest.yaml` and `architecture/test_topology.yaml` before commit. Adding a 6th profile requires a follow-up packet so P2 ships with a small, defensible initial population.

---

## §7. Test Fixture Catalog

Located at `tests/topology_v_next/regression/`:

### probe1 — `test_companion_required_missing_emits_issue.py`
Setup: binding with `modify_calibration_weighting` requiring `docs/reference/zeus_calibration_weighting_authority.md`, NO skip token configured.
Action: `admit(intent="modify_existing", files=["src/calibration/weighting.py"])`.
Assert: `decision.severity == Severity.ADVISORY` (P2.a), exactly one issue with `code == "missing_companion"`, message contains the exact pattern `MISSING_COMPANION profile=modify_calibration_weighting missing_companion=docs/reference/zeus_calibration_weighting_authority.md triggered_by=src/calibration/weighting.py`. `decision.ok is True` (advisory does not fail admission in P2.a).

### probe2 — `test_companion_required_present_admits_clean.py`
Setup: same binding.
Action: `admit(intent="modify_existing", files=["src/calibration/weighting.py", "docs/reference/zeus_calibration_weighting_authority.md"])`.
Assert: `decision.severity == Severity.ADMIT`, no `missing_companion` issue, `decision.ok is True`.

### probe3 — `test_companion_skip_token_logs_and_admits.py`
Setup: binding for `modify_vendor_response` with `COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1`. Set env `COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1`. Use a tmp_path for the skip log.
Action: `admit(intent="modify_existing", files=["src/data/vendor_response_x.py"])`.
Assert: `decision.ok is True`, exactly one issue with `code == "companion_skip_token_used"`, AND `state/companion_skip_token_log.jsonl` (tmp_path-redirected) contains exactly one record with the correct `profile`, `source_files`, `expected_companions`, `token_value`. Unset env on teardown.

### probe4 — `test_companion_required_path_match_is_exact.py`
Setup: binding requiring `docs/reference/zeus_calibration_weighting_authority.md`.
Action 1: `admit(..., files=["src/calibration/weighting.py", "docs/reference/zeus_calibration_weighting_authority.md.bak"])` — backup file present, real doc absent.
Assert 1: `missing_companion` issue STILL emitted (substring match would falsely admit; we require exact path equality after PurePosixPath normalization).
Action 2: `admit(..., files=["src/calibration/weighting.py", "DOCS/REFERENCE/zeus_calibration_weighting_authority.md"])` — case mismatch.
Assert 2: `missing_companion` issue still emitted (path comparison is case-sensitive on the in-repo POSIX form, which matches git semantics).

### probe5 — `test_companion_required_nested_source_path_admits.py`
Setup: binding with `modify_calibration_weighting` covering `src/calibration/*.py` and `src/calibration/sub/*.py`, requiring the calibration authority doc.
Action: `admit(intent="modify_existing", files=["src/calibration/sub/internal_helper.py", "docs/reference/zeus_calibration_weighting_authority.md"])`.
Assert: `decision.severity == Severity.ADMIT`, no `missing_companion` issue. Verifies that the gate operates on the resolved profile_id (post-composition), not on lexical file-prefix matching.

Total: 5 probes, ~250 LOC. Each probe is independent — no shared state, no fixture chaining beyond the standard tmp_path + monkeypatch idiom.

---

## §8. Self-Check (P1.0 §7 model)

### 8.1 Does `companion_required:` field carry state across calls?

NO. The field is read from the binding YAML on every `admit()` call via `profile_loader.load_binding_layer()`. There is no in-memory cache, no session-scoped state, no sidecar file that records “this profile’s companion was already checked.” The field is a pure declaration — semantically equivalent to a constant, replaceable by editing the YAML and re-invoking. This matches P1 SCAFFOLD §7.1’s stance on `intent_class` (caller-supplied, not session-state).

### 8.2 Does `companion_skip_acknowledge_token:` become a sidecar that drifts?

NO. Justification: the token is a single-call audit-trail mechanism, not mutable state.
- The token is **declared** in the binding YAML (per-profile literal string).
- The token is **honored** by reading `os.environ` at `admit()` time — environment is a per-call surface, not persistent state.
- The token use is **logged** to `state/companion_skip_token_log.jsonl` — but that log is OUTPUT, not a feedback input to future admissions. No code reads it back to make a routing decision.
- Token value is literal-match (`token_key=token_value`); no fuzzy match, no precedence merging, no token-from-multiple-sources resolution.
The skip-token mechanism cannot drift because it has no mutable surface to drift on. The risk would be if the logger started feeding back into admission (e.g., “this agent skipped 3 times this week → soft-block next time”) — explicitly out of scope; if introduced, requires its own packet because that creates session-state.

### 8.3 Does the admission engine extension introduce any new admission rail parallel to v_next?

NO. `_check_companion_required` is a NEW INTERNAL HELPER inside the existing P1 `admit()` function. It is invoked between `_apply_composition` and `_apply_severity_overrides` (§3.1 ordering). No new public function, no new module-level entry point, no new CLI flag. The single new module `companion_skip_logger.py` is a logger only — it does not implement admission and is not callable as an admission entry point. Universal §15 G2/G3/G5 guardrails are unchanged: HARD_STOP path untouched, AdmissionDecision struct shape only gains additional issues (existing field), Companion-Loop-Break (§9 source-pair) is orthogonal and unchanged.

The §9 distinction is critical (per §0 INCONSISTENCY-2): §9 handles SOURCE-pair cohorts (file + file); P2 handles SOURCE→AUTHORITY-DOC pair (file + narrative). They run in adjacent steps of the same algorithm, both produce IssueRecords into the same issues list, and are reviewed together by `_apply_severity_overrides`. They are NOT a parallel rail; they are two helpers on the same rail.

### 8.4 Does the error format invite phrasing-game-tax friction?

NO. Justification: the gate is deterministic on the (profile_id, files-set, env, binding) tuple. The agent has exactly two routes when MISSING_COMPANION emits:
1. **Add the named doc to the changeset.** The error message names the EXACT POSIX path; copy-paste — no inference required.
2. **Set the env-var skip token** (only if the profile declares one). The token string is literal, cited verbatim in the binding YAML; no fuzzy match.

There is NO third route. The agent CANNOT:
- Rephrase the task to bypass the gate (the gate runs after profile resolution; phrasing has already been excluded by P1’s LEXICAL_PROFILE_MISS structural fix).
- Substitute a different doc (path equality is exact post-normalization; probe4 verifies).
- Retry-with-different-wording (the only inputs that change the outcome are the files-set and the env, neither of which is wording).

Friction-budget contribution: a missing-companion ADVISORY does not fail admission in P2.a (`ok=True`), so `friction_budget_used` does not increment from this issue alone. In P2.b after promotion to SOFT_BLOCK, repeated attempts that don’t alter the files-set will be detected as SLICING_PRESSURE by P1’s existing detection logic. This makes the gate self-instrumenting: any phrasing-game pressure on it would surface in the existing P1 friction-pattern logs, not as a new metric.

---

## §9. P2 Phase Plan (within-packet sub-phasing)

P2 ships in three named sub-phases that mirror the P1/MIGRATION_PATH soft-block-first principle:

| Sub-phase | Duration | Severity table entry | Behavior |
|-----------|----------|-----------------------|----------|
| P2.a SHADOW | days 1–14 from PR merge | `missing_companion: advisory` | MISSING_COMPANION emits as ADVISORY; admission proceeds (`ok=True`); divergence logger records every emission. |
| P2.b SOFT_BLOCK | day 15+ if cutover criteria met | `missing_companion: soft_block` | Same emission path; severity table promotes to SOFT_BLOCK; admission refuses without companion or skip token. |
| P2.c HARD_BLOCK | OUT OF P2 SCOPE | reserved | Flagged for separate packet. Requires re-architecture of skip-token semantics (Universal §15 G6 — once HARD_STOP, no override path). |

Cutover criteria (P2.a → P2.b), mirrored from MIGRATION_PATH §Phase 2:
1. Shadow log shows ≥ 95% AGREE rate between P2.a advisory emissions and the eventual outcome (i.e. the agent did add the companion or use the skip token in ≥ 95% of MISSING_COMPANION cases within 24h).
2. Zero false positives where a profile incorrectly demanded a companion that was not authoritative. **Detection mechanism (option a):** any agent that believes a MISSING_COMPANION emission was a false positive sets env marker `COMPANION_FALSE_POSITIVE=1` and re-runs admit(); the skip-token logger records a `false_positive_claim` row (same JSONL, distinct `event_type` field) with `profile`, `source_files`, `agent_id`, and `ts`. These rows surface in the weekly digest under a "Claimed false positives" section; a human reviewer inspects each claim against the binding YAML before the P2.b promotion decision. Non-zero claimed-false-positive count blocks automatic promotion — human sign-off required. (Option b — require human inspection of every MISSING_COMPANION emission — was considered and rejected as operationally unsustainable at scale.)
3. Skip-token log shows fewer than N_skip_threshold uses per week (default N_skip_threshold = 5 across all profiles; tunable in binding).

Promotion mechanism: edit `binding.severity_overrides["missing_companion"] = "soft_block"` in the binding YAML and re-deploy. No code change. Rollback: revert the YAML line. One-revert-commit reversibility preserved per P1 MIGRATION_PATH discipline.

---

## §10. LOC Budget & Module Map

Sub-packet sizing target: 800–1200 LOC. Allocation:

| Surface | LOC | Notes |
|---------|-----|-------|
| `dataclasses.py` (additive fields) + `profile_loader.py` (parse + validate) | ~80 | Two new fields, two new validation warnings. |
| `admission_engine.py` (`_check_companion_required` + insertion) | ~150 | New helper plus 2-3 line wiring in `admit()`. |
| `companion_skip_logger.py` (NEW small module, ~150 LOC cap) | ~150 | Atomic JSONL append + env-resolved agent_id. Mirrors `divergence_logger.py` shape. |
| Weekly digest generator (`scripts/companion_skip_digest.py` or extend `authority_drift_surface`) | ~150 | Markdown rendering of the skip log. |
| Test fixtures (5 probes per §7) | ~250 | One file per probe, pytest idioms. |
| Binding YAML entries (5 profile candidates per §6) | ~50 | Append-only edits to `architecture/topology_v_next_binding.yaml`. |
| **Total** | **~830** | Within 800–1200 budget; ~370 LOC headroom for unforeseen integration glue. |

No existing module exceeds its P1 cap as a result of P2:
- `dataclasses.py` (P1 cap 150 LOC) gains ~10 LOC for two field declarations → headroom OK.
- `profile_loader.py` (P1 cap 300 LOC) gains ~70 LOC for parse + validate → within cap.
- `admission_engine.py` (P1 cap 600 LOC) gains ~150 LOC for the helper + wiring → within cap.

---

## §11. P1 Compatibility Confirmation

The P2 mechanism is strictly additive against P1:
- `CoverageMap.profiles` shape is UNCHANGED (per §0 INCONSISTENCY-1 resolution).
- `BindingLayer` gains two new fields with `default_factory=dict` defaults — existing P1 binding YAML loads with NO modification.
- `admit()` public signature is UNCHANGED — P1 SCAFFOLD §6’s Codex single-import contract holds.
- `AdmissionDecision` struct shape is UNCHANGED — `missing_companion` and `companion_skip_token_used` are NEW issue codes flowing into the existing `issues` field.
- The cli_integration_shim (`scripts/topology_v_next/cli_integration_shim.py`) is UNCHANGED — shadow comparison sees the new issues automatically because `divergence_logger.classify_divergence` reads from the existing struct.
- The 5 P2 initial profile entries are additive YAML-only edits to `architecture/topology_v_next_binding.yaml`; no removal of existing entries.

Confirmed: P2 is a clean additive extension. No P1 regression risk. No P1 SCAFFOLD §7.4 anti-pattern triggered (no `derive_intent_from_phrase`, no phrase-substring check inside `coverage_map.py` or `composition_rules.py`, no `task_phrase` parameter on any new public function).

---

## §12. Open Items for P3 (NOT in P2 scope)

For traceability:
1. HARD_BLOCK escalation for any profile (Universal §15 G6 implications must be re-architected first).
2. Cross-profile companion declarations (e.g., a single edit triggering multiple profiles each requiring different docs — current behavior emits one issue per missing doc per profile; reviewer-facing UX may need polish).
3. Auto-suggest companion from `architecture/docs_registry.yaml` when a new profile is added (currently human-authored).
4. Skip-token quota / rate-limit per agent_id (currently log-only; no admission feedback).
5. Integration with the weekly `authority_drift_surface` task to cross-correlate skip-token uses with subsequent drift_score increases for the skipped doc — closes the loop empirically.

---

## §13. Module-to-Closure-Surface Traceability

| Module / Surface | What it closes from §1 motivation |
|------------------|-----------------------------------|
| `BindingLayer.companion_required` field | Declares the source→authority-doc pairing at the binding layer (was undeclared, hence undetectable). |
| `BindingLayer.companion_skip_tokens` field | Provides the audited override path so the gate is not unfalsifiable. |
| `_check_companion_required()` in admission_engine | Converts the question from “find drift later” into “block at admit time.” |
| `companion_skip_logger.py` + weekly digest | Ensures every override is visible to humans within ≤ 7 days. |
| 5 initial profile entries (§6) | Brings the 3 TOPOLOGY BLOCKING + 2 high-churn doc surfaces under the gate immediately. |
| Phase plan (§9) | Soft-block-first preserves the P1 reversibility discipline. |

Every surface introduced by P2 maps to either a structural fix for the §1 motivation OR a guardrail for the structural fix itself. No orphan surfaces.
