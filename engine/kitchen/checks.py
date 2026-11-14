"""Проверки проекта (1.6): находки с кодом, уровнем, значением и пределом.

Проверки ничего не бросают на ошибках проекта: каждая проблема — находка, чтобы мастер
видел все сразу. Пороги и допуски — из мастерской (`construction.yaml`, `equipment.yaml`).
"""

import re
from typing import Literal

from pydantic import BaseModel

from kitchen import formulas
from kitchen.errors import KitchenError, WorkshopError
from kitchen.models import Workshop
from kitchen.parts import TEMPLATES, ModuleDims, TemplateOut, build_module, module_dims
from kitchen.project import ModuleSpec, Project

__all__ = ["Finding", "check_project"]

_KIND_RU = {"lower": "нижний", "upper": "верхний"}


class Finding(BaseModel):
    """Находка проверки. `subject` — модуль, деталь или стена; `value` и `limit` — числа проверки."""

    code: str
    level: Literal["error", "warning"]
    subject: str
    message: str
    value: float | None = None
    limit: float | None = None


def _num(x: float) -> int | float:
    return int(x) if float(x).is_integer() else x


def _row_sums(project: Project, ws: Workshop) -> list[Finding]:
    """Ширины модулей ряда + угловые смещения с соседней стены (+ пенал из другого ряда) = длина стены."""
    rules = ws.construction.corner_rules
    offsets = {"lower": rules.lower_offset, "upper": rules.upper_offset}
    extra: dict[tuple[str, str], float] = {}  # (стена, вид ряда) -> смещения и пеналы
    tall_widths: dict[str, float] = {}
    found: list[Finding] = []
    walls = {w.label for w in project.walls}
    for wall in project.walls:
        for row in wall.rows:
            for m in row.modules:
                if m.template == "tall_oven":
                    tall_widths[wall.label] = tall_widths.get(wall.label, 0) + m.width
                if m.corner is None:
                    continue
                adjacent = m.corner.adjacent_wall
                if adjacent == wall.label or adjacent not in walls:
                    found.append(
                        Finding(
                            code="corner_wall",
                            level="error",
                            subject=m.label,
                            message=f"Модуль {m.label}: соседняя стена «{adjacent}» должна существовать и отличаться от стены «{wall.label}».",
                        )
                    )
                    continue
                offset = m.corner.offset if m.corner.offset is not None else offsets[row.kind]
                extra[(adjacent, row.kind)] = extra.get((adjacent, row.kind), 0) + offset
    for wall in project.walls:
        for row in wall.rows:
            total = sum(m.width for m in row.modules) + extra.get((wall.label, row.kind), 0)
            if not any(m.template == "tall_oven" for m in row.modules):
                total += tall_widths.get(wall.label, 0)
            if abs(total - wall.length) > rules.wall_tolerance:
                found.append(
                    Finding(
                        code="row_sum",
                        level="error",
                        subject=wall.label,
                        message=(
                            f"Стена {wall.label}: {_KIND_RU[row.kind]} ряд занимает {_num(total)} мм "
                            f"(модули, угловые смещения, пенал), а длина стены {_num(wall.length)} мм: "
                            f"разница {_num(abs(total - wall.length))} мм (допуск {rules.wall_tolerance} мм)."
                        ),
                        value=total,
                        limit=wall.length,
                    )
                )
    return found


def _drawer_depth(module: ModuleSpec, dims: ModuleDims, ws: Workshop) -> Finding | None:
    """Внутренняя глубина — глубина корпуса: задняя стенка накладная, фасад снаружи."""
    guide = ws.construction.tandembox.guide_length
    if not TEMPLATES[module.template].drawers or dims.depth >= guide:
        return None
    return Finding(
        code="drawer_depth",
        level="error",
        subject=module.label,
        message=(
            f"Модуль {module.label}: внутренняя глубина {dims.depth} мм меньше направляющей ящика {_num(guide)} мм."
        ),
        value=dims.depth,
        limit=guide,
    )


def _appliance_niche(appliance, ws: Workshop) -> tuple[float, float | None, float | None, bool]:
    """Что требуется от ниши: габариты из проекта, недостающие — ниша из appliances.yaml.
    Позиция каталога выбирается по типу и типоразмеру (ширине); если такого типоразмера нет,
    подстановки нет: известное проверяется, неизвестное названо в предупреждении (последнее
    значение — «чего-то не хватает»)."""
    item = next(
        (i for i in ws.appliances.items if i.type == appliance.type and i.nominal_width == appliance.width),
        None,
    )
    if item is None:
        return (
            appliance.width,
            appliance.height,
            appliance.depth,
            appliance.height is None or appliance.depth is None,
        )
    return (
        item.niche_width,
        appliance.height if appliance.height is not None else item.niche_height,
        appliance.depth if appliance.depth is not None else item.niche_depth,
        False,
    )


def _inner_width(dims: ModuleDims, ws: Workshop) -> int:
    """Внутренняя ширина корпуса — по формуле мастерской (`ящики.внутренняя_ширина`)."""
    return int(
        formulas.evaluate(ws.construction.drawers.inner_width, W=dims.width, T=ws.construction.case_thickness)
    )


def _niches(project: Project, ws: Workshop, dims: dict[str, tuple[ModuleSpec, ModuleDims]]) -> list[Finding]:
    """Ниша модуля ≥ требований ниши техники. Ниша модуля — внутренняя ширина и глубина корпуса;
    у модуля без корпуса (ПММ) — ширина модуля, а высота — высота ряда плюс ножки."""
    found: list[Finding] = []
    for a in project.appliances:
        if a.module not in dims:
            found.append(
                Finding(
                    code="appliance_module",
                    level="error",
                    subject=a.module,
                    message=f"Техника «{a.type}» привязана к модулю {a.module}, которого нет в проекте.",
                )
            )
            continue
        module, d = dims[a.module]
        width, height, depth, incomplete = _appliance_niche(a, ws)
        if incomplete:
            found.append(
                Finding(
                    code="appliance_no_data",
                    level="warning",
                    subject=a.module,
                    message=(
                        f"Техника «{a.type}» шириной {_num(a.width)} мм в модуле {a.module}: такого типоразмера нет "
                        "в appliances.yaml, а высота или глубина не указаны, поэтому нишу можно проверить только "
                        "по известным габаритам."
                    ),
                )
            )
        bare = not TEMPLATES[module.template].carcass
        limits = (
            ("ширина", width, d.width if bare else _inner_width(d, ws)),
            ("высота", height, d.height + ws.construction.legs.height if bare else None),
            ("глубина", depth, d.depth),
        )
        for name, value, limit in limits:
            if value is None or limit is None or value <= limit:
                continue
            found.append(
                Finding(
                    code="niche_too_small",
                    level="error",
                    subject=a.module,
                    message=(
                        f"Модуль {a.module}: {name} ниши под «{a.type}» {_num(value)} мм не помещается "
                        f"в модуль ({_num(limit)} мм)."
                    ),
                    value=_num(value),
                    limit=_num(limit),
                )
            )
    return found


def _modules(project: Project):
    for wall in project.walls:
        for row in wall.rows:
            for module in row.modules:
                yield row, module


def _build(project: Project, ws: Workshop, found: list[Finding]):
    """Габариты и детали каждого модуля; сбой расчёта модуля — находка, а не обрыв."""
    dims: dict[str, tuple[ModuleSpec, ModuleDims]] = {}
    built: dict[str, TemplateOut] = {}
    for row, module in _modules(project):
        try:
            d = module_dims(module, row.kind, ws)
        except KitchenError as e:
            found.append(_build_failure(module, e))
            continue
        dims[module.label] = (module, d)
        depth = _drawer_depth(module, d, ws)
        if depth:
            found.append(depth)
            continue  # расчёт упёрся бы в ту же глубину
        try:
            built[module.label] = build_module(module, row, project.materials, ws, project.appliances)
        except KitchenError as e:
            found.append(_build_failure(module, e))
    return dims, built


def _build_failure(module: ModuleSpec, error: KitchenError) -> Finding:
    return Finding(
        code="module_build", level="error", subject=module.label, message="\n".join(error.problems)
    )


def _along_texture(sheet, ws_sheet: tuple[float, float]) -> tuple[float, float]:
    """(вдоль текстуры, поперёк) для листа с текстурой «вдоль N»."""
    match = re.fullmatch(r"вдоль\s+(\d+)", sheet.texture.strip()) if isinstance(sheet.texture, str) else None
    if match is None or float(match.group(1)) not in ws_sheet:
        raise WorkshopError(
            f"materials.yaml: у материала «{sheet.id}» текстура «{sheet.texture}» — "
            f"ожидалось «вдоль N», где N — одна из сторон листа {ws_sheet[0]:g}×{ws_sheet[1]:g}."
        )
    along = float(match.group(1))
    return along, ws_sheet[1] if along == ws_sheet[0] else ws_sheet[0]


def _sheet_fit(built: dict[str, TemplateOut], ws: Workshop) -> list[Finding]:
    """Деталь в рабочем поле листа (лист минус обрезка с двух сторон). Текстурная — без поворота."""
    sheets = {m.id: m for m in ws.materials.sheets}
    trim = 2 * ws.equipment.sheet.perimeter_trim
    found: list[Finding] = []
    for out in built.values():
        for part in out.parts:
            sheet = sheets.get(part.material)
            if sheet is None:  # фасады и столешница — не листовые детали
                continue
            if part.texture:
                along, across = (v - trim for v in _along_texture(sheet, sheet.sheet))
                limits = (("длина (вдоль текстуры)", part.length, along), ("ширина", part.width, across))
            else:
                big, small = (v - trim for v in sorted(sheet.sheet, reverse=True))
                limits = (("длина", part.length, big), ("ширина", part.width, small))
                if part.length <= big and part.width <= small:
                    continue
            for name, value, limit in limits:
                if value > limit:
                    found.append(
                        Finding(
                            code="part_exceeds_sheet",
                            level="error",
                            subject=part.id,
                            message=(
                                f"Деталь {part.id}: {name} {_num(value)} мм не помещается в рабочее поле листа "
                                f"{_num(limit)} мм (лист {sheet.sheet[0]:g}×{sheet.sheet[1]:g} минус обрезка "
                                f"по {ws.equipment.sheet.perimeter_trim:g} мм с каждой стороны)."
                            ),
                            value=value,
                            limit=limit,
                        )
                    )
    return found


def _no_back(built: dict[str, TemplateOut], dims: dict[str, tuple[ModuleSpec, ModuleDims]]) -> list[Finding]:
    """Корпус без задней стенки. Модуль без корпуса (только фасад) не в счёт."""
    return [
        Finding(
            code="no_back_panel",
            level="warning",
            subject=label,
            message=f"Модуль {label}: без задней стенки — проверьте, что так и задумано.",
        )
        for label, out in built.items()
        if any(p.name == "боковина" for p in out.parts)
        and not any(p.name == "задняя стенка" for p in out.parts)
    ]


def check_project(project: Project, ws: Workshop) -> list[Finding]:
    """Все проверки проекта, ошибки и предупреждения вместе."""
    found = _row_sums(project, ws)
    dims, built = _build(project, ws, found)
    found += _niches(project, ws, dims)
    found += _sheet_fit(built, ws)
    found += _no_back(built, dims)
    return found
