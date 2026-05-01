# ATTRIBUTION_DRIFT packet — executor boot

Created: 2026-04-28
Author: executor-harness-fixes@zeus-harness-debate-2026-04-27 (LONG-LAST)
Judge: team-lead
Source dispatch: DISPATCH_ATTRIBUTION_DRIFT_PACKET (R3 next packet post-EDGE_OBSERVATION ship)
Plan-evidence basis: docs/operations/task_2026-04-27_harness_debate/round3_verdict.md
Reuse note: same K1 read-only patterns + same canonical surface
(query_authoritative_settlement_rows / position_events) as the just-shipped
EDGE_OBSERVATION packet. The 3-batch + critic-gate cadence repeats.

## §0 Read summary

| Source | What I learned |
|---|---|
| AGENTS.md L114-126 | 4-strategy table (Settlement Capture / Shoulder Bin Sell / Center Bin Buy / Opening Inertia) + edge-source taxonomy + alpha-decay table; `strategy_key` is "sole governance identity for attribution, risk policy, and performance slicing" |
| AGENTS.md L60-68 | Bin types: `point` (1) / `finite_range` (2) / `open_shoulder` (unbounded). "Shoulder bins are not symmetric bounded ranges. Do not infer bin semantics from label punctuation or continuous-interval intuition." — antibody for naive label parsing |
| src/state/strategy_tracker.py L49 | STRATEGIES enum: settlement_capture / shoulder_sell / center_buy / opening_inertia |
| src/engine/evaluator.py L420-441 | **GROUND TRUTH dispatch logic** for strategy_key assignment. The rule is small + deterministic: (1) discovery_mode=DAY0_CAPTURE → settlement_capture, (2) discovery_mode=OPENING_HUNT → opening_inertia, (3) bin.is_shoulder → shoulder_sell, (4) direction=='buy_yes' → center_buy, (5) fallback → opening_inertia. This rule IS the spec; the detector re-applies it on persisted attributes and compares. |
| src/types/market.py L95-105 + L124 | `Bin.is_shoulder` = `is_open_low or is_open_high`; "Shoulder bin 'X°F or below': unbounded → width = None"; label patterns "X or below" / "X or higher" / "X+" indicate shoulder |
| src/state/edge_observation.py | Same canonical surface: query_authoritative_settlement_rows + metric_ready filter + STRATEGY_KEYS list. Reuse pattern is direct. |
| ULTIMATE_PLAN.md L305-308 | Packet definition: "No detector exists for silent attribution drift (e.g., a position labeled shoulder_bin_sell but executed against center_bin_buy semantics)." |
| src/state/db.py L613/669/706/2205/2265 | discovery_mode is persisted on trade_decisions + position_current rows; query_authoritative_settlement_rows includes it (verified via _normalize_position_settlement_event row shape inspection) |

## §1 Path corrections from dispatch

| Dispatch said | Actual on HEAD | Implication |
|---|---|---|
| "look at how positions are labeled with strategy_key during ENTRY" | Found at src/engine/evaluator.py L420-441 (`_strategy_key_for` + `_strategy_key_for_hypothesis`) | Both helpers use SAME 5-clause dispatch; this rule IS the spec. |
| "look at row.bin_label / row.target_date / row.direction to infer execution semantics" | Confirmed: the normalized row from query_authoritative_settlement_rows has bin_label + direction (via position_current LEFT JOIN) | Need to ALSO surface discovery_mode to re-apply the full rule. Will check normalizer. |
| "edge_observation.py just shipped — REUSE" | edge_observation.py at HEAD has STRATEGY_KEYS + same canonical surface | Re-use STRATEGY_KEYS import + same metric_ready filter pattern + same window/sample_quality conventions |

**KEY RISK**: if the normalized row from `_normalize_position_settlement_event` does NOT include `discovery_mode`, the detector cannot apply clauses 1-2 of `_strategy_key_for`. I will check at BATCH 1 start; if absent, options: (a) extend the normalizer (out of scope per K0_frozen_kernel rule), (b) JOIN to trade_decisions in the query (fits the K1 read-only pattern), (c) treat clauses 1-2 as "insufficient_signal" when discovery_mode missing and only assert clauses 3-5 (bin.is_shoulder + direction). **Default plan: option (c) — degrade gracefully**, document the limitation, and propose option (b) as a future enhancement if operator decides drift detection on Day0/Opening trades is critical.

## §2 Per-batch design sketch

### BATCH 1 — `compute_attribution_signature_per_position` + `detect_attribution_drift` (~6-10h)

**File**: `src/state/attribution_drift.py` (NEW, ~120-180 LOC).

**Function signatures**:
```python
@dataclass
class AttributionSignature:
    position_id: str
    label_strategy: str              # the persisted strategy_key
    inferred_strategy: str | None    # what the dispatch rule yields, or None
    bin_topology: Literal["point","finite_range","open_shoulder","unknown"]
    direction: str                   # buy_yes / buy_no / unknown
    discovery_mode: str | None
    is_label_inferable: bool         # False when discovery_mode missing AND we cannot infer

@dataclass
class AttributionVerdict:
    kind: Literal["label_matches_semantics","drift_detected","insufficient_signal"]
    position_id: str
    signature: AttributionSignature
    evidence: dict[str, Any]   # mismatch detail, what each rule clause said

def _classify_bin_topology(bin_label: str) -> str: ...
def _infer_strategy_from_signature(sig: AttributionSignature) -> str | None: ...
def detect_attribution_drift(row: dict) -> AttributionVerdict: ...
```

**`_classify_bin_topology` heuristic** (per AGENTS.md L66 antibody warning — but we MUST use the label string because that's what's persisted):
- `"or below"` / `"or higher"` / `" or "` patterns / `"+"` suffix → `open_shoulder`
- single integer + `°C` → `point`
- `N-M°F` range → `finite_range`
- else → `unknown` (insufficient_signal)

**Tests** (`tests/test_attribution_drift.py`, NEW, ~150-200 LOC, ~7 tests):
1. `test_settlement_capture_label_matches_when_day0_discovery` — discovery_mode=day0_capture + label=settlement_capture → label_matches
2. `test_drift_detected_label_says_shoulder_but_bin_is_finite_range` — labeled shoulder_sell, bin_label="50-51°F" → drift_detected
3. `test_drift_detected_label_says_center_buy_but_direction_is_buy_no` — labeled center_buy, direction=buy_no → drift_detected (rule 4 only triggers center_buy on buy_yes)
4. `test_label_matches_when_all_clauses_align` — labeled center_buy, direction=buy_yes, finite_range bin → match
5. `test_insufficient_signal_when_discovery_mode_missing_and_clauses_1_2_required` — labeled settlement_capture but discovery_mode=None → insufficient (cannot rule out clause 1)
6. `test_bin_topology_classifier` — covers point / finite_range / open_shoulder / unknown
7. `test_unknown_strategy_label_quarantined` — strategy_key not in 4 known → insufficient (or drift; design choice)

**Mesh**: register in source_rationale.yaml + test_topology.yaml (planning-lock applies; ARCH_PLAN_EVIDENCE=round3_verdict.md).

### BATCH 2 — `compute_drift_rate_per_strategy` + tests (~4-6h)

**File**: extends `src/state/attribution_drift.py` (~50-80 LOC added).

**Signature**:
```python
def compute_drift_rate_per_strategy(
    conn: sqlite3.Connection, window_days: int = 7, end_date: str | None = None,
) -> dict[str, dict]:
    """Aggregate per-strategy drift counts over a window. Mirrors
    compute_realized_edge_per_strategy shape (window/sample_quality).
    Returns: {strategy_key: {drift_rate, n_positions, n_drift, n_matches, n_insufficient, sample_quality, window_start, window_end}}.
    """
```

Reuses metric_ready filter + STRATEGY_KEYS + sample_quality boundaries from edge_observation.py.

**Tests** (~6): per-strategy rate correctness, sample-quality boundaries, empty-result safety, window filter, mixed-verdict aggregation, drift_rate denominator excludes insufficient_signal counts.

### BATCH 3 — `attribution_drift_weekly.py` runner + AGENTS.md + e2e tests (~3-5h)

**File**: `scripts/attribution_drift_weekly.py` (NEW, ~120-180 LOC).

CLI mirrors `edge_observation_weekly.py`: `--end-date`, `--window-days`, `--db-path`, `--report-out`, `--stdout`.
Default report path: `docs/operations/attribution_drift/weekly_<YYYY-MM-DD>.json`.
Exit 1 if any strategy has drift_rate above a threshold (default ~0.05 = 5% of positions in the window have label/semantics mismatch).

NEW dir `docs/operations/attribution_drift/` + small AGENTS.md (same option (b) pattern as edge_observation/).

**Tests** (~3-4 e2e): structural shape, empty-DB graceful, drift propagation with synthetic data, custom report path round-trip.

**Mesh**: register in script_manifest.yaml + test_topology.yaml.

## §3 Risk assessment per batch

| Batch | Risk | Mitigation |
|---|---|---|
| 1 | LOW-MEDIUM (label-vs-semantics inference is the load-bearing step; bin-label heuristic could mis-classify) | Tests cover each bin-topology class explicitly; insufficient_signal is the safe verdict when classifier is uncertain (per AGENTS.md L66 antibody) |
| 2 | LOW (pure aggregation over BATCH 1 outputs) | Reuses EDGE_OBSERVATION conventions; tests follow same structure |
| 3 | LOW-MEDIUM (CLI + JSON output + new dir + script_manifest registration) | Same precedent as EDGE_OBSERVATION BATCH 3 (operator already approved option b for the dir + AGENTS.md) |

**Cross-batch risk**: if `_normalize_position_settlement_event` does not surface `discovery_mode`, my BATCH 1 must degrade gracefully (insufficient_signal). Will verify at start of BATCH 1 implementation; if a small read-only extension to the normalizer is needed, that becomes a separate operator-decision point (touches K0 zone).

## §4 Discipline pledges

- ARCH_PLAN_EVIDENCE = `docs/operations/task_2026-04-27_harness_debate/round3_verdict.md` for every architecture/** edit
- Pytest baseline preserved per BATCH (current 109/22/0; new tests bump baseline)
- file:line citations grep-verified within 10 min before commit
- Disk-first: write before SendMessage
- Co-tenant git hygiene: stage SPECIFIC files; never `git add -A`
- NO commits without critic-gate APPROVE
- Per BATCH: SendMessage `BATCH_X_DONE_ATTRIBUTION_DRIFT files=<paths> tests=<X passed Y failed> baseline=<status> planning_lock=<receipt>`

## §5 Out-of-scope (per dispatch — will NOT touch)

- LEARNING_LOOP packet (separate; later)
- CALIBRATION_HARDENING (Week 13; precondition INV-15+09 met but packet itself separate)
- WS_OR_POLL_TIGHTENING (separate)
- Schema migrations (use existing position_events surface)
- Modifications to strategy_tracker.py (deprecated; build around)

## §6 Open clarifications for team-lead (recommend defaults if no specific guidance)

1. **discovery_mode availability in normalized row**: confirm I can rely on `discovery_mode` field surfacing from query_authoritative_settlement_rows? **Default: verify at BATCH 1 start; if absent, degrade to insufficient_signal for clauses 1-2 (Day0/Opening detection) and only assert clauses 3-5 (shoulder/direction). Document the limitation.**
2. **Insufficient-signal denominator**: when computing drift_rate per strategy, should `insufficient_signal` positions be EXCLUDED from the denominator (drift_rate = drift / (drift + match)), or counted separately? **Default: EXCLUDE from denominator; surface n_insufficient as separate field. Reason: a drift_rate of "5%" should mean 5% of definitively-classifiable positions drifted, not 5% diluted by uncertainty.**
3. **Drift threshold for runner exit-1**: dispatch suggested ~5% as cron alert threshold; configurable via CLI flag? **Default: yes, --drift-rate-threshold N (default 0.05); operator can tune per their tolerance.**
4. **Test coverage for the "label says shoulder_sell but bin is finite_range" case**: this is the canonical drift case from ULTIMATE_PLAN.md L305-308. Synthesize via direct `_insert_settled` call with mismatched bin_label + strategy_key in the helper? **Default: yes, use the same _insert_settled helper from BATCH 1 of EDGE_OBSERVATION (already in tests/test_edge_observation.py); extend it to allow custom bin_label override for this test bed.**

Will idle after BOOT_ACK_EXECUTOR_ATTRIBUTION_DRIFT. Will execute BATCH 1 only after explicit GO_BATCH_1 from team-lead, with answers to §6 clarifications (or default-to-recommendation if no specific guidance).

End of boot.
