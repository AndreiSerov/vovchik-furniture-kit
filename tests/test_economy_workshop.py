"""Тикет 17: `economy.yaml` — восьмой файл мастерской, правила замен, загрузка и плашка мока."""

import pytest
from kitchen import api
from kitchen.errors import WorkshopError
from kitchen.result import MOCK_BANNER

from tests.conftest import REPO

PETROV = REPO / "tests" / "fixtures" / "petrov_project.json"
OTHER_FILES = (
    "equipment",
    "construction",
    "materials",
    "hardware",
    "labor",
    "pricing",
    "appliances",
)


def edit(home, name, old, new):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8")
    assert old in text, (name, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def load_errors(home):
    with pytest.raises(WorkshopError) as e:
        api.load_workshop(home)
    return e.value.problems


def test_economy_file_is_a_mock_workshop_file_with_five_rules(home):
    workshop = api.load_workshop(home)
    assert "economy.yaml" in workshop.mock_files and len(workshop.mock_files) == 8
    assert [r.id for r in workshop.economy.rules] == [
        "carousel_to_shelf",
        "drawers_to_doors",
        "lift_to_door",
        "no_bucket",
        "no_dryer",
    ]
    assert all(r.client_text.strip() and r.name.strip() and r.cases for r in workshop.economy.rules)


def test_check_workshop_lists_economy_file(home):
    checked = api.check_workshop(home)
    summary = {f.file: f for f in checked.files}
    assert checked.valid and summary["economy.yaml"].mock is True
    assert summary["economy.yaml"].counts == {"замены": 5}


def test_banner_follows_economy_file_alone(home):
    for name in OTHER_FILES:
        edit(home, f"{name}.yaml", "mock: true", "mock: false")
    only_economy = api.estimate_from_project(PETROV, home)
    assert only_economy.mock_files == ["economy.yaml"]
    assert only_economy.warning == MOCK_BANNER
    edit(home, "economy.yaml", "mock: true", "mock: false")
    clean = api.estimate_from_project(PETROV, home)
    assert clean.mock is False and clean.warning is None and clean.mock_files == []


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("замены:", "лишнее: 1\nзамены:", "лишнее"),
        ("шаблон: base_corner", "шаблон: base_cornet", "base_cornet"),
        ("убрать_вставку: bin_2sec", "убрать_вставку: bin_9", "bin_9"),
        ("вставка: bin_2sec", "вставка: bin_9", "bin_9"),
        ("убрать_подъёмник: true", "убрать_подъёмник_: true", "убрать_подъёмник_"),
        ("текст_клиенту:", "текст_клиенту_:", "текст_клиенту_"),
        ("шаблон: wall_door", "шаблон: base_door", "base_door"),
        ("полок: 1", "полок: -1", "полок"),
    ],
)
def test_bad_rule_is_load_error_naming_file_and_key(home, old, new, expected):
    edit(home, "economy.yaml", old, new)
    checked = api.check_workshop(home)
    assert not checked.valid
    assert any("economy.yaml" in e and expected in e for e in checked.errors), checked.errors
    assert not any("Input should" in e or "validation error" in e for e in checked.errors)


def test_duplicate_rule_id_is_load_error(home):
    edit(home, "economy.yaml", "id: no_dryer", "id: no_bucket")
    assert any("economy.yaml" in e and "no_bucket" in e for e in load_errors(home))


def test_rule_without_change_is_load_error(home):
    edit(home, "economy.yaml", "на: {убрать_вставку: bin_2sec}", "на: {}")
    assert any("economy.yaml" in e for e in load_errors(home))
