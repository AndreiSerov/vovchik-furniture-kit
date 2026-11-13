import json
from datetime import date

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

from tests.test_check_brief import sergey

runner = CliRunner()


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr("kitchen.store.today", lambda: date(2026, 11, 14))


@pytest.fixture
def brief_file(tmp_path):
    p = tmp_path / "brief.json"
    p.write_text(json.dumps(sergey(), ensure_ascii=False), encoding="utf-8")
    return p


def test_brief_save_text_has_banner_and_counts(home, brief_file):
    r = runner.invoke(app, ["brief", "save", "Сергеев", str(brief_file)])
    assert r.exit_code == 0, r.output
    lines = r.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "Заказ 2026-001-sergeev заведён, бриф сохранён." in r.output
    assert "обязательных полей: 4" in r.output and "рисков: 10" in r.output


def test_brief_save_json_is_the_api_result(home, brief_file):
    r = runner.invoke(app, ["brief", "save", "2026-047-sergeev", str(brief_file), "--json"])
    assert r.exit_code == 0, r.output
    data = api.BriefSaveResult.model_validate_json(r.output)
    assert (data.order, data.created, data.conflicts, data.mock) == ("2026-047-sergeev", True, 10, True)
    assert MOCK_BANNER not in r.output


def test_brief_save_rejected_exits_1_in_russian(home, tmp_path):
    bad = sergey()
    bad["budget"]["amout_rub"] = {"value": 1, "source": "client"}
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    r = runner.invoke(app, ["brief", "save", "2026-047-sergeev", str(p)])
    assert r.exit_code == 1
    assert "budget.amout_rub" in r.output and "Бриф не сохранён" in r.output


def test_brief_show_prints_brief_json_and_json_equals_api(home, brief_file):
    runner.invoke(app, ["brief", "save", "2026-047-sergeev", str(brief_file)])
    r = runner.invoke(app, ["brief", "show", "sergeev"])
    assert r.exit_code == 0 and json.loads(r.output) == sergey() and MOCK_BANNER not in r.output
    j = runner.invoke(app, ["brief", "show", "sergeev", "--json"])
    assert json.loads(j.output) == json.loads(api.brief_show("sergeev", home).model_dump_json())


def test_brief_show_without_brief_exits_1(home):
    api.order_new("2026-047-petrov")
    r = runner.invoke(app, ["brief", "show", "petrov"])
    assert r.exit_code == 1 and "брифа ещё нет" in r.output


def test_quick_estimate_text(home):
    r = runner.invoke(app, ["quick-estimate", "--meters", "4.6"])
    assert r.exit_code == 0, r.output
    lines = r.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "391 000 – 506 000 ₽" in r.output and "вверх до 1000" in r.output


def test_quick_estimate_json_equals_api(home):
    r = runner.invoke(app, ["quick-estimate", "--meters", "2.8", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.output) == json.loads(api.quick_estimate(2.8, home).model_dump_json())
    assert json.loads(r.output)["mock"] is True and MOCK_BANNER not in r.output


@pytest.mark.parametrize("meters", ["0", "-2"])
def test_quick_estimate_rejects_non_positive(home, meters):
    r = runner.invoke(app, ["quick-estimate", "--meters", meters])
    assert r.exit_code == 1 and "больше нуля" in r.output
