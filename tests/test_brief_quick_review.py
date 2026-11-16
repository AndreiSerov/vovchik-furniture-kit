"""Тикет 44, ревью 1: быстрый опрос не спорит с общими шагами и держится схемы брифа."""

import json
import re

from tests.test_brief_skill import (
    BANNED,
    PHRASE_CLIENT,
    PHRASE_MOCK,
    expand,
    quick_questions,
    quick_section,
    read,
    skill_files,
    steps,
)

NULL_PHRASE = "чтобы дать ориентир по цене, мне нужны длины стен"
MOCK_CLIENT = "цену назову после замера"


def quick_items() -> list[str]:
    return re.split(r"^\d\. ", quick_section(), flags=re.MULTILINE)[1:]


def route_line() -> str:
    modes = read("SKILL.md").split("## Режим")[1].split("\n## ")[0]
    lines = [x for x in modes.splitlines() if x.startswith("- **Быстрый опрос")]
    assert len(lines) == 1
    return lines[0]


# ---- B1: поля из схемы ----


def test_quick_field_paths_exist_in_the_schema():
    sections = json.loads(read("references", "brief_schema.json"))["sections"]
    paths = [p for q in quick_questions() for p in re.findall(r"`([a-z_.*]+)`", q.split("«")[0])]
    assert len(paths) >= 9, paths  # фильтр не пустой
    for path in paths:  # существование путей проверяет `skill_lint.check_question_paths` (scripts/check)
        assert path.partition(".")[0] in sections, path
    assert {"timeline.desired_date", "budget.amount_rub", "budget.includes_appliances"} <= set(paths)
    assert "deadline" not in paths


# ---- B2: общие шаги и быстрый маршрут ----


def test_quick_route_names_its_steps_and_cannot_rerun_save_or_log():
    route = route_line()
    assert "затем шаги" not in route and "1–8" not in route
    reached = expand(route.split("шаги", 1)[1])
    numbered = steps(read("SKILL.md"))
    assert reached == {1, 2, 3, 5, 6, 7, 8}  # шаг 4 (отбор вопросов с лимитом 15) быстрому опросу не нужен
    assert set(numbered) >= reached
    end = quick_items()[-1]
    assert end.startswith("**Конец")
    assert "один раз" in end and "не повторяй" in end  # конец — единственный проход по шагам 6–8


def test_step_6_sends_the_quick_survey_rest_to_section_7_not_to_section_4():
    step6 = steps(read("SKILL.md"))[6]
    assert "Холодильник и антресоль без ответа — в раздел 4" in step6  # правило интервью осталось
    assert "В быстром опросе" in step6 and "раздел 7" in step6
    assert "раздел «Быстрый опрос»" in step6  # правило живёт в разделе, шаг только указывает на него
    assert "Уточнить по телефону и на замере" in quick_section()


def test_photo_first_rule_and_template_section_4_know_the_quick_exception():
    rules = read("SKILL.md").split("## Правила поведения")[1].split("\n## ")[0]
    photo = next(x for x in rules.splitlines() if x.startswith("- **Фото всегда."))
    first = next(x for x in rules.splitlines() if x.startswith("- **Лимит вопросов."))
    assert "быстром опросе" in photo and "пятый" in photo
    assert "Просьба о фото идёт первой" in first and "кроме быстрого опроса" in first
    section4 = read("assets", "brief_template.md").split("## 4.")[1].split("\n## ")[0]
    assert "первым пунктом просьба о фото" in section4
    assert "в быстром опросе" in section4 and "раздел 7" in section4


def test_rules_heading_and_mode_bullets_are_in_shape():
    body = read("SKILL.md")
    assert "## Правила поведения (все три режима)" in body
    assert "(оба режима)" not in body
    modes = body.split("## Режим")[1].split("\n## ")[0]
    bullets = [i for i, x in enumerate(modes.splitlines()) if x.startswith("- **")]
    assert len(bullets) == 3
    assert bullets == list(range(bullets[0], bullets[0] + 3))  # подряд, без пустой строки


# ---- T1–T8 ----


def test_quick_client_quotes_live_in_questions_md_only():
    quotes = [m for q in quick_questions() for m in re.findall(r"«([^»]{15,})»", q)]
    assert len(quotes) == 5, quotes
    for quote in quotes:
        holders = [p.name for p in skill_files() if quote in p.read_text(encoding="utf-8")]
        assert holders == ["questions.md"], (quote, holders)


def test_quick_section_defers_every_client_price_phrase_to_step_5():
    fork = steps(read("SKILL.md"))[5]
    section = quick_section()
    for phrase in (PHRASE_CLIENT, PHRASE_MOCK, NULL_PHRASE, MOCK_CLIENT):
        assert phrase in fork, phrase
        assert phrase not in section, phrase


def test_quick_fork_keeps_the_mock_away_from_the_client_and_says_it_once():
    fork = next(i for i in quick_items() if i.startswith("**Вилка"))
    assert "только без мока" in fork and "клиент не получает ни вилки, ни чисел" in fork
    assert "один раз" in fork and "не повторяй" in fork


def test_quick_end_runs_checks_save_and_log():
    end = quick_items()[-1]
    for needle in ("шаги 3 и 6", "по шагу 7", "по шагу 8"):
        assert needle in end, needle


def test_quick_bank_header_holds_the_limits_and_photo_is_not_first():
    text = read("references", "questions.md").split("\n## Быстрый опрос")[1]
    header = text.strip().splitlines()[0]
    for needle in ("Пять вопросов", "не больше двух в сообщении", "фото входит"):
        assert needle in header, needle
    photo = quick_questions()[4]
    assert "фото" in photo and "перв" not in photo


def test_quick_save_and_duplicate_are_masters_business_and_described_at_the_bare_name_save():
    items = quick_items()
    contact = next(i for i in items if i.startswith("**Сохранение сразу после контакта"))
    assert "мастера о фамилии не спрашивай" in contact
    assert "задавай мастеру, не клиенту" in contact
    assert "Заказ с фамилией" in contact and "<заказ> ещё нет" in contact
    assert "заведён, бриф сохранён" in contact and "бриф сохранён" in contact
    assert "<имя латиницей>" in contact and "для Ольги — olga" in contact
    assert "kitchen brief save olga brief.json" not in quick_section()


def test_quick_survey_leaves_the_mandatory_part_out():
    section = quick_section()
    assert "в быстрый опрос не входит" in section and "холодильник" in section


def test_quick_wording_is_in_first_person_singular():
    plural = re.compile(r"\b(мы|нам|наш\w*|сделаем|перезвоним|подскажем|расскажем|приедем)\b", re.IGNORECASE)
    quotes = " ".join(m for q in quick_questions() for m in re.findall(r"«([^»]+)»", q))
    assert "я смогу" in quotes  # фильтр видит текст
    assert not plural.search(quotes), plural.search(quotes)


# ---- D1: эвал по ходам ----


def test_eval_five_is_turn_by_turn_with_a_phone_and_an_early_save():
    case = json.loads(read("evals", "evals.json"))["evals"][4]
    prompt = case["prompt"]
    assert "только на заданный вопрос" in prompt and "по одному ходу" in prompt
    assert re.search(r"\+7[ \d-]{10,}", prompt)  # телефон дан
    for answer in ("3 метра", "400 тысяч", "к лету"):
        assert answer in prompt, answer
    texts = case["assertions"]
    early = [t for t in texts if "до вопроса о стенах" in t and "kitchen brief save" in t]
    assert len(early) == 1 and "olga" in early[0]
    once = [t for t in texts if "один раз" in t and "после длин" in t]
    assert len(once) == 1
    assert BANNED.search(json.dumps(case, ensure_ascii=False)) is None
