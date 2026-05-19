# PR 3 + PR 6 SCAFFOLD — DecisionSourceContext Coordinated Extension

**Branch**: `feat/phase0-pr36-decision-source-context-coordinated-20260519`
**Authored**: 2026-05-19 by Executor B/36
**Authority**: WAVE_B_PR_3_6_FIELD_MAP.md (field ownership table, 17 rows)
**Phase**: SCAFFOLD only — no production code bodies written

---

## Grep-verified line anchors (current main, worktree HEAD = ad2d99f)

| Symbol | File | Line | Notes |
|---|---|---|---|
| `class DecisionSourceContext:` | `src/contracts/execution_intent.py` | 597 | v4 said :605 — drifted -8 lines (Wave A landed) |
| `causality_status: str` | `src/contracts/snapshot_ingest_contract.py` | 96 | matches L12 |
| `def _f1_fallback_end_utc` | `src/strategy/market_phase.py` | 203 | matches L5 |
| `_f1_fallback_end_utc(` invocation | `src/strategy/market_phase.py` | 236 | write-site for `polymarket_end_anchor_source` |
| `confirmation_count=_extract_int` | `src/execution/fill_tracker.py` | 542 | PR 6 split site |
| `if rows and observed_members < 51` | `src/data/ecmwf_open_data.py` | 775 | `first_member_observed_time` capture site |
| `raw_orderbook_hash: str` | `src/contracts/executable_market_snapshot_v2.py` | 94 | READ-ONLY for B/36 (B/27 owns writes) |

**SCAFFOLD FINDING F1**: Field map rows 1-3 (`observation_time`, `provider_reported_time`, `observation_available_at`) are labelled "(existing)" but grep confirms ZERO occurrences in `DecisionSourceContext` and ZERO codebase occurrences of `provider_reported_time` / `observation_available_at`. These are genuinely NEW fields. PR 3 must ADD them to the dataclass, THEN add validators. The "(existing)" label refers to the conceptual domain (they live in `observation_instants_v2`), not the Python type.

---

## Current DecisionSourceContext — 12 existing fields

`src/contracts/execution_intent.py:597-700` (frozen dataclass):
```
source_id, model_family, forecast_issue_time, forecast_valid_time,
forecast_fetch_time, forecast_available_at, raw_payload_hash,
degradation_level, forecast_source_role, authority_tier,
decision_time, decision_time_status
```
Total after PR 3 (+ 4 new) = 16 fields.
Total after PR 6 (+ 8 more) = 24 fields.
Field map coordination check: 12 existing + 1 PR 3 new (`polymarket_end_anchor_source`) + 3 NEW observation fields (F1 finding) + 8 PR 6 new = 24.

---

## PR 3 — Commit 1 plan (file-by-file)

### 1. `src/contracts/execution_intent.py` (line ~597)

**Action**: Add 4 new fields to `DecisionSourceContext` dataclass after `decision_time_status`; add 3 ordering assertions in `integrity_errors()`.

**New fields to add** (all `str = ""`):
```python
# Observation timing chain (rows 1-3 in field map — NEW per F1 finding)
observation_time: str = ""           # UTC ISO; when observation instrument recorded
provider_reported_time: str = ""     # UTC ISO; timestamp as reported by weather provider
observation_available_at: str = ""   # UTC ISO; when Zeus first could access this observation
# Anchor source tag (row 17 in field map — NEW)
polymarket_end_anchor_source: str = ""  # "gamma_explicit" | "f1_12z_fallback"
```

**New integrity_errors() assertions** (after existing checks, using `_context_time()`):
```python
obs_time = _context_time(self.observation_time)
prov_time = _context_time(self.provider_reported_time)
obs_avail = _context_time(self.observation_available_at)
dec_time = parsed_times["decision_time"]   # already computed above

if obs_time and prov_time and obs_time > prov_time:
    errors.append("obs_after_provider")
if prov_time and obs_avail and prov_time > obs_avail:
    errors.append("provider_after_available")
if obs_avail and dec_time and obs_avail > dec_time:
    errors.append("available_after_decision")
```

**Update `from_forecast_context()`**: pass empty string defaults for new fields (no source in this factory path).

### 2. `src/contracts/snapshot_ingest_contract.py` (line 96)

**Action**: Replace `causality_status: str` field type with `CausalityStatus` Literal alias; declare the Literal type above the dataclass.

```python
# Insert before class SnapshotIngestDecision (line ~89):
from typing import Literal
CausalityStatus = Literal[
    "AVAILABLE_AFTER_DECISION",
    "DECISION_BEFORE_FORECAST_AVAILABLE",
    "EXCESSIVE_CLOCK_DRIFT",
    "INCLUSION_AFTER_FINALITY",
    "MISSING_CAUSALITY_FIELD",
    "OBS_AFTER_PROVIDER",
    "OK",
    "PROVIDER_AFTER_AVAILABLE",
    "SUBMIT_AFTER_ACK",
]

# Change field:
causality_status: CausalityStatus  # was: str
```

Note: Last 4 values (`EXCESSIVE_CLOCK_DRIFT`, `INCLUSION_AFTER_FINALITY`, `SUBMIT_AFTER_ACK`, + `OBS_AFTER_PROVIDER`/`PROVIDER_AFTER_AVAILABLE`) are PR-6-flagged; declared here in PR 3 so PR 6 uses without re-editing.

### 3. `src/strategy/market_phase.py` (line 236)

**Action**: At the `_f1_fallback_end_utc` invocation site, return a tuple `(polymarket_end_utc, anchor_source)` or pass a callback. The caller at line ~218-236 currently returns a `MarketPhase`; we need to thread `polymarket_end_anchor_source` through to the settlement_commands writer. Approach: return the anchor tag as a side-channel value or add it to the return dict.

**Concrete change**: modify `_resolve_market_phase_from_payload()` (or equivalent caller at :236) to compute:
```python
anchor_source = "f1_12z_fallback" if not end_str else "gamma_explicit"
polymarket_end_utc = (
    _parse_utc(end_str) if end_str else _f1_fallback_end_utc(target_local_date)
)
```
Then thread `anchor_source` to the settlement_commands INSERT (via a context dict or direct column write).

**Constraint**: Do not change the `_f1_fallback_end_utc` function signature — only the call site at :236.

### 4. `src/state/schema/v2_schema.py` or `src/execution/settlement_commands.py`

**Action**: Add migration ALTER ADD COLUMN:
```sql
ALTER TABLE settlement_commands ADD COLUMN polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit';
```
Backfill logic: rows where `tx_hash IS NOT NULL` or `market_end_at IS NOT NULL` get `gamma_explicit`; others get `f1_12z_fallback`. The migration script handles this.

**Migration script**: `scripts/migrate_settlement_commands_polymarket_anchor.py` (idempotent, dry-run flag).

### 5. New test file: `tests/test_decision_source_context_causality_pr3.py`

Relationship tests R-3.1, R-3.2, R-3.3 (written BEFORE implementation):
- R-3.1: `observation_time > provider_reported_time` → `integrity_errors()` contains `"obs_after_provider"`
- R-3.2: `provider_reported_time > observation_available_at` → contains `"provider_after_available"`
- R-3.3: `observation_available_at > decision_time` → contains `"available_after_decision"`
- R-3.4 (happy path): in-order timestamps → no causality errors emitted
- R-3.5: `causality_status` field accepts all 9 values of `CausalityStatus` Literal without type error

---

## PR 6 — Commit 2 plan (file-by-file)

### 1. `src/contracts/execution_intent.py` (line ~601, after PR 3 fields)

**Action**: Add 8 NEW fields to `DecisionSourceContext` (field map rows 5-8 validator upgrades + rows 9-16 new fields):

```python
# Forecast timing chain — validators only on existing str fields (rows 5-8)
# (no new fields; existing forecast_issue_time etc. already present)

# Ensemble run timing (rows 9-10) — NEW
first_member_observed_time: str = ""   # UTC ISO; first ENS member downloaded
run_complete_time: str = ""            # UTC ISO; all 51 members present

# Alpha proxy (row 11) — NEW
raw_orderbook_hash_transition_delta_ms: int | None = None  # ms since hash last changed

# Submission chain (rows 12-13) — NEW
zeus_submit_intent_time: str = ""   # UTC ISO; moment Zeus called executor.submit()
venue_ack_time: str = ""            # UTC ISO; Polymarket REST ack received

# Chain finality split (rows 14-15) — NEW
first_inclusion_block_time: str = ""    # UTC ISO; tx first seen in any block
finality_confirmed_time: str = ""       # UTC ISO; ≥6 confirmation watermark

# Clock drift (row 16) — NEW
clock_skew_estimate_ms: int | None = None   # host clock − venue Date: header (ms)
```

**New integrity_errors() assertions** (rows 5-8 forecast ordering + PR 6 orderings):
```python
# Rows 5-8: forecast timing validators
issue_t = _context_time(self.forecast_issue_time)
fetch_t = _context_time(self.forecast_fetch_time)
avail_t = _context_time(self.forecast_available_at)
# (existing checks for forecast_issue_after_decision etc. already present)

# Rows 14-15 chain finality
incl_t = _context_time(self.first_inclusion_block_time)
fin_t = _context_time(self.finality_confirmed_time)
if incl_t and fin_t and incl_t > fin_t:
    errors.append("inclusion_after_finality")

# Rows 12-13 submission
submit_t = _context_time(self.zeus_submit_intent_time)
ack_t = _context_time(self.venue_ack_time)
if submit_t and ack_t and submit_t > ack_t:
    errors.append("submit_after_ack")

# Row 16 clock drift
if self.clock_skew_estimate_ms is not None and abs(self.clock_skew_estimate_ms) > 100:
    errors.append("excessive_clock_drift")
```

### 2. `src/data/ecmwf_open_data.py` (line 775)

**Action**: Capture `first_member_observed_time` at the `observed_members < 51` flip point, and `run_complete_time` when `observed_members >= 51` (all members present).

```python
# At line 775, inside the partial-run detection block:
first_member_observed_time_iso = (
    min(row.get("source_available_at", "") for row in rows if row.get("source_available_at"))
    or ""
)
run_complete_time_iso = (
    "" if partial_run else
    (max(row.get("source_available_at", "") for row in rows if row.get("source_available_at")) or "")
)
```

### 3. `src/execution/fill_tracker.py` (line 542)

**Action**: Split `confirmation_count` write into `first_inclusion_block_time` + `finality_confirmed_time` watermarks. Keep `confirmation_count` (existing column, no schema break).

```python
# Derive from existing block_number + confirmation_count data in payload_dict
first_inclusion_block_time = (
    observed_at.isoformat() if _extract_int(payload_dict, "block_number", "blockNumber") else None
)
finality_confirmed_time = (
    observed_at.isoformat()
    if _extract_int(payload_dict, "confirmation_count", "confirmationCount", default=0) >= 6
    else None
)
```

### 4. `src/execution/executor.py` (line ~2402)

**Action**: Capture `zeus_submit_intent_time` before submit call; capture `venue_ack_time` from existing `ack_time` (already computed at line 2402). Thread both to settlement_commands INSERT.

### 5. `src/runtime/clock_skew_probe.py` (NEW module)

**Interface sketch**:
```python
# Lifecycle: periodic probe (60s interval suggested)
# Purpose: NTP-style skew estimate vs Polymarket REST Date: header
# Reuse: called by cycle_runtime or executor on each submit; result cached 60s

def probe_clock_skew(polymarket_base_url: str, timeout_s: float = 2.0) -> int | None:
    """Return estimated skew in ms (local − venue). None if probe fails.
    Uses HEAD /markets (cheap endpoint) and reads Date: response header.
    Caches result for CACHE_TTL_S to avoid per-order overhead."""

_CACHE: dict[str, tuple[float, int | None]] = {}  # url → (expires_ts, skew_ms)
CACHE_TTL_S = 60
```

**Dependencies**: stdlib `urllib.request` only (no new packages).

### 6. Storage migrations

#### `ensemble_snapshots_v2` (forecasts.db)
```sql
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN first_member_observed_time TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN run_complete_time TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN raw_orderbook_hash_transition_delta_ms INTEGER;
```
Script: `scripts/migrate_ensemble_snapshots_v2_alpha_proxy.py` (idempotent).

#### `settlement_commands` (world.db)
```sql
ALTER TABLE settlement_commands ADD COLUMN zeus_submit_intent_time TEXT;
ALTER TABLE settlement_commands ADD COLUMN venue_ack_time TEXT;
ALTER TABLE settlement_commands ADD COLUMN clock_skew_estimate_ms_at_submit INTEGER;
```
(Cross-DB note: `settlement_commands` lives in `world.db`, `ensemble_snapshots_v2` in `forecasts.db`. INV-37 ATTACH+SAVEPOINT only required when a SINGLE transaction spans both. These three columns are world.db-only; no INV-37 complexity. The `ensemble_snapshots_v2` migration is forecasts.db-only.)

#### `wrap_unwrap_commands` (world.db)
```sql
ALTER TABLE wrap_unwrap_commands ADD COLUMN first_inclusion_block_time TEXT;
ALTER TABLE wrap_unwrap_commands ADD COLUMN finality_confirmed_time TEXT;
```

### 7. New test files

**`tests/test_decision_source_context_pr6.py`** — relationship tests R-6.1 to R-6.3:
- R-6.1: `first_inclusion_block_time > finality_confirmed_time` → `"inclusion_after_finality"` in errors
- R-6.2: `zeus_submit_intent_time > venue_ack_time` → `"submit_after_ack"` in errors
- R-6.3: `abs(clock_skew_estimate_ms) > 100` → `"excessive_clock_drift"` in errors
- R-6.4: `raw_orderbook_hash_transition_delta_ms` non-null on every post-PR6 `ensemble_snapshots_v2` row (antibody probe via test DB fixture)

**`tests/test_inv_alpha_provenance.py`** — INV-alpha-provenance antibody:
- Every row inserted to `ensemble_snapshots_v2` via the writer path has non-null `raw_orderbook_hash_transition_delta_ms` (or writer raises `MissingAlphaProxyError`).

---

## CausalityStatus enum — 9 values (alphabetized)

```python
CausalityStatus = Literal[
    "AVAILABLE_AFTER_DECISION",       # PR 3 validator: obs_avail > decision_time
    "DECISION_BEFORE_FORECAST_AVAILABLE",  # existing, renamed from generic
    "EXCESSIVE_CLOCK_DRIFT",          # PR 6 validator: |clock_skew_ms| > 100
    "INCLUSION_AFTER_FINALITY",       # PR 6 validator: incl > finality
    "MISSING_CAUSALITY_FIELD",        # existing (line 122)
    "OBS_AFTER_PROVIDER",             # PR 3 validator: obs_time > provider_time
    "OK",                             # existing success path
    "PROVIDER_AFTER_AVAILABLE",       # PR 3 validator: provider_time > obs_avail
    "SUBMIT_AFTER_ACK",               # PR 6 validator: submit > ack
]
```

---

## Storage migration SQL — complete draft

### PR 3 migration (world.db, settlement_commands)
```sql
-- idempotent: ALTER TABLE ADD COLUMN succeeds silently if column exists (SQLite ≥3.37)
ALTER TABLE settlement_commands
  ADD COLUMN polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit';
-- Backfill: rows where end was not explicitly provided get f1_12z_fallback
-- (heuristic: if tx_hash IS NULL = pre-submission, use gamma_explicit default)
```

### PR 6 migrations
```sql
-- forecasts.db
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN first_member_observed_time TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN run_complete_time TEXT;
ALTER TABLE ensemble_snapshots_v2 ADD COLUMN raw_orderbook_hash_transition_delta_ms INTEGER;

-- world.db (settlement_commands)
ALTER TABLE settlement_commands ADD COLUMN zeus_submit_intent_time TEXT;
ALTER TABLE settlement_commands ADD COLUMN venue_ack_time TEXT;
ALTER TABLE settlement_commands ADD COLUMN clock_skew_estimate_ms_at_submit INTEGER;

-- world.db (wrap_unwrap_commands)
ALTER TABLE wrap_unwrap_commands ADD COLUMN first_inclusion_block_time TEXT;
ALTER TABLE wrap_unwrap_commands ADD COLUMN finality_confirmed_time TEXT;
```

---

## Non-collision with B/27

- `src/contracts/executable_market_snapshot_v2.py`: B/36 reads `raw_orderbook_hash` (line 94) to derive `raw_orderbook_hash_transition_delta_ms` in the monitor-refresh path. B/36 does NOT write to this file. B/27 owns all writes.
- The derivation site for `raw_orderbook_hash_transition_delta_ms` is in the consumer (monitor/evaluator) — not in `executable_market_snapshot_v2.py`.

---

## LOC estimate

| Component | Lines |
|---|---|
| `execution_intent.py` additions (fields + validators) | ~60 |
| `snapshot_ingest_contract.py` (enum + type change) | ~20 |
| `market_phase.py` (anchor tagging) | ~15 |
| `fill_tracker.py` (finality split) | ~25 |
| `ecmwf_open_data.py` (member timestamps) | ~20 |
| `executor.py` (submit intent + ack capture) | ~20 |
| `clock_skew_probe.py` (new module) | ~70 |
| Migration scripts × 3 | ~120 |
| Test files × 3 | ~200 |
| **Total production** | **~330** |
| **Total with tests** | **~530** |

**Well within 1500 LOC SCAFFOLD cap. Single-PR shipment confirmed.**

---

## ESCALATION / open items for SCAFFOLD critic

1. **F1 (field-map "(existing)" label)**: Rows 1-3 fields are genuinely new to `DecisionSourceContext`. Critic should confirm PR 3 must ADD these fields (not just add validators on non-existent fields).
2. **`observation_instants_v2` no-migration**: Field map lists `observation_instants_v2` as the storage table for rows 1-3 but those columns don't exist in the schema. PR 3 is only adding them to the Python type; no DB migration for `observation_instants_v2` is listed in the field map. Confirm: is a migration for `observation_instants_v2` out of scope for Phase 0?
3. **`run_complete_time` writer site**: The field map says `ecmwf_open_data.py` (all-members watermark) but the `observed_members >= 51` positive branch isn't a single flip-point in the current code — the negative branch at :775 is the PARTIAL gate. The complete-run watermark needs to be derived from `source_run_completeness == "COMPLETE"`. Confirm write site.
4. **`raw_orderbook_hash_transition_delta_ms` derivation**: The field map says "read site + new derivation in monitor_refresh" — but the monitor_refresh file is not listed in the file list. The derivation requires knowing the PREVIOUS hash. Where is the prior hash stored? Likely the snapshot cache in cycle_runtime. Critic to confirm derivation site before implementation.
