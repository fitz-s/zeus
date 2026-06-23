# Toxic deep-OTM cheap-YES admissions: the q_lcb-above-point false-edge root

- Created: 2026-06-23
- Last audited: 2026-06-23
- Authority basis: AGENTS.md §0 Probability Authority (raw-q, q_lcb conservative floor); operator
  laws (no caps / no haircuts / no new gate / no shadow / raw q is sole authority);
  diagnosis task 2026-06-23 (q_lcb false-edge root).
- Scope: DIAGNOSIS ONLY. No code changed. The fix below is a proposal for the operator to
  verify + implement.

## TL;DR

The live admission gate trades on `edge_lcb = payoff_q_lcb − cost > 0` and **ignores
`point_ev`**. `payoff_q_lcb` is the 5th-percentile of the **JointQBand** payoff value
(`quantile_0.05(band.samples @ payoff)`), while `point_ev` uses the **point JointQ**
(`point_q @ payoff − cost`). For deep-OTM bins the band's 5th-percentile lower bound
**EXCEEDS the point estimate** — a lower bound above its own point. That manufactures false
edge on cheap deep-OTM YES tickets and admits them at zero/negative point EV.

**Inversion is REAL and large.** Last 400 `DecisionProofAccepted` proofs, cheap buy_yes
(cost ≤ 0.12), n=217 selected economics:

- `payoff_q_lcb > q_dot_payoff` (lower bound exceeds the point): **117 / 217 = 54%**,
  median ratio **11.8×**, max **8.5e28×**.
- `point_ev ≤ 0` admitted purely on `edge_lcb > 0`: **94 / 217 = 43%**.

Worked live row (Ankara, zeus-world.db `edli_live_order_events`,
`decision_audit.qkernel_execution_economics`):
`cost=0.0073`, `q_dot_payoff=6.22e-16` (point ≈ Dirac, bin ~8σ out),
`payoff_q_lcb=0.0580`, `edge_lcb=+0.0510`, `point_ev=−0.0073`. Admitted at **negative
point EV** because `edge_lcb > 0`.

## 1. file:line — where each quantity is computed

### The economics (point_ev, edge_lcb, q_dot_payoff)
`src/decision/payoff_vector.py::compute_candidate_economics` (def L742; computation L789-820):

```
789  cost = _route_cost_value(candidate_route.route_cost)
790  q_dot = point_fair_value(joint_q, payoff)        # POINT q @ payoff
791  point_ev = q_dot - cost
792  q_guard = _validate_guarded_payoff_q_lcb(guarded_payoff_q_lcb)
793  edge_lcb = (
794      float(q_guard) - cost                         # guarded path (reliability deflation)
795      if q_guard is not None
796      else edge_lower_bound(band, payoff, cost, alpha=alpha)   # BAND 5pct quantile path
797  )
...
812      point_ev=point_ev,
813      edge_lcb=edge_lcb,
817      q_dot_payoff=q_dot,
```

- `point_fair_value` (L286-295): `float(joint_q.q @ payoff)` — the POINT JointQ dot payoff.
- `edge_lower_bound` (L298-319): `np.quantile(band.samples @ payoff - cost, alpha)` with
  `alpha = band.alpha = 0.05` — the BAND (JointQBand) 5th-percentile of the payoff value.

### payoff_q_lcb is reverse-derived from edge_lcb
`src/engine/qkernel_spine_bridge.py:1356-1357`:
```
1356  edge_lcb = float(selected.edge_lcb)
1357  payoff_q_lcb = edge_lcb + cost_value      # == quantile_0.05(band.samples @ payoff)
```
and stamped into `qkernel_execution_economics` (L1361-1376) with `point_ev` (L1369),
`q_dot_payoff` (L1373). The receipt-identity check
`src/engine/event_reactor_adapter.py:1510` asserts `payoff_q_lcb == cost + edge_lcb`.

### The JointQBand (the inflated lower bound's source)
`src/probability/joint_q_band.py::build_joint_q_band` (L363-445):
- `point_q = build_joint_q(pd, omega)` (L407) — the point.
- per draw: `mu_k = draw_mu(pd, rng)` (L416), `sigma_k = draw_sigma(pd, rng)` (L417),
  `pd_k = replace(pd, mu_native=mu_k, sigma_native=sigma_k)` (L422),
  `q_k = integrate_all_bins(pd_k, omega)` (L425).
- `q_lcb = np.quantile(samples, alpha, axis=0)` (L429).
- `draw_sigma` (L297-312): `drawn ~ N(sigma_native, disp)` then
  **`return max(drawn, floor)`** where `floor = max(realized_floor, 1e-6)` (L308). The
  floor is **ONE-SIDED — it only ever WIDENS sigma**, never narrows it.
- `_sigma_draw_dispersion` (L247-267): the draw-sigma dispersion (the perturbation around
  the served width).

### The point JointQ
`src/probability/joint_q.py::build_joint_q` (L217-306): integrates `N(mu_native, sigma_native)`
over the settlement preimage and renormalizes (L298-299 `q = q / q.sum()`).

### The admission gate
`src/decision/payoff_vector.py::live_candidate_passes` (L828-861):
```
854  return (
855      economics.edge_lcb > 0.0
856      and economics.delta_u_at_min > 0.0
857      and economics.optimal_delta_u > 0.0
858      and candidate_route.route_cost.executable
859      and direction_law_proof_present
860      and market_coherence_accepted
861  )
```
`point_ev` is **NOT** in the gate. It is explicitly demoted to telemetry
(`scalar_trade_score`, L864-877; "logged, not acted on"). The spine prefilter mirrors this:
`src/engine/qkernel_spine_bridge.py:1395` `"passed_prefilter": edge_lcb > 0.0`.

### The reliability guard (the other q_lcb; a `min`, cannot fix this)
`src/decision/qlcb_reliability_guard.py::apply_guard` (L309-...): `q_safe = min(band_q_lcb, L_g)`
(spec L21; served L331) where `L_g` = one-sided Wilson-95 LB of the cell's realized OOF
hit-rate. Wired in `src/decision/family_decision_engine.py:1068-1105`:
`q_lcb_route = edge_lcb + cost` (L1069) → guard → recompute. **The guard is a `min`: it can
only LOWER band_q_lcb, never raise it; and it operates at coarse bucket granularity
(`qlcb_bucket(band_q_lcb)`, `bin_position` ∈ {modal, nonmodal} only — L350-353), so it does
not see per-bin far-tail distance.** Live rows carry `q_lcb_guard_basis=OOF_WILSON_95`,
`q_lcb_guard_abstained=False` — the guard is ACTIVE and licenses these admissions because its
bucket-level `L_g` ≥ the inflated `band_q_lcb` (the `min` leaves the inflated value intact).

## 2. CONFIRMED: payoff_q_lcb can (and does) exceed the point q

**Confirmed — empirically and structurally.**

Empirically: 117/217 cheap buy_yes rows have `payoff_q_lcb > q_dot_payoff` (above). The
candidate-level materializer bound (`q_lcb_calibration_source=FORECAST_BOOTSTRAP`,
`candidates[*].q_lcb_5pct`) is correctly ≈0 for these bins (e.g. Munich `3.78e-07`, Madrid
`5.27e-09`, Shenzhen `5.46e-03`), yet the **spine economics `payoff_q_lcb` is 0.058–0.117** —
1–8 orders of magnitude higher. So the inflated bound is the **JointQBand** lower bound used
by `edge_lower_bound`, NOT the materializer's persisted `forecast_posteriors.q_lcb`.

### Why the band 5pct exceeds the point (the mechanism)

For a deep-OTM YES bin i, `payoff = e_i`, so `point_q @ payoff = q_point_i` and
`band.samples @ payoff = samples[:, i]`. The point integrates `N(mu*, sigma_native)`; the
band integrates `N(mu_k, sigma_k)` per draw with **`sigma_k = max(N(sigma_native, disp), floor)`**.

The floor (`draw_sigma` L308) is **one-sided** — it truncates the LEFT tail of the sigma draw
but leaves the RIGHT (wider) tail intact. Therefore:

1. `E[sigma_k] > sigma_native` (the draw distribution is right-shifted by the floor truncation
   plus any realized_floor > sigma_native). The band's *typical* predictive is **wider** than
   the point's.
2. For a far-tail bin (μ* many σ away), the per-bin mass `mass_i(sigma)` is **convex and
   sharply increasing in sigma** — a wider sigma piles far-tail mass that a narrow sigma does
   not (a 1.6× sigma widening at z=8 moves the bin mass by ~10^14). By Jensen, with the
   one-sided-widened `sigma_k`, the band's bin-i mass is **systematically and dramatically
   higher** than the point's `mass_i(sigma_native)`.
3. Because EVERY draw's sigma is floored upward, even the **5th-percentile** of the band's
   bin-i mass sits ABOVE the point mass: the point uses `sigma_native`; the band's 5pct draw
   uses `max(low_sigma_draw, floor) ≥ floor ≥ sigma_native`. So
   `quantile_0.05(samples[:,i]) ≥ mass_i(floor) ≥ mass_i(sigma_native) = q_point_i` for a
   far-tail bin where mass is monotone increasing in sigma.

That is the structural break: **a lower bound (5th percentile) built over draws whose width is
floored one-sided ABOVE the point's width can never be a true lower bound for a far-tail bin —
it is bounded BELOW by the floored-width mass, which exceeds the point mass.** The `mu_k`
wander alone does NOT cause this (it makes the 5pct fall below the point, as expected); the
**one-sided sigma floor** is the dominant lift. The simplex renorm and day0 conditioning are
secondary (renorm denominator ≈ 1 over the full Omega; day0 is carried identically into
`pd_k`).

Worked number (far-tail YES bin, μ* ≈ 8σ out, cost ≈ 0.0073, matching the Ankara live row):
- point: `sigma_native ≈ 1.5` → `q_point_i ≈ 6e-16` (≈ the live `q_dot_payoff=6.22e-16`).
- band: `sigma_k` floored at `realized_floor` (settlement-graded, ~2–4°C for far leads) and
  right-skewed → the bin's mass across draws has its 5pct pinned near `mass_i(floor) ≈ 0.058`
  (≈ the live `payoff_q_lcb=0.058`).
- result: `edge_lcb = 0.058 − 0.0073 = +0.051 > 0` admits, while
  `point_ev = 6e-16 − 0.0073 = −0.0073 ≤ 0`. The inversion is the floored-width far-tail mass.

## 3. WHY live trades on edge_lcb while point_ev ≤ 0

`point_ev` is **computed but never gated** — it is intentionally telemetry-only
(`payoff_vector.py` L864-877 `scalar_trade_score`; demotion rationale L101-105, 823-826). The
ONLY admission inputs are the vector `edge_lcb` / `delta_u_at_min` / `optimal_delta_u`
(`live_candidate_passes` L854-861) and the spine prefilter `edge_lcb > 0`
(`qkernel_spine_bridge.py:1395`). The design intent was that `edge_lcb`, being the
*conservative lower bound*, dominates the looser `point_ev`; that intent is **violated** when
the lower bound is inflated above the point. So the gate is not "ignoring a good signal" — it
is trusting a bound that is silently no longer a lower bound.

`delta_u_at_min`/`optimal_delta_u` do not save it: the robust ΔU sizing
(`_PreparedSizing` / `optimize_vector_stake`, L455-735) consumes the SAME inflated
`band.samples` (via `_candidate_guarded_pi` / `effective_outcome_pi`), so a bin with inflated
band mass also shows positive robust ΔU at the venue-min stake.

## 4. PROPOSED FIX (single, universal, law-compliant)

**Make `edge_lcb` a genuine conservative lower bound by construction: it can never exceed the
point edge.** The lower bound of the payoff value must be ≤ the point value — that is the
definition of a lower bound. Equivalently `payoff_q_lcb ≤ q_dot_payoff`.

**Lever (file:line):** `src/decision/payoff_vector.py::compute_candidate_economics`, at the
`edge_lcb` assignment (L793-797). After computing `edge_lcb` and `point_ev`, clamp:

```
edge_lcb = min(edge_lcb, point_ev)
```

(equivalently clamp the band path `edge_lower_bound(...)` and the guarded path
`q_guard - cost` to never exceed `q_dot - cost`). This is a one-line correctness clip at the
single seam where both quantities already exist (`point_ev` L791, `edge_lcb` L793, both in
scope before the `CandidateEconomics` build L810).

### Why this is the right fix and law-compliant

- **It fixes an EXISTING computation, adds NO new gate.** `edge_lcb` is *already documented as
  a lower bound* (L298-306 "robust lower credible bound"); a lower bound exceeding its point is
  a bug in that computation. The clip restores the invariant the code already claims. This is
  the operator's "fix an existing gate to be correct, do not add gate mass" path verbatim.
- **No cap / haircut / allowlist / price-band / throttle.** It is not a notional cap or a
  q-haircut — it never touches the point q / μ / sizing magnitude; it only enforces
  `lower_bound ≤ point` on the bound itself.
- **Raw q stays the sole authority; point_ev stays honest.** `q_dot_payoff` (raw point q) is
  untouched and becomes the ceiling for its own lower bound — the most honest possible
  reference. No market-anchor, no shadow, no flag.
- **Universal — helps ALL bins and BOTH sides, not one order type.** For any bin where the
  bound exceeded the point (deep-OTM tails), `edge_lcb` drops to `point_ev` and the bin
  self-rejects when `point_ev ≤ 0`. The inversion is YES-dominant but not YES-only: last 400
  proofs, **YES 117/217 = 54% inverted, NO 21/183 = 11% inverted** — the clip corrects both.
  For bins where the band bound is already ≤ point (89% of NO legs, 46% of YES), the clip is
  the **identity — zero behavior change on the legitimate path** (modal / shoulder / in-the-money
  legs are untouched).
- **Does it over-reject a legitimate bin?** No. A true lower bound is BY DEFINITION ≤ the point
  estimate; there is no legitimate case where the 5th-percentile of a bin's mass should exceed
  the bin's point mass. Any case where it does is the floored-width artifact. So the clip can
  only remove false edge, never real edge. (Net direction on the live book: it removes the
  buy_yes deep-OTM garbage and the 11% of NO legs whose bound also over-claimed; on the bins
  where the bound is already a true lower bound it is the identity, so the legitimate modal/NO
  book is not perturbed.)

### Downstream-invariant consistency (verified)

The clip is consistent with every downstream consumer:
- `payoff_q_lcb` is reverse-derived as `edge_lcb + cost` (`qkernel_spine_bridge.py:1357`)
  AFTER the clip, so the receipt identity `payoff_q_lcb == cost + edge_lcb`
  (`event_reactor_adapter.py:1510`) still holds.
- The clamped `payoff_q_lcb = min(old, q_dot_payoff) ≤ 1.0` still satisfies the range check
  (`event_reactor_adapter.py:1504`).
- A clipped-negative `edge_lcb` (from `point_ev ≤ 0`) correctly fails the
  `edge_lcb <= 0.0 → return None` admission check (`event_reactor_adapter.py:1508`) — exactly
  the intended self-rejection of the deep-OTM tail, with NO new gate.

### Deeper alternative considered (and why the clip is preferred as the immediate fix)

FIX-2: make the band coherent with the point by removing the one-sided sigma floor inside
`draw_sigma` (L308) — use a **two-sided** sigma posterior so the band's 5pct is a genuine lower
bound by construction. This attacks the ROOT (the floored width) and would also fix the band's
`q_ucb` and the robust-ΔU sizing inputs. **However** the floor at `realized_floor` is a
deliberate sigma-authority invariant (AGENTS.md §0: "settlement sigma-shape floor… no draw is
sub-realized") — removing/loosening it re-opens an over-confident-narrow-sigma failure the
floor was built to prevent, and is a wider blast-radius change to the band semantics. The
clip (FIX-1) is the **minimal, provably-safe** correction that restores the lower-bound
invariant at the seam without weakening the sigma floor. Recommend FIX-1 now; consider FIX-2
(two-sided sigma uncertainty that preserves the realized-floor on the *served* sigma but not on
the *draw* lower tail) as a follow-up coherence improvement to the band's upside as well.

## Secondary note (NOT the primary deliverable): fill-observation dead

The operator observed real fills the system does not record (Shenzhen/Warsaw `local_only`,
`chain_shares=0`; 0 `UserTradeObserved` recently). In `edli_live_order_events` the
`UserTradeObserved` event (source_authority `user_channel`) is the fill-observation lane. Live
counts this run: `UserTradeObserved=87` total but the operator reports a gap since ~00:58 —
consistent with the user-channel WS not delivering. Path: the `user_channel` source authority
writes `UserTradeObserved` / `UserOrderObserved` into `edli_live_order_events`; a stalled WS
leaves the projection (`edli_live_order_projection`) on `local_only` with `chain_shares=0`.
This is a SEPARATE defect from the q_lcb root (it is fill ingest, not belief). file:line for
the user-channel writer not traced here — flagged for a dedicated fill-lane diagnosis
(see sibling `2026-06-23_fill_observation_dead_22h.md`).

## Evidence appendix (DB queries, read-only)

- DB: `state/zeus-world.db` (`?mode=ro`, `PRAGMA busy_timeout=10000`).
- Table: `edli_live_order_events`, `event_type='DecisionProofAccepted'`, JSON column
  `payload_json`, path `decision_audit.qkernel_execution_economics`.
- Cheap buy_yes (cost ≤ 0.12), last 400 proofs, n=217: 54% inversion (median 11.8×, max
  8.5e28×), 43% point_ev ≤ 0.
- Representative rows: Ankara (cost 0.0073, q_dot 6.2e-16, payoff_q_lcb 0.058, point_ev
  −0.0073); Munich (cost 0.0094, q_dot 9.9e-3, payoff_q_lcb 0.069, point_ev 0.0005); Madrid
  (cost 0.0157, q_dot 4.2e-2, payoff_q_lcb 0.096); Shenzhen (cost 0.0105, q_dot 3.1e-2,
  payoff_q_lcb 0.117).
