import json
from pathlib import Path

from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_cut_order_contract(home):
    """`kitchen cut <заказ>`: --json равен API, текст с плашкой, версией, листами, процентом и резами."""
    api.brief_save("2026-047-petrov", FIXTURES / "petrov_brief.json", home)
    api.import_project("2026-047-petrov", FIXTURES / "petrov_project.json", home=home)
    r = runner.invoke(app, ["cut", "petrov", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == json.loads(api.cut_from_order("petrov", home).model_dump_json())
    t = runner.invoke(app, ["cut", "2026-047-petrov"])
    assert t.exit_code == 0, t.output
    lines = t.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "версия 1" in t.output and "ldsp_16_white" in t.output and "Лист 1" in t.output
    assert "78.1%" in t.output and "Н1-бок-Л" in t.output and "Рез 1" in t.output
    assert "Столешница" in t.output and "2200" in t.output


def test_cut_needs_exactly_one_source(home, base_door_project):
    none = runner.invoke(app, ["cut"])
    assert none.exit_code == 1 and "--project" in none.output
    both = runner.invoke(app, ["cut", "petrov", "--project", str(base_door_project)])
    assert both.exit_code == 1 and "либо" in both.output


def test_cut_oversize_part_exits_1_without_traceback(home, base_door_project):
    path = home / "workshop/materials.yaml"
    path.write_text(path.read_text().replace("лист: [2800, 2070]", "лист: [500, 500]"))
    r = runner.invoke(app, ["cut", "--project", str(base_door_project)])
    assert r.exit_code == 1 and "Traceback" not in r.output and "не помещается" in r.output
