"""Offline comparison of retained NEA Changi S24 and WRH WSSS response bodies.
The two anonymous read-only HTTP acquisitions are documented in transport.json
and comparison.json. No nearest-time matching or instrument certificate is used.
"""
import gzip
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[3]
while not (ROOT/'src').is_dir(): ROOT=ROOT.parent
sys.path.insert(0,str(ROOT))
from src.data.noaa_wrh_timeseries import rows_from_payload
HERE=Path(__file__).parent
nea_bytes=gzip.decompress((HERE/'nea_temperature.json.gz').read_bytes())
wrh_bytes=gzip.decompress((HERE/'wrh_wsss.json.gz').read_bytes())
nea=json.loads(nea_bytes)
assert nea['metadata']['reading_unit']=='deg C'
station=next(s for s in nea['metadata']['stations'] if s['id']=='S24')
resolver={row.utc:row for row in rows_from_payload(json.loads(wrh_bytes),'WSSS')}
pairs=[]
for item in nea['items']:
    stamp=datetime.fromisoformat(item['timestamp']).astimezone(timezone.utc)
    if stamp not in resolver: continue
    for reading in item['readings']:
        if reading['station_id']!='S24':continue
        a=float(reading['value']);b=resolver[stamp].air_temp
        pairs.append({'time':stamp.isoformat(),'candidate_c':a,'resolver_c':b,
                      'candidate_contract':math.floor(a+.5),'resolver_contract':math.floor(b+.5),
                      'match':math.floor(a+.5)==math.floor(b+.5)})
prior=json.loads((HERE/'comparison.json').read_text())
assert pairs==prior['pairs']
assert hashlib.sha256(wrh_bytes).hexdigest()==prior['resolver_body_sha256']
assert sum(p['match'] for p in pairs)==prior['n_exact']
print(len(pairs),'exact-time pairs;',prior['n_exact'],'matches;',len(prior['mismatches']),'mismatches; no promotion')
