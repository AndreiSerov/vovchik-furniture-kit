"""Тикет 55: ручка на фасаде встраиваемой ПММ (`dishwasher_panel`), проверки приёмки 1–9."""

import json
import re
from pathlib import Path

from kitchen import api
from pypdf import PdfReader

from tests.test_assembly import ORDER, edit, module_of, petrov, write_project
from tests.test_gap import base, gap, lower, write
from tests.test_parts_api import _edit_workshop

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIXTURES.parent / "golden"


def n6(parts):
    return next(m for m in parts.modules if m.module == "Н6")


def test_petrov_handles_18_and_n6_has_one(home):
    """Проверка 1."""
    parts = api.parts_from_project(FIXTURES / "petrov_project.json", home)
    assert parts.hardware["handle_profile"] == 18
    assert n6(parts).hardware == {"handle_profile": 1}
    golden = json.loads((GOLDEN / "dishwasher_panel_600.json").read_text(encoding="utf-8"))
    assert golden["hardware"] == {"handle_profile": 1}


def test_construction_two_handles_per_facade_gives_two_on_n6_and_36_for_petrov(home):
    """Проверка 5."""
    _edit_workshop(home, "construction.yaml", "ручки: по одной на фасад", "ручки: по 2 на фасад")
    parts = api.parts_from_project(FIXTURES / "petrov_project.json", home)
    assert n6(parts).hardware == {"handle_profile": 2}
    assert parts.hardware["handle_profile"] == 36


def test_module_material_handles_override_gives_second_handle_on_n6_only(home):
    """Проверка 6."""
    knob = '  - {id: handle_knob, ставится: объект, название: "Ручка-кнопка", упаковка: 1, цена: 100, магазин: Фурнитура-Маркет}\n'
    _edit_workshop(home, "hardware.yaml", "  - {id: handle_profile,", knob + "  - {id: handle_profile,")
    data = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    for wall in data["walls"]:
        for row in wall["rows"]:
            for module in row["modules"]:
                if module["label"] == "Н6":
                    module["materials"] = {"handles": "handle_knob"}
    parts = api.parts_from_project(write_project(home, data), home)
    assert n6(parts).hardware == {"handle_knob": 1}
    assert parts.hardware["handle_profile"] == 17 and parts.hardware["handle_knob"] == 1


def test_dishwasher_in_gap_has_no_facade_and_no_handle(home):
    """Проверка 7: свободно стоящая ПММ в пустом месте ручки не получает."""
    path = write(
        home,
        [{"length": 1050, "label": "A", "rows": [lower(base("Н1"), gap("ПМ1", 450))]}],
        appliances=[{"type": "dishwasher", "width": 450, "module": "ПМ1"}],
    )
    parts = api.parts_from_project(path, home)
    assert parts.hardware["handle_profile"] == 2
    assert all(m.module != "ПМ1" for m in parts.modules)


def test_petrov_assembly_n6_lists_one_handle_without_steps(home):
    """Проверка 2 и блок Н6 в JSON."""
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    block = module_of(result, "Н6")
    assert [(h.id, h.quantity, h.installed) for h in block.hardware] == [("handle_profile", 1, "объект")]
    assert block.steps == [] and not block.carcass
    total: dict[str, int] = {}
    for m in result.modules:
        for h in m.hardware:
            total[h.id] = total.get(h.id, 0) + h.quantity
    assert total == api.parts_from_order(ORDER, home).hardware


def test_petrov_assembly_pdf_has_handle_line_under_n6_row_and_no_parts_table(home):
    """Проверка 3."""
    petrov(home)
    result = api.assembly_and_save(ORDER, home)
    text = "\n".join(p.extract_text() for p in PdfReader(result.output.file).pages)
    flat = re.sub(r"\s+", " ", text)
    row = "Н6 — фасад посудомоечной машины, 450 мм: корпуса нет, фасад Н6-фасад крепится к посудомойке на объекте"
    assert f"{row} Фурнитура: Ручка-профиль торцевая — 1 шт, на объекте" in flat
    assert text.count("Н6-фасад") == 1
    block = flat.split("Н6 — фасад посудомоечной машины")[1].split("В5 — ")[0]
    assert "Детали (" not in block


def test_handle_marked_workshop_on_n6_has_no_site_note_and_still_no_steps(home):
    """Проверка 4."""
    petrov(home)
    edit(
        home, "hardware.yaml", "{id: handle_profile, ставится: объект,", "{id: handle_profile, ставится: цех,"
    )
    block = module_of(api.assembly_from_order(ORDER, home), "Н6")
    assert [(h.id, h.quantity, h.installed) for h in block.hardware] == [("handle_profile", 1, "цех")]
    assert block.steps == []
    result = api.assembly_and_save(ORDER, home)
    flat = re.sub(r"\s+", " ", "\n".join(p.extract_text() for p in PdfReader(result.output.file).pages))
    block = flat.split("Н6 — фасад посудомоечной машины")[1].split("В5 — ")[0]
    assert "Фурнитура: Ручка-профиль торцевая — 1 шт" in block and "1 шт, на объекте" not in block


def test_petrov_purchase_estimate_economy_and_trip_numbers(home):
    """Проверка 8: числа с прогона движка до тикета 55 (Петров, версия 1)."""
    petrov(home)
    parts = api.parts_from_order(ORDER, home)
    assert parts.hardware["handle_profile"] == 18
    purchase = api.purchase_from_order(ORDER, home)
    assert purchase.total == 158074.70
    handles = [i for shop in purchase.shops for i in shop.items if i.id == "handle_profile"]
    assert [(i.quantity, i.amount) for i in handles] == [(18, 6300.00)]
    estimate = api.estimate_from_order(ORDER, home, economy=True)
    assert estimate.price == 222000 and estimate.margin == 16405.59
    assert (estimate.economy.price, estimate.economy.saving) == (185000, 37000)
    trip = api.trip_from_order(ORDER, home)
    site = next(sec for sec in trip.sections if sec.title.startswith("Фурнитура для монтажа"))
    assert "Ручка-профиль торцевая — 18 шт" in [i.text for i in site.items]


def test_workshop_check_passes_and_only_the_dishwasher_golden_holds_the_handle(home):
    """Проверка 9: мастерская цела, из эталонов ручку несёт только `dishwasher_panel_600`."""
    result = api.check_workshop(home)
    assert result.valid and result.errors == []
    goldens = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in GOLDEN.glob("*.json")}
    with_handle = {
        n
        for n, g in goldens.items()
        if "handle_profile" in g.get("hardware", {}) and n.startswith("dishwasher")
    }
    assert with_handle == {"dishwasher_panel_600.json"}
