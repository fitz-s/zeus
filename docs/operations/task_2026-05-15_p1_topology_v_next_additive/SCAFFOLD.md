# P1 Topology v_next Additive Route — SCAFFOLD

Created: 2026-05-15
Status: SPEC ONLY — no implementation code; this document is the build contract for P1
Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/{UNIVERSAL_TOPOLOGY_DESIGN.md, ZEUS_BINDING_LAYER.md, MIGRATION_PATH.md, HIDDEN_BRANCH_LESSONS.md}

This SCAFFOLD specifies the parallel admission route (`scripts/topology_v_next/`) that ships alongside `scripts/topology_doctor.py`. Current admission stays sole authority. v_next runs as a shadow — every admission attempt also calls v_next; results compared and logged; ZERO hard blocks from v_next during P1.

---

## §0. Input Inconsistencies Found (binding instruction precedence)

INCONSISTENCY-1: GOAL vs MIGRATION_PATH §Phase 1.
- GOAL says P1 ships v_next "as a parallel route alongside current topology_doctor admission" with "v_next logs per-call divergence over a 7-day shadow window."
- MIGRATION_PATH.md §Phase 1 explicitly says v_next "structures are built and populated but not consulted" — Phase 1 logs only coverage gaps and kernel alerts, NOT per-call admission divergence. Per-call shadow comparison (`admit_v_next()` called for every admission) is MIGRATION_PATH §Phase 2.
- Resolution: GOAL is the binding instruction. This SCAFFOLD treats P1 as "P1-as-defined-by-GOAL" — Phase 1 structures + Phase 2 shadow comparison combined into a single 7-day shadow window. SOFT-BLOCK first principle preserved: v_next NEVER hard-blocks in P1; logs divergence only.
- Flag: the universal docs registry should reconcile this. P2 of the engineering package (the next packet after this SCAFFOLD ships) should clarify whether MIGRATION_PATH should be revised to absorb the per-call shadow logging into Phase 1, or whether GOAL should be re-titled "P1+P2 telescoped."

INCONSISTENCY-2: Universal §9 Companion-Loop-Break vs Universal §8 Cohort.
- Universal §9 declares "§8 cohort pattern specialized to a profile's declared 2-file companion pair. §8 is source of truth; §9 is the compatibility shim. ONE mechanism, two failure modes." This is internally consistent but the existing `_apply_companion_loop_break` in topology_doctor_digest.py:1440 is a separate code path. v_next must implement both as ONE function and surface §9 as a thin shim.
- Resolution: SCAFFOLD module `companion_loop_break.py` is small (≤200 LOC) and delegates to `composition_rules.cohort_admit()`. No second mechanism.

INCONSISTENCY-3: ZEUS_BINDING_LAYER §3 lists `.claude/hooks/**` as `CREDENTIAL_OR_AUTH_SURFACE`. Universal §5 lists CREDENTIAL_OR_AUTH_SURFACE for "credential files, auth token stores, permission manifests." Hook dispatch is not strictly auth, but the binding layer's explicit override is permitted by Universal §10 ("Project binding layers may promote any advisory to soft_block, or any soft_block to hard_stop. They may not demote hard_stop.") — promotion of hook dispatch into kernel is allowed. NO ACTION; flagged for P1 reviewer awareness.

---

## §1. Module Layout

Root: `scripts/topology_v_next/`
Total module count: 10 (incl. `__init__.py` and dataclasses module)
Summed LOC budget (cap): ≤ 2500 LOC. Per-module values below are CAPS, not targets.

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
- `def load_binding_layer(path: Path | str) -> BindingLayer` — single source path; no auto-discovery, no merging. Codex-invocable: `load_binding_layer("architecture/topology_v_next_binding.yaml")`.
- `def validate_binding_layer(bl: BindingLayer) -> list[str]` — returns list of warnings (gaps in coverage, expired authority TTLs, intent_extensions missing namespace prefix). Returns empty list if clean. Does NOT raise on warnings — diagnostic only.
Imports: `yaml`, `pathlib.Path`, `.dataclasses` types.

### 1.4 `scripts/topology_v_next/intent_resolver.py` (~200 LOC cap)
**Critical anti-sidecar property**: this module does NOT derive intent from `task` phrase. Intent is supplied by caller as a typed `Intent` enum value (or string that resolves to one). The "resolver" only validates and normalizes the supplied intent value.
Public:
- `def resolve_intent(intent_value: str | Intent | None, *, binding: BindingLayer) -> tuple[Intent, list[IssueRecord]]` — returns the validated Intent and a list of issues (e.g., `intent_enum_unknown` ADVISORY when caller supplied a string not in enum). When intent is `None`, returns `Intent.other` plus an ADVISORY `intent_unspecified` issue.
- `def is_zeus_intent(intent: Intent) -> bool` — namespace check.
Phrase is NEVER an input here. (See §7 self-check.)
Imports: `.dataclasses` types.

### 1.5 `scripts/topology_v_next/hard_safety_kernel.py` (~250 LOC cap)
Runs O(1) per file via prefix/glob matching. Returns kernel alerts independent of profile selection.
Public:
- `def kernel_check(files: list[str], *, binding: BindingLayer) -> list[IssueRecord]` — returns one IssueRecord per file matching a hard_stop pattern. Severity = HARD_STOP. metadata includes `category` from binding (LIVE_SIDE_EFFECT_PATH, CANONICAL_TRUTH_REWRITE, etc.).
- `def is_hard_stopped(files: list[str], binding: BindingLayer) -> bool` — convenience boolean for early-exit in admission_engine.
Imports: `fnmatch`, `.dataclasses` types.

### 1.6 `scripts/topology_v_next/coverage_map.py` (~250 LOC cap)
Resolves files to candidate profiles via Coverage Map (Universal §6).
Public:
- `def resolve_candidates(files: list[str], coverage_map: CoverageMap) -> dict[str, set[str]]` — returns `{file_path: {profile_id, ...}}` for each file. Empty set means coverage gap.
- `def coverage_gaps(candidates: dict[str, set[str]], coverage_map: CoverageMap) -> list[IssueRecord]` — emits `coverage_gap` ADVISORY for files in no profile, no orphan list, no hard_stop list.
- `def union_candidate_profiles(candidates: dict[str, set[str]]) -> set[str]` — collapse per-file candidate sets into the union of profiles touched by the change.
Imports: `fnmatch`, `pathlib.PurePosixPath`, `.dataclasses` types.

### 1.7 `scripts/topology_v_next/composition_rules.py` (~300 LOC cap)
Implements Universal §7 Rules C1–C4 and §8 Cohort Admission. §9 is delegated here; companion_loop_break.py is a thin shim.
Public:
- `def cohort_admit(intent: Intent, files: list[str], cohorts: tuple[CohortDecl, ...]) -> CohortDecl | None` — returns matching cohort or None. Match = all files in cohort.files (after glob expansion of `{new_module}` patterns) AND intent in cohort.intent_classes.
- `def apply_composition(intent: Intent, files: list[str], candidates: dict[str, set[str]], binding: BindingLayer) -> tuple[str | None, list[IssueRecord]]` — returns (resolved_profile_id_or_None, issues). Tries C1 (additive companion) → C2 (subsumption) → C3 (explicit union profile) → C4 (cohort, via cohort_admit). Returns `composition_conflict` SOFT_BLOCK when nothing resolves.
- `def explain_rejected(candidates: dict[str, set[str]], binding: BindingLayer, hint: str) -> str | None` — returns `closest_rejected_profile` for diagnostic only. Hint used ONLY here, ONLY for ranking; never gates routing.
Imports: `.dataclasses` types, `.coverage_map`.

### 1.8 `scripts/topology_v_next/companion_loop_break.py` (~200 LOC cap)
Compatibility shim per Universal §9. Delegates to composition_rules.cohort_admit().
Public:
- `def companion_loop_break(intent: Intent, files: list[str], binding: BindingLayer) -> tuple[bool, str | None, IssueRecord | None]` — returns (mode_a_admit_bool, mode_b_missing_companion_path_or_None, issue_record_or_None). Mode A: companion declared and present in files → auto-admit. Mode B: companion declared and absent → SOFT_BLOCK (in P1 logged only, not enforced).
- Internally just enumerates 2-file cohorts in binding.cohorts and calls cohort_admit().
Imports: `.dataclasses`, `.composition_rules`.

### 1.9 `scripts/topology_v_next/admission_engine.py` (~600 LOC cap)
The orchestrator implementing Universal §4 Profile Matching Algorithm steps 1–8.
Public:
- `def admit(intent: str | Intent | None, files: list[str], hint: str = "", *, binding: BindingLayer | None = None, friction_state: dict[str, Any] | None = None) -> AdmissionDecision` — sole public entry. If `binding` is None, calls `profile_loader.load_binding_layer(default_path)`. Returns full AdmissionDecision struct per Universal §2.3 / §11.
- Internal helpers: `_run_kernel`, `_resolve_intent`, `_resolve_candidates`, `_apply_composition`, `_apply_companion_loop`, `_apply_severity_overrides`, `_assemble_diagnosis`, `_increment_friction_budget`.

Friction-budget handling (anti-sidecar justification): `friction_state` is an OPTIONAL dict supplied by the CLI shim. The engine reads/increments `attempts_this_session` only if state is supplied. When omitted (Codex one-shot calls), friction_budget_used defaults to 1 and no SLICING_PRESSURE detection runs. State is held by the CALLER, not by a v_next service. (See §7 self-check.)

CRITICAL anti-sidecar property: `task` / `task_phrase` is NOT a parameter. Only `intent`, `files`, `hint`. The hint flows only into `composition_rules.explain_rejected()` and `closest_rejected_profile`; it cannot influence the matched profile.

Imports: all sibling modules, `.dataclasses` types, `time`.

### 1.10 `scripts/topology_v_next/divergence_logger.py` (~200 LOC cap)
JSONL writer for per-call shadow comparison records (see §4 schema).
Public:
- `def log_divergence(record: dict[str, Any], path: Path = DEFAULT_LOG_PATH) -> None` — append-only JSONL write. Atomic write (write to .tmp + rename). DEFAULT_LOG_PATH = `state/topology_v_next_divergence.jsonl`.
- `def classify_divergence(old_admit_result: dict[str, Any], new_admit_result: AdmissionDecision) -> str` — returns one of: `AGREE`, `DISAGREE_PROFILE`, `DISAGREE_SEVERITY_v_next_more_permissive`, `DISAGREE_SEVERITY_v_next_more_strict`, `DISAGREE_HARD_STOP`, `DISAGREE_COMPANION`. Used to stamp `agreement_bool` and the diff field of each record.
- `def detect_friction_pattern_hit(old_result: dict, new_result: AdmissionDecision) -> FrictionPattern | None` — heuristic mapping of divergence shape → friction pattern label; populates `friction_pattern_hit_if_any` field.
Imports: `json`, `pathlib`, `os`, `time`, `.dataclasses`.

### 1.11 `scripts/topology_v_next/cli_integration_shim.py` (~50 LOC cap)
Single function called from `scripts/topology_doctor.py:run_navigation()` when `--v-next-shadow` flag is set. Self-contained; no scattered hooks.
Public:
- `def shadow_compare(*, task: str, files: list[str], intent: str | None, current_admission: dict[str, Any]) -> None` — invoked AFTER current admission completes. Calls `admit(intent, files, hint=task)`, classifies divergence, calls `log_divergence`. Catches any exception and logs to `state/topology_v_next_errors.jsonl` so a v_next bug never breaks the current admission path. SOFT-BLOCK first principle: returns None unconditionally.
Imports: `.admission_engine`, `.divergence_logger`, `.profile_loader`.

---

## §2. Test Layout

Root: `tests/topology_v_next/`

### 2.1 Unit tests (one file per module)
- `tests/topology_v_next/test_dataclasses.py` — frozen-ness, to_dict roundtrip, Intent enum coverage incl. zeus.* extensions
- `tests/topology_v_next/test_profile_loader.py` — YAML loading, missing-field defaults, validate_binding_layer warnings
- `tests/topology_v_next/test_intent_resolver.py` — Intent enum match, unknown intent → ADVISORY, None → other+ADVISORY, **assert task/phrase is NOT a parameter** (introspect signature)
- `tests/topology_v_next/test_hard_safety_kernel.py` — every binding hard_stop_paths pattern flagged for at least one canonical file; non-matching paths return empty
- `tests/topology_v_next/test_coverage_map.py` — multi-profile candidates, orphan detection, gap reporting, union_candidate_profiles set algebra
- `tests/topology_v_next/test_composition_rules.py` — C1 additive companion, C2 subsumption, C3 explicit union, C4 cohort delegation, hint-never-routes property test
- `tests/topology_v_next/test_companion_loop_break.py` — Mode A admit (companion present), Mode B issue (companion absent), assert delegation to cohort_admit
- `tests/topology_v_next/test_admission_engine.py` — full §4 algorithm trace per step; HARD_STOP short-circuit; AdmissionDecision struct field population; friction_budget_used defaulting when no state supplied
- `tests/topology_v_next/test_divergence_logger.py` — JSONL append, atomicity (write to tmp + rename), classify_divergence label coverage, detect_friction_pattern_hit heuristics
- `tests/topology_v_next/test_cli_integration_shim.py` — shim swallows v_next exceptions; shim NEVER raises into caller; shim writes correct record shape

### 2.2 Integration test
- `tests/topology_v_next/integration/test_shadow_mode_e2e.py` — end-to-end: invoke `topology_doctor.py --navigation --task "..." --files ... --intent ... --v-next-shadow`, confirm both current admission AND v_next divergence record produced; current admission output unchanged from non-shadow run; assert state/topology_v_next_divergence.jsonl gained exactly one record.

### 2.3 Friction-pattern regression tests (one per pattern from Universal §12)
Located at `tests/topology_v_next/regression/`:
- `test_friction_LEXICAL_PROFILE_MISS.py` — same files, two task phrases, ASSERT v_next produces same profile both times
- `test_friction_UNION_SCOPE_EXPANSION.py` — coherent multi-profile change (e.g., new test + test_topology.yaml cohort), ASSERT v_next admits via cohort
- `test_friction_SLICING_PRESSURE.py` — supply friction_state with N=3 attempts shrinking scope, ASSERT v_next emits SLICING_PRESSURE diagnosis (P1 logs only; P2 will gate)
- `test_friction_PHRASING_GAME_TAX.py` — same intent+files with 3 different hints, ASSERT identical profile_matched + friction_budget_used unchanged across calls
- `test_friction_INTENT_ENUM_TOO_NARROW.py` — supply unknown intent string, ASSERT ADVISORY `intent_enum_unknown` raised + decision proceeds with `Intent.other`
- `test_friction_CLOSED_PACKET_STILL_LOAD_BEARING.py` — touch a file whose binding artifact_authority_status row is `CURRENT_LOAD_BEARING` with stale `last_confirmed`, ASSERT ADVISORY `authority_status_stale` raised
- `test_friction_ADVISORY_OUTPUT_INVISIBILITY.py` — admit with non-empty issues, ASSERT AdmissionDecision.issues populated even when ok=True; ASSERT to_dict() output contains all issues at top level (not buried)

---

## §3. Wire-Up Plan

Single integration point: `scripts/topology_doctor.py` `run_navigation()` at line 2636. Shadow comparison invoked AFTER current admission produces its result, BEFORE return.

Unified diff (≤30 lines as required):

```diff
--- a/scripts/topology_doctor.py
+++ b/scripts/topology_doctor.py
@@ -7,6 +7,7 @@
 from __future__ import annotations
 
 import argparse
 import json
 import os
 import re
 import sys
+from typing import TYPE_CHECKING
@@ -2636,6 +2637,7 @@ def run_navigation(
     task: str,
     files: list[str] | None = None,
     *,
     strict_health: bool = False,
     issue_schema_version: str = "1",
     intent: str | None = None,
+    v_next_shadow: bool = False,
     task_class: str | None = None,
@@ -2730,6 +2732,15 @@ def run_navigation(
     payload = _assemble_navigation_payload(...)  # existing line; not changed
+    if v_next_shadow:
+        try:
+            from scripts.topology_v_next.cli_integration_shim import shadow_compare
+            shadow_compare(
+                task=task, files=files or [], intent=intent,
+                current_admission=payload.get("admission") or {},
+            )
+        except Exception:
+            pass  # SOFT-BLOCK first principle: shadow MUST NOT break current path
     return payload
```

Plus `scripts/topology_doctor_cli.py` add-arg (counted within the 30-line budget):
```diff
--- a/scripts/topology_doctor_cli.py
+++ b/scripts/topology_doctor_cli.py
@@ -94,6 +94,7 @@
     parser.add_argument("--intent", default=None, help="Typed digest profile id; ...")
+    parser.add_argument("--v-next-shadow", action="store_true", help="Enable topology_v_next shadow comparison; logs divergence to state/topology_v_next_divergence.jsonl. NEVER blocks; logging only.")
@@ -385,6 +386,8 @@
             if value is not None:
                 key = ...  # existing
                 navigation_kwargs[key] = value
+        if getattr(args, "v_next_shadow", False):
+            navigation_kwargs["v_next_shadow"] = True
```

Total: ≤ 25 LOC of diff across 2 files. The integration is a pure additive call site; no existing code is modified except adding a kwarg pass-through.

Choice of `run_navigation` over `build_digest`: confirmed correct per advisor cross-check. `run_navigation` is the CLI-facing entry, which is what `--v-next-shadow` gates. Shadow at `build_digest` would log every internal admission regardless of CLI invocation — out of scope for P1.

---

## §4. Divergence Log Schema

Storage: `state/topology_v_next_divergence.jsonl` — append-only JSONL. Atomic appends via write-to-tmp + os.rename. One record per admission call where `--v-next-shadow` is set.

Per-call record (canonical field order):
```json
{
  "ts": "2026-05-15T13:42:01.234Z",
  "session_id": "<from CLI env or short uuid>",
  "task_phrase_hash": "<sha256(task)[:12] — phrase hash, NOT raw phrase>",
  "intent_supplied": "create_new",
  "intent_resolved": "create_new",
  "files_in_call": ["scripts/topology_v_next/foo.py", "tests/topology_v_next/test_foo.py"],
  "profile_resolved_v_next": "test_suite",
  "profile_resolved_current": "test_suite",
  "old_admit_result": {
    "ok": true, "status": "advisory_only", "profile": "test_suite", "issues_count": 0
  },
  "new_admit_result": {
    "ok": true, "severity": "ADMIT", "profile": "test_suite",
    "issues_count": 0, "companion_files": [], "missing_phrases": [],
    "closest_rejected_profile": null, "friction_budget_used": 1
  },
  "agreement_bool": true,
  "diff_class": "AGREE",
  "friction_pattern_hit_if_any": null,
  "kernel_alerts_count": 0,
  "v_next_error": null
}
```

Fields rationale:
- `task_phrase_hash` (NOT raw phrase): avoids leaking task content into a long-lived log; preserves "same phrase twice" detection without retention risk.
- `diff_class` enum: `AGREE | DISAGREE_PROFILE | DISAGREE_SEVERITY_v_next_more_permissive | DISAGREE_SEVERITY_v_next_more_strict | DISAGREE_HARD_STOP | DISAGREE_COMPANION`.
- `v_next_error`: populated when shim caught an exception. Null on clean run. Lets P2 audit how many shadow calls failed silently.

Retention policy:
- 7-day rolling window during P1 shadow phase. After day 7, the log is rotated to `state/topology_v_next_divergence.jsonl.<YYYYMMDD>` and a fresh JSONL begins. Rotated files retained ≥ 90 days for P2/P3 evidence.
- Max single-file size cap: 50 MB. If exceeded, force rotation regardless of date (defensive against cycle storms).
- Log path is git-ignored via `.gitignore` add of `state/topology_v_next_divergence.jsonl*`. NO commit of raw shadow data.

Error log: `state/topology_v_next_errors.jsonl`. Same retention. Records v_next exceptions with stack hash (not full trace) so P2 can quantify shim reliability.

---

## §5. Acceptance Probe Sequence (7-day shadow window)

Each probe = (trigger, expected divergence pattern, kill-criterion). Kill-criterion: if observed, immediately revert v_next via the documented one-revert-commit rollback (delete scripts/topology_v_next/, the diff in topology_doctor.py, and the JSONL log).

### Day 1 — LEXICAL_PROFILE_MISS probe
Trigger: invoke `--navigation --task "fix lexical mismatch in topology" --files architecture/admission_severity.yaml --intent modify_existing --v-next-shadow`, then again with `--task "update severity yaml entry"`. Same intent, same files, different phrase.
Expected divergence: current may resolve to two different profiles (or one hits advisory_only). v_next must produce identical `profile_resolved_v_next` for both calls.
Kill-criterion: v_next produces different profiles for the two calls → intent_resolver is leaking phrase signal → revert and re-spec.

### Day 2 — UNION_SCOPE_EXPANSION probe
Trigger: change set spanning `tests/test_new_thing.py` + `architecture/test_topology.yaml` with `--intent create_new --v-next-shadow`.
Expected divergence: current emits scope_expansion advisory or routes to advisory_only. v_next admits via cohort `zeus.new_test_with_topology_registration`. Diff class = `DISAGREE_SEVERITY_v_next_more_permissive`.
Kill-criterion: v_next blocks (severity ≥ SOFT_BLOCK) on this declared cohort → cohort wiring broken → revert.

### Day 3 — INTENT_ENUM_TOO_NARROW probe
Trigger: invoke with `--intent zeus.calibration_update --files src/calibration/foo.py --v-next-shadow`.
Expected divergence: current may not recognize `zeus.calibration_update`. v_next recognizes via binding intent_extensions, routes to `calibration` profile.
Kill-criterion: v_next emits `intent_enum_unknown` for a binding-declared zeus.* extension → profile_loader bug → revert.

### Day 4 — PHRASING_GAME_TAX probe
Trigger: same intent (`modify_existing`) + same files, three different task phrases over a 10-min window.
Expected divergence: v_next produces three identical AdmissionDecision objects (modulo ts/session_id). `friction_budget_used` increments only if friction_state passed; for one-shot calls, stays 1.
Kill-criterion: v_next produces ≥ 2 distinct `profile_resolved_v_next` across the three calls → revert.

### Day 5 — CLOSED_PACKET_STILL_LOAD_BEARING probe
Trigger: invoke with `--files docs/operations/task_2026-05-06_topology_redesign/SOMETHING.md --intent modify_existing --v-next-shadow`. The binding marks this dir as `CURRENT_HISTORICAL`.
Expected divergence: current likely silent. v_next emits ADVISORY `authority_status_stale` if `last_confirmed` ≥ TTL old, or `closed_packet_authority` ADVISORY otherwise.
Kill-criterion: v_next emits HARD_STOP on a `CURRENT_HISTORICAL` doc → severity table miswired → revert.

### Day 6 — ADVISORY_OUTPUT_INVISIBILITY probe
Trigger: any admission that produces issues but `ok=True`. Inspect divergence log record.
Expected: `new_admit_result.issues_count > 0` while `new_admit_result.ok == true` is correctly captured. Output normalization (Universal §11) verified by record shape — every issue surfaced.
Kill-criterion: `issues_count` field missing or always 0 in records where issues exist → divergence logger or to_dict() bug → revert.

### Day 7 — HARD_STOP integrity probe
Trigger: invoke with `--files src/execution/executor.py --intent hotfix --v-next-shadow`.
Expected: BOTH current AND v_next stop. Current via existing admission gate; v_next via Hard Safety Kernel returning HARD_STOP severity. Diff class = `AGREE`.
Kill-criterion: v_next severity < HARD_STOP for any binding `hard_stop_paths` pattern → kernel match function broken → revert + immediate red-flag (this is the Universal §15 G1 guardrail).

ROLLOVER: after Day 7, aggregate divergence counts; if AGREE rate ≥ 95% AND zero `DISAGREE_HARD_STOP` AND zero kill-criterion hits, P1 exit criteria met → packet ready for P2 phase 2 cutover work.

---

## §6. API Contract — Codex-Invocable

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
- No reliance on `sys.modules[__name__]` indirection (the existing `topology_doctor.main()` pattern is NOT mirrored).
- No reliance on Claude-Code-injected env vars.
- No reliance on argparse parsing — Python-callable directly.
- Function does not perform git or filesystem writes (other than the optional divergence log when wrapped by `cli_integration_shim`). Pure read of binding YAML.

Codex compatibility: CONFIRMED. The `admit()` function is the universal entry; all Codex/agent invocations route through it.

---

## §7. Self-Check

### 7.1 Sidecar Risk per New Field
For each AdmissionDecision field (Universal §2.3), justify why it is NOT itself a sidecar:

- `ok: bool` — derived from `severity ∈ {ADMIT, ADVISORY}`. Pure function of severity. No sidecar.
- `profile_matched: ProfileId | null` — output of §4 algorithm. The TARGET output. Never a sidecar — it IS the routing decision.
- `intent_class: Intent` — caller-supplied (validated). NOT computed from phrase. No sidecar; this is the new keying input the whole spec hinges on.
- `severity: Severity` — derived from issues + binding severity_overrides via deterministic lookup table. Pure function. No sidecar.
- `issues: list[IssueRecord]` — concrete diagnostic records produced by the algorithm steps. The §11 output normalization fix. Not a sidecar; surfacing them is the FIX for ADVISORY_OUTPUT_INVISIBILITY.
- `companion_files: list[FilePath]` — output of cohort/composition rules. Diagnostic only. No sidecar.
- `missing_phrases: list[str]` — generated by `composition_rules.explain_rejected()` purely for human-readable diagnosis. Critically: phrases are OUTPUT, never input. Cannot become a sidecar because they have no causal feedback into the next admission.
- `closest_rejected_profile: ProfileId | null` — diagnostic only. Hint-driven; never routes. No sidecar.
- `friction_budget_used: int` — SUSPICIOUS field per advisor. Justification: in P1, the engine reads this from CALLER-supplied `friction_state` dict. There is NO v_next service maintaining session state. When friction_state is None (Codex one-shots), value is 1 unconditionally. The caller (cli_integration_shim) holds the counter for shadow-CLI invocations only. Conclusion: in P1 this is just an echo of caller state; not a sidecar service. P2 may add a session-scoped counter file if needed; SCAFFOLD does NOT introduce one in P1.
- `diagnosis: DiagnosisEntry | null` — output of §12 Failure-as-Diagnosis. Diagnostic only. No sidecar.
- `kernel_alerts: list[IssueRecord]` — output of Hard Safety Kernel. Algorithmic output. No sidecar.

CONCLUSION: no sidecar fields. The hardest case (friction_budget_used) is justified as caller-state-echo with explicit P1 boundary.

### 7.2 Friction Patterns from Universal §12 — closure status

| Pattern | How v_next closes it (P1 scope) |
|---------|---------------------------------|
| LEXICAL_PROFILE_MISS | Intent is the routing key; phrase never enters profile selection. Same files + same intent → same profile, regardless of phrase. STRUCTURAL closure. |
| UNION_SCOPE_EXPANSION | Cohort declarations (Universal §8 / Zeus binding §6) + Composition Rules C1–C4 admit coherent multi-profile change sets. STRUCTURAL closure. |
| SLICING_PRESSURE | DETECTED only in P1 (logged in shadow as DiagnosisEntry when 3 attempts shrink scope inside 30 min). Gate added in P2 — explicitly deferred. PARTIAL: detection structural; enforcement P2. |
| PHRASING_GAME_TAX | Same intent + files always returns same AdmissionDecision regardless of phrase. friction_budget_used stays at 1 for repeated identical calls. STRUCTURAL closure. |
| INTENT_ENUM_TOO_NARROW | Binding-layer `intent_extensions` registers project-specific intents (e.g. zeus.calibration_update). Unknown intent → `Intent.other` + ADVISORY (not crash). STRUCTURAL closure. |
| CLOSED_PACKET_STILL_LOAD_BEARING | `artifact_authority_status` registry checked at admission; ADVISORY emitted when status=`CURRENT_LOAD_BEARING` is touched, or when `last_confirmed` exceeds `confirmation_ttl_days`. STRUCTURAL closure. |
| ADVISORY_OUTPUT_INVISIBILITY | AdmissionDecision struct surfaces `issues` at top level; `to_dict()` always includes them. `ok=True` with non-empty issues is now a typed condition that callers must inspect. STRUCTURAL closure (output schema enforces visibility). |

ALL 7 patterns enumerated. 6 closed structurally in P1; 1 (SLICING_PRESSURE) detected in P1, enforcement deferred to P2 with explicit naming.

### 7.3 Anti-Meta-Pattern Proof — Why This Is Not Sidecar #8

HIDDEN_BRANCH_LESSONS Cross-Iteration Meta-Pattern: every prior iteration "added a LAYER before or after `_resolve_profile`, never changed `_resolve_profile` itself or the `(task, files)` kernel."

This SCAFFOLD's structural change:
1. `admit()` does NOT call `_resolve_profile`. It does NOT call `topology_doctor_digest.build_digest()`. It is a NEW admission unit `(intent, files, hint)` running alongside, not on top of, the existing `(task, files)` unit.
2. The phrase (`hint`) is a strict OUTPUT contributor (closest_rejected_profile diagnostic), never an input to `coverage_map.resolve_candidates()` or `composition_rules.apply_composition()`. Verified by signature: those functions do not accept a phrase parameter.
3. `intent_resolver.resolve_intent()` validates a CALLER-SUPPLIED enum value. There is no `derive_intent_from_phrase()` function anywhere in v_next. (Anti-pattern catch: if this function ever appears, LEXICAL_PROFILE_MISS is recreated one layer down.)
4. P1 rollback is one revert commit (delete `scripts/topology_v_next/` + the diff). Current admission path is unchanged. This satisfies the "no big-bang cutover, each phase independently reversible" property from MIGRATION_PATH.
5. The `cli_integration_shim` is the ONE wire-up point; ≤ 25 LOC of diff. If the shim grows beyond that or sprouts additional call sites, the design has reverted to sidecar shape and must be reviewed.

CONCLUSION: structural change, not sidecar. The keying function is replaced (in the parallel route), not wrapped.

### 7.4 Anti-pattern catch list (review checklist for the PR)
Reviewers MUST grep the v_next implementation PR for these anti-patterns:
- `def derive_intent_from_phrase` / `def infer_intent` / `def guess_intent` → FAIL
- Any phrase-substring check inside `coverage_map.py` or `composition_rules.py` → FAIL
- Any call to `topology_doctor_digest.build_digest` inside v_next → FAIL (would re-couple to old kernel)
- Any non-shim call site in `topology_doctor.py` outside the single `run_navigation` insertion → FAIL
- `task_phrase` or `task` parameter on any v_next public function except the shim's pass-through → FAIL

---

## §8. Open Items for P2 (NOT in P1 scope)

For traceability — flagged so P2 packet authors know where to pick up:
1. SLICING_PRESSURE enforcement gate (currently detection-only in P1).
2. Cutover order from MIGRATION_PATH §Phase 3 (per-profile cutover with config flag).
3. Reconcile MIGRATION_PATH §Phase 1 vs GOAL (see §0 INCONSISTENCY-1).
4. Friction-budget session counter persistence (currently caller-supplied only).
5. Removal of legacy `admit()` from topology_doctor_digest.py (Phase 4).

---

## §9. Module-to-Friction-Pattern Traceability Matrix

| Module | Friction patterns it closes |
|--------|------------------------------|
| dataclasses.py | ADVISORY_OUTPUT_INVISIBILITY (struct shape forces visibility) |
| profile_loader.py | INTENT_ENUM_TOO_NARROW (binding intent_extensions) |
| intent_resolver.py | LEXICAL_PROFILE_MISS (intent is routing key) |
| hard_safety_kernel.py | (Universal §15 G1 — kernel must run regardless) |
| coverage_map.py | (foundation; no single pattern) |
| composition_rules.py | UNION_SCOPE_EXPANSION (C1–C4 + cohort) |
| companion_loop_break.py | UNION_SCOPE_EXPANSION (specialized cohort case) |
| admission_engine.py | PHRASING_GAME_TAX (deterministic on intent+files), CLOSED_PACKET_STILL_LOAD_BEARING (authority check) |
| divergence_logger.py | SLICING_PRESSURE (detection via shadow log) |
| cli_integration_shim.py | (wire-up only) |

Every Universal §12 friction pattern maps to at least one module. No pattern is orphaned.
