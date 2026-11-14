import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import KitchenError, OrderError, WorkshopError

from tests.conftest import FIELDS_NOT_IN_GOLDEN

GOLDEN = Path(__file__).resolve().parent / "golden" / "base_door_600.json"


def test_golden_base_door_600(home, base_door_project):
    """Эталон из ТЗ: размеры, количества, кромка 3.710/3.890/4.3 м, фасады 0.4253 м² — до миллиметра."""
    result = api.parts_from_project(base_door_project, home)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    # площадь есть только у фасадов: у остальных деталей area_m2 пуст и в эталон не входит
    assert result.model_dump(exclude=FIELDS_NOT_IN_GOLDEN, exclude_none=True) == expected


def test_mock_flag_on_result(home, base_door_project):
    result = api.parts_from_project(base_door_project, home)
    assert result.mock is True
    assert {"construction.yaml", "materials.yaml", "hardware.yaml"} <= set(result.mock_files)


def test_project_file_not_found(home, base_door_project):
    with pytest.raises(OrderError) as e:
        api.parts_from_project(home / "nope.json", home)
    assert "не найден" in e.value.problems[0]


def test_project_file_not_json(home, base_door_project):
    p = home / "broken.json"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "не JSON" in e.value.problems[0]


def test_unknown_template_lists_known(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["template"] = "base_nope"
    p = home / "bad_template.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    text = "\n".join(e.value.problems)
    assert "base_nope" in text and "base_door" in text


def test_duplicate_module_labels_break_part_ids(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    modules = project["walls"][0]["rows"][0]["modules"]
    modules.append(dict(modules[0]))
    p = home / "dup.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "не уникальна" in e.value.problems[0] and "Н1-бок-Л" in e.value.problems[0]


def test_base_door_rejected_in_upper_row(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["kind"] = "upper"
    p = home / "upper.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "нижнем ряду" in e.value.problems[0]


def test_unknown_body_material(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["materials"]["body"] = "ldsp_nope"
    p = home / "bad_body.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "ldsp_nope" in e.value.problems[0] and "materials.yaml" in e.value.problems[0]


def test_unknown_project_schema_key(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["materialz"] = {}
    p = home / "bad_key.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "materialz" in e.value.problems[0] and "неизвестный ключ" in e.value.problems[0]


def test_non_integer_module_width(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["width"] = 600.5
    p = home / "frac.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "600.5" in e.value.problems[0] and "целое" in e.value.problems[0]


# --- варианты проекта ----------------------------------------------------------


def _project(home, name="variant.json", *, width=600, body=None, shelves=1):
    project = json.loads((home / "project.json").read_text(encoding="utf-8"))
    module = project["walls"][0]["rows"][0]["modules"][0]
    module["width"] = width
    module["shelves"] = shelves
    if body:
        project["materials"]["body"] = body
    p = home / name
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return p


def _parts(result):
    return {p.id: p for m in result.modules for p in m.parts}


def _edit_workshop(home, fname, old, new):
    p = home / "workshop" / fname
    text = p.read_text(encoding="utf-8")
    assert old in text
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_w400_front_edges_follow_the_front_not_the_long_side(home, base_door_project):
    """W400: дно и полка длиннее по глубине; кромка — на передней (короткой) стороне 368 и 366."""
    r = api.parts_from_project(_project(home, width=400), home)
    parts = _parts(r)
    bottom, shelf = parts["Н1-дно"], parts["Н1-полка"]
    assert (bottom.length, bottom.width) == (560, 368)
    assert bottom.edges == {"L1": "", "L2": "", "W1": "", "W2": "edge_08_white"}
    assert (shelf.length, shelf.width) == (540, 366)
    assert shelf.edges == {"L1": "", "L2": "", "W1": "", "W2": "edge_08_white"}
    # боковины 2×720 + дно 368 + царги 2×368 + полка 366 = 2910 мм; 6 сторон × 30 мм припуска = 180;
    # 3090 + 10 % = 3399 -> вверх до 0.1 м = 3.4
    assert r.edges["edge_08_white"].model_dump() == {
        "clean_m": 2.91,
        "with_allowance_m": 3.09,
        "purchase_m": 3.4,
    }
    # царги лежат плашмя между боковинами у верха корпуса: z = H - толщина
    for rail in (parts["Н1-царга-1"], parts["Н1-царга-2"]):
        assert rail.position.z == 704 and rail.orientation.thickness == "z"
    assert (parts["Н1-царга-1"].position.y, parts["Н1-царга-2"].position.y) == (460, 0)


def test_one_door_for_narrow_module(home, base_door_project):
    r = api.parts_from_project(_project(home, width=400), home)
    doors = [p for p in _parts(r).values() if p.name == "фасад"]
    assert [(d.id, d.width, d.length) for d in doors] == [("Н1-фасад", 396, 716)]
    assert r.hardware["hinge_71B3550"] == 2  # 716 мм <= 900: две петли на фасад
    assert r.hardware["handle_profile"] == 1


def test_w900_gets_six_legs_and_two_doors(home, base_door_project):
    r = api.parts_from_project(_project(home, width=900), home)
    assert r.hardware["leg_100"] == 6
    assert [p.width for p in _parts(r).values() if p.name == "фасад"] == [447, 447]


def test_facade_area_per_part_and_total_is_their_sum(home, base_door_project):
    r = api.parts_from_project(_project(home, width=400), home)
    door = _parts(r)["Н1-фасад"]
    assert door.area_m2 == 0.283536  # 716 × 396 мм
    assert [p.area_m2 for p in _parts(r).values() if p.name != "фасад"] == [None] * 7
    assert r.facade_area == 0.2835
    r600 = api.parts_from_project(_project(home, name="w600.json"), home)
    areas = [p.area_m2 for p in _parts(r600).values() if p.name == "фасад"]
    assert areas == [0.212652, 0.212652] and r600.facade_area == 0.4253


def test_textured_body_keeps_grain_axis_and_oak_edge(home, base_door_project):
    r = api.parts_from_project(_project(home, width=400, body="ldsp_16_oak"), home)
    parts = _parts(r)
    bottom, shelf = parts["Н1-дно"], parts["Н1-полка"]
    # длина вдоль текстуры, без поворота, даже когда она короче
    assert (bottom.length, bottom.width, bottom.orientation.length) == (368, 560, "x")
    assert (shelf.length, shelf.width, shelf.orientation.length) == (366, 540, "x")
    carcass = [p for p in parts.values() if p.material == "ldsp_16_oak"]
    assert len(carcass) == 6 and all(p.texture for p in carcass)
    assert all(p.edges["L1"] == "edge_08_oak" for p in carcass)
    assert set(r.edges) == {"edge_08_oak"}


@pytest.mark.parametrize("width", [601, 501])
def test_odd_width_is_a_russian_error_naming_module_and_width(home, base_door_project, width):
    with pytest.raises(OrderError) as e:
        api.parts_from_project(_project(home, width=width), home)
    text = e.value.problems[0]
    assert "Н1" in text and str(width) in text and "нацело" in text and "чётную" in text


def test_non_strict_int_shelves_rejected(home, base_door_project):
    p = _project(home)
    project = json.loads(p.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["shelves"] = True
    p.write_text(json.dumps(project), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "shelves" in e.value.problems[0]


# --- числа и правила мастерской читаются, а не угадываются ---------------------


def test_fractional_millimetres_rejected_at_load(home, base_door_project):
    _edit_workshop(home, "construction.yaml", "отступ_от_боковин: 1,", "отступ_от_боковин: 1.5,")
    with pytest.raises(WorkshopError) as e:
        api.parts_from_project(base_door_project, home)
    text = "\n".join(e.value.problems)
    assert "отступ_от_боковин" in text and "целое" in text


@pytest.mark.parametrize(
    ("old", "new", "bad"),
    [
        ("заглушки: по одной на конфирмат", "заглушки: каждый второй", "заглушки"),
        ("ответные_планки: по одной на петлю", "ответные_планки: на каждую третью", "ответные_планки"),
        ("ручки: по одной на фасад", "ручки: две на дверь", "ручки"),
        ("царги: одна длинная сторона", "царги: как получится", "царги"),
        ("корпус: передняя видимая кромка, ПВХ 0.8", "корпус: вся деталь", "корпус"),
        ("полки: передняя кромка", "полки: сверху", "полки"),
    ],
)
def test_unknown_prose_rule_is_a_load_error(home, base_door_project, old, new, bad):
    _edit_workshop(home, "construction.yaml", old, new)
    with pytest.raises(WorkshopError) as e:
        api.parts_from_project(base_door_project, home)
    text = "\n".join(e.value.problems)
    assert "construction.yaml" in text and bad in text


def test_ratio_phrases_drive_hardware_counts(home, base_door_project):
    base = api.parts_from_project(base_door_project, home).hardware
    _edit_workshop(
        home, "construction.yaml", "заглушки: по одной на конфирмат", "заглушки: по 2 на конфирмат"
    )
    _edit_workshop(
        home, "construction.yaml", "ответные_планки: по одной на петлю", "ответные_планки: по 2 на петлю"
    )
    _edit_workshop(home, "construction.yaml", "ручки: по одной на фасад", "ручки: по 2 на фасад")
    hw = api.parts_from_project(base_door_project, home).hardware
    assert hw["confirmat_cap_white"] == 2 * base["confirmat_cap_white"]
    assert hw["plate_173L6100"] == 2 * base["plate_173L6100"]
    assert hw["handle_profile"] == 2 * base["handle_profile"]


def test_shelf_edge_rule_changes_which_side_is_edged(home, base_door_project):
    """«одна длинная сторона» у полки W400 кромит длинную сторону 540, а не переднюю 366."""
    _edit_workshop(home, "construction.yaml", "полки: передняя кромка", "полки: одна длинная сторона")
    r = api.parts_from_project(_project(home, width=400), home)
    shelf = _parts(r)["Н1-полка"]
    assert shelf.edges == {"L1": "edge_08_white", "L2": "", "W1": "", "W2": ""}
    assert r.edges["edge_08_white"].clean_m == 3.084  # 2910 - 366 + 540


def test_odd_width_error_is_a_kitchen_error(home, base_door_project):
    with pytest.raises(KitchenError):
        api.parts_from_project(_project(home, width=601), home)
