# Phase 10B Contract v2 — DT-Seam Cleanup (post critic-dave cycle-3 precommit)

**Written**: 2026-04-19 post Phase 10A commit `81294d2` pushed.
**Revised**: 2026-04-19 post scout landing-zone + critic-dave cycle-3 precommit — **v1 had 2 CRITICAL contract errors (B067/B074 wrong file:line + stale bug descriptions) + 3 MAJOR (S4 "activate" false claim, S2 consumer list bloated, S3 site count underestimated). This v2 absorbs all findings.**
**Branch**: `data-improve` @ `81294d2`.
**Mode**: Gen-Verifier. critic-dave cycle 3 (retirement cycle; L20/L21 retirement learnings saved to memory).

## v1 → v2 delta

| Finding | v1 | v2 correction |
|---|---|---|
| C1 — B067 `db.py:2571-2745` wrong + "hardcoded env='live' literal" false claim | S6 in scope | **DROPPED**. Grep-verified: `db.py:2265` reads `getattr(pos, "env", "live")` — pos.env with fallback. Paper mode retired → "live" default is correct. Bug description was stale audit memory. |
| C2 — B074 `portfolio.py:741-744` wrong citation; actual sites L789/L855 architect-gated YELLOW | S7 in scope | **DROPPED**. Comment at L849-854 explicitly marks this as architect-decided YELLOW preserving provenance. Not a peacetime fix. Defer to P10C or architect packet. |
| M1 — S4 "Activate pre-existing stub" — stub is already live testing different semantics | R-CO.1 "activate" | Renamed to **EXTEND**. `test_fdr_family_key_is_canonical` is already live at `tests/test_dual_track_law_stubs.py:195-217` testing scope separation (`h_id != e_id`). New semantics: add metric-aware assertion alongside. |
| M2 — S2 R4 consumer list overestimated; FDR + Kelly are NOT consumers | "evaluator + FDR + Kelly" | **Singleton**: only `src/engine/evaluator.py` (L1446/L1466/L1529). Plus `scripts/bridge_oracle_to_calibration.py` (one-shot bridge). Blast radius ≤ 3 files. |
| M3 — S3 R5 site count underestimated (grep shows 9, not 3) | 3 sites | Explicit allowlist of **9 runtime seams** (below). R-CN.2 AST probe scoped to allowlist, not grep-wide. |
| m1 — v2_row_counts has no consumer (checkbox risk) | write-only shape assertion | Add minimal consumer: append to existing status_summary's operator-read path; flag in status when `v2 claimed closed AND zero rows` discrepancy. |
| m2 — R5 Literal tightening may surface monitor_refresh LOW TypeError | not flagged | Forward-log noted; if regression surfaces it, xfail with ticket. |
| Parallelism — S2 + S4 both touch `evaluator.py` L1446-1529 region | Executor-A vs Executor-B | Combined into **single executor worker** (S1-S5 serial) to avoid merge conflict on evaluator.py. |

**Net effect**: Scope shrinks from 7 items to 5 (S1 R3, S2 R4, S3 R5, S4 R9, S5 R11). S6/S7 deferred.

## Why this phase exists

Phase 10A closed all Bucket A (audit-append residue + tracker lag + sentinel debt) at commit `81294d2`. Phase 10B addresses Bucket B DT-seam correctness gaps that unblock Gate F (low-track limited activation):

- **R3 replay legacy fallback metric-aware WHERE** — LOW replay in v2-empty Golden Window falls through to HIGH-only filter.
- **R4 oracle_penalty (city, metric) keying** — cache keyed by city alone; LOW inherits HIGH error rates. Delivers 2/3 of DT#7.
- **R5 MetricIdentity at runtime seams** — `temperature_metric: str` across 9 seams; mis-typed metric falls through to silent legacy-fallback.
- **R9 FDR family_id metric-aware** — latent crosstalk once Gate F opens.
- **R11 v2 row-count observability sensor** — meta-immune-system gap P9C closure fired under.

## Scope — 5 items (ONE atomic commit, single executor worker)

### S1 — R3 replay legacy fallback metric-aware WHERE (~30 LOC)

**File**: `src/engine/replay.py:309` (grep-verified).

**Current** (L307-310):
```sql
SELECT ... FROM forecasts
WHERE city = ? AND target_date = ?
  AND forecast_high IS NOT NULL
```

**Target**: metric-aware branch based on caller-provided `temperature_metric`:
```python
col = "forecast_low" if temperature_metric == "low" else "forecast_high"
where = f"AND {col} IS NOT NULL"
```

Translator at L286-295 already populates both columns — confirmed by scout.

**Antibodies (R-CL.1/2)**:
- **R-CL.1**: LOW replay with v2 empty + legacy row with `forecast_low=X, forecast_high=NULL` → returns the LOW row
- **R-CL.2**: HIGH replay unchanged behavior — pair-negative surgical-revert probe

### S2 — R4 oracle_penalty (city, metric) keying (~50 LOC, 3 files)

**Files (grep-verified)**:
- `src/strategy/oracle_penalty.py:46` (`_cache: dict[str, OracleInfo]`) + L49 `_load` + L93 `get_oracle_info`
- `src/engine/evaluator.py:1446, 1466, 1529` — sole runtime consumer
- `scripts/bridge_oracle_to_calibration.py` — one-shot bridge producer (writes `data/oracle_error_rates.json`)
- `data/oracle_error_rates.json` — persistence file; schema must add metric dimension

**Current**: `dict[str, OracleInfo]` keyed by `city`. `data/oracle_error_rates.json` shape `{city: {...}}`.

**Target**:
- In-memory cache: `dict[tuple[str, str], OracleInfo]` keyed by `(city, temperature_metric)`
- JSON schema: `{city: {high: {...}, low: {...}}}` — nested metric dimension
- Legacy JSON shape (no metric nesting) migrated as `(city, "high")` entries on load; LOW cache starts empty
- `get_oracle_info(city, temperature_metric)` signature adds kwarg
- All 3 consumer sites in `evaluator.py` pass `position.temperature_metric` / candidate metric
- `bridge_oracle_to_calibration.py` writes new nested shape; reads for both metrics where available

**Antibodies (R-CM.1/2/3)**:
- **R-CM.1**: seeding `(chicago, high)` penalty → `get_oracle_info("chicago", "low")` returns separate uncontaminated OracleInfo
- **R-CM.2**: cache invalidation per `(city, metric)` — invalidating HIGH does not evict LOW
- **R-CM.3**: legacy single-dim JSON loads into `(city, "high")` entries only (backward-compat migration)

**Delivers 2/3 of DT#7**.

### S3 — R5 MetricIdentity at runtime seams (~40 LOC, 9 sites)

**Explicit allowlist of seams** (grep-verified 2026-04-19):

| File | Line | Current signature |
|---|---|---|
| `src/state/portfolio.py` | 152 | `temperature_metric: str = "high"` (Position dataclass field) |
| `src/calibration/manager.py` | 128 | `temperature_metric: str = "high"` (get_calibrator arg) |
| `src/calibration/manager.py` | 246 | `temperature_metric: str = "high"` (sibling function) |
| `src/engine/replay.py` | 244 | `temperature_metric: str = "high"` (replay config) |
| `src/engine/replay.py` | 318 | `temperature_metric: str = "high"` (`_forecast_reference_for`) |
| `src/engine/replay.py` | 356 | `temperature_metric: str = "high"` (`_forecast_snapshot_for`) |
| `src/engine/replay.py` | 385 | `temperature_metric: str = "high"` (`get_decision_reference_for`) |
| `src/engine/replay.py` | 1167 | `temperature_metric: str = "high"` (replay helper) |
| `src/engine/replay.py` | 1995 | `temperature_metric: str = "high"` (`run_replay` public entry) |

**Target**: `temperature_metric: Literal["high", "low"] = "high"` at all 9 seams. Lightweight — no full `MetricIdentity` adoption; `MetricIdentity.from_raw()` still happens at JSON/SQL boundaries (unchanged).

**Type-check improvement**: mis-typed metric (e.g., `"HIGH"`, `"Low "`, `"high_temp"`) now raises `TypeError` via `typing.Literal` runtime enforcement (actually — Python's `Literal` is compile-time only; runtime check requires explicit validation. Use `assert temperature_metric in ("high", "low")` at each seam, OR switch to `MetricIdentity` if the Literal trade-off proves weak).

**Trade-off acknowledgment (per dave cycle-3 precommit Probe B)**: `Literal` is a semantic downgrade from full `MetricIdentity`. If P10C (or later) needs physical_quantity/observation_field/data_version at these seams, the upgrade path is: flip annotations to `MetricIdentity`, replace string lookups with `.temperature_metric` attribute access. For P10B, Literal is sufficient.

**Antibodies (R-CN.1/2)**:
- **R-CN.1**: AST probe — each of the 9 seams has `Literal["high", "low"]` annotation on `temperature_metric` parameter/field
- **R-CN.2**: allowlist-scoped grep-gate — the 9 seams above must stay on Literal; pre-existing `temperature_metric: str` at OTHER sites (≥30 per dave M3) is out-of-scope for P10B (documented carry-forward for P10C)

**Forward-log**: R5 Literal tightening may surface monitor_refresh LOW regression as TypeError at a now-stricter seam. If regression surfaces, either (i) fix in same commit, or (ii) xfail with a P10C ticket. Team-lead flags post-impl.

### S4 — R9 FDR family_id metric-aware (~25 LOC, EXTEND not ACTIVATE)

**File**: `src/strategy/selection_family.py:28-57` (grep-verified).

**Current** (verified by scout):
- L28-48 `make_hypothesis_family_id(*, cycle_mode, city, target_date, discovery_mode, decision_snapshot_id)`
- L51+ `make_edge_family_id(*, ... strategy_key)`
- Tuple parts: `["hyp", cycle_mode, city, target_date, discovery_mode, decision_snapshot_id]` — no metric dimension

**Target**: add `temperature_metric: Literal["high", "low"]` as required kwarg to both functions. Tuple becomes `["hyp", cycle_mode, city, target_date, temperature_metric, discovery_mode, decision_snapshot_id]` (or similar positional insertion).

**Test EXTEND (not activate)**:
- `tests/test_dual_track_law_stubs.py:195-217` `test_fdr_family_key_is_canonical` is **already live at HEAD** testing scope separation `h_id != e_id`.
- EXTEND: add assertion `make_hypothesis_family_id(..., temperature_metric="high") != make_hypothesis_family_id(..., temperature_metric="low")` (same other args).
- Test count unchanged; assertion count increases by 1.

**Callers**: `src/engine/evaluator.py:1458-1459` — must pass candidate metric.

**Antibodies (R-CO.1/2)**:
- **R-CO.1**: EXTEND the existing test with metric-discriminating assertion. Surgical-revert: drop metric from family_id tuple → test fails.
- **R-CO.2**: evaluator caller AST probe — `evaluator.py:1458-1459` call sites pass `temperature_metric=` kwarg.

**Side-effect scan (per dave precommit prediction #4)**: other FDR tests may assert metric-agnostic family_id structure. Scout + executor must enumerate `tests/test_fdr*.py` + `tests/test_selection_family*.py` for hardcoded ID string assertions. If found, migrate or xfail.

### S5 — R11 v2 row-count observability sensor + discrepancy flag (~50 LOC)

**File (grep-verified)**: `src/observability/status_summary.py` (`STATUS_PATH` L36, `write_status` L72).

**Target**:
- Add `_get_v2_row_counts(conn) -> dict[str, int]` helper that queries 5 v2 tables (`platt_models_v2`, `calibration_pairs_v2`, `ensemble_snapshots_v2`, `historical_forecasts_v2`, `settlements_v2`)
- Emit `v2_row_counts` into status payload
- **Consumer per dave m1 mitigation**: if claimed closures (e.g., `dual_track_closure: true`) AND any v2 table has 0 rows, emit `discrepancy_flags: ["v2_empty_despite_closure_claim"]` in status. This is the meta-immune-system signal P9C fired without.

**Antibodies (R-CP.1/2)**:
- **R-CP.1**: status_summary output contains `v2_row_counts` dict keyed by 5 table names; values are actual sqlite COUNT queries (not hardcoded)
- **R-CP.2**: discrepancy flag fires when `v2_row_counts["platt_models_v2"]=0` AND a "closure" claim is present — closes the checkbox risk

## Hard constraints

- **No TIGGE import** / **no v2 table writes** / **no SQL DDL on v2**
- **No `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` change** — R13 deferred
- **No `kelly_size` strict migration** — R10 deferred to P10C
- **No Day0 LOW live activation** — Gate F blocked
- **No `except Exception` narrowing** — deferred
- **No B067 touch** (DROPPED from scope; bug claim stale)
- **No B074 touch** (DROPPED; architect YELLOW)
- Golden Window intact

## Acceptance

**Regression budget**: 142 failed / 1894 passed / 93 skipped / 7 subtests (post-P10A baseline).
- Delta failed ≤ 0
- Delta passed ≥ new antibody count + any side-effect unblocks
- Skipped unchanged (no newly-activated tests — S4 is EXTEND, not ACTIVATE)

**R-letter namespace**: R-CL onwards (R-CK.6 last used in P10A cycle-2 fix).

**Antibodies minimum**:

| ID | Target |
|---|---|
| R-CL.1/2 | replay legacy LOW WHERE + HIGH pair-negative |
| R-CM.1/2/3 | oracle_penalty (city, metric) isolation + invalidation + legacy JSON migration |
| R-CN.1/2 | MetricIdentity 9-seam allowlist: Literal annotations + AST allowlist-scoped gate |
| R-CO.1/2 | FDR family_id metric-aware EXTEND + evaluator caller AST |
| R-CP.1/2 | v2 row-count sensor + discrepancy flag consumer |

All antibodies must surgical-revert-fail.

## Out-of-scope (deferred to P10C or architect)

- B067 (claim stale; bug not real)
- B074 (architect YELLOW — not peacetime)
- R6 Gate C resolution (user ruling)
- R7 Golden Window lift timing (user ruling)
- R8 "DUAL-TRACK MAIN LINE CLOSED" title amendment
- R10 Kelly strict ExecutionPrice (breaking; needs user ruling)
- R12 H7 144-failure triage
- R13 `_TRUTH_AUTHORITY_MAP` re-decision
- B055 (DT#6 architect packet)
- B099 (DT#1 architect packet)
- monitor_refresh LOW plumbing (may surface under S3; forward-log if so)
- Phase 10A S1 except-narrowing (ops-behavior shift; user ruling)
- Pre-existing `temperature_metric: str` sites outside the 9-seam allowlist (P10C blanket migration or piecemeal)

## Sequencing plan (simplified vs v1 — single executor)

1. team-lead writes contract v2 ← this file
2. critic-dave cycle-3 precommit already returned ITERATE on v1; v2 should get quick ACK (absorbed findings)
3. **Single executor worker** implements S1-S5 in order (S2 + S4 both touch evaluator.py — serial execution within one worker avoids merge collision)
4. team-lead rolls together, disk-verify
5. Regression run
6. critic-dave cycle-3 wide review (retirement cycle; adversarial posture per L6 calibration)
7. ITERATE or PASS → commit + push
8. critic-dave retires at close; critic-eve opens on any future phase

## Grep-gate discipline (applied per L20)

All S-item file:line citations grep-verified within last 5 minutes of contract lock. Scout landing-zone scan independently confirmed the citations. If executor finds a citation stale, STOP and request team-lead re-grep.
