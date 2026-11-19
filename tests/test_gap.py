"""Тикет 32: пустое место в ряду (срез а) — схема, сумма ряда, общие позиции, пропуск в обходах."""

import copy
import json

import pytest
from kitchen import api
from kitchen.errors import OrderError

from tests.conftest import BASE_DOOR_PROJECT

ORDER = "2026-047-petrov"


def mod(label, template, width, **extra):
    return {"label": label, "template": template, "width": width, **extra}


def gap(label, width=600, **extra):
    return {"label": label, "gap": True, "width": width, **extra}


def base(label, width=600):
    return mod(label, "base_door", width)


def wall_door(label, width=600):
    return mod(label, "wall_door", width)


def write(home, walls, *, top="top_38_marble", led=False, appliances=None, name="p.json"):
    data = copy.deepcopy(BASE_DOOR_PROJECT)
    data["walls"] = walls
    if top:
        data["materials"]["countertop"] = top
    if led:
        data["led"] = True
    if appliances:
        data["appliances"] = appliances
    path = home / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def lower(*entries):
    return {"kind": "lower", "modules": list(entries)}


def upper(*entries):
    return {"kind": "upper", "modules": list(entries)}


def wall_3700(lower_gap=True, upper_gap=True):
    low = [gap("ПМ1") if lower_gap else base("Н0")] + [base(f"Н{i}") for i in range(1, 5)] + [base("Н5", 700)]
    up = (
        [gap("ПМ2") if upper_gap else wall_door("В0")]
        + [wall_door(f"В{i}") for i in range(1, 5)]
        + [wall_door("В5", 700)]
    )
    return [{"label": "A", "length": 3700, "rows": [lower(*low), upper(*up)]}]


def common(home, path):
    return {c.kind: c for c in api.calculation(project=path, home=home).parts().common}


def codes(home, path):
    return {f.code for f in api.calculation(project=path, home=home).check().findings}


# --- схема ---------------------------------------------------------------------


def test_gap_entry_parses_and_is_in_the_row_in_place(home):
    walls = [{"label": "A", "length": 1200, "rows": [lower(base("Н1"), gap("ПМ1"))]}]
    quote = api.calculation(project=write(home, walls), home=home).quote(14)
    assert [(m.label, m.row) for m in quote.modules] == [("Н1", "lower"), ("ПМ1", "lower")]


@pytest.mark.parametrize(
    "entry",
    [
        {
            "label": "ПМ1",
            "gap": True,
            "width": 600,
            "template": "base_door",
        },  # неизвестный ключ пустого места
        {"label": "ПМ1", "gap": True, "width": 600, "height": 700},
        {"label": "ПМ1", "gap": True, "width": 0},
        {"label": "ПМ1", "gap": True, "width": -5},
        {"label": "ПМ1", "gap": True},
        {"gap": True, "width": 600},  # метка обязательна
    ],
)
def test_gap_schema_rejects(home, entry):
    walls = [{"label": "A", "length": 600, "rows": [lower(entry)]}]
    with pytest.raises(OrderError):
        api.calculation(project=write(home, walls), home=home).check()


def test_fractional_gap_width_is_the_whole_millimetres_error(home):
    path = write(home, [{"label": "A", "length": 1200, "rows": [lower(base("Н1"), gap("ПМ1", 600.5))]}])
    [f] = [f for f in api.calculation(project=path, home=home).check().findings if "целое" in f.message]
    assert "ПМ1" in f.message and "600.5" in f.message
    with pytest.raises(OrderError, match="целое число миллиметров"):
        api.calculation(project=path, home=home).parts()


# --- сумма ряда -----------------------------------------------------------------


def test_row_sum_counts_gap_and_message_names_gaps(home):
    assert "row_sum" not in codes(home, write(home, wall_3700()))
    walls = wall_3700()
    walls[0]["length"] = 3800
    found = api.calculation(project=write(home, walls), home=home).check().findings
    messages = sorted(f.message for f in found if f.code == "row_sum")
    tail = "занимает 3700 мм (модули, пустые места, угловые смещения, пенал), "
    tail += "а длина стены 3800 мм: разница 100 мм (допуск 0 мм)."
    assert messages == [f"Стена A: верхний ряд {tail}", f"Стена A: нижний ряд {tail}"]


# --- общие позиции ----------------------------------------------------------------


def test_common_lengths_skip_gaps_and_full_project_is_3700(home):
    path = write(home, wall_3700(), led=True)
    items = common(home, path)
    for kind in ("plinth", "rail", "led", "countertop"):
        assert items[kind].length_mm == 3100, kind
    full = common(home, write(home, wall_3700(False, False), led=True, name="full.json"))
    for kind in ("plinth", "rail", "led", "countertop"):
        assert full[kind].length_mm == 3700, kind


def test_only_lower_gap_changes_plinth_and_countertop_only(home):
    items = common(home, write(home, wall_3700(upper_gap=False), led=True))
    assert (items["plinth"].length_mm, items["countertop"].length_mm) == (3100, 3100)
    assert (items["rail"].length_mm, items["led"].length_mm) == (3700, 3700)


def test_basis_tails_only_with_gaps(home):
    items = common(home, write(home, wall_3700(), led=True))
    assert items["plinth"].basis == (
        "фронт нижних рядов 3100 мм (без глухой части углов 0 мм, без пустых мест 600 мм) "
        "+ запас 5%, штуки по 4000 мм"
    )
    assert items["rail"].basis.startswith("верхние ряды 3100 мм (без пустых мест 600 мм) + запас 5%, ")
    assert items["led"].basis.startswith("верхние ряды 3100 мм (без пустых мест 600 мм) + запас 5%, ")
    assert items["countertop"].basis == (
        "общая длина 3100 мм (без пустых мест 600 мм), листы по 4100 мм вверх до целого"
    )
    plain = common(home, write(home, wall_3700(False, False), led=True, name="full.json"))
    assert "пустых мест" not in plain["plinth"].basis + plain["rail"].basis + plain["countertop"].basis
    assert plain["plinth"].basis.startswith(
        "фронт нижних рядов 3700 мм (без глухой части углов 0 мм) + запас"
    )


def test_under_countertop_gap_is_in_countertop_but_not_plinth(home):
    walls = [
        {
            "label": "A",
            "length": 1650,
            "rows": [lower(base("Н1"), gap("ПМ1", 450, under_countertop=True), base("Н2"))],
        }
    ]
    items = common(home, write(home, walls))
    assert items["countertop"].length_mm == 1650
    assert items["plinth"].length_mm == 1200
    assert "без пустых мест 450 мм" in items["plinth"].basis
    assert "без пустых мест" not in items["countertop"].basis


def test_tall_on_wall_without_lower_row_is_in_plinth(home):
    walls = [
        {
            "label": "A",
            "length": 1200,
            "rows": [lower(base("Н1"), base("Н2")), upper(wall_door("В1"), wall_door("В2"))],
        },
        {"label": "Б", "length": 600, "rows": [upper(mod("П1", "tall_oven", 600))]},
    ]
    assert common(home, write(home, walls))["plinth"].length_mm == 1800


def test_tall_of_other_row_after_gap_is_placed_and_row_sum_holds(home):
    # П1 записан в верхнем ряду на отступе 600 (после ПМ2); нижний ряд берёт его на то же место
    walls = [
        {
            "label": "A",
            "length": 1800,
            "rows": [
                lower(gap("ПМ1"), base("Н1")),
                upper(gap("ПМ2"), mod("П1", "tall_oven", 600), wall_door("В1")),
            ],
        }
    ]
    path = write(home, walls)
    assert "row_sum" not in codes(home, path)


# --- обходы пропускают пустое место -------------------------------------------------


def test_gap_is_skipped_by_every_walk(home):
    path = write(home, wall_3700(), led=True)
    parts = api.calculation(project=path, home=home).parts()
    assert {m.module for m in parts.modules} == {"Н1", "Н2", "Н3", "Н4", "Н5", "В1", "В2", "В3", "В4", "В5"}
    api.calculation(project=path, home=home).cut()
    api.calculation(project=path, home=home).drilling()
    assert "ПМ1" not in {m.label for m in api.calculation(project=path, home=home).assembly().modules}
    estimate = api.calculation(project=path, home=home).estimate()
    install = next(line for line in estimate.labor.lines if line.operation == "install")
    assert install.quantity == 10
    quote = api.calculation(project=path, home=home).quote(14)
    assert [m.text for m in quote.modules if m.label in ("ПМ1", "ПМ2")] == [
        "пустое место, 600 мм",
        "пустое место, 600 мм",
    ]


def test_corner_next_to_gap_is_first_with_blind_part_on_the_left(home):
    corner = mod("Н4", "base_corner", 1050, corner={"adjacent_wall": "Б"})
    walls = [
        {"label": "A", "length": 1650, "rows": [lower(corner, gap("ПМ1"))]},
        {"label": "Б", "length": 1200, "rows": [lower(base("Н5"), base("Н6"))]},
    ]
    parts = api.calculation(project=write(home, walls), home=home).parts()
    corner_parts = next(m.parts for m in parts.modules if m.module == "Н4")
    filler = next(p for p in corner_parts if p.name == "заглушка")
    facade = next(p for p in corner_parts if p.name == "фасад")
    assert filler.position.x < facade.position.x


def test_economy_purchase_and_cutting_skip_gaps(home):
    path = write(home, wall_3700(), led=True)
    api.order_new(ORDER)
    api.import_project("petrov", path, home=home)
    assert api.calculation(order="petrov", home=home).estimate(economy=True).price > 0
    purchase = api.calculation(order="petrov", home=home).purchase()
    assert purchase.total > 0
    cut = api.calculation(order="petrov", home=home).cut()
    assert all("ПМ" not in str(piece) for piece in cut.materials)


def test_gap_width_600_and_600_point_0_make_no_new_version(home):
    path = write(home, wall_3700())
    api.order_new(ORDER)
    assert api.import_project("petrov", path, home=home).version == 1
    text = path.read_text(encoding="utf-8").replace('"width": 600,', '"width": 600.0,')
    path.write_text(text, encoding="utf-8")
    assert api.import_project("petrov", path, home=home).changed is False


def test_schema_error_names_the_key_as_written_in_the_file(home):
    walls = [{"label": "A", "length": 600, "rows": [lower(base("Н1", 0)), upper(gap("ПМ2", 0))]}]
    with pytest.raises(OrderError) as e:
        api.calculation(project=write(home, walls), home=home).check()
    assert "«walls[0].rows[0].modules[0].width»" in str(e.value)
    assert "«walls[0].rows[1].modules[0].width»" in str(e.value)


def test_schema_error_keeps_module_key_outside_row_entries(home):
    walls = [{"label": "A", "length": 600, "rows": [lower(base("Н1"))]}]
    with pytest.raises(OrderError) as e:
        api.calculation(
            project=write(home, walls, appliances=[{"type": "oven", "width": 600}]), home=home
        ).check()
    assert "«appliances[0].module»" in str(e.value)


def test_cut_json_has_no_gaps_mm(home):
    assert (
        '"gaps_mm"'
        not in api.calculation(project=write(home, wall_3700()), home=home).cut().model_dump_json()
    )


# --- тикет 51: отметка «под столешницей» у пустого места верхнего ряда ----------

UPPER_COUNTERTOP = (
    "Место ПМ1 в верхнем ряду: отметка «под столешницей» не действует — "
    "столешница идёт только над нижним рядом."
)


def test_upper_gap_under_countertop_is_one_warning_and_the_project_is_accepted(home):
    walls = [
        {
            "label": "A",
            "length": 1200,
            "rows": [
                lower(base("Н1"), base("Н2")),
                upper(gap("ПМ1", 600, under_countertop=True), wall_door("В1")),
            ],
        }
    ]
    path = write(home, walls)
    result = api.calculation(project=path, home=home).check()
    [found] = [f for f in result.findings if f.code == "gap_upper_countertop"]
    assert (found.level, found.subject, found.message) == ("warning", "ПМ1", UPPER_COUNTERTOP)
    assert result.errors == []
    assert common(home, path)["countertop"].length_mm == 1200
    api.order_new(ORDER)
    assert api.import_project("petrov", path, home=home).version == 1
    parts = api.calculation(project=path, home=home).parts()
    assert UPPER_COUNTERTOP in {f.message for f in parts.findings}


def test_lower_gap_under_countertop_and_plain_upper_gap_give_no_such_finding(home):
    walls = [
        {
            "label": "A",
            "length": 1200,
            "rows": [
                lower(gap("ПМ1", 600, under_countertop=True), base("Н1")),
                upper(gap("ПМ2", 600), wall_door("В1")),
            ],
        }
    ]
    assert "gap_upper_countertop" not in codes(home, write(home, walls))


# --- тикет 52: высота и глубина пустого места на видах и в 3D (ответы мастера 8 и 12) ---


def views_of(home, walls):
    return api.calculation(project=write(home, walls), home=home).views()


def gap_of(result, label):
    return next(g for g in result.scene.gaps if g.label == label)


def box_of(result, label):
    return next(m for m in result.scene3d.modules if m.label == label)


def test_lower_gap_is_as_high_as_the_top_of_the_lowers_of_its_wall(home):
    walls = wall_3700()
    walls[0]["rows"][0]["modules"][1]["height"] = 800  # Н1 выше остальных
    result = views_of(home, walls)
    assert gap_of(result, "ПМ1").front.height == 900  # ножки 100 + 800, а не 820
    page = next(p for p in api.views_layout(result) if p.wall == "A")
    assert ("верх нижних", 900) in [(lv.name, lv.z) for lv in page.levels]
    box = box_of(result, "ПМ1").boxes[0]
    assert (box.min[2], box.max[2]) == (0, 900)


def test_lower_gap_beside_only_a_tall_module_keeps_the_workshop_height(home):
    walls = [{"label": "A", "length": 1200, "rows": [lower(gap("ПМ1"), mod("П1", "tall_oven", 600))]}]
    result = views_of(home, walls)
    assert gap_of(result, "ПМ1").front.height == 820
    assert box_of(result, "ПМ1").boxes[0].max[2] == 820


def test_gap_depth_is_that_of_the_nearest_module_and_the_deeper_of_two(home):
    result = views_of(home, wall_3700())
    pm1 = gap_of(result, "ПМ1")
    assert pm1.plan.y1 - pm1.plan.y0 == 579  # единственный сосед — Н1
    assert box_of(result, "ПМ1").depth == 579
    assert "579" in box_of(result, "ПМ1").size_label

    walls = [
        {"label": "A", "length": 1800, "rows": [lower(base("Н1") | {"depth": 600}, gap("ПМ1"), base("Н2"))]}
    ]
    between = box_of(views_of(home, walls), "ПМ1")
    assert between.depth == 619  # Н1 619 слева, Н2 579 справа
    assert between.boxes[0].max[1] - between.boxes[0].min[1] == 619


def test_gap_skips_gaps_and_tall_modules_when_looking_for_a_neighbour(home):
    walls = [{"label": "A", "length": 2400, "rows": [
        lower(mod("П1", "tall_oven", 600), gap("ПМ1"), gap("ПМ2"), base("Н1") | {"depth": 600}),
    ]}]  # fmt: skip
    result = views_of(home, walls)
    assert box_of(result, "ПМ1").depth == 619 and box_of(result, "ПМ2").depth == 619


def test_lone_gap_of_a_row_takes_the_wall_row_depth_then_the_workshop(home):
    walls = [{"label": "A", "length": 1200, "rows": [lower(gap("ПМ1"), mod("П1", "tall_oven", 600))]}]
    result = views_of(home, walls)
    tall = next(m for m in result.scene.modules if m.label == "П1")
    assert result.scene.walls[0].depths["lower"] == round(tall.depth)
    assert box_of(result, "ПМ1").depth == round(tall.depth)

    alone = views_of(home, [{"label": "A", "length": 600, "rows": [lower(gap("ПМ1"))]}])
    assert box_of(alone, "ПМ1").depth == 560  # ряда в стене нет — нижний модуль мастерской
