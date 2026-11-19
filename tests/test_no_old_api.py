"""Тикет 71 (п. 12): тесты не зовут старую форму `api`, которую удаляет тикет 75 (спека этапа 9,
«Удаление старой формы»). Файлы, которые её ещё зовут, перечислены в `OLD_API_EXCEPTIONS`; тикеты 73–74
переводят их на расчёт, и список только сокращается (храповик ниже)."""

import ast
import re
from pathlib import Path

import pytest
from kitchen import api

TESTS = Path(__file__).resolve().parent

# Имена, которые тикет 75 удаляет из `kitchen.api`, и константы имён файлов, удалённые в тикете 71.
REMOVED = {
    "parts_from_order",
    "parts_from_project",
    "cut_from_order",
    "cut_from_project",
    "cut_and_save",
    "edges_from_order",
    "edges_from_project",
    "edges_and_save",
    "drilling_from_order",
    "drilling_from_project",
    "drilling_and_save",
    "assembly_from_order",
    "assembly_from_project",
    "assembly_and_save",
    "trip_from_order",
    "trip_from_project",
    "trip_and_save",
    "purchase_from_order",
    "purchase_and_save",
    "estimate_from_order",
    "estimate_from_project",
    "estimate_and_save",
    "quote_from_order",
    "quote_from_project",
    "quote_and_save",
    "views_from_order",
    "views_from_project",
    "views_and_save",
    "placement_from_order",
    "placement_from_project",
    "check_order",
    "check_project",
    "economy_parts_from_project",
    "production_and_save",
    "_drilling_writer",
    "_assembly_writer",
    "_trip_writer",
    "_save_output",
    "_purchase_files",
    "_estimate_files",
    "_estimate_run",
    "_quote",
    "_build_parts",
    "_build_cut",
    "CUTTING_PDF",
    "DRILLING_PDF",
    "ASSEMBLY_PDF",
    "TRIP_PDF",
    "VIEWS_PDF",
    "VIEWS_HTML",
}

# Файлы `tests/`, которые ещё зовут старую форму (список тикета 71); тикеты 73–74 его опустошают.
OLD_API_EXCEPTIONS = {
    "stage9_snapshot.py",
}


def _is_api(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "api"


def old_api_uses(path: Path) -> set[str]:
    """Имена старой формы, к которым файл обращается: `api.<имя>`, `getattr`/`setattr(api, "<имя>")`,
    строка `"kitchen.api.<имя>"` и `from kitchen.api import <имя>`."""
    used = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "kitchen.api":
            used |= {alias.name for alias in node.names if alias.name in REMOVED}
        if isinstance(node, ast.Attribute) and _is_api(node.value) and node.attr in REMOVED:
            used.add(node.attr)
        if isinstance(node, ast.Call) and len(node.args) >= 2 and _is_api(node.args[0]):
            name = node.args[1]
            if isinstance(name, ast.Constant) and name.value in REMOVED:
                used.add(name.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            prefix, _, name = node.value.rpartition(".")
            if prefix == "kitchen.api" and name in REMOVED:
                used.add(name)
    return used


def test_tests_do_not_call_the_old_api():
    problems = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name in OLD_API_EXCEPTIONS:
            continue
        used = old_api_uses(path)
        if used:
            problems.append(f"{path.name}: {sorted(used)}")
    assert not problems, (
        "Тест зовёт старую форму api — переведи его на api.calculation/save/production:\n"
        + ("\n".join(problems))
    )


def test_old_api_exceptions_only_shrink():
    stale = [
        name
        for name in sorted(OLD_API_EXCEPTIONS)
        if not (TESTS / name).exists() or not old_api_uses(TESTS / name)
    ]
    assert not stale, (
        "Убери из OLD_API_EXCEPTIONS файлы, которые больше не зовут старую форму api:\n" + "\n".join(stale)
    )


@pytest.mark.parametrize("name", sorted(REMOVED))
def test_the_old_api_is_gone(name):
    """Тикет 75 (п. 1): старой формы в `kitchen.api` нет."""
    assert not hasattr(api, name)


# --- тикет 75 (п. 3): документы и движок не называют старую форму ---------------------------------

REPO = TESTS.parent
ENGINE = REPO / "engine" / "kitchen"
# Документы, которые описывают текущий код. `docs/specs/` и `docs/reviews/` — история, их не проверяем.
DOCS = [
    REPO / "DECISIONS.md",
    REPO / "GLOSSARY.md",
    REPO / "AGENTS.md",
    *sorted((REPO / "docs" / "agents").rglob("*.md")),
    *sorted((REPO / "docs" / "adr").rglob("*.md")),
    *sorted(p for p in (REPO / "skills").rglob("*") if p.is_file() and p.suffix in {".md", ".py", ".json"}),
]
# Одноимённые определения, которые к старой форме не относятся: доменная проверка проекта и вид вывода.
DOMAIN_NAMES = {("checks.py", "check_project"), ("store.py", "VIEWS_HTML")}


def _api_mentions(text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\bapi\.(\w+)", text) if m.group(1) in REMOVED}


def _defined(path: Path) -> set[str]:
    """Имена, которые модуль определяет на верхнем уровне: функции, классы, присваивания."""
    names = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {t.id for t in targets if isinstance(t, ast.Name)}
    return names


def test_docs_do_not_name_the_old_api():
    problems = [
        f"{path.relative_to(REPO)}: {sorted(found)}"
        for path in DOCS
        if (found := _api_mentions(path.read_text(encoding="utf-8")))
    ]
    assert not problems, "Документ называет удалённую форму api:\n" + "\n".join(problems)


def test_engine_neither_defines_nor_calls_the_old_api():
    problems = []
    for path in sorted(ENGINE.rglob("*.py")):
        defined = {n for n in _defined(path) & REMOVED if (path.name, n) not in DOMAIN_NAMES}
        used = old_api_uses(path)
        if defined or used:
            problems.append(f"{path.name}: определяет {sorted(defined)}, зовёт {sorted(used)}")
    assert not problems, "\n".join(problems)


def test_the_doc_guard_sees_an_old_name():
    """Страж ловит и `api.X`, и `kitchen.api.X`, а доменную `checks.check_project` не считает."""
    assert _api_mentions("`api.cut_from_order` и `kitchen.api.check_order`") == {
        "cut_from_order",
        "check_order",
    }
    assert _api_mentions("`kitchen.checks.check_project`, `store.VIEWS_HTML`, `api.calculation`") == set()
