"""Тикет 34: проект и шаблоны отклоняют неверный ввод находкой, а не трейсбеком и не отрицательными деталями."""

import json
import re
from collections import Counter

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import KitchenError
from kitchen.parts import BACK_PANEL, TEMPLATES, Template
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
    result = api.calculation(project=path, home=home).check()
    assert codes(result) == ["module_build"]
    assert "неизвестный шаблон" in result.errors[0].message


def test_unknown_template_does_not_hide_the_other_modules(home):
    modules = [
        {"label": "Н1", "template": "foo", "width": 600},
        {"label": "Н2", "template": "base_drawers", "width": 600, "depth": 400},
    ]
    result = api.calculation(project=write(home, lower(*modules), 1200), home=home).check()
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
    result = api.calculation(project=path, home=home).check()
    assert codes(result) == ["module_build"]
    with pytest.raises(KitchenError):
        api.calculation(project=path, home=home).parts()


def test_nonpositive_part_error_names_part_and_size(home):
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 30}), 30)
    message = api.calculation(project=path, home=home).check().errors[0].message
    assert "Н1" in message and "-2" in message


@pytest.mark.parametrize("key", ["width", "height", "depth"])
@pytest.mark.parametrize("value", [0, -100, True])
def test_schema_rejects_nonpositive_module_size(home, key, value):
    module = {"label": "Н1", "template": "base_door", "width": 600, key: value}
    path = write(home, lower(module), 600)
    with pytest.raises(KitchenError, match="Н1|width|height|depth|больше"):
        api.calculation(project=path, home=home).check()


def test_schema_rejects_nonpositive_niche(home):
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 600}), 600)
    data = json.loads(path.read_text())
    data["appliances"] = [{"type": "oven", "width": 600, "module": "Н1", "niche_height": 0}]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(KitchenError):
        api.calculation(project=path, home=home).check()


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
        api.calculation(project=path, home=home).check()
    with pytest.raises(KitchenError):
        api.calculation(project=path, home=home).parts()


# Инвариант: количество фурнитуры не меньше нуля
def test_module_with_negative_hardware_is_a_module_build_error(home, monkeypatch):
    original = TEMPLATES["base_door"]

    def build(*args):
        out = original.build(*args)
        out.hardware = Counter({"shelf_pin_5": -4})
        return out

    monkeypatch.setitem(TEMPLATES, "base_door", Template(build, "lower", title="нижний модуль"))
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 600}), 600)
    assert codes(api.calculation(project=path, home=home).check()) == ["module_build"]
    with pytest.raises(KitchenError, match="shelf_pin_5"):
        api.calculation(project=path, home=home).parts()


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
    result = api.calculation(project=path, home=home).check()
    assert [f.subject for f in result.errors if f.code == "duplicate_label"] == ["Н1"]
    assert any(f.code == "part_exceeds_sheet" and f.subject.startswith("Н1") for f in result.errors)


def test_tall_written_in_two_rows_is_a_duplicate_label(home):
    rows = [
        {"kind": "lower", "modules": [{"label": "П1", "template": "tall_oven", "width": 600}]},
        {"kind": "upper", "modules": [{"label": "П1", "template": "tall_oven", "width": 600}]},
    ]
    result = api.calculation(project=write(home, rows, 600), home=home).check()
    assert [f.subject for f in result.errors if f.code == "duplicate_label"] == ["П1"]


def test_import_rejects_duplicate_labels_with_the_same_finding(home):
    api.order_new("2026-047-petrov", "Петров", home=home)
    modules = [
        {"label": "Н1", "template": "base_door", "width": 600},
        {"label": "Н1", "template": "base_door", "width": 400},
    ]
    path = write(home, lower(*modules), 1000)
    message = next(
        f.message
        for f in api.calculation(project=path, home=home).check().errors
        if f.code == "duplicate_label"
    )
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
    assert [f for f in api.calculation(project=path, home=home).check().findings if f.code == "row_sum"] == []
    result = api.calculation(project=path, home=home).parts()
    assert [f for f in result.findings if f.code == "row_sum"] == []
    assert next(c for c in result.common if c.kind == "plinth").length_mm == 1200


def test_one_tall_and_two_talls_in_one_row_as_before(home):
    one = write(home, lower({"label": "П1", "template": "tall_oven", "width": 600}), 600)
    assert [f for f in api.calculation(project=one, home=home).check().findings if f.code == "row_sum"] == []
    assert (
        next(
            c for c in api.calculation(project=one, home=home).parts().common if c.kind == "plinth"
        ).length_mm
        == 600
    )
    two = write(
        home,
        lower(
            {"label": "П1", "template": "tall_oven", "width": 600},
            {"label": "П2", "template": "tall_oven", "width": 600},
        ),
        1200,
    )
    assert [f for f in api.calculation(project=two, home=home).check().findings if f.code == "row_sum"] == []
    assert (
        next(
            c for c in api.calculation(project=two, home=home).parts().common if c.kind == "plinth"
        ).length_mm
        == 1200
    )


# T1. Ящик Tandembox
def test_drawer_does_not_fit_has_one_text_for_base_drawers_and_tall(home):
    drawers = write(
        home, lower({"label": "Н1", "template": "base_drawers", "width": 120, "height": 150}), 120
    )
    base = api.calculation(project=drawers, home=home).check().errors[0].message
    assert "ящик не помещается" in base
    assert any(ch.isdigit() for ch in base.split("не помещается", 1)[1])
    # пенал: габариты проекта до ящика не доходят (ниши не вмещаются раньше), низкий фасад ящика — в мастерской
    path = home / "workshop" / "construction.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("высота_фасада_ящика: 280", "высота_фасада_ящика: 50"),
        encoding="utf-8",
    )
    tall = write(home, lower({"label": "П1", "template": "tall_oven", "width": 600}), 600)
    tall_message = api.calculation(project=tall, home=home).check().errors[0].message
    assert "ящик не помещается" in tall_message
    # одна формулировка: после метки текст до «ящик не помещается» совпадает
    shape = lambda text: re.sub(r"\d+", "N", text.split(":", 1)[1])
    assert shape(base) == shape(tall_message)


# Сетка: шаблон × габариты
SIZES = (1, 2, 30, 100, 150, 300, 450, 600, 900, 1200, 3000)
WORKING_WIDTHS = (750, 1050)  # рабочие ширины шаблонов из эталонов: сушка 750, угловой 1050 (150 — в SIZES)


def grid_modules(template):
    widths = sorted({*SIZES, *WORKING_WIDTHS})
    shapes = [(w, h, d) for w in widths for h in SIZES for d in SIZES] + [(w, None, None) for w in widths]
    for w, h, d in shapes:
        module = {"label": f"M{w}_{h}_{d}", "template": template, "width": w}
        if h is not None:
            module.update(height=h, depth=d)
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
    result = api.calculation(project=write(home, [], 0, walls=walls), home=home).check()
    bad = {f.subject for f in result.errors if f.code != "row_sum"}
    good = [m for m in modules if m["label"] not in bad]
    assert good, f"сетка ни разу не построила {template}"  # иначе сетка про этот шаблон ничего не проверяет
    walls[0]["rows"][0]["modules"] = good
    parts = api.calculation(project=write(home, [], 0, walls=walls), home=home).parts()
    assert {m.module for m in parts.modules} == {m["label"] for m in good}
    for module in parts.modules:
        for p in module.parts:
            assert p.length > 0 and p.width > 0 and p.thickness > 0, (p.id, p.length, p.width)
            if p.name != BACK_PANEL:
                assert min(p.position.x, p.position.y, p.position.z) >= 0, (p.id, p.position)
    assert all(n >= 0 for n in parts.hardware.values()), parts.hardware


# Ревью тикета 34
def test_countertop_excludes_tall_from_other_row_after_own_tall(home):
    # стена 3000: Н1 | П1 | Н2 | П2 | Н3; П1 записан в нижнем ряду, П2 — в верхнем
    base = lambda label: {"label": label, "template": "base_door", "width": 600}
    upper = lambda label: {"label": label, "template": "wall_door", "width": 600}
    tall = lambda label: {"label": label, "template": "tall_oven", "width": 600}
    walls = [
        {
            "label": "A",
            "length": 3000,
            "rows": [
                {"kind": "lower", "modules": [base("Н1"), tall("П1"), base("Н2"), base("Н3")]},
                {"kind": "upper", "modules": [upper("В1"), upper("В2"), tall("П2"), upper("В3")]},
            ],
        }
    ]
    path = write(home, [], 0, walls=walls)
    data = json.loads(path.read_text())
    data["materials"]["countertop"] = "top_38_marble"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    result = api.calculation(project=path, home=home).parts()
    assert [f for f in result.findings if f.code == "row_sum"] == []
    top = next(c for c in result.common if c.kind == "countertop")
    assert top.length_mm == 1800, top


def test_appliance_on_unknown_template_module_is_one_finding(home):
    path = write(home, lower({"label": "Н1", "template": "foo", "width": 600}), 600)
    data = json.loads(path.read_text())
    data["appliances"] = [{"type": "oven", "width": 595, "module": "Н1"}]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert codes(api.calculation(project=path, home=home).check()) == ["module_build"]


def depth_codes(home, depth):
    path = write(home, lower({"label": "Н1", "template": "base_drawers", "width": 600, "depth": depth}), 600)
    return [f for f in api.calculation(project=path, home=home).check().errors]


def test_drawer_depth_is_one_rule_with_the_back_wall(home):
    # дно ящика 490 + задняя стенка 16 = 506 мм: глубина 500 (длина направляющей) уже мала
    for depth in (500, 505):
        errors = depth_codes(home, depth)
        assert [f.code for f in errors] == ["drawer_depth"], (depth, errors)
        assert "506" in errors[0].message and str(depth) in errors[0].message
        with pytest.raises(KitchenError, match="506"):
            api.calculation(
                project=write(
                    home,
                    lower({"label": "Н1", "template": "base_drawers", "width": 600, "depth": depth}),
                    600,
                ),
                home=home,
            ).parts()
    assert depth_codes(home, 506) == []


# Метки после нормализации
@pytest.mark.parametrize("second", ["н1", " Н1 ", "H1", "Н1\u00a0"])
def test_labels_equal_after_normalisation_are_duplicates(home, second):
    modules = [
        {"label": "Н1", "template": "base_door", "width": 600},
        {"label": second, "template": "base_door", "width": 600},
    ]
    result = api.calculation(project=write(home, lower(*modules), 1200), home=home).check()
    found = [f for f in result.errors if f.code == "duplicate_label"]
    assert len(found) == 1
    assert "«Н1»" in found[0].message and f"«{second}»" in found[0].message


def test_kitchen_parts_gives_the_duplicate_label_finding_text(home):
    modules = [
        {"label": "Н1", "template": "base_door", "width": 600},
        {"label": "Н1", "template": "base_door", "width": 400},
    ]
    path = write(home, lower(*modules), 1000)
    message = next(
        f.message
        for f in api.calculation(project=path, home=home).check().errors
        if f.code == "duplicate_label"
    )
    with pytest.raises(KitchenError) as e:
        api.calculation(project=path, home=home).parts()
    assert message in e.value.problems
    assert "Маркировка" not in str(e.value)


def test_duplicate_label_keeps_dims_of_the_first_module(home):
    # первый Н1 шириной 400 не вмещает нишу ПММ 450; второй 700 вмещает: ниша проверяется по первому
    modules = [
        {"label": "Н1", "template": "base_door", "width": 400},
        {"label": "Н1", "template": "base_door", "width": 700},
    ]
    path = write(home, lower(*modules), 1100)
    data = json.loads(path.read_text())
    data["appliances"] = [{"type": "dishwasher", "width": 450, "module": "Н1"}]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert "niche_too_small" in codes(api.calculation(project=path, home=home).check())


# Тикет 35: текст находки глубины ящика и `limit` называют одно число
@pytest.mark.parametrize("depth", [450, 499, 500, 505])
def test_drawer_depth_text_and_limit_name_the_same_number(home, depth):
    (finding,) = depth_codes(home, depth)
    assert f"{finding.limit:g}" in finding.message, (depth, finding)
    assert finding.value == depth
