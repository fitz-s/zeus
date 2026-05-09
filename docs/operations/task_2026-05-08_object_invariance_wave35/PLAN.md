# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Authority basis: root AGENTS.md object-meaning invariance goal; docs/to-do-list/known_gaps.md CRITICAL SQLite live-lock gap; Wave35 topology route

# Object-Meaning Invariance Wave35 — Calibration Bulk Writer Isolation

Status: REPAIRED LOCALLY, AWAITING CRITIC, CODE/TEST ONLY, NOT DATA MUTATION AUTHORITY

This wave repairs the CRITICAL known gap where calibration rebuild/refit scripts
can materialize bulk calibration writes against the same canonical
`state/zeus-world.db` persistence object used by live runtime readers/writers.
SQLite has a single physical writer per database; using the live world DB as the
default target for long calibration jobs changes the object from "offline bulk
calibration artifact" into "live runtime persistence lock competitor."

This packet does not authorize running rebuild/refit, writing canonical DBs,
promoting calibration outputs, migrating schema, relabeling rows, or live
unlock.

## Candidate Boundary Selection

| Candidate | Live-money effect | Safe code repair now | Decision |
|---|---|---|---|
| SQLite calibration rebuild/refit writer -> live world DB | Can block daemon restart/live world writes during trading windows | Yes: fail closed before write-mode scripts target canonical `zeus-world.db` | Selected |
| D3 remaining live materialization fallback | Can still populate pending position economics from submitted/fallback price before fill authority | Yes, but already had two adjacent cost waves; lower immediate ops blast radius than live-lock | Next candidate |
| Source/settlement current-fact blockers | HK/WU/Taipei/DST can corrupt historical settlement/certification | No safe closure without external evidence or rebuild/operator-go | Blocked for code-only wave |
| RED direct venue side-effect SLA | Could delay direct cancel/sweep side effects | Requires live-side-effect packet/operator-go | Blocked |
| `hourly_observations` cleanup | Low live impact | Safe cleanup, but lower priority | Defer |

## Invariant Restored

Calibration bulk write-mode scripts must not treat the canonical live shared
world database as their default mutable target. If a calibration artifact is
computable but would require writing the live world DB, the script must fail
closed before opening a write connection. Dry-run/read-only inspection remains
allowed.

## Material Value Lineage

| Value | Real object denoted | Origin | Authority/evidence class | Unit/time basis | Transformation | Persistence | Consumers | Verdict |
|---|---|---|---|---|---|---|---|---|
| `--db` / `args.db_path` | Physical DB selected for calibration writes | CLI | Operator-selected persistence target | filesystem path at script start | Must resolve explicitly in write mode | sqlite DB path | rebuild/refit write connection | Ambiguous before repair |
| `ZEUS_WORLD_DB_PATH` | Canonical shared world DB used by live runtime/source/calibration readers | `src/state/db.py` | Canonical world persistence | live runtime persistence object | Dry-run may read; write-mode bulk jobs must not target by default | `state/zeus-world.db` | daemon/source/calibration runtime | Broken before repair |
| `calibration_pairs_v2` rows | Rebuilt calibration pair corpus | `scripts/rebuild_calibration_pairs_v2.py` | Bulk calibration artifact, not live runtime write permission | rebuild scope / n_mc / source identity | May be written only to explicit isolated DB in this wave | selected DB | Platt refit, transfer proof | Repaired by target gate |
| `platt_models_v2` rows | Refit calibration model authority candidate | `scripts/refit_platt_v2.py` | Bulk model artifact, promotion candidate | refit scope / rebuild sentinel | May be written only to explicit isolated DB in this wave | selected DB | live scoring after promotion | Repaired by target gate |

## Repair Plan

1. Add a small explicit write-target guard in `rebuild_calibration_pairs_v2.py`
   and `refit_platt_v2.py`.
2. In dry-run mode, preserve read-only canonical world DB inspection.
3. In write mode, require `--db` and reject any path resolving to
   `ZEUS_WORLD_DB_PATH`.
4. Keep existing `--no-dry-run --force`, preflight, rebuild-complete sentinel,
   per-bucket commit, and dry-run behavior intact.
5. Add relationship tests proving write-mode scripts refuse default/shared
   canonical DB targets before opening write connections, while an explicit
   isolated DB path may reach the existing write seam.

## Verification Plan

- `py_compile` for touched scripts/tests.
- Focused pytest in `tests/test_rebuild_live_sentinel.py`.
- Focused static/grep sweep for remaining write-mode default paths in the
  touched calibration scripts.
- Planning-lock and map-maintenance checks with this plan as evidence.
- Critic review after patch.

## Repair Summary

Implemented:

- `scripts/rebuild_calibration_pairs_v2.py` write mode now resolves an
  isolated calibration write target before preflight or write connection open.
  `--no-dry-run --force` without `--db` fails closed, and a `--db` resolving to
  canonical `ZEUS_WORLD_DB_PATH` fails closed.
- `scripts/refit_platt_v2.py` uses the same write-target guard before preflight
  or write connection open.
- Dry-run mode remains read-only and may inspect canonical world truth via
  `mode=ro` URI.
- The existing force gate, preflight gate, rebuild-complete sentinel gate,
  per-bucket commit behavior, and bulk writer lock remain in place for isolated
  DB writes.
- `architecture/script_manifest.yaml` now matches the executable contract:
  both rebuild/refit v2 entries declare `explicit_isolated_staging_db` as write
  target, not `state/zeus-world.db`, and state that isolated output is not live
  calibration authority without separate promotion evidence.

Finding classification:

| ID | Severity | Object meaning changed | Boundary | Economic effect | Active status | Repair |
|---|---|---|---|---|---|---|
| W35-1 | S0 | Offline/bulk calibration write artifact became canonical live world DB writer by default | CLI write target -> SQLite persistence authority | Long rebuild/refit can compete with live daemon on SQLite single-writer lock | Active in rebuild/refit CLIs before repair | Write mode requires explicit isolated `--db` and rejects canonical shared world DB before opening write connections |

Downstream contamination sweep:

| Surface | Result |
|---|---|
| Live daemon/world DB | Touched scripts no longer default to `ZEUS_WORLD_DB_PATH` on write path. Dry-run canonical reads remain read-only. |
| Calibration promotion | No promotion path added; isolated DB output still needs separate operator-approved promotion evidence. |
| Refit authority | Rebuild-complete sentinel gate remains; this wave changes only physical write target eligibility. |
| Legacy fallback | No compatibility flag added to bypass the shared-world rejection. |
| Script authority registry | Updated to prevent manifest consumers from reading these scripts as canonical world DB writers after the code change. |

## Verification

- `python -m py_compile scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py tests/test_rebuild_live_sentinel.py` — pass.
- `pytest -q tests/test_rebuild_live_sentinel.py` — `20 passed, 2 skipped`.
- Static sweep of touched scripts: write paths use `write_db_path`; remaining
  `ZEUS_WORLD_DB_PATH` references are dry-run read-only URI opens or the shared
  DB rejection guard.
- Critic pass 1: `REVISE` because `architecture/script_manifest.yaml` still
  declared both scripts as `state/zeus-world.db` writers. Fixed by updating the
  two manifest entries to isolated staging write targets plus promotion barriers.
- `topology_doctor --freshness-metadata --changed-files ...` — pass after
  updating test lifecycle header.
- `topology_doctor --planning-lock --plan-evidence ...` — pass.
- `topology_doctor --map-maintenance --map-maintenance-mode advisory ...` —
  pass.
- `topology_doctor --naming-conventions` — pass.
- `git diff --check` — pass.
- `topology_doctor --scripts` remains blocked by pre-existing repo-wide script
  manifest/ephemeral drift unrelated to the touched scripts; changed-file
  invocation reports the same global drift and no touched-script-specific
  blocker.

## Stop Conditions

Stop before any action that writes canonical DBs, runs rebuild/refit/eval in
write mode, promotes calibration outputs, performs migration/backfill/relabeling,
or claims live unlock.
