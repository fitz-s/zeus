# src/calibration AGENTS — Zone K3 (Math/Data)

Module book: `docs/reference/modules/calibration.md`
Machine registry: `architecture/module_manifest.yaml`

## Strategy of record — Extended Platt is the offline BASELINE, not the live path

Live q-construction is the **replacement chain** (`docs/authority/replacement_final_form_2026_06_09.md`; root `AGENTS.md` probability-chain block): per-model walk-forward de-bias (`src/forecast/bayes_precision_fusion.py` `eb_bias`, λ=n/(n+8)) → T2 Bayesian precision fusion, Ledoit-Wolf Σ (`fuse_bayes_precision_posterior`) → settlement-preimage bin integration (**`src/calibration/emos.py` `bin_probability_settlement`** — this zone owns the live integrator). Extended Platt below (`platt.py`) is offline/comparison baseline calibration, NOT the primary path. `emos.py` (not Platt) builds the live q; Platt must not cap, floor, or veto it without new authority.

## WHY this zone matters

Raw ensemble probabilities are systematically biased — overconfident at long lead times, underconfident near settlement. Platt calibration corrects this bias using a three-parameter logistic: `P_cal = sigmoid(A·logit(P_raw) + B·lead_days + C)`.

The critical design decision: `lead_days` is an **input feature**, not a bucket dimension. This triples positive samples per training bucket (45→135) vs the 72-bucket approach. Without temporal decay, Zeus overtrades stale forecasts.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `emos.py` | **LIVE settlement integrator** — `bin_probability_settlement(μ*,σ)` WMO round-half preimage → bin q; primary q-construction for the replacement chain | HIGH — strategy-of-record q |
| `platt.py` | Extended Platt calibrator + bootstrap | HIGH — offline baseline calibration, not primary q |
| `manager.py` | Calibration lifecycle, maturity gates | HIGH — controls when calibration applies |
| `store.py` | Persistence of calibration parameters | MEDIUM |
| `retrain_trigger.py` | Operator-gated retrain/promotion wiring + frozen-replay gate | HIGH — live calibration promotion seam |
| `effective_sample_size.py` | Decision-group calibration sample accounting | MEDIUM |
| `blocked_oos.py` | Blocked out-of-sample calibration evaluation facts | MEDIUM |
| `drift.py` | Calibration drift detection | MEDIUM |

## Domain rules

- Calibration retrain/promotion requires operator evidence + runtime gate + frozen-replay PASS; drift records an audit result and blocks promotion.
- Retrain corpus reads must be CONFIRMED-only from `venue_trade_facts`; MATCHED/MINED are execution observations, not training truth.
- **Maturity gates are safety-critical**: n < 15 → use P_raw directly (no fit). 15–50 → strong regularization (C=0.1). 50+ → standard fit
- 200 bootstrap parameter sets (A_i, B_i, C_i) feed σ_parameter in double-bootstrap CI — without them, edge CI is systematically too narrow → overtrading
- Logit clamping: P values clamped to [0.01, 0.99] before logit transform to prevent log(0)
- Shoulder bins (open-ended tails) stay in raw probability space, not width-normalized density

## Common mistakes

- Treating lead_days as a bucket/dimension instead of a Platt input feature → collapses sample count
- Skipping bootstrap parameter generation → edge CI too narrow → overtrading
- Changing maturity thresholds without understanding why they exist → calibrating on noise
- Normalizing shoulder bins by width → infinite density artifacts
- Promoting audit instrumentation output to a live blocker without governance packet

## Active Routing vs Audit Instrumentation

- **Active routing**: `platt.py`, `manager.py`, `store.py` — these are on the live execution path. Changes require a governance packet.
- **Audit instrumentation**: `blocked_oos.py`, `effective_sample_size.py` — these collect evaluation facts but do NOT gate live execution. Their outputs are additive metrics in `status_summary`, never live blockers.
- Day0 residual fact collection is audit-only.
- Promotion of any audit metric to a live blocker requires: 30+ days of parallel data, explicit operator approval, and a governance packet per `docs/authority/zeus_current_architecture.md`.
