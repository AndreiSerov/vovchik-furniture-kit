"""Тикет 60: ручки считаются в одном месте — фасады × `construction.ручки.per_unit`."""

import copy
import json
import re

import pytest
from kitchen import api

from tests.conftest import BASE_DOOR_PROJECT, GOLDEN_DIR
from tests.test_parts_api import _edit_workshop

FIXTURES = GOLDEN_DIR.parent / "fixtures"
ENGINE = GOLDEN_DIR.parent.parent / "engine" / "kitchen"
UPPER = {"wall_corner", "wall_door", "wall_lift", "wall_hood", "wall_dryer"}
TEMPLATES = [
    "tall_oven",
    "base_door",
    "base_corner",
    "wall_corner",
    "wall_door",
    "wall_lift",
    "wall_hood",
    "wall_dryer",
    "base_sink",
    "base_bottle",
    "base_drawers",
    "dishwasher_panel",
]  # полнота — в test_handles_registry_is_covered
WIDTHS = {"base_corner": 1050, "base_bottle": 150, "base_sink": 750, "wall_dryer": 750}


def one_module_project(home, template, width=None, **extra):
    row_kind = "upper" if template in UPPER else "lower"
    module = {"label": "М1", "template": template, "width": width or WIDTHS.get(template, 600), **extra}
    if "corner" in template:
        module["corner"] = {"adjacent_wall": "Б"}
    data = copy.deepcopy(BASE_DOOR_PROJECT)
    data["walls"] = [
        {"label": "A", "length": 3000, "rows": [{"kind": row_kind, "modules": [module]}]},
        {"label": "Б", "length": 1, "rows": []},
    ]
    path = home / "one.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_petrov_has_18_handles(home):
    """Проверка 1: число ручек Петрова; детали Петрова держат эталоны и `test_petrov.py`."""
    parts = api.calculation(project=FIXTURES / "petrov_project.json", home=home).parts()
    assert parts.hardware["handle_profile"] == 18


CASES = [(t, None, {}) for t in TEMPLATES] + [
    ("base_door", 600, {"contents": {"doors": 1}}),
    ("base_door", 900, {"contents": {"doors": 2}}),
]


@pytest.mark.parametrize("per_unit", [1, 2])
@pytest.mark.parametrize(("template", "width", "extra"), CASES)
def test_handles_equal_facades_times_per_unit(home, template, width, extra, per_unit):
    """Проверка 3."""
    if per_unit == 2:
        _edit_workshop(home, "construction.yaml", "ручки: по одной на фасад", "ручки: по 2 на фасад")
    parts = api.calculation(project=one_module_project(home, template, width, **extra), home=home).parts()
    module = parts.modules[0]
    facades = sum(p.area_m2 is not None for p in module.parts)
    assert facades > 0
    assert module.hardware["handle_profile"] == facades * per_unit


@pytest.mark.parametrize(
    ("template", "width", "extra", "handles"),
    [
        ("base_drawers", None, {}, 3),
        ("tall_oven", None, {}, 2),
        ("base_door", 900, {"contents": {"doors": 2}}, 2),
        ("base_door", 400, {}, 1),
    ],
)
def test_handles_per_template_are_concrete_numbers(home, template, width, extra, handles):
    """Проверка 3, числа по шаблонам (тикет 65): не пересчёт того же правила."""
    parts = api.calculation(project=one_module_project(home, template, width, **extra), home=home).parts()
    assert parts.modules[0].hardware["handle_profile"] == handles


def test_handles_registry_is_covered():
    schema = json.loads((ENGINE / "schemas" / "project.schema.json").read_text(encoding="utf-8"))
    assert sorted(schema["$defs"]["ModuleSpec"]["properties"]["template"]["enum"]) == sorted(TEMPLATES)


def test_module_without_facades_has_no_handle_key(home):
    """Проверка 5."""
    parts = api.calculation(
        project=one_module_project(home, "base_door", 600, contents={"doors": 0}), home=home
    ).parts()
    module = parts.modules[0]
    assert not any(p.area_m2 is not None for p in module.parts)
    assert "handle_profile" not in module.hardware
    assert "handle_profile" not in parts.hardware


def test_handles_per_unit_is_used_in_one_place():
    """Проверка 6: тест-страж по тексту исходников."""
    hits = [
        f"{path.name}:{n}"
        for path in sorted(ENGINE.glob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"handles\.per_unit", line)
    ]
    assert len(hits) == 1, hits
