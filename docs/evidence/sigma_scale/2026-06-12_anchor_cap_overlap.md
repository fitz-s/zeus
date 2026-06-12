# Anchor-Cap vs σ-Scale Overlap Analysis
Generated: 2026-06-12T~16:00Z
Branch: fix/opportunity-book-selector
Authority: operator question 2026-06-12

## Context

Two q-corrections active simultaneously post 14:54Z:
1. **Market-anchor q_lcb cap** (`replacement_q_market_anchor_enabled=true`; alpha=0.4;
   fires for buy_no at dist ≤ 1.5 settlement steps from mode bin)
2. **σ-scale fit** (k=1.5833, w=0.2811 for C-family; F refused n=47<60 → k=1.0)

Question: Is the anchor cap (a) redundant, (b) still binding materially (double-shrink),
or (c) binding in a different regime the σ-fix does not cover?

## Data Model (exact, from code)

`event_reactor_adapter.py` lines 7142-7166:
- `q_lcb_no = 1 - q_ucb_yes`   (NO lower-bound = 1 minus YES bootstrap CI upper-bound)
- `q_model_no = 1 - q_yes`     (NO point estimate)

`market_anchor.py`:
- `q_anchor = alpha * q_model_no + (1-alpha) * q_market_no`  (alpha=0.4)
- Cap fires when `q_lcb_no > q_anchor`; capped output = q_anchor

## Why σ-Fix Does NOT Eliminate the Cap

σ-fix widens σ_pred by k=1.5833 → flattens the q_yes per-bin distribution →
**widens the YES bootstrap confidence interval** → raises q_ucb_yes →
raises `q_lcb_no = 1 - q_ucb_yes`.

The σ-fix **increases** q_lcb_no (wider CI → higher NO lower-bound).
Cap bind rates are therefore expected to be equal or higher post-fix, not lower.
The two corrections target orthogonal invariants:
- σ-fix: point-estimate calibration (mode-bin ratio 0.514 → ~0.961)
- Cap: lower-bound market-consistency (q_lcb_no ≤ alpha-blend of model+market)

## Real-Data Results

Source: `state/zeus-forecasts.db` (SELECT-only, mode=ro URI).
Rows: 980 posteriors; 2789 near-center bin slots (dist ≤ 1.5 steps from mode).
Pre-fix (k≈1.0): 2079 C slots + 597 F slots.
Post-fix (k≈1.5833): 113 C slots (only 24 post-fix posteriors in DB snapshot).
F_post: 0 rows (F-family fitting refused; no k=1.5833 rows exist).

Market NO price distribution (5743 conditions, from production log 2026-06-12):
  p10=0.620  p25=0.810  p50=0.970  p75=0.990  mean=0.867

### Cap Bind Rates by Family, Era, and Market Price

```
key          mkt_no      n  n_cap  bind%  d_mean   d_max
C_pre         0.620   2079   1882  90.5%  0.0864  0.2274
C_pre         0.730   2079   1276  61.4%  0.0419  0.1614
C_pre         0.810   2079    556  26.7%  0.0203  0.1134
C_pre         0.839   2079    293  14.1%  0.0135  0.0960
C_pre         0.867   2079     74   3.6%  0.0129  0.0792
C_pre         0.880   2079     41   2.0%  0.0124  0.0714
C_pre         0.930   2079      1   0.0%  0.0414  0.0414
C_pre         0.970   2079      1   0.0%  0.0174  0.0174
C_pre         0.990   2079      1   0.0%  0.0054  0.0054

C_post        0.620    113    100  88.5%  0.1188  0.1746
C_post        0.730    113     97  85.8%  0.0547  0.1086
C_post        0.810    113     66  58.4%  0.0188  0.0606
C_post        0.839    113     30  26.5%  0.0147  0.0432
C_post        0.867    113      9   8.0%  0.0124  0.0264
C_post        0.880    113      5   4.4%  0.0115  0.0186
C_post        0.930    113      0   0.0%     —       —
C_post        0.970    113      0   0.0%     —       —
C_post        0.990    113      0   0.0%     —       —

F_pre         0.620    597    558  93.5%  0.0865  0.2280
F_pre         0.730    597    400  67.0%  0.0383  0.1620
F_pre         0.810    597     99  16.6%  0.0263  0.1140
F_pre         0.867    597     24   4.0%  0.0294  0.0798
F_pre         0.930    597      8   1.3%  0.0310  0.0420
F_pre         0.970    597      7   1.2%  0.0093  0.0180

F_post        (all)      0      0     —     —       —
              (F-family k=1.5833 refused; no post-fix F rows in DB)
```

## Key Findings

1. **Cap still fires materially post σ-fix.**
   C_post at mkt_no=0.810: 58.4% bind rate, d_mean=0.019.
   C_post at mkt_no=0.730: 85.8% bind rate, d_mean=0.055.
   At the market p25 (mkt_no=0.810) the cap suppresses q_lcb_no in more than half of
   all near-center C-family bins.

2. **σ-fix INCREASES cap bind rate, not decreases.**
   C_pre vs C_post at mkt_no=0.730: 61.4% → 85.8%.
   C_pre vs C_post at mkt_no=0.810: 26.7% → 58.4%.
   Consistent with theory: wider σ → wider CI → higher q_ucb_yes → higher q_lcb_no →
   cap engages at higher market prices.

3. **No redundancy.**
   The cap prevents q_lcb_no from exceeding the alpha-blend of model+market regardless
   of how σ-wide the CI is. σ-fix makes this constraint *more* relevant, not less.

4. **F-family post-fix: no data yet.**
   F refused fitting (n=47 < 60). No k=1.5833 F rows exist. F_pre shows cap fires
   at competitive prices (93.5% at 0.620, 67% at 0.730) — if F ever gets fitted, expect
   similar or higher post-fix bind rates.

5. **Log coverage gap: pre-fix log ends 11:27Z; σ-fix deployed 14:54Z.**
   Pre-fix log: 135 activations, mean_delta=0.054, max=0.141.
   Post-fix log: zero coverage (deployment after log window closed).
   Real-data analysis above is the only post-fix evidence available.

## Verdict: INTERNALIZE

The market-anchor cap is **not redundant**.

- Orthogonal invariant to σ-fix (market-consistency vs calibration).
- Materially binding at competitive prices (mkt_no ≤ 0.880).
- σ-fix amplifies cap activation — deleting it would remove a constraint that is now
  *more* necessary, not less.
- Change "INTERIM antibody" label to permanent market-consistency constraint.
- Retain `replacement_q_market_anchor_enabled` flag.
