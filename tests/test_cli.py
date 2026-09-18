"""Tests for the `geonexus` CLI commands."""

from __future__ import annotations

import json

import pytest

from geonexus import __version__
from geonexus.cli import main


def test_cli_version(capsys) -> None:
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == f"geonexus {__version__}"


def test_cli_card_validate_ok(capsys) -> None:
    path = "examples/amazon_ndvi/geocard.yaml"
    assert main(["card", "validate", path]) == 0
    assert "OK:" in capsys.readouterr().out


def test_cli_card_validate_invalid(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("geocard_version: '0.1'\nid: x\ntype: data\nname: X\n", encoding="utf-8")
    assert main(["card", "validate", str(bad)]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_cli_card_inspect(tmp_path, capsys) -> None:
    card = tmp_path / "card.json"
    card.write_text(
        json.dumps(
            {
                "geocard_version": "0.1",
                "id": "inspect-me",
                "type": "data",
                "name": "Inspect",
                "description": "d",
            }
        ),
        encoding="utf-8",
    )
    assert main(["card", "inspect", str(card)]) == 0
    out = capsys.readouterr().out
    assert '"id": "inspect-me"' in out


def test_cli_init(tmp_path, capsys) -> None:
    project = tmp_path / "my-project"
    assert main(["init", str(project)]) == 0
    assert (project / "geocard.yaml").exists()
    assert (project / "README.md").exists()
    assert "Initialized" in capsys.readouterr().out


def test_cli_unknown_command(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["not-a-command"])
    assert exc_info.value.code == 2


def test_cli_web_start_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["web", "start", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--registry" in out
    assert "--node-api-key" in out
    assert "--cors-origin" in out
    assert "--user" in out


def test_split_users_parses_and_rejects() -> None:
    """``--user`` 是本地唯一能登上 Web 层的入口，解析必须严格且容忍密码里的 =。"""
    from geonexus.cli import _split_users

    assert _split_users(None) == []
    assert _split_users(["admin=admin"]) == [("admin", "admin")]
    assert _split_users(["d=p@ss=word"]) == [("d", "p@ss=word")]
    assert _split_users(["a=1", "b=2"]) == [("a", "1"), ("b", "2")]

    for bad in (["admin"], ["=x"], ["x="]):
        with pytest.raises(SystemExit):
            _split_users(bad)


def test_cli_card_import_ogc_records_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["card", "import-ogc-records", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--record" in out
    assert "--collection" in out


def test_cli_card_import_ogc_tiles_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["card", "import-ogc-tiles", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--tileset" in out
    assert "--style" in out


def test_cli_card_import_ogc_legacy_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["card", "import-ogc-legacy", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--service" in out
    assert "wms" in out and "wmts" in out
