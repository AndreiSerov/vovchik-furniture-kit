"""Тикет 31: скилл `kitchen-order` («продолжи заказ X»), триггеры пяти скиллов, сценарий через API.

Тексты скилла сверяются с настоящим выводом `kitchen status`: каждая цитата, раздел и код выхода,
которые скилл называет, есть в выводе команды. Выборки не пустые: пустой фильтр не должен проходить.
"""

import json
import re

import pytest
import yaml
from kitchen import api, skill_lint
from kitchen.cli import app
from typer.testing import CliRunner

from tests.conftest import REPO

SKILLS = REPO / "skills"
ORDER = "2026-047-petrov"
FIXTURES = REPO / "tests" / "fixtures"
FIVE = ["kitchen-brief", "kitchen-project", "kitchen-production", "kitchen-estimate", "kitchen-order"]
SECTIONS = ["Этап", "Версия проекта", "Устарело", "Следующий шаг", "Сделано", "Дальше"]


def skill_text(name: str = "kitchen-order") -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def description(name: str) -> str:
    front = skill_text(name).split("---")[1]
    return yaml.safe_load(front)["description"]


def body() -> str:
    return skill_text().split("---", 2)[2]


def run(args):
    return CliRunner().invoke(app, args)


def step(n: int) -> str:
    """Текст шага n раздела «## Шаги» (до следующего шага)."""
    steps = body().split("## Шаги")[1].split("## Ответ мастеру")[0]
    match = re.search(rf"^{n}\. (.*?)(?=^\d+\. |\Z)", steps, re.DOTALL | re.MULTILINE)
    assert match, f"нет шага {n}"
    return match.group(1)


def section(text: str, title: str) -> str:
    match = re.search(rf"^## {title}\n(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE)
    assert match, f"нет раздела «{title}»"
    return match.group(1).strip()


# --- текст скилла -----------------------------------------------------------------------------


def test_order_skill_has_only_name_and_description_and_passes_its_shape():
    assert "kitchen-order" in skill_lint.SHAPE_REQUIREMENTS
    assert "kitchen-order" not in skill_lint.SHAPE_EXEMPT
    assert skill_lint.lint() == []
    assert description("kitchen-order")


@pytest.mark.parametrize("name", FIVE)
def test_description_fits_the_format_limit(name):
    assert 0 < len(description(name)) <= 1024


def test_order_description_catches_its_phrases_and_names_the_other_four_skills():
    text = description("kitchen-order")
    for needle in ("продолжи заказ", "где мы остановились"):
        assert needle in text
    for other in FIVE[:-1]:
        assert other in text, other


@pytest.mark.parametrize("name", FIVE[:-1])
def test_every_other_skill_description_sends_the_order_question_to_kitchen_order(name):
    assert "kitchen-order" in description(name)


@pytest.mark.parametrize("name", FIVE[1:4])
def test_working_skills_name_all_neighbours_in_their_exclusions(name):
    text = description(name)
    others = [s for s in FIVE if s != name]
    assert len(others) == 4
    assert [s for s in others if s not in text] == []


def test_old_nameless_pointer_to_the_order_question_is_gone():
    for name in FIVE[1:4]:
        assert "где остановились по заказу»." not in description(name), name


def test_order_body_has_status_stale_mock_rule_and_both_exit_codes():
    text = body()
    assert "kitchen status <заказ>" in text
    assert "Устарело" in text
    assert "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" in text and "MOCKS.md" in text
    assert "Код выхода 1" in text and "Код выхода 2" in text
    assert "Пока ничего." in text  # «Устарело» и «Сделано» могут быть пустыми
    assert "Проекта ещё нет." in text


def test_order_body_has_no_banned_voice_and_no_numbers_from_the_engine():
    text = body()
    assert (
        re.search(r"(?i)(?<!\w)(мы|нам|наш(?:а|е|и|у|его|ему|ей|им|их|ими)?|давай\w*|продолжаем)(?!\w)", text)
        is None
    )
    assert skill_lint.check_no_formulas("kitchen-order", skill_text().splitlines()) == []


def test_log_is_written_only_for_decisions_and_never_as_done():
    text = body()
    assert re.search(r"kitchen log <заказ> \"<решение[^\"]*\" --kind decision", text)
    assert "--kind done" not in text
    assert "журнал не трогай" in text


def test_skill_never_points_to_a_first_or_last_line_of_output():
    assert re.search(r"(?i)(первая|последняя|первой|последней) строк", body()) is None


# --- триггеры ---------------------------------------------------------------------------------


def phrases() -> dict[str, str]:
    data = json.loads((SKILLS / "triggers.json").read_text(encoding="utf-8"))["phrases"]
    return {p["text"]: p["skill"] for p in data}


def test_trigger_set_has_enough_phrases_and_every_skill_has_two():
    by_text = phrases()
    assert len(by_text) >= 10
    for name in FIVE:
        assert sum(1 for s in by_text.values() if s == name) >= 2, name


@pytest.mark.parametrize(
    ("text", "skill"),
    [
        ("Продолжи заказ Петрова", "kitchen-order"),
        ("Где мы остановились с Петровым?", "kitchen-order"),
        ("Посчитай закупку Петрову", "kitchen-estimate"),
        ("Пересчитай раскрой Петрову", "kitchen-production"),
        ("Сколько выйдет Петров?", "kitchen-estimate"),
    ],
)
def test_boundary_phrases_are_expected_to_go_where_the_ticket_says(text, skill):
    assert phrases()[text] == skill


def test_client_question_before_measuring_goes_to_brief():
    (found,) = [(t, s) for t, s in phrases().items() if "Сколько стоит такая кухня?" in t]
    assert found[1] == "kitchen-brief"
    assert found[0] == "Клиент до замера пишет: «Сколько стоит такая кухня?»"


def test_brief_and_estimate_descriptions_separate_the_two_price_questions():
    brief, estimate = description("kitchen-brief"), description("kitchen-estimate")
    assert "заказа или проекта ещё нет" in brief
    assert "kitchen-estimate" in brief and "kitchen-order" in brief
    assert "сколько выйдет" in estimate


# --- настоящий вывод status -------------------------------------------------------------------


def test_sections_named_in_the_skill_exist_in_the_real_status(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    text = api.status(ORDER, home).text
    for title in SECTIONS:
        assert f"«{title}»" in body() or f"## {title}" in body(), title
        assert section(text, title)


def test_new_order_status_prints_the_empty_phrases_the_skill_quotes(home):
    api.order_new("2026-049-ivanov", client="Иванов", home=home)
    text = api.status("2026-049-ivanov", home).text
    assert section(text, "Версия проекта") == "Проекта ещё нет."
    assert section(text, "Устарело") == "Пока ничего."
    assert section(text, "Сделано") == "Пока ничего."


def test_done_entries_run_from_old_to_new_so_the_last_one_is_last_in_the_section(home):
    api.order_new(ORDER, home=home)
    api.log_add(ORDER, "done", "первое", home)
    api.log_add(ORDER, "done", "второе", home)
    done = section(api.status(ORDER, home).text, "Сделано").splitlines()
    assert done[-1].endswith("второе") and done[0].endswith("первое")


def test_banner_stands_at_both_ends_so_the_skill_names_sections_by_title(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    result = run(["status", ORDER])
    lines = result.output.strip().splitlines()
    assert lines[0] == lines[-1] == "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"


def test_missing_order_exits_one_with_the_quoted_message(home):
    result = run(["status", "Сидоров"])
    assert result.exit_code == 1
    assert "не найден" in result.output
    assert "«…» не найден" in body() and "Код выхода 1" in body()


def test_several_orders_exit_one_with_a_list_and_ask_for_the_number(home):
    api.order_new("2026-047-petrov", client="Петров П.", home=home)
    api.order_new("2026-048-petrov", client="Петров И.", home=home)
    result = run(["status", "Петров"])
    assert result.exit_code == 1
    assert "подходит к нескольким заказам: 2026-047-petrov, 2026-048-petrov" in result.output
    assert "Уточните номер" in result.output
    assert "подходит к нескольким заказам" in body()
    # по названному номеру статус читается
    assert run(["status", "2026-048-petrov"]).exit_code == 0


def test_genitive_surname_is_not_found_and_the_skill_says_to_use_the_nominative(home):
    api.order_new(ORDER, client="Петров П. П.", home=home)
    assert run(["status", "Петрова"]).exit_code == 1
    assert run(["status", "Петров"]).exit_code == 0
    text = body()
    assert "именительном падеже" in text and "«Петрова» → «Петров»" in text
    assert "«Петрова» она не найдёт" not in text


def test_no_workshop_exits_one_with_the_quoted_message(tmp_path, monkeypatch):
    monkeypatch.setenv("KITCHEN_HOME", str(tmp_path))
    result = run(["status", "Петров"])
    assert result.exit_code == 1
    assert "нет workshop/" in result.output
    assert "в этой папке нет workshop/" in body()


def test_status_without_an_order_exits_two(home):
    result = run(["status"])
    assert result.exit_code == 2


# --- передача профильному скиллу --------------------------------------------------------------


def handoff_table() -> dict[str, str]:
    rows = re.findall(r"^\s*\| «([^»]+)» \| (.+?) \|$", body(), re.MULTILINE)
    return dict(rows)


def routed_commands() -> dict[str, str]:
    """Команда из «Пересчитать устаревшее» -> скилл, как их назвал скилл."""
    line = next(ln for ln in body().splitlines() if ln.lstrip().startswith("- «Пересчитать устаревшее»"))
    routed: dict[str, str] = {}
    for part in line.split(". Остальные")[0].split(";"):
        target = re.search(r"`(kitchen-[a-z]+)`\s*$", part.strip().rstrip("."))
        assert target, part
        for command in re.findall(r"`kitchen ([a-z0-9]+)`", part):
            routed[command] = target.group(1)
    return routed


def skill_for(step_text: str, commands: list[str]) -> str | None:
    table = handoff_table()
    assert len(table) == 6
    for start, target in table.items():
        if not step_text.startswith(start):
            continue
        if start == "Пересчитать устаревшее":
            return routed_commands()[commands[0].split()[1]]
        match = re.fullmatch(r"`(kitchen-[a-z]+)`", target)
        return match.group(1) if match else None
    return None


def _import_v2(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    path = home / "project-v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, path, home=home)


def test_order_found_by_surname_then_next_step_follows_ticket_49_table_and_handoff(home):
    """«Продолжи заказ Петрова» на всех шагах жизни заказа: шаг из таблицы и скилл из таблицы скилла."""
    api.order_new(ORDER, client="Петров П. П.", home=home)
    seen: list[tuple[str, str | None]] = []

    def check(prefix: str, skill: str | None):
        result = api.status("Петров", home)  # по фамилии в именительном падеже
        assert result.order == ORDER
        step = section(result.text, "Следующий шаг")
        assert step == result.next_step.text
        assert step.startswith(prefix), step
        assert skill_for(step, result.next_step.commands) == skill, step
        seen.append((prefix, skill))
        return result

    check("Собрать бриф", "kitchen-brief")
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    check("Сделать проект", "kitchen-project")
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    check("Смета и КП", "kitchen-estimate")
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 35, home)
    check("Документы цеха", "kitchen-production")
    api.production_and_save(ORDER, home)
    done = check("Все документы готовы", None)
    assert section(done.text, "Устарело") == "Пока ничего."
    _import_v2(home)
    stale = check("Пересчитать устаревшее", "kitchen-estimate")
    assert stale.next_step.commands[0] == f"kitchen estimate {ORDER}"
    assert f"kitchen estimate {ORDER}" in section(stale.text, "Устарело")
    assert len(seen) == 6


def test_order_with_project_and_no_brief_still_has_a_step(home):
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    result = api.status(ORDER, home)
    assert section(result.text, "Версия проекта").startswith("Версия 1")
    assert skill_for(result.next_step.text, result.next_step.commands) == "kitchen-estimate"


def test_every_recompute_command_in_stale_lines_is_routed_by_the_skill():
    routed = routed_commands()
    real = {r.command for r in api.OUTPUT_RECOMPUTE.values()} | {api.PRODUCTION_RECOMPUTE.command}
    assert len(real) >= 8
    assert real <= set(routed), real - set(routed)
    for command in ("estimate", "purchase", "quote"):
        assert routed[command] == "kitchen-estimate"
    for command in ("production", "cut", "edges", "assembly", "trip", "drilling"):
        assert routed[command] == "kitchen-production"
    assert routed["3d"] == "kitchen-project"


def test_workshop_not_found_message_is_the_one_the_skill_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("KITCHEN_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    result = run(["status", "Петров"])
    assert result.exit_code == 1
    assert "Мастерская не найдена" in result.output
    assert "Мастерская не найдена" in body()


# --- ревью 1 ----------------------------------------------------------------------------------


def test_scenario_status_has_the_right_dalshe_section(home):
    api.order_new(ORDER, client="Петров П. П.", home=home)
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    api.estimate_and_save(ORDER, home)
    api.quote_and_save(ORDER, 35, home)
    api.production_and_save(ORDER, home)
    api.log_add(
        ORDER, "next", "согласовать фасады", home
    )  # импорт, смета, КП и производство «Дальше» сбрасывают (57)
    assert section(api.status("Петров", home).text, "Дальше").endswith("согласовать фасады")
    api.log_add(ORDER, "done", "фасады согласованы", home)
    assert section(api.status("Петров", home).text, "Дальше") == "Пока ничего."


def test_step_two_reads_the_dalshe_section_and_skips_it_when_empty():
    text = step(2)
    assert "Записано дальше: <строки раздела «Дальше»>" in text
    assert "Пока ничего." in text


def test_order_number_without_year_exits_one_and_the_skill_has_a_branch_for_it(home):
    api.order_new(ORDER, client="Петров П. П.", home=home)
    result = run(["status", "047"])
    assert result.exit_code == 1
    assert "похоже на номер заказа" in result.output
    assert "ГГГГ-NNN-фамилия" in result.output
    branch = step(1)
    assert "похоже на номер заказа" in branch
    assert "полный номер или фамилию" in branch


def test_unknown_error_with_code_one_is_shown_as_is_and_stops():
    assert "другое сообщение с кодом 1 — как есть, остановись" in step(1)


@pytest.mark.parametrize("name", ["kitchen-project", "kitchen-production", "kitchen-estimate"])
def test_working_skills_do_not_promise_every_form_of_the_order_name(name):
    text = skill_text(name)
    assert "поймут любой вариант" not in text
    assert "именительном падеже" in text


def test_acceptance_line_of_ticket_31_uses_the_nominative():
    spec = (REPO / "docs" / "specs" / "stage-6-skills.md").read_text(encoding="utf-8")
    ticket = spec.split("## Тикет 31")[1].split("## Тикет 49")[0]
    assert 'api.status("Петрова")' not in ticket
    assert 'api.status("Петров")' in ticket


def test_decisions_do_not_contradict_the_trigger_rule_and_shape_exemption():
    decisions = (REPO / "DECISIONS.md").read_text(encoding="utf-8")
    assert "Нет файла или пустой набор проходит (до тикета 31)" not in decisions
    assert "Сейчас в наборе три фразы" not in decisions
    assert "`kitchen-brief` и `kitchen-order` в `SHAPE_EXEMPT`" not in decisions


def test_step_two_pins_the_parts_of_the_answer():
    text = step(2)
    assert "последняя запись списка в разделе «Сделано»" in text
    assert "Дальше: <раздел «Следующий шаг» как есть>" in text
    assert "«пока ничего»" in text and "«проекта ещё нет»" in text
    assert "Сделано последним" in text and "Устарело" in text


def test_step_three_asks_before_handing_over_and_does_not_choose_alone():
    text = step(3)
    assert "Передать работу скиллу <имя>?" in text
    assert "После согласия мастера" in text
    assert "Текст шага не из таблицы" in text and "Скилл не выбирай сам" in text
    assert "Мастер хочет другое" in text


def test_step_one_waits_for_the_master_and_does_not_guess_or_create():
    text = step(1)
    assert "Жди ответа" in text and "Номер не угадывай" in text and "Заказ сам не заводи" in text
    assert "потом повтори шаг 1" in text


def test_mock_refusal_names_telegram_and_vk():
    text = body()
    assert "На «отправь клиенту всё равно»" in text
    assert "Telegram или VK" in text and "клиенту ничего не отправляю" in text


def test_step_four_writes_next_from_the_masters_own_words():
    assert 'kitchen log <заказ> "<шаг>" --kind next' in step(4)


def test_stale_drilling_map_is_only_shown_never_started():
    text = step(3)
    assert "показать, не запускать" in text and "`kitchen drilling`" in text
    assert "статус прочитается заново" not in text


def test_answer_parts_are_listed_in_one_place():
    tail = body().split("## Ответ мастеру")[1]
    assert "шаге 2" in tail
    assert "этап, версия" not in tail


def test_decisions_31_match_the_repo():
    d = (REPO / "DECISIONS.md").read_text(encoding="utf-8")
    assert "`kitchen-order` в него не входил" not in d
    if list(SKILLS.glob("triggers-results-*.md")):
        assert "выбор скилла по фразам не прогонялся" not in d


@pytest.mark.parametrize("name", ["kitchen-project", "kitchen-production", "kitchen-estimate"])
def test_working_skills_carry_the_same_nominative_sentence(name):
    assert (
        "Заказ называй номером, номером-фамилией или фамилией в именительном падеже"
        " («Петров», не «Петрова»): команды падежи не склоняют."
    ) in skill_text(name)


def test_not_found_branch_retries_once_then_asks_and_repeats_step_one():
    (branch,) = [ln for ln in step(1).splitlines() if "не найден»" in ln]
    assert "один раз повтори команду с именительным («Петрова» → «Петров»)" in branch
    assert "когда мастер ответит, потом повтори шаг 1 с его названием" in branch


def test_short_number_branch_asks_the_master_and_does_not_guess():
    (branch,) = [ln for ln in step(1).splitlines() if "похоже на номер заказа" in ln]
    assert "покажи сообщение как есть и спроси полный номер или фамилию. Жди ответа" in branch


def test_exit_two_and_missing_workshop_stop_or_ask_and_never_guess():
    text = step(1)
    assert (
        "Код выхода 2 — команда не получила заказ (не назван). Спроси мастера, какой заказ, и повтори."
        in text
    )
    assert "покажи сообщение как есть и остановись: без папки мастерской статус не прочитать" in text
    assert (
        "Любое другое сообщение с кодом 1 — как есть, остановись: сообщение скажи мастеру дословно, догадок не добавляй."
        in text
    )


def test_step_two_reads_version_from_its_section_and_says_nothing_for_empty_stale():
    text = step(2)
    assert "Версия: из раздела «Версия проекта» («Версия N от …»)" in text
    assert "Устарело: строки раздела «Устарело» как есть. Если там «Пока ничего.» — скажи «ничего»." in text
    assert "Если там «Пока ничего.» — эту часть пропусти." in text
    assert "«## Дальше»" in body()


def test_all_documents_ready_is_handed_to_nobody():
    assert handoff_table()["Все документы готовы"].startswith("ничего не передавай")


def test_drilling_map_is_never_started_without_a_direct_request():
    assert "карту присадки без прямой просьбы мастера не запускай и не предлагай" in step(3)


def test_no_log_entry_when_the_master_decided_nothing():
    text = step(4)
    assert "Мастер ничего не решал — журнал не трогай" in text
    assert text.count("kitchen log <заказ>") == 2
