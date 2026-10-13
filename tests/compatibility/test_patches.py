from __future__ import annotations

from pathlib import Path

import yaml

from integration.version_check import PINNED, check
from scripts.apply_patches import head_commit, patch_state

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = yaml.safe_load((ROOT / "patches" / "manifest.yaml").read_text(encoding="utf-8"))


def test_manifest_pins_match_version_check():
    assert MANIFEST["pinned"]["tradingagents"]["commit"] == PINNED["tradingagents"]
    assert MANIFEST["pinned"]["TradingAgents-GUI"]["commit"] == PINNED["TradingAgents-GUI"]


def test_upstream_heads_match_pins():
    rep = check()
    assert rep["tradingagents"]["match"] is True
    assert rep["TradingAgents-GUI"]["match"] is True


def test_patch_files_exist_and_are_applied():
    for name, spec in MANIFEST["projects"].items():
        root = ROOT / spec["dir"]
        assert (root / ".git").exists()
        assert head_commit(root) == MANIFEST["pinned"][
            "TradingAgents-GUI" if name == "tradingagents-gui" else name
        ]["commit"]
        for p in spec["patches"]:
            patch = ROOT / p["file"]
            assert patch.exists(), f"missing patch {p['file']}"
            # every file listed in the manifest exists in the tree
            for rel in p["files"]:
                assert (root / rel).exists()
            assert patch_state(root, patch) == "applied", p["file"]


def test_apply_is_idempotent():
    from scripts.apply_patches import apply_all

    results = apply_all(None, ROOT, quiet=True)
    assert all(v in ("applied", "already-applied") for v in results.values()), results
