"""Техника для проверок берётся из последнего брифа заказа (а моковый appliances.yaml — запасной источник)."""

import json

from kitchen import api
from kitchen import brief as brief_mod

from tests.test_check_brief import c, sergey
from tests.test_checks import findings, single, with_appliances

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


def dw(**extra):
    return {"type": "dishwasher", "built_in": True, "model": "Bosch SMV", **extra}


def project_dw(home, **extra):
    appliance = {"type": "dishwasher", "width": 450, "module": "Н1", **extra}
    return with_appliances(single(home, "dishwasher_panel", 450), appliance)


# --- состав и привязка -----------------------------------------------------------


def test_no_brief_is_a_warning_and_does_not_block(home):
    api.order_new(ORDER, client="Петров П. П.")
    result = api.check_project(project_dw(home), home)
    [f] = [f for f in result.findings if f.code == "brief_missing"]
    assert f.level == "warning" and "брифа нет, техника не сверена" in f.message
    assert result.brief_id is None and result.errors == []


def test_brief_present_means_no_missing_warning_and_result_names_the_brief(home):
    save_brief(home, [dw(width_mm=450)])
    result = api.check_project(project_dw(home), home)
    assert [f for f in result.findings if f.code == "brief_missing"] == []
    assert result.brief_id == 1 and result.brief_saved_at


def test_latest_brief_is_used(home):
    save_brief(home, [])
    save_brief(home, [dw(width_mm=450)], name="b2.json")
    assert api.check_project(project_dw(home), home).brief_id == 2


def test_built_in_appliance_without_module_is_an_error(home):
    save_brief(home, [dw(width_mm=450)])
    [f] = findings(single(home, "base_door"), home, "appliance_unbound")
    assert f.level == "error" and "Bosch SMV" in f.message and f.subject.startswith("посудомойка")
    assert findings(project_dw(home), home, "appliance_unbound") == []


def test_not_built_in_appliance_needs_no_module(home):
    save_brief(home, [{"type": "dishwasher", "built_in": False}])
    assert findings(single(home, "base_door"), home, "appliance_unbound") == []


def test_count_per_type_is_compared(home):
    save_brief(home, [dw(), dw()])
    [f] = findings(project_dw(home), home, "appliance_unbound")
    assert f.level == "error"


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


# --- размеры ------------------------------------------------------------------------


def test_niche_uses_dimensions_from_brief(home):
    save_brief(home, [dw(width_mm=450, height_mm=830, depth_mm=560)])
    [f] = findings(project_dw(home), home, "niche_too_small")
    assert (f.value, f.limit) == (830, 820)
    save_brief(home, [dw(width_mm=450, height_mm=815, depth_mm=550)], name="b2.json")
    assert findings(project_dw(home), home, "niche_too_small") == []


def test_dimensions_string_of_old_brief_is_parsed(home):
    save_brief(home, [dw(width_mm=450, dimensions="450×830×560")])
    [f] = findings(project_dw(home), home, "niche_too_small")
    assert (f.value, f.limit) == (830, 820)
    assert findings(project_dw(home), home, "brief_dimensions") == []


def test_unrecognised_dimensions_string_is_a_warning(home):
    save_brief(home, [dw(width_mm=450, dimensions="большая")])
    [f] = findings(project_dw(home), home, "brief_dimensions")
    assert f.level == "warning" and "большая" in f.message


def test_dimensions_string_formats():
    parse = brief_mod.parse_dimensions
    assert parse("450x820x560") == (450, 820, 560)
    assert parse("450 х 820 × 560 мм") == (450, 820, 560)
    assert parse("") is None and parse("450") is None


def test_dimensions_missing_in_brief_come_from_mock_catalog_with_warning(home):
    save_brief(home, [dw(width_mm=450)])
    [f] = findings(project_dw(home), home, "appliance_catalog_dims")
    assert f.level == "warning"
    assert "из мок-каталога — уточните модель у клиента" in f.message and "Bosch SMV" in f.message
    save_brief(home, [dw(width_mm=460, height_mm=815, depth_mm=550)], name="b2.json")
    assert findings(project_dw(home), home, "appliance_catalog_dims") == []


def test_project_dimensions_that_differ_from_brief_are_a_warning(home):
    save_brief(home, [dw(width_mm=450, height_mm=815, depth_mm=550)])
    [f] = findings(project_dw(home, height=800), home, "appliance_dims_differ")
    assert f.level == "warning" and "800" in f.message and "815" in f.message
    assert findings(project_dw(home, height=815), home, "appliance_dims_differ") == []


# --- «в столешницу» из construction.yaml ------------------------------------------------


def test_countertop_types_come_from_construction_yaml(home):
    hob = {"type": "hob", "width": 770, "height": 50, "depth": 520, "module": "Н1"}
    path = with_appliances(single(home, "base_door", 800), hob)
    assert findings(path, home, "niche_too_small") == []
    cfg = home / "workshop/construction.yaml"
    cfg.write_text(
        cfg.read_text().replace("техника_в_столешницу: [hob, sink]", "техника_в_столешницу: [sink]")
    )
    [f] = findings(path, home, "niche_too_small")
    assert (f.value, f.limit) == (770, 768)


# --- импорт --------------------------------------------------------------------------------


def test_import_reports_brief_used_and_logs_it(home):
    api.order_new(ORDER, client="Петров П. П.")
    save_brief(home, [dw(width_mm=450, height_mm=815, depth_mm=550)])
    result = api.import_project("petrov", project_dw(home))
    assert result.brief_id == 1
    assert "брифом №1" in api.status("petrov").text


def test_import_with_unbound_built_in_appliance_is_rejected(home):
    import pytest
    from kitchen.errors import OrderError

    save_brief(home, [dw()])
    with pytest.raises(OrderError, match="Bosch SMV"):
        api.import_project("petrov", single(home, "base_door"))


def test_brief_schema_has_structured_dimensions(repo):
    schema = json.loads((repo / "skills/kitchen-brief/references/brief_schema.json").read_text())
    item = schema["sections"]["appliances"]["fields"]["items"]["item"]
    assert "height_mm" in item and "depth_mm" in item


# --- бриф Петрова (понадобится в тикете 11) -------------------------------------------------


def test_petrov_brief_fixture_has_all_appliances_from_the_spec(repo, home):
    path = repo / "tests/fixtures/petrov_brief.json"
    api.brief_save(ORDER, path, home)
    shown = api.brief_show(ORDER, home)
    types = sorted(a.type for a in brief_mod.appliances(shown.brief))
    assert types == ["dishwasher", "hob", "hood", "microwave", "oven", "sink"]
    assert all(a.built_in for a in brief_mod.appliances(shown.brief))
