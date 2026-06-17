# Workspace Routing — Fundamental Redesign (DESIGN OF RECORD)

<!-- Created: 2026-06-14 -->
<!-- Last reused or audited: 2026-06-14 -->
<!-- Authority basis: operator brief 2026-06-14 (workspace sprawl + context-management revolution); architecture/file_arrangement.yaml; .claude/hooks/{dispatch.py,registry.yaml}; scripts/{topology_doctor_docs_checks.py,zpkt.py,worktree_doctor.py}; Claude Code hooks doc (code.claude.com/docs/en/hooks). -->

**Status:** DESIGN APPROVED — implementation GATED (no code until operator authorizes a build step).
**This file dogfoods its own design:** it is a work-artifact, so it lives in its own by-work folder `docs/operations/current/workspace-routing-redesign/` with its `scope.yaml` sibling — exactly where the router (R20) would route it.

---

## 0. Thesis

Stop *telling* agents where files go and stop *auditing* where they went. Make the **Write tool itself put the file in the right place** at the moment of writing — by rewriting `file_path` via the PreToolUse `updatedInput` field — and delete every advisory/audit mechanism the rewrite makes redundant.

The causal layer moves from KNOWLEDGE (read a manifest, remember a rule → decays) to ACTION AFFORDANCE (the only place a write *can* land is the canonical place → cannot decay by non-use). This is the layer the only two non-decayed hooks (`cotenant_staging_guard`, `maintree_git_state_guard`) already operate on.

**Keystone empirical fact** (verified, Claude Code hooks doc): *"The tool result reflects the rewritten path. If you modify file_path in updatedInput for a Write tool, the tool executes on the modified path, and Claude sees the result from that modified path."* → the agent's own path model is corrected through the **reliable** channel (tool result), so silent routing needs **zero friction** and **no ASK** on the common path.

---

## 1. Diagnosis (what the repo actually shows)

Verified at HEAD (`b6900d76a8`, `live/iteration-2026-06-13`):

| Symptom | Measured |
|---|---|
| `docs/` total files | 534, 16 subdirs |
| `docs/operations/` loose root files | 142 |
| archive dirs (should be 1) | 3 — `docs/archive`(1), `docs/archives`(53), `docs/historical_evidence`(14) |
| plan homes (should be 1) | 2 — `docs/plans`(4) + `docs/operations/` |
| `.claude/worktrees/` | 17,297 files (gitignored, never pruned) |
| root loose tracked | `find_zombies.py`, `task.md`, `workspace_map.md` |
| `.omc/` | `wf_*.js`×4, `*.txt`, `.DS_Store`; `.omc/research/*.md` tracked despite `.omc/` gitignored |

**Already built, already correct, just not wired:** `file_arrangement.yaml` (policy, but `enforcement: advisory`, "never blocks" → skipped); `docs/operations/current/` (canonical tree exists); `scripts/zpkt.py` (routing tool); `scripts/topology_doctor_docs_checks.py` (H1–H8 audits, only run if an agent chooses to — it doesn't).

**Lesson:** policy/tree/tool are right. The single missing element is a force at the **write boundary in every agent context** that is cheap to obey, expensive to evade. Everything else is paid for. Advisory everything decayed; only the two `_BLOCK_SENTINEL` hooks survived — because they act at the action.

---

## 2. The help-vs-block scale (the heart)

Binary thinking was the trap: NUDGE (advisory → decays) vs HARD-STOP (block → obstructed-and-bypassed). There is a third strength that does neither:

```
  weak  NUDGE        SILENT-ROUTE          ASK*       HARD-STOP  strong
        (advisory)   (updatedInput +       (rare,     (deny/exit2)
                      tool-result path)    authority)
        ambiguous    ← THE COMMON PATH →    authority   corruption
                       (zero friction)       only
```

**SILENT-ROUTE** is the keystone: the write *succeeds, in the right place*, and the canonical path comes back through the tool result the agent always reads. Nothing to remember, obey, or route around — the path of least resistance and the correct path become the same path (R3 + R4 + R11 together, which binary mechanisms cannot do).

### Strengths
1. **SILENT-ROUTE** (common path, vast majority) — *computable home + non-authority*. Rewrite `file_path`→canonical; tool result reports it; one-line `additionalContext` is belt-and-suspenders, not load-bearing. Applies to **all** computable non-authority kinds **including plans and scripts** (no ASK — the tool-result channel keeps the path model correct without friction). High-precision scratch → `.omx/` under the R16 bar.
2. **NUDGE** — *ambiguous home OR unknown kind*. Lands as-asked; `additionalContext` names likely home(s) + the `zpkt` command. **Unknown kind always lands here, never silent-routed into an ignored dir** (R16 anti-data-loss). Zero friction (write succeeds).
3. **ASK** (`permissionDecision:"ask"`) — *the one surviving case: a NEW authority-surface file with no inferable home* (e.g. new `docs/authority/<topic>.md`, or a new operator-gated operations package). Survives because it is rare (no routine friction → no evasion training), guessing an authority home is worse than one prompt, and it matches the operator's existing operator-gate at exactly this boundary.
4. **HARD-STOP** (`deny`/exit 2) — only two corruption cases, never misplacement:
   - overwriting an existing canonical **authority** file with a non-authority artifact;
   - writing into `.claude/worktrees/<other-agent>/` from outside that worktree (cross-tenant).

**Decision rule:** *NEW Write only. Computable + non-authority → SILENT-ROUTE. Ambiguous / unknown → NUDGE (never bury). Ambiguous authority home → ASK. Deny only to prevent truth-clobber or cross-tenant write.*

### Why Write-only, never Edit/MultiEdit (R13)
Edit/MultiEdit require the agent to have Read the file at `P` and built `old_string` against it; rewriting `file_path`→canonical desyncs `old_string` → the edit fails or corrupts the canonical file. An Edit is by definition a write to a file that already exists → it already has a home; relocation is the one-time migration's job, not the per-write router's. The routing rewrite registers on the **`Write` tool only**; the absorbed authority/capability checks keep their `Edit|Write|MultiEdit|NotebookEdit` coverage for their own purposes.

---

## 3. The ultimate file classification (R21) — the classifier contract

This taxonomy is the contract `route_write` consumes and the schema-drift antibody (§7) pins.

| # | Class | Detection | Canonical home | Authority | Lifecycle | Router strength |
|---|---|---|---|---|---|---|
| 1 | operation_plan | `*PLAN*.md` | `current/<work>/PLAN.md` | yes | per-mission→archived | SILENT (ambig work→NUDGE) |
| 2 | operation_scope | `scope.yaml` | `current/<work>/scope.yaml` | yes | per-mission→archived | SILENT |
| 3 | operation_evidence | `*_EVIDENCE*`, `evidence/` | `current/<work>/evidence/<file>` | no | per-mission→archived | SILENT |
| 4 | operation_report | `*_REPORT*`, `report.md` | `current/<work>/report.md` | no (self-declare) | per-mission→archived | SILENT |
| 5 | operation_closeout | `closeout*` | `current/<work>/closeout.md` | no (triggers lifecycle) | per-mission→archived | SILENT + lifecycle NUDGE |
| 6 | agent_runtime_capsule | capsule/handoff md | `current/<work>/agent_runtime/<ts>_<slug>.md` | no | per-mission→archived | SILENT |
| 7 | active_task_ledger | `task.md` in work | `current/<work>/task.md` | no | per-mission→archived | SILENT |
| 8 | authority_law | `docs/authority/` | `docs/authority/<topic>.md` | yes | permanent | ASK if new+ambiguous; else SILENT; HARD-STOP on clobber |
| 9 | durable_reference | `docs/reference/` | `docs/reference/<topic>.md` | durable | permanent | NUDGE if topic uninferable; else SILENT |
| 10 | known_gap | `known_gaps.md` | `docs/to-do-list/known_gaps.md` | no | permanent | SILENT |
| 11 | current_state pointer | `current_state.md` | `docs/operations/current_state.md` | yes | permanent | HARD-STOP on duplicate; else not-routed |
| 12 | source module | `src/` | `src/<module>/...` | code | permanent | NOT router-governed |
| 13 | tool/script | `scripts/`, root one-off `.py` | `scripts/<name>.py` (+provenance hdr) | no | permanent OR disposable | NUDGE (real-vs-throwaway ambiguous → never bury) |
| 14 | test | `tests/test_*.py` | `tests/...` (+provenance hdr) | no | permanent | NOT router-governed |
| 15 | architecture manifest | `architecture/*.yaml` | `architecture/<name>.yaml` | yes | permanent | ASK/NUDGE new; existing edits via absorbed pre_edit_architecture |
| 16 | hook/governance config | `registry.yaml`, `settings.json` | in place | yes | permanent | NOT router-governed (boot self-test) |
| 17 | runtime_scratch | `wf_*.js`, `*_scratch*`, `*.tmp` | `.omx/` (gitignored) | no | disposable | SILENT only at R16 high-precision bar; else NUDGE |
| 18 | db-backed state | `state/` | `state/...` | no | disposable | NOT router-governed |
| 19 | omc scratch | `.omc/` | `.omc/...` (gitignored, Option A) | no | disposable | NOT router-governed |
| 20 | root allow-listed | exact-name on allow-list | repo root | varies | permanent | NOT router-governed |
| 21 | UNKNOWN | matches nothing | lands as-asked | unknown | unknown | NUDGE — never SILENT, never `.omx` |

**Root allow-list principle:** a file may live at repo root **only if a tool requires it there**. `workspace_map.md` KEEP (referenced by topology.yaml / topology_doctor / map_maintenance); `find_zombies.py`, `task.md` leave (§8).

---

## 4. By-work structure + lifecycle (R20)

**Organizing unit = the named work folder, not by-kind dispersion.** Every mission lives in ONE self-contained folder holding all its artifacts together:

```
docs/operations/current/<work-name>/
  PLAN.md          ← the plan (authority)
  scope.yaml       ← what this work may touch (authority)
  evidence/        ← this work's evidence
  report.md        ← this work's report (non-authority)
  closeout.md      ← closeout (writing it triggers lifecycle)
  agent_runtime/   ← capsules/handoffs
  task.md          ← this work's ledger
```

`current/` means **only the active mission(s)** — literal. It is not a by-kind accretion bucket and not where finished work piles up (that backlog dumping is *why* 142 files went loose). B2 gets strictly stronger: opening one work folder shows the whole work adjacent — plan, scope, evidence, report — with zero injection.

**Lifecycle — 2-stage (operator-decided):** `current/<work>/` (active) → `docs/archive/<work>/` (done). Writing `closeout.md` emits a NUDGE to run `zpkt close <work>`, which performs the operator-gated, reference-checked `git mv current/<work>/ → archive/<work>/` (R10/R17 — never auto-bulldoze; runtime-reader scan before move). `current/` stays small by construction.

**Work-name resolution (the hard part — §3a):** route_write infers WHICH work folder a work-artifact write belongs to, by priority:
1. **explicit** — `<work-name>` already present in the write path → use it.
2. **exact-slug-match** — filename slug exactly matches an existing `current/<work>/` → use it.
3. **single-active default** — exactly one active work named in `current_state.md`/`package.yaml` → route there.
4. **unique session context** — the session is unambiguously scoped to one work → use it.
5. **fail → NUDGE** — zero or multiple candidate works, or no exact match → NUDGE naming the candidates + `zpkt`. **Never silently misfile into the wrong work.**

**Proposed `file_arrangement.yaml` rewrite:** the `artifact_kinds` canonical paths change from by-kind (`current/plans/<slug>/PLAN.md`, `current/evidence/<slug>/`, …) to by-work (`current/<work>/PLAN.md`, `current/<work>/evidence/`, …). `legacy_paths` retain the old by-kind + `task_*/` layouts so the migration recognizes them. Authority flags preserved. (Proposal only — the file is not edited until build step S0.)

---

## 5. The collapse — net mechanism count DECREASES (R6)

**DELETED (6):**
1. `pre_edit_architecture` (advisory hook) → folded into route_write.
2. `pre_write_capability_gate` (advisory hook) → folded into route_write.
3. `file_arrangement.yaml` **enforcement clause** → becomes a pure data table consumed by route_write (policy data stays — R12; only the dead "enforcement" concept dies).
4. `topology_doctor --navigation` placement-audit invocation → routing makes placement correct at write-time; nothing to audit.
5. critic-agent **placement-review** responsibility → structurally always correct.
6. scattered ad-hoc docs-root checks → route_write is the single chokepoint; H5–H8 demote to a once-per-PR CI backstop.

**ADDED (1):** `route_write` — one handler in the existing `dispatch.py`, on a `Write`-only PreToolUse matcher.

**Net:** −6 conceptual mechanisms (2 live hooks), +1. Edit/Write-line handlers go 3→2. `topology_doctor_docs_checks.py` not deleted but **demoted** to CI backstop for the residue routing can't see (raw `mkdir`/`>` outside the Write tool). R6 satisfied: count strictly decreases. R20 adds nothing — it repoints existing data + `zpkt`.

---

## 6. The mechanism — `route_write`

One handler `_run_advisory_check_route_write(payload)` in `dispatch.py`, on a **`Write`-only** PreToolUse matcher (split from the combined Edit/Write line). No new event, process, or framework.

```
input: tool_input.file_path = P, content   (Write tool ONLY — R13)
1. if P already EXISTS on disk → return None (overwrite of an already-homed file; only the
   truth-clobber HARD-STOP inspects it). Makes the router idempotent.
2. resolve P repo-relative; if outside repo / inside .omx/ / already AT canonical → None (fast no-op).
3. classify(P, content) → class  (R21 taxonomy: filename + content + path signals; else UNKNOWN).
4. if class is a work-artifact → resolve work-name (§4 resolver; fail → NUDGE).
5. canonical = canonical_path(class, work-name, slug).
6. apply the scale (§2):
     computable + non-authority   → SILENT-ROUTE (updatedInput.file_path = canonical)
     ambiguous / UNKNOWN          → NUDGE (land as-asked; NEVER .omx — R16)
     ambiguous authority home     → ASK
     authority-clobber|cross-tenant → HARD-STOP (exit 2)
7. fail-open on ANY exception (charter): a crash never blocks a write.
```

**R16 precision bar (anti-data-loss):** silent-route into `.omx/` fires ONLY on a closed allow-list of unambiguous scratch shapes (`wf_*.js`, `*_scratch*`, `*.tmp`, known one-off prefixes) with consistent extension/content. A real-looking root `.py`/`.json`/`.md` lacking a home is AMBIGUOUS → NUDGE, never buried. UNKNOWN can never reach SILENT-ROUTE. *When in doubt, make it visible, never hide it.*

**Why the right causal layer (R2/R5):** fires on the tool → reaches **subagents** (PreToolUse fires there; UserPromptSubmit does not). The 142 loose files arrived via subagent writes that never saw `file_arrangement.yaml`; this catches all of them. It is not knowledge — the agent need not read, remember, or run anything.

---

## 7. Adoption + durability (the operator's core doubt)

### Why this does NOT repeat the codegraph/topology ~0-use failure (D2)
| | codegraph / topology | route_write |
|---|---|---|
| How it runs | agent must **choose to call** | fires **automatically** on every Write |
| Requires awareness | YES (remember + decide) | NO |
| Failure mode | "forgot / didn't bother" → decays | no call site to omit |
| Layer | knowledge (remember→choose) | action (the Write triggers it) |

**You cannot fail to adopt a mechanism that requires no action from you** — same reason the two surviving blocking hooks never decayed. Residual non-use vectors each terminate in a RED test or boot warning (see table).

### Durability — every decay vector → a failing test or self-correcting fallback (D3)
| # | Vector | Defense | Test / fallback |
|---|---|---|---|
| 1 | `updatedInput` API drift | PostToolUse verifies rewrite took; mismatch → NUDGE; CI backstop floor | self-correct + RED antibody |
| 2 | Bash-write bypass (`echo>f`,`mkdir`,heredoc) | demoted H5–H8 CI backstop on committed residue; optional PreToolUse(Bash) NUDGE | self-correct **at commit only** — honestly not closed in-session |
| 3 | Classification rot | UNKNOWN→NUDGE (visible, never buried); known-kind antibody | RED test + safe default |
| 4 | Migration breaks runtime reader | runtime-reader scan + `lsof`/daemon re-probe + drain/repoint, atomic, gated | verified-before-merge gate |
| 5 | Authority-ASK dismissed | lands visible; CI backstop flags authority misplacement; rare by construction | self-correct at commit |
| 6 | Router deleted | BLOCKING-tier in registry.yaml + antibody (wrong-path Write reroutes; Edit never reroutes) | RED test |
| 7 | Router silent no-ops (fail-open hides break) | boot self-test asserts handler resolves + SessionStart smoke | RED test + boot warn |
| 8 | `file_arrangement.yaml` schema drift | antibody asserts fixed (class→canonical) mappings resolve | RED test |
| 9 | Worktree prune races live agent | clean+merged+no-process+age gate; never removes dirty/attached | safe-by-gate |
| 10 | Work mis-resolution (concurrent works) | resolver fails → NUDGE; antibody on multiple-active | RED test + safe default |
| 11 | Lifecycle move breaks reference | `zpkt close` operator-gated + reference-checked (not auto) | verified gate |

**Honest gap:** vector #2 (Bash-writes) is closed only at commit by the CI backstop, not in-session. Not overclaimed. Everything else terminates in a red test or a verified gate → meets the "lasts a month without redesign" bar.

---

## 8. The backlog — concrete disposition (D4)

The router prevents *recurrence*; it does not clean the existing pile. No item is bulldozed: committed files move only via reference-checked `git mv`; dead files get a provenance verdict.

| Class | Count | Disposition | Vector | Gate |
|---|---|---|---|---|
| `docs/operations/` loose | 142 | classify → `git mv` into `current/<work>/` (or `archive/<work>/` if done); dead → delete w/ verdict | one-time migration | operator-gated, ref+runtime scan; sub-batch by kind |
| `docs/archives/`(53)+`historical_evidence/`(14) | 67 | merge into single `docs/archive/` | one-time migration | operator-gated, ref-checked |
| `docs/plans/` | 4 | `git mv` into `current/<work>/` or `archive/` | one-time migration | operator-gated |
| `find_zombies.py` (root) | 1 | **DEAD_DELETE** — zero refs, throwaway ast scanner | delete | operator confirm |
| `task.md` (root) | 1 | **STALE_DELETE** — pointer stub; ledger already moved | delete | grep `"/task.md"` first |
| `workspace_map.md` (root) | 1 | **CURRENT_REUSABLE — KEEP IN PLACE** — load-bearing, referenced by topology.yaml/topology_doctor/map_maintenance | keep | none |
| `station_migration_alerts.json` (root, untracked) | 1 | delete stale root copy (canonical writer target is `state/`) | delete | none (untracked) |
| `.omc/` clutter | ~6 | Option A → pure scratch, already disposable; optional `rm` | router (future) + manual | none |
| `.omc/research/*.md` (tracked) | 1 | **Option A (DECIDED)** — relocate durable content to `docs/reference/`, then `git rm --cached` | untrack | operator (decided) |
| `.claude/worktrees/` | 17,297 | prune stale; fix remove-flow | worktree prune (§9) | auto for clean+merged; never dirty/attached |

**Sequence the operator actually runs:** (1) worktree prune (independent, biggest visible win, lowest risk) → (2) untrack/delete dead-or-stale singletons → (3) the big by-work migrations (gated, ref+runtime-scanned, one sub-batch at a time) → (4) router already live, preventing re-formation.

---

## 9. Worktree explosion — distinct mechanism (R18)

17,297 gitignored files; the router can't touch it (it's `EnterWorktree`/`git worktree add`, not Write). Reuse `scripts/worktree_doctor.py` + the two existing worktree hooks (no new mechanism):
1. **Prune** — `worktree_doctor --prune`: STALE when ALL of [branch merged into origin/main OR session-branch gone] ∧ [working tree clean] ∧ [no live process cwd/handle inside] ∧ [older than threshold] → `git worktree remove`. **Never removes dirty or process-attached trees.** Run from the existing SessionStart sweep + manual `--prune` for the backlog.
2. **Stop re-accumulation** — on `WorktreeRemove`/`post_merge_cleanup`, when a worktree's branch is merged + clean, **auto-`git worktree remove`** instead of printing a checklist. Clean-merged removal loses nothing; dirty/unmerged stay + get the advisory. Same help-not-block move as the router. Independent of route_write — shippable first.

---

## 10. `.omc/` leak — Option A (DECIDED, R19)

`.gitignore` has `.omc/` but `.omc/research/zeus_gate_removal_list_2026-06-13.md` is tracked (half-versioned). **Option A (operator-locked):** `.omc/` is pure scratch. If the tracked file holds anything durable, relocate it to `docs/reference/<topic>.md` first; then `git rm --cached` it, leaving `.omc/` uniformly gitignored. Matches the scratch-never-committed law + no-shadow preference.

---

## 11. Candidates considered
- **A — CI/pre-commit block:** fires too late (after 30 wrong writes), obstructs, pure-block → bypassed, misses never-committed scratch. **Rejected.**
- **B — better advisory + CLAUDE.md + SessionStart audit:** the falsified knowledge-layer approach (~0 organic use across 11 sessions). **Rejected.**
- **C (CHOSEN) — Write-router with `updatedInput`, zero-friction silent-route, ASK only for ambiguous authority, audit demoted to CI backstop.** Only candidate satisfying R3+R4 together. Paired with the worktree mechanism (§9) and the migration (§8) it is the full design — the router alone is necessary-not-sufficient (R15).

---

## 12. Implementation gate — steps spec'd, SEQUENCE TBD (operator decides)
Independently shippable + reversible units; order open.
- **S0** — repoint `file_arrangement.yaml` (by-work canonical paths, legacy retained) + `zpkt` to the by-work shape. Precedes any routing/migration.
- **S1** — `route_write` handler on a Write-only matcher + antibody tests (reroute-new-write / never-reroute-Edit); SILENT-ROUTE for high-precision scratch only first (smallest blast radius). Register BLOCKING-tier.
- **S2** — widen SILENT-ROUTE to all computable non-authority kinds (plans/scripts/evidence/report/closeout); UNKNOWN→NUDGE; authority→ASK.
- **S3** — fold `pre_edit_architecture` + `pre_write_capability_gate` into route_write's authority/capability checks (keep their Edit-line coverage).
- **S4** — worktree mechanism (§9): `worktree_doctor --prune` + clean-merged auto-remove. *Independent — can ship first if the 17K mess is the priority.*
- **S5** — `.omc/` Option A untrack (§10).
- **S6** — one-at-a-time reference-checked **by-work** migration (§8): 142 loose + archives + plans regroup INTO `current/<work>/` or `archive/<work>/`; runtime-reader scan + live-daemon drain/repoint; treat as a live DB migration.
- **S7** — flip `file_arrangement.yaml` enforcement clause to data-only; demote topology H5–H8 to CI backstop.

S4 and S5 address the visible backlog independently; S0–S3 + S7 are the recurrence-killer; S6 is highest-risk, gated last. **No code until the operator authorizes a specific step.**
