#!/usr/bin/env python3
"""Roll back TradingAgents-MOEX patches (spec §12).

Restores the upstream sources to their pre-install state without touching
user data: ~/.tradingagents/ settings, reports, journals, caches and
backups are never removed unless --purge-data is given explicitly.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.apply_patches import BACKUP_ROOT, load_manifest  # noqa: E402


def reverse_patch(root: Path, patch: Path) -> str:
    fwd = subprocess.run(
        ["git", "-C", str(root), "apply", "--reverse", "--check", str(patch)],
        capture_output=True, text=True,
    )
    if fwd.returncode == 0:
        proc = subprocess.run(
            ["git", "-C", str(root), "apply", "--reverse", str(patch)],
            capture_output=True, text=True,
        )
        return "reverted" if proc.returncode == 0 else f"failed: {proc.stderr.strip()[:200]}"
    already = subprocess.run(
        ["git", "-C", str(root), "apply", "--check", str(patch)],
        capture_output=True, text=True,
    )
    if already.returncode == 0:
        return "not-applied"
    return "conflict"


def restore_from_backups(root: Path, manifest: dict, project: str) -> list[str]:
    """Copy the newest backup of each patched file back into the tree."""
    restored: list[str] = []
    if not BACKUP_ROOT.exists():
        return restored
    stamps = sorted((d for d in BACKUP_ROOT.iterdir() if (d / project).exists()),
                    reverse=True)
    for stamp in stamps:
        proj_dir = stamp / project
        for p in manifest["projects"][project]["patches"]:
            for rel in p.get("files", []):
                src = proj_dir / rel
                dst = root / rel
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
                    restored.append(f"{rel}@{stamp.name}")
        if restored:
            break
    return restored


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tradingagents-root")
    ap.add_argument("--gui-root")
    ap.add_argument("--restore-backups", action="store_true",
                    help="restore files from .backups/ when reverse patching fails")
    ap.add_argument("--uninstall-package", action="store_true",
                    help="also pip uninstall the tradingagents-moex package")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    roots = {
        "tradingagents": Path(
            args.tradingagents_root
            or REPO_ROOT / manifest["projects"]["tradingagents"]["dir"]
        ).resolve(),
        "tradingagents-gui": Path(
            args.gui_root
            or REPO_ROOT / manifest["projects"]["tradingagents-gui"]["dir"]
        ).resolve(),
    }

    print("== TradingAgents-MOEX uninstaller ==")
    failed = False
    for name, spec in manifest["projects"].items():
        root = roots[name]
        if not (root / ".git").exists():
            print(f"[skip] {name}: {root} is not a git checkout")
            continue
        for p in spec["patches"]:
            outcome = reverse_patch(root, REPO_ROOT / p["file"])
            print(f"  {name}: {p['file']} → {outcome}")
            if outcome == "conflict" and args.restore_backups:
                restored = restore_from_backups(root, manifest, name)
                print("  restored:", restored or "no backups found")
                outcome = reverse_patch(root, REPO_ROOT / p["file"])
                print(f"  retry {p['file']} → {outcome}")
            if outcome == "conflict":
                failed = True

    if args.uninstall_package:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", "tradingagents-moex"],
            capture_output=True, text=True,
        )
        print("  pip uninstall:", "ok" if proc.returncode == 0 else proc.stderr.strip()[:200])

    print("\nUser data in ~/.tradingagents/ (settings, reports, journals) was kept.")
    print("To remove it as well, delete that directory manually.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
