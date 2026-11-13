"""Хранилище заказов: SQLite (ADR 0001). Здесь только БД и поиск заказа; логика команд — в `kitchen.api`."""

import os
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Literal

from kitchen.errors import OrderError
from kitchen.workshop import resolve_home

SCHEMA_VERSION = 2

# Миграции: номер схемы -> скрипт (операторы через «;», без «;» внутри комментариев).
# Добавить v3: написать _SCHEMA_V3, внести в MIGRATIONS и поднять SCHEMA_VERSION.
_SCHEMA_V1 = """
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,          -- ГГГГ-NNN
    slug TEXT NOT NULL,                   -- фамилия латиницей
    client TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE briefs (                     -- каждое сохранение брифа — строка, актуален последний
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE project_versions (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    version INTEGER NOT NULL,
    data TEXT NOT NULL,                   -- канонический JSON без поля version
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (order_id, version)
);
CREATE TABLE log (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('done', 'decision', 'next', 'note')),
    text TEXT NOT NULL,
    project_version INTEGER              -- версия проекта на момент записи, NULL если проекта ещё нет
);
CREATE TABLE outputs (                    -- используется с этапа 2
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


# check_result — итог check_brief.py при сохранении (комментарий внутри ALTER ломает схему в БД)
_SCHEMA_V2 = """
ALTER TABLE briefs ADD COLUMN check_result TEXT NOT NULL DEFAULT '{}'
"""

MIGRATIONS = {1: _SCHEMA_V1, 2: _SCHEMA_V2}


def now() -> str:
    """Текущее время в UTC (так хранится)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def today() -> date:
    """Сегодняшняя дата по местному времени (год для нумерации заказов)."""
    return datetime.now().astimezone().date()


def local_date(ts: str, tz: tzinfo | None = None) -> str:
    """Дата записи по местному времени (`tz` — для тестов)."""
    return datetime.fromisoformat(ts).astimezone(tz).date().isoformat()


def db_path(home: Path) -> Path:
    env = os.environ.get("KITCHEN_DB")
    if not env:
        return home / "orders" / "kitchen.db"
    return home / Path(env).expanduser()  # абсолютный путь остаётся как есть


def _user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _check_not_newer(current: int, path: Path) -> None:
    if current > SCHEMA_VERSION:
        raise OrderError(
            f"БД {path} создана более новой версией kitchen (схема {current}, эта версия знает "
            f"{SCHEMA_VERSION}). Обновите kitchen."
        )


def _migrate(conn: sqlite3.Connection, path: Path) -> None:
    """Доводит схему до SCHEMA_VERSION. Всё под одной блокировкой: параллельный первый запуск ждёт,
    перечитывает номер схемы и ничего не создаёт повторно; сбой посреди скрипта откатывается."""
    current = _user_version(conn)
    _check_not_newer(current, path)
    if current == SCHEMA_VERSION:
        return
    conn.isolation_level = None  # ручное управление транзакцией
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = _user_version(conn)  # перечитать под блокировкой
            _check_not_newer(current, path)
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for statement in MIGRATIONS[version].split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = ""


def _open(path: Path) -> sqlite3.Connection:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
    except (OSError, sqlite3.Error) as e:
        raise OrderError(f"БД {path}: не удалось открыть ({e}).") from e
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _migrate(conn, path)
    except sqlite3.DatabaseError as e:
        conn.close()
        if "not a database" in str(e):
            raise OrderError(f"БД {path}: файл не является базой данных kitchen.") from e
        raise OrderError(f"БД {path}: не удалось открыть ({e}).") from e
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def connect(home: Path | None = None):
    """Открывает БД (создаёт папки и схему при первом обращении) и закрывает по выходу."""
    home = home or resolve_home()
    with closing(_open(db_path(home))) as conn:
        yield conn


# --- имена заказов ----------------------------------------------------------

# Упрощённая транслитерация в духе загранпаспорта (не ГОСТ 7.79 B).
_TRANSLIT_TABLE = (
    "а=a б=b в=v г=g д=d е=e ё=e ж=zh з=z и=i й=y к=k л=l м=m н=n о=o п=p р=r с=s т=t у=u ф=f "
    "х=kh ц=ts ч=ch ш=sh щ=shch ъ= ы=y ь= э=e ю=yu я=ya"
)
_TRANSLIT = dict(pair.split("=") for pair in _TRANSLIT_TABLE.split())

NUMBER_RE = re.compile(r"^(\d{4}-\d{3})$")
ID_RE = re.compile(r"^(\d{4}-\d{3})-(.+)$")


def slugify(text: str) -> str:
    latin = "".join(_TRANSLIT.get(c, c) for c in text.strip().lower())
    return re.sub(r"[^a-z0-9]+", "-", latin).strip("-")


def parse_order_id(text: str) -> tuple[str, str]:
    """`2026-047-петров` -> (`2026-047`, `petrov`)."""
    m = ID_RE.match(text.strip())
    slug = slugify(m.group(2)) if m else ""
    if not m or not slug:
        raise OrderError(f"«{text}» — не номер заказа. Ожидалось ГГГГ-NNN-фамилия, например 2026-047-petrov.")
    return m.group(1), slug


def order_ref_of(row: sqlite3.Row) -> str:
    return f"{row['number']}-{row['slug']}"


@dataclass(frozen=True)
class OrderQuery:
    """Разобранный запрос заказа: `number` (ГГГГ-NNN), `full` (номер-фамилия) или `surname`."""

    kind: Literal["number", "full", "surname"]
    number: str | None = None
    slug: str | None = None


def parse_query(text: str) -> OrderQuery:
    """Единственное место, где запрос превращается в номер, полный номер или фамилию.

    Всё с цифрами, что не номер и не полный номер, — ошибка (опечатка в номере не станет фамилией).
    """
    q = text.strip()
    if NUMBER_RE.match(q):
        return OrderQuery("number", number=q)
    if ID_RE.match(q):
        number, slug = parse_order_id(q)
        return OrderQuery("full", number=number, slug=slug)
    if any(c.isdigit() for c in q):
        raise OrderError(
            f"«{text}» похоже на номер заказа, ожидалось ГГГГ-NNN-фамилия, например 2026-047-petrov."
        )
    slug = slugify(q)
    if not slug:
        raise OrderError(f"«{text}» — не фамилия. Укажите номер-фамилию (2026-047-petrov) или фамилию.")
    return OrderQuery("surname", slug=slug)


def find_order(conn: sqlite3.Connection, query: str) -> sqlite3.Row:
    """Точный номер, `номер-фамилия` или подстрока фамилии или имени клиента (кириллица и латиница)."""
    parsed = parse_query(query)
    rows = conn.execute("SELECT * FROM orders ORDER BY number").fetchall()
    if parsed.kind == "number":
        hits = [r for r in rows if r["number"] == parsed.number]
    elif parsed.kind == "full":
        hits = [r for r in rows if r["number"] == parsed.number and r["slug"] == parsed.slug]
        taken = [r for r in rows if r["number"] == parsed.number]
        if not hits and taken:
            raise OrderError(
                f"Номер {parsed.number} занят заказом {order_ref_of(taken[0])}, а не «{query.strip()}»."
            )
    else:
        needle = parsed.slug
        hits = [r for r in rows if needle in r["slug"] or needle in slugify(r["client"])]
    if not hits:
        raise OrderError(f"Заказ «{query}» не найден. Заведите его: kitchen order new ГГГГ-NNN-фамилия.")
    if len(hits) > 1:
        listing = ", ".join(order_ref_of(r) for r in hits)
        raise OrderError(f"«{query}» подходит к нескольким заказам: {listing}. Уточните номер.")
    return hits[0]
