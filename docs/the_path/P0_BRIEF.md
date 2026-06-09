# P0 Executable Brief — Bias/Resolution Bridge + EMOS Product Axis

> Created: 2026-06-07
> Authority basis: PR_SPEC.md §3 P0 + §0 process-memory; Design 1 (reuse-audit), Design 2 (emos-key), Design 3 (measure).
> Worktree: /Users/leofitz/zeus-thepath-audit (read-only mirror of main; changes execute in a worktree off `thepath/audit-realign`).
> This file is the ONLY artifact P0 produces before code changes begin. No live paths touched here.

---

## §1 REUSE VERDICTS + THE LIVE-0.25-BRIDGED ANSWER

### 1.1 What is live serving today?

**VERDICT: Live 0.25° (ecmwf_open_data) is RAW-REUSED — the bridge code is built-and-orphaned, NOT serving.**

Evidence chain (static trace, must be re-probed on the live DB — see §4):
- `write_promoted_edli_bias.py:84-92`: writes `model_bias_ens` with `error_model_family='edli_per_city_v1'`, `n_prior=0`, `weight_live=1.0` (OpenData-only mean; no TIGGE prior mixed).
- `write_d7_rolling_edli_bias.py:378-379`: writes same family, `prior_data_version=None`.
- The live EDLI reactor (`event_reactor_adapter.py:6461-6552 _maybe_apply_edli_bias_correction`) reads family `edli_per_city_v1` — the RAW-REUSED rows.
- `transport_bias_prior` (ens_bias_model.py:295) and the full `BiasCandidate` gate are invoked only by `ens_error_model.fit_error_model_from_db`, which writes family `full_transport_v1`.
- `full_transport_live_enabled=false` (settings.json:280). Config note (settings.json:283) confirms: "the exit FT route is permanently dead (0-row family)".

Consequence: VERIFY#9 (no un-bridged coarse prior leaks) is SATISFIED TODAY — the bridge does not run. This is safe. The bridge being orphaned is a latent wiring risk, not a live bug. The P0 fix is a structural guard against a FUTURE accidental rewire, not a live-bug patch.

### 1.2 Reuse verdicts

| Component | Verdict | Basis |
|---|---|---|
| `transport_bias_prior` (ens_bias_model.py:295-325) | **CURRENT_REUSABLE** | Header dated 2026-05-24/audited 2026-05-24, math consistent with PR_SPEC §0.7 ~80%-built claim. |
| `BiasCandidate` + accept-gate (ens_bias_model.py:334-391) | **CURRENT_REUSABLE** | evidence_product gate is correct today. Needs `resolution` field added (P0 additive). |
| `score_error_model_candidates.py:107-150` | **CURRENT_REUSABLE** | Gate logic sound. Needs (product AND resolution) keying (P0 additive, offline-only). |
| EMOS 3-key cell infrastructure (`emos.py:249`, `emos_q_builder.py`) | **CURRENT_REUSABLE** | Clean function; needs a 4th optional axis + resolver (P0 additive). |
| `ens_bias_repo.py` PRAGMA-guarded extension pattern | **CURRENT_REUSABLE** | Pattern already established (lines 165, 189, 223, 543, 699). `grid_resolution` column follows same pattern. |
| The Path's proposed `resolution_bridges` / `bias_substrate` tables | **DEAD_DELETE (never build)** | One-builder violation: duplicates `model_bias_ens`. Resolution belongs on the existing table as an additive-nullable column. |
| `replacement_emos_cell_key` (7-key, replacement_forecast_emos_identity.py) | **CURRENT_REUSABLE as audit/provenance identity** | NOT a serving namespace. Needs a serving-key adapter that collapses to the 4-key EMOS axis. |

### 1.3 The live-0.25-bridged answer

Live 0.25° is **not** bridged. It is raw-reused (OpenData-only mean). The P0 work does NOT change what is served to the 138 open positions. The goal of P0 is:
1. Make resolution-mismatch **unconstructable** in the offline gate (not just avoidable by convention).
2. Add a product axis to EMOS so a second NWP product cannot silently collide with the incumbent 3-key cells.
3. Measure whether keyed serving (when it eventually exists) is >= current on settled truth.

---

## §2 ORDERED CHANGE-LIST WITH BACKWARD-COMPAT PROOFS

Build order is fixed. Each change is additive; none changes live serving behavior with flags at current defaults.

### Change 0 — Re-probe the live DB BEFORE any code change (blocking prerequisite)

This is not a code change. It is an evidence gate required before Change 1 is meaningful.

**Action**: Run these two queries against `state/zeus-world.db` (mode=ro, immutable=1):
```sql
-- Q1: Confirm edli_per_city_v1 is OpenData-only (n_prior=0, weight_live=1.0)
SELECT city, season, month, live_data_version, n_prior, weight_live, error_model_family, estimator
FROM model_bias_ens
WHERE error_model_family = 'edli_per_city_v1' AND authority = 'VERIFIED'
LIMIT 20;

-- Q2: Confirm full_transport_v1 is a 0-row dead family
SELECT COUNT(*) as cnt FROM model_bias_ens WHERE error_model_family = 'full_transport_v1';
```

**EXPECT**: Q1 rows have `n_prior=0`, `weight_live=1.0`. Q2 count = 0.
**If Q2 count > 0**: the bridge IS partially wired and the QUARANTINED verdict becomes a live bug. Escalate to operator before proceeding with any code change.

### Change 1 — Add `resolution: str` field to `BiasCandidate` (ens_bias_model.py:334-348)

**File**: `src/calibration/ens_bias_model.py`

**What**: Add `resolution: str = ""` to the `BiasCandidate` frozen dataclass (after the existing `n: int` field). Update `build_candidate_biases` to accept a `target_resolution: str = ""` kwarg and stamp it on the `raw` and `opendata_bias` candidates; stamp `resolution=""` on the `tigge_prior` candidate (its resolution is coarse and intentionally left for the gate to check, not self-declared).

**Why**: Today the only product-discriminator is `evidence_product` (a free string). A 0.5-grid OpenData string can share a product token with a 0.25-grid OpenData target if naming drifts. Adding `resolution` makes the gate key `(evidence_product, resolution)` so a coarse prior cannot serve a finer product even if product tokens collide.

**Backward-compat proof**: Default `resolution=""` keeps all existing `BiasCandidate` constructions valid. The only live consumer is the OFFLINE scoring script — it does NOT touch the 138 open positions or `model_bias_ens` serving rows. No DB schema change. Byte-identical to live serving (which never constructs `BiasCandidate`). Existing tests pass with the default.

### Change 2 — Tighten the accept-gate to (product AND resolution) keying (scripts/score_error_model_candidates.py:107-120)

**File**: `scripts/score_error_model_candidates.py`

**What**: At the gate line 118, add a resolution match alongside the product check:
```python
# BEFORE:
if candidate_products.get(name) != target_product:
    ...
# AFTER:
product_ok = candidate_products.get(name) == target_product
resolution_ok = (candidate_resolutions.get(name, "") == "" or
                 candidate_resolutions.get(name) == target_resolution)
if not (product_ok and resolution_ok):
    ...
```
Thread `target_resolution: str = ""` through `run_scoring(472)` → `score_bucket` → the gate. Populate `candidate_resolutions` map alongside `candidate_products` (lines 380-381) from `cand.resolution`. When `target_resolution=""` (legacy invocation), the resolution check passes trivially (fall-through to product-only, current behavior).

**Why**: This is the structural realization of PR_SPEC §0.9 (σ widen-only; resolution mismatch must be TYPED BLOCKED). Applying a 0.5 prior to a 0.25 target becomes unconstructable in the gate, not merely avoidable by naming convention.

**Backward-compat proof**: Offline-only script. No live path, no open-position impact. `target_resolution=""` default preserves current behavior exactly. No DB change.

### Change 3 — Add `grid_resolution TEXT` column to `model_bias_ens` (RECOMMENDATION, not forced)

**File**: `src/calibration/ens_bias_repo.py` (DDL ~44-65, `write_bias_model`, `read_bias_model:614-694`)

**What**: Following the established PRAGMA-guarded extension pattern (already used at lines 165, 189, 223, 543, 699):
- Add `grid_resolution TEXT` as a PRAGMA-guarded additive-nullable ALTER in the DDL boot.
- In `write_bias_model`: derive `grid_resolution` FROM `live_data_version` (single source of truth — never accept it as a free independent param; a mapping dict from known live_data_version tokens to resolution strings).
- In `read_bias_model`: if the caller passes `grid_resolution` AND the column exists (PRAGMA guard), add it to the WHERE clause. When column absent or caller omits it, behavior is byte-identical to today.

**Why**: This makes resolution authoritative on the SERVING path (not just the offline gate). It is the one-builder-compliant home for The Path's `resolution_bridges` concept. The new tables must NOT be created.

**Backward-compat proof**: `grid_resolution NULL` on the 71 existing `edli_per_city_v1` VERIFIED rows. `read_bias_model` filters on it ONLY when caller passes it AND column exists (mirrors existing `error_model_family` guard at 696-702). With column absent or NULL: byte-identical. The 138 open positions resolve byte-identically. **This guarantee is the acceptance condition: if it cannot be met, this change is deferred.**

**RISK — derive-from-live_data_version is mandatory**: If a producer stamps `grid_resolution` independently of `live_data_version`, a mismatch creates a 0-match where one matched before, silently dropping bias correction. Derive it, never accept it as a free param.

### Change 4 — Add `product_id` axis to EMOS key builder + resolver (src/calibration/emos.py:249-362)

**File**: `src/calibration/emos.py`

**What**: Near line 36, add:
```python
INCUMBENT_PRODUCT_ID: str = "ecmwf_open_data"  # dominant live source_id; see OPEN QUESTION #1
```

Rewrite `emos_cell_key` (line 249) to:
```python
def emos_cell_key(city: str, season: str, metric: str, product_id: str | None = None) -> str:
    base = f"{city}|{season}|{str(metric).lower()}"
    if product_id is None or product_id == INCUMBENT_PRODUCT_ID:
        return base  # legacy 3-key — byte-identical for the incumbent
    return f"{base}|{product_id}"
```

Add a resolver (new function, no call-site changes required for existing callers):
```python
def resolve_emos_cell(
    cells: dict, city: str, season: str, metric: str, product_id: str | None = None
) -> tuple[dict | None, str]:
    k4 = emos_cell_key(city, season, metric, product_id)
    cell = cells.get(k4)
    if cell is not None:
        return cell, k4
    if product_id is not None and product_id != INCUMBENT_PRODUCT_ID:
        return None, k4  # non-incumbent NEVER falls back to legacy — no collision possible
    k3 = emos_cell_key(city, season, metric)  # incumbent fallback
    return cells.get(k3), k3
```

**Why**: This is the F1 HIGH fix. A second NWP product cannot collide with the incumbent 3-key cells by construction. Non-incumbent products that lack their own 4-key cell resolve to `None` (honest-raw fallback), never the incumbent cell.

**Backward-compat proof**: Every existing caller omits `product_id` → key string is the unchanged 3-key `"City|SEASON|metric"`. No 4-key cells exist today, so for the incumbent `k4 == k3` and the single `.get` hits the same cell. The default-argument signature is additive (keyword-only compatible). No positional caller breaks. The 400 existing cells remain addressed identically.

**OPEN QUESTION #1 BLOCKS this change**: `INCUMBENT_PRODUCT_ID` value choice (see §5).

### Change 5 — Thread `product_id` through serving accessors (emos.py:258-362, emos_q_builder.py)

**Files**: `src/calibration/emos.py` (emos_predictive, emos_sigma_model, settlement_sigma_floor), `src/calibration/emos_q_builder.py:33-181`

**What**: Add optional `product_id: str | None = None` kwarg to `emos_predictive`, `emos_sigma_model`, `settlement_sigma_floor`, `build_emos_q`, `build_honest_raw_q`. Replace inline key builds + `.get(key)` with `resolve_emos_cell(cells, city, season, metric, product_id)`. Forward `product_id` through `emos_q_builder.py` to the accessor calls.

**Backward-compat proof**: Default `product_id=None` → resolver returns the legacy 3-key cell exactly as before. No serving caller passes `product_id` yet, so runtime behavior is identical. Existing call sites in `event_reactor_adapter.py:5815,5875` and `monitor_refresh.py:191,228` are unchanged.

### Change 6 — Fix the raw f-string bypass in event_reactor_adapter.py:6925

**File**: `src/engine/event_reactor_adapter.py:6925`

**What**: The shadow-ledger served-status probe reads `tbl.get('cells', {}).get(f"{family.city}|{season}|high")` directly. Replace with `resolve_emos_cell(tbl.get('cells', {}), family.city, season, 'high')[0]`. Pure diagnostic read; not in the trade-decision path.

**Backward-compat proof**: With no `product_id` argument, resolver yields the identical 3-key lookup. Byte-identical served_status.

### Change 7 — Fit script gains `--product-id` arg (scripts/fit_emos_calibration.py)

**File**: `scripts/fit_emos_calibration.py:58-77` (argparse), `102-107` (snapshot query), `171-184` (write)

**What**: Add `ap.add_argument('--product-id', default=INCUMBENT_PRODUCT_ID)`. When `args.product_id != INCUMBENT_PRODUCT_ID`, AND snapshots have a product column (see OPEN QUESTION #2), filter `AND source_id = ?` in the SELECT. Write uses the key law: `emos_cell_key(city, season, metric, args.product_id)` — which collapses to 3-key for the incumbent. Add `_meta['product_id'] = args.product_id`.

**GUARD**: Fit-time guard REFUSES to write a 4-key cell whose `product_id == INCUMBENT_PRODUCT_ID` (make the divergent state where both 3-key and 4-key incumbent cells coexist unconstructable).

**Backward-compat proof**: Default run (no `--product-id` or `=INCUMBENT_PRODUCT_ID`) writes the EXACT legacy 3-key cell strings and the EXACT existing unfiltered query. Re-fitting/re-promoting produces a diff-free table. Only an explicit non-incumbent `--product-id` changes keying, writing additive 4-key cells alongside (never overwriting) the incumbent 3-key cells.

**OPEN QUESTION #2 BLOCKS the non-incumbent filter**: product column vocabulary in `ensemble_snapshots` (see §5).

### Change 8 — Replacement serving-key adapter (src/data/replacement_forecast_emos_identity.py)

**File**: `src/data/replacement_forecast_emos_identity.py` + `src/data/replacement_forecast_refit_handoff.py`

**What**: No behavioral change to the 7-key `replacement_emos_cell_key` (it is the full-lineage refit-evidence identity, not a serving address). ADD an adapter:
```python
serving_key = emos_cell_key(city, season, metric, product_id=evidence.product_id)
```
This closes the one-truth gap: the 7-key verifier audits provenance; the 4-key serving key is the address in the ONE shared table. Same `product_id` axis, two granularities.

**Backward-compat proof**: Replacement path is flag-gated and never touched the live 3-key serving. The replacement `product_id` is distinct from `INCUMBENT_PRODUCT_ID`, so replacement cells are 4-key and cannot shadow incumbent 3-key cells.

### Change 9 — One-builder CI guard (no new table names, ever)

**File**: New test (e.g., `tests/test_schema_no_duplicate_tables.py` or a CI grep)

**What**: A pytest / CI check asserting NO table named `resolution_bridges` or `bias_substrate` exists in `src/state/schema/`. Makes The Path's duplicate-table proposal unconstructable.

**Backward-compat proof**: Additive test; no live behavior.

---

## §3 ANTIBODY TESTS — WRITE FIRST (TDD, before any implementation)

Write these tests RED before implementing Changes 1-9. Each test must FAIL on the current codebase before implementation and PASS after. File locations are recommendations; keep with nearest existing test file.

### AT-1: RELATIONSHIP TEST — resolution mismatch is TYPED REFUSED in the offline gate
**File**: `tests/test_ens_bias_candidates.py` (new test)
**Assertion**:
```python
# Build a candidate with evidence for resolution 0.5
cand = BiasCandidate(name="tigge_prior", bias=0.3, evidence_product="opendata_test", resolution="0.5", n=100)
# Gate must REFUSE it for target resolution 0.25 even when product tokens match
result = accept_gate({"tigge_prior": cand}, target_product="opendata_test", target_resolution="0.25")
assert result is None or result.name != "tigge_prior"
```
This test CANNOT BE WRITTEN TODAY because `BiasCandidate` has no `resolution` field — which is itself the finding (PR_SPEC §0: "if you cannot express the cross-module invariant as a pytest assertion you do not understand the relationship yet").

### AT-2: RELATIONSHIP TEST — non-incumbent product cannot resolve incumbent cell
**File**: `tests/test_emos_key.py` (new file)
**Assertion**:
```python
cells = {"Amsterdam|DJF|high": {"params": [1,2,3,4,5], "n": 1420, "served": "emos"}}
cell, key = resolve_emos_cell(cells, "Amsterdam", "DJF", "high", product_id="openmeteo_v1")
assert cell is None  # non-incumbent NEVER gets the incumbent cell
assert key == "Amsterdam|DJF|high|openmeteo_v1"  # 4-key address returned
```
This is the F1 collision-impossible guarantee. Currently constructable as a bug (no product axis exists); after Change 4 it becomes unconstructable.

### AT-3: BACKWARD-COMPAT BYTE-IDENTITY — incumbent key is always the legacy 3-key
**File**: `tests/test_emos_key.py`
```python
assert emos_cell_key("Amsterdam", "DJF", "high") == "Amsterdam|DJF|high"
assert emos_cell_key("Amsterdam", "DJF", "high", product_id=None) == "Amsterdam|DJF|high"
assert emos_cell_key("Amsterdam", "DJF", "high", product_id=INCUMBENT_PRODUCT_ID) == "Amsterdam|DJF|high"
# All three are string-equal — a future refactor that appends "|ecmwf_open_data" fails CI
```

### AT-4: LIVE TABLE RESOLVES UNCHANGED — parametrized over all 400 cells
**File**: `tests/test_emos_key.py`
```python
import json, pathlib
cells = json.loads(pathlib.Path("state/emos_calibration.json").read_text())["cells"]
for key in cells:
    city, season, metric = key.split("|")
    cell_none, k_none = resolve_emos_cell(cells, city, season, metric, product_id=None)
    cell_inc, k_inc = resolve_emos_cell(cells, city, season, metric, product_id=INCUMBENT_PRODUCT_ID)
    assert cell_none is cells[key]
    assert cell_inc is cells[key]
    assert k_none == key
    assert k_inc == key
```
Guards all 138 open positions: their q is recomputed from the identical cell.

### AT-5: BIAS RESOLUTION — read_bias_model fail-closed on resolution mismatch
**File**: `tests/test_ens_bias_repo.py` (extend)
```python
# A row with grid_resolution='0.5' must NOT be returned for a caller requesting '0.25'
# (when the column exists)
row = read_bias_model(conn, ..., grid_resolution="0.25")
assert row is None  # only a '0.5' row exists in the fixture
```
And:
```python
# When column absent (legacy DB), behavior is byte-identical to today
row_legacy = read_bias_model(conn_no_column, ...)
assert row_legacy == expected_legacy_row
```

### AT-6: DEAD-FAMILY INVARIANT — full_transport_v1 armed-but-zero is detectable
**File**: `tests/test_bias_family_state.py` (new)
```python
# If full_transport_v1 has rows, full_transport_live_enabled must be true
# If full_transport_live_enabled is false, full_transport_v1 must have 0 rows
# (turns the config-note observation into an enforced invariant)
count = conn.execute("SELECT COUNT(*) FROM model_bias_ens WHERE error_model_family='full_transport_v1'").fetchone()[0]
flag = settings["full_transport_live_enabled"]
if not flag:
    assert count == 0, f"Dead family has {count} rows but flag is false — armed-but-zero state"
```

### AT-7: ONE-BUILDER CI GUARD — no new resolution/bias tables
**File**: CI grep or `tests/test_schema_no_duplicate_tables.py`
```python
import subprocess
result = subprocess.run(["grep", "-r", "resolution_bridges\|bias_substrate", "src/state/schema/"], capture_output=True)
assert result.returncode != 0 or result.stdout == b""
```

---

## §4 BEFORE/AFTER MEASUREMENT PROTOCOL

### 4.0 CRITICAL CORRECTIONS TO STALE TABLE NAMES (from Design 3)

| PR_SPEC reference | Actual live object |
|---|---|
| `calibration_pairs_v2` | ARCHIVED as `calibration_pairs_v2_archived_2026_05_11`. Live table: `calibration_pairs` (48,157,324 rows) |
| "emos table" (DB) | NOT a DB table. File: `state/emos_calibration.json` (400 cells, 3-key) |
| `settlement_outcomes` in zeus-world.db | WRONG DB. Lives in `zeus-forecasts.db` ONLY |
| Hyphenated vs underscore DBs | Live: `zeus-forecasts.db` (35G), `zeus-world.db` (54G). Stubs: `zeus_forecasts.db`, `zeus_world.db` (0-byte) |
| "resolution/product column" | NOT a literal column. Derived from `source_id` + `bin_grid_id`/`bin_schema_id` in `ensemble_snapshots` |

### 4.1 DB sources and open modes

All reads: `sqlite3.connect("file:PATH?mode=ro&immutable=1", uri=True)`. Never write. WAL is active on live DBs; immutable reads may miss the latest uncommitted rows — acceptable for backtest of SETTLED history; state this in any report.

```
zeus-forecasts.db:
  settlement_outcomes  — TRUTH. WHERE authority='VERIFIED' (6637 rows). QUARANTINED (373) EXCLUDED.
  ensemble_snapshots   — forecast input. Lookahead frontier: available_at (NOT NULL). NOT imported_at.
  calibration_pairs    — training source. 48M rows. Use indexed scans ONLY:
                         idx_calibration_pairs_refit_core or idx_calibration_pairs_city_date_metric.
                         Never full-scan (confirmed timeout at 48M rows).

zeus-world.db:
  model_bias_ens       — bias rows. edli_per_city_v1 family (71 VERIFIED rows for live serving).

state/emos_calibration.json:
  400 cells. 3-key. Incumbent EMOS serving table.
```

### 4.2 Walk-forward law

For each city C with VERIFIED settlement depth >= N_min=40 settled days (ineligible cities REPORTED separately, never counted toward majority):
1. Order VERIFIED `settlement_outcomes` rows by `target_date` ascending.
2. For each fold's test date T:
   - Fit/serve INCUMBENT and KEYED configs using ONLY rows with `available_at < decision_instant(T)` AND `target_date < T` (typed BLOCKED on any lookahead — see AT antibody).
   - Produce served q per bin for both configs.
   - Grade against `settlement_outcomes.winning_bin` for (city, T, metric).
3. Decision_instant per fold mirrors the live entry lead (fix lead band per city so BEFORE/AFTER share the same lead — see OPEN QUESTION #4).

### 4.3 Metrics (both required for PASS)

**Metric A — Settlement bin-hit rate**: fraction of folds where served-argmax-bin == `winning_bin`. Also report the probabilistic variant: mean q assigned to `winning_bin` (higher=better).

**Metric B — After-cost win-rate on selective traded subset**: the traded subset for each config = bins where `q_lcb − ask − cost > δ` (via `executable_cost.py:70` — this function MUST be used; never midpoint/last/complement). Realized win = `winning_bin` in traded bin. Report win-rate AND traded-count AND total after-cost ROI. A win-rate improvement with collapsed volume is NOT a pass.

**After-cost reconstruction**: use `market_price_history` (best_bid, best_ask, recorded_at) at the nearest-prior quote to the decision instant. If `executable_market_snapshots` has coverage, prefer it (orderbook_top_ask, depth_at_best_ask, fee_details_json). Report book-coverage% per city; low-coverage cities are bin-hit-only.

### 4.4 PASS bar

Keyed serving must satisfy ALL of:
1. Settlement bin-hit rate >= incumbent in a MAJORITY of eligible cities.
2. After-cost win-rate >= incumbent in a MAJORITY of eligible cities.
3. NO city materially worse — "materially worse" = keyed below incumbent by more than the binomial 1-sigma noise band: `sqrt(p*(1-p)/n_folds)` for that city's fold count. Any city worse by > that band is a BLOCKER even if the majority passes.
4. Ties (within 1-sigma) count as non-regressions, not wins.

BEFORE/AFTER runs are PAIRED per fold: identical target_dates, identical lead band, identical δ, identical Kelly fraction, identical `executable_cost` engine. Only varied factor: cell-selection / bridge-keying.

### 4.5 INDEPENDENT RE-PROBE (iron rule — single run != trust)

The entire per-city walk-forward MUST be re-run by a SEPARATE invocation with a different fold seed/window-start offset. The majority verdict AND the no-city-materially-worse verdict must reproduce on the re-probe. A pass that does not reproduce is NOT a pass.

### 4.6 Boot-parity check (PR_SPEC §4.1)

With keyed flag OFF, daemon first cycle byte-identical to pre-PR. This proves the 138 open positions keep resolving on the existing 3-key cells. Run as a dry boot (no live submit) before any PR merge.

### 4.7 Measurement harness location

`scripts/p0_serving_measurement.py` — READ-ONLY against live DBs. Never imported by the daemon. Opens every DB with `mode=ro&immutable=1`. Additive, no live behavior.

---

## §5 OPEN QUESTIONS NEEDING OPERATOR DECISION

These are blocking or near-blocking. Code cannot answer them.

### OQ-1 (BLOCKS Change 4 — INCUMBENT_PRODUCT_ID value) [BLOCKING]

**Question**: What is the canonical `INCUMBENT_PRODUCT_ID` string for the incumbent EMOS cells?

**Context**: The 400 existing cells were fit by a PRODUCT-BLIND query (`fit_emos_calibration.py:102-107` has no product filter), so they represent a POOLED ecmwf_ens + tigge + ecmwf_ifs025 member set, NOT pure `ecmwf_open_data`. Tagging the incumbent default as `"ecmwf_open_data"` is a semantic approximation (the dominant live source_id, but not the only thing the cells were fit on). Alternatively, use a sentinel like `"legacy_pooled_ens"` to honestly name the pooled provenance.

**Choices**:
- (a) `"ecmwf_open_data"` — matches the live `source_id`; familiar; semantically approximate.
- (b) `"legacy_pooled_ens"` — honest about the pooled provenance; avoids a future misread where someone sees the label and assumes the cells are ecmwf_open_data-only filtered.

**Important**: Either way, the SERVING key for the incumbent MUST collapse to the bare 3-key string regardless of which sentinel is chosen. The choice only affects the comparison string in the gate.

**Recommendation**: Use (b) to preserve data-provenance honesty (Fitz #4), and document it in `_meta.product_id` of the fit output.

### OQ-2 (BLOCKS non-incumbent fit filter in Change 7) [BLOCKING for non-incumbent fits; incumbent fits are unblocked]

**Question**: Which `ensemble_snapshots` column is the authoritative product axis for the fit's per-product filter?

**Context**: Candidates are `source_id` (`'ecmwf_open_data'`/`'tigge_mars'`/NULL), `model_version` (`'ecmwf_ens'`/`'tigge'`/`'ecmwf_ifs025'`), `bin_grid_id`/`bin_schema_id`, or a composite. The replacement product uses `source_family='derived_posterior'` + `product_id='openmeteo_..._v1'` — a different taxonomy than the incumbent snapshot columns. The P0 implementation needs the operator to declare the single `product_id` vocabulary that both the incumbent and replacement map into.

**Note**: The incumbent fit (Change 7 default run) is UNBLOCKED — it keeps the unfiltered pool and writes 3-key cells exactly as today. Only non-incumbent fits are blocked on this answer.

### OQ-3 (INFORMS eligibility list for §4 measurement) [Near-blocking for measurement]

**Question**: What is the per-city live-source (`ecmwf_open_data`, 613K rows) own-resolution settled-day depth?

**Context**: The 48M-row `calibration_pairs` GROUP BY timed out; the per-city ecmwf count is unknown. This determines which cities can be graded on the keyed path in the measurement protocol. Cities below N_min=40 own-resolution settled days are ineligible. The harness must measure this first (with a proper index or date-bounded scan) before the eligibility list is final.

**Action required from operator**: Authorize a bounded scan on `calibration_pairs` WHERE `source_id='ecmwf_open_data'` using `idx_calibration_pairs_city_date_metric` to get per-city row counts. May take several minutes.

### OQ-4 (INFORMS walk-forward lead band) [Near-blocking for measurement]

**Question**: Is the per-city lead band (Day1-3 entry, or other) available as explicit data, or must it be inferred from `venue_order_facts` / decision timing?

**Context**: The walk-forward protocol requires fixing a lead band per city so BEFORE/AFTER share the same lead. If only inferable, the inference needs its own antibody test for lookahead (see AT antibody). The operator should either provide the config value or confirm the inference method.

### OQ-5 (INFORMS P2 reuse of §4 harness) [Deferred — P2 gate]

**Question**: For the P2 blend fine-tuning parsimony guardrail (>2pp in a majority of settlement contexts), is the 2pp threshold measured on bin-hit rate, after-cost win-rate, or both?

**Recommendation**: Both must clear +2pp majority (parsimony favors equal-weight on ties). But the contract should confirm. This does not block P0.

### OQ-6 (INFORMS long-term EMOS cell lifecycle) [Deferred — post-138-positions-close]

**Question**: Once a 4-key product is promoted, does P2 intend to RETIRE the incumbent 3-key cells, or run both indefinitely?

**Context**: Retirement would require a migration rewriting 3-key → 4-key under INCUMBENT_PRODUCT_ID. That migration MUST be deferred until after the 138 positions close, or it breaks back-compat mid-flight.

---

## §6 CONSTRAINTS CARRIED INTO IMPLEMENTATION

1. **One-builder/one-truth/one-exit**: `model_bias_ens` is the ONLY bias table. No `resolution_bridges` / `bias_substrate` tables. `emos_calibration.json` is the ONLY EMOS table.
2. **Sigma widen-only across the bridge** (PR_SPEC §0.9): when/if transport_bias_prior is wired to live serving, `total_residual_sd_c` on a bridged row must be >= the coarse prior sigma. Never sharpen.
3. **Settlement truth is VERIFIED-WU only**: `settlement_outcomes WHERE authority='VERIFIED'` in `zeus-forecasts.db`. QUARANTINED rows are permanently excluded.
4. **Lookahead is typed BLOCKED**: `available_at` is the frontier. Never `imported_at`/`recorded_at`.
5. **After-cost via `executable_cost.py:70` only**: assert_not_midpoint_cost law.
6. **Flag-off byte-identical**: all new serving code is behind feature flags defaulting to current behavior. The 138 open positions must not be disturbed.
7. **Change 3 (grid_resolution column) is a recommendation, not mandatory for P0 ship**: P0 can ship with Changes 1-2 + 4-9 only. Change 3 is belt-and-suspenders for the serving path; it is not needed to close the offline gate gap or the EMOS product-axis gap. Recommend shipping it alongside but it is not a blocker.
