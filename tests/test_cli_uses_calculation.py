"""Тикет 72 (п. 2): CLI строит расчёт через `api.calculation` и не обращается к старой форме `api`,
которую удаляет тикет 75 (имена — `REMOVED` из `test_no_old_api.py`)."""

import ast
from pathlib import Path

from tests.test_no_old_api import REMOVED

CLI = Path(__file__).resolve().parent.parent / "engine" / "kitchen" / "cli.py"


def test_cli_does_not_touch_removed_api_names():
    used = sorted(
        {
            node.attr
            for node in ast.walk(ast.parse(CLI.read_text(encoding="utf-8")))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "api"
            and node.attr in REMOVED
        }
    )
    assert not used, f"cli.py зовёт старую форму api: {used}"
