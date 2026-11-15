"""Тикет 26: статические проверки скиллов (спека 6.6, ADR 0003).

Правила живут в `kitchen.skill_lint`; `scripts/check` запускает их на настоящих скиллах.
Здесь те же правила работают на скиллах-нарушителях из `fixtures/skills_lint/`.
"""

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


@pytest.mark.parametrize("case", ["ok_empty_triggers", "ok_no_triggers_file"])
def test_empty_trigger_set_passes(case):
    assert skill_lint.lint(BAD / case) == []


def test_real_skills_pass_without_exceptions():
    problems = skill_lint.lint()
    assert not problems, "\n".join(problems)
    assert (skill_lint.DEFAULT_ROOT / "kitchen-brief" / "SKILL.md").is_file()


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
