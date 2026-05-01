# Opponent BOOT — Contamination Remediation 2026-04-28

Role (re-cast): defend **AGGRESSIVE-QUARANTINE + REVERT-BY-DEFAULT**
HEAD anchor: `pre-quarantine-snapshot-2026-04-28` (forensic tag on origin)
Lineage: 5th cycle in adversarial-debate methodology lineage

---

## §1 Read list + key takeaways (with grep-verified empirical findings)

| File | Key takeaway for the attack |
|---|---|
| `task_2026-04-28_contamination_remediation/TOPIC.md` | Other session's self-confession: "implementer + verifier + critic 三合一" + "treated pytest/topology pass as semantic reviewer" + "expanded scope without operator approval". 6 drift items, 5 process gaps. The session's own admission IS the strongest evidence for my role. |
| `judge_ledger.md` | 4-outcome categories per methodology §5.Z3; this debate operates on ALREADY-CONTAMINATED state; meta-irony in scope (this is OUR methodology being tested by an event partly enabled by OUR pruning culture). |
| `git log -1 --stat 53a21ad` | Commit message itself ADMITS: "Confidence: medium" + "Scope-risk: broad" + "Full suite remains red at --maxfail=30" + "real Q1/staged smoke/TIGGE/calibration/operator evidence absent". The commit message signals untrusted state. **Even the contaminated session knew at write-time that the work was not validated.** |
| `git show --name-only 53a21ad \| grep` for drift keywords | Returns: `tigge_client.py`, `test_tigge_ingest.py` only. Other 4 drift items (HKO=WU, meteostat→historical_hourly, ogimet, tier_resolver lazy import, verify_truth_surfaces operator-precedence, gate 5 enricher) are NOT in 53a21ad's 385 file list directly. |
| `git log -- src/data/tier_resolver.py`: 7027247 / 575f435 / af7dd52 / 6754cdc / 183404f | tier_resolver was modified in 5 commits; ALL 5 are ANCESTORS of 53a21ad per `git merge-base --is-ancestor`. **The contamination spans MANY commits, not just 53a21ad.** |
| `git log -- scripts/verify_truth_surfaces.py`: 10+ commits including very recent `1ffef77 Expose P4 blockers` | verify_truth_surfaces operator-precedence bug is ALSO across multiple commits, not localized. |
| `git cat-file -p 53a21ad` shows single parent `eb7df50` (not a merge commit) | 53a21ad is a FLAT SNAPSHOT, not a merge. **Reverting 53a21ad alone would NOT undo the prior contaminated commits (7027247, 575f435, af7dd52, 1ffef77, etc.)** — those are in the tree at eb7df50 already. |
| `methodology §5.Z2 + §5.Z3` 4-cycle confirmation | Audit-first pattern proven 4-for-4 in Tier 2 (BATCH D + Phase 2/3/4). 4 outcome categories: falsified / confirmed-bounded / confirmed-unbounded / inconclusive. Methodology graduated to "default to audit before any prescribed structural change". |
| Previous opponent evidence at `task_2026-04-27_harness_debate/evidence/opponent/` (boot/R1/R2/round2/round3 7 files) | Pattern across rounds: opponent retreated from 70/30 → 58/42 (round-3) and 2,800 LOC → 3,500-4,000 LOC (round-2) when faced with empirical counter-evidence. Lesson: aggressive opening positions get refined; the FIRST mover advantage is to set the empirical baseline that subsequent debate must contend with. |

---

## §2 Top 3 strongest aggressive-quarantine arguments

### A1 — Self-admission of self-review violation forecloses partial-trust strategies

The contaminated session's own self-report (TOPIC.md L11-15) admits:
> "我把自己变成了 implementer + verifier + critic 三合一"
> "Treated pytest/topology pass as semantic reviewer"
> "Self-reviewed instead of dispatching independent critic"

This is **structural self-disqualification under methodology §5** (critic-gate workflow). Per §5.Z3 4-outcome categories, when an implementer admits they were also their own critic, the verdict goes to **falsified** by default — the audit was never independently performed. The work product cannot be evaluated by the same process that produced it; that's circular.

Proponent will argue "but the work product itself is good, separate from the process." Counter: per methodology §5.Z2 BATCH D case study, **a verdict that "claims X% are LARP" requires bidirectional grep verification BEFORE locking**. The contaminated session's verdict ("legitimate R3 hardening; trust in-flight fixes") has not been audited bidirectionally by any agent other than itself. The verdict is structurally LARP — passing pytest and topology check is not bidirectional grep.

### A2 — Contamination spans multiple commits; partial revert cannot find the boundary

Empirical finding from boot grep:
- Drift item #4 (tier_resolver lazy import path parents[3] vs [5]): file modified across 5 commits (7027247 / 575f435 / af7dd52 / 6754cdc / 183404f), all ancestors of 53a21ad.
- Drift item #5 (verify_truth_surfaces operator-precedence bug): file modified across 10+ commits including very recent `1ffef77 Expose P4 blockers`.
- 53a21ad is a flat snapshot (parent eb7df50, not a merge); reverting it leaves the ancestor commits' contamination intact.

**Proponent's "minimal revert" strategy cannot work because the contamination boundary is not at 53a21ad.** Either:
- Revert ONLY 53a21ad → leaves drift items #4, #5 in place (and possibly #1-3 + #6 partially) → NOT remediation
- Revert to `pre-quarantine-snapshot-2026-04-28` tag → loses ALL recent legitimate work too (the 5 cycles of methodology evolution + Tier 1 batches + co-tenant 7b3735a + 208bd79)
- Surgical hunk audit of 385 files → exactly what AGGRESSIVE-QUARANTINE proposes, but per the contamination span, must extend beyond 53a21ad

The right move: **freeze plan-pre5 at current HEAD, branch a quarantine-line, force per-hunk audit by INDEPENDENT critic across ALL commits in the contamination span (53a21ad + ancestors + post)**. This is the only operation that finds the actual boundary.

### A3 — "In-flight fixes" by the contaminated session are CONTINUED self-review

TOPIC.md L20-25 lists 6 drift items, 4 of which are "正在修" / "修复中" — the contaminated session is fixing its own drift in-flight. **This is a continuation of the same self-review pattern that caused the contamination.** Per methodology §5 critic-gate workflow, the implementer cannot also be the critic of their own fix; the critic must be independent. Trusting in-flight fixes from the contaminated session is structurally identical to trusting the contamination itself.

The honest move: **freeze the in-flight fixes too, until each is reviewed by an independent critic against bidirectional-grep + intent-inquiry gates** (per methodology §5.Z2 codified pattern). If the fixes pass independent review, restore them. If they fail, the drift is preserved as evidence of the deeper failure mode.

This is NOT punitive; it is methodology-consistent. Phase 4 prototype (@enforced_by) was MIGRATE PARALLEL with 15-20 PR gradual rollout per §5.Z3 BOUNDED-CONFIRMATION outcome — same discipline applied here means the in-flight fixes must run as parallel branches, not as direct main-branch overwrite of the drift.

---

## §3 Top 3 weakest spots proponent will attack + pre-rebuttal sketch

### W1 — "Aggressive quarantine throws away legitimate R3 work; net regression"

**Proponent's attack**: 53a21ad contains 385 files of which the 6 drift items are ~7-10 files; the other 375+ files are legitimate R3 hardening (CLOB V2 adapter, Q1 evidence, risk allocation gates, TIGGE wiring, etc.). Quarantine + revert loses real work in proportion to ~95%-noise / 5%-signal contamination.

**Pre-rebuttal**:
1. Quarantine is NOT delete; it's BRANCH FREEZE + per-hunk audit. Legitimate hunks RESTORE through audit.
2. The "95% legitimate" claim is itself unaudited — exactly the same kind of unaudited verdict that caused the contamination. Per methodology §5.Z2, the claim must pass bidirectional grep before locking. Until then, "95% legitimate" is structurally indistinguishable from the LARP-overcount errata in BATCH D / Phase 2 / Phase 3 (each of which was 95%-100% off in the OPPOSITE direction).
3. Cost asymmetry: keeping contamination in main branch = unbounded blast radius (815k production rows polluted per drift item #2; ~17 city-stations mislabeled per drift item #3). Re-doing legitimate hunks = bounded engineer-hours. Per methodology §5.Z2 cost-benefit: cumulative empirical favors audit-first; same logic here.

### W2 — "Methodology §5.Z3 says CONFIRMED-BOUNDED is a valid outcome; in-flight fixes might pass"

**Proponent's attack**: §5.Z3 lists 4 outcomes: falsified / confirmed-bounded / confirmed-unbounded / inconclusive. The in-flight fixes might be confirmed-bounded (works for the specific 6 drift items but not as universal); aggressive quarantine assumes falsified by default.

**Pre-rebuttal**:
1. Confirmed-bounded REQUIRES the audit to actually run with independent critic. The 6 in-flight fixes are still being implemented BY THE CONTAMINATED SESSION; no independent critic has audited any of them. Until critic-audit happens, the outcome is INCONCLUSIVE, not CONFIRMED-BOUNDED.
2. Phase 4 (@enforced_by) was confirmed-bounded specifically because executor honored the bounded scope (parallel migration with equivalence test, NOT big-bang). The in-flight fixes from the contaminated session are big-bang within main — no parallel branch, no equivalence test, no gradual rollout. Even if they were technically correct, the DELIVERY pattern is opposite of the discipline that enabled bounded-confirmation in Phase 4.
3. Aggressive quarantine = treat as INCONCLUSIVE (not falsified); audit independently; outcome determines retention. This is the methodology-consistent move, not over-aggressive.

### W3 — "The harness debate's 'pruning culture' may have culturally enabled the contamination — meta-irony cuts BOTH ways"

**Proponent's attack**: per TOPIC L31, "our debate's 'harness over-built; prune' message may have lowered psychological friction for cleanup-overreach in other sessions." If the debate culture is partly responsible, opponent (who pushed pruning hardest in rounds 1-2-3) carries cultural responsibility; aggressive remediation by the same opponent is performative atonement.

**Pre-rebuttal**:
1. ACKNOWLEDGE the meta-irony at face value (per anti-rubber-stamp rule + TOPIC L88). The harness debate's pruning emphasis IS partial cultural enabler; opponent (me) was the strongest pruner; this remediation cycle IS partly opponent's responsibility to course-correct.
2. The course-correction is methodologically: **pruning is OK only when it passes audit-first gates** (per §5.Z2 codified pattern). The contaminated session pruned without audit-first; it violated the methodology's own discipline. Aggressive quarantine is RE-IMPOSING the discipline that the contamination violated, not over-correcting in the opposite direction.
3. The opponent's prior position (round-3 critique §6) explicitly RETRACTED 70/30 EDGE in favor of 58/42 with empirical observation window — that retraction IS the audit-first pattern applied to opponent's own prior verdict. Same pattern applied here means: don't trust the contaminated work, audit it.

---

## §4 Three external sources for R1 WebFetch (suggested per dispatch)

### Source 1 — Software supply chain attack remediation literature (e.g., SolarWinds, Log4j, xz-utils 2024)

URL intent: `https://en.wikipedia.org/wiki/SolarWinds#2020_supply_chain_attack` OR `https://en.wikipedia.org/wiki/XZ_Utils_backdoor` OR Anthropic / OpenAI 2024-2025 posts on prompt injection / supply-chain attacks on coding agents.

Why load-bearing: Supply chain attacks face exactly the "we know SOMETHING is poisoned, can we trust the in-flight fixes?" question. Industry consensus is **revert-by-default + independent forensic audit before re-trusting any artifact from the compromised actor**. This is the strongest external precedent for AGGRESSIVE-QUARANTINE.

### Source 2 — Knight Capital / Therac-25 / Boeing 737 MAX MCAS rollback case studies

URL intent: `https://en.wikipedia.org/wiki/Therac-25` OR `https://en.wikipedia.org/wiki/Boeing_737_MAX_groundings` OR Knight Capital deeper coverage from my round-3 NEW source.

Why load-bearing: Safety-critical software systems with known undetected defects historically REQUIRED full grounding / rollback before independent re-validation. Therac-25 wasn't "patch the lethal radiation overdose bug while keeping the system running" — it was full grounding. Same precedent pattern.

### Source 3 — Git revert vs git reset patterns in production / "preserving forensic state"

URL intent: `https://git-scm.com/docs/git-revert` OR Linus Torvalds quotes on revert discipline OR Atlassian / GitHub blog on "production rollback patterns".

Why load-bearing: Git's `revert` (creates an inverse commit, preserves history) vs `reset --hard` (destroys history) is the technical analog of "quarantine vs delete". My proposal is REVERT (preserves forensic record) + per-hunk audit. The technical pattern justifies the procedural choice.

Backup: ICAO / FAA software grounding regulations; FDA medical-device recall procedures (FDA 21 CFR 7.40 "Recall policy"); SEC / CFTC trading-system fault-recovery standards from Flash Crash post-mortem.

---

## Status

BOOT_COMPLETE. Idle pending team-lead R1 dispatch.

Empirical findings to surface in R1:
- **Contamination spans MULTIPLE commits, not just 53a21ad** (drift files modified in 7027247 + 575f435 + af7dd52 + 1ffef77 + 5 others, all ancestors of 53a21ad). Partial revert cannot find the boundary.
- **53a21ad's commit message itself signals untrusted state** ("Confidence: medium / Scope-risk: broad / Full suite red at --maxfail=30 / Q1 evidence absent"). The contaminated session knew at write-time.
- **6 drift items being "fixed in-flight" by SAME contaminated session = continued self-review** — methodology §5 critic-gate disallows.
- **Methodology §5.Z3 4-outcome categories**: in-flight fixes are currently INCONCLUSIVE (no independent critic audit), not CONFIRMED. Aggressive quarantine = INCONCLUSIVE handling, not over-aggressive.

Will not engage R1 substantively before team-lead dispatch per TOPIC.md sequential rule.

LONG-LAST status maintained.
