"""Тикет 26: статические проверки скиллов (спека 6.6, ADR 0003).

Правила — в `skill_lint.py`. Здесь они работают на настоящих скиллах `skills/kitchen-*`
и на скиллах-нарушителях из `fixtures/skills_bad/`.
"""

import pytest

from tests.skill_lint import (
    REPO,
    check_commands,
    check_frontmatter,
    check_no_formulas,
    check_triggers,
    load_triggers,
    skill_dirs,
)

BAD = REPO / "tests" / "fixtures" / "skills_bad"
REAL = skill_dirs()


def test_there_are_real_skills_to_check():
    assert [p.name for p in REAL] == ["kitchen-brief"]


@pytest.mark.parametrize("skill", REAL, ids=lambda p: p.name)
def test_real_skill_frontmatter(skill):
    assert check_frontmatter(skill) == []


@pytest.mark.parametrize("skill", REAL, ids=lambda p: p.name)
def test_real_skill_commands_exist(skill):
    assert check_commands(skill) == []


@pytest.mark.parametrize("skill", REAL, ids=lambda p: p.name)
def test_real_skill_has_no_formulas(skill):
    assert check_no_formulas(skill) == []


def test_extra_frontmatter_field_is_caught():
    problems = check_frontmatter(BAD / "extra_frontmatter")
    assert problems == ["лишние поля frontmatter: ['allowed-tools']"]


def test_missing_frontmatter_is_caught():
    assert check_frontmatter(BAD / "missing_frontmatter") == ["нет frontmatter"]


def test_name_must_match_folder():
    (problem,) = check_frontmatter(BAD / "wrong_name")
    assert "не совпадает с папкой" in problem


def test_unknown_commands_are_caught_in_spans_and_blocks():
    problems = check_commands(BAD / "unknown_command")
    assert problems == [
        "нет команды `kitchen purchse`",
        "нет команды `kitchen brief nosuch`",
        "нет команды `kitchen frobnicate`",
    ]


def test_arithmetic_in_body_is_caught():
    problems = check_no_formulas(BAD / "formula_arithmetic")
    assert len(problems) == 3
    assert all("арифметика" in p for p in problems)


def test_millimetres_and_sizes_in_body_are_caught():
    problems = check_no_formulas(BAD / "formula_mm")
    assert any("размер в мм" in p for p in problems)
    assert any("596x716" in p for p in problems)


def test_real_trigger_set_is_valid():
    skills = {p.name for p in REAL}
    assert check_triggers(load_triggers(), skills) == []


SKILLS = {"kitchen-brief", "kitchen-project"}


def phrases(*pairs):
    return {"phrases": [{"text": t, "skill": s} for t, s in pairs]}


def test_empty_trigger_set_is_an_error():
    assert check_triggers({"phrases": []}, SKILLS) == ["набор фраз пуст"]


@pytest.mark.parametrize("data", [[], {}, {"phrases": "x"}, None])
def test_trigger_set_needs_the_schema(data):
    assert check_triggers(data, SKILLS) == ['нужен объект {"phrases": [...]}']


@pytest.mark.parametrize(
    "item",
    [{"text": "a"}, {"text": "", "skill": "kitchen-brief"}, {"text": "a", "skill": "x", "z": 1}, "a"],
)
def test_malformed_phrase_is_an_error(item):
    problems = check_triggers({"phrases": [item]}, SKILLS)
    assert any("нужны непустой text и skill" in p for p in problems)


def test_duplicate_phrase_is_an_error_ignoring_case_and_spaces():
    data = phrases(("Составь бриф", "kitchen-brief"), ("  составь  БРИФ", "kitchen-project"))
    assert any("повторяет фразу 0" in p for p in check_triggers(data, SKILLS))


def test_unknown_expected_skill_is_an_error():
    data = phrases(("a", "kitchen-brief"), ("b", "kitchen-project"), ("c", "kitchen-nope"))
    assert check_triggers(data, SKILLS) == ["фраза 2: нет скилла «kitchen-nope»"]


def test_skill_without_a_phrase_is_an_error():
    data = phrases(("a", "kitchen-brief"))
    assert check_triggers(data, SKILLS) == ["у скилла «kitchen-project» нет фразы"]


def test_wall_labels_are_not_arithmetic(tmp_path):
    skill = tmp_path / "kitchen-x"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: kitchen-x\ndescription: d\n---\n\nУгол между стенами A/B, срок ГГГГ-ММ-ДД, заказ 2026-047.\n",
        encoding="utf-8",
    )
    assert check_no_formulas(skill) == []
