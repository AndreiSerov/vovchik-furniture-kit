import json
from pathlib import Path
from unittest.mock import patch

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import OrderError
from kitchen.project import Project
from typer.testing import CliRunner

from tests.conftest import BASE_DOOR_PROJECT


def project():
    return {
        "order": "2026-047",
        "ceiling_height": 2700,
        "materials": {
            "body": "body",
            "facades_lower": "white",
            "facades_upper": "grey",
            "handles": "handle",
            "countertop": "top",
        },
        "walls": [
            {
                "label": "A",
                "length": 2800,
                "rows": [
                    {
                        "kind": "lower",
                        "materials": {"facades": "black"},
                        "modules": [
                            {
                                "label": "Н1",
                                "template": "base_corner",
                                "width": 600,
                                "corner": {"adjacent_wall": "Б"},
                                "contents": {
                                    "doors": 1,
                                    "shelves": 0,
                                    "drawers": 0,
                                    "inserts": ["corner_carousel"],
                                    "lift": "lift",
                                },
                                "without_back": True,
                            },
                            {
                                "label": "П1",
                                "template": "tall_oven",
                                "width": 600,
                                "materials": {"body": "oak"},
                            },
                        ],
                    }
                ],
            }
        ],
        "appliances": [
            {"type": "oven", "width": 600, "model": "X", "height": 590, "depth": 550, "module": "П1"}
        ],
        "led": True,
    }


def schema_import(order, path):
    """Импорт без проверок конструкции (1.6): фикстура ниже — про схему и diff, не про раскрой.
    Схема и прочие проверки обёртки работают как обычно; подменяются только проверки конструкции."""
    with patch.object(api, "run_checks", lambda *args: []):
        return api.import_project(order, path)


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_full_project_import_and_schema_rejection(home, tmp_path):
    api.order_new("2026-047-petrov")
    p = write(tmp_path / "project.json", project())
    assert schema_import("petrov", p).version == 1
    bad = project()
    bad["walls"][0]["rows"][0]["modules"][0]["width"] = "not a number"
    with pytest.raises(OrderError):
        schema_import("petrov", write(p, bad))
    assert api.export_project("petrov").version == 1


def test_schema_is_in_sync_with_model(repo):
    assert (
        json.loads((repo / "engine/kitchen/schemas/project.schema.json").read_text())
        == Project.model_json_schema()
    )


def test_diff_reports_width_and_added_removed_modules(home, tmp_path):
    api.order_new("2026-047-petrov")
    old = project()
    p = write(tmp_path / "project.json", old)
    schema_import("petrov", p)
    new = project()
    modules = new["walls"][0]["rows"][0]["modules"]
    modules[0]["width"] = 650
    modules.pop()
    modules.append({"label": "Н2", "template": "base_door", "width": 400})
    schema_import("petrov", write(p, new))
    result = api.diff_project("petrov", 1, 2)
    assert any("Н1" in s and "ширина 600 → 650" in s for s in result.changes)
    assert any("П1" in s and "удалён" in s for s in result.changes)
    assert any("Н2" in s and "добавлен" in s for s in result.changes)
    cli = CliRunner().invoke(app, ["diff", "petrov", "v1", "v2", "--json"])
    assert cli.exit_code == 0
    assert json.loads(cli.output) == result.model_dump(mode="json")


def test_duplicate_module_labels_are_rejected(home, tmp_path):
    api.order_new("2026-047-petrov")
    # повтор метки — находка `duplicate_label` из проверок проекта (тикет 34), поэтому импорт полный
    data = json.loads(json.dumps(BASE_DOOR_PROJECT))
    modules = data["walls"][0]["rows"][0]["modules"]
    modules.append(dict(modules[0]))
    with pytest.raises(OrderError, match="Н1"):
        api.import_project("petrov", write(tmp_path / "p.json", data))


def test_import_ignores_any_supplied_version(home, tmp_path):
    api.order_new("2026-047-petrov")
    data = project()
    data["version"] = "ignored"
    assert schema_import("petrov", write(tmp_path / "p.json", data)).version == 1


def test_parts_rejects_unsupported_contents(home, base_door_project):
    data = json.loads(base_door_project.read_text())
    data["walls"][0]["rows"][0]["modules"][0]["contents"] = {"drawers": 2}
    write(base_door_project, data)
    with pytest.raises(OrderError, match="наполнение"):
        api.parts_from_project(base_door_project)


def test_material_overrides_take_precedence(home, base_door_project):
    data = json.loads(base_door_project.read_text())
    row = data["walls"][0]["rows"][0]
    row["materials"] = {"body": "ldsp_16_oak"}
    write(base_door_project, data)
    result = api.parts_from_project(base_door_project)
    assert result.modules[0].parts[0].material == "ldsp_16_oak"
    row["modules"][0]["materials"] = {"body": "ldsp_16_white"}
    write(base_door_project, data)
    result = api.parts_from_project(base_door_project)
    assert result.modules[0].parts[0].material == "ldsp_16_white"


def test_diff_gives_readable_error_for_old_incomplete_project(home, tmp_path):
    api.order_new("2026-047-petrov")
    p = write(tmp_path / "p.json", {"order": "2026-047", "walls": [1]})
    api.import_project("petrov", p, validate=lambda data, ctx: api.ValidationResult())
    schema_import("petrov", write(p, project()))
    with pytest.raises(OrderError, match="Версия 1 не соответствует"):
        api.diff_project("petrov", 1, 2)


@pytest.mark.parametrize("field,value", [("ceiling_height", "2700"), ("led", "false")])
def test_import_rejects_strings_for_number_and_boolean(home, tmp_path, field, value):
    api.order_new("2026-047-petrov")
    data = project()
    data[field] = value
    with pytest.raises(OrderError):
        schema_import("petrov", write(tmp_path / "p.json", data))


@pytest.mark.parametrize("field,value", [("width", "600"), ("without_back", "false")])
def test_import_rejects_strings_in_module(home, tmp_path, field, value):
    api.order_new("2026-047-petrov")
    data = project()
    data["walls"][0]["rows"][0]["modules"][0][field] = value
    with pytest.raises(OrderError):
        schema_import("petrov", write(tmp_path / "p.json", data))


def test_diff_compares_older_minimal_version(home, tmp_path):
    api.order_new("2026-047-petrov")
    old = {
        "order": "2026-047",
        "walls": [
            {
                "label": "A",
                "rows": [
                    {"kind": "lower", "modules": [{"label": "Н1", "template": "base_corner", "width": 400}]}
                ],
            }
        ],
    }
    p = write(tmp_path / "p.json", old)
    api.import_project("petrov", p, validate=lambda data, ctx: api.ValidationResult())
    schema_import("petrov", write(p, project()))
    assert any("Н1: ширина 400 → 600" in c for c in api.diff_project("petrov", 1, 2).changes)


def test_diff_names_added_nested_fields_in_russian(home, tmp_path):
    api.order_new("2026-047-petrov")
    old = project()
    module = old["walls"][0]["rows"][0]["modules"][0]
    del module["contents"]
    del module["corner"]
    p = write(tmp_path / "p.json", old)
    schema_import("petrov", p)
    schema_import("petrov", write(p, project()))
    changes = "\n".join(api.diff_project("petrov", 1, 2).changes)
    assert "наполнение: двери не задано → 1" in changes
    assert "угол: соседняя стена не задано → Б" in changes
    assert '"doors"' not in changes and '"adjacent_wall"' not in changes


# --- тикет 51: diff показывает смену техники у модулей и мест -------------------

PETROV = Path(__file__).resolve().parent / "fixtures" / "petrov_project.json"


def petrov_versions(tmp_path, *edits):
    """Петров v1 и по версии на каждую правку (каждая правка — к предыдущей версии)."""
    api.order_new("2026-047-petrov")
    data = json.loads(PETROV.read_text(encoding="utf-8"))
    versions = [data]
    for edit in edits:
        data = json.loads(json.dumps(data))
        edit(data)
        versions.append(data)
    for n, version in enumerate(versions, 1):
        schema_import("petrov", write(tmp_path / f"v{n}.json", version))


def appliance(data, kind):
    return next(a for a in data["appliances"] if a["type"] == kind)


def test_diff_shows_the_hob_moved_between_modules(home, tmp_path):
    petrov_versions(tmp_path, lambda d: appliance(d, "hob").update(module="Н2"))
    assert api.diff_project("petrov", 1, 2).changes == [
        "Н1: техника варочная панель → не задано",
        "Н2: техника не задано → варочная панель",
    ]


def test_diff_shows_appliance_field_changes_before_the_kit(home, tmp_path):
    def edit(d):
        appliance(d, "sink")["width"] = 600
        appliance(d, "sink")["model"] = "Franke"
        appliance(d, "dishwasher")["module"] = "Н5"

    petrov_versions(tmp_path, edit)
    assert api.diff_project("petrov", 1, 2).changes == [
        "Н5: мойка: ширина 750 → 600",
        "Н5: мойка: модель не задано → Franke",
        "Н5: техника мойка → мойка, посудомойка",
        "Н6: техника посудомойка → не задано",
    ]


def test_diff_shows_appliance_change_of_a_gap(home, tmp_path):
    def add_fridge(d):
        d["appliances"].append({"type": "fridge", "width": 600, "model": "A", "module": "ПМ1"})

    def change(d):
        appliance(d, "fridge").update(width=560, model="B")

    petrov_versions(tmp_path, add_fridge, change)
    assert api.diff_project("petrov", 1, 2).changes == ["ПМ1: техника не задано → холодильник"]
    assert api.diff_project("petrov", 2, 3).changes == [
        "ПМ1: холодильник: ширина 600 → 560",
        "ПМ1: холодильник: модель A → B",
    ]


@pytest.mark.parametrize("broken", ["missing", ["oven"]])
def test_diff_of_an_unchecked_old_version_with_appliance_without_type_is_a_readable_error(
    home, tmp_path, broken
):
    api.order_new("2026-047-petrov")
    old = json.loads(PETROV.read_text(encoding="utf-8"))
    if broken == "missing":
        del old["appliances"][2]["type"]
    else:
        old["appliances"][2]["type"] = broken
    api.import_project(
        "petrov", write(tmp_path / "v1.json", old), validate=lambda data, ctx: api.ValidationResult()
    )
    schema_import("petrov", write(tmp_path / "v2.json", json.loads(PETROV.read_text(encoding="utf-8"))))
    with pytest.raises(OrderError, match="Версия 1 не соответствует"):
        api.diff_project("petrov", 1, 2)


def test_diff_pairs_the_remaining_fridge_not_the_removed_one(home, tmp_path):
    two = [
        {"type": "fridge", "width": 450, "model": "X", "module": "ПМ1"},
        {"type": "fridge", "width": 600, "model": "Y", "module": "ПМ1"},
    ]
    petrov_versions(tmp_path, lambda d: d["appliances"].extend(two), lambda d: d["appliances"].remove(two[0]))
    assert api.diff_project("petrov", 2, 3).changes == ["ПМ1: техника холодильник, холодильник → холодильник"]


def test_diff_of_two_fridges_swapped_in_the_list_has_no_field_lines(home, tmp_path):
    two = [
        {"type": "fridge", "width": 450, "model": "X", "module": "ПМ1"},
        {"type": "fridge", "width": 600, "model": "Y", "module": "ПМ1"},
    ]
    petrov_versions(tmp_path, lambda d: d["appliances"].extend(two), lambda d: d["appliances"].reverse())
    assert api.diff_project("petrov", 2, 3).changes == []


def test_diff_names_a_field_of_the_second_appliance_of_one_kind(home, tmp_path):
    two = [
        {"type": "fridge", "width": 450, "model": "X", "module": "ПМ1"},
        {"type": "fridge", "width": 600, "model": "Y", "module": "ПМ1"},
    ]
    petrov_versions(
        tmp_path, lambda d: d["appliances"].extend(two), lambda d: d["appliances"][-1].update(width=560)
    )
    assert api.diff_project("petrov", 2, 3).changes == ["ПМ1: холодильник: ширина 600 → 560"]


def test_diff_names_niche_fields_of_an_appliance(home, tmp_path):
    def edit(d):
        oven = next(a for a in d["appliances"] if a["type"] == "oven")
        oven.update(niche_width=570, height=600, depth=560)

    petrov_versions(tmp_path, edit)
    assert api.diff_project("petrov", 1, 2).changes == [
        "П1: духовой шкаф: высота 595 → 600",
        "П1: духовой шкаф: глубина 567 → 560",
        "П1: духовой шкаф: ширина ниши 560 → 570",
    ]


def test_diff_puts_the_kit_line_before_the_position_line(home, tmp_path):
    def edit(d):
        d["walls"][1]["rows"][0]["modules"].reverse()
        next(a for a in d["appliances"] if a["type"] == "dishwasher")["module"] = "Н5"

    petrov_versions(tmp_path, edit)
    assert api.diff_project("petrov", 1, 2).changes == [
        "Н5: техника мойка → мойка, посудомойка",
        "Н5: положение Б, нижний, 1 → Б, нижний, 2",
        "Н6: техника посудомойка → не задано",
        "Н6: положение Б, нижний, 2 → Б, нижний, 1",
    ]


# Повторное ревью 51: правило пар приборов одного вида (`common.pair_by_model_then_order`).
def test_diff_pairs_appliances_by_model_ignoring_case_before_order(home, tmp_path):
    three = [
        {"type": "fridge", "width": 450, "model": "X", "module": "ПМ1"},
        {"type": "fridge", "width": 500, "model": "Y", "module": "ПМ1"},
        {"type": "fridge", "width": 600, "model": "Z", "module": "ПМ1"},
    ]

    def relabel(d):
        mine = [a for a in d["appliances"] if a["type"] == "fridge"]
        mine[1]["model"], mine[2]["model"] = "y", "z"
        d["appliances"] = [a for a in d["appliances"] if a["type"] != "fridge"] + [mine[2], mine[1], mine[0]]

    petrov_versions(tmp_path, lambda d: d["appliances"].extend(three), relabel)
    assert api.diff_project("petrov", 2, 3).changes == [
        "ПМ1: холодильник: модель Y → y",
        "ПМ1: холодильник: модель Z → z",
    ]


def test_diff_pairs_appliances_without_models_in_list_order(home, tmp_path):
    two = [
        {"type": "fridge", "width": 450, "module": "ПМ1"},
        {"type": "fridge", "width": 500, "module": "ПМ1"},
    ]

    def widen(d):
        for a in d["appliances"]:
            if a["type"] == "fridge":
                a["width"] += 10

    petrov_versions(tmp_path, lambda d: d["appliances"].extend(two), widen)
    assert api.diff_project("petrov", 2, 3).changes == [
        "ПМ1: холодильник: ширина 450 → 460",
        "ПМ1: холодильник: ширина 500 → 510",
    ]
