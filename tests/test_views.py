"""Тикет 41: виды кухни в PDF — план сверху и вид на каждую стену спереди (`kitchen 3d`).

Логика — через API (`api.views_from_project`, `api.views_from_order`, `api.views_layout`, `api.views_and_save`),
PDF читается `pypdf`."""

import json
from pathlib import Path

from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from pypdf import PdfReader
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PETROV = FIXTURES / "petrov_project.json"
ORDER = "2026-047-petrov"
runner = CliRunner()


def petrov_order(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, PETROV, home=home)


def pages_of(result):
    return [p.extract_text() for p in PdfReader(result.output.file).pages]


def petrov_labels():
    data = json.loads(PETROV.read_text(encoding="utf-8"))
    return {w["label"]: [m["label"] for row in w["rows"] for m in row["modules"]] for w in data["walls"]}


def test_petrov_views_have_plan_and_a_page_per_wall_with_its_labels(home):
    petrov_order(home)
    result = api.views_and_save(ORDER, home)
    assert result.output.file == str(home / "orders" / ORDER / "out" / "kitchen-views.pdf")
    pages = pages_of(result)
    labels = petrov_labels()
    assert len(pages) == 1 + len(labels) == result.pages
    assert "План сверху" in pages[0]
    for label in (m for ms in labels.values() for m in ms):
        assert label in pages[0], label
    for text, (wall, modules) in zip(pages[1:], labels.items(), strict=True):
        length = {"A": 2800, "Б": 1800}[wall]
        assert f"Стена {wall}, {length} мм — вид спереди" in text
        for label in modules:
            assert label in text, (wall, label)


def test_cli_order_prints_source_checks_and_views_line_and_json_is_the_api_result(home):
    petrov_order(home)
    result = runner.invoke(app, ["3d", "petrov"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    body = [line for line in lines if line and line != MOCK_BANNER]
    assert body[0].startswith(f"Источник: заказ {ORDER}, версия 1 проекта")
    warnings = len(api.views_from_order(ORDER, home).check_warnings)
    assert warnings == 3  # предупреждения проверок Петрова с брифом
    assert body[1] == "Проверки: 0 ошибок, 3 предупреждения"
    path = home / "orders" / ORDER / "out" / "kitchen-views.pdf"
    assert body[2] == f"Виды PDF: {path} (стен 2, страниц 3)"
    data = json.loads(runner.invoke(app, ["3d", "petrov", "--json"]).output)
    expected = json.loads(api.views_from_order(ORDER, home).model_dump_json())
    assert data["scene"] == expected["scene"] and data["findings"] == expected["findings"]
    assert data["mock"] is True and data["output"]["kind"] == "views" and data["pages"] == 3


def test_cli_project_writes_no_files_and_order_with_project_is_an_error(home):
    project = home / "p.json"
    project.write_text(PETROV.read_text(encoding="utf-8"), encoding="utf-8")
    result = runner.invoke(app, ["3d", "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "Файл не записан (--project)" in result.output
    assert (
        json.loads(runner.invoke(app, ["3d", "--project", str(project), "--json"]).output)["output"] is None
    )
    assert not (home / "orders").exists()
    for args in (["3d"], ["3d", "petrov", "--project", str(project)]):
        bad = runner.invoke(app, args)
        assert bad.exit_code != 0 and "либо заказ" in bad.output
