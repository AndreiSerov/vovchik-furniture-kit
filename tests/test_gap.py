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
    return {c.kind: c for c in api.parts_from_project(path, home).common}


def codes(home, path):
    return {f.code for f in api.check_project(path, home).findings}


# --- схема ---------------------------------------------------------------------


def test_gap_entry_parses_and_is_in_the_row_in_place(home):
    walls = [{"label": "A", "length": 1200, "rows": [lower(base("Н1"), gap("ПМ1"))]}]
    quote = api.quote_from_project(write(home, walls), 14, home)
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
        api.check_project(write(home, walls), home)


def test_fractional_gap_width_is_the_whole_millimetres_error(home):
    path = write(home, [{"label": "A", "length": 1200, "rows": [lower(base("Н1"), gap("ПМ1", 600.5))]}])
    [f] = [f for f in api.check_project(path, home).findings if "целое" in f.message]
    assert "ПМ1" in f.message and "600.5" in f.message
    with pytest.raises(OrderError, match="целое число миллиметров"):
        api.parts_from_project(path, home)


# --- сумма ряда -----------------------------------------------------------------


def test_row_sum_counts_gap_and_message_names_gaps(home):
    assert "row_sum" not in codes(home, write(home, wall_3700()))
    walls = wall_3700()
    walls[0]["length"] = 3800
    found = api.check_project(write(home, walls), home).findings
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
    parts = api.parts_from_project(path, home)
    assert {m.module for m in parts.modules} == {"Н1", "Н2", "Н3", "Н4", "Н5", "В1", "В2", "В3", "В4", "В5"}
    api.cut_from_project(path, home)
    api.drilling_from_project(path, home)
    assert "ПМ1" not in {m.label for m in api.assembly_from_project(path, home).modules}
    estimate = api.estimate_from_project(path, home)
    install = next(line for line in estimate.labor.lines if line.operation == "install")
    assert install.quantity == 10
    quote = api.quote_from_project(path, 14, home)
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
    parts = api.parts_from_project(write(home, walls), home)
    corner_parts = next(m.parts for m in parts.modules if m.module == "Н4")
    filler = next(p for p in corner_parts if p.name == "заглушка")
    facade = next(p for p in corner_parts if p.name == "фасад")
    assert filler.position.x < facade.position.x


def test_economy_purchase_and_cutting_skip_gaps(home):
    path = write(home, wall_3700(), led=True)
    api.order_new(ORDER)
    api.import_project("petrov", path, home=home)
    assert api.estimate_from_order("petrov", home, economy=True).price > 0
    purchase = api.purchase_from_order("petrov", home)
    assert purchase.total > 0
    cut = api.cut_from_order("petrov", home)
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
        api.check_project(write(home, walls), home)
    assert "«walls[0].rows[0].modules[0].width»" in str(e.value)
    assert "«walls[0].rows[1].modules[0].width»" in str(e.value)


def test_schema_error_keeps_module_key_outside_row_entries(home):
    walls = [{"label": "A", "length": 600, "rows": [lower(base("Н1"))]}]
    with pytest.raises(OrderError) as e:
        api.check_project(write(home, walls, appliances=[{"type": "oven", "width": 600}]), home)
    assert "«appliances[0].module»" in str(e.value)


def test_cut_json_has_no_gaps_mm(home):
    assert '"gaps_mm"' not in api.cut_from_project(write(home, wall_3700()), home).model_dump_json()


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
    result = api.check_project(path, home)
    [found] = [f for f in result.findings if f.code == "gap_upper_countertop"]
    assert (found.level, found.subject, found.message) == ("warning", "ПМ1", UPPER_COUNTERTOP)
    assert result.errors == []
    assert common(home, path)["countertop"].length_mm == 1200
    api.order_new(ORDER)
    assert api.import_project("petrov", path, home=home).version == 1
    parts = api.parts_from_project(path, home)
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
