# Zeus Target State

Version: 2026-04-05 (trimmed 2026-04-10)
Status: Historical target-state authority. Preserve as design law for intended end-state semantics, not as current runtime/readiness truth.
Original: `docs/archives/specs/zeus_FINAL_spec_original.md` (627 lines)

> **Reading note**: This file is a trimmed historical design surface. Use it for end-state semantics, invariant intent, and the original endgame hypothesis. **Do not infer current runtime status, live-readiness, active blockers, or current priorities from this file.** Current truth lives in `docs/operations/current_state.md` and `docs/known_gaps.md`.

---

## §0 Historical executive frame

Zeus is a durable, position-governed weather-arbitrage trading runtime on Polymarket.

Historical snapshot as of 2026-04-05:
- 112 math tests passing, 97.8% high-confidence hit rate in signal layer.
- 104 paper trades executed, 33 closed positions.
- Paper P&L: **-$6.82**. Never taken to live.
- P0–P8 foundation installed; P7R7 was the then-current autonomous stop boundary.
- Two failure classes were identified as the then-visible live blockers: (a) epistemic fragmentation across signal→strategy→execution (P9), (b) implicit external-reality assumption drift (P10).

This section captures the historical endgame hypothesis at that time. It is not a present-tense statement of what Zeus will do next.

---

## §3 New invariants (INV-11..13)

INV-01..10 are defined in `docs/authority/zeus_architecture.md`. The following are additions from this FINAL spec:

### INV-11. External assumptions are explicit and verified.
Every assumption about external market reality (fees, tick size, settlement source, protocol timing, resolution rules) must be a typed `RealityContract` with TTL, verification method, and criticality. Silent assumption drift is forbidden. Hardcoded numeric values mirroring an external fact are INV-11 violations unless wrapped in a contract.

### INV-12. Cross-layer probability/edge carries provenance.
Floats crossing signal→strategy, strategy→execution, or execution→settlement boundaries must carry a typed wrapper declaring: optimization target (accuracy vs EV), source (model vs market vs blended), confidence bound, and whether external costs (fee/slippage/vig) have been deducted. A bare `float` at a cross-layer seam is an INV-12 violation.

### INV-13. Unregistered numeric constants in Kelly-class multiplicative cascades are forbidden.
Any adjustment to α, Kelly multiplier, edge threshold, or sizing fraction must be registered in a `ProvenanceRegistry` with: declared optimization target, data basis, replacement criteria. An unregistered constant in a cascade product is neutralized (weight=1.0) until registered.

---

## §4 Historical priority structure

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

P0–P8 details live in `docs/authority/zeus_architecture.md`.

---

# PART I — Historical foundation summary (P0–P8)

- **P0**: Exit intent as lifecycle event; 4 strategy keys frozen; single SQLite transaction boundary; data availability as explicit truth; packetized discipline.
- **P1**: `position_events` + `position_current`; `append_event_and_project()` API; JSON surfaces become derived.
- **P2**: Entry/exit as separate lifecycle events; order model with `order_role ∈ {entry, exit}`; paper/live parity. **Gap**: `execution_fact` table empty.
- **P3**: Risk policies keyed on `strategy_key`; control commands must alter outcomes (INV-05).
- **P4**: Four fact layers (`opportunity_fact`, `outcome_fact`, `availability_fact`, `execution_fact`); point-in-time snapshots. **Gap**: `outcome_fact` and `execution_fact` empty.
- **P5**: Finite phase vocabulary; transitions enumerated; quarantine and day0 rules.
- **P6**: One derived operator surface; control plane commands in canonical store.
- **P7**: R1–R7 complete. M4 destructive retirement deferred behind P9–P11.
- **P8**: Work packets as only coding unit; 8 hard rules; anti-vibe checklist; evidence bundle mandatory.

---

# PART II — Historical final extensions (P9–P11)

## P9 — Epistemic contract and provenance enforcement

### P9.1 Decision
Install cross-layer semantic contracts preventing probability/edge/sizing floats from crossing module boundaries without declared provenance, optimization target, and cost treatment.

### P9.2 Why (2026-04-05 math lane audit)
The signal layer's 97.8% hit rate does not compose into profit because each cross-layer handoff loses the semantic that makes the upstream number meaningful. Six design gaps (D1–D6) identified. Gemini audit concluded: **K0 epistemic fragmentation** — three math islands (signal/Brier, strategy/defensive scaling with 17 magic numbers, execution/physical reality) that cannot compose because no contract types cross them.

### P9.3 Six design gaps (D1–D6)

**D1 — Alpha blending optimization target misalignment (CRITICAL)**
`market_fusion.compute_alpha()` adjusts α via spread/lead/freshness/model_agreement. All validated against Brier score, but profit requires EV > cost. Brier-optimization converges Zeus to market, edge → 0.
→ α outputs become `AlphaDecision(value, optimization_target, evidence_basis, ci_bound)`.

**D2 — TAIL_ALPHA_SCALE=0.5 breaks buy_no primary edge source (CRITICAL)**
Scaling α toward market on tails directly halves the edge buy_no depends on (lottery effect overpricing).
→ `TailTreatment` must declare whether it serves calibration OR profit.

**D3 — Entry price uses implied probability, not VWMP+fee (SEVERE)**
`BinEdge.entry_price = p_market[i]` but execution price = ask + taker fee (5%) + slippage. Kelly systematically oversizes.
→ `ExecutionPrice(vwmp_or_ask, fee_deducted=True/False, currency)` at Kelly boundary.

**D4 — Entry-exit epistemic asymmetry (CRITICAL)**
Entry: BH FDR α=0.10 + bootstrap n=200 + CI_lower > 0. Exit: 2-cycle confirmation only. System admits edges cautiously, exits aggressively. True edges killed by noise before maturation.
→ Entry and exit share same `DecisionEvidence` contract with symmetric statistical burden.

**D5 — Vig smearing distorts edge signal (MEDIUM)**
`p_market` includes vig (~0.95–1.05). Blend then normalize smears vig bias.
→ Vig normalization before blend under declared `VigTreatment` contract.

**D6 — EV hold calculation ignores funding/correlation cost (MEDIUM)**
`net_hold = shares × p_posterior` assumes free carry. Ignores opportunity cost + correlation crowding.
→ `HoldValue` contract declaring included costs; minimum = fee + time-cost-to-settlement.

### P9.4 Core artifacts
```
src/contracts/
  provenance_registry.py    [NEW — INV-13]
  alpha_decision.py         [NEW — D1]
  tail_treatment.py         [NEW — D2]
  execution_price.py        [NEW — D3]
  decision_evidence.py      [NEW — D4]
  vig_treatment.py          [NEW — D5]
  hold_value.py             [NEW — D6]

tests/
  test_provenance_enforcement.py
  test_kelly_cascade_bounds.py
  test_entry_exit_symmetry.py
  test_alpha_target_coherence.py
```

### P9.5 Provenance registry (INV-13 machinery)
```python
@dataclass(frozen=True)
class ProvenanceRecord:
    constant_name: str
    file_location: str
    declared_target: str    # "brier_score" | "ev" | "risk_cap" | "physical_constraint"
    data_basis: str
    validated_at: str
    replacement_criteria: str
    cascade_bound: tuple[float, float] | None

REGISTRY: dict[str, ProvenanceRecord] = load_from_yaml("config/provenance_registry.yaml")
```

Runtime: unregistered constant in `requires_provenance=True` cascade → `UnregisteredConstantError`. Emergency bypass auto-expires in 7 days.

### P9.6 Anti-vibe checklist addendum
Item #9: *Did this patch introduce any unregistered numeric constant that composes with other constants in a sizing/edge/probability cascade?*

### P9.7 Tests
- `test_no_bare_float_at_kelly_boundary`
- `test_all_alpha_adjustments_declare_target`
- `test_kelly_cascade_product_bounded`: worst-case product in `[0.001, 1.0]`
- `test_entry_exit_evidence_symmetric`
- `test_tail_treatment_declared`

### P9.8 Exit condition
All 6 contract types exist, all 17 hardcoded constants registered or removed, all P9.7 tests pass, trace-level audit shows no bare-float cross-layer handoffs in sizing pipeline.

---

## P10 — External reality contract layer

### P10.1 Decision
Install typed contracts making every external-reality assumption (market structure, protocol timing, settlement source, fee schedule, tick size, resolution rules) explicit, TTL-bound, verifiable, and runtime-actuated.

### P10.2 Why
17 external-assumption gaps identified. Pattern: system runs on implicit assumption → world changes → system doesn't notice → silent drift → P&L loss not attributable to any bug. P10 builds the sensing layer for future drift.

### P10.3 RealityContract
```python
@dataclass(frozen=True)
class RealityContract:
    contract_id: str
    category: str              # "economic" | "execution" | "data" | "protocol"
    assumption: str
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
- **Protocol** (`config/reality_contracts/protocol.yaml`): resolution timeline, WebSocket, rate-limit behavior.

### P10.5 Verifier semantics
```python
class RealityContractVerifier:
    def verify_all_blocking(self) -> VerificationResult: ...
    def detect_drift(self) -> list[DriftEvent]: ...
    def generate_antibody(self, drift: DriftEvent) -> Antibody: ...
```

### P10.6 P-FEE-GUARD (temporary patch)
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

Must be replaced by `RealityContract` entries within P10 window.

### P10.7 Gap-to-contract mapping

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

### P10.8 Tests
- `test_all_blocking_contracts_verified_before_trade`
- `test_fee_included_in_edge_calculation`
- `test_tick_size_enforced_on_order_rounding`
- `test_drift_detection_generates_antibody`

### P10.9 Exit condition
All blocking contracts exist with verification methods. P-FEE-GUARD replaced. Drift detection generates antibodies, not just alerts. Runtime behavior changes when blocking contracts fail.

---

## P11 — External audit boundary and retirement readiness

### P11.1 Decision
Expose typed, derived status for Venus consumption. Zeus remains sole authority writer. Three-layer consciousness: RiskGuard reflex / Zeus runtime / Venus reasoning.

### P11.2 Boundary law
Venus may read derived status and consume typed contracts. Venus may NOT write DB truth, become repo authority, or land code without P8 work-packet discipline.

### P11.3 Venus-consumable surfaces
```
AUTHORITATIVE_INPUTS_FOR_VENUS = [
    "zeus/state/status_summary-{mode}.json",
    "zeus/state/positions-{mode}.json",
    "zeus/state/strategy_tracker-{mode}.json",
    "zeus/state/risk_state-{mode}.db",
    "zeus/state/zeus.db",
    "zeus/state/control_plane-{mode}.json",
    "zeus/state/assumptions.json",
    "zeus/config/reality_contracts/*.yaml",
]
```

### P11.4 Audit lanes

| Lane | Frequency | Purpose |
|---|---|---|
| Heartbeat | 30 min | Fast safety: healthcheck, freshness, contract verification |
| Daily audit | 6am UTC | Trade review, drift detection, attribution consistency |
| Weekly audit | Mon 6am | Edge realization, settlement quality, full contract audit |

### P11.5 Heartbeat question
> *Is Zeus currently safe, fresh, and reality-aligned enough to keep operating without intervention?*

### P11.6 Antibody pipeline
Drift → classify (critical/moderate/low) → antibody (code/config/doc change) → `known_gaps.md` + work packet → human approval gate.

### P11.7 Retirement readiness (gates M4)
- P10 reality contracts running ≥ 7 days, no blocking failures
- P9 provenance registry complete
- At least one antibody: detection → test → code → permanent immunity
- Paper P&L stabilization (2-week trend)

---

# PART III — Historical §Endgame clause

## §E1 Binary gate

After P9 and P10 land, Zeus enters **1-week live**.

### Entry conditions
- P9 closed: all D1–D6 contracts exist, INV-12/13 tests pass, 17 constants registered.
- P10 closed: all blocking reality contracts verified, P-FEE-GUARD replaced.
- Paper P&L for 7 trailing days logged.
- Live bankroll capped at smallest amount author can psychologically ignore (declared in `state/endgame_bankroll.txt`).

### During the week
- Zeus runs live, unattended, 7 calendar days.
- Author does not intervene unless trading stalls (zero decisions >24h).
- Author does not modify code, config, or spec.
- Venus heartbeat runs; author acts only on RED kill-switch.
- P&L observed at end of week only.

### Exit conditions (end of week 7)

```
IF  live_pnl > 0    → Zeus continues. Author may resume work.
IF  live_pnl < 0    → Zeus is retired. Daemons stopped. No new packet.
IF  live_pnl == 0   → Extend 7 more days, same rules. Max one extension.
                      If still zero → treat as negative.
```

## §E2 Post-gate rules

**If Zeus continues (P&L > 0):**
No new architecture layers. Future work is per-packet improvements only. Live continues hands-off except kill-switch. Monthly P&L + contract drift review.

**If Zeus retires (P&L ≤ 0):**
- `git tag zeus-final-{date}`
- Write `POSTMORTEM.md` (max 1000 words): what did signal predict correctly? Where did edge leak? What would have to be true?
- Extract methodology artifacts into standalone project.
- Archive codebase read-only.
- **No new trading system project for 3 calendar months.**

## §E3 Why this clause exists

The author has built architecture faster than he has run experiments. This clause forces the architecture to face reality instead of recurring into new meta-layers. $0.1 is the threshold because below it, any reasonable fee/slippage model consumes any apparent edge. The 3-month no-new-system clause exists because immediately starting a replacement would reconstitute the same pattern.

---

# Coda

> *"这个文档本身也会在翻译中损失。但 Bin.unit、for_city()、test_celsius_cities_get_celsius_semantics() 不会。"*
>
> The value of this document is that it be encoded as:
> 1. `src/contracts/*.py` — typed contracts
> 2. `config/reality_contracts/*.yaml` — explicit assumptions
> 3. `config/provenance_registry.yaml` — registered constants
> 4. `tests/test_reality_contracts.py`, `tests/test_provenance_enforcement.py` — verification coverage
> 5. `architecture/invariants.yaml` INV-11/12/13 — machine-checkable law
>
> Once those exist, this document can disappear. The endgame clause will decide.
>
> This document is intentionally preserved as the last explicit endgame design. It is not a substitute for current-state evidence.
