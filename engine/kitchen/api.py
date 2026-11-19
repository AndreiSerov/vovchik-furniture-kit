"""Публичный API движка: интерфейс над `kitchen.calculation` (ADR 0002). CLI только печатает результат."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kitchen import brief as brief_check
from kitchen import placement, store
from kitchen.assembly import AssemblyModule
from kitchen.assembly_pdf import AssemblyPageLayout, layout, write_assembly
from kitchen.calculation import (
    PRODUCTION_KINDS,
    AssemblyResult,
    CheckResult,
    CutResult,
    DrillingResult,
    EdgingResult,
    EstimateResult,
    LogEntry,
    OutputInfo,
    OutputKind,
    PartsResult,
    PartsSource,
    PlacementResult,
    ProductionResult,
    PurchaseResult,
    QuoteResult,
    TripResult,
    ValidationContext,
    ViewsResult,
    _assembly,
    _build_cut,
    _build_parts,
    _canonical,
    _current_version,
    _draft_warnings,
    _edging,
    _estimate,
    _estimate_markdown,
    _insert_log,
    _latest_brief,
    _open_order,
    _order_project,
    _output_info,
    _project_hash,
    _purchase,
    _purchase_markdown,
    _read_json,
    _save_outputs,
    _trip,
    _with_economy,
    _write_reason,
    calculation,
    parse_project_file,
)
from kitchen.checks import Finding
from kitchen.checks import check_project as run_checks
from kitchen.common import (
    CommonItem,
    pair_by_model_then_order,
)
from kitchen.cutting_pdf import write_cutting_map
from kitchen.drilling import DrillingCheck
from kitchen.drilling_pdf import PageLayout, map_layout, write_drilling_map
from kitchen.economy import EconomyLine, EconomyVariant
from kitchen.edging_pdf import write_edging_map
from kitchen.errors import InputError, KitchenError, OrderError
from kitchen.estimate import EstimateLabor, LaborLine
from kitchen.format import group_digits, num, plural, working_days
from kitchen.geometry import PURPOSE_RU
from kitchen.metrics import Stamp
from kitchen.models import Appliances, Economy, Hardware, Labor, Materials, Workshop
from kitchen.parts import (
    APPLIANCE_NAMES,
    ROW_NOM,
    EdgeTotals,
    ModuleParts,
    Part,
    PartsGap,
)
from kitchen.placement import ModulePlace, SceneObject
from kitchen.project import Project, parse_project
from kitchen.quote import DAYS_ERROR, build_quote, check_days, money, parse_days, percent
from kitchen.quote_pdf import write_quote
from kitchen.result import MOCK_BANNER, Result
from kitchen.trip_pdf import TripPageLayout, write_trip
from kitchen.trip_pdf import layout as trip_page_layout
from kitchen.views import ViewPage
from kitchen.views import layout as views_page_layout
from kitchen.views_html import write_html
from kitchen.views_pdf import write_views
from kitchen.workshop import FILES, Loaded, load, load_workshop, resolve_home

__all__ = [
    "DAYS_ERROR",
    "HOLE_PURPOSES",
    "OUTPUT_RECOMPUTE",
    "PRODUCTION_KINDS",
    "PRODUCTION_RECOMPUTE",
    "AssemblyModule",
    "AssemblyPageLayout",
    "AssemblyResult",
    "BriefSaveResult",
    "BriefShowResult",
    "CheckResult",
    "CommonItem",
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
    "ImportResult",
    "LaborLine",
    "LogEntry",
    "LogResult",
    "ModuleParts",
    "OrderInfo",
    "OrderResult",
    "OutputInfo",
    "OutputKind",
    "PageLayout",
    "PartsGap",
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
    "calculation",
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
    "working_days",
]


class FileSummary(Result):
    file: str
    counts: dict[str, int]


class WorkshopCheck(Result):
    valid: bool
    files: list[FileSummary]
    errors: list[str]
    warnings: list[str] = Field(default_factory=list)


# Что считать в сводке файла: поля моделей; русские ключи берём из алиасов моделей, а не повторяем здесь.
_COUNTED = {
    "materials.yaml": (Materials, ("sheets", "edges", "countertops", "facades")),
    "hardware.yaml": (Hardware, ("items",)),
    "appliances.yaml": (Appliances, ("items",)),
    "labor.yaml": (Labor, ("norms_min",)),
    "economy.yaml": (Economy, ("rules",)),
}


def _counts(name: str, data: dict) -> dict[str, int]:
    if name in _COUNTED:
        model, fields = _COUNTED[name]
        keys = [model.model_fields[f].alias or f for f in fields]
        # Скаляр под списочным ключом не считаем: его назовёт ошибка загрузки (файл и ключ).
        return {k: len(v) if isinstance(v := data.get(k), list | dict) else 0 for k in keys}
    return {"ключей": len([k for k in data if k != "mock"])}


def _workshop_warnings(raw: dict[str, dict]) -> list[str]:
    """Предупреждения, не ошибки: данные верны, но клиент увидит не то, что задумано."""
    items = raw.get("hardware.yaml", {}).get("позиции")
    found = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("для_клиента_мн") and not item.get("для_клиента"):
            found.append(
                f"hardware.yaml: позиция «{item.get('id')}»: есть для_клиента_мн, но нет для_клиента — "
                "в КП название этой позиции не пишется."
            )
    return found


def check_workshop(home: Path | None = None) -> WorkshopCheck:
    loaded = load(home or resolve_home())
    mock = loaded.mock_files
    files = [
        FileSummary(
            file=n, counts=_counts(n, loaded.raw[n]), mock=n in mock, mock_files=[n] if n in mock else []
        )
        for n in FILES
        if n in loaded.raw
    ]
    return WorkshopCheck(
        valid=not loaded.problems,
        files=files,
        errors=loaded.problems,
        warnings=_workshop_warnings(loaded.raw),
        mock=bool(mock),
        mock_files=mock,
    )


# --- заказы ------------------------------------------------------------------


class OrderInfo(BaseModel):
    id: str
    number: str
    slug: str
    client: str
    phone: str
    address: str
    created_at: str
    updated_at: str


class OrderResult(Result):
    order: OrderInfo
    created: bool = False


class LogResult(Result):
    order: str
    entry: LogEntry


class ImportResult(Result):
    order: str
    changed: bool
    version: int
    previous_version: int | None
    content_hash: str
    warnings: list[str] = Field(default_factory=list)
    brief_id: int | None = None  # бриф, с которым сверена техника


class ExportResult(Result):
    order: str
    version: int
    path: str
    project: dict[str, Any]


class StaleOutput(BaseModel):
    """Устаревший вывод: версия-источник меньше текущей версии проекта."""

    kind: store.OutputKind
    path: str  # относительно KITCHEN_HOME, как в БД
    source_version: int
    command: str  # команда пересчёта целиком, `kitchen cut <заказ>`


class NextStep(BaseModel):
    """Следующий шаг по заказу: фраза для status.md и команды из неё."""

    text: str
    commands: list[str] = Field(default_factory=list)


class StatusResult(Result):
    order: str
    stage: str
    project_version: int | None
    path: str
    text: str
    stale: list[StaleOutput] = Field(default_factory=list)
    next_step: NextStep


OUTPUT_RECOMPUTE = store.OUTPUT_RECOMPUTE
PRODUCTION_RECOMPUTE = store.PRODUCTION_RECOMPUTE
# Команды шагов «Следующий шаг», у которых нет вывода и записи в реестре пересчёта (тикет 49).
IMPORT_COMMAND = store.Recompute("импорт проекта", "import", "<файл>")
BRIEF_SAVE_COMMAND = store.Recompute("сохранение брифа", "brief save", "<файл>")


LOG_KINDS = ("done", "decision", "next", "note")


def _info(row: sqlite3.Row) -> OrderInfo:
    return OrderInfo(
        id=store.order_ref_of(row),
        **{k: row[k] for k in ("number", "slug", "client", "phone", "address", "created_at", "updated_at")},
    )


def _mock_of(loaded: Loaded) -> dict[str, Any]:
    """Плашка мока на всех командах заказа: показываем, пока хоть один файл мастерской мок."""
    return {"mock": bool(loaded.mock_files), "mock_files": loaded.mock_files}


def is_mock(home: Path | None = None) -> bool:
    """Мок ли мастерская сейчас; мастерской нет — `False` (выводить нечего). Для плашки на сообщении об ошибке."""
    try:
        return bool(load(home or resolve_home()).mock_files)
    except KitchenError:
        return False


def _mock(home: Path) -> dict[str, Any]:
    return _mock_of(load(home))


def _insert_order(
    conn: sqlite3.Connection, number: str, slug: str, client: str, phone: str, address: str
) -> sqlite3.Row:
    """Проверка номера и вставка заказа; вызывать внутри `BEGIN IMMEDIATE` (параллельные запуски не гоняются)."""
    taken = conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()
    if taken:
        raise OrderError(f"Номер {number} уже занят заказом {store.order_ref_of(taken)}.")
    ts = store.now()
    try:
        conn.execute(
            "INSERT INTO orders (number, slug, client, phone, address, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (number, slug, client, phone, address, ts, ts),
        )
    except sqlite3.IntegrityError as e:
        raise OrderError(f"Номер {number} уже занят другим заказом.") from e
    return conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()


def order_new(
    order: str, client: str = "", phone: str = "", address: str = "", home: Path | None = None
) -> OrderResult:
    home = home or resolve_home()
    number, slug = store.parse_order_id(order)
    with store.connect(home) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _insert_order(conn, number, slug, client, phone, address)
        conn.commit()
        return OrderResult(order=_info(row), created=True, **_mock(home))


def order_show(query: str, home: Path | None = None) -> OrderResult:
    with _open_order(query, home) as o:
        return OrderResult(order=_info(o.row), **_mock(o.home))


# --- проект: импорт и экспорт ------------------------------------------------


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)  # отклоняют импорт
    warnings: list[str] = field(default_factory=list)  # пишутся в журнал как note
    brief_id: int | None = None  # какой бриф использован при проверке


Validator = Callable[[Any, ValidationContext], ValidationResult]


def validate_wrapper(data: Any, ctx: ValidationContext) -> ValidationResult:
    """Проверить заказ, схему и конструкцию проекта (`kitchen.checks`): ошибки отклоняют импорт."""
    try:
        project = parse_project_file(data, ctx)
    except OrderError as e:
        return ValidationResult(errors=e.problems)
    if ctx.workshop is None:  # без мастерской проект не проверить, а непроверенный не принимаем
        return ValidationResult(
            errors=[
                "Мастерская не загрузилась, проект проверить нельзя (см. kitchen check-workshop):",
                *ctx.workshop_problems,
            ]
        )
    findings = run_checks(project, ctx.workshop, ctx.brief)
    return ValidationResult(
        errors=[f.message for f in findings if f.level == "error"],
        warnings=[f.message for f in findings if f.level == "warning"],
        brief_id=ctx.brief_id,
    )


BRIEF_SAVED = "бриф сохранён"  # единственная авто-запись «сделано», которая «Дальше» не сбрасывает


def _write_text(path: Path, text: str) -> None:
    """Запись файла заказа: папка вместо файла, нет доступа, нет места — русская ошибка, а не трейсбек."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        raise OrderError(f"Не удалось записать файл {path}: {_write_reason(e)}.") from e


def _same_project(version: sqlite3.Row, digest: str) -> bool:
    """Версия в БД совпадает с проектом по нормализованному хешу.

    Версии, записанные до нормализации, хранят хеш сырого JSON: их хеш пересчитывается из данных версии,
    история не переписывается."""
    if version["content_hash"] == digest:
        return True
    try:
        return _data_hash(json.loads(version["data"])) == digest
    except ValueError:
        return False


def import_project(
    order: str, path: Path, validate: Validator = validate_wrapper, home: Path | None = None
) -> ImportResult:
    home = home or resolve_home()
    data = _read_json(Path(path), "Файл проекта")
    with _open_order(order, home) as o:
        loaded = load(home)
        mock = _mock_of(loaded)
        current = _current_version(o.conn, o.pk)
        latest = _latest_brief(o.conn, o.pk)
        ctx = ValidationContext(
            o.ref,
            o.row["number"],
            loaded.workshop,
            loaded.problems,
            json.loads(latest["data"]) if latest else None,
            latest["id"] if latest else None,
        )
        checked = validate(data, ctx)
        if checked.errors:
            tail = "Проект отклонён, текущая версия не изменена." if current else "Проект отклонён."
            raise OrderError([*checked.errors, tail])
        data = {k: v for k, v in data.items() if k != "version"}  # номер версии ставит движок
        text = _canonical(data)
        digest = _data_hash(data)
        o.conn.execute("BEGIN IMMEDIATE")
        current = _current_version(o.conn, o.pk)  # перечитать под блокировкой
        prev = current["version"] if current else None
        if current and _same_project(current, digest):
            return ImportResult(
                order=o.ref,
                changed=False,
                version=prev,
                previous_version=prev,
                content_hash=digest,
                warnings=checked.warnings,
                brief_id=current["brief_id"],  # бриф, записанный у версии, а не новый
                **mock,
            )
        version = (prev or 0) + 1
        o.conn.execute(
            "INSERT INTO project_versions (order_id, version, data, content_hash, created_at, brief_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (o.pk, version, text, digest, store.now(), checked.brief_id),
        )
        for warning in checked.warnings:
            _insert_log(o.conn, o.pk, "note", f"Предупреждение при импорте: {warning}", version)
        if checked.brief_id is not None:
            _insert_log(
                o.conn,
                o.pk,
                "note",
                f"Проверки проекта: техника сверена с брифом №{checked.brief_id}",
                version,
            )
        _insert_log(o.conn, o.pk, "done", f"принята версия {version}", version, auto=True)
        o.changed = True
        return ImportResult(
            order=o.ref,
            changed=True,
            version=version,
            previous_version=prev,
            content_hash=digest,
            warnings=checked.warnings,
            brief_id=checked.brief_id,
            **mock,
        )


def export_project(
    order: str, version: int | None = None, out: Path | None = None, home: Path | None = None
) -> ExportResult:
    with _open_order(order, home) as o:
        if version is None:
            found = _current_version(o.conn, o.pk)
            if not found:
                raise OrderError(f"У заказа {o.ref} проекта ещё нет. Сначала kitchen import.")
        else:
            found = o.conn.execute(
                "SELECT * FROM project_versions WHERE order_id = ? AND version = ?", (o.pk, version)
            ).fetchone()
            if not found:
                raise OrderError(f"У заказа {o.ref} нет версии {version} проекта.")
        project = {**json.loads(found["data"]), "version": found["version"]}
        target = Path(out) if out else o.home / "orders" / o.ref / "project.json"
        _write_text(target, json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return ExportResult(
            order=o.ref, version=found["version"], path=str(target), project=project, **_mock(o.home)
        )


class DiffResult(Result):
    order: str
    from_version: int
    to_version: int
    changes: list[str]


def _modules(data: dict) -> dict[str, tuple[dict, str, str, int]]:
    """Записи рядов (модули и пустые места) по меткам: запись, стена, вид ряда, место среди всех записей ряда."""
    modules = {}
    for wall in data.get("walls", []):
        for row in wall.get("rows", []):
            for index, module in enumerate(row.get("modules", [])):
                label = module["label"]
                if not isinstance(label, str) or label in modules or row["kind"] not in ("lower", "upper"):
                    raise ValueError("повторная метка или неверный ряд")
                modules[label] = (module, wall["label"], row["kind"], index)
    return modules


_DIFF_NAMES = {
    "template": "шаблон",
    "width": "ширина",
    "height": "высота",
    "depth": "глубина",
    "shelves": "полки",
    "doors": "двери",
    "drawers": "ящики",
    "materials": "материалы",
    "body": "корпус",
    "facades": "фасады",
    "facade_color": "цвет фасада",
    "handles": "ручки",
    "countertop": "столешница",
    "contents": "наполнение",
    "inserts": "вставки",
    "lift": "подъёмник",
    "without_back": "без задней стенки",
    "corner": "угол",
    "adjacent_wall": "соседняя стена",
    "under_countertop": "под столешницей",
}


def _value(value: Any) -> str:
    if value is None:
        return "не задано"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _changes(before: dict, after: dict, prefix: str = "") -> list[str]:
    changes = []
    for key in sorted(before.keys() | after.keys()):
        if key == "label":
            continue
        left, right = before.get(key), after.get(key)
        if left == right:
            continue
        name = f"{prefix}{_DIFF_NAMES.get(key, key)}"
        if (
            (isinstance(left, dict) or isinstance(right, dict))
            and (left is None or isinstance(left, dict))
            and (right is None or isinstance(right, dict))
        ):
            changes.extend(_changes(left or {}, right or {}, name + ": "))
        else:
            changes.append(f"{name} {_value(left)} → {_value(right)}")
    return changes


_APPLIANCE_FIELDS = {
    "width": _DIFF_NAMES["width"],
    "model": "модель",
    "height": _DIFF_NAMES["height"],
    "depth": _DIFF_NAMES["depth"],
    "niche_width": "ширина ниши",
    "niche_height": "высота ниши",
    "niche_depth": "глубина ниши",
}


def _appliance_name(item: dict) -> str:
    return APPLIANCE_NAMES.get(item["type"], item["type"])


def _appliances_of(data: dict) -> dict[str, list[dict]]:
    """Техника проекта по меткам модулей и мест."""
    found: dict[str, list[dict]] = {}
    for item in data.get("appliances", []):
        if not isinstance(item["type"], str):
            raise TypeError("вид техники не строка")
        found.setdefault(item["module"], []).append(item)
    return found


def _kit(items: list[dict] | None) -> str:
    """Состав техники места: «холодильник, плита» или пусто."""
    return ", ".join(sorted(_appliance_name(i) for i in items or []))


def _appliance_changes(before: list[dict] | None, after: list[dict] | None) -> list[str]:
    """Поля прибора одного вида, который стоит на месте в обеих версиях: пары — как в «Составе техники»
    (сначала по модели, затем по порядку; `common.pair_by_model_then_order`)."""
    changes = []
    kinds = [i["type"] for i in [*(before or []), *(after or [])]]
    for kind in dict.fromkeys(kinds):
        pairs, _, _ = pair_by_model_then_order(
            [i for i in before or [] if i["type"] == kind],
            [i for i in after or [] if i["type"] == kind],
            lambda i: i.get("model"),
        )
        for old, new in pairs:
            if new is None:
                continue
            for key, name in _APPLIANCE_FIELDS.items():
                if old.get(key) != new.get(key):
                    changes.append(
                        f"{_appliance_name(new)}: {name} {_value(old.get(key))} → {_value(new.get(key))}"
                    )
    return changes


def diff_project(order: str, from_version: int, to_version: int, home: Path | None = None) -> DiffResult:
    """Сравнить сохранённые версии проекта по меткам модулей."""
    with _open_order(order, home) as o:
        projects = []
        for version in (from_version, to_version):
            row = o.conn.execute(
                "SELECT data FROM project_versions WHERE order_id = ? AND version = ?", (o.pk, version)
            ).fetchone()
            if not row:
                raise OrderError(f"У заказа {o.ref} нет версии {version} проекта.")
            data = json.loads(row["data"])
            try:
                projects.append((_modules(data), _appliances_of(data)))
            except (TypeError, KeyError, ValueError, AttributeError) as e:
                raise OrderError(
                    f"Версия {version} не соответствует текущей схеме проекта: "
                    "невозможно определить модули, стены или ряды."
                ) from e
        (before, before_kit), (after, after_kit) = projects
        changes = []
        for label in sorted(before.keys() | after.keys()):
            if label not in before:
                new, new_wall, new_row, new_pos = after[label]
                if new.get("gap"):
                    where = f"{new_wall}, {ROW_NOM[new_row]}, {new_pos + 1}"
                    changes.append(f"{label}: добавлено пустое место {_value(new['width'])} мм ({where})")
                else:
                    changes.append(f"{label}: добавлен модуль")
            elif label not in after:
                changes.append(
                    f"{label}: удалено пустое место"
                    if before[label][0].get("gap")
                    else f"{label}: удалён модуль"
                )
            else:
                old, old_wall, old_row, old_pos = before[label]
                new, new_wall, new_row, new_pos = after[label]
                if bool(old.get("gap")) != bool(new.get("gap")):
                    now = "пустое место" if new.get("gap") else "модуль"
                    changes.append(f"{label}: {'модуль' if new.get('gap') else 'пустое место'} → {now}")
                else:
                    changes.extend(f"{label}: {line}" for line in _changes(old, new))
                    changes.extend(
                        f"{label}: {line}"
                        for line in _appliance_changes(before_kit.get(label), after_kit.get(label))
                    )
                    if _kit(before_kit.get(label)) != _kit(after_kit.get(label)):
                        changes.append(
                            f"{label}: техника {_value(_kit(before_kit.get(label)) or None)} → "
                            f"{_value(_kit(after_kit.get(label)) or None)}"
                        )
                if (old_wall, old_row, old_pos) != (new_wall, new_row, new_pos):
                    changes.append(
                        f"{label}: положение {old_wall}, {ROW_NOM[old_row]}, {old_pos + 1} → "
                        f"{new_wall}, {ROW_NOM[new_row]}, {new_pos + 1}"
                    )
        return DiffResult(
            order=o.ref, from_version=from_version, to_version=to_version, changes=changes, **_mock(o.home)
        )


# --- журнал и статус ---------------------------------------------------------


def log_add(order: str, kind: str, text: str, home: Path | None = None) -> LogResult:
    if kind not in LOG_KINDS:
        raise OrderError(f"Тип записи «{kind}» неизвестен. Допустимо: {', '.join(LOG_KINDS)}.")
    if not text.strip():
        raise OrderError("Текст записи пустой.")
    with _open_order(order, home) as o:
        current = _current_version(o.conn, o.pk)
        entry = _insert_log(o.conn, o.pk, kind, text.strip(), current["version"] if current else None)
        o.changed = True
        return LogResult(order=o.ref, entry=entry, **_mock(o.home))


STATUS_HEADER = (
    "> Сгенерировано `kitchen status`. Не правьте руками: файл перезаписывается. "
    "Записи добавляются через `kitchen log`."
)


def _stage(has_project: bool, has_outputs: bool, has_brief: bool) -> str:
    """Этап заказа по тому, что уже есть: выводы -> «Выводы», проект -> «Проект», бриф -> «Бриф»."""
    if has_outputs:
        return "Выводы"
    if has_project:
        return "Проект"
    return "Бриф" if has_brief else "Новый заказ"


def _section(title: str, lines: list[str], empty: str) -> list[str]:
    return [f"## {title}", "", *(lines or [empty]), ""]


def _stale_outputs(
    conn: sqlite3.Connection, order_pk: int, order_ref: str, current_version: int | None
) -> list[StaleOutput]:
    """Выводы, чья версия-источник меньше текущей версии проекта (смена данных мастерской не в счёт)."""
    if current_version is None:
        return []
    rows = conn.execute(
        "SELECT kind, path, source_version FROM outputs WHERE order_id = ? AND source_version < ? ORDER BY id",
        (order_pk, current_version),
    ).fetchall()
    unknown = sorted({r["kind"] for r in rows if r["kind"] not in OUTPUT_RECOMPUTE})
    if unknown:
        raise OrderError(
            f"В БД есть выводы неизвестного вида: {', '.join(unknown)}. "
            "Их записал более новый движок — обновите kitchen."
        )
    return [
        StaleOutput(
            kind=r["kind"],
            path=r["path"],
            source_version=r["source_version"],
            command=" ".join(
                part
                for part in (
                    "kitchen",
                    OUTPUT_RECOMPUTE[r["kind"]].command,
                    order_ref,
                    OUTPUT_RECOMPUTE[r["kind"]].args,
                )
                if part
            ),
        )
        for r in rows
    ]


def _stale_line(s: StaleOutput) -> str:
    label = OUTPUT_RECOMPUTE[s.kind].label
    return f"- {label} `{s.path}`: версия {s.source_version}, пересчитать: `{s.command}`"


def _command(recompute: store.Recompute, order_ref: str) -> str:
    return " ".join(p for p in ("kitchen", recompute.command, order_ref, recompute.args) if p)


def _next_step(
    order_ref: str, has_brief: bool, has_project: bool, stale: list[StaleOutput], kinds: set[str]
) -> NextStep:
    """Первое подходящее правило таблицы тикета 49."""
    if not has_project:
        if has_brief:
            cmd = _command(IMPORT_COMMAND, order_ref)
            return NextStep(text=f"Сделать проект: kitchen-project, затем {cmd}.", commands=[cmd])
        cmd = _command(BRIEF_SAVE_COMMAND, order_ref)
        return NextStep(text=f"Собрать бриф: kitchen-brief, затем {cmd}.", commands=[cmd])
    due = [
        s for s in stale if s.kind != store.DRILLING
    ]  # карта присадки следующим шагом не зовётся (тикет 57)
    if due:
        workshop = any(s.kind in PRODUCTION_KINDS for s in due)
        commands = list(dict.fromkeys(s.command for s in due if s.kind not in PRODUCTION_KINDS))
        if workshop:  # один `kitchen production` вместо отдельных команд цеха, после сметы и КП
            commands.append(_command(PRODUCTION_RECOMPUTE, order_ref))
        return NextStep(text=f"Пересчитать устаревшее: {', '.join(commands)}.", commands=commands)
    estimate = _command(OUTPUT_RECOMPUTE[store.ESTIMATE_MD], order_ref)
    if store.QUOTE_PDF not in kinds:
        quote = _command(OUTPUT_RECOMPUTE[store.QUOTE_PDF], order_ref)
        return NextStep(text=f"Смета и КП: {estimate}, затем {quote}.", commands=[estimate, quote])
    if store.ESTIMATE_MD not in kinds:
        return NextStep(text=f"Смета: {estimate}.", commands=[estimate])
    if not set(PRODUCTION_KINDS) <= kinds:
        cmd = _command(PRODUCTION_RECOMPUTE, order_ref)
        return NextStep(text=f"Документы цеха: {cmd}.", commands=[cmd])
    return NextStep(text="Все документы готовы: выезд на монтаж по чек-листу.")


def status(order: str, home: Path | None = None, tz: tzinfo | None = None) -> StatusResult:
    """Собирает status.md. Даты в нём — по местному времени (`tz` — для тестов)."""

    def day(ts: str) -> str:
        return store.local_date(ts, tz)

    with _open_order(order, home) as o:
        cur = _current_version(o.conn, o.pk)

        def entries(kind: str, limit: int = -1) -> list[sqlite3.Row]:
            return o.conn.execute(
                "SELECT * FROM log WHERE order_id = ? AND kind = ? ORDER BY id DESC LIMIT ?",
                (o.pk, kind, limit),
            ).fetchall()

        def dated(rows: list[sqlite3.Row]) -> list[str]:
            return [f"- {day(r['created_at'])}: {r['text']}" for r in rows]

        def exists(table: str) -> bool:
            sql = f"SELECT 1 FROM {table} WHERE order_id = ?"  # имя таблицы из кода, не от пользователя
            return bool(o.conn.execute(sql, (o.pk,)).fetchone())

        kinds = {r["kind"] for r in o.conn.execute("SELECT kind FROM outputs WHERE order_id = ?", (o.pk,))}
        has_brief = exists("briefs")
        stage = _stage(cur is not None, bool(kinds), has_brief)
        who = ", ".join(x for x in (o.row["client"], o.row["phone"], o.row["address"]) if x)
        lines = [f"# Заказ {o.ref}", "", STATUS_HEADER, ""]
        if who:
            lines += [who, ""]
        lines += ["## Этап", "", stage, "", "## Версия проекта", ""]
        lines += [f"Версия {cur['version']} от {day(cur['created_at'])}."] if cur else ["Проекта ещё нет."]
        lines.append("")
        stale = _stale_outputs(o.conn, o.pk, o.ref, cur["version"] if cur else None)
        lines += _section("Устарело", [_stale_line(s) for s in stale], "Пока ничего.")
        next_step = _next_step(o.ref, has_brief, cur is not None, stale, kinds)
        lines += _section("Следующий шаг", [next_step.text], "")
        lines += _section("Решения клиента", dated(entries("decision")[::-1]), "Пока нет.")
        lines += _section("Сделано", dated(entries("done")[::-1]), "Пока ничего.")
        last_done = o.conn.execute(
            "SELECT MAX(id) FROM log WHERE order_id = ? AND kind = 'done' AND (auto = 0 OR text != ?)",
            (o.pk, BRIEF_SAVED),
        ).fetchone()[0]
        fresh = [r for r in entries("next", 5) if last_done is None or r["id"] > last_done]
        lines += _section("Дальше", dated(fresh), "Пока ничего.")
        notes = entries("note")
        if notes:
            lines += _section("Заметки", dated(notes[::-1]), "")
        text = "\n".join(lines).rstrip() + "\n"
        mock = _mock(o.home)
        target = o.home / "orders" / o.ref / "status.md"
        _write_text(target, f"{MOCK_BANNER}\n\n{text}\n{MOCK_BANNER}\n" if mock["mock"] else text)
        return StatusResult(
            order=o.ref,
            stage=stage,
            project_version=cur["version"] if cur else None,
            path=str(target),
            text=text,
            stale=stale,
            next_step=next_step,
            **mock,
        )


# --- бриф --------------------------------------------------------------------


class BriefSaveResult(Result):
    order: str
    created: bool
    saved_at: str
    missing_required: int
    conflicts: int
    check: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)  # бриф сохранён, но status.md не записан


class BriefShowResult(Result):
    order: str
    saved_at: str
    brief: dict[str, Any]
    check: dict[str, Any]


def _next_number(conn: sqlite3.Connection) -> str:
    year = store.today().year
    taken = conn.execute("SELECT number FROM orders WHERE number LIKE ?", (f"{year}-%",)).fetchall()
    nxt = max((int(r["number"][5:]) for r in taken), default=0) + 1
    if nxt > 999:
        raise OrderError(f"В {year} году заказы закончились: номера выше {year}-999 не бывает.")
    return f"{year}-{nxt:03d}"


def _resolve_for_brief(
    conn: sqlite3.Connection, query: str, client: str, phone: str
) -> tuple[sqlite3.Row, bool]:
    """Заказ для брифа (внутри `BEGIN IMMEDIATE`).

    Номер — существующий заказ. Номер-фамилия — существующий или новый с этим номером. Одна фамилия —
    только новый заказ: если заказ с точно такой фамилией уже есть, это ошибка со списком.
    """
    if store.is_phone_query(query):  # заказ по телефону: новый по телефону не заводится
        return store.find_order(conn, query), False
    parsed = store.parse_query(query)
    if parsed.kind == "number":
        return store.find_order(conn, query), False
    if parsed.kind == "full":
        if conn.execute("SELECT 1 FROM orders WHERE number = ?", (parsed.number,)).fetchone():
            return store.find_order(conn, query), False
        return _insert_order(conn, parsed.number, parsed.slug, client, phone, ""), True
    same = conn.execute("SELECT * FROM orders WHERE slug = ? ORDER BY number", (parsed.slug,)).fetchall()
    if same:
        listing = ", ".join(store.order_ref_of(r) for r in same)
        problems = [
            f"Заказ с фамилией «{parsed.slug}» уже есть: {listing}.",
            "Чтобы обновить его бриф: kitchen brief save <номер-фамилия из списка> brief.json.",
        ]
        try:  # подсказка не должна заслонять главную ошибку
            problems.append(
                f"Чтобы завести новый: kitchen brief save {_next_number(conn)}-{parsed.slug} brief.json."
            )
        except OrderError:
            pass
        raise OrderError(problems)
    number = _next_number(conn)
    return _insert_order(conn, number, parsed.slug, client, phone, ""), True


def brief_save(order: str, brief_path: Path, home: Path | None = None) -> BriefSaveResult:
    """Сохраняет бриф заказа. Заказа нет — создаёт (по фамилии берёт следующий номер года)."""
    home = home or resolve_home()
    brief = _read_json(Path(brief_path), "Файл брифа")
    if not isinstance(brief, dict):
        raise OrderError("Бриф должен быть JSON-объектом ({...}), а не списком или значением.")
    check = brief_check.check(brief, store.today())  # неизвестные поля и ошибки схемы отклоняют бриф
    mock = _mock(home)
    with store.connect(home) as conn:
        conn.execute("BEGIN IMMEDIATE")
        phone = brief_check.client_phone(brief)
        row, created = _resolve_for_brief(conn, order, brief_check.client_name(brief), phone)
        ts = store.now()
        if phone and not row["phone"]:  # телефон, уже записанный в заказ, не меняется
            conn.execute("UPDATE orders SET phone = ? WHERE id = ?", (phone, row["id"]))
        conn.execute(
            "INSERT INTO briefs (order_id, data, check_result, created_at) VALUES (?, ?, ?, ?)",
            (row["id"], _canonical(brief), json.dumps(check, ensure_ascii=False), ts),
        )
        current = _current_version(conn, row["id"])
        _insert_log(conn, row["id"], "done", BRIEF_SAVED, current["version"] if current else None, auto=True)
        conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (ts, row["id"]))
        conn.commit()
        result = BriefSaveResult(
            order=store.order_ref_of(row),
            created=created,
            saved_at=ts,
            missing_required=check["summary"]["missing_required"],
            conflicts=check["summary"]["conflicts"],
            check=check,
            **mock,
        )
    try:
        status(result.order, home)  # папка заказа и status.md: этап «Бриф», запись «бриф сохранён»
    except OrderError as e:  # бриф уже в БД: отказ заставил бы скилл сохранять его заново
        reason = " ".join(e.problems).rstrip(".")
        result.warnings.append(f"status.md не записан: {reason}. Повторите: kitchen status {result.order}.")
    return result


def brief_show(order: str, home: Path | None = None) -> BriefShowResult:
    """Последний сохранённый бриф заказа."""
    with _open_order(order, home) as o:
        row = o.conn.execute(
            "SELECT * FROM briefs WHERE order_id = ? ORDER BY id DESC LIMIT 1", (o.pk,)
        ).fetchone()
        if not row:
            raise OrderError(f"У заказа {o.ref} брифа ещё нет. Сначала kitchen brief save.")
        return BriefShowResult(
            order=o.ref,
            saved_at=row["created_at"],
            brief=json.loads(row["data"]),
            check=json.loads(row["check_result"]),
            **_mock(o.home),
        )


# --- детали проекта -----------------------------------------------------------


def _data_hash(data: dict) -> str:
    """Хеш проекта из JSON. Проект, который схема не читает (старая версия, чужая проверка), — хеш сырого JSON."""
    try:
        return _project_hash(parse_project(data))
    except OrderError:
        return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def parts_from_project(path: Path, home: Path | None = None) -> PartsResult:
    """`kitchen parts --project <файл>`: детали проекта из файла (черновик до импорта)."""
    return calculation(project=path, home=home).parts()


def parts_from_order(order: str, home: Path | None = None) -> PartsResult:
    """`kitchen parts <заказ>`: детали текущей версии проекта, проверки с последним брифом заказа."""
    return calculation(order=order, home=home).parts()


# --- раскрой ------------------------------------------------------------------


def cut_from_project(path: Path, home: Path | None = None) -> CutResult:
    """`kitchen cut --project <файл>`: раскрой деталей проекта из файла (черновик до импорта)."""
    return calculation(project=path, home=home).cut()


def cut_from_order(order: str, home: Path | None = None) -> CutResult:
    """`kitchen cut <заказ>`: раскрой деталей текущей версии проекта."""
    return calculation(order=order, home=home).cut()


CUTTING_PDF = "cutting-map.pdf"


def _save_output(
    home: Path, source: PartsSource, kind: OutputKind, filename: str, write_pdf: Callable[[Path], None]
) -> OutputInfo:
    info = _output_info(home, source, kind, filename)
    _save_outputs(home, source, [(info, write_pdf)])
    return info


def cut_and_save(order: str, home: Path | None = None, on: date | None = None) -> CutResult:
    """`kitchen cut <заказ>`: раскрой и сохранённый PDF карты."""
    home = home or resolve_home()
    result = cut_from_order(order, home)
    output = _save_output(
        home,
        result.source,
        store.CUTTING,
        CUTTING_PDF,
        lambda path: write_cutting_map(
            path,
            result.materials,
            kerf=result.kerf,
            kerf_declared=result.kerf_declared,
            trim=result.trim,
            stamp=Stamp(result.source.order, result.source.version, result.mock, on or store.today()),
        ),
    )
    return result.model_copy(update={"output": output})


def edges_from_order(order: str, home: Path | None = None) -> EdgingResult:
    return calculation(order=order, home=home).edging()


def edges_from_project(path: Path, home: Path | None = None) -> EdgingResult:
    return calculation(project=path, home=home).edging()


def edges_and_save(order: str, home: Path | None = None, on: date | None = None) -> EdgingResult:
    """Карта кромления текущей версии и её регистрация в заказе."""
    home = home or resolve_home()
    result = edges_from_order(order, home)
    output = _save_output(
        home,
        result.source,
        store.EDGING,
        "edging-map.pdf",
        lambda path: write_edging_map(
            path,
            result.parts,
            result.edges,
            purchase_digits=result.purchase_digits,
            stamp=Stamp(result.source.order, result.source.version, result.mock, on or store.today()),
        ),
    )
    return result.model_copy(update={"output": output})


# --- присадка -----------------------------------------------------------------


HOLE_PURPOSES = PURPOSE_RU  # назначение отверстия -> по-русски, для текста CLI


def drilling_from_order(order: str, home: Path | None = None) -> DrillingResult:
    """Присадка текущей версии проекта заказа, без записи файлов."""
    return calculation(order=order, home=home).drilling()


def drilling_from_project(path: Path, home: Path | None = None) -> DrillingResult:
    """`kitchen drilling --project <файл>`: присадка проекта из файла, файлов не пишет."""
    return calculation(project=path, home=home).drilling()


DRILLING_PDF = "drilling-map.pdf"


def drilling_map_layout(result: DrillingResult) -> list[PageLayout]:
    """Раскладка страниц карты присадки (строки, контуры, базовые кромки, цепочки) — ровно то, что рисует PDF."""
    return map_layout(result.parts, result.checks)


def _drilling_writer(result: DrillingResult, on: date | None) -> Callable[[Path], None]:
    return lambda path: write_drilling_map(
        path,
        result.parts,
        result.checks,
        stamp=Stamp(result.source.order, result.source.version, result.mock, on or store.today()),
    )


def drilling_and_save(order: str, home: Path | None = None, on: date | None = None) -> DrillingResult:
    """`kitchen drilling <заказ>`: присадка текущей версии, PDF карты и регистрация вывода (как у раскроя)."""
    home = home or resolve_home()
    result = drilling_from_order(order, home)
    output = _save_output(home, result.source, store.DRILLING, DRILLING_PDF, _drilling_writer(result, on))
    return result.model_copy(update={"output": output})


# --- сборочный лист -----------------------------------------------------------


def assembly_from_order(order: str, home: Path | None = None) -> AssemblyResult:
    """Сборочный лист текущей версии проекта заказа, без записи файлов."""
    return calculation(order=order, home=home).assembly()


def assembly_from_project(path: Path, home: Path | None = None) -> AssemblyResult:
    """`kitchen assembly --project <файл>`: сборочный лист проекта из файла, файлов не пишет."""
    return calculation(project=path, home=home).assembly()


ASSEMBLY_PDF = "assembly.pdf"


def assembly_layout(result: AssemblyResult) -> list[AssemblyPageLayout]:
    """Раскладка страниц сборочного листа (строки, кегли, положение) — ровно то, что рисует PDF."""
    return layout(result.modules)


def assembly_and_save(order: str, home: Path | None = None, on: date | None = None) -> AssemblyResult:
    """`kitchen assembly <заказ>`: сборочный лист текущей версии, PDF и регистрация вывода."""
    home = home or resolve_home()
    result = assembly_from_order(order, home)
    output = _save_output(home, result.source, store.ASSEMBLY, ASSEMBLY_PDF, _assembly_writer(result, on))
    return result.model_copy(update={"output": output})


def _assembly_writer(result: AssemblyResult, on: date | None) -> Callable[[Path], None]:
    return lambda path: write_assembly(
        path,
        result.modules,
        stamp=Stamp(result.source.order, result.source.version, result.mock, on or store.today()),
    )


# --- чек-лист выезда ----------------------------------------------------------


def trip_from_order(order: str, home: Path | None = None) -> TripResult:
    """Чек-лист выезда текущей версии проекта заказа, без записи файлов."""
    return calculation(order=order, home=home).trip()


def trip_from_project(path: Path, home: Path | None = None) -> TripResult:
    """`kitchen trip --project <файл>`: чек-лист из файла, файлов не пишет; брифа у файла нет — шапка из «—»."""
    return calculation(project=path, home=home).trip()


TRIP_PDF = "trip-checklist.pdf"


def trip_layout(result: TripResult) -> list[TripPageLayout]:
    """Раскладка страниц чек-листа (строки, кегли, положение) — ровно то, что рисует PDF."""
    return trip_page_layout(result.header, result.sections)


def trip_and_save(order: str, home: Path | None = None, on: date | None = None) -> TripResult:
    """`kitchen trip <заказ>`: чек-лист текущей версии, PDF и регистрация вывода."""
    home = home or resolve_home()
    result = trip_from_order(order, home)
    output = _save_output(home, result.source, store.TRIP, TRIP_PDF, _trip_writer(result, on))
    return result.model_copy(update={"output": output})


def _trip_writer(result: TripResult, on: date | None) -> Callable[[Path], None]:
    return lambda path: write_trip(
        path,
        result.header,
        result.sections,
        stamp=Stamp(result.source.order, result.source.version, result.mock, on or store.today()),
    )


def purchase_from_order(order: str, home: Path | None = None) -> PurchaseResult:
    return calculation(order=order, home=home).purchase()


def _purchase_files(
    home: Path, result: PurchaseResult
) -> tuple[PurchaseResult, list[tuple[OutputInfo, Callable]]]:
    outputs = [
        _output_info(home, result.source, store.PURCHASE_MD, "purchase.md"),
        _output_info(home, result.source, store.PURCHASE_JSON, "purchase.json"),
    ]
    result = result.model_copy(update={"outputs": outputs})
    return result, [
        (outputs[0], lambda path: path.write_text(_purchase_markdown(result), encoding="utf-8")),
        (outputs[1], lambda path: path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")),
    ]


def purchase_and_save(order: str, home: Path | None = None) -> PurchaseResult:
    home = home or resolve_home()
    result, files = _purchase_files(home, purchase_from_order(order, home))
    _save_outputs(home, result.source, files)
    return result


def _estimate_run(
    project: Project,
    home: Path,
    source: PartsSource,
    brief: sqlite3.Row | None,
    warnings: list[str],
    workshop: Workshop,
    economy: bool,
) -> tuple[EstimateResult, PartsResult]:
    """Смета снимка проекта и его детали: одна цепочка для сметы и КП, цена у них одна."""
    parts = _build_parts(project, home, source, brief, warnings, workshop)
    cut = _build_cut(project, home, source, brief, warnings, workshop)
    result = _estimate(project, parts, cut, workshop)
    draft = _draft_warnings(source, parts)
    result = result.model_copy(update={"warnings": [*result.warnings, *draft]}) if draft else result
    return _with_economy(result, project, home, workshop, economy), parts


def estimate_from_order(order: str, home: Path | None = None, economy: bool = False) -> EstimateResult:
    """Смета текущей версии проекта; `economy` добавляет вариант «экономия» (копия, версия не меняется)."""
    return calculation(order=order, home=home).estimate(economy)


def estimate_from_project(path: Path, home: Path | None = None, economy: bool = False) -> EstimateResult:
    """`kitchen estimate --project <файл>`: смета проекта из файла, без файлов выводов и записи в БД."""
    return calculation(project=path, home=home).estimate(economy)


def economy_parts_from_project(path: Path, home: Path | None = None) -> PartsResult:
    """Детали копии проекта из файла со всеми предложенными заменами «экономии»: то, из чего считана цена
    «экономии». Ничего не пишет."""
    return calculation(project=path, home=home).economy_parts()


def _estimate_files(
    home: Path, result: EstimateResult
) -> tuple[EstimateResult, list[tuple[OutputInfo, Callable]]]:
    outputs = [
        _output_info(home, result.source, store.ESTIMATE_MD, "estimate.md"),
        _output_info(home, result.source, store.ESTIMATE_JSON, "estimate.json"),
    ]
    result = result.model_copy(update={"outputs": outputs})
    return result, [
        (outputs[0], lambda path: path.write_text(_estimate_markdown(result), encoding="utf-8")),
        (outputs[1], lambda path: path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")),
    ]


def estimate_and_save(order: str, home: Path | None = None, economy: bool = False) -> EstimateResult:
    home = home or resolve_home()
    result, files = _estimate_files(home, estimate_from_order(order, home, economy))
    _save_outputs(home, result.source, files, f"смета посчитана из версии {result.source.version}")
    return result


def _quote(
    project: Project,
    home: Path,
    source: PartsSource,
    brief: sqlite3.Row | None,
    warnings: list[str],
    days: int,
    economy: bool,
) -> QuoteResult:
    check_days(days)  # единственная проверка срока: до расчёта, без срока КП не выдать
    workshop = load_workshop(home)
    estimate, parts = _estimate_run(project, home, source, brief, warnings, workshop, economy)
    body = build_quote(
        project, parts.modules, workshop, days=days, price=estimate.price, economy=estimate.economy
    )
    return QuoteResult(
        **body.model_dump(),
        source=estimate.source,
        warnings=estimate.warnings,
        mock=estimate.mock,
        mock_files=estimate.mock_files,
    )


def quote_from_order(order: str, days: int, home: Path | None = None, economy: bool = False) -> QuoteResult:
    """КП текущей версии проекта заказа. `days` — срок изготовления и монтажа в рабочих днях, вписывает мастер."""
    return calculation(order=order, home=home).quote(days, economy)


def quote_from_project(path: Path, days: int, home: Path | None = None, economy: bool = False) -> QuoteResult:
    """`kitchen quote --project <файл>`: КП проекта из файла, без файлов выводов и записи в БД."""
    return calculation(project=path, home=home).quote(days, economy)


def quote_and_save(
    order: str, days: int, home: Path | None = None, economy: bool = False, on: date | None = None
) -> QuoteResult:
    """КП заказа: `quote.pdf` и `quote.json` из одной модели, обе записи — в реестре выводов заказа."""
    home = home or resolve_home()
    result = quote_from_order(order, days, home, economy)
    outputs = [
        _output_info(home, result.source, store.QUOTE_PDF, "quote.pdf"),
        _output_info(home, result.source, store.QUOTE_JSON, "quote.json"),
    ]
    result = result.model_copy(update={"outputs": outputs})
    _save_outputs(
        home,
        result.source,
        [
            (
                outputs[0],
                lambda path: write_quote(
                    path,
                    result,
                    stamp=Stamp(result.source.order, result.source.version, result.mock, on or store.today()),
                ),
            ),
            (
                outputs[1],
                lambda path: path.write_text(
                    result.model_dump_json(indent=2, exclude={"warnings"}) + "\n", encoding="utf-8"
                ),
            ),
        ],
        f"КП посчитано из версии {result.source.version}, срок {working_days(days)}",
    )
    return result


def production_and_save(order: str, home: Path | None = None, on: date | None = None) -> ProductionResult:
    home = home or resolve_home()
    project, source, brief, warnings = _order_project(order, home)
    workshop = load_workshop(home)
    parts = _build_parts(project, home, source, brief, warnings, workshop)
    cut = _build_cut(project, home, source, brief, warnings, workshop)
    edging = _edging(parts)
    purchase, purchase_files = _purchase_files(home, _purchase(project, parts, cut, workshop))
    assembly = _assembly(project, parts, workshop)
    assembly_output = _output_info(home, source, store.ASSEMBLY, ASSEMBLY_PDF)
    trip = _trip(project, parts, workshop, brief)
    trip_output = _output_info(home, source, store.TRIP, TRIP_PDF)
    cut_output = _output_info(home, source, store.CUTTING, CUTTING_PDF)
    edging_output = _output_info(home, source, store.EDGING, "edging-map.pdf")
    day = on or store.today()
    files = [
        (
            cut_output,
            lambda path: write_cutting_map(
                path,
                cut.materials,
                kerf=cut.kerf,
                kerf_declared=cut.kerf_declared,
                trim=cut.trim,
                stamp=Stamp(source.order, source.version, cut.mock, day),
            ),
        ),
        (
            edging_output,
            lambda path: write_edging_map(
                path,
                edging.parts,
                edging.edges,
                purchase_digits=edging.purchase_digits,
                stamp=Stamp(source.order, source.version, edging.mock, day),
            ),
        ),
        *purchase_files,
        (assembly_output, _assembly_writer(assembly, day)),
        (trip_output, _trip_writer(trip, day)),
    ]
    _save_outputs(home, source, files, f"документы цеха посчитаны из версии {source.version}")
    return ProductionResult(
        cutting=cut.model_copy(update={"output": cut_output}),
        edging=edging.model_copy(update={"output": edging_output}),
        purchase=purchase,
        assembly=assembly.model_copy(update={"output": assembly_output}),
        trip=trip.model_copy(update={"output": trip_output}),
        mock=parts.mock,
        mock_files=parts.mock_files,
        mock_notice=MOCK_BANNER if parts.mock else None,
    )


def list_outputs(order: str, home: Path | None = None) -> list[OutputInfo]:
    """Выводы заказа из БД с версиями-источниками, по времени записи."""
    home = home or resolve_home()
    with _open_order(order, home) as o:
        rows = o.conn.execute("SELECT * FROM outputs WHERE order_id = ? ORDER BY id", (o.pk,)).fetchall()
    return [
        OutputInfo(
            kind=r["kind"],
            path=r["path"],
            file=str(home / r["path"]),
            source_version=r["source_version"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# --- проверки проекта ---------------------------------------------------------


def check_project(path: Path, home: Path | None = None) -> CheckResult:
    """`проверки проекта` из файла без БД: все находки сразу."""
    return calculation(project=path, home=home).check()


def check_order(order: str, home: Path | None = None) -> CheckResult:
    """`kitchen check <заказ>`: находки проверок текущей версии проекта с последним брифом заказа."""
    return calculation(order=order, home=home).check()


# --- расстановка в комнате ----------------------------------------------------


def part_in_room(place: ModulePlace, part: Part) -> SceneObject:
    """Деталь модуля (`parts`) в комнате той же расстановкой, что и модуль: контракт для видов и HTML."""
    return placement.part_in_room(place, part)


def placement_from_project(path: Path, home: Path | None = None) -> PlacementResult:
    """Расстановка проекта из файла (черновик до импорта)."""
    return calculation(project=path, home=home).placement()


def placement_from_order(order: str, home: Path | None = None) -> PlacementResult:
    """Расстановка текущей версии проекта заказа."""
    return calculation(order=order, home=home).placement()


# --- виды кухни в PDF ---------------------------------------------------------


VIEWS_PDF = "kitchen-views.pdf"
VIEWS_HTML = "kitchen-3d.html"


def views_from_project(path: Path, home: Path | None = None) -> ViewsResult:
    """`kitchen 3d --project <файл>`: виды проекта из файла, файлов не пишет."""
    return calculation(project=path, home=home).views()


def views_from_order(order: str, home: Path | None = None) -> ViewsResult:
    """Виды текущей версии проекта заказа, без записи файлов."""
    return calculation(order=order, home=home).views()


def views_layout(result: ViewsResult) -> list[ViewPage]:
    """Раскладка страниц видов (фигуры, линии, подписи, цепочки, высоты) — ровно то, что рисует PDF."""
    if result._pages is None:  # результат собран не `_views` (например, разобран из JSON)
        return views_page_layout(result.scene, len(result.errors))
    return result._pages


def views_and_save(order: str, home: Path | None = None, on: date | None = None) -> ViewsResult:
    """`kitchen 3d <заказ>`: виды текущей версии — PDF и HTML 3D, оба регистрируются как выводы одной версии."""
    home = home or resolve_home()
    result = views_from_order(order, home)
    pages = views_layout(result)
    date_ = on or store.today()
    pdf = _output_info(home, result.source, store.VIEWS, VIEWS_PDF)
    html = _output_info(home, result.source, store.VIEWS_HTML, VIEWS_HTML)
    _save_outputs(
        home,
        result.source,
        [
            (
                pdf,
                lambda path: write_views(
                    path,
                    pages,
                    stamp=Stamp(result.source.order, result.source.version, result.mock, date_),
                ),
            ),
            (
                html,
                lambda path: write_html(
                    path,
                    result.scene3d,
                    stamp=Stamp(result.source.order, result.source.version, result.mock, date_),
                    errors=len(result.errors),
                ),
            ),
        ],
    )
    return result.model_copy(update={"output": pdf, "output_html": html})


# --- быстрая вилка ------------------------------------------------------------


class QuickEstimate(Result):
    meters: int | float
    per_meter_low: int | float
    per_meter_high: int | float
    total_low: int
    total_high: int
    rounding: str  # как округлено, словами
    total_low_text: str  # «306 000»: готовая строка для скилла
    total_high_text: str


def quick_estimate(meters: float, home: Path | None = None) -> QuickEstimate:
    """Вилка цены по погонным метрам: быстрая_оценка_за_пог_м × метры, концы округлены по `pricing.yaml`."""
    if not (meters > 0 and meters != float("inf")):  # NaN и бесконечность тоже сюда
        raise InputError("Длина кухни в погонных метрах должна быть числом больше нуля.")
    workshop = load_workshop(home)
    pricing = workshop.pricing
    low, high = pricing.quick_estimate_per_m
    rule = pricing.price_rounding
    exact = Decimal(str(meters))  # через строку: без шума плавающей запятой
    total_low = rule.apply(exact * Decimal(str(low)))
    total_high = rule.apply(exact * Decimal(str(high)))
    return QuickEstimate(
        meters=num(meters),
        per_meter_low=num(low),
        per_meter_high=num(high),
        total_low=total_low,
        total_high=total_high,
        rounding=rule.describe(),
        total_low_text=group_digits(total_low),
        total_high_text=group_digits(total_high),
        mock=pricing.mock,  # вилка зависит только от pricing.yaml (решение мастера, тикет 58)
        mock_files=["pricing.yaml"] if pricing.mock else [],
    )
