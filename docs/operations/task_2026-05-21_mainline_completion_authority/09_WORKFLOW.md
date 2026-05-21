# Per-Phase Orchestration Workflow

Authority for orchestrator behavior: `~/.claude/skills/orchestrator-delivery/SKILL.md`. This file applies the universal skill to Zeus mainline phase work.

## The per-phase loop

```
1. Read authority (chain row 1-2 verbatim; row 5 fresh)
2. Planner dispatch (opus or sonnet per phase scope)
   ├── input: phase ENUM slot + dossier intent + current main state
   ├── output: per-phase ultraplan with ≤4 tracks, sized 100-500 LOC each
3. Plan opus critic (always)
   ├── input: planner output + dossier-cited authority
   ├── output: APPROVE / REVISE with specific gap citations
4. Per-track SCAFFOLD dispatch (sonnet executor)
   ├── input: track contract (verbatim authority cites + 3-class grep + expected outputs)
   ├── output: architectural skeleton (types + signatures + migration outline + test names)
5. Wave SCAFFOLD critic (opus) ← MANDATORY per scaffold_critic_dispatch rule, no skip
   ├── input: ALL track scaffolds + cross-track invariants
   ├── output: SCAFFOLD_CRITIC_PASS or SCAFFOLD_CRITIC_FIX_REQUIRED
6. Per-track production dispatch (sonnet executor, can resume scaffold agent)
   ├── input: SCAFFOLD-approved skeleton + relationship test names
   ├── output: production code + relationship tests + PR opened
7. PR fix-loop (executor self-polls silently)
   ├── input: PR number + worktree path
   ├── executor: reads bot comments via gh CLI, fixes, commits, resolves threads, monitors CI silently
   ├── output: ONE terminal message "PR #N merge-ready sha=X threads=N/N CI=green age=Ns bots=copilot+codex"
8. Orchestrator merges on terminal emission (no re-verify)
   ├── gh pr merge --squash --delete-branch
   ├── tag phase<N>_track<M>_landed
9. (After all tracks land) Wave closure verifier (opus)
   ├── input: all merged tracks + cross-track invariants
   ├── output: PASS → push phase<N>_landed umbrella tag; FAIL → remediation dispatch
10. Surface 4 (Evidence Ladder) and 7 (Settlement) update registry/yaml
    ├── architecture/strategy_profile_registry.yaml extended
    ├── architecture/settlement_dual_source_truth_2026_05_07.yaml extended
```

## Model tier routing (Zeus-specific overlay)

| Task | Tier | Reason |
|---|---|---|
| File/directory locate, line:column lookup | **haiku** | 1M context, ~$0.001/call. Audit only — never destructive (`rm`, `git worktree remove`, schema migration). |
| Per-PR bot-comment classify + fix dispatch | **sonnet** | Executor reads PR directly; no orchestrator-side relay. |
| Per-track SCAFFOLD execution | **sonnet** | Implementation per phase contract; no architectural decisions inside phase. |
| Per-track production | **sonnet** | Same. |
| Verifier on standard surface | **sonnet** | Regression re-runs with pass/fail report. |
| Per-track SCAFFOLD critic | **sonnet** | Per ultraplan §L: SCAFFOLD critic is sonnet-tier (cross-module semantic reasoning on architectural skeleton). Opus reserved for wave critics. |
| Wave-level cross-track critic | **opus** | Cross-PR coherence audit; one per wave per ultraplan §L. Budgeted: 9 opus total for plan-lock + wave + closure. |
| Math defect / calibration / Kelly architecture | **opus** | High architectural load. |
| K0 LIVE BOUNDARY phase critic | **opus** | Phases 6 (promotion gate) + 7 (settlement type-gate) are K0 — failures cost real capital. |
| Closure verifier (wave end) | **opus** | Final live-money truth gate per Phase 0 §I precedent. |

## Authority verbatim cite (mandatory per `feedback_dispatch_brief_cite_authority_verbatim_not_paraphrase`)

Every dispatch brief MUST include the authority verbatim, not paraphrased. Pattern:

```
**Authority**:
1. PRIMARY (operator-blessed plan): `<path>` §<N>.<M> verbatim:
   ```
   <copy-paste verbatim text>
   ```
2. SUBSTANTIVE INTENT (operator dossier): `<path>` §<N> verbatim:
   ```
   <copy-paste verbatim text>
   ```
3. CURRENT CODE STATE: re-grep `git show origin/main:<path>` before edit; 10-min staleness window.
```

Paraphrasing introduces translation loss. The agent will faithfully implement the divergence.

## PR fix-loop discipline (mandatory per `feedback_executor_pr_monitor_silent_terminal_only`)

The dispatched executor owns the entire PR fix-loop. ONE terminal message: `"PR #N merge-ready sha=<sha> threads=N/N CI=green age=<s>s bots=copilot+codex"`. Conditions:

- `mergeable=MERGEABLE` + `mergeStateStatus=CLEAN`
- `reviewThreads.resolved == reviewThreads.total`
- All required CI checks pass
- PR age ≥ 600s (Zeus pre-merge gate)
- BOTH Copilot + Codex auto-reviewers have fired (verify via `gh api repos/.../comments --jq '[.[].user.login] | unique'`)

NO "Holding" / "fix commit pushed" / "still waiting for CI" emissions. Orchestrator merges on terminal emission WITHOUT re-reading the same gh endpoints. Re-reading same endpoints across two agents IS the waste this rule prevents.

## Cross-phase invariant ledger

`docs/operations/task_<phase>_*/INVARIANTS.jsonl` (append-only, flock-guarded). Each phase appends invariants newly asserted by the phase, formatted as:

```json
{"asserted_at": "<iso>", "kind": "invariant", "id": "INV-shoulder-cluster-cap", "rule": "<text>", "asserted_by": "<phase>", "evidence": "<file:line or query>"}
```

Phase N's wave-critic reads phases 0..N-1 invariants and checks the new phase doesn't violate them.

## Schema bump procedure (Zeus-specific per `feedback_schema_bump_requires_explicit_world_db_migration`)

When a phase bumps `SCHEMA_VERSION` or `SCHEMA_FORECASTS_VERSION`:

1. **Code merge** (PR closes).
2. **Daemon restart** (`launchctl kickstart -k gui/$(id -u)/com.zeus.live-trading`). Verify via `boot_sha=` line in log.
3. **Explicit world DB migration** — MANDATORY:
   ```python
   from src.state.db import init_schema, get_world_connection
   c = get_world_connection()
   init_schema(c)
   c.commit()
   ```
   `init_schema(conn=None)` does NOT persist; pass explicit connection + `.commit()`.

Verify: `sqlite3 state/zeus-world.db "PRAGMA user_version;"` equals new SCHEMA_VERSION.

## P0 live-money discipline (per `feedback_p0_live_money_merge_must_be_single_purpose`)

P0 live-capital structural change (sizing, family/portfolio, fail-closed gates) ships as DEDICATED single-purpose PR. Title format: `fix(live)-P0-<short-tag>: <one-line gate description>`. Annotated tag `p0_<n>_landed` on merge sha. NEVER bundle with unrelated work.

## NOT in scope for orchestrator

- Live daemon operations (restart, schema migrate, hot-fix reconciliation) — operator's domain except for the single explicit migration step above.
- Worktree cleanup (`git worktree remove` is destructive — never delegated to haiku).
- Editing plugin cache paths (clobbered on update).

## Cancellation

After Phase N closure with operator approval:
- `/oh-my-claudecode:cancel` cleans up autopilot / ralph / ultrawork state.
- Mark `MEMORY.md` index entries as `[completed]`.
- Push umbrella tag `phase<N>_landed`.
- Write closure doc.

If session ends mid-phase: autopilot state persists (`active=false` until resume). Next session reads phase-package files to recover scope.
