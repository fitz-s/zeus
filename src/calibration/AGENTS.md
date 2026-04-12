# src/calibration AGENTS — Zone K3 (Math/Data)

## WHY this zone matters

Raw ensemble probabilities are systematically biased — overconfident at long lead times, underconfident near settlement. Platt calibration corrects this bias using a three-parameter logistic: `P_cal = sigmoid(A·logit(P_raw) + B·lead_days + C)`.

The critical design decision: `lead_days` is an **input feature**, not a bucket dimension. This triples positive samples per training bucket (45→135) vs the 72-bucket approach. Without temporal decay, Zeus overtrades stale forecasts.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `platt.py` | Extended Platt calibrator + bootstrap | HIGH — core calibration engine |
| `manager.py` | Calibration lifecycle, maturity gates | HIGH — controls when calibration applies |
| `store.py` | Persistence of calibration parameters | MEDIUM |
| `effective_sample_size.py` | Decision-group calibration sample accounting | MEDIUM |
| `blocked_oos.py` | Blocked out-of-sample calibration evaluation facts | MEDIUM |
| `drift.py` | Calibration drift detection | MEDIUM |

## Domain rules

- **Maturity gates are safety-critical**: n < 15 → use P_raw directly (no fit). 15–50 → strong regularization (C=0.1). 50+ → standard fit
- 200 bootstrap parameter sets (A_i, B_i, C_i) feed σ_parameter in double-bootstrap CI — without them, edge CI is systematically too narrow → overtrading
- Logit clamping: P values clamped to [0.01, 0.99] before logit transform to prevent log(0)
- Shoulder bins (open-ended tails) stay in raw probability space, not width-normalized density

## Common mistakes

- Treating lead_days as a bucket/dimension instead of a Platt input feature → collapses sample count
- Skipping bootstrap parameter generation → edge CI too narrow → overtrading
- Changing maturity thresholds without understanding why they exist → calibrating on noise
- Normalizing shoulder bins by width → infinite density artifacts
