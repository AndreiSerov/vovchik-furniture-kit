"""Тикет 63 (а): типы полей брифа по схеме — в check_brief.check и в отказе brief save."""

import json
from datetime import date
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

from tests.test_check_brief import SCHEMA, TODAY, cb, run, sergey

PETROV = Path(__file__).parent / "fixtures" / "petrov_brief.json"


def petrov():
    return json.loads(PETROV.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("kitchen.store.today", lambda: date(2026, 11, 14))


def test_fridge_string_is_an_error_at_its_path():
    brief = petrov()
    brief["appliances"]["fridge"] = {"value": "60 см у входа", "source": "client"}
    errors = run(brief)["errors"]
    assert "appliances.fridge: '60 см у входа' — ожидалось объект" in errors


def test_brief_save_rejects_fridge_string(home, tmp_path):
    brief = petrov()
    brief["appliances"]["fridge"] = "60 см у входа"
    p = tmp_path / "b.json"
    p.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.brief_save("petrov", p)
    assert "Ошибка в брифе: appliances.fridge: '60 см у входа' — ожидалось объект" in e.value.problems
    assert "Бриф не сохранён." in e.value.problems


@pytest.mark.parametrize(
    ("bad", "ok"),
    [("2700", False), (2700.0, True), (True, False), (2700.5, False), (2700, True)],
)
def test_integer_field_is_strict(bad, ok):
    brief = petrov()
    brief["room"]["ceiling_height_mm"] = {"value": bad, "source": "client"}
    errors = run(brief)["errors"]
    if ok:
        assert errors == []
    else:
        assert f"room.ceiling_height_mm: {bad!r} — ожидалось целое число" in errors


def test_string_boolean_and_nested_keys_are_typed():
    brief = petrov()
    brief["client"]["name"] = {"value": 7, "source": "client"}
    brief["room"]["has_island"] = {"value": "да", "source": "client"}
    brief["appliances"]["hood"]["value"]["width_mm"] = "600"
    brief["room"]["walls"]["value"][1]["length_mm"] = "1800"
    errors = run(brief)["errors"]
    assert "client.name: 7 — ожидалось строка" in errors
    assert "room.has_island: 'да' — ожидалось да/нет" in errors
    assert "appliances.hood.width_mm: '600' — ожидалось целое число" in errors
    assert "room.walls[1].length_mm: '1800' — ожидалось целое число" in errors


def test_list_field_and_its_items_are_typed():
    brief = petrov()
    brief["room"]["walls"] = {"value": "A 2800", "source": "client"}
    brief["appliances"]["items"]["value"][2] = "варочная"
    errors = run(brief)["errors"]
    assert "room.walls: 'A 2800' — ожидалось список" in errors
    assert "appliances.items[2]: 'варочная' — ожидалось объект" in errors


def test_enum_in_list_item_has_path_with_index():
    brief = petrov()
    brief["appliances"]["items"]["value"][0]["type"] = "toaster"
    errors = run(brief)["errors"]
    assert any(e.startswith("appliances.items[0].type: 'toaster' не из перечня") for e in errors), errors


def test_empty_values_are_not_checked():
    brief = petrov()
    brief["room"]["ceiling_height_mm"] = {"value": "", "source": "client"}
    brief["appliances"]["fridge"] = {"value": {}, "source": "client"}
    brief["client"]["email"] = {"value": None, "source": "client"}
    assert run(brief)["errors"] == []


def test_good_briefs_have_no_errors():
    assert run(petrov())["errors"] == []
    assert run(sergey())["errors"] == []


def test_script_and_save_give_the_same_errors(home, tmp_path):
    brief = petrov()
    brief["room"]["ceiling_height_mm"] = {"value": "2700", "source": "client"}
    direct = cb.check(brief, cb.load_settings(cb.SETTINGS_PATH, TODAY), SCHEMA)["errors"]
    p = tmp_path / "b.json"
    p.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.brief_save("petrov", p)
    assert [f"Ошибка в брифе: {x}" for x in direct] == [
        x for x in e.value.problems if x.startswith("Ошибка в брифе")
    ]
