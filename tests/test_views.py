"""Тикет 41: виды кухни в PDF — план сверху и вид на каждую стену спереди (`kitchen 3d`).

Логика — через API (`api.views_from_project`, `api.views_from_order`, `api.views_layout`, `api.views_and_save`),
PDF читается `pypdf`."""

import json
from pathlib import Path

from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from pypdf import PdfReader
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PETROV = FIXTURES / "petrov_project.json"
ORDER = "2026-047-petrov"
runner = CliRunner()
MM = 72 / 25.4


def petrov_order(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, PETROV, home=home)


def pages_of(result):
    return [p.extract_text() for p in PdfReader(result.output.file).pages]


def petrov_labels():
    data = json.loads(PETROV.read_text(encoding="utf-8"))
    return {w["label"]: [m["label"] for row in w["rows"] for m in row["modules"]] for w in data["walls"]}


def test_petrov_views_have_plan_and_a_page_per_wall_with_its_labels(home):
    petrov_order(home)
    result = api.views_and_save(ORDER, home)
    assert result.output.file == str(home / "orders" / ORDER / "out" / "kitchen-views.pdf")
    pages = pages_of(result)
    labels = petrov_labels()
    assert len(pages) == 1 + len(labels) == result.pages
    assert "План сверху" in pages[0]
    for label in (m for ms in labels.values() for m in ms):
        assert label in pages[0], label
    for text, (wall, modules) in zip(pages[1:], labels.items(), strict=True):
        length = {"A": 2800, "Б": 1800}[wall]
        assert f"Стена {wall}, {length} мм — вид спереди" in text
        for label in modules:
            assert label in text, (wall, label)


def test_cli_order_prints_source_checks_and_views_line_and_json_is_the_api_result(home):
    petrov_order(home)
    result = runner.invoke(app, ["3d", "petrov"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    body = [line for line in lines if line and line != MOCK_BANNER]
    assert body[0].startswith(f"Источник: заказ {ORDER}, версия 1 проекта")
    warnings = len(api.views_from_order(ORDER, home).check_warnings)
    assert warnings == 3  # предупреждения проверок Петрова с брифом
    assert body[1] == "Проверки: 0 ошибок, 3 предупреждения"
    path = home / "orders" / ORDER / "out" / "kitchen-views.pdf"
    assert body[2] == f"Виды PDF: {path} (стен 2, страниц 3)"
    data = json.loads(runner.invoke(app, ["3d", "petrov", "--json"]).output)
    expected = json.loads(api.views_from_order(ORDER, home).model_dump_json())
    assert data["scene"] == expected["scene"] and data["findings"] == expected["findings"]
    assert data["mock"] is True and data["output"]["kind"] == "views" and data["pages"] == 3


def test_cli_project_writes_no_files_and_order_with_project_is_an_error(home):
    project = home / "p.json"
    project.write_text(PETROV.read_text(encoding="utf-8"), encoding="utf-8")
    result = runner.invoke(app, ["3d", "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert "Файл не записан (--project)" in result.output
    assert (
        json.loads(runner.invoke(app, ["3d", "--project", str(project), "--json"]).output)["output"] is None
    )
    assert not (home / "orders").exists()
    for args in (["3d"], ["3d", "petrov", "--project", str(project)]):
        bad = runner.invoke(app, args)
        assert bad.exit_code != 0 and "либо заказ" in bad.output


def petrov_views(home, **edits):
    """Виды проекта Петрова из файла; `edits` — правки модулей по метке, как в `tests.test_placement.petrov`."""
    from tests.test_placement import petrov

    path = home / "p.json"
    path.write_text(json.dumps(petrov(**edits), ensure_ascii=False), encoding="utf-8")
    return api.views_from_project(path, home)


def wall_page(pages, wall):
    return next(p for p in pages if p.kind == "wall" and p.wall == wall)


def test_width_chains_of_each_wall_page_add_up_to_the_wall_length(home):
    pages = api.views_layout(petrov_views(home))
    a, b = wall_page(pages, "A"), wall_page(pages, "Б")
    # A: П1 600 входит в оба ряда; Б: смещение нижнего 600 и верхнего 336, ПМ1 264 (тикет 50)
    assert {c.row: c.values for c in a.chains} == {
        "lower": [600, 600, 400, 150, 1050],
        "upper": [600, 600, 600, 400, 600],
    }
    assert {c.row: c.values for c in b.chains} == {"lower": [600, 750, 450], "upper": [336, 264, 750, 450]}
    for page, length in ((a, 2800), (b, 1800)):
        texts = [label.text for label in page.labels]
        for chain in page.chains:
            assert sum(chain.values) == length
            for value in chain.values:
                assert str(value) in texts
        assert str(length) in texts  # длина стены общей стрелкой


def test_heights_from_the_floor_follow_the_tall_height_of_the_workshop(home):
    from tests.test_placement import edit

    edit(home, "construction.yaml", "пенал: {высота: 2100", "пенал: {высота: 2300")
    page = wall_page(api.views_layout(petrov_views(home)), "A")
    assert [(lv.name, lv.z) for lv in page.levels] == [
        ("", 0),
        ("ножки", 100),
        ("верх нижних", 820),
        ("низ верхних", 1680),
        ("верх верхних", 2400),
        ("потолок", 2700),
    ]
    texts = [label.text for label in page.labels]
    assert "2400 верх верхних" in texts and "2700 потолок" in texts and "0" in texts


def facade_fills(page):
    return {s.fill for s in page.shapes if s.kind == "facade"}


def test_facade_fill_comes_from_the_ral_table_and_unknown_colour_is_grey_and_named(home):
    # Петров: нижние «RAL 7044» (серый шёлк, CAC4B0), верхние «RAL 9003» (F4F4F4)
    page = wall_page(api.views_layout(petrov_views(home)), "Б")
    assert facade_fills(page) == {(0xCA, 0xC4, 0xB0), (0xF4, 0xF4, 0xF4)}
    malina = petrov_views(home, Н5={"materials": {"facade_color": "малиновый"}})
    page = wall_page(api.views_layout(malina), "Б")
    assert (0xB0, 0xB0, 0xB0) in facade_fills(page)
    assert "малиновый" in [label.text for label in page.labels]
    assert "RAL 7044" not in [label.text for label in page.labels]  # распознанный цвет не подписывается


def test_facades_are_the_facade_parts_of_the_module(home):
    # Н1 base_drawers — три фасада ящиков, Н4 — один фасад 450 и глухая часть 600
    result = petrov_views(home)
    n1 = next(m for m in result.scene.modules if m.label == "Н1")
    assert len(n1.facades) == 3 and {f.width for f in n1.facades} == {596}
    n4 = next(m for m in result.scene.modules if m.label == "Н4")
    assert [f.width for f in n4.facades] == [450] and n4.blind_width == 600
    assert n4.blind.x == 1750 + 1050 - 600  # глухая часть справа: Н4 последний в ряду стены A
    page = wall_page(api.views_layout(result), "A")
    assert "глухая часть 600" in [label.text for label in page.labels]


def test_plan_names_the_gap_with_its_appliance_and_hatches_it(home):
    data = json.loads(PETROV.read_text(encoding="utf-8"))
    data["walls"][1]["rows"][0]["modules"].append({"label": "ПМ2", "gap": True, "width": 600})
    data["walls"][1]["length"] = 2400
    data["walls"][1]["rows"][1]["modules"].append({"label": "ПМ3", "gap": True, "width": 600})
    data["appliances"].append({"type": "fridge", "width": 580, "module": "ПМ2"})
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pages = api.views_layout(api.views_from_project(path, home))
    plan = pages[0]
    assert "ПМ2 холодильник" in [label.text for label in plan.labels]
    assert sum(s.kind == "gap" and s.hatch for s in plan.shapes) == 3
    assert "холодильник" in [label.text for label in wall_page(pages, "Б").labels]


def assert_labels_fit(pages):
    # A4 альбомная 297×210 мм, поля 10 мм, колонтитул 9 мм над нижним полем
    left, right, bottom, top = 10 * MM, 287 * MM, 19 * MM, 200 * MM
    for page in pages:
        boxes = [(label.text, label.box()) for label in page.labels]
        for text, (x0, y0, x1, y1) in boxes:
            assert left <= x0 and x1 <= right + 1e-6, (page.title, text)
            assert bottom <= y0 and y1 <= top + 1e-6, (page.title, text)
        for i, (t1, a) in enumerate(boxes):
            for t2, b in boxes[i + 1 :]:
                eps = 1e-6  # строки одного блока касаются рамками
                apart = a[2] <= b[0] + eps or b[2] <= a[0] + eps or a[3] <= b[1] + eps or b[3] <= a[1] + eps
                assert apart, (page.title, t1, t2)


def u_shaped(home):
    from tests.test_placement import corner, mod, write

    walls = [
        {"label": "A", "length": 1650, "rows": [
            {"kind": "lower", "modules": [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")]},
            {"kind": "upper", "modules": [mod("В1", "wall_door", 1650)]},
        ]},
        {"label": "Б", "length": 2400, "rows": [
            {"kind": "lower", "modules": [
                mod("Н5", "base_door", 450), mod("Н6", "base_bottle", 150), mod("Н9", "base_door", 150),
                corner("Н7", "base_corner", 1050, "В"),
            ]},
        ]},
        {"label": "В", "length": 1800, "rows": [{"kind": "lower", "modules": [mod("Н8", "base_door", 1200)]}]},
    ]  # fmt: skip
    return api.views_from_project(write(home, walls), home)


def long_wall(home):
    from tests.test_placement import mod, write

    lower = [mod(f"Н{i}", "base_door", 150 if i % 2 else 450) for i in range(1, 21)]
    upper = [mod(f"В{i}", "wall_door", 300) for i in range(1, 21)]
    return api.views_from_project(write(home, [{"label": "A", "length": 6000, "rows": [
        {"kind": "lower", "modules": lower}, {"kind": "upper", "modules": upper},
    ]}]), home)  # fmt: skip


def test_views_layout_keeps_every_label_inside_the_sheet_and_apart(home):
    for result in (petrov_views(home), u_shaped(home), long_wall(home)):
        pages = api.views_layout(result)
        assert len(pages) == 1 + len(result.scene.walls)
        assert_labels_fit(pages)
