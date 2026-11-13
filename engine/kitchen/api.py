"""Публичный API движка. Вся логика здесь; CLI только печатает результат."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from kitchen import store
from kitchen.errors import OrderError
from kitchen.result import MOCK_BANNER, Result
from kitchen.workshop import FILES, load, load_workshop, resolve_home

__all__ = [
    "ExportResult",
    "FileSummary",
    "ImportResult",
    "LogEntry",
    "LogResult",
    "OrderInfo",
    "OrderResult",
    "StatusResult",
    "WorkshopCheck",
    "check_workshop",
    "export_project",
    "import_project",
    "load_workshop",
    "log_add",
    "order_new",
    "order_show",
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


class OrderInfo(BaseModel):  # pydantic BaseModel без полей мока
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
        id=store.order_id_of(row),
        **{k: row[k] for k in ("number", "slug", "client", "phone", "address", "created_at", "updated_at")},
    )


def _mock(home: Path) -> dict[str, Any]:
    """Плашка мока на всех командах заказа: показываем, пока хоть один файл мастерской мок."""
    files = load(home).mock_files
    return {"mock": bool(files), "mock_files": files}


def _current_version(conn: sqlite3.Connection, order_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM project_versions WHERE order_id = ? ORDER BY version DESC LIMIT 1", (order_id,)
    ).fetchone()


def order_new(
    order: str, client: str = "", phone: str = "", address: str = "", home: Path | None = None
) -> OrderResult:
    home = home or resolve_home()
    number, slug = store.parse_order_id(order)
    with store.connect(home) as conn:
        if conn.execute("SELECT 1 FROM orders WHERE number = ?", (number,)).fetchone():
            raise OrderError(f"Заказ с номером {number} уже есть. Номер заказа уникален.")
        ts = store.now()
        conn.execute(
            "INSERT INTO orders (number, slug, client, phone, address, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (number, slug, client, phone, address, ts, ts),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM orders WHERE number = ?", (number,)).fetchone()
        return OrderResult(order=_info(row), created=True, **_mock(home))


def order_show(query: str, home: Path | None = None) -> OrderResult:
    home = home or resolve_home()
    with store.connect(home) as conn:
        return OrderResult(order=_info(store.find_order(conn, query)), **_mock(home))


# --- проект: импорт и экспорт ------------------------------------------------

Validator = Callable[[Any, str], list[str]]


def validate_wrapper(data: Any, order_number: str) -> list[str]:
    """Минимальная обёртка проекта до этапа 1: объект с полем `order`, совпадающим с заказом.

    Шов для замены: тикет 06 подставит полную схему, тикет 10 — проверки правил. Валидатор получает
    разобранный JSON и номер заказа (`ГГГГ-NNN`), возвращает список русских проблем.
    """
    if not isinstance(data, dict):
        return ["Проект должен быть JSON-объектом ({...}), а не списком или значением."]
    if "order" not in data:
        return ["В проекте нет поля «order» с номером заказа."]
    value = data["order"]
    if not isinstance(value, str) or value.split("-")[:2] != order_number.split("-"):
        return [f"Поле «order» = «{value}» не совпадает с заказом {order_number}."]
    return []


def _canonical(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def import_project(
    order: str, path: Path, validate: Validator = validate_wrapper, home: Path | None = None
) -> ImportResult:
    home = home or resolve_home()
    path = Path(path)
    if not path.is_file():
        raise OrderError(f"Файл проекта {path} не найден.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        where = f" (строка {e.lineno}, столбец {e.colno})" if isinstance(e, json.JSONDecodeError) else ""
        raise OrderError(f"{path.name}: не JSON{where}. Текущая версия проекта не изменена.") from e
    with store.connect(home) as conn:
        row = store.find_order(conn, order)
        problems = validate(data, row["number"])
        if problems:
            raise OrderError([*problems, "Проект отклонён, текущая версия не изменена."])
        data = {k: v for k, v in data.items() if k != "version"}  # номер версии ставит движок
        text = _canonical(data)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        oid = store.order_id_of(row)
        conn.execute("BEGIN IMMEDIATE")
        current = _current_version(conn, row["id"])
        prev = current["version"] if current else None
        if current and current["content_hash"] == digest:
            conn.rollback()
            return ImportResult(
                order=oid,
                changed=False,
                version=prev,
                previous_version=prev,
                content_hash=digest,
                **_mock(home),
            )
        version = (prev or 0) + 1
        ts = store.now()
        conn.execute(
            "INSERT INTO project_versions (order_id, version, data, content_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (row["id"], version, text, digest, ts),
        )
        conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (ts, row["id"]))
        conn.commit()
        return ImportResult(
            order=oid,
            changed=True,
            version=version,
            previous_version=prev,
            content_hash=digest,
            **_mock(home),
        )


def export_project(
    order: str, version: int | None = None, out: Path | None = None, home: Path | None = None
) -> ExportResult:
    home = home or resolve_home()
    with store.connect(home) as conn:
        row = store.find_order(conn, order)
        oid = store.order_id_of(row)
        if version is None:
            found = _current_version(conn, row["id"])
            if not found:
                raise OrderError(f"У заказа {oid} проекта ещё нет. Сначала kitchen import.")
        else:
            found = conn.execute(
                "SELECT * FROM project_versions WHERE order_id = ? AND version = ?", (row["id"], version)
            ).fetchone()
            if not found:
                raise OrderError(f"У заказа {oid} нет версии {version} проекта.")
    project = {**json.loads(found["data"]), "version": found["version"]}
    target = Path(out) if out else home / "orders" / oid / "project.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return ExportResult(order=oid, version=found["version"], path=str(target), project=project, **_mock(home))


# --- журнал и статус ---------------------------------------------------------


def log_add(order: str, kind: str, text: str, home: Path | None = None) -> LogResult:
    home = home or resolve_home()
    if kind not in LOG_KINDS:
        raise OrderError(f"Тип записи «{kind}» неизвестен. Допустимо: {', '.join(LOG_KINDS)}.")
    if not text.strip():
        raise OrderError("Текст записи пустой.")
    with store.connect(home) as conn:
        row = store.find_order(conn, order)
        current = _current_version(conn, row["id"])
        pv = current["version"] if current else None
        ts = store.now()
        conn.execute(
            "INSERT INTO log (order_id, created_at, kind, text, project_version) VALUES (?, ?, ?, ?, ?)",
            (row["id"], ts, kind, text.strip(), pv),
        )
        conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (ts, row["id"]))
        conn.commit()
        entry = LogEntry(created_at=ts, kind=kind, text=text.strip(), project_version=pv)
        return LogResult(order=store.order_id_of(row), entry=entry, **_mock(home))


STATUS_HEADER = (
    "<!-- Сгенерировано `kitchen status`. Не правьте руками: файл перезаписывается. "
    "Записи добавляются через `kitchen log`. -->"
)


def _stage(has_project: bool, has_outputs: bool, has_brief: bool) -> str:
    """Этап заказа по тому, что уже есть: выводы -> «Выводы», проект -> «Проект», бриф -> «Бриф»."""
    if has_outputs:
        return "Выводы"
    if has_project:
        return "Проект"
    return "Бриф" if has_brief else "Новый заказ"


def _section(title: str, rows: list[sqlite3.Row], empty: str, dated: bool = True) -> list[str]:
    lines = [f"## {title}", ""]
    lines += [f"- {r['created_at'][:10]}: {r['text']}" if dated else f"- {r['text']}" for r in rows] or [
        empty
    ]
    return [*lines, ""]


def status(order: str, home: Path | None = None) -> StatusResult:
    home = home or resolve_home()
    with store.connect(home) as conn:
        row = store.find_order(conn, order)
        oid = store.order_id_of(row)
        cur = _current_version(conn, row["id"])

        def entries(kind: str, limit: int | None = None) -> list[sqlite3.Row]:
            return conn.execute(
                "SELECT * FROM log WHERE order_id = ? AND kind = ? ORDER BY id DESC LIMIT ?",
                (row["id"], kind, limit if limit is not None else -1),
            ).fetchall()

        def exists(table: str) -> bool:
            return bool(
                conn.execute(f"SELECT 1 FROM {table} WHERE order_id = ?", (row["id"],)).fetchone()
            )  # имя таблицы из кода, не от пользователя

        stage = _stage(cur is not None, exists("outputs"), exists("briefs"))
        who = ", ".join(x for x in (row["client"], row["phone"], row["address"]) if x)
        lines = [f"# Заказ {oid}", "", STATUS_HEADER, ""]
        if who:
            lines += [who, ""]
        lines += ["## Этап", "", stage, "", "## Версия проекта", ""]
        lines += [f"Версия {cur['version']} от {cur['created_at'][:10]}."] if cur else ["Проекта ещё нет."]
        lines.append("")
        lines += _section("Решения клиента", list(reversed(entries("decision"))), "Пока нет.")
        lines += _section("Сделано", list(reversed(entries("done"))), "Пока ничего.")
        lines += _section("Дальше", entries("next", 5), "Пока не записано.")
        notes = entries("note")
        if notes:
            lines += _section("Заметки", list(reversed(notes)), "")
    text = "\n".join(lines).rstrip() + "\n"
    mock = _mock(home)
    target = home / "orders" / oid / "status.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    body = f"{MOCK_BANNER}\n\n{text}\n{MOCK_BANNER}\n" if mock["mock"] else text
    target.write_text(body, encoding="utf-8")
    return StatusResult(
        order=oid,
        stage=stage,
        project_version=cur["version"] if cur else None,
        path=str(target),
        text=text,
        **mock,
    )
