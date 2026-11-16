"""Тикет 59: скиллы следуют ответам мастера по уборке этапа 6 и тикетам 57 и 58.

Тесты текста: у каждого решения цитата из спеки (`docs/specs/stage-6-skills.md`, «Тикет 59»), формулировка
положительная, выборки не пустые (пустой фильтр не должен проходить). Контрактные тесты: то, что скилл
цитирует из вывода команд, команда печатает.
"""

import json
import re

from kitchen import api, skill_lint
from kitchen.cli import app
from typer.testing import CliRunner

from tests.conftest import REPO
from tests.skill_steps import numbered_steps, skill_text
from tests.test_order_skill import skill_for
from tests.test_status_next_step import FIXTURES, ORDER, _v1

BRIEF = REPO / "skills" / "kitchen-brief"
runner = CliRunner()


def brief_file(*parts: str) -> str:
    return BRIEF.joinpath(*parts).read_text(encoding="utf-8")


def quick_steps() -> str:
    return skill_text("kitchen-brief").split("## Быстрый опрос")[1]


def interview() -> str:
    return skill_text("kitchen-brief").split("## Интервью")[1].split("## Быстрый опрос")[0]


def has(text: str, *needles: str) -> None:
    assert text.strip()  # фильтр не пустой
    for needle in needles:
        assert needle in text, needle


# --- (5) бюджет с техникой выключает «экономию» ----------------------------------------------------


def test_estimate_budget_with_appliances_does_not_offer_economy():
    step5 = numbered_steps("kitchen-estimate")[5]
    has(
        step5,
        "budget.includes_appliances.value",
        "Бюджет клиента с техникой, а смета без неё — сравнивать не с чем",
    )
    assert "`true`" in step5 and "`false`" in step5 and "`null`" in step5
    # Положительная часть: без `true` сравнение как раньше.
    assert "Цена сметы выше бюджета из брифа" in step5


# --- (6) срок из просьбы мастера ---------------------------------------------------------------------


def test_estimate_term_from_request_is_taken_without_asking():
    text = skill_text("kitchen-estimate")
    has(text, "срок называет мастер — в просьбе или в ответе на вопрос")
    assert "Срок КП — всегда вопрос мастеру" not in text
    step7 = numbered_steps("kitchen-estimate")[7]
    has(step7, "«N дней» и «N рабочих дней»", "рабочие дни", "без переспроса")
    for unclear in ("«календарных»", "«месяц»", "«недели»", "дробное"):
        assert unclear in step7, unclear
    has(step7, "Срок изготовления и монтажа: …")
    # Пересчёт устаревшего КП без срока в просьбе — переспросить.
    step8 = numbered_steps("kitchen-estimate")[8]
    has(step8, "срок, названный в самой просьбе, принимается без вопроса", "срок спроси заново")
    assert "срок <N> рабочих дней (вписал мастер)" not in step7


# --- (15) «дальше» берётся из «Следующий шаг», присадки там нет --------------------------------------


def test_estimate_next_step_has_no_drilling_and_production_keeps_safety_line():
    step9 = numbered_steps("kitchen-estimate")[9]
    has(step9, "где присадки больше нет")
    production = numbered_steps("kitchen-production")
    has(production[6], "Если шаг — карта присадки, которую мастер не просил")
    has(production[2], "Устаревшая карта присадки в «Устарело»")
    assert "«Следующий шаг»" not in production[2].split("Устаревшая карта присадки")[1]


def test_order_skill_has_estimate_row_and_no_drilling_rows():
    text = skill_text("kitchen-order")
    has(text, "| «Смета:» | `kitchen-estimate` |")
    assert "kitchen drilling" not in text
    assert "показать, не запускать" not in text


# --- (8), (9) брифовая вилка строками и порог «верх до потолка» --------------------------------------


def test_brief_fork_is_carried_as_text_keys():
    step5 = numbered_steps("kitchen-brief")[5]
    has(
        step5,
        "`total_low_text`, `total_high_text`",
        "ориентир до замера: от <total_low_text> до <total_high_text> ₽",
        "Вилка для вас: от <total_low_text> до <total_high_text> ₽",
    )
    assert "<total_low>" not in step5 and "<total_high>" not in step5


def test_brief_section_6_names_both_numbers_for_the_ceiling_threshold():
    step6 = numbered_steps("kitchen-brief")[6]
    has(
        step6,
        "`tall_uppers_budget`",
        "вилка считает кухню без надбавки за верх до потолка",
        "порог из текста находки",
        "`total_low_text`/`total_high_text`",
    )
    template = brief_file("assets", "brief_template.md").split("## 6.")[1].split("## 7.")[0]
    has(template, "вилка считает кухню без надбавки за верх до потолка", "`tall_uppers_budget`")


def test_quick_estimate_prints_the_keys_the_skill_quotes(home):
    data = json.loads(runner.invoke(app, ["quick-estimate", "--meters", "3.6", "--json"]).output)
    step5 = numbered_steps("kitchen-brief")[5]
    ready = [line for line in step5.splitlines() if "Готово, когда в JSON есть ключи" in line]
    assert len(ready) == 1
    keys = re.findall(r"`(\w+)`", ready[0])
    assert {"total_low_text", "total_high_text", "mock"} <= set(keys)
    assert set(keys) <= set(data), set(keys) - set(data)
    assert data["total_low_text"] == "306 000"


# --- (12) мок цен до длин стен ---------------------------------------------------------------------


def test_brief_asks_for_walls_without_price_hint_on_mock_prices():
    step5 = numbered_steps("kitchen-brief")[5]
    has(
        step5,
        "kitchen quick-estimate --meters 1 --json",
        "Для проекта мне нужны длины стен: пришлите, пожалуйста.",
        "число этого вызова клиенту и мастеру не показывается",
        "чтобы дать ориентир по цене, мне нужны длины стен: пришлите, пожалуйста",
    )


def test_quick_estimate_with_one_meter_prints_mock(home):
    result = runner.invoke(app, ["quick-estimate", "--meters", "1", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["mock"] is True


# --- (11) порядок вопросов и текст о верхних шкафах ----------------------------------------------------


def test_questions_put_mandatory_part_after_walls():
    questions = brief_file("references", "questions.md")
    assert questions.index("`room.walls`") < questions.index("## Обязательная часть")
    assert questions.index("## Обязательная часть") < questions.index("`room.ceiling_height_mm`")
    mandatory = questions.split("## Обязательная часть")[1].split("\n## ")[0]
    has(
        mandatory,
        "У какой стены — <метки> — и с какого края будет стоять холодильник, если стоять лицом к стене?",
    )


def test_interview_step_1_asks_walls_before_the_mandatory_part():
    text = interview().split("2. **Обязательная часть")[0]
    has(text, "стены и длины", "Обязательная часть")
    assert text.index("стены и длины") < text.index("«Обязательная часть»")


def test_upper_cabinets_question_is_verbatim_and_passes_the_formula_heuristic():
    questions = brief_file("references", "questions.md")
    has(questions, "«Верхние шкафы стандартные — 72 или 90 см — или до потолка?»")
    assert "стандартные, повыше" not in questions
    assert skill_lint.lint(skill_lint.DEFAULT_ROOT) == []


# --- (13) быстрый опрос: клиент из мессенджера ---------------------------------------------------------


def test_quick_survey_messenger_client_is_not_asked_for_a_phone():
    questions = brief_file("references", "questions.md").split("## Быстрый опрос")[1]
    first = questions.split("\n2. ")[0]
    has(first, "Как к вам обращаться?", "из мессенджера", "телефон не спрашивай", "Имя уже названо")
    steps = quick_steps()
    has(steps, "из мессенджера", "не больше 5 вопросов")
    assert "(N из 5 вопросов)" in steps


# --- журнал без дублей авто-записей (57) --------------------------------------------------------------


def _done_lines(name: str) -> list[str]:
    lines = [line for line in skill_text(name).splitlines() if "--kind done" in line]
    assert lines, name  # фильтр не пустой
    return lines


def test_project_skill_does_not_log_what_import_logs():
    text = skill_text("kitchen-project")
    assert "принята версия N:" not in text
    assert "запись «сделано» — «принята версия 1»" not in text
    has(text, "об импорте («принята версия N») пишет сама команда `kitchen import`")
    (line,) = _done_lines("kitchen-project")
    has(line, "3D и виды из версии N")


def test_production_skill_logs_only_single_documents():
    (line,) = _done_lines("kitchen-production")
    has(line, "карта раскроя из версии N")
    assert "документы цеха из версии N" not in line
    has(
        numbered_steps("kitchen-production")[6],
        "После `kitchen production` запись «сделано» пишет сама команда",
    )


def test_estimate_skill_logs_only_purchase_and_economy_as_done():
    (line,) = _done_lines("kitchen-estimate")
    has(line, "закупка", "экономия")
    assert "смета" not in line.lower() and "КП" not in line
    has(numbered_steps("kitchen-estimate")[9], "о смете и КП пишет сама команда")


def test_skills_keep_the_shape_the_lint_requires():
    assert skill_lint.lint(skill_lint.DEFAULT_ROOT) == []


# --- бриф по телефону (57) -----------------------------------------------------------------------------


def test_brief_skill_saves_by_phone_and_says_the_phone_is_copied():
    step7 = numbered_steps("kitchen-brief")[7]
    has(step7, "kitchen brief save <телефон>", "`client.phone`", "переносится в заказ")
    assert "не найден" in step7


def test_brief_save_by_unknown_phone_prints_not_found(home):
    result = runner.invoke(app, ["brief", "save", "+7 911 111-11-11", str(FIXTURES / "petrov_brief.json")])
    assert result.exit_code == 1
    assert "не найден" in result.output


def test_order_hands_the_estimate_step_to_kitchen_estimate(home):
    _v1(home)
    api.quote_and_save(ORDER, 20, home)
    api.production_and_save(ORDER, home)
    step = api.status(ORDER, home).next_step
    assert step.text.startswith("Смета:")
    assert skill_for(step.text, step.commands) == "kitchen-estimate"
