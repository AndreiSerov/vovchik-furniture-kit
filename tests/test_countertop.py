"""Тикет 35: столешница считается общей длиной и целыми листами, без кусков."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import OrderError
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def base(label, width=600):
    return {"label": label, "template": "base_door", "width": width}


def tall(label):
    return {"label": label, "template": "tall_oven", "width": 600}


def project(home, walls, top="top_38_marble"):
    materials = {
        "body": "ldsp_16_white",
        "facades_lower": "mdf_enamel_matt",
        "facades_upper": "mdf_enamel_matt",
        "handles": "handle_profile",
    }
    if top:
        materials["countertop"] = top
    data = {"order": ORDER, "ceiling_height": 2700, "walls": walls, "materials": materials}
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def straight(total):
    """Нижний ряд из модулей по 600 и остатка; стены поровну, не длиннее 4000.

    Модули не уже 200 мм и нечётной ширины только с одной дверью (не шире 500): мелкий модуль
    даёт ошибку пересечения отверстий присадки (тикет 19), нечётный двухдверный — ошибку деления фасадов.
    """
    walls, n = [], 0
    count = -(-total // 4000)
    sizes = [total // count + (1 if i < total % count else 0) for i in range(count)]
    for part in sizes:
        modules = []
        while part > 0:
            if part > 900:
                width = 600
            elif part > 600:
                width = 400
            elif part > 500 and part % 2:
                width = part - 300
            else:
                width = part
            n += 1
            modules.append(base(f"Н{n}", width))
            part -= width
        walls.append(
            {
                "label": f"W{len(walls)}",
                "length": sum(m["width"] for m in modules),
                "rows": [{"kind": "lower", "modules": modules}],
            }
        )
    return walls


def top_of(home, walls):
    path = project(home, walls)
    top = next(c for c in api.parts_from_project(path, home).common if c.kind == "countertop")
    cut = api.cut_from_project(path, home).countertop
    assert (cut.total_length_mm, cut.sheet_count) == (top.length_mm, top.quantity)
    return top.length_mm, top.quantity


def test_petrov_countertop_and_totals_unchanged(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    cut = api.cut_and_save("petrov", home).countertop
    assert (cut.total_length_mm, cut.sheet_count) == (3400, 1)
    assert api.purchase_and_save("petrov", home).total == 158074.70
    assert api.estimate_and_save("petrov", home).price == 222000


@pytest.mark.parametrize(("total", "sheets"), [(4100, 1), (4101, 2), (8200, 2), (8201, 3), (9000, 3)])
def test_sheets_are_total_length_over_sheet_length_rounded_up(home, total, sheets):
    assert top_of(home, straight(total)) == (total, sheets)


def test_total_is_lower_modules_without_tall_and_corner_offset(home):
    # стена А: Н1 | П1 (пенал нижнего ряда) | Н4 угловой 1050; в верхнем ряду пенал П2 и угловой В4;
    # стена Б: Н5 | Н6. Под столешницей 600 + 1050 + 600 + 600: без пеналов и без смещения угла.
    def module(template, label, width, **kw):
        return {"label": label, "template": template, "width": width, **kw}

    corner = module("base_corner", "Н4", 1050, corner={"adjacent_wall": "Б"})
    upper = [
        module("wall_door", "В1", 600),
        tall("П2"),
        module("wall_corner", "В4", 600, corner={"adjacent_wall": "Б"}),
    ]
    walls = [
        {
            "label": "А",
            "length": 2250,
            "rows": [
                {"kind": "lower", "modules": [base("Н1"), tall("П1"), corner]},
                {"kind": "upper", "modules": upper},
            ],
        },
        {"label": "Б", "length": 1200, "rows": [{"kind": "lower", "modules": [base("Н5"), base("Н6")]}]},
    ]
    assert top_of(home, walls) == (2850, 1)


def test_without_countertop_there_is_none_anywhere(home):
    data = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    del data["materials"]["countertop"]
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert api.cut_from_project(path, home).countertop is None
    assert "countertop" not in [c.kind for c in api.parts_from_project(path, home).common]
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, path, home=home)
    kinds = {i.kind for shop in api.purchase_from_order("petrov", home).shops for i in shop.items}
    assert "countertop" not in kinds


def test_unknown_countertop_is_an_error(home):
    with pytest.raises(OrderError, match="top_nope"):
        api.cut_from_project(project(home, straight(1200), top="top_nope"), home)


def test_kerf_does_not_change_sheets(home):
    path = project(home, straight(4100))
    text = (home / "workshop/equipment.yaml").read_text()
    (home / "workshop/equipment.yaml").write_text(text.replace("пропил: 4", "пропил: 50"))
    assert api.cut_from_project(path, home).countertop.sheet_count == 1


def test_no_pieces_in_cut_purchase_and_estimate(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)

    def run(*args):
        r = runner.invoke(app, [*args, "petrov"])
        assert r.exit_code == 0, r.output
        return r.output

    cut, cut_json = run("cut"), run("cut", "--json")
    production_json = run("production", "--json")
    texts = [cut, cut_json, run("purchase"), run("estimate"), production_json]
    texts += [p.read_text(encoding="utf-8") for p in (home / "orders" / ORDER / "out").glob("*.md")]
    assert all("куск" not in text.lower() for text in texts)
    assert "общая длина 3400 мм" in cut and "листов 1" in cut
    assert "sheets" not in json.loads(cut_json)["countertop"]
    assert "sheets" not in json.loads(production_json)["cutting"]["countertop"]


def test_corner_in_one_row_only(home):
    corner = {"label": "Н4", "template": "base_corner", "width": 1050, "corner": {"adjacent_wall": "Б"}}
    walls = [
        {
            "label": "А",
            "length": 2250,
            "rows": [
                {"kind": "lower", "modules": [base("Н1"), tall("П1"), corner]},
                {"kind": "upper", "modules": [{"label": "В1", "template": "wall_door", "width": 600}]},
            ],
        },
        {"label": "Б", "length": 1200, "rows": [{"kind": "lower", "modules": [base("Н5"), base("Н6")]}]},
    ]
    assert top_of(home, walls) == (2850, 1)


def test_cut_gives_the_duplicate_label_finding_text(home):
    walls = [
        {"label": "A", "length": 1000, "rows": [{"kind": "lower", "modules": [base("Н1"), base("Н1", 400)]}]}
    ]
    path = project(home, walls)
    message = next(f.message for f in api.check_project(path, home).errors if f.code == "duplicate_label")
    with pytest.raises(OrderError) as e:
        api.cut_from_project(path, home)
    assert message in e.value.problems
    assert "Маркировка" not in str(e.value)
