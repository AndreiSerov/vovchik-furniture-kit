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
SECTIONS = ["Этап", "Версия проекта", "Устарело", "Следующий шаг", "Сделано"]


def skill_text(name: str = "kitchen-order") -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def description(name: str) -> str:
    front = skill_text(name).split("---")[1]
    return yaml.safe_load(front)["description"]


def body() -> str:
    return skill_text().split("---", 2)[2]


def run(args):
    return CliRunner().invoke(app, args)


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
    (found,) = [(t, s) for t, s in phrases().items() if t.startswith("Сколько стоит такая кухня?")]
    assert found[1] == "kitchen-brief"
    assert "до замера" in found[0]


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
    result = run(["status", "Петров"])
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
    real = {r.command for r in api.OUTPUT_RECOMPUTE.values()} | {
        "production"
    }  # production — «Документы цеха» в status
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
