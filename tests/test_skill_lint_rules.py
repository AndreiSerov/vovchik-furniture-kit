"""Тикет 26: правила `kitchen.skill_lint` на коротких примерах (разбор вызовов, формулы, ввод).

Фикстуры-нарушители по одной на правило — в `test_skill_checks.py`.
"""

import pytest
from kitchen import skill_lint

from tests.conftest import REPO

BAD = REPO / "tests" / "fixtures" / "skills_lint"
HEAD = "---\nname: kitchen-x\ndescription: d\n---\n\n"  # тело начинается со строки 6


def lint_text(tmp_path, text, name="kitchen-x"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_lint.lint(tmp_path)


def lint_body(tmp_path, body):
    return lint_text(tmp_path, HEAD + body)


def write_triggers(tmp_path, content):
    (tmp_path / "triggers.json").write_text(content, encoding="utf-8")


# --- разбор вызова `kitchen` ---------------------------------------------------------------


def test_mentions_of_the_program_are_not_calls(tmp_path):
    body = (
        "Передай скиллу `kitchen-project`. Числа считает `kitchen`. Логика в `kitchen.api`.\n"
        "Файл `kitchen_brief.json`, `cd kitchen-brief`, `mykitchen foo`, `kitchen2 foo`.\n"
    )
    assert lint_body(tmp_path, body) == []


def test_second_command_on_the_line_is_checked(tmp_path):
    assert lint_body(tmp_path, "```sh\nkitchen status 2026-047 && kitchen foo\n```\n") == [
        "kitchen-x: строка 7: нет команды `kitchen foo` в CLI"
    ]
    assert lint_body(tmp_path, "```sh\nkitchen cut 1 | kitchen foo\n```\n") == [
        "kitchen-x: строка 7: нет команды `kitchen foo` в CLI"
    ]


def test_shell_punctuation_after_a_command_is_not_part_of_it(tmp_path):
    body = "```sh\nkitchen status; echo ok\necho $(kitchen status)\nkitchen brief \\\n  save b.json\n```\n"
    assert lint_body(tmp_path, body) == []
    body = "```sh\nsh -c 'kitchen status'\nKITCHEN_HOME=repo-dir kitchen status\n```\n"
    assert lint_body(tmp_path, body) == []


@pytest.mark.parametrize(
    "body",
    ["~~~\nkitchen foo\n~~~\n", "Запусти:\n\n    kitchen foo\n", "Запусти `kitchen foo`.\n"],
    ids=["tilde-fence", "indented-block", "inline"],
)
def test_unknown_command_is_found_in_every_kind_of_code(tmp_path, body):
    (problem,) = lint_body(tmp_path, body)
    assert "нет команды `kitchen foo`" in problem


def test_commands_in_plain_prose_and_list_items_are_not_checked(tmp_path):
    assert lint_body(tmp_path, "Вызови kitchen foo в тексте.\n\n- пункт\n    - kitchen foo\n") == []


def test_unknown_flag_is_an_error_known_and_help_are_not(tmp_path):
    assert lint_body(tmp_path, "`kitchen cut <заказ> --nope`\n") == [
        "kitchen-x: строка 6: нет флага `--nope` у команды `kitchen cut`"
    ]
    assert lint_body(tmp_path, "`kitchen status ЗАКАЗ --json` и `kitchen brief --help`\n") == []


@pytest.mark.parametrize("stub", ["<заказ>", "…", "...", "{команда}", "ЗАКАЗ", "$ORDER", "[заказ]"])
def test_placeholder_stops_the_command_path(tmp_path, stub):
    problems = lint_body(tmp_path, f"`kitchen brief {stub}`\n")
    assert problems == ["kitchen-x: строка 6: `kitchen brief` — группа, нужна подкоманда"]
    assert lint_body(tmp_path, f"`kitchen status {stub} --json`\n") == []


@pytest.mark.parametrize("call", ["kitchen brief --json", "kitchen order <заказ>", "kitchen order 2026-047"])
def test_group_without_a_subcommand_is_an_error(tmp_path, call):
    problems = lint_body(tmp_path, f"`{call}`\n")
    assert len(problems) == 1
    assert "нужна подкоманда" in problems[0] or "нет подкоманды" in problems[0]


# --- пустые папки --------------------------------------------------------------------------


def test_no_skills_found_is_an_error(tmp_path):
    assert skill_lint.lint(tmp_path) == [f"{tmp_path}: нет ни одного скилла kitchen-*"]
    assert skill_lint.lint(tmp_path / "нет") == [f"{tmp_path / 'нет'}: нет такой папки со скиллами"]
    assert skill_lint.main([str(tmp_path)]) == 1


def test_skill_folder_without_skill_md_is_an_error(tmp_path):
    (tmp_path / "kitchen-empty").mkdir()
    assert skill_lint.lint(tmp_path) == ["kitchen-empty: строка 1: нет файла SKILL.md"]


# --- формулы -------------------------------------------------------------------------------

LABEL_CASES = {
    "размер «ширина×высота»": "дверь 596x716",
    "размер в мм": "модуль 720 мм",
    "размер с единицей": "столешница 3,2 м",
    "арифметика над числами": "берём 600 + 18",
    "арифметика с переменной": "ширина W - 4",
    "арифметика словами": "ширина модуля минус 4",
    "присваивание формулы": "H = a * b",
}


@pytest.mark.parametrize("label", skill_lint.FORMULA_LABELS)
def test_every_formula_rule_fires(tmp_path, label):
    (problem,) = lint_body(tmp_path, LABEL_CASES[label] + "\n")
    assert f"строка 6: {label} «" in problem


def test_every_formula_label_has_a_case():
    assert set(LABEL_CASES) == set(skill_lint.FORMULA_LABELS)


CAUGHT = [
    "W - 4", "W-4", "w - 4", "2 * W", "2×H", "Ш - 4", "ширина - 4", "width - 4", "height*2", "В × 2",
    "W / 2", "(W - 4) / 2", "W - H", "600-4", "600 - 4", "600 + 18", "4 + 600", "3,2 м", "720 мм",
    "ширина модуля минус 4", "высота шкафа плюс 18",
]  # fmt: skip
PASSES = [
    "PDF + HTML", "Telegram / WhatsApp", "A / B", "PDF - 2 листа", "UTF-8", "ADR-0003", "ISO-8601",
    "A-4", "Задай 3 - 5 вопросов, 1 - 2 в сообщении", "шаг 1 / 3", "24/7", "30-40 дней",
    "4 м столешницы", "посудомойка 45 см", "заказ 2026-047-petrov", "срок 2026-11-13", "ГГГГ-ММ-ДД",
    "угол A/B", "стены A и B", "15 вопросов", "10/12/2026",
]  # fmt: skip


@pytest.mark.parametrize("text", CAUGHT)
def test_formula_is_caught(tmp_path, text):
    assert lint_body(tmp_path, f"Считай так: {text}.\n") != []


@pytest.mark.parametrize("text", PASSES)
def test_prose_is_not_a_formula(tmp_path, text):
    assert lint_body(tmp_path, f"Пример: {text}.\n") == []


def test_one_size_gives_one_message(tmp_path):
    assert len(lint_body(tmp_path, "Стена 2800×2070.\n")) == 1


def test_arithmetic_fixture_fires_each_arithmetic_rule():
    problems = skill_lint.lint(BAD / "formula_arithmetic")
    labels = {p.split(": ")[2].split(" «")[0] for p in problems}
    assert labels == {"арифметика с переменной", "арифметика над числами", "присваивание формулы"}


# --- устойчивость к странному вводу --------------------------------------------------------


def test_non_string_frontmatter_key_is_reported_not_raised(tmp_path):
    text = "---\nname: kitchen-x\ndescription: d\non: push\n1: x\n---\n\nТекст\n"
    assert lint_text(tmp_path, text) == [
        "kitchen-x: строка 4: лишнее поле frontmatter «on» (ADR 0003: только name и description)",
        "kitchen-x: строка 5: лишнее поле frontmatter «1» (ADR 0003: только name и description)",
    ]


def test_non_utf8_files_are_reported_and_other_skills_still_checked(tmp_path):
    bad = tmp_path / "kitchen-a"
    bad.mkdir()
    (bad / "SKILL.md").write_bytes(b"---\nname: kitchen-a\n\xff\xfe\n---\n")
    (tmp_path / "triggers.json").write_bytes(b'{"phrases": []}\n\xff')
    other = tmp_path / "kitchen-b"
    other.mkdir()
    body = HEAD.replace("kitchen-x", "kitchen-b") + "`kitchen foo`\n"
    (other / "SKILL.md").write_text(body, encoding="utf-8")
    assert skill_lint.lint(tmp_path) == [
        "kitchen-a: строка 3: файл не в UTF-8",
        "kitchen-b: строка 6: нет команды `kitchen foo` в CLI",
        "triggers.json: строка 2: файл не в UTF-8",
    ]


def test_bom_has_its_own_message(tmp_path):
    problems = lint_text(tmp_path, "﻿" + HEAD + "Текст\n")
    assert problems == ["kitchen-x: строка 1: файл начинается с BOM, уберите его"]


def test_duplicate_frontmatter_key_is_an_error(tmp_path):
    text = "---\nname: kitchen-x\ndescription: a\ndescription: b\n---\n\nТекст\n"
    assert lint_text(tmp_path, text) == ["kitchen-x: строка 4: повтор ключа frontmatter «description»"]


def test_yaml_error_has_a_line_and_a_hint_for_colons(tmp_path):
    problems = lint_text(tmp_path, "---\nname: kitchen-x\ndescription: Скилл: про кухни\n---\n\nТекст\n")
    assert len(problems) == 1
    assert problems[0].startswith("kitchen-x: строка 3: frontmatter не разбирается")
    assert "возьми в кавычки" in problems[0]


def test_frontmatter_that_is_not_yaml_or_not_a_mapping(tmp_path):
    (bad,) = lint_text(tmp_path, "---\nname: [\n---\n\nТекст\n")
    assert "frontmatter не разбирается" in bad
    assert lint_text(tmp_path, "---\n- a\n- b\n---\n\nТекст\n") == [
        "kitchen-x: строка 2: frontmatter не словарь"
    ]


@pytest.mark.parametrize("key", ["name", "description"])
def test_empty_name_or_description_is_an_error(tmp_path, key):
    other = "description: d" if key == "name" else "name: kitchen-x"
    problems = lint_text(tmp_path, f'---\n{key}: ""\n{other}\n---\n\nТекст\n')
    assert problems == [f"kitchen-x: строка 1: нет поля {key} во frontmatter или оно пустое"]


def test_frontmatter_is_not_searched_for_commands_and_formulas(tmp_path):
    text = '---\nname: kitchen-x\ndescription: "запусти `kitchen foo`, W - 4, 720 мм"\n---\n\nТекст\n'
    assert lint_text(tmp_path, text) == []


# --- набор фраз ----------------------------------------------------------------------------


def test_duplicate_phrase_ignores_case_yo_and_trailing_punctuation(tmp_path):
    lint_text(tmp_path, HEAD + "Текст\n")
    write_triggers(
        tmp_path,
        '{"phrases": [{"text": "Ёлка", "skill": "kitchen-x"}, {"text": "елка!", "skill": "kitchen-x"}]}',
    )
    assert skill_lint.lint(tmp_path) == ["triggers.json: фраза 2: дубль фразы 1 «елка!» (скилл kitchen-x)"]


def test_empty_phrase_text_and_extra_phrase_key_are_errors(tmp_path):
    lint_text(tmp_path, HEAD + "Текст\n")
    write_triggers(
        tmp_path,
        '{"phrases": [{"text": "  ", "skill": "kitchen-x"}, {"text": "a", "skill": "kitchen-x", "note": 1}]}',
    )
    assert skill_lint.lint(tmp_path) == [
        "triggers.json: фраза 1: нужны непустой text и skill, и больше ничего",
        "triggers.json: фраза 2: нужны непустой text и skill, и больше ничего",
    ]


def test_one_bad_phrase_gives_one_message_not_a_missing_phrase_too(tmp_path):
    lint_text(tmp_path, HEAD + "Текст\n")
    write_triggers(tmp_path, '{"phrases": [{"text": "", "skill": "kitchen-x"}]}')
    assert len(skill_lint.lint(tmp_path)) == 1


def test_arithmetic_next_to_a_length_unit_is_caught(tmp_path):
    """Второе правило с меткой «арифметика над числами»: число с единицей длины рядом с оператором."""
    (problem,) = lint_body(tmp_path, "Текст: 45 см + 5\n")
    assert "арифметика над числами «45 см + 5»" in problem
