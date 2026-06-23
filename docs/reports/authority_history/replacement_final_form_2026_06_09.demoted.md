# Demoted Authority History — replacement_final_form_2026_06_09

Status: historical evidence / authority-history report  
Default-read: false  
Original path: `docs/authority/replacement_final_form_2026_06_09.md`  
Original blob SHA at demotion: `bd42f04318246f09456421dbfd9fcb3b052996aa`  
Demoted: 2026-06-23  
Active replacement: `docs/authority/zeus_current_architecture.md`; `docs/reference/zeus_prediction_market_quant_reference.md`; `docs/reference/zeus_math_spec.md`.

---

## Why This File Was Demoted

The original file was a dated 2026-06-09 strategy-of-record note. It named a specific replacement probability chain, commits, audit date, fixed/fitted constants, and then-current interpretation of fallback/shadow/diagnostic vocabulary. That material is useful history but is no longer safe in `docs/authority/**` because future agents could treat its dated claims as current executable law.

Surviving durable law was promoted into:

- `docs/authority/zeus_current_architecture.md` — current architecture/probability/q-kernel law;
- `docs/reference/zeus_prediction_market_quant_reference.md` — complete current money-path reference;
- `docs/reference/zeus_math_spec.md` — current q/q_lcb/payoff/utility math reference.

Do not cite this demoted source as present-tense law. Use it only for historical reconstruction, commit archaeology, or audit provenance.

---

## Current Truth Replacing The Old Claim

The current deploy path is code-proven from:

```text
src/data/replacement_forecast_materializer.py
src/forecast/bayes_precision_fusion.py
src/calibration/emos.py
src/engine/qkernel_spine_bridge.py
src/decision/family_decision_engine.py
src/engine/event_reactor_adapter.py
config/settings.json
architecture/negative_constraints.yaml
architecture/db_table_ownership.yaml
```

The current durable claim is not “the 2026-06-09 paper is authority.” The current claim is:

```text
raw_model_forecasts
  -> bayesian precision fusion
  -> settlement-preimage q/q-band over Ω
  -> q-kernel family decision
  -> side-aware route/payoff economics
  -> utility/risk/execution gates
```

Any future work must inspect current code/config/manifests rather than reloading the old dated authority file.
