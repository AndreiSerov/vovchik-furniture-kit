"""CLI `kitchen`: тонкая обёртка над `kitchen.api`.

Текст по умолчанию, `--json` печатает ровно результат API. Плашка мока и выход при
ошибке живут в `emit`/`fail`: все команды печатают через них.
"""

from collections.abc import Callable
from typing import Annotated, TypeVar

import typer

from kitchen import api
from kitchen.errors import WorkshopError
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


def fail(err: WorkshopError) -> None:
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
    except WorkshopError as e:
        fail(e)
        return
    emit(result, _render_check, json, ok=result.valid)
