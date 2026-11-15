"""Тикет 18: КП — PDF клиенту: состав, итог, предоплата, срок; без себестоимости и наценки."""

import copy
import json
import re
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import typer
from kitchen import api
from kitchen.cli import app
from kitchen.errors import InputError, OrderError
from kitchen.result import MOCK_BANNER
from pypdf import PdfReader
from typer.testing import CliRunner

from tests.conftest import BASE_DOOR_PROJECT, REPO

FIXTURES = REPO / "tests" / "fixtures"
PETROV = FIXTURES / "petrov_project.json"
BRIEF = FIXTURES / "petrov_brief.json"
ORDER = "2026-047-petrov"
SMALL_ORDER = "2026-051-sidorov"
SMALL_PRICE = 96000  # цена малого проекта на мок-мастерской (тест сверяет её со сметой)
runner = CliRunner()

# Малый проект: ящики, мойка с ведром, верхний подъёмник. Метки и ширины заданы вручную.
SMALL = {
    **copy.deepcopy(BASE_DOOR_PROJECT),
    "order": SMALL_ORDER,
    "walls": [
        {
            "label": "A",
            "length": 1350,
            "rows": [
                {
                    "kind": "lower",
                    "modules": [
                        {"label": "Н1", "template": "base_drawers", "width": 600},
                        {
                            "label": "Н2",
                            "template": "base_sink",
                            "width": 750,
                            "contents": {"inserts": ["bin_2sec"]},
                        },
                    ],
                },
                {
                    "kind": "upper",
                    "modules": [
                        {"label": "В1", "template": "wall_lift", "width": 600},
                        {"label": "В2", "template": "wall_door", "width": 750, "shelves": 1},
                    ],
                },
            ],
        }
    ],
    "appliances": [{"type": "sink", "width": 750, "module": "Н2"}],
}


def pdf_text(path) -> str:
    return " ".join(" ".join(p.extract_text().split()) for p in PdfReader(path).pages)


def workshop_home(tmp_path_factory, name):
    home = tmp_path_factory.mktemp(name)
    shutil.copytree(REPO / "workshop", home / "workshop")
    return home


def write_project(home, project, name="project.json"):
    path = home / name
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return path


def import_order(home, project=SMALL):
    api.order_new(project["order"], client="Сидоров С. С.", home=home)
    api.import_project(project["order"], write_project(home, project, "import.json"), home=home)


def edit(home, name, old, new):
    path = home / "workshop" / name
    text = path.read_text(encoding="utf-8")
    assert old in text, (name, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


def versions_of(home):
    """Текущая версия проекта и раздел «Версия проекта» из status.md: по ним видно, что версия не добавилась."""
    status = api.status(SMALL_ORDER, home)
    section = re.search(r"^## Версия проекта\n(.*?)(?=^## |\Z)", status.text, re.DOTALL | re.MULTILINE)
    assert section, status.text
    return status.project_version, section.group(1)


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ")


@pytest.fixture(scope="module")
def petrov(tmp_path_factory):
    """КП Петрова считается один раз на модуль, в своей мастерской: не зависит от orders/ репозитория."""
    home = workshop_home(tmp_path_factory, "quote-petrov")
    api.brief_save(ORDER, BRIEF, home)
    api.import_project(ORDER, PETROV, home=home)
    result = api.quote_and_save(ORDER, 35, home)
    return result, api.estimate_from_order(ORDER, home), pdf_text(result.outputs[0].file)


@pytest.fixture(scope="module")
def small_economy(tmp_path_factory):
    """КП малого проекта с вариантом «экономия»: заказ в БД, сравнение со сметой того же снимка."""
    home = workshop_home(tmp_path_factory, "quote-small-economy")
    import_order(home)
    before = versions_of(home)
    result = api.quote_and_save(SMALL_ORDER, 20, home, economy=True)
    after = versions_of(home)
    estimate = api.estimate_from_order(SMALL_ORDER, home, economy=True)
    return home, result, estimate, before, after


# --- итог, предоплата, остаток -------------------------------------------------


def test_total_equals_estimate_price_and_prepayment_follows_formula(petrov):
    quote, estimate, _ = petrov
    assert quote.price == estimate.price == 222000
    assert quote.prepayment_share == 0.5
    assert quote.prepayment == 111000 and quote.balance == 111000
    assert quote.prepayment + quote.balance == quote.price


def test_prepayment_override_changes_both_figures(tmp_path):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    edit(tmp_path, "pricing.yaml", "предоплата: 0.5", "предоплата: 0.3")
    quote = api.quote_from_project(write_project(tmp_path, SMALL), 35, tmp_path)
    assert quote.price == SMALL_PRICE
    assert quote.prepayment == SMALL_PRICE * 3 // 10 and quote.balance == SMALL_PRICE - SMALL_PRICE * 3 // 10


def test_prepayment_is_whole_rubles_and_balance_is_the_rest(tmp_path):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    edit(tmp_path, "pricing.yaml", "предоплата: 0.5", "предоплата: 0.3333")
    quote = api.quote_from_project(write_project(tmp_path, SMALL), 35, tmp_path)
    expected = int(Decimal(SMALL_PRICE) * Decimal("0.3333") + Decimal("0.5"))  # до рубля, от половины вверх
    assert quote.prepayment == expected and quote.balance == SMALL_PRICE - expected


def test_prepayment_above_one_is_a_load_error(home):
    edit(home, "pricing.yaml", "предоплата: 0.5", "предоплата: 1.5")
    checked = api.check_workshop(home)
    assert not checked.valid and any("pricing.yaml" in e and "предоплата" in e for e in checked.errors)


# --- срок --------------------------------------------------------------------


@pytest.mark.parametrize("days", [None, 0, -1, 366, 2.5, True, "35"])
def test_bad_days_is_a_russian_error(home, days):
    with pytest.raises(InputError) as e:
        api.quote_from_project(PETROV, days, home)
    assert "--days" in "".join(e.value.problems) and "рабочих дней" in "".join(e.value.problems)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--days"],
        ["--days="],
        ["--days=", "35"],
        ["--days", "0"],
        ["--days", "-1"],
        ["--days", "2.5"],
        ["--days", "abc"],
        ["--days", "366"],
    ],
)
def test_cli_bad_days_is_a_russian_error_with_exit_1(home, args):
    result = runner.invoke(app, ["quote", "--project", str(PETROV), *args])
    assert result.exit_code == 1, result.output
    assert "--days" in result.output and "рабочих дней" in result.output
    assert "Traceback" not in result.output and "Usage" not in result.output


def test_days_value_is_written_into_pdf(petrov):
    quote, _, text = petrov
    assert quote.days == 35
    assert "Срок изготовления и монтажа: 35 рабочих дней" in text


# --- содержимое PDF -----------------------------------------------------------


def test_pdf_has_total_prepayment_balance_days_and_master_details(petrov):
    quote, _, text = petrov
    for expected in (
        f"Итого: {money(quote.price)} ₽",
        f"Предоплата (50 %): {money(quote.prepayment)} ₽",
        f"Остаток: {money(quote.balance)} ₽",
        "Мастеров Мастер Мастерович",
        "+7 (000) 000-00-00",
        "Город Н",
        "самозанятый",
        "ИНН 000000000000",
        "Предложение действует 14 дней",
    ):
        assert expected in text, expected


def test_pdf_lists_every_petrov_module_in_wall_and_row_order(petrov):
    quote, _, text = petrov
    labels = [m.label for m in quote.modules]
    assert labels == ["П1", "Н1", "Н2", "Н3", "Н4", "В1", "В2", "В3", "В4", "Н5", "Н6", "В5", "В6"]
    positions = [text.index(f"{label} — ") for label in labels]
    assert positions == sorted(positions)


FORBIDDEN_WORDS = ("себестоимость", "наценк", "маржа", "труд", "расходник")


def amounts_of_estimate(estimate) -> set[int]:
    """Суммы, которые клиент видеть не должен: материалы, наценка, расходники, доставка, труд и его строки."""
    values = [
        estimate.materials_cost,
        estimate.markup,
        estimate.consumables,
        estimate.delivery,
        estimate.labor.total,
        estimate.price_before_rounding,
        estimate.margin,
        *(line.amount for line in estimate.labor.lines),
    ]
    return {round(v) for v in values if round(v) >= 100}


def numbers_in(text: str) -> set[int]:
    """Числа текста PDF: цифры с пробелом-разделителем тысяч склеены, дробная часть отброшена."""
    return {
        int(n.replace(" ", ""))
        for n in re.findall(r"(?<!\w)\d(?:[\d ]*\d)?(?!\w)", text)
        if n.replace(" ", "")
    }


def test_pdf_and_json_hold_no_cost_words_or_cost_amounts(petrov):
    quote, estimate, text = petrov
    lowered = text.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in lowered, word
    shown = numbers_in(text)
    hidden = amounts_of_estimate(estimate)
    assert hidden, "в смете должны быть суммы, которых нет в КП"
    assert not (shown & (hidden - {quote.price, quote.prepayment, quote.balance})), shown & hidden
    data = json.loads(quote.model_dump_json())
    data.pop("mock_files")  # имена файлов мастерской (labor.yaml) — не суммы и не слова КП
    as_json = json.dumps(data, ensure_ascii=False).lower()
    for word in (*FORBIDDEN_WORDS, "materials_cost", "markup", "labor", "consumables", "margin", "delivery"):
        assert word not in as_json, word


def test_composition_words_for_petrov(petrov):
    quote, _, text = petrov
    by_label = {m.label: m.text for m in quote.modules}
    assert by_label["Н1"].startswith("нижний модуль с тремя ящиками, 600 мм")
    assert "духовой шкаф" in by_label["П1"] and "микроволновка" in by_label["П1"]
    assert "Н1 — нижний модуль с тремя ящиками, 600 мм" in text
    assert "ЛДСП Egger 16 мм, белый W1000 ST9" in text  # корпус
    assert "RAL 7044" in text and "RAL 9003" in text  # цвета фасадов
    assert "Столешница Egger 38 мм, мрамор Леванто" in text


def test_no_hardware_brands_in_composition(petrov):
    _, _, text = petrov
    for brand in ("Blum", "Tandembox", "Aventos", "Hettich", "петл", "направляющ"):
        assert brand.lower() not in text.lower(), brand


def test_small_project_composition_is_exact_and_lift_front_is_not_a_door(tmp_path):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    quote = api.quote_from_project(write_project(tmp_path, SMALL), 20, tmp_path)
    assert [(m.label, m.text) for m in quote.modules] == [
        ("Н1", "нижний модуль с тремя ящиками, 600 мм"),
        ("Н2", "модуль под мойку с двумя дверями и выдвижным ведром, 750 мм; техника: мойка"),
        ("В1", "верхний модуль с одной полкой и подъёмным фасадом, 600 мм"),
        ("В2", "верхний модуль с двумя дверями и одной полкой, 750 мм"),
    ]
    assert "дверью" not in {m.label: m.text for m in quote.modules}["В1"]


# --- мок ---------------------------------------------------------------------


def test_mock_quote_has_banner_in_pdf_json_and_notice(petrov):
    quote, _, text = petrov
    assert quote.mock and quote.warning == MOCK_BANNER and "master.yaml" in quote.mock_files
    assert text.count(MOCK_BANNER) >= 2  # водяной знак и колонтитул
    assert json.loads(quote.model_dump_json())["warning"] == MOCK_BANNER


def test_no_banner_when_all_nine_files_are_real(home):
    for path in (home / "workshop").glob("*.yaml"):
        path.write_text(
            path.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8"
        )
    import_order(home)
    quote = api.quote_and_save(SMALL_ORDER, 20, home)
    assert not quote.mock and quote.warning is None
    assert MOCK_BANNER not in pdf_text(quote.outputs[0].file)
    assert MOCK_BANNER not in Path(quote.outputs[1].file).read_text(encoding="utf-8")


def test_master_file_alone_keeps_the_banner(home):
    for name in (
        "equipment",
        "construction",
        "materials",
        "hardware",
        "labor",
        "pricing",
        "appliances",
        "economy",
    ):
        edit(home, f"{name}.yaml", "mock: true", "mock: false")
    import_order(home)
    quote = api.quote_and_save(SMALL_ORDER, 20, home)
    assert quote.mock_files == ["master.yaml"] and quote.warning == MOCK_BANNER
    assert MOCK_BANNER in pdf_text(quote.outputs[0].file)


# --- вариант «экономия» -------------------------------------------------------


def test_economy_has_both_prices_and_the_same_saving_as_estimate(small_economy):
    _, quote, estimate, _, _ = small_economy
    assert estimate.price == SMALL_PRICE
    assert estimate.economy and estimate.economy.replacements, "на малом проекте должны быть замены"
    assert quote.price == estimate.price
    assert quote.economy.price_as_is == estimate.economy.price_as_is == estimate.price
    assert quote.economy.price == estimate.economy.price
    assert quote.economy.saving == estimate.economy.saving > 0
    assert [(r.client_text, r.modules) for r in quote.economy.replacements] == [
        (r.client_text, r.modules) for r in estimate.economy.replacements
    ]
    assert "name" not in json.loads(quote.model_dump_json())["economy"]["replacements"][0]


def test_economy_pdf_shows_prices_texts_and_no_line_sums(small_economy):
    _, quote, estimate, _, _ = small_economy
    text = pdf_text(quote.outputs[0].file)
    assert f"Цена как в проекте: {money(quote.economy.price_as_is)} ₽" in text
    assert f"Цена «экономии»: {money(quote.economy.price)} ₽" in text
    assert f"Разница: {money(quote.economy.saving)} ₽" in text
    for line in estimate.economy.replacements:
        assert line.client_text.rstrip(".") in text
        assert line.price not in numbers_in(text) - {quote.economy.price, quote.price}
        if line.saving != quote.economy.saving:
            assert line.saving not in numbers_in(text) - {quote.economy.saving}
    for word in (*FORBIDDEN_WORDS, "tandembox", "aventos"):
        assert word not in text.lower()


def test_economy_does_not_change_project_versions(small_economy):
    _, _, _, before, after = small_economy
    assert before == after and before[0] == 1


def test_quote_without_economy_has_no_economy_section(tmp_path):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    quote = api.quote_from_project(write_project(tmp_path, SMALL), 20, tmp_path)
    assert quote.economy is None and "economy" not in json.loads(quote.model_dump_json())


# --- вывод: файлы, реестр, устаревание -----------------------------------------


def test_outputs_are_registered_replaced_and_marked_stale(home):
    import_order(home)
    first = api.quote_and_save(SMALL_ORDER, 20, home)
    pdf, js = (Path(o.file) for o in first.outputs)
    assert pdf.name == "quote.pdf" and js.name == "quote.json" and pdf.parent.name == "out"
    assert json.loads(js.read_text(encoding="utf-8"))["price"] == first.price
    api.quote_and_save(SMALL_ORDER, 45, home)
    assert "45 рабочих дней" in pdf_text(pdf) and json.loads(js.read_text(encoding="utf-8"))["days"] == 45
    kinds = [o.kind for o in api.list_outputs(SMALL_ORDER, home)]
    assert sorted(kinds) == ["quote_json", "quote_pdf"]  # повтор заменил записи, а не добавил
    changed = {**SMALL, "ceiling_height": 2650}
    api.import_project(SMALL_ORDER, write_project(home, changed, "v2.json"), home=home)
    stale = {s.kind: s.command for s in api.status(SMALL_ORDER, home).stale}
    assert set(stale) == {"quote_pdf", "quote_json"}
    assert all(c == f"kitchen quote {SMALL_ORDER} --days <рабочих дней>" for c in stale.values())


def test_quote_of_order_without_project_version_is_a_clear_error(home):
    api.order_new(SMALL_ORDER, client="Сидоров", home=home)
    with pytest.raises(OrderError) as e:
        api.quote_and_save(SMALL_ORDER, 20, home)
    assert "проекта ещё нет" in "".join(e.value.problems)


def test_project_file_writes_nothing(tmp_path):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    api.quote_from_project(write_project(tmp_path, SMALL), 20, tmp_path)
    assert not (tmp_path / "orders").exists()


# --- CLI ---------------------------------------------------------------------


def test_cli_json_is_the_api_model_and_text_has_the_total(home):
    import_order(home)
    result = runner.invoke(app, ["quote", "sidorov", "--days", "20", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    expected = api.quote_from_order(SMALL_ORDER, 20, home)
    assert data == json.loads(expected.model_copy(update={"outputs": []}).model_dump_json()) | {
        "outputs": data["outputs"]
    }
    assert [o["kind"] for o in data["outputs"]] == ["quote_pdf", "quote_json"]
    text = runner.invoke(app, ["quote", "sidorov", "--days", "20"])
    assert text.exit_code == 0 and f"{money(expected.price)} ₽" in text.output


def test_cli_order_and_project_together_is_an_error(home):
    result = runner.invoke(app, ["quote", "sidorov", "--project", str(PETROV), "--days", "20"])
    assert result.exit_code == 1 and "либо заказ" in result.output


def test_every_output_kind_still_has_a_cli_command():
    commands = set(typer.main.get_command(app).commands)
    assert {r.command for r in api.OUTPUT_RECOMPUTE.values()} <= commands


def test_master_yaml_is_the_ninth_mock_file(home):
    workshop = api.load_workshop(home)
    assert "master.yaml" in workshop.mock_files and len(workshop.mock_files) == 9
    master = workshop.master
    assert (master.status, master.offer_days) == ("самозанятый", 14)
    summary = {f.file: f for f in api.check_workshop(home).files}
    assert summary["master.yaml"].mock and summary["master.yaml"].counts == {"ключей": 6}


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("статус: самозанятый", "статус: корпорация", "статус"),
        ('инн: "000000000000"', 'инн: "123"', "инн"),
        ('инн: "000000000000"', "инн: 123456789012", "инн"),
        (
            "срок_действия_предложения_дней: 14",
            "срок_действия_предложения_дней: 0",
            "срок_действия_предложения_дней",
        ),
        ('имя: "Мастеров Мастер Мастерович"', 'имя: ""', "имя"),
    ],
)
def test_master_yaml_bad_values_are_russian_load_errors(home, old, new, key):
    edit(home, "master.yaml", old, new)
    errors = api.check_workshop(home).errors
    assert any("master.yaml" in e and key in e for e in errors), errors
    assert not any("Input should" in e or "validation error" in e for e in errors), errors


# --- ревью: склонение, проценты, состав словами, материалы по модулям ------------------------------


def small_quote(tmp_path, project=SMALL, days=20, **edits):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    for name, (old, new) in edits.items():
        edit(tmp_path, name, old, new)
    return api.quote_from_project(write_project(tmp_path, project), days, tmp_path)


@pytest.mark.parametrize(
    ("count", "words"),
    [
        (1, "1 рабочий день"),
        (2, "2 рабочих дня"),
        (5, "5 рабочих дней"),
        (11, "11 рабочих дней"),
        (21, "21 рабочий день"),
        (22, "22 рабочих дня"),
        (112, "112 рабочих дней"),
    ],
)
def test_days_are_declined(count, words):
    assert api.plural(count, "рабочий день", "рабочих дня", "рабочих дней") == words


def test_days_and_offer_days_are_declined_in_pdf(home):
    edit(home, "master.yaml", "срок_действия_предложения_дней: 14", "срок_действия_предложения_дней: 21")
    import_order(home)
    quote = api.quote_and_save(SMALL_ORDER, 21, home)
    text = pdf_text(quote.outputs[0].file)
    assert "Срок изготовления и монтажа: 21 рабочий день" in text
    assert "Предложение действует 21 день" in text


@pytest.mark.parametrize(
    ("share", "words"), [(0.5, "50 %"), (0.3333, "33,33 %"), (0.3, "30 %"), (1, "100 %"), (0, "0 %")]
)
def test_prepayment_percent_is_plain_with_comma(share, words):
    assert api.percent(share) == words


def test_master_details_come_from_master_yaml(home):
    edit(home, "master.yaml", "Мастеров Мастер Мастерович", "Другой Мастер Иванович")
    edit(home, "master.yaml", "+7 (000) 000-00-00", "+7 (111) 111-11-11")
    edit(home, "master.yaml", "Город Н", "Город Эн")
    edit(home, "master.yaml", "самозанятый", "ИП")
    edit(home, "master.yaml", "000000000000", "123456789012")
    edit(home, "master.yaml", "срок_действия_предложения_дней: 14", "срок_действия_предложения_дней: 30")
    import_order(home)
    text = pdf_text(api.quote_and_save(SMALL_ORDER, 20, home).outputs[0].file)
    for expected in (
        "Другой Мастер Иванович",
        "+7 (111) 111-11-11",
        "Город Эн",
        "ИП, ИНН 123456789012",
        "Предложение действует 30 дней",
    ):
        assert expected in text, expected
    assert "Мастеров" not in text


@pytest.mark.parametrize("key", ["имя", "телефон", "город"])
def test_master_text_of_spaces_is_a_load_error(home, key):
    path = home / "workshop" / "master.yaml"
    lines = [
        f'{key}: "   "' if line.startswith(f"{key}:") else line
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert any("master.yaml" in e and key in e for e in api.check_workshop(home).errors)


def test_petrov_lines_name_carousel_bucket_dryer_and_lift_in_words(petrov):
    quote, _, _ = petrov
    by_label = {m.label: m.text for m in quote.modules}
    assert by_label["Н4"] == "нижний угловой модуль с одной дверью и поворотной каруселью, 1050 мм"
    assert by_label["Н5"] == "модуль под мойку с двумя дверями и выдвижным ведром, 750 мм; техника: мойка"
    assert by_label["В1"] == "модуль над вытяжкой с подъёмным фасадом, 600 мм; техника: вытяжка"
    assert by_label["В5"] == "верхний модуль с подъёмным фасадом и сушкой для посуды, 750 мм"
    assert by_label["П1"].endswith("техника: духовой шкаф, микроволновка")
    assert by_label["Н6"].endswith("техника: посудомойка")


def test_client_names_live_in_hardware_yaml(tmp_path):
    changed = small_quote(
        tmp_path,
        petrov_like := copy.deepcopy(SMALL),
        **{"hardware.yaml": ('для_клиента: "выдвижным ведром"', 'для_клиента: "ведром-вертушкой"')},
    )
    assert petrov_like
    assert "с двумя дверями и ведром-вертушкой" in {m.label: m.text for m in changed.modules}["Н2"]


def with_materials(**overrides):
    project = copy.deepcopy(SMALL)
    project["materials"] = {
        **project["materials"],
        "facades_lower_color": "RAL 7044",
        "facades_upper_color": "RAL 9003",
        "countertop": "top_38_marble",
    }
    for path, value in overrides.items():
        target = project["walls"][0]["rows"][{"row_lower": 0, "row_upper": 1}[path.split(":")[0]]]
        if ":" in path and path.split(":")[1] != "row":
            target = target["modules"][int(path.split(":")[1])]
        target["materials"] = value
    return project


def test_row_facade_colour_override_reaches_quote(tmp_path):
    quote = small_quote(tmp_path, with_materials(**{"row_lower:row": {"facade_color": "RAL 3020"}}))
    assert quote.facades_lower == "МДФ 19 мм эмаль матовая, RAL 3020"
    assert quote.facades_upper == "МДФ 19 мм эмаль матовая, RAL 9003"


def test_module_override_lists_all_values_with_labels(tmp_path):
    quote = small_quote(tmp_path, with_materials(**{"row_lower:1": {"facade_color": "RAL 3020"}}))
    assert (
        quote.facades_lower
        == "МДФ 19 мм эмаль матовая, RAL 7044 (Н1), МДФ 19 мм эмаль матовая, RAL 3020 (Н2)"
    )


def test_body_override_lists_values_with_labels_and_pdf_prints_them(home):
    import_order(home, with_materials(**{"row_lower:0": {"body": "ldsp_16_oak"}}))
    quote = api.quote_and_save(SMALL_ORDER, 20, home)
    assert quote.body.startswith("ЛДСП Egger 16 мм, дуб Галифакс H1180 (Н1), ЛДСП Egger 16 мм, белый")
    assert "Корпус: " + quote.body in pdf_text(quote.outputs[0].file)


def test_label_runs_are_joined_into_a_range(tmp_path):
    project = with_materials()
    project["walls"][0]["rows"] = project["walls"][0]["rows"][:1]
    project["walls"][0]["length"] = 1800
    project["appliances"] = []
    project["walls"][0]["rows"][0]["modules"] = [
        {"label": f"Н{n}", "template": "base_door", "width": 450, "shelves": 1} for n in range(1, 5)
    ]
    project["walls"][0]["rows"][0]["modules"][3]["materials"] = {"body": "ldsp_16_oak"}
    white, oak = "ЛДСП Egger 16 мм, белый W1000 ST9", "ЛДСП Egger 16 мм, дуб Галифакс H1180"
    assert small_quote(tmp_path, project).body == f"{white} (Н1–Н3), {oak} (Н4)"


def test_group_without_modules_is_not_printed(home):
    project = with_materials()
    project["walls"][0]["rows"] = project["walls"][0]["rows"][:1]
    import_order(home, project)
    quote = api.quote_and_save(SMALL_ORDER, 20, home)
    text = pdf_text(quote.outputs[0].file)
    assert quote.facades_upper is None and "Фасады верхние" not in text and "Фасады нижние" in text


def test_countertop_word_is_not_repeated(petrov):
    _, _, text = petrov
    assert "Столешница Egger 38 мм, мрамор Леванто" in text and "Столешница: Столешница" not in text


def test_economy_without_replacements_says_so(tmp_path):
    project = copy.deepcopy(BASE_DOOR_PROJECT)
    project["order"] = SMALL_ORDER
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    quote = api.quote_from_project(write_project(tmp_path, project), 20, tmp_path, economy=True)
    assert quote.economy.replacements == [] and quote.economy.price == quote.price


def test_replacement_line_ends_with_modules_in_brackets_and_a_full_stop(small_economy):
    _, quote, _, _, _ = small_economy
    text = pdf_text(quote.outputs[0].file)
    for r in quote.economy.replacements:
        word = "модуль" if len(r.modules) == 1 else "модули"
        assert f"({word} {', '.join(r.modules)})." in text
