# P1 Topology v_next Additive Route — SCAFFOLD (rev 1.1)

Created: 2026-05-15
Revised: 2026-05-15 — P1.0 critic FIX_REQUIRED applied (SEV-1 C1 + SEV-2 M1–M6)
Status: SPEC ONLY — no implementation code; this document is the build contract for P1
Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/{UNIVERSAL_TOPOLOGY_DESIGN.md, ZEUS_BINDING_LAYER.md, MIGRATION_PATH.md, HIDDEN_BRANCH_LESSONS.md}

**STRUCTURAL REFRAMING (rev 1.1):** P1 is STRUCTURES-ONLY. MIGRATION_PATH.md Phase 1 mandate is explicit: "structures built and populated but NOT consulted." P1.0 SCAFFOLD deviated from this by including cli_integration_shim, divergence_logger, shadow_compare, --v-next-shadow CLI flag, JSONL log schema, and a 7-day probe sequence. All of those belong in P3 (`topology_v_next_phase2_shadow`). This revision honors MIGRATION_PATH Phase 1 authority and restricts P1 to 10 modules that EXIST, can be unit-tested, and are Codex-importable — but NO production caller invokes them yet.

Modules removed from P1 scope (deferred to P3):
- `cli_integration_shim.py` — wire-up shim
- `divergence_logger.py` — JSONL append + classify + friction detection
- `shadow_compare()` function
- `--v-next-shadow` CLI flag
- JSONL divergence log schema and retention policy
- 7-day shadow probe sequence

P3 adds shim + wire-up diff against actual `scripts/topology_doctor.py:2636 run_navigation`. P3 also adds divergence_summary module + CLI for Day-7 aggregate. JSONL logger ships in P3 with its concurrency contract.

---

## §0. Input Inconsistencies Found (binding instruction precedence)

INCONSISTENCY-1: GOAL vs MIGRATION_PATH §Phase 1 — **PRIOR RESOLUTION REVERTED**.
- P1.0 SCAFFOLD resolved this in favor of GOAL (treating P1 as "P1+P2 telescoped"). That self-resolution unilaterally telescoped MIGRATION_PATH Phase 1+2 and set a governance precedent.
- Rev 1.1 reverts: MIGRATION_PATH Phase 1 authority is honored. GOAL is the binding *intent* document; MIGRATION_PATH is the binding *sequencing* document. When they conflict on implementation order, MIGRATION_PATH governs sequence. GOAL's per-call shadow vision is deferred to P3 as specified.
- Flag for ZEUS_BINDING reviewer: docs registry should reconcile GOAL vs MIGRATION_PATH explicitly. This SCAFFOLD does not unilaterally resolve it; it defers.

INCONSISTENCY-2: Universal §9 Companion-Loop-Break vs Universal §8 Cohort.
- Universal §9 declares "§8 cohort pattern specialized to a profile's declared 2-file companion pair. ONE mechanism, two failure modes." The existing `_apply_companion_loop_break` in topology_doctor_digest.py:1440 is a separate code path. v_next must implement both as ONE function.
- Resolution: SCAFFOLD module `companion_loop_break.py` is small (≤200 LOC) and delegates to `composition_rules.cohort_admit()`. No second mechanism.

INCONSISTENCY-3: ZEUS_BINDING_LAYER §3 lists `.claude/hooks/**` as `CREDENTIAL_OR_AUTH_SURFACE`. Universal §5 lists CREDENTIAL_OR_AUTH_SURFACE for "credential files, auth token stores, permission manifests." Hook dispatch promotion is permitted by Universal §10 ("Project binding layers may promote any advisory to soft_block"). **FLAG FOR ZEUS_BINDING REVIEWER AWARENESS**: Universal §10 permits severity promotion, not category re-classification. Whether hook dispatch promotion constitutes an allowed severity promotion or an impermissible category reclassification requires explicit ZEUS_BINDING reviewer sign-off; this SCAFFOLD does not dismiss it as resolved.

Additional Inconsistencies Found (rev 1.1):
- INCONSISTENCY-4: P1.0 §7.2 table claimed SLICING_PRESSURE "DETECTED only in P1 (logged in shadow as DiagnosisEntry when 3 attempts shrink scope inside 30 min)." This is false: detection requires `friction_state` plumbing from the CLI shim, which does not exist in P1. With no shim and no `friction_state` supplier, detection never fires. The table overclaimed "partial closure." Corrected in §7.2 below.
- INCONSISTENCY-5: P1.0 §7.2 claimed CLOSED_PACKET_STILL_LOAD_BEARING as "STRUCTURAL closure" via `admission_engine.py`, but the internal helpers list at §1.9 omitted the helper that would perform the authority status check. The module that "closes" a pattern must contain the helper that checks it. Corrected: `_check_authority_status` added to admission_engine internal helpers (§1.5 below).
- INCONSISTENCY-6: P1.0 §7.2 claimed ADVISORY_OUTPUT_INVISIBILITY "STRUCTURAL closure" for multi-call detection. Multi-call pattern requires the divergence_logger/analyzer pipeline, which is deferred to P3. The struct-level fix (AdmissionDecision.issues top-level field) closes the single-call aspect only. Multi-call aspect deferred. Corrected in §7.2.

---

## §1. Module Layout

Root: `scripts/topology_v_next/`
Total module count: 10 (incl. `__init__.py` and dataclasses module)
Summed LOC budget (cap): ≤ 1800 LOC (reduced from 2500 after removing shim + logger). Per-module values below are CAPS, not targets.

### 1.1 `scripts/topology_v_next/__init__.py` (~30 LOC cap)
Re-exports the public API for Codex-invocable single-import access.
```python
from .admission_engine import admit, AdmissionDecision, Severity, Intent
from .profile_loader import load_binding_layer
__all__ = ["admit", "AdmissionDecision", "Severity", "Intent", "load_binding_layer"]
```
Imports: stdlib only.

### 1.2 `scripts/topology_v_next/dataclasses.py` (~150 LOC cap)
Pure data declarations; no logic.
Classes:
- `class Severity(str, Enum): ADMIT, ADVISORY, SOFT_BLOCK, HARD_STOP`
- `class Intent(str, Enum): plan_only, create_new, modify_existing, refactor, audit, hygiene, hotfix, rebase_keepup, other` + zeus extension values from ZEUS_BINDING_LAYER §2 (`zeus.settlement_followthrough`, `zeus.calibration_update`, `zeus.data_authority_receipt`, `zeus.topology_tooling`)
- `class FrictionPattern(str, Enum): LEXICAL_PROFILE_MISS, UNION_SCOPE_EXPANSION, SLICING_PRESSURE, PHRASING_GAME_TAX, INTENT_ENUM_TOO_NARROW, CLOSED_PACKET_STILL_LOAD_BEARING, ADVISORY_OUTPUT_INVISIBILITY`
- `@dataclass(frozen=True) class IssueRecord: code: str; path: str; severity: Severity; message: str; metadata: dict[str, Any]`
- `@dataclass(frozen=True) class DiagnosisEntry: pattern: FrictionPattern; evidence: str; resolution_path: str`
- `@dataclass(frozen=True) class AdmissionDecision: ok: bool; profile_matched: str | None; intent_class: Intent; severity: Severity; issues: tuple[IssueRecord, ...]; companion_files: tuple[str, ...]; missing_phrases: tuple[str, ...]; closest_rejected_profile: str | None; friction_budget_used: int; diagnosis: DiagnosisEntry | None; kernel_alerts: tuple[IssueRecord, ...]; def to_dict(self) -> dict[str, Any]`
- `@dataclass(frozen=True) class CoverageMap: profiles: dict[str, tuple[str, ...]]; orphaned: tuple[str, ...]; hard_stop_paths: tuple[str, ...]`
- `@dataclass(frozen=True) class CohortDecl: id: str; profile: str; intent_classes: tuple[Intent, ...]; files: tuple[str, ...]; description: str`
- `@dataclass(frozen=True) class BindingLayer: project_id: str; intent_extensions: tuple[Intent, ...]; coverage_map: CoverageMap; cohorts: tuple[CohortDecl, ...]; severity_overrides: dict[str, Severity]; high_fanout_hints: tuple[dict[str, Any], ...]; artifact_authority_status: dict[str, dict[str, Any]]`
Imports: `from dataclasses import dataclass, field`; `from enum import Enum`; `from typing import Any`.

### 1.3 `scripts/topology_v_next/profile_loader.py` (~300 LOC cap)
Loads ZEUS_BINDING_LAYER YAML into typed `BindingLayer` dataclass. Validates schema. Reports unknown fields without crashing.
Public:
- `def load_binding_layer(path: Path | str) -> BindingLayer` — single source path; no auto-discovery, no merging. Codex-invocable: `load_binding_layer("architecture/topology_v_next_binding.yaml")`. Default path is `architecture/topology_v_next_binding.yaml`; raises `FileNotFoundError` with a message naming the path when absent (m1 minor: load order documented here, not hidden in admission_engine). Stub binding YAML ships as part of P1.3 deliverable (see §10).
- `def validate_binding_layer(bl: BindingLayer) -> list[str]` — returns list of warnings (gaps in coverage, expired authority TTLs, intent_extensions missing namespace prefix). Returns empty list if clean. Does NOT raise on warnings — diagnostic only.
Imports: `yaml`, `pathlib.Path`, `.dataclasses` types.

### 1.4 `scripts/topology_v_next/intent_resolver.py` (~200 LOC cap)
**Critical anti-sidecar property**: this module does NOT derive intent from `task` phrase. Intent is supplied by caller as a typed `Intent` enum value (or string that resolves to one). The "resolver" only validates and normalizes the supplied intent value.
Public:
- `def resolve_intent(intent_value: str | Intent | None, *, binding: BindingLayer) -> tuple[Intent, list[IssueRecord]]` — returns the validated Intent and a list of issues (e.g., `intent_enum_unknown` ADVISORY when caller supplied a string not in enum). When intent is `None`, returns `Intent.other` plus an ADVISORY `intent_unspecified` issue.
- `def is_zeus_intent(intent: Intent) -> bool` — namespace check.
Phrase is NEVER an input here. (See §7 self-check.)
Imports: `.dataclasses` types.

### 1.5 `scripts/topology_v_next/admission_engine.py` (~600 LOC cap)
The orchestrator implementing Universal §4 Profile Matching Algorithm steps 1–8.
Public:
- `def admit(intent: str | Intent | None, files: list[str], hint: str = "", *, binding: BindingLayer | None = None, friction_state: dict[str, Any] | None = None) -> AdmissionDecision` — sole public entry. If `binding` is None, calls `profile_loader.load_binding_layer("architecture/topology_v_next_binding.yaml")`; raises `FileNotFoundError` with message naming the path if absent. Returns full AdmissionDecision struct per Universal §2.3 / §11.
- Internal helpers: `_run_kernel`, `_resolve_intent`, `_resolve_candidates`, `_apply_composition`, `_apply_companion_loop`, `_apply_severity_overrides`, `_check_authority_status`, `_assemble_diagnosis`, `_increment_friction_budget`.

`_check_authority_status(file_paths: list[str], artifact_authority_status: dict[str, dict[str, Any]]) -> list[IssueRecord]`: checks each touched file against `binding.artifact_authority_status`. Emits `authority_status_stale` ADVISORY when `last_confirmed` exceeds `confirmation_ttl_days`, and `closed_packet_authority` ADVISORY when `status == "CURRENT_HISTORICAL"`. This helper is unit-testable in P1; it has no production caller until P3 wires the shim. (Fix for INCONSISTENCY-5 / critic M3: this was the missing helper that §7.2 previously attributed CLOSED_PACKET_STILL_LOAD_BEARING closure to.)

Friction-budget handling: `friction_state` is an OPTIONAL dict supplied by the CLI shim (which does not exist in P1). The engine reads/increments `attempts_this_session` only if state is supplied. When omitted (Codex one-shot calls or all P1 invocations), `friction_budget_used` defaults to 1 and no SLICING_PRESSURE detection runs. State is held by the CALLER, not by a v_next service. (See §7 self-check.)

CRITICAL anti-sidecar property: `task` / `task_phrase` is NOT a parameter. Only `intent`, `files`, `hint`. The hint flows only into `composition_rules.explain_rejected()` and `closest_rejected_profile`; it cannot influence the matched profile.

Imports: all sibling modules, `.dataclasses` types, `time`.

### 1.6 `scripts/topology_v_next/hard_safety_kernel.py` (~250 LOC cap)
Runs O(1) per file via prefix/glob matching. Returns kernel alerts independent of profile selection.
Public:
- `def kernel_check(files: list[str], *, binding: BindingLayer) -> list[IssueRecord]` — returns one IssueRecord per file matching a hard_stop pattern. Severity = HARD_STOP. metadata includes `category` from binding (LIVE_SIDE_EFFECT_PATH, CANONICAL_TRUTH_REWRITE, etc.).
- `def is_hard_stopped(files: list[str], binding: BindingLayer) -> bool` — convenience boolean for early-exit in admission_engine.
Imports: `fnmatch`, `.dataclasses` types.

### 1.7 `scripts/topology_v_next/coverage_map.py` (~250 LOC cap)
Resolves files to candidate profiles via Coverage Map (Universal §6).
Public:
- `def resolve_candidates(files: list[str], coverage_map: CoverageMap) -> dict[str, set[str]]` — returns `{file_path: {profile_id, ...}}` for each file. Empty set means coverage gap.
- `def coverage_gaps(candidates: dict[str, set[str]], coverage_map: CoverageMap) -> list[IssueRecord]` — emits `coverage_gap` ADVISORY for files in no profile, no orphan list, no hard_stop list.
- `def union_candidate_profiles(candidates: dict[str, set[str]]) -> set[str]` — collapse per-file candidate sets into the union of profiles touched by the change.
Imports: `fnmatch`, `pathlib.PurePosixPath`, `.dataclasses` types.

### 1.8 `scripts/topology_v_next/composition_rules.py` (~300 LOC cap)
Implements Universal §7 Rules C1–C4 and §8 Cohort Admission. §9 is delegated here; companion_loop_break.py is a thin shim.
Public:
- `def cohort_admit(intent: Intent, files: list[str], cohorts: tuple[CohortDecl, ...]) -> CohortDecl | None` — returns matching cohort or None. Match = all files in cohort.files (after glob expansion of `{new_module}` patterns) AND intent in cohort.intent_classes.
- `def apply_composition(intent: Intent, files: list[str], candidates: dict[str, set[str]], binding: BindingLayer) -> tuple[str | None, list[IssueRecord]]` — returns (resolved_profile_id_or_None, issues). Tries C1 (additive companion) → C2 (subsumption) → C3 (explicit union profile) → C4 (cohort, via cohort_admit). Returns `composition_conflict` SOFT_BLOCK when nothing resolves.
- `def explain_rejected(candidates: dict[str, set[str]], binding: BindingLayer, hint: str) -> str | None` — returns `closest_rejected_profile` for diagnostic only. Hint used ONLY here, ONLY for ranking; never gates routing.
Imports: `.dataclasses` types, `.coverage_map`.

### 1.9 `scripts/topology_v_next/companion_loop_break.py` (~200 LOC cap)
Compatibility shim per Universal §9. Delegates to composition_rules.cohort_admit().
Public:
- `def companion_loop_break(intent: Intent, files: list[str], binding: BindingLayer) -> tuple[bool, str | None, IssueRecord | None]` — returns (mode_a_admit_bool, mode_b_missing_companion_path_or_None, issue_record_or_None). Mode A: companion declared and present in files → auto-admit. Mode B: companion declared and absent → SOFT_BLOCK (in P1 logged in AdmissionDecision.issues only, not enforced at the gate — no gate exists until P3).
- Internally just enumerates 2-file cohorts in binding.cohorts and calls cohort_admit().
Imports: `.dataclasses`, `.composition_rules`.

### 1.10 `scripts/topology_v_next/severity_overrides.py` (~120 LOC cap)
Applies ZEUS_BINDING_LAYER §4 severity override table to the candidate issue list.
Public:
- `def apply_overrides(issues: list[IssueRecord], overrides: dict[str, Severity]) -> list[IssueRecord]` — returns new list with severities remapped per override dict. No mutation; returns new IssueRecord instances.
- `def effective_severity(issues: list[IssueRecord]) -> Severity` — returns the maximum severity across all issues (HARD_STOP > SOFT_BLOCK > ADVISORY > ADMIT).
Imports: `.dataclasses` types.

---

## §2. Test Layout

Root: `tests/topology_v_next/`
All test globs: `tests/**/test_*.py` (subdirectory-aware; covers both flat and nested layouts).

### 2.1 Unit tests (one file per module)
- `tests/topology_v_next/test_dataclasses.py` — frozen-ness, to_dict roundtrip, Intent enum coverage incl. zeus.* extensions
- `tests/topology_v_next/test_profile_loader.py` — YAML loading, missing-field defaults, validate_binding_layer warnings; FileNotFoundError message names the missing path
- `tests/topology_v_next/test_intent_resolver.py` — Intent enum match, unknown intent → ADVISORY, None → other+ADVISORY, **assert task/phrase is NOT a parameter** (introspect signature)
- `tests/topology_v_next/test_hard_safety_kernel.py` — every binding hard_stop_paths pattern flagged for at least one canonical file; non-matching paths return empty
- `tests/topology_v_next/test_coverage_map.py` — multi-profile candidates, orphan detection, gap reporting, union_candidate_profiles set algebra
- `tests/topology_v_next/test_composition_rules.py` — C1 additive companion, C2 subsumption, C3 explicit union, C4 cohort delegation, hint-never-routes property test
- `tests/topology_v_next/test_companion_loop_break.py` — Mode A admit (companion present), Mode B issue (companion absent), assert delegation to cohort_admit
- `tests/topology_v_next/test_admission_engine.py` — full §4 algorithm trace per step; HARD_STOP short-circuit; AdmissionDecision struct field population; friction_budget_used defaulting when no state supplied; _check_authority_status emits authority_status_stale when TTL exceeded
- `tests/topology_v_next/test_severity_overrides.py` — override application, effective_severity ordering, no-mutation property

### 2.2 Friction-pattern unit tests (P1-testable patterns only)
Located at `tests/topology_v_next/regression/`:
- `test_friction_LEXICAL_PROFILE_MISS.py` — same files, two hint strings, ASSERT v_next produces same profile_matched both times
- `test_friction_UNION_SCOPE_EXPANSION.py` — coherent multi-profile change (e.g., new test + test_topology.yaml cohort), ASSERT v_next admits via cohort
- `test_friction_PHRASING_GAME_TAX.py` — same intent+files with 3 different hints, ASSERT identical profile_matched + friction_budget_used unchanged across calls
- `test_friction_INTENT_ENUM_TOO_NARROW.py` — supply unknown intent string, ASSERT ADVISORY `intent_enum_unknown` raised + decision proceeds with `Intent.other`
- `test_friction_CLOSED_PACKET_STILL_LOAD_BEARING.py` — touch a file whose binding artifact_authority_status row is `CURRENT_HISTORICAL`, ASSERT ADVISORY `authority_status_stale` or `closed_packet_authority` raised via _check_authority_status
- `test_friction_ADVISORY_OUTPUT_INVISIBILITY.py` — admit with non-empty issues, ASSERT AdmissionDecision.issues populated even when ok=True; ASSERT to_dict() output contains all issues at top level (not buried)

Deferred to P3 (require shim/friction_state plumbing not present in P1):
- `test_friction_SLICING_PRESSURE.py` — requires friction_state with N=3 attempts from CLI shim; not testable in P1. Ships in P3.
- Integration test `tests/topology_v_next/integration/test_shadow_mode_e2e.py` — requires --v-next-shadow CLI flag. Ships in P3.

---

## §3. Wire-Up Note (P1 does NOT wire up)

P1 modules exist and are importable but NO production caller invokes them.

The actual wire-up diff goes in P3 (`topology_v_next_phase2_shadow` packet). For reference:
- Integration point: `scripts/topology_doctor.py` `run_navigation()` at line 2636
- `run_navigation` inlines the admission payload via `build_digest(...)` at line 2665 (NOT via a function named `_assemble_navigation_payload` — that name does not exist in the codebase)
- P3 adds the `shadow_compare` shim call after `build_digest` returns, guarded by a `--v-next-shadow` CLI flag
- P3 also adds `divergence_logger.py` with JSONL append + atomic write (tmp+rename) + concurrency contract

No wire-up diff in this document. Fabricated diff context from P1.0 is removed.

---

## §4. API Contract — Codex-Invocable

Single-import invocation requirement: any agent (Claude Code, Codex CLI, gemini, raw Python) must be able to call v_next admission without harness magic.

Verified shape:
```python
# Codex one-shot example (zero env, zero CLI):
from scripts.topology_v_next import admit, AdmissionDecision

decision: AdmissionDecision = admit(
    intent="create_new",
    files=["scripts/topology_v_next/admission_engine.py"],
    hint="add v_next admission engine",  # OPTIONAL; never routes
)

assert isinstance(decision, AdmissionDecision)
print(decision.to_dict())  # JSON-serializable dict
```

Function signature pinned:
```python
def admit(
    intent: str | Intent | None,
    files: list[str],
    hint: str = "",
    *,
    binding: BindingLayer | None = None,
    friction_state: dict[str, Any] | None = None,
) -> AdmissionDecision: ...
```

Properties guaranteed:
- All inputs JSON-serializable (str, list[str], None).
- Output has `.to_dict()` returning JSON-serializable dict (Universal §2.3 fields).
- `binding=None` loads from `architecture/topology_v_next_binding.yaml`; raises `FileNotFoundError` naming that path if absent. Stub binding YAML ships in P1.3.
- No reliance on Claude-Code-injected env vars.
- No reliance on argparse parsing — Python-callable directly.
- Function does not perform any filesystem writes. Pure read of binding YAML.

---

## §5. Self-Check

### 5.1 Sidecar Risk per New Field
For each AdmissionDecision field (Universal §2.3), justify why it is NOT itself a sidecar:

- `ok: bool` — derived from `severity ∈ {ADMIT, ADVISORY}`. Pure function of severity. No sidecar.
- `profile_matched: ProfileId | null` — output of §4 algorithm. The TARGET output. Not a sidecar.
- `intent_class: Intent` — caller-supplied (validated). NOT computed from phrase. No sidecar.
- `severity: Severity` — derived from issues + binding severity_overrides via deterministic lookup. Pure function. No sidecar.
- `issues: tuple[IssueRecord, ...]` — concrete diagnostic records produced by the algorithm steps. The §11 output normalization fix. Surfacing them is the FIX for ADVISORY_OUTPUT_INVISIBILITY. Not a sidecar.
- `companion_files: tuple[str, ...]` — output of cohort/composition rules. Diagnostic only. No sidecar.
- `missing_phrases: tuple[str, ...]` — generated by `composition_rules.explain_rejected()` purely for human-readable diagnosis. Phrases are OUTPUT, never input. Cannot become a sidecar.
- `closest_rejected_profile: ProfileId | null` — diagnostic only. Hint-driven; never routes. No sidecar.
- `friction_budget_used: int` — SUSPICIOUS field. Justification: the engine reads this from CALLER-supplied `friction_state` dict. There is NO v_next service maintaining session state. When `friction_state` is None (all P1 invocations), value is 1 unconditionally. Caller (P3 shim) holds the counter; P1 never does. Not a sidecar in P1.
- `diagnosis: DiagnosisEntry | null` — output of §12 Failure-as-Diagnosis. Diagnostic only. No sidecar.
- `kernel_alerts: tuple[IssueRecord, ...]` — output of Hard Safety Kernel. Algorithmic output. No sidecar.

CONCLUSION: no sidecar fields. friction_budget_used is caller-state-echo in P1 with explicit boundary.

### 5.2 Friction Patterns — honest P1 closure status

| Pattern | P1 closure status | Evidence |
|---------|--------------------|---------|
| LEXICAL_PROFILE_MISS | **Traceable closed (P1)** | Intent is the routing key; phrase never enters profile selection. Same files + same intent → same profile, regardless of phrase. intent_resolver.py is the structural fix. |
| UNION_SCOPE_EXPANSION | **Traceable closed (P1)** | Cohort declarations (Universal §8 / Zeus binding §6) + Composition Rules C1–C4 admit coherent multi-profile change sets. composition_rules.py is the structural fix. |
| PHRASING_GAME_TAX | **Traceable closed (P1)** | Same intent + files always returns same AdmissionDecision regardless of phrase. Deterministic on (intent, files). No phrase input to coverage_map or composition_rules. |
| INTENT_ENUM_TOO_NARROW | **Traceable closed (P1)** | Binding-layer `intent_extensions` registers project-specific intents. Unknown intent → `Intent.other` + ADVISORY (not crash). profile_loader.validate_binding_layer detects missing namespace prefix. |
| ADVISORY_OUTPUT_INVISIBILITY | **Partial (P1 single-call aspect only)** | AdmissionDecision struct surfaces `issues` at top level; `to_dict()` always includes them. `ok=True` with non-empty issues is now a typed condition. Multi-call/aggregate detection requires P3 divergence_summary module. |
| SLICING_PRESSURE | **Structures exist; detection deferred to P3** | `friction_state` parameter and `FrictionPattern.SLICING_PRESSURE` enum value exist in P1 dataclasses. Detection never fires in P1: no CLI shim supplies friction_state. Gate deferred to P3 when shim adds friction_state plumbing. NOT partial-closed in P1 — structures only. |
| CLOSED_PACKET_STILL_LOAD_BEARING | **Partial (P1 single-call helper only)** | `_check_authority_status` helper inside admission_engine checks artifact_authority_status per call and emits IssueRecord. Helper is unit-testable. No production caller until P3 wire-up. Multi-call pattern (loading a closed packet across sessions) requires P3 aggregate. |

Summary: 4 traceable closed + 1 partial (ADVISORY_OUTPUT_INVISIBILITY) + 1 partial (CLOSED_PACKET_STILL_LOAD_BEARING) + 1 structures-only deferred (SLICING_PRESSURE) to P3.

### 5.3 Anti-Meta-Pattern Proof — Why This Is Not Sidecar #8

HIDDEN_BRANCH_LESSONS Cross-Iteration Meta-Pattern: every prior iteration "added a LAYER before or after `_resolve_profile`, never changed `_resolve_profile` itself or the `(task, files)` kernel."

This SCAFFOLD's structural change:
1. `admit()` does NOT call `_resolve_profile`. It does NOT call `topology_doctor_digest.build_digest()`. It is a NEW admission unit `(intent, files, hint)` running alongside, not on top of, the existing `(task, files)` unit.
2. The phrase (`hint`) is a strict OUTPUT contributor (closest_rejected_profile diagnostic), never an input to `coverage_map.resolve_candidates()` or `composition_rules.apply_composition()`. Those functions do not accept a phrase parameter.
3. `intent_resolver.resolve_intent()` validates a CALLER-SUPPLIED enum value. There is no `derive_intent_from_phrase()` function anywhere in v_next.
4. P1 rollback is one revert commit (delete `scripts/topology_v_next/`). Current admission path is unchanged. P1 has zero production call sites.
5. The P3 shim will be the ONE wire-up point; ≤ 25 LOC of diff. If the shim grows beyond that or sprouts additional call sites, the design has reverted to sidecar shape.

**Sidecar-risk PASS**: P1 ships structures that P3 integrates. The structures are the minimum viable replacement for the current admission unit, not a parallel rail. The "no integration" shape is exactly what MIGRATION_PATH Phase 1 specifies. This is not sidecar avoidance — the keying function `(intent, files, hint)` genuinely changes structurally per HIDDEN_BRANCH_LESSONS Cross-Iteration Meta-Pattern. The structures are the deliverable, not a preparatory step for a sidecar.

### 5.4 Anti-pattern catch list (review checklist for the P1 implementation PR)
Reviewers MUST grep the v_next implementation PR for these anti-patterns:
- `def derive_intent_from_phrase` / `def infer_intent` / `def guess_intent` → FAIL
- Any phrase-substring check inside `coverage_map.py` or `composition_rules.py` → FAIL
- Any call to `topology_doctor_digest.build_digest` inside v_next → FAIL (would re-couple to old kernel)
- Any import of `cli_integration_shim` or `divergence_logger` (not present in P1) → FAIL
- Any call site in `scripts/topology_doctor.py` (no wire-up in P1) → FAIL
- `task_phrase` or `task` parameter on any v_next public function → FAIL

---

## §6. Open Items for P3 (NOT in P1 scope)

For traceability — flagged so P3 packet (`topology_v_next_phase2_shadow`) authors know where to pick up:
1. `cli_integration_shim.py` — wire-up point in `run_navigation()` at `scripts/topology_doctor.py:2636`.
2. `divergence_logger.py` — JSONL append with atomicity contract (write-to-tmp + rename). Note: append-only semantics and tmp+rename are reconciled in P3: the JSONL is append-only as a logical property; each individual write is atomic via tmp+rename before the append is visible. These are not mutually exclusive — tmp+rename achieves atomic append.
3. `--v-next-shadow` CLI flag and `shadow_compare()` function.
4. `divergence_summary` module + CLI for Day-7 AGREE-rate aggregate. This is the owner of Day-7 AGREE-rate; without it, ADVISORY_OUTPUT_INVISIBILITY multi-call remains unmonitored (INCONSISTENCY-4 remediation is in P3).
5. SLICING_PRESSURE detection gate (requires friction_state from shim).
6. Integration test `test_shadow_mode_e2e.py`.
7. 7-day shadow probe sequence (Days 1–7).
8. Cutover order from MIGRATION_PATH §Phase 3 (per-profile cutover with config flag).
9. Friction-budget session counter persistence (currently caller-supplied only in P3 shim).
10. MIGRATION_PATH §Phase 1 vs GOAL reconciliation — docs registry action for ZEUS_BINDING reviewer.

---

## §7. Module-to-Friction-Pattern Traceability Matrix

| Module | Friction patterns it closes or touches |
|--------|----------------------------------------|
| dataclasses.py | ADVISORY_OUTPUT_INVISIBILITY (struct shape forces per-call visibility) |
| profile_loader.py | INTENT_ENUM_TOO_NARROW (binding intent_extensions + validate warns on missing namespace) |
| intent_resolver.py | LEXICAL_PROFILE_MISS (intent is routing key, not phrase) |
| hard_safety_kernel.py | (Universal §15 G1 — kernel must run regardless; no friction pattern but required invariant) |
| coverage_map.py | (foundation for composition; no single pattern direct-closure) |
| composition_rules.py | UNION_SCOPE_EXPANSION (C1–C4 + cohort), PHRASING_GAME_TAX (deterministic on intent+files) |
| companion_loop_break.py | UNION_SCOPE_EXPANSION (specialized 2-file cohort case) |
| admission_engine.py | PHRASING_GAME_TAX (deterministic orchestration), CLOSED_PACKET_STILL_LOAD_BEARING (_check_authority_status helper) |
| severity_overrides.py | (binding override application; no single friction pattern) |
| *(divergence_logger — P3)* | SLICING_PRESSURE (detection via shadow log), ADVISORY_OUTPUT_INVISIBILITY multi-call |
| *(cli_integration_shim — P3)* | (wire-up only; friction_state plumbing enables SLICING_PRESSURE detection) |

Every Universal §12 friction pattern maps to at least one module or an explicit P3 deferral. No pattern is orphaned.

---

## §8. Sub-Packet Decomposition

P1 is not a single big-bang implementation. Three independently testable sub-packets:

### P1.1 — Data Layer (~400 LOC cap)
**Deliverables**: `dataclasses.py` + `profile_loader.py` + `intent_resolver.py` + stub binding YAML at `architecture/topology_v_next_binding.yaml`.
**Tests**: `test_dataclasses.py`, `test_profile_loader.py`, `test_intent_resolver.py`, `test_friction_INTENT_ENUM_TOO_NARROW.py`, `test_friction_LEXICAL_PROFILE_MISS.py` (partial — intent_resolver component only).
**Exit criterion**: `pytest tests/topology_v_next/test_dataclasses.py tests/topology_v_next/test_profile_loader.py tests/topology_v_next/test_intent_resolver.py` all pass against stub binding YAML.
**Dependencies**: none.

### P1.2 — Admission Core (~700 LOC cap)
**Deliverables**: `hard_safety_kernel.py` + `coverage_map.py` + `composition_rules.py` + `companion_loop_break.py` + `admission_engine.py` (incl. `_check_authority_status`).
**Tests**: `test_hard_safety_kernel.py`, `test_coverage_map.py`, `test_composition_rules.py`, `test_companion_loop_break.py`, `test_admission_engine.py`, `test_friction_UNION_SCOPE_EXPANSION.py`, `test_friction_PHRASING_GAME_TAX.py`, `test_friction_CLOSED_PACKET_STILL_LOAD_BEARING.py`.
**Exit criterion**: `pytest tests/topology_v_next/` passes (P1.1 + P1.2 test files); `admit()` callable from bare Python with stub binding YAML.
**Dependencies**: P1.1 must be complete.

### P1.3 — Binding + Overrides + Public API (~500 LOC cap)
**Deliverables**: `severity_overrides.py` + `__init__.py` (public re-exports) + complete stub `architecture/topology_v_next_binding.yaml` (full Zeus binding structure, not just schema stub) + `test_severity_overrides.py` + `test_friction_ADVISORY_OUTPUT_INVISIBILITY.py` + `test_friction_PHRASING_GAME_TAX.py` (full cross-module).
**Tests**: `test_severity_overrides.py`, remaining regression tests, Codex one-shot invocation shape verified.
**Exit criterion**: `from scripts.topology_v_next import admit, AdmissionDecision` works; all `tests/topology_v_next/` and `tests/topology_v_next/regression/` tests pass (excluding deferred SLICING_PRESSURE and integration tests).
**Dependencies**: P1.2 must be complete.

Each sub-packet is ≤ 1000 LOC. Each has independent exit criteria. Each is independently reviewable.
