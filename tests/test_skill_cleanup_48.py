"""Уборка этапа 6 (тикет 48): правила `skill_lint`, закреплённые находками ревью тикетов 27–44."""

import json

from kitchen import skill_lint

GOOD_HEAD = "`kitchen status <заказ>`, «Устарело», «МОК-ДАННЫЕ».\n"


def shape_problems(body: str, skill: str = "kitchen-project") -> list[str]:
    lines = f"---\nname: {skill}\ndescription: d\n---\n\n{body}".splitlines()
    return skill_lint.check_shape(skill, lines)


# --- ревью 27: --kind только в том же вызове ------------------------------------


def test_kind_in_another_kitchen_call_of_the_same_span_does_not_count():
    body = GOOD_HEAD + "`kitchen log <заказ> x --kind done; kitchen status --kind next`\n"
    assert shape_problems(body) == ["kitchen-project: нет шага kitchen log с --kind next"]


def test_kind_after_a_quoted_text_in_the_same_call_counts():
    body = GOOD_HEAD + '`kitchen log <заказ> "a; b" --kind done` и `kitchen log <заказ> "c" --kind next`\n'
    assert shape_problems(body) == []


def test_a_call_ends_at_an_unquoted_shell_separator_only():
    calls = list(skill_lint.kitchen_calls('kitchen log x "a; b" --kind done; kitchen status'))
    assert [c.strip() for c in calls] == ['log x "a; b" --kind done', "status"]


def test_a_call_ends_at_a_closing_paren_outside_quotes():
    code = '$(kitchen status x) и kitchen log y "t)" --kind next'
    assert [c.strip() for c in skill_lint.kitchen_calls(code)] == ["status x", 'log y "t)" --kind next']


# --- ревью 31: сообщение выводится из константы ---------------------------------


def test_one_phrase_message_follows_the_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_lint, "MIN_PHRASES_PER_SKILL", 3)
    monkeypatch.setattr(skill_lint, "MIN_PHRASES", 2)
    path = tmp_path / "triggers.json"
    phrases = [{"text": "а", "skill": "kitchen-x"}, {"text": "б", "skill": "kitchen-x"}]
    path.write_text(json.dumps({"phrases": phrases}), encoding="utf-8")
    assert skill_lint.check_triggers(path, {"kitchen-x"}) == [
        "kitchen-x: одна фраза в triggers.json, нужно не меньше 3"
    ]


# --- ревью 44: подполя элемента списка в questions.md ---------------------------


def skill_with_question(folder, arrow: str):
    refs = folder / "references"
    refs.mkdir()
    (refs / "questions.md").write_text(f"- **сейчас** → {arrow}. «Вопрос?»\n", encoding="utf-8")
    schema = {
        "sections": {"appliances": {"fields": {"items": {"type": "list", "item": {"model": "string"}}}}}
    }
    (refs / "brief_schema.json").write_text(json.dumps(schema), encoding="utf-8")
    return folder


def test_question_path_into_a_list_item_checks_the_item_field(tmp_path):
    folder = skill_with_question(tmp_path, "`appliances.items[].model`")
    assert skill_lint.check_question_paths("kitchen-brief", folder) == []


def test_question_path_with_an_unknown_item_field_is_an_error(tmp_path):
    folder = skill_with_question(tmp_path, "`appliances.items[].nope`")
    message = "kitchen-brief: references/questions.md: строка 1: поле `appliances.items[].nope` не из brief_schema.json"
    assert skill_lint.check_question_paths("kitchen-brief", folder) == [message]


def test_question_path_with_a_star_item_field_is_known(tmp_path):
    folder = skill_with_question(tmp_path, "`appliances.items[].*`")
    assert skill_lint.check_question_paths("kitchen-brief", folder) == []


def test_real_questions_use_list_item_paths_and_they_are_checked():
    folder = skill_lint.DEFAULT_ROOT / "kitchen-brief"
    questions = (folder / "references" / "questions.md").read_text(encoding="utf-8")
    assert "appliances.items[]." in questions  # фильтр не пустой: подполя в деле
    assert skill_lint.check_question_paths("kitchen-brief", folder) == []


# --- ревью 49: команды шагов «Следующий шаг» из реестра -------------------------


def test_next_step_commands_without_an_output_exist_in_the_cli():
    from kitchen import api
    from kitchen.cli import app

    tree = skill_lint.command_tree(app)
    for recompute in (api.IMPORT_COMMAND, api.BRIEF_SAVE_COMMAND):
        level = tree
        for word in recompute.command.split():
            assert word in level, recompute
            level = level[word]
        assert isinstance(level, frozenset)  # команда, а не группа


def test_next_step_texts_are_unchanged_by_the_registry(home):
    from kitchen import api

    api.order_new("2026-001-ivanov", home=home)
    text = api.status("ivanov", home).next_step.text
    assert text == "Собрать бриф: kitchen-brief, затем kitchen brief save 2026-001-ivanov <файл>."
