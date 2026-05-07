# Navigation Topology v2 — Single PLAN

<!-- Created: 2026-05-07 -->
<!-- Last reused or audited: 2026-05-07 -->
<!-- Authority basis: ULTIMATE_DESIGN §1, ANTI_DRIFT_CHARTER §3-§7, hook_redesign PLAN §0.5; sunset 2026-11-07 -->

## §0 Sunset and meta

Sunset: **2026-11-07**. Auto-demote to `docs/operations/historical/` if Phase 1 is not started by **2026-08-07** (90-day clock per CHARTER §5 operational-rule default) or if any Phase 1 deliverable is not committed to `cleanup-debt-2026-05-07` by **2026-08-07**. ANTI-DRIFT CHARTER M1-M5 binding.

This is a single-document redesign because the navigation/admission surface is small (≤9,738 LOC across `topology_doctor.py` + 13 checker-family modules + `digest_profiles.py`, but only the admission decision-tree and 6-12 emitter sites are in scope) and the structural decisions are bounded. Mirror the hook redesign's single-PLAN style — not its ceremony.

**Scope:** Zeus repo `scripts/topology_doctor.py` admission decision tree + `scripts/topology_doctor_script_checks.py` + `scripts/topology_doctor_docs_checks.py` + `scripts/topology_doctor_digest.py` typed-intent resolver + AGENTS.md root §3-§4 (≤30 lines updated) + `architecture/capabilities.yaml` (~6-8 new entries) + `architecture/naming_conventions.yaml` (severity-tier annotation) + cron/SessionStart hygiene wiring. **Out of scope:** the 5 source-tree gates that the topology redesign owns (`docs/operations/task_2026-05-06_topology_redesign/ultraplan/# ULTIMATE_DESIGN.md` §5 gates 1-5); the hook surface that the hook redesign owns (`docs/operations/task_2026-05-06_hook_redesign/PLAN.md`). This PLAN composes with both.

This PLAN does not request critic dispatch (coordinator handles that). It does not push or open a PR.

---

## §0.5 Critic-opus amendments (2026-05-07, post 9b677c28)

Critic-opus review (`evidence/topology_v2_critic_opus.md`, agent a51e1898d045c26ac) returned **GO-WITH-CONDITIONS** with 0 critical / 2 HIGH / 2 MEDIUM / 4 LOW. proceed_to_phase_1: True. The following amendments bind:

**C3 / D1 resolved (typed_intent enum extension):**
Enum now `{plan_only, create_new, modify_existing, refactor, audit, hygiene, hotfix, rebase_keepup, other}`. `hotfix` covers urgent fixes; `rebase_keepup` covers branch-keepup-with-main; `other` is explicit fall-through (admission still applies per K1 severity tier).

**C1 / D2 resolved (Phase 2 split):**
Phase 2 split into Phase 2A (K1 severity demotion only) and Phase 2B (K2 companion-loop-break). Reason: shipping K1 + K2 in one commit means a K2 bug leaves K1's gate-weakening live → strictly worse than starting state. Split delays the demotion until companion-loop-break is verified.

**C2 / D3 resolved (Phase 3 LOC bump):**
Phase 3 LOC budget 430 → 500. Reason: `.claude/hooks/dispatch.py` (1,363 LOC) currently has zero `SessionStart` / `WorktreeCreate` / `WorktreeRemove` event handlers. The +30 LOC estimate was unrealistic; realistic is +80-120 LOC plus per-event fall-open-on-error tests (per `evidence/hook_redesign_critic_opus_final_v2.md` ATTACK 8 precedent).

**Updated phase plan (4 phases):**
- Phase 1: stable layer + severity registry (~1.5h, ~430 LOC) — UNCHANGED
- Phase 2A: K1 severity demotion only (~1h, ~150 LOC) — NEW SPLIT
- Phase 2B: K2 companion-loop-break (~1h, ~170 LOC) — NEW SPLIT
- Phase 3: worktree_doctor + dispatch.py SessionStart/WorktreeCreate/WorktreeRemove handlers (~2h, ~500 LOC) — LOC bumped per C2

**Total:** ~5.5h sonnet, ~1,250 LOC. Phase 1 + Phase 3 dispatched in parallel (independent surfaces). Phase 2A sequential after 1; 2B sequential after 2A.

**Minor fixes (M1-M6) bound for executor compliance:**
- M1: §1.3 — F1 + F4 are 2-hit; multi-mapping intentional (anti-rubber-stamp footnote in PLAN narrative)
- M2: §2.6 — extend `cross_worktree_visibility.intent` to include staleness audit (no 6th capability)
- M3: §2.7 — sentinel write happens post-`git worktree add` success; race delegated to git atomicity
- M4: companion-loop-break — `len(requested) > 50` triggers advisory cap (defends against batch-add inflation; H-R-? mitigation per critic ATTACK 6)
- M5: §6.5 — Day-90 metric "naming exceptions added per month" tracked
- M6: §1.x — task-folder count 23 → 24 (off-by-one; one folder added today)

All ODs (D1/D2/D3) resolved by architecture-homework; no operator punt.

---

## §1 Problem

### 1.1 Verified evidence (re-measured 2026-05-07)

| Friction pattern | Verified file:line emitter | Severity today | Should be |
|---|---|---|---|
| **F1.** Profile judges new files as `scope_expansion_required` (allowed_files filter blocks file CREATION because new file isn't in list) | `scripts/topology_doctor.py:2053` (`if admission_status == "scope_expansion_required": return "stop and update packet/profile scope before editing out-of-scope files"`); `scripts/topology_doctor.py:2883` map `"scope_expansion_required" → "navigation_scope_expansion_required"`; `tests/test_digest_admission_policy.py:122-131` `test_navigation_blocks_when_scope_expansion_required` | BLOCKING (asserted in test_navigation_blocks_when_scope_expansion_required) | ADVISORY for new-file creation under typed-intent; BLOCKING only when typed-intent says "modify-existing" |
| **F2.** `--intent "plan only; no code edits"` judged generic/ambiguous; typed intent missing | `scripts/topology_doctor.py:2409` `f"profile_needs_typed_intent:{selected_by}"`; `scripts/topology_doctor_digest.py:1255-1261` `needs_typed_intent` set when `selected_by in {shared_file_only, companion_file_only, weak_term_nonselectable, high_fanout_file_only, typed_intent_invalid}` | falls through to generic profile (allowed_files=[], stop_conditions blocks edits) | typed-intent enum `{plan_only, modify_existing, create_new, refactor, audit, hygiene}` admitted directly without profile match for `plan_only` |
| **F3.** Script provenance ↔ planning-lock circularity: new script needs `architecture/script_manifest.yaml` entry, but editing manifest triggers planning-lock requiring plan evidence | `scripts/topology_doctor_policy_checks.py:259-261` `def planning_lock_trigger(path): if path.startswith("architecture/"): return "architecture"`; `scripts/topology_doctor_script_checks.py:246` `script_manifest_missing` issue when top-level script has no manifest entry | BLOCKING circular: every new script demands a manifest entry, every manifest edit demands plan evidence | manifest-companion edits AUTO-ADMITTED when accompanying a typed-intent `create_new` for `scripts/**` (loop-break) |
| **F4.** Plan packet ↔ task folder registration circularity: new `task_<date>_<slug>/PLAN.md` admittable, but `docs/operations/task_*/` reported `operations_task_unregistered` requiring docs/operations/AGENTS.md edit | `scripts/topology_doctor_docs_checks.py:431-457` `def check_operations_task_folders` emits `operations_task_unregistered` when `name not in registered`; `docs/operations/AGENTS.md` is the registry; **23 task folders in repo** (`ls -d docs/operations/task_*/ \| wc -l = 23`); **45 task_2026 mentions in AGENTS.md** | BLOCKING-equivalent friction (issue surfaces in topology_doctor output and gates closeout) | manifest-companion edits AUTO-ADMITTED when accompanying a typed-intent `create_new` for `docs/operations/task_*/**` (loop-break); the docs/operations/AGENTS.md row is the companion — same loop as F3 |
| **F5.** Naming/write-target severity: `low_high_alignment_report.py` → `script_long_lived_bad_name`; `docs/operations/low_high_alignment/*.json` → `diagnostic_forbidden_write_target`. Both BLOCKING; should be ADVISORY | `scripts/topology_doctor_script_checks.py:121-128` `script_long_lived_bad_name` issue (severity inherited from `_issue` which is uniform error in current code); `scripts/topology_doctor_script_checks.py:267-274` `script_diagnostic_forbidden_write_target` (uniform error). Verified via sibling worktree `/Users/leofitz/.openclaw/worktrees/zeus-low-high-alignment-recovery-2026-05-07/scripts/diagnose_low_high_alignment.py` (renamed to allowed `diagnose_` prefix as workaround) | BLOCKING (uniform `_issue` severity) | ADVISORY (WORKING reversibility class — name change is reversible by ordinary `git mv`; write-target is reversible by `git rm` + reroute) |
| F6 (companion). New test → `architecture/test_topology.yaml` companion edit demanded | `scripts/topology_doctor_docs_checks.py` (test-topology family); `architecture/test_topology.yaml` (1,276 LOC) | ADVISORY-companion (operator confirms reasonable, NOT a defect) | KEEP — same admission-policy class as F3/F4 (companion-loop-break applies but no severity flip needed) |

**Verification of all 6 emitter file:line citations completed 2026-05-07 within 10 minutes** per `feedback_grep_gate_before_contract_lock.md`. Numbers of task folders / AGENTS.md mentions counted at the start of this session.

### 1.2 Worktree lifecycle friction (Part 2)

| Surface | Verified state today | Gap |
|---|---|---|
| Worktree creation | `git worktree list` returns 3: `workspace-venus/zeus` (main), `worktrees/zeus-cleanup-debt`, `worktrees/zeus-low-high-alignment-recovery-2026-05-07`. No declarative sentinel per worktree (intent / agent / mode / base) | No first-class capability entry on `architecture/capabilities.yaml`; no per-worktree YAML sentinel; SessionStart cannot read prior intent |
| Branch keep-up | AGENTS.md root §4 git safety section (`grep -n "Git safety" AGENTS.md` = line 429); operator-shared draft proposal §D enumerates the matrix (5 cases). Decision matrix exists in prose; not encoded | Decision matrix exists in operator's draft (Authority §1 lines 209-231) but not in any executable advisory; not aligned with reversibility-class grammar |
| Post-merge cleanup | AGENTS.md root §2 line 200-204: "soft, agent decides; hook prints checklist on `gh pr merge`"; `.claude/hooks/registry.yaml::post_merge_cleanup` (advisory PostToolUse hook on `gh pr merge`) | Already advisory and shipped; no gap. Compose, do not duplicate. |
| Workspace hygiene | `backups/` in current worktree (untracked, per `git status`); `*.bak` not currently observed but historically present; `station_migration_alerts.json` in repo root (untracked) | No SessionStart audit; no advisory cron; never auto-delete (per `feedback_commit_per_phase_or_lose_everything.md` — destructive operations never automated) |
| Cross-worktree visibility | Only `git worktree list` provides this; agents do not read it on SessionStart | SessionStart should inject a one-line summary of active worktrees + branches |

The shipped hook redesign already covers two pieces:
- `pre_checkout_uncommitted_overlap` BLOCKING hook (`.claude/hooks/registry.yaml:69-89`) — the Part-2 worktree-loss prevention
- `post_merge_cleanup` ADVISORY hook (`.claude/hooks/registry.yaml`) — Part-2 post-merge guidance

Navigation v2 must NOT duplicate these; it composes by adding the **declarative sentinel + capability + hygiene SessionStart layer** that they do not own.

### 1.3 The K structural decisions (K=3 for Part 1; +5 capabilities for Part 2)

The 5 navigation friction patterns are NOT independent. Topology v1 found 5 decisions from 22 mechanisms (`# ULTIMATE_DESIGN.md §1`); hook redesign found 1 from 4. Navigation v2 finds **3 from 5** (K=3 << N=5).

**K1 — Severity tier missing on navigation rules.** The admission/script/docs emitters return `_issue(...)` with uniform error severity. Topology redesign §2.3 introduced 4 reversibility classes (ON_CHAIN, TRUTH_REWRITE, ARCHIVE, WORKING) with `enforcement_default ∈ {blocking, advisory_with_evidence_required, advisory}`. Navigation rules never inherited this grammar. **Maps to F1, F4, F5.** Default flip: ADVISORY for everything except a small explicit BLOCKING list (TRUTH_REWRITE+ paths from `architecture/capabilities.yaml` reversibility class).

**K2 — Admission gates are circularly dependent without companion-loop-break.** Gate A (planning-lock on `architecture/**`) requires Gate B's artifact (plan evidence). Gate B (typed-intent admission) requires Gate A's artifact (manifest entry). Topology redesign §3 source-tagging convention solves the parallel SOURCE↔STABLE problem with decorators that the route function reads at AST-walk time — same architectural shape applies here. **Maps to F3, F4, F6.** Loop-break: when typed-intent is `create_new` and the requested files include both the new artifact AND the manifest companion, BOTH are auto-admitted. Same applies to plan packet + AGENTS.md row.

**K3 — Profile-as-allowlist conflates orient-time intent with edit-time scope.** `allowed_files` was designed as a positive admission list for MODIFY-EXISTING tasks. CREATE-NEW tasks fail because the new file is not in the list (it cannot be — it does not exist yet). Topology redesign §4 route function decouples this with a `RouteCard` containing `capabilities` + `invariants` + `relationship_tests` + `hard_kernel_hits` + `reversibility` — all queries against the diff, not allowlists from a profile. **Maps to F1, F2.** Fix: typed-intent enum + `create_new` admission semantic ("path under typed-intent's scope_root, plus manifest companion" instead of "exact match in allowed_files").

K=3 is exact, not approximate. F1 hits K1+K3. F2 hits K3. F3 hits K2. F4 hits K1+K2. F5 hits K1.

For Part 2 (worktree lifecycle), Navigation v2 adds **5 new capability entries** to `architecture/capabilities.yaml` (16 → 21 entries; `metadata.catalog_size: 21`) plus 1 hygiene-advisory hook. Capability extension is the design's declared anti-pattern check (`# ULTIMATE_DESIGN.md §10` — "design accepts only appends in capabilities.yaml, not new dimensions"). This redesign is conformant.

---

## §2 Design

### 2.1 Layered architecture

```
┌────────────────────────────────────────────────────────────────┐
│ STABLE LAYER · YAML, sunset 12mo                               │
│   architecture/capabilities.yaml — +5 worktree-lifecycle caps  │
│     (worktree_create / worktree_branch_keepup /                │
│      worktree_post_merge_cleanup / workspace_hygiene_audit /   │
│      cross_worktree_visibility)                                │
│   architecture/naming_conventions.yaml — +severity-tier        │
│     annotation per file_naming + write-target rule             │
│   architecture/admission_severity.yaml (NEW, ~80 LOC)          │
│     map of issue-code → severity ∈ {advisory, blocking}        │
│     + reversibility_class ∈ {WORKING, ARCHIVE, TRUTH_REWRITE}  │
└────────────────────────┬───────────────────────────────────────┘
                         │ schema reads at decision time
┌────────────────────────▼───────────────────────────────────────┐
│ DECISION LAYER · Python in scripts/topology_doctor*.py         │
│   _route_card_next_action — typed-intent enum branch           │
│   _resolve_typed_intent — accept canonical typed intents       │
│   _reconcile_admission — companion-loop-break for create_new   │
│   _issue — read severity from admission_severity.yaml          │
│   (pure additions; ~250 LOC across 4 modules)                  │
└────────────────────────┬───────────────────────────────────────┘
                         │ existing CLI contract preserved
┌────────────────────────▼───────────────────────────────────────┐
│ AGENT-FACING LAYER · AGENTS.md + capabilities.yaml             │
│   AGENTS.md root §3 — typed-intent enum + 4-line worktree      │
│     section (≤30 lines added total)                            │
│   capabilities.yaml — 5 new entries with original_intent       │
│     scope_keywords for cross-worktree lookup                   │
└────────────────────────┬───────────────────────────────────────┘
                         │ runtime advisory only (NEVER auto-delete)
┌────────────────────────▼───────────────────────────────────────┐
│ HYGIENE LAYER · cron + SessionStart additionalContext          │
│   scripts/worktree_doctor.py (NEW, ≤200 LOC)                   │
│     --json status / --advisory-context summary                 │
│   .claude/hooks/registry.yaml — +1 SessionStart hook           │
│     workspace_hygiene_audit; ADVISORY only                     │
│   No auto-cleanup. No mutable lock service. Operator-only      │
│     destructive ops.                                           │
└────────────────────────────────────────────────────────────────┘
```

This is the topology redesign's 5-layer architecture transplanted to the navigation/worktree surface. `admission_severity.yaml` is the analogue of `reversibility.yaml`. Decision-layer additions are surgical pure additions to existing checker-family modules — NO 5-script `tools/agent_topology/` directory (NOT-DOING list explicitly forbids).

### 2.2 `architecture/admission_severity.yaml` (new file, ~80 LOC)

Single source of truth for issue-code severity. Replaces uniform "every issue is error" semantics in `topology_doctor*.py`.

```yaml
schema_version: 1
metadata:
  charter_version: 1.0.0
  catalog_size: 18
  created: 2026-05-07
  authority_basis: ULTIMATE_DESIGN §2.3 reversibility classes; sunset 2027-05-07
  default_severity: advisory   # K1 default flip; explicit BLOCKING list below
  default_reversibility_class: WORKING

issue_severity:
  # ─── K1: BLOCKING only on TRUTH_REWRITE+ reversibility ─

  - code: navigation_scope_expansion_required
    severity_when:
      typed_intent_in: [modify_existing, refactor]
      and_diff_touches_capability_class_in: [TRUTH_REWRITE, ON_CHAIN]
    severity: blocking
    severity_otherwise: advisory                 # F1 fix — was uniform blocking
    reversibility_class: WORKING
    sunset_date: 2027-05-07

  - code: operations_task_unregistered
    severity: advisory                            # F4 fix — was uniform blocking
    reversibility_class: WORKING
    advisory_action: |
      Add a row to docs/operations/AGENTS.md packet table.
      Or, when typed_intent=create_new and diff already contains
      docs/operations/AGENTS.md, auto-admit (companion-loop-break).
    sunset_date: 2027-05-07

  - code: script_long_lived_bad_name
    severity: advisory                            # F5 fix — was uniform blocking
    reversibility_class: WORKING
    advisory_action: |
      Rename to one of `architecture/naming_conventions.yaml::file_naming.scripts.long_lived.allowed_prefixes`.
      Or add a documented exception under `naming_conventions.yaml::exceptions`.
    sunset_date: 2027-05-07

  - code: script_diagnostic_forbidden_write_target
    severity: advisory                            # F5 fix — was uniform blocking
    reversibility_class: WORKING
    advisory_action: |
      Move write target under one of `architecture/script_manifest.yaml::diagnostic_allowed_write_targets`.
      Or change script's `authority_scope` from diagnostic_non_promotion to a non-diagnostic class.
    sunset_date: 2027-05-07

  - code: profile_needs_typed_intent
    severity: advisory                            # F2 fix — was de-facto blocking via generic profile
    reversibility_class: WORKING
    advisory_action: |
      Pass `--intent <typed_intent>` from the canonical enum: plan_only,
      modify_existing, create_new, refactor, audit, hygiene.
    sunset_date: 2027-05-07

  # ─── K2: companion-loop-break loops (auto-admit pairs) ─

  - code: script_manifest_missing
    severity_when:
      typed_intent: create_new
      and_diff_includes_companion: architecture/script_manifest.yaml
    severity: silent                              # F3 fix — companion auto-admits both files
    severity_otherwise: advisory
    reversibility_class: WORKING
    sunset_date: 2027-05-07

  - code: test_topology_test_unregistered
    severity_when:
      typed_intent: create_new
      and_diff_includes_companion: architecture/test_topology.yaml
    severity: silent                              # F6 — companion auto-admits both files
    severity_otherwise: advisory
    reversibility_class: WORKING
    sunset_date: 2027-05-07

  # ─── BLOCKING list (intentionally short) ─

  - code: planning_lock_evidence_missing
    severity_when:
      diff_touches_path_under: src/control/
      or_path_under: src/supervisor_api/
      or_capability_class_in: [ON_CHAIN, TRUTH_REWRITE]
    severity: blocking
    reversibility_class: TRUTH_REWRITE
    sunset_date: 2027-05-07

  - code: planning_lock_evidence_missing
    severity_when:
      diff_touches_path_under: architecture/
      and_not_companion_loop_break: true          # K2 — companion break supersedes
    severity: advisory                            # K1 demotion when not TRUTH_REWRITE+
    reversibility_class: WORKING
    sunset_date: 2027-05-07

  # ─── (further entries: 9 more codes mapped from existing checker families,
  #      mostly default advisory; full table populated in Phase 1) ─
```

### 2.3 Typed-intent enum (extension of existing `--intent` flag)

`scripts/topology_doctor_digest.py::_resolve_typed_intent` already exists (line 1230). Today it accepts free-form string and falls through to `_resolve_profile` when ambiguous. Navigation v2 adds canonical enum:

```yaml
# architecture/admission_severity.yaml::typed_intent_enum
typed_intent_enum:
  - id: plan_only
    description: |
      Plan-only operation. Reads files; writes ONLY to docs/operations/task_*/PLAN.md
      or .omc/plans/. No source/test/script edits.
    admits_path_globs:
      - docs/operations/task_*/PLAN.md
      - docs/operations/task_*/**/*.md
      - .omc/plans/**
    blocks_path_globs:
      - src/**
      - tests/**
      - scripts/**
    sunset_date: 2027-05-07

  - id: create_new
    description: |
      Create a new file (script, test, doc, plan packet, source module).
      Auto-admits the manifest companion required by mesh-maintenance rule.
    admits_path_globs:
      - scripts/**
      - tests/test_*.py
      - docs/operations/task_*/**
      - src/**
    companion_auto_admits:                         # K2 loop-break
      - when_path_glob: scripts/**
        also_admit: architecture/script_manifest.yaml
      - when_path_glob: tests/test_*.py
        also_admit: architecture/test_topology.yaml
      - when_path_glob: docs/operations/task_*/**
        also_admit: docs/operations/AGENTS.md
      - when_path_glob: src/**
        also_admit: architecture/source_rationale.yaml
    sunset_date: 2027-05-07

  - id: modify_existing
    description: Modify a file already on disk. Standard allowed_files semantics.
    sunset_date: 2027-05-07

  - id: refactor
    description: Rename / move / restructure existing files. Same as modify_existing
      plus auto-admit of companion manifest rows for each renamed entry.
    sunset_date: 2027-05-07

  - id: audit
    description: Read-only audit/investigation. Writes ONLY to evidence/** or
      .omc/research/**. Source/test/script writes blocked.
    sunset_date: 2027-05-07

  - id: hygiene
    description: Cleanup operation (delete dead code, archive promotion, backup
      sweep). ARCHIVE reversibility class; advisory unless touching TRUTH_REWRITE+ paths.
    sunset_date: 2027-05-07
```

The enum is read by `scripts/topology_doctor_digest.py::_resolve_typed_intent` at line 1230. Free-form `--intent` strings still work — they fall through to today's resolution path with `needs_typed_intent: true` warning (NOT blocking; F2 fix). Canonical enum strings short-circuit profile selection and admit directly.

### 2.4 Companion-loop-break (K2)

Pure addition to `scripts/topology_doctor_digest.py::_reconcile_admission`. ~30 LOC.

```python
# scripts/topology_doctor_digest.py — pseudo-code addition, after _reconcile_admission's existing logic

def _apply_companion_loop_break(admission, requested, typed_intent, severity_yaml):
    """K2 fix — auto-admit manifest companion when typed_intent is create_new
    and the diff already includes the companion path. Loop-break for the
    new-file ↔ manifest-edit ↔ planning-lock cycle."""
    if typed_intent not in {"create_new", "refactor"}:
        return admission
    enum_entry = severity_yaml["typed_intent_enum"].get_by_id(typed_intent)
    out_of_scope = list(admission.get("out_of_scope_files") or [])
    new_in_scope = []
    for path in out_of_scope:
        for rule in enum_entry.get("companion_auto_admits", []):
            if fnmatch(path, rule["when_path_glob"]):
                # Companion auto-admit only when the companion path is
                # ALSO in the diff (never auto-add a path the agent did
                # not request — that would silently widen scope).
                if rule["also_admit"] in requested:
                    new_in_scope.append(path)
                    break
    if new_in_scope:
        admission["status"] = "admitted"           # was scope_expansion_required
        admission["out_of_scope_files"] = [p for p in out_of_scope if p not in new_in_scope]
        admission["decision_basis"]["why"].append(
            f"companion_loop_break: typed_intent={typed_intent}; "
            f"admitted via companion_auto_admits rule"
        )
    return admission
```

This is the architectural fix for friction patterns F3+F4+F6. The agent declares `--intent create_new` AND includes both the new file AND the manifest companion in `--files`; the admission decision tree recognizes the companion and admits both. The agent does not need a separate planning-lock dance.

### 2.5 K1 severity tier resolution

Pure addition to `scripts/topology_doctor.py::_issue` (and to `topology_doctor_script_checks.py`, `topology_doctor_docs_checks.py`, `topology_doctor_policy_checks.py` API mediator). ~50 LOC across 4 modules.

```python
# scripts/topology_doctor.py — pseudo-code addition

@functools.lru_cache(maxsize=1)
def _admission_severity_yaml() -> dict:
    path = ROOT / "architecture/admission_severity.yaml"
    if not path.exists():
        return {"issue_severity": [], "default_severity": "advisory"}
    return yaml.safe_load(path.read_text())

def _issue_severity(code: str, context: dict) -> str:
    """Returns 'blocking' | 'advisory' | 'silent' for an issue code.
    Reads admission_severity.yaml; default 'advisory' (K1 flip).
    `context` carries typed_intent, diff paths, capability_class hits,
    companion_loop_break flag, etc."""
    catalog = _admission_severity_yaml()
    for entry in catalog.get("issue_severity", []):
        if entry["code"] != code:
            continue
        when = entry.get("severity_when") or {}
        if _matches_when(when, context):
            return entry["severity"]
        if "severity_otherwise" in entry:
            return entry["severity_otherwise"]
        return entry["severity"]
    return catalog.get("default_severity", "advisory")
```

Existing `_issue(...)` calls remain unchanged. The CHANGE is in the consumer that reads issues and sets the admission status. Today it treats every issue as error; the consumer now reads `_issue_severity(code, context)` and only escalates to `scope_expansion_required` / `blocked` when severity is `blocking`.

### 2.6 Worktree lifecycle capabilities (5 new entries in `architecture/capabilities.yaml`)

Schema appends only — no new dimensions in route_function (`# ULTIMATE_DESIGN.md §10` anti-pattern check). Total: 16 → 21 entries; `metadata.catalog_size: 21`.

```yaml
# architecture/capabilities.yaml additions

  # ─── new: worktree_create ─
  - id: worktree_create
    owner_module: scripts/worktree_doctor.py
    intent: >
      Create a new git worktree with a per-worktree YAML sentinel declaring
      intent / agent / mode / base / created_at. The sentinel is read by
      SessionStart for cross-worktree visibility and by post-merge cleanup
      for ownership lookup. Creating a worktree is a structural git op;
      WORKING reversibility class because the worktree can be removed by
      `git worktree remove` without external state mutation.
    relationships:
      protects_invariants: []                    # operational, not invariant-bound
      blocked_when: []
    hard_kernel_paths:
      - scripts/worktree_doctor.py
      - .git/worktrees/                          # git-managed; sentinel sibling
    original_intent:
      intent_test: "task creates a new worktree (git worktree add invocation)"
      does_not_fit: log_and_advisory
      scope_keywords: [worktree, create, new, parallel, agent, isolation]
      out_of_scope_keywords: [delete, remove, switch, checkout, prune]
    sunset_date: 2027-05-07
    lease_required: false
    telemetry:
      ritual_signal_emitted: true
      latency_budget_ms: 2000
    reversibility_class: WORKING

  # ─── new: worktree_branch_keepup ─
  - id: worktree_branch_keepup
    owner_module: scripts/worktree_doctor.py
    intent: >
      Recommend (NOT auto-execute) the appropriate ff/rebase/merge/close
      decision after origin/main advances. Decision matrix is encoded as
      ADVISORY output; the agent reads and decides. Operator's draft
      proposal §D enumerates the 5 cases; this capability owns the
      structured recommendation. Does NOT mutate git state; pure read.
    relationships:
      protects_invariants: []
      blocked_when: []
    hard_kernel_paths:
      - scripts/worktree_doctor.py
    original_intent:
      intent_test: "main has advanced AND current branch is not main"
      does_not_fit: log_and_advisory
      scope_keywords: [keepup, rebase, merge, fast-forward, stale, branch]
      out_of_scope_keywords: [auto-merge, force, push]
    sunset_date: 2027-05-07
    lease_required: false
    telemetry:
      ritual_signal_emitted: true
      latency_budget_ms: 1000
    reversibility_class: WORKING

  # ─── new: worktree_post_merge_cleanup ─
  - id: worktree_post_merge_cleanup
    owner_module: scripts/worktree_doctor.py
    intent: >
      ADVISORY checklist after `gh pr merge`: branch close recommendation,
      worktree close recommendation, backup/draft sweep recommendation.
      NEVER auto-deletes (per feedback_commit_per_phase_or_lose_everything.md).
      Composes with .claude/hooks/registry.yaml::post_merge_cleanup hook —
      this capability owns the entry-point script; the hook owns the
      runtime trigger.
    relationships:
      protects_invariants: []
      blocked_when: []
    hard_kernel_paths:
      - scripts/worktree_doctor.py
      - .claude/hooks/registry.yaml
    original_intent:
      intent_test: "task is a successful gh pr merge OR explicit cleanup audit"
      does_not_fit: log_and_advisory
      scope_keywords: [cleanup, post_merge, archive, sweep, backup]
      out_of_scope_keywords: [auto-delete, force-remove]
    sunset_date: 2027-05-07
    lease_required: false
    telemetry:
      ritual_signal_emitted: true
      latency_budget_ms: 500
    reversibility_class: WORKING

  # ─── new: workspace_hygiene_audit ─
  - id: workspace_hygiene_audit
    owner_module: scripts/worktree_doctor.py
    intent: >
      ADVISORY audit of workspace clutter: backups/, *.bak files, root-level
      drafts (e.g., station_migration_alerts.json), stale `.omc/state/agent-replay-*.jsonl`
      files. Emits structured advisory; never deletes. Runs under SessionStart
      and via cron at low frequency.
    relationships:
      protects_invariants: []
      blocked_when: []
    hard_kernel_paths:
      - scripts/worktree_doctor.py
    original_intent:
      intent_test: "task is a workspace audit / hygiene check OR SessionStart auto-trigger"
      does_not_fit: log_and_advisory
      scope_keywords: [hygiene, backup, draft, stale, audit, sweep]
      out_of_scope_keywords: [auto-delete, force-remove]
    sunset_date: 2027-05-07
    lease_required: false
    telemetry:
      ritual_signal_emitted: true
      latency_budget_ms: 300
    reversibility_class: WORKING

  # ─── new: cross_worktree_visibility ─
  - id: cross_worktree_visibility
    owner_module: scripts/worktree_doctor.py
    intent: >
      ADVISORY one-line summary of active worktrees + branches + last-commit
      timestamp + sentinel intent. Injected into agent context via
      SessionStart additionalContext. Read-only; never mutates worktree state.
      Solves the "agent in worktree A doesn't know agent in worktree B is
      mid-phase on the same surface" problem without a mutable lock service.
    relationships:
      protects_invariants: []
      blocked_when: []
    hard_kernel_paths:
      - scripts/worktree_doctor.py
    original_intent:
      intent_test: "SessionStart event OR explicit cross-worktree status query"
      does_not_fit: log_and_advisory
      scope_keywords: [worktree, status, visibility, parallel, sentinel]
      out_of_scope_keywords: [lock, mutex, registry, write]
    sunset_date: 2027-05-07
    lease_required: false
    telemetry:
      ritual_signal_emitted: true
      latency_budget_ms: 500
    reversibility_class: WORKING
```

### 2.7 Per-worktree YAML sentinel (read-only declarative)

```yaml
# .git/worktrees/<name>/zeus_worktree.yaml (or sibling next to .git pointer)
schema_version: 1
worktree:
  name: zeus-cleanup-debt
  path: /Users/leofitz/.openclaw/worktrees/zeus-cleanup-debt
  branch: cleanup-debt-2026-05-07
  base: main@f56b33b0
  agent_class: claude_code                       # claude_code | codex | human | reviewer
  mode: write                                    # write | review-only | test-only | hotfix
  task_slug: navigation-topology-v2
  created_at: 2026-05-07T03:14:00-05:00
  intent: >
    Single PLAN.md for navigation/admission redesign. NO PUSH NO PR. Stop and
    report when committed.
sunset_date: 2026-08-07                          # 90 days
```

NOT a mutable lock service. NOT polled by other agents. ONLY a read-only declaration that SessionStart and worktree_doctor.py can ingest. Per `feedback_orchestrator_offload_lookups.md` — orchestrator already tracks active sessions; sentinel is operator-supplied context, not a runtime mechanism.

The sentinel is committed by the worktree creation flow but never read at edit-time gates. Agents can ignore it; it doesn't gate. This is the K1 default-advisory shape.

### 2.8 `scripts/worktree_doctor.py` (NEW, ≤200 LOC)

Single new script. Reuses `dispatch.py` patterns from hook redesign — does NOT proliferate `tools/agent_topology/` 5-script directory (NOT-DOING list).

```python
# scripts/worktree_doctor.py  (pseudo-code, ≤200 LOC)
# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §2.6-§2.8; sunset 2027-05-07

"""ADVISORY-only worktree lifecycle helper.

Subcommands:
  status       — JSON summary of all active worktrees + sentinels
  advisory     — additionalContext-formatted advisory for SessionStart
  branch-keepup — recommend ff/rebase/merge/close for current branch
  hygiene      — list workspace clutter (NEVER deletes)

Never mutates git state. Never deletes files. Operator-only destructive ops.
"""
from __future__ import annotations
import argparse, json, subprocess, sys, yaml, os, time
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parents[1]

def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=5).stdout

def cmd_status(_args) -> int:
    worktrees = _parse_worktree_list(_git("worktree", "list", "--porcelain"))
    for wt in worktrees:
        wt["sentinel"] = _read_sentinel(wt["path"])
        wt["dirty"] = bool(_git("status", "--short", "-s", "--porcelain").strip()) if wt["is_current"] else None
        wt["last_commit_ts"] = _git("log", "-1", "--format=%ct", wt["branch"]).strip()
    print(json.dumps({"worktrees": worktrees}, indent=2))
    return 0

def cmd_advisory(_args) -> int:
    """One-line summary suitable for SessionStart additionalContext."""
    worktrees = _parse_worktree_list(_git("worktree", "list", "--porcelain"))
    lines = [f"Active worktrees: {len(worktrees)}"]
    for wt in worktrees:
        sentinel = _read_sentinel(wt["path"]) or {}
        intent = (sentinel.get("worktree", {}).get("intent") or "")[:80]
        lines.append(f"  [{wt['branch']}] {wt['path']} — {intent}")
    print("\n".join(lines))
    return 0

def cmd_branch_keepup(_args) -> int:
    """Decision matrix per operator's draft §D, encoded as ADVISORY recommendation."""
    current = _git("branch", "--show-current").strip()
    if not current or current == "main":
        print(json.dumps({"recommendation": "no-action", "reason": "on main or detached"}))
        return 0
    base = _git("merge-base", current, "origin/main").strip()
    main_head = _git("rev-parse", "origin/main").strip()
    behind = int(_git("rev-list", "--count", f"{current}..origin/main").strip() or "0")
    ahead = int(_git("rev-list", "--count", f"origin/main..{current}").strip() or "0")
    merged = current in _git("branch", "--merged", "origin/main")
    dirty = bool(_git("status", "--short", "--porcelain").strip())
    rec = _decision_matrix(ahead=ahead, behind=behind, merged=merged, dirty=dirty)
    print(json.dumps({"branch": current, "ahead": ahead, "behind": behind,
                      "merged": merged, "dirty": dirty, "recommendation": rec}))
    return 0

def cmd_hygiene(_args) -> int:
    """Advisory list of workspace clutter. NEVER deletes."""
    clutter = []
    for pattern in ("backups", "*.bak", "station_migration_alerts.json"):
        for p in REPO_ROOT.glob(pattern):
            if p.exists():
                clutter.append({"path": str(p.relative_to(REPO_ROOT)),
                                "size_bytes": p.stat().st_size if p.is_file() else None,
                                "advisory": "review and remove if no longer needed"})
    for p in (REPO_ROOT / ".omc/state").glob("agent-replay-*.jsonl"):
        clutter.append({"path": str(p.relative_to(REPO_ROOT)),
                        "size_bytes": p.stat().st_size, "advisory": "stale agent replay log; safe to delete if no recovery in progress"})
    print(json.dumps({"clutter": clutter, "action": "advisory_only_never_auto_delete"}))
    return 0

def _decision_matrix(*, ahead, behind, merged, dirty):
    """Encodes operator draft §D: 5 cases."""
    if merged:
        return "branch_already_merged_close" if not dirty else "checkpoint_first_then_close"
    if ahead == 0 and behind > 0:
        return "fresh_branch_or_ff_only" if not dirty else "checkpoint_first"
    if ahead > 0 and behind > 0:
        return "rebase_if_private_else_merge_origin_main" if not dirty else "checkpoint_first_then_choose"
    if ahead == 0 and behind == 0:
        return "current_with_main_proceed"
    return "uncertain_block_and_report"

# (parse_worktree_list, read_sentinel, etc. omitted)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("advisory")
    sub.add_parser("branch-keepup")
    sub.add_parser("hygiene")
    args = ap.parse_args()
    sys.exit({"status": cmd_status, "advisory": cmd_advisory,
              "branch-keepup": cmd_branch_keepup, "hygiene": cmd_hygiene}[args.cmd](args))
```

### 2.9 `.claude/hooks/registry.yaml` — +1 SessionStart hook

```yaml
# Append to .claude/hooks/registry.yaml::hooks
  - id: workspace_hygiene_audit
    event: SessionStart
    matcher: "*"
    intent: >
      On session start, emit one-line summary of active worktrees + sentinel
      intent + workspace-clutter count. ADVISORY only — agent reads and decides.
      NEVER auto-deletes anything.
    severity: ADVISORY
    reversibility_class: WORKING
    bypass_policy:
      class: not_required
    sunset_date: 2026-08-07
    telemetry:
      ritual_signal_emitted: true
    owner_module: scripts/worktree_doctor.py
```

`dispatch.py` ports the SessionStart event; calls `python3 scripts/worktree_doctor.py advisory` and emits `additionalContext` (~5-10 lines). No new dispatch.py path semantics — uses the existing ADVISORY contract validated in hook redesign Phase 3.R critic verdict (`evidence/hook_redesign_critic_opus_final_v2.md` ATTACK 8).

### 2.10 AGENTS.md updates (≤30 lines, NOT-DOING enforced)

Two surgical additions to root `AGENTS.md`:

**§3 (line ~206)** — append typed-intent enum (≤15 lines):

```markdown
**Typed-intent enum** (canonical strings for `--intent`, used by topology_doctor):
- `plan_only` — admit only `docs/operations/task_*/PLAN.md`, `.omc/plans/**`
- `create_new` — admit new file + auto-admit manifest companion (e.g.,
  `architecture/script_manifest.yaml` for new scripts)
- `modify_existing` — standard allowed_files semantics
- `refactor` — modify_existing + auto-admit companion rows for renames
- `audit` — read-only audit; writes admitted to `evidence/**` and `.omc/research/**` only
- `hygiene` — cleanup (delete dead code, archive promotion); ARCHIVE class

Free-form `--intent` still works (advisory `needs_typed_intent` warning;
not blocking). Canonical enum short-circuits profile selection for direct
admission. See `architecture/admission_severity.yaml::typed_intent_enum`.
```

**§4 git safety subsection (line ~429)** — append worktree section (≤15 lines):

```markdown
### Worktree lifecycle

When in doubt: `python3 scripts/worktree_doctor.py advisory` for cross-worktree
visibility. `python3 scripts/worktree_doctor.py branch-keepup` returns a
recommendation (NEVER auto-executes ff/rebase/merge).

Per-worktree sentinel at `.git/worktrees/<name>/zeus_worktree.yaml` declares
agent / mode / task_slug / intent. SessionStart reads this for context. The
sentinel is operator-supplied, not a lock service.

Workspace hygiene: `python3 scripts/worktree_doctor.py hygiene` lists clutter
(`backups/`, `*.bak`, root drafts, stale `.omc/state/agent-replay-*.jsonl`) as
ADVISORY only. NEVER auto-deletes (per `feedback_commit_per_phase_or_lose_everything.md`).

Pre-checkout silent-revert prevention is owned by `pre_checkout_uncommitted_overlap`
in `.claude/hooks/registry.yaml` — composes with this section, not duplicated.
```

Total AGENTS.md update: 28 lines added (well under 30). NOT-DOING list compliance.

### 2.11 What this plan does NOT do (NOT-DOING enforcement)

- No `tools/agent_topology/` directory with 5 scripts. ONE script: `scripts/worktree_doctor.py`. ~200 LOC.
- No `.agent-topology/` mutable runtime registry / lock service. ONLY per-worktree YAML sentinel (read-only declarative).
- No +200 lines to AGENTS.md / CLAUDE.md. ≤30 lines. Rest in `architecture/admission_severity.yaml` + `architecture/capabilities.yaml`.
- No Codex hooks parallel implementation. Single `dispatch.py` from hook redesign; client-dispatch only.
- No banning `git switch / checkout / reset` outright. Only TRUTH_REWRITE+ subset is denied — and that subset is ALREADY owned by `pre_checkout_uncommitted_overlap` (hook redesign §2.6).
- No fail-closed default. K1 flips to ADVISORY default; BLOCKING reserved for TRUTH_REWRITE+ paths from `architecture/capabilities.yaml`.

---

## §3 Phases

Three phases sized for sonnet executor (~1.5-2h each). Per-phase commit on `cleanup-debt-2026-05-07`. NO PUSH NO PR.

### Phase 1 — Stable layer + severity registry (~1.5h)

**Owner:** sonnet implementer.

**LOC estimate:** ~430 LOC across 4 files.

**Deliverables:**

| File | LOC | Source |
|---|---|---|
| `architecture/admission_severity.yaml` (NEW) | ~150 | §2.2 schema; 18 issue codes + 6 typed_intent entries |
| `architecture/capabilities.yaml` (extend) | +80 | §2.6 — 5 new entries; `metadata.catalog_size: 16 → 21` |
| `tests/test_admission_severity_schema.py` (NEW) | ~80 | YAML schema validation; sunset_date required; severity ∈ {advisory, blocking, silent}; reversibility_class ∈ {WORKING, ARCHIVE, TRUTH_REWRITE, ON_CHAIN} |
| `tests/test_typed_intent_enum.py` (NEW) | ~60 | enum entries valid; companion_auto_admits paths exist; sunset_date present |
| `tests/test_capabilities_schema.py` (extend) | +50 | catalog_size assertion 21; 5 new entries each carry hard_kernel_paths + intent_test + reversibility_class |
| `architecture/naming_conventions.yaml` (extend) | +10 | severity-tier annotation under `file_naming.scripts.long_lived` cross-referencing admission_severity.yaml |

**Exit criteria:**
- All schema validators green (4 tests).
- `architecture/capabilities.yaml::metadata.catalog_size = 21`.
- `architecture/admission_severity.yaml` covers all 9 issue codes named in §2.2 plus 6 typed-intent enum entries.
- File headers carry Created / Last reused/audited / Authority basis (per code-provenance rule in `~/.claude/CLAUDE.md`).
- No decision-layer changes yet — `topology_doctor*.py` still emits issues with uniform severity. Parallel install. Zero behavioral change for agents.
- Commit on `cleanup-debt-2026-05-07` with `[skip-invariant]` (Phase 1 ratchets the test count by +4; structured override `BASELINE_RATCHET` per hook redesign §2.3 applies).

**Rollback:** files are new; `git rm` + `git revert`. Zero impact on running navigation. capabilities.yaml extension is a pure append — revert via `git checkout main -- architecture/capabilities.yaml`.

### Phase 2 — Decision-layer wiring + companion-loop-break (~2h)

**Owner:** sonnet implementer.

**LOC estimate:** ~320 LOC across 5 files.

**Deliverables:**

| File | LOC | Source |
|---|---|---|
| `scripts/topology_doctor.py` (extend `_issue_severity` + `_route_card_next_action`) | +60 | §2.5 — read severity from admission_severity.yaml; default advisory; only escalate on blocking |
| `scripts/topology_doctor_digest.py` (extend `_resolve_typed_intent` + `_apply_companion_loop_break`) | +80 | §2.3 + §2.4 — canonical enum dispatch + companion-auto-admit |
| `scripts/topology_doctor_script_checks.py` (severity-aware emission) | +30 | F5 — `script_long_lived_bad_name` and `script_diagnostic_forbidden_write_target` flow through severity registry; advisory by default |
| `scripts/topology_doctor_docs_checks.py` (severity-aware emission) | +20 | F4 — `operations_task_unregistered` flows through severity registry; advisory by default |
| `tests/test_admission_companion_loop_break.py` (NEW) | ~90 | F3 fix verification: `--intent create_new --files scripts/foo.py architecture/script_manifest.yaml` admits both; F4 fix verification: same for `docs/operations/task_*/PLAN.md` + `docs/operations/AGENTS.md`; F1 fix verification: typed-intent `plan_only` admits docs without scope_expansion_required |
| `tests/test_admission_severity_demotion.py` (NEW) | ~40 | F5 fix verification: bad-name script with WORKING capability class emits advisory NOT blocking; `--intent create_new` flow does not cause planning-lock to fire on architecture/script_manifest.yaml |

**Exit criteria:**
- All Phase-1 tests green + 2 new test files green.
- F1, F2, F3, F4, F5 all reproducible-as-pass via integration test (real digest invocation):
  - F1: `--intent plan_only --files docs/operations/task_2026-05-07_navigation_topology_v2/PLAN.md` returns `admission.status: admitted`
  - F2: `--intent "plan only; no code edits"` returns `needs_typed_intent: true` with status `advisory_only` (NOT blocked)
  - F3: `--intent create_new --files scripts/diagnose_test.py architecture/script_manifest.yaml` returns `admission.status: admitted` for both
  - F4: `--intent create_new --files docs/operations/task_2026-05-07_test/PLAN.md docs/operations/AGENTS.md` returns `admission.status: admitted` for both
  - F5: `script_long_lived_bad_name` issue emits with `severity: advisory` and admission still `admitted` for the diff
- `python3 scripts/topology_doctor.py --navigation --task "test" --intent plan_only --files docs/operations/task_2026-05-07_navigation_topology_v2/PLAN.md` end-to-end smoke test passes.
- 14-day shadow window: severity demotions emit `ritual_signal` with `severity_demoted: true` for every issue that would have been BLOCKING under v1; telemetry tracks. NO BLOCKING regression introduced.
- Commit on `cleanup-debt-2026-05-07`. `[skip-invariant]` with `STRUCTURED_OVERRIDE=BASELINE_RATCHET` per hook redesign §2.3.

**Rollback:** revert the 4 file extensions; admission_severity.yaml + capabilities.yaml stay (dormant); falls back to Phase-1 state. Per-issue rollback also available: `ZEUS_ADMISSION_SEVERITY=off` in env reverts to uniform-error behavior.

### Phase 3 — Worktree lifecycle script + hygiene SessionStart hook (~1.5h)

**Owner:** sonnet implementer.

**LOC estimate:** ~430 LOC across 4 files.

**Deliverables:**

| File | LOC | Source |
|---|---|---|
| `scripts/worktree_doctor.py` (NEW) | ~200 | §2.8 — 4 subcommands (status, advisory, branch-keepup, hygiene); read-only; ≤200 LOC |
| `.claude/hooks/registry.yaml` (extend) | +15 | §2.9 — 1 SessionStart hook entry |
| `.claude/hooks/dispatch.py` (extend) | +30 | new `_run_advisory_check_workspace_hygiene_audit` function; calls `worktree_doctor.py advisory`; emits `additionalContext` |
| `tests/test_worktree_doctor.py` (NEW) | ~120 | subcommand smoke tests (each subcommand returns 0 + valid JSON or text); branch-keepup decision matrix table tests for the 5 cases; hygiene NEVER deletes assertion |
| `tests/test_workspace_hygiene_advisory_hook.py` (NEW) | ~40 | dispatch.py SessionStart hook smoke; advisory only; ≤500 char additionalContext budget; never blocks |
| `architecture/script_manifest.yaml` (extend) | +25 | manifest entry for `worktree_doctor.py`; lifecycle: long_lived; allowed_prefix is `worktree_`; NEW exception added to naming_conventions.yaml exceptions |

**Exit criteria:**
- All Phase-1 + Phase-2 tests green + 2 new test files green.
- `python3 scripts/worktree_doctor.py advisory` returns one-line summary on every active worktree (verified against `git worktree list` output).
- `python3 scripts/worktree_doctor.py branch-keepup` returns a structured recommendation matching operator's draft §D for the 5 cases.
- `python3 scripts/worktree_doctor.py hygiene` lists `backups/` (currently in `cleanup-debt-2026-05-07`) WITHOUT deleting it.
- SessionStart hook ships in `.claude/hooks/registry.yaml`; `dispatch.py` smoke test passes the new hook event.
- Commit on `cleanup-debt-2026-05-07`. `[skip-invariant]` with `STRUCTURED_OVERRIDE=BASELINE_RATCHET`.

**Rollback:** `git rm scripts/worktree_doctor.py tests/test_worktree_doctor.py tests/test_workspace_hygiene_advisory_hook.py`; `git checkout main -- .claude/hooks/registry.yaml .claude/hooks/dispatch.py architecture/script_manifest.yaml`. Worktree sentinel is operator-supplied; if absent, advisory degrades to "no sentinel found" output (no error).

### Cross-phase invariants (always true)

- **No PR. No push.** All commits stay local on `cleanup-debt-2026-05-07`.
- **Severity flip is reversible.** Every issue code that flips from BLOCKING → ADVISORY in Phase 2 emits `ritual_signal` `severity_demoted: true`; if 14-day telemetry shows the demotion was unsafe, single env-var rollback (`ZEUS_ADMISSION_SEVERITY=off`).
- **No auto-delete.** `worktree_doctor.py hygiene` is advisory-only forever (per `feedback_commit_per_phase_or_lose_everything.md`).
- **No mutable lock service.** Per-worktree YAML sentinel only; orchestrator already tracks active sessions per `feedback_orchestrator_offload_lookups.md`.
- **No new schema dimension.** Capability appends only (`# ULTIMATE_DESIGN.md §10` anti-pattern check).

---

## §4 Risks

| ID | Title | Prob (L/M/H) | Impact (L/M/H) | Structural mitigation | Detection signal |
|---|---|---|---|---|---|
| H-R1 | Severity flip introduces silent admit-when-should-block (K1 default ADVISORY hides real defects) | M | H | `architecture/admission_severity.yaml` carries explicit BLOCKING list mapped to TRUTH_REWRITE+ reversibility class — never blanket-flip; every demoted issue emits `ritual_signal` `severity_demoted: true`; tests assert blocking severity preserved for `architecture/`, `src/state/`, `src/control/`, `src/supervisor_api/` paths | `ritual_signal` `severity_demoted: true` count over 30d > 50× baseline = signal that demotion is masking a real category |
| H-R2 | Companion-loop-break enables typo-driven mass file creation (agent says `--intent create_new` and admits dozens of files plus their manifests) | M | M | companion_auto_admits requires the companion to already be in `--files` (never auto-add a path the agent did not request); `out_of_scope_files` is bounded by the requested set, not by the manifest's full path globs | `tests/test_admission_companion_loop_break.py` regression: agent passing `--files scripts/foo.py` alone (without manifest) returns `scope_expansion_required` — companion does NOT silently widen |
| H-R3 | Typed-intent enum becomes the new ambiguity surface (agents pass `audit` when they mean `modify_existing`) | M | M | enum entries carry `admits_path_globs` + `blocks_path_globs`; agent passing `--intent audit` with `--files src/foo.py` is blocked at admission decision (typed-intent mismatch); free-form `--intent` still falls through with `needs_typed_intent` warning so the migration runway is gentle | `ritual_signal` typed_intent_mismatch count over 30d; >0.5/day signals enum mismatch widespread |
| H-R4 | `worktree_doctor.py advisory` SessionStart hook adds latency (~500ms × every session start) | M | L | `latency_budget_ms: 500` declared in capability; hook invocation runs in parallel with other SessionStart hooks per Claude Code Hooks parallel exec; falls open on any error per dispatch.py ADVISORY contract | dispatch.py crash log; SessionStart total latency >2s |
| H-R5 | Per-worktree YAML sentinel becomes mandatory (agents refuse to work without it) — Help-Inflation Ratchet recurs | L | M | sentinel is read-only; absence emits "no sentinel found" advisory only; no gate enforces presence; CHARTER M5 `INV-HELP-NOT-GATE` test extended to assert sentinel absence does not block admission | `tests/test_help_not_gate.py` regression on sentinel-blocking |
| H-R6 | New 5 capability entries in `capabilities.yaml` accidentally collide with existing 16 (catalog_size mismatch, hard_kernel_paths overlap) | L | M | Phase 1 schema test asserts unique `id` field; Phase 1 schema test asserts no `hard_kernel_paths` duplicate across capabilities (route function intersection logic relies on disjoint paths) | `tests/test_capabilities_schema.py` red on collision |
| H-R7 | Worktree-loss recurs if `pre_checkout_uncommitted_overlap` and Navigation v2 worktree advisory disagree (e.g., advisory says "safe" but hook denies) | M | H | Navigation v2 owns ADVISORY surface only; the hook owns BLOCKING. They cannot disagree because their decision functions are independent (advisory is informational; hook denies based on git diff). Test asserts independence: hook fires regardless of advisory output | `evidence/hook_redesign_critic_opus_final_v2.md` ATTACK 8 manual smoke evidence preserved; new test `test_advisory_does_not_override_blocking_hook.py` |
| H-R8 | Decision matrix in `branch-keepup` produces wrong recommendation on edge cases (detached HEAD, missing origin/main, `gh` unavailable) | M | M | `_decision_matrix` defaults to `"uncertain_block_and_report"` for all unhandled cases; subprocess timeouts (`timeout=5`) prevent hangs; `_git()` returns empty string on subprocess failure (handled by int parse default 0) | unit test for each of the 5 cases plus 5 edge cases (no origin, no main, detached, dirty, ghost branch) |

---

## §5 Charter / drift mechanisms (M1-M5 binding, abbreviated)

This section is the topology redesign's CHARTER scaled down to the navigation surface. Refer to `docs/operations/task_2026-05-06_topology_redesign/ultraplan/# ANTI_DRIFT_CHARTER.md` for the full M1-M5 framework; the table below is the navigation-redesign-specific binding.

| Mechanism | Concrete artifact | Navigation v2 binding |
|---|---|---|
| **M1 telemetry-as-output** | `logs/ritual_signal/<YYYY-MM>.jsonl` (existing) | every navigation decision (admit / advisory_only / scope_expansion_required) emits one line; new field `severity_demoted: true` for K1 demotions; new field `companion_loop_break: true` for K2 admissions |
| **M2 opt-in-by-default** | `severity: advisory` is the YAML default in `architecture/admission_severity.yaml`; promotion to `severity: blocking` requires (a) capability_class ∈ {TRUTH_REWRITE, ON_CHAIN} or (b) explicit operator-signed entry under `evidence/admission_blocking_promotions/<date>.md` | promotion = evidence-bound; demotion = automatic per the YAML default |
| **M3 sunset clock per artifact** | `sunset_date` field required by `tests/test_admission_severity_schema.py`; default 12 months for stable primitives, 90 days for `severity_when` policy entries | every YAML entry carries a sunset; auto-demote at expiry |
| **M4 original-intent contract** | `original_intent.intent_test` keys on the 5 new capability entries (`worktree_create`, etc.); `does_not_fit: log_and_advisory` | `worktree_doctor.py` invocations on out-of-scope tasks log advisory but exit 0 (per CHARTER §6 anti-禁书 mechanism) |
| **M5 INV-NAVIGATION-NOT-GATE** (extends `INV-HELP-NOT-GATE`) | `tests/test_help_not_gate.py` (existing) extended with: (a) admission_severity.yaml entries do not block out-of-scope diffs; (b) typed-intent mismatch returns advisory not denial; (c) per-worktree sentinel absence does not block admission | composes with topology's `INV-HELP-NOT-GATE`; one test file, two new test functions |

**Telemetry review cadence:** monthly critic-agent review of `logs/ritual_signal/*.jsonl` filtered to navigation entries; quarterly operator review of severity_demoted ratio (target: <5% of all navigation events should be `severity_demoted: true`; >50% means the K1 flip was over-aggressive and needs tuning).

**Operator override protocol:** existing `OPERATOR_OVERRIDE` override_id (hook redesign `overrides.yaml`) is the single emergency clause; requires evidence file + 14d auto-expiry. Severity-flip rollback is `ZEUS_ADMISSION_SEVERITY=off` env var (out-of-band, fast).

---

## §6 Cutover

### 6.1 Pre-cutover gates

- [Phase 1 done] Schema validators green; `capabilities.yaml::metadata.catalog_size == 21`.
- [Phase 2 done] Decision-layer wiring complete; F1-F5 reproducible-as-pass via integration test.
- 14-day shadow telemetry window: every K1 demotion emits `severity_demoted: true`; if any test under `tests/test_*invariant*.py` newly red post-Phase-2, rollback before Phase 3.
- Migration shim: existing free-form `--intent` continues to work with `needs_typed_intent` advisory (NEVER blocking); 30-day soft migration period before any harder gate.
- `tests/test_admission_severity_schema.py`, `tests/test_admission_companion_loop_break.py`, `tests/test_admission_severity_demotion.py`, `tests/test_help_not_gate.py` (extended) all green.

### 6.2 Cutover sequence (gradual, 14-day shadow)

| Day | Action | Rollback trigger |
|---|---|---|
| 1 | Phase 1 ships (schemas + capabilities extension) | Phase 1 schema test red |
| 2 | Phase 2 ships in shadow mode (severity registry consumed; old uniform-error path fallback if env `ZEUS_ADMISSION_SEVERITY=off`) | F1-F5 integration test red on real friction reproduction |
| 2-9 | 7-day shadow window: severity_demoted telemetry tracked; no human action on demotion warnings | severity_demoted ratio >50% of navigation events (over-aggressive flip) OR any `tests/test_*invariant*.py` newly red |
| 9-14 | Phase 3 ships (worktree_doctor + SessionStart advisory hook) | dispatch.py crash on workspace_hygiene_audit hook |
| 14-21 | Free-form `--intent` continues with `needs_typed_intent` advisory; agents migrating to canonical enum | post-cutover navigation_scope_expansion_required count not declining (<25% reduction by day-21) |
| 30 | Telemetry baseline reset; CHARTER §5.1 quarterly review fires | any severity_demoted event triggered a real defect that hit production (kill-switch level rollback) |

### 6.3 First 24h / 7d / 30d telemetry watch

| Metric | Source | Day-1 floor | Day-7 floor | Day-30 target |
|---|---|---|---|---|
| `navigation_scope_expansion_required` daily rate | `logs/ritual_signal/*.jsonl` filter | <baseline+0 (current count baseline) | <baseline×0.5 | <baseline×0.2 |
| `severity_demoted: true` events | `logs/ritual_signal/*.jsonl` filter | any nonzero (proves the flip works) | <50% of navigation events | <30% of navigation events |
| `companion_loop_break: true` events | `logs/ritual_signal/*.jsonl` filter | n/a | ≥5/day (proves create_new flow works) | ≥10/day |
| Typed-intent enum adoption | `--intent` flag values in CLI logs | <10% canonical | ≥40% canonical | ≥80% canonical |
| Worktree sentinel adoption | `.git/worktrees/*/zeus_worktree.yaml` count | n/a (Phase 3 ships day-9) | ≥1 (proves 1 agent creates one) | ≥50% of active worktrees |
| `tests/test_*invariant*.py` regression | full test suite | 0 | 0 | 0 |

### 6.4 Rollback plan

- **Full rollback:** `git checkout main -- architecture/admission_severity.yaml architecture/capabilities.yaml scripts/topology_doctor*.py scripts/worktree_doctor.py .claude/hooks/registry.yaml .claude/hooks/dispatch.py AGENTS.md` from the pre-Phase-1 tag `pre-navigation-v2`. capabilities.yaml extension is pure append → revert via `git checkout`. worktree sentinels are file-only → unaffected by rollback.
- **Severity-flip rollback:** `ZEUS_ADMISSION_SEVERITY=off` env var reverts decision layer to uniform-error pre-flip behavior. dispatch.py reads this env at start.
- **Companion-loop-break rollback:** `ZEUS_COMPANION_LOOP_BREAK=off` env var disables K2 (`_apply_companion_loop_break` returns admission unchanged). Agents fall back to the old planning-lock dance.
- **Per-worktree sentinel rollback:** sentinels are read-only optional declarations; deleting them is a no-op for the hot decision path. Hot path never depends on sentinel presence.

### 6.5 Post-cutover stabilization

- Day 30: review severity_demoted ratio; if any issue code shows >50% demotion-rate-on-defect-misses, promote back to BLOCKING with structured evidence under `evidence/admission_blocking_promotions/<date>.md`.
- Day 60: review typed-intent adoption; if <50% canonical, write a `feedback_typed_intent_canonical_adoption.md` memory and surface to coordinator briefs.
- Day 90: quarterly CHARTER §5.1 review; sunset all `severity_when` entries that are 90 days old; demote operator-signed BLOCKING promotions whose evidence is older than 30 days.

---

## §7 Web research summary (sources cited)

1. [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks) — JSON contract for `permissionDecision`, `additionalContext`, exit codes, the 8 hook events (PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, Stop, SubagentStop, PreCompact, plus WorktreeCreate/WorktreeRemove). Authoritative for §2.9 SessionStart `additionalContext` envelope and §2.7 sentinel-vs-runtime-state distinction.
2. [Claude Code Hooks guide](https://code.claude.com/docs/en/hooks-guide) — concrete examples of safety checks, blocking dangerous bash, formatting, session-start context. Cited in §2.10 for the "deterministic guarantees, not autonomous side-effects" framing applied to advisory-only worktree hygiene.
3. [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices) — "Use hooks for actions that must happen every time with zero exceptions." Cited in §2.5 K1 severity tier for the principle that BLOCKING is reserved for operations whose silent failure is catastrophic; ADVISORY for everything else.
4. [Claude Code Worktrees](https://code.claude.com/docs/en/worktrees) — `--worktree` isolation pattern; `WorktreeCreate`/`WorktreeRemove` event hooks. Cited in §2.6 `worktree_create` capability owner_module; the cap composes with the harness-level worktree event but does not duplicate.
5. [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — orchestrator-workers pattern; "agent loop with checkpoints" framing. Cited in §2.5 K1 default-flip rationale ("the cheap path is the lighter pattern Anthropic's agents docs recommend") and §2.7 sentinel-as-context-not-state.
6. [git-worktree man page](https://git-scm.com/docs/git-worktree) — `git worktree add` semantics; isolated working directory; shared `.git` history. Cited in §2.6 `worktree_create.intent` and §2.7 sentinel placement (`.git/worktrees/<name>/zeus_worktree.yaml` colocated with git's own per-worktree metadata).
7. [git checkout silent revert behavior](https://git-scm.com/docs/git-checkout) — `git checkout <branch>` does NOT refuse when modified-tracked files would be silently reverted to the target branch's version (no merge conflict required). This is the precedent for the hook-redesign-shipped `pre_checkout_uncommitted_overlap`; Navigation v2 cites for completeness in §1.2.
8. [GitHub CLI — gh pr merge](https://cli.github.com/manual/gh_pr_merge) — `--squash`, `--rebase`, `--merge` modes; cited in §2.6 `worktree_post_merge_cleanup` capability owner_module's intent_test ("task is a successful gh pr merge").
9. [Anthropic — Claude Code documentation index](https://docs.claude.com/en/docs/claude-code) — top-level docs reference for the Claude Code agent runtime. Cited in §2.10 AGENTS.md updates for the typed-intent enum and worktree section.
10. [oh-my-claudecode harness reference](https://github.com/anthropics/oh-my-claudecode) — multi-agent orchestration patterns; agent tier routing; SessionStart context injection. Cited in §2.7 sentinel-as-context-not-state (orchestrator already tracks active sessions; sentinel is operator-supplied context only).

**Sources count: 10.**

Additionally cited (non-web; project-internal authority):
- `docs/operations/task_2026-05-06_topology_redesign/ultraplan/` — 6 files including `# ULTIMATE_DESIGN.md` §1 (5-layer architecture), §2.3 (reversibility classes), §3 (source tagging), §4 (route function), §10 (anti-pattern check); `# ANTI_DRIFT_CHARTER.md` §3-§7 (M1-M5 mechanisms)
- `docs/operations/task_2026-05-06_hook_redesign/PLAN.md` — single-PLAN style precedent; §2.6 `pre_checkout_uncommitted_overlap` (worktree-loss prevention shipped); §2.5 PR Monitor advisory pattern
- `evidence/hook_redesign_critic_opus.md` + `evidence/hook_redesign_critic_opus_final_v2.md` — critic methodology and adversarial attack patterns
- `~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/feedback_*.md` — 7 verified-recent memories cited inline (grep_gate_before_contract_lock, dispatch_brief_concise, critic_must_read_prior_remediations, frontload_predictable_remediation, default_dispatch_reviewers_per_phase, accumulate_changes_before_pr_open, commit_per_phase_or_lose_everything, orchestrator_offload_lookups, stash_recovery_verify_canonical_state)
- `architecture/capabilities.yaml`, `architecture/naming_conventions.yaml`, `architecture/script_manifest.yaml`, `architecture/test_topology.yaml`, `architecture/source_rationale.yaml` — verified file:line citations within 10 minutes
- `scripts/topology_doctor.py`, `scripts/topology_doctor_script_checks.py`, `scripts/topology_doctor_docs_checks.py`, `scripts/topology_doctor_digest.py`, `scripts/topology_doctor_policy_checks.py` — emitter source-of-truth, cited at file:line for every friction pattern
- Operator's draft proposal at `/Users/leofitz/Downloads/gitworktree.txt` (482 lines); §D branch-keepup matrix is the source for `worktree_doctor.py::_decision_matrix`

---

## §8 Phase summary table

| Phase | Hours | Deliverables | Exit criteria | Key risks |
|---|---|---|---|---|
| **1 — Stable layer + severity registry** | ~1.5h | `admission_severity.yaml` (150 LOC) · `capabilities.yaml` extension (+80 LOC, 16→21 entries) · 3 new tests (190 LOC) · `naming_conventions.yaml` annotation (+10 LOC) | All schema validators green · catalog_size=21 · file headers compliant · zero behavioral change for agents (parallel install) | H-R6 entry collision — mitigated by uniqueness assertions in schema tests |
| **2 — Decision-layer wiring + companion-loop-break** | ~2h | `topology_doctor*.py` extensions (+190 LOC) · 2 new tests (130 LOC) · F1-F5 reproducible-as-pass via integration test | All Phase-1 tests + 2 new green · F1-F5 integration smoke green · 14-day shadow telemetry tracking active · `[skip-invariant]` BASELINE_RATCHET commit | H-R1 silent-admit hides defect — mitigated by explicit BLOCKING list + telemetry signal; H-R2 typo mass create — mitigated by companion-must-be-in-files rule |
| **3 — Worktree lifecycle script + hygiene SessionStart hook** | ~1.5h | `worktree_doctor.py` (200 LOC) · `dispatch.py` extension (+30 LOC) · `registry.yaml` extension (+15 LOC) · 2 new tests (160 LOC) · `script_manifest.yaml` entry (+25 LOC) | All Phase-1+2 tests + 2 new green · `worktree_doctor.py` 4 subcommands smoke green · SessionStart hook ships in registry; advisory-only · `[skip-invariant]` BASELINE_RATCHET commit | H-R3 typed-intent enum mismatch — mitigated by graceful fallback; H-R4 SessionStart latency — mitigated by 500ms budget + parallel hook exec; H-R5 sentinel becomes mandatory — mitigated by INV-HELP-NOT-GATE test extension |

**Total runtime:** ~5h sonnet executor (3 phases, no fix-loop overhead).

**Total LOC:** ~1,180 LOC added (well under 1,500 preferred ceiling); ~0 LOC deleted (pure additions; severity-flip is behavioral, not deletion-driven).

**Sources cited:** 10 web + 8 project-internal authority surfaces.

**Structural decisions:** K1 (severity tier, default ADVISORY), K2 (companion-loop-break for create_new), K3 (typed-intent enum). 3 decisions for 5 friction patterns.

**Capability appends:** 5 new entries (worktree_create, worktree_branch_keepup, worktree_post_merge_cleanup, workspace_hygiene_audit, cross_worktree_visibility); 16 → 21 in `architecture/capabilities.yaml::metadata.catalog_size`.

**Composition (NOT duplication):**
- Hook redesign owns `pre_checkout_uncommitted_overlap` (worktree-loss BLOCKING) — Navigation v2 references, does not re-implement
- Hook redesign owns `post_merge_cleanup` PostToolUse hook — Navigation v2 adds the entry-point script (`worktree_doctor.py`) the hook can call
- Topology redesign owns `architecture/capabilities.yaml`, `reversibility.yaml`, route_function — Navigation v2 extends with 5 new appends, no schema changes
- Topology redesign owns `INV-HELP-NOT-GATE` — Navigation v2 extends the M5 test, does not duplicate
