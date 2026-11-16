"""Тикет 40: чек-лист выезда (4.4) — ключ `выезд` equipment.yaml, состав разделов, PDF, учёт, `kitchen production`."""

import json
import re
import typing
from pathlib import Path

import pytest
import yaml
from kitchen import api
from kitchen.cli import app
from kitchen.errors import KitchenError
from pypdf import PdfReader
from typer.testing import CliRunner

from tests.test_gap_appliances import fridge_project

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
MOCK = "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"
runner = CliRunner()
MM = 72 / 25.4


def without_trip_key(home) -> list[str]:
    path = home / "workshop" / "equipment.yaml"
    text = path.read_text(encoding="utf-8")
    return text[: text.index("выезд:")].splitlines()  # ключ стоит в конце файла


def test_repository_equipment_names_the_trip_lists_and_mocks_md_mentions_them(repo):
    data = yaml.safe_load((repo / "workshop" / "equipment.yaml").read_text(encoding="utf-8"))
    assert set(data["выезд"]) == {"инструмент", "расходники"}
    assert all(isinstance(s, str) for key in data["выезд"] for s in data["выезд"][key])
    mocks = (repo / "MOCKS.md").read_text(encoding="utf-8")
    assert "`выезд.инструмент`" in mocks and "`выезд.расходники`" in mocks


def test_workshop_without_trip_key_is_load_error(base_door_project, home):
    (home / "workshop" / "equipment.yaml").write_text("\n".join(without_trip_key(home)) + "\n")
    with pytest.raises(KitchenError) as e:
        api.parts_from_project(base_door_project, home)
    assert "выезд" in "\n".join(e.value.problems)


@pytest.mark.parametrize(
    "bad",
    ["выезд: {инструмент: [a], расходники: [b], склад: [c]}", "выезд: {инструмент: a, расходники: [b]}"],
    ids=["unknown-key", "not-a-list"],
)
def test_bad_trip_key_is_load_error_naming_it(base_door_project, home, bad):
    (home / "workshop" / "equipment.yaml").write_text("\n".join([*without_trip_key(home), bad]) + "\n")
    with pytest.raises(KitchenError) as e:
        api.parts_from_project(base_door_project, home)
    text = "\n".join(e.value.problems)
    assert "выезд" in text


def petrov(home, brief=None):
    api.brief_save(ORDER, brief or FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def section(result, prefix):
    return next(s for s in result.sections if s.title.startswith(prefix))


def texts(result, prefix):
    return [i.text for i in section(result, prefix).items]


def brief_with_installation(home, **fields):
    brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    brief["installation"] = {k: {"value": v, "source": "client"} for k, v in fields.items()}
    path = home / "brief-installation.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    return path


def test_petrov_modules_section_lists_all_13_in_project_order(home):
    # 13 модулей и пустое место ПМ1 за верхним угловым стены Б (тикет 50)
    petrov(home)
    r = api.trip_from_order(ORDER, home)
    assert section(r, "Модули").title == "Модули (13), пустых мест 1"
    items = texts(r, "Модули")
    parts = api.parts_from_order(ORDER, home)
    assert len(items) == 14
    assert sorted(i.split(" — ")[0] for i in items) == sorted([*(m.module for m in parts.modules), "ПМ1"])
    assert "ПМ1 — пустое место, 264 мм" in items
    assert "П1 — пенал, 600 мм" in items
    assert "Н6 — фасад посудомоечной машины, 450 мм" in items


def test_petrov_common_items_without_hangers(home):
    petrov(home)
    common = texts(api.trip_from_order(ORDER, home), "Общие позиции")
    by_name = {t.split(" — ")[0]: t.split(" — ")[1] for t in common}
    assert [v for k, v in by_name.items() if k.startswith("Цоколь")] == ["1 шт"]
    assert by_name["Монтажная шина 2 м"] == "2 шт"
    assert [v for k, v in by_name.items() if "LED" in k and "питания" not in k.lower()] == ["4 м"]
    assert [v for k, v in by_name.items() if "питания" in k.lower()] == ["1 шт"]
    assert "Столешница Egger 38 мм, мрамор Леванто — 1 лист 4100×600 (общая длина 3400 мм)" in common
    assert not any("Навес" in t for t in common)
    assert len(common) == 5  # цоколь, шина, LED, блок питания, столешница


def test_petrov_site_hardware_is_summed_over_the_project(home):
    petrov(home)
    site = texts(api.trip_from_order(ORDER, home), "Фурнитура для монтажа")
    assert "Опора регулируемая 100 мм с клипсой — 26 шт" in site
    assert "Ручка-профиль торцевая — 18 шт" in site
    assert "Навес для верхних шкафов Camar 807 — 12 шт" in site
    assert len(site) == 3


def test_petrov_client_appliances_include_the_sink(home):
    petrov(home)
    items = texts(api.trip_from_order(ORDER, home), "Техника клиента")
    assert len(items) == 6
    assert "Духовой шкаф — П1" in items and "Мойка — Н5" in items and "Вытяжка — В1" in items


def test_appliance_line_carries_the_model_when_there_is_one(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["appliances"][0]["model"] = "Bosch HBA"
    path = home / "p.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert "Духовой шкаф Bosch HBA — П1" in texts(api.trip_from_project(path, home), "Техника клиента")


def test_header_takes_client_and_installation_from_the_latest_brief(home):
    petrov(
        home,
        brief_with_installation(
            home, parking="paid", dismantling="client", walls_evenness="uneven", floor_evenness="even"
        ),
    )
    assert api.trip_from_order(ORDER, home).header.lines() == [
        "Клиент: Петров, +7 900 000-00-00",
        "Адрес: Москва",
        "Этаж: —, лифт: —",
        "Парковка: платная",
        "Демонтаж старой кухни: делает клиент",
        "Стены: неровные, пол: ровный",
    ]


def test_header_dashes_for_missing_and_unknown_values(home):
    petrov(home, brief_with_installation(home, parking="unknown", floor_evenness="uneven"))
    assert api.trip_from_order(ORDER, home).header.lines()[3:] == [
        "Парковка: —",
        "Демонтаж старой кухни: —",
        "Стены: —, пол: неровный",
    ]


def test_order_without_brief_has_dash_header_and_warning(home):
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    r = api.trip_from_order(ORDER, home)
    assert r.header.lines() == [
        "Клиент: —",
        "Адрес: —",
        "Этаж: —, лифт: —",
        "Парковка: —",
        "Демонтаж старой кухни: —",
        "Стены: —, пол: —",
    ]
    assert "брифа нет — шапка пустая" in r.warnings


def test_project_file_has_dash_header_even_when_the_order_has_a_brief(home):
    petrov(home)
    r = api.trip_from_project(FIXTURES / "petrov_project.json", home)
    assert r.header.lines()[0] == "Клиент: —" and r.output is None


def test_base_door_project_has_no_countertop_rail_led_or_zero_lines(base_door_project, home):
    r = api.trip_from_project(base_door_project, home)
    everything = "\n".join(i.text for s in r.sections for i in s.items)
    for word in ("Столешница", "шина", "LED", "питания", "Навес", " 0 "):
        assert word not in everything, word
    assert not any(t.endswith(" — 0 шт") for t in texts(r, "Общие позиции"))
    assert texts(r, "Модули") == ["Н1 — нижний модуль, 600 мм"]


def test_empty_project_has_empty_modules_but_tools_and_consumables(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["walls"] = [{"label": "A", "length": 2800, "rows": []}]
    project["appliances"] = []
    path = home / "empty.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    r = api.trip_from_project(path, home)
    assert section(r, "Модули").title == "Модули (0)" and section(r, "Модули").items == []
    assert texts(r, "Инструмент") and texts(r, "Расходники")
    assert texts(r, "Фурнитура для монтажа") == []


def test_tools_and_consumables_come_from_equipment_yaml_as_is(base_door_project, home):
    lines = [
        *without_trip_key(home),
        "выезд:",
        "  инструмент: [Болгарка, Лазерный уровень]",
        "  расходники: []",
    ]
    (home / "workshop" / "equipment.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = api.trip_from_project(base_door_project, home)
    assert texts(r, "Инструмент") == ["Болгарка", "Лазерный уровень"]
    assert section(r, "Расходники").items == []


def test_handles_marked_workshop_leave_the_trip_checklist(base_door_project, home):
    assert any("Ручка" in t for t in texts(api.trip_from_project(base_door_project, home), "Фурнитура"))
    path = home / "workshop" / "hardware.yaml"
    text = path.read_text(encoding="utf-8")
    old = "{id: handle_profile, ставится: объект,"
    assert old in text
    path.write_text(text.replace(old, "{id: handle_profile, ставится: цех,"), encoding="utf-8")
    assert not any("Ручка" in t for t in texts(api.trip_from_project(base_door_project, home), "Фурнитура"))


# --- PDF, CLI, учёт, production -------------------------------------------------


def pages_of(result):
    return [p.extract_text() for p in PdfReader(result.output.file).pages]


def unmock(home):
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")


def import_v2(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    path = home / "v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, path, home=home)


def test_petrov_pdf_has_header_sections_and_a_box_on_every_item(home):
    petrov(home)
    r = api.trip_and_save(ORDER, home)
    assert r.output.file == str(home / "orders" / ORDER / "out" / "trip-checklist.pdf")
    text = "\n".join(pages_of(r))
    flat = re.sub(r"\s+", " ", text)
    assert "Чек-лист выезда" in text
    assert "Клиент: Петров, +7 900 000-00-00" in text
    for title in (
        "Модули (13), пустых мест 1",
        "Общие позиции",
        "Фурнитура для монтажа",
        "Техника клиента",
        "Инструмент",
        "Расходники",
    ):
        assert title in text, title
    assert "☐ П1 — пенал, 600 мм" in flat
    assert "☐ Опора регулируемая 100 мм с клипсой — 26 шт" in flat
    assert "☐ Столешница Egger 38 мм, мрамор Леванто — 1 лист 4100×600 (общая длина 3400 мм)" in flat
    items = sum(len(s.items) for s in r.sections)
    assert text.count("☐") == items
    for label in (m.module for m in api.parts_from_order(ORDER, home).modules):
        assert len(re.findall(rf"☐ {re.escape(label)} — ", text)) == 1, label


def test_empty_section_prints_a_dash(home):
    path = home / "workshop" / "equipment.yaml"
    path.write_text(
        "\n".join([*without_trip_key(home), "выезд:", "  инструмент: []", "  расходники: [Скотч]"]) + "\n"
    )
    petrov(home)
    text = "\n".join(pages_of(api.trip_and_save(ORDER, home)))
    assert re.search(r"Инструмент\n\s*—\n", text)


def test_long_checklist_flows_to_more_pages_inside_the_sheet(home):
    petrov(home)
    result = api.trip_from_order(ORDER, home)
    long = [s.model_copy(update={"items": s.items * 6}) for s in result.sections]
    pages = api.trip_layout(result.model_copy(update={"sections": long}))
    assert len(pages) > 1
    for page in pages:
        for line in page.lines:
            assert 10 * MM - 1e-6 <= line.x and line.x + line.width <= 200 * MM + 1e-6, line.text
            assert line.y >= 19 * MM - 1e-6, line.text
    titles = {s.title for s in long}
    for page in pages[:-1]:  # заголовок раздела не остаётся последней строкой страницы
        assert page.lines[-1].text not in titles


def test_watermark_banner_and_version_in_footer_when_mock_and_none_without(home):
    petrov(home)
    for text in pages_of(api.trip_and_save(ORDER, home)):
        assert text.count(MOCK) == 2 and "версия проекта 1" in text
    unmock(home)
    r = api.trip_and_save(ORDER, home)
    assert not r.mock
    for text in pages_of(r):
        assert "МОК" not in text and "версия проекта 1" in text


def test_output_is_registered_and_becomes_stale_after_new_version(home):
    petrov(home)
    api.trip_and_save(ORDER, home)
    outputs = [o for o in api.list_outputs(ORDER, home) if o.kind == "trip"]
    assert [(o.path, o.source_version) for o in outputs] == [(f"orders/{ORDER}/out/trip-checklist.pdf", 1)]
    import_v2(home)
    status = api.status(ORDER, home)
    assert [s.command for s in status.stale if s.kind == "trip"] == [f"kitchen trip {ORDER}"]
    assert "чек-лист выезда" in status.text
    api.trip_and_save(ORDER, home)
    assert not [s for s in api.status(ORDER, home).stale if s.kind == "trip"]


def test_from_order_writes_nothing(home):
    petrov(home)
    r = api.trip_from_order(ORDER, home)
    assert r.output is None and r.source.version == 1
    assert not (home / "orders" / ORDER / "out" / "trip-checklist.pdf").exists()


def test_cli_order_text_and_json(home):
    petrov(home)
    data = json.loads(runner.invoke(app, ["trip", "petrov", "--json"]).output)
    expected = json.loads(api.trip_from_order(ORDER, home).model_dump_json())
    assert data["sections"] == expected["sections"] and data["header"] == expected["header"]
    assert data["mock"] is True and data["output"]["kind"] == "trip" and data["source"]["version"] == 1
    assert set(data) >= {"source", "output", "warnings", "mock", "header", "sections"}
    assert set(data["sections"][0]) == {"title", "items"} and set(data["sections"][0]["items"][0]) == {"text"}
    text = runner.invoke(app, ["trip", "petrov"])
    assert text.exit_code == 0, text.output
    lines = text.output.strip().splitlines()
    assert lines[0] == MOCK and lines[-1] == MOCK
    assert "Чек-лист выезда:" in lines and "версия 1 проекта" in text.output
    assert "Клиент: Петров, +7 900 000-00-00" in lines
    assert "Модули (13), пустых мест 1:" in lines and "  П1 — пенал, 600 мм" in lines
    assert "☐" not in text.output
    assert any("trip-checklist.pdf" in line for line in lines)


def test_cli_project_writes_nothing_and_order_with_project_is_an_error(home):
    project = home / "p.json"
    project.write_text((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"), encoding="utf-8")
    result = runner.invoke(app, ["trip", "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "Файл не записан (--project)" in result.output
    assert (
        json.loads(runner.invoke(app, ["trip", "--project", str(project), "--json"]).output)["output"] is None
    )
    assert not (home / "orders").exists()
    for args in (["trip"], ["trip", "petrov", "--project", str(project)]):
        bad = runner.invoke(app, args)
        assert bad.exit_code != 0 and "либо заказ" in bad.output


def test_output_kind_trip_is_in_the_recompute_registry():
    assert "trip" in typing.get_args(api.OutputKind)
    assert api.OUTPUT_RECOMPUTE["trip"].command == "trip"
    assert api.OUTPUT_RECOMPUTE["trip"].label == "чек-лист выезда"


def test_production_writes_trip_checklist_and_leaves_nothing_stale(home):
    petrov(home)
    r = api.production_and_save(ORDER, home)
    assert "trip" in api.PRODUCTION_KINDS and "drilling" not in api.PRODUCTION_KINDS
    assert {o.kind for o in api.list_outputs(ORDER, home)} == set(api.PRODUCTION_KINDS)
    assert (home / "orders" / ORDER / "out" / "trip-checklist.pdf").is_file()
    assert r.trip.output.kind == "trip" and r.trip.header.client == "Петров, +7 900 000-00-00"
    import_v2(home)
    assert {s.kind for s in api.status(ORDER, home).stale} == set(api.PRODUCTION_KINDS)
    api.production_and_save(ORDER, home)
    assert not api.status(ORDER, home).stale


def test_production_cli_prints_trip_after_assembly(home):
    petrov(home)
    text = runner.invoke(app, ["production", "petrov"]).output
    assert text.index("assembly.pdf") < text.index("trip-checklist.pdf")


def test_glossary_names_the_trip_checklist(repo):
    glossary = (repo / "GLOSSARY.md").read_text(encoding="utf-8")
    assert "| Чек-лист выезда | trip checklist |" in glossary


def test_check_workshop_passes_on_the_repository_workshop(home):
    result = api.check_workshop(home)
    assert result.valid
    equipment = next(f for f in result.files if f.file == "equipment.yaml")
    assert equipment.counts


def test_trip_lists_the_gap_as_a_place(home):
    r = api.trip_from_project(fridge_project(home, 620), home)
    assert "ПМ1 — место под холодильник, 620 мм" in texts(r, "Модули")
    assert "Холодильник — ПМ1" in texts(r, "Техника клиента")


def test_gap_without_appliance_is_an_empty_place_first_in_its_row(home):
    path = fridge_project(home, 600, appliance={"type": "sink", "width": 600, "module": "Н1"})
    r = api.trip_from_project(path, home)
    assert section(r, "Модули").title == "Модули (1), пустых мест 1"
    assert texts(r, "Модули") == ["ПМ1 — пустое место, 600 мм", "Н1 — нижний модуль, 600 мм"]


def test_trip_pdf_and_cli_work_on_a_project_with_a_gap(home):
    path = fridge_project(home, 620)
    out = runner.invoke(app, ["trip", "--project", str(path)]).output
    assert "ПМ1 — место под холодильник, 620 мм" in out
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, path, home=home)
    assert runner.invoke(app, ["production", ORDER]).exit_code == 0
    assert (home / "orders" / ORDER / "out" / "trip-checklist.pdf").exists()


def test_every_production_kind_builds_on_a_project_with_a_gap(home):
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, fridge_project(home, 620), home=home)
    api.production_and_save(ORDER, home)
    assert {o.kind for o in api.list_outputs(ORDER, home)} == set(api.PRODUCTION_KINDS)


def test_header_has_floor_and_elevator_after_the_address(home):
    brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    brief["client"]["floor"] = {"value": 5, "source": "client"}
    brief["client"]["elevator"] = {"value": "freight", "source": "client"}
    path = home / "brief-floor.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    petrov(home, path)
    lines = api.trip_from_order(ORDER, home).header.lines()
    assert lines[1:3] == ["Адрес: Москва", "Этаж: 5, лифт: грузовой"]


@pytest.mark.parametrize(
    ("raw", "label"), [("none", "нет"), ("passenger", "пассажирский"), ("freight", "грузовой")]
)
def test_elevator_labels_come_from_the_brief_schema(home, raw, label):
    brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    brief["client"]["elevator"] = {"value": raw, "source": "client"}
    path = home / "brief-lift.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    petrov(home, path)
    assert api.trip_from_order(ORDER, home).header.lines()[2] == f"Этаж: —, лифт: {label}"


def test_petrov_modules_keep_the_project_order(home):
    petrov(home)
    labels = [t.split(" — ")[0] for t in texts(api.trip_from_order(ORDER, home), "Модули")]
    assert labels == ["П1", "Н1", "Н2", "Н3", "Н4", "В1", "В2", "В3", "В4", "Н5", "Н6", "ПМ1", "В5", "В6"]


@pytest.mark.parametrize(("drop", "expected"), [("phone", "Петров, —"), ("name", "—, +7 900 000-00-00")])
def test_client_line_keeps_the_known_half(home, drop, expected):
    brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    del brief["client"][drop]
    path = home / "brief-partial.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    petrov(home, path)
    assert api.trip_from_order(ORDER, home).header.client == expected


def test_cli_empty_section_prints_a_dash(home):
    path = home / "workshop" / "equipment.yaml"
    path.write_text(
        "\n".join([*without_trip_key(home), "выезд:", "  инструмент: []", "  расходники: [Скотч]"]) + "\n"
    )
    petrov(home)
    out = runner.invoke(app, ["trip", "petrov"]).output
    assert "Инструмент:\n  —\nРасходники:\n  Скотч\n" in out


def test_section_heading_never_ends_a_page_whatever_the_item_count(home):
    petrov(home)
    result = api.trip_from_order(ORDER, home)
    first = result.sections[0]
    for n in range(1, 90):
        items = [first.items[0].model_copy(update={"text": f"строка {i}"}) for i in range(n)]
        sections = [first.model_copy(update={"items": items}), *result.sections[1:]]
        pages = api.trip_layout(result.model_copy(update={"sections": sections}))
        titles = {s.title for s in sections}
        for page in pages[:-1]:
            assert page.lines[-1].text not in titles, (n, page.lines[-1].text)


def test_long_item_wraps_under_the_text_not_under_the_box(home):
    petrov(home)
    result = api.trip_from_order(ORDER, home)
    first = result.sections[0]
    long = first.items[0].model_copy(update={"text": "очень длинная строка " * 20})
    sections = [first.model_copy(update={"items": [long]}), *result.sections[1:]]
    lines = api.trip_layout(result.model_copy(update={"sections": sections}))[0].lines
    start = next(i for i, l in enumerate(lines) if l.text.startswith("☐ очень"))
    assert lines[start + 1].x > lines[start].x and not lines[start + 1].text.startswith("☐")


def test_long_item_stays_inside_the_right_margin(home):
    petrov(home)
    result = api.trip_from_order(ORDER, home)
    first = result.sections[0]
    long = first.items[0].model_copy(update={"text": "очень длинная строка " * 20})
    sections = [first.model_copy(update={"items": [long]}), *result.sections[1:]]
    for page in api.trip_layout(result.model_copy(update={"sections": sections})):
        for line in page.lines:
            assert line.x + line.width <= 200 * MM + 1e-6, line.text


# Уборка этапа 4 (тикет 46).
def test_client_name_with_spaces_around_is_printed_trimmed(home):
    brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    brief["client"]["name"] = {"value": "  Петров  ", "source": "client"}
    path = home / "brief-spaces.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    petrov(home, path)
    assert api.trip_from_order(ORDER, home).header.client == "Петров, +7 900 000-00-00"


def test_phone_of_only_spaces_is_printed_as_missing(home):
    """Ревью 46: поле из одних пробелов — пусто, в шапке прочерк, как без поля."""
    brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    brief["client"]["phone"] = {"value": "   ", "source": "client"}
    path = home / "brief-blank-phone.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    petrov(home, path)
    assert api.trip_from_order(ORDER, home).header.client == "Петров, —"


def test_production_cli_prints_the_empty_brief_warning(home):
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    out = runner.invoke(app, ["production", "petrov"]).output
    assert "Предупреждения:" in out and "брифа нет — шапка пустая" in out


def test_continuation_page_starts_at_the_top_without_the_heading_gap(home):
    """Заголовок раздела, с которого начинается следующая страница, стоит у верха поля: отступ перед разделом
    на новой странице не нужен."""
    petrov(home)
    result = api.trip_from_order(ORDER, home)
    first = result.sections[0]
    titles = {s.title for s in result.sections}
    seen = 0
    for n in range(1, 60):
        items = [first.items[0].model_copy(update={"text": f"строка {i}"}) for i in range(n)]
        sections = [first.model_copy(update={"items": items}), *result.sections[1:]]
        for page in api.trip_layout(result.model_copy(update={"sections": sections}))[1:]:
            head = page.lines[0]
            assert head.y == pytest.approx(page.top - 13.0 + (13.0 - head.size) / 2), (n, head.text)
            seen += head.text in titles
    assert seen  # случай «заголовок первой строкой страницы» в переборе был


def test_two_line_heading_is_not_split_between_pages(home):
    petrov(home)
    result = api.trip_from_order(ORDER, home)
    first, second = result.sections[:2]
    long = second.model_copy(update={"title": "Очень длинное название раздела чек-листа " * 4})
    for n in range(1, 90):
        items = [first.items[0].model_copy(update={"text": f"строка {i}"}) for i in range(n)]
        sections = [first.model_copy(update={"items": items}), long, *result.sections[2:]]
        pages = api.trip_layout(result.model_copy(update={"sections": sections}))
        for page in pages[:-1]:
            assert not page.lines[-1].bold, (
                n,
                page.lines[-1].text,
            )  # заголовок (жирный) не последний на странице


def test_production_cli_prints_a_warning_shared_by_purchase_and_trip_once(home):
    """Ревью 46: предупреждение, которое есть и в закупке, и в чек-листе, `production` печатает один раз."""
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)  # бриф новее версии проекта
    out = runner.invoke(app, ["production", "petrov"]).output
    assert out.count("новее брифа") == 1, out
