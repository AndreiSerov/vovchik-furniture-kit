"""Тикет 49: «Следующий шаг» в status.md, «Дальше» только новее последнего done, поиск по падежу."""

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


@pytest.mark.parametrize("query", ["Петрова", "Петрову", "Петровым"])
def test_surname_in_oblique_case(home, query):
    api.order_new(ORDER, client="Петров", home=home)
    assert api.status(query, home).order == ORDER


def test_exact_match_beats_trimming(home):
    api.order_new("2026-047-petrov", home=home)
    api.order_new("2026-048-petrova", home=home)
    assert api.status("Петрова", home).order == "2026-048-petrova"


def test_two_petrovs_is_an_error_with_list(home):
    api.order_new("2026-047-petrov", home=home)
    api.order_new("2026-048-petrov", home=home)
    with pytest.raises(OrderError) as e:
        api.status("Петрова", home)
    assert "2026-047-petrov" in str(e.value) and "2026-048-petrov" in str(e.value)
