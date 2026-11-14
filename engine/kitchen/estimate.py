"""Смета: себестоимость из закупки, труд по операциям, цена с округлением."""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel

from kitchen.cutting import MaterialCut
from kitchen.models import Workshop
from kitchen.parts import DRAWER_BACK, DRAWER_BOTTOM, TEMPLATES, EdgeTotals, ModuleParts
from kitchen.project import Project

# Детали ящиков Tandembox (GLOSSARY: дно-ящ, зад-ящ) присадки не требуют.
DRAWER_PART_NAMES = frozenset({DRAWER_BOTTOM, DRAWER_BACK})


class LaborLine(BaseModel):
    """Строка труда: операция, откуда взялось количество, норма, минуты и рубли."""

    operation: str
    name: str
    quantity: float
    unit: str
    basis: str
    norm_min: float
    minutes: float
    amount: float


class EstimateLabor(BaseModel):
    rate_per_hour: float
    lines: list[LaborLine]
    minutes: float
    total: float


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _quantities(
    project: Project,
    ws: Workshop,
    modules: list[ModuleParts],
    hardware: dict[str, int],
    edges: dict[str, EdgeTotals],
    cut_materials: list[MaterialCut],
) -> list[tuple[str, str, str, Decimal, str, str]]:
    """Операция, поле нормы, название, количество, единица, откуда количество. Порядок — порядок смены."""
    sheets = {m.id for m in ws.materials.sheets}
    hdf = ws.construction.back_panel.material
    cut = sum(m.parts_count for m in cut_materials)
    drill = 0
    for module in modules:
        for part in module.parts:
            if part.material in sheets:
                if part.material != hdf and part.name not in DRAWER_PART_NAMES:
                    drill += 1
            elif part.area_m2 is not None:
                drill += 1
    cases = sum(1 for module in modules if TEMPLATES[module.template].carcass)
    drawers = hardware.get(ws.construction.drawers.hardware, 0)
    edge_m = sum((_decimal(e.with_allowance_m) for e in edges.values()), Decimal(0))

    def count(kind: str) -> int:
        return sum(1 for a in project.appliances if a.type == kind)

    return [
        (
            "cut",
            "cut_per_part",
            "Распил",
            Decimal(cut),
            "дет.",
            "детали из листа (ЛДСП и ХДФ), без фасадов и столешницы",
        ),
        (
            "edge",
            "edge_per_m",
            "Кромка",
            edge_m,
            "м",
            "метраж кромки из деталей с припуском, до округления закупки",
        ),
        (
            "drill",
            "drill_per_part",
            "Присадка",
            Decimal(drill),
            "дет.",
            "детали ЛДСП корпуса и фасады, без ХДФ и деталей ящиков",
        ),
        ("case_assembly", "case_assembly", "Сборка корпуса", Decimal(cases), "мод.", "модули с боковинами"),
        (
            "drawer_assembly_extra",
            "drawer_assembly_extra",
            "Сборка ящика Tandembox",
            Decimal(drawers),
            "ящ.",
            "комплекты Tandembox в фурнитуре модулей",
        ),
        ("install", "install_per_module", "Монтаж", Decimal(len(modules)), "мод.", "все модули проекта"),
        (
            "sink_cutout",
            "sink_cutout",
            "Врезка мойки",
            Decimal(count("sink")),
            "шт.",
            "мойки в технике проекта",
        ),
        (
            "hob_cutout",
            "hob_cutout",
            "Врезка варочной",
            Decimal(count("hob")),
            "шт.",
            "варочные в технике проекта",
        ),
        ("site_visit", "site_visit", "Выезд", Decimal(1), "шт.", "один на заказ"),
        ("measuring", "measuring", "Замер", Decimal(1), "шт.", "один на заказ"),
        ("design", "design", "Проектирование", Decimal(1), "шт.", "одно на заказ"),
    ]


def build_labor(
    project: Project,
    ws: Workshop,
    modules: list[ModuleParts],
    hardware: dict[str, int],
    edges: dict[str, EdgeTotals],
    cut_materials: list[MaterialCut],
) -> EstimateLabor:
    """Строки — до копеек; итог — от точных минут: Σ минут × ставка / 60, до копеек."""
    norms = ws.labor.norms_min
    rate = _decimal(ws.labor.hourly_rate)
    lines = []
    total_minutes = Decimal(0)
    for operation, norm_field, name, quantity, unit, basis in _quantities(
        project, ws, modules, hardware, edges, cut_materials
    ):
        norm = _decimal(getattr(norms, norm_field))
        minutes = quantity * norm
        total_minutes += minutes
        lines.append(
            LaborLine(
                operation=operation,
                name=name,
                quantity=float(quantity),
                unit=unit,
                basis=basis,
                norm_min=float(norm),
                minutes=float(minutes),
                amount=float(_money(minutes * rate / 60)),
            )
        )
    return EstimateLabor(
        rate_per_hour=float(rate),
        lines=lines,
        minutes=float(total_minutes),
        total=float(_money(total_minutes * rate / 60)),
    )


class Pricing(BaseModel):
    markup: float
    consumables: float
    delivery: float
    price_before_rounding: float
    price: int
    margin: float
    rounding: str


def build_pricing(ws: Workshop, materials_cost: float, labor_total: float) -> Pricing:
    """Цена = материалы + наценка + расходники + доставка + труд; округляется только итог."""
    p = ws.pricing
    materials = _decimal(materials_cost)
    markup = _money(materials * _decimal(p.materials_markup))
    consumables = _money(materials * _decimal(p.consumables_share))
    delivery = _money(_decimal(p.materials_delivery))
    labor = _decimal(labor_total)
    raw = materials + markup + consumables + delivery + labor
    price = p.price_rounding.apply(raw)
    return Pricing(
        markup=float(markup),
        consumables=float(consumables),
        delivery=float(delivery),
        price_before_rounding=float(raw),
        price=price,
        margin=float(_decimal(price) - (materials + consumables + delivery + labor)),
        rounding=p.price_rounding.describe(),
    )
