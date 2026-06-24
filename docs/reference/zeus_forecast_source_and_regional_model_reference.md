# Zeus Forecast Source And Regional Model Reference

Status: canonical durable reference  
Authority rank: reference. Code, manifests, DB/runtime receipts, and `docs/authority/**` win on disagreement.  
Freshness model: durable source/model architecture. Current provider health, available cycles, live row counts, and source validity live in current-fact surfaces and runtime DB receipts.

---

## 0. Purpose

This reference defines how Zeus should reason about forecast sources, model families, regional experts, product identity, and source-role separation. It prevents agents from confusing forecast skill source, settlement source, deterministic grid products, ensemble products, global models, regional experts, historical residual support, and current-cycle live evidence.

Use this with `docs/reference/zeus_prediction_market_quant_reference.md`, `docs/reference/zeus_data_and_replay_reference.md`, `docs/reference/zeus_market_settlement_reference.md`, `architecture/source_rationale.yaml`, `architecture/city_truth_contract.yaml`, and `architecture/fatal_misreads.yaml`.

---

## 1. Source Roles Are Non-Fungible

| Role | Meaning | Forbidden inference |
|---|---|---|
| settlement source | source/product that resolves or verifies market settlement value | endpoint availability proves settlement correctness |
| Day0 monitoring source | current same-day running extreme or nowcast observation source | final settlement truth |
| historical hourly source | hourly/sub-hourly corpus for extrema, diurnal, or history | settlement source without proof |
| forecast skill source | forecast-vs-outcome residual/training corpus | live settlement source |
| live forecast source | current-cycle model evidence for q | current source validity by itself |
| venue/CLOB source | Polymarket market, orderbook, command, fill, or chain truth | physical weather truth |

A provider can expose several products. Zeus uses each product in a specific role. Do not use one role as another unless fresh evidence proves equivalence for the exact city, date, metric, and product.

---

## 2. Product Identity Is Part Of The Forecast Value

A forecast number without physical product identity is not sufficient for live money or training.

A valid forecast row should preserve:

```text
source_id
source_family
provider
model / model_name
product_id
endpoint / endpoint_mode
request_url_hash or request identity
source_cycle_time
target_date
city / requested coordinates / timezone
cell_selection / grid identity / elevation / downscaling policy
metric high|low
forecast_value_c
artifact/provenance linkage
```

Implementation anchors:

- `src/data/replacement_forecast_materializer.py`
- `src/data/bayes_precision_fusion_capture.py`
- `src/data/bayes_precision_fusion_history_provider.py`
- `src/forecast/bayes_precision_fusion.py`
- `architecture/db_table_ownership.yaml`

---

## 3. Global Anchor, Decorrelated Globals, And Regional Experts

Reference explanation; exact active model set must be inspected in current source, config, and DB.

The replacement family can use:

- an anchor/prior model for central product identity;
- decorrelated global models as likelihood instruments;
- regional experts when city, lead, domain, residual history, and current serving evidence support inclusion;
- walk-forward residual history to estimate bias, precision, and covariance;
- explicit fallback or no-live-eligibility when support is missing.

Do not assume a hard-coded regional set from old docs. Inspect fusion math, serving modules, raw model rows, product identity fields, source_run rows, and readiness tables for the current task.

Regional experts are not automatically superior. They are useful only when product domain, city geography, lead bucket, residual history, and current serving evidence justify inclusion.

---

## 4. Walk-Forward Residual Discipline

Residual evidence must be point-in-time and settlement-graded.

Rules:

1. residual history must use target dates strictly before the decision target;
2. residuals must join to verified settlement/outcome truth;
3. covariance rows must align by target date, not by vector length coincidence;
4. high and low residuals must not mix;
5. product identity must not silently change across historical and live rows;
6. thin history must widen uncertainty, trigger fallback, or block live eligibility rather than create false precision.

Code anchors:

- `src/forecast/bayes_precision_fusion.py::_common_window_residual_matrix`
- `src/forecast/bayes_precision_fusion.py::fuse_bayes_precision_posterior`
- `src/data/bayes_precision_fusion_history_provider.py`

---

## 5. Regional Inclusion Checklist

Before adding or changing a regional model/product:

1. identify the source role;
2. identify product identity;
3. prove high/low and local-day aggregation;
4. prove city/domain/lead applicability;
5. prove residual corpus quality;
6. define missing-current behavior;
7. define missing-history behavior;
8. update DB/manifests/tests/reference if durable;
9. keep current row counts and provider health out of durable reference.

---

## 6. Failure Classes

| Failure | Mechanism | Prevention |
|---|---|---|
| grid/product collision | same logical model key but different request/product identity | product id, request hash, conflict audit |
| regional overtrust | regional model added without residual or domain proof | residual/history/current-serving gates |
| date misalignment | residual vectors share length but not dates | common target-date residual matrix |
| source-role collapse | forecast product used as settlement source | source-role manifests and fatal misreads |
| high/low mix | metric missing from key or payload | metric identity in row keys and q family |
| stale cycle laundering | old source cycle rematerialized as fresh | monotone cycle and bounded-staleness gates |
| current fact frozen into docs | provider health copied into reference | operations current-fact pointer with expiry |

---

## 7. Design Principle

A robust forecast-source system should make these errors structurally difficult:

- every forecast value carries immutable product identity;
- every model inclusion carries an evidence class and uncertainty effect;
- every residual covariance row is date-aligned;
- every source role is typed;
- every current fact expires;
- every q carrier proves family topology and dependency identity;
- every regional/product change updates tests and manifests before live use.

Do not solve source uncertainty with prose confidence. Solve it with typed provenance, uncertainty widening, fail-closed admission, and settlement-graded evidence.
