"""Тикет 36: `kitchen check <заказ>|--project <файл>` — находки проверок проекта без расчёта выводов."""

import json
import os
import sqlite3
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def petrov_data():
    return json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))


def write(home, data, name="p.json"):
    path = home / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def unmock(home):
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")


def log_count(home):
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        return conn.execute("SELECT COUNT(*) FROM log").fetchone()[0]


def first_module(data, label="Н1"):
    return next(m for w in data["walls"] for r in w["rows"] for m in r["modules"] if m["label"] == label)


def test_petrov_order_exit_0_banner_and_json_codes(home):
    petrov(home)
    r = runner.invoke(app, ["check", "petrov"])
    assert r.exit_code == 0, r.output
    lines = r.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert f"Проверки проекта {ORDER}:" in r.output
    assert "Источник: заказ 2026-047-petrov, версия 1 проекта" in r.output
    assert "Проверки: 0 ошибок, 3 предупреждения" in r.output
    assert "  [предупреждение] " in r.output
    j = runner.invoke(app, ["check", "petrov", "--json"])
    assert j.exit_code == 0, j.output
    data = json.loads(j.output)
    expected = [f.code for f in api.calculation(order="petrov", home=home).parts().findings]
    assert [f["code"] for f in data["findings"]] == expected and expected
    assert data["source"]["kind"] == "order" and data["source"]["version"] == 1
    assert data["warning"] == MOCK_BANNER
    assert data == json.loads(api.calculation(order="petrov", home=home).check().model_dump_json())


def test_project_row_sum_error_exit_1_errors_before_warnings(home):
    petrov(home)
    data = petrov_data()
    first_module(data)["width"] += 100
    r = runner.invoke(app, ["check", "--project", str(write(home, data))])
    assert r.exit_code == 1, r.output
    lines = r.output.splitlines()
    err = [i for i, line in enumerate(lines) if line.startswith("  [ОШИБКА]")]
    warn = [i for i, line in enumerate(lines) if line.startswith("  [предупреждение]")]
    assert err and warn and max(err) < min(warn)
    message = api.calculation(project=write(home, data), home=home).check().errors[0].message
    assert f"  [ОШИБКА] {message}" in r.output
    assert "Проверки: 1 ошибка, 3 предупреждения" in r.output
    j = runner.invoke(app, ["check", "--project", str(write(home, data)), "--json"])
    assert j.exit_code == 1


def test_project_without_order_in_db_is_brief_missing_exit_0(home):
    r = runner.invoke(app, ["check", "--project", str(FIXTURES / "petrov_project.json")])
    assert r.exit_code == 0, r.output
    j = runner.invoke(app, ["check", "--project", str(FIXTURES / "petrov_project.json"), "--json"])
    codes = [f["code"] for f in json.loads(j.output)["findings"]]
    assert "brief_missing" in codes
    assert json.loads(j.output)["source"]["kind"] == "file"


def test_order_without_project_exit_2(home):
    api.order_new(ORDER, "Петров", home=home)
    r = runner.invoke(app, ["check", "petrov"])
    assert r.exit_code == 2
    assert "У заказа 2026-047-petrov проекта ещё нет. Сначала kitchen import." in r.output


def test_unknown_order_and_broken_json_exit_2(home):
    assert runner.invoke(app, ["check", "nobody"]).exit_code == 2
    bad = home / "bad.json"
    bad.write_text("{не json", encoding="utf-8")
    r = runner.invoke(app, ["check", "--project", str(bad)])
    assert r.exit_code == 2 and "Traceback" not in r.output
    r = runner.invoke(app, ["check", "--project", str(home / "nope.json")])
    assert r.exit_code == 2


def test_broken_workshop_exit_2(home):
    petrov(home)
    (home / "workshop" / "pricing.yaml").write_text("mock: true\nпредоплата: [0.5\n", encoding="utf-8")
    r = runner.invoke(app, ["check", "petrov"])
    assert r.exit_code == 2 and "Traceback" not in r.output


def test_newer_brief_is_noted(home):
    petrov(home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    r = runner.invoke(app, ["check", "petrov"])
    assert r.exit_code == 0, r.output
    assert "Бриф №2 новее брифа, с которым проверена версия 1 (№1)" in r.output
    assert r.output.strip().splitlines()[-1] == MOCK_BANNER
    j = json.loads(runner.invoke(app, ["check", "petrov", "--json"]).output)
    assert "новее" in j["brief_note"]


def test_order_and_project_together_or_none_exit_2(home):
    assert runner.invoke(app, ["check"]).exit_code == 2
    r = runner.invoke(app, ["check", "petrov", "--project", str(FIXTURES / "petrov_project.json")])
    assert r.exit_code == 2


def test_unmocked_workshop_has_no_banner(home):
    petrov(home)
    unmock(home)
    r = runner.invoke(app, ["check", "petrov"])
    assert r.exit_code == 0, r.output
    assert MOCK_BANNER not in r.output
    assert json.loads(runner.invoke(app, ["check", "petrov", "--json"]).output)["warning"] is None


def test_unbuildable_template_and_duplicate_label_are_findings(home):
    data = petrov_data()
    first_module(data)["template"] = "foo"
    r = runner.invoke(app, ["check", "--project", str(write(home, data))])
    assert r.exit_code == 1 and "Traceback" not in r.output
    j = json.loads(runner.invoke(app, ["check", "--project", str(write(home, data)), "--json"]).output)
    assert "module_build" in [f["code"] for f in j["findings"]]
    data = petrov_data()
    mods = data["walls"][0]["rows"][0]["modules"]
    mods[1]["label"] = mods[0]["label"]
    j = json.loads(runner.invoke(app, ["check", "--project", str(write(home, data)), "--json"]).output)
    assert "duplicate_label" in [f["code"] for f in j["findings"]]


def test_schema_rejection_matches_import(home):
    petrov(home)
    data = petrov_data()
    data["лишнее"] = 1
    path = write(home, data)
    imp = runner.invoke(app, ["import", "petrov", str(path)])
    chk = runner.invoke(app, ["check", "--project", str(path)])
    assert imp.exit_code == 1 and chk.exit_code == 1
    problems = [p for p in imp.output.splitlines() if p and p != MOCK_BANNER and "отклонён" not in p]
    assert problems
    for line in problems:
        assert line in chk.output


def test_check_writes_no_log_and_no_status(home):
    petrov(home)
    api.status(ORDER, home)
    status = home / "orders" / ORDER / "status.md"
    before = log_count(home)
    os.utime(status, ns=(1, 1))
    for argv in (["check", "petrov"], ["check", "--project", str(FIXTURES / "petrov_project.json")]):
        runner.invoke(app, argv)
    assert status.stat().st_mtime_ns == 1
    assert log_count(home) == before


def test_version_field_same_verdict_as_import(home):
    petrov(home)
    for bad in (0, "2"):
        path = write(home, {**petrov_data(), "version": bad})
        assert runner.invoke(app, ["import", "petrov", str(path)]).exit_code == 0
        assert runner.invoke(app, ["check", "--project", str(path)]).exit_code == 0


def test_missing_order_same_text_as_import(home):
    petrov(home)
    data = petrov_data()
    del data["order"]
    path = write(home, data)
    imp = runner.invoke(app, ["import", "petrov", str(path)])
    chk = runner.invoke(app, ["check", "--project", str(path)])
    assert "В проекте нет поля «order» с номером заказа." in imp.output
    assert "В проекте нет поля «order» с номером заказа." in chk.output


def test_source_carries_brief(home):
    petrov(home)
    assert "; бриф №1 от " in runner.invoke(app, ["check", "petrov"]).output
    out = runner.invoke(app, ["check", "--project", str(FIXTURES / "petrov_project.json"), "--json"]).output
    j = json.loads(out)
    assert j["source"]["brief_id"] == 1 and j["source"]["brief_saved_at"] == j["brief_saved_at"]


def test_brief_note_after_findings(home):
    petrov(home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    lines = [x for x in runner.invoke(app, ["check", "petrov"]).output.splitlines() if x]
    note = next(i for i, x in enumerate(lines) if x.startswith("Бриф №2 новее"))
    assert note == len(lines) - 2 and lines[note - 1].startswith("  [")


def test_row_sum_error_has_both_numbers(home):
    petrov(home)
    data = petrov_data()
    first_module(data)["width"] += 100
    r = runner.invoke(app, ["check", "--project", str(write(home, data))])
    line = next(x for x in r.output.splitlines() if x.startswith("  [ОШИБКА]"))
    assert "2900 мм" in line and "2800 мм" in line


def test_real_project_without_findings(home):
    brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    brief["appliances"] = []
    api.brief_save(ORDER, write(home, brief, "b.json"), home)
    project = {
        "order": ORDER,
        "ceiling_height": 2700,
        "walls": [
            {
                "label": "A",
                "length": 600,
                "rows": [
                    {
                        "kind": "lower",
                        "modules": [{"label": "Н1", "template": "base_door", "width": 600, "shelves": 1}],
                    }
                ],
            }
        ],
        "materials": {
            "body": "ldsp_16_white",
            "facades_lower": "mdf_enamel_matt",
            "facades_upper": "mdf_enamel_matt",
            "handles": "handle_profile",
        },
    }
    r = runner.invoke(app, ["check", "--project", str(write(home, project))])
    assert r.exit_code == 0, r.output
    assert "Проверки: 0 ошибок, 0 предупреждений" in r.output and "Замечаний нет." in r.output


@pytest.mark.parametrize(("value", "expected"), [(2, 2), (0, None), (True, None), ("2", None)])
def test_project_file_source_version_is_int_ge_1_only(home, value, expected):
    data = {**json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8")), "version": value}
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = CliRunner().invoke(app, ["parts", "--project", str(path), "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["source"]["version"] == expected
