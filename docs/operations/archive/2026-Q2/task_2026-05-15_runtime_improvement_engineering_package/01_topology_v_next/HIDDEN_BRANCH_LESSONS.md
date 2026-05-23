# Hidden Branch Lessons

Created: 2026-05-15
Status: Evidence synthesis — read-only analysis of seven closed design packets

This document mines seven past topology/hook redesign packets for their
shipped wins, what did not ship, what failure pattern persisted after each,
and what v_next must absorb so no real-damage guard is reverted.

---

## Iteration 1 — topology_noise_repair (2026-05-05)

### what_problem
The task-boot-profiles guard failed at runtime because the required_reads
field inside `agent_runtime` pointed to an archived packet path that no
longer existed on disk. Separately, the script routing profile did not admit
real script files — glob patterns were absent, so topology returned advisory
for the files it was supposed to govern.

### what_shipped
- Required-reads corrected to reference the current authority surface
  (living docs, not archived packet paths)
- Glob patterns added to the script routing profile so real script files
  are admitted into their intended profile rather than falling through to
  generic or advisory_only
- All tests passed after the fix; 28 pre-existing global issues remained
  but none were in the repaired surfaces

### what_reverted_or_abandoned
Nothing was reverted. This was a targeted repair with a narrow diff.

### what_persisted
The architectural mismatch that caused the breakage — profile required_reads
are maintained by hand with no expiry or freshness check — was not addressed.
Any future reference drift would produce the same failure silently.

### failure_pattern_this_did_not_fix
LEXICAL_PROFILE_MISS (profiles matched by substring overlap; a different
task phrasing on the same file set reaches a different profile or falls
to advisory).

### what_v_next_must_absorb
Required-reads fields in any profile must reference living authority surfaces,
not packet-relative paths. Paths cited in profile metadata should be validated
at build time. The glob-pattern addition for script admission was a real win
and must not regress.

---

## Iteration 2 — topology_redesign (2026-05-06)

### what_problem
Topology was file-permission oriented rather than object-meaning oriented.
The same file (high fanout) could be schema, runtime state, producer, or
provenance authority depending on the task — a single profile assignment
could not capture this. The system had no model for the semantic meaning of
a change across surfaces; it only modeled which files were touched.

### what_shipped
This packet was a DESIGN PROPOSAL, not shipped code. It introduced:
- Hard safety kernel concept (classes of changes that are absolute hard
  stops regardless of profile or intent match)
- Advisory hazard model with 12 categorized facets
- Tri-state ambiguity (ADMIT / SOFT_BLOCK / HARD_BLOCK) replacing the
  binary admit/reject
- Proof obligations for high-risk surfaces
- Retired-object housekeeping route concept
- Object-boundary cohort admission concept (admit a coherent cluster of
  objects rather than a file list)
- Semantic boot vocabulary proposal
- Migration phases A through E sketched

None of the core admission algorithm changes were implemented. Open
decisions in §13 remained unresolved at packet close.

### what_reverted_or_abandoned
The complete implementation was deferred. The proposal's depth (12-facet
hazard model, cohort admission, proof obligations) was not converted to code.
The open-decision list was carried forward unsettled.

### what_persisted
The hard safety kernel concept — certain change classes are hard stops
regardless of routing — was carried forward as design intent and later
appeared as a named invariant. The tri-state admission model influenced
subsequent designs even without direct implementation.

### failure_pattern_this_did_not_fix
UNION_SCOPE_EXPANSION (a coherent change touching files from multiple profiles
triggers scope_expansion and is refused, even when the change is semantically
unified). The cohort-admission proposal would have addressed this but was not
implemented.

### what_v_next_must_absorb
The hard safety kernel — some change classes must remain hard stops regardless
of profile match — is not optional and must be preserved in any v_next.
The 12-facet hazard categorization is a valid decomposition of risk surface
even if the implementation changes. Cohort admission (admitting a semantically
unified cluster rather than a file list) is the right fix for union-scope
expansion and v_next must implement it.

---

## Iteration 3 — hook_redesign (2026-05-06)

### what_problem
188 `[skip-invariant]` commits in 60 days (~3.1/day), indicating agents
were bypassing hooks rather than resolving the underlying admission friction.
One worktree loss incident ($190 realized cost) was traced to hook bypass.
PR auto-review timing introduced cost spikes. Hooks were using a legacy
exit-2 contract without structured telemetry.

### what_shipped
- `registry.yaml` (~250 LOC): structured hook registry replacing ad-hoc
  shell scripts
- `dispatch.py` (~300 LOC): single dispatch entry point replacing 7 separate
  shell scripts
- Structured override catalog with 9 override IDs and auto-expiry
- `overrides.yaml` (~150 LOC): explicit override manifest
- ADVISORY fail-open / BLOCKING fail-closed two-tier model

### what_reverted_or_abandoned
The BLOCKING tier and `overrides.yaml` were later abandoned (see
hook_redesign_v2). The structured-override infrastructure, which was
the main novelty of this packet, was eventually retracted.

### what_persisted
The single-entry-point dispatch model (dispatch.py) persisted. Registry.yaml
as the canonical hook definition source persisted. Structured override
categorization as a concept (even without the file) informed later work.

### failure_pattern_this_did_not_fix
PHRASING_GAME_TAX (each admission attempt costs tokens and invocations;
agents spending 3+ attempts before success is common). The override system
addressed bypass symptoms but did not reduce the number of attempts needed
per successful admission.

### what_v_next_must_absorb
Single-entry dispatch (registry + dispatcher) is the right architecture.
Structured telemetry on hook outcomes (which hook fired, which path, why)
is a real win that enables failure-as-diagnosis. The two-tier
ADVISORY/BLOCKING distinction was the right conceptual split even though
the BLOCKING tier was later simplified — v_next should preserve the
concept of "this class of change is always a hard stop" without
rebuilding the overrides infrastructure.

---

## Iteration 4 — navigation_topology_v2 (2026-05-07)

### what_problem
Five verified friction patterns (F1-F5) with file:line citations, the
worst being:
- Profile lookup was substring/keyword sensitive — same file set with
  different task phrasing reached different profiles (LEXICAL_PROFILE_MISS)
- Companion files required by manifests were auto-included by topology
  but their inclusion triggered scope_expansion refusal (companion-loop-break
  absent)
- Typed intent was required internally but no canonical enum was published
  to agents, producing typed_intent_invalid errors on legitimate intents

### what_shipped
- `architecture/admission_severity.yaml`: YAML source of truth mapping
  issue_code to severity (default_severity: advisory). This externalized
  severity decisions from code.
- Typed-intent enum (canonical values: plan_only, create_new,
  modify_existing, refactor, audit, hygiene, hotfix, rebase_keepup, other)
  integrated throughout topology_doctor_digest.py
- `_apply_companion_loop_break`: auto-admits manifest companion when
  typed_intent is create_new and companion path is already in --files
- `scripts/worktree_doctor.py`: 4 subcommands for worktree lifecycle
- +1 SessionStart hook in registry.yaml
- 5 new capability entries in existing profiles

### what_reverted_or_abandoned
Nothing was reverted. All three structural decisions (K1, K2, K3) shipped
and remain in current codebase.

### what_persisted
admission_severity.yaml (12488 bytes, verified May 14 2026), worktree_doctor.py
(20814 bytes, May 7), companion_loop_break and typed_intent_enum throughout
topology_doctor_digest.py — all verified present.

### failure_pattern_this_did_not_fix
SLICING_PRESSURE (repeated admission failure trains agents to ship
smaller-than-correct units, creating a ratchet effect that accumulates
debt). Severity externalization reduced blocking severity for some cases
but did not change the incentive structure that causes agents to slice.

### what_v_next_must_absorb
The admission_severity.yaml externalization is the right pattern: severity
decisions belong in a declarative config, not buried in code. The
companion-loop-break is a concrete win that v_next must preserve or
generalize. The typed-intent enum created a shared vocabulary between
agents and the admission system — v_next must preserve this vocabulary
and its extension mechanism.

---

## Iteration 5 — hook_redesign_v2 (2026-05-07)

### what_problem
Discovery that the entire STRUCTURED_OVERRIDE / `evidence/operator_signed/`
infrastructure built in hook_redesign (iteration 3) was duplicating
Claude Code's built-in permission system. The sidecar had overbuilt
the authorization model.

### what_shipped
- ALL BLOCKING hooks dropped; all hooks made ADVISORY
- overrides.yaml deleted
- BLOCKING tier removed from the model
- dispatch.py main() simplified to pure advisory path
- Boot self-test added: handler existence check at dispatch.py load time

### what_reverted_or_abandoned
The BLOCKING tier, the structured-override catalog, and overrides.yaml
were all retracted. The 9 override IDs with auto-expiry were deleted.
This was the one deliberately subtractive iteration.

### what_persisted
Single-entry dispatch, registry.yaml, and advisory-mode hooks all persisted.
The boot self-test (K1 of this packet) persisted. The insight — that sidecar
accumulation can duplicate the host platform's native authorization model —
was the conceptual win.

### failure_pattern_this_did_not_fix
ADVISORY_OUTPUT_INVISIBILITY (ok=True while the JSON body holds advisory_only
warnings the agent never surfaces). Making all hooks advisory removed blocking
friction but did not ensure advisory signals were actually consumed.

### what_v_next_must_absorb
When a sidecar has grown to duplicate the host platform's native mechanism,
retraction is correct. This principle — periodic audit of accumulated sidecars
against the host platform's evolving capabilities — is a governance pattern
v_next should encode as a standing practice, not a one-time correction.

---

## Iteration 6 — topology_redesign_completion (2026-05-08)

### what_problem
A prior "Phase 5.B" deleted 3 helper modules but left ~30 dead stubs in
topology_doctor.py (lines 1367-1591 at that time). Two CLI subcommands
(`semantic-bootstrap`, `context-pack`) crashed with ModuleNotFoundError.
Eight surfaces in architecture YAMLs and docs/reference cited the now-deleted
topology_schema.yaml. The deletion was incomplete.

### what_shipped
- Dead stubs removed from topology_doctor.py
- CLI subcommands fixed (or removed if orphaned)
- All 8 references to topology_schema.yaml updated to reflect deletion
- Discoverability updated: AGENTS.md, architecture YAML, docs/reference
  all consistent with the post-deletion state

### what_reverted_or_abandoned
Nothing added; this was completion-only. No new features, no restoration
of deleted modules.

### what_persisted
The topology_schema.yaml deletion remained permanent. Schema data was inlined
rather than held in a separate file. Verified: no topology_schema.yaml in
current codebase.

### failure_pattern_this_did_not_fix
CLOSED_PACKET_STILL_LOAD_BEARING (closed packets still own truth that the
current runtime depends on; topology has no signal for this distinction).
The deletion-completion work assumed deleted artifacts were safe to remove,
but had no mechanism to check whether downstream consumers still referenced
them at runtime.

### what_v_next_must_absorb
Deletion must be paired with a forward-reference audit: find every path that
cites the deleted artifact before deletion is marked complete. This is a
concrete, automatable check. V_next should encode "deletion is not complete
until all forward references are cleared" as a first-class operation rather
than a catch-up repair.

---

## Iteration 7 — post_s4_residuals_topology (2026-05-09)

### what_problem
Post-PR-#104 state: the S1-S4 observability/provenance repair line exposed
recurring route friction. Natural packet wording often reached generic or
advisory_only. High-fanout files made routing brittle. New test companion
requirements were easy to miss. Planning docs paths were initially
unclassified. Topology feedback was partly ephemeral (not persisted to
packet evidence).

### what_shipped
This was a planning-only packet. Seven structural improvements were proposed:
1. Packet preflight checklist generator
2. Topology explain for advisory-only decisions
3. High-fanout route hints from metadata (not a hand-maintained catalog)
4. New-test companion guard in packet templates
5. Mandatory topology capsule in packet progress
6. Post-merge cleanup recipe
7. Split of data-authority packets from code-observability packets

None of the seven were implemented in this packet.

### what_reverted_or_abandoned
N/A (planning only).

### what_persisted
The preflight checklist proposal and the topology explain proposal are
still open design work. The friction taxonomy from this packet (natural
wording, high-fanout brittleness, ephemeral feedback, missing companion
requirements) informed the autonomous agent runtime audit that preceded
the current engineering package.

### failure_pattern_this_did_not_fix
INTENT_ENUM_TOO_NARROW: the typed-intent enum added in iteration 4 still
does not cover all legitimate agent intents, producing typed_intent_invalid
errors on valid task formulations not anticipated at enum design time.

### what_v_next_must_absorb
The friction taxonomy here is a direct input to the friction-budget SLO
concept (admission-attempts-to-success p50/p95). The explain mechanism
for advisory-only decisions — telling the agent "closest admitted profile
is X, rejected because of Y, missing strong phrase Z" — is the right
anti-LEXICAL_PROFILE_MISS measure and must ship in v_next. The preflight
checklist concept (which profile, which phrases, which companions) is
the right onboarding surface for any new packet type.

---

## Cross-Iteration Meta-Pattern

### The Invariant That Never Changed

Across all seven iterations, the admission decision unit was never changed:
`(lexical-task-phrase, file-path-list)`. Profile selection keys on
substring/keyword overlap between the task phrase and profile definitions,
then checks the file list against the selected profile's scope rules.
Every sidecar added since iteration 1 — severity registry, typed-intent enum,
companion-loop-break, hook telemetry, worktree doctor, deletion-reference audit
— sits on top of this same keying function.

**Code-grounded evidence (iterations 4 and 6):**

`scripts/topology_doctor_digest.py` (78kB, current codebase):

- `build_digest(api, task, files, *, intent, ...)` (line 1703) — the outer
  keying signature. `task` (lexical phrase) and `files` remain the two
  positional inputs. `intent` is an added keyword argument, not a positional
  replacement.
- `_collect_evidence(topology, task, requested)` → `_resolve_profile(evidence,
  topology)` (lines 1726, 627 / 1749) — the core evidence-collection and
  profile-resolution path. Unchanged by iterations 4 or 6.
- Iteration 4's additions — `_resolve_typed_intent` (line 838) and
  `_apply_companion_loop_break` (line 1440) — are called in `build_digest` at
  lines 1743 and 1766, BEFORE `_resolve_profile` is invoked. They short-circuit
  some cases; they do not replace the keying function.
- Iteration 6 removed dead stubs (lines ~1367-1591 in its pre-deletion state);
  no keying change: `_resolve_profile` and `_collect_evidence` signatures are
  unmodified.

The pattern: each iteration added a LAYER before or after `_resolve_profile`,
never changed `_resolve_profile` itself or the `(task, files)` kernel. This is
the "additive sidecar" structure the meta-finding names.

The one subtractive iteration (hook_redesign_v2) recognized that sidecar
accumulation had duplicated the host platform's authorization model and
retracted it. But the underlying admission keying was not the target — the
retraction was of the authorization layer, not the routing layer.

### Why Incrementalism Keeps Producing the Same Friction

Semantic matching was never structurally separated from lexical routing.
The system asks: "which profile's keywords overlap with this task phrase?"
It does not ask: "what is the agent's intent, independent of phrasing, and
which profile governs that semantic intent class?"

Any improvement that does not change the keying function produces a
circumvention sidecar: add the right phrase → succeed. Change phrase →
fail. The sidecar teaches agents what phrases work; it does not make the
routing robust to legitimate phrase variation.

The companion-loop-break (K2 from iteration 4) is the closest any iteration
came to changing the keying function for one specific case (typed_intent
= create_new, companion already in --files → auto-admit). V_next must
generalize this pattern: intent-class drives routing, phrasing is a fallback
signal not the primary signal.

### What V_Next Must Change

Not add to the existing keying function. Change it. The admission unit must
shift from:
```
(lexical-task-phrase, file-path-list) → profile
```
to:
```
(typed-intent, file-path-list, profile-hint?) → admission-decision
```
where typed-intent is a first-class input resolved before profile selection,
and the lexical phrase is used only for hint generation when typed-intent
is ambiguous. This is the one structural change no prior iteration made,
and the reason the seven friction patterns remain partially active after
seven iterations.
