"""Тикет 33: поле мастерской × (−1, 0) — ошибка загрузки или честный расчёт Петрова.

Сам факт границы у каждого числового поля проверяет `test_structure_guards.py`.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from kitchen import api
from kitchen.errors import KitchenError

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FILES = (
    "equipment.yaml",
    "construction.yaml",
    "materials.yaml",
    "hardware.yaml",
    "labor.yaml",
    "pricing.yaml",
    "appliances.yaml",
    "economy.yaml",
    "master.yaml",
)
BAD = (-1, 0)


def numeric_paths(node, path=()):
    """Пути ко всем числам (не bool) в разобранном YAML."""
    if isinstance(node, bool):
        return
    if isinstance(node, int | float):
        yield path
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from numeric_paths(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from numeric_paths(value, (*path, index))


def _cases():
    for name in FILES:
        data = yaml.safe_load((REPO / "workshop" / name).read_text(encoding="utf-8"))
        seen = set()
        for path in numeric_paths(data):
            field = tuple(k for k in path if isinstance(k, str))
            if field in seen:
                continue  # граница живёт в типе поля: одного элемента списка достаточно
            seen.add(field)
            for bad in BAD:
                yield pytest.param(name, path, bad, id=f"{name}:{'.'.join(map(str, path))}={bad}")


def negatives(node, where="$"):
    """Пути к отрицательным числам в JSON результата."""
    if isinstance(node, bool):
        return []
    if isinstance(node, int | float):
        return [where] if node < 0 else []
    found = []
    items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, value in items:
        found += negatives(value, f"{where}.{key}")
    return found


COMPUTES = (api.parts_from_project, api.cut_from_project, api.estimate_from_project)


@pytest.fixture(scope="module")
def baseline():
    """Отрицательные числа у Петрова на настоящей мастерской (положение задней стенки ХДФ): так задумано."""
    petrov = FIXTURES / "petrov_project.json"
    return {c.__name__: set(negatives(json.loads(c(petrov, REPO).model_dump_json()))) for c in COMPUTES}


def with_value(data, path, value):
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


@pytest.mark.parametrize(("name", "path", "bad"), list(_cases()))
def test_numeric_field_bad_value_is_load_error_or_honest_calculation(
    tmp_path, monkeypatch, baseline, name, path, bad
):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    monkeypatch.setenv("KITCHEN_HOME", str(tmp_path))
    target = tmp_path / "workshop" / name
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    with_value(data, path, bad)
    target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    checked = api.check_workshop(tmp_path)
    key = next(str(k) for k in reversed(path) if isinstance(k, str))
    if not checked.valid:
        assert any(name in e and key in e for e in checked.errors), (checked.errors, key)
        return
    petrov = FIXTURES / "petrov_project.json"
    for compute in COMPUTES:
        try:
            result = compute(petrov, tmp_path)
        except KitchenError:
            return  # русская ошибка расчёта — не молчаливо неверный вывод
        new = set(negatives(json.loads(result.model_dump_json()))) - baseline[compute.__name__]
        assert not new, (compute.__name__, name, path, bad, sorted(new)[:3])
