# src/signal AGENTS — Zone K3 (Math/Data)

Module book: `docs/reference/modules/signal.md`
Machine registry: `architecture/module_manifest.yaml`

## Strategy of record (2026-06-09) — this zone is now the BASELINE, not the primary

Live forecast authority is the **replacement chain** (`docs/authority/replacement_final_form_2026_06_09.md`; root `AGENTS.md` probability-chain block): per-model walk-forward de-bias (`src/forecast/u0r_bayes.py` `eb_bias`) → T2 Bayesian precision fusion (`fuse_u0r_posterior`) → settlement-preimage q (`src/calibration/emos.py` `bin_probability_settlement`, q_shape `fused_normal_direct`). The `src/signal/` 51-ENS→P_raw path (`ensemble_signal.py` `analytic_p_raw_vector_from_maxes`) is the **independent legacy baseline / LCB cap** — joined as a floor in `src/engine/event_reactor_adapter.py` (`effective_q_lcb = min(proof.q_lcb_5pct, replacement_hook_result.effective_q_lcb)`), NOT the primary q. Edit here for the baseline only; the live q lives in `src/forecast/` + `src/data/replacement_forecast_*`.

## WHY this zone matters

Signal is where Zeus converts 51 raw ensemble members into tradeable probability vectors. The critical insight: WU settles on integers, so probability mass concentrates at bin boundaries. Simple member-counting ignores measurement uncertainty — Zeus's Monte Carlo simulates the full chain: `atmosphere → NWP member → ASOS sensor noise (σ ≈ 0.2–0.5°F) → METAR rounding → WU integer display`.

If you break the Monte Carlo or remove the sensor noise, P_raw becomes systematically wrong at every bin boundary.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `ensemble_signal.py` | 51 members → P_raw, closed-form Gaussian-mixture (`analytic_p_raw_vector_from_maxes`; 10k-MC `p_raw_vector_from_maxes` retired) | HIGH — core BASELINE engine (legacy/cap, not primary q) |
| `day0_signal.py` | Day-0 observation replaces forecast | MEDIUM — hard floor logic |
| `day0_window.py` | When to enter day-0 mode | LOW |
| `forecast_uncertainty.py` | Bootstrap σ sources for CI | MEDIUM — feeds double-bootstrap |
| `model_agreement.py` | Inter-model agreement scoring | LOW |
| `diurnal.py` | Diurnal cycle adjustments | LOW |

## Domain rules

- Monte Carlo N is configurable (`ensemble_n_mc`) — don't hardcode
- Instrument noise σ is per-unit (°C calibrated independently, not °F/1.8)
- Bimodal detection uses KDE, not simple range checks
- `SettlementSemantics` from `src/contracts/` must round all simulated values — never do raw rounding here

## Common mistakes

- Removing or reducing Monte Carlo iterations "for speed" → destroys bin-boundary accuracy
- Using mean instead of per-member daily max → wrong physical quantity
- Ignoring unit-specific sensor noise → °C cities get wrong σ
- Forgetting timezone handling for `select_hours_for_target_date` → wrong day's max
