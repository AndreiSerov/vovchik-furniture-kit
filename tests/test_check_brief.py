import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "kitchen-brief"
SCRIPT = SKILL / "scripts" / "check_brief.py"

spec = importlib.util.spec_from_file_location("check_brief", SCRIPT)
cb = importlib.util.module_from_spec(spec)
sys.modules["check_brief"] = cb
spec.loader.exec_module(cb)

SCHEMA = json.loads(cb.SCHEMA_PATH.read_text(encoding="utf-8"))
TODAY = date(2026, 11, 13)


def c(value):
    return {"value": value, "source": "client"}


def run(brief, **overrides):
    settings = cb.load_settings(cb.SETTINGS_PATH, TODAY)
    settings.update(overrides)
    return cb.check(brief, settings, SCHEMA)


def rules(result):
    return {f["rule"] for f in result["conflicts"]}


def sergey():
    """Бриф из сообщения Сергея — эталонный тест скилла."""
    return {
        "client": {"name": c("Сергей"), "phone": c("+7 916 482-17-35")},
        "room": {
            "shape": c("corner"),
            "walls": c([{"name": "A", "length_mm": 2800}, {"name": "B", "length_mm": 1800}]),
            "ceiling_height_mm": c(2700),
            "window": c({"present": True, "wall": "B", "radiator_below": True}),
            "water": c({"position": "в углу"}),
        },
        "appliances": {
            "items": c(
                [
                    {"type": "sink", "location": "возле окна"},
                    {"type": "dishwasher", "width_mm": 450, "who_buys": "client"},
                    {"type": "oven", "location": "в высоком шкафу", "who_buys": "client"},
                    {"type": "microwave", "location": "в высоком шкафу", "who_buys": "client"},
                    {"type": "hob", "fuel": "induction", "who_buys": "client"},
                ]
            ),
            "fridge": c({"existing": True, "width_mm": 600, "location": "у входа"}),
            "hood": c({"kind": "built_in"}),
        },
        "style": {"colors": c("низ серый, верх белый"), "fronts_finish": c("matte")},
        "storage": {"upper_height": c("to_ceiling")},
        "countertop": {"material": c("под камень")},
        "budget": {
            "amount_rub": c(400000),
            "includes_assembly": c(True),
            "includes_appliances": c(False),
        },
        "timeline": {"desired_date": c("2027-01-31"), "desired_date_text": c("к концу января")},
    }


def test_sergey_brief_finds_expected_problems():
    result = run(sergey())
    assert {
        "window_uppers",
        "sink_window",
        "tall_uppers_budget",
        "builtin_without_model",
        "hood_exhaust",
        "renovation_stage",
        "fridge_door",
    } <= rules(result)
    assert result["summary"]["running_meters"] == 4.0  # 2,8 + 1,8 − 0,6 на угол
    assert result["unknown_fields"] == [] and result["errors"] == []


def test_every_finding_has_both_texts_and_valid_ask():
    for f in run(sergey())["conflicts"]:
        assert f["designer_note"] and f["client_question"]
        assert f["ask"] in {"now", "measure"}
        assert f["severity"] in {"conflict", "risk", "question"}


def test_missing_required_marks_paths_covered_by_rules():
    missing = {m["path"]: m for m in run(sergey())["missing_required"]}
    assert "client.address" in missing and missing["client.address"]["covered_by"] is None
    assert missing["room.renovation_stage"]["covered_by"] == "renovation_stage"
    assert "budget.amount_rub" not in missing


def test_tall_uppers_threshold_scales_with_length():
    brief = sergey()
    brief["budget"]["amount_rub"] = c(440000)  # 4,0 п. м → порог 450 000
    assert "tall_uppers_budget" in rules(run(brief))
    brief["budget"]["amount_rub"] = c(460000)
    assert "tall_uppers_budget" not in rules(run(brief))


def test_budget_tight_without_reserve():
    brief = sergey()
    brief["budget"]["amount_rub"] = c(480000)  # выше 450 000, но меньше +10%
    assert "budget_tight" in rules(run(brief))
    brief["budget"]["flexibility"] = c("flexible")
    assert "budget_tight" not in rules(run(brief))


def test_budget_below_standard_minimum():
    brief = sergey()
    brief["storage"]["upper_height"] = c("720")
    brief["budget"]["amount_rub"] = c(250000)
    assert "budget_tight" in rules(run(brief))


def test_threshold_comes_from_settings():
    assert "tall_uppers_budget" not in rules(run(sergey(), min_budget_to_ceiling_per_reference_length=300000))


def test_disabled_rule_is_skipped():
    assert "fridge_door" not in rules(run(sergey(), disabled_rules=["fridge_door"]))


def test_rough_renovation_and_tight_deadline():
    brief = sergey()
    brief["room"]["renovation_stage"] = c("rough")
    brief["timeline"]["desired_date"] = c("2026-12-10")
    found = {f["rule"]: f for f in run(brief)["conflicts"]}
    assert found["renovation_stage"]["severity"] == "risk"
    assert "нереален" in found["deadline"]["designer_note"]


def test_gas_with_gas_hob_mentions_hood_height():
    brief = sergey()
    brief["room"]["gas"] = c({"present": True})
    brief["appliances"]["items"]["value"][-1]["fuel"] = "gas"
    gas = next(f for f in run(brief)["conflicts"] if f["rule"] == "gas")
    assert "750" in gas["designer_note"]


def test_gas_hob_without_gas_is_conflict():
    brief = sergey()
    brief["appliances"]["items"]["value"][-1]["fuel"] = "gas"
    gas = next(f for f in run(brief)["conflicts"] if f["rule"] == "gas")
    assert gas["severity"] == "conflict"


@pytest.mark.parametrize("budget", [{}, {"amount_rub": c(400000), "includes_assembly": c(False)}])
def test_budget_unclear(budget):
    brief = sergey()
    brief["budget"] = budget
    assert "budget_unclear" in rules(run(brief))


def test_island_with_sink_needs_floor_utilities():
    brief = sergey()
    brief["room"]["has_island"] = c(True)
    brief["room"]["renovation_stage"] = c("rough")
    brief["appliances"]["items"]["value"][0]["location"] = "на острове"
    island = next(f for f in run(brief)["conflicts"] if f["rule"] == "island_utilities")
    assert "канализация" in island["designer_note"] and island["ask"] == "now"
    assert "Когда планируется стяжка" in island["client_question"]


def u_island(island_length):
    brief = sergey()
    brief["room"]["shape"] = c("u_shape")
    brief["room"]["walls"] = c(
        [{"name": "A", "length_mm": 4200}, {"name": "B", "length_mm": 2500}, {"name": "C", "length_mm": 2500}]
    )
    brief["room"]["island"] = c({"length_mm": island_length, "depth_mm": 1000})
    return brief


def test_island_too_long_for_u_is_conflict():
    found = {f["rule"]: f for f in run(u_island(2400))["conflicts"]}
    assert found["island_fits"]["severity"] == "conflict"
    assert "3000" in found["island_fits"]["designer_note"]


def test_island_that_fits_goes_to_measure():
    found = {f["rule"]: f for f in run(u_island(1200))["conflicts"]}
    assert found["island_fits"]["ask"] == "measure"


def test_island_counts_in_running_meters():
    # 4,2 + 2,5 + 2,5 − 2×0,6 + 2,4
    assert run(u_island(2400))["summary"]["running_meters"] == 10.4


def test_empty_hood_and_fridge_are_not_marked_covered():
    brief = sergey()
    del brief["appliances"]["hood"], brief["appliances"]["fridge"]
    missing = {m["path"]: m for m in run(brief)["missing_required"]}
    assert missing["appliances.hood"]["covered_by"] is None
    assert missing["appliances.fridge"]["covered_by"] is None


def test_unclear_built_in_is_a_question_not_conflict():
    found = [f for f in run(sergey())["conflicts"] if f["rule"] == "builtin_without_model"]
    severities = {f["severity"] for f in found}
    assert severities == {"conflict", "question"}  # вытяжка без модели + неясно про посудомойку и др.


def test_existing_freestanding_cooker_needs_no_model():
    brief = sergey()
    brief["appliances"]["items"] = c([{"type": "cooker", "fuel": "gas", "keep_existing": True}])
    brief["appliances"]["hood"] = c({"kind": "wall"})
    brief["room"]["gas"] = c({"present": True})
    result = run(brief)
    assert "builtin_without_model" not in rules(result)
    gas = next(f for f in result["conflicts"] if f["rule"] == "gas")
    assert "750" in gas["designer_note"]


def test_unknown_gas_and_unknown_hob_fuel_asks_both():
    brief = sergey()
    brief["appliances"]["items"]["value"][-1].pop("fuel")
    gas = next(f for f in run(brief)["conflicts"] if f["rule"] == "gas")
    assert "индукционную" in gas["client_question"]


def test_budget_question_asks_only_real_gaps():
    brief = sergey()
    del brief["budget"]["includes_assembly"]
    q = next(f for f in run(brief)["conflicts"] if f["rule"] == "budget_unclear")["client_question"]
    assert "сборка" in q and "техника" not in q


def test_window_not_on_upper_wall_is_fine():
    brief = sergey()
    brief["storage"]["upper_walls"] = c(["A"])
    assert "window_uppers" not in rules(run(brief))


def test_unknown_fields_and_bad_date_reported():
    brief = sergey()
    brief["room"]["windw"] = c({})
    brief["timeline"]["desired_date"] = c("конец января")
    result = run(brief)
    assert "room.windw" in result["unknown_fields"]
    assert result["errors"]


def test_raw_values_without_wrapper_are_accepted():
    brief = {"storage": {"upper_height": "to_ceiling"}, "budget": {"amount_rub": 300000}}
    assert "tall_uppers_budget" in rules(run(brief))


def test_cli_outputs_json(tmp_path):
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(sergey(), ensure_ascii=False), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--today", "2026-11-13"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    assert set(data) == {"summary", "missing_required", "conflicts", "unknown_fields", "errors"}
