"""Эталоны угловых шаблонов. ПРОВЕРИТЬ МАСТЕРУ."""

import json

from kitchen import api


def corner_project(path, template, width, kind):
    project = json.loads(path.read_text(encoding="utf-8"))
    if not any(wall["label"] == "Б" for wall in project["walls"]):
        project["walls"].append({"label": "Б", "length": 1800, "rows": []})
    row = project["walls"][0]["rows"][0]
    row["kind"] = kind
    row["modules"] = [
        {
            "label": "Н1" if kind == "lower" else "В1",
            "template": template,
            "width": width,
            "corner": {"adjacent_wall": "Б"},
        }
    ]
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


def test_base_corner_has_carousel_and_one_facade(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: заглушка закрывает глухую часть, карусель вместо полки.
    corner_project(base_door_project, "base_corner", 1050, "lower")
    result = api.parts_from_project(base_door_project, home)
    parts = result.modules[0].parts
    assert [p.width for p in parts if p.name == "фасад"] == [450]
    assert len([p for p in parts if p.name == "заглушка"]) == 1
    assert not any(p.name == "полка" for p in parts)
    assert result.hardware["corner_carousel"] == 1


def test_wall_corner_odd_width_uses_one_door(home, base_door_project):
    # ПРОВЕРИТЬ МАСТЕРУ: прямой корпус, одна дверь и заглушка из ЛДСП.
    corner_project(base_door_project, "wall_corner", 601, "upper")
    result = api.parts_from_project(base_door_project, home)
    parts = result.modules[0].parts
    assert len([p for p in parts if p.name == "фасад"]) == 1
    assert len([p for p in parts if p.name == "заглушка"]) == 1
    assert result.hardware["hanger_camar807"] == 2
    assert "corner_carousel" not in result.hardware


def test_corner_filler_uses_workshop_material_and_edge_rule(home, base_door_project):
    path = home / "workshop/construction.yaml"
    path.write_text(path.read_text().replace("кромить_заглушку: true", "кромить_заглушку: false"))
    corner_project(base_door_project, "base_corner", 1050, "lower")
    result = api.parts_from_project(base_door_project, home)
    part = next(p for p in result.modules[0].parts if p.name == "заглушка")
    assert part.material == "ldsp_16_white"
    assert all(not edge for edge in part.edges.values())


def test_corner_rejects_impossible_geometry(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    corner_project(base_door_project, "base_corner", 1050, "lower")
    project = json.loads(base_door_project.read_text())
    project["walls"][0]["rows"][0]["modules"][0]["depth"] = 10
    base_door_project.write_text(json.dumps(project))
    with pytest.raises(OrderError, match="габариты"):
        api.parts_from_project(base_door_project, home)


def test_corner_manual_golden(home, base_door_project):
    from pathlib import Path

    # ПРОВЕРИТЬ МАСТЕРУ. Низ: дно/царги 1018, заглушка 594, фасад 450;
    # кромка 7114 + 9×30 = 7384 мм, закупка 8,2 м. Карусель одна.
    # Верх: дно/крыша 568×600, полка 566×580, заглушка 150, фасад 444;
    # кромка 4874 + 9×30 = 5144 мм, закупка 5,7 м.
    for template, width, kind in (("base_corner", 1050, "lower"), ("wall_corner", 600, "upper")):
        corner_project(base_door_project, template, width, kind)
        result = api.parts_from_project(base_door_project, home)
        expected = json.loads((Path(__file__).parent / f"golden/{template}_{width}.json").read_text())
        assert result.model_dump(exclude={"mock", "mock_files"}, exclude_none=True) == expected


def test_corner_uses_specified_filler_material(home, base_door_project):
    path = home / "workshop/construction.yaml"
    path.write_text(
        path.read_text().replace("материал_заглушки: ldsp_16_white", "материал_заглушки: ldsp_16_oak")
    )
    corner_project(base_door_project, "base_corner", 1050, "lower")
    result = api.parts_from_project(base_door_project, home)
    part = next(p for p in result.modules[0].parts if p.name == "заглушка")
    assert part.material == "ldsp_16_oak"
    assert part.thickness == 16
    assert set(part.edges.values()) == {"edge_08_oak"}


def test_wall_corner_rejects_carousel(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    corner_project(base_door_project, "wall_corner", 600, "upper")
    project = json.loads(base_door_project.read_text())
    project["walls"][0]["rows"][0]["modules"][0]["contents"] = {"inserts": ["corner_carousel"]}
    base_door_project.write_text(json.dumps(project))
    with pytest.raises(OrderError, match="наполнение"):
        api.parts_from_project(base_door_project, home)


def test_corner_accepts_matching_contents(home, base_door_project):
    for template, width, kind, contents in (
        ("base_corner", 1050, "lower", {"doors": 1, "shelves": 0, "inserts": ["corner_carousel"]}),
        ("wall_corner", 600, "upper", {"doors": 1, "shelves": 1}),
    ):
        corner_project(base_door_project, template, width, kind)
        project = json.loads(base_door_project.read_text())
        project["walls"][0]["rows"][0]["modules"][0]["contents"] = contents
        base_door_project.write_text(json.dumps(project, ensure_ascii=False))
        result = api.parts_from_project(base_door_project, home)
        assert len([p for p in result.modules[0].parts if p.name == "фасад"]) == 1


def test_corner_requires_adjacent_wall(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    corner_project(base_door_project, "base_corner", 1050, "lower")
    project = json.loads(base_door_project.read_text())
    del project["walls"][0]["rows"][0]["modules"][0]["corner"]
    base_door_project.write_text(json.dumps(project))
    with pytest.raises(OrderError, match="соседн"):
        api.parts_from_project(base_door_project, home)


def test_workshop_rejects_negative_corner_values(home):
    path = home / "workshop/construction.yaml"
    path.write_text(path.read_text().replace("саморезов_заглушки: 4", "саморезов_заглушки: -100"))
    result = api.check_workshop(home)
    assert any("саморезов_заглушки" in error for error in result.errors)


def test_corner_rejects_missing_or_same_adjacent_wall(home, base_door_project):
    import pytest
    from kitchen.errors import OrderError

    for neighbor in ("нет", "A"):
        corner_project(base_door_project, "base_corner", 1050, "lower")
        project = json.loads(base_door_project.read_text())
        project["walls"][0]["rows"][0]["modules"][0]["corner"]["adjacent_wall"] = neighbor
        base_door_project.write_text(json.dumps(project))
        with pytest.raises(OrderError, match="соседн"):
            api.parts_from_project(base_door_project, home)
