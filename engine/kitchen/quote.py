"""КП (тикет 18): содержание коммерческого предложения клиенту из проекта, деталей и сметы.

Здесь только модель КП и сборка её из готовых результатов: цену и вариант «экономия» считает смета
(`kitchen.api`), детали — `kitchen.parts`. Себестоимости, наценки и труда в модели нет: КП строится
из итога, и эти числа в неё не попадают.
"""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from kitchen.common import size_countertop
from kitchen.economy import EconomyVariant
from kitchen.errors import InputError, OrderError
from kitchen.models import Master, Workshop
from kitchen.parts import (
    APPLIANCE_NAMES,
    DRAWER_BOTTOM,
    TEMPLATES,
    ModuleParts,
    build_module,
    lower_facades,
    resolve_materials,
)
from kitchen.project import ModuleSpec, Project, Row

__all__ = [
    "DAYS_ERROR",
    "MAX_DAYS",
    "QuoteBody",
    "QuoteEconomy",
    "QuoteMaster",
    "QuoteModule",
    "QuoteReplacement",
    "build_quote",
    "check_days",
    "describe_module",
    "money",
    "parse_days",
    "percent",
    "plural",
]

MAX_DAYS = 365
DAYS_ERROR = f"Срок: укажите --days N — целое число рабочих дней от 1 до {MAX_DAYS}, например --days 35."


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
    """Всё, что видит клиент. Одна модель для `quote.pdf`, `quote.json` и `--json`.

    `body`, `facades_lower`, `facades_upper` — одно значение или несколько с метками модулей
    («RAL 3020 (Н1–Н5), RAL 7044 (П1)»); группа без модулей — `None` и в КП не печатается. `countertop` —
    название столешницы проекта (без меток) или `None`, если столешницы в цене нет."""

    master: QuoteMaster
    days: int
    modules: list[QuoteModule]
    body: str | None
    facades_lower: str | None
    facades_upper: str | None
    countertop: str | None
    price: int
    prepayment_share: float
    prepayment: int
    balance: int
    economy: QuoteEconomy | None = Field(default=None, exclude_if=lambda value: value is None)


# --- числа и деньги ---------------------------------------------------------


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 день», «2 дня», «5 дней», «21 день», «12 дней»."""
    last, tens = count % 10, count % 100
    form = one if last == 1 and tens != 11 else few if 2 <= last <= 4 and not 12 <= tens <= 14 else many
    return f"{count} {form}"


def money(value: int) -> str:
    """222000 -> «222 000 ₽»."""
    return f"{value:,}".replace(",", " ") + " ₽"


def percent(share: float) -> str:
    """0.3333 -> «33,33 %», 0.5 -> «50 %»: без экспоненты, запятая у дробной части."""
    value = (Decimal(str(share)) * 100).normalize()
    return format(value, "f").replace(".", ",") + " %"


def check_days(days: object) -> int:
    """Срок вписывает мастер: целое число рабочих дней от 1 до `MAX_DAYS`. Движок срок не считает."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise InputError(DAYS_ERROR)
    return days


def parse_days(text: str | None) -> int:
    """`--days` из командной строки: только целое десятичное число; остальное — та же русская ошибка.
    Границы проверяет `check_days` при расчёте."""
    if text is None or not text.strip().isascii() or not text.strip().isdigit():
        raise InputError(DAYS_ERROR)
    return int(text)


# --- состав -----------------------------------------------------------------

# Числительные для «с тремя ящиками»: творительный падеж, 1–3 словом, дальше цифрой.
_COUNT = {"m": {1: "одним", 2: "двумя", 3: "тремя"}, "f": {1: "одной", 2: "двумя", 3: "тремя"}}


def _counted(count: int, gender: str, one: str, many: str) -> str:
    return f"{_COUNT[gender].get(count, str(count))} {one if count == 1 else many}"


def _enumerate(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " и " + items[-1]


def _hardware_phrase(name: str, many: str | None, count: int) -> str:
    """«выдвижным ведром»; два и больше — «двумя выдвижными вёдрами» (форма — `для_клиента_мн`)."""
    if count == 1:
        return name
    return _counted(count, "m", many, many) if many else f"{name} ({count} шт.)"


def _appliance(appliance) -> str | None:
    """Прибор словами брифа; «другая техника» слов не имеет: пишется модель прибора, если есть, иначе ничего."""
    if appliance.type == "other":
        return appliance.model or None
    return APPLIANCE_NAMES[appliance.type]


def describe_module(module: ModuleSpec, row: Row, built: ModuleParts, project: Project, ws: Workshop) -> str:
    """«нижний модуль с тремя ящиками, 600 мм; техника: мойка». Ящики, двери и полки считаются из деталей
    модуля (то же, что идёт в раскрой); карусель, ведро, сушка и подъёмный фасад — по фурнитуре модуля и
    названию для клиента из `hardware.yaml`; техника — из проекта. Подъёмный фасад дверью не считается."""
    template = TEMPLATES[module.template]
    hardware = build_module(module, row, project.materials, ws, project.appliances).hardware
    named = {i.id: i.client_name for i in ws.hardware.items if i.client_name}
    plural_names = {i.id: i.client_name_many for i in ws.hardware.items if i.client_name_many}
    lifted = ws.construction.upper_rules.lift in hardware
    names = [p.name for p in built.parts]
    drawers = names.count(DRAWER_BOTTOM)
    doors = 0 if lifted or not template.carcass else names.count("фасад") - drawers
    shelves = names.count("полка")
    extras = []
    if drawers:
        extras.append(_counted(drawers, "m", "ящиком", "ящиками"))
    if doors > 0:
        extras.append(_counted(doors, "f", "дверью", "дверями"))
    if shelves:
        extras.append(_counted(shelves, "f", "полкой", "полками"))
    for h, count in hardware.items():
        if h in named:
            extras.append(_hardware_phrase(named[h], plural_names.get(h), count))
    text = template.title + (f" с {_enumerate(extras)}" if extras else "") + f", {module.width:g} мм"
    kinds = [_appliance(a) for a in project.appliances if a.module == module.label]
    kinds = [k for k in kinds if k]
    return text + (f"; техника: {', '.join(kinds)}" if kinds else "")


# --- материалы --------------------------------------------------------------


def _runs(labels: list[str], order: list[str]) -> str:
    """«Н1–Н3, Н5»: подряд идущие в порядке модулей группы от трёх склеиваются в диапазон."""
    positions = sorted(order.index(label) for label in labels)
    runs: list[list[int]] = []
    for position in positions:
        if runs and position == runs[-1][-1] + 1:
            runs[-1].append(position)
        else:
            runs.append([position])
    parts = []
    for run in runs:
        names = [order[i] for i in run]
        parts.append(f"{names[0]}–{names[-1]}" if len(names) >= 3 else ", ".join(names))
    return ", ".join(parts)


def _group(values: list[tuple[str, str]]) -> str | None:
    """Значения по модулям группы: одно у всех — как есть, разные — все с метками модулей."""
    if not values:
        return None
    order = [label for label, _ in values]
    distinct = list(dict.fromkeys(value for _, value in values))
    if len(distinct) == 1:
        return distinct[0]
    return ", ".join(
        f"{value} ({_runs([label for label, v in values if v == value], order)})" for value in distinct
    )


def _name(items, item_id: str) -> str:
    """Название материала для клиента. Неизвестный id — ошибка: сырой id клиенту не показывают."""
    for item in items:
        if item.id == item_id:
            return item.name
    raise OrderError(f"Материал «{item_id}» не найден в materials.yaml: в КП его назвать нечем.")


def _prepayment(price: int, share: float) -> int:
    """Предоплата = итог × доля, до рубля (от половины вверх); остаток — разность."""
    return int((Decimal(price) * Decimal(str(share))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


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
    """КП из проекта, деталей модулей и цены сметы того же снимка проекта. Корпус и цвета фасадов — по модулям,
    как их видит расчёт деталей (`parts.resolve_materials`); столешница — одна на проект, как в цене."""
    by_label = {m.module: m for m in built}
    modules, body, lower, upper = [], [], [], []
    for wall in project.walls:
        for row in wall.rows:
            for module in row.modules:
                label = module.label
                modules.append(
                    QuoteModule(
                        label=label,
                        wall=wall.label,
                        row=row.kind,
                        text=describe_module(module, row, by_label[label], project, ws),
                    )
                )
                m = resolve_materials(module, row, project.materials)
                if TEMPLATES[module.template].carcass:  # корпус — только у модулей с корпусом (не ПММ)
                    body.append((label, _name(ws.materials.sheets, m.body)))
                if "фасад" in {p.name for p in by_label[label].parts}:
                    is_lower = lower_facades(module, row)
                    facade = _name(ws.materials.facades, m.facades_lower if is_lower else m.facades_upper)
                    color = m.facades_lower_color if is_lower else m.facades_upper_color
                    (lower if is_lower else upper).append((label, f"{facade}, {color}" if color else facade))
    top = size_countertop(project, ws)  # та же столешница, что в цене, раскрое и закупке
    share = ws.pricing.prepayment
    prepayment = _prepayment(price, share)
    return QuoteBody(
        master=_master(ws.master),
        days=days,
        modules=modules,
        body=_group(body),
        facades_lower=_group(lower),
        facades_upper=_group(upper),
        countertop=top.name if top and top.sheet_count else None,
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
