"""Тикет 45: уборка проверок проекта — минимальная деталь, метки, одна функция дублей, сходимость рядов."""

import json
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest
from kitchen import api
from kitchen.common import row_layouts
from kitchen.errors import KitchenError, OrderError

from tests.test_input_guards_project import lower, write

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def too_small(result):
    return [f for f in result.errors if f.code == "part_too_small"]


def test_petrov_copy_has_no_part_too_small(home):
    result = api.check_project(FIXTURES / "petrov_project.json", home)
    assert too_small(result) == []


def test_base_door_35_wide_has_a_part_too_small_finding_with_label_and_size(home):
    path = write(home, lower({"label": "Н1", "template": "base_door", "width": 35}), 35)
    found = too_small(api.check_project(path, home))
    assert found
    shelf = next(f for f in found if f.message.startswith("Н1-полка "))
    assert shelf.subject == "Н1"
    assert shelf.message == "Н1-полка 540×1 мм: меньше 30 мм — проверьте размеры модуля"
    assert shelf.value == 1 and shelf.limit == 30


def test_base_drawers_120_wide_has_a_part_too_small_finding(home):
    path = write(home, lower({"label": "В1", "template": "base_drawers", "width": 120}), 120)
    found = too_small(api.check_project(path, home))
    assert found
    assert all(
        "меньше 30 мм" in f.message and f.subject == "В1" and f.message.startswith("В1-") for f in found
    )


def test_missing_min_part_size_key_is_a_load_error(home):
    path = home / "workshop" / "construction.yaml"
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.startswith("мин_размер_детали")]
    assert len(lines) == len(text.splitlines()) - 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = api.check_workshop(home)
    assert not result.valid
    assert any("construction.yaml" in e and "мин_размер_детали" in e for e in result.errors)


# --- нормализация меток ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("У1", "Y1"),
        ("У1", "y1"),
        ("Н1", "Н\u200b1"),
        ("Н1", "\ufeffН1"),
        ("Н1", "Н\u00ad1"),
        ("Н1", "Н\uff11"),
        ("В12", "В\uff11\uff12"),
    ],
)
def test_labels_equal_after_normalisation_are_duplicates_unicode(home, first, second):
    modules = [
        {"label": first, "template": "base_door", "width": 600},
        {"label": second, "template": "base_door", "width": 600},
    ]
    found = [f for f in api.check_project(write(home, lower(*modules), 1200), home).errors]
    assert [f.code for f in found if f.code == "duplicate_label"] == ["duplicate_label"]


def test_different_labels_are_not_duplicates(home):
    modules = [
        {"label": "Н1", "template": "base_door", "width": 600},
        {"label": "Н2", "template": "base_door", "width": 600},
    ]
    result = api.check_project(write(home, lower(*modules), 1200), home)
    assert [f for f in result.errors if f.code == "duplicate_label"] == []


# --- одна функция дублей меток ----------------------------------------------------------------


@pytest.fixture
def repeated(home):
    modules = [
        {"label": "Н1", "template": "base_door", "width": 600},
        {"label": "H1", "template": "base_door", "width": 600},
    ]
    return write(home, lower(*modules), 1200)


def test_parts_and_cut_raise_the_same_order_error_as_the_finding(home, repeated):
    message = next(f.message for f in api.check_project(repeated, home).errors if f.code == "duplicate_label")
    for call in (api.parts_from_project, api.cut_from_project, api.estimate_from_project):
        with pytest.raises(OrderError) as e:
            call(repeated, home)
        assert e.value.problems == [message]


def test_api_builds_the_duplicate_error_in_one_place():
    source = (Path(api.__file__)).read_text(encoding="utf-8")
    assert source.count("duplicate_labels(") == 1


# --- сходимость раскладки ряда ----------------------------------------------------------------


def test_row_layouts_raises_when_offsets_do_not_settle():
    """Модуль перед пеналом меняет ширину при каждом чтении: отступ пенала не сходится."""
    ticks = count()

    class Wobbly(SimpleNamespace):
        @property
        def width(self):
            return 100 + next(ticks) * 7

    wobbly = Wobbly(label="В1", template="base_door")
    tall = SimpleNamespace(label="П1", template="tall_oven", width=600)
    door = SimpleNamespace(label="Н1", template="base_door", width=600)
    wall = SimpleNamespace(
        rows=[
            SimpleNamespace(kind="lower", modules=[door, door]),
            SimpleNamespace(kind="upper", modules=[wobbly, tall]),
        ]
    )
    with pytest.raises(KitchenError, match="не сходится"):
        row_layouts(wall)


def test_row_layouts_still_settles_on_petrov(home):
    data = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    assert any(m["template"] == "tall_oven" for w in data["walls"] for r in w["rows"] for m in r["modules"])
    assert api.check_project(FIXTURES / "petrov_project.json", home).errors == []
