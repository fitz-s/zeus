# Strategy Module Authority Book

**Recommended repo path:** `docs/reference/modules/strategy.md`
**Current code path:** `src/strategy`
**Authority status:** Dense module reference for edge selection, fusion, FDR, correlation control, and sizing support.

## 1. Module purpose
Convert calibrated predictive information and market context into a portfolio of tradable opportunities that respect multiple-testing, correlation, and capital-allocation constraints.

## 2. What this module is not
- Not the owner of raw weather truth or settlement semantics.
- Not a replacement for riskguard or execution discipline.
- Not a place to hide strategy-specific exceptions that should be general law.

## 3. Domain model
- StrategyBenchmarkSuite replay/paper/live-shadow benchmark and promotion gate (R3 A1).
- Market analysis and family scan.
- Correlation pruning and selection family logic.
- FDR filter and statistical multiple-testing control.
- Kelly sizing inputs and oracle-penalty/risk-limit adjustments.
- Market fusion / posterior-vs-price edge shaping.

## 4. Runtime role
Produces the candidate set and prioritization structure that engine/evaluator can turn into actual decisions.

## 5. Authority role
Derived but economically central. Strategy code must stay downstream of contracts, signal, calibration, and risk law. It may optimize, not redefine semantics.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `docs/reference/zeus_domain_model.md` and `zeus_math_spec.md` for posterior/edge/FDR/Kelly logic
- `src/strategy/market_analysis.py`, `market_fusion.py`, `fdr_filter.py`, `kelly.py`, `correlation.py`, `risk_limits.py`, `selection_family.py`
- `docs/authority/zeus_current_architecture.md` money path

### Non-authority surfaces
- Ad hoc strategy narratives from old reports
- Historical success/failure anecdotes not grounded in current code/tests
- Market dashboard heuristics not expressed in code

## 7. Public interfaces
- Market analysis/fusion outputs used by evaluator
- Correlation/FDR filters
- Kelly/risk-limit calculations

## 8. Internal seams
- Posterior fusion vs market-analysis input shape
- FDR filtering vs family-scan grouping
- Kelly sizing vs risk limits vs execution availability

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `market_analysis.py / market_analysis_family_scan.py` | Primary market candidate analysis surfaces. |
| `market_fusion.py` | Posterior/market combination logic. |
| `fdr_filter.py` | Multiple-testing defense layer. |
| `kelly.py` | Sizing logic; dangerous if semantically untethered. |
| `correlation.py` | Cross-market dependence control. |
| `risk_limits.py / oracle_penalty.py / selection_family.py` | Strategy-side constraints and grouping. |
| `benchmark_suite.py / data_lake.py` | R3 A1 evidence-only benchmark metrics, replay-corpus accessor, and replay→paper→shadow promotion gate. |
| `candidates/` | R3 A1 non-executable strategy candidate stubs for future benchmark registration. |

## 10. Relevant tests
- tests/test_alpha_target_coherence.py
- tests/test_correlation.py
- tests/test_center_buy_diagnosis.py
- tests/test_center_buy_repair.py
- tests/test_cluster_collapse.py
- tests/test_cluster_taxonomy_backfill.py

## 11. Invariants
- INV-NEW-Q: no strategy may be promoted to live unless StrategyBenchmarkSuite.promotion_decision() returns PROMOTE from replay + paper + shadow evidence.
- Strategy logic must stay downstream of semantic truth, not infer its own contract/source law.
- Multiple-testing and dependence control are part of economic safety, not optional optimization.
- Sizing must remain coupled to calibrated uncertainty and risk limits.

## 12. Negative constraints
- A1 live-shadow evaluation is read-only evidence; it must not place orders, activate credentials, mutate production DB/state artifacts, or authorize CLOB cutover.
- Strategy candidate stubs are not executable alpha.
- Do not let strategy modules reach into execution/state to patch economic truth directly.
- Do not treat narrow historical strategy wins/losses as universal law.

## 13. Known failure modes
- Tiny-edge strategies survive math review but fail structurally once execution/exit/risk are included.
- Correlation or family grouping is wrong, so nominal diversification is fake.
- Strategy naming masks economically different bet types under one label.

## 14. Historical failures and lessons
- [Archive evidence] `docs/archives/reports/strategy_failure_analysis.md` found structural rather than purely statistical reasons for center_buy and shoulder_sell failures.
- [Archive evidence] `docs/archives/findings/p10_adversarial_findings.md` is useful as a stress-test lens: how can an apparently valid strategy fail under adversarial but realistic assumptions?

## 15. Code graph high-impact nodes
- `src/strategy/market_analysis.py` and `market_fusion.py` likely bridge signal/calibration outputs into evaluator decisions.
- `kelly.py` and `fdr_filter.py` are smaller but economically load-bearing nodes.

## 16. Likely modification routes
- Posterior/market fusion change: review math spec, evaluator, risk limits, and tests together.
- Selection/correlation change: review family grouping, FDR, and cluster tests.

## 17. Planning-lock triggers
- Any change to posterior fusion, FDR, Kelly, or family/correlation logic.

## 18. Common false assumptions
- If edge exists, strategy is good.
- A single strategy label implies one economic shape.
- Strategy can compensate for execution or lifecycle flaws.

## 19. Do-not-change-without-checking list
- FDR/Kelly semantics without math-spec alignment
- Grouping/family logic without correlation tests

## 20. Verification commands
```bash
pytest -q tests/test_strategy_benchmark.py
pytest -q tests/test_alpha_target_coherence.py tests/test_correlation.py tests/test_center_buy_diagnosis.py tests/test_center_buy_repair.py
pytest -q tests/test_cluster_collapse.py tests/test_cluster_taxonomy_backfill.py
python -m py_compile src/strategy/*.py
```

## 21. Rollback strategy
Rollback strategy packets together with dependent evaluator/tests if the decision surface changed materially.

## 22. Open questions
- Should strategy family definitions be elevated into a machine-readable manifest?
- Which current strategy assumptions are still hidden in packet notes rather than code/reference?

## 23. Future expansion notes
- Add strategy failure-matrix appendices grounded in current test evidence.
- Register strategy families and dependencies in the future module manifest.

## 24. Rehydration judgement
This book is the dense reference layer for strategy. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
