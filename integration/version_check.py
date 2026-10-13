"""Compatibility check against the pinned upstream revisions (spec §12, §14.5)."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Pinned SHAs — must match patches/manifest.yaml «supported_upstream».
PINNED = {
    "tradingagents": "1394a3f72aa4393e1a98f51b382434c4b4c2d972",
    "TradingAgents-GUI": "774a0fa2aa3ee576d993ab2389e661a44ac9c37b",
}

_ROOT = Path(__file__).resolve().parent.parent


def _head_sha(repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _dirty(repo: Path) -> bool:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, timeout=15,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def check() -> dict:
    """{repo: {expected, actual, match, dirty, status}} with status in
    supported | untested | unsupported."""
    result: dict = {"all_supported": True}
    for name, expected in PINNED.items():
        repo = _ROOT / "upstream" / name
        if not repo.exists():
            result[name] = {"status": "missing", "match": False,
                            "all_supported": False}
            result["all_supported"] = False
            continue
        actual = _head_sha(repo)
        match = actual == expected
        entry = {
            "expected": expected,
            "actual": actual,
            "match": match,
            "dirty": _dirty(repo),
            "status": "supported" if match else "untested",
        }
        if not match:
            result["all_supported"] = False
        result[name] = entry
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(check(), indent=2))
