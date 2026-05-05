# Strategy Redesign — Day0-as-Endgame Reframing

**Created**: 2026-05-04
**Authority basis**: operator directive 2026-05-04 — "Day0 是终章，不是一个策略，所有的订单最后都持有到 Day 0；对所有策略需要重新通过 Zeus 的完整数学流程进行重新推理找到最佳方案" + "需要通过 critic"
**Status**: PLAN ONLY — implementation BLOCKED on critic adversarial review + operator approval of decisions §7
**Branch / PR**: this plan lives under PR #49 (`activation-evidence-gating-2026-05-04`) until critic-approved; implementation gets a separate PR

---

## 0. Why this exists

Operator framing 2026-05-04:

> "day 0 现在已经是完全违背我的设计原则，所有的订单最后都持有到 day 0，这个 transition 不是一个策略而是一个终章"

Translation of the design principle being violated:
- Current code treats `DAY0_CAPTURE` as a peer entry mode alongside `OPENING_HUNT` and `UPDATE_REACTION`.
- The right framing: **every live position eventually transitions through a "Day0 terminal posture"** — the last 24h before settlement where the only relevant questions are *hold / exit / redeem*, not *enter*. Day0 is a phase of the position lifecycle, not a candidate-discovery mode.
- The current taxonomy invented `settlement_capture` as a "Day0 entry strategy" because the bottom-up edge-source classification needed a label for Day0-time entries. That label conflates terminal-phase posture with entry signal.

The operator further mandates: re-derive **all** strategies through Zeus's complete math pipeline. Don't patch §3.1-3.6 from `STRATEGIES_AND_GAPS.md` one by one; restart the derivation from first principles and let the strategy taxonomy fall out of the math.

This plan is the derivation, not the implementation. Implementation packets follow critic approval.

---

## 1. The complete Zeus math pipeline (code-grounded inventory)

Every live trade decision flows through these layers. This catalog is grounded in code, not the strategy doc.

```
                    ┌─────────────────────────────────────────────────────┐
                    │  L0  Source ingestion + provenance                  │
                    │      tigge / opendata / wu / hko / ogimet           │
                    │      → ensemble_snapshots_v2 + observation_instants │
                    └─────────────────────────────────────────────────────┘
                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    │  L1  Distribution synthesis                          │
                    │      σ_instrument(unit, city) (sigma sampling)       │
                    │      member_maxes_for_target_date (ensemble pull)    │
                    │      p_raw_vector_from_maxes (Monte Carlo n=10000)   │
                    │      → p_raw[bin] uncalibrated bin probabilities     │
                    └─────────────────────────────────────────────────────┘
                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    │  L2  Calibration                                     │
                    │      ExtendedPlattCalibrator (per source × metric)   │
                    │      DDD v2 Rail 1/Rail 2 (data-density-discount)    │
                    │      → p_calibrated[bin]                              │
                    └─────────────────────────────────────────────────────┘
                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    │  L3  Posterior fusion                                │
                    │      market_fusion (model + observation + market)    │
                    │      day0_router (Day0-phase weight shift)           │
                    │      → p_posterior[bin]                               │
                    └─────────────────────────────────────────────────────┘
                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    │  L4  Edge identification                             │
                    │      For each bin × direction (buy_yes, buy_no):     │
                    │      edge = p_posterior - market_price - taker_fee   │
                    │      Forward edge guard (CI-based)                   │
                    │      → edge candidates [Bin, direction, edge_bps]    │
                    └─────────────────────────────────────────────────────┘
                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    │  L5  FDR filter (false-discovery control)            │
                    │      Benjamini-Hochberg over the full hypothesis     │
                    │      family, NOT just positive-edge survivors        │
                    │      → edges_after_fdr ⊆ edges                       │
                    └─────────────────────────────────────────────────────┘
                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    │  L6  Sizing                                          │
                    │      kelly_size(p_posterior, fee-adj entry, b)       │
                    │      × strategy_kelly_multiplier (today: 1.0/.5/0)   │
                    │      × city_kelly_multiplier (Denver/Paris 0.7)      │
                    │      × dynamic_kelly_mult (¼-Kelly default)          │
                    │      × (1 − DDD_discount) (Rail 2 confidence)        │
                    │      Throttle: cluster>10% half, heat>25% half       │
                    └─────────────────────────────────────────────────────┘
                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    │  L7  Live submission gates                           │
                    │      _LIVE_ALLOWED_STRATEGIES allowlist              │
                    │      rollout_mode + promotion_evidence (Phase C)     │
                    │      RiskLevel ≠ RED                                 │
                    │      max_per_market / max_per_event / cluster caps   │
                    │      → submit | reject_with_typed_reason              │
                    └─────────────────────────────────────────────────────┘
                                          ↓
                    ┌─────────────────────────────────────────────────────┐
                    │  L8  Position lifecycle (post-fill)                  │
                    │      pending_entry → open → (terminal posture) →     │
                    │        settled / voided / quarantined / admin_closed │
                    │      Exit policies (8 types — see §3 below)          │
                    │      Reconciliation (chain ↔ local DB)               │
                    └─────────────────────────────────────────────────────┘
```

Key code-grounded references:
- L1: `src/signal/ensemble_signal.py::p_raw_vector_from_maxes`
- L2: `src/calibration/platt.py::ExtendedPlattCalibrator`, `src/oracle/data_density_discount.py`
- L3: `src/strategy/market_fusion.py`, `src/signal/day0_router.py`
- L4: `src/engine/evaluator.py` BinEdge construction
- L5: `src/strategy/fdr_filter.py::fdr_filter`
- L6: `src/strategy/kelly.py::kelly_size`, `STRATEGY_KELLY_MULTIPLIERS`, `CITY_KELLY_MULTIPLIERS`
- L7: `src/control/control_plane.py::_LIVE_ALLOWED_STRATEGIES`, `src/engine/evaluator.py::_live_entry_forecast_rollout_blocker`
- L8: `src/state/portfolio.py`, `src/engine/cycle_runtime.py` exit logic

---

## 2. The reframing — Day0 as a *position phase*, not an *entry mode*

### 2.1 Position phases (the new spine)

```
   PHASE-A: PRE-DAY0 (entry-eligible)
     hours_to_local_target_end > 24
     ── new positions can open here
     ── posterior is dominated by ensemble forecast (L1+L2)
     ── observation_instants only contributes through prior-day signal

   PHASE-B: DAY0-INTRADAY (entry-restricted, hold-dominant)
     0 < hours_to_local_target_end ≤ 24
     ── Day0Router activates: posterior weights shift toward observation
     ── new entries permitted ONLY when observation provides strict edge
        (i.e., the live ensemble + observed-so-far combo produces edge
         that pre-Day0 ensemble alone could not have detected)
     ── existing positions are evaluated for HOLD / EXIT / SCALE-OUT
     ── this is the "终章" — the math is dominated by closing-out,
        not opening

   PHASE-C: POST-RESOLUTION
     hours_to_local_target_end ≤ 0
     ── target day complete; resolution is determinate
     ── only HOLD-TO-REDEEM or FORCE-EXIT (toxicity / vig blow-up)
     ── settled at UMA on next cycle
```

The local-tz anchor is `target_local_date end-of-day` in the **city's** timezone (per operator's "当地市场 0 点前的 24 个小时"). It is NOT UMA's UTC-resolution time (~10:00 UTC the day after target_date). The two differ by up to ~10h depending on city longitude.

### 2.2 Why this matters mathematically

Under the current "Day0 is a separate strategy" framing:
- `settlement_capture` competes with `opening_inertia` for Kelly budget.
- `_LIVE_ALLOWED_STRATEGIES` includes `settlement_capture` as a peer entry.
- Per-trade Kelly multiplier `settlement_capture=1.0` treats Day0 entries as full-confidence.

Under the new "Day0 is terminal posture" framing:
- Day0 is the **phase**, not the strategy.
- An entry that opens during Phase-B is rare and conditional (edge must exist BECAUSE observation has narrowed the posterior, not despite the late timing).
- Most Day0-time activity is HOLD / EXIT logic on positions opened in Phase-A.
- Kelly sizing in Phase-B for new entries should be DOWN-WEIGHTED relative to Phase-A entries, not equal — because by Phase-B the residual uncertainty is concentrated in the diurnal+observation signal, which is a different (and historically less-calibrated) source than the ensemble.

### 2.3 What the four current `strategy_key` values become

| Current key | Current meaning | Reframed identity |
|---|---|---|
| `opening_inertia` | OPENING_HUNT mode entries (markets <24h old) | **Phase-A entry strategy** at *fresh-market* sub-condition |
| `center_buy` | UPDATE_REACTION + buy_yes + center bin | **Phase-A entry strategy** at *NWP-release* sub-condition, central-bin direction |
| `shoulder_sell` | UPDATE_REACTION + buy_no + shoulder bin | **Phase-A entry strategy** at *NWP-release* sub-condition, shoulder direction (buy_no) — **currently runtime-disabled** |
| `settlement_capture` | DAY0_CAPTURE mode entries (<6h to settle) | **Phase-B entry strategy** (Day0-conditional new opens) — but this label conflates entries with hold-management |

The current taxonomy is missing the explicit terminal-phase posture management — that logic is scattered across `cycle_runtime.py` exit policies + `day0_router.py` weight shifts but has no first-class strategy_key.

---

## 3. The eight exit policies under the reframing

Code (`docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md` §1.4) names these:

| Exit | Phase scope | Reframed role |
|---|---|---|
| `RED_FORCE_EXIT` | All phases | Global risk-off override (independent of phase) |
| `SETTLEMENT_IMMINENT` (<1h) | Phase-B tail | **Terminal-posture: redeem-or-flip decision** |
| `WHALE_TOXICITY` | All phases | Toxic-flow defense (independent of phase) |
| `MODEL_DIVERGENCE_PANIC` | Phase-A dominant | Cross-source disagreement breaks Phase-A entry premise |
| `FLASH_CRASH_PANIC` (rate < −0.15/hr) | All phases | Velocity defense |
| `VIG_EXTREME` | All phases | Microstructure defense |
| `DAY0_OBSERVATION_REVERSAL` | **Phase-B only** | Day0 observation flips edge — terminal-phase logic |
| `EDGE_REVERSAL` | All phases | Posterior-vs-price flips for 2+ cycles |

Reframed: `DAY0_OBSERVATION_REVERSAL` is the only **terminal-phase-native** exit. The others apply across phases. The "Day0 is endgame" principle says Phase-B exit logic should be richer than just one observation-reversal trigger — it should include hold-vs-redeem yield calculus, toxicity-adjusted spread, and time-to-settle decay.

**Decision: Phase-B needs at least three dedicated terminal-posture exit triggers** (proposed; operator decides):
- T-B1: `DAY0_OBSERVATION_REVERSAL` (already exists)
- T-B2: `HOLD_VS_REDEEM_YIELD_INVERSION` — when expected redemption value < current bid net of fees+slippage, flip to exit
- T-B3: `SETTLEMENT_DRIFT_DECAY` — as time-to-settle approaches 0, residual edge from posterior is increasingly captured in the price; if posterior – price < (taker_fee + microstructure_uncertainty), exit

---

## 4. Re-derived strategy taxonomy (proposed)

Replacing the current four `strategy_key` values:

```
ENTRY strategies (Phase-A only):
  E1  fresh_market_entry          — markets <24h since open, ensemble-dominant posterior
  E2  nwp_release_entry           — central-bin entries triggered by NWP cycle
  E3  shoulder_relative_entry     — buy-NO on shoulder bins (currently shoulder_sell, gated)
  E4  inverse_relative_entry      — buy-YES on shoulder, buy-NO on center (dormant pair §3.5)

ENTRY strategies (Phase-B only):
  E5  day0_observation_entry      — strict-conditional Phase-B entries when observation
                                    creates an edge that did not exist in Phase-A.
                                    Down-weighted Kelly (e.g., 0.3× of Phase-A peer)

POSTURE strategies (Phase-B only):
  P1  terminal_hold_to_redeem     — hold positions to settlement when expected redemption
                                    yield > exit yield
  P2  terminal_exit_on_decay      — exit when posterior - price - fees converges to zero
  P3  terminal_exit_on_reversal   — exit when Day0 observation flips edge sign

CONTINUOUS strategies (all phases):
  C1  posture_adjustment_on_event — re-evaluate sizing on price-drift event (§3.4 future)
  C2  middle_state_recheck        — diagnostic re-evaluation in 20h gap (§3.2 future)
```

**Total**: 5 entry strategies + 3 terminal-posture strategies + 2 continuous = 10 strategy keys (vs current 4).

The increase is not bloat — it makes the implicit phase distinction explicit and gives each Kelly-multiplier / FDR-bucket / reporting-cohort its own first-class identity.

---

## 5. Math implications layer-by-layer

### L1 (Distribution synthesis)
- **No change** in Phase-A.
- **Phase-B**: ensemble alone is insufficient; observation_instants becomes a co-equal posterior input. The Day0 router already does this; the change is making it explicit that Phase-B posterior fusion is structurally different from Phase-A.

### L2 (Calibration)
- **Per-phase calibration cohorts**: Platt fits today are per (source × metric). The reframing implies a two-level cohort: (source × metric × phase). Phase-A and Phase-B residuals have different distributions because Phase-B adds the observation signal.
- **Decision**: Do we maintain separate calibration tables for `phase=pre_day0` vs `phase=day0` and how does that interact with the existing `EXTENDED_PLATT` artifact?

### L3 (Posterior fusion)
- Day0Router's weight shift logic must be moved from a runtime `if mode == DAY0_CAPTURE` branch to an explicit phase parameter on every posterior-fusion call. Eliminates the implicit-phase-coupling bug class.

### L4 (Edge identification)
- Edge construction is unchanged.
- BUT: Phase-B edges should carry a `phase=day0` label so downstream FDR and sizing layers can apply phase-specific rules.

### L5 (FDR filter)
- **Per-phase FDR budget**: today FDR runs across all candidates regardless of phase. Phase-A and Phase-B candidates have different a-priori edge distributions; mixing them in one BH bucket either over-rejects Phase-A (too conservative) or under-rejects Phase-B (false discoveries on stale Phase-B edges).
- **Decision**: split FDR family by phase (pre_day0 family + day0 family).

### L6 (Sizing)
- Per-strategy Kelly multipliers replicate per-(strategy × phase). Phase-B multipliers default lower:
  ```
  E5 day0_observation_entry:        0.30  (vs E1 fresh_market_entry: 1.0)
  P1 terminal_hold_to_redeem:       N/A (no new size; managing existing)
  P2/P3 terminal_exit_on_*:         exit-only (size flips negative)
  ```
- **Decision**: exact multipliers for each new strategy_key.

### L7 (Live submission gates)
- `_LIVE_ALLOWED_STRATEGIES` becomes a function of (strategy × phase) rather than just strategy. Phase-B strategies require additional promotion evidence (e.g., observation-source freshness must be verified at gate-time).
- The Phase-C activation flags (`_ROLLOUT_GATE`, `_READINESS_WRITER`) gain a phase dimension: a single `state/entry_forecast_promotion_evidence.json` is replaced by a phase-keyed evidence map.

### L8 (Position lifecycle)
- The phase transition Phase-A → Phase-B becomes a first-class state-machine event with side effects:
  - Re-evaluate per-position `terminal_posture_decision`.
  - Trigger hold/exit/scale-out evaluator.
  - Snapshot position into a Phase-B audit trail.
- The current `ExitDecision` enum is extended with the three new T-B* triggers.

---

## 6. Implementation packets (post-critic-approval ordering)

```
P0  Phase-typing infrastructure
    - Define LifecyclePhase enum (PRE_DAY0 / DAY0 / POST_RESOLUTION)
    - Compute `hours_to_local_target_end` from city.timezone + target_local_date
    - Tag every candidate / decision / position with its phase
    - Tests: phase-transition relationship tests
    Risk: HIGH (touches every layer)

P1  L7 (gate) + L6 (sizing) phase-aware
    - _LIVE_ALLOWED_STRATEGIES becomes phase-aware
    - Per-phase Kelly multipliers
    - Tests: gate/sizing per-phase invariants
    Risk: MEDIUM

P2  Posture-strategy taxonomy (P1/P2/P3)
    - New strategy_keys for terminal posture management
    - Wire HOLD_TO_REDEEM and EXIT_ON_DECAY logic at L8
    - Tests: position-lifecycle relationship tests
    Risk: HIGH (changes exit semantics for existing positions)

P3  Phase-B entry rule (E5)
    - Day0-conditional entry with strict observation-source gate
    - Decision: do we replace settlement_capture entirely, or coexist for 1 release?
    Risk: MEDIUM

P4  Per-phase calibration cohorts (L2)
    - Extend Platt artifact to (source × metric × phase)
    - Backfill: do we have enough Phase-B observations per city to fit?
    Risk: HIGH (calibration regression risk)

P5  Per-phase FDR (L5)
    - Split BH family by phase
    Risk: LOW

P6  Inverse pair wiring (E4)
    - Activate shoulder_buy + center_sell after E1-E3 are stable
    Risk: MEDIUM (formerly §3.5)

P7  Continuous strategies (C1, C2)
    - PRICE_DRIFT_REACTION + MIDDLE_STATE_HUNT (formerly §3.2 + §3.4)
    Risk: HIGH (new cadence semantics)
```

**Each packet is a separate PR with its own critic pass.** No single PR closes more than one packet.

---

## 7. Open decisions for operator (BLOCKING for P0)

1. **Phase boundary anchor**: confirmed = "city-local end-of-target_date midnight" per operator's stated framing. Lock in this plan?
2. **Phase enum naming**: `LifecyclePhase.{PRE_DAY0, DAY0, POST_RESOLUTION}` vs `LifecyclePhase.{ENTRY_PHASE, TERMINAL_PHASE, RESOLVED}`?
3. **Naming consistency**: should the new strategy_keys use the labels in §4 or different ones? Compatibility with existing reporting schemas (`edge_observation.STRATEGY_KEYS`, `attribution_drift` classifier) matters.
4. **Per-phase calibration cohort**: implement P4 in this redesign series, or defer? P4 is the highest-risk packet and requires Platt rebuild evidence.
5. **Migration strategy for live positions**: when P0 ships, every `position_current` row needs a `phase` column. Backfill rule for existing rows?
6. **`settlement_capture` deprecation timeline**: hard-cut to E5 in P3, or run E5 + settlement_capture in parallel for one release with explicit attribution to compare?
7. **Critic scope**: critic-opus reviews this PLAN for adversarial flaws BEFORE any P0 code lands. Operator approves or sends back for revision.

---

## 8. Critic adversarial-review attack vectors

The critic should hammer at least these:

- **A1 Premise drift**: is "Day0 是终章" actually true for ALL position types (e.g., short-horizon settlement_capture-equivalents that never see a Phase-A)? If not, the reframing breaks for those.
- **A2 Math soundness**: §5's per-phase calibration claim requires a different Platt fit per phase. Do we have the calibration data volume to fit two cohorts per (source × metric)? Or does this re-introduce a small-sample bias problem the current single-cohort fit avoids?
- **A3 Strategy explosion**: 4 → 10 strategy_keys may overrun reporting (`edge_observation`, `attribution_drift`, DB CHECK constraints). Verify each downstream surface can absorb the cardinality before P0.
- **A4 Phase-transition atomicity**: a position's phase changes during a cycle (target_date passes midnight in city tz). Race between cycle and phase-transition observer? What if the cycle reads `phase=PRE_DAY0` but writes apply with `phase=DAY0` because midnight passed mid-cycle?
- **A5 Backwards compatibility**: existing `position_current` rows have no phase. Backfill to current phase or to creation phase? Different choices change historical attribution.
- **A6 §3.6 (OPENING_HUNT 24h cutoff)**: the current `OPENING_HUNT` window (markets <24h since open) is unrelated to the local-tz target_date phase boundary. Does the reframing keep both, or is one redundant?
- **A7 Day0Router move from runtime branch to explicit phase**: the current implicit branch couples Day0Router to `mode == DAY0_CAPTURE`. Refactor risk: does any existing site rely on that coupling for non-phase-related reasons?
- **A8 Test inflation**: P0 alone implies hundreds of new tests if we apply relationship-test rigor across every layer. What's the floor of must-have invariants vs nice-to-have coverage?
- **A9 Rollback of in-flight positions**: if P2 changes exit semantics and a live position was opened under old semantics, what's the migration policy? Hold to old rule or apply new rule retroactively?
- **A10 Calibration_pairs_v2 cohort split**: per-phase Platt would split the `calibration_pairs_v2` corpus. Do we have enough per-phase samples per city × metric? Operator's existing 32h n_mc=10000 rebuild was done on the unified corpus.

---

## 9. Non-goals (call out explicitly to bound scope)

- This plan does NOT propose changing the four current `strategy_key` values today. P0 ships infrastructure (phase typing); strategy renames come in P1-P6.
- This plan does NOT propose rebuilding `calibration_pairs_v2` until P4. The unified-cohort Platt artifact stays current through P0-P3.
- This plan does NOT change the bankroll truth chain. Bankroll is fixed (PR #46) and the current on-chain wallet remains the sole source.
- This plan does NOT touch the entry-forecast contract (PR #47) or activation gating (PR #49). Those are orthogonal and already complete.
- This plan does NOT depend on §3.1 / §3.5 / §3.6 from `STRATEGIES_AND_GAPS.md`. Those gaps are reframed within P0-P6 and supersede the original numbering.

---

## 10. Success criteria

This plan succeeds when:

1. critic-opus runs the §8 adversarial template and returns either APPROVED or APPROVED-WITH-CAVEATS. Caveats addressed in plan amendments before code starts.
2. Operator approves §7's open decisions in writing (commit message or PR body).
3. P0 ships in its own PR with: phase enum, phase computation, every layer tagged, relationship tests for phase-transition atomicity, no behavioral change at flag-default OR (more aggressive) phase-aware logic gated on a single new env flag for the first 24h.
4. Each subsequent packet (P1-P7) lands in its own PR, each with its own critic pass, with the relationship tests in P0 still green.
5. Final state: the four current `strategy_key` values are deprecated; live trading runs on the §4 ten-key taxonomy with phase-aware gates, sizing, calibration, FDR, and exit logic.

---

## Cross-references

- Code grounding: §1 paths
- Originating directive: operator 2026-05-04 chat session
- Subsumed gap doc: `docs/operations/task_2026-05-02_full_launch_audit/STRATEGIES_AND_GAPS.md` §3.1-3.6
- Subsumed plan: `docs/operations/task_2026-05-02_strategy_update_execution_plan/PLAN.md` (Stage 5+ work is recast into P0-P7)
- Adjacent contracts: PR #47 entry-forecast contract, PR #49 activation evidence gating, PR #46 bankroll doctrine
