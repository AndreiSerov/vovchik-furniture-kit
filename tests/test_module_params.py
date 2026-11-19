"""Параметры модуля: цвет фасада (story 4), число полок и дверей (story 5)."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

from tests.conftest import BASE_DOOR_PROJECT


def _project(home, mutate):
    project = json.loads(json.dumps(BASE_DOOR_PROJECT))
    mutate(project)
    path = home / "variant.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def _parts(home, mutate):
    return api.calculation(project=_project(home, mutate), home=home).parts()


# --- цвет фасада ---------------------------------------------------------------
# Н1 W600: две двери 297 x 716 мм, 2 * 0.212652 = 0.425304 -> 0.4253 м² (эталон ТЗ).


def test_module_facade_color_overrides_project_color(home):
    def mutate(p):
        p["materials"]["facades_lower_color"] = "RAL 7044"
        p["walls"][0]["rows"][0]["modules"][0]["materials"] = {"facade_color": "RAL 5002"}

    assert _parts(home, mutate).facade_area_by_color == {"RAL 5002": 0.4253}


def test_row_facade_color_splits_area_by_color(home):
    def mutate(p):
        p["materials"]["facades_lower_color"] = "RAL 7044"
        row = p["walls"][0]["rows"][0]
        row["materials"] = {"facade_color": "RAL 9003"}
        row["modules"].append(
            {"label": "Н2", "template": "base_door", "width": 600, "materials": {"facade_color": "RAL 3020"}}
        )

    # ряд красит Н1 в 9003, Н2 переопределяет цвет сам
    assert _parts(home, mutate).facade_area_by_color == {"RAL 3020": 0.4253, "RAL 9003": 0.4253}


def test_upper_row_color_override_goes_to_upper_facades(home):
    def mutate(p):
        p["materials"]["facades_lower_color"] = "RAL 7044"
        p["materials"]["facades_upper_color"] = "RAL 9003"
        p["walls"][0]["rows"].append(
            {
                "kind": "upper",
                "materials": {"facade_color": "RAL 1015"},
                "modules": [{"label": "В1", "template": "wall_door", "width": 600}],
            }
        )

    assert set(_parts(home, mutate).facade_area_by_color) == {"RAL 7044", "RAL 1015"}


def test_facade_color_override_splits_purchase_by_color(home):
    fixtures = Path(__file__).resolve().parent / "fixtures"
    order = "2026-047-petrov"
    project = json.loads((fixtures / "petrov_project.json").read_text(encoding="utf-8"))
    # Н1 перекрашен модулем: у Петрова нижние фасады RAL 7044, верхние RAL 9003
    first_lower = next(r for r in project["walls"][0]["rows"] if r["kind"] == "lower")["modules"][0]
    first_lower["materials"] = {**(first_lower.get("materials") or {}), "facade_color": "RAL 5002"}
    path = home / "petrov_recolored.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.brief_save(order, fixtures / "petrov_brief.json", home)
    api.import_project(order, path, home=home)

    purchased = {
        i.color: i.quantity
        for shop in api.calculation(order=order, home=home).purchase().shops
        for i in shop.items
        if i.kind == "facade"
    }
    assert set(purchased) == {"RAL 5002", "RAL 7044", "RAL 9003"}
    assert purchased == api.calculation(order=order, home=home).parts().facade_area_by_color


# --- число полок и дверей --------------------------------------------------------
# Н1 base_door W600 H720 D560, T16: полка (W - 2T - 2) x (D - 20) = 566 x 540, первая на z=300,
# шаг 300 -> вторая на z=600, третья (900) выше H - 2T = 688. Полкодержателей 4 на полку.


def _module(p, **fields):
    p["walls"][0]["rows"][0]["modules"][0].update(fields)


def _module_parts(result, label="Н1"):
    return {part.id: part for m in result.modules if m.module == label for part in m.parts}


@pytest.mark.parametrize("how", ["contents", "legacy"])
def test_two_shelves(home, how):
    def mutate(p):
        _module(p, **({"contents": {"shelves": 2}} if how == "contents" else {"shelves": 2}))

    result = _parts(home, mutate)
    parts = _module_parts(result)
    assert "Н1-полка" not in parts
    for n, z in ((1, 300), (2, 600)):
        shelf = parts[f"Н1-полка-{n}"]
        assert (shelf.length, shelf.width, shelf.position.z) == (566, 540, z)
    assert result.hardware["shelf_pin_5"] == 8
    assert result.hardware["confirmat_7x50"] == 8  # корпус не изменился


def test_zero_shelves_has_no_shelf_and_no_pins(home):
    result = _parts(home, lambda p: _module(p, contents={"shelves": 0}))
    assert not [n for n in _module_parts(result) if "полка" in n]
    assert "shelf_pin_5" not in result.hardware


def test_too_many_shelves_names_limit(home):
    with pytest.raises(OrderError) as e:
        _parts(home, lambda p: _module(p, contents={"shelves": 3}))
    text = e.value.problems[0]
    assert "Н1" in text and "не более 2" in text and "тикет" not in text


def test_contents_shelves_override_legacy_shelves_field(home):
    result = _parts(home, lambda p: _module(p, shelves=1, contents={"shelves": 2}))
    assert {"Н1-полка-1", "Н1-полка-2"} <= set(_module_parts(result))


def test_single_door_on_600_module(home):
    # одна дверь допустима до 600 мм по ширине модуля: 600 - 4 = 596 x 716
    result = _parts(home, lambda p: _module(p, contents={"doors": 1}))
    parts = _module_parts(result)
    doors = [x for x in parts.values() if x.name == "фасад"]
    assert [(d.id, d.length, d.width) for d in doors] == [("Н1-фасад", 716, 596)]
    # фасад 716 мм -> 2 петли (ступень до 900), планка на петлю, одна ручка
    assert (result.hardware["hinge_71B3550"], result.hardware["plate_173L6100"]) == (2, 2)
    assert result.hardware["handle_profile"] == 1
    # саморезы: 2 петли * 4 + ХДФ ceil(2 * (716 + 596) / 150) = 8 + 18
    assert result.hardware["screw_3_5x16"] == 26


def test_two_doors_on_narrow_module_when_asked(home):
    # W=400 по умолчанию одна дверь; две допустимы от 300: (400 - 4 - 2) / 2 = 197
    def mutate(p):
        _module(p, width=400, contents={"doors": 2})

    doors = [x for x in _module_parts(_parts(home, mutate)).values() if x.name == "фасад"]
    assert sorted((d.id, d.width) for d in doors) == [("Н1-фасад-Л", 197), ("Н1-фасад-П", 197)]


def test_no_doors_is_open_module(home):
    result = _parts(home, lambda p: _module(p, contents={"doors": 0}))
    assert not [x for x in _module_parts(result).values() if x.name == "фасад"]
    assert result.facade_area == 0
    assert not {"hinge_71B3550", "plate_173L6100", "handle_profile"} & set(result.hardware)


@pytest.mark.parametrize(
    ("width", "doors", "expect"),
    [(700, 1, "600"), (250, 2, "300"), (600, 3, "не более 2")],
)
def test_door_count_outside_rule_is_rejected(home, width, doors, expect):
    with pytest.raises(OrderError) as e:
        _parts(home, lambda p: _module(p, width=width, contents={"doors": doors}))
    text = e.value.problems[0]
    assert "Н1" in text and expect in text and "тикет" not in text


def test_base_door_rejects_drawer_contents_without_ticket_reference(home):
    with pytest.raises(OrderError) as e:
        _parts(home, lambda p: _module(p, contents={"drawers": 2}))
    assert "Н1" in e.value.problems[0] and "тикет" not in e.value.problems[0]


def test_wall_door_shelves_and_single_door(home):
    # В1 wall_door W600: глубина 320, полка 566 x (320 - 20) = 300; фасад (H - 4) = 716 x (600 - 4) = 596
    def mutate(p):
        p["walls"][0]["rows"].append(
            {
                "kind": "upper",
                "modules": [
                    {
                        "label": "В1",
                        "template": "wall_door",
                        "width": 600,
                        "contents": {"shelves": 2, "doors": 1},
                    }
                ],
            }
        )

    result = _parts(home, mutate)
    parts = _module_parts(result, "В1")
    assert [(parts[f"В1-полка-{n}"].length, parts[f"В1-полка-{n}"].width) for n in (1, 2)] == [(566, 300)] * 2
    assert (parts["В1-фасад"].length, parts["В1-фасад"].width) == (716, 596)
    assert result.hardware["hinge_71B3550"] == 4 + 2  # две двери Н1 и одна дверь В1
