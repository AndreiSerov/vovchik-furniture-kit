"""Тикет 65: уборка брифа (М1, М3 и пункты ревью тикетов 62 и 63)."""

import pytest

from tests.test_check_brief import SCHEMA, SKILL, c, cb, rules, run, sergey, with_fridge

FRIDGE_RULES = {"fridge_place", "over_fridge"}


def test_built_in_fridge_out_of_kitchen_needs_no_model():
    """Проверка 2: холодильник `wall: none` без модели не даёт ни конфликта, ни вопроса."""
    result = run(with_fridge(sergey(), wall="none", built_in=True))
    for f in result["conflicts"]:
        assert "холодильник" not in f["designer_note"] + f["client_question"]
        assert "appliances.fridge" not in f["paths"]
    assert result["errors"] == []


def test_built_in_fridge_on_a_wall_without_model_is_still_a_conflict():
    result = run(with_fridge(sergey(), wall="A", built_in=True))
    found = [
        f for f in result["conflicts"] if f["rule"] == "builtin_without_model" and f["severity"] == "conflict"
    ]
    assert any("холодильник" in f["designer_note"] for f in found)


def test_fridge_wall_none_with_spaces_is_not_in_kitchen():
    """Проверка 3."""
    brief = with_fridge(sergey(), wall=" none ")
    brief["storage"]["upper_height"] = c("to_ceiling")
    assert not FRIDGE_RULES & rules(run(brief))


def test_wrong_wall_length_type_is_one_error_without_traceback():
    """Проверка 4."""
    brief = sergey()
    brief["room"]["walls"] = c([{"name": "A", "length_mm": "1800"}, {"name": "B", "length_mm": 1800}])
    brief["budget"] = {"amount_rub": c(300000)}
    brief["storage"]["upper_height"] = c("to_ceiling")
    errors = run(brief)["errors"]
    assert errors == ["room.walls[0].length_mm: '1800' — ожидалось целое число"]


def test_fuel_typo_is_an_enum_error():
    """Проверка 5."""
    bad = sergey()
    bad["appliances"]["items"]["value"].append({"type": "hob", "fuel": "plasma"})
    assert any("appliances.items[" in e and "'plasma' не из перечня" in e for e in run(bad)["errors"])
    good = sergey()
    good["appliances"]["items"]["value"].append({"type": "hob", "fuel": "gas"})
    assert not any("fuel" in e for e in run(good)["errors"])


def test_fuel_description_starts_with_enum():
    desc = SCHEMA["sections"]["appliances"]["fields"]["items"]["item"]["fuel"]
    assert desc.startswith("enum: ") and cb._enum_values(desc) == ["induction", "electric", "gas"]


def test_item_description_is_parsed_in_one_place_and_empty_is_safe():
    assert cb._item_kind("") == ""
    assert cb._item_kind("string: бренд") == "string"
    assert cb._item_kind("enum: a, b") == "enum"


def test_protrusion_size_description_gives_a_string_example():
    desc = SCHEMA["sections"]["room"]["fields"]["protrusions"]["item"]["size_mm"]
    assert desc.startswith("string") and "300×250" in desc


def test_skill_step_7_names_the_placeholder_type():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "ожидалось <тип>" in text
    assert "ожидалось целое число | строка" not in text


@pytest.mark.parametrize("bad", ["1800", True])
@pytest.mark.parametrize("budget", [100000, 300000])
def test_budget_rules_are_silent_and_meters_none_on_wrong_length_type(bad, budget):
    brief = sergey()
    brief["room"]["walls"] = c([{"name": "A", "length_mm": bad}, {"name": "B", "length_mm": 1800}])
    brief["budget"] = {"amount_rub": c(budget)}
    brief["storage"]["upper_height"] = c("to_ceiling")
    result = run(brief)
    assert result["summary"]["running_meters"] is None
    assert not {"tall_uppers_budget", "budget_tight"} & rules(result)
    assert len(result["errors"]) == 1


def test_island_length_type_error_gives_no_partial_meters():
    brief = sergey()
    brief["room"]["island"] = c({"length_mm": "1500"})
    result = run(brief)
    assert result["errors"] == ["room.island.length_mm: '1500' — ожидалось целое число"]
    assert result["summary"]["running_meters"] is None
