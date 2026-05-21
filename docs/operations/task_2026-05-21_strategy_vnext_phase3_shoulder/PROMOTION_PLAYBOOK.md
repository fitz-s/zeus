# Shoulder Strategy Promotion Playbook

**Created**: 2026-05-21  
**Authority**: AUTHORITY_GPT_ROUND_1_DOSSIER.md §7.4 (promotion labels) + §12 (promotion gate)  
**Plan**: PHASE_3_SHOULDER_PLAN.md §2 T3 + §3 Cross-Track Invariants

---

## 0. Critical Invariant

**This playbook is READ-ONLY for agents.** No code in Zeus may mutate `live_status` in
`architecture/strategy_profile_registry.yaml` based on automated signals. Promotion is
**operator-gated** — a human must execute the final `live_status` flip after reviewing
the readiness report and signing off on every gate in §3.

---

## 1. Current State (Phase 3 T3)

| Strategy | `live_status` | Notes |
|---|---|---|
| `shoulder_sell` | `shadow` | `kelly_default_multiplier: 0.0` — shadow logging only, no capital deployed |
| `shoulder_buy` | `blocked` | Blocked; no shadow logging |

Phase 3 T3 target state = **same**. No live_status changes at T3 end.

---

## 2. Promotion Label Taxonomy (dossier §7.4)

| Label | Meaning | Registry state |
|---|---|---|
| `SHADOW_FIRST` | Running shadow decisions; accumulating evidence | `live_status: shadow`, `kelly_default_multiplier: 0.0` |
| `UNKNOWN_BUT_INTERESTING` | Research-grade; not yet shadow-eligible | `live_status: shadow` with minimal sample |
| `RESEARCH_ONLY` | Analytical / structural study; no shadow | `live_status: blocked` |
| `IMPLEMENTATION_READY` | Operator has reviewed all gates; eligible for live_status flip | Operator authorization required |

---

## 3. Promotion Gate Checklist (dossier §12)

Before any `shoulder_sell` or `shoulder_buy` live_status flip, ALL gates must pass:

### Gate 1 — Shadow Evidence (INSUFFICIENT_SHADOW → resolved)

```bash
python scripts/shoulder_shadow_readiness_report.py --markdown
```

- [ ] `ReadinessStatus.READY_FOR_OPERATOR_REVIEW` (not INSUFFICIENT_*)
- [ ] `shadow_decision_count >= 100` (MIN_SHADOW_DECISIONS)
- [ ] `stress_coverage_count >= 10` (MIN_STRESS_SCENARIOS)
- [ ] `regime_coverage_count >= 2` (MIN_REGIME_VARIETY)

### Gate 2 — Cluster Cap Health

```bash
python scripts/shoulder_shadow_readiness_report.py --json | python -c "import json,sys; r=json.load(sys.stdin); print(r['exposure_total_usd'])"
```

- [ ] `exposure_total_usd` is within operator-approved limits
- [ ] No active `SHOULDER_CLUSTER_CAP_EXCEEDED` rejections in recent no_trade_events

### Gate 3 — Stress Test Coverage (dossier §7.5)

Six stress scenarios must be represented in `tail_stress_scenarios`:

- [ ] `FORECAST_PLUS_2SIGMA` — +2σ forecast error
- [ ] `STATION_ANOMALY` — station anomaly scenario
- [ ] `LATE_DAY_ADVECTION` — late-day advection shift
- [ ] `SOURCE_REVISION` — source revision scenario
- [ ] `MODEL_TAIL_UNDERDISPERSION` — model tail underdispersion
- [ ] `CORRELATED_CITY_CRASH` — correlated city crash

### Gate 4 — Cluster Correlation Safety (dossier §7.5)

- [ ] No same-direction shoulder sell from multiple cities under one heat dome in current ledger
- [ ] `shoulder_exposure_ledger` shows no cross-city cluster violations

### Gate 5 — Day0 Bound Verification (dossier §7.6)

> "Shoulder strategy becomes safer only when Day0 bound has eliminated tail."

- [ ] `Day0BoundState` 6-class upgrade (Phase 5/6) is live
- [ ] `SHOULDER_DAY0_BOUND_NOT_ELIMINATED` no-trade gate has fired and been reviewed

*Note: test `test_inv_shoulder_safer_after_day0_bound` is marked `xfail` until Phase 5/6.*

### Gate 6 — Operator Sign-Off

- [ ] Operator has read this playbook and the readiness report
- [ ] Operator has reviewed the 5 most recent shadow decisions manually
- [ ] Operator approves `live_status` flip via explicit registry YAML edit

---

## 4. Performing the Flip (operator only)

After ALL gates in §3 pass:

1. Edit `architecture/strategy_profile_registry.yaml`:
   - `shoulder_sell`: change `live_status: shadow` → `live_status: live` AND set `kelly_default_multiplier` to the approved value (e.g. 0.05–0.20 per dossier §7.5 Kelly haircut range)
   - `shoulder_buy`: change `live_status: blocked` → `live_status: shadow` (first step only; live requires separate gate)

2. Commit with message: `feat(shoulder): promote shoulder_sell to live — readiness_status=READY_FOR_OPERATOR_REVIEW sha=<commit>`

3. Tag the commit: `git tag shoulder-live-promote-$(date +%Y%m%d)`

4. Restart Zeus daemon to pick up registry change.

5. Monitor first 5 live shoulder decisions manually.

---

## 5. Rollback

If any live shoulder decision produces an unexpected result:

```bash
# EH-2: disable VNEXT + force shadow/blocked
export ZEUS_SHOULDER_VNEXT_ENABLED=0
# EH-3: per-track schema rollback (SCAFFOLD — run() guard must be removed first)
python scripts/rollback_phase3_t3.py --dry-run  # review first
# Remove the NotImplementedError guard in run()/main(), then:
python scripts/rollback_phase3_t3.py           # execute after review
```

Restore `architecture/strategy_profile_registry.yaml`:
- `shoulder_sell`: `live_status: shadow`, `kelly_default_multiplier: 0.0`
- `shoulder_buy`: `live_status: blocked`

---

## 6. Regime Taxonomy Extension

The T1 6-member `WeatherRegimeTag` enum (`HEAT_DOME / COLD_SNAP / NORMAL / SHOULDER_SEASON / SOURCE_ANOMALY / UNKNOWN`) is MINIMAL per plan §2 T1 non-goals. Operator may extend by:

1. Adding new members to `src/contracts/weather_regime_tag.py`
2. Updating `src/strategy/correlation_cluster.py` zone map if new region is needed
3. Updating `src/contracts/weather_regime_tag.py` classifier rule in `regime_tag_for()`
4. Re-running stress tests with new regime tags

No `live_status` mutation is required for regime extension.

---

*This playbook is operator-gated. Agents: do NOT automate live_status flips.*
