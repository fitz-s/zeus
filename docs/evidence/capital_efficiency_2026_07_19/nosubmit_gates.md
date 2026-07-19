# No-Submit Gate Audit — `edli_no_submit_receipts`

Read-only investigation. All DB access via `sqlite3 -readonly` / Python `sqlite3` opened
`?mode=ro`; no writes anywhere. All counterfactuals are computed on frozen decision-time
receipts joined to `forecasts.settlement_outcomes` (`authority='VERIFIED'`) using the
repo's own canonical grading function `grade_receipt()` (`src/contracts/graded_receipt.py`)
and bin parser `_bin_from_label()` (`src/cron/settlement_attribution.py:60`) — the exact
function the live `settlement_attribution` cron uses. No hand-parsed bin punctuation, no
market-price replay.

## 0. Headline finding — the data has a duplication trap, and once corrected there is no
   meaningful EV sitting behind these gates

`edli_no_submit_receipts` looks like it is reporting **62,944 rejected trades worth
$1.9M** over the window. It is not. **62,671 of 62,944 rows (99.6%) share one reason,
`event_bound_final_intent_no_submit`, which is not an economic gate at all** — it is the
hardcoded terminal state of a proof-only builder that the reactor calls on *every* cycle
for candidates that already passed every real gate (Kelly, FDR, RiskGuard). The reactor
re-evaluates the same handful of already-non-actionable candidates every cycle (minutes
apart) for days, and logs a fresh receipt row each time. Deduplicating to distinct
`(city, target_date, metric, direction)` markets collapses the picture from 62,944 rows /
$1.9M notional to **239 distinct markets / ~$4,672 notional, counterfactual PnL +$83
(breakeven)**. There is no large EV being destroyed or saved by any threshold gate in this
table — the real finding is a **telemetry/architecture defect** (duplicate logging of a
dead-by-design code path, and a separate Day0 admission gate that leaves *zero* durable
trace anywhere). See §1 and §6.

## 1. Why 99.6% of "no-submit" rows are not a gate

`edli_no_submit_receipts` is populated from `_build_event_bound_no_submit_receipt_core()`
(`src/engine/event_reactor_adapter.py:11787`), whose own docstring says: *"Produce a typed
no-submit EDLI proof without running the cycle runner."* It calls
`build_event_bound_final_intent_receipt()` (`src/engine/event_bound_final_intent.py:493`)
with `live_submit_enabled=False` **hardcoded as a literal** at its one and only call site
(`event_reactor_adapter.py:14158`); the function's own default is also `False`. A
repo-wide grep confirms **no call site anywhere passes `True`** — this code path is
structurally incapable of ever emitting `SUBMITTED`.

```sql
-- kelly_pass / fdr_pass / trade_score_positive across ALL 62,944 rows:
SELECT json_extract(receipt_json,'$.kelly_pass'), json_extract(receipt_json,'$.fdr_pass'),
       json_extract(receipt_json,'$.trade_score_positive'), COUNT(*)
FROM edli_no_submit_receipts GROUP BY 1,2,3;
-- 1|1|1|62944   <- every single no-submit receipt already cleared Kelly + FDR + score>0
```

By the time a candidate reaches this receipt, it has **already passed every real
economic gate**. The `reason` field defaults to `event_bound_final_intent_no_submit`
purely because this builder never runs the live submit path.

**Overlap check** (is this just a duplicate log of trades that succeeded through a
different path?): of 327 distinct `condition_id`s in this bucket, only **1** ever appears
in `zeus_trades.db:position_current` (the entered-positions table). These are not
duplicate logs of successful trades — they are candidates that were fully qualified and
genuinely never got an order, through *this* code path, on *this* cycle.

**Duplication check** (is 62,671 rows really 62,671 opportunities?):

```
event_bound_final_intent_no_submit: raw_rows=61,643  distinct markets=162
  worst repeat: Toronto 2026-06-03 high buy_no logged 1,974 times
  average repeat: 380x per distinct market
```

The same handful of markets get re-logged as "no-submit" on nearly every reactor cycle for
days. Any EV or dollar total computed at the receipt-row level (as the task's Q1/Q2
framing invites) is inflated by two orders of magnitude versus the real distinct-market
exposure.

Separately, `mainstream_agreement_fail_reason` (`MAINSTREAM_NOT_CLOSE` 2,608 rows,
`MAINSTREAM_FAIL_CLOSED` 605, `DIRECTION_AGREES_MAINSTREAM_SHORTING_LIKELY` 407, etc.) is
**not a gate at all** — `src/strategy/mainstream_agreement.py` states explicitly in its
own docstrings: *"the gate verdict can NEVER exclude a candidate from selection"* and
*"this verdict never gates production... reference-only."* It rides along on receipts as
diagnostic metadata regardless of the real `reason`. There is no KEEP/LOOSEN/TIGHTEN
verdict to render on it because it does not gate anything.

## 2. Data coverage caveat

```sql
SELECT MIN(decision_time), MAX(decision_time), COUNT(*) FROM edli_no_submit_receipts;
-- 2026-05-31T12:44:17Z | 2026-06-29T01:44:25Z | 62944
```

The table's last row is **2026-06-29**, twenty days before today (2026-07-19). This is
not "the last 30 days" as of now — it is a fixed ~30-day window that ended three weeks
ago. `decision_certificates` in the same DB runs current to today
(`MAX(decision_time)=2026-07-19T07:39:39Z`), so the receipt writer for this table appears
to have gone idle (or the table stopped being fed) well before now — worth an operator
check independent of this audit, since it means no-submit telemetry has a 3-week blind
spot right up to the present.

## 3. Q1 — no-submit receipts by reason (raw + corrected)

```sql
SELECT json_extract(receipt_json,'$.reason') AS r, COUNT(*), SUM(kelly_size_usd)
FROM edli_no_submit_receipts GROUP BY r ORDER BY 2 DESC;
```

| reason | raw rows | raw Σkelly_size_usd | **distinct markets** | **representative Σsize (deduped)** |
|---|---:|---:|---:|---:|
| `event_bound_final_intent_no_submit` (not a gate — §1) | 61,643 | $1,890,497 | **162** | **$3,907** |
| `real_order_submit_disabled` | 139 | $1,513 | 65 | $616 |
| `SUBMIT_ABORTED_EXPECTED_PROFIT_BELOW_STRATEGY_FLOOR` | 70 | $499 | **2** | $14 |
| `SUBMIT_ABORTED_MODE_FLIPPED` (all bid/ask variants) | 51 | $136 | 10 | $135 |
| `EDLI_LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT` | 5 | $39 | 1 | $8 |
| all reasons combined | 62,944 | $1,902,920 | **239** | **$4,672** |

The raw-row and raw-dollar columns are what the task's Q1 literally asks for; the
distinct-market / representative-size columns are the corrected reality. Reporting only
the first two columns would materially overstate the case for any gate change.

## 4. Q2 — counterfactual PnL by reason, joined to settlement truth

Method: for every receipt, `_bin_from_label(bin_label, unit)` → `grade_receipt(bin, direction,
settlement)` against `forecasts.settlement_outcomes WHERE authority='VERIFIED'`
(direction law + unit antibody + bin-kind membership — the identical logic
`src.cron.settlement_attribution.load_attribution_input_rows` uses in production).
61,908/62,944 rows (98.4%) joined to a VERIFIED settlement; the remainder have no
settled outcome yet (recent target dates) and are excluded, not assumed.

PnL formula (as specified): `won ? (1−c)·size : −c·size`, `c = c_fee_adjusted`,
`size = kelly_size_usd`. Deduped version takes one representative entry per distinct
market (median price/size across its repeat receipts; the settlement outcome is
invariant per market).

| reason | raw n (graded) | raw win rate | raw counterfactual PnL | **deduped markets** | **deduped win rate** | **deduped counterfactual PnL** |
|---|---:|---:|---:|---:|---:|---:|
| `event_bound_final_intent_no_submit` | 61,643 | 76.2% | **+$37,658** | 162 | 56.8% | **+$147** |
| `real_order_submit_disabled` | 139 | 54.7% | −$95 | 65 | 52.3% | +$9 |
| `SUBMIT_ABORTED_EXPECTED_PROFIT_BELOW_STRATEGY_FLOOR` | 70 | 0.0% | −$370 | 2 | 0.0% | −$11 |
| `SUBMIT_ABORTED_MODE_FLIPPED` | 51 | 37.3% | −$76 | 10 | 30.0% | −$63 |
| `EDLI_LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT` | 5 | 0.0% | −$28 | 1 | 0.0% | −$6 |
| **all reasons combined** | 61,908 | 76.0% | +$37,091 | **239** | **54.0%** | **+$83** |

The raw column's 76.2% win rate and +$37,658 counterfactual PnL for the dominant bucket is
an artifact of counting the same ~160 markets up to 1,974 times each — it is **not** real
signal. The deduped column (239 real markets, +$83 total) is the honest number: **the
entire no-submit population, across every reason, nets to statistical noise over the
month** — no reason bucket here represents meaningful EV destroyed or saved.

`SUBMIT_ABORTED_EXPECTED_PROFIT_BELOW_STRATEGY_FLOOR` is the one bucket that looks like a
clean gate doing its job (0% win rate both raw and deduped) — but it rests on **2
distinct real markets**, not the 70 the raw-row count implies. n=2 is not evidence a
threshold is well-calibrated; it is directionally consistent with the gate being correct
and nothing more.

## 5. Q3 — Day0-specific (`DAY0_*`) reasons: zero visibility, not zero rejections

```sql
SELECT json_extract(receipt_json,'$.reason'), COUNT(*) FROM edli_no_submit_receipts
WHERE json_extract(receipt_json,'$.reason') LIKE 'DAY0%' GROUP BY 1;
-- (no rows)
```

No `DAY0_*` reason appears anywhere in 62,944 receipts, nor in `event_dead_letters`
(25,741 rows, checked for `LIKE '%DAY0%'` and by `failure_stage`/`error_message` sample —
none). Tracing why:

`day0_live_admission_rejection_reason()` (`src/engine/day0_admission.py:52`) can return
`DAY0_CITY_NOT_ALLOWLISTED`, `DAY0_METRIC_NOT_IN_STAGE`, `DAY0_FAST_OBS_UNSUPPORTED`,
`DAY0_SOURCE_HEALTH_NOT_ADMISSIBLE`, `DAY0_QUOTE_TIME_MISSING`,
`DAY0_QUOTE_STALE_VS_OBSERVATION`, `DAY0_ONE_BIN_EDGE_FRAGILE`,
`DAY0_FINAL_LOCALDAY_NOENTRY`, `DAY0_TAKER_ENTRY_FORBIDDEN`, or (from a sibling check)
`DAY0_SUBMIT_TIME_BIN_DEAD`. When it fires, the caller
(`event_reactor_adapter.py:16201-16202`, inside `_build_live_execution_command_certificates`
— a **later, separate stage** from the `_build_event_bound_no_submit_receipt_core` proof
builder in §1) does:

```python
if day0_admission_rejection is not None:
    raise ValueError(f"DAY0_LIVE_ADMISSION_REJECTED:{day0_admission_rejection}")
```

The only handler that converts a `ValueError` into a receipt reason,
`_live_inference_authority_missing_reason()` (`event_reactor_adapter.py:10646`), only
recognizes `FORECAST_AUTHORITY_EVIDENCE_MISSING:` / `CALIBRATION_AUTHORITY_EVIDENCE_MISSING:`
prefixes and returns `None` for anything else — including `DAY0_LIVE_ADMISSION_REJECTED:`.
That handler lives in an earlier try/except (line 14338) than where the Day0 raise
happens (line 16202), so it cannot even see it.

**Conclusion: Day0 admission-gate rejections currently leave no queryable trace in any
table this audit could find.** This is not "the Day0 gate set is well-calibrated" or "too
tight" — it is a genuine blind spot. Q3's premise (join Day0 receipts to settlement) is
**not answerable from current data**, and that gap is itself the actionable finding: the
Day0 lane is new (per the brief) and running with zero audit trail on its own rejection
gates. Recommend wiring `DAY0_LIVE_ADMISSION_REJECTED:*` into a receipt (or at minimum
`event_dead_letters`) before trusting or tuning `day0_admission.py`'s thresholds.

## 6. Q4 — threshold sensitivity without price replay

The task asks to bin rejected candidates by margin-below-cutoff and check whether
realized win rate stays above breakeven. This requires a reason bucket that (a) is a real
scalar-threshold gate and (b) has enough *distinct* rejected markets to bin. Neither
condition is met inside `edli_no_submit_receipts`:

- Every receipt in the table already has `trade_score_positive=1` (§1) — there is no
  population of trade-score-rejected candidates to bin here; that gate's rejects, if any,
  never reach this table.
- `mainstream_agreement_fail_reason` is diagnostic-only (§1) — grading its margin against
  breakeven would answer a question about a value that provably has no causal effect on
  submission.
- The one true scalar-floor gate present, `SUBMIT_ABORTED_EXPECTED_PROFIT_BELOW_STRATEGY_FLOOR`,
  has only **2 distinct markets** (§4) — not enough to build a margin-vs-win-rate curve
  with any statistical meaning. Both available points are losses, consistent with (but not
  proof of) a correctly-placed floor.

No margin-sensitivity curve can be honestly constructed from this table for this window.
If threshold tuning is wanted, it needs either a longer receipt history (once the 3-week
gap in §2 is understood/fixed) or instrumenting the actual trade-score/edge-rejected
population, which this table does not currently carry.

## 7. Q5 — entered cohort cross-check

`zeus_trades.db:position_current`, settled/economically-closed positions with
`target_date` in the same window (2026-05-31 to 2026-06-29):

```sql
SELECT price_band, COUNT(*) n, SUM(realized_pnl_usd>0) wins,
       ROUND(SUM(realized_pnl_usd),2) total_pnl, ROUND(SUM(cost_basis_usd),2) total_cost
FROM position_current WHERE phase IN ('settled','economically_closed')
  AND target_date BETWEEN '2026-05-31' AND '2026-06-29' GROUP BY price_band;
```

| entry price band | n | wins | total PnL | total cost basis |
|---|---:|---:|---:|---:|
| 0.00–0.10 | 22 | 0 | −$17.31 | $18.41 |
| 0.10–0.30 | 1 | 1 | +$15.84 | $2.16 |
| 0.30–0.50 | 5 | 1 | +$8.58 | $30.43 |
| 0.50–0.70 | 63 | 16 | −$179.07 | $484.60 |
| 0.70–0.90 | 36 | 13 | −$49.66 | $312.62 |
| 0.90–1.00 | 2 | 1 | +$0.27 | $18.22 |
| **total** | **150** | 32 | **−$221.35** | **$866.44** |

The **entered** cohort realized roughly **−25.5% on cost basis** over this exact window
(n=150, small-sample caveat applies), versus the no-submit population's **~breakeven
+$83 on $4,672** (§4, deduped, n=239 distinct markets). There is no evidence in this
window that the no-submit boundary is placed too tight — if anything the candidates that
were entered underperformed the candidates that were not, though both samples are small
enough (150 and 239) that this should be read as "no red flag," not "entries are broken."

## 8. Per-gate verdict

| gate / reason | verdict | basis |
|---|---|---|
| `event_bound_final_intent_no_submit` (proof-only builder, §1) | **not a gate — fix the telemetry** | Hardcoded `live_submit_enabled=False`; 380x average duplicate logging per market; deduped EV ≈ $147 over a month. Loosening/tightening this "reason" is a category error — there is no threshold to move. If the intent is ever to make this path live, that is a separate go/no-live decision, not a gate-tuning one. |
| `mainstream_agreement_fail_reason` (MAINSTREAM_NOT_CLOSE etc.) | **not a gate — no verdict applies** | Code explicitly documents it as reference-only, never excludes candidates. |
| `SUBMIT_ABORTED_EXPECTED_PROFIT_BELOW_STRATEGY_FLOOR` | **KEEP** | 0% win rate on both real instances (n=2 markets); directionally correct, though n is too small to prove calibration. |
| `SUBMIT_ABORTED_MODE_FLIPPED` (maker/taker book-flip abort) | **KEEP** | 30% deduped win rate on 10 markets, net −$63; a timing/execution-safety gate, not an edge gate — its job is to stop stale-mode submits, and its outcomes don't argue for loosening. |
| `EDLI_LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT` | **KEEP** (n=1, not decidable) | Only one real instance; no basis to change. |
| `real_order_submit_disabled` | **KEEP** | Near-breakeven (+$9 deduped on 65 markets); this looks like a genuine operational kill-switch (distinct from the always-off `event_bound_final_intent_no_submit` literal — grep shows a separate flag at `event_reactor_adapter.py:6850` / `reactor.py:5088`), not miscalibrated. |
| `DAY0_*` admission gates | **CANNOT GRADE — instrument first** | Zero receipts, zero dead-letters; no counterfactual possible (§5). |

## 9. What this audit did not have

- No pending-DB-write anywhere; every query above is reproducible read-only.
- `edli_no_submit_receipts` window is 2026-05-31→2026-06-29 only (§2) — nothing after
  that date exists in this table as of 2026-07-19, so this audit cannot speak to gate
  behavior in the most recent three weeks.
- Day0 admission-gate rejections are entirely untelemetered (§5) — a real coverage gap,
  not a "gate is fine" result.

---

**Reproduction**: canonical grading script used
`src.cron.settlement_attribution._bin_from_label` +
`src.contracts.graded_receipt.grade_receipt` against
`state/zeus-world.db` (main, read-only) with `state/zeus-forecasts.db` ATTACHed
read-only as `forecasts`. No `db_writer_lock` was acquired (bypassed the cron's own
connection helper, which takes a write lock, in favor of a plain read-only URI connection
plus manual read-only ATTACH) to avoid any contention with the live daemon.
