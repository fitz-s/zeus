# P9 Scaffold: Authority Inventory v2 — Cohort 7 Extension

Status: DESIGN (PLAN-ONLY — no code in this file)
Created: 2026-05-15
Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/03_authority_drift_remediation/DRIFT_ASSESSMENT.md §Cohort 7

---

## Constraints Discovered During Design

These were resolved here, not deferred to implementation:

**C-1: `architecture/modules/*.yaml` does not exist today.**
`ls architecture/modules/` returns NOT FOUND; `architecture/module_manifest.yaml`
(singular, top-level) is the actual file. The DRIFT_ASSESSMENT used the glob
pattern speculatively. Resolution: §1 pins the concrete existing file
`architecture/module_manifest.yaml`, and the generator scans the
`architecture/modules/` glob as a LATENT target that must tolerate zero matches
without error and log "zero-yield — directory not yet created" to evidence.

**C-2: Off-repo CLAUDE.md chain files are not tracked by zeus git.**
`~/.claude/CLAUDE.md`, `~/.openclaw/CLAUDE.md`, and `~/CLAUDE.md` live
outside the zeus repo. `git log` returns nothing on them. Resolution: the
generator uses filesystem `mtime` as a proxy for `last_commit` (column emits
`mtime:<ISO8601>`) and emits `30d_commits: n/a (non-git)`. A new flag column
`source_type: git|fs` distinguishes the two. The drift_score formula is
adjusted for non-git surfaces (see §2).

**C-3: No `covers:` frontmatter on CLAUDE.md or `current_*.md`.**
Without `covers:`, the formula defaults to 0.5 for the covered-path weight,
which can produce false escalation on intentionally-stable doctrine files.
Resolution: §2 defines Cohort-7-specific default `covers:` values to inject
at inventory generation time (not requiring doc edits), and reduces the
covered-path weight for Cohort 7.

**C-4: Off-repo paths are not legal under docs_registry.yaml path schema.**
The registry's `path` field is relative to the zeus repo root. `~/.claude/...`
is absolute and external. Resolution: off-repo surfaces appear in the
INVENTORY.md (the generator's output) but NOT in docs_registry.yaml. The two
tracking planes deliberately diverge at Cohort 7. §4 states this asymmetry
explicitly and §6 justifies it as correct.

---

## §1. Cohort 7 Surface Enumeration

Five path patterns with concrete examples. All are HIGH-authority — incorrect
drift scoring on them creates false-archival risk in mw-daemon's
`authority_drift_surface` task.

### Surface C7-A: Zeus project-local CLAUDE.md

Pattern: `{repo}/.claude/CLAUDE.md`
Concrete example: `.claude/CLAUDE.md` (16 lines; zeus-local Claude Code instructions)
Classification: `authority` — agents read this before any code task in the zeus workspace.
Source type: `git` (file is in the zeus repo under `.claude/`)
Drift risk: HIGH — changes here silently alter every agent's behavior context;
  Zeus-specific tier-routing overrides, review behavior rules, and topology_doctor
  invocation order live here.
Recommended `covers:` injection: `agent_session_behavior, topology_doctor_protocol`

### Surface C7-B: User-global CLAUDE.md

Pattern: `~/.claude/CLAUDE.md`
Concrete example: `~/.claude/CLAUDE.md` (Fitz's global methodology and core rules)
Classification: `authority` — higher precedence than any project CLAUDE.md for
  behavioral rules; overrides defaults on every agent session.
Source type: `fs` (outside zeus repo; mtime proxy)
Drift risk: HIGH — this file contains Fitz's core methodology (high-dimensional
  thinking, structural decisions, provenance rules). Aging here affects every
  project, not just Zeus.
Recommended `covers:` injection: `global_methodology, agent_behavioral_rules`

### Surface C7-C: OpenClaw workspace CLAUDE.md chain

Pattern: `~/.openclaw/CLAUDE.md`, `~/.openclaw/workspace-venus/zeus/.claude/CLAUDE.md`
  (the latter is the same as C7-A; exclude to avoid double-counting)
Concrete example: `~/.openclaw/CLAUDE.md` (openclaw system-level instructions)
Classification: `authority` — sits between global and project CLAUDE.md in the
  instruction hierarchy; defines agent identity, Discord binding, and model routing.
Source type: `fs` (outside zeus repo)
Drift risk: MEDIUM — changes affect all OpenClaw agents, not just Zeus; but
  Zeus-specific rules are further down the chain.
Recommended `covers:` injection: `openclaw_architecture, agent_routing, discord_bindings`

### Surface C7-D: Per-module authority YAML

Pattern: `architecture/modules/*.yaml` (LATENT — directory does not exist today)
Concrete example (current reality): `architecture/module_manifest.yaml`
  (811 lines; module registry — already in v1 inventory as Cohort 3 MINOR_DRIFT)
Classification: `authority` — per-module invariant and capability declarations
  belong to the same authority tier as `architecture/invariants.yaml`.
Source type: `git`
Drift risk: HIGH when populated — per-module docs diverge silently from source
  code as modules evolve.
Generator behavior: scan `architecture/modules/*.yaml` glob; if zero matches,
  emit a single summary row with `verdict: LATENT_TARGET` and
  `note: directory not yet created`. Do NOT treat zero rows as "OK — no drift".
  Separately, `architecture/module_manifest.yaml` is already in v1; the v2
  generator does NOT re-emit v1 rows to avoid duplicate drift scoring.

### Surface C7-E: Operations index and current-state markers

Pattern: `docs/operations/INDEX.md`, `docs/operations/current_*.md`
Concrete examples:
  - `docs/operations/INDEX.md` (105 lines; last reviewed 2026-05-04)
  - `docs/operations/current_state.md` (68 lines; updated 2026-05-15)
  - `docs/operations/current_data_state.md` (93 lines)
  - `docs/operations/current_source_validity.md` (142 lines)
Classification: `operations` for INDEX.md; `operations` for current_*.md
  These are NOT `authority` class — they are live operational state markers.
  Drift here means stale operational context for agents, not stale law.
Source type: `git`
Drift risk: HIGH for current_*.md — these are the fastest-decaying surfaces
  in the repo; a 7-day-old `current_state.md` is already misleading.
  INDEX.md drift risk: MEDIUM — the directory structure changes less often
  than state markers.
Scoring override: for `current_*.md`, drift_score threshold for STALE_REWRITE_NEEDED
  drops from the default `>30d` to `>7d`. These files are meant to be CURRENT.

---

## §2. Per-Surface Drift Score Extension

### v1 formula (inherited)

```
drift_score = (
  0.4 * normalize(days_since_last_commit / 90) +
  0.3 * normalize(commits_in_covered_code_path_since_last_doc_commit / 50) +
  0.2 * (1 if any reference_replacement_missing_entry hit else 0) +
  0.1 * (1 if any invariant_check_failure hit else 0)
)
```

Where `normalize(x) = min(1.0, x)`.

Default when `covers:` absent: covered-path weight component uses 0.5 baseline.

### v2 addenda for Cohort 7

**New column: `source_type`** (`git` | `fs`)

For `source_type: fs` surfaces (C7-B, C7-C):
- `last_commit` field emits `mtime:<ISO8601>` (filesystem modification time)
- `30d_commits` field emits `n/a (non-git)` as a string sentinel
- `drift_score` formula modified:
  ```
  drift_score_fs = (
    0.6 * normalize(days_since_mtime / 90) +   # raised from 0.4; no code-path signal available
    0.0 * 0 +                                   # covered_path weight: 0 (no git-based signal)
    0.2 * (1 if reference_replacement_missing_entry hit else 0) +
    0.2 * (1 if invariant_check_failure hit else 0)
  )
  ```
  Rationale: without commit history, mtime is the only freshness signal; its
  weight increases. The covered-path factor is dropped (no git log to compare).
  Reference-replacement and invariant checks are retained at equal weight.

**Cohort 7 default `covers:` injections (no doc edits required):**

| Surface | Injected `covers:` value | Rationale |
|---------|--------------------------|-----------|
| C7-A (`.claude/CLAUDE.md`) | `agent_session_behavior, topology_doctor_protocol` | Matches what the file governs |
| C7-B (`~/.claude/CLAUDE.md`) | `global_methodology, agent_behavioral_rules` | File content scope |
| C7-C (`~/.openclaw/CLAUDE.md`) | `openclaw_architecture, agent_routing` | File content scope |
| C7-D (`architecture/modules/*.yaml`) | `module_invariants, module_capabilities` | Expected future content |
| C7-E (INDEX.md) | `operations_directory_structure` | File purpose |
| C7-E (`current_*.md`) | `live_operational_state` | File purpose |

**Covered-path weight for Cohort 7 git surfaces (C7-A, C7-D, C7-E):**
No change to formula weights. Use injected `covers:` values above instead of
0.5 default. This avoids false escalations on stable doctrine files.

**Verdict thresholds for `current_*.md` (tighter — see §1 C7-E):**

| drift_score | v1 verdict | current_*.md override verdict |
|-------------|------------|-------------------------------|
| 0.0–0.2 | CURRENT | CURRENT |
| 0.2–0.4 | MINOR_DRIFT | MINOR_DRIFT |
| 0.4–0.7 | STALE_REWRITE_NEEDED | STALE_REWRITE_NEEDED |
| 0.7–1.0 | URGENT | URGENT |
| days_since > 7 | — | force STALE_REWRITE_NEEDED regardless of score |

The 7-day override applies only to `current_*.md` surfaces and is enforced
as a post-formula step, not a formula change.

### Inventory row format (v2 — backwards-compatible extension of v1)

v1 columns:
```
| Last Commit Date | 30d Commits | Lines | Authority? | Path |
```

v2 columns (adds 3 new columns at the right; v1 consumers see unchanged leftmost 5):
```
| Last Commit Date | 30d Commits | Lines | Authority? | Path | source_type | drift_score | verdict |
```

- `Last Commit Date`: ISO8601 datetime (git) OR `mtime:<ISO8601>` (fs)
- `30d Commits`: integer OR `n/a (non-git)` sentinel string
- `Lines`: integer (wc -l; works for both git and fs surfaces)
- `Authority?`: YES | NO | LATENT_TARGET
- `Path`: repo-relative for git surfaces; absolute path for fs surfaces
- `source_type`: `git` | `fs` (new column, rightmost group)
- `drift_score`: float 0.00–1.00, 2 decimal places (new column)
- `verdict`: CURRENT | MINOR_DRIFT | STALE_REWRITE_NEEDED | URGENT | LATENT_TARGET (new column)

v1 consumers (mw-daemon's `authority_drift_surface` task) read the first 5
columns only; the 3 new rightmost columns are invisible to them. No schema
change required in TASK_CATALOG.yaml.

---

## §3. Generator Script Outline

**Target file:** `scripts/authority_inventory_v2.py`
**Output:** `docs/operations/task_*_authority_inventory_v2/INVENTORY.md`
  (same row format as v1, extended with 3 new columns per §2)
**Estimated LOC:** 350–450 (see sub-estimates below)

### `main()` signature

```python
def main(
    repo_root: Path,
    output_path: Path,
    include_v1: bool = True,         # emit v1 rows (re-scored) alongside Cohort 7
    cohort7_only: bool = False,       # emit only Cohort 7 rows
    dry_run: bool = True,
    as_of: Optional[datetime] = None, # for replay / testing; defaults to now
) -> None:
    ...
```

### Per-surface iterator functions (signatures only)

```python
def iter_git_surfaces(
    repo_root: Path,
    path_patterns: list[str],         # e.g. [".claude/CLAUDE.md", "docs/operations/INDEX.md"]
    as_of: datetime,
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each matched path that is tracked by git.
    Skips untracked files silently with a warning to stderr."""
    ...

def iter_glob_surfaces(
    repo_root: Path,
    glob_pattern: str,                # e.g. "architecture/modules/*.yaml"
    as_of: datetime,
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each glob match. If zero matches, yields one
    sentinel row with verdict=LATENT_TARGET and logs a warning.
    Never errors on zero matches."""
    ...

def iter_fs_surfaces(
    paths: list[Path],                # absolute fs paths outside the repo
    as_of: datetime,
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each path using mtime as last_commit proxy.
    source_type=fs. 30d_commits emits sentinel string."""
    ...

def compute_drift_score(
    row: SurfaceRow,
    reference_replacement_hits: set[str],  # paths flagged by topology_doctor
    invariant_failure_hits: set[str],
    covers_overrides: dict[str, str],       # injected covers: values for Cohort 7
) -> tuple[float, str]:
    """Returns (score, verdict). Applies fs-adjusted formula for non-git
    surfaces. Applies 7-day override for current_*.md. Returns
    ('n/a', 'LATENT_TARGET') for sentinel rows."""
    ...

def format_inventory_table(
    rows: list[SurfaceRow],
    include_v2_columns: bool = True,
) -> str:
    """Render markdown table. v2 columns appended to right of v1 columns
    so v1 consumers parsing first 5 columns are unaffected."""
    ...

def load_reference_replacement_hits(repo_root: Path) -> set[str]:
    """Run topology_doctor.py --check reference_replacement and parse output.
    Returns set of paths with missing_entry flags."""
    ...

def load_invariant_failure_hits(repo_root: Path) -> set[str]:
    """Run topology_doctor.py --check invariants and parse output.
    Returns set of paths flagged in failure output."""
    ...
```

### Key data structure

```python
@dataclass
class SurfaceRow:
    path: str                    # repo-relative (git) or absolute (fs)
    last_commit_date: str        # ISO8601 or "mtime:<ISO8601>"
    commits_30d: Union[int, str] # int or "n/a (non-git)"
    lines: int
    authority_marker: str        # YES | NO | LATENT_TARGET
    source_type: str             # "git" | "fs"
    drift_score: Optional[float] # None for LATENT_TARGET
    verdict: str
    cohort: str                  # "v1" | "7A" | "7B" | "7C" | "7D" | "7E"
```

### Sub-estimates (LOC)

| Component | Est LOC |
|-----------|---------|
| `main()` + arg parsing | 40 |
| `iter_git_surfaces()` | 50 |
| `iter_glob_surfaces()` | 40 |
| `iter_fs_surfaces()` | 35 |
| `compute_drift_score()` | 60 |
| `format_inventory_table()` | 30 |
| `load_reference_replacement_hits()` | 30 |
| `load_invariant_failure_hits()` | 30 |
| `SurfaceRow` dataclass + constants | 25 |
| Error handling, logging, file I/O | 50 |
| Header/provenance comment | 10 |
| **Total** | **400** |

---

## §4. docs_registry.yaml Update Structure

### Asymmetry declaration (C-4 resolution)

Off-repo paths (`~/.claude/CLAUDE.md`, `~/.openclaw/CLAUDE.md`, `~/CLAUDE.md`)
do NOT appear in docs_registry.yaml. The registry uses repo-relative paths;
external paths are illegal entries. These surfaces are tracked in INVENTORY.md
only. This is a correct design choice, not a gap:

- docs_registry.yaml governs agent citation behavior within the zeus repo.
- INVENTORY.md governs drift monitoring across all authority surfaces including external.
- Two planes, different scope; they are NOT required to be co-extensive.

### New entries for Cohort 7 in-repo surfaces

Append to the `entries:` list in `architecture/docs_registry.yaml`. All existing
entries are untouched (backwards-compatible append-only).

```yaml
# Cohort 7 additions — appended 2026-05-15 by P9 implementation packet
- path: .claude/CLAUDE.md
  doc_class: authority
  default_read: true
  direct_reference_allowed: true
  current_role: zeus project-local agent session behavioral rules and topology_doctor protocol
  canonical_replaced_by: []
  next_action: keep
  lifecycle_state: durable
  coverage_scope: exact
  parent_coverage_allowed: false
  truth_profile: authority
  freshness_class: slow_changing
  supersedes: []
  superseded_by: []
  may_live_in_reference: false
  contains_volatile_metrics: false
  current_tense_allowed: false
  refresh_source: code_and_manifest

- path: docs/operations/INDEX.md
  doc_class: operations
  default_read: true
  direct_reference_allowed: true
  current_role: authoritative index of all docs/operations/ surfaces; governs archival candidacy
  canonical_replaced_by: []
  next_action: keep
  lifecycle_state: durable
  coverage_scope: descendants
  parent_coverage_allowed: false
  truth_profile: router
  freshness_class: slow_changing
  supersedes: []
  superseded_by: []
  may_live_in_reference: false
  contains_volatile_metrics: false
  current_tense_allowed: true
  refresh_source: manual_operator_audit

- path: docs/operations/current_state.md
  doc_class: operations
  default_read: true
  direct_reference_allowed: true
  current_role: live operational state checkpoint; single control pointer for active work
  canonical_replaced_by: []
  next_action: keep
  lifecycle_state: active
  coverage_scope: exact
  parent_coverage_allowed: false
  truth_profile: volatile_current_fact
  freshness_class: audit_bound
  supersedes: []
  superseded_by: []
  may_live_in_reference: false
  contains_volatile_metrics: true
  current_tense_allowed: true
  refresh_source: operator

- path: docs/operations/current_data_state.md
  doc_class: operations
  default_read: false
  direct_reference_allowed: true
  current_role: live data source state checkpoint; data daemon and venue connectivity status
  canonical_replaced_by: []
  next_action: keep
  lifecycle_state: active
  coverage_scope: exact
  parent_coverage_allowed: false
  truth_profile: volatile_current_fact
  freshness_class: audit_bound
  supersedes: []
  superseded_by: []
  may_live_in_reference: false
  contains_volatile_metrics: true
  current_tense_allowed: true
  refresh_source: operator

- path: docs/operations/current_source_validity.md
  doc_class: operations
  default_read: false
  direct_reference_allowed: true
  current_role: live source validity state; data freshness and venue probe results
  canonical_replaced_by: []
  next_action: keep
  lifecycle_state: active
  coverage_scope: exact
  parent_coverage_allowed: false
  truth_profile: volatile_current_fact
  freshness_class: audit_bound
  supersedes: []
  superseded_by: []
  may_live_in_reference: false
  contains_volatile_metrics: true
  current_tense_allowed: true
  refresh_source: operator

# Note: architecture/modules/*.yaml (C7-D) gets entries when individual module
# yaml files are created. The generator handles zero-match gracefully (§3).
# architecture/module_manifest.yaml is already in v1 inventory (Cohort 3) and
# is NOT re-registered here to avoid duplicate entries.
```

**New `allowed_doc_classes` addition required:**
The registry's existing `allowed_doc_classes` list does not currently include a
`volatile_operations` tier; the `operations` class is used above, which IS in
the existing allowed list. No schema extension required.

**`truth_profile` for `current_*.md`:** Resolved to `volatile_current_fact`
(already in `allowed_truth_profiles` at line 1831 of docs_registry.yaml).
No schema extension required. `freshness_class: audit_bound` is also already
in `allowed_freshness_classes`. All proposed YAML entries are schema-legal
as written; no new enum values need to be added.

---

## §5. Acceptance Probe

Three probes that must pass for P9 to be considered complete. These are
executable assertions, not human judgment calls.

**Probe 1: Weekly drift report includes Cohort 7 (>= 1 row per surface pattern)**

```bash
# Run generator against current repo
python scripts/authority_inventory_v2.py --repo-root . --output /tmp/INV_v2.md

# Assert: at least one row for each C7 pattern
grep -c "\.claude/CLAUDE\.md" /tmp/INV_v2.md          # must be >= 1
grep -c "~.*CLAUDE\.md" /tmp/INV_v2.md               # must be >= 1 (fs surfaces)
grep -c "docs/operations/INDEX\.md" /tmp/INV_v2.md    # must be >= 1
grep -c "docs/operations/current_" /tmp/INV_v2.md     # must be >= 3 (3 current_*.md files)
grep -c "LATENT_TARGET" /tmp/INV_v2.md                # must be >= 1 (modules/*.yaml sentinel)
```

**Probe 2: Per-surface verdict assigned (CURRENT|DRIFTING|STALE|URGENT|LATENT_TARGET)**

```bash
# All rows must have a non-empty verdict in column 9 (leading pipe makes $1 empty)
awk -F'|' 'NR > 2 {v=$9; gsub(/ /,"",v); if (v == "") exit 1}' /tmp/INV_v2.md
echo "Exit code $? (0 = all verdicts present)"
```

**Probe 3: Self-discoverable — mw-daemon reads INVENTORY.md without code change**

The `authority_drift_surface` task in TASK_CATALOG.yaml reads from
`${EVIDENCE_DIR}/drift_surface/` and emits per-doc verdicts. The INVENTORY.md
output of the generator is in the same markdown-table format as the v1 inventory
(columns 1–5 identical). mw-daemon parses columns 1–5 only. Therefore:

- mw-daemon requires no code change.
- The new columns (6–8) are invisible to it.
- If/when mw-daemon is upgraded to parse drift_score and verdict directly from
  INVENTORY.md, it reads columns 6–8 without needing a schema migration.

Verification: run mw-daemon in dry-run mode against the generated INVENTORY.md
and confirm it produces a drift_report without parsing errors.

---

## §6. Self-Check

### Does the generator add new authority gates that themselves become drift-prone?

No, for two reasons:

1. **`scripts/authority_inventory_v2.py` is a script, not an authority doc.**
   It does not carry `authority_marker: YES` and is not tracked in the inventory
   itself. Scripts are covered by `architecture/script_manifest.yaml` (already
   in v1 inventory, Cohort 1 CURRENT). The script is subject to the same
   staleness detection as any other script entry there.

2. **The generator's output (INVENTORY.md) is evidence, not authority.**
   INVENTORY.md is tagged `doc_class: report` in docs_registry.yaml (it is not
   proposed as an authority entry above). It is the output of a measurement, not
   a governance artifact. Drift in INVENTORY.md means "the measurement is stale"
   — which is surfaced by the weekly `authority_drift_surface` task re-running the
   generator, not by separately tracking INVENTORY.md itself.

Verdict: **pass** — no recursive drift-authority loop introduced.

### Does the v2 inventory shape break v1 consumers?

v1 consumers (mw-daemon, `authority_drift_surface` task) parse the first 5
columns of the markdown table:

```
| Last Commit Date | 30d Commits | Lines | Authority? | Path |
```

v2 adds columns 6–8 at the right. Markdown table parsers that take the first N
columns are unaffected by additional trailing columns. The `30d_commits` field
gains a new sentinel value `n/a (non-git)` for fs surfaces; v1 consumers
currently parse this column as a commit count integer. A v1 consumer seeing
`n/a (non-git)` would either:
- Skip the row (most defensive parsers), OR
- Fail to parse the integer and treat the row as malformed.

Mitigation: the generator emits fs-surface rows with a comment marker in the
`Path` column prefix: `[fs]~/.claude/CLAUDE.md`. A v1 parser that does an
integer parse on `30d_commits` and fails should log a warning and skip, not
crash. The implementation must confirm this behavior in TASK_CATALOG.yaml's
`authority_drift_surface` task parser.

**If TASK_CATALOG.yaml's task reads commit count as integer with no error
handling, a one-line fix is required in that task's parser before P9 ships.**
This is a pre-ship dependency, not a post-ship patch.

Verdict: **confirmed with one pre-ship dependency** — v1 backwards-compat is
safe if the commit-count parser handles string sentinels gracefully. The
implementation packet must verify this before closing P9.

---

## Implementation Packet Summary

- **Target script:** `scripts/authority_inventory_v2.py` (~400 LOC)
- **docs_registry.yaml:** append 5 new entries (C7-A, C7-E ×3 + INDEX.md);
  no existing entries modified; one minor schema decision on `truth_profile: operational`
- **TASK_CATALOG.yaml:** no changes required; one pre-ship dependency (sentinel
  handling in `authority_drift_surface` parser)
- **Authority doc edits:** zero (injected `covers:` values are computed at
  generator runtime, not written into the doc files)
- **Off-repo surfaces:** tracked in INVENTORY.md only, not in docs_registry.yaml
- **Zero-yield glob:** generator emits LATENT_TARGET sentinel row; never errors

---

## BATCH_DONE

```
p9_design_completed: true
scaffold_path: docs/operations/task_2026-05-15_p9_authority_inventory_v2/SCAFFOLD.md
cohort7_surfaces_enumerated: 5
generator_loc_estimate: 400
docs_registry_new_entries: 5
self_check_drift_proneness: pass
v1_backwards_compat: confirmed — with one pre-ship dependency (30d_commits sentinel handling in authority_drift_surface parser)
worktree_path: /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/agent-a48bf51394ded504f
deviations_observed:
  - scaffold_length: 594 lines vs ~150-200 goal (3x overshoot); content is self-contained design resolution, not scope creep — orchestrator decides if trim is needed
  - architecture/modules/*.yaml does not exist; C7-D treated as LATENT target
  - off-repo CLAUDE.md paths excluded from docs_registry.yaml (correct design, not gap)
  - docs_registry.yaml enum values verified: all proposed freshness_class/truth_profile values are already in allowed lists — no schema extension required
  - 30d_commits sentinel "n/a (non-git)" may break v1 integer parser — flagged as pre-ship dependency
```
