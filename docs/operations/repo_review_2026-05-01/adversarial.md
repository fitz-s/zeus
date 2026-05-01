# Adversarial repo review of Zeus
HEAD: 21cff1ec9537aeed802ab8c25bd9f578f4a97aee
Branch: ultrareview25-remediation-2026-05-01
Reviewer: critic-opus (read-only adversarial subagent)
Date: 2026-05-01
Boot: AGENTS.md + architecture/AGENTS.md + invariants.yaml + docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md + known_gaps.md

## Subject
Adversarial review of live-trading-critical surfaces under 10 attack patterns (drift gap, test bypass, doc-only invariant, mode bleed, provenance evaporation, atomicity, time semantics, settlement edge cases, risk coercion, lifecycle phase invention).

## Verdict
APPROVED-WITH-CAVEATS — no P0-money-path blockers found. **Two P1 invariant-citation drifts** (INV-05 cite resolves to nothing at HEAD; INV-22 cite under-tests its own statement) plus several P2 doc/code-spec mismatches. Hooks/schema/sweep claims from PR1 / P1 / P2a remediation packets resolve correctly at HEAD.

---

## ATTACK 1 — Drift gap [VERDICT: FAIL]

### Finding A1.1 — INV-05 cited test does not exist (P1, K1_governance / risk surface)

`architecture/invariants.yaml:55-56` cites INV-05 enforcement as
`tests/test_architecture_contracts.py::test_risk_actions_exist_in_schema`.

- Grep at HEAD: `grep -rn "test_risk_actions_exist_in_schema" tests/` → 0 hits.
- pytest -k confirmation: `.venv/bin/python -m pytest tests/test_architecture_contracts.py::test_risk_actions_exist_in_schema` → `ERROR: not found ... no tests ran`.
- Closest existing test: `tests/test_architecture_contracts.py:81 test_strategy_policy_tables_exist_in_schema` (asserts `CREATE TABLE IF NOT EXISTS risk_actions` — i.e. the schema fragment INV-05 may have meant to gate). It is renamed/rolled-up; YAML never followed.

Drifting claim: **invariants.yaml** lists INV-05 as `enforced_by.tests:` with the named ID. PLAN.md does not call this out. The cycle 2026-04-28 BATCH D citation repair sweep that updated INV-02/INV-09/INV-14/INV-15/INV-16/INV-17 missed INV-05.

Reproduction:
```
git rev-parse HEAD                                  # 21cff1ec
grep -rn "test_risk_actions_exist_in_schema" tests/ # empty
.venv/bin/python -m pytest tests/test_architecture_contracts.py::test_risk_actions_exist_in_schema  # not found
```

Severity: P1. INV-05 is the load-bearing invariant for `RiskLevel` semantics ("Risk must change behavior; advisory-only is theater"). A YAML citation that cannot be located by `pytest --collect-only` makes the invariant doc-only at the citation layer and silently weakens the antibody contract.

Antibody (mandatory fix):
1. Either rename `test_strategy_policy_tables_exist_in_schema` → `test_risk_actions_exist_in_schema` to match YAML, OR
2. Update YAML to point at the existing test ID.
3. **More importantly**: even after the rename, the existing test only asserts `CREATE TABLE IF NOT EXISTS risk_actions` — that is structural existence, not behavioral enforcement of "advisory-only forbidden." Add a behavior antibody:
   ```python
   def test_risk_red_changes_runtime_behavior():
       """INV-05: RED is not advisory. cycle_runner must invoke
       _execute_force_exit_sweep on RED regardless of force_exit_review."""
       # Build minimal portfolio + monkeypatch get_current_level→RED
       # + assert _execute_force_exit_sweep was called.
   ```

### Finding A1.2 — AGENTS.md "9 states" vs enum's 10 (P3, doc drift)

`AGENTS.md:89` and `docs/reference/zeus_domain_model.md:162` say
`9 states in LifecyclePhase enum`. Actual enum at `src/state/lifecycle_manager.py:9-19` has **10** (`PENDING_ENTRY`, `ACTIVE`, `DAY0_WINDOW`, `PENDING_EXIT`, `ECONOMICALLY_CLOSED`, `SETTLED`, `VOIDED`, `QUARANTINED`, `ADMIN_CLOSED`, `UNKNOWN`).

UNKNOWN was added later for chain reconciliation states (CHAIN_UNKNOWN per INV-18). Doc-rot, low severity, but counts can be cited in audits and fail surreptitiously.

Antibody: regex pin in `tests/test_architecture_contracts.py` asserting AGENTS.md and `LIFECYCLE_PHASE_VOCABULARY` length match.

---

## ATTACK 2 — Test bypass [VERDICT: PASS-WITH-NOTE]

`tests/test_executor.py:44-66` autouse fixture monkeypatches **6 production gates**:

```
src.control.cutover_guard.assert_submit_allowed
src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type
src.state.collateral_ledger.assert_buy_preflight
src.state.collateral_ledger.assert_sell_preflight
src.execution.executor._reserve_collateral_for_buy
src.execution.executor._reserve_collateral_for_sell
```

Every test in `test_executor.py` (960 LOC, dozens of tests) traverses a `_live_order` path with these gates **deactivated**. This is unit isolation, not deception — but it means executor tests do **NOT** assert that the live-path gate sequence (`assert_submit_allowed → assert_heartbeat_allows_order_type → assert_buy_preflight → reserve_collateral → place_limit_order`) is preserved.

Coverage of the gates EXISTS in dedicated tests:
- `tests/test_cutover_guard.py:344 test_executor_raises_cutover_pending_when_freeze` — exercises real `assert_submit_allowed`.
- `tests/test_v2_adapter.py:709 test_polymarket_client_wrapper_fails_closed_before_unbound_v2_preflight`.
- `tests/test_executor_command_split.py:990 test_v2_preflight_failure_writes_rejected_event`.

So the antibodies exist. The **PASS-with-note** is: there is no integration test that wires the entire gate cascade through to a real call to `place_limit_order`. Test-bypass risk is a "collective" weakness — order-of-gates regression would not be caught by `test_executor.py` alone.

Antibody (P3): one integration test `test_executor_full_gate_cascade` that exercises `executor._live_order` through real (not monkeypatched) `assert_*` functions on a fixture-only DB.

---

## ATTACK 3 — Doc-only invariant [VERDICT: FAIL]

### Finding A3.1 — INV-05 (see Attack 1.1)

INV-05's only cited test resolves to nothing; even the closest replacement is structural-existence not behavioral. Effective enforcement = doc-only.

### Finding A3.2 — INV-22 cited test under-tests its own statement (P1, governance)

INV-22 statement (`invariants.yaml:225`): *"make_family_id() must resolve to one canonical family grammar across every call site... **Enforcement intent: one choke-point helper + test asserting every call site delegates to it.**"*

Cited test: `tests/test_dual_track_law_stubs.py:210 test_fdr_family_key_is_canonical`.

What the test actually does (inspected lines 210-244): asserts `make_hypothesis_family_id` and `make_edge_family_id` produce distinct deterministic IDs, and that high/low temperature_metric produces different IDs. **It does NOT assert that every call site delegates to the helper.**

The test is the wrong shape for the stated invariant. A semgrep rule (`zeus-no-fdr-family-key-drift`) is also cited; that rule is the real enforcement. But the YAML's `enforced_by.tests:` is doc-only at the test layer.

Reproduction:
```
grep -A30 "def test_fdr_family_key_is_canonical" tests/test_dual_track_law_stubs.py
```

Antibody: add `test_no_inline_family_id_construction()` that walks `src/` AST and asserts no `f"{...}:{...}:..."` pattern that recreates the family_id grammar appears outside `src/strategy/selection_family.py`.

### Finding A3.3 — INV-19 RED-sweep relationship antibody not cited (P2)

INV-19 cites `test_red_triggers_active_position_sweep`. The actual relationship antibody (cross-module — sweep marker → evaluate_exit consumer) is `tests/test_dual_track_law_stubs.py:325 test_red_force_exit_marker_drives_evaluate_exit_to_exit` — added during critic-carol cycle 3 ITERATE (per its own docstring). YAML never picked up the strengthened citation.

Doc-only at citation layer; behaviorally enforced. P2 (citation-rot, not enforcement-rot).

---

## ATTACK 4 — Mode bleed [VERDICT: NO DRIFT FOUND]

`src/config.py:48-57` `get_mode()` is hardcoded `return "live"`. Historical `ZEUS_MODE` env switch retired (per `src/control/control_plane.py:63` "no longer reads the retired ZEUS_MODE switch").

Grep search across `src/`: only 2 hits for `ZEUS_MODE` — both in retiring docstrings. `cycle_runtime.py:2503` reads `is_live_env = str(env or "").strip().lower() == "live"` and gates **live submission** behind that check; non-live env paths don't reach `place_limit_order`.

Evidence (passing tests): `tests/test_cutover_guard.py::test_executor_raises_cutover_pending_when_freeze`.

NO DRIFT FOUND.

---

## ATTACK 5 — Provenance evaporation [VERDICT: PASS]

`src/contracts/provenance_registry.py:231-235` fail-closes on REGISTRY_DEGRADED:

```python
if REGISTRY_DEGRADED:
    raise UnregisteredConstantError(
        f"INV-13: provenance registry failed to load — governance disabled. ..."
    )
```

Bypass path requires explicit `register_emergency_bypass()` with TTL. No silent fall-through.

Cited test (`tests/test_provenance_enforcement.py`) has 19 in-class test methods covering Kelly/market_fusion constants, registry shape, bypass TTL.

NO DRIFT FOUND for INV-13 surface.

---

## ATTACK 6 — Concurrency / atomicity [VERDICT: PASS]

L30 lesson (`with conn:` inside SAVEPOINT atomicity collision): audited all 14 `with conn:` sites in `src/`.

- `src/state/ledger.py:208 append_many_and_project()` uses **explicit SAVEPOINT** (lines 250-264), not `with conn:`. Per docstring lines 213-234 this is deliberate (DR-33-B 2026-04-24 fix per memory L30).
- `src/state/venue_command_repo.py` uses SAVEPOINT context manager (lines 282-297). Correct.
- `src/state/db.py:5087` `with conn:` is for token suppression dual-write — does NOT call `append_many_and_project` inside.
- `src/calibration/retrain_trigger.py:422,443` `with conn:` blocks call `_insert_version` + `save_platt_model_v2` + `deactivate_model_v2`. Inspected `src/calibration/store.py:416-485`: those callees use bare `conn.execute` only — no nested `with conn:`. **No L30 collision.**
- `src/state/chain_reconciliation.py:272,318` calls `append_many_and_project` from inside `_append_canonical_rescue_if_available` / `_append_canonical_size_correction_if_available`. Caller `reconcile()` (line 181) does NOT wrap in `with conn:`. SAVEPOINT (inside append_many_and_project) is the only transaction boundary — clean.

NO DRIFT FOUND.

---

## ATTACK 7 — Time semantics [VERDICT: PASS]

- `src/data/hourly_instants_append.py:135-159` correctly handles DST (uses `ZoneInfo`, sets `is_ambiguous_local_hour` and `is_missing_local_hour` flags, computes `dst_active`, converts to UTC).
- `src/contracts/dst_semantics.py` is a real DST helper module (not just a stub).
- Searched for `datetime.now()` (tz-naive): only hit at `cycle_runtime.py:2100` is a comment. All other calls use `datetime.now(timezone.utc)`.
- `src/calibration/decision_group.py:141` rejects naive datetime with `ValueError("issue_time must be timezone-aware; naive datetime is ambiguous")`.

NO DRIFT FOUND. London 2025-03-30 case (Fitz Constraint #4 canonical example) is structurally protected.

---

## ATTACK 8 — Settlement edge cases [VERDICT: PASS]

`src/contracts/settlement_semantics.py` separates `WMO_HalfUp` from `HKO_Truncation` with explicit Chinese-language warning comments (lines 73-82, 164-167) about not cross-applying. `assert_settlement_value` is mandatory per AGENTS.md. Shoulder bin asymmetry handled in `src/contracts/calibration_bins.py:114-185` with explicit interior-span parity check (line 146).

Per known_gaps.md "harvester settlement lookup is metric/source/station aware, LOW settlement writes use LOW identity" → resolved overlay 2026-04-30.

NO DRIFT FOUND in production-path settlement code.

Note: `src/contracts/settlement_semantics.py:170` HKO uses `precision=1.0` despite the docstring at line 167 saying "HKO reports 0.1°C precision". Reading carefully, the comment refers to *source resolution* and 1.0 = settlement-integer truncation. Coherent on second read; could be reworded for non-confusion (P3 doc nit).

---

## ATTACK 9 — Risk level coercion [VERDICT: FAIL]

### Finding A9.1 — Missing trailing-loss reference produces DATA_DEGRADED, not RED (P1, behavior vs spec)

AGENTS.md:81-83 (root law):
> Overall level = max of all individual levels. Computation error or broken truth input → RED. Fail-closed.

Code: `src/riskguard/riskguard.py:269-277`:
```python
if status not in ("ok", "stale_reference") or reference is None:
    return {
        "loss": 0.0,
        "level": RiskLevel.DATA_DEGRADED,
        ...
    }
```

`risk_level.py:28`: `order = {GREEN:0, DATA_DEGRADED:1, YELLOW:2, ORANGE:3, RED:4}`.

DATA_DEGRADED is BELOW YELLOW in the ordinal, far below RED. `cycle_runner.py:570 red_risk_sweep = risk_level == RiskLevel.RED` — **DATA_DEGRADED does NOT trigger sweep.**

Practical effect: if the trailing-loss reference is unavailable (a "broken truth input" by the AGENTS.md spec), Zeus stays in DATA_DEGRADED → no new entries (good — INV-05 partially satisfied via `_risk_allows_new_entries`), but **no RED sweep of active positions**. AGENTS.md spec is "Fail-closed → RED"; code is "Fail-soft → DATA_DEGRADED ≈ YELLOW."

This is defensible operator design (DATA_DEGRADED is a distinct truth claim about NOT KNOWING vs KNOWING the system is breached) but it **contradicts the literal AGENTS.md text**. There is no test enforcing either direction.

Reproduction:
```
grep -rn "DATA_DEGRADED.*RED\|missing.*reference.*RED" tests/  # 0 hits
```

Severity: P1. The behavior/spec divergence is on the load-bearing risk surface. Either AGENTS.md should be amended to permit the DATA_DEGRADED middle level on missing reference, or the code should escalate to RED on missing reference. Neither is done.

Antibody: write the chosen-direction test. Either:
```python
def test_missing_trailing_loss_reference_escalates_to_red():
    # AGENTS.md path
    snap = _trailing_loss_snapshot(empty_db, ...)
    assert snap["level"] == RiskLevel.RED
```
or amend AGENTS.md and add:
```python
def test_missing_trailing_loss_reference_returns_data_degraded():
    snap = _trailing_loss_snapshot(empty_db, ...)
    assert snap["level"] == RiskLevel.DATA_DEGRADED
    # Document why DATA_DEGRADED is not RED-equivalent here.
```

### Finding A9.2 — `get_force_exit_review()` returns False on missing row (P3)

`src/riskguard/riskguard.py:1077-1099`: `get_force_exit_review` returns `False` on `row is None`, `True` on `Exception`. The "no row" case logically ≈ "no risk state computed yet" = same as "DB not initialized" = should be conservative TRUE (or the function should not be called before first riskguard tick).

Currently mitigated by `red_risk_sweep = risk_level == RiskLevel.RED` running independently. So missing-row + RED → sweep fires; missing-row + GREEN → no sweep. But the asymmetry between "exception → True (closed)" vs "no row → False (open)" is suspicious.

Severity: P3. Mitigated. Antibody: `if row is None: return True` to match exception semantics.

---

## ATTACK 10 — Lifecycle phase string invention [VERDICT: PASS]

`src/state/lifecycle_manager.py:9-19` defines closed enum. `LIFECYCLE_PHASE_VOCABULARY = tuple(phase.value for phase in LifecyclePhase)` is the canonical vocabulary. Semgrep rule `zeus-no-direct-phase-assignment` (cited by INV-07, present at `architecture/ast_rules/semgrep_zeus.yml:56`).

Inspected enum vs all `phase=` assignments in src/state/portfolio.py and projection.py — all use enum members.

NO DRIFT FOUND.

---

## Top 10 ranked findings (severity-weighted)

| Rank | Finding | Severity | Pattern | Money-path stage |
|------|---------|----------|---------|------------------|
| 1 | INV-05 cited test does not exist; closest substitute is structural-existence only, not behavior | P1 | 1 + 3 | Risk → Execution gate |
| 2 | Missing trailing-loss reference produces DATA_DEGRADED, contradicting AGENTS.md "Computation error → RED. Fail-closed." | P1 | 9 | Risk → Sweep |
| 3 | INV-22 cited test under-asserts its own statement (every-call-site delegation) | P1 | 3 | Calibration / Edge attribution |
| 4 | Test-executor autouse fixture stubs 6 production gates; no integration-cascade test asserts gate ordering | P3 | 2 | Execution |
| 5 | INV-19 strengthened relationship antibody not cited in YAML | P2 | 3 | Risk → Exit |
| 6 | `get_force_exit_review()` row-None vs exception fail-closed asymmetry | P3 | 9 | Risk → Sweep |
| 7 | AGENTS.md "9 states" vs 10-member LifecyclePhase enum | P3 | 1 | Lifecycle |
| 8 | Settlement HKO precision=1.0 vs docstring "reports 0.1°C precision" wording | P3 | 8 | Settlement |
| 9 | F3 hook regex relies on first-line scan; multi-line heredoc with `git merge x` on a non-first line not blocked (documented trade-off lines 31-35) | P3 | 1 | Hook protection |
| 10 | (no further P0/P1/P2 found) | — | — | — |

Cumulative P0 count: **0**. P1 count: **3**. P2 count: **1**. P3 count: **5**.

---

## Speculative (could-be findings without reproduction at HEAD)

- **S1 (low confidence)**: `cycle_runtime.py:2503` `is_live_env = str(env or "").strip().lower() == "live"` — if `env` is None or unset, falls back to non-live path. With `get_mode()` hardcoded to `"live"`, this is fine TODAY. If a future caller passes a different env, behavior diverges silently. Not reproducible at HEAD without code change.
- **S2 (low confidence)**: `chain_reconciliation.py:299` early-return on `pending_entry` phase says "race: skip canonical size correction here." If a fill never lands, the row stays in `pending_entry` indefinitely and size correction never fires. Quarantine-48h flow likely catches it, but I did not trace the full path.
- **S3 (low confidence)**: PR-statement F18 plan claims "production canonical writer always passes the value (`src/state/projection.py:90` uses CANONICAL_POSITION_CURRENT_COLUMNS)". Verified at HEAD. But: any future writer that bypasses `projection.py` (e.g., a migration/repair script) would now fail at INSERT (not the prior silent-default). Tests for the migrate path may regress. Not a drift; a side-effect of P2a.

---

## Required fixes (in REJECTED priority order — operator may downgrade if business reasons override)

1. **A1.1 / A3.1 INV-05 citation**: in `architecture/invariants.yaml:55-56`, either rename the test to match or update the cite. Add a behavior antibody (provided pseudo-code in A1.1).
2. **A9.1 trailing-loss missing reference**: pick a direction. If DATA_DEGRADED is intended (current code), amend `AGENTS.md:83` to document the middle-level. If RED is intended (current spec), patch `riskguard.py:272` to escalate to RED on missing reference. Add the corresponding test.
3. **A3.2 INV-22 enforcement**: add `test_no_inline_family_id_construction()` AST walker.
4. **A1.2 / A3.3 / A2 P3 items**: batch as a doc-citation-sweep next remediation cycle.

---

End of report.
