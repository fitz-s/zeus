# PR 2+7 Bundled Scaffold — WideSpread/Depth + EffectiveKellyContext

**Date**: 2026-05-19
**Branch**: feat/phase0-pr27-effective-kelly-bundled-20260519
**Authority**: PHASE_0_V4_ULTRAPLAN.md §E.1 + PHASE_0_V4_ADDENDUM.md + critic_2_pr2_pr7_kelly.md
**Invariants closed**: INV-kelly-effective (new) + INV-12, INV-21, INV-33

---

## 1. Grep-verified call-site enumeration (7 sites)

### Direct `kelly_size()` callers — 2 sites

| # | File | Line | Signature snapshot |
|---|---|---|---|
| K1 | `src/engine/evaluator.py` | **816** | `kelly_size(p_posterior, ep_fee_adjusted, sizing_bankroll, kelly_multiplier)` — inside `_size_at_execution_price_boundary` |
| K2 | `src/backtest/executable_ev_replay.py` | **119** | `kelly_size(p_posterior=…, entry_price=executable_price, bankroll=replay_input.bankroll_usd, kelly_mult=replay_input.kelly_multiplier)` |

### `_size_at_execution_price_boundary()` callers — 5 sites

| # | File | Line | Context |
|---|---|---|---|
| W1 | `src/engine/evaluator.py` | **3676** | main evaluate_candidate sizing — `km * risk_throttle` as kelly_multiplier |
| W2 | `src/engine/cycle_runtime.py` | **756** | `_reprice_decision_from_executable_snapshot` — passive maker reprice; `snapshot` in scope |
| W3 | `src/engine/cycle_runtime.py` | **793** | `_reprice_decision_from_executable_snapshot` — best_ask taker sizing; `snapshot` in scope |
| W4 | `src/engine/cycle_runtime.py` | **862** | `_reprice_decision_from_executable_snapshot` — depth-sweep limit; `snapshot` in scope |
| W5 | `src/engine/replay.py` | **1722** | replay sizing — no snapshot object in scope at call point |

**Evidence**: grep output `_size_at_execution_price_boundary` = 8 matches / 3 files (2 def+self-call in evaluator.py, 4 in cycle_runtime.py, 2 in replay.py). `kelly_size(` direct callers = 2 production sites confirmed.

---

## 2. PR 2 — Field declarations for ExecutableMarketSnapshotV2

### New fields (appended after `freshness_deadline` in the frozen dataclass)

```python
# PR 2 — microstructure transparency fields
wide_spread_display_substitution: bool = False
# True when observed spread >= WIDE_SPREAD_THRESHOLD_USD (0.10).
# Polymarket substitutes last-trade price for midpoint in UI above this.

depth_at_best_ask: int = 0
# Number of shares available at best ask from orderbook_depth_jsonb["asks"][0]["size"].
# Parsed as int (shares, rounded down). 0 = one-sided book or unavailable.

# NOTE (2026-05-19 bot-review fixup): spread_observed_window_ms was in the original
# scaffold design but was REMOVED from scope in Path-A.  Only two fields shipped:
# wide_spread_display_substitution and depth_at_best_ask.  Do not add
# spread_observed_window_ms unless explicitly re-scoped in a future PR.
```

### Validator additions in `__post_init__`

```python
if not isinstance(self.wide_spread_display_substitution, bool):
    raise TypeError("wide_spread_display_substitution must be bool")
if self.depth_at_best_ask < 0:
    raise ValueError("depth_at_best_ask must be >= 0")
# Derive wide_spread_display_substitution from top-level fields if not set.
# NOT auto-derived in __post_init__ — caller must compute and pass explicitly
# so the derivation logic is grep-visible at the construction site.
```

### One-sided-book semantics

- `orderbook_top_ask is None` → `depth_at_best_ask = 0`, `wide_spread_display_substitution` derived from `orderbook_top_bid` alone against last-trade (caller sets).
- Crossed book (`top_bid >= top_ask`) — already raises in existing `__post_init__`; no change needed.

### WIDE_SPREAD_THRESHOLD constant

New constant in `executable_market_snapshot_v2.py`:
```python
WIDE_SPREAD_THRESHOLD_USD = Decimal("0.10")  # Polymarket UI substitution threshold
```

### Storage migration — `executable_market_snapshots` table (world.db)

Two new columns added via `ALTER TABLE … ADD COLUMN` in `snapshot_repo.py::init_snapshot_schema()`
(spread_observed_window_ms removed from scope — see note above):
```sql
ALTER TABLE executable_market_snapshots ADD COLUMN wide_spread_display_substitution INTEGER NOT NULL DEFAULT 0 CHECK (wide_spread_display_substitution IN (0,1));
ALTER TABLE executable_market_snapshots ADD COLUMN depth_at_best_ask INTEGER NOT NULL DEFAULT 0;
```
Pattern: `ADD COLUMN … DEFAULT` matches existing SQLite ALTER pattern used by Wave-A PRs. No SAVEPOINT required — single-DB, no cross-DB write. INV-37 does NOT apply here.

`_row_from_snapshot` and `_snapshot_from_row` updated to include the two shipped fields.

### market_scanner.py — populate at construction

In `market_scanner.py` at the `ExecutableMarketSnapshotV2(…)` construction site (~line 1946):
```python
# Parse spread and depth from raw_orderbook
_spread = _compute_spread(raw_orderbook, top_bid, top_ask)
wide_spread_display_substitution=(_spread is not None and _spread >= WIDE_SPREAD_THRESHOLD_USD),
depth_at_best_ask=_depth_at_best_ask(raw_orderbook),
# spread_observed_window_ms removed from scope (not shipped in Path-A)
```

`_compute_spread` and `_depth_at_best_ask` are module-private helpers in `market_scanner.py`. `_depth_at_best_ask` parses `orderbook["asks"][0]["size"]` — same pattern as `_top_book_level_decimal` already in `market_scanner.py`.

---

## 3. PR 7 — EffectiveKellyContext bucket policy

### New file: `src/contracts/effective_kelly_context.py`

```python
# Lifecycle: PR 2+7 — microstructure-aware Kelly haircut
# Purpose: Graded Kelly multiplier based on spread × depth × order_type
# Reuse: Thread through _size_at_execution_price_boundary and kelly_size callers

@dataclass(frozen=True)
class EffectiveKellyContext:
    spread_usd: Decimal          # observed bid-ask spread (ask - bid, or None if one-sided)
    depth_at_best_ask: int       # shares at best ask (0 = unavailable)
    order_type: str              # "FOK" | "FAK" | "GTC" | "GTD" | "LIMIT"
    fee_erased: bool = False     # True when spread+fee fully erases edge

class MissingEffectiveContextError(ValueError):
    """Raised when kelly_size is called without EffectiveKellyContext
    and wide_spread_display_substitution=True. INV-kelly-effective."""
```

### Bucket policy (3 spread × 2 depth × order_type → haircut)

| Spread bucket | Depth bucket | FOK haircut | FAK haircut |
|---|---|---|---|
| TIGHT (< $0.05) | DEEP (≥ 100 shares) | 1.00 (no haircut) | 1.00 |
| TIGHT (< $0.05) | SHALLOW (< 100 shares) | 0.85 | 0.75 |
| MID ($0.05–$0.10) | DEEP (≥ 100 shares) | 0.90 | 0.80 |
| MID ($0.05–$0.10) | SHALLOW (< 100 shares) | 0.70 | 0.55 |
| WIDE (≥ $0.10) | DEEP (≥ 100 shares) | 0.50 | 0.30 |
| WIDE (≥ $0.10) | SHALLOW (< 100 shares) | 0.30 | 0.10 |

**FOK rationale**: wide spread + FOK = price-guaranteed (fill or kill); haircut reflects reduced fill probability. **FAK rationale**: wide spread + FAK = partial fill at bad price guaranteed; deeper haircut needed.

Non-FOK/FAK order types (GTC, GTD, LIMIT) use FAK haircut values (conservative).

**Fee-erased branch**: if `fee_erased=True`, haircut = 0.0 (forces kelly_size to return 0.0). Checked before bucket lookup.

### Composition with existing 4 multipliers

`EffectiveKellyContext.haircut()` returns a scalar `float`. This is applied as a **5th multiplicative factor** AFTER the existing 4-multiplier chain:
```
km_effective = km_4x * haircut_factor
```
Where `km_4x = base_kelly × strategy_kelly_multiplier × city_kelly_multiplier × (1 - DDD_discount)`.

This composes at `_size_at_execution_price_boundary` parameter: `kelly_multiplier = km_4x * context.haircut()`.

### INV-kelly-effective enforcement

`kelly_size` signature UNCHANGED. The enforcement point is at `_size_at_execution_price_boundary` (the structural wrapper). PR 7 adds an optional `effective_context` parameter to `_size_at_execution_price_boundary`:

```python
def _size_at_execution_price_boundary(
    *,
    p_posterior: float,
    entry_price: float,
    fee_rate: float,
    sizing_bankroll: float,
    kelly_multiplier: float,
    effective_context: EffectiveKellyContext | None = None,  # NEW
) -> float:
```

Fail-closed rule: if `effective_context is None` AND caller is a live-money path (detectable via `ZEUS_MODE=live`), raise `MissingEffectiveContextError`. For backtest/replay paths, `effective_context=None` degrades gracefully (no haircut, logs WARNING).

Live-money detection: import `src.contracts.run_mode` (already used in codebase for ZEUS_MODE checks).

---

## 4. Threading plan — 7 call sites

| Site | Action | Context source |
|---|---|---|
| K1 (evaluator.py:816) | No change — `_size_at_execution_price_boundary` is the enforcement point; `kelly_size` itself stays unchanged | — |
| K2 (executable_ev_replay.py:119) | Add `effective_context` keyword; backtest path → graceful degrade (no haircut, warning) | No snapshot available; pass `None` with `is_backtest=True` |
| W1 (evaluator.py:3676) | Pass `effective_context` derived from `ens_result` microstructure fields if present; else `None` (graceful degrade) | `ens_result` has `bid`/`ask`/`bid_sz` from `clob.get_best_bid_ask`; order_type from candidate |
| W2 (cycle_runtime.py:756) | Pass `effective_context` from `snapshot` (already in scope at line 684) | `snapshot.wide_spread_display_substitution`, `snapshot.depth_at_best_ask`; order_type from `decision` |
| W3 (cycle_runtime.py:793) | Same as W2 | Same snapshot |
| W4 (cycle_runtime.py:862) | Same as W2 | Same snapshot |
| W5 (replay.py:1722) | Graceful degrade (`effective_context=None`) — replay has no snapshot object at this call point | No snapshot in scope at line 1722; log WARNING |

**Snapshot availability at W1**: `ens_result` from `clob.get_best_bid_ask` call at evaluator.py:2795 provides `bid`, `ask`, `bid_sz`, `ask_sz` — these are captured into `microstructure_sink` dict. The `spread_usd` = `ask - bid`, `depth_at_best_ask` = `int(ask_sz)`. Order type comes from `execution_intent` (default is LIMIT/FOK per market). At W1 the evaluator does NOT have the `ExecutableMarketSnapshotV2` loaded — it calls `clob.get_best_bid_ask` directly. So context is derived from raw bid/ask in the evaluate loop, not from the snapshot.

---

## 5. Relationship tests planned (pre-implementation per Fitz methodology)

### R-EE.1: `MissingEffectiveContextError` on wide spread + live path

```python
# tests/test_inv_kelly_effective.py
def test_missing_context_raises_on_wide_spread_live_path():
    """INV-kelly-effective: _size_at_execution_price_boundary raises when
    wide_spread_display_substitution=True and effective_context=None in live mode."""
```

### R-EE.2: AST audit — every prod caller passes EffectiveKellyContext

```python
def test_ast_audit_all_kelly_callers_pass_effective_context():
    """AST-scan src/ for _size_at_execution_price_boundary calls;
    assert each passes effective_context keyword argument."""
```

### R-EE.3: Haircut bucket table — property test

```python
def test_haircut_fok_always_gte_fak_same_bucket():
    """FOK haircut >= FAK haircut for every spread×depth combination."""
```

### R-EE.4: Haircut monotonicity — wider spread = smaller haircut

```python
def test_haircut_tight_gte_mid_gte_wide():
    """For fixed depth and order_type: tight bucket haircut >= mid >= wide."""
```

### R-EE.5: PR 2 field persistence roundtrip

```python
def test_wide_spread_fields_roundtrip_through_snapshot_repo():
    """Insert ExecutableMarketSnapshotV2 with wide_spread=True, depth=50, window_ms=0;
    reload from DB; assert fields equal."""
```

### R-EE.6: One-sided book → depth_at_best_ask=0

```python
def test_one_sided_book_yields_zero_depth():
    """When orderbook_top_ask is None, depth_at_best_ask must be 0."""
```

### R-EE.7: fee_erased branch forces zero size

```python
def test_fee_erased_context_yields_zero_size():
    """When EffectiveKellyContext.fee_erased=True, kelly size returned is 0.0."""
```

### R-EE.8: cycle_runtime snapshot threading — integration

```python
def test_cycle_runtime_reprice_passes_context_from_snapshot():
    """_reprice_decision_from_executable_snapshot passes effective_context
    constructed from snapshot.wide_spread_display_substitution and
    snapshot.depth_at_best_ask."""
```

---

## 6. Files to create/modify

| File | Action | PR |
|---|---|---|
| `src/contracts/executable_market_snapshot_v2.py` | ADD 3 fields + WIDE_SPREAD_THRESHOLD + validators | 2 |
| `src/state/snapshot_repo.py` | ADD 3 ALTER TABLE columns + row/unrow update | 2 |
| `src/data/market_scanner.py` | POPULATE 3 fields at construction site | 2 |
| `src/contracts/effective_kelly_context.py` | CREATE new file: EffectiveKellyContext + MissingEffectiveContextError + haircut() | 7 |
| `src/engine/evaluator.py` | EXTEND _size_at_execution_price_boundary signature + W1 threading | 7 |
| `src/engine/cycle_runtime.py` | THREAD effective_context at W2, W3, W4 | 7 |
| `src/engine/replay.py` | THREAD effective_context at W5 (graceful degrade) | 7 |
| `src/backtest/executable_ev_replay.py` | THREAD effective_context at K2 (graceful degrade) | 7 |
| `architecture/topology.yaml` | ADD packet `phase0-pr27-effective-kelly` | 7 |
| `architecture/test_topology.yaml` | ADD 8 new test entries | 7 |
| `tests/test_inv_kelly_effective.py` | CREATE with R-EE.1–R-EE.8 (xfail pre-implementation) | 7 |
| `tests/test_pr2_wide_spread_fields.py` | CREATE with R-EE.5, R-EE.6 (xfail pre-implementation) | 2 |

---

## 7. Migration plan

**Table**: `executable_market_snapshots` — owned by `world.db` per `architecture/db_table_ownership.yaml:509`.

**Migration approach**: `ALTER TABLE … ADD COLUMN … DEFAULT` (SQLite supports this without table rebuild). Three separate ALTER statements, each idempotent via try/except for "duplicate column" error. Applied in `init_snapshot_schema()` at startup — safe for WAL mode. No SAVEPOINT needed (single-DB DDL).

**Existing rows**: default values (`0`, `0`, `0`) apply retroactively — correct semantics (pre-PR2 snapshots are treated as narrow-spread, unknown depth, point-in-time). No backfill required.

**INV-37 note**: This is a single-DB operation on world.db only. INV-37 (ATTACH+SAVEPOINT) applies to cross-DB writes only; this migration is exempt.

---

## 8. Risk surfaces (per REVIEW.md Tier 0)

| Surface | Risk | Mitigation |
|---|---|---|
| `src/engine/evaluator.py` (T0) | Adding `effective_context` optional param to `_size_at_execution_price_boundary` — must not change existing callers' behavior when `effective_context=None` | Default `None` + graceful degrade = no behavioral change for pre-wired callers |
| `src/engine/cycle_runtime.py` (T0) | Passing context derived from snapshot — snapshot already loaded in scope; construction must not raise | Defensive: if snapshot lacks new fields (legacy row), fall back to `effective_context=None` |
| `src/state/snapshot_repo.py` (T0) | ALTER TABLE DDL at init time — idempotent guard needed | Try/except on `duplicate column` error; log INFO on skip |
| `src/contracts/executable_market_snapshot_v2.py` (T0) | Frozen dataclass change breaks all construction sites that don't pass new fields | Fields have defaults — all existing callers unaffected without code change |
| INV-kelly-effective enforcement | Fail-closed in live mode only; graceful in backtest | ZEUS_MODE detection via existing `run_mode` contract |

---

## 9. Estimated final LOC

| Category | Est. lines |
|---|---|
| New contract `effective_kelly_context.py` | ~80 |
| `executable_market_snapshot_v2.py` additions | ~40 |
| `snapshot_repo.py` additions | ~30 |
| `market_scanner.py` additions | ~25 |
| `evaluator.py` changes | ~40 |
| `cycle_runtime.py` threading | ~25 |
| `replay.py` + `executable_ev_replay.py` | ~20 |
| `topology.yaml` packet | ~40 |
| **Production total** | **~300** |
| `tests/test_inv_kelly_effective.py` | ~180 |
| `tests/test_pr2_wide_spread_fields.py` | ~100 |
| `test_topology.yaml` entries | ~80 |
| **Test total** | **~360** |
| **Grand total** | **~660** |

Well within 300-LOC PR floor and below 1500-LOC halt threshold.

---

## 10. Open escalations

None — all design decisions resolvable via inspection:
- `ZEUS_MODE` detection pattern: confirmed via existing `run_mode` contract usage.
- Depth threshold (100 shares): operationally motivated by Polymarket minimum order; operator-adjustable via settings if needed post-Phase-0.
- Spread thresholds ($0.05/$0.10): $0.10 from Polymarket docs (verified in plans); $0.05 is the midpoint bucket boundary (no secondary docs source, conservative half of $0.10).
