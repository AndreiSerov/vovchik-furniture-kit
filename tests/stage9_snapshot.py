"""Снимок выводов для проверки рефакторинга этапа 9 (тикет 66; удаляется тикетом 75).

Помощник строит шесть случаев функциями `kitchen.api` и пишет снимок в папку: файлы «путь → текст».
Имя не `test_*`, pytest его не собирает. В репозитории снимок не хранится: тест прогоняет помощник на
коммите 086cf59 и на текущем коде и сравнивает две папки.

    uv run python tests/stage9_snapshot.py --out DIR     # снимок текущего `kitchen` в DIR (для ручного diff -r)

Мастерская (`workshop/`) и входные файлы берутся из репозитория, где лежит помощник, при любом `kitchen`.
Помощник ходит только в `kitchen.api`; `kitchen.store.today` и `kitchen.store.now` подменяет строкой.
Вызовы API собраны в `order_reads`, `project_reads` и `save_steps`: в тикете 75 ветка для нового
интерфейса (`hasattr(api, "calculation")`) добавляется именно там.
"""

import copy
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

from kitchen import api
from pydantic import BaseModel
from pypdf import PdfReader

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
FIXTURES = TESTS / "fixtures"

ORDER = "2026-047-petrov"
NOW = "2026-11-18T12:00:00+00:00"
TODAY = date(2026, 11, 18)
DAYS = 20
HOME = "<HOME>"

# Малый проект теста КП (`tests/test_quote.py`, SMALL) на стене 1000 мм вместо 1350: ряды не сходятся.
BROKEN_ORDER = "2026-051-sidorov"


def _broken_project() -> dict:
    return {
        "order": BROKEN_ORDER,
        "ceiling_height": 2700,
        "walls": [
            {
                "label": "A",
                "length": 1000,
                "rows": [
                    {
                        "kind": "lower",
                        "modules": [
                            {"label": "Н1", "template": "base_drawers", "width": 600},
                            {
                                "label": "Н2",
                                "template": "base_sink",
                                "width": 750,
                                "contents": {"inserts": ["bin_2sec"]},
                            },
                        ],
                    },
                    {
                        "kind": "upper",
                        "modules": [
                            {"label": "В1", "template": "wall_lift", "width": 600},
                            {"label": "В2", "template": "wall_door", "width": 750, "shelves": 1},
                        ],
                    },
                ],
            }
        ],
        "materials": {
            "body": "ldsp_16_white",
            "facades_lower": "mdf_enamel_matt",
            "facades_upper": "mdf_enamel_matt",
            "handles": "handle_profile",
        },
        "appliances": [{"type": "sink", "width": 750, "module": "Н2"}],
    }


# --- запись значений ----------------------------------------------------------


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def _capture(call: Callable[[], Any]) -> Any:
    """Результат вызова в виде JSON; ошибка записывается типом и текстом."""
    try:
        return _plain(call())
    except Exception as e:  # noqa: BLE001 - снимок хранит любую ошибку старого кода как есть
        return {"error": f"{type(e).__name__}: {e}"}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


class Scrub:
    """Замена абсолютного префикса home (оба написания) на `<HOME>` во всех текстах снимка."""

    def __init__(self, home: Path):
        # Длинное написание первым: `/private/var/x` содержит `/var/x`.
        self.prefixes = sorted({str(home.resolve()), str(home)}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for prefix in self.prefixes:
            text = text.replace(prefix, HOME)
        return text


def _scrub_html(text: str) -> str:
    """Встроенные three.js и orbit заменяются на sha256: остальной HTML хранится целиком."""

    for name in ("three", "orbit"):
        text = re.sub(
            rf'(<script id="{name}"[^>]*>)(.*?)(</script>)',
            lambda m: m.group(1) + hashlib.sha256(m.group(2).encode()).hexdigest() + m.group(3),
            text,
            flags=re.DOTALL,
        )
    return text


# --- файлы и таблицы заказа ---------------------------------------------------


def _out_files(home: Path) -> dict[str, Path]:
    return {
        p.relative_to(home).as_posix(): p for p in sorted((home / "orders").glob("*/out/*")) if p.is_file()
    }


def _file_text(path: Path) -> str:
    if path.suffix == ".pdf":
        pages = PdfReader(path).pages
        return "".join(f"=== страница {i} ===\n{p.extract_text()}\n" for i, p in enumerate(pages, 1))
    text = path.read_text(encoding="utf-8")
    return _scrub_html(text) if path.suffix == ".html" else text


def _db_rows(home: Path) -> dict[str, list]:
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        outputs = conn.execute("SELECT kind, path, source_version FROM outputs ORDER BY id").fetchall()
        done = conn.execute(
            "SELECT text, project_version, auto FROM log WHERE kind = 'done' ORDER BY id"
        ).fetchall()
    return {
        "outputs": [{"kind": k, "path": p, "source_version": v} for k, p, v in outputs],
        "done": [{"text": t, "project_version": v, "auto": bool(a)} for t, v, a in done],
    }


# --- случаи -------------------------------------------------------------------


class Case:
    """Один случай: свой tmp home, снимок читающих выводов и (для 1 и 4) шагов `*_and_save`."""

    def __init__(self, root: Path, name: str):
        self.name = name
        self.home = root / name
        self.home.mkdir(parents=True)
        shutil.copytree(REPO / "workshop", self.home / "workshop")
        self.scrub = Scrub(self.home)
        self.files: dict[str, str] = {}
        self._seen: dict[str, str] = {}

    def put(self, name: str, text: str) -> None:
        self.files[f"{self.name}/{name}"] = self.scrub(text)

    def copy_in(self, fixture: str) -> Path:
        target = self.home / fixture
        shutil.copy(FIXTURES / fixture, target)
        return target

    def write_json(self, name: str, data: Any) -> Path:
        path = self.home / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def edit_workshop(self, name: str, old: str, new: str) -> None:
        path = self.home / "workshop" / name
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (name, old)
        path.write_text(text.replace(old, new), encoding="utf-8")

    # читающие выводы

    def reads(self, calls: dict[str, Callable[[], Any]]) -> None:
        for key, call in calls.items():
            self.put(f"reads/{key}.json", _dump(_capture(call)))

    def order_reads(self, order: str) -> None:
        home = self.home
        self.reads(
            {
                "parts": lambda: api.parts_from_order(order, home),
                "cut": lambda: api.cut_from_order(order, home),
                "edging": lambda: api.edges_from_order(order, home),
                "drilling": lambda: api.drilling_from_order(order, home),
                "assembly": lambda: api.assembly_from_order(order, home),
                "trip": lambda: api.trip_from_order(order, home),
                "purchase": lambda: api.purchase_from_order(order, home),
                "estimate": lambda: api.estimate_from_order(order, home),
                "estimate_economy": lambda: api.estimate_from_order(order, home, economy=True),
                "quote": lambda: api.quote_from_order(order, DAYS, home),
                "quote_economy": lambda: api.quote_from_order(order, DAYS, home, economy=True),
                "views": lambda: api.views_from_order(order, home),
                "placement": lambda: api.placement_from_order(order, home),
                "check": lambda: api.check_order(order, home),
                "layout_drilling": lambda: api.drilling_map_layout(api.drilling_from_order(order, home)),
                "layout_assembly": lambda: api.assembly_layout(api.assembly_from_order(order, home)),
                "layout_trip": lambda: api.trip_layout(api.trip_from_order(order, home)),
                "layout_views": lambda: api.views_layout(api.views_from_order(order, home)),
            }
        )

    def project_reads(self, path: Path) -> None:
        home = self.home
        self.reads(
            {
                "parts": lambda: api.parts_from_project(path, home),
                "cut": lambda: api.cut_from_project(path, home),
                "edging": lambda: api.edges_from_project(path, home),
                "drilling": lambda: api.drilling_from_project(path, home),
                "assembly": lambda: api.assembly_from_project(path, home),
                "trip": lambda: api.trip_from_project(path, home),
                "estimate": lambda: api.estimate_from_project(path, home),
                "estimate_economy": lambda: api.estimate_from_project(path, home, economy=True),
                "quote": lambda: api.quote_from_project(path, DAYS, home),
                "quote_economy": lambda: api.quote_from_project(path, DAYS, home, economy=True),
                "views": lambda: api.views_from_project(path, home),
                "placement": lambda: api.placement_from_project(path, home),
                "check": lambda: api.check_project(path, home),
                "economy_parts": lambda: api.economy_parts_from_project(path, home),
                "layout_drilling": lambda: api.drilling_map_layout(api.drilling_from_project(path, home)),
                "layout_assembly": lambda: api.assembly_layout(api.assembly_from_project(path, home)),
                "layout_trip": lambda: api.trip_layout(api.trip_from_project(path, home)),
                "layout_views": lambda: api.views_layout(api.views_from_project(path, home)),
            }
        )

    # шаги записи

    def save_steps(self, order: str) -> None:
        home = self.home
        steps: list[tuple[str, Callable[[], Any]]] = [
            ("cut", lambda: api.cut_and_save(order, home)),
            ("edging", lambda: api.edges_and_save(order, home)),
            ("drilling", lambda: api.drilling_and_save(order, home)),
            ("assembly", lambda: api.assembly_and_save(order, home)),
            ("trip", lambda: api.trip_and_save(order, home)),
            ("purchase", lambda: api.purchase_and_save(order, home)),
            ("estimate", lambda: api.estimate_and_save(order, home)),
            ("estimate_economy", lambda: api.estimate_and_save(order, home, economy=True)),
            ("quote", lambda: api.quote_and_save(order, DAYS, home)),
            ("quote_economy", lambda: api.quote_and_save(order, DAYS, home, economy=True)),
            ("views", lambda: api.views_and_save(order, home)),
            ("production", lambda: api.production_and_save(order, home)),
        ]
        for number, (key, call) in enumerate(steps, 1):
            prefix = f"save/{number:02d}_{key}"
            self.put(f"{prefix}/result.json", _dump(_capture(call)))
            current = _out_files(home)
            for rel, path in current.items():
                text = self.scrub(_file_text(path))
                if self._seen.get(rel) != text:  # в снимок идут новые и изменившиеся файлы
                    self._seen[rel] = text
                    self.put(f"{prefix}/files/{rel.removeprefix('orders/')}.txt", text)
            tables = _db_rows(home)
            self.put(f"{prefix}/outputs.json", _dump(tables["outputs"]))
            self.put(f"{prefix}/done.json", _dump(tables["done"]))
            self.put(f"{prefix}/files.json", _dump(sorted(current)))


def _without_sink_brief(raw: dict) -> dict:
    brief = copy.deepcopy(raw)
    items = brief["appliances"]["items"]["value"]
    brief["appliances"]["items"]["value"] = [i for i in items if i["type"] != "sink"]
    return brief


def _without_sink_project(raw: dict) -> dict:
    project = copy.deepcopy(raw)
    project["appliances"] = [a for a in project["appliances"] if a["type"] != "sink"]  # модуль Н5 остаётся
    return project


def case_1(root: Path) -> Case:
    """Заказ Петрова."""
    c = Case(root, "1_order_petrov")
    api.brief_save(ORDER, c.copy_in("petrov_brief.json"), c.home)
    api.import_project(ORDER, c.copy_in("petrov_project.json"), home=c.home)
    c.order_reads(ORDER)
    c.save_steps(ORDER)
    return c


def case_2(root: Path) -> Case:
    """Файл Петрова при существующем заказе с брифом."""
    c = Case(root, "2_file_petrov_with_order")
    api.brief_save(ORDER, c.copy_in("petrov_brief.json"), c.home)
    project = c.copy_in("petrov_project.json")
    api.import_project(ORDER, project, home=c.home)
    c.project_reads(project)
    return c


def case_3(root: Path) -> Case:
    """Заказ без брифа."""
    c = Case(root, "3_order_without_brief")
    api.order_new(ORDER, home=c.home)
    api.import_project(ORDER, c.copy_in("petrov_project.json"), home=c.home)
    c.order_reads(ORDER)
    return c


def case_4(root: Path) -> Case:
    """Бриф новее проекта, пропил 4.2, без мойки."""
    c = Case(root, "4_newer_brief_kerf_no_sink")
    raw_brief = json.loads((FIXTURES / "petrov_brief.json").read_text(encoding="utf-8"))
    raw_project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    brief = c.write_json("brief_no_sink.json", _without_sink_brief(raw_brief))
    project = c.write_json("project_no_sink.json", _without_sink_project(raw_project))
    api.order_new(ORDER, home=c.home)
    api.brief_save(ORDER, brief, c.home)
    api.import_project(ORDER, project, home=c.home)
    api.brief_save(ORDER, brief, c.home)
    c.edit_workshop("equipment.yaml", "пропил: 4}", "пропил: 4.2}")
    c.order_reads(ORDER)
    c.save_steps(ORDER)
    return c


def case_5(root: Path) -> Case:
    """Файл, где модуль Н3 не строится."""
    c = Case(root, "5_file_module_does_not_build")
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    for wall in project["walls"]:
        for row in wall["rows"]:
            for module in row["modules"]:
                if module["label"] == "Н3":
                    module["width"] = 100
                if module["label"] == "Н4":
                    module["width"] = 1100
    c.project_reads(c.write_json("project.json", project))
    return c


def case_6(root: Path) -> Case:
    """Файл с ошибкой проверки, детали строятся."""
    c = Case(root, "6_file_check_error")
    c.project_reads(c.write_json("project.json", _broken_project()))
    return c


CASES = (case_1, case_2, case_3, case_4, case_5, case_6)


def build_snapshot(root: Path | None = None) -> dict[str, str]:
    """Прогон всех случаев на старом коде; `root` — папка для tmp home (по умолчанию временная)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = root or Path(tmp)
        files: dict[str, str] = {}
        with (
            mock.patch("kitchen.store.today", lambda: TODAY),
            mock.patch("kitchen.store.now", lambda: NOW),
        ):
            for build in CASES:
                files.update(build(base).files)
        return files


def write_snapshot(files: dict[str, str], target: Path) -> None:
    for rel, text in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--out":
        sys.exit("usage: python tests/stage9_snapshot.py --out DIR")
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    write_snapshot(build_snapshot(), out)
