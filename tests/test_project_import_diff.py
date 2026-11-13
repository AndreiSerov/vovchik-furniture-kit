import json

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import OrderError
from kitchen.project import Project
from typer.testing import CliRunner


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


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_full_project_import_and_schema_rejection(home, tmp_path):
    api.order_new("2026-047-petrov")
    p = write(tmp_path / "project.json", project())
    assert api.import_project("petrov", p).version == 1
    bad = project()
    bad["walls"][0]["rows"][0]["modules"][0]["width"] = "not a number"
    with pytest.raises(OrderError):
        api.import_project("petrov", write(p, bad))
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
    api.import_project("petrov", p)
    new = project()
    modules = new["walls"][0]["rows"][0]["modules"]
    modules[0]["width"] = 650
    modules.pop()
    modules.append({"label": "Н2", "template": "base_door", "width": 400})
    api.import_project("petrov", write(p, new))
    result = api.diff_project("petrov", 1, 2)
    assert any("Н1" in s and "ширина 600 → 650" in s for s in result.changes)
    assert any("П1" in s and "удалён" in s for s in result.changes)
    assert any("Н2" in s and "добавлен" in s for s in result.changes)
    cli = CliRunner().invoke(app, ["diff", "petrov", "v1", "v2", "--json"])
    assert cli.exit_code == 0
    assert json.loads(cli.output) == result.model_dump(mode="json")


def test_duplicate_module_labels_are_rejected(home, tmp_path):
    api.order_new("2026-047-petrov")
    data = project()
    modules = data["walls"][0]["rows"][0]["modules"]
    modules.append(dict(modules[0]))
    with pytest.raises(OrderError, match="Н1"):
        api.import_project("petrov", write(tmp_path / "p.json", data))


def test_import_ignores_any_supplied_version(home, tmp_path):
    api.order_new("2026-047-petrov")
    data = project()
    data["version"] = "ignored"
    assert api.import_project("petrov", write(tmp_path / "p.json", data)).version == 1


def test_parts_rejects_unsupported_contents(home, base_door_project):
    data = json.loads(base_door_project.read_text())
    data["walls"][0]["rows"][0]["modules"][0]["contents"] = {"shelves": 0}
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
    api.import_project("petrov", write(p, project()))
    with pytest.raises(OrderError, match="Версия 1 не соответствует"):
        api.diff_project("petrov", 1, 2)
