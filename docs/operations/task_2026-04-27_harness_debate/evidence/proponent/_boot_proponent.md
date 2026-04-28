# Proponent Boot — Zeus Harness Debate 2026-04-27

Role: proponent-harness (defends harness as net-positive ROI for live-trading correctness on Opus 4.7 / GPT 5.5)
HEAD: 874e00cc0244135f49708682cab434b4d151d25d
Stance: harness is net-positive; the surface is doing real work that long-context models cannot replace, and the cost is bounded by enforceable maintenance discipline.

---

## Section 1 — Read list + key takeaway per file

| # | File | Key takeaway |
|---|------|--------------|
| 1 | `docs/operations/task_2026-04-27_harness_debate/TOPIC.md` (93 lines) | Question is ROI on Opus 4.7 / GPT 5.5 specifically. Three axes: file cleanliness, runtime helpfulness, topology-as-law. ≥2 WebFetch/round, file:line grep-verified within 10 min, sequential turns. |
| 2 | `docs/operations/task_2026-04-27_harness_debate/judge_ledger.md` (53 lines) | Judge does NOT participate; verdict synthesizes both sides; if net-negative, winner must propose subtraction list. |
| 3 | `AGENTS.md` (335 lines) | Three-layer authority: money path → topology digest → scoped routers. Hard rules: settlement gated by `SettlementSemantics.assert_settlement_value()`, RED→cancel+sweep, advisory-only risk forbidden (INV-05). The harness IS the encoding of these rules into machine-checkable form. |
| 4 | `workspace_map.md` (107 lines) | Visibility classes (tracked/derived/historical/runtime/generated) explicitly separate authority from noise. Manifest list (lines 56-78) is what differentiates Zeus from a generic Python repo. |
| 5 | `architecture/invariants.yaml` (370 lines, sampled 1-120) | INV-01..INV-30 each have `enforced_by:` block with semgrep_rule_ids/tests/schema/scripts. This is NOT prose — it is a contract surface where each rule names its enforcement antibody. INV-21 (Kelly requires distribution not bare price) literally compiles to a semgrep rule + a pytest. |
| 6 | `architecture/fatal_misreads.yaml` (153 lines) | 7 documented "false equivalences" (e.g. `api_returns_data == settlement_correct_source`, `airport_station == city_settlement_station`). These are exactly the cross-module relationship bugs that Fitz Constraint #4 (data provenance) names — they are NOT discoverable by grep or by reading function signatures, only by knowing the domain. |
| 7 | `architecture/code_review_graph_protocol.yaml` (62 lines) | Graph is explicitly "derived_not_authority". Two-stage protocol: semantic_boot (mandatory) → graph_context (optional). Forbidden uses enumerated. This is the harness preventing a known agent failure mode (graph-as-truth). |
| 8 | `architecture/task_boot_profiles.yaml` (360 lines, sampled) | 7 task classes (source_routing, settlement_semantics, hourly_observation_ingest, day0_monitoring, calibration, docs_authority, graph_review) each with `required_proofs` (question + evidence). Question-first boot — agent must ANSWER before code. |
| 9 | `architecture/source_rationale.yaml` (1573 lines, sampled 1-80) | Per-file rationale + hazard badges (SOURCE_TRUTH_PLANE_COLLAPSE, WMO_ROUNDING_LAW, ORACLE_TRUNCATION_BIAS, CALIBRATION_FAMILY_MIXING). These badges are the "antibody as type" pattern — they encode invariants the codebase cannot express in Python's type system. |
| 10 | `r3/R3_README.md` (270 lines) | R3 organizes 312h of work across 20 phase cards by data-lifecycle, with critical path Z0→Z1→Z2→Z3→Z4→U1→U2→M1→M2→M5→T1→A1→A2→G1. Antibody discipline (NC-NEW-A..J) is INHERITED from R2 — drift didn't erase prior antibodies. Memory cross-references are explicit. |
| 11 | `r3/IMPLEMENTATION_PROTOCOL.md` (sampled 1-200, 14 anti-drift mechanisms in §1) | Each predicted failure mode has a concrete artifact: citation drift → `r3_drift_check.py`; agent context loss → `INVARIANTS_LEDGER`; schema drift between phases → `frozen_interfaces/`; cross-phase invariant break → `invariant_ledger_check.py`. The protocol is itself a structural decision (Fitz #1). |
| 12 | `r3/learnings/Z2_codex_2026-04-27_retro.md` (88 lines) | Concrete win: critic-opus caught (a) compatibility code is live code, (b) preflight must be centralized not assumed, (c) ACK requires order id, (d) provenance hash over final fields, (e) snapshot freshness needs time semantics, (f) YAML is code (19 malformed slice-cards). All 6 caught BEFORE merge by harness gates. Without harness, all 6 would have shipped. |
| 13 | `RETROSPECTIVE_2026-04-26.md` (87 lines) | Harness operator's own self-criticism — 7 failure modes identified mid-run. KEY: even the failures (parallel firehose, boot/A2A interleave, file path mistakes, WebFetch fallback gap) were CAUGHT and recorded for next session. The retro itself is a harness output. Net debate result: 5+8 BUSTED-HIGH plan premises caught in V2 plan via WebFetch (pUSD vs USDC, V1 release date error, heartbeat existed in V1 v0.34.2, post_only existed). Without harness, plan would have been built on fiction. |

---

## Section 2 — Top 3 strongest pro-harness arguments

### Argument A — Antibody catch-rate is empirically high; the Z2 retro is a bounded case study

Z2 retro (`r3/learnings/Z2_codex_2026-04-27_retro.md:21-67`) lists 6 critic-caught implementation defects in a SINGLE phase, all of which would have shipped silent-but-broken to live trading without the harness gate:
- Compatibility code is live code (would have left a V1-shaped live bypass)
- Preflight not centralized (would have bypassed Q1 gate from one of two paths)
- ACK without order id (would have appended `SUBMIT_ACKED` to durable journal with phantom orders)
- Provenance hash over post-mutation fields (envelope hash disagrees with side/size)
- Snapshot freshness without time semantics (stale snapshots silently accepted)
- 19 malformed slice-card YAML (downstream phases would inherit broken contract)

Each of these is a Fitz #4 (data provenance) failure mode — the code is "correct" but the relationship between modules is broken. None are catchable by `pytest -q` alone; they require the critic-opus + verifier + topology gate stack. **Anchor**: Z2_retro lines 21-67. Net dollars saved: at least one (compat-bypass) is a live-money loss vector at unbounded scale.

### Argument B — Topology IS enforced law via tests/CI/semgrep, not LARP

The "topology-as-law" axis (TOPIC.md axis 3) attacks whether invariants are actually enforceable. The harness has the receipts:
- `architecture/invariants.yaml:6-101` — every INV-01..INV-30 has `enforced_by:` with `semgrep_rule_ids:`, `tests:`, `schema:`, `scripts:`. INV-21 (Kelly distribution requirement) is enforced by `tests/test_dual_track_law_stubs.py::test_kelly_input_carries_distributional_info` AND `semgrep zeus-no-bare-entry-price-kelly`.
- `architecture/code_review_graph_protocol.yaml:51-58` — explicit `forbidden_uses:` list (settlement truth, source validity, current fact freshness, authority rank, planning lock waiver, receipt or manifest waiver). Grep proves these are wired into `topology_doctor.py` (1630 lines per TOPIC.md row).
- `r3/IMPLEMENTATION_PROTOCOL.md` §1 row 6 — `scripts/invariant_ledger_check.py` runs cross-phase invariant verification on every PR. Not a doc; a CI gate.

The opponent will likely cite "769 .md files" and "29 yaml manifests" as bloat. The pro response: the manifests carry the 30+ invariants that NO long-context model can synthesize, because they encode TRADING-DOMAIN truths (settlement station ≠ airport station, Day0 source ≠ historical hourly source) that exist NOWHERE in the source code or git history.

### Argument C — Translation Loss is thermodynamic; harness is the only countermeasure

Per Fitz Constraint #2 (translation loss is thermodynamic): design intent survives at ~20% across sessions; only code/types/tests survive at ~100%. Opus 4.7's 1M context does NOT change this — it is a session-local property; cross-session, the Markov boundary remains.

Concrete evidence the harness is the antibody-encoding mechanism:
- `architecture/fatal_misreads.yaml:31-153` — 7 misreads each with `proof_files`, `invalidation_condition`, `tests`, `task_classes`. Without this manifest, every fresh agent would re-discover (via failure) that `api_returns_data != settlement_correct_source`. The manifest converts a thermodynamic loss into a one-time encoding.
- `RETROSPECTIVE_2026-04-26.md:7-12` — opponent-down's WebFetch (a harness-required step) caught 5+8 BUSTED-HIGH plan premises in the V2 plan. Without harness's ≥2 WebFetch/round rule, the V2 plan would have shipped with pUSD vs USDC confusion, V1 release date error, false claims that heartbeat/post_only didn't exist in V1.
- `feedback_on_chain_eth_call_for_token_identity` (memory) was DERIVED from this debate process and is now permanent cross-session knowledge. The harness is the antibody-production line.

The opponent must explain how a 1M-context model knows to dispatch `eth_call` against `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` for symbol() / name() resolution WITHOUT a memory entry telling it to. The honest answer: it doesn't. Long context only helps if the right facts are IN the context window. The harness is the curation layer that puts them there.

---

## Section 3 — Top 3 weakest spots + pre-rebuttal

### Weakness 1 — Surface-area / maintenance cost (29 yaml × 15,223 lines + 769 md + 41 AGENTS.md routers)

**Opponent attack**: "The harness has accumulated entropy faster than it sheds. 769 .md files is its own attention drain. The 41 AGENTS.md routers force agents to read 41 files before they can navigate."

**Pre-rebuttal**:
1. Refuse the false dichotomy. The agent does NOT read all 41 routers — `topology_doctor.py --navigation --task <task> --files <files>` returns a SCOPED route. Per `AGENTS.md:171-184`, the digest output is `allowed_files`, `forbidden_files`, `gates`, `stop_conditions` — all narrowed by task. The 41 routers are an INDEX, not a reading list.
2. The 15,223 lines of yaml are NOT consumed in a single session. `architecture/source_rationale.yaml:73-78` (package_defaults) shows the structure: each agent only reads the rationale for the specific src/** paths they are touching. The boot profile (`task_boot_profiles.yaml:38-86`) explicitly lists `required_reads` per task class.
3. Concession: there IS legitimate maintenance burden. The Z2 retro mentions 19 malformed slice-card YAML files. The honest pro position is that this maintenance burden is BOUNDED by `r3_drift_check.py` and `invariant_ledger_check.py` automation, and the cost is amortized across multiple agent sessions.

### Weakness 2 — Long-context Opus 4.7 (1M tokens) makes manifests redundant

**Opponent attack**: "With 1M context, the agent could read all 173 src .py + 241 tests .py + critical docs in one session. The manifest layer was a workaround for short context; on Opus 4.7 / GPT 5.5 it's pure overhead."

**Pre-rebuttal**:
1. Context window != attention quality. Even with 1M tokens, attention-over-context is sub-quadratic effective-bandwidth. Reading 1M tokens of raw source does NOT give the same understanding as reading the right 30K tokens — and the harness is the curation that picks the right 30K. Will cite published needle-in-haystack benchmarks in R1.
2. Manifests encode CROSS-SESSION knowledge. A single 1M-context session can read the entire Zeus repo, but it cannot remember what BROKE LAST WEEK. `architecture/fatal_misreads.yaml` is the only mechanism that survives compaction.
3. The harness specifically encodes things that DON'T exist in the source code: data provenance (which station settles which city/date), invariants over relationships (Day0 source ≠ historical hourly source), and forbidden semantic equivalences. No amount of source-code reading produces these.
4. Opus 4.7 / GPT 5.5 are MORE prone, not less, to plausible-but-wrong synthesis at scale. The harness's `forbidden_shortcuts:` (per `task_boot_profiles.yaml:75-78`) is exactly the antibody for capable-model overreach.

### Weakness 3 — RETROSPECTIVE_2026-04-26.md self-reports 7 process failures

**Opponent attack**: "Your own retrospective documents parallel firehose chaos, boot/A2A interleave faults, file path mistakes, token waste, WebFetch fallback gaps, notification-only messages — that's 7 process failures in a single debate cycle. The harness is producing the failures it then catches."

**Pre-rebuttal**:
1. Engage at face value. YES, the harness produced these failures. But each failure was CAUGHT, RECORDED, and converted into a permanent cross-session learning (e.g., `feedback_idle_only_bootstrap`, `feedback_verify_paths_before_prompts`, `feedback_converged_results_to_disk`, `feedback_on_chain_eth_call_for_token_identity`). The retrospective IS the immune-system response per Fitz Constraint #3.
2. The counterfactual is the right metric. Without the harness, would these 7 failures have surfaced AT ALL? Or would they have silently corrupted the V2 plan (which is what the BUSTED-HIGH catches prove was the actual stake)?
3. Net outcome of the debate cycle being retrospected: 5+8 BUSTED-HIGH plan premises caught in V2 plan; 80% citation drift caught at multi-review; pUSD/USDC dispute resolved via on-chain eth_call. The 7 process failures are the COST; the catches are the BENEFIT. Net ROI defense requires the math.
4. Concession: the harness has "harness-on-harness" overhead that needs continuous pruning. I will concede specific subtraction candidates in R2 if pressed (e.g., redundant memory entries about the harness itself).

---

## Section 4 — 3 external sources for R1 WebFetch

### Source 1 — Anthropic public posts on agent harness / scaffolding (2025-2026)

**URL intent**: `https://www.anthropic.com/engineering/built-multi-agent-research-system` (multi-agent research system writeup) + Anthropic's "Building effective agents" post.

**Why load-bearing**: Anthropic's own publications on long-context agent systems will likely document that scaffolding/harness/structured-context-curation REMAINS necessary at long-context scale. Quote will refute the "1M context kills harness" attack directly. If Anthropic itself runs an explicit harness around Opus, the burden of proof shifts to opponent.

### Source 2 — Cognition Labs / Devin / Cursor / Replit Agent architecture writeups

**URL intent**: Cognition Labs Devin technical post + Cursor "shadow workspace" / agent mode docs + Replit Agent v2/v3 architecture posts. Search target: "Cognition Devin architecture multi-agent harness 2025 2026" and "Cursor agent mode long context".

**Why load-bearing**: Industry leaders running long-context agents in production will document their OWN harness layers. If Devin/Cursor/Replit Agent run task-routing + invariant-checking + per-task scoped context curation, that is direct industry parallel to Zeus harness. The opponent will struggle to argue Zeus is unique in needing this.

### Source 3 — Long-context navigation benchmarks vs structured navigation

**URL intent**: `https://arxiv.org/` search for "long context retrieval needle haystack 2025" + "agent task routing structured context vs raw" + Anthropic's published needle-in-haystack ablations on Claude 4.6/4.7.

**Why load-bearing**: Published benchmarks on long-context retrieval degradation (especially "needle in haystack" + "multiple needles" variants) will quantify the attention-quality tax of feeding raw 1M-token context vs scoped 30K-token context. If retrieval F1 degrades >X% beyond N tokens, that is direct evidence the harness's curation layer is load-bearing. Anti-strawman: opponent's "1M context replaces harness" claim requires retrieval F1 ≈ 100% across all 1M tokens, which the literature does not support.

**Fallback if WebFetch blocked** (per memory `feedback_on_chain_eth_call_for_token_identity`): dispatch sub-agent with curl + alternate UA; or pivot to repo-internal proof (cite Z2 retro empirical evidence + RETROSPECTIVE_2026-04-26.md BUSTED-HIGH count) and label as "external pending — repo evidence stands alone".

---

## Self-discipline notes for R1

- ≤500 char/A2A turn; ≤200 char converged statement.
- Disk-first: write `evidence/proponent/R1_opening.md` BEFORE SendMessage.
- ≥2 WebFetch in R1 — call them in parallel with the boot reads to avoid sequential latency.
- Engage opponent's STRONGEST point at face value before pivoting (no strawman per anti-rubber-stamp rules).
- Acknowledge tradeoffs explicitly (per "what a win looks like" criterion 4).
- file:line cites grep-verified within 10 min.
- LONG-LAST: do NOT shut down; persist for round-2 alt-system debate.
