"""Закупка и производство через публичный API и CLI."""

import json
import sqlite3
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import KitchenError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def test_purchase_order_items_packaging_and_shops(home):
    petrov(home)
    result = api.purchase_from_order(ORDER, home)
    assert result.source.version == 1 and result.mock
    shops = {shop.name: shop for shop in result.shops}
    items = {item.id: item for shop in result.shops for item in shop.items if item.kind != "facade"}
    assert items["ldsp_16_white"].quantity == 4
    assert items["hdf_3_white"].quantity == 2
    assert items["edge_08_white"].quantity == 50.9
    assert (items["confirmat_7x50"].needed, items["confirmat_7x50"].quantity) == (102, 2)
    assert items["confirmat_7x50"].amount == 900
    assert (items["hanger_camar807"].needed, items["hanger_camar807"].quantity) == (12, 12)
    assert items["led_profile_m"].needed == 4
    assert items["top_38_marble"].quantity == 1
    assert shops["ЛДСП-Центр"].total == 29010.8
    facades = [item for shop in result.shops for item in shop.items if item.kind == "facade"]
    assert {(i.id, i.color, i.quantity, i.lead_days) for i in facades} == {
        ("mdf_enamel_matt", "RAL 7044", 2.6292, 10),
        ("mdf_enamel_matt", "RAL 9003", 2.307, 10),
    }


def test_purchase_writes_matching_json_and_markdown(home):
    petrov(home)
    result = api.purchase_and_save(ORDER, home)
    outputs = {output.kind: output for output in result.outputs}
    assert set(outputs) == {"purchase_md", "purchase_json"}
    data = json.loads(Path(outputs["purchase_json"].file).read_text())
    assert data == json.loads(result.model_dump_json())
    text = Path(outputs["purchase_md"].file).read_text()
    assert "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" in text and "версия проекта 1" in text
    for shop in result.shops:
        assert shop.name in text
        for item in shop.items:
            assert item.id in text
    assert {(o.kind, o.source_version) for o in api.list_outputs(ORDER, home)} == {
        ("purchase_md", 1),
        ("purchase_json", 1),
    }


def test_production_writes_all_outputs_of_same_version(home):
    petrov(home)
    result = api.production_and_save(ORDER, home)
    outputs = api.list_outputs(ORDER, home)
    assert {(o.kind, o.source_version) for o in outputs} == {
        ("cutting", 1),
        ("edging", 1),
        ("purchase_md", 1),
        ("purchase_json", 1),
        ("assembly", 1),
        ("trip", 1),
    }
    assert all(Path(o.file).is_file() for o in outputs)
    assert (
        result.purchase.source.version == result.cutting.source.version == result.edging.source.version == 1
    )


def test_failed_production_commit_restores_all_files_and_records(home):
    petrov(home)
    api.production_and_save(ORDER, home)
    before = api.list_outputs(ORDER, home)
    content = {o.path: Path(o.file).read_bytes() for o in before}
    project = json.loads((FIXTURES / "petrov_project.json").read_text())
    project["ceiling_height"] = 2650
    path = home / "project-v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False))
    api.import_project(ORDER, path, home=home)
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        conn.executescript("""
            CREATE TABLE production_commit_failure (
                order_id INTEGER REFERENCES orders(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_purchase_commit AFTER INSERT ON outputs
            WHEN NEW.kind = 'purchase_json' BEGIN
                INSERT INTO production_commit_failure VALUES (-1);
            END;
        """)
    with pytest.raises(sqlite3.IntegrityError):
        api.production_and_save(ORDER, home)
    assert api.list_outputs(ORDER, home) == before
    assert {o.path: Path(o.file).read_bytes() for o in before} == content
    assert sorted(p.name for p in Path(before[0].file).parent.iterdir()) == [
        "assembly.pdf",
        "cutting-map.pdf",
        "edging-map.pdf",
        "purchase.json",
        "purchase.md",
        "trip-checklist.pdf",
    ]


def test_edge_minimum_is_rounded_up_to_the_workshop_step(home, base_door_project):
    materials = home / "workshop" / "materials.yaml"
    materials.write_text(materials.read_text().replace("продажа: метраж от 1 м", "продажа: метраж от 4.35 м"))
    project = json.loads(base_door_project.read_text())
    project["walls"][0]["length"] = 600
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, base_door_project, home=home)
    result = api.purchase_from_order(ORDER, home)
    edge = next(item for shop in result.shops for item in shop.items if item.id == "edge_08_white")
    assert edge.needed == 4.3 and edge.quantity == 4.4


def test_markdown_preserves_the_price_precision_in_json(home):
    petrov(home)
    materials = home / "workshop" / "materials.yaml"
    materials.write_text(materials.read_text().replace("цена_за_м: 12,", "цена_за_м: 12.345,"))
    result = api.purchase_and_save(ORDER, home)
    md = Path(next(o.file for o in result.outputs if o.kind == "purchase_md")).read_text()
    edge = next(item for shop in result.shops for item in shop.items if item.id == "edge_08_white")
    assert edge.unit_price == 12.345
    assert "50.9 м × 12.345 ₽" in md


def test_markdown_preserves_quantity_precision(home):
    petrov(home)
    materials = home / "workshop" / "materials.yaml"
    materials.write_text(
        materials.read_text().replace("продажа: метраж от 1 м", "продажа: метраж от 123.4567 м")
    )
    construction = home / "workshop" / "construction.yaml"
    construction.write_text(construction.read_text().replace("вверх до 0.1 м", "вверх до 0.0001 м"))
    result = api.purchase_and_save(ORDER, home)
    md = Path(next(o.file for o in result.outputs if o.kind == "purchase_md")).read_text()
    edge = next(item for shop in result.shops for item in shop.items if item.id == "edge_08_white")
    assert edge.quantity == 123.4567
    assert "123.4567 м ×" in md


def test_purchase_markdown_includes_newer_brief_warning(home):
    petrov(home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    result = api.purchase_and_save(ORDER, home)
    md = Path(next(o.file for o in result.outputs if o.kind == "purchase_md")).read_text()
    assert result.warnings and all(warning in md for warning in result.warnings)


@pytest.mark.parametrize("restore_error", [OSError("сбой"), KeyboardInterrupt("прерывание")])
def test_failed_restore_attempts_remaining_backups_and_reports_location(home, monkeypatch, restore_error):
    petrov(home)
    api.production_and_save(ORDER, home)
    before = api.list_outputs(ORDER, home)
    content = {o.path: Path(o.file).read_bytes() for o in before}
    project = json.loads((FIXTURES / "petrov_project.json").read_text())
    project["ceiling_height"] = 2650
    path = home / "project-v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False))
    api.import_project(ORDER, path, home=home)
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        conn.executescript("""
            CREATE TABLE production_commit_failure (
                order_id INTEGER REFERENCES orders(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_purchase_commit AFTER INSERT ON outputs
            WHEN NEW.kind = 'purchase_json' BEGIN
                INSERT INTO production_commit_failure VALUES (-1);
            END;
        """)
    original_replace = Path.replace

    def fail_restore(source, target):
        if source.name == "backup-1":
            raise restore_error
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_restore)
    with pytest.raises(KitchenError, match="Резервные копии") as failure:
        api.production_and_save(ORDER, home)
    assert api.list_outputs(ORDER, home) == before
    for output in before:
        if output.kind != "edging":
            assert Path(output.file).read_bytes() == content[output.path]
    backup = next(Path(before[0].file).parent.glob(".output-*/backup-1"))
    assert str(backup.parent) in str(failure.value)
    assert backup.read_bytes() == content[next(o.path for o in before if o.kind == "edging")]


def test_facades_with_same_color_but_different_materials_are_separate(home):
    materials = home / "workshop" / "materials.yaml"
    materials.write_text(
        materials.read_text()
        + '  - {id: other_mdf, название: "Другой МДФ", толщина: 19, цена_за_м2: 12000, '
        + "заказ_на_стороне: true, срок_раб_дней: 15, магазин: Фасад-Про}\n"
    )
    project = json.loads((FIXTURES / "petrov_project.json").read_text())
    module = next(
        m for wall in project["walls"] for row in wall["rows"] for m in row["modules"] if m["label"] == "Н2"
    )
    module["materials"] = {"facades": "other_mdf"}
    path = home / "project.json"
    path.write_text(json.dumps(project, ensure_ascii=False))
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, path, home=home)
    result = api.purchase_from_order(ORDER, home)
    facades = [item for shop in result.shops for item in shop.items if item.kind == "facade"]
    assert {(i.id, i.color, i.lead_days) for i in facades} == {
        ("mdf_enamel_matt", "RAL 7044", 10),
        ("mdf_enamel_matt", "RAL 9003", 10),
        ("other_mdf", "RAL 7044", 15),
    }


def test_common_and_module_hardware_combine_before_pack_rounding(home):
    construction = home / "workshop" / "construction.yaml"
    construction.write_text(
        construction.read_text().replace("позиция: rail_2000", "позиция: hanger_camar807")
    )
    petrov(home)
    result = api.purchase_from_order(ORDER, home)
    hangers = [item for shop in result.shops for item in shop.items if item.id == "hanger_camar807"]
    assert len(hangers) == 1 and hangers[0].needed == 14 and hangers[0].quantity == 14


def test_unrecognized_edge_sale_rule_is_reported(home):
    materials = home / "workshop" / "materials.yaml"
    materials.write_text(materials.read_text().replace("продажа: метраж от 1 м", "продажа: отрез по звонку"))
    petrov(home)
    with pytest.raises(KitchenError, match="непонятное условие продажи"):
        api.purchase_from_order(ORDER, home)
