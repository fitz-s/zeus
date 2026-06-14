# P3 — ARCHITECTURE ADJUDICATION: Per-Layer Rebuild-vs-Repair Verdict

**Date:** 2026-06-14
**Mode:** READ-ONLY architecture adjudicator. No edits, no deploy, no live touch. DBs opened `?mode=ro`. Every load-bearing claim re-verified at source or DB **this session**, never inherited from the plans or red-teams.
**Role:** Adjudicate, per architectural layer (bin-belief/calibration, candidate-generation, gates, submit, reconcile), the verdict `TARGETED_FIX | PARTIAL_REBUILD | GROUND_UP_REBUILD`, with the K<<N kernel each collapses to, the KEEP-list, and the migration boundary. The decisive question the operator named: **is this a calibration repair, or does a layer need a rebuild?**
**Authority spine:** `P1_strategy_of_record.md`, the three completed workstreams (`P2_W-QLCB.md`, `P2_W-SUBMIT.md`, `P2_W-EDGE-LOCATE.md`), `P2_sequence_and_critical_path.md`, `P1_redteam_1.md` (RT#1 — base-rate kill), `P3_redteam_2.md` (RT#2 — crush-premise refutation + law-8), `diagnosis_confirmation.md`. Operator contract laws 1–8.

---

## 0. THE ADJUDICATOR'S RE-VERIFICATION (the facts every verdict rests on)

I did not inherit the disputed claims. I re-ran them at source and DB this session. The verification **overturns the strategy-of-record's headline** and **vindicates RT#2's refutation** — which forces a different per-layer verdict than P1/P2 proposed. The five facts that drive everything below:

| # | Claim under dispute | What I measured this session | Verdict |
|---|---|---|---|
| **F1** | Ring q_lcb is "crushed to ≈0 by the 3.0°C jitter" (P1 §1.2, W-QLCB §0.2) | Per-bin scan of 2266 posteriors carrying both maps: ring band (point .07–.15, n=6954) is **1.3% crushed, avg lcb/pt 0.36**; FUSED_NORMAL_FULL ring median q_lcb = **0.032**. Crush is **monotone in point-mass, tail-only** (pt<.01: 99.8% crushed). | **REFUTED.** Ring is NOT crushed. RT#2 confirmed. |
| **F2** | The live ring producer IS the `N(μ*, 3.0°C)` bootstrap (the producer-fix target N5) | Reproduced `_build_fused_q_bounds` exactly (`replacement_forecast_materializer.py:1399-1426`): at center_sigma=3.0 a ring bin gives q_lcb=**0.003** (lcb/pt 0.02). The live DB ring is **0.032** (10×). A 3.0 bootstrap **cannot** produce the live ring bound. | **The provenance `anchor_sigma_c=3.0` is NOT the σ the live ring bound was drawn at.** The producer-fix (N5) attacks a value the live ring does not use. |
| **F3** | The ring is under-priced ("realized 0.108 vs market 0.091", P1 §0; the suppressed alpha) | Event-level dedup of `no_trade_regret_events` (one row per city·date·bin, `would_have_won`): **every band's edge-after-fee is NEGATIVE.** Ring band (cost .05–.15): **66 events, 2 wins, WR 0.030, edge after 1¢ fee = −0.072.** | **REFUTED, and worse than neutral.** The ring is **over-priced / adversely-selected** for buy_yes. The "alpha" was the 40× row-count illusion. |
| **F4** | "The point q is honest; the correct bin is selected; Σq=winners" (P1 §2 KEEP; the law-8 foundation) | Law-8 per-event audit (°C cities, matched units, n=219): posterior peak == settled bin **only 26%**; within one ring step 67%; **mean \|peak−settled\| = 1.28°C ≈ one full ring step** (median 1.00°C). | **Aggregate-honest but per-event noisy.** The model's center MAE ≈ the ring-bin width. It **cannot reliably select the exact ring bin** — irreducible center noise, not a fixable bias. |
| **F5** | The UP arm reads a settlement-realized rate; mutates only the q_lcb seam | `_isotonic_realized_rate` short-circuits to `np.mean(ys)` on the single-band live stream (`settlement_backward_coverage.py:115-116`) → **pooled BIN BASE RATE**; `arm_gate_coverage_blocks` reads the SAME verdict **unconditionally** (`:228`). | **CONFIRMED** (RT#1/RT#2). UP arm is a base-rate admitter that also mutates the ARM interlock. |

**The synthesis of F1–F5 is the whole adjudication:** the model's belief is *honest at every layer* (point q calibrated in aggregate, ring q_lcb uncrushed at 0.032, gate rejecting correctly), and **the settled record says there is no positive-edge band to unblock** — the ring is over-priced and the peak bin is only ~1°C-accurate. **No layer is broken in a way that suppresses real alpha, because there is no suppressed real alpha at the ring.** This is the law-1 verdict, now proven event-level, not asserted. The architecture question therefore inverts: it is **not** "which layer to rebuild to release alpha" but **"which layers are honest and must be KEPT untouched, and which carry dishonest *vocabulary/construction* that should be SIMPLIFIED away regardless of alpha."**

Secondary facts re-confirmed (not disputed): submit arm ON (`real_order_submit_enabled=True`, `reactor_mode=live`, `edli_live_operator_authorized=True`, `edli_live_scope=forecast_plus_day0`); bundle path live (`...soft_anchor_trade_authority_enabled=True`); coverage gate live (`q_lcb_settlement_coverage_gate_enabled=True`); EMOS off (`edli_emos_ci_live_enabled=None`); C2/C3 selection-shrinkage import pinned `authority_on=False` (`event_reactor_adapter.py:2811`) → dead.

---

## 1. THE LAYER MAP (what the five layers are, in data-flow order)

```
 ┌─ L1 BIN-BELIEF / CALIBRATION ──────────────────────────────────────────────┐
 │ member resample → MAP-Platt(#129) → posterior point q (q_json)              │
 │ → predictive σ floor 1.0°C → bundle bound producer (_build_fused_q_bounds)  │
 │   → q_lcb_json (the bound), σ-shape fit (sigma_scale_fit.json, candidate)    │
 │ OWNS: bin identity, point belief, lower-bound width. Files: posterior chain, │
 │   replacement_forecast_materializer.py, src/calibration/*                    │
 └──────────────────────────────┬──────────────────────────────────────────────┘
 ┌─ L2 CANDIDATE-GENERATION ─────┴──────────────────────────────────────────────┐
 │ direction_law (T = max(1 step, k·σ), k=1.0) → bin scoping → FDR/lfsr (#60)    │
 │ → horse-race Kelly sizing (#63). OWNS: which (bin,direction) reach the gate.  │
 └──────────────────────────────┬──────────────────────────────────────────────┘
 ┌─ L3 GATES (ADMISSION) ────────┴──────────────────────────────────────────────┐
 │ capital_efficiency: (q_lcb−price)/price ≤ 0 → reject (live_admission.py:87)   │
 │ + coverage shrink (apply_settlement_coverage, shrink-only) + dead licensing   │
 │ vocab (source-allow-list, EMOS) + arm_gate_coverage_blocks. OWNS: admit/reject.│
 └──────────────────────────────┬──────────────────────────────────────────────┘
 ┌─ L4 SUBMIT ───────────────────┴──────────────────────────────────────────────┐
 │ K=1 fresh-book re-price → mode validate-or-abort (SUBMIT_ABORTED_MODE_FLIPPED) │
 │ → executor. OWNS: turning an admission into a venue order. arm ON.            │
 └──────────────────────────────┬──────────────────────────────────────────────┘
 ┌─ L5 RECONCILE / SETTLEMENT ───┴──────────────────────────────────────────────┐
 │ exchange_reconcile (M5/B1 latch) + settlement_outcomes(VERIFIED) + grade_     │
 │ receipt + no_trade_regret_events. OWNS: truth, latch self-heal, the grade.    │
 └──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. PER-LAYER ADJUDICATION

### L1 — BIN-BELIEF / CALIBRATION → **TARGETED_FIX** (construction-honesty cleanup only; NO alpha rebuild)

**Verdict: TARGETED_FIX. The belief is honest; do not rebuild it. The only legitimate work is removing dishonest *construction vocabulary*, not changing what the model believes.**

**Why not a rebuild (the operator's core question, answered):** the layer's output is calibrated. Aggregate Σq=winners (S1); ring q_lcb sits at an honest, uncrushed 0.032 (F1); the predictive-σ floor of 1.0°C (`replacement_forecast_materializer.py:1119`) matches the measured fused-center MAE of 0.85–1.31°C (the materializer's own comment, `:1217-1220`) **and** my independent peak-vs-settled MAE of 1.28°C (F4). The layer is telling the truth about an irreducibly ~1°C-uncertain center. A model whose center MAE equals the ring-bin width **cannot** be re-engineered into correct-exact-ring-bin selection — that uncertainty is physical (NWP ensemble spread at this lead), not a software defect. **Rebuilding L1 to chase exact-bin alpha would be rebuilding against the weather, not against a bug.** Law 8 is satisfied: the metadata foundation is honest; it is just not *sharp*, and sharpness is not available.

**What F2 changes vs the strategy-of-record:** P1/W-QLCB built their entire causal fix (N5 producer σ_center, N7 UP arm) on "the 3.0°C jitter crushes the ring." F2 proves the live ring bound (0.032) is **inconsistent with a 3.0 bootstrap** (which yields 0.003). So the producer-fix N5 attacks a code path that **does not produce the live ring bound** — it would change the TAIL (where 3.0 does bite, and where the bound *should* stay ≈0, F1/laws 1/4/8), re-loosening the settlement-dead 0/72 far tail. **N5 as specified is DEMOTED to a no-op-on-ring / tail-loosener and must NOT ship as an alpha lever** (RT#2 §2 confirmed; I confirm the numeric basis at F2).

**The K<<N kernel L1 collapses to:** ONE bound producer with an **honest, traced draw-σ**, replacing the current state where the provenance stamp (`anchor_sigma_c=3.0`) disagrees with the value the live ring bound was actually drawn at (F2). The collapse is *provenance honesty*, not a width change:
- **K-L1-a:** Trace why the live ring q_lcb is 0.032 when provenance says σ=3.0 (the D3-A1 diagnostic, never run). Almost certainly the live read path (`event_reactor_adapter._replacement_yes_lcb_for_bin`) is NOT using the 3.0-bootstrap map for FUSED_NORMAL_FULL ring bins — it reads a different persisted bound (the materializer comment at `:1216-1226` says the 3.0 bootstrap was *measured useless and not shipped on the live bound*; the live bound is the member-vote Wilson / per-bin path). **The fix is to make provenance name the σ the bound was actually drawn at** — a correctness/observability fix, zero behavior change, so the next session stops chasing a phantom 3.0 crush.
- **K-L1-b:** Keep the σ-shape fit (`sigma_scale_fit.json`) **candidate/shadow** — do NOT promote it to live point-q (N6). F3 kills its rationale: it was promoted to "move tail mass onto the ring so the ring point clears," but the ring is over-priced (edge −0.072), so a higher ring point q admits a **losing** trade. Promoting N6 moves the crown jewel to widen the ceiling of a money-losing band (RT#1 KILL-2). **N6 stays off the live path.**

**KEEP-list (touching any is out of scope — these are the honest foundation):**
- The point posterior chain end-to-end (member resample → MAP-Platt #129 → posterior). Σq=winners. **Crown jewel.**
- The 1.0°C predictive-σ floor (`:1119`) — it is the honest encoding of the measured center MAE; tightening it (N5 at 0.4°C → lcb/pt 0.94) is the over-confidence the materializer comment forbids and RT#2 §2 quantified.
- The honest zero q_lcb on far-tail / open-tail bins (F1 population A). Settlement-dead (0/72); never lift it.
- The `q_lcb ≤ q_point` invariant.

**Migration boundary:** K-L1-a is a provenance-string + diagnostic change inside `replacement_forecast_materializer.py` / the read path — **no rematerialization of beliefs, no live-table rename, byte-identical q values.** It is shippable immediately as a correctness cleanup. K-L1-b is a **deletion of a promotion plan**, not code (the σ-shape fit stays `candidate=true` as it is today).

**Provenance verdict on the disputed artifacts:** `sigma_center_fit.json` (N5) — **DEAD_DELETE as an alpha lever** (attacks a non-live σ; tail-loosener). The 3.0-bootstrap map for FUSED rows — **QUARANTINED** (known-useless per the materializer's own comment; confirm it is genuinely not the live ring read in K-L1-a, then it is dead vocabulary to remove).

---

### L2 — CANDIDATE-GENERATION → **TARGETED_FIX** (one constant → fitted boundary; NO new widener)

**Verdict: TARGETED_FIX, and the *widening* half (E3/N11) is DEMOTED to "do not ship."** The layer is structurally correct; it has one hardcoded constant (`DIRECTION_LAW_SIGMA_K=1.0`, `direction_law.py:57`) that the constant-elimination program (#64) should make a fitted boundary — but **fitted, not widened toward the ring**, because F3 shows the ring is a loss band.

**Why not a rebuild:** `direction_law` already scopes candidates to the near-center ring (`T = max(1 step, k·σ)`), which is exactly where the only conceivable edge could live and is the geometry that makes the Milan far-tail-YES loss unconstructable (law 6). It is sound. The only defect is the *hardcoded* `k=1.0` (a constant where a fitted boundary belongs, #64).

**What F3/F4 change vs the plan:** W-EDGE-LOCATE's E3 (N11) proposed to *widen* `T` per-cell (raise `k`) on E2-LICENSED cells to "restore the mid-band." F3 (every band's event-edge is negative) and F4 (mid-band c_mid edge −0.034) prove **there is no licensable band to widen toward.** Widening `T` admits *more* of a loss distribution. **N11 is DEMOTED to "do not ship" until/unless a band ever licenses positive at n≥30 (which F3 says it currently does not, anywhere).** The `k_eff` machinery may be *built* as the #64 fitted-boundary replacement (k fitted to the settlement coverage radius, RT#2's "Alt C"), but its default and its only honest setting today is **k = the fitted coverage radius ≈ current 1.0**, never a verdict-gated widen.

**The K<<N kernel L2 collapses to:** `T = max(1 step, k_fitted·σ)` where `k_fitted` is the settlement-residual coverage radius (one fitted boundary, #64), replacing the hardcoded 1.0 — **byte-identical to today** because the fitted radius ≈ 1.0 on the measured 1.28°C MAE. No verdict-gated widening seam (that was N11; demoted). One constant eliminated; zero geometry change.

**KEEP-list:** `direction_law` itself (encodes where settlement-backed edge *would* live; law 6 inviolable); FDR/lfsr (#60) and horse-race Kelly (#63) — downstream relays that re-compute correctly once belief is correct (law 8); they are not the defect.

**Migration boundary:** `k_eff` as a fitted-constant replacement is a `direction_law.py` change with a default-identical fit; **no new gate, no live-table change.** The widening seam (N11) is **not built.**

---

### L3 — GATES (ADMISSION) → **PARTIAL_REBUILD** (collapse dead vocabulary; KEEP the one honest gate; KILL the UP arm)

**Verdict: PARTIAL_REBUILD — but the rebuild is *subtraction*, not addition.** The honest core (`capital_efficiency`) is perfect and stays byte-identical. The *vocabulary around it* (three licensing dialects, a shrink-only coverage seam, a dead EMOS lane, a dead C2/C3 import) is N redundant authorities that collapse to K=1, and the proposed UP arm (N7) is a **net-new dishonest authority that must NOT be built.**

**Why PARTIAL_REBUILD and not TARGETED_FIX:** the layer carries genuine *authority sprawl* — the diagnosis and S5 enumerated multiple licensing vocabularies (`q_lcb_calibration_source` string allow-list at `live_admission.py:141,183`; EMOS_ANALYTIC; SETTLEMENT_ISOTONIC; the coverage verdict; `arm_gate_coverage_blocks`) where exactly **one** settlement-backed authority should govern. Collapsing N authorities to K=1 is a structural change to the layer's authority graph (a partial rebuild of the *licensing wiring*), larger than a point fix — but it is pure subtraction, and the decision arithmetic (`(q_lcb−price)/price`) is untouched.

**Why the UP arm (N7) must NOT be built (the layer's biggest risk):** F5 + RT#1 + RT#2 converge — the UP arm lifts q_lcb toward `_isotonic_realized_rate`, which on the live single-band stream is `np.mean(ys)` = **the bin's unconditional base rate** (law 4: "in the price, NOT alpha"). It is gated by a backtest (D2) that reads the same base-rate pool (blind to the defect), it mutates `arm_gate_coverage_blocks` (the operator's verified-correct-before-live interlock, un-modeled by any plan), and — decisively — F3 shows the ring it would admit **loses 7.2¢/share after fee.** The UP arm is an engine for admitting a measured-losing distribution under a base-rate costume. **N7 is KILLED, not demoted.** No forecast-conditional redesign is warranted either, because F3 says the target band is unprofitable even with a perfect conditional rate.

**The K<<N kernel L3 collapses to (the honest K=1 + the deletions):**
- **K-L3 (the one gate):** `capital_efficiency`: `(q_lcb − price)/price ≤ 0 → reject` (`live_admission.py:87-119`). **Byte-identical. The crown gate. Never loosen; never add an UP arm to its input.**
- **Delete (pure subtraction, all confirmed dead-live):** the source-string allow-list licensing (`live_admission.py:141,183` G2/G4 — never stamps `EMOS_ANALYTIC` live); the dead C2/C3 selection-shrinkage import (`event_reactor_adapter.py:2811`, `authority_on=False` → NULL stamps); the dead δ-penalty / shadow N_eff·JS fields on the live q_lcb seam. (This is N2, **KEEP/ship** — every deleted path is byte-identical-live.)
- **Collapse the coverage seam to its honest single direction:** `apply_settlement_coverage` (`:204-225`) stays **shrink-only** (its "Never widen" comment is *correct* — F1 shows there is nothing to widen toward; the ring is honest already). The dishonesty was the *plan to make it bidirectional*, not the seam. **KEEP the shrink-only seam; KILL the bidirectional rewrite.** This auto-resolves KILL-3: with no bidirectional rewrite, `arm_gate_coverage_blocks` is untouched and the ARM interlock is preserved.
- **D6 guard:** keep the `coverage_unlicensed_tail` antibody's *effect* (it fail-closes the unbacked far tail → `capital_efficiency` rejects). Collapse only the *vocabulary* (make the live coverage verdict the sole license authority, the string a telemetry field), never the antibody.

**KEEP-list:** `capital_efficiency` (the honest arbiter); the shrink-only coverage direction; `arm_gate_coverage_blocks` (frozen — the ARM interlock; the bidirectional rewrite that threatened it is killed); the profitable-era NO eligibility gate (#74) and market-anchor cap (#7472) — they close real loss classes; INV-37 cross-DB discipline.

**Migration boundary:** the deletions (N2) are byte-identical-live, shippable now. The licensing-vocabulary collapse touches `live_admission.py` G2/G4 + the source-string reader — a contained change with the `coverage_unlicensed_tail` regression antibody (Milan-24C) re-homed onto the surviving single verdict. **No DB schema change; no live-table rename; the decision arithmetic is frozen.** The UP arm module (`settlement_calibrated_qlcb.py`) is **never created.**

---

### L4 — SUBMIT → **TARGETED_FIX** (one terminal-abort → re-decision; correctly LATENT)

**Verdict: TARGETED_FIX, and correctly sequenced LAST / latent.** The arm is ON (confirmed); the path is structurally correct (K=1 fresh-book re-price, #39). The single real defect is `SUBMIT_ABORTED_MODE_FLIPPED` terminally discarding an admitted candidate on a transient maker/taker tick (W-S2). The fix is to make mode-flip a re-decision under the *same* `capital_efficiency` criterion (the K-spine), which is the correct SIMPLIFY.

**Why not a rebuild:** there is no master-flag dark-hold (arm ON), no broken plumbing — the diagnosis proved the submit-stage non-fills are honest fail-closed degrades (allocator/portfolio/arm-token momentarily unavailable) plus one code gate. A rebuild is unwarranted; this is a one-function change (`_validate_final_order_mode_or_abort` → `_resolve_final_order_mode_or_abort`).

**What F3 changes:** W-SUBMIT already sequenced N9 last ("only after a real ring candidate exists"). F3 strengthens this to a **hard latency**: since *no* band is profitable event-level, shipping N9 now would only speed a losing distribution to the venue (laws 2/4 FAILURE). N9's value is **purely contingent** on L3 ever admitting a profitable candidate — which F3 says it currently will not. So N9 is **built-and-tested but NOT armed** until a profitable admission exists. RT#1 KILL-4's TAKER→MAKER looser-cost leak is real and the RED test must include a settlement-deadness check, but it is moot while no profitable candidate flows.

**The K<<N kernel L4 collapses to:** ONE submit-mode authority — re-price the chosen mode on the fresh book; if it clears the same `q_lcb>price` (K-spine), submit in the fresh mode; else abort. This collapses the separate "mode-equality validator" vocabulary into the one admission criterion (gate count −1). Plus the W-S1 observability assert (every receipt names its `submit_lane`) and the W-S3 tick-size de-dup (#64 constant).

**KEEP-list:** the master arm and its honest fail-closed degrades (allocator/portfolio/arm-token guards — intended arm-state, not gates to remove); the maker-book-agreement wall and taker spread guard (downstream walls that still fire on the resolved mode); K=1 fresh-book authority (#39); the P0 fail-closed "never default a taker submit" invariant.

**Migration boundary:** code-path-local in `event_reactor_adapter.py` submit boundary; no schema, no live-table, no latch touch. **N9 is merged but ARM-gated OFF until a profitable admission exists.**

---

### L5 — RECONCILE / SETTLEMENT → **TARGETED_FIX** (observability only; the truth layer is sound)

**Verdict: TARGETED_FIX (telemetry only). This is the healthiest layer; do not touch the mechanics.** The M5/B1 latch self-heals (verified OPEN, 0 unresolved); `settlement_outcomes(VERIFIED)` is the single truth authority (7018 rows); `grade_receipt` is the single preimage-correct grader; `no_trade_regret_events` provides the counterfactual substrate that made F3 measurable.

**The one real risk this layer must own (surfaced by F4):** the grader's bin-match must be preimage-correct, because F4's law-8 audit is only trustworthy if `winning_bin` is derived through the same `settlement_semantics` preimage as the posterior bins. My F4 audit hit a label-space mismatch (question-string vs bin-label vs raw value, and °C/°F unit mixing) on the first pass — which is itself the warning: **the cross-grade between posterior-bin-identity and settlement-bin-identity is not yet asserted-equal anywhere.** The E1/E2 instrument (W-EDGE-LOCATE, the read-only edge-location query + grading harness) is the correct home for this, and it is **the one genuinely new artifact worth building** — not as an alpha gate (there is no alpha to gate) but as the **standing settlement-honesty instrument** that (a) re-grades through the spine to catch any `would_have_won` mislabel (law 8), and (b) emits the dated, numeric "every band is event-level unprofitable" verdict that is the operator's law-1 DONE.

**The K<<N kernel L5 collapses to:** the existing reconcile/settlement mechanics UNCHANGED + the W-S1 lane-stamp assert (L4) + the E1/E2 read-only grading harness as the **sole** counterfactual-edge instrument (QUARANTINE `edge_observation.py` for this purpose — it reads executed fills, of which there are ~zero). E2 persists only a dated JSON evidence artifact, **never a DB authority** (operator no-gate-mass law) — recomputable from settlement, which is the only truth.

**KEEP-list:** `settlement_outcomes(VERIFIED)` as sole truth; `grade_receipt` as sole grader; the M5/B1 self-healing latch (#31); `no_trade_regret_events` writer; INV-37 ATTACH+SAVEPOINT for the E1 cross-DB read.

**Migration boundary:** E1/E2 are pure read-only analysis emitting JSON under `docs/evidence/` — **nothing to roll back, cannot break live by construction.** No schema migration (no new table).

---

## 3. THE ADJUDICATION TABLE (one screen)

| Layer | Verdict | K<<N kernel it collapses to | KEEP untouched | The plan node(s) this overturns |
|---|---|---|---|---|
| **L1 bin-belief / calibration** | **TARGETED_FIX** (provenance honesty only) | One bound producer whose provenance names its *actual* draw-σ (K-L1-a); σ-shape fit stays candidate (K-L1-b) | point-q chain (crown jewel); 1.0°C predictive-σ floor; honest far-tail zero; q_lcb≤q_point | **N5 DEAD** (attacks non-live σ; tail-loosener). **N6 NOT SHIPPED** (raises ceiling of a loss band). |
| **L2 candidate-gen** | **TARGETED_FIX** (1 constant → fitted boundary) | `T=max(1 step, k_fitted·σ)`, k_fitted=coverage radius ≈ today's 1.0 | direction_law geometry (law 6); FDR/lfsr; horse-race Kelly | **N11 (E3 widen) NOT SHIPPED** (widens toward a loss band); k_eff built only as #64 fitted-constant, default-identical. |
| **L3 gates (admission)** | **PARTIAL_REBUILD** (collapse vocabulary; subtraction) | `capital_efficiency` (byte-identical) + shrink-only coverage; N licensing dialects → 1 verdict | capital_efficiency; shrink-only direction; arm_gate_coverage_blocks (frozen); #74; #7472 cap; INV-37 | **N7 (UP arm) KILLED** (base-rate admitter; mutates ARM interlock; admits a −0.072 band). **Bidirectional rewrite KILLED.** N2 deletions KEEP. |
| **L4 submit** | **TARGETED_FIX** (1 abort → re-decision) | One submit-mode authority under the K-spine; lane-stamp assert; tick de-dup | master arm + honest degrades; maker/taker walls; K=1 (#39); fail-closed-no-default-taker | **N9 built but ARM-gated OFF** until a profitable admission exists (F3); add cheap-tail RED test. N8/N10 KEEP. |
| **L5 reconcile / settlement** | **TARGETED_FIX** (telemetry only) | Existing mechanics + E1/E2 read-only grading harness (JSON, no DB authority) | settlement_outcomes(VERIFIED); grade_receipt; M5/B1 latch; regret writer | E1/E2 KEEP (the one worthwhile new artifact — as a settlement-honesty instrument, not an alpha gate). `edge_observation.py` QUARANTINED. |

**Net architectural verdict:** **NO layer needs a GROUND_UP_REBUILD. One layer (L3) needs a PARTIAL_REBUILD that is entirely *subtraction* (collapse N licensing authorities to 1; the decision arithmetic is frozen). Four layers are TARGETED_FIX, and three of those fixes are observability/provenance-honesty with zero behavior change.** This is **a calibration-honesty + vocabulary-collapse problem, NOT a rebuild problem** — which is the direct answer to the operator's framing.

---

## 4. WHAT THE RED-TEAMS FORCED ME TO OVERTURN (the delta from P1/P2)

The strategy-of-record (P1) and the q_lcb workstream (W-QLCB) proposed a three-part *causal fix* (N5 producer σ_center + N6 σ-shape point-q + N7 bidirectional UP arm) to "release the suppressed ring alpha." **My re-verification kills all three as alpha levers:**

1. **There is no suppressed ring alpha** (F3: event-level edge −0.072 on the ring; every band negative). The premise of the entire causal fix is refuted at settlement.
2. **The ring is not crushed** (F1) and **the live ring bound is not the 3.0 bootstrap** (F2), so N5 attacks a non-live σ and would loosen the honest dead tail.
3. **The UP arm reads a base rate and mutates the ARM interlock** (F5) and would admit the measured-losing band.
4. **The peak bin is only ~1°C-accurate** (F4) — the model honestly cannot select the exact ring bin, so no q_lcb/point-q fix can manufacture correct-bin edge that the weather does not provide.

**The honest residue is small and almost entirely subtraction:** the SIMPLIFY thrusts (T1 observability, T2 dead-gate deletion, the L3 vocabulary collapse, the L4 mode re-decision, the L2 constant-elimination, the L5 grading instrument) are all sound and survive — they make the system *honest and legible*, and they are worth shipping **regardless of alpha** because they collapse N→K and remove lies. **The two *causal alpha* fixes (the bidirectional q_lcb authority and the σ promotions) do not survive and must not ship.** The operator's DONE is reached not by a fill but by the L5 instrument emitting the dated, event-level, vs-market verdict that **the ring (and every band) is efficient-to-adverse for buy_yes after the 1¢ fee** — the law-1 outcome, now proven, that the strategy-of-record said it was open to but built a fix to avoid.

---

## 5. THE MIGRATION BOUNDARY (one paragraph, for the implementation planner)

Everything that ships is **byte-identical-live or read-only** at the point of merge: L1's provenance-honesty fix (no belief change), L2's fitted-constant (default ≈ 1.0), L3's dead-path deletions + licensing-vocabulary collapse (every deleted path confirmed dead-live; `capital_efficiency` arithmetic frozen; `arm_gate_coverage_blocks` frozen), L4's mode re-decision (ARM-gated OFF until a profitable admission exists) + lane-stamp assert, and L5's read-only E1/E2 JSON instrument. **No live-table rename, no belief rematerialization, no DB schema migration, no new persisted gate authority, no new flag that defaults ON.** The two things that the prior plan would have built and that this adjudication forbids — the `settlement_calibrated_qlcb.py` bidirectional UP arm (L3) and the `sigma_center_fit.json` producer lever (L1/N5) — are **not created**. The single new artifact worth building is the L5 settlement-honesty grading harness, and it can break nothing because it only reads settlement and writes a dated evidence JSON. The boundary between "ship now" and "ARM-gated" is exactly the boundary between *honesty/legibility work* (ship) and *anything that changes an admit/submit decision* (ARM-gated on the E2 dated evidence) — and per F3, the E2 evidence today licenses **no** profitable band, so nothing crosses the ARM boundary until the settled record changes.

*End P3 architecture adjudication. Read-only; no production code or daemon changed. Every load-bearing claim cited to file:line, table, or query+counts run this session: per-bin crush scan (2266 posteriors), `_build_fused_q_bounds` σ-sweep reproduction, event-level regret dedup (negative edge every band), law-8 peak-vs-settled MAE audit (1.28°C, °C-only n=219), `_isotonic_realized_rate` short-circuit + `arm_gate_coverage_blocks` (`settlement_backward_coverage.py:115,228`), submit-arm config, dead C2/C3 import (`event_reactor_adapter.py:2811`).*
