# FIX_F25 — decision_snapshot_id (DSI) threading via Strategy R

**Authority**: Sonnet deep-design 2026-05-17T13:11Z + RUN_7 finding F25 + RUN_8 sweep cards.
**Status**: READY-TO-SHIP after operator approval.
**Karachi 5/17 blast radius**: LOW (evaluator only runs in cycle_runner, deploys between 10-30min cycle ticks; not on redeem cascade path).

---

## 1. Problem statement

`opportunity_fact.snapshot_id` 68.2% NULL (19,175 rows) + `probability_trace_fact.decision_snapshot_id` 67.74% NULL — still growing 2026-05-02 → 2026-05-17. Root cause: `EdgeDecision()` ctor has 71 sites in `src/engine/evaluator.py:evaluate_candidate()` (function at line 1675), of which 31 do NOT pass `decision_snapshot_id` (all early-rejection paths firing BEFORE `snapshot_id` is resolved at line 2378+).

## 2. Strategy R — chosen approach

Introduce `_make_rejection_decision(...) -> EdgeDecision` helper that all 31 early-rejection sites funnel through. Helper stamps `decision_snapshot_id = "<pre_snapshot:rejected>"` sentinel (non-NULL, SQL-queryable via `LIKE '<pre_snapshot:%`). Dataclass adds `__post_init__` rejecting `None`.

**Strategy P (hoist snapshot_id) REJECTED** — would force ENS snapshot writes for trivially-rejected candidates, changing DB write timing.
**Strategy Q (frozen + post_init only) REJECTED** — `frozen=True` collides with `cycle_runtime.py:2584` mutation `_d.market_phase = _phase_value`.

## 3. Code changes

### 3a. EdgeDecision dataclass (`src/engine/evaluator.py:227-277`)

```python
@dataclass
class EdgeDecision:
    ...
    decision_snapshot_id: str = ""
    ...

    def __post_init__(self):
        if self.decision_snapshot_id is None:
            raise ValueError(
                "EdgeDecision.decision_snapshot_id must not be None"
            )
```

**Do NOT add `frozen=True`** — cycle_runtime.py:2584 mutates `market_phase` post-construction.

### 3b. Helper insertion (~line 279, after dataclass, before `_read_v2_snapshot_metadata`)

```python
_PRE_SNAPSHOT_DSI_SENTINEL = "<pre_snapshot:rejected>"

def _make_rejection_decision(
    *,
    rejection_stage: str,
    rejection_reasons: list[str],
    selected_method: str,
    applied_validations: list[str],
    availability_status: str = "",
    p_raw=None,
) -> "EdgeDecision":
    """Canonical ctor for pre-snapshot rejections. Stamps DSI sentinel."""
    return EdgeDecision(
        False,
        decision_id=_decision_id(),
        rejection_stage=rejection_stage,
        rejection_reasons=rejection_reasons,
        selected_method=selected_method,
        applied_validations=applied_validations,
        availability_status=availability_status,
        p_raw=p_raw,
        decision_snapshot_id=_PRE_SNAPSHOT_DSI_SENTINEL,
    )
```

### 3c. Migrate 31 sites (lines 1723-2384 in current HEAD; +1 from RUN_8 estimate of 2422)

Mechanical replacement pattern:
```python
# BEFORE
return [EdgeDecision(
    False,
    decision_id=_decision_id(),
    rejection_stage="FOO",
    rejection_reasons=[...],
    availability_status="...",
    selected_method=selected_method,
    applied_validations=[...],
)]

# AFTER
return [_make_rejection_decision(
    rejection_stage="FOO",
    rejection_reasons=[...],
    availability_status="...",
    selected_method=selected_method,
    applied_validations=[...],
)]
```

Sites that also pass `p_raw=p_raw` (L2365, L2084, L2162) include that kwarg in the helper call.

**Pre-implementation gate**: re-run `grep -n "return \[EdgeDecision(" src/engine/evaluator.py | awk '$1 < 2390'` to capture fresh line numbers.

### 3d. Net LOC delta

- `__post_init__` validator: +4 LOC
- Helper + sentinel constant: +20 LOC
- 31 site migrations: each net -1 LOC (collapse 6-line ctor → 5-line helper call)
- **Total: ~−7 LOC**

## 4. Antibody test (`tests/engine/test_evaluator_dsi_invariant.py`)

```python
# Created: 2026-05-17
# Authority basis: F25 audit / Strategy R sentinel contract
import re
import pytest
from unittest.mock import MagicMock
from src.config import City
from src.engine.evaluator import MarketCandidate, evaluate_candidate, _PRE_SNAPSHOT_DSI_SENTINEL

_SENTINEL_RE = re.compile(r"^<pre_snapshot:.+>$")

def _city():
    return City(name="NYC", lat=40.78, lon=-73.87, timezone="America/New_York",
                cluster="NYC", settlement_unit="F", wu_station="KLGA")

def _candidate_few_bins():
    return MarketCandidate(city=_city(), target_date="2026-06-01",
        outcomes=[{"range_low": 70, "range_high": 80, "title": "70-80°F"}],
        hours_since_open=24.0, temperature_metric="high")

def test_early_rejection_dsi_is_sentinel():
    """< 3 bins triggers MARKET_FILTER before snapshot; DSI must be sentinel."""
    decisions = evaluate_candidate(
        _candidate_few_bins(), conn=None,
        portfolio=MagicMock(), clob=MagicMock(), limits=MagicMock(),
    )
    assert decisions
    d = decisions[0]
    assert d.rejection_stage == "MARKET_FILTER"
    assert _SENTINEL_RE.match(d.decision_snapshot_id), (
        f"Expected sentinel, got {d.decision_snapshot_id!r}"
    )
```

## 5. Consumer impact audit

| Consumer | Behavior with sentinel |
|---|---|
| `src/state/db.py:5177` (probability_trace_fact write) | TEXT column, no FK — safe |
| `src/state/db.py:5454` (opportunity_fact.snapshot_id write) | TEXT, no FK — safe |
| `src/state/decision_chain.py:394-395` (truthy check) | **IMPROVES** — non-empty sentinel passes truthy check, removes `degraded_reasons.append("missing_decision_snapshot_id")` for pre-snapshot rows |
| `src/state/db.py:5837` (`if dsi not in (None, "")`) | safe (sentinel is non-empty, non-None) |
| `src/contracts/edge_context.py:24` (dataclass field) | safe (passthrough) |

**No FK constraints on either fact table's DSI column.** Sentinel is safe at schema layer.

## 6. SQL audit query (post-deploy verification)

```sql
-- Should trend toward 0 NULL after deploy
SELECT COUNT(*) FROM opportunity_fact WHERE snapshot_id IS NULL AND recorded_at > '<deploy_ts>';

-- New pattern: rejected rows have sentinel
SELECT COUNT(*) FROM probability_trace_fact 
WHERE decision_snapshot_id LIKE '<pre_snapshot:%' AND recorded_at > '<deploy_ts>';
```

## 7. Ship sequencing

1. Critic-review this fix-shape file
2. Operator approval gate
3. Implementer: apply 3a → 3b → 3c → write 4 antibody test
4. Run full pytest on `tests/engine/test_evaluator_*` (current passes must stay green)
5. Run new `test_evaluator_dsi_invariant.py` (must pass)
6. Deploy between cycle ticks (any time)
7. Post-deploy: run SQL audit query at T+1h to confirm NULL trend

## 8. Karachi 5/17 specifics

- evaluate_candidate runs in `cycle_runtime.py` (line 2576 comment references the 30+ return sites)
- NOT imported by `src/execution/{harvester,harvester_pnl_resolver,settlement_commands}` (zero grep hits)
- NOT imported by `src/state/chain_reconciliation.py`
- Therefore: deployable DURING Karachi window with zero cascade-disruption risk

## 9. Open questions for critic

1. Should the sentinel format be `<pre_snapshot:{rejection_stage}>` (carry the stage) instead of generic? Pro: better forensics. Con: explodes LIKE patterns in audit queries.
2. Should the antibody also assert `decision_chain.py` no longer adds `missing_decision_snapshot_id` to `degraded_reasons` for sentinel rows?

## 10. Two-sentinel reconciliation (2026-05-17, post-#137 code-review NIT2)

Shipped code stamps **two distinct sentinels on two distinct fields** — both correct, no drift:

| Field | Sentinel | Stamped at | Purpose |
|---|---|---|---|
| `decision_snapshot_id` | `<pre_snapshot:rejected>` | `_make_rejection_decision` (this doc §3) | Rejection-path DSI; never resolves to a real snapshot row |
| `decision_id` | `<pre_decision:family>` | F2 caller `_record_selection_family_facts` at `evaluator.py:3131` | Family-level audit row written BEFORE per-candidate decision_id materializes |

Both: non-NULL, SQL-queryable via `LIKE '<pre_%'`, never collide with real UUIDs. Audit-query convention: `WHERE field NOT LIKE '<pre_%'` filters out both sentinel families.
