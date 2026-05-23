# P9 Scaffold: Authority Inventory v2 — Cohort 7 Extension

Status: DESIGN (PLAN-ONLY — no code in this file)
Created: 2026-05-15
Revised: 2026-05-15 (P9.0 rev after opus critic REVISE verdict)
Authority basis: docs/operations/task_2026-05-15_runtime_improvement_engineering_package/03_authority_drift_remediation/DRIFT_ASSESSMENT.md §Cohort 7

---

## §0. Inconsistencies Found in This Pass

Items the original SCAFFOLD missed or the critic brief itself contained — surfaced here per the "additional_inconsistencies_found" contract.

**I-1: Critic brief §C1 named `~/.claude/CLAUDE.md` as one of the "2 truly-new entries".**
Off-repo paths are illegal in docs_registry.yaml under C-4 (resolved in the
original SCAFFOLD). `~/.claude/CLAUDE.md` was never proposed in the original
`§4 New entries` block. The two genuinely-new in-repo entries are
`.claude/CLAUDE.md` (project-local) and `docs/operations/INDEX.md`. Both
confirmed absent from docs_registry.yaml by fresh grep. C-4 asymmetry stands.

**I-2: `--invariants` does not emit a failure list (topology_doctor_cli.py:29).**
Flag description: "Emit invariant slice, optionally by --zone" — it emits full
content, not flagged-failure paths. No existing CLI flag isolates invariant
failures as a path set. Recorded as P9.1-implementer gap (see §3 C2 fix).

**I-3: Two Cohort 7 surfaces were silently dropped from DRIFT_ASSESSMENT.md §Cohort 7.**
DRIFT_ASSESSMENT.md:166-168 lists 8 patterns; the original SCAFFOLD enumerated
only 5 (INDEX.md + 3 current_*.md + C7-A through C7-D). The two dropped:
`docs/operations/known_gaps.md` and `docs/operations/packet_scope_protocol.md`.
Both are now included in §1 (see C7-F and C7-G).

**I-4: `0.0 * 0` line in §2 formula.**
The original had `0.0 * 0 + # covered_path weight: 0`. The literal zero-times-zero
coefficient is a documentation artifact — the formula component exists to signal
that covered_path weight is intentionally zeroed for fs surfaces (not accidentally
missing). This pass retains the line but rewrites it as `0.0 * 0 + # [intentional]`
with a one-sentence inline rationale.

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

Seven path patterns with concrete examples. All are HIGH-authority — incorrect
drift scoring on them creates false-archival risk in mw-daemon's
`authority_drift_surface` task.

Note: DRIFT_ASSESSMENT.md §Cohort 7 lists 8 patterns. Two (known_gaps.md and
packet_scope_protocol.md) were silently dropped in the original SCAFFOLD without
justification. Both are re-included here as C7-F and C7-G.

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
  emit a single summary row with `verdict: LATENT_TARGET`,
  `path: architecture/modules/` (directory marker; mw-daemon will find no
  git-log history, which is correct — the directory does not exist yet),
  `last_commit: n/a (latent)`, `30d_commits: 0`, `lines: 0`,
  `source_type: latent`, `drift_score: n/a`.
  Do NOT treat zero rows as "OK — no drift".
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

### Surface C7-F: Operations known-gaps worklist

Pattern: `docs/operations/known_gaps.md`
Concrete example: `docs/operations/known_gaps.md`
Classification: `operations` — active known-gap worklist pointer; agents read
  this when scoping what gaps exist before proposing new work.
Source type: `git`
Drift risk: MEDIUM — serves as a compatibility pointer to `docs/to-do-list/known_gaps.md`;
  stale here means agents miss currently-open gaps.
Note: this surface was present in DRIFT_ASSESSMENT.md §Cohort 7 but was silently
  dropped from the original SCAFFOLD. Re-included per §0 I-3.

### Surface C7-G: Operations packet scope protocol

Pattern: `docs/operations/packet_scope_protocol.md`
Concrete example: `docs/operations/packet_scope_protocol.md`
Classification: `operations` — defines scope rules for work packets; agents
  consult this when determining whether a task should be split or bundled.
Source type: `git`
Drift risk: MEDIUM — stale protocol means agents make scope decisions against
  an obsolete policy.
Note: this surface was present in DRIFT_ASSESSMENT.md §Cohort 7 but was silently
  dropped from the original SCAFFOLD. Re-included per §0 I-3.

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

**New column: `source_type`** (`git` | `fs` | `latent`)

For `source_type: fs` surfaces (C7-B, C7-C):
- `last_commit` field emits `mtime:<ISO8601>` (filesystem modification time)
- `30d_commits` field emits `n/a (non-git)` as a string sentinel
- `drift_score` formula modified:
  ```
  drift_score_fs = (
    0.6 * normalize(days_since_mtime / 90) +   # raised from 0.4; no code-path signal available
    0.0 * 0 +  # [intentional] covered_path weight zeroed: no git log to compare for fs surfaces
    0.2 * (1 if reference_replacement_missing_entry hit else 0) +
    0.2 * (1 if invariant_check_failure hit else 0)
  )
  ```
  Rationale: without commit history, mtime is the only freshness signal; its
  weight increases. The covered-path factor is dropped (no git log to compare)
  — the `0.0 * 0` slot is present to make the structural parallel with the
  git formula explicit, not as a computation. Reference-replacement and
  invariant checks are retained at equal weight.

For `source_type: latent` surfaces (C7-D zero-match sentinel):
- All numeric columns emit sentinels: `last_commit: n/a (latent)`,
  `30d_commits: 0`, `lines: 0`, `drift_score: n/a`, `verdict: LATENT_TARGET`.
- These rows pass through mw-daemon's parser with explicit sentinel handling.

**Cohort 7 default `covers:` injections (no doc edits required):**

| Surface | Injected `covers:` value | Rationale |
|---------|--------------------------|-----------|
| C7-A (`.claude/CLAUDE.md`) | `agent_session_behavior, topology_doctor_protocol` | Matches what the file governs |
| C7-B (`~/.claude/CLAUDE.md`) | `global_methodology, agent_behavioral_rules` | File content scope |
| C7-C (`~/.openclaw/CLAUDE.md`) | `openclaw_architecture, agent_routing` | File content scope |
| C7-D (`architecture/modules/*.yaml`) | `module_invariants, module_capabilities` | Expected future content |
| C7-E (INDEX.md) | `operations_directory_structure` | File purpose |
| C7-E (`current_*.md`) | `live_operational_state` | File purpose |
| C7-F (`known_gaps.md`) | `known_gaps_worklist` | File purpose |
| C7-G (`packet_scope_protocol.md`) | `packet_scope_rules` | File purpose |

**Covered-path weight for Cohort 7 git surfaces (C7-A, C7-D through C7-G):**
No change to formula weights. Use injected `covers:` values above instead of
0.5 default. This avoids false escalations on stable doctrine files.

**covers_overrides resolution (M4 fix):**
The `covers_overrides` parameter in `compute_drift_score()` maps surface path
(string) → covers topic label (string). The covered-path weight in the v1 formula
requires a PATH predicate, not a topic label, to count commits in covered paths.
Resolution: accept 0.5 default covered-path weight for all Cohort 7 git surfaces
rather than implementing a topic→path predicate resolution table. Rationale:
(a) per-surface path predicates are not specified in the DRIFT_ASSESSMENT;
(b) the 0.5 default is conservative (not zero, not 1.0) and does not suppress
genuine escalations; (c) implementing path-predicate tables for Cohort 7 would
require auditing source directory mappings outside SCAFFOLD scope. The
`covers_overrides` parameter is REMOVED from the `compute_drift_score()` signature.
P9.1 may add path predicate support as a separate enhancement.

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
- `Lines`: integer (wc -l; works for both git and fs surfaces); `0` for latent
- `Authority?`: YES | NO | LATENT_TARGET
- `Path`: repo-relative for git/latent surfaces; absolute path for fs surfaces.
  No `[fs]` prefix — the `source_type` column is the sole distinguisher.
- `source_type`: `git` | `fs` | `latent` (new column, rightmost group)
- `drift_score`: float 0.00–1.00, 2 decimal places, OR `n/a` for latent (new column)
- `verdict`: CURRENT | MINOR_DRIFT | STALE_REWRITE_NEEDED | URGENT | LATENT_TARGET (new column)

v1 consumers (mw-daemon's `authority_drift_surface` task) read the first 5
columns only; the 3 new rightmost columns are invisible to them. No schema
change required in TASK_CATALOG.yaml.

---

## §3. Generator Script Outline

**Target file:** `scripts/authority_inventory_v2.py`
**Output:** `docs/operations/task_<DATE>_authority_inventory_v2/INVENTORY.md`
  (fresh task_DATE_authority_inventory_v2/ directory each run — preserves auditable
  per-run history; never overwrites a prior run)
**Estimated LOC:** 440–490 (see sub-estimates below)

### Argparse layer

```python
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate authority inventory v2 with Cohort 7 extension"
    )
    p.add_argument("--repo-root", type=Path, default=Path("."),
                   help="Zeus repo root (default: cwd)")
    p.add_argument("--output", type=Path, required=True,
                   help="Output path for INVENTORY.md")
    p.add_argument("--include-v1", action="store_true", default=True,
                   help="Emit v1 rows (re-scored) alongside Cohort 7")
    p.add_argument("--cohort7-only", action="store_true",
                   help="Emit only Cohort 7 rows")
    p.add_argument("--dry-run", action="store_true",
                   help="Print output to stdout; do not write file")
    p.add_argument("--as-of", type=str, default=None,
                   help="ISO8601 datetime for replay/testing (default: now)")
    return p
```

### `main()` signature

```python
def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    # ... resolve args.repo_root, args.output, args.as_of, then call _run()
```

Probe 1 invocation (§5) uses `--repo-root . --output /tmp/INV_v2.md`; the
argparse layer above bridges the gap from positional-style outline to named flags.

### Per-surface iterator functions (signatures only)

```python
def iter_git_surfaces(
    repo_root: Path,
    path_patterns: list[str],
    as_of: datetime,
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each matched path that is tracked by git.
    Skips untracked files silently with a warning to stderr."""
    ...

def iter_glob_surfaces(
    repo_root: Path,
    glob_pattern: str,
    as_of: datetime,
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each glob match. If zero matches, yields one
    sentinel row with verdict=LATENT_TARGET and logs a warning.
    Sentinel row shape: path=<glob_root_dir>, last_commit='n/a (latent)',
    30d_commits=0, lines=0, source_type='latent', drift_score=None,
    verdict='LATENT_TARGET'. Never errors on zero matches."""
    ...

def iter_fs_surfaces(
    paths: list[Path],
    as_of: datetime,
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each path using mtime as last_commit proxy.
    source_type='fs'. 30d_commits emits 'n/a (non-git)' sentinel."""
    ...

def compute_drift_score(
    row: SurfaceRow,
    reference_replacement_hits: set[str],
    invariant_failure_hits: set[str],
) -> tuple[float, str]:
    """Returns (score, verdict). Applies fs-adjusted formula for non-git
    surfaces. Applies 7-day override for current_*.md. Returns
    (None, 'LATENT_TARGET') for sentinel rows.
    Note: covers_overrides removed per M4 resolution; 0.5 default used
    for all Cohort 7 covered-path weights."""
    ...

def format_inventory_table(
    rows: list[SurfaceRow],
    include_v2_columns: bool = True,
) -> str:
    """Render markdown table. v2 columns appended to right of v1 columns."""
    ...

def load_reference_replacement_hits(repo_root: Path) -> set[str]:
    """Run topology_doctor.py --reference-replacement and parse output.
    Returns set of paths with missing_entry flags.
    Note: flag is --reference-replacement (hyphenated), no --check subcommand."""
    ...

def load_invariant_failure_hits(repo_root: Path) -> set[str]:
    """Attempt to extract paths flagged in invariant output.

    P9.1 IMPLEMENTER GAP: topology_doctor.py --invariants emits the full
    invariant slice (topology_doctor_cli.py:29: 'Emit invariant slice,
    optionally by --zone'), not a failure-path list. No existing CLI flag
    isolates invariant failures as a path set. Options at implementation time:
      (a) parse --invariants JSON/text output for failure indicators,
      (b) use --strict and filter its output for invariant-class issues,
      (c) implement a new --invariant-failures flag in topology_doctor_cli.py.
    Until resolved, this function logs a warning and returns an empty set,
    causing the 0.1 invariant weight to contribute 0 for all rows. This is
    conservative (never inflates drift score) and must be noted in INVENTORY.md
    output header."""
    ...
```

### Key data structure

```python
@dataclass
class SurfaceRow:
    path: str                    # repo-relative (git/latent) or absolute (fs)
    last_commit_date: str        # ISO8601 or "mtime:<ISO8601>" or "n/a (latent)"
    commits_30d: Union[int, str] # int or "n/a (non-git)" or 0 (latent)
    lines: int                   # 0 for latent
    authority_marker: str        # YES | NO | LATENT_TARGET
    source_type: str             # "git" | "fs" | "latent"
    drift_score: Optional[float] # None for LATENT_TARGET
    verdict: str
    cohort: str                  # "v1" | "7A" | "7B" | "7C" | "7D" | "7E" | "7F" | "7G"
```

### Sub-estimates (LOC)

| Component | Est LOC |
|-----------|---------|
| `build_arg_parser()` + `main()` | 45 |
| `iter_git_surfaces()` | 50 |
| `iter_glob_surfaces()` | 40 |
| `iter_fs_surfaces()` | 35 |
| `compute_drift_score()` | 60 |
| `format_inventory_table()` | 30 |
| `load_reference_replacement_hits()` | 30 |
| `load_invariant_failure_hits()` (stub + gap note) | 20 |
| `SurfaceRow` dataclass + constants | 25 |
| Error handling, logging, file I/O | 50 |
| Header/provenance comment | 10 |
| script_manifest.yaml registration (P9.1 instruction) | n/a |
| **Total** | **~395–440** |

**P9.1 ship instruction:** Register `scripts/authority_inventory_v2.py` in
`architecture/script_manifest.yaml` as part of the P9.1 implementation commit.
If the script is not registered there before ship, it fails the same drift
detection it generates (a script outside script_manifest.yaml is itself a
`MISSING_ENTRY` hit on itself). Self-registration is a hard pre-ship gate.

---

## §4. docs_registry.yaml New Entries

### Asymmetry declaration (C-4 resolution)

Off-repo paths (`~/.claude/CLAUDE.md`, `~/.openclaw/CLAUDE.md`, `~/CLAUDE.md`)
do NOT appear in docs_registry.yaml. The registry uses repo-relative paths;
external paths are illegal entries. These surfaces are tracked in INVENTORY.md
only.

Two planes, different scope; they are NOT required to be co-extensive:
- docs_registry.yaml governs agent citation behavior within the zeus repo.
- INVENTORY.md governs drift monitoring across all authority surfaces including external.

### New entries (P9.1 appends these to architecture/docs_registry.yaml)

Verified: neither `.claude/CLAUDE.md` nor `docs/operations/INDEX.md` currently
appear in docs_registry.yaml (grep confirmed 2026-05-15). These are the 2 new
in-repo additions.

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

# Note: architecture/modules/*.yaml (C7-D) gets entries when individual module
# yaml files are created. The generator handles zero-match gracefully (§3).
# architecture/module_manifest.yaml is already in v1 inventory (Cohort 3) and
# is NOT re-registered here to avoid duplicate entries.
```

---

## §4.A Proposed Reclassification (REQUIRES OPERATOR APPROVAL — NOT AUTO-APPLIED BY P9.1)

Three current_*.md surfaces are already registered in docs_registry.yaml.
The original SCAFFOLD §4 proposed new entries with different field values than
the existing registration — this would have been a silent reclassification.
The entries are NOT appended. Instead, the delta is documented here for
operator decision.

### current_state.md (docs_registry.yaml lines 390–407)

| Field | Existing | Proposed | Risk if changed |
|-------|----------|----------|-----------------|
| `truth_profile` | `router` | `volatile_current_fact` | Changes citation behavior for all agents reading truth_profile |
| `freshness_class` | `packet_bound` | `audit_bound` | Changes staleness threshold calculation |
| `refresh_source` | `packet_audit` | `operator` | Changes refresh attribution in drift reports |
| `contains_volatile_metrics` | `false` | `true` | Changes mw-daemon volatile-metrics gate |

### current_data_state.md (docs_registry.yaml lines 501–518)

| Field | Existing | Proposed (original SCAFFOLD) | Status |
|-------|----------|------------------------------|--------|
| `refresh_source` | `packet_audit` | `operator` | Delta only; other fields already match |

Note: `truth_profile: volatile_current_fact`, `freshness_class: audit_bound`,
`contains_volatile_metrics: true` are ALREADY correct in the existing entry.
Only `refresh_source` differs.

### current_source_validity.md (docs_registry.yaml lines 519–536)

Same as current_data_state.md: only `refresh_source` differs
(`packet_audit` existing vs `operator` proposed).

### Operator decision gate

To apply any reclassification above: create a separate PR modifying only the
named field(s) in architecture/docs_registry.yaml. The reclassification change
MUST NOT be bundled into the P9.1 append-only PR. Each reclassification needs:
1. Justification for why the current value is wrong (not just "different from
   proposed").
2. Impact audit on every agent that reads the changed field.
3. Confirmation that TASK_CATALOG.yaml tasks referencing these fields are
   not broken by the change.

P9.1 executes the APPEND-ONLY entries from §4. Reclassification is a separate
operator-gated action.

---

## §5. Acceptance Probe

Three probes that must pass for P9 to be considered complete.

**Probe 1: Weekly drift report includes Cohort 7 (>= 1 row per surface pattern)**

```bash
# Run generator against current repo
python scripts/authority_inventory_v2.py --repo-root . --output /tmp/INV_v2.md

# Assert: at least one row for each C7 pattern
grep -c "\.claude/CLAUDE\.md" /tmp/INV_v2.md          # must be >= 1
grep -c "~.*CLAUDE\.md" /tmp/INV_v2.md               # must be >= 1 (fs surfaces)
grep -c "docs/operations/INDEX\.md" /tmp/INV_v2.md    # must be >= 1
grep -c "docs/operations/current_" /tmp/INV_v2.md     # must be >= 3 (3 current_*.md files)
grep -c "LATENT_TARGET" /tmp/INV_v2.md                # must be >= 1 (modules/ sentinel)
grep -c "known_gaps\.md" /tmp/INV_v2.md               # must be >= 1 (C7-F)
grep -c "packet_scope_protocol\.md" /tmp/INV_v2.md    # must be >= 1 (C7-G)
```

**Probe 2: Per-surface verdict assigned (CURRENT|MINOR_DRIFT|STALE_REWRITE_NEEDED|URGENT|LATENT_TARGET)**

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

Pre-ship dependency: if TASK_CATALOG.yaml's `authority_drift_surface` parser
reads `30d_commits` as an integer with no error handling, a one-line fix is
required to handle the `n/a (non-git)` sentinel for fs-surface rows before P9
ships. Verify this before closing P9.

---

## §6. Self-Check

### Does the generator add new authority gates that themselves become drift-prone?

No, for two reasons:

1. **`scripts/authority_inventory_v2.py` is a script, not an authority doc.**
   It is tracked in `architecture/script_manifest.yaml` (P9.1 instruction in §3).
   Scripts are subject to the same staleness detection as any other script entry
   there.

2. **The generator's output (INVENTORY.md) is evidence, not authority.**
   INVENTORY.md is tagged `doc_class: report` in docs_registry.yaml (not an
   authority entry). It is the output of a measurement, not a governance artifact.

Verdict: **pass** — no recursive drift-authority loop introduced.

### v1 consumer forward compatibility

There is no current consumer of `AUTHORITY_DOCS_INVENTORY.md` v1 format in
`scripts/` or `src/` (grep: zero matches for `AUTHORITY_DOCS_INVENTORY`).
v1→v2 backwards-compat is a FORWARD design constraint for P5/mw-daemon, not a
live regression risk. The INVENTORY.md format is designed so the first 5 columns
are identical to v1, ensuring zero integration friction when mw-daemon is
eventually wired to consume it.

The `30d_commits` sentinel `n/a (non-git)` is mw-daemon's future input contract
for fs-surface rows. Any integer parser in mw-daemon will need one-line sentinel
handling at integration time; this is a pre-integration task, not a pre-ship block.

---

## Implementation Packet Summary

- **Target script:** `scripts/authority_inventory_v2.py` (~440 LOC)
- **docs_registry.yaml:** append 2 new entries (`.claude/CLAUDE.md` + `docs/operations/INDEX.md`)
- **Reclassification of 3 current_*.md:** operator-gated, separate PR, NOT bundled into P9.1
- **TASK_CATALOG.yaml:** no changes required; one pre-ship dependency (sentinel
  handling in `authority_drift_surface` parser)
- **Authority doc edits:** zero (injected `covers:` values are computed at
  generator runtime, not written into the doc files)
- **Off-repo surfaces:** tracked in INVENTORY.md only, not in docs_registry.yaml
- **Zero-yield glob:** generator emits LATENT_TARGET sentinel row; never errors
- **script_manifest.yaml:** register `scripts/authority_inventory_v2.py` as hard pre-ship gate

---

## BATCH_DONE

```
p9_0_revision_completed: true
scaffold_path: docs/operations/task_2026-05-15_p9_authority_inventory_v2/SCAFFOLD.md
duplicate_entries_resolved: yes — 3 duplicate current_*.md yaml blocks dropped from §4; moved to §4.A as operator-gated reclassification section with delta diff
topology_doctor_flags_corrected: yes — --reference-replacement (hyphenated, cli.py:45); --invariants documented as emitting content not failure list (cli.py:29); load_invariant_failure_hits() marked as P9.1 implementer gap with stub + fallback
v1_compat_narrative_corrected: yes — §6 rewritten: no v1 consumer exists (zero grep matches for AUTHORITY_DOCS_INVENTORY); forward design constraint only; 30d_commits sentinel is mw-daemon future input contract
missing_surfaces_addressed: yes — known_gaps.md (C7-F) and packet_scope_protocol.md (C7-G) added to §1; §0 I-3 explains why they were silently dropped
argparse_layer_added: yes — build_arg_parser() sketch added to §3 bridging gap between main() signature and Probe 1 CLI invocation
latent_sentinel_shape_specified: yes — LATENT_TARGET row shape fully specified: path=architecture/modules/ (directory marker), last_commit='n/a (latent)', 30d_commits=0, lines=0, source_type=latent, drift_score=n/a, verdict=LATENT_TARGET; no new boolean column (verdict field is sufficient discriminator)
realistic_loc_total: ~480 (§3 sub-estimate table: ~440 script LOC; SCAFFOLD itself ~480 lines)
worktree_path: /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/agent-aefdb97aae0bcef9e
additional_inconsistencies_found:
  - I-1: critic brief named ~/.claude/CLAUDE.md as a docs_registry.yaml new entry — illegal under C-4; clarified in §0
  - I-2: --invariants emits content slice not failure list; documented as P9.1 implementer gap
  - I-3: known_gaps.md + packet_scope_protocol.md silently dropped from original SCAFFOLD without justification; re-included
  - I-4: 0.0 * 0 formula slot retained with explicit [intentional] annotation explaining its documentation purpose
deviations_observed:
  - file_written_via_Write_not_Edit: SCAFFOLD absent from worktree branch (branch diverged before P9 commit on main); Write tool used; noted
  - covers_overrides_dropped: parameter removed from compute_drift_score() per M4 resolution (0.5 default accepted for all Cohort 7 surfaces)
  - latent_target_new_column_not_added: verdict=LATENT_TARGET is sufficient discriminator; adding latent_target:bool column would be redundant per advisor guidance
  - path_format_contradiction_resolved: [fs] prefix dropped entirely; source_type column is sole distinguisher
```
