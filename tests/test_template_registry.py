"""Реестр шаблонов: габариты и техника проекта берутся из реестра, а не из каскада имён."""

import json
from pathlib import Path

from kitchen import parts, project, workshop

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def petrov_project():
    return project.parse_project(json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8")))


def find(proj, label):
    for wall in proj.walls:
        for row in wall.rows:
            for module in row.modules:
                if module.label == label:
                    return row, module
    raise KeyError(label)


def test_wall_corner_depth_is_upper_depth(home):
    # Верхний угловой — той же глубины, что обычные верхние (тикет 50): нет отдельного ключа мастерской.
    ws = workshop.load_workshop(home)
    row, module = find(petrov_project(), "В4")
    assert parts.module_dims(module, row.kind, ws).depth == 320
    row, module = find(petrov_project(), "В2")
    assert parts.module_dims(module, row.kind, ws).depth == 320


def test_registry_build_uses_project_appliances(home):
    # Пенал с нишей духовки высотой 700 вместо 590: ниша строится по технике проекта через реестр.
    ws = workshop.load_workshop(home)
    proj = petrov_project()
    proj.appliances[0].niche_height = 700
    row, module = find(proj, "П1")
    dims = parts.module_dims(module, row.kind, ws)
    ctx = parts.BuildContext(appliances=proj.appliances, row=row)
    via_registry = parts.TEMPLATES["tall_oven"].build(module, dims, proj.materials, ws, ctx)
    via_module = parts.build_module(module, row, proj.materials, ws, proj.appliances)
    catalog = parts.TEMPLATES["tall_oven"].build(
        module, dims, proj.materials, ws, parts.BuildContext(appliances=[], row=row)
    )

    # отверстия присадки ставит `build_module` после шаблона (тикет 19): сравниваются детали без них
    def dump(out):
        return [p.model_dump(exclude={"holes", "base_edges"}) for p in out.parts]

    assert dump(via_registry) == dump(via_module)
    assert dump(via_registry) != dump(catalog)
