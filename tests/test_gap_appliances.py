"""Тикет 32: пустое место в ряду (срез б) — техника, холодильник, строки вывода, diff."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

from kitchen import api
from kitchen.cli import app
from typer.testing import CliRunner

from tests.test_check_brief import c, sergey
from tests.test_gap import ORDER, base, codes, gap, lower, upper, wall_3700, wall_door, write

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BRIEF_FRIDGE_600 = {"existing": True, "width_mm": 600, "location": "у входа"}


def save_brief(home, fridge=None):
    brief = sergey()
    del brief["appliances"]["hood"]  # встраиваемая вытяжка без модуля проекта — отдельная находка
    if fridge is not None:
        brief["appliances"]["fridge"] = c(fridge)
    path = home / "brief.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    api.brief_save(ORDER, path, home)


def fridge_project(home, gap_width=600, appliance=None, name="p.json", **gap_extra):
    appliance = appliance or {"type": "fridge", "width": 600, "module": "ПМ1"}
    row = lower(gap("ПМ1", gap_width, **gap_extra), base("Н1"))
    walls = [{"label": "A", "length": gap_width + 600, "rows": [row]}]
    return write(home, walls, appliances=[appliance], name=name)


def gap_findings(home, path):
    return [f for f in api.check_project(path, home).findings if f.code.startswith("gap_")]


def test_fridge_not_wider_than_gap_without_clearance_is_gap_too_small(home):
    save_brief(home, BRIEF_FRIDGE_600)
    [f] = gap_findings(home, fridge_project(home, 600))
    assert (f.code, f.level, f.subject, f.value, f.limit) == ("gap_too_small", "error", "ПМ1", 620, 600)
    assert f.message == "Место ПМ1: 600 мм, а холодильнику по брифу нужно 620 мм (600 + зазор 20)"


def test_fridge_fits_in_620_gap_and_needs_no_module(home):
    save_brief(home, BRIEF_FRIDGE_600)
    result = api.check_project(fridge_project(home, 620), home)
    assert [f for f in result.findings if f.code.startswith("gap_") or f.code == "appliance_module"] == []
    assert result.errors == []


def test_gap_width_from_project_when_brief_has_none(home):
    save_brief(home, {"existing": True})
    [f] = gap_findings(home, fridge_project(home, 600))
    assert f.message == "Место ПМ1: 600 мм, а холодильнику по проекту нужно 620 мм (600 + зазор 20)"


def test_other_kind_in_gap_is_gap_appliance(home):
    appliance = {"type": "oven", "width": 600, "module": "ПМ1"}
    [f] = gap_findings(home, fridge_project(home, 700, appliance=appliance))
    assert (f.code, f.level) == ("gap_appliance", "error")
    assert f.message == (
        "В пустом месте ПМ1 может стоять только отдельностоящая техника "
        "(холодильник, посудомойка, плита, стиральная машина), а привязан «духовой шкаф»"
    )


def test_appliance_in_upper_row_gap_is_an_error(home):
    walls = [{"label": "A", "length": 1200, "rows": [upper(gap("ПМ2"), wall_door("В1"))]}]
    appliance = {"type": "fridge", "width": 500, "module": "ПМ2"}
    found = api.check_project(write(home, walls, appliances=[appliance]), home).findings
    [f] = [f for f in found if f.subject == "ПМ2" and f.level == "error"]
    assert f.message == "Место ПМ2 в верхнем ряду: технику ставят только в пустое место нижнего ряда"


def test_built_in_fridge_in_brief_and_gap_in_project_is_a_warning(home):
    save_brief(home, {"built_in": True, "model": "Liebherr ICNe", "width_mm": 560})
    path = fridge_project(home, 620, appliance={"type": "fridge", "width": 560, "module": "ПМ1"})
    [f] = gap_findings(home, path)
    assert (f.code, f.level, f.subject) == ("gap_built_in", "warning", "ПМ1")
    assert f.message == "Холодильник в брифе встраиваемый, а в проекте стоит в пустом месте ПМ1"
    assert api.check_project(path, home).errors == []


def test_clearance_and_kinds_come_from_the_workshop(home):
    save_brief(home, BRIEF_FRIDGE_600)
    cfg = home / "workshop/construction.yaml"
    text = cfg.read_text(encoding="utf-8")
    assert "зазор: 20" in text and "техника: [fridge, dishwasher, cooker, washing_machine]" in text
    path = fridge_project(home, 620)
    assert gap_findings(home, path) == []
    cfg.write_text(text.replace("пустое_место:\n  зазор: 20", "пустое_место:\n  зазор: 30"), encoding="utf-8")
    [f] = gap_findings(home, path)
    assert f.message.endswith("нужно 630 мм (600 + зазор 30)")
    cfg.write_text(text.replace("[fridge, dishwasher,", "[dishwasher,"), encoding="utf-8")
    assert [f.code for f in gap_findings(home, path)] == ["gap_appliance"]


def test_workshop_gap_kinds_are_validated_and_documented(home, repo):
    cfg = home / "workshop/construction.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("[fridge,", "[fridgee,"), encoding="utf-8")
    result = api.check_workshop(home)
    assert not result.valid and any("fridgee" in e for e in result.errors)
    mocks = (repo / "MOCKS.md").read_text(encoding="utf-8")
    assert "пустое_место.зазор" in mocks and "пустое_место.техника" in mocks


def test_parts_line_and_quote_line_for_gap_with_appliance(home):
    save_brief(home, BRIEF_FRIDGE_600)
    path = fridge_project(home, 620)
    parts = api.parts_from_project(path, home)
    assert [(g.label, g.width, g.appliance) for g in parts.gaps] == [("ПМ1", 620, "холодильник")]
    out = CliRunner().invoke(app, ["parts", "--project", str(path)]).output
    assert "ПМ1 — пустое место 620 мм (холодильник), деталей нет" in out
    quote = api.quote_from_project(path, 14, home)
    assert next(m for m in quote.modules if m.label == "ПМ1").text == "место под холодильник, 620 мм"


def test_parts_line_without_appliance(home):
    out = CliRunner().invoke(app, ["parts", "--project", str(write(home, wall_3700()))]).output
    assert "ПМ2 — пустое место 600 мм\n" in out


def test_install_in_estimate_does_not_count_gap(home):
    path = fridge_project(home, 620)
    lines = api.estimate_from_project(path, home).labor.lines
    install = next(line for line in lines if line.operation == "install")
    assert install.quantity == 1 and install.basis.endswith("все модули проекта")


def import_checked_off(home, path):
    with patch.object(api, "run_checks", lambda *args: []):
        return api.import_project("petrov", path, home=home)


def test_diff_lines(home):
    api.order_new(ORDER)
    petrov = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    p = home / "petrov.json"

    def save(data):
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        import_checked_off(home, p)

    def lower_b(data):
        wall = next(w for w in data["walls"] if w["label"] == "Б")
        return next(r for r in wall["rows"] if r["kind"] == "lower")["modules"]

    save(petrov)
    new = copy.deepcopy(petrov)
    modules = lower_b(new)
    n6 = next(m for m in modules if m["label"] == "Н6")
    old_width = int(n6["width"])
    n6["width"] = old_width - 100
    modules.append(gap("ПМ2"))
    save(new)
    assert api.diff_project("petrov", 1, 2, home).changes == [
        f"Н6: ширина {old_width} → {old_width - 100}",
        f"ПМ2: добавлено пустое место 600 мм (Б, нижний, {len(modules)})",
    ]
    newer = copy.deepcopy(new)
    next(m for m in lower_b(newer) if m["label"] == "ПМ2")["width"] = 650
    newer["appliances"].append({"type": "fridge", "width": 600, "module": "ПМ2"})
    save(newer)
    assert api.diff_project("petrov", 2, 3, home).changes == [
        "ПМ2: ширина 600 → 650",
        "ПМ2: техника не задано → холодильник",
    ]
    gone = copy.deepcopy(newer)
    lower_b(gone).pop()
    gone["appliances"].pop()
    save(gone)
    assert api.diff_project("petrov", 3, 4, home).changes == ["ПМ2: удалено пустое место"]
    assert api.diff_project("petrov", 4, 4, home).changes == []
    out = CliRunner().invoke(app, ["diff", "petrov", "v4", "v4"]).output
    assert "Изменений модулей и пустых мест нет." in out


def test_diff_module_to_gap_and_label_clash(home):
    api.order_new(ORDER)
    first = write(
        home, [{"label": "A", "length": 1200, "rows": [lower(base("Н1"), base("Н6"))]}], name="a.json"
    )
    import_checked_off(home, first)
    second = write(
        home, [{"label": "A", "length": 1200, "rows": [lower(base("Н1"), gap("Н6"))]}], name="b.json"
    )
    import_checked_off(home, second)
    assert api.diff_project("petrov", 1, 2, home).changes == ["Н6: модуль → пустое место"]
    clash = write(
        home, [{"label": "A", "length": 1200, "rows": [lower(base("ПМ1"), gap("ПМ1"))]}], name="c.json"
    )
    assert "duplicate_label" in codes(home, clash)


def test_kitchen_check_sees_gap_findings_like_parts(home):
    save_brief(home, BRIEF_FRIDGE_600)
    path = fridge_project(home, 600)
    out = CliRunner().invoke(app, ["check", "--project", str(path), "--json"])
    assert "gap_too_small" in {f["code"] for f in json.loads(out.output)["findings"]}
    assert "gap_too_small" in {f.code for f in api.parts_from_project(path, home).findings}


def two_appliances_project(home, gap_width):
    row = lower(gap("ПМ1", gap_width), base("Н1"))
    walls = [{"label": "A", "length": gap_width + 600, "rows": [row]}]
    apps = [
        {"type": "dishwasher", "width": 450, "module": "ПМ1"},
        {"type": "washing_machine", "width": 560, "module": "ПМ1"},
    ]
    return write(home, walls, appliances=apps)


def test_gap_with_two_appliances_must_fit_their_sum_plus_clearance(home):
    [f] = gap_findings(home, two_appliances_project(home, 600))
    assert (f.code, f.level, f.subject, f.value, f.limit) == ("gap_too_small", "error", "ПМ1", 1030, 600)
    assert "посудомойке" in f.message and "стиральной машине" in f.message
    assert "450 + 560 + зазор 20" in f.message
    assert gap_findings(home, two_appliances_project(home, 1050)) == []


def test_quote_names_gap_appliances_in_accusative(home):
    walls = [{"label": "A", "length": 1200, "rows": [lower(gap("ПМ1", 600), base("Н1"))]}]
    apps = [{"type": "dishwasher", "width": 450, "module": "ПМ1"}]
    quote = api.quote_from_project(write(home, walls, appliances=apps), 14, home)
    assert quote.modules[0].text == "место под посудомойку, 600 мм"


def test_built_in_dishwasher_in_gap_is_not_gap_built_in(home):
    brief = sergey()
    del brief["appliances"]["hood"]
    brief["appliances"]["items"] = c([{"type": "dishwasher", "width_mm": 450, "built_in": True}])
    path = home / "brief.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    api.brief_save(ORDER, path, home)
    project = fridge_project(home, 620, appliance={"type": "dishwasher", "width": 450, "module": "ПМ1"})
    assert "gap_built_in" not in codes(home, project)


def test_empty_brief_fridge_is_not_a_brief_appliance(home):
    save_brief(home, {"location": ""})
    assert "appliance_not_in_brief" in codes(home, fridge_project(home, 620))


def test_gap_width_source_named_for_brief_and_project_mix(home):
    save_brief(home)  # у Сергея посудомойка 450 в брифе, стиральной машины нет
    [f] = gap_findings(home, two_appliances_project(home, 600))
    assert f.message == (
        "Место ПМ1: 600 мм, а посудомойке и стиральной машине по брифу и проекту нужно 1030 мм (450 + 560 + зазор 20)"
    )


def test_wrong_kind_in_gap_is_not_counted_in_width_sum(home):
    path = two_appliances_project(home, 600)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["appliances"] = [
        {"type": "oven", "width": 600, "module": "ПМ1"},
        {"type": "fridge", "width": 600, "module": "ПМ1"},
    ]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    found = {f.code: f for f in gap_findings(home, path)}
    assert set(found) == {"gap_appliance", "gap_too_small"}
    assert (
        found["gap_too_small"].message
        == "Место ПМ1: 600 мм, а холодильнику по проекту нужно 620 мм (600 + зазор 20)"
    )
