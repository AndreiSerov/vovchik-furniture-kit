"""Тикет 23: проверка проекта `tall_overlap` (решение мастера 2026-11-15) — пенал на месте углового смещения
или поперёк модуля другого ряда — `error`, импорт отклоняется."""

from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

from tests.test_placement import corner, mod, wall, write

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def tall_overlaps(home, walls):
    return [f for f in api.check_project(write(home, walls), home).findings if f.code == "tall_overlap"]


def test_tall_of_other_row_on_the_corner_offset(home):
    # Н4 последний на A: нижний ряд Б начинается со смещения 600; пенал П1 записан в верхнем ряду Б на x 0
    walls = [
        wall("A", 1650, [mod("Н1", "base_door", 600), corner("Н4", "base_corner", 1050, "Б")]),
        wall(
            "Б",
            1800,
            [mod("Н5", "base_door", 1200)],
            [mod("П1", "tall_oven", 600), mod("В5", "wall_door", 1200)],
        ),
    ]
    [f] = tall_overlaps(home, walls)
    assert (f.level, f.subject, f.message) == (
        "error",
        "П1",
        "Пенал П1 (x 0–600 на стене Б) стоит на месте углового смещения или пересекает модуль Н4 нижнего ряда.",
    )


def test_tall_stepped_over_by_module_of_other_row(home):
    # П1 в верхнем ряду на x 600–1200, а нижний Н2 стоит на 450–1050 и «перешагивает» его
    walls = [
        wall(
            "A",
            2400,
            [mod("Н1", "base_door", 450), mod("Н2", "base_door", 600), mod("Н3", "base_door", 750)],
            [mod("В1", "wall_door", 600), mod("П1", "tall_oven", 600), mod("В2", "wall_door", 1200)],
        )
    ]
    [f] = tall_overlaps(home, walls)
    assert f.message == (
        "Пенал П1 (x 600–1200 на стене A) стоит на месте углового смещения или пересекает модуль Н2 нижнего ряда."
    )


def test_tall_in_line_with_modules_of_other_row_has_no_finding(home):
    walls = [
        wall(
            "A",
            2400,
            [mod("Н1", "base_door", 600), mod("Н2", "base_door", 1200)],
            [mod("В1", "wall_door", 600), mod("П1", "tall_oven", 600), mod("В2", "wall_door", 1200)],
        )
    ]
    assert tall_overlaps(home, walls) == []


def test_petrov_has_no_tall_overlap(home):
    findings = api.check_project(FIXTURES / "petrov_project.json", home).findings
    assert [f for f in findings if f.code == "tall_overlap"] == []


def test_import_rejects_tall_overlap(home):
    api.order_new("2026-047-petrov", home=home)
    walls = [
        wall(
            "A",
            2400,
            [mod("Н1", "base_door", 450), mod("Н2", "base_door", 600), mod("Н3", "base_door", 750)],
            [mod("В1", "wall_door", 600), mod("П1", "tall_oven", 600), mod("В2", "wall_door", 1200)],
        )
    ]
    path = write(home, walls)
    with pytest.raises(OrderError) as e:
        api.import_project("2026-047-petrov", path, home=home)
    assert any("Пенал П1 (x 600–1200 на стене A)" in p for p in e.value.problems)
