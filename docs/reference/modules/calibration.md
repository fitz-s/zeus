# Calibration Module Authority Book

**Recommended repo path:** `docs/reference/modules/calibration.md`
**Current code path:** `src/calibration`
**Authority status:** Dense module reference for post-signal calibration, drift management, and training-group hygiene.

## 1. Module purpose
Turn raw predictive distributions into calibrated probabilities using point-in-time features, correct outcome pairing, and defensible grouping/drift logic.

## 2. What this module is not
- Not a place to fix wrong source truth with statistics.
- Not a generic ML playground detached from point-in-time semantics.
- Not an excuse to mix hindsight data into training pairs.

## 3. Domain model
- Extended Platt calibration and numerical safety.
- Decision groups, out-of-sample blocking, and effective sample size.
- Drift detection and calibration manager/store behavior.
- Metric specifications for high/low and forecast family identities.

## 4. Runtime role
Consumes P_raw and outcome/feature data to produce `P_cal` and calibration artifacts consumed by evaluator/strategy.

## 5. Authority role
Calibration is mathematically flexible but semantically brittle. It cannot rescue wrong Y truth, wrong source/date alignment, or wrong metric identity.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `docs/reference/zeus_math_spec.md` calibration sections
- `src/calibration/platt.py`, `manager.py`, `store.py`, `decision_group.py`, `metric_specs.py`, `drift.py`, `blocked_oos.py`, `effective_sample_size.py`
- `docs/authority/zeus_current_architecture.md` point-in-time and dual-track law

### Non-authority surfaces
- Backfilled forecast skill rows not proven for training
- Historical performance summaries without point-in-time discipline
- Packet-era calibration experiments not adopted into current code/tests

## 7. Public interfaces
- Calibration manager/store
- Platt fitting and inference helpers
- Decision-group and blocked-OOS grouping logic
- Drift/effective sample size helpers

## 8. Internal seams
- Raw probability vs feature side information
- Manager/store persistence vs stateless fitting logic
- Decision-group identity vs metric spec vs data version

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `platt.py` | Core calibration logic. |
| `manager.py / store.py` | Lifecycle and persistence of calibration artifacts. |
| `decision_group.py` | Point-in-time grouping and independence discipline. |
| `metric_specs.py` | Metric identity and feature-contract anchor. |
| `drift.py / blocked_oos.py / effective_sample_size.py` | Health/robustness layer. |

## 10. Relevant tests
- tests/test_calibration_manager.py
- tests/test_calibration_quality.py
- tests/test_calibration_unification.py
- tests/test_drift.py
- tests/test_bayesian_sigma_evaluation.py
- tests/test_bootstrap_symmetry.py

## 11. Invariants
- Calibration pairs must use the correct point-in-time truth family.
- Metric identity and data version must be explicit.
- A wrong source/date mapping cannot be statistically corrected into validity.
- Extremely confident P_raw values require safe clipping/logit handling, not row dropping.

## 12. Negative constraints
- Do not mix post-hoc knowledge into training pairs.
- Do not train across high/low or forecast-family identities unless explicitly designed to do so.
- Do not treat forecast-skill tables as self-evidently canonical training truth.

## 13. Known failure modes
- Wrong observation family or stationarity assumptions produce phantom bias that calibration 'learns'.
- Decision-group leakage overstates effective sample size and confidence.
- Forecast-skill and settlement truth get collapsed into one training target.
- Low-track and high-track families are conflated.

## 14. Historical failures and lessons
- [Archive evidence] `audits/math_audit_2026_04_06.md` and historical calibration materials stressed explicit logit safety, bin support, and independent decision groups.
- [Archive evidence] Reality-crisis and legacy truth-surface audits showed that wrong Y truth cannot be repaired by better fitting.

## 15. Code graph high-impact nodes
- `src/calibration/platt.py` and `manager.py` are likely the central calibration hubs.
- `decision_group.py` and `metric_specs.py` are smaller but semantically load-bearing bridge files.

## 16. Likely modification routes
- Platt or feature change: review math spec, signal, contracts, and tests together.
- Data pairing/grouping change: review source/data truth and current fact surfaces first.

## 17. Planning-lock triggers
- Any change to calibration formulas, grouping logic, metric identity, or training data contract.

## 18. Common false assumptions
- Calibration can absorb any source mismatch if enough history exists.
- Forecast-skill tables are automatically safe training inputs.
- High/low family sharing is a performance optimization with no semantic downside.

## 19. Do-not-change-without-checking list
- Platt fitting semantics without math-spec review
- Decision-group identity logic
- Metric-spec and data-version wiring

## 20. Verification commands
```bash
pytest -q tests/test_calibration_manager.py tests/test_calibration_quality.py tests/test_calibration_unification.py
pytest -q tests/test_drift.py tests/test_bayesian_sigma_evaluation.py tests/test_bootstrap_symmetry.py
python -m py_compile src/calibration/*.py
```

## 21. Rollback strategy
Rollback calibration packets together with any persisted model-family changes or training-data assumptions.

## 22. Open questions
- Which current forecast-skill inputs are still transitional and should be marked as such in module/books manifests?

## 23. Future expansion notes
- Add a calibration-data contract appendix keyed by metric family and source family.
- Add graph-derived dependent-test packs for calibration changes.

## 24. Rehydration judgement
This book is the dense reference layer for calibration. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
