"""Тикет 61: отказы из-за правил мастерской — находки проверок (`drilling_mismatch`, `sheet_over_saw`)."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

from tests.test_checks import make_project, module
from tests.test_parts_api import _edit_workshop

PETROV = Path(__file__).resolve().parent / "fixtures" / "petrov_project.json"
ORDER = "2026-047-petrov"


def codes(path, home, code):
    return [f for f in api.check_project(path, home).findings if f.code == code]


def new_order():
    api.order_new(ORDER, client="Петров П. П.", phone="+7 900 000-00-00", address="Москва")


def test_fewer_shelf_pins_is_drilling_mismatch_warning_with_drilling_text(home):
    """Проверка 1."""
    _edit_workshop(home, "construction.yaml", "полкодержателей: 4", "полкодержателей: 2")
    with pytest.raises(OrderError) as refusal:
        api.drilling_from_project(PETROV, home)
    [f] = codes(PETROV, home, "drilling_mismatch")
    assert f.level == "warning"
    assert f.message == str(refusal.value)
    assert "полкодержатели" in f.message
    new_order()
    result = api.import_project("petrov", PETROV)
    assert any("Присадка не сходится" in w for w in result.warnings)


def test_two_plates_per_hinge_is_drilling_mismatch(home):
    """Проверка 2."""
    _edit_workshop(
        home, "construction.yaml", "ответные_планки: по одной на петлю", "ответные_планки: по 2 на петлю"
    )
    with pytest.raises(OrderError) as refusal:
        api.drilling_from_project(PETROV, home)
    [f] = codes(PETROV, home, "drilling_mismatch")
    assert f.level == "warning"
    assert f.message == str(refusal.value)
    assert "планк" in f.message


def test_no_shelves_no_hinges_gives_no_drilling_finding(home):
    """Проверка 3."""
    _edit_workshop(home, "construction.yaml", "полкодержателей: 4", "полкодержателей: 2")
    path = make_project(home / "p.json", [("A", 600, [("lower", [module("Н1", "base_drawers", 600)])])])
    assert codes(path, home, "drilling_mismatch") == []
    api.drilling_from_project(path, home)


def _small_saw(home):
    _edit_workshop(home, "equipment.yaml", "макс_длина_реза: 3200", "макс_длина_реза: 2500")


def test_saw_shorter_than_sheet_is_sheet_over_saw_error_with_cut_text(home):
    """Проверка 4."""
    _small_saw(home)
    with pytest.raises(OrderError) as refusal:
        api.cut_from_project(PETROV, home)
    found = codes(PETROV, home, "sheet_over_saw")
    assert found and all(f.level == "error" for f in found)
    assert str(refusal.value) in {f.message for f in found}
    assert len({f.subject for f in found}) == len(found)  # одна находка на материал
    new_order()
    with pytest.raises(OrderError) as imp:
        api.import_project("petrov", PETROV)
    assert any("подрезка листа" in p for p in imp.value.problems)


def test_saw_finding_only_for_materials_with_parts(home):
    _small_saw(home)
    path = make_project(home / "p.json", [("A", 600, [("lower", [module("Н1", "base_door", 600)])])])
    subjects = {f.subject for f in codes(path, home, "sheet_over_saw")}
    assert "ldsp_16_white" in subjects
    assert "ldsp_16_oak" not in subjects  # деталей из дуба в проекте нет


def test_part_larger_than_sheet_field_is_part_exceeds_sheet_with_cut_text(home):
    """Проверка 5: регресс."""
    data = json.loads(PETROV.read_text(encoding="utf-8"))
    wall = data["walls"][1]
    wall["length"] = 3000
    wall["rows"][0]["modules"] = [module("Н9", "base_door", 2900)]
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as refusal:
        api.cut_from_project(path, home)
    found = codes(path, home, "part_exceeds_sheet")
    assert found and all(f.level == "error" for f in found)
    assert str(refusal.value) in {f.message for f in found}


def test_petrov_on_standard_workshop_has_same_findings_as_before(home):
    """Проверка 6: снимок до правки."""
    found = [(f.code, f.level, f.subject) for f in api.check_project(PETROV, home).findings]
    assert found == [
        ("brief_missing", "warning", ORDER),
        ("appliance_no_data", "warning", "Н5"),
        ("no_back_panel", "warning", "Н5"),
    ]
