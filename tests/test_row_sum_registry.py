"""Сумма ряда: «входит в оба ряда» — свойство шаблона в реестре, а не имя шаблона."""

import dataclasses

from kitchen import parts

from tests.test_checks import findings, make_project, module


def test_row_sum_uses_both_rows_property_of_registered_template(home, monkeypatch):
    # Пенал под другим именем, но с тем же свойством реестра: 600 + 2200 = 2800 в обоих рядах.
    monkeypatch.setitem(parts.TEMPLATES, "tall_other", dataclasses.replace(parts.TEMPLATES["tall_oven"]))
    wall = (
        "A",
        2800,
        [
            ("lower", [module("П1", "tall_other", 600), module("Н1", "base_door", 2200)]),
            ("upper", [module("В1", "wall_door", 2200)]),
        ],
    )
    path = make_project(home / "p.json", [wall])
    assert findings(path, home, "row_sum") == []


def test_row_sum_without_the_property_counts_only_own_row(home, monkeypatch):
    monkeypatch.setitem(
        parts.TEMPLATES, "tall_oven", dataclasses.replace(parts.TEMPLATES["tall_oven"], both_rows=False)
    )
    wall = (
        "A",
        2800,
        [
            ("lower", [module("П1", "tall_oven", 600), module("Н1", "base_door", 2200)]),
            ("upper", [module("В1", "wall_door", 2200)]),
        ],
    )
    path = make_project(home / "p.json", [wall])
    [f] = findings(path, home, "row_sum")
    assert (f.value, f.limit) == (2200, 2800)
