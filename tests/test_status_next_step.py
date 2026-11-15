"""Тикет 49: «Следующий шаг» в status.md, «Дальше» только новее последнего ручного done, поиск по телефону."""

import json
import re
import sqlite3
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"


def _section(text: str, title: str) -> str:
    match = re.search(rf"^## {title}\n(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE)
    assert match, f"нет раздела «{title}»"
    return match.group(1).strip()


def _import_v2(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    path = home / "project-v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, path, home=home)


def _step(home):
    r = api.status(ORDER, home)
    assert _section(r.text, "Следующий шаг") == r.next_step.text
    return r.next_step


def _v1(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def test_new_order_needs_brief(home):
    api.order_new(ORDER, home=home)
    step = _step(home)
    assert step.text == "Собрать бриф: kitchen-brief, затем kitchen brief save 2026-047-petrov <файл>."
    assert step.commands == [f"kitchen brief save {ORDER} <файл>"]


def test_after_brief_needs_project(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    step = _step(home)
    assert step.text == "Сделать проект: kitchen-project, затем kitchen import 2026-047-petrov <файл>."
    assert step.commands == [f"kitchen import {ORDER} <файл>"]


def test_after_import_needs_estimate_and_quote(home):
    _v1(home)
    step = _step(home)
    assert step.text == (
        "Смета и КП: kitchen estimate 2026-047-petrov, затем "
        "kitchen quote 2026-047-petrov --days <рабочих дней>."
    )
    assert step.commands == [f"kitchen estimate {ORDER}", f"kitchen quote {ORDER} --days <рабочих дней>"]


def test_stale_outputs_come_first(home):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    _import_v2(home)
    step = _step(home)
    assert step.text.startswith("Пересчитать устаревшее: ")
    assert f"kitchen estimate {ORDER}" in step.commands
    assert step.text == f"Пересчитать устаревшее: {', '.join(step.commands)}."
    assert len(set(step.commands)) == len(step.commands)


def test_quote_without_production_needs_workshop_documents(home):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 35, home)
    step = _step(home)
    assert step.text == "Документы цеха: kitchen production 2026-047-petrov."
    assert step.commands == [f"kitchen production {ORDER}"]


def test_everything_ready(home):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 35, home)
    api.production_and_save(ORDER, home)
    step = _step(home)
    assert step.text == "Все документы готовы: выезд на монтаж по чек-листу."
    assert step.commands == []


def test_production_kinds_match_what_production_writes(home):
    _v1(home)
    api.production_and_save(ORDER, home)
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        kinds = {r[0] for r in conn.execute("SELECT kind FROM outputs")}
    assert kinds == set(api.PRODUCTION_KINDS)
    assert "drilling" not in api.PRODUCTION_KINDS


def test_next_entries_only_after_last_done(home):
    api.order_new(ORDER, home=home)
    api.log_add(ORDER, "next", "старое", home)
    api.log_add(ORDER, "done", "сделано", home)
    assert _section(api.status(ORDER, home).text, "Дальше") == "Пока ничего."
    api.log_add(ORDER, "next", "новое", home)
    later = _section(api.status(ORDER, home).text, "Дальше")
    assert "новое" in later and "старое" not in later


def test_next_entries_shown_when_nothing_done(home):
    api.order_new(ORDER, home=home)
    api.log_add(ORDER, "next", "позвонить клиенту", home)
    assert "позвонить клиенту" in _section(api.status(ORDER, home).text, "Дальше")


def test_automatic_done_does_not_reset_next(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.log_add(ORDER, "next", "позвонить клиенту", home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)  # программа пишет «бриф сохранён»
    assert "позвонить клиенту" in _section(api.status(ORDER, home).text, "Дальше")
    api.log_add(ORDER, "done", "позвонил", home)
    assert _section(api.status(ORDER, home).text, "Дальше") == "Пока ничего."


def test_estimate_without_quote_still_needs_quote(home):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    assert _step(home).text.startswith("Смета и КП: ")


def test_one_workshop_document_is_not_enough(home):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 35, home)
    api.cut_and_save(ORDER, home)
    assert _step(home).text == "Документы цеха: kitchen production 2026-047-petrov."


def test_stale_workshop_documents_collapse_to_production(home):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 35, home)
    api.production_and_save(ORDER, home)
    _import_v2(home)
    step = _step(home)
    assert step.text == (
        "Пересчитать устаревшее: kitchen estimate 2026-047-petrov, "
        "kitchen quote 2026-047-petrov --days <рабочих дней>, kitchen production 2026-047-petrov."
    )
    assert step.commands[-1] == f"kitchen production {ORDER}"


def test_one_stale_workshop_document_also_gives_production(home):
    _v1(home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 35, home)
    api.production_and_save(ORDER, home)
    _import_v2(home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 35, home)
    api.cut_and_save(ORDER, home)
    assert _step(home).text == "Пересчитать устаревшее: kitchen production 2026-047-petrov."


@pytest.mark.parametrize("query", ["+7 900 000-00-00", "89000000000", "9000000000"])
def test_order_found_by_phone(home, query):
    api.order_new(ORDER, client="Петров", phone="+7 (900) 000-00-00", home=home)
    api.order_new("2026-048-ivanov", client="Иванов", phone="8 911 111 11 11", home=home)
    assert api.status(query, home).order == ORDER


def test_phone_without_match_and_with_two_matches(home):
    api.order_new("2026-047-petrov", phone="+7 900 000-00-00", home=home)
    api.order_new("2026-048-ivanov", phone="8 900 000 00 00", home=home)
    with pytest.raises(OrderError) as e:
        api.status("9000000000", home)
    assert "2026-047-petrov" in str(e.value) and "2026-048-ivanov" in str(e.value)
    with pytest.raises(OrderError, match="не найден"):
        api.status("9111111111", home)


def test_order_number_is_not_a_phone(home):
    api.order_new(ORDER, phone="2026047 0000", home=home)  # номер заказа не считается телефоном
    api.order_new("2026-048-ivanov", client="Иванов", home=home)
    assert api.status("2026-047", home).order == ORDER
    assert api.status("Иванов", home).order == "2026-048-ivanov"


def test_v3_database_marks_brief_saved_as_automatic(home):
    # база v3: создаём текущую через API и откатываем колонку log.auto
    api.order_new(ORDER, client="Петров", home=home)
    api.log_add(ORDER, "next", "позвонить клиенту", home)
    db = home / "orders" / "kitchen.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO log (order_id, created_at, kind, text) VALUES (1, '2026-11-01T00:00:00+00:00', 'done', 'бриф сохранён')"
    )
    conn.execute("ALTER TABLE log DROP COLUMN auto")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    first = api.status(ORDER, home)
    assert "позвонить клиенту" in _section(first.text, "Дальше")
    rows = (
        sqlite3.connect(db)
        .execute("SELECT kind, auto FROM log WHERE kind IN ('next', 'done') ORDER BY id")
        .fetchall()
    )
    assert rows == [("next", 0), ("done", 1)]
    assert api.status(ORDER, home).text == first.text  # повторное открытие ничего не меняет


def test_phone_compares_ten_digits_not_fewer(home):
    api.order_new(ORDER, phone="+7 900 123-45-67", home=home)
    api.order_new("2026-048-ivanov", phone="+7 911 123-45-67", home=home)
    assert api.status("89001234567", home).order == ORDER
