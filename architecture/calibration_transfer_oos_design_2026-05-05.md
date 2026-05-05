# Calibration transfer OOS evidence — design 2026-05-05

**Authority basis**: critic-opus 2026-05-05 verdict on user Issue 2.2 + sonnet scoping report. PR #55 had OOS infrastructure; PR #56 merge removed it under assumption `oracle_evidence_status` would replace — wiring never completed. Legacy `evaluate_calibration_transfer_policy` (string-mapping) is currently load-bearing on `evaluator.py:900`.

## Required evidence (the math)

Per `(target_source_id, target_cycle, season, cluster, metric, horizon_profile)` live-route:

- Apply source-trained Platt model to target-domain held-out pairs.
- `brier_target` = Brier score on target-domain OOS predictions vs target labels.
- `brier_source` = Platt fit's in-sample Brier (already stored on `platt_models_v2`).
- `brier_diff` = `brier_target - brier_source`.
- Verdict: `LIVE_ELIGIBLE` iff `brier_diff ≤ threshold` AND `n_pairs ≥ 200` AND `evaluated_at` not stale (90 days).

Reasoning: this directly answers "is source-trained model safe for target predictions?" Re-fit on target gives parameter drift (proxy); held-out gives direct prediction quality.

## Architectural verdicts (was: "5 operator questions")

### Q1 — Same-domain fast-path: **YES, skip OOS**

`(tigge_mars, 00z) → (tigge_mars, 00z)` is source domain itself. No transfer occurs. In-sample Brier on `platt_models_v2.brier_insample` already measures calibration quality. Verdict: same-domain returns `LIVE_ELIGIBLE` with `status='same_domain_no_transfer', n_pairs=0, brier_diff=0`. Definitional fact.

### Q2 — Held-out prediction vs re-fit on target: **HELD-OUT PREDICTION**

The gate question is "is source-trained model safe for target predictions?" Held-out prediction Brier directly answers it. Re-fit on target gives `(A_source - A_target, B_source - B_target)` parameter drift — useful diagnostic, but not load-bearing for the gate. Mathematical correctness, not operator preference.

### Q3 — Brier-diff threshold (0.005): **default 0.005, operator-tunable per-policy**

Genuine business tradeoff (tighter = false alarms; looser = silent miscalibration). 0.005 ≈ 0.5% probability-error-on-average is a reasonable starting point. Implementation: `config/settings.json::calibration_transfer_brier_diff_threshold` per-policy override; sane default. Operator tunes post-hoc without code change.

**This is the only operator delta required** — and has a default.

### Q4 — Asymmetric cycle transfer (12z OpenData → tigge 12z or 00z): **REFUSE DOUBLE-JUMPS**

`(opendata, 12z) → (tigge, 00z)` is two stacked transfer questions: cross-source AND cross-cycle simultaneously. Architectural verdict: **forbid the double-jump path entirely**. 12z OpenData input MUST route to `(tigge, 12z)` Platt model. If `(tigge, 12z)` doesn't exist for the bucket, route → `SHADOW_ONLY` (route is blocked, not validated as unsafe).

Implementation: `evaluator.py` route selection enforces cycle-aligned routing. `validated_calibration_transfers` rows are per single-jump only.

Tradeoff: simpler validation surface; cost is requiring `(tigge, 12z)` Platt models before any 12z OpenData live promotion. Phase 1 + cycle-stratified refit (post Phase 0a, 2026-05-05) covers this.

### Q5 — Accumulating visibility (INSUFFICIENT_SAMPLE vs ACCUMULATING): **DUAL — both, on different axes**

- `INSUFFICIENT_SAMPLE` = policy verdict returned by `evaluate_calibration_transfer_policy_with_evidence` (caller treats as `SHADOW_ONLY`).
- `readiness_state` row = diagnostic logging "n_pairs=120/200, ETA when threshold met".

These are orthogonal axes (verdict vs progress). Both serve, no decision needed.

## Schema

```sql
CREATE TABLE validated_calibration_transfers (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id             TEXT NOT NULL,
    source_id             TEXT NOT NULL,           -- training source: 'tigge_mars'
    target_source_id      TEXT NOT NULL,           -- serving source: 'ecmwf_open_data' | 'tigge_mars'
    source_cycle          TEXT NOT NULL,           -- '00' | '12'
    target_cycle          TEXT NOT NULL,           -- '00' | '12'
    horizon_profile       TEXT NOT NULL,           -- 'full' | 'short'
    season                TEXT NOT NULL,
    cluster               TEXT NOT NULL,
    metric                TEXT NOT NULL CHECK (metric IN ('high', 'low')),
    n_pairs               INTEGER NOT NULL,
    brier_source          REAL NOT NULL,
    brier_target          REAL NOT NULL,
    brier_diff            REAL NOT NULL,
    brier_diff_threshold  REAL NOT NULL,
    status                TEXT NOT NULL
        CHECK (status IN ('LIVE_ELIGIBLE', 'TRANSFER_UNSAFE', 'INSUFFICIENT_SAMPLE', 'same_domain_no_transfer')),
    evidence_window_start TEXT NOT NULL,
    evidence_window_end   TEXT NOT NULL,
    platt_model_key       TEXT NOT NULL,
    evaluated_at          TEXT NOT NULL,
    UNIQUE (policy_id, target_source_id, target_cycle, season, cluster, metric,
            horizon_profile, platt_model_key)
);
CREATE INDEX idx_validated_transfers_route
    ON validated_calibration_transfers(target_source_id, target_cycle, season, cluster, metric);
```

## Function signature

```python
def evaluate_calibration_transfer_policy_with_evidence(
    *,
    config: EntryForecastConfig,
    source_id: str,                      # training Platt source
    target_source_id: str,               # forecast input source (live)
    source_cycle: str,
    target_cycle: str,
    horizon_profile: str,
    season: str,
    cluster: str,
    metric: str,
    platt_model_key: str,
    conn: sqlite3.Connection,
    now: datetime,
    staleness_days: int = 90,
) -> CalibrationTransferDecision:
    """DB-row-as-authority replacement for legacy string-mapping policy.

    Same-domain fast-path: source_id==target AND cycles match → LIVE_ELIGIBLE.
    Otherwise queries validated_calibration_transfers for matching row.
    Stale or missing → SHADOW_ONLY. brier_diff > threshold → TRANSFER_UNSAFE.
    `live_promotion_approved` flag is REMOVED — DB row is authority.
    """
```

## Cadence

- **Post-refit hook**: `refit_platt_v2.py` writes a new Platt model → trigger OOS eval against all known target routes → write `validated_calibration_transfers` rows.
- **Daily cron 06:00 UTC** (off-peak): re-evaluate stale rows (>90 days), write fresh rows.
- **Feature flag** `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED`: when false, fall back to legacy `evaluate_calibration_transfer_policy`. When true, new evidence-gated function is authoritative.

## Implementation phases

| Phase | Trigger | Scope | Status |
|---|---|---|---|
| X.1 ✅ DONE 2026-05-05 | post Phase 0a | scaffold `validated_calibration_transfers` schema in `v2_schema.py` + `evaluate_calibration_transfer_policy_with_evidence` stub + same-domain fast-path + `_TRANSFER_SOURCE_BY_OPENDATA_VERSION` NameError fix | landed |
| X.2 ✅ DONE 2026-05-05 | parallel | OOS evaluator script `scripts/evaluate_calibration_transfer_oos.py` + 7 tests (today writes 0 rows; ready for Phase 1 12z ingest) | landed |
| Phase β ✅ DONE 2026-05-05 | parallel | `evaluator.py:900` switched to `_with_evidence`; `evaluator.py:2736` reads `validated_calibration_transfers` row → computes σ → passes to `MarketAnalysis(transfer_logit_sigma=σ)`. legacy `evaluate_calibration_transfer_policy` emits `DeprecationWarning` when flag-on | landed |
| X.3 ✅ FLAG FLIPPED 2026-05-05 | operator: "都激活吧" | `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true` set via `launchctl setenv` (session-level immediate) + `~/.zshrc` (shell persistence). Daemon LOCKED → 0 trade impact. Resting position now: cross-domain → SHADOW_ONLY pending OOS evidence rows; same-domain unchanged | active |
| **PENDING — daemon plist update** | when `com.zeus.live-trading.plist.locked-2026-05-04-*.bak` returns to active | Add `<key>EnvironmentVariables</key><dict><key>ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED</key><string>true</string></dict>` to plist. `launchctl setenv` does NOT persist to plist-spawned daemons across reboot. | required for production |
| X.4 (deferred) | post X.3 stable + Phase 1 evidence rows ≥ 200 per bucket | remove legacy `evaluate_calibration_transfer_policy` + drop `live_promotion_approved` arg from all callsites | deferred |

## Known follow-ups (post-activation)

1. **None-arg same-domain false-positive at `_write_entry_readiness_for_candidate`**: Phase β report noted the callsite passes `None` for `source_cycle`/`target_cycle`/etc. since readiness is written pre-forecast. With `None == None` the same-domain fast-path may fire spuriously → LIVE_ELIGIBLE on non-same-domain routes. Daemon-locked = 0 trade impact today. Fix: thread the actual forecast route metadata through to readiness writer, OR have the function reject `None` source_cycle as `INSUFFICIENT_INFO → SHADOW_ONLY`.
2. **`entry_forecast_shadow.py:179` callsite still uses legacy**: when flag-on, will emit DeprecationWarning. Migration is mechanical but distinct from this activation.
3. **`config/settings.json::transfer_logit_sigma_scale`**: default 4.0 ships; tune post-Phase-1 if OOS empirics warrant.

## Open delta for operator (single item, post-hoc)

- Set `config/settings.json::calibration_transfer_brier_diff_threshold` per-policy. Default 0.005 ships in X.1; operator can override before X.3 flag flip.

## Cross-references

- `architecture/incomplete_migrations_2026-05-05.yaml` (to be created): registers PR #56 evidence-removal-without-replacement as a recurring failure pattern.
- `docs/operations/archive/task_2026-05-04_tigge_ingest_resilience/POST_PR55_PR56_REALIGNMENT.md:54-55` — original TODO note acknowledging the gap.
