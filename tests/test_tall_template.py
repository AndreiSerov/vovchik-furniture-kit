"""Пенал с духовкой и СВЧ. ПРОВЕРИТЬ МАСТЕРУ."""

import json

from kitchen import api

from tests.conftest import NEW_FIELDS


def test_tall_oven_contains_two_niches_and_drawer(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"] = [{"label": "П1", "template": "tall_oven", "width": 600}]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(base_door_project, home)
    parts = result.modules[0].parts
    assert len([p for p in parts if p.name == "перегородка ниши"]) == 2
    assert len([p for p in parts if p.name == "дно ящика"]) == 1
    assert result.hardware["tandembox_M_500"] == 1
    assert result.hardware["leg_100"] == 4


def test_tall_oven_manual_golden(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: 280 фасад ящика, ниши 590 и 380, перегородки z=282/888/1284.
    # Верхняя дверь 798×596; дно ящика 493×490; задняя стенка 481×68.
    # Кромка 2×2100 + 5×568 + 566 = 7606 мм; 8 сторон + 240 мм = 7846.
    # Конфирматы 8+12, петли 2, саморезы 36+8+8, опоры 4, ручки 2.
    from pathlib import Path

    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"] = [{"label": "П1", "template": "tall_oven", "width": 600}]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(base_door_project, home)
    expected = json.loads((Path(__file__).parent / "golden/tall_oven_600.json").read_text(encoding="utf-8"))
    assert result.model_dump(exclude=NEW_FIELDS, exclude_none=True) == expected


def test_tall_oven_project_microwave_dimensions_change_niche(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"] = [{"label": "П1", "template": "tall_oven", "width": 600}]
    project["appliances"] = [{"type": "microwave", "width": 600, "height": 420, "module": "П1"}]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(base_door_project, home)
    by_id = {p.id: p for p in result.modules[0].parts}
    assert by_id["П1-перег-2"].position.z == 1324
    assert by_id["П1-фасад"].position.z == 1340


def test_tall_oven_rejects_impossible_drawer_or_niche(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"] = [{"label": "П1", "template": "tall_oven", "width": 500}]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError, match="нишу техники"):
        api.parts_from_project(base_door_project, home)
    project["walls"][0]["rows"][0]["modules"][0]["width"] = 600
    project["walls"][0]["rows"][0]["modules"][0]["depth"] = 500
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError, match="нишу техники"):
        api.parts_from_project(base_door_project, home)


def test_tall_oven_invalid_appliance_reference_is_rejected(home, base_door_project):
    import pytest
    from kitchen.errors import WorkshopError

    construction = home / "workshop/construction.yaml"
    construction.write_text(
        construction.read_text(encoding="utf-8").replace("духовка: oven_60", "духовка: microwave"),
        encoding="utf-8",
    )
    with pytest.raises(WorkshopError, match="правила_пенала.духовка"):
        api.parts_from_project(base_door_project, home)


def test_tall_upper_row_uses_module_facade_override(home, base_door_project):
    path = home / "workshop/materials.yaml"
    text = path.read_text()
    text = text.replace(
        "  # толщина — ДОБАВЛЕНО (МОК): МДФ 19 мм",
        '  - {id: other_facade, название: "Фасад", толщина: 16, цена_за_м2: 100, заказ_на_стороне: true, срок_раб_дней: 10, магазин: Фасад-Про}\n  # толщина — ДОБАВЛЕНО (МОК): МДФ 19 мм',
    )
    path.write_text(text)
    project = json.loads(base_door_project.read_text())
    row = project["walls"][0]["rows"][0]
    row["kind"] = "upper"
    row["modules"] = [
        {"label": "П1", "template": "tall_oven", "width": 600, "materials": {"facades": "other_facade"}}
    ]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    result = api.parts_from_project(base_door_project, home)
    facades = [part for part in result.modules[0].parts if part.name == "фасад"]
    assert all(part.material == "other_facade" and part.thickness == 16 for part in facades)


def test_tall_odd_niche_height_places_shelf_on_whole_mm(home, base_door_project):
    project = json.loads(base_door_project.read_text())
    project["walls"][0]["rows"][0]["modules"] = [{"label": "П1", "template": "tall_oven", "width": 600}]
    project["appliances"] = [{"type": "microwave", "module": "П1", "width": 600, "height": 381}]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    result = api.parts_from_project(base_door_project, home)
    shelf = next(part for part in result.modules[0].parts if part.name == "полка")
    assert shelf.position.z == 1700
