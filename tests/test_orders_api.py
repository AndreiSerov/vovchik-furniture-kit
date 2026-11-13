import json
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, timedelta, timezone

import pytest
from kitchen import api
from kitchen.errors import OrderError


def _project(tmp_path, order="2026-047", name="project.json", **extra):
    p = tmp_path / name
    p.write_text(json.dumps({"order": order, "walls": [], **extra}, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def order(home):
    api.order_new("2026-047-petrov", client="Петров П. П.", phone="+7 900 000-00-00", address="Москва")
    return "2026-047-petrov"


# --- заказы и поиск ---------------------------------------------------------


def test_order_new_creates_db_and_order(home):
    r = api.order_new("2026-047-petrov", client="Петров П. П.")
    assert r.created and r.order.id == "2026-047-petrov" and r.order.number == "2026-047"
    assert (home / "orders" / "kitchen.db").is_file()
    assert r.mock is True  # мастерская пока из моков


def test_order_new_transliterates_cyrillic_surname(home):
    assert api.order_new("2026-048-Щербаков").order.id == "2026-048-shcherbakov"


def test_order_new_duplicate_number_rejected(order):
    with pytest.raises(OrderError, match="2026-047"):
        api.order_new("2026-047-ivanov")


@pytest.mark.parametrize("bad", ["petrov", "2026-47-petrov", "2026-047-", "2026-047"])
def test_order_new_bad_id(home, bad):
    with pytest.raises(OrderError, match="ГГГГ-NNN-фамилия"):
        api.order_new(bad)


@pytest.mark.parametrize("q", ["2026-047", "2026-047-petrov", "2026-047-Петров", "petr", "Петров", "ТРО"])
def test_lookup_finds_order(order, q):
    assert api.order_show(q).order.id == "2026-047-petrov"


def test_lookup_ambiguous_lists_candidates(order):
    api.order_new("2026-048-petrenko")
    with pytest.raises(OrderError) as e:
        api.order_show("petr")
    assert "2026-047-petrov" in e.value.problems[0] and "2026-048-petrenko" in e.value.problems[0]


def test_lookup_not_found(order):
    with pytest.raises(OrderError, match="не найден"):
        api.order_show("sidorov")


def test_kitchen_db_override(home, tmp_path, monkeypatch):
    db = tmp_path / "elsewhere" / "my.db"
    monkeypatch.setenv("KITCHEN_DB", str(db))
    api.order_new("2026-047-petrov")
    assert db.is_file() and not (home / "orders" / "kitchen.db").exists()


def test_schema_version_stored_and_newer_db_rejected(home):
    api.order_new("2026-047-petrov")
    db = home / "orders" / "kitchen.db"
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(OrderError, match="новой версией"):
        api.order_show("petrov")


# --- импорт и экспорт -------------------------------------------------------


def test_import_creates_v1(order, tmp_path):
    r = api.import_project(order, _project(tmp_path))
    assert r.changed and r.version == 1 and r.previous_version is None


def test_reimport_same_is_unchanged(order, tmp_path):
    api.import_project(order, _project(tmp_path))
    r = api.import_project(order, _project(tmp_path))
    assert not r.changed and r.version == 1


def test_key_order_and_whitespace_do_not_make_a_version(order, tmp_path):
    api.import_project(order, _project(tmp_path))
    p = tmp_path / "other.json"
    p.write_text('{ "walls": [],\n  "order": "2026-047" }', encoding="utf-8")
    assert not api.import_project(order, p).changed


def test_changed_project_is_v2(order, tmp_path):
    api.import_project(order, _project(tmp_path))
    r = api.import_project(order, _project(tmp_path, walls=[{"label": "А"}]))
    assert r.changed and r.version == 2 and r.previous_version == 1


def test_version_field_in_file_is_ignored(order, tmp_path):
    r = api.import_project(order, _project(tmp_path, version=17))
    assert r.version == 1
    assert not api.import_project(order, _project(tmp_path, version=3)).changed


def test_order_may_be_full_id(order, tmp_path):
    assert api.import_project(order, _project(tmp_path, order="2026-047-petrov")).version == 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{ не json", "не JSON"),
        ("[1, 2]", "объект"),
        ('{"walls": []}', "order"),
        ('{"order": "2026-999"}', "2026-999"),
    ],
)
def test_invalid_import_rejected_and_current_version_intact(order, tmp_path, content, message):
    api.import_project(order, _project(tmp_path))
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.import_project(order, bad)
    assert message in "\n".join(e.value.problems)
    assert api.export_project(order).version == 1


def test_import_missing_file(order, tmp_path):
    with pytest.raises(OrderError, match="не найден"):
        api.import_project(order, tmp_path / "nope.json")


def test_validator_errors_reject(order, tmp_path):
    def strict(data, ctx):
        return api.ValidationResult(errors=["нужны стены"] if not data.get("walls") else [])

    with pytest.raises(OrderError, match="нужны стены"):
        api.import_project(order, _project(tmp_path), validate=strict)
    assert not api.status(order).project_version


def test_validator_gets_context_and_warnings_go_to_log(order, tmp_path):
    seen = []

    def lenient(data, ctx):
        seen.append(ctx)
        return api.ValidationResult(warnings=["шкаф Н1 узкий"])

    r = api.import_project(order, _project(tmp_path), validate=lenient)
    assert r.warnings == ["шкаф Н1 узкий"]
    assert (seen[0].order_ref, seen[0].order_number) == ("2026-047-petrov", "2026-047")
    assert seen[0].workshop is not None
    assert "Предупреждение при импорте: шкаф Н1 узкий" in api.status(order).text
    api.import_project(order, _project(tmp_path), validate=lenient)  # без изменений: не дублируем
    assert api.status(order).text.count("шкаф Н1 узкий") == 1


def test_order_field_must_be_number_or_exact_full_id(order, tmp_path):
    with pytest.raises(OrderError, match="не совпадает"):
        api.import_project(order, _project(tmp_path, order="2026-047-ivanov"))


def test_rejected_message_depends_on_existing_version(order, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(OrderError) as e:
        api.import_project(order, bad)
    assert "не изменена" not in "\n".join(e.value.problems)


def test_nan_rejected(order, tmp_path):
    bad = tmp_path / "nan.json"
    bad.write_text('{"order": "2026-047", "x": NaN}', encoding="utf-8")
    with pytest.raises(OrderError, match="NaN"):
        api.import_project(order, bad)


def test_export_has_version_and_roundtrips_without_new_version(order, tmp_path):
    api.import_project(order, _project(tmp_path))
    out = tmp_path / "exported.json"
    r = api.export_project(order, out=out)
    assert json.loads(out.read_text(encoding="utf-8")) == {"order": "2026-047", "walls": [], "version": 1}
    assert r.version == 1 and r.path == str(out)
    assert not api.import_project(order, out).changed


def test_export_default_path_and_specific_version(order, home, tmp_path):
    api.import_project(order, _project(tmp_path))
    api.import_project(order, _project(tmp_path, walls=[1]))
    r = api.export_project(order, version=1)
    assert r.version == 1 and r.project["walls"] == []
    assert r.path == str(home / "orders" / "2026-047-petrov" / "project.json")
    assert (home / "orders" / "2026-047-petrov" / "project.json").is_file()
    assert api.export_project(order).version == 2


def test_export_unknown_version_and_no_project(order, tmp_path):
    with pytest.raises(OrderError, match="проекта ещё нет"):
        api.export_project(order)
    api.import_project(order, _project(tmp_path))
    with pytest.raises(OrderError, match="версии 5"):
        api.export_project(order, version=5)


# --- журнал и статус --------------------------------------------------------


def test_log_rejects_unknown_kind(order):
    with pytest.raises(OrderError, match="done"):
        api.log_add(order, "oops", "текст")


def test_log_records_project_version(order, tmp_path):
    assert api.log_add(order, "note", "до проекта").entry.project_version is None
    api.import_project(order, _project(tmp_path))
    assert api.log_add(order, "done", "после").entry.project_version == 1


def test_status_has_all_sections_and_writes_file(order, home, tmp_path):
    api.log_add(order, "decision", "фасады белые матовые")
    api.log_add(order, "done", "замер сделан")
    api.log_add(order, "next", "старое дело")
    api.log_add(order, "next", "заказать фасады")
    api.import_project(order, _project(tmp_path))
    r = api.status(order)
    text = (home / "orders" / "2026-047-petrov" / "status.md").read_text(encoding="utf-8")
    assert r.path == str(home / "orders" / "2026-047-petrov" / "status.md")
    assert "Сгенерировано" in text and "Не правьте" in text
    for section in ("## Этап", "## Версия проекта", "## Решения клиента", "## Сделано", "## Дальше"):
        assert section in text
    assert "фасады белые матовые" in text and "замер сделан" in text and "заказать фасады" in text
    assert "версия 1" in text.lower()
    assert r.stage == "Проект" and r.project_version == 1
    assert text.index("заказать фасады") < text.index("старое дело")  # свежее выше
    assert re.search(r"\d{4}-\d{2}-\d{2}.*замер сделан", text)


def test_status_stage_rule(order, tmp_path):
    assert api.status(order).stage == "Новый заказ"
    api.import_project(order, _project(tmp_path))
    assert api.status(order).stage == "Проект"


def test_status_empty_sections_say_so(order):
    text = api.status(order).text
    assert "Проекта ещё нет" in text and "Пока ничего" in text


# --- мок --------------------------------------------------------------------


def test_results_carry_mock_flag_when_workshop_is_mock(order, tmp_path):
    assert api.import_project(order, _project(tmp_path)).mock is True
    assert api.status(order).mock_files


def test_no_mock_when_workshop_is_not_mock(home):
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")
    assert api.order_new("2026-047-petrov").mock is False


# --- даты, БД, поиск ---------------------------------------------------------


def test_status_dates_are_local_not_utc(order, monkeypatch):
    monkeypatch.setattr("kitchen.store.now", lambda: "2026-11-13T22:30:00+00:00")
    api.log_add(order, "done", "замер сделан")
    moscow = timezone(timedelta(hours=3))
    assert "- 2026-11-14: замер сделан" in api.status(order, tz=moscow).text
    assert "- 2026-11-13: замер сделан" in api.status(order, tz=UTC).text


def test_concurrent_first_use_of_fresh_db(home):
    code = (
        "import sys\nfrom pathlib import Path\nfrom kitchen import store\n"
        "with store.connect(Path(sys.argv[1])): pass"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(home)], stderr=subprocess.PIPE) for _ in range(12)
    ]
    results = [(p.wait(), p.stderr.read().decode()) for p in procs]
    assert all(rc == 0 for rc, _ in results), results
    conn = sqlite3.connect(home / "orders" / "kitchen.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_not_a_database_file(home):
    db = home / "orders" / "kitchen.db"
    db.parent.mkdir()
    db.write_text("это не база, а просто текст " * 50, encoding="utf-8")
    with pytest.raises(OrderError, match="не является базой"):
        api.order_new("2026-047-petrov")


def test_unopenable_database(home, monkeypatch):
    monkeypatch.setenv("KITCHEN_DB", str(home))  # папка вместо файла
    with pytest.raises(OrderError, match="не удалось открыть"):
        api.order_new("2026-047-petrov")


def test_relative_kitchen_db_resolves_against_home(home, monkeypatch, tmp_path):
    monkeypatch.setenv("KITCHEN_DB", "data/my.db")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    api.order_new("2026-047-petrov")
    assert (home / "data" / "my.db").is_file()
    assert not (elsewhere / "data").exists()


def test_lookup_by_client_name_translit(home):
    api.order_new("2026-050-scherbakov", client="Щербаков Иван")
    assert api.order_show("Щербаков").order.id == "2026-050-scherbakov"
    assert api.order_show("иван").order.id == "2026-050-scherbakov"


def test_full_id_with_taken_number_says_who_holds_it(order):
    with pytest.raises(OrderError, match=r"2026-047 занят заказом 2026-047-petrov"):
        api.order_show("2026-047-ivanov")


def test_updated_at_touched_only_on_write(order, monkeypatch):
    before = api.order_show(order).order.updated_at
    monkeypatch.setattr("kitchen.store.now", lambda: "2030-01-01T00:00:00+00:00")
    assert api.order_show(order).order.updated_at == before
    api.log_add(order, "note", "x")
    assert api.order_show(order).order.updated_at == "2030-01-01T00:00:00+00:00"


# --- правки ревью тикета 03 (в тикете 04) -----------------------------------


def test_unchanged_reimport_keeps_warnings_without_relogging(order, tmp_path):
    def lenient(data, ctx):
        return api.ValidationResult(warnings=["шкаф Н1 узкий"])

    api.import_project(order, _project(tmp_path), validate=lenient)
    again = api.import_project(order, _project(tmp_path), validate=lenient)
    assert again.changed is False and again.warnings == ["шкаф Н1 узкий"]
    assert api.status(order).text.count("шкаф Н1 узкий") == 1


def test_order_new_takes_the_write_lock_before_checking_the_number(home, monkeypatch):
    real_connect = sqlite3.connect
    seen: list[str] = []

    class Logging(sqlite3.Connection):
        def execute(self, sql, *args):
            seen.append(" ".join(sql.split()))
            return super().execute(sql, *args)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Logging, **k))
    api.order_new("2026-047-petrov")
    start = seen.index("BEGIN IMMEDIATE")  # первая транзакция — миграция схемы, нужна вторая
    after = seen[start + 1 :]
    assert "BEGIN IMMEDIATE" in after
    mine = after[after.index("BEGIN IMMEDIATE") :]
    assert mine.index("BEGIN IMMEDIATE") < min(
        i for i, q in enumerate(mine) if q.startswith("SELECT * FROM orders WHERE number")
    )


def test_integrity_error_on_insert_is_russian_order_error(home, monkeypatch):
    real_connect = sqlite3.connect

    class Racing(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.lstrip().startswith("INSERT INTO orders"):
                raise sqlite3.IntegrityError("UNIQUE constraint failed: orders.number")
            return super().execute(sql, *args)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Racing, **k))
    with pytest.raises(OrderError, match="уже занят"):
        api.order_new("2026-047-petrov")


def test_every_migration_splits_into_complete_statements():
    from kitchen import store

    for version, script in store.MIGRATIONS.items():
        chunks = [c for c in script.split(";") if c.strip()]
        assert chunks, version
        for chunk in chunks:
            assert sqlite3.complete_statement(chunk + ";"), (version, chunk)


@pytest.mark.parametrize("value", ["2026-047-Petrov", "2026-047-Петров", "2026-047-petrov", "2026-047"])
def test_wrapper_accepts_surname_in_any_spelling(order, tmp_path, value):
    assert api.import_project(order, _project(tmp_path, order=value)).version == 1
