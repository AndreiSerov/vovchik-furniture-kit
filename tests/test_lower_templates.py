"""Эталоны нижних шаблонов. ПРОВЕРИТЬ МАСТЕРУ."""

import json
from pathlib import Path

import pytest
from kitchen import api

from tests.conftest import FIELDS_NOT_IN_GOLDEN


@pytest.fixture(autouse=True)
def no_legacy_shelves(base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0].pop("shelves", None)
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


def test_dishwasher_panel_600(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: один фасад 716 × 596, фурнитура к ПММ не рассчитывается.
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["template"] = "dishwasher_panel"
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(base_door_project, home)
    expected = json.loads(
        (Path(__file__).parent / "golden/dishwasher_panel_600.json").read_text(encoding="utf-8")
    )
    assert result.model_dump(exclude=FIELDS_NOT_IN_GOLDEN, exclude_none=True) == expected
    (part,) = result.modules[0].parts
    assert (part.id, part.name, part.length, part.width) == ("Н1-фасад", "фасад", 716, 596)
    assert result.hardware == {}
    assert result.facade_area == 0.4267
    assert result.edges == {}


def test_base_sink_750(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: мойка без полки и задней стенки, две царги и ведро.
    # Кромка: (720×2 + 718×3) = 3594 мм, припуск 150 мм, закупка 4.2 м.
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    module = project["walls"][0]["rows"][0]["modules"][0]
    module.update(template="base_sink", width=750, without_back=True, contents={"inserts": ["bin_2sec"]})
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(base_door_project, home)
    expected = json.loads((Path(__file__).parent / "golden/base_sink_750.json").read_text(encoding="utf-8"))
    assert result.model_dump(exclude=FIELDS_NOT_IN_GOLDEN, exclude_none=True) == expected
    assert [p.name for p in result.modules[0].parts] == [
        "боковина",
        "боковина",
        "дно",
        "царга",
        "царга",
        "фасад",
        "фасад",
    ]
    assert result.hardware["bin_2sec"] == 1
    assert "shelf_pin_5" not in result.hardware
    assert any("без задней стенки" in w for w in result.warnings)


def test_base_bottle_150(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: одна выдвижная бутылочница, полки и петли отсутствуют.
    # Кромка: (720×2 + 118×3) = 1794 мм, припуск 150 мм, закупка 2.2 м.
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    module = project["walls"][0]["rows"][0]["modules"][0]
    module.update(template="base_bottle", width=150, contents={"inserts": ["bottle_150"]})
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(base_door_project, home)
    expected = json.loads((Path(__file__).parent / "golden/base_bottle_150.json").read_text(encoding="utf-8"))
    assert result.model_dump(exclude=FIELDS_NOT_IN_GOLDEN, exclude_none=True) == expected
    assert len([p for p in result.modules[0].parts if p.name == "фасад"]) == 1
    assert not any(p.name == "полка" for p in result.modules[0].parts)
    assert result.hardware["bottle_150"] == 1
    assert "hinge_71B3550" not in result.hardware


def test_base_drawers_600(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: три Tandembox, фасады 140 + 286 + 286 с зазорами 2 мм.
    # Корпус: кромка 3144 мм, припуск 150 мм, закупка 3.7 м.
    # Внутренняя ширина 568: дно 493×490, задняя стенка ящика 481×68 мм.
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    module = project["walls"][0]["rows"][0]["modules"][0]
    module.update(template="base_drawers", width=600, contents={"drawers": 3})
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(base_door_project, home)
    expected = json.loads(
        (Path(__file__).parent / "golden/base_drawers_600.json").read_text(encoding="utf-8")
    )
    assert result.model_dump(exclude=FIELDS_NOT_IN_GOLDEN, exclude_none=True) == expected
    parts = result.modules[0].parts
    assert sorted(p.width for p in parts if p.name == "фасад") == [140, 286, 286]
    assert len([p for p in parts if p.name == "дно ящика"]) == 3
    assert len([p for p in parts if p.name == "задняя стенка ящика"]) == 3
    assert result.hardware["tandembox_M_500"] == 3
    assert "hinge_71B3550" not in result.hardware


def test_drawer_odd_width_has_single_facade_per_drawer(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    module = project["walls"][0]["rows"][0]["modules"][0]
    module.update(template="base_drawers", width=601, contents={"drawers": 3})
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(base_door_project, home)
    assert len([p for p in result.modules[0].parts if p.name == "фасад"]) == 3


def test_sink_without_back_does_not_require_back_material(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["template"] = "base_sink"
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    path = home / "workshop/construction.yaml"
    path.write_text(
        path.read_text().replace("материал: hdf_3_white", "материал: ldsp_16_white"), encoding="utf-8"
    )
    assert any("без задней стенки" in w for w in api.parts_from_project(base_door_project, home).warnings)


def test_bottle_one_facade_above_door_threshold(home, base_door_project):
    path = home / "workshop/construction.yaml"
    path.write_text(path.read_text().replace("макс_ширина: 150", "макс_ширина: 700"), encoding="utf-8")
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0].update(template="base_bottle", width=601)
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert (
        len(
            [p for p in api.parts_from_project(base_door_project, home).modules[0].parts if p.name == "фасад"]
        )
        == 1
    )


def test_drawer_nonpositive_dimension_fails(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0].update(template="base_drawers", width=100)
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError, match="Н1.*положительными"):
        api.parts_from_project(base_door_project, home)


def test_drawer_edging_follows_workshop_rule(home, base_door_project):
    path = home / "workshop/construction.yaml"
    path.write_text(
        path.read_text().replace("кромить_детали: false", "кромить_детали: true"), encoding="utf-8"
    )
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0].update(template="base_drawers", contents={"drawers": 3})
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    parts = api.parts_from_project(base_door_project, home).modules[0].parts
    for part in parts:
        if part.name in ("дно ящика", "задняя стенка ящика"):
            assert set(part.edges.values()) == {"edge_08_white"}


def test_drawer_uses_its_material_thickness(home, base_door_project):
    path = home / "workshop/construction.yaml"
    text = path.read_text(encoding="utf-8")
    # У материала ящика толщина 3 мм; правила корпуса остаются 16 мм.
    text = text.replace("материал: ldsp_16_white\n", "материал: hdf_3_white\n")
    path.write_text(text, encoding="utf-8")
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["template"] = "base_drawers"
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    parts = api.parts_from_project(base_door_project, home).modules[0].parts
    bottom = next(p for p in parts if p.id == "Н1-дно-ящ-1")
    back = next(p for p in parts if p.id == "Н1-зад-ящ-1")
    assert bottom.thickness == back.thickness == 3
    assert back.position.y + back.thickness == bottom.position.y


def test_legacy_shelves_are_not_silently_ignored(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    for template, shelves in (
        ("base_sink", 1),
        ("base_sink", 2),
        ("base_bottle", 1),
        ("base_bottle", 2),
        ("base_drawers", 1),
        ("base_drawers", 2),
    ):
        project = json.loads(base_door_project.read_text(encoding="utf-8"))
        module = project["walls"][0]["rows"][0]["modules"][0]
        module.update(template=template, shelves=shelves, width=150 if template == "base_bottle" else 600)
        base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(OrderError, match="Н1.*пол"):
            api.parts_from_project(base_door_project, home)


def test_drawers_and_bottle_do_not_consult_hinge_table(home, base_door_project):
    path = home / "workshop/construction.yaml"
    path.write_text(
        path.read_text().replace("[[900, 2], [1600, 3], [2000, 4], [9999, 5]]", "[[100, 2]]"),
        encoding="utf-8",
    )
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    for template, width in (("base_drawers", 600), ("base_bottle", 150)):
        project["walls"][0]["rows"][0]["modules"][0].update(template=template, width=width)
        base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        assert api.parts_from_project(base_door_project, home).modules


def test_bottle_rejects_other_or_duplicate_inserts(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    module = project["walls"][0]["rows"][0]["modules"][0]
    for inserts in (["bin_2sec"], ["bottle_150", "bottle_150"]):
        module.update(template="base_bottle", width=150, contents={"inserts": inserts})
        base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(OrderError, match="Н1.*одна вставка"):
            api.parts_from_project(base_door_project, home)


def test_drawer_formulas_come_from_workshop(home, base_door_project):
    path = home / "workshop/construction.yaml"
    text = path.read_text(encoding="utf-8").replace('"W - 2 * T"', '"W - 2 * T - 10"')
    text = text.replace('"Z + T"', '"Z + T + 5"')
    path.write_text(text, encoding="utf-8")
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["template"] = "base_drawers"
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    parts = api.parts_from_project(base_door_project, home).modules[0].parts
    bottom = next(p for p in parts if p.id == "Н1-дно-ящ-1")
    assert (bottom.length, bottom.width, bottom.position.z) == (490, 483, 23)
