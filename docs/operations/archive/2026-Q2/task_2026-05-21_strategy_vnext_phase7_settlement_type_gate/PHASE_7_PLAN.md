# Phase 7 — Settlement Social→Type-Gate Migration (v2)

**Created/Revised:** 2026-05-21 (plan-critic v1→v2)
**Authority:** 08_PHASE_7_SETTLEMENT_TYPE_GATE.md · v4 §M line 1104 · dossier §3.7/§6.7/§13.1#10
**Dependency:** Phase 6 EvidenceTier framework on main before T3 dispatch.

## Schema Targets (re-grep at dispatch)

- **World DB `SCHEMA_VERSION` (src/state/db.py:852):** lifecycle_state JSON-only on Position (precedent: Phase 2 T5 market_slug at portfolio.py:436-439). **NO ALTER. NO bump.** Stays at N.
- **Forecasts DB `SCHEMA_FORECASTS_VERSION` (src/state/db.py:2528):** T1 bumps N→N+1 (outcome_type column). T3 bumps N+1→N+2 (settlement_capture_verifications table); OR combined N→N+1 if dispatched as one PR.
- Executor MUST re-grep both at dispatch start; Phases 3/5/6 may bump before Phase 7.

## Track T1 — SettlementOutcome enum + Position.lifecycle_state (~300 LOC)

**Files:** `src/contracts/settlement_outcome.py` (new) · `src/state/portfolio.py` · `src/state/schema/v2_schema.py` · `src/state/db.py`

**Deliverables:**
1. `SettlementOutcome(IntEnum)` 10 members verbatim from authority (values 0,1,2,3,4,5,6,100,101,102).
2. `Position.lifecycle_state: SettlementOutcome = SettlementOutcome.UNRESOLVED` appended after `market_slug` (JSON-only).
3. **Coercion guard** in `Position.__post_init__` mirroring direction pattern (portfolio.py:448-449): `if not isinstance(self.lifecycle_state, SettlementOutcome): self.lifecycle_state = SettlementOutcome(int(self.lifecycle_state))`. Also wire into `_position_from_projection_row` (portfolio.py:1313) so DB-loaded int restores to IntEnum.
4. `settlements_v2.outcome_type INTEGER` — append column in `_create_settlements_v2` (v2_schema.py:30) + ALTER branch in `init_schema_forecasts`. Bump `SCHEMA_FORECASTS_VERSION` N→N+1.
5. `VALID_FORWARD_TRANSITIONS` dict + `apply_transition` raising `InvalidSettlementTransition`.

**OBSERVATION_REVISED (=6) transitions — explicit:**
- Forward-only to SOURCE_REVISION (102) or DISPUTED (100).
- NO transition back to PHYSICALLY_CONFIRMED, SOURCE_PUBLISHED_VENUE_UNRESOLVED, or UNRESOLVED.

**Acceptance criteria:**
- `list(SettlementOutcome)` → 10 members, exact values per authority.
- `Position()` defaults lifecycle_state to `UNRESOLVED`; `asdict → JSON → load → Position` round-trip produces `SettlementOutcome` instance (NOT raw int).
- `settlements_v2` includes `outcome_type INTEGER` column.
- Forward chain `UNRESOLVED→PHYSICALLY_CONFIRMED→SOURCE_PUBLISHED_VENUE_UNRESOLVED→VENUE_RESOLVED_WIN→REDEEMED` succeeds.
- `apply_transition(REDEEMED, UNRESOLVED)` raises.
- `apply_transition(OBSERVATION_REVISED, PHYSICALLY_CONFIRMED)` raises.
- `apply_transition(OBSERVATION_REVISED, SOURCE_REVISION)` succeeds.
- `apply_transition(PHYSICALLY_CONFIRMED, DISPUTED)` succeeds.
- Tag `phase7_track1_landed`.

## Track T2 — Harvester social-string refactor (~150-200 LOC)

**Scope (verified at v2):** ONLY callsite is `src/execution/harvester.py:1097`. `harvester_truth_writer.py` has ZERO `umaResolutionStatus ==` matches. LOC revised from ~400.

**Files:** `src/execution/harvester.py` · `src/contracts/settlement_outcome.py` (classifier colocated)

**Deliverables:**
1. `classify_settlement_outcome(market_json: dict) → SettlementOutcome`. Executor MUST audit live Gamma JSON at dispatch for actual field names — do NOT assume `automaticallyResolved` / `negRiskMarketID` exist (neither appears in current src/).
2. Replace `market.get("umaResolutionStatus") != "resolved"` (harvester.py:1097) with `classify_settlement_outcome(market) not in {VENUE_RESOLVED_WIN, VENUE_RESOLVED_LOSE, REDEEMED}` (semantically-equivalent typed branch).
3. **Fail-closed direction inference:** when `umaResolutionStatus == "resolved"` but `outcomePrices` is missing, malformed, or non-binary, classifier returns `SOURCE_PUBLISHED_VENUE_UNRESOLVED`. NEVER assume WIN on missing data. Docstring: "fail-closed: returns unresolved when direction cannot be inferred; never assume WIN on missing data."
4. Legacy / classifier-cannot-decide → `SettlementOutcome.UNRESOLVED` (backward compat).

**Acceptance criteria:**
- `grep -rn "umaResolutionStatus ==" src/` returns 0 (CI antibody).
- `classify({"umaResolutionStatus":"resolved","outcomePrices":["1","0"]})` → `VENUE_RESOLVED_WIN`.
- `classify({"umaResolutionStatus":"resolved","outcomePrices":["0","1"]})` → `VENUE_RESOLVED_LOSE`.
- `classify({"umaResolutionStatus":"resolved"})` (missing outcomePrices) → `SOURCE_PUBLISHED_VENUE_UNRESOLVED` (fail-closed).
- `classify({"umaResolutionStatus":"resolved","outcomePrices":["0.5","0.5"]})` → `SOURCE_PUBLISHED_VENUE_UNRESOLVED`.
- `classify({})` → `UNRESOLVED`.
- Existing harvester tests pass without behavior change.
- Tag `phase7_track2_landed`.

## Track T3 — SettlementCaptureVerifier (~250 LOC)

**Dependency:** Phase 6 EvidenceTier framework on main.

**Files:** `src/contracts/settlement_capture_verifier.py` (new) · `src/state/schema/v2_schema.py` · `src/state/db.py`

**Deliverables:**
1. `settlement_capture_verifications` table in zeus-forecasts.db. Columns: `verification_id PK`, `city TEXT`, `target_date TEXT`, `temperature_metric TEXT`, `fact_known_time TEXT`, `source_published_time TEXT`, `venue_resolved_time TEXT`, `redeemed_time TEXT`, `coherence_verdict TEXT CHECK IN ('COHERENT','INCOHERENT','INCOMPLETE')`, `incoherence_reason TEXT`, `evidence_tier TEXT`, `recorded_at TEXT DEFAULT CURRENT_TIMESTAMP`. Unique on `(city,target_date,temperature_metric)`. Bump SCHEMA_FORECASTS_VERSION N+1→N+2 (or combined with T1 to N+1).
2. **3-valued verdict semantics:**
   - `COHERENT` — all 4 timestamps populated AND `fact_known ≤ source_published ≤ venue_resolved ≤ redeemed`.
   - `INCOHERENT` — all 4 populated BUT ordering violated.
   - `INCOMPLETE` — subset populated; ordering cannot be evaluated.
3. `SettlementCaptureVerifier.verify(position) → VerificationResult`.
4. ATTACH+SAVEPOINT writes under INV-37.
5. Pre-promotion gate: `resolution_window_maker` / `settlement_capture` require `COHERENT` over recent N (threshold in config/settings.json).

**Acceptance criteria:**
- `verify` with `venue_resolved < source_published` (all 4) → `INCOHERENT` + reason.
- `verify` with all 4 in order → `COHERENT`.
- `verify` with only fact_known + source_published populated → `INCOMPLETE`.
- No raw `conn.commit()` outside SAVEPOINT.
- Tag `phase7_track3_landed`.

## Track T4 — Backfill script (~200 LOC)

**Files:** `scripts/backfill_settlement_outcome_type.py` (new)

**Deliverables:**
1. Reads `settlements_v2` WHERE `outcome_type IS NULL` from zeus-forecasts.db.
2. **Inline authority→outcome mapping:**
   - `authority='VERIFIED'` AND `winning_bin` non-null → `VENUE_RESOLVED_WIN` (settlements_v2 stores winning side only; LOSE not derivable here).
   - `authority='UNVERIFIED'` → `UNRESOLVED`.
   - `authority='QUARANTINED'` → `DISPUTED`.
3. ATTACH+SAVEPOINT chunked 500 rows; `--dry-run`; idempotent.
4. File header: Created, Last reused or audited, Authority basis.

**Acceptance criteria:**
- `--dry-run` deterministic across two runs on 1k synthetic rows.
- Idempotent re-run produces identical `outcome_type IS NOT NULL` counts.
- VERIFIED + winning_bin populated → `outcome_type=3` (VENUE_RESOLVED_WIN).
- QUARANTINED → `outcome_type=100` (DISPUTED).
- UNVERIFIED → `outcome_type=0` (UNRESOLVED).
- No raw `conn.commit()` at top level.
- Tag `phase7_track4_landed`.

## CI Antibody & Tags

`grep -rn "umaResolutionStatus ==" src/` → 0. Tags: `phase7_track{1,2,3,4}_landed`, `phase7_landed`.

## What Phase 7 Does NOT Do

- Live promotion of strategies (Phase 6 EvidenceTier gates).
- Modify `ResolutionEra` / `EraAuthorityBasis` (Phase 0 PR 1).
- SQL ALTER on positions / world-db tables (lifecycle_state is JSON-only).
