# Demoted Authority History — regime_unification_2026_06_12

Status: historical evidence / authority-history report  
Default-read: false  
Original path: `docs/authority/regime_unification_2026-06-12.md`  
Original blob SHA at demotion: `f12689c847713e4f1419cd398b2ea031f497b27c`  
Demoted: 2026-06-23  
Active replacement: `docs/authority/zeus_current_architecture.md`; `docs/authority/zeus_current_delivery.md`; `docs/reference/zeus_prediction_market_quant_reference.md`.

---

## Why This File Was Demoted

The original file was a dated regime-unification directive that usefully killed era layering, fallback/shadow confusion, and multiple probability authorities. It also contained date-scoped assertions about the then-current replacement chain, q_lcb machinery, market-anchor caps, coverage completion, and runtime vocabulary.

Those claims cannot remain in `docs/authority/**` as present-tense law. Surviving durable rules were promoted into active authority/reference:

- exactly one executable probability authority per live decision domain;
- degraded data is explicit absence/staleness branding, not fallback into another era;
- legacy ENS/Platt/market_fusion and old q_lcb surfaces are diagnostics/history unless current code proves active use;
- current facts expire and must not become durable law;
- packet/history sources cannot sit in default boot.

Do not cite this demoted source as current architecture. Use current code, manifests, and active authority/reference instead.

---

## Current Truth Replacing The Old Claim

Current default decision authority when q-kernel is enabled:

```text
src/engine/event_reactor_adapter.py
  -> src/engine/qkernel_spine_bridge.py
  -> src/decision/family_decision_engine.py
```

Current probability authority:

```text
raw_model_forecasts
  -> src/forecast/bayes_precision_fusion.py
  -> src/calibration/emos.py settlement-preimage q
  -> src/data/replacement_forecast_materializer.py q/q_lcb/q_ucb carrier
```

Current docs authority isolation is defined by:

```text
docs/authority/zeus_current_architecture.md
docs/authority/zeus_current_delivery.md
docs/authority/ARCHIVAL_RULES.md
architecture/docs_registry.yaml
```
