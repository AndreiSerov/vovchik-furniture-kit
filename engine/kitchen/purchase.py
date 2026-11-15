"""Расчёт закупки из деталей и раскроя с ценами мастерской."""

import re
from collections import defaultdict
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from kitchen.common import CommonItem, CountertopSize
from kitchen.cutting import MaterialCut
from kitchen.errors import OrderError
from kitchen.models import Workshop
from kitchen.parts import EdgeTotals, ModuleParts, facade_color, is_facade
from kitchen.project import Project


class PurchaseItem(BaseModel):
    kind: Literal["sheet", "edge", "hardware", "countertop", "facade"]
    id: str
    name: str
    quantity: float
    unit: str
    unit_price: float
    amount: float
    needed: float | None = None
    needed_unit: str | None = None
    pack_size: int | None = None
    color: str | None = None
    lead_days: int | None = None


class PurchaseShop(BaseModel):
    name: str
    items: list[PurchaseItem]
    total: float


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _minimum_sale(edge_id: str, sale: str) -> Decimal:
    match = re.fullmatch(r"\s*метраж\s+от\s+(\d+(?:[.,]\d+)?)\s*м\s*", sale, re.IGNORECASE)
    if not match or Decimal(match.group(1).replace(",", ".")) <= 0:
        raise OrderError(f"materials.yaml: кромка {edge_id}: непонятное условие продажи «{sale}».")
    return Decimal(match.group(1).replace(",", "."))


def _facades(project: Project, modules: list[ModuleParts]) -> dict[tuple[str, str], Decimal]:
    colors = {}
    for wall in project.walls:
        for row in wall.rows:
            for module in row.cabinets:
                colors[module.label] = facade_color(module, row, project.materials)
    groups: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for module in modules:
        for part in module.parts:
            if is_facade(part):
                groups[(part.material, colors[module.module])] += _decimal(part.area_m2)
    return {k: v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) for k, v in sorted(groups.items())}


def build_purchase(
    project: Project,
    ws: Workshop,
    modules: list[ModuleParts],
    hardware: dict[str, int],
    edges: dict[str, EdgeTotals],
    common: list[CommonItem],
    materials: list[MaterialCut],
    countertop: CountertopSize | None,
) -> tuple[list[PurchaseShop], float]:
    grouped: dict[str, list[PurchaseItem]] = defaultdict(list)

    def add(shop: str, kind: str, item_id: str, name: str, quantity: Decimal, unit: str, price, **extra):
        unit_price = _decimal(price)
        if not quantity.is_finite() or not unit_price.is_finite() or quantity < 0 or unit_price < 0:
            raise OrderError(
                f"Позиция «{item_id}»: количество и цена должны быть конечными неотрицательными числами."
            )
        grouped[shop].append(
            PurchaseItem(
                kind=kind,
                id=item_id,
                name=name,
                quantity=float(quantity),
                unit=unit,
                unit_price=float(unit_price),
                amount=_money(quantity * unit_price),
                **extra,
            )
        )

    sheet_catalog = {item.id: item for item in ws.materials.sheets}
    for material in materials:
        item = sheet_catalog[material.material]
        add(item.shop, "sheet", item.id, item.name, _decimal(material.sheet_count), "лист", item.price)
    edge_catalog = {item.id: item for item in ws.materials.edges}
    for edge_id, total in edges.items():
        item = edge_catalog[edge_id]
        needed = _decimal(total.purchase_m)
        quantity = max(needed, _minimum_sale(edge_id, item.sale))
        rule = ws.construction.edging.purchase_rounding.model_copy(update={"direction": "up"})
        quantity = rule.apply_mm(quantity * 1000) / 1000
        add(
            item.shop,
            "edge",
            item.id,
            item.name,
            quantity,
            "м",
            item.price_per_m,
            needed=float(needed),
            needed_unit="м",
        )
    quantities = dict(hardware)
    units = dict.fromkeys(quantities, "шт")
    for item in common:
        if item.in_hardware or item.kind == "countertop" or not item.quantity:
            continue
        if item.item in units and units[item.item] != item.unit:
            raise OrderError(f"Фурнитура «{item.item}» посчитана в разных единицах.")
        units[item.item] = item.unit
        quantities[item.item] = quantities.get(item.item, 0) + item.quantity
    catalog = {item.id: item for item in ws.hardware.items}
    for item_id, needed in sorted(quantities.items()):
        if not needed:
            continue
        item = catalog[item_id]
        if item.pack <= 0:
            raise OrderError(f"hardware.yaml: упаковка позиции «{item_id}» должна быть больше нуля.")
        packs = (_decimal(needed) / item.pack).to_integral_value(rounding=ROUND_CEILING)
        add(
            item.shop,
            "hardware",
            item.id,
            item.name,
            packs,
            "уп",
            item.price,
            needed=needed,
            needed_unit=units[item_id],
            pack_size=item.pack,
        )
    if countertop and countertop.sheet_count:
        catalog_top = {item.id: item for item in ws.materials.countertops}
        item = catalog_top[countertop.item]
        add(item.shop, "countertop", item.id, item.name, _decimal(countertop.sheet_count), "лист", item.price)
    catalog_facade = {item.id: item for item in ws.materials.facades}
    for (item_id, color), area in _facades(project, modules).items():
        item = catalog_facade[item_id]
        add(
            item.shop,
            "facade",
            item.id,
            item.name,
            area,
            "м²",
            item.price_per_m2,
            color=color,
            lead_days=item.lead_days,
        )
    shops = [
        PurchaseShop(
            name=name, items=items, total=_money(sum((_decimal(i.amount) for i in items), Decimal(0)))
        )
        for name, items in sorted(grouped.items())
    ]
    return shops, _money(sum((_decimal(s.total) for s in shops), Decimal(0)))
