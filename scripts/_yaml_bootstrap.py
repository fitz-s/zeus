from __future__ import annotations

from pathlib import Path
import sys


def import_yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parents[1]
        candidates = sorted((root / '.venv' / 'lib').glob('python*/site-packages'))
        for candidate in candidates:
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.append(candidate_str)
            try:
                import yaml  # type: ignore
                return yaml
            except ModuleNotFoundError:
                continue
        raise
