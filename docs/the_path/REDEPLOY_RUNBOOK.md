# ThePath — Redeploy Runbook

```
Created: 2026-06-07
Last reused or audited: 2026-06-07
Authority basis: docs/the_path/{P1_BRIEF.md, REALIGN_0_1_AUTHORITY.md, QLCB_HONESTY.md,
                 OBSERVE_BASELINE.md, REAUDIT_0_1.md, PR_SPEC.md}; branch thepath/audit-realign
                 HEAD b0d96c8c6f + Activate working-tree edits (day0 lane writes).
Live merge-base: e5e5f022ee.
Verdict: REDEPLOY_READY (mainline byte-identical, antibodies pre-existing-only, dry boot clean).
```

This branch is the complete safe PR (Phase -1 + integration/evidence-gate + q_lcb floor +
P1 instrumentation) plus the Day0-lane activation edits. It is safe to redeploy onto the live
293-order mainline because **the trading decision path is byte-identical** and **every new live
capability is flag-OFF or fail-soft shadow logging**.

---

## 1. Deploy posture — the exact flag state to ship

Ship `config/settings.json` `edli_v1` block AS-IS on this branch. The load-bearing values:

| Flag | Ship value | Why |
|---|---|---|
| `replacement_qlcb_settlement_sigma_floor_enabled` | **false** | NEW shadow flag. OFF ⇒ replacement q_lcb byte-identical to today's raw Wilson. HIGH-RISK to flip (moves live q_lcb DOWN on overconfident bins). |
| `q_lcb_settlement_coverage_gate_enabled` | **false** | Unchanged vs live. Coverage-shrink K3 path stays shadow. |
| `edli_live_operator_authorized` | **false** | Unchanged vs live. Fail-safe: `require_operator_arm` returns `None` ⇒ no-submit adapter. |
| `tiny_live_notional_cap_enabled` | **true** | TIGHTENING vs live (was false). Caps now ENFORCED. Ship ON. |
| `tiny_live_daily_order_cap_enabled` | **true** | TIGHTENING vs live (was false). Ship ON. |
| `edli_emos_sole_calibrator_enabled` | **true** | Unchanged vs live. |
| evidence gate | **active** (no flag — always on) | Denies live 0.1 authority on the on-disk failing `promotion_evidence.json`. |

**Net live-money delta of this deploy = the two tiny-cap flags flipping false→true (a safety
tightening). Nothing in the deploy loosens live behavior.** The new `replacement_qlcb_*` flag is
default-OFF and net-new, so it changes nothing until the operator flips it.

Verify before boot:
```bash
cd /Users/leofitz/zeus   # LIVE checkout, after merging this branch
python3 - <<'PY'
import json
d=json.load(open("config/settings.json"))["edli_v1"]
assert d["replacement_qlcb_settlement_sigma_floor_enabled"] is False
assert d["q_lcb_settlement_coverage_gate_enabled"] is False
assert d["edli_live_operator_authorized"] is False
assert d["tiny_live_notional_cap_enabled"] is True
assert d["tiny_live_daily_order_cap_enabled"] is True
print("FLAG POSTURE OK")
PY
```

---

## 2. The two critical flag-flips that license live edge (operator decisions — do NOT flip at deploy)

Both are gated on **settled-market (VERIFIED WU) evidence**, not on calendar time. Neither should
be flipped at deploy. They are listed in `open_decisions_for_operator`.

### Flip A — `replacement_qlcb_settlement_sigma_floor_enabled: false → true`
- **What it does:** floors each replacement YES q_lcb at the settlement-grounded bin mass
  (only-lowers; never raises). Kills the 0.5–0.7 overconfidence dead band that lost ~100% in
  `OBSERVE_BASELINE.md`.
- **Settlement gate to flip:** default-ON is licensed only after **≥30 settled markets** pass
  per-band coverage validation via the `zeus.replacement_qlcb_shadow` claimed→floored log
  (QLCB_HONESTY.md FIX-C). Shadow-validate the claimed→floored delta before promotion.
- **PRECONDITION CAVEAT (see §4):** the shadow-log that produces this validation data only
  accrues **after the evidence gate passes** (Flip B). Until then, `claimed→floored` rows do not
  accumulate, so this validation cannot complete first. Sequencing: Flip B's evidence must mature
  before Flip A's shadow data can mature.

### Flip B — replacement_0_1 live 0.1 authority (the `0.1` edge)
- **What it does:** grants the replacement forecast live 0.1 probability authority.
- **Settlement gate to flip:** the on-disk `state/replacement_forecast_shadow/promotion_evidence.json`
  must PASS its own gate — i.e. `replacement_live_authority_evidence_gate(promotion, capital)` returns
  `(True, ())`. It currently DENIES with 4 blocking codes (see §4). The gate requires, among others:
  `official_days ≥ 5`, `official_rows ≥ 250`, `q_lcb_coverage ≥ 0.95`, `nested_walk_forward_passed`,
  positive after-cost PnL, same-CLOB replay complete, fee/depth fill evidence, product-specific refit.
- **No flag to "force" this.** The gate is a pure predicate over the on-disk evidence dataclasses;
  it cannot be bypassed by a flag. Authority arrives when the evidence is earned.

---

## 3. Data timeline (~weeks)

| Clock | Starts accruing | Operator precondition |
|---|---|---|
| (a) `obs_available_at` on `day0_nowcast_runs` | On redeploy, once the identity fit is persisted AND the monitor cycle reaches a Day0 position (`hours_remaining ≤ 6`). | **Run the persist script on LIVE (see §4 step 1).** Without the fit the lane short-circuits (`monitor_refresh.py:1767`). |
| (b) EMS depth tap → `market_price_history` 'full' rows | On redeploy, every discovery/scan cycle that scans markets with captured EMS snapshots. No flag, no operator action. | Discovery cycle must run; `executable_market_snapshots` rows must exist (executor already captures them). |
| (c) Day0 lane rows (`day0_nowcast_runs`) | Same as (a) — the fit is the load-bearing activator. | Same as (a). |
| (d) q_lcb shadow-log (claimed→floored) | **Only after the evidence gate passes (Flip B).** Does NOT accrue at deploy. | Promotion+capital evidence must pass the gate first (§2 Flip B). |

Realistic timeline: the obs-timing edge (G-DAY0 in PR_SPEC.md) needs **weeks** of settled Day0
markets carrying honest `obs_available_at` before the ROI re-run is decisive. The replacement
promotion evidence needs ≥5 official days / ≥250 official rows of settled validation.

---

## 4. Operational steps at redeploy

1. **Start the Day0 obs-timing clock (operator must run on LIVE).** The audit could not write to
   LIVE (iron rule). From the LIVE checkout:
   ```bash
   cd /Users/leofitz/zeus
   python3 scripts/persist_day0_horizon_identity_fit.py
   ```
   This persists a CONSERVATIVE identity fit (alpha=1, beta..epsilon=0, `predict_proba(p)==p`,
   ZERO claimed skill — the model asserts the raw input is its own best estimate; n_obs=0). It
   does NOT manufacture skill; it only flips `read_latest_platt_fit()` from None to non-None so the
   lane stops short-circuiting and starts writing rows with honest `obs_available_at`. A proper
   temporal-holdout fit is deferred (see §5) and the script docstring flags the refinement.
   Verify: `read_latest_platt_fit()` returns `hpf_v1_identity_conservative_v1`.

2. **Confirm the evidence gate denies (expected at deploy).** It is healthy for the gate to deny —
   live 0.1 authority must stay OFF until evidence is earned. The dry boot confirmed it denies on
   the live on-disk artifact with codes:
   `REPLACEMENT_PROMOTION_INSUFFICIENT_OFFICIAL_DAYS`, `..._INSUFFICIENT_OFFICIAL_ROWS`,
   `..._Q_LCB_COVERAGE_TOO_LOW`, `..._NESTED_WALK_FORWARD_NOT_PASSED`.

3. **Boot the daemon with the flag posture in §1.** No `edli_live_operator_authorized` ⇒
   no-submit adapter (fail-safe). Caps ON.

---

## 5. Validation re-run command

Antibody suite (run from the LIVE or audit checkout, all green = pre-existing-only failures):
```bash
cd /Users/leofitz/zeus-thepath-audit
PYTHONPATH=$PWD python3 -m pytest \
  tests/test_day0_nowcast_lane_writes.py \
  tests/test_day0_nowcast_obs_available_at.py \
  tests/test_intraday_orderbook_depth_capture.py \
  tests/test_one_minus_value_equivalence.py \
  tests/test_live_buy_no_material_admission.py \
  tests/engine/test_replacement_0_1_authority_evidence_gate.py \
  tests/engine/test_replacement_0_1_qlcb_dispersion_floor.py \
  tests/engine/test_replacement_0_1_qlcb_k3_and_shadowlog.py \
  tests/engine/test_replacement_0_1_live_authority_probability.py \
  tests/money_path/test_operator_arm_gates_edli_submit.py \
  tests/money_path/test_tiny_live_caps_enabled.py \
  -q --no-header
```

G-DAY0 ROI re-run (deferred until obs-timing data accrues — weeks): re-run the Day0 obs-timing
edge under honest queryable-time (`obs_available_at`) + real fills (EMS depth). Per PR_SPEC.md §P1:
**ROI ≤ 0 kills the Day0 profit thesis; keep the Day0 mask as safety only.**

Mainline parity re-check (must print 0):
```bash
cd /Users/leofitz/zeus-thepath-audit
git diff e5e5f022ee..HEAD -- src/execution/executor.py | wc -c   # 0
git diff e5e5f022ee..HEAD -- src/engine/cycle_runtime.py -- ':!*depth*' | wc -c  # see note below
```
NOTE: `cycle_runtime.py` is NOT byte-identical — it carries one additive, fail-soft, flag-free
EMS depth-tap writer queued in `execute_discovery_phase` (the discovery/telemetry path, NOT the
executor decision path). It runs inside the sandboxed `_flush_derived_writes()` try/except and
cannot raise into the cycle; absent EMS ⇒ no-op. executor.py IS byte-identical. See §6.

---

## 6. Deferred items (NOT blocking deploy)

- **`day0_extreme_updated.py` backfill proxy fix (P1_BRIEF §2d).** The backfill plane still uses the
  `MAX(imported_at)` provenance-contaminated proxy at `day0_extreme_updated.py:222`. The honest
  `archive_dissemination_lag` constants (`WU_ICAO ~+35 min`, `ASOS/METAR ~+10 min`) and the
  `rolling_hourly_imported_at` path are vocab-reserved in `day0_nowcast_store.py:202` but the
  per-source lag constants are NOT yet wired into the backfill event builder. Operator decision.
- **Proper temporal-holdout Day0 horizon Platt fit.** The shipped fit is the conservative identity
  (zero skill by design). A real fit needs the P1_BRIEF §4 Step 0 cross-DB reconstruction harness
  (obs-lock from `observation_instants` + running-max + fixed publish lag + daypart + VERIFIED
  settlement join, INV-37). Schedule once enough `day0_nowcast_runs` rows with
  `obs_availability_provenance='live_fetch'` accumulate.
- **C3 / H1–H4 newtypes.** Type-system antibodies (making the wrong code unwritable) deferred to a
  later PR.
- **`captured_at` provenance confirmation (P1_BRIEF §181).** Confirm `executable_market_snapshots.captured_at`
  is a true wall-clock fetch time (not backfill-derived) before trusting the EMS depth-tap
  decision→snapshot gap as honest queryable-time.

---

## 7. Rollback

This deploy is reversible by reverting the merge. No schema migration was performed (all new
columns pre-exist on the deployed tables; the writer stamps `schema_version=4` which every table
variant accepts — see `day0_nowcast_store.py` root-cause note). The Day0 fit can be removed by
deleting the `day0_horizon_platt_fits` row; the lane then short-circuits back to 0 rows.
```
