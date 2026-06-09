# REAUDIT 0.1 — Live `replacement_0_1` Authority Path (consolidated, evidence-verified)

> Created: 2026-06-07
> Last reused or audited: 2026-06-07
> Authority basis: REALIGN_0_1_AUTHORITY.md + PR_SPEC.md §0/§1/§2; five re-audit lenses (AUTH_GATE, DIRLAW_NOSIDE, E2E_TRACE, SUBMIT_PATH, BUNDLE_INTEGRITY); inline code verification against PRIMARY `/Users/leofitz/zeus` @ `fix/opportunity-book-selector` (HEAD `16c35e7445`+hotfixes) + READ-ONLY DB/on-disk probes.
> Scope: READ-ONLY audit consolidation. No source/test edits. This file is the single re-pointed authority for Phase −1 FIX-1/FIX-2b on the path that is **actually live**.

All line numbers below were re-verified against the PRIMARY tree (not the stale worktree). Verification receipts are inline.

---

## §0 The one-paragraph picture (re-probed reality, not memory)

For `event_type=='FORECAST_SNAPSHOT_READY'`, the live YES-probability authority is `_replacement_authority_probability_and_fdr_proof` (`src/engine/event_reactor_adapter.py:5430` def, called FIRST at `:5301` inside `_live_yes_probabilities`; if non-None it IS the live answer at `:5309-5310`, canonical only runs as fallback at `:5311`). Its sole authority gate is `_replacement_authority_enabled()` (`:5348-5353`) = `bool(flags['openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled'])` — **flag alone** (verified `True` in `config/settings.json`). Its other three gates (`_latest_replacement_readiness` `:5451`, `read_replacement_forecast_bundle` `:5459`, bin-binding `:5479-5481`) are forecast-availability checks, NOT settlement evidence. The settlement-grounded gate predicates `ReplacementForecastPromotionEvidence.promotion_allowed()` (`src/data/replacement_forecast_runtime_policy.py:101-102`) and `…CapitalObjectiveEvidence.capital_objective_allowed()` (`:142-143`) are **never referenced anywhere on this path** (grep-confirmed). The evidence objects ARE loaded in `main.py:5162-5163` and threaded to the legacy hook (`:5198-5199`) — but the 0.1 path never receives them, and once the 0.1 path runs it stamps `payload['_edli_q_source']='replacement_0_1'` (`:5504`), which makes the legacy hook SKIP itself (`_replacement_primary_authority_already_applied` `:5227-5228`, gate at `:1474`) — so no evidence-bearing backstop exists.

**PROVEN HARM (verified this session):** the on-disk `state/replacement_forecast_shadow/promotion_evidence.json` (loaded by `main.py:4698-4724`) returns `promotion_allowed()==False` with blocking codes `[REPLACEMENT_PROMOTION_INSUFFICIENT_OFFICIAL_DAYS, …INSUFFICIENT_OFFICIAL_ROWS, …Q_LCB_COVERAGE_TOO_LOW, …NESTED_WALK_FORWARD_NOT_PASSED]`. (Capital-objective evidence currently returns `True` — promotion is the binding failure, not capital.) DB (`state/zeus-forecasts.db`, mode=ro): **171/171** `forecast_posteriors` rows are `trade_authority_status='SHADOW_ONLY'` and **171/171** have NULL/empty `q_lcb_json`. The bundle reader is type-restricted to `{SHADOW_ONLY, SHADOW_VETO_ONLY}` (`replacement_forecast_bundle_reader.py:54`, SQL `:434`) — i.e. the bundle is type-guaranteed shadow, and the live consumer trades on it anyway. Today the ONLY thing preventing live fills is an **accident** (modal-bin Wilson-LCB ≈0.42 < the 0.51 win-rate floor), not the evidence gate.

---

## §1 FIX-1 (re-pointed): the single shared evidence gate — ONE builder, consulted by both paths

This re-points PR_SPEC FIX-1 from "the legacy resolver" to "the actually-live 0.1 path + the legacy resolver, behind ONE gate." K=1 structural decision (iron rule #4: one truth). The fix composes ABOVE the existing structural checks (NO-side fail-closed, single-bin binding) — it does not replace them.

### 1.1 The gate function (one builder, pure, no IO)

Add ONE pure predicate, co-located with the evidence dataclasses (NO new module, NO second loader):

```
# src/data/replacement_forecast_runtime_policy.py  (next to the evidence dataclasses)
def replacement_live_authority_evidence_gate(
    promotion_evidence: ReplacementForecastPromotionEvidence | None,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None,
) -> tuple[bool, tuple[str, ...]]:
    if promotion_evidence is None:
        return (False, ("REPLACEMENT_LIVE_AUTHORITY_PROMOTION_EVIDENCE_REQUIRED",))
    if capital_objective_evidence is None:
        return (False, ("REPLACEMENT_LIVE_AUTHORITY_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED",))
    blocking = (
        promotion_evidence.blocking_reason_codes()
        + capital_objective_evidence.blocking_reason_codes()
    )
    if blocking:
        return (False, blocking)
    return (True, ())
```

It performs NO IO (takes already-constructed dataclasses), so it is trivially unit-testable and cannot drift from `promotion_allowed()`/`capital_objective_allowed()` (it reuses their `blocking_reason_codes()`).

### 1.2 Reuse the existing loaders — do NOT build a second provenance path

`main.py` already loads both evidence objects fail-closed-to-None: `_replacement_forecast_promotion_evidence_from_settings` (`main.py:4698-4724`) and `_replacement_forecast_capital_objective_evidence_from_settings` (`main.py:4726-4751`), both reading `state/replacement_forecast_shadow/promotion_evidence.json`. They are called at `main.py:5162-5163` and threaded into the adapter at `:5198-5199`. The 0.1 path simply does not receive them — **the gap is wiring, not a missing loader.** Adding a fresh JSON reader inside `event_reactor_adapter` would create two readers of the same file (drift risk) — explicitly forbidden. ONE loader (main.py), ONE gate (runtime_policy.py), TWO consult sites.

### 1.3 Insertion Point A — the 0.1 path (the live chokepoint)

Thread the already-loaded `promotion_evidence`/`capital_objective_evidence` from `main.py:5162-5163` down through `_live_yes_probabilities` → `_replacement_authority_probability_and_fdr_proof` as new keyword params (they already exist as objects; the adapter request constructed at `main.py:5176-5199` already carries them to the legacy hook — pass the same references). Then inside `_replacement_authority_probability_and_fdr_proof`, **immediately after the flag check at `:5445-5446`, BEFORE the readiness load at `:5451`:**

```
permitted, codes = replacement_live_authority_evidence_gate(promotion_evidence, capital_objective_evidence)
if not permitted:
    return None        # fail-safe DEGRADE: :5309 falls through to canonical/shadow at :5311
```

`return None` (not `raise`) is deliberate: it makes `_live_yes_probabilities` fall through to `_canonical_probability_and_fdr_proof` (`:5311`) so live trading continues on the canonical kernel rather than crashing the cycle. Because the gate runs BEFORE `payload['_edli_q_source']='replacement_0_1'` (`:5504`), the q_source is never stamped on a failed-evidence cycle — which re-enables the legacy hook (§1.5) as a second evidence-gated layer.

### 1.4 Insertion Point B — the legacy resolver (defense-in-depth; fixes the already-failing antibody)

In `resolve_replacement_forecast_runtime_policy` (`runtime_policy.py:195-248`), the `else:` branch at `:237-239` currently sets `status=LIVE_AUTHORITY_STATUS` UNCONDITIONALLY (verified: body never references `promotion_evidence`/`capital_objective_evidence`, the two params accepted at `:198-199`). Replace the unconditional assignment:

```
else:
    permitted, codes = replacement_live_authority_evidence_gate(promotion_evidence, capital_objective_evidence)
    if permitted:
        status = LIVE_AUTHORITY_STATUS
        reason_codes = ("REPLACEMENT_NEW_DATA_LIVE_AUTHORITY",)
    else:
        status = SHADOW_VETO_ONLY_STATUS
        reason_codes = ("REPLACEMENT_LIVE_AUTHORITY_REQUIRES_EVIDENCE", *codes)
```

The existing `:245-247` ternaries already null `trade_authority_enabled`/`kelly_increase_enabled`/`direction_flip_enabled` whenever `status != LIVE_AUTHORITY_STATUS`, so no extra forcing is needed. Mirror into `evaluate_replacement_forecast_switch_decision` (`switch_decision.py:170-181`): the `LIVE_AUTHORITY_STATUS` branch never reads `request.capital_objective_evidence` (verified dead param) — fold `replacement_live_authority_evidence_gate(...)` into that branch and return `SWITCH_SHADOW_VETO_ONLY` when not permitted. **Use the SAME gate function — do NOT author a second predicate.**

### 1.5 Why both insertion points, and how they compose (the degrade ladder)

A (0.1 path) denies authority on absent/failing evidence → returns None → q_source NOT stamped `replacement_0_1` → `_replacement_primary_authority_already_applied(proof)` is False (`:5227-5228`) → the legacy hook IS invoked at `:1474` → its resolver (B) caps at `SHADOW_VETO_ONLY`. One coherent degrade ladder, no orphaned evidence, no parallel mechanism. The single-owner q_source skip itself is CORRECT (commit `aeff1cd24b` de-dup) and is NOT changed.

### 1.6 TDD — relationship tests FIRST (cross-boundary), then make the existing failing antibodies pass

Write BEFORE implementation:

1. `tests/engine/test_replacement_0_1_live_authority_probability.py::test_replacement_0_1_authority_denied_when_settlement_evidence_absent_or_failing` — flag True (monkeypatch settings so `_replacement_authority_enabled()` is True), stub readiness+bundle+bin-binding all PASS (so evidence is the ONLY remaining gate). Assert ALL THREE of `(promotion_evidence=None)`, `(capital_objective_evidence=None)`, `(real on-disk failing promotion_evidence)` make `_replacement_authority_probability_and_fdr_proof` return None, AND that the FSR path then falls back to canonical (`payload['_edli_q_source'] != 'replacement_0_1'`).
2. **Single-owner proof:** monkeypatch `replacement_live_authority_evidence_gate` to a known verdict; assert BOTH the 0.1 path (test 1) and `resolve_replacement_forecast_runtime_policy` observe the patched verdict — proving one owner, not two predicates.
3. **Make the existing already-failing antibodies pass:** `tests/test_replacement_forecast_runtime_policy.py:86-111` (flag-true + no-evidence ⇒ status `BLOCKED`/SHADOW_VETO with `REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED`) and `:177-213` (weak evidence ⇒ not LIVE_AUTHORITY; passing ⇒ LIVE_AUTHORITY). Both currently FAIL (`AssertionError: 'LIVE_AUTHORITY' == 'BLOCKED'`). Note: the existing success-path assertion in `test_replacement_0_1_live_authority_probability.py` that asserts `probability_authority=='replacement_0_1'` with NO evidence **encodes the bug as expected behavior** — it must be moved behind the evidence gate (set both evidence objects to passing in that test's fixture).
4. **E2E backstop re-enable:** flag True + failing on-disk evidence ⇒ assert (a) `_replacement_authority_probability_and_fdr_proof` returns None, (b) `payload['_edli_q_source'] != 'replacement_0_1'`, (c) `_replacement_primary_authority_already_applied(proof)` is False so the legacy hook IS invoked, (d) the hook result is SHADOW_VETO_ONLY/BLOCKED, not LIVE_AUTHORITY.

---

## §2 Ranked residual gap ledger (CRITICAL → LOW)

Each row: file:line (verified) + the antibody test that makes the error category unconstructable.

### CRITICAL

**C1 — Flag-alone live authority; settlement-validated promotion+capital evidence never consumed on the live path.**
`event_reactor_adapter.py:5348-5353` (flag-only `_replacement_authority_enabled`), gate at `:5445-5446`, no reference to `promotion_allowed()`/`capital_objective_allowed()` anywhere in the file. Loaders exist + threaded (`main.py:4698-4751`, `5162-5163`, `5198-5199`) but the 0.1 path never receives them. **Proven harm:** on-disk `promotion_allowed()==False`; all 171 posteriors SHADOW_ONLY.
*Antibody:* §1.6 tests 1+2 (relationship: flag-true + absent/failing evidence ⇒ None; single-owner gate patch observed by both sites). Fix = FIX-1 (§1).

**C2 — Legacy resolver `resolve_replacement_forecast_runtime_policy` grants LIVE_AUTHORITY from flags alone; evidence params dead (antibody currently FAILS).**
`runtime_policy.py:195-248`; `else:` at `:237-239` unconditional `LIVE_AUTHORITY_STATUS`; params `:198-199` never referenced in body (verified). `switch_decision.py:170-181` LIVE_AUTHORITY branch never reads `capital_objective_evidence` (same theater on next layer).
*Antibody:* §1.6 test 3 (make `tests/test_replacement_forecast_runtime_policy.py:86-111,177-213` pass). Fix = FIX-1 Insertion Point B (§1.4).

**C3 — Live authority trusts a bundle the materializer declares SHADOW_ONLY / "shadow_point_probability_only" / `q_lcb_json_role='absent_no_calibrated_lcb_available'` / `training_allowed=False`; status fields never read.**
The q is a raw Bayesian fusion (AIFS prior × Gaussian Open-Meteo anchor, `anchor_weight=0.80`) in `src/strategy/openmeteo_ecmwf_ifs9_aifs_soft_anchor.py:176-212` — ZERO settlement-VERIFIED calibration step. The live consumer reads `replacement_bundle.q` directly (`event_reactor_adapter.py:5476-5484`) but NEVER reads `replacement_bundle.trade_authority_status`. Materializer provenance: `src/data/replacement_forecast_materializer.py:455-499`. This is data-provenance bypass (Fitz #4): the data carries `authority=SHADOW`, the code overrides it with a flag.
*Antibody:* constructor-level — add a typed `LiveReplacementBundle` newtype whose `__post_init__` raises unless `trade_authority_status=='LIVE_AUTHORITY'` AND `q_lcb is not None`; require `_replacement_authority_probability_and_fdr_proof` to accept ONLY that type (the current `ReplacementForecastPosteriorBundle` stays the shadow type). Then `LiveReplacementBundle(trade_authority_status='SHADOW_ONLY')` is a TypeError — a shadow bundle reaching the live consumer is **unconstructable**. Until that newtype lands, the §1 evidence gate (C1) already blocks the live path because promotion evidence fails. C3 is the deeper structural antibody; C1 is the immediate one. (Note: `'LIVE_AUTHORITY'` is not in the bundle reader's allowed set today, so the promotion job that writes it must be the thing that flips it — see §5.)

**C4 — OperatorArm (FIX-2b) is NOT implemented; canary POSTs real orders with `edli_live_operator_authorized=false`.**
`grep -rn 'OperatorArm\|operator_arm' src/` returns ZERO. `_assert_edli_live_promotion_artifact` (which checks `edli_live_operator_authorized`, `main.py:563-567`) is called ONLY at `main.py:1119` (the `edli_live` branch), NOT the `edli_live_canary` branch. The EDLI `_submit` gate (`event_reactor_adapter.py:915-940`) has no token check.
*Antibody:* modes×operator_authorized matrix test — `edli_live_canary` + `real_order_submit_enabled=True` + `edli_live_operator_authorized=False` ⇒ adapter returns `reason='OPERATOR_ARM_MISSING'`, `executor_submit` never called. Fix = FIX-2b (§3). NOTE on live-trade context: `edli_live_order_events=0` — the 293 live fills came from the MAINLINE executor, not EDLI. So FIX-2b hardens the armed-but-not-yet-filling EDLI path; it does NOT touch the mainline that is currently filling (confirmed independent, §6).

### HIGH

**H1 — DIRECTION LAW (`buy_yes ⟺ bin==argmax(q)`) is not re-asserted where the 0.1 proof binds; the enforcing hook is intentionally skipped for `replacement_0_1`.**
The argmax rule lives only in `replacement_forecast_hook_factory.py:280-294` (`_h3_selected_bin_id`→`_h3_direction_for_candidate_bin`). The live path skips the hook (`event_reactor_adapter.py:1474` + `_replacement_primary_authority_already_applied` `:5227-5228`). The 0.1 builder emits a `buy_yes` proof for EVERY bin with that bin's own `q_yes` (`:5477-5484`); a non-modal bin whose YES is mispriced cheap yields a selectable wrong-side `buy_yes`. The law is trusted upstream, never re-checked at the consuming boundary (PR_SPEC §0.5).
*Antibody:* relationship test — bundle argmax=bin B, a `buy_yes` proof on bin A≠B with `q_yes>price` must be non-selectable (prefilter False, typed `REPLACEMENT_FORECAST_DIRECTION_LAW_VIOLATION`). Structural: frozen `ReplacementYesCandidate(bin_id, selected_bin_id)` whose `__post_init__` raises unless `bin_id==selected_bin_id`; build the live `buy_yes` leg only through it. Compute `selected_bin=argmax(q_map)` ONCE in `_replacement_authority_probability_and_fdr_proof` and stamp into evidence; refuse `buy_yes` admissibility for `bin != selected_bin` in the per-candidate loop (`:5477-5503`). This is the re-located PR_SPEC FIX-3 for the live path.

**H2 — posterior_id / probability_authority dropped at the `_CandidateProof` boundary — no typed column carries them to any receipt; fill→posterior link unresolvable in SQL.**
`_replacement_authority_probability_and_fdr_proof` returns `posterior_id`/`probability_authority`/`replacement_product_id` in the evidence dict (`:5505-5517`), but `_generate_candidate_proofs` reads ONLY `p_cal_vector_hash`/`p_live_vector_hash` — `posterior_id`/`probability_authority` are silently discarded. `_CandidateProof`, `EventSubmissionReceipt` (`reactor.py:96-173`), and `edli_no_submit_receipts` (`no_submit_receipts.py:75-162`) have no such columns. Only durable trace is the `receipt_json` blob + `DecisionProofAccepted` payload — unqueryable without `JSON_EXTRACT`. (See §4 for the e2e additions.)
*Antibody:* after a FSR cycle, `SELECT … WHERE probability_authority='replacement_0_1'` returns rows and `posterior_id IS NOT NULL` equal to the bundle's posterior_id — no `JSON_EXTRACT`.

**H3 — Readiness `expires_at` is loaded but NEVER compared to `decision_time`; with `decision_time=now`, an arbitrarily stale READY posterior grants live authority.**
`expires_at` is assigned once (`replacement_forecast_hook_factory.py:136`) and never compared anywhere on the replacement live path (grep-confirmed). The bundle reader rejects FUTURE posteriors (`replacement_forecast_bundle_reader.py:443-446`) but imposes NO upper age bound. `_latest_replacement_readiness` just takes `ORDER BY computed_at DESC LIMIT 1` with status READY. `decision_time=datetime.now(UTC)` (`main.py:5159`). Stale-data-trade is the inverse of zero-trade fault — trading dead forecast as live.
*Antibody:* relationship test — readiness `expires_at < decision_time` (and/or `source_cycle_time` older than a configured horizon, e.g. >24-36h) ⇒ `_replacement_authority_probability_and_fdr_proof` raises `REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_EXPIRED` / returns None. Put the bound in the bundle reader so both the live path and legacy hook inherit it (one gate). `expires_at` must be a hard gate, not metadata.

**H4 — Topology-core equivalence drops `settlement_unit`/`display_unit`/`rounding_rule` — a row with different settlement semantics but identical physical geometry can bind to a current market.**
`_topology_core` (`replacement_forecast_bundle_reader.py:345-366`) keeps ONLY `{bin_id, lower_c, upper_c, center_c, settlement_step_c}` — verified it DROPS `display_unit`, `settlement_unit`, `rounding_rule`. The hash-mismatch fallback `_topology_core_equivalent` (`:369-372`, called at `:478`) therefore treats two posteriors that agree on Celsius geometry but disagree on `rounding_rule` (e.g. `wmo_half_up` vs `oracle_truncate`, keyed off `hko` settlement source) or `settlement_unit` as identical. `rounding_rule` changes which integer the oracle settles to at the bin boundary — same physical core, different settlement outcome. Archived `NO_TOPO` legacy rows happen to be safe (`_topology_core` returns None ⇒ not equivalent), but the allowance is a general path, not scoped to them.
*Antibody:* relationship test across posterior→market settlement boundary — two topologies identical in `lower_c/upper_c/center_c` but differing in `rounding_rule` (or `settlement_unit`) ⇒ `_topology_core_equivalent` returns False, bundle read returns `REPLACEMENT_POSTERIOR_BIN_TOPOLOGY_HASH_MISMATCH`. Fix: include `settlement_unit` + `rounding_rule` in `_topology_core` (only `display_unit` may be excluded as pure presentation). The right invariant: a posterior binds only if its full SETTLEMENT identity (geometry + unit + rounding) matches.

### MEDIUM

**M1 — `q_lcb_json_role='absent_no_calibrated_lcb_available'` is ignored; the live authority silently substitutes a self-derived Wilson-LCB for a calibrated one.**
All 171 bundles declare role `absent_no_calibrated_lcb_available` and NULL `q_lcb_json`. `_replacement_yes_lcb_for_bin` (`event_reactor_adapter.py:5414-5427`) treats absence as license to manufacture an LCB from AIFS member spread (`:5418-5424`) — a forecast-internal dispersion bound, NOT settlement-coverage-grounded — stamped `source='FORECAST_BOOTSTRAP'`. The 0.51 floor + Kelly then consume this as if it were the conservative win-rate LCB (overconfidence=ruin: the 95% claim is unearned vs VERIFIED WU settlement).
*Antibody:* `test_live_authority_blocks_when_lcb_role_absent` — role `absent_no_calibrated_lcb_available` + flag True ⇒ `_replacement_authority_probability_and_fdr_proof` returns None; the AIFS-spread substitution is reachable ONLY for shadow/veto, never for a vector feeding the live win-rate floor. (Folds into C3's typed-bundle antibody: live bundle requires `role=='calibrated'`.)

**M2 — `LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES` contains `YES_UCB_DERIVED`, which is NOT in `CALIBRATION_SOURCES` — allow-list ⊄ carrier vocabulary.**
`live_admission.py:23` allow-list = `{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC, YES_UCB_DERIVED}`; `qlcb_provenance.py:43-46` `CALIBRATION_SOURCES` = `{FORECAST_BOOTSTRAP, EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}` and `QlcbProvenance.__post_init__` raises on any source outside that frozenset (verified). So a `q_lcb` can NEVER carry `YES_UCB_DERIVED` — dead/misleading vocabulary; fail-safe today (never matches) but mis-routes if anyone adds it to one set only. This is PR_SPEC FIX-4's exact open item.
*Antibody:* module-load invariant — `assert LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES <= CALIBRATION_SOURCES` (fails today). Make it a collection-time assertion so the build breaks on divergence. Fix: remove `YES_UCB_DERIVED` from line 23 (genuine native-NO sources are EMOS_ANALYTIC + SETTLEMENT_ISOTONIC) unless first added to `CalibrationSource` Literal + `CALIBRATION_SOURCES`.

**M3 — `q_lcb_calibration_source` queryable only via `JSON_EXTRACT` — no typed column on `edli_no_submit_receipts`.**
Written to `receipt_json` (`no_submit_receipts.py:189`); field exists on `EventSubmissionReceipt` (`reactor.py:173`) but no dedicated column.
*Antibody:* after a `replacement_0_1` cycle, `edli_no_submit_receipts.q_lcb_calibration_source == 'FORECAST_BOOTSTRAP'`; any `probability_authority='replacement_0_1'` row with NULL source fails. (Same migration batch as §4.)

### LOW

**L1 — `p_cal_vector_hash == p_live_vector_hash` on the 0.1 path — no independent cal/live distinction (two-hash audit is vacuous for replacement orders).**
`event_reactor_adapter.py:5509-5516` sets both from the same `q_by_condition`. On canonical they differ (Platt-cal vs bias-adjusted); on 0.1 there is one vector.
*Antibody:* assert `p_cal_vector_hash == p_live_vector_hash` for `replacement_0_1` receipts (documents the known-identical state so any future divergence is caught), OR collapse to one `p_replacement_vector_hash` key + update the unpack site. Read-only doc note for now.

**L2 — buy_no fail-closed q/lcb=0.0 is hardcoded in TWO places (authority builder `event_reactor_adapter.py:5491-5496` + the canonical proof generator's buy_no leg) — duplicate mechanism (iron rule: ONE truth).**
NO-side derivation is genuinely safe (see §6) — this is defense-in-depth only: a future edit could re-enable one site while leaving the other.
*Antibody:* consolidate to a single `fail_closed_no_leg()` helper; property test — for ANY `q_yes∈[0,1]` and ANY aifs prob, `lcb_by_direction[(cond,'buy_no')].q_lcb==0.0` and `prefilter[(cond,'buy_no')] is False`.

**L3 — `replacement_forecast_receipt_tag` (carrying `posterior_id`) is set ONLY on the legacy hook path, not the 0.1 path — partial overlap with H2.**
`replacement_forecast_reactor_hook.py:161` writes `posterior_id` into the tag; the 0.1 path initialises the tag to None (`:1473`) and never sets it. So the new live path has NO `replacement_forecast` blob in `receipt_json`.
*Antibody:* after a 0.1 cycle, `receipt.replacement_forecast['posterior_id'] == bundle posterior_id`. Subsumed by H2's typed-column fix (preferred over re-adding a JSON blob).

---

## §3 FIX-2b — OperatorArm, re-located insertion points on current primary

The submit gate shifted ~1-2 lines from PR_SPEC's stated 916-939 because the hotfix inserted the 2026-06-04 OPERATOR LAW comment block (`event_reactor_adapter.py:941-954`) AFTER the three guards. Verified current layout inside `_submit` (closure of `event_bound_live_adapter_from_trade_conn`, def at `:836`, `_submit` at `:900`):

- `:915-916` `proof_accepted`/`decision_proof_bundle` guard
- `:917-924` `LIVE_CANARY_DISABLED`
- `:925-931` `EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED`
- `:933-939` `EXECUTOR_BOUNDARY_MISSING`
- `:941-954` OPERATOR LAW comment block (mainstream is display-only)
- `:955-961` canary_force_taker
- `:962-987` build certificates + `executor_submit` call at `:987`

**Insertion point:** a NEW if-block between line 939 (end of `EXECUTOR_BOUNDARY_MISSING`) and line 941 (OPERATOR LAW comment):

```
if real_order_submit_enabled and operator_arm is None:
    return EventSubmissionReceipt(
        False, event.event_id, event.causal_snapshot_id,
        reason="OPERATOR_ARM_MISSING", proof_accepted=False,
    )
```

**Signature:** add `operator_arm: OperatorArm | None = None` to `event_bound_live_adapter_from_trade_conn` (`:836`).

**Token construction (main.py):** `OperatorArm` must be a sentinel type constructible ONLY in `main.py` after asserting `edli_live_operator_authorized==true` — NOT a dataclass importable/instantiable elsewhere. The current `edli_live`-only check is `_assert_edli_live_promotion_artifact` (`main.py:563-567`) called at `:1119`. Extend to the canary branch: construct the token in BOTH `{edli_live, edli_live_canary}` branches only when `edli_live_operator_authorized==true`, and pass it into `event_bound_live_adapter_from_trade_conn` at the `main.py:5176-5199` kwargs. Absent token ⇒ no-submit adapter (or `OPERATOR_ARM_MISSING` receipt).

**Independence constraint (do NOT regress §6):** add `operator_arm` ONLY to the EDLI adapter closure. Do NOT add it to `execute_final_intent`/`_live_order` — the mainline executor must stay independent of the EDLI submit boundary.

**TDD matrix:** `edli_live_canary` + `real_order_submit_enabled=True` + `edli_live_operator_authorized=False` ⇒ `reason='OPERATOR_ARM_MISSING'`, `executor_submit` never called; `edli_live_canary` + `authorized=True` ⇒ `executor_submit` called. (Currently passes vacuously because no check exists.)

---

## §4 E2E order-trace — minimum additions to make every `replacement_0_1` order SQL-reconstructable

Today the only durable trace of which posterior drove a live order is the `receipt_json` blob + the `DecisionProofAccepted` payload — both require `JSON_EXTRACT`. The fill→posterior link is currently unresolvable in SQL. Minimum additions (one idempotent migration batch, `ALTER TABLE … ADD COLUMN`, no NOT NULL to preserve hash stability of existing rows):

1. **`posterior_id` typed column** on `edli_no_submit_receipts` + `edli_live_order_projection` (FK `forecast_posteriors(posterior_id)`). Carry it: add `posterior_id: int | None` to `_CandidateProof` (populate from `probability_evidence['posterior_id']` at the unpack site near `event_reactor_adapter.py:5101`) and to `EventSubmissionReceipt` (`reactor.py:~96`); write it in `EdliNoSubmitReceiptLedger.insert_idempotent` (`no_submit_receipts.py:75`); add to `_live_decision_audit_payload` (`event_reactor_adapter.py:2752`) so the live order aggregate event is self-contained.
2. **`probability_authority` typed column** on the same two tables (populate from `receipt.q_source` = `'replacement_0_1'`), plus a partial index `… WHERE probability_authority IS NOT NULL` so "all replacement_0_1 orders today" is an indexed scan.
3. **Fill→posterior link**: add `posterior_id INTEGER` to `execution_fact` (`db.py:8018-8054` upsert), populated in the FILL reconciliation path by joining `edli_no_submit_receipts` on `final_intent_id`. Lower-blast-radius alternative: carry `posterior_id` only in the `DecisionProofAccepted` aggregate payload (`_live_decision_audit_payload`), making the live-order aggregate self-contained without touching `execution_fact` schema.
4. **`q_lcb_calibration_source` typed column** on `edli_no_submit_receipts` (M3) — same batch; populate from `receipt.q_lcb_calibration_source` (`reactor.py:173`).

Antibody for the batch: a simulated FSR cycle with the flag enabled produces `edli_no_submit_receipts` and (on real-submit) `execution_fact` rows where `probability_authority='replacement_0_1'` and `posterior_id` is non-NULL and equals the bundle's `posterior_id` — all queryable with NO `JSON_EXTRACT`.

---

## §5 Bundle / settlement-grounding verdict + data-provenance hole

**Verdict: NOT settlement-grounded.** The live `replacement_0_1` q is an unvalidated raw forecast blend — AIFS member prior × Gaussian Open-Meteo IFS9 deterministic anchor (`anchor_weight=0.80`, `anchor_sigma_c=3.00`, `openmeteo_ecmwf_ifs9_aifs_soft_anchor.py:176-212`) — with NO Platt/isotonic/reliability/settlement step anywhere. The materializer itself stamps the row `posterior_authority_status='SHADOW_ONLY'`, `trade_authority_status='SHADOW_ONLY'`, `q_point_json_role='shadow_point_probability_only'`, `q_lcb_json_role='absent_no_calibrated_lcb_available'`, `training_allowed=False` (`replacement_forecast_materializer.py:455-499`). DB confirms 171/171 SHADOW_ONLY with NULL `q_lcb_json`.

**The data-provenance hole (Fitz #4, the canonical failure mode):** the bundle DATA carries `authority=SHADOW`; the CODE overrides it with a feature flag and trades on it as live truth. The reconstructed Wilson-LCB the live path trades on (`_replacement_yes_lcb_for_bin:5418-5424`) measures Monte-Carlo member-count sampling uncertainty on the AIFS PRIOR (before the 0.80 anchor blend) — NOT calibration error vs VERIFIED WU settlement — so its 95% claim is unearned (M1). The DB schema makes `trade_authority_status='LIVE'/'LIVE_AUTHORITY'` literally absent today (bundle reader restricts to SHADOW_*); the code layer is the only place that grants live, via the flag.

**Correct invariant (the antibody, beyond the §1 gate):** live authority requires `trade_authority_status=='LIVE_AUTHORITY'` written ONLY by a settlement-evidence promotion job, AND `q_lcb_json_role=='calibrated'` with non-NULL `q_lcb`. Encode as the `LiveReplacementBundle` newtype (C3) so "consume a SHADOW_ONLY / absent-LCB row on the live path" is **unconstructable**. The materializer already produces the correct shadow stamping — the missing piece is a promotion job that, on passing settlement evidence, re-stamps `LIVE_AUTHORITY` + emits a calibrated LCB. Until then, every live `replacement_0_1` decision is uncalibrated by construction; the §1 gate (which fails today because promotion evidence fails) is the correct interim block.

---

## §6 ALREADY SAFE — do NOT touch (preserve when layering FIX-1/FIX-2b/H1-H4)

These are confirmed correct against primary code; the new gates layer ABOVE them, never replace them.

- **NO-side fail-closed, native-only (no YES→NO complement).** Triple-confirmed: authority builder sets `buy_no` lcb=0.0 / p_value=1.0 / prefilter=False unconditionally (`event_reactor_adapter.py:5491-5503`), independent of `q_yes`; the proof generator's `buy_no` leg is hardcoded q=0/lcb=0; the `conservative_edge>confidence_gap` waiver in `live_admission.py:153-156` cannot fire on this path (q=lcb=0 ⇒ conservative_edge<0). Backed by `QlcbProvenance` frozen type + the complement AST guard. (L2 is a duplicate-mechanism cleanup note, not a correctness gap.)
- **Wilson-LCB fallback is conservative + unit-correct** (`_wilson_lower_bound:5356-5365`, `_replacement_yes_lcb_for_bin:5414-5427`): standard one-sided 95% (z=1.645), `min(q_yes, …)` guarantees LCB ≤ point estimate; pure probability units; `_replacement_bound_to_c:5403-5411` raises on any unit other than C/F; fail-closed to 0.0 on error. (M1 critiques USING it as a calibrated LCB on the live path, not the math.)
- **Exact physical-bin binding** (`_candidate_replacement_bin_id:5368-5400`): requires exactly one physical match (`len(matches)==1`, `abs_tol=1e-9` on C bounds) or raises `BIN_BINDING_MISSING:{condition_id}` at `:5479-5481`. (H4 critiques the hash-mismatch FALLBACK `_topology_core`, a different code path.)
- **After-/future-decision-time rejection + dependency/identity-hash gates** in the bundle reader: future posteriors BLOCKED (`replacement_forecast_bundle_reader.py:443-446`), dependency `source_run_id` match (`:456-457`), posterior/baseline readiness match (`:448-453`), identity/dependency/config-hash presence (`:480-482`). (H3 critiques the MISSING upper age bound, not these.)
- **YES q_lcb capped at q_yes** (`:5417,5424`) — LCB can never exceed the point.
- **Single-owner q_source skip** (`_replacement_primary_authority_already_applied:5227-5228`, gate `:1474`) is CORRECT for direction-proof de-dup (commit `aeff1cd24b`). FIX-1 fixes the side-effect (no backstop) WITHOUT changing the skip.
- **Submit gate structure** (`_submit:915-939`) is intact and correct; the three hotfix pre-venue blocks (below-min BUY `executor.py:1732-1744`; risk-allocator-not-UNKNOWN `executor.py:3172-3183`; reactor lock-retry `reactor.py`) are confined to `executor.py`/`reactor.py`, do NOT touch the EDLI adapter, and cannot halt the mainline. (FIX-2b adds the OperatorArm check at the existing gate; it does not rewrite the gate.)
- **Mainline executor independence**: `run_cycle → cycle_runtime.py:5935-5954 → execute_final_intent (executor.py:1978)` does NOT import/call `event_reactor_adapter`/`OpportunityEventReactor`/`event_bound_live_adapter_from_trade_conn`. `edli_live_order_events=0` ⇒ the 293 live fills are mainline. FIX-2b/FIX-1 must preserve this (EDLI-only edits).

---

## §7 Structural summary (K ≪ N)

The five lenses surfaced ~20 findings; they are symptoms of **4 structural decisions incompletely executed**:

1. **ONE settlement-evidence gate** (C1, C2, C3, M1, §5) — `replacement_live_authority_evidence_gate`, one builder, consulted by the 0.1 path AND the legacy resolver; plus the `LiveReplacementBundle` newtype that makes a shadow bundle on the live path unconstructable.
2. **ONE operator-arm token** (C4) — `OperatorArm` gating every real submit by type, canary included.
3. **ONE direction-law re-assertion at the bind site** (H1) — `ReplacementYesCandidate` newtype, `buy_yes ⟺ bin==argmax(q)` unconstructable otherwise.
4. **ONE freshness + complete-settlement-identity discipline** (H3, H4) — `expires_at` as a hard gate in the bundle reader; `settlement_unit`+`rounding_rule` back in `_topology_core`.

The remaining rows (H2, M2, M3, L1-L3) are traceability/vocabulary hygiene that compose with, but do not block, the four decisions above.
