import json
import sqlite3
from datetime import UTC, date

import pytest
from kitchen import api, store
from kitchen.errors import KitchenError, OrderError, WorkshopError

from tests.test_check_brief import c, sergey

TODAY = date(2026, 11, 14)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("kitchen.store.today", lambda: TODAY)


def _brief(tmp_path, brief=None, name="brief.json"):
    p = tmp_path / name
    p.write_text(json.dumps(sergey() if brief is None else brief, ensure_ascii=False), encoding="utf-8")
    return p


# --- brief save --------------------------------------------------------------


def test_save_with_full_id_creates_order_with_client_from_brief(home, tmp_path):
    r = api.brief_save("2026-047-Сергеев", _brief(tmp_path))
    assert r.created is True and r.order == "2026-047-sergeev"
    assert api.order_show("2026-047").order.client == "Сергей"
    assert r.mock is True
    # счётчики эталонного брифа Сергея, как их считает check_brief.py
    assert (r.missing_required, r.conflicts) == (4, 10)
    assert len(r.check["conflicts"]) == 10 and r.check["unknown_fields"] == []


def test_save_into_existing_order_does_not_recreate(home, tmp_path):
    api.order_new("2026-047-petrov", client="Петров П. П.")
    r = api.brief_save("2026-047-petrov", _brief(tmp_path))
    assert r.created is False and r.order == "2026-047-petrov"
    assert api.order_show("petrov").order.client == "Петров П. П."  # клиента не перезаписываем


def test_save_by_bare_number_needs_existing_order(home, tmp_path):
    with pytest.raises(OrderError, match="не найден"):
        api.brief_save("2026-047", _brief(tmp_path))
    api.order_new("2026-047-petrov")
    assert api.brief_save("2026-047", _brief(tmp_path)).order == "2026-047-petrov"


def test_save_full_id_with_other_surname_is_rejected(home, tmp_path):
    api.order_new("2026-047-petrov")
    with pytest.raises(OrderError, match="2026-047 занят заказом 2026-047-petrov"):
        api.brief_save("2026-047-ivanov", _brief(tmp_path))


def test_surname_only_takes_001_in_empty_year(home, tmp_path):
    r = api.brief_save("Сергеев", _brief(tmp_path))
    assert r.created and r.order == "2026-001-sergeev"


def test_surname_only_takes_next_number_of_current_year(home, tmp_path):
    api.order_new("2026-047-petrov")
    api.order_new("2025-090-old")  # другой год не считается
    assert api.brief_save("Сергеев", _brief(tmp_path)).order == "2026-048-sergeev"


def test_surname_only_matching_existing_order_is_an_error_with_suggestion(home, tmp_path):
    api.order_new("2026-047-sergeev")
    api.order_new("2025-010-sergeev")  # совпадение ищется по всем годам
    with pytest.raises(OrderError) as e:
        api.brief_save("sergeev", _brief(tmp_path))
    text = "\n".join(e.value.problems)
    assert "2026-047-sergeev" in text and "2025-010-sergeev" in text
    assert "kitchen brief save 2026-048-sergeev" in text  # как завести новый
    with pytest.raises(OrderError, match="не найден"):
        api.order_show("2026-048")  # ничего не создано


def test_surname_match_is_exact_slug_not_substring_or_client_name(home, tmp_path):
    api.order_new("2026-047-petrov", client="Сергеев Иван")
    api.order_new("2026-048-sergeevich")
    assert api.brief_save("sergeev", _brief(tmp_path)).order == "2026-049-sergeev"


@pytest.mark.parametrize(
    "bad", ["2026-47-ivanov", "2026-0477-x", "petrov 2026-047", "2026-047-", "47", "2026/047"]
)
def test_malformed_order_id_is_not_taken_for_a_surname(home, tmp_path, bad):
    with pytest.raises(OrderError, match="похоже на номер заказа, ожидалось ГГГГ-NNN-фамилия"):
        api.brief_save(bad, _brief(tmp_path))
    with pytest.raises(OrderError, match="похоже на номер заказа"):
        api.order_show(bad)  # тот же разбор запроса, что и в поиске заказа
    api.order_new("2026-001-first")
    assert api.order_show("first").order.number == "2026-001"  # ничего лишнего не создано


@pytest.mark.parametrize("bad", ["  ", "---"])
def test_query_without_letters_is_rejected(home, tmp_path, bad):
    with pytest.raises(OrderError, match="фамилия"):
        api.brief_save(bad, _brief(tmp_path))


def test_resave_replaces_current_brief(home, tmp_path):
    api.brief_save("2026-047-sergeev", _brief(tmp_path))
    second = sergey()
    second["budget"]["amount_rub"] = c(900000)
    api.brief_save("2026-047-sergeev", _brief(tmp_path, second, "second.json"))
    shown = api.brief_show("sergeev")
    assert shown.brief["budget"]["amount_rub"]["value"] == 900000
    assert shown.check["summary"]["conflicts"] < 10  # бюджет вырос: часть замечаний ушла


def test_show_returns_saved_brief_and_check(home, tmp_path):
    api.brief_save("2026-047-sergeev", _brief(tmp_path))
    shown = api.brief_show("sergeev")
    assert shown.order == "2026-047-sergeev"
    assert shown.brief == sergey()
    assert shown.check["summary"]["conflicts"] == 10
    assert shown.mock is True and shown.mock_files  # плашка мока везде одинаковая


def test_show_without_brief(home):
    api.order_new("2026-047-petrov")
    with pytest.raises(OrderError, match="брифа ещё нет"):
        api.brief_show("petrov")


def test_unknown_field_rejects_and_stores_nothing(home, tmp_path):
    bad = sergey()
    bad["budget"]["amout_rub"] = c(1)
    with pytest.raises(OrderError) as e:
        api.brief_save("2026-047-sergeev", _brief(tmp_path, bad))
    assert "budget.amout_rub" in "\n".join(e.value.problems)
    with pytest.raises(OrderError, match="не найден"):  # заказ не создан
        api.order_show("2026-047")


def test_schema_error_rejects_and_keeps_previous_brief(home, tmp_path):
    api.brief_save("2026-047-sergeev", _brief(tmp_path))
    bad = sergey()
    bad["timeline"]["desired_date"] = c("кое-когда")
    with pytest.raises(OrderError) as e:
        api.brief_save("2026-047-sergeev", _brief(tmp_path, bad, "bad.json"))
    assert "timeline.desired_date" in "\n".join(e.value.problems)
    assert api.brief_show("sergeev").brief == sergey()


def test_conflicts_and_missing_fields_do_not_block(home, tmp_path):
    sparse = {"client": {"name": c("Анна")}}
    r = api.brief_save("2026-047-anna", _brief(tmp_path, sparse))
    assert r.created and r.missing_required > 0


@pytest.mark.parametrize("content", ["не json", "[1, 2]", '"строка"'])
def test_brief_must_be_json_object(home, tmp_path, content):
    p = tmp_path / "b.json"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(OrderError, match="JSON"):
        api.brief_save("2026-047-sergeev", p)


def test_missing_brief_file(home, tmp_path):
    with pytest.raises(OrderError, match="не найден"):
        api.brief_save("2026-047-sergeev", tmp_path / "nope.json")


def test_save_shows_stage_brief_and_logs_done(home, tmp_path, monkeypatch):
    monkeypatch.setattr("kitchen.store.now", lambda: "2026-11-14T10:00:00+00:00")
    api.brief_save("2026-047-sergeev", _brief(tmp_path))
    st = api.status("sergeev", tz=UTC)
    assert st.stage == "Бриф"
    assert "## Сделано\n\n- 2026-11-14: бриф сохранён\n" in st.text


def test_old_v1_database_is_upgraded_and_keeps_orders(home, tmp_path):
    db = home / "orders" / "kitchen.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    for statement in store.MIGRATIONS[1].split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute(
        "INSERT INTO orders (number, slug, client, created_at, updated_at)"
        " VALUES ('2026-047', 'petrov', 'Петров', '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00')"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    assert api.order_show("petrov").order.client == "Петров"
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 2
    assert api.brief_save("2026-047-petrov", _brief(tmp_path)).created is False
    assert api.brief_show("petrov").check["summary"]["conflicts"] == 10


# --- quick-estimate ----------------------------------------------------------


def test_quick_estimate_2_8_m(home):
    r = api.quick_estimate(2.8)
    assert (r.per_meter_low, r.per_meter_high) == (85000, 110000)
    assert '"per_meter_low":85000,' in r.model_dump_json()  # целое, не 85000.0
    # 2.8 * 85000 = 238000, 2.8 * 110000 = 308000: уже кратны 1000
    assert (r.total_low, r.total_high) == (238000, 308000)
    assert r.meters == 2.8 and r.mock is True and "pricing.yaml" in r.mock_files


def test_quick_estimate_4_6_m_survives_float_noise(home):
    # в float 4.6 * 85000 = 390999.99999999994: «вниз до 1000» дало бы 390000 (и 505000 для верхнего конца)
    _set_rounding(home, "вниз до 1000")
    r = api.quick_estimate(4.6)
    assert (r.total_low, r.total_high) == (391000, 506000)
    assert r.rounding == "вниз до 1000"


def test_quick_estimate_rounds_up(home):
    # 3.3 * 85000 = 280500 -> 281000; 3.3 * 110000 = 363000 -> 363000
    r = api.quick_estimate(3.3)
    assert (r.total_low, r.total_high) == (281000, 363000)
    # 0.01 м: 850 -> 1000 и 1100 -> 2000
    r = api.quick_estimate(0.01)
    assert (r.total_low, r.total_high) == (1000, 2000)


def _set_rounding(home, phrase):
    p = home / "workshop" / "pricing.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("вверх до 1000", phrase), encoding="utf-8")


def test_rounding_rule_is_parsed_into_typed_rule(home):
    rule = api.load_workshop(home).pricing.price_rounding
    assert (rule.direction, rule.step) == ("up", 1000)


def test_rounding_down_and_other_step(home):
    _set_rounding(home, "вниз до 500")
    rule = api.load_workshop(home).pricing.price_rounding
    assert (rule.direction, rule.step) == ("down", 500)
    # 3.3 * 85000 = 280500 (кратно 500), 3.3 * 110000 = 363000
    r = api.quick_estimate(3.3)
    assert (r.total_low, r.total_high) == (280500, 363000)
    # 0.01 м: 850 -> 500 и 1100 -> 1000
    r = api.quick_estimate(0.01)
    assert (r.total_low, r.total_high) == (500, 1000)


@pytest.mark.parametrize(
    "phrase", ["как получится", "вверх 1000", "вверх до тысячи", "вверх до 0", "до ближайших 1000"]
)
def test_unknown_rounding_phrase_is_russian_load_error(home, phrase):
    _set_rounding(home, phrase)
    with pytest.raises(WorkshopError) as e:
        api.load_workshop(home)
    text = "\n".join(e.value.problems)
    assert "pricing.yaml" in text and "округление_цены" in text and phrase in text
    assert "вверх до" in text  # подсказка допустимой формы
    assert api.check_workshop(home).valid is False


@pytest.mark.parametrize("meters", [0, -1, float("nan"), float("inf")])
def test_quick_estimate_needs_positive_meters(home, meters):
    with pytest.raises(KitchenError, match="больше нуля"):
        api.quick_estimate(meters)
