"""Тикет 26: статические проверки скиллов (спека 6.6, ADR 0003).

Правила живут в `kitchen.skill_lint`; `scripts/check` запускает их на настоящих скиллах.
Здесь те же правила работают на скиллах-нарушителях из `fixtures/skills_lint/`.
"""

import json
import re
import subprocess
import sys

import pytest
import typer
from kitchen import skill_lint
from kitchen.cli import app

from tests.conftest import REPO

BAD = REPO / "tests" / "fixtures" / "skills_lint"

VIOLATIONS = {
    "extra_frontmatter": [
        "kitchen-bad: строка 4: лишнее поле frontmatter «allowed-tools» (ADR 0003: только name и description)"
    ],
    "no_frontmatter": ["kitchen-bad: строка 1: нет frontmatter (блока `---` с name и description)"],
    "no_name": ["kitchen-bad: строка 1: нет поля name во frontmatter или оно пустое"],
    "no_description": ["kitchen-bad: строка 1: нет поля description во frontmatter или оно пустое"],
    "wrong_name": ["kitchen-bad: строка 2: name «другое» не совпадает с папкой"],
    "unknown_command": ["kitchen-bad: строка 8: нет команды `kitchen foo` в CLI"],
    "unknown_subcommand": ["kitchen-bad: строка 8: нет подкоманды `kitchen brief foo` в CLI"],
    "command_in_block": ["kitchen-bad: строка 10: нет команды `kitchen frobnicate` в CLI"],
    "group_without_subcommand": ["kitchen-bad: строка 8: `kitchen brief` — группа, нужна подкоманда"],
    "formula": ["kitchen-bad: строка 8: арифметика с переменной «W - 4» (формулы — только в движке)"],
    "formula_mm": [
        "kitchen-bad: строка 8: размер «ширина×высота» «596x716» (формулы — только в движке)",
        "kitchen-bad: строка 8: размер в мм «720 мм» (формулы — только в движке)",
    ],
    "formula_arithmetic": [
        "kitchen-bad: строка 6: арифметика над числами «600 + 18» (формулы — только в движке)",
        "kitchen-bad: строка 6: арифметика с переменной «W - 4» (формулы — только в движке)",
        "kitchen-bad: строка 6: присваивание формулы «H = a * b» (формулы — только в движке)",
    ],
    "shape_no_status": ["kitchen-project: нет шага kitchen status"],
    "shape_no_done": ["kitchen-project: нет шага kitchen log с --kind done"],
    "shape_no_next": ["kitchen-project: нет шага kitchen log с --kind next"],
    "shape_no_stale": ["kitchen-project: нет раздела «Устарело»"],
    "shape_no_mock": ["kitchen-project: нет плашки «МОК-ДАННЫЕ»"],
    "trigger_duplicate": ["triggers.json: фраза 2: дубль фразы 1 «  составь  БРИФ» (скилл kitchen-bad)"],
    "trigger_unknown_skill": ["triggers.json: фраза 2: неизвестный скилл «kitchen-nope»"],
    "skill_without_phrase": ["kitchen-bad: нет ни одной фразы в triggers.json"],
    "triggers_bad_structure": ['triggers.json: строка 1: нужен объект {"phrases": [...]}'],
    "triggers_not_json": [
        "triggers.json: строка 1: не JSON: Expecting property name enclosed in double quotes"
    ],
    "triggers_bad_phrase": [
        "triggers.json: фраза 1: нужны непустой text и skill, и больше ничего",
        "kitchen-bad: нет ни одной фразы в triggers.json",
    ],
}


@pytest.mark.parametrize("case", sorted(VIOLATIONS))
def test_violator_is_caught_with_its_own_message(case):
    assert skill_lint.lint(BAD / case) == VIOLATIONS[case]


def test_every_violator_fixture_has_an_expected_message():
    cases = {p.name for p in BAD.iterdir() if not p.name.startswith("ok_")}
    assert cases == set(VIOLATIONS)


@pytest.mark.parametrize("case", ["ok_empty_triggers", "ok_no_triggers_file", "ok_shape"])
def test_empty_trigger_set_passes(case):
    assert skill_lint.lint(BAD / case) == []


def test_real_skills_pass_without_exceptions():
    problems = skill_lint.lint()
    assert not problems, "\n".join(problems)
    assert (skill_lint.DEFAULT_ROOT / "kitchen-brief" / "SKILL.md").is_file()


def test_kitchen_project_skill_has_its_triggers_and_names_the_ticket_commands():
    root = skill_lint.DEFAULT_ROOT
    phrases = json.loads((root / "triggers.json").read_text(encoding="utf-8"))["phrases"]
    mine = {p["text"] for p in phrases if p["skill"] == "kitchen-project"}
    assert mine == {"Поменяй ширину Н2 у Петрова на 450", "Покажи 3D кухни Петрова"}
    body = (root / "kitchen-project" / "SKILL.md").read_text(encoding="utf-8")
    for command in ("status", "check", "export", "import", "diff", "3d", "log"):
        assert f"kitchen {command} " in body, command
    assert "kitchen check --project" in body


def test_kitchen_project_skill_regression_guards():
    body = (skill_lint.DEFAULT_ROOT / "kitchen-project" / "SKILL.md").read_text(encoding="utf-8")
    assert "Нет заказа или проекта — скажи об этом и остановись" not in body
    assert "v0" not in body
    assert "Проекта ещё нет" in body
    if "3d --project" in body:
        assert "Файл не записан" in body


def test_kitchen_production_skill_has_its_triggers_and_names_the_ticket_commands():
    root = skill_lint.DEFAULT_ROOT
    phrases = json.loads((root / "triggers.json").read_text(encoding="utf-8"))["phrases"]
    mine = {p["text"] for p in phrases if p["skill"] == "kitchen-production"}
    assert {"Сделай раскрой Петрову", "Что везти на монтаж к Петрову"} <= mine
    body = (root / "kitchen-production" / "SKILL.md").read_text(encoding="utf-8")
    for command in ("status", "check", "production", "cut", "edges", "assembly", "trip", "parts", "log"):
        assert f"kitchen {command} " in body, command
    assert skill_lint.lint(root) == []


def test_kitchen_production_skill_regression_guards():
    body = (skill_lint.DEFAULT_ROOT / "kitchen-production" / "SKILL.md").read_text(encoding="utf-8")
    assert "kitchen labels" not in body
    assert "первая строка" not in body.lower()
    # карта присадки — только по прямой просьбе
    assert "прямой просьбе" in body
    assert body.count("kitchen drilling") == 1
    # ошибочные пути: код 1 и код 2, нет проекта, «Устарело» пусто
    assert "Код выхода 1" in body and "Код выхода 2" in body
    assert "Проекта ещё нет" in body
    assert "Пока ничего." in body
    assert "Документы посчитаны на мок-данных мастерской (MOCKS.md) — сверьте размеры перед распилом" in body


def test_kitchen_production_eval_describes_the_stale_cut_map_scenario():
    path = skill_lint.DEFAULT_ROOT / "kitchen-production" / "evals" / "evals.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["skill_name"] == "kitchen-production"
    (case,) = data["evals"]
    assert "дай карту раскроя Петрова" in case["prompt"]
    assert "scripts/petrov-sandbox" in case["setup"]
    assert "kitchen import 2026-047-petrov" in case["setup"]
    assert "facades_lower_color" in case["setup"]
    assert len(case["assertions"]) >= 5


def test_kitchen_production_eval_setup_really_makes_the_cut_map_stale(home):
    from pathlib import Path

    from kitchen import api

    fixtures = Path(__file__).resolve().parent / "fixtures"
    order = "2026-047-petrov"
    api.brief_save(order, fixtures / "petrov_brief.json", home)
    api.import_project(order, fixtures / "petrov_project.json", home=home)
    api.production_and_save(order, home)
    project = json.loads((fixtures / "petrov_project.json").read_text(encoding="utf-8"))
    project["materials"]["facades_lower_color"] = "RAL 7016"
    path = home / "petrov_v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert api.import_project(order, path, home=home).version == 2
    stale = {s.kind: s for s in api.status(order, home).stale}
    assert stale["cutting"].source_version == 1
    assert stale["cutting"].command == f"kitchen cut {order}"


def test_kitchen_production_commands_print_the_ready_markers_the_skill_quotes(home):
    from kitchen import api
    from kitchen.cli import app
    from typer.testing import CliRunner

    from tests.conftest import REPO

    order = "2026-047-petrov"
    fixtures = REPO / "tests" / "fixtures"
    api.brief_save(order, fixtures / "petrov_brief.json", home)
    api.import_project(order, fixtures / "petrov_project.json", home=home)
    body = (skill_lint.DEFAULT_ROOT / "kitchen-production" / "SKILL.md").read_text(encoding="utf-8")
    step4 = body.split("4. **")[1].split("5. **")[0]
    commands = re.findall(r"`kitchen (production|cut|edges|assembly|trip|parts|drilling) <заказ>`", body)
    assert {"production", "cut", "edges", "assembly", "trip", "parts"} <= set(commands)
    assert "Детали проекта" in step4 and "Источник" in step4
    for command in commands:
        out = CliRunner().invoke(app, [command, order]).output
        if command == "parts":
            assert re.search(r"^Детали проекта .+:$", out, re.MULTILINE), command
            assert re.search(r"^Источник: .*версия \d+ проекта", out, re.MULTILINE), command
        else:
            assert re.search(r"^(Файл|.+ PDF): ", out, re.MULTILINE), command
            assert re.search(r"версия проекта \d+", out), command


def test_kitchen_production_step_1_stops_without_a_project_and_wording_guards():
    body = (skill_lint.DEFAULT_ROOT / "kitchen-production" / "SKILL.md").read_text(encoding="utf-8")
    step1 = body.split("1. **")[1].split("2. **")[0]
    assert "«Проекта ещё нет» — остановись" in step1
    assert "не остановка" not in step1
    step2 = body.split("2. **")[1].split("3. **")[0]
    assert "шаги 3, 4 с его командой" in step2
    assert "если напечатаны" in body
    assert "<документ>: посчитан из версии K, пересчитан из версии N" in body


def test_kitchen_estimate_skill_has_its_triggers_and_names_the_ticket_commands():
    root = skill_lint.DEFAULT_ROOT
    phrases = json.loads((root / "triggers.json").read_text(encoding="utf-8"))["phrases"]
    mine = {p["text"] for p in phrases if p["skill"] == "kitchen-estimate"}
    assert mine == {
        "Посчитай закупку Петрову",
        "Сколько выйдет кухня Петрова",
        "Сделай КП Петрову",
        "А если подешевле?",
    }
    body = (root / "kitchen-estimate" / "SKILL.md").read_text(encoding="utf-8")
    for command in ("status", "check", "purchase", "estimate", "quote", "brief show", "log"):
        assert f"kitchen {command} " in body, command
    assert "kitchen estimate <заказ> --economy" in body
    assert "kitchen quote <заказ> --days N" in body
    assert "Какой срок изготовления и монтажа вписать в КП, в рабочих днях?" in body
    assert "клиенту не отправляю; что заменить — MOCKS.md" in body
    assert "первая строка" not in body.lower()


def test_kitchen_estimate_eval_asks_the_term_and_refuses_the_mock_send():
    path = skill_lint.DEFAULT_ROOT / "kitchen-estimate" / "evals" / "evals.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["skill_name"] == "kitchen-estimate"
    by_prompt = {case["prompt"]: case for case in data["evals"]}
    assert len(by_prompt) == 2
    for case in data["evals"]:
        assert "scripts/petrov-sandbox" in case["setup"]
        assert len(case["assertions"]) >= 5
    quote = next(c for p, c in by_prompt.items() if "«Сделай КП Петрову»" in p)
    assert any("не вызвана" in a and "quote" in a for a in quote["assertions"])
    send = next(c for p, c in by_prompt.items() if "«Отправь КП клиенту в телеграм»" in p)
    assert any("Telegram" in a and "VK" in a for a in send["assertions"])
    assert "kitchen quote 2026-047-petrov --days 20" in send["setup"]


def test_brief_and_order_requirements_work_when_not_exempt(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_lint, "SHAPE_EXEMPT", set())

    def fresh(n):
        d = tmp_path / str(n)
        d.mkdir()
        return d

    assert lint_named(fresh(1), "kitchen-brief", "Пусто.\n") == [
        "kitchen-brief: нет шага kitchen brief save",
        "kitchen-brief: нет шага kitchen log с --kind next",
        "kitchen-brief: нет плашки «МОК-ДАННЫЕ»",
    ]
    assert lint_named(fresh(2), "kitchen-order", "Пусто.\n") == [
        "kitchen-order: нет шага kitchen status",
        "kitchen-order: нет раздела «Устарело»",
    ]
    brief = "`kitchen brief save <заказ> <файл>`, `kitchen log <заказ> x --kind next`, МОК-ДАННЫЕ.\n"
    order = "`kitchen status <заказ>`, Устарело.\n"
    assert lint_named(fresh(3), "kitchen-brief", brief) == []
    assert lint_named(fresh(4), "kitchen-order", order) == []


def test_frontmatter_words_do_not_count(tmp_path):
    d = tmp_path / "kitchen-project"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: kitchen-project\ndescription: Устарело и МОК-ДАННЫЕ\n---\n\n"
        "`kitchen status <заказ>`, `kitchen log <заказ> x --kind done`, `kitchen log <заказ> y --kind next`.\n",
        encoding="utf-8",
    )
    assert skill_lint.lint(tmp_path) == [
        "kitchen-project: нет раздела «Устарело»",
        "kitchen-project: нет плашки «МОК-ДАННЫЕ»",
    ]


def test_kind_with_equals_and_fenced_continued_log_count(tmp_path):
    body = (
        "Устарело, МОК-ДАННЫЕ, `kitchen status <заказ>`.\n\n"
        "`kitchen log <заказ> x --kind=done`\n\n"
        '```\nkitchen log <заказ> \\\n  --kind next "y"\n```\n'
    )
    assert lint_named(tmp_path, "kitchen-project", body) == []


def test_kitchen_project_eval_describes_the_petrov_scenario():
    path = skill_lint.DEFAULT_ROOT / "kitchen-project" / "evals" / "evals.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["skill_name"] == "kitchen-project"
    (case,) = data["evals"]
    assert "сделай Н2 шириной 450" in case["prompt"]
    assert "scripts/petrov-sandbox" in case["prompt"]
    assert len(case["assertions"]) >= 5


def test_command_added_to_the_cli_counts_as_existing(tmp_path):
    skill = tmp_path / "kitchen-x"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: kitchen-x\ndescription: d\n---\n\nЗапусти `kitchen newcmd` и `kitchen grp sub 5`.\n",
        encoding="utf-8",
    )
    assert len(skill_lint.lint(tmp_path)) == 2  # в настоящем CLI таких команд нет

    extended = typer.Typer()
    grp = typer.Typer()
    extended.add_typer(grp, name="grp")
    extended.command("newcmd")(lambda: None)
    grp.command("sub")(lambda: None)
    assert skill_lint.lint(tmp_path, extended) == []


def test_every_real_cli_command_is_known_to_the_checker():
    tree = skill_lint.command_tree(app)
    assert {"quick-estimate", "purchase", "production"} <= set(tree)
    assert {"save", "show"} == set(tree["brief"])


def test_wall_labels_dates_and_order_numbers_are_not_arithmetic(tmp_path):
    skill = tmp_path / "kitchen-x"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: kitchen-x\ndescription: d\n---\n\nУгол между стенами A/B, срок ГГГГ-ММ-ДД, заказ 2026-047.\n",
        encoding="utf-8",
    )
    assert skill_lint.lint(tmp_path) == []


def lint_named(tmp_path, name, body):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\n{body}", encoding="utf-8")
    return skill_lint.lint(tmp_path)


def test_shape_requires_status_log_stale_and_mock_banner(tmp_path):
    assert lint_named(tmp_path, "kitchen-estimate", "Пусто.\n") == [
        "kitchen-estimate: нет шага kitchen status",
        "kitchen-estimate: нет шага kitchen log с --kind done",
        "kitchen-estimate: нет шага kitchen log с --kind next",
        "kitchen-estimate: нет раздела «Устарело»",
        "kitchen-estimate: нет плашки «МОК-ДАННЫЕ»",
    ]


def test_brief_and_order_wait_for_their_tickets():
    assert {"kitchen-brief", "kitchen-order"} <= skill_lint.SHAPE_EXEMPT
    assert set(skill_lint.SHAPE_REQUIREMENTS) == {
        "kitchen-project",
        "kitchen-production",
        "kitchen-estimate",
        "kitchen-brief",
        "kitchen-order",
    }


def test_exempt_skill_without_shape_passes(tmp_path):
    assert lint_named(tmp_path, "kitchen-brief", "Пусто.\n") == []


def test_skill_outside_the_list_has_no_shape_requirements(tmp_path):
    assert lint_named(tmp_path, "kitchen-other", "Пусто.\n") == []


def test_log_kinds_must_sit_on_a_kitchen_log_call(tmp_path):
    body = (
        "`kitchen status <заказ>`, «Устарело», «МОК-ДАННЫЕ».\n"
        "`kitchen log <заказ> x --kind done`, а `--kind next` стоит отдельно.\n"
    )
    assert lint_named(tmp_path, "kitchen-project", body) == [
        "kitchen-project: нет шага kitchen log с --kind next"
    ]


def run_lint(*args):
    return subprocess.run(
        [sys.executable, "-m", "kitchen.skill_lint", *args], check=False, capture_output=True, text=True
    )


def test_command_line_fails_on_a_violator_and_passes_on_real_skills():
    bad = run_lint(str(BAD / "formula"))
    assert bad.returncode == 1
    assert VIOLATIONS["formula"][0] in bad.stderr
    assert run_lint().returncode == 0


def test_scripts_check_runs_the_skill_lint_on_an_active_line():
    text = (REPO / "scripts" / "check").read_text(encoding="utf-8")
    assert re.search(r"^uv run python -m kitchen\.skill_lint\s*$", text, re.MULTILINE)


def test_frontmatter_code_spans_do_not_count_as_steps(tmp_path):
    d = tmp_path / "kitchen-project"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: kitchen-project\ndescription: Сначала `kitchen status <заказ>`.\n---\n\n"
        "Устарело, МОК-ДАННЫЕ, `kitchen log <заказ> x --kind done`, `kitchen log <заказ> y --kind next`.\n",
        encoding="utf-8",
    )
    assert skill_lint.lint(tmp_path) == ["kitchen-project: нет шага kitchen status"]


def test_step_needs_whole_words(tmp_path):
    body = (
        "Устарело, МОК-ДАННЫЕ, `notkitchen status <заказ>`.\n"
        "`kitchen log <заказ> x --kind donex`, `kitchen log <заказ> y --kind next`.\n"
    )
    assert lint_named(tmp_path, "kitchen-project", body) == [
        "kitchen-project: нет шага kitchen status",
        "kitchen-project: нет шага kitchen log с --kind done",
    ]


def test_kitchen_production_eval_expects_the_single_document_journal_entry():
    path = skill_lint.DEFAULT_ROOT / "kitchen-production" / "evals" / "evals.json"
    (case,) = json.loads(path.read_text(encoding="utf-8"))["evals"]
    assert "документы цеха из версии 2" not in case["expected_output"]
    assert "карта раскроя из версии 2" in case["expected_output"]


def test_kitchen_production_step_4_quotes_the_markers_the_commands_print():
    body = (skill_lint.DEFAULT_ROOT / "kitchen-production" / "SKILL.md").read_text(encoding="utf-8")
    step4 = body.split("4. **")[1].split("5. **")[0]
    for marker in (
        "«Файл: …»",
        "«… PDF: …»",
        "«версия проекта N»",
        "«Детали проекта …:»",
        "«Источник: заказ …, версия N проекта»",
    ):
        assert marker in step4, marker


def _estimate_body():
    return (skill_lint.DEFAULT_ROOT / "kitchen-estimate" / "SKILL.md").read_text(encoding="utf-8")


def _step(n):
    return _estimate_body().split(f"\n{n}. **")[1].split(f"\n{n + 1}. **")[0]


def test_kitchen_estimate_r29_B1_every_route_lists_check_and_quote_lists_estimate():
    routes = [line for line in _step(2).splitlines() if line.lstrip().startswith("- ")]
    assert len(routes) == 5, routes
    for line in routes:
        steps = re.search(r"шаги ([\d, ]+)\.", line).group(1).split(", ")
        assert steps[0] == "3", line
        assert steps[-2:] == ["8", "9"], line
    quote = next(line for line in routes if "КП" in line)
    assert "шаги 3, 5, 7, 8, 9" in quote, quote


def test_kitchen_estimate_problems_keep_variant_term_not_converted_budget_skipped():
    assert "а вариант всё равно дай" in _step(6)
    assert "`warnings`" in _step(6)
    ask = _estimate_body().split("1. Спроси срок")[1].split("2. `kitchen quote")[0]
    assert "не переводи сам" in ask and "спроси ещё раз" in ask, ask
    assert "только если мастер не просил «экономию»" in _step(5)
    assert "Мастер просил другое — только предложи" in _step(8)
    assert "сделать проект (kitchen-project), потом смета" not in _estimate_body()


def test_kitchen_estimate_r29_B2_problems_alone_do_not_cancel_economy():
    assert not re.search(r"problems[^.]*не пуст[^.]*не получился", _step(6))


def test_kitchen_estimate_r29_B3_non_number_term_is_asked_again_before_quote():
    ask = _estimate_body().split("1. Спроси срок")[1].split("2. `kitchen quote")[0]
    assert "не число" in ask or "не целое" in ask, ask


def test_kitchen_estimate_skill_keeps_the_ticket_rules():
    for text in (
        "не бери его из брифа",
        "Пересчёт устаревшего КП — снова спроси срок",
        "Мастер просил КП — срок спроси заново",
        "срок прошлого КП не повторяй",
        "внутренний документ",
        "«КП посчитано на мок-данных — в PDF водяной знак, клиенту не отправляю; что заменить — MOCKS.md»",
        "в том числе через Telegram или VK",
        "«Пока ничего.»",
        "Проекта ещё нет",
        "--kind decision",
        "economy.price",
        "economy.saving",
        "client_text",
        "budget.amount_rub.value",
    ):
        assert text in _estimate_body(), text


def test_kitchen_estimate_r29_journal_is_last_and_mock_phrases_are_split():
    body = _estimate_body()
    assert _step(8).startswith("Устаревшее.")
    assert _step(9).startswith("Запиши журнал.")
    assert "срок без согласия не спрашивай" in _step(8)
    assert "Посчитано на мок-данных мастерской — клиенту не отправляю; что заменить — MOCKS.md" in body
    assert "Фразу про КП не говори, если КП не делали" in body
    assert "сначала шаг 5" not in _step(6)
