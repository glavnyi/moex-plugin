#!/usr/bin/env python3
"""Update the upstream working copies and re-apply the patch sets (spec §12).

Default target is the pinned commit recorded in patches/manifest.yaml.
Pass --ref to test another branch/tag/SHA; the compatibility report will
mark unverified targets as untested.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.apply_patches import apply_all, load_manifest  # noqa: E402


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
    )


def _ref_exists(root: Path, ref: str) -> bool:
    """True when ref resolves to a commit in this checkout (after fetch)."""
    for spec in (ref, f"refs/tags/{ref}", f"refs/remotes/origin/{ref}"):
        probe = _git(root, "rev-parse", "--verify", "--quiet",
                     f"{spec}^{{commit}}")
        if probe.returncode == 0 and probe.stdout.strip():
            return True
    return False


def _patch_override(name: str, root: Path) -> dict:
    """Map a pinned-project name to the manifest «projects» key."""
    return {"tradingagents-gui" if name == "TradingAgents-GUI" else name:
            str(root)}


def update_project(name: str, root: Path, ref: str, pin: str,
                   force: bool) -> str:
    if not (root / ".git").exists():
        return f"missing checkout: {root}"
    fetch = _git(root, "fetch", "origin", "--tags")
    if fetch.returncode != 0:
        return f"fetch failed: {fetch.stderr.strip()[:200]}"
    # One --ref may not exist in every repo (the two upstreams have their own
    # tags). Never destroy a working copy over it: keep the pinned commit.
    target = ref
    note = ""
    if not _ref_exists(root, ref):
        if ref == pin:
            return f"pinned commit {pin[:12]} not found — run git fetch"
        target = pin
        note = f"[skip] ref {ref} not in {name}; kept pinned {pin[:12]} — "
    # Drop local modifications so the checkout is pristine before re-applying.
    reset = _git(root, "reset", "--hard")
    if reset.returncode != 0:
        return f"reset failed: {reset.stderr.strip()[:200]}"
    clean = _git(root, "clean", "-fd")
    if clean.returncode != 0:
        return f"clean failed: {clean.stderr.strip()[:200]}"
    checkout = _git(root, "checkout", "--detach", target)
    if checkout.returncode != 0:
        # Restore the pinned state so the copy is never left bare.
        _git(root, "reset", "--hard", pin)
        apply_all(None, REPO_ROOT, _patch_override(name, root),
                  force=force, quiet=True)
        return (f"checkout {target} failed: {checkout.stderr.strip()[:200]}"
                f" (restored pinned {pin[:12]})")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    print(f"  {name}: now at {head[:12]}")
    results = apply_all(None, REPO_ROOT, _patch_override(name, root),
                        force=force, quiet=True)
    return note + ", ".join(f"{Path(k).name}={v}" for k, v in results.items())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", help="branch/tag/SHA to check out (default: pinned commit)")
    ap.add_argument("--tradingagents-root")
    ap.add_argument("--gui-root")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    roots = {
        "tradingagents": Path(args.tradingagents_root or
                              REPO_ROOT / manifest["projects"]["tradingagents"]["dir"]),
        "TradingAgents-GUI": Path(args.gui_root or
                                  REPO_ROOT / manifest["projects"]["tradingagents-gui"]["dir"]),
    }
    print("== updating upstreams ==")
    failed = False
    for name, pin in manifest["pinned"].items():
        ref = args.ref or pin["commit"]
        print(f"-> {name} @ {ref}")
        msg = update_project(name, roots[name], ref, pin["commit"], args.force)
        print("  ", msg)
        if "failed" in msg or "missing" in msg:
            failed = True
    print("\nRun scripts/check_compatibility.py to record the new status.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
