"""Смета через публичный API: себестоимость из закупки, труд по операциям, цена с округлением."""

import json
import re
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import KitchenError
from kitchen.result import MOCK_BANNER

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"

# Малый проект: мойка, ящики с варочной, ПММ. Количества ниже посчитаны вручную по деталям:
#   Н1 base_sink: 5 ЛДСП корпуса (2 бока, дно, 2 царги), 2 фасада, без задней стенки и полки;
#   Н2 base_drawers: 5 ЛДСП корпуса, 1 ХДФ, 3 фасада, 3 ящика Tandembox по 2 детали ЛДСП;
#   Н3 dishwasher_panel: 1 фасад, корпуса нет.
SMALL = {
    "order": ORDER,
    "ceiling_height": 2700,
    "walls": [
        {
            "label": "A",
            "length": 1800,
            "rows": [
                {
                    "kind": "lower",
                    "modules": [
                        {"label": "Н1", "template": "base_sink", "width": 600},
                        {"label": "Н2", "template": "base_drawers", "width": 600},
                        {"label": "Н3", "template": "dishwasher_panel", "width": 600},
                    ],
                }
            ],
        }
    ],
    "materials": {
        "body": "ldsp_16_white",
        "facades_lower": "mdf_enamel_matt",
        "facades_upper": "mdf_enamel_matt",
        "handles": "handle_profile",
    },
    "appliances": [
        {"type": "sink", "width": 600, "module": "Н1"},
        {"type": "hob", "width": 600, "module": "Н2"},
        {"type": "dishwasher", "width": 600, "module": "Н3"},
    ],
}

SMALL_COUNTS = {
    "cut": 17,
    "edge": 6.588,  # с припуском, из api.parts: чистые 6.288 м + 12 кромлёных сторон по 25 мм
    "drill": 16,  # 10 ЛДСП корпуса + 6 фасадов; ХДФ и 6 деталей ящиков не считаются
    "case_assembly": 2,  # Н1 и Н2; ПММ без корпуса
    "drawer_assembly_extra": 3,
    "install": 3,
    "sink_cutout": 1,
    "hob_cutout": 1,
    "site_visit": 1,
    "measuring": 1,
    "design": 1,
}


def edit_workshop(home, name, old, new):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def unmock(home):
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


@pytest.fixture
def small(home):
    api.order_new(ORDER, home=home)
    path = home / "small.json"
    path.write_text(json.dumps(SMALL, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, path, home=home)
    return home


def lines(result):
    return {line.operation: line for line in result.labor.lines}


def test_cost_equals_purchase_total_on_petrov(home):
    petrov(home)
    result = api.estimate_from_order(ORDER, home)
    assert result.materials_cost == api.purchase_from_order(ORDER, home).total
    assert result.source.version == 1


def test_cost_equals_purchase_total_on_small_project(small):
    result = api.estimate_from_order(ORDER, small)
    assert result.materials_cost == api.purchase_from_order(ORDER, small).total


def test_small_project_quantities_match_hand_count(small):
    result = api.estimate_from_order(ORDER, small)
    assert {op: line.quantity for op, line in lines(result).items()} == SMALL_COUNTS
    assert (
        api.parts_from_project(small / "small.json", small).edges["edge_08_white"].with_allowance_m == 6.588
    )


def test_small_project_labor_is_minutes_times_rate(small):
    result = api.estimate_from_order(ORDER, small)
    # норма × количество, минут: 4*17, 3*6.588, 5*16, 30*2, 15*3, 45*3, 60, 60, 90, 90, 240
    minutes = {
        "cut": 68,
        "edge": 19.764,
        "drill": 80,
        "case_assembly": 60,
        "drawer_assembly_extra": 45,
        "install": 135,
        "sink_cutout": 60,
        "hob_cutout": 60,
        "site_visit": 90,
        "measuring": 90,
        "design": 240,
    }
    # ставка 1000 ₽/ч: рубли строки = минуты × 1000 / 60, до копеек
    rubles = {
        "cut": 1133.33,
        "edge": 329.40,
        "drill": 1333.33,
        "case_assembly": 1000,
        "drawer_assembly_extra": 750,
        "install": 2250,
        "sink_cutout": 1000,
        "hob_cutout": 1000,
        "site_visit": 1500,
        "measuring": 1500,
        "design": 4000,
    }
    got = lines(result)
    assert {op: line.minutes for op, line in got.items()} == minutes
    assert {op: line.amount for op, line in got.items()} == rubles
    assert result.labor.minutes == pytest.approx(947.764)
    assert result.labor.total == 15796.07  # Σ минут × ставка / 60, не сумма округлённых строк
    assert result.labor.rate_per_hour == 1000


def test_labor_total_is_sum_minutes_times_rate(small):
    r = api.estimate_from_order(ORDER, small)
    assert r.labor.total == 15796.07  # 947.764 мин × 1000 / 60 = 15796.0667
    assert sum(line.amount for line in r.labor.lines) == pytest.approx(15796.06)  # строки — до копеек


def test_sink_with_tandembox_insert_counts_a_drawer_set(small):
    """Ящик = комплект Tandembox в фурнитуре модулей: поведение зафиксировано, решение ждёт мастера."""
    data = json.loads((small / "small.json").read_text(encoding="utf-8"))
    data["walls"][0]["rows"][0]["modules"][0]["contents"] = {"inserts": ["tandembox_M_500"]}
    path = small / "sink-insert.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert lines(api.estimate_from_project(path, small))["drawer_assembly_extra"].quantity == 4


def test_petrov_quantities(home):
    petrov(home)
    got = lines(api.estimate_from_order(ORDER, home))
    parts = api.parts_from_project(FIXTURES / "petrov_project.json", home)
    assert got["cut"].quantity == 82  # 71 ЛДСП + 11 ХДФ; фасады и столешница не считаются
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    labels = [m["label"] for w in project["walls"] for r in w["rows"] for m in r["modules"]]
    assert got["install"].quantity == len(labels) == len(parts.modules)
    assert got["drill"].quantity == 81  # 63 ЛДСП корпуса (71 − 8 деталей ящиков) + 18 фасадов
    assert got["case_assembly"].quantity == 12  # 13 модулей без ПММ
    assert got["edge"].quantity == 46.216
    assert got["sink_cutout"].quantity == 1 and got["hob_cutout"].quantity == 1
    assert got["drawer_assembly_extra"].quantity == parts.hardware["tandembox_M_500"]
    assert got["site_visit"].quantity == got["measuring"].quantity == got["design"].quantity == 1


def test_norm_swap_changes_only_its_line_and_total(small):
    before = api.estimate_from_order(ORDER, small)
    edit_workshop(small, "labor.yaml", "распил_на_деталь: 4", "распил_на_деталь: 10")
    after = api.estimate_from_order(ORDER, small)
    b, a = lines(before), lines(after)
    assert a["cut"].minutes == 170 and a["cut"].amount == 2833.33
    assert {k: v for k, v in a.items() if k != "cut"} == {k: v for k, v in b.items() if k != "cut"}
    assert after.labor.total == pytest.approx(before.labor.total - 1133.33 + 2833.33)
    assert after.materials_cost == before.materials_cost


def test_rate_swap_changes_amounts_not_minutes(small):
    before = api.estimate_from_order(ORDER, small)
    edit_workshop(small, "labor.yaml", "ставка_в_час: 1000", "ставка_в_час: 1500")
    after = api.estimate_from_order(ORDER, small)
    for op, line in lines(after).items():
        assert line.minutes == lines(before)[op].minutes
    assert lines(after)["install"].amount == 3375
    assert after.labor.total == 23694.10  # 947.764 мин × 25 ₽/мин


def quiet_pricing(home, rounding="вверх до 1000"):
    """Наценка, расходники, доставка и труд равны нулю: цена зависит только от материалов и доставки."""
    edit_workshop(home, "pricing.yaml", "наценка_на_материалы: 0.10", "наценка_на_материалы: 0")
    edit_workshop(home, "pricing.yaml", "расходники_от_материалов: 0.03", "расходники_от_материалов: 0")
    edit_workshop(home, "pricing.yaml", "доставка_материалов: 2500", "доставка_материалов: 0")
    edit_workshop(home, "pricing.yaml", "округление_цены: вверх до 1000", f"округление_цены: {rounding}")
    edit_workshop(home, "labor.yaml", "ставка_в_час: 1000", "ставка_в_час: 0")


def test_price_formula_with_default_pricing(small):
    result = api.estimate_from_order(ORDER, small)
    materials = Decimal(str(result.materials_cost))
    raw = materials * Decimal("1.13") + Decimal(2500) + Decimal("15796.06")
    expected = int((raw / 1000).to_integral_value(rounding=ROUND_CEILING)) * 1000
    assert result.price == expected
    assert result.price_before_rounding == pytest.approx(float(raw), abs=0.02)
    assert result.consumables == pytest.approx(float(materials * Decimal("0.03")), abs=0.01)
    assert result.markup == pytest.approx(float(materials * Decimal("0.10")), abs=0.01)
    assert result.delivery == 2500
    assert result.rounding == "вверх до 1000"


@pytest.mark.parametrize("markup,consumables", [("0.25", "0.07"), ("0.5", "0")])
def test_markup_and_consumables_follow_pricing_yaml_on_petrov(home, markup, consumables):
    petrov(home)
    edit_workshop(home, "pricing.yaml", "наценка_на_материалы: 0.10", f"наценка_на_материалы: {markup}")
    edit_workshop(
        home, "pricing.yaml", "расходники_от_материалов: 0.03", f"расходники_от_материалов: {consumables}"
    )
    r = api.estimate_from_order(ORDER, home)
    materials = Decimal(str(api.purchase_from_order(ORDER, home).total))
    assert r.markup == float((materials * Decimal(markup)).quantize(Decimal("0.01"), ROUND_HALF_UP))
    assert r.consumables == float((materials * Decimal(consumables)).quantize(Decimal("0.01"), ROUND_HALF_UP))
    raw = materials * (1 + Decimal(markup) + Decimal(consumables)) + 2500 + Decimal(str(r.labor.total))
    assert r.price == int((raw / 1000).to_integral_value(rounding=ROUND_CEILING)) * 1000


def with_delivery(home, amount):
    path = home / "workshop" / "pricing.yaml"
    text = re.sub(
        r"доставка_материалов: .*", f"доставка_материалов: {amount}", path.read_text(encoding="utf-8")
    )
    path.write_text(text, encoding="utf-8")


def test_price_rounds_up_but_exact_multiple_stays(small):
    quiet_pricing(small)
    materials = Decimal(str(api.estimate_from_order(ORDER, small).materials_cost))
    target = (materials / 1000).to_integral_value(rounding=ROUND_CEILING) * 1000 + 1000
    with_delivery(small, target - materials)
    assert api.estimate_from_order(ORDER, small).price == target  # ровно кратно: не растёт
    with_delivery(small, f"{target - materials + 1}")
    assert api.estimate_from_order(ORDER, small).price == target + 1000  # на рубль больше: растёт
    with_delivery(small, f"{target - materials - 1}")
    assert api.estimate_from_order(ORDER, small).price == target  # на рубль меньше: тот же кратный


def test_price_rounding_rule_comes_from_pricing_yaml(small):
    quiet_pricing(small, rounding="вниз до 500")
    materials = Decimal(str(api.estimate_from_order(ORDER, small).materials_cost))
    base = (materials / 500).to_integral_value(rounding=ROUND_CEILING) * 500 + 500
    with_delivery(small, base - materials + 499)
    result = api.estimate_from_order(ORDER, small)
    assert result.price == base and result.rounding == "вниз до 500"


def test_only_total_price_is_rounded(small):
    result = api.estimate_from_order(ORDER, small)
    assert result.price_before_rounding != result.price
    assert result.price_before_rounding * 100 == pytest.approx(round(result.price_before_rounding * 100))
    parts = (result.materials_cost, result.markup, result.consumables, result.delivery, result.labor.total)
    assert result.price_before_rounding == pytest.approx(sum(parts), abs=0.005)
    assert result.margin == pytest.approx(
        result.price - result.materials_cost - result.consumables - result.delivery - result.labor.total,
        abs=0.005,
    )


def test_mock_warning_in_json_and_markdown(small):
    result = api.estimate_and_save(ORDER, small)
    assert result.mock and result.warning == MOCK_BANNER
    data = json.loads(Path(result.outputs[1].file).read_text(encoding="utf-8"))
    assert data["warning"] == MOCK_BANNER
    assert MOCK_BANNER in Path(result.outputs[0].file).read_text(encoding="utf-8")


def test_no_mock_warning_when_all_files_are_real(small):
    unmock(small)
    result = api.estimate_and_save(ORDER, small)
    assert result.mock is False and result.warning is None
    data = json.loads(Path(result.outputs[1].file).read_text(encoding="utf-8"))
    assert data["warning"] is None
    assert MOCK_BANNER not in Path(result.outputs[0].file).read_text(encoding="utf-8")


def test_markdown_and_json_come_from_one_model(small):
    result = api.estimate_and_save(ORDER, small)
    outputs = {o.kind: o for o in result.outputs}
    assert set(outputs) == {"estimate_md", "estimate_json"}
    assert json.loads(Path(outputs["estimate_json"].file).read_text(encoding="utf-8")) == json.loads(
        result.model_dump_json()
    )
    text = Path(outputs["estimate_md"].file).read_text(encoding="utf-8")
    assert "версия проекта 1" in text
    for line in result.labor.lines:
        assert line.name in text
    assert f"{result.price}" in text and f"{result.labor.total:.2f}" in text
    assert {(o.kind, o.source_version) for o in api.list_outputs(ORDER, small)} == {
        ("estimate_md", 1),
        ("estimate_json", 1),
    }


def test_repeat_estimate_replaces_files_and_records(small):
    first = api.estimate_and_save(ORDER, small)
    edit_workshop(small, "labor.yaml", "распил_на_деталь: 4", "распил_на_деталь: 10")
    second = api.estimate_and_save(ORDER, small)
    assert second.labor.total != first.labor.total
    records = [o for o in api.list_outputs(ORDER, small) if o.kind.startswith("estimate")]
    assert len(records) == 2
    assert (
        json.loads(Path(second.outputs[1].file).read_text(encoding="utf-8"))["labor"]["total"]
        == second.labor.total
    )


def test_estimate_does_not_touch_other_outputs(small):
    api.purchase_and_save(ORDER, small)
    api.estimate_and_save(ORDER, small)
    assert {o.kind for o in api.list_outputs(ORDER, small)} == {
        "purchase_md",
        "purchase_json",
        "estimate_md",
        "estimate_json",
    }


def test_estimate_without_project_version_is_a_clear_error(home):
    api.order_new(ORDER, home=home)
    with pytest.raises(KitchenError, match="проекта ещё нет"):
        api.estimate_and_save(ORDER, home)


def test_estimate_from_project_file_matches_order_and_writes_nothing(small):
    result = api.estimate_from_project(small / "small.json", small)
    from_order = api.estimate_from_order(ORDER, small)
    assert result.source.kind == "file"
    assert result.labor == from_order.labor and result.price == from_order.price
    assert result.outputs == []
    assert not (small / "orders" / ORDER / "out").exists()
