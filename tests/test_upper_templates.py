"""Эталоны верхних шаблонов. ПРОВЕРИТЬ МАСТЕРУ."""

import json
from pathlib import Path

import pytest
from kitchen import api

from tests.conftest import FIELDS_NOT_IN_GOLDEN


def upper_project(path, template, width=600, contents=None):
    project = json.loads(path.read_text(encoding="utf-8"))
    row = project["walls"][0]["rows"][0]
    row["kind"] = "upper"
    row["modules"] = [{"label": "В1", "template": template, "width": width}]
    if contents is not None:
        row["modules"][0]["contents"] = contents
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


def test_wall_door_roof_and_hangers(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: крыша и дно между боковинами, два навеса, без опор.
    upper_project(base_door_project, "wall_door")
    result = api.parts_from_project(base_door_project, home)
    parts = result.modules[0].parts
    assert len([p for p in parts if p.name == "крыша"]) == 1
    assert len([p for p in parts if p.name == "дно"]) == 1
    assert not any(p.name == "царга" for p in parts)
    assert result.hardware["hanger_camar807"] == 2
    assert "leg_100" not in result.hardware


def test_wall_lift_one_facade_and_lift(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: цельный фасад, один подъёмник, петли отсутствуют.
    upper_project(base_door_project, "wall_lift", contents={"lift": "aventos_hks"})
    result = api.parts_from_project(base_door_project, home)
    assert len([p for p in result.modules[0].parts if p.name == "фасад"]) == 1
    assert result.hardware["aventos_hks"] == 1
    assert "hinge_71B3550" not in result.hardware


def test_wall_dryer_has_dryer_and_no_bottom_or_shelf(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: сушка заменяет полку, днище отсутствует.
    upper_project(base_door_project, "wall_dryer", width=750, contents={"inserts": ["dryer_750"]})
    result = api.parts_from_project(base_door_project, home)
    assert not any(p.name in ("дно", "полка") for p in result.modules[0].parts)
    assert result.hardware["dryer_750"] == 1


def test_wall_hood_opening_uses_workshop_niche(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: открытый проём 568×280, передняя планка 568×40.
    upper_project(base_door_project, "wall_hood")
    result = api.parts_from_project(base_door_project, home)
    parts = result.modules[0].parts
    assert len([p for p in parts if p.name == "передняя планка дна вытяжки"]) == 1
    assert not any(p.name in ("дно", "полка") for p in parts)


def test_dryer_rejects_wrong_width(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    upper_project(base_door_project, "wall_dryer", width=600)
    with pytest.raises(OrderError, match="ширин"):
        api.parts_from_project(base_door_project, home)


def test_hood_has_fasteners_for_bottom_frame(home, base_door_project):
    upper_project(base_door_project, "wall_hood")
    result = api.parts_from_project(base_door_project, home)
    # Крыша: 4 конфирмата; передняя планка: ещё 2.
    assert result.hardware["confirmat_7x50"] == 6


@pytest.mark.parametrize(
    "template,width,contents",
    [
        ("wall_door", 600, None),
        ("wall_lift", 600, {"lift": "aventos_hks"}),
        ("wall_dryer", 750, {"inserts": ["dryer_750"]}),
        ("wall_hood", 600, None),
    ],
)
def test_upper_manual_golden(home, base_door_project, template, width, contents):
    # ПРОВЕРИТЬ МАСТЕРУ. Двери/подъёмник: кромка 3142 мм, с припуском 3292, закупка 3.7 м.
    # Корпус 720×320, дно/крыша 568×320, полка 566×300, ХДФ 716×596.
    # Сушка: кромка 2×720+718=2158 мм; 3 стороны, 2248 мм с припуском.
    # Вытяжка: кромка 2×720+568+568=2576 мм; 4 стороны, 2696 мм с припуском.
    # Крепёж вытяжки: крыша 4, передняя планка 2 конфирмата.
    upper_project(base_door_project, template, width, contents)
    result = api.parts_from_project(base_door_project, home)
    expected = json.loads(
        (Path(__file__).parent / f"golden/{template}_{width}.json").read_text(encoding="utf-8")
    )
    assert result.model_dump(exclude=FIELDS_NOT_IN_GOLDEN, exclude_none=True) == expected


def test_hood_rejects_too_narrow_front_rail(home, base_door_project):
    from kitchen.errors import OrderError

    path = home / "workshop/appliances.yaml"
    path.write_text(path.read_text().replace("ниша_глубина: 280", "ниша_глубина: 319"), encoding="utf-8")
    upper_project(base_door_project, "wall_hood")
    with pytest.raises(OrderError, match="планк"):
        api.parts_from_project(base_door_project, home)


def test_invalid_upper_hardware_reference_reported_at_workshop_load(home):
    path = home / "workshop/construction.yaml"
    path.write_text(
        path.read_text().replace("навес: hanger_camar807", "навес: missing_hanger"), encoding="utf-8"
    )
    result = api.check_workshop(home)
    assert any("missing_hanger" in problem for problem in result.errors)


@pytest.mark.parametrize(
    "template,width,contents",
    [
        ("wall_hood", 600, {"lift": "aventos_hks"}),
        ("wall_dryer", 750, {"lift": "aventos_hks", "inserts": ["dryer_750"]}),
    ],
)
def test_hood_and_dryer_can_have_lift(home, base_door_project, template, width, contents):
    upper_project(base_door_project, template, width, contents)
    result = api.parts_from_project(base_door_project, home)
    assert len([part for part in result.modules[0].parts if part.name == "фасад"]) == 1
    assert result.hardware["aventos_hks"] == 1
    assert "hinge_71B3550" not in result.hardware


def test_upper_rejects_impossible_geometry(home, base_door_project):
    from kitchen.errors import OrderError

    upper_project(base_door_project, "wall_door", width=20)
    project = json.loads(base_door_project.read_text())
    project["walls"][0]["rows"][0]["modules"][0].update(height=200, depth=10)
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    with pytest.raises(OrderError, match="В1.*габарит"):
        api.parts_from_project(base_door_project, home)


def test_hood_uses_project_appliance_dimensions(home, base_door_project):
    upper_project(base_door_project, "wall_hood")
    project = json.loads(base_door_project.read_text())
    project["appliances"] = [{"type": "hood", "width": 500, "height": 150, "depth": 240, "module": "В1"}]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    result = api.parts_from_project(base_door_project, home)
    rail = next(p for p in result.modules[0].parts if p.name == "передняя планка дна вытяжки")
    assert rail.width == 80
    assert rail.position.y == 240


def test_hood_rejects_nonpositive_project_appliance_dimensions(home, base_door_project):
    from kitchen.errors import OrderError

    upper_project(base_door_project, "wall_hood")
    project = json.loads(base_door_project.read_text())
    project["appliances"] = [{"type": "hood", "width": 500, "height": 150, "depth": -10, "module": "В1"}]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    with pytest.raises(OrderError, match="В1.*положительн"):
        api.parts_from_project(base_door_project, home)


def test_hood_nominal_width_uses_niche_from_workshop(home, base_door_project):
    upper_project(base_door_project, "wall_hood")
    project = json.loads(base_door_project.read_text())
    project["appliances"] = [{"type": "hood", "width": 600, "module": "В1"}]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    result = api.parts_from_project(base_door_project, home)
    rail = next(p for p in result.modules[0].parts if p.name == "передняя планка дна вытяжки")
    assert rail.width == 40
