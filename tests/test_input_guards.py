"""Тикет 33: неверный ввод мастерской, заказов и раскроя отклоняется русской ошибкой."""

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import KitchenError
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

from tests.conftest import BASE_DOOR_PROJECT

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FILES = (
    "equipment.yaml",
    "construction.yaml",
    "materials.yaml",
    "hardware.yaml",
    "labor.yaml",
    "pricing.yaml",
    "appliances.yaml",
    "economy.yaml",
)
ENGLISH = ("Input should", "greater_than", "finite_number", "Value error", "validation error")


def edit(home, name, old, new):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8")
    assert old in text, (name, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def project_file(tmp_path, name="p.json", **changes):
    project = json.loads(json.dumps(BASE_DOOR_PROJECT))
    project["walls"][0]["length"] = 600
    p = tmp_path / name
    p.write_text(json.dumps({**project, **changes}, ensure_ascii=False), encoding="utf-8")
    return p


def _walls():
    walls = json.loads(json.dumps(BASE_DOOR_PROJECT["walls"]))
    walls[0]["length"] = 600
    return walls


def errors_of(home):
    result = api.check_workshop(home)
    assert not result.valid
    return result.errors


# --- загрузка мастерской ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text", "key"),
    [
        ("hardware.yaml", "mock: true\nпозиции: 5\n", "позиции"),
        ("hardware.yaml", "mock: true\nпозиции: abc\n", "позиции"),
        ("labor.yaml", "mock: true\nставка_в_час: 1000\nнормы_минут: 5\n", "нормы_минут"),
    ],
)
def test_scalar_under_list_key_is_russian_error(home, name, text, key):
    (home / "workshop" / name).write_text(text, encoding="utf-8")
    assert any(name in e and key in e for e in errors_of(home))


def test_materials_scalar_list_key_is_russian_error(home):
    edit(home, "materials.yaml", "листовые:", "листовые: 7\nнелистовые:")
    assert any("materials.yaml" in e for e in errors_of(home))


def test_non_utf8_file_is_russian_error(home):
    p = home / "workshop" / "equipment.yaml"
    p.write_bytes(p.read_text(encoding="utf-8").encode("cp1251"))
    errors = errors_of(home)
    assert any("equipment.yaml" in e and "UTF-8" in e for e in errors)


@pytest.mark.parametrize("name", FILES)
def test_duplicate_yaml_key_in_every_file(home, name):
    p = home / "workshop" / name
    p.write_text(p.read_text(encoding="utf-8") + "mock: false\n", encoding="utf-8")
    errors = errors_of(home)
    assert any(name in e and "mock" in e and "повтор" in e for e in errors), errors


def test_duplicate_nested_yaml_key(home):
    edit(home, "labor.yaml", "  выезд: 90", "  выезд: 90\n  выезд: 1")
    assert any("labor.yaml" in e and "выезд" in e and "повтор" in e for e in errors_of(home))


@pytest.mark.parametrize("name", FILES)
def test_bom_in_yaml_is_accepted(home, name):
    """Регрессия: PyYAML сам снимает BOM; покрытием `utf-8-sig` этот тест не считается."""
    p = home / "workshop" / name
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
    assert api.check_workshop(home).valid


def test_duplicate_hardware_id(home):
    p = home / "workshop" / "hardware.yaml"
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join([*lines, lines[2].replace("цена: 520", "цена: 1")]) + "\n", encoding="utf-8")
    assert any("hardware.yaml" in e and "hinge_71B3550" in e and "повтор" in e for e in errors_of(home))


def test_duplicate_sheet_material_id(home):
    p = home / "workshop" / "materials.yaml"
    lines = p.read_text(encoding="utf-8").splitlines()
    first = next(i for i, line in enumerate(lines) if "id: ldsp_16_white" in line)
    lines.insert(first + 1, lines[first].replace("цена: 4200", "цена: 1"))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert any("materials.yaml" in e and "ldsp_16_white" in e and "повтор" in e for e in errors_of(home))


def test_duplicate_appliance_id(home):
    p = home / "workshop" / "appliances.yaml"
    lines = p.read_text(encoding="utf-8").splitlines()
    first = next(i for i, line in enumerate(lines) if "id: oven_60" in line)
    p.write_text("\n".join([*lines, lines[first]]) + "\n", encoding="utf-8")
    assert any("appliances.yaml" in e and "oven_60" in e and "повтор" in e for e in errors_of(home))


# --- диапазоны чисел ---------------------------------------------------------------------------

RANGES = [
    ("labor.yaml", "ставка_в_час: 1000", "ставка_в_час: -1000", "ставка_в_час"),
    ("labor.yaml", "ставка_в_час: 1000", "ставка_в_час: .inf", "ставка_в_час"),
    ("labor.yaml", "ставка_в_час: 1000", "ставка_в_час: .nan", "ставка_в_час"),
    ("labor.yaml", "  распил_на_деталь: 4", "  распил_на_деталь: -5", "распил_на_деталь"),
    ("pricing.yaml", "наценка_на_материалы: 0.10", "наценка_на_материалы: -3", "наценка_на_материалы"),
    ("pricing.yaml", "доставка_материалов: 2500", "доставка_материалов: -1", "доставка_материалов"),
    ("pricing.yaml", "расходники_от_материалов: 0.03", "расходники_от_материалов: .inf", "расходники"),
    ("pricing.yaml", "предоплата: 0.5", "предоплата: -0.5", "предоплата"),
    ("pricing.yaml", "[85000, 110000]", "[110000, -5]", "быстрая_оценка_за_пог_м"),
    ("pricing.yaml", "[85000, 110000]", "[110000, 85000]", "быстрая_оценка_за_пог_м"),
    ("pricing.yaml", "[85000, 110000]", "[0, 110000]", "быстрая_оценка_за_пог_м"),
    ("hardware.yaml", "упаковка: 100, цена: 450", "упаковка: 0, цена: 450", "упаковка"),
    ("hardware.yaml", "цена: 520", "цена: -1", "цена"),
    ("materials.yaml", "цена: 4200", "цена: -4200", "цена"),
    ("materials.yaml", "цена: 4200", "цена: .inf", "цена"),
    ("construction.yaml", "высота: 300, шаг: 300", "высота: 300, шаг: 0", "шаг"),
]


@pytest.mark.parametrize(("name", "old", "new", "key"), RANGES)
def test_workshop_number_out_of_range_is_load_error(home, name, old, new, key):
    edit(home, name, old, new)
    errors = [e for e in errors_of(home) if name in e and key in e]
    assert errors, key
    for e in errors:
        assert not any(w in e for w in ENGLISH), e


def test_quick_estimate_is_not_computed_with_bad_range(home):
    edit(home, "pricing.yaml", "[85000, 110000]", "[110000, -5]")
    r = runner.invoke(app, ["quick-estimate", "--meters", "3"])
    assert r.exit_code == 1 and "быстрая_оценка_за_пог_м" in r.output


def test_negative_rate_is_a_workshop_error_for_estimate(home):
    edit(home, "labor.yaml", "ставка_в_час: 1000", "ставка_в_час: -1000")
    with pytest.raises(KitchenError):
        api.estimate_from_project(FIXTURES / "petrov_project.json", home)


# --- пропил и целые поля пилы и листа -------------------------------------------------------------


def gaps(result):
    out = []
    for m in result.materials:
        for s in m.sheets:
            for i, a in enumerate(s.placements):
                for b in s.placements[i + 1 :]:
                    out.append(max(b.x - a.x - a.w, a.x - b.x - b.w, b.y - a.y - a.h, a.y - b.y - b.h))
    return out


@pytest.mark.parametrize(("kerf", "used"), [("4.5", 5), ("3.2", 4)])
def test_fractional_kerf_rounds_up_with_warning(home, kerf, used):
    edit(home, "equipment.yaml", "пропил: 4", f"пропил: {kerf}")
    result = api.cut_from_project(FIXTURES / "petrov_project.json", home)
    assert result.kerf == used
    assert min(gaps(result)) >= used
    assert any(kerf in w and f"{used} мм" in w for w in result.warnings), result.warnings
    text = runner.invoke(app, ["cut", "--project", str(FIXTURES / "petrov_project.json")])
    assert text.exit_code == 0 and f"пропил {kerf} мм учтён как {used} мм" in text.output
    as_json = json.loads(
        runner.invoke(app, ["cut", "--project", str(FIXTURES / "petrov_project.json"), "--json"]).output
    )
    assert any(f"пропил {kerf} мм учтён как {used} мм" in w for w in as_json["warnings"])


def test_integer_kerf_has_no_warning(home):
    result = api.cut_from_project(FIXTURES / "petrov_project.json", home)
    assert result.kerf == 4 and not any("пропил" in w.lower() for w in result.warnings)
    edit(home, "equipment.yaml", "пропил: 4", "пропил: 4.0")
    again = api.cut_from_project(FIXTURES / "petrov_project.json", home)
    assert again.kerf == 4 and not any("пропил" in w.lower() for w in again.warnings)


def test_kerf_warning_is_in_cutting_map_pdf(home):
    from pypdf import PdfReader

    edit(home, "equipment.yaml", "пропил: 4", "пропил: 3.2")
    api.brief_save("2026-047-petrov", FIXTURES / "petrov_brief.json")
    api.import_project("petrov", FIXTURES / "petrov_project.json")
    result = api.cut_and_save("petrov")
    text = PdfReader(result.output.file).pages[0].extract_text()
    assert "3.2" in text


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("обрезка_по_периметру: 10", "обрезка_по_периметру: 12.5", "обрезка_по_периметру"),
        ("макс_длина_реза: 3200", "макс_длина_реза: 3200.5", "макс_длина_реза"),
    ],
)
def test_fractional_saw_and_trim_are_load_errors(home, old, new, key):
    edit(home, "equipment.yaml", old, new)
    assert any("equipment.yaml" in e and key in e and "целое" in e for e in errors_of(home))


@pytest.mark.parametrize("sheet", ["[2800.6, 2070]", "[2800, 2070.4]"])
def test_fractional_sheet_is_load_error(home, sheet):
    edit(
        home,
        "materials.yaml",
        "лист: [2800, 2070], текстура: false, толщина: 16",
        f"лист: {sheet}, текстура: false, толщина: 16",
    )
    assert any("materials.yaml" in e and "целое" in e for e in errors_of(home))


def test_negative_kerf_is_load_error(home):
    edit(home, "equipment.yaml", "пропил: 4", "пропил: -4")
    assert any("equipment.yaml" in e and "пропил" in e for e in errors_of(home))


def test_no_round_over_workshop_fields_in_cutting_code():
    """В `cutting.py` и `common.py` нет `round`/`int` над полями мастерской: они целые в модели.

    `common.py:round(m.width)` над полем проекта — тикет 34."""
    import ast

    engine = Path(api.__file__).parent
    for name in ("cutting.py", "common.py"):
        for call in ast.walk(ast.parse((engine / name).read_text(encoding="utf-8"))):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in ("round", "int")
            ):
                fields = {n.attr for n in ast.walk(call) if isinstance(n, ast.Attribute)}
                assert not fields & {"kerf", "perimeter_trim", "max_cut_length", "sheet", "equipment"}, (
                    name,
                    ast.unparse(call),
                )


# --- версия проекта ---------------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["2026-047", "2026-047-ПЕТРОВ", "2026-047-petrov"])
def test_order_field_spelling_does_not_make_a_version(home, tmp_path, spelling):
    api.order_new("2026-047-petrov")
    assert api.import_project("petrov", project_file(tmp_path, order="2026-047-petrov")).version == 1
    again = api.import_project("petrov", project_file(tmp_path, "q.json", order=spelling))
    assert again.changed is False and again.version == 1


def test_integer_written_as_float_does_not_make_a_version(home, tmp_path):
    api.order_new("2026-047-petrov")
    api.import_project("petrov", project_file(tmp_path))
    walls = _walls()
    walls[0]["rows"][0]["modules"][0]["width"] = 600.0
    again = api.import_project("petrov", project_file(tmp_path, "q.json", walls=walls))
    assert again.changed is False and again.version == 1


def test_real_change_makes_version_two(home, tmp_path):
    api.order_new("2026-047-petrov")
    api.import_project("petrov", project_file(tmp_path))
    again = api.import_project("petrov", project_file(tmp_path, "q.json", ceiling_height=2500))
    assert again.changed is True and again.version == 2


def test_version_written_before_hash_fix_is_not_duplicated(home, tmp_path):
    """В БД лежит версия со старым хешем (sha256 сырого JSON, другое написание order): новой версии нет."""
    path = project_file(tmp_path, order="2026-047")
    old_text = json.dumps(
        {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k != "version"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    api.order_new("2026-047-petrov")
    api.import_project("petrov", path)  # заводим БД и версию 1, затем подменяем её на «старую»
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        conn.execute(
            "UPDATE project_versions SET data = ?, content_hash = ?",
            (old_text, hashlib.sha256(old_text.encode("utf-8")).hexdigest()),
        )
    same = api.import_project("petrov", path)
    assert same.changed is False and same.version == 1
    spelled = api.import_project("petrov", project_file(tmp_path, "q.json", order="2026-047-petrov"))
    assert spelled.changed is False and spelled.version == 1
    changed = api.import_project("petrov", project_file(tmp_path, "r.json", ceiling_height=2500))
    assert changed.changed is True and changed.version == 2
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        assert conn.execute("SELECT count(*) FROM project_versions").fetchone()[0] == 2


def test_file_and_order_source_hashes_agree(home, tmp_path):
    path = project_file(tmp_path)
    api.order_new("2026-047-petrov")
    api.import_project("petrov", path)
    file_hash = api.parts_from_project(path, home).source.content_hash
    assert api.parts_from_order("petrov", home).source.content_hash == file_hash
    spelled = project_file(tmp_path, "q.json", order="2026-047")
    assert api.parts_from_project(spelled, home).source.content_hash == file_hash


# --- BOM в JSON ---------------------------------------------------------------------------------------


def test_project_json_with_bom_is_accepted(home, tmp_path):
    api.order_new("2026-047-petrov")
    p = project_file(tmp_path, "bom.json")
    p.write_text(p.read_text(encoding="utf-8"), encoding="utf-8-sig")
    assert api.import_project("petrov", p).version == 1


def test_brief_json_with_bom_is_accepted(home, tmp_path):
    p = tmp_path / "brief.json"
    p.write_text((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"), encoding="utf-8-sig")
    assert api.brief_save("2026-047-petrov", p).created is True


# --- запись файлов -----------------------------------------------------------------------------------


def imported(home, tmp_path):
    api.order_new("2026-047-petrov")
    api.import_project("petrov", project_file(tmp_path))


def test_export_to_directory_is_russian_error(home, tmp_path):
    imported(home, tmp_path)
    with pytest.raises(KitchenError) as e:
        api.export_project("petrov", out=tmp_path)
    assert str(tmp_path) in str(e.value)


def test_export_to_unreachable_path_is_russian_error(home, tmp_path):
    imported(home, tmp_path)
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(KitchenError):
        api.export_project("petrov", out=blocker / "sub" / "p.json")


def test_status_file_write_error_is_russian_error(home, tmp_path):
    imported(home, tmp_path)
    (home / "orders" / "2026-047-petrov" / "status.md").mkdir(parents=True)
    with pytest.raises(KitchenError) as e:
        api.status("petrov")
    assert "status.md" in str(e.value)


def test_status_file_carries_mock_banner(home):
    api.order_new("2026-047-petrov")
    r = api.status("petrov")
    lines = Path(r.path).read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == lines[-1] == MOCK_BANNER


# --- контракт CLI: испорченный ввод, код 1, без трейсбека, плашка мока ---------------------------------------


# --- повторы: что именно повторилось --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("section", "id_", "key"),
    [
        ("кромка:", "edge_08_white", "кромка"),
        ("столешницы:", "top_38_marble", "столешницы"),
        ("фасады:", "mdf_enamel_matt", "фасады"),
        ("листовые:", "ldsp_16_white", "листовые"),
    ],
)
def test_duplicate_material_ids_name_the_yaml_key(home, section, id_, key):
    p = home / "workshop" / "materials.yaml"
    lines = p.read_text(encoding="utf-8").splitlines()
    first = next(i for i, line in enumerate(lines) if f"id: {id_}," in line)
    lines.insert(first + 1, lines[first])
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert any("materials.yaml" in e and f"«{key}»" in e and id_ in e for e in errors_of(home)), errors_of(
        home
    )


def test_duplicate_hardware_and_appliance_ids_name_the_yaml_key(home):
    for name, id_ in (("hardware.yaml", "hinge_71B3550"), ("appliances.yaml", "oven_60")):
        p = home / "workshop" / name
        lines = p.read_text(encoding="utf-8").splitlines()
        first = next(i for i, line in enumerate(lines) if f"id: {id_}," in line)
        p.write_text("\n".join([*lines, lines[first]]) + "\n", encoding="utf-8")
    errors = errors_of(home)
    assert any("hardware.yaml" in e and "«позиции»" in e for e in errors)
    assert any("appliances.yaml" in e and "«позиции»" in e for e in errors)


# --- YAML: нехешируемые ключи и merge-ключи ---------------------------------------------------------------------


@pytest.mark.parametrize("line", ["[1, 2]: 3\n", "? {a: 1}\n: 3\n"])
def test_unhashable_yaml_key_is_russian_error(home, line):
    p = home / "workshop" / "labor.yaml"
    p.write_text(p.read_text(encoding="utf-8") + line, encoding="utf-8")
    assert any("labor.yaml" in e for e in errors_of(home))
    cli = runner.invoke(app, ["check-workshop"])
    assert cli.exit_code == 1 and isinstance(cli.exception, SystemExit)


@pytest.mark.parametrize("extra", ["", ", цена: 2"])
def test_yaml_merge_key_is_not_a_syntax_error(home, extra):
    p = home / "workshop" / "hardware.yaml"
    item = "{<<: {id: zzz, название: x, упаковка: 1, цена: 1, магазин: m}" + extra + "}"
    p.write_text(p.read_text(encoding="utf-8") + f"  - {item}\n", encoding="utf-8")
    assert api.check_workshop(home).valid, api.check_workshop(home).errors


# --- границы чисел, найденные ревью ---------------------------------------------------------------------------------


@pytest.mark.parametrize("step", ["0", "-1"])
def test_back_panel_fastening_step_must_be_positive(home, step):
    path = home / "workshop" / "construction.yaml"
    text = path.read_text(encoding="utf-8")
    changed = re.sub(r"крепёж_шаг: \d+", f"крепёж_шаг: {step}", text)
    assert changed != text
    path.write_text(changed, encoding="utf-8")
    assert any("construction.yaml" in e and "крепёж_шаг" in e for e in errors_of(home))
    r = runner.invoke(app, ["parts", "--project", str(FIXTURES / "petrov_project.json")])
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit), repr(r.exception)


def test_negative_edge_allowance_is_load_error(home):
    edit(home, "equipment.yaml", "припуск_на_сторону: 30", "припуск_на_сторону: -100000")
    assert any("припуск_на_сторону" in e for e in errors_of(home))


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("полкодержателей: 4", "полкодержателей: -1", "полкодержателей"),
        ("навесов: 2", "навесов: -1", "навесов"),
        ("на_петлю: 4", "на_петлю: -1", "на_петлю"),
        ("до_800: 4", "до_800: -1", "до_800"),
        ("[900, 2]", "[900, -2]", "петли_по_высоте_фасада"),
        ("царга_ширина: 100", "царга_ширина: 0", "царга_ширина"),
        ("толщина_корпуса: 16", "толщина_корпуса: 0", "толщина_корпуса"),
    ],
)
def test_negative_hardware_norm_is_load_error(home, old, new, key):
    edit(home, "construction.yaml", old, new)
    assert any(key in e for e in errors_of(home))


@pytest.mark.parametrize(
    "key",
    list(
        LABOR_NORMS := [
            "распил_на_деталь",
            "кромка_на_метр",
            "присадка_на_деталь",
            "сборка_корпуса",
            "сборка_доп_на_ящик",
            "монтаж_на_модуль",
            "врезка_мойки",
            "врезка_варочной",
            "выезд",
            "замер",
            "проектирование",
        ]
    ),
)
def test_each_of_eleven_labor_norms_rejects_negative(home, key):
    path = home / "workshop" / "labor.yaml"
    text = path.read_text(encoding="utf-8")
    changed = re.sub(rf"(  {key}: )\d+", r"\g<1>-5", text)
    assert changed != text
    path.write_text(changed, encoding="utf-8")
    assert any("labor.yaml" in e and key in e for e in errors_of(home))


@pytest.mark.parametrize(
    ("name", "old", "new", "key"),
    [
        ("materials.yaml", "цена_за_м: 12", "цена_за_м: -12", "цена_за_м"),
        ("pricing.yaml", "расходники_от_материалов: 0.03", "расходники_от_материалов: -0.03", "расходники"),
        ("equipment.yaml", "обрезка_по_периметру: 10", "обрезка_по_периметру: -1", "обрезка_по_периметру"),
        ("equipment.yaml", "макс_длина_реза: 3200", "макс_длина_реза: 0", "макс_длина_реза"),
        ("materials.yaml", "лист: [4100, 600]", "лист: [4100.5, 600]", "лист"),
    ],
)
def test_more_workshop_ranges(home, name, old, new, key):
    edit(home, name, old, new)
    assert any(name in e and key in e for e in errors_of(home))


# --- выводы заказа ---------------------------------------------------------------------------------------------------


def test_out_dir_is_a_file_is_russian_error(home):
    api.brief_save("2026-047-petrov", FIXTURES / "petrov_brief.json")
    api.import_project("petrov", FIXTURES / "petrov_project.json")
    out = home / "orders" / "2026-047-petrov" / "out"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("x", encoding="utf-8")
    r = runner.invoke(app, ["cut", "petrov"])
    assert r.exit_code == 1 and isinstance(r.exception, SystemExit), repr(r.exception)
    assert "out" in r.output


# --- мок: нечитаемый файл считается моком ---------------------------------------------------------------------------


def test_unreadable_file_counts_as_mock_even_when_the_rest_is_not(home):
    """Осторожное правило (DECISIONS, «Мастерская»): нечитаемый файл — мок, пока его не починят."""
    for f in (home / "workshop").glob("*.yaml"):
        f.write_text(f.read_text(encoding="utf-8").replace("mock: true", "mock: false", 1), encoding="utf-8")
    assert api.check_workshop(home).mock is False
    p = home / "workshop" / "labor.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "ставка_в_час: 5\n", encoding="utf-8")
    r = api.check_workshop(home)
    assert not r.valid and r.mock is True and r.mock_files == ["labor.yaml"]


# --- пропил: цифры и места -----------------------------------------------------------------------------------------


def test_kerf_warning_shows_the_declared_value(home):
    edit(home, "equipment.yaml", "пропил: 4", "пропил: 4.0000001")
    r = api.cut_from_project(FIXTURES / "petrov_project.json", home)
    assert any("пропил 4.0000001 мм учтён как 5 мм" in w for w in r.warnings), r.warnings


def test_fractional_kerf_in_countertop(home):
    """Куски 2200 + 1200 с пропилом 2.2 (учтён как 3) не влезают в лист 3402: нужен второй."""
    edit(home, "materials.yaml", "лист: [4100, 600]", "лист: [3402, 600]")
    petrov = FIXTURES / "petrov_project.json"
    edit(home, "equipment.yaml", "пропил: 4", "пропил: 2")
    assert api.cut_from_project(petrov, home).countertop.sheet_count == 1
    edit(home, "equipment.yaml", "пропил: 2", "пропил: 2.2")
    assert api.cut_from_project(petrov, home).countertop.sheet_count == 2


def test_kerf_warning_in_purchase_estimate_production_and_map(home):
    from pypdf import PdfReader

    edit(home, "equipment.yaml", "пропил: 4", "пропил: 3.2")
    api.brief_save("2026-047-petrov", FIXTURES / "petrov_brief.json")
    api.import_project("petrov", FIXTURES / "petrov_project.json")
    needle = "пропил 3.2 мм учтён как 4 мм"
    assert any(needle in w for w in api.purchase_from_order("petrov", home).warnings)
    assert any(needle in w for w in api.estimate_from_order("petrov", home).warnings)
    assert any(
        needle in w for w in api.estimate_from_project(FIXTURES / "petrov_project.json", home).warnings
    )
    production = api.production_and_save("petrov", home)
    assert any(needle in w for w in production.purchase.warnings)
    assert any(needle in w for w in production.cutting.warnings)
    pdf = home / "orders" / "2026-047-petrov" / "out" / "cutting-map.pdf"
    assert "3.2" in PdfReader(pdf).pages[0].extract_text()


# --- хеш версии: старые версии и пустое наполнение ---------------------------------------------------------------------


def test_source_hash_of_order_with_legacy_version_matches_file(home, tmp_path):
    path = project_file(tmp_path, order="2026-047")
    api.order_new("2026-047-petrov")
    api.import_project("petrov", path)
    old_text = json.dumps(
        {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k != "version"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        conn.execute(
            "UPDATE project_versions SET data = ?, content_hash = ?",
            (old_text, hashlib.sha256(old_text.encode("utf-8")).hexdigest()),
        )
    from_order = api.parts_from_order("petrov", home).source.content_hash
    assert from_order == api.parts_from_project(path, home).source.content_hash


def test_empty_contents_is_the_same_project_as_no_contents(home, tmp_path):
    api.order_new("2026-047-petrov")
    api.import_project("petrov", project_file(tmp_path))
    walls = _walls()
    walls[0]["rows"][0]["modules"][0]["contents"] = {}
    again = api.import_project("petrov", project_file(tmp_path, "q.json", walls=walls))
    assert again.changed is False and again.version == 1


def test_json_not_in_utf8_is_russian_error(home, tmp_path):
    api.order_new("2026-047-petrov")
    p = project_file(tmp_path)
    p.write_bytes(p.read_text(encoding="utf-8").encode("cp1251", errors="replace"))
    with pytest.raises(KitchenError) as e:
        api.import_project("petrov", p)
    assert "UTF-8" in str(e.value)


# --- контракт CLI на всех испорченных мастерских ------------------------------------------------------------------------


def _read(home, name):
    return (home / "workshop" / name).read_text(encoding="utf-8")


def _swap(name, old, new):
    return lambda home: (home / "workshop" / name).write_text(
        _read(home, name).replace(old, new), encoding="utf-8"
    )


def _append(name, text):
    return lambda home: (home / "workshop" / name).write_text(_read(home, name) + text, encoding="utf-8")


def _cp1251(name):
    return lambda home: (home / "workshop" / name).write_bytes(_read(home, name).encode("cp1251"))


def _repeat_line(name, marker):
    def spoil(home):
        lines = _read(home, name).splitlines()
        index = next(i for i, line in enumerate(lines) if marker in line)
        lines.insert(index + 1, lines[index])
        (home / "workshop" / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    return spoil


BROKEN = {
    "повторный id фурнитуры": _repeat_line("hardware.yaml", "id: hinge_71B3550"),
    "повторный id листового материала": _repeat_line("materials.yaml", "id: ldsp_16_white"),
    "повторный id прибора": _repeat_line("appliances.yaml", "id: oven_60"),
    **{f"{n}:{old}->{new}": _swap(n, old, new) for n, old, new, _ in RANGES},
    "скаляр под списком": lambda h: (h / "workshop" / "hardware.yaml").write_text(
        "mock: true\nпозиции: 5\n", encoding="utf-8"
    ),
    "не UTF-8": _cp1251("equipment.yaml"),
    "нехешируемый ключ": _append("labor.yaml", "[1, 2]: 3\n"),
    **{f"повторный ключ {n}": _append(n, "mock: false\n") for n in FILES},
    "обрезка 12.5": _swap("equipment.yaml", "обрезка_по_периметру: 10", "обрезка_по_периметру: 12.5"),
    "рез 3200.5": _swap("equipment.yaml", "макс_длина_реза: 3200", "макс_длина_реза: 3200.5"),
    "лист 2800.6": _swap(
        "materials.yaml",
        "лист: [2800, 2070], текстура: false, толщина: 16",
        "лист: [2800.6, 2070], текстура: false, толщина: 16",
    ),
    "шаг крепежа 0": lambda h: (h / "workshop" / "construction.yaml").write_text(
        re.sub(r"крепёж_шаг: \d+", "крепёж_шаг: 0", _read(h, "construction.yaml")), encoding="utf-8"
    ),
}


@pytest.mark.parametrize("case", list(BROKEN))
def test_cli_on_broken_workshop_exits_1_with_banner_and_no_traceback(home, tmp_path, case):
    imported(home, tmp_path)
    BROKEN[case](home)
    petrov = str(FIXTURES / "petrov_project.json")
    for args in (
        ["check-workshop"],
        ["cut", "--project", petrov],
        ["parts", "--project", petrov],
        ["quick-estimate", "--meters", "3"],
    ):
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and isinstance(r.exception, SystemExit), (args, r.output, repr(r.exception))
        assert "Traceback" not in r.output and MOCK_BANNER in r.output, (args, r.output)
    st = runner.invoke(app, ["status", "petrov"])  # команды заказа работают и при сломанной мастерской
    assert st.exit_code == 0 and "Traceback" not in st.output and MOCK_BANNER in st.output


def test_cli_on_bad_order_input_exits_1_with_banner(home, tmp_path):
    imported(home, tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    for args in (
        ["export", "petrov", "--out", str(tmp_path)],
        ["export", "petrov", "--out", str(blocker / "a" / "p.json")],
        ["import", "petrov", str(project_file(tmp_path, "w.json", order="2026-999"))],
    ):
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and isinstance(r.exception, SystemExit), (args, r.output)
        assert "Traceback" not in r.output and MOCK_BANNER in r.output, (args, r.output)
