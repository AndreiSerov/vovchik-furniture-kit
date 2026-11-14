"""Техника в проверках: требования берёт бриф заказа, а нишу строит проект (шаблон), как в расчёте деталей."""

import json

import pytest
from kitchen import api
from kitchen import brief as brief_mod
from kitchen.errors import OrderError
from kitchen.models import APPLIANCE_KINDS

from tests.test_check_brief import c, sergey
from tests.test_checks import findings, make_project, single, with_appliances

ORDER = "2026-047-petrov"


def save_brief(home, items=(), hood=None, name="brief.json"):
    b = sergey()
    b["appliances"]["items"] = c(list(items))
    if hood is None:
        del b["appliances"]["hood"]
    else:
        b["appliances"]["hood"] = c(hood)
    path = home / name
    path.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    return api.brief_save(ORDER, path, home)


def niche(w, h, d):
    return {"niche_width_mm": w, "niche_height_mm": h, "niche_depth_mm": d}


def dw(**extra):
    return {"type": "dishwasher", "built_in": True, "model": "Bosch SMV", **extra}


def oven(**extra):
    return {"type": "oven", "built_in": True, "model": "Bosch HBG", **extra}


def project_dw(home, **extra):
    appliance = {"type": "dishwasher", "width": 450, "module": "Н1", **extra}
    return with_appliances(single(home, "dishwasher_panel", 450), appliance)


def project_oven(home, **extra):
    appliance = {"type": "oven", "width": 600, "module": "Н1", **extra}
    return with_appliances(single(home, "tall_oven", 600), appliance)


def project_hood(home, **extra):
    appliance = {"type": "hood", "width": 600, "module": "В1", **extra}
    return with_appliances(single(home, "wall_hood", 600, row="upper"), appliance)


def errors(path, home):
    return [f for f in api.check_project(path, home).findings if f.level == "error"]


# --- какой бриф --------------------------------------------------------------------


def test_no_brief_is_a_warning_and_does_not_block(home):
    api.order_new(ORDER, client="Петров П. П.")
    result = api.check_project(project_dw(home), home)
    [f] = [f for f in result.findings if f.code == "brief_missing"]
    assert f.level == "warning" and "брифа нет, техника не сверена" in f.message.lower()
    assert "нет брифа" not in f.message.lower()
    assert result.brief_id is None and result.errors == []


def test_brief_present_means_no_missing_warning_and_result_names_the_brief(home):
    save_brief(home, [dw()])
    result = api.check_project(project_dw(home), home)
    assert [f for f in result.findings if f.code == "brief_missing"] == []
    assert result.brief_id == 1 and result.brief_saved_at


def test_latest_brief_is_used(home):
    save_brief(home, [])
    save_brief(home, [dw()], name="b2.json")
    assert api.check_project(project_dw(home), home).brief_id == 2


# --- состав и привязка к шаблонам ------------------------------------------------------


def test_built_in_appliance_without_module_is_an_error(home):
    save_brief(home, [dw()])
    [f] = findings(single(home, "base_door"), home, "appliance_unbound")
    assert f.level == "error" and "Bosch SMV" in f.message and f.subject.startswith("посудомойка")
    assert findings(project_dw(home), home, "appliance_unbound") == []


def test_not_built_in_appliance_needs_no_module(home):
    save_brief(home, [{"type": "dishwasher", "built_in": False}])
    assert findings(single(home, "base_door"), home, "appliance_unbound") == []


def test_appliance_no_template_accepts_is_a_warning(home):
    save_brief(home, [{"type": "coffee_machine", "built_in": True, "model": "Delonghi"}])
    [f] = findings(single(home, "base_door"), home, "appliance_no_template")
    assert f.level == "warning" and "шаблона под «" in f.message and "Delonghi" in f.message
    assert findings(single(home, "base_door"), home, "appliance_unbound") == []


def test_incompatible_appliance_and_module_is_an_error(home):
    save_brief(home, [oven()], hood={"kind": "built_in"})
    hood_below = with_appliances(single(home, "base_door"), {"type": "hood", "width": 600, "module": "Н1"})
    [f] = findings(hood_below, home, "appliance_wrong_module")
    assert (f.level, f.subject) == ("error", "Н1")
    oven_above = with_appliances(
        single(home, "wall_door", row="upper"), {"type": "oven", "width": 600, "module": "В1"}
    )
    assert [f.subject for f in findings(oven_above, home, "appliance_wrong_module")] == ["В1"]
    assert findings(project_oven(home), home, "appliance_wrong_module") == []


def test_oven_under_hob_in_base_door_is_a_warning_not_an_error(home):
    save_brief(home, [oven(), {"type": "hob", "built_in": True, "model": "Hob"}])
    path = with_appliances(
        single(home, "base_door"),
        {"type": "hob", "width": 600, "module": "Н1"},
        {"type": "oven", "width": 600, "module": "Н1"},
    )
    assert errors(path, home) == []
    [f] = findings(path, home, "appliance_no_niche")
    assert (
        f.level == "warning" and "не строит нишу под «oven»" in f.message and "проверьте вручную" in f.message
    )
    # варочная — на столешнице, шаблон ей ничего не строит и не жалуется
    assert all("hob" not in f.message for f in findings(path, home, "appliance_no_niche"))


def test_hob_over_base_corner_is_allowed(home):
    save_brief(home, [{"type": "hob", "built_in": True, "model": "Hob"}])
    corner = {"label": "Н1", "template": "base_corner", "width": 1050, "corner": {"adjacent_wall": "Б"}}
    walls = [("A", 1050, [("lower", [corner])]), ("Б", 1800, [])]
    path = with_appliances(
        make_project(home / "p.json", walls), {"type": "hob", "width": 600, "module": "Н1"}
    )
    assert errors(path, home) == []
    assert findings(path, home, "appliance_no_niche") == []


def test_no_template_warning_is_not_given_when_a_template_exists_or_not_built_in(home):
    save_brief(home, [dw(), {"type": "coffee_machine", "built_in": False}])
    assert findings(single(home, "base_door"), home, "appliance_no_template") == []
    assert len(findings(single(home, "base_door"), home, "appliance_unbound")) == 1  # ПММ — другое


def test_count_per_type_is_compared(home):
    save_brief(home, [dw(model="A"), dw(model="B")])
    [f] = findings(project_dw(home, model="A"), home, "appliance_unbound")
    assert f.level == "error" and "B" in f.message


def test_project_appliance_missing_in_brief_is_a_warning(home):
    save_brief(home, [])
    [f] = findings(project_dw(home), home, "appliance_not_in_brief")
    assert (f.level, f.subject) == ("warning", "Н1")
    save_brief(home, [dw()], name="b2.json")
    assert findings(project_dw(home), home, "appliance_not_in_brief") == []


def test_built_in_hood_from_brief_object_needs_a_module(home):
    save_brief(home, [], hood={"kind": "built_in", "width_mm": 600})
    [f] = findings(single(home, "base_door"), home, "appliance_unbound")
    assert "вытяжка" in f.message
    save_brief(home, [], hood={"kind": "island"}, name="b2.json")
    assert findings(single(home, "base_door"), home, "appliance_unbound") == []


def test_pairing_goes_by_model_first(home):
    save_brief(home, [dw(model="Big", **niche(600, 820, 560)), dw(model="Compact", **niche(450, 820, 560))])
    path = project_dw(home, model="Compact")
    [f] = findings(path, home, "appliance_unbound")
    assert "Big" in f.message and "Compact" not in f.message
    assert findings(path, home, "appliance_pairing") == []
    # с Compact сверена ниша 450, а не 600 из первого по порядку Big
    assert findings(path, home, "niche_too_small") == []


def test_pairing_without_models_goes_by_order_with_a_warning(home):
    save_brief(home, [dw(model=None), dw(model=None)])
    [f] = findings(project_dw(home), home, "appliance_pairing")
    assert f.level == "warning" and "без моделей" in f.message and "по порядку" in f.message
    save_brief(home, [dw(model=None)], name="b2.json")
    assert findings(project_dw(home), home, "appliance_pairing") == []


# --- ниша: требование из брифа против ниши, которую строит модуль ------------------------------


def test_oven_body_bigger_than_niche_is_not_an_error(home):
    # корпус 595×595×550 в пенале 600, а размеры встраивания — 560×590×550
    save_brief(home, [oven(width_mm=595, height_mm=595, depth_mm=550, **niche(560, 590, 550))])
    assert errors(project_oven(home), home) == []


def test_hood_598_in_wall_hood_600_is_not_an_error(home):
    save_brief(home, [], hood={"kind": "built_in", "width_mm": 598, **niche(560, 180, 280)})
    assert errors(project_hood(home), home) == []


def test_brief_niche_taller_than_tall_module_builds_is_an_error(home):
    save_brief(home, [oven(**niche(560, 600, 550))])
    [f] = findings(project_oven(home), home, "niche_too_small")
    assert (f.level, f.value, f.limit) == ("error", 600, 590)  # в брифе 600, пенал строит 590
    assert "600" in f.message and "590" in f.message


def test_built_niche_comes_from_the_project(home):
    save_brief(home, [oven(**niche(560, 600, 550))])
    # в проекте ниша 610 высотой — именно её строит шаблон
    assert findings(project_oven(home, niche_height=610), home, "niche_too_small") == []
    save_brief(home, [oven(**niche(560, 620, 550))], name="b2.json")
    [f] = findings(project_oven(home, niche_height=610), home, "niche_too_small")
    assert (f.value, f.limit) == (620, 610)


def test_niche_in_brief_differing_from_built_is_a_warning_to_carry_into_project(home):
    save_brief(home, [oven(**niche(560, 585, 550))])  # пенал строит по каталогу 590
    [f] = findings(project_oven(home), home, "niche_not_in_project")
    assert f.level == "warning" and "перенесите размеры встраивания из брифа в проект" in f.message
    carried = project_oven(home, niche_height=585, niche_depth=550)
    assert findings(carried, home, "niche_not_in_project") == []


def test_niche_in_brief_equal_to_catalog_needs_no_carrying(home):
    save_brief(home, [oven(**niche(560, 590, 550))])
    assert findings(project_oven(home), home, "niche_not_in_project") == []


def test_dishwasher_panel_builds_no_niche_from_project_so_no_carry_warning(home):
    save_brief(home, [dw(**niche(450, 800, 500))])
    assert findings(project_dw(home), home, "niche_not_in_project") == []


def test_dishwasher_niche_against_module_space(home):
    save_brief(home, [dw(**niche(450, 830, 570))])
    found = sorted((f.value, f.limit) for f in findings(project_dw(home), home, "niche_too_small"))
    assert found == [(570, 560), (830, 820)]  # высота модуля 720 + ножки 100
    save_brief(home, [dw(**niche(450, 820, 560))], name="b2.json")
    assert findings(project_dw(home), home, "niche_too_small") == []


def test_body_dimensions_are_not_compared_with_niche(home):
    save_brief(home, [dw(width_mm=600, height_mm=900, depth_mm=700, **niche(450, 820, 560))])
    assert findings(project_dw(home), home, "niche_too_small") == []


def test_countertop_types_come_from_construction_yaml(home):
    save_brief(home, [{"type": "hob", "built_in": True, "model": "Hob", **niche(770, 50, 520)}])
    hob = {"type": "hob", "width": 600, "module": "Н1"}
    path = with_appliances(single(home, "base_door", 800), hob)
    assert findings(path, home, "niche_too_small") == []
    cfg = home / "workshop/construction.yaml"
    cfg.write_text(
        cfg.read_text().replace("техника_в_столешницу: [hob, sink]", "техника_в_столешницу: [sink]")
    )
    [f] = findings(path, home, "niche_too_small")
    assert (f.value, f.limit) == (770, 768)


def test_standard_hob_over_standard_module_is_not_an_error(home):
    save_brief(home, [{"type": "hob", "built_in": True, "model": "Hob"}])
    for template in ("base_door", "base_drawers"):
        hob = {"type": "hob", "width": 600, "module": "Н1"}
        assert errors(with_appliances(single(home, template), hob), home) == []


def test_appliance_must_refer_to_existing_module(home):
    path = project_dw(home, module="Н9")
    [f] = findings(path, home, "appliance_module")
    assert (f.level, f.subject) == ("error", "Н9")
    assert findings(project_dw(home), home, "appliance_module") == []


# --- нет размеров встраивания: типовая ниша каталога -------------------------------------------


def test_missing_niche_in_brief_takes_catalog_with_warning(home):
    save_brief(home, [dw()])
    [f] = findings(project_dw(home), home, "appliance_catalog_dims")
    assert f.level == "warning" and "Bosch SMV" in f.message
    assert "уточните размеры встраивания по инструкции модели" in f.message
    assert findings(project_dw(home), home, "niche_too_small") == []


def test_catalog_warning_only_when_catalog_is_really_used(home):
    # ширина 450 равна типоразмеру, но бриф дал все размеры встраивания
    save_brief(home, [dw(width_mm=450, **niche(450, 820, 560))])
    assert findings(project_dw(home), home, "appliance_catalog_dims") == []


def test_catalog_niche_is_the_requirement_when_brief_is_silent(home):
    a = home / "workshop/appliances.yaml"
    a.write_text(a.read_text().replace("ниша_высота: 820", "ниша_высота: 900"))
    save_brief(home, [dw()])
    [f] = findings(project_dw(home), home, "niche_too_small")
    assert (f.value, f.limit) == (900, 820)


def test_no_brief_uses_catalog_requirement(home):
    a = home / "workshop/appliances.yaml"
    a.write_text(a.read_text().replace("ниша_высота: 820", "ниша_высота: 900"))
    [f] = findings(project_dw(home), home, "niche_too_small")
    assert (f.value, f.limit) == (900, 820)


def test_unknown_appliance_size_without_niche_is_a_warning(home):
    hob = {"type": "hob", "built_in": True, "model": "Wide"}
    path = with_appliances(single(home, "base_door"), {"type": "hob", "width": 700, "module": "Н1"})
    save_brief(home, [hob])
    [f] = findings(path, home, "appliance_no_data")
    assert (f.level, f.subject) == ("warning", "Н1")
    # с размерами встраивания данных хватает
    save_brief(home, [{**hob, **niche(690, 50, 500)}], name="b2.json")
    assert findings(path, home, "appliance_no_data") == []


# --- размеры в брифе -----------------------------------------------------------------------------


def test_dimensions_string_of_old_brief_is_parsed(home):
    save_brief(home, [dw(dimensions="450×830×560")])
    brief = brief_mod.appliances(api.brief_show(ORDER, home).brief)[0]
    assert (brief.width, brief.height, brief.depth) == (450, 830, 560)
    assert findings(project_dw(home), home, "brief_dimensions") == []


def test_unrecognised_dimensions_string_is_a_warning(home):
    save_brief(home, [dw(dimensions="большая")])
    [f] = findings(project_dw(home), home, "brief_dimensions")
    assert f.level == "warning" and "большая" in f.message


def test_dimensions_string_formats():
    parse = brief_mod.parse_dimensions
    assert parse("450x820x560") == (450, 820, 560)
    assert parse("450 х 820 × 560 мм") == (450, 820, 560)
    assert parse("") is None and parse("450") is None


def test_brief_schema_has_body_and_niche_dimensions(repo):
    schema = json.loads((repo / "skills/kitchen-brief/references/brief_schema.json").read_text())
    fields = schema["sections"]["appliances"]["fields"]
    for item in (fields["items"]["item"], fields["hood"]["item"]):
        for key in ("height_mm", "depth_mm", "niche_width_mm", "niche_height_mm", "niche_depth_mm"):
            assert key in item


# --- виды техники в мастерской -----------------------------------------------------------------


def test_workshop_appliance_kinds_must_exist_in_brief_schema(home):
    cfg = home / "workshop/construction.yaml"
    cfg.write_text(
        cfg.read_text().replace("техника_в_столешницу: [hob, sink]", "техника_в_столешницу: [hobb]")
    )
    result = api.check_workshop(home)
    assert not result.valid and any("hobb" in e for e in result.errors)


# --- импорт и версия ------------------------------------------------------------------------------


def test_import_records_brief_used_in_the_version_and_log(home):
    api.order_new(ORDER, client="Петров П. П.")
    save_brief(home, [dw(**niche(450, 820, 560))])
    result = api.import_project("petrov", project_dw(home))
    assert result.brief_id == 1
    assert "брифом №1" in api.status("petrov").text


def test_reimport_unchanged_returns_brief_recorded_with_version(home):
    api.order_new(ORDER, client="Петров П. П.")
    save_brief(home, [dw(**niche(450, 820, 560))])
    path = project_dw(home)
    api.import_project("petrov", path)
    save_brief(home, [dw(**niche(450, 820, 560))], name="b2.json")
    again = api.import_project("petrov", path)
    assert not again.changed and again.brief_id == 1


def test_import_with_unbound_built_in_appliance_is_rejected(home):
    save_brief(home, [dw()])
    with pytest.raises(OrderError, match="Bosch SMV"):
        api.import_project("petrov", single(home, "base_door"))


# --- бриф Петрова (понадобится в тикете 11) -------------------------------------------------------


def test_petrov_brief_fixture_has_all_appliances_with_niches(repo, home):
    api.brief_save(ORDER, repo / "tests/fixtures/petrov_brief.json", home)
    items = brief_mod.appliances(api.brief_show(ORDER, home).brief)
    assert sorted(a.type for a in items) == ["dishwasher", "hob", "hood", "microwave", "oven", "sink"]
    assert all(a.built_in for a in items)
    for a in items:
        if a.type in ("oven", "microwave", "dishwasher", "hood"):
            assert None not in (a.niche_width, a.niche_height, a.niche_depth), a.type


# --- виды техники: константа движка, а не файл скилла -------------------------------------


def test_engine_appliance_kinds_cover_brief_schema(repo):
    import re

    schema = json.loads((repo / "skills/kitchen-brief/references/brief_schema.json").read_text())
    text = schema["sections"]["appliances"]["fields"]["items"]["item"]["type"]
    in_schema = {
        m.group(1) for part in text.split("enum:", 1)[1].split(",") if (m := re.match(r"\s*([a-z_]+)", part))
    }
    assert in_schema and in_schema <= APPLIANCE_KINDS and "hood" in APPLIANCE_KINDS


def test_workshop_loads_without_the_skill_schema(home, monkeypatch):
    monkeypatch.setattr(brief_mod, "SKILL", home / "no-skill")
    assert api.check_workshop(home).valid


# --- размеры техники, которые не удалось прочитать ----------------------------------------------


def test_unreadable_appliance_size_is_named_even_when_module_was_not_built(home):
    save_brief(home, [oven()])
    path = with_appliances(
        single(home, "tall_oven", 600, depth=450),
        {"type": "oven", "width": 600, "niche_height": 590.5, "module": "Н1"},
    )
    assert findings(path, home, "drawer_depth") and findings(path, home, "module_build") == []
    [f] = findings(path, home, "appliance_size")
    assert f.level == "error" and f.subject == "Н1"


def test_unreadable_appliance_size_is_not_repeated_when_module_build_names_it(home):
    save_brief(home, [oven()])
    path = with_appliances(
        project_oven(home), {"type": "oven", "width": 600, "niche_height": 590.5, "module": "Н1"}
    )
    assert findings(path, home, "module_build") and findings(path, home, "appliance_size") == []
