"""Хранилище заказов: SQLite (ADR 0001). Здесь только БД и поиск заказа; логика команд — в `kitchen.api`."""

import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from kitchen.errors import OrderError
from kitchen.workshop import resolve_home

SCHEMA_VERSION = 1

# user_version хранит номер схемы; миграции будущих версий добавляются сюда по номеру.
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
CREATE TABLE briefs (                     -- заполняет `brief save` (тикет 04)
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


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def db_path(home: Path) -> Path:
    import os

    env = os.environ.get("KITCHEN_DB")
    return Path(env).expanduser() if env else home / "orders" / "kitchen.db"


def _migrate(conn: sqlite3.Connection, path: Path) -> None:
    (current,) = conn.execute("PRAGMA user_version").fetchone()
    if current > SCHEMA_VERSION:
        raise OrderError(
            f"БД {path} создана более новой версией kitchen (схема {current}, эта версия знает {SCHEMA_VERSION}). "
            "Обновите kitchen."
        )
    if current < 1:
        conn.executescript(_SCHEMA_V1)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


@contextmanager
def connect(home: Path | None = None):
    """Открывает БД (создаёт папки и схему при первом обращении) и закрывает по выходу."""
    home = home or resolve_home()
    path = db_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _migrate(conn, path)
        yield conn


# --- имена заказов ----------------------------------------------------------

# Упрощённая транслитерация (близка к ГОСТ 7.79 системе B без диакритики).
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


def order_id_of(row: sqlite3.Row) -> str:
    return f"{row['number']}-{row['slug']}"


def find_order(conn: sqlite3.Connection, query: str) -> sqlite3.Row:
    """Точный номер, `номер-фамилия` или подстрока фамилии (кириллица и латиница)."""
    q = query.strip()
    rows = conn.execute("SELECT * FROM orders ORDER BY number").fetchall()
    if NUMBER_RE.match(q):
        hits = [r for r in rows if r["number"] == q]
    elif ID_RE.match(q):
        number, slug = parse_order_id(q)
        hits = [r for r in rows if r["number"] == number and r["slug"] == slug]
    else:
        needle = slugify(q)
        hits = [r for r in rows if needle and needle in r["slug"]]
    if not hits:
        raise OrderError(f"Заказ «{query}» не найден. Заведите его: kitchen order new ГГГГ-NNN-фамилия.")
    if len(hits) > 1:
        listing = ", ".join(order_id_of(r) for r in hits)
        raise OrderError(f"«{query}» подходит к нескольким заказам: {listing}. Уточните номер.")
    return hits[0]
