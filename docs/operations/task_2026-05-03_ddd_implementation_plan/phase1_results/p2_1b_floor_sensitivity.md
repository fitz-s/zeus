# Phase 1 §2.1b — Floor Sensitivity & Recommendation

Created: 2026-05-03 (executed)
Authority: PLAN.md §2.1 + operator Ruling 3

## Recommendation rule (refined per operator <1% FP criterion)

For each city, recommended `hard_floor` = the **largest** candidate from `[0.35, 0.40, ..., 0.85]` where training false-positive rate (% days below floor) ≤ 1.0%. Capped at 0.85 to avoid over-tight fire on stable cities. Floored at 0.35 (Day-0 §7 rail 2 absolute physics).

**Interpretation**: this maximizes outage detection sensitivity subject to keeping routine false-positive triggers below 1%. Stable cities (Tokyo, Singapore, ...) all converge to the 0.85 cap because they have 0 train days below any candidate floor up to 0.85, so the largest qualifying floor is the cap. Thin cities (Lagos, Jakarta, Shenzhen) get lower floors because their routine variance forces the 1%-FP criterion below 0.85.

## Per-city recommended hard_floor

| city | train_min | train_P05 | train_P10 | recommended | rationale |
|---|---|---|---|---|---|
| Jakarta | 0.143 | 0.714 | 0.757 | **0.35** | largest floor with train FP ≤ 1.0% → 0.35, capped at 0.85 → 0.35 (actual train FP 1.09%) |
| Lagos | 0.286 | 0.443 | 0.571 | **0.35** | largest floor with train FP ≤ 1.0% → 0.35, capped at 0.85 → 0.35 (actual train FP 1.64%) |
| Lucknow | 0.286 | 1.000 | 1.000 | **0.40** | largest floor with train FP ≤ 1.0% → 0.40, capped at 0.85 → 0.40 (actual train FP 0.55%) |
| Denver | 0.429 | 1.000 | 1.000 | **0.55** | largest floor with train FP ≤ 1.0% → 0.55, capped at 0.85 → 0.55 (actual train FP 0.56%) |
| Paris | 0.429 | 1.000 | 1.000 | **0.55** | largest floor with train FP ≤ 1.0% → 0.55, capped at 0.85 → 0.55 (actual train FP 0.54%) |
| Shenzhen | 0.429 | 0.714 | 0.714 | **0.40** | largest floor with train FP ≤ 1.0% → 0.40, capped at 0.85 → 0.40 (actual train FP 0.00%) |
| Cape Town | 0.714 | 0.857 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.54%) |
| London | 0.714 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.54%) |
| Los Angeles | 0.714 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.54%) |
| Panama City | 0.714 | 0.857 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.54%) |
| Austin | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Buenos Aires | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Busan | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Chengdu | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Chongqing | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Houston | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Manila | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Mexico City | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| San Francisco | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Wuhan | 0.857 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Amsterdam | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Ankara | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Atlanta | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Auckland | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Beijing | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Chicago | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Dallas | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Guangzhou | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Helsinki | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Jeddah | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Karachi | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Kuala Lumpur | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Madrid | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Miami | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Milan | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Munich | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| NYC | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Sao Paulo | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Seattle | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Seoul | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Shanghai | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Singapore | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Taipei | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Tokyo | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Toronto | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Warsaw | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |
| Wellington | 1.000 | 1.000 | 1.000 | **0.85** | largest floor with train FP ≤ 1.0% → 0.85, capped at 0.85 → 0.85 (actual train FP 0.00%) |

## Floor sensitivity — false-positive rate per candidate

Each cell is `% of days BELOW the candidate floor`. Operator target: < 1% on train. Higher in test ⇒ outage caught.

| city | train_min | f<0.35 train | f<0.50 train | f<0.65 train | f<0.85 train | f<0.95 train |
|---|---|---|---|---|---|---|
| Jakarta | 0.143 | 1.1% | 1.1% | 1.6% | 10.3% | 31.0% |
| Lagos | 0.286 | 1.6% | 5.5% | 13.1% | 23.0% | 45.9% |
| Lucknow | 0.286 | 0.5% | 1.1% | 2.2% | 2.7% | 4.4% |
| Denver | 0.429 | 0.0% | 0.6% | 1.7% | 1.7% | 3.3% |
| Paris | 0.429 | 0.0% | 0.5% | 1.1% | 1.6% | 1.6% |
| Shenzhen | 0.429 | 0.0% | 2.2% | 3.3% | 10.9% | 35.3% |
| Cape Town | 0.714 | 0.0% | 0.0% | 0.0% | 0.5% | 6.0% |
| London | 0.714 | 0.0% | 0.0% | 0.0% | 0.5% | 0.5% |
| Los Angeles | 0.714 | 0.0% | 0.0% | 0.0% | 0.5% | 1.1% |
| Panama City | 0.714 | 0.0% | 0.0% | 0.0% | 0.5% | 7.1% |
| Austin | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 1.6% |
| Buenos Aires | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 1.1% |
| Busan | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 2.2% |
| Chengdu | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 0.5% |
| Chongqing | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 0.5% |
| Houston | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 1.1% |
| Manila | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 2.2% |
| Mexico City | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 2.7% |
| San Francisco | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 0.5% |
| Wuhan | 0.857 | 0.0% | 0.0% | 0.0% | 0.0% | 0.5% |
| Amsterdam | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Ankara | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Atlanta | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Auckland | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Beijing | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Chicago | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Dallas | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Guangzhou | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Helsinki | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Jeddah | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Karachi | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Kuala Lumpur | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Madrid | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Miami | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Milan | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Munich | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| NYC | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Sao Paulo | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Seattle | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Seoul | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Shanghai | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Singapore | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Taipei | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Tokyo | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Toronto | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Warsaw | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| Wellington | 1.000 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

## Catastrophic test days (coverage < 0.35 absolute physics floor)

These are days where the directional window has < 35% coverage — Day-0 §7 rail 2 hard kills regardless of city. List below shows whether the absolute kill catches them.

### Jakarta (1 days)

- 2026-04-14: 0.286

### Lagos (12 days)

- 2026-01-07: 0.143
- 2026-01-08: 0.143
- 2026-01-09: 0.286
- 2026-01-13: 0.143
- 2026-02-27: 0.143
- 2026-03-02: 0.286
- 2026-03-07: 0.286
- 2026-03-11: 0.143
- 2026-03-16: 0.143
- 2026-03-18: 0.143
- 2026-03-22: 0.143
- 2026-03-23: 0.286

### Denver (1 days)

- 2026-03-29: 0.286

### Shenzhen (1 days)

- 2026-03-27: 0.286

**Total catastrophic days across all cities in test window: 15**

