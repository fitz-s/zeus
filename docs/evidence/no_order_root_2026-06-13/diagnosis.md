# No-order root cause — live diagnosis 2026-06-13 (overturns the K-cut Stage-1 premise)

```
Created: 2026-06-13
Authority basis: live receipts (zeus-world.db no_trade_regret_events, risk_state.db),
  riskguard-live.log, code reading (src/strategy/live_inference/live_admission.py,
  src/engine/event_reactor_adapter.py, src/riskguard/riskguard.py). RULE 1 (trades always
  exist; no-trade = our defect, root-cause on re-probed reality not memory).
Status: DIAGNOSIS. Supersedes the gate-mass framing of melodic-orbiting-pearl.md / the
  K-cut Stage-1 "neutralize capital_efficiency" plan.
```

## Verdict: the no-order incident is TWO independent blockers — NEITHER is the Layer-A gate-mass

The approved K-cut plan (and `docs/operations/kcut_decision_path_collapse_2026-06-13.md`)
held that ~174 Layer-A gates wrongly kill +EV near-center buy_no. **Code reading +
live receipts refute that premise.** The two real blockers:

### Blocker 1 — RiskGuard persistent DATA_DEGRADED (dependency_db_locked)
- `evaluate_riskguard` (money_path_adapters.py:354) passes **iff level is GREEN**.
- `get_current_level` (riskguard.py:1972) returns DATA_DEGRADED when the latest risk_state
  row carries `riskguard_degraded_reason=dependency_db_locked` and no fresh (<5min) full
  risk row exists.
- risk_state: persistently DATA_DEGRADED for ~6h (01:00–06:57Z), reason `dependency_db_locked`,
  error `"database is locked"`, `full_metrics_status=unavailable_no_fresh_full_risk_row`.
  94% GREEN over 4 days but in multi-hour bursts it blocks **every** post-Kelly tradeable
  bet (2113 RISK_GUARD_BLOCKED receipts / 17h).
- **Mechanism (riskguard-live.log + _tick_once):** each tick opens `zeus_conn` =
  `get_trade_connection_with_world_required(write_class="live")` at riskguard.py:1315, THEN
  does ~5s of Polymarket HTTP (balance-allowance + positions, bankroll truth) at 1339, with
  the write-class conn held open across the network I/O. Concurrent reactor writes on the
  16.5GB zeus_trades + 45GB world + 38GB forecasts WAL surface contend → metric read /
  strategy_health write hits "database is locked" → retries exhaust → DATA_DEGRADED.
- **This is a recurrence of the class root-fixed in 9f70e9c581** (LAW: a DB connection /
  write txn is NEVER held across network I/O; short per-pass conns). Fix = restore that law
  for the RiskGuard tick (read DB into memory + close BEFORE the bankroll HTTP, or use a
  read-only WAL snapshot for the metric read which is contention-immune, and make the
  auxiliary strategy_health write non-fatal). **Fail-closed MUST be preserved** — a genuine
  read failure still degrades; never make RiskGuard report GREEN when truth is degraded.
- Honest gate; do NOT weaken the degrade logic — eliminate the LOCK.

### Blocker 2 — honest no-edge / calibration #2 (NOT gate-mass), during GREEN windows
- Clean isolation: in a 4h 100%-GREEN window (2–6h ago) with 0 RISK_GUARD blocks, 202/249
  regret events were `EVENT_BOUND_ALL_CANDIDATES_REJECTED`.
- **`capital_efficiency` is the honest +EV gate, not scar tissue.**
  `live_capital_efficiency_rejection_reason` (live_admission.py:113) fires **iff
  `(q_lcb − price)/price ≤ 0`, i.e. `q_lcb ≤ price`** — conservative EV ≤ 0. It is the
  minimal "don't trade −EV" rule. Neutralizing it (K-cut Stage-1b) would **admit −EV trades.**
- **The +EV-looking candidates are honestly vetoed, not gate-mass victims.** Across 692
  ALL_CANDIDATES families: 461 had a +EV "best" candidate; every one sits in the
  `direction_law` / `coverage_unlicensed_tail` bucket (capital_efficiency CANNOT reject a
  +EV candidate by construction). They are: sub-cent far-tail buy_yes (`price 0.001, ev 38×`
  — direction-illegal longshots; coverage = unlicensed tail) and near-modal buy_no (Milan
  +22% — phantom edge on the forecast bin → direction_law veto). **No admissible
  direction-legal +EV trade is being wrongly blocked.**
- The real lever for real near-center edge is **calibration #2** (flat-σ under-disperses the
  modal bin → phantom buy_no edge that direction-law correctly kills; genuine near-center
  edge stays too weak to clear cost). Fix = C1 era-EB / C3 JS-toward-market (built, gated),
  NOT removing the honest Layer-A gates.

### Plan defect found (trust code over docs)
The architect doc / plan **scrambled the gate→line map**: actual code is 7580
capital_efficiency, 7598 buy_no_conservative_evidence, **7614 direction_law**, 7628
coverage_unlicensed_tail. The plan would have made **7614 = direction law** shadow-diagnostic
(believing it was coverage) — disabling the buy_no-direction safety gate (Paris-wrong-trade
class). Executing the K-cut verbatim would both admit −EV trades AND disable direction law.

### Secondary suspicion — reactor throughput
Current daemon processes ~14 families / 35min vs ~170 historically. Low coverage may hide
markets where a genuine direction-legal +EV exists (RULE 1). Worth a separate probe after
the RiskGuard lock is fixed.

## What to do (honest, no gate weakened)
1. **Fix the RiskGuard dependency-lock storm** (restore the no-conn-across-IO law; read-only
   WAL metric read; resilient auxiliary write; fail-closed preserved). Reliability → GREEN.
2. **DROP the K-cut Layer-A gate removal** — those gates are the honest K, not the disease.
3. **Calibration #2 (C1/C3)** is the path to real direction-legal near-center alpha.
4. Re-measure throughput once GREEN is reliable.
