# Zeus Upstream Data Rebuild — Plan

**v2.2 (2026-04-13)**: scope narrowing per user directive — this packet is strictly **collect + store code preparation** while TIGGE download completes. Any change that only matters when running Platt recalibrate (i.e., utilization) is deferred to §10.1 post-packet. Specifically moved OUT of §5 and INTO §10.1: **Change J (logit_safe eps alignment)**, **Change K (Platt bootstrap by decision_group)**, **Change N (live `_bin_probability` histogram)**, and the **Change G.7 proposal (manager.py n_eff counter)** raised from relationship test R9. Rationale: none of these affect data that is collected or written; they affect how the Platt fit consumes that data. No point changing them until TIGGE coverage is complete and the at-recalibrate-time packet runs. The 11 remaining Changes (A, B, C, D, E, F, G.1–G.6, H, I, L, M) form the new scope boundary.

**2026-04-14 refit-preflight update**: the at-recalibrate-time fixes J/K/N/G.7
have now been implemented before data refit: `logit_safe` exists, Platt
bootstrap can resample decision groups, manager/refit maturity uses n_eff, and
live bin probability now uses the shared bin helper. The unresolved eps policy
question remains whether to keep the current conservative `0.01` clamp or move
to the math-spec `1e-6` in a measured refit experiment.

**v2.1 (2026-04-13)**: iteration 2 fixes per Critic ITERATE verdict — §16 authority gate fact correction (MUST-FIX 1), Change G fallback deletion (MUST-FIX 2), Change N live `_bin_probability` fix (MUST-FIX 3, Option A), §8.1 statistical correction (SHOULD-FIX 1), Change M UTC-explicit `issue_time` (SHOULD-FIX 2), Change G test G3 SQLite round-trip clarification (SHOULD-FIX 3), test F1 analytical tolerance (SHOULD-FIX 4), E2E fixture bump to 60 days (SHOULD-FIX 5), staged Bin migration ADR note (SHOULD-FIX 6).

**v2 (2026-04-13 earlier)**: second-order trap corrections per user review — decision_group_id hash stability, ±inf JSON boundary safety, square-matrix training window.

**Scope**: upstream data **collection and storage code preparation** only. Consumers of the data (Platt fit eps, bootstrap CI, live trading `_bin_probability`) are out of scope — see §10.1. Midstream/downstream wiring issues deferred.
**Branch**: `data-improve`.
**Supersedes**: all prior K5-refactor F1-F5 files (now in worktree `_superseded/`), `DATA-REBUILD-PLAN.md` draft (worktree).
**Authority contract**: `docs/reference/zeus_math_spec.md` v2 is the reference math specification for this plan. Executable contracts, root `AGENTS.md`, `architecture/**`, and `docs/authority/**` win on disagreement.
**Last updated**: 2026-04-13.

---

## 0. Status snapshot

| Item | State |
|---|---|
| Math spec (`docs/reference/zeus_math_spec.md`) | v2 locked, includes WMO rounding + decision_group + open-boundary bins + logit clipping |
| `AGENTS.md` §1 "Why settlement is integer" | Patched to WMO asymmetric half-up |
| `docs/reference/statistical_methodology.md` | Patched (np.round → np.floor(x+0.5)) |
| TIGGE cache | 14 GB at `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/` |
| TIGGE regions multi-step (48-168h lead) | 11 GB, 2024-01-01 → 2026-03-09, GRIB files only, **no JSON extraction yet** |
| TIGGE per-city 24h lead | 2.2 GB, 2023-10-23 → 2026-04-09, JSON already extracted |
| TIGGE download pipeline | Active (`tigge_full_history_pipeline.py` + cron) |
| `ensemble_snapshots` DB row count | **0** (GRIB on disk not yet ingested to DB) |
| Live forecast path | Open-Meteo API (temporary fallback); TIGGE GRIB is intended canonical |
| Live calibration Y source | Polymarket `winningOutcome` via harvester (separate from rebuild path) |
| Rebuild training data path | `backfill_wu_daily_all → rebuild_settlements → rebuild_calibration_pairs_canonical → refit_platt` |
| `SettlementSemantics` rounding rule | **FIXED in Packet 1** — active rule is `wmo_half_up`; verify before any rebuild |
| `init_schema` on production DB | Refit-preflight repair makes `calibration_decision_group` lead-key migration data-preserving; broader production schema checks still run at Step 0 |
| `decision_group_id` population coverage | API/refit-gated: pair writers must pass explicit nonblank IDs; refit rejects missing IDs |
| `Bin` outer-boundary ±inf support | Implemented as staged `None`/±inf compatibility with JSON sentinel helpers |
| Logit clipping in Platt | `logit_safe()` implemented; eps value remains an explicit refit policy decision |
| Bootstrap resampling unit | Platt parameter bootstrap supports decision-group resampling when IDs are supplied |
| `authority='VERIFIED'` live-path gate | Store/refit paths filter VERIFIED rows; canonical-only refit gate added |

---

## 0.5 RALPLAN-DR Summary (Deliberate Mode)

### Principles

1. **decision_group is the atomic independent sample unit.** All statistics that assume independence — n_eff, bootstrap CI, Kelly sizing — must count and resample by decision_group, never by pair row.
2. **WMO half-up at every rounding site.** `floor(x + 0.5)` is the single permitted implementation for settlement-aligned values everywhere in the pipeline: Monte Carlo simulation, settlement derivation, P_raw computation. No exceptions.
3. **Square-matrix training data.** The Extended Platt's `lead_days` coefficient B must be estimated from data that is symmetric across all lead values. Any training corpus with asymmetric lead-day distribution corrupts B and distorts CIs at long lead.
4. **Data provenance at every DB boundary.** Rows without `authority='VERIFIED'` do not enter the computation chain. The `authority` column is the only acceptable gate — not code comments, not documentation. This gate is a TARGET of this rebuild, not a pre-existing feature.
5. **Structural fixes over patches.** The 17 defects are symptoms of 5 structural decisions (rounding authority, hash stability, bin boundary safety, training symmetry, provenance gate). Fix the structure; don't patch the symptoms.

### Decision Drivers (top 3)

1. **TIGGE coverage shape.** 11 GB of regional GRIBs cover 2024-01-01 → 2026-03-09 for all 7 lead days, but nothing before that. The training window must respect this boundary; any extension must be validated for symmetry.
2. **User's kill-switch on patching.** The methodology (see `CLAUDE.md`) explicitly rejects 17 individual patches in favor of structural changes that make defect categories impossible. This forces Changes A-N to each target a defect category, not a symptom.
3. **`decision_group_id` independence invariant.** If the hash is unstable, `n_eff` is inflated, bootstrap intervals are falsely tight, and Kelly sizes incorrectly. This is the highest-order correctness invariant because it affects every downstream statistical operation.

### Viable Options

Two approaches were considered for the overall rebuild strategy:

**Option A (Chosen): Full structural rebuild with 14 letter-coded changes.**
Fix all defect categories structurally (hash stability, WMO rounding, bin safety, bootstrap resampling, Platt equivalence, live bin_probability) before touching any data. Run pipeline from scratch on square-matrix training window.
- Pro: permanent immunity to every defect category; clean audit trail; each Change independently reviewable
- Pro: rebuild is idempotent and re-runnable as TIGGE pipeline extends coverage
- Con: 14 changes must all land before Step 0 can run; higher upfront cost

**Option B (Rejected): Incremental patch-and-run.**
Fix only the blocking defects (D1 WMO rounding, D3 schema idempotence, D15 ingest script) and run the pipeline, accepting that decision_group_id stability, bootstrap correctness, and bin topology remain soft risks.
- Pro: faster first data pass
- Con: produces training data that may silently violate the independence invariant; any discovered defect post-run requires re-running the entire pipeline from scratch anyway; violates the structural-fix methodology
- **Invalidated**: once Option A's structural changes exist, Option B offers no residual benefit and its training data cannot be certified. The re-run cost of discovering defects post-Option-B exceeds the upfront cost of Option A.

### Pre-mortem (3 Scenarios)

**Scenario 1: Platt models silently miscalibrated after rebuild due to a remaining rounding/hash/infinity bug.**
- How it happens: a code path that doesn't go through `SettlementSemantics.round_values` (e.g., a helper script that computes settlement directly via `int()` or `round()`) corrupts a minority of pair rows; or `compute_id()` receives a timezone-aware datetime on some paths and a naive datetime on others, producing duplicate hash collisions that inflate apparent n_eff for the affected buckets.
- Detection mechanism: Step 8 soft gate — per-lead-days calibration slope A coefficient outside [0.3, 2.5] is a signal. The hash stability audit (1000 random rows re-computed) would catch the decision_group collision. WMO verification table unit test `tests/test_wmo_rounding.py` would catch the rounding bug at Step 0.
- Prevention: semantic_linter forbids `np.round`/`round(` on settlement paths (Step 0 pre-flight, hard failure). `compute_id()` is the ONLY hash producer; defensive type normalization inside it raises `TypeError` on ambiguous input rather than silently producing a wrong hash.

**Scenario 2: TIGGE multi-step extraction (Change L) fails partway through 11 GB of GRIBs, leaving half-extracted state.**
- Failure modes: disk full mid-extraction; `eccodes` GRIB parse error on a corrupted file; script crash at a specific dated directory; background TIGGE download cron writing to a file while the extractor reads it.
- Detection: Step 2 gate checks that per-city JSON count matches expected dates × cities. A partial extraction would show count below threshold. Additionally, the extraction script should write a per-dated-directory sentinel file on success; re-runs skip sentinel-present directories.
- Prevention: idempotent extraction — per-directory sentinel file (e.g., `.extracted_ok`) prevents re-processing already-extracted directories. Atomic rename on output files: write to `.tmp`, then `os.replace()` to final path. This means a crash mid-file leaves a `.tmp` orphan, not a corrupt final file. Step 2 gate is a hard gate (abort rebuild on failure).

**Scenario 3: Destructive Step 1 migration wipes production rows but subsequent backfill fails, leaving zero usable training data.**
- Failure modes: `backfill_wu_daily_all.py` crashes due to a WU API rate limit; IngestionGuard rejects all rows due to a unit validation bug introduced by Change C; DB backup taken before Step 1 is itself corrupt.
- Detection: after Step 1, immediately assert `SELECT COUNT(*) FROM observations` is near-zero (expected: wipe was intended). After Step 4 (WU backfill), assert per-city row count ≥ 99% of expected coverage. Any deficit → stop and escalate before proceeding to Step 5.
- Prevention: (a) full DB backup via `sqlite3 db.sqlite ".backup db_backup_prestep1.sqlite"` before Step 1 runs, with backup size verification; (b) test the full Step 4 script against a **sandbox DB clone** before running on production DB; (c) IngestionGuard Change C changes have their own unit test green-gate at Step 0, so unit validation bugs are caught pre-Step 1.

---

## 1. Why we collect data

Three user-stated reasons. The whole plan is in service of these.

### 1.1 Eliminate systematic bias in the X → Y chain

The chain from ECMWF raw forecast (X) to Polymarket settlement (Y) has three compounding error sources:
- **Instrument error**: ASOS sensor noise (σ ≈ 0.2–0.5°F)
- **Rounding truncation**: WMO asymmetric half-up `floor(x+0.5)` per §1.2 of `zeus_math_spec.md`
- **Temporal skill decay**: raw ensemble probabilities are overconfident at long lead and underconfident near settlement

Without training data that captures these biases per (city × season × lead_days), the forecast chain is structurally miscalibrated. Rebuild's job is to generate the data that lets `refit_platt.py` learn the corrections.

### 1.2 Build (P_raw, outcome) training pairs for Extended Platt

The product of rebuild is a table of training pairs:

```
(P_raw, lead_days, outcome ∈ {0,1}, city, season, decision_group_id)
```

`P_raw` comes from Monte Carlo over the 51-member ensemble with instrument noise + WMO rounding per `zeus_math_spec.md §4`. `outcome` comes from WU-reported integer settlement mapped to bins per `zeus_math_spec.md §11`. Extended Platt (§6 of math spec) fits `P_cal = σ(A·logit_safe(P_raw) + B·lead_days + C)` per (city × season) bucket.

The point of collecting is **not observations in general** — it's these specific pairs structured correctly.

### 1.3 Preserve independence for CI and Kelly

Bootstrap CI (`zeus_math_spec.md §8.2`) and fractional Kelly (`§10`) both assume independent samples. If we treat correlated pair rows (all bins from one ensemble snapshot) as independent, bootstrap intervals shrink falsely and Kelly over-sizes. The result is a system that is confidently wrong and **over-positions into its own calibration error**.

The independent sample unit is the **decision group** (`zeus_math_spec.md §12.1`):

```
g = (city, target_date, issue_time, source_model_version)
n_eff = #{g}
```

Every statistical operation that assumes independence must resample / split / count by decision_group, never by pair row.

---

## 2. What we collect

Atomic unit = **Decision Group**. Three sides per group:

### 2.1 X side — prior forecast

Data:
- 51-member ECMWF ensemble (1 control + 50 perturbed), ensemble source
- Each member = **daily max** of that member's hourly forecast, computed in **city local time** (`zeus_math_spec.md §2.3`)
- Values stored in **city settlement unit** (F or C), converted from native Kelvin at ingest

Storage:
- Table: `ensemble_snapshots`
- Columns (atom): `(city, target_date, issue_time, lead_hours, members_json, authority, provenance_metadata, model_version, data_version)`
- `members_json` = list of 51 floats (daily maxes per member)
- `source_model_version` is the logical identity value passed to
  `compute_id(...)`; in the current schema it is derived from `data_version`
  with `model_version` as an explicit fallback.
- `authority = 'VERIFIED'` only after passing §3 guardrails + blessed pipeline
- `provenance_metadata` = JSON with `{grib_file_path, extractor_version, ingest_run_id, regional_or_per_city}`

Lead set: `{24, 48, 72, 96, 120, 144, 168}` hours = `{1, 2, 3, 4, 5, 6, 7}` days

### 2.2 Y side — truth settlement

Data:
- WU-reported integer daily high for 45 cities (via WU ICAO historical API)
- HKO-reported integer daily high for Hong Kong (HK packet deferred — see §10; same rigor when executed)
- Integer in city settlement unit, **produced via WMO `floor(x + 0.5)` rule**, never via `np.round`/`round`

Storage:
- Intermediate: `observations` table via `ObservationAtom` + `IngestionGuard`
- Derived: `settlements` table via `rebuild_settlements.py` + `SettlementSemantics.assert_settlement_value`
- Columns (settlement): `(city, target_date, settlement_value, settlement_source, authority, UNIQUE(city, target_date))`

### 2.3 Topology side — Polymarket bin structure

Data:
- Per (city, target_date): the set of bins Polymarket defined for that market
- Each bin: `Bin(unit, low, high, direction ∈ {yes, no})`
- **Outer bins use `float('-inf')` / `float('inf')`** to ensure coverage invariant `∪ bins = ℤ`
- Format: "X° or lower" → `Bin(low=-inf, high=X)`; "Y° or higher" → `Bin(low=Y, high=+inf)`

Storage:
- Table: `market_events` (existing) — bin rows per market
- New constraint: `low`, `high` must be type `REAL` allowing IEEE 754 `-inf`/`+inf`
- Validation: §3.2 Bin topology completeness scan
- **JSON boundary**: `Bin.low`/`high` stay as Python `float` in memory and as SQLite `REAL`. They MUST NOT cross `json.dumps()` directly — use `to_json_safe()` / `from_json_safe()` wrappers (§3.5, Change H).

---

## 3. How we ensure correctness (the 5 guardrails)

Every row that reaches `authority='VERIFIED'` in any of the 5 tables must pass these checks. Each guardrail runs at a defined point in the pipeline and fails fast.

### 3.1 Pre-write WMO rounding enforcement

**What it prevents**: banker's rounding (`round()` / `np.round()`) corrupting settlement values and Monte Carlo simulated readings. Both must use WMO `floor(x + 0.5)`, or P_raw and Y end up in different rounding conventions.

**Where it runs**:
1. **Static check** (pre-flight, before rebuild begins): `semantic_linter` rule that forbids `np.round`, `round(` on any line in `src/contracts/settlement_semantics.py`, `src/signal/ensemble_signal.py`, `src/data/rebuild_validators.py`, `scripts/rebuild_*.py`. Exit non-zero if any hit.
2. **Runtime assertion**: `SettlementSemantics.round_values` must use `np.floor(scaled + 0.5)`. Add a unit test: `round_values([74.5, -1.5, -2.5])` must equal `[75, -1, -2]`, NOT `[74, -2, -2]`.
3. **CI gate**: pytest test `tests/test_wmo_rounding.py` asserts the WMO verification table from `zeus_math_spec.md §1.2`.

**On failure**: abort the rebuild pipeline. No partial state.

### 3.2 Bin topology completeness scan (coverage check)

**What it prevents**: a market with a gap in its bin set (missing outer `-inf` / `+inf` bins, or a missing intermediate integer) silently producing `outcome_yes = 0` for every bin, violating the "exactly one winning bin" invariant (`zeus_math_spec.md §5.3`, `§11.2`).

**Where it runs**: `src/types/market.py::validate_bin_topology(market_bins) -> None` (new helper). Called:
1. **Pre-pair-generation** (`rebuild_calibration_pairs_canonical.py` Step 6): before generating any `calibration_pairs` for a (city, target_date), validate that market's bins.
2. **Live entry gate**: pending follow-up. The helper exists, but evaluator
   wiring is not yet present and must not be claimed as complete.

**Validation logic**:
```python
def validate_bin_topology(bins: list[Bin]) -> None:
    if not bins:
        raise BinTopologyError("empty bin set")
    # Sort by low edge (treat -inf as smallest, +inf as largest)
    sorted_bins = sorted(bins, key=lambda b: (b.low, b.high))
    # Leftmost bin must start at -inf
    if sorted_bins[0].low != float('-inf'):
        raise BinTopologyError(f"leftmost bin low={sorted_bins[0].low}, must be -inf")
    # Rightmost bin must end at +inf
    if sorted_bins[-1].high != float('inf'):
        raise BinTopologyError(f"rightmost bin high={sorted_bins[-1].high}, must be +inf")
    # No gaps: bins[i].high + 1 == bins[i+1].low (for integer-closed intervals)
    for i in range(len(sorted_bins) - 1):
        if sorted_bins[i].high + 1 != sorted_bins[i+1].low:
            raise BinTopologyError(
                f"gap between bins: [{sorted_bins[i].low},{sorted_bins[i].high}] and "
                f"[{sorted_bins[i+1].low},{sorted_bins[i+1].high}]"
            )
```

**On failure**: **quarantine the entire (city, target_date)**. Do NOT generate any calibration pairs from that day. Log to `availability_fact` with failure_type=`BinTopologyError`. The group is excluded from training, but the rebuild continues for other days.

### 3.3 Hash stability for decision_group_id

**What it prevents**: the same ensemble snapshot being assigned two different `decision_group_id`s across runs — which would inflate `n_eff` and break bootstrap/maturity gates.

**Canonical hash template** (v2 — explicit strftime, no `.isoformat()` ambiguity):

```python
def compute_id(
    city: str,
    target_date: Union[date, datetime, str],
    issue_time: Union[datetime, str],
    source_model_version: str
) -> str:
    """
    Canonical decision_group_id producer. This is the ONLY function in the
    codebase permitted to compute this hash. All callers must import this.

    Type normalization (before hashing):
    - target_date: forced to datetime.date, then formatted as '%Y-%m-%d'
      (strips all time/tz info; date carries none)
    - issue_time: forced to UTC-aware datetime, then formatted as
      '%Y-%m-%dT%H:00:00Z' (hour resolution, explicit UTC, explicit Z suffix)
    """
    # --- target_date normalization ---
    if isinstance(target_date, str):
        _target_date = date.fromisoformat(target_date)
    elif isinstance(target_date, datetime):
        _target_date = target_date.date()
    elif isinstance(target_date, date):
        _target_date = target_date
    else:
        raise TypeError(f"target_date must be date/datetime/str, got {type(target_date)}")
    date_str = _target_date.strftime('%Y-%m-%d')

    # --- issue_time normalization ---
    if isinstance(issue_time, str):
        _issue_time = datetime.fromisoformat(issue_time)
    elif isinstance(issue_time, datetime):
        _issue_time = issue_time
    else:
        raise TypeError(f"issue_time must be datetime/str, got {type(issue_time)}")
    # Force to UTC
    if _issue_time.tzinfo is None:
        raise TypeError("issue_time must be timezone-aware; naive datetime is ambiguous")
    _issue_time_utc = _issue_time.astimezone(timezone.utc)
    time_str = _issue_time_utc.strftime('%Y-%m-%dT%H:00:00Z')

    payload = f"{city}|{date_str}|{time_str}|{source_model_version}"
    return hashlib.sha1(payload.encode()).hexdigest()
```

**Why explicit strftime and not `.isoformat()`**:
- `date(2024,1,1).isoformat()` → `"2024-01-01"` (no time component)
- `datetime(2024,1,1,0,0).isoformat()` → `"2024-01-01T00:00:00"` (different string)
- `datetime(2024,1,1,0,0,tzinfo=UTC).isoformat()` → `"2024-01-01T00:00:00+00:00"` (different again)
- The same physical snapshot reaching `compute_id()` via Python construction, SQLite round-trip, or JSON deserialization will have different Python types; without normalization, the hash would differ silently.

Using `strftime('%Y-%m-%d')` on a normalized `datetime.date` and `strftime('%Y-%m-%dT%H:00:00Z')` on a UTC-forced datetime guarantees identical byte payloads regardless of input type.

**Additional controls**:
1. `compute_id()` is the **ONLY** hash producer. `semantic_linter` rule forbids `sha1`/`md5`/`hashlib` on identifier-generation paths outside `src/calibration/decision_group.py`.
2. **Write/refit gate**: `add_calibration_pair()` refuses blank IDs and
   `refit_platt.py` refuses VERIFIED rows with missing IDs. The current schema
   remains nullable for legacy DB compatibility.
3. **Coverage assertion**: SQL check after rebuild:
   ```sql
   SELECT COUNT(*) FROM calibration_pairs WHERE decision_group_id IS NULL;
   -- must be 0
   ```
4. **Stability audit**: after full rebuild, re-compute `decision_group_id` for a sample of rows from their stored components; any mismatch = bug. Sample 1000 random rows as sanity check.
5. **Round-trip regression test** (see §14 expanded test plan): same logical snapshot through 3 code paths must produce identical hash.

**On failure**: abort rebuild; fix the offending insert path.

### 3.4 Histogram boundary safety (±inf handling)

**What it prevents**: attempting to iterate over `range(b.low, b.high)` when `b.high = float('inf')` causes `OverflowError` or infinite loop. Must use **comprehension over histogram keys**, not range-based iteration.

**Where it runs**: `src/signal/ensemble_signal.py::p_raw_vector` and `scripts/rebuild_calibration_pairs_canonical.py` both call into the same Monte Carlo histogram code (per `zeus_math_spec.md §12.3` equivalence rule).

**Correct implementation**:
```python
def bin_probability_from_histogram(histogram: dict[int, int], bin_low: float, bin_high: float, total: int) -> float:
    """Sum histogram over [bin_low, bin_high]. Safe for ±inf edges."""
    # Use key comprehension, NOT range(bin_low, bin_high+1)
    count = sum(
        histogram[k] for k in histogram.keys()
        if bin_low <= k <= bin_high
    )
    return count / total
```

When `bin_low = -inf`: `-inf <= k` is True for every k, so the left edge is open.
When `bin_high = +inf`: `k <= inf` is True for every k, so the right edge is open.
No special-case branching needed.

**Forbidden alternatives**:
- `range(int(bin_low), int(bin_high) + 1)` — fails on `int(-inf)` / `int(inf)` with OverflowError
- `for k in range(bin_low, bin_high + 1)` — same failure
- Eager pre-computation of all integers in range — undefined for ±inf

**Unit test**: `bin_probability_from_histogram({70: 100, 80: 200}, -inf, 75, 300) == 100/300`.

### 3.5 JSON boundary safety for ±inf Bin values

**What it prevents**: `json.dumps(float('inf'))` produces `"Infinity"` which is illegal in RFC 8259 / ECMA-404 and crashes strict parsers in Go, Rust, Java, and Python's own `json.dumps(..., allow_nan=False)`. Any path where Bin data serializes via standard JSON (API exposure, backup export, IPC, contract extraction) would crash or silently corrupt.

**DB boundary (preserved as float)**: `Bin.low` / `Bin.high` remain Python `float` including ±inf in memory and as SQLite `REAL` columns. SQLite stores ±inf via IEEE 754 binary64; Python's `sqlite3` driver preserves them as `float`. No coercion to string.

Migration test: insert `(city='TEST', low=float('-inf'), high=float('inf'))`, select back, assert `isinstance(low, float) and math.isinf(low)` and same for `high`.

**JSON serialization boundary (sentinel encoding)**: no `Bin` containing ±inf may cross any `json.dumps()` call unless wrapped by the sanctioned helpers in `src/types/market.py`:

```python
# Integer sentinel convention:
# float('-inf') <-> sentinel -32768
# float('+inf') <-> sentinel +32767
_INF_LOW_SENTINEL  = -32768
_INF_HIGH_SENTINEL = +32767

def to_json_safe(b: Bin) -> dict:
    """Encode a Bin for JSON serialization. Replaces ±inf with integer sentinels."""
    return {
        'unit':  b.unit,
        'low':   _INF_LOW_SENTINEL  if math.isinf(b.low)  and b.low  < 0 else int(b.low),
        'high':  _INF_HIGH_SENTINEL if math.isinf(b.high) and b.high > 0 else int(b.high),
        'is_open_low':  b.is_open_low,
        'is_open_high': b.is_open_high,
    }

def from_json_safe(d: dict) -> Bin:
    """Decode a Bin from JSON. Restores sentinel integers to ±inf."""
    low  = float('-inf') if d['low']  == _INF_LOW_SENTINEL  else float(d['low'])
    high = float('+inf') if d['high'] == _INF_HIGH_SENTINEL else float(d['high'])
    return Bin(unit=d['unit'], low=low, high=high)
```

Rationale for integer sentinel over custom encoder approach: survives strict parsers in all languages (Go, Rust, Java) without schema awareness. The sentinel values (-32768, +32767) lie well outside any plausible real-world temperature range and are unambiguous.

**Semantic linter rule**: forbid `json.dumps(` where the argument could contain a `Bin` object, outside of code that explicitly calls `to_json_safe()` first.

**Scope decision**: TRAP B folds into Change H (±inf Bin support) rather than spawning a new Change N. The JSON safety helpers (`to_json_safe` / `from_json_safe`) are a natural extension of the `Bin` class boundary work already in Change H. No new letter is needed.

**On failure**: `json.dumps(..., allow_nan=False)` will raise `ValueError` at the call site, making the bug loud rather than silent.

---

## 4. Known defects that block collection

Each defect violates one or more guardrails. Must be fixed before any data is collected.

| # | Defect | File:line | Violates | Fix (Change letter) |
|---|---|---|---|---|
| D1 | **RESOLVED in Packet 1**: `SettlementSemantics` uses `wmo_half_up` and no longer calls `np.round` for WU settlement rounding | `src/contracts/settlement_semantics.py` | §3.1 | Verify before rebuild |
| D2 | **RESOLVED in Packet 1**: `_simulate_settlement` inherits WMO half-up via SettlementSemantics | `src/signal/ensemble_signal.py:153-154` | §3.1 | Follows from D |
| D3 | `init_schema` not idempotent on pre-K1 DB — K1 columns never added | `src/state/db.py:169` | (operational blocker) | Change **A** |
| D4 | `load_cities` has no lat/lon/timezone/country_code validation | `src/config.py:163` | (metadata integrity, supports §3.3) | Change **B** |
| D5 | `IngestionGuard.check_unit_consistency` Sub-check b skips unknown units | `src/data/ingestion_guard.py:143-179` | §3.1 (Rankine/unknown passes silently) | Change **C** |
| D6 | `IngestionGuard.validate()` else branch defaults unknown units to F | `src/data/ingestion_guard.py:378` | §3.1 | Change **C** |
| D7 | `check_seasonal_plausibility` trusts caller-supplied hemisphere | `src/data/ingestion_guard.py:258` | (metadata integrity) | Change **C** |
| D8 | Legacy `rebuild_calibration.py` used simplified local `p_raw`, not live `p_raw_vector` | `scripts/rebuild_calibration.py` (retired fail-closed) | (`zeus_math_spec.md §12.3` equivalence) | Change **F** |
| D9 | `calibration_pairs.decision_group_id` coverage unverified; legacy inserts may write NULL | `scripts/generate_calibration_pairs.py` (retired fail-closed) + other call sites | §3.3 | Change **G** |
| D10 | `Bin.low/high` ±inf representation unverified; outer-bin Polymarket markets may fail to load | `src/types/market.py` | §3.2, §3.4 | Change **H** |
| D11 | No `bin_topology_check` helper exists | (not yet implemented) | §3.2 | Change **I** |
| D12 | Logit clamping in Platt unverified; Monte Carlo with `p=0` or `p=1` may NaN the loss | `src/calibration/platt.py` | (`zeus_math_spec.md §6.1`) | Change **J** |
| D13 | Bootstrap may resample rows instead of decision_groups | `src/strategy/market_analysis.py:185-244` | §3.3 + math spec §8.2 | Change **K** |
| D14 | Multi-step TIGGE extraction (48-168h) not yet run; 0 JSON files in `tigge_ecmwf_ens_regions/` | (no extraction script output) | (data availability) | Change **L** |
| D15 | `ensemble_snapshots` DB empty; no GRIB → DB ingestor has been run | (0 rows) | (data availability) | Change **M** |
| D16 | `wu_daily_collector.py` omits `authority` from INSERT — live WU becomes dead for training | `src/data/wu_daily_collector.py:128-138` | (deferred to midstream packet — NOT this rebuild) | Deferred |
| D17 | `assert_settlement_value` bypassed in `harvester.py:652`, `replay.py:503` | (AGENTS.md line 94 contract violation) | (deferred to settlement audit packet) | Deferred |

**New defects added in v2**:

| # | Defect | File | Violates | Fix |
|---|---|---|---|---|
| D18 | `compute_id()` uses `.isoformat()` which produces different strings for `date`, naive `datetime`, and tz-aware `datetime` — same snapshot via different paths gets different hashes | `src/calibration/decision_group.py` (once created) | §3.3 | Change **G** (extended) |
| D19 | `json.dumps()` on Bin objects produces RFC 8259-invalid `"Infinity"` — crashes strict parsers | `src/types/market.py` (any JSON export path) | §3.5 | Change **H** (extended) |
| D20 | Training window includes asymmetric 70-day 24h-only tail (2023-10-23 → 2023-12-31) — distorts Platt B (lead_days slope) and long-lead CI | canonical rebuild training window config | §0.5 Principle 3 | §8 training window (config change only) |

**New defects added in v2.1**:

| # | Defect | File | Violates | Fix |
|---|---|---|---|---|
| D21 | `store.py:70-71` fallback generates plausible-looking non-null naive string when `decision_group_id is None` — structural monopoly is convention-based only | `src/calibration/store.py:70-71` | §3.3 | Change **G** (G.6 — delete fallback) |
| D22 | `market_analysis.py::_bin_probability` (line 314) uses range-based iteration on outer bins — silently mis-classifies after rebuild since rebuild uses histogram approach | `src/strategy/market_analysis.py:314-321` | `zeus_math_spec.md §12.3` equivalence | Change **N** |

**Rule**: Defects D1-D15, D18-D22 MUST be fixed before Step 0 passes. D16-D17 are deferred to future packets and do NOT block the upstream rebuild.

---

## 5. Code changes (A-N)

Letter-coded so you can approve individually and so agents can land one at a time.

### Change A — `src/state/db.py` `init_schema` idempotent
- Add `K1_OBSERVATION_COLUMNS` constant and `_add_columns_if_missing(conn, table, cols)` helper
- Call helper for `observations`, `settlements`, `calibration_pairs`, `platt_models`, `ensemble_snapshots`
- Add `schema_migrations` tracking table + row per `init_schema` call
- Fixes D3

### Change B — `src/config.py::load_cities` load-time validation
- Add `lat ∈ [-90, 90]`, `lon ∈ [-180, 180]`, `ZoneInfo(timezone)` construction, `country_code` matches ISO-3166-1 alpha-2
- Fail at import time with `ValueError`
- Fixes D4

### Change C — `src/data/ingestion_guard.py` unit + hemisphere rigor
- Line 143: `check_unit_consistency` adds Sub-check 0 rejecting `raw_unit not in ('C','F','K')`
- Line 378: `validate()` else branch replaced with `raise UnitConsistencyViolation`
- Line 258: `check_seasonal_plausibility` drops `hemisphere` parameter; re-derives from `cities_by_name[city].lat` via `hemisphere_for_lat()`
- Fixes D5, D6, D7

### Change D — `src/contracts/settlement_semantics.py` WMO half-up
- Add new rule value `"wmo_half_up"` to `rounding_rule` Literal
- `round_values` for `wmo_half_up` computes `np.floor(scaled + 0.5)` (not `np.round`)
- `default_wu_fahrenheit`, `default_wu_celsius`, `for_city` constructors all use `rounding_rule="wmo_half_up"`
- Add unit test: `round_values([52.5, 74.5, -0.5, -1.5, -2.5])` must equal `[53, 75, 0, -1, -2]`
- Fixes D1 (and by extension D2)

### Change E — `src/signal/ensemble_signal.py::_simulate_settlement` (no code change; derives from D)
- Current implementation already calls `self.settlement_semantics.round_values(values)` — fix propagates automatically once D lands
- Add regression test verifying Monte Carlo rounds match WU rule for known extremes

### Change F — retire legacy `scripts/rebuild_calibration.py`
- `scripts/rebuild_calibration.py` is retained only as a fail-closed tombstone.
- The active calibration-pair rebuild path is
  `scripts/rebuild_calibration_pairs_canonical.py`.
- The canonical path uses `src/signal/ensemble_signal.py::p_raw_vector_from_maxes`,
  `SettlementSemantics`, and canonical bin grids instead of the retired
  simplified local p_raw/bin-taxonomy path.
- Fixes D8 by removing the old write surface rather than redirecting through
  an ambiguous legacy command name.

### Change G — `calibration_pairs.decision_group_id` hash + audit (v2 extended + v2.1 fallback deletion)
- Add module `src/calibration/decision_group.py::compute_id(city, target_date, issue_time, source_model_version) -> str`
- Uses **explicit strftime templates** (not `.isoformat()`) per §3.3 canonical hash template:
  - `target_date` → normalized to `datetime.date` → `strftime('%Y-%m-%d')`
  - `issue_time` → forced to UTC-aware `datetime` → `strftime('%Y-%m-%dT%H:00:00Z')`
- Raises `TypeError` on naive `issue_time` (ambiguous timezone)
- Every `add_calibration_pair` call site must pass a non-null `decision_group_id` from this function
- Add `semantic_linter` rule forbidding `sha1/md5/hashlib` on identifier-generation paths outside `src/calibration/decision_group.py`
- Add SQL gate in `refit_platt.py`: refuse to fit if `COUNT(*) FROM calibration_pairs WHERE decision_group_id IS NULL > 0`
- Deprecate `scripts/generate_calibration_pairs.py` (superseded by
  `rebuild_calibration_pairs_canonical.py`)
- **G.6 (new in v2.1): DELETE the fallback at `src/calibration/store.py:70-71`** that generates `f"{city}|{target_date}|{forecast_available_at}|lead={lead_days:g}"`. Replace with `raise TypeError('decision_group_id must be provided explicitly; call src.calibration.decision_group.compute_id() to generate it')`. This makes the monopoly structural, not caller-policed. Without this deletion, the NOT NULL constraint is satisfied by a plausible-looking naive string — the defect category survives.
- Note on D18 (hash fragility): the fix requires BOTH the new `compute_id()` function (G.1-G.5) AND the deletion of the legacy fallback (G.6). One without the other leaves the defect live.
- Fixes D9, D18, D21

### Change H — `src/types/market.py::Bin` outer-boundary ±inf support + JSON safety (v2 extended)
- `Bin.low` and `Bin.high` type: `float` (supports `-inf`, `+inf`)
- `Bin.__post_init__` validates: `low ≤ high`; when `low = -inf` `high` must be integer or `+inf`; same for `high`
- Add `Bin.contains(v: int) -> bool`: `self.low <= v <= self.high`
- Add `Bin.is_open_low` / `is_open_high` convenience properties
- DB column types for `market_events.range_low`, `range_high`: `REAL` (not INTEGER; SQLite supports IEEE ±inf)
- **New in v2**: Add `to_json_safe(b: Bin) -> dict` and `from_json_safe(d: dict) -> Bin` at module level (see §3.5 for implementation). Integer sentinels: `float('-inf')` ↔ -32768, `float('+inf')` ↔ +32767.
- **New in v2**: Add `semantic_linter` rule forbidding bare `json.dumps()` on paths that may receive a `Bin` object outside `to_json_safe()`.
- SQLite migration test: insert/select ±inf, assert preserved as Python `float`.
- **Migration strategy (v2.1 note)**: a quick grep for `b.low is None` call sites should be done at execution time to determine atomic vs staged migration risk. If ≤5 call sites, atomic migration (all in one Change H commit) is fine. If >10 call sites, staged migration is safer: (1) accept `float('±inf')` alongside `None` and migrate consumers one at a time, (2) then in a follow-on commit deprecate `None`. The executor should run this grep before landing Change H and choose accordingly. This decision is deferred to execution time because it is purely a blast-radius judgment, not an architectural choice.
- Fixes D10, D19

### Change I — `src/types/market.py::validate_bin_topology` helper
- New function implementing the §3.2 validation (sorted bins, -inf left edge, +inf right edge, no integer gaps)
- Raises `BinTopologyError` on any failure
- Called from `rebuild_calibration_pairs_canonical.py`; live evaluator gate
  wiring remains pending.
- Unit tests cover: happy case; missing -inf; missing +inf; gap between bins; empty set
- Fixes the canonical-rebuild side of D11; live-market gate remains open.

### Change J — `logit_safe` implemented; eps policy still open
`src/calibration/platt.py` now centralizes finite logit clipping in
`logit_safe(...)`. The code still uses the conservative `P_CLAMP_LOW = 0.01`
default; the open decision is whether a measured refit experiment should move
that to the math-spec `1e-6`.

### Change K — Platt bootstrap by decision_group implemented
`ExtendedPlattCalibrator.fit(...)` now accepts `decision_group_ids` and
bootstraps by sampled decision groups when provided. ENS member bootstrap in
`market_analysis.py` remains member-level because it represents σ_ensemble.

### Change L — Run `scripts/extract_tigge_region_member_vectors_multistep.py` against existing `raw/tigge_ecmwf_ens_regions/` (no code change, script already exists)
- Script is in `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/`
- Consumes regional GRIBs (48-168h lead), produces per-city per-lead JSON extracts
- **No network fetch** — all GRIBs already on disk
- Before running: verify regional cache directory is readable, script has been audited for correctness
- **Sentinel pattern (new in v2)**: write `.extracted_ok` per dated directory on success; re-runs skip sentinel-present dirs; crash leaves only `.tmp` orphan, not corrupt final file
- Fixes D14

### Change M — `scripts/ingest_grib_to_snapshots.py` (new script)
- For each (city, target_date, lead_hours) JSON extract:
  - Load 51-member list
  - Convert Kelvin → city settlement unit if needed (per `zeus_math_spec.md §1.5`)
  - Validate via `src/data/rebuild_validators.py::validate_ensemble_snapshot_for_calibration` (existing K1 validator)
  - Write row to `ensemble_snapshots` with `authority='VERIFIED'` and `provenance_metadata` populated
  - Populate `data_version` / `model_version` so `source_model_version` can be derived for `decision_group_id` (§3.3)
- Idempotent via `INSERT OR REPLACE` keyed on `(city, target_date, issue_time, lead_hours)`
- **M.7 (new in v2.1)**: `issue_time` MUST be written to SQLite as UTC-explicit ISO string via `dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')`. NEVER use `.isoformat()` or naive datetime. Rationale: if `issue_time` is written as naive or as `+00:00` suffix, then when `compute_id()` reads it back via `datetime.fromisoformat()`, it produces a naive or offset-aware datetime respectively — and the round-trip string from strftime will differ. Using the explicit `Z`-suffix format guarantees `datetime.fromisoformat()` round-trip produces a UTC-aware datetime that strftime formats identically. Verify round-trip in integration test (Step 3 gate).
- Fixes D15

### Change N — live bin probability equivalence implemented
`market_analysis._bin_probability` and `ensemble_signal.p_raw_vector_from_maxes`
now route through shared `src/types/market.py` bin containment/count helpers.
- Fixes D22 — **deferred**

---

## 6. Import sequence

Each step has (a) a gate condition, (b) a rollback criterion, (c) the guardrail(s) it exercises.

### Step 0 — Pre-flight
- All 14 code changes (A-N) landed on `data-improve` branch
- Full `pytest tests/` run green (baseline 1120 + new tests for Changes A-N)
- Guardrails §3.1 static lint passes (no `np.round`/`round` on settlement paths)
- `semantic_linter` rules for hashlib isolation and json.dumps Bin boundary pass
- **Gate**: all green; otherwise stop and fix
- **Rollback**: n/a (read-only checks)

### Step 1 — Schema migration (destructive, one-shot)
- Take full DB backup: `sqlite3 db.sqlite ".backup db_backup_prestep1.sqlite"` and verify backup file size is non-zero
- Test Step 4 (WU backfill script) against a **sandbox DB clone** before running on production DB
- Run `scripts/migrate_add_authority_column.py` with `ZEUS_DESTRUCTIVE_CONFIRMED=1`
- Adds authority column to 5 tables + wipes non-TIGGE historical rows per existing K4-code script
- Run `init_schema` (Change A) — ensures all K1 + authority columns + `schema_migrations` tracking
- **Gate**: `schema_migrations` table has row with `schema_version='K1+A+WMO'`; all 5 tables have authority column; no unintended wipe
- **Rollback**: `sqlite3 db_backup_prestep1.sqlite ".backup db.sqlite"` (restore from pre-step backup)
- **Guardrails exercised**: §3.3 (schema carries decision_group_id; API/refit
  gates enforce non-null for active learning rows)

### Step 2 — Multi-step GRIB extraction (Change L)
- Run `extract_tigge_region_member_vectors_multistep.py` on the regional cache
- Produces per-city JSON for lead_hours ∈ {48,72,96,120,144,168} for every dated GRIB in `tigge_ecmwf_ens_regions/`
- No network fetch — all GRIBs already on disk; idempotent via per-dir `.extracted_ok` sentinels
- **Gate**: `find raw/tigge_ecmwf_ens_regions -name "*.json"` returns non-zero count for the full 2024-01-01 → 2026-03-09 window; per-city JSON count matches expected dates × cities
- **Rollback**: delete newly created JSON files (identifiable by the same extraction run); delete `.extracted_ok` sentinels for affected dirs
- **Guardrails exercised**: none yet (data sitting on disk)

### Step 3 — Ingest GRIB extracts → `ensemble_snapshots` (Change M)
- For each (city, target_date, lead_hours) across both regional (48-168h) and per-city (24h) JSON:
  - Load 51 members
  - Convert Kelvin → city unit if needed
  - Validate via `validate_ensemble_snapshot_for_calibration`
  - Write with `authority='VERIFIED'`, populate `model_version` and `data_version`
  - Write `issue_time` as UTC-explicit ISO string per M.7
- **Gate**: `SELECT COUNT(*) FROM ensemble_snapshots WHERE authority='VERIFIED'` matches expected row count; no NaN members; every row has `model_version` and `data_version` populated; round-trip `issue_time` + derived source version → `compute_id()` test passes for 100 sampled rows
- **Rollback**: restore DB backup or delete rows by the audited ingest manifest/run
  metadata once task #53 defines it; current schema has no `rebuild_run_id` on
  `ensemble_snapshots`.
- **Guardrails exercised**: §3.1 (via rebuild_validators Kelvin check; the Change D-fixed rounding applies if any conversion happens)

### Step 4 — Historical WU daily backfill → `observations`
- Run `scripts/backfill_wu_daily_all.py --start 2024-01-01 --end 2026-03-09 --source wu_icao_history`
- **Note (v2 change)**: start date is 2024-01-01, NOT 2023-10-23. The 70-day 24h-only tail is excluded from the primary training corpus (see §8). Run separately only after §8 post-rebuild evaluation.
- Skip HK (deferred); run for 45 cities
- Each row goes through `ObservationAtom` + `IngestionGuard` 5 layers (with Changes C fixes)
- Writes with `authority='VERIFIED'`
- **Gate**: per-city coverage ≥ 99% of expected target dates; no `IngestionRejected` errors on clean days; `availability_fact` records rejections for explainable reasons only
- **Rollback**: `DELETE FROM observations WHERE source='wu_icao_history' AND rebuild_run_id=<this run>`
- **Guardrails exercised**: §3.1 (WMO rounding in assert_settlement_value, via wu_daily_collector which already calls it); §3.3 indirectly (observations don't have decision_group_id but feed settlements)

### Step 5 — Derive `settlements` from VERIFIED observations
- Run `scripts/rebuild_settlements.py --no-dry-run`
- Reads VERIFIED observations; applies `SettlementSemantics.assert_settlement_value` (now Change D-fixed); writes VERIFIED settlements
- **Gate**: `settlement count ≈ observation count`; no NaN `settlement_value`; every settlement has `settlement_source` populated
- **Rollback**: restore DB backup or delete the Step 5 settlement slice by
  `(city, target_date, authority)` after dry-run accounting; current schema has
  no `rebuild_run_id` on `settlements`.
- **Guardrails exercised**: §3.1 (runtime assertion — every settlement_value written goes through `wmo_half_up`)

### Step 6 — Derive `calibration_pairs` (Change F — canonical Bin/Monte Carlo)
- Run `scripts/rebuild_calibration_pairs_canonical.py`
- For each VERIFIED ensemble snapshot with a matching VERIFIED observation:
  - Load VERIFIED `ensemble_snapshots` for all (issue_time, lead_hours) pairs targeting that date
  - Build the fixed canonical grid for the city's settlement unit; fixed grids
    are topology-tested in `tests/test_calibration_bins_canonical.py`
  - For each snapshot, run `p_raw_vector_from_maxes` once against the canonical grid
  - Compute `decision_group_id = compute_id(city, target_date, issue_time, source_model_version)` (Change G §3.3 — explicit strftime, UTC-normalized)
  - Write canonical pair rows with explicit nonblank `decision_group_id`
- **Gate**:
  - per (city × season) bucket pair count ≥ 15 OR explicitly flagged insufficient (no silent dropout)
  - `SELECT COUNT(*) FROM calibration_pairs WHERE authority='VERIFIED' AND (decision_group_id IS NULL OR decision_group_id='') = 0`
  - Hash stability sample: 1000 rows re-computed and verified (3 code paths per §14 TRAP A test)
  - canonical grid topology tests pass for both F and C grids
- **Rollback**: `DELETE FROM calibration_pairs WHERE bin_source='canonical_v1'`
- **Guardrails exercised**: §3.2 (topology scan), §3.3 (hash stability), §3.4 (histogram boundary), §3.5 (no Bin JSON leakage), §3.1 (indirect via Monte Carlo rounding)

### Step 7 — Refit Platt models
- Run `scripts/refit_platt.py`
- Reads VERIFIED pairs with `logit_safe` (Change J)
- Fits Extended Platt per (city × season) with decision_group-weighted loss (Change G enables this via `n_eff`)
- Writes VERIFIED platt_models with WMO-consistent maturity gates
- **Gate**: every expected bucket has either a fitted model OR insufficient-maturity skip; no silent failure; no NaN parameters
- **Rollback**: restore `platt_models` from snapshot taken before Step 7
- **Guardrails exercised**: §3.3 (n_eff used; Change G enables)

### Step 8 — Verification (AGENTS.md §1 standards)
- Run `python scripts/run_replay.py --mode wu_settlement_sweep --start 2024-01-01 --end 2026-03-09`
- **Hard gates** (any failure = full rollback):
  - `probability_group_integrity` passes for every (city × season)
  - `mean_p_raw_on_actual_yes > mean_p_raw_on_actual_no` per AGENTS.md line 59
  - Platt fit succeeds for every bucket with n_eff ≥ 15
- **Soft gates** (failure = stop and escalate to user, not auto-rollback):
  - Brier skill vs climatology regression within 0.05 of pre-rebuild baseline
  - No city's valid-group binary skill deviates >2σ from median
  - Per-lead-days calibration slope reasonable (A coefficient in [0.3, 2.5] expected range)
  - **New in v2 (TRAP C gate)**: per-bucket A/B/C coefficient shift report between square-matrix-only fit vs candidate fit (see §8.1 below)
- **Rollback**: full DB backup restore if any hard gate fails

### Step 8.1 — Square-matrix vs 70-day-tail evaluation (TRAP C post-rebuild gate)

This step runs **after** Step 7 succeeds on the square-matrix window. It evaluates whether the 70-day 24h-only tail (2023-10-23 → 2023-12-31) can safely be added.

**Procedure** (corrected in v2.1 — compare all 3 coefficients, not just A and C):
1. Baseline: Platt from Step 7 uses square-matrix window only → parameters `(A_ref, B_ref, C_ref)` per (city × season)
2. Candidate: backfill WU 2023-10-23 → 2023-12-31 (24h lead only); ingest to `observations`; derive `settlements`; generate `calibration_pairs` for those 70 days with `lead_days=1` only; fit Platt on union of square-matrix + tail → `(A_cand, B_cand, C_cand)`
3. **Bootstrap CI comparison — all 3 coefficients** (v2.1 correction: the v2 procedure of holding B fixed while comparing A and C was statistically wrong; A and C are jointly estimated with B, so if B shifts due to the tail data, A and C shifts partly reflect the B change, not just the tail's direct contribution. The correct procedure compares all three independently):
   - Compute bootstrap 95% CI for `A_ref`, `B_ref`, `C_ref` by resampling decision_groups from the square-matrix corpus
   - Compute bootstrap 95% CI for `A_cand`, `B_cand`, `C_cand` by resampling decision_groups from the combined corpus
   - Additionally check B independently: if `B_cand` 95% CI does not overlap `B_ref` 95% CI (B shifted), the tail is already contaminating the temporal skill decay estimate and should be excluded regardless of A/C stability
4. **Accept tail if** ALL of the following hold: (a) 95% CI overlap ≥ 80% for A, (b) 95% CI overlap ≥ 80% for B, (c) 95% CI overlap ≥ 80% for C, AND (d) one-sided t-test p ≥ 0.05 for all three parameters (fail-to-reject H0 that tail shifts any parameter)
5. **Reject tail (permanently exclude)** if ANY of A, B, or C shows: 95% CI overlap < 80% OR one-sided t-test p < 0.05
6. Log decision with bootstrap statistics for all 3 coefficients to `schema_migrations` or a dedicated `training_window_audit` table

**Why this gate exists**: Extended Platt fits B (temporal skill decay) globally across all lead_days. Adding 70 extra days at lead_days=1 while the rest of the corpus has 7 lead values creates an asymmetric distribution that over-anchors the B coefficient toward short-lead patterns and under-disperses long-lead CIs. The gate detects this empirically before contaminating production. Comparing all 3 coefficients ensures B contamination itself is detected, not masked by a "hold B fixed" assumption.

### Step 9 — Cleanup + commit
- Cleanup: no temp tables left; `availability_fact` has clean log; `schema_migrations` records the K1+A+WMO version
- Commit series on `data-improve` per §15 commit strategy

---

## 7. Current data state (reality snapshot)

### 7.1 TIGGE disk

```
/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/
├── tigge_ecmwf_ens_regions/   (11 GB, multi-step 48-168h)
│   ├── americas/               (4.0 GB, ~153 dated subdirs, some empty)
│   ├── asia/                   (2.1 GB, ~152 dated subdirs)
│   ├── europe_africa/          (3.2 GB, ~153 dated subdirs)
│   └── oceania/                (1.4 GB, ~152 dated subdirs)
├── tigge_ecmwf_ens/            (2.2 GB, per-city 24h lead, JSON already extracted)
├── ecmwf_open_ens/             (764 MB, Open-Meteo backup)
└── solar/                       (14 MB, diurnal/solar)
```

Regional GRIB date range: 2024-01-01 → 2026-03-09 (but some date dirs are empty — real data points ~540-577).
Per-city 24h date range: 2023-10-23 → 2026-04-09 (daily, ~900 subdirs per city).

### 7.2 TIGGE download pipeline

Active. `tigge_full_history_pipeline.py` running as background process (investigator #1 saw PID 182). Two cron jobs: hourly full-history fill, 15-minute daily forward-fill. Coverage **will extend past 2026-03-09 over time** — rebuild should be re-runnable as new data lands.

### 7.3 DB state

- `ensemble_snapshots`: 0 rows (GRIBs on disk not yet ingested)
- `observations`: last-known state per SESSION_HANDOFF has contaminated rows that will be wiped by Step 1
- `settlements`: will be re-derived in Step 5
- `calibration_pairs`: will be re-derived in Step 6
- `platt_models`: will be re-fit in Step 7

### 7.4 WU endpoints

- **Backfill (this rebuild)**: `backfill_wu_daily_all.py` uses `historical.json` ICAO station endpoint
- **Live daemon (not this rebuild)**: `wu_daily_collector.py` uses `timeseries.json` geocode endpoint — **different endpoint, potentially different values** for the same (city, date); flagged as D18 deferred concern

---

## 8. Training window

**Primary window (square matrix)**: 2024-01-01 → 2026-03-09 — the intersection of:
- Regional multi-step (48-168h) coverage (2024-01-01 → 2026-03-09, ~150 effective dates)
- Per-city 24h coverage (daily throughout the same period)

This window produces a **square data matrix**: each decision_group has all 7 lead_days available (lead_days ∈ {1,2,3,4,5,6,7}), ensuring the Platt B coefficient (temporal skill decay) is estimated from a symmetric lead distribution.

**Square-matrix principle**: training data must maintain shape-symmetry across the lead_days dimension. An asymmetric corpus (more rows at lead_days=1 than at lead_days=7) corrupts the B coefficient and produces under-dispersed CIs at long lead — exactly when reliable uncertainty matters most for sizing.

**Expected decision_group count**:
- ~150 regional dates × 46 cities × ~6 lead steps (48-168h) = ~41,400 multi-step decision groups
- Plus per-city 24h daily coverage for the same 150 dates = ~6,900 24h decision groups
- Total: ~48,300 decision groups
- Per (city × season) bucket: ~48,300 / (46 × 4) = ~263 decision groups average
- Well above n_eff = 50 maturity threshold for "standard fit" (C_reg = 1.0)

**Extra 24h-only coverage (held out)**: 2023-10-23 → 2023-12-31 (~70 days, ~3,220 decision groups at lead_days=1 only). This is **NOT included in the baseline training run**. It is held out as an experimental supplement and evaluated post-rebuild via the §8.1 gate:
- If gate passes (no statistically significant shift to A, B, or C): include in production refit
- If gate fails (any coefficient CI overlap < 80% or t-test p < 0.05): permanently exclude

**Not included**: days where regional GRIB failed to download (empty dated dirs). Quarantined by Step 2 gate.

**Re-run horizon**: when TIGGE pipeline completes more dates (past 2026-03-09), the rebuild is re-runnable to extend the window. No architectural changes needed.

---

## 9. Acceptance — what "success" means

The rebuild is accepted if and only if Step 8 verification passes all hard gates AND Claude-human review agrees on soft gates.

Hard gates (automatic pass/fail):
1. `probability_group_integrity` = PASS for every (city × season) bucket
2. `mean_p_raw_on_actual_yes > mean_p_raw_on_actual_no` per AGENTS.md line 59
3. Every (city × season) bucket with `n_eff ≥ 15` has a fitted Platt model with non-NaN parameters
4. Zero rows in `calibration_pairs` with `decision_group_id IS NULL`
5. Zero violations of `semantic_linter` WMO rule on settlement paths
6. Full `pytest` green (baseline 1120 + new tests for A-N)

Soft gates (escalate to user on failure):
1. Brier skill vs climatology within 0.05 of pre-rebuild baseline (if known; otherwise informational)
2. No city's valid-group binary skill is >2σ from the across-city median
3. Per-lead-days calibration slope looks reasonable (A coefficient in [0.3, 2.5] expected range)
4. Per (city × season) bucket pair count distribution looks reasonable (no bucket at exactly the minimum 15 which would suggest truncation)
5. **New in v2 (TRAP C)**: per-bucket A/B/C shift between square-matrix-only fit and candidate fit within the §8.1 acceptance bounds (all 3 coefficients)

---

## 10. Out of scope (deferred to future packets)

These are real gaps but not this rebuild:

| Gap | Deferred to packet |
|---|---|
| `wu_daily_collector` writes `authority=UNVERIFIED` silently, live WU dead for training | Live observation promotion |
| `assert_settlement_value` bypassed in `harvester.py:652`, `replay.py:503`, `calibration/store.py:75` | Settlement contract audit |
| HK `settlement_source_type='hko'` declared but no HKO API client exists | HK settlement path packet |
| WU geocode (live) vs ICAO (backfill) endpoint discrepancy | WU source unification |
| `winning_bin` (Polymarket Gamma) vs `settlement_value` (WU) no cross-validation | Settlement cross-check |
| Season label convention split between `manager.py` flip-normalized and `ingestion_guard.py` calendar | Typed season contract |
| `refit_platt` Platt coefficient drift gate | refit_platt hardening |
| Future SPOF defense via WU ICAO × IEM ASOS triangulation (NOT WU × TIGGE — that's a category error) | Authority SPOF defense |
| Empirical-Bayes partial pooling, EMOS distributional correction, full-family FDR, correlation shrinkage, day0 two-stage residual model | All in `zeus_math_spec.md §15` deferred |

---

## 10.1 Post-this-packet (at-recalibrate-time packet)

**Trigger**: TIGGE download completes → `ensemble_snapshots` holds VERIFIED rows for the full square-matrix window 2024-01-01 → 2026-03-09 → ready to run `refit_platt.py`.

**Why split**: none of these changes affect data that is collected or written. They affect how the Platt fit consumes that data. There is zero cost to deferring them until the exact moment before running recalibrate — and substantial cost to doing them now: each forces re-fit of existing Platt models that will be thrown away anyway when the rebuild completes.

This packet is strictly **collect + store code preparation**. The at-recalibrate-time packet picks up here.

### Post-packet changes

| Original label | Scope | Target file(s) | Blocker / prerequisite |
|---|---|---|---|
| **Change J** (eps alignment) | `logit_safe` exists; choose whether to tighten the default eps from `0.01` to `1e-6` after measuring refit impact | `src/calibration/platt.py` | User/math decision |
| **Change K** (Platt bootstrap by decision_group) | Implemented for `ExtendedPlattCalibrator.fit(..., decision_group_ids=...)` | `src/calibration/platt.py` | Refit must pass group IDs |
| **Change N** (live `_bin_probability` histogram) | Implemented through shared bin value helper | `src/strategy/market_analysis.py`, `src/types/market.py` | Validate on refit dry-run |
| **Change G.7** (n_eff in manager) | Implemented in manager/refit bucket counts | `src/calibration/manager.py`, `scripts/refit_platt.py` | Requires populated IDs |

### Deferred relationship tests (already in `tests/test_data_rebuild_relationships.py` plan, then pruned out of this packet)

- **R2.2** `test_r2_live_bin_probability_equals_histogram_lookup` — requires Change N; covered by post-packet
- **R7** `test_r7_platt_fit_survives_p_raw_boundary_extremes` — Platt fit consumer invariant; currently GREEN (existing 0.01 clipping works), post-packet Change J may tighten to 1e-6 at which point this test re-green-locks the new eps
- **R8** `test_r8_platt_bootstrap_preserves_decision_group_integrity` — requires Change K corrected-target
- **R9** `test_r9_maturity_gate_counts_decision_groups_not_rows` — requires Change G.7

### Explicit scope boundary

When the at-recalibrate-time packet runs, it inherits everything this packet leaves behind:
- `calibration_pairs` rows with `decision_group_id` populated by `compute_id()` (Change G.1–G.6, in scope here)
- `Bin` type supports ±inf edges (Change H, in scope here)
- `validate_bin_topology` helper exists (Change I, in scope here)
- `rebuild_calibration_pairs_canonical.py` produces pairs via WMO Monte Carlo
  histogram (Change F, in scope here)
- `ensemble_snapshots` rows written with UTC-explicit `issue_time` (Change M.7, in scope here)

The post-packet's job is to make Platt fit consume those inputs correctly. No data migration, no backfill — just code changes to 3 files (`platt.py`, `manager.py`, `market_analysis.py`) and one test file re-green cycle.

---

## 11. Cross-references

- `AGENTS.md` §1 — domain authority. Contains the patched WMO rounding rule.
- `docs/reference/zeus_math_spec.md` v2 — math contract. Every rule in this plan must be consistent with it.
- `docs/reference/statistical_methodology.md` — detailed statistics (Chinese). Rounding section patched.
- `docs/reference/quantitative_research.md` — market microstructure and domain rationale.
- `docs/reference/zeus_domain_model.md` — 16-city worked example of the probability chain.
- Archived packet `docs/archives/work_packets/branches/data-improve/data_rebuild/2026-04-13_zeus_data_improve_large_pack/DB rebuild/` — investigator reports (zeus-understanding-*.md), tracer evidence (data-flow-trace-*.md), K5-refactor _superseded/ files. Historical reference; not authoritative.

---

## 12. What I need from you

- **Approve Changes A-N scope** (or veto any individual letter) — note that Changes G and H are extended in v2/v2.1 to address D18/D19/D21 respectively; Change N is new in v2.1 for D22
- **Approve training window**: 2024-01-01 → 2026-03-09 primary (square matrix only); 2023-10-23 → 2023-12-31 held out pending §8.1 evaluation
- **Approve acceptance standards** in §9 (or change thresholds)
- After those 3 approvals, relationship tests first (per methodology: relationship tests → implementation → function tests), then per-change commit packs, each going through reviewer + critic gates before landing on `data-improve`

**Nothing runs against the real DB until every Change A-N is committed and Step 0 passes green.**

---

## 13. ADR (Architecture Decision Record)

**Decision**: Full structural rebuild with 14 letter-coded changes (Option A), square-matrix training window (2024-01-01 → 2026-03-09), with the 70-day 24h-only tail (2023-10-23 → 2023-12-31) held out pending post-rebuild empirical evaluation.

**Decision Drivers**:
1. TIGGE regional coverage starts 2024-01-01; extending before that date produces asymmetric lead distribution
2. User's structural-fix methodology forbids patching symptoms — each Change must eliminate a defect category
3. decision_group independence invariant is the highest-order correctness requirement; any hash instability defeats the entire statistical chain

**Alternatives considered**:
- Incremental patch-and-run (Option B): rejected because it cannot certify the independence invariant; any post-run defect discovery requires full pipeline re-run anyway
- Include 2023-10-23 → 2023-12-31 in baseline: rejected because asymmetric lead distribution distorts B (temporal skill decay) and long-lead CIs; held out for empirical evaluation instead
- Staged Bin migration (for Change H): instead of atomically migrating all `Bin.low/high` from `None` to `float` in one commit, accept `float('±inf')` alongside `None` first, migrate consumers one at a time, then deprecate `None`. This reduces the blast radius of Change H, the highest-risk single-file change. Atomic migration was provisionally chosen as simpler, but the executor should grep for `b.low is None` call sites before landing Change H. If ≤5 call sites, atomic is fine; if >10, staged is safer. This decision is deferred to execution time.

**Why chosen**: Option A produces certified training data (`authority='VERIFIED'`, hash-stable `decision_group_id`, WMO-correct rounding at every site) that can be audited and re-verified. Option B would produce uncertified data requiring re-run. The cost difference is upfront complexity vs downstream uncertainty.

**Consequences**: 14 Changes must all land before any data is touched. The pipeline is fully idempotent; re-runs are safe as TIGGE coverage extends.

**Follow-ups**: (a) Post-rebuild §8.1 evaluation to determine if 70-day tail is safe to add; (b) D16-D17 deferred defects (live WU authority, harvester bypass) require separate packets; (c) WU ICAO vs geocode endpoint discrepancy requires WU source unification packet; (d) executor must decide atomic vs staged Bin migration based on `b.low is None` grep count at Change H time.

---

## 14. Expanded test plan

### 14.1 Unit tests per Change (minimum 3 per Change, mapped to defect)

**Change A** (init_schema idempotent):
1. `test_init_schema_idempotent_on_fresh_db` — run twice, assert no error, assert `schema_migrations` has 2 rows
2. `test_init_schema_adds_missing_K1_columns` — start with pre-K1 schema, run init_schema, assert K1 columns exist
3. `test_init_schema_does_not_wipe_existing_rows` — insert a row, run init_schema, assert row survives

**Change B** (load_cities validation):
1. `test_load_cities_rejects_invalid_lat` — city with lat=91 raises ValueError
2. `test_load_cities_rejects_bad_timezone` — invalid timezone string raises ValueError at import
3. `test_load_cities_rejects_bad_country_code` — 3-letter country code raises ValueError

**Change C** (IngestionGuard unit + hemisphere rigor):
1. `test_ingestion_guard_rejects_rankine` — raw_unit='R' raises UnitConsistencyViolation, not silent pass
2. `test_ingestion_guard_rejects_unknown_unit` — raw_unit='X' raises, not defaults to F
3. `test_ingestion_guard_derives_hemisphere_from_lat` — southern-hemisphere city with wrong caller-supplied hemisphere is corrected via lat lookup

**Change D** (WMO half-up):
1. `test_wmo_round_half_up_verification_table` — full table from `zeus_math_spec.md §1.2`: [52.5→53, 74.5→75, -0.5→0, -1.5→-1, -1.5→-1, -2.5→-2, -3.5→-3]
2. `test_settlement_semantics_default_constructors_use_wmo` — `default_wu_fahrenheit()` and `default_wu_celsius()` both have rounding_rule='wmo_half_up'
3. `test_round_values_rejects_bankers` — assert `round_values([74.5]) == [75]` not 74 (banker's would give 74)

**Change E** (Monte Carlo WMO propagation):
1. `test_simulate_settlement_at_boundary` — member=74.5°F, sigma=0 (noiseless), output=75 (not 74)
2. `test_simulate_settlement_negative_boundary` — member=-1.5°F, sigma=0, output=-1 (not -2)
3. `test_monte_carlo_rounding_matches_settlement_semantics` — run MC with sigma=0 on 51 identical members at 74.5; assert all reported integers are 75

**Change F** (legacy rebuild fails closed; canonical rebuild owns live pipeline):
1. `test_legacy_rebuild_calibration_fails_closed` — executing
   `scripts/rebuild_calibration.py` returns non-zero, prints the canonical
   replacement, and performs no DB work.
2. `test_canonical_rebuild_imports_live_mc_path` — assert
   `rebuild_calibration_pairs_canonical.py` imports `p_raw_vector_from_maxes`,
   `SettlementSemantics`, and canonical grid helpers.
3. `test_canonical_rebuild_requires_write_gates` — assert canonical script keeps
   `--force` and `--allow-unaudited-ensemble` gates.

**Change G** (decision_group hash stability — TRAP A):
1. `test_compute_id_explicit_strftime_date_types` — same (city, target_date as date, issue_time as UTC datetime) produces same hash as (city, same date as datetime, same issue_time via DB round-trip)
2. `test_compute_id_rejects_naive_issue_time` — naive datetime raises TypeError
3. `test_compute_id_stability_across_paths` (TRAP A regression test) — same logical snapshot via 3 explicit paths must produce identical hash:
   - **Path 1**: Direct Python construction: `datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)`
   - **Path 2**: SQLite TEXT round-trip: write `issue_time` as `'2024-01-15T12:00:00Z'`, read back via `sqlite3` cursor, re-parse via `datetime.fromisoformat()`, then call `compute_id()`
   - **Path 3**: SQLite TEXT round-trip: same write, read back, re-parse via `datetime.strptime('%Y-%m-%dT%H:%M:%SZ')`, then call `compute_id()`
   - All three must produce identical `decision_group_id`. (Note: JSON round-trip is trivially identical for strings and not a meaningful fragility test.)
4. `test_compute_id_semantic_linter_violation` — grep the codebase for `hashlib` / `sha1` outside `decision_group.py`; assert zero hits
5. `test_store_fallback_deleted` — assert `src/calibration/store.py` does not contain the substring `f"{city}|{target_date}|{forecast_available_at}|lead=`; assert `add_calibration_pair` raises `TypeError` when called with `decision_group_id=None`

**Change H** (Bin ±inf + JSON safety — TRAP B):
1. `test_bin_sqlite_preserves_inf` (TRAP B regression test) — insert `Bin(low=-inf, high=75)` to DB, select back, assert `isinstance(low, float) and math.isinf(low) and low < 0`
2. `test_bin_json_roundtrip_preserves_inf` (TRAP B regression test) — `Bin(low=-inf, high=75)` → `to_json_safe()` → `json.dumps(..., allow_nan=False)` must NOT raise; round-trip via `from_json_safe()` must yield `math.isinf(result.low) and result.low < 0` and `result.high == 75.0`
3. `test_bin_to_json_safe_no_nan_inf_in_output` — `json.dumps(to_json_safe(Bin(-inf, +inf)), allow_nan=False)` does not raise and produces valid RFC 8259 JSON
4. `test_bin_contains_open_edge` — `Bin(low=-inf, high=75).contains(-9999) == True`; `Bin(low=75, high=+inf).contains(99999) == True`

**Change I** (validate_bin_topology):
1. `test_validate_bin_topology_happy_path` — bins covering (-inf, 70], (71, 75], (76, +inf) passes
2. `test_validate_bin_topology_missing_inf_low` — leftmost bin starts at 65, not -inf → raises BinTopologyError
3. `test_validate_bin_topology_gap` — gap between (65, 70] and (72, +inf) → raises BinTopologyError
4. `test_validate_bin_topology_empty` — empty list raises BinTopologyError

**Change J** (logit clipping):
1. `test_logit_safe_zero` — `logit_safe(0.0)` is finite (not -inf)
2. `test_logit_safe_one` — `logit_safe(1.0)` is finite (not +inf)
3. `test_logit_safe_half` — `abs(logit_safe(0.5)) < 1e-9`
4. `test_platt_fit_with_extreme_p_raw` — dataset with P_raw=0.0 and P_raw=1.0 rows does not NaN the loss

**Change K** (bootstrap by decision_group):
1. `test_bootstrap_resamples_groups_not_rows` — dataset with 3 decision_groups of 5 bins each; bootstrap must produce samples with ≤3 distinct groups per bootstrap draw
2. `test_bootstrap_group_ci_wider_than_row_ci` — correlated rows within groups produce wider CI when resampling by group vs row
3. `test_bootstrap_bins_within_group_kept_together` — assert that when decision_group G is sampled, all its bin rows appear together

**Change L** (TIGGE extraction idempotency):
1. `test_extraction_sentinel_prevents_reprocessing` — create `.extracted_ok` for a dated dir; run extractor; assert extraction function called 0 times for that dir
2. `test_extraction_atomic_rename_on_output` — simulate crash mid-file; assert no corrupt final file (only `.tmp` orphan)
3. `test_extraction_count_matches_expected` — mock GRIB dir with known files; assert JSON output count matches expected dates × cities

**Change M** (ingest_grib_to_snapshots idempotency + UTC issue_time):
1. `test_ingest_idempotent_via_insert_or_replace` — run twice for same (city, date, issue_time, lead_hours); assert row count is 1, not 2
2. `test_ingest_populates_model_and_data_version` — assert ingested row has non-null `model_version` and `data_version`
3. `test_ingest_converts_kelvin_correctly` — member=300K for an F city; assert `members_json` value ≈ 80.33 (not still 300)
4. `test_ingest_issue_time_utc_explicit_roundtrip` (M.7 test) — write a row via `ingest_grib_to_snapshots.py` with `issue_time=datetime(2024,6,1,12,tzinfo=timezone.utc)`; read back `issue_time` string from DB; parse via `datetime.fromisoformat()`; call `compute_id()` with the result; assert the resulting hash matches `compute_id()` called with the original Python `datetime(2024,6,1,12,tzinfo=timezone.utc)`. This is the SQLite PATH 2 round-trip from TRAP A test, applied specifically to the M.7 write path.

**Change N** (live `_bin_probability` histogram equivalence):
1. `test_bin_probability_open_low_uses_histogram` — `Bin(low=-inf, high=75)` with members [73,74,75,76,77]: assert result = 3/5
2. `test_bin_probability_open_high_uses_histogram` — `Bin(low=76, high=+inf)` with same members: assert result = 2/5
3. `test_bin_probability_equivalence_with_rebuild_path` — run both `_bin_probability` and `bin_probability_from_histogram` on the same 51-member array for the same bin; assert results differ by < 1e-10
4. `test_bin_probability_no_overflow_on_inf_edges` — assert no OverflowError or ValueError when `b.low = float('-inf')` or `b.high = float('+inf')`

### 14.2 Integration tests per Step (one end-to-end test per step, cover gate + rollback path)

- `test_step0_preflight_fails_on_bankers_rounding_in_settlement` — plant a `np.round` in a monitored file; assert pre-flight exits non-zero
- `test_step1_schema_migration_idempotent` — run migration twice on sandbox DB; assert no error on second run
- `test_step2_extraction_gate_on_incomplete_output` — mock partial JSON output (half the cities missing); assert gate fails
- `test_step3_ingest_rejects_nan_member` — JSON with NaN in members_json; assert `validate_ensemble_snapshot_for_calibration` rejects
- `test_step3_issue_time_utc_round_trip` (new in v2.1) — ingest a row; read back `issue_time` from `ensemble_snapshots`; verify `compute_id()` produces the same hash as computed at ingest time. This validates M.7 in the integration context.
- `test_step4_backfill_coverage_gate` — mock WU returning 90% of expected dates; assert gate reports per-city coverage shortfall
- `test_step5_settlement_count_approximately_equals_observation_count` — on sandbox DB with 100 observations, assert settlements count in [98, 102]
- `test_step6_bin_topology_quarantine_logs_to_availability_fact` — plant a gap-bin market; assert that day's pairs are not generated and an availability_fact row appears
- `test_step7_refit_rejects_null_decision_group_id` — plant a NULL decision_group_id row; assert refit_platt.py refuses to fit
- `test_step8_hard_gate_on_p_raw_direction_failure` — mock calibration_pairs where mean_p_raw_on_actual_yes < mean_p_raw_on_actual_no; assert hard gate fires
- `test_step81_tail_evaluation_rejects_on_high_shift` — synthesize tail data that dramatically shifts A coefficient; assert §8.1 gate rejects inclusion (now checks all 3 coefficients)

### 14.3 E2E test on synthetic fixture

`test_full_pipeline_e2e_synthetic` — **2 cities (one NH, one SH) × 60 days × 7 lead_days** (bumped from 30 days in v2.1 to ensure per-bucket n_eff ≥ 15). Fixture note: with 2 cities × 60 days × 7 leads, per-bucket pair count ≈ 60 / 4 seasons × 2 cities = ~30 per season-city bucket, comfortably above the n_eff ≥ 15 Platt maturity gate. The 30-day fixture would yield only ~7-8 per bucket, exercising only the insufficient-maturity skip path, not the Platt fit path.

Synthetic TIGGE JSON + synthetic WU observations. Runs A→N→Step 1→…→Step 8 on a sandbox DB. Covers:
- Happy path: 840 decision_groups (2 cities × 60 days × 7 leads)
- WMO rounding at Steps 3 and 5
- Hash stability: re-compute decision_group_id for all 840 groups, assert 0 mismatches
- Bin topology: one city has a gap-bin market on day 15 → assert that day quarantined
- Platt fit runs on decision_group-counted n_eff (not row count)
- All 5 hard gates pass
- Live `_bin_probability` (Change N) and rebuild path (Change F) produce identical p_raw for same inputs

### 14.4 Trap regression tests (explicit names)

- `test_decision_group_id_stability_across_paths` (TRAP A) — see Change G unit test 3 above
- `test_bin_json_roundtrip_preserves_inf` (TRAP B) — see Change H unit test 2 above
- `test_square_matrix_training_no_asymmetric_bias` (TRAP C):
  1. Synthesize calibration_pairs with square-matrix window (7 leads each)
  2. Add 70 synthetic lead_days=1-only rows with a deliberate short-lead bias pattern
  3. Fit Platt on square-matrix only → `(A_ref, B_ref, C_ref)`
  4. Fit Platt on union → `(A_cand, B_cand, C_cand)`
  5. Assert bootstrap 95% CI overlap of A drops below 80% (i.e., the test detects the distortion, confirming the detection mechanism works)
  6. Also assert B shifts (CI overlap < 80% for B) — confirming that the revised §8.1 procedure's B-check would also fire
  7. Assert the §8.1 gate would fire for this synthesized case

### 14.5 Observability — what metrics are emitted at each step

- **Step 0**: semantic_linter prints counts of files scanned and hits found. Exit 0 = clean, exit 1 = violations with file:line.
- **Step 1**: `schema_migrations` table gets a row with timestamp, schema_version, row counts of each table before and after migration.
- **Step 2**: extractor logs per-region-dir count of GRIBs processed and JSONs written. Summary line: `Extracted N city-date-lead JSONs from M GRIB files.`
- **Step 3**: ingestor logs per-city row counts written to `ensemble_snapshots`. Final: `Ingested N rows, M skipped (already present).`
- **Step 4**: backfill logs per-city success/reject counts. `availability_fact` entries for every rejected day with reason code.
- **Step 5**: settlement derivation logs `derived N settlements from N observations; 0 NaN values`.
- **Step 6**: calibration pair generation logs per-(city, target_date) status: generated / quarantined(BinTopologyError) / skipped(no snapshot). Final: `Generated N pairs from M decision_groups; K days quarantined.`
- **Step 7**: refit logs per-(city × season) bucket: n_eff, fit_status (fit/skipped/failed), A/B/C coefficients. Any NaN coefficient is a hard error.
- **Step 8**: replay logs hard gate results. Step 8.1 logs A/B/C shift statistics (all 3 coefficients) and accept/reject decision for the 70-day tail.
- **All steps**: rollback must use actual schema-supported keys. Today only
  selected tables carry `rebuild_run_id`; canonical calibration-pair rollback
  uses `bin_source='canonical_v1'`, and future audited GRIB ingest must define
  its run marker in task #53 before live DB mutation.

---

## 15. Commit strategy

### 15.1 Commit groupings

Proposed breakdown — 7 commits for Changes A-N plus operational steps:

| Commit | Changes | Rationale |
|---|---|---|
| `rebuild/schema-guardrails` | A, B, C | Schema idempotence + ingestion validation — all touch infrastructure, independent of rounding changes. Commit these first so the DB is sound. |
| `rebuild/wmo-rounding` | D, E | Rounding fix + Monte Carlo propagation. Self-contained; no dependencies on other changes. |
| `rebuild/calibration-pipeline` | F, G, H, I | Rebuild_calibration live pipeline + decision_group hash + Bin boundary + topology validation. These are mutually dependent (F uses H's Bin objects; G's hash is called from F). |
| `rebuild/platt-bootstrap` | J, K | Platt logit clipping + bootstrap resampling. Mutually independent but both touch the calibration/strategy layer. |
| `rebuild/live-bin-equivalence` | N | Live `_bin_probability` histogram fix. Separate commit because it touches `market_analysis.py` which is live-path; deserves independent review. |
| `rebuild/tigge-extraction` | L | Extraction script run + sentinel idempotency. Separate because it's operational (no src/ changes). |
| `rebuild/ingest-script` | M | New ingest_grib_to_snapshots.py script. |

### 15.2 Changes that can be committed in parallel (independent files)

- A (db.py) and B (config.py) touch completely different files — can be developed in parallel by different agents
- D (settlement_semantics.py) and J (platt.py) are independent
- K (market_analysis.py bootstrap fix) and N (market_analysis.py _bin_probability fix) touch the same file — must be sequenced or developed together to avoid merge conflicts

### 15.3 Commit message template

```
rebuild/<area>: <what changed> (fixes D<N>[, D<N>])

- <specific change 1>
- <specific change 2>
- Tests: <test names added>

Authority: zeus_math_spec.md §<section>
```

Example:
```
rebuild/wmo-rounding: replace banker's rounding with WMO floor(x+0.5) (fixes D1, D2)

- SettlementSemantics: add wmo_half_up rule using np.floor(scaled + 0.5)
- All constructors (default_wu_fahrenheit, default_wu_celsius, for_city) use wmo_half_up
- Tests: test_wmo_round_half_up_verification_table, test_settlement_semantics_default_constructors_use_wmo

Authority: zeus_math_spec.md §1.2
```

### 15.4 Scope discipline (CRITICAL)

**NEVER use `git add .` or `git add -A` for these commits.** The `data-improve` branch has 22+ pending uncommitted file deletions in `docs/` and `zeus_data_improvement_foundation_plus/` that belong to a separate cleanup and must NOT be swept into the rebuild commits.

Commit only by explicit file path:
```bash
git add src/state/db.py tests/test_schema_idempotent.py
git commit -m "rebuild/schema-guardrails: ..."
```

Before each commit, run `git status` and review every file in the staging area. If any file outside the Change's expected scope appears, unstage it.

---

## 16. Pre-compact handoff checklist

The next post-compact session must read these files in this order before taking any action:

### Mandatory first reads

1. **`AGENTS.md`** — read §1 ("Why settlement is integer"). Contains the WMO floor(x+0.5) rule as the top-level authority. This file wins any disagreement with other documents.

2. **`docs/reference/zeus_math_spec.md`** — skim the table of contents; read §1 (unit/rounding), §5 (bins including ±inf), §6 (Extended Platt with logit_safe), §12 (decision_group and training pair construction). This is the reference math specification; executable contracts and authority manifests win on disagreement.

3. **`docs/operations/data_rebuild_plan.md`** (this file, v2.1) — the operational plan. §0.5 RALPLAN-DR summary orients you quickly. §3 has the 5 guardrails. §5 has the 14 Changes.

### Git state checks

```bash
git status        # confirm you are on data-improve branch; see the 22+ deletions pending
git log --oneline -10  # confirm last commits are K4.5.x series
```

Expected last commits: `6cc8b26 K4.5.1 commit 3`, `1fb04a1 K4.5.1 commit 2`, etc. If you see different recent commits, stop and verify you are in the right repo and branch.

### Known running processes (do not kill)

- **TIGGE download pipeline**: `tigge_full_history_pipeline.py` running as background daemon (cron-managed). PID was 182 when last seen. Do NOT kill it. It is extending the TIGGE coverage window while the rebuild is planned.

### Authority gate status (CRITICAL — read this before touching any code)

The calibration-store authority gate changed during the 2026-04-14
refit-preflight repair. Current source status:

- `get_pairs_for_bucket` in `src/calibration/store.py` defaults to
  `authority_filter='VERIFIED'`, and the refit/manager paths also request
  `bin_source_filter='canonical_v1'`.
- `load_platt_model` in `src/calibration/store.py` now loads only active
  `authority='VERIFIED'` models.
- `scripts/refit_platt.py` refuses to fit unless VERIFIED pairs are exclusively
  canonical_v1 rows with nonblank `decision_group_id`.
- This does **not** mean all live decision seams are authority-complete:
  evaluator/market-fusion authority propagation and broader stale-model
  governance remain separate follow-up work.

Treat the calibration-store gate as present, but treat the wider live-path
authority story as only partially closed. Any report claiming the old
`get_pairs_for_bucket` / `load_platt_model` authority gap still exists is stale;
any report claiming evaluator/market-fusion authority is fully solved is an
overclaim.

### Known gaps — do NOT re-investigate

The following are already resolved. Do NOT spawn investigation agents for these:
- TIGGE disk layout and file format: documented in §7.1 and investigator reports in `docs/archives/work_packets/branches/data-improve/data_rebuild/2026-04-13_zeus_data_improve_large_pack/DB rebuild/`
- `ensemble_snapshots` is 0 rows: confirmed (GRIBs on disk, no ingestor run yet)
- `SettlementSemantics` uses banker's rounding: confirmed at `src/contracts/settlement_semantics.py:30,81,96,120`
- `compute_id()` `.isoformat()` fragility: root-caused in §3.3 of this plan
- Bin ±inf JSON serialization hazard: root-caused in §3.5 of this plan
- Training window asymmetry: root-caused in §8 of this plan
- Live forecast path uses Open-Meteo (not TIGGE GRIB) as temporary fallback: confirmed
- `store.py:70-71` fallback generates naive string when `decision_group_id=None`: confirmed (Change G.6 deletes it)
- `market_analysis.py:314-321` `_bin_probability` uses range-based logic, not histogram: confirmed (Change N replaces it)
- `authority='VERIFIED'` live-path gate status: **NOT built** (see authority gate status section above)

### Pending files on `data-improve` (leave alone)

There are 22+ pending file deletions tracked by git in `docs/` and `zeus_data_improvement_foundation_plus/`. These are a SEPARATE cleanup operation. Do not touch or commit them as part of the rebuild. `git status` will show them as deleted/untracked; ignore them.

### First action for the next session

Verify this plan with the user ("proceed with Changes A-N?"), then start writing **relationship tests first** before touching any implementation file. Per the methodology: relationship tests → implementation → function tests.

---

## 17. Open questions / deferred decisions

- **§8.1 tail evaluation timing**: should the 70-day tail evaluation run immediately after Step 7 (before Step 8 hard gates), or as a separate post-rebuild analysis pass? Current plan: separate pass. Change if operational sequencing needs it.
- **Run marker format for future audited ingest**: task #53 must define the
  actual schema-supported run marker before live GRIB ingestion. Do not assume
  every rebuild table has `rebuild_run_id`.
- **Sandbox DB path**: Step 1 requires a sandbox DB for testing backfill before production run. Path not specified — implementation agent should confirm with user before Step 1.
- **Staged vs atomic Bin migration (Change H)**: executor must grep `b.low is None` call sites before landing Change H. If ≤5, atomic; if >10, staged. Decision deferred to execution time.
- **`bin_probability_from_histogram` shared location**: Change N requires this function be importable by both the canonical rebuild path and `market_analysis.py`. Executor should place it in `src/types/market.py` (co-located with `Bin`) or `src/signal/ensemble_signal.py` (co-located with `p_raw_vector`), whichever avoids circular import. Check import graph at execution time.
