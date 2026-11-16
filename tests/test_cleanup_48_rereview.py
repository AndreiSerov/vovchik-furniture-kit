from tests.skill_steps import numbered_steps
from tests.test_skill_texts_48 import brief_text


def test_parse_mode_has_no_per_message_question_limit_outside_interview_and_quick_survey():  # ревью 48 B3
    common = brief_text("SKILL.md").split("## Интервью")[0]  # режим, правила всех трёх режимов, шаги
    assert "не больше двух" not in common and "2 вопрос" not in common
    section4 = brief_text("assets", "brief_template.md").split("## 4.")[1].split("\n## ")[0]
    assert section4.count("сообщени") == 1  # только «Готовый текст сообщения клиенту»


def test_estimate_step_8_keeps_the_drilling_map_out_of_the_production_handoff():  # ревью 48 D5
    line = next(x for x in numbered_steps("kitchen-estimate")[8].splitlines() if "Карту присадки назови" in x)
    assert "Про первые четыре скажи «Устарели документы цеха; пересчёт — kitchen-production»" in line
    assert "в «пересчёт — kitchen-production» её не включай, пока мастер не попросит карту прямо" in line


import pytest
from kitchen import skill_lint

from tests.test_skill_cleanup_48 import skill_with_question


@pytest.mark.parametrize("arrow", ["`appliances.*[].model`", "`appliances.[].model`"])
def test_question_path_with_brackets_after_a_star_or_empty_field_is_an_error_not_a_crash(tmp_path, arrow):
    folder = skill_with_question(tmp_path, arrow)
    message = f"kitchen-brief: references/questions.md: строка 1: поле {arrow} не из brief_schema.json"
    assert skill_lint.check_question_paths("kitchen-brief", folder) == [message]


import re
from pathlib import Path

REVIEWS = Path(__file__).resolve().parents[1] / "docs" / "reviews" / "2026-11-15"


def test_deferred_points_to_every_master_question_of_cleanup_48():
    deferred = (REVIEWS / "deferred.md").read_text(encoding="utf-8")
    table = (
        (REVIEWS / "agent-decisions.md")
        .read_text(encoding="utf-8")
        .split("Вопросы мастеру из уборки этапа 6")[1]
    )
    numbers = {int(n) for n in re.findall(r"^\| (\d+) \|", table, re.MULTILINE)}
    cited = {int(n) for n in re.findall(r"\(48, вопрос (\d+)", deferred)}
    assert numbers and numbers == cited


def test_deferred_43_section_4_item_is_master_question_18():
    deferred = (REVIEWS / "deferred.md").read_text(encoding="utf-8")
    item = next(x for x in deferred.splitlines() if x.startswith("- Черновик раздела 4"))
    assert "вопрос 18" in item and "закрыто f3ea3dc" not in item
