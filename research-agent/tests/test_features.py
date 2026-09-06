# tests/test_features.py — feature registry, enable/disable, uninstall safety
import os

import pytest

from research_agent import features
from research_agent.features import registry as feats


def test_core_feature_present_and_not_uninstallable():
    assert features.is_core("core") is True
    assert feats.get_feature("core") is not None


def test_optional_features_declared():
    for fid in ("memory_tier_b", "diagnostics", "mcp", "knowledge_graph"):
        f = feats.get_feature(fid)
        assert f is not None, f"{fid} missing"
        assert f.owned_files or f.owned_packages


def test_is_enabled_default_true():
    # without config override, optional features default enabled
    assert features.is_enabled("memory_tier_b") is True


def test_set_enabled_roundtrip(temp_data_dir, monkeypatch):
    monkeypatch.setattr("research_agent.config.get_data_dir", lambda: temp_data_dir)
    assert feats.set_enabled("memory_tier_b", False) is True
    assert feats.is_enabled("memory_tier_b") is False
    assert feats.set_enabled("memory_tier_b", True) is True
    assert feats.is_enabled("memory_tier_b") is True


def test_set_enabled_core_rejected(temp_data_dir):
    assert feats.set_enabled("core", False) is False


def test_set_enabled_unknown_rejected(temp_data_dir):
    assert feats.set_enabled("nonexistent_feature", False) is False


def test_scan_references_self_excluded(temp_data_dir):
    """Owned files referencing their own package must not be flagged."""
    src_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
    # diagnostics feature: owned package research_agent.diagnostics
    offenders = feats.scan_references("diagnostics", src_root)
    # diagnostics files themselves excluded; core agent.py references → expected flagged
    assert all("diagnostics" in o for o in offenders)


def test_scan_references_diagnostics_is_clean():
    """After gating, core has no module-level reference → diagnostics is uninstallable."""
    src_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
    assert feats.scan_references("diagnostics", src_root) == []


def test_scan_flags_module_level_reference(tmp_path):
    """A core file with a TOP-LEVEL import of an owned module must be flagged."""
    pkg = tmp_path / "research_agent"
    (pkg / "diag").mkdir(parents=True)
    (pkg / "diag" / "__init__.py").write_text("", encoding="utf-8")
    bad = pkg / "core_module.py"
    bad.write_text("from research_agent.diag import X\n", encoding="utf-8")
    # register a throwaway feature pointing at the tmp package
    feats.FEATURES["_tmpfeat"] = feats.Feature(
        id="_tmpfeat", owned_packages=["research_agent.diag"])
    try:
        offenders = feats.scan_references("_tmpfeat", str(pkg))
        assert any("core_module" in o for o in offenders)
    finally:
        feats.FEATURES.pop("_tmpfeat", None)


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_cli_feature_list_smoke(temp_data_dir, monkeypatch):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    monkeypatch.setattr("research_agent.config.get_data_dir", lambda: temp_data_dir)
    runner = CliRunner()
    res = runner.invoke(cli_mod.feature, ["list"])
    assert res.exit_code == 0
    assert "memory_tier_b" in res.output
    assert "diagnostics" in res.output
    assert "core" in res.output


def test_cli_feature_disable_and_list(temp_data_dir, monkeypatch):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    monkeypatch.setattr("research_agent.config.get_data_dir", lambda: temp_data_dir)
    runner = CliRunner()
    r1 = runner.invoke(cli_mod.feature, ["disable", "mcp"])
    assert r1.exit_code == 0
    assert feats.is_enabled("mcp") is False
    r2 = runner.invoke(cli_mod.feature, ["enable", "mcp"])
    assert r2.exit_code == 0
    assert feats.is_enabled("mcp") is True


def test_cli_uninstall_core_rejected(temp_data_dir, monkeypatch):
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    monkeypatch.setattr("research_agent.config.get_data_dir", lambda: temp_data_dir)
    runner = CliRunner()
    res = runner.invoke(cli_mod.feature, ["uninstall", "core", "--yes"])
    assert res.exit_code == 0
    assert "不可卸载" in res.output


def test_cli_uninstall_cancelled_without_yes(temp_data_dir, monkeypatch):
    """Uninstall prompts for confirmation; declining cancels (nothing deleted)."""
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    monkeypatch.setattr("research_agent.config.get_data_dir", lambda: temp_data_dir)
    runner = CliRunner()
    res = runner.invoke(cli_mod.feature, ["uninstall", "mcp"], input="n\n")
    assert res.exit_code == 0
    assert "已取消" in res.output
    # mcp_loader still present
    from research_agent.tools import mcp_loader  # noqa
    assert os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                       "src", "research_agent", "tools", "mcp_loader.py"))


def test_cli_uninstall_requires_confirmation_for_real_feature(temp_data_dir, monkeypatch):
    """Without --yes and declining, feature stays enabled and files intact."""
    from click.testing import CliRunner
    from research_agent import cli as cli_mod
    monkeypatch.setattr("research_agent.config.get_data_dir", lambda: temp_data_dir)
    runner = CliRunner()
    # decline via input 'n'
    res = runner.invoke(cli_mod.feature, ["uninstall", "mcp"], input="n\n")
    assert res.exit_code == 0
    from research_agent import features as feats_pkg
    assert feats_pkg.is_enabled("mcp") is True
