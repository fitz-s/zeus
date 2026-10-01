"""Bounded free-public reobservation across a UTC rollover; no production DB I/O.
Reuses reviewed collectors; every sample body and request/receipt is retained.
The local output directory must not already contain an earlier window.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import time
sys.path.insert(0, str(Path(__file__).parent))
import round4_collect as c
from round4_global_race import mgm_group
from round4_final_native import india, russia
from round4_extra_native import madrid, italy, hongkong
from bs4 import BeautifulSoup


def knmi_metar():
    r, meta = c.fetch('knmi_public_metar', 'https://www.knmi.nl/nederland-nu/luchtvaart/vliegveldwaarnemingen')
    text = BeautifulSoup(r.text, 'html.parser').get_text(' ', strip=True)
    rows = []
    for match in re.finditer(r'(?:METAR|SPECI)\s+EHAM\s+\d{6}Z[^=]+=', text):
        sample = c.raw_metar(match[0], 'EHAM', c.instant(meta['receipt_at']))
        if sample: rows.append(sample)
    c.record('knmi_public_metar', 'EHAM', rows, meta)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--rounds', type=int, default=15)
    a = p.parse_args()
    if not 1 <= a.rounds <= 20: raise ValueError('bounded rounds must be 1..20')
    c.OUT = c.BASE / 'round4_resume'
    c.OUT.mkdir(exist_ok=True)
    if (c.OUT/'samples.json').exists(): raise FileExistsError('preserve prior experiment')
    ids = tuple(sorted(c.BYID))
    groups = [ids[i:i+10] for i in range(0,len(ids),10)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for n in range(a.rounds):
            started = time.monotonic()
            funcs = [lambda group=group: mgm_group(group) for group in groups]
            funcs += [india, russia, c.japan, c.canada, c.korea, knmi_metar,
                      lambda:italy('LIMC'), lambda:italy('SBGR'), lambda:italy('MMMX'),
                      hongkong, c.awc, lambda:c.resolver('C'), lambda:c.resolver('F')]
            if n % 5 == 0: funcs += [c.wu, madrid]
            shift = n % len(funcs)
            list(pool.map(c.guarded, funcs[shift:]+funcs[:shift]))
            c.save()
            print('RESUME',n,'samples',len(c.ROWS),'bounds',len(c.BRACKETS),datetime.now(timezone.utc).isoformat(),flush=True)
            if n < a.rounds-1: time.sleep(max(0,60-(time.monotonic()-started)))
    c.CLIENT.close()


if __name__ == '__main__': main()
