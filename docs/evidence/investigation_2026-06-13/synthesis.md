# Zeus Weather Derivatives — Multi-Population Adversarial Investigation Synthesis

**Date of snapshot:** 2026-06-13
**Date of synthesis write:** 2026-06-14
**Investigation type:** Broad divergent multi-population adversarial investigation (clean-room ideal vs forensic actual vs empirical truth vs execution-witness), across 16 money-path angles. READ-ONLY: no code edits, no live changes.

**Agent count:** 152
**Candidate defects found:** 99
**Refuted or demoted:** 25
**Hardened survivors (as of 06-13 snapshot):** 3 (all subsequently stale after 06-14T06:01 self-clear — see keep_invariants)

---

## Provenance / Snapshot

- **Current code SHA (at snapshot):** `0b553c90fd` (gate-mass collapse Wave C/D: remove redundant re-checks + banned throttles)
- **Tree cleanliness:** Dirty — 3 untracked files in `.claude/` and `docs/operations/` (evidence/investigation artifacts)
- **Daemon uptime (PID:etime):** Main: 3h18m36s · Forecast daemon: 5h41m20s · Ingest: 2h37m54s
- **DB canonical exists:** All three present: `zeus-world.db` (42G, 23:28), `zeus-forecasts.db` (36G, 23:28), `zeus_trades.db` (18G, 23:28)

---

## Targeted Fix vs Rebuild Verdict

**Verdict: TARGETED_FIX**

This is NOT a ground-up rebuild. The spine — settlement-preimage q, fusion, capital_efficiency gate, reconcile/absorber, INV-37 — is correct. The defect is a K=3 surgical reconnection of the calibration-licensing lane so that an honest licensed q_lcb can REACH the cheap-tail opportunity the reactor already detects.

K<<N target:

**(K1) ONE reachable settlement-licensed source for cheap tail bins.** Either (a) materialize `state/emos_ci_license.json` for the HIGH-metric cities that have a fitted EMOS CI (re-enabling the buy_yes lane), or (b) replace the binary {licensed-source membership} test in `coverage_unlicensed_tail` with the SAME `settlement_backward_coverage` VERDICT authority already used elsewhere (`settlement_backward_coverage.py`), so a `FORECAST_BOOTSTRAP` q_lcb that the settled record BACKS within tolerance is admitted without needing a separate source-string. Option (b) is preferred: it collapses two parallel licensing vocabularies (source-allow-list in live_admission vs verdict-status in settlement_backward_coverage) into ONE authority — exactly the operator's "collapse N gates to K" law.

**(K2) buy_no honest NO-side LCB.** Stop hard-coding `emos_q_lcb_no=0.0`; either supply a real NO-posterior or formally declare buy_no longshots out-of-scope (they are base-rate per operator law #5, so declaring them out-of-scope is legitimate and SIMPLER).

**(K3) Fix the reactor cycle-summary** to attribute the rejection to the gate that actually killed the best candidate, so "no-edge" is never confused with "de-licensed". Migration order: K3 first (observability, zero risk), then K1(b) behind the existing verdict authority (no new gate), then K2 decision (likely out-of-scope declaration). No table renames, no new flags (operator law: no shadow, go-live-direct).

---

## Ordered First Cut

### Rank 1 — Fix reactor cycle-summary observability

**What:** Fix the reactor cycle-summary so the rejection reason attributed to the cycle is the gate that actually rejected the displayed "best" candidate (separate the display-EV pick from the bucket-label, and emit the per-candidate winning-gate for the best).

**Affected stage:** Stage 4 observability (`event_reactor_adapter.py:7149-7206`)

**Why first:** Zero runtime risk, and it is the prerequisite for trusting every other diagnosis. Three prior refuters AND the witness mislabeled the binding constraint as `capital_efficiency`/no-edge when it is `coverage_unlicensed_tail`/de-licensing. Until the log stops lying, every fix is flying blind.

**Minimal patch:** In the cycle-summary builder, compute `best_rejection_reason` = the actual reason string for the max-display-EV candidate, and print `"best=... rejected_by=<gate>"` instead of letting `best=` float free of its kill reason. Do not change any gate logic.

**Expected stage delta:** Log lines change from `"ALL_CANDIDATES_REJECTED ... best=Manila +97"` to `"best=Manila +97 rejected_by=COVERAGE_UNLICENSED_TAIL"`. No change to certs/fills.

**Kill criteria:** If after the patch the best= candidate's `rejected_by` is genuinely `capital_efficiency` (q_lcb<=price for the SAME q_lcb shown), then the no-edge hypothesis is real and K1/K2 are unnecessary — STOP and report honest-no-edge.

---

### Rank 2 — Re-route coverage_unlicensed_tail to settlement verdict authority

**What:** Re-route `coverage_unlicensed_tail`'s licensing test from the static source-allow-list `{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}` to the `settlement_backward_coverage` VERDICT authority already in the codebase, so a `FORECAST_BOOTSTRAP` tail q_lcb that the settled record BACKS (status in `LICENSING_STATUSES`) is admitted, and only genuinely-unbacked tails are rejected.

**Affected stage:** Stage 4 admission (`live_admission.py:141-176`) + the verdict thread already present at `event_reactor_adapter.py:12235`

**Why first (among K1 options):** This is the single wire that reconnects detected +EV to admission WITHOUT loosening the honest q_lcb>price gate and WITHOUT inventing a new source. It collapses two licensing vocabularies into one (operator: collapse N->K), and it is the minimal correct fix: the gate's INTENT (block unbacked tails) is preserved; only the membership test is replaced by the authoritative verdict.

**Minimal patch:** In `coverage_unlicensed_tail_rejection_reason`, replace `"source in licensed_sources"` with `"settlement_coverage_status in SETTLEMENT_COVERAGE_LICENSING_STATUSES"` (thread the same status already computed per family). Keep the `price<0.05` and `q_lcb>2x price` conditions. Add a property test: a tail bin with LICENSED verdict admits; with INSUFFICIENT_DATA rejects.

**Expected stage delta:** Cheap +EV candidates in cities/bins WITH settled backing become admitted → first non-`FORECAST_BOOTSTRAP`-only receipts → `proof_accepted>0` → first LIVE `decision_certificate` since 06-12 → (with B1 already self-cleared) first new envelope/command. Candidates WITHOUT backing still correctly rejected.

**Kill criteria:** If after this change the count of families with a LICENSED settlement verdict on any cheap tail bin is ZERO (run the verdict over today's families), then the settled record genuinely does not back ANY cheap claim → the edge is unproven, NOT suppressed → do NOT force it; escalate to the calibration-coverage rebuild instead of admitting unbacked tails.

---

### Rank 3 — Adjudicate buy_no longshots explicitly

**What:** Adjudicate buy_no longshots explicitly: either supply a real NO-side posterior LCB (not the 0.0 stub) or formally declare buy_no cheap-tail out-of-scope per operator law #5 (buy_no win is base-rate already priced).

**Affected stage:** Stage 3/4 (`event_reactor_adapter.py ~12064` `emos_q_lcb_no=0.0`; `live_buy_no_conservative_evidence` gate)

**Why first (relative to rank 4+):** Cleans up the dead buy_no licensing stub so it stops masquerading as a lane and stops polluting the rejection buckets. Lower urgency because buy_no longshots are the LEAST likely profitable class (base-rate), so the honest answer is probably out-of-scope — which is the SIMPLER outcome the operator's collapse-law favors.

**Minimal patch:** Decision, not just code: if keeping buy_no tail, replace `emos_q_lcb_no=0.0` with a materialized NO-posterior; if dropping it, route `buy_no price<0.05` to an explicit `DIRECTION_SCOPE_BUY_NO_TAIL_OUT_OF_SCOPE` reason and remove the misleading 0.0 stamp.

**Expected stage delta:** buy_no tail candidates stop generating false `capital_efficiency` rejections (they reject with an honest scope reason); no change to buy_yes fills. Rejection buckets become truthful.

**Kill criteria:** If a backtest of buy_no cheap-tail over settled history shows positive realized edge net of cost (contradicting the base-rate assumption), do NOT declare out-of-scope — build the NO posterior instead.

---

## Full Rebuild Blueprint

NOT a ground-up rebuild. The spine (settlement-preimage q, fusion, capital_efficiency gate, reconcile/absorber, INV-37) is correct. The defect is a K=3 surgical reconnection of the calibration-LICENSING lane so that an honest licensed q_lcb can REACH the cheap-tail opportunity the reactor already detects. K<<N target:

**(K1)** ONE reachable settlement-licensed source for cheap tail bins — either (a) materialize `state/emos_ci_license.json` for the HIGH-metric cities that have a fitted EMOS CI (re-enabling the buy_yes lane), or (b) replace the binary {licensed-source membership} test in `coverage_unlicensed_tail` with the SAME `settlement_backward_coverage` VERDICT authority already used elsewhere (`settlement_backward_coverage.py`), so a `FORECAST_BOOTSTRAP` q_lcb that the settled record BACKS within tolerance is admitted without needing a separate source-string. (b) is preferred: it collapses two parallel licensing vocabularies (source-allow-list in live_admission vs verdict-status in settlement_backward_coverage) into ONE authority — exactly the operator's "collapse N gates to K" law.

**(K2)** buy_no honest NO-side LCB: stop hard-coding `emos_q_lcb_no=0.0`; either supply a real NO-posterior or formally declare buy_no longshots out-of-scope (they are base-rate per operator law #5, so declaring them out-of-scope is legitimate and SIMPLER).

**(K3)** fix the reactor cycle-summary to attribute the rejection to the gate that actually killed the best candidate, so "no-edge" is never confused with "de-licensed".

Migration: K3 first (observability, zero risk), then K1(b) behind the existing verdict authority (no new gate), then K2 decision (likely out-of-scope declaration). No table renames, no new flags (operator law: no shadow, go-live-direct).

---

## Keep Invariants

### 1. B1 self-clears autonomously via the settled-class path — hardened defects are now STALE

Finding `5bbc2be2` resolved `2026-06-14T06:01:47` by `src.execution.exchange_reconcile` (NOT an operator). The external-close absorber (#31) + settled-class reconcile DO work on the correct timeline (once the market crosses `terminal_after`). Do NOT rebuild the reconcile/absorber — preserve it. The three "hardened" defects (R1/R15/R11) are STALE-as-of-now: true at the 06-13 snapshot, void after 06-14T06:01. The R1/R15/R11 trio described a true-but-transient latch that the settled-class path cleared automatically once Denver 06-12 crossed `terminal_after` (06-14T06:00Z). Do not act on them as live blockers.

### 2. capital_efficiency gate is the HONEST q_lcb>price-after-cost test — do not loosen

`live_admission.py:113-118`, `conservative_ev_per_dollar=(q_lcb-price)/price`, reject iff <=0. This is correct and load-bearing. Do NOT loosen it. It is not the defect; it is the truthful final arbiter. The defect is UPSTREAM (which q_lcb / which source reaches it). Task #66 already adjudicated this.

### 3. coverage_unlicensed_tail's INTENT is correct and must survive

It is the fail-CLOSED dual of the K3 fail-open (Milan-24C incident `0b5c305e`, `docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md`). Letting a raw `FORECAST_BOOTSTRAP` tail q_lcb trade unbacked is exactly the loss class it prevents. A rebuild must keep a licensing discipline on cheap tail bins — the fix is to make a HONEST licensed source REACHABLE, not to delete the gate.

### 4. INV-37 cross-DB write discipline and K1 DB split are intact and correct

INV-37 (ATTACH+SAVEPOINT, never independent connections) and the K1 DB split (`zeus-world` / `zeus_trades` / `zeus-forecasts`) are intact and correct; the reconcile self-clear wrote correctly across DBs. Preserve.

### 5. Settlement is the only truth — core q spine is correct and uninvolved

Settlement is the only truth; settlement-preimage bin integration, per-city DST/time-semantics contract (#16), direction law, and the σ-shape floor are the correct spine of q. None of these are implicated in the no-fill — keep all.

### 6. EMOS is HIGH-metric-only by design — CI-honesty law

EMOS is HIGH-metric-only by design (HIGH params on LOW members = garbage) and `k_cov` must never tighten sigma (CI-honesty law). Any reachability fix must respect both — do not license LOW-metric or shrink sigma below MC.

---

## The Five Contradictions

### Contradiction 1 — REACTOR SAYS +EV, GATE SAYS REJECT

The reactor's own cycle summary computes `ev_per_dollar=+97.04` for Manila buy_yes (`q_lcb=0.1961` vs `price=0.0020`) and `+41.79` for Wellington, yet labels the whole cycle `EVENT_BOUND_ALL_CANDIDATES_REJECTED` with `capital_efficiency_lcb_ev` as the dominant bucket. The "best" candidate is selected by DISPLAY ev (raw `FORECAST_BOOTSTRAP` q_lcb) but killed by a DIFFERENT gate (`coverage_unlicensed_tail` at `live_admission.py:141`), so the reason label and the displayed candidate do not refer to the same gate. The log conflates two gates and reads as "honest no-edge" when the truth is "cheap +EV systematically de-licensed". (`event_reactor_adapter.py:7149-7206`)

### Contradiction 2 — FUSION/CALIBRATION CLAIMS EDGE EXACTLY WHERE LICENSING ZEROES IT

The q_lcb pipeline produces material disagreement with the market precisely on cheap longshot tail bins (`price<0.05`) — the only place a small stake can return multiples — but `coverage_unlicensed_tail` (`live_admission.py:152-176`) rejects exactly that intersection (`price<TAIL_PRICE_MAX=0.05 AND q_lcb>2x price AND source not in {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}`). Calibration asserts the edge and the licensing layer voids it at the same coordinate. Cheap-price opportunity and licensed-source coverage are anti-correlated by construction.

### Contradiction 3 — LICENSED-SOURCE LANE IS UNREACHABLE FOR THE OPPORTUNITY IT GATES

The two settlement-licensed sources both fail on the tail. (a) `EMOS_ANALYTIC` fires 0 times in the entire live log (grep count = 0) because `state/emos_ci_license.json` DOES NOT EXIST → load fail-opens to `{}` → every city unlicensed → `emos_ci_k_cov` returns `None` → the override early-returns before stamping (`event_reactor_adapter.py:12003-12013`). (b) Even when EMOS would fire it stamps `emos_q_lcb_no=0.0` for buy_no (`line ~12064`) so buy_no can never be EMOS-licensed. (c) `SETTLEMENT_ISOTONIC` fires 19,715 times but only SHRINKS q_lcb toward market (fail-closed) and requires `min_n=30` settled obs per (city, metric, season, bin) which tail bins on near-future dates do not have. Net: there is no live code path that promotes a cheap +EV candidate to a licensed source, so 100% of them die.

### Contradiction 4 — B1 "COINCIDENCE" WAS AN ARTIFACT — CERT BLACKOUT PRECEDED THE FINDING

The witness tied Stage-4 cert blackout to the B1 finding at `2026-06-12T22:58`. Ground truth: last `decision_certificate` is `2026-06-12T17:04` and last `no_submit_receipt` `12:12` — BOTH predate the `22:58` finding by hours. The cert engine went dark BEFORE the submit latch froze, so B1 cannot be the cause of the cert blackout. Two independent failures were fused into one narrative.

### Contradiction 5 — buy_no >0.6 "edge" is base-rate, not alpha (operator law #5 violated in the witness framing)

The only price band that ever filled is `cost>0.6` (38 of 39 recent receipts), where ~90% buy_no win-rate is already in the price. `avg(q_lcb-cost)=+0.0598` there is the conservative bound clearing a favorite already priced as a favorite — NOT tradeable edge. The mid 0.2-0.6 class (the real edge band) has collapsed to n=1. So "unblock submission" would, at best, re-enable base-rate favorite-buying, which is not profitable alpha.

---

## Defect Ledger (Full)

### D1 — EMOS_ANALYTIC licensing lane DEAD: state/emos_ci_license.json missing

**Tag:** MECHANICAL_BLOCK
**Impact:** 95

**Evidence:** `ls state/emos_ci_license.json` → No such file. `load_emos_ci_license()` fail-opens to `{}` (`emos_ci_license.py:30`). `emos_ci_k_cov(city)` returns `None` for every city → override early-returns (`event_reactor_adapter.py:12011-12013`) BEFORE any `EMOS_ANALYTIC` stamp. `grep -c 'EMOS-CI override' logs/zeus-live.log = 0`; `grep -c 'K3 coverage shrink' = 19715`. Receipt source distribution: 85/85 recent = `FORECAST_BOOTSTRAP`, 0 ever `EMOS_ANALYTIC` or `SETTLEMENT_ISOTONIC` (all-time). Because every cheap candidate stays `FORECAST_BOOTSTRAP`, `coverage_unlicensed_tail` rejects every `price<0.05` material-disagreement longshot — the exact +EV opportunities the reactor flags (Manila +97, Wellington +41).

**Repro:** `ls -la state/emos_ci_license.json` (absent); `grep -c 'EMOS-CI override' logs/zeus-live.log` (=0); `sqlite3 state/zeus-world.db "SELECT q_lcb_calibration_source, COUNT(*) FROM edli_no_submit_receipts GROUP BY 1"` (FORECAST_BOOTSTRAP only).

---

### D2 — EMOS buy_no LCB hard-coded to 0.0

**Tag:** LOGIC_DEFECT
**Impact:** 70

**Evidence:** `event_reactor_adapter.py ~line 12064`: `emos_q_lcb_no = 0.0` (comment: "Buy-NO requires an explicit NO-side posterior/LCB, not a YES complement"). The override loop then stamps `source=EMOS_ANALYTIC` for BOTH directions but with `emos_lcb=0.0` on buy_no. So a buy_no longshot gets a licensed source but `q_lcb=0.0`, which fails `capital_efficiency` (`0.0<=price`). The NO side has no honest licensed lane at all; only buy_yes could ever be EMOS-licensed.

**Repro:** Read `event_reactor_adapter.py` lines 12060-12085; confirm `emos_q_lcb_no=0.0` unconditionally.

---

### D3 — SETTLEMENT_ISOTONIC only SHRINKS (fail-closed) and needs min_n=30 obs that tail bins never have

**Tag:** DESIGN_GAP
**Impact:** 65

**Evidence:** `event_reactor_adapter.py:12235-12245`: `settlement_backward_coverage_check(min_n=30)`; `apply_settlement_coverage` only writes `SETTLEMENT_ISOTONIC` when `new_q != claimed` (a shrink). For a longshot tail bin (e.g. 35C+ in shoulder season) there are <30 settled obs, so the verdict is `INSUFFICIENT_DATA` and q_lcb is NOT licensed. 19,715 K3 shrinks fired but 0 produced a `SETTLEMENT_ISOTONIC`-sourced receipt. This is the OTHER licensed source and it too cannot reach the opportunity.

**Repro:** `grep -c 'K3 coverage shrink' logs/zeus-live.log` (19715); `SELECT COUNT(*) FROM edli_no_submit_receipts WHERE q_lcb_calibration_source='SETTLEMENT_ISOTONIC'` (=0).

---

### D4 — Reactor cycle-summary conflates two gates: observability defect

**Tag:** OBSERVABILITY_DEFECT
**Impact:** 55

**Evidence:** `event_reactor_adapter.py:7149-7206`: the n= bucket counts and the `best=` string are computed independently. `best=` picks max display `ev_per_dollar` (raw `FORECAST_BOOTSTRAP` q_lcb, e.g. +97) while the dominant bucket label is `capital_efficiency_lcb_ev`. An operator reading the log concludes "honest no-edge" (`capital_efficiency`) when the cheap winners are actually dying on `coverage_unlicensed_tail` (licensing), a fixable upstream defect. This is why three prior refuters and the witness mislabeled the binding constraint.

**Repro:** `grep 'EDLI reactor cycle result' logs/zeus-live.log | tail -10` — observe `best=` with `ev_per_dollar>0` inside an `ALL_CANDIDATES_REJECTED` line.

---

### D5 — Decision-cert engine (Stage 4) dark since 2026-06-12T17:04, independent of B1

**Tag:** MECHANICAL_BLOCK
**Impact:** 60

**Evidence:** `decision_certificates` last row `2026-06-12T17:04:15`; reactor cycles continue NOW (`06-14T06:34`, `processed=4 rejected=4`). `proof_accepted=0` across all last-24h cycles. Certs are dark because EVERY candidate is rejected (the licensing-starvation chain above), not because the engine stopped — but the witness fused this with B1. The fix is the licensing lane, not the cert engine.

**Repro:** `grep 'EDLI reactor cycle result' logs/zeus-live.log | tail -25 | grep proof_accepted=0` (all); `sqlite3 state/zeus-world.db "SELECT MAX(decision_time) FROM decision_certificates"` (2026-06-12T17:04).

---

### D6 — B1/B2/B3 "hardened" defects are STALE post-06-14T06:01 self-clear (R13 vindicated)

**Tag:** STALE_FINDING
**Impact:** 40

**Evidence:** `sqlite3 state/zeus_trades.db "SELECT COUNT(*) FROM exchange_reconcile_findings WHERE resolved_at IS NULL"` = 0. Finding `5bbc2be2` `resolved_at=2026-06-14T06:01:47` `resolved_by=src.execution.exchange_reconcile`. The R1/R15/R11 "hardened" trio described a true-but-transient latch that the settled-class path cleared automatically once Denver 06-12 crossed `terminal_after` (06-14T06:00Z). Do not act on them as live blockers.

**Repro:** `sqlite3 state/zeus_trades.db "SELECT finding_id,resolved_at,resolved_by FROM exchange_reconcile_findings WHERE finding_id LIKE '5bbc2be2%'"`.

---

## Unverified Items

1. **WHETHER ANY cheap tail bin on a CURRENT target date actually carries a LICENSED settlement_backward_coverage verdict (>=30 settled obs backing the claim).** This is THE decisive unknown for rank-2's kill-criteria: if zero families qualify, the edge is unproven (honest-no-edge / calibration-coverage deficit) and rank-2 must NOT be applied. NOT verified here — requires running the verdict over today's families. This single check decides targeted-fix vs calibration-rebuild.

2. **Whether unblocking submission (B1, now self-cleared) would produce a PROFITABLE fill:** answered NO for the CURRENT candidate set (zero admitted candidates today; the only historically-filled band is >0.6 base-rate buy_no which is not alpha). But NOT verified whether the cheap buy_yes longshots, IF admitted via rank-2, are actually profitable at settlement — needs a backtest of `FORECAST_BOOTSTRAP` tail q_lcb vs realized settlement on the longshot class.

3. **Whether `state/emos_ci_license.json` was ever populated (deleted vs never-created).** `git log` / file history not checked. Determines whether rank-2 option (a) [restore file] is a regression-repair or a never-built feature.

4. **Whether the mid-band (0.2-0.6) candidate collapse (n=1 recent) is a separate upstream defect** (fusion coverage / family generation) or simply the natural absence of mid-priced weather markets this week. Stage-1 (`selection_family_fact`) and Stage-3 (`opportunity_fact`) being structurally zero across the whole window is unexplained — the decision path evidently routes through a different mechanism (the EDLI reactor reads EMS directly), but the dead family/opportunity tables are not confirmed dead-vs-vestigial.

5. **Whether B3 (Beijing 06-14 NO open position with blind-exit `BELIEF_AUTHORITY_FAULT`, stale belief 87 cycles) is still live and at risk** — not re-probed in this pass; the K6 belief-dead circuit breaker (task #47) remains pending and could be the real money-at-risk item independent of the no-NEW-fill question.

6. **The EMOS skip path:** confirmed 0 "EMOS-CI override" lines but did NOT confirm whether "EMOS-CI live override skipped" warnings fire (the bin-level skip) vs the family-level early-return — minor, does not change the missing-license-file root cause.

---

## Completeness Critic — Gaps

### GAP 1 — The tail gate label `coverage_unlicensed_tail` does not exist in the live system as a persisted reason

The synthesis repeatedly names `coverage_unlicensed_tail` (`live_admission.py:141`, `:152-176`, `:141`) as the dominant reject reason. The actual gate labels in `no_trade_events` are `strategy_economic_floor` (1,058), `ultra_low_price_not_authorized` (373), and `confidence_band_insufficient` (379). `coverage_unlicensed_tail` is either a renamed gate, a log-level label not persisted to the table, or fabricated by a prior agent. The synthesis's entire architectural claim — that a licensing check blocks +EV tail candidates — may be attributed to the wrong gate.

**Next probe:** `grep -n "coverage_unlicensed_tail" src/execution/live_admission.py src/execution/event_reactor_adapter.py` to confirm whether this string exists at all in live code, and map the actual gate at line 141 to its persisted `reason` string.

### GAP 2 — EMOS shadow ledger shows `emos_q` populated (6,875 of 10k recent entries) yet synthesis claims EMOS fires 0 times

The synthesis states `emos_ci_license.json ABSENT -> load fail-opens to {} -> every city unlicensed -> emos_ci_k_cov returns None -> override early-returns`. But 6,875 of the last 10k ledger entries have `emos_q not None` and `served='emos'`. This is a direct contradiction: either EMOS is computing and serving (the ledger proves it), or `served='emos'` means something different from `q_lcb_calibration_source='EMOS_ANALYTIC'` in receipts. The two can coexist if the shadow ledger records EMOS-computed values that are then downgraded to `FORECAST_BOOTSTRAP` at the licensing step — but this path is unverified.

**Next probe:** Read `emos_ci_license.py` load function and `event_reactor_adapter.py` ~line 12003-12013 to confirm whether `emos_q` in the ledger means "computed but then rejected by license check" or whether the license file is NOT actually required for shadow-ledger population.

### GAP 3 — 454 cheap-tail buy_yes receipts ALL pass capital_efficiency yet none submitted; 439/454 have NULL source (not FORECAST_BOOTSTRAP)

The synthesis claims these are killed by `coverage_unlicensed_tail`. But `q_lcb_calibration_source` is NULL for 439 of the 454 — not `FORECAST_BOOTSTRAP`. A NULL source is a different failure mode than an unlicensed source. Something upstream of the licensing gate is producing receipts with null source stamps, which may mean the receipt path itself is broken for cheap tail candidates before they ever reach a licensing check.

**Next probe:** Sample 5 of the 454 cheap-tail buy_yes receipts by `receipt_id`; trace the `receipt_json` field to see what gate recorded the rejection reason and whether a licensing check appears.

### GAP 4 — Mid-band (0.2-0.6 cost) receipt count (3,285) is not zero and exceeds the synthesis's "collapsed to n=1" claim

The synthesis frames buy_yes mid-band as existentially absent. The receipts table shows 3,285 receipts with `cost 0.2-0.6` (direction unfiltered). This may be dominated by buy_no, but the n=1 claim is stated for recent fills, not for all receipts. The mid-band edge verdict ("not profitable alpha") rests on whether q_lcb>cost holds here.

**Next probe:** `SELECT direction, AVG(q_lcb_5pct - c_cost_95pct), COUNT(*) FROM edli_no_submit_receipts WHERE c_cost_95pct BETWEEN 0.2 AND 0.6 GROUP BY direction` — confirm the q_lcb distribution in the mid-band to verify the "base-rate only" verdict.

### GAP 5 — B1 self-clear time (06-14T06:01:47) is unverified against the CURRENT latch state

The synthesis (via R13) asserts B1 resolved autonomously. The live evidence at session start says B1 was frozen since `06-12T22:58`. The self-clear claim names a specific timestamp but the current `allow_submit` state has not been re-read in this session. If the latch re-froze (another phantom finding arrived), the "B1 clear" verdict is stale.

**Next probe:** `SELECT allow_submit, frozen_at, frozen_reason FROM <submit_latch_table>` (or equivalent) to confirm the latch is currently open and no new `exchange_reconcile_findings` are blocking submission.

### GAP 6 — Opportunity book selector ON/OFF gate deletion materialization status unconfirmed

The live evidence explicitly flags this as unresolved: "one path kept — NOT confirmed whether materialization survived." None of R1-R16 appear to have probed this. The `opportunity_book.py` and `opportunity_selector.py` exist and are imported by `event_reactor_adapter.py`, confirming code presence. But materialization = whether `build_family_opportunity_book` is actually called on every reactor cycle and its output persisted.

**Next probe:** `grep -n "build_family_opportunity_book" src/engine/event_reactor_adapter.py` and check call-site guard conditions — confirm there is no residual ON/OFF flag or import-guard that silently skips the build.

### GAP 7 — `confidence_band_insufficient` (379) and `model_conflict` (336) are the 2nd and 3rd largest rejection classes but neither is mentioned in the synthesis

These are not small-print. 379 + 336 = 715 rejections not covered by any of the 16 angles. The `confidence_band_insufficient` traces show `yes_ci_lower_nonpositive` — meaning q_lcb at the CI lower bound is ≤0, which kills the candidate BEFORE licensing. If calibration is systematically producing zero-floor LCBs on precisely the cheap tail bins that would otherwise be +EV, this is a third independent mechanism killing the same opportunity class, upstream of the licensing gate the synthesis focuses on entirely.

**Next probe:** Cross-tabulate `confidence_band_insufficient` rejections by cost bin — confirm whether this rejection class concentrates at `cost < 0.05` (tail) or is distributed across the cost range.

---

*End of synthesis. Untrimmed, per operator instruction.*
