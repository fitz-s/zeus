# Q-Kernel Rebuild — Settlement-Graded After-Cost EV (GOAL #117 deploy gate)

Created: 2026-06-15. `scripts/qkernel_settlement_graded_ev.py`. Read-only on live data (mode=ro); no venue calls; no daemon restart. Extends `qkernel_arm_replay.py` §5 — closes the condition_id→bin join the replay flagged DATA-COVERAGE-LIMITED, by joining each book condition_id to its bin via `zeus-world.db.no_trade_regret_events.bin_label` (the question string that encodes the bin; 1:1 with condition_id, all 1986 labels parse, 0 unmatched).

**Method.** Per settled family the rebuilt spine is reconstructed VERBATIM from the validated replay (same fresh members at decision cycle = target−1d, same predictive-RSS σ, same grid Omega, same joint q + coherent band). For every book bin (joined via bin_label) both legs (buy_yes / buy_no) are priced at the **taker ask only** plus the **real taker fee** (`fee_details_json.fee_rate_fraction`, uniformly 0.05). Each leg's vector edge is computed with the LIVE spine functions `src/decision/payoff_vector.edge_lower_bound` and `point_fair_value` over the Arrow-Debreu payoff on the grid Omega. The spine GATE `edge_lcb>0 AND point_ev>0` is applied; among passers the spine picks **argmax point_ev** (the ΔU>0 proxy: at a flat exposure baseline family log-growth ΔU is monotone in the point edge). A family the spine no-trades contributes ZERO trades (not a 0-EV trade). The picked leg is settlement-graded: realized winning bin = `round_single(realized)` via `SettlementSemantics.for_city` (HK oracle_truncate; others wmo_half_up).

> **ΔU proxy caveat (honest scope).** The full live pass also requires `delta_u_at_min>0` and `optimal_delta_u>0` from the vector ΔU sizing against the live PortfolioExposureVector + executable cost curve — state not reconstructable offline. We reproduce the load-bearing **vector edge_lcb>0** gate exactly (live function, real band) and proxy the ΔU sign by `point_ev>0`. This is the spine's edge gate faithfully; it is NOT the full sizing pass. Grades the SIGN/CI of after-cost EV on the spine's selected legs, the §117 question.

## Coverage

- Settled VERIFIED families in window: **715**
- Settled families with a book condition joined via bin_label: **440**
- Families with a usable spine AND a joined book (spine-evaluated): **388**
- Of those, spine NO-TRADED (no leg passed edge_lcb>0 ∧ point_ev>0): **119**
- **Spine-SELECTED graded trades (n): 269**
- Drop / skip reasons: no_book_for_settled_family=263, no_members_or_resolution=64

## Overall after-cost EV (spine-selected trades)

- n trades: **269**
- mean after-cost EV per share: **-0.0026**
- bootstrap 95% CI (resample families, 5000): **[-0.0380, 0.0321]**
- median EV: -0.0021; win-rate (payoff=1): 0.164; mean cost (ask+fee): 0.1662
- **sign: INDETERMINATE (CI spans 0)**

## By side

| side | n | mean EV | 95% CI | win-rate |
|---|---|---|---|---|
| buy_yes | 148 | 0.0154 | [-0.0095, 0.0462] | 0.034 |
| buy_no | 121 | -0.0247 | [-0.0933, 0.0451] | 0.322 |

## By class (modal / ring / tail)

Class of the spine's PICKED leg: **modal** = the spine's favorite (highest-q) bin; **ring** = an adjacent bounded (point/range) bin that is not the modal; **tail** = a shoulder bin ("X or below" / "X or higher").

| class | n | mean EV | 95% CI | win-rate | sign |
|---|---|---|---|---|---|
| modal | 64 | -0.0536 | [-0.1308, 0.0247] | 0.172 | 0-span |
| ring | 190 | 0.0089 | [-0.0311, 0.0480] | 0.163 | 0-span |
| tail | 15 | 0.0682 | [-0.0290, 0.2137] | 0.133 | 0-span |

## By metric

| metric | n | mean EV | 95% CI |
|---|---|---|---|
| high | 246 | -0.0083 | [-0.0450, 0.0269] |
| low | 23 | 0.0582 | [-0.0160, 0.1728] |

## VERDICT

**INDETERMINATE — the spine's after-cost EV on its selected trades is mean -0.0026 with the 95% CI SPANNING 0 (CI [-0.0380, 0.0321], n=269): not statistically distinguishable from zero at this sample size.**

- Best class: **tail** (n=15, mean EV 0.0682, CI [-0.0290, 0.2137], 0-spanning).
- Worst class: **modal** (n=64, mean EV -0.0536, CI [-0.1308, 0.0247], 0-spanning).

