"""Provenance gathering: parsing + best-effort degradation (no real subprocess)."""

from __future__ import annotations

import subprocess

import pytest

from claude_ablation_lab.provenance import gather_provenance


def _cp(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr="")


@pytest.mark.unit
def test_gather_provenance_parses_all_fields(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    mcp_out = (
        "Checking MCP server health…\n"
        "plugin:context7:context7: npx -y pkg - ✔ Connected\n"  # name contains colons
        "github: https://api.example/mcp/ (HTTP) - ✔ Connected\n"
    )

    def fake_run(argv, **_kw):  # noqa: ANN001, ANN003
        if argv[:2] == ["claude", "--version"]:
            return _cp("2.1.193 (Claude Code)\n")
        if argv[:3] == ["claude", "mcp", "list"]:
            return _cp(mcp_out)
        if "rev-parse" in argv:
            return _cp("abc123def\n")
        return _cp("", returncode=1)

    monkeypatch.setattr("claude_ablation_lab.provenance.subprocess.run", fake_run)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("global", encoding="utf-8")

    prov = gather_provenance(home=tmp_path)
    assert prov.claude_version == "2.1.193"
    assert prov.harness_sha == "abc123def"
    assert prov.mcp_servers == ("plugin:context7:context7", "github")  # colons preserved
    assert prov.global_layer is not None and len(prov.global_layer) == 12


@pytest.mark.unit
def test_gather_provenance_best_effort_when_everything_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    def boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise FileNotFoundError("no such tool")

    monkeypatch.setattr("claude_ablation_lab.provenance.subprocess.run", boom)
    prov = gather_provenance(home=tmp_path)  # empty home → no global layer
    assert prov.claude_version is None
    assert prov.harness_sha is None
    assert prov.mcp_servers == ()
    assert prov.global_layer is None


@pytest.mark.unit
def test_global_layer_digest_changes_with_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "claude_ablation_lab.provenance.subprocess.run", lambda *a, **k: _cp("", returncode=1)
    )
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    settings = claude_dir / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    first = gather_provenance(home=tmp_path).global_layer
    settings.write_text('{"changed": true}', encoding="utf-8")
    second = gather_provenance(home=tmp_path).global_layer
    assert first is not None and second is not None and first != second
