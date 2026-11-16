"""Заказ Петрова целиком (1.7): фикстура проекта по ТЗ, проверки, детали, общие позиции."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"


@pytest.fixture
def petrov(home):
    """Заказ Петрова в БД: бриф и версия 1 проекта."""
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    return home


def test_petrov_passes_checks_without_errors_and_n5_warns(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    result = api.check_project(FIXTURES / "petrov_project.json", home)
    assert [f.message for f in result.errors] == []
    back = [f for f in result.warnings if f.code == "no_back_panel"]
    assert [f.subject for f in back] == ["Н5"]


def all_parts(result):
    return [p for m in result.modules for p in m.parts]


def test_petrov_parts_from_order(petrov):
    result = api.parts_from_order("petrov", petrov)
    assert [m.module for m in result.modules] == [
        "П1", "Н1", "Н2", "Н3", "Н4", "В1", "В2", "В3", "В4", "Н5", "Н6", "В5", "В6",
    ]  # fmt: skip
    parts = all_parts(result)
    assert len(parts) == 100
    ids = [p.id for p in parts]
    assert len(ids) == len(set(ids))  # маркировки уникальны во всём заказе
    for p in parts:
        assert p.position is not None and p.orientation is not None, p.id
        assert {p.orientation.length, p.orientation.width, p.orientation.thickness} == {"x", "y", "z"}, p.id
    assert result.edges["edge_08_white"].purchase_m == 50.9
    hw = result.hardware
    assert (hw["aventos_hks"], hw["bin_2sec"]) == (2, 1)  # подъёмники В1 и В5, ведро Н5 из ТЗ
    assert (hw["hinge_71B3550"], hw["plate_173L6100"], hw["handle_profile"]) == (20, 20, 18)
    assert (hw["hanger_camar807"], hw["screw_3_5x16"]) == (12, 330)


def test_source_names_version_and_brief(petrov):
    result = api.parts_from_order(ORDER, petrov)
    src = result.source
    assert (src.kind, src.order, src.version) == ("order", ORDER, 1)
    assert src.brief_id == 1 and src.version_brief_id == 1
    assert len(src.content_hash) == 64
    assert result.mock is True


def test_newer_brief_warns_that_version_is_stale(petrov):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", petrov)  # бриф №2 новее записанного у версии
    result = api.parts_from_order(ORDER, petrov)
    assert (result.source.brief_id, result.source.version_brief_id) == (2, 1)
    text = " ".join(result.warnings)
    assert "бриф №2" in text.lower() and "№1" in text and "версия 1" in text


def test_no_project_yet_is_an_error(home):
    api.order_new(ORDER, home=home)
    with pytest.raises(OrderError, match="проекта ещё нет"):
        api.parts_from_order(ORDER, home)


def test_project_file_source_and_findings(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    result = api.parts_from_project(FIXTURES / "petrov_project.json", home)
    src = result.source
    assert (src.kind, src.order, src.version, src.brief_id) == ("file", ORDER, None, 1)
    assert src.path.endswith("petrov_project.json")
    assert {f.code for f in result.findings} >= {"no_back_panel"}
    assert not [f for f in result.findings if f.level == "error"]
    # тот же проект, что в БД: хеш содержимого совпадает с хешем версии
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    assert api.parts_from_order(ORDER, home).source.content_hash == src.content_hash


def test_facade_area_by_color(petrov):
    result = api.parts_from_order(ORDER, petrov)
    by_color = result.facade_area_by_color
    assert set(by_color) == {"RAL 7044", "RAL 9003"}
    lower = {"П1", "Н1", "Н2", "Н3", "Н4", "Н5", "Н6"}
    expected = {"RAL 7044": 0.0, "RAL 9003": 0.0}
    for m in result.modules:
        color = "RAL 7044" if m.module in lower else "RAL 9003"
        expected[color] += sum(p.area_m2 for p in m.parts if p.area_m2 is not None)
    assert by_color == {k: pytest.approx(v, abs=1e-4) for k, v in expected.items()}
    assert sum(by_color.values()) == pytest.approx(result.facade_area, abs=1e-4)


def test_facade_without_color_is_grouped_apart(home, base_door_project):
    result = api.parts_from_project(base_door_project, home)
    assert result.facade_area_by_color == {"без цвета": 0.4253}


def common(result):
    return {c.kind: c for c in result.common}


def test_petrov_common_items(petrov):
    """Руками по правилам МОК: нижний фронт 2800 + 1200 − угловое смещение 600 = 3400 мм, верхние ряды 2200 + 1200 = 3400 мм,
    запас на стыки 5%: цоколь 3570 мм → 1 штука 4 м; шина 3570 мм → 2 по 2 м; LED 3.57 → 4 м,
    блок питания 1 (5 м на блок); столешница: общая длина 2200 + 1200 = 3400 мм — один лист 4100."""
    c = common(api.parts_from_order(ORDER, petrov))
    assert list(c) == ["plinth", "rail", "hangers", "led", "led_power_supply", "countertop"]
    assert (c["plinth"].item, c["plinth"].quantity, c["plinth"].length_mm) == ("plinth_100", 1, 3400)
    assert (c["rail"].item, c["rail"].quantity, c["rail"].length_mm) == ("rail_2000", 2, 3400)
    assert (c["hangers"].item, c["hangers"].quantity, c["hangers"].in_hardware) == (
        "hanger_camar807",
        12,
        True,
    )
    assert (c["led"].item, c["led"].quantity, c["led"].unit, c["led"].length_mm) == (
        "led_profile_m",
        4,
        "м",
        3400,
    )
    assert (c["led_power_supply"].item, c["led_power_supply"].quantity) == ("led_psu", 1)
    top = c["countertop"]
    assert (top.item, top.quantity, top.unit, top.length_mm) == ("top_38_marble", 1, "лист", 3400)
    assert "3400" in top.basis
    assert not any(x.in_hardware for k, x in c.items() if k != "hangers")


def test_common_items_are_not_added_to_hardware(petrov):
    result = api.parts_from_order(ORDER, petrov)
    assert not {"plinth_100", "rail_2000", "led_profile_m", "led_psu"} & set(result.hardware)


def test_without_led_and_countertop_those_items_are_absent(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["led"] = False
    del project["materials"]["countertop"]
    path = home / "p.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    kinds = [c.kind for c in api.parts_from_project(path, home).common]
    assert kinds == ["plinth", "rail", "hangers"]


def test_countertop_longer_than_sheet_takes_whole_sheets(home):
    materials = home / "workshop/materials.yaml"
    materials.write_text(materials.read_text().replace("лист: [4100, 600]", "лист: [2000, 600]"))
    result = api.parts_from_project(FIXTURES / "petrov_project.json", home)
    top = common(result)["countertop"]
    # общая длина 3400 мм при листе 2000 — два листа
    assert top.quantity == 2


def test_common_rules_come_from_workshop(home):
    path = home / "workshop/construction.yaml"
    text = path.read_text()
    path.write_text(text.replace("запас_на_стыки: 0.05", "запас_на_стыки: 0.2"))
    c = common(api.parts_from_project(FIXTURES / "petrov_project.json", home))
    assert c["plinth"].quantity == 2  # 3400 × 1.2 = 4080 мм > одной штуки
    path.write_text(text.replace("смещение_нижнего: 600", "смещение_нижнего: 500"))
    c = common(api.parts_from_project(FIXTURES / "petrov_project.json", home))
    assert c["plinth"].length_mm == 3500  # угловое смещение — из мастерской, а не из кода
    path.write_text(text.replace("округление_метров: 1", "округление_метров: 5"))
    c = common(api.parts_from_project(FIXTURES / "petrov_project.json", home))
    assert c["led"].quantity == 5  # 3.57 м вверх до кратного 5


def test_tall_in_upper_row_gives_same_plinth_and_countertop_as_in_lower_row(home):
    # Пенал описывается один раз и входит в оба ряда: цоколь 3400 мм (2800 + 1200 - 600 глухой части угла)
    # и общая длина столешницы 3400 мм не зависят от того, в каком ряду описан П1.
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    rows = {row["kind"]: row for row in project["walls"][0]["rows"]}
    assert rows["lower"]["modules"][0]["label"] == "П1"
    rows["upper"]["modules"].insert(0, rows["lower"]["modules"].pop(0))
    path = home / "p.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.parts_from_project(path, home)
    c = common(result)
    assert c["plinth"].length_mm == 3400
    assert c["rail"].length_mm == 3400
    assert c["countertop"].length_mm == 3400


def test_tall_in_upper_row_breaks_countertop_at_its_place(home):
    # Н1 600 | П1 600 | Н2 400 на стене 1600: общая длина столешницы 1000 мм, цоколь 1600 мм.
    project = {
        "order": ORDER,
        "ceiling_height": 2700,
        "walls": [
            {
                "label": "A",
                "length": 1600,
                "rows": [
                    {
                        "kind": "lower",
                        "modules": [
                            {"label": "Н1", "template": "base_door", "width": 600},
                            {"label": "Н2", "template": "base_door", "width": 400},
                        ],
                    },
                    {
                        "kind": "upper",
                        "modules": [
                            {"label": "В1", "template": "wall_door", "width": 600},
                            {"label": "П1", "template": "tall_oven", "width": 600},
                            {"label": "В2", "template": "wall_door", "width": 400},
                        ],
                    },
                ],
            }
        ],
        "materials": {
            "body": "ldsp_16_white",
            "facades_lower": "mdf_enamel_matt",
            "facades_upper": "mdf_enamel_matt",
            "handles": "handle_profile",
            "countertop": "top_38_marble",
        },
    }
    path = home / "p.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    c = common(api.parts_from_project(path, home))
    assert c["plinth"].length_mm == 1600
    assert c["countertop"].length_mm == 1000
    assert c["countertop"].quantity == 1


def test_workshop_rejects_unknown_common_item_id(home):
    path = home / "workshop/construction.yaml"
    path.write_text(path.read_text().replace("позиция: plinth_100", "позиция: plinth_nope"))
    result = api.check_workshop(home)
    assert not result.valid
    assert any("общие_позиции.цоколь.позиция" in e and "plinth_nope" in e for e in result.errors)


def test_unknown_countertop_is_a_clear_error(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["materials"]["countertop"] = "top_nope"
    path = home / "p.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(OrderError, match="top_nope"):
        api.parts_from_project(path, home)
