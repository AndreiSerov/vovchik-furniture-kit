"""Статические проверки скиллов `skills/kitchen-*` (спека 6.6, ADR 0003).

Запуск: `python -m kitchen.skill_lint [папка_скиллов]`; код возврата 1, если есть нарушения.
Его зовёт `scripts/check`. Сообщения: «скилл: строка N: что не так».
"""

import json
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import typer
import yaml

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "skills"
TRIGGERS_FILE = "triggers.json"
ALLOWED_FRONTMATTER = {"name", "description"}

# Дерево команд: группа — словарь подкоманд, команда — множество её флагов.
Tree = dict[str, "dict | frozenset[str]"]


def read_utf8(path: Path, label: str) -> tuple[str | None, list[str]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        line = raw[: err.start].count(b"\n") + 1
        return None, [f"{label}: строка {line}: файл не в UTF-8"]
    if text.startswith("﻿"):
        return text[1:], [f"{label}: строка 1: файл начинается с BOM, уберите его"]
    return text, []


def split_frontmatter(lines: list[str]) -> tuple[list[str] | None, int]:
    """Строки frontmatter (без `---`) и номер (с нуля) первой строки тела."""
    if not lines or lines[0].strip() != "---":
        return None, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], i + 1
    return None, 0


def check_frontmatter(skill: str, lines: list[str]) -> list[str]:
    front, _ = split_frontmatter(lines)
    if front is None:
        return [f"{skill}: строка 1: нет frontmatter (блока `---` с name и description)"]
    text = "\n".join(front)
    try:
        node = yaml.compose(text)
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        mark = getattr(err, "problem_mark", None)
        line = mark.line + 2 if mark else 1
        problem = getattr(err, "problem", None) or "ошибка YAML"
        hint = " (значение с двоеточием возьми в кавычки)" if "mapping values" in problem else ""
        return [f"{skill}: строка {line}: frontmatter не разбирается: {problem}{hint}"]
    if not isinstance(node, yaml.MappingNode) or not isinstance(data, dict):
        return [f"{skill}: строка 2: frontmatter не словарь"]
    problems = []
    seen: set[str] = set()
    for key_node, _ in node.value:
        key = str(key_node.value)
        line = key_node.start_mark.line + 2
        if key in seen:
            problems.append(f"{skill}: строка {line}: повтор ключа frontmatter «{key}»")
        seen.add(key)
        if key not in ALLOWED_FRONTMATTER:
            problems.append(
                f"{skill}: строка {line}: лишнее поле frontmatter «{key}» "
                "(ADR 0003: только name и description)"
            )
    for key in sorted(ALLOWED_FRONTMATTER):
        if not isinstance(data.get(key), str) or not data[key].strip():
            problems.append(f"{skill}: строка 1: нет поля {key} во frontmatter или оно пустое")
    if isinstance(data.get("name"), str) and data["name"].strip() not in {"", skill}:
        line = next((k.start_mark.line + 2 for k, _ in node.value if k.value == "name"), 1)
        problems.append(f"{skill}: строка {line}: name «{data['name']}» не совпадает с папкой")
    return problems


def command_tree(app: typer.Typer) -> Tree:
    """Команды приложения; флаги приложения целиком лежат под ключом `""`."""

    def flags(cmd: object) -> frozenset[str]:
        found = {"--help"}
        for param in getattr(cmd, "params", []):
            found.update(getattr(param, "opts", []))
            found.update(getattr(param, "secondary_opts", []))
        return frozenset(found)

    def walk(cmd: object) -> "dict | frozenset[str]":
        if isinstance(cmd, typer.core.TyperGroup):
            return {name: walk(sub) for name, sub in cmd.commands.items()}
        return flags(cmd)

    root = typer.main.get_command(app)
    tree = walk(root)
    assert isinstance(tree, dict)
    tree[""] = flags(root)
    return tree


INLINE_CODE = re.compile(r"`([^`\n]+)`")
LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")
FENCE = re.compile(r"\s*(```|~~~)")
KITCHEN_CALL = re.compile(r"(?<![\w./-])kitchen(?=[ \t]+\S)")
SHELL_END = re.compile(r"[;|&)'\"]")


def code_lines(lines: list[str], start: int) -> list[tuple[int, str]]:
    """(номер строки с 1, код): блоки ``` и ~~~, блоки с отступом, участки в обратных кавычках.

    Строки блока, оканчивающиеся на `\\`, склеиваются; номер — первой строки.
    """
    block: list[tuple[int, str]] = []
    fence: str | None = None
    prev_blank = True
    prev_indented = False
    for i in range(start, len(lines)):
        line = lines[i]
        opening = FENCE.match(line)
        indented = (
            fence is None
            and (line.startswith(("    ", "\t")))
            and line.strip() != ""
            and (prev_blank or prev_indented)
            and not LIST_ITEM.match(line.lstrip())
        )
        if opening and (fence is None or opening.group(1) == fence):
            fence = None if fence else opening.group(1)
        elif fence or indented:
            block.append((i + 1, line))
        else:
            block.extend((i + 1, m.group(1)) for m in INLINE_CODE.finditer(line))
        prev_blank = line.strip() == ""
        prev_indented = bool(indented)
    joined: list[tuple[int, str]] = []
    carry: tuple[int, str] | None = None
    for lineno, text in block:
        if carry:
            lineno, text = carry[0], carry[1] + " " + text.strip()
            carry = None
        if text.rstrip().endswith("\\"):
            carry = (lineno, text.rstrip()[:-1])
        else:
            joined.append((lineno, text))
    if carry:
        joined.append(carry)
    return joined


def is_flag(word: str) -> bool:
    return re.match(r"--?[A-Za-z]", word) is not None


def is_placeholder(word: str) -> bool:
    return (
        word in {"…", "..."}
        or word.startswith(("<", "[", "{", "$", "…"))
        or (word.isalpha() and word.isupper() and len(word) > 1)
    )


def check_call(words: list[str], tree: Tree) -> list[str]:
    """Один вызов `kitchen …`: команда и подкоманда существуют, флаги есть у команды."""
    global_flags = tree[""]
    level: object = tree
    path: list[str] = []
    problems: list[str] = []
    unresolved = False
    for raw in words:
        name = raw.split("=")[0]
        if isinstance(level, dict):
            if is_flag(raw):
                if name == "--help":
                    return problems
                if name not in global_flags:
                    unresolved = True
                    break
                continue
            word = raw.rstrip(".,:;!?")
            if is_placeholder(raw) or word == "":
                unresolved = True
                break
            if word not in level or word == "":
                what = "подкоманды" if path else "команды"
                return [*problems, f"нет {what} `kitchen {' '.join([*path, word])}` в CLI"]
            path.append(word)
            level = level[word]
        elif is_flag(raw) and name not in level and name not in global_flags:
            problems.append(f"нет флага `{name}` у команды `kitchen {' '.join(path)}`")
    if isinstance(level, dict) and (unresolved or path):
        if path:
            problems.append(f"`kitchen {' '.join(path)}` — группа, нужна подкоманда")
        elif unresolved and not all(is_flag(w) for w in words):
            problems.append("`kitchen` — нужна команда")
    return problems


def check_commands(skill: str, lines: list[str], tree: Tree) -> list[str]:
    _, start = split_frontmatter(lines)
    problems = []
    for lineno, code in code_lines(lines, start):
        for call in KITCHEN_CALL.finditer(code):
            rest = code[call.end() :]
            end = SHELL_END.search(rest)
            words = (rest[: end.start()] if end else rest).split()
            problems.extend(f"{skill}: строка {lineno}: {p}" for p in check_call(words, tree))
    return problems


# Формулы и числа живут в движке (AGENTS.md, принцип 2): скилл их не содержит.
DIM = r"(?<!\w)(?:[WHDTLШВГТ]|width|height|depth|ширина|высота|глубина|толщина)(?!\w)"
OP = r"[-+*/×÷−]"
NUM = r"\d+(?:[.,]\d+)?"
FLAGS = re.IGNORECASE


def numeric_arithmetic(match: re.Match[str]) -> bool:
    """Арифметика над числами — только с числом от трёх цифр; `600-4` — без пробелов, `2026-047` нет."""
    int_len = {side: len(re.split(r"[.,]", match[side])[0]) for side in ("l", "r")}
    if match["op"] == "-" and not (match["s1"] and match["s2"]):
        return int_len["l"] >= 3 and int_len["r"] <= 2
    return int_len["l"] >= 3 or int_len["r"] >= 3


# (метка, регэксп, фильтр); раньше стоящее правило забирает фрагмент себе.
FORMULA_RULES: list[tuple[str, re.Pattern[str], Callable[[re.Match[str]], bool] | None]] = [
    (
        "размер «ширина×высота»",
        re.compile(r"(?<![\w.,])(?:\d{3,}\s*[xх×]\s*\d+|\d+\s*[xх×]\s*\d{3,})(?!\w)"),
        None,
    ),
    ("размер в мм", re.compile(rf"(?<![\w.,]){NUM}\s*(?:мм|mm)(?!\w)", FLAGS), None),
    ("размер с единицей", re.compile(r"(?<![\w.,])\d+[.,]\d+\s*(?:см|м)(?!\w)"), None),
    (
        "арифметика над числами",
        re.compile(
            rf"(?<![\w.,/-])(?P<l>{NUM})(?P<s1>\s*)(?P<op>{OP})(?P<s2>\s*)(?P<r>{NUM})(?!\w|[.,]\d|-\d)"
        ),
        numeric_arithmetic,
    ),
    (
        "арифметика над числами",
        re.compile(
            rf"(?<![\w.,]){NUM}\s*(?:см|м)(?!\w)\s*{OP}\s*{NUM}|{NUM}\s*{OP}\s*{NUM}\s*(?:см|м)(?!\w)"
        ),
        None,
    ),
    ("арифметика с переменной", re.compile(rf"{DIM}\s*{OP}\s*(?:\d|{DIM})", FLAGS), None),
    ("арифметика с переменной", re.compile(rf"(?<![\w.,]){NUM}\s*[*×÷/+]\s*{DIM}", FLAGS), None),
    (
        "арифметика словами",
        re.compile(
            r"(?<!\w)(?:ширин\w*|высот\w*|глубин\w*|толщин\w*|width|height|depth)(?:\s+\S+){0,3}?"
            r"\s+(?:минус|плюс|умножить на|разделить на|делить на)\s+\d",
            FLAGS,
        ),
        None,
    ),
    (
        "присваивание формулы",
        re.compile(
            rf"{DIM}\s*=\s*\(?\w+\)?(?:(?:\s+-\s+|\s*[+*/×÷]\s*)\(?\w+\)?)+",
            FLAGS,
        ),
        None,
    ),
]
FORMULA_LABELS = tuple(dict.fromkeys(label for label, _, _ in FORMULA_RULES))


def formula_spans(line: str) -> Iterator[tuple[str, str]]:
    taken: list[tuple[int, int]] = []
    for label, pattern, accept in FORMULA_RULES:
        for m in pattern.finditer(line):
            span = (m.start(), m.end())
            if accept and not accept(m):
                continue
            if any(span[0] < b and a < span[1] for a, b in taken):
                continue
            taken.append(span)
            yield label, m.group(0)


def check_no_formulas(skill: str, lines: list[str]) -> list[str]:
    _, start = split_frontmatter(lines)
    problems = []
    for i in range(start, len(lines)):
        problems.extend(
            f"{skill}: строка {i + 1}: {label} «{found}» (формулы — только в движке)"
            for label, found in formula_spans(lines[i])
        )
    return problems


# Форма скилла (спека 6.4): статус в начале, журнал в конце, устаревшее и плашка мока.
# Требование — (сообщение «нет …», проверка по строкам кода и тексту тела).
Shape = Callable[[list[str], str], bool]


def _step(command: str, kind: str | None = None) -> Shape:
    call = re.compile(rf"(?<![\w./-]){command}(?!\w)")
    flag = re.compile(rf"--kind[ =]{kind}(?!\w)") if kind else None
    return lambda code, _text: any(call.search(c) and (flag is None or flag.search(c)) for c in code)


def _word(word: str) -> Shape:
    return lambda _code, text: word in text


STATUS = ("нет шага kitchen status", _step(r"kitchen\s+status"))
LOG_DONE = ("нет шага kitchen log с --kind done", _step(r"kitchen\s+log", "done"))
LOG_NEXT = ("нет шага kitchen log с --kind next", _step(r"kitchen\s+log", "next"))
STALE = ("нет раздела «Устарело»", _word("Устарело"))
MOCK = ("нет плашки «МОК-ДАННЫЕ»", _word("МОК-ДАННЫЕ"))
WORKING_SKILL = [STATUS, LOG_DONE, LOG_NEXT, STALE, MOCK]

SHAPE_REQUIREMENTS: dict[str, list[tuple[str, Shape]]] = {
    "kitchen-project": WORKING_SKILL,
    "kitchen-production": WORKING_SKILL,
    "kitchen-estimate": WORKING_SKILL,
    "kitchen-brief": [
        ("нет шага kitchen brief save", _step(r"kitchen\s+brief\s+save")),
        LOG_NEXT,
        MOCK,
    ],
    "kitchen-order": [STATUS, STALE],
}
# kitchen-order — до тикета 31.
SHAPE_EXEMPT = {"kitchen-order"}


def check_shape(skill: str, lines: list[str]) -> list[str]:
    if skill in SHAPE_EXEMPT:
        return []
    _, start = split_frontmatter(lines)
    code = [text for _, text in code_lines(lines, start)]
    text = "\n".join(lines[start:])
    return [f"{skill}: {message}" for message, ok in SHAPE_REQUIREMENTS.get(skill, []) if not ok(code, text)]


def normalise_phrase(text: str) -> str:
    text = " ".join(text.lower().replace("ё", "е").split())
    return text.strip(" .,!?:;…")


def check_triggers(path: Path, skills: set[str]) -> list[str]:
    """Набор фраз `{"phrases": [{"text", "skill"}]}`. Нет файла или пустой набор — проходит."""
    name = path.name
    if not path.is_file():
        return []
    text, problems = read_utf8(path, name)
    if text is None:
        return problems
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        return [*problems, f"{name}: строка {err.lineno}: не JSON: {err.msg}"]
    if not isinstance(data, dict) or not isinstance(data.get("phrases"), list):
        return [*problems, f'{name}: строка 1: нужен объект {{"phrases": [...]}}']
    phrases = data["phrases"]
    seen: dict[str, int] = {}
    covered = set()
    for i, item in enumerate(phrases, 1):
        if isinstance(item, dict) and isinstance(item.get("skill"), str):
            covered.add(item["skill"])
        ok = (
            isinstance(item, dict)
            and set(item) == {"text", "skill"}
            and isinstance(item["text"], str)
            and item["text"].strip()
            and isinstance(item["skill"], str)
        )
        if not ok:
            problems.append(f"{name}: фраза {i}: нужны непустой text и skill, и больше ничего")
            continue
        key = normalise_phrase(item["text"])
        if key in seen:
            problems.append(
                f"{name}: фраза {i}: дубль фразы {seen[key]} «{item['text']}» (скилл {item['skill']})"
            )
        seen.setdefault(key, i)
        if item["skill"] not in skills:
            problems.append(f"{name}: фраза {i}: неизвестный скилл «{item['skill']}»")
    if phrases:
        problems.extend(f"{s}: нет ни одной фразы в {name}" for s in sorted(skills - covered))
    return problems


ARROW_PATHS = re.compile(r"→\s*((?:`[^`]+`(?:,\s*)?)+)")


def check_question_paths(skill: str, folder: Path) -> list[str]:
    """Каждое поле после `→` в `references/questions.md` есть в `references/brief_schema.json`."""
    questions = folder / "references" / "questions.md"
    schema = folder / "references" / "brief_schema.json"
    if not questions.is_file() or not schema.is_file():
        return []
    label = f"{skill}: references/questions.md"
    text, problems = read_utf8(questions, label)
    if text is None:
        return problems
    try:
        sections = json.loads(schema.read_text(encoding="utf-8"))["sections"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return [*problems, f"{skill}: references/brief_schema.json: нет раздела sections"]
    for n, line in enumerate(text.splitlines(), 1):
        found = ARROW_PATHS.search(line)
        if not found:
            continue
        for path in re.findall(r"`([^`]+)`", found.group(1)):
            section, _, field = path.partition(".")
            field = re.sub(r"\[\].*$", "", field)
            known = section in sections and (
                not field or field == "*" or field in sections[section].get("fields", {})
            )
            if not known:
                problems.append(f"{label}: строка {n}: поле `{path}` не из brief_schema.json")
    return problems


def lint(root: Path = DEFAULT_ROOT, app: typer.Typer | None = None) -> list[str]:
    """Все нарушения в скиллах папки `root`; пусто — всё в порядке."""
    if app is None:
        from kitchen.cli import app as cli_app

        app = cli_app
    if not root.is_dir():
        return [f"{root}: нет такой папки со скиллами"]
    tree = command_tree(app)
    folders = sorted(p for p in root.glob("kitchen-*") if p.is_dir())
    if not folders:
        return [f"{root}: нет ни одного скилла kitchen-*"]
    problems: list[str] = []
    found: set[str] = set()
    for folder in folders:
        skill_file = folder / "SKILL.md"
        if not skill_file.is_file():
            problems.append(f"{folder.name}: строка 1: нет файла SKILL.md")
            continue
        found.add(folder.name)
        text, errors = read_utf8(skill_file, folder.name)
        problems += errors
        if text is None:
            continue
        lines = text.splitlines()
        problems += check_frontmatter(folder.name, lines)
        problems += check_commands(folder.name, lines, tree)
        problems += check_no_formulas(folder.name, lines)
        problems += check_shape(folder.name, lines)
        problems += check_question_paths(folder.name, folder)
    problems += check_triggers(root / TRIGGERS_FILE, found)
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else DEFAULT_ROOT
    problems = lint(root)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
