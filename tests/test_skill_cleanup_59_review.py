import re

from kitchen import skill_lint

from tests.conftest import REPO
from tests.skill_steps import numbered_steps, skill_text

QUESTIONS = REPO / "skills" / "kitchen-brief" / "references" / "questions.md"


def test_walls_request_phrase_follows_the_mock_key():
    null = next(line for line in numbered_steps("kitchen-brief")[5].splitlines() if "равно `null`" in line)
    on_mock = null.split('При `"mock": true`')[1]
    on_real = null.split('При `"mock": false`')[1].split('При `"mock": true`')[0]
    assert "«Для проекта мне нужны длины стен: пришлите, пожалуйста.»" in on_mock
    assert "ориентир по цене не обещай" in on_mock
    assert "«чтобы дать ориентир по цене, мне нужны длины стен: пришлите, пожалуйста»" in on_real


def test_only_true_appliances_flag_switches_off_the_economy_offer():
    step5 = numbered_steps("kitchen-estimate")[5]
    assert "`budget.includes_appliances.value` равно `true` — «экономию» по бюджету не предлагай" in step5
    assert "Значение `false`, `null` или поля нет — сравнение как ниже" in step5


def test_messenger_client_with_known_name_skips_the_first_quick_question():
    first = QUESTIONS.read_text(encoding="utf-8").split("## Быстрый опрос")[1].split("\n2. ")[0]
    messenger = first.split("Клиент из мессенджера:")[1]
    assert "Имя уже названо — первый вопрос пропусти" in messenger
    step2 = skill_text("kitchen-brief").split("## Быстрый опрос")[1].split("\n3. ")[0]
    messenger = step2.split("Клиент из мессенджера:")[1]
    assert "телефон не спрашивай, его даёт мастер" in messenger
    assert "первый вопрос — «Как к вам обращаться?», если имя неизвестно" in messenger
    assert "известно — первый вопрос пропусти" in messenger


def test_upper_cabinet_heights_pass_the_formula_heuristic():
    (line,) = [l for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if "`storage.upper_height`" in l]
    assert "72 или 90 см" in line
    assert list(skill_lint.formula_spans(line)) == []


def test_production_answer_names_no_parts_count_per_material():
    text = skill_text("kitchen-production")
    assert "листов: <число" in numbered_steps("kitchen-production")[5]
    assert not re.search(r"детал\w*\s*(?:<|\d)", text)


def test_phone_save_never_opens_a_new_order():
    step7 = numbered_steps("kitchen-brief")[7]
    assert "новый заказ по телефону не заводится" in step7
    assert "записанный телефон не меняется" in step7


def test_section_6_names_the_threshold_and_the_fork_ends_together():
    step6 = numbered_steps("kitchen-brief")[6]
    assert (
        "в разделе 6 оба числа: порог из текста находки и концы вилки `total_low_text`/`total_high_text`"
        in step6
    )
    template = (REPO / "skills" / "kitchen-brief" / "assets" / "brief_template.md").read_text(
        encoding="utf-8"
    )
    assert "оба числа: порог из текста находки и концы вилки `total_low_text`/`total_high_text`" in template


def test_project_done_only_after_order_3d_not_after_draft():
    (line,) = [l for l in numbered_steps("kitchen-project")[11].splitlines() if "--kind done" in l]
    assert "Сделано, только после `kitchen 3d <заказ>`" in line
    assert "После `kitchen 3d --project <файл>` запись не пишется" in line


def test_mock_probe_runs_only_when_the_client_asked_about_price():
    null = next(l for l in numbered_steps("kitchen-brief")[5].splitlines() if "равно `null`" in l)
    before, _ = null.split("kitchen quick-estimate --meters 1 --json")
    assert "Клиент о цене не спрашивал — больше ничего не запускай" in before
    assert "Спросил — один раз" in before


def test_appliances_budget_is_not_compared_with_the_estimate():
    flag = numbered_steps("kitchen-estimate")[5].split("равно `true`")[1].split("Значение `false`")[0]
    assert "«экономию» по бюджету не предлагай" in flag
    assert "цену с бюджетом не сравнивай" in flag


def test_interview_asks_the_fridge_wall_by_label_from_the_walls_answer():
    step1 = skill_text("kitchen-brief").split("## Интервью")[1].split("\n2. ")[0]
    assert "(холодильник — меткой стены из ответа о длинах)" in step1
    mandatory = QUESTIONS.read_text(encoding="utf-8").split("## Обязательная часть")[1].split("\n## ")[0]
    assert "Метки стен возьми из ответа о длинах." in mandatory


def test_section_6_threshold_rule_is_not_limited_to_real_prices():
    assert (
        "Сработала `tall_uppers_budget` («верх до потолка») — в разделе 6 оба числа"
        in numbered_steps("kitchen-brief")[6]
    )


def test_quick_survey_bank_keeps_the_limit_of_five():
    quick = QUESTIONS.read_text(encoding="utf-8").split("## Быстрый опрос")[1]
    assert "Лимит — пять на весь опрос, фото входит" in quick


def test_estimate_term_other_than_working_days_means_asking_again():
    step71 = numbered_steps("kitchen-estimate")[7].split("\n   2. ")[0]
    assert "«N дней» и «N рабочих дней» — рабочие дни" in step71
    for word in ("«календарных»", "«месяц»", "«недели»", "дробное"):
        assert word in step71
    assert "не переводи сам, спроси ещё раз: «Назовите срок числом рабочих дней»" in step71


def test_quick_survey_end_does_not_demand_all_five_questions():
    step7 = skill_text("kitchen-brief").split("## Быстрый опрос")[1].split("\n7. ")[1].split("\n8. ")[0]
    assert "Опрос закончен, когда заданы все вопросы раздела" in step7
    assert "все пять вопросов" not in step7
