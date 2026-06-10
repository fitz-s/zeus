# Zeus Doc Gap Inventory — 2026-05-24

> **Pre-merge (2026-06-09):** superseded by the replacement_forecast merge. The gap findings predate the replacement chain becoming strategy of record — authority `docs/authority/replacement_final_form_2026_06_09.md`.

Created: 2026-05-24
Authority basis: read-only codebase + target-doc audit

## Scope

Target docs audited (the "guide docs"):
- `README.md` (repo root, 186 lines)
- `AGENTS.md` (repo root, 516 lines)
- `docs/AGENTS.md` (docs router, 101 lines)
- `docs/authority/zeus_current_architecture.md` (440 lines)
- `docs/reference/zeus_architecture_reference.md` (172 lines)
- `docs/reference/modules/*.md` (per-module dense books — 25 files)

Methodology: parallel grep across all target docs for each subsystem's
canonical identifiers (file names, class names, key concepts). "NO" means
zero matches in ALL target docs. "PARTIAL" means mentioned but without
the specific sub-mechanism in question.

---

## Gap Table

| # | Subsystem | Implemented where | Mentioned in any target doc? | Severity | Suggested one-line pointer | Best insertion doc |
|---|---|---|---|---|---|---|
| 1a | DDD v2 — Data Density Discount core (two-rail trigger, continuous curve, p05 floor) | `src/oracle/data_density_discount.py`, `src/oracle/ddd_artifacts/v2_city_floors.json`, `src/oracle/ddd_artifacts/v2_nstar.json` | NO (zero hits for "ddd", "data density", "density discount", "n_star", "two-rail" in any target doc) | **CRITICAL** | `**DDD (Data Density Discount v2):** \`src/oracle/data_density_discount.py\` detects anomalous observation-coverage outages via a two-rail trigger (absolute + relative) and applies a continuous Kelly discount; spec: \`docs/reference/zeus_oracle_density_discount_reference.md\`.` | `docs/reference/zeus_architecture_reference.md` §Subsystem Map + `AGENTS.md` §Step 3 reference list |
| 1b | DDD oracle-rate / mismatch bound — `oracle_error_rates.json`, beta-binomial posterior, daily-update cron job | `src/oracle/data_density_discount.py` (consumes), `src/state/paths.py::oracle_error_rates_path`, `src/ingest_main.py` (writer job) | NO (oracle_penalty mentioned once in `docs/AGENTS.md` as city-onboard gate, but oracle-rate/mismatch-bound/beta-binomial are nowhere explained) | **CRITICAL** | `**Oracle error rates:** beta-binomial mismatch bound in \`data/oracle_error_rates.json\`, written daily by \`ingest_main.py\` cron job; feeds DDD and strategy oracle penalty.` | `docs/reference/zeus_architecture_reference.md` §Truth And Control Surfaces |
| 2a | Hierarchical ENS bias model — empirical-Bayes posterior (TIGGE prior + OpenData likelihood), `model_bias_ens_v2` table, SNR correction-strength gate | `src/calibration/ens_bias_model.py`, `src/calibration/ens_bias_repo.py` | PARTIAL — `docs/AGENTS.md` mentions `model_bias_ens_v2` rows + `ens_bias_repo.py` in the city-onboard checklist; no mention in `README.md`, `AGENTS.md`, or `zeus_architecture_reference.md` probability chain | **CRITICAL** | `**ENS bias correction:** empirical-Bayes shrinkage of TIGGE structural prior toward live OpenData residuals, SNR-gated; applied pre-MC; \`src/calibration/ens_bias_model.py\` + \`ens_bias_repo.py\`.` | `README.md` §Calibration + `docs/reference/zeus_architecture_reference.md` §Probability Chain |
| 2b | Predictive-error layer (universal location+scale+SNR gate, `ens_error_model.py`, 0.5→0.25 bias-transport) | `src/calibration/ens_error_model.py` | NO (zero hits in any target doc) | **CRITICAL** | `**Predictive-error layer:** \`src/calibration/ens_error_model.py\` corrects residual-aware MC draw distribution (location+scale+SNR gate, 0.5→0.25 variance transport); PR #336.` | `docs/reference/modules/calibration.md` §Source files table |
| 3 | Data-source precision asymmetry — TIGGE O640 (0.5°) vs ECMWF OpenData (0.25°), downsampled 4×4 to reconcile; `ecmwf_open_data.py:83-110` | `src/data/ecmwf_open_data.py` (lines 83–110), `architecture/zeus_grid_resolution_authority_2026_05_07.yaml`, `architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml` | NO in target docs (one footnote in `docs/reference/zeus_calibration_weighting_authority.md` about "~25 km horizontal resolution" but not the asymmetry or reconciliation mechanism) | **CRITICAL** | `**Grid resolution asymmetry:** TIGGE is O640 (≈0.5°); OpenData is 0.25°; live path downsamples 4×4 to canonical 0.5°; binding law: \`architecture/zeus_grid_resolution_authority_2026_05_07.yaml\`.` | `docs/reference/zeus_architecture_reference.md` §Pipeline Data Flow, `docs/reference/modules/data.md` |
| 4 | Probability chain — ENS bias step missing from the documented chain | `README.md`, `AGENTS.md`, `zeus_architecture_reference.md` all show the chain ending at `51 ENS → MC → P_raw → Platt → P_cal → α → P_posterior`; the bias-correction pre-MC step is absent | PARTIAL — chain documented but missing the bias-correction step now implemented | **CRITICAL** | Update chain in `README.md` and `zeus_architecture_reference.md` to: `51 ENS members → ENS bias correction (empirical-Bayes) → per-member daily-max → 10k MC → P_raw → Extended Platt → P_cal → α-fusion → edge → Kelly` | `README.md` §How it works + `docs/reference/zeus_architecture_reference.md` §Probability Chain |
| 5a | K1 DB split — missing `zeus-forecasts.db` from architecture reference | `AGENTS.md` (root, lines 47–55): full K1 split documented. `zeus_architecture_reference.md` mentions only 2 DBs (`zeus_trades.db`, `zeus-world.db`), silently omits `zeus-forecasts.db` | PARTIAL — covered in root `AGENTS.md`; architecture reference missing third DB | **CRITICAL** | Add `state/zeus-forecasts.db` to the Truth And Control Surfaces list in `zeus_architecture_reference.md`. | `docs/reference/zeus_architecture_reference.md` §Truth And Control Surfaces |
| 5b | INV-37 ATTACH+SAVEPOINT cross-DB rule — absent from architecture docs | `AGENTS.md` (root): INV-37 named + `get_forecasts_connection_with_world()` + `trade_connection_with_world_flocked()` described. Architecture ref and current_architecture doc: zero mentions. | PARTIAL — root AGENTS.md covers it; no pointer in authority docs | **MINOR** | `**INV-37:** cross-DB writes must use ATTACH+SAVEPOINT via \`get_forecasts_connection_with_world()\`; independent connections spanning DBs are forbidden.` | `docs/authority/zeus_current_architecture.md` §8 Runtime Truth |
| 6a | `src/oracle/` package — not in architecture reference subsystem map | `src/oracle/data_density_discount.py`, `src/oracle/ddd_artifacts/` | NO mention of `src/oracle` as a package in `zeus_architecture_reference.md` subsystem map | **CRITICAL** | Add bullet: `Oracle/DDD: \`src/oracle/**\`, data density discount, oracle error rate consumption.` | `docs/reference/zeus_architecture_reference.md` §Subsystem Map |
| 6b | `src/risk_allocator/` package — not in architecture reference subsystem map | `src/risk_allocator/governor.py` (PortfolioGovernor / RiskAllocator) | Mentioned extensively in `docs/reference/modules/riskguard.md`, `engine.md`, `execution.md`; but not in `zeus_architecture_reference.md` subsystem map or `README.md` | **MINOR** | Add line to subsystem map: `Risk allocator: \`src/risk_allocator/**\`, capital allocation governor (PortfolioGovernor), kill-switch, and reduce-only enforcement.` | `docs/reference/zeus_architecture_reference.md` §Subsystem Map |
| 6c | `src/backtest/` package | `src/backtest/` (directory exists) | NO mention in any target doc | **MINOR** | Add to subsystem map or repo structure section. | `docs/reference/zeus_architecture_reference.md` §Subsystem Map |
| 6d | `src/runtime/` package (bankroll, posture, timeout) | `src/runtime/bankroll_provider.py`, `src/runtime/posture.py` | NO mention in any target doc | **MINOR** | `**Runtime utilities:** \`src/runtime/**\` — bankroll provider, clock-skew probe, timeout guard, posture signals.` | `docs/reference/zeus_architecture_reference.md` §Subsystem Map |
| 7 | Oracle density discount reference doc not linked from guide docs | `docs/reference/zeus_oracle_density_discount_reference.md` (629 lines, REFERENCE/DESIGN-LAW status) | NO link in `README.md`, `AGENTS.md` Step 3 reference list, or `zeus_architecture_reference.md` | **CRITICAL** | Add to root `AGENTS.md` Step 3 list: `- \`docs/reference/zeus_oracle_density_discount_reference.md\` — DDD v2 rationale, two-rail design, oracle-rate/Kelly integration` | `AGENTS.md` §Step 3 reference list |
| 8 | ENS bias model files missing from calibration module book source table | `src/calibration/ens_bias_model.py`, `src/calibration/ens_bias_repo.py`, `src/calibration/ens_error_model.py` (all new, PR #334/#336) | NO — `docs/reference/modules/calibration.md` source file table lists `platt.py`, `manager.py`, `store.py`, `decision_group.py`, `metric_specs.py`, `drift.py`, `blocked_oos.py`, `effective_sample_size.py`, `retrain_trigger.py`; the three ENS bias files are absent | **CRITICAL** | Add rows to calibration.md §9 source table: `ens_bias_model.py / ens_bias_repo.py / ens_error_model.py: Hierarchical ENS bias estimator (empirical-Bayes), DB persistence, predictive-error layer.` | `docs/reference/modules/calibration.md` §9 Source files |
| 9 | Math spec: ENS bias correction not a current spec section | `docs/reference/zeus_math_spec.md` §15.2 marks EMOS as "Deferred"; the implemented empirical-Bayes shrinkage (different from EMOS) has no current spec section | PARTIAL — §15.1 has deferred Bayes partial pooling, §15.2 has deferred EMOS; neither is the implemented `ens_bias_model.py` | **CRITICAL** | Add a new current section (e.g. §10.5 or addendum) to `zeus_math_spec.md` for the implemented empirical-Bayes ENS bias correction, referencing `src/calibration/ens_bias_model.py`. | `docs/reference/zeus_math_spec.md` |

---

## Coverage Summary

| Severity | Count |
|---|---|
| CRITICAL | 9 |
| MINOR | 4 |
| **Total** | **13** |

---

## By-Doc Gap Count (CRITICAL insertions needed)

| Target doc | CRITICAL gaps requiring insertion |
|---|---|
| `docs/reference/zeus_architecture_reference.md` | 6 (DDD subsystem, oracle-rate surfaces, ENS bias chain step, zeus-forecasts.db, oracle package, oracle density doc pointer) |
| `README.md` | 2 (ENS bias chain step, DDD acknowledgment) |
| `AGENTS.md` | 2 (oracle density reference doc in Step 3 list, DDD in reference list) |
| `docs/reference/zeus_math_spec.md` | 1 (ENS bias current spec section) |
| `docs/reference/modules/calibration.md` | 1 (ENS bias source file rows) |
| `docs/authority/zeus_current_architecture.md` | 0 CRITICAL (1 MINOR: INV-37) |
| `docs/AGENTS.md` | 0 (ENS bias city-onboard pointer already present) |

---

## Notes

1. The `zeus_oracle_density_discount_reference.md` (629 lines, REFERENCE/DESIGN-LAW status) exists and is thorough, but **zero target docs link to it** — agents working on Kelly sizing, DDD, or oracle penalties will miss it entirely unless they happen to ls `docs/reference/`.

2. The empirical-Bayes ENS bias model (PRs #334/#336, live as of 2026-05-24) is the most recently merged major subsystem and is **entirely absent from the probability chain description** in README, root AGENTS.md, and zeus_architecture_reference.md.

3. Grid resolution asymmetry (O640 TIGGE vs 0.25° OpenData, 4×4 downsample) is in `architecture/` YAMLs but invisible from guide docs — agents touching data ingest or calibration equivalence will not find it without a pointer.

4. `src/oracle/` is the only top-level `src/` package with zero acknowledgment in the architecture reference subsystem map.
