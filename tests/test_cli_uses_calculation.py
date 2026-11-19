"""Тикет 72: CLI строит расчёт через `api.calculation` и не обращается к старой форме `api`, которую удаляет
тикет 75 (имена — `REMOVED`, обход — `old_api_uses` из `test_no_old_api.py`: одно правило в одном месте).
Собственная проверка CLI «укажите либо заказ…» идёт раньше отказа `calculation()` у каждой команды."""

from pathlib import Path

import pytest
from kitchen.cli import app
from typer.testing import CliRunner

from tests.test_no_old_api import old_api_uses

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "engine" / "kitchen" / "cli.py"
PROJECT = REPO / "tests" / "fixtures" / "petrov_project.json"
runner = CliRunner()


def test_cli_does_not_touch_removed_api_names():
    used = sorted(old_api_uses(CLI))
    assert not used, f"cli.py зовёт старую форму api: {used}"


def test_cli_guard_sees_from_import_getattr_and_attribute(tmp_path):
    probe = tmp_path / "cli.py"
    probe.write_text(
        "from kitchen import api\nfrom kitchen.api import cut_and_save\n"
        "getattr(api, 'check_order')\napi.trip_from_project\n",
        encoding="utf-8",
    )
    assert old_api_uses(probe) == {"cut_and_save", "check_order", "trip_from_project"}


CLI_EXTRA = {
    "parts": [],
    "edges": [],
    "drilling": [],
    "assembly": [],
    "trip": [],
    "3d": [],
    "cut": [],
    "estimate": [],
    "check": [],
    "quote": ["--days", "35"],
}


@pytest.mark.parametrize("both", [False, True], ids=["neither", "both"])
@pytest.mark.parametrize("cmd", CLI_EXTRA)
def test_cli_either_or_text_wins_over_calculation(home, cmd, both):
    """Текст CLI с подсказкой команды, а не «Укажите либо заказ, либо файл проекта.» из `calculation()`."""
    hint = f"kitchen {cmd} petrov" + (" --days 35" if cmd == "quote" else "")
    args = [cmd, *CLI_EXTRA[cmd], *(["petrov", "--project", str(PROJECT)] if both else [])]
    r = runner.invoke(app, args)
    assert r.exit_code == (2 if cmd == "check" else 1), r.output
    assert f"Укажите либо заказ ({hint}), либо файл: --project project.json." in r.output
    assert "либо файл проекта." not in r.output
