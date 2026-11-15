"""Тикет 22, срез (б): PDF сборочного листа, раскладка страниц, `kitchen assembly`, учёт, `kitchen production`."""

import json
import re
import typing
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import KitchenError
from pypdf import PdfReader
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
MOCK = "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"
runner = CliRunner()
MM = 72 / 25.4


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def pages_of(result):
    return [p.extract_text() for p in PdfReader(result.output.file).pages]


def unmock(home):
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")


def all_marks(home):
    return [p.id for m in api.parts_from_order(ORDER, home).modules for p in m.parts]


def test_petrov_pdf_has_every_module_and_every_mark_once_in_the_parts_table(home):
    petrov(home)
    r = api.assembly_and_save(ORDER, home)
    assert r.output.file == str(home / "orders" / ORDER / "out" / "assembly.pdf")
    text = "\n".join(pages_of(r))
    marks = all_marks(home)
    assert len(marks) == 100
    no_carcass = {p.mark for m in r.modules if not m.carcass for p in m.parts}
    assert no_carcass == {"Н6-фасад"}
    for mark in marks:
        if mark in no_carcass:  # у модуля без корпуса таблицы нет: маркировка стоит в его единственной строке
            assert text.count(mark) == 1, mark
        else:  # строка таблицы деталей начинается с маркировки; в шагах она встречается дополнительно
            assert len(re.findall(rf"^\s*{re.escape(mark)} · ", text, re.MULTILINE)) == 1, mark
    labels = [m.label for m in r.modules]
    assert len(labels) == 13
    for label in labels:
        assert len(re.findall(rf"^{re.escape(label)} — ", text, re.MULTILINE)) == 1, label
    assert "Детали (13):" in text and "Порядок сборки:" in text and "Фурнитура:" in text
    assert "П1-бок-Л · боковина · 2100×560×16 мм · ЛДСП Egger 16 мм, белый W1000 ST9" in text
    assert "Техника: духовой шкаф, микроволновка" in text
    assert "Петля Blum Clip Top Blumotion 110° накладная — 2 шт" in text
    assert re.search(r"^\s*1\. П1-дно к П1-бок-Л, П1-бок-П — \d+ конфирмат", text, re.MULTILINE)


def test_dishwasher_block_is_one_line_and_has_no_empty_sections(home):
    petrov(home)
    text = "\n".join(pages_of(api.assembly_and_save(ORDER, home)))
    line = "Н6 — фасад посудомоечной машины, 450 мм: корпуса нет, фасад Н6-фасад крепится к посудомойке на объекте"
    flat = re.sub(r"\s+", " ", text)
    assert line in flat
    assert "Н6-фасад · " not in text  # таблицы деталей у такого блока нет
    assert "Детали (0)" not in text


def test_watermark_banner_and_version_in_footer_when_mock_and_none_without(home):
    petrov(home)
    r = api.assembly_and_save(ORDER, home)
    for text in pages_of(r):
        assert text.count(MOCK) == 2 and "версия проекта 1" in text
    unmock(home)
    r = api.assembly_and_save(ORDER, home)
    assert not r.mock
    for text in pages_of(r):
        assert "МОК" not in text and "версия проекта 1" in text


def test_empty_project_is_one_page_saying_no_modules(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["walls"] = [{"label": "A", "length": 2800, "rows": []}]
    project["appliances"] = []
    path = home / "empty.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, path, home=home)
    r = api.assembly_and_save(ORDER, home)
    assert r.modules == []
    pages = pages_of(r)
    assert len(pages) == 1 and "Модулей нет" in pages[0]


def test_layout_keeps_module_whole_and_text_inside_the_sheet(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    pages = api.assembly_layout(result)
    assert len(pages) > 1
    for page in pages:
        for line in page.lines:
            assert 10 * MM - 1e-6 <= line.x and line.x + line.width <= 200 * MM + 1e-6, line.text
            assert line.y >= 19 * MM - 1e-6, line.text  # над колонтитулом
    seen = [label for page in pages for label in page.modules if not label.endswith("продолжение")]
    assert seen == [m.label for m in result.modules]
    for label in seen:  # без «продолжения»: модуль целиком на одной странице
        assert sum(label in page.modules for page in pages) == 1
    assert not any("продолжение" in line.text for page in pages for line in page.lines)


def test_long_module_continues_on_next_page_with_continuation_heading(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    block = result.modules[0]
    long = block.model_copy(update={"parts": block.parts * 8})
    pages = api.assembly_layout(result.model_copy(update={"modules": [long]}))
    assert len(pages) >= 2
    assert not any(line.text.startswith(f"{long.label}, продолжение") for line in pages[0].lines)
    assert pages[1].lines[0].text == f"{long.label}, продолжение" and pages[1].lines[0].bold
    for page in pages:
        assert all(line.y >= 19 * MM - 1e-6 for line in page.lines)


def test_output_is_registered_and_becomes_stale_after_new_version(home):
    petrov(home)
    api.assembly_and_save(ORDER, home)
    outputs = [o for o in api.list_outputs(ORDER, home) if o.kind == "assembly"]
    assert [(o.path, o.source_version) for o in outputs] == [(f"orders/{ORDER}/out/assembly.pdf", 1)]
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    v2 = home / "v2.json"
    v2.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, v2, home=home)
    status = api.status(ORDER, home)
    assert [s.command for s in status.stale if s.kind == "assembly"] == [f"kitchen assembly {ORDER}"]
    assert "сборочный лист" in status.text
    api.assembly_and_save(ORDER, home)
    assert not [s for s in api.status(ORDER, home).stale if s.kind == "assembly"]


def test_from_order_writes_nothing(home):
    petrov(home)
    r = api.assembly_from_order(ORDER, home)
    assert r.output is None and r.source.version == 1
    assert not (home / "orders" / ORDER / "out" / "assembly.pdf").exists()


def test_cli_order_text_and_json(home):
    petrov(home)
    data = json.loads(runner.invoke(app, ["assembly", "petrov", "--json"]).output)
    expected = json.loads(api.assembly_from_order(ORDER, home).model_dump_json())
    assert data["modules"] == expected["modules"] and data["mock"] is True
    assert data["output"]["kind"] == "assembly" and data["source"]["version"] == 1
    assert set(data) >= {"source", "output", "warnings", "mock", "modules"}
    assert set(data["modules"][0]) >= {"label", "title", "parts", "hardware", "appliances", "steps"}
    assert set(data["modules"][0]["parts"][0]) == {"mark", "name", "size", "material"}
    assert set(data["modules"][0]["hardware"][0]) == {"id", "name", "quantity", "unit"}
    text = runner.invoke(app, ["assembly", "petrov"])
    assert text.exit_code == 0, text.output
    lines = text.output.strip().splitlines()
    assert lines[0] == MOCK and lines[-1] == MOCK
    assert "Сборочный лист:" in lines and "версия 1 проекта" in text.output
    p1 = next(m for m in data["modules"] if m["label"] == "П1")
    summary = f"  П1 — пенал: деталей 13, позиций фурнитуры {len(p1['hardware'])}, шагов {len(p1['steps'])}"
    assert summary in lines
    assert "  Н6 — фасад посудомоечной машины: деталей 1, позиций фурнитуры 0, шагов 0" in lines
    assert any("assembly.pdf" in line for line in lines)


def test_cli_project_writes_nothing_and_order_with_project_is_an_error(home):
    project = home / "p.json"
    project.write_text((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"), encoding="utf-8")
    result = runner.invoke(app, ["assembly", "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "Файл не записан (--project)" in result.output
    assert (
        json.loads(runner.invoke(app, ["assembly", "--project", str(project), "--json"]).output)["output"]
        is None
    )
    assert not (home / "orders").exists()
    for args in (["assembly"], ["assembly", "petrov", "--project", str(project)]):
        bad = runner.invoke(app, args)
        assert bad.exit_code != 0 and "либо заказ" in bad.output


def test_production_writes_assembly_not_drilling_map_and_leaves_nothing_stale(home):
    petrov(home)
    r = api.production_and_save(ORDER, home)
    assert {o.kind for o in api.list_outputs(ORDER, home)} == set(api.PRODUCTION_KINDS)
    assert set(api.PRODUCTION_KINDS) == {
        "cutting", "edging", "purchase_md", "purchase_json", "assembly",
    }  # fmt: skip
    out = home / "orders" / ORDER / "out"
    assert (out / "assembly.pdf").is_file() and not (out / "drilling-map.pdf").exists()
    assert r.assembly.output.kind == "assembly" and not hasattr(r, "drilling")
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    v2 = home / "v2.json"
    v2.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, v2, home=home)
    assert {s.kind for s in api.status(ORDER, home).stale} == set(api.PRODUCTION_KINDS)
    api.production_and_save(ORDER, home)
    assert not api.status(ORDER, home).stale


def test_production_cli_prints_assembly_after_purchase_and_no_drilling_map(home):
    petrov(home)
    text = runner.invoke(app, ["production", "petrov"]).output
    assert text.index("purchase.json") < text.index("assembly.pdf")
    assert "drilling-map.pdf" not in text and "присадки" not in text


def test_output_kind_assembly_is_in_the_recompute_registry():
    assert "assembly" in typing.get_args(api.OutputKind)
    assert api.OUTPUT_RECOMPUTE["assembly"].command == "assembly"
    assert api.OUTPUT_RECOMPUTE["assembly"].label == "сборочный лист"


def test_bad_workshop_stage_list_blocks_the_command(home):
    petrov(home)
    path = home / "workshop" / "construction.yaml"
    path.write_text(
        re.sub(r"^сборка:.*\n", "сборка: [корпус]\n", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    )
    with pytest.raises(KitchenError):
        api.assembly_and_save(ORDER, home)


SECTION_HEADS = ("Детали (", "Фурнитура:", "Порядок сборки:")


def test_section_heading_is_never_the_last_line_of_a_page(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    block = result.modules[0]
    for n in range(40, 120):
        long = block.model_copy(update={"parts": (block.parts * 10)[:n]})
        pages = api.assembly_layout(result.model_copy(update={"modules": [long]}))
        for page in pages[:-1]:
            assert not page.lines[-1].text.startswith(SECTION_HEADS), (n, page.lines[-1].text)


def test_carcass_module_without_workshop_hardware_says_none(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    bare = result.modules[0].model_copy(update={"hardware": []})
    pages = api.assembly_layout(result.model_copy(update={"modules": [bare]}))
    assert "Фурнитура: нет" in [line.text for line in pages[0].lines]


def test_wrapped_step_continues_with_hanging_indent(home):
    petrov(home)
    pages = api.assembly_layout(api.assembly_from_order(ORDER, home))
    lines = [line for page in pages for line in page.lines]
    i = next(i for i, ln in enumerate(lines) if ln.text.startswith("5. Направляющие") and "Н1-" in ln.text)
    assert lines[i + 1].text.startswith("Н1-дно-ящ-3") and lines[i + 1].x > lines[i].x
