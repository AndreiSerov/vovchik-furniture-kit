import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

GOLDEN = Path(__file__).resolve().parent / "golden" / "base_door_600.json"


def test_golden_base_door_600(home, base_door_project):
    """Эталон из ТЗ: размеры, количества, кромка 3.710/3.890/4.3 м, фасады 0.4253 м² — до миллиметра."""
    result = api.parts_from_project(base_door_project, home)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert result.model_dump(exclude={"mock", "mock_files"}) == expected


def test_mock_flag_on_result(home, base_door_project):
    result = api.parts_from_project(base_door_project, home)
    assert result.mock is True
    assert len(result.mock_files) == 6


def test_project_file_not_found(home, base_door_project):
    with pytest.raises(OrderError) as e:
        api.parts_from_project(home / "nope.json", home)
    assert "не найден" in e.value.problems[0]


def test_project_file_not_json(home, base_door_project):
    p = home / "broken.json"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "не JSON" in e.value.problems[0]


def test_unknown_template_lists_known(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["template"] = "base_nope"
    p = home / "bad_template.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    text = "\n".join(e.value.problems)
    assert "base_nope" in text and "base_door" in text


def test_duplicate_module_labels_break_part_ids(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    modules = project["walls"][0]["rows"][0]["modules"]
    modules.append(dict(modules[0]))
    p = home / "dup.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "не уникальна" in e.value.problems[0] and "Н1-бок-Л" in e.value.problems[0]


def test_base_door_rejected_in_upper_row(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["kind"] = "upper"
    p = home / "upper.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "нижнем ряду" in e.value.problems[0]


def test_unknown_body_material(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["materials"]["body"] = "ldsp_nope"
    p = home / "bad_body.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "ldsp_nope" in e.value.problems[0] and "materials.yaml" in e.value.problems[0]


def test_unknown_project_schema_key(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["materialz"] = {}
    p = home / "bad_key.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "materialz" in e.value.problems[0] and "неизвестный ключ" in e.value.problems[0]


def test_non_integer_module_width(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["width"] = 600.5
    p = home / "frac.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.parts_from_project(p, home)
    assert "600.5" in e.value.problems[0] and "целое" in e.value.problems[0]
