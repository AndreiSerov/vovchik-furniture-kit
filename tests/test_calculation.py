"""Тикет 70: расчёт (`api.calculation`) и его методы-выводы по таблице «Выводы расчёта» спеки этапа 9."""

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import InputError, OrderError, WorkshopError
from typer.testing import CliRunner

from tests.test_input_guards_project import lower, write
from tests.test_placement import petrov

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def petrov_order(home):
    """«Заказ Петрова»: бриф и проект в tmp home."""
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def petrov_file(home):
    """«Файл Петрова»: копия проекта в tmp home."""
    path = home / "petrov_project.json"
    shutil.copy(FIXTURES / "petrov_project.json", path)
    return path


# --- 70.2: ровно одно из заказа и файла ---------------------------------------------------------


def test_calculation_needs_exactly_one_of_order_and_file(home):
    for kwargs in ({}, {"order": ORDER, "project": petrov_file(home)}):
        with pytest.raises(OrderError) as e:
            api.calculation(home=home, **kwargs)
        assert e.value.problems == ["Укажите либо заказ, либо файл проекта."]


# --- 70.3: правило брифа ------------------------------------------------------------------------


def test_file_outputs_take_the_order_brief_except_trip_and_placement(home):
    petrov_order(home)
    latest = api.brief_show(ORDER, home)
    calc = api.calculation(project=petrov_file(home), home=home)
    source = calc.parts().source
    assert (source.brief_id, source.brief_saved_at) == (1, latest.saved_at)
    trip = calc.trip()
    assert (trip.source.brief_id, trip.source.brief_saved_at) == (None, None)
    assert set(trip.header.model_dump().values()) == {"—"}
    assert "брифа нет — шапка пустая" in trip.warnings
    assert calc.placement().source.brief_id is None
    assert calc.check().brief_note is None


# --- 70.4: бриф файла читается лениво -----------------------------------------------------------


def garbage_db(home):
    (home / "orders").mkdir(exist_ok=True)
    (home / "orders" / "kitchen.db").write_text("garbage", encoding="utf-8")


def test_trip_and_placement_of_a_file_do_not_open_the_db(home):
    garbage_db(home)
    calc = api.calculation(project=petrov_file(home), home=home)
    calc.trip()
    calc.placement()
    with pytest.raises(OrderError) as e:
        calc.parts()
    assert e.value.problems[0].endswith("файл не является базой данных kitchen.")


# --- 70.5: порядок ошибок -----------------------------------------------------------------------


def break_workshop(home):
    (home / "workshop" / "construction.yaml").write_text("корпус: [\n", encoding="utf-8")


@pytest.fixture
def repeated(home):
    modules = [
        {"label": "Н1", "template": "base_door", "width": 600},
        {"label": "H1", "template": "base_door", "width": 600},
    ]
    return write(home, lower(*modules), 1200)


def test_broken_json_fails_at_creation_and_check_exits_with_2(home, monkeypatch):
    break_workshop(home)
    bad = home / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.calculation(project=bad, home=home)
    assert e.value.problems == ["bad.json: не JSON (строка 1, столбец 2)."]
    monkeypatch.setenv("KITCHEN_HOME", str(home))
    assert runner.invoke(app, ["check", "--project", str(bad)]).exit_code == 2


def test_quote_checks_the_term_before_the_workshop(home):
    break_workshop(home)
    with pytest.raises(InputError) as e:
        api.calculation(project=petrov_file(home), home=home).quote(0)
    assert e.value.problems == [api.DAYS_ERROR]


def test_quote_checks_the_term_before_the_brief(home):
    break_workshop(home)
    garbage_db(home)
    with pytest.raises(InputError) as e:
        api.calculation(project=petrov_file(home), home=home).quote(0)
    assert e.value.problems == [api.DAYS_ERROR]


@pytest.mark.parametrize("method", ["parts", "cut", "views", "placement"])
def test_workshop_loads_before_the_repeated_label_check(home, repeated, method):
    break_workshop(home)
    calc = api.calculation(project=repeated, home=home)
    with pytest.raises(WorkshopError) as e:
        getattr(calc, method)()
    assert any("construction.yaml" in p for p in e.value.problems), e.value.problems


# --- 70.6: мастерская и детали — по одному разу ------------------------------------------------


def counter(monkeypatch, target):
    """Подменяет имя модуля строкой (`kitchen.calculation.X`) обёрткой со счётчиком вызовов."""
    module, name = target.rsplit(".", 1)
    original = getattr(sys.modules[module], name)
    calls = []
    monkeypatch.setattr(target, lambda *a, **k: calls.append(1) or original(*a, **k))
    return calls


def test_workshop_and_parts_are_computed_once_per_calculation(home, monkeypatch):
    petrov_order(home)
    loads = counter(monkeypatch, "kitchen.calculation.load_workshop")
    computes = counter(monkeypatch, "kitchen.calculation.compute_parts")
    calc = api.calculation(order=ORDER, home=home)
    for method in (
        calc.parts,
        calc.cut,
        calc.edging,
        calc.drilling,
        calc.assembly,
        calc.trip,
        calc.purchase,
        calc.estimate,
        calc.views,
        calc.check,
        calc.placement,
    ):
        method()
    assert (len(loads), len(computes)) == (1, 1)
    calc.quote(20)
    assert (len(loads), len(computes)) == (1, 1)


# --- 70.7: кэш по аргументам --------------------------------------------------------------------


def test_outputs_are_cached_per_argument_set(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    assert calc.estimate() is calc.estimate()
    assert calc.quote(20) is calc.quote(20)
    assert calc.quote(20) is not calc.quote(21)
    assert calc.quote(21).days == 21


# --- 70.8, 70.9: виды без `parts()` -------------------------------------------------------------


def test_views_draw_a_module_that_does_not_build(home):
    path = home / "p.json"
    path.write_text(
        json.dumps(petrov(Н3={"width": 100}, Н4={"width": 1100}), ensure_ascii=False), encoding="utf-8"
    )
    calc = api.calculation(project=path, home=home)
    assert [(f.code, f.subject) for f in calc.views().errors] == [("module_build", "Н3")]
    with pytest.raises(OrderError) as e:
        calc.parts()
    assert e.value.problems == [
        "Модуль Н3: отверстие «конфирмат» Ø5 на детали Н3-дно (x=16, y=510, z=8) пересекается с отверстием «конфирмат»."
    ]


def test_views_do_not_need_the_common_items(home):
    data = petrov()
    data["materials"]["countertop"] = "nope"
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    calc = api.calculation(project=path, home=home)
    assert calc.views().errors == []
    with pytest.raises(OrderError) as e:
        calc.parts()
    assert e.value.problems[0].startswith("Столешница «nope»")


# --- 70.10: новые сочетания ---------------------------------------------------------------------


def test_purchase_of_a_file_matches_the_order(home):
    petrov_order(home)
    of_order = api.calculation(order=ORDER, home=home).purchase()
    of_file = api.calculation(project=petrov_file(home), home=home).purchase()
    assert (of_file.shops, of_file.total) == (of_order.shops, of_order.total)


def test_economy_parts_of_an_order_match_the_file(home):
    petrov_order(home)
    of_order = api.calculation(order=ORDER, home=home).economy_parts()
    of_file = api.calculation(project=petrov_file(home), home=home).economy_parts()
    assert (of_order.modules, of_order.hardware, of_order.warnings) == (
        of_file.modules,
        of_file.hardware,
        of_file.warnings,
    )


# --- 70.11: повтор меток ------------------------------------------------------------------------


def test_repeated_labels_refuse_outputs_but_check_finds_them(home, repeated):
    calc = api.calculation(project=repeated, home=home)
    found = [f for f in calc.check().findings if f.code == "duplicate_label"]
    assert len(found) == 1
    for method in (calc.placement, calc.parts, calc.views):
        with pytest.raises(OrderError) as e:
            method()
        assert e.value.problems == [found[0].message]


# --- 70.12: расчёт из файла ничего не пишет -----------------------------------------------------


def every_output(calc):
    for method in (
        calc.parts,
        calc.cut,
        calc.edging,
        calc.drilling,
        calc.assembly,
        calc.trip,
        calc.purchase,
        calc.estimate,
        lambda: calc.estimate(economy=True),
        lambda: calc.quote(20),
        lambda: calc.quote(20, economy=True),
        calc.views,
        calc.placement,
        calc.check,
        calc.economy_parts,
    ):
        method()


def log_count(home):
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        return conn.execute("SELECT COUNT(*) FROM log").fetchone()[0]


def test_file_calculation_without_an_order_creates_no_orders_folder(home):
    every_output(api.calculation(project=petrov_file(home), home=home))
    assert not (home / "orders").exists()


def test_file_calculation_leaves_the_order_outputs_and_log_alone(home):
    petrov_order(home)
    outputs, entries = api.list_outputs(ORDER, home), log_count(home)
    every_output(api.calculation(project=petrov_file(home), home=home))
    assert (api.list_outputs(ORDER, home), log_count(home)) == (outputs, entries)


# --- 70.13: глоссарий ---------------------------------------------------------------------------


def test_glossary_names_the_calculation_and_the_new_estimate_definition():
    rows = (REPO / "GLOSSARY.md").read_text(encoding="utf-8").splitlines()
    assert any(row.startswith("| Расчёт | calculation |") for row in rows)
    assert (
        "| Смета | estimate | Внутренние цифры мастера: себестоимость, труд, наценка, цена | калькуляция |"
        in rows
    )


# --- ревью 70: пробелы, которые переживали все тесты, кроме снимка ------------------------------------


def test_estimate_with_and_without_economy_are_separate_entries(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    plain = calc.estimate()
    with_economy = calc.estimate(economy=True)
    assert plain.economy is None
    assert with_economy.economy is not None
    assert calc.estimate() is plain


def test_quote_with_economy_is_its_own_entry(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    assert calc.quote(20).economy is None
    assert calc.quote(20, economy=True).economy is not None


def test_quote_with_economy_takes_the_cached_estimate(home, monkeypatch):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    calc.estimate(economy=True)
    computes = counter(monkeypatch, "kitchen.calculation.compute_parts")
    calc.quote(20, economy=True)
    assert computes == []


def test_assembly_of_a_file_loads_the_workshop_before_the_brief(home):
    """Как было в `assembly_from_project`: мастерская раньше брифа (решение мастера 2026-11-19, «ничего не меняется»)."""
    break_workshop(home)
    garbage_db(home)
    with pytest.raises(WorkshopError):
        api.calculation(project=petrov_file(home), home=home).assembly()


def test_economy_parts_of_a_file_reads_the_brief_before_the_workshop(home):
    """Допустимое изменение порядка по спеке (п. 8 «Когда что читается»)."""
    break_workshop(home)
    garbage_db(home)
    with pytest.raises(OrderError) as e:
        api.calculation(project=petrov_file(home), home=home).economy_parts()
    assert e.value.problems[0].endswith("файл не является базой данных kitchen.")


def test_views_and_economy_parts_of_a_file_take_the_order_brief(home):
    petrov_order(home)
    calc = api.calculation(project=petrov_file(home), home=home)
    assert calc.views().source.brief_id == 1
    assert calc.economy_parts().source.brief_id == 1


def test_cut_and_views_carry_the_newer_brief_warning(home):
    petrov_order(home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)  # бриф №2 новее записанного у версии
    calc = api.calculation(order=ORDER, home=home)
    note = calc.check().brief_note
    assert note.startswith("Бриф №2 новее")
    assert calc.cut().warnings[-1] == note
    assert calc.views().warnings[-1] == note
