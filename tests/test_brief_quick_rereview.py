import json

from kitchen import skill_lint

from tests.test_brief_quick_review import quick_items
from tests.test_brief_skill import expand, quick_questions, read, steps


def _skill(tmp_path, schema, questions):
    refs = tmp_path / "kitchen-x" / "references"
    refs.mkdir(parents=True)
    (refs / "brief_schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (refs / "questions.md").write_text(questions, encoding="utf-8")
    return tmp_path / "kitchen-x"


def test_question_paths_checks_every_path_on_the_line_not_only_the_first(tmp_path):
    schema = {"sections": {"room": {"fields": {"walls": {}}}}}
    folder = _skill(tmp_path, schema, "# Банк\n- **сейчас** → `room.walls`, `room.nope`. «Длины?»\n")
    assert skill_lint.check_question_paths("kitchen-x", folder) == [
        "kitchen-x: references/questions.md: строка 2: поле `room.nope` не из brief_schema.json"
    ]


def test_question_paths_reports_a_schema_without_sections(tmp_path):
    folder = _skill(tmp_path, {"fields": {}}, "# Банк\n- → `room.walls`.\n")
    assert skill_lint.check_question_paths("kitchen-x", folder) == [
        "kitchen-x: references/brief_schema.json: нет раздела sections"
    ]


def test_parse_and_interview_still_run_every_step_with_photo_first_and_section_4():
    body = read("SKILL.md")
    modes = body.split("## Режим")[1].split("\n## ")[0].splitlines()
    numbered = set(steps(body))
    for mode in ("Разбор", "Интервью"):
        route = next(x for x in modes if x.startswith(f"- **{mode}"))
        assert expand(route.split("шаги", 1)[1]) == numbered, mode
    interview = body.split("\n## Интервью")[1].split("\n## ")[0]
    assert "просьбы о фото" in interview.split("2. **")[0]
    assert "в разделе 4, если `ask: now`" in steps(body)[6]


def test_quick_duplicate_waits_for_the_masters_choice_and_takes_the_ref_from_that_answer():
    contact = next(i for i in quick_items() if i.startswith("**Сохранение сразу после контакта"))
    after = contact.split("уже есть»", 1)[1]
    assert "«тот же клиент или новый»" in after and "по шагу 7" in after
    assert "из ответа после выбора" in after and "«Заказ <номер-имя>: бриф сохранён.»" in after


def test_quick_survey_does_not_ask_or_count_what_the_client_already_said():
    limits = next(i for i in quick_items() if i.startswith("**Лимиты"))
    assert "уже ответил, не задавай и не считай" in limits
    assert "Имя уже названо — спроси только телефон" in quick_questions()[0]


def test_eval_five_gives_an_answer_for_every_quick_question():
    prompt = json.loads(read("evals", "evals.json"))["evals"][4]["prompt"]
    for answer in (
        "+7 900",
        "прямая",
        "3 метра",
        "400 тысяч",
        "техника входит",
        "сборка нет",
        "к лету",
        "фото пришлёт позже",
    ):
        assert answer in prompt, answer
