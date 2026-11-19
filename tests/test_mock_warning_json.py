"""AGENTS.md, принцип 4: JSON с моком несёт текст плашки (поле `warning`) во всех командах."""

import json

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

runner = CliRunner()


def unmock(home):
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")


def test_api_result_carries_warning_when_mock(home, base_door_project):
    result = api.calculation(project=base_door_project, home=home).parts()
    assert result.mock is True and result.warning == MOCK_BANNER
    assert json.loads(result.model_dump_json())["warning"] == MOCK_BANNER


def test_api_result_has_no_warning_without_mock(home, base_door_project):
    unmock(home)
    result = api.calculation(project=base_door_project, home=home).parts()
    assert result.mock is False and result.warning is None


@pytest.mark.parametrize(
    "argv",
    [
        ["check-workshop"],
        ["quick-estimate", "--meters", "2.8"],
        ["parts", "--project", "{project}"],
        ["edges", "--project", "{project}"],
        ["assembly", "--project", "{project}"],
        ["trip", "--project", "{project}"],
    ],
    ids=lambda a: a[0],
)
def test_cli_json_carries_banner_text(home, base_door_project, argv):
    args = [a.format(project=base_door_project) for a in argv]
    r = runner.invoke(app, [*args, "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)  # остаётся валидным JSON
    assert data["mock"] is True
    assert data["warning"] == MOCK_BANNER


def test_cli_json_has_no_banner_without_mock(home, base_door_project):
    unmock(home)
    r = runner.invoke(app, ["parts", "--project", str(base_door_project), "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["mock"] is False and not data.get("warning")
    assert MOCK_BANNER not in r.output
