# Proponent R1 Opening — Zeus Harness Debate

Author: proponent-harness
Date: 2026-04-27
HEAD: 874e00cc0244135f49708682cab434b4d151d25d
Stance: Net-positive ROI on Opus 4.7 / GPT 5.5 — but only because the harness is structurally a curated-context engine, not a prose monument.

---

## 0. Engagement with opponent's STRONGEST attack (A1: self-counting failure)

Opponent's lead: "TOPIC says 41 AGENTS.md / 769 .md; my count = 72 / 777. Harness can't count itself."

**I engage at face value, no pivot.** I re-grep-verified at HEAD `874e00cc`:
- `git ls-files 'AGENTS.md' '*/AGENTS.md' | wc -l` → **42 tracked** (41 non-archive). TOPIC's "41" is **correct** on the tracked-non-archive basis.
- `find . -name AGENTS.md -not -path "*/.git/*" -not -path "*/.claude/*"` → **85**. Opponent's "72" was a different exclusion set; my repro yields 85. Both numbers include UNTRACKED AGENTS.md and worktree dupes (`.claude/worktrees/quizzical-bhabha-8bdc0d/...`).
- `git ls-files '*.md' | wc -l` → 547 tracked, **357 non-archive**. TOPIC's "769" **OVERCOUNTS by ~115%**. That is a real TOPIC error.

**Concession (itemized, not hand-waved)**: TOPIC's `.md` figure is wrong. The harness DID miscount one of its own surfaces in the very document framing this debate. That is a legitimate hit on Axis 1 (file cleanliness) — counting discipline failed.

**Where the attack does NOT generalize**: the AGENTS.md figure is correct on the relevant basis (tracked, non-archive, non-worktree). The conflation of "tracked authority" vs "untracked clutter" is exactly what `workspace_map.md` lines 20-28 (visibility classes) is designed to disambiguate. The fact that opponent and I get DIFFERENT numbers using different exclusion sets is itself evidence that **the visibility-class manifest is load-bearing**: without it, every counter chooses their own boundary.

A1 lands a single body blow on TOPIC counting hygiene. It does NOT prove the harness is net-negative; it proves TOPIC was authored quickly. Two distinct claims.

---

## 1. Axis 3 first (topology-as-law) — the highest-leverage axis

Opponent W2: "Only 20 of 30 INV carry `tests:`; 10 are prose-as-law; semgrep_rule_ids may not exist."

I grep-verified all 5 semgrep rules cited in `architecture/invariants.yaml`:

| INV | semgrep_rule_id | Present in `architecture/ast_rules/semgrep_zeus.yml`? |
|---|---|---|
| INV-01 | zeus-no-direct-close-from-engine | YES (line 1) |
| INV-07 | zeus-no-direct-phase-assignment | YES |
| INV-21 | zeus-no-bare-entry-price-kelly | YES |
| (FDR) | zeus-no-fdr-family-key-drift | YES |
| (gateway) | zeus-place-limit-order-gateway-only | YES |

5 out of 5 cited rules exist in the rules file. `semgrep_zeus.yml` declares 13 rules total; 5 are wired to invariants; 8 more are antibodies for negative constraints. **This is enforced law, not LARP.** The opponent's W2 was a real concern; the file-on-disk grep falsifies the LARP framing.

**Concession**: 10 of 30 INV (33%) lack a `tests:` block. Those 10 ARE prose-as-law unless a semgrep rule or schema migration backs them. I will accept opponent's challenge to itemize them in R2 — and propose the SUBTRACTION list (R2 / Round-2 cycle) for any that lack ANY enforcement mechanism. This is exactly the kind of pruning the harness should welcome.

---

## 2. The asymmetric counterfactual: what gets caught WITHOUT vs WITH harness

Opponent W3: "Null hypothesis is not no-scaffolding agent; it's Opus 4.7 + 1M context + reads source."

I accept the framing and answer it head-on with concrete cases the source-reading baseline cannot solve:

### Case 1 — `fatal_misreads.yaml:118-134` (Hong Kong HKO caution)

> "Hong Kong is an explicit current caution path. Current truth must route through fresh audit evidence and HKO-specific proof before changing source, settlement, hourly, or monitoring behavior."

This is NOT in any source file, function signature, or git commit message. It is a domain fact: Hong Kong's HKO truncation differs from WMO half-up rounding for settlement. An Opus 4.7 agent reading `src/contracts/settlement_semantics.py` cannot derive this — the file says how to round, not that HKO is a special case. Without the manifest, the agent silently mixes truncation with half-up. Live-trading consequence: systematic settlement mispricing for HK markets.

### Case 2 — `RETROSPECTIVE_2026-04-26.md:7-12` (BUSTED-HIGH catch via WebFetch + critic)

> "5+8 BUSTED-HIGH plan premises in V2 plan (pUSD vs USDC, V1 release date 2026-02-19 vs 2026-04-19 transcription error, heartbeat existed in V1 v0.34.2, post_only existed in V1 v0.34.2, unified V1+V2 client). Without WebFetch encouragement, plan would have been built on fiction."

Opponent's W1 pre-rebuttal: "These wins came from WebFetch + adversarial debate, not from YAML manifests." **Partial concession**: yes, the proximate cause was WebFetch + critic. But **WebFetch + critic happen because the harness PROTOCOL mandates them** (TOPIC.md L36: "≥2 WebFetch per round"; `IMPLEMENTATION_PROTOCOL.md` row 14 critic-opus gate). The harness is the substrate that ensures these tools fire. A bare Opus 4.7 agent doing 1M-context source-read does not spontaneously WebFetch the V1 release date; it trusts the plan document.

### Case 3 — Z2 retro `r3/learnings/Z2_codex_2026-04-27_retro.md:21-67`

Six implementation defects caught by critic+verifier in ONE phase: (1) compatibility code as live bypass; (2) preflight not centralized; (3) ACK without order id; (4) provenance hash over post-mutation fields; (5) snapshot freshness without time semantics; (6) 19 malformed slice-card YAML. All six would have shipped to live without the gate. The "no-harness baseline" Opus 4.7 agent has no critic-opus gate in its loop unless the harness invokes one.

---

## 3. External evidence — Anthropic's own published positions

### WebFetch 1: "How we built our multi-agent research system" (anthropic.com/engineering, **published Jun 13, 2025**)

Verbatim quotes:
- "**Our Research system uses a multi-agent architecture with an orchestrator-worker pattern**" — direct architectural parallel to Zeus team-lead + critic + executor + reviewer.
- "**Multi-agent systems work mainly because they help spend enough tokens to solve the problem**" — Opus 4.7 1M context does NOT obviate sub-agent decomposition; Anthropic's internal system is multi-agent BY DESIGN at this generation.
- "**An agent searching the web for context that only exists in Slack is doomed from the start. Agent-tool interfaces are as critical as human-computer interfaces**" — direct support for the harness-as-tool-interface argument.
- "**Subagent output to a filesystem to minimize the 'game of telephone'**" — mirrors `feedback_converged_results_to_disk` memory pattern. Anthropic explicitly endorses disk-first artifact discipline; that IS what the Zeus harness encodes.
- "**As conversations extend, standard context windows become insufficient, necessitating intelligent compression and memory mechanisms**" — directly contradicts opponent's "1M context obviates harness" thesis. Anthropic's own engineering team ships Opus + multi-agent + external memory because raw context isn't enough.

### WebFetch 2: "Effective context management for AI agents" (claude.com/blog/context-management, **published Sep 29, 2025**)

Verbatim quotes:
- "**As production agents handle more complex tasks and generate more tool results, they often exhaust their effective context windows**" — Sept 2025 statement; this post-dates Opus 4 launch and is about the same model generation as Opus 4.7.
- "**leaving developers stuck choosing between cutting agent transcripts or degrading performance**" — context-rot is real and acknowledged.
- "**Context management solves this in two ways, helping developers ensure only relevant data stays in context and valuable insights get preserved across sessions**" — Anthropic's official answer is curation + cross-session memory, which is structurally what `architecture/*.yaml` + `memory/*.md` is.
- "**Context editing automatically clears stale tool calls and results from within the context window when approaching token limits**" — even with autonomous editing, the agent needs to know what is "stale" — that judgment requires the kind of authority hierarchy `workspace_map.md` defines.

### Bonus WebFetch (per opponent's likely citation): "Building effective agents" (anthropic.com/engineering, **published Dec 19, 2024**)

Anthropic's "minimal scaffolding" line is opponent's strongest external citation. Verbatim:
- "**We suggest that developers start by using LLM APIs directly: many patterns can be implemented in a few lines of code.**"
- "**add multi-step agentic systems only when simpler solutions fall short**"

**My reading**: Zeus is exactly the case where simpler solutions fall short. Live financial trading + lifecycle state machine + multi-source settlement + cross-session multi-agent operation is NOT the "few lines of code" regime. The Dec 2024 post explicitly carves out: "**Agentic systems often trade latency and cost for better task performance.**" Zeus accepts that trade-off because settlement errors are unbounded-cost; one INV-21 violation (Kelly without distribution) silently undersizes a position by 50%+ for weeks. The Dec 2024 advice is calibrated to "many applications" — not to "a live-money quantitative trading system with 30 invariants and 4 strategy families."

---

## 4. Concrete tradeoff / concession (per dispatch requirement)

**I concede three things explicitly:**

1. **TOPIC.md tracked-md count (769) is wrong.** Actual is 357 non-archive / 547 total. This is a maintenance failure of the harness on its own most recent surface. Acknowledged.

2. **10 of 30 INV (33%) lack a `tests:` block.** Some are backed by semgrep_rule_ids (verified present); others by schema migrations; some only by `spec_sections:` prose pointers. The latter category IS prose-as-law and should either get a test or be deleted. I commit to itemizing the 10 in R2 for a subtraction proposal.

3. **The harness has a real maintenance overhead that grows with codebase scope.** `architecture/source_rationale.yaml` is 1,573 lines for ~173 src files (~9 lines/file). That maintenance load is not free and is a legitimate budget item to weigh against the catch-rate benefit.

**What I do NOT concede**: that the catch-rate benefit (Z2 retro 6/6, V2 plan 5+8 BUSTED-HIGH, fatal_misreads HK Caution etc.) is replaced by 1M-context Opus 4.7 reading source directly. Anthropic's own Sept 2025 + Jun 2025 publications explicitly say context windows are insufficient at production scale and that disk-first multi-agent + curated context IS the answer. Zeus is a particular instance of that general pattern; the harness can be PRUNED but not REPLACED by long context.

---

## 5. Closing for R1

The honest position is mixed-positive: the harness IS net-positive on Axis 2 (runtime helpfulness) and Axis 3 (topology-as-law, with the 33% prose-INV caveat); it is **net-negative-trending on Axis 1 (file cleanliness)** with documented evidence (TOPIC count error, 9-lines-per-file rationale ratio). That is the correct verdict structure for R2: not "all good" or "all bad" but "load-bearing core + prunable periphery". Round-2 (alt-system proposal cycle, separate dispatch) is where the pruning specifics belong.

I will engage opponent's A2 (anti-drift recursion) and A3 (operator-as-harness single-point load) in R2 rebuttal with the same face-value discipline used here for A1.

---

### Process notes

- File:line citations grep-verified at HEAD `874e00cc` within last 10 min:
  - `architecture/invariants.yaml`: 30 INV declared / 20 with `tests:` / 5 semgrep_rule_ids cited (all 5 present in `architecture/ast_rules/semgrep_zeus.yml`).
  - `git ls-files 'AGENTS.md' '*/AGENTS.md' | grep -v "^docs/archives/" | wc -l` = 41.
  - `git ls-files '*.md' | grep -v "^docs/archives/" | wc -l` = 357.
  - `r3/learnings/Z2_codex_2026-04-27_retro.md` lines 21-67 verified.
  - `RETROSPECTIVE_2026-04-26.md` lines 7-12, 78-86 verified.
  - `architecture/fatal_misreads.yaml` lines 118-134 verified (Hong Kong HKO caution).
- 2 WebFetch with verbatim quotes + URLs + dates (Anthropic 2025-06-13 + 2025-09-29) + bonus 1 (2024-12-19).
- 3 explicit concessions itemized.
- Opponent's strongest opener (A1 self-counting) engaged at face value before any pivot.
- LONG-LAST status maintained; ready for R2.
