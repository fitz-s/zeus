"""YAML loader for RealityContract definitions.

Reads config/reality_contracts/*.yaml and instantiates RealityContract objects.
Mutable last_verified timestamps are stored in state/reality_contract_state.json
and override the static YAML values (§P10 adversarial finding #2).

YAML schema per contract entry:
  contract_id: str
  assumption: str
  current_value: any
  verification_method: str
  last_verified: str  # ISO-8601 UTC
  ttl_seconds: int
  criticality: "blocking" | "degraded" | "advisory"
  on_change_handlers: list[str]  # optional, default []

The category is inferred from the filename stem
(economic / execution / data / protocol).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.contracts.reality_contract import RealityContract

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {"economic", "execution", "data", "protocol"}
_VALID_CRITICALITIES = {"blocking", "degraded", "advisory"}

_DEFAULT_CONTRACTS_DIR = (
    Path(__file__).parent.parent.parent / "config" / "reality_contracts"
)

_DEFAULT_STATE_FILE = (
    Path(__file__).parent.parent.parent / "state" / "reality_contract_state.json"
)


def _parse_datetime(value: str | datetime) -> datetime:
    """Parse ISO-8601 string or passthrough datetime, always UTC-aware."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_mutable_state(state_file: Path) -> dict[str, str]:
    """Load mutable last_verified timestamps from state file.

    Returns dict mapping contract_id -> ISO-8601 datetime string.
    Returns empty dict if file missing or malformed.
    """
    if not state_file.exists():
        return {}
    try:
        with state_file.open() as f:
            data = json.load(f)
        return data.get("last_verified", {})
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Malformed reality_contract_state.json, ignoring mutable state")
        return {}


def record_verification(contract_id: str, state_file: Path | str | None = None) -> None:
    """Record a successful live verification by updating the mutable state file.

    Called by the verifier after a successful external check. Updates
    last_verified for the given contract_id to now(UTC).

    §P10 adversarial finding #2: last_verified must be mutable, not static YAML.
    """
    if state_file is None:
        state_file = _DEFAULT_STATE_FILE
    state_file = Path(state_file)

    # Read existing state
    if state_file.exists():
        try:
            with state_file.open() as f:
                data = json.load(f)
        except (json.JSONDecodeError, TypeError):
            data = {}
    else:
        data = {}

    if "last_verified" not in data:
        data["last_verified"] = {}

    data["last_verified"][contract_id] = datetime.now(timezone.utc).isoformat()

    # Atomic write
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(state_file)

    logger.info("Recorded verification for contract '%s'", contract_id)


def load_contracts(
    contracts_dir: Path | str | None = None,
) -> list[RealityContract]:
    """Load all YAML files from contracts_dir and return RealityContract instances.

    Args:
        contracts_dir: Path to directory containing *.yaml files.
                       Defaults to config/reality_contracts/ relative to repo root.

    Returns:
        List of RealityContract instances, one per YAML entry across all files.

    Raises:
        ValueError: If a YAML file has an unrecognised stem (not a valid category)
                    or a required field is missing.
    """
    if contracts_dir is None:
        contracts_dir = _DEFAULT_CONTRACTS_DIR
    contracts_dir = Path(contracts_dir)

    # Load mutable last_verified overrides (§P10 adversarial finding #2)
    mutable_state = _load_mutable_state(_DEFAULT_STATE_FILE)

    contracts: list[RealityContract] = []

    for yaml_path in sorted(contracts_dir.glob("*.yaml")):
        category = yaml_path.stem
        if category not in _VALID_CATEGORIES:
            raise ValueError(
                f"Unrecognised contract category '{category}' from file {yaml_path}. "
                f"Expected one of {_VALID_CATEGORIES}."
            )

        with yaml_path.open() as f:
            entries: list[dict[str, Any]] = yaml.safe_load(f) or []

        if not isinstance(entries, list):
            raise ValueError(
                f"{yaml_path}: expected a YAML list of contract entries, got {type(entries)}."
            )

        for entry in entries:
            _validate_entry(entry, yaml_path)
            cid = entry["contract_id"]
            # Use mutable state timestamp if available, else fall back to YAML
            if cid in mutable_state:
                last_verified = _parse_datetime(mutable_state[cid])
            else:
                last_verified = _parse_datetime(entry["last_verified"])
            contracts.append(
                RealityContract(
                    contract_id=cid,
                    category=category,
                    assumption=entry["assumption"],
                    current_value=entry["current_value"],
                    verification_method=entry["verification_method"],
                    last_verified=last_verified,
                    ttl_seconds=int(entry["ttl_seconds"]),
                    criticality=entry["criticality"],
                    on_change_handlers=entry.get("on_change_handlers", []),
                )
            )
            logger.debug("Loaded contract %s (%s)", cid, category)

    logger.info("Loaded %d reality contracts from %s", len(contracts), contracts_dir)
    return contracts


def _validate_entry(entry: dict[str, Any], source: Path) -> None:
    required = [
        "contract_id", "assumption", "current_value",
        "verification_method", "last_verified", "ttl_seconds", "criticality",
    ]
    missing = [k for k in required if k not in entry]
    if missing:
        raise ValueError(
            f"{source}: contract entry missing required fields: {missing}. Entry: {entry}"
        )
    crit = entry["criticality"]
    if crit not in _VALID_CRITICALITIES:
        raise ValueError(
            f"{source}: invalid criticality '{crit}' in contract '{entry['contract_id']}'. "
            f"Expected one of {_VALID_CRITICALITIES}."
        )
