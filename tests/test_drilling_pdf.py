"""Тикет 19, срез (б): карта присадки PDF, `kitchen drilling`, учёт вывода. PDF читаем обратно `pypdf`."""

import json
import re
from pathlib import Path

from kitchen import api
from kitchen.cli import app
from pypdf import PdfReader
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
MOCK = "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"
runner = CliRunner()
TITLE = re.compile(r"^Деталь (\S+) — ", re.MULTILINE)


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def pages_of(result):
    return [p.extract_text() for p in PdfReader(result.output.file).pages]


def test_map_has_each_drilled_part_once_and_no_part_without_holes(home):
    petrov(home)
    r = api.drilling_and_save(ORDER, home)
    assert r.output.file == str(home / "orders" / ORDER / "out" / "drilling-map.pdf")
    pages = pages_of(r)
    titles = [t for text in pages for t in TITLE.findall(text)]
    drilled = [p.id for p in r.parts]
    assert sorted(titles) == sorted(drilled) and len(titles) == len(set(titles))
    everything = {p.id for m in api.parts_from_order(ORDER, home).modules for p in m.parts}
    assert not set(titles) & (everything - set(drilled))  # детали без отверстий страниц не занимают
    assert "Н6-фасад" not in drilled and "В1-фасад" not in drilled  # ПММ и подъёмник без чашек
    for text in pages:
        assert 2 <= len(TITLE.findall(text)) <= 4
        assert text.count(MOCK) == 2 and "версия проекта 1" in text  # водяной знак и колонтитул
    assert len(pages) == -(-len(drilled) // 4)


def test_map_without_mock_has_no_watermark_and_shows_hole_numbers(home):
    petrov(home)
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")
    r = api.drilling_and_save(ORDER, home)
    assert not r.mock
    pages = pages_of(r)
    assert all("МОК" not in text for text in pages)
    text = "\n".join(pages)
    door = next(p for p in r.parts if p.id == "Н2-фасад")
    assert "базовые кромки: нижняя, левая" in text
    for h in door.holes:  # строка таблицы отверстий с расстояниями от базовых кромок
        assert f"чашка петли · пласть -y · Ø35×13 · {h.from_base[0]} · {h.from_base[1]}" in text


def test_output_is_registered_and_becomes_stale_after_new_version(home):
    petrov(home)
    r = api.drilling_and_save(ORDER, home)
    outputs = [o for o in api.list_outputs(ORDER, home) if o.kind == "drilling"]
    assert [(o.path, o.source_version) for o in outputs] == [(f"orders/{ORDER}/out/drilling-map.pdf", 1)]
    assert r.output.source_version == 1
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    v2 = home / "v2.json"
    v2.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, v2, home=home)
    stale = [s for s in api.status(ORDER, home).stale if s.kind == "drilling"]
    assert [s.command for s in stale] == [f"kitchen drilling {ORDER}"]
    assert "карта присадки" in api.status(ORDER, home).text
    api.drilling_and_save(ORDER, home)
    assert not [s for s in api.status(ORDER, home).stale if s.kind == "drilling"]


def test_drilling_from_order_writes_nothing(home):
    petrov(home)
    r = api.drilling_from_order(ORDER, home)
    assert r.output is None and r.source.version == 1
    assert not (home / "orders" / ORDER / "out" / "drilling-map.pdf").exists()


def test_cli_order_json_matches_api_and_text_names_pdf(home):
    petrov(home)
    result = runner.invoke(app, ["drilling", "petrov", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    expected = json.loads(api.drilling_from_order(ORDER, home).model_dump_json())
    assert data["parts"] == expected["parts"] and data["checks"] == expected["checks"]
    assert data["output"]["kind"] == "drilling" and data["mock"] is True
    text = runner.invoke(app, ["drilling", "petrov"])
    assert text.exit_code == 0, text.output
    assert "drilling-map.pdf" in text.output and "конфирматы: отверстий в пласть 102 = 102" in text.output
    assert MOCK in text.output


def test_cli_project_writes_no_files_and_needs_exactly_one_source(home):
    project = home / "p.json"
    project.write_text((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"), encoding="utf-8")
    result = runner.invoke(app, ["drilling", "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["output"] is None
    assert not (home / "orders").exists()
    for args in (["drilling"], ["drilling", "petrov", "--project", str(project)]):
        bad = runner.invoke(app, args)
        assert bad.exit_code != 0 and "либо заказ" in bad.output


def test_order_without_drilled_parts_gets_one_page_saying_so(home):
    """Только фасад ПММ: отверстий нет — одна страница с пометкой, сверка по нулям."""
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["walls"] = [
        {
            "label": "A",
            "length": 450,
            "rows": [
                {"kind": "lower", "modules": [{"label": "Н6", "template": "dishwasher_panel", "width": 450}]}
            ],
        }
    ]
    project["appliances"] = [{"type": "dishwasher", "width": 450, "module": "Н6"}]
    path = home / "pmm.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, path, home=home)
    r = api.drilling_and_save(ORDER, home)
    assert r.parts == [] and {(c.holes, c.expected) for c in r.checks} == {(0, 0)}
    pages = pages_of(r)
    assert len(pages) == 1 and "Деталей с отверстиями нет." in pages[0]
