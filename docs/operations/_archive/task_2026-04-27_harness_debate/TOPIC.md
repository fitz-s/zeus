# Zeus Harness Debate — Topic Anchor

Created: 2026-04-27
Judge: team-lead@zeus-harness-debate-2026-04-27
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main, branch plan-pre5)
Cycle: ROUND-1 (pro/con verdict). Round-2 (alt-system proposals) reserved for later dispatch.

## Core question

Given Opus 4.7 (1M context window, released 2026-Q1) and GPT 5.5 capabilities as of 2026-04-27, is Zeus's current harness — defined below — **net-positive ROI for live-trading correctness**, or has the constraint surface itself become the dominant attention-drift cost?

## Harness scope (the "标的物" being judged)

| Surface | Size | Function |
|---|---|---|
| `architecture/*.yaml` | 29 files / 15,223 lines | invariants + topology + source_rationale + fatal_misreads + task_boot_profiles + module_manifest + code_review_graph_protocol + ... |
| `AGENTS.md` routers | 41 tracked-non-archive (72 total on disk including worktree clones; both debate sides verified) | per-directory routing + domain rules |
| Tracked `.md` (non-archive) | **357 files** (judge-verified 2026-04-28; original count of ~769 was overcount — included worktrees / archive bodies) | docs / ops / reference |
| `scripts/topology_doctor.py` | 1,630 lines | navigation / digest / planning-lock / map-maintenance facade |
| `r3/IMPLEMENTATION_PROTOCOL.md` | 465 lines / 14 anti-drift mechanisms / 12-step boot | governs 220-280h R3 plan execution |
| `r3/` debate harness itself | 3-region × 3-layer × multi-review × cross-cuts + judge ledger + per-phase boot evidence | active for current Ultimate Plan |
| Codebase being protected | 173 src .py / 241 tests .py / 135 scripts .py | the actual code (~ 1/4 the harness surface) |

## Three axes (non-exclusive — debate may weight differently)

1. **File cleanliness** — is the docs/yaml/architecture surface organized in a way that aids navigation, or has it accumulated entropy faster than it sheds? (e.g. ~769 .md files; 29 yaml manifests; 41 AGENTS.md routers; many `feedback_*` memories about harness drift itself)
2. **Agent runtime helpfulness** — do `topology_doctor`, AGENTS.md routers, slice cards, boot evidence files, multi-review folds actually reduce error rate per task on Opus 4.7 / GPT 5.5, or are they mostly ritual that modern long-context models route around?
3. **Topology system** — is the explicit topology (zones / invariants / negative_constraints / source_rationale / module_manifest / fatal_misreads) actually enforceable law via tests/CI, or LARP-as-law that decays faster than it's maintained?

## Required engagement

Each side MUST:

- **(a) Repo evidence** — cite concrete `architecture/`, `r3/`, `RETROSPECTIVE_2026-04-26.md`, debate-caught BUSTED-HIGH wins (5+8 in Down region), process-failure self-reports (7 in retrospective), and the per-phase boot evidence files. file:line citations grep-verified within 10 min.

- **(b) External reality** — ≥2 WebFetch per round (teammates self-decide upper limit). Suggested sources:
  - Anthropic public posts on agent harness / scaffolding / long-context (2025-2026)
  - Cognition Labs Devin / Replit Agent / Cursor / GitHub Copilot Agent architecture writeups
  - Published benchmarks on long-context navigation vs structured navigation
  - HRT / Jane Street / quant industry reports on engineering harness ROI (if obtainable)
  - GPT-5.5 / Opus 4.7 capability claims and limitations

- **(c) Concession bank lock** by R2 close (each side records what opponent argued that they concede, what remains contested, what is unresolvable from current evidence)

## R-format (this debate cycle only)

| Round | Content | Disk artifact |
|---|---|---|
| Boot | Each side reads context, writes _boot_{role}.md, SendMessage BOOT_ACK | `evidence/{role}/_boot_{role}.md` |
| R1 | Opening + initial external evidence (≥2 WebFetch each) | `evidence/{role}/R1_opening.md` |
| R2 | Rebuttal + second-round evidence + concession bank lock | `evidence/{role}/R2_rebuttal.md` |
| Final | Judge writes verdict + (if net-negative) requires winner subtraction list, loser dissent | `verdict.md` |

## Out of scope (reserved for round-2 debate cycle)

- "More advanced system" alt-proposals (post-verdict, separate dispatch — teammates persist as longlast)
- Concrete migration / sunset planning
- Implementation cost estimation

## Token discipline

- ≤500 char/A2A turn; ≤200 char converged statement
- IDLE-ONLY bootstrap; substantive R1 only after team-lead dispatches
- Disk-first: every round writes to `evidence/{role}/RN_*.md` BEFORE SendMessage
- file:line citations grep-verified within 10 min before any contract lock
- WebFetch blocked → dispatch sub-agent with curl / different UA / alternate route (memory pattern: `feedback_on_chain_eth_call_for_token_identity`)
- Sequential turns: proponent R1 → opponent R1 → close R1; same R2

## Anti-rubber-stamp rules (per memory `feedback_critic_prompt_adversarial_template`)

- No "narrow scope self-validating" arguments
- No "pattern proven" without citing the specific test that proves it
- Each side must engage opponent's strongest point at face value before pivoting
- Concessions must be itemized (not "agreed in principle")

## Judge process

- team-lead maintains `judge_ledger.md`; does NOT participate substantively
- Routes cross-questions if either side raises one
- After R2 close, writes `verdict.md` synthesizing both sides
- If net-negative verdict: requires winner to propose subtraction list (which `architecture/*.yaml` files / `AGENTS.md` routers / `r3/` protocol layers to delete or merge); loser submits dissent
- If net-positive verdict: no forced action; verdict stands as durable record
- Round-2 alt-system debate is separate dispatch (later session)

## What a "win" looks like

This is NOT a vote. The judge weighs:
1. Engagement quality with opponent's strongest claim (not the strawman)
2. External evidence concreteness (cite quotes + URLs, not vague "industry consensus")
3. Repo evidence specificity (file:line + grep-verified, not "the architecture YAMLs feel bloated")
4. Acknowledgment of trade-offs (a side that admits no downside is not winning)
5. Survival under cross-examination (R2 rebuttal — does the position still hold after attack?)
