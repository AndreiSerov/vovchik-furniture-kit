"""Чек-лист выезда (4.4, тикет 40): что взять на объект — шапка заказа, модули, общие позиции, фурнитура «объект»,
техника клиента, инструмент и расходники.

Чисел и формул здесь нет: общие позиции и фурнитура берутся из расчёта деталей, инструмент и расходники — из
`equipment.yaml` (`выезд`), шапка — из последнего брифа (подписи перечней — `value_labels` схемы брифа).
"""

from typing import Any

from pydantic import BaseModel

from kitchen import brief as brief_text
from kitchen.assembly import UNIT
from kitchen.common import CommonItem, size_countertop
from kitchen.models import Workshop
from kitchen.parts import APPLIANCE_NAMES, module_dims, module_line
from kitchen.project import Project, is_gap
from kitchen.quote import describe_gap

__all__ = ["NO_BRIEF_WARNING", "TripHeader", "TripItem", "TripSection", "build_trip"]

DASH = "—"
NO_BRIEF_WARNING = "брифа нет — шапка пустая"


class TripItem(BaseModel):
    text: str


class TripSection(BaseModel):
    """Раздел чек-листа; у пустого раздела `items` пуст (PDF и CLI печатают «—»)."""

    title: str
    items: list[TripItem]


class TripHeader(BaseModel):
    """Шапка: значения как напечатаны, «—» — нет в брифе."""

    client: str
    address: str
    floor_lift: str
    parking: str
    dismantling: str
    walls: str
    floor: str

    def lines(self) -> list[str]:
        return [
            f"Клиент: {self.client}",
            f"Адрес: {self.address}",
            f"Этаж: {self.floor_lift}",
            f"Парковка: {self.parking}",
            f"Демонтаж старой кухни: {self.dismantling}",
            f"Стены: {self.walls}, пол: {self.floor}",
        ]


def _text(brief: dict[str, Any] | None, path: str) -> str:
    raw = brief_text.value(brief, path) if brief else None
    return DASH if raw is None else str(raw)


def _label(brief: dict[str, Any] | None, path: str) -> str:
    raw = brief_text.value(brief, path) if brief else None
    return DASH if raw is None or raw == "unknown" else brief_text.value_label(path, raw)


def _client(brief: dict[str, Any] | None) -> str:
    name, phone = _text(brief, "client.name"), _text(brief, "client.phone")
    return DASH if name == phone == DASH else f"{name}, {phone}"


def build_header(brief: dict[str, Any] | None) -> TripHeader:
    return TripHeader(
        client=_client(brief),
        address=_text(brief, "client.address"),
        floor_lift=f"{_text(brief, 'client.floor')}, лифт: {_label(brief, 'client.elevator')}",
        parking=_label(brief, "installation.parking"),
        dismantling=_label(brief, "installation.dismantling"),
        walls=_label(brief, "installation.walls_evenness"),
        floor=_label(brief, "installation.floor_evenness"),
    )


def _common_lines(project: Project, common: list[CommonItem], ws: Workshop) -> list[str]:
    top = size_countertop(project, ws)
    lines = []
    for item in common:
        if item.in_hardware or item.quantity <= 0:  # навесы — в «Фурнитуре для монтажа»
            continue
        if item.kind == "countertop" and top is not None:
            lines.append(
                f"{item.name} — {item.quantity} {item.unit} {top.sheet_length}×{top.sheet_width} "
                f"(общая длина {top.total_length_mm} мм)"
            )
        else:
            lines.append(f"{item.name} — {item.quantity} {item.unit}")
    return lines


def _appliance_lines(project: Project) -> list[str]:
    lines = []
    for a in project.appliances:
        kind = APPLIANCE_NAMES[a.type]
        name = f"{kind[0].upper()}{kind[1:]}" + (f" {a.model}" if a.model else "")
        lines.append(f"{name} — {a.module}")
    return lines


def build_trip(
    project: Project, common: list[CommonItem], hardware: dict[str, int], ws: Workshop
) -> list[TripSection]:
    """Разделы чек-листа по порядку; пустой раздел остаётся с пустым списком."""
    modules = [
        f"{m.label} — {describe_gap(m, project)}"
        if is_gap(m)
        else module_line(m, f"{module_dims(m, row.kind, ws).width}")
        for wall in project.walls
        for row in wall.rows
        for m in row.modules
    ]
    site = [
        f"{i.name} — {hardware[i.id]} {UNIT}"
        for i in ws.hardware.items
        if i.installed == "объект" and hardware.get(i.id, 0) > 0
    ]
    lists = ws.equipment.trip
    sections = [
        (f"Модули ({len(modules)})", modules),
        ("Общие позиции", _common_lines(project, common, ws)),
        ("Фурнитура для монтажа", site),
        ("Техника клиента", _appliance_lines(project)),
        ("Инструмент", lists.tools),
        ("Расходники", lists.consumables),
    ]
    return [TripSection(title=t, items=[TripItem(text=x) for x in items]) for t, items in sections]
