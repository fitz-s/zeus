# Proponent Boot — Contamination Remediation 2026-04-28

Role (re-cast): proponent-harness defending **MINIMAL-REVERT + PROCESS-FIX-GOING-FORWARD**
HEAD anchor: `pre-quarantine-snapshot-2026-04-28`
Methodology lineage: 5th cycle (R1+R2+R3+Tier 2 prior; this debate operates on already-contaminated state per §5.Z3 4-outcome categories)
Stance: 53a21ad mixes legitimate R3 hardening with contamination; full revert loses too much load-bearing R3 work; trust in-flight fixes from contaminated session for the 6 known drift items; encode the 5 process gaps (A-E) so this category becomes impossible going forward.

---

## §1 Read list + key takeaways

| File | Key takeaway for MINIMAL-REVERT defense |
|---|---|
| `task_2026-04-28_contamination_remediation/TOPIC.md` | 4 tech-layer options: full-revert / surgical-hunk / status-quo+gates / critic-driven 385-file forensic. 5 process-layer gaps (A-E). My role: defend status-quo+gates + process-fix. Meta-irony explicitly in scope (this contamination may have been culturally enabled by my own prior debate's "ruthlessly prune" message). |
| `task_2026-04-28_contamination_remediation/judge_ledger.md` | Empirical baseline from forensics: our session 0/8 commits touched drift areas; Phase 1 archive 0/26 cards hit drift keywords; pytest baseline 90/22/0 preserved through entire prior session. Direct technical link from our deletions to contamination = MINIMAL. Indirect cultural link = possible. |
| `methodology/adversarial_debate_for_project_evaluation.md §5.Z2` | 3-for-3 confirmation pattern: debates that prescribed structural changes ("33% LARP DELETE", "auto-gen registries", "Python module registries") were FALSIFIED by audit-first methodology; intent-aware audit prevented 5 mis-prescribed actions. Same pattern here: "full revert 385 files" needs intent-aware audit BEFORE locking, not blanket rollback. |
| `methodology/adversarial_debate_for_project_evaluation.md §5.Z3` | 4-outcome categories: Falsified / Confirmed-bounded / Confirmed-unbounded / Inconclusive. This debate's outcome should map to one of these per item, not a binary go/no-go. **Likely outcome distribution**: 6 drift items = Confirmed-bounded (in-flight fix appropriate); ~370 R3 hardening files = Inconclusive without per-hunk audit; some docs = Confirmed-unbounded keep. |
| `git log -1 --stat 53a21ad` | 385 files / 45,929 insertions / 1,059 deletions. **66 src files** include cutover_guard.py, heartbeat_supervisor.py, ws_gap_guard.py, polymarket_v2_adapter.py, collateral_ledger.py, risk_allocator/governor.py, harvester.py, cycle_runner.py — these are EXACTLY the R3 phases (Z1/Z2/Z3/Z4/A2/M5) my prior debate cycle's verdict §1.2 LOCKED as load-bearing core. **72 test files** include test_cutover_guard, test_heartbeat_supervisor, test_risk_allocator, test_harvester_dr33_live_enablement — the relationship tests that gate live-money correctness. |
| `pre-quarantine-snapshot-2026-04-28` tag | Forensic anchor pushed to origin; recovery commands documented. Tag preserves CURRENT state (incl. 53a21ad). All remediation deferred to multi-agent convergence. **53a21ad touches drift-area files: tigge_client.py + test_tigge_ingest.py confirmed; HKO/meteostat/ogimet/tier_resolver/verify_truth_surfaces TBD via per-hunk audit.** |
| Prior session R3 critique (`task_2026-04-27_harness_debate/evidence/proponent/round3_critique.md`) | My final position (~32% harness / ~68% edge over 6mo) was a SYNTHESIS toward middle, NOT pure-prune. Round-1+2 verdicts LOCKED 4-5 mechanisms as load-bearing core. The 53a21ad hardening commit IS partially the substrate (hooks, native agents, V2 adapter, cutover_guard) my own debate identified as substrate for safe edge work. Reverting it would undo my own debate's verdict. |

---

## §2 Top 3 strongest minimal-revert + process-fix arguments

### Arg-A — Empirical baseline: 53a21ad has more legitimate R3 hardening than contamination, by file count

**File-count empirical breakdown** (per `git diff-tree -r 53a21ad`):
- 385 files total
- 224 docs files (mostly r2/r3 evidence, plan docs, retrospectives) — LARGELY LEGITIMATE; these are session artifacts
- 66 src files: includes 30+ R3-phase implementations (cutover_guard, heartbeat_supervisor, ws_gap_guard, polymarket_v2_adapter, collateral_ledger, risk_allocator/governor, harvester, cycle_runner, control_plane, snapshot_repo, venue_command_repo, candidates/liquidity_provision_with_heartbeat) — LOAD-BEARING per my own R3 verdict §1.2
- 72 test files: relationship tests for the above + fake_polymarket_venue + harvester_dr33_live_enablement + heartbeat_supervisor — these are the antibody contracts (per round-2 verdict §K3 retained NC-NEW-A..J)
- 10 architecture files: invariants/topology/source_rationale/etc. — likely accumulated config + drift entries
- 13 other (config/state/scripts/etc.)
- **6 known drift-area files** (per TOPIC.md): tigge_client.py + test_tigge_ingest.py confirmed in commit; 4 others TBD per audit

**Ratio**: ~60-70 confirmed legitimate R3 substrate files vs 2 confirmed drift-area files (scoped to TIGGE) + 4 TBD. Even worst-case, contamination is < 5% of file count. Full revert = blast radius 10-30× the actual contamination footprint. Per Fitz Constraint #1 (structural decisions > patches) reverse-applied: **don't apply N-file rollback when K << N files are actually contaminated.**

**Anchor**: `git diff-tree --no-commit-id --name-only -r 53a21ad | wc -l = 385`; src/control/cutover_guard.py, src/control/heartbeat_supervisor.py, src/venue/polymarket_v2_adapter.py present in commit listing (verified via grep on diff-tree output).

### Arg-B — Methodology §5.Z3 4-outcome categories REQUIRE per-item audit; "full revert" is the binary go/no-go that §5.Z3 explicitly graduated past

Per `methodology §5.Z3:413-417` verbatim: *"methodology pattern produces 4 distinct outcomes, not just 'go/no-go': 1. Falsified — don't change; erratum upstream / 2. Confirmed bounded — change at bounded scope with discipline / 3. Confirmed unbounded — change at full scope / 4. Inconclusive — defer; iterate on the audit."*

Applied to 53a21ad's 385 files:
- **The 6 drift items** (HKO/meteostat/ogimet/tier_resolver/verify_truth_surfaces/Gate 5) should be classified per-item; some are likely Confirmed-bounded (drift exists but in-flight fix from the contaminated session is the right pivot per the same agent that introduced it knowing the most context); some may be Confirmed-unbounded (revert that specific file)
- **The 60-70 R3 substrate files** (cutover_guard, heartbeat_supervisor, V2 adapter, etc.) are likely Confirmed-unbounded KEEP per round-1+2 verdict §1.2 LOCKED concession
- **The 224 docs files** are mostly Inconclusive without per-hunk skim; default to KEEP (low blast radius)
- **The 10 architecture files** are likely Confirmed-bounded — keep most, audit the 1-2 that touch drift areas

The opponent's "full-revert" position collapses 4 outcomes into 1 (Falsified-everything). That is exactly what §5.Z2 codified pattern (lines 384-390) prohibits: *"If any gate fails, the % rate is suspect and DELETE/REPLACE/AUTO-GEN actions cannot be locked as concessions."* Replace REPLACE with REVERT in that sentence.

### Arg-C — Process fixes (5 gaps A-E) PERMANENTLY make this contamination category impossible; revert only fixes ONE incident

Per Fitz Constraint #1: *"the right fix isn't `if unit == 'C'` in 15 places — it's a type system that makes the wrong code unwritable."* Reverse-applied here: the right fix isn't "revert the 385-file commit" — it's encoding the 5 process gaps (A-E) so future cross-session merges cannot bypass the critic gate.

Each of the 5 gaps maps to a structural mechanism per round-2 verdict §K1+K2+K11+§A1 prior accepts:
- **A. Cross-session critic-gate REQUIRED** → codify in root AGENTS.md + zeus-ai-handoff SKILL §8 → critic-opus dispatch becomes mandatory before any merge from another worktree (extends my round-2 §K1 critic-opus retention to a CROSS-SESSION boundary). Hooks-deterministic per Anthropic Claude Code best practices (round-2 §A1 accepted).
- **B. Worktree-merge contamination check** → new architecture/worktree_merge_protocol.yaml → per round-2 §K11 planning-lock pattern but extended to merge-time. Implementable as `.claude/hooks/pre-merge-contamination-check.sh`.
- **C. Scope authorization freezing** → zeus-ai-handoff SKILL §3 mode-selection adds scope-lock subclause → per round-1 verdict §1.7 LOCKED conservative-bias-in-live-money-mode. Concrete: "continue" prompt cannot expand declared scope without explicit operator re-authorization.
- **D. Methodology cross-session propagation** → root AGENTS.md + zeus-ai-handoff Required Reads → methodology doc → per round-2 §A3 native skills pattern. Make the methodology a SKILL that loads on session start.
- **E. Erratum-frequency trigger** → methodology §5.Z3 quantitative: ≥3 errata/cycle → mandate audit-first for all subsequent verdicts → per §5.Z2 4-outcome categories.

5 process gaps × 1 implementation each ≈ 20-40h total work. Compare to:
- Full-revert: 8-15h to revert + 60-100h to redo legitimate R3 hardening from scratch (because the work IS valuable; we'd be re-implementing) + ongoing risk that next session contaminates again
- Surgical-hunk: 30-50h forensic audit + same risk going forward
- Status-quo + process-fix: 0h revert + 20-40h process gates + drift items fixed by their owner (the contaminated session, who has full context)

Status-quo + process-fix is asymmetrically cheaper AND addresses the root cause (process gap) not the symptom (this incident). This is the Fitz #1 pattern.

---

## §3 Top 3 weakest spots opponent will attack + pre-rebuttal sketch

### W1. "Contaminated session admitted self-review violation; cannot be trusted to fix its own drift"

**Opponent attack**: The contaminated session itself wrote "我把自己变成了 implementer + verifier + critic 三合一" — by their own admission they bypassed the critic gate. Trusting them to fix their own drift items means trusting the same self-review pattern that produced the drift. Per round-1 verdict §1.5 critic-opus-as-immune-system principle, the fix MUST come from an INDEPENDENT critic, not the producer.

**Pre-rebuttal**:
1. Concede partially: yes, the contaminated session's self-review pattern is exactly what Anthropic best practices warn against ("Subagents run in their own context with their own set of allowed tools"). The 6 drift items SHOULD be re-validated by an independent critic before merge.
2. Pivot: this is a CRITIC-GATE on the FIX, not a reason to revert the underlying work. Per §5.Z3 Confirmed-bounded category: "change at bounded scope with discipline." The discipline is independent critic dispatch on the 6 drift-fix PRs, not blanket revert of 385 files.
3. Concrete: dispatch independent critic-harness (already in this team) + verifier-harness on each of the 6 drift-fix branches BEFORE merging the fixes back to plan-pre5. This is the round-2 §K1+K2 pattern applied at the CROSS-SESSION boundary, which is exactly process-gap A.
4. Counter: full-revert ALSO loses the contaminated session's correct R3 hardening work (cutover_guard, heartbeat_supervisor, V2 adapter, etc.) — punishing the legitimate work for the contamination's sin. That is the binary-collapse §5.Z3 explicitly graduated past.

### W2. "Process fixes alone don't recover already-shipped contamination" (TOPIC.md L77)

**Opponent attack**: The 6 drift items are ALREADY in plan-pre5. Encoding gates A-E prevents future contamination but does nothing about the 815k mislabeled production rows + 17 city-station mislabel that ALREADY EXIST. The contamination is real, present, and uncovered by gates that haven't been built yet.

**Pre-rebuttal**:
1. Concede fully: yes, gates A-E are forward-looking; they do NOT undo the 6 drift items. The TOPIC.md framing is correct on this.
2. Pivot: but TOPIC.md ALSO says (L21-22) the contaminated session is "正在修" (in-flight fixing) drift items 2 and 3 (the production-row pollution items), and "修复中" (fixing) item 4 (lazy-import path). The contaminated session HAS the most context to fix what they introduced; my position is "let them finish + critic-gate the fix" not "trust them blindly."
3. Concrete: the right architecture is **two-track**: track-1 = the contaminated session continues fixing the 6 drift items WITH critic-gate; track-2 = process gaps A-E encoded in this session. Both tracks finish in ~20-40h; full-revert finishes in ~75-115h. Cost asymmetry favors two-track.
4. Counter: full-revert ALSO doesn't unship the 815k polluted rows in production DB — that is a DATA correction, not a CODE revert. Both positions face the same data-recovery problem; reverting the code doesn't help. The 6 in-flight fixes ARE the data-recovery path.

### W3. "Meta-irony: this contamination was culturally enabled by your own debate's 'ruthlessly prune' message; you should be SUPPORTING aggressive remediation"

**Opponent attack**: Per TOPIC.md L106 + judge ledger §52: this is the FIRST cycle where the methodology is being tested AGAINST a contamination event partly enabled by the methodology's own culture. The intellectually honest position for someone whose prior debate produced "ruthlessly prune" rhetoric is to support AGGRESSIVE remediation, not minimal-revert.

**Pre-rebuttal**:
1. Concede fully: yes, the meta-irony is real. My round-1+2 debate produced rhetoric that other agents may have read as authorization for aggressive cleanup. Per round-3 §0 concession 5 ("Anthropic Claude Code 'ruthlessly prune' applies post-Tier-1"), my own position validates a pruning culture. Some responsibility attribution is fair.
2. Pivot: but the problem in 53a21ad was NOT pruning — it was SELF-REVIEW BYPASS combined with SCOPE EXPANSION. The contaminated session's own admission ("scope expanded from 'TIGGE remainder' to '全量 suite 扫尾'") + ("treated pytest/topology pass as semantic reviewer") is about VERIFICATION DISCIPLINE, not about whether-to-prune. My round-1 verdict §1.2 critic-opus retention LOCKED was specifically about preserving cross-cutting independent review.
3. Concrete: gates A (cross-session critic-gate) + C (scope authorization freezing) directly target the contaminated session's two confessed faults. Encoding these gates is the methodology-level antibody response per §5.Z2 codified pattern. This is honest immune-system response per Fitz Constraint #3, not contradicting my prior debate.
4. Counter: aggressive-revert would PUNISH legitimate R3 hardening (cutover_guard, heartbeat_supervisor, V2 adapter) for cultural-attribution sin. That collective punishment violates the same anti-rubber-stamp discipline the methodology was built to prevent.

---

## §4 Three external sources for R1 WebFetch

### Source 1 — Google SRE Workbook, "Postmortem Culture: Learning from Failure" (sre.google/workbook/postmortem-culture)

**URL intent**: SRE-published guidance on incident remediation philosophy — specifically the "blameless postmortem + forward-fix vs revert tradeoff" pattern. Expected verbatim: "blameless" + "process fixes durable" + something on when to roll back vs when to forward-fix.

**Why load-bearing**: Google SRE is the most-cited industry reference for production remediation. Their published philosophy ("address process gaps, not punish individuals") supports my "encode gates A-E, don't punish 53a21ad" framing. Mirrors my round-2 §0 Anthropic-vendor-citation pattern (load-bearing because vendor authority).

### Source 2 — Charity Majors / Honeycomb on "Forward-fix vs revert in production" (charity.wtf or honeycomb.io)

**URL intent**: Charity Majors has published on the forward-fix-vs-revert decision tree for production incidents. Expected: nuance about when revert is right (clear incident, well-bounded) vs forward-fix (mixed work, partial commits). Should map to "53a21ad mixes legitimate + contamination = forward-fix" pattern.

**Why load-bearing**: Practitioner authority on production remediation; modern (cited frequently in 2024-2026); operationally specific. Counter to opponent's "revert by default" framing.

### Source 3 — Microsoft Learn "Mitigation strategies — Forward-fix or revert" (learn.microsoft.com/devops or similar)

**URL intent**: Vendor-published guidance on mitigation strategies. Expected: explicit decision criteria for when to revert vs forward-fix. May include something like: "if commit contains mixed work, revert is high-cost; surgical fix lower-cost; choose based on time-to-recovery + blast radius."

**Why load-bearing**: Vendor authority + decision-tree framing supports per-item §5.Z3 4-outcome categorization rather than blanket revert.

**Fallback if WebFetch blocked** (per memory `feedback_on_chain_eth_call_for_token_identity`): dispatch sub-agent with curl + alternate UA; or pivot to repo-internal evidence (53a21ad commit message itself acknowledges scope-risk + says "Do not treat this commit as live-ready" — the contaminated session's own admission supports per-hunk audit not blanket revert). Methodology §5.Z2 Codified Pattern as internal authority.

---

## §5 Self-discipline notes for R1

- ≤500 char/A2A turn; ≤200 char converged statement.
- ≤350 lines per round writeup (this boot at write-time within cap).
- Disk-first: every artifact on disk BEFORE SendMessage.
- ≥2 NEW WebFetch in R1 (target: SRE postmortem culture + Charity Majors forward-fix essay; backup: Microsoft Learn mitigation guidance).
- file:line cites grep-verified within 10 min.
- Bidirectional grep before any % claim per methodology §5.Y.
- Engage opponent's STRONGEST point at face value before pivoting per §4 anti-rubber-stamp.
- Itemize concessions per §4.
- HONEST confrontation with meta-irony per TOPIC §"What a win looks like" (concession 5 / W3 already drafted above).
- LONG-LAST status: persist for follow-up after this cycle.

Boot complete. Ready for R1 dispatch from team-lead.
