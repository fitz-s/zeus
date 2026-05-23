# Universal Topology Design v_next

Created: 2026-05-15
Status: SPEC ONLY — no implementation code in this document
Scope: Project-agnostic. Zero project-specific identifiers. Project bindings
live exclusively in ZEUS_BINDING_LAYER.md. Universality test: a second project
in a different language and domain can adopt this with only its own binding layer.

## 1. Problem Statement

A routing system that prevents agents from making dangerous changes creates
friction for legitimate changes when the routing decision depends on the
agent's phrasing rather than intent. Seven prior iterations added sidecars
(severity registry, typed-intent enum, companion-loop-break, hook telemetry,
deletion-reference audit) to reduce friction without changing the root
admission unit. This spec changes it.

### 1.1 Glossary

- **friction_budget** — session-scoped instrument tracking admission attempt
  efficiency (§14). Fields: `attempts_this_session`, `p50_target`, `p95_target`,
  `current_rate`.
- **friction_budget_alert** — diagnostic emitted when `attempts_this_session`
  exceeds p95 target; triggers DiagnosisEntry (§12). Not a gate.
- **CLOSED_PACKET_STILL_LOAD_BEARING** — canonical friction pattern name for a
  closed packet whose evidence is still load-bearing (§12, §13). This spelling
  is authoritative; do not use `CLOSED_ARTIFACT_STILL_LOAD_BEARING`.

---

## 2. Core Abstractions

### 2.1 Profile
A Profile is a named configuration that governs a semantic category of
changes. It specifies:
- Which intent classes it serves
- Which authority surfaces it covers
- What evidence a change must carry
- What the hard-stop conditions are (regardless of profile match)

A Profile is NOT a list of files. Files are bound to profiles via Coverage
Maps at the project binding layer.

### 2.2 Intent
An Intent is a first-class typed enum value that the agent supplies before
profile selection begins. Intent drives routing. Phrasing is a disambiguation
hint, not the routing key.

Canonical intent values:
- `plan_only` — produce a plan/spec document; no source changes
- `create_new` — introduce a new artifact (file, module, config)
- `modify_existing` — change an existing artifact's behavior or content
- `refactor` — restructure without changing external behavior
- `audit` — read-only analysis; no writes
- `hygiene` — cleanup, rename, formatting, dead-code removal
- `hotfix` — urgent narrow repair of a live defect
- `rebase_keepup` — merge/rebase maintenance, no semantic change
- `other` — explicit escape hatch; requires human-readable justification field

Extension mechanism: projects define additional intent values in their binding
layer. Extension values must be prefixed with a project namespace token to
prevent collision with future universal values.

### 2.3 Admission Decision
The admission decision is a structured object, not a single boolean:

```
AdmissionDecision:
  ok: bool
  profile_matched: ProfileId | null
  intent_class: Intent
  severity: ADMIT | ADVISORY | SOFT_BLOCK | HARD_STOP
  issues: list[IssueRecord]
  companion_files: list[FilePath]
  missing_phrases: list[str]       # what phrasing would have helped
  closest_rejected_profile: ProfileId | null  # why advisory fired
  friction_budget_used: int        # admission attempts this session
```

### 2.4 Severity Tiers
Four tiers; project binding assigns specific issue codes to tiers:

- `ADMIT` — proceed; no conditions
- `ADVISORY` — proceed; agent must surface the advisory text to the
  human before the next change (fail-open, exit 0)
- `SOFT_BLOCK` — do not proceed until agent resolves the listed conditions;
  resolution path is defined per issue code
- `HARD_STOP` — unconditional stop; no override path; human action required

The critical property: `HARD_STOP` is determined before profile selection,
by the Hard Safety Kernel (§5), not by profile logic.

---

## 3. The New Admission Unit

Previous admission unit: `(lexical-task-phrase, file-path-list) → profile`

New admission unit: `(typed-intent, file-path-list, profile-hint?) → AdmissionDecision`

Where:
- `typed-intent` is the canonical enum value; resolved before any profile
  lookup begins
- `file-path-list` is evaluated against the Coverage Map after intent
  is resolved
- `profile-hint` is an optional free-text hint (the agent's task phrase)
  used ONLY when intent alone does not disambiguate among multiple
  candidate profiles — it is never the primary routing signal

This change eliminates LEXICAL_PROFILE_MISS as a structural failure mode:
two legitimate intents using different phrasing but identical file sets
now route identically, because intent removes phrase as a routing key.

How the new keying function distributes across §4:
- **Files drive candidate selection** (§4 step 2): Coverage Map resolves
  candidate profiles from file-path-list, independent of phrasing.
- **Intent gates cohort admission** (§4 step 4a, §8): cohort `intent_classes`
  filter determines whether a matched cohort applies.
- **Intent feeds binding-layer disambiguation for high-fanout files**
  (§4 step 4b, §7): the project binding layer resolves by typed-intent
  when files appear in multiple profiles.
- **Profile-hint is diagnostic-only** (§4 step 5): populates
  `closest_rejected_profile`; never a routing gate.

---

## 4. Profile Matching Algorithm

```
match(intent, files, hint?) → AdmissionDecision:
  1. Run Hard Safety Kernel check (§5). If any file triggers HARD_STOP,
     return immediately with severity=HARD_STOP. No profile selection.

  2. Resolve candidate profiles:
     coverage_map = project binding layer's file-to-profile assignments
     candidates = {profile for file in files if file in coverage_map[profile]}
     If candidates is empty: severity=ADVISORY, closest_rejected_profile=null

  3. If len(candidates) == 1: profile_matched = candidates[0]. Done.

  4. If len(candidates) > 1 (UNION_SCOPE case):
     a. Apply Cohort Admission check (§8): if all files belong to a
        single declared cohort, admit the cohort profile.
     b. If no cohort covers the union: apply Composition Rules (§7).
     c. If composition rules produce a single admission profile: use it.
     d. If unresolvable: severity=SOFT_BLOCK, list the conflicting profiles,
        request agent decompose or declare cohort.

  5. Apply hint only in step 4d to rank candidate profiles for the
     "closest_rejected_profile" field in the output. Never for routing.

  6. Apply Companion-Loop-Break (§9).

  7. Apply Severity Lookup (§10) to all issues collected.

  8. Return AdmissionDecision with all fields populated.
```

---

## 5. Hard Safety Kernel

The Hard Safety Kernel runs before profile selection. It is unconditional.
A project binding layer defines which file patterns and change classes
trigger HARD_STOP. The universal core provides the kernel mechanism and
the following universal HARD_STOP categories (project binding assigns specific
paths):

- `LIVE_SIDE_EFFECT_PATH` — files whose modification triggers a live external
  action (API calls, financial transactions, data writes to production stores)
- `CANONICAL_TRUTH_REWRITE` — files that are the single source of truth for
  a domain fact; rewrite requires proof that the new truth is authoritative
- `SCHEMA_MIGRATION` — changes to the persistence schema of canonical stores
- `LIFECYCLE_GRAMMAR` — changes to the command/event grammar that governs
  the system's lifecycle transitions
- `CREDENTIAL_OR_AUTH_SURFACE` — credential files, auth token stores,
  permission manifests

Hard Safety Kernel checks run in O(1) per file via prefix/glob matching.
They do not require profile lookup.

---

## 6. Coverage Map

The Coverage Map is the project binding layer's declaration of which
profile governs each file or file pattern. A file MUST be in exactly one
of:
- Explicitly covered by a profile
- Explicitly orphaned (listed as ORPHAN in the coverage map)
- Explicitly forbidden (listed as HARD_STOP_PATH in the kernel)

A file that is in none of these three categories is a coverage gap.
The system MUST surface coverage gaps as ADVISORY issues, not silently
route to generic.

Coverage map format (project binding layer defines values):
```yaml
coverage_map:
  profiles:
    - id: <profile_id>
      patterns: [<glob_pattern>, ...]
  orphaned: [<glob_pattern>, ...]
  hard_stop_paths: [<glob_pattern>, ...]
```

---

## 7. Composition Rules

When a change spans files from multiple profiles, Composition Rules
determine whether the union is admissible:

Rule C1 (Additive Companion): if the additional files are declared
companions of the primary profile and the intent is create_new or
modify_existing, admit the union under the primary profile.

Rule C2 (Subsumption): if profile B's scope is a strict subset of
profile A's scope, and all files from B are also files of A, admit
under A.

Rule C3 (Explicit Union Profile): a project binding layer may declare
union profiles for known multi-surface operations. Union profiles are
first-class profiles, not special cases.

Rule C4 (Cohort): see §8. If no rule applies: SOFT_BLOCK with listing of
conflicting profiles and the files driving each conflict.

---

## 8. Cohort Admission

A Cohort is a named, declared set of files that are semantically unified
for a specific intent class. When a change's file set matches a declared
cohort (all files in the cohort, intent matches the cohort's intent class),
admit the entire cohort under the cohort's governing profile without
scope_expansion friction.

Cohort declaration (project binding layer):
```yaml
cohorts:
  - id: <cohort_id>
    profile: <profile_id>
    intent_classes: [<intent>, ...]
    files: [<file_path>, ...]
    description: <human-readable rationale>
```

Cohort admission generalizes the Companion-Loop-Break from a single
specific case (create_new + companion in --files) to a first-class pattern.

---

## 9. Companion-Loop-Break

This section is the §8 cohort pattern specialized to a profile's declared
2-file companion pair. §8 is source of truth; §9 is the compatibility shim.
ONE mechanism, two failure modes:

**Mode A (fail-open):** companion declared and present in --files, but
composition would otherwise block. Auto-admit without scope_expansion
friction. (Intent must be `create_new`; companion was included because the
profile requires it.)

**Mode B (fail-closed):** companion declared and absent from --files.
SOFT_BLOCK naming the missing companion. See
`03_authority_drift_remediation/REMEDIATION_PLAN.md § Companion-Update
Enforcement` for the `companion_required:` schema and override token.

New companion patterns should be §8 cohorts, not additional §9 rules.
This behavior must not regress in v_next.

---

## 10. Severity Lookup

Severity is declared in an external YAML config (project binding layer
extends the universal defaults). The universal defaults:

```yaml
default_severity: advisory
issue_severity_overrides:
  hard_stop_kernel_match: hard_stop
  coverage_gap: advisory
  composition_conflict: soft_block
  companion_missing: advisory
  closed_packet_authority: advisory
  evidence_tier_mismatch: advisory
```

Project binding layers may promote any advisory to soft_block, or any
soft_block to hard_stop. They may not demote hard_stop.

---

## 11. Output Normalization (ADVISORY_OUTPUT_INVISIBILITY Fix)

Every admission call returns the full AdmissionDecision struct (§2.3).
The routing layer MUST populate:
- `issues` with every detected condition, regardless of severity
- `missing_phrases` with phrases that would have clarified ambiguous routing
- `closest_rejected_profile` when severity is ADVISORY or SOFT_BLOCK
- `friction_budget_used` as a running count of admission attempts in the
  current session

Callers MUST surface all ADVISORY and above issues to the agent before
proceeding. An `ok=True` result with non-empty `issues` is not a clean
pass — it is a pass with conditions. The agent contract requires
acknowledging conditions before the next change.

This replaces the pattern where `ok=True` with advisory body was treated
as unconditional green.

---

## 12. Failure-as-Diagnosis

When admission fails (SOFT_BLOCK or HARD_STOP), the output MUST include
a diagnosis entry:

```
DiagnosisEntry:
  pattern: FrictionPattern   # one of the seven named patterns
  evidence: str              # concrete reason (profile name, file, issue code)
  resolution_path: str       # what the agent should do next
```

Named friction patterns (from operational history; universal names):
- `LEXICAL_PROFILE_MISS` — phrase mismatch despite correct intent
- `UNION_SCOPE_EXPANSION` — coherent multi-profile change refused
- `SLICING_PRESSURE` — agent forced to ship sub-correct units
- `PHRASING_GAME_TAX` — repeated attempts before success
- `INTENT_ENUM_TOO_NARROW` — valid intent not in canonical enum
- `CLOSED_PACKET_STILL_LOAD_BEARING` — artifact is closed but still authoritative (see §1.1 glossary)
- `ADVISORY_OUTPUT_INVISIBILITY` — advisory conditions not surfaced to agent

Diagnosis output turns a blocked admission into actionable information,
not a dead end.

---

## 13. Closed-Artifact Authority Distinction (CLOSED_PACKET_STILL_LOAD_BEARING fix)

In v_current, lifecycle status (open/closed) is conflated with authority
status — a closed packet whose evidence is still load-bearing is invisible
to admission. V_next separates these via a registry field:

Registry row schema:
```yaml
artifact_authority_status:
  - path: <file_or_directory_path>
    status: <one of: CURRENT_LOAD_BEARING | CURRENT_HISTORICAL |
                     STALE_REWRITE_NEEDED | DEMOTE | QUARANTINE | ARCHIVED>
    last_confirmed: <YYYY-MM-DD>      # date a human or automated check last verified
    confirmation_ttl_days: <int>      # max days before ADVISORY fires on next admission
    reason: <human-readable rationale>
```

Status values: `CURRENT_LOAD_BEARING` (authoritative for live runtime) |
`CURRENT_HISTORICAL` (closed; no live dependency) |
`STALE_REWRITE_NEEDED` (drifted) | `DEMOTE` | `QUARANTINE` | `ARCHIVED`.

Freshness enforcement: when an admission touches a file whose registry row
has `last_confirmed` older than `confirmation_ttl_days` days, the system
emits ADVISORY (issue code: `authority_status_stale`). This converts
hand-maintenance into surfaced-drift — staleness is visible at next
admission rather than discovered during an incident.

When a change touches a file whose parent packet has `CURRENT_LOAD_BEARING`
status, the admission system surfaces this as an ADVISORY with the owning
packet's ID. Deletion or overwrite of such a file is SOFT_BLOCK by default.

---

## 14. Friction Budget

The friction budget tracks admission efficiency:

```
FrictionBudget:
  attempts_this_session: int
  p50_target: 1            # median: first attempt succeeds
  p95_target: 2            # 95th percentile: at most 2 attempts
  current_rate: float      # rolling sessions
```

When `attempts_this_session` exceeds the p95 target for a single admission,
the output includes a friction_budget_alert with the detected friction
pattern. This data feeds the Failure-as-Diagnosis (§12) loop.

The friction budget is a measurement instrument, not a gate for most cases.
Exceeding p95 triggers a friction_budget_alert (DiagnosisEntry in §12).

**SLICING_PRESSURE gate (exception):** When `N` or more admission attempts
within `M` minutes share an overlapping file set and each attempt is a strict
subset of the previous, the system emits `SOFT_BLOCK` with pattern
`SLICING_PRESSURE`. Default: N=3, M=30. Project binding may tighten only.
The diagnosis entry names the overlapping files so the agent can declare a
cohort (§8) rather than continue decomposing. Slicing pressure reveals itself
as attempt-clustering with shrinking scope — reusing the friction budget avoids
a new §.

---

## 15. Non-Negotiable Guardrails

These apply universally, regardless of project binding:

G1: Hard Safety Kernel runs before profile selection. No intent value,
    no profile match, no cohort declaration can bypass it.
G2: HARD_STOP has no override path at the agent level. Human action required.
G3: All admission outputs include the full AdmissionDecision struct. No
    caller may treat ok=True with non-empty issues as unconditional green.
G4: Evidence tier is immutable by the routing layer. The layer reads evidence
    tier from the project binding; it does not compute or override it.
G5: The Companion-Loop-Break (§9) may never be disabled without equivalent
    cohort declaration replacing it.
G6: Severity may be promoted by project binding (advisory → soft_block)
    but never demoted from hard_stop.
G7: Deletion of any artifact requires a forward-reference audit clearing
    all references before deletion is marked complete.

---

## 16. What the Agent Sees — Admission Flow Example

Input:
```
intent: create_new
files: [config/new_feature.yaml, src/feature_loader.py]
```

Flow: Hard Safety Kernel passes → Coverage Map finds two profiles
(config_management, core_runtime) → Composition Rule C1 resolves: config
file is a declared companion of core_runtime loader change → Companion-Loop-Break:
config already in --files, intent is create_new, auto-admit → no issues.

Output:
```
ok: true, profile: core_runtime, severity: ADMIT, friction_budget_used: 1
```

Old system: phrase "add loader" → core_runtime; "add config" →
config_management; "update feature" → advisory_only. Agent rephrases 2-3
times. friction_budget_used: 3. Same files, same intent, triple the cost.
The new system routes on typed-intent; the phrase is never the routing key.
