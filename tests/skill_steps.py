"""Общие помощники тестов текстов скиллов: границы шагов в одном месте (ревью 28, тикет 48)."""

import re

from kitchen import skill_lint


def skill_text(name: str) -> str:
    return (skill_lint.DEFAULT_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def numbered_steps(name: str, section: str = "## Шаги") -> dict[int, str]:
    """Пункты верхнего уровня раздела: номер -> текст (до следующего пункта верхнего уровня)."""
    body = skill_text(name).split(section)[1].split("\n## ")[0]
    parts = re.split(r"^(\d+)\. \*\*", body, flags=re.MULTILINE)
    found = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    assert found, name  # фильтр не пустой
    return found
