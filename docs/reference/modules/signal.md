# Signal Module Authority Book

**Recommended repo path:** `docs/reference/modules/signal.md`
**Current code path:** `src/signal`
**Authority status:** Dense module reference for predictive signal generation, day0 routing, diurnal logic, and uncertainty features.

## 1. Module purpose
Transform forecast ensembles and observation-derived context into raw predictive distributions and day0 signals that match Zeus's discrete-settlement market objects.

## 2. What this module is not
- Not settlement law.
- Not current source-validity law by itself.
- Not a place to compress hourly/Day0/settlement tracks into one generic temperature stream.

## 3. Domain model
- ECMWF/TIGGE ensemble handling and Monte Carlo raw probability.
- Day0 signal routing and same-day windows.
- Diurnal/persistence/nowcast features from hourly observation truth.
- Forecast uncertainty and model agreement helpers.

## 4. Runtime role
Produce the pre-calibration probability and day0 continuation context that evaluator and calibration consume.

## 5. Authority role
Mathematically dense but still downstream of contracts/source law. It must preserve the chain from atmosphere to discrete settlement, not replace it with convenient proxies.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `docs/reference/zeus_math_spec.md` sections on forecast data, Monte Carlo, and units/rounding
- `docs/reference/zeus_domain_model.md` for the probability chain
- `docs/authority/zeus_current_architecture.md` truth planes and hourly-extrema rule
- `src/signal/ensemble_signal.py`, `diurnal.py`, `day0_*`, `forecast_uncertainty.py`, `model_agreement.py`

### Non-authority surfaces
- A convenient hourly feed that is not proven for the relevant truth plane
- Old exploratory notebooks on nowcasting
- Graph context alone without math/source proof

## 7. Public interfaces
- Raw ensemble signal builders
- Day0 signal/router/window helpers
- Diurnal and uncertainty feature helpers

## 8. Internal seams
- Raw probability generation vs diurnal/day0 feature augmentation
- Day0 high vs Day0 low causality families
- Historical hourly features vs live same-day monitor context

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `ensemble_signal.py` | Core Monte Carlo probability engine. |
| `diurnal.py` | Historical-hourly shape logic; DST and hourly semantics matter here. |
| `day0_signal.py / day0_high_signal.py / day0_low_nowcast_signal.py / day0_router.py / day0_window.py` | Same-day runtime path. |
| `forecast_uncertainty.py / model_agreement.py` | Support features and agreement metrics. |
| `day0_extrema.py` | Extremum-aware day0 helper surface. |

## 10. Relevant tests
- tests/test_ensemble_signal.py
- tests/test_day0_window.py
- tests/test_day0_runtime_observation_context.py
- tests/test_diurnal.py
- tests/test_diurnal_curves_empty_hk_handled.py
- tests/test_bootstrap_symmetry.py

## 11. Invariants
- Signal must stay consistent with settlement semantics and target metric identity.
- Hourly aggregation used for features must preserve the extrema required by the target metric.
- High and low day0 paths are distinct causality families.
- Point-in-time snapshot truth outranks hindsight data for calibration/training.

## 12. Negative constraints
- Do not reintroduce Open-Meteo grid-snap as a silent Tier-4 escape hatch for settlement-adjacent logic.
- Do not use UTC-day maxima/minima when the market is defined by local calendar day.
- Do not let day0 monitoring truth silently stand in for settlement truth.

## 13. Known failure modes
- Intra-hour extrema are lost by snap-to-HH:00 logic, poisoning diurnal/Day0 behavior.
- DST or local-day handling shifts maxima/minima into the wrong market day.
- Day0 low/high paths reuse code but not causality, causing wrong exits or training features.
- Fallback forecast or observation feeds silently drift into canonical training logic.

## 14. Historical failures and lessons
- [Archive evidence] History lore already records DST diurnal rebuild risk and HKO truncation bias; signal docs must make these lessons first-class.
- [Archive evidence] The archived deep map and reality-crisis materials reinforce that signal is only one stage in the money path; it cannot compensate for wrong truth-plane mapping.

## 15. Code graph high-impact nodes
- `src/signal/ensemble_signal.py` and `diurnal.py` are likely the most structurally central signal nodes.
- `src/signal/day0_router.py` and day0 signal files bridge into engine/execution-sensitive behavior.

## 16. Likely modification routes
- Raw-probability or Monte Carlo change: review math spec, contracts, calibration, and tests together.
- Day0/diurnal change: review data truth, current source validity, and engine/execution consumers.

## 17. Planning-lock triggers
- Any change to ensemble Monte Carlo, day0 routing, diurnal semantics, or metric identity.
- Any change that touches hourly truth, source/date/unit/track proof, or calibration coupling.

## 18. Common false assumptions
- Hourly data is just a feature surface, so source semantics are loose.
- A 1-hour sample can substitute for an hourly extremum aggregate.
- High and low paths are symmetric enough to share implicit defaults.

## 19. Do-not-change-without-checking list
- `ensemble_signal.py` rounding/noise assumptions without math-spec review
- `diurnal.py` local-day/DST logic
- Day0 router semantics without current source-validity review

## 20. Verification commands
```bash
pytest -q tests/test_ensemble_signal.py tests/test_day0_window.py tests/test_day0_runtime_observation_context.py
pytest -q tests/test_diurnal.py tests/test_diurnal_curves_empty_hk_handled.py tests/test_bootstrap_symmetry.py
python -m py_compile src/signal/*.py
```

## 21. Rollback strategy
Rollback signal packets as one semantic bundle; do not keep changed day0 or diurnal logic if data/source coupling was reverted.

## 22. Open questions
- Which signal-layer assumptions still live only in packet docs or oral memory?
- Do current module surfaces clearly separate day0 runtime signal from training feature generation?

## 23. Future expansion notes
- Add explicit high/low module subsections or sub-books if low-track live authority expands.
- Attach graph-derived impacted-test suggestions for signal edits.

## 24. Rehydration judgement
This book is the dense reference layer for signal. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
