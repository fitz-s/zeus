# critic-opus PROCESS post-mortem — session 5278ceeb-620a-45f7-aa49-e9d619595321

HEAD: f56b33b0033bb08070870f2aeda39045f404ea8b (workspace-venus/zeus tip)
Branch: live-launch-prep-2026-05-05 (workspace) | cleanup-debt-2026-05-07 (worktree under review)
Reviewer: critic-opus
Date: 2026-05-07
Authority basis: orchestrator-delivery SKILL.md §269 empirical reference; ledger at `.claude/orchestrator/runs/topology-redesign-2026-05-06/state/agent_registry.jsonl` (49 entries)

---

## §1 Session arc summary

24-hour session ran a topology-redesign + hook-redesign pair with concurrent maintenance. PR #70 (calibration quarantine, 6 files / +353 / -8) merged at 07:18Z. PR #71 (topology + hook redesign, 154 files / +21,202 / -3,223) merged at 07:18:31Z — exceeded Copilot's 20K-line review limit, lost the auto-review channel. PR #72 (navigation topology v2 + post-session debt cleanup, 25 files / +5,206 / -29) opened during cleanup and remained OPEN at session close after 8 review-batch fixes (Codex P1 + Copilot F1..F8, commit 6751d5f2).

Phases delivered per ledger: Phase 0.A→0.H (5 sub-phases + critic), Phase 1 + 1.R, Phase 2 + 2.R, Phase 3 + 3.H, Phase 4.A → 4.D + 4.H + 4.R + 4.H.R, Phase 5.A → 5.D, hook redesign Phase 0 + 1 + 2 + 3 + 3.R, navigation topology v2 Phase 0 + 1 + 2A + 2B + 3 + 4 + 4.R. Two stash-recovery incidents: catastrophic checkout reverting ~$190 of uncommitted work (recovered via `e355af51`); Phase 1+3 sweep collision where Phase 3 committed Phase 1's unstaged files (recovered via stash baseline diff at registry entry 19 "surfaced_issue").

Subagent dispatches: ~30 distinct agentIds in the ledger. Bypass culture replaced (friction-delta -91%, 21.42% topology tool-call ratio → 0.40h replay friction). 5 explicit feedback memories added during session.

Word count: 198.

---

## §2 Token + context burn estimate (per orchestrator-delivery SKILL.md §269)

The May3 R5 reference was 30 dispatches → ~450K tokens with target ~150-200K. This session ran ~30+ dispatches across two subgraphs of work (topology + hook + nav-v2) plus one /compact event.

**Estimated coordinator burn: ~600-750K input tokens** (extrapolated from May3 R5 baseline; this session was longer, had more sub-phase dispatches, and showed the same emission-layer disciplines failing per SKILL.md:485-498).

Attribution table (per SKILL.md§269 categories):

| Category | Est % | Evidence |
|---|---|---|
| SendMessage redundant fields | ~25-30% | SKILL.md:178 says "self-correction empirically does not hold". No hook deployed (SKILL.md:192 still recommends). Operator's directive at invariants.jsonl line 2 "Reuse subagents via SendMessage" was attempted but failed 3× immediately on namespaced executors (entries 3-5: `claude-sonnet-4-6` model resolution error — exact failure documented at SKILL.md:109-146). Three failed sends + every successful send still likely carried duplicate `content`/`recipient` fields. |
| Dispatch brief over-detailing | ~30-35% | Hook redesign Phase 0 brief authored a single 600-line PLAN.md (registry entry 39, ledger said `single-doc redesign per operator scope correction`) — appropriate as ARTIFACT but if inlined into a dispatch brief violates the ≤50-line rule at SKILL.md:206-234. Phase 4.A executor `resume_count: 1` (entry 19) and Phase 5.B `resume_count: 1` (entry 31) imply at minimum one round of re-context-passing. |
| Heredoc-via-Bash echoes | ~5% | Lower than May3 because most ledger appends used Edit tool per SKILL.md:235; visible from JSONL line shape (entry 46 uses different quoting style → Bash heredoc, entries 47+ use Edit-style). |
| End-of-turn summaries + structured-return parsing | ~25-30% | Multiple critic verdict files in `evidence/` (phase{0..5}_h_decision.md, hook_redesign_critic_opus_final_v2.md, topology_v2_critic_opus_final_v2.md) are each multi-K artifacts; coordinator necessarily re-read them to dispatch remediation. Compaction (1×) likely added another summary tax. |
| Necessary boot + invariants reads | ~5-10% | Lower bound. |

Verdict: this session burned roughly 3-4× the SKILL.md target. The single largest waster, per SKILL.md:485-498 ("self-discipline reminders empirically do not hold"), is SendMessage redundant fields — and the SKILL.md was updated 2 days before this session with that exact warning. Behavior unchanged. **Tool-layer hook still missing.**

---

## §3 Was task ideal? — verdict + sub-ideal moments

**Verdict on outcome: A-** (delivered scope > planned scope: PR #70 + PR #71 both merged; PR #72 opened with full review-batch close; bypass culture eliminated; friction-delta -91%; 5 feedback memories captured).

**Verdict on process: C** (avoidable rework, missed pre-flight checks, scope creep into nav-v2 mid-cleanup).

Sub-ideal moments:

1. **Stash catastrophe (entry 21 `recovery_from_stash_e355af51`)**: branch checkout from topology-redesign to main silently reverted ~$190 of uncommitted phase work. Root cause: 7 phases worked WITHOUT inter-phase commits. Captured in `feedback_commit_per_phase_or_lose_everything.md`. **Should have been a per-phase commit gate from Phase 0.D forward** — orchestrator-delivery SKILL.md does not currently enforce.

2. **Phase 1+3 sweep collision (entry 19 `surfaced_issue`)**: Phase 3 critic ran narrow regression list (5 test files / 82 passed); missed full-suite breakage of 113 tests. Phase 4.A discovered the import-chain failures. **Critic baseline-narrowing failure mode** — also currently uncaptured in SKILL.md.

3. **Namespaced-executor SendMessage failures (entries 3-5)**: 3 successive `claude-sonnet-4-6` model-not-found errors despite SKILL.md:109-146 explicitly warning about this exact failure. Operator directive at invariants.jsonl line 2 was followed mechanically without checking the gotcha. **First-dispatch capability probe** (SKILL.md:141 "Run a one-shot resume probe early") was not run.

4. **Sonnet critic missed P5-M1** (entry 33): opus tier rerun caught `settlement_commands.py::submit_redeem` on-chain mutation NOT gated by gate_runtime — capability `on_chain_mutation` was missing the path. Five prior sonnet critics had passed it. Validates SKILL.md MODEL_TIERING.md "high-risk domains → opus critic default". **Should have been opus critic from Phase 4.H given live-money surface.** Phase 4 critic was sonnet (entry 26: `model: sonnet`).

5. **Scope creep into nav-v2 mid-cleanup**: PR #71 merged at 07:18Z; nav-v2 PR #72 opened the SAME day with 5,206 additions across 25 files, plus 8 review fixes. Per `feedback_accumulate_changes_before_pr_open.md` the policy is to batch — but a fresh PR mid-cleanup re-triggers paid auto-reviewers. The critic-opus topology_v2_critic_opus.md → topology_v2_critic_opus_final.md → topology_v2_critic_opus_final_v2.md three-pass loop suggests scope was misestimated up front.

---

## §4 Compaction analysis

One manual `/compact` event mid-session (per operator brief). The ledger continues without identity loss after the event — entries 26+ retain consistent agentId references and reference earlier registered agents (e.g., entry 28 resumes agentId `af8fca0ad6dd562bd` from entry 26 via SendMessage). **Identity persistence: clean.** This is empirical confirmation of SKILL.md:651-668 ("Surviving compaction — promote insight to disk within same response"): every operator directive landed in invariants.jsonl as a structured row, so compaction preserved the structure even if narrative was lossy.

**Open-task state post-compact: NOT verifiable from ledger alone.** Entries 36 and 37 have out-of-order timestamps (36: 19:23, 37: 19:35 — registered after entries 28-35 with later timestamps), suggesting either coordinator re-emitted ledger appends post-compact or out-of-band edits. This is a regression signal: **the ledger should be append-only with monotonically-increasing timestamps**; bumps indicate coordinator confusion.

Net: compaction did not break the run, but timestamp inversion shows the post-compact coordinator was less precise about ledger order. **Failure mode is silent**, not surfaced anywhere in evidence/.

---

## §5 Agent reuse pattern + critic rotation analysis

Critic-opus / code-reviewer dispatches across the session:

| AgentId | Phase | Tier | Verdict |
|---|---|---|---|
| `a69db1a17e33aaa7d` | 0.H | sonnet | NO-GO |
| `aef2951d8b3116423` | 1.H | sonnet | GO-WITH-CONDITIONS |
| `a4b036005b06a79ac` | 2.H | sonnet | GO-WITH-CONDITIONS |
| `adc9160d4df3bbfd1` | 3.H | sonnet | GO-WITH-CONDITIONS |
| `af8fca0ad6dd562bd` | 4.H + 4.H.R (resume) | sonnet | NO-GO → GO-WITH-CONDITIONS |
| `ae8409b090c90b1d8` | 5.H | **opus** | GO-WITH-CONDITIONS (caught P5-M1 missed by 5 prior sonnets) |
| `ac0e874ad47ad8053` | hook.0.critic | opus | GO-WITH-CONDITIONS |
| `a140eaefcb1f75004` | hook.3.critic | opus | NO-GO (truncated, found dispatch.py phase1_stub regression) |
| (unregistered) | nav-v2 critic round 1 + 2 | opus | (3 verdict files in evidence/) |

**Rotation pattern**: sonnet sonnet sonnet sonnet sonnet **opus** for the runtime gates, and opus for the high-stakes redesign-plan critics (hooks, nav-v2). This is the inverse of SKILL.md§291-310 "rotate the persistent critic at 8+ phases" — the pattern was instead "rotate sonnet→opus when surface = LIVE BOUNDARY". Both are valid heuristics but neither is encoded.

**Critic rotation discipline, per SKILL.md:621-650 spot-trace rule**: "When a run reports uniformly-success metrics, require at least one fresh-context spot-trace of an APPROVED phase." Phase 4.H was NOT spot-traced — sonnet APPROVED, then Phase 5 opus caught P5-M1 (`settlement_commands.py::submit_redeem` ungated). This is the rule firing exactly as specified, but the spot-trace was implicit (next-phase critic happened to be tier-rotated) rather than explicit (no `spot_trace_*` ledger entries). **Lucky catch, not systematic catch.**

**SendMessage reuse for critics**: entry 28 (`af8fca0ad6dd562bd` resumed via SendMessage for 4.H.R re-verify) is the ONLY clear SendMessage critic resume. All other critics were fresh Agent() spawns. This contradicts invariants.jsonl line 2 ("Reuse subagents via SendMessage") — **operator directive was de-facto abandoned for critic dispatches after the namespaced-executor failure on Phase 0**. Should have been re-asserted post-fix or formally retracted.

---

## §6 Skills improvement proposals

Six concrete deltas. Each cites session evidence per anti-rubber-stamp rule.

### Δ1: Add per-phase commit gate to orchestrator-delivery SKILL.md

`~/.claude/skills/orchestrator-delivery/SKILL.md` — currently has no per-phase commit rule.

Current state: phase-close protocol (line 252-265) requires disposition of carry-forwards but NOT commit-on-disk.

Proposed addition (new subsection after §253):
```
### Per-phase commit gate (2026-05-07 stash-recovery learning)
Every phase close MUST produce a git commit before the next phase
dispatches. Long branched orchestrations have empirically silently lost
~$190 of work via inter-branch checkout when 7 phases held uncommitted
state (Zeus topology-redesign 2026-05-06, registry entry 21).
Recovery via stash is operator-mediated and not always possible.
Coordinator MUST: (a) commit phase artifacts on close, (b) refuse
next-phase dispatch if `git status` shows uncommitted phase work.
```
Rationale: directly captures the registry entry 21 incident; SKILL.md currently lacks any tooling to prevent recurrence.

### Δ2: Add critic baseline-coverage rule

`~/.claude/skills/orchestrator-delivery/SKILL.md` — currently no rule about critic regression scope.

Proposed addition after §621-650 spot-trace block:
```
### Critic regression scope: full suite OR explicit narrow declaration (2026-05-07)
A critic's regression baseline must either (a) run the full pytest suite,
or (b) declare explicitly which test files are NOT in scope and why. Phase
3 critic ran 5 files/82 tests, missed 113 import-chain breakages from same
phase's deletions; Phase 4.A discovered the gap (Zeus topology-redesign,
registry entry 19 surfaced_issue field). Critics whose deletions touch
import surfaces MUST run full suite.
```
Rationale: registry entry 19 captures the exact failure mode in operator's own words; not in any SKILL doc.

### Δ3: First-dispatch resume probe — promote from recommendation to gate

`~/.claude/skills/orchestrator-delivery/SKILL.md:141` currently says "Run a one-shot resume probe early in any new run" as a recommendation under "Gotcha — agent-definition `model:` must be an alias".

Proposed: promote to a coordinator startup gate. Current state quote: "3. Run a one-shot resume probe early in any new run: dispatch a trivial executor/explore boot, send a no-op SendMessage, confirm it doesn't crash with the model-not-found error." Change "early in any new run" → "before the first SendMessage of the run; refuse SendMessage path until probe completes".

Rationale: this session's invariants.jsonl entries 3-5 are three back-to-back failures of the exact gotcha SKILL.md warned about. Self-discipline did not hold per SKILL.md:485-498's own meta-rule.

### Δ4: Live-boundary critic tier auto-escalation

`~/.claude/skills/orchestrator-delivery/references/MODEL_TIERING.md:13-15` says opus critic for "auth, payments, settlement, oracle, calibration, security, secrets, migrations" but is "Not enforced".

Proposed: enforce. Add a coordinator check before dispatching critic: if any phase deliverable touches `src/execution/**`, `src/venue/**`, `src/contracts/{settlement_semantics,execution_price,venue_submission_envelope,fx_classification}.py`, or capabilities.yaml hard_kernel_paths in those modules → critic_tier=opus mandatory.

Rationale: this session's Phase 4 critic was sonnet and missed `settlement_commands.py::submit_redeem` ungated on-chain mutation (registry entry 33: "opus caught this; 5 prior sonnet critics missed it"). Five-critic miss on a live-money surface is the strongest evidence in the session.

### Δ5: Promote 3 in-session feedback memories to project-memory

`~/.openclaw/workspace-venus/zeus/memory/`: `feedback_commit_per_phase_or_lose_everything.md`, `feedback_stash_recovery_verify_canonical_state.md`, `feedback_orchestrator_offload_lookups.md` already exist. The post-mortem registry shows 3 more patterns worthy of memory:

- `feedback_critic_full_suite_when_deletions_present.md` (per Δ2)
- `feedback_namespaced_executor_resume_probe_first.md` (per Δ3)
- `feedback_phase_critic_tier_matches_surface_risk.md` (per Δ4)

Rationale: per operator's "don't stuff Claude memory" rule, only the three above are session-distinct enough to warrant a memory file. Do NOT promote: SendMessage-fields (already at SKILL.md:178), dispatch-brief-≤50 (already), heredoc→Edit (already).

### Δ6: Ledger append-only-monotone discipline

`~/.claude/skills/orchestrator-delivery/SKILL.md` — currently no rule about ledger timestamp ordering.

Proposed: add to "Run state layout" section (line 41-57):
```
**Append-only-monotone**: agent_registry.jsonl appends MUST have
timestamps >= the previous line's timestamp. Out-of-order appends
indicate coordinator post-compact confusion (Zeus topology-redesign
2026-05-06: entries 36+37 timestamps preceded entries 28-35).
Coordinator should re-read tail before append and refuse to append
older-timestamp rows.
```
Rationale: silent failure mode visible in this session's ledger; not surfaced in any verdict.

---

## §7 Verdict

```
session_quality_grade: A-
process_efficiency_grade: C
top_3_skills_improvements:
  - "~/.claude/skills/orchestrator-delivery/SKILL.md:253: insert per-phase commit gate (Δ1) — direct evidence registry entry 21 stash recovery"
  - "~/.claude/skills/orchestrator-delivery/references/MODEL_TIERING.md:15: change 'Not enforced' to enforced for execution/venue/contracts surfaces (Δ4) — direct evidence Phase 5 opus catching P5-M1 missed by 5 sonnets"
  - "~/.claude/skills/orchestrator-delivery/SKILL.md:141: promote resume probe from recommendation to startup gate (Δ3) — direct evidence invariants entries 3-5 three-strike namespaced-executor failure"
operator_decisions_pending:
  - "PR #72 (nav-v2): merge or hold? 8 review fixes already landed (commit 6751d5f2); no critic-opus closing pass on the review batch itself"
  - "Δ4 enforcement scope: should opus-critic auto-escalation also extend to riskguard/control/supervisor_api per AGENTS.md Tier 0 list, or stay narrower? Tradeoff is opus cost vs missed-P5-M1-shaped findings."
  - "Δ1 enforcement: hard-refuse next-phase dispatch on uncommitted state, or warn-and-allow? Hard-refuse breaks brief operator runs that intentionally batch."
```

Word count: 2,237.
