"""Тикет 23, ревью: границы расстановки и `tall_overlap`, которые не ловил основной набор (мутации из ревью 1bc87e7)."""

from kitchen import api

from tests.test_placement import PETROV, corner, mod, place, placement_error, wall, write, write_data


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
