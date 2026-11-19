"""Тикет 75 (п. 5): детали и раскрой считает один путь — методы расчёта `Calculation`, и для проекта,
и для копий «экономии» (решение мастера 2026-11-19). Отдельных построителей `_build_parts`/`_build_cut` нет."""

import ast
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "engine" / "kitchen"
CALCULATION = ENGINE / "calculation.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _called(node: ast.AST, name: str) -> bool:
    """Вызов `name(...)` или `<что-то>.name(...)`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == name) or (
        isinstance(func, ast.Attribute) and func.attr == name
    )


def _callers(name: str) -> list[str]:
    """Где в `calculation.py` зовут `name`: `Класс.метод` или имя функции модуля."""
    found = []

    def walk(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = where
            if isinstance(child, ast.ClassDef | ast.FunctionDef):
                inner = f"{where}.{child.name}" if where else child.name
            if _called(child, name):
                found.append(where)
            walk(child, inner)

    walk(_tree(CALCULATION), "")
    return found


def test_no_separate_parts_and_cut_builders_in_the_engine():
    defined = [
        f"{path.name}:{node.name}"
        for path in sorted(ENGINE.rglob("*.py"))
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name in {"_build_parts", "_build_cut"}
    ]
    assert defined == []


def test_parts_and_cut_results_are_built_only_by_the_calculation_methods():
    assert _callers("PartsResult") == ["Calculation._parts"]
    assert _callers("CutResult") == ["Calculation._cut"]
    assert _callers("compute_parts") == ["Calculation._computed"]


def test_economy_copies_are_priced_and_built_by_a_calculation_of_the_copy():
    """Цены копий и детали копии со всеми заменами — расчёт копии (`_copy`), а не своя цепочка."""
    copies = _callers("_copy")
    assert copies.count("Calculation._economy") == 2  # каждая замена и все замены сразу
    assert copies.count("Calculation._economy_parts") == 2  # цена «как в проекте» и детали копии
