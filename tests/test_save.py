"""Тикет 71: `api.save`, таблица сохранения и `api.production` (спека этапа 9, «Сохранение»)."""

import ast
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from kitchen import api
from kitchen.errors import OrderError
from pypdf import PdfReader

from tests.test_calculation import counter

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
ORDER = "2026-047-petrov"


def petrov_order(home):
    """«Заказ Петрова»: бриф и проект в tmp home."""
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def petrov_file(home):
    """«Файл Петрова»: копия проекта в tmp home."""
    path = home / "petrov_project.json"
    shutil.copy(FIXTURES / "petrov_project.json", path)
    return path


def import_v2(home):
    """«v2»: тот же проект с `ceiling_height: 2650` в тот же заказ."""
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    path = home / "project-v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert api.import_project(ORDER, path, home=home).version == 2


def out_files(home):
    folder = home / "orders" / ORDER / "out"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


# --- 71.3: что возвращает `save` и когда он отказывает --------------------------------------------


def test_save_returns_a_tuple_of_copies_with_the_output_filled(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    cut = calc.cut()
    saved = api.save(calc, cut)
    assert isinstance(saved, tuple) and len(saved) == 1
    assert saved[0].output.path == f"orders/{ORDER}/out/cutting-map.pdf"
    assert Path(saved[0].output.file).is_file()
    assert cut.output is None  # результат из кэша расчёта не правится на месте


def done_texts(home):
    db = sqlite3.connect(home / "orders" / "kitchen.db")
    return [r[0] for r in db.execute("SELECT text FROM log WHERE kind = 'done' ORDER BY id")]


def output_rows(home):
    db = sqlite3.connect(home / "orders" / "kitchen.db")
    return db.execute("SELECT count(*) FROM outputs").fetchone()[0]


def log_rows(home):
    db = sqlite3.connect(home / "orders" / "kitchen.db")
    return db.execute("SELECT count(*) FROM log").fetchone()[0]


def refusal(call):
    with pytest.raises(OrderError) as e:
        call()
    return e.value.problems


FILE_REFUSAL = ["Расчёт из файла (--project) не сохраняется: файлы пишет только заказ."]


# --- 71.2: расчёт из файла не сохраняется ---------------------------------------------------------


def test_a_file_calculation_is_not_saved(home):
    petrov_order(home)
    calc = api.calculation(project=petrov_file(home), home=home)
    assert refusal(lambda: api.save(calc, calc.cut())) == FILE_REFUSAL
    assert out_files(home) == []
    assert api.list_outputs(ORDER, home) == []


# --- 71.3: отказы `save` и их порядок -------------------------------------------------------------


def test_save_refuses_nothing_foreign_rowless_and_repeated_results(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    import_v2(home)
    other = api.calculation(order=ORDER, home=home)
    nothing = refusal(lambda: api.save(calc))
    foreign = refusal(lambda: api.save(calc, other.cut()))
    rowless = refusal(lambda: api.save(calc, calc.parts()))
    repeated = refusal(lambda: api.save(calc, calc.cut(), calc.cut()))
    assert len({tuple(p) for p in (nothing, foreign, rowless, repeated)}) == 4
    assert out_files(home) == []
    assert api.list_outputs(ORDER, home) == []
    for result in (calc.check(), calc.placement(), calc.economy_parts()):
        assert refusal(lambda result=result: api.save(calc, result)) == rowless_text(result)
    # несколько ошибок сразу: срабатывает первая по порядку спеки
    file_calc = api.calculation(project=petrov_file(home), home=home)
    assert refusal(lambda: api.save(file_calc)) == FILE_REFUSAL
    assert refusal(lambda: api.save(calc, calc.parts(), calc.parts())) == rowless
    assert refusal(lambda: api.save(calc, calc.cut(), other.cut())) == foreign
    assert refusal(lambda: api.save(calc, calc.cut(), calc.cut(), calc.parts())) == rowless


def rowless_text(result):
    return [f"Вывод {type(result).__name__} не сохраняется: файлов у него нет."]


def test_save_refusal_texts(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    assert refusal(lambda: api.save(calc)) == ["Нечего сохранять: передайте хотя бы один вывод расчёта."]
    assert refusal(lambda: api.save(calc, calc.parts())) == rowless_text(calc.parts())
    assert refusal(lambda: api.save(calc, calc.cut(), calc.cut())) == [
        "Вид вывода «cutting» повторяется в одном сохранении."
    ]
    import_v2(home)
    other = api.calculation(order=ORDER, home=home)
    assert refusal(lambda: api.save(calc, other.cut())) == [
        "Вывод посчитан другим расчётом: сохраняются только выводы этого расчёта."
    ]


def test_save_returns_copies_in_argument_order_and_writes_rows_in_that_order(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    trip, cut = api.save(calc, calc.trip(), calc.cut())
    assert (trip.output.kind, cut.output.kind) == ("trip", "cutting")
    assert [o.kind for o in api.list_outputs(ORDER, home)] == ["trip", "cutting"]


# --- 71.4, 71.5: откат ---------------------------------------------------------------------------


def fail_commits(home, kind=None):
    """Триггер `AFTER INSERT ON outputs`: отложенный внешний ключ роняет commit после записи строки.
    `kind` — только после строки этого вида: остальные строки того же `save` попали бы в БД, если бы
    `save` писал не одной транзакцией."""
    when = f"WHEN NEW.kind = '{kind}'" if kind else ""
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        conn.executescript(f"""
            CREATE TABLE output_commit_failure (
                order_id INTEGER REFERENCES orders(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_output_commit AFTER INSERT ON outputs {when} BEGIN
                INSERT INTO output_commit_failure VALUES (-1);
            END;
        """)


def file_bytes(outputs):
    return {o.path: Path(o.file).read_bytes() for o in outputs}


@pytest.mark.parametrize("kind", [None, "trip"])
def test_failed_save_keeps_the_v1_files_rows_and_log(home, kind):
    petrov_order(home)
    v1 = api.calculation(order=ORDER, home=home)
    api.save(v1, v1.cut(), v1.trip())
    before = api.list_outputs(ORDER, home)
    content = file_bytes(before)
    import_v2(home)
    rows, entries = output_rows(home), log_rows(home)
    v2 = api.calculation(order=ORDER, home=home)
    fail_commits(home, kind)
    with pytest.raises(sqlite3.IntegrityError):
        api.save(v2, v2.cut(), v2.trip())
    assert file_bytes(before) == content
    assert (output_rows(home), log_rows(home)) == (rows, entries)
    assert out_files(home) == ["cutting-map.pdf", "trip-checklist.pdf"]


def test_failed_production_keeps_the_v1_documents_and_writes_no_entry(home):
    petrov_order(home)
    v1 = api.calculation(order=ORDER, home=home)
    api.production(v1)
    before = api.list_outputs(ORDER, home)
    assert [o.kind for o in before] == list(api.PRODUCTION_KINDS)
    assert done_texts(home)[-1:] == ["документы цеха посчитаны из версии 1"]
    content = file_bytes(before)
    import_v2(home)
    done = done_texts(home)
    v2 = api.calculation(order=ORDER, home=home)
    fail_commits(home)
    with pytest.raises(sqlite3.IntegrityError):
        api.production(v2)
    assert file_bytes(before) == content and len(content) == 6
    assert done_texts(home) == done
    assert "документы цеха посчитаны из версии 2" not in done_texts(home)


def test_production_writes_one_done_entry(home):
    petrov_order(home)
    done = done_texts(home)
    api.production(api.calculation(order=ORDER, home=home))
    assert done_texts(home) == [*done, "документы цеха посчитаны из версии 1"]


# --- 71.6: записи «сделано» -----------------------------------------------------------------------


ESTIMATE_DONE = "смета посчитана из версии 1"
QUOTE_DONE = "КП посчитано из версии 1, срок 20 рабочих дней"


def test_estimate_and_quote_write_their_done_entries(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    api.save(calc, calc.estimate())
    assert done_texts(home)[-1] == ESTIMATE_DONE
    api.save(calc, calc.quote(20))
    assert done_texts(home)[-1] == QUOTE_DONE


def test_estimate_and_quote_together_write_both_entries_in_argument_order(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    done = done_texts(home)
    api.save(calc, calc.estimate(), calc.quote(20))
    assert done_texts(home) == [*done, ESTIMATE_DONE, QUOTE_DONE]


@pytest.mark.parametrize("kind", [None, "quote_json"])
def test_failed_save_of_estimate_and_quote_writes_no_entry(home, kind):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    done = done_texts(home)
    fail_commits(home, kind)
    with pytest.raises(sqlite3.IntegrityError):
        api.save(calc, calc.estimate(), calc.quote(20))
    assert done_texts(home) == done


# --- 71.7: файлы из результатов -------------------------------------------------------------------


def test_written_files_come_from_the_saved_results(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    purchase, quote, views = api.save(calc, calc.purchase(), calc.quote(20), calc.views())
    files = {o.kind: Path(o.file) for o in purchase.outputs + quote.outputs}
    assert purchase.outputs and json.loads(files["purchase_json"].read_text(encoding="utf-8")) == json.loads(
        purchase.model_dump_json()
    )
    written = json.loads(files["quote_json"].read_text(encoding="utf-8"))
    assert "warnings" not in written and written["outputs"] == json.loads(quote.model_dump_json())["outputs"]
    assert (views.output.kind, Path(views.output.file).suffix) == ("views", ".pdf")
    assert (views.output_html.kind, Path(views.output_html.file).suffix) == ("views_html", ".html")
    assert Path(views.output.file).read_bytes().startswith(b"%PDF")
    assert "<html" in Path(views.output_html.file).read_text(encoding="utf-8").lower()


# --- 71.9: версия сменилась между расчётом и записью ----------------------------------------------


def test_save_after_a_new_version_is_refused(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    import_v2(home)
    assert refusal(lambda: api.save(calc, calc.cut())) == [
        "Изменилась версия проекта во время расчёта. Повторите команду."
    ]
    assert out_files(home) == []


# --- 71.10, 71.11: счётчики и виды документов цеха ------------------------------------------------


def test_production_loads_the_workshop_and_computes_parts_once(home, monkeypatch):
    petrov_order(home)
    loads = counter(monkeypatch, "kitchen.calculation.load_workshop")
    computes = counter(monkeypatch, "kitchen.calculation.compute_parts")
    api.production(api.calculation(order=ORDER, home=home))
    assert (len(loads), len(computes)) == (1, 1)


def test_production_kinds_keep_their_order():
    assert api.PRODUCTION_KINDS == ("cutting", "edging", "purchase_md", "purchase_json", "assembly", "trip")


# --- 71.15: имена файлов живут только в таблице сохранения ----------------------------------------


@pytest.mark.parametrize(
    "name", ["CUTTING_PDF", "DRILLING_PDF", "ASSEMBLY_PDF", "TRIP_PDF", "VIEWS_PDF", "VIEWS_HTML"]
)
def test_file_name_constants_are_gone_from_api(name):
    assert not hasattr(api, name)


# --- 71.8: имена файлов и писатели раскроя и кромки — только в таблице сохранения -----------------

ENGINE = REPO / "engine" / "kitchen"
# Имена файлов из таблицы сохранения спеки этапа 9.
FILE_NAMES = {
    "cutting-map.pdf",
    "edging-map.pdf",
    "drilling-map.pdf",
    "assembly.pdf",
    "trip-checklist.pdf",
    "purchase.md",
    "purchase.json",
    "estimate.md",
    "estimate.json",
    "quote.pdf",
    "quote.json",
    "kitchen-views.pdf",
    "kitchen-3d.html",
}
WRITERS = {"write_cutting_map": "cutting_pdf.py", "write_edging_map": "edging_pdf.py"}


def save_table_lines():
    tree = ast.parse((ENGINE / "calculation.py").read_text(encoding="utf-8"))
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "SAVE_TABLE"
    )
    return range(node.lineno, node.end_lineno + 1)


def engine_uses():
    """(имя, файл, строка) каждого литерала-имени файла и каждого вызова писателя раскроя и кромки."""
    uses = []
    for path in sorted(ENGINE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in FILE_NAMES and id(node) not in docstrings:
                uses.append((node.value, path.name, node.lineno))
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name in WRITERS and path.name != WRITERS[name]:
                    uses.append((name, path.name, node.lineno))
    return uses


def test_file_names_and_cut_edging_writers_live_only_in_the_save_table():
    uses = engine_uses()
    table = save_table_lines()
    for name in [*sorted(FILE_NAMES), *WRITERS]:
        found = [(file, line) for n, file, line in uses if n == name]
        assert len(found) == 1 and found[0][0] == "calculation.py" and found[0][1] in table, (name, found)


# --- 71.14: тема DECISIONS ------------------------------------------------------------------------


def test_decisions_theme_carries_the_spec_tables_and_the_quote_term_entry():
    text = (REPO / "DECISIONS.md").read_text(encoding="utf-8")
    theme = text.split("## Расчёт и сохранение выводов\n", 1)[1].split("\n## ", 1)[0]
    assert "**Срок КП проверяется первым**" in theme
    assert "| Метод | Бриф в `source` и в проверках | Особые правила |" in theme
    assert "| Результат | Файлы: вид → имя (поле) | «Сделано» |" in theme
    for name in FILE_NAMES:
        assert f"→ `{name}`" in theme, name
    for method in ("`parts()`", "`cut()`", "`trip()`", "`quote(days, economy)`", "`economy_parts()`"):
        assert f"| {method} |" in theme, method


# --- `on` — дата штампа ---------------------------------------------------------------------------


def test_save_stamps_the_given_date_on_every_file(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    cut, views = api.save(calc, calc.cut(), calc.views(), on=date(2026, 1, 2))
    assert "02.01.2026" in PdfReader(cut.output.file).pages[0].extract_text()
    assert "02.01.2026" in Path(views.output_html.file).read_text(encoding="utf-8")


# --- ревью 71: сверка «тот же расчёт» по всем полям и порядок отказов production --------------------

FOREIGN = ["Вывод посчитан другим расчётом: сохраняются только выводы этого расчёта."]


def _project(**changes):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project.update(changes)
    return project


def test_save_refuses_a_result_of_the_same_version_with_another_project(home, tmp_path):
    petrov_order(home)
    other_home = tmp_path / "other"
    shutil.copytree(home / "workshop", other_home / "workshop")
    path = other_home / "p.json"
    path.write_text(json.dumps(_project(ceiling_height=2650), ensure_ascii=False), encoding="utf-8")
    api.order_new(ORDER, home=other_home)
    api.import_project(ORDER, path, home=other_home)
    calc = api.calculation(order=ORDER, home=home)
    other = api.calculation(order=ORDER, home=other_home)
    assert calc.source.version == other.source.version == 1
    assert refusal(lambda: api.save(calc, other.cut())) == FOREIGN
    assert out_files(home) == []


def test_save_refuses_a_result_of_another_order_with_the_same_project(home):
    petrov_order(home)
    ivanov = "2026-048-ivanov"
    path = home / "ivanov.json"
    path.write_text(json.dumps(_project(order=ivanov), ensure_ascii=False), encoding="utf-8")
    api.order_new(ivanov, home=home)
    api.import_project(ivanov, path, home=home)
    calc = api.calculation(order=ORDER, home=home)
    other = api.calculation(order=ivanov, home=home)
    assert other.source.content_hash == calc.source.content_hash
    assert refusal(lambda: api.save(calc, other.cut())) == FOREIGN
    assert out_files(home) == []


def test_save_refuses_a_file_result_of_the_same_version_and_project(home):
    """Файл с `version: 1` и тем же проектом даёт тот же хеш, но источник у вывода заказа должен быть заказ (ADR 0001)."""
    petrov_order(home)
    path = home / "same.json"
    path.write_text(json.dumps(_project(version=1), ensure_ascii=False), encoding="utf-8")
    calc = api.calculation(order=ORDER, home=home)
    from_file = api.calculation(project=path, home=home)
    assert (from_file.source.version, from_file.source.content_hash) == (1, calc.source.content_hash)
    assert refusal(lambda: api.save(calc, from_file.purchase())) == FOREIGN
    assert out_files(home) == []


def test_save_refuses_a_production_result_as_rowless(home):
    petrov_order(home)
    calc = api.calculation(order=ORDER, home=home)
    produced = api.production(calc)
    assert refusal(lambda: api.save(calc, produced)) == [
        "Вывод ProductionResult не сохраняется: файлов у него нет."
    ]


def test_production_refuses_with_the_parts_error_before_the_cut_error(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    path = home / "nope.json"
    materials = {**_project()["materials"], "countertop": "nope"}
    path.write_text(json.dumps(_project(materials=materials), ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, path, home=home)
    equipment = home / "workshop" / "equipment.yaml"
    text = equipment.read_text(encoding="utf-8")
    assert "макс_длина_реза: 3200" in text
    equipment.write_text(text.replace("макс_длина_реза: 3200", "макс_длина_реза: 500"), encoding="utf-8")
    with pytest.raises(OrderError, match="Столешница «nope»"):
        api.production(api.calculation(order=ORDER, home=home))
