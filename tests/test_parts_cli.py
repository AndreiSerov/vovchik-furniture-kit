import json
from pathlib import Path

from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

runner = CliRunner()


def test_parts_json_equals_api_and_text_has_banner(home, base_door_project):
    r = runner.invoke(app, ["parts", "--project", str(base_door_project), "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data == json.loads(api.calculation(project=base_door_project, home=home).parts().model_dump_json())
    assert data["mock"] is True and data["warning"] == MOCK_BANNER

    t = runner.invoke(app, ["parts", "--project", str(base_door_project)])
    assert t.exit_code == 0, t.output
    lines = t.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "Н1-бок-Л" in t.output and "2026-047-petrov" in t.output
    assert "3.71" in t.output and "0.4253" in t.output
    # истории 9-10: толщина, материал, текстура и id кромки по сторонам в строке детали
    assert (
        "    Н1-бок-Л — боковина 720×560×16 мм, ldsp_16_white, без текстуры, кромка: L1 edge_08_white"
        in t.output
    )
    assert "    Н1-зад — задняя стенка 716×596×3 мм, hdf_3_white, без текстуры\n" in t.output
    part = data["modules"][0]["parts"][0]
    assert {"thickness", "material", "texture", "edges"} <= set(part)


def test_parts_missing_file_exits_1(home):
    r = runner.invoke(app, ["parts", "--project", str(home / "nope.json")])
    assert r.exit_code == 1 and "не найден" in r.output


def test_odd_width_exits_1_without_traceback(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["walls"][0]["rows"][0]["modules"][0]["width"] = 601
    odd = home / "odd.json"
    odd.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    for extra in ([], ["--json"]):
        r = runner.invoke(app, ["parts", "--project", str(odd), *extra])
        assert r.exit_code == 1, r.output
        assert "Traceback" not in r.output
        assert "Н1" in r.output and "601" in r.output and "чётную" in r.output


def test_parts_order_contract(home):
    """`kitchen parts <заказ>`: --json равен API, плашка мока, версия и бриф, находки и общие позиции в тексте."""
    from pathlib import Path

    fixtures = Path(__file__).resolve().parent / "fixtures"
    api.brief_save("2026-047-petrov", fixtures / "petrov_brief.json", home)
    api.import_project("2026-047-petrov", fixtures / "petrov_project.json", home=home)
    r = runner.invoke(app, ["parts", "petrov", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == json.loads(
        api.calculation(order="petrov", home=home).parts().model_dump_json()
    )
    t = runner.invoke(app, ["parts", "2026-047-petrov"])
    assert t.exit_code == 0, t.output
    lines = t.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    assert "версия 1" in t.output and "бриф №1" in t.output
    # находка проверок — строка списка, а не подстрока предупреждения шаблона («Н5: без задней стенки»)
    assert any(line.startswith("  [предупреждение] Модуль Н5: без задней стенки") for line in lines)
    summary = "Проверки: 0 ошибок, 3 предупреждения"
    assert summary in lines
    assert lines.index(summary) < next(i for i, line in enumerate(lines) if "Модуль П1" in line)
    assert "Общие позиции" in t.output and "plinth_100" in t.output and "RAL 7044" in t.output

    f = runner.invoke(app, ["parts", "--project", str(fixtures / "petrov_project.json")])
    assert f.exit_code == 0, f.output
    assert "файл" in f.output and "petrov_project.json" in f.output and "бриф №1" in f.output


def test_parts_needs_exactly_one_source(home, base_door_project):
    none = runner.invoke(app, ["parts"])
    assert none.exit_code == 1 and "заказ" in none.output and "--project" in none.output
    both = runner.invoke(app, ["parts", "petrov", "--project", str(base_door_project)])
    assert both.exit_code == 1 and "либо" in both.output


def test_parts_text_marks_textured_material(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    project["materials"]["body"] = "ldsp_16_oak"
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    t = runner.invoke(app, ["parts", "--project", str(base_door_project)])
    assert t.exit_code == 0, t.output
    assert "ldsp_16_oak, текстура вдоль длины, кромка: L1 edge_08_oak" in t.output


def test_parts_text_prints_the_gap_in_its_place_in_the_row():
    petrov = str(Path(__file__).resolve().parent / "fixtures" / "petrov_project.json")
    out = runner.invoke(app, ["parts", "--project", petrov]).output.splitlines()
    gap = out.index("  ПМ1 — пустое место 264 мм")
    before = [line for line in out[:gap] if line.startswith("  Модуль ")]
    assert before[-1].startswith("  Модуль Н6 ")
    assert out[gap - 1].startswith("    Н6-") and out[gap + 1].startswith("  Модуль В5 ")
    data = json.loads(runner.invoke(app, ["parts", "--project", petrov, "--json"]).output)
    assert data == json.loads(api.calculation(project=petrov).parts().model_dump_json())
    assert [g["label"] for g in data["gaps"]] == ["ПМ1"]
    assert "modules_before" not in data["gaps"][0]


def test_parts_text_prints_gaps_at_the_start_and_at_the_end_of_rows(home):
    from tests.test_gap import base, gap, lower, upper, wall_door, write

    walls = [
        {
            "label": "A",
            "length": 1800,
            "rows": [
                lower(gap("ПМ1", 600), base("Н1"), gap("ПМ2", 600)),
                upper(wall_door("В1"), gap("ПМ3", 600), gap("ПМ4", 600)),
            ],
        },
    ]
    out = runner.invoke(app, ["parts", "--project", str(write(home, walls))]).output.splitlines()
    heads = [line for line in out if line.startswith("  ") and not line.startswith("    ")]
    assert [h.split(" — ")[0] for h in heads[:6]] == [
        "  ПМ1",
        "  Модуль Н1 (base_door)",
        "  ПМ2",
        "  Модуль В1 (wall_door)",
        "  ПМ3",
        "  ПМ4",
    ]
