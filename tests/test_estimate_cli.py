"""Контракт команды `kitchen estimate`."""

import json
from pathlib import Path

from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def test_estimate_cli_json_matches_api_and_text_shows_numbers(home):
    petrov(home)
    result = runner.invoke(app, ["estimate", "petrov", "--json"])
    assert result.exit_code == 0, result.output
    expected = api.save(calc := api.calculation(order="petrov", home=home), calc.estimate())[0]
    assert json.loads(result.output) == json.loads(expected.model_dump_json())
    text = runner.invoke(app, ["estimate", "petrov"])
    assert text.exit_code == 0, text.output
    lines = text.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "версия 1" in text.output and "Труд" in text.output and "Распил" in text.output
    assert f"{expected.price}" in text.output and "estimate.md" in text.output


def test_estimate_cli_project_file_writes_nothing(home):
    result = runner.invoke(app, ["estimate", "--project", str(FIXTURES / "petrov_project.json"), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["outputs"] == []
    assert not (home / "orders").exists()


def test_estimate_cli_rejects_order_with_project_and_neither(home):
    both = runner.invoke(app, ["estimate", "petrov", "--project", str(FIXTURES / "petrov_project.json")])
    assert both.exit_code == 1 and "либо заказ" in both.output
    neither = runner.invoke(app, ["estimate"])
    assert neither.exit_code == 1 and "либо заказ" in neither.output


def test_estimate_cli_reports_missing_project_without_traceback(home):
    api.order_new(ORDER, home=home)
    result = runner.invoke(app, ["estimate", "petrov"])
    assert result.exit_code == 1
    assert "проекта ещё нет" in result.output and "Traceback" not in result.output
