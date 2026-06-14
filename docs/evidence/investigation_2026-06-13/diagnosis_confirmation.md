# Diagnosis Confirmation — Read-Only Re-Adjudication of the No-Fill Synthesis

**Date:** 2026-06-14
**Mode:** READ-ONLY (no code edits, no daemon touch, DBs opened `-readonly`).
**Tests:** `docs/evidence/investigation_2026-06-13/synthesis.md` (the TARGETED_FIX claim) against primary evidence.
**Authority basis:** live config `config/settings.json`, live log `logs/zeus-live.log` (06-13/06-14), `state/zeus-world.db` (`edli_no_submit_receipts`, `no_trade_events`), `state/zeus_trades.db` (`exchange_reconcile_findings`), live source (`event_reactor_adapter.py`, `live_admission.py`, `main.py`).

---

## ACTUAL BINDING CONSTRAINT (corrected headline)

**The #1 binding constraint that prevents a correct cheap-/mid-bin +EV trade from filling is (b): the honest `capital_efficiency` gate firing on a q_lcb that has collapsed to ≈0 — NOT (a) licensing de-coverage.**

Ranked by live evidence (aggregate rejection buckets across all reactor cycles in `logs/zeus-live.log`):

| Rank | Gate | Count | Share | Verdict |
|---|---|---|---|---|
| 1 | `capital_efficiency_lcb_ev` (q_lcb ≤ price-after-cost, mostly q_lcb≈0) | **18,829** | **~88%** | THE binding constraint |
| 2 | `other` (mixed) | 2,820 | ~13% | secondary |
| 3 | `direction_law` | 682 | ~3% | minor |
| 4 | **`coverage_unlicensed_tail`** (the synthesis's claimed cause) | **123** | **~0.6%** | NEGLIGIBLE — synthesis misattributed |

The synthesis named `coverage_unlicensed_tail` as the dominant kill and built a K=3 licensing-reconnection fix on it. The gate is real and wired, but it fires on **0.6% of rejections** and has **never once been persisted into a receipt** (0 of 62,874). The synthesis is **REFUTED on its central causal claim.** The licensing lane is a real-but-tiny tail effect (1–2 candidates per 22-candidate cycle); the mass killer is honest no-edge driven by q_lcb≈0.

A second, equally important structural finding: **`edli_no_submit_receipts` is by construction the table of `proof_accepted=1, submitted=0` candidates** — every one of its 62,874 rows already PASSED admission. The synthesis read non-submission in that table as admission-stage de-licensing; it is actually submit-stage non-execution layered on top of a separately-failing admission stage. Both the receipt table AND the live cycle log must be read to see the true picture, and they tell the same story: capital_efficiency, not licensing.

---

## GAP-BY-GAP RESOLUTION

### GAP 1 — does `coverage_unlicensed_tail` exist in live code; is it the persisted reason?

**Probe:** `grep -rn "coverage_unlicensed_tail" src/`; `no_trade_events` reason counts; receipt-level reason counts.

**Result:**
- The string EXISTS in live code: `live_admission.py:141` (`coverage_unlicensed_tail_rejection_reason`) + `event_reactor_adapter.py:231,7112,7583-7603`.
- It returns a `missing_reason` of the form `COVERAGE_UNLICENSED_TAIL:...` that lives ONLY on the in-memory proof object and the cycle-summary classifier (`event_reactor_adapter.py:7112`). It is **NOT** a member of the `no_trade_events.reason` CHECK constraint, so it can never be a `no_trade_events` row.
- `no_trade_events` is **STALE**: its data spans 2026-05-20 → **2026-05-28** only. Zero rows since 06-07. The critic's "strategy_economic_floor 1058 / confidence_band_insufficient 379 / model_conflict 336" are **all-time May counts**, not the live no-fill drivers. They are from a different (pre-EDLI-reactor) decision path and are irrelevant to the current window.
- Receipt-level: `COVERAGE_UNLICENSED_TAIL` appears in **0 of 62,874** receipt_json blobs. `ADMISSION_CAPITAL_EFFICIENCY` appears in all 91 recent ones.

**Verdict:** The synthesis attributed the no-fill to the WRONG gate AND conflated a stale May table with the live path. `coverage_unlicensed_tail` is a live-log cycle-summary label, never a persisted rejection, and never the dominant one.

---

### GAP 2 — does EMOS fire? Is contradiction C3 ("EMOS fires 0×") true?

**Probe:** `ls state/emos_ci_license.json`; read `emos_ci_license.py` load + override site `event_reactor_adapter.py:11994-12013`; config flags; `state/emos_shadow_ledger.jsonl` served distribution.

**Result:**
- `state/emos_ci_license.json` **does not exist** (and never has — see GAP 6).
- The **live override** (`_emos_ci_live_override`, the ONLY path that stamps `q_lcb_calibration_source=EMOS_ANALYTIC`) is gated by THREE conditions, ALL of which fail:
  1. `edli_emos_ci_live_enabled` — **absent from config → default `False`** (`event_reactor_adapter.py:11995`). This alone makes the override a no-op.
  2. `family.metric == "high"`.
  3. `emos_ci_k_cov(family.city) is not None` — returns `None` for every city because the license file is absent → early-return at `:12011`.
- The **shadow ledger** (`state/emos_shadow_ledger.jsonl`, 280 MB) shows served='emos' on 6,875/10k recent lines — TRUE. But the shadow ledger is written by `_write_emos_shadow_ledger` under a **different flag** (`edli_emos_shadow_ledger_enabled`) and `served='emos'` means only "an EMOS calibration cell was FOUND and computed" — it is **observational**, written before/independent of the licensing step, and never reaches receipts as a licensed source.

**Verdict on C3:** **PARTIALLY TRUE, correctly-for-the-wrong-reason.** EMOS *is computed* (shadow ledger proves it), but the EMOS *live licensing override* fires **0×** because the flag is OFF (default) — the license file is a red herring; even restoring it changes nothing while `edli_emos_ci_live_enabled` is absent/False. `served='emos'` ≠ licensed-and-serving; it is computed-then-never-licensed. The synthesis's remedy (materialize the license file) would NOT re-enable the lane on its own.

---

### GAP 3 — the cheap-tail buy_yes receipts: NULL source, what gate recorded non-submission?

**Probe:** Sample 5 cheap-tail buy_yes (`c_cost_95pct<0.05`, `q_lcb_5pct>2×cost`) receipts; read `receipt_json`; cross-tab `proof_accepted`/`submitted`/top-level `reason`.

**Result:**
- All 454 cheap-tail buy_yes receipts have **`proof_accepted=1`, `trade_score_positive=1`, `submitted=0`**. They PASSED admission (including `coverage_unlicensed_tail`, which would have set `score=0` and blocked prefilter — they were not blocked).
- Their top-level `reason`: `event_bound_final_intent_no_submit` (418), `real_order_submit_disabled` (23), `SUBMIT_ABORTED_MODE_FLIPPED` (13) — **all SUBMIT-stage reasons**, none a licensing/admission gate.
- Inside `receipt_json`, the per-candidate `missing_reason` fields for the LOSING candidates are `ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:...q_lcb=0.000017...` / `q_lcb=0.000000` — i.e. honest capital-efficiency on a zero q_lcb.
- **NULL source meaning:** `q_lcb_calibration_source` is NULL because no licensing override (EMOS or SETTLEMENT_ISOTONIC) ever stamped a source — the column is left unstamped on the canonical FORECAST path. NULL is the *default* state (62,789 of 62,874 receipts are NULL; only 85 ever got `FORECAST_BOOTSTRAP`). NULL is NOT a broken receipt path and NOT a distinct "unlicensed" failure mode — it is the normal absence of a source stamp.

**Verdict:** These receipts are `proof_accepted` candidates that died at SUBMIT (latch/mode-flip/submit-disabled), with the underlying admission winner being capital_efficiency on q_lcb≈0. No licensing gate recorded their non-submission. The synthesis's premise for GAP 3 (a broken cheap-tail receipt path or a licensing kill) is false.

---

### GAP 4 / GAP 7 — `confidence_band_insufficient` + `model_conflict`: is `yes_ci_lower_nonpositive` (q_lcb≤0) the upstream killer of cheap bins?

**Probe:** `no_trade_events` reason_detail for `confidence_band_insufficient`; receipt header q_lcb distribution for cheap-tail buy_yes; live-log "best" q_lcb distribution.

**Result:**
- `confidence_band_insufficient` (379) and `model_conflict` (336) in `no_trade_events` are **May data (05-23 → 05-28)** — NOT the live path (see GAP 1). Their `reason_detail` is `"0 edges found, 0 passed FDR; EDGE_SCAN_TRACE(decisions=no_na..."` — an FDR-edge-scan artifact, not a per-candidate `yes_ci_lower_nonpositive` stamp. So the *literal* `confidence_band_insufficient` gate is not active in the current path.
- BUT the **mechanism** GAP 7 describes (q_lcb collapsed to ≤0 killing cheap candidates upstream) is **REAL and is the dominant effect**, just under a different label. In the live cycle log, 115 of the displayed "best" candidates have `q_lcb=0.0000` exactly and reject with `ev_per_dollar=-1.0000` via `capital_efficiency_lcb_ev`. The aggregate `capital_efficiency_lcb_ev=18,829` is overwhelmingly this zero/near-zero-q_lcb class.
- Receipt headers for the 454 cheap-tail buy_yes carry the per-receipt DISPLAY winner's q_lcb (MIN 0.0196, AVG 0.234) which looks positive — this is the display/kill split: the header shows the best-display q_lcb while the killed candidates inside carry q_lcb≈0.

**Verdict:** GAP 7's substance is correct and decisive — **a q_lcb zero-floor IS the binding upstream constraint** — but it manifests as `capital_efficiency_lcb_ev` (q_lcb≤price after the floor collapses q_lcb to 0), not as a literal `confidence_band_insufficient` row. This is mechanism (b), and it dwarfs licensing ~150:1. The synthesis ignored this entirely.

---

### GAP 5 — is the B1 submit latch OPEN now?

**Probe:** `exchange_reconcile_findings WHERE resolved_at IS NULL`; latest M5 WS-gap line in `logs/zeus-live.log`.

**Result:**
- **0 unresolved findings now.** Most recent finding `a5fcfe36...` recorded AND resolved `2026-06-13T22:05` by `src.execution.exchange_reconcile`.
- Log timeline: the M5 latch was **CLOSED** ("kept submit latch closed", `m5_findings_unresolved`) repeatedly from 06-13T23:32 through 06-14T00:59, then **CLEARED** at `2026-06-14T01:06:43` ("M5 WS-gap reconcile cleared submit latch", `m5_reconcile_complete`).
- After the clear, the allocator is CONFIGURED and the live-bridge lane is active (`EDLI live-bridge allocator refresh: CONFIGURED ... bankroll=1222`) on every cycle 01:44–01:58.

**Verdict:** B1 latch is **OPEN now** (cleared 06-14T01:06, autonomously, per the keep-invariant). The submit lane is unblocked. Yet `proof_accepted=0` on every post-clear cycle — proving the constraint is NOT the latch and NOT submit-stage; it is upstream at admission (capital_efficiency). The latch is a real-but-transient and now-cleared blocker, NOT the binding constraint.

---

### GAP 6 — was `state/emos_ci_license.json` ever populated (deleted vs never-built)?

**Probe:** `git log --all -- state/emos_ci_license.json`; grep for where it is written.

**Result:**
- `git log --all -- state/emos_ci_license.json` returns **empty** — the file was **never committed**.
- No code path WRITES the file; it is only READ (`emos_ci_license.py:72` `load_emos_ci_license`) and the boot guard `main.py:998-1029` explicitly says **"Operator must populate state/emos_ci_license.json"** — it is an operator-armed artifact, not a built one.

**Verdict:** **NEVER-BUILT, by design.** It is an operator-arm input gated behind a default-OFF flag (`edli_emos_ci_live_enabled`). Restoring/"materializing" it (synthesis option K1a) is neither a regression-repair nor sufficient — the flag is off, and per operator law (memory: no shadow/gate-mass) building a new license artifact + flag is exactly the gate-accretion the operator forbids.

---

### GAP (mid-band) — 0.2–0.6: real edge or base-rate?

**Probe:** `SELECT direction, AVG(q_lcb_5pct - c_cost_95pct), COUNT(*) FROM edli_no_submit_receipts WHERE c_cost_95pct BETWEEN 0.2 AND 0.6 GROUP BY direction`.

**Result:**
- All-time: `buy_no` avg edge **+0.475** (n=3,034); `buy_yes` avg edge **+0.120** (n=251).
- Recent (06-10+): a single `buy_no` row (n=1, +0.154). The mid-band buy_yes population is effectively absent in the current window.
- The huge `buy_no` mid-band "edge" (+0.475) is the conservative LCB clearing a favorite already priced as a favorite — base-rate (operator law #5), not alpha. The high-bin (>0.6) buy_no population (54,776 rows, all positive-edge) confirms the table is dominated by base-rate favorite-buying.

**Verdict:** Contradiction C5 is **CONFIRMED** — the only durable "edge" band is base-rate buy_no, not tradeable alpha. Unblocking submission re-enables base-rate favorite-buying, not the cheap-longshot alpha the synthesis targets.

---

## CORRECTED CAUSAL CHAIN

1. **Admission stage (the real bottleneck):** ~88% of all candidate rejections are `capital_efficiency_lcb_ev`, overwhelmingly because **q_lcb has collapsed to ≈0** on the candidates the market prices cheap. With q_lcb≈0, conservative EV = (q_lcb−price)/price ≈ −1, an honest reject. This is mechanism (b) and is the #1 binding constraint.
2. **Licensing stage (`coverage_unlicensed_tail`):** a genuine fail-closed guard, but it fires on only ~0.6% of rejections (123 lifetime in the log; 0 in receipts) — the 1–2 cheap-tail material-disagreement candidates per cycle (Panama +15, Lucknow +5, Tokyo +18). Real, tiny, NOT the cause of the no-fill.
3. **Submit stage:** when admission DID pass historically (the 62,874 `proof_accepted` receipts), submission was blocked by `real_order_submit_disabled` / `SUBMIT_ABORTED_MODE_FLIPPED` / the now-cleared M5 latch. As of 06-14T01:06 the latch is open, but admission produces `proof_accepted=0`, so submit never gets a candidate.

**Net:** The system is not "de-licensing detected +EV." It is **correctly rejecting candidates whose conservative q_lcb is ≈0** (no proven edge after the LCB floor), with a negligible licensing tail on top, behind a submit latch that is now open and idle for lack of an admitted candidate.

---

## IMPLICATION FOR THE PLAN

The synthesis's K1 (re-route/restore the licensing lane) addresses **0.6% of the problem** and would, even if it worked, only admit the 1–2 cheap-tail candidates per cycle whose realized edge is unproven (its own rank-2 kill-criterion). Do **NOT** build the EMOS license file or re-route `coverage_unlicensed_tail` as the headline fix.

The real question the plan must answer is **why q_lcb collapses to ≈0 on cheap candidates** (the `capital_efficiency_lcb_ev`=18,829 mass) — i.e. the calibration/LCB-floor producing a zero lower bound exactly where a cheap stake would pay multiples. That is a calibration-coverage / LCB-construction question (mechanism b), upstream of every licensing concern, and it is what the synthesis omitted. Whether a non-zero honest q_lcb even EXISTS for these cheap bins (vs. the edge being genuinely unproven / base-rate per C5) is the true targeted-fix-vs-rebuild fork — and the C5 evidence leans toward "the durable edge is base-rate, not cheap-longshot alpha," meaning the honest answer may be that there is little real cheap-bin alpha to unblock.

*End of confirmation. Read-only; no code or daemon changes made.*
