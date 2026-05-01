# Repo Review 2026-05-01 — File Index

Generated 2026-05-01 by team-lead. Single entry point for the review + remediation deliverables. Read this first; jump to whichever artifact is relevant.

## Where to start

| If you are ... | Read |
|---|---|
| The operator scanning what landed | [SYNTHESIS.md §4.5 Action log](SYNTHESIS.md) |
| Investigating a specific finding | The lane report whose name matches your concern |
| Resolving a filed proposal | [AGENTS_MD_DATA_DEGRADED_clarification.md](AGENTS_MD_DATA_DEGRADED_clarification.md) or [CI_GATE_TRIAGE_PROPOSAL.md](CI_GATE_TRIAGE_PROPOSAL.md) |
| Onboarding a new agent / clone | This file → SYNTHESIS.md → operator action items at the end of SYNTHESIS §4.5 |

## A. Review artifacts (in this directory)

| File | Author | Read time | Bottom line |
|---|---|---|---|
| [SYNTHESIS.md](SYNTHESIS.md) | team-lead | 15 min | K-pattern + 5 K-decisions + P0 clip + action log |
| [architecture.md](architecture.md) | architect (opus) | 10 min | 4 K-decisions, 10 ranked findings, 7-INV drift sample |
| [adversarial.md](adversarial.md) | critic-opus (opus) | 10 min | 10 attack patterns; DATA_DEGRADED→RED finding origin |
| [test_topology.md](test_topology.md) | test-engineer | 12 min | 36-INV coverage matrix, 83+50 skip ledger, CI scope |
| [security.md](security.md) | security-reviewer | 8 min | Per-category findings; WU finding superseded by P3 work |
| [live_running.md](live_running.md) | verifier | 12 min | 5 READY / 5 SOFT / 0 NOT-READY across 10 subsystems; 120-failure full-suite finding |

## B. Filed proposals (operator action required)

| File | Decision needed |
|---|---|
| [AGENTS_MD_DATA_DEGRADED_clarification.md](AGENTS_MD_DATA_DEGRADED_clarification.md) | (a) Clarify AGENTS.md to match code / (b) Reverse design / (c) Defer |
| [CI_GATE_TRIAGE_PROPOSAL.md](CI_GATE_TRIAGE_PROPOSAL.md) | Schedule a 3-5 day triage slice for the 120-failure cleanup |

## C. Code that landed this session (relative to repo root)

### New files
```
SECURITY-FALSE-POSITIVES.md                          # root: durable false-positive index
.gitleaks.toml                                       # root: gitleaks config + WU allowlist
.claude/hooks/pre-commit                             # git-channel orchestrator (invariant + secrets)
.claude/hooks/pre-commit-secrets.sh                  # gitleaks runner (dual-channel)
.claude/hooks/pre-merge-commit                       # symlink → pre-merge-contamination-check.sh
scripts/install_hooks.sh                             # one-time per-clone hooks setup
docs/operations/repo_review_2026-05-01/              # this whole directory
```

### Modified files
```
.claude/hooks/pre-commit-invariant-test.sh           # dual-channel + baseline 217→219
.claude/hooks/pre-merge-contamination-check.sh       # dual-channel
.claude/settings.json                                # +secrets-scan agent entry
src/contracts/settlement_semantics.py                # 30-line dormancy header on settle_market
src/data/observation_client.py                       # WU REVIEW-SAFE banner
src/data/daily_obs_append.py                         # WU REVIEW-SAFE banner
src/data/wu_hourly_client.py                         # WU REVIEW-SAFE short reference
tests/test_architecture_contracts.py                 # +INV-05 antibody
tests/test_dual_track_law_stubs.py                   # +INV-19a antibody (DATA_DEGRADED design lock)
tests/test_settlement_semantics.py                   # +INV-X for_city routing antibody
```

### New test antibodies (3 total, +2 to baseline; INV-19a not yet in TEST_FILES)
- `tests/test_architecture_contracts.py::test_risk_actions_exist_in_schema` (INV-05)
- `tests/test_dual_track_law_stubs.py::test_data_degraded_does_not_trigger_force_exit_sweep` (INV-19a)
- `tests/test_settlement_semantics.py::test_settlement_semantics_construction_routes_through_for_city` (INV-X for_city routing)

## D. What's NOT done (P1/P2 from SYNTHESIS §3-4, awaiting bandwidth)

| Priority | Item | Why deferred |
|---|---|---|
| P1-1 | launchd inject `ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET` + Polymarket WS L2 creds | Operator-machine-specific |
| P1-2 | Drop `DEFAULT 'high'` ×4 + CI grep-gate (K-D) | Requires `ARCH_PLAN_EVIDENCE` env (architecture/ edits gated) — separate slice |
| P1-3 | `TruthAuthority(StrEnum)` + exhaustive consumer match (K-C) | Invasive: touches every truth-file consumer; deserves its own slice |
| P1-5 | `place_limit_order` runtime call-stack guard (K-A) | Moderate scope; defensive only |
| P1-6 | Retire `make_family_id` deprecated wrapper / amend INV-22 | Mid-migration cleanup |
| P1-7 | Replace `polymarket_client.py:52-59` subprocess code-string | Security hardening |
| P1-9 | Tests for INV-03 / INV-07 / INV-10 (zero coverage) | Each needs careful semantic design |
| P1-10 | F12 (INV-23 ↔ NC-17 anchor) operator ruling | Operator only |
| P2 | `source: str` type-wrap; SQL whitelist; FM-08 semgrep; inv_prototype idempotency | Lower-leverage |
| P0-6 spinoff | 120-failure pytest triage (CI_GATE_TRIAGE_PROPOSAL.md) | 3-5 day dedicated slice |

In progress this session (P1 batch 1 follow-up):
- **P1-8** invariant citation consistency check — drafted as `scripts/check_invariant_test_citations.py` + companion test
- **P1-4** `requirements.txt` pin + add cryptography/web3/websockets

## E. Operator manual steps (cannot be automated)

```bash
# 1. Activate dual-channel hooks (REQUIRED — agent-only wiring otherwise)
bash scripts/install_hooks.sh

# 2. Optional: enable secrets scanning at commit time
brew install gitleaks

# 3. Decide the two filed proposals (B above)
```

`scripts/install_hooks.sh` is idempotent, smoke-tests the hooks, and reports active config. Re-runnable safely.

## F. Verification commands

```bash
# Confirm new antibodies pass
.venv/bin/python -m pytest tests/test_architecture_contracts.py::test_risk_actions_exist_in_schema -v
.venv/bin/python -m pytest tests/test_dual_track_law_stubs.py::test_data_degraded_does_not_trigger_force_exit_sweep -v
.venv/bin/python -m pytest tests/test_settlement_semantics.py::test_settlement_semantics_construction_routes_through_for_city -v

# Confirm hook baseline arithmetic
grep BASELINE_PASSED .claude/hooks/pre-commit-invariant-test.sh   # → 219

# Confirm hook detection works on both channels
GIT_INDEX_FILE=fake COMMIT_INVARIANT_TEST_SKIP=1 .claude/hooks/pre-commit-invariant-test.sh </dev/null
echo '{"tool_input":{"command":"git commit -m foo"}}' | COMMIT_INVARIANT_TEST_SKIP=1 .claude/hooks/pre-commit-invariant-test.sh

# Confirm WU REVIEW-SAFE tags landed
grep -rn "REVIEW-SAFE: WU_PUBLIC_KEY" src/ SECURITY-FALSE-POSITIVES.md .gitleaks.toml
```
