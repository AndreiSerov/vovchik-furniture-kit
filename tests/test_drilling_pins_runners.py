"""Тикет 20: присадка — ряды отверстий под полкодержатели и отверстия направляющих Tandembox (через API)."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PETROV = FIXTURES / "petrov_project.json"


def project_with(path, *modules, kind="lower"):
    project = json.loads(path.read_text(encoding="utf-8"))
    row = project["walls"][0]["rows"][0]
    row["kind"] = kind
    row["modules"] = list(modules)
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def parts_of(result):
    return {p.id: p for m in result.modules for p in m.parts}


def of(part, purpose):
    return [h for h in part.holes or [] if h.purpose == purpose]


def rows(part):
    """Ряды полкодержателей: расстояние от передней кромки -> высоты от нижней кромки по порядку."""
    out: dict[int, list[int]] = {}
    for h in of(part, "shelf_pin"):
        bottom, front = h.from_base  # базовые кромки боковины — нижняя и передняя
        out.setdefault(front, []).append(bottom)
    return {k: sorted(v) for k, v in sorted(out.items())}


# Полка на 300: центр отверстия под неё на радиус штыря ниже (МОК 2.5), по два шага выше и ниже.
R300 = [233.5, 265.5, 297.5, 329.5, 361.5]
R600 = [533.5, 565.5, 597.5, 629.5, 661.5]


def test_one_shelf_rows_of_five_32_apart_centred_on_shelf_height(home, base_door_project):
    """base_door 600×720×560, полка 0…540 на 300: на каждой боковине спереди (МОК 37 от передней кромки полки,
    560 − 540 + 37 = 57 от передней кромки боковины) и сзади (37 от задней, 560 − 37 = 523 от передней)
    по 5 отверстий Ø5 с шагом 32; среднее — на 300 − 2.5 (верх штыря Ø5 — низ полки)."""
    p = parts_of(api.parts_from_project(base_door_project, home))
    for side in ("Н1-бок-Л", "Н1-бок-П"):
        assert rows(p[side]) == {57: R300, 523: R300}, side
        assert {(h.diameter, h.surface) for h in of(p[side], "shelf_pin")} == {(5, "face")}
    assert {h.from_face for h in of(p["Н1-бок-Л"], "shelf_pin")} == {"+x"}  # внутренние пласти
    assert {h.from_face for h in of(p["Н1-бок-П"], "shelf_pin")} == {"-x"}


def test_two_shelves_each_get_rows_and_no_shelves_get_none(home, base_door_project):
    module = {"label": "Н1", "template": "base_door", "width": 600, "contents": {"shelves": 2}}
    p = parts_of(api.parts_from_project(project_with(base_door_project, module), home))
    expected = R300 + R600  # полки на 300 и 600
    assert rows(p["Н1-бок-Л"]) == rows(p["Н1-бок-П"]) == {57: expected, 523: expected}
    module["contents"] = {"shelves": 0}
    p = parts_of(api.parts_from_project(project_with(base_door_project, module), home))
    assert not [h for part in p.values() for h in of(part, "shelf_pin")]


def test_rows_on_both_sides_are_mirrored(home, base_door_project):
    """Те же расстояния от нижней и передней кромок, сверлят с внутренних пластей — зеркально."""
    p = parts_of(api.parts_from_project(base_door_project, home))
    left, right = (sorted(h.from_base for h in of(p[s], "shelf_pin")) for s in ("Н1-бок-Л", "Н1-бок-П"))
    assert left == right and len(left) == 10


def test_row_keys_move_holes(home, base_door_project):
    edit_construction(
        home,
        "отверстий_в_ряду: 5, шаг: 32, отступ_от_передней_кромки_полки: 37, отступ_от_задней_кромки_полки: 37, "
        "центр_ниже_полки: 2.5",
        "отверстий_в_ряду: 3, шаг: 25, отступ_от_передней_кромки_полки: 50, отступ_от_задней_кромки_полки: 40, "
        "центр_ниже_полки: 3",
    )
    p = parts_of(api.parts_from_project(base_door_project, home))
    # спереди 20 + 50 = 70 от передней кромки боковины, сзади 560 − 40 = 520; центр 300 − 3
    assert rows(p["Н1-бок-Л"]) == {70: [272, 297, 322], 520: [272, 297, 322]}


def test_front_row_follows_the_shelf_front_gap(home, base_door_project):
    """Полка утоплена на 60 (0…500): передний ряд — в 37 от её кромки, 60 + 37 = 97 от кромки боковины."""
    edit_construction(home, "отступ_спереди: 20", "отступ_спереди: 60")
    p = parts_of(api.parts_from_project(base_door_project, home))
    assert rows(p["Н1-бок-Л"]) == {97: R300, 523: R300}


def test_row_at_the_roof_shifts_down_by_whole_steps(home, base_door_project):
    """wall_door 960, 3 полки (300, 600, 900): ряд верхней полки 833.5…961.5 заходит за боковину высотой 960 и
    под крышу (944–960); он сдвигается вниз на один шаг — 801.5…929.5, отверстие под полку (897.5) остаётся
    в ряду, отверстий по-прежнему 5 (решение мастера)."""
    module = {"label": "В1", "template": "wall_door", "width": 600, "height": 960, "contents": {"shelves": 3}}
    p = parts_of(api.parts_from_project(project_with(base_door_project, module, kind="upper"), home))
    top = [801.5, 833.5, 865.5, 897.5, 929.5]
    full = R300 + R600
    assert rows(p["В1-бок-Л"]) == rows(p["В1-бок-П"]) == {57: full + top, 283: full + top}


def test_row_at_the_bottom_shifts_up_by_whole_steps(home, base_door_project):
    """Полка на 50: ряд −16.5…111.5; −16.5 за боковиной, 15.5 (окружность 13–18) заходит под дно 0–16 —
    ряд сдвигается вверх на два шага: 47.5…175.5."""
    edit_construction(home, "высота: 300, шаг: 300", "высота: 50, шаг: 300")
    p = parts_of(api.parts_from_project(base_door_project, home))
    up = [47.5, 79.5, 111.5, 143.5, 175.5]
    assert rows(p["Н1-бок-Л"]) == {57: up, 523: up}


def test_shelf_hole_under_the_bottom_is_a_module_error(home, base_door_project):
    """Полка на 10: отверстие под полку само под дном — никакой сдвиг ряда его не спасает."""
    edit_construction(home, "высота: 300, шаг: 300", "высота: 10, шаг: 300")
    with pytest.raises(OrderError, match="Н1: ряд полкодержателей.*Н1-бок-Л.*Н1-полка"):
        api.parts_from_project(base_door_project, home)


def edit_construction(home, old, new):
    path = home / "workshop" / "construction.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def plane_distance(part, a, b):
    axis = part.orientation.thickness
    ca, cb = a.center.model_dump(), b.center.model_dump()
    return sum((ca[k] - cb[k]) ** 2 for k in "xyz" if k != axis) ** 0.5


CROWDED = [
    ({"label": "Н1", "template": "base_door", "width": 600, "contents": {"shelves": 2}}, "lower"),
    (
        {"label": "В1", "template": "wall_door", "width": 600, "height": 960, "contents": {"shelves": 3}},
        "upper",
    ),
    ({"label": "П1", "template": "tall_oven", "width": 600}, "lower"),
    ({"label": "Н1", "template": "base_drawers", "width": 600}, "lower"),
]


@pytest.mark.parametrize(("module", "kind"), CROWDED, ids=[m["template"] for m, _ in CROWDED])
def test_pin_and_runner_holes_do_not_meet_confirmat_or_plate_holes(home, base_door_project, module, kind):
    """Каждое отверстие полкодержателя и направляющей — не ближе суммы радиусов к любому другому отверстию
    в пласти той же детали (конфирматы сквозные, планки и ряды — с одной внутренней пласти)."""
    result = api.parts_from_project(project_with(base_door_project, module, kind=kind), home)
    checked = 0
    for part in parts_of(result).values():
        face = [h for h in part.holes or [] if h.surface == "face"]
        for i, a in enumerate(face):
            for b in face[i + 1 :]:
                if {a.purpose, b.purpose} & {"shelf_pin", "runner"}:
                    checked += 1
                    assert plane_distance(part, a, b) >= (a.diameter + b.diameter) / 2, (part.id, a, b)
    assert checked


def test_pin_row_on_the_plate_line_is_a_module_error(home, base_door_project):
    """Ряд на одной линии с планками (оба в 37 от передней кромки боковины: 20 + 17): полка на 600, отверстия
    597.5 и 629.5 против планок 602 и 634 — сходятся, это ошибка модуля, а не молча пропущенное отверстие."""
    edit_construction(home, "отступ_от_передней_кромки_полки: 37", "отступ_от_передней_кромки_полки: 17")
    module = {"label": "Н1", "template": "base_door", "width": 600, "contents": {"shelves": 2}}
    with pytest.raises(OrderError, match="Н1-бок-Л.*пересекается"):
        api.parts_from_project(project_with(base_door_project, module), home)


def runners(part):
    """Отверстия направляющих: (от нижней кромки, от передней) по порядку."""
    return sorted(h.from_base for h in of(part, "runner"))


def at_heights(*zs):
    return sorted((z, y) for z in zs for y in (37, 261, 453))


@pytest.mark.parametrize(
    ("module", "sets", "heights"),
    [
        # фасады 286, 286, 140 от z = 2, 290, 578: дно ящика на Z + 16 — 18, 306, 594; отверстия на 6 выше
        ({"label": "Н1", "template": "base_drawers", "width": 600}, 3, (24, 312, 600)),
        ({"label": "П1", "template": "tall_oven", "width": 600}, 1, (24,)),  # ящик пенала: фасад от 2
    ],
    ids=["base_drawers_600", "tall_oven_600"],
)
def test_runner_holes_match_tandembox_sets_at_drawer_heights(home, base_door_project, module, sets, heights):
    """По 3 отверстия (МОК: 37, 261, 453 от передней кромки) на каждой боковине: 6 на комплект Tandembox."""
    result = api.parts_from_project(project_with(base_door_project, module), home)
    p = parts_of(result)
    label = module["label"]
    assert result.hardware["tandembox_M_500"] == sets
    found = [h for part in p.values() for h in of(part, "runner")]
    assert len(found) == sets * 2 * 3
    for side, face in ((f"{label}-бок-Л", "+x"), (f"{label}-бок-П", "-x")):
        assert runners(p[side]) == at_heights(*heights), side
        assert {(h.from_face, h.diameter, h.depth) for h in of(p[side], "runner")} == {(face, 5, 13)}


def test_runner_keys_move_holes(home, base_door_project):
    edit_construction(
        home,
        "отверстия_от_передней_кромки: [37, 261, 453], над_дном_ящика: 6",
        "отверстия_от_передней_кромки: [40, 300], над_дном_ящика: 10",
    )
    module = {"label": "П1", "template": "tall_oven", "width": 600}
    p = parts_of(api.parts_from_project(project_with(base_door_project, module), home))
    assert runners(p["П1-бок-Л"]) == [(28, 40), (28, 300)]


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("отверстий_в_ряду: 5", "отверстий_в_ряду: 4", "отверстий_в_ряду"),
        (
            "отступ_от_задней_кромки_полки: 37",
            "отступ_от_задней_кромки_полки: 0",
            "отступ_от_задней_кромки_полки",
        ),
        ("центр_ниже_полки: 2.5", "центр_ниже_полки: -1", "центр_ниже_полки"),
        ("центр_ниже_полки: 2.5", "центр_ниже_полки: 2.55", "центр_ниже_полки"),
        ("[37, 261, 453]", "[261, 37]", "отверстия_от_передней_кромки"),
        ("[37, 261, 453]", "[]", "отверстия_от_передней_кромки"),
        ("над_дном_ящика: 6", "над_дном_ящика: -1", "над_дном_ящика"),
        (
            "над_дном_ящика: 6, диаметр: 5, глубина: 13",
            "над_дном_ящика: 6, диаметр: 5, глубина: 13, лишний: 1",
            "лишний",
        ),
    ],
)
def test_bad_pin_or_runner_key_is_a_load_error(home, old, new, key):
    edit_construction(home, old, new)
    checked = api.check_workshop(home)
    assert not checked.valid
    assert any("construction.yaml" in e and key in e for e in checked.errors), checked.errors


def test_petrov_pin_and_runner_holes_reconcile_with_hardware(home):
    """Петров: 6 полок × 4 полкодержателя = 24, по 5 отверстий на полкодержатель — 120; Tandembox 4 комплекта
    (Н1 — 3 ящика, П1 — 1) × 6 отверстий — 24. Сверки тикета 19 — прежние."""
    result = api.drilling_from_project(PETROV, home)
    checks = {c.name: (c.holes, c.expected) for c in result.checks}
    assert checks == {
        "конфирматы: отверстий в пласть": (102, 102),
        "конфирматы: отверстий в торец": (102, 102),
        "чашки петель": (20, 20),
        "ответные планки: отверстий": (40, 40),
        "полкодержатели: отверстий": (120, 120),
        "направляющие: отверстий": (24, 24),
    }
    parts = api.parts_from_project(PETROV, home)
    assert parts.hardware["shelf_pin_5"] == 24 and parts.hardware["tandembox_M_500"] == 4


def test_pins_per_shelf_other_than_four_rows_refuse_the_map(home):
    """Присадка ставит 4 ряда на полку (по два на боковину); 6 полкодержателей на полку — расхождение
    с закупкой, карта отказывает и называет причину."""
    edit_construction(home, "полкодержателей: 4", "полкодержателей: 6")
    with pytest.raises(OrderError, match=r"полкодержатели: отверстий — 120, а нужно 180.*Причина: .*4 ряда"):
        api.drilling_from_project(PETROV, home)


def test_summary_line_names_every_check(home):
    """Строка сверки в шапке карты — полная или короткая — называет все сверки с числами, ни одна не теряется.
    Длинные имена не помещаются — идёт короткая строка, и в ней тоже все шесть (короткое имя — часть сверки)."""
    result = api.drilling_from_project(PETROV, home)
    assert len(result.checks) == 6
    summary = api.drilling_map_layout(result)[0].header[1].text
    for c in result.checks:
        assert (
            f"{c.name} {c.holes} = {c.expected}" in summary or f"{c.short} {c.holes}={c.expected}" in summary
        )
    long = [
        c.model_copy(update={"name": c.name + " (очень длинное пояснение сверки)"}) for c in result.checks
    ]
    summary = api.drilling_map_layout(result.model_copy(update={"checks": long}))[0].header[1].text
    assert summary.startswith("Сверка: ")
    for c in result.checks:
        assert f"{c.short} {c.holes}={c.expected}" in summary, (c.short, summary)


def table(scheme):
    return [t.text for t in scheme.texts[4:]]  # после заголовка, подзаголовка, вида и сверления


def test_map_table_shows_a_row_of_holes_as_one_line(home):
    """Петров: ряд полкодержателей — одна строка таблицы с номерами всех пяти отверстий, крайними
    расстояниями и шагом; отверстия направляющей — одна строка со списком расстояний. Отметок на схеме —
    по-прежнему по одной на отверстие."""
    result = api.drilling_from_project(PETROV, home)
    by_id = {p.id: p for p in result.parts}
    schemes = {s.part: s for page in api.drilling_map_layout(result) for s in page.schemes}
    rows_ = table(schemes["Н2-бок-Л"])
    pin_row = "полкодержатель · пласть +x · Ø5×10 · 233.5…361.5 шаг 32"
    assert any(r.endswith(f"{pin_row} · 57") for r in rows_), rows_  # ряд не сдвинут — без пометки
    assert any(r.endswith(f"{pin_row} · 523") for r in rows_), rows_
    assert len(schemes["Н2-бок-Л"].holes) == len(by_id["Н2-бок-Л"].holes)
    pins = [i for i, h in enumerate(by_id["Н2-бок-Л"].holes, 1) if h.purpose == "shelf_pin"]
    assert any(r.startswith(f"{pins[0]}–{pins[4]}. полкодержатель") for r in rows_), rows_
    runner = table(schemes["Н1-бок-П"])
    assert any(r.endswith("направляющая · пласть -x · Ø5×13 · 24 · 37, 261, 453") for r in runner), runner


def test_row_outside_the_shelf_depth_is_a_module_error_naming_the_keys(home, base_door_project):
    """Передний ряд в 600 от передней кромки полки глубиной 540 — за полкой; ошибка называет ключи."""
    edit_construction(home, "отступ_от_передней_кромки_полки: 37", "отступ_от_передней_кромки_полки: 600")
    with pytest.raises(
        OrderError,
        match="Н1: ряд полкодержателей на боковине Н1-бок-Л .*не под полкой Н1-полка.*"
        "отступ_от_передней_кромки_полки.*полка.отступ_спереди",
    ):
        api.parts_from_project(base_door_project, home)


def test_shifted_row_marks_the_hole_that_holds_the_shelf(home, base_door_project):
    """Карта wall_door 960: у сдвинутого ряда верхней полки таблица называет отверстие под полку."""
    module = {"label": "В1", "template": "wall_door", "width": 600, "height": 960, "contents": {"shelves": 3}}
    result = api.drilling_from_project(project_with(base_door_project, module, kind="upper"), home)
    schemes = {s.part: s for page in api.drilling_map_layout(result) for s in page.schemes}
    rows_ = table(schemes["В1-бок-Л"])
    shifted = [r for r in rows_ if "801.5…929.5 шаг 32" in r]
    assert len(shifted) == 2 and all(r.endswith(" · под полку 897.5") for r in shifted), rows_
    assert not [r for r in rows_ if "под полку" in r and "801.5" not in r], rows_
    side = next(p for p in result.parts if p.id == "В1-бок-Л")
    held = [h.from_base[0] for h in side.holes if h.purpose == "shelf_pin" and h.holds_shelf]
    assert sorted(held) == [297.5, 297.5, 597.5, 597.5, 897.5, 897.5]  # по одному на ряд


def test_runner_hole_does_not_go_under_the_cabinet_bottom(home, base_door_project):
    """Ревью: над_дном_ящика 0 — отверстие нижнего ящика на 18 (окружность 15.5–20.5) заходит под дно 0–16."""
    edit_construction(home, "над_дном_ящика: 6", "над_дном_ящика: 0")
    module = {"label": "Н1", "template": "base_drawers", "width": 600}
    with pytest.raises(OrderError, match="Н1.*направляющая.*Н1-бок-Л.*под деталь Н1-дно"):
        api.parts_from_project(project_with(base_door_project, module), home)


@pytest.mark.parametrize(
    "change",
    [{"diameter": 8}, {"depth": 12}, {"purpose": "runner"}, {"from_face": "-x"}, {"from_base": (200, 57)}],
    ids=["diameter", "depth", "purpose", "face", "not_increasing"],
)
def test_table_does_not_merge_holes_that_differ(home, change):
    """Ревью: строка-ряд только у одинаковых отверстий с растущим расстоянием; одно отличное — ряд рвётся."""
    result = api.drilling_from_project(PETROV, home)
    side = next(p for p in result.parts if p.id == "Н2-бок-Л")
    pins = [i for i, h in enumerate(side.holes) if h.purpose == "shelf_pin"][:5]
    holes = list(side.holes)
    holes[pins[2]] = holes[pins[2]].model_copy(update=change)
    crowded = result.model_copy(update={"parts": [side.model_copy(update={"holes": holes})]})
    rows_ = table(api.drilling_map_layout(crowded)[0].schemes[0])
    assert not any(r.startswith(f"{pins[0] + 1}–{pins[4] + 1}.") for r in rows_), rows_
    assert not any(r.startswith(f"{pins[0] + 1}–") for r in rows_), rows_  # и двух отверстий ряд не бывает


def test_runner_holes_per_set_follow_the_distance_list(home):
    """Две позиции направляющей вместо трёх: Петров — 4 комплекта × 2 × 2 = 16, сверка сходится."""
    edit_construction(
        home, "отверстия_от_передней_кромки: [37, 261, 453]", "отверстия_от_передней_кромки: [37, 261]"
    )
    checks = {c.name: (c.holes, c.expected) for c in api.drilling_from_project(PETROV, home).checks}
    assert checks["направляющие: отверстий"] == (16, 16)


def test_row_hole_touching_the_bottom_stays(home, base_door_project):
    """Полка на 85: нижнее отверстие ряда 18.5 касается верха дна (16) окружностью — касание не мешает,
    ряд не сдвигается."""
    edit_construction(home, "высота: 300, шаг: 300", "высота: 85, шаг: 300")
    p = parts_of(api.parts_from_project(base_door_project, home))
    row = [18.5, 50.5, 82.5, 114.5, 146.5]
    assert rows(p["Н1-бок-Л"]) == {57: row, 523: row}
