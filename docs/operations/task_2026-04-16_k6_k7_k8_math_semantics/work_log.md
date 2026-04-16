# Work Log

Date: 2026-04-16
Branch: data-improve
Task: K6/K7/K8 remediation — math/probability semantics, lifecycle/execution closure, control surface hardening (36 bugs across k_bugs.json)
Changed files: `src/state/db.py`, `src/strategy/market_analysis.py`, `src/execution/harvester.py`
Summary: Fixed three remaining K6 math-semantics bugs. B068: `log_trade_exit` was writing `pos.p_cal` into the `p_posterior` DB column, silently conflating calibrated raw probability with Bayesian posterior; fixed to read `pos.p_posterior`. B083: `_bootstrap_bin_no` WND input-space path had a silent `else p_raw_all[j]` fallback for open/shoulder bins (width=None or 0), mixing calibration spaces without warning; replaced with explicit `ValueError` matching the guard already present in `_bootstrap_bin`. B046: stale docstring in `_find_winning_bin` still referenced the removed `outcomePrices[0] >= 0.95` price-based fallback; updated to accurately describe the authority-only implementation. All 15 K7 (lifecycle/execution closure) and all 8 K8 (control surface) bugs were verified as already resolved in prior commits.
Verification: `python3 -c "import ast; [ast.parse(open(f).read()) for f in ['src/state/db.py', 'src/strategy/market_analysis.py', 'src/execution/harvester.py']]"` (syntax clean); `python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py src/strategy/market_analysis.py src/execution/harvester.py --plan-evidence docs/operations/current_state.md` (ok); `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files src/state/db.py src/strategy/market_analysis.py src/execution/harvester.py` (ok).
Next: Commit the 3 changed files and this work log. No open blockers. Entire K6/K7/K8 remediation complete.
