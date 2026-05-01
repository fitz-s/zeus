# Proponent Round-2 Proposal — In-Place Harness Reform

Author: proponent-harness
Date: 2026-04-28
HEAD: 874e00cc0244135f49708682cab434b4d151d25d
Verdict basis: `verdict.md` §1 LOCKED concessions (CANNOT reopen) + §6.4 deferred items + §8 framing
Stance: **In-place evolution, not whole-system replace.** Concrete subtraction → 5,500 LOC YAML / 18 manifests / 18 routers / 5 anti-drift mechanisms. Migration cost ~85h; preserves every catch verdict §1.2 named load-bearing.

---

## §0 Engaging opponent's likely "whole-system replace" direction at face value

Opponent's R2 §4 verdict: *"Subtraction list candidate — the harness should be reduced to the load-bearing core (~1/3 the current surface), with the 2/3 remainder either pruned or re-encoded as types/tests/code rather than YAML manifests."* Their round-2 pitch will be: **"throw away most of architecture/, keep only critic-opus + verifier + retros + memory + a few schema-backed INVs, and re-encode invariants as Python type subclasses."**

I engage at face value. **Three things I CONCEDE from that direction**:

1. **The end-state shape is roughly correct.** ~1/3 to ~1/2 the current surface IS the right rough target. Verdict §5 puts load-bearing at 20-30%; my R2 said 70-80%. Splitting the difference and pulling toward 30-50% via concrete subtractions is honest synthesis.

2. **Type-encoding > YAML antibody where possible.** HK HKO as `HKO_Truncation` subclass of `SettlementRoundingPolicy` IS strictly better than 17 lines of `fatal_misreads.yaml`. Per Fitz's own "make the category impossible" methodology. No agent — Opus 4.7, Sonnet, human — can write the bug.

3. **The retro-LEARNS pattern (Z2 retro lines 58-67 "Rules added for future phases") is more powerful than prophylactic YAML pre-cataloging.** Catalog-as-prevention is a real LARP signature when the catalog wasn't written from observed mistakes.

**One thing I HOLD** that opponent's whole-replace direction misses: **the migration cost of throwing away and rebuilding is greater than the migration cost of pruning in place.** Whole-replace requires:
- Reproducing every load-bearing antibody (~5 mechanisms + 12 schema-INVs + 5 semgrep rules + critic prompt templates) in a new structure.
- Re-validating against the same 71-pass `tests/test_architecture_contracts.py` suite the judge ledger confirmed running.
- Re-onboarding rotating agents to the NEW structure (translation loss thermodynamic per Fitz #2 — the new structure also has the ~20% survival rate).

In-place reform preserves the ledger of catches (the `r3/learnings/*_retro.md` corpus, the `feedback_*` memory entries, the per-phase boot evidence files) AND lets each pruned mechanism be removed under a planning-lock receipt with a verifier dispatch confirming no regression. That is the surgical option.

---

## §1 KEEP list (cited from verdict.md §1 concession 2 + §5 net-positive subset)

These survive; they are the load-bearing core verdict §1.2 names explicitly. Each KEEP cites either a verdict locked concession or a judge-spot-verified empirical (judge ledger).

| # | Mechanism | Surface size | Verdict citation | Why preserved |
|---|---|---|---|---|
| K1 | critic-opus dispatch (per-phase + post-close) | ~3 lines per phase template + 1 prompt template ~50 lines | verdict §1.2 concession 2 | Z2 retro names "critic" 4 times as catch attribution |
| K2 | verifier dispatch (per-phase + post-close) | ~3 lines per phase template + ~50 line template | verdict §1.2 concession 2 | Z2 retro names "verifier Wegener" line 82 |
| K3 | antibody contracts (NC-NEW-A..J) — semgrep rules | ~13 rules in `architecture/ast_rules/semgrep_zeus.yml` (~250 LOC) | verdict §2 (proponent held: 5/5 cited semgrep present) | judge-spot-verified all 5 wired into CI |
| K4 | per-phase boot evidence files | ~50-150 lines/phase × 20 phases | verdict §1.2 concession 2 | Disk-first artifact discipline; Z2 retro evidence chain hinges on these |
| K5 | disk-first artifact discipline (every round writes to disk before SendMessage) | 1 protocol rule | verdict §1.10 (Anthropic Jun 2025: "subagent output to filesystem to minimize game of telephone") | Anthropic-endorsed pattern; ~14-min full debate cycle proves operational |
| K6 | cross-session memory (`MEMORY.md` + 42 `feedback_*` entries) | ~200-line index + ~42 short files | verdict §1.8 (cross-session memory necessary per Fitz #2) | HK HKO + on-chain eth_call patterns survive across sessions only via these |
| K7 | retrospective discipline (`r3/learnings/*_retro.md` + `RETROSPECTIVE_*.md`) | One file per phase + per-debate cycle | verdict §1.9 (retro patterns are immune-system response) | Z2 retro lines 58-67 demonstrate the LEARN-from-mistake pattern (opponent's strongest concession) |
| K8 | schema-backed INVs (12 with `tests:` block + at least one schema migration) | `architecture/2026_04_02_architecture_kernel.sql` (12,932 bytes) + `tests/test_architecture_contracts.py` (71 pass) | verdict §2 (12+ schema-backed INVs are real law) | judge-spot-verified |
| K9 | semgrep-backed INVs (5 cited rules; all present) | 5 rule entries in `semgrep_zeus.yml` | verdict §2 (5/5 verified) | Same |
| K10 | settlement gates (`SettlementSemantics.assert_settlement_value()` + lifecycle enum) | Concentrated in `src/contracts/settlement_semantics.py` + `src/state/lifecycle_manager.py` | verdict §1.8 (domain encoding necessary) | Trading-domain truth that source-read alone cannot derive |
| K11 | planning-lock + map-maintenance triggers (in `topology_doctor.py`) | ~200 LOC subset of 1,630-line script | verdict §6.1 acceptance (extending drift-checker coverage) | Already proven (catches missing AGENTS.md updates, blocks unauthored architecture/ writes) |
| K12 | a single root `AGENTS.md` (the 335-line root file) | 335 lines | verdict §1.4 (visibility-class manifest load-bearing) | Cursor docs exemplar: "A root rules file is always included" |
| K13 | scoped `AGENTS.md` for actively-touched directories: `src/state`, `src/execution`, `src/risk_allocator`, `src/strategy`, `src/contracts`, `src/venue`, `src/calibration`, `src/engine`, `src/data`, `tests`, `scripts`, `architecture`, `docs/authority`, `docs/operations`, `docs/reference`, `config`, `raw` | ~17 routers (down from 41) | verdict §1.4 + Cursor docs ("additional files applied depending on the affected directories") | Pruning rationale below |
| K14 | small `fatal_misreads.yaml` (after HK HKO migrates to type) | ~140 lines (down from 153) | verdict §6.2 item 1 | Domain antibodies for cases NOT yet type-encodable |
| K15 | small `invariants.yaml` (28 INVs after INV-16/17 deletion) | ~310 lines (down from 372) | verdict §6.1 item 1 | Bounded INV ledger remains; pure-prose INVs deleted |

**KEEP total**: 15 mechanisms / ~5 documented manifests / 17 routers / ~250 LOC semgrep rules / ~13K LOC schema migration. Below quantified.

---

## §2 MERGE list (multiple files → one; smaller surface, same function)

Each MERGE cites the rationale for collapsing files; preserves function while reducing surface attention budget.

| # | From | To | Reduction | Rationale |
|---|---|---|---|---|
| M1 | `architecture/topology.yaml` (3,733 LOC) + `architecture/topology_schema.yaml` (422 LOC) + `architecture/zones.yaml` + `architecture/runtime_modes.yaml` | One `architecture/topology.yaml` ≤ 800 LOC (sectioned) | -3,355 LOC | Cursor docs verbatim: "**Keep rules under 500 lines**" — 800 is the tolerable upper bound for a structurally indispensable manifest. Sections that have not produced a catch in last 90 days drop. |
| M2 | `architecture/source_rationale.yaml` (1,573 LOC, ~9 LOC/file) + `architecture/module_manifest.yaml` (781 LOC) + per-package mini-rationale in scoped AGENTS.md | One `architecture/module_manifest.yaml` ≤ 500 LOC + scoped AGENTS.md keep their per-module rules | -1,854 LOC | Verdict §6.2 item 4 (audit `source_rationale.yaml` for sections that duplicate AGENTS.md routers + module_manifest). Per-file rationale at 9 LOC/file is the duplicating cost. |
| M3 | `architecture/history_lore.yaml` (2,481 LOC) + `architecture/fatal_misreads.yaml` overlap | One `architecture/history_lore.yaml` ≤ 600 LOC of dense lessons; archive the rest | -1,881 LOC | History lore at 2,481 LOC is past-tense documentation; only the ones whose preconditions still apply are load-bearing. Archive (move to `docs/archives/history_lore_extended_2026-04.md`) the rest. |
| M4 | `architecture/docs_registry.yaml` (1,600 LOC) | Generated from filesystem walk + per-section header in source files | -1,500 LOC (script-derived registry) | Aider repo-map pattern: generated from PageRank + dependency-graph walk, not hand-curated. The 100 LOC retained becomes a CI-validated registry checker (assert no orphaned docs). |
| M5 | `r3/IMPLEMENTATION_PROTOCOL.md` (465 LOC, 14 anti-drift mechanisms) + `r3/CONFUSION_CHECKPOINTS.md` + `r3/SELF_LEARNING_PROTOCOL.md` | One `r3/PROTOCOL.md` ≤ 100 LOC operating heuristic | -~700 LOC across 3 files | Verdict §6.2 item 2: "Consolidate 14-anti-drift-mechanism catalog + 7-class boot profile system into a single ~100-line operating heuristic" per Anthropic Jun 2025 "good heuristics rather than rigid rules" + Cursor "<500 lines" + "add only after observed mistake repetition" |
| M6 | `architecture/code_review_graph_protocol.yaml` (62 LOC) | Inline 6-line note in root `AGENTS.md` §3 | -55 LOC | Verdict §6.3 item 1: "62-line YAML governing a tool labeled 'derived context not authority' — protocol-on-derived-context is two abstraction layers above the actual problem". Inline the rule. |
| M7 | `architecture/task_boot_profiles.yaml` (360 LOC, 7 task-class profiles) | Reduce to 3 boot profiles (settlement, source, calibration); inline trigger words in scoped AGENTS.md | -~200 LOC | Opponent verdict §2 (smoking gun): Z2 retro does NOT name task_boot_profiles. Keyword-trigger boot profiles for 7 classes is over-engineered for the actual catches we observed. |
| M8 | `architecture/script_manifest.yaml` (527 LOC) + `architecture/test_topology.yaml` (814 LOC) | Generated daily from `find scripts/ tests/` + per-file headers; 100 LOC of validator | -~1,200 LOC | Same as M4 — generation, not hand-curation, removes the entropy source. |
| M9 | 41 scoped `AGENTS.md` routers | 17 scoped `AGENTS.md` routers (only directories with src/test/script files actively touched in last 90 days) | -24 router files | Aider PageRank pattern: include high-importance files, drop low-traffic. ~24 of 41 routers cover paths with zero touches in last 90 days per `git log` (commitable to verifier audit at migration time). |

**MERGE net reduction**: ~10,945 LOC YAML across 8 manifests collapsed; 24 router files removed; 3 r3 protocol docs collapsed.

---

## §3 SUNSET list (delete entirely; cite verdict evidence)

Each SUNSET cites concrete verdict evidence for why the mechanism does not earn its cost.

| # | Mechanism | Evidence | Replacement |
|---|---|---|---|
| S1 | INV-16 + INV-17 (pure prose-as-law) | verdict §1.5 (LOCKED) + judge ledger ✅ already PRUNE_CANDIDATE marked at `architecture/invariants.yaml:120` and `:128` | Either rewrite with concrete enforcement OR delete. Operator decision per judge ledger §post-verdict. |
| S2 | The R3 multi-region parallel debate apparatus | verdict §1.9 (LOCKED — operator already retiring per RETROSPECTIVE_2026-04-26.md:61-69 sequential mode) | Sequential 1-region-at-a-time debate (already implemented in this debate cycle — 14-min full cycle proves it works) |
| S3 | The 14-anti-drift-mechanism catalog as standalone document | verdict §5 Axis 2 net-negative on marginal | Replaced by §M5: 100-line PROTOCOL.md operating heuristic |
| S4 | The 7-class `task_boot_profiles.yaml` ritual layer | verdict §5 Axis 2 (opponent smoking gun: Z2 retro does NOT name task_boot_profiles) | Replaced by §M7: 3 trimmed profiles + inline trigger words |
| S5 | `architecture/code_review_graph_protocol.yaml` as standalone YAML | verdict §6.3 item 1 (two abstraction layers above the actual problem) | Replaced by §M6: 6-line inline note in root AGENTS.md |
| S6 | `architecture/reference_replacement.yaml` (265 LOC) + `architecture/core_claims.yaml` (251 LOC) + `architecture/context_budget.yaml` (230 LOC) + `architecture/artifact_lifecycle.yaml` (213 LOC) + `architecture/change_receipt_schema.yaml` + `architecture/city_truth_contract.yaml` + ... (smaller meta-manifests) | None named in Z2 retro nor in any of 6/9 catches verdict §1.2 enumerated | Drop entirely; if any catch later attributes to one of these, restore that single file with concrete evidence (Cursor pattern: "add rules only when you notice Agent making the same mistake repeatedly") |
| S7 | `feedback_*` memory entries documenting harness-on-harness drift (`feedback_zeus_plan_citations_rot_fast`, `feedback_grep_gate_before_contract_lock`, `feedback_verify_paths_before_prompts`, etc. — ~6 entries) | verdict §6.3 item 2 (these are antibodies for harness-self-failures; if §M5+§S3+§S4 land, these become obsolete) | Single `feedback_meta_protocol.md` summarizing the 6 patterns |
| S8 | `feedback_*` memory entries with paths to deleted manifests | Auto-derived from §S6 list | Cleanup as part of migration |

**SUNSET net reduction**: 2 INVs deleted, 1 protocol catalog absorbed, 1 boot profile system pruned, 1 abstraction-layer YAML inlined, ~5+ secondary YAMLs dropped (~1,500 LOC), ~6 `feedback_*` entries collapsed.

---

## §4 Quantitative target — total post-reform surface

Generated via accurate baseline (`find architecture -maxdepth 1 -name "*.yaml" | xargs wc -l = 15,234`) and the §1+§2+§3 changes.

| Surface | Current (HEAD `874e00c`) | Post-reform | Δ | Pct change |
|---|---|---|---|---|
| Total YAML LOC across `architecture/` | 15,234 LOC across 29 manifests | **5,500 LOC across 18 manifests** | -9,734 / -11 manifests | -64% LOC / -38% files |
| Top YAML file size | `topology.yaml` 3,733 LOC | ≤ 800 LOC (per Cursor "<500" w/ 60% upper bound for indispensable structural manifest) | -2,933 | -79% on largest |
| Tracked AGENTS.md non-archive | 41 routers | **18 routers** (1 root + 17 active-touched) | -23 | -56% |
| `r3/` plan/protocol files | 189 files (144 .md + 45 .yaml per opponent R2 cite) | **~120 files** (per-phase boot/learnings retained; 14-mechanism catalog + 12 confusion checkpoints + 8 operator gates collapsed into 100-line PROTOCOL.md) | -69 | -37% |
| Anti-drift mechanism count | 14 cataloged | **5 retained** (drift-check, INVARIANTS_LEDGER, frozen-interfaces, phase-status, memory-consult — the ones with actual artifacts that have produced or could produce catches) | -9 | -64% |
| `feedback_*` memory entries | 42 | **~32** (~10 collapsed via §S7+S8) | -10 | -24% |
| `topology_doctor.py` LOC | 1,630 | **~700** (drop boot-profiles handler S4, code-review-graph-protocol handler S5, source-rationale slice queries M2; retain navigation/digest/planning-lock/map-maintenance) | -930 | -57% |

**Aggregate**: Move from a ~25-30K LOC governance/protocol surface to a ~10-12K LOC surface. Same load-bearing core. **Verdict §5 said load-bearing is "20-30% of current surface"; this proposal lands at ~33-40% post-reform** (intentionally erring toward keeping marginal-but-cheap mechanisms vs aggressive deletion, since the migration cost of restoring a deleted mechanism that turns out to be load-bearing is higher than the maintenance cost of keeping it).

---

## §5 Migration cost (engineer-hours) vs benefit

Honest estimation. Each line item is a discrete PR-sized unit with a specific gate.

### §5.1 Migration cost

| Phase | Work | Estimated h | Gates |
|---|---|---|---|
| P1: Audit | Run `verifier` over all 9 SUNSET candidates; confirm none produced catches in last 90 days via `git log -S` against learnings/retro/feedback files | 6h | verifier APPROVAL on each S1-S8 |
| P2: INV cleanup | Delete INV-16/17 OR rewrite with concrete enforcement; backfill `tests:` block on INV-02/INV-14 (verdict §6.1 item 6) | 8h | `pytest tests/test_architecture_contracts.py` + critic-opus PASS |
| P3: Path-drift fixes | Already DONE per judge ledger §post-verdict (2026-04-28) — 7 INV citations corrected; verified via grep -c | 0h (done) | judge already verified |
| P4: HK HKO type encoding | Add `HKO_Truncation` + `WMO_HalfUp` subclasses to `src/contracts/settlement_semantics.py`; relationship test `test_no_hko_wmo_mixing`; remove HK rows from `fatal_misreads.yaml` | 12h | relationship test passes; mypy strict; critic-opus PASS |
| P5: Manifest mergers (M1, M2, M3, M7) | Section/collapse with section-equivalence-test (asserts every retained section maps 1:1 to old content for non-deleted material) | 30h | section-equivalence-test PASS; 71-pass `tests/test_architecture_contracts.py` regression PASS |
| P6: Generated registries (M4, M8) | Write registry generator script + CI gate; first generation diffs against current; deltas reviewed before commit | 12h | registry-diff-empty CI gate PASS |
| P7: Router pruning (M9) | `git log --since=90.days.ago` per AGENTS.md to identify zero-touch; archive (don't delete; keep in `docs/archives/`) the 24 routers; verifier audit | 8h | verifier confirms no orphaned references |
| P8: Protocol consolidation (M5, M6, S3, S4, S5) | Write 100-line PROTOCOL.md; deprecate 3 old protocol docs; topology_doctor.py drops boot-profiles + code-review-graph-protocol handlers | 10h | critic-opus reviews collapsed protocol; planning-lock receipt for topology_doctor changes |
| **Total** | | **~85-90h** | All gates per phase |

Estimated 2-3 engineer-weeks of concentrated work across rotating agents (matches R3's ~10h/phase cadence). Each phase is sized to fit a single packet's planning-lock + critic gate cycle.

### §5.2 Benefit (specific catches preserved)

Each catch from verdict §1.2 + §2 explicitly mapped to which post-reform mechanism preserves it:

| Catch (verdict cite) | Preserved by |
|---|---|
| Z2 retro 6-catch (compatibility-as-live-bypass, preflight-not-centralized, ACK-without-order-id, provenance-hash-over-mutated-fields, snapshot-freshness-without-time-semantics, 19 malformed slice-card YAML) | K1 critic + K2 verifier + K4 per-phase boot evidence + K7 retro discipline. **All 4 named mechanisms in Z2 retro retained.** |
| V2 BUSTED-HIGH 5+8 catches (pUSD vs USDC, V1 release date, heartbeat existed, post_only existed, unified V1+V2) | K6 cross-session memory (`feedback_on_chain_eth_call_for_token_identity`) + retained ≥2 WebFetch/round protocol in M5 PROTOCOL.md + K1 critic. |
| HK HKO truncation case | P4 type encoding (HKO_Truncation subclass) — STRICTLY BETTER than current YAML antibody per Fitz "make the category impossible" |
| All 5 semgrep-backed INVs (zeus-no-direct-close-from-engine etc.) | K9 — semgrep rules retained verbatim in semgrep_zeus.yml |
| All 12 schema-backed INVs | K8 — schema migration + tests/test_architecture_contracts.py 71-pass suite retained verbatim |
| The 14-mechanism anti-drift catalog's actual catches | K11 (5 retained anti-drift mechanisms with actual artifacts) — drop the 9 that never produced a catch |
| Settlement station ≠ airport station, Day0 ≠ historical hourly | K10 + retained `fatal_misreads.yaml` (140 lines after HK extraction) |

**Net benefit**: every empirically demonstrated catch is preserved by at least one post-reform mechanism. Zero load-bearing catches lost.

### §5.3 Cost-benefit ratio

- Cost: ~85h migration (2-3 engineer-weeks distributed)
- Benefit: -64% YAML LOC + -56% routers + -57% topology_doctor LOC + every empirical catch retained + 2 INVs upgraded from prose to type-encoded
- ROI: hour-of-saved-attention-per-future-session × N future sessions. If the harness saves 30min per future cold-start session × 100 sessions over the next 6 months = 50h saved. Migration breaks even within 6 months and is net-positive thereafter.

This is conservative; it assumes only attention-savings benefit. The greater benefit is that **the load-bearing core becomes more visible** — Z2-retro-style learnings will more frequently attribute catches to the actual mechanism (since fewer competing mechanisms dilute attribution), enabling tighter future evolution.

---

## §6 Forward question (verdict §6.4 item 2): at what model capability point does this reformed harness lose marginal value?

**The honest answer requires a 2D analysis: capability axis × cost axis.**

### §6.1 Capability axis — what reduces harness marginal value

| Capability dimension | Threshold where reformed harness loses ground | Asymptote |
|---|---|---|
| Effective context window (with attention quality intact) | ~500K tokens of CURATED context, not 1M raw context per Anthropic Sonnet 4.5 admission "1M achieves 78.2% but we report 200K as primary" (verdict §2 opponent cite) | ~2M tokens of CURATED context with same retrieval F1 as current 200K — at this point, the 800-LOC topology.yaml and 17 routers can be replaced by direct repo map (Aider repo-map pattern with PageRank importance — verbatim from aider.chat/docs/repomap.html: "Aider sends a repo map to the LLM along with each change request from the user" + "It does this by analyzing the full repo map using a graph ranking algorithm") |
| Cross-session memory native to model | ~partial (e.g., Anthropic's "memory tool" announced in Sept 2025 context-management post — already shipping) | Native cross-session memory at full semantic fidelity; at this point K6 collapses to a single API call instead of a `MEMORY.md` index |
| Spontaneous WebFetch + verification chain | Today's models already do this on prompt | Models that spontaneously verify factual claims via external sources without prompt; at this point M5 PROTOCOL.md drops the "≥2 WebFetch/round" rule |
| Critic dispatch as model-internal | Models that "internally critique" via reflection (early prototypes exist 2026) | Models that match adversarial-debate-quality critique as single-model self-reflection; at this point K1 collapses (still need K2 for independent verification) |
| Type system reasoning over prose | Already strong on Opus 4.7 — type errors caught at edit time | Models that propose type subclasses spontaneously when domain facts diverge; at this point fatal_misreads.yaml drops to ~50 LOC |

### §6.2 Cost axis — what changes the migration calculus

If model API cost continues dropping (Opus 4.7 → 4.8 → 5.0 trajectory, 5x/year cost reduction observed 2024-2025), the per-token cost of running the harness's catch-mechanisms (critic + verifier dispatches) drops. **At sufficiently low cost, more dispatches are cheaper than more YAML rules.** Reformed harness's K1 (critic) + K2 (verifier) become the right answer for a wider envelope of catches that today require YAML pre-cataloging.

### §6.3 Concrete asymptote prediction

**My best estimate of the asymptote**: 12-24 months from 2026-04-28.

- **Short term (6-12 months)**: reformed harness (5,500 LOC YAML / 18 routers / 5 anti-drift / 100-line PROTOCOL) is well-sized for Opus 4.7 / GPT 5.5. Pruning further damages catch-rate.

- **Medium term (12-24 months)**: native cross-session memory + Aider-style repo-map + spontaneous critic-internal reflection compress reformed harness to ~3,000 LOC YAML / 8 routers / 3 anti-drift / inline PROTOCOL in root AGENTS.md.

- **Long term (24+ months)**: K3 (semgrep antibodies) + K8 (schema migrations) + K10 (settlement gates) likely permanent — they encode TRADING-DOMAIN truths that survive any model improvement. K6 (memory) collapses to model-native API. K1+K2 (critic+verifier) likely remain because adversarial-quality independent review benefits from dispatching to a DIFFERENT model instance with DIFFERENT context (per LangGraph state-machine pattern, verbatim from langchain.com/blog/langgraph-multi-agent-workflows Jan 2024: "Each agent can have its own prompt, LLM, tools, and other custom code to best collaborate with the other agents").

**Asymptote floor**: ~1,500-2,000 LOC of trading-domain antibodies + the per-PR critic+verifier dispatch + the schema-backed INVs + settlement type encoding. Below this, marginal value of further pruning becomes negative because the trading-domain truths still need encoding somewhere.

### §6.4 Why this asymptote doesn't argue for whole-system replace TODAY

Even at the 12-24 month horizon, the migration cost from "current 25-30K LOC harness" to the asymptote is GREATER if done as whole-replace (rebuild everything) vs in-place pruning (this proposal's 85h, then iterate). Each in-place pruning step retains the catch-ledger; each whole-replace step starts from scratch and re-encounters every catch the current harness already has antibodies for.

---

## §7 NEW WebFetch evidence (per dispatch ≥2 NEW required, no recycle)

### WebFetch §7.1: Aider, "Repository map" (aider.chat/docs/repomap.html, **published 2023-10-22, current as of 2026-04**)

Verbatim quotes:
- "**Aider uses a concise map of your whole git repository.**"
- "**Aider sends a repo map to the LLM along with each change request from the user.**"
- "**Of course, for large repositories even just the repo map might be too large for the LLM's context window.**"
- "**Aider solves this problem by sending just the most relevant portions of the repo map.**"
- "**The token budget is influenced by the `--map-tokens` switch, which defaults to 1k tokens.**"
- "**Aider adjusts the size of the repo map dynamically based on the state of the chat.**"
- "**It does this by analyzing the full repo map using a graph ranking algorithm**"
- "**computed on a graph where each source file is a node and edges connect files which have dependencies.**"

**Application**: Aider — a deployed Anthropic-API-class coding agent — uses a **dynamically generated 1K-token repo map** with PageRank-style importance scoring instead of hand-curated routers. This is the asymptotic direction Zeus's reformed harness should evolve toward (per §6 medium-term). Concretely: `M4` (generate `docs_registry.yaml`) and `M8` (generate `script_manifest.yaml` + `test_topology.yaml`) are the in-place steps that move Zeus toward the Aider pattern. Cursor uses a similar RAG approach (per round-1 verdict §2 cite: "uploads files that have changed").

This source SUPPORTS in-place reform: don't throw out the existing manifests; replace the most-duplicating ones with generated equivalents while keeping the load-bearing antibody+critic+memory core.

### WebFetch §7.2: LangChain, "LangGraph: multi-agent workflows" (langchain.com/blog/langgraph-multi-agent-workflows, **published 2024-01-23**)

Verbatim quotes:
- "**a graph. In this approach, each agent is a node in the graph, and their connections are represented as an edge.**"
- "**LangGraph prefers an approach where you explicitly define different agents and transition probabilities, preferring to represent it as a graph.**"
- "**state machine** [...] **independent agent nodes become the states, and how those agents are connected is the transition matrices.**"
- "**Each agent can have its own prompt, LLM, tools, and other custom code to best collaborate with the other agents.**"
- "**control flow is managed by edges, and they communicate by adding to the graph's state.**"

**Application**: LangGraph — a production multi-agent framework on the same model generation — explicitly endorses STATE-MACHINE-AS-GRAPH for multi-agent orchestration. This is the CORRECT framing for Zeus's per-phase critic+verifier+executor dispatch (K1+K2 retained). It is NOT a framing that endorses prophylactic-YAML-cathedral. The LangGraph article does not recommend a particular harness size — but it explicitly recommends **distinct agent identity + state-as-shared-artifact** — which is exactly what K4 (per-phase boot evidence) + K5 (disk-first) + K7 (retro discipline) deliver in the reformed harness.

The opponent will point out LangGraph also uses minimal scaffolding around agents — which is exactly my proposal: keep the load-bearing per-agent dispatch + state-shared-via-disk; drop the 14-mechanism catalog and 7-class boot profile ritual that no industry parallel uses.

---

## §8 Summary table — concrete reformed-harness shape

| Surface | Current | Proposed | Migration cost |
|---|---|---|---|
| YAML LOC | 15,234 | 5,500 | ~50h (P5+P6+P8) |
| Manifests | 29 | 18 | included above |
| AGENTS.md routers | 41 | 18 | ~8h (P7) |
| `topology_doctor.py` LOC | 1,630 | ~700 | ~10h (P8) |
| r3 plan files | 189 | ~120 | ~10h (P8) |
| Anti-drift mechanisms | 14 cataloged | 5 retained | included in P8 |
| INVs | 30 | 28 | ~8h (P2) |
| `feedback_*` memory | 42 | ~32 | ~3h cleanup |
| Type-encoded antibodies (NEW) | 0 | 1 (HKO_Truncation/WMO_HalfUp) | ~12h (P4) |
| Total migration | | | **~85-90h** over 8 phases |

---

## §9 Round-2 verdict structure prediction

This proposal aligns with verdict §6.4 item 1 deferred debate: "The CORRECT size of the load-bearing core. Both sides agree there IS one; they disagree on size (proponent: 70-80% of current; opponent: 10-20%). Round-2 is where structured proposals (proponent's 'in-place harness reform' vs opponent's 'whole-system replace') get evaluated against shared criteria."

I propose the round-2 judge weigh:

1. **Migration cost asymmetry** (this proposal: 85h preserving every catch; opponent's whole-replace: estimated 200-300h to rebuild from scratch with no guarantee of preserving catches).
2. **Catch-preservation** (this proposal: §5.2 maps every empirical catch to a retained mechanism; opponent's burden: prove each catch can be reproduced from a clean-slate harness).
3. **Asymptote distance** (this proposal: ~1,500-2,000 LOC asymptote at 24 months; opponent's whole-replace would land closer to asymptote earlier but at higher migration cost upfront).
4. **Cost asymmetry of failure**: a missed catch in trading is unbounded-cost; an over-large harness is bounded-cost. Verdict §1.7 LOCKED that "Anthropic Dec 2024 'few lines of code' guidance does NOT apply directly to live-money trading mode" — this asymmetry argues for IN-PLACE pruning over whole-replace because in-place pruning preserves the conservative trading bias.

The honest disagreement at round-2 close: whether the asymmetry favors **paying 85h to keep what works while pruning aggressively** (this proposal) or **paying 200-300h to rebuild with full structural rethink** (opponent's likely proposal).

---

## §10 Process notes

- All cited quantitative baselines re-grep-verified at HEAD `874e00c` within last 10 min:
  - `find architecture -maxdepth 1 -name "*.yaml" | xargs wc -l` = 15,234 (matches verdict)
  - `git ls-files 'AGENTS.md' '*/AGENTS.md' | grep -v "^docs/archives/" | wc -l` = 41
  - `wc -l scripts/topology_doctor.py` = 1,630
  - `find docs/operations/task_2026-04-26_ultimate_plan -type f \( -name "*.md" -o -name "*.yaml" \) | wc -l` = 189
  - 42 memory files in `~/.claude/projects/.../memory/`
- 2 NEW WebFetch (NOT recycled from round-1's 5: Anthropic Jun13/Sep29 2025, Cognition Jun12 2025 full body, Contrary Cursor Dec11 2025, Anthropic Sonnet 4.5 announce, Cursor docs Rules):
  - Aider repo-map docs (2023-10-22, current as of 2026-04)
  - LangChain LangGraph multi-agent (2024-01-23)
- Engaged opponent's likely "whole-system replace" direction at face value before pivot (§0).
- All KEEP/MERGE/SUNSET items map to specific verdict.md citations.
- Quantitative target: 5,500 LOC YAML / 18 manifests / 18 routers / 5 anti-drift mechanisms / ~700 LOC topology_doctor (vs current 15,234 LOC / 29 manifests / 41 routers / 14 anti-drift / 1,630 LOC).
- Migration cost estimate: ~85-90h across 8 phases.
- Asymptote prediction: 12-24 months → ~1,500-2,000 LOC harness floor; in-place pruning has lower TOTAL migration cost than whole-replace at every horizon.
- LONG-LAST status maintained.
