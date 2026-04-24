# Topology Doctor Regression Commands

## Compile

```bash
python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
```

## Deterministic tests

```bash
pytest -q tests/test_topology_doctor.py -m "not live_topology"
```

Before P5 marker split exists, use targeted selections:

```bash
pytest -q tests/test_topology_doctor.py -k "navigation or closeout or graph or context_pack"
```

## Lane commands

```bash
python scripts/topology_doctor.py --navigation --task "topology system reform" --files scripts/topology_doctor.py --json
python scripts/topology_doctor.py closeout --changed-files scripts/topology_doctor.py tests/test_topology_doctor.py --summary-only
python scripts/topology_doctor.py --docs --json
python scripts/topology_doctor.py --source --json
python scripts/topology_doctor.py --tests --json
python scripts/topology_doctor.py --scripts --json
python scripts/topology_doctor.py --code-review-graph-status --json
python scripts/topology_doctor.py context-pack --profile package_review --files scripts/topology_doctor.py --json
```

## Output expectations after P0/P1

- Navigation JSON has direct blockers and repo health warnings.
- Closeout JSON has scoped blockers and unrelated warning counts.
- Issues retain code/path/message/severity.
- Typed fields appear where implemented.
