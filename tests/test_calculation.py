"""Тикет 70: расчёт (`api.calculation`) и его методы-выводы по таблице «Выводы расчёта» спеки этапа 9."""

import shutil
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"


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
