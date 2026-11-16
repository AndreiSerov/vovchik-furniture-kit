"""Тикет 23: расстановка модулей в комнате (`api.placement_from_project`). Начало координат — начало первой
стены цепочки на полу; x вдоль неё, y от стены в комнату, z вверх; поворот стены — градусы вокруг вертикали.
Коробка модуля — корпус плюс фасад."""

import copy
import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

from tests.conftest import BASE_DOOR_PROJECT


def mod(label, template, width, **extra):
    return {"label": label, "template": template, "width": width, **extra}


def wall(label, length, lower=None, upper=None):
    rows = []
    if lower is not None:
        rows.append({"kind": "lower", "modules": lower})
    if upper is not None:
        rows.append({"kind": "upper", "modules": upper})
    return {"label": label, "length": length, "rows": rows}


def write_data(walls, **extra):
    data = copy.deepcopy(BASE_DOOR_PROJECT)
    data["walls"] = walls
    data.update(extra)
    return data


def write(home, walls, **extra):
    data = write_data(walls, **extra)
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def place(home, walls, **extra):
    return api.placement_from_project(write(home, walls, **extra), home)


def box(result, label):
    """Коробка модуля в комнате: ((x0, y0, z0), (x1, y1, z1))."""
    return next(m for m in result.modules if m.label == label).bounds()


def test_straight_kitchen_of_one_wall(home):
    # стена 1800 из трёх base_door 600: модули друг за другом от начала стены, на ножках 100,
    # глубина корпуса 560 + фасад МДФ 19
    result = place(home, [wall("A", 1800, [mod(f"Н{i}", "base_door", 600) for i in (1, 2, 3)])])
    assert [box(result, f"Н{i}") for i in (1, 2, 3)] == [
        ((0, 0, 100), (600, 579, 820)),
        ((600, 0, 100), (1200, 579, 820)),
        ((1200, 0, 100), (1800, 579, 820)),
    ]
    assert [(w.label, w.rotation) for w in result.walls] == [("A", 0)]


def offsets(result, label):
    """Угловые смещения на стене: (ряд, от, до, угловой модуль)."""
    w = next(w for w in result.walls if w.label == label)
    return [(o.row, o.start, o.end, o.module) for o in w.offsets]


def test_corner_first_in_row_of_wall_b_takes_the_end_of_wall_a(home):
    # Н4 первый в ряду стены Б с соседом A: начало Б встречается с концом A; смещение — x 1650–2250 стены A,
    # пенал П1 верхнего ряда A стоит на x 1050 (после В1), в нижнем ряду — после Н1 + Н2 = 1050
    walls = [
        wall(
            "A",
            2250,
            [mod("Н1", "base_door", 600), mod("Н2", "base_door", 450)],
            [mod("В1", "wall_door", 1050), mod("П1", "tall_oven", 600), mod("В2", "wall_door", 600)],
        ),
        wall(
            "Б",
            2250,
            [mod("Н4", "base_corner", 1050, corner={"adjacent_wall": "A"}), mod("Н5", "base_door", 1200)],
        ),
    ]
    result = place(home, walls)
    assert offsets(result, "A") == [("lower", 1650, 2250, "Н4")]
    assert offsets(result, "Б") == []
    assert next(m.x for m in result.modules if m.label == "П1") == 1050
    assert box(result, "П1") == ((1050, 0, 100), (1650, 579, 2200))
    # стена Б начинается в конце A и идёт от неё в комнату (поворот 90): глубина модуля — вдоль −x комнаты
    assert [(w.label, (w.origin.x, w.origin.y), w.rotation) for w in result.walls] == [
        ("A", (0, 0), 0),
        ("Б", (2250, 0), 90),
    ]
    assert box(result, "Н4") == ((2250 - 579, 0, 100), (2250, 1050, 820))
    assert box(result, "Н5") == ((2250 - 579, 1050, 100), (2250, 2250, 820))


PETROV = Path(__file__).resolve().parent / "fixtures" / "petrov_project.json"


def overlap(a, b):
    """Коробки пересекаются по объёму; касание — не пересечение."""
    (a0, a1), (b0, b1) = a, b
    return all(a0[i] < b1[i] and b0[i] < a1[i] for i in range(3))


def edit(home, name, old, new):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8")
    assert old in text, (name, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_petrov_modules_do_not_intersect(home):
    result = api.placement_from_project(PETROV, home)
    boxes = {m.label: m.bounds() for m in result.modules}
    assert len(boxes) == 13
    clashes = [(a, b) for a in boxes for b in boxes if a < b and overlap(boxes[a], boxes[b])]
    assert clashes == []


def test_petrov_corner_modules_stand_in_the_corner(home):
    result = api.placement_from_project(PETROV, home)
    # стена Б начинается в конце A (угол x 2800) и идёт от него в комнату
    assert [(w.label, (w.origin.x, w.origin.y), w.rotation) for w in result.walls] == [
        ("A", (0, 0), 0),
        ("Б", (2800, 0), 90),
    ]
    assert box(result, "Н4") == ((1750, 0, 100), (2800, 579, 820))
    assert box(result, "В4") == ((2200, 0, 1480), (2800, 339, 2200))
    # нижний ряд Б — после смещения нижнего 600, верхний — после смещения верхнего 336 (тикет 50): ПМ1 264,
    # затем В5 над мойкой Н5
    assert offsets(result, "Б") == [("lower", 0, 600, "Н4"), ("upper", 0, 336, "В4")]
    assert [(g.label, g.x, g.width) for g in result.gaps] == [("ПМ1", 336, 264)]
    at = {m.label: m.x for m in result.modules if m.wall == "Б"}
    assert at == {"Н5": 600, "Н6": 1350, "В5": 600, "В6": 1350}
    assert box(result, "Н5") == ((2800 - 579, 600, 100), (2800, 1350, 820))


def test_petrov_tall_spans_from_legs_to_top_of_uppers(home):
    result = api.placement_from_project(PETROV, home)
    # верх верхних = ножки.высота 100 + пенал.высота 2100; верхние выровнены по верху
    assert result.top_of_uppers == 2200
    assert box(result, "П1") == ((0, 0, 100), (600, 579, 2200))
    assert {box(result, m.label)[1][2] for m in result.modules if m.row == "upper"} == {2200}


def test_tall_height_of_workshop_moves_top_of_uppers(home):
    edit(home, "construction.yaml", "пенал: {высота: 2100", "пенал: {высота: 2300")
    result = api.placement_from_project(PETROV, home)
    assert result.top_of_uppers == 2400
    assert box(result, "В1") == ((600, 0, 1680), (1200, 339, 2400))
    assert box(result, "П1")[1][2] == 2400


def petrov(**changes):
    """Проект Петрова с правками модулей по метке: {"Н4": {"corner": None}}; `None` убирает ключ."""
    data = json.loads(PETROV.read_text(encoding="utf-8"))
    for w in data["walls"]:
        for row in w["rows"]:
            for m in row["modules"]:
                for key, value in changes.get(m["label"], {}).items():
                    if value is None:
                        m.pop(key, None)
                    else:
                        m[key] = value
    return data


def placement_error(home, data):
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.placement_from_project(path, home)
    return e.value.problems


def test_walls_without_corner_module_are_not_connected(home):
    data = petrov(Н4={"corner": None}, В4={"corner": None})
    assert placement_error(home, data) == [
        "Стены A и Б не соединены угловым модулем: расстановка строит только стены, связанные углами."
    ]


def corner(label, template, width, to):
    return mod(label, template, width, corner={"adjacent_wall": to})


def test_rows_contradict_each_other_in_one_corner(home):
    # нижний угловой последний на A (конец A к началу Б), верхний — первый на A (начало A к концу Б)
    walls = [
        wall(
            "A",
            1650,
            [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")],
            [corner("В4", "wall_corner", 600, "Б"), mod("В1", "wall_door", 1050)],
        ),
        wall("Б", 1800, [mod("Н5", "base_door", 1200)], [mod("В5", "wall_door", 1464)]),
    ]
    assert placement_error(home, write_data(walls)) == [
        "Угол между A и Б задан противоречиво: нижний ряд — конец A к началу Б, верхний — начало A к концу Б."
    ]


def test_two_lower_corner_modules_in_one_corner(home):
    walls = [
        wall("A", 1650, [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")]),
        wall("Б", 1800, [corner("Н5", "base_corner", 1050, "A"), mod("Н6", "base_door", 150)]),
    ]
    assert placement_error(home, write_data(walls)) == [
        "В углу A–Б два угловых модуля нижнего ряда: Н4 и Н5."
    ]


def test_corner_modules_at_both_ends_of_a_row_leading_to_one_wall(home):
    # тикет 54 (6): Н1 первый (начало A к концу Б), Н3 последний (конец A к началу Б) — разные углы, одна стена
    walls = [
        wall(
            "A",
            3300,
            [
                corner("Н1", "base_corner", 1050, "Б"),
                mod("Н2", "base_door", 1200),
                corner("Н3", "base_corner", 1050, "Б"),
            ],
        ),
        wall("Б", 1800, [mod("Н5", "base_door", 1200)]),
    ]
    assert placement_error(home, write_data(walls)) == [
        "Стена A: угловые модули Н1 и Н3 нижнего ряда стоят на разных концах ряда, но оба угла ведут к Б."
    ]


def test_upper_corner_modules_at_both_ends_of_a_row_name_the_upper_row(home):
    walls = [
        wall(
            "A",
            3000,
            [mod("Н1", "base_door", 3000)],
            [
                corner("В1", "wall_corner", 600, "Б"),
                mod("В2", "wall_door", 1800),
                corner("В3", "wall_corner", 600, "Б"),
            ],
        ),
        wall("Б", 1800, [mod("Н5", "base_door", 1800)]),
    ]
    assert placement_error(home, write_data(walls)) == [
        "Стена A: угловые модули В1 и В3 верхнего ряда стоят на разных концах ряда, но оба угла ведут к Б."
    ]


def test_corner_module_in_the_middle_of_a_row(home):
    walls = [
        wall(
            "A",
            2250,
            [
                mod("Н1", "base_door", 600),
                corner("Н7", "base_corner", 1050, "Б"),
                mod("Н2", "base_door", 600),
            ],
        ),
        wall("Б", 1800, [mod("Н5", "base_door", 1200)]),
    ]
    assert placement_error(home, write_data(walls)) == [
        "Модуль Н7 стоит посередине ряда, угловой модуль должен быть первым или последним."
    ]


def test_top_of_uppers_above_ceiling_is_an_error_with_both_numbers(home):
    edit(home, "construction.yaml", "пенал: {высота: 2100", "пенал: {высота: 2700")
    assert placement_error(home, petrov()) == ["Верх верхних модулей 2800 мм выше потолка 2700 мм."]


def test_top_of_uppers_at_the_ceiling_is_fine(home):
    data = petrov()
    data["ceiling_height"] = 2200
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert api.placement_from_project(path, home).top_of_uppers == 2200


def test_corner_only_in_lower_row_upper_modules_clash_in_the_corner(home):
    # угловой только в нижнем ряду: нижний ряд Б — со смещения нижнего, верхний — с 0, и В1 с В5 сходятся в углу
    walls = [
        wall(
            "A",
            1650,
            [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")],
            [mod("В1", "wall_door", 1650)],
        ),
        wall("Б", 1800, [mod("Н5", "base_door", 1200)], [mod("В5", "wall_door", 1800)]),
    ]
    result = place(home, walls)
    assert {m.label: m.x for m in result.modules if m.wall == "Б"} == {"Н5": 600, "В5": 0}
    assert [(f.code, f.level, f.message) for f in result.findings] == [
        (
            "corner_overlap",
            "error",
            "В1 (стена A) и В5 (стена Б) пересекаются в углу: нужен верхний угловой модуль или отступ верхнего ряда",
        )
    ]


def test_petrov_has_no_placement_findings(home):
    assert api.placement_from_project(PETROV, home).findings == []


def test_corner_only_in_upper_row_lower_modules_clash_in_the_corner(home):
    walls = [
        wall(
            "A",
            1650,
            [mod("Н1", "base_door", 1650)],
            [mod("В1", "wall_door", 1050), corner("В4", "wall_corner", 600, "Б")],
        ),
        wall("Б", 1800, [mod("Н5", "base_door", 1800)], [mod("В5", "wall_door", 1464)]),
    ]
    result = place(home, walls)
    assert [f.message for f in result.findings] == [
        "Н1 (стена A) и Н5 (стена Б) пересекаются в углу: нужен нижний угловой модуль или отступ нижнего ряда"
    ]


def test_u_shaped_kitchen(home):
    # A → Б → В: углы Н4 (конец A) и Н7 (конец Б); В повёрнута на 180°, её модули смотрят в −y комнаты
    walls = [
        wall("A", 1650, [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")]),
        wall("Б", 2400, [mod("Н5", "base_door", 750), corner("Н7", "base_corner", 1050, "В")]),
        wall("В", 1800, [mod("Н8", "base_door", 1200)]),
    ]
    result = place(home, walls)
    assert [(w.label, (w.origin.x, w.origin.y), w.rotation) for w in result.walls] == [
        ("A", (0, 0), 0),
        ("Б", (1650, 0), 90),
        ("В", (1650, 2400), 180),
    ]
    assert box(result, "Н7") == ((1650 - 579, 1350, 100), (1650, 2400, 820))
    assert box(result, "Н8") == ((1650 - 1800, 2400 - 579, 100), (1650 - 600, 2400, 820))
    assert result.findings == []


def test_chain_starts_at_the_wall_whose_start_is_free(home):
    # стены записаны не по порядку цепочки: начало координат — в начале A, у которой начало ни с чем не соединено
    walls = [
        wall("Б", 1800, [mod("Н5", "base_door", 1200)]),
        wall("A", 1650, [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")]),
    ]
    result = place(home, walls)
    assert [(w.label, (w.origin.x, w.origin.y), w.rotation) for w in result.walls] == [
        ("A", (0, 0), 0),
        ("Б", (1650, 0), 90),
    ]


def inside(inner, outer):
    (i0, i1), (o0, o1) = inner, outer
    return all(o0[k] <= i0[k] and i1[k] <= o1[k] for k in range(3))


@pytest.mark.parametrize("label", ["Н4", "П1"])
def test_parts_of_module_lie_inside_its_box_except_hdf_back(home, label):
    # контракт расширения (виды 41, HTML 42): детали модуля из `parts` кладутся в сцену той же расстановкой
    result = api.placement_from_project(PETROV, home)
    parts = next(m for m in api.parts_from_project(PETROV, home).modules if m.module == label).parts
    place_ = next(m for m in result.modules if m.label == label)
    objects = [api.part_in_room(place_, p) for p in parts]
    assert {o.kind for o in objects} == {"part"}
    outside = [o.label for o in objects if not inside(o.bounds(), place_.bounds())]
    assert outside == [f"{label}-зад"]
    back = next(p for p in parts if p.id == f"{label}-зад")
    assert back.position.y < 0 and back.material == "hdf_3_white"
    # фасады помечены: на будущее «скрыть фасады» (HTML, тикет 42)
    assert {o.label for o in objects if o.facade} == {p.id for p in parts if p.area_m2 is not None}


def test_scene_lists_modules_walls_and_floor(home):
    result = api.placement_from_project(PETROV, home)
    kinds = [o.kind for o in result.scene]
    assert kinds.count("module") == 13 and kinds.count("wall") == 2 and kinds.count("floor") == 1
    n5 = next(o for o in result.scene if o.label == "Н5")
    assert (n5.module, n5.rotation, n5.bounds(), n5.color) == ("Н5", 90, box(result, "Н5"), "RAL 7044")
    # стена — плоскость высотой до потолка проекта; пол накрывает стены и модули в плане
    b = next(o for o in result.scene if o.kind == "wall" and o.label == "Б")
    assert b.bounds() == ((2800, 0, 0), (2800, 1800, 2700))
    floor = next(o for o in result.scene if o.kind == "floor")
    assert floor.bounds() == ((0, 0, 0), (2800, 1800, 0))
    json.dumps(result.model_dump(mode="json"))  # сцена — JSON


def test_placement_of_order_keeps_source_version_and_mock_mark(home):
    api.order_new("2026-047-petrov", home=home)
    api.import_project("2026-047-petrov", PETROV, home=home)
    result = api.placement_from_order("2026-047-petrov", home=home)
    assert (result.source.kind, result.source.version) == ("order", 1)
    assert result.mock and result.warning == "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"
    assert result.model_dump(mode="json")["warning"] == "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"


def test_project_without_walls_is_an_error(home):
    assert placement_error(home, write_data([])) == ["В проекте нет стен: расставлять нечего."]
