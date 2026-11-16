"""Уборка этапа 6 (тикет 48): тексты скиллов, закрытые находки ревью тикетов 27–44."""

import json
import re
import subprocess
import sys

from kitchen import api, skill_lint

from tests.skill_steps import numbered_steps, skill_text


def brief_text(*parts: str) -> str:
    return skill_lint.DEFAULT_ROOT.joinpath("kitchen-brief", *parts).read_text(encoding="utf-8")


def test_step_helper_finds_every_numbered_step():
    assert sorted(numbered_steps("kitchen-project")) == list(range(1, 12))
    assert sorted(numbered_steps("kitchen-production")) == list(range(1, 7))
    assert sorted(numbered_steps("kitchen-estimate")) == list(range(1, 10))
    assert sorted(numbered_steps("kitchen-order")) == list(range(1, 5))
    assert sorted(numbered_steps("kitchen-brief")) == list(range(1, 9))


# --- kitchen-project ------------------------------------------------------------


def test_project_steps_2_6_and_9_have_a_ready_sign():  # ревью 27: у каждого шага признак готовности
    steps = numbered_steps("kitchen-project")
    for n in (2, 6, 9):
        assert "Готово, когда" in steps[n], n


def test_project_step_4_knows_exit_code_2_and_step_6_knows_the_first_project():  # ревью 27
    steps = numbered_steps("kitchen-project")
    assert "Код выхода 2" in steps[4] and "не JSON" in steps[4]
    assert "версия не изменилась" in steps[6]
    assert "Проект отклонён, версии пока нет" in steps[6]  # первый проект: версии нет, сказать честно


def test_project_step_8_lists_everything_3d_project_prints():  # ревью 27
    step8 = numbered_steps("kitchen-project")[8]
    for line in ("Файл не записан (--project)", "Проверки: …", "Источник: …", "Предупреждения: …"):
        assert line in step8, line
    assert "только эта строка" not in step8


def test_project_step_10_answers_nothing_stale_and_step_11_skips_the_banner():  # ревью 27
    steps = numbered_steps("kitchen-project")
    assert "«Пока ничего.»" in steps[10] and "Устаревшего нет" in steps[10]
    assert "первая строка diff" not in steps[11]
    assert "первая строка изменений без плашки «МОК-ДАННЫЕ»" in steps[11]


def test_not_found_is_retried_once_in_the_nominative_in_every_working_skill():  # ревью 31
    for name in ("kitchen-project", "kitchen-production", "kitchen-estimate"):
        step1 = numbered_steps(name)[1]
        assert "один раз повтори команду с именительным («Петрова» → «Петров»)" in step1, name
        assert "не нашёл и так" in step1, name


# --- kitchen-estimate -----------------------------------------------------------


def test_estimate_step_8_knows_every_stale_kind_outside_money():  # ревью 29: реестр выводов
    step8 = numbered_steps("kitchen-estimate")[8]
    money = {"purchase", "estimate", "quote"}
    outside = {r.label for r in api.OUTPUT_RECOMPUTE.values() if r.command not in money}
    assert {"виды кухни", "3D кухни (html)", "карта присадки"} <= outside  # фильтр не пустой
    words = {  # вид вывода -> как его называет шаг
        "карта раскроя": "раскрой",
        "карта кромления": "кромку",
        "сборочный лист": "сборочный лист",
        "чек-лист выезда": "чек-лист выезда",
        "карта присадки": "карту присадки",
        "виды кухни": "виды кухни",
        "3D кухни (html)": "3D кухни",
    }
    assert set(words) == outside  # новый вид без слов в шаге роняет тест
    for label, word in words.items():
        assert word in step8, label
    assert "пересчёт — kitchen-project" in step8


def test_estimate_without_a_term_records_what_to_do_and_mock_refusal_records_the_replacement():  # ревью 29
    steps = numbered_steps("kitchen-estimate")
    assert "назвать срок КП" in steps[8]
    assert "Сразу после шага 7 строк про КП нет" in steps[8]
    assert "заменить мок-данные мастерской (MOCKS.md)" in steps[9]
    assert "вместо шага «выезд на монтаж»" in steps[9]


# --- kitchen-order --------------------------------------------------------------


def test_order_skill_pins_the_ready_sign_and_the_answer_tail():  # ревью 31: мутации N18, N19
    steps = numbered_steps("kitchen-order")
    ready = "Готово, когда в ответе есть все части (запись «Дальше» — если она есть) и шаг слово в слово"
    assert ready in steps[2]
    tail = skill_text("kitchen-order").split("## Ответ мастеру")[1]
    assert tail.strip() == "Состав ответа — в шаге 2, вопрос о передаче — в шаге 3. Мок — фраза из правил."


# --- kitchen-brief --------------------------------------------------------------


def test_brief_step_1_has_the_standard_ready_sign_and_keeps_brief_json_out_of_the_skill_folder():  # ревью 43
    step1 = numbered_steps("kitchen-brief")[1]
    assert "Готово, когда" in step1 and "Шаг закончен" not in step1
    assert "в рабочей папке заказа, не в папке скилла" in step1


def test_brief_message_to_the_client_has_at_most_two_questions():  # ревью 43: черновик раздела 4
    rules = brief_text("SKILL.md").split("## Правила поведения")[1].split("\n## ")[0]
    limit = next(x for x in rules.splitlines() if x.startswith("- **Лимит вопросов."))
    assert "не больше двух" in limit and "в черновике раздела 4" in limit
    section4 = brief_text("assets", "brief_template.md").split("## 4.")[1].split("\n## ")[0]
    assert "не больше двух вопросов" in section4


def test_brief_quick_end_keeps_the_section_7_rule_in_one_place_and_does_not_save_twice():  # ревью 44
    quick = brief_text("SKILL.md").split("## Быстрый опрос")[1]
    end = next(x for x in quick.splitlines() if x.startswith("7. **Конец"))
    assert "кладёт в раздел 7" not in end  # правило живёт в пункте 6 раздела
    assert "раздел 7" in next(x for x in quick.splitlines() if x.startswith("6. **Что не спрошено"))
    assert "не менялся с последнего сохранения" in end and "второй раз не сохраняй" in end


def test_brief_step_6_ready_sign_names_section_7_for_the_quick_survey():  # ревью 44
    assert "в быстром опросе — ещё и 7" in numbered_steps("kitchen-brief")[6]


def test_questions_to_the_client_use_the_masters_voice():  # ревью 43: «выводим/подключаем/ставим»
    questions = brief_text("references", "questions.md")
    script = brief_text("scripts", "check_brief.py")
    for text in (questions, script):
        assert not re.search(r"\b(?:выводим|подключаем|ставим)\b", text)
    assert "Воздух выводить в вентканал" in questions  # фильтр не пустой
    assert "Вытяжку подключать к вентканалу" in script


def test_brief_eval_1_checks_the_dishwasher_advice():  # ревью 43
    path = skill_lint.DEFAULT_ROOT / "kitchen-brief" / "evals" / "evals.json"
    first = next(e for e in json.loads(path.read_text(encoding="utf-8"))["evals"] if e["id"] == 1)
    assert "Мойку возле окна" in first["prompt"] and "Посудомойка" in first["prompt"]
    assert any("Подскажу: посудомойку лучше поставить рядом с мойкой" in a for a in first["assertions"])


def test_check_brief_rejects_a_json_array_with_a_clear_message(tmp_path):  # ревью 43
    script = skill_lint.DEFAULT_ROOT / "kitchen-brief" / "scripts" / "check_brief.py"
    bad = tmp_path / "brief.json"
    bad.write_text("[]", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(script), str(bad)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 1
    assert "Бриф должен быть JSON-объектом" in done.stderr and "Traceback" not in done.stderr
    assert "Бриф должен быть JSON-объектом" in brief_text("SKILL.md")
