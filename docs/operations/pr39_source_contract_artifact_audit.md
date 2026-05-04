# PR39 Source-Contract Artifact Audit
> Created: 2026-05-02 | Branch: `source-contract-protocol-slim-clean-2026-05-02`

## Decision

Keep PR39's source-contract protocol changes and raw oracle shadow snapshot evidence. Do not keep PR39's generated runtime config, generated data corpora, or local runtime state artifacts in the slim branch.

## Retained

- `architecture/script_manifest.yaml`
- `scripts/source_contract_auto_convert.py`
- `scripts/watch_source_contract.py`
- `tests/test_market_scanner_provenance.py`
- `raw/oracle_shadow_snapshots/*/*.json` (144 JSON files, raw evidence only)

## Excluded From Slim Branch

These paths were introduced by PR39 commit `59f7be12` and are absent from `origin/main`:

| Path | Live comparison | Reason excluded |
| --- | --- | --- |
| `config/settings.json` | Different from active live settings (`opening_hunt_interval_min=30`, `ensemble.primary=ecmwf_ifs025` vs live `5` / `tigge`) | Changes live runtime/trading meaning |
| `state/control_plane.json` | Different from active live control-plane ack (`2026-04-12` vs `2026-05-02`) | Local operator/runtime state |
| `config/city_correlation_matrix.json` | Absent from active live worktree | Generated/runtime input; needs separate promotion decision |
| `config/city_monthly_bounds.json` | Same as active live local file | Generated/runtime input; identical does not make it canonical |
| `data/oracle_error_rates.json` | Same as active live local file | Generated/runtime input; identical does not make it canonical |
| `data/pm_settlement_truth.json` | Absent from active live worktree | Generated corpus; needs separate provenance review |
| `data/pm_settlements_full.json` | Absent from active live worktree | Generated corpus; needs separate provenance review |
| `docs/archives/packets/task_2026-04-23_data_readiness_remediation/evidence/*.json` | Absent from active live worktree | Packet evidence; excluded from this slim code/evidence branch |
| `state/assumptions.json` | Same as active live local file | Runtime state; local-only even when identical |
| `state/cancel-signal-state.json` | Absent from active live worktree | Runtime state; local-only |
| `state/reality_contract_state.json` | Absent from active live worktree | Runtime state; local-only |
| `state/venus_antibody_queue.json` | Absent from active live worktree | Runtime state; local-only |
| `state/venus_sensing_report.json` | Absent from active live worktree | Runtime state; local-only |

## Verification

- `tests/test_market_scanner_provenance.py`: 64 passed using the existing Zeus venv and a temporary symlink to active live `config/settings.json`; the symlink was removed after the run.
- `scripts/source_contract_auto_convert.py`, `scripts/watch_source_contract.py`, and `tests/test_market_scanner_provenance.py` compile under the existing Zeus venv.
- 144 retained raw snapshot JSON files parse successfully.
- `git diff --check` and `git diff --cached --check` passed before committing retained raw evidence.

## Open Caveat

`origin/main` still ignores raw captures under `/raw/*`; the retained raw snapshots were force-added as evidence. If this slim branch lands before the PR38 raw-evidence ignore-policy fix, reviewers should explicitly approve the raw snapshot exception or drop the raw snapshot commit.