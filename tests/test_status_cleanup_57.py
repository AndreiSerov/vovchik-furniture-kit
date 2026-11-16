"""Тикет 57: статус без сметы, присадка вне «Следующего шага», авто-записи «сделано», бриф по телефону."""

import json
import re
import sqlite3
from datetime import UTC
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import OrderError
from typer.testing import CliRunner

from tests.test_status_next_step import FIXTURES, ORDER, _import_v2, _section, _step, _v1

runner = CliRunner()
PHONE = "+7 900 000-00-00"


def _log(home, kind="done"):
    db = sqlite3.connect(home / "orders" / "kitchen.db")
    return db.execute(
        "SELECT text, auto, project_version FROM log WHERE kind = ? ORDER BY id", (kind,)
    ).fetchall()


def _done_texts(home):
    return [r[0] for r in _log(home)]


# --- 1, 2: следующий шаг ------------------------------------------------------


def test_quote_without_estimate_asks_for_estimate(home):
    _v1(home)
    api.quote_and_save(ORDER, 20, home)
    api.production_and_save(ORDER, home)
    step = _step(home)
    assert step.text == "Смета: kitchen estimate 2026-047-petrov."
    assert step.commands == [f"kitchen estimate {ORDER}"]
    api.estimate_and_save(ORDER, home)
    assert _step(home).text.startswith("Все документы готовы")


def _drilling_then_v2(home):
    _v1(home)
    api.production_and_save(ORDER, home)
    api.quote_and_save(ORDER, 20, home)
    api.drilling_and_save(ORDER, home)
    _import_v2(home)


def test_stale_drilling_is_not_a_next_step(home):
    _drilling_then_v2(home)
    step = _step(home)
    assert step.text == (
        "Пересчитать устаревшее: kitchen quote 2026-047-petrov --days <рабочих дней>, "
        "kitchen production 2026-047-petrov."
    )
    assert "kitchen drilling" not in step.text
    assert not any("drilling" in c for c in step.commands)
    stale = _section(api.status(ORDER, home).text, "Устарело")
    assert "kitchen drilling" in stale  # в «Устарело» строка карты присадки остаётся


def test_only_drilling_stale_falls_through_to_next_rule(home):
    _drilling_then_v2(home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 20, home)
    api.production_and_save(ORDER, home)
    step = _step(home)  # присадка осталась устаревшей, но шаг — по следующим правилам
    assert step.text == "Все документы готовы: выезд на монтаж по чек-листу."
    assert "kitchen drilling" in _section(api.status(ORDER, home).text, "Устарело")


def test_stale_3d_stays_in_the_line(home):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    api.views_and_save(ORDER, home)
    _import_v2(home)
    assert f"kitchen 3d {ORDER}" in _step(home).commands


# --- 3, 4: записи «сделано» ---------------------------------------------------


def _cycle(home, days=20):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, days, home)
    api.production_and_save(ORDER, home)


def test_done_entries_of_the_petrov_cycle(home):
    _cycle(home)
    done = _section(api.status(ORDER, home, tz=UTC).text, "Сделано")
    texts = [re.sub(r"^- \S+: ", "", line) for line in done.splitlines()]
    assert texts == [
        "бриф сохранён",
        "принята версия 1",
        "смета посчитана из версии 1",
        "КП посчитано из версии 1, срок 20 рабочих дней",
        "документы цеха посчитаны из версии 1",
    ]
    rows = _log(home)
    assert [r[1] for r in rows] == [1] * 5
    assert [r[2] for r in rows][1:] == [1, 1, 1, 1]


@pytest.mark.parametrize(
    ("days", "phrase"), [(21, "21 рабочий день"), (3, "3 рабочих дня"), (5, "5 рабочих дней")]
)
def test_quote_entry_declines_working_days(home, days, phrase):
    _v1(home)
    api.quote_and_save(ORDER, days, home)
    assert _done_texts(home)[-1] == f"КП посчитано из версии 1, срок {phrase}"


def test_economy_estimate_writes_the_same_entry(home):
    _v1(home)
    api.estimate_and_save(ORDER, home, economy=True)
    assert _done_texts(home)[-1] == "смета посчитана из версии 1"


def test_new_version_entry_names_the_version(home):
    _v1(home)
    _import_v2(home)
    assert _done_texts(home)[-1] == "принята версия 2"


# --- 5, 6: чего не пишем ------------------------------------------------------


def test_single_commands_and_unchanged_import_write_nothing(home):
    _v1(home)
    before = _log(home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)  # без изменений
    api.cut_and_save(ORDER, home)
    api.edges_and_save(ORDER, home)
    api.purchase_and_save(ORDER, home)
    api.assembly_and_save(ORDER, home)
    api.trip_and_save(ORDER, home)
    api.drilling_and_save(ORDER, home)
    api.views_and_save(ORDER, home)
    assert _log(home) == before


def test_refused_save_writes_no_entry(home, monkeypatch):
    _v1(home)
    original = api.quote_from_order

    def stale_after_compute(*args, **kwargs):
        result = original(*args, **kwargs)
        _import_v2(home)  # версия сменилась между расчётом и записью
        return result

    monkeypatch.setattr(api, "quote_from_order", stale_after_compute)
    with pytest.raises(OrderError, match="Изменилась версия проекта"):
        api.quote_and_save(ORDER, 20, home)
    assert not [t for t in _done_texts(home) if t.startswith("КП")]


# --- 7: «Дальше» --------------------------------------------------------------


def _next(home):
    return _section(api.status(ORDER, home).text, "Дальше")


def test_automatic_entries_reset_next_but_brief_saved_does_not(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.log_add(ORDER, "next", "позвонить клиенту", home)
    assert "позвонить клиенту" in _next(home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    assert _next(home) == "Пока ничего."
    api.log_add(ORDER, "next", "согласовать фасады", home)
    assert "согласовать фасады" in _next(home)
    api.production_and_save(ORDER, home)
    assert _next(home) == "Пока ничего."
    api.log_add(ORDER, "next", "выезд на монтаж", home)
    assert "выезд на монтаж" in _next(home)


@pytest.mark.parametrize("step", ["estimate", "quote"])
def test_estimate_and_quote_reset_next(home, step):
    _v1(home)
    api.log_add(ORDER, "next", "старое", home)
    if step == "estimate":
        api.estimate_and_save(ORDER, home)
    else:
        api.quote_and_save(ORDER, 20, home)
    assert _next(home) == "Пока ничего."


# --- 8: телефон ---------------------------------------------------------------


def _orders(home):
    return (
        sqlite3.connect(home / "orders" / "kitchen.db").execute("SELECT number, phone FROM orders").fetchall()
    )


def test_brief_save_copies_phone_to_new_order(home):
    api.brief_save("petrov", FIXTURES / "petrov_brief.json", home)
    assert [r[1] for r in _orders(home)] == [PHONE]


def test_brief_save_copies_phone_to_existing_order_without_one(home):
    api.order_new(ORDER, client="Петров", home=home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    assert [r[1] for r in _orders(home)] == [PHONE]


def test_brief_save_keeps_recorded_phone(home):
    api.order_new(ORDER, client="Петров", phone="8 911 111 11 11", home=home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    assert [r[1] for r in _orders(home)] == ["8 911 111 11 11"]


def test_brief_save_by_phone_updates_existing_order(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    again = api.brief_save(PHONE, FIXTURES / "petrov_brief.json", home)
    assert (again.order, again.created) == (ORDER, False)
    assert len(_orders(home)) == 1
    briefs = sqlite3.connect(home / "orders" / "kitchen.db").execute("SELECT COUNT(*) FROM briefs").fetchone()
    assert briefs == (2,)


def test_brief_save_by_unknown_phone_creates_nothing(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    result = runner.invoke(app, ["brief", "save", "+7 911 222-33-44", str(FIXTURES / "petrov_brief.json")])
    assert result.exit_code == 1
    assert "не найден" in result.output
    assert len(_orders(home)) == 1


def test_brief_save_by_phone_of_two_orders_lists_them(home):
    api.order_new(ORDER, phone=PHONE, home=home)
    api.order_new("2026-048-ivanov", phone="89000000000", home=home)
    result = runner.invoke(app, ["brief", "save", PHONE, str(FIXTURES / "petrov_brief.json")])
    assert result.exit_code == 1
    assert ORDER in result.output and "2026-048-ivanov" in result.output


def test_cli_brief_save_by_phone_message(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    result = runner.invoke(app, ["brief", "save", PHONE, str(FIXTURES / "petrov_brief.json")])
    assert result.exit_code == 0
    assert "Заказ 2026-047-petrov: бриф сохранён." in result.output


def test_brief_without_phone_leaves_order_phone_empty(home):
    brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    del brief["client"]["phone"]
    path = Path(home / "b.json")
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    api.brief_save("petrov", path, home)
    assert [r[1] for r in _orders(home)] == [""]


def test_quote_only_asks_for_estimate_before_workshop_documents(home):
    _v1(home)
    api.quote_and_save(ORDER, 20, home)
    assert _step(home).text == "Смета: kitchen estimate 2026-047-petrov."


@pytest.mark.parametrize(
    ("save", "compute", "prefix"),
    [
        (lambda h: api.estimate_and_save(ORDER, h), "estimate_from_order", "смета"),
        (lambda h: api.production_and_save(ORDER, h), "_order_project", "документы цеха"),
    ],
)
def test_refused_estimate_and_production_write_no_entry(home, monkeypatch, save, compute, prefix):
    _v1(home)
    original = getattr(api, compute)

    def stale_after_compute(*args, **kwargs):
        result = original(*args, **kwargs)
        _import_v2(home)
        return result

    monkeypatch.setattr(api, compute, stale_after_compute)
    with pytest.raises(OrderError, match="Изменилась версия проекта"):
        save(home)
    assert not [t for t in _done_texts(home) if t.startswith(prefix)]


def test_failed_done_entry_rolls_back_the_outputs(home, monkeypatch):
    _v1(home)
    original = api._insert_log

    def failing(conn, pk, kind, text, version, auto=False):
        if text.startswith("КП посчитано"):
            raise sqlite3.OperationalError("disk I/O error")
        return original(conn, pk, kind, text, version, auto)

    monkeypatch.setattr(api, "_insert_log", failing)
    with pytest.raises(sqlite3.OperationalError):
        api.quote_and_save(ORDER, 20, home)
    conn = sqlite3.connect(home / "orders" / "kitchen.db")
    kinds = {r[0] for r in conn.execute("SELECT kind FROM outputs")}
    assert "quote_pdf" not in kinds
    assert not (home / "orders" / ORDER / "out" / "quote.pdf").exists()
