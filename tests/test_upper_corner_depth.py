"""Тикет 50: верхний угловой модуль берёт глубину верхних; смещение верхнего ряда соседней стены —
глубина верхних плюс толщина фасада углового."""

import copy
import json
from pathlib import Path

from kitchen import api

from tests.conftest import BASE_DOOR_PROJECT, REPO

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def mod(label, template, width, **extra):
    return {"label": label, "template": template, "width": width, **extra}


def corner_walls(b_width, corner_extra=None):
    """Стена А — один верхний угловой 600; стена Б — один верхний модуль шириной `b_width`."""
    corner = mod("В1", "wall_corner", 600, corner={"adjacent_wall": "Б"}, **(corner_extra or {}))
    return [
        {"label": "A", "length": 600, "rows": [{"kind": "upper", "modules": [corner]}]},
        {
            "label": "Б",
            "length": 1000,
            "rows": [{"kind": "upper", "modules": [mod("В2", "wall_door", b_width)]}],
        },
    ]


def write(home, walls):
    data = copy.deepcopy(BASE_DOOR_PROJECT)
    data["walls"] = walls
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def row_sums(home, walls):
    return [f for f in api.check_project(write(home, walls), home).findings if f.code == "row_sum"]


def edit(home, name, old, new):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8")
    assert old in text, (name, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_upper_offset_is_upper_depth_plus_facade_thickness(home):
    # мок: верхний.глубина 320 + фасад МДФ 19 = 339; 661 + 339 = 1000
    assert row_sums(home, corner_walls(661)) == []
    [f] = row_sums(home, corner_walls(660))
    assert (f.subject, f.value, f.limit) == ("Б", 999, 1000)


def test_upper_offset_follows_upper_depth_of_workshop(home):
    edit(home, "construction.yaml", "глубина: 320", "глубина: 350")
    assert row_sums(home, corner_walls(631)) == []  # 350 + 19 = 369
    [f] = row_sums(home, corner_walls(661))
    assert f.value == 1030


def test_upper_offset_follows_facade_thickness_of_the_corner(home):
    lines = (home / "workshop/materials.yaml").read_text(encoding="utf-8").splitlines()
    line = next(ln for ln in lines if "id: mdf_enamel_matt" in ln)
    thick = line.replace("mdf_enamel_matt", "mdf_22").replace("толщина: 19", "толщина: 22")
    edit(home, "materials.yaml", line, line + "\n" + thick)
    walls = corner_walls(658, {"materials": {"facades": "mdf_22"}})
    assert row_sums(home, walls) == []  # 320 + 22 = 342; 658 + 342 = 1000
    assert row_sums(home, corner_walls(658)) != []  # фасад обычной толщины — 339, а не 342


def test_lower_offset_is_unchanged(home):
    corner = mod("Н1", "base_corner", 1050, corner={"adjacent_wall": "Б"})
    walls = [
        {"label": "A", "length": 1050, "rows": [{"kind": "lower", "modules": [corner]}]},
        {"label": "Б", "length": 1000, "rows": [{"kind": "lower", "modules": [mod("Н2", "base_door", 400)]}]},
    ]
    assert row_sums(home, walls) == []  # 400 + 600


def test_removed_keys_are_unknown_keys_of_the_workshop(home):
    path = home / "workshop/construction.yaml"
    original = path.read_text(encoding="utf-8")
    for key, value in (("глубина_верхнего", 600), ("смещение_верхнего", 600)):
        path.write_text(
            original.replace("  допуск_стены: 0\n", f"  допуск_стены: 0\n  {key}: {value}\n"),
            encoding="utf-8",
        )
        result = api.check_workshop(home)
        assert not result.valid
        assert any(key in e and "неизвестный ключ" in e for e in result.errors), result.errors
    path.write_text(original, encoding="utf-8")
    assert api.check_workshop(home).valid


def test_shipped_workshop_has_no_removed_keys():
    text = (REPO / "workshop/construction.yaml").read_text(encoding="utf-8")
    assert "глубина_верхнего" not in text and "смещение_верхнего" not in text


def test_petrov_wall_b_upper_row_ends_with_gap_and_sums(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    wall_b = next(w for w in project["walls"] if w["label"] == "Б")
    upper = next(r for r in wall_b["rows"] if r["kind"] == "upper")
    assert upper["modules"][-1].get("gap") is True
    assert upper["modules"][-1]["width"] == 261  # 1800 − 450 − 750 − 339
    api.brief_save("2026-047-petrov", FIXTURES / "petrov_brief.json", home)
    result = api.check_project(FIXTURES / "petrov_project.json", home)
    assert [f.message for f in result.errors] == []


def test_petrov_v4_depth_is_upper_depth(home):
    api.brief_save("2026-047-petrov", FIXTURES / "petrov_brief.json", home)
    result = api.parts_from_project(FIXTURES / "petrov_project.json", home)
    v4 = next(m for m in result.modules if m.module == "В4")
    assert {p.width for p in v4.parts if p.name == "боковина"} == {320}
    bottom = next(p for p in v4.parts if p.name == "дно")
    assert (bottom.length, bottom.width) == (568, 320)
    facade = next(p for p in v4.parts if p.name == "фасад")
    assert facade.position.y == 320


def test_docs_describe_the_new_rule():
    mocks = (REPO / "MOCKS.md").read_text(encoding="utf-8")
    decisions = (REPO / "DECISIONS.md").read_text(encoding="utf-8")
    assert "реально 350–400" in mocks and "глубина_верхнего" not in mocks
    topic = next(ln for ln in decisions.splitlines() if ln.startswith("- **Угловые модули:**"))
    assert "Смещение обоих рядов 600" not in topic and "глубиной 600 мм" not in topic
    assert "глубина верхних" in topic and "толщина фасада" in topic
