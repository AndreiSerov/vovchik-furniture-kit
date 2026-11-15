"""Тикет 34: проект и шаблоны отклоняют неверный ввод находкой, а не трейсбеком и не отрицательными деталями."""

import json
import re
from collections import Counter

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import KitchenError
from kitchen.parts import BACK_PANEL, TEMPLATES, Template, TemplateOut
from kitchen.project import Project
from typer.testing import CliRunner

runner = CliRunner()
MATERIALS = {
    "body": "ldsp_16_white",
    "facades_lower": "mdf_enamel_matt",
    "facades_upper": "mdf_enamel_matt",
    "handles": "handle_profile",
}


def write(home, rows, length, order="2026-047-petrov", walls=None):
    data = {
        "order": order,
        "ceiling_height": 2700,
        "walls": walls or [{"label": "A", "length": length, "rows": rows}],
        "materials": MATERIALS,
    }
    path = home / "project.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def lower(*modules):
    return [{"kind": "lower", "modules": list(modules)}]


def codes(result):
    return [f.code for f in result.errors]


# S1. Неизвестный шаблон
def test_unknown_template_is_a_finding_not_a_keyerror(home):
    path = write(home, lower({"label": "Н1", "template": "foo", "width": 600}), 600)
    result = api.check_project(path, home)
    assert codes(result) == ["module_build"]
    assert "неизвестный шаблон" in result.errors[0].message


def test_unknown_template_does_not_hide_the_other_modules(home):
    modules = [
        {"label": "Н1", "template": "foo", "width": 600},
        {"label": "Н2", "template": "base_drawers", "width": 600, "depth": 400},
    ]
    result = api.check_project(write(home, lower(*modules), 1200), home)
    assert sorted(f.subject for f in result.errors) == ["Н1", "Н2"]


def test_import_of_unknown_template_is_rejected_in_russian(home):
    api.order_new("2026-047-petrov", "Петров", home=home)
    path = write(home, lower({"label": "Н1", "template": "foo", "width": 600}), 600)
    with pytest.raises(KitchenError, match="неизвестный шаблон"):
        api.import_project("petrov", path, home=home)


def test_cli_unknown_template_import_and_parts(home):
    api.order_new("2026-047-petrov", "Петров", home=home)
    path = write(home, lower({"label": "Н1", "template": "foo", "width": 600}), 600)
    for args in (["import", "petrov", str(path)], ["parts", "--project", str(path)]):
        r = runner.invoke(app, args)
        assert r.exit_code == 1, args
        assert "неизвестный шаблон" in r.output
        assert "Traceback" not in r.output
        assert not isinstance(r.exception, KeyError)


def test_schema_template_is_the_registry(repo):
    schema = json.loads((repo / "engine/kitchen/schemas/project.schema.json").read_text())
    assert schema["$defs"]["ModuleSpec"]["properties"]["template"]["enum"] == sorted(TEMPLATES)
    assert Project.model_json_schema() == schema


# S2. Размеры
@pytest.mark.parametrize(
    "module",
    [
        {"label": "Н1", "template": "base_door", "width": 30},
        {"label": "Н1", "template": "base_door", "width": 600, "depth": 10},
        {"label": "Н1", "template": "dishwasher_panel", "width": 2},
        {"label": "Н1", "template": "base_drawers", "width": 600, "height": 150},
    ],
)
def test_nonpositive_part_sizes_are_an_error(home, module):
    path = write(home, lower(module), module["width"])
    result = api.check_project(path, home)
    assert codes(result) == ["module_build"]
    with pytest.raises(KitchenError):
        api.parts_from_project(path, home)


def test_nonpositive_part_error_names_part_and_size(home):
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 30}), 30)
    message = api.check_project(path, home).errors[0].message
    assert "Н1" in message and "-2" in message


@pytest.mark.parametrize("key", ["width", "height", "depth"])
@pytest.mark.parametrize("value", [0, -100])
def test_schema_rejects_nonpositive_module_size(home, key, value):
    module = {"label": "Н1", "template": "base_door", "width": 600, key: value}
    path = write(home, lower(module), 600)
    with pytest.raises(KitchenError, match="Н1|width|height|depth|больше"):
        api.check_project(path, home)


def test_schema_rejects_nonpositive_niche(home):
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 600}), 600)
    data = json.loads(path.read_text())
    data["appliances"] = [{"type": "oven", "width": 600, "module": "Н1", "niche_height": 0}]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(KitchenError):
        api.check_project(path, home)


def test_schema_file_has_minimums(repo):
    defs = json.loads((repo / "engine/kitchen/schemas/project.schema.json").read_text())["$defs"]
    spec = defs["ModuleSpec"]["properties"]
    assert spec["width"]["exclusiveMinimum"] == 0 and "gt" not in spec["width"]
    assert spec["shelves"]["minimum"] == 0


# S3. Полки
@pytest.mark.parametrize("shelves", [-1, -5])
def test_negative_legacy_shelves_rejected(home, shelves):
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 600, "shelves": shelves}), 600)
    with pytest.raises(KitchenError):
        api.check_project(path, home)
    with pytest.raises(KitchenError):
        api.parts_from_project(path, home)


# Инвариант: количество фурнитуры не меньше нуля
def test_negative_hardware_is_an_error(home, monkeypatch):
    original = TEMPLATES["base_door"]

    def build(*args):
        out = original.build(*args)
        out.hardware = Counter({"shelf_pin_5": -4})
        return out

    monkeypatch.setitem(TEMPLATES, "base_door", Template(build, "lower"))
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 600}), 600)
    assert codes(api.check_project(path, home)) == ["module_build"]
    with pytest.raises(KitchenError, match="shelf_pin_5"):
        api.parts_from_project(path, home)


def test_negative_total_hardware_is_an_error_even_if_the_checks_were_skipped(home, monkeypatch):
    def build(module, dims, materials, ws, ctx):
        return TemplateOut(hardware=Counter({"shelf_pin_5": -1}))

    monkeypatch.setitem(TEMPLATES, "base_door", Template(build, "lower"))
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 600}), 600)
    with pytest.raises(KitchenError, match="shelf_pin_5"):
        api.parts_from_project(path, home)


# S4. Метки
def test_check_project_reports_duplicate_labels(home):
    path = write(
        home,
        lower(
            {"label": "Н1", "template": "base_door", "width": 600, "height": 3000},
            {"label": "Н1", "template": "base_door", "width": 400},
        ),
        1000,
    )
    result = api.check_project(path, home)
    assert [f.subject for f in result.errors if f.code == "duplicate_label"] == ["Н1"]
    assert any(f.code == "part_exceeds_sheet" and f.subject.startswith("Н1") for f in result.errors)


def test_tall_written_in_two_rows_is_a_duplicate_label(home):
    rows = [
        {"kind": "lower", "modules": [{"label": "П1", "template": "tall_oven", "width": 600}]},
        {"kind": "upper", "modules": [{"label": "П1", "template": "tall_oven", "width": 600}]},
    ]
    result = api.check_project(write(home, rows, 600), home)
    assert [f.subject for f in result.errors if f.code == "duplicate_label"] == ["П1"]


def test_import_rejects_duplicate_labels_with_the_same_finding(home):
    api.order_new("2026-047-petrov", "Петров", home=home)
    modules = [
        {"label": "Н1", "template": "base_door", "width": 600},
        {"label": "Н1", "template": "base_door", "width": 400},
    ]
    path = write(home, lower(*modules), 1000)
    message = next(f.message for f in api.check_project(path, home).errors if f.code == "duplicate_label")
    with pytest.raises(KitchenError) as e:
        api.import_project("petrov", path, home=home)
    assert message in e.value.problems


# S5. Пеналы в разных рядах
def talls_in_two_rows(home):
    rows = [
        {"kind": "lower", "modules": [{"label": "П1", "template": "tall_oven", "width": 600}]},
        {"kind": "upper", "modules": [{"label": "П2", "template": "tall_oven", "width": 600}]},
    ]
    return write(home, rows, 1200)


def test_two_talls_in_different_rows_fill_both_rows(home):
    path = talls_in_two_rows(home)
    assert [f for f in api.check_project(path, home).findings if f.code == "row_sum"] == []
    result = api.parts_from_project(path, home)
    assert [f for f in result.findings if f.code == "row_sum"] == []
    assert next(c for c in result.common if c.kind == "plinth").length_mm == 1200


def test_one_tall_and_two_talls_in_one_row_as_before(home):
    one = write(home, lower({"label": "П1", "template": "tall_oven", "width": 600}), 600)
    assert [f for f in api.check_project(one, home).findings if f.code == "row_sum"] == []
    assert next(c for c in api.parts_from_project(one, home).common if c.kind == "plinth").length_mm == 600
    two = write(
        home,
        lower(
            {"label": "П1", "template": "tall_oven", "width": 600},
            {"label": "П2", "template": "tall_oven", "width": 600},
        ),
        1200,
    )
    assert [f for f in api.check_project(two, home).findings if f.code == "row_sum"] == []
    assert next(c for c in api.parts_from_project(two, home).common if c.kind == "plinth").length_mm == 1200


# T1. Ящик Tandembox
def test_drawer_does_not_fit_has_one_text_for_base_drawers_and_tall(home):
    drawers = write(
        home, lower({"label": "Н1", "template": "base_drawers", "width": 120, "height": 150}), 120
    )
    base = api.check_project(drawers, home).errors[0].message
    assert "ящик не помещается" in base
    assert any(ch.isdigit() for ch in base.split("не помещается", 1)[1])
    # пенал: габариты проекта до ящика не доходят (ниши не вмещаются раньше), низкий фасад ящика — в мастерской
    path = home / "workshop" / "construction.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("высота_фасада_ящика: 280", "высота_фасада_ящика: 50"),
        encoding="utf-8",
    )
    tall = write(home, lower({"label": "П1", "template": "tall_oven", "width": 600}), 600)
    tall_message = api.check_project(tall, home).errors[0].message
    assert "ящик не помещается" in tall_message
    # одна формулировка: после метки текст до «ящик не помещается» совпадает
    shape = lambda text: re.sub(r"\d+", "N", text.split(":", 1)[1])
    assert shape(base) == shape(tall_message)


# Сетка: шаблон × габариты
SIZES = (1, 2, 30, 100, 150, 300, 450, 600, 900, 1200, 3000)
GRID = {"width": SIZES, "height": (1, 2, 100, 300, 720, 3000), "depth": (1, 2, 100, 300, 560, 3000)}


def grid_modules(template):
    for w in GRID["width"]:
        for h in GRID["height"]:
            for d in GRID["depth"]:
                module = {"label": f"M{w}_{h}_{d}", "template": template, "width": w, "height": h, "depth": d}
                if template in ("base_corner", "wall_corner"):
                    module["corner"] = {"adjacent_wall": "Б"}
                yield module


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_grid_of_sizes_gives_error_or_sound_parts(home, template):
    row_kind = "upper" if TEMPLATES[template].row_kind == "upper" else "lower"
    modules = list(grid_modules(template))
    walls = [
        {"label": "A", "length": 1, "rows": [{"kind": row_kind, "modules": modules}]},
        {"label": "Б", "length": 1, "rows": []},
    ]
    result = api.check_project(write(home, [], 0, walls=walls), home)
    bad = {f.subject for f in result.errors if f.code != "row_sum"}
    good = [m for m in modules if m["label"] not in bad]
    if not good:  # шаблону при любых габаритах сетки не хватает (например сушка только 750 мм)
        return
    walls[0]["rows"][0]["modules"] = good
    path = write(home, [], 0, walls=walls)
    parts = api.parts_from_project(path, home)
    for module in parts.modules:
        for p in module.parts:
            assert p.length > 0 and p.width > 0 and p.thickness > 0, (p.id, p.length, p.width)
            if p.name != BACK_PANEL:
                assert min(p.position.x, p.position.y, p.position.z) >= 0, (p.id, p.position)
    assert all(n >= 0 for n in parts.hardware.values()), parts.hardware
