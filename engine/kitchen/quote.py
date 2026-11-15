"""КП (тикет 18): содержание коммерческого предложения клиенту из проекта, деталей и сметы.

Здесь только модель КП и сборка её из готовых результатов: цену и вариант «экономия» считает смета
(`kitchen.api`), детали — `kitchen.parts`. Себестоимости, наценки и труда в модели нет: КП строится
из итога, и эти числа в неё не попадают.
"""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from kitchen.economy import EconomyVariant
from kitchen.errors import InputError
from kitchen.models import Master, Workshop
from kitchen.parts import APPLIANCE_NAMES, DRAWER_BOTTOM, TEMPLATES, ModuleParts
from kitchen.project import ModuleSpec, Project, ProjectMaterials

__all__ = [
    "QuoteBody",
    "QuoteEconomy",
    "QuoteMaster",
    "QuoteModule",
    "QuoteReplacement",
    "build_quote",
    "check_days",
    "describe_module",
    "parse_days",
]

DAYS_ERROR = "Срок: укажите --days N — целое число рабочих дней больше нуля, например --days 35."


class QuoteMaster(BaseModel):
    name: str
    phone: str
    city: str
    status: str
    inn: str
    offer_days: int


class QuoteModule(BaseModel):
    label: str
    wall: str
    row: str
    text: str


class QuoteReplacement(BaseModel):
    """Замена «экономии» для клиента: текст `текст_клиенту` и модули, без сумм. Название правила — внутреннее
    (в нём бывают марки фурнитуры), клиенту не идёт."""

    client_text: str
    modules: list[str]


class QuoteEconomy(BaseModel):
    price_as_is: int
    price: int
    saving: int
    replacements: list[QuoteReplacement]


class QuoteBody(BaseModel):
    """Всё, что видит клиент. Одна модель для `quote.pdf`, `quote.json` и `--json`."""

    master: QuoteMaster
    days: int
    modules: list[QuoteModule]
    body: str
    facades_lower: str
    facades_upper: str
    countertop: str | None
    price: int
    prepayment_share: float
    prepayment: int
    balance: int
    economy: QuoteEconomy | None = Field(default=None, exclude_if=lambda value: value is None)


def check_days(days: object) -> int:
    """Срок вписывает мастер: целое число рабочих дней больше нуля. Движок срок не считает."""
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise InputError(DAYS_ERROR)
    return days


def parse_days(text: str | None) -> int:
    """`--days` из командной строки: только целое десятичное число; остальное — та же русская ошибка."""
    if text is None or not text.strip().isascii() or not text.strip().isdigit():
        raise InputError(DAYS_ERROR)
    return check_days(int(text))


# Числительные для «с тремя ящиками»: творительный падеж, 1–3 словом, дальше цифрой.
_COUNT = {"m": {1: "одним", 2: "двумя", 3: "тремя"}, "f": {1: "одной", 2: "двумя", 3: "тремя"}}


def _counted(count: int, gender: str, one: str, many: str) -> str:
    return f"{_COUNT[gender].get(count, str(count))} {one if count == 1 else many}"


def _enumerate(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " и " + items[-1]


def describe_module(module: ModuleSpec, built: ModuleParts, project: Project) -> str:
    """«нижний модуль с тремя ящиками, 600 мм; техника: мойка». Ящики, двери и полки считаются из
    деталей модуля (то же, что идёт в раскрой), техника — из проекта."""
    template = TEMPLATES[module.template]
    names = [p.name for p in built.parts]
    drawers = names.count(DRAWER_BOTTOM)
    doors = names.count("фасад") - drawers if template.carcass else 0
    shelves = names.count("полка")
    extras = []
    if drawers:
        extras.append(_counted(drawers, "m", "ящиком", "ящиками"))
    if doors > 0:
        extras.append(_counted(doors, "f", "дверью", "дверями"))
    if shelves:
        extras.append(_counted(shelves, "f", "полкой", "полками"))
    text = template.title + (f" с {_enumerate(extras)}" if extras else "") + f", {module.width:g} мм"
    kinds = [APPLIANCE_NAMES[a.type] for a in project.appliances if a.module == module.label]
    return text + (f"; техника: {', '.join(kinds)}" if kinds else "")


def _prepayment(price: int, share: float) -> int:
    """Предоплата = итог × доля, до рубля (от половины вверх); остаток — разность."""
    return int((Decimal(price) * Decimal(str(share))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _name(items, item_id: str) -> str:
    return next((i.name for i in items if i.id == item_id), item_id)


def _facade(ws: Workshop, material: str, color: str | None) -> str:
    name = _name(ws.materials.facades, material)
    return f"{name}, {color}" if color else name


def _master(master: Master) -> QuoteMaster:
    return QuoteMaster(
        name=master.name,
        phone=master.phone,
        city=master.city,
        status=master.status,
        inn=master.inn,
        offer_days=master.offer_days,
    )


def build_quote(
    project: Project,
    built: list[ModuleParts],
    ws: Workshop,
    *,
    days: int,
    price: int,
    economy: EconomyVariant | None,
) -> QuoteBody:
    """КП из проекта, деталей модулей и цены сметы того же снимка проекта."""
    by_label = {m.module: m for m in built}
    modules = [
        QuoteModule(
            label=module.label,
            wall=wall.label,
            row=row.kind,
            text=describe_module(module, by_label[module.label], project),
        )
        for wall in project.walls
        for row in wall.rows
        for module in row.modules
    ]
    share = ws.pricing.prepayment
    prepayment = _prepayment(price, share)
    materials: ProjectMaterials = project.materials
    return QuoteBody(
        master=_master(ws.master),
        days=check_days(days),
        modules=modules,
        body=_name(ws.materials.sheets, materials.body),
        facades_lower=_facade(ws, materials.facades_lower, materials.facades_lower_color),
        facades_upper=_facade(ws, materials.facades_upper, materials.facades_upper_color),
        countertop=_name(ws.materials.countertops, materials.countertop) if materials.countertop else None,
        price=price,
        prepayment_share=share,
        prepayment=prepayment,
        balance=price - prepayment,
        economy=(
            QuoteEconomy(
                price_as_is=economy.price_as_is,
                price=economy.price,
                saving=economy.saving,
                replacements=[
                    QuoteReplacement(client_text=r.client_text, modules=r.modules)
                    for r in economy.replacements
                ],
            )
            if economy
            else None
        ),
    )
