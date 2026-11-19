"""Формулы construction.yaml: поведение через публичный API (`kitchen.api`).

Синтаксис и переменные проверяются при загрузке мастерской (`api.load_workshop`),
значения — при расчёте деталей (`api.calculation(project=…).parts()`)."""

import pytest
from kitchen import api
from kitchen.errors import OrderError, WorkshopError


def _break(home, fname, old, new):
    p = home / "workshop" / fname
    text = p.read_text(encoding="utf-8")
    assert old in text
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def _problems(home):
    with pytest.raises(WorkshopError) as e:
        api.load_workshop(home)
    return "\n".join(e.value.problems)


def _part(home, project, part_id):
    result = api.calculation(project=project, home=home).parts()
    return next(p for m in result.modules for p in m.parts if p.id == part_id)


def _set_module(project, **fields):
    import json

    data = json.loads(project.read_text(encoding="utf-8"))
    module = data["walls"][0]["rows"][0]["modules"][0]
    if "template" in fields:
        module.pop("shelves", None)  # у ящиков полок нет
    module.update(fields)
    project.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# --- значения формул при расчёте деталей -------------------------------------------------------


def test_height_formula_gives_facade_length(home, base_door_project):
    assert _part(home, base_door_project, "Н1-фасад-Л").length == 716  # H - 4 при H = 720
    _break(home, "construction.yaml", 'высота_нижнего: "H - 4"', 'высота_нижнего: "H - 10"')
    assert _part(home, base_door_project, "Н1-фасад-Л").length == 710


def test_single_door_width_formula(home, base_door_project):
    _set_module(base_door_project, width=450)
    assert _part(home, base_door_project, "Н1-фасад").width == 446  # W - 4
    _break(home, "construction.yaml", 'одна_дверь_ширина: "W - 4"', 'одна_дверь_ширина: "W - 6"')
    assert _part(home, base_door_project, "Н1-фасад").width == 444


def test_two_doors_width_formula(home, base_door_project):
    assert _part(home, base_door_project, "Н1-фасад-Л").width == 297  # (W - 4 - 2) / 2 при W = 600


def test_pair_formula_with_cyrillic_variable(home, base_door_project):
    _set_module(base_door_project, template="base_drawers", width=572, contents={"drawers": 3})
    bottom = _part(home, base_door_project, "Н1-дно-ящ-1")
    # (внутр_ширина - 75) x (500 - 10) при внутр_ширина = 540; длина детали — больший размер
    assert (bottom.length, bottom.width) == (490, 465)


def test_inexact_division_is_a_russian_error_with_module_and_width(home, base_door_project):
    _break(home, "construction.yaml", '"(W - 4 - 2) / 2"', '"(W - 3) / 2"')
    with pytest.raises(OrderError) as e:
        api.calculation(project=base_door_project, home=home).parts()
    text = "\n".join(e.value.problems)
    assert "Н1" in text and "600" in text and "нацело" in text


def test_division_by_zero_is_a_russian_error(home, base_door_project):
    _break(home, "construction.yaml", '"(W - 4 - 2) / 2"', '"W / 0"')
    with pytest.raises((OrderError, WorkshopError)) as e:
        api.calculation(project=base_door_project, home=home).parts()
    assert "деление на ноль" in "\n".join(e.value.problems)


def test_fractional_constant_equal_to_integer_is_accepted(home, base_door_project):
    _break(home, "construction.yaml", 'одна_дверь_ширина: "W - 4"', 'одна_дверь_ширина: "W - 4.0"')
    _set_module(base_door_project, width=450)
    assert _part(home, base_door_project, "Н1-фасад").width == 446


# --- формулы проверяются при загрузке мастерской ------------------------------------------------


@pytest.mark.parametrize("bad", ["W ** 2", "min(W, 100)", "__import__('os').system('ls')", "W @ 2"])
def test_forbidden_constructs_rejected_at_load(home, bad):
    _break(home, "construction.yaml", '"(W - 4 - 2) / 2"', f'"{bad}"')
    text = _problems(home)
    assert "две_двери_ширина" in text


def test_variable_of_another_formula_rejected_at_load(home):
    _break(home, "construction.yaml", 'высота_нижнего: "H - 4"', 'высота_нижнего: "внутр_ширина - 4"')
    text = _problems(home)
    assert "высота_нижнего" in text and "«внутр_ширина»" in text


def test_non_string_formula_rejected_at_load(home):
    _break(home, "construction.yaml", 'высота_нижнего: "H - 4"', "высота_нижнего: 596")
    assert "ожидалась строка-формула, получено 596" in _problems(home)


def test_fractional_constant_rejected_at_load(home):
    _break(home, "construction.yaml", 'одна_дверь_ширина: "W - 4"', 'одна_дверь_ширина: "W - 4.5"')
    text = _problems(home)
    assert "4.5" in text and "дробные" in text


def test_unknown_variable_rejected_at_load(home):
    _break(home, "construction.yaml", 'высота_нижнего: "H - 4"', 'высота_нижнего: "Q - 4"')
    text = _problems(home)
    assert "высота_нижнего" in text and "«Q»" in text


def test_bad_syntax_rejected_at_load(home):
    _break(home, "construction.yaml", '"(W - 4 - 2) / 2"', '"W ** 2"')
    text = _problems(home)
    assert "две_двери_ширина" in text and "неожиданный элемент" in text


def test_bad_purchase_rounding_rejected_at_load(home):
    _break(
        home, "construction.yaml", "округление_закупки: вверх до 0.1 м", "округление_закупки: вверх до 0.1"
    )
    text = _problems(home)
    assert "округление_закупки" in text and "вверх до N м" in text


def test_unsorted_steps_rejected_at_load(home):
    _break(home, "construction.yaml", "[[900, 2], [1600, 3]", "[[1600, 2], [900, 3]")
    text = _problems(home)
    assert "петли_по_высоте_фасада" in text and "по возрастанию" in text


def test_unknown_edge_in_material_mapping_rejected_at_load(home):
    _break(home, "materials.yaml", "ldsp_16_white: edge_08_white", "ldsp_16_white: edge_nope")
    text = _problems(home)
    assert "кромка_к_материалу" in text and "edge_nope" in text


def test_unknown_material_in_edge_mapping_rejected_at_load(home):
    _break(home, "materials.yaml", "ldsp_16_white: edge_08_white", "ldsp_nope: edge_08_white")
    text = _problems(home)
    assert "кромка_к_материалу" in text and "ldsp_nope" in text
