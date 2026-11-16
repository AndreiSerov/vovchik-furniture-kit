"""Тикет 43: `kitchen-brief` говорит голосом мастера, даёт одну вилку и сохраняет бриф в заказ.

Статические проверки текста скилла и контрактные: каждая строка вывода, которую скилл цитирует,
есть в настоящем выводе команды. Утверждения о присутствии идут рядом с утверждениями об отсутствии:
фильтр, который ничего не выбрал, не должен проходить.
"""

import json
import re
import subprocess
import sys

import pytest
from kitchen import api, skill_lint
from kitchen.cli import app
from typer.testing import CliRunner

from tests.conftest import REPO

SKILL = REPO / "skills" / "kitchen-brief"
CHECK_BRIEF = SKILL / "scripts" / "check_brief.py"
BANNED = re.compile(r"салон|дизайнер|менеджер", re.IGNORECASE)

PHRASE_CLIENT = "ориентир до замера: от <total_low> до <total_high> ₽"
PHRASE_MOCK = "вилка посчитана на мок-ценах, клиенту не отправляю; замените pricing.yaml (MOCKS.md)"
PHRASE_PREMIUM = "с такими материалами цена будет выше этой вилки, точно скажу после проекта"
PHRASE_DISHWASHER = "посудомойку лучше поставить рядом с мойкой — короткий слив"
STEP_COMMANDS = {
    "check_brief": "python3 scripts/check_brief.py",
    "quick-estimate": "kitchen quick-estimate --meters <summary.running_meters> --json",
    "save": "kitchen brief save",
    "log": "--kind next",
}


def read(*parts: str) -> str:
    return SKILL.joinpath(*parts).read_text(encoding="utf-8")


def skill_files() -> list:
    files = [
        SKILL / "SKILL.md",
        *sorted((SKILL / "references").glob("*.md")),
        SKILL / "assets" / "brief_template.md",
        SKILL / "evals" / "evals.json",
        CHECK_BRIEF,
    ]
    return files


def test_master_voice_files_exist_and_have_no_salon_words():
    files = skill_files()
    names = {f.name for f in files}
    assert {"SKILL.md", "questions.md", "rules.md", "options.md", "brief_template.md"} <= names
    assert {"evals.json", "check_brief.py"} <= names
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert text.strip(), path
        found = [(n, line) for n, line in enumerate(text.splitlines(), 1) if BANNED.search(line)]
        assert not found, f"{path.name}: {found}"


def test_salon_word_filter_is_not_vacuous():
    assert BANNED.search("наш Салон") and BANNED.search("дизайнера") and BANNED.search("менеджер")
    assert not BANNED.search("мастер, замер")


def test_salon_value_stays_in_the_schema_for_old_briefs():
    schema = read("references", "brief_schema.json")
    assert "salon" in schema and "старых брифах" in schema


def test_description_is_in_the_masters_voice_and_still_triggers_on_a_client_message():
    body = read("SKILL.md")
    description = body.split("---")[1]
    assert "мастер" in description
    assert "переписку с клиентом" in description
    assert "до замера" in description


def test_skill_speaks_in_first_person_of_the_master():
    body = read("SKILL.md")
    assert "я сам замеряю, делаю и ставлю" in body
    assert "приеду на замер" in body


def test_real_skill_passes_lint_and_is_not_exempt_from_shape():
    assert "kitchen-brief" not in skill_lint.SHAPE_EXEMPT
    assert skill_lint.lint() == []
    reqs = skill_lint.SHAPE_REQUIREMENTS["kitchen-brief"]
    assert len(reqs) == 3  # brief save, --kind next, МОК-ДАННЫЕ


def test_levels_and_the_price_table_are_gone_everywhere():
    settings = json.loads(read("references", "settings.json"))
    assert "price_ranges_per_meter" not in settings
    assert settings["min_budget_to_ceiling_per_reference_length"] > 0  # остальные пороги остались
    assert settings["min_budget_standard_per_reference_length"] > 0
    assert settings["max_client_questions"] == 15
    for path in skill_files() + [SKILL / "references" / "settings.json"]:
        text = path.read_text(encoding="utf-8")
        assert "price_ranges" not in text, path.name
        assert not re.search(r"\b(economy|middle)\b", text.replace("economy.yaml", "")), path.name
        assert not re.search(r"\bpremium\b|517 500|настроек салона", text, re.IGNORECASE), path.name


def test_one_fork_from_quick_estimate_with_the_three_phrases():
    body = read("SKILL.md")
    for phrase in (PHRASE_CLIENT, PHRASE_MOCK, PHRASE_PREMIUM, PHRASE_DISHWASHER):
        assert phrase in body, phrase
    assert STEP_COMMANDS["quick-estimate"] in body
    assert '"mock": true' in body
    assert "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" in body
    assert not re.search(r"перв(?:ая|ой) строк", body.lower())  # первая строка вывода — плашка мока


def test_mock_refusal_covers_telegram_and_vk():
    body = read("SKILL.md")
    assert "отправь всё равно" in body
    assert "Telegram" in body and "VK" in body


def test_dishwasher_advice_is_in_the_interview_and_in_the_client_text_of_the_template():
    assert PHRASE_DISHWASHER in read("SKILL.md")
    assert "посудомойк" in read("assets", "brief_template.md").lower()
    assert "рядом с мойкой" in read("assets", "brief_template.md")


def test_skill_writes_only_next_and_decision_log_entries():
    body = read("SKILL.md")
    kinds = set(re.findall(r"--kind[ =](\w+)", body))
    assert kinds == {"next", "decision"}
    assert "`done` пишет сама команда" in body


# ---- маршруты: каждый маршрут доходит до каждого обязательного шага ----


def steps(body: str) -> dict[int, str]:
    section = body.split("## Шаги")[1].split("\n## ")[0]
    parts = re.split(r"^(\d+)\. \*\*", section, flags=re.MULTILINE)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def expand(route: str) -> set[int]:
    got: set[int] = set()
    for a, b in re.findall(r"(\d+)(?:[–-](\d+))?", route):
        got |= set(range(int(a), int(b or a) + 1))
    return got


def test_steps_are_numbered_without_gaps_and_hold_the_mandatory_commands():
    numbered = steps(read("SKILL.md"))
    assert sorted(numbered) == list(range(1, len(numbered) + 1))
    where = {key: [n for n, text in numbered.items() if cmd in text] for key, cmd in STEP_COMMANDS.items()}
    assert all(where.values()), where
    assert len(numbered) >= 7


@pytest.mark.parametrize("mode", ["Разбор", "Интервью", "Быстрый опрос"])
def test_every_mode_route_reaches_check_fork_save_and_log(mode):
    body = read("SKILL.md")
    numbered = steps(body)
    routes = [
        line
        for line in body.split("## Режим")[1].split("\n## ")[0].splitlines()
        if line.startswith(f"- **{mode}")
    ]
    assert len(routes) == 1
    reached = expand(routes[0].split("шаги", 1)[1])
    for key, cmd in STEP_COMMANDS.items():
        needed = {n for n, text in numbered.items() if cmd in text}
        assert needed <= reached, (mode, key, needed, reached)


def test_no_line_jumps_past_a_mandatory_step():
    numbered = steps(read("SKILL.md"))
    last = max(numbered)
    save = next(n for n, t in numbered.items() if STEP_COMMANDS["save"] in t)
    log = next(n for n, t in numbered.items() if STEP_COMMANDS["log"] in t)
    assert save < log == last
    refs = [
        (n, int(target))
        for n, text in numbered.items()
        if n < save
        for target in re.findall(r"шаг(?:у|а|е|и)? (\d+)", text)
    ]
    assert refs  # фильтр не пустой: иначе проверка проходит ни о чём
    assert all(target <= save for _, target in refs), refs


# ---- признаки готовности держатся на выводе ошибок ----


def test_each_command_has_instructions_for_exit_1_and_exit_2():
    numbered = steps(read("SKILL.md"))
    estimate = next(t for t in numbered.values() if STEP_COMMANDS["quick-estimate"] in t)
    save = next(t for t in numbered.values() if STEP_COMMANDS["save"] in t)
    check = next(t for t in numbered.values() if STEP_COMMANDS["check_brief"] in t)
    for text in (estimate, save):
        assert "Код выхода 1" in text and "Код выхода 2" in text
    assert "Код выхода 1" in check and "`errors`" in check
    assert "заказ с фамилией" in save.lower()
    assert "Бриф не сохранён" in save
    assert "`summary.running_meters`" in estimate
    assert "null" in estimate


def run(args):
    return CliRunner().invoke(app, args)


def test_quoted_save_and_log_lines_are_printed_by_the_commands(home):
    brief = REPO / "tests" / "fixtures" / "petrov_brief.json"
    body = read("SKILL.md")
    first = run(["brief", "save", "ivanov", str(brief)])
    assert first.exit_code == 0
    assert "бриф сохранён" in first.output and "бриф сохранён" in body
    again = run(["brief", "save", "ivanov", str(brief)])
    assert again.exit_code == 1
    assert "Заказ с фамилией «ivanov» уже есть" in again.output
    assert "Заказ с фамилией «…» уже есть" in body
    assert "Чтобы обновить его бриф:" in again.output and "Чтобы завести новый:" in again.output
    assert "Чтобы обновить его бриф" in body and "Чтобы завести новый" in body
    ref = re.search(r"Заказ (\S+)", first.output).group(1)
    logged = run(["log", ref, "дальше", "--kind", "next"])
    assert logged.exit_code == 0 and "записано" in logged.output and "записано" in body
    decided = run(["log", ref, "решение", "--kind", "decision"])
    assert decided.exit_code == 0 and "записано" in decided.output


def test_brief_rejected_by_save_prints_the_quoted_texts(home, tmp_path):
    brief = json.loads((REPO / "tests" / "fixtures" / "petrov_brief.json").read_text(encoding="utf-8"))
    brief["storage"] = {"over_fridge": {"value": "bogus", "source": "client"}}
    path = tmp_path / "bad_enum.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    result = run(["brief", "save", "petrov", str(path)])
    assert result.exit_code == 1
    assert "Ошибка в брифе:" in result.output and "Бриф не сохранён." in result.output
    assert "Ошибка в брифе" in read("SKILL.md")


def test_quick_estimate_keys_and_errors_match_what_the_skill_quotes(home):
    ok = run(["quick-estimate", "--meters", "4.6", "--json"])
    assert ok.exit_code == 0
    data = json.loads(ok.output)
    assert {"total_low", "total_high", "mock"} <= set(data)
    assert data["mock"] is True  # песочница: мок; скилл ловит именно этот ключ
    zero = run(["quick-estimate", "--meters", "0", "--json"])
    assert zero.exit_code == 1
    assert "Длина кухни в погонных метрах должна быть числом больше нуля." in zero.output
    missing = run(["quick-estimate"])
    assert missing.exit_code == 2
    bad = run(["quick-estimate", "--meters", "abc"])
    assert bad.exit_code == 2


def test_check_brief_reports_running_meters_and_errors_with_exit_0(tmp_path):
    brief = REPO / "tests" / "fixtures" / "petrov_brief.json"
    done = subprocess.run(
        [sys.executable, str(CHECK_BRIEF), str(brief)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0
    report = json.loads(done.stdout)
    assert {"summary", "errors", "conflicts", "missing_required"} <= set(report)
    assert report["summary"]["running_meters"] is not None
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(CHECK_BRIEF), str(empty)], capture_output=True, text=True, check=False
    )
    assert json.loads(done.stdout)["summary"]["running_meters"] is None  # вилки нет: скилл просит длины


def test_api_names_the_mock_flag_of_the_fork(home):
    assert api.quick_estimate(4.6, home).mock is True


# ---- шаблон, вопросы ----


def test_template_has_workzone_and_installation_sections_with_dash_for_empty():
    template = read("assets", "brief_template.md")
    assert "**Рабочая зона.**" in template
    assert "**Монтаж.**" in template
    low = template.lower()
    assert "парковка" in low and "демонтаж" in low and "ровность" in low
    assert "мойка" in template.split("**Рабочая зона.**")[1].split("**")[0].lower()
    assert "«—»" in template


def test_template_fridge_line_and_section_6_compare_with_the_fork_in_words():
    template = read("assets", "brief_template.md")
    assert "стена A, слева / справа" in template and "не на кухне" in template
    assert "антресоль над ним: да / нет / не решено" in template
    section6 = template.split("## 6.")[1]
    for word in ("ниже вилки", "внутри вилки", "выше вилки"):
        assert word in section6, word
    assert "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" in section6
    assert "без деления на метры" in section6


def test_mandatory_interview_part_holds_the_fridge_and_antresol_questions():
    questions = read("references", "questions.md")
    _, mandatory = questions.split("## Обязательная часть")
    mandatory = mandatory.split("\n## ")[0]
    assert "appliances.fridge" in mandatory and "storage.over_fridge" in mandatory
    assert "холодильник" in mandatory and "антресол" in mandatory
    assert "с какого края" in mandatory or "слева или справа" in mandatory
    assert PHRASE_DISHWASHER in mandatory
    body = read("SKILL.md")
    interview = body.split("## Интервью")[1]
    assert "Обязательная часть" in interview and "не переносится" in interview


# ---- эвалы ----


def test_evals_one_to_three_use_the_fork_and_the_fourth_is_the_interview():
    data = json.loads(read("evals", "evals.json"))
    assert data["skill_name"] == "kitchen-brief"
    evals = data["evals"]
    assert [e["id"] for e in evals] == [1, 2, 3, 4, 5]
    blob = json.dumps(data, ensure_ascii=False)
    assert "517 500" not in blob and "настроек салона" not in blob
    for case in evals:
        assert "scripts/petrov-sandbox" in case["setup"]
        assert len(case["assertions"]) >= 5
        assert "kitchen brief save" in " ".join(case["assertions"])
    for case in evals[:3]:  # эвал 5 — быстрый опрос, его проверяет test_quick_survey_eval
        text = " ".join(case["assertions"])
        assert "quick-estimate" in text
    assert any("мок" in a.lower() and "клиент" in a for a in evals[1]["assertions"])
    assert any("выше этой вилки" in a for a in evals[2]["assertions"])
    interview = " ".join(evals[3]["assertions"]).lower()
    for needle in ("до потолка", "60 см", "стен", "край", "антресол", "рядом с мойкой", "голос мастера"):
        assert needle in interview, needle
    assert "Хочу кухню" in evals[3]["prompt"]


# ---- ревью 1: ветки вилки, дубль фамилии, тексты, коды выхода ----


def fork_lines() -> list[str]:
    step = steps(read("SKILL.md"))[5]
    lines = [line.strip() for line in step.splitlines() if line.strip().startswith("- ")]
    assert lines
    return lines


def test_mock_branch_keeps_the_fork_from_the_client():
    mock = [line for line in fork_lines() if line.startswith('- `"mock": true`')]
    real = [line for line in fork_lines() if line.startswith('- `"mock": false`')]
    assert len(mock) == 1 and len(real) == 1
    assert "клиенту вилку не называй" in mock[0]
    assert PHRASE_CLIENT not in mock[0]
    assert PHRASE_CLIENT in real[0]


def test_premium_phrase_reaches_the_client_only_without_mock_and_is_written_verbatim():
    premium = [line for line in fork_lines() if PHRASE_PREMIUM in line]
    assert len(premium) == 1
    assert "вилка клиенту выдана" in premium[0]
    assert "На моке клиенту этого не говори" in premium[0]
    assert "дословно" in premium[0]
    step6 = steps(read("SKILL.md"))[6]
    assert PHRASE_PREMIUM in step6 and "дословно" in step6


def test_null_meters_runs_no_command():
    null = [line for line in fork_lines() if "равно `null`" in line]
    assert len(null) == 1
    assert "Команду не запускай" in null[0] and "kitchen quick-estimate" not in null[0]


def test_client_price_answers_do_not_contradict_each_other():
    body = read("SKILL.md")
    assert "назову, когда будут длины стен" not in body
    assert "ориентир по цене назову" not in body
    null = next(line for line in fork_lines() if "равно `null`" in line)
    assert "нужны длины стен" in null
    mock = next(line for line in fork_lines() if line.startswith('- `"mock": true`'))
    assert "цену назову после замера" in mock


def test_fork_is_computed_for_a_budget_but_given_to_the_client_only_on_a_price_question():
    step = steps(read("SKILL.md"))[5]
    assert "Считай, если клиент спросил о цене или в брифе есть бюджет" in step
    assert "Клиенту вилку называй, только если он спросил о цене" in step
    assert "Если клиент спросил о цене и вилка не на моке" in read("assets", "brief_template.md")


def test_interview_brief_step_reaches_save_and_log():
    interview = read("SKILL.md").split("## Интервью")[1]
    last = [line for line in interview.splitlines() if line.startswith("8. **Бриф.**")]
    assert len(last) == 1
    assert "шаги 2–8" in last[0]


def dup_line() -> str:
    save = steps(read("SKILL.md"))[7]
    dup = [line for line in save.splitlines() if "Заказ с фамилией" in line]
    assert len(dup) == 1
    return dup[0]


def test_duplicate_surname_asks_the_master_and_runs_only_printed_commands():
    line = dup_line()
    assert "«Это тот же клиент или новый?»" in line
    assert "Свою команду не придумывай" in line


def test_duplicate_surname_names_the_list_line_and_asks_which_of_several_orders(home):
    line = dup_line()
    assert not re.search(r"перв(?:ая|ой) строк", line.lower())
    assert "Заказ с фамилией" in line and "какой номер" in line
    brief = REPO / "tests" / "fixtures" / "petrov_brief.json"
    for ref in ("2026-047-petrov", "2026-049-petrov"):  # два заказа Петрова: в сообщении оба номера
        assert run(["brief", "save", ref, str(brief)]).exit_code == 0
    again = run(["brief", "save", "petrov", str(brief)])
    assert again.exit_code == 1
    assert "уже есть: 2026-047-petrov, 2026-049-petrov." in again.output
    assert "уже есть:" not in again.output.splitlines()[0]  # первая строка — плашка мока, а не список


def test_save_step_names_both_rejection_messages():
    save = steps(read("SKILL.md"))[7]
    assert "Ошибка в брифе" in save and "Неизвестное поле брифа" in save
    assert "Бриф не сохранён" in save


def test_steps_have_a_ready_sign():
    numbered = steps(read("SKILL.md"))
    for n in (2, 3, 4, 5, 6, 7, 8):
        assert re.search(r"Готово, когда", numbered[n]), n


def test_mandatory_fridge_questions_use_the_ticket_30_texts_exactly_as_check_brief_prints_them():
    mandatory = read("references", "questions.md").split("## Обязательная часть")[1].split("\n## ")[0]
    printed = CHECK_BRIEF.read_text(encoding="utf-8")
    fridge = "У какой стены и с какого края будет стоять холодильник, если стоять лицом к стене? Какой он ширины и высоты?"
    over = "Над холодильником можно сделать шкаф до потолка — нужен?"
    assert f"«{fridge}»" in mandatory and f"«{over}»" in mandatory
    # check_brief печатает тот же текст (в коде он разбит на строки: склеиваем соседние литералы)
    joined = re.sub(r'"\s*\n\s*"', "", printed)
    assert fridge in joined and over in joined


def test_antresol_word_is_not_in_any_client_quote_of_the_mandatory_part():
    mandatory = read("references", "questions.md").split("## Обязательная часть")[1].split("\n## ")[0]
    quotes = re.findall(r"«([^»]*)»", mandatory)
    assert len(quotes) >= 3
    assert not [q for q in quotes if "антресол" in q.lower()]


def test_fridge_wording_lives_in_one_place():
    questions = read("references", "questions.md")
    for text in ("У какой стены и с какого края", "Над холодильником можно сделать шкаф"):
        holders = [
            p.name for p in skill_files() if p.suffix == ".md" and text in p.read_text(encoding="utf-8")
        ]
        assert holders == ["questions.md"], (text, holders)
    assert "У какой стены" in questions


FIRM_VOICE = re.compile(
    r"\b(подскажем|расскажем|сделаем|посчитаем|назовём|пришлём|приедем|уточним|замерим|измерим|"
    r"замер делаем|закладываем|переносим|спрашиваем)\b",
    re.IGNORECASE,
)


def test_no_first_person_plural_in_client_texts_and_the_filter_scans_something():
    assert FIRM_VOICE.search("Если не решили — расскажем плюсы") and FIRM_VOICE.search("Подскажем размеры")
    assert not FIRM_VOICE.search("расскажу, подскажу, приеду")
    scanned = 0
    for path in skill_files():
        if path.suffix == ".json":
            continue
        text = path.read_text(encoding="utf-8")
        scanned += len(text)
        found = [
            (n, m.group(0)) for n, line in enumerate(text.splitlines(), 1) for m in FIRM_VOICE.finditer(line)
        ]
        assert not found, f"{path.name}: {found}"
    assert scanned > 10_000


def test_eval_four_client_asks_the_price_and_the_order_is_smirnova():
    case = json.loads(read("evals", "evals.json"))["evals"][3]
    blob = json.dumps(case, ensure_ascii=False)
    assert "smirnova" in blob
    assert not re.search(r"smirnov(?!a)", blob)
    assert "Сколько" in case["prompt"] and "стоить" in case["prompt"]


def run_check_brief(*args):
    return subprocess.run(
        [sys.executable, str(CHECK_BRIEF), *args], capture_output=True, text=True, check=False
    )


def test_check_brief_exit_codes_the_skill_quotes(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"storage": {"over_fridge": {"value": "bogus", "source": "client"}}}), encoding="utf-8"
    )
    done = run_check_brief(str(bad))
    assert done.returncode == 0
    assert json.loads(done.stdout)["errors"]  # ошибки есть, а код 0
    missing = run_check_brief(str(tmp_path / "nope.json"))
    assert missing.returncode == 1 and "Traceback" in missing.stderr
    assert run_check_brief().returncode == 2


def test_fork_keys_quoted_by_the_skill_are_printed_by_quick_estimate(home):
    data = json.loads(run(["quick-estimate", "--meters", "4.0", "--json"]).output)
    ready = [
        line for line in steps(read("SKILL.md"))[5].splitlines() if "Готово, когда в JSON есть ключи" in line
    ]
    assert len(ready) == 1
    keys = re.findall(r"`(\w+)`", ready[0])
    assert {"total_low", "total_high", "mock"} <= set(keys)
    assert set(keys) <= set(data), set(keys) - set(data)


def test_template_section_6_carries_the_premium_phrase_verbatim():
    section6 = read("assets", "brief_template.md").split("## 6.")[1]
    assert PHRASE_PREMIUM in section6
    assert "«с такими материалами цена будет выше вилки»" not in section6


def test_antresol_word_is_not_in_the_client_why_of_the_mandatory_part():
    questions = read("references", "questions.md")
    assert "говорить клиенту" in questions.split("## 0.")[0]  # «Зачем» — текст клиенту
    mandatory = questions.split("## Обязательная часть")[1].split("\n## ")[0]
    whys = re.findall(r"\*Зачем:\*([^(\n]*)", mandatory)
    assert len(whys) >= 3
    assert not [w for w in whys if "антресол" in w.lower()], whys


def test_mock_phrase_sits_only_in_the_mock_branch():
    mock = next(l for l in fork_lines() if l.startswith('- `"mock": true`'))
    real = next(l for l in fork_lines() if l.startswith('- `"mock": false`'))
    assert PHRASE_MOCK in mock and PHRASE_MOCK not in real


def test_template_section_4_keeps_the_fork_from_the_client_on_mock():
    section4 = read("assets", "brief_template.md").split("## 4.")[1].split("\n## ")[0]
    assert "на моке цену клиенту не называть" in section4 and "«цену назову после замера»" in section4


def test_duplicate_surname_never_lets_the_agent_pick_a_number():
    assert "сам не выбирай" in dup_line()


def test_check_brief_step_says_exit_0_with_errors():
    check = next(t for t in steps(read("SKILL.md")).values() if "python3 scripts/check_brief.py" in t)
    assert "Код выхода 0 — при любом брифе" in check


def test_interview_mandatory_part_is_never_dropped_by_the_limit():
    interview = read("SKILL.md").split("## Интервью")[1]
    rule = next(l for l in interview.splitlines() if l.startswith("2. **Обязательная часть"))
    assert "в лимит не отбрасывается" in rule
    assert "отложить" not in rule and "можно" not in rule.split(":**")[0]


def test_save_ref_is_taken_from_the_saved_line_not_from_a_banner_line():
    save = steps(read("SKILL.md"))[7]
    assert "из этой строки" in save
    assert not re.search(r"(перв|последн)\w* строк", save.lower())


def test_unknown_field_text_quoted_by_the_skill_is_printed_by_brief_save(home, tmp_path):
    brief = json.loads((REPO / "tests" / "fixtures" / "petrov_brief.json").read_text(encoding="utf-8"))
    brief["room"]["foo"] = {"value": 1, "source": "client"}
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    result = CliRunner().invoke(app, ["brief", "save", "petrov", str(path)])
    assert result.exit_code == 1
    assert "Неизвестное поле брифа: room.foo (опечатка?)." in result.output
    assert "Бриф не сохранён." in result.output
    quoted = re.findall(r"«(Неизвестное поле[^»]*)»", steps(read("SKILL.md"))[7])
    assert quoted == ["Неизвестное поле брифа: … (опечатка?)."]


def test_log_step_runs_only_after_save():
    assert "Только после сохранения" in steps(read("SKILL.md"))[8]


# ---- тикет 44: быстрый опрос ----

QUICK_TRIGGER = "Клиент с сайта спрашивает, сколько стоит кухня"
ESTIMATE_TRIGGER = "Сколько выйдет кухня Петрова"


def quick_section() -> str:
    body = read("SKILL.md")
    assert body.count("\n## Быстрый опрос") == 1
    return body.split("\n## Быстрый опрос")[1].split("\n## ")[0]


def quick_questions() -> list[str]:
    text = read("references", "questions.md")
    assert text.count("\n## Быстрый опрос") == 1
    section = text.split("\n## Быстрый опрос")[1].split("\n## ")[0]
    items = re.findall(r"^(\d)\. (.+)$", section, flags=re.MULTILINE)
    assert [n for n, _ in items] == ["1", "2", "3", "4", "5"]
    return [t for _, t in items]


def test_quick_survey_is_the_third_mode_with_its_triggers():
    modes = read("SKILL.md").split("## Режим")[1].split("\n## ")[0]
    line = next(x for x in modes.splitlines() if x.startswith("- **Быстрый опрос"))
    for needle in ("с сайта", "из мессенджера", "быстро"):
        assert needle in line, needle
    description = read("SKILL.md").split("---")[1]
    assert "с сайта" in description and "быстрый опрос" in description.lower()


def test_quick_survey_limits_and_counter_line():
    section = quick_section()
    assert "не больше 5 вопросов" in section
    assert "не больше 2 вопросов в сообщении" in section
    assert "(N из 5 вопросов)" in section
    body = read("SKILL.md")
    rules = body.split("## Правила поведения")[1].split("\n## ")[0]
    assert "быстрого опроса" in rules  # общие «лимит» и «фото всегда» знают исключение


def test_quick_survey_questions_come_in_the_ticket_order_and_photos_are_fifth():
    name, walls, budget, deadline, photo = (q.lower() for q in quick_questions())
    assert "имя" in name and "телефон" in name
    assert "длин" in walls and "стен" in walls
    assert "техник" in budget and "сборк" in budget
    assert "срок" in deadline
    assert "фото" in photo and "позже" in photo
    assert "подскажем" not in f"{name} {walls} {budget} {deadline} {photo}"


def test_quick_survey_does_not_repeat_the_client_wording_of_the_fork_or_the_duplicate_rule():
    section = quick_section()
    body = read("SKILL.md")
    assert "шагу 5" in section
    assert "шагу 7" in section
    for phrase in (PHRASE_CLIENT, PHRASE_MOCK, PHRASE_PREMIUM, "Это тот же клиент или новый?"):
        assert phrase not in section, phrase
        assert body.count(phrase) >= 1, phrase
    assert "Это тот же клиент или новый?" in body
    assert "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" not in section  # плашка и мок-фраза — только в шаге 5
    assert not re.search(r"перв(?:ая|ой) строк|послед(?:няя|ней) строк", section.lower())


def test_quick_survey_saves_after_the_contact_and_after_each_portion_into_the_same_order():
    section = quick_section()
    assert "kitchen brief save olga brief.json" in section
    assert "латиницей" in section
    assert "kitchen brief save <заказ> brief.json" in section
    assert "номер-имя" in section and "первого ответа команды" in section
    assert "после каждой новой порции ответов" in section
    assert "тот же заказ" in section
    assert "Заказ с фамилией" in section and "какой номер" not in section  # дубль — только по шагу 7


def test_quick_survey_fork_comes_right_after_the_lengths_and_the_rest_goes_to_the_phone_list():
    section = quick_section()
    assert re.search(r"сразу после длин[^.]*шаг 5", section)
    assert "Уточнить по телефону и на замере" in section
    assert "лимит" in section.lower() and "сверх пяти" in section


def test_template_has_the_quick_survey_list_and_the_counter_line():
    template = read("assets", "brief_template.md")
    assert "(N из 5 вопросов)" in template
    heading = "## 7. Уточнить по телефону и на замере"
    assert heading in template
    tail = template.split(heading)[1]
    assert "списком" in tail and "- {{" in tail
    assert "быстр" in tail.lower()


def test_quick_wording_lives_in_questions_md_only():
    questions = quick_questions()
    for text in questions:
        holders = [
            p.name for p in skill_files() if p.suffix == ".md" and text in p.read_text(encoding="utf-8")
        ]
        assert holders == ["questions.md"], (text, holders)


def test_trigger_phrase_for_the_website_client_goes_to_brief_and_does_not_clash_with_estimate():
    phrases = json.loads((REPO / "skills" / "triggers.json").read_text(encoding="utf-8"))["phrases"]
    by_text = {p["text"]: p["skill"] for p in phrases}
    assert by_text[QUICK_TRIGGER] == "kitchen-brief"
    assert by_text[ESTIMATE_TRIGGER] == "kitchen-estimate"
    assert skill_lint.lint() == []
    estimate = (REPO / "skills" / "kitchen-estimate" / "SKILL.md").read_text(encoding="utf-8")
    assert (
        "при первом контакте клиента с сайта" in read("SKILL.md").split("---")[1]
    )  # различие описано в brief
    assert "заказа или проекта ещё нет" in read("SKILL.md").split("---")[1]
    assert "Клиент с сайта" not in estimate


def only_name_brief(tmp_path, name="Ольга"):
    path = tmp_path / "brief.json"
    path.write_text(
        json.dumps({"client": {"name": {"value": name, "source": "client"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_two_name_only_briefs_the_second_fails_with_the_new_order_command_and_it_creates_one(home, tmp_path):
    brief = only_name_brief(tmp_path)
    first = run(["brief", "save", "olga", str(brief)])
    assert first.exit_code == 0
    assert "заведён, бриф сохранён." in first.output
    ref1 = re.search(r"Заказ (\S+) заведён", first.output).group(1)
    assert ref1.endswith("-olga")
    again = run(["brief", "save", "olga", str(brief)])
    assert again.exit_code == 1
    assert "Заказ с фамилией «olga» уже есть" in again.output
    new_cmd = re.search(r"Чтобы завести новый: kitchen brief save (\S+) brief\.json\.", again.output)
    assert new_cmd, again.output
    ref2 = new_cmd.group(1)
    assert ref2 != ref1 and ref2.endswith("-olga")
    assert not (home / "orders" / ref2).exists()
    second = run(["brief", "save", ref2, str(brief)])  # команда из текста ошибки, brief.json — наш файл
    assert second.exit_code == 0
    assert f"Заказ {ref2} заведён, бриф сохранён." in second.output
    assert (home / "orders" / ref1).is_dir() and (home / "orders" / ref2).is_dir()
    assert sorted(p.name for p in (home / "orders").iterdir() if p.name.endswith("-olga")) == sorted(
        [ref1, ref2]
    )


def test_same_client_after_the_duplicate_error_saves_into_the_found_order_and_later_portions_follow(
    home, tmp_path
):
    brief = only_name_brief(tmp_path)
    first = run(["brief", "save", "olga", str(brief)])
    ref = re.search(r"Заказ (\S+) заведён", first.output).group(1)
    again = run(["brief", "save", "olga", str(brief)])
    assert f"уже есть: {ref}." in again.output
    same = run(["brief", "save", ref, str(brief)])
    assert same.exit_code == 0 and f"Заказ {ref}: бриф сохранён." in same.output
    assert "Заказ <номер-фамилия>: бриф сохранён." in read("SKILL.md")
    portion = json.loads(brief.read_text(encoding="utf-8"))
    portion["client"]["phone"] = {"value": "+7 900 000-00-00", "source": "client"}
    brief.write_text(json.dumps(portion, ensure_ascii=False), encoding="utf-8")
    assert run(["brief", "save", ref, str(brief)]).exit_code == 0
    shown = run(["brief", "show", ref])
    assert shown.exit_code == 0 and "+7 900 000-00-00" in shown.output


def test_check_brief_on_a_name_only_brief_reports_no_meters_so_the_fork_waits_for_the_lengths(tmp_path):
    done = run_check_brief(str(only_name_brief(tmp_path)))
    assert done.returncode == 0
    report = json.loads(done.stdout)
    assert report["summary"]["running_meters"] is None
    assert report["summary"]["missing_required"] > 0  # пустые обязательные поля не мешают сохранению


def test_quick_survey_eval_matches_the_ticket():
    data = json.loads(read("evals", "evals.json"))
    case = data["evals"][4]
    assert case["id"] == 5 and "quick" in case["name"]
    assert "scripts/petrov-sandbox" in case["setup"] and "olga" in case["setup"]
    assert "С сайта: «Здравствуйте, сколько будет стоить кухня?» Ольга" in case["prompt"]
    text = " ".join(case["assertions"]).lower()
    for needle in (
        "первый вопрос",
        "телефон",
        "не больше 5 вопросов",
        "не больше 2",
        "quick-estimate",
        "мок-фраза",
        "уточнить по телефону и на замере",
        "kitchen brief save olga brief.json",
        "после каждой новой порции",
    ):
        assert needle in text, needle
    assert BANNED.search(json.dumps(case, ensure_ascii=False)) is None
