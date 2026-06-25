"""Cross-process market-absence evidence for pending weather families.

The substrate observer owns Gamma/listing probes, while the order daemon owns
reactor horizon decisions. An in-process backoff map cannot cross that boundary,
so Gamma-empty evidence is mirrored to a small state file with an explicit expiry.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from src.config import state_path


ABSENCE_EVIDENCE_FILE = "market_absence_evidence.json"


def family_key(city: object, target_date: object, metric: object) -> tuple[str, str, str]:
    return (_family_text_key(city), str(target_date or "").strip(), _metric_key(metric))


def record_gamma_empty_families(
    families: Iterable[tuple[object, object, object]],
    *,
    ttl_seconds: float,
    observed_at: datetime | None = None,
    path: Path | None = None,
) -> None:
    ttl = max(0.0, float(ttl_seconds))
    if ttl <= 0.0:
        return
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)
    clean = [family_key(city, target_date, metric) for city, target_date, metric in families]
    clean = [key for key in clean if key[0] and key[1] and key[2]]
    if not clean:
        return
    target = path or state_path(ABSENCE_EVIDENCE_FILE)
    try:
        payload = _read_payload(target, now=now)
        evidence = payload.setdefault("families", {})
        for city_key, target_date, metric_key in clean:
            key = _serialized_key((city_key, target_date, metric_key))
            evidence[key] = {
                "city_key": city_key,
                "target_date": target_date,
                "metric": metric_key,
                "source": "gamma_empty",
                "observed_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
        payload["updated_at"] = now.isoformat()
        payload["schema_version"] = 1
        _atomic_write_json(target, payload)
    except Exception:
        return


def has_recent_gamma_empty_evidence(
    *,
    city: object,
    target_date: object,
    metric: object,
    now: datetime | None = None,
    path: Path | None = None,
) -> bool:
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    key = _serialized_key(family_key(city, target_date, metric))
    if not all(key.split("|")):
        return False
    target = path or state_path(ABSENCE_EVIDENCE_FILE)
    try:
        payload = _read_payload(target, now=checked_at)
        row = (payload.get("families") or {}).get(key)
        if not isinstance(row, dict):
            return False
        if str(row.get("source") or "") != "gamma_empty":
            return False
        expires_at = _parse_utc(row.get("expires_at"))
        return expires_at is not None and expires_at > checked_at
    except Exception:
        return False


def _read_payload(path: Path, *, now: datetime) -> dict:
    if not path.exists():
        return {"schema_version": 1, "updated_at": now.isoformat(), "families": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": 1, "updated_at": now.isoformat(), "families": {}}
    if not isinstance(raw, dict):
        raw = {}
    families = raw.get("families")
    if not isinstance(families, dict):
        families = {}
    kept = {}
    for key, row in families.items():
        if not isinstance(row, dict):
            continue
        expires_at = _parse_utc(row.get("expires_at"))
        if expires_at is not None and expires_at > now:
            kept[str(key)] = row
    raw["families"] = kept
    raw.setdefault("schema_version", 1)
    raw.setdefault("updated_at", now.isoformat())
    return raw


def _family_text_key(value: object) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.replace("-", " ").replace("_", " ").split())


def _metric_key(value: object) -> str:
    text = _family_text_key(value)
    if text in {"low", "lowest", "min", "minimum"} or text.startswith("lowest "):
        return "low"
    if text in {"high", "highest", "max", "maximum"} or text.startswith("highest "):
        return "high"
    return text


def _serialized_key(key: tuple[str, str, str]) -> str:
    return "|".join(key)


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
