"""Имена `api.__all__` по тикетам этапа 9: перенос в `kitchen.calculation` (69) прежних имён не убирает,
расчёт (70) и сохранение (71) добавляют новую форму, тикет 75 удаляет старую."""

from kitchen import api

from tests.test_no_old_api import REMOVED

# `api.__all__` до тикета 69: перенос не убирает из интерфейса ни одного имени.
BEFORE_69 = {
    "AssemblyModule",
    "AssemblyPageLayout",
    "AssemblyResult",
    "BriefSaveResult",
    "BriefShowResult",
    "CheckResult",
    "CommonItem",
    "DAYS_ERROR",
    "DiffResult",
    "DrillingCheck",
    "DrillingResult",
    "EconomyLine",
    "EconomyVariant",
    "EdgeTotals",
    "EdgingResult",
    "EstimateLabor",
    "EstimateResult",
    "ExportResult",
    "FileSummary",
    "Finding",
    "HOLE_PURPOSES",
    "ImportResult",
    "LaborLine",
    "LogEntry",
    "LogResult",
    "ModuleParts",
    "OUTPUT_RECOMPUTE",
    "OrderInfo",
    "OrderResult",
    "OutputInfo",
    "PRODUCTION_KINDS",
    "PRODUCTION_RECOMPUTE",
    "PageLayout",
    "PartsResult",
    "PartsSource",
    "PlacementResult",
    "ProductionResult",
    "PurchaseResult",
    "QuickEstimate",
    "QuoteResult",
    "StaleOutput",
    "Stamp",
    "StatusResult",
    "ValidationContext",
    "ValidationResult",
    "ViewPage",
    "ViewsResult",
    "WorkshopCheck",
    "assembly_and_save",
    "assembly_from_order",
    "assembly_from_project",
    "assembly_layout",
    "brief_save",
    "brief_show",
    "check_order",
    "check_project",
    "check_workshop",
    "cut_and_save",
    "cut_from_order",
    "cut_from_project",
    "diff_project",
    "drilling_and_save",
    "drilling_from_order",
    "drilling_from_project",
    "drilling_map_layout",
    "economy_parts_from_project",
    "edges_and_save",
    "edges_from_order",
    "edges_from_project",
    "estimate_and_save",
    "estimate_from_order",
    "estimate_from_project",
    "export_project",
    "group_digits",
    "import_project",
    "list_outputs",
    "load_workshop",
    "log_add",
    "money",
    "order_new",
    "order_show",
    "parse_days",
    "part_in_room",
    "parts_from_order",
    "parts_from_project",
    "percent",
    "placement_from_order",
    "placement_from_project",
    "plural",
    "production_and_save",
    "purchase_and_save",
    "purchase_from_order",
    "quick_estimate",
    "quote_and_save",
    "quote_from_order",
    "quote_from_project",
    "resolve_home",
    "status",
    "trip_and_save",
    "trip_from_order",
    "trip_from_project",
    "trip_layout",
    "validate_wrapper",
    "views_and_save",
    "views_from_order",
    "views_from_project",
    "views_layout",
}

# Тесты и CLI берут их из `api` (`test_status_stale.py`, `cli.py`), а в `__all__` их не было.
ADDED_IN_69 = {"OutputKind", "PartsGap", "working_days"}

# Тикет 70: расчёт — новая форма интерфейса рядом со старой.
ADDED_IN_70 = {"calculation"}

# Тикет 71: сохранение выводов расчёта.
ADDED_IN_71 = {"save", "production"}


# Тикет 75: типы результатов, которые тесты берут из `api`, — в `__all__` (спека: «все типы результатов»).
ADDED_IN_75 = {"CutResult", "TripResult"}


def test_api_all_is_the_new_form_without_the_old_one():
    expected = (BEFORE_69 | ADDED_IN_69 | ADDED_IN_70 | ADDED_IN_71 | ADDED_IN_75) - REMOVED
    assert set(api.__all__) == expected


def test_api_all_has_the_calculation_and_no_removed_name():
    """Тикет 75 (п. 4)."""
    assert not set(api.__all__) & REMOVED
    assert {"calculation", "save", "production", "Stamp"} <= set(api.__all__)


def test_every_name_in_api_all_resolves():
    missing = [name for name in api.__all__ if not hasattr(api, name)]
    assert not missing, missing
