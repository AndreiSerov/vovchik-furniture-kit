"""Тикет 58: ответы мастера по уборке этапа 6 — проверки и числа."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import OrderError
from kitchen.result import MOCK_BANNER
from pypdf import PdfReader
from typer.testing import CliRunner

from tests.test_check_brief import c, cb, run, sergey

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def set_mock(home, name, value):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8").replace("mock: true", "mock: false")
    path.write_text(text.replace("mock: false", f"mock: {str(value).lower()}"), encoding="utf-8")


def unmock_all_but(home, keep):
    for path in (home / "workshop").glob("*.yaml"):
        set_mock(home, path.name, path.name == keep)


# --- (2) проверка файла проекта говорит то же, что загрузка ------------------


def test_check_project_says_the_same_as_import(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    data = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    modules = {m["label"]: m for w in data["walls"] for r in w["rows"] for m in r["modules"]}
    modules["Н3"]["width"] = 100
    modules["Н2"]["width"] = 450  # ряд сходится: 450 + 100 = 400 + 150
    path = home / "draft.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    checked = runner.invoke(app, ["check", "--project", str(path), "--json"])
    assert checked.exit_code == 1, checked.output
    errors = [f["message"] for f in json.loads(checked.output)["findings"] if f["level"] == "error"]
    assert len(errors) == 1
    with pytest.raises(OrderError) as refused:
        api.import_project(ORDER, path, home=home)
    assert errors[0] in refused.value.problems


# --- (4) кромка «к закупке» с одним знаком -----------------------------------


def test_edges_cli_text_has_one_decimal_for_purchase(home):
    petrov(home)
    out = runner.invoke(app, ["edges", ORDER]).output
    for total in api.parts_from_order(ORDER, home).edges.values():
        assert f"к закупке {total.purchase_m:.1f} м" in out
    assert "50.9 м" in out and "50.900" not in out


def test_edging_pdf_purchase_column_has_one_decimal(home):
    petrov(home)
    result = api.edges_and_save(ORDER, home)
    text = "\n".join(page.extract_text() for page in PdfReader(result.output.file).pages)
    assert "50.9" in text and "50.900" not in text
    for total in result.edges.values():
        assert f"{total.clean_m:.3f}" in text and f"{total.with_allowance_m:.3f}" in text


def test_edges_json_keys_are_unchanged(home):
    petrov(home)
    data = json.loads(runner.invoke(app, ["edges", ORDER, "--json"]).output)
    assert set(data) == {"mock", "mock_files", "warning", "source", "parts", "edges", "warnings", "output"}
    assert data["edges"] == json.loads(runner.invoke(app, ["parts", ORDER, "--json"]).output)["edges"]


@pytest.mark.parametrize(
    ("step", "digits"),
    [("0.1", 1), ("0.5", 1), ("1", 1), ("0.05", 2), ("0.025", 3)],
)
def test_purchase_digits_follow_the_rounding_step(home, step, digits):
    path = home / "workshop" / "construction.yaml"
    text = path.read_text(encoding="utf-8").replace("вверх до 0.1 м", f"вверх до {step} м")
    path.write_text(text, encoding="utf-8")
    petrov(home)
    out = runner.invoke(app, ["edges", ORDER]).output
    result = api.edges_and_save(ORDER, home)
    pdf = "\n".join(page.extract_text() for page in PdfReader(result.output.file).pages)
    for total in result.edges.values():
        assert f"к закупке {total.purchase_m:.{digits}f} м" in out
        assert f"{total.purchase_m:.{digits}f}" in pdf


def test_production_edge_line_is_unchanged(home):
    petrov(home)
    out = runner.invoke(app, ["production", ORDER]).output
    for total in api.parts_from_order(ORDER, home).edges.values():
        assert f"к закупке {total.purchase_m:.1f} м" in out


# --- (9) вилка строками ------------------------------------------------------


def test_quick_estimate_json_has_ready_strings(home):
    r = runner.invoke(app, ["quick-estimate", "--meters", "3.6", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["total_low_text"] == "306 000" and data["total_high_text"] == "396 000"
    assert data["total_low"] == 306000 and data["total_high"] == 396000
    assert set(data) == {
        "mock", "mock_files", "warning", "meters", "per_meter_low", "per_meter_high",
        "total_low", "total_high", "rounding", "total_low_text", "total_high_text",
    }  # fmt: skip
    text = runner.invoke(app, ["quick-estimate", "--meters", "3.6"]).output
    assert "Вилка цены: 306 000 – 396 000 ₽ (округление " in text


def test_quote_and_range_share_the_digit_grouping(home):
    assert (
        api.money(222000) == "222 000 ₽" and api.money(1000000) == "1 000 000 ₽" and api.money(999) == "999 ₽"
    )
    data = api.quick_estimate(10, home)
    assert api.money(data.total_low) == f"{data.total_low_text} ₽"
    assert api.money(data.total_high) == f"{data.total_high_text} ₽"


# --- (10) мок вилки — только pricing.yaml ------------------------------------


def test_range_is_not_mock_when_only_materials_are_mock(home):
    unmock_all_but(home, "materials.yaml")
    data = json.loads(runner.invoke(app, ["quick-estimate", "--meters", "3.6", "--json"]).output)
    assert data["mock"] is False and data["mock_files"] == [] and data["warning"] is None
    assert MOCK_BANNER not in runner.invoke(app, ["quick-estimate", "--meters", "3.6"]).output


def test_range_is_mock_when_pricing_is_mock(home):
    unmock_all_but(home, "pricing.yaml")
    data = json.loads(runner.invoke(app, ["quick-estimate", "--meters", "3.6", "--json"]).output)
    assert data["mock"] is True and data["mock_files"] == ["pricing.yaml"]
    assert data["warning"] == MOCK_BANNER
    assert MOCK_BANNER in runner.invoke(app, ["quick-estimate", "--meters", "3.6"]).output


def test_broken_other_file_still_fails_the_range(home):
    unmock_all_but(home, "pricing.yaml")
    (home / "workshop" / "labor.yaml").write_text("не: [yaml", encoding="utf-8")
    assert runner.invoke(app, ["quick-estimate", "--meters", "3.6"]).exit_code != 0


# --- (17) дробное число без дробной части в брифе ----------------------------


def brief_file(tmp_path, brief):
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("storage", "upper_height", 720.0),
        ("storage", "upper_height", 900.0),
        ("workzone", "sink_bowls", 2.0),
        ("workzone", "sink_bowls", 1.50),
        ("workzone", "sink_bowls", 1.0),
    ],
)
def test_integral_float_passes_check_and_save(home, tmp_path, section, field, value):
    brief = sergey()
    brief.setdefault(section, {})[field] = c(value)
    assert run(brief)["errors"] == []
    api.brief_save(ORDER, brief_file(tmp_path, brief), home)  # не бросает


@pytest.mark.parametrize("bad", ["720.0", "abc", "750"])
def test_bad_enum_value_fails_in_both_paths_alike(home, tmp_path, bad):
    brief = sergey()
    brief["storage"]["upper_height"] = c(bad)
    errors = run(brief)["errors"]
    assert any("storage.upper_height" in e and "не из перечня" in e for e in errors)
    with pytest.raises(OrderError) as refused:
        api.brief_save(ORDER, brief_file(tmp_path, brief), home)
    assert [e for e in refused.value.problems if e.startswith("Ошибка в брифе")] == [
        f"Ошибка в брифе: {e}" for e in errors
    ]


def test_integral_float_in_object_enum_key(home):
    brief = sergey()
    brief["appliances"]["hood"] = c({"kind": "built_in", "exhaust": "duct"})
    assert run(brief)["errors"] == []
    brief["appliances"]["hood"] = c({"kind": 2.0})
    assert any("appliances.hood.kind" in e for e in run(brief)["errors"])


REPO = Path(__file__).resolve().parents[1]


def test_production_bundle_edging_map_follows_the_rounding_step_of_home(home, monkeypatch):
    path = home / "workshop" / "construction.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("вверх до 0.1 м", "вверх до 0.05 м"), encoding="utf-8"
    )
    petrov(home)
    monkeypatch.delenv("KITCHEN_HOME")
    monkeypatch.chdir(REPO)  # у репозитория свой workshop/ с шагом 0.1
    result = api.production_and_save(ORDER, home)
    pdf = home / "orders" / ORDER / "out" / "edging-map.pdf"
    text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert result.edging.purchase_digits == 2
    for total in result.edging.edges.values():
        assert f"{total.purchase_m:.2f}" in text


def test_edges_cli_keeps_three_decimals_for_clean_and_allowance(home):
    petrov(home)
    out = runner.invoke(app, ["edges", ORDER]).output
    for edge_id, t in api.parts_from_order(ORDER, home).edges.items():
        assert (
            f"Кромка {edge_id}: чистая {t.clean_m:.3f} м, с припуском {t.with_allowance_m:.3f} м, "
            f"к закупке {t.purchase_m:.1f} м"
        ) in out


def test_boolean_is_not_a_number_in_enum():
    brief = sergey()
    brief.setdefault("workzone", {})["sink_bowls"] = c(True)
    assert any("workzone.sink_bowls" in e and "не из перечня" in e for e in run(brief)["errors"])


@pytest.mark.parametrize(
    ("section", "field", "value"), [("storage", "upper_height", 720.5), ("workzone", "sink_bowls", 2.5)]
)
def test_fractional_float_is_not_rounded_into_the_enum(section, field, value):
    brief = sergey()
    brief.setdefault(section, {})[field] = c(value)
    assert any(f"{section}.{field}" in e and "не из перечня" in e for e in run(brief)["errors"])


def test_integral_float_in_object_enum_key_of_any_schema():
    schema = {"sections": {"s": {"fields": {"o": {"type": "object", "item": {"n": "enum: 1, 2 — число"}}}}}}
    assert cb.enum_errors({"s": {"o": c({"n": 2.0})}}, schema) == []
    assert cb.enum_errors({"s": {"o": c({"n": 3.0})}}, schema) == ["s.o.n: 3.0 не из перечня 1, 2"]


def test_parts_and_production_edge_line_follow_the_rounding_step(home):
    path = home / "workshop" / "construction.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("вверх до 0.1 м", "вверх до 0.05 м"), encoding="utf-8"
    )
    petrov(home)
    for command in ("parts", "production"):
        out = runner.invoke(app, [command, ORDER]).output
        for total in api.parts_from_order(ORDER, home).edges.values():
            assert f"к закупке {total.purchase_m:.2f} м" in out


def test_rub_grouping_in_quick_estimate_text(home):
    out = runner.invoke(app, ["quick-estimate", "--meters", "3.6"]).output
    assert "  За пог. м: 85 000 – 110 000 ₽" in out


def test_production_edge_line_follows_the_rounding_step_with_trailing_zero(home):
    path = home / "workshop" / "construction.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("вверх до 0.1 м", "вверх до 0.25 м"), encoding="utf-8"
    )
    petrov(home)
    assert "к закупке 51.00 м" in runner.invoke(app, ["production", ORDER]).output


def test_quick_estimate_text_rounds_a_fractional_per_meter_price(home):
    path = home / "workshop" / "pricing.yaml"
    text = path.read_text(encoding="utf-8").replace("[85000, 110000]", "[85000.6, 110000]")
    path.write_text(text, encoding="utf-8")
    out = runner.invoke(app, ["quick-estimate", "--meters", "3.6"]).output
    assert "  За пог. м: 85 001 – 110 000 ₽" in out
