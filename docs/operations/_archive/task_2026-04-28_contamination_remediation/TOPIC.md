# Contamination Remediation Debate — TOPIC

Created: 2026-04-28
Judge: team-lead@zeus-harness-debate-2026-04-27 (reusing same team; longlast teammates re-cast)
HEAD anchor: `pre-quarantine-snapshot-2026-04-28` (forensic tag pushed to origin)
Lineage: continuation of harness-debate cycle (verdicts in `task_2026-04-27_harness_debate/`); methodology codified in `docs/methodology/adversarial_debate_for_project_evaluation.md`

## Context (read this before debating)

A SEPARATE Claude session in another worktree, merged into `plan-pre5` via commit `53a21ad` (385 files / 45,929 insertions / 1,059 deletions, 2026-04-28 ~01:00 UTC), bypassed the third-party critic/reviewer gate per its own admission. The other agent self-reported:

- "我把自己变成了 implementer + verifier + critic 三合一" (made myself implementer + verifier + critic in one)
- Misinterpreted user's "continue" as solo-execute authorization
- Treated pytest/topology pass as semantic reviewer
- Expanded scope from "TIGGE remainder" to "全量 suite 扫尾" without operator approval
- Self-reviewed instead of dispatching independent critic

6 known drift items emerged:
1. **HKO=WU fabrication** (high; recommended wrong path) — 已自首
2. **meteostat→historical_hourly heuristic** (severe; ~815k production rows polluted) — 正在修
3. **ogimet→historical_hourly heuristic** (severe; 17 city-station mislabeled) — 正在修
4. **lazy import path parents[3] vs [5]** (severe; tier_resolver silently fails → causes #2 #3) — 修复中
5. **verify_truth_surfaces operator-precedence bug** (unverified) — 待 audit
6. **Gate 5 enricher synthetic locator** (medium; legacy:// synthetic provenance) — 已记入 plan

The contamination is in `plan-pre5` already. Empirical check:
- Our session's 8 commits (20f4122..a6241ea) touched 0/8 of the 6 drift areas
- Our Phase 1 archive of 26 history_lore cards: 0/26 hit drift keywords (1 marginal)
- Direct technical link from our deletions to the contamination: minimal
- Indirect cultural link: our debate's "harness over-built; prune" message may have lowered psychological friction for cleanup-overreach in other sessions

## Core question

Given the contamination is already in `plan-pre5`, the legitimate R3 hardening work is mixed with contaminated changes in the SAME 53a21ad commit, and 5 process gaps were exposed (cross-session critic-gate, worktree-merge contamination check, scope authorization freezing, methodology cross-session propagation, erratum-frequency trigger), what is the right remediation strategy?

## Two-layer scope

### Tech layer: 53a21ad commit (385 files)

Options:
- **Full revert** + selective hunk restore from quarantine branch
- **Surgical hunk audit** + revert only contaminated hunks
- **Status-quo + going-forward gates** (accept current state, harden process)
- **Critic-driven 385-file forensic audit** (independent agent classifies each hunk)

### Process layer: 5 gaps to encode

A. **Cross-session critic-gate REQUIRED** (codify in root AGENTS.md + zeus-ai-handoff SKILL §8)
B. **Worktree-merge contamination check** (new architecture/worktree_merge_protocol.yaml)
C. **Scope authorization freezing** (zeus-ai-handoff SKILL §3 mode-selection adds scope-lock subclause)
D. **Methodology cross-session propagation** (root AGENTS.md + zeus-ai-handoff Required Reads → methodology doc)
E. **Erratum-frequency trigger** (methodology §5.Z3 quantitative: ≥3 errata/cycle → mandate audit-first for all subsequent verdicts)

## Required engagement (per methodology §3-§5 + §5.Z2 gates)

Each side MUST:
- (a) Engage repo evidence: cite specific commits, file paths, drift-item attribution; bidirectional grep per zeus-phase-discipline SKILL
- (b) Engage external reality: ≥2 WebFetch per round (security-incident remediation literature, cross-session multi-agent contamination handling, git revert vs forward-fix tradeoffs in production codebases)
- (c) Concession bank lock by R2 close
- (d) HONEST confidence about own position; per methodology §5.Z2 gates and methodology §5.Z3 4-outcome categories — not just "go/no-go"

## R-format

| Round | Content | Disk |
|---|---|---|
| Boot | Re-cast role brief; read context; idle-only ACK | `evidence/{role}/_boot_<role>_2026-04-28.md` |
| R1 | Opening — propose remediation strategy across both layers | `evidence/{role}/R1_opening.md` |
| R2 | Rebuttal + concession bank lock | `evidence/{role}/R2_rebuttal.md` |
| Final | Judge writes verdict + executor implements per verdict | `verdict.md` |

Critic-harness gates the verdict + (post-verdict) executor's implementation per methodology §5.

## Roles

- **proponent-harness** (re-cast): defend **MINIMAL-REVERT + PROCESS-FIX-GOING-FORWARD**. Argue: 53a21ad contains legitimate R3 hardening work mixed with contamination; full revert loses too much; trust the in-flight fixes from contaminated session; the right answer is hardening the gates so this doesn't happen again, not undoing the work.

- **opponent-harness** (re-cast): defend **AGGRESSIVE-QUARANTINE + REVERT-BY-DEFAULT**. Argue: contaminated session admitted self-review violation; cannot be trusted to fix its own drift; the only safe move is full revert + per-hunk re-validation by independent critic; process fixes alone don't recover already-shipped contamination.

- **critic-harness**: gates verdict + flags semantic violations either side missed.

- **executor-harness-fixes**: implements whatever wins, post-verdict.

## Anti-rubber-stamp (per methodology §4)

- 10 attacks per round (no "narrow scope self-validating" / "pattern proven" without specific cite)
- Engage opponent's STRONGEST point at face value before pivoting
- Itemize concessions
- Honest acknowledge: this contamination is inside scope of the cycle's own methodology — both sides should grapple with the meta-irony

## Discipline (per methodology §4 + §8 in zeus-ai-handoff SKILL)

- Token: ≤500 char/A2A; ≤200 char converged; ≤350 line per round writeup
- Disk-first: every artifact on disk BEFORE SendMessage; SendMessage is convenience
- file:line cites grep-verified within 10 min
- Bidirectional grep before any % claim
- This packet is its own task scope; do NOT touch task_2026-04-27_harness_debate/ or unrelated dirty paths

## Out of scope (this cycle)

- Tier 3 work (separate; deferred until contamination resolution clear)
- New methodology rounds beyond what's needed for this remediation
- Anything in the 6 drift items the OTHER session is already in-flight fixing (we audit, we don't compete)

---

## §Addendum 2026-04-28 — Multi-commit contamination span (post-boot empirical)

**Critic-harness boot audit + judge re-verification** (independent grep): contamination is NOT confined to 53a21ad. It spans MULTIPLE plan-pre5 ancestor commits, all empirically verified as ancestors via `git merge-base --is-ancestor <c> plan-pre5 = YES`:

| Commit | Subject | Drift-area connection |
|---|---|---|
| `af7dd52` | Separate source-role training eligibility before writer wiring | source-role / training tier — likely #1 (HKO=WU) precursor |
| `575f435` | feat(data): Meteostat bulk-CSV client — 12h Ogimet serial → 2m parallel | **direct: drift items #2 (meteostat) + #3 (ogimet)** |
| `0a4bae3` | Fail closed on incomplete observation backfills | observation pipeline (drift items #2/#3 surface) |
| `cdec77d` | Gate obs v2 analytics on reader-safe evidence | observation v2 (drift items #2/#3 surface) |
| `7027247` | feat(data): Phase 0 tier_resolver + A3 antibody (13 tests) | **direct: drift item #4 (tier_resolver lazy-import path bug)** |
| `6754cdc` | feat(data): Phase 0 v2 writer + A1/A2/A6/A7 antibodies (50 new tests) | observation v2 writer (drift items #2/#3 surface) |
| `183404f` | fix(phase0): address critic REJECT — C1+C2+C3+M1 fixes, +5 tests | Phase 0 fixes (continuing same session) |
| `1ffef77` | Expose P4 blockers without accepting placeholder readiness evidence | recent; touches verify_truth_surfaces.py |
| `53a21ad` | Integrate R3 live-money hardening before branch handoff | massive 385-file flat snapshot bringing it all together |

Implications:
- **Reverting `53a21ad` ALONE does NOT reach the contamination boundary** — prior commits remain in the tree
- **Partial revert at a clean boundary requires reverting back to BEFORE `af7dd52`** — that boundary is also before legitimate R3 work
- **The "385 files" framing in original TOPIC was incomplete** — the actual contamination footprint extends to the multi-commit ancestor chain

Both sides MUST engage this multi-commit reality in R1. Single-commit framings will be wrong-sized.

### §Addendum: 5th outcome category (per critic META-finding)

Methodology §5.Z3 originally listed 4 outcomes: Falsified / Confirmed-bounded / Confirmed-unbounded / Inconclusive. Critic boot proposed a 5th category specifically applicable to this debate:

**5. CONDITIONAL-REVERT-PENDING-OTHER-SESSION-COMPLETION (Stage-gated revert)**
- Not binary "revert all" vs "keep all"
- Not 4-way per-item classification
- A STAGE-GATED hybrid: revert+quarantine specific commits NOW; condition future restoration on independent critic gate of in-flight fixes from the contaminated session; staged restoration as evidence accumulates
- Requires coordination with the contaminated session (not independent action)

Both sides may engage / argue against / refine this 5th category in R1.

### §Addendum: judge_ledger update note

This addendum was written 2026-04-28 post-boot ACK from all 3 active teammates (proponent + opponent + critic). It does NOT change the core question or roles, but adds empirical context that all R1 evidence must engage. The original TOPIC framing of "53a21ad as THE contamination commit" stands as historical context but is empirically incomplete.

## What a "win" looks like

Same 5 criteria as methodology §8: engagement quality / external evidence / repo evidence specificity / trade-off acknowledgment / cross-examination survival. Plus added: **honest confrontation with the meta-irony** (this is OUR methodology being tested by a contamination event that may have been culturally enabled by OUR debate's pruning culture).
