# Math Audit — 2026-04-06

Auditor: math-auditor (Opus 4.6 1M)
Scope: All math-related [OPEN] gaps in `workspace-venus/memory/known_gaps.md`
Method: codebase read + grep + 104 math tests (all passing)

---

## Test Suite Results

```
tests/test_market_analysis.py       17 passed
tests/test_kelly.py                 16 passed
tests/test_kelly_cascade_bounds.py  29 passed
tests/test_alpha_target_coherence.py 27 passed
tests/test_entry_exit_symmetry.py   15 passed
TOTAL: 104 passed, 0 failed
```

---

## Gap 1: MODEL_DIVERGENCE_PANIC threshold 0.15 (line 66)

**Current code state:**
- `config/settings.json:199-202`: `divergence_soft_threshold = 0.20`, `divergence_hard_threshold = 0.30`
- `src/state/portfolio.py:1397-1419`: Both thresholds are loaded via `ExpiringAssumption` from `settings["exit"]` with fallbacks matching the config values.
- `src/execution/exit_triggers.py:83-102`: Two-tier divergence system:
  - Hard threshold (0.30): immediate exit, no velocity confirmation needed.
  - Soft threshold (0.20): requires adverse market velocity <= -0.05/hr to confirm.
- The old 0.15 value is **gone**. The thresholds were raised to 0.20/0.30 as proposed.
- Both are fully configurable via `config/settings.json`, wrapped in `ExpiringAssumption` with provenance in `config/provenance_registry.yaml`.

**Verdict: FIXED**

**Evidence:** `config/settings.json:199` shows `0.2`, line 201 shows `0.3`. Code at `exit_triggers.py:83-102` uses `divergence_hard_threshold()` and `divergence_soft_threshold()` which read from config. The two-tier system with velocity confirmation on soft threshold addresses the "false exit" problem from the gap description.

---

## Gap 2: alpha_overrides only London verified profitable (line 115)

**Current code state:**
- `src/strategy/market_fusion.py:80-96`: `compute_alpha()` documents alpha_overrides as **DEPRECATED**. Comments state: "D1 analysis showed MAE->alpha mapping has r=+0.032 (no signal). Override lookup kept for manual experimentation but table is empty and no longer auto-populated by the weekly cycle."
- `src/strategy/market_fusion.py:154-194`: `_get_alpha_override()` still queries the `alpha_overrides` table, but the table has 0 rows per code comments (line 95: "alpha_overrides has 0 rows").
- The system now uses per-decision adjustments (ensemble spread, model agreement, lead days, hours_since_open) instead of per-city overrides.

**Verdict: STILL OPEN (but risk is mitigated)**

The gap is technically still open because the `alpha_overrides` table and lookup code still exist. However, the practical risk is near-zero: the table is empty, not auto-populated, and the code path returns `None` (falling through to `BASE_ALPHA_BY_LEVEL`). The gap should remain open as a reminder to either delete the dead code or add validation if the table is ever re-populated.

---

## Gap 3: Harvester bias correction not synced (line 119)

**Current code state:**
- `config/settings.json:233`: `bias_correction_enabled: false` -- bias correction is globally disabled.
- `src/signal/ensemble_signal.py:135-142`: `EnsembleSignal.__init__` checks `settings._data.get("bias_correction_enabled", False)` and only applies bias correction if true. Currently off.
- `src/execution/harvester.py:526-563`: `harvest_settlement()` takes `p_raw_vector` from snapshot context (decision-time stored values), not from fresh ENS fetch. It does not re-run ensemble signal or apply bias correction independently.
- `src/engine/evaluator.py:618`: Evaluator records `bias_corrected=bool(getattr(ens, "bias_corrected", False))` on the MarketAnalysis object.

**Verdict: STILL OPEN**

While bias correction is currently disabled globally (so there is no active mismatch), the gap remains structurally present. If `bias_correction_enabled` is set to `true`, the evaluator would apply bias correction to p_raw before storing snapshots, and the harvester would consume those corrected p_raw values from snapshots. However, there is no explicit flag on calibration pairs recording whether bias correction was applied. The proposed antibody (recording `bias_corrected: bool` on calibration pairs) has NOT been implemented.

---

## Gap 4: Exit authority incomplete context (line 211)

**Current code state:**
- `src/state/portfolio.py:92-106`: `ExitContext.missing_authority_fields()` checks `fresh_prob`, `fresh_prob_is_fresh`, `current_market_price`, `current_market_price_is_fresh`, `hours_to_settlement`, `position_state`. Returns list of missing fields.
- `src/state/portfolio.py:275-280`: When missing fields exist, `evaluate_exit()` returns `ExitDecision(False, "INCOMPLETE_EXIT_CONTEXT (missing=...)")` -- it does NOT hard-fail; it returns a no-exit decision.
- `src/engine/cycle_runtime.py:523-529`: The monitoring phase logs `INCOMPLETE_EXIT_CONTEXT` as a warning and increments a summary counter, but **continues the cycle** without acting on exit.
- `fresh_prob_is_fresh` is set in `monitor_refresh.py` via `_set_monitor_probability_fresh()`. If the ENS refresh or Day0 refresh fails, it is set to `False`, which causes the incomplete context warning.

**Verdict: STILL OPEN**

The system degrades gracefully (no hard-fail, no crash), but the proposed antibody (making `fresh_prob_is_fresh` a required field and skipping exit authority until the next full refresh) has NOT been fully implemented. The exit authority still *runs* with incomplete context and returns a no-exit decision, meaning positions with stale freshness data are silently held through potentially adverse moves. The log evidence cited in the gap (4 positions repeatedly triggering this warning) confirms this is still actively occurring.

---

## Gap 5: Entry sizing too permissive on weak CI (line 227)

**Current code state:**
- `src/engine/evaluator.py:761-766`: `dynamic_kelly_mult()` scales Kelly multiplier by `ci_width`, `lead_days`, and `portfolio_heat`, but there is **no hard gate** for degenerate CI (ci_lower == ci_upper == 0).
- `src/engine/evaluator.py:806-807`: `kelly_size()` is called with the throttled multiplier. If `ci_width = 0`, `dynamic_kelly_mult` would produce a higher multiplier (tighter CI = more confidence), which is exactly backwards for degenerate CI.
- No grep match found for `ci_lower == ci_upper == 0`, `fill_quality == 0`, or `degenerate.*ci` in the evaluator or monitor_refresh.

**Verdict: STILL OPEN**

There is no rejection gate for degenerate CI. A reconstructed day0 entry with `ci_lower == ci_upper == 0` and `fill_quality = 0` would pass through sizing with a higher Kelly multiplier (since `ci_width = 0` is treated as "very tight CI" rather than "no CI data"). The proposed antibody (hard size cap or rejection gate for degenerate CI) has NOT been implemented.

---

## Gap 6: Positions without monitor chain (line 233)

**Current code state:**
- `src/engine/cycle_runtime.py:402-571`: The monitoring phase iterates all positions and skips those in `pending_tracked`, quarantined, admin_closed, economically_closed, or pending_exit states. For active positions, it calls `refresh_position()` and then `evaluate_exit()`.
- No grep match for `monitor_chain_missing` anywhere in the codebase.
- A position could reach settlement without ever being monitored if:
  1. It enters and the next cycle's monitor phase fails (exception at line 568).
  2. The position transitions to settlement before the next successful monitor cycle.
  3. The harvester settles it without checking for prior monitor chain events.

**Verdict: STILL OPEN**

There is no explicit check for `monitor_chain_missing`. The proposed antibody (requiring at least one post-entry monitor scan before settlement) has NOT been implemented. The harvester (`_settle_positions`) settles any matching position regardless of whether it was ever monitored.

---

## Gap 7: Exit hard-fails on missing best_bid (line 239)

**Current code state:**
- `src/state/portfolio.py:452-460`: `_buy_yes_exit()` returns `ExitDecision(False, "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)")` when `best_bid is None`. This is a no-exit decision, not a hard-fail.
- `src/engine/cycle_runtime.py:320-322`: In paper mode, `best_bid` is synthesized from `p_market` if the live bid is unavailable. This means paper mode has a degraded fallback.
- For live mode, if CLOB bid/ask fetch fails, `best_bid` stays `None` and buy_yes positions cannot exit via EDGE_REVERSAL.
- Buy_no exit path in `src/state/portfolio.py:525-613` does NOT require `best_bid` -- it uses `current_market_price` for the EV gate instead.

**Verdict: STILL OPEN (partially mitigated in paper mode)**

Paper mode has a fallback (`best_bid = p_market`). Live mode has no degraded fallback for buy_yes exits when the CLOB bid is missing. The proposed antibody (mid/last-trade/conservative proxy fallback) has NOT been implemented for live mode.

---

## Gap 8: EDGE_REVERSAL too conservative (line 245)

**Current code state:**
- `src/state/portfolio.py:1374,1452`: `consecutive_confirmations()` reads from `settings["exit"]["consecutive_confirmations"]`.
- `config/settings.json:187`: `"consecutive_confirmations": 2` -- requires 2 consecutive negative cycles.
- `src/execution/exit_triggers.py:83-102`: Hard divergence kill-switch exists at `divergence_hard_threshold() = 0.30` which fires immediately (single-shot, no confirmation needed).
- `src/execution/exit_triggers.py:90-102`: Soft divergence at 0.20 with adverse velocity confirmation is also single-shot.
- However, the EDGE_REVERSAL path itself still requires 2 confirmations (line 160-161 in exit_triggers.py, line 496 in portfolio.py).

**Verdict: PARTIALLY FIXED**

The hard divergence kill-switch (single-shot on extreme divergence at 0.30) was added as proposed. But for moderate cases between 0.0 and 0.20 divergence, EDGE_REVERSAL still requires 2 consecutive confirmations. The gap description asked for "a separate hard divergence kill-switch for extreme cases" which now exists. The conservative reversal path for moderate cases remains unchanged by design.

---

## Gap 9: Settlement win/lose semantics ambiguous (line 251)

**Current code state:**
- `src/state/db.py:28-39`: Contract version upgraded to `position_settled.v2`. Settlement detail fields now include:
  - `won` -- legacy boolean, marked "ambiguous, do not use in new code"
  - `market_bin_won` -- True iff position's bin matched the winning bin
  - `position_profitable` -- True iff realized PnL > 0
- `src/state/db.py:1739-1747`: `_canonical_position_settled_payload()` populates all three fields:
  ```python
  "won": bool(won),                              # legacy
  "market_bin_won": bool(won),                   # v2+
  "position_profitable": pnl is not None and pnl > 0,  # v2+
  ```

**Verdict: FIXED**

The field has been split into explicit semantic names (`market_bin_won`, `position_profitable`) as proposed. The legacy `won` field is retained for backward compatibility but marked as deprecated. The `direction_correct` field mentioned in the proposal was not added, but the two fields that were added resolve the core ambiguity (bin-won vs profitable).

---

## Gap 10: Harvester Stage-2 bootstrap vs canonical DB (line 263)

**Current code state:**
- `src/execution/harvester.py:57-65`: `_has_canonical_position_history()` checks if a position has canonical event history. If not, it skips the dual-write path.
- `src/execution/harvester.py:75-110`: `_dual_write_canonical_settlement_if_available()` explicitly checks for canonical history before attempting the dual write. If the helper fails, it raises `RuntimeError` with a descriptive message.
- `src/execution/harvester.py:148-164`: The harvester now resolves snapshot contexts via `_snapshot_contexts_for_market()`, which checks authoritative settlement rows first, then legacy rows, then falls back to open portfolio snapshots.
- Line 163-164: Learning contexts explicitly exclude `working_state_fallback` authority level.

**Verdict: STILL OPEN**

The harvester has improved its snapshot resolution logic, but the core problem persists: when canonical DB schema helpers fail on bootstrapped databases, the result is `pairs_created=0` despite `settlements_found=141`. The Stage-2 bootstrap path is still being exercised but producing zero output. The proposed antibody (explicit runtime-DB shape gate before Stage-2 work) has NOT been implemented.

---

## Gap 11: LA Gamma markets mislabeled Milan (line 269)

**Current code state:**
- `src/data/market_scanner.py:194-211`: `_match_city()` iterates all cities and checks aliases and slug_names against the event title/slug text.
- `config/cities.json:321-342`: Los Angeles has aliases `["Los Angeles", "LA", "los angeles"]` and slug_names `["los-angeles"]`.
- There is NO sanity check for LA vs Milan. The matching is first-match-wins: whichever city's alias matches first in the iteration order gets the event.
- If Milan has an alias that matches a substring in an LA event title (or vice versa), the wrong city could be selected.

**Verdict: STILL OPEN**

No city-to-market sanity check exists. The matching algorithm is purely substring-based with no disambiguation for ambiguous cases. The proposed antibody (explicit city-to-market sanity check, fail-closed on mismatch) has NOT been implemented.

---

## Gap 12: solar_daily schema corruption (line 205)

**Current code state:**
- The gap is tagged `[STALE-UNVERIFIED]` in known_gaps.md, not `[OPEN]`.
- Latest cycles completed without the error appearing. The gap notes it may have been intermittent or masked.

**Verdict: STALE-UNVERIFIED (no change)**

Cannot verify without a deliberate `day0_capture` run. The gap was already marked as stale-unverified.

---

## Gap 13: Open-Meteo quota contention (line 124)

**Current code state:**
- `src/data/openmeteo_quota.py`: Process-local `OpenMeteoQuotaTracker` with daily limit of 10,000 calls, 80% warning threshold, 95% hard block. Handles 429 with cooldown.
- The tracker is **process-local** (line 103: `quota_tracker = OpenMeteoQuotaTracker()`). Each Python process has its own counter. There is NO shared state file, no IPC, no workspace-wide coordination.
- The gap is tagged `[STALE-UNVERIFIED]` in known_gaps.md.

**Verdict: STILL OPEN (structurally)**

Quota tracking is process-local only. If multiple workspace agents (Zeus, data ingestion agents, Rainstorm legacy) call Open-Meteo concurrently, they each track their own count independently with no coordination. The proposed antibody (workspace-wide quota coordination) has NOT been implemented.

---

## Summary Table

| # | Gap | Verdict | Action Needed |
|---|-----|---------|---------------|
| 1 | MODEL_DIVERGENCE_PANIC 0.15 | **FIXED** | Update known_gaps.md |
| 2 | alpha_overrides only London | STILL OPEN (mitigated) | Keep open |
| 3 | Harvester bias correction | STILL OPEN | Keep open |
| 4 | Exit authority incomplete context | STILL OPEN | Keep open |
| 5 | Entry sizing weak CI | STILL OPEN | Keep open |
| 6 | Positions without monitor chain | STILL OPEN | Keep open |
| 7 | Exit hard-fails missing best_bid | STILL OPEN (paper mitigated) | Keep open |
| 8 | EDGE_REVERSAL too conservative | **PARTIALLY FIXED** | Update known_gaps.md |
| 9 | Settlement win/lose semantics | **FIXED** | Update known_gaps.md |
| 10 | Harvester Stage-2 bootstrap | STILL OPEN | Keep open |
| 11 | LA Gamma mislabeled Milan | STILL OPEN | Keep open |
| 12 | solar_daily schema corruption | STALE-UNVERIFIED | No change |
| 13 | Open-Meteo quota contention | STILL OPEN (structurally) | No change |

---

## Proposed known_gaps.md Edits

### Gap 1: MODEL_DIVERGENCE_PANIC -- change [OPEN] to [FIXED]

Replace line 66:
```
### [OPEN] MODEL_DIVERGENCE_PANIC threshold 0.15 太激进
```
With:
```
### [FIXED] MODEL_DIVERGENCE_PANIC threshold upgraded to two-tier 0.20/0.30 system (2026-04-06, math-audit)
```
Add after line 76:
```
**Antibody deployed:** Divergence threshold raised from 0.15 to two-tier system: soft=0.20 (requires adverse velocity confirmation at -0.05/hr) and hard=0.30 (immediate exit). Both configurable via `config/settings.json`, wrapped in `ExpiringAssumption` with provenance registry entries. `src/execution/exit_triggers.py:83-102`.
```

### Gap 8: EDGE_REVERSAL -- change [P1][OPEN] to [P1][PARTIALLY FIXED]

Replace line 246:
```
### [P1][OPEN] EDGE_REVERSAL is conservative enough to miss fast divergence
```
With:
```
### [P1][PARTIALLY FIXED] EDGE_REVERSAL hard divergence kill-switch added (2026-04-06, math-audit)
```
Add after line 250:
```
**Partial antibody deployed:** Hard divergence kill-switch at 0.30 fires single-shot without confirmation. Soft divergence at 0.20 with adverse velocity confirmation also fires single-shot. Standard EDGE_REVERSAL still requires 2 consecutive confirmations for moderate cases (by design). The extreme-case kill-switch proposed in the gap now exists.
```

### Gap 9: Settlement semantics -- change [P1][OPEN] to [P1][FIXED]

Replace line 252:
```
### [P1][OPEN] Settlement and win/lose semantics are ambiguous in audit records
```
With:
```
### [P1][FIXED] Settlement semantics split into market_bin_won + position_profitable (2026-04-06, math-audit)
```
Add after line 256:
```
**Antibody deployed:** `position_settled.v2` contract in `src/state/db.py:28-39` adds explicit `market_bin_won` (bin matched winning bin) and `position_profitable` (PnL > 0) fields. Legacy `won` field retained for backward compatibility, marked deprecated. `_canonical_position_settled_payload()` populates all three fields.
```
