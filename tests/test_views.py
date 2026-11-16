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
    # Н1 base_drawers — три фасада ящиков, Н4 — один фасад 450 и глухая часть 598 (от края фасада)
    result = petrov_views(home)
    n1 = next(m for m in result.scene.modules if m.label == "Н1")
    assert len(n1.facades) == 3 and {f.width for f in n1.facades} == {596}
    n4 = next(m for m in result.scene.modules if m.label == "Н4")
    assert [f.width for f in n4.facades] == [450] and n4.blind_width == 598
    # глухая часть справа (Н4 последний в ряду стены A): от края фасада до края модуля (решение мастера, 9)
    assert (n4.blind.x, n4.blind.width) == (2202, 598)
    page = wall_page(api.views_layout(result), "A")
    assert "глухая часть 598" in [label.text for label in page.labels]
    v4 = next(m for m in result.scene.modules if m.label == "В4")
    assert (v4.blind.x, v4.blind.width, v4.blind_width) == (2646, 154, 154)
    for m in (n4, v4):  # край фасада не заходит за глухую часть
        assert max(f.x + f.width for f in m.facades) <= m.blind.x


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


def unmock(home):
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")


def import_v2(home):
    project = json.loads(PETROV.read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    path = home / "v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, path, home=home)


def test_watermark_and_banner_on_every_page_when_mock_and_none_without(home):
    petrov_order(home)
    for text in pages_of(api.views_and_save(ORDER, home)):
        assert text.count(MOCK_BANNER) == 2 and "версия проекта 1" in text  # знак и строка колонтитула
    unmock(home)
    result = api.views_and_save(ORDER, home)
    assert not result.mock
    for text in pages_of(result):
        assert "МОК" not in text and "версия проекта 1" in text


def test_output_is_registered_and_becomes_stale_after_new_version(home):
    petrov_order(home)
    api.views_and_save(ORDER, home)
    outputs = [o for o in api.list_outputs(ORDER, home) if o.kind == "views"]
    assert [(o.path, o.source_version) for o in outputs] == [(f"orders/{ORDER}/out/kitchen-views.pdf", 1)]
    import_v2(home)
    status = api.status(ORDER, home)
    assert [s.command for s in status.stale if s.kind == "views"] == [f"kitchen 3d {ORDER}"]
    assert "виды кухни" in status.text
    api.views_and_save(ORDER, home)
    assert not [s for s in api.status(ORDER, home).stale if s.kind == "views"]


def test_from_order_writes_nothing(home):
    petrov_order(home)
    result = api.views_from_order(ORDER, home)
    assert result.output is None and result.source.version == 1
    assert not (home / "orders" / ORDER / "out" / "kitchen-views.pdf").exists()
    assert api.list_outputs(ORDER, home) == []


def test_pdf_is_deterministic_for_one_date(home):
    from datetime import date

    petrov_order(home)
    first = Path(api.views_and_save(ORDER, home, on=date(2026, 11, 15)).output.file).read_bytes()
    second = Path(api.views_and_save(ORDER, home, on=date(2026, 11, 15)).output.file).read_bytes()
    assert first == second


def test_project_with_check_errors_is_drawn_with_a_banner_on_every_page(home):
    # сумма ряда не сходится: стена A длиннее модулей на 100 мм — ошибка проверок, виды рисуются
    data = json.loads(PETROV.read_text(encoding="utf-8"))
    data["walls"][0]["length"] = 2900
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = api.views_from_project(path, home)
    errors = len(result.errors)
    assert errors >= 1
    banner = f"Проверки: {errors} {'ошибка' if errors == 1 else 'ошибки'} — сверьте проект"
    for page in api.views_layout(result):
        assert banner in [label.text for label in page.labels], page.title
    assert all(banner not in [lb.text for lb in p.labels] for p in api.views_layout(petrov_views(home)))


def test_placement_error_refuses_with_its_text_and_writes_no_file(home):
    from tests.test_placement import petrov

    petrov_order(home)
    # верх пенала 2800 выше потолка 2700: проверки проходят, расстановка нет
    project = petrov(П1={"height": 2700})
    path = home / "v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert api.import_project(ORDER, path, home=home).version == 2
    result = runner.invoke(app, ["3d", "petrov"])
    assert result.exit_code != 0
    assert "Верх верхних модулей 2800 мм выше потолка 2700 мм." in result.output
    assert not (home / "orders" / ORDER / "out" / "kitchen-views.pdf").exists()


def test_corner_overlap_refuses_the_views(home):
    from tests.test_placement import corner, mod, write

    walls = [
        {"label": "A", "length": 1650, "rows": [
            {"kind": "lower", "modules": [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")]},
            {"kind": "upper", "modules": [mod("В1", "wall_door", 1650)]},
        ]},
        {"label": "Б", "length": 1800, "rows": [
            {"kind": "lower", "modules": [mod("Н5", "base_door", 1200)]},
            {"kind": "upper", "modules": [mod("В5", "wall_door", 1800)]},
        ]},
    ]  # fmt: skip
    import pytest
    from kitchen.errors import OrderError

    with pytest.raises(OrderError) as e:
        api.views_from_project(write(home, walls), home)
    assert e.value.problems == [
        "В1 (стена A) и В5 (стена Б) пересекаются в углу: нужен верхний угловой модуль или отступ верхнего ряда"
    ]


def test_cli_prints_tall_heights_warning_after_the_checks_line(home):
    from tests.test_placement import mod, write

    walls = [{"label": "A", "length": 1800, "rows": [
        {"kind": "lower", "modules": [
            mod("П1", "tall_oven", 600, height=2100), mod("П2", "tall_oven", 600, height=2300),
            mod("Н1", "base_door", 600),
        ]},
        {"kind": "upper", "modules": [mod("В1", "wall_door", 600)]},
    ]}]  # fmt: skip
    result = runner.invoke(app, ["3d", "--project", str(write(home, walls))])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    checks = next(i for i, line in enumerate(lines) if line.startswith("Проверки:"))
    assert lines[checks + 1] == "Пеналы разной высоты: П1 2100 мм, П2 2300 мм — верх верхних по П2 (2400 мм)."


def banner_on_every_page(result, errors):
    pages = api.views_layout(result)
    assert pages
    assert all(any(f"Проверки: {errors} " in lb.text for lb in p.labels) for p in pages)


def test_views_draw_a_project_whose_module_does_not_build(home):
    from tests.test_placement import petrov

    path = home / "p.json"
    path.write_text(
        json.dumps(petrov(Н3={"width": 100}, Н4={"width": 1100}), ensure_ascii=False), encoding="utf-8"
    )
    assert api.placement_from_project(path, home).findings == []
    assert [f.code for f in api.check_project(path, home).errors] == ["module_build"]
    result = api.views_from_project(path, home)
    assert [f.code for f in result.errors] == ["module_build"]
    assert all(any("Проверки: 1 ошибка" in lb.text for lb in p.labels) for p in api.views_layout(result))
    n3 = next(m for m in result.scene.modules if m.label == "Н3")
    assert n3.facades == []  # модуль не построился — голая коробка


def test_views_draw_unknown_template_and_too_small_part_as_bare_boxes(home):
    from tests.test_placement import petrov

    for edit, code in (({"template": "base_foo"}, "module_build"), ({"width": 50}, "part_too_small")):
        data = petrov(Н2=edit) if "template" in edit else petrov(Н3=edit)
        path = home / "p.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        checked = api.check_project(path, home)
        assert code in [f.code for f in checked.errors], code
        assert api.placement_from_project(path, home).findings == []  # расстановка проходит, отказа нет
        result = api.views_from_project(path, home)
        assert code in [f.code for f in result.errors]
        bare = [m.label for m in result.scene.modules if not m.facades and not m.tall and m.row == "lower"]
        assert bare, code
        assert ("Н2" if "template" in edit else "Н3") in bare
        banner_on_every_page(result, len(result.errors))


def test_cli_3d_project_with_a_module_that_does_not_build_succeeds_and_names_the_error(home):
    # импорт такой проект отклоняет; у заказа он возникает после правки мастерской (тест ниже)
    from tests.test_placement import petrov

    path = home / "p.json"
    path.write_text(
        json.dumps(petrov(Н3={"width": 100}, Н4={"width": 1100}), ensure_ascii=False), encoding="utf-8"
    )
    result = runner.invoke(app, ["3d", "--project", str(path)])
    assert result.exit_code == 0, result.output
    assert "Проверки: 1 ошибка" in result.output


def test_gap_fronts_follow_their_row(home):
    pm1 = next(g for g in petrov_views(home).scene.gaps if g.label == "ПМ1")
    assert (pm1.front.x, pm1.front.z, pm1.front.width, pm1.front.height) == (336, 1480, 264, 720)


def test_low_of_uppers_is_the_lowest_upper_on_the_wall(home):
    from tests.test_placement import petrov

    path = home / "p.json"
    path.write_text(json.dumps(petrov(В1={"height": 900}), ensure_ascii=False), encoding="utf-8")
    page = next(p for p in api.views_layout(api.views_from_project(path, home)) if p.wall == "A")
    assert ("низ верхних", 1300) in [(lv.name, lv.z) for lv in page.levels]


def test_blind_part_of_a_corner_first_in_its_row_is_on_the_left(home):
    from tests.test_placement import corner, mod, wall, write

    walls = [
        wall(
            "A", 2250, [mod("Н1", "base_door", 600), mod("Н2", "base_door", 450), mod("Н3", "base_door", 600)]
        ),
        wall("Б", 2250, [corner("Н4", "base_corner", 1050, "A"), mod("Н5", "base_door", 1200)]),
    ]
    n4 = next(m for m in api.views_from_project(write(home, walls), home).scene.modules if m.label == "Н4")
    assert n4.blind_width == 598 and n4.blind.x == 0
    assert min(f.x for f in n4.facades) == 598


def test_plan_row_depth_is_the_deepest_module_of_the_row_with_facade(home):
    from tests.test_placement import petrov

    path = home / "p.json"
    path.write_text(json.dumps(petrov(Н1={"depth": 600}), ensure_ascii=False), encoding="utf-8")
    plan = api.views_layout(api.views_from_project(path, home))[0]
    texts = [lb.text for lb in plan.labels]
    assert "глубина: нижние 619, верхние 339" in texts  # стена A: 600 + 19 фасад, остальные 579
    assert "глубина: нижние 579, верхние 339" in texts  # стена Б без правок


def test_views_of_a_clean_project_keep_the_parts_warnings(home):
    assert api.views_from_project(PETROV, home).warnings == ["Н5: без задней стенки"]


def test_only_the_module_that_does_not_build_loses_its_facades(home):
    from tests.test_placement import petrov

    clean = {m.label: m for m in api.views_from_project(PETROV, home).scene.modules}
    path = home / "p.json"
    path.write_text(
        json.dumps(petrov(Н3={"width": 100}, Н4={"width": 1100}), ensure_ascii=False), encoding="utf-8"
    )
    broken = {m.label: m for m in api.views_from_project(path, home).scene.modules}
    assert [label for label, m in broken.items() if not m.facades] == ["Н3"]
    for label in ("Н1", "Н5", "В5", "В6"):
        assert broken[label].facades == clean[label].facades, label
    assert broken["Н4"].blind_width == 648  # от края фасада, не «ширина минус фасады»


def test_order_views_draw_a_module_that_stopped_building_after_a_workshop_edit(home):
    petrov_order(home)
    yaml = home / "workshop" / "construction.yaml"
    text = yaml.read_text(encoding="utf-8")
    assert "макс_ширина: 150}" in text
    yaml.write_text(text.replace("макс_ширина: 150}", "макс_ширина: 100}"), encoding="utf-8")
    result = api.views_and_save(ORDER, home)
    assert [(f.code, f.subject) for f in result.errors] == [("module_build", "Н3")]
    assert result.output is not None
    assert next(m for m in result.scene.modules if m.label == "Н3").facades == []


def test_views_and_save_lays_pages_out_once(home, monkeypatch):
    """Тикет 47: раскладка страниц считается в `_views` один раз; запись PDF берёт её же."""
    petrov_order(home)
    calls = []
    real = api.views_page_layout
    monkeypatch.setattr(api, "views_page_layout", lambda *a, **k: calls.append(1) or real(*a, **k))
    api.views_and_save(ORDER, home)
    assert len(calls) == 1


def test_views_layout_of_a_result_read_back_from_json_lays_the_pages_out_again(home):
    result = petrov_views(home)
    again = api.ViewsResult.model_validate_json(result.model_dump_json())
    assert [p.model_dump() for p in api.views_layout(again)] == [
        p.model_dump() for p in api.views_layout(result)
    ]


def test_pdf_page_of_wall_a_names_both_blind_parts_by_the_facade_edge(home):
    petrov_order(home)
    text = " ".join(pages_of(api.views_and_save(ORDER, home))[1].split())  # узкая подпись переносится
    assert "глухая часть 598" in text and "глухая часть 154" in text


def test_petrov_upper_gap_is_as_deep_as_its_upper_neighbour_on_the_plan_and_in_3d(home):
    result = petrov_views(home)
    pm1 = next(g for g in result.scene.gaps if g.label == "ПМ1")
    assert pm1.plan.x0 == 2461 and pm1.plan.x1 - pm1.plan.x0 == 339  # у стены Б, развёрнутой по оси x
    box = next(m for m in result.scene3d.modules if m.label == "ПМ1")
    assert box.depth == 339 and box.size_label == "Ш×В×Г 264×720×339 мм"
    assert box.caption == "ПМ1 — пустое место, 264 мм"
    assert (box.boxes[0].min[0], box.boxes[0].max[0]) == (2461, 2800)


def test_camera_looks_at_half_of_the_tallest_box_of_the_scene(home):
    assert petrov_views(home).scene3d.camera.target == [2800, 0, 1100]  # пенал 2200
    from tests.test_placement import mod, wall, write

    lowers = [mod(f"Н{i}", "base_door", 600) for i in range(1, 4)]
    scene = api.views_from_project(write(home, [wall("A", 1800, lowers)]), home).scene3d
    assert max(b.max[2] for m in scene.modules for b in m.boxes) == 820
    assert scene.camera.target == [900, 0, 410]  # одна стена — её середина; половина верха нижних
    assert long_wall(home).scene3d.camera.target[2] == 1100  # верхние до 2200 — половина их верха
