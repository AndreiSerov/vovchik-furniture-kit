"""Публичный API движка. Вся логика здесь; CLI только печатает результат."""

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kitchen import brief as brief_check
from kitchen import store
from kitchen.errors import InputError, OrderError
from kitchen.models import Workshop
from kitchen.result import MOCK_BANNER, Result
from kitchen.workshop import FILES, Loaded, load, load_workshop, resolve_home

__all__ = [
    "BriefSaveResult",
    "BriefShowResult",
    "ExportResult",
    "FileSummary",
    "ImportResult",
    "LogEntry",
    "LogResult",
    "OrderInfo",
    "OrderResult",
    "QuickEstimate",
    "StatusResult",
    "ValidationContext",
    "ValidationResult",
    "WorkshopCheck",
    "brief_save",
    "brief_show",
    "check_workshop",
    "export_project",
    "import_project",
    "load_workshop",
    "log_add",
    "order_new",
    "order_show",
    "quick_estimate",
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


_LIST_KEYS = {
    "materials.yaml": ["листовые", "кромка", "столешницы", "фасады"],
    "hardware.yaml": ["позиции"],
    "labor.yaml": ["нормы_минут"],
}


def _counts(name: str, data: dict) -> dict[str, int]:
    keys = _LIST_KEYS.get(name)
    if keys:
        return {k: len(data.get(k) or []) for k in keys}
    return {"ключей": len([k for k in data if k != "mock"])}


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
        valid=not loaded.problems, files=files, errors=loaded.problems, mock=bool(mock), mock_files=mock
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


class ExportResult(Result):
    order: str
    version: int
    path: str
    project: dict[str, Any]


class StatusResult(Result):
    order: str
    stage: str
    project_version: int | None
    path: str
    text: str


LOG_KINDS = ("done", "decision", "next", "note")


def _info(row: sqlite3.Row) -> OrderInfo:
    return OrderInfo(
        id=store.order_ref_of(row),
        **{k: row[k] for k in ("number", "slug", "client", "phone", "address", "created_at", "updated_at")},
    )


def _mock_of(loaded: Loaded) -> dict[str, Any]:
    """Плашка мока на всех командах заказа: показываем, пока хоть один файл мастерской мок."""
    return {"mock": bool(loaded.mock_files), "mock_files": loaded.mock_files}


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


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)  # отклоняют импорт
    warnings: list[str] = field(default_factory=list)  # пишутся в журнал как note


Validator = Callable[[Any, ValidationContext], ValidationResult]


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
    """Минимальная обёртка проекта до этапа 1: объект с полем `order` — номер заказа или `номер-фамилия`.

    Шов для замены: тикет 06 подставит полную схему, тикет 10 — проверки правил (с мастерской из
    контекста и предупреждениями).
    """
    if not isinstance(data, dict):
        return ValidationResult(["Проект должен быть JSON-объектом ({...}), а не списком или значением."])
    if "order" not in data:
        return ValidationResult(["В проекте нет поля «order» с номером заказа."])
    value = data["order"]
    if not _same_order(value, ctx):
        return ValidationResult([f"Поле «order» = «{value}» не совпадает с заказом {ctx.order_ref}."])
    return ValidationResult()


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
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise OrderError(f"{path.name}: не JSON (строка {e.lineno}, столбец {e.colno}).") from e
    except ValueError as e:  # NaN/Infinity и ошибка кодировки
        raise OrderError(f"{path.name}: не JSON ({e}).") from e


def import_project(
    order: str, path: Path, validate: Validator = validate_wrapper, home: Path | None = None
) -> ImportResult:
    home = home or resolve_home()
    data = _read_json(Path(path), "Файл проекта")
    with _open_order(order, home) as o:
        loaded = load(home)
        mock = _mock_of(loaded)
        current = _current_version(o.conn, o.pk)
        ctx = ValidationContext(o.ref, o.row["number"], loaded.workshop)
        checked = validate(data, ctx)
        if checked.errors:
            tail = "Проект отклонён, текущая версия не изменена." if current else "Проект отклонён."
            raise OrderError([*checked.errors, tail])
        data = {k: v for k, v in data.items() if k != "version"}  # номер версии ставит движок
        text = _canonical(data)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        o.conn.execute("BEGIN IMMEDIATE")
        current = _current_version(o.conn, o.pk)  # перечитать под блокировкой
        prev = current["version"] if current else None
        if current and current["content_hash"] == digest:
            return ImportResult(
                order=o.ref,
                changed=False,
                version=prev,
                previous_version=prev,
                content_hash=digest,
                warnings=checked.warnings,
                **mock,
            )
        version = (prev or 0) + 1
        o.conn.execute(
            "INSERT INTO project_versions (order_id, version, data, content_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (o.pk, version, text, digest, store.now()),
        )
        for warning in checked.warnings:
            _insert_log(o.conn, o.pk, "note", f"Предупреждение при импорте: {warning}", version)
        o.changed = True
        return ImportResult(
            order=o.ref,
            changed=True,
            version=version,
            previous_version=prev,
            content_hash=digest,
            warnings=checked.warnings,
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
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return ExportResult(
            order=o.ref, version=found["version"], path=str(target), project=project, **_mock(o.home)
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
        lines += _section("Решения клиента", dated(entries("decision")[::-1]), "Пока нет.")
        lines += _section("Сделано", dated(entries("done")[::-1]), "Пока ничего.")
        lines += _section("Дальше", dated(entries("next", 5)), "Пока не записано.")
        notes = entries("note")
        if notes:
            lines += _section("Заметки", dated(notes[::-1]), "")
        text = "\n".join(lines).rstrip() + "\n"
        mock = _mock(o.home)
        target = o.home / "orders" / o.ref / "status.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        body = f"{MOCK_BANNER}\n\n{text}\n{MOCK_BANNER}\n" if mock["mock"] else text
        target.write_text(body, encoding="utf-8")
        return StatusResult(
            order=o.ref,
            stage=stage,
            project_version=cur["version"] if cur else None,
            path=str(target),
            text=text,
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


# --- быстрая вилка -----------------------------------------------------------


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
