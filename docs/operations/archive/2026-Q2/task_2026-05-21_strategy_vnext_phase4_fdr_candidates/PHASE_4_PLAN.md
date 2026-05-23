# Phase 4 Plan — FDR `spread_bucket` Extension + 6 Candidate Stubs Production (v2)

**Created:** 2026-05-21 · **Revised:** 2026-05-21 (plan-critic v2)
**Authority:** `05_PHASE_4_FDR_FAMILY_CANDIDATES.md` (incl. §"FDR + spread_bucket (math basis)") · v4 §M lines 1100-1101 · dossier §2.5/§12/§13.2.

## Context

SCHEMA_VERSION = 15 (`origin/main:src/state/db.py:852`); no bump in Phase 4. Six stubs at 11 LOC each (`__init__`-only). `selection_family.py` already carries `snap=`/`src=`/`rgm=` prefixes (Phase 3 T1). `decision_events.strategy_key TEXT NOT NULL` exists (`db.py:1318` block); `no_trade_events` carries `reason` (NoTradeReason enum CHECK) but NO strategy attribution column — relationship tests use `reason` match. `WeatherRegimeTag` is a 6-member StrEnum with `UNKNOWN` as fail-open value; `regime_tag_for()` returns `WeatherRegimeTag.UNKNOWN`, never `None`. `strategy_profile.py:239` declares `_REQUIRED_FIELDS`; unrecognized fields raise — `executable_alpha` belongs to `CandidateMetadata`, NOT to registry YAML.

## Guardrails

**Must Have:** spread_bucket appended with prefix `sb=`; all 6 candidates `live_status: shadow`; relationship test per candidate; NoTradeReason additions enumerated below; `CandidateMetadata.executable_alpha = True` on each candidate (metadata-only, NOT registry field).

**Must NOT:** promote any candidate to `live`; bump SCHEMA_VERSION; add `shadow_experiment_cohort` table; pass `executable_alpha` to registry YAML (raises RegistrySchemaError); hardcode SCHEMA_VERSION outside `db.py:SCHEMA_VERSION` import.

## Track Flow

```
T1 (serial, ~250 LOC) — spread_bucket FDR partition
  └─► T2 (parallel pair, ~200 LOC each) — stale_quote_detector + resolution_window_maker
        └─► T3 (parallel pair, ~250 LOC each) — liquidity_provision_with_heartbeat + weather_event_arbitrage
              └─► T4 (after Phase 3 T1 + Phase 5 T2 merge) — cross_market_correlation_hedge + neg_risk_basket
```

## T1 — spread_bucket FDR Partition (~250 LOC)

**Files:** `src/strategy/selection_family.py`

**Deliverables:**
1. Add `spread_bucket: Literal["tight","medium","wide"] = ""` kwarg to `make_hypothesis_family_id` and `make_edge_family_id`. Position-prefix `sb={spread_bucket}` appended only when non-empty (default "" preserves existing IDs byte-for-byte).
2. Bucket thresholds: tight ≤ $0.05; medium ≤ $0.10; wide > $0.10 (matches Phase 0 PR 2+7 `EffectiveKellyContext`).
3. Grammar:
   - hyp: `hyp|{cycle_mode}|{city}|{target_date}|{metric}|{discovery_mode}[|snap=…][|src=…][|rgm=…][|sb=…]`
   - edge: `edge|…[|snap=…][|src=…][|rgm=…][|sb=…]`
4. Shoulder family ID: `make_shoulder_hypothesis_family_id` is explicitly DEFERRED in Phase 4 (shoulder family grammar is fixed by dossier §7.5 5-segment form; bucket discrimination for shoulder is a Phase 6 concern alongside `ShadowDecisionLogVNext`). Plan revisits after Phase 6.

**Acceptance criteria:**
- `grep "spread_bucket" src/strategy/selection_family.py` shows it in both signatures + docstrings.
- No-collision proof: `make_hypothesis_family_id(decision_snapshot_id="X")` ≠ `make_hypothesis_family_id(spread_bucket="X")` ≠ `make_hypothesis_family_id(source="X")` ≠ `make_hypothesis_family_id(regime="X")`. All four produce distinct byte strings.
- BH partition antibody (counts-based, NOT threshold-direction — tied p-values otherwise produce spurious pass/fail): synthetic 100-hypothesis family mixing tight + medium + wide. Assertion: `len(tight_bucket_hypotheses) < len(all_hypotheses_mixed) AND len(wide_bucket_hypotheses) < len(all_hypotheses_mixed)`. Each per-bucket BH call receives strictly fewer hypotheses than the mixed family → BH `k·α/m` denominator shrinks → bucket partition is provably non-loosening. Test must FAIL against pre-T1 code (where `spread_bucket` kwarg absent → all hypotheses share one family) and PASS post-T1.
- Default callers unchanged: `make_hypothesis_family_id(cycle_mode=…, city=…, target_date=…, temperature_metric=…, discovery_mode=…)` produces identical IDs pre- and post-T1.
- Tag: `phase4_t1_landed`.

## T2 — stale_quote_detector + resolution_window_maker (~200 LOC each, parallel)

**Files:** `src/strategy/candidates/{stale_quote_detector,resolution_window_maker}.py`; `architecture/strategy_profile_registry.yaml`.

**Base interface contract (applies to T2/T3/T4):** Each candidate implements
```python
def evaluate(self, *, context: MarketAnalysisVNext, conn: sqlite3.Connection, decision_time: datetime) -> CandidateDecision: ...
```
where `CandidateDecision` is a frozen dataclass with one of: (a) `outcome: Literal["enter"]` + `side`, `target_price`, `target_size_usd`, `edge`, `p_posterior` (writes `decision_events` row via canonical writer); or (b) `outcome: Literal["no_trade"]` + `reason: NoTradeReason`, `reason_detail` (writes `no_trade_events` row). Never returns None; never silent. Add `evaluate()` to `BaseStrategyCandidate` in `src/strategy/candidates/__init__.py` as part of T2 (one-line abstract method).

**Per candidate:**
1. Replace stub body with production `evaluate()` per contract above. `CandidateMetadata(family=<concrete name>, executable_alpha=True)` (metadata only — `executable_alpha` is `CandidateMetadata` field per `candidates/__init__.py`, NOT registry YAML).
2. Registry entry in `strategy_profile_registry.yaml` using only `_REQUIRED_FIELDS` (verify field set in `strategy_profile.py:239` at dispatch time): `live_status: shadow`. Adding `executable_alpha` raises RegistrySchemaError.
3. NoTradeReason additions: `STALE_QUOTE_FILL_INFEASIBLE` (stale_quote_detector); `RESOLUTION_DISPUTED` (resolution_window_maker).
4. Provenance header per file: `Created: 2026-05-21 · Last reused/audited: 2026-05-21 · Authority basis: 05_PHASE_4_FDR_FAMILY_CANDIDATES.md`.

**Acceptance criteria per candidate:**
- File > 50 LOC with `evaluate()` matching base signature.
- Relationship test (TWO assertions): (i) on enter-decision input → `decision_events` row written with `strategy_key == <candidate_name>`; (ii) on no-trade input → `no_trade_events` row written with `reason == <candidate's reason enum value>`. Neither path silently drops.
- `strategy_profile_registry.yaml` row exists; `strategy_profile.get(<candidate>).is_runtime_live() == False`.
- Tag: `phase4_t2_landed`.

## T3 — liquidity_provision_with_heartbeat + weather_event_arbitrage (~250 LOC each, parallel after T2)

**Files:** `src/strategy/candidates/{liquidity_provision_with_heartbeat,weather_event_arbitrage}.py`; registry yaml.

**Deliverables:** Same structure as T2 with the candidate-specific edge sources from authority doc §"Candidate strategies" table. NoTradeReason additions: `LIQPROV_HEARTBEAT_ABSENT` (liquidity_provision_with_heartbeat — guard when fill_probability field is absent on `MarketAnalysisVNext`); `WEATHER_ALERT_SOURCE_UNTRUSTED` (weather_event_arbitrage — guard when external alert feed is not wired).

**Acceptance criteria:** Same as T2 plus: the missing-field guard path is exercised in at least one test (NoTradeReason emission, not just happy-path enter). Tag: `phase4_t3_landed`.

## T4 — cross_market_correlation_hedge + neg_risk_basket (~300 LOC each, deferred)

**Unblock conditions (BOTH must be on main):** Phase 3 T1 production merge (`regime_tag_for()` callable returning `WeatherRegimeTag`); Phase 5 T2 merge (`correlation_cluster_for()` callable on shrunk correlation matrix).

**Deliverables:** Production `evaluate()` per base contract. NoTradeReason additions: `CORR_HEDGE_REGIME_UNAVAILABLE`, `NEGRISK_FAMILY_INCOMPLETE`.

**Acceptance criteria:**
- `cross_market_correlation_hedge.evaluate()` emits `no_trade_events` row with `reason == CORR_HEDGE_REGIME_UNAVAILABLE` when `regime_tag_for()` returns `WeatherRegimeTag.UNKNOWN` (the fail-open value; `regime_tag_for` never returns None per `weather_regime_tag.py` design note).
- `neg_risk_basket.evaluate()` emits `NEGRISK_FAMILY_INCOMPLETE` when full token book per family is unavailable.
- Tag: `phase4_t4_landed` + umbrella `phase4_landed`.

## Critics (informational — NOT dispatched from this brief)

T1 opus SCAFFOLD critic (math + ID-grammar non-collision proof); T2/T3/T4 sonnet SCAFFOLD critic per track; one opus wave-critic before final merge.
