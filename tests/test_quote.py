"""Тикет 18: КП — PDF клиенту: состав, итог, предоплата, срок; без себестоимости и наценки."""

import copy
import json
import re
import shutil
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
    quote = api.quote_from_project(PETROV, 35, tmp_path)
    assert quote.price == 222000
    assert quote.prepayment == 66600 and quote.balance == 155400


def test_prepayment_is_whole_rubles_and_balance_is_the_rest(tmp_path):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    edit(tmp_path, "pricing.yaml", "предоплата: 0.5", "предоплата: 0.3333")
    quote = api.quote_from_project(PETROV, 35, tmp_path)
    assert quote.prepayment == 73993  # 222000 × 0.3333 = 73992.6, до рубля вверх от половины
    assert quote.balance == 222000 - 73993


def test_prepayment_above_one_is_a_load_error(home):
    edit(home, "pricing.yaml", "предоплата: 0.5", "предоплата: 1.5")
    checked = api.check_workshop(home)
    assert not checked.valid and any("pricing.yaml" in e and "предоплата" in e for e in checked.errors)


# --- срок --------------------------------------------------------------------


@pytest.mark.parametrize("days", [None, 0, -1, 2.5, True, "35"])
def test_bad_days_is_a_russian_error(home, days):
    with pytest.raises(InputError) as e:
        api.quote_from_project(PETROV, days, home)
    assert "--days" in "".join(e.value.problems) and "рабочих дней" in "".join(e.value.problems)


@pytest.mark.parametrize(
    "args", [[], ["--days", "0"], ["--days", "-1"], ["--days", "2.5"], ["--days", "abc"]]
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
        f"Предоплата (50%): {money(quote.prepayment)} ₽",
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
    assert "духовка" in by_label["П1"] and "СВЧ" in by_label["П1"]
    assert "Н1 — нижний модуль с тремя ящиками, 600 мм" in text
    assert "ЛДСП Egger 16 мм, белый W1000 ST9" in text  # корпус
    assert "RAL 7044" in text and "RAL 9003" in text  # цвета фасадов
    assert "Столешница Egger 38 мм, мрамор Леванто" in text


def test_no_hardware_brands_in_composition(petrov):
    _, _, text = petrov
    for brand in ("Blum", "Tandembox", "Aventos", "Hettich", "петл", "направляющ"):
        assert brand.lower() not in text.lower(), brand


def test_small_project_composition_is_exact(tmp_path):
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    quote = api.quote_from_project(write_project(tmp_path, SMALL), 20, tmp_path)
    assert [(m.label, m.text) for m in quote.modules] == [
        ("Н1", "нижний модуль с тремя ящиками, 600 мм"),
        ("Н2", "модуль под мойку с двумя дверями, 750 мм; техника: мойка"),
        ("В1", "верхний модуль-подъёмник с одной дверью и одной полкой, 600 мм"),
        ("В2", "верхний модуль с двумя дверями и одной полкой, 750 мм"),
    ]


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
        assert line.client_text in text
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
