#!/bin/bash
# Usage: physical_round_metrics.sh "<start CDT YYYY-MM-DD HH:MM>" "<end CDT>"
# Physical round durations (Running -> executed), reseed batches, write deferrals.
s="$1"; e="$2"; cd /Users/leofitz/zeus
awk -v s="$s" -v e="$e" 'substr($0,1,16)>=s && substr($0,1,16)<e' logs/zeus-ingest.log logs/zeus-ingest.err > /tmp/prm.$$
/Users/leofitz/zeus/.venv/bin/python - /tmp/prm.$$ <<'PY'
import sys, re, json, datetime as dt
def ts(l): return dt.datetime.strptime(l[:23], "%Y-%m-%d %H:%M:%S,%f")
start=None; durs=[]; batches=[]; deferred=0; skipped=0; failed=0
for l in open(sys.argv[1], errors="replace"):
    if '_day0_fmi_temperature_tick' in l:
        if 'Running job' in l: start=ts(l)
        elif 'executed successfully' in l and start: durs.append((ts(l)-start).total_seconds()); start=None
        elif 'skipped: maximum' in l: skipped+=1
    if 'PHYSICAL_CURRENT_RESEED_BATCH_TRACE' in l:
        batches.append(json.loads(l.split('BATCH_TRACE ',1)[1]))
    if 'PHYSICAL_CURRENT_WRITE_DEFERRED' in l: deferred+=1
    if 'PHYSICAL_CURRENT_RESEED_FAILED' in l: failed+=1
def pct(v,p):
    v=sorted(v); return v[min(len(v)-1,int(p*len(v)))] if v else None
print(f"rounds={len(durs)} p50={pct(durs,.5)} p90={pct(durs,.9)} p99={pct(durs,.99)} max={max(durs) if durs else None} skipped={skipped}")
er=[b['enqueue_return_ms']/1000 for b in batches]; rt=[b['routes'] for b in batches]
print(f"batches={len(batches)} routes={sum(rt)} enqueue_s p50={pct(er,.5)} p90={pct(er,.9)} max={max(er) if er else None} statuses={sorted(set(b['status'] for b in batches))}")
print(f"write_deferred={deferred} reseed_failed={failed}")
PY
rm -f /tmp/prm.$$
