# Zeus FINAL Spec

Version: 2026-04-05 — Terminal document
Status: **FINAL**. Not v2, not revision-pending. This is the last architecture document Zeus will receive.
Author: Fitz, with Claude Opus 4.6 synthesis
Predecessors merged:
- `zeus_durable_architecture_spec.md` (2026-04-02) — P0–P8 foundation
- `TOP_PRIORITY_zeus_reality_crisis_response.md` (2026-04-03) — 17 reality gaps
- `reality_contract_mainline_plan.md` (2026-04-04) — P9–P11 extension
- `reality_contract_mainline_test_spec.md` (2026-04-04) — verification shape
- `venus_operator_architecture.md` (2026-04-03) — three-layer consciousness
- Math lane audit (2026-04-05) — D1–D6 + K0 epistemic fragmentation
- Endgame clause (2026-04-05) — 1 week live / ±$0.1 binary gate

---

## Reading instructions

This document has three parts and a terminal clause:

- **Part I — Installed foundation** (P0–P8, INV-01..10): what is already built and holding.
- **Part II — Final extensions** (P9–P11, INV-11..13): what must land before live.
- **Part III — Endgame clause**: the binary gate that ends Zeus, either into real operation or into archive.
- **Appendix**: Venus architecture content preserved as implementation extension, not competing authority.

Read order for a first-time agent:
1. §0 Executive frame
2. Part III Endgame clause (to understand the stakes before deciding anything)
3. §3 Invariants (INV-01..13)
4. §4 Priority structure
5. The specific P layer your packet touches
6. Math lane findings in P9

---

## §0 Executive frame

Zeus is a durable, position-governed weather-arbitrage trading runtime built against Polymarket. It has:

- One canonical lifecycle authority.
- One canonical strategy governance key.
- One point-in-time learning chain.
- One executable protective policy substrate.
- One bounded lifecycle grammar.
- One operator-facing derived surface.
- One packetized coding discipline (P8) that prevents LLMs from turning architecture into whack-a-mole patchwork.

As of 2026-04-05, Zeus has:
- 112 math tests passing, 97.8% high-confidence hit rate in the signal layer.
- 104 paper trades executed, 33 closed positions.
- Paper P&L: **-$6.82**. Never taken to live.
- P0–P8 foundation installed; P7R7 is the current autonomous stop boundary.
- Two discovered failure classes that block live: (a) epistemic fragmentation across signal→strategy→execution (§P9), and (b) implicit external-reality assumption drift (§P10).

Zeus will not expand further. This spec closes the architecture. After it lands, Zeus either trades live under the §Endgame clause or is archived.

---

## §1 Source basis

Four sources ground this spec:

1. **Completed internal architecture** (`zeus_durable_architecture_spec.md`): canonical lifecycle authority, execution truth, strategy-aware protection, learning facts, lifecycle grammar, packetized discipline. All P0–P8 content landed in the repo.
2. **External reality crisis response** (`TOP_PRIORITY_zeus_reality_crisis_response.md`): 17 external-assumption gaps with market reality; Reality Contract Layer proposal.
3. **Math lane audit** (2026-04-05 conversation): D1–D6 decision-layer math design gaps; 17 unregistered hardcoded constants in `src/strategy/`; K0 diagnosis of epistemic fragmentation confirmed by third-party Gemini review.
4. **Venus operator architecture** (`venus_operator_architecture.md`, `venus_zeus_audit_integration_plan.md`): three-layer consciousness (RiskGuard reflex / Zeus execution / Venus reasoning), heartbeat/daily/weekly audit orchestration, antibody framework.

---

## §2 Architectural intent

Zeus is not evolving into a generalized workflow platform. Zeus is evolving into a durable trading runtime with:

- one canonical lifecycle authority,
- one canonical governance key (`strategy_key`),
- one point-in-time learning chain,
- one executable protective policy substrate,
- one bounded lifecycle grammar,
- one operator-facing derived surface,
- **one epistemic contract layer that prevents cross-layer semantic collapse** (new, P9),
- **one external-reality contract layer that surfaces assumption drift** (new, P10),
- **one external-audit boundary that keeps Venus consumer-side** (new, P11),
- one coding discipline that prevents LLMs from turning architecture into whack-a-mole patchwork.

### Explicit non-goals
- Generalized distributed event bus.
- Asynchronous projector fabric.
- New strategy taxonomies.
- New discovery modes.
- **Expanding signal sophistication** — the signal layer is frozen after the 2026-04-04 math lane closure.
- Rebuilding research stack or backtest engine.
- UI expansion beyond minimal operator surfaces.
- Parallel truth surfaces "temporarily" without deletion plan.
- **Any architecture work beyond P11.** After P11 closes, all further effort is implementation + operation, not spec.

---

## §3 Architectural invariants

These are spec authority. Any patch that breaks one is invalid unless the spec is explicitly revised — **and this spec is FINAL, so revisions are forbidden**.

### INV-01. Exit is not local close.
A monitor decision may produce `EXIT_INTENT`. It may not directly imply economic closure or lifecycle completion.

### INV-02. Settlement is not exit.
Economic exit and final market settlement are separate lifecycle events.

### INV-03. Canonical authority is append-first.
Lifecycle truth is represented as canonical events plus a deterministic current projection.

### INV-04. `strategy_key` is the sole governance key.
`edge_source`, `discovery_mode`, `entry_method`, and scheduler mode are metadata, not competing governance centers.

### INV-05. Risk must change behavior.
If a risk or control command cannot alter evaluator/sizing/execution outcome, it is theater.

### INV-06. Point-in-time truth beats hindsight truth.
Learning data must preserve what was knowable at decision time, not what is visible later.

### INV-07. Lifecycle grammar is finite.
States exist only if they change governance, execution, or reconciliation semantics.

### INV-08. Every write path has one transaction boundary.
For canonical lifecycle writes, event append and current projection update must occur in the same SQLite transaction.

### INV-09. Missing data is first-class truth.
Unavailable or rate-limited upstream data must be represented explicitly in learning and diagnostics.

### INV-10. LLM output is never authority.
Spec, invariants, tests, and evidence are authority. Generated code is only a proposal until validated.

### INV-11. External assumptions are explicit and verified. **(NEW)**
Every assumption about external market reality (fees, tick size, settlement source, protocol timing, resolution rules) must be represented as a typed `RealityContract` with TTL, verification method, and criticality. Silent assumption drift is forbidden. Hardcoded numeric values mirroring an external fact are INV-11 violations unless wrapped in a contract.

### INV-12. Cross-layer probability/edge carries provenance. **(NEW — math lane outcome)**
Floats crossing signal→strategy, strategy→execution, or execution→settlement boundaries must carry a typed wrapper declaring: optimization target (accuracy vs EV), source (model vs market vs blended), confidence bound, and whether external costs (fee/slippage/vig) have been deducted. A bare `float` at a cross-layer seam is an INV-12 violation.

### INV-13. Unregistered numeric constants in Kelly-class multiplicative cascades are forbidden. **(NEW — math lane outcome)**
Any additive/multiplicative adjustment to α, Kelly multiplier, edge threshold, or sizing fraction must be registered in a `ProvenanceRegistry` with: declared optimization target, data basis, replacement criteria. An unregistered constant in a cascade product is implicitly weight=1.0 (i.e., neutralized) until registered.

---

## §4 Priority structure

```
P0  Bearing-capacity prerequisites                    [installed]
P1  Canonical lifecycle authority                     [installed]
P2  Execution truth and exit lifecycle                [installed]
P3  Strategy-aware protective spine                   [installed]
P4  Learning spine and data availability truth        [installed]
P5  Lifecycle phase engine                            [installed]
P6  Operator, control, observability compression      [installed]
P7  Migration plan (R1–R7 complete; M4 deferred)      [R7 autonomous stop]
P8  Human + LLM coding operating system               [installed]

P9  Epistemic contract and provenance enforcement     [REQUIRED BEFORE LIVE]
P10 External reality contract layer                   [REQUIRED BEFORE LIVE]
P11 External audit boundary + retirement readiness    [LANDS WITH P10]

§Endgame  Binary live gate                            [AFTER P9-P11]
```

P0–P8 are documented in the predecessor `zeus_durable_architecture_spec.md`. Their content is **not repeated here** — that file remains the reference for their details. This FINAL spec treats them as substrate.

P9–P11 are specified below in full, because they are the remaining work that decides whether Zeus ever trades live.

---

# PART I — Installed foundation (P0–P8)

**This part is intentionally short.** The full content of P0–P8 lives in `zeus_durable_architecture_spec.md`. This FINAL spec quotes only the invariants (above) and the headline decisions.

## P0 — Bearing-capacity prerequisites
- P0.1 Execution truth semantics before ledger work: `EXIT_INTENT` as lifecycle event, not local close.
- P0.2 Attribution grammar frozen: 4 strategy keys (`settlement_capture`, `shoulder_sell`, `center_buy`, `opening_inertia`), no fallback bucket.
- P0.3 Canonical transaction boundary: event + projection in one SQLite txn.
- P0.4 Data availability as explicit truth (`availability_fact`).
- P0.5 Implementation operating system (packetized discipline).

## P1 — Canonical lifecycle authority
- `position_events` (append-only) + `position_current` (projection).
- `append_event_and_project()` API; single transaction boundary.
- JSON surfaces (`positions.json`, `status_summary.json`) become derived, not authoritative.

## P2 — Execution truth and exit lifecycle
- Entry intent + exit intent are separate lifecycle events.
- Order model with `order_role ∈ {entry, exit}`.
- Paper/live parity via `env` field, not separate code paths.
- **Current gap**: `execution_fact` table is empty (0 rows); legacy content lives in `trade_decisions`. This is a migration gap inside P7.

## P3 — Strategy-aware protective spine
- Risk policies keyed on `strategy_key`.
- Control commands must alter evaluator/sizing/execution outcome (INV-05).
- `center_buy` gate blocks only that strategy; others remain alive.

## P4 — Learning spine and data availability truth
- `opportunity_fact`, `outcome_fact`, `availability_fact`, `execution_fact` fact layers.
- Point-in-time snapshots preserve decision-time truth (INV-06).
- **Current gap**: `outcome_fact` empty, `execution_fact` empty. P&L currently recoverable via `trade_decisions.settlement_edge_usd` + `positions-paper.json`.

## P5 — Lifecycle phase engine
- Finite phase vocabulary; transitions enumerated.
- Quarantine rule, Day0 rule codified.

## P6 — Operator, control, observability compression
- One operator surface (`status_summary.json`) derived from canonical authority.
- Control plane commands land in canonical store.

## P7 — Migration plan
- R1–R7 complete (autonomous stop at R7).
- **M4 destructive retirement deferred behind P9–P11 reality-alignment gates.**

## P8 — Human + LLM coding operating system
- Work packets are the only coding unit.
- 8 hard rules for LLM coding (§P8.5 of predecessor spec).
- Anti-vibe checklist (§P8.6). **Being extended in P9 with item #9, see below.**
- Evidence bundle mandatory.

---

# PART II — Final extensions (P9–P11)

## P9 — Epistemic contract and provenance enforcement

### P9.1 Decision
Install a cross-layer semantic contract that prevents probability/edge/sizing floats from crossing module boundaries without declared provenance, optimization target, and cost treatment.

### P9.2 Why (evidence from 2026-04-05 math lane audit)

The signal layer's 97.8% high-confidence hit rate does not compose into profit because each cross-layer handoff loses the semantic that makes the upstream number meaningful. Six specific design gaps were identified (D1–D6 below). A third-party Gemini audit (2026-04-05) concluded that D1–D6 are symptoms of **K0: epistemic fragmentation** — three islands of math (signal pursuing "truth/Brier", strategy doing "defensive scaling" with 17 magic numbers, execution dealing with "physical reality" of fees/slippage) that cannot compose because no contract types cross them.

Evidence base:
- `src/strategy/market_fusion.py`: 11 α-adjustment hardcoded values, optimization target is Brier score.
- `src/strategy/kelly.py`: 6 multiplicative hardcoded values in dynamic multiplier cascade; cascade product can reach 0.00094 without tests bounding it.
- `src/strategy/market_analysis.py:141`: `entry_price = p_market[i]` (implied probability, not VWMP+fee).
- `src/execution/exit_triggers.py`: exit uses 2-cycle confirmation; entry uses bootstrap n=200 + BH-FDR α=0.10. Asymmetric epistemic standards.
- Only 4 of 17 hardcoded strategy constants are registered via `HARDCODED()` provenance decorator.

Gemini verdict: *"每一层都在用自己的硬编码（那 17 个魔法数）试图修补上一层传来的'不确定性'，但由于没有 Provenance 记录，这些修补在乘法级联下产生了非线性的毁灭性后果。"*

### P9.3 Six design gaps (D1–D6) — to be resolved by P9 contracts, not by patching

#### D1 — Alpha blending optimization target misalignment (CRITICAL)
`market_fusion.compute_alpha()` adjusts α via spread/lead/freshness/model_agreement terms. All adjustments were validated against Brier score. Brier measures calibration; profit requires EV > cost. When the market is already calibrated, Brier-optimization converges Zeus to the market, and edge → 0.

**P9 resolution:** α outputs become `AlphaDecision(value, optimization_target, evidence_basis, ci_bound)`. Downstream consumers must declare their target. Brier-optimized α fed into EV-seeking sizing raises a runtime contract violation.

#### D2 — TAIL_ALPHA_SCALE=0.5 breaks buy_no primary edge source (CRITICAL)
`market_fusion.compute_posterior()` scales α to 0.5 for tail bins. `exit_triggers` documents buy_no base win rate ~87.5%, which comes from market tail overpricing (lottery effect). Scaling α toward market on tails **directly halves the edge buy_no depends on**.

**P9 resolution:** Tail scaling becomes a typed `TailTreatment` decision that must declare whether it serves calibration accuracy OR profit — not both. If profit, it must be validated against buy_no P&L, not Brier.

#### D3 — Entry price uses implied probability, not VWMP+fee (SEVERE)
`BinEdge.entry_price = p_market[i]`, which is implied probability. Kelly computes `f* = (p_posterior - entry_price) / (1 - entry_price)` using this. But Polymarket execution price = ask + taker fee (5%) + slippage. Kelly systematically oversizes.

**P9 resolution:** `entry_price` at the Kelly boundary must be typed `ExecutionPrice(vwmp_or_ask, fee_deducted=True/False, currency)`. Bare floats at this seam are INV-12 violations.

#### D4 — Entry-exit epistemic asymmetry (CRITICAL; already a live blocker)
| Dimension | Entry | Exit |
|---|---|---|
| Multiple-testing correction | BH FDR α=0.10 | None |
| Evidence standard | bootstrap p-value | single forward_edge |
| Confirmation | CI_lower > 0 + FDR | 2 consecutive negative cycles |
| Sample width | n_bootstrap=200+ | 2-cycle observation |

System admits edges cautiously, exits aggressively. True edges get killed by noise before maturation. Rainstorm's 7/8 buy_no false-EDGE_REVERSAL death matches this pattern.

**P9 resolution:** Entry and exit decisions share the same `DecisionEvidence` contract. Exit evidence must meet symmetric statistical burden (bootstrap CI or FDR-adjusted consecutive tests).

#### D5 — Vig smearing distorts edge signal (MEDIUM)
`p_market` includes vig (~0.95–1.05). `compute_posterior` blends then normalizes, smearing vig bias across all bins.

**P9 resolution:** Vig normalization (`p_market_clean = p_market / vig`) must happen before blend, under a declared `VigTreatment` contract.

#### D6 — EV hold calculation ignores funding/correlation cost (MEDIUM)
`exit_triggers` EV gate: `net_hold = shares × p_posterior`. Assumes free carry to settlement. Ignores opportunity cost of locked bankroll + correlation crowding of other positions.

**P9 resolution:** `HoldValue` contract must declare what costs are included; default minimum is fee + time-cost-to-settlement.

### P9.4 Core P9 artifacts

```
src/contracts/
  epistemic_context.py      [exists, to be extended]
  edge_context.py           [exists, to be extended]
  execution_intent.py       [exists]
  provenance_registry.py    [NEW — INV-13 enforcement]
  alpha_decision.py         [NEW — D1]
  tail_treatment.py         [NEW — D2]
  execution_price.py        [NEW — D3]
  decision_evidence.py      [NEW — D4]
  vig_treatment.py          [NEW — D5]
  hold_value.py             [NEW — D6]

tests/
  test_provenance_enforcement.py  [NEW — INV-13 tests]
  test_kelly_cascade_bounds.py    [NEW — multiplicative product bound]
  test_entry_exit_symmetry.py     [NEW — D4 symmetry]
  test_alpha_target_coherence.py  [NEW — D1 target mismatch detection]
```

### P9.5 Provenance registry contract (INV-13 machinery)

```python
@dataclass(frozen=True)
class ProvenanceRecord:
    constant_name: str
    file_location: str
    declared_target: str    # "brier_score" | "ev" | "risk_cap" | "physical_constraint"
    data_basis: str         # description of how value was chosen
    validated_at: str       # ISO date
    replacement_criteria: str  # what evidence triggers re-fit
    cascade_bound: tuple[float, float] | None  # if part of multiplicative cascade

REGISTRY: dict[str, ProvenanceRecord] = load_from_yaml("config/provenance_registry.yaml")
```

Runtime rule: a constant not in REGISTRY that appears in a cascade path flagged as `requires_provenance=True` raises `UnregisteredConstantError` unless emergency-bypass flag is set. Emergency bypasses are logged and auto-expire after 7 days.

### P9.6 Anti-vibe checklist extension (P8.6 addendum)
Add item #9 to P8.6 checklist:
> **9. Did this patch introduce any unregistered numeric constant that composes with other constants in a sizing/edge/probability cascade?**
> If yes, the patch must include a `ProvenanceRecord` in `config/provenance_registry.yaml` and a cascade-bound test.

### P9.7 Tests
- `test_no_bare_float_at_kelly_boundary`: static analysis asserts Kelly receives typed `ExecutionPrice`, not `float`.
- `test_all_alpha_adjustments_declare_target`: every call to `compute_alpha()` path has a declared target.
- `test_kelly_cascade_product_bounded`: worst-case product of all multiplicative adjustments stays in `[0.001, 1.0]`.
- `test_entry_exit_evidence_symmetric`: entry and exit use same statistical burden.
- `test_tail_treatment_declared`: any α scaling on tails has a declared optimization target.

### P9.8 P9 exit condition
P9 closes when: all 6 contract types exist, all 17 strategy-layer hardcoded constants are either registered in `provenance_registry.yaml` or removed, all P9.7 tests pass, and a trace-level audit shows no bare-float cross-layer handoffs in the sizing pipeline.

---

## P10 — External reality contract layer

### P10.1 Decision
Install a typed contract layer that makes every external-reality assumption (market structure, protocol timing, settlement source, fee schedule, tick size, resolution rules) explicit, TTL-bound, verifiable, and runtime-actuated.

### P10.2 Why
The 2026-04-03 reality audit identified 17 external-assumption gaps. The pattern is:
> System runs on implicit assumption → external world changes → system does not notice → silent drift → P&L loss not attributable to any bug.

Fixing each of the 17 gaps individually is whack-a-mole. P10 builds the sensing layer that detects future drift of this class.

### P10.3 RealityContract data structure

```python
@dataclass(frozen=True)
class RealityContract:
    contract_id: str
    category: str              # "economic" | "execution" | "data" | "protocol"
    assumption: str            # human-readable
    current_value: Any
    verification_method: str
    last_verified: datetime
    ttl_seconds: int
    criticality: str           # "blocking" | "degraded" | "advisory"
    on_change_handlers: list[str]

    @property
    def is_stale(self) -> bool:
        return (datetime.utcnow() - self.last_verified).total_seconds() > self.ttl_seconds

    @property
    def must_reverify(self) -> bool:
        return self.is_stale and self.criticality == "blocking"
```

### P10.4 Four contract families

- **Economic** (`config/reality_contracts/economic.yaml`): taker fee, maker rebate, round-trip cost.
- **Execution** (`config/reality_contracts/execution.yaml`): tick size, min order size, price bounds.
- **Data** (`config/reality_contracts/data.yaml`): settlement source per market, Gamma vs CLOB consistency.
- **Protocol** (`config/reality_contracts/protocol.yaml`): resolution timeline, WebSocket necessity, rate-limit behavior.

### P10.5 Verifier semantics

```python
class RealityContractVerifier:
    def verify_all_blocking(self) -> VerificationResult:
        """Called before any trading decision."""
        failures = [c for c in self.contracts if c.criticality == "blocking" and not self._verify(c)]
        return VerificationResult(can_trade=(not failures), failures=failures)

    def detect_drift(self) -> list[DriftEvent]:
        """Called by Venus heartbeat."""
        ...

    def generate_antibody(self, drift: DriftEvent) -> Antibody:
        """Drift -> test/contract/code change that makes that class permanently detected."""
        ...
```

### P10.6 P-FEE-GUARD (immediate temporary patch before full P10)
Because fee is the single highest-impact external assumption and P10 will take time to build, an immediate temporary patch:

```python
class FeeGuard:
    ASSUMED_TAKER_FEE = 0.05
    ASSUMED_ROUND_TRIP = 0.10

    @classmethod
    def adjust_edge(cls, gross_edge: float) -> float:
        return gross_edge - cls.ASSUMED_TAKER_FEE

    @classmethod
    def min_gross_edge_for_trade(cls) -> float:
        return cls.ASSUMED_TAKER_FEE + 0.02  # 7% floor
```

This is an explicit, temporary, documented patch. It must be replaced by `RealityContract` entries in `config/reality_contracts/economic.yaml` within the P10 window. No other temporary patches in the reality category are allowed.

### P10.7 17 gaps — to contract mapping

| Gap | Category | Contract ID | Criticality |
|---|---|---|---|
| Taker fee 5% missing | economic | `FEE_RATE_WEATHER` | blocking |
| Maker rebate | economic | `MAKER_REBATE_RATE` | degraded |
| Tick size dynamic | execution | `TICK_SIZE_STANDARD` | blocking |
| Min order size | execution | `MIN_ORDER_SIZE_SHARES` | blocking |
| Settlement source per city | data | `SETTLEMENT_SOURCE_{CITY}` | blocking |
| Gamma vs CLOB | data | `GAMMA_CLOB_PRICE_CONSISTENCY` | advisory |
| WebSocket vs REST | protocol | `WEBSOCKET_REQUIRED` | degraded |
| API rate limiting | protocol | `RATE_LIMIT_BEHAVIOR` | degraded |
| UMA resolution timeline | protocol | `RESOLUTION_TIMELINE` | advisory |
| NOAA time scale | data | `NOAA_TIME_SCALE` | blocking |
| Contract rules drift | data | `MARKET_CONTRACT_RULES` | blocking |
| Order depth ≠ display | execution | `ORDER_DEPTH_REAL` | degraded |
| (remaining 5 gaps listed in §Appendix) |

### P10.8 Tests
- `test_all_blocking_contracts_verified_before_trade`.
- `test_fee_included_in_edge_calculation`.
- `test_tick_size_enforced_on_order_rounding`.
- `test_drift_detection_generates_antibody`.

### P10.9 P10 exit condition
All blocking contracts exist with verification methods. `P-FEE-GUARD` replaced by economic contracts. Drift detection generates antibodies (tests or contract updates), not just alerts. Runtime behavior changes when blocking contracts fail.

---

## P11 — External audit boundary and retirement readiness

### P11.1 Decision
Expose typed, derived status for Venus consumption. Keep Zeus as sole authority writer. Preserve three-layer consciousness model (RiskGuard reflex / Zeus runtime / Venus reasoning).

### P11.2 Boundary law
From `zeus_autonomous_delivery_constitution.md`:
> Venus may read derived status and consume typed contracts. Venus may NOT write DB truth, become repo authority, or land code directly into `src/` without P8 work-packet discipline.

### P11.3 Venus-consumable surfaces
```
AUTHORITATIVE_INPUTS_FOR_VENUS = [
    "zeus/state/status_summary-{mode}.json",      # derived
    "zeus/state/positions-{mode}.json",           # derived
    "zeus/state/strategy_tracker-{mode}.json",    # derived
    "zeus/state/risk_state-{mode}.db",
    "zeus/state/zeus.db",                          # read-only
    "zeus/state/control_plane-{mode}.json",        # Zeus honors
    "zeus/state/assumptions.json",                 # RCL surface
    "zeus/config/reality_contracts/*.yaml",        # RCL config
]
```

### P11.4 Audit lane orchestration

| Lane | Frequency | Cost | Purpose |
|---|---|---|---|
| **Heartbeat** | 30 min | low | Fast safety: healthcheck, freshness, contract verification |
| **Daily audit** | 6am UTC | medium | Trade review, drift detection, attribution consistency |
| **Weekly audit** | Mon 6am UTC | high | Edge realization, settlement quality, full contract audit |

### P11.5 Heartbeat question (the only question)
> *Is Zeus currently safe, fresh, and reality-aligned enough to keep operating without intervention?*

If no → Venus posts to control_plane with recommended action. Zeus honors or flags.

### P11.6 Antibody generation pipeline
1. Venus detects drift → `DriftEvent`
2. Drift classified: `critical` → code change, `moderate` → config change, `low` → documentation
3. Antibody = packet draft for P8 implementation
4. Antibody goes into `known_gaps.md` + work packet queue
5. Human approval gate before implementation

### P11.7 Retirement readiness
P7 M4 (destructive delete of legacy surfaces) is gated behind:
- P10 reality contracts running ≥ 7 days with no blocking failures
- P9 provenance registry complete
- At least one antibody successfully converted from detection → test → code → permanent immunity
- Paper P&L showing stabilization (trend over 2 weeks, not a single point)

---

# PART III — §Endgame clause

## §E1 Binary gate

After P9 and P10 land (P11 can land concurrently with P10), Zeus enters **1-week live**.

### Entry conditions
- P9 closed: all D1–D6 contracts exist, INV-12/13 tests pass, 17 hardcoded constants registered.
- P10 closed: all blocking reality contracts exist and verified, P-FEE-GUARD replaced.
- Paper P&L for 7 trailing days logged and archived.
- Live bankroll capped at the smallest amount the author can psychologically ignore (declared in `state/endgame_bankroll.txt` before start).

### During the week
- **Zeus runs live, unattended, for 7 calendar days.**
- Author does not intervene unless trading stalls (zero decisions for > 24h).
- Author does not modify code, config, or spec during the week.
- Venus heartbeat runs; Venus may post to `control_plane.json`, but **author does not act on Venus output during the week** except to kill-switch on RED status.
- P&L is observed at end of week only. Intraday observation allowed but not actionable.

### Exit conditions (decided at end of week 7)

```
IF  live_pnl > 0         (strictly positive, even +$0.01)
THEN Zeus continues. Author may resume work.

IF  live_pnl < 0         (strictly negative, even -$0.01)
THEN Zeus is retired. Daemons stopped. Author does NOT open a new packet.

IF  live_pnl == 0        (exactly zero, no trades filled, or exact breakeven)
THEN extend 7 more days, same rules. Maximum one extension.
     If still zero after extension, treat as negative outcome.
```

### §E2 Post-gate rules

**If Zeus continues (P&L > 0):**
- The 6 contracts from P9 and the reality contracts from P10 are the protected spine. No new architecture layers. Future work is per-packet improvements only, no new P layers.
- Live continues under the same hands-off rule: no intervention during trading windows except kill-switch.
- Monthly review of P&L and contract drift; not spec review.

**If Zeus retires (P&L ≤ 0):**
- `git tag zeus-final-{date}` the repo state at retirement.
- Write `POSTMORTEM.md` — max 1000 words, answer:
  - What did the signal layer actually predict correctly that the market didn't?
  - Where did edge leak: sizing, execution, exit, or vig?
  - What would have to be true for this class of system to work?
- Extract methodology artifacts (epistemic_scaffold, high-dimensional thinking methodology, structural decisions framework, provenance registry pattern) into a separate standalone project.
- Archive Zeus codebase read-only. Do not delete.
- **Do not open a new trading system project for 3 calendar months.**

### §E3 Why this clause exists

This clause exists because the author has, over 6 weeks, built architecture faster than he has run experiments. The architecture is good. The architecture is finished. This clause forces the architecture to face reality instead of recurring into new meta-layers.

The author's pattern has been: problem appears → write higher-level spec → brief relief → architecture lands → new blind spot → deeper spec. This clause breaks the loop by binding the outcome to a single binary at a known date.

$0.1 is the threshold because:
- Below it, any reasonable fee/slippage model consumes any apparent edge.
- It is small enough to be an unambiguous signal, large enough to exceed random-walk noise over 7 days of active trading.
- It makes the decision uninterpretable: no amount of "but if we fix X" narrative can move the outcome across zero.

The 3-month no-new-system clause exists because immediately starting a replacement would reconstitute the same pattern. 3 months is long enough for the compulsion to weaken and for the author to notice whether building trading systems was the goal or the symptom.

---

# Appendix — Preserved supporting content

## A. Venus operator architecture (from `venus_operator_architecture.md`)

Three-layer consciousness model (to be implemented as external supporting runtime, not Zeus-internal):

```
Layer 3: VENUS (Reasoning — slow, adaptive, catches unknown unknowns)
   reads Zeus state files, spawns Claude Code via ACP,
   maintains world model, reports to Discord, persists findings in memory.

Layer 2: ZEUS DAEMON (Execution — mechanical, deterministic, fast)
   runs trading cycles, exposes state via JSON + DB,
   honors control_plane, logs everything to chronicle.

Layer 1: RISKGUARD (Reflex — fast, threshold-based, fail-closed)
   60-second tick, Brier/drawdown/loss thresholds,
   halts on RED, Discord alerts with cooldown.
```

Venus integration uses existing infrastructure (agent identity, Discord presence, cron engine, ACP, native filesystem access, memory markdown). No net-new infra required.

## B. Authoritative read order (for any agent entering the repo)

1. This file (`zeus_FINAL_spec.md`)
2. `docs/architecture/zeus_durable_architecture_spec.md` (P0–P8 details)
3. `architecture/invariants.yaml` (machine-checkable law)
4. `architecture/self_check/authority_index.md`
5. Packet-specific `work_packet.yaml`
6. Code

## C. Methodology attribution

This spec draws on:
- Fitz's **High-Dimensional Thinking** methodology (CLAUDE.md): 10 bugs = 1 design failure; fix the category, not the instance.
- Fitz's **Four Constraints of Delegated Intelligence**: structural decisions > patches; translation loss as thermodynamic limit; immune system > security guard; data provenance > code correctness.
- Fitz's **Epistemic Scaffold** (`~/epistemic_scaffold/`): provenance interrogation, cross-module relationships, external authority wrapping.
- Gemini 2026-04-03 external math review.
- Gemini 2026-04-05 provenance audit concluding K0 epistemic fragmentation.

## D. What is NOT in this spec (intentional)

- Specific fee rates, tick sizes, or market rules → those live in `config/reality_contracts/*.yaml`, not in spec.
- New signal math → signal layer is frozen after 2026-04-04 math lane closure.
- Cron scheduler design → lives in Venus, not Zeus.
- New strategy types → strategy_key grammar is frozen.
- Backtest system → out of scope.

---

# Coda

> Your design philosophy already predicted this:
> *"这个文档本身也会在翻译中损失。但 Bin.unit、for_city()、test_celsius_cities_get_celsius_semantics() 不会。"*
>
> So the value of this document is not that it be read. The value is that it be encoded as:
> 1. `src/contracts/*.py` — typed contracts
> 2. `config/reality_contracts/*.yaml` — explicit assumptions
> 3. `config/provenance_registry.yaml` — registered constants
> 4. `tests/test_reality_contracts.py`, `tests/test_provenance_enforcement.py` — verification coverage
> 5. `architecture/invariants.yaml` INV-11/12/13 — machine-checkable law
>
> Once those exist, this document can disappear. The system will continue to run, or it will not. The endgame clause will decide.
>
> **This is FINAL. No v2.**
