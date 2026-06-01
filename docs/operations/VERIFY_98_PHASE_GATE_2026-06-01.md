# VERIFY #98 — forecast_only phase-gate (commit 57e2114f02) — READ-ONLY VERDICT

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: independent read-only verification (critic agent). Originals:
  src/engine/event_reactor_adapter.py, src/strategy/market_phase.py,
  src/strategy/market_phase_evidence.py, DAY0_OBSERVATION_WRONGSIDE_ROOT /
  DAY0_PHASE_GATE_IMPL / DESIGN_CRITIC (2026-06-01), live state/zeus-world.db.
- Verdict derived from originals + live DB simulation. NO code changed, NO git.

## VERDICT: **CORRECT** (with 2 non-blocking observability caveats)

The gate is a category-killer that correctly excludes same-day already-realizing/
closed markets from forecast_only while preserving legitimate forward edge. Safety
property (no wrong-side same-day admission) is sound and bypass-free. Two caveats are
observability-only (the trade is still blocked either way); neither blocks the fix.

---

## PER-ITEM FINDINGS

### 1. CORRECTNESS OF THE RULE (admit ONLY PRE_SETTLEMENT_DAY) — **OK**

The rule is correct, NOT over-exclusion, and the SETTLEMENT_DAY hardening (critic
MAJOR-4) is right, not too aggressive.

- Phase boundary authority: `settlement_day_entry_utc` = **city-local 00:00 of
  target_date** (`market_phase.py:120-125`). PRE_SETTLEMENT_DAY ends the instant the
  target *local calendar day* begins — which is exactly when the daily extremum
  (max/min for that local day) starts realizing as observable temperature.
- forecast_only is structurally blind to observation (root doc §2; the day0
  absorbing mask fires ONLY for `DAY0_EXTREME_UPDATED`, gated OFF —
  `event_reactor_adapter.py:2966/2975/3331`; `main.py` `_assert_edli_live_scope`
  hard-raises if the day0 flag is on). Trading any part of the local target day
  without observation is wrong-side risk.
- **Far-east timing (the brief's concern #1), quantified for target 2026-06-02:**
  PRE_SETTLEMENT_DAY ends at Tokyo/Seoul `2026-06-01T15:00Z`, Shanghai/Shenzhen/Wuhan
  `16:00Z`, Wellington `12:00Z`, vs F1 Polymarket close `2026-06-02T12:00Z`. So a
  ~20-21h window exists where the market is still OPEN on Polymarket but the gate
  rejects it as SETTLEMENT_DAY. **This is CORRECT, not over-exclusion:** for Tokyo
  `high`, 12:00Z close = 21:00 local on the target day — by then the afternoon peak
  (~14:00 local = ~05:00Z) is already observed. The gate refuses to let forecast_only
  trade a window in which the extremum is realizing unseen. The IMPL doc (line 83-84)
  explicitly owns this as intended.
- MAJOR-4 hardening correct: under the design's looser "admit SETTLEMENT_DAY", a
  `low` realized overnight (local day begun, pre-12:00Z) decided at 10:00Z would be
  admitted blind. Admitting ONLY PRE_SETTLEMENT_DAY makes the already-observed-extremum
  category *unconstructable* (resolves MAJOR-4 by construction, not patch). Cost:
  forgoes early-local-day forecast edge in far-east cities — acceptable, since that
  edge belongs to the disjoint day0 observation-aware scope.
- Not RESOLVED-too-aggressive: rejecting POST_TRADING/RESOLVED is obviously correct
  (market closed / settled).

Residual (design-acknowledged, not a defect): forecast_only permanently forgoes the
pre-extremum-realization slice of same-local-day edge for ALL cities. For far-east
cities this slice is larger in UTC terms. The correct place to recover it is the day0
scope, never forecast_only.

### 2. BLAST ON THE LIVE POOL — **OK** (simulated; gate NOT yet live — see caveat A)

Simulated the gate against the actual last-1h receipt pool (`edli_no_submit_receipts`,
decision_time 2026-06-01T17:08-18:08Z) using the real phase machinery + city configs:

- **All 12 same-day (target_date==2026-06-01) families → POST_TRADING → REJECT.**
  Paris/Seoul/Tokyo/Shanghai/Shenzhen/Wuhan/Tel Aviv/Toronto/London/Wellington/Warsaw/
  Sao Paulo — every wrong-side same-day buy_no candidate dies. The verified-wrong Paris
  buy_no (q_NO=0.997 on observed low=14C) is killed. ✓
- **Future-date forward trades ADMITTED:** ALL 2026-06-03 families →
  PRE_SETTLEMENT_DAY → ADMIT, including **Shanghai 2026-06-03** (the operator's
  future-date near-sure-win) and every other far-east city for 06-03. The good
  forward trades survive. ✓
- **06-02 split (the only nuance):** western 06-02 (NYC/Sao Paulo/Tel Aviv/Toronto/
  Warsaw) → PRE_SETTLEMENT_DAY → ADMIT; far-east 06-02 (Seoul/Shanghai/Shenzhen/
  Taipei/Tokyo/Wuhan/Wellington) → SETTLEMENT_DAY → REJECT (their 06-02 local day has
  already begun at 18:08Z 06-01). This is the item-1 intended consequence, not a bug.

Net: kills wrong-side without killing the good forward trades. ✓

### 3. UNDER-FIRE / BYPASS PATHS — **OK** (no bypass)

- **Both submit adapters funnel through the single gated function.**
  `event_bound_no_submit_adapter` (:245) and `event_bound_live_adapter` (:285) both
  call `build_event_bound_no_submit_receipt`; the live adapter returns immediately
  when `proof_accepted is not True` (:295-296), so a phase-closed receipt
  short-circuits BEFORE any certificate/executor build. No POST_TRADING family can
  score or submit.
- **Continuous re-decision RE-HITS the gate (IMPL doc step-4 confirmed).** The live
  re-decision path (`main.py:3415-3427`) calls `_edli_emit_forecast_snapshot_events`
  → `ForecastSnapshotReadyTrigger.scan_committed_snapshots`, which re-emits
  **FORECAST_SNAPSHOT_READY** events (distinct source to dodge dedup), NOT a separate
  type. They carry `event_type=="FORECAST_SNAPSHOT_READY"` → re-enter the gate → same
  reject. No re-fire of a closed family.
- **`EDLI_REDECISION_PENDING` is NOT a bypass.** `src/events/continuous_redecision.py`
  (`REDECISION_EVENT_TYPE="EDLI_REDECISION_PENDING"`) is a separate, un-wired surface
  (not invoked by the live emit). Even if routed, `edli_source_truth_gate`
  (`event_reactor_adapter.py:141-163`) fail-closes any event_type ∉
  {FORECAST_SNAPSHOT_READY, DAY0_EXTREME_UPDATED} → it would never reach the builder.
- **DAY0 exemption correct.** Gate scoped to FORECAST_SNAPSHOT_READY; DAY0_EXTREME_
  UPDATED is gated OFF in this scope and owns its own observation-aware logic when
  later activated. Scopes disjoint.
- **POST_TRADING cannot score:** gate sits after `family = decision.candidate_family`
  (:618) and BEFORE `_generate_candidate_proofs` (:649). No q/FDR/Kelly path precedes it.

### 4. from_market_dict on Row vs dict + F1 fallback — **OK**

- **No sqlite3.Row reaches `from_market_dict`.** The selected `row`
  (`_selected_snapshot_row_for_event` :600) is sourced from
  `_latest_snapshot_rows_for_event_family`, which **already normalizes** every
  sqlite3.Row to a plain dict (`event_reactor_adapter.py:3952`,
  returns `list[dict[str, Any]]`). The gate's `dict(selected_market_row)` is a
  no-op copy of an already-dict; safe. The unknown-city path returns phase=None →
  reject before any dict work.
- **F1 fallback → POST_TRADING for past-12:00Z same-day, NOT silent admit.** When
  `market_end_at` is NULL/absent (critic MAJOR-3: ~100% of retained rows),
  `from_market_dict` falls back to `_f1_fallback_end_utc` = 12:00Z of target_date
  (`market_phase.py:203-211`, `market_phase_evidence.py:191`). A same-day decision at
  16:00Z ≥ 12:00Z end → POST_TRADING → reject. Proven by
  `test_same_day_post_trading_is_rejected` (live-verified: 7/7 pass) and by the live
  blast simulation (all 06-01 families = POST_TRADING via F1 fallback, since
  market_end_at NULL). The NULL-fail-OPEN hole of the dropped ae5fe38 SQL predicate is
  closed by using typed phase evidence + F1 fallback (fail-closed). ✓
- Parse failures in `from_market_dict` are caught and return `_unknown(...)` →
  phase=None → reject. Fail-closed throughout.

### 5. REJECT-RECEIPT OBSERVABILITY (regret ledger) — **OK in production, with CAVEAT B**

- The reject receipt populates `city`, `target_date`, `metric`, `family_id`,
  `source_status="MATCH"`, `family_complete=True`, `reason=
  "EVENT_BOUND_MARKET_PHASE_CLOSED:<phase>:<phase_source>"`.
- `_write_regret` (`reactor.py:601-644`) pulls city/target_date/metric/family_id via
  `_receipt_or_payload`, preferring receipt fields → all populated. Sufficient for a
  meaningful regret-ledger row.
- **Production match-layer passes (verified against live data):** live FSR event
  payloads do NOT carry `condition_id`/`token_id`/`executable_snapshot_id` (confirmed:
  a recent payload's keys omit them, =None). So `_receipt_matches_event`
  (`reactor.py`) — which would reject a receipt whose `condition_id` is None when the
  payload's is truthy — short-circuits those checks (expected falsy) and returns True.
  The receipt then flows through `_receipt_money_path_blocker` →
  `_reject_event` → `_write_regret` with the **phase reason** intact. Corroborated:
  100% of live last-1h regret rows (TRADE_SCORE_NON_POSITIVE, FDR_REJECTED, etc. — all
  on receipts that also lack payload condition_id) carry city, and **zero**
  `EVENT_SUBMISSION_RECEIPT_MISSING_OR_UNBOUND` rows exist. The phase-gate receipt
  behaves identically. ✓

---

## CAVEATS (non-blocking)

**CAVEAT A — gate is committed but NOT yet live.** The running daemon (PID 14287)
started 2026-06-01 10:33 AM, BEFORE the gate commit 57e2114f02 (12:15 PM). It is still
writing same-day buy_no receipts; **zero `EVENT_BOUND_MARKET_PHASE_CLOSED` rows exist
in the live regret ledger (ever).** The checkout HEAD DOES contain the gate (commit is
an ancestor of HEAD). Item-2 blast is therefore SIMULATED against live rows, not
observed firing. ACTION: restart the shadow daemon, then re-run the #98 blast query and
confirm same-day count → 0 and MARKET_PHASE_CLOSED rows appear (IMPL doc residual §1
already lists this). Until restart, the wrong-side admission is still live in shadow.

**CAVEAT B — observability correctness is untested and depends on an unpinned payload
invariant.** The phase-provenance reject reason survives to the regret ledger ONLY
because live FSR payloads omit `condition_id`. No test covers the end-to-end path
through `_receipt_matches_event`. The TIER-2 test asserts on the RAW builder receipt
(`_receipt(...)` = `build_event_bound_no_submit_receipt` directly), bypassing the
reactor match layer — AND its fixture `_bound_forecast_event` DOES inject
`condition_id`/`token_id` into the payload, the opposite of production. If a future
change adds condition_id to live FSR payloads, the receipt (which sets condition_id=None)
would FAIL `_receipt_matches_event` → reject reason silently degrades to
`EVENT_SUBMISSION_RECEIPT_MISSING_OR_UNBOUND`, losing phase provenance. **Safety is
unaffected** (the trade is still blocked), only observability. ANTIBODY suggestion
(not required for this fix): either (a) populate `condition_id`/`token_id` on the
phase-gate reject receipt from the selected `row`, or (b) add a reactor-level test
that drives a phase-closed family through `_reject_event` and asserts the regret row's
`rejection_reason` starts with `EVENT_BOUND_MARKET_PHASE_CLOSED`.

---

## EVIDENCE INDEX (file:line / live)

- Gate: `src/engine/event_reactor_adapter.py:619-648` (`_FORECAST_ONLY_ADMIT_PHASES`
  :487, `_edli_forecast_only_phase_evidence` :490-525, `_forecast_only_phase_admits`
  :528-531).
- Both adapters → builder: :245, :285; live short-circuit :295-296.
- Phase authority: `src/strategy/market_phase.py:120-125` (SD entry = local 00:00),
  :164-177 (POST_TRADING/SETTLEMENT_DAY/PRE_SETTLEMENT_DAY), :203-211 (F1 fallback).
- Evidence builder + fail-closed: `src/strategy/market_phase_evidence.py:145-224`.
- Row normalization to dict: `event_reactor_adapter.py:3952`,
  `_latest_snapshot_rows_for_event_family` returns `list[dict]` (:3908).
- Source-truth fail-closed: `event_reactor_adapter.py:141-163`.
- Re-decision emits FSR: `main.py:3415-3427`, `_edli_emit_forecast_snapshot_events`
  :3700-3741 → `ForecastSnapshotReadyTrigger.scan_committed_snapshots`.
- Regret writer: `src/events/reactor.py:556-644`, `_receipt_matches_event` :480/`+`,
  `_receipt_or_payload` :675+.
- Tests: `tests/engine/test_edli_forecast_only_phase_exclusion.py` 7/7 pass;
  `test_event_reactor_no_bypass.py` 74 pass / 1 xfail.
- Live: daemon PID 14287 start 10:33 < commit 12:15; 0 MARKET_PHASE_CLOSED rows;
  12 same-day 06-01 families all POST_TRADING (simulated); 06-03 Shanghai +all 06-03
  ADMIT; live FSR payload keys omit condition_id/token_id; 0
  EVENT_SUBMISSION_RECEIPT_MISSING_OR_UNBOUND rows.
