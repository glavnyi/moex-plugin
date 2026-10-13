#!/usr/bin/env python3
"""Apply the TradingAgents-MOEX patch sets to upstream working copies.

Idempotent: a patch that is already applied is skipped; backups of every
modified file are written before the first application (spec §12).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "patches" / "manifest.yaml"
BACKUP_ROOT = REPO_ROOT / ".backups"


def load_manifest(path: Path = MANIFEST) -> dict:
    if yaml is None:
        raise SystemExit("pyyaml is required: pip install pyyaml")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def pin_key(project_name: str) -> str:
    """Map manifest ``projects`` keys to the matching ``pinned`` keys."""
    return "TradingAgents-GUI" if project_name == "tradingagents-gui" else project_name


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=check,
    )


def head_commit(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def patch_state(root: Path, patch: Path) -> str:
    """Return 'applied' | 'clean' | 'conflict' for one patch file."""
    rev = subprocess.run(
        ["git", "-C", str(root), "apply", "--reverse", "--check", str(patch)],
        capture_output=True, text=True,
    )
    if rev.returncode == 0:
        return "applied"
    fwd = subprocess.run(
        ["git", "-C", str(root), "apply", "--check", str(patch)],
        capture_output=True, text=True,
    )
    if fwd.returncode == 0:
        return "clean"
    return "conflict"


def backup_files(root: Path, files: list[str], backup_dir: Path) -> list[Path]:
    saved: list[Path] = []
    for rel in files:
        src = root / rel
        if not src.exists():
            continue
        dst = backup_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        saved.append(dst)
    return saved


def apply_patch(root: Path, patch: Path, files: list[str],
                backup_dir: Path | None = None, force: bool = False) -> str:
    state = patch_state(root, patch)
    if state == "applied":
        return "already-applied"
    if state == "conflict" and not force:
        return "conflict"
    if backup_dir is not None:
        backup_files(root, files, backup_dir)
    # Plain apply first (keeps the index clean), then 3-way, then patch(1).
    proc = subprocess.run(
        ["git", "-C", str(root), "apply", str(patch)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            ["git", "-C", str(root), "apply", "--3way", str(patch)],
            capture_output=True, text=True,
        )
    if proc.returncode != 0:
        proc = subprocess.run(
            ["patch", "-p1", "-N", "-d", str(root), "-i", str(patch)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return f"failed: {(proc.stderr or proc.stdout).strip()[:200]}"
    return "applied" if state == "clean" else "applied"


def apply_all(projects: dict, repo_root: Path = REPO_ROOT,
              root_overrides: dict | None = None, force: bool = False,
              quiet: bool = False) -> dict[str, str]:
    manifest = load_manifest()
    overrides = root_overrides or {}
    results: dict[str, str] = {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for name, spec in manifest["projects"].items():
        root = Path(overrides.get(name) or (repo_root / spec["dir"])).resolve()
        if not (root / ".git").exists():
            results[name] = f"missing: {root}"
            if not quiet:
                print(f"[skip] {name}: no git checkout at {root}")
            continue
        pinned = manifest["pinned"][pin_key(name)]["commit"]
        head = head_commit(root)
        if head != pinned and not force:
            results[name] = f"sha-mismatch: {head[:12]} != {pinned[:12]} (use --force)"
            if not quiet:
                print(f"[warn] {name}: HEAD {head[:12]} != pinned {pinned[:12]} — "
                      "patches may not apply (use --force to try anyway)")
        backup_dir = BACKUP_ROOT / stamp / name
        for p in spec["patches"]:
            patch_path = repo_root / p["file"] if not Path(p["file"]).is_absolute() else Path(p["file"])
            if not patch_path.exists():
                results[p["file"]] = "missing-patch"
                continue
            outcome = apply_patch(root, patch_path, p.get("files", []),
                                  backup_dir=backup_dir, force=force)
            results[p["file"]] = outcome
            if not quiet:
                print(f"[{outcome}] {name}: {p['file']}")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tradingagents-root", help="path to a tradingagents checkout")
    ap.add_argument("--gui-root", help="path to a TradingAgents-GUI checkout")
    ap.add_argument("--force", action="store_true",
                    help="apply even when HEAD != pinned commit or state conflicts")
    args = ap.parse_args(argv)
    overrides = {}
    if args.tradingagents_root:
        overrides["tradingagents"] = args.tradingagents_root
    if args.gui_root:
        overrides["TradingAgents-GUI"] = args.gui_root
    results = apply_all(None, REPO_ROOT, overrides, force=args.force)
    bad = {k: v for k, v in results.items()
           if v not in ("applied", "already-applied")}
    if bad:
        print("finished with problems:", bad, file=sys.stderr)
        return 1
    print("all patches applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
