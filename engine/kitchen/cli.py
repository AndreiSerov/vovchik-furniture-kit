"""CLI `kitchen`: тонкая обёртка над `kitchen.api`.

Текст по умолчанию, `--json` печатает ровно результат API. Плашка мока и выход при
ошибке живут в `emit`/`fail`: все команды печатают через них.
"""

import json as stdlib_json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn, TypeVar

import typer
from typer.core import TyperCommand

from kitchen import api
from kitchen.errors import InputError, KitchenError, ProjectFileError
from kitchen.result import MOCK_BANNER, Result

app = typer.Typer(help="Движок мастерской: проверка данных, заказы, раскрой, смета.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Движок мастерской: проверка данных, заказы, раскрой, смета."""


JsonOpt = Annotated[bool, typer.Option("--json", help="Печатать результат API как JSON.")]


R = TypeVar("R", bound=Result)


def emit(result: R, render: Callable[[R], str], as_json: bool, ok: bool = True, code: int = 1) -> None:
    """Печатает результат; при моке добавляет плашку первой и последней строкой текста."""
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        text = render(result)
        typer.echo(f"{MOCK_BANNER}\n\n{text}\n\n{MOCK_BANNER}" if result.mock else text)
    if not ok:
        raise typer.Exit(code)


def fail(err: KitchenError, code: int = 1) -> NoReturn:
    text = "\n".join(err.problems)
    # Принцип 4: цифры в тексте ошибки могли быть посчитаны из мок-мастерской, плашка нужна и здесь.
    typer.echo(f"{MOCK_BANNER}\n\n{text}\n\n{MOCK_BANNER}" if api.is_mock() else text, err=True)
    raise typer.Exit(code)


def _render_check(r: api.WorkshopCheck) -> str:
    lines = ["Мастерская: " + ("данные верны." if r.valid else "найдены ошибки.")]
    for f in r.files:
        counts = ", ".join(f"{k}: {v}" for k, v in f.counts.items())
        lines.append(f"  {f.file}{' (мок)' if f.mock else ''} — {counts}")
    if r.errors:
        lines.append("")
        lines += [f"ОШИБКА {e}" for e in r.errors]
    if r.warnings:
        lines.append("")
        lines += [f"ПРЕДУПРЕЖДЕНИЕ {w}" for w in r.warnings]
    if r.mock:
        lines += [
            "",
            "Мок-файлы (значения-заглушки, замените своими, см. MOCKS.md): " + ", ".join(r.mock_files),
        ]
    return "\n".join(lines)


@app.command("check-workshop")
def check_workshop(json: JsonOpt = False) -> None:
    """Проверить файлы workshop/ и напечатать сводку."""
    try:
        result = api.check_workshop()
    except KitchenError as e:
        fail(e)
    emit(result, _render_check, json, ok=result.valid)


# --- заказы ------------------------------------------------------------------

order_app = typer.Typer(help="Заказы: завести, найти.", no_args_is_help=True)
app.add_typer(order_app, name="order")

OrderArg = Annotated[
    str, typer.Argument(help="Номер, номер-фамилия или часть фамилии (кириллицей или латиницей).")
]


def _render_order(r: api.OrderResult) -> str:
    o = r.order
    head = f"Заказ {o.id} заведён." if r.created else f"Заказ {o.id}."
    rows = [("Клиент", o.client), ("Телефон", o.phone), ("Адрес", o.address)]
    return "\n".join([head, *[f"  {k}: {v}" for k, v in rows if v]])


@order_app.command("new")
def order_new(
    order: Annotated[str, typer.Argument(help="Номер-фамилия: 2026-047-petrov.")],
    client: Annotated[str, typer.Option("--client", help="ФИО клиента.")] = "",
    phone: Annotated[str, typer.Option("--phone", help="Телефон.")] = "",
    address: Annotated[str, typer.Option("--address", help="Адрес объекта.")] = "",
    json: JsonOpt = False,
) -> None:
    """Завести заказ."""
    try:
        result = api.order_new(order, client=client, phone=phone, address=address)
    except KitchenError as e:
        fail(e)
    emit(result, _render_order, json)


@order_app.command("show")
def order_show(order: OrderArg, json: JsonOpt = False) -> None:
    """Найти заказ и показать его карточку."""
    try:
        result = api.order_show(order)
    except KitchenError as e:
        fail(e)
    emit(result, _render_order, json)


def _render_import(r: api.ImportResult) -> str:
    if not r.changed:
        return f"Заказ {r.order}: без изменений, текущая версия проекта — {r.version}."
    return f"Заказ {r.order}: принята версия {r.version} проекта."


@app.command("import")
def import_(
    order: OrderArg,
    project: Annotated[Path, typer.Argument(help="Файл project.json.")],
    json: JsonOpt = False,
) -> None:
    """Импортировать проект: новая версия появляется, только если проект изменился."""
    try:
        result = api.import_project(order, project)
    except KitchenError as e:
        fail(e)
    emit(result, _render_import, json)


@app.command("export")
def export(
    order: OrderArg,
    out: Annotated[
        Path | None, typer.Option("--out", help="Куда писать (по умолчанию orders/<заказ>/project.json).")
    ] = None,
    version: Annotated[
        int | None, typer.Option("--version", help="Номер версии (по умолчанию текущая).")
    ] = None,
    json: JsonOpt = False,
) -> None:
    """Записать project.json версии проекта (с номером версии)."""
    try:
        result = api.export_project(order, version=version, out=out)
    except KitchenError as e:
        fail(e)
    emit(result, lambda r: f"Заказ {r.order}: версия {r.version} проекта записана в {r.path}.", json)


@app.command("diff")
def diff(
    order: OrderArg,
    from_version: Annotated[str, typer.Argument(help="Исходная версия, например v1.")],
    to_version: Annotated[str, typer.Argument(help="Новая версия, например v2.")],
    json: JsonOpt = False,
) -> None:
    """Показать изменения модулей между версиями проекта."""

    def version(value: str) -> int:
        number = value.removeprefix("v")
        if not number.isascii() or not number.isdigit() or int(number) < 1:
            raise typer.BadParameter("Ожидается номер версии: v1, v2, …")
        return int(number)

    try:
        result = api.diff_project(order, version(from_version), version(to_version))
    except KitchenError as e:
        fail(e)
    emit(
        result, lambda r: "\n".join(r.changes) if r.changes else "Изменений модулей и пустых мест нет.", json
    )


@app.command("log")
def log(
    order: OrderArg,
    text: Annotated[str, typer.Argument(help="Текст записи.")],
    kind: Annotated[str, typer.Option("--kind", help="done, decision, next или note.")] = "note",
    json: JsonOpt = False,
) -> None:
    """Добавить запись в журнал заказа."""
    try:
        result = api.log_add(order, kind, text)
    except KitchenError as e:
        fail(e)
    emit(result, lambda r: f"Заказ {r.order}: записано ({r.entry.kind}): {r.entry.text}", json)


@app.command("status")
def status(order: OrderArg, json: JsonOpt = False) -> None:
    """Собрать и напечатать status.md заказа (файл orders/<заказ>/status.md перезаписывается)."""
    try:
        result = api.status(order)
    except KitchenError as e:
        fail(e)
    emit(result, lambda r: f"{r.text}\nФайл: {r.path}", json)


def _render_source(src: api.PartsSource) -> str:
    if src.kind == "order":
        head = f"Источник: заказ {src.order}, версия {src.version} проекта"
    else:
        version = f"версия {src.version}" if src.version else "версия в файле не записана"
        head = f"Источник: файл {src.path} (проект {src.order}, {version})"
    head += f", хеш {src.content_hash[:12]}"
    if src.brief_id is None:
        return head + "; брифа нет"
    brief = f"бриф №{src.brief_id} от {src.brief_saved_at}"
    if src.version_brief_id is not None and src.version_brief_id != src.brief_id:
        brief += f" (у версии записан бриф №{src.version_brief_id})"
    return f"{head}; {brief}"


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    """«1 ошибка», «2 ошибки», «5 ошибок»."""
    if n % 100 in range(11, 15):
        return f"{n} {forms[2]}"
    return f"{n} {forms[0] if n % 10 == 1 else forms[1] if n % 10 in (2, 3, 4) else forms[2]}"


def _render_parts(r: api.PartsResult) -> str:
    errors = sum(f.level == "error" for f in r.findings)
    summary = (
        f"Проверки: {_plural(errors, ('ошибка', 'ошибки', 'ошибок'))}, "
        f"{_plural(len(r.findings) - errors, ('предупреждение', 'предупреждения', 'предупреждений'))}"
    )
    lines = [f"Детали проекта {r.order}:", _render_source(r.source), summary]
    for m in r.modules:
        lines.append(f"  Модуль {m.module} ({m.template}) — {len(m.parts)} дет.")
        for p in m.parts:
            edged = ", ".join(f"{side} {edge}" for side, edge in p.edges.items() if edge)
            tail = f", кромка: {edged}" if edged else ""
            grain = "текстура вдоль длины" if p.texture else "без текстуры"
            lines.append(
                f"    {p.id} — {p.name} {p.length}×{p.width}×{p.thickness} мм, {p.material}, {grain}{tail}"
            )
    for g in r.gaps:
        what = f" ({g.appliance})" if g.appliance else ""
        tail = ", деталей нет" if g.appliance else ""
        lines.append(f"  {g.label} — пустое место {g.width} мм{what}{tail}")
    hw = ", ".join(f"{pid} ×{n}" for pid, n in r.hardware.items())
    lines.append(f"Фурнитура: {hw}")
    for edge_id, e in r.edges.items():
        lines.append(
            f"Кромка {edge_id}: чистая {e.clean_m:.3f} м, с припуском "
            f"{e.with_allowance_m:.3f} м, к закупке {e.purchase_m:.1f} м"
        )
    lines.append(f"Фасады: {r.facade_area:.4f} м²")
    for color, area in r.facade_area_by_color.items():
        lines.append(f"  {color}: {area:.4f} м²")
    lines.append("Общие позиции:")
    for c in r.common:
        note = " (уже в фурнитуре)" if c.in_hardware else ""
        lines.append(f"  {c.item} — {c.name}: {c.quantity} {c.unit}{note}; {c.basis}")
    if r.findings:
        lines.append("Находки проверок:")
        for f in r.findings:
            lines.append(f"  [{'ОШИБКА' if f.level == 'error' else 'предупреждение'}] {f.message}")
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


@app.command("parts")
def parts(
    order: Annotated[
        str | None,
        typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project."),
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, черновик до импорта).")
    ] = None,
    json: JsonOpt = False,
) -> None:
    """Детали проекта: по модулям, фурнитура, кромка, фасады, общие позиции, находки проверок."""
    try:
        if (order is None) == (project is None):
            raise InputError("Укажите либо заказ (kitchen parts petrov), либо файл: --project project.json.")
        result = api.parts_from_order(order) if order is not None else api.parts_from_project(project)
    except KitchenError as e:
        fail(e)
    emit(result, _render_parts, json)


def _render_edges(r: api.EdgingResult) -> str:
    lines = ["Карта кромления:", _render_source(r.source)]
    for p in r.parts:
        sides = ", ".join(f"{side}: {edge}" for side, edge in p.edges.items() if edge)
        lines.append(f"  {p.id} {p.length}×{p.width} мм — {sides}")
    for edge_id, e in r.edges.items():
        lines.append(
            f"Кромка {edge_id}: чистая {e.clean_m:.3f} м, с припуском {e.with_allowance_m:.3f} м, "
            f"к закупке {e.purchase_m:.3f} м"
        )
    if r.output:
        lines.append(f"Карта кромления PDF: {r.output.file} (версия проекта {r.output.source_version})")
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


@app.command("edges")
def edges(
    order: Annotated[
        str | None, typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project.")
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, черновик до импорта).")
    ] = None,
    json: JsonOpt = False,
) -> None:
    """Кромление: детали, стороны и метраж; для заказа также PDF карты."""
    try:
        if (order is None) == (project is None):
            raise InputError("Укажите либо заказ (kitchen edges petrov), либо файл: --project project.json.")
        result = api.edges_and_save(order) if order is not None else api.edges_from_project(project)
    except KitchenError as e:
        fail(e)
    emit(result, _render_edges, json)


def _render_drilling(r: api.DrillingResult) -> str:
    lines = ["Карта присадки:", _render_source(r.source)]
    for p in r.parts:
        holes = p.holes or []
        kinds = ", ".join(
            f"{api.HOLE_PURPOSES[k]} {n}"
            for k in api.HOLE_PURPOSES
            if (n := sum(1 for h in holes if h.purpose == k))
        )
        lines.append(f"  {p.id} {p.length}×{p.width} мм — отверстий {len(holes)}: {kinds}")
    lines.append("Сверка с фурнитурой:")
    for c in r.checks:
        lines.append(f"  {c.name} {c.holes} = {c.expected} ({c.basis})")
    if r.output:
        lines.append(f"Карта присадки PDF: {r.output.file} (версия проекта {r.output.source_version})")
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


@app.command("drilling")
def drilling(
    order: Annotated[
        str | None, typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project.")
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, черновик до импорта).")
    ] = None,
    json: JsonOpt = False,
) -> None:
    """Присадка: отверстия под конфирматы и петли, сверка с фурнитурой; для заказа также PDF карты."""
    try:
        if (order is None) == (project is None):
            raise InputError(
                "Укажите либо заказ (kitchen drilling petrov), либо файл: --project project.json."
            )
        result = api.drilling_and_save(order) if order is not None else api.drilling_from_project(project)
    except KitchenError as e:
        fail(e)
    emit(result, _render_drilling, json)


def _render_assembly(r: api.AssemblyResult) -> str:
    lines = ["Сборочный лист:", _render_source(r.source)]
    for m in r.modules:
        name = m.title.split(",")[0]  # «П1 — пенал»: метка и название шаблона без размеров
        lines.append(
            f"  {name}: деталей {len(m.parts)}, позиций фурнитуры {len(m.hardware)}, шагов {len(m.steps)}"
        )
    if r.output:
        lines.append(f"Сборочный лист PDF: {r.output.file} (версия проекта {r.output.source_version})")
    else:
        lines.append("Файл не записан (--project)")
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


@app.command("assembly")
def assembly(
    order: Annotated[
        str | None, typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project.")
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, черновик до импорта).")
    ] = None,
    json: JsonOpt = False,
) -> None:
    """Сборочный лист: по модулям детали, фурнитура цеха и порядок сборки; для заказа также PDF."""
    try:
        if (order is None) == (project is None):
            raise InputError(
                "Укажите либо заказ (kitchen assembly petrov), либо файл: --project project.json."
            )
        result = api.assembly_and_save(order) if order is not None else api.assembly_from_project(project)
    except KitchenError as e:
        fail(e)
    emit(result, _render_assembly, json)


def _render_trip(r: api.TripResult) -> str:
    lines = ["Чек-лист выезда:", _render_source(r.source), *r.header.lines()]
    for section in r.sections:
        lines.append(f"{section.title}:")
        lines += [f"  {i.text}" for i in section.items] or ["  —"]
    if r.output:
        lines.append(f"Чек-лист выезда PDF: {r.output.file} (версия проекта {r.output.source_version})")
    else:
        lines.append("Файл не записан (--project)")
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


@app.command("trip")
def trip(
    order: Annotated[
        str | None, typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project.")
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, черновик до импорта).")
    ] = None,
    json: JsonOpt = False,
) -> None:
    """Чек-лист выезда: модули, общие позиции, фурнитура и техника для монтажа, инструмент; для заказа также PDF."""
    try:
        if (order is None) == (project is None):
            raise InputError("Укажите либо заказ (kitchen trip petrov), либо файл: --project project.json.")
        result = api.trip_and_save(order) if order is not None else api.trip_from_project(project)
    except KitchenError as e:
        fail(e)
    emit(result, _render_trip, json)


def _render_cut(r: api.CutResult) -> str:
    lines = [
        f"Раскрой проекта {r.order}:",
        _render_source(r.source),
        f"Пила: пропил {r.kerf} мм, обрезка по периметру {r.trim} мм, макс. рез {r.max_cut_length} мм",
    ]
    for m in r.materials:
        grain = ", текстура вдоль первой стороны" if m.texture else ""
        lines.append(
            f"Материал {m.material} — {m.name}: листов {m.sheet_count} ({m.sheet_size[0]}×{m.sheet_size[1]} мм"
            f"{grain}), деталей {m.parts_count}, использование {m.utilization:.1%}, отход {m.waste_m2:.3f} м²"
        )
        for s in m.sheets:
            lines.append(f"  Лист {s.number}: {len(s.placements)} дет., использование {s.utilization:.1%}")
            for pl in s.placements:
                turn = ", повёрнута" if pl.rotated else ""
                lines.append(f"    {pl.part} {pl.w}×{pl.h} мм, x={pl.x} y={pl.y}{turn}")
            for c in s.cuts:
                axis = "вертикальный, x" if c.direction == "vertical" else "горизонтальный, y"
                what = "обрезка отхода" if c.kind == "trim" else "разделяет детали"
                lines.append(
                    f"    Рез {c.number}: {axis}={c.position}, от {c.start} до {c.end} (длина {c.length} мм), {what}"
                )
    if r.countertop:
        t = r.countertop
        lines.append(
            f"Столешница {t.item} — {t.name}: общая длина {t.total_length_mm} мм, "
            f"листов {t.sheet_count} ({t.sheet_length} мм)"
        )
    if r.output:
        lines.append(f"Карта раскроя PDF: {r.output.file} (версия проекта {r.output.source_version})")
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


@app.command("cut")
def cut(
    order: Annotated[
        str | None,
        typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project."),
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, черновик до импорта).")
    ] = None,
    json: JsonOpt = False,
) -> None:
    """Раскрой: гильотинная укладка по листам с порядком резов, столешница линейно. Заказ — ещё и PDF карты."""
    try:
        if (order is None) == (project is None):
            raise InputError("Укажите либо заказ (kitchen cut petrov), либо файл: --project project.json.")
        result = api.cut_and_save(order) if order is not None else api.cut_from_project(project)
    except KitchenError as e:
        fail(e)
    emit(result, _render_cut, json)


def _render_purchase(r: api.PurchaseResult) -> str:
    lines = ["Закупка:", _render_source(r.source)]
    for shop in r.shops:
        lines.append(f"Магазин {shop.name}:")
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
            note = f" ({', '.join(details)})" if details else ""
            lines.append(
                f"  {item.id} — {item.name}{note}: {item.quantity} {item.unit} × "
                f"{item.unit_price} ₽ = {item.amount:.2f} ₽"
            )
        lines.append(f"  Итого по магазину: {shop.total:.2f} ₽")
    lines.append(f"Итого: {r.total:.2f} ₽")
    for output in r.outputs:
        lines.append(f"Файл: {output.file} (версия проекта {output.source_version})")
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


@app.command("purchase")
def purchase(order: OrderArg, json: JsonOpt = False) -> None:
    """Список закупки по магазинам: Markdown и JSON для текущей версии заказа."""
    try:
        result = api.purchase_and_save(order)
    except KitchenError as e:
        fail(e)
    emit(result, _render_purchase, json)


def _render_order_estimate(r: api.EstimateResult) -> str:
    lines = [
        "Смета:",
        _render_source(r.source),
        f"Себестоимость материалов (закупка): {r.materials_cost:.2f} ₽",
    ]
    lines.append(f"Труд (ставка {r.labor.rate_per_hour:g} ₽/ч):")
    for line in r.labor.lines:
        lines.append(
            f"  {line.name}: {line.quantity:g} {line.unit} ({line.basis}) × {line.norm_min:g} мин = "
            f"{line.minutes:g} мин = {line.amount:.2f} ₽"
        )
    lines += [
        f"  Итого труд: {r.labor.minutes:g} мин, {r.labor.total:.2f} ₽",
        f"Наценка на материалы: {r.markup:.2f} ₽",
        f"Расходники: {r.consumables:.2f} ₽",
        f"Доставка материалов: {r.delivery:.2f} ₽",
        f"Итого до округления: {r.price_before_rounding:.2f} ₽",
        f"Цена: {r.price} ₽ (округление: {r.rounding}); маржа {r.margin:.2f} ₽",
    ]
    if r.economy:
        lines += _render_economy(r.economy)
    for output in r.outputs:
        lines.append(f"Файл: {output.file} (версия проекта {output.source_version})")
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


def _render_economy(e: api.EconomyVariant) -> list[str]:
    lines = ["Экономия:", f"  Цена как в проекте: {e.price_as_is} ₽"]
    lines += [
        f"  {line.name} (модули {', '.join(line.modules)}): цена с заменой {line.price} ₽, разница {line.saving} ₽"
        for line in e.replacements
    ]
    lines += [
        f"  Не предложено, не удешевляет: {line.name} (модули {', '.join(line.modules)}), разница {line.saving} ₽"
        for line in e.not_offered
    ]
    lines += [f"  Не строится: {problem}" for problem in e.problems]
    lines.append(f"  Цена «экономии» со всеми заменами: {e.price} ₽, разница {e.saving} ₽")
    if e.replacements:
        lines.append("  Сумма разниц по строкам может не совпасть с итогом: целые листы, округление.")
    return lines


@app.command("estimate")
def estimate(
    order: Annotated[
        str | None,
        typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project."),
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, файлы не пишутся).")
    ] = None,
    economy: Annotated[
        bool,
        typer.Option(
            "--economy", help="Добавить вариант «экономия»: замены из economy.yaml и разница в цене."
        ),
    ] = False,
    json: JsonOpt = False,
) -> None:
    """Смета: себестоимость из закупки, труд по операциям, цена. Заказ — ещё и estimate.md, estimate.json."""
    try:
        if (order is None) == (project is None):
            raise InputError(
                "Укажите либо заказ (kitchen estimate petrov), либо файл: --project project.json."
            )
        result = (
            api.estimate_and_save(order, economy=economy)
            if order is not None
            else api.estimate_from_project(project, economy=economy)
        )
    except KitchenError as e:
        fail(e)
    emit(result, _render_order_estimate, json)


def _render_quote(r: api.QuoteResult) -> str:
    lines = [
        "КП:",
        _render_source(r.source),
        f"Итого: {api.money(r.price)}",
        f"Предоплата ({api.percent(r.prepayment_share)}): {api.money(r.prepayment)}",
        f"Остаток: {api.money(r.balance)}",
        f"Срок изготовления и монтажа: {api.plural(r.days, 'рабочий день', 'рабочих дня', 'рабочих дней')}",
    ]
    if r.economy:
        e = r.economy
        lines.append(
            f"Экономия: цена как в проекте {api.money(e.price_as_is)}, цена «экономии» {api.money(e.price)}, "
            f"разница {api.money(e.saving)}"
        )
    lines += [f"Файл: {output.file} (версия проекта {output.source_version})" for output in r.outputs]
    if r.warnings:
        lines.append("Предупреждения: " + "; ".join(r.warnings))
    return "\n".join(lines)


class _QuoteCommand(TyperCommand):
    """`--days` без значения падает в разборе опций раньше нашей проверки: ошибка тоже по-русски."""

    def parse_args(self, ctx, args):
        given = list(args)  # разбор съедает список: проверять после ошибки нечего
        try:
            return super().parse_args(ctx, args)
        except Exception as e:  # UsageError живёт во внутреннем модуле typer: берём по тексту
            if "--days" in str(e) or "--days=" in given:  # «--days= 35»: пустое значение и лишний аргумент
                fail(InputError(api.DAYS_ERROR))
            raise


@app.command("quote", cls=_QuoteCommand)
def quote(
    order: Annotated[
        str | None,
        typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project."),
    ] = None,
    days: Annotated[
        str | None,
        typer.Option(
            "--days",
            metavar="N",
            help="Срок изготовления и монтажа в рабочих днях: целое от 1 до 365 (обязательно).",
        ),
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, файлы не пишутся).")
    ] = None,
    economy: Annotated[
        bool, typer.Option("--economy", help="Добавить вариант «экономия»: две цены и список замен.")
    ] = False,
    json: JsonOpt = False,
) -> None:
    """КП клиенту: состав, итог, предоплата, срок, без себестоимости и наценки. Заказ — ещё quote.pdf и quote.json."""
    try:
        term = api.parse_days(days)
        if (order is None) == (project is None):
            raise InputError(
                "Укажите либо заказ (kitchen quote petrov --days 35), либо файл: --project project.json."
            )
        result = (
            api.quote_and_save(order, term, economy=economy)
            if order is not None
            else api.quote_from_project(project, term, economy=economy)
        )
    except KitchenError as e:
        fail(e)
    emit(result, _render_quote, json)


def _render_production(r: api.ProductionResult) -> str:
    lines = ["Производство:", _render_source(r.purchase.source)]
    for material in r.cutting.materials:
        lines.append(
            f"Материал {material.material} — {material.name}: листов {material.sheet_count}, "
            f"использование {material.utilization:.1%}"
        )
    if r.cutting.countertop:
        top = r.cutting.countertop
        lines.append(f"Столешница {top.item}: общая длина {top.total_length_mm} мм, листов {top.sheet_count}")
    purchase_edges = {
        item.id: item.quantity for shop in r.purchase.shops for item in shop.items if item.kind == "edge"
    }
    for edge_id, edge in r.edging.edges.items():
        lines.append(
            f"Кромка {edge_id}: чистая {edge.clean_m} м, с припуском "
            f"{edge.with_allowance_m} м, к закупке {purchase_edges[edge_id]} м"
        )
    for shop in r.purchase.shops:
        lines.append(f"Магазин {shop.name}: {shop.total:.2f} ₽")
    lines.append(f"Итого закупка: {r.purchase.total:.2f} ₽")
    for output in (r.cutting.output, r.edging.output, *r.purchase.outputs):
        if output:
            lines.append(f"Файл: {output.file} (версия проекта {output.source_version})")
    if r.assembly.output:
        lines.append(
            f"Сборочный лист PDF: {r.assembly.output.file} (версия проекта {r.assembly.output.source_version})"
        )
    if r.trip.output:
        lines.append(
            f"Чек-лист выезда PDF: {r.trip.output.file} (версия проекта {r.trip.output.source_version})"
        )
    if r.purchase.warnings:
        lines.append("Предупреждения: " + "; ".join(r.purchase.warnings))
    return "\n".join(lines)


@app.command("production")
def production(order: OrderArg, json: JsonOpt = False) -> None:
    """Пересчитать раскрой, кромление, закупку, сборочный лист и чек-лист выезда текущей версии заказа."""
    try:
        result = api.production_and_save(order)
    except KitchenError as e:
        fail(e)
    emit(result, _render_production, json)


# --- бриф и быстрая вилка ----------------------------------------------------

brief_app = typer.Typer(help="Бриф заказа: сохранить, показать.", no_args_is_help=True)
app.add_typer(brief_app, name="brief")


def _render_brief_saved(r: api.BriefSaveResult) -> str:
    head = f"Заказ {r.order} заведён, бриф сохранён." if r.created else f"Заказ {r.order}: бриф сохранён."
    return "\n".join(
        [
            head,
            f"  Не заполнено обязательных полей: {r.missing_required}",
            f"  Противоречий и рисков: {r.conflicts}",
        ]
    )


@brief_app.command("save")
def brief_save(
    order: Annotated[
        str,
        typer.Argument(help="Номер-фамилия (2026-047-petrov) или одна фамилия: тогда следующий номер года."),
    ],
    brief: Annotated[Path, typer.Argument(help="Файл brief.json.")],
    json: JsonOpt = False,
) -> None:
    """Проверить и сохранить бриф; заказа нет — завести."""
    try:
        result = api.brief_save(order, brief)
    except KitchenError as e:
        fail(e)
    emit(result, _render_brief_saved, json)


@brief_app.command("show")
def brief_show(order: OrderArg, json: JsonOpt = False) -> None:
    """Напечатать сохранённый бриф (JSON)."""
    try:
        result = api.brief_show(order)
    except KitchenError as e:
        fail(e)
    emit(result, lambda r: _dumps(r.brief), json)


def _dumps(data: object) -> str:
    return stdlib_json.dumps(data, ensure_ascii=False, indent=2)


def _rub(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def _render_estimate(r: api.QuickEstimate) -> str:
    return "\n".join(
        [
            f"Кухня {r.meters:g} пог. м.",
            f"  За пог. м: {_rub(r.per_meter_low)} – {_rub(r.per_meter_high)} ₽",
            f"  Вилка цены: {_rub(r.total_low)} – {_rub(r.total_high)} ₽ (округление {r.rounding})",
        ]
    )


@app.command("quick-estimate")
def quick_estimate(
    meters: Annotated[float, typer.Option("--meters", help="Длина кухни в погонных метрах.")],
    json: JsonOpt = False,
) -> None:
    """Быстрая вилка цены по погонным метрам (расценка из pricing.yaml)."""
    try:
        result = api.quick_estimate(meters)
    except KitchenError as e:
        fail(e)
    emit(result, _render_estimate, json)


def _render_check_result(r: api.CheckResult) -> str:
    errors = r.errors
    summary = (
        f"Проверки: {_plural(len(errors), ('ошибка', 'ошибки', 'ошибок'))}, "
        f"{_plural(len(r.warnings), ('предупреждение', 'предупреждения', 'предупреждений'))}"
    )
    lines = [f"Проверки проекта {r.order}:", _render_source(r.source), summary]
    if r.findings:
        lines += [f"  [ОШИБКА] {f.message}" for f in errors]
        lines += [f"  [предупреждение] {f.message}" for f in r.warnings]
    else:
        lines.append("Замечаний нет.")
    if r.brief_note:
        lines.append(r.brief_note)
    return "\n".join(lines)


@app.command("check")
def check(
    order: Annotated[
        str | None,
        typer.Argument(help="Заказ: номер, номер-фамилия или фамилия. Либо --project."),
    ] = None,
    project: Annotated[
        Path | None, typer.Option("--project", help="Файл project.json (без БД, черновик до импорта).")
    ] = None,
    json: JsonOpt = False,
) -> None:
    """Проверки проекта без расчёта выводов. Код выхода: 0 — ошибок нет, 1 — есть ошибка, 2 — команда не выполнилась."""
    try:
        if (order is None) == (project is None):
            raise InputError("Укажите либо заказ (kitchen check petrov), либо файл: --project project.json.")
        result = api.check_order(order) if order is not None else api.check_project(project)
    except ProjectFileError as e:  # файл — JSON, но проект надо править: код 1
        fail(e)
    except KitchenError as e:
        fail(e, code=2)
    emit(result, _render_check_result, json, ok=not result.errors)
