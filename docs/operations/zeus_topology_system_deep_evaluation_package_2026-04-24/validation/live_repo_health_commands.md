# Live Repo Health Commands

These commands are intentionally live. They may fail because active repo state has real drift. Do not confuse live drift with deterministic topology regression.

```bash
python scripts/topology_doctor.py --strict --json
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --source --json
python scripts/topology_doctor.py --tests --json
python scripts/topology_doctor.py --scripts --json
python scripts/topology_doctor.py --history-lore --json
python scripts/topology_doctor.py --reference-replacement --json
python scripts/topology_doctor.py --code-review-graph-status --json
python scripts/topology_doctor.py --context-packs --json
python scripts/topology_doctor.py compiled-topology --json
```

## Reporting rule

For each failure, classify:

- scoped blocker,
- global drift,
- stale fixture assumption,
- missing manifest registration,
- obsolete rule,
- current-state assumption,
- graph advisory,
- repair draft candidate.

## Do not

Do not edit manifests simply to make a live command pass unless the packet scope owns that manifest repair.
