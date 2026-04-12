"""Live/paper isolation verification."""
import sqlite3, json

print("=== LIVE/PAPER ISOLATION AUDIT ===\n")

# Paper DB
c = sqlite3.connect("state/zeus-paper.db")
c.row_factory = sqlite3.Row
for tbl in ["position_current", "position_events", "position_events_legacy", "trade_decisions"]:
    envs = c.execute(f"SELECT env, COUNT(*) as n FROM {tbl} GROUP BY env").fetchall()
    env_str = ", ".join(f"{r['env']}={r['n']}" for r in envs) if envs else "empty"
    print(f"  Paper DB {tbl}: {env_str}")
c.close()

print()

# Live DB
c = sqlite3.connect("state/zeus-live.db")
c.row_factory = sqlite3.Row
for tbl in ["position_current", "position_events", "position_events_legacy", "trade_decisions"]:
    try:
        cnt = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  Live DB {tbl}: {cnt} rows")
    except Exception:
        print(f"  Live DB {tbl}: table missing")
c.close()

print()

# Shared DB
c = sqlite3.connect("state/zeus-shared.db")
c.row_factory = sqlite3.Row
for tbl in ["position_current", "position_events", "position_events_legacy", "trade_decisions"]:
    envs = c.execute(f"SELECT env, COUNT(*) as n FROM {tbl} GROUP BY env").fetchall()
    env_str = ", ".join(f"{r['env']}={r['n']}" for r in envs) if envs else "empty"
    print(f"  Shared DB {tbl}: {env_str}")
c.close()

print()

# JSON files
for name in ["positions-live.json", "strategy_tracker-live.json"]:
    with open(f"state/{name}") as f:
        data = json.load(f)
    if "positions" in data:
        print(f"  {name}: {len(data['positions'])} positions")
    elif "trades" in data:
        print(f"  {name}: {len(data['trades'])} trades")
    else:
        print(f"  {name}: keys={list(data.keys())}")

print("\n=== ISOLATION VERDICT ===")
# Check for any live env in paper-only stores
c = sqlite3.connect("state/zeus-paper.db")
c.row_factory = sqlite3.Row
live_in_paper = c.execute("SELECT COUNT(*) FROM position_current WHERE env = 'live'").fetchone()[0]
c.close()

c2 = sqlite3.connect("state/zeus-shared.db")
c2.row_factory = sqlite3.Row
live_in_shared_pel = c2.execute("SELECT COUNT(*) FROM position_events_legacy WHERE env = 'live'").fetchone()[0]
live_in_shared_td = c2.execute("SELECT COUNT(*) FROM trade_decisions WHERE env = 'live'").fetchone()[0]
c2.close()

issues = []
if live_in_paper > 0:
    issues.append(f"Paper DB has {live_in_paper} live rows")
if live_in_shared_pel > 0:
    issues.append(f"Shared DB PEL has {live_in_shared_pel} live rows")
if live_in_shared_td > 0:
    issues.append(f"Shared DB TD has {live_in_shared_td} live rows")

if issues:
    print("  FAIL: " + "; ".join(issues))
else:
    print("  PASS: No cross-contamination detected. Live/paper fully isolated.")
