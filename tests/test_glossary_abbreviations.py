"""Спека 1.2: сокращения маркировок из деталей всех шаблонов описаны в таблице GLOSSARY.md."""

import json
import re
from pathlib import Path

from kitchen import api

from tests.conftest import REPO

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def glossary_abbreviations() -> set[str]:
    text = (REPO / "GLOSSARY.md").read_text(encoding="utf-8")
    section = text.split("## Сокращения маркировок", 1)[1]
    rows = [ln for ln in section.splitlines() if ln.startswith("|")]
    return {ln.split("|")[1].strip() for ln in rows[2:]}  # без шапки и разделителя


def abbreviation_of(part) -> str:
    """`Н1-дно-ящ-1` → `дно-ящ`: без метки модуля и без уточнения (Л/П или номер)."""
    rest = part.id.removeprefix(f"{part.module}-")
    return re.sub(r"-(Л|П|\d+)$", "", rest)


def used_abbreviations(result) -> set[str]:
    return {abbreviation_of(p) for m in result.modules for p in m.parts}


def test_every_abbreviation_of_petrov_templates_is_in_glossary(home):
    result = api.calculation(project=FIXTURES / "petrov_project.json", home=home).parts()
    assert {m.template for m in result.modules} >= {"base_drawers", "base_corner", "wall_hood", "tall_oven"}
    assert used_abbreviations(result) - glossary_abbreviations() == set()


def test_every_abbreviation_of_wall_lift_is_in_glossary(home, base_door_project):
    project = json.loads(base_door_project.read_text(encoding="utf-8"))
    row = project["walls"][0]["rows"][0]
    row["kind"] = "upper"
    row["modules"] = [
        {"label": "В1", "template": "wall_lift", "width": 600, "contents": {"lift": "aventos_hks"}}
    ]
    base_door_project.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = api.calculation(project=base_door_project, home=home).parts()
    assert used_abbreviations(result) - glossary_abbreviations() == set()


def test_glossary_has_no_unused_abbreviation(home):
    """Таблица не обещает сокращений, которых нет в деталях заказа Петрова (он покрывает все шаблоны, кроме подъёмника)."""
    result = api.calculation(project=FIXTURES / "petrov_project.json", home=home).parts()
    unused = glossary_abbreviations() - used_abbreviations(result)
    assert unused == set()
