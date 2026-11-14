"""Раскрой (2.1): гильотинная укладка деталей по листам через `kitchen.api`."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
KERF, TRIM = 4, 10  # из workshop/equipment.yaml (мок)


def set_sheet(home, size):
    """Уменьшает лист всех листовых материалов, чтобы небольшой набор деталей занял несколько листов."""
    path = home / "workshop/materials.yaml"
    path.write_text(path.read_text().replace("лист: [2800, 2070]", f"лист: {list(size)}"))


def write_project(home, mutate):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    mutate(project)
    path = home / "p.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def materials(result):
    return {m.material: m for m in result.materials}


def apart(a, b):
    """Две детали разведены по одной из осей не менее чем на пропил."""
    return (
        a.x + a.w + KERF <= b.x
        or b.x + b.w + KERF <= a.x
        or a.y + a.h + KERF <= b.y
        or b.y + b.h + KERF <= a.y
    )


def test_base_door_is_cut_per_sheet_material_without_facades(home, base_door_project):
    result = api.cut_from_project(base_door_project, home)
    by = materials(result)
    assert set(by) == {"ldsp_16_white", "hdf_3_white"}  # фасад МДФ в раскрой не идёт, ХДФ отдельно от ЛДСП
    parts = {p.id: p for p in api.parts_from_project(base_door_project, home).modules[0].parts}
    placed = {pl.part for m in result.materials for s in m.sheets for pl in s.placements}
    assert placed == {pid for pid, p in parts.items() if p.material != "mdf_enamel_matt"}
    assert result.mock is True and result.source.kind == "file"


def test_pieces_are_inside_working_field_and_do_not_overlap_with_kerf(home):
    set_sheet(home, (2800, 1200))
    path = write_project(home, lambda p: None)
    result = api.cut_from_project(path, home)
    ldsp = materials(result)["ldsp_16_white"]
    assert ldsp.sheet_count > 1 and ldsp.sheet_count == len(ldsp.sheets)
    sizes = {p.id: p for m in api.parts_from_project(path, home).modules for p in m.parts}
    for sheet in ldsp.sheets:
        for pl in sheet.placements:
            assert pl.x >= TRIM and pl.y >= TRIM, pl.part
            assert pl.x + pl.w <= 2800 - TRIM and pl.y + pl.h <= 1200 - TRIM, pl.part
            part = sizes[pl.part]
            assert sorted((pl.w, pl.h)) == sorted((part.length, part.width)), pl.part
        for i, a in enumerate(sheet.placements):
            for b in sheet.placements[i + 1 :]:
                assert apart(a, b), (a.part, b.part)


def test_textured_material_is_never_rotated_and_grain_runs_along_x(home):
    path = write_project(home, lambda p: p["materials"].update(body="ldsp_16_oak"))
    result = api.cut_from_project(path, home)
    oak = materials(result)["ldsp_16_oak"]
    assert oak.texture is True and oak.sheet_size == (2800, 2070)
    sizes = {p.id: p for m in api.parts_from_project(path, home).modules for p in m.parts}
    placements = [pl for s in oak.sheets for pl in s.placements]
    assert placements and not any(pl.rotated for pl in placements)
    assert all((pl.w, pl.h) == (sizes[pl.part].length, sizes[pl.part].width) for pl in placements)


def test_untextured_material_rotates_when_the_sheet_demands_it(home):
    set_sheet(home, (800, 2800))  # узкий лист: длинные детали встают только поперёк первой оси
    result = api.cut_from_project(write_project(home, lambda p: None), home)
    placements = [pl for s in materials(result)["ldsp_16_white"].sheets for pl in s.placements]
    assert any(pl.rotated for pl in placements)
    assert all(pl.x + pl.w <= 800 - TRIM and pl.y + pl.h <= 2800 - TRIM for pl in placements)


def test_part_larger_than_working_field_is_an_error(home):
    set_sheet(home, (2100, 2070))  # поле 2080: боковина пенала 2100 мм не влезает ни так, ни так
    with pytest.raises(OrderError, match=r"П1-бок-Л.*2100.*не помещается"):
        api.cut_from_project(write_project(home, lambda p: None), home)


def replay_cuts(placements, cuts, size):
    """Независимый проигрыватель резов: каждый рез идёт от края до края куска (пропил может лечь на уже
    существующий пропил); в конце каждая деталь лежит в своём куске ровно по размеру, куски без деталей —
    отход. Возвращает число кусков с деталями."""
    pieces = [(TRIM, TRIM, size[0] - TRIM, size[1] - TRIM)]  # x0, y0, x1, y1
    for n, cut in enumerate(cuts, 1):
        assert cut.number == n and cut.length == cut.end - cut.start
        for i, (x0, y0, x1, y1) in enumerate(pieces):
            if cut.direction == "vertical":
                hit = cut.position < x1 and cut.position + KERF > x0 and (cut.start, cut.end) == (y0, y1)
            else:
                hit = cut.position < y1 and cut.position + KERF > y0 and (cut.start, cut.end) == (x0, x1)
            if hit:
                break
        else:
            pytest.fail(f"рез {n} {cut} не идёт от края до края ни одного куска")
        pieces.pop(i)
        if cut.direction == "vertical":
            halves = [
                (x0, y0, max(x0, cut.position), y1),
                (max(x0, min(cut.position + KERF, x1)), y0, x1, y1),
            ]
        else:
            halves = [
                (x0, y0, x1, max(y0, cut.position)),
                (x0, max(y0, min(cut.position + KERF, y1)), x1, y1),
            ]
        pieces += [h for h in halves if h[2] > h[0] and h[3] > h[1]]
    holders = 0
    for pl in placements:
        inside = [
            p for p in pieces if p[0] <= pl.x and p[1] <= pl.y and pl.x + pl.w <= p[2] and pl.y + pl.h <= p[3]
        ]
        assert len(inside) == 1, pl.part
        assert (inside[0][2] - inside[0][0], inside[0][3] - inside[0][1]) == (pl.w, pl.h), pl.part
    for p in pieces:
        n = sum(
            p[0] <= pl.x and pl.x + pl.w <= p[2] and p[1] <= pl.y and pl.y + pl.h <= p[3] for pl in placements
        )
        assert n <= 1
        holders += n
    return holders


def replay(sheet, size):
    return replay_cuts(sheet.placements, sheet.cuts, size)


def test_gap_not_larger_than_kerf_still_gets_a_trim_cut():
    """Зазор перед деталью 1…пропил (здесь 4 мм после реза): деталь всё равно освобождается резом."""
    from kitchen.cutting import Placement, cut_order

    def pl(name, x, y, w, h):
        return Placement(part=name, x=x, y=y, w=w, h=h, rotated=False)

    for placements in (
        [pl("a", 10, 10, 100, 100), pl("b", 118, 10, 50, 100)],
        [pl("a", 14, 14, 100, 100)],
        [pl("a", 12, 10, 100, 100)],
        [pl("a", 10, 10, 100, 100), pl("b", 10, 117, 100, 50)],
    ):
        cuts = cut_order(placements, (300, 200), KERF, TRIM)
        assert replay_cuts(placements, cuts, (300, 200)) == len(placements), placements


def test_part_two_mm_from_field_edge_comes_out_exactly_nominal():
    """Полоса тоньше пропила у края поля (2 мм) всё равно режется: иначе деталь выйдет больше номинала."""
    from kitchen.cutting import Placement, cut_order

    size = (2800, 2070)  # поле: x 10…2790, y 10…2060
    for pl in (
        Placement(part="a", x=12, y=10, w=100, h=100, rotated=False),  # слева 2 мм
        Placement(part="a", x=10, y=10, w=2778, h=100, rotated=False),  # справа 2 мм
        Placement(part="a", x=10, y=12, w=100, h=100, rotated=False),  # сверху 2 мм
        Placement(part="a", x=10, y=10, w=100, h=2048, rotated=False),  # снизу 2 мм
    ):
        cuts = cut_order([pl], size, KERF, TRIM)
        assert cuts, pl
        assert replay_cuts([pl], cuts, size) == 1  # проигрыватель строгий: деталь ровно своего размера


def test_cut_order_is_guillotine_and_frees_every_part(home):
    set_sheet(home, (2800, 1200))
    result = api.cut_from_project(write_project(home, lambda p: None), home)
    for m in result.materials:
        for sheet in m.sheets:
            assert sheet.cuts, "у листа с деталями есть резы"
            assert replay(sheet, m.sheet_size) == len(sheet.placements)
            assert all(c.length <= result.max_cut_length for c in sheet.cuts)


def test_pinwheel_layout_is_an_engine_error():
    """Пять деталей «мельницей» (четыре по кругу и одна в центре) не режутся от края до края."""
    from kitchen.cutting import Placement, cut_order
    from kitchen.errors import KitchenError

    def pl(name, x, y, w, h):
        return Placement(part=name, x=x, y=y, w=w, h=h, rotated=False)

    pinwheel = [
        pl("a", 0, 0, 600, 300),
        pl("b", 604, 0, 296, 600),
        pl("c", 304, 604, 596, 296),
        pl("d", 0, 304, 300, 596),
        pl("e", 304, 304, 296, 296),
    ]
    with pytest.raises(KitchenError, match="гильотинно"):
        cut_order(pinwheel, (900, 900), KERF, 0)


def test_cut_longer_than_machine_maximum_is_an_error(home):
    path = home / "workshop/equipment.yaml"
    path.write_text(path.read_text().replace("макс_длина_реза: 3200", "макс_длина_реза: 1500"))
    with pytest.raises(OrderError, match=r"длиннее максимума станка 1500"):
        api.cut_from_project(write_project(home, lambda p: None), home)


def test_result_is_deterministic(home):
    path = write_project(home, lambda p: None)
    first = api.cut_from_project(path, home).model_dump_json()
    assert api.cut_from_project(path, home).model_dump_json() == first


def test_countertop_is_cut_linearly_apart_from_sheet_parts(home):
    result = api.cut_from_project(write_project(home, lambda p: None), home)
    top = result.countertop
    assert (top.item, top.sheet_length, top.sheet_count) == ("top_38_marble", 4100, 1)
    assert top.sheets == [[2200, 1200]]  # нижние ряды 2200 и 1200 мм без пенала; 2200 + 4 + 1200 ≤ 4100
    assert "top_38_marble" not in materials(result)  # в листовой раскрой столешница не попадает


def test_without_countertop_there_is_no_countertop_cut(home):
    path = write_project(home, lambda p: p["materials"].pop("countertop"))
    assert api.cut_from_project(path, home).countertop is None


@pytest.fixture
def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    return home


def test_petrov_cut_numbers_and_source(petrov):
    """Эталон по моку, ПРОВЕРИТЬ МАСТЕРУ (DECISIONS.md): ЛДСП 71 деталь 18.111 м² — 4 листа (нижняя граница по
    площади 3.12), ХДФ 11 деталей 5.2302 м² — 2 листа (один лист 5.796 м² не вмещает их гильотинно)."""
    result = api.cut_from_order(ORDER, petrov)
    assert (result.source.kind, result.source.order, result.source.version) == ("order", ORDER, 1)
    assert result.mock is True and (result.kerf, result.trim, result.max_cut_length) == (4, 10, 3200)
    by = materials(result)
    assert list(by) == ["ldsp_16_white", "hdf_3_white"]
    ldsp, hdf = by["ldsp_16_white"], by["hdf_3_white"]
    assert (ldsp.sheet_count, ldsp.parts_count, ldsp.parts_area_m2) == (4, 71, 18.111)
    assert (hdf.sheet_count, hdf.parts_count, hdf.parts_area_m2) == (2, 11, 5.2302)
    assert ldsp.utilization == pytest.approx(18.111 / (4 * 2.8 * 2.07), abs=1e-4)
    assert ldsp.waste_m2 == pytest.approx(4 * 2.8 * 2.07 - 18.111, abs=1e-3)
    parts = [p for m in api.parts_from_order(ORDER, petrov).modules for p in m.parts]
    expected = {p.id for p in parts if p.material != "mdf_enamel_matt"}
    assert {pl.part for m in result.materials for s in m.sheets for pl in s.placements} == expected
    for m in result.materials:
        for sheet in m.sheets:
            assert replay(sheet, m.sheet_size) == len(sheet.placements)
            for i, a in enumerate(sheet.placements):
                assert all(apart(a, b) for b in sheet.placements[i + 1 :])


def test_no_project_yet_is_an_error(home):
    api.order_new(ORDER, home=home)
    with pytest.raises(OrderError, match="проекта ещё нет"):
        api.cut_from_order(ORDER, home)


def test_sheet_trimming_cut_is_checked_against_machine_maximum(home):
    path = home / "workshop/equipment.yaml"
    path.write_text(path.read_text().replace("макс_длина_реза: 3200", "макс_длина_реза: 2790"))
    with pytest.raises(OrderError, match=r"подрезка листа 2800 мм.*2790"):
        api.cut_from_project(write_project(home, lambda p: None), home)


def test_every_candidate_layout_replays_as_guillotine(home):
    """Все раскладки всех эвристик `_layouts` (не только выбранные) режутся и проигрываются: Петров и
    наборы случайных размеров с фиксированным seed."""
    import random

    from kitchen import cutting
    from kitchen.parts import compute_parts
    from kitchen.project import parse_project
    from kitchen.workshop import load_workshop

    ws = load_workshop(home)
    modules = compute_parts(
        parse_project(json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))), ws
    ).modules
    template = modules[0].parts[0]
    sets = []
    for material in ("ldsp_16_white", "hdf_3_white"):
        sets.append({p.id: p for m in modules for p in m.parts if p.material == material})
    rng = random.Random(12)
    for _ in range(12):
        parts = {}
        for i in range(rng.randint(3, 20)):
            a = rng.choice([rng.randint(40, 2700), rng.choice([100, 320, 560, 568, 596, 716, 720])])
            b = rng.randint(40, min(a, 2000))
            parts[f"p{i:02}"] = template.model_copy(update={"id": f"p{i:02}", "length": a, "width": b})
        sets.append(parts)
    size, field = (2800, 2070), (2780, 2050)
    checked = 0
    for parts in sets:
        for name, layout in cutting._layouts(parts, field, KERF, True):
            for rects in layout:
                placements = [cutting._real(r, parts[r.id], KERF, TRIM) for r in rects]
                cuts = cutting.cut_order(placements, size, KERF, TRIM)
                assert replay_cuts(placements, cuts, size) == len(placements), name
                checked += 1
    assert checked > 5000


def test_output_is_identical_across_hash_seeds(home):
    import os
    import subprocess
    import sys

    path = write_project(home, lambda p: None)
    outs = []
    for seed in ("0", "1"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "KITCHEN_HOME": str(home)}
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "from kitchen.cli import app; app()",
                "cut",
                "--project",
                str(path),
                "--json",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        outs.append(r.stdout)
    assert outs[0] == outs[1] and '"materials"' in outs[0]


def test_material_texture_forbids_rotation_of_all_its_parts(home):
    """Текстурный ХДФ «вдоль 2070»: задняя стенка пенала 2096 мм не влезает вдоль 2070 — и проверка проекта,
    и раскрой называют деталь по маркировке (раньше раскрой падал без маркировки)."""
    path = home / "workshop/materials.yaml"
    path.write_text(
        path.read_text().replace("текстура: false, толщина: 3", "текстура: вдоль 2070, толщина: 3")
    )
    project = write_project(home, lambda p: None)
    with pytest.raises(OrderError, match="П1-зад"):
        api.cut_from_project(project, home)
    found = api.check_project(project, home).errors
    assert [f.subject for f in found if f.code == "part_exceeds_sheet"] == ["П1-зад"]
