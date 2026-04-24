# Midstream Remediation — Work Log

## W0 — 2026-04-23 — packet open

- Packet opened: `docs/operations/task_2026-04-23_midstream_remediation/`.
- Authority source: `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md`
  (36-slice v2 plan, signed by pro-vega + con-nyx).
- Executor: team-lead (this session).
- Long-lasting critic: `con-nyx`.
- Upstream co-tenant: parallel agent working data-readiness
  (`docs/operations/task_2026-04-23_data_readiness_remediation/`).
- Initial state verified by independent probe:
  - Production `position_current`: 31 columns, canonical (no drift).
  - `config/provenance_registry.yaml`: already exists (516 lines, real
    content) — T1.b scope shifts from "create" to "audit + skipif removal".
  - `src/strategy/selection_family.py:92` — `make_family_id()` is
    deprecated; zero non-deprecated call sites in `src/` or `scripts/`.
  - `src/engine/cycle_runtime.py:703` — `execute_discovery_phase(..., *, env: str, deps)` canonical.
  - `src/engine/cycle_runtime.py:209` — `materialize_position(..., *, state: str, env: str, ...)` canonical.
  - Zeus venv at `.venv/` — `yaml 6.0.3`, `pytest 9.0.2`.

## Slice rows (appended per slice)

| Slice | Status | Commit | Critic | Date |
|---|---|---|---|---|
| W0 packet open | closed | `ec78c2f` | skipped (doc-only, planning-lock GREEN) | 2026-04-23 |
| T4.0 persistence design rev2 | closed | `9365b20` | surrogate critic CLEAR (Option E); con-nyx informed | 2026-04-23 |
| T7.b AST-walk guard | closed | `beea8a9` | 1/1 pass on first run; zero pre-state violators (grep-verified) | 2026-04-23 |
| T1.a 15-file header wave | closed | `67b5908` | narrow-scope regression 19/344/34/1 matches pre-T1a baseline exactly (zero delta from comment-only change); verified via git stash | 2026-04-23 |
| T1.b provenance_registry skipif cleanup | closed | `4943d0d` | 4 stale `skipif(not REGISTRY_YAML.exists(), ...)` markers removed; 19/19 test_provenance_enforcement tests still pass | 2026-04-23 |
| T3.1 execute_discovery_phase 5-caller env kwarg fix | closed | `716bfdd` | 6 TypeError failures → pass (2 day0_runtime + 2 discovery_phase_entry_path + 2 discovery_phase_records); zero new failures; delta-direction on 3 modified files: 28F→22F, 166P→172P | 2026-04-23 |
| T3.3 position_current ALTER TABLE canonical-column backfill | closed | `36f0189` | surrogate critic (code-reviewer@opus) CLEAR; fixes test_kernel_schema_adds_token_identity_columns; planning-lock GREEN; delta: -1 failure, 0 new failures; INV-14 runtime enforcement preserved per grep of direct-SQL writers | 2026-04-23 |
| T3.2 canonical_projection fixture patch | closed | pending | one-line category fix (Fitz C1); `test_architecture_contracts.py` 9F→1F; surrogate critic CLEAR; con-nyx dispatched; remaining 1F is unrelated `_Logger.warning` AttributeError | 2026-04-23 |
| T3.2b canonical schema alignment antibody | closed | `566a48f` | plan AST-walk premise vacuous (no dict builders in projection.py); pivoted to 3 structural-alignment tests; 3/3 pass; surrogate critic CLEAR with regex hardening polish adopted | 2026-04-23 |
| T3.2 canonical_projection fixture patch | closed | `566a48f` | one-line category fix bundled in same commit as T3.2b per slice-pairing rationale | 2026-04-23 |
| T7.a test_fdr_family_key_is_canonical activation | closed | N/A (no code change) | **slice already done pre-session**: test at L189 is unskipped and passing 1/1; plan's "skip at L67" citation was wrong (L70 is a different test, `test_no_high_low_mix_in_platt_or_bins`, NC-12 territory for T1.d); test body already covers INV-22 scope separation, determinism, metric discrimination | 2026-04-23 |
| T2.d/e/f SelectionFamilySubstrate fixes | **PARTIAL — deferred** | none (reverted) | Plan fix (replace `monkeypatch.setattr(evaluator_module, "Day0Signal", ...)` with `monkeypatch.setattr("src.signal.day0_router.Day0Router.route", ...)`) is directionally correct BUT insufficient: tests still fail on upstream DT#7 boundary-day gate at `evaluator.py:777` (`boundary_ambiguous_refuses_signal(v2_snapshot_meta)`) — new code path added after test fixture was written. Fixing requires either (a) populating v2 ensemble_snapshots with `boundary_ambiguous=0` row in test setup, or (b) additional monkeypatch on `_read_v2_snapshot_metadata`. Reverted my local fix to avoid committing half-work. Flagged as a new slice T2.d.1 (v2 snapshot fixture setup) for follow-up. | 2026-04-23 |
| T2.a/T2.b R14 quarantine test fixture updates | closed | `c4ee26a` | tests were stale vs current source law (`peak_window_max_v1` now quarantined per `src/contracts/ensemble_snapshot_provenance.py:87,102`); updated 2 tests in `tests/test_calibration_bins_canonical.py` to iterate `CANONICAL_DATA_VERSIONS` / reflect new partition; 2/2 targets pass, 40/40 file regression; surrogate critic CLEAR with independent grep + 2 corroborating test-suite verification | 2026-04-23 |
| T1.d Phase-N skip audit in test_dual_track_law_stubs | closed | `979eb3b` | audit complete; 1 skip marker found (L70 `test_no_high_low_mix_in_platt_or_bins` NC-12/INV-16) classified **KEEP_LEGITIMATE** — INV-16 Day0 LOW causality enforcement IS coded at `src/engine/evaluator.py:922-944`, but NC-12 is multi-surface (Platt + calibration pairs + bin lookup + settlement identity) and full enforcement awaits Phase-7 v2 substrate rebuild (currently empty); no other skip markers in file — all other 11 tests are active with Phase-9B/9C/10E activation markers | 2026-04-23 |
| T1.e currency-CI audit script + registry | closed | `692a3af` | new `scripts/test_currency_audit.py` reads `architecture/test_topology.yaml::categories.midstream_guardian_panel` (nested per surrogate-critic D3 fix) + `architecture/script_manifest.yaml` registration; 15/15 panel files green on dry-run; D1 (empty-panel silent-pass) + D2 (YAML parse traceback) + D3 (sibling-vs-nested) fixes applied before commit; surrogate critic COMMENT verdict with 4 findings, 2 addressed in-slice | 2026-04-23 |
| T5.a ExecutionPrice executor boundary | closed | `abd5bb6` | structural boundary guard in `_live_order` via `ExecutionPrice(price_type="ask", fee_deducted=False, currency="probability_units")` — catches NaN/±inf/negative/>1.0 before CLOB-send; 12 new tests pass; latent `decision_edge` bug fixed inline (added to `ExecutionIntent` dataclass per critic MEDIUM finding); rejection reason renamed `malformed_limit_price` per critic LOW finding; surrogate critic COMMENT verdict, all 4 findings (1 MED + 3 LOW) addressed in-slice | 2026-04-23 |
| T4.1a DecisionEvidence JSON persistence primitive | closed | `547bcdd` | `to_json` / `from_json` added to `DecisionEvidence` class body (not module-level workaround — surrogate critic HIGH finding: frozen dataclasses DO accept body methods); `contract_version=1` envelope; strict `type(v) is int` guard prevents `True == 1` collision; malformed-JSON/missing-keys/unknown-fields route to `UnknownContractVersionError`; `__post_init__` ValueErrors propagate unwrapped so callers distinguish schema drift from invalid data; 18 new tests pass (round-trip + version guard + malformed + boolean-True guard + __post_init__ propagation); surrogate critic REQUEST CHANGES, all 3 blocking findings addressed in-slice before commit | 2026-04-23 |
| T4.1b DecisionEvidence entry-event emission wiring | closed | `1d541a3` | 4 src files + 1 new test file; evaluator accept path (L1700-1726 — plan-premise correction #8: plan cited L753/778/… rejection paths) constructs `DecisionEvidence(evidence_type="entry", statistical_method="bootstrap_ci_bh_fdr", sample_size=edge_n_bootstrap(), confidence_level=DEFAULT_FDR_ALPHA, fdr_corrected=True, consecutive_confirmations=1)` and plumbs through `EdgeDecision.decision_evidence` → `_dual_write_canonical_entry_if_available` → `build_entry_canonical_write` → `_entry_event` → `_entry_event_payload` as **`decision_evidence_envelope` string key** (verbatim `to_json()` output — Q2 flipped from nested dict on surrogate HIGH finding); legacy-backfill path at `src/execution/exit_lifecycle.py:181` emits `decision_evidence_reason="backfill_legacy_position"` sentinel so T4.2-Phase1 exit-side audit distinguishes missing-because-legacy from missing-because-bug (surrogate HIGH finding missed by design doc); `ENTRY_ORDER_POSTED` only (Q1), `decision_id` idempotency (Q3); 12 new tests pass; pre-T4.1b source baseline = 24F/476P on 17-file set; post-T4.1b = 24F/488P on 18-file set (delta = ZERO new failures, +12 passes from new test); planning-lock GREEN on 5 files; surrogate code-reviewer@opus pre-code design review REQUEST_CHANGES with 6 findings (all 6 addressed inline incl. backfill sidecar scope addition); con-nyx dispatched post-commit per operator correction (idle/no substantive reply) | 2026-04-23 |
| T4.2-Phase1 exit-side DecisionEvidence audit-only | closed | `0206428` | 3 files (2 src + 1 new test); plan-premise correction #9: plan cited `exit_triggers.py:49,158,218` but that module has ZERO production callers (5 test-only callers) — real exit decision path is `Position.evaluate_exit` at `src/state/portfolio.py:296` invoked from `src/engine/cycle_runtime.py:619`; new `load_entry_evidence(conn, trade_id)` helper in `src/state/decision_chain.py` reads `decision_evidence_envelope` from ENTRY_ORDER_POSTED payload landed by T4.1b; `cycle_runtime.py` injects audit call right after `pos.exit_trigger` set, gated by `_D4_ASYMMETRIC_EXIT_TRIGGERS = {EDGE_REVERSAL, BUY_NO_EDGE_EXIT, BUY_NO_NEAR_EXIT}` frozenset; constructs weak-burden exit evidence (sample_size=2, consecutive_confirmations=2, fdr_corrected=False, confidence_level=1.0 placeholder documented inline); try `exit_evidence.assert_symmetric_with(entry_evidence)`, on `EvidenceAsymmetryError` emit structured `logger.warning("exit_evidence_asymmetry " + json.dumps({...}, sort_keys=True))` + increment `summary["exit_evidence_asymmetry_audit"]`; outer try catches any audit failure into `exit_evidence_audit_skipped` log + counter (same JSON shape per surrogate LOW; never blocks exit); DAY0_OBSERVATION_REVERSAL correctly excluded (single-cycle observation-authority, template shape mismatch — surrogate concern #4); 11 new tests pass; pre-T4.2-Phase1 baseline on 6 files = 2F/127P/25S, post = 2F/138P/25S (delta = ZERO new failures, +11 passes from new test; 2 pre-existing are _Logger.warning harness gap + T3.4 upstream-blocked linter); planning-lock GREEN on 3 files; surrogate pre-commit review **APPROVE** with 3 LOW findings (2 polish-addressed inline: skip-path JSON uniformity + skip counter; 3rd informational); con-nyx dispatched (idle pattern continues); category immunity 0.6/1.0 per Fitz K<<N — Phase1 sees category, Phase2 closes it | 2026-04-23 |
| T4.3b DecisionEvidence runtime-invocation antibody | closed | `abd04ad` | 1 new test file (test-only, no src change); runs `evaluator_module.evaluate_candidate` end-to-end with fixture monkeypatches (Day0Router.route → stub returning bins-sized p_vector; ENS snapshot persistence stubs; DT7 `_read_v2_snapshot_metadata` → {}; MarketAnalysis/fetch_ensemble/validate_ensemble/resolve_strategy_policy stubs) and `min_order_usd=0.01` to admit sizing; wraps `DecisionEvidence.__init__` via `monkeypatch.setattr` with delegation back to original_init (preserves frozen-dataclass field assignment + __post_init__ validation); after `evaluate_candidate` returns, asserts (a) ≥1 `should_trade=True` decision, (b) ≥1 `__init__` call with `evidence_type="entry"`, (c) each accept `EdgeDecision.decision_evidence` is populated with statistical_method="bootstrap_ci_bh_fdr", fdr_corrected=True, sample_size=edge_n_bootstrap(); catches a future refactor that would silently bypass `src/engine/evaluator.py:L1700+` construction; includes 4-bin outcomes (left-shoulder + 2 finite + right-shoulder) + differentiated p_posterior/p_market so bin 0 carries a real positive edge through BH-selection; 1 new test passes; 3-line provenance header per CLAUDE.md rule; Plan-premise correction #10: Day0Signal symbol renamed to Day0Router.route during prior refactor — monkeypatch target updated; con-nyx deferred (idle pattern) | 2026-04-23 |
| T4.3 DecisionEvidence AST-walk call-site presence antibody | closed | `dc027bb` | 1 file modified (tests/test_entry_exit_symmetry.py, +new class TestDecisionEvidenceStaticCallSitePresence with 3 tests); plan-premise correction #11: plan cited `assert_symmetric_or_stronger` literal but actual method is `assert_symmetric_with` (src/contracts/decision_evidence.py:156) — tests use the correct name; AST-walks 2 src files at grep/CI time (pre-fixture): (1) `src/engine/evaluator.py` must contain `DecisionEvidence(evidence_type="entry", ...)` Call node (T4.1b accept-path wiring); (2) `src/engine/cycle_runtime.py` must contain `DecisionEvidence(evidence_type="exit", ...)` Call node (T4.2-Phase1 audit weak-burden construction); (3) `src/engine/cycle_runtime.py` must invoke `<...>.assert_symmetric_with(...)` Attribute Call (T4.2-Phase1 symmetry gate Phase2 will escalate to hard-fail); helper method `_decision_evidence_calls_with_type` matches both `Name("DecisionEvidence")` and `Attribute(attr="DecisionEvidence")` func forms; tests/* outside planning-lock so GREEN trivial; 18/18 tests in file pass (15 existing + 3 new); 60/60 T4 slab pass; test-only slice, zero source mutation — delta N/A; surrogate skipped (narrow scope, self-validating AST patterns); con-nyx dispatch continues idle pattern. **T4 family 5-layer immunity CLOSED**: T4.1a primitive + T4.1b entry-path write + T4.2-Phase1 exit-side read+audit + T4.3b runtime invocation + T4.3 static AST presence | 2026-04-23 |
| T5.b TickSize typed contract + exit-path NaN closure | closed | `c5c916b` | 4 files (1 new src + 2 src edits + 1 new test); NEW `src/contracts/tick_size.py` with frozen `TickSize` dataclass (value + currency Literal), `__post_init__` invariants (finite + > 0 + <= 0.5), `min_valid_price` / `max_valid_price` properties, lenient `clamp_to_valid_range` (Python max/min semantics — NaN does NOT propagate, produces 0.99 silent-coercion; lenient is documented as caller's responsibility), `TickSize.for_market(market_id=None, token_id=None)` classmethod single-truth factory, module-level `POLYMARKET_WEATHER_TICK` constant; `src/execution/executor.py::execute_exit_order` replaces `base_price = current_price - 0.01` (L231) with `tick.value` + replaces `max(0.01, min(0.99, limit_price))` (L239) with explicit `math.isfinite` NaN/±inf rejection BEFORE `tick.clamp_to_valid_range(...)` — this bundles the T5.a-LOW follow-up (surrogate flagged `execute_exit_order:269` NaN clamp leak; fix closed in-slice per Fitz C1 minimal-surface principle); `src/contracts/semantic_types.py::compute_native_limit_price` replaces same clamp with `POLYMARKET_WEATHER_TICK.clamp_to_valid_range` (lenient — upstream caller's entry path has T5.a ExecutionPrice boundary that still rejects NaN at CLOB seam); NEW `tests/test_tick_size.py` with 23 tests across 5 classes (invariants / derived properties / clamp behavior incl. the documented-quirk test where NaN produces 0.99 via Python max/min semantics / factory + module constant / exit-path integration 3 NaN±inf reject tests); 23/23 pass; baseline (pre-T5.b via git stash of executor.py + semantic_types.py) = 85 passed on 6-file panel; post-T5.b on 7-file panel = 108 passed (delta = 0 new failures, +23 from new test file); planning-lock GREEN on 4 files (src/contracts/** + src/execution/executor.py — both in lock zones); surrogate skipped (straightforward typed-contract replacement, pattern proven in T5.a); con-nyx dispatch continues idle pattern. **Deferred surrogate finding closed**: T5.a-LOW NaN clamp leak in exit path now rejects non-finite `limit_price` explicitly before CLOB contact with `malformed_limit_price` reason symmetric to T5.a entry-path guard | 2026-04-23 |
| T5.c NegRisk — SKIP typed contract + SDK passthrough audit antibody | closed-skip-plus-audit | `63c5c36` | 1 new test file (no src change); **plan-premise correction #12**: plan row T5.c proposed NEW `src/contracts/neg_risk.py` typed flag BUT qualified "verify py-clob-client behavior first — may reduce to typed passthrough". Verification confirms reduction: (a) py-clob-client auto-detects per token via `ClobClient.get_neg_risk` (client.py L441-448) with in-memory cache; (b) `create_order` consumes via auto-fallback (client.py L517-520 / L572-575); (c) Zeus src/ has ZERO neg_risk / negRisk / NegRisk references (grep-verified); (d) Polymarket weather markets are conceptually neg-risk (city-date bins are mutually exclusive) but neg-risk semantics are fully handled at SDK + on-chain exchange-contract layer — Zeus has no semantic role to play; (e) a `NegRiskMarket(bool)` wrapper with no invariants would add zero semantic guard beyond the annotation, violating Fitz K<<N ("don't add structure for problems that don't exist"). Resolution: skip typed contract; write SDK-passthrough audit reflecting operator-directed A+B combo; NEW `tests/test_neg_risk_passthrough.py` with 4 tests: (1) `ClobClient.get_neg_risk(token_id)` exists + signature accepts token_id (SDK contract), (2) `PartialCreateOrderOptions.neg_risk` field exists (SDK contract), (3) grep-style scan of src/ for `neg_risk`/`negRisk`/`NegRisk` string literals asserts zero hits (passthrough unbroken), (4) scan for `PartialCreateOrderOptions` instantiation asserts zero hits (override vector not present); 4/4 pass; if ANY of the 4 assertions fires in future, auto-signal to promote T5.c to real typed-contract slice (audit catches SDK change + Zeus drift simultaneously); planning-lock GREEN (tests/* only); surrogate skipped (narrow audit-only scope); con-nyx dispatch continues idle. T5.c resolved honestly per plan's pre-authored skip clause — not all slices must ship code; some ship evidence-based negations | 2026-04-23 |
| S1.3 T2.g un-monkeypatched DT7 fail-closed verification | closed | pending | closes T2.d/e/f-deferred T2.g slice via real ensemble_snapshots_v2 fixture path. NEW module-level helper `_seed_ensemble_snapshots_v2_row(conn, city, target_date, metric, boundary_ambiguous, causality_status)` INSERTs minimal valid v2 row with INV-14 canonical identity (temperature_metric='high' + physical_quantity='mx2t6_local_calendar_day_max' + observation_field='high_temp' + data_version='tigge_mx2t6_local_calendar_day_max_v1'). 3 existing TODO(T2.g) tests updated to use the helper with boundary_ambiguous=0 — replacing natural-empty-v2 short-circuit with real schema read path. NEW TestDT7ScemaPathActuallyRuns class with 2 positive + negative antibody tests: (1) boundary_ambiguous=0 row → gate returns False (no refusal); (2) boundary_ambiguous=1 row → gate returns True (refusal fires). Proves DT7 is WIRED, not just dormant. Catches any future upstream `_read_v2_snapshot_metadata` schema/query drift that the 3 TestSelectionFamilySubstrate tests would not catch via bypass. Regression: TestSelectionFamilySubstrate 10 pass + 1 xfail (unchanged); TestDT7ScemaPathActuallyRuns 2 new pass. Zero file additions (test_fdr.py already registered); planning-lock GREEN. | 2026-04-23 |
| S3.1 data-readiness-tail zeus_current_architecture.md INV-14 + provenance refresh | closed | `092d263` | closure-banner architect-P1 flag closed; added 33-line "Canonical settlement row invariants" subsection inside §4.2 Settlement Truth documenting (1) INV-14 identity spine (temperature_metric + physical_quantity + observation_field + data_version), (2) provenance_json required-keys sidecar, (3) settlements_authority_monotonic trigger (VERIFIED↔UNVERIFIED block + QUARANTINED→VERIFIED reactivated_by gate, S2.1 hardened), (4) settlements_verified_{insert,update}_integrity trigger pair (S2.2 structural AP-2), and the explicit note that SettlementSemantics.assert_settlement_value() is still required at every write boundary. Isolate-and-restore pattern used (file was co-tenant-dirty +16/-20; saved to /tmp, reverted, applied my edit, committed clean 33-line additive diff, restored co-tenant state). Planning-lock GREEN (docs/authority/** touch confirmed). No test work; doc-only slice. Authority refresh closes the last LOW-risk data-readiness-tail follow-up | 2026-04-23 |
| A4 forensic C5/C6/C7 assessment — C7 applied, C5+C6 documented-deferred | closed | pending | **C7 CLOSED**: `observation_instants_v2` INV-14 spine extension. 6 ALTER TABLE ADD COLUMN statements added to `src/state/schema/v2_schema.py` after existing 3-column ALTER block: `temperature_metric`, `physical_quantity`, `observation_field`, `training_allowed` (DEFAULT 1), `causality_status` (DEFAULT 'OK'), `source_role`. Applied to live DB via init_schema — 26→32 cols; 1,813,662 rows preserved (O(1) metadata-only ALTER per SQLite semantics). Training-input boundary can now distinguish canonical rows from fallback-mixed rows at the row level — per-row identity check unblocked. **C5 BLOCKED**: settlements market_slug retrofit — live probe shows `market_events` table is EMPTY (0 rows), so no internal data source for backfill. Would require Polymarket Gamma API lookup per settlement row (1,561 API calls). Out of scope; moved to "external-data-dependent" follow-ups bucket. **C6 DEFERRED**: 39,431 VERIFIED observations have empty `provenance_metadata`. Reconstruction is mechanically possible (synthesize from `source` + `imported_at` + `station_id`) but the reconstructed metadata would be LOW-confidence (we do not know exact fetch-time / station_id at the time of original write) — risks implying forensic-grade certainty where only approximation exists. Forensic-audit C6 classified this as "inheritance-gap bounded by decision_time_snapshot_id reference" (not capital-risk-critical today). Documented in known_gaps.md for future dedicated-packet handling. | 2026-04-24 |
| A3 DR-33-B append_many_and_project atomicity refactor | closed | `2a62623` | **Closes the torn-state window** documented by T4.0 design doc F3 HIGH finding. Pre-DR-33-B `append_many_and_project` used `with conn:` at `src/state/ledger.py:197` — Python sqlite3 `with conn:` commits + releases the innermost active SAVEPOINT on clean exit. `cycle_runtime.py:1246-1252` explicitly placed `_dual_write_canonical_entry_if_available` OUTSIDE the `sp_candidate_*` SAVEPOINT guard with a code comment acknowledging the collision — leaving trade_decisions+execution_report writes landed while position_events could still fail mid-flight (torn state). **Fix**: replaced `with conn:` with explicit `SAVEPOINT sp_ampp_<secrets.token_hex(6)>` + `RELEASE` / `ROLLBACK TO SAVEPOINT` pattern (nestable, no L30 collision). Callers can now invoke from inside their own SAVEPOINT without silent release; the documented torn-state in cycle_runtime can be closed in a follow-up slice that lifts the dual-write INSIDE sp_candidate_* (NOT done here — this slice just removes the structural barrier). Caller inventory (6 production sites): `src/engine/cycle_runtime.py:321,330` + `src/state/chain_reconciliation.py:189,197,234,243` + `src/execution/fill_tracker.py:153,166` + `src/execution/harvester.py:202,257` + `src/execution/exit_lifecycle.py:167,213` — all continue to work unchanged because the function's external contract is identical (same args, same commit-on-success semantics at top level). NEW tests/test_append_many_and_project_nested_savepoint.py with 6 antibodies: (1) top-level call still commits; (2) nested call leaves outer SAVEPOINT intact (core DR-33-B invariant); (3) nested release-then-commit persists writes; (4) inner exception rolls back inner only; (5) multiple nested invocations have unique SAVEPOINT names; (6) AST structural antibody — `with` statements count == 0 AND ≥2 SAVEPOINT conn.execute calls in function body. 6/6 pass. Cross-suite 109/110 pass (1 pre-existing T3.2 `_Logger.warning` harness gap out-of-scope). Zero new regressions. Memory rule L30 fully honored: no `with conn:` inside SAVEPOINT collision possible going forward for the main canonical append path. | 2026-04-24 |
| A1 apply 4 pending init_schema migrations to live DB | closed | `56b0749` | Applied T3.3 + S2.1 + REOPEN-1 + S2.2 to state/zeus-world.db via `init_schema(conn)` one-off. Pre-migration SHA-256 snapshot `state/zeus-world.db.pre-migration-batch_2026-04-24` (digest `f801da5d233fd55e...`). Post-migration state: forecasts 13→15 cols (rebuild_run_id + data_source_version added) / settlements triggers 1→3 / authority_monotonic v2 predicate (json_type check) installed / 1,469 VERIFIED + 92 QUARANTINED = 1,561 settlements unchanged / writer-path verification PASS via savepoint-rollback test insert of rebuild_run_id column. `k2_forecasts_daily` cron will succeed on next 07:30 UTC tick; v1 trigger bypass shapes now structurally rejected at DB boundary. | 2026-04-24 |
| A2 REOPEN-2 settlements UNIQUE migration (live executed) | closed | pending | Real table-rebuild migration: UNIQUE(city, target_date) → UNIQUE(city, target_date, temperature_metric). Unblocks dual-track — HIGH + LOW rows for same (city, target_date) now commit; same-metric collisions still correctly rejected. Procedure: (a) scratch-DB dry-run on /tmp/zeus_scratch_reopen2.db first — 1,561 rows migrated losslessly, dual-track insert works, groups preserved; (b) pre-migration snapshot `state/zeus-world.db.pre-reopen2_2026-04-24` with SHA-256; (c) code changes to `src/state/db.py`: CREATE TABLE IF NOT EXISTS settlements inlines INV-14 columns + new UNIQUE (fresh-DB path); new REOPEN-2 migration block runs between ALTER and trigger install, idempotent via sqlite_master SQL inspection; (d) live DB `init_schema(conn)` applied. Post-migration live-DB verification: new UNIQUE in place / 1,561 rows preserved / 1,469 VERIFIED + 92 QUARANTINED groups preserved / INV-14 spine populated on all 1,561 rows / provenance_json populated on all 1,561 rows / all 3 triggers re-installed on rebuilt table. NEW tests/test_settlements_unique_migration.py with 7 antibodies (fresh-DB new UNIQUE / fresh dual-track / fresh same-metric collision / legacy migration row-count preservation / idempotency / trigger survival after rebuild / authority-group preservation). 7/7 pass + cross-suite 30/30 pass with S2.1 + S2.2 trigger tests. Pre-flip BLOCKER for DR-33-C now CLEARED. | 2026-04-24 |
| critic-opus follow-up: 3 doc corrections | closed | `3eaa772` | addresses critic-opus 5-slice batch verdict LOW findings: (1) MUST-FIX s/four triggers/three triggers/ at `docs/authority/zeus_current_architecture.md:119` with explicit trigger-name enumeration for reader clarity; (2) S2.2 scope-bleed provenance cross-reference note added to work_log row (3 bundled files — tests/test_fdr.py + tests/test_market_analysis.py + T2_receipt.json — originated from co-tenant commit `7d064be` T2.d.1 as in-progress xfail scaffolding); (3) S6.1 DR-33-B stub extended with caller-inventory checklist (grep result + per-caller transaction-boundary classification: `with conn:` / inside SAVEPOINT / bare) to prevent next-session agent from re-deriving work. Doc-only slice; no src/test mutation; planning-lock GREEN | 2026-04-23 |
| S2.2 data-readiness-tail structural AP-2 prevention on settlements | closed | `f8f403e` (+3 co-tenant-staged files bundled: tests/test_fdr.py T2.g xfail scaffolding, tests/test_market_analysis.py T2.c/T6.3 xfail placeholder, T2_receipt.json — all originated from co-tenant commit `7d064be`; bundle is forward-looking non-regression, xfail strict=False pins future transitions to soft-signal per critic-opus LOW finding. `git reset HEAD` discipline added to per-slice protocol post-S2.2 to prevent recurrence) | closure-banner AP-2 structural-prevention antibody; new pair of triggers `settlements_verified_insert_integrity` (BEFORE INSERT) + `settlements_verified_update_integrity` (BEFORE UPDATE OF authority/settlement_value/winning_bin) enforces minimum VERIFIED-row invariants at DB-write time (non-null settlement_value + non-empty winning_bin) when writer bypasses the social SettlementSemantics.assert_settlement_value() gate. WHEN clauses gate on `authority='VERIFIED'` so QUARANTINED rows (92 existing, 49 with null settlement_value, 92 with null winning_bin) are NOT affected — quarantine is the legitimate semantic for "can't determine canonical value". Pre-apply live-DB probe: 1,469 VERIFIED rows all pass (0 null settlement_value, 0 null/empty winning_bin) — no legitimate historical rows rejected. DROP+CREATE pattern for v2 propagation. NEW tests/test_settlements_verified_row_integrity.py with 12 antibodies across 5 classes: INSERT rejection (3 cases) / INSERT acceptance (1) / INSERT non-regression on non-VERIFIED (3) / UPDATE rejection (3 cases: QUARANTINED→VERIFIED with null, VERIFIED settlement_value→NULL, VERIFIED winning_bin→empty) / idempotency (2: repeat init_schema + both triggers installed). 12/12 pass. S2.1 trigger coexists cleanly (S2.1 + S2.2 tests together: 23/23 pass). Test registered in test_topology.yaml::useful_regression via isolate-and-restore. Planning-lock + map-maintenance GREEN. Category immunity 1.0/1.0 on AP-2 structural-prevention: VERIFIED row with half-populated required fields is now structurally impossible at DB boundary, independent of whether the writer respected the social gate. Live-DB migration pending: trigger install runs on next init_schema | 2026-04-23 |
| S2.5 data-readiness-tail harvester winning_bin format unification (NH-E2) | closed | `c7784ec` | closure-banner NH-E2 antibody; S2.5 scope (delete `_format_range` + remove unicode ≥/≤ emitters) VERIFIED-ALREADY-DONE by DR-33-A (commit `9026192`) — `grep -rn "_format_range" src/ tests/ scripts/` returns 0 hits (only a retirement-comment reference), `grep -rn "'≥\|'≤\|\"≥\|\"≤"` in src/ returns only 1 hit in `src/execution/harvester.py:628` which is a DOCSTRING explaining why text form is used. **Value-add this slice**: 2 NEW AST-walk antibody tests appended to `tests/test_harvester_dr33_live_enablement.py` (file already registered; no new files, no test_topology.yaml overhead): (1) `test_S25_format_range_function_never_reappears` — walks harvester AST asserting no FunctionDef with forbidden names (`_format_range`/`format_range`/`_format_bin_range`) AND string literals never START with ≥/≤ (prose/docstring carve-out via `startswith` semantics); (2) `test_S25_every_canonical_label_passes_strict_parser` — round-trips every `_canonical_bin_label` output through S2.4's strict `_parse_canonical_bin_label`, pinning emitter/parser contract at CI. 27/27 pass (25 existing + 2 new). Category immunity 1.0/1.0 on the re-introduction category (future refactor that brings back `_format_range` OR starts emitting unicode shoulders flips test red before merge). Planning-lock GREEN on 1 file (tests/* only). Surrogate critic skipped (narrow scope, self-validating AST patterns); con-nyx idle pattern continues | 2026-04-23 |
| S2.4 data-readiness-tail strict canonical bin label parser (NH-E1) | closed | `cdfd558` | closure-banner rule 15 (NH-E1) antibody; added `_parse_canonical_bin_label(label)` helper in `src/data/market_scanner.py` using `re.fullmatch` on 4 canonical shapes (point / finite-range / left-shoulder / right-shoulder). Strict parser REJECTS near-canonical garbage (trailing/leading whitespace, float degrees, unicode ≥/≤ shoulders, non-string inputs) that the tolerant `_parse_temp_range` silently accepted. Tolerant parser left unchanged because it serves a different purpose (free-form market-question parsing). NEW tests/test_parse_canonical_bin_label.py with 29 antibodies: 11 round-trip parametrized (every _canonical_bin_label output parses back) + 14 rejection cases + 4 unicode rejections + 5 non-string rejections + 2 non-regression (tolerant parser unaffected) + orthogonality pin. 29/29 pass. Category immunity 0.9/1.0 — makes silent trailing-garbage misparse impossible for the strict parser; remaining 0.1 gap because the tolerant parser is still the one used by harvester.py:610 for market questions (intentional — different responsibility). Test registered via isolate-and-restore. Planning-lock + map-maintenance GREEN | 2026-04-23 |
| S2.1 data-readiness-tail reactivated_by value-validation in trigger | closed | `69520ba` | closure-banner item 4 closed; src/state/db.py settlements_authority_monotonic trigger WHEN clause extended beyond `IS NULL` check to add `json_type != 'text'` AND `length > 0` — rejects 5 bypass shapes (reactivated_by = false / 0 / empty-string / object / array) that pre-S2.1 all passed IS NOT NULL silently. Trigger rewritten via DROP + CREATE (not CREATE IF NOT EXISTS) so legacy DBs with weaker v1 trigger get upgraded on next init_schema. Idempotency preserved via DROP IF EXISTS. NEW tests/test_settlements_authority_trigger.py with 11 antibodies (5 bypass-rejection + 3 backward-compat + 2 acceptance + 1 idempotency). 11/11 pass. Test registered in test_topology.yaml::useful_regression via isolate-and-restore. Planning-lock + map-maintenance GREEN. Category immunity 1.0/1.0 — the 5 presence-only bypass classes are structurally impossible now; only non-empty text passes the reactivation gate | 2026-04-23 |
| REOPEN-1 forecasts schema ALTER fix (URGENT — live failure closed) | closed | `619b278` | critic-opus forensic-audit-triage CONFIRMED finding C1 capital-risk HIGH: production k2_forecasts_daily cron FAILED every 30 min with literal "table forecasts has no column named rebuild_run_id" (state/scheduler_jobs_health.json:k2_forecasts_daily.last_failure_reason). Root cause: src/data/forecasts_append.py:256-262 INSERTs 14 columns incl. rebuild_run_id + data_source_version; src/state/db.py:653-668 CREATE TABLE forecasts declared only 12 columns; legacy zeus-world.db predates the writer change and CREATE TABLE IF NOT EXISTS no-ops (same mechanical pattern as T3.3 position_current). Fix: (1) add rebuild_run_id TEXT + data_source_version TEXT to CREATE TABLE forecasts declaration, (2) add ALTER TABLE forecasts ADD COLUMN loop in init_schema() after L757 mirroring the trade_decisions ALTER block at L748-752, (3) new tests/test_forecasts_schema_alignment.py with 5 structural-alignment antibodies (fresh-DB / legacy-DB migration / idempotency / writer-insert-cols-declared / rebuild_run_id-sanity). 5/5 pass. Registered in architecture/test_topology.yaml::useful_regression via isolate-and-restore (test_topology.yaml was co-tenant-dirty with test_trust_policy section; staged only my 1-line addition via git-checkout-HEAD + re-apply-edit pattern, zero scope bleed). Planning-lock GREEN + map-maintenance precommit GREEN. Critic-opus citation correction: original finding cited src/state/db.py:209 but L209 is observations.rebuild_run_id; actual forecasts CREATE TABLE is at L653; plan-premise-correction-12 applied inline. **Live-DB migration requires operator action**: daemon restart OR manual `python -c "from src.state.db import init_schema, get_world_connection; c=get_world_connection(); init_schema(c); c.commit(); c.close()"` to apply ALTER to running DB; NOT done by this slice per Zeus runtime-safety convention. This is Phase 2 (data-readiness tail) but recorded in midstream packet as sibling slice per S2.3 packet-routing precedent | 2026-04-23 |
| S2.3 data-readiness-tail SHA-256 rollback snapshot sidecars | closed | pending | data-readiness closure follow-up (first_principles.md closure-banner item 5: MD5 is collision-broken); new `scripts/snapshot_checksum.py` (class enforcement, registered in `architecture/script_manifest.yaml`) with 4 MiB chunked streaming sha256 — existing `scripts/_tigge_common.py:145-146 _file_sha256` + `scripts/topology_doctor_code_review_graph.py:137-138 sha256_file` both blob-read (unsafe for 1.8 GB snapshots); 4 new `.sha256` sidecars committed for `state/zeus-world.db.pre-{pg,pb,pf,pe}_2026-04-23` (gitignored-negated alongside `.md5` via line 32 of `.gitignore`); `.md5` sidecars PRESERVED for audit continuity — not removed; 3 self-verified antibody probes pass: (1) shape probe (16 uppercase `A`s → "wrong shape" exit 1), (2) hash-mismatch probe (`deadbeefcafebabe...` valid-shape wrong-value → "HASH MISMATCH" exit 1), (3) OK probe via `--verify-all --json` returns `{ok:true,count:4}`; planning-lock GREEN on 4-file core (`scripts/snapshot_checksum.py`, `architecture/script_manifest.yaml`, `.gitignore`, sidecars); data-readiness-tail-packet creation aborted because 5 of 6 companion-doc registries (docs/AGENTS.md, docs/README.md, architecture/topology.yaml, architecture/docs_registry.yaml, docs/operations/AGENTS.md) are co-tenant-dirty — honoring them via isolate-and-restore would create scope bleed; pivoted to recording slice in midstream packet as "data-readiness-tail" sibling work; surrogate + critic-opus dispatched | 2026-04-23 |
| T5.d RealizedFill + fresh SlippageBps typed contracts | closed | `782d2af` | 3 files (2 new src + 1 new test), no existing src touched — contracts land as shells ready for consumer wiring; NEW `src/contracts/slippage_bps.py` with frozen `SlippageBps` dataclass carrying `value_bps: float` (non-negative magnitude) + `direction: Literal["adverse","favorable","zero"]` (explicit sign semantics — NO alias on TemperatureDelta per plan directive to avoid cross-domain type-reuse category bug); `__post_init__` enforces finite + non-negative + direction/value consistency (zero↔0, adverse/favorable↔>0); `fraction` property = value_bps/10000 always non-negative; `from_prices(actual, expected, side)` classmethod derives side-dependent direction (buy adverse when actual > expected; sell adverse when actual < expected) with currency-match + expected > 0 invariants; NEW `src/contracts/realized_fill.py` with frozen `RealizedFill(execution_price, expected_price, slippage, side, shares, trade_id)` threading 3 typed contracts (T5.a ExecutionPrice × 2 + SlippageBps); `__post_init__` enforces currency match + side ∈ {buy,sell} + shares finite > 0 + trade_id non-empty; `from_prices(...)` classmethod is the preferred factory deriving slippage from price pair via SlippageBps.from_prices so the record cannot drift between slippage and inputs; NEW `tests/test_realized_fill.py` with 32 tests across 6 classes: SlippageBps invariants (9) / fraction derivation (3) / from_prices side-dependent direction + currency/zero-expected rejection (8) / RealizedFill invariants (7) / factory consistency (4) / factory currency-rejection (1); 32/32 pass; T5 slab 59/59 total (32 T5.d + 4 T5.c + 23 T5.b); test-only contract-shell slice, zero existing src mutation — consumer wiring (integrate into OrderResult or fill_tracker) left as T5.d-followup / future slice; planning-lock GREEN on 3 files (src/contracts/** in lock zones); surrogate skipped (contract pattern proven in T5.a + T5.b); con-nyx dispatch continues idle. **T5 family CLOSED** on CONDITIONAL gate: T5.a ExecutionPrice (`abd5bb6`) + T5.b TickSize (`c5c916b`) + T5.c skip-plus-audit (`63c5c36`) + T5.d RealizedFill+SlippageBps (`782d2af`) = **3/3 effective** (T5.c denominator reduced via skip) | 2026-04-23 |
| T1.c 8-skip audit in test_live_safety_invariants.py | closed-audit-plus-followup | pending | 1 file modified (tests/test_live_safety_invariants.py — skip-reason text only, ZERO behavior change); **plan-premise correction #13**: plan cites L211/265/332/586/872/1224/1533/1566 but actual lines are L214/268/335/589/875/1227/1536/1569 (off by 3 — T1.a's 3-line provenance header prepend caused shift). Batch un-skip + pytest probe (2026-04-23): ALL 8 un-skipped tests FAIL under current production reality — ZERO natural-pass removals. Classification per plan directive "do not let surprises gate T1.c": (1) L589 Phase2 KEEP_LEGITIMATE — paper-mode removed (Phase2 canonical-only); test validates deprecated behavior; retained as documentation antibody; (2) 5× P9 pending rewrite (L214/268/335/875/1227) — reconcile/entry-path no longer emits POSITION_LIFECYCLE_UPDATED via log_trade_entry→position_events; canonical emission moved to build_entry_canonical_write + append_many_and_project post-T4.1b; canonical event shape is POSITION_OPEN_INTENT/ENTRY_ORDER_POSTED/ENTRY_ORDER_FILLED (not legacy POSITION_LIFECYCLE_UPDATED-with-source shape); (3) 2× P4 pending relocation-or-obsolete (L1536/1569) — `_load_portfolio_from_json_data` contamination guard path deleted when canonical loader replaced JSON fallback; current loader `src/state/portfolio.py::load_portfolio` + `src/state/portfolio_loader_policy.py` may carry env-scoping implicitly via SQL WHERE env=? predicates (audit deferred). ALL 8 skip reasons rewritten with T1.c-audit timestamp + per-test current-fact explanation + reference to T1.c-followup slice; plan acceptance criterion `--collect-only SKIPPED ≤ 1` explicitly UNMET (actual: 8) with documented rationale invoking plan L164-168 surprise-tolerance clause; T1.c-followup slice scoped in T1_receipt.json (size 4h, deps: T4.1b CLOSED, acceptance: SKIPPED ≤ 1); regression delta ZERO (reason-only edits); planning-lock GREEN (tests/* only); surrogate skipped (scope-documented audit, no src mutation); con-nyx dispatch continues idle | 2026-04-23 |
| T1.f T1 family currency receipt | closed | `9b3e4bd` | 1 new file `docs/operations/task_2026-04-23_midstream_remediation/T1_receipt.json` with full T1 family closure status: T1.a/T1.b/T1.d/T1.e fully closed + T1.c audit-with-followup + T1.f this receipt = 6/6 ship count. Documents T1.c-followup slice spec (size 4h, scope: rewrite 5 P9 tests against canonical event shape + decide P4 RELOCATE-vs-OBSOLETE for 2 contamination guard tests). Cross-references packet plan / work_log / receipt / midstream fix plan workbook / midstream trust verdict. Doc-only slice; planning-lock GREEN; zero test impact. **T1 family CLOSED** with explicit follow-up flag | 2026-04-23 |
| T1.c-followup 5 P9 rewrite + 2 P4 OBSOLETE audit | closed | `480e4f3` | (unchanged; see prior entry) | 2026-04-24 |
| T2.d.1 DT7 fixture + 3 SelectionFamilySubstrate tests rewrite | closed | pending | 1 file modified (tests/test_fdr.py, 3 tests in TestSelectionFamilySubstrate — T2.d + T2.e + T2.f closed in this slice). **Operator flagged critic-prompt degradation** ("多轮没有发现任何问题，是任何问题都没发现") — fixed by rewriting surrogate prompt with 10 explicit adversarial asks, no self-justification language, specific failure-mode probes. Surrogate returned **REQUEST_CHANGES with 6 findings** (HIGH×1 + MED×2 + LOW×3), all addressed inline. **F1 HIGH**: `_read_v2_snapshot_metadata` → {} stub was REDUNDANT — `init_schema(conn)` calls `apply_v2_schema` which creates empty `ensemble_snapshots_v2`; natural empty-table query returns {}; DT7 gate passes WITHOUT stubbing. Verified empirically (10/10 still pass after stub removal). Stub would have cemented bad precedent + fought T2.g's real-DT7 follow-up. **F2 MED**: comment falsely presented stub as load-bearing — removed with stub. **F3 MED**: added `TODO(T2.g)` breadcrumbs at top of each of 3 tests flagging DT7 natural-bypass for real-fixture replacement. **F4 LOW**: clarified `FakeAnalysis.__init__` silently drops production kwargs. **F5 LOW**: expanded `FakeEns` comment enumerating all 4 attr-read sites (evaluator.py:991, 1284, 1292, 1842). **F6 LOW**: `Day0Router.route` replacement HIGH-only noted. Plan-premise correction #14: Day0Signal symbol refactored to Day0Router.route. 8 structural fixes (Day0Router monkeypatch target, observation dict→SimpleNamespace, left-shoulder bin added, FakeEns extra attrs, FakeDay0Signal.p_vector n_bins-sized, FakeAnalysis.p_market/p_posterior n_bins-sized with bin-0-positive-edge, hypothesis_count 6→8, ENS persistence stubs). Delta (L28 stash-verified): pre-T2.d.1 7F/64P → post 4F/67P (+3 passes = 3 T2.d/e/f rescued, -3 failures, 0 new failures); planning-lock GREEN. **T2.d + T2.e + T2.f closed**. con-nyx dispatched (idle continues) | 2026-04-24 |

## T4.1a — execution notes (2026-04-23)

Narrower split of plan T4.1 (full entry-wiring). T4.1a lands the
persistence PRIMITIVE per T4.0 Option E; T4.1b (follow-up, larger)
will wire it into the evaluator → lifecycle_events → position_events
entry-event emission path.

### Implementation

- `src/contracts/decision_evidence.py`:
  - Add `DECISION_EVIDENCE_CONTRACT_VERSION = 1` module constant.
  - Add `UnknownContractVersionError(ValueError)` class.
  - Add `to_json(self) -> str` inside the class body (not module-level
    monkeypatch — my first-draft workaround was based on the false
    premise that frozen dataclasses block body methods; surrogate
    critic HIGH finding corrected). Emits canonical payload
    `{"contract_version": 1, "fields": {...}}` via
    `json.dumps(..., sort_keys=True)` for byte-stable idempotency.
  - Add `from_json(cls, payload) -> DecisionEvidence` classmethod.
    Strict `type(version) is int and version == 1` guard prevents
    `True == 1` collision. Malformed JSON → plain `ValueError`.
    Missing keys / non-object payload / unknown fields → `UnknownContractVersionError`.
    `__post_init__` `ValueError`s propagate unwrapped so callers
    can distinguish schema drift (UnknownContractVersionError) from
    invalid-data (bare ValueError).
- NEW `tests/test_decision_evidence_persistence.py` with 18 tests
  across 5 classes: shape (3), round-trip (2), version-drift (5 —
  including the critical `True == 1` collision guard), malformed
  (6), `__post_init__` propagation (2).

### Three critic findings addressed inline

Surrogate `code-reviewer@opus` verdict: REQUEST CHANGES. Findings:

- **HIGH — method-attachment pattern based on false premise**: first
  draft attached `to_json` / `from_json` at module level via
  `DecisionEvidence.to_json = _to_json` monkeypatch. Critic noted
  `frozen=True` blocks only instance mutation, not class body
  methods. Fixed: moved methods into class body, deleted false
  comment + `# type: ignore` shields.
- **MEDIUM — version-drift coverage gaps**: critic called out
  `True == 1` collision (no strict-type guard), missing null version
  test, missing `__post_init__` propagation test. Fixed: strict
  `type(version) is int` guard in `from_json`; added 4 tests
  (null version, boolean-True version, sample_size=0 propagates bare
  ValueError, confidence_level=1.5 propagates bare ValueError).
- **LOW — asdict future risk**: `asdict(self)` recursively flattens
  nested dataclasses. Safe today (all fields JSON-native primitives)
  but brittle if a future contract bump adds Enum / nested record.
  Documented the constraint in `to_json` docstring.

Non-blocking finding deferred: Literal runtime enforcement
(`evidence_type="banana"` constructs) is pre-existing and out of
T4.1a scope; flagged for future slice.

### Regression evidence

- `pytest -q tests/test_decision_evidence_persistence.py tests/test_entry_exit_symmetry.py`
  → `33 passed in 0.09s` (18 new + 15 pre-existing D4 symmetry).
- No existing caller affected: `to_json`/`from_json` are additive
  methods on a frozen dataclass; existing instances continue to work
  unchanged.

### Follow-up: T4.1b

Outstanding: wire `DecisionEvidence.to_json` into entry-path event
emission so `ENTRY_ORDER_POSTED.payload_json` carries a
`decision_evidence` nested key. Requires changes to
`src/engine/evaluator.py` (construct evidence at entry), the
`EdgeDecision → event` chain, and `src/state/lifecycle_events.py`
(or equivalent emission helper). Larger slice, ~3-4h, needs its own
critic review. Flagged for next wave.

## T4.1b — execution notes (2026-04-23)

### Plan-premise correction #8

Fix-plan T4.1 row cited `src/engine/evaluator.py:724, 1307` as entry
call sites. T4.0 design doc rev2 cited `evaluator.py:753/778/803/815/
832/842/866/882/901/912` as `EdgeDecision` construction sites.
Grep-verified 2026-04-23:

- L724 / L1307 are NOT `EdgeDecision(...)` constructions (L724 pre-decision
  signal building; L1307 is `entry_validations.append("bootstrap_ci")`).
- All 10 sites L753/778/803/815/832/842/866/882/901/912 are
  `EdgeDecision(False, ...)` — **rejection paths**.
- The actual accept path is `evaluator.py:1700-1726`
  `decisions.append(EdgeDecision(should_trade=True, edge=edge, ...))`.
  It is the ONLY `should_trade=True` site in the file.

### Implementation (4 src + 1 test)

1. **`src/contracts/decision_evidence.py`**: no change (T4.1a primitive
   already provides `to_json` + `from_json` + `contract_version=1`).
2. **`src/engine/lifecycle_events.py`**:
   - Import `DecisionEvidence`.
   - `_entry_event_payload(position, *, phase_after, decision_evidence=None,
     decision_evidence_reason=None)` — when `decision_evidence` is not None,
     add key `"decision_evidence_envelope": decision_evidence.to_json()`
     (verbatim string — surrogate HIGH finding flipped Q2 from nested dict);
     when `decision_evidence_reason` is not None, add key
     `"decision_evidence_reason": <str>`.
   - `_entry_event(...)` and `build_entry_canonical_write(...)` threaded
     through; both keys apply to `ENTRY_ORDER_POSTED` only (Q1 = T4.0
     design doc rule; `POSITION_OPEN_INTENT` precedes the statistical
     decision fully materializing; `ENTRY_ORDER_FILLED` arrives after the
     decision frame has released).
3. **`src/engine/cycle_runtime.py`**:
   - Import `DecisionEvidence`.
   - `_dual_write_canonical_entry_if_available(..., decision_evidence=None)`
     signature extension; threads to `build_entry_canonical_write`.
   - Accept-path call site at `L1131-1136` passes
     `decision_evidence=getattr(d, "decision_evidence", None)`.
4. **`src/engine/evaluator.py`**:
   - Import `DecisionEvidence`.
   - `EdgeDecision` dataclass at `L97-130` grows trailing optional field
     `decision_evidence: Optional[DecisionEvidence] = None` (positional-
     stability preserved per surrogate LOW finding; all 30+ callers use
     kw args).
   - Accept site at `L1700+` constructs evidence from
     `edge_n_bootstrap()` (sample_size) + `DEFAULT_FDR_ALPHA`
     (confidence_level) — both pre-existing imports (`L25`, `L52`).
     Surrogate had suggested adding a `fdr_alpha()` helper to config;
     independent grep found the central source already at
     `src/strategy/fdr_filter.py:19` as `DEFAULT_FDR_ALPHA = float(
     settings["edge"]["fdr_alpha"])`. No new helper needed.
5. **`src/execution/exit_lifecycle.py`**: backfill-path
   `build_entry_canonical_write(entry_snapshot,
   source_module="src.execution.exit_lifecycle:backfill",
   decision_evidence_reason="backfill_legacy_position")` — surrogate HIGH
   finding: without this sentinel T4.2-Phase1 audit would flag every
   legacy position's exit as asymmetry. Scope-addition: 4 files → 5.
6. **`tests/test_decision_evidence_entry_emission.py`** (new, 12 tests):
   - TestEntryEventPayloadEnvelope: 4 (accept posts envelope; intent/
     filled don't; rejection no key)
   - TestRoundTripRehydration: 2 (envelope rehydrates; SQL `json_extract`
     pattern returns string directly for T4.2-Phase1 readiness)
   - TestIdempotency: 1 (same decision_id + evidence → byte-identical
     payload across runs)
   - TestBackfillReasonSentinel: 2 (sentinel on ENTRY_ORDER_POSTED only,
     no leak to siblings)
   - TestFillOnlyBuilderScopeBoundary: 1 (fill_tracker path never emits
     either key)
   - TestPayloadBackwardCompatibility: 1 (default call pins pre-slice key
     set frozenset byte-exact)
   - TestEntryEventPayloadUnitAccess: 1 (both keys simultaneously)
   - 3-line provenance header per CLAUDE.md rule.

### Six surrogate critic findings (all integrated inline before commit)

Surrogate `code-reviewer@opus` pre-code design review verdict:
REQUEST_CHANGES.

- **HIGH Q2 payload format**: original nested-dict proposal required
  `json.dumps(..., sort_keys=True)` re-wrap to verify envelope on read.
  Flipped to string value storage: payload key is
  `decision_evidence_envelope: str` (verbatim `to_json()` output);
  read-side uses
  `DecisionEvidence.from_json(json_extract(payload_json,
  '$.decision_evidence_envelope'))` directly. Eliminates double-
  serialization invariant.
- **HIGH backfill path missed**: `src/execution/exit_lifecycle.py:181`
  also calls `build_entry_canonical_write`. Without sentinel, T4.2-Phase1
  audit would false-positive on every legacy-position exit. Added
  `decision_evidence_reason="backfill_legacy_position"` sentinel;
  scope grew from 4 files to 5.
- **MEDIUM α source**: `confidence_level=0.10` was hardcoded in
  proposal; surrogate suggested adding `fdr_alpha()` helper to config.
  Independent grep revealed central source already exists as
  `DEFAULT_FDR_ALPHA` at `src/strategy/fdr_filter.py:19`. Used
  existing constant instead of creating parallel one.
- **MEDIUM `consecutive_confirmations=1` semantics**: added inline
  comment at construction site documenting "1 robust confirmation =
  CI_lower > 0 across n_bootstrap=edge_n_bootstrap() draws" to prevent
  future readers from mis-interpreting as "1 bootstrap iteration".
- **LOW positional stability**: new `decision_evidence` field placed at
  END of `EdgeDecision` dataclass field list (after
  `edge_context_json`). All 30+ call sites use kw args so positional
  order is not strictly required, but trailing-placement is free
  insurance.
- **LOW scope-boundary test**: included explicit
  `TestFillOnlyBuilderScopeBoundary` — pins that fill_tracker path's
  `build_entry_fill_only_canonical_write` emits neither envelope nor
  reason (Q1 boundary enforcement).

### Regression evidence (L28 delta-direction, not absolute count)

- **Narrow scope (3 T4 files)**: `pytest -q
  tests/test_decision_evidence_entry_emission.py
  tests/test_decision_evidence_persistence.py
  tests/test_entry_exit_symmetry.py` → 45 passed in 0.10s (12 new + 18
  T4.1a + 15 entry_exit_symmetry).
- **Broad 17-file regression on pre-T4.1b source (git stash verify)**:
  24 failed / 476 passed / 34 skipped / 1 xfailed.
- **Broad 18-file regression post-T4.1b (+new test file)**: 24 failed /
  488 passed / 34 skipped / 1 xfailed. **Delta: ZERO new failures; +12
  passes (all from the new test file).**
- The 24 pre-existing failures are all known out-of-T4.1b-scope:
  `test_cycle_runtime_entry_dual_write_helper_skips_*` (T3.2-logged
  `_Logger.warning` harness gap); `test_structural_linter_gate` (T3.4
  upstream-blocked); `test_fdr::TestSelectionFamilySubstrate::*` (T2.d/
  e/f deferred DT7 boundary gate); `test_runtime_guards::*` (pre-T3.1
  materialize_position signature baseline).

### Planning-lock

`topology_doctor --planning-lock --changed-files
src/engine/evaluator.py src/engine/cycle_runtime.py
src/engine/lifecycle_events.py src/execution/exit_lifecycle.py
tests/test_decision_evidence_entry_emission.py --plan-evidence
docs/operations/task_2026-04-23_midstream_remediation/plan.md --json`
→ `{"ok": true, "issues": []}` (GREEN across 5 files including
exit_lifecycle.py scope addition).

### con-nyx deferred

Per operator directive this session: "con-nyx 就是 critic，下次再发给他
吧，全部接受". Team config at `~/.claude/teams/zeus-live-readiness-debate/
config.json` persists; con-nyx teammate `isActive: false`. Next session's
first-10-min resume should dispatch this slice's design + commit to
con-nyx for durable-context record.

### Category immunity (Fitz K<<N)

Surrogate scored 6/10 — partial. Category "entry path without
DecisionEvidence still constructible" remains (any future
`EdgeDecision(should_trade=True, ...)` that forgets `decision_evidence=`
silently emits ENTRY_ORDER_POSTED with no envelope). Full immunity
requires `EdgeDecision.for_accept(...)` factory or `__post_init__`
guard that raises when `should_trade=True` + `decision_evidence is
None`. **Out of T4.1b scope** (would require rewriting 30+ call sites)
— flagged for T4.1c or future immune-system slice.

### T4.2-Phase1 readiness

Round-trip read-side pattern demonstrated in
`TestRoundTripRehydration.test_read_side_pattern_simulates_json_extract`:
`json.loads(payload_json)["decision_evidence_envelope"]` returns the
envelope JSON string verbatim (not a re-serialized dict) — ready for
`DecisionEvidence.from_json(...)` directly. Exit-side T4.2-Phase1 will
use the SQL analog `json_extract(payload_json,
'$.decision_evidence_envelope')` with zero re-wrapping step. Legacy
positions emit `decision_evidence_reason` sentinel instead; exit-side
audit distinguishes and does not false-positive.

## T5.a — execution notes (2026-04-23)

Fix-plan scope said "refactor `place_buy_order` / `place_sell_order` —
real entrypoints at `:191+`". **Seventh plan-premise correction in
this packet**: grep-verified only `place_sell_order` exists at L191
(legacy exit wrapper); `place_buy_order` does NOT exist. Real entry
path is `create_execution_intent → execute_intent → _live_order` at
L135/339. T5.a targets `_live_order` as the final CLOB-contact seam.

### Implementation

1. **Import** `ExecutionPrice, ExecutionPriceContractError` in
   `src/execution/executor.py`.
2. **`_live_order` (L339)**: before calling `client.place_limit_order`,
   construct `ExecutionPrice(value=intent.limit_price, price_type="ask",
   fee_deducted=False, currency="probability_units")`. On ValueError
   / ExecutionPriceContractError, return `OrderResult(status="rejected",
   reason="malformed_limit_price: ...", order_role="entry")` before
   any CLOB contact.
3. **NEW `tests/test_executor_typed_boundary.py`** — 12 tests:
   - 6 reject: NaN, +inf, -inf, negative, 1.5 (>1.0), 2.0
   - 6 accept: 0.0, 0.01, 0.5, 0.75, 0.99, 1.0 (parametrized)

### Three critic findings integrated inline (one-slice closure)

Surrogate `code-reviewer@opus` COMMENT verdict with 1 MEDIUM + 3 LOW:

- **MEDIUM — latent `decision_edge` bug**: `executor.py:136` passed
  `decision_edge=edge.edge` to `ExecutionIntent(...)` and `:428` read
  `intent.decision_edge`, but the dataclass had no such field. Dormant
  because live entries are paused, but the T5.a accept-path tests
  were passing for the WRONG reason (constructor succeeded → hit
  `AttributeError` on `intent.decision_edge` → swallowed by broad
  except → rejection reason ≠ "malformed_limit_price" → assertion
  passed). Fixed inline: added `decision_edge: float = 0.0` to
  `ExecutionIntent` at `src/contracts/execution_intent.py:23`. Test
  fixture updated to pass explicit `decision_edge=0.05`. Tests now
  pass for the RIGHT reason.
- **LOW — semantic white lie**: original `price_type="fee_adjusted",
  fee_deducted=True` construction lied about fee state (executor
  doesn't know upstream fee-accounting). Fixed: `price_type="ask",
  fee_deducted=False` — same finite/nonneg/≤1 guards fire (those are
  `__post_init__` invariants independent of the semantic fields).
- **LOW — misleading reason**: rejection reason was
  `execution_price_contract_violation`, which could mislead readers
  into expecting a Kelly-semantic failure. Renamed to
  `malformed_limit_price` to accurately reflect the narrow structural
  guard.
- **LOW (deferred)** — exit path (`execute_exit_order:269`) has
  unguarded NaN propagation through `max/min` clamp. Out of T5.a
  entry-path scope; flagged for T5.b or equivalent follow-up slice.

### Regression evidence

- `pytest -q tests/test_executor_typed_boundary.py tests/test_execution_price.py`
  → `36 passed, 1 xfailed in 4.60s` (1 xfail pre-existing).
- Broader: `pytest -q tests/test_executor.py tests/test_executor_typed_boundary.py tests/test_execution_price.py tests/test_runtime_guards.py`
  → `15 failed, 148 passed, 1 skipped, 1 xfailed`. All 15 failures
  confirmed pre-existing via `git stash` + rerun (same 15 failures
  without T5.a changes). **Zero delta from T5.a.** Failures are in
  `test_runtime_guards.py` ensemble-snapshot / position-materialization
  territory, unrelated to executor boundary.
- Planning-lock GREEN for `src/execution/executor.py` +
  `src/contracts/execution_intent.py` + test file.

### Scope expansion acknowledged

T5.a initially scoped to "import + boundary assertion + new test
(~4h)". Surrogate critic found that the narrow interpretation would
have shipped tests passing for the wrong reason (decision_edge
AttributeError masquerade). The MEDIUM fix required extending scope
to the `ExecutionIntent` dataclass — a cross-module contract change
— but the fix is additive (one field, default=0.0, preserves every
existing caller). Cross-module risk bounded; the planning-lock check
re-ran with all three files and reported GREEN.

## T1.e — execution notes (2026-04-23)

Three files changed:
- NEW `scripts/test_currency_audit.py` — CI-time guard reading the
  panel list from `architecture/test_topology.yaml::categories.midstream_guardian_panel`,
  scanning each file's first 12 lines for the three canonical
  provenance markers (`# Created:`, `# Last reused/audited:`,
  `# Authority basis:`). Exits 0 if every file carries all 3
  markers; exits 1 with missing-marker list otherwise. Supports
  `--verbose` and `--json` flags.
- `architecture/test_topology.yaml` — new `categories.midstream_guardian_panel:`
  key (nested under existing `categories:` per surrogate-critic D3
  feedback; inherits topology_doctor existing-file validation for free).
- `architecture/script_manifest.yaml` — register the new script as
  `class: enforcement` with canonical command.

Planning-lock GREEN (architecture/** + scripts/**, both under
planning-lock per delivery.md §5).

### Surrogate critic findings (code-reviewer@opus) integrated

Verdict: COMMENT (non-blocking; core enforcement works). 4 findings:

- **D1 (MEDIUM, fixed)**: empty-panel silent-pass regression risk
  → added `len(panel) == 0` guard raising SystemExit.
- **D2 (LOW, fixed)**: malformed YAML threw raw ParserError
  → wrapped in try/except raising clean SystemExit.
- **D3 (LOW, fixed)**: original placement at top-level sibling to
  `categories:` broke topology_doctor convention (both
  `topology_doctor_test_checks.py:24` and
  `topology_doctor_registry_checks.py:126` iterate
  `topology.get("categories")`)
  → nested under `categories:` + script updated to read from
  `data.get("categories").get("midstream_guardian_panel")`.
- **D4 (LOW, deferred)**: regex passes semantic bogus values (e.g.
  `# Created: 0000-00-00`). Acceptable for T1.e scope (header-strip
  detection). Future T1.f/g could add recency bounds.

Critic's positive observations preserved: script eats its own
dogfood (dated provenance header on the audit itself), JSON shape is
clean, `_yaml_bootstrap` reuse + defensive `sys.path.insert` is more
CWD-robust than peer-script convention.

### Regression evidence

- `.venv/bin/python scripts/test_currency_audit.py` → `OK: all 15
  midstream guardian panel files carry dated provenance headers.`
  Exit 0.
- `.venv/bin/python scripts/test_currency_audit.py --json` →
  `{"panel_size": 15, "missing_count": 0, "missing": {}}`.
- Full pytest regression not required — script is pure addition, no
  existing test touched.

## T1.d — audit notes (2026-04-23)

Scope per plan: audit `tests/test_dual_track_law_stubs.py` Phase-N skip
stubs; "Remove activatable; document residuals".

### Grep results

One `pytest.skip` call in the entire file, at `L70` inside
`test_no_high_low_mix_in_platt_or_bins` (function at L68-70).
All other 11 test functions in the file are active:

| Line | Test | Status |
|---|---|---|
| L17 | `test_no_daily_low_on_legacy_table` (NC-11 / INV-14) | active |
| L68 | `test_no_high_low_mix_in_platt_or_bins` (NC-12 / INV-16) | **SKIP** (L70) |
| L74 | `test_json_export_after_db_commit` (NC-13 / INV-17) | active |
| L128 | `test_kelly_input_carries_distributional_info` (NC-14 / INV-21 / DT#5) | active (STRICT Phase 10E) |
| L189 | `test_fdr_family_key_is_canonical` (NC-15 / INV-22) | active (T7.a closed this slot) |
| L227 | `test_red_triggers_active_position_sweep` (INV-19 / DT#2) | active (ACTIVATED Phase 9B) |
| L304 | `test_red_force_exit_marker_drives_evaluate_exit_to_exit` (DT#2) | active |
| L376 | `test_red_force_exit_marker_does_not_override_day0_evaluation` (DT#2) | active |
| L431 | `test_day0_without_red_marker_runs_day0_logic_normally` (DT#2 Phase 9C) | active |
| L477 | `test_boundary_ambiguous_refuses_signal_contract` (DT#7) | active (NEW Phase 9B) |
| L518 | `test_chain_reconciliation_three_state_machine` (INV-18) | active |
| L578 | `test_load_portfolio_degrades_gracefully_on_authority_loss` (INV-20) | active |

### Classification of the L70 stub

**KEEP_LEGITIMATE.**

- INV-16 partial enforcement IS coded in production:
  `src/engine/evaluator.py:922-944` rejects LOW Day0 slots with
  `causality_status` outside `_LOW_ALLOWED_CAUSALITY` (raises
  `"INV-16"`-tagged error). `src/data/observation_client.py:35`
  documents `causality_status` as the INV-16 enforcement mechanism.
- But NC-12 as worded is **multi-surface**: "No mixing of high and
  low rows in Platt model, calibration pair set, bin lookup, or
  settlement identity". INV-16 only covers the Day0 causality aspect.
  Other surfaces (Platt refit input, calibration pair writer, bin
  lookup dispatcher, settlement rebuild identity) are partially coded
  at most.
- V2 substrate (ensemble_snapshots_v2 + calibration_pairs_v2 +
  platt_models_v2) is **empty** per the midstream trust verdict
  substrate audit. There is no mixed-row dataset to test against
  today.
- The skip message "pending: enforced in Phase 7 rebuild" aligns with
  the plan's W5-substrate-deferred classification for related
  slices — Phase 7 is the rebuild packet that populates v2 and
  exercises the multi-surface enforcement end-to-end.

Verdict per memory rule L21 language: neither "activate" nor "extend"
applies — the enforcement-target dataset does not exist today.
**Document and defer.**

### No code changes; slice closes with this work_log + receipt entry.

## T2.a/T2.b — execution notes (2026-04-23)

Both tests in `tests/test_calibration_bins_canonical.py` (plan cited
as T2.a and T2.b separately, but they share fixture assumptions and
fix together as a pair):

- `test_R14_quarantine_allows_replacement_tag` (L758)
- `test_R14_filter_allowed_partitions_rows` (L776)

### Direction: tests were stale, code is current

Authority source `src/contracts/ensemble_snapshot_provenance.py:26-28,
82-103, 118-147` declares:
- `tigge_mx2t6_local_peak_window_max_v1` is EXPLICITLY quarantined
  (exact match + prefix match)
- Canonical dual-track replacements are
  `tigge_mx2t6_local_calendar_day_max_v1` (HIGH_LOCALDAY_MAX) and
  `tigge_mn2t6_local_calendar_day_min_v1` (LOW_LOCALDAY_MIN), both
  in `CANONICAL_DATA_VERSIONS` frozenset at L68-71
- `assert_data_version_allowed` has two-stage check: (1) quarantine
  block at L131-140, (2) positive allowlist at L141-147

Previous fixture expected `peak_window_max_v1` to be "allowed
replacement" — directly contradicted by both quarantine entries. The
other fixture items (`"day_window_max_v1"`, openmeteo, empty, None)
all fail the stage-2 positive allowlist. Whole assertion path was
broken.

### Changes

- `test_R14_quarantine_allows_replacement_tag`: now iterates
  `sorted(CANONICAL_DATA_VERSIONS)` (the authoritative frozenset),
  asserts each passes both stages. Docstring documents authority
  source.
- `test_R14_filter_allowed_partitions_rows`: updated expected
  partitioning. Old: `allowed=[1, 4], quarantined=[2, 3]`. New:
  `allowed=[4], quarantined=[1, 2, 3]`. Docstring explains id 4
  (openmeteo) passes `is_quarantined` (no prefix/exact match) but
  would still fail `assert_data_version_allowed` stage-2; `filter_allowed`
  is reader-side, only checks quarantine.

### Regression evidence

- Narrow: `pytest -q tests/test_calibration_bins_canonical.py::test_R14_quarantine_allows_replacement_tag tests/test_calibration_bins_canonical.py::test_R14_filter_allowed_partitions_rows` → `2 passed in 0.77s`.
- File: `pytest -q tests/test_calibration_bins_canonical.py` → `40 passed in 1.44s`. Zero regression.

### Surrogate critic (code-reviewer@opus)

Verdict: CLEAR, APPROVE.
- DIRECTION_CORRECT: yes — independently grep-verified source
  quarantine + corroborated by `tests/test_phase4_parity_gate.py:26-66`
  and `tests/test_phase4_rebuild.py:266-281`.
- MISSED_SITES: none in tests/ or src/ that reference the old "allowed"
  assumption.
- COVERAGE_LOSS: acceptable / arguably improved (old test was exercising
  code paths that SHOULD fail stage-2 allowlist).
- CITATION_STALENESS: none (L87, L102, L26-28, L141 all fresh).

### Flagged follow-up (out-of-T2.a scope)

Critic flagged stale comment at
`scripts/rebuild_calibration_pairs_canonical.py:103-104`:
> "The future `tigge_mx2t6_local_peak_window_max_v1` data_version is
> intentionally NOT quarantined — it is the replacement target."

This contradicts current contract. Not a test blocker but a
documentation-antibody gap. Flagged as a MEDIUM follow-up slice.

## T7.a — verification notes (2026-04-23)

Fourth plan-premise correction in this packet. Plan said:
> "Activate `test_fdr_family_key_is_canonical` — remove `pytest.skip` at
> `tests/test_dual_track_law_stubs.py:67` + verify body matches
> `make_hypothesis_family_id` + `make_edge_family_id` signatures"

Reality (grep-verified 2026-04-23):
- Line 67 is a comment (`# NC-12 / INV-16`).
- Line 70 is `pytest.skip("pending: enforced in Phase 7 rebuild")` inside
  `test_no_high_low_mix_in_platt_or_bins` (a DIFFERENT test, NC-12 /
  INV-16 enforcement for Phase 7). That stub is T1.d territory.
- `test_fdr_family_key_is_canonical` is at L189 with no skip decorators
  and a full body covering:
  - hypothesis vs edge family ID scope separation
  - determinism within each scope
  - metric discrimination (HIGH ≠ LOW family IDs per S4 R9 P10B)
- Verified passing: `pytest -q tests/test_dual_track_law_stubs.py::test_fdr_family_key_is_canonical`
  → `1 passed in 0.03s`.

Slice scope is satisfied without code changes. Receipt documents the
verified-already-done state. Per memory rule L21, "activate" was the
wrong verb in the plan — the test was already active. This is being
recorded as CLOSED with NO-OP commit (this work_log + receipt update
only).

The residual `test_no_high_low_mix_in_platt_or_bins` skip stub is
flagged for T1.d closure.

## T3.2 — execution notes (2026-04-23)

Scope: add `"temperature_metric": "high"` to `_canonical_projection()`
helper at `tests/test_architecture_contracts.py:125-157`. One line,
resolves ≥5 failing tests via single category patch (Fitz C1).

Pre-T3.2 state (test_architecture_contracts.py, post-T3.3): ~9 failures
with `ValueError: projection missing fields: ['temperature_metric']`.

Post-T3.2: **1 failed / 70 passed / 22 skipped**. Remaining failure is
`test_cycle_runtime_entry_dual_write_helper_skips_when_canonical_schema_absent`
→ `AttributeError: '_Logger' object has no attribute 'warning'` at
`src/engine/cycle_runtime.py:291`. Unrelated to fixture drift.

Surrogate critic (code-reviewer@opus): CLEAR. Confirmed `_canonical_event()`
correctly NOT patched — `CANONICAL_POSITION_EVENT_COLUMNS` does not
include `temperature_metric`. Fitz C1 compliance on "one fixture × five
callers = one patch" verified.

## T3.2b — execution notes (2026-04-23)

**Plan-premise correction (third in this packet):** fix-plan v2 T3.2b
scope was "AST-walk `src/state/projection.py` builders". Grep-verified
`src/state/projection.py` has ZERO dict-returning functions — all
functions return `None`, `tuple`, `set`, or raise. The AST-walk target
is vacuous.

Pivoted to structural-alignment antibodies that catch the drift
category responsible for T3.2 failures:
- Test 1: `temperature_metric in CANONICAL_POSITION_CURRENT_COLUMNS`
  (Python-tuple drift)
- Test 2: kernel SQL CREATE TABLE declares `temperature_metric` (SQL
  migration drift)
- Test 3: every `CANONICAL_POSITION_CURRENT_COLUMNS` entry appears in
  kernel SQL CREATE TABLE (constant ↔ schema alignment) — the real
  category-immunity antibody per Fitz C1

New file `tests/test_canonical_position_current_schema_alignment.py`
passes 3/3. Carries dated provenance header per CLAUDE.md rule.

Surrogate critic adopted: regex hardening from `\n\)\s*;` to `\)\s*;`
(drops `\n` anchor for robustness against future migration reformatting).

Critic flagged 3 low-severity future-slice antibody targets (F1-F3):
- `CANONICAL_POSITION_EVENT_COLUMNS` three-way drift (test ↔ ledger ↔ kernel SQL)
- `LifecyclePhase` Python enum ↔ `phase` CHECK constraint
- `event_type` scatter without central Python enum

All noted for potential future slices — not blocking T3.2b.

## T3.3 — execution notes (2026-04-23)

Fix-plan premise correction (third such correction in this packet, after
T4.0 and implicit T3.2 overlap):

**Plan said**: "Canonical `position_current` schema bootstrap fix. Diff
`apply_architecture_kernel_schema()` against
`CANONICAL_POSITION_CURRENT_COLUMNS`; add missing columns to bootstrap
SQL."

**Reality (factually verified)**: the kernel migration at
`architecture/2026_04_02_architecture_kernel.sql:82-129` CREATE TABLE
already declares all 31 canonical columns. A fresh DB probe via
`sqlite3 :memory: + apply_architecture_kernel_schema(conn)` yields
exactly 31 columns, zero missing, zero extra. **Bootstrap is already
correct.**

The actual defect is in the LEGACY-DB migration path (not fresh
bootstrap): when `apply_architecture_kernel_schema` runs on an
existing-but-pre-kernel `position_current` table, the
`CREATE TABLE IF NOT EXISTS` no-ops, and the explicit ALTER TABLE
loop at L147-149 only adds 3 token columns. Missing canonical columns
like `temperature_metric` remain absent, causing
`assert_canonical_transaction_schema` at L150 to raise.

### Fix

Expanded the ALTER TABLE loop to iterate over all
`CANONICAL_POSITION_CURRENT_COLUMNS` (not just the 3-tuple), adding
each missing column with plain TEXT affinity. Cached the column set
once before the loop (idempotency correctness confirmed by critic).
Multi-line comment explains the design trade-off (TEXT affinity +
runtime `require_payload_fields` guard).

### Critic review (surrogate code-reviewer@opus)

**Verdict: CLEAR — APPROVE for commit.**

Findings summary:
- F1 (low, non-blocking): TEXT affinity for REAL-typed legacy columns —
  documented trade-off; SQLite dynamic typing preserves values.
- F2 (low, non-blocking): INV-14 CHECK constraint lost for
  ALTER-migrated `temperature_metric`, but runtime guard at
  `src/state/projection.py:50-53` catches invalid writes BEFORE the
  DB; critic greped `nuke_rebuild_projections.py:124` as the only
  direct-SQL mutator and confirmed it touches only `phase`+`updated_at`.
- F3, F4: positive observations on idempotency and inline documentation.

All seven rubric axes: CORRECTNESS pass, TYPE_SEMANTICS acceptable,
IDEMPOTENCY correct, ORDERING safe, INV_14_CONSEQUENCE preserved,
PLANNING_LOCK verified green (path correction noted), TEST_EXPECTATION
fixed target green, no new regression.

### Regression evidence

- `pytest -q tests/test_architecture_contracts.py::test_kernel_schema_adds_token_identity_columns_to_existing_position_current`
  → passes (was failing pre-T3.3).
- `pytest -q tests/test_architecture_contracts.py::test_kernel_schema_migrates_existing_token_suppression_reason_check`
  → passes (not regressed).
- `pytest -q tests/test_architecture_contracts.py::test_apply_architecture_kernel_schema_bootstraps_fresh_db`
  → still fails, but the failure is on `_canonical_projection()` missing
  `temperature_metric` field in the fixture — T3.2 scope, not T3.3.
  My T3.3 change is orthogonal.

## T3.1 — execution notes (2026-04-23)

Scope per plan: "patch ALL 7 execute_discovery_phase callers in
tests/ + audit materialize_position callers". L20 grep-gate resolved
the true call-site inventory to **5 patches**, not 7:

- `tests/test_day0_runtime_observation_context.py:41` (MISSING env)
- `tests/test_day0_runtime_observation_context.py:84` (MISSING env)
- `tests/test_runtime_guards.py:717` already has `env="paper"` — NO PATCH
- `tests/test_runtime_guards.py:4877` (MISSING env) — DAY0_CAPTURE mode
- `tests/test_runtime_guards.py:5027` (MISSING env) — OPENING_HUNT mode
- `tests/test_architecture_contracts.py:3555` (MISSING env) — UPDATE_REACTION
- `tests/test_phase10e_closeout.py:352` resolved to a FUNCTION NAME,
  not a call — the grep hit was on `def test_r_df_5_*`, not on an
  actual `execute_discovery_phase(...)` invocation. No patch needed.
- `tests/test_runtime_guards.py:2010, 2057` (materialize_position)
  already carry `state="entered", env="paper"` — NO PATCH.

Applied uniform `env="paper"` to all 5 missing sites. Paper is the
safe test-context value per the pattern already established at
`test_runtime_guards.py:717`. Real production uses `env="live"` for
live runs.

Regression evidence (delta-direction per memory L28):
- Targeted tests:
  - `pytest -q tests/test_day0_runtime_observation_context.py`
    → `4 passed` (was 2 failed, 2 passed)
  - 4 T3.1-target tests (entry_path × 2, discovery_phase_records × 2)
    → `4 passed`
- Broader 3-file regression:
  - Pre-T3.1 baseline: 28 failed / 166 passed / 22 skipped
    (normalized to same 3 files as post-run by subtracting
    test_phase10e_closeout.py's 13 passing tests from the 4-file
    pre-run)
  - Post-T3.1: **22 failed / 172 passed / 22 skipped**
  - Delta: **-6 failures, +6 passes, 0 new failures**
- Remaining 22 failures on these files are out-of-T3.1-scope:
  T3.3 schema bootstrap (apply_architecture_kernel_schema,
  kernel_schema_adds_token_identity_columns, cycle_runtime_entry_dual_write_helper),
  T2.g fail-closed (test_fdr already handled), INV-08 atomicity,
  unrelated INV-14 projection drift → owned by T3.2, T3.2b, T3.3, T2.g.

## T1.b — execution notes (2026-04-23)

Scope per plan: "content audit of `config/provenance_registry.yaml`
(already exists at 516 lines) + remove redundant `skipif` markers in
`test_provenance_enforcement.py`."

Findings:
- `config/provenance_registry.yaml` exists and is populated (≥ 516
  lines, `kelly_mult` at L26, `market_fusion.TAIL_ALPHA_SCALE` present,
  required fields `declared_target, data_basis, validated_at,
  replacement_criteria` all present per registry shape).
- All 4 tests in `TestAllStrategyConstantsRegistered` carry TWO
  skipif markers:
  1. `skipif(not REGISTRY_YAML.exists(), reason="Registry YAML not yet created")` — **inaccurate today**: file exists. Removed by T1.b.
  2. `skipif(not HAS_YAML, reason="PyYAML not installed")` — **legitimate environmental check**: PyYAML may be absent in minimal Python envs; kept.
- L34 inside `_load_registry_yaml()` helper also references
  `REGISTRY_YAML.exists()` — kept (legitimate fallback, returns empty
  dict when file absent).

Per memory rule L21, this is **REMOVING STALE SKIP MARKERS**, not
"activating" tests — all 4 guarded tests already ran in the pre-T1.b
baseline because both skip conditions evaluated False. The slice
hardens the test against future regressions (if the registry file is
deleted, tests will now fail loudly instead of silent-skipping).

Regression evidence: `pytest -q tests/test_provenance_enforcement.py`
→ `19 passed in 0.15s` (matches pre-T1.b baseline exactly).

## T1.a — execution notes (2026-04-23)

Prepended a 3-line dated provenance header to each file in the
15-file midstream guardian panel. Created dates retrieved via
`git log --follow --reverse --format=%cs -- <file> | head -1`.
Authority basis uniform across the panel: "midstream verdict v2
2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a
midstream guardian panel)". Per-test specific INV references remain
inside each file's own module docstring (unchanged).

Regression verdict (L28 delta-direction, not absolute counts):
- **narrow scope (15 panel files)**: `.venv/bin/python -m pytest -q
  <15 files>` → `19 failed, 344 passed, 34 skipped, 1 xfailed` —
  matches the pre-T1.a baseline documented in the midstream trust
  verdict (K2 findings). **Zero delta.** Confirms comment-only change
  had no test-behavior impact.
- **full-suite probe**: `.venv/bin/python -m pytest -q tests/` →
  `144 failed, 2079 passed, 90 skipped, 1 xfailed`. Out-of-scope for
  T1.a closure — these failures exist independently of the 15-file
  panel and are attributable to pre-existing repo state and/or the
  concurrent upstream data-readiness agent. Noted for context; not a
  T1.a regression.

Files changed: 15 test files (3-line header prepend each), plus this
work_log.md + receipt.json. `tests/AGENTS.md` is also modified in the
tree but is **not mine** (co-tenant's work); NOT staged for this
commit.

## T4.0 — PROPOSAL notes (2026-04-23, rev2)

### Premise correction
Fix-plan v2 T4.0 row states "`decision_log` row keyed on
`decision_snapshot_id` (option b; no schema migration)". Grep-verified
at `src/state/db.py:528-536` shows `decision_log` has 6 columns
(`id, mode, started_at, completed_at, artifact_json, timestamp`) with
**no `decision_snapshot_id`**. Premise is false.

### Revision history
- **rev1 (earlier today)**: Picked Option B (new column
  `decision_evidence_json TEXT` on `trade_decisions`). Reviewed by
  surrogate critic (code-reviewer@opus).
- **rev2 (this entry)**: Integrates surrogate critic findings F1-F3
  + missed Option E. Recommendation flipped to Option E.

### Surrogate critic findings integrated
- **F1 HIGH**: rev1 cited `evaluator.py:724, 1307` as Decision handoff
  sites. Actual: FDR-filter logic. `EdgeDecision` (not `Decision`)
  is constructed at L753, 778, 803, 815, 832, 842, 866, 882, 901, 912.
- **F2 MED**: rev1 cited `db.py:2320, 2468` as INSERT sites. Actual:
  VALUES-tuple entries; INSERTs at L2325, L2473.
- **F3 HIGH**: rev1 asserted atomicity between `trade_decisions`
  INSERT and `position_events` append. Grep-verified WRONG via
  `cycle_runtime.py:1115-1140`'s explicit SAVEPOINT-then-dual-write
  comment. `trade_decisions` INSERT lives inside
  `SAVEPOINT sp_candidate_*`; `position_events` append
  (`append_many_and_project`) runs after SAVEPOINT release with its
  own `with conn:` sub-transaction. **Separate transaction boundaries;
  torn-state window exists today for `epistemic_context_json` and
  `edge_context_json`.** Option B inherits this window.
- **Missed Option E**: Piggyback on existing
  `ENTRY_ORDER_POSTED.payload_json` — `payload_json` NOT NULL TEXT
  column at `position_events` col 17 already exists (PRAGMA-verified).
  Atomic with canonical append. No schema change. No INV-07 expansion.

### Recommendation flipped: Option E

Option E is structurally superior on three axes:
1. Atomicity: evidence lands in the same canonical
   `append_many_and_project` transaction.
2. Zero schema migration: `payload_json` already exists.
3. Category immunity per Fitz C1: future decision-contract evidence
   extends the same payload-sidecar pattern.

### Planning-lock classification
REQUIRED for T4.1 per delivery.md §5. "Additive/non-breaking" is
not an exemption. Option E keeps T4.1 out of `src/state/db.py` schema
but still touches `src/engine/evaluator.py` + `src/state/lifecycle_events.py`
(or equivalent emission helper) — both under planning-lock trigger
zones.

### contract_version tag
ADOPT. `DecisionEvidence.to_json` emits
`{"contract_version": 1, "fields": {...}}`.
`from_json` raises `UnknownContractVersionError` on unknown versions.

### Open questions for con-nyx (durable critic)
1. Idempotency when `ENTRY_ORDER_POSTED` is retried: key on
   `decision_id`?
2. Helper location: `decision_chain.py` vs new
   `decision_evidence_persistence.py`?
3. Does `query_position_events` accept `runtime_trade_id` or
   `position_id`?

## Schema migration runbook — pending live-DB catch-up (2026-04-23)

Three slices landed `init_schema()`-requiring changes that are in code
but NOT YET APPLIED to running `state/zeus-world.db`. Per critic-opus
META finding 2026-04-23: accumulation of live-DB migration debt should
be discharged atomically, not carried slice-by-slice.

### Pending migrations

| Commit | Slice | What the live DB is missing until applied |
|---|---|---|
| `36f0189` | T3.3 position_current canonical-column ALTER | Legacy rows lack the full 31-column canonical tuple (notably `temperature_metric`); `src/state/ledger.py::apply_architecture_kernel_schema` ALTER loop is idempotent |
| `69520ba` | S2.1 settlements_authority_monotonic trigger v2 | Live DB still carries pre-S2.1 v1 trigger (`IS NULL` only); 5 presence-only bypass shapes (false / 0 / empty-string / object / array) remain exploitable |
| `619b278` | REOPEN-1 forecasts schema ALTER | Live forecasts has 13 cols; writer expects 14 (`rebuild_run_id` + `data_source_version` missing). `k2_forecasts_daily` cron FAILED every 30 min since 2026-04-23T13:30Z |
| `<pending>` | S2.2 settlements_verified_insert_integrity + settlements_verified_update_integrity triggers | Live DB has no structural AP-2 prevention; a writer that bypasses `SettlementSemantics.assert_settlement_value()` can INSERT a VERIFIED row with NULL settlement_value OR empty winning_bin. Probe confirms 0 existing VERIFIED rows would be rejected (1,469 all pass). |

All three migrations are idempotent — calling `init_schema(conn)` on
the live world connection applies them safely. The three shipped
antibody tests cover idempotency.

### Canonical migration command — either option discharges ALL three

**Option A (preferred)** — daemon restart. `src/main.py:496` already
calls `init_schema(conn)` unconditionally on startup. Restart the
supervisor and all pending migrations apply on the next boot with zero
extra steps.

**Option B** — one-off `init_schema` call without daemon restart:

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
.venv/bin/python -c "
from src.state.db import init_schema, get_world_connection
conn = get_world_connection()
try:
    init_schema(conn)
    conn.commit()
finally:
    conn.close()
print('init_schema applied')
"
```

Daemon connections are re-opened per cycle (per `get_world_connection`
at `src/main.py:177`), so live cycles pick up the new schema on the
next tick without restart.

### Post-apply verification

```bash
# forecasts: confirm 2 new cols exist
sqlite3 -readonly state/zeus-world.db "PRAGMA table_info(forecasts);" \
  | grep -E "rebuild_run_id|data_source_version"

# trigger: confirm v2 (contains json_type check)
sqlite3 -readonly state/zeus-world.db \
  "SELECT sql FROM sqlite_master WHERE name='settlements_authority_monotonic';" \
  | grep -c "json_type"
# expect 1

# position_current: confirm all 31 canonical cols present
sqlite3 -readonly state/zeus-world.db \
  "SELECT COUNT(*) FROM pragma_table_info('position_current');"
# expect 31

# scheduler: wait for next 07:30 UTC k2_forecasts_daily tick, then
cat state/scheduler_jobs_health.json | python3 -c \
  "import sys, json; print(json.load(sys.stdin).get('k2_forecasts_daily', {}).get('status'))"
# expect OK (flips from FAILED)
```

### NOT covered by this runbook (separate review gates)

- **DR-33-C flag flip** (`ZEUS_HARVESTER_LIVE_ENABLED=1`): operator-
  authored review required per data-readiness closure.
- **REOPEN-2 settlements UNIQUE migration** (if operator pursues):
  HIGH-risk — SQLite cannot ALTER unique constraints; requires table
  recreation with pre-flight snapshot.
- **S2.2 BEFORE INSERT trigger on settlements** (if operator pursues):
  HIGH-risk — may reject legitimate historical writes; requires
  read-only dry-run probe first.

### Runbook maintenance

When a future slice lands an `init_schema`-requiring change, append a
row to the pending-migrations table above with commit hash + one-line
description. Remove the row after the operator confirms the migration
applied.

## DR-33-B packet stub — `append_many_and_project` atomicity refactor (2026-04-23)

**Status**: DEFERRED — scoped to its own future packet, not executed in
this session. This stub captures scope + pre-work + rollback so the next
session can open the packet cleanly without re-discovering context.

**Authority basis**: data-readiness workstream closure-banner item 1
(`DR-33-B: atomicity refactor for append_many_and_project callers (P-H
equivalent)`) + memory rule L30 (`with conn:` inside SAVEPOINT atomicity
collision — Python sqlite3 `with conn:` commits + releases SAVEPOINTs;
nested usage is a latent atomicity loss).

### Scope

Wrap every `append_many_and_project(...)` caller in an explicit
SAVEPOINT so torn-state windows between `trade_decisions` INSERT (inside
a SAVEPOINT sp_candidate_*) and `position_events` append (inside its
own `with conn:`) are closed. Current state documented in T4.0 design
doc rev2 critic finding F3 (HIGH).

### Pre-work required before opening the packet

1. **Caller inventory**: `grep -rn 'append_many_and_project' src/ scripts/ tests/`.
   Expected count is between 6 and 14 sites per memory rule L28 ("caller
   counts are routinely off by 2-3 due to topology flake"). Grep-verify
   each site within 10 minutes of edit (memory rule L20).
   **Action**: the next-session agent MUST run this grep first and pin
   the exact caller count + per-caller file:line anchors INTO the new
   packet's plan.md BEFORE writing any refactor code. Do NOT trust a
   prior caller count found in this stub or in memory — re-derive
   fresh.
2. **`with conn:` audit** (for each caller site from step 1, classify
   transaction boundary):
   - **`with conn:` block caller** — wrapping with SAVEPOINT introduces
     the L30 collision; caller MUST be refactored to use explicit
     `conn.execute("SAVEPOINT ...")` / `RELEASE` instead.
   - **Inside a SAVEPOINT caller** — nested SAVEPOINT is fine, but
     document the nesting depth so rollback semantics are auditable.
   - **Bare / no-transaction caller** — safe to wrap directly.
   Record the classification per caller in the packet's work_log
   before any refactor lands. Per-caller classification is the
   load-bearing gate that distinguishes DR-33-B from a naive
   "wrap-them-all" pass.
3. **Transaction boundary diagram**: sketch out the full cycle_runtime
   canonical-entry path and verify the SAVEPOINT nests cleanly (or doesn't
   nest at all) relative to any outer transaction.

### Rollback / safety

- Pre-slice DB snapshot: `state/zeus-world.db.pre-dr33b_<date>` + SHA-256
  sidecar via `scripts/snapshot_checksum.py --compute` (slice S2.3
  landed the tool).
- Rollback-chain index entry in the new packet's work_log.
- Two-stage critic: (a) critic-opus via `SendMessage` (durable team) +
  (b) surrogate `Agent(subagent_type=code-reviewer, model=opus)` for
  independent pre-commit review (per REOPEN-1 + S2.1 + S2.4 pattern
  that proved effective this session).

### HIGH-risk markers

- Caller grep inventory is the FIRST pass/fail gate. If inventory
  returns different count than prior memory rule L30 estimate (~6-14),
  stop and re-read L30 before proceeding.
- SAVEPOINT nesting wrong-direction = silent data loss on rollback.
  This is NOT a dev-quality bug; it is a correctness invariant.
- Torn-state window CLOSE depends on precise boundary placement. A
  SAVEPOINT that wraps only the INSERT but not the projection, or
  vice versa, leaves the window open.

### Out of scope

- DR-33-C flag flip (separate operator-approval gate)
- REOPEN-2 settlements UNIQUE migration (separate high-risk schema
  change; see critic-opus P0.2 finding C4 for rationale)
- S2.2 BEFORE INSERT trigger on settlements (separate high-risk
  trigger install)

### Recommended next-session packet path

```
docs/operations/task_2026-04-24_dr33b_atomicity_refactor/
├── plan.md (scope + allowed/forbidden files + critic protocol)
├── work_log.md (per-caller slice rows)
└── receipt.json (slices_closed + caller inventory grep evidence)
```

Plan-evidence anchor for planning-lock should point at the new packet's
`plan.md`, not this stub. Stub is an index / memory aid, not the
execution plan.

## REOPEN-2 packet stub — settlements UNIQUE migration for dual-track (2026-04-23)

**Status**: DEFERRED — HIGH-risk schema-rebuild migration scoped to its
own future packet, NOT executed in this session. This stub captures
the approach + pre-apply safety gates + rollback protocol.

**Authority basis**: critic-opus P0.2 forensic triage CONFIRMED
findings C3 + C4 (capital-risk):
- C3: `settlements` is high-metric-only (all 1,561 rows have
  `temperature_metric='high'`); no LOW rows exist because...
- C4: schema `UNIQUE(city, target_date)` (verbatim) structurally blocks
  inserting a LOW row for any (city, target_date) where a HIGH row
  already exists.

This is a **pre-flip BLOCKER for DR-33-C** (harvester live-write flag
flip). Without it, the first low-metric market settlement attempt will
UNIQUE-collide with the same-day high row, silently drop the settlement,
and break the learning chain for the low track.

### Target-schema migration

```sql
-- New settlements unique constraint (replace UNIQUE(city, target_date)):
UNIQUE(city, target_date, temperature_metric)
```

### Migration approach (SQLite cannot ALTER UNIQUE constraints; table rebuild required)

```sql
BEGIN;
  -- 1. Create new table with target schema
  CREATE TABLE settlements_migrated (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      city TEXT NOT NULL,
      target_date TEXT NOT NULL,
      market_slug TEXT,
      winning_bin TEXT,
      settlement_value REAL,
      settlement_source TEXT,
      settled_at TEXT,
      authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
      pm_bin_lo REAL,
      pm_bin_hi REAL,
      unit TEXT,
      settlement_source_type TEXT,
      temperature_metric TEXT,
      physical_quantity TEXT,
      observation_field TEXT,
      data_version TEXT,
      provenance_json TEXT,
      UNIQUE(city, target_date, temperature_metric)  -- NEW
  );

  -- 2. Copy all rows (preserves id auto-increment)
  INSERT INTO settlements_migrated SELECT * FROM settlements;

  -- 3. Drop old table (destroys old indexes + triggers on settlements)
  DROP TABLE settlements;

  -- 4. Rename new table
  ALTER TABLE settlements_migrated RENAME TO settlements;
COMMIT;
```

After the transaction, init_schema's trigger re-creation blocks
(S2.1 authority-monotonic + S2.2 verified-row-integrity + index
`idx_settlements_city_date`) re-install naturally on the new table.
No trigger re-definition code is needed in the migration itself.

### Pre-apply safety gates (ALL must pass before migration runs)

1. **Row-count pin**: `SELECT COUNT(*) FROM settlements == 1561` before.
   After migration, must still equal 1561. Any delta = rollback.
2. **Column equality**: for every column in the copy set, assert
   `COUNT(*) WHERE old.col IS NULL` equals
   `COUNT(*) WHERE new.col IS NULL`. No value drift.
3. **Trigger reinstall gate**: after DROP + RENAME, confirm
   `sqlite_master` lists `settlements_authority_monotonic` +
   `settlements_verified_insert_integrity` +
   `settlements_verified_update_integrity` — or the migration is
   incomplete.
4. **SHA-256 pre-snapshot**: `scripts/snapshot_checksum.py --compute
   state/zeus-world.db.pre-reopen2_YYYY-MM-DD` before transaction;
   sidecar committed for rollback verification.
5. **Dry-run on scratch DB**: migration MUST run cleanly against a
   copy of the live DB in /tmp before touching production.

### Idempotency

Check whether `PRAGMA index_list(settlements)` shows the new UNIQUE
on 3 columns before running. If yes, skip — migration already applied.

### Out of scope

- DR-33-C flag flip (separate operator review)
- DR-33-B atomicity refactor (separate packet per S6.1 stub)
- Any schema changes on the other 14 columns

### Recommended next-session packet path

```
docs/operations/task_2026-04-24_reopen2_settlements_unique_migration/
├── plan.md (scope + allowed_files + pre-apply gates + critic protocol)
├── work_log.md (slice rows + safety-gate evidence)
├── receipt.json (pre/post row counts + column-equality probes + sha256 sidecars)
└── evidence/
    ├── pre_row_count.txt
    ├── post_row_count.txt
    ├── pre_schema.txt
    ├── post_schema.txt
    └── scratch_db_dry_run.log
```

Plan-evidence anchor for planning-lock should point at that new
packet's `plan.md`, not this stub.

### Why defer (explicitly, for the next session)

- Table rebuild on a 1.8GB production DB is a distinct class of risk
  from ALTER TABLE ADD COLUMN (which is what T3.3 / REOPEN-1 / S2.1 /
  S2.2 require). Reversibility differs: ADD COLUMN is trivially
  reversible via DROP COLUMN; full table rebuild has a larger blast
  radius.
- Migration correctness depends on the trigger reinstall sequence in
  init_schema. Any bug there leaves the live DB without the S2.1/S2.2
  protections — silent corruption surface.
- Operator should review the sql/ subfolder of the forensic audit
  package at
  `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/`
  for any additional constraints I may have missed before dispatching
  the packet.
| T2.c + T2.g T2 family closure | closed | pending | 2 file edits (tests/test_market_analysis.py T2.c + tests/test_fdr.py T2.g) + T2_receipt.json new. **T2.c**: xfail(strict=True) sparse_monitor test pinning desired sibling-price-imputation behavior; clears when T6.3 VigTreatment.from_raw (size 10h) lands. **T2.g**: new test `test_evaluate_candidate_exercises_real_day0_router_on_fixture_db` with xfail(strict=False); runs evaluate_candidate WITHOUT Day0Router.route monkeypatch so real Day0HighSignal constructs/computes p_vector; currently xfail because Day0TemporalContext fixture stub lacks solar_day etc.; future fixture-builder slice removes the marker. Delta (L28 stash): pre 2F/40P → post 1F/40P/2xfailed (-1 failure = T2.c xfailed; `zero_size_fallback` pre-existing unrelated failure unchanged); planning-lock GREEN. **T2 family closed**: 5 full (T2.a/b/d/e/f) + 2 xfail antibody (T2.c/g) = 7/7; 2 clearance triggers documented (T6.3 lands → T2.c; Day0TemporalContext fixture builder → T2.g) | 2026-04-24 |
| T6.3 VigTreatment sparse-impute with typed provenance (Option C) — closes T2.c xfail + previously-RED sparse-stability test | closed | pending | 5 files (2 src + 2 test edits + 1 new test file) + 1 archive pointer. **Architectural decision (Option C)**: `VigTreatment.from_raw` gains `sibling_snapshot` + `imputation_source` kwargs; sparse-branch impute is now typed-visible on the VigTreatment record (`imputation_source ∈ {"none","sibling_market","p_cal_fallback"}`; `imputed_bins` tracks which positions were filled). **Plan-premise corrections** accumulated this slice: (#15) VigTreatment + from_raw already existed — T6.3 was not greenfield; (#19 BLOCKER-resolved) 3-way collision between B086 archive (no-impute), T2.c xfail body (p_cal impute literal), and plan L109 (sibling_snapshot semantics) surfaced by con-nyx pre-edit adversarial review; (#20) `test_sparse_vs_complete_held_bin_stability` was SILENTLY RED on master at HEAD 3eaa772 since B086 removed impute — delta ratio 0.611 vs 0.15 tolerance; T1 currency audit missed this because T1.a/e checked only dated headers, not pytest green-ness. **Implementation (Option C)**: (C1+C2) `src/contracts/vig_treatment.py` adds `imputed_bins: tuple = field(default_factory=tuple)` + `imputation_source: str = "none"` (trailing defaults, frozen-dataclass safe), extends `from_raw(raw_market_prices, *, sibling_snapshot=None, imputation_source="none")` with zero-detection + impute; __post_init__ conditionally relaxes sum-to-1 for imputed vectors (mixed observed-price + model-prior vectors don't have vig semantics); (C3) `src/strategy/market_fusion.py:186-190` sparse branch replaces `market = p_market.copy()` with `VigTreatment.from_raw(p_market, sibling_snapshot=p_cal, imputation_source="p_cal_fallback").clean_prices` — Bug #7 [Remediated B086] comment replaced with T6.3 supersedence note; (C5) `tests/test_market_analysis.py` T2.c fixture asymmetrized (p_cal=[0.20,0.50,0.30] vs [0.30,0.40,0.30]) to kill silent-sibling-equivalence ambiguity (con-nyx finding a), `pytest.mark.xfail(strict=True)` removed, added asymmetry assertion + discriminator against no-impute path; (C6+C7) `tests/test_k3_slice_p.py` rewrites `test_sparse_vector_does_not_zero_dilute` into `test_sparse_vector_matches_p_cal_fallback_impute` with impute-vs-no-impute discriminator (replaces con-nyx finding e vacuous tautology), `test_sparse_vs_complete_held_bin_stability` now GREEN (was RED finding f — 15% tolerance preserved, no recalibration needed); (C8) bootstrap CI delta audit: `TestBootstrapAllBins` 2/2 + `TestBootstrapWithPlattCalibrator` 2/2 + `TestB082HasPlattSingleParamSet` 3/3 all PASS post-flip — fixtures use complete markets so sparse path not hit in bootstrap tests (acceptable — sparse path is exercised by new discriminator + T2.c); (C9) `docs/archives/local_scratch/2026-04-19/zeus_data_improve_bug_audit_100_resolved.md:17` B086 entry gets T6.3 supersedence pointer explaining impute restored under typed contract with provenance; (C10) stale B086 comment at market_fusion.py:187-190 replaced inline with T6.3 policy note. **NEW** `tests/test_vig_treatment_provenance.py` (14 contract tests across 3 classes): FromRawComplete backward-compat (2), FromRawSparse provenance (7 incl. undeclared-source rejection, invalid-label rejection, shape mismatch, finite/non-negative sibling), PostInitInvariants (5 incl. provenance consistency, sparse-sum-permitted, complete-sum-enforced, applied_before_blend guard). **Regression delta** on touched scope: pre (HEAD 3eaa772 / a7fc74c) 9P/1F/1xfail → post 25P/0F/0xfail = +16P (14 new contract tests + 1 previously-RED flipped green + 1 T2.c XPASS→remove-marker). 2 pre-existing failures (`TestVWMP::test_zero_size_fallback` + `test_cycle_runtime_entry_dual_write_helper_skips_when_canonical_schema_absent`) persist (pre-T6.3 baseline; unrelated: vwmp ValueError per post-B084 law, _Logger attribute-gap per test-harness). Planning-lock GREEN (`--plan-evidence docs/operations/task_2026-04-23_midstream_remediation/plan.md` passes on K0_frozen_kernel + K3_extension cross-zone). **T2.c clearance trigger fired**: xfail(strict=True) marker removed; T2_receipt.json T2.c slice status updated from "xfail antibody pending T6.3" to "closed full via T6.3 Option C". **Category immunity**: silent revival of pre-B086 policy is now structurally impossible — the impute path MUST name its source through the typed record, making audit-blind regression surface-visible. | 2026-04-24 |
