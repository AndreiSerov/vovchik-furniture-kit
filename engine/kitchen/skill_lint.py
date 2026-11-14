"""Статические проверки скиллов `skills/kitchen-*` (спека 6.6, ADR 0003).

Запуск: `python -m kitchen.skill_lint [папка_скиллов]`; код возврата 1, если есть нарушения.
Его зовёт `scripts/check`. Сообщения: «скилл: строка N: что не так».
"""

import json
import re
import sys
from pathlib import Path

import typer
import yaml

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "skills"
TRIGGERS_FILE = "triggers.json"
ALLOWED_FRONTMATTER = {"name", "description"}


def skill_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("kitchen-*") if (p / "SKILL.md").is_file())


def split_frontmatter(lines: list[str]) -> tuple[list[str] | None, int]:
    """Строки frontmatter (без `---`) и номер (с нуля) первой строки тела."""
    if not lines or lines[0].strip() != "---":
        return None, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], i + 1
    return None, 0


def key_line(front: list[str], key: str) -> int:
    for i, line in enumerate(front):
        if re.match(rf"{re.escape(key)}\s*:", line):
            return i + 2
    return 1


def check_frontmatter(skill: str, lines: list[str]) -> list[str]:
    front, _ = split_frontmatter(lines)
    if front is None:
        return [f"{skill}: строка 1: нет frontmatter (блока `---` с name и description)"]
    try:
        data = yaml.safe_load("\n".join(front))
    except yaml.YAMLError:
        return [f"{skill}: строка 1: frontmatter не разбирается как YAML"]
    if not isinstance(data, dict):
        return [f"{skill}: строка 1: frontmatter не словарь"]
    problems = []
    for key in sorted(set(data) - ALLOWED_FRONTMATTER):
        problems.append(
            f"{skill}: строка {key_line(front, key)}: лишнее поле frontmatter «{key}» "
            "(ADR 0003: только name и description)"
        )
    for key in sorted(ALLOWED_FRONTMATTER):
        if not isinstance(data.get(key), str) or not data[key].strip():
            problems.append(f"{skill}: строка 1: нет поля {key} во frontmatter или оно пустое")
    if isinstance(data.get("name"), str) and data["name"] != skill:
        problems.append(
            f"{skill}: строка {key_line(front, 'name')}: name «{data['name']}» не совпадает с папкой"
        )
    return problems


def command_tree(app: typer.Typer) -> dict[str, dict | None]:
    """Команды приложения: группа — словарь подкоманд, команда — None."""

    def walk(cmd: object) -> dict[str, dict | None] | None:
        if not isinstance(cmd, typer.core.TyperGroup):
            return None
        return {name: walk(sub) for name, sub in cmd.commands.items()}

    return walk(typer.main.get_command(app)) or {}


INLINE_CODE = re.compile(r"`([^`\n]+)`")
KITCHEN_CALL = re.compile(r"(?<![\w./-])kitchen((?:[ \t]+[^\s`]+)*)")


def code_lines(lines: list[str], start: int) -> list[tuple[int, str]]:
    """(номер строки с 1, код): строки внутри блока ``` и участки в обратных кавычках."""
    found = []
    in_fence = False
    for i in range(start, len(lines)):
        line = lines[i]
        if line.strip().startswith("```"):
            in_fence = not in_fence
        elif in_fence:
            found.append((i + 1, line))
        else:
            found.extend((i + 1, m.group(1)) for m in INLINE_CODE.finditer(line))
    return found


def check_commands(skill: str, lines: list[str], tree: dict[str, dict | None]) -> list[str]:
    _, start = split_frontmatter(lines)
    problems = []
    for lineno, code in code_lines(lines, start):
        for call in KITCHEN_CALL.finditer(code):
            level: dict[str, dict | None] | None = tree
            path: list[str] = []
            words = call.group(1).split()
            for word in words:
                if level is None or word.startswith(("-", "<", "[", "$")):
                    break
                path.append(word)
                if word not in level:
                    what = "подкоманды" if len(path) > 1 else "команды"
                    problems.append(f"{skill}: строка {lineno}: нет {what} `kitchen {' '.join(path)}` в CLI")
                    break
                level = level[word]
            else:
                if level is not None:
                    problems.append(
                        f"{skill}: строка {lineno}: `kitchen {' '.join(path)}` — группа, нужна подкоманда"
                    )
    return problems


# Формулы и числа живут в движке (AGENTS.md, принцип 2): скилл их не содержит.
FORMULA_PATTERNS = {
    "арифметика с переменной": re.compile(
        r"\b[A-Z][A-Za-z_]*\s*[-+*/×]\s*\d|\b[A-Z][A-Za-z_]*\s+[-+*/×]\s+[A-Z]\w*\b"
    ),
    "арифметика над числами": re.compile(r"\b\d+\s*(?:[*×÷]|\s[-+/]\s)\s*\d+"),
    "присваивание формулы": re.compile(r"\b[A-Z][A-Za-z_]*\s*=\s*[\w(]+\s*[-+*/×]"),
    "размер в мм": re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:мм|mm)\b", re.IGNORECASE),
    "размер «ширина×высота»": re.compile(r"\b\d+\s*[xх×]\s*\d+\b"),
}


def check_no_formulas(skill: str, lines: list[str]) -> list[str]:
    _, start = split_frontmatter(lines)
    problems = []
    for i in range(start, len(lines)):
        for label, pattern in FORMULA_PATTERNS.items():
            problems.extend(
                f"{skill}: строка {i + 1}: {label} «{m.group(0)}» (формулы — только в движке)"
                for m in pattern.finditer(lines[i])
            )
    return problems


def normalise_phrase(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def check_triggers(path: Path, skills: set[str]) -> list[str]:
    """Набор фраз `{"phrases": [{"text", "skill"}]}`. Нет файла или пустой набор — проходит."""
    name = path.name
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        return [f"{name}: строка {err.lineno}: не JSON: {err.msg}"]
    if not isinstance(data, dict) or not isinstance(data.get("phrases"), list):
        return [f'{name}: строка 1: нужен объект {{"phrases": [...]}}']
    phrases = data["phrases"]
    problems = []
    seen: dict[str, int] = {}
    covered = set()
    for i, item in enumerate(phrases, 1):
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
        covered.add(item["skill"])
    if phrases:
        problems.extend(f"{s}: нет ни одной фразы в {name}" for s in sorted(skills - covered))
    return problems


def lint(root: Path = DEFAULT_ROOT, app: typer.Typer | None = None) -> list[str]:
    """Все нарушения в скиллах папки `root`; пусто — всё в порядке."""
    if app is None:
        from kitchen.cli import app as cli_app

        app = cli_app
    tree = command_tree(app)
    problems = []
    dirs = skill_dirs(root)
    for d in dirs:
        lines = (d / "SKILL.md").read_text(encoding="utf-8").splitlines()
        problems += check_frontmatter(d.name, lines)
        problems += check_commands(d.name, lines, tree)
        problems += check_no_formulas(d.name, lines)
    problems += check_triggers(root / TRIGGERS_FILE, {d.name for d in dirs})
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
