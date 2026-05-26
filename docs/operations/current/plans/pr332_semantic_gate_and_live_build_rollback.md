# PR332 semantic gate and live build rollback

## Objective

Repair the exact-head PR332 live-target blockers that prevent required money-path
CI from accepting the branch and that can leave partial live-order aggregate
rows after a failed live command build.

## Scope

- Register newly introduced EDLI pre-submit blocker states in the money-path
  object registry and route them to the existing money-path tests.
- Add a transactional rollback boundary around live command/certificate build
  phases that append live-order aggregate rows before returning a rejected
  receipt.
- Keep live-order schema initialization safe inside rollback boundaries by
  avoiding implicit commits during idempotent index creation.
- Add focused tests proving the registry accepts the states and a forced
  post-aggregate build failure cannot leave `ExecutionCommandCreated` projection
  state behind.
- Keep the daemon scheduler constructor patchable at module scope so PR332's
  required no-submit daemon smoke can replace it without starting APScheduler.

## Non-goals

- Do not enable live submit, live canary, Day0 hard-fact live mode, or production
  daemon restart.
- Do not implement full user-channel socket runtime or Day0 DAG in this hotfix.

## Verification

- `python scripts/ci/semantic_diff_classifier.py --base origin/main --head HEAD --objects architecture/money_path_objects.yaml --mapping architecture/money_path_ci.yaml --fail-on-unregistered`
- `python scripts/check_schema_version.py`
- `python -m pytest -q tests/money_path --maxfail=5 --timeout=300`
- Focused EDLI money-path tests covering live-order aggregate rollback and
  pre-submit authority states.
