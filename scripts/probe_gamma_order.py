# Created: 2026-05-11
# Last reused or audited: 2026-05-11
# Authority basis: PLAN.md §6 F5, critic v4 ACCEPT 2026-05-11
"""One-shot probe: verify Gamma API honours order=endDate&ascending=false.

Usage:
    python -m scripts.probe_gamma_order

Fetches 3 pages (offset=0, 100, 1000) with order=endDate&ascending=false
and asserts each page's endDate values are in descending order relative to
the previous page.

Exit code 0 = PASS (descending confirmed).
Exit code 1 = FAIL (ordering violated — revert D.1 ordering params per F5).
"""
from __future__ import annotations

import sys
import httpx

from src.data.market_scanner import GAMMA_BASE

PROBE_OFFSETS = [0, 100, 1000]
LIMIT = 2


def _fetch_page(offset: int) -> list[str]:
    resp = httpx.get(
        f"{GAMMA_BASE}/events",
        params={
            "closed": "true",
            "limit": LIMIT,
            "offset": offset,
            "order": "endDate",
            "ascending": "false",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    batch = resp.json()
    return [ev.get("endDate", "") for ev in batch if ev.get("endDate")]


def main() -> int:
    print(f"probe_gamma_order: fetching offsets {PROBE_OFFSETS} with limit={LIMIT}")
    pages: dict[int, list[str]] = {}
    for offset in PROBE_OFFSETS:
        try:
            dates = _fetch_page(offset)
            pages[offset] = dates
            print(f"  offset={offset}: endDates={dates}")
        except httpx.HTTPError as exc:
            print(f"  offset={offset}: HTTP ERROR: {exc}")
            print("FAIL — could not fetch probe pages")
            return 1

    # Verify within each page the dates are non-increasing
    for offset, dates in pages.items():
        for i in range(len(dates) - 1):
            if dates[i] < dates[i + 1]:
                print(
                    f"FAIL — page offset={offset} is NOT descending: "
                    f"{dates[i]} < {dates[i+1]}"
                )
                return 1

    # Verify across pages: page-0 max >= page-100 max >= page-1000 max
    prev_offset = None
    for offset in PROBE_OFFSETS:
        if not pages.get(offset):
            continue
        page_max = max(pages[offset])
        if prev_offset is not None and pages.get(prev_offset):
            prev_max = max(pages[prev_offset])
            if page_max > prev_max:
                print(
                    f"FAIL — offset={offset} max endDate ({page_max}) > "
                    f"offset={prev_offset} max endDate ({prev_max}); "
                    "API is NOT in descending order. Revert D.1 ordering params (F5)."
                )
                return 1
        prev_offset = offset

    print("PASS — Gamma API returns closed events in descending endDate order.")
    print("order=endDate&ascending=false contract verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
