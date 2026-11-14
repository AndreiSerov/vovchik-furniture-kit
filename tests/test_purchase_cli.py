"""Контракт команд закупки и производства."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def test_purchase_cli_matches_api_and_shows_items(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    result = runner.invoke(app, ["purchase", "petrov", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == json.loads(api.purchase_and_save("petrov", home).model_dump_json())
    text = runner.invoke(app, ["purchase", "petrov"])
    assert text.exit_code == 0, text.output
    lines = text.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "версия 1" in text.output
    assert "ЛДСП-Центр" in text.output and "confirmat_7x50" in text.output
    assert "102" in text.output and "900.00" in text.output
    assert "Итого" in text.output and "purchase.md" in text.output and "purchase.json" in text.output


def test_production_cli_matches_api_and_shows_outputs(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    result = runner.invoke(app, ["production", "petrov", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == json.loads(api.production_and_save("petrov", home).model_dump_json())
    text = runner.invoke(app, ["production", "petrov"])
    assert text.exit_code == 0, text.output
    lines = text.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "версия 1" in text.output and "ldsp_16_white" in text.output
    assert "edge_08_white" in text.output and "50.9" in text.output
    assert "ЛДСП-Центр" in text.output and "Итого" in text.output
    for filename in ("cutting-map.pdf", "edging-map.pdf", "purchase.md", "purchase.json"):
        assert filename in text.output


@pytest.mark.parametrize("command", ["purchase", "production"])
def test_commands_report_missing_project_without_traceback(home, command):
    api.order_new(ORDER, home=home)
    result = runner.invoke(app, [command, "petrov"])
    assert result.exit_code == 1
    assert "проекта ещё нет" in result.output and "Traceback" not in result.output


def test_purchase_without_mock_has_no_banner(home, base_door_project):
    for workshop_file in (home / "workshop").glob("*.yaml"):
        workshop_file.write_text(workshop_file.read_text().replace("mock: true", "mock: false"))
    project = json.loads(base_door_project.read_text())
    project["walls"][0]["length"] = 600
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, base_door_project, home=home)
    result = runner.invoke(app, ["purchase", "petrov"])
    assert result.exit_code == 0, result.output
    assert MOCK_BANNER not in result.output


@pytest.mark.parametrize(
    "minimum,step,expected", [("4.35", "0.1", "4.4"), ("123.4567", "0.0001", "123.4567")]
)
def test_production_shows_actual_edge_purchase_length(home, base_door_project, minimum, step, expected):
    materials = home / "workshop" / "materials.yaml"
    materials.write_text(
        materials.read_text().replace("продажа: метраж от 1 м", f"продажа: метраж от {minimum} м")
    )
    construction = home / "workshop" / "construction.yaml"
    construction.write_text(construction.read_text().replace("вверх до 0.1 м", f"вверх до {step} м"))
    project = json.loads(base_door_project.read_text())
    project["walls"][0]["length"] = 600
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, base_door_project, home=home)
    result = runner.invoke(app, ["production", "petrov"])
    assert result.exit_code == 0, result.output
    line = next(line for line in result.output.splitlines() if line.startswith("Кромка edge_08_white:"))
    assert f"к закупке {expected} м" in line
