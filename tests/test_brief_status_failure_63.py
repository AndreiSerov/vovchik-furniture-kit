"""Тикет 63 (б): сбой записи status.md после сохранения брифа — предупреждение, а не отказ."""

import json
from datetime import date
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from typer.testing import CliRunner

runner = CliRunner()
PETROV = Path(__file__).parent / "fixtures" / "petrov_brief.json"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("kitchen.store.today", lambda: date(2026, 11, 14))


@pytest.fixture
def blocked(home):
    """status.md заказа — папка: записать файл нельзя."""
    (home / "orders" / "2026-001-petrov" / "status.md").mkdir(parents=True)
    return home


def test_api_saves_brief_and_warns_when_status_md_is_a_directory(blocked):
    result = api.brief_save("petrov", PETROV)
    assert result.created and result.order == "2026-001-petrov"
    assert len(result.warnings) == 1 and "status.md не записан" in result.warnings[0]
    assert "kitchen status 2026-001-petrov" in result.warnings[0]
    assert api.brief_show("2026-001-petrov").brief == json.loads(PETROV.read_text(encoding="utf-8"))


def test_clean_save_has_no_warnings(home):
    assert api.brief_save("petrov", PETROV).warnings == []


def test_cli_exit_0_and_prints_saved_and_warning(blocked):
    r = runner.invoke(app, ["brief", "save", "petrov", str(PETROV)])
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert any(x.startswith("Заказ 2026-001-petrov заведён, бриф сохранён.") for x in lines)
    assert any(x.startswith("Предупреждение: status.md не записан:") for x in lines)
    assert "Повторите: kitchen status 2026-001-petrov." in r.output


def test_cli_json_has_warnings(blocked):
    r = runner.invoke(app, ["brief", "save", "petrov", str(PETROV), "--json"])
    assert r.exit_code == 0, r.output
    assert "status.md не записан" in json.loads(r.output)["warnings"][0]


def test_warning_has_single_period_before_repeat(blocked):
    w = api.brief_save("petrov", PETROV).warnings[0]
    assert w.endswith(". Повторите: kitchen status 2026-001-petrov.") and ".. " not in w


def test_existing_order_also_warns(home):
    first = api.brief_save("petrov", PETROV)
    status = home / "orders" / first.order / "status.md"
    status.unlink()
    status.mkdir()
    r = api.brief_save(first.order, PETROV)
    assert not r.created and r.warnings
