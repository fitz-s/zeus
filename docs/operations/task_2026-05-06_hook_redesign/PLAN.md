# Hook System Redesign — Single PLAN

## §0 Sunset & meta

Sunset: 2026-08-06. Auto-demote to `docs/operations/historical/` if Phase 1 has not started. Charter rules in `.claude/CLAUDE.md` and AGENTS.md root §4 Planning lock apply.

This is a single-document redesign because the hook surface is small (1,337 LOC across 8 scripts plus `.claude/settings.json`) and the design is bounded. Mirror the rigor of `docs/operations/task_2026-05-06_topology_redesign/ultraplan/` — not its ceremony.

Scope: Zeus repo `.claude/hooks/` + `.claude/settings.json` + `.git/hooks/pre-commit` + the agent operating envelope around git/PR. Out of scope: source-tree gates that the topology redesign owns (Gates 1-5 of `ULTIMATE_DESIGN §5`); this PLAN composes with that work, it does not duplicate it.

---

## §1 Problem

### 1.1 Verified evidence (re-measured 2026-05-06)

| Metric | Briefing claim | Verified | Note |
|---|---|---|---|
| `[skip-invariant]` commits, last 60d | 161 | **187** | `git log --all --grep="skip-invariant" --since="60 days ago" --oneline \| wc -l` |
| `[skip-invariant]` commits, last 30d | — | **187** | every one is in the last 35 days; rate accelerating, not decaying |
| Daily bypass rate | ~2.7/day | **~3.1/day** | 187/60 |
| Hooks LOC | — | 1,337 | `wc -l .claude/hooks/*.{sh,py}` |
| Hook scripts | — | 8 (7 sh + 1 py shared) | `ls .claude/hooks/` |
| Documented escape hatches in `pre-commit-invariant-test.sh` | — | **3** (marker, sentinel file, env var) + 1 file-based skip sentinel = 4 | lines 79-126 of that script |
| Today's loss event | — | ~$190 | uncommitted work on `topology-redesign-2026-05-06` reverted by silent branch checkout, recovered via stash forensics by operator |

The bypass rate has **not** declined under the existing "documented escape hatches" framework. Codifying 3 ways to skip a gate did not reduce skip count — it normalized it.

### 1.2 The four problem categories

1. **Bypass-as-default culture.** `[skip-invariant]` appears in 187 of last 60d's commits — many are legitimate (origin/main baseline regressed, calibration cherry-picks crossing baseline counters, recovery commits documenting state) and some are convenience. The hook script itself **lists three skip mechanisms in priority order in its own comments** (`pre-commit-invariant-test.sh` lines 79-126), implicitly endorsing bypass as a routine workflow rather than an emergency. Ratchet: opt-in helper → standard workflow → 禁书 (anti-help-inflation pattern from the topology redesign's CHARTER).

2. **PR auto-review timing.** `gh pr create` triggers Copilot + Codex within 5-8 min. Each push to an open PR re-triggers them. Per `feedback_accumulate_changes_before_pr_open.md` (2 days old), agents do not currently know to:
   - accumulate ≥2 commits worth of work before `gh pr create`,
   - batch pushes,
   - self-monitor for review comments after open,
   - resolve review feedback without operator nudging.
   Result: paid auto-review burned per-push; operator becomes the polling loop ("did Copilot weigh in yet?"). The harness has no SubagentStop / Stop / Monitor wiring around PR open.

3. **Worktree / checkout safety.** Today (2026-05-06): coordinator was holding ~$190 of working-tree edits across Phases 0.D / 1 / 2 / 3 / 4.A / 4.B of topology redesign with **zero commits**. An external process (calibration commit pipeline) ran `git checkout main` then `git checkout live-launch-blockers-2026-05-06`. Every modified-tracked file silently reverted; staged-deletions vanished. Stash `e355af51` survived only because git auto-stashed; recovery required operator-mediated forensic diagnosis (per `feedback_stash_recovery_verify_canonical_state.md`). Subsequently, the recovery executor restored the wrong copy of `capabilities.yaml` (6 entries instead of canonical 16) from `stash@{0}^2` working-tree instead of `stash@{0}^3` untracked-tree — a regression that ran undetected through 8 commits and Phase 5.A. **The harness has no per-phase commit gate, no pre-checkout overlap detector, and no canonical-state verifier on stash restore.**

4. **State-of-the-art drift.** Existing hooks were written 2026-04-27 → 2026-05-06; the Claude Code hook system itself shipped major capabilities in this window: `permissionDecision` JSON envelopes, `additionalContext` injection, `WorktreeCreate`/`WorktreeRemove` event hooks, `SubagentStop` agentId enrichment. Existing Zeus hooks use the older "exit 2 to block / exit 0 to allow" contract exclusively (verified across all 7 hooks). They emit no structured JSON and contribute no `additionalContext`. Source: [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks).

### 1.3 Why these are one problem, not four

All four are symptoms of one structural failure: **the hook layer is a blocking gate without telemetry, without escalation classes, and without lifecycle awareness.** Each gate is a binary "block now / allow now" with a documented escape hatch. There is no record of how often it fired, no severity tier (`ON_CHAIN` vs `WORKING` from topology redesign §2.3), no integration with the agent's task loop (PR open → monitor → resolve), no awareness of session boundaries (start of multi-phase task → require commit-per-phase). Fix the architecture; the four symptoms collapse.

---

## §2 Design

### 2.1 Layered architecture

```
┌────────────────────────────────────────────────────────────────┐
│ STABLE LAYER · YAML, sunset 12mo                               │
│   .claude/hooks/registry.yaml — single source for hooks/gates  │
│   .claude/hooks/overrides.yaml — structured override catalog   │
└────────────────────────┬───────────────────────────────────────┘
                         │ schema reads at hook start
┌────────────────────────▼───────────────────────────────────────┐
│ DISPATCH LAYER · Python module                                 │
│   .claude/hooks/dispatch.py — single entry point per event     │
│   replaces 7 individual shell scripts                          │
│   reads registry.yaml; emits ritual_signal; returns structured │
│   permissionDecision JSON                                      │
└────────────────────────┬───────────────────────────────────────┘
                         │ JSON contract per Claude Code Hooks
┌────────────────────────▼───────────────────────────────────────┐
│ EVENT LAYER · .claude/settings.json                            │
│   PreToolUse / PostToolUse / SessionStart / Stop /             │
│   SubagentStop / PreCompact / WorktreeCreate / WorktreeRemove  │
└────────────────────────┬───────────────────────────────────────┘
                         │ JSON stdin per event
┌────────────────────────▼───────────────────────────────────────┐
│ TELEMETRY LAYER · jsonl logs                                   │
│   .claude/logs/hook_signal/<YYYY-MM>.jsonl                     │
│   one line per fire (advisory or blocking)                     │
│   schema: {hook_id, event, decision, reason, override_id?,     │
│             session_id, agent_id?, ts}                         │
└────────────────────────────────────────────────────────────────┘
```

This is the topology redesign's 5-layer architecture transplanted to the hook surface. `registry.yaml` is the analogue of `capabilities.yaml`. `dispatch.py` is the analogue of `route_function.py`. The structured `permissionDecision` is the analogue of the Write-tool capability gate.

### 2.2 `registry.yaml` schema (new file)

```yaml
schema_version: 1
metadata:
  charter_version: 1.0.0
  catalog_size: 8                          # current hook count
  sunset_default: 90d                      # operational rules

hooks:
  - id: invariant_test
    event: PreToolUse
    matcher: Bash
    intent: >
      Run pytest baseline before `git commit` to catch regressions.
    blocked_when:
      - regression_below_baseline
    severity: BLOCKING                     # advisory | blocking
    reversibility_class: TRUTH_REWRITE     # mirror of topology §2.3
    bypass_policy:
      class: structured_override           # not free-form
      override_ids: [BASELINE_RATCHET, MAIN_REGRESSION, COTENANT_SHIM]
      max_active_per_30d: 5                # per override_id
      requires_evidence_file: true
    sunset_date: 2026-08-06
    telemetry:
      ritual_signal_emitted: true
    owner_module: src/architecture/hooks/invariant_test.py

  - id: secrets_scan
    event: PreToolUse
    matcher: Bash
    intent: gitleaks against staged content for `git commit`.
    severity: BLOCKING
    reversibility_class: ARCHIVE
    bypass_policy:
      class: structured_override
      override_ids: [REVIEW_SAFE_TAG, OPERATOR_CLEARED]
      requires_evidence_file: true
    sunset_date: 2026-08-06

  - id: cotenant_staging_guard
    event: PreToolUse
    matcher: Bash
    intent: Block broad `git add` in main worktree (co-tenant absorption).
    severity: BLOCKING
    reversibility_class: WORKING
    bypass_policy:
      class: structured_override
      override_ids: [SOLO_AGENT, ISOLATED_WORKTREE]
    sunset_date: 2026-08-06

  - id: pre_checkout_uncommitted_overlap         # NEW (Worktree-loss prevention)
    event: PreToolUse
    matcher: Bash
    intent: >
      Refuse `git checkout <branch>` / `git switch <branch>` when the
      working tree has tracked modifications that overlap with the
      target branch's tree (silent revert risk).
    severity: BLOCKING
    reversibility_class: TRUTH_REWRITE
    bypass_policy:
      class: structured_override
      override_ids: [STASH_FIRST_VERIFIED, OPERATOR_DESTRUCTIVE]
      requires_evidence_file: true
    sunset_date: 2026-08-06

  - id: pr_create_loc_accumulation               # NEW (LOC-accumulate-before-PR)
    event: PreToolUse
    matcher: Bash
    intent: >
      Advise (not block) when `gh pr create` is invoked with <X commits
      or <Y LOC since branch base — accumulating reduces paid auto-reviews.
    severity: ADVISORY                            # advisory only
    reversibility_class: WORKING
    bypass_policy:
      class: not_required                         # advisory; agent decides
    sunset_date: 2026-08-06

  - id: pr_open_monitor_arm                      # NEW (PR auto-review monitoring)
    event: PostToolUse
    matcher: Bash
    intent: >
      After successful `gh pr create` or `gh pr ready`, emit
      additionalContext instructing the agent to arm a Monitor on
      `gh pr checks --watch --interval=30` + a 30s poll on
      `gh pr view --json reviews,comments`.
    severity: ADVISORY
    sunset_date: 2026-08-06

  - id: phase_close_commit_required              # NEW (Per-phase commit)
    event: SubagentStop
    matcher: "*"
    intent: >
      When a phase-class subagent (planner/critic/executor with
      role_phase=*) returns and the working tree has tracked changes
      not yet committed, emit additionalContext + a soft block.
    severity: ADVISORY                            # soft; PR-loss is the heavier gate
    sunset_date: 2026-08-06

  - id: pre_merge_contamination
    event: PreToolUse
    matcher: Bash
    intent: Conflict-first guidance + MERGE_AUDIT_EVIDENCE validation on protected branches.
    severity: BLOCKING                            # only when evidence requested but invalid
    reversibility_class: TRUTH_REWRITE
    bypass_policy:
      class: structured_override
      override_ids: [OPERATOR_OVERRIDE]
    sunset_date: 2026-08-06

  - id: post_merge_cleanup
    event: PostToolUse
    matcher: Bash
    intent: Soft cleanup checklist after `gh pr merge`.
    severity: ADVISORY
    sunset_date: 2026-08-06

  - id: pre_edit_architecture
    event: PreToolUse
    matcher: Edit|Write|MultiEdit|NotebookEdit
    intent: Refuse edit to architecture/** without ARCH_PLAN_EVIDENCE.
    severity: BLOCKING
    reversibility_class: ARCHIVE
    sunset_date: 2026-08-06

  - id: pre_write_capability_gate
    event: PreToolUse
    matcher: Edit|Write|MultiEdit|NotebookEdit
    intent: Topology Gate 1 — refuse writes to blocking-class capability paths.
    severity: BLOCKING                            # delegate to topology redesign
    reversibility_class: cite_capability_class
    sunset_date: 2026-08-04                       # already set
```

### 2.3 `overrides.yaml` schema (new file) — replaces `[skip-invariant]`

```yaml
schema_version: 1
overrides:
  - id: BASELINE_RATCHET
    description: >
      Phase deliberately raises the pytest baseline (e.g., adds new
      antibody tests). Hook expects the new count; old count is stale.
    requires:
      evidence_file: docs/evidence/baseline_ratchets/<date>_<phase>.md
      fields_required: [old_baseline, new_baseline, justification, phase_id]
      auto_expires_after: 24h
    audit_log: .claude/logs/hook_overrides/baseline_ratchet.jsonl

  - id: MAIN_REGRESSION
    description: >
      origin/main itself failed before this PR began. Reconciling on
      our branch should not be gated by main's pre-existing failures.
    requires:
      evidence_file: docs/evidence/main_regressions/<date>.md
      fields_required: [main_commit_sha, failing_test_count_on_main, justification]
      auto_expires_after: 7d
    audit_log: .claude/logs/hook_overrides/main_regression.jsonl

  - id: COTENANT_SHIM
    description: >
      Co-tenant agent's commit pipeline ran while we were mid-phase;
      reconciling its baseline-bump entry into our branch.
    requires:
      evidence_file: docs/evidence/cotenant_shims/<date>.md
      fields_required: [cotenant_commit_sha, justification]
      auto_expires_after: 24h

  - id: REVIEW_SAFE_TAG
    description: >
      Inline [REVIEW-SAFE: <TAG>] tag exists, registered in
      SECURITY-FALSE-POSITIVES.md and .gitleaks.toml allowlist.
    requires:
      inline_tag_present: true
      registry_lookup: SECURITY-FALSE-POSITIVES.md
      auto_expires_after: never

  - id: OPERATOR_CLEARED
    description: One-shot human-cleared finding.
    requires:
      evidence_file: docs/evidence/secrets_overrides/<date>.md
      operator_signature: true
      auto_expires_after: 24h

  - id: STASH_FIRST_VERIFIED
    description: >
      Operator has stashed; pre-checkout overlap is acknowledged and
      recoverable. Hook accepts after `git stash list` shows ≥1 stash
      whose timestamp is within 60s.
    requires:
      git_stash_list_recent: 60s
      auto_expires_after: 5m

  - id: OPERATOR_DESTRUCTIVE
    description: Operator explicitly accepts uncommitted work loss.
    requires:
      evidence_file: docs/evidence/destructive_checkouts/<date>.md
      operator_signature: true
      auto_expires_after: 1h

  - id: SOLO_AGENT
    description: No co-tenant active; main-worktree broad-stage is safe.
    requires:
      orchestrator_active_agents: 1
      auto_expires_after: 1h

  - id: ISOLATED_WORKTREE
    description: cwd is a linked worktree (isolated index).
    requires:
      git_dir_path_contains: "/worktrees/"
      auto_expires_after: never

  - id: OPERATOR_OVERRIDE
    description: Emergency operator clause; hard ceiling.
    requires:
      evidence_file: docs/evidence/operator_overrides/<date>.md
      operator_signature: true
      auto_expires_after: 14d
```

**Why this replaces `[skip-invariant]`:** today's commit-message marker is free-form, leaves no audit beyond `git log --grep`, has no expiry, and conflates 3+ legitimately distinct cases. The structured override catalog forces an agent to declare *which* of 9 named cases applies, attach evidence, and accept an auto-expiry. Telemetry captures `override_id` per use; `tests/test_override_health.py` (CHARTER M5) asserts no single override exceeds its `max_active_per_30d` cap. Cite: `feedback_grep_gate_before_contract_lock.md` (verify before lock); `feedback_accumulate_changes_before_pr_open.md` (2-day-old, verified).

### 2.4 `dispatch.py` (new module, ≤300 LOC)

Single entry point for every hook event. Replaces 7 shell scripts.

```python
# .claude/hooks/dispatch.py  (pseudo-code, ≤300 LOC)
from __future__ import annotations
import json, sys, os, time, yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY  = yaml.safe_load((REPO_ROOT / ".claude/hooks/registry.yaml").read_text())
OVERRIDES = yaml.safe_load((REPO_ROOT / ".claude/hooks/overrides.yaml").read_text())

def main(hook_id: str) -> int:
    payload = json.loads(sys.stdin.read())
    event   = payload.get("hook_event_name", "")
    spec    = next((h for h in REGISTRY["hooks"] if h["id"] == hook_id), None)
    if not spec:
        emit_signal(hook_id, event, "missing_spec", "allow", payload)
        return 0

    # Severity gate: ADVISORY hooks never block; only emit additionalContext.
    if spec["severity"] == "ADVISORY":
        ctx = run_advisory_check(spec, payload)
        return emit_advisory(hook_id, event, ctx)

    # BLOCKING hooks: run check, accept structured override, else block.
    decision, reason = run_blocking_check(spec, payload)
    if decision == "deny":
        override = detect_override(spec, payload)
        if override:
            if validate_override(override, spec, payload):
                log_override_use(spec["id"], override, payload)
                emit_signal(hook_id, event, "override_accepted",
                            "allow", payload, override_id=override["id"])
                return 0
            emit_signal(hook_id, event, "override_invalid",
                        "deny", payload, override_id=override["id"])
            return emit_deny("override evidence invalid", spec)
        emit_signal(hook_id, event, reason, "deny", payload)
        return emit_deny(reason, spec)
    emit_signal(hook_id, event, "passed", "allow", payload)
    return 0

def emit_advisory(hook_id, event, additional_context: str | None) -> int:
    """Emit hookSpecificOutput.additionalContext (Claude Code JSON contract)."""
    if not additional_context:
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": additional_context
        }
    }))
    return 0

def emit_deny(reason: str, spec: dict) -> int:
    """PreToolUse permissionDecision=deny. Per Claude Code Hooks doc."""
    if spec["event"] == "PreToolUse":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason
            }
        }))
        return 0   # JSON envelope, exit 0 per contract
    print(reason, file=sys.stderr)
    return 2       # legacy exit-2 path for non-PreToolUse events

# emit_signal writes one line to .claude/logs/hook_signal/<YYYY-MM>.jsonl
# detect_override / validate_override read overrides.yaml and check the
# concrete evidence file exists, has required fields, and is within auto_expires_after.
```

`.claude/settings.json` collapses to:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash",
        "hooks": [{ "type": "command",
                    "command": ".claude/hooks/dispatch.py invariant_test" },
                  { "type": "command",
                    "command": ".claude/hooks/dispatch.py secrets_scan" },
                  { "type": "command",
                    "command": ".claude/hooks/dispatch.py cotenant_staging_guard" },
                  { "type": "command",
                    "command": ".claude/hooks/dispatch.py pre_checkout_uncommitted_overlap" },
                  { "type": "command",
                    "command": ".claude/hooks/dispatch.py pr_create_loc_accumulation" },
                  { "type": "command",
                    "command": ".claude/hooks/dispatch.py pre_merge_contamination" }] },
      { "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [{ "type": "command",
                    "command": ".claude/hooks/dispatch.py pre_edit_architecture" },
                  { "type": "command",
                    "command": ".claude/hooks/dispatch.py pre_write_capability_gate" }] }
    ],
    "PostToolUse": [
      { "matcher": "Bash",
        "hooks": [{ "type": "command",
                    "command": ".claude/hooks/dispatch.py post_merge_cleanup" },
                  { "type": "command",
                    "command": ".claude/hooks/dispatch.py pr_open_monitor_arm" }] }
    ],
    "SubagentStop": [
      { "matcher": "*",
        "hooks": [{ "type": "command",
                    "command": ".claude/hooks/dispatch.py phase_close_commit_required" }] }
    ]
  }
}
```

### 2.5 PR auto-review Monitor pattern

Triggered by `pr_open_monitor_arm` (PostToolUse). Decision: emit `additionalContext` instead of side-effect-spawning a Monitor — the agent reads the context and decides to arm. This respects the Claude Code best-practices recommendation that hooks deliver "deterministic guarantees, not autonomous side-effects" ([Claude Code Best Practices](https://code.claude.com/docs/en/best-practices)).

The injected context is:

```
PR opened. Per feedback_accumulate_changes_before_pr_open.md, paid
auto-reviewers (Copilot, Codex) fire within 5-8 minutes. Arm a
Monitor and a watcher now:

  Monitor(persistent=true,
          command="prev=''; while true; do
                     s=$(gh pr checks <num> --json name,bucket); 
                     cur=$(jq -r '.[] | select(.bucket!=\"pending\") | \"\\(.name): \\(.bucket)\"' <<<\"$s\" | sort);
                     comm -13 <(echo \"$prev\") <(echo \"$cur\");
                     prev=$cur;
                     jq -e 'all(.bucket!=\"pending\")' <<<\"$s\" >/dev/null && break;
                     sleep 30;
                   done")

  Bash(run_in_background=true,
       command="last=$(date -u +%Y-%m-%dT%H:%M:%SZ); 
                while true; do
                  now=$(date -u +%Y-%m-%dT%H:%M:%SZ); 
                  gh api 'repos/{owner}/{repo}/pulls/<num>/comments?since='$last --jq '...';
                  last=$now; sleep 60;
                done")

Stop both watchers when:
  (a) all checks resolved AND
  (b) all review comments resolved (gh pr view --json reviews shows
      latestReviews state=APPROVED or no actionable items),
  OR if 60 min idle elapses (escalate).

Address comments by commit, not by reply. After each commit batch,
poll once more before declaring DONE.
```

Citations: [gh pr checks --watch --interval=30 spec](https://cli.github.com/manual/gh_pr_checks); [gh pr view --json reviews,comments](https://cli.github.com/manual/gh_pr_view); the Monitor + while-true poll pattern matches the Claude Code Monitor tool's "per-occurrence with natural end" example. The agent already has Monitor; this hook does not invent a tool, only ensures the agent knows to use it.

**Why advisory not blocking**: blocking on PR open creates a chicken-and-egg with the agent's task; advisory + structured context is the lighter pattern that Anthropic's `building-effective-agents` recommends as the "agent loop with checkpoints" envelope.

### 2.6 Worktree-loss prevention — pick: pre-checkout overlap detector with structured override

Problem statement (today's incident): operator-mediated `git checkout main` reverted the topology-redesign working tree silently. Git did NOT refuse the checkout because no merge conflict existed — every modified file existed cleanly on `main` at a different content. Per [git-worktree docs](https://git-scm.com/docs/git-worktree), git refuses a checkout only when:
- the same branch is checked out in another worktree, or
- a merge conflict cannot be auto-resolved.

It does NOT refuse when modified-tracked files would be silently reverted to the target branch's version — that is `git checkout`'s by-design behavior. The harness must add the protection.

**Picked design** (justified below): `pre_checkout_uncommitted_overlap` PreToolUse hook on Bash. When the agent runs `git checkout <branch>`, `git switch <branch>`, `gh pr checkout`, or any sibling, the hook:

1. Runs `git diff --name-only HEAD` (tracked modifications).
2. Runs `git ls-tree -r --name-only <target-branch>` (target branch tree).
3. Computes the intersection. If non-empty AND the diff content differs from the target branch's content for those files, emit `permissionDecision: deny` with the structured override hint:

```
BLOCKED: `git checkout <branch>` would silently revert <N> tracked
modifications that exist at different content on <branch>:

  src/calibration/manager.py     (45 lines diff vs HEAD; 12 lines diff vs target)
  config/settings.json           (3 lines diff vs HEAD; 8 lines diff vs target)
  ...

Lossless options (pick one):
  (a) git stash push -m "pre-checkout-<phase-id>" — recovery via
      git stash show -p stash@{0}^3 if untracked tree needed.
  (b) git commit -m "phase N WIP: <one-line>" — committed work
      survives any subsequent checkout.
  (c) git worktree add ../zeus-checkout <branch> && cd ../zeus-checkout
      — isolated index, no overlap.

Override (operator-only, requires evidence file):
  STRUCTURED_OVERRIDE=STASH_FIRST_VERIFIED git checkout <branch>
  STRUCTURED_OVERRIDE=OPERATOR_DESTRUCTIVE git checkout <branch>
```

**Why this design over alternatives:**

- Auto-stash with explicit recovery: rejected. Today's recovery already proved that operator-mediated stash forensics is fallible (`stash@{0}^2` vs `stash@{0}^3` confusion, per `feedback_stash_recovery_verify_canonical_state.md`). Adding more stashes adds more forensic surface.
- Branch-attached commit-required-before-checkout: rejected as too rigid. Many legitimate `git checkout` flows (operator quick-look at another branch's file) do not warrant a commit.
- Pre-checkout detector with structured override: chosen. The hook gives the agent three lossless options inline, leaves the decision in the agent's task loop, and creates an audit trail when an operator does override.

Citations: [git-worktree safety patterns](https://git-scm.com/docs/git-worktree) — git's own --force-twice safeguard is the precedent for "default refuse, structured override allowed"; [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks) for the `permissionDecision: deny` envelope.

### 2.7 LOC-accumulate-before-PR — pick: PreToolUse advisory hook with skill backup

Problem: agents do not currently know the rule. Per `feedback_accumulate_changes_before_pr_open.md`, the user's framing is "改动需要累积到一个层级才开pr 因为打开会有auto review."

Three discoverability options were considered:

| Option | Pro | Con |
|---|---|---|
| Pre-PR hook | Deterministic; agent gets advisory at exact moment of `gh pr create` | Adds noise on every PR open including the legitimate ones |
| Slash command (e.g., `/oh-my-claudecode:pr-ready`) | Agent invokes when intent is clear | Agent has to know the command exists |
| Memory file the agent reads before `gh pr create` | Already in pattern via `feedback_*` | Agent is not currently primed to read it pre-`gh pr create` |

**Picked: PreToolUse advisory hook (`pr_create_loc_accumulation`) + memory cross-reference.**

The hook fires on `gh pr create` / `gh pr ready`. It checks:
- commits since branch base (`git rev-list --count <base>..HEAD`)
- LOC since branch base (`git diff --shortstat <base>..HEAD`)
- count of pushes already done (informational; from `git reflog show <branch>`)

If commits < 2 OR LOC < 80 (heuristic floor; tunable in registry.yaml), emit advisory `additionalContext`:

```
ADVISORY: PR open about to fire paid auto-reviewers (Copilot + Codex
+ ultrareview within 5-8 min). Current accumulation:
   commits since base: <N>
   LOC since base:     <M>
   pushes already:     <K>

Per feedback_accumulate_changes_before_pr_open.md (verified 2026-05-04):
PRs should open at ≥2 commits and ≥80 LOC unless explicitly approved
for a quick fix. If this open is intentional (urgent fix, isolated
bug), proceed. If you have more pending work on this branch, hold the
PR open until accumulation reaches the threshold.

This is advisory; not blocking.
```

Justification: hook is the deterministic discoverability path Anthropic recommends ("hooks for actions that must happen every time with zero exceptions"). Memory cross-reference closes the loop — the hook **cites** the feedback memory by path so the agent can read it for full context.

### 2.8 Per-phase commit gate — pick: SubagentStop advisory

`phase_close_commit_required` SubagentStop hook fires when any subagent with `agent_type` matching `*phase_*` (planner/critic/executor in a phase context) returns. The hook reads:
- `git status --porcelain` — count of modified-tracked files
- `git rev-parse --abbrev-ref HEAD` — current branch
- `git log -1 --since="<phase_start>" --pretty=%H` — last commit timestamp

If tracked changes exist AND no commit has happened during this phase, emit `additionalContext`:

```
ADVISORY: phase subagent returned with <N> tracked modifications and
zero commits during this phase. Per feedback_commit_per_phase_or_lose_everything.md
(verified 2026-05-06; cost: $190 in the topology redesign session):

   git add <phase-N specific paths>
   git commit -m "phase N close: <one-line summary>"

Skip if this phase deliberately accumulates into a later commit
(rare; applies to recovery + stash-restore phases only).
```

**Why advisory not blocking**: blocking SubagentStop is a heavy gate that interferes with legitimate streaming workflows. Advisory + memory-citation puts the discoverability and the cost evidence in the agent's context exactly when it can act. Per Claude Code Hooks doc, SubagentStop supports both advisory and blocking; advisory is the lighter contract for a behavioral nudge.

### 2.9 State-of-the-art alignment

Eight hook events available per [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks): PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, Stop, SubagentStop, PreCompact, plus WorktreeCreate / WorktreeRemove. Existing Zeus hooks use only **2** (PreToolUse, PostToolUse). The redesign adds:

- **SubagentStop** — phase-close commit gate (§2.8).
- Optional future **SessionStart** — inject worktree-state summary so the agent boots with awareness of pending stashes, uncommitted overlap, open PRs, active Monitors. Out of scope for Phase 1; tracked as future work in §6.
- Optional future **PreCompact** — emit summary of unfired structured overrides + unresolved PR comments so they survive context compaction. Out of scope for Phase 1.

All hooks use the structured JSON envelope (`hookSpecificOutput.permissionDecision` / `additionalContext`) per the Claude Code Hooks reference, not the legacy exit-2-and-stderr pattern.

---

## §3 Phases

Three phases sized for sonnet executor (~1-2 hours each).

### Phase 1 — Stable layer + dispatch (~2h)

**Owner:** sonnet implementer.

**Deliverables:**

| File | LOC | Source |
|---|---|---|
| `.claude/hooks/registry.yaml` | ~250 | §2.2 schema; 11 hook entries |
| `.claude/hooks/overrides.yaml` | ~150 | §2.3 schema; 9 override entries |
| `.claude/hooks/dispatch.py` | ~300 | §2.4 pseudo-code; production module |
| `tests/test_hook_registry_schema.py` | ~80 | YAML schema validation; sunset_date required; severity enum |
| `tests/test_hook_dispatch_smoke.py` | ~100 | sample payloads for each hook_id; assert exit codes + JSON envelope |

**Exit criteria:**
- All YAML schema validators green.
- `dispatch.py invariant_test` returns exit-0 + valid JSON envelope on a test commit (smoke test).
- No live hook wired yet — `.claude/settings.json` unchanged at end of Phase 1 (parallel install).
- File headers carry Created / Last reused/audited / Authority basis (per `feedback_verify_paths_before_prompts.md` legacy rule + AGENTS.md provenance rule).

**Rollback:** files are new; delete. Zero impact on running hooks.

### Phase 2 — New gates + structured-override migration (~2h)

**Owner:** sonnet implementer.

**Deliverables:**

| File | LOC | Source |
|---|---|---|
| `pre_checkout_uncommitted_overlap` impl in `dispatch.py` | +60 | §2.6 |
| `pr_create_loc_accumulation` impl in `dispatch.py` | +50 | §2.7 |
| `pr_open_monitor_arm` impl in `dispatch.py` | +30 | §2.5 |
| `phase_close_commit_required` impl in `dispatch.py` | +50 | §2.8 |
| `tests/test_pre_checkout_overlap.py` | ~80 | sample git states; assert deny on overlap, allow on disjoint |
| `tests/test_structured_overrides.py` | ~120 | each override_id exercise; assert evidence-file validation; assert auto-expiry |
| `docs/evidence/baseline_ratchets/.gitkeep` etc. | n/a | placeholder evidence dirs |
| Migration shim: `pre-commit-invariant-test.sh` accepts both `[skip-invariant]` (legacy) AND `STRUCTURED_OVERRIDE=BASELINE_RATCHET` (new); legacy emits `migration_warning` ritual_signal. | +20 to existing | shim period only |

**Exit criteria:**
- New hooks pass smoke + structured-override validation tests.
- Legacy `[skip-invariant]` still accepted but emits `migration_warning` per use (telemetry). Migration runway: 30 days.
- `.claude/settings.json` updated to add the 4 new hooks AND retain the 7 existing ones (parallel-shipping).
- `tests/test_help_not_gate.py` (composes with topology redesign CHARTER M5) extended with hook coverage: no hook fires on a payload outside its `intent`.

**Rollback:** revert `.claude/settings.json` to Phase-1 state; keep dispatch.py code in place (dormant).

### Phase 3 — Cutover + telemetry + legacy retirement (~1.5h)

**Owner:** sonnet implementer + critic.

**Deliverables:**

| Item | Source |
|---|---|
| `.claude/settings.json`: 7 legacy shell-script entries removed; replaced by `dispatch.py <hook_id>` invocations | §2.4 final form |
| 7 legacy shell scripts moved to `.claude/hooks/legacy/` (kept readable for 30 days, then `git rm`) | parallel removal |
| `tests/test_hook_signal_health.py` (~60 LOC) | M1 telemetry: assert ritual_signal lines well-formed; >5% advisory-with-no-action over 7d auto-flags for review |
| `tests/test_override_health.py` (~80 LOC) | M5: assert no override_id exceeds `max_active_per_30d` |
| `docs/operations/hook_redesign_cutover_evidence.md` | shadow telemetry summary: 7d of dual-running before legacy retire |
| Operator GO sign-off on `evidence/hook_phase3_decision.md` | mid-drift check |

**Exit criteria** (numeric; cite back to §1.1 problem table):
- `[skip-invariant]` rate over 14d post-cutover < 1.5/day (current 3.1/day; target halving).
- ≥80% of `[skip-invariant]` use migrated to a structured override_id.
- Telemetry shows zero `pre_checkout_uncommitted_overlap` fires that ended in subsequent stash recovery (regression detector).
- Hook surface LOC: 1,337 → ≤800 (registry+overrides+dispatch+tests subsume 7 shell scripts).
- All 11 hooks emit `ritual_signal` at every fire; one missing emit blocks Phase 3 GO.

**Rollback:** restore legacy shell scripts from `.claude/hooks/legacy/`; revert `.claude/settings.json`. Per-hook feature flag also available: `ZEUS_HOOK_DISPATCH=off` falls back to legacy path. dispatch.py reads this env at start.

### Cross-phase invariants (always true)

- **No hook bypasses --no-verify.** Same as legacy contract; `--no-verify` skips ALL git hooks, not Claude Code hooks. Reaffirmed in CHARTER §6.
- **Every override leaves an audit log.** `.claude/logs/hook_overrides/<override_id>.jsonl` is the canonical record; `[skip-invariant]` audit-via-git-log is preserved for the 30d migration window.
- **No live trading unlock.** Hook redesign does not touch `ZEUS_HARVESTER_LIVE_ENABLED`, kill switch, or settlement window freeze.
- **No production DB writes.** All testing uses fixtures + temp git repos.

---

## §4 Risks

| ID | Title | Prob (L/M/H) | Impact (L/M/H) | Structural mitigation | Detection signal |
|---|---|---|---|---|---|
| H-R1 | Structured overrides become the new bypass culture (helper-inflation ratchet recurs at the override layer) | M | H | `max_active_per_30d` cap per override_id (registry.yaml); telemetry `tests/test_override_health.py` flags >threshold use; quarterly critic review per CHARTER §5.1 | jsonl line count per override_id over 30d > cap |
| H-R2 | `pre_checkout_uncommitted_overlap` produces false positives on legitimate operator quick-looks | M | M | Hook emits 3 lossless options inline; `STASH_FIRST_VERIFIED` override is ergonomic (5min auto-expiry, no evidence file required, just a recent stash); 14d shadow window before blocking | telemetry advisory_to_blocking_ratio; >50% override usage signals false-positive surplus |
| H-R3 | Legacy `[skip-invariant]` agents continue using the marker after migration runway closes | H | M | 30d migration emits `migration_warning` per use; coordinator briefs (per `feedback_dispatch_brief_concise.md`) cite the new override_ids by path; `tests/test_legacy_marker_retired.py` blocks once migration window closes | post-migration `[skip-invariant]` count > 0 in 7d window |
| H-R4 | dispatch.py becomes the single point of failure (one bug breaks all 11 hooks) | M | H | dispatch.py per-hook try/except: a hook crash logs `ritual_signal` `dispatch_error` and exits 0 (fail-open per Claude Code best-practice "exit 0 on hook error"); `tests/test_hook_dispatch_smoke.py` exercises every hook_id every CI run | CI red on dispatch.py; ritual_signal `dispatch_error` count > 0 |
| H-R5 | Monitor-arming advisory ignored by agent (PR auto-review goes unwatched) | M | M | `pr_open_monitor_arm` advisory cites Anthropic's "agent loop with checkpoints" + the cost evidence (paid review per push); `feedback_accumulate_changes_before_pr_open.md` is referenced by path; future Phase 4 (out of scope) could add a `Stop` hook that refuses session-stop while a PR is open with unresolved comments | telemetry advisory_emitted vs Monitor-actually-armed ratio |
| H-R6 | SubagentStop `phase_close_commit_required` fires too aggressively, including on non-phase subagents | M | L | matcher narrowed to `agent_type` containing `phase_` substring; advisory severity (no block); 14d shadow window before any escalation | telemetry advisory_emitted vs commit-actually-followed ratio |

---

## §5 Charter / drift mechanisms (abbreviated)

This section is the topology redesign's CHARTER scaled down to the hook surface. Refer to `docs/operations/task_2026-05-06_topology_redesign/ultraplan/# ANTI_DRIFT_CHARTER.md` for the full M1-M5 framework; the table below is the hook-redesign-specific binding.

| Mechanism | Concrete artifact | Hook redesign binding |
|---|---|---|
| **M1 telemetry-as-output** | `.claude/logs/hook_signal/<YYYY-MM>.jsonl` | every `dispatch.py` invocation writes one line; schema `{hook_id, event, decision, reason, override_id?, session_id, agent_id?, ts}`; >20% blocking-ratio over 30d auto-flags for tuning |
| **M2 opt-in-by-default** | `severity: ADVISORY \| BLOCKING` in registry.yaml | new hooks default to `ADVISORY`; promotion to `BLOCKING` requires (a) operator signature in PR description, (b) cite of recent miss within 30d, (c) `sunset_date` |
| **M3 sunset clock per hook** | `sunset_date` field required by registry.yaml schema | `tests/test_hook_registry_schema.py` enforces; default 90d for advisory hooks, 12mo for stable primitives |
| **M4 original-intent contract** | `intent` + `blocked_when` keys in registry.yaml | hook refuses to fire when payload doesn't match intent (e.g., `invariant_test` only fires on `git commit`, not on `git status`); current `pre-commit-invariant-test.sh` already does this via `has-git-subcommand commit` parser, kept |
| **M5 INV-HOOK-NOT-GATE** | `tests/test_hook_not_gate.py` (~150 LOC) | composes with topology's `INV-HELP-NOT-GATE`; asserts no hook blocks a payload outside its `intent`; asserts every hook fire emits `ritual_signal`; asserts no override_id exceeds `max_active_per_30d` |

**Telemetry review cadence:** monthly critic-agent review of `hook_signal/*.jsonl`; quarterly operator review.

**Operator override protocol:** `OPERATOR_OVERRIDE` override_id (overrides.yaml) is the single emergency clause; requires evidence file + 14d auto-expiry. `--no-verify` and direct edit of `.claude/settings.json` remain available as the absolute escape hatch but are out-of-band.

---

## §6 Cutover

### 6.1 Pre-cutover gates

- [Phase 1 done] Schema validators green; smoke tests green.
- [Phase 2 done] Parallel running ≥7 days; `dispatch.py` matches legacy hook output on all sampled commits.
- 14-day shadow telemetry window for `pre_checkout_uncommitted_overlap` before its blocking severity activates (until then, advisory only).
- Migration shim accepts both `[skip-invariant]` and structured override; ≥80% of new commits use structured override before legacy is retired.
- `tests/test_hook_dispatch_smoke.py`, `tests/test_structured_overrides.py`, `tests/test_help_not_gate.py` all green.

### 6.2 Cutover sequence (gradual)

| Day | Action | Rollback trigger |
|---|---|---|
| 1 | Phase 1 ships (registry + dispatch + tests; settings.json unchanged) | Schema test red |
| 2-7 | Phase 2 ships in parallel mode (legacy + new run side-by-side) | Smoke test red on real commit traffic; >5% mismatch between legacy and new outputs |
| 7-14 | `pre_checkout_uncommitted_overlap` activates as ADVISORY only | telemetry advisory-to-blocking conversion ratio >50% (false-positive signal) |
| 14-21 | `pre_checkout_uncommitted_overlap` activates as BLOCKING | telemetry shows ≥1 stash-recovery event in this window |
| 14-30 | Legacy `[skip-invariant]` accepted with `migration_warning` | post-cutover `[skip-invariant]` rate not declining (<25% reduction by day-21) |
| 30 | Legacy shell scripts deleted from `.claude/hooks/`; `[skip-invariant]` no longer accepted | any hook regression discovered post-deletion → restore from git tag `pre-hook-cutover` |

### 6.3 First 24h / 7d / 30d telemetry watch

| Metric | Source | Day-1 floor | Day-7 floor | Day-30 target |
|---|---|---|---|---|
| `[skip-invariant]` daily rate | `git log --grep="skip-invariant" --since="1 day ago"` | <3.5/day (current+0.5 buffer) | <2.5/day | <1.5/day |
| Structured override use | `.claude/logs/hook_overrides/*.jsonl` | any nonzero | ≥30% of all override events | ≥80% |
| `pre_checkout_uncommitted_overlap` true-positives | `.claude/logs/hook_signal/*.jsonl` filter | n/a (advisory only) | ≥1 (proves the gate fires) | trend stable |
| `pr_open_monitor_arm` advisory-followed ratio | hook_signal advisory_emitted vs Monitor-armed | n/a | ≥30% | ≥70% |
| `phase_close_commit_required` advisory-followed ratio | hook_signal advisory_emitted vs commit-followed | n/a | ≥40% | ≥80% |
| Stash-recovery events (proxy: `git reflog \| grep WIP-on`) | reflog audit weekly | 0 (current was 1 over 60d before redesign) | 0 | 0 |

### 6.4 Rollback plan

- **Full rollback:** restore `.claude/settings.json` from git tag `pre-hook-cutover`; restore 7 legacy shell scripts from `.claude/hooks/legacy/`; `dispatch.py` becomes dormant (no callers).
- **Per-hook rollback:** set `ZEUS_HOOK_DISPATCH_<hook_id>=off` in env; `dispatch.py` falls through to legacy script for that one hook.
- **Override-class rollback:** if `STASH_FIRST_VERIFIED` proves too lax in production, set `overrides.yaml::STASH_FIRST_VERIFIED.requires.auto_expires_after: 0s` to disable.

### 6.5 Post-cutover stabilization

- Day 30: archive legacy shell scripts (`git rm .claude/hooks/legacy/`).
- Day 60: review override usage telemetry; if any override_id exceeds `max_active_per_30d` cap, demote to `BLOCKING` or sunset.
- Day 90: full anti-drift telemetry baseline reset; CHARTER §5.1 quarterly review fires.

---

## §7 Web research summary (sources cited)

1. [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks) — JSON contract for `permissionDecision`, `additionalContext`, exit codes, the 8 hook events (PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, Stop, SubagentStop, PreCompact, plus WorktreeCreate/WorktreeRemove). Authoritative for §2.4 dispatch.py JSON envelope.
2. [Claude Code Hooks guide](https://code.claude.com/docs/en/hooks-guide) — concrete examples of safety checks, blocking dangerous bash, formatting, session-start context. Cited in §2.4 for the "deterministic guarantees, not autonomous side-effects" framing.
3. [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices) — "Use hooks for actions that must happen every time with zero exceptions." Cited in §2.5 (PR Monitor advisory not blocking) and §2.7 (LOC-accumulate hook).
4. [Claude Code Worktrees](https://code.claude.com/docs/en/worktrees) — `--worktree` isolation pattern; `WorktreeCreate`/`WorktreeRemove` events. Cited in §2.6 as the harness-level alternative to per-checkout overlap detection.
5. [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — orchestrator-workers and evaluator-optimizer patterns; "agent loop with checkpoints" for monitoring long-running external processes. Cited in §2.5 (PR Monitor design).
6. [git-worktree man page](https://git-scm.com/docs/git-worktree) — git's own --force-twice safeguard precedent ("default refuse, structured override allowed"); documented edge cases including silent-revert behavior absent merge conflict. Cited in §2.6 for the worktree-loss prevention design.
7. [GitHub CLI — gh pr checks](https://cli.github.com/manual/gh_pr_checks) — `--watch`, `--interval=10` (default), `--json bucket,name,state`, exit code 8 for pending. Cited in §2.5 Monitor command.
8. [GitHub CLI — gh pr view](https://cli.github.com/manual/gh_pr_view) — `--json reviews,comments,latestReviews` fields. Cited in §2.5 Monitor command.
9. [Gitleaks documentation](https://github.com/gitleaks/gitleaks) — false-positive mechanisms: inline `gitleaks:allow` comment, `.gitleaksignore` finding fingerprint, `[[rules.allowlists]]` config, `--baseline-path`. Cited in §2.3 `REVIEW_SAFE_TAG` override design (current Zeus pattern composes inline tag + SECURITY-FALSE-POSITIVES.md registry + .gitleaks.toml allowlist).
10. [GitHub Actions — pull_request events](https://docs.github.com/en/actions/using-workflows/triggering-a-workflow) — `pull_request_review` and `pull_request_review_comment` event taxonomy; informs the polling cadence in §2.5 Monitor (30s for local checks, 60s for remote API).

**Sources count: 10.**

Additionally cited (non-web; project-internal authority):
- `docs/operations/task_2026-05-06_topology_redesign/ultraplan/` — 6 files (verbatim file names with `# ` prefix)
- `~/.claude/skills/orchestrator-delivery/SKILL.md`
- `~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/feedback_*.md` — 7 verified-recent memories cited inline
- `.claude/hooks/*.{sh,py}` — 8 hook files, 1,337 LOC, all read for verbatim citations

---

## §8 Phase summary table

| Phase | Days | Deliverables | Exit criteria | Key risks |
|---|---|---|---|---|
| **1 — Stable layer + dispatch** | ~2h | `registry.yaml` (250 LOC) · `overrides.yaml` (150 LOC) · `dispatch.py` (300 LOC) · 2 schema/smoke tests (180 LOC) | All schema validators green · smoke tests green · settings.json unchanged · file headers compliant | H-R4 dispatch.py SPOF — mitigated by per-hook try/except + smoke test every CI |
| **2 — New gates + structured-override migration** | ~2h | 4 new gates impl in dispatch.py (190 LOC) · 2 override tests (200 LOC) · settings.json gains 4 entries · migration shim on legacy `[skip-invariant]` | New hooks pass smoke + structured-override tests · legacy still accepted but emits migration_warning · `tests/test_help_not_gate.py` extended | H-R2 false positives on quick-look checkouts — mitigated by 14d advisory-only window; H-R5 Monitor advisory ignored — telemetry tracks |
| **3 — Cutover + telemetry + legacy retirement** | ~1.5h | settings.json: 7 legacy entries removed · legacy shells moved to `.claude/hooks/legacy/` · 2 telemetry tests (140 LOC) · cutover evidence doc · operator GO sign-off | `[skip-invariant]` rate <1.5/day (50% reduction) · ≥80% migrated to structured override · zero stash-recovery events · hook surface ≤800 LOC | H-R1 override culture — `max_active_per_30d` cap + quarterly review; H-R3 legacy marker holdouts — migration_warning + day-30 retirement |

**Total runtime: ~5.5h (sonnet executor, 3 phases, no fix-loop overhead).**

Sources cited: **10 web + 4 project-internal authority surfaces.**
