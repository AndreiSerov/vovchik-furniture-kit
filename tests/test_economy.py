"""Тикет 17: вариант «экономия» — замены из `economy.yaml` на копии проекта, цена тем же движком."""

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from kitchen import api

from tests.conftest import BASE_DOOR_PROJECT, REPO

PETROV = REPO / "tests" / "fixtures" / "petrov_project.json"
BRIEF = REPO / "tests" / "fixtures" / "petrov_brief.json"
ORDER = "2026-047-petrov"


def modules_of(project):
    return {m["label"]: m for wall in project["walls"] for row in wall["rows"] for m in row["modules"]}


def petrov_data():
    return json.loads(PETROV.read_text(encoding="utf-8"))


def write(tmp_path, project, name="variant.json"):
    path = tmp_path / name
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def price_of(home, project, name="variant.json"):
    return api.estimate_from_project(write(home, project, name), home).price


# Те же замены, что в economy.yaml, но выписанные вручную в JSON проекта: цену такого проекта считает
# обычная смета, без кода замен. Метки — модули Петрова.
def by_hand(project, *rules):
    project = copy.deepcopy(project)
    modules = modules_of(project)
    for rule in rules:
        if rule == "carousel_to_shelf":
            modules["Н4"]["contents"] = {"shelves": 1, "inserts": []}
        elif rule == "drawers_to_doors":
            modules["Н1"].update(template="base_door", contents={"shelves": 1})
        elif rule == "lift_to_door":
            modules["В1"]["contents"] = {}
            if modules["В5"]["template"] == "wall_dryer":
                modules["В5"]["contents"] = {}
        elif rule == "no_bucket":
            modules["Н5"]["contents"] = {"inserts": []}
        elif rule == "no_dryer":
            modules["В5"].update(template="wall_door", contents={"shelves": 1})
        else:
            raise AssertionError(rule)
    return project


RULES = ("carousel_to_shelf", "drawers_to_doors", "lift_to_door", "no_bucket", "no_dryer")
PETROV_MODULES = {
    "carousel_to_shelf": ["Н4"],
    "drawers_to_doors": ["Н1"],
    "lift_to_door": ["В1", "В5"],
    "no_bucket": ["Н5"],
    "no_dryer": ["В5"],
}


@pytest.fixture(scope="module")
def petrov_economy():
    home = REPO
    return api.estimate_from_project(PETROV, home, economy=True)


def test_all_five_rules_apply_to_petrov_and_differences_match_hand_variants(home, petrov_economy):
    economy = petrov_economy.economy
    as_is = api.estimate_from_project(PETROV, home).price
    assert economy.price_as_is == as_is == petrov_economy.price
    assert not economy.not_offered and not economy.problems
    assert [line.id for line in economy.replacements] == list(RULES)
    base = petrov_data()
    for line in economy.replacements:
        assert line.modules == PETROV_MODULES[line.id]
        single = price_of(home, by_hand(base, line.id))
        assert line.price == single
        assert line.saving == as_is - single > 0
        assert line.client_text.strip() and line.name.strip()
    together = price_of(home, by_hand(base, *RULES))
    assert economy.price == together and economy.saving == as_is - together > 0


def test_copy_of_petrov_has_no_carousel_tandembox_aventos_bucket_or_dryer(home):
    result = api.economy_parts_from_project(PETROV, home)
    templates = {m.module: m.template for m in result.modules}
    assert "base_drawers" not in templates.values() and "wall_dryer" not in templates.values()
    for banned in ("corner_carousel", "aventos_hks", "bin_2sec", "dryer_750"):
        assert banned not in result.hardware, banned
    # Tandembox остаётся только в пенале: один комплект, ящик пенала
    assert result.hardware["tandembox_M_500"] == 1
    tall = next(m for m in result.modules if m.template == "tall_oven")
    drawer_parts = [
        p for m in result.modules for p in m.parts if p.name in ("дно ящика", "задняя стенка ящика")
    ]
    assert drawer_parts and {p.module for p in drawer_parts} == {tall.module}
    original = api.parts_from_project(PETROV, home)
    assert original.hardware["corner_carousel"] == 1  # исходный проект не тронут


def test_economy_leaves_project_version_and_versions_table_untouched(home):
    api.brief_save(ORDER, BRIEF, home)
    api.import_project(ORDER, PETROV, home=home)
    db = home / "orders" / "kitchen.db"

    def snapshot():
        conn = sqlite3.connect(db)
        try:
            return (
                conn.execute("SELECT * FROM project_versions ORDER BY id").fetchall(),
                conn.execute(
                    "SELECT id, number, slug, client, created_at FROM orders ORDER BY id"
                ).fetchall(),
            )
        finally:
            conn.close()

    before = snapshot()
    result = api.estimate_and_save("petrov", home, economy=True)
    assert result.economy is not None and result.source.version == 1
    after = snapshot()
    assert after[0] == before[0] and len(after[0]) == 1
    assert after[1] == before[1]
    assert api.status("petrov", home).project_version == 1


def test_project_without_suitable_modules_has_no_replacements(home):
    path = write(home, BASE_DOOR_PROJECT, "plain.json")
    result = api.estimate_from_project(path, home, economy=True)
    assert result.economy.replacements == [] and result.economy.not_offered == []
    assert result.economy.price == result.economy.price_as_is == result.price
    assert result.economy.saving == 0


def three_drawers_project(widths):
    project = copy.deepcopy(BASE_DOOR_PROJECT)
    project["walls"][0]["length"] = 3600
    project["walls"][0]["rows"][0]["modules"] = [
        {"label": f"Н{i}", "template": "base_drawers", "width": w} for i, w in enumerate(widths, 1)
    ]
    return project


def test_module_that_does_not_build_is_reported_and_others_are_replaced(home):
    # ширина 601 — две двери (601 - 6) не делятся нацело: замена на этом модуле не строится
    path = write(home, three_drawers_project([600, 450, 601]), "drawers.json")
    result = api.estimate_from_project(path, home, economy=True)
    (line,) = result.economy.replacements
    assert line.id == "drawers_to_doors" and line.modules == ["Н1", "Н2"]
    assert len(result.economy.problems) == 1
    problem = result.economy.problems[0]
    assert problem.startswith("Н3:") and "не строится" in problem and "601" in problem
    expected = copy.deepcopy(three_drawers_project([600, 450, 601]))
    for module in expected["walls"][0]["rows"][0]["modules"][:2]:
        module.update(template="base_door", contents={"shelves": 1})
    assert line.price == price_of(home, expected, "expected.json")


def test_rule_that_builds_on_no_module_gives_only_a_problem(home):
    path = write(home, three_drawers_project([601]), "single.json")
    result = api.estimate_from_project(path, home, economy=True)
    assert result.economy.replacements == [] and len(result.economy.problems) == 1
    assert result.economy.price == result.economy.price_as_is


def test_replacement_that_does_not_save_is_not_offered_but_shown_with_reason(home):
    # цена ведра 0: замена «без ведра» не дешевеет, разница 0
    petrov_hardware = (home / "workshop" / "hardware.yaml").read_text(encoding="utf-8")
    assert 'id: bin_2sec, название: "Ведро выдвижное 2 секции", упаковка: 1, цена: 3500' in petrov_hardware
    (home / "workshop" / "hardware.yaml").write_text(
        petrov_hardware.replace(
            '"Ведро выдвижное 2 секции", упаковка: 1, цена: 3500',
            '"Ведро выдвижное 2 секции", упаковка: 1, цена: 0',
        ),
        encoding="utf-8",
    )
    api.brief_save(ORDER, BRIEF, home)
    api.import_project(ORDER, PETROV, home=home)
    result = api.estimate_and_save("petrov", home, economy=True)
    economy = result.economy
    assert "no_bucket" not in [line.id for line in economy.replacements]
    (skipped,) = economy.not_offered
    assert skipped.id == "no_bucket" and skipped.saving <= 0 and skipped.modules == ["Н5"]
    others = [r for r in RULES if r != "no_bucket"]
    assert [line.id for line in economy.replacements] == others
    assert economy.price == price_of(home, by_hand(petrov_data(), *others))
    markdown = Path(result.outputs[0].file).read_text(encoding="utf-8")
    assert "не удешевляет, не предложена" in markdown and skipped.name in markdown


def test_lift_to_door_on_wall_lift_module(home):
    project = copy.deepcopy(BASE_DOOR_PROJECT)
    project["walls"][0]["rows"] = [
        {"kind": "upper", "modules": [{"label": "В1", "template": "wall_lift", "width": 600}]}
    ]
    result = api.estimate_from_project(write(home, project, "lift.json"), home, economy=True)
    (line,) = result.economy.replacements
    assert line.id == "lift_to_door" and line.modules == ["В1"]
    expected = copy.deepcopy(project)
    expected["walls"][0]["rows"][0]["modules"][0]["template"] = "wall_door"
    assert line.price == price_of(home, expected, "door.json")
    parts = api.economy_parts_from_project(write(home, project, "lift.json"), home)
    assert [m.template for m in parts.modules] == ["wall_door"] and "aventos_hks" not in parts.hardware


def dryer_project(*, lift):
    project = copy.deepcopy(BASE_DOOR_PROJECT)
    module = {"label": "В5", "template": "wall_dryer", "width": 750}
    if lift:
        module["contents"] = {"lift": "aventos_hks"}
    project["walls"][0]["rows"] = [{"kind": "upper", "modules": [module]}]
    return project


def test_dryer_with_lift_lift_rule_and_no_dryer_rule_alone_and_together(home):
    # В5 как у Петрова: сушка с подъёмником. Каждое правило само по себе считается как проект, где
    # изменено только оно; вместе — без ошибки, сушки и подъёмника в копии нет.
    project = dryer_project(lift=True)
    result = api.estimate_from_project(write(home, project, "dryer.json"), home, economy=True)
    economy = result.economy
    lines = {line.id: line for line in economy.replacements}
    assert set(lines) == {"lift_to_door", "no_dryer"} and not economy.problems
    assert lines["lift_to_door"].modules == lines["no_dryer"].modules == ["В5"]
    assert lines["lift_to_door"].price == price_of(home, dryer_project(lift=False), "no_lift.json")
    no_dryer = copy.deepcopy(project)
    no_dryer["walls"][0]["rows"][0]["modules"][0].update(template="wall_door", contents={"shelves": 1})
    assert lines["no_dryer"].price == economy.price == price_of(home, no_dryer, "no_dryer.json")
    copy_parts = api.economy_parts_from_project(write(home, project, "dryer.json"), home)
    assert [m.template for m in copy_parts.modules] == ["wall_door"]
    assert "dryer_750" not in copy_parts.hardware and "aventos_hks" not in copy_parts.hardware


def test_economy_unaffected_estimate_is_the_same_with_and_without_flag(home, petrov_economy):
    plain = api.estimate_from_project(PETROV, home)
    assert plain.economy is None
    assert "economy" not in json.loads(plain.model_dump_json())
    data = json.loads(petrov_economy.model_dump_json())
    assert {k: v for k, v in data.items() if k != "economy"} == json.loads(plain.model_dump_json())
