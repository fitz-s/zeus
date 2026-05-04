# Null-floor behavior in live engine

Created: 2026-05-03
Authority: read-only code audit (haiku-H, verifies review2 §5.6)

## Headline

- DDD wired into live decision path? NO
- Null floor (HK/Istanbul/Moscow/Tel Aviv) handling: N/A (DDD not wired)
- Verdict: The silent-exposure risk is currently N/A because DDD is not yet implemented in the live sizing path. However, the existing `oracle_penalty.py` mechanism (which DDD is planned to join) defaults to `1.0` (no penalty) for unknown pairs, which matches the "SILENT_ALLOW" risk profile for future integration.

## §1: Floor JSON references in src/

(None found. `grep` for the filename and contents in `src/` returned zero results.)

## §2: DDD-related references in src/

(None found. `grep` for `data_density`, `DDD`, and `density_discount` in `src/` returned zero results.)

## §3: Live decision path floor lookup

The current live decision path in `src/engine/evaluator.py` handles oracle penalties but does NOT yet include density discounts.

File: `src/engine/evaluator.py:2538-2556`
```python
        # Oracle penalty gate — blacklisted cities skip trading entirely.
        # S2 R4 P10B: pass temperature_metric so LOW candidates use separate oracle info.
        oracle = get_oracle_info(city.name, temperature_metric.temperature_metric)
        if oracle.status == OracleStatus.BLACKLIST:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="ORACLE_BLACKLISTED",
                rejection_reasons=[
                    f"oracle_error_rate={oracle.error_rate:.1%} > 10% — city blacklisted"
                ],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "oracle_penalty"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue
```

The Kelly sizing logic in `src/engine/evaluator.py:2581-2587` and `src/strategy/kelly.py` also shows no DDD implementation:

File: `src/engine/evaluator.py:2581-2587`
```python
            km = dynamic_kelly_mult(
                base=settings["sizing"]["kelly_multiplier"],
                ci_width=edge.ci_upper - edge.ci_lower,
                lead_days=lead_days_for_calibration,
                portfolio_heat=current_heat,
                strategy_key=strategy_key,
            )
```

## §4: Canonical reference doc behavior spec

File: `docs/reference/zeus_oracle_density_discount_reference.md:11-25`
```markdown
## Status

REFERENCE / DESIGN-LAW. The corrected DDD formula in §6 is the canonical
specification for any future implementation. Implementation tracker:
`docs/operations/task_2026-05-02_settlement_pipeline_audit/DATA_DENSITY_DISCOUNT.md`.

...

> **Platt calibration absorbs regime-conditional station artifacts under
> sufficiently large samples; therefore Data Density Discount must NOT
> double-penalize routine sparsity already priced into the model. DDD's
> correct role is detecting *sudden anomalous outages* relative to a hardened
> baseline, modulated by per-city Platt sample size.**
```

## §5: Verdict per city

| city | live behavior when floor=null | safety classification |
|---|---|---|
| Hong Kong | N/A (No DDD wired) | UNPROTECTED |
| Istanbul | N/A (No DDD wired) | UNPROTECTED |
| Moscow | N/A (No DDD wired) | UNPROTECTED |
| Tel Aviv | N/A (No DDD wired) | UNPROTECTED |

**Audit Observation**: While DDD is not wired, the `oracle_penalty` system (which is live) defaults to "OK" (1.0x multiplier) for any city not in `oracle_error_rates.json`. If DDD is implemented using the same "unknown = default" pattern, these cities will indeed have silent exposure to full Kelly sizing despite their missing primary feeds.
