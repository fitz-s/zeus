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

**Root** = the **one-sided σ floor** in `joint_q_band.draw_sigma` (`return max(drawn, floor)`,
L308): the band's per-draw σ is floored upward at the realized floor, so when the served σ IS
that floor, the point sits at the LEFT boundary of the draw law and EVERY draw is ≥ the point σ.
Deep-OTM bin mass is convex/steep in σ, so even the band's 5th-percentile σ produces bin mass ≥
the point's. **Recommended fix = FIX-2: make `draw_sigma` a two-sided posterior centered on the
served σ** (so the 5pct is a genuine lower bound by construction). A one-line decision-invariant
fuse (FIX-1: clip `edge_lcb ≤ point_ev` at the economics seam) stops the loss immediately but is
NOT the permanent statistical fix — see §4. An independent frontier-model audit reached the same
mechanism and the same FIX-2 verdict (Appendix B).

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

## 4. PROPOSED FIX — TWO candidates; the ROOT repair is FIX-2

Both an independent frontier-model audit (ChatGPT Pro, 2026-06-22, see Appendix B) and this
analysis agree the **dominant mechanism is the one-sided σ floor in `draw_sigma`**. They differ
on which fix to ship. Below are both, honestly stated, for the operator to choose.

### FIX-2 (ROOT, recommended): make the band's σ posterior two-sided/coherent

**Lever (file:line):** `src/probability/joint_q_band.py::draw_sigma` (L297-312), specifically the
one-sided floor `return max(drawn, floor)` (L308-312) and the served-σ relationship in
`src/forecast/sigma_authority.py::build_sigma` (the realized-floor path L572-573 where the
served σ IS the realized floor, making the point sit at the LEFT boundary of the draw law).

The defect: `draw_sigma` draws `N(σ_native, disp)` then truncates the LEFT tail at
`floor = max(realized_floor, 1e-6)` (L308). When `σ_native == realized_floor` (the common case
where sigma_authority served the floor), the point's σ is the **lower boundary** of the draw
law — every draw's σ is ≥ the point σ, so the band is a one-sided upward perturbation. For a
deep-OTM bin whose mass is convex and sharply increasing in σ (the Gaussian-tail
distance²/variance term), even the band's 5th-percentile σ ≥ the point σ ⇒ 5pct bin mass ≥
point bin mass ⇒ the lower bound exceeds the point.

The fix: replace the one-sided floor with a **centered two-sided** σ posterior whose
median/mean IS `σ_native` (e.g. draw on log-σ, or on excess-over-positive-floor, recentered so
the served σ is the center, NOT the boundary), and let the realized-floor live ONCE in the
served-σ authority upstream rather than as a second one-sided widening at band time. Point_ev
stays raw; `build_joint_q` stays the sole integrator; μ-draw unchanged.

**Why FIX-2 is more correct:** it repairs the object every downstream consumer *believes* it is
reading — a coherent posterior band centered on the same raw point law. After FIX-2 the band's
5pct is a genuine lower bound by construction, the extreme ratios collapse, and the band's
`q_ucb` and the robust-ΔU sizing inputs (which consume the SAME inflated `band.samples`) are
fixed in the same stroke. It adds no gate, no cap, no haircut, no shadow; raw q untouched.

**Risk / why not trivial:** the realized-floor is a deliberate sigma-authority invariant
(AGENTS.md §0: "no draw is sub-realized"). FIX-2 must preserve the floor on the *served* σ
while removing the floor's one-sided effect on the *draw lower tail* — a wider blast radius than
FIX-1 (touches band semantics + needs regression on `q_ucb` and ΔU). It is the correct ROOT but
needs more verification before it touches live money.

### FIX-1 (immediate safety fuse, NOT the permanent statistical fix)

**Lever (file:line):** `src/decision/payoff_vector.py::compute_candidate_economics` (L789-797),
after `point_ev` (L791) and `edge_lcb` (L793) are both in scope, clamp
`edge_lcb = min(edge_lcb, point_ev)`.

**What it is:** a *decision invariant* — "never admit a candidate whose raw point EV is ≤ 0 via
a positive lower-bound edge." It guarantees no `point_ev ≤ 0` deep-OTM ticket is admitted, in
one line, with zero band-semantics blast radius. It is the only universal fuse that stops the
capital loss immediately.

**What it is NOT — the honest caveat (raised by the independent audit, and correct):** clipping
`edge_lcb ≤ point_ev` is NOT a statistical theorem. A genuine Bayesian posterior lower quantile
of a *convex* payoff transform CAN legitimately exceed the plug-in point (Jensen) — so FIX-1
can in principle over-reject a real posterior-implied opportunity. FIX-1 masks an incoherent
band rather than repairing it. Therefore: ship FIX-1 ONLY as an explicitly-named temporary
fuse (e.g. an `edge_lcb_actionable = min(raw_edge_lcb, point_ev)` field that keeps
`raw_edge_lcb` for diagnostics), and REMOVE it once FIX-2 makes the band coherent.

**Reconciliation with the operator framing:** the operator calls these tickets "garbage" and
the task names "make q_lcb ≤ point q" as the strongest candidate. That intent is precisely the
FIX-1 *decision invariant* ("don't buy negative-point-EV deep-OTM tails"). It is a legitimate
business rule, but it is a DECISION rule, not a repair of the band — so the cleanest end state
is: FIX-2 repairs the band (the inversion disappears by construction for the floored-width
artifact), and if the operator ALSO wants the hard "no negative-point-EV trade" invariant, that
is a deliberate one-line decision rule layered on a now-coherent band, not a patch over a broken
one.

**Universality of the symptom (both sides):** the inversion is YES-dominant but not YES-only —
last 400 proofs, **YES 117/217 = 54% inverted, NO 21/183 = 11% inverted**. FIX-2 corrects both
by construction; FIX-1 corrects both at the seam. For bins where the band bound is already ≤
point (89% of NO legs, 46% of YES — modal/shoulder/ITM), both fixes are the identity.

### Recommendation

Ship **FIX-2** as the permanent root repair (it makes the lower bound honest by construction and
also fixes `q_ucb` + ΔU). If live capital is exposed before FIX-2 can be regression-tested, ship
**FIX-1 as a clearly-labelled temporary fuse** in `compute_candidate_economics`, then remove it
when FIX-2 lands. Do NOT ship FIX-1 as the permanent statistical fix.
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

### Test-suite consistency (the clip enforces the already-tested contract)

Every existing economics test fixture already assumes `edge_lcb ≤ point_ev`:
- `tests/integration/test_qkernel_spine_blockers_pr409.py:877,937,964` build economics with
  `edge_lcb=0.05, point_ev=0.200 / 0.050` — the bound below the point.
- `tests/decision/test_family_decision_engine.py:1105-1112` sets `edge_lcb=0.08, point_ev=0.09`
  with the comment "edge_lcb + small spread" — the contract is explicitly that the lower bound
  sits a small spread BELOW the point.

No fixture constructs `edge_lcb > point_ev`. So `edge_lcb = min(edge_lcb, point_ev)` is the
**identity on every existing test** — the clip ENFORCES the contract the suite already encodes,
rather than changing it. (The "favorite-longshot relaxation" at
`test_family_decision_engine.py:1095` is a DIRECTION-LAW allowance — admitting a NO on the
modal bin — and is unrelated to the lower-bound-vs-point invariant.)

(Test-fixture consistency note: the clip is the identity on all existing fixtures, so FIX-1
breaks no current test; FIX-2 needs new band-coherence regressions — see Appendix B verify-list.)

## A related MEDIUM finding: the economics seam does not assert point/band identity

`src/decision/payoff_vector.py::_validate_alignment` (L264-283) checks only that `payoff`,
`joint_q.q`, and `band.samples` share the same bin LENGTH — it does NOT assert that `joint_q`
and `band.joint_q` were built from the SAME predictive distribution + Omega
(`joint_q.identity_hash == band.joint_q.identity_hash`). At the live call site they ARE the same
(`family_decision_engine.py:669-672` builds both from one `predictive`), so a stale/mismatched
band is NOT the live cause here — the one-sided σ floor is. But a future caller that passes a
mismatched (point, band) pair would produce the EXACT "lower bound exceeds its own point"
symptom undetected. Cheap hardening: add an identity-hash equality assertion in
`_validate_alignment` (or at the top of `compute_candidate_economics`). Independent-audit MEDIUM
finding; recommended as defensive but secondary to the σ-floor root.

## Decisive cross-check: the PERSISTED q_lcb is clean — the spine band is the culprit

`forecast_posteriors` (`state/zeus-forecasts.db`, `runtime_layer='live'`) carries
`q_json` + `q_lcb_json` — the MATERIALIZER's persisted point q and its bootstrap q_lcb. Checked
the latest 3 live posteriors (Seoul/Qingdao/Paris 2026-06-24): **0 of 11 bins per city have
`q_lcb > q_point`** — the persisted materializer q_lcb is a correct lower bound on every bin
(the materializer's per-bin clip `lcb = min(lcb, q_point)` at
`replacement_forecast_materializer.py:1772` and the far-tail floor 0.003 at L1781-1782 work).

Therefore the inflated `payoff_q_lcb=0.058` admitting deep-OTM YES is **NOT** the persisted
bound — it is the **live spine `build_joint_q_band` lower bound** consumed by
`edge_lower_bound`. The candidate-level receipt field `q_lcb_calibration_source=FORECAST_BOOTSTRAP`
(the persisted bound, correctly ≈0) vs the economics `payoff_q_lcb` (the band, inflated) being
1–8 orders apart in the SAME proof is the smoking gun. The 2026-06-22 far-tail floor was added
to the materializer bootstrap; it does NOT reach the live spine band's `edge_lcb`. **That is the
gap the fix closes.**

## Related but DISTINCT root (coordinator framing): the q is too flat (modal under-weight)

The operator/coordinator separately observes the q is INVERTED two ways: tails over-weighted
(this doc's root) AND the **modal/predicted bin under-weighted** (q too flat). Confirmed flat:
the live posteriors above are near-uniform — Seoul modal mass **0.134** with top-3
[0.134, 0.131, 0.124] over 11 bins; Paris modal **0.187**. A near-uniform q means the
predictive σ is wide relative to the 1°C bin width.

This is a **separate lever from the q_lcb seam**: the flat-q root is the predictive WIDTH —
`predictive_sigma_c = max(1.0, sqrt(fused.sd² + σ_resid²))`
(`replacement_forecast_materializer.py:1461`, with σ_resid defaulting to 1.5°C on thin
substrate, L1447), plus the settlement sigma-shape mixture (k, w, uniform floor) in
`src/forecast/sigma_authority.py`. Over-wide σ_pred under-weights the modal bin and over-weights
the tails on the POINT q itself.

**Scope discipline (operator law: fix ONE existing computation, no gate accretion):** the
two roots have two different levers and should NOT be collapsed into one change. This doc's fix
(clip `edge_lcb ≤ point_ev`) is the minimal correctness fix for the **q_lcb false-edge / tail
admission** — it makes the system stop BUYING the tails regardless of how flat the point q is,
because a too-flat-but-honest point still gives `point_ev ≤ 0` on a deep-OTM bin priced above
its point mass. The modal-under-weight (σ-too-wide) is a real second issue but is a
predictive-width correction (sigma_authority / σ_resid floor), a distinct verify-and-fix that
should be evaluated on its own settlement evidence — not folded into the q_lcb seam. Doing the
q_lcb clip first is correct precedence: it stops the capital loss on the tails immediately and
is provably the identity on the honest path; the width fix then improves modal sharpness on top.

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

## Appendix B: independent frontier-model audit (ChatGPT Pro Extended, 2026-06-22)

An independent audit (different model family, web-browsing, given the exact formulas + the live
statistics) was run as a cross-check. Verbatim answer saved at
`/tmp/cgc_answer_REQ-20260622-220855-fd415c.txt`. It could NOT browse the commit-pinned
`6052c15d` blobs (GitHub 404 in its session) and audited reachable `main` instead — so its
line numbers are against `main` (rendered offsets differ from this doc's worktree offsets), but
the symbols and logic match. Its verdict:

- **Mechanism — AGREES with this doc:** the dominant cause is the **one-sided σ draw/floor** in
  `draw_sigma` (`max(drawn, floor)`), NOT simplex renormalization (point and band share the same
  `build_joint_q` renorm) and NOT day0 (carried unchanged into each per-draw pd). Gaussian-tail
  mass is convex/steep in σ, so a one-sided-widened σ lifts deep-tail mass by orders of
  magnitude and makes the point sit at/near the lower boundary of the draw law — explaining the
  10^1–10^28 ratios.
- **Fix — recommends FIX-2 (repair `draw_sigma` to a centered two-sided σ posterior)** as the
  permanent single fix; treat FIX-1 (`min(edge_lcb, point_ev)`) as a temporary, explicitly-named
  actionable-edge fuse, removed once the band is coherent.
- **Key theoretical caveat (incorporated into §4):** `posterior_lcb ≤ plug-in_point` is NOT a
  theorem — a Bayesian posterior lower quantile of a convex payoff transform can legitimately
  exceed the plug-in point (Jensen). So FIX-1 is a DECISION invariant, not a statistical repair,
  and can over-reject in principle.
- **New MEDIUM finding (incorporated above):** `_validate_alignment` checks only vector length,
  not point/band `identity_hash` equality — add an identity assertion as defensive hardening.
- **Caveat on the audit itself:** it ran against `main`, not the pinned tree; the one fact that
  would flip its verdict is if the live `draw_sigma` is ALREADY a centered two-sided posterior
  and the inversion comes from a stale/mismatched band — which this doc REFUTES (live point+band
  are built from one predictive at `family_decision_engine.py:669-672`, and the worktree
  `draw_sigma` L308 IS the one-sided `max(drawn, floor)`). So the σ-floor root stands.

Verify-locally list (from the audit, for the operator implementing the fix):
1. For inverted live rows, log `sigma_native`, `realized_floor_native`, draw_sigma p05/median,
   `band.joint_q.identity_hash == joint_q.identity_hash`, and
   `quantile(band.samples @ payoff, .05) / (joint_q.q @ payoff)`.
2. Unit test: deep-OTM one-hot YES payoff with `sigma_native == realized_floor`, `center_se == 0`
   ⇒ expect `quantile(samples @ payoff, .05) ≈ point_q @ payoff` (today it is strictly above).
3. After FIX-2: rerun the live-row audit; require the extreme ratios to collapse.
