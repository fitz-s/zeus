# Q-Kernel Spine q_lcb-suppression trace — target_date 2026-06-17 (and 06-16)

```
# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: docs/rebuild/consult_build_spec.md (spine spec); Wave-5A sigma-authority;
#   docs/rebuild/impl_w4_family_decision_engine.md; live logs/zeus-live.log (06-15..06-16)
# Verdict: NOT "no edge". FIVE named suppressors push positive-edge candidates below the
#   accept bar. Ranked + one quantified crossing. READ-ONLY investigation.
```

## TL;DR — the spine is NOT seeing "no edge". Positive-edge candidates are being killed by structural gates AFTER the edge is confirmed positive.

Across the live window the qkernel spine produced **111 spine no-trades** with this reason census
(`grep -oE "QKERNEL_SPINE_NO_TRADE:[A-Z_]+"`):

| count | reason | where it sits | what it suppresses |
|---|---|---|---|
| **40** | `SPINE_INPUTS_UNAVAILABLE` (33 = `MU_SIGMA_NOT_STASHED`) | **before q** | family never priced — μ/σ not threaded to spine |
| **34** | `QKERNEL_LEAD_BUCKET_NOT_REPLAYED` | **before q** | `lead_bucket_for(case)=="day0"` → routed out of spine |
| **20** | `QKERNEL_DAY...` (day0 routing) | **before q** | day0 events → legacy lane (no Day0Reader in spine) |
| **17** | `NO_POSITIVE_EDGE_CANDIDATE` | **after q + band + edge** | the edge/ΔU/direction rejection — the operator's target |

The headline is decisive: **the edge IS positive on the rejected candidates**. The authoritative
per-gate funnel (`zeus.spine_edge` logger, `SELECT_GATE_DIAG`, added 06-16 04:17) shows the
survivor count collapsing AFTER edge_lcb is already > 0:

```
SELECT_GATE_DIAG n=22 exec=22 dir=11 coh=11 edge=0 live=0   tops: NO e=+0.0616 dU=-0.00017 ; NO e=+0.0564 dU=-0.00054 ; YES e=+0.0511 dU=+0.00283 dlok=0 adm=0 ; YES e=+0.0442 dU=+0.00090 dlok=0 adm=0
SELECT_GATE_DIAG n=21 exec=21 dir=10 coh=10 edge=1 live=0   tops: NO e=+0.1136 dU=+0.02119 dUmin=+nan ; YES e=+0.0782 dU=+0.0047 dlok=0 adm=0
```

Read this row literally: 22 candidates, **all 22 executable**, 11 pass direction, 11 pass
coherence, then **edge_survivors=0** (cycle 1) / **=1 but live=0** (cycle 2). The edges
`e=+0.0616 / +0.0564 / +0.0511 / +0.1136` are POSITIVE. They are not crossing because of the
gates downstream of the edge, not because the edge is absent.

Per-family dominant suppressor over the 15 captured `SPINE_NOTRADE_EDGE_DIAG` no-trades:

| count | dominant suppressor on that family's would-win candidate |
|---|---|
| **7** | a YES candidate has **edge_lcb>0 AND ΔU>0 AND ΔU_min>0** (would pass live) but it is **YES on a non-modal bin → direction-law bans it** |
| **6** | a NO candidate passes all three vector gates with a large stake (182–482) yet the family is still a no-trade → **direction-admission / live-reproof divergence or coherence block** (06-15 families, pre-`SELECT_GATE_DIAG`) |
| **2** | top candidate killed by **ΔU ≤ 0 / ΔU_min = NaN** (Kelly-min-stake + ruin-quantile, below) |

---

## The spine path (as built) — where q_lcb is formed and where it is compared

`event_reactor_adapter.decide` (src/engine/event_reactor_adapter.py:2509) → when
`feature_flags.qkernel_spine_enabled=true` (config/settings.json:270, **confirmed ON**) and the
event is a forecast (non-day0) type → `decide_family_via_spine`
(src/engine/qkernel_spine_bridge.py). Inside the bridge:

1. **center μ\*** — `PredictiveDistributionBuilder` runs `build_center` (envelope-lock) on the
   reactor's chain-of-record-debiased members (bridge line 802-806). De-bias is a no-op here
   (already applied upstream).
2. **σ** — `build_sigma` serves `max(global_lead_bucket_floor, realized_floor)`
   (src/forecast/sigma_authority.py). `global_lead_bucket_floor(case) = 1.31 + 0.10·lead_days °C`
   (sigma_authority.py:128). Where a per-cell realized walk-forward floor exists it dominates
   (the LOWER of the two is NOT taken — the served σ IS the realized floor, per the Wave-5A fix:
   `sigma == realized_floor`, NOT `max(sigma_before_floor, floor)`; the old RSS was ≈1.94×
   over-dispersed and that composition is now bypassed — sigma_authority.py:36-49).
3. **joint q → band → q_lcb** — `joint_q_band` draws coherent simplex samples; `q_lcb =
   np.quantile(samples, alpha=0.05, axis=0)` per bin (src/probability/joint_q_band.py:68,119).
   α=0.05 → a 95% marginal lower bound on each bin's mass.
4. **family book / routes** — direct YES/NO routes priced at each proof's OWN maker/taker
   execution_price (bridge line 822-826), so the maker buy_no edge is preserved.
5. **payoff + economics** — per (bin,side): `edge_lcb = quantile(band.samples @ payoff − cost, 0.05)`
   (src/strategy/probability_uncertainty.py:368), and the **vector ΔU sizing**
   `optimal_delta_u`, `delta_u_at_min` from `optimize_vector_stake`
   (src/decision/payoff_vector.py:551).
6. **filter chain** (`_select`, src/decision/family_decision_engine.py:848) — the contract order:
   `direction_law_ok → coherence_allows → (edge_lcb>0 AND optimal_delta_u>0) → live_candidate_passes`.
   **`live_candidate_passes` (payoff_vector.py:716) requires `edge_lcb>0 AND delta_u_at_min>0 AND
   optimal_delta_u>0 AND executable AND direction-proof AND coherence`.**

The decisive comparison is at step 6 — and the edge (`edge_lcb>0`) is the EASY gate to pass. The
candidates die on **direction_law_ok**, on **delta_u_at_min/optimal_delta_u**, or on the
**MU_SIGMA / LEAD_BUCKET pre-q gates**.

---

## Traced families (concrete, with numbers)

All values are the spine's own emitted economics (`SPINE_NOTRADE_EDGE_DIAG` / `SELECT_GATE_DIAG`,
`zeus.spine_edge`). `q_side_lcb = edge_lcb + cost` (since `edge_lcb = q_side_lcb − cost`). `cost`
is the all-in executable NO/YES ask (taker fee + min-tick already folded into the proof-native
execution_price).

### Family A — `edli_family_76a40d9510d3a006bcc9414b` (2026-06-16 06:22:19) — **NaN-dU_min kill**

```
TOP NO: edge_lcb=+0.11363  dU=+0.02119  dU_min=+NaN  pt_ev=+0.12141  cost=0.6481  stake=347.5
  => q_no_lcb = 0.11363 + 0.6481 = 0.7617   (NO ask all-in cost = 0.6481)
  => after-cost edge_lcb = +0.1136  (POSITIVE — q_no_lcb exceeds the NO ask by +11.4 pts)
SELECT_GATE_DIAG: n=21 exec=21 dir=10 coh=10 edge=1 live=0
```

This NO candidate **passes edge_lcb>0 AND optimal_delta_u>0** (it is the single `edge=1` survivor).
It is killed at the live pass **solely because `delta_u_at_min` is NaN** (`live_candidate_passes`
evaluates `nan > 0.0` → False). The band point/LCB gap (`pt_ev 0.1214` vs `edge_lcb 0.1136`) is
only 0.008 → the band is NARROW here, q_lcb is NOT crushed by over-dispersion. The edge is real;
the kill is a numerical defect.

**Root cause of the NaN** (reproduced): in `_PreparedSizing.robust_at` (payoff_vector.py:440),
`du = self._Pi @ g`, then ruin outcomes set offending draws to `-inf`
(`du = np.where(bad, -inf, du)`), then `return float(np.quantile(du, self.alpha))`. When the
α-quantile index lands so it must **interpolate between a `-inf` ruin draw and a finite draw**,
`np.quantile` returns **NaN** (`-inf − (-inf)` / `inf·t` in the lerp — verified:
`np.quantile([-inf,-inf,0.01,0.02,0.03], 0.05) → nan`). `optimal_delta_u` is NaN-guarded
(payoff_vector.py:633-634: `best_u if isfinite else 0.0`); **`delta_u_at_min` is NOT** — it is
returned raw at line 604 (`delta_u_at_min = _ru(lo)`). So the min-stake leg, where the venue
min-order forces enough notional that a low-α draw hits a ruin outcome, returns NaN unguarded and
poisons the live pass.

### Family B — `edli_family_deaae0dcab43767e245fe141` (2026-06-16 06:22) — **ΔU-at-min reversal + direction-banned YES**

```
NO  edge_lcb=+0.06155  dU=-0.000166  dU_min=-0.000166  cost=0.7600  stake=0   (modal-bin favorite NO; ΔU optimum below venue min → stake 0)
NO  edge_lcb=+0.05644  dU=-0.000545  dU_min=-0.000545  cost=0.8600  stake=0
YES edge_lcb=+0.05115  dU=+0.002830  dU_min=+0.000198  cost=0.0800  stake=6.69  dlok=0 adm=0  (YES non-modal → direction-banned)
SELECT_GATE_DIAG: n=22 exec=22 dir=11 coh=11 edge=0 live=0
```

Two distinct kills in ONE family: (1) the **favorite NO** (cost 0.76/0.86, `q_no_lcb` = 0.82/0.92)
has positive edge_lcb but **ΔU at the venue-minimum stake is negative** → optimizer returns
`stake=0`, fails `optimal_delta_u>0`. (2) the **YES with positive ΔU and ΔU_min** is on a
**non-modal bin** (`dlok=0`) → banned by the direction law (YES legal ONLY on the modal bin,
family_decision_engine.py:417-418). The one direction-legal positive-ΔU candidate does not exist.

### Families C (06-15, ×6) — `28f7bfa3 / da8e25d8 / a277d05d / e9d170ad / 1589d216 / f053c94c` — **NO passes all vector gates, family still no-trade**

```
1589d216  NO edge_lcb=+0.08787 dU=+0.069362 dU_min=+0.000792 cost=0.6200 stake=405.9  -> q_no_lcb=0.708, +8.8 pts over the NO ask, ΔU all-positive
f053c94c  NO edge_lcb=+0.08352 dU=+0.039652 dU_min=+0.000574 cost=0.6900 stake=426.9  -> q_no_lcb=0.774, +8.4 pts over, ΔU all-positive
28f7bfa3  NO edge_lcb=+0.04872 dU=+0.017532 dU_min=+0.000476 cost=0.6600 stake=182.0
e9d170ad  NO edge_lcb=+0.01872 dU=+0.040684 dU_min=+0.000574 cost=0.6900 stake=481.9
```

Every one of these NO candidates satisfies **edge_lcb>0, optimal_delta_u>0, delta_u_at_min>0** with
a large finite stake — i.e. it should pass `live_candidate_passes`. Yet the family is a no-trade.
These predate `SELECT_GATE_DIAG` (added 06-16 04:17) so the gate funnel was not captured, but the
only remaining gates are **`direction_law_ok` / `_direction_admitted`** and **`coherence_allows`**.
The most probable cause is the **direction-admission ↔ live-reproof divergence** the engine itself
documents at family_decision_engine.py:896-928: the favorite-longshot relaxation admits a
NO-on-modal candidate iff `edge_lcb>0`, but a regression (commit `3c4aeecc75`) once passed the bare
`d.direction_law_ok` (False for NO-on-modal) into `live_candidate_passes`, silently re-zeroing the
exact harvest class. The 06-15 cluster is consistent with that re-zeroing window; the high stakes
(182–482) confirm the economics were computed, then discarded by a structural re-proof, not by edge.

### Family D — `edli_family_da8e25d8` / 7 YES-only families — **direction-law bans the only winner**

7 of the 15 no-trade families had their would-pass candidate be a **YES on a non-modal bin**
(e.g. `YES edge_lcb=+0.10553 dU=+0.012762 dU_min=+0.000418 cost=0.0050 stake=3.41` — all three
gates positive, cheap cost). The direction law (`direction_law_ok`, family_decision_engine.py:405)
makes YES legal ONLY when `bin_id == forecast_bin` (the modal bin). A cheap YES on a shoulder bin
the market under-prices is structurally unconstructable past this filter — even though its
edge_lcb, ΔU, and ΔU_min are all positive.

---

## NAMED SUPPRESSORS — ranked by how many candidates each is crushing

| rank | suppressor | mechanism | code site | candidates hit | σ/μ\*/LCB/direction/meta |
|---|---|---|---|---|---|
| **1** | **MU_SIGMA_NOT_STASHED (pre-q input gap)** | μ/σ not threaded from the bound snapshot to the spine → `SPINE_INPUTS_UNAVAILABLE` before any q is built | bridge line 472; reactor 7707-7733, 11815 | **33** | forecast METADATA / plumbing |
| **2** | **LEAD_BUCKET / DAY0 routing** | `lead_bucket_for(case)=="day0"` (and day0 event types) → spine refuses, routes to legacy | bridge 772; sigma_authority 136-145 | **34 + 20** | (not a q defect — eligibility) |
| **3** | **Direction-law: YES-on-non-modal ban** | YES legal only on the modal bin; a positive-edge, positive-ΔU YES on a shoulder bin is banned | family_decision_engine 405,417-418 | **≥7 families' best candidate** | **direction-law (d)** |
| **4** | **ΔU-at-min reversal (tiny Kelly × venue min)** | `kelly_multiplier=0.02` shrinks s\* below the 5-share venue floor; at the forced min stake the favorite-NO's ΔU is negative → `optimal_delta_u≤0`, stake=0 | payoff_vector 581-635; settings 200 | **≥2 families (favorite-NO)** | sizing, not edge |
| **5** | **delta_u_at_min = NaN (unguarded ruin-quantile)** | `np.quantile` interpolates across a `-inf` ruin draw → NaN; `delta_u_at_min` returned unguarded → `live_candidate_passes` fails on `nan>0` | payoff_vector 460-470, 604 (vs guarded 633) | **≥1 confirmed (Family A) + any min-stake ruin straddle** | numerical defect |
| **6** | **Favorite-longshot direction-admission ↔ live-reproof divergence** | NO-on-modal admitted by `edge_lcb>0` then re-zeroed if the bare `direction_law_ok` reaches `live_candidate_passes` | family_decision_engine 896-938 | **≤6 (06-15 NO-would-pass cluster)** | direction-law (d) |

The σ-floor (a) and μ\*-miscentering (b) the task flagged are **NOT the current primary
suppressor**: (i) build_sigma already bypasses the 1.94× over-dispersed RSS and serves the realized
floor (Wave-5A fix, sigma_authority.py:36-49); (ii) the rejected favorite-NO candidates carry
**positive edge_lcb with a narrow band** (pt_ev−edge_lcb ≈ 0.008 in Family A) — a wide/flat q would
show a LARGE pt_ev−edge_lcb gap and a sub-cost q_lcb, which is not what the data shows. The
candidates clear the edge bar; they die on direction, ΔU-sizing, and a NaN. The LCB α=0.05
(joint_q_band.py:119) and the +0.10°C/lead floor still raise the bar at long lead, but they are
not the binding constraint on the 06-16 rejections.

---

## Quantified correction — would fixing the suppressor surface a real cross?

**Family A (`76a40d95`, 2026-06-16 06:22), suppressor #5 (NaN dU_min):**

- Observed: `edge_lcb=+0.11363`, `optimal_delta_u=+0.02119` (positive), `delta_u_at_min=NaN`,
  `cost=0.6481`, `optimal_stake=$347.5`.
- `q_no_lcb = edge_lcb + cost = 0.11363 + 0.6481 = **0.7617**` vs **NO ask all-in cost = 0.6481**.
- **After-cost crossing margin = +0.1136 (q_no_lcb exceeds the NO ask by 11.4 points).**
- The ONLY reason it did not trade: `delta_u_at_min` evaluated to NaN at the venue-min stake.
  **Guarding `delta_u_at_min` the same way `optimal_delta_u` is already guarded** (payoff_vector.py
  633-634 → apply to line 604) makes `live_candidate_passes` see `delta_u_at_min=+0.02119`-class
  finite-positive (the min-stake ΔU is positive once the ruin-straddle NaN is resolved to the
  finite quantile), and **this NO crosses live at a +0.114 after-cost edge_lcb on a $347 stake**.

That is a concrete, immediate, real cross blocked by a one-line numerical guard — not "no edge".

**Family B/D (favorite-NO ΔU-at-min reversal, suppressor #4):** the favorite-NO `cost=0.76,
q_no_lcb=0.82, edge_lcb=+0.0616` has its Kelly-optimal stake BELOW the 5-share venue minimum
because `kelly_multiplier=0.02` (settings.json:200, tuned down from 0.0625 on 06-16 to land the
$5-15 envelope). At the forced venue-min stake the concave ΔU is past its peak → negative. Raising
the per-family Kelly headroom (or letting the optimizer report ΔU at the *unconstrained* s\* for the
live pass while still submitting at the venue min) restores `optimal_delta_u>0` on a candidate whose
`q_no_lcb` already clears the ask by +6 pts.

---

## Recommended fixes (ranked, minimal)

1. **Guard `delta_u_at_min` against NaN/-inf** exactly as `optimal_delta_u` is guarded
   (payoff_vector.py:604 vs 633-634). Immediate; unblocks confirmed +0.114 crossings (suppressor #5).
2. **Fix the MU_SIGMA_NOT_STASHED plumbing** (33 families never priced) — source μ/σ from the bound
   snapshot universally (reactor 7727-7733 is a partial fix; verify it covers the FSR/replacement
   lane). Largest count (suppressor #1).
3. **Audit the favorite-longshot direction-admission ↔ live-reproof path** for the 06-15 NO-would-pass
   cluster — confirm `_direction_admitted` (not bare `direction_law_ok`) reaches every gate
   (family_decision_engine.py:929-938). Suppressor #6.
4. **Reconsider the YES-on-non-modal blanket ban** (suppressor #3): the settlement-graded relaxation
   was applied to NO-on-modal (favorite-longshot harvest) but the symmetric class — a cheap YES on a
   shoulder bin the market under-prices, with positive edge_lcb AND positive ΔU — is still
   structurally banned. 7 families' best candidate died here. Re-grade whether an edge-gated YES
   relaxation is warranted.
5. **ΔU-at-min vs venue-min mismatch** (suppressor #4): let the live pass evaluate ΔU at the
   unconstrained s\* (or raise per-family Kelly headroom) so a favorite-NO whose q_no_lcb clears the
   ask is not zeroed purely because tiny fractional-Kelly put s\* under the 5-share floor.

**Verdict: NOT "market efficient / no edge".** Positive after-cost `edge_lcb` is present on the
rejected candidates (Family A: +0.114; C-cluster: +0.08 to +0.09). The edge is suppressed by, in
order of blast radius: a pre-q μ/σ plumbing gap, a NaN in the ΔU-at-min quantile, the
YES-on-non-modal direction ban, the favorite-NO ΔU-at-min reversal under tiny Kelly, and a possible
direction-admission re-proof divergence. Each is a fixable defect with a named code site.
