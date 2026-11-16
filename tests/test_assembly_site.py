"""Тикет 53: фурнитура «объект» в списке сборочного листа, число ящиков в шаге, счёт модулей в чек-листе выезда."""

import json
import re

import pytest
from kitchen import api
from kitchen.cli import app
from pypdf import PdfReader
from typer.testing import CliRunner

from tests.conftest import BASE_DOOR_PROJECT
from tests.test_assembly import ORDER, edit, mark_workshop, module_of, petrov, write_project
from tests.test_gap import gap, lower, write

runner = CliRunner()
PETROV_HARDWARE = {
    "handle_profile": 18,
    "leg_100": 26,
    "hanger_camar807": 12,
    "confirmat_7x50": 102,
    "hinge_71B3550": 20,
    "screw_3_5x16": 330,
}
STAGE_STEP = {  # позиция -> начало строки её шага
    "hinge_71B3550": "Петли",
    "tandembox_M_500": "Направляющие",
    "aventos_hks": "Подъёмник",
    "shelf_pin_5": "Полкодержатели",
}


def mark_site(home, item):
    edit(home, "hardware.yaml", f"{{id: {item},", f"{{id: {item}, ставится: объект,")


def test_petrov_site_hardware_stays_in_the_list_with_count_and_note(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    n1 = module_of(result, "Н1")
    handle = next(h for h in n1.hardware if h.id == "handle_profile")
    assert (handle.quantity, handle.installed) == (3, "объект")
    assert any(h.id == "leg_100" and h.installed == "объект" for h in n1.hardware)
    assert any(h.id == "hanger_camar807" for h in module_of(result, "В2").hardware)
    total: dict[str, int] = {}
    for m in result.modules:
        for h in m.hardware:
            total[h.id] = total.get(h.id, 0) + h.quantity
    parts = api.parts_from_order(ORDER, home)
    assert (
        {k: total[k] for k in PETROV_HARDWARE}
        == PETROV_HARDWARE
        == {k: parts.hardware[k] for k in PETROV_HARDWARE}
    )


def test_petrov_no_handle_or_leg_steps_and_site_flag_exactly_on_three_ids(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    for m in result.modules:
        assert not any(s.startswith(("Ручки", "Опоры")) for s in m.steps), m.label
    site = {h.id for m in result.modules for h in m.hardware if h.installed == "объект"}
    assert site == {"leg_100", "hanger_camar807", "handle_profile"}
    shop = {h.installed for m in result.modules for h in m.hardware if h.id == "hinge_71B3550"}
    assert shop == {"цех"}
    data = json.loads(
        runner.invoke(app, ["assembly", ORDER, "--json"], env={"KITCHEN_HOME": str(home)}).output
    )
    h0 = next(h for h in data["modules"][1]["hardware"] if h["id"] == "handle_profile")
    assert set(h0) == {"id", "name", "quantity", "unit", "installed"} and h0["installed"] == "объект"


def test_petrov_drawer_step_names_drawer_count_not_bottoms(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    name = "Tandembox antaro M 500, комплект"
    assert f"Направляющие {name} — 3 компл., ящиков: 3" in module_of(result, "Н1").steps
    assert f"Направляющие {name} — 1 компл., ящиков: 1" in module_of(result, "П1").steps
    assert not any("-дно-ящ-" in s for m in result.modules for s in m.steps)
    assert any(p.mark == "Н1-дно-ящ-3" for p in module_of(result, "Н1").parts)  # таблица деталей — как была


def test_petrov_handles_equal_facade_count_in_each_carcass_module(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    parts = api.parts_from_order(ORDER, home)
    for m in result.modules:
        built = next(b for b in parts.modules if b.module == m.label)
        facades = sum(1 for p in built.parts if p.name.startswith("фасад"))
        got = sum(h.quantity for h in m.hardware if h.id == "handle_profile")
        assert got == facades, m.label


@pytest.mark.parametrize("item", sorted(STAGE_STEP))
def test_site_item_stays_listed_with_note_and_its_stage_step_goes(home, item):
    petrov(home)
    before = api.assembly_from_order(ORDER, home)
    mark_site(home, item)
    after = api.assembly_from_order(ORDER, home)
    for b, a in zip(before.modules, after.modules, strict=True):
        qty = {h.id: h.quantity for h in b.hardware}
        assert {h.id: h.quantity for h in a.hardware} == qty, a.label
        if item in qty:
            assert next(h for h in a.hardware if h.id == item).installed == "объект"
        assert not any(s.startswith(STAGE_STEP[item]) for s in a.steps), a.label
        rest = [s for s in b.steps if not s.startswith(STAGE_STEP[item])]
        assert a.steps == rest, a.label
    assert any(s.startswith(STAGE_STEP[item]) for m in before.modules for s in m.steps)


@pytest.mark.parametrize(("item", "start"), [("confirmat_7x50", " к "), ("screw_3_5x16", "Задняя стенка")])
def test_confirmat_and_screw_steps_stay_when_marked_site(home, item, start):
    petrov(home)
    before = api.assembly_from_order(ORDER, home)
    mark_site(home, item)
    after = api.assembly_from_order(ORDER, home)
    assert [m.steps for m in after.modules] == [m.steps for m in before.modules]
    assert any(start in s for m in after.modules for s in m.steps)
    assert any(h.id == item and h.installed == "объект" for m in after.modules for h in m.hardware)


def test_handles_marked_workshop_have_no_note_step_stays_and_trip_omits_them(home):
    petrov(home)
    mark_workshop(home, "handle_profile")
    result = api.assembly_from_order(ORDER, home)
    n1 = module_of(result, "Н1")
    assert next(h for h in n1.hardware if h.id == "handle_profile").installed == "цех"
    assert any(s.startswith("Ручки") for s in n1.steps)
    trip = api.trip_from_order(ORDER, home)
    site = next(s for s in trip.sections if s.title == "Фурнитура для монтажа")
    assert not any("Ручка" in i.text for i in site.items)


def test_sheet_text_marks_site_items(home):
    from kitchen.cli import _render_assembly

    petrov(home)
    api.assembly_and_save(ORDER, home)
    out = api.assembly_from_order(ORDER, home)
    assert out.modules
    assert "позиций фурнитуры 9" in _render_assembly(out).replace("\n", " ")
    pdf = next((home / "orders").rglob("*assembly*.pdf"))
    text = "\n".join(p.extract_text() for p in PdfReader(pdf).pages)
    assert re.search(r"Ручка-профиль торцевая\s+—\s+3\s+шт,\s+на\s+объекте", text)


def trip_titles(result):
    return result.sections[0].title


def test_petrov_trip_counts_modules_and_gaps_in_pdf_cli_and_json(home):
    petrov(home)
    api.trip_and_save(ORDER, home)
    env = {"KITCHEN_HOME": str(home)}
    title = "Модули (13), пустых мест 1"
    assert trip_titles(api.trip_from_order(ORDER, home)) == title
    assert f"{title}:" in runner.invoke(app, ["trip", ORDER], env=env).output
    assert (
        json.loads(runner.invoke(app, ["trip", ORDER, "--json"], env=env).output)["sections"][0]["title"]
        == title
    )
    pdf = next((home / "orders").rglob("*trip*.pdf"))
    text = "\n".join(p.extract_text() for p in PdfReader(pdf).pages)
    assert title in text


def test_trip_without_gaps_and_empty_project(home):
    path = write_project(home, BASE_DOOR_PROJECT)
    assert trip_titles(api.trip_from_project(path, home)) == "Модули (1)"
    empty = json.loads(json.dumps(BASE_DOOR_PROJECT))
    empty["walls"][0]["rows"] = []
    assert trip_titles(api.trip_from_project(write_project(home, empty), home)) == "Модули (0)"


def test_trip_only_gaps_says_zero_modules(home):
    walls = [{"label": "A", "length": 600, "rows": [lower(gap("ПМ1", 600))]}]
    path = write(home, walls, appliances=[], name="gaps.json")
    assert trip_titles(api.trip_from_project(path, home)) == "Модули (0), пустых мест 1"


def _lines(result):
    return [ln.text for page in api.assembly_layout(result) for ln in page.lines]


def test_workshop_items_have_no_site_note_and_site_items_do(home):
    """Ревью 53: пометка «на объекте» — только у позиций «объект»."""
    petrov(home)
    lines = _lines(api.assembly_from_order(ORDER, home))
    assert "Конфирмат 7x50 — 8 шт" in lines
    assert "Ручка-профиль торцевая — 3 шт, на объекте" in lines
    assert not any(t.startswith("Конфирмат") and "на объекте" in t for t in lines)


def test_handles_marked_workshop_line_has_no_site_note(home):
    petrov(home)
    mark_workshop(home, "handle_profile")
    result = api.assembly_from_order(ORDER, home)
    lines = _lines(result.model_copy(update={"modules": [module_of(result, "Н1")]}))
    assert "Ручка-профиль торцевая — 3 шт" in lines
