"""Ограждения структуры: ловят то, что ревью раньше находило руками."""

import ast
import re
from pathlib import Path

from kitchen.parts import TEMPLATES

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
ENGINE = ROOT / "engine" / "kitchen"

# ADR 0002: тесты логики идут через kitchen.api, CLI — контрактными тестами.
PUBLIC = {"kitchen.api", "kitchen.cli", "kitchen.errors", "kitchen.result"}

# Храповик: тесты, которые уже ходят во внутренние модули. Список только сокращается.
# Новый тест берёт данные через kitchen.api; если без внутреннего модуля никак — запиши
# причину в DECISIONS.md и добавь сюда в том же коммите.
LEGACY_INTERNAL_IMPORTS = {
    "test_brief_api.py": {"kitchen.store"},
    "test_checks_brief.py": {"kitchen.brief", "kitchen.models"},
    "test_cut_pdf.py": {"kitchen.cutting", "kitchen.cutting_pdf", "kitchen.pdf"},
    "test_cut_pdf_dense.py": {"kitchen.cutting", "kitchen.cutting_pdf", "kitchen.pdf"},
    "test_cutting.py": {"kitchen.cutting", "kitchen.parts", "kitchen.project", "kitchen.workshop"},
    "test_orders_api.py": {"kitchen.store"},
    "test_pdf.py": {"kitchen.pdf"},
    "test_project_import_diff.py": {"kitchen.project"},
    "test_row_sum_registry.py": {"kitchen.parts"},
    "test_structure_guards.py": {"kitchen.parts"},
    "test_template_registry.py": {"kitchen.parts", "kitchen.project", "kitchen.workshop"},
}

# Файлы, где шаблоны модулей определены. Остальной движок берёт свойства шаблона из TEMPLATES.
TEMPLATE_HOMES = {"parts.py", "tall.py"}


def kitchen_imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kitchen"):
            if node.module == "kitchen":
                found |= {f"kitchen.{alias.name}" for alias in node.names}
            else:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names if alias.name.startswith("kitchen")}
    return found


def test_tests_reach_logic_through_the_api():
    problems = []
    for path in sorted(TESTS.glob("test_*.py")):
        extra = kitchen_imports(path) - PUBLIC - LEGACY_INTERNAL_IMPORTS.get(path.name, set())
        if extra:
            problems.append(f"{path.name}: {sorted(extra)}")
    assert not problems, "ADR 0002: тесты логики идут через kitchen.api.\n" + "\n".join(problems)


def test_legacy_list_only_shrinks():
    stale = []
    for name, allowed in LEGACY_INTERNAL_IMPORTS.items():
        path = TESTS / name
        unused = allowed - (kitchen_imports(path) if path.exists() else set())
        if unused:
            stale.append(f"{name}: {sorted(unused)}")
    assert not stale, "Убери из LEGACY_INTERNAL_IMPORTS то, что тест больше не импортирует:\n" + "\n".join(
        stale
    )


def test_template_names_live_only_in_the_registry():
    names = "|".join(re.escape(n) for n in TEMPLATES)
    pattern = re.compile(rf"""["']({names})["']""")
    problems = []
    for path in sorted(ENGINE.glob("*.py")):
        if path.name in TEMPLATE_HOMES:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                problems.append(f"{path.name}:{number}: {line.strip()}")
    assert not problems, (
        "Свойство шаблона берётся из реестра TEMPLATES, а не по имени шаблона:\n" + "\n".join(problems)
    )


def test_drawer_part_names_live_only_in_parts():
    pattern = re.compile(r"""["'](дно ящика|задняя стенка ящика)["']""")
    problems = []
    for path in sorted(ENGINE.glob("*.py")):
        if path.name == "parts.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                problems.append(f"{path.name}:{number}: {line.strip()}")
    assert not problems, "Имена деталей ящика берутся из parts.DRAWER_BOTTOM и DRAWER_BACK:\n" + "\n".join(
        problems
    )
