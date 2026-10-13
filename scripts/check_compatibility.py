#!/usr/bin/env python3
"""Compatibility report for TradingAgents-MOEX (spec §12, §14.5).

Verifies: pinned upstream commits, patch state, extension imports and a
quick functional slice. Exit codes: 0 supported, 2 untested upstream,
1 mismatch/broken.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.apply_patches import (  # noqa: E402
    head_commit, load_manifest, patch_state, pin_key,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    report: dict = {"pinned": {}, "patches": {}, "extension": {}}

    for name, pin in manifest["pinned"].items():
        report["pinned"][name] = {"expected": pin["commit"], "status": pin["status"]}

    from integration.version_check import check as version_check

    vc = version_check()
    report["upstream"] = vc

    for name, spec in manifest["projects"].items():
        root = REPO_ROOT / spec["dir"]
        if not (root / ".git").exists():
            report["patches"][name] = {"root": str(root), "error": "not a checkout"}
            continue
        head = head_commit(root)
        states = {}
        for p in spec["patches"]:
            states[p["file"]] = patch_state(root, REPO_ROOT / p["file"])
        report["patches"][name] = {
            "root": str(root),
            "head": head,
            "head_matches_pin": head == manifest["pinned"][pin_key(name)]["commit"],
            "patches": states,
        }

    try:
        import integration  # noqa: F401
        import moex  # noqa: F401
        import tinvest  # noqa: F401
        from moex.news.registry import SOURCE_REGISTRY
        from moex.risk import DEFAULT_RISK

        report["extension"] = {
            "importable": True,
            "news_sources": len(SOURCE_REGISTRY),
            "risk_keys": sorted(DEFAULT_RISK),
        }
    except Exception as exc:  # noqa: BLE001
        report["extension"] = {"importable": False, "error": str(exc)}

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("== TradingAgents-MOEX compatibility ==")
        for name, up in vc.items():
            if isinstance(up, dict):
                print(f"  upstream {name}: {up.get('status')} "
                      f"(match={up.get('match')}, dirty={up.get('dirty')})")
        for name, ps in report["patches"].items():
            if "error" in ps:
                print(f"  patches {name}: {ps['error']}")
            else:
                combined = ",".join(f"{Path(k).name}:{v}" for k, v in ps["patches"].items())
                print(f"  patches {name}: head={ps['head'][:12]} pin={ps['head_matches_pin']} [{combined}]")
        ext = report["extension"]
        print(f"  extension: importable={ext.get('importable')} "
              f"sources={ext.get('news_sources', 0)}")

    if not report["extension"].get("importable"):
        return 1
    if any(isinstance(v, dict) and v.get("match") is False for v in vc.values()):
        return 1
    if any(isinstance(v, dict) and v.get("status") == "untested" for v in vc.values()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
