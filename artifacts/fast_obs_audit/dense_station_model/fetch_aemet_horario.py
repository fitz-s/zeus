"""Madrid LEMD: AEMET public website XML 'horario' table (no key), station 3129 Madrid Aeropuerto.

The endpoint exposes only the last 24 hourly periods (no archive); the keyed OpenData API is not available here
(no key in the keychain; a keyless call to opendata.aemet.es returns HTTP 200 with an empty body). One request.
Output: raw/aemet_lemd_horario_fetched_<UTC>.xml.gz (merged by the runner with the earlier capture in
../daily_extreme_agreement/raw/).
"""
import gzip
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
URL = "https://www.aemet.es/es/api-eltiempo/udat/tablas-graficas/horario/9/3129"


def main():
    with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "zeus-obs-research (read-only audit)"}), timeout=30) as r:
        body = r.read()
    p = RAW / f"aemet_lemd_horario_fetched_{datetime.now(timezone.utc):%Y%m%dT%H%MZ}.xml.gz"
    p.write_bytes(gzip.compress(body))
    print(p.name, len(body))


if __name__ == "__main__":
    main()
