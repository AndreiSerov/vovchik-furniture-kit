import json

from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

runner = CliRunner()


def test_parts_json_equals_api_and_text_has_banner(home, base_door_project):
    r = runner.invoke(app, ["parts", "--project", str(base_door_project), "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data == json.loads(api.parts_from_project(base_door_project, home).model_dump_json())
    assert data["mock"] is True and MOCK_BANNER not in r.output

    t = runner.invoke(app, ["parts", "--project", str(base_door_project)])
    assert t.exit_code == 0, t.output
    lines = t.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "Н1-бок-Л" in t.output and "2026-047-petrov" in t.output
    assert "3.71" in t.output and "0.4253" in t.output


def test_parts_missing_file_exits_1(home):
    r = runner.invoke(app, ["parts", "--project", str(home / "nope.json")])
    assert r.exit_code == 1 and "не найден" in r.output


def test_odd_width_exits_1_without_traceback(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["width"] = 601
    odd = home / "odd.json"
    odd.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    for extra in ([], ["--json"]):
        r = runner.invoke(app, ["parts", "--project", str(odd), *extra])
        assert r.exit_code == 1, r.output
        assert "Traceback" not in r.output
        assert "Н1" in r.output and "601" in r.output and "чётную" in r.output


def test_parts_order_contract(home):
    """`kitchen parts <заказ>`: --json равен API, плашка мока, версия и бриф, находки и общие позиции в тексте."""
    from pathlib import Path

    fixtures = Path(__file__).resolve().parent / "fixtures"
    api.brief_save("2026-047-petrov", fixtures / "petrov_brief.json", home)
    api.import_project("2026-047-petrov", fixtures / "petrov_project.json", home=home)
    r = runner.invoke(app, ["parts", "petrov", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == json.loads(api.parts_from_order("petrov", home).model_dump_json())
    t = runner.invoke(app, ["parts", "2026-047-petrov"])
    assert t.exit_code == 0, t.output
    lines = t.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "версия 1" in t.output and "бриф №1" in t.output
    assert "Н5: без задней стенки" in t.output  # находка проверок
    assert "Общие позиции" in t.output and "plinth_100" in t.output and "RAL 7044" in t.output

    f = runner.invoke(app, ["parts", "--project", str(fixtures / "petrov_project.json")])
    assert f.exit_code == 0, f.output
    assert "файл" in f.output and "petrov_project.json" in f.output and "бриф №1" in f.output


def test_parts_needs_exactly_one_source(home, base_door_project):
    none = runner.invoke(app, ["parts"])
    assert none.exit_code == 1 and "заказ" in none.output and "--project" in none.output
    both = runner.invoke(app, ["parts", "petrov", "--project", str(base_door_project)])
    assert both.exit_code == 1 and "либо" in both.output
