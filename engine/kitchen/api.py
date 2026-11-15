"""Публичный API движка. Вся логика здесь; CLI только печатает результат."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from kitchen import brief as brief_check
from kitchen import store
from kitchen.assembly import AssemblyModule, build_assembly
from kitchen.assembly_pdf import AssemblyPageLayout, layout, write_assembly
from kitchen.checks import Finding, duplicate_labels
from kitchen.checks import check_project as run_checks
from kitchen.common import CommonItem, CountertopSize, compute_common, size_countertop
from kitchen.cutting import MaterialCut, cut_parts, kerf_warnings
from kitchen.cutting_pdf import write_cutting_map
from kitchen.drilling import DrillingCheck, reconcile
from kitchen.drilling_pdf import PageLayout, map_layout, write_drilling_map
from kitchen.economy import EconomyLine, EconomyVariant, apply_rule
from kitchen.edging_pdf import write_edging_map
from kitchen.errors import InputError, KitchenError, OrderError
from kitchen.estimate import EstimateLabor, LaborLine, build_labor, build_pricing
from kitchen.models import Appliances, Economy, Hardware, Labor, Materials, Workshop
from kitchen.parts import PURPOSE_RU, EdgeTotals, ModuleParts, Part, compute_parts
from kitchen.project import Project, parse_project
from kitchen.purchase import PurchaseShop, build_purchase
from kitchen.quote import DAYS_ERROR, QuoteBody, build_quote, check_days, money, parse_days, percent, plural
from kitchen.quote_pdf import write_quote
from kitchen.result import MOCK_BANNER, Result
from kitchen.workshop import FILES, Loaded, load, load_workshop, resolve_home

__all__ = [
    "DAYS_ERROR",
    "HOLE_PURPOSES",
    "OUTPUT_RECOMPUTE",
    "PRODUCTION_KINDS",
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
    "PageLayout",
    "PartsResult",
    "PartsSource",
    "ProductionResult",
    "PurchaseResult",
    "QuickEstimate",
    "QuoteResult",
    "StaleOutput",
    "StatusResult",
    "ValidationContext",
    "ValidationResult",
    "WorkshopCheck",
    "assembly_and_save",
    "assembly_from_order",
    "assembly_from_project",
    "assembly_layout",
    "brief_save",
    "brief_show",
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
    "import_project",
    "list_outputs",
    "load_workshop",
    "log_add",
    "money",
    "order_new",
    "order_show",
    "parse_days",
    "parts_from_order",
    "parts_from_project",
    "percent",
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
    "validate_wrapper",
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


class LogEntry(BaseModel):
    created_at: str
    kind: str
    text: str
    project_version: int | None


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


class StatusResult(Result):
    order: str
    stage: str
    project_version: int | None
    path: str
    text: str
    stale: list[StaleOutput] = Field(default_factory=list)


OUTPUT_RECOMPUTE = store.OUTPUT_RECOMPUTE


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


def _current_version(conn: sqlite3.Connection, pk: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM project_versions WHERE order_id = ? ORDER BY version DESC LIMIT 1", (pk,)
    ).fetchone()


@dataclass
class OpenOrder:
    """Найденный заказ с открытой БД. `ref` — строка `номер-фамилия`, `pk` — ключ в БД."""

    conn: sqlite3.Connection
    row: sqlite3.Row
    home: Path
    changed: bool = False  # поставить True после записи: при выходе обновится updated_at

    @property
    def ref(self) -> str:
        return store.order_ref_of(self.row)

    @property
    def pk(self) -> int:
        return self.row["id"]


@contextmanager
def _open_order(query: str, home: Path | None = None) -> Iterator[OpenOrder]:
    home = home or resolve_home()
    with store.connect(home) as conn:
        o = OpenOrder(conn, store.find_order(conn, query), home)
        yield o
        if o.changed:
            conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (store.now(), o.pk))
        conn.commit()


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
class ValidationContext:
    """Что нужно проверке проекта: заказ и мастерская (`None`, если её данные не загрузились)."""

    order_ref: str  # номер-фамилия
    order_number: str  # ГГГГ-NNN
    workshop: Workshop | None = None
    workshop_problems: list[str] = field(default_factory=list)  # почему мастерская не загрузилась
    brief: dict | None = None  # последний бриф заказа (техника сверяется с ним)
    brief_id: int | None = None


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)  # отклоняют импорт
    warnings: list[str] = field(default_factory=list)  # пишутся в журнал как note
    brief_id: int | None = None  # какой бриф использован при проверке


Validator = Callable[[Any, ValidationContext], ValidationResult]


def _latest_brief(conn: sqlite3.Connection, pk: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM briefs WHERE order_id = ? ORDER BY id DESC LIMIT 1", (pk,)).fetchone()


def _same_order(value: Any, ctx: ValidationContext) -> bool:
    """Номер заказа или `номер-фамилия`; фамилия сравнивается после транслитерации."""
    if not isinstance(value, str):
        return False
    if value == ctx.order_number:
        return True
    try:
        return store.parse_order_id(value) == (
            ctx.order_number,
            ctx.order_ref.removeprefix(ctx.order_number + "-"),
        )
    except OrderError:
        return False


def validate_wrapper(data: Any, ctx: ValidationContext) -> ValidationResult:
    """Проверить заказ, схему и конструкцию проекта (`kitchen.checks`): ошибки отклоняют импорт."""
    if not isinstance(data, dict):
        return ValidationResult(["Проект должен быть JSON-объектом ({...}), а не списком или значением."])
    if "order" not in data:
        return ValidationResult(["В проекте нет поля «order» с номером заказа."])
    value = data["order"]
    if not _same_order(value, ctx):
        return ValidationResult([f"Поле «order» = «{value}» не совпадает с заказом {ctx.order_ref}."])
    try:
        project = parse_project({k: v for k, v in data.items() if k != "version"})
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


def _canonical(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} в JSON недопустимо")


def _insert_log(conn: sqlite3.Connection, pk: int, kind: str, text: str, version: int | None) -> LogEntry:
    ts = store.now()
    conn.execute(
        "INSERT INTO log (order_id, created_at, kind, text, project_version) VALUES (?, ?, ?, ?, ?)",
        (pk, ts, kind, text, version),
    )
    return LogEntry(created_at=ts, kind=kind, text=text, project_version=version)


def _read_json(path: Path, what: str) -> Any:
    if not path.is_file():
        raise OrderError(f"{what} {path} не найден.")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise OrderError(f"{path.name}: не JSON (строка {e.lineno}, столбец {e.colno}).") from e
    except UnicodeDecodeError as e:
        raise OrderError(f"{path.name}: файл не в кодировке UTF-8; сохраните его как UTF-8.") from e
    except ValueError as e:  # NaN/Infinity
        raise OrderError(f"{path.name}: не JSON ({e}).") from e


_WRITE_REASONS: dict[type[OSError], str] = {
    IsADirectoryError: "по этому пути папка, а не файл",
    NotADirectoryError: "часть пути — не папка",
    PermissionError: "нет доступа",
    FileNotFoundError: "путь недоступен",
    FileExistsError: "по этому пути уже есть файл вместо папки",
    OSError: "ошибка записи",
}


def _write_reason(e: OSError) -> str:
    return next((text for kind, text in _WRITE_REASONS.items() if isinstance(e, kind)), "ошибка записи")


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
                projects.append(_modules(data))
            except (TypeError, KeyError, ValueError, AttributeError) as e:
                raise OrderError(
                    f"Версия {version} не соответствует текущей схеме проекта: "
                    "невозможно определить модули, стены или ряды."
                ) from e
        before, after = projects
        changes = []
        for label in sorted(before.keys() | after.keys()):
            if label not in before:
                changes.append(f"{label}: добавлен модуль")
            elif label not in after:
                changes.append(f"{label}: удалён модуль")
            else:
                old, old_wall, old_row, old_pos = before[label]
                new, new_wall, new_row, new_pos = after[label]
                changes.extend(f"{label}: {line}" for line in _changes(old, new))
                if (old_wall, old_row, old_pos) != (new_wall, new_row, new_pos):
                    rows = {"lower": "нижний", "upper": "верхний"}
                    changes.append(
                        f"{label}: положение {old_wall}, {rows[old_row]}, {old_pos + 1} → "
                        f"{new_wall}, {rows[new_row]}, {new_pos + 1}"
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

        stage = _stage(cur is not None, exists("outputs"), exists("briefs"))
        who = ", ".join(x for x in (o.row["client"], o.row["phone"], o.row["address"]) if x)
        lines = [f"# Заказ {o.ref}", "", STATUS_HEADER, ""]
        if who:
            lines += [who, ""]
        lines += ["## Этап", "", stage, "", "## Версия проекта", ""]
        lines += [f"Версия {cur['version']} от {day(cur['created_at'])}."] if cur else ["Проекта ещё нет."]
        lines.append("")
        stale = _stale_outputs(o.conn, o.pk, o.ref, cur["version"] if cur else None)
        lines += _section("Устарело", [_stale_line(s) for s in stale], "Пока ничего.")
        lines += _section("Решения клиента", dated(entries("decision")[::-1]), "Пока нет.")
        lines += _section("Сделано", dated(entries("done")[::-1]), "Пока ничего.")
        lines += _section("Дальше", dated(entries("next", 5)), "Пока не записано.")
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


def _resolve_for_brief(conn: sqlite3.Connection, query: str, client: str) -> tuple[sqlite3.Row, bool]:
    """Заказ для брифа (внутри `BEGIN IMMEDIATE`).

    Номер — существующий заказ. Номер-фамилия — существующий или новый с этим номером. Одна фамилия —
    только новый заказ: если заказ с точно такой фамилией уже есть, это ошибка со списком.
    """
    parsed = store.parse_query(query)
    if parsed.kind == "number":
        return store.find_order(conn, query), False
    if parsed.kind == "full":
        if conn.execute("SELECT 1 FROM orders WHERE number = ?", (parsed.number,)).fetchone():
            return store.find_order(conn, query), False
        return _insert_order(conn, parsed.number, parsed.slug, client, "", ""), True
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
    return _insert_order(conn, number, parsed.slug, client, "", ""), True


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
        row, created = _resolve_for_brief(conn, order, brief_check.client_name(brief))
        ts = store.now()
        conn.execute(
            "INSERT INTO briefs (order_id, data, check_result, created_at) VALUES (?, ?, ?, ?)",
            (row["id"], _canonical(brief), json.dumps(check, ensure_ascii=False), ts),
        )
        current = _current_version(conn, row["id"])
        _insert_log(conn, row["id"], "done", "бриф сохранён", current["version"] if current else None)
        conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (ts, row["id"]))
        conn.commit()
        return BriefSaveResult(
            order=store.order_ref_of(row),
            created=created,
            saved_at=ts,
            missing_required=check["summary"]["missing_required"],
            conflicts=check["summary"]["conflicts"],
            check=check,
            **mock,
        )


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


class PartsSource(BaseModel):
    """Откуда посчитан вывод: версия проекта заказа или файл; бриф, с которым прошли проверки."""

    kind: Literal["order", "file"]
    order: str  # номер-фамилия (из БД) или поле order файла
    version: int | None  # версия проекта; у файла — только если она записана в самом файле
    content_hash: str  # sha256 нормализованного проекта (`_project_hash`): у файла и версии из БД одинаков
    path: str | None = None  # путь файла проекта
    brief_id: int | None = None  # последний бриф заказа: с ним посчитаны проверки
    brief_saved_at: str | None = None
    version_brief_id: int | None = None  # бриф, записанный у версии при импорте


class PartsResult(Result):
    """Детали по модулям, фурнитура, кромка, площадь фасадов, находки проверок, версия-источник."""

    order: str  # поле order проекта
    source: PartsSource
    modules: list[ModuleParts]
    hardware: dict[str, int]
    edges: dict[str, EdgeTotals]
    facade_area: float  # м², округлено до 4 знаков
    facade_area_by_color: dict[str, float]  # м² по цветам фасадов проекта
    common: list[CommonItem]  # общие позиции кухни: цоколь, шина, навесы, LED, столешница
    findings: list[Finding]
    warnings: list[str]


def _project_hash(project: Project) -> str:
    """Хеш нормализованного проекта: написание `order`, `600` и `600.0`, порядок ключей хеш не меняют."""
    normalized = project.model_dump(mode="json", exclude={"order", "version"})
    for wall in normalized["walls"]:
        for row in wall["rows"]:
            for module in row["modules"]:
                # `contents: {}` и отсутствующий `contents` — один проект
                if module.get("contents") and not any(module["contents"].values()):
                    module["contents"] = None
    return hashlib.sha256(_canonical(normalized).encode()).hexdigest()


def _data_hash(data: dict) -> str:
    """Хеш проекта из JSON. Проект, который схема не читает (старая версия, чужая проверка), — хеш сырого JSON."""
    try:
        return _project_hash(parse_project(data))
    except OrderError:
        return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def _require_unique_labels(project: Project) -> None:
    """Повтор метки — ошибка с тем же текстом, что у находки проверок и при импорте."""
    repeated = duplicate_labels(project)
    if repeated:
        raise OrderError([f.message for f in repeated])


def _build_parts(
    project: Project,
    home: Path,
    source: PartsSource,
    brief: sqlite3.Row | None,
    extra_warnings: list[str],
    workshop: Workshop | None = None,
) -> PartsResult:
    workshop = workshop or load_workshop(home)
    _require_unique_labels(project)
    computed = compute_parts(project, workshop)
    source = source.model_copy(
        update={
            "brief_id": brief["id"] if brief else None,
            "brief_saved_at": brief["created_at"] if brief else None,
        }
    )
    return PartsResult(
        order=project.order,
        source=source,
        modules=computed.modules,
        hardware=computed.hardware,
        edges=computed.edges,
        facade_area=computed.facade_area,
        facade_area_by_color=computed.facade_by_color,
        common=compute_common(project, workshop, computed.hardware),
        findings=run_checks(project, workshop, json.loads(brief["data"]) if brief else None),
        warnings=[*computed.warnings, *extra_warnings],
        mock=workshop.is_mock,
        mock_files=workshop.mock_files,
    )


def _brief_of_project(home: Path, order: str) -> sqlite3.Row | None:
    """Последний бриф заказа из БД; заказа или БД ещё нет — `None` (проект проверяют как черновик)."""
    if not store.db_path(home).exists():
        return None
    with store.connect(home) as conn:
        try:
            return _latest_brief(conn, store.find_order(conn, order)["id"])
        except OrderError:
            return None


def parts_from_project(path: Path, home: Path | None = None) -> PartsResult:
    """`kitchen parts --project <файл>`: детали проекта из файла (черновик до импорта)."""
    home = home or resolve_home()
    project, source = _project_file(path)
    return _build_parts(project, home, source, _brief_of_project(home, project.order), [])


def _order_project(order: str, home: Path) -> tuple[Project, PartsSource, sqlite3.Row | None, list[str]]:
    """Текущая версия проекта заказа: проект, источник, последний бриф, предупреждения об устаревшем брифе."""
    with _open_order(order, home) as o:
        current = _current_version(o.conn, o.pk)
        if not current:
            raise OrderError(f"У заказа {o.ref} проекта ещё нет. Сначала kitchen import.")
        project = parse_project({**json.loads(current["data"]), "version": current["version"]})
        latest = _latest_brief(o.conn, o.pk)
    warnings = []
    if latest and latest["id"] != current["brief_id"]:
        was = f"№{current['brief_id']}" if current["brief_id"] else "никакой"
        warnings.append(
            f"Бриф №{latest['id']} новее брифа, с которым проверена версия {current['version']} "
            f"({was}): расчёт и проверки идут с последним брифом. "
            "Импортируйте проект заново (kitchen import), чтобы записать его у версии."
        )
    source = PartsSource(
        kind="order",
        order=o.ref,
        version=current["version"],
        content_hash=_project_hash(project),  # нормализованный: версии со старым хешем в БД сходятся с файлом
        version_brief_id=current["brief_id"],
    )
    return project, source, latest, warnings


def parts_from_order(order: str, home: Path | None = None) -> PartsResult:
    """`kitchen parts <заказ>`: детали текущей версии проекта, проверки с последним брифом заказа."""
    home = home or resolve_home()
    project, source, latest, warnings = _order_project(order, home)
    return _build_parts(project, home, source, latest, warnings)


# --- раскрой ------------------------------------------------------------------


OutputKind = store.OutputKind


class OutputInfo(BaseModel):
    """Вывод заказа: файл в `orders/<заказ>/out/` и версия проекта, из которой он посчитан."""

    kind: OutputKind
    path: str  # относительно KITCHEN_HOME, как в БД
    file: str  # полный путь
    source_version: int
    # Время записи в БД. В результате команды не заполняется: вывод детерминирован.
    created_at: str | None = None


class CutResult(Result):
    """Раскрой листовых деталей по материалам, с порядком резов; версия-источник."""

    order: str
    source: PartsSource
    kerf: int  # пропил, мм, как его учёл раскрой (дробный из equipment.yaml — вверх до целого)
    kerf_declared: float  # пропил, как он записан в equipment.yaml
    trim: int  # обрезка по периметру, мм
    max_cut_length: int  # макс. длина реза станка, мм
    materials: list[MaterialCut]
    countertop: CountertopSize | None  # общая длина и листы столешницы; нет столешницы в проекте — `None`
    warnings: list[str]
    output: OutputInfo | None = None  # PDF карты раскроя; только после `cut_and_save`


def _build_cut(
    project: Project,
    home: Path,
    source: PartsSource,
    brief: sqlite3.Row | None,
    extra: list[str],
    workshop: Workshop | None = None,
) -> CutResult:
    workshop = workshop or load_workshop(home)
    _require_unique_labels(project)
    computed = compute_parts(project, workshop)
    saw = workshop.equipment.saw
    return CutResult(
        order=project.order,
        source=source.model_copy(
            update={
                "brief_id": brief["id"] if brief else None,
                "brief_saved_at": brief["created_at"] if brief else None,
            }
        ),
        kerf=saw.kerf_mm,
        kerf_declared=saw.kerf,
        trim=workshop.equipment.sheet.perimeter_trim,
        max_cut_length=saw.max_cut_length,
        materials=cut_parts(computed.modules, workshop),
        countertop=size_countertop(project, workshop),
        warnings=[*computed.warnings, *extra, *kerf_warnings(saw)],
        mock=workshop.is_mock,
        mock_files=workshop.mock_files,
    )


def cut_from_project(path: Path, home: Path | None = None) -> CutResult:
    """`kitchen cut --project <файл>`: раскрой деталей проекта из файла (черновик до импорта)."""
    home = home or resolve_home()
    project, source = _project_file(path)
    return _build_cut(project, home, source, _brief_of_project(home, project.order), [])


def cut_from_order(order: str, home: Path | None = None) -> CutResult:
    """`kitchen cut <заказ>`: раскрой деталей текущей версии проекта."""
    home = home or resolve_home()
    project, source, latest, warnings = _order_project(order, home)
    return _build_cut(project, home, source, latest, warnings)


CUTTING_PDF = "cutting-map.pdf"


def _output_info(home: Path, source: PartsSource, kind: OutputKind, filename: str) -> OutputInfo:
    if source.version is None:
        raise OrderError(f"У заказа {source.order} нет версии проекта.")
    rel = f"orders/{source.order}/out/{filename}"
    return OutputInfo(kind=kind, path=rel, file=str(home / rel), source_version=source.version)


def _save_outputs(
    home: Path, source: PartsSource, files: list[tuple[OutputInfo, Callable[[Path], None]]]
) -> None:
    """Регистрирует и устанавливает комплект файлов под одной блокировкой; сбой восстанавливает старый комплект."""
    if source.version is None:
        raise OrderError(f"У заказа {source.order} нет версии проекта.")
    directory = home / "orders" / source.order / "out"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".output-", dir=directory))
    except OSError as e:
        raise OrderError(f"Не удалось подготовить папку выводов {directory}: {_write_reason(e)}.") from e
    installed: list[tuple[Path, Path | None]] = []
    preserve_staging = False
    try:
        for info, write in files:
            write(staging / Path(info.file).name)
        with store.connect(home) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = store.find_order(conn, source.order)
                current = _current_version(conn, row["id"])
                if current is None or current["version"] != source.version:
                    raise OrderError("Изменилась версия проекта во время расчёта. Повторите команду.")
                for index, (info, _) in enumerate(files):
                    path = Path(info.file)
                    backup_name = "previous.pdf" if index == 0 else f"backup-{index}"
                    backup = staging / backup_name if path.exists() else None
                    if backup is not None:
                        shutil.copy2(path, backup)
                    store.register_output(conn, row["id"], info.kind, info.path, source.version)
                    (staging / path.name).replace(path)
                    installed.append((path, backup))
                conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (store.now(), row["id"]))
                conn.commit()
            except BaseException as error:
                failures = []
                try:
                    for path, backup in reversed(installed):
                        try:
                            if backup is not None:
                                backup.replace(path)
                            else:
                                path.unlink(missing_ok=True)
                        except BaseException as restore_error:  # noqa: BLE001 — сохранить резерв и продолжить откат при прерывании
                            failures.append(f"{path}: {restore_error}")
                finally:
                    conn.rollback()
                if failures:
                    preserve_staging = True
                    raise OrderError(
                        f"Не удалось восстановить файлы после ошибки: {'; '.join(failures)}. "
                        f"Резервные копии сохранены в {staging}. Первоначальная ошибка: {error}"
                    ) from error
                raise
    finally:
        if not preserve_staging:
            shutil.rmtree(staging, ignore_errors=True)


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
            order=result.source.order,
            version=result.source.version,
            mock=result.mock,
            on=on or store.today(),
        ),
    )
    return result.model_copy(update={"output": output})


class EdgingResult(Result):
    """Детали с кромкой и итоговый метраж из расчёта деталей."""

    source: PartsSource
    parts: list[Part]
    edges: dict[str, EdgeTotals]
    warnings: list[str]
    output: OutputInfo | None = None


def _edging(parts: PartsResult) -> EdgingResult:
    return EdgingResult(
        source=parts.source,
        parts=[p for m in parts.modules for p in m.parts if any(p.edges.values())],
        edges=parts.edges,
        warnings=parts.warnings,
        mock=parts.mock,
        mock_files=parts.mock_files,
    )


def edges_from_order(order: str, home: Path | None = None) -> EdgingResult:
    return _edging(parts_from_order(order, home))


def edges_from_project(path: Path, home: Path | None = None) -> EdgingResult:
    return _edging(parts_from_project(path, home))


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
            order=result.source.order,
            version=result.source.version,
            mock=result.mock,
            on=on or store.today(),
        ),
    )
    return result.model_copy(update={"output": output})


# --- присадка -----------------------------------------------------------------


HOLE_PURPOSES = PURPOSE_RU  # назначение отверстия -> по-русски, для текста CLI


class DrillingResult(Result):
    """Карта присадки: детали с отверстиями (по модулям проекта) и сверка с фурнитурой этапа 1."""

    source: PartsSource
    parts: list[Part]
    checks: list[DrillingCheck]
    warnings: list[str]
    output: OutputInfo | None = None


def _drilling(parts: PartsResult, workshop: Workshop) -> DrillingResult:
    drilled = [p for m in parts.modules for p in m.parts if p.holes]
    return DrillingResult(
        source=parts.source,
        parts=drilled,
        checks=reconcile(drilled, parts.hardware, workshop),
        warnings=parts.warnings,
        mock=parts.mock,
        mock_files=parts.mock_files,
    )


def drilling_from_order(order: str, home: Path | None = None) -> DrillingResult:
    """Присадка текущей версии проекта заказа, без записи файлов."""
    home = home or resolve_home()
    project, source, latest, warnings = _order_project(order, home)
    workshop = load_workshop(home)
    return _drilling(_build_parts(project, home, source, latest, warnings, workshop), workshop)


def drilling_from_project(path: Path, home: Path | None = None) -> DrillingResult:
    """`kitchen drilling --project <файл>`: присадка проекта из файла, файлов не пишет."""
    home = home or resolve_home()
    return _drilling(parts_from_project(path, home), load_workshop(home))


DRILLING_PDF = "drilling-map.pdf"


def drilling_map_layout(result: DrillingResult) -> list[PageLayout]:
    """Раскладка страниц карты присадки (строки, контуры, базовые кромки, цепочки) — ровно то, что рисует PDF."""
    return map_layout(result.parts, result.checks)


def _drilling_writer(result: DrillingResult, on: date | None) -> Callable[[Path], None]:
    return lambda path: write_drilling_map(
        path,
        result.parts,
        result.checks,
        order=result.source.order,
        version=result.source.version,
        mock=result.mock,
        on=on or store.today(),
    )


def drilling_and_save(order: str, home: Path | None = None, on: date | None = None) -> DrillingResult:
    """`kitchen drilling <заказ>`: присадка текущей версии, PDF карты и регистрация вывода (как у раскроя)."""
    home = home or resolve_home()
    result = drilling_from_order(order, home)
    output = _save_output(home, result.source, store.DRILLING, DRILLING_PDF, _drilling_writer(result, on))
    return result.model_copy(update={"output": output})


# --- сборочный лист -----------------------------------------------------------


class AssemblyResult(Result):
    """Сборочный лист: по модулям детали, фурнитура «цех» и шаги сборки; версия-источник."""

    source: PartsSource
    output: OutputInfo | None = None
    warnings: list[str]
    modules: list[AssemblyModule]


def _assembly(project: Project, parts: PartsResult, workshop: Workshop) -> AssemblyResult:
    return AssemblyResult(
        source=parts.source,
        warnings=parts.warnings,
        modules=build_assembly(project, parts.modules, workshop),
        mock=parts.mock,
        mock_files=parts.mock_files,
    )


def assembly_from_order(order: str, home: Path | None = None) -> AssemblyResult:
    """Сборочный лист текущей версии проекта заказа, без записи файлов."""
    home = home or resolve_home()
    project, source, latest, warnings = _order_project(order, home)
    workshop = load_workshop(home)
    return _assembly(project, _build_parts(project, home, source, latest, warnings, workshop), workshop)


def assembly_from_project(path: Path, home: Path | None = None) -> AssemblyResult:
    """`kitchen assembly --project <файл>`: сборочный лист проекта из файла, файлов не пишет."""
    home = home or resolve_home()
    project, source = _project_file(path)
    workshop = load_workshop(home)
    parts = _build_parts(project, home, source, _brief_of_project(home, project.order), [], workshop)
    return _assembly(project, parts, workshop)


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
        order=result.source.order,
        version=result.source.version,
        mock=result.mock,
        on=on or store.today(),
    )


class PurchaseResult(Result):
    """Позиции закупки с количеством, ценами и суммами по магазинам."""

    source: PartsSource
    shops: list[PurchaseShop]
    total: float
    warnings: list[str]
    mock_notice: str | None = None
    outputs: list[OutputInfo] = Field(default_factory=list)


def _purchase(project: Project, parts: PartsResult, cut: CutResult, workshop: Workshop) -> PurchaseResult:
    shops, total = build_purchase(
        project,
        workshop,
        parts.modules,
        parts.hardware,
        parts.edges,
        parts.common,
        cut.materials,
        cut.countertop,
    )
    return PurchaseResult(
        source=parts.source,
        shops=shops,
        total=total,
        warnings=[*parts.warnings, *kerf_warnings(workshop.equipment.saw)],
        mock=parts.mock,
        mock_files=parts.mock_files,
        mock_notice=MOCK_BANNER if parts.mock else None,
    )


def purchase_from_order(order: str, home: Path | None = None) -> PurchaseResult:
    home = home or resolve_home()
    project, source, brief, warnings = _order_project(order, home)
    workshop = load_workshop(home)
    return _purchase(
        project,
        _build_parts(project, home, source, brief, warnings, workshop),
        _build_cut(project, home, source, brief, warnings, workshop),
        workshop,
    )


def _purchase_markdown(result: PurchaseResult) -> str:
    """Телефонная версия закупки: все суммы и количества из единой модели."""
    lines = []
    if result.mock_notice:
        lines += [f"**{result.mock_notice}**", ""]
    lines += ["# Закупка", "", f"Заказ {result.source.order} · версия проекта {result.source.version}", ""]
    if result.warnings:
        lines += ["## Предупреждения", "", *(f"- {warning}" for warning in result.warnings), ""]
    for shop in result.shops:
        lines += [f"## {shop.name}", ""]
        for item in shop.items:
            details = []
            if item.needed is not None:
                details.append(f"нужно {item.needed} {item.needed_unit}")
            if item.pack_size is not None:
                details.append(f"в упаковке {item.pack_size} {item.needed_unit}")
            if item.color:
                details.append(f"цвет {item.color}")
            if item.lead_days is not None:
                details.append(f"срок {item.lead_days} раб. дней")
            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(
                f"- {item.name} [{item.id}]{suffix}: {item.quantity} {item.unit} × "
                f"{item.unit_price} ₽ = {item.amount:.2f} ₽"
            )
        lines += ["", f"**Итого по магазину: {shop.total:.2f} ₽**", ""]
    lines += [f"**Итого: {result.total:.2f} ₽**", ""]
    return "\n".join(lines)


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


class EstimateResult(Result):
    """Смета: себестоимость материалов из закупки, труд по операциям, цена с округлением."""

    source: PartsSource
    materials_cost: float
    markup: float
    consumables: float
    delivery: float
    labor: EstimateLabor
    price_before_rounding: float
    price: int
    margin: float
    rounding: str
    warnings: list[str]
    mock_notice: str | None = None
    outputs: list[OutputInfo] = Field(default_factory=list)
    # вариант «экономия» (`--economy`); без него ключа в выводе нет
    economy: EconomyVariant | None = Field(default=None, exclude_if=lambda value: value is None)


NO_SINK_WARNING = "в проекте нет мойки — врезка мойки не посчитана"


def _estimate(project: Project, parts: PartsResult, cut: CutResult, workshop: Workshop) -> EstimateResult:
    purchase = _purchase(project, parts, cut, workshop)
    labor = build_labor(project, workshop, parts.modules, parts.hardware, parts.edges, cut.materials)
    pricing = build_pricing(workshop, purchase.total, labor.total)
    no_sink = [] if any(a.type == "sink" for a in project.appliances) else [NO_SINK_WARNING]
    return EstimateResult(
        source=parts.source,
        materials_cost=purchase.total,
        labor=labor,
        warnings=[*parts.warnings, *no_sink, *kerf_warnings(workshop.equipment.saw)],
        mock=parts.mock,
        mock_files=parts.mock_files,
        mock_notice=MOCK_BANNER if parts.mock else None,
        **pricing.model_dump(),
    )


def _priced(project: Project, home: Path, source: PartsSource, workshop: Workshop) -> EstimateResult:
    """Смета копии проекта тем же путём, что и смета проекта: детали -> раскрой -> закупка -> смета."""
    return _estimate(
        project,
        _build_parts(project, home, source, None, [], workshop),
        _build_cut(project, home, source, None, [], workshop),
        workshop,
    )


def _economy(
    project: Project,
    home: Path,
    source: PartsSource,
    workshop: Workshop,
    price_as_is: int,
    price_together: bool = True,
) -> tuple[EconomyVariant, Project]:
    """Вариант «экономия»: цена с каждой заменой отдельно (разница на правило) и со всеми предложенными сразу.
    Замена с разницей не больше нуля не предлагается. Копия проекта нигде не сохраняется."""
    offered: list[EconomyLine] = []
    not_offered: list[EconomyLine] = []
    problems: list[str] = []
    for rule in workshop.economy.rules:
        applied = apply_rule(project, rule, workshop)
        problems += applied.problems
        if not applied.modules:
            continue
        price = _priced(applied.project, home, source, workshop).price
        line = EconomyLine(
            id=rule.id,
            name=rule.name,
            client_text=rule.client_text,
            modules=applied.modules,
            price=price,
            saving=price_as_is - price,
        )
        (offered if line.saving > 0 else not_offered).append(line)
    chosen = {line.id for line in offered}
    together = project
    for rule in workshop.economy.rules:
        if rule.id in chosen:
            applied = apply_rule(together, rule, workshop)
            together = applied.project
            problems += [p for p in applied.problems if p not in problems]
    if not offered:
        price = price_as_is
    elif len(offered) == 1:
        price = offered[0].price  # одна замена: «со всеми» и «с одной» — один и тот же проект
    elif price_together:
        price = _priced(together, home, source, workshop).price
    else:
        price = 0  # цена нужна только вариантам с `price_together`; детали копии от неё не зависят
    variant = EconomyVariant(
        price_as_is=price_as_is,
        price=price,
        saving=price_as_is - price,
        replacements=offered,
        not_offered=not_offered,
        problems=problems,
    )
    return variant, together


def _with_economy(
    result: EstimateResult, project: Project, home: Path, workshop: Workshop, economy: bool
) -> EstimateResult:
    if not economy:
        return result
    variant, _ = _economy(project, home, result.source, workshop, result.price)
    return result.model_copy(update={"economy": variant})


def _draft_warnings(source: PartsSource, parts: PartsResult) -> list[str]:
    """Проект из файла (черновик до импорта) с ошибками проверок: расчёт идёт, но импорт такой проект отклонит.
    Версия заказа таких ошибок не несёт."""
    if source.kind != "file":
        return []
    return [
        f"Проект с ошибкой проверки (импорт его отклонит): {f.message}"
        for f in parts.findings
        if f.level == "error"
    ]


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
    home = home or resolve_home()
    project, source, brief, warnings = _order_project(order, home)
    return _estimate_run(project, home, source, brief, warnings, load_workshop(home), economy)[0]


def _project_file(path: Path) -> tuple[Project, PartsSource]:
    data = _read_json(Path(path), "Файл проекта")
    project = parse_project(data)
    source = PartsSource(
        kind="file",
        order=project.order,
        version=project.version,
        content_hash=_project_hash(project),
        path=str(path),
    )
    return project, source


def estimate_from_project(path: Path, home: Path | None = None, economy: bool = False) -> EstimateResult:
    """`kitchen estimate --project <файл>`: смета проекта из файла, без файлов выводов и записи в БД."""
    home = home or resolve_home()
    project, source = _project_file(path)
    brief = _brief_of_project(home, project.order)
    return _estimate_run(project, home, source, brief, [], load_workshop(home), economy)[0]


def economy_parts_from_project(path: Path, home: Path | None = None) -> PartsResult:
    """Детали копии проекта из файла со всеми предложенными заменами «экономии»: то, из чего считана цена
    «экономии». Ничего не пишет."""
    home = home or resolve_home()
    project, source = _project_file(path)
    workshop = load_workshop(home)
    base = _priced(project, home, source, workshop).price  # без неё не известно, какие замены предлагаются
    _, together = _economy(project, home, source, workshop, base, price_together=False)
    return _build_parts(together, home, source, _brief_of_project(home, project.order), [], workshop)


def _economy_markdown(economy: EconomyVariant) -> list[str]:
    lines = ["## Экономия", "", f"Цена как в проекте: {economy.price_as_is} ₽", ""]
    if economy.replacements:
        lines += [
            f"- {line.name} (модули {', '.join(line.modules)}): цена с этой заменой {line.price} ₽, "
            f"разница {line.saving} ₽. {line.client_text}"
            for line in economy.replacements
        ]
        lines += [
            "",
            f"**Цена «экономии» со всеми заменами: {economy.price} ₽, разница {economy.saving} ₽**",
            "",
            "Сумма разниц по строкам может не совпасть с итогом: листы закупаются целыми, цена округляется.",
            "",
        ]
    elif economy.not_offered:
        lines += [
            (
                "Подходящие замены не удешевляют, поэтому не предложены: "
                f"цена «экономии» {economy.price} ₽, разница {economy.saving} ₽."
            ),
            "",
        ]
    else:
        lines += [f"Подходящих замен нет: цена «экономии» {economy.price} ₽, разница {economy.saving} ₽.", ""]
    if economy.not_offered:
        lines += ["### Не предложено", ""]
        lines += [
            f"- {line.name} (модули {', '.join(line.modules)}): не удешевляет, не предложена; "
            f"цена с этой заменой {line.price} ₽, разница {line.saving} ₽"
            for line in economy.not_offered
        ]
        lines.append("")
    if economy.problems:
        lines += ["### Замена не строится", "", *(f"- {problem}" for problem in economy.problems), ""]
    return lines


def _estimate_markdown(result: EstimateResult) -> str:
    """Телефонная версия сметы: все суммы и количества из единой модели."""
    lines = []
    if result.mock_notice:
        lines += [f"**{result.mock_notice}**", ""]
    lines += ["# Смета", "", f"Заказ {result.source.order} · версия проекта {result.source.version}", ""]
    if result.warnings:
        lines += ["## Предупреждения", "", *(f"- {warning}" for warning in result.warnings), ""]
    labor = result.labor
    lines += [
        "## Труд",
        "",
        f"Ставка {labor.rate_per_hour:g} ₽/ч.",
        "",
        *(
            f"- {line.name}: {line.quantity:g} {line.unit} ({line.basis}) × {line.norm_min:g} мин = "
            f"{line.minutes:g} мин = {line.amount:.2f} ₽"
            for line in labor.lines
        ),
        "",
        f"**Итого труд: {labor.minutes:g} мин, {labor.total:.2f} ₽**",
        "",
        "## Цена",
        "",
        f"- Материалы (закупка): {result.materials_cost:.2f} ₽",
        f"- Наценка на материалы: {result.markup:.2f} ₽",
        f"- Расходники: {result.consumables:.2f} ₽",
        f"- Доставка материалов: {result.delivery:.2f} ₽",
        f"- Труд: {labor.total:.2f} ₽",
        f"- Итого до округления: {result.price_before_rounding:.2f} ₽",
        f"- Маржа с учётом округления: {result.margin:.2f} ₽",
        "",
        f"**Цена: {result.price} ₽** (округление: {result.rounding})",
        "",
    ]
    if result.economy:
        lines += _economy_markdown(result.economy)
    return "\n".join(lines)


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
    _save_outputs(home, result.source, files)
    return result


class QuoteResult(QuoteBody, Result):
    """КП: то, что видит клиент. Цена — из сметы того же снимка проекта; себестоимости и наценки нет."""

    source: PartsSource
    warnings: list[str] = Field(
        default_factory=list
    )  # для мастера (CLI, `--json`); в quote.json и PDF не идут
    outputs: list[OutputInfo] = Field(default_factory=list)


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
    home = home or resolve_home()
    project, source, brief, warnings = _order_project(order, home)
    return _quote(project, home, source, brief, warnings, days, economy)


def quote_from_project(path: Path, days: int, home: Path | None = None, economy: bool = False) -> QuoteResult:
    """`kitchen quote --project <файл>`: КП проекта из файла, без файлов выводов и записи в БД."""
    home = home or resolve_home()
    project, source = _project_file(path)
    return _quote(project, home, source, _brief_of_project(home, project.order), [], days, economy)


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
                    order=result.source.order,
                    version=result.source.version,
                    mock=result.mock,
                    on=on or store.today(),
                ),
            ),
            (
                outputs[1],
                lambda path: path.write_text(
                    result.model_dump_json(indent=2, exclude={"warnings"}) + "\n", encoding="utf-8"
                ),
            ),
        ],
    )
    return result


# Документы цеха: виды выводов, которые пишет `kitchen production` (тикет 49 читает этот список).
PRODUCTION_KINDS: tuple[OutputKind, ...] = (
    store.CUTTING,
    store.EDGING,
    store.PURCHASE_MD,
    store.PURCHASE_JSON,
    store.ASSEMBLY,
)


class ProductionResult(Result):
    """Все производственные выводы одной версии проекта."""

    cutting: CutResult
    edging: EdgingResult
    purchase: PurchaseResult
    assembly: AssemblyResult
    mock_notice: str | None = None


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
                order=source.order,
                version=source.version,
                mock=cut.mock,
                on=day,
            ),
        ),
        (
            edging_output,
            lambda path: write_edging_map(
                path,
                edging.parts,
                edging.edges,
                order=source.order,
                version=source.version,
                mock=edging.mock,
                on=day,
            ),
        ),
        *purchase_files,
        (assembly_output, _assembly_writer(assembly, day)),
    ]
    _save_outputs(home, source, files)
    return ProductionResult(
        cutting=cut.model_copy(update={"output": cut_output}),
        edging=edging.model_copy(update={"output": edging_output}),
        purchase=purchase,
        assembly=assembly.model_copy(update={"output": assembly_output}),
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


class CheckResult(Result):
    """Находки проверок проекта: ошибки отклоняют импорт, предупреждения идут в журнал."""

    order: str
    findings: list[Finding]
    brief_id: int | None = None  # какой бриф использован (последний сохранённый заказа)
    brief_saved_at: str | None = None

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


def check_project(path: Path, home: Path | None = None) -> CheckResult:
    """`проверки проекта` из файла без БД: все находки сразу."""
    home = home or resolve_home()
    project = parse_project(_read_json(Path(path), "Файл проекта"))
    workshop = load_workshop(home)
    latest = None
    if store.db_path(home).exists():
        with store.connect(home) as conn:
            try:
                latest = _latest_brief(conn, store.find_order(conn, project.order)["id"])
            except OrderError:  # заказа ещё нет: проект проверяют как черновик
                latest = None
    return CheckResult(
        order=project.order,
        findings=run_checks(project, workshop, json.loads(latest["data"]) if latest else None),
        brief_id=latest["id"] if latest else None,
        brief_saved_at=latest["created_at"] if latest else None,
        mock=workshop.is_mock,
        mock_files=workshop.mock_files,
    )


# --- быстрая вилка ------------------------------------------------------------


class QuickEstimate(Result):
    meters: int | float
    per_meter_low: int | float
    per_meter_high: int | float
    total_low: int
    total_high: int
    rounding: str  # как округлено, словами


def _num(x: float) -> int | float:
    """Целое число — целым в JSON (85000, не 85000.0)."""
    return int(x) if x == int(x) else x


def quick_estimate(meters: float, home: Path | None = None) -> QuickEstimate:
    """Вилка цены по погонным метрам: быстрая_оценка_за_пог_м × метры, концы округлены по `pricing.yaml`."""
    if not (meters > 0 and meters != float("inf")):  # NaN и бесконечность тоже сюда
        raise InputError("Длина кухни в погонных метрах должна быть числом больше нуля.")
    workshop = load_workshop(home)
    pricing = workshop.pricing
    low, high = pricing.quick_estimate_per_m
    rule = pricing.price_rounding
    exact = Decimal(str(meters))  # через строку: без шума плавающей запятой
    return QuickEstimate(
        meters=_num(meters),
        per_meter_low=_num(low),
        per_meter_high=_num(high),
        total_low=rule.apply(exact * Decimal(str(low))),
        total_high=rule.apply(exact * Decimal(str(high))),
        rounding=rule.describe(),
        mock=workshop.is_mock,
        mock_files=workshop.mock_files,
    )
