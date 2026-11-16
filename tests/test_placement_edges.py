"""Тикет 23, ревью: границы расстановки и `tall_overlap`, которые не ловил основной набор (мутации из ревью 1bc87e7)."""

from kitchen import api
from kitchen.cli import app
from typer.testing import CliRunner

from tests.test_placement import PETROV, corner, edit, mod, place, placement_error, wall, write, write_data


def tall_overlaps(home, walls):
    return [
        f.message for f in api.check_project(write(home, walls), home).findings if f.code == "tall_overlap"
    ]


def test_tall_of_other_row_after_gap_stands_after_the_gap(home):
    # deferred ticket 32: lower [ПМ1, Н1], upper [ПМ2, П1, В1] -> lower order ПМ1, П1, Н1
    walls = [{"label": "A", "length": 1800, "rows": [
        {"kind": "lower", "modules": [{"label": "ПМ1", "gap": True, "width": 600}, mod("Н1", "base_door", 600)]},
        {"kind": "upper", "modules": [
            {"label": "ПМ2", "gap": True, "width": 600}, mod("П1", "tall_oven", 600), mod("В1", "wall_door", 600),
        ]},
    ]}]  # fmt: skip
    result = place(home, walls)
    assert {m.label: m.x for m in result.modules} == {"Н1": 1200, "П1": 600, "В1": 1200}
    assert [(g.label, g.row, g.x) for g in result.gaps] == [("ПМ1", "lower", 0), ("ПМ2", "upper", 0)]


def test_tall_right_after_start_offset_of_other_row_is_fine(home):
    walls = [
        wall("A", 1650, [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")]),
        wall(
            "Б",
            1800,
            [mod("Н5", "base_door", 600)],
            [mod("В5", "wall_door", 600), mod("П1", "tall_oven", 600), mod("В6", "wall_door", 600)],
        ),
    ]
    assert tall_overlaps(home, walls) == []


def test_tall_right_before_end_offset_of_other_row_is_fine(home):
    # spec case "угловой в начале ряда стены Б": П1 1050–1650, offset 1650–2250 — touching, no finding
    walls = [
        wall(
            "A",
            2250,
            [mod("Н1", "base_door", 600), mod("Н2", "base_door", 450)],
            [mod("В1", "wall_door", 1050), mod("П1", "tall_oven", 600), mod("В2", "wall_door", 600)],
        ),
        wall("Б", 2250, [corner("Н4", "base_corner", 1050, "A"), mod("Н5", "base_door", 1200)]),
    ]
    assert tall_overlaps(home, walls) == []


def test_tall_on_end_offset_of_other_row(home):
    walls = [
        wall(
            "A",
            2250,
            [mod("Н1", "base_door", 600), mod("Н2", "base_door", 450)],
            [mod("В1", "wall_door", 1200), mod("П1", "tall_oven", 600), mod("В2", "wall_door", 450)],
        ),
        wall("Б", 2250, [corner("Н4", "base_corner", 1050, "A"), mod("Н5", "base_door", 1200)]),
    ]
    assert tall_overlaps(home, walls) == [
        "Пенал П1 (x 1200–1800 на стене A) стоит на месте углового смещения или пересекает модуль Н4 нижнего ряда."
    ]


def test_ceiling_is_not_checked_without_uppers_and_tall(home):
    # DECISIONS «Расстановка в комнате»: потолок проверяется, только если есть верхние модули или пенал
    result = place(
        home, [wall("A", 1800, [mod(f"Н{i}", "base_door", 600) for i in (1, 2, 3)])], ceiling_height=2000
    )
    assert result.top_of_uppers == 2200


def test_parts_of_module_on_turned_wall_lie_inside_its_box(home):
    result = api.placement_from_project(PETROV, home)
    parts = next(m for m in api.parts_from_project(PETROV, home).modules if m.module == "В6").parts
    place_ = next(m for m in result.modules if m.label == "В6")
    assert place_.rotation == 90
    (lo, hi) = place_.bounds()
    outside = [
        p.id
        for p in parts
        if not all(
            lo[k] <= a and b <= hi[k] for k, (a, b) in enumerate(zip(*api.part_in_room(place_, p).bounds()))
        )
    ]
    assert outside == ["В6-зад"]


def test_fourth_wall_of_chain_turns_270(home):
    walls = [
        wall("A", 2000, [mod("Н1", "base_door", 950), corner("Н4", "base_corner", 1050, "Б")]),
        wall("Б", 2000, [mod("Н5", "base_door", 350), corner("Н6", "base_corner", 1050, "В")]),
        wall("В", 2000, [mod("Н7", "base_door", 350), corner("Н8", "base_corner", 1050, "Г")]),
        wall("Г", 950, [mod("Н9", "base_door", 350)]),
    ]
    result = place(home, walls)
    assert [(w.label, (w.origin.x, w.origin.y), w.rotation) for w in result.walls][-1] == (
        "Г",
        (0, 2000),
        270,
    )
    assert next(m for m in result.modules if m.label == "Н9").bounds() == ((0, 1050, 100), (579, 1400, 820))


def ring(lengths, labels="AБВГ"):
    """Стены по кругу: на каждой [Н, угловой → следующая стена], последняя ведёт к первой."""
    walls = []
    for i, length in enumerate(lengths):
        to = labels[(i + 1) % len(lengths)]
        walls.append(
            wall(
                labels[i],
                length,
                [
                    mod(f"Н{2 * i + 1}", "base_door", length - 1650),
                    corner(f"Н{2 * i + 2}", "base_corner", 1050, to),
                ],
            )
        )
    return walls


def test_three_walls_in_a_ring_are_an_error_with_the_gap(home):
    # тикет 54 (13): A, Б, В по 2850 — конец В в (0, 2850), начало A в (0, 0)
    assert placement_error(home, write_data(ring([2850, 2850, 2850]))) == [
        "Стены A, Б, В соединены по кругу, а длины не сходятся на 2850 мм."
    ]


def test_closed_square_passes_and_its_last_wall_ends_at_the_origin(home):
    result = place(home, ring([2850, 2850, 2850, 2850]))
    assert [(w.label, (w.origin.x, w.origin.y), w.rotation, w.length) for w in result.walls] == [
        ("A", (0, 0), 0, 2850),
        ("Б", (2850, 0), 90, 2850),
        ("В", (2850, 2850), 180, 2850),
        ("Г", (0, 2850), 270, 2850),  # 2850 к −y: конец в (0, 0)
    ]


def test_square_with_a_longer_wall_does_not_close(home):
    # В 2950: конец В в (−100, 2850), конец Г в (−100, 0)
    assert placement_error(home, write_data(ring([2850, 2850, 2950, 2850]))) == [
        "Стены A, Б, В, Г соединены по кругу, а длины не сходятся на 100 мм."
    ]


def test_two_walls_in_a_ring_are_an_error_with_the_gap(home):
    # A → Б и Б → A: конец Б в (1800, 2400), до начала A — 3000
    assert placement_error(home, write_data(ring([1800, 2400]))) == [
        "Стены A, Б соединены по кругу, а длины не сходятся на 3000 мм."
    ]


def test_ring_gap_is_named_in_the_order_of_the_chain_from_the_first_wall_of_the_project(home):
    walls = ring([2850, 2850, 2950, 2850])
    assert placement_error(home, write_data(walls[2:] + walls[:2])) == [
        "Стены В, Г, A, Б соединены по кругу, а длины не сходятся на 100 мм."
    ]


def test_ring_gap_within_wall_tolerance_passes(home):
    edit(home, "construction.yaml", "допуск_стены: 0", "допуск_стены: 100")
    assert [w.label for w in place(home, ring([2850, 2850, 2950, 2850])).walls] == ["A", "Б", "В", "Г"]
    edit(home, "construction.yaml", "допуск_стены: 100", "допуск_стены: 99")
    assert placement_error(home, write_data(ring([2850, 2850, 2950, 2850]))) == [
        "Стены A, Б, В, Г соединены по кругу, а длины не сходятся на 100 мм."
    ]


def test_ring_gap_is_rounded_to_whole_mm(home):
    # A 1000, Б 1000 по кругу: √2 · 1000 = 1414,2 → 1414
    walls = ring([1650, 1650])
    walls[0]["length"] = walls[1]["length"] = 1000
    walls[0]["rows"][0]["modules"] = [corner("Н2", "base_corner", 1000, "Б")]
    walls[1]["rows"][0]["modules"] = [corner("Н4", "base_corner", 1000, "A")]
    assert placement_error(home, write_data(walls)) == [
        "Стены A, Б соединены по кругу, а длины не сходятся на 1414 мм."
    ]


def test_ring_gap_is_compared_with_the_tolerance_after_rounding(home):
    # В 2950,3: расхождение 100,3 → 100 мм, допуск 100 — проходит (решение агента: сравнивается округлённое N)
    edit(home, "construction.yaml", "допуск_стены: 0", "допуск_стены: 100")
    walls = ring([2850, 2850, 2950, 2850])
    walls[2]["length"] = 2950.3
    assert [w.label for w in place(home, walls).walls] == ["A", "Б", "В", "Г"]


def test_two_wall_ring_by_different_rows_stays_contradictory(home):
    # кольцо из двух стен, заданное разными рядами, — прежнее «противоречиво», а не текст стен по кругу
    walls = [
        wall("A", 1800, [mod("Н1", "base_door", 750), corner("Н2", "base_corner", 1050, "Б")]),
        wall(
            "Б",
            2400,
            [mod("Н3", "base_door", 2400)],
            [mod("В1", "wall_door", 1800), corner("В2", "wall_corner", 600, "A")],
        ),
    ]
    assert placement_error(home, write_data(walls)) == [
        "Угол между A и Б задан противоречиво: нижний ряд — конец A к началу Б, верхний — начало A к концу Б."
    ]


def test_ring_error_comes_with_the_ceiling_error(home):
    edit(home, "construction.yaml", "пенал: {высота: 2100", "пенал: {высота: 2700")
    walls = ring([2850, 2850, 2850])
    walls[0]["rows"].append({"kind": "upper", "modules": [mod("В1", "wall_door", 2850)]})
    assert placement_error(home, write_data(walls)) == [
        "Верх верхних модулей 2800 мм выше потолка 2700 мм.",
        "Стены A, Б, В соединены по кругу, а длины не сходятся на 2850 мм.",
    ]


def test_ring_is_accepted_by_check_and_import_and_3d_refuses_without_files(home):
    path = write(home, ring([2850, 2850, 2850]))
    assert [f.code for f in api.check_project(path, home).findings if f.level == "error"] == []
    api.order_new("2026-047-petrov", home=home)
    assert api.import_project("petrov", path, home=home).version == 1
    result = CliRunner().invoke(app, ["3d", "petrov"])
    assert result.exit_code != 0
    assert "Стены A, Б, В соединены по кругу, а длины не сходятся на 2850 мм." in result.output
    out = home / "orders" / "2026-047-petrov" / "out"
    assert not (out / "kitchen-views.pdf").exists() and not (out / "kitchen-3d.html").exists()


def test_module_without_facade_color_has_no_scene_color(home):
    result = place(home, [wall("A", 1800, [mod(f"Н{i}", "base_door", 600) for i in (1, 2, 3)])])
    assert {o.color for o in result.scene if o.kind == "module"} == {None}


def test_wall_end_meeting_two_walls_is_an_error(home):
    walls = [
        wall(
            "A",
            1650,
            [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")],
            [mod("В1", "wall_door", 1050), corner("В4", "wall_corner", 600, "В")],
        ),
        wall("Б", 1800, [mod("Н5", "base_door", 1200)]),
        wall("В", 1800, [], [mod("В5", "wall_door", 1464)]),
    ]
    assert placement_error(home, write_data(walls)) == [
        "Конец стены A встречается с несколькими стенами: Б и В."
    ]


def test_corner_to_missing_wall_is_a_placement_error(home):
    walls = [wall("A", 1650, [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Я")])]
    assert placement_error(home, write_data(walls)) == [
        "Модуль Н4: соседняя стена «Я» должна существовать и отличаться от стены «A»."
    ]


def test_neighbour_tall_shifted_in_other_row_does_not_flag_the_next_tall(home):
    # Н1 «перешагивает» П1; П1 в нижнем ряду сдвинут на 450–1050 и заходит на место П2 (900–1500) — это не модуль
    # нижнего ряда, находка только у П1
    walls = [
        wall(
            "A",
            1800,
            [mod("Н1", "base_door", 450), mod("Н2", "base_door", 1350)],
            [
                mod("В1", "wall_door", 300),
                mod("П1", "tall_oven", 600),
                mod("П2", "tall_oven", 600),
                mod("В2", "wall_door", 300),
            ],
        ),
    ]
    assert tall_overlaps(home, walls) == [
        "Пенал П1 (x 300–900 на стене A) стоит на месте углового смещения или пересекает модуль Н1 нижнего ряда."
    ]


def test_tall_of_adjacent_wall_clashes_with_upper_corner_module(home):
    # В4 — угловой верхнего ряда, а пенал П1 нижнего ряда Б стоит у угла: исключение «угловой и свой ряд соседней
    # стены» его не прячет
    walls = [
        wall(
            "A",
            1650,
            [mod("Н1", "base_door", 1650)],
            [mod("В1", "wall_door", 1050), corner("В4", "wall_corner", 600, "Б")],
        ),
        wall("Б", 1800, [mod("П1", "tall_oven", 600), mod("Н5", "base_door", 1200)]),
    ]
    assert [f.message for f in place(home, walls).findings] == [
        "Н1 (стена A) и П1 (стена Б) пересекаются в углу: нужен нижний угловой модуль или отступ нижнего ряда",
        "В4 (стена A) и П1 (стена Б) пересекаются в углу: нужен верхний угловой модуль или отступ верхнего ряда",
    ]


def test_tall_of_upper_row_with_own_height_stands_on_the_legs(home):
    # DECISIONS «Коробка в сцене»: пенал — от ножек на свою высоту, и тогда, когда он записан в верхнем ряду
    walls = [
        wall(
            "A",
            1200,
            [mod("Н1", "base_door", 600)],
            [mod("П1", "tall_oven", 600, height=2000), mod("В1", "wall_door", 600)],
        )
    ]
    p1 = next(m for m in place(home, walls).modules if m.label == "П1")
    assert (p1.bounds()[0][2], p1.bounds()[1][2]) == (100, 2100)


def test_floor_covers_walls_that_run_into_negative_x(home):
    # П-образная: стена В (поворот 180) идёт от x 1650 до −150; пол накрывает её
    walls = [
        wall("A", 1650, [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")]),
        wall("Б", 2400, [mod("Н5", "base_door", 750), corner("Н7", "base_corner", 1050, "В")]),
        wall("В", 1800, [mod("Н8", "base_door", 1200)]),
    ]
    floor = next(o for o in place(home, walls).scene if o.kind == "floor")
    assert floor.bounds() == ((-150, 0, 0), (1650, 2400, 0))
