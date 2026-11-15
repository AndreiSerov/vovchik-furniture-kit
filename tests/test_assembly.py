"""Тикет 22, срез (а): ключи `сборка` и `ставится`, фурнитура и состав модуля, шаги сборки из модели."""

import json
import re
from pathlib import Path

import pytest
import yaml
from kitchen import api
from kitchen.errors import KitchenError

from tests.conftest import BASE_DOOR_PROJECT

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
STAGES = ["корпус", "задняя_стенка", "полки", "ящики", "фасады", "ручки", "опоры"]


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def edit(home, name, old, new):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def set_stages(home, stages):
    path = home / "workshop" / "construction.yaml"
    text = re.sub(
        r"^сборка:.*\n",
        f"сборка: [{', '.join(stages)}]\n",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    path.write_text(text, encoding="utf-8")


def module_of(result, label):
    return next(m for m in result.modules if m.label == label)


def write_project(home, project):
    path = home / "project.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def test_module_hardware_sums_to_project_hardware(home):
    parts = api.parts_from_project(FIXTURES / "petrov_project.json", home)
    total: dict[str, int] = {}
    for module in parts.modules:
        for item, n in module.hardware.items():
            total[item] = total.get(item, 0) + n
    assert total == parts.hardware
    assert (total["confirmat_7x50"], total["hinge_71B3550"], total["screw_3_5x16"]) == (102, 20, 330)


def test_base_door_steps_follow_construction_order_and_confirmat_count(base_door_project, home):
    result = api.assembly_from_project(base_door_project, home)
    (module,) = result.modules
    hardware = api.parts_from_project(base_door_project, home).modules[0].hardware
    confirmats = [int(n) for n in re.findall(r"— (\d+) конфирмат", "\n".join(module.steps))]
    assert sum(confirmats) == hardware["confirmat_7x50"]
    assert module.steps[0].startswith("Н1-дно к Н1-бок-Л, Н1-бок-П — ")
    assert module.steps[-1].startswith("Петли Петля Blum") and module.steps[-1].endswith(
        "— 4 шт, фасады: Н1-фасад-Л, Н1-фасад-П"
    )
    kinds = [
        next(
            kind
            for kind, key in (
                ("корпус", " к "),
                ("задняя_стенка", "Задняя стенка"),
                ("полки", "Полкодержатели"),
                ("фасады", "Петли"),
            )
            if key in step
        )
        for step in module.steps
    ]
    assert kinds == ["корпус"] * kinds.count("корпус") + ["задняя_стенка", "полки", "фасады"]
    assert module.steps[-3] == "Задняя стенка Н1-зад — 18 саморезов"
    assert module.steps[-2] == "Полкодержатели — 4 шт, полки: Н1-полка"


def test_swapped_stage_order_in_workshop_copy_reorders_steps(base_door_project, home):
    before = api.assembly_from_project(base_door_project, home).modules[0].steps
    set_stages(home, ["фасады", "корпус", "задняя_стенка", "полки", "ящики", "ручки", "опоры"])
    after = api.assembly_from_project(base_door_project, home).modules[0].steps
    assert after[0] == before[-1] and after[1:] == before[:-1]


@pytest.mark.parametrize(
    ("stages", "needle"),
    [
        ([*STAGES, "кухня"], "кухня"),
        (["корпус", *STAGES], "корпус"),
        (STAGES[:-1], "опоры"),
    ],
)
def test_bad_stage_list_is_load_error_naming_the_stage(base_door_project, home, stages, needle):
    set_stages(home, stages)
    with pytest.raises(KitchenError) as e:
        api.assembly_from_project(base_door_project, home)
    text = "\n".join(e.value.problems)
    assert "сборка" in text and needle in text


def test_missing_assembly_key_is_load_error(base_door_project, home):
    path = home / "workshop" / "construction.yaml"
    path.write_text(
        re.sub(r"^сборка:.*\n", "", path.read_text(encoding="utf-8"), flags=re.MULTILINE), encoding="utf-8"
    )
    with pytest.raises(KitchenError) as e:
        api.assembly_from_project(base_door_project, home)
    assert "сборка" in "\n".join(e.value.problems)


def test_repository_workshop_names_the_mock_stage_order_and_site_hardware(repo):
    data = yaml.safe_load((repo / "workshop" / "construction.yaml").read_text(encoding="utf-8"))
    assert data["сборка"] == STAGES
    hardware = yaml.safe_load((repo / "workshop" / "hardware.yaml").read_text(encoding="utf-8"))
    on_site = {i["id"] for i in hardware["позиции"] if i.get("ставится") == "объект"}
    assert on_site == {"leg_100", "handle_profile", "hanger_camar807"}


def test_unknown_install_place_is_load_error(base_door_project, home):
    edit(home, "hardware.yaml", "{id: leg_100, ставится: объект,", "{id: leg_100, ставится: склад,")
    with pytest.raises(KitchenError) as e:
        api.assembly_from_project(base_door_project, home)
    text = "\n".join(e.value.problems)
    assert "ставится" in text and "склад" in text


def test_site_hardware_is_not_in_workshop_sheet_and_handles_appear_when_marked_workshop(
    base_door_project, home
):
    plain = api.assembly_from_project(base_door_project, home).modules[0]
    ids = [h.id for h in plain.hardware]
    assert "hinge_71B3550" in ids and not {"handle_profile", "leg_100", "hanger_camar807"} & set(ids)
    edit(
        home, "hardware.yaml", "{id: handle_profile, ставится: объект,", "{id: handle_profile, ставится: цех,"
    )
    marked = api.assembly_from_project(base_door_project, home).modules[0]
    handle = next(h for h in marked.hardware if h.id == "handle_profile")
    assert handle.quantity == 2 and handle.unit == "шт"  # две двери — две ручки
    assert "Ручки Ручка-профиль торцевая — 2 шт, фасады: Н1-фасад-Л, Н1-фасад-П" in marked.steps
    assert [h.id for h in marked.hardware if h.id != "handle_profile"] == ids  # порядок hardware.yaml


def test_hardware_line_has_catalog_name_and_count(base_door_project, home):
    module = api.assembly_from_project(base_door_project, home).modules[0]
    hinges = next(h for h in module.hardware if h.id == "hinge_71B3550")
    assert hinges.name == "Петля Blum Clip Top Blumotion 110° накладная" and hinges.quantity == 4


CASES = {
    "tall_oven": ("lower", {"label": "П1", "template": "tall_oven", "width": 600}),
    "base_door": ("lower", {"label": "Н1", "template": "base_door", "width": 600, "shelves": 1}),
    "base_corner": (
        "lower",
        {"label": "Н1", "template": "base_corner", "width": 1050, "corner": {"adjacent_wall": "Б"}},
    ),
    "base_sink": ("lower", {"label": "Н1", "template": "base_sink", "width": 600}),
    "base_bottle": ("lower", {"label": "Н1", "template": "base_bottle", "width": 150}),
    "base_drawers": ("lower", {"label": "Н1", "template": "base_drawers", "width": 600}),
    "wall_door": ("upper", {"label": "В1", "template": "wall_door", "width": 600}),
    "wall_lift": ("upper", {"label": "В1", "template": "wall_lift", "width": 600}),
    "wall_dryer": ("upper", {"label": "В1", "template": "wall_dryer", "width": 750}),
    "wall_hood": ("upper", {"label": "В1", "template": "wall_hood", "width": 600}),
    "wall_corner": (
        "upper",
        {"label": "В1", "template": "wall_corner", "width": 600, "corner": {"adjacent_wall": "Б"}},
    ),
}
APPLIANCES = {
    "wall_hood": [
        {"type": "hood", "width": 598, "height": 190, "depth": 290, "niche_width": 560,
         "niche_height": 180, "niche_depth": 280, "module": "В1"},
    ],
    "tall_oven": [
        {"type": "oven", "width": 595, "height": 595, "depth": 567, "niche_width": 560,
         "niche_height": 590, "niche_depth": 550, "module": "П1"},
        {"type": "microwave", "width": 594, "height": 390, "depth": 360, "niche_width": 560,
         "niche_height": 380, "niche_depth": 350, "module": "П1"},
    ],
}  # fmt: skip


def one_module_project(template):
    row, module = CASES[template]
    project = json.loads(json.dumps(BASE_DOOR_PROJECT))
    project["walls"][0]["rows"] = [{"kind": row, "modules": [module]}]
    project["walls"].append({"label": "Б", "length": 3000, "rows": []})
    if template in APPLIANCES:
        project["appliances"] = APPLIANCES[template]
    return project


def test_cases_cover_every_template_with_a_carcass(home):
    """Шаблоны движка — из текста ошибки неизвестного шаблона; без корпуса только `dishwasher_panel`."""
    path = write_project(home, one_module_project("base_door"))
    project = json.loads(path.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["template"] = "нет_такого"
    with pytest.raises(KitchenError) as e:
        api.assembly_from_project(write_project(home, project), home)
    known = set(re.search(r"известные: ([^)]*)\)", "\n".join(e.value.problems)).group(1).split(", "))
    assert known - {"dishwasher_panel"} == set(CASES) and len(CASES) == 11


@pytest.mark.parametrize("template", sorted(CASES))
def test_every_carcass_template_gives_a_block_with_a_step(home, template):
    path = write_project(home, one_module_project(template))
    (block,) = api.assembly_from_project(path, home).modules
    assert block.carcass and block.steps, template


def test_dishwasher_panel_has_no_carcass_and_no_steps(home):
    petrov(home)
    block = module_of(api.assembly_from_order(ORDER, home), "Н6")
    assert not block.carcass and block.steps == []
    assert block.title == "Н6 — фасад посудомоечной машины, 450 мм"
    assert block.note == "корпуса нет, фасад Н6-фасад крепится к посудомойке на объекте"
    assert [p.mark for p in block.parts] == ["Н6-фасад"]


def test_module_titles_and_part_lines_use_template_title_and_materials(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    assert [m.label for m in result.modules] == [
        "П1", "Н1", "Н2", "Н3", "Н4", "В1", "В2", "В3", "В4", "Н5", "Н6", "В5", "В6",
    ]  # fmt: skip
    p1 = module_of(result, "П1")
    assert p1.title == "П1 — пенал, 600×2100×560 мм"
    assert len(p1.parts) == 13
    side = next(p for p in p1.parts if p.mark == "П1-бок-Л")
    assert (side.name, side.size, side.material) == (
        "боковина",
        "2100×560×16 мм",
        "ЛДСП Egger 16 мм, белый W1000 ST9",
    )
    assert p1.appliances == ["духовой шкаф", "микроволновка"]
    assert module_of(result, "Н2").appliances == []


def test_drawer_and_hinge_steps_name_catalog_items_and_parts(home):
    petrov(home)
    result = api.assembly_from_order(ORDER, home)
    drawers = module_of(result, "Н1").steps
    assert (
        "Направляющие Tandembox antaro M 500, комплект — 3 компл., "
        "ящики: Н1-дно-ящ-1, Н1-дно-ящ-2, Н1-дно-ящ-3" in drawers
    )
    lift = module_of(result, "В5").steps
    assert lift[-1] == "Подъёмник Blum Aventos HK-S — 1 шт, фасады: В5-фасад"
    assert not any(
        "Задняя стенка" in s for s in module_of(result, "Н5").steps
    )  # без задней стенки строки нет
    for module in result.modules:
        assert all(not s.startswith(("Ручки", "Опоры")) for s in module.steps)


def test_other_appliance_is_named_in_assembly_sheet(home):
    project = json.loads(json.dumps(BASE_DOOR_PROJECT))
    project["appliances"] = [{"type": "other", "width": 500, "module": "Н1"}]
    (block,) = api.assembly_from_project(write_project(home, project), home).modules
    assert block.appliances == ["другая техника"]


def mark_workshop(home, item):
    edit(home, "hardware.yaml", f"{{id: {item}, ставится: объект,", f"{{id: {item}, ставится: цех,")


def test_legs_and_handles_marked_workshop_get_steps_only_where_the_module_has_them(home):
    petrov(home)
    mark_workshop(home, "leg_100")
    mark_workshop(home, "handle_profile")
    result = api.assembly_from_order(ORDER, home)
    n1 = module_of(result, "Н1")
    legs = next(h for h in n1.hardware if h.id == "leg_100")
    assert f"Опоры {legs.name} — {legs.quantity} шт" in n1.steps
    for m in result.modules:
        assert not any(" — 0 шт" in s for s in m.steps), m.label
        has_legs = any(h.id == "leg_100" for h in m.hardware)
        assert has_legs == any(s.startswith("Опоры") for s in m.steps), m.label
        has_handles = any(h.id == "handle_profile" for h in m.hardware)
        assert has_handles == any(s.startswith("Ручки") for s in m.steps), m.label
