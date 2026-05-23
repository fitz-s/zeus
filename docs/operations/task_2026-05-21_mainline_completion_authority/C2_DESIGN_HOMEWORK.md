# C-2 Design Homework: IOC settlement_day=0.5 Provenance Verdict

**Investigator:** Executor (2026-05-22)
**Question:** Is `imminent_open_capture.kelly_phase_overrides[settlement_day]=0.5`
(+ `allowed_market_phases` including `settlement_day`) CORRECT design, or a
PRE-EXISTING wrong design / bug? Which verdict governs how C-2 finishes?

---

## Q1: Does IOC's design intent imply it SHOULD trade at settlement_day?

**Intent (directive §9):** IOC = 0–24h-to-resolution posterior-collapse strategy.
Edge source = ENS ensemble probability vs market price in the window where
`σ²(τ) ↓ 0 as τ ↓ 0`. Markets in this window are, by definition, on their
*settlement_day* — their hours_to_resolution < 24h. The cycle uses a pure UTC
time filter (not city-local DAY0_CAPTURE gating) precisely to catch markets that
are already on settlement_day but not yet routed to `settlement_capture`.

**Conclusion:** IOC's thesis *requires* `settlement_day` to be in
`allowed_market_phases`. A strategy with 0–24h scope that cannot fire on
settlement_day has no markets to trade. The 0.5 Kelly override is also
consistent with the "conservative sizing" language in the thesis and the
`kelly_default_multiplier: 0.5` default — it is not a relaxation from a
stricter baseline.

---

## Q2: Under ZEUS_MARKET_PHASE_DISPATCH=1 (live default), is the IOC settlement_day=0.5 override ever reachable?

**Code path (evaluator.py:2233–2244):**
```python
def _strategy_key_for(candidate, edge):
    from src.engine.dispatch import is_settlement_day_dispatch
    if is_settlement_day_dispatch(candidate):        # dispatch.py:216
        ...
        return "settlement_capture"   # or "day0_nowcast_entry"
    if candidate.discovery_mode == DiscoveryMode.OPENING_HUNT.value:
        return "opening_inertia"
    if candidate.discovery_mode == DiscoveryMode.IMMINENT_OPEN_CAPTURE.value:
        return "imminent_open_capture"   # <-- after C-2 fix
```

`is_settlement_day_dispatch` returns `market_phase == "settlement_day"` when
`ZEUS_MARKET_PHASE_DISPATCH=1` (dispatch.py:122–216). So when the flag is ON,
**any candidate with `market_phase==settlement_day` is intercepted at line 2236
and returned as `settlement_capture` (or `day0_nowcast_entry`) before the IOC
branch is reached**. The IOC branch executes ONLY for candidates that are NOT
on settlement_day, i.e., `market_phase==pre_settlement_day`.

**Under flag OFF (emergency kill-switch `ZEUS_MARKET_PHASE_DISPATCH=0`):**
`is_settlement_day_dispatch` falls back to the legacy `DiscoveryMode.DAY0_CAPTURE`
check (dispatch.py:232–233). IOC candidates have `discovery_mode=IMMINENT_OPEN_CAPTURE`,
so they are NOT intercepted even on settlement_day — the IOC branch fires, yields
`strategy_key="imminent_open_capture"`, and `kelly_for_phase("settlement_day")`
returns 0.5. This is the only code path where the override is reachable.

**Verdict on reachability:** The `settlement_day=0.5` override is a **deliberate
flag-OFF fallback**, not dead code. Under live default it is unreachable (fully
shadowed by settlement_capture dispatch). Under emergency revert it activates and
allows IOC to size at 0.5 Kelly on settlement_day — consistent with the IOC thesis
(0–24h window trades ARE settlement_day trades).

---

## Q3: git blame / git log — when was the override added, by what commit, deliberate or copy-paste?

**Commit:** `f83db10008` — *feat(cycle): add IMMINENT_OPEN_CAPTURE mode for D+1 / re-opened markets (#205)*
**Date:** 2026-05-19 11:07 PDT
**Author:** Fitz (operator)

The `imminent_open_capture` profile was created from scratch in this commit
(not copied from `settlement_capture` or any legacy profile). The commit diff
shows `+settlement_day: 0.5` alongside `+allowed_market_phases: [pre_settlement_day, settlement_day]`
and `+kelly_default_multiplier: 0.5`. The commit message explicitly states
"kelly=0.5, both market phases" as a deliberate design choice:

> *"Approved operator urgency: Polymarket May 20-21 re-opens (2026-05-19)."*
> *"alpha window is narrow so sizing is conservative"*

The C-2 bug commit (`7e4ca31386`, 2026-05-22) changed only `_strategy_key_for` /
`_strategy_key_for_hypothesis` in `evaluator.py` — routing IMMINENT_OPEN_CAPTURE
from `"opening_inertia"` → `"imminent_open_capture"`. The registry YAML was
**not touched** in the C-2 fix; the `settlement_day=0.5` override has been in
the registry unchanged since `f83db10008`.

No evidence of copy-paste error: `opening_inertia` sets `settlement_day: 0.0`
(alpha decayed by then). IOC deliberately sets `settlement_day: 0.5` because
its 0–24h window is on settlement_day by definition.

---

## Q4: Does IOC also being allowed at settlement_day represent intended overlap or design collision with settlement_capture?

`settlement_capture` owns `settlement_day` under the live-default flag ON — its
profile has `allowed_market_phases: [settlement_day]` only, and the dispatch
interceptor routes every `market_phase==settlement_day` candidate there first
(evaluator.py:2236–2240).

The IOC `allowed_market_phases: [pre_settlement_day, settlement_day]` is **not a
design collision** — it is the flag-OFF safety net. The two strategies are
**mutually exclusive under flag ON**:
- Flag ON → dispatch.py intercepts settlement_day → settlement_capture
- Flag ON → IOC branch fires only for pre_settlement_day candidates

Under flag OFF the overlap activates: IOC would size at 0.5 (vs settlement_capture's
1.0). This is intentional — settlement_capture's observation-locked arbitrage is
not available in flag-OFF emergency revert context where the full phase-dispatch
machinery is disabled, so a conservative 0.5 IOC fallback is the correct behavior.

---

## Summary Table

| Question | Evidence | Finding |
|---|---|---|
| Should IOC trade at settlement_day by design? | §9 thesis: 0–24h window = settlement_day by definition | YES — required |
| Is settlement_day=0.5 reachable under live default? | dispatch.py:216 intercepts before IOC branch | NO (unreachable under flag ON) |
| Is it dead config? | dispatch.py:122–133 flag-OFF kill-switch documented | NO — deliberate flag-OFF fallback |
| Who added it and when? | commit f83db10008 (2026-05-19), operator urgency for May 20-21 re-opens | Deliberate original design |
| Collision with settlement_capture? | mutually exclusive under flag ON; flag-OFF fallback under flag OFF | Intended overlap, not collision |

---

## VERDICT: CORRECT_DESIGN

The `kelly_phase_overrides[settlement_day]=0.5` override and
`allowed_market_phases=[pre_settlement_day, settlement_day]` on
`imminent_open_capture` are **correct original design**, added deliberately in
commit `f83db10008` (2026-05-19) for the Polymarket May 20-21 re-open urgency.
The override is unreachable under the live-default flag ON (settlement_capture
dispatch intercepts first) and functions as a documented flag-OFF fallback
consistent with IOC's 0–24h thesis. C-2 fix path **A (Accept)** applies:
the 0.5 override reveals intended IOC sizing that the pre-C-2 wrong mapping
(`"opening_inertia"`) had suppressed. No registry correction is needed.
