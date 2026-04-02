# Plan: Go-Live Readiness Review
> Created: 2026-03-31 | Status: COMPLETED

## Goal
Determine whether Zeus is ready to go live by running the practical test surface, identifying blocking issues, and producing a prioritized readiness assessment.

## Context
- The repo already has strong architectural doctrine and a broad pytest suite.
- Earlier local `pytest` failed only because the system Python lacked dependencies; the project `.venv` has a fuller runtime.
- Go-live readiness needs both execution evidence and code-review style risk analysis.

## Approach
Use the project virtualenv to run the full test suite and supplemental checks, then inspect failures and critical runtime paths through a live-safety review lens. Separate findings into must-fix blockers, should-fix risks, and later improvements so the final decision is actionable.

## Tasks

- [x] 1. Establish the test baseline
  - Files: `requirements.txt`, `.venv`, `pytest.ini`
  - What: Verify the intended Python environment and run the canonical test entrypoint.

- [x] 2. Run the full verification surface
  - Files: `tests/`, `scripts/semantic_linter.py`, runtime scripts as relevant
  - What: Run pytest and any explicit structural/lint-style gates that matter to live safety.

- [x] 3. Triage failures and inspect critical paths
  - Files: `src/engine/`, `src/execution/`, `src/state/`, `src/data/`, `src/riskguard/`
  - What: Determine whether failures are test drift, logic defects, runtime hazards, or environment/setup gaps.

- [x] 4. Patch low-risk blockers when clearly fixable
  - Files: scoped to discovered issues
  - What: Make targeted fixes that improve readiness without destabilizing the architecture.

- [x] 5. Produce go-live review and recommendations
  - Files: final report only
  - What: Provide severity-ranked findings, residual risks, and a clear go/no-go recommendation.

## Risks / Open Questions
- The repo has dirty runtime state files; verification should avoid mutating live-ish state unless necessary.
- Some failures may reflect API drift or test drift rather than product regressions, and those need different recommendations.
- A full "go live" decision can still be blocked by operational setup outside code, such as wallet/keychain configuration and soak duration.

## Result
- Full pytest from project virtualenv passed after cleanup: `296 passed, 3 skipped`.
- Structural linter passed.
- Healthcheck now reads mode-qualified state, parses modern launchctl output, and verifies both daemon and RiskGuard health.
- Bin-width normalization now flows through the calibration path.
- Exit thresholds now use conservative confidence bounds instead of point-estimate edge alone.
