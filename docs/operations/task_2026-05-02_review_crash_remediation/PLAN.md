# Plan: Crashed Review Findings Remediation
> Created: 2026-05-02 | Status: IN PROGRESS on `review-crash-remediation-2026-05-02`

## Goal
Convert the two crashed review sessions' partial findings into a deduped, topology-routed remediation program: real issues fixed with regression antibodies, duplicates/refuted items closed with evidence, and unrelated dirty work preserved.

## Context / Guardrails
- Source branch observed: `live-unblock-ws-snapshot-2026-05-01`; implementation branch/worktree: `review-crash-remediation-2026-05-02` at `/Users/leofitz/.openclaw/workspace-venus/zeus-review-crash-remediation-2026-05-02`.
- Existing dirty work is unrelated and must not be staged or rewritten: `src/calibration/store.py`, several `src/data/**` files, `src/ingest/polymarket_user_channel.py`, and multiple tests.
- This plan artifact is topology-admitted as `operation planning packet`, risk tier `T1`, target `docs/operations/task_2026-05-02_review_crash_remediation/PLAN.md`.
- The implementation files are T3/governance; combined topology routing was ambiguous, so fixes must be split by typed intent.
- Existing root `PLAN.md` is unrelated PR 36/37 work and must not be overwritten.

## Evidence Snapshot
- `.claude/worktrees/data-rebuild` is a tracked gitlink (`160000`) with no `.gitmodules`; the directory on disk is empty.
- `.gitignore` ignores `docs/archives/` despite 217 tracked archive files; it also ignores `/raw/*` while `architecture/artifact_lifecycle.yaml` names `raw/oracle_shadow_snapshots/*/*.json` as evidence.
- `.github/workflows/architecture_advisory_gates.yml`, `architecture/zones.yaml`, and `architecture/negative_constraints.yaml` cite nonexistent `migrations/2026_04_02_architecture_kernel.sql`; the real file is `architecture/2026_04_02_architecture_kernel.sql`.
- The workflow also cites nonexistent `docs/work_packets/**`; active packet paths are already `docs/operations/**`, and archives live under `docs/archives/**`.
- SQL contains `DAY0_WINDOW_ENTERED`; `architecture/kernel_manifest.yaml` omits it, and `scripts/check_kernel_manifests.py` only checks manifest→SQL, not SQL→manifest.
- `.claude/settings.json` advertises `73/22/0`; the invariant hook uses `656/46/0`.
- Hook command detectors are still regex-based and miss `git -C/-c commit`, long-option merge, and quoted/value-less option forms.
- `pre-edit-architecture.sh` fail-opens on bad JSON and over-matches any path containing `/architecture/`.
- `pre-commit-invariant-test.sh` skips when `.venv/bin/python` is missing; `python -m pytest` itself is correct and should be marked refuted.
- `pre-commit-secrets.sh` audits working-tree root `requirements.txt`, not staged blobs or subdirectory requirements files.
- `.gitleaks.toml` has a broad `[REVIEW-SAFE: ...]` catch-all that can suppress unregistered tags.
- `.importlinter` forbids imports currently present in `src/execution/harvester.py`.
- `architecture/inv_prototype.py` idempotency is fixed, but async tests, schema-content citations, and repeated file reads remain weak.
- Active refs to `INV-11` / `INV-12` exist; `architecture/invariants.yaml` does not define them.

## Structural Decisions
1. **K1 — Parse hook commands, do not regex-guess.** One shared hook parser/channel helper should handle git global options, quoted args, and every git subcommand on the first command line.
2. **K2 — Hooks fail closed when proof is unavailable.** Missing venv, malformed JSON, parser ambiguity, or log setup failure must block or emit an explicit audit-logged override path.
3. **K3 — Scanners must use staged content and exact allowlists.** Dependency audit reads index blobs; review-safe tags must be registered before being honored.
4. **K4 — Manifest/path refs need inverse consistency gates.** Active paths, SQL enum atoms, invariant ids, and lifecycle destinations must resolve both directions.
5. **K5 — Import contracts must match real planes.** Either move harvester out of the live execution plane or split the contract; do not hide violations with unexplained ignores.

## Deduped Issue Map
| Group | Findings collapsed | Disposition | Primary files |
|---|---|---|---|
| A | Anonymous/orphan gitlink | Real | `.claude/worktrees/data-rebuild`, `.gitignore` |
| B | `docs/archives/` ignored while tracked | Real | `.gitignore`, docs ops/lifecycle docs |
| C | `/raw/*` vs oracle snapshot evidence path | Real policy drift | `.gitignore`, `architecture/artifact_lifecycle.yaml` |
| D | Stale `migrations/` + `docs/work_packets/**` paths | Real | workflow, `zones.yaml`, `negative_constraints.yaml` |
| E | Missing `DAY0_WINDOW_ENTERED` manifest atom | Real | `kernel_manifest.yaml`, `check_kernel_manifests.py` |
| F | Missing `INV-11` / `INV-12` definitions | Real; needs law choice | `invariants.yaml`, `test_topology.yaml`, active refs |
| G | Git command hook bypasses | Real | `.claude/hooks/*.sh`, hook tests |
| H | `pre-edit-architecture.sh` fail-open/over-match | Real | hook + tests |
| I | Baseline description drift | Real | `.claude/settings.json` |
| J | Invariant hook missing-venv skip/parser fragility | Real; `python -m pytest` claim refuted | invariant hook + tests |
| K | Pre-commit orchestrator exit behavior | Real hardening | `.claude/hooks/pre-commit` |
| L | Pre-merge evidence/logging edge cases | Real | pre-merge hook + tests |
| M | pip-audit root-only / working-tree scan | Real hardening | secrets hook + tests |
| N | gitleaks catch-all / WU allowlist verification | Real hardening | `.gitleaks.toml`, `SECURITY-FALSE-POSITIVES.md` |
| O | `inv_prototype.py` async/schema/cache weaknesses | Real hardening | `inv_prototype.py`, `test_inv_prototype.py` |
| P | Semgrep redundant/dead excludes | Low-risk cleanup | `semgrep_zeus.yml` |
| Q | `.importlinter` contract violations | Real; architecture choice | `.importlinter`, `src/execution/harvester.py` or moved module |

## Execution Slices

### 0. Freeze baseline and ownership
- Files: none.
- What: re-run `git status --short`, `git worktree list`, and per-file diff stats; decide whether to implement here or in a dedicated worktree; preserve unrelated dirty files.
- Verify: dirty inventory recorded; no unrelated files staged.

### 1. Repo index and lifecycle path hygiene
- Files: `.claude/worktrees/data-rebuild`, `.gitignore`, maybe `architecture/artifact_lifecycle.yaml`.
- What: remove the anonymous gitlink unless operator wants a real submodule; align `docs/archives/` and `raw/oracle_shadow_snapshots` with lifecycle policy.
- Antibody: check that tracked lifecycle destinations are not hidden by `.gitignore`.
- Verify: gitlink gone from `git ls-files -s`; archive/raw probe paths match chosen policy.

### 2. CI/workflow and architecture path drift
- Files: workflow, `architecture/zones.yaml`, `architecture/negative_constraints.yaml`, `scripts/check_kernel_manifests.py`, focused tests.
- What: replace stale `migrations/` refs; remove/replace `docs/work_packets/**`; add active-path existence validation.
- Verify: no active stale path grep hits; manifest checker passes.

### 3. Kernel event and invariant registry consistency
- Files: `kernel_manifest.yaml`, `invariants.yaml`, `test_topology.yaml`, checker/tests.
- What: add `DAY0_WINDOW_ENTERED` if current law; add inverse SQL↔manifest check; decide whether to define `INV-11`/`INV-12` or rewrite active refs.
- Verify: SQL event atoms all match manifest; active `INV-\d+` refs resolve outside archives.

### 4. Hook parser and fail-closed refactor
- Files: `.claude/hooks/pre-commit-invariant-test.sh`, `pre-commit-secrets.sh`, `pre-merge-contamination-check.sh`, `pre-edit-architecture.sh`, `pre-commit`, `.claude/settings.json`, hook tests.
- What: add shared parser/channel helper; handle `git -C`, `git -c`, `--git-dir`, `--work-tree`, `--no-pager`, and quoted options; fail closed on ambiguous git-looking commands; fix evidence trailing comments without accepting commented/nested spoofs; log override channel and non-empty command context; make override `mkdir` warning-only; block malformed pre-edit JSON; gate only repo-root `architecture/**`; fail closed or explicit-fallback when venv is missing; preserve first failing sub-hook status; update baseline text.
- Verify: hook tests cover every bypass from both reviews; agent-channel and git-channel smoke invocations behave as expected.

### 5. Secrets and dependency scanner hardening
- Files: `pre-commit-secrets.sh`, `.gitleaks.toml`, `SECURITY-FALSE-POSITIVES.md`, registry/checker tests.
- What: remove broad review-safe catch-all or gate it behind registry validation; keep WU allowlist exact; audit staged requirements blobs for every requirements file; correct advisory wording or implement a true one-time marker.
- Verify: staged unregistered `[REVIEW-SAFE: NEW_TAG]` blocks; staged requirements audit ignores unstaged working-tree changes.

### 6. `inv_prototype.py` citation semantics
- Files: `architecture/inv_prototype.py`, `tests/test_inv_prototype.py`.
- What: use AST lookup for `def`, `async def`, and `TestClass::test_name`; require schema citations to name content, not just files; memoize lazy validation without mutating `drift_findings`.
- Verify: tests catch async refs, missing schema tokens, and repeated-read regressions.

### 7. Semgrep config cleanup
- Files: `architecture/ast_rules/semgrep_zeus.yml`, optional config-lint test.
- What: remove or document redundant `tests/**/*.py` excludes; add lint for impossible include/exclude pairs if useful.
- Verify: semgrep config validates; lint passes.

### 8. Import-boundary contract repair
- Files: `.importlinter`, `src/execution/harvester.py` or new module path, routing manifests/docs if moved.
- What: decide whether harvester is outside live execution; split/move accordingly. Avoid unexplained ignores.
- Verify: `importlinter check` or equivalent AST boundary checker reports zero violations.

### 9. Closeout and independent review
- Files: this plan; optional work log if implementation proceeds here.
- What: update statuses, record fixed/duplicate/refuted findings, run independent reviewer/verifier pass.
- Verify: focused checks pass; per-file diff stats match intended edits; global pre-existing failures documented.

## Implementation Order
0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9. Stop between slices 3, 4, and 8 if topology admission or operator law choice is ambiguous.

## Open Questions
1. Delete `.claude/worktrees/data-rebuild` gitlink from the index, or restore a real submodule/worktree? Default: delete gitlink.
2. Should `raw/oracle_shadow_snapshots` be tracked evidence or local-only raw data? Current files disagree.
3. Should `INV-11`/`INV-12` be defined in `invariants.yaml`, or should active refs be rewritten as retired ids? Default: define them because active tests/source docs use them.
4. Is `src/execution/harvester.py` live execution or settlement/learning? Default: split planes explicitly.
5. Should missing `gitleaks` / `pip-audit` remain advisory or become fail-closed on protected branches?

## Minimal Verification Set
- `scripts/check_kernel_manifests.py`
- `tests/test_invariant_citations.py`
- `tests/test_inv_prototype.py`
- Hook regression subset in `tests/test_topology_doctor.py` or new `tests/test_hooks.py`
- New lifecycle-ignore and review-safe-tag checks
- `importlinter check` if available, otherwise the replacement AST boundary checker
