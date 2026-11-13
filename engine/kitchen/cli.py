"""CLI `kitchen`: тонкая обёртка над `kitchen.api`.

Текст по умолчанию, `--json` печатает ровно результат API. Плашка мока и выход при
ошибке живут в `emit`/`fail`: все команды печатают через них.
"""

import json as stdlib_json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn, TypeVar

import typer

from kitchen import api
from kitchen.errors import KitchenError
from kitchen.result import MOCK_BANNER, Result

app = typer.Typer(help="Движок мастерской: проверка данных, заказы, раскрой, смета.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Движок мастерской: проверка данных, заказы, раскрой, смета."""


JsonOpt = Annotated[bool, typer.Option("--json", help="Печатать результат API как JSON.")]


R = TypeVar("R", bound=Result)


def emit(result: R, render: Callable[[R], str], as_json: bool, ok: bool = True) -> None:
    """Печатает результат; при моке добавляет плашку первой и последней строкой текста."""
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        text = render(result)
        typer.echo(f"{MOCK_BANNER}\n\n{text}\n\n{MOCK_BANNER}" if result.mock else text)
    if not ok:
        raise typer.Exit(1)


def fail(err: KitchenError) -> NoReturn:
    typer.echo("\n".join(err.problems), err=True)
    raise typer.Exit(1)


def _render_check(r: api.WorkshopCheck) -> str:
    lines = ["Мастерская: " + ("данные верны." if r.valid else "найдены ошибки.")]
    for f in r.files:
        counts = ", ".join(f"{k}: {v}" for k, v in f.counts.items())
        lines.append(f"  {f.file}{' (мок)' if f.mock else ''} — {counts}")
    if r.errors:
        lines.append("")
        lines += [f"ОШИБКА {e}" for e in r.errors]
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
