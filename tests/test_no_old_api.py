"""Тикет 71 (п. 12): тесты не зовут старую форму `api`, которую удаляет тикет 75 (спека этапа 9,
«Удаление старой формы»). Файлы, которые её ещё зовут, перечислены в `OLD_API_EXCEPTIONS`; тикеты 73–74
переводят их на расчёт, и список только сокращается (храповик ниже)."""

import ast
from pathlib import Path

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
    "test_assembly.py",
    "test_assembly_pdf.py",
    "test_assembly_site.py",
    "test_backlog_61.py",
    "test_checks.py",
    "test_checks_brief.py",
    "test_cleanup_58.py",
    "test_corner_row_layout.py",
    "test_corner_side.py",
    "test_corner_templates.py",
    "test_countertop.py",
    "test_cut_pdf.py",
    "test_cutting.py",
    "test_dishwasher_handle.py",
    "test_drilling.py",
    "test_drilling_pdf.py",
    "test_drilling_pins_runners.py",
    "test_economy.py",
    "test_economy_workshop.py",
    "test_edges.py",
    "test_estimate.py",
    "test_formulas.py",
    "test_gap.py",
    "test_gap_appliances.py",
    "test_gap_neighbours.py",
    "test_glossary_abbreviations.py",
    "test_handles_one_place.py",
    "test_input_guards.py",
    "test_input_guards_project.py",
    "test_lower_templates.py",
    "test_mock_warning_json.py",
    "test_module_params.py",
    "test_order_skill.py",
    "test_parts_api.py",
    "test_petrov.py",
    "test_placement.py",
    "test_placement_edges.py",
    "test_placement_tall_order.py",
    "test_placement_tall_top.py",
    "test_project_cleanup.py",
    "test_project_import_diff.py",
    "test_purchase.py",
    "test_quote.py",
    "test_skill_checks.py",
    "test_skill_cleanup_59.py",
    "test_stamp.py",
    "test_status_cleanup_57.py",
    "test_status_next_step.py",
    "test_status_stale.py",
    "test_tall_overlap.py",
    "test_tall_template.py",
    "test_trip.py",
    "test_upper_corner_depth.py",
    "test_upper_templates.py",
    "test_views.py",
    "test_views_html.py",
    "test_views_js.py",
    "test_workshop_number_grid.py",
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
