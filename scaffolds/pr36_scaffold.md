# PR 3 + PR 6 SCAFFOLD — DecisionSourceContext Coordinated Extension

**Branch**: `feat/phase0-pr36-decision-source-context-coordinated-20260519`
**Authored**: 2026-05-19 by Executor B/36 (v1); revised 2026-05-19 by Executor B/36 v2; Path F finalized 2026-05-19 by Executor B/36 v3
**Authority**: WAVE_B_PR_3_6_FIELD_MAP.md (field ownership table, 17 rows)
**Phase**: SCAFFOLD REVISION — all 3 blocking items resolved; production not yet started

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

## BLOCKING REVISION 1 — Field count correction (applied)

**Field map coordination count of 21 (used in v1 SCAFFOLD) was based on rows 1-3 being labelled "(existing)" in `DecisionSourceContext`. F1 finding corrects this: rows 1-3 are genuinely new dataclass fields — zero codebase occurrences of `provider_reported_time` and `observation_available_at` confirmed by grep. Post-merge `DecisionSourceContext` field count = 24.**

The "(existing)" label in the field map refers to the conceptual measurement domain (fields tracked in `observation_instants_v2`), NOT the Python dataclass. The field map table is authoritative for field ownership; the "(existing)" label is wrong for the Python type.

---

## Current DecisionSourceContext — 12 existing fields

`src/contracts/execution_intent.py:597-700` (frozen dataclass):
```
source_id, model_family, forecast_issue_time, forecast_valid_time,
forecast_fetch_time, forecast_available_at, raw_payload_hash,
degradation_level, forecast_source_role, authority_tier,
decision_time, decision_time_status
```
Total after PR 3 (+ 4 new fields: `observation_time`, `provider_reported_time`, `observation_available_at`, `polymarket_end_anchor_source`) = **16 fields**.
Total after PR 6 (+ 8 more new fields) = **24 fields**.
Field map coordination check: 12 existing + 4 PR 3 new + 8 PR 6 new = 24 (not 21).

---

## BLOCKING REVISION 2 — F4 prior-hash cache specification (applied)

**Problem**: `raw_orderbook_hash` in `cycle_runtime.py:950` is a one-shot audit value in a reprice function — not a persisted inter-cycle cache. There is no existing mechanism to compute `raw_orderbook_hash_transition_delta_ms` (requires knowing the PREVIOUS hash + timestamp). The v1 SCAFFOLD's hypothesis that the derivation could live in `cycle_runtime`'s "snapshot cache" was wrong.

**Verified location for the per-cycle refresh consumer path**: `src/engine/monitor_refresh.py` — specifically `monitor_quote_refresh()` (line 652) and `refresh_position()` (line 1320), which run per held-position per cycle. The hash comparison must happen at or near the orderbook snapshot acquisition point in this module.

**Specified cache design**:
```python
# src/engine/monitor_refresh.py — module-level, process-local dict
_prev_orderbook_hash_by_market: dict[str, tuple[str, float]] = {}
# key: market_condition_id (or token_id used as market key)
# value: (hash_str, captured_at_unix_ts)
```

**Lifecycle**:
- **Read**: on each per-cycle snapshot construction, compare `snapshot.raw_orderbook_hash` to `_prev_orderbook_hash_by_market.get(market_key)`. If hash differs from prior AND prior entry exists: `delta_ms = int((time.time() - prev_ts) * 1000)`.
- **Write**: after each hash observation (whether changed or not), update `_prev_orderbook_hash_by_market[market_key] = (current_hash, time.time())`.
- **Lifetime**: process-local. No persistence needed for Phase 0 instrument. Value is `None` for first observation per market (no prior to compare against).
- **Null semantics**: `raw_orderbook_hash_transition_delta_ms = None` when no prior hash exists for this market key (first observation in process lifetime).

**Location constraint**: B/36 adds this dict to `src/engine/monitor_refresh.py` only. B/27 owns `src/contracts/executable_market_snapshot_v2.py` — B/36 reads `snapshot.raw_orderbook_hash` as an attribute access, no edits to that file.

---

## BLOCKING REVISION 3 — Validator vacuousness / Path F (RESOLVED)

**Orchestrator decision (v3 final)**: **Path F — Optional `provider_reported_time` + conditional validators**.

**Why the prior paths were rejected**:
- **Path A** (vacuous validators with empty-string default): pollutes schema — reader cannot distinguish "deliberately empty" from "writer broke". Violates "every field has observable semantics" principle.
- **Path B-degraded / B-alt** (fabricate `provider_reported_time = observation_time`): `obs_after_provider` assertion becomes trivially always-true; instrument is permanently always-green regardless of real data quality. Silent fabrication is worse than no instrument.
- **Path F** (Optional + conditional): honest — `None` means "this source doesn't expose a separate provider-reported timestamp"; assertions fire only on real data; schema is future-compatible for institutional sources (NOAA, METAR) that DO expose separate observation/report times.

**Authority basis**: Code-over-docs rule (CLAUDE.md). `src/data/observation_client.py:324` is ground truth — `valid_time_gmt` is the only timestamp in WU API payload. No fabrication permitted.

---

### Path F specification

**Field declaration** (in `DecisionSourceContext`):
```python
provider_reported_time: Optional[str] = None   # None = source doesn't expose separate reported-at
```
NOT `str = ""`. The `Optional[str]` type is intentional — allows `None` to mean "not available" rather than "empty string / writer forgot to populate".

**Field count**: Still 24. `provider_reported_time` is a declared field (just Optional). 12 existing + 4 PR 3 new + 8 PR 6 new = 24. No change from BLOCKING REVISION 1.

**Conditional validators in `integrity_errors()`**:
```python
obs_time = _context_time(self.observation_time)
obs_avail = _context_time(self.observation_available_at)
dec_time = parsed_times["decision_time"]   # already computed above

# provider_reported_time is Optional — only assert when populated
if self.provider_reported_time is not None:
    prov_time = _context_time(self.provider_reported_time)
    if obs_time and prov_time and obs_time > prov_time:
        errors.append("obs_after_provider")
    if prov_time and obs_avail and prov_time > obs_avail:
        errors.append("provider_after_available")

# available_after_decision stays UNCONDITIONAL (observation_available_at is mandatory)
if obs_avail and dec_time and obs_avail > dec_time:
    errors.append("available_after_decision")
```

When `provider_reported_time is None`: the two conditional assertions skip silently — no fabrication, no fake-passing, no vacuous always-green.

**Writer wire-up** (Path F):

`src/data/observation_client.py` (WU branch — at `DecisionSourceContext` factory or at the `get_current_observation()` write-back site):
```python
observation_time = valid_time_gmt_iso   # from obs.get("valid_time_gmt")
provider_reported_time = None           # WU API has no separate reported-at field
observation_available_at = datetime.now(timezone.utc).isoformat()  # harvester write-back time
```

`src/data/observation_client.py` (IEM ASOS branch — `_fetch_iem_asos`):
- Grep `_fetch_iem_asos` for any candidate "reported-at" field in ASOS payload. If none (expected): `provider_reported_time = None`.
- `observation_available_at = datetime.now(timezone.utc).isoformat()` regardless.

`observation_available_at` is **MANDATORY** (not Optional). The gap `observation_available_at − observation_time` is a real, meaningful harvester-latency measurement even without provider-reported time.

**Semantic note on `observation_available_at`**: This is the moment Zeus's harvester wrote back the observation, i.e., when it became available to downstream consumers. It is NOT the "observation valid time" (`observation_time`). The delta captures real harvesting lag.

**Tests** — conditional branch coverage required:

In `tests/test_decision_source_context_causality_pr3.py`:
- R-3.1 (conditional): `provider_reported_time = "2026-01-01T10:00:00Z"`, `observation_time = "2026-01-01T10:01:00Z"` → `"obs_after_provider"` in errors
- R-3.2 (conditional): `provider_reported_time = "2026-01-01T10:02:00Z"`, `observation_available_at = "2026-01-01T10:01:00Z"` → `"provider_after_available"` in errors
- R-3.3 (unconditional): `observation_available_at > decision_time` → `"available_after_decision"` in errors (unchanged)
- **R-3.NEW-a**: `provider_reported_time = None` → `"obs_after_provider"` NOT in errors regardless of `observation_time` value
- **R-3.NEW-b**: `provider_reported_time = None` → `"provider_after_available"` NOT in errors regardless of `observation_available_at` value
- R-3.4 (happy path): in-order timestamps with real `provider_reported_time` → no causality errors
- R-3.5: `CausalityStatus` accepts all 9 Literal values without type error

PR 6's 3 validators (`inclusion_after_finality`, `submit_after_ack`, `excessive_clock_drift`) are unconditional and unaffected by Path F.

---

## PR 3 — Commit 1 plan (file-by-file)

### 1. `src/contracts/execution_intent.py` (line ~597)

**Action**: Add 4 new fields to `DecisionSourceContext` dataclass after `decision_time_status`; add 3 ordering assertions in `integrity_errors()`.

**New fields to add** (Path F: `provider_reported_time` is `Optional[str]`, others `str = ""`):
```python
# Observation timing chain (rows 1-3 in field map — NEW per F1 finding)
observation_time: str = ""                  # UTC ISO; when observation instrument recorded
provider_reported_time: Optional[str] = None  # UTC ISO; provider's stated reported-at, if exposed
observation_available_at: str = ""          # UTC ISO; harvester write-back time (MANDATORY)
# Anchor source tag (row 17 in field map — NEW)
polymarket_end_anchor_source: str = ""      # "gamma_explicit" | "f1_12z_fallback"
```

`Optional` import: add `from typing import Optional` if not already present in `execution_intent.py`.

**New integrity_errors() assertions** (after existing checks, using `_context_time()`):
```python
obs_time = _context_time(self.observation_time)
obs_avail = _context_time(self.observation_available_at)
dec_time = parsed_times["decision_time"]   # already computed above

# provider_reported_time is Optional — only assert when populated (Path F)
if self.provider_reported_time is not None:
    prov_time = _context_time(self.provider_reported_time)
    if obs_time and prov_time and obs_time > prov_time:
        errors.append("obs_after_provider")
    if prov_time and obs_avail and prov_time > obs_avail:
        errors.append("provider_after_available")

# available_after_decision is UNCONDITIONAL — observation_available_at is mandatory
if obs_avail and dec_time and obs_avail > dec_time:
    errors.append("available_after_decision")
```

**Update `from_forecast_context()`**: pass `observation_time=""`, `provider_reported_time=None`, `observation_available_at=""`, `polymarket_end_anchor_source=""` for the new fields (no source in this factory path).

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

**Header block** (per `architecture/naming_conventions.yaml`):
```python
# Lifecycle: PR 3 relationship tests for DecisionSourceContext causality ordering
# Purpose: Verify observation-chain ordering assertions (obs ≤ provider ≤ available ≤ decision)
# Reuse: relationship tests; do NOT import from test_decision_source_context_pr6.py
# Created: 2026-05-19
```

Relationship tests R-3.1 to R-3.5 + conditional None branches (written BEFORE implementation):
- R-3.1: `provider_reported_time = "...T10:01Z"`, `observation_time = "...T10:02Z"` (obs after provider) → `integrity_errors()` contains `"obs_after_provider"`
- R-3.2: `provider_reported_time = "...T10:02Z"`, `observation_available_at = "...T10:01Z"` (provider after avail) → contains `"provider_after_available"`
- R-3.3: `observation_available_at > decision_time` → contains `"available_after_decision"` (UNCONDITIONAL — no `provider_reported_time` dependency)
- R-3.4 (happy path): in-order timestamps with real `provider_reported_time` → no causality errors emitted
- R-3.5: `causality_status` field accepts all 9 values of `CausalityStatus` Literal without type error
- **R-3.NEW-a** (Path F conditional): `provider_reported_time = None`, `observation_time` set to future (would trigger obs_after_provider if `provider_reported_time` were populated) → `"obs_after_provider"` NOT in errors
- **R-3.NEW-b** (Path F conditional): `provider_reported_time = None`, `observation_available_at` set to past (would trigger provider_after_available if populated) → `"provider_after_available"` NOT in errors
- **R-3.NEW-c** (Path F mandatory): `observation_available_at` populated even when `provider_reported_time = None`, and `available_after_decision` still fires when ordering violated

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

**`tests/test_decision_source_context_pr6.py`**

Header block:
```python
# Lifecycle: PR 6 relationship tests for DecisionSourceContext timing chain
# Purpose: Verify chain-finality split, submit/ack ordering, clock drift threshold
# Reuse: relationship tests only; alpha-provenance antibody lives in test_inv_alpha_provenance.py
# Created: 2026-05-19
```

Relationship tests R-6.1 to R-6.4:
- R-6.1: `first_inclusion_block_time > finality_confirmed_time` → `"inclusion_after_finality"` in errors
- R-6.2: `zeus_submit_intent_time > venue_ack_time` → `"submit_after_ack"` in errors
- R-6.3: `abs(clock_skew_estimate_ms) > 100` → `"excessive_clock_drift"` in errors
- R-6.4: `raw_orderbook_hash_transition_delta_ms` non-null on every post-PR6 `ensemble_snapshots_v2` row (antibody probe via test DB fixture)

**`tests/test_inv_alpha_provenance.py`**

Header block:
```python
# Lifecycle: INV-alpha-provenance antibody test
# Purpose: Every ensemble_snapshots_v2 write via PR6 path has non-null raw_orderbook_hash_transition_delta_ms
# Reuse: standalone; no shared fixtures with timing-chain tests
# Created: 2026-05-19
```

Content:
- Every row inserted to `ensemble_snapshots_v2` via the writer path has non-null `raw_orderbook_hash_transition_delta_ms` (or writer raises `MissingAlphaProxyError`).

### test_topology.yaml registration (non-blocking rec #5 — mandatory at production commit)

Add to `tests/test_topology.yaml` in the production commit:
```yaml
- path: tests/test_decision_source_context_causality_pr3.py
  kind: relationship
  created: 2026-05-19
  pr: PR3
- path: tests/test_decision_source_context_pr6.py
  kind: relationship
  created: 2026-05-19
  pr: PR6
- path: tests/test_inv_alpha_provenance.py
  kind: antibody
  created: 2026-05-19
  pr: PR6
```

### db_table_ownership.yaml column inventory (non-blocking rec #6 — mandatory at production commit)

Add 8 new columns across 3 tables to `architecture/db_table_ownership.yaml`:
- `ensemble_snapshots_v2`: `first_member_observed_time`, `run_complete_time`, `raw_orderbook_hash_transition_delta_ms`
- `settlement_commands`: `polymarket_end_anchor_source`, `zeus_submit_intent_time`, `venue_ack_time`, `clock_skew_estimate_ms_at_submit`
- `wrap_unwrap_commands`: `first_inclusion_block_time`, `finality_confirmed_time`

### INDEX.md entry (non-blocking rec #7 — mandatory at production commit)

Add to `docs/operations/INDEX.md`:
```
src/runtime/clock_skew_probe.py — NTP-style clock skew probe vs Polymarket REST Date: header.
  Caches result 60s. Returns int ms (local − venue) or None on probe failure. PR 6.
```

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

## Error-string naming convention (non-blocking rec #1)

The existing `integrity_errors()` uses **long-form** for forecast validators: `"forecast_available_after_decision"`, `"forecast_issue_after_fetch_time"`, etc. The new PR 3/6 validators MUST match this convention:

- Use: `"obs_after_provider"`, `"provider_after_available"`, `"available_after_decision"` (short-form already in v1 SCAFFOLD)
- Rationale for keeping both: existing long-form strings are stored in downstream DB columns and checked in tests — renaming them is a breaking change. New observation-chain validators use short-form because they are in a new namespace (observation chain, not forecast chain). Add a comment at top of `integrity_errors()`:
  ```python
  # Error-string conventions:
  # - Forecast-chain violations use long-form: "forecast_<what>_after_<when>"
  # - Observation/causality violations use short-form: "<subject>_after_<ref>"
  # The difference is intentional — do not normalize without a migration.
  ```

## CausalityStatus ↔ integrity_errors() mapping (non-blocking rec #2)

Add to `src/contracts/snapshot_ingest_contract.py` alongside `CausalityStatus`:
```python
# Explicit mapping: integrity_errors() short-form string → CausalityStatus value.
# Prevents the two namespaces from drifting independently.
INTEGRITY_ERROR_TO_CAUSALITY: dict[str, str] = {
    "available_after_decision":        "AVAILABLE_AFTER_DECISION",
    "forecast_available_after_decision": "DECISION_BEFORE_FORECAST_AVAILABLE",
    "excessive_clock_drift":           "EXCESSIVE_CLOCK_DRIFT",
    "inclusion_after_finality":        "INCLUSION_AFTER_FINALITY",
    "obs_after_provider":              "OBS_AFTER_PROVIDER",
    "provider_after_available":        "PROVIDER_AFTER_AVAILABLE",
    "submit_after_ack":                "SUBMIT_AFTER_ACK",
    # "missing_*" fields → "MISSING_CAUSALITY_FIELD" (handled by prefix check in consumer)
}
```

---

## Storage migration SQL — complete draft

### PR 3 migration (world.db, settlement_commands)
```sql
-- idempotent: ALTER TABLE ADD COLUMN succeeds silently if column exists (SQLite ≥3.37)
ALTER TABLE settlement_commands
  ADD COLUMN polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'gamma_explicit';
-- Backfill: market_end_at IS NOT NULL → 'gamma_explicit' (explicit Gamma API end provided)
--           market_end_at IS NULL     → 'f1_12z_fallback' (F1 derived anchor was used)
-- NOTE: tx_hash IS NULL heuristic from v1 was WRONG — tx_hash tracks submission state,
-- not whether the market endDate was explicit. market_end_at column is the correct discriminator.
-- Verify market_end_at column exists in settlement_commands before running backfill.
UPDATE settlement_commands
  SET polymarket_end_anchor_source = CASE
    WHEN market_end_at IS NOT NULL THEN 'gamma_explicit'
    ELSE 'f1_12z_fallback'
  END;
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

## LOC estimate (revised v3 — Path F final)

| Component | Lines |
|---|---|
| `execution_intent.py` additions (fields + validators, Path F Optional) | ~65 |
| `snapshot_ingest_contract.py` (enum + type change + mapping dict) | ~30 |
| `market_phase.py` (anchor tagging) | ~15 |
| `fill_tracker.py` (finality split) | ~25 |
| `ecmwf_open_data.py` (member timestamps) | ~20 |
| `executor.py` (submit intent + ack capture) | ~20 |
| `clock_skew_probe.py` (new module) | ~70 |
| `monitor_refresh.py` (prior-hash cache + delta derivation) | ~30 |
| `observation_client.py` (Path F writer: `observation_available_at = now()`, `provider_reported_time = None`) | ~30 |
| Migration scripts × 3 (realistic: 70 LOC each) | ~210 |
| Test files × 3 (incl. R-3.NEW-a/b conditional None branches) | ~270 |
| **Total production** | **~515** |
| **Total with tests** | **~785** |

**Path F note**: No fabrication writer needed (`provider_reported_time = None` for WU/IEM is a 2-line assignment, not a derived field). Smaller scope than B-degraded. `observation_available_at` write adds ~15 LOC real value (harvester latency measurement). Well within 1500 LOC SCAFFOLD cap.

---

## ESCALATION / open items (v3 revision — all blocking items resolved)

1. **F1 (field-map "(existing)" label)**: RESOLVED. Rows 1-3 are genuinely new fields (confirmed by grep + BLOCKING REVISION 1 above).
2. **`observation_instants_v2` no-migration**: RESOLVED. Critic approved deferral to Phase 1. Out of scope for Phase 0.
3. **`run_complete_time` writer site**: RESOLVED per APPROVED items from critic. Write site = `source_run_completeness == "COMPLETE"` branch in `_fetch_ecmwf_run_data()` result-building block (line ~818), NOT the `< 51` negative guard.
4. **`raw_orderbook_hash_transition_delta_ms` derivation**: RESOLVED in BLOCKING REVISION 2. Cache dict in `src/engine/monitor_refresh.py` (module-level, process-local).
5. **Path B writer feasibility → Path F**: RESOLVED. Orchestrator decided Path F (Optional + conditional validators). `provider_reported_time: Optional[str] = None`; WU+IEM writers set `None` (no fabrication); conditional validators skip silently when None. Full spec in BLOCKING REVISION 3 above. No new ESCALATIONs.
6. **Backfill heuristic correction**: RESOLVED. `tx_hash IS NULL` heuristic was inverted in v1 SCAFFOLD. Correct discriminator = `market_end_at IS NOT NULL → 'gamma_explicit'`. Fixed in Storage migration SQL section above.

**All 3 BLOCKING items resolved. No open ESCALATIONs. Ready for Wave-B opus critic review before production.**
