# Phase 2.5 Design: Calibration Transfer Policy — Replace String Mapping with Statistical Domain Check

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04
**Author:** Claude Opus 4.7
**Authority basis:** may4math.md Finding 2 (`CRITICAL_STAT_RISK`, `SHADOW_ONLY_UNTIL_SOURCE_CYCLE_OOS`); operator directive 2026-05-04 to address hidden cross-module branches.

---

## Hidden branch identified

`src/data/calibration_transfer_policy.py:14`:
```python
POLICY_ECMWF_OPENDATA_USES_TIGGE_LOCALDAY_CAL_V1 = "ecmwf_open_data_uses_tigge_localday_cal_v1"
```

`evaluate_calibration_transfer_policy()` returns `LIVE_ELIGIBLE` when `live_promotion_approved=True`, else `SHADOW_ONLY`. **No source-cycle compatibility check, no horizon-profile check, no statistical OOS evidence requirement.**

This is the **actual** authority gate that lets ECMWF Open Data forecasts inherit TIGGE-trained Platt models for live trading. If left as-is:
- Phase 2 (cycle stratification of Platt buckets) is necessary but not sufficient
- Phase 3 (`ENSEMBLE_MODEL_SOURCE_MAP[ecmwf_ifs025] → ecmwf_open_data`) routes the data, but the bucket-resolution layer still uses string-mapped TIGGE calibration without proving statistical equivalence

Without Phase 2.5, Phase 2 + Phase 3 jointly produce: 12z OpenData forecast → routed to ecmwf_open_data → calibration looked up via string-mapped TIGGE bucket key → **bypasses cycle-stratified bucket entirely** if transfer policy mapping is consulted upstream.

## Decision

Replace the string-keyed `evaluate_calibration_transfer_policy()` with a `ForecastCalibrationDomain` compatibility check that:

1. Compares (source_id, cycle_hour_utc, horizon_profile, metric, season) of the live forecast against the calibrator's training domain
2. Uses a registered `validated_transfers` table that records which (train_domain, test_domain) pairs have been OOS-validated
3. Returns `LIVE_ELIGIBLE` only when:
   - `forecast.domain == calibrator.domain` (exact match), OR
   - `(forecast.domain, calibrator.domain)` is in `validated_transfers` with passing OOS evidence
4. Returns `SHADOW_ONLY` for unvalidated transfers
5. Returns `BLOCK` for known-invalid (e.g., 06z/18z forecasts on full-profile calibrator)

## Schema additions

### New table: `validated_calibration_transfers`

```sql
CREATE TABLE IF NOT EXISTS validated_calibration_transfers (
  transfer_id TEXT PRIMARY KEY,
  train_source_id TEXT NOT NULL,
  train_cycle_hour_utc TEXT NOT NULL,
  train_horizon_profile TEXT NOT NULL,
  train_data_version TEXT NOT NULL,
  test_source_id TEXT NOT NULL,
  test_cycle_hour_utc TEXT NOT NULL,
  test_horizon_profile TEXT NOT NULL,
  test_data_version TEXT NOT NULL,
  metric TEXT NOT NULL,
  season TEXT NOT NULL,
  brier_score REAL,
  log_loss REAL,
  calibration_slope REAL,
  calibration_intercept REAL,
  reliability_passed INTEGER,
  executable_ev_delta_bps REAL,
  n_test_pairs INTEGER NOT NULL,
  validated_at TEXT NOT NULL,
  validated_by TEXT NOT NULL,
  authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
  expires_at TEXT,
  notes TEXT
);
```

Initial seed: NO transfers are pre-validated. Every (train, test) cell starts as SHADOW_ONLY until OOS evidence is recorded.

### `ForecastCalibrationDomain` dataclass

```python
@dataclass(frozen=True)
class ForecastCalibrationDomain:
    source_id: str
    cycle_hour_utc: str   # '00' | '12' (rejecting 06/18 for entry_primary)
    horizon_profile: str  # 'full' | 'short'
    metric: str           # 'high' | 'low'
    season: str           # 'DJF' | 'MAM' | 'JJA' | 'SON'
    data_version: str

    def matches(self, other: "ForecastCalibrationDomain") -> bool:
        return all(getattr(self, f) == getattr(other, f) for f in self.__dataclass_fields__)
```

## API replacement

### Old (string mapping):
```python
def evaluate_calibration_transfer_policy(
    *,
    forecast_data_version: str,
    calibrator_data_version: str,
    live_promotion_approved: bool = False,
) -> CalibrationTransferDecision:
    ...
```

### New (domain check):
```python
def evaluate_calibration_transfer(
    *,
    forecast_domain: ForecastCalibrationDomain,
    calibrator_domain: ForecastCalibrationDomain,
    operator_override_token: Optional[str] = None,  # for emergency override; logged
) -> CalibrationTransferDecision:
    """
    Returns:
      - LIVE_ELIGIBLE if domains match, OR validated_calibration_transfers has passing row
      - SHADOW_ONLY if no validated row exists for this (train, test) pair
      - BLOCK if cycle/horizon are categorically incompatible (e.g., 06z forecast, full-profile calibrator)
    """
```

The old `live_promotion_approved` Boolean is **removed**. Authority comes from validated_calibration_transfers, not operator opt-in.

## Required validation experiments (per may4math.md Finding 1+2)

Before unlock, run the source-cycle holdout matrix:

| Train domain | Test domain | Pass criterion |
|---|---|---|
| TIGGE 00z | TIGGE 00z holdout | baseline (must converge) |
| TIGGE 00z | TIGGE 12z holdout | within-source cycle transfer |
| TIGGE 12z | TIGGE 12z holdout | (after 90-day 12z backfill) baseline |
| TIGGE 00z+12z pooled | cycle holdout | pooling safety |
| TIGGE 00z | OpenData 00z | source transfer (uses recent OpenData ground truth) |
| TIGGE 00z | OpenData 12z | source + cycle transfer |
| OpenData 00z | OpenData 12z | within-source cycle transfer |

Metrics for "pass":
- multiclass Brier ≤ 1.05× train-domain Brier
- log loss ≤ 1.05× train-domain log loss
- calibration slope within [0.85, 1.15]
- calibration intercept within ±0.05 of zero
- reliability bins: ≥ 80% of bins within ±0.05 of identity
- executable EV delta_bps ≥ -50 (i.e., not materially worse than train-domain replay)

Validated rows persist in `validated_calibration_transfers` with the actual computed metrics.

## Live evaluator integration

```python
# In src/engine/evaluator.py — replace existing transfer policy call

forecast_domain = ForecastCalibrationDomain(
    source_id=executable_forecast.source_id,
    cycle_hour_utc=parse_cycle_hour(executable_forecast.issue_time),
    horizon_profile=executable_forecast.horizon_profile,
    metric=candidate.metric,
    season=derive_season_local(candidate.target_date, candidate.city),
    data_version=executable_forecast.data_version,
)

calibrator_domain = ForecastCalibrationDomain(
    source_id=platt_model.source_id,
    cycle_hour_utc=platt_model.cycle_hour_utc,
    horizon_profile=platt_model.horizon_profile,
    metric=platt_model.temperature_metric,
    season=platt_model.season,
    data_version=platt_model.data_version,
)

decision = evaluate_calibration_transfer(
    forecast_domain=forecast_domain,
    calibrator_domain=calibrator_domain,
)

if decision.status == "BLOCK":
    return reject(rejection_stage="CALIBRATION_DOMAIN_MISMATCH", reason=decision.reason_codes)
elif decision.status == "SHADOW_ONLY":
    return reject(rejection_stage="CALIBRATION_TRANSFER_NOT_VALIDATED", reason=decision.reason_codes)
# else proceed with live calibration
```

## Tests

```python
# tests/test_calibration_transfer_domain.py

def test_exact_domain_match_returns_live_eligible():
    """Same source × cycle × horizon × metric × season × data_version → LIVE_ELIGIBLE without validation row."""

def test_unvalidated_cross_source_returns_shadow_only():
    """TIGGE-trained, OpenData-live → SHADOW_ONLY when no validated_transfers row exists."""

def test_validated_transfer_returns_live_eligible():
    """Insert a validated row, then transfer is LIVE_ELIGIBLE for that (train, test) pair only."""

def test_invalid_cycle_returns_block():
    """06z forecast on full-profile calibrator → BLOCK (categorically inelligible)."""

def test_no_operator_override_path():
    """live_promotion_approved=True is no longer recognized; only validated_transfers grants live."""

def test_domain_mismatch_emits_specific_reason_code():
    """rejection_reason_json contains 'CALIBRATION_DOMAIN_MISMATCH:source=tigge_mars vs ecmwf_open_data'"""
```

## Migration path for existing callers

1. Locate every caller of `evaluate_calibration_transfer_policy` (`grep -rn evaluate_calibration_transfer_policy src/`)
2. Replace with `evaluate_calibration_transfer` plumbed with `ForecastCalibrationDomain` constructed from caller context
3. Remove `live_promotion_approved` config flag from `config/settings.json` (it becomes irrelevant)

## Risks

- **Bootstrap problem**: with NO validated_transfers initially, EVERY transfer is SHADOW_ONLY. Live trading is fully blocked until at least one validated row lands. **This is the correct conservative default** — operator runs the validation experiments, records the rows, then live becomes possible per validated cell.
- **Validation runs need historical data**: TIGGE 00z baseline requires the 17-month corpus we already have. TIGGE 12z baseline requires the 90-day backfill (Phase 1B in flight). OpenData rows require ingest history (~30+ days available).
- **Operator escape hatch**: in emergency, an `operator_override_token` (signed, logged, expires) can bypass the gate for a specific cell. NEVER auto-renew. Token issuance is a separate operator action with audit trail.

## Sequencing

This Phase 2.5 lands AFTER Phase 2 (Platt cycle stratification — Platt models must have cycle/source/horizon columns first) and BEFORE Phase 3 (routing fix — must not enable 12z routing until validated_transfers proves it safe).

```
Phase 1 (12z code + backfill)  →  Phase 2 (Platt schema + retrain)  →  Phase 2.5 (transfer policy + validation runs)  →  Phase 3 (routing)  →  unlock
```

## Out of scope

- Bayesian uncertainty propagation through transfer (deferred — covered by may4math.md Stage 4 hierarchical shrinkage)
- Multi-source ensemble blending (out of scope for unlock)
- Automated continuous re-validation (deferred — for now, validated rows are explicit operator actions)
