"""Расчёт выводов заказа: типы результатов, чтение проекта и брифа, сборщики выводов, тексты файлов
и сохранение. `kitchen.api` — интерфейс над ним (ADR 0002); этот модуль `api` не импортирует."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

from kitchen import store
from kitchen.assembly import AssemblyModule, build_assembly
from kitchen.assembly_pdf import write_assembly
from kitchen.checks import Finding, duplicate_labels
from kitchen.checks import check_project as run_checks
from kitchen.common import (
    CommonItem,
    CountertopSize,
    compute_common,
    size_countertop,
)
from kitchen.cutting import MaterialCut, cut_parts, kerf_warnings
from kitchen.cutting_pdf import write_cutting_map
from kitchen.drilling import DrillingCheck, reconcile
from kitchen.drilling_pdf import write_drilling_map
from kitchen.economy import EconomyLine, EconomyVariant, apply_rule
from kitchen.edging_pdf import write_edging_map
from kitchen.errors import OrderError, ProjectFileError
from kitchen.estimate import EstimateLabor, build_labor, build_pricing
from kitchen.format import purchase_digits, working_days
from kitchen.metrics import Stamp
from kitchen.models import Workshop
from kitchen.parts import (
    EdgeTotals,
    ModuleParts,
    Part,
    PartsComputation,
    PartsGap,
    buildable_module_parts,
    compute_parts,
)
from kitchen.placement import GapPlace, ModulePlace, SceneObject, WallPlace, place_project
from kitchen.project import Project, parse_project
from kitchen.purchase import PurchaseShop, build_purchase
from kitchen.quote import QuoteBody, build_quote, check_days
from kitchen.quote_pdf import write_quote
from kitchen.result import MOCK_BANNER, Result
from kitchen.scene3d import Scene3D, build_scene3d
from kitchen.trip import NO_BRIEF_WARNING, TripHeader, TripSection, build_header, build_trip
from kitchen.trip_pdf import write_trip
from kitchen.views import ViewPage, ViewScene, build_scene
from kitchen.views import layout as views_page_layout
from kitchen.views_html import write_html
from kitchen.views_pdf import write_views
from kitchen.workshop import load_workshop, resolve_home


class LogEntry(BaseModel):
    created_at: str
    kind: str
    text: str
    project_version: int | None


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


@dataclass
class ValidationContext:
    """Что нужно проверке проекта: заказ и мастерская (`None`, если её данные не загрузились)."""

    order_ref: str  # номер-фамилия
    order_number: str  # ГГГГ-NNN
    workshop: Workshop | None = None
    workshop_problems: list[str] = field(default_factory=list)  # почему мастерская не загрузилась
    brief: dict | None = None  # последний бриф заказа (техника сверяется с ним)
    brief_id: int | None = None


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


def parse_project_file(data: Any, ctx: ValidationContext | None = None) -> Project:
    """Разбор файла проекта: один для импорта и `kitchen check --project`. Не объект, нет `order`, чужой
    заказ (только с `ctx`), схема; номер версии ставит движок, поле `version` файла не разбирается."""
    if not isinstance(data, dict):
        raise ProjectFileError("Проект должен быть JSON-объектом ({...}), а не списком или значением.")
    if "order" not in data:
        raise ProjectFileError("В проекте нет поля «order» с номером заказа.")
    value = data["order"]
    if ctx is not None and not _same_order(value, ctx):
        raise ProjectFileError(f"Поле «order» = «{value}» не совпадает с заказом {ctx.order_ref}.")
    try:
        return parse_project({k: v for k, v in data.items() if k != "version"})
    except OrderError as e:
        raise ProjectFileError(e.problems) from e


def _canonical(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} в JSON недопустимо")


def _insert_log(
    conn: sqlite3.Connection, pk: int, kind: str, text: str, version: int | None, auto: bool = False
) -> LogEntry:
    ts = store.now()
    conn.execute(
        "INSERT INTO log (order_id, created_at, kind, text, project_version, auto) VALUES (?, ?, ?, ?, ?, ?)",
        (pk, ts, kind, text, version, int(auto)),
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
    gaps: list[PartsGap]  # пустые места рядов: деталей у них нет
    hardware: dict[str, int]
    edges: dict[str, EdgeTotals]
    facade_area: float  # м², округлено до 4 знаков
    facade_area_by_color: dict[str, float]  # м² по цветам фасадов проекта
    common: list[CommonItem]  # общие позиции кухни: цоколь, шина, навесы, LED, столешница
    findings: list[Finding]
    warnings: list[str]
    purchase_digits: int = Field(default=1, exclude=True)  # знаков у «к закупке»; в JSON не идёт


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
    computed_parts: Callable[[], PartsComputation] | None = None,
) -> PartsResult:
    """`computed_parts` — кэш `compute_parts` расчёта; без него детали считаются заново (копии «экономии»)."""
    workshop = workshop or load_workshop(home)
    _require_unique_labels(project)
    computed = computed_parts() if computed_parts else compute_parts(project, workshop)
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
        gaps=computed.gaps,
        hardware=computed.hardware,
        edges=computed.edges,
        facade_area=computed.facade_area,
        facade_area_by_color=computed.facade_by_color,
        common=compute_common(project, workshop, computed.hardware),
        findings=run_checks(project, workshop, json.loads(brief["data"]) if brief else None),
        warnings=[*computed.warnings, *extra_warnings],
        purchase_digits=purchase_digits(workshop.construction.edging.purchase_rounding.step_m),
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
    computed_parts: Callable[[], PartsComputation] | None = None,
) -> CutResult:
    workshop = workshop or load_workshop(home)
    _require_unique_labels(project)
    computed = computed_parts() if computed_parts else compute_parts(project, workshop)
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


def _output_info(home: Path, source: PartsSource, kind: OutputKind, filename: str) -> OutputInfo:
    if source.version is None:
        raise OrderError(f"У заказа {source.order} нет версии проекта.")
    rel = f"orders/{source.order}/out/{filename}"
    return OutputInfo(kind=kind, path=rel, file=str(home / rel), source_version=source.version)


def _save_outputs(
    home: Path,
    source: PartsSource,
    files: list[tuple[OutputInfo, Callable[[Path], None]]],
    done: list[str],
) -> None:
    """Регистрирует и устанавливает комплект файлов под одной блокировкой; сбой восстанавливает старый комплект.

    `done` — тексты авто-записей «сделано» (тикет 57) по порядку: пишутся в той же транзакции, откат убирает
    и их.
    """
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
                for text in done:
                    _insert_log(conn, row["id"], "done", text, source.version, auto=True)
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


class EdgingResult(Result):
    """Детали с кромкой и итоговый метраж из расчёта деталей."""

    source: PartsSource
    parts: list[Part]
    edges: dict[str, EdgeTotals]
    warnings: list[str]
    output: OutputInfo | None = None
    purchase_digits: int = Field(
        default=1, exclude=True
    )  # знаков у «к закупке» по шагу округления; в JSON не идёт


def _edging(parts: PartsResult) -> EdgingResult:
    return EdgingResult(
        purchase_digits=parts.purchase_digits,
        source=parts.source,
        parts=[p for m in parts.modules for p in m.parts if any(p.edges.values())],
        edges=parts.edges,
        warnings=parts.warnings,
        mock=parts.mock,
        mock_files=parts.mock_files,
    )


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


class AssemblyResult(Result):
    """Сборочный лист: по модулям детали, вся фурнитура (у «объект» — пометка) и шаги сборки; версия-источник."""

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


class TripResult(Result):
    """Чек-лист выезда: шапка из брифа, разделы со строками; версия-источник."""

    source: PartsSource
    output: OutputInfo | None = None
    warnings: list[str]
    header: TripHeader
    sections: list[TripSection]


def _trip(project: Project, parts: PartsResult, workshop: Workshop, brief: sqlite3.Row | None) -> TripResult:
    """Шапка берётся из брифа `brief` (строка БД); нет брифа — «—» и предупреждение."""
    data = json.loads(brief["data"]) if brief else None
    return TripResult(
        source=parts.source,
        warnings=[*parts.warnings, *([] if brief else [NO_BRIEF_WARNING])],
        header=build_header(data),
        sections=build_trip(project, parts.common, parts.hardware, workshop),
        mock=parts.mock,
        mock_files=parts.mock_files,
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


def _project_file(path: Path) -> tuple[Project, PartsSource]:
    data = _read_json(Path(path), "Файл проекта")
    project = parse_project_file(data)
    version = data.get("version")
    source = PartsSource(
        kind="file",
        order=project.order,
        version=version if type(version) is int and version >= 1 else None,
        content_hash=_project_hash(project),
        path=str(path),
    )
    return project, source


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


class QuoteResult(QuoteBody, Result):
    """КП: то, что видит клиент. Цена — из сметы того же снимка проекта; себестоимости и наценки нет."""

    source: PartsSource
    warnings: list[str] = Field(
        default_factory=list
    )  # для мастера (CLI, `--json`); в quote.json и PDF не идут
    outputs: list[OutputInfo] = Field(default_factory=list)


class ProductionResult(Result):
    """Все производственные выводы одной версии проекта."""

    cutting: CutResult
    edging: EdgingResult
    purchase: PurchaseResult
    assembly: AssemblyResult
    trip: TripResult
    mock_notice: str | None = None


class CheckResult(Result):
    """Находки проверок проекта: ошибки отклоняют импорт, предупреждения идут в журнал."""

    order: str
    source: PartsSource  # версия проекта заказа или файл, как у `parts`
    findings: list[Finding]
    brief_id: int | None = None  # какой бриф использован (последний сохранённый заказа)
    brief_saved_at: str | None = None
    brief_note: str | None = None  # бриф новее брифа версии: совет переимпортировать (только у заказа)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


def _check_result(
    project: Project, source: PartsSource, brief: sqlite3.Row | None, note: str | None, workshop: Workshop
) -> CheckResult:
    """Находки проверок: одна функция для файла и для заказа; импорт зовёт те же `run_checks`."""
    return CheckResult(
        order=project.order,
        source=source.model_copy(
            update={
                "brief_id": brief["id"] if brief else None,
                "brief_saved_at": brief["created_at"] if brief else None,
            }
        ),
        findings=run_checks(project, workshop, json.loads(brief["data"]) if brief else None),
        brief_id=brief["id"] if brief else None,
        brief_saved_at=brief["created_at"] if brief else None,
        brief_note=note,
        mock=workshop.is_mock,
        mock_files=workshop.mock_files,
    )


class PlacementResult(Result):
    """Расстановка модулей проекта в комнате (тикет 23): стены, модули с коробками, версия-источник.
    Только API: команд и файлов нет, её читают виды (41) и HTML (42)."""

    order: str
    source: PartsSource
    walls: list[WallPlace]
    modules: list[ModulePlace]
    gaps: list[GapPlace]
    top_of_uppers: int  # верх верхних модулей от пола, мм
    findings: list[Finding]  # находки расстановки при готовых положениях: `corner_overlap`, `tall_heights`
    scene: list[SceneObject]  # модель сцены: модули, стены, пол


def _placement(project: Project, source: PartsSource, workshop: Workshop) -> PlacementResult:
    _require_unique_labels(project)
    placed = place_project(project, workshop)
    return PlacementResult(
        order=project.order,
        source=source,
        walls=placed.walls,
        modules=placed.modules,
        gaps=placed.gaps,
        top_of_uppers=placed.top_of_uppers,
        findings=placed.findings,
        scene=placed.scene,
        mock=workshop.is_mock,
        mock_files=workshop.mock_files,
    )


class ViewsResult(Result):
    """Виды кухни (тикет 41): сцена видов из расстановки, находки проверок проекта (при ошибках виды рисуются с
    плашкой) и предупреждения расстановки (`tall_heights`). Ошибки расстановки — отказ (`OrderError`)."""

    order: str
    source: PartsSource
    findings: list[Finding]  # находки проверок проекта, как у `kitchen check`
    placement_findings: list[Finding]  # предупреждения расстановки: `tall_heights`
    scene: ViewScene
    scene3d: Scene3D  # та же сцена, что встроена в HTML (тикет 42)
    walls: int
    pages: int
    warnings: list[str]
    output: OutputInfo | None = None  # PDF видов
    output_html: OutputInfo | None = None  # HTML 3D
    _pages: list[ViewPage] | None = PrivateAttr(default=None)  # раскладка из `_views`, в JSON не идёт

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def check_warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


def _views(
    project: Project,
    source: PartsSource,
    brief: sqlite3.Row | None,
    warnings: list[str],
    workshop: Workshop,
    computed_parts: Callable[[], PartsComputation] | None = None,
) -> ViewsResult:
    _require_unique_labels(project)
    placed = place_project(project, workshop)
    refused = [f.message for f in placed.findings if f.level == "error"]
    if refused:
        raise OrderError(refused)
    findings = run_checks(project, workshop, json.loads(brief["data"]) if brief else None)
    errors = sum(f.level == "error" for f in findings)
    if errors:  # проект с ошибками проверок рисуется: не построившийся модуль — голая коробка без фасадов
        module_parts, part_warnings = buildable_module_parts(project, workshop), []
    else:
        computed = computed_parts() if computed_parts else compute_parts(project, workshop)
        module_parts, part_warnings = computed.modules, computed.warnings
    scene = build_scene(project, placed, module_parts, workshop)
    pages = views_page_layout(scene, errors)  # подписи, которым нет места, — ошибка уже здесь
    result = ViewsResult(
        order=project.order,
        source=source.model_copy(
            update={
                "brief_id": brief["id"] if brief else None,
                "brief_saved_at": brief["created_at"] if brief else None,
            }
        ),
        findings=findings,
        placement_findings=placed.findings,
        scene=scene,
        scene3d=build_scene3d(project, placed, scene, module_parts, workshop),
        walls=len(scene.walls),
        pages=len(pages),
        warnings=[*part_warnings, *warnings],
        mock=workshop.is_mock,
        mock_files=workshop.mock_files,
    )
    result._pages = pages
    return result


# --- расчёт ---------------------------------------------------------------------------------------


class Calculation:
    """Расчёт: одна версия проекта (заказа или файла) вместе с мастерской и брифом; из него считаются выводы.

    Проект читается при создании. Бриф заказа читается сразу, бриф файла — при первом обращении к `brief`,
    и только у выводов, которым он нужен. Мастерская загружается при первом выводе, один раз. Выводы
    кэшируются по аргументам: результат из расчёта не правят на месте, только через `model_copy`.
    """

    def __init__(
        self,
        home: Path,
        project: Project,
        source: PartsSource,
        warnings: list[str],
        brief: sqlite3.Row | None = None,
        brief_read: bool = False,
    ) -> None:
        self.home = home
        self.project = project
        self.source = source  # без брифа: `brief_id` и `brief_saved_at` пусты
        self._warnings = warnings  # «бриф новее» у заказа; у файла пусто
        self._brief = brief
        self._brief_read = brief_read
        self._workshop: Workshop | None = None
        self._cache: dict[tuple, Any] = {}

    @property
    def brief(self) -> sqlite3.Row | None:
        """Последний бриф заказа. У файла — из БД при первом обращении (заказа или БД нет — `None`)."""
        if not self._brief_read:
            self._brief = _brief_of_project(self.home, self.project.order)
            self._brief_read = True
        return self._brief

    def _ws(self) -> Workshop:
        if self._workshop is None:
            self._workshop = load_workshop(self.home)
        return self._workshop

    def _cached(self, key: tuple, build: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    def _computed(self) -> PartsComputation:
        """Детали проекта (`compute_parts`) — общий кэш `parts`, `cut`, `views` и `trip` файла."""
        return self._cached(("compute_parts",), lambda: compute_parts(self.project, self._ws()))

    def _parts(self, brief: sqlite3.Row | None, warnings: list[str]) -> PartsResult:
        return _build_parts(self.project, self.home, self.source, brief, warnings, self._ws(), self._computed)

    def parts(self) -> PartsResult:
        """Детали, общие позиции и проверки с брифом; предупреждения деталей, затем «бриф новее»."""
        return self._cached(("parts",), lambda: self._parts(self.brief, self._warnings))

    def cut(self) -> CutResult:
        """Раскрой: только кэш `compute_parts`, без общих позиций; предупреждения деталей, «бриф новее», пропил."""
        return self._cached(("cut",), self._cut)

    def _cut(self) -> CutResult:
        brief = self.brief
        return _build_cut(
            self.project, self.home, self.source, brief, self._warnings, self._ws(), self._computed
        )

    def edging(self) -> EdgingResult:
        return self._cached(("edging",), lambda: _edging(self.parts()))

    def drilling(self) -> DrillingResult:
        return self._cached(("drilling",), lambda: _drilling(self.parts(), self._ws()))

    def assembly(self) -> AssemblyResult:
        return self._cached(("assembly",), self._assembly)

    def _assembly(self) -> AssemblyResult:
        workshop = (
            self._ws()
        )  # как в `assembly_from_project`: мастерская раньше брифа файла (решение мастера)
        return _assembly(self.project, self.parts(), workshop)

    def trip(self) -> TripResult:
        """Чек-лист: шапка из брифа заказа. У файла детали без брифа, БД не открывается, шапка из «—»."""
        return self._cached(("trip",), self._trip)

    def _trip(self) -> TripResult:
        if self.source.kind == "file":
            return _trip(self.project, self._parts(None, []), self._ws(), None)
        return _trip(self.project, self.parts(), self._ws(), self.brief)

    def purchase(self) -> PurchaseResult:
        """Закупка: предупреждения `parts()`, затем пропил. Работает и для файла."""
        return self._cached(
            ("purchase",), lambda: _purchase(self.project, self.parts(), self.cut(), self._ws())
        )

    def estimate(self, economy: bool = False) -> EstimateResult:
        """Смета; `economy` добавляет вариант «экономия» поверх сметы без него (копии проекта — без брифа)."""
        return self._cached(("estimate", bool(economy)), lambda: self._estimate(bool(economy)))

    def _estimate(self, economy: bool) -> EstimateResult:
        if economy:
            return _with_economy(self.estimate(), self.project, self.home, self._ws(), economy=True)
        parts = self.parts()
        result = _estimate(self.project, parts, self.cut(), self._ws())
        draft = _draft_warnings(self.source, parts)
        return result.model_copy(update={"warnings": [*result.warnings, *draft]}) if draft else result

    def quote(self, days: int, economy: bool = False) -> QuoteResult:
        """КП: срок проверяется первым, раньше брифа и мастерской; цена — из `estimate(economy)`."""
        check_days(days)
        return self._cached(("quote", days, bool(economy)), lambda: self._quote(days, bool(economy)))

    def _quote(self, days: int, economy: bool) -> QuoteResult:
        estimate = self.estimate(economy)
        body = build_quote(
            self.project,
            self.parts().modules,
            self._ws(),
            days=days,
            price=estimate.price,
            economy=estimate.economy,
        )
        return QuoteResult(
            **body.model_dump(),
            source=estimate.source,
            warnings=estimate.warnings,
            mock=estimate.mock,
            mock_files=estimate.mock_files,
        )

    def views(self) -> ViewsResult:
        """Виды: кэш `compute_parts` (при находках `error` — `buildable_module_parts`), `parts()` не зовёт."""
        return self._cached(("views",), self._views)

    def _views(self) -> ViewsResult:
        brief = self.brief
        return _views(self.project, self.source, brief, self._warnings, self._ws(), self._computed)

    def placement(self) -> PlacementResult:
        """Расстановка: `source` без брифа, БД у файла не открывается."""
        return self._cached(("placement",), lambda: _placement(self.project, self.source, self._ws()))

    def check(self) -> CheckResult:
        """Находки проверок с брифом; `brief_note` — «бриф новее» у заказа, у файла `None`. `parts()` не зовёт."""
        return self._cached(("check",), self._check)

    def _check(self) -> CheckResult:
        brief = self.brief
        note = self._warnings[0] if self._warnings else None
        return _check_result(self.project, self.source, brief, note, self._ws())

    def economy_parts(self) -> PartsResult:
        """Детали копии проекта со всеми предложенными заменами «экономии» — то, из чего считана её цена.
        Детали копии — с брифом, цены копий — без; предупреждения только деталей копии."""
        return self._cached(("economy_parts",), self._economy_parts)

    def _economy_parts(self) -> PartsResult:
        brief, workshop = self.brief, self._ws()
        # от цены «как в проекте» зависит, какие замены предлагаются
        base = _priced(self.project, self.home, self.source, workshop).price
        _, together = _economy(self.project, self.home, self.source, workshop, base, price_together=False)
        return _build_parts(together, self.home, self.source, brief, [], workshop)


def calculation(
    order: str | None = None, project: Path | None = None, home: Path | None = None
) -> Calculation:
    """Расчёт по заказу (текущая версия проекта) или по файлу проекта (черновик до импорта); ровно одно."""
    if (order is None) == (project is None):
        raise OrderError("Укажите либо заказ, либо файл проекта.")
    home = home or resolve_home()
    if order is not None:
        parsed, source, brief, warnings = _order_project(order, home)
        return Calculation(home, parsed, source, warnings, brief, brief_read=True)
    parsed, source = _project_file(project)
    return Calculation(home, parsed, source, [])


# --- сохранение выводов ---------------------------------------------------------------------------


def _stamp(result: Any, on: date) -> Stamp:
    """Штамп вывода из результата: одно место, поля по именам (`bool` — подкласс `int`)."""
    return Stamp(order=result.source.order, version=result.source.version, mock=result.mock, on=on)


def _json_writer(exclude: set[str] | None = None) -> Callable[[Path, Any, Stamp], None]:
    """JSON результата с заполненными `outputs`; `exclude` — поля, которые в файл не идут."""
    return lambda path, result, stamp: path.write_text(
        result.model_dump_json(indent=2, exclude=exclude) + "\n", encoding="utf-8"
    )


def view_pages(result: ViewsResult) -> list[ViewPage]:
    """Раскладка страниц видов: из `_views` (`result._pages`), у результата, разобранного из JSON, — заново."""
    if result._pages is None:
        return views_page_layout(result.scene, len(result.errors))
    return result._pages


@dataclass(frozen=True)
class SavedFile:
    """Файл вывода: вид в `outputs`, имя в `out/`, поле результата и писатель (путь, результат, штамп)."""

    kind: OutputKind
    name: str
    field: Literal["output", "output_html", "outputs"]
    write: Callable[[Path, Any, Stamp], None]


@dataclass(frozen=True)
class Saving:
    """Как сохраняется тип результата: файлы по порядку и необязательный текст записи «сделано»."""

    files: tuple[SavedFile, ...]
    done: Callable[[Any], str] | None = None


# Таблица сохранения (спека этапа 9): единственное место, где имена файлов выводов связаны с писателями.
# Писатели зовутся по глобальным именам модуля, поэтому тест подменяет `kitchen.calculation.write_*`.
SAVE_TABLE: dict[type[Result], Saving] = {
    CutResult: Saving(
        (
            SavedFile(
                store.CUTTING,
                "cutting-map.pdf",
                "output",
                lambda path, r, stamp: write_cutting_map(
                    path, r.materials, kerf=r.kerf, kerf_declared=r.kerf_declared, trim=r.trim, stamp=stamp
                ),
            ),
        )
    ),
    EdgingResult: Saving(
        (
            SavedFile(
                store.EDGING,
                "edging-map.pdf",
                "output",
                lambda path, r, stamp: write_edging_map(
                    path, r.parts, r.edges, purchase_digits=r.purchase_digits, stamp=stamp
                ),
            ),
        )
    ),
    DrillingResult: Saving(
        (
            SavedFile(
                store.DRILLING,
                "drilling-map.pdf",
                "output",
                lambda path, r, stamp: write_drilling_map(path, r.parts, r.checks, stamp=stamp),
            ),
        )
    ),
    AssemblyResult: Saving(
        (
            SavedFile(
                store.ASSEMBLY,
                "assembly.pdf",
                "output",
                lambda path, r, stamp: write_assembly(path, r.modules, stamp=stamp),
            ),
        )
    ),
    TripResult: Saving(
        (
            SavedFile(
                store.TRIP,
                "trip-checklist.pdf",
                "output",
                lambda path, r, stamp: write_trip(path, r.header, r.sections, stamp=stamp),
            ),
        )
    ),
    PurchaseResult: Saving(
        (
            SavedFile(
                store.PURCHASE_MD,
                "purchase.md",
                "outputs",
                lambda path, r, stamp: path.write_text(_purchase_markdown(r), encoding="utf-8"),
            ),
            SavedFile(store.PURCHASE_JSON, "purchase.json", "outputs", _json_writer()),
        )
    ),
    EstimateResult: Saving(
        (
            SavedFile(
                store.ESTIMATE_MD,
                "estimate.md",
                "outputs",
                lambda path, r, stamp: path.write_text(_estimate_markdown(r), encoding="utf-8"),
            ),
            SavedFile(store.ESTIMATE_JSON, "estimate.json", "outputs", _json_writer()),
        ),
        done=lambda r: f"смета посчитана из версии {r.source.version}",
    ),
    QuoteResult: Saving(
        (
            SavedFile(
                store.QUOTE_PDF,
                "quote.pdf",
                "outputs",
                lambda path, r, stamp: write_quote(path, r, stamp=stamp),
            ),
            SavedFile(store.QUOTE_JSON, "quote.json", "outputs", _json_writer(exclude={"warnings"})),
        ),
        done=lambda r: f"КП посчитано из версии {r.source.version}, срок {working_days(r.days)}",
    ),
    ViewsResult: Saving(
        (
            SavedFile(
                store.VIEWS,
                "kitchen-views.pdf",
                "output",
                lambda path, r, stamp: write_views(path, view_pages(r), stamp=stamp),
            ),
            SavedFile(
                store.VIEWS_HTML,
                "kitchen-3d.html",
                "output_html",
                lambda path, r, stamp: write_html(path, r.scene3d, stamp=stamp, errors=len(r.errors)),
            ),
        )
    ),
}


def _same_calculation(source: PartsSource, calc_source: PartsSource) -> bool:
    return (source.order, source.version, source.content_hash) == (
        calc_source.order,
        calc_source.version,
        calc_source.content_hash,
    )


def _filled(home: Path, result: Any, saving: Saving) -> tuple[Any, list[OutputInfo]]:
    """Копия результата с заполненными полями таблицы (`output`, `output_html` или список `outputs`)
    и записи выводов в порядке файлов таблицы."""
    infos = [_output_info(home, result.source, f.kind, f.name) for f in saving.files]
    update: dict[str, Any] = {}
    for f, info in zip(saving.files, infos, strict=True):
        if f.field == "outputs":
            update.setdefault("outputs", []).append(info)
        else:
            update[f.field] = info
    return result.model_copy(update=update), infos


def save(
    calc: Calculation, *results: Any, on: date | None = None, _done: str | None = None
) -> tuple[Any, ...]:
    """Сохраняет выводы расчёта заказа одной транзакцией: файлы в `out/`, записи `outputs` и «сделано»
    в порядке аргументов. Возвращает копии результатов с заполненными полями файлов, тоже по порядку.

    `on` — дата штампа (по умолчанию сегодня). `_done` — запись «сделано» всего набора (`production`)."""
    if calc.source.kind == "file":
        raise OrderError("Расчёт из файла (--project) не сохраняется: файлы пишет только заказ.")
    if not results:
        raise OrderError("Нечего сохранять: передайте хотя бы один вывод расчёта.")
    for result in results:
        source = getattr(result, "source", None)
        if source is not None and not _same_calculation(source, calc.source):
            raise OrderError("Вывод посчитан другим расчётом: сохраняются только выводы этого расчёта.")
    savings = []
    for result in results:
        saving = SAVE_TABLE.get(type(result))
        if saving is None:
            raise OrderError(f"Вывод {type(result).__name__} не сохраняется: файлов у него нет.")
        savings.append(saving)
    kinds = [f.kind for saving in savings for f in saving.files]
    repeated = next((kind for i, kind in enumerate(kinds) if kind in kinds[:i]), None)
    if repeated is not None:
        raise OrderError(f"Вид вывода «{repeated}» повторяется в одном сохранении.")
    day = on or store.today()
    saved: list[Any] = []
    files: list[tuple[OutputInfo, Callable[[Path], None]]] = []
    done: list[str] = []
    for result, saving in zip(results, savings, strict=True):
        copy, infos = _filled(calc.home, result, saving)
        stamp = _stamp(copy, day)
        for f, info in zip(saving.files, infos, strict=True):
            files.append((info, lambda path, write=f.write, r=copy, s=stamp: write(path, r, s)))
        if saving.done is not None:
            done.append(saving.done(copy))
        saved.append(copy)
    if _done is not None:
        done.append(_done)
    _save_outputs(calc.home, calc.source, files, done)
    return tuple(saved)


# Документы цеха по порядку: результат и метод расчёта, который его считает.
_PRODUCTION: tuple[tuple[type[Result], Callable[[Calculation], Any]], ...] = (
    (CutResult, Calculation.cut),
    (EdgingResult, Calculation.edging),
    (PurchaseResult, Calculation.purchase),
    (AssemblyResult, Calculation.assembly),
    (TripResult, Calculation.trip),
)

# Виды выводов, которые пишет `kitchen production` (тикет 49 читает этот список): из таблицы сохранения.
PRODUCTION_KINDS: tuple[OutputKind, ...] = tuple(
    f.kind for kind, _ in _PRODUCTION for f in SAVE_TABLE[kind].files
)


def production(calc: Calculation, on: date | None = None) -> ProductionResult:
    """Документы цеха одной версии одним `save`: раскрой, кромка, закупка, сборочный лист, чек-лист
    и одна запись «сделано» на весь набор."""
    parts = calc.parts()  # как раньше: отказ деталей раньше отказа раскроя
    cutting, edging, purchase, assembly, trip = save(
        calc,
        *(method(calc) for _, method in _PRODUCTION),
        on=on,
        _done=f"документы цеха посчитаны из версии {calc.source.version}",
    )
    return ProductionResult(
        cutting=cutting,
        edging=edging,
        purchase=purchase,
        assembly=assembly,
        trip=trip,
        mock=parts.mock,
        mock_files=parts.mock_files,
        mock_notice=MOCK_BANNER if parts.mock else None,
    )
