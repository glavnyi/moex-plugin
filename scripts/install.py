#!/usr/bin/env python3
"""Install TradingAgents-MOEX: verify upstreams, back up, apply patches,
install the extension package, run checks (spec §12).

Usage:
  python scripts/install.py [--tradingagents-root DIR] [--gui-root DIR]
                            [--venv PATH] [--skip-package] [--force]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.apply_patches import (  # noqa: E402
    apply_all, head_commit, load_manifest,
)


def _check_environment() -> list[str]:
    problems: list[str] = []
    try:
        import flask  # noqa: F401
    except ImportError:
        problems.append("flask is missing in this Python environment")
    try:
        import moex, tinvest, integration  # noqa: F401
    except ImportError as exc:
        problems.append(f"extension package not importable: {exc}")
    return problems


def _install_package(python: str) -> bool:
    proc = subprocess.run(
        [python, "-m", "pip", "install", "-e", str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:], file=sys.stderr)
        return False
    return True


def _run_checks() -> bool:
    checks = [
        [ "-c",
          "import moex, tinvest, integration; "
          "from integration.version_check import check; "
          "import json; r = check(); "
          "print('upstream:', r['tradingagents']['status'], r['TradingAgents-GUI']['status']); "
          "assert r['tradingagents']['match'] and r['TradingAgents-GUI']['match']" ],
        [ "-c",
          "from integration import register_moex_vendors, enrich_run_config; "
          "from integration.gui_adapter import moex_vendor_config; "
          "cfg = enrich_run_config({'market': 'MOEX', 'ticker': 'SBER'}, {}); "
          "assert cfg['market'] == 'MOEX' and len(cfg['tool_vendors']) == 9; "
          "print('moex config fragment: ok')" ],
        [ "-c",
          "from moex.risk import check_proposal; from moex.news.registry import SOURCE_REGISTRY; "
          "print('risk + news registry: ok,', len(SOURCE_REGISTRY), 'sources')" ],
    ]
    ok = True
    for argv in checks:
        proc = subprocess.run([sys.executable, *argv], capture_output=True, text=True)
        out = (proc.stdout or "").strip().splitlines()
        print("  ✓", out[-1] if out else argv[-1][:60])
        if proc.returncode != 0:
            ok = False
            print("  ✗", (proc.stderr or "").strip().splitlines()[-1], file=sys.stderr)
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tradingagents-root", help="path to a tradingagents checkout")
    ap.add_argument("--gui-root", help="path to a TradingAgents-GUI checkout")
    ap.add_argument("--venv", help="Python interpreter of the environment that runs the GUI")
    ap.add_argument("--skip-package", action="store_true",
                    help="do not pip-install the extension package")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    print("== TradingAgents-MOEX installer ==")
    for name, pin in manifest["pinned"].items():
        print(f"  pinned {name}: {pin['commit'][:12]} ({pin['status']})")

    overrides = {}
    if args.tradingagents_root:
        overrides["tradingagents"] = args.tradingagents_root
    if args.gui_root:
        overrides["TradingAgents-GUI"] = args.gui_root

    print("\n[1/4] applying patches (backups under .backups/)")
    results = apply_all(None, REPO_ROOT, overrides, force=args.force)
    problems = {k: v for k, v in results.items()
                if v not in ("applied", "already-applied")}
    if problems:
        for k, v in problems.items():
            print(f"  ✗ {k}: {v}", file=sys.stderr)
        if not args.force:
            print("aborted: fix the conflicts or rerun with --force", file=sys.stderr)
            return 1

    print("\n[2/4] installing extension package")
    python = args.venv or sys.executable
    if args.skip_package:
        print("  skipped (--skip-package)")
    elif _install_package(python):
        print(f"  ✓ pip install -e {REPO_ROOT} -> {python}")
    else:
        print("  ✗ pip install failed", file=sys.stderr)
        return 1

    print("\n[3/4] environment checks")
    problems = _check_environment()
    if problems:
        for p in problems:
            print("  ✗", p, file=sys.stderr)
        return 1
    print("  ✓ flask + moex/tinvest/integration importable")

    print("\n[4/4] functional checks")
    if not _run_checks():
        return 1

    gui_dir = Path(overrides.get("TradingAgents-GUI")
                   or REPO_ROOT / manifest["projects"]["tradingagents-gui"]["dir"])
    print("\n== installed ==")
    print("  run the GUI:  cd", gui_dir, "&& python gui/app.py")
    print("  then open the MOEX and Песочница tabs in the browser.")
    print("  T-Invest sandbox: export TINVEST_TOKEN=... before starting the GUI.")
    print("  rollback:     python scripts/uninstall.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
