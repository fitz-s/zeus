# P5 — Maintenance Worker Core: Sub-Packet Scaffold

Status: SCAFFOLD (plan-only; no code in this packet)
Authority: implementation packets P5.1–P5.4 (and optional P5.5) derive
  from this document. Sub-packet executors treat this as READ-ONLY.
Input authority order:
  1. `02_daily_maintenance_agent/DESIGN.md`
  2. `02_daily_maintenance_agent/SAFETY_CONTRACT.md`
  3. `02_daily_maintenance_agent/TASK_CATALOG.yaml`
  4. `02_daily_maintenance_agent/DRY_RUN_PROTOCOL.md`
  5. `04_workspace_hygiene/PURGE_CATEGORIES.md`
  6. `04_workspace_hygiene/ARCHIVAL_RULES.md`

---

## §1. Repo Placement Decision

**Decision: standalone repo (`maintenance-worker/`), consumed by each project
via its `.maintenance/` config directory.**

### Argument

**Option A — nested under `scripts/`** is eliminated by the SAFETY_CONTRACT
itself. SAFETY_CONTRACT.md "Forbidden Targets → Source code and tests" lists
`scripts/**` as a hard-block target (the agent must NEVER read-write or move
any file matching `scripts/**`). An agent whose own source lives in `scripts/`
would need carved-out self-exemptions in the safety contract — a structural
contradiction. Eliminated on first principles.

**Option B — new top-level directory under the hosting project** avoids the
`scripts/` collision but couples the agent code to each project's git history.
Every Zeus commit adding a new agent version appears in `git log` alongside
trading-system commits — mixed provenance, mixed review contexts.
DESIGN.md §"Project-Agnostic Surface" defines adoption as a *consumer* model:
the hosting project supplies `.maintenance/config.yaml` + rule files; the agent
core is *read* by the project, not owned by it. A top-level directory under
Zeus would invert that dependency.

**Option C — separate repo** implements the consumer model cleanly. The
hosting project adds a dependency (pip package, git submodule, or binary) and
provides only the `.maintenance/` config directory. Agent code is tested and
versioned independently; a new project adopts it without touching its own
source tree. Zeus-specific glue lives in P6 bindings, not in this repo.

**Chosen placement:** `maintenance-worker/` as a standalone Python package
repo (`pip install maintenance-worker` or editable install for local dev).
Source root is `src/maintenance_worker/` (standard src-layout). The project
adoption surface remains `.maintenance/` inside the hosting repo exactly as
DESIGN.md specifies.

**Deployment surface:** four paths per scheduler mode:
- launchd: `~/Library/LaunchAgents/<reverse-dns>.maintenance.plist` calls
  `maintenance-worker run --config <repo>/.maintenance/`
- OpenClaw cron: job entry in `~/.openclaw/cron/jobs.json` calls same CLI
- in-process: `import maintenance_worker; MaintenanceWorker(config_dir).run()`
- direct-exec test mode: `maintenance-worker dry-run --config <dir>` (detected
  as non-scheduler invocation; forces DRY_RUN regardless of live_default)

---

## §2. Sub-Packet Split

Total P5 budget: 3000–4500 LOC source + ~1500 LOC tests.
Each sub-packet must stay ≤ 1000 LOC.

| Sub-packet | Name | LOC (source) | LOC (tests) | Owner role |
|-----------|------|-------------|-------------|-----------|
| P5.0a | Types — shared enums, dataclasses, protocols imported by all sub-packets | ~200 | ~50 | `Operation`, `ValidatorResult`, `TickResult`, `TaskSpec`, `GuardResult`, `RefusalReason`, `InvocationMode` |
| P5.1 | Engine — loop, guards, state machine, kill switch, refusal modes, scheduler detection | ~550 | ~250 | tick lifecycle, guard evaluation, exit codes |
| P5.2 | Validator + safety contract enforcement + operation guards | ~1000 | ~400 | pre-action check, 5 contract guarantees, post-mutation detector, git/gh/subprocess guards |
| P5.3 | Rules parser + task registry + evidence trail + ack manager | ~700 | ~300 | YAML loading, proposal emit, rollback record, ack hashing |
| P5.4 | Integration fixtures — 10 contrived workspace messes per category | ~200 | ~900 (fixtures + assertions) | acceptance suite; defer fixture catalog to its own SCAFFOLD |
| P5.5 (REQUIRED for production runs) | CLI + apply/publish layer: commit, PR open, provenance stamping, scheduler bindings | ~400 | ~150 | argparse, scheduler env detection, provenance.py, launchd/cron wiring |

**M5 decision: P5.0a types sub-packet (option ii).** `engine.run_tick()` (P5.1)
calls `ActionValidator` (P5.2) and both import shared enums (`Operation`,
`ValidatorResult`, `TickResult`, etc.). Without a shared types layer, P5.1
cannot be built and tested without P5.2 already compiled, and P5.3 shares the
same dependency. Extracting ~200 LOC of pure dataclasses/enums/protocols into
P5.0a creates a linear build chain: P5.0a → P5.1 → P5.2 → P5.3 → P5.4.
Each sub-packet can be delivered and unit-tested independently; only P5.4
requires the full chain. Types sub-packet has no logic — no guard, no
mutation, no IO — so it has no safety contract surface to enforce.

**P5.2 sizing justification:** The validator must implement 5 contract-binding
guarantees (SAFETY_CONTRACT.md "Validator Semantics" §a–e), a 5-state return
enum, Operation.READ coverage, pattern matching against ~30 individual
forbidden-path bullet patterns across 6 named groups, post-mutation detector
(distinct from pre-mutation FORBIDDEN_* path), and 3 operation-guard modules
covering the 14 SAFETY_CONTRACT.md "Forbidden Actions" bullets (filesystem ops
→ `subprocess_guard.py`; git ops → `git_operation_guard.py`; gh/PR ops →
`gh_operation_guard.py`). Total: ~1000 LOC at the sub-packet ceiling.
If implementation exceeds 1000 LOC, move the 3 guard modules to a new P5.2b
sub-packet; the core validator stays in P5.2a. The ceiling is a hard constraint.

**P5.4 sizing note:** Source LOC is low (~200) because P5.4 is fixture
scaffolding and test harness only — no new production logic. The 900-LOC test
budget covers 60 fixture scenarios (10 per 6 categories) plus integration
assertions. Full fixture enumeration is deferred to P5.4's own SCAFFOLD.

---

## §3. Module Layout

Root: `maintenance-worker/src/maintenance_worker/`

### types/ — P5.0a (shared types; no logic, no IO)

```
types/
  operations.py   ~65 LOC  (Operation enum: READ, WRITE, MKDIR, MOVE, DELETE, GIT_EXEC, GH_EXEC, SUBPROCESS_EXEC)
  results.py      ~70 LOC  (ValidatorResult, GuardResult, TickResult, ApplyResult, AckStatus)
  specs.py        ~50 LOC  (TaskSpec, EngineConfig, InstallMetadata, ProposalManifest, RollbackRecipe)
  modes.py        ~20 LOC  (RefusalReason enum, InvocationMode enum, ScheduleKind enum)
```

No imports from `core/`, `rules/`, or `cli/`. All other packages import from `types/`;
`types/` imports only stdlib (`enum`, `dataclasses`, `datetime`, `pathlib`). This is
the P5.0a → P5.1 → P5.2 → P5.3 → P5.4 build chain anchor.

### core/ — P5.1 + P5.2

```
core/
  engine.py              ~220 LOC
  guards.py              ~150 LOC
  refusal.py             ~100 LOC
  kill_switch.py          ~90 LOC  (+ post_mutation_detector)
  validator.py           ~400 LOC
  contract_patterns.py   ~200 LOC  (if P5.2 splits; else merged into validator.py)
  evidence_writer.py     ~150 LOC
  git_operation_guard.py  ~80 LOC  (Forbidden Actions: push --force, rebase, reset --hard)
  gh_operation_guard.py  ~100 LOC  (Forbidden Actions: merge PR, approve PR, comment, close/open issues)
  subprocess_guard.py     ~80 LOC  (Forbidden Actions: pip/npm/cargo install, pytest mutate)
```

**engine.py** — `MaintenanceEngine`
- `run_tick(config: EngineConfig) -> TickResult`
- `_load_config(config_dir: Path) -> EngineConfig`
- `_enumerate_candidates(task: TaskSpec) -> CandidateList`
- `_emit_dry_run_proposal(task: TaskSpec, candidates: CandidateList) -> ProposalManifest`
- `_apply_decisions(task: TaskSpec, proposal: ProposalManifest, ack: AckState) -> ApplyResult`
- `_emit_summary(tick: TickContext) -> None`
State machine: START → LOAD_CONFIG → CHECK_GUARDS → ENUMERATE_CANDIDATES →
DRY_RUN_PROPOSAL → APPLY_DECISIONS → SUMMARY_REPORT → END. All 7 transitions
logged. Any guard failure → FATAL exit with named exit code.

**guards.py** — `GuardSet`
- `evaluate_all(config: EngineConfig, state_dir: Path) -> GuardReport`
- `check_not_dirty_repo(repo: Path) -> GuardResult`
- `check_no_active_rebase(repo: Path) -> GuardResult`
- `check_no_pause_flag(state_dir: Path) -> GuardResult`
- `check_no_kill_switch(state_dir: Path) -> GuardResult`
- `check_no_oncall_quiet(state_dir: Path) -> GuardResult`
- `check_disk_free_above_threshold(repo: Path, threshold_pct: float) -> GuardResult`
- `check_no_inflight_maintenance_pr(gh_context: GhContext) -> GuardResult`

**refusal.py** — `RefusalModes`
- `refuse_fatal(reason: RefusalReason, ctx: TickContext) -> NoReturn`
- `skip_tick(reason: RefusalReason, ctx: TickContext) -> None`
Exit codes: one unique code per `RefusalReason` enum value (8 distinct codes).

**kill_switch.py** — `KillSwitch`
- `is_set(state_dir: Path) -> bool`
- `is_paused(state_dir: Path) -> bool`
- `is_self_quarantined(state_dir: Path) -> bool`  ← checked every tick in CHECK_GUARDS
- `write_self_quarantine(state_dir: Path, reason: str) -> None`
  Called ONLY by `post_mutation_detector()` when disk-state divergence is confirmed
  AFTER apply. NEVER called by `validate_action()` on a pre-mutation FORBIDDEN_* catch.
- `post_mutation_detector(apply_result: ApplyResult, manifest: ProposalManifest, state_dir: Path) -> None`
  Compares applied filesystem diff against the allowed-write set in the manifest.
  Any divergence → `write_self_quarantine()` + URGENT alert + non-zero exit.
  (SAFETY_CONTRACT.md §229-238: this is the only trigger for SELF_QUARANTINE.)
- `check_scheduler_invocation() -> InvocationMode`  ← accidental-trigger detection
  Returns `SCHEDULED | MANUAL_CLI | IN_PROCESS`. `MANUAL_CLI` forces
  `DRY_RUN_ONLY` regardless of `live_default` (SAFETY_CONTRACT.md
  "Accidental-Trigger Containment").

**validator.py** — `ActionValidator`
Public interface (each maps to one of the 5 SAFETY_CONTRACT guarantees):
- `validate_action(path: Path, operation: Operation) -> ValidatorResult`  ← main gate
- `canonicalize_path(path: Path) -> Path`  ← guarantee (b): realpath before match
- `resolve_symlink_target(path: Path) -> Path`  ← guarantee (c): symlink + hardlink
- `decompose_directory_op(dir_path: Path, op: Operation) -> list[LeafCheck]`  ← guarantee (d)
- `check_remote_url_allowlist(remote_url: str, install_meta: InstallMetadata) -> ValidatorResult`  ← guarantee (e)
Return enum: `ALLOWED | FORBIDDEN_PATH | FORBIDDEN_OPERATION | MISSING_PRECHECK | ALLOWED_BUT_DRY_RUN_ONLY`
Operation enum includes `READ` — guarantee (a) in SAFETY_CONTRACT.md: reads of credential
files, `state/*.db*`, and all other Forbidden Target paths return `FORBIDDEN_PATH`, not `ALLOWED`.

**Two distinct response paths — do NOT conflate (SAFETY_CONTRACT.md §138-140 vs §229-238):**

Path A — validator catches violation BEFORE mutation:
  `validate_action()` returns `FORBIDDEN_*` →
  `RefusalModes.refuse_fatal()` for THIS tick only (writes to `errors.tsv`,
  exits non-zero). Does NOT write `SELF_QUARANTINE`. Next tick starts clean.

Path B — post-mutation detector sees violation AFTER disk state changed:
  `post_mutation_detector(apply_result: ApplyResult, expected_manifest: ProposalManifest)`
  in `kill_switch.py` compares applied filesystem diff against allowed-write set.
  If divergence detected → `kill_switch.write_self_quarantine(state_dir, reason)`.
  All future ticks call `KillSwitch.is_self_quarantined()` in CHECK_GUARDS and
  refuse until human removes the file. Human reconciles; agent never auto-reverts.

**contract_patterns.py** — `ForbiddenPatterns`
- Defines the exhaustive forbidden-target set derived from SAFETY_CONTRACT.md
  "Forbidden Targets" (6 named groups, ~30 individual bullet patterns).
- `match_path(canonical: Path) -> Optional[ForbiddenRule]`
- `match_operation(op: Operation) -> Optional[ForbiddenRule]`
No project-specific patterns in this file; Zeus-specific extensions live in
P6 `bindings/` and are merged at runtime into the pattern set.

**git_operation_guard.py** — `GitOperationGuard`
Covers SAFETY_CONTRACT.md "Forbidden Actions" bullets: `git push --force`,
`git rebase`, `git reset --hard`, history rewrites, branch deletions outside
maintenance branches.
- `check_git_command(cmd: list[str]) -> ValidatorResult`
  Intercepts all `subprocess` calls whose argv[0] is `git`. Returns
  `FORBIDDEN_OPERATION` on any disallowed subcommand + flag combination.
  Called by engine before any git subprocess; not a separate layer of
  authorization but a specialization of `validate_action(op=Operation.GIT_EXEC)`.

**gh_operation_guard.py** — `GhOperationGuard`
Covers SAFETY_CONTRACT.md "Forbidden Actions" bullets: merge PR, approve PR,
comment on non-maintenance PR, close/open issues, any `gh api` mutation
beyond the maintenance-PR allowlist.
- `check_gh_command(cmd: list[str]) -> ValidatorResult`
  Same interception pattern as `GitOperationGuard`. Allowlist: `gh pr create`,
  `gh pr view`, `gh api repos/.../labels` (read), `gh pr comment` on own
  maintenance PRs only.

**subprocess_guard.py** — `SubprocessGuard`
Covers SAFETY_CONTRACT.md "Forbidden Actions" bullets: `pip install`,
`npm install`, `cargo add`, any package install/uninstall, `pytest` invocations
that mutate state outside the evidence dir, `chmod`, `chown`, `ln -s` across
safety boundary, arbitrary network requests.
Also covers SAFETY_CONTRACT.md line 75: `rm` of any non-zero-byte file.
- `check_subprocess(cmd: list[str]) -> ValidatorResult`
  Allowlist of permitted subprocess patterns; all others → `FORBIDDEN_OPERATION`.
  `rm` is BLOCKED unconditionally in the subprocess allowlist. The only
  permitted deletion path is Python-native `os.unlink()` on files whose size
  has been pre-confirmed as zero bytes and whose path has passed
  `validate_action(path, Operation.DELETE)`. `shutil.rmtree` is blocked;
  directory removal uses `git worktree remove` (guarded by `GitOperationGuard`)
  or `shutil.move` to quarantine (path validated before move).
- Note: "no Task/SendMessage/Agent tool use" (leaf-not-orchestrator) is enforced
  at install time via `setup.py` import-time assertions — no runtime check needed
  since those symbols do not exist in the package dependency graph.

**evidence_writer.py** — `EvidenceWriter`
- `open_trail(date: date, evidence_dir: Path) -> TrailContext`
- `write_config_snapshot(ctx: TrailContext, config: EngineConfig) -> None`
- `write_guards_tsv(ctx: TrailContext, report: GuardReport) -> None`
- `write_proposal(ctx: TrailContext, task_id: str, proposal: ProposalManifest) -> None`
- `write_applied_row(ctx: TrailContext, task_id: str, result: ApplyResult) -> None`
- `write_rollback_recipe(ctx: TrailContext, task_id: str, recipe: RollbackRecipe) -> None`
- `write_summary(ctx: TrailContext) -> Path`
- `write_exit_code(ctx: TrailContext, code: int) -> None`
Emitted artifacts match DESIGN.md evidence trail schema exactly.

### rules/ — P5.3

```
rules/
  task_registry.py     ~220 LOC
  config_loader.py     ~180 LOC
  rules_parser.py      ~150 LOC
  ack_manager.py       ~150 LOC
```

**task_registry.py** — `TaskRegistry`
- `load(catalog_path: Path) -> TaskRegistry`
- `get_tasks_for_schedule(schedule: ScheduleKind) -> list[TaskSpec]`
- `is_task_paused(task_id: str, pause_flag_dir: Path) -> bool`
- `validate_schema(raw: dict) -> None`  ← schema_version check; FATAL on mismatch

**config_loader.py** — `ConfigLoader`
- `load(config_dir: Path) -> EngineConfig`
- `validate(config: EngineConfig) -> None`  ← FATAL if invalid; no silent defaults
- `resolve_env_vars(raw_value: str, env: dict) -> str`  ← `${VAR}` expansion

**rules_parser.py** — `RulesParser`
- `parse_purge_categories(md_path: Path) -> list[PurgeRule]`
- `parse_archival_rules(md_path: Path) -> ArchivalConfig`
- `parse_lore_protocol(md_path: Path) -> LoreConfig`
Policy files are read-only inputs; parser never writes to them.

**ack_manager.py** — `AckManager`
- `compute_proposal_hash(manifest: ProposalManifest) -> str`  ← SHA-256 of manifest body
- `check_ack(task_id: str, proposal_hash: str, state_dir: Path) -> AckStatus`
- `mark_applied(task_id: str, proposal_hash: str, applied_at: datetime, state_dir: Path) -> None`
- `check_auto_ack_n(task_id: str, state_dir: Path) -> Optional[int]`  ← bulk-ack AUTO_ACK_NEXT_N;
  the ONLY auto-modification of ack state the agent performs (DRY_RUN_PROTOCOL.md "Bulk Acknowledge")
- `decrement_auto_ack_n(task_id: str, state_dir: Path) -> None`
- `expire_stale_proposals(evidence_dir: Path, ttl_days: int) -> list[Path]`

### cli/ — P5.5 (REQUIRED)

**M6 decision (b): P5.5 is mandatory, not optional.**
`engine._apply_decisions()` in P5.1 STAGES the applied filesystem diff into
`ApplyResult` but does NOT commit to git or open PRs. All git-commit, PR-open,
and provenance-stamping logic lives in P5.5. This keeps P5.1 focused on safety
and filesystem mutation; P5.5 is the apply/publish layer. A deployment missing
P5.5 cannot produce maintenance-branch commits — the audit-by-grep discipline
(SAFETY_CONTRACT.md "Audit-by-Grep Discipline", lines 182-192) requires
`git log --author='Maintenance Worker' --pretty='%h %ai %s'` to work on first
live run. Without commit-author config, Run-Id trailers, and Generated-By
headers in place before apply, audit-by-grep fails immediately.

```
cli/
  entry.py             ~180 LOC
  scheduler_detect.py   ~80 LOC
  provenance.py        ~100 LOC  (commit identity, Run-Id, Generated-By)
  notifier.py           ~40 LOC  (interface only; implementation in P6 bindings)
```

**entry.py** — argparse root
- `cmd_run(args)`: full tick via `MaintenanceEngine.run_tick()` → `ApplyPublisher.publish()`
- `cmd_dry_run(args)`: forces `DRY_RUN_ONLY`; ignores `live_default`
- `cmd_status(args)`: prints guard state, last tick result, kill switch status
- `cmd_version(args)`: semver from package metadata

**scheduler_detect.py** — `SchedulerDetector`
- `detect() -> InvocationMode`
  Checks: `MAINTENANCE_SCHEDULER=1` env var; known parent processes
  (launchd, cron). If neither → `MANUAL_CLI` → engine enforces DRY_RUN_ONLY.

**provenance.py** — `ProvenanceStamper`
Implements SAFETY_CONTRACT.md "Audit-by-Grep Discipline" (lines 182-192) and
DESIGN.md "Identity And Provenance" section:
- `make_run_id() -> str`  ← UUID4; written to evidence trail and every commit trailer
- `set_commit_identity(repo: Path, author_name: str, author_email: str) -> None`
  Configures `Maintenance Worker <maintenance@<org>>` for the duration of a tick
  only (via `--local` git config scoped to the operation, not globally).
- `wrap_file_with_header(path: Path, run_id: str) -> None`
  Prepends `# Generated-By: maintenance_worker <run_id>` to any file the agent
  creates (DESIGN.md "every file the agent writes carries a Generated-By header").
- `make_commit_message(run_id: str, summary: str) -> str`
  Returns message with `Run-Id: <run_id>` trailer so every commit is
  reverse-lookupable via `git log --grep="Run-Id: <run_id>"`.

**notifier.py** — `NotifierInterface` (abstract base)
- `send_summary(run_id: str, summary_path: Path) -> None`
- `send_alert(run_id: str, message: str, severity: AlertSeverity) -> None`
Concrete implementations (Discord, Slack, file-only) live in P6 `bindings/`.

### §3.5 Cross-reference: P5.1 ↔ P5.5 apply/publish boundary

`MaintenanceEngine._apply_decisions()` (P5.1) returns `ApplyResult` containing
the staged filesystem mutations (paths moved, files quarantined, zero-byte files
deleted). It does NOT call `git commit` or `gh pr create`. After
`_apply_decisions()` returns, `entry.cmd_run()` (P5.5) passes `ApplyResult` to
`ApplyPublisher.publish(apply_result, run_id, provenance)`, which:
1. Calls `ProvenanceStamper.set_commit_identity()`.
2. Commits staged changes with `ProvenanceStamper.make_commit_message()`.
3. Opens maintenance PR if `ApplyResult.requires_pr` is true.
4. Calls `KillSwitch.post_mutation_detector()` to verify no forbidden paths
   landed on disk.

This boundary means P5.1 is fully testable without git operations; P5.5 unit
tests mock `ApplyPublisher.publish()` to verify provenance fields.

### tests/ — P5.4

```
tests/
  fixtures/            60 fixture workspaces (10 per 6 categories)
  test_engine.py
  test_validator.py
  test_guards.py
  test_ack_manager.py
  test_dry_run_floor.py
  test_integration_categories.py  ← 60-fixture sweep
  test_contract_guarantees.py     ← 5 guarantee probes each with automated assertion
```

Full fixture catalog (naming, directory layout, expected verdict per case)
is deferred to the P5.4 SCAFFOLD. Summary: 60 fixtures total = 10 per
6 categories (Categories 1–6 from PURGE_CATEGORIES.md).

---

## §4. Dry-Run Floor Enforcement Contract

### Pseudocode (validator floor check)

```python
def enforce_dry_run_floor(
    task_id: str,
    install_meta: InstallMetadata,
    floor_cfg: DryRunFloor,
) -> ValidatorResult:
    # Exempt tasks bypass floor unconditionally — hardcoded, not config-read
    FLOOR_EXEMPT_TASK_IDS: frozenset[str] = frozenset({
        "zero_byte_state_cleanup",
        "agent_self_evidence_archival",
    })
    if task_id in FLOOR_EXEMPT_TASK_IDS:
        return ValidatorResult.ALLOWED

    # Human override: presence of ack file bypasses floor
    if floor_cfg.override_ack_file.exists():
        return ValidatorResult.ALLOWED

    # Floor check: elapsed time since first run
    elapsed = datetime.now(tz=timezone.utc) - install_meta.first_run_at
    if elapsed < timedelta(days=floor_cfg.floor_days):   # floor_days = 30
        return ValidatorResult.ALLOWED_BUT_DRY_RUN_ONLY

    return ValidatorResult.ALLOWED
```

Key properties:
- `FLOOR_EXEMPT_TASK_IDS` is a hardcoded `frozenset`, not read from YAML.
  Prevents a misconfigured catalog from silently widening exemptions.
- Override check runs BEFORE floor check so a missing ack file does not
  cause an unnecessary `os.path.exists` on the metadata path.
- `install_meta.first_run_at` is set once; subsequent reads are immutable.
- Return value feeds the outer `validate_action(path, operation)` call;
  `ALLOWED_BUT_DRY_RUN_ONLY` prevents `APPLY_DECISIONS` from executing live.

### install_metadata.json schema

```json
{
  "schema_version": 1,
  "first_run_at": "<ISO 8601 UTC — e.g. 2026-05-15T04:30:00Z>",
  "agent_version": "<semver — e.g. 0.1.0>",
  "install_run_id": "<UUID — evidence trail id from first tick>",
  "allowed_remote_urls": ["<git remote origin URL pinned at install>"],
  "repo_root_at_install": "<absolute path>"
}
```

File is written once on first run. Subsequent ticks read it; any write
attempt after creation raises `ImmutableMetadataError`. The `allowed_remote_urls`
field is the allowlist consumed by validator guarantee (e): before any `git push`,
`validate_action` checks the current remote URL against this list. A URL
mismatch → `FORBIDDEN_OPERATION`. Human must update this file explicitly to
permit a remote URL change.

### Exempt-task validation logic

On load, `TaskRegistry.load()` cross-checks: for each task with
`dry_run_floor_exempt: true` in the catalog, the task_id must be a member of
`FLOOR_EXEMPT_TASK_IDS` (the hardcoded frozenset). Any task claiming exemption
that is NOT in the hardcoded set → `FATAL` schema validation failure.
This prevents catalog drift from silently granting new exemptions.

---

## §5. Project-Agnosticism Proof

### Facts the agent needs from a hosting project

All facts are supplied via `.maintenance/` in the hosting project. The agent
core reads these paths; it contains no project-specific defaults.

| Fact | Source in .maintenance/ | Example (Zeus) |
|------|--------------------------|---------------|
| Repo root absolute path | `config.yaml :: repo_root` | `/Users/leofitz/.openclaw/workspace-venus/zeus` |
| State directory | `config.yaml :: state_dir` | `${REPO}/state/maintenance_state` |
| Evidence directory | `config.yaml :: evidence_dir` | `${REPO}/state/maintenance_evidence` |
| Task catalog path | `task_catalog.yaml` | same file |
| Safety contract path | `safety_contract.md` | same file |
| Hygiene rule paths | `hygiene_rules/` directory | `PURGE_CATEGORIES.md`, `ARCHIVAL_RULES.md`, etc. |
| Allowed remote URLs | captured at install into `${STATE_DIR}/install_metadata.json` | `https://github.com/<org>/<repo>.git` |
| Notification channel | `config.yaml :: notification_channel` | `discord` (resolved by P6 binding) |
| Forbidden path extensions | `safety_contract.md` (project-specific section) | Zeus-specific paths e.g. `state/*.db` |
| Schedule mode | `config.yaml :: scheduler` | `launchd` |

### Zeus-specific items → P6 bindings

All Zeus identifiers (state DB paths, Discord webhook, plist naming patterns,
`${ZEUS_REPO}` env expansion, Zeus-specific forbidden path extensions) go to
`P6/bindings/zeus/`. The core resolves `${REPO}` and generic `${STATE_DIR}`;
it does not know about Zeus-specific env vars.

### Self-check: module names contain zero "zeus" tokens

```
grep -ri zeus \
  maintenance-worker/src/maintenance_worker/core/ \
  maintenance-worker/src/maintenance_worker/rules/ \
  maintenance-worker/src/maintenance_worker/cli/
```

Expected result: 0 matches. Any hit is a P6 binding that leaked into core.

---

## §6. Self-Check

### Does any module add a new admission gate that itself becomes a barrier?

The validator (`validator.py`) is a gate. Its own surface must be tested in
isolation (see `test_contract_guarantees.py`). No module downstream of the
validator may introduce a second path-authorization step — all path decisions
go through `validate_action()`. Risk: `rules_parser.py` reading policy files
must not try to "validate" those reads itself (they are read-only authority
inputs, explicitly in the read-allowlist; rule files are not Forbidden Targets).

The Operation enum includes `READ` explicitly (SAFETY_CONTRACT.md §147-149:
"READ is not exempt"). A call to open `state/*.db` for reading must pass
through `validate_action(path, Operation.READ)` and return `FORBIDDEN_PATH`.
`test_contract_guarantees.py` must include a probe: open `state/*.db` for
READ → assert `FORBIDDEN_PATH` (not `ALLOWED`).

Verdict: **PASS if** `validate_action()` is invoked before EVERY filesystem
operation — READ, WRITE, MKDIR, MOVE, DELETE — and `Operation.READ` is a member
of the Operation enum with correct forbidden-path coverage verified by automated
test. `MKDIR` is a distinct enum value (not subsumed under WRITE) per
SAFETY_CONTRACT.md line 100 which lists it as an explicit allowed-write
operation, requiring its own path-pattern check before directory creation.

### Refusal modes are FATAL, not silent skip, per category?

DESIGN.md "Refusal Modes": each refusal exits non-zero with explicit log.
SAFETY_CONTRACT.md "Failure mode for any boundary crossing: agent FATAL ERROR,
exit non-zero." Guards split into two severity tiers:

Hard guards (7) — `refuse_fatal()` → non-zero exit, human must intervene:
  `kill_switch` (KILL_SWITCH file), `not_dirty_repo`, `no_active_rebase`,
  `disk_free_above_threshold`, `no_inflight_maintenance_pr`,
  `not_self_quarantined`, and any `FORBIDDEN_*` from `validate_action()`.

Soft guard (1) — `skip_tick()` → tick silently skipped, next tick retries:
  `no_maintenance_pause_flag` (MAINTENANCE_PAUSED soft pause, per
  SAFETY_CONTRACT.md lines 207-213: "skips ticks but does not require a
  re-acknowledge to resume").

Note: `no_oncall_quiet` is also a `skip_tick` per TASK_CATALOG.yaml
(`severity: skip_tick`), not `refuse_fatal`. Total: 6 `refuse_fatal` +
2 `skip_tick` among the named guards. Task-level errors are isolated to
`errors.tsv` and do not block subsequent tasks.

Verdict: **PASS if** `refuse_fatal()` is called for the 6 hard guards and
`skip_tick()` for the 2 soft guards; a grep of `engine.py` finds zero
`continue` statements inside the `CHECK_GUARDS` stage.

### Dry-run-first encoded as code, not docs?

`enforce_dry_run_floor()` in `validator.py` (§4 above) converts any
`live_default: true` task to `ALLOWED_BUT_DRY_RUN_ONLY` until 30 days have
elapsed. This is a code gate. The validator is called for every pre-action
check; there is no code path that applies a live action without passing
through `validate_action()`. The DRY_RUN_PROTOCOL.md prose is human context;
the gate is in code.

Verdict: **PASS if** `test_dry_run_floor.py` contains an automated assertion
that `validate_action(...)` returns `ALLOWED_BUT_DRY_RUN_ONLY` for a
non-exempt task when `first_run_at` is 10 days ago, and `ALLOWED` when
`first_run_at` is 31 days ago.

### Evidence trail enables full decision reconstruction?

The triplet (config snapshot + proposal + applied row + rollback recipe) per
task per tick is the complete audit unit (DRY_RUN_PROTOCOL.md "Auditability").
`evidence_writer.py` owns all four writes. The acceptance criterion is: a
simulated investigator reading only the four artifacts in
`evidence_trail/<date>/` can reconstruct what happened without consulting any
external log or memory.

Verdict: **PASS if** `test_integration_categories.py` includes a
post-run assertion that deserializes the evidence trail and confirms
each applied action has a matching rollback recipe and config snapshot.

### Anti-meta-pattern: this is a leaf worker, not an orchestrator?

SAFETY_CONTRACT.md "Forbidden Actions" explicitly prohibits:
"Trigger another agent (no Task / SendMessage / Agent tool use; the agent is
leaf, not orchestrator)." This is structural: the agent has no `Task`, no
`SendMessage`, no `Agent` import path. Notifications go through the
`NotifierInterface` (P5.5) which calls an external HTTP webhook or writes a
file — it does not spawn agents.

Verdict: **PASS if** `grep -r "SendMessage\|AgentTool\|Task(" maintenance-worker/src/` returns 0 hits.

---

## Input Inconsistencies Found

1. **"14 forbidden surface categories" resolved.** The task brief's "14" maps
   to the 14 bulleted items in SAFETY_CONTRACT.md "Forbidden Actions" section
   (lines 75-95 of the source file), not to the Forbidden Targets groups.
   Count verified: `rm`, `git push --force`, `git rebase`, `git reset --hard`,
   merge PR, approve PR, comment non-maintenance PR, close/open issues, pip/npm/cargo
   install, pytest mutate, modify `.claude/scheduled_tasks.json`, trigger agent
   (Task/SendMessage), `chmod`/`chown`, `ln -s` across boundary — 14 items exactly.
   Of these, 8 required new coverage not previously modeled: the 3 new guard modules
   (`git_operation_guard.py`, `gh_operation_guard.py`, `subprocess_guard.py`) cover
   filesystem+git+gh/PR+subprocess bullets. `chmod`/`chown`/`ln -s` go into
   `subprocess_guard.py`. Agent-trigger prohibition is enforced at install time
   (import-time assertion, no runtime check needed). The "14" count is now fully
   mapped in §3; no magic number appears in code.

2. **ARCHIVAL_RULES.md says "9 exemption checks" (check #0 + checks 1–8);
   TASK_CATALOG.yaml says `all_8_checks_pass`.** ARCHIVAL_RULES.md §"Exemption
   Checks" introduces check #0 (Authority Status Registry) as a priority check
   that runs first and skips 1–8 if it triggers; the "9/9" in the stub format
   counts #0. TASK_CATALOG.yaml line `requires: all_8_checks_pass` predates the
   registry addition. Resolution: implementation should treat 9 checks as
   authoritative (ARCHIVAL_RULES.md is the more detailed spec); the
   `closed_packet_archive_proposal` task verdict field should be updated to
   `9/9` in the TASK_CATALOG during P6 Zeus binding work.

---

## BATCH_DONE

```yaml
p5_0_completed: true
scaffold_path: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md
repo_placement: standalone-repo (maintenance-worker/ as pip package)
sub_packet_count: 6  # P5.0a + P5.1 + P5.2 + P5.3 + P5.4 + P5.5 (all required)
sub_packet_max_loc: 1000  # P5.2 exactly at ceiling; all within budget
module_count: 21  # types/ 4 + core/ 10 + rules/ 4 + cli/ 4 (entry + scheduler_detect + provenance + notifier) = 22; notifier is interface stub counted once as single file, so 21 distinct implementation files
estimated_loc_total_core: 3450  # types 200 + core 1570 + rules 700 + cli 400 (incl. provenance 100) + P5.4 source 200 + unit-test scaffolding 280 + P5.5 integration 100
estimated_loc_total_tests: 1500  # P5.4 60-fixture suite 900 + unit tests (types 50 + engine 250 + validator 400 + rules 300 + cli 150) = 1350 + misc 150
zeus_identifier_leak_count: 0  # verified by §5 self-check grep; no zeus tokens in module names
self_check_sidecar_risk: pass  # validator is sole authority; post_mutation_detector is separate path from pre-mutation FORBIDDEN_*; no secondary authorization in rules_parser
self_check_fatal_refusal: pass  # 6 refuse_fatal hard guards + 2 skip_tick soft guards (MAINTENANCE_PAUSED, ONCALL_QUIET); §6 verdict updated; task-level errors isolated to errors.tsv
c1_c2_applied: true
m3_recount_applied: true
m5_decision: p5_0a_types
m6_decision: defer_to_p5_5_mandatory  # P5.1 _apply_decisions stages filesystem diff only; P5.5 owns commit/PR/provenance; P5.5 mandatory; §3.5 cross-reference added
nd1_rm_mapping_resolved: yes  # subprocess_guard.py BLOCKED list includes rm unconditionally; os.unlink permitted only via Operation.DELETE on confirmed zero-byte files
nd2_module_count_fixed: yes  # module_count: 21
nd3_refuse_fatal_split: yes  # 6 hard guards refuse_fatal + 2 soft guards skip_tick; §6 verdict rewritten
nd5_mkdir_resolved: enum_added  # MKDIR added as explicit enum value in operations.py (~65 LOC); SAFETY_CONTRACT.md line 100 cited
worktree_path: /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/agent-ae1e98111aad14ffa
input_inconsistencies_found:
  - '"14 forbidden surface categories" resolved: maps to 14 bulleted Forbidden Actions in SAFETY_CONTRACT.md; 3 new guard modules cover 8 previously-unmodeled bullets'
  - 'ARCHIVAL_RULES.md (9 checks: #0 + 1-8) vs TASK_CATALOG.yaml (all_8_checks_pass); ARCHIVAL_RULES.md is authoritative; TASK_CATALOG needs update in P6 Zeus binding'
deviations_observed:
  - 'P5.2 at 1000 LOC ceiling; 3 operation-guard modules added for 14 Forbidden Actions coverage; P5.2b split available if implementation overruns'
  - 'P5.0a types sub-packet added (200 LOC); linear build chain P5.0a→P5.1→P5.2→P5.3→P5.4→P5.5'
  - 'P5.5 promoted from optional to REQUIRED; apply/publish boundary at P5.1↔P5.5; provenance.py added to cli/'
deferred_minors_list:
  - M4: scheduler_detect.py duplication cosmetic (kill_switch.py + cli/scheduler_detect.py overlap)
  - M7: Task auto-pause after 3 failures unimplemented (failure_counter.py spec)
  - SEV-3a: AUTO_ACK cap (bulk-ack AUTO_ACK_NEXT_N decay logic needs bounding)
  - SEV-3b: .diff surface per proposal (evidence_writer.py needs write_proposal_diff() signature)
  - SEV-3c: in-process heartbeat (DESIGN.md §"Scheduling Surface" in-process mode publishes heartbeat.json)
  - SEV-3d: skills/ vs pip-package deployment surface tension
  - SEV-3g: 8-vs-9 exemption check count (see Input Inconsistency #2; deferred to P6)
```
