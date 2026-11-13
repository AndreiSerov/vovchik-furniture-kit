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
