import json

from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

runner = CliRunner()


def test_check_workshop_text_has_banner_first_and_last(home):
    r = runner.invoke(app, ["check-workshop"])
    assert r.exit_code == 0
    lines = r.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "materials.yaml" in r.output and "листовые: 3" in r.output


def test_check_workshop_json_equals_api(home):
    r = runner.invoke(app, ["check-workshop", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.output) == json.loads(api.check_workshop(home).model_dump_json())
    assert json.loads(r.output)["mock"] is True
    assert len(json.loads(r.output)["mock_files"]) == 6
    assert MOCK_BANNER not in r.output


def test_no_banner_when_nothing_is_mock(home):
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")
    r = runner.invoke(app, ["check-workshop"])
    assert r.exit_code == 0 and MOCK_BANNER not in r.output
    assert json.loads(runner.invoke(app, ["check-workshop", "--json"]).output)["mock"] is False


def test_invalid_workshop_exit_1_with_russian_error(home):
    p = home / "workshop" / "labor.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("замер: 90", "замер: много"), encoding="utf-8")
    r = runner.invoke(app, ["check-workshop"])
    assert r.exit_code == 1
    assert "нормы_минут.замер" in r.output and "много" in r.output
    assert r.output.strip().splitlines()[0] == MOCK_BANNER
    assert runner.invoke(app, ["check-workshop", "--json"]).exit_code == 1


def test_workshop_not_found(tmp_path, monkeypatch):
    monkeypatch.delenv("KITCHEN_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["check-workshop"])
    assert r.exit_code == 1
    assert "Мастерская не найдена" in r.output


def test_root_discovery_from_repo_subfolder(monkeypatch, repo):
    monkeypatch.delenv("KITCHEN_HOME", raising=False)
    monkeypatch.chdir(repo / "tests")
    assert runner.invoke(app, ["check-workshop"]).exit_code == 0


# --- заказы -------------------------------------------------------------------


def _api_json(result) -> dict:
    return json.loads(result.model_dump_json())


def _project_file(tmp_path, **extra):
    p = tmp_path / "project.json"
    p.write_text(json.dumps({"order": "2026-047", **extra}), encoding="utf-8")
    return p


def test_order_new_text_and_json(home):
    r = runner.invoke(app, ["order", "new", "2026-047-petrov", "--client", "Петров", "--phone", "123"])
    assert r.exit_code == 0 and "2026-047-petrov" in r.output and "Петров" in r.output
    assert r.output.strip().splitlines()[0] == MOCK_BANNER
    shown = runner.invoke(app, ["order", "show", "Петров", "--json"])
    assert shown.exit_code == 0
    assert json.loads(shown.output) == _api_json(api.order_show("petrov", home))


def test_order_errors_exit_1_in_russian(home):
    r = runner.invoke(app, ["order", "show", "nobody"])
    assert r.exit_code == 1 and "не найден" in r.output
    assert runner.invoke(app, ["order", "new", "bad"]).exit_code == 1


def test_import_export_log_status_flow(home, tmp_path):
    runner.invoke(app, ["order", "new", "2026-047-petrov"])
    p = _project_file(tmp_path)
    r = runner.invoke(app, ["import", "petrov", str(p)])
    assert r.exit_code == 0 and "версия 1" in r.output
    again = runner.invoke(app, ["import", "petrov", str(p), "--json"])
    assert again.exit_code == 0 and json.loads(again.output)["changed"] is False

    out = tmp_path / "out.json"
    ex = runner.invoke(app, ["export", "petrov", "--out", str(out), "--json"])
    assert ex.exit_code == 0 and json.loads(ex.output) == _api_json(
        api.export_project("petrov", out=out, home=home)
    )
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == 1

    lg = runner.invoke(app, ["log", "petrov", "--kind", "decision", "фасады белые"])
    assert lg.exit_code == 0 and "decision" in lg.output
    assert runner.invoke(app, ["log", "petrov", "--kind", "oops", "x"]).exit_code == 1

    st = runner.invoke(app, ["status", "petrov"])
    assert st.exit_code == 0 and "фасады белые" in st.output and "## Дальше" in st.output
    assert st.output.strip().splitlines()[-1] == MOCK_BANNER
    stj = json.loads(runner.invoke(app, ["status", "petrov", "--json"]).output)
    assert stj == _api_json(api.status("petrov", home))
    assert MOCK_BANNER not in stj["text"] and stj["mock"] is True


def test_import_invalid_exit_1(home, tmp_path):
    runner.invoke(app, ["order", "new", "2026-047-petrov"])
    bad = tmp_path / "bad.json"
    bad.write_text("{ нет", encoding="utf-8")
    r = runner.invoke(app, ["import", "petrov", str(bad)])
    assert r.exit_code == 1 and "не JSON" in r.output


def test_order_commands_without_workshop_fail(tmp_path, monkeypatch):
    monkeypatch.delenv("KITCHEN_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["order", "new", "2026-047-petrov"])
    assert r.exit_code == 1 and "Мастерская не найдена" in r.output
