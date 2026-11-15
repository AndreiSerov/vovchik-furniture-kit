"""Тикет 19: присадка — отверстия под конфирматы, чашки петель и ответные планки у деталей (через API)."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PETROV = FIXTURES / "petrov_project.json"


def holes(result, purpose=None, surface=None):
    return [
        (p, h)
        for m in result.modules
        for p in m.parts
        for h in p.holes or []
        if (purpose is None or h.purpose == purpose) and (surface is None or h.surface == surface)
    ]


@pytest.fixture(scope="module")
def petrov():
    return api.parts_from_project(PETROV, Path(__file__).resolve().parent.parent)


def test_petrov_confirmat_holes_cups_and_plate_holes_match_hardware(petrov):
    hw = petrov.hardware
    face = holes(petrov, "confirmat", "face")
    end = holes(petrov, "confirmat", "end")
    assert len(face) == len(end) == hw["confirmat_7x50"] == 102
    assert len(holes(petrov, "hinge_cup")) == hw["hinge_71B3550"] == 20
    # МОК: две дырки на ответную планку (`присадка.ответная_планка.отверстий`)
    assert len(holes(petrov, "hinge_plate")) == hw["plate_173L6100"] * 2 == 40


def project_with(path, *modules, kind="lower"):
    """Проект `base_door_project` с заданными модулями одного ряда."""
    project = json.loads(path.read_text(encoding="utf-8"))
    row = project["walls"][0]["rows"][0]
    row["kind"] = kind
    row["modules"] = list(modules)
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def parts_of(result):
    return {p.id: p for m in result.modules for p in m.parts}


def cups(part):
    return sorted(
        (h.from_base for h in part.holes or [] if h.purpose == "hinge_cup"), key=lambda b: (b[1], b[0])
    )


def test_base_door_600_cups_22_from_facade_edge_and_100_from_ends(home, base_door_project):
    """Фасады 716×297 (две двери): по 2 чашки; базовые кромки фасада — нижняя и левая.
    Левая дверь на петлях слева: 22 от левой кромки; правая — справа: 297 − 22 = 275 от левой."""
    p = parts_of(api.parts_from_project(base_door_project, home))
    left, right = p["Н1-фасад-Л"], p["Н1-фасад-П"]
    assert left.base_edges == right.base_edges == ["-z", "-x"]
    assert cups(left) == [(100, 22), (616, 22)]
    assert cups(right) == [(100, 275), (616, 275)]
    for facade in (left, right):
        assert {(h.diameter, h.depth, h.from_face) for h in facade.holes} == {(35, 13, "-y")}


def test_facade_above_900_has_three_cups_middle_exactly_halfway(home, base_door_project):
    """Нижний модуль высотой 1000: фасад 996 > 900 — 3 петли, крайние в 100 от торцов, средняя посередине 498."""
    path = project_with(
        base_door_project, {"label": "Н1", "template": "base_door", "width": 400, "height": 1000}
    )
    result = api.parts_from_project(path, home)
    assert cups(parts_of(result)["Н1-фасад"]) == [(100, 22), (498, 22), (896, 22)]
    assert result.hardware["hinge_71B3550"] == 3


def test_tall_door_cups_follow_hinge_table(home, base_door_project):
    """Пенал: верхняя дверь 798 мм — 2 петли по таблице; пенал выше (2500) — дверь 1198 мм, 3 петли."""
    path = project_with(base_door_project, {"label": "П1", "template": "tall_oven", "width": 600})
    door = parts_of(api.parts_from_project(path, home))["П1-фасад"]
    assert cups(door) == [(100, 22), (698, 22)]
    assert not parts_of(api.parts_from_project(path, home))["П1-фасад-1"].holes  # фасад ящика без петель
    path = project_with(path, {"label": "П1", "template": "tall_oven", "width": 600, "height": 2500})
    result = api.parts_from_project(path, home)
    assert cups(parts_of(result)["П1-фасад"]) == [(100, 22), (599, 22), (1098, 22)]
    assert result.hardware["hinge_71B3550"] == 3


def plated(result):
    """Маркировки деталей с отверстиями ответных планок."""
    return sorted(
        p.id for p in parts_of(result).values() if any(h.purpose == "hinge_plate" for h in p.holes or [])
    )


@pytest.mark.parametrize(
    ("contents", "expected"),
    [(None, ["Н1-бок-Л"]), ({"hinge_side": "left"}, ["Н1-бок-Л"]), ({"hinge_side": "right"}, ["Н1-бок-П"])],
)
def test_single_door_plates_follow_hinge_side(home, base_door_project, contents, expected):
    module = {"label": "Н1", "template": "base_door", "width": 400}
    if contents:
        module["contents"] = contents
    result = api.parts_from_project(project_with(base_door_project, module), home)
    assert plated(result) == expected
    door = parts_of(result)["Н1-фасад"]
    edge = 22 if expected == ["Н1-бок-Л"] else 396 - 22  # фасад 396: чашки у кромки со стороны петель
    assert {b[1] for b in cups(door)} == {edge}


def test_two_doors_plates_on_both_sides(home, base_door_project):
    assert plated(api.parts_from_project(base_door_project, home)) == ["Н1-бок-Л", "Н1-бок-П"]


def test_hinge_side_on_two_doors_or_corner_is_an_error(home, base_door_project):
    two = {"label": "Н1", "template": "base_door", "width": 600, "contents": {"hinge_side": "right"}}
    with pytest.raises(OrderError, match="Н1.*сторона петель"):
        api.parts_from_project(project_with(base_door_project, two), home)
    lift = {"label": "В1", "template": "wall_lift", "width": 600, "contents": {"hinge_side": "left"}}
    with pytest.raises(OrderError, match="В1.*сторона петель"):
        api.parts_from_project(project_with(base_door_project, lift, kind="upper"), home)


def test_corner_hinges_follow_place_in_row(home):
    """Петров: Н4 и В4 — последние в ряду, глухая часть справа, петли слева; первый в ряду — зеркально."""
    petrov = json.loads(PETROV.read_text(encoding="utf-8"))
    path = home / "petrov.json"
    path.write_text(json.dumps(petrov, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(path, home)
    ids = plated(result)
    assert {"Н4-бок-Л", "В4-бок-Л"} <= set(ids) and not {"Н4-бок-П", "В4-бок-П"} & set(ids)
    lower = petrov["walls"][0]["rows"][0]["modules"]
    lower.insert(0, lower.pop())  # Н4 первым в ряду: глухая часть слева, петли справа
    lower[0]["corner"] = {"adjacent_wall": "Б"}
    path.write_text(json.dumps(petrov, ensure_ascii=False), encoding="utf-8")
    ids = plated(api.parts_from_project(path, home))
    assert "Н4-бок-П" in ids and "Н4-бок-Л" not in ids
    corner = {"label": "Н4", "template": "base_corner", "width": 1050, "corner": {"adjacent_wall": "Б"}}
    corner["contents"] = {"hinge_side": "right"}
    lower[0] = corner
    path.write_text(json.dumps(petrov, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError, match="Н4.*углового.*мест"):
        api.parts_from_project(path, home)


def extent(part):
    o = part.orientation
    size = {o.length: part.length, o.width: part.width, o.thickness: part.thickness}
    at = part.position.model_dump()
    return {a: (at[a], at[a] + size[a]) for a in "xyz"}


def test_every_hole_is_inside_its_part(petrov):
    """Устье на своей грани, окружность внутри детали, глубина не больше детали по оси сверления."""
    for part, h in holes(petrov):
        ext, c, axis = extent(part), h.center.model_dump(), h.from_face[1]
        assert c[axis] == (ext[axis][0] if h.from_face[0] == "-" else ext[axis][1]), (part.id, h)
        assert (part.orientation.thickness == axis) == (h.surface == "face"), (part.id, h)
        for a in "xyz":
            if a != axis:
                assert ext[a][0] + h.diameter / 2 <= c[a] <= ext[a][1] - h.diameter / 2, (part.id, a, h)
        assert h.depth <= ext[axis][1] - ext[axis][0], (part.id, h)
        assert not h.through or h.depth == part.thickness


def test_offsets_from_edges_are_not_less_than_the_keys(petrov):
    """Конфирматы: крайние в 50 мм от передней и задней кромки детали (у короткого соединения с одним
    конфирматом — посередине: планка дна вытяжки 40 мм — в 20); чашки — 22 от кромки и не ближе 100 к торцам;
    планки — в 37 от передней кромки боковины."""
    for part, h in holes(petrov, "confirmat", "end"):
        lo, hi = extent(part)["y"]
        front, back = hi - h.center.y, h.center.y - lo
        if hi - lo <= 150:  # одна ступень таблицы — один конфирмат
            assert front == back or abs(front - back) == 1, (part.id, h)
        else:
            assert min(front, back) >= 50 and (front == 50 or back == 50), (part.id, h)
    for part, h in holes(petrov, "hinge_cup"):
        (x0, x1), (z0, z1) = extent(part)["x"], extent(part)["z"]
        assert min(h.center.x - x0, x1 - h.center.x) == 22, part.id
        assert min(h.center.z - z0, z1 - h.center.z) >= 100, part.id
    for part, h in holes(petrov, "hinge_plate"):
        assert extent(part)["y"][1] - h.center.y == 37, part.id


def test_end_hole_axis_meets_face_hole_of_paired_part(petrov):
    """У каждого отверстия в торец есть сквозное в пласти парной детали того же модуля на той же оси."""
    for m in petrov.modules:
        face = {}
        for p in m.parts:
            for h in p.holes or []:
                if h.purpose == "confirmat" and h.surface == "face":
                    axis = h.from_face[1]
                    face[(axis, *(h.center.model_dump()[a] for a in "xyz" if a != axis))] = p.id
        for p in m.parts:
            for h in p.holes or []:
                if h.purpose == "confirmat" and h.surface == "end":
                    axis = h.from_face[1]
                    key = (axis, *(h.center.model_dump()[a] for a in "xyz" if a != axis))
                    assert key in face and face[key] != p.id, (p.id, h)


def signature(part, mirror_axis=None):
    """Отверстия детали без грани сверления; `mirror_axis` — расстояние по этой базовой кромке зеркалится."""
    out = []
    for h in part.holes or []:
        base = list(h.from_base)
        if mirror_axis is not None:
            i = [e[1] for e in part.base_edges].index(mirror_axis)
            base[i] = (part.width if part.orientation.width == mirror_axis else part.length) - base[i]
        out.append((h.purpose, h.surface, h.diameter, h.depth, tuple(base)))
    return sorted(out)


def test_left_and_right_sides_and_facades_are_mirrored(home, base_door_project):
    p = parts_of(api.parts_from_project(base_door_project, home))
    left, right = p["Н1-бок-Л"], p["Н1-бок-П"]
    assert signature(left) == signature(right) and signature(left)
    assert {h.from_face for h in left.holes if h.purpose == "hinge_plate"} == {"+x"}  # внутренняя пласть
    assert {h.from_face for h in right.holes if h.purpose == "hinge_plate"} == {"-x"}
    assert {h.from_face for h in left.holes if h.purpose == "confirmat"} == {"-x"}  # наружная пласть
    assert {h.from_face for h in right.holes if h.purpose == "confirmat"} == {"+x"}
    assert signature(p["Н1-фасад-Л"]) == signature(p["Н1-фасад-П"], mirror_axis="x")


# Все 12 шаблонов: модуль, ряд. Каждый строится, отверстия — только трёх назначений тикета 19.
TEMPLATE_MODULES = [
    ({"label": "П1", "template": "tall_oven", "width": 600}, "lower"),
    ({"label": "Н1", "template": "base_door", "width": 600}, "lower"),
    ({"label": "Н1", "template": "base_corner", "width": 1050, "corner": {"adjacent_wall": "Б"}}, "lower"),
    ({"label": "Н1", "template": "base_sink", "width": 750}, "lower"),
    ({"label": "Н1", "template": "base_bottle", "width": 150}, "lower"),
    ({"label": "Н1", "template": "base_drawers", "width": 600}, "lower"),
    ({"label": "Н1", "template": "dishwasher_panel", "width": 600}, "lower"),
    ({"label": "В1", "template": "wall_door", "width": 600}, "upper"),
    ({"label": "В1", "template": "wall_corner", "width": 600, "corner": {"adjacent_wall": "Б"}}, "upper"),
    ({"label": "В1", "template": "wall_lift", "width": 600}, "upper"),
    ({"label": "В1", "template": "wall_hood", "width": 600}, "upper"),
    ({"label": "В1", "template": "wall_dryer", "width": 750}, "upper"),
]


def two_walls(path):
    project = json.loads(path.read_text(encoding="utf-8"))
    project["walls"].append({"label": "Б", "length": 1800, "rows": []})
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("module", "kind"), TEMPLATE_MODULES, ids=[m["template"] for m, _ in TEMPLATE_MODULES]
)
def test_every_template_builds_and_drills_only_confirmats_and_hinges(home, base_door_project, module, kind):
    result = api.parts_from_project(two_walls(project_with(base_door_project, module, kind=kind)), home)
    found = holes(result)
    assert {h.purpose for _, h in found} <= {"confirmat", "hinge_cup", "hinge_plate"}
    hw = result.hardware
    assert len([1 for _, h in found if h.purpose == "hinge_cup"]) == hw.get("hinge_71B3550", 0)
    assert len([1 for _, h in found if h.purpose == "confirmat" and h.surface == "end"]) == hw.get(
        "confirmat_7x50", 0
    )
    if module["template"] in ("wall_lift", "dishwasher_panel", "base_drawers", "base_bottle"):
        assert not [h for _, h in found if h.purpose == "hinge_cup"]  # подъёмник, ПММ, ящики, бутылочница


def test_lift_facades_have_no_cups_and_hangers_handles_legs_are_not_drilled(petrov):
    """В1 и В5 с подъёмником, Н6 — фасад ПММ: чашек нет. Навесы, ручки, опоры отверстий не дают."""
    p = parts_of(petrov)
    for pid in ("В1-фасад", "В5-фасад", "Н6-фасад"):
        assert not p[pid].holes, pid
    assert {h.purpose for _, h in holes(petrov)} == {"confirmat", "hinge_cup", "hinge_plate"}


def edit_construction(home, old, new):
    path = home / "workshop" / "construction.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_workshop_offsets_move_holes(home, base_door_project):
    edit_construction(home, "отступ_от_кромки_фасада: 22", "отступ_от_кромки_фасада: 24")
    edit_construction(home, "отступ_от_торца: 100", "отступ_от_торца: 110")
    edit_construction(home, "отступ_от_лицевой_кромки: 50", "отступ_от_лицевой_кромки: 60")
    edit_construction(home, "отступ_от_передней_кромки: 37", "отступ_от_передней_кромки: 40")
    p = parts_of(api.parts_from_project(base_door_project, home))
    assert cups(p["Н1-фасад-Л"]) == [(110, 24), (606, 24)]
    bottom = sorted(h.from_base for h in p["Н1-дно"].holes)  # от передней и левой кромок дна
    assert bottom == [(60, 0), (60, 568), (510, 0), (510, 568)]
    assert {h.from_base[1] for h in p["Н1-бок-Л"].holes if h.purpose == "hinge_plate"} == {40}


def test_base_edges_rule_changes_reference_edges(home, base_door_project):
    edit_construction(
        home, "базовые_кромки: [нижняя, передняя, левая]", "базовые_кромки: [верхняя, задняя, правая]"
    )
    p = parts_of(api.parts_from_project(base_door_project, home))
    assert p["Н1-фасад-Л"].base_edges == ["+z", "+x"]
    assert cups(p["Н1-фасад-Л"]) == [(100, 275), (616, 275)]


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("глубина_чашки: 13", "глубина_чашки: 13, лишний: 1", "лишний"),
        ("отступ_от_торца: 100", "отступ_от_торца: 0", "отступ_от_торца"),
        (
            "базовые_кромки: [нижняя, передняя, левая]",
            "базовые_кромки: [нижняя, верхняя, левая]",
            "базовые_кромки",
        ),
    ],
)
def test_bad_drilling_key_is_a_load_error(home, old, new, key):
    edit_construction(home, old, new)
    checked = api.check_workshop(home)
    assert not checked.valid
    assert any("construction.yaml" in e and key in e for e in checked.errors), checked.errors


def test_drilling_keys_are_listed_in_mocks(repo):
    import yaml

    section = yaml.safe_load((repo / "workshop/construction.yaml").read_text(encoding="utf-8"))["присадка"]
    mocks = (repo / "MOCKS.md").read_text(encoding="utf-8")
    keys = {"присадка", *section, *(k for v in section.values() if isinstance(v, dict) for k in v)}
    assert sorted(k for k in keys if k not in mocks) == []


def test_drilling_reconciles_holes_with_hardware(home):
    """Карта присадки Петрова: только детали с отверстиями; сверка с фурнитурой этапа 1 сходится."""
    result = api.drilling_from_project(PETROV, home)
    assert all(p.holes for p in result.parts)
    assert len(result.parts) == len({p.id for p in result.parts})
    checks = {c.name: (c.holes, c.expected) for c in result.checks}
    assert checks == {
        "конфирматы: отверстий в пласть": (102, 102),
        "конфирматы: отверстий в торец": (102, 102),
        "чашки петель": (20, 20),
        "ответные планки: отверстий": (40, 40),
    }
    assert result.mock is True


def test_drilling_refuses_when_holes_do_not_match_hardware(home):
    """Две планки на петлю присадка не размечает: в деталях предупреждение, карта — ошибка сверки."""
    edit_construction(home, "ответные_планки: по одной на петлю", "ответные_планки: по 2 на петлю")
    parts = api.parts_from_project(PETROV, home)
    assert any("ответных планок не размечены" in w for w in parts.warnings)
    with pytest.raises(OrderError, match="ответные планки: отверстий — 0, а нужно 80"):
        api.drilling_from_project(PETROV, home)


def test_plate_holes_are_symmetric_around_cup_height(home, base_door_project):
    """base_door 600: чашки на высоте 2 + 100 = 102 и 2 + 616 = 618 от низа корпуса; отверстия планки — ±16."""
    p = parts_of(api.parts_from_project(base_door_project, home))
    zs = sorted(h.center.z for h in p["Н1-бок-Л"].holes if h.purpose == "hinge_plate")
    assert zs == [86, 118, 602, 634]
    assert sorted(h.center.z for h in p["Н1-фасад-Л"].holes) == [102, 618]


def test_end_hole_is_in_the_middle_of_thickness_and_face_hole_goes_through(home, base_door_project):
    p = parts_of(api.parts_from_project(base_door_project, home))
    ends = [h for h in p["Н1-дно"].holes if h.surface == "end"]
    assert ends and {h.in_thickness for h in ends} == {8}
    assert {h.center.z for h in ends} == {8}  # дно на z=0, толщина 16
    faces = [h for h in p["Н1-бок-Л"].holes if h.purpose == "confirmat"]
    assert faces and all(h.through and h.depth == 16 and h.surface == "face" for h in faces)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("отступ_от_торца: 100", "отступ_от_торца: 400", "Н1-фасад-Л высотой 716 мм ниже двух отступов"),
        ("отступ_от_лицевой_кромки: 50", "отступ_от_лицевой_кромки: 520", "Н1.*короче отступов конфирматов"),
        ("глубина_в_торец: 40", "глубина_в_торец: 600", "Н1.*выходит за деталь"),
        ("глубина_в_торец: 40", "глубина_в_торец: 300", "Н1.*пересекается"),  # встречные в дне 568 мм
        ("шаг: 32, отступ_от_передней_кромки", "шаг: 2, отступ_от_передней_кромки", "Н1.*пересекается"),
    ],
)
def test_impossible_drilling_is_a_module_error(home, base_door_project, old, new, message):
    edit_construction(home, old, new)
    with pytest.raises(OrderError, match=message):
        api.parts_from_project(base_door_project, home)


def test_tiny_module_holes_clash_is_a_module_error(home, base_door_project):
    """W 100: дно 68 мм, встречные отверстия 40 + 40 мм в торцах сходятся — ошибка модуля (DECISIONS)."""
    path = project_with(base_door_project, {"label": "Н1", "template": "base_door", "width": 100})
    with pytest.raises(OrderError, match="Н1.*Н1-дно.*пересекается"):
        api.parts_from_project(path, home)


def test_two_plates_per_hinge_warn_once_and_map_names_the_cause(home):
    edit_construction(home, "ответные_планки: по одной на петлю", "ответные_планки: по 2 на петлю")
    parts = api.parts_from_project(PETROV, home)
    assert len([w for w in parts.warnings if "ответных планок не размечены" in w]) == 1
    with pytest.raises(OrderError, match="Причина: .*планки по 2 на петлю.*не размечает"):
        api.drilling_from_project(PETROV, home)


# Повторное ревью тикета 19: сквозное против глухого и чашка вне фасада — ошибки модуля.
def test_through_confirmat_and_plate_hole_on_one_axis_clash(home, base_door_project):
    edit_construction(home, "отступ_от_лицевой_кромки: 50", "отступ_от_лицевой_кромки: 37")
    edit_construction(home, "отступ_от_торца: 100", "отступ_от_торца: 22")
    path = project_with(base_door_project, {"label": "Н1", "template": "base_door", "width": 450})
    with pytest.raises(OrderError, match="Н1-бок-Л.*пересекается"):
        api.parts_from_project(path, home)


def test_cup_outside_facade_is_a_module_error(home, base_door_project):
    edit_construction(home, "отступ_от_кромки_фасада: 22", "отступ_от_кромки_фасада: 10")
    with pytest.raises(OrderError, match="чашка петли.*выходит за деталь"):
        api.parts_from_project(base_door_project, home)
