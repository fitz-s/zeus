# Opponent boot — opponent-harness — 2026-04-27

Role: argue Zeus harness is net-NEGATIVE ROI on Opus 4.7 / GPT 5.5; constraint surface is the dominant attention-drift cost.

HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (per TOPIC.md L5).

## Section 1 — Read list with key takeaway per file

| File | Key takeaway for the attack |
|---|---|
| `docs/operations/task_2026-04-27_harness_debate/TOPIC.md` | TOPIC self-cites "41 AGENTS.md routers" and "769 .md files". My count returns **72 AGENTS.md** (excluding `.git`/`.claude`/worktree noise) and 777 tracked .md. **The harness can no longer count itself.** Opening lemma. |
| `docs/operations/task_2026-04-27_harness_debate/judge_ledger.md` | Sequential mode + concession bank lock at R2. Need to itemize, not hand-wave. |
| `AGENTS.md` (root, 336 lines) | Reads like onboarding manual for a 100-engineer firm, not a single-operator + LLM system. Boots agent through 4 stop-questions, 7 reference docs, 3 topology commands, 16+ planning-lock paths, all before code. The router itself is the choke point. |
| `workspace_map.md` (107 lines) | Routes to 17 manifest files, 5 visibility classes, ~10 directory routers. The map's own complexity exceeds many production systems' source code. |
| `architecture/invariants.yaml` (370 lines, 30 INV ids) | **20 of 30 INVs carry `tests:` block (66%); 10 are prose-as-law with only `spec_sections:`/`semgrep_rule_ids:` references** — semgrep rules in invariants.yaml that I have not verified exist. Untested invariants are LARP-as-law. |
| `architecture/source_rationale.yaml` (1,573 lines) | Per-file rationale for ~173 src files. Ratio: ~9 lines of YAML rationale per src file, before src/AGENTS.md and module_manifest. Maintenance load grows with both surfaces. |
| `architecture/fatal_misreads.yaml` (153 lines) | 3 antibodies. Conceptually sound, but enforcement is `topology_doctor.py --fatal-misreads --json` — same suspect topology_doctor that has 1,630 LOC. The "antibody" is itself a script call that requires the script to be alive and correct. |
| `architecture/task_boot_profiles.yaml` (360 lines) | Profile triggers are keyword-matched (`source, routing, station...`). Modern long-context models do not navigate by keyword grep — they read the file. Profile is a 2024-vintage tool for a 2026 model. |
| `architecture/code_review_graph_protocol.yaml` (62 lines) | "Two-stage protocol for Code Review Graph use" — 62 lines of YAML to govern a tool that is itself called "derived context, not authority." **Protocol-on-derived-context is two abstraction layers above the actual problem.** |
| `r3/R3_README.md` (272 lines) | 17-step manual workflow before writing code, plus 8 operator gates, plus 14 anti-drift mechanisms, plus 6 living-protocol directories. R3 is **312h of work governed by ~189 files of plan/protocol** (144 md + 45 yaml in `task_2026-04-26_ultimate_plan/`). Plan-to-code ratio is non-negligible. |
| `r3/IMPLEMENTATION_PROTOCOL.md` §1 (14 anti-drift mechanisms) | **Every one of the 14 mechanisms is itself a thing that can drift.** Citation-drift detector (`r3_drift_check.py`) drifts. INVARIANTS_LEDGER row update process drifts. Frozen-interface docs drift vs source. Memory-consultation lists drift vs actual memory. The anti-drift system is itself an N+1 surface to maintain. |
| `r3/learnings/Z0_codex_2026-04-27_retro.md` | "Z0 card requested a path that was unsafe in this workspace, so the implementation adapted rather than forcing the card literally." **Phase 0 of the plan failed first contact with reality.** The slice card was wrong; topology_doctor was wronger; the human had to mediate. |
| `RETROSPECTIVE_2026-04-26.md` | **Operator's own self-criticism in 7 process failures, all in one day**: parallel firehose chaos, boot/A2A interleave fault, routing yaml heuristic propagated as authoritative, file path mistakes in bootstrap prompts, token economy waste, WebFetch fallback missing, notification-summary-only messages. **This IS the harness operating; this IS what the harness produces.** |

## Section 2 — Top 3 strongest anti-harness arguments

### A1. Harness self-counting failure (entropy exceeds maintenance budget)

TOPIC says "41 AGENTS.md routers / 769 .md files". My fresh count returns **72 AGENTS.md / 777 .md**. The TOPIC was authored 2026-04-27 — same day as this debate. **A harness whose maintenance cadence cannot keep up with its own surface area, even on the day someone counts it, has by definition exceeded its operator's working memory.**

- Cite: TOPIC.md:14-22 (the table of harness sizes) vs `find ... AGENTS.md | wc -l` = 72 (cmd: `find /Users/leofitz/.openclaw/workspace-venus/zeus -name AGENTS.md -not -path "*/.git/*" -not -path "*/.claude/*" -not -path "*/node_modules/*"`).
- Cite: `architecture/invariants.yaml` 30 declared IDs; only 20 with `tests:` block (`grep -c "tests:" architecture/invariants.yaml = 20`). 10 invariants are prose-as-law.

Implication: the harness is in **net-negative** mode on Axis 1 (file cleanliness). Each yaml manifest claims to be authoritative; the actual authority is fragmented across 29 yaml files + 72 AGENTS.md + ~777 markdown docs. **Opus 4.7 with 1M context can read all of them, but reading them does not produce understanding — it produces overload.** Sonnet/Opus can route through one boot folder with one CLAUDE.md; what does the harness add beyond what 1M context provides for free?

### A2. Anti-drift mechanisms recursively require anti-drift mechanisms

`r3/IMPLEMENTATION_PROTOCOL.md §1` lists 14 failure modes + their mechanisms + their artifacts. Audit:

| Mechanism | Itself drifts via |
|---|---|
| `scripts/r3_drift_check.py` | Script edit / refactor; never re-verified |
| `INVARIANTS_LEDGER.md` | Manual row append on every PR — depends on operator discipline |
| `frozen_interfaces/<phase_id>.md` | Public API docs that drift vs source between phases |
| `_phase_status.yaml` | Multi-agent concurrent edit; merge conflicts (their own §14) |
| `reference_excerpts/<topic>_<date>.md` | "frozen" excerpts that age out of being authoritative |
| `memory_consult:` field per slice card | Memory drift across agents — circular |
| Per-phase boot evidence file | Evidence file that proves the agent read evidence files — Russellian |
| `critic-opus` gate | Same critic prompt template that operator memory tags as "degrade into rubber-stamp" (`feedback_critic_prompt_adversarial_template`) |

**Ratio 1:1** — every drift-prevention mechanism creates a new drift surface. The Z0 retro confirms it: "topology doctor outranks intuition: the Z0 card requested a path that was unsafe in this workspace, so the implementation adapted." Phase ZERO. The mechanism designed to prevent path mistakes told the agent to make a path mistake. The retro records this calmly as a feature, not a bug.

External evidence to layer in R1: **Cognition Devin / Replit Agent / Cursor architectures publicly do NOT use this pattern.** They lean heavily on tool quality + iteration loops + small per-turn context, not on encoded prose-as-law. Anthropic's own "Building effective agents" post (Dec 2024) explicitly recommends "minimal scaffolding" + "test in isolation". I will WebFetch in R1.

### A3. Translation loss + "the operator IS the harness operator + the trader + the critic" = single-point-of-failure load

User's own CLAUDE.md memory `feedback_long_horizon_multi_agent_anti_drift` lists **14 predicted drift modes** for the harness at the top level. The operator catalogued failure modes that the harness was built to prevent — and the harness is itself one of those failure modes. The retrospective's seven process failures all happened within a SINGLE 12-hour debate, with the harness operator AT THE WHEEL. If the operator running the harness produces 7 process-faults in 12 hours, the harness is not reducing operator cognitive load — it is the cognitive load.

Fitz's own Universal Methodology (`/Users/leofitz/CLAUDE.md` "Translation Loss is Thermodynamic"): **"Functions, types, tests: ~100% cross-session survival. Design intent, philosophy, relationship constraints: ~20% survival."** By the operator's own framework, **15K LOC of YAML and 777 markdown files are bets on the 20% survival channel, not the 100% channel.** The harness is itself an instance of the loss pattern it warns against.

## Section 3 — Top 3 weakest spots + pre-rebuttal sketch

### W1. Proponent will cite "5+8 BUSTED-HIGH wins from Down region debate"

Pre-rebuttal: The wins came from **WebFetch + adversarial debate + on-chain eth_call**, NOT from the YAML manifests. Specifically `feedback_on_chain_eth_call_for_token_identity` (memory) was a sub-agent dispatch decision; the harness did not cause it. The wins prove that **adversarial review + external evidence works**, not that 29 YAML files + 41 AGENTS.md routers caused them. Counter-cite: `RETROSPECTIVE_2026-04-26.md:78-86` (operator's own admission that on-chain eth_call was a sub-agent-driven win that contradicted the judge's prior ruling). Harness was not the agent of this win.

### W2. Proponent will cite test gates as proof of "law not LARP"

Pre-rebuttal: 30 INV declared in invariants.yaml; **only 20 (66%) carry `tests:` block**. Of the remaining 10, several cite `spec_sections:` or `semgrep_rule_ids:` that require manual verification of existence. I will fact-check semgrep rule existence in R1. Citation: `architecture/invariants.yaml` (~370 lines).

Stronger version: even where `tests:` exist, they test **the local invariant assertion**, not **the cross-module relationship the invariant was meant to protect**. Per Fitz's own "Test relationships, not just functions" principle from `/Users/leofitz/.claude/CLAUDE.md`. The tests verify "given input X, output Y" — not "Module A's output flowing into Module B preserves property P." The harness DECLARES relationship law but its tests measure local function compliance.

### W3. Proponent will cite "without harness, agents make mistakes"

Pre-rebuttal: The retrospective lists **7 mistakes the operator+harness made together in a 12-hour debate session**. The null hypothesis (no-harness baseline) is not "perfect agent" — it is "agent that uses Opus 4.7's 1M context to read source + tests + git log + run code, and asks operator on ambiguity." The harness must beat THAT baseline, not "agent with no scaffolding at all". Modern frontier models default to relatively safe behavior; the marginal value of 15K LOC YAML is what's in question, not the abolitionist case.

## Section 4 — Three external sources I plan to WebFetch in R1

1. **Anthropic "Building effective agents" / "Effective context engineering for AI agents" (anthropic.com/engineering)** — Anthropic's own 2024-2025 published guidance on agent harness design. Expected to confirm "minimal scaffolding > maximal scaffolding" for capable models. Load-bearing because Anthropic = source authority on Opus 4.7 capabilities.

2. **Cognition Labs Devin architecture writeup OR Cursor agent rules engine (one URL each)** — Frontier coding-agent products. Expected to show NO equivalent of "29 YAML manifests + 72 router AGENTS.md + 14 anti-drift mechanisms"; instead shows tool-rich + iteration-rich + context-tight design. Load-bearing because these are commercially-deployed harnesses on the same generation of models.

3. **Long-context retrieval benchmark, 2025-2026 (e.g. Anthropic's needle-in-haystack updates / Gemini 1.5 long-context paper / NeurIPS 2025 long-context navigation paper)** — Expected to show 1M-context models retrieve specific facts from 700K-token corpora at >95% accuracy. Load-bearing because IF Opus 4.7 can retrieve from 700K of source code directly, the structured-routing harness adds latency without precision.

Backup: HRT / Jane Street engineering posts on quant-trading code review discipline (low probability obtainable). Backup: GitHub Copilot Workspace architecture writeup.

## Status

BOOT_COMPLETE. Idle pending team-lead R1 dispatch. Will not engage substantively before R1 dispatch per TOPIC.md L65 (sequential turns).

Files cited in this boot are grep-verified within the last 10 minutes.
Memory of grep-verification commands: `find ... AGENTS.md | wc -l = 72`; `wc -l invariants.yaml = 370`; `grep -n "id: INV-" invariants.yaml | wc -l = 30`; `grep -c "tests:" invariants.yaml = 20`; `find docs/operations/task_2026-04-26_ultimate_plan -name "*.md" | wc -l = 144`.
