"""Статические проверки скиллов `skills/kitchen-*` (спека 6.6, ADR 0003).

Здесь только разбор и правила; тесты — `test_skill_checks.py`.
"""

import json
import re
from pathlib import Path

import typer
import yaml
from kitchen.cli import app

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
TRIGGERS = Path(__file__).resolve().parent / "skill_triggers.json"

ALLOWED_FRONTMATTER = {"name", "description"}


def skill_dirs(root: Path = SKILLS) -> list[Path]:
    return sorted(p for p in root.glob("kitchen-*") if (p / "SKILL.md").is_file())


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Возвращает (frontmatter, тело); без блока `---` frontmatter — None."""
    match = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return None, text
    return match.group(1), match.group(2)


def read_skill(skill_dir: Path) -> tuple[str | None, str]:
    return split_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))


def check_frontmatter(skill_dir: Path) -> list[str]:
    raw, _ = read_skill(skill_dir)
    if raw is None:
        return ["нет frontmatter"]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as err:
        return [f"frontmatter не разбирается: {err}"]
    if not isinstance(data, dict):
        return ["frontmatter не словарь"]
    problems = []
    extra = set(data) - ALLOWED_FRONTMATTER
    if extra:
        problems.append(f"лишние поля frontmatter: {sorted(extra)}")
    for key in sorted(ALLOWED_FRONTMATTER):
        if not isinstance(data.get(key), str) or not data[key].strip():
            problems.append(f"поле {key} пустое или не строка")
    if isinstance(data.get("name"), str) and data["name"] != skill_dir.name:
        problems.append(f"name «{data['name']}» не совпадает с папкой «{skill_dir.name}»")
    return problems


def command_tree() -> dict[str, dict | None]:
    """Команды `kitchen` из CLI: группа — словарь подкоманд, команда — None."""

    def walk(cmd: object) -> dict[str, dict | None] | None:
        if not isinstance(cmd, typer.core.TyperGroup):
            return None
        return {name: walk(sub) for name, sub in cmd.commands.items()}

    return walk(typer.main.get_command(app)) or {}


CODE_SPAN = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
KITCHEN_CALL = re.compile(r"(?<![\w./-])kitchen((?:[ \t]+[^\s`]+)*)")


def kitchen_mentions(body: str) -> list[list[str]]:
    """Слова после `kitchen` в каждом вызове внутри кода (строка кода или блок)."""
    calls = []
    for span in CODE_SPAN.findall(body):
        for line in span.strip("`").splitlines():
            calls.extend(match.group(1).split() for match in KITCHEN_CALL.finditer(line))
    return calls


def check_commands(skill_dir: Path) -> list[str]:
    _, body = read_skill(skill_dir)
    tree = command_tree()
    problems = []
    for words in kitchen_mentions(body):
        level = tree
        path = []
        for word in words:
            if level is None or word.startswith(("-", "<", "[", "$")):
                break
            path.append(word)
            if word not in level:
                problems.append(f"нет команды `kitchen {' '.join(path)}`")
                break
            level = level[word]
        else:
            if level is not None:
                problems.append(f"`kitchen {' '.join(path)}` — группа, нужна подкоманда")
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


def check_no_formulas(skill_dir: Path) -> list[str]:
    _, body = read_skill(skill_dir)
    problems = []
    for lineno, line in enumerate(body.splitlines(), 1):
        for label, pattern in FORMULA_PATTERNS.items():
            for found in pattern.finditer(line):
                problems.append(f"строка {lineno} тела: {label}: «{found.group(0)}»")
    return problems


def normalise_phrase(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def check_triggers(data: object, skills: set[str]) -> list[str]:
    """Схема: {"phrases": [{"text": str, "skill": str}, ...]}; уникальность и покрытие."""
    if not isinstance(data, dict) or not isinstance(data.get("phrases"), list):
        return ['нужен объект {"phrases": [...]}']
    phrases = data["phrases"]
    if not phrases:
        return ["набор фраз пуст"]
    problems = []
    seen: dict[str, int] = {}
    covered = set()
    for i, item in enumerate(phrases):
        ok = (
            isinstance(item, dict)
            and set(item) == {"text", "skill"}
            and isinstance(item["text"], str)
            and item["text"].strip()
            and isinstance(item["skill"], str)
        )
        if not ok:
            problems.append(f"фраза {i}: нужны непустой text и skill, и больше ничего")
            continue
        key = normalise_phrase(item["text"])
        if key in seen:
            problems.append(f"фраза {i} повторяет фразу {seen[key]}: «{item['text']}»")
        seen.setdefault(key, i)
        if item["skill"] not in skills:
            problems.append(f"фраза {i}: нет скилла «{item['skill']}»")
        covered.add(item["skill"])
    problems.extend(f"у скилла «{s}» нет фразы" for s in sorted(skills - covered))
    return problems


def load_triggers(path: Path = TRIGGERS) -> object:
    return json.loads(path.read_text(encoding="utf-8"))
