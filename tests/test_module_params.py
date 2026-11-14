"""Параметры модуля: цвет фасада (story 4), число полок и дверей (story 5)."""

import json
from pathlib import Path

from kitchen import api

from tests.conftest import BASE_DOOR_PROJECT


def _project(home, mutate):
    project = json.loads(json.dumps(BASE_DOOR_PROJECT))
    mutate(project)
    path = home / "variant.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def _parts(home, mutate):
    return api.parts_from_project(_project(home, mutate), home)


# --- цвет фасада ---------------------------------------------------------------
# Н1 W600: две двери 297 x 716 мм, 2 * 0.212652 = 0.425304 -> 0.4253 м² (эталон ТЗ).


def test_module_facade_color_overrides_project_color(home):
    def mutate(p):
        p["materials"]["facades_lower_color"] = "RAL 7044"
        p["walls"][0]["rows"][0]["modules"][0]["materials"] = {"facade_color": "RAL 5002"}

    assert _parts(home, mutate).facade_area_by_color == {"RAL 5002": 0.4253}


def test_row_facade_color_splits_area_by_color(home):
    def mutate(p):
        p["materials"]["facades_lower_color"] = "RAL 7044"
        row = p["walls"][0]["rows"][0]
        row["materials"] = {"facade_color": "RAL 9003"}
        row["modules"].append(
            {"label": "Н2", "template": "base_door", "width": 600, "materials": {"facade_color": "RAL 3020"}}
        )

    # ряд красит Н1 в 9003, Н2 переопределяет цвет сам
    assert _parts(home, mutate).facade_area_by_color == {"RAL 3020": 0.4253, "RAL 9003": 0.4253}


def test_upper_row_color_override_goes_to_upper_facades(home):
    def mutate(p):
        p["materials"]["facades_lower_color"] = "RAL 7044"
        p["materials"]["facades_upper_color"] = "RAL 9003"
        p["walls"][0]["rows"].append(
            {
                "kind": "upper",
                "materials": {"facade_color": "RAL 1015"},
                "modules": [{"label": "В1", "template": "wall_door", "width": 600}],
            }
        )

    assert set(_parts(home, mutate).facade_area_by_color) == {"RAL 7044", "RAL 1015"}


def test_facade_color_override_splits_purchase_by_color(home):
    fixtures = Path(__file__).resolve().parent / "fixtures"
    order = "2026-047-petrov"
    project = json.loads((fixtures / "petrov_project.json").read_text(encoding="utf-8"))
    # Н1 перекрашен модулем: у Петрова нижние фасады RAL 7044, верхние RAL 9003
    first_lower = next(r for r in project["walls"][0]["rows"] if r["kind"] == "lower")["modules"][0]
    first_lower["materials"] = {**(first_lower.get("materials") or {}), "facade_color": "RAL 5002"}
    path = home / "petrov_recolored.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.brief_save(order, fixtures / "petrov_brief.json", home)
    api.import_project(order, path, home=home)

    purchased = {
        i.color: i.quantity
        for shop in api.purchase_from_order(order, home).shops
        for i in shop.items
        if i.kind == "facade"
    }
    assert set(purchased) == {"RAL 5002", "RAL 7044", "RAL 9003"}
    assert purchased == api.parts_from_order(order, home).facade_area_by_color
