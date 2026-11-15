"""Тикет 41, первый слайс: верх верхних по самому высокому пеналу проекта (DECISIONS «Верх верхних по пеналу»).
`top_of_uppers` = `ножки.высота` + высота самого высокого пенала (своя `height`, иначе `пенал.высота`);
пеналы разной высоты — предупреждение расстановки `tall_heights`."""

from kitchen import api

from tests.test_placement import PETROV, box, mod, petrov, place, placement_error, wall


def write_petrov(home, data):
    import json

    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_own_height_of_tall_sets_top_of_uppers(home):
    result = api.placement_from_project(write_petrov(home, petrov(П1={"height": 2300})), home)
    assert result.top_of_uppers == 2400
    assert box(result, "П1")[1][2] == 2400
    assert {box(result, m.label)[1][2] for m in result.modules if m.row == "upper"} == {2400}
    assert result.findings == []


def two_talls(home, h1, h2):
    return place(
        home,
        [
            wall(
                "A",
                1800,
                [
                    mod("П1", "tall_oven", 600, height=h1),
                    mod("П2", "tall_oven", 600, height=h2),
                    mod("Н1", "base_door", 600),
                ],
                [mod("В1", "wall_door", 600)],
            )
        ],
    )


def test_talls_of_different_heights_warn_and_uppers_follow_the_tallest(home):
    result = two_talls(home, 2100, 2300)
    assert result.top_of_uppers == 2400
    assert box(result, "В1")[1][2] == 2400
    assert box(result, "П1")[1][2] == 2200
    assert [(f.code, f.level, f.message) for f in result.findings] == [
        (
            "tall_heights",
            "warning",
            "Пеналы разной высоты: П1 2100 мм, П2 2300 мм — верх верхних по П2 (2400 мм).",
        )
    ]


def test_talls_of_one_height_give_no_warning(home):
    assert two_talls(home, 2300, 2300).findings == []


def test_tall_above_the_ceiling_is_the_placement_error(home):
    assert placement_error(home, petrov(П1={"height": 2700})) == [
        "Верх верхних модулей 2800 мм выше потолка 2700 мм."
    ]


def test_petrov_has_no_placement_finding(home):
    result = api.placement_from_project(PETROV, home)
    assert result.top_of_uppers == 2200
    assert result.findings == []


def test_without_tall_top_follows_tall_height_of_workshop(home):
    result = place(home, [wall("A", 600, [mod("Н1", "base_door", 600)], [mod("В1", "wall_door", 600)])])
    assert result.top_of_uppers == 2200


def test_tallest_tall_listed_first_still_sets_top_and_is_named(home):
    result = two_talls(home, 2300, 2100)
    assert result.top_of_uppers == 2400
    assert [f.message for f in result.findings] == [
        "Пеналы разной высоты: П1 2300 мм, П2 2100 мм — верх верхних по П1 (2400 мм)."
    ]
