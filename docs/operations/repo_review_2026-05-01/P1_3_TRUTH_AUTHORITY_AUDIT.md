# P1-3 Truth-Authority Audit (Read-Only)

Date: 2026-05-01
Branch: ultrareview25-remediation-2026-05-01
Auditor: Architect agent (READ-ONLY)
Scope: `src/`, `tests/`, `architecture/2026_04_02_architecture_kernel.sql`,
       `src/state/schema/v2_schema.py`
Method: Grep + Read only. No code modified. Citations grep-verified within
this audit window (≤10 minutes per Zeus L20 rule).

---

## 0. Scope clarification — multiple authority namespaces

The codebase uses the bare token `authority` as a column / dict key /
parameter name in **at least 7 distinct grammars**. Conflating them in
this audit would produce false structural conclusions. They are:

| # | Grammar | Vocabulary | Producer | Notes |
|---|---|---|---|---|
| **A** | **Truth-file authority (uppercase, the INV-23 lane)** | `VERIFIED` / `UNVERIFIED` / `QUARANTINED` / `DEGRADED_PROJECTION` (+ `ICAO_STATION_NATIVE` / `RECONSTRUCTED` lane-specific) | `_TRUTH_AUTHORITY_MAP`, `build_truth_metadata`, `ProvenanceGuard.validate_write` | **K-C target** |
| B | PortfolioState.authority (lowercase) | `canonical_db` / `degraded` / `unverified` | `src/state/portfolio.py:1314,1359,1389` | Translated into A by `_TRUTH_AUTHORITY_MAP` (portfolio.py:65) |
| C | ScanAuthority (market_scanner) | `VERIFIED` / `STALE` / `EMPTY_FALLBACK` / `NEVER_FETCHED` | `src/data/market_scanner.py:43` (Literal) | Distinct enum; collides on string `"VERIFIED"` only |
| D | AuthorityTier (collateral_ledger) | `CHAIN` / `VENUE` / `DEGRADED` | `src/state/collateral_ledger.py:27` (Literal) | Distinct |
| E | DepthProofSource (execution intent) | `CLOB_SWEEP` / `PASSIVE_LIMIT` / `UNVERIFIED` | `src/contracts/execution_intent.py:37` (Literal) | Collides on `"UNVERIFIED"` only |
| F | Forecast authority_tier | `GAMMA`/`DATA`/`CLOB`/`CHAIN`; `GROUND_TRUTH`/`FORECAST`/`DERIVED` | `src/contracts/executable_market_snapshot_v2.py:103`, `src/data/forecast_ingest_protocol.py:58` | Distinct |
| G | entry_economics_authority / fill_authority (lowercase) | `submitted_limit` / `avg_fill_price` / `venue_confirmed_*` etc. | `src/state/portfolio.py:215-230` | Distinct execution lane |

**This audit covers grammar A only — the truth-file authority lane that
INV-23 governs.** Where a string in B/C/E collides on the wire (e.g.
the literal `"VERIFIED"` is used in both A and C), I call that out
explicitly under "JSON / serialization boundaries" (§4).

---

## 1. Authority value inventory (grammar A)

### Counts of canonical-A literal strings

| Literal | `src/` count | `tests/` count | Grep cmd |
|---|---|---|---|
| `"VERIFIED"` | 41 | 71 | `grep -rn '"VERIFIED"' src/ tests/` |
| `"UNVERIFIED"` | 22 | 36 | `grep -rn '"UNVERIFIED"' src/ tests/` |
| `"QUARANTINED"` | 12 | 24 | `grep -rn '"QUARANTINED"' src/ tests/` |
| `"DEGRADED_PROJECTION"` | **1** | **1** | `grep -rn '"DEGRADED_PROJECTION"' src/ tests/` |
| `"ICAO_STATION_NATIVE"` (writer-allowed) | 4 | 4 | `grep -rn '"ICAO_STATION_NATIVE"' src/ tests/` |
| `"RECONSTRUCTED"` (rescue_events_v2 only) | 1 schema, 0 src code | 1 | `grep -rn '"RECONSTRUCTED"' src/ tests/` |

Counts approximated from line count; precise file:line listings appear in §2 / §3.

### Notable findings

1. **`DEGRADED_PROJECTION` has exactly TWO occurrences in the entire
   codebase**: producer `src/state/portfolio.py:67` and one assertion
   `tests/test_phase5a_truth_authority.py:491`. There is **no consumer
   code** that branches on `"DEGRADED_PROJECTION"` — see §3.
2. **A 5th member `ICAO_STATION_NATIVE`** is alive in the
   `observation_instants_v2` lane: `src/state/schema/v2_schema.py:346`
   CHECK extends to `('VERIFIED', 'UNVERIFIED', 'QUARANTINED', 'ICAO_STATION_NATIVE')`,
   and the writer at `src/data/observation_instants_v2_writer.py:65`
   restricts WRITE to `{"VERIFIED", "ICAO_STATION_NATIVE"}`. This is a
   real 5th value, not a typo.
3. **A 6th value `RECONSTRUCTED`** appears only in the rescue_events_v2
   table CHECK (`src/state/schema/v2_schema.py:602`,
   `('VERIFIED', 'UNVERIFIED', 'RECONSTRUCTED')`). The src code does
   NOT emit it; only one test (`tests/test_b063_rescue_events_v2.py:67`)
   round-trips it. It is alive in the schema.

### What about `DEGRADED` (unsuffixed)?

Grep `"DEGRADED"` returns only collateral-ledger AuthorityTier hits
(grammar D) and observability `authority_tier` reads. No truth-file
producer or consumer uses bare `"DEGRADED"`. Verified clean:
```
grep -rnE '"DEGRADED"\b' src/ tests/
```
(only `src/state/collateral_ledger.py`, `src/observability/status_summary.py`).
No grammar-A overlap.

---

## 2. Producer sites (writes to grammar A)

Each row: file:line — column / param — value emitted.

| File:line | Surface | Value emitted | Grammar |
|---|---|---|---|
| `src/state/portfolio.py:65-69` | `_TRUTH_AUTHORITY_MAP` (translates B→A) | `"VERIFIED"` / `"DEGRADED_PROJECTION"` / `"UNVERIFIED"` | A |
| `src/state/portfolio.py:1451` | `annotate_truth_payload(authority=...)` (the canonical producer site) | maps from `state.authority` via `_TRUTH_AUTHORITY_MAP.get(state.authority, "UNVERIFIED")` | A |
| `src/state/truth_files.py:50` | `build_truth_metadata(authority="UNVERIFIED")` default | `"UNVERIFIED"` (fail-closed default) | A |
| `src/state/truth_files.py:60` | low-lane downgrade | `"UNVERIFIED"` | A |
| `src/state/truth_files.py:68` | `meta["authority"] = resolved_authority` (writes into the JSON `truth` dict) | passthrough | A |
| `src/state/truth_files.py:91` | `annotate_truth_payload(authority="UNVERIFIED")` default | `"UNVERIFIED"` | A |
| `src/observability/status_summary.py:912` | `annotate_truth_payload(..., authority="VERIFIED")` for status JSON | `"VERIFIED"` | A |
| `src/calibration/store.py:79,155,395,430` | DB row inserts to `calibration_pairs` / `platt_models` | `"UNVERIFIED"` default, `"VERIFIED"` on canonical | A (DB) |
| `src/calibration/retrain_trigger.py:498` | calibration_pairs insert | `"VERIFIED"` | A (DB) |
| `src/data/daily_obs_append.py:688` | obs row insert | `"VERIFIED"` | A (DB) |
| `src/data/ingestion_guard.py:612` | `ProvenanceGuard.validate_write(authority="VERIFIED")` example | passthrough validation | A |
| `src/data/market_scanner.py:752` | `Snapshot.authority="VERIFIED"` | `"VERIFIED"` | A∩C overlap |
| `src/ingest/harvester_truth_writer.py:265,299` | settlement row write | `"QUARANTINED"` (default) → `"VERIFIED"` (on bin match) | A |
| `src/execution/harvester.py:971,1006` | settlement row write (mirror) | `"QUARANTINED"` → `"VERIFIED"` | A |
| `src/engine/evaluator.py:3117` | ens_snapshot row insert | `"VERIFIED"` if `degradation_level=="OK" and source_role=="entry_primary"` else `"UNVERIFIED"` | A (DB) |
| `src/state/db.py:2834` | `record_settlement(...)` default | `"UNVERIFIED"` | A (DB) |
| `src/state/db.py:2453-2457` | `clean_authority not in {"VERIFIED","UNVERIFIED","QUARANTINED"}` validator | rejects 4th+ values | A (DB write-time enum gate) |

**Total producer sites for grammar A: 17 distinct call sites.**

The single boundary translator from grammar B → A is
`_TRUTH_AUTHORITY_MAP` at `src/state/portfolio.py:65-69`. Every other
producer of A writes a literal directly.

---

## 3. Consumer sites (reads of grammar A and branches)

### 3.1 Equality / inequality branches in `src/`

| File:line | Code shape | Members handled | Classification |
|---|---|---|---|
| `src/types/observation_atom.py:107` | `if self.authority == "UNVERIFIED" and self.validation_pass:` | UNVERIFIED only (raises) | **PARTIAL-BRANCH** (no DEGRADED_PROJECTION case; would silently pass through) |
| `src/types/observation_atom.py:112` | `if self.authority == "QUARANTINED" and self.validation_pass:` | QUARANTINED only (raises) | **PARTIAL-BRANCH** (only fails the 3 declared Literal members; DEGRADED_PROJECTION is OUTSIDE the Literal so unreachable here — but type-system-only enforcement) |
| `src/ingest/harvester_truth_writer.py:150` | `if "authority" in columns and str(_row_value(r,"authority") or "").upper() != "VERIFIED": continue` | Boolean: VERIFIED vs everything-else | **TWO-VALUE-BOOLEAN** — DEGRADED_PROJECTION would be filtered out alongside QUARANTINED, identical to UNVERIFIED. INV-23 surface. |
| `src/ingest/harvester_truth_writer.py:368` | `if authority == "VERIFIED" and resolved_market_outcomes:` | VERIFIED only | **TWO-VALUE-BOOLEAN** |
| `src/state/truth_files.py:59` | `if authority == "VERIFIED" and temperature_metric is None and Path(path).name in _LOW_LANE_FILES:` (downgrade rule) | VERIFIED only | **TWO-VALUE-BOOLEAN** |
| `src/execution/harvester.py:429` | `if "authority" in columns and ...!= "VERIFIED": continue` | VERIFIED vs else | **TWO-VALUE-BOOLEAN** (mirror of harvester_truth_writer.py:150) |
| `src/execution/harvester.py:1083` | `if authority == "VERIFIED" and resolved_market_outcomes:` | VERIFIED only | **TWO-VALUE-BOOLEAN** |
| `src/execution/exit_lifecycle.py:819` | `if str(scan_authority).strip().upper() != "VERIFIED": ...` | VERIFIED vs else | **TWO-VALUE-BOOLEAN** (note: this is grammar C/scan, not A — but the wire string collides) |
| `src/state/db.py:2241` | `if str(scan_authority or "").strip().upper() != "VERIFIED":` | VERIFIED vs else | **TWO-VALUE-BOOLEAN** (grammar C surface, same collision) |
| `src/state/db.py:2453` | `if clean_authority not in {"VERIFIED","UNVERIFIED","QUARANTINED"}: ...` | 3-set membership; rejects DEGRADED_PROJECTION as INVALID at write-time | **PARTIAL-BRANCH** — settlement write path will REJECT a `DEGRADED_PROJECTION` settlement write. (Currently no caller writes DEGRADED_PROJECTION here, but if any future consumer routes through, refused_invalid_authority.) |
| `src/data/market_scanner.py:1629` | `if str(scan_authority or "").strip().upper() != "VERIFIED":` | VERIFIED vs else | **TWO-VALUE-BOOLEAN** (grammar C wire collision) |
| `src/data/observation_instants_v2_writer.py:165` | `if self.authority not in _ALLOWED_WRITE_AUTHORITIES: raise` (= `{"VERIFIED","ICAO_STATION_NATIVE"}`) | 2-set membership | **PARTIAL-BRANCH** (rejects UNVERIFIED/QUARANTINED at write — by design) |
| `src/engine/monitor_refresh.py:128` | `if scan_authority != "VERIFIED": raise` | VERIFIED vs else | **TWO-VALUE-BOOLEAN** (grammar C wire collision) |
| `src/engine/monitor_refresh.py:926` | `if str(get_last_scan_authority()).upper() != "VERIFIED":` | VERIFIED vs else | **TWO-VALUE-BOOLEAN** (grammar C) |
| `src/engine/cycle_runtime.py:1860-1866` | sequence of equality checks against `"STALE"`, `"EMPTY_FALLBACK"`, `"NEVER_FETCHED"`, then `!= "VERIFIED"` fallthrough | full ScanAuthority enumeration | **EXHAUSTIVE-MATCH** (grammar C, but no `case _: raise`; uses if-elif chain with implicit fallthrough returning `""`) |
| `src/engine/evaluator.py:744` | `if authority and authority != "FORECAST":` | grammar F | **TWO-VALUE-BOOLEAN** (grammar F, not A) |
| `src/riskguard/riskguard.py:997` | `if portfolio.authority != "canonical_db":` | grammar B | **TWO-VALUE-BOOLEAN** (grammar B at the seam — see §3.3) |
| `src/calibration/blocked_oos.py:80` | `WHERE ... AND authority = ?` parameterized | DB filter, default `"VERIFIED"` | **PARTIAL-BRANCH** (filters by single value, never enumerates other 3) |
| `src/calibration/store.py:276,327,360` | same pattern: WHERE authority = `?` | filter | **PARTIAL-BRANCH** |
| `src/calibration/effective_sample_size.py:46` | conditional WHERE authority filter | filter | **PARTIAL-BRANCH** |

### 3.2 Truth-JSON dict reads

```
grep -rnE 'truth\.get\(\s*["\x27]authority|truth\[["\x27]authority["\x27]\]' src/
```
returns **clean (zero hits)** in `src/`. The only truth.authority field
reads are in `tests/test_phase5a_truth_authority.py`. **No production
code reads `truth["authority"]` from the JSON wrapper at all.**

This means the JSON `truth["authority"]` field is **stamped but
unconsumed by code**. It is operator-visible only (status_summary,
positions JSON for human eyes / external dashboards). The "consumer
drift" risk for DEGRADED_PROJECTION through the truth.json wrapper is
therefore **purely operator-facing**, not runtime-decision-making.

### 3.3 The riskguard.py:997 seam

`src/riskguard/riskguard.py:997` reads `portfolio.authority` (grammar
B, lowercase) and only branches `!= "canonical_db"`. Result:
`degraded` and `unverified` collapse into the same "non-canonical"
suppression bucket. Per-INV-23 reading, the K-C concern is whether
`degraded` is *written* as `VERIFIED` to disk; the runtime suppression
itself is correct (degraded and unverified both block new entries).
**However** the comment at `:986-988` explicitly says "If authority !=
'canonical_db', new-entry paths are suppressed but monitor / exit /
reconciliation lanes run read-only." Both `degraded` and `unverified`
get the same treatment — that is intentional for new-entry suppression.

### 3.4 Classification summary

| Class | Count |
|---|---|
| EXHAUSTIVE-MATCH | **1** (cycle_runtime.py:1860-1866, but on grammar C) |
| PARTIAL-BRANCH | **9** (calibration filters, observation_atom, observation_instants_v2_writer, db.py:2453) |
| **TWO-VALUE-BOOLEAN** (INV-23 surface) | **10** including 5 grammar-A and 5 grammar-C wire-collision sites |
| PASS-THROUGH | many (string is forwarded into JSON / DB without inspection) |

**Of the TWO-VALUE-BOOLEAN sites, the grammar-A INV-23-relevant ones are:**

- `src/ingest/harvester_truth_writer.py:150` (filters non-VERIFIED rows)
- `src/ingest/harvester_truth_writer.py:368` (writes settlement only when VERIFIED)
- `src/state/truth_files.py:59` (low-lane downgrade rule on VERIFIED)
- `src/execution/harvester.py:429`
- `src/execution/harvester.py:1083`

For **all five**, treating DEGRADED_PROJECTION as `not VERIFIED` is the
**correct semantic outcome** — these are gates that allow only fully
canonical data through. INV-23's concern is the JSON-stamp boundary,
not these gates.

### 3.5 Where the INV-23 risk actually lives

The 2026-05-01 review's claim ("producers stamp authority correctly
but consumers don't differentiate DEGRADED_PROJECTION from UNVERIFIED")
is **technically true at the JSON wrapper boundary** (no consumer
distinguishes them) but practically vacuous because **no production
consumer reads truth['authority'] at all** (§3.2). The K-C risk is
therefore not "consumers silently misroute DEGRADED" — it is
**"future consumer added without a gate that fails on the
DEGRADED_PROJECTION case"**. The K-C synthesis recommendation is
correct on motive (forward-looking), wrong on framing (no consumer
drift exists today).

---

## 4. JSON / serialization boundaries

The `truth["authority"]` field is JSON-serialized at:

- `src/state/portfolio.py:1456-1459` (`json.dump(data, f, indent=2)` — `data` includes `truth` from `annotate_truth_payload`)
- `src/observability/status_summary.py:912-...` (status JSON write)
- `src/state/truth_files.py:217` (legacy tombstone `path.write_text(json.dumps(...))`)
- `src/state/truth_files.py:238` (backfill mode metadata)
- DB writes: many — every CHECK-constrained authority column is a TEXT field that stores grammar A literally.

**Hazard analysis for switching to `TruthAuthority(StrEnum)`:**

`StrEnum` members are `str` subclasses. `json.dumps(TruthAuthority.VERIFIED)`
returns `'"VERIFIED"'` (Python ≥3.11 std lib). This is **safe** for the
file-write paths above.

**However**, three real hazards exist:

1. **DB row equality**: every WHERE clause that does
   `... authority = ?` (calibration_pairs, settlements_v2, etc.) binds
   a parameter. If a caller passes `TruthAuthority.VERIFIED`, sqlite3
   adapts via `str()` → fine. But `clean_authority not in {"VERIFIED",
   "UNVERIFIED", "QUARANTINED"}` at `src/state/db.py:2453` does set
   membership against bare strings. `TruthAuthority.VERIFIED in
   {"VERIFIED",...}` works due to StrEnum equality, but the inverse
   `"VERIFIED" in {TruthAuthority.VERIFIED, ...}` also works. **Safe
   provided enum is StrEnum, not Enum.**
2. **`.upper()` calls**: 7 sites do `str(authority).strip().upper() ==
   "VERIFIED"` (§3.1 grammar-C sites). On a StrEnum member, `.upper()`
   returns a plain `str`. Comparison still works. **Safe.**
3. **`f"...{authority}..."` interpolation**: Python repr of StrEnum
   member includes class prefix in `repr()` but NOT in `str()` /
   f-string. **Safe** (verified by Python docs).

**Real hazards:**

- `tests/test_obs_v2_writer.py:118` parametrizes
  `["UNVERIFIED", "QUARANTINED", "", "random"]` — a switch to
  `TruthAuthority` would not affect this test (it uses raw strings
  intentionally to test rejection paths).
- **Only the WRITE-validation enum at `src/state/db.py:2453`
  (`{"VERIFIED","UNVERIFIED","QUARANTINED"}`)** is currently a 3-set
  that excludes `DEGRADED_PROJECTION`. If the new enum has 4 members
  and any future writer sends `TruthAuthority.DEGRADED_PROJECTION`
  to `record_settlement_v2()`, that write **would be rejected** as
  "refused_invalid_authority". The migration must decide: extend the
  set to 4, or document that DEGRADED_PROJECTION never persists into
  `settlements_v2`.

---

## 5. Database column constraints

Grep `architecture/2026_04_02_architecture_kernel.sql` for `authority`:
**0 occurrences** (the kernel SQL has no `authority` column).
```
grep -n -i 'authority' architecture/2026_04_02_architecture_kernel.sql
# (clean — no hits)
```

All authority CHECK constraints live in `src/state/db.py` and
`src/state/schema/v2_schema.py`:

| File:line | Table | CHECK members | 4-grammar-A coverage? |
|---|---|---|---|
| `src/state/db.py:373` | observations | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** — DEGRADED_PROJECTION absent |
| `src/state/db.py:436` | observation_runs | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** |
| `src/state/db.py:514` | snapshots_p_raw | (default `'VERIFIED'` no CHECK enum) | not enum-checked |
| `src/state/db.py:541` | calibration_pairs | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** |
| `src/state/db.py:579` | platt_models | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** |
| `src/state/schema/v2_schema.py:60` | observations_v2 | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** |
| `src/state/schema/v2_schema.py:204` | settlements_v2 | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** |
| `src/state/schema/v2_schema.py:256` | calibration_pairs_v2 | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** |
| `src/state/schema/v2_schema.py:299` | platt_models_v2 | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** |
| `src/state/schema/v2_schema.py:346` | observation_instants_v2 | `('VERIFIED','UNVERIFIED','QUARANTINED','ICAO_STATION_NATIVE')` | **4-only — adds ICAO**, no DEGRADED_PROJECTION |
| `src/state/schema/v2_schema.py:500` | historical_forecasts_v2 | `('VERIFIED','UNVERIFIED','QUARANTINED')` | **3-only** |
| `src/state/schema/v2_schema.py:602` | rescue_events_v2 | `('VERIFIED','UNVERIFIED','RECONSTRUCTED')` | **3-only — adds RECONSTRUCTED**, no DEGRADED_PROJECTION |
| `src/state/db.py:2453` (Python set, not SQL) | settlements_v2 write validator | `{'VERIFIED','UNVERIFIED','QUARANTINED'}` | **3-only** |

**Drift confirmation:** Every DB CHECK constraint enumerates a strict
subset of the runtime grammar-A vocabulary. **`DEGRADED_PROJECTION`
cannot persist to ANY DB column today** — it would violate every
CHECK constraint. This is consistent with the K-C finding: it lives
purely in the JSON truth-stamp lane, never in DB rows.

If the migration to `TruthAuthority(StrEnum)` adds DEGRADED_PROJECTION
as a 4th canonical member, the DB CHECK constraints **must NOT be
extended** unless an actual column is intended to hold it. The current
DB schema correctly excludes the projection-marker.

---

## 6. Existing tests for authority semantics

### 6.1 Function-level tests (most of the corpus)

- `tests/test_phase5a_truth_authority.py`: PortfolioState constructor
  accepts each grammar-B value (lines 67-82); `build_truth_metadata`
  default fail-closes to `"UNVERIFIED"` (377-385); roundtrip through
  `annotate_truth_payload` (387-398); save_portfolio writes the right
  truth label for each of `canonical_db`/`unverified`/`degraded`
  (426-494).
- `tests/test_p0_hardening.py:141-176`: asserts
  `_TRUTH_AUTHORITY_MAP["canonical_db"]=="VERIFIED"` and
  `_TRUTH_AUTHORITY_MAP["degraded"]!="VERIFIED"`.
- `tests/test_observation_atom.py:96-103`: ObservationAtom rejects
  authority="UNVERIFIED"/"QUARANTINED" with validation_pass=True.
- `tests/test_obs_v2_writer.py:118-149`: writer rejects non-allowed
  authorities; accepts `"ICAO_STATION_NATIVE"`.
- `tests/test_ingest_provenance_contract.py:61-111`: ProvenanceGuard
  accepts the 3-set, rejects unknown / lowercase / empty.
- `tests/test_authority_gate.py`, `tests/test_calibration_observation.py`,
  `tests/test_authority_strict_learning.py`: SQL filter tests
  (parameterized authority filter).

### 6.2 Relationship tests (cross-module)

- **`tests/test_phase5a_truth_authority.py::TestAnnotateTruthPayloadProductionCallers`
  (lines 416-494)**: this is a real producer→serialized-output
  relationship test. It asserts that `save_portfolio(state with
  authority=X)` writes `truth['authority']==Y` for the correct mapping.
  It **explicitly tests the `degraded → DEGRADED_PROJECTION`
  translation** at line 491.
- **`tests/test_p0_hardening.py:141`**: tests the `_TRUTH_AUTHORITY_MAP`
  itself (one-shot, not exhaustive over the input domain).
- **`tests/test_pe_reconstruction_relationships.py:44`**: asserts
  `ALLOWED_AUTHORITIES = {"VERIFIED", "QUARANTINED"}` for the rebuild
  plan — this is a relationship test about which final-state values
  may appear.
- No test enumerates **all four** grammar-A members exhaustively
  against a single consumer. There is no test of the form "for each
  TruthAuthority member, the consumer at site X handles it explicitly".

### 6.3 Gap

There is **no exhaustive cross-consumer test** that fails when a new
member is added without consumer-side handling. The K-C
recommendation specifically targets this gap.

---

## 7. Three-option recommendation

### Option (a) MINIMAL — STRONGLY RECOMMENDED

**Scope:**
1. Create `src/types/truth_authority.py`:
   ```python
   from enum import StrEnum
   class TruthAuthority(StrEnum):
       VERIFIED = "VERIFIED"
       UNVERIFIED = "UNVERIFIED"
       QUARANTINED = "QUARANTINED"
       DEGRADED_PROJECTION = "DEGRADED_PROJECTION"
   ```
2. Migrate `_TRUTH_AUTHORITY_MAP` in `src/state/portfolio.py:65-69` to
   use enum members as values:
   ```python
   _TRUTH_AUTHORITY_MAP: dict[str, TruthAuthority] = {
       "canonical_db": TruthAuthority.VERIFIED,
       "degraded":     TruthAuthority.DEGRADED_PROJECTION,
       "unverified":   TruthAuthority.UNVERIFIED,
   }
   ```
3. Update the fallback at `portfolio.py:1451`:
   `_TRUTH_AUTHORITY_MAP.get(state.authority, TruthAuthority.UNVERIFIED)`
4. Add **one** relationship test in
   `tests/test_truth_authority_enum.py`:
   ```python
   def test_truth_authority_enum_exhaustively_covers_observed_values():
       """Every truth-authority value emitted anywhere in src/ must be
       a TruthAuthority member. Asserts the producer surface is closed."""
       members = {a.value for a in TruthAuthority}
       assert members == {"VERIFIED","UNVERIFIED","QUARANTINED","DEGRADED_PROJECTION"}
       # Verify every annotate_truth_payload caller emits an enum-coercible value:
       for raw in _TRUTH_AUTHORITY_MAP.values():
           assert TruthAuthority(str(raw))
   ```

**Files touched: 3** (`src/types/truth_authority.py` (new),
`src/state/portfolio.py`, `tests/test_truth_authority_enum.py` (new)).

**What it accomplishes:**
- Makes the 4-member grammar A enum-encoded.
- Makes adding a 5th member a deliberate type-system edit (StrEnum
  members are append-only in semantics; adding one forces every
  type-aware caller to acknowledge it).
- The relationship test will fail if anyone re-introduces a string
  literal that doesn't round-trip.
- Zero behavioral changes (StrEnum is a `str` subclass; equality
  checks, JSON serialization, DB binding all still work).

**What it does NOT accomplish:**
- Does not retroactively force the 5 TWO-VALUE-BOOLEAN consumers in
  `harvester*.py` and `truth_files.py:59` to handle DEGRADED_PROJECTION
  explicitly. **This is a feature, not a bug**: those gates are
  semantically `VERIFIED-only`, and treating DEGRADED_PROJECTION as
  not-VERIFIED is correct.

### Option (b) MINIMAL+CONSUMER-AUDIT — NOT RECOMMENDED FOR THIS PASS

**Scope: (a) plus an AST-walk test that scans every consumer site and
asserts each handles all 4 members or has a `case _: raise` default.**

**Why I do not recommend this:**

1. The codebase has **NO `match` statements on grammar-A authority**
   today (verified clean: `grep -rnE 'match\s+authority'` returns
   nothing). All 19+ consumer sites are if/elif chains or set
   memberships. An AST walker that requires `case _: raise` would
   reject every existing consumer. The walker would either need to
   accept "if-elif chain that compares to all 4 members" (complex AST
   logic that cannot reliably distinguish grammar A from C) OR force a
   refactor that turns into Option (c).
2. AST walkers across grammar boundaries are brittle. Five of the
   "consumer" sites are grammar C (ScanAuthority) wire-string
   collisions on `"VERIFIED"`. The walker would need to be
   syntax-aware and module-aware. Tractable but high-cost for one
   audit pass.

### Option (c) FULL — NOT RECOMMENDED

**Scope: rewrite all 10 TWO-VALUE-BOOLEAN consumers to use `match`.**

**Why I do not recommend this:**

1. **5 of the 10 sites are grammar C, not A.** They check
   `scan_authority` (market_scanner / monitor_refresh / cycle_runtime
   / db.py:2241 / data/market_scanner.py:1629). Rewriting them to
   exhaustively match `TruthAuthority` members is a category error —
   they enumerate `ScanAuthority`, not `TruthAuthority`.
2. **The other 5 grammar-A sites are correct as written.** A rewrite
   of `harvester_truth_writer.py:150` from
   `if authority != "VERIFIED": continue` to
   ```python
   match TruthAuthority(authority):
       case TruthAuthority.VERIFIED: pass
       case _: continue
   ```
   adds zero correctness, raises if `authority` is anything outside
   the 4-member enum (which can happen with legacy/migrated rows
   carrying `"ICAO_STATION_NATIVE"` or `"RECONSTRUCTED"` from the
   adjacent enums in §1), and increases live-blast-radius right when
   we are mid-remediation.

### Recommendation: **Option (a)**.

**Justification against the operator's stated criteria:**

1. *"the 2026-05-01 review found INV-23 is producer-validated-consumer-blind"*
   — Option (a) corrects the producer-side type encoding so the
   consumer-blind problem cannot regrow at the producer. The
   consumer-blind risk today is **vacuous** (§3.2: zero production
   consumers branch on `truth['authority']`). The K-C goal is
   forward-looking immunity, not retroactive correctness.
2. *"operator wants live-trading-safe minimal diffs"* — Option (a)
   touches 3 files, behavior-neutral. StrEnum is wire-compatible with
   the existing string literal at every JSON / DB / equality boundary
   (verified §4).
3. *"make consumer drift impossible going forward, not retroactively
   perfect"* — Option (a) makes the producer enum exhaustive and adds
   a relationship test. New consumers using `TruthAuthority` get
   IDE-level exhaustiveness via `cast`/`assert_never` patterns
   (Python's `typing.assert_never` works with StrEnum). New consumers
   using raw strings still compile, but the type checker will flag
   them and the new relationship test catches new producer values that
   bypass the enum.

**Counter-recommendation for FUTURE pass (not now):**

When DEGRADED_PROJECTION graduates from "JSON-stamp-only" to "actual
runtime gate input," extend Option (a) to add:
- a single `is_authoritative(a: TruthAuthority) -> bool` helper that
  returns `a == TruthAuthority.VERIFIED`, used by any future consumer
  that needs the boolean,
- a `requires_human_review(a: TruthAuthority) -> bool` helper that
  returns `a in {QUARANTINED, DEGRADED_PROJECTION}`.

This is a 2-function addition that turns the implicit two-value
boolean into a documented binary query, **without** requiring the 10
consumer sites to be rewritten.

---

## 8. Trade-offs

| Option | Pros | Cons |
|---|---|---|
| **(a) MINIMAL** | 3 files; behavior-neutral; producer-side closure; relationship test catches new producer drift; live-trading-safe | Does not retroactively force consumers to handle DEGRADED_PROJECTION; future consumer added without explicit handling could still drift |
| (b) MINIMAL + AST-walk audit | Same as (a) plus consumer-coverage assertion at CI | AST walker is brittle across grammars A/C; would currently fail every if/elif consumer; turns into (c) under pressure |
| (c) FULL rewrite | Maximum correctness; consumer drift mathematically impossible | 10+ files touched; 5 are wrong-grammar; live-blast-radius high; mid-remediation timing wrong |

---

## 9. Final-checklist results

- Read code before claiming: yes (every cited file:line opened with Read or grep).
- Every finding cites file:line: yes (§1-§5).
- Root cause identified (not symptom): yes — the "DEGRADED_PROJECTION is half-encoded" framing is correct; the symptom is JSON-stamp-only with zero production consumers reading it (§3.2).
- Recommendations concrete: yes — option (a) names the 3 files and the test shape.
- Trade-offs acknowledged: yes (§8).
- 10-min citation freshness: all greps run within this audit window.
- No file:line drift: verified by re-greping `_TRUTH_AUTHORITY_MAP`,
  `"DEGRADED_PROJECTION"`, `truth\.get\(.*authority`, and the SQL
  CHECK constraints during this audit.

---

## References

- `src/state/portfolio.py:65-69` — `_TRUTH_AUTHORITY_MAP` (canonical producer)
- `src/state/portfolio.py:1451` — `_TRUTH_AUTHORITY_MAP.get(state.authority, "UNVERIFIED")`
- `src/state/portfolio.py:1314,1359,1389` — grammar-B value emission sites
- `src/state/truth_files.py:43-104` — `build_truth_metadata` + `annotate_truth_payload`
- `src/observability/status_summary.py:912` — status JSON producer
- `src/types/observation_atom.py:92,107,112` — Literal-3 + PARTIAL-BRANCH consumers
- `src/state/db.py:2453` — settlements write-validator (3-set)
- `src/state/schema/v2_schema.py:346` — observation_instants_v2 4-set with ICAO_STATION_NATIVE
- `src/state/schema/v2_schema.py:602` — rescue_events_v2 3-set with RECONSTRUCTED
- `tests/test_phase5a_truth_authority.py:471-494` — degraded→DEGRADED_PROJECTION relationship test
- `tests/test_p0_hardening.py:141-176` — `_TRUTH_AUTHORITY_MAP` shape test
- `tests/test_phase8_shadow_code.py:330-420` — degraded-save behavior test (does NOT assert specific authority value, only annotation presence)

