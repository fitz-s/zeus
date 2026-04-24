# Codex Prompt — Topology Packet Closeout

Use this after each packet.

Required steps:

1. Show changed files:
   ```bash
   git status --short
   git diff --stat
   ```

2. Run packet-specific tests from the packet blueprint.

3. Run general topology checks:
   ```bash
   python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py
   python scripts/topology_doctor.py closeout --changed-files <changed-files> --summary-only
   ```

4. If docs/manifests changed, run:
   ```bash
   python scripts/topology_doctor.py --docs --json
   python scripts/topology_doctor.py --map-maintenance --changed-files <changed-files> --json
   ```

5. If graph/context changed, run:
   ```bash
   python scripts/topology_doctor.py --code-review-graph-status --json
   ```

6. Write or update packet work log/receipt as required by current delivery rules.

7. Explicitly list:
   - direct blockers fixed,
   - global health warnings not in scope,
   - deferrals,
   - rollback path,
   - next packet recommendation.

Do not claim clean global health unless strict/global health was run and passed.
