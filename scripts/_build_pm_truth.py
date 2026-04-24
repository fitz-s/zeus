"""Build COMPLETE PM settlement truth from all closed Gamma API events.
Parse winning bin from market outcomePrices to get actual PM settlement."""
import requests, json, time, re, sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Fetch all closed events
base_url = "https://gamma-api.polymarket.com/events"
all_events = []
offset = 0
limit = 100
while True:
    params = {"tag_id": 103040, "limit": limit, "offset": offset, "closed": True}
    r = requests.get(base_url, params=params, timeout=30)
    r.raise_for_status()
    batch = r.json()
    if not batch:
        break
    all_events.append(batch)
    print(f"  Fetched offset={offset}, got {len(batch)} events", file=sys.stderr)
    if len(batch) < limit:
        break
    offset += limit
    time.sleep(0.3)

# Flatten
all_events = [ev for batch in all_events for ev in batch]
print(f"Total closed events: {len(all_events)}", file=sys.stderr)

# City name mapping
city_map = {
    "New York City": "NYC", "NYC": "NYC", "New York": "NYC",
    "Los Angeles": "Los Angeles", "San Francisco": "San Francisco",
    "Hong Kong": "Hong Kong", "Mexico City": "Mexico City",
    "Sao Paulo": "Sao Paulo", "São Paulo": "Sao Paulo",
    "Buenos Aires": "Buenos Aires", "Cape Town": "Cape Town",
    "Kuala Lumpur": "Kuala Lumpur", "Tel Aviv": "Tel Aviv",
    "Panama City": "Panama City",
}
for c in ["Amsterdam", "Ankara", "Atlanta", "Auckland", "Austin", "Beijing",
           "Busan", "Chengdu", "Chicago", "Chongqing", "Dallas", "Denver",
           "Guangzhou", "Helsinki", "Houston", "Istanbul", "Jakarta", "Jeddah",
           "Karachi", "Lagos", "London", "Lucknow", "Madrid", "Manila",
           "Miami", "Milan", "Moscow", "Munich", "Paris", "Seattle",
           "Seoul", "Shanghai", "Shenzhen", "Singapore", "Taipei", "Tokyo",
           "Toronto", "Warsaw", "Wellington", "Wuhan"]:
    city_map[c] = c

month_map = {"January": "01", "February": "02", "March": "03", "April": "04",
             "May": "05", "June": "06", "July": "07", "August": "08",
             "September": "09", "October": "10", "November": "11", "December": "12",
             "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "Dec": "12"}

def parse_event_title(title, event_end_date=None):
    m = re.search(r'(?:temperature|temp).*?in (.+?) on (.+?)(?:\?|$)', title, re.I)
    if not m:
        return None, None
    city_raw = m.group(1).strip()
    date_raw = m.group(2).strip().rstrip('?')
    city = city_map.get(city_raw)
    if not city:
        for k, v in city_map.items():
            if k.lower() in city_raw.lower() or city_raw.lower() in k.lower():
                city = v
                break
    date = None
    # Format with year: "30 Dec '25" or "December 30, 2025"
    m2 = re.search(r'(\d+)\s+(\w+)\s+[\'"]?(\d+)', date_raw)
    if m2:
        day = int(m2.group(1))
        month = month_map.get(m2.group(2))
        year = m2.group(3)
        if len(year) == 2:
            year = "20" + year
        if month:
            date = f"{year}-{month}-{day:02d}"
    if not date:
        m3 = re.search(r'(\w+)\s+(\d+),?\s*(\d{4})', date_raw)
        if m3:
            month = month_map.get(m3.group(1))
            day = int(m3.group(2))
            year = m3.group(3)
            if month:
                date = f"{year}-{month}-{day:02d}"
    # Format without year: "December 30" or "April 15"
    if not date:
        m4 = re.search(r'(\w+)\s+(\d+)', date_raw)
        if m4:
            month = month_map.get(m4.group(1))
            day = int(m4.group(2))
            if month:
                # Get year from event endDate
                year = None
                if event_end_date:
                    year = event_end_date[:4]
                if not year:
                    year = "2026"  # fallback
                date = f"{year}-{month}-{day:02d}"
    return city, date

def parse_market_bin(question):
    # Below / or lower
    m = re.search(r'(-?\d+)\s*°([CF])\s+or (?:below|lower)', question)
    if m:
        return (None, int(m.group(1)), m.group(2))
    # Above / or higher
    m = re.search(r'(-?\d+)\s*°([CF])\s+or (?:above|higher)', question)
    if m:
        return (int(m.group(1)), None, m.group(2))
    # Range
    m = re.search(r'between\s+(-?\d+)\s*[-–]\s*(-?\d+)\s*°([CF])', question)
    if m:
        return (int(m.group(1)), int(m.group(2)), m.group(3))
    # Exact (single degree)
    m = re.search(r'be\s+(-?\d+)\s*°([CF])\s+(?:on|in)', question)
    if m:
        return (int(m.group(1)), int(m.group(1)), m.group(2))
    return None

# Process all events
settlements = []
errors = []
for ev in all_events:
    city, date = parse_event_title(ev['title'], ev.get('endDate', ''))
    if not city or not date:
        errors.append(f"Can't parse: {ev['title']}")
        continue

    res_source = ev.get('resolutionSource', '')

    # Find winning market
    winning_bin = None
    unit = None
    for mkt in ev.get('markets', []):
        prices_raw = mkt.get('outcomePrices', '[]')
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        if len(prices) >= 2 and prices[0] == "1":
            parsed = parse_market_bin(mkt.get('question', ''))
            if parsed:
                lo, hi, u = parsed
                winning_bin = (lo, hi)
                unit = u
                break

    if winning_bin is None:
        errors.append(f"No winning market for {city} {date}: {ev['title']}")
        continue

    lo, hi = winning_bin
    if lo == hi and lo is not None:
        sv = lo
    else:
        sv = None

    settlements.append({
        "city": city,
        "date": date,
        "pm_bin_lo": lo,
        "pm_bin_hi": hi,
        "pm_exact_value": sv,
        "unit": unit,
        "resolution_source": res_source,
    })

# Summary
exact = sum(1 for s in settlements if s['pm_exact_value'] is not None)
ranged = sum(1 for s in settlements if s['pm_exact_value'] is None)
print(f"Parsed: {len(settlements)} settlements, {len(errors)} errors")
print(f"Exact value known: {exact}")
print(f"Range only: {ranged}")
if errors:
    print(f"\nErrors ({len(errors)}):")
    for e in errors[:20]:
        print(f"  {e}")

city_counts = defaultdict(int)
for s in settlements:
    city_counts[s['city']] += 1
print(f"\nPer-city:")
for c in sorted(city_counts):
    print(f"  {c}: {city_counts[c]}")

out = PROJECT_ROOT / 'data' / 'pm_settlements_full.json'
with open(out, 'w') as f:
    json.dump(settlements, f, indent=2)
print(f"\nSaved to {out}")
