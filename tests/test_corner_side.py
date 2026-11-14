"""Сторона глухой части углового модуля. ПРОВЕРИТЬ МАСТЕРУ."""

import json

from kitchen import api

from tests.test_corner_templates import corner_project


def add_neighbour(path, kind, *, before):
    project = json.loads(path.read_text(encoding="utf-8"))
    modules = project["walls"][0]["rows"][0]["modules"]
    template = "base_door" if kind == "lower" else "wall_door"
    other = {"label": "Н9" if kind == "lower" else "В9", "template": template, "width": 600}
    modules.insert(0 if before else len(modules), other)
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")


def x_of(result, label, name):
    parts = next(m.parts for m in result.modules if m.module == label)
    return next(p.position.x for p in parts if p.name == name)


def test_corner_last_in_row_has_blind_part_at_the_corner(home, base_door_project):
    # ТЗ: Н4 последний на стене А, стена Б идёт от угла: глухая часть справа, дверь со стороны прохода.
    # Низ: 2 + фасад 450 + 2 + заглушка 594 + 2 = 1050. Верх: 2 + 444 + 2 + 150 + 2 = 600.
    for template, width, kind, label, facade_x, filler_x in (
        ("base_corner", 1050, "lower", "Н1", 2, 454),
        ("wall_corner", 600, "upper", "В1", 2, 448),
    ):
        corner_project(base_door_project, template, width, kind)
        add_neighbour(base_door_project, kind, before=True)
        result = api.parts_from_project(base_door_project, home)
        assert x_of(result, label, "фасад") == facade_x
        assert x_of(result, label, "заглушка") == filler_x


def test_corner_first_in_row_is_mirrored(home, base_door_project):
    # Угол слева: заглушка слева, дверь справа от неё. Низ: 2 + 594 + 2 = 598; верх: 2 + 150 + 2 = 154.
    for template, width, kind, label, facade_x, filler_x in (
        ("base_corner", 1050, "lower", "Н1", 598, 2),
        ("wall_corner", 600, "upper", "В1", 154, 2),
    ):
        corner_project(base_door_project, template, width, kind)
        add_neighbour(base_door_project, kind, before=False)
        result = api.parts_from_project(base_door_project, home)
        assert x_of(result, label, "фасад") == facade_x
        assert x_of(result, label, "заглушка") == filler_x
