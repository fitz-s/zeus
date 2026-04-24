# raw AGENTS

Raw external evidence captured by Zeus operators or runtime support scripts.
These files are input evidence, not canonical strategy authority.

## File Registry

| Path | Purpose |
|------|---------|
| `oracle_shadow_snapshots/` | Per-city oracle API snapshots captured near settlement windows for oracle-error-rate calibration evidence |

## Rules

- Raw snapshots are evidence until compared against Polymarket settlement truth
  and promoted through a declared bridge/config path.
- Do not treat raw JSON as canonical DB truth.
- Keep generated OS/cache files out of Git.
