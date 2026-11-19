"""Тикет 54 (7): пеналы разных рядов одной стены, которым выходит один и тот же отступ, стоят по порядку меток
(«естественно»: буквы после нормализации, затем число), какой бы ряд или стена ни были записаны первыми."""

import itertools

import pytest
from kitchen import api

from tests.test_placement import corner, mod, place, wall, write


def rows(*rows):
    return [{"kind": kind, "modules": modules} for kind, modules in rows]


def xs(result):
    return {m.label: m.x for m in result.modules}


def one_wall(length, *row_list):
    return [{"label": "A", "length": length, "rows": rows(*row_list)}]


@pytest.mark.parametrize("flip", [False, True])
def test_talls_of_two_rows_on_one_offset_stand_by_label(home, flip):
    lower = ("lower", [mod("П2", "tall_oven", 600), mod("Н1", "base_door", 600)])
    upper = ("upper", [mod("П1", "tall_oven", 600), mod("В1", "wall_door", 600)])
    result = place(home, one_wall(2400, *((upper, lower) if flip else (lower, upper))))
    assert xs(result) == {"П1": 0, "П2": 600, "Н1": 1200, "В1": 1200}


@pytest.mark.parametrize("flip", [False, True])
def test_label_numbers_compare_as_numbers(home, flip):
    lower = ("lower", [mod("П10", "tall_oven", 600), mod("Н1", "base_door", 600)])
    upper = ("upper", [mod("П2", "tall_oven", 600), mod("В1", "wall_door", 600)])
    result = place(home, one_wall(2400, *((upper, lower) if flip else (lower, upper))))
    assert (xs(result)["П2"], xs(result)["П10"]) == (0, 600)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Т1", "T2"),  # кириллическая Т и латинская T — одна буква
        ("п2", "П10"),  # регистр не важен
        ("П２", "П10"),  # полноширинная цифра — та же 2
        ("\u200bП2", "П10"),  # невидимый символ не считается
    ],
)
@pytest.mark.parametrize("flip", [False, True])
def test_labels_compare_after_normalisation(home, first, second, flip):
    lower = ("lower", [mod(second, "tall_oven", 600), mod("Н1", "base_door", 600)])
    upper = ("upper", [mod(first, "tall_oven", 600), mod("В1", "wall_door", 600)])
    result = place(home, one_wall(2400, *((upper, lower) if flip else (lower, upper))))
    assert (xs(result)[first], xs(result)[second]) == (0, 600)


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_three_talls_of_three_rows_stand_by_label(home, order):
    made = [
        ("lower", [mod("П3", "tall_oven", 600), mod("Н1", "base_door", 600)]),
        ("upper", [mod("П1", "tall_oven", 600), mod("В1", "wall_door", 600)]),
        ("upper", [mod("П2", "tall_oven", 600)]),
    ]
    result = place(home, one_wall(2400, *(made[i] for i in order)))
    assert {k: v for k, v in xs(result).items() if k.startswith("П")} == {"П1": 0, "П2": 600, "П3": 1200}


@pytest.mark.parametrize("flip", [False, True])
def test_order_inside_a_row_is_kept_when_a_tall_of_another_row_wins_the_offset(home, flip):
    # П2 и П3 на отступе 0 — первым П2; П3 и П1 одного ряда идут, как записаны: П3, затем П1
    lower = ("lower", [mod("П3", "tall_oven", 600), mod("П1", "tall_oven", 600)])
    upper = ("upper", [mod("П2", "tall_oven", 600), mod("В1", "wall_door", 600)])
    result = place(home, one_wall(2400, *((upper, lower) if flip else (lower, upper))))
    assert xs(result) == {"П2": 0, "П3": 600, "П1": 1200, "В1": 1800}


@pytest.mark.parametrize("flip", [False, True])
def test_gap_on_the_offset_of_a_tall_does_not_take_part_in_label_order(home, flip):
    # пустое место «А1» раньше «П2» по меткам, но пенал другого ряда на его отступе стоит первым, как раньше
    lower = ("lower", [mod("П2", "tall_oven", 600), mod("Н1", "base_door", 600)])
    upper = ("upper", [{"label": "А1", "gap": True, "width": 600}, mod("В1", "wall_door", 600)])
    result = place(home, one_wall(2400, *((upper, lower) if flip else (lower, upper))))
    assert xs(result) == {"П2": 0, "Н1": 600, "В1": 1200}
    assert [(g.label, g.x) for g in result.gaps] == [("А1", 600)]


@pytest.mark.parametrize("flip", [False, True])
def test_tall_right_after_a_gap_of_another_row_keeps_its_place(home, flip):
    # пенал встык с пустым местом: П2 другого ряда на 0 встаёт перед ПМ1, П1 — после ПМ1 на 1200, В1 — над ПМ1;
    # отступы разные, порядок меток не нужен (как раньше)
    lower = ("lower", [{"label": "ПМ1", "gap": True, "width": 600}, mod("П1", "tall_oven", 600)])
    upper = ("upper", [mod("П2", "tall_oven", 600), mod("В1", "wall_door", 600)])
    result = place(home, one_wall(2400, *((upper, lower) if flip else (lower, upper))))
    assert xs(result) == {"П2": 0, "П1": 1200, "В1": 600}
    assert [(g.label, g.x) for g in result.gaps] == [("ПМ1", 600)]


@pytest.mark.parametrize("walls_flip", [False, True])
@pytest.mark.parametrize("rows_flip", [False, True])
def test_tie_after_corner_offsets_does_not_depend_on_wall_or_row_order(home, walls_flip, rows_flip):
    # на Б нижний ряд со смещения 600, верхний — 336 + В5 264 = 600: П1 и П2 на одном отступе
    a = wall(
        "A",
        1650,
        [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")],
        [mod("В1", "wall_door", 1050), corner("В4", "wall_corner", 600, "Б")],
    )
    lower = ("lower", [mod("П2", "tall_oven", 600), mod("Н5", "base_door", 600)])
    upper = ("upper", [mod("В5", "wall_door", 264), mod("П1", "tall_oven", 600), mod("В6", "wall_door", 600)])
    b = {"label": "Б", "length": 2400, "rows": rows(*((upper, lower) if rows_flip else (lower, upper)))}
    result = place(home, [b, a] if walls_flip else [a, b])
    assert {k: v for k, v in xs(result).items() if k in ("П1", "П2", "Н5", "В6")} == {
        "П1": 600,
        "П2": 1200,
        "Н5": 1800,
        "В6": 1800,
    }


@pytest.mark.parametrize("flip", [False, True])
def test_tall_of_another_row_with_a_smaller_offset_is_not_overtaken_by_label(home, flip):
    # П9 верхнего ряда на 300 (после В1 300) — меньше отступа П1 в нижнем (600): в нижнем ряду П9 встаёт раньше
    # П1, как до тикета 54, хотя метка П1 раньше; П1 сдвигается на 1200
    lower = ("lower", [mod("Н1", "base_door", 600), mod("П1", "tall_oven", 600)])
    upper = ("upper", [mod("В1", "wall_door", 300), mod("П9", "tall_oven", 600)])
    result = place(home, one_wall(2400, *((upper, lower) if flip else (lower, upper))))
    assert (xs(result)["П9"], xs(result)["П1"]) == (300, 1200)


@pytest.mark.parametrize("flip", [False, True])
def test_talls_with_labels_equal_after_normalisation_do_not_stand_on_one_place(home, flip):
    # П1 и п1 — повтор метки: расстановка отказывает, а проверки видят `duplicate_label`, но не `tall_overlap` —
    # порядок модулей в рядах всё равно ставит пеналы друг за другом, а не на одно место
    lower = ("lower", [mod("П1", "tall_oven", 600), mod("Н1", "base_door", 600)])
    upper = ("upper", [mod("п1", "tall_oven", 600), mod("В1", "wall_door", 600)])
    path = write(home, one_wall(2400, *((upper, lower) if flip else (lower, upper))))
    codes = {f.code for f in api.calculation(project=path, home=home).check().findings}
    assert "duplicate_label" in codes and "tall_overlap" not in codes
