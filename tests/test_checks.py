"""Проверки проекта (1.6): на каждый код — срабатывающий и несрабатывающий проект."""

import copy
import json

import pytest
from kitchen import api
from kitchen.errors import OrderError

from tests.conftest import BASE_DOOR_PROJECT


def module(label, template, width, **extra):
    return {"label": label, "template": template, "width": width, **extra}


def make_project(path, walls):
    """Проект на материалах эталона; `walls` — [(метка, длина, [(вид, [модули])])]."""
    project = copy.deepcopy(BASE_DOOR_PROJECT)
    project["walls"] = [
        {"label": label, "length": length, "rows": [{"kind": kind, "modules": mods} for kind, mods in rows]}
        for label, length, rows in walls
    ]
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def findings(path, home, code):
    return [f for f in api.check_project(path, home).findings if f.code == code]


def lower_wall(*widths, length=2800):
    return ("A", length, [("lower", [module(f"Н{i}", "base_door", w) for i, w in enumerate(widths, 1)])])


# --- row_sum ---------------------------------------------------------------


def test_row_sum_fires_with_value_and_limit(home, tmp_path):
    path = make_project(home / "p.json", [lower_wall(600, 600)])
    [f] = findings(path, home, "row_sum")
    assert (f.level, f.subject, f.value, f.limit) == ("error", "A", 1200, 2800)
    assert "1200" in f.message and "2800" in f.message
    assert "нижний ряд" in f.message and "1600" in f.message  # разница 2800 − 1200


def test_row_sum_passes_when_equal(home):
    path = make_project(home / "p.json", [lower_wall(700, 700, 700, 700)])
    assert findings(path, home, "row_sum") == []


def test_row_sum_counts_corner_offset_from_adjacent_wall(home):
    corner = module("Н1", "base_corner", 1050, corner={"adjacent_wall": "Б"})
    wall_a = (
        "A",
        2800,
        [("lower", [corner, module("Н2", "base_door", 700), module("Н3", "base_door", 1050)])],
    )
    ok = ("Б", 1800, [("lower", [module("Н4", "base_door", 600), module("Н5", "base_door", 600)])])
    path = make_project(home / "p.json", [wall_a, ok])
    assert findings(path, home, "row_sum") == []  # 1200 + смещение 600 = 1800
    bad = ("Б", 1800, [("lower", [module("Н4", "base_door", 600), module("Н5", "base_door", 500)])])
    path = make_project(home / "p.json", [wall_a, bad])
    [f] = findings(path, home, "row_sum")
    assert (f.subject, f.value, f.limit) == ("Б", 1700, 1800)


def test_row_sum_counts_tall_in_both_rows(home):
    tall = module("П1", "tall_oven", 600)
    lower = ("lower", [tall, module("Н1", "base_door", 1100), module("Н2", "base_door", 1100)])
    upper_ok = ("upper", [module("В1", "wall_door", 1100), module("В2", "wall_door", 1100)])
    path = make_project(home / "p.json", [("A", 2800, [lower, upper_ok])])
    assert findings(path, home, "row_sum") == []
    upper_bad = ("upper", [module("В1", "wall_door", 1400), module("В2", "wall_door", 1400)])
    path = make_project(home / "p.json", [("A", 2800, [lower, upper_bad])])
    [f] = findings(path, home, "row_sum")
    assert (f.subject, f.value, f.limit) == ("A", 3400, 2800)


def test_row_sum_uses_workshop_tolerance(home):
    path = home / "workshop/construction.yaml"
    path.write_text(path.read_text().replace("допуск_стены: 0", "допуск_стены: 5"))
    project = make_project(home / "p.json", [lower_wall(700, 700, 700, 695)])
    assert findings(project, home, "row_sum") == []
    project = make_project(home / "p.json", [lower_wall(700, 700, 700, 690)])
    assert len(findings(project, home, "row_sum")) == 1


# --- drawer_depth ------------------------------------------------------------


def single(home, template, width=600, row="lower", **extra):
    """Стена ровно по ширине единственного модуля: row_sum не мешает."""
    label = "Н1" if row == "lower" else "В1"
    return make_project(home / "p.json", [("A", width, [(row, [module(label, template, width, **extra)])])])


def test_drawer_depth_fires_when_module_shallower_than_guide(home):
    path = single(home, "base_drawers", depth=400)
    [f] = findings(path, home, "drawer_depth")
    assert (f.level, f.subject, f.value, f.limit) == ("error", "Н1", 400, 500)
    assert "500" in f.message and "400" in f.message
    # ошибка глубины не прячется за вторым сообщением о расчёте того же модуля
    assert findings(path, home, "module_build") == []


def test_drawer_depth_passes_with_standard_depth(home):
    path = single(home, "base_drawers")
    assert findings(path, home, "drawer_depth") == []


def test_drawer_depth_uses_workshop_guide_length(home):
    c = home / "workshop/construction.yaml"
    c.write_text(c.read_text().replace("длина_направляющей: 500", "длина_направляющей: 600"))
    [f] = findings(single(home, "base_drawers"), home, "drawer_depth")
    assert (f.value, f.limit) == (560, 600)


def test_drawer_depth_also_checks_tall_drawer(home):
    path = single(home, "tall_oven", depth=450)
    [f] = findings(path, home, "drawer_depth")
    assert (f.value, f.limit) == (450, 500)


# --- вспомогательное для тестов техники (сами тесты — в test_checks_brief.py) ---------------


def with_appliances(path, *appliances):
    project = json.loads(path.read_text())
    project["appliances"] = list(appliances)
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


# --- part_exceeds_sheet --------------------------------------------------------


def with_body(path, material):
    project = json.loads(path.read_text())
    project["materials"]["body"] = material
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def test_sheet_field_is_sheet_minus_trim_on_both_sides(home):
    # лист 2800×2070, обрезка 10 мм с каждой стороны → поле 2780×2050
    # самая длинная деталь — задняя стенка ХДФ, W − 4
    assert findings(single(home, "base_door", 2784), home, "part_exceeds_sheet") == []  # 2780
    [f] = findings(single(home, "base_door", 2786), home, "part_exceeds_sheet")
    assert (f.level, f.subject, f.value, f.limit) == ("error", "Н1-зад", 2782, 2780)


def test_sheet_field_uses_workshop_trim(home):
    e = home / "workshop/equipment.yaml"
    e.write_text(e.read_text().replace("обрезка_по_периметру: 10", "обрезка_по_периметру: 0"))
    assert findings(single(home, "base_door", 2786), home, "part_exceeds_sheet") == []


def test_untextured_part_may_be_turned_textured_may_not(home):
    m = home / "workshop/materials.yaml"
    m.write_text(m.read_text().replace("текстура: вдоль 2800", "текстура: вдоль 2070"))
    # дно 2118 × 560: нетекстурному листу хватает поворота, текстурному (волокно вдоль 2050) — нет
    assert findings(single(home, "base_door", 2150), home, "part_exceeds_sheet") == []
    oak = with_body(single(home, "base_door", 2150), "ldsp_16_oak")
    [f, *_] = findings(oak, home, "part_exceeds_sheet")
    assert (f.value, f.limit) == (2118, 2050)


def test_facades_are_not_sheet_parts(home):
    # высота 2900: боковина и ХДФ не лезут в лист, а фасад 2896 мм — не листовая деталь
    subjects = {
        f.subject for f in findings(single(home, "base_door", height=2900), home, "part_exceeds_sheet")
    }
    assert "Н1-бок-Л" in subjects and "Н1-фасад" not in subjects and "Н1-фасад-Л" not in subjects


# --- no_back_panel / module_build -------------------------------------------------


def test_module_without_back_is_a_warning(home):
    [f] = findings(single(home, "base_sink"), home, "no_back_panel")
    assert (f.level, f.subject) == ("warning", "Н1")
    assert "без задней стенки" in f.message and "Н1" in f.message


def test_module_with_back_or_without_carcass_has_no_warning(home):
    assert findings(single(home, "base_door"), home, "no_back_panel") == []
    assert findings(single(home, "dishwasher_panel", 450), home, "no_back_panel") == []


def test_template_error_becomes_a_finding_not_a_crash(home):
    [f] = findings(single(home, "base_door", 601), home, "module_build")
    assert (f.level, f.subject) == ("error", "Н1")
    assert "нацело" in f.message
    assert findings(single(home, "base_door", 600), home, "module_build") == []


def test_all_findings_come_at_once(home):
    walls = [
        (
            "A",
            2800,
            [("lower", [module("Н1", "base_door", 601), module("Н2", "base_drawers", 600, depth=400)])],
        )
    ]
    codes = {f.code for f in api.check_project(make_project(home / "p.json", walls), home).findings}
    assert codes == {"row_sum", "module_build", "drawer_depth", "brief_missing"}


# --- импорт ------------------------------------------------------------------------


def test_import_rejects_project_with_error_and_keeps_nothing(home):
    api.order_new("2026-047-petrov", client="Петров П. П.", phone="+7 900 000-00-00", address="Москва")
    path = make_project(home / "p.json", [lower_wall(600, 600)])
    with pytest.raises(OrderError) as e:
        api.import_project("petrov", path)
    assert any("2800" in p for p in e.value.problems)
    assert not api.status("petrov").project_version


def test_import_saves_warnings_to_log(home):
    api.order_new("2026-047-petrov", client="Петров П. П.", phone="+7 900 000-00-00", address="Москва")
    result = api.import_project("petrov", single(home, "base_sink"))
    assert any("без задней стенки" in w for w in result.warnings)
    assert "без задней стенки" in api.status("petrov").text


# --- corner_wall -----------------------------------------------------------------------


def test_corner_wall_must_exist_and_differ(home):
    corner = module("Н1", "base_corner", 1050, corner={"adjacent_wall": "Х"})
    path = make_project(home / "p.json", [("A", 1050, [("lower", [corner])])])
    [f] = findings(path, home, "corner_wall")
    assert (f.level, f.subject) == ("error", "Н1")
    ok = module("Н1", "base_corner", 1050, corner={"adjacent_wall": "Б"})
    path = make_project(home / "p.json", [("A", 1050, [("lower", [ok])]), ("Б", 1800, [])])
    assert findings(path, home, "corner_wall") == []


def test_corner_offset_from_project_overrides_workshop(home):
    corner = module("Н1", "base_corner", 1050, corner={"adjacent_wall": "Б", "offset": 650})
    other = ("Б", 1800, [("lower", [module("Н2", "base_door", 600), module("Н3", "base_door", 550)])])
    path = make_project(home / "p.json", [("A", 1050, [("lower", [corner])]), other])
    assert findings(path, home, "row_sum") == []  # 1150 + 650


def test_import_rejects_when_workshop_did_not_load(home):
    api.order_new("2026-047-petrov", client="Петров П. П.", phone="+7 900 000-00-00", address="Москва")
    path = single(home, "base_door")
    c = home / "workshop/construction.yaml"
    c.write_text(c.read_text().replace("толщина_корпуса: 16", "толщина_корпуса: abc"))
    with pytest.raises(OrderError) as e:
        api.import_project("petrov", path)
    assert "Мастерская" in "\n".join(e.value.problems)
    assert not api.status("petrov").project_version
