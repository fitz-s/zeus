# Entry-gate correctness audit — is selection systematically correct per price bucket?

- Created: 2026-06-22
- Last audited: 2026-06-22
- Authority basis: READ-ONLY audit of the live decision gate
  (`src/decision/family_decision_engine.py`, `src/decision/payoff_vector.py`,
  `src/strategy/utility_ranker.py`, `src/decision/qlcb_reliability_guard.py`) vs
  real runtime data (`state/zeus-world.db` settlement_attribution; `state/qlcb_oof_reliability.json`;
  `logs/zeus-live.log` SELECT_GATE_DIAG). No edits, no live changes, entry pause untouched.

## VERDICT (one line)

Entry selection is **NOT systematically correct by price bucket** — it is *q_lcb-calibration
correct*, with **no price-bucket-aware toxicity defense**. The gate does **not** structurally
reject the toxic `.70-.90 buy_no` favorite class (the dU gate is mathematically equivalent to
the edge gate at the margin, not a stricter favorite filter), it **does** wrongly suppress the
genuine-edge `.05-.50 buy_yes` class (qlcb guard abstains for thin OOF evidence + direction-law
blocks 50 fully-economics-passing YES candidates in the live log), and it admits the toxic
`dust <0.05 buy_yes` class. Resuming entries with this gate would re-enter favorite-tax and
dust losers and keep alpha suppressed. The brief's premise — "conservative pi_lose=q_ucb_yes
makes optimal_delta_u<0 for `.70-.90 buy_no` → rejected" — is **false**; see §2.

## 0. Realized edge by entry class (reproduced from settlement_attribution)

`state/zeus-world.db` (NOT zeus_trades — that copy is empty; matches K1 DB-split). 133 graded
positions, settled 2026-06-13..06-22.

| direction | bucket | n | win_rate | avg_price | realized_edge |
|---|---|---|---|---|---|
| buy_no  | .50-.70  | 55 | 0.673 | 0.635 | **+0.038 GENUINE** |
| buy_no  | .70-.90  | 43 | 0.628 | 0.752 | **−0.124 TOXIC (favorite tax)** |
| buy_no  | >=.90    | 6  | 1.000 | 0.959 | +0.041 (tiny n) |
| buy_yes | dust<.05 | 12 | 0.000 | 0.008 | **−0.008 TOXIC (0% win)** |
| buy_yes | .05-.50  | 17 | 0.353 | 0.282 | **+0.071 GENUINE** |

Exact match to the verified facts in the brief. **Provenance caveat:** the toxic `.70-.90 buy_no`
fills carry target dates 2026-06-07..06-19 — *entered* under the pre-fix regime
(`effective_outcome_pi` landed 06-08; direction-law relaxation 06-15). These settled losses are a
**mixed regime** and do NOT by themselves prove the *current* gate admits the favorite tax — the
current-gate verdict comes from code construction (§2) + the live log + the live guard artifact.

## 1. The live gate IS FamilyDecisionEngine.decide() (confirmed)

- `config/settings.json:251` `qkernel_spine_enabled: true` → the live per-family decision is
  `FamilyDecisionEngine.decide()` via `src/engine/qkernel_spine_bridge.py` (bridge),
  wired into `src/engine/event_reactor_adapter.py:3160`.
- SELECT_GATE_DIAG lines (`family_decision_engine.py:1224`) are the current gate's output;
  most recent 2026-06-22 14:43. **They fire ONLY on no-trade cycles** (inside the
  `if not survivors:` branch, `:1197`) — so the log is a no-trade-only view; an admitted trade
  emits no diag. All 763 diag cycles show `live=0` (no candidate cleared the full live pass in
  the logged window, consistent with the entry pause holding deep-OTM off).

The gate filter chain (`family_decision_engine.py:1124-1271`, `_select`):
`executable → direction_law_ok (OR guard-licensed) → coherence_allows → (edge_lcb>0 AND optimal_delta_u>0) → live_candidate_passes (re-proof incl. delta_u_at_min>0) → argmax utility-density`.

## 2. Per-bucket gate verdict BY CONSTRUCTION (the crux)

### 2a. edge_lcb gate: `edge_lcb = q_lcb_side − cost`, admit iff > 0

`payoff_vector.py:298-319` (`edge_lower_bound`) + `:790-797`. For a buy_no at price `c`,
`edge_lcb ≈ q_lcb_no − c`; for buy_yes, `≈ q_lcb_yes − price`. The certificate's
`q_live`/`q_lcb_5pct` are **side-space position-win probabilities** (verified: buy_no @0.84 →
q_live=1.0, q_lcb=0.98 = P(NO wins); resolver `settlement_skill_attribution.py:645-701` reads them
from the immutable ActionableTradeCertificate, no `side` field — already side-resolved).

Overlay on the 54 graded fills that carry decision-time q_lcb (`edge_lcb proxy = q_lcb_5pct − price`):

| direction | bucket | n | realized_edge | mean edge_lcb | % would-pass edge |
|---|---|---|---|---|---|
| buy_no  | .50-.70  | 26 | +0.135 | +0.160 | 100% |
| buy_no  | .70-.90  | 20 | **−0.157** | **+0.094** | **100%** |
| buy_no  | >=.90    | 4  | +0.040 | +0.040 | 100% |
| buy_yes | dust<.05 | 2  | −0.015 | +0.068 | 100% |
| buy_yes | .05-.50  | 2  | −0.360 | +0.075 | 100% |

**Every bucket — including the toxic `.70-.90 buy_no` (realized −15.7¢, decision-time edge_lcb
+9.4¢) — shows 100% would-pass-edge.** The decision-time q_lcb was over-confident in exactly the
toxic region. The edge gate does NOT discriminate the favorite class.

### 2b. dU gate: `optimal_delta_u > 0` (the claimed favorite defense) — FALSE

`payoff_vector.py:639-735` (`optimize_vector_stake`) maximizes the robust ΔU over band draws using
the side-conservative `effective_outcome_pi` (`utility_ranker.py:386-474`): for NO_i, loss-mass on
own bin = `1 − q_lcb_no = q_ucb_yes`, win-mass = `q_lcb_no`. Payoff geometry (`utility_ranker.py:35-36`):
NO_i pays `s·(1−c)/c` on a win, `−s` on a loss.

**Marginal ΔU at infinitesimal stake** = `s·[ q·(1−c)/c − (1−q) ] / A`, which is `> 0` **iff
`q_lcb_no > c`** — the *identical* condition to `edge_lcb > 0`. Numeric check (baseline A=1000):

| bucket | c | q_lcb_no | edge_lcb | dU(s=1) | dU(s=10) | verdict |
|---|---|---|---|---|---|---|
| .50-.70 NO | 0.635 | 0.795 | +0.160 | +0.00025 | +0.00250 | ADMIT |
| .70-.90 NO | 0.752 | 0.851 | +0.099 | +0.00013 | +0.00130 | **ADMIT** |
| >=.90 NO   | 0.959 | 1.000 | +0.041 | +0.00004 | +0.00043 | ADMIT |

The conservative `pi_lose=q_ucb_yes` is *already baked into* `q_lcb_no = 1 − q_ucb_yes`. If that
conservative bound still exceeds price, **both** edge and dU pass. The dU gate is *marginally*
stricter than edge only at the finite-stake boundary (discrete grid + ruin + exposure concavity) —
the live log shows 57 NO instances with `e>0 & dU<=0` (all near-zero edge), confirming a thin
boundary band, **not** a price-bucket-aware favorite filter. The dU gate does NOT systematically
reject `.70-.90 buy_no`.

### 2c. direction-law (`family_decision_engine.py:477-505`)

- NO_i: legal iff bin ≠ forecast (settlement) bin. A `.70-.90 buy_no` is on a non-forecast bin →
  **direction-law admits it.**
- YES_i: legal iff bin == forecast bin OR `point_q >= 0.05` (`CALIBRATED_NONMODAL_Q_FLOOR`, :474).
  A `.05-.50 buy_yes` typically has point_q ≥ 0.05 → admitted; a `dust<.05 buy_yes` with
  q_in_bin≈0.12 also clears the 0.05 floor → **admitted.**

### 2d. qlcb_reliability_guard (`src/decision/qlcb_reliability_guard.py`) — ACTIVE, and it is the
real per-bucket behavior driver

Artifact `state/qlcb_oof_reliability.json` **exists** (06-18, 314 cells) → guard is ACTIVE (not
inert). Cell key `(metric|lead|side|bin_position|q_lcb_bucket)`, uniform 0.05 buckets. Rule
(`:29-30`): trade iff `N_g >= N_MIN(30)` AND realized Wilson-LB `>= bucket_floor − EPS(0.02)`;
else deflate `q_safe` or ABSTAIN. Ran the live guard against representative per-bucket inputs:

| class | served q_lcb | q_safe | abstain | cell | effect |
|---|---|---|---|---|---|
| .70-.90 NO favorite | 0.85 | 0.850 | **False** | NO\|nonmodal\|qb17 (hit 0.95, n=1852) | **passes through — NOT defended** |
| .70-.90 NO favorite | 0.82 | 0.820 | **False** | NO\|nonmodal\|qb16 (hit 0.90, n=1786) | passes through |
| .50-.70 NO genuine | 0.795 | 0.795 | False | NO\|nonmodal\|qb15 (hit 0.87, n=1533) | passes (correct) |
| **.05-.50 YES genuine** | 0.435 | **0.000** | **True** | YES\|nonmodal\|qb8 (n=**1**) | **ABSTAINS — suppressed** |
| dust YES toxic | 0.083 | 0.083 | False | YES\|nonmodal\|qb1 (hit 0.155, n=1454) | passes → edge +0.075 → admits |
| >=.90 NO | 1.0 | 0.999 | False | NO\|nonmodal\|qb19 | passes |

The guard is keyed on **q_lcb, not price**, so it cannot see price-conditioned adverse selection.
Its OOF table reports NO-nonmodal high-q_lcb cells realize 0.87-0.95 (n=1500-1850) — well above
floor → **licensed**. But the 43 actual `.70-.90` fills won only 62.8%: the OOF hit-rate measures
P(NO-claim correct) over *all* candidates, while the fills are the *price-selected* subset. The
guard's blind spot is exactly the favorite tax.

## 3. LIVE LOG evidence (SELECT_GATE_DIAG, no-trade cycles only)

Cross-tab of every diag candidate by sign(edge_lcb) × sign(dU):

| period | side | e>0 & dU>0 | e>0 & dU<=0 | e<=0 | max_e | max_dU |
|---|---|---|---|---|---|---|
| all-time | NO | 29 | 57 | 757 | +0.220 | +0.095 |
| all-time | YES | 51 | 1 | 1643 | +0.010 | +0.003 |
| >=06-20 | NO | 1 | 47 | 410 | — | — |
| >=06-20 | YES | 2 | 1 | 978 | — | — |

- `live>=1` cycles = **0** of 763 (diag is no-trade-only; admitted trades emit no diag).
- Candidates with `e>0 AND dU>0 AND dUmin>0` (full-economics pass) that the cycle still dropped:
  **50 YES blocked by direction-law (adm=0), 0 NO blocked.** This is the suppressed-alpha
  signature on the YES side — genuine-edge YES that cleared edge+dU but failed the direction/guard
  license. NO candidates with positive economics are never direction-blocked (NO on non-forecast
  bins is always legal).

## 4. THE GAP LIST (explicit)

**Toxic classes that still PASS the gate (would re-enter losers if pause lifts):**

1. **`.70-.90 buy_no` (favorite tax, −12.4¢, n=43)** — NOT structurally rejected. edge gate,
   dU gate, direction-law, and the qlcb guard *all admit* it when decision-time q_lcb_no > price
   (which it was, mean +9.4¢). The only thing stopping it is whether the *live* q_lcb for a given
   candidate undercuts price — a per-candidate q-quality lottery, not a class defense. **Highest-priority gap.**
2. **`dust <0.05 buy_yes` (−0.8¢, 0% win, n=12)** — admitted: q_lcb 0.083 > price 0.008 → edge
   +0.075; direction-law clears (point_q ≥ 0.05 floor); guard licenses (qb1, n=1454). Dollar
   impact small but the gate does not reject it by construction.

**Genuine-edge classes WRONGLY rejected (suppressed alpha):**

3. **`.05-.50 buy_yes` (+7.1¢ GENUINE, n=17)** — the qlcb guard **ABSTAINS** it: its q_lcb region
   (≈0.28-0.44, qb5-qb8) has thin/empty YES-nonmodal OOF evidence (qb8 n=1, qb5 n=18, all < N_MIN=30),
   because high-q_lcb YES is rare under 1°-wide bins (YES win-mass concentrates in qb0, n=34295,
   hit 0.014). Live log: 50 YES candidates passed edge+dU but were direction/guard-blocked. The
   alpha is real (settlement-graded +7.1¢) but the guard has no evidence cell to license it.

**Correctly handled:**

4. `.50-.70 buy_no` (+3.8¢) — admitted (q_lcb_no 0.795 > price, guard licenses qb15). Correct.
5. `>=.90 buy_no` (+4.0¢, n=6) — admitted. Correct but n too small to trust.

## 5. Root cause

The gate is **calibration-correct, not bucket-correct.** Every admit/reject reduces to
`q_lcb_side vs price` (edge ≡ marginal-dU) plus a q_lcb-keyed (price-blind) reliability guard.
There is no price-bucket-aware toxicity term anywhere in the chain. So:
- the favorite tax survives whenever decision-time q_lcb_no is over-confident at high price
  (price-conditioned adverse selection the q_lcb-keyed guard cannot see), and
- the genuine `.05-.50 YES` alpha is starved because the OOF table lacks evidence in the q_lcb
  region where YES edge lives.

Fixing this requires either price-conditioned reliability cells (add a price/cost dimension to the
guard so the favorite region is graded on its *realized fill* outcomes, not all-candidate hit-rate)
or seeding YES-nonmodal mid-q_lcb OOF evidence — NOT a price cap/allowlist (operator no-caps law).
This audit is read-only; no remedy implemented.
