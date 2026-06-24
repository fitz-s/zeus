# dU<0 Blockade: Root Cause + Proposed Fix

**Date:** 2026-06-22  
**Token:** `930c414612dda0f1f4594ee4f5e7d7be6f1cf495f93cd3665aacf9630db2ff33`  
**Market:** highest-temperature-in-manila-on-june-23-2026  
**Side:** NO (38°C+ bin)  
**Symptom:** `edge_lcb=+0.00084` but `dU=-0.000420 = dU_min` → `proof_accepted=0` every cycle  

---

## 1. Runtime Values (from live SPINE_NOTRADE_EDGE_TELEMETRY)

```
2026-06-22 12:14:15 family=edli_family_2076fac2...
  930c4146... NO DIRECT edge_lcb=+0.00084 dU=-0.000420 dU_min=-0.000420 cost=0.9962 stake=0
2026-06-22 12:17:34
  930c4146... NO DIRECT edge_lcb=+0.00101 dU=-0.000420 dU_min=-0.000420 cost=0.9962 stake=0
```

`dU = dU_min` every cycle → optimizer found dU monotonically negative, best at lo, but lo<0.

---

## 2. Root Cause (file:line + evidence chain)

### 2a. Two separate q_lcb quantities diverge for NO candidates

**edge_lcb** (`src/decision/payoff_vector.py:796`):
```python
edge_lcb = edge_lower_bound(band, payoff, cost, alpha=alpha)
# = 5th-pct quantile of (band.samples @ payoff) - cost
# = 1 - q_lcb_yes_5pct - cost
# = 1 - 0.0029 - 0.9962 = +0.00084  (5th-pct YES draw ≈ 0.0029)
```

**candidate.q_lcb for NO side** (`src/engine/event_reactor_adapter.py:9084`):
```python
q_lcb = float(proof.q_lcb_5pct)   # for NO direction: = 1 - q_ucb_yes_95pct
```

`q_lcb_no = 1 - q_ucb_yes_95pct` where `q_ucb_yes_95pct` is the 95th-percentile YES draw.  
For Manila 38°C+ bin: model uncertainty is high in the upper YES tail.  
**q_ucb_yes_95pct ≈ 0.09** → `candidate.q_lcb ≈ 0.91`

### 2b. effective_outcome_pi uses candidate.q_lcb = 0.91, not 0.997

`src/strategy/utility_ranker.py:448`:
```python
own_q_lcb_no = float(candidate.q_lcb)   # = 0.91 (NOT 0.997)
own_mass = 1.0 - own_q_lcb_no           # = 0.09 (loss mass)
```

### 2c. Pi matrix has pi_lose = 0.09 for ALL 4000 draws → dU(lo) is always negative

At `lo = 0.9962 * 5 = $4.981`, flat baseline B = $1026.29:
```
g_win  = log(1026.29 + 0.019) - log(1026.29) = +1.85e-5
g_lose = log(1026.29 - 4.981) - log(1026.29) = -4.87e-3

dU(lo) = 0.91 * 1.85e-5 + 0.09 * (-4.87e-3)
       = +1.68e-5 - 4.38e-4
       = -4.21e-4   ✓  (matches log: -0.000420)
```

### 2d. Verification: analytic with correct pi_lose = 0.00296 gives positive dU

```
dU_correct = 0.99704 * 1.85e-5 + 0.00296 * (-4.87e-3)
           = +1.85e-5 - 1.44e-5
           = +4.1e-6  (positive → should trade)
```

### 2e. Monotone dU → optimizer returns (0, -4.21e-4, -4.21e-4)

`optimize_vector_stake` (`src/decision/payoff_vector.py:733`):
```python
if best_u <= 0.0 or best_s <= 0:
    return Decimal("0"), best_u, delta_u_at_min
```
→ `(0, -4.21e-4, -4.21e-4)` → `dU = dU_min = -4.2e-4` exactly as logged.

---

## 3. Causal Path (source)

```
proof.q_lcb_5pct (NO direction = 1 - q_ucb_yes_95pct)
    ↓
NativeSideCandidate.q_lcb = 0.91                  [event_reactor_adapter.py:9084]
    ↓
effective_outcome_pi: own_mass = 1 - 0.91 = 0.09  [utility_ranker.py:451]
    ↓
_PreparedSizing._Pi: pi_lose = 0.09 for ALL draws  [payoff_vector.py:517]
    ↓
robust_at(lo) = 0.91*g_win + 0.09*g_lose = -4.2e-4 [payoff_vector.py:543,551]
    ↓
optimize_vector_stake returns (0, -4.2e-4, -4.2e-4)  [payoff_vector.py:733]
    ↓
live_candidate_passes: optimal_delta_u <= 0 → NO TRADE [payoff_vector.py:828+]
```

### What ruled out:

- **Flat vs. non-flat exposure**: No open Manila positions. buy_yes on 29°C bin never filled (notional=$0.23 < $1 min). Exposure IS flat.
- **OOF guard deflation**: Cell `high|L1|NO|nonmodal|qb19` has L_g=0.99869 ≥ q_lcb_route=0.99704 → q_safe unchanged. Guard condition `guarded_edge < edge_lcb` = `0.00084 < 0.00084` = FALSE → no recompute.
- **lo == hi early return**: Manila NO book has 2 ask levels (0.998×155.61, 0.999×178.63) → lo≠hi, line 671 does NOT fire.
- **Ruin at lo**: lo=$4.98, B=$1026 → B-lo=$1021 > 0, no ruin.
- **Band alpha**: alpha=0.05 (5th pct). Even at 0.05, the 4000 draws all have pi_lose=0.09 (fixed by candidate.q_lcb), so all draws yield the same dU → 5th-pct = mean = -4.2e-4.

---

## 4. Magnitude Check

Reverse-engineering from dU=-4.2e-4:
```
pi_lose_effective = (dU - g_win) / (g_lose - g_win)
                  = (-4.2e-4 - 1.85e-5) / (-4.87e-3 - 1.85e-5)
                  = -4.39e-4 / -4.89e-3 = 0.0898

candidate.q_lcb = 1 - 0.0898 = 0.9102  ✓ (predicted 0.91 matches)
q_ucb_yes_95pct = 1 - 0.9102 = 0.0898  ✓ (9% upper tail consistent with band dispersion)
```

---

## 5. Architectural Nature of the Bug

This is a **calibration asymmetry** between edge_lcb and dU:

| Quantity | q_lcb used | For 38°C+ NO |
|---|---|---|
| `edge_lcb` | 5th pct of NO = `1 - q_ucb_yes_5pct` = 0.997 | +0.00084 |
| `candidate.q_lcb` | 5th pct of NO **proof** = `1 - q_ucb_yes_95pct` = 0.91 | drives dU = -4.2e-4 |

The `proof.q_lcb_5pct` field for a NO direction stores `1 - q_ucb_yes_95pct` — the MOST PESSIMISTIC NO bound (how likely is it to WIN on the WORST 5% of draws for YES). This is correct for conservative Kelly sizing in isolation, but combined with `effective_outcome_pi`'s `own_mass = 1 - candidate.q_lcb`, it sets pi_lose much higher than the actual 5th-pct loss probability.

The **direct conflict**: `edge_lcb` says trade (positive after-cost edge at 5th pct NO mass), but `optimize_vector_stake` says don't trade (negative ΔU when pi_lose = upper-tail YES mass = 9%). These two quantities are measuring the conservative probability on DIFFERENT tails of the same distribution.

---

## 6. Proposed Fix (DO NOT APPLY LIVE until team-lead approves)

### Option A: Use band-derived pi_lose in effective_outcome_pi (correct tail alignment)

In `effective_outcome_pi` (`utility_ranker.py:448`), `own_q_lcb_no` should come from the BAND's own 5th-pct NO draw, not `candidate.q_lcb`. Since `_PreparedSizing` already has the full band, pass `band_alpha_q_lcb_no` explicitly:

**Problem**: `effective_outcome_pi` is called inside `_PreparedSizing.__init__` per draw but `candidate.q_lcb` doesn't vary per draw. The fix needs to make `own_mass` draw-dependent.

Actually: for each draw k, the draw's own_bin mass IS `samples[k, own_bin_idx]`. The correct per-draw loss probability is `samples[k, own_bin_idx]` itself — not a fixed `1 - candidate.q_lcb`.

**Root alignment fix** (`payoff_vector.py:508-518`): replace the call to `effective_outcome_pi` with a per-draw version that uses `samples[k, own_bin_idx]` as the loss mass:

```python
for k in range(n_draws):
    pi = _draw_to_pi(samples[k, :], omega, matrix)
    # For NO: per-draw pi[own_bin] IS the loss mass (don't re-anchor to candidate.q_lcb)
    # For YES: per-draw pi[own_bin] IS the win mass (unchanged by effective_outcome_pi)
    # So: use pi directly, don't call effective_outcome_pi or _candidate_guarded_pi
    for j, y in enumerate(outcomes):
        Pi[k, j] = float(pi.get(y, 0.0))
```

But this removes the "NO overconfidence" correction (Hidden #3). The original fix was needed because `pi[own_bin]` from a raw YES-band draw equals `q_lcb_yes_5pct` (too optimistic for NO). The per-draw fix should instead anchor `own_mass` to the DRAW's own value:

```python
for k in range(n_draws):
    pi_raw = _draw_to_pi(samples[k, :], omega, matrix)
    # The per-draw loss mass for NO_i is the draw's own_bin probability
    # This is already conservative per-draw; no further anchoring to candidate.q_lcb
    eff_pi = _draw_conservative_pi(candidate, matrix, pi_raw)
    for j, y in enumerate(outcomes):
        Pi[k, j] = float(eff_pi.get(y, 0.0))
```

Where `_draw_conservative_pi` uses `pi_raw[own_bin]` as the loss mass (already the draw's own value).

### Option B: Use guarded_payoff_q_lcb from the certificate (targeted fix)

If `qkernel_execution_economics.payoff_q_lcb` is set correctly to the TRUE 5th-pct NO q_lcb (= 1 - q_lcb_yes_5pct = 0.997), then passing it as `guarded_payoff_q_lcb` would override `candidate.q_lcb` in `_candidate_guarded_pi`. This path already exists (`payoff_vector.py:511-515`).

**Requires**: the qkernel certificate must emit `payoff_q_lcb = 1 - band_q_lcb_yes_5pct` (the correct NO lower bound), not `1 - q_ucb_yes_95pct`.

### Option C: Decouple candidate.q_lcb from sizing

Set `candidate.q_lcb` for NO to the BAND's 5th-pct NO probability (= `1 - q_ucb_yes_5pct` at `alpha_complement = 1 - 0.05 = 0.95` of YES). Currently `proof.q_lcb_5pct` for NO = `1 - q_ucb_yes_95pct` which is too conservative.

**Recommended fix**: Option B via certificate payoff_q_lcb, as the certificate path already exists. The qkernel spine should stamp `payoff_q_lcb = quantile(1 - band_q_no_5pct, ...)` using the same alpha as edge_lcb. This aligns the sizing's loss probability with the edge measurement's tail.

---

## 7. Impact

- ALL Manila 38°C+ NO candidates blocked (and any other near-certain NO where q_ucb_yes_95pct >> q_lcb_yes_5pct)
- Affects high-uncertainty bins where band spread is large (model disagrees on tail probability)
- `edge_lcb > 0` but `dU_min < 0` is the fingerprint

**Source files:**
- `src/decision/payoff_vector.py:448,510-518` (Pi matrix construction)
- `src/strategy/utility_ranker.py:448-451` (effective_outcome_pi own_mass)
- `src/engine/event_reactor_adapter.py:9084` (q_lcb from proof.q_lcb_5pct)
