"""Проверки проекта (1.6): находки с кодом, уровнем, значением и пределом.

Проверки ничего не бросают на ошибках проекта: каждая проблема — находка, чтобы мастер
видел все сразу. Пороги и допуски — из мастерской (`construction.yaml`, `equipment.yaml`).
"""

import re
from typing import Literal

from pydantic import BaseModel

from kitchen import formulas
from kitchen.brief import BriefAppliance
from kitchen.brief import appliances as brief_appliances
from kitchen.errors import KitchenError, WorkshopError
from kitchen.models import Workshop
from kitchen.parts import BACK_PANEL, TEMPLATES, ModuleDims, TemplateOut, build_module, module_dims
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


def _appliance_niche(appliance, brief: BriefAppliance | None, ws: Workshop):
    """Что требуется от ниши, по порядку источников: бриф, проект (его строят шаблоны), мок-каталог.
    Позиция каталога — по типу и типоразмеру (ширине); такого типоразмера нет — подстановки нет:
    известное проверяется, неизвестное называет предупреждение. Возвращает ширину, высоту, глубину,
    «не хватает данных» и «что-то взято из каталога»."""
    width = (brief.width if brief and brief.width else None) or appliance.width
    item = next(
        (i for i in ws.appliances.items if i.type == appliance.type and i.nominal_width == width), None
    )
    height = (brief.height if brief else None) or appliance.height
    depth = (brief.depth if brief else None) or appliance.depth
    if item is None:
        return width, height, depth, height is None or depth is None, False
    from_catalog = True  # ширина-типоразмер читается как ниша каталога
    if height is None:
        height = item.niche_height
    if depth is None:
        depth = item.niche_depth
    return item.niche_width, height, depth, False, from_catalog


def _inner_width(dims: ModuleDims, ws: Workshop) -> int:
    """Внутренняя ширина корпуса — по формуле мастерской (`ящики.внутренняя_ширина`)."""
    return int(
        formulas.evaluate(ws.construction.drawers.inner_width, W=dims.width, T=ws.construction.case_thickness)
    )


def _name(appliance, brief: BriefAppliance | None) -> str:
    return brief.label if brief else appliance.type


def _pair(project: Project, brief: list[BriefAppliance], ws: Workshop, found: list[Finding]):
    """Сверка состава: приборы проекта и брифа одного вида идут парами по порядку.
    Возвращает прибор проекта → прибор брифа (или `None`)."""
    paired: dict[int, BriefAppliance | None] = {}
    by_type: dict[str, list[BriefAppliance]] = {}
    for b in brief:
        by_type.setdefault(b.type, []).append(b)
    used: dict[str, int] = {}
    for a in project.appliances:
        index = used.get(a.type, 0)
        used[a.type] = index + 1
        candidates = by_type.get(a.type, [])
        paired[id(a)] = candidates[index] if index < len(candidates) else None
        if paired[id(a)] is None:
            found.append(
                Finding(
                    code="appliance_not_in_brief",
                    level="warning",
                    subject=a.module,
                    message=f"Техника «{a.type}» в модуле {a.module} есть в проекте, но не в брифе заказа.",
                )
            )
    for type_, items in by_type.items():
        for b in items[used.get(type_, 0) :]:
            if b.built_in and type_ in ws.construction.module_appliances:
                found.append(
                    Finding(
                        code="appliance_unbound",
                        level="error",
                        subject=b.label,
                        message=f"Встраиваемая техника из брифа «{b.label}» не привязана ни к одному модулю проекта.",
                    )
                )
    return paired


def _niches(
    project: Project,
    ws: Workshop,
    dims: dict[str, tuple[ModuleSpec, ModuleDims]],
    brief: list[BriefAppliance] | None,
) -> list[Finding]:
    """Ниша модуля ≥ требований ниши техники. Ниша модуля — внутренняя ширина и глубина корпуса;
    у модуля без корпуса (ПММ) — ширина модуля, а высота — высота ряда плюс ножки."""
    found: list[Finding] = []
    if brief is None:
        found.append(
            Finding(
                code="brief_missing",
                level="warning",
                subject=project.order,
                message="У заказа нет брифа: брифа нет, техника не сверена с клиентом (размеры — из проекта и мок-каталога).",
            )
        )
        paired: dict[int, BriefAppliance | None] = {id(a): None for a in project.appliances}
    else:
        paired = _pair(project, brief, ws, found)
        found += [
            Finding(
                code="brief_dimensions",
                level="warning",
                subject=b.label,
                message=f"Бриф, «{b.label}»: {w}.",
            )
            for b in brief
            for w in b.warnings
        ]
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
        b = paired[id(a)]
        width, height, depth, incomplete, from_catalog = _appliance_niche(a, b, ws)
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
        if b and from_catalog:
            found.append(
                Finding(
                    code="appliance_catalog_dims",
                    level="warning",
                    subject=a.module,
                    message=f"Размеры «{b.label}» из мок-каталога — уточните модель у клиента.",
                )
            )
        if b:
            differ = [
                f"{name} в проекте {_num(mine)} мм, в брифе {_num(theirs)} мм"
                for name, mine, theirs in (
                    ("ширина", a.width, b.width),
                    ("высота", a.height, b.height),
                    ("глубина", a.depth, b.depth),
                )
                if mine is not None and theirs is not None and mine != theirs
            ]
            if differ:
                found.append(
                    Finding(
                        code="appliance_dims_differ",
                        level="warning",
                        subject=a.module,
                        message=f"Модуль {a.module}, «{b.label}»: размеры проекта расходятся с брифом ({'; '.join(differ)}).",
                    )
                )
        bare = not TEMPLATES[module.template].carcass
        # прибор «в столешницу» шире корпуса модуля может быть: его ширина не про нишу корпуса
        on_top = a.type in ws.construction.countertop_appliances
        limits = (
            ("ширина", width, None if on_top else d.width if bare else _inner_width(d, ws)),
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
                        f"Модуль {a.module}: {name} ниши под «{_name(a, b)}» {_num(value)} мм не помещается "
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
        if TEMPLATES[dims[label][0].template].carcass and not any(p.name == BACK_PANEL for p in out.parts)
    ]


def check_project(project: Project, ws: Workshop, brief: dict | None = None) -> list[Finding]:
    """Все проверки проекта, ошибки и предупреждения вместе. `brief` — последний бриф заказа (или `None`)."""
    found = _row_sums(project, ws)
    dims, built = _build(project, ws, found)
    found += _niches(project, ws, dims, None if brief is None else brief_appliances(brief))
    found += _sheet_fit(built, ws)
    found += _no_back(built, dims)
    return found
